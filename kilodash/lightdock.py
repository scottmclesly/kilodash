"""Light Dock — Prime-side frame codec, client and sync engine
(DOCK-PROTOCOL.md, mirrored verbatim in this repo and Scottina-Light).

Scope — PROVISIONING + RETRIEVAL ONLY, enforced by the protocol's positive
allow-list: push wall-clock time, push decode tables (TABLES.md §5 export
shape), pull and then verified-delete Light's CLOSED SD logs. Nothing here
can trigger transmission, start a capture or execute anything on Light, and
per §6 a dock session suspends Light's logging — the engine states that in
the session log rather than hiding it.

The guard-rail principle mirrors cantick.py/la.py: every frame is built by a
validated builder, every read is bounded (§2 resync rule — never allocate or
block on an unvalidated LEN), every request carries a timeout (§5) so a
wedged Light degrades to a truthful log line, never a hung screen. The
shared conformance asset is To-DoLists/dock-vectors.json (§10);
tests/test_lightdock.py runs this codec and the whole engine against
tests/fakelight.py replaying those vectors on a PTY — no firmware in the
loop.

Pieces (wired into the Light Dock screen's enter/exit lifecycle, Phase 3):
  * frame codec       — build_frame / FrameScanner + per-command payload
                        builders and response parsers, all module-level and
                        unit-testable.
  * DockClient        — strictly sequential request/response over the CDC
                        port (Prime is the only initiator, §1). SEQ echo is
                        checked; ERROR frames raise DockRemoteError.
  * clock_quality()   — Prime's HONEST clock quality. Never claims `ntp`
                        unless NTP is synchronized right now; quality 0 with
                        a nonzero epoch is never sent (§4 SET_CLOCK).
  * LightDockSync     — the session: HELLO → clock → tables → logs → BYE,
                        emitting timestamped session-log events the screen
                        renders. Resume logic is just the diff run again —
                        no resume state is persisted anywhere (§4 COMMIT).
"""

import hashlib
import logging
import os
import select
import struct
import threading
import time

from .cantick import crc16_ccitt_false as crc16

log = logging.getLogger("kilodash.lightdock")

PROTO_VERSION = 1
SOF = 0xA5
FRAME_OVERHEAD = 7              # SOF + TYPE + SEQ + LEN(2) + CRC(2), §2
DEFAULT_MAX_PAYLOAD = 1024      # Light's expected v1 value; §3: read, not assumed

TYPES = {"HELLO": 0x01, "SET_CLOCK": 0x02, "LIST": 0x03, "PUT": 0x04,
         "COMMIT": 0x05, "GET": 0x06, "DELETE": 0x07, "BYE": 0x08,
         "ERROR": 0xEF}
TYPE_NAMES = {v: k for k, v in TYPES.items()}

ERRORS = {0x0001: "ERR_BAD_CRC", 0x0002: "ERR_BAD_FRAME",
          0x0003: "ERR_UNKNOWN_TYPE", 0x0004: "ERR_NO_SD",
          0x0005: "ERR_PATH_REJECTED", 0x0006: "ERR_NOT_FOUND",
          0x0007: "ERR_IO", 0x0008: "ERR_HASH_MISMATCH", 0x0009: "ERR_BUSY",
          0x000A: "ERR_UNSUPPORTED_VER", 0x000B: "ERR_TOO_LARGE"}
ERROR_CODES = {v: k for k, v in ERRORS.items()}

# §5 — Prime puts a timeout on EVERY request. DELETE is 60 s (ratified
# amendment): the redock resume path deletes without a cached digest, so
# Light may rehash a multi-MB file from a cold card.
TIMEOUT_DEFAULT = 2.0
TIMEOUTS = {"COMMIT": 30.0, "DELETE": 60.0}

CLOCK_QUALITY_NAMES = {0: "unsynced", 1: "rtc", 2: "ntp", 3: "gps"}
# Semantic ordering (DOCK-PROTOCOL.md v1.1): ntp ≥ gps > rtc > unsynced.
# The numeric wire values are labels, not a quality scale. A v1-foundation
# Light rejects the unknown value 3 per its §7 reject pass; the engine then
# underclaims as `rtc` — a downgrade is honest, an upgrade never is.
CLOCK_QUALITY_V1_FALLBACK = {3: 1}
# System time earlier than this cannot be real (Pi 5 RTC with no coin cell
# resets across power-off); an implausible clock is reported unsynced.
CLOCK_PLAUSIBLE_FLOOR = 1735689600      # 2025-01-01T00:00:00Z

