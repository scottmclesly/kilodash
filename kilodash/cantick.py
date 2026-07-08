"""CanTick WiFi↔CAN bridge — Pi-side link manager, heartbeat, provisioning, AP.

Scope — DIAGNOSTICS + NORMAL CAN PARTICIPATION ONLY (see PROTOCOL.md). CanTick
tunnels a CAN bus over WiFi so it appears here as an ordinary SocketCAN
interface (slcan0); the one allowed TX is normal node traffic, and listen-only
is enforced on the device (set at provisioning, reported in the heartbeat
`mode` field) so it remains enforceable from this side.

The guard-rail principle mirrors la.py/scan.py: every external command is
assembled by a builder from validated, allow-listed values and emitted as an
ARGUMENT ARRAY (never a shell string). The socat/slcand invocations are the
exact PROTOCOL.md §1 reference commands — nothing device-supplied ever reaches
an argv. tests/test_cantick.py pins the builders verbatim.

Pieces (wired into the CAN screen's enter/exit lifecycle):
  * CanTickLink        — supervised socat + slcand pair (§1). socat listens on
                         TCP 29536; the PTY appears when a CanTick dials in,
                         slcand attaches slcan0, and the supervisor relaunches
                         the pair (with backoff) whenever either side drops.
  * HeartbeatListener  — read-only UDP 29537 listener (§2): per-device
                         freshness, rx/s, drop-rising and contract-version
                         warnings. It never sends.
  * CanTickProvisioner — one-time USB CDC credential push (§4, CTK1| framing,
                         CRC-16/CCITT-FALSE, base64 ssid/psk). Never logs PSKs.
  * CanTickAP          — reversible hostapd+dnsmasq fallback AP on wlan0 (§5),
                         only when no uplink exists. On this unit wlan0 is
                         NetworkManager-managed and the standing "uplink
                         watchdog" is NM autoconnect (the per-screen watchdogs
                         in wifisniff.py/kismet.py only run while those screens
                         sniff, and only one screen is ever active): pausing =
                         `nmcli device set wlan0 managed no` with the prior
                         state recorded, resuming = restoring it. wlan1/ALFA is
                         never touched.
"""

import base64
import glob
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import threading
import time

log = logging.getLogger("kilodash.cantick")

PTY_LINK = "/dev/cantick0"          # fixed by PROTOCOL.md §1
RUN_DIR = "/run/kilodash-cantick"   # kilodash-owned temp path (AP configs)

# PROTOCOL.md §1 — Lawicel SLCAN bitrate codes (250k -> -s5)
SLCAN_BITRATE_CODES = {
    10000: 0, 20000: 1, 50000: 2, 100000: 3, 125000: 4,
    250000: 5, 500000: 6, 800000: 7, 1000000: 8,
}

FRESH_SECS = 6.0            # §2: stale after 3 missed 2 s heartbeats
CONTRACT_VERSION = 1

# Defaults for the `cantick` config block; block() merges these under any
# partially-saved block so config.json upgrades never leave a key missing.
CONFIG_DEFAULTS = {
    "enabled": True,
    "slcan_iface": "slcan0",
    "tcp_port": 29536,
    "hb_port": 29537,
    "bitrate": 250000,
    "listen_only": False,
    "fallback_ap_ssid": "Scottina-CanTick",
    "fallback_psk": "",
    "ap_gateway": "192.168.42.1",
    "expected_contract_version": CONTRACT_VERSION,
}


class CanTickError(Exception):
    """Raised when a command cannot be safely assembled or a step is refused."""


def block(config):
    """The merged `cantick` config block (defaults + whatever is saved)."""
    saved = {}
    try:
        saved = config["cantick"] or {}
    except KeyError:
        pass
    out = dict(CONFIG_DEFAULTS)
    if isinstance(saved, dict):
        out.update(saved)
    return out


def ensure_fallback_psk(config):
    """Return the fallback-AP PSK, generating and persisting it ONCE (§4).
    The same pair is pushed to every CanTick; the Phase-5 AP hosts it."""
    blk = block(config)
    psk = blk.get("fallback_psk") or ""
    if len(psk) >= 8:
        return psk
    alphabet = string.ascii_letters + string.digits
    psk = "".join(secrets.choice(alphabet) for _ in range(20))
    blk["fallback_psk"] = psk
    config.set("cantick", blk)
    return psk