CAPTURES_DIR = "/opt/kilodash/captures"
PULLED_PREFIX = "light-"        # pulled logs land flat in captures/ so they
                                # ride the Files screen's USB offload for free


class DockError(Exception):
    """Protocol or transport failure that ends the current request."""


class DockTimeout(DockError):
    """No valid response inside the §5 window."""


class DockRemoteError(DockError):
    """Light answered with an ERROR frame."""

    def __init__(self, code, message):
        self.code = code
        self.code_name = ERRORS.get(code, "ERR_0x%04X" % code)
        self.remote_message = message
        super().__init__("%s: %s" % (self.code_name, message))


# -------------------------------------------------------------- pack/unpack --
def pack_str(value):
    """§2 string: u8 length + UTF-8 bytes, no NUL terminator."""
    b = value if isinstance(value, bytes) else str(value).encode("utf-8")
    if len(b) > 255:
        raise DockError("string too long for wire: %d bytes" % len(b))
    return bytes([len(b)]) + b


def unpack_str(buf, off):
    """Returns (text, new_offset); raises DockError on truncation."""
    if off >= len(buf):
        raise DockError("truncated string length")
    n = buf[off]
    end = off + 1 + n
    if end > len(buf):
        raise DockError("truncated string body")
    return buf[off + 1:end].decode("utf-8", errors="replace"), end


def check_path(path):
    """Prime-side twin of Light's §7 reject pass (defense in depth — a path
    that would be rejected over there is a bug over here)."""
    if (not path or not path.startswith("/") or ".." in path
            or "\\" in path or "\0" in path):
        raise DockError("path fails the reject pass: %r" % path)
    return path


# ------------------------------------------------------------------ framing --
def build_frame(type_name, seq, payload=b""):
    """§2 layout: SOF | TYPE | SEQ | LEN(u16le) | PAYLOAD | CRC16le over
    TYPE..PAYLOAD."""
    body = bytes([TYPES[type_name], seq & 0xFF]) + struct.pack("<H", len(payload)) + payload
    return bytes([SOF]) + body + struct.pack("<H", crc16(body))


class FrameScanner:
    """§2 bounded, sync-safe scanner. feed() bytes in, get events out:
      ("frame", type_byte, seq, payload)   — a CRC-valid frame
      ("badcrc", type_byte, seq)           — complete frame, CRC failed
    A LEN over max_payload or a CRC failure discards exactly one byte and
    rescans; nothing is ever allocated or blocked on an unvalidated LEN."""

    def __init__(self, max_payload=DEFAULT_MAX_PAYLOAD):
        self.max_payload = max_payload
        self._buf = bytearray()

    def feed(self, data):
        self._buf.extend(data)
        events = []
        buf = self._buf
        while True:
            sof = buf.find(bytes([SOF]))
            if sof < 0:
                buf.clear()
                break
            del buf[:sof]
            if len(buf) < 5:
                break                           # header incomplete — wait
            ln = struct.unpack_from("<H", buf, 3)[0]
            if ln > self.max_payload:
                del buf[:1]                     # unvalidated LEN: resync
                continue
            end = 5 + ln + 2
            if len(buf) < end:
                break                           # frame incomplete — wait
            body = bytes(buf[1:5 + ln])
            if struct.unpack_from("<H", buf, 5 + ln)[0] != crc16(body):
                events.append(("badcrc", body[0], body[1]))
                del buf[:1]
                continue
            events.append(("frame", body[0], body[1], body[4:]))
            del buf[:end]
        return events


# ------------------------------------------------- request payload builders --
def set_clock_payload(epoch, quality):
    if quality not in CLOCK_QUALITY_NAMES:
        raise DockError("bad clock quality: %r" % (quality,))
    if quality == 0 and epoch:
        # §4: a bad clock is labeled, never laundered into Light's logs
        raise DockError("refusing to send unsynced quality with a nonzero epoch")
    return struct.pack("<Q", int(epoch)) + bytes([quality])