# ------------------------------------------------------------- §1 builders ---
_IFACE_RE = re.compile(r"^slcan[0-9]$")


def build_socat_command(tcp_port):
    """Exact PROTOCOL.md §1 socat invocation as an argument list."""
    port = int(tcp_port)
    if not 1 <= port <= 65535:
        raise CanTickError(f"invalid tcp port: {tcp_port!r}")
    return ["socat", f"TCP-LISTEN:{port},reuseaddr",
            f"PTY,link={PTY_LINK},raw,echo=0"]


def build_slcand_command(bitrate, iface):
    """Exact PROTOCOL.md §1 slcand invocation as an argument list."""
    if bitrate not in SLCAN_BITRATE_CODES:
        raise CanTickError(f"unsupported bitrate: {bitrate!r}")
    if not _IFACE_RE.fullmatch(iface or ""):
        raise CanTickError(f"invalid slcan iface: {iface!r}")
    return ["slcand", "-o", "-c", f"-s{SLCAN_BITRATE_CODES[bitrate]}",
            PTY_LINK, iface]


def _sh(*cmd, timeout=6):
    return subprocess.run(list(cmd), capture_output=True, text=True,
                          timeout=timeout)


# ---------------------------------------------------------- interface link ---
_ACTIVE_LINKS = set()       # running CanTickLinks; devices.py keeps the CAN
_ACTIVE_LOCK = threading.Lock()   # tile/screen alive while one is listening


def link_active():
    """True while any CanTickLink is started (devices.py 'can' presence)."""
    with _ACTIVE_LOCK:
        return bool(_ACTIVE_LINKS)


class CanTickLink:
    """Supervised socat+slcand pair (PROTOCOL.md §1).

    start() launches a supervisor thread: socat listens on the TCP port; the
    PTY only appears once a CanTick dials in, then slcand attaches `iface` and
    the link goes up. If either side dies (CanTick WiFi drop closes the TCP
    stream and socat exits), everything is torn down and the pair relaunched
    with backoff so the next dial-in re-establishes cleanly. start()/stop()
    are idempotent; teardown always runs, even on supervisor crash.
    """

    # supervisor states (status()["state"])
    STOPPED, LISTENING, UP, BACKOFF = "stopped", "listening", "up", "backoff"

    def __init__(self, iface="slcan0", tcp_port=29536, bitrate=250000):
        # validate at construction — the builders are the safety boundary
        self._socat_cmd = build_socat_command(tcp_port)
        self._slcand_cmd = build_slcand_command(bitrate, iface)
        self.iface = iface
        self.state = self.STOPPED
        self.restarts = 0
        self._since = time.monotonic()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._socat = None

    # ---- public ----
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return                          # idempotent
            self._stop.clear()
            self._thread = threading.Thread(target=self._supervise,
                                            daemon=True)
            self._thread.start()
        with _ACTIVE_LOCK:
            _ACTIVE_LINKS.add(id(self))
        log.info("cantick link started (listening on %s)",
                 self._socat_cmd[1])

    def stop(self):
        with _ACTIVE_LOCK:
            _ACTIVE_LINKS.discard(id(self))
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=8)
        self._teardown()                        # safe when already down
        self._set_state(self.STOPPED)

    def status(self):
        return {"state": self.state, "iface": self.iface,
                "restarts": self.restarts,
                "since": time.monotonic() - self._since}

    # ---- internals ----
    def _set_state(self, state):
        if state != self.state:
            log.info("cantick link: %s -> %s", self.state, state)
            self.state = state
            self._since = time.monotonic()

    def _iface_present(self):
        return os.path.isdir(f"/sys/class/net/{self.iface}")

    def _wait(self, secs):
        return self._stop.wait(secs)

    def _supervise(self):
        backoff = 1.0
        try:
            while not self._stop.is_set():
                came_up = self._run_once()
                if self._stop.is_set():
                    break
                self.restarts += 1
                # a link that held >30 s earns a fresh backoff ladder
                backoff = 1.0 if came_up else min(backoff * 2, 15.0)
                self._set_state(self.BACKOFF)
                self._wait(backoff)
        finally:
            self._teardown()                    # never leave a half-torn link

    def _run_once(self):
        """One socat->slcand->up cycle. Returns True if the link was up for a
        while (resets backoff), False for a fast failure."""
        self._set_state(self.LISTENING)
        try:
            self._socat = subprocess.Popen(
                self._socat_cmd, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except OSError as e:
            log.error("socat launch failed: %s", e)
            return False

        # PTY appears only when a CanTick dials in — wait indefinitely,
        # stop-responsive, while socat itself stays alive.
        while not os.path.exists(PTY_LINK):
            if self._stop.is_set() or self._socat.poll() is not None:
                self._teardown()
                return False
            self._wait(0.2)

        try:
            subprocess.run(self._slcand_cmd, timeout=6,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.error("slcand launch failed: %s", e)
            self._teardown()
            return False

        # slcand daemonizes; the kernel iface appearing is the success signal
        for _ in range(15):
            if self._iface_present() or self._stop.is_set():
                break
            self._wait(0.2)
        if not self._iface_present():
            log.error("slcand did not create %s", self.iface)
            self._teardown()
            return False

        _sh("ip", "link", "set", self.iface, "up")
        self._set_state(self.UP)
        up_at = time.monotonic()

        # monitor: socat exiting (TCP dropped) or the iface vanishing ends
        # this cycle; the supervisor loop relaunches the pair
        while not self._stop.is_set():
            if self._socat.poll() is not None or not self._iface_present():
                break
            self._wait(0.5)
        held = time.monotonic() - up_at > 30.0
        self._teardown()
        return held

    def _slcand_pids(self):
        """Find the daemonized slcand serving OUR pty (exact argv match on
        /proc cmdlines — never a shell pattern)."""
        pids = []
        for p in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                argv = open(p, "rb").read().split(b"\0")
            except OSError:
                continue
            if (argv and os.path.basename(argv[0]).decode(errors="replace")
                    == "slcand" and PTY_LINK.encode() in argv):
                pids.append(int(p.split("/")[2]))
        return pids

    def _teardown(self):
        if self._iface_present():
            _sh("ip", "link", "set", self.iface, "down")
        for pid in self._slcand_pids():
            for sig, wait in ((15, 1.0), (9, 0)):
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                if wait and self._wait_pid_gone(pid, wait):
                    break
        if self._socat:
            if self._socat.poll() is None:
                self._socat.terminate()
                try:
                    self._socat.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._socat.kill()
            self._socat = None
        if os.path.islink(PTY_LINK):            # socat's symlink, if stranded
            try:
                os.unlink(PTY_LINK)
            except OSError:
                pass

    @staticmethod
    def _wait_pid_gone(pid, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.1)
        return False


# ------------------------------------------------------------- §2 heartbeat --
class HeartbeatListener:
    """Read-only UDP listener for CanTick heartbeats (PROTOCOL.md §2).

    Tracks per-device (`name`) last-seen time and latest fields, computes rx/s
    between datagrams, flags a rising `drop` counter, and raises a (non-fatal)
    contract-version warning when `v` != expected. It binds and receives ONLY —
    nothing is ever sent on the socket. Runs in a daemon thread on the same
    poll-with-timeout pattern the rest of kilodash uses; with no CanTick
    present the thread just sleeps in recvfrom (a 1 s wakeup to stay
    stop-responsive), which is the slow idle.
    """

    def __init__(self, port=29537, expected_version=CONTRACT_VERSION,
                 fresh_secs=FRESH_SECS):
        self.port = int(port)
        self.expected_version = expected_version
        self.fresh_secs = fresh_secs
        self.version_warning = None      # e.g. "cantick-01 speaks v2, want v1"
        self._devices = {}               # name -> record dict
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._sock = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return                                  # idempotent
        self._stop.clear()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.port))
            s.settimeout(1.0)
        except OSError as e:
            log.error("heartbeat bind udp/%s failed: %s", self.port, e)
            return
        self._sock = s
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=3)
        if self._sock:
            self._sock.close()
            self._sock = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self._handle(data)

    def _handle(self, data):
        """Parse one heartbeat datagram (split out for unit tests)."""
        try:
            d = json.loads(data.decode("utf-8", errors="replace"))
        except (ValueError, AttributeError):
            return
        if not isinstance(d, dict):
            return
        name = str(d.get("name") or "cantick")
        now = time.monotonic()
        with self._lock:
            prev = self._devices.get(name)
            rec = {"name": name, "seen": now,
                   "fw": d.get("fw"), "bitrate": d.get("bitrate"),
                   "mode": d.get("mode"), "rx": d.get("rx"),
                   "tx": d.get("tx"), "drop": d.get("drop"),
                   "rssi": d.get("rssi"), "v": d.get("v"),
                   "rx_rate": 0.0, "drop_rise": 0.0}
            if prev:
                dt = now - prev["seen"]
                rx0, rx1 = prev.get("rx"), rec["rx"]
                if (dt > 0 and isinstance(rx0, (int, float))
                        and isinstance(rx1, (int, float)) and rx1 >= rx0):
                    rec["rx_rate"] = (rx1 - rx0) / dt
                d0, d1 = prev.get("drop"), rec["drop"]
                rec["drop_rise"] = prev.get("drop_rise", 0.0)
                if (isinstance(d0, (int, float))
                        and isinstance(d1, (int, float)) and d1 > d0):
                    rec["drop_rise"] = now
            self._devices[name] = rec
        v = d.get("v")
        if v != self.expected_version:
            self.version_warning = (f"{name} speaks contract v{v}, "
                                    f"expected v{self.expected_version}")

    # ---- queries ----
    def is_fresh(self, name):
        with self._lock:
            rec = self._devices.get(name)
        return bool(rec) and time.monotonic() - rec["seen"] <= self.fresh_secs

    def latest(self):
        """Most-recently-seen device record (copy) with computed extras, or
        None. `fresh`: within the freshness window; `drop_rising`: the drop
        counter increased in the last 10 s (the early bus-overrun warning)."""
        with self._lock:
            if not self._devices:
                return None
            rec = dict(max(self._devices.values(), key=lambda r: r["seen"]))
        now = time.monotonic()
        rec["age"] = now - rec["seen"]
        rec["fresh"] = rec["age"] <= self.fresh_secs
        rec["drop_rising"] = now - rec.get("drop_rise", 0.0) <= 10.0 \
            and rec.get("drop_rise", 0.0) > 0
        return rec