def list_payload(dirname, want_hashes, start_index=0):
    if dirname not in ("/logs/", "/tables/"):
        raise DockError("LIST accepts /logs/ or /tables/ only")
    return (pack_str(dirname) + bytes([1 if want_hashes else 0])
            + struct.pack("<H", start_index))


def put_payload(path, offset, chunk):
    check_path(path)
    return (pack_str(path) + struct.pack("<I", offset)
            + struct.pack("<H", len(chunk)) + bytes(chunk))


def commit_payload(path, sha256_digest):
    check_path(path)
    if len(sha256_digest) != 32:
        raise DockError("COMMIT needs a 32-byte sha256 digest")
    return pack_str(path) + bytes(sha256_digest)


def get_payload(path, offset, max_len):
    check_path(path)
    return pack_str(path) + struct.pack("<I", offset) + struct.pack("<H", max_len)


def delete_payload(path, sha256_digest):
    check_path(path)
    if len(sha256_digest) != 32:
        raise DockError("DELETE needs a 32-byte sha256 digest")
    return pack_str(path) + bytes(sha256_digest)


# ------------------------------------------------------- response parsers ---
def parse_hello(p):
    off = 0
    ver = struct.unpack_from("<H", p, off)[0]; off += 2
    product, off = unpack_str(p, off)
    fw, off = unpack_str(p, off)
    if len(p) < off + 14:
        raise DockError("short HELLO response")
    sd = p[off]; off += 1
    epoch = struct.unpack_from("<Q", p, off)[0]; off += 8
    quality, set_boot = p[off], p[off + 1]; off += 2
    max_payload = struct.unpack_from("<H", p, off)[0]; off += 2
    flags = p[off]
    return {"proto_version": ver, "product": product, "fw_version": fw,
            "sd_present": sd, "clock_epoch": epoch, "clock_quality": quality,
            "clock_set_this_boot": set_boot, "max_payload": max_payload,
            "flags": flags, "logging_was_active": bool(flags & 1),
            "logging_suspended": bool(flags & 2)}


def parse_set_clock(p):
    if len(p) != 9:
        raise DockError("short SET_CLOCK response")
    return {"ok": p[0], "epoch_echo": struct.unpack_from("<Q", p, 1)[0]}


def parse_list(p):
    """One PAGE of a LIST response (§4: LIST is paginated, and it must be)."""
    if len(p) < 5:
        raise DockError("short LIST response")
    start_index = struct.unpack_from("<H", p)[0]
    count = struct.unpack_from("<H", p, 2)[0]
    off, entries = 4, []
    for _ in range(count):
        name, off = unpack_str(p, off)
        if len(p) < off + 13:
            raise DockError("truncated LIST entry")
        size = struct.unpack_from("<I", p, off)[0]; off += 4
        mtime = struct.unpack_from("<Q", p, off)[0]; off += 8
        has_sha = p[off]; off += 1
        entry = {"name": name, "size": size, "mtime": mtime, "sha256": None}
        if has_sha:
            if len(p) < off + 32:
                raise DockError("truncated LIST sha256")
            entry["sha256"] = p[off:off + 32].hex(); off += 32
        entries.append(entry)
    if off + 1 != len(p):
        raise DockError("LIST response length mismatch")
    return {"start_index": start_index, "count": count, "entries": entries,
            "more": p[off]}


def parse_put(p):
    if len(p) != 5:
        raise DockError("short PUT response")
    return {"ok": p[0], "total_bytes_staged": struct.unpack_from("<I", p, 1)[0]}


def parse_get(p):
    if len(p) < 7:
        raise DockError("short GET response")
    offset = struct.unpack_from("<I", p)[0]
    ln = struct.unpack_from("<H", p, 4)[0]
    if len(p) != 4 + 2 + ln + 1:
        raise DockError("GET response length mismatch")
    return {"offset": offset, "len": ln, "data": p[6:6 + ln], "eof": p[6 + ln]}


def parse_ok(p):
    if len(p) != 1:
        raise DockError("short ok response")
    return {"ok": p[0]}


def parse_error(p):
    code = struct.unpack_from("<H", p)[0]
    message, _ = unpack_str(p, 2)
    return {"code": code, "message": message}


RESPONSE_PARSERS = {"HELLO": parse_hello, "SET_CLOCK": parse_set_clock,
                    "LIST": parse_list, "PUT": parse_put, "GET": parse_get,
                    "COMMIT": parse_ok, "DELETE": parse_ok, "BYE": parse_ok}


# ------------------------------------------------------------------- client --
class DockClient:
    """Strictly sequential request/response over Light's CDC port (§1).

    `port` is a /dev tty path (opened with pyserial; the nominal baud is
    ignored by CDC) or an already-open file descriptor (tests hand it a PTY).
    One request outstanding, ever; the response must echo the request's SEQ.
    An ERROR frame raises DockRemoteError; silence raises DockTimeout after
    the §5 window. Every read is bounded by the §2 scanner.
    """

    def __init__(self, port):
        self._lock = threading.Lock()
        self._seq = 0
        self.max_payload = DEFAULT_MAX_PAYLOAD
        self._scanner = FrameScanner(DEFAULT_MAX_PAYLOAD)
        self.activity = 0               # frames moved; screen maps to pulses
        if isinstance(port, int):
            self._fd, self._ser = port, None
        else:
            import serial               # pyserial, present on this image
            # A plain open is safe on the SAMD51 (only the 1200-baud touch
            # resets it); DTR asserted tells TinyUSB-style CDC "host is here".
            self._ser = serial.Serial(port, 115200, timeout=0)
            self._fd = self._ser.fileno()

    def close(self):
        if self._ser is not None:
            self._ser.close()
        # a bare fd belongs to whoever handed it to us

    def request(self, type_name, payload=b"", timeout=None):
        """Send one frame, return the parsed response payload dict."""
        if len(payload) > self.max_payload:
            raise DockError("payload %d exceeds Light's max_payload %d"
                            % (len(payload), self.max_payload))
        timeout = timeout or TIMEOUTS.get(type_name, TIMEOUT_DEFAULT)
        with self._lock:                # §1: exactly one outstanding request
            self._seq = self._seq % 255 + 1
            seq = self._seq
            os.write(self._fd, build_frame(type_name, seq, payload))
            self.activity += 1
            deadline = time.monotonic() + timeout
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise DockTimeout("%s: no response in %.1fs"
                                      % (type_name, timeout))
                r, _, _ = select.select([self._fd], [], [], remain)
                if not r:
                    continue
                data = os.read(self._fd, 4096)
                if not data:
                    raise DockError("port closed")
                for ev in self._scanner.feed(data):
                    if ev[0] == "badcrc":
                        log.warning("lightdock: response frame failed CRC")
                        continue
                    _, tbyte, rseq, rpayload = ev
                    if rseq != seq:
                        log.warning("lightdock: stale frame seq=%d "
                                    "(expected %d)", rseq, seq)
                        continue
                    self.activity += 1
                    if tbyte == TYPES["ERROR"]:
                        err = parse_error(rpayload)
                        raise DockRemoteError(err["code"], err["message"])
                    if tbyte != TYPES[type_name]:
                        raise DockError("response type 0x%02x to %s"
                                        % (tbyte, type_name))
                    return RESPONSE_PARSERS[type_name](rpayload)

    # ---- commands ----
    def hello(self):
        info = self.request("HELLO")
        # §3: read max_payload, never assume it — it also bounds our scanner
        self.max_payload = info["max_payload"]
        self._scanner.max_payload = info["max_payload"]
        return info

    def set_clock(self, epoch, quality):
        return self.request("SET_CLOCK", set_clock_payload(epoch, quality))

    def list_dir(self, dirname, want_hashes):
        """The complete directory: walks §4 pagination until more=0.

        The whole walk finishes inside this call, which is what upholds the
        §4 ordering rule — Prime never mutates a directory mid-pagination
        because no mutation can be issued while this loop owns the port.
        """
        entries, start = [], 0
        while True:
            page = self.request("LIST",
                                list_payload(dirname, want_hashes, start))
            if page["start_index"] != start:
                # the §4 desync tell: indices shifted under the walk
                raise DockError("LIST desync: asked from %d, got page at %d"
                                % (start, page["start_index"]))
            entries.extend(page["entries"])
            if not page["more"]:
                return {"count": len(entries), "entries": entries}
            if not page["count"]:
                raise DockError("LIST stalled: more=1 with an empty page")
            start += page["count"]

    def put(self, path, offset, chunk):
        return self.request("PUT", put_payload(path, offset, chunk))

    def commit(self, path, sha256_digest):
        return self.request("COMMIT", commit_payload(path, sha256_digest))

    def get(self, path, offset, max_len):
        return self.request("GET", get_payload(path, offset, max_len))

    def delete(self, path, sha256_digest):
        return self.request("DELETE", delete_payload(path, sha256_digest))

    def bye(self):
        return self.request("BYE")

    # ---- chunk sizing (§3 — wire format is fixed; these are Prime's choices)
    def put_chunk_size(self, path):
        """Largest chunk so the whole PUT payload fits max_payload."""
        return self.max_payload - 7 - len(path.encode("utf-8"))

    def get_chunk_size(self):
        """Largest GET max_len whose RESPONSE payload (7 bytes of overhead)
        still fits max_payload — our scanner enforces that bound on reads."""
        return self.max_payload - 7