# ---------------------------------------------------- §4 provisioning wire ---
def crc16_ccitt_false(data):
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflect, xorout 0.
    Check: crc16_ccitt_false(b"123456789") == 0x29B1."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def frame(body):
    """CTK1|<body>|CRC=XXXX\\n — CRC over everything before `|CRC=` (i.e.
    including the CTK1| prefix; see PROTOCOL.md §4 assumption note)."""
    if not isinstance(body, str) or any(c in body for c in "|\r\n"):
        raise CanTickError("invalid frame body")
    prefix = f"CTK1|{body}"
    crc = crc16_ccitt_false(prefix.encode())
    return f"{prefix}|CRC={crc:04X}\n".encode()


def parse_reply(line):
    """Parse a device reply into (kind, fields). Tolerates framed
    (CTK1|…|CRC=xxxx) and bare replies; a framed reply with a BAD CRC returns
    (None, {}). kind is the first token (ACK/NAK/STATUS); fields are k=v."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = (line or "").strip()
    if not line:
        return None, {}
    if line.startswith("CTK1|"):
        body, sep, tail = line.rpartition("|CRC=")
        if not sep:
            return None, {}
        try:
            if crc16_ccitt_false(body.encode()) != int(tail, 16):
                return None, {}
        except ValueError:
            return None, {}
        line = body[len("CTK1|"):]
    parts = line.split()
    kind = parts[0] if parts else None
    fields = {}
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        if eq:
            fields[k] = v
    return kind, fields


def _b64(value):
    return base64.b64encode(value.encode()).decode()


def set_creds_body(slot, ssid, psk):
    if slot not in ("primary", "fallback"):
        raise CanTickError(f"invalid creds slot: {slot!r}")
    if not ssid:
        raise CanTickError("empty ssid")
    return f"SET_CREDS slot={slot} ssid={_b64(ssid)} psk={_b64(psk or '')}"


def set_net_body(bitrate, listen_only):
    if bitrate not in SLCAN_BITRATE_CODES:
        raise CanTickError(f"unsupported bitrate: {bitrate!r}")
    return f"SET_NET bitrate={bitrate} listen_only={1 if listen_only else 0}"


def wifi_creds_nm(iface="wlan0"):
    """The Pi's CURRENT WiFi SSID+PSK via NetworkManager (the Phase-0 finding
    for this unit; a wpa_supplicant build would parse wpa_supplicant.conf
    instead). Needs root: `nmcli -s` reveals the secret. Never log the PSK."""
    con = _sh("nmcli", "-t", "-g", "GENERAL.CONNECTION",
              "device", "show", iface).stdout.strip()
    if not con:
        raise CanTickError(f"{iface} is not connected")
    ssid = _sh("nmcli", "-t", "-g", "802-11-wireless.ssid",
               "connection", "show", con).stdout.strip() or con
    psk = _sh("nmcli", "-s", "-t", "-g", "802-11-wireless-security.psk",
              "connection", "show", con).stdout.strip()
    if not psk:
        raise CanTickError(f"no PSK readable for '{con}' (need root)")
    return ssid, psk


class CanTickProvisioner:
    """One-time USB credential push (PROTOCOL.md §4) over CDC serial.

    provision() pushes primary (the Pi's current WiFi) + fallback (the
    Phase-5 AP) credentials, then SET_NET, COMMIT and a GET_STATUS verify.
    Retries once on `NAK err=crc`. PSKs never appear in logs or status
    strings — only command verbs do.
    """

    def __init__(self, port, timeout=3.0):
        self.port = port
        self.timeout = timeout

    def provision(self, primary, fallback, bitrate, listen_only):
        """primary/fallback: (ssid, psk) tuples. Returns (ok, message)."""
        bodies = [set_creds_body("primary", *primary),
                  set_creds_body("fallback", *fallback),
                  set_net_body(bitrate, listen_only),
                  "COMMIT"]
        try:
            import serial               # pyserial, present on this image
            with serial.Serial(self.port, 115200,
                               timeout=self.timeout) as ser:
                for body in bodies:
                    verb = body.split()[0]
                    kind, fields = self._xfer(ser, body)
                    if kind != "ACK":
                        err = fields.get("err", kind or "no reply")
                        return False, f"{verb}: {err}"
                kind, fields = self._xfer(ser, "GET_STATUS")
                if kind != "STATUS":
                    return False, f"GET_STATUS: {kind or 'no reply'}"
                if fields.get("prov") not in ("1", None):
                    return False, f"device reports prov={fields.get('prov')}"
                return True, "provisioned (prov=%s)" % fields.get("prov", "?")
        except CanTickError:
            raise
        except Exception as e:          # noqa: BLE001 — serial layer errors
            return False, f"serial: {e}"

    def _xfer(self, ser, body):
        for attempt in (0, 1):
            ser.write(frame(body))
            ser.flush()
            kind, fields = parse_reply(ser.readline())
            if kind == "NAK" and fields.get("err") == "crc" and attempt == 0:
                log.info("cantick prov: NAK err=crc on %s, retrying",
                         body.split()[0])
                continue
            return kind, fields
        return None, {}


# --------------------------------------------------------- §5 AP fallback ----
class CanTickAP:
    """Reversible WPA2 fallback AP on wlan0 (PROTOCOL.md §5).

    start() refuses unless there is genuinely NO uplink (no default route and
    wlan0 not associated). It records wlan0's prior NetworkManager management
    state, unmanages it (this IS pausing the uplink watchdog on this unit —
    NM autoconnect is the standing reconnector; the per-screen watchdogs in
    wifisniff/kismet aren't running while the CAN screen is open), assigns the
    static gateway address and launches hostapd + dnsmasq from generated
    configs under RUN_DIR. stop() reverses every step and removes the configs
    (they hold the PSK). Context-manager friendly; stop() is idempotent and
    safe to call from `finally`. wlan1/ALFA is never touched.
    """

    IFACE = "wlan0"                     # fixed: the AP only ever uses wlan0

    def __init__(self, ssid, psk, gateway="192.168.42.1"):
        for label, val in (("ssid", ssid), ("psk", psk)):
            if not val or any(c in val for c in "\r\n\0"):
                raise CanTickError(f"invalid AP {label}")
        if not 8 <= len(psk) <= 63:
            raise CanTickError("AP psk must be 8..63 chars")
        self.ssid, self.psk = ssid, psk
        self.gateway = str(ipaddress.IPv4Address(gateway))
        self.active = False
        self._prior_managed = True
        self._procs = []

    # ---- state probes ----
    def uplink_present(self):
        route = _sh("ip", "-j", "route").stdout
        try:
            has_default = any(r.get("dst") == "default"
                              for r in json.loads(route or "[]"))
        except ValueError:
            has_default = False
        state = _sh("nmcli", "-t", "-g", "GENERAL.STATE",
                    "device", "show", self.IFACE).stdout
        associated = state.strip().startswith("100")
        return has_default or associated

    # ---- config generation (unit-testable, no side effects) ----
    def hostapd_conf(self):
        return (f"interface={self.IFACE}\n"
                "driver=nl80211\n"
                f"ssid={self.ssid}\n"
                "hw_mode=g\n"
                "channel=6\n"
                "wpa=2\n"
                "wpa_key_mgmt=WPA-PSK\n"
                f"wpa_passphrase={self.psk}\n"
                "rsn_pairwise=CCMP\n")

    def dnsmasq_conf(self):
        net = ipaddress.IPv4Network(f"{self.gateway}/24", strict=False)
        lo, hi = net.network_address + 10, net.network_address + 100
        return (f"interface={self.IFACE}\n"
                "bind-interfaces\n"
                f"listen-address={self.gateway}\n"
                f"dhcp-range={lo},{hi},{net.netmask},12h\n"
                f"dhcp-option=option:router,{self.gateway}\n"
                f"address=/scottina.local/{self.gateway}\n")

    # ---- lifecycle ----
    def start(self):
        """Returns (ok, message). Refuses (False, why) rather than raising for
        the expected cases so the screen can just show the message."""
        if self.active:
            return True, "AP already up"
        if self.uplink_present():
            return False, "uplink present — AP not needed"
        for binary in ("hostapd", "dnsmasq"):
            if not shutil.which(binary):
                return False, f"{binary} not installed"
        state = _sh("nmcli", "-t", "-g", "GENERAL.STATE",
                    "device", "show", self.IFACE).stdout.lower()
        self._prior_managed = "unmanaged" not in state
        log.info("cantick AP: starting on %s (prior managed=%s)",
                 self.IFACE, self._prior_managed)
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            hpath = os.path.join(RUN_DIR, "hostapd.conf")
            dpath = os.path.join(RUN_DIR, "dnsmasq.conf")
            for path, text in ((hpath, self.hostapd_conf()),
                               (dpath, self.dnsmasq_conf())):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                             0o600)                     # confs hold the PSK
                with os.fdopen(fd, "w") as f:
                    f.write(text)
            _sh("nmcli", "device", "set", self.IFACE, "managed", "no")
            _sh("ip", "addr", "flush", "dev", self.IFACE)
            _sh("ip", "addr", "add", f"{self.gateway}/24", "dev", self.IFACE)
            _sh("ip", "link", "set", self.IFACE, "up")
            self._procs = [
                subprocess.Popen(["hostapd", hpath],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL),
                subprocess.Popen(["dnsmasq", f"--conf-file={dpath}",
                                  "--keep-in-foreground", "--pid-file="],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL),
            ]
            time.sleep(1.0)
            dead = [p.args[0] for p in self._procs if p.poll() is not None]
            if dead:
                raise CanTickError(f"{os.path.basename(str(dead[0]))} exited")
        except Exception as e:          # noqa: BLE001 — must never strand wlan0
            self.stop()
            return False, f"AP failed: {e}"
        self.active = True
        return True, f"AP up: {self.ssid} @ {self.gateway}"

    def stop(self):
        """Full reversal — safe to call at any point, any number of times."""
        for p in self._procs:
            try:
                p.terminate()
                p.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    p.kill()
                except OSError:
                    pass
        self._procs = []
        _sh("ip", "addr", "flush", "dev", self.IFACE)
        if self._prior_managed:
            # restoring management resumes NM autoconnect — the uplink
            # watchdog picks wlan0 back up from here
            _sh("nmcli", "device", "set", self.IFACE, "managed", "yes")
        for name in ("hostapd.conf", "dnsmasq.conf"):
            try:
                os.unlink(os.path.join(RUN_DIR, name))
            except OSError:
                pass
        if self.active:
            log.info("cantick AP: down, %s restored", self.IFACE)
        self.active = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