# ------------------------------------------------------------- clock honesty --
def clock_quality(runner=None):
    """(quality, name) for SET_CLOCK — honest, never optimistic (§4).

    `gps` if chrony is synchronized to the GPS refclock right now (better
    than `rtc`/`unsynced` and network-independent — GPS-Integration Phase
    1); `ntp` only if NTP is synchronized RIGHT NOW (timedatectl); `rtc`
    only if a hardware RTC exists AND system time is plausible (Pi 5's RTC
    holds through power-off only with the coin cell fitted); otherwise
    `unsynced`, and the engine sends nothing rather than stamp Light's
    logs with a lie.

    Ordering note: chrony synced to the GPS refclock also reports
    NTPSynchronized=yes through timedatectl, so the GPS check must run
    first — claiming `ntp` off a GPS-disciplined clock would be the
    optimistic lie this function exists to prevent.
    """
    if runner is None:
        from . import system
        runner = system.run
    tracking = runner(["chronyc", "tracking"]) or ""
    lines = tracking.splitlines()
    synced = any(l.startswith("Leap status") and "Normal" in l
                 for l in lines)
    ref_is_gps = any(l.startswith("Reference ID") and "(GPS)" in l
                     for l in lines)
    if synced and ref_is_gps:
        return 3, "gps"
    if runner(["timedatectl", "show", "-p", "NTPSynchronized",
               "--value"]).strip().lower() == "yes":
        return 2, "ntp"
    if os.path.exists("/sys/class/rtc/rtc0") and time.time() > CLOCK_PLAUSIBLE_FLOOR:
        return 1, "rtc"
    return 0, "unsynced"


# -------------------------------------------------------------- sync engine --
def _safe_leaf(name):
    """A LIST entry name must be a bare leaf (§4 LIST); anything else from
    the wire is discarded, not sanitized."""
    return (name and "/" not in name and "\\" not in name
            and "\0" not in name and name not in (".", "..")
            and not name.startswith("."))


class LightDockSync:
    """One dock session: HELLO → clock → tables → logs → BYE.

    Pure logic, no drawing: the screen constructs one of these per dock (or
    per Re-sync tap), runs run() on a system.Task thread, and renders
    .events / .state / .progress. Prime always wins — stale tables on Light
    are overwritten, never merged; logs are pulled, verified, then deleted.
    Interruption safety is COMMIT atomicity plus rerunning the diff: no
    resume state exists on either side.
    """

    SYNCING, COMPLETE, INTERRUPTED = "syncing", "complete", "interrupted"

    def __init__(self, port_or_client, *, pull_logs=True,
                 captures_dir=CAPTURES_DIR, on_event=None,
                 clock_source=clock_quality, time_fn=time.time):
        self._port = port_or_client
        self.pull_logs = pull_logs
        self.captures_dir = captures_dir
        self._on_event = on_event
        self._clock_source = clock_source
        self._time = time_fn
        self.client = None
        self.state = self.SYNCING
        self.events = []                # (wall_epoch, text) session-log lines
        self.progress = {"bytes_done": 0, "bytes_total": 0}
        # Phase + peer identity, surfaced for the web mirror's `lightdock`
        # model (WEB-PROTOCOL.md §4.5). The panel infers phase from the newest
        # log line; the mirror needs it as a field, and `hello` already
        # carries the device info — it was previously parsed and discarded.
        self.phase = "idle"             # idle|hello|clock|tables|logs|done|error
        self.info = None                # parsed HELLO: product, fw_version, …
        self.counts = {"tables_pushed": 0, "tables_skipped": 0,
                       "tables_failed": 0, "logs_pulled": 0,
                       "logs_deleted": 0, "logs_failed": 0}

    # ---- session log (the screen's bottom pane; every line a truth) ----
    def _log(self, text):
        self.events.append((self._time(), text))
        log.info("lightdock: %s", text)
        if self._on_event:
            self._on_event(text)

    # ---- the session ----
    def run(self):
        """Returns the final state. Never raises for protocol/transport
        trouble — a wedged Light degrades to truthful log lines."""
        owns_client = not isinstance(self._port, DockClient)
        try:
            self.client = (DockClient(self._port) if owns_client
                           else self._port)
        except Exception as e:          # noqa: BLE001 — port open failed
            self._log("port open failed: %s" % e)
            self.phase = "error"
            self.state = self.INTERRUPTED
            return self.state
        degraded = False
        try:
            self.phase = "hello"
            info = self.client.hello()
            self.info = info
            self._log("hello: %s %s (sd %s, max_payload %d)"
                      % (info["product"], info["fw_version"],
                         "present" if info["sd_present"] else "MISSING",
                         info["max_payload"]))
            if info["logging_suspended"]:
                self._log("logging suspended for dock%s — gap is recorded "
                          "on Light (§6)"
                          % (" (was active)" if info["logging_was_active"]
                             else ""))
            if info["proto_version"] != PROTO_VERSION:
                degraded = True
                self._log("Light speaks protocol v%d, Prime v%d — degraded "
                          "to clock-set only (§8)"
                          % (info["proto_version"], PROTO_VERSION))
            self.phase = "clock"
            self._sync_clock(info)
            if degraded:
                self._log("tables/logs not attempted (version mismatch)")
            elif not info["sd_present"]:
                self._log("tables skipped, logs skipped — no SD in Light")
            else:
                self.phase = "tables"
                self._sync_tables()
                if self.pull_logs:
                    self.phase = "logs"
                    self._sync_logs()
                else:
                    self._log("logs: auto-pull is off")
            self.client.bye()
            self._log("session complete")
            self.phase = "done"
            self.state = self.COMPLETE
        except (DockError, OSError) as e:
            self._log("interrupted: %s" % e)
            self.phase = "error"
            try:
                self.client.bye()       # best effort; the 10 s watchdog is
            except (DockError, OSError):    # Light's real safety net (§5)
                pass
            self.state = self.INTERRUPTED
        finally:
            if owns_client and self.client:
                self.client.close()
        return self.state

    # ---- clock ----
    def _sync_clock(self, hello_info):
        quality, qname = self._clock_source()
        if quality == 0:
            self._log("clock NOT sent — Prime is unsynced (a bad clock is "
                      "labeled, never laundered)")
            return
        epoch = int(self._time())
        try:
            self.client.set_clock(epoch, quality)
        except DockRemoteError:
            fallback = CLOCK_QUALITY_V1_FALLBACK.get(quality)
            if fallback is None:
                raise
            # A pre-v1.1 Light rejects the `gps` flag value per its §7
            # reject pass. Underclaim and resend: a downgrade is honest.
            self.client.set_clock(epoch, fallback)
            self._log("clock quality %s unknown to this Light — sent as %s "
                      "(underclaim, never overclaim)"
                      % (qname, CLOCK_QUALITY_NAMES[fallback]))
            qname = CLOCK_QUALITY_NAMES[fallback]
        prior = ("was %s" % CLOCK_QUALITY_NAMES.get(
            hello_info["clock_quality"], "?")
            if hello_info["clock_epoch"] else "was never set")
        self._log("clock → set (%s; %s)" % (qname, prior))

    # ---- tables ----
    def _local_tables(self):
        """Enabled + verified tables as (leaf_name, abs_path), in the
        TABLES.md §5 export shape: <name>.json plus its manifest sidecar."""
        try:
            from tables import store
        except ImportError:
            self._log("tables store unavailable — push skipped")
            return []
        out = []
        for t in store.list_tables():
            if not t["enabled"]:
                continue
            for path in (store.table_path(t["name"]),
                         store.meta_path(t["name"])):
                if os.path.isfile(path):
                    out.append((os.path.basename(path), path))
        return out

    def _sync_tables(self):
        local = self._local_tables()
        if not local:
            self._log("tables: none enabled — nothing to push")
            return
        remote = {e["name"]: e["sha256"]
                  for e in self.client.list_dir("/tables/", True)["entries"]}
        todo = []
        for leaf, path in local:
            with open(path, "rb") as f:
                content = f.read()
            digest = hashlib.sha256(content).hexdigest()
            if remote.get(leaf) == digest:
                self.counts["tables_skipped"] += 1
            else:
                todo.append((leaf, content))
        total = len(local)
        if not todo:
            self._log("tables %d/%d — all current" % (total, total))
            return
        self.progress["bytes_total"] += sum(len(c) for _, c in todo)
        for leaf, content in todo:
            dest = "/tables/" + leaf
            try:
                chunk_size = self.client.put_chunk_size(dest)
                for off in range(0, len(content) or 1, chunk_size):
                    self.client.put(dest, off, content[off:off + chunk_size])
                    self.progress["bytes_done"] += \
                        len(content[off:off + chunk_size])
                self.client.commit(dest, hashlib.sha256(content).digest())
                self.counts["tables_pushed"] += 1
                self._log("tables %d/%d — pushed %s"
                          % (self.counts["tables_pushed"]
                             + self.counts["tables_skipped"], total, leaf))
            except DockRemoteError as e:
                self.counts["tables_failed"] += 1
                self._log("tables — %s FAILED (%s)" % (leaf, e))

    # ---- logs ----
    def _sync_logs(self):
        entries = [e for e in self.client.list_dir("/logs/", False)["entries"]
                   if _safe_leaf(e["name"])]
        if not entries:
            self._log("logs: none on Light")
            return
        os.makedirs(self.captures_dir, exist_ok=True)
        self.progress["bytes_total"] += sum(e["size"] for e in entries)
        failures = []
        for e in entries:
            try:
                self._pull_one(e)
            except DockRemoteError as err:
                self.counts["logs_failed"] += 1
                failures.append("%s: %s" % (e["name"], err.code_name))
        tail = (" (verify failed: %s)" % "; ".join(failures)) if failures else ""
        self._log("logs: %d pulled, %d deleted%s"
                  % (self.counts["logs_pulled"], self.counts["logs_deleted"],
                     tail))

    def _pull_one(self, entry):
        name, size = entry["name"], entry["size"]
        remote_path = "/logs/" + name
        dest = os.path.join(self.captures_dir, PULLED_PREFIX + name)

        # Already pulled? (name+size match) — verify by digest via DELETE
        # itself: Light compares against what it SENT, we hash what we HAVE.
        if os.path.isfile(dest) and os.path.getsize(dest) == size:
            digest = self._sha_file(dest)
            try:
                self.client.delete(remote_path, digest)
                self.counts["logs_deleted"] += 1
                self._log("logs — %s already pulled; deleted on Light" % name)
                return
            except DockRemoteError as err:
                if err.code != ERROR_CODES["ERR_HASH_MISMATCH"]:
                    raise
                # same name+size, different bytes: pull it, keep both

        part = dest + ".part"
        h = hashlib.sha256()
        received = 0
        chunk = self.client.get_chunk_size()
        with open(part, "wb") as f:
            while True:
                r = self.client.get(remote_path, received, chunk)
                f.write(r["data"])
                h.update(r["data"])
                received += r["len"]
                self.progress["bytes_done"] += r["len"]
                if r["eof"]:
                    break
                if r["len"] == 0:
                    raise DockError("GET stalled at offset %d of %s"
                                    % (received, remote_path))
        digest = h.digest()

        final = dest
        if os.path.isfile(dest):
            if self._sha_file(dest) == digest:
                os.unlink(part)                 # byte-identical: dedupe
            else:
                n = 1                           # same name, different data:
                while os.path.isfile(final):    # keep both, never overwrite
                    final = "%s.%d" % (dest, n)
                    n += 1
                os.replace(part, final)
        else:
            os.replace(part, final)
        self.counts["logs_pulled"] += 1

        # The scary step, defended (§4 DELETE): only unlink what verified.
        self.client.delete(remote_path, digest)
        self.counts["logs_deleted"] += 1
        self._log("logs — pulled %s (%d B), verified, deleted on Light"
                  % (name, received))

    @staticmethod
    def _sha_file(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.digest()
