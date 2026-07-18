"""LAN Scan safety core + nmap runner.

Scope — DIAGNOSTICS ONLY. This screen answers three questions and nothing else:
  * What devices are alive on my subnet?      (Discover)
  * What services/versions are they running?  (Services)
  * Is an expected port open on a known host?  (Ports)
plus a best-effort OS guess (Identify). It is explicitly NOT capable of
evasion, NSE scripts, vuln probing, or spoofing. See LAN-Scan-Refactor-TODO.md.

The guard-rail principle: every scan command is assembled here from discrete
intents (mode + validated target + validated ports), never from a free-text
flag string the user typed. The UI exposes only the four modes below, so an
offensive scan is *unexpressible* — and even if a bad value arrived from
elsewhere, _enforce_rejects() refuses the assembled command (defense in depth).
"""

import ipaddress
import json
import re
import subprocess
import threading

# The four — and only — allowed modes. This tuple IS the safety boundary.
MODES = ("Discover", "Ports", "Services", "Identify")

# Curated common-port list used when the Ports field is left blank, and the
# fixed set Services/Identify probe. A small, familiar set — not an exhaustive
# sweep. Bounding Services/Identify to these ports (instead of nmap's default
# top-1000) is what makes them actually complete on a Pi over a /24 subnet
# rather than grinding for minutes.
COMMON_PORTS = ("21,22,23,25,53,80,110,111,135,139,143,161,443,445,"
                "993,995,1723,3306,3389,5900,8080")

# Per-host ceiling for the heavier Services/Identify scans so one unresponsive
# host on a subnet can't stall the whole sweep. Not an offensive/evasion knob.
HOST_TIMEOUT = "60s"

# Offensive/evasion flags the builder must never emit and must actively refuse.
# NSE (--script / -sC) is the top priority: it is nmap's primary offensive
# subsystem and must be provably unreachable. Matched case-sensitively so our
# own -sn / -sT / -sV / -sT are never confused with -sN / -sS / -sT variants.
_REJECT_EXACT = frozenset({
    "-sC",                       # NSE default scripts
    "-sS", "-sF", "-sX", "-sN",  # stealth / half-open / evasion scans
    "-A",                        # aggressive (bundles NSE + OS + traceroute)
    "-D", "-S",                  # decoys / source-IP spoofing
    "-f",                        # packet fragmentation (firewall evasion)
    "-T4", "-T5",                # evasion-tuned timing
})
_REJECT_PREFIX = ("--script",       # NSE (all forms: --script=..., --script-args)
                  "--spoof-mac",    # identity spoofing
                  "--mtu",          # fragmentation
                  "--data-length")  # padding (evasion)

MAX_LINES = 400          # cap retained output rows to protect Pi memory
SCAN_TIMEOUT = 120       # hard ceiling on a single scan


class ScanError(Exception):
    """Raised when a scan cannot be safely assembled or is refused."""


# --------------------------------------------------------------- validation --
def _valid_target(t):
    """Accept an IPv4/IPv6 address, a CIDR network, or a DNS hostname. Anything
    else — spaces, shell metacharacters, flags — is rejected before it can reach
    the builder."""
    if not t or len(t) > 255:
        return False
    try:
        if "/" in t:
            ipaddress.ip_network(t, strict=False)
            return True
        ipaddress.ip_address(t)
        return True
    except ValueError:
        pass
    # hostname: dot-separated labels of alnum + hyphen, no leading/trailing '-'
    label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})"
    return bool(re.fullmatch(rf"{label}(?:\.{label})*\.?", t))


def _valid_ports(p):
    """Ports field: digits, commas and hyphens only (e.g. 22,80,8000-8100)."""
    if not p or len(p) > 512:
        return False
    if not re.fullmatch(r"[0-9,\-]+", p):
        return False
    return any(c.isdigit() for c in p)


def _enforce_rejects(args):
    """Defense in depth: refuse the assembled command if any offensive flag
    slipped in from anywhere. The UI can't emit these; this catches the case
    where a value arrives from elsewhere."""
    for a in args:
        if a in _REJECT_EXACT or any(a.startswith(p) for p in _REJECT_PREFIX):
            raise ScanError(f"rejected offensive flag: {a}")


# ---------------------------------------------------------------- builder ----
def build_scan_command(mode, target, ports=None):
    """Assemble a scan command as an ARGUMENT ARRAY (never a shell string, so
    there is nothing to inject into). Maps each allowed mode to a fixed, safe
    flag set. Raises ScanError on a bad mode/target/ports or if any offensive
    flag would be present.

    Discover  -> -sn                                   (host discovery, no port scan)
    Ports     -> -sT -p <list>                         (TCP connect scan; curated ports if blank)
    Services  -> -sT -sV -p <common> --host-timeout    (version detection, bounded)
    Identify  -> -sT -O  -p <common> --host-timeout    (OS detection; needs root — see ScanJob)

    Services/Identify are pinned to COMMON_PORTS and a per-host timeout so they
    return within seconds per host instead of sweeping nmap's default top-1000.
    """
    if mode not in MODES:
        raise ScanError(f"unknown mode: {mode!r}")
    target = (target or "").strip()
    if not _valid_target(target):
        raise ScanError(f"invalid target: {target!r}")

    args = ["nmap"]
    if mode == "Discover":
        args += ["-sn"]
    elif mode == "Ports":
        p = (ports or "").strip() or COMMON_PORTS
        if not _valid_ports(p):
            raise ScanError(f"invalid ports: {ports!r}")
        args += ["-sT", "-p", p]
    elif mode == "Services":
        args += ["-sT", "-sV", "-p", COMMON_PORTS, "--host-timeout", HOST_TIMEOUT]
    elif mode == "Identify":
        args += ["-sT", "-O", "-p", COMMON_PORTS, "--host-timeout", HOST_TIMEOUT]

    # Default to -sT (connect scan) everywhere except Discover so unprivileged
    # operation works. The validated target goes last; it can never start with
    # '-' (validator rejects that) so it is never parsed as an option.
    args += [target]
    _enforce_rejects(args)
    return args


# ------------------------------------------------------------- privilege ----
def _is_root():
    import os
    return hasattr(os, "geteuid") and os.geteuid() == 0


# --------------------------------------------------------- output parsing ----
_REPORT_RE = re.compile(r"^Nmap scan report for (.+)$")
# "22/tcp   open  ssh     OpenSSH 8.4"  /  "443/tcp closed https"
_PORT_RE = re.compile(r"^(\d{1,5})/(tcp|udp)\s+(\S+)\s+(\S+)(?:\s+(.*))?$")


def _report_target(text):
    """'hostname (1.2.3.4)' -> ('1.2.3.4', 'hostname'); '1.2.3.4' -> ('1.2.3.4', '')."""
    m = re.match(r"^(.*?)\s+\(([^)]+)\)$", text)
    if m:
        return m.group(2), m.group(1)
    return text, ""


def parse_nmap(text):
    """Parse nmap normal output into structured host dicts:
        {ip, host, up, ports: [{port, proto, state, service, version}], info: [...]}
    Used by the tests and mirrors the incremental parser in ScanJob."""
    hosts = []
    cur = None
    for line in text.splitlines():
        m = _REPORT_RE.match(line)
        if m:
            ip, host = _report_target(m.group(1).strip())
            cur = {"ip": ip, "host": host, "up": True, "ports": [], "info": []}
            hosts.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("Host is up"):
            cur["up"] = True
        elif line.startswith("Host seems down") or "0 hosts up" in line:
            cur["up"] = False
        elif line.startswith("MAC Address:") or line.startswith("OS details:") \
                or line.startswith("Running:") or line.startswith("Aggressive OS"):
            cur["info"].append(line.strip())
        else:
            pm = _PORT_RE.match(line)
            if pm:
                cur["ports"].append({
                    "port": int(pm.group(1)), "proto": pm.group(2),
                    "state": pm.group(3), "service": pm.group(4),
                    "version": (pm.group(5) or "").strip(),
                })
    return hosts


# ------------------------------------------------------------- scan job ------
class ScanJob:
    """Run a scan and stream parsed rows as they arrive. Poll .done and read
    .lines / .host_count / .status each tick. .lines is a list of
    (indent, text, color_key) tuples the screen renders as monospace rows.

    Never auto-escalates: Identify (-O) needs root, so if we aren't root the
    job refuses up front with a clear message rather than failing silently.
    """

    def __init__(self, mode, target, ports=None):
        self.mode = mode
        self.lines = []
        self.hosts = []          # structured {ip, host, up, mac, ports, info}
        self.host_count = 0
        self.done = False
        self.error = None
        self.status = "Starting…"
        self._lock = threading.Lock()
        self._proc = None
        self._stopped = False
        self._cur = None
        self._curhost = None

        try:
            self.cmd = build_scan_command(mode, target, ports)
        except ScanError as e:
            # Neutral error surfaced in the output pane; nothing runs.
            self._refuse(str(e))
            return
        if mode == "Identify" and not _is_root():
            self._refuse("OS Identify needs root — start Scottina as root",
                         status="Identify needs root")
            return

        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    # ---- public control ----
    def stop(self):
        self._stopped = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    # ---- internals ----
    def _refuse(self, msg, status=None):
        self.error = msg
        self.status = status or f"Refused: {msg}"
        self._add(0, msg, "bad")
        self.done = True

    def _add(self, indent, text, color):
        with self._lock:
            self.lines.append((indent, text, color))
            if len(self.lines) > MAX_LINES:
                # drop oldest, note the truncation once
                del self.lines[0:len(self.lines) - MAX_LINES]

    def snapshot(self):
        """Thread-safe copy of the current lines for rendering."""
        with self._lock:
            return list(self.lines)

    def hosts_snapshot(self):
        """Thread-safe shallow copy of the structured host list for card
        rendering. Each entry is {ip, host, up, mac, ports, info}."""
        with self._lock:
            return [dict(h) for h in self.hosts]

    def _run(self):
        try:
            self._proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
        except FileNotFoundError:
            self._finish(error="nmap not installed", status="nmap not installed")
            return
        except OSError as e:
            self._finish(error=str(e), status=f"Scan error: {e}")
            return

        self.status = "Scanning…"
        try:
            for raw in self._proc.stdout:
                if self._stopped:
                    break
                self._consume(raw.rstrip("\n"))
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self.stop()

        if self._stopped:
            self._finish(status=f"Stopped · {self.host_count} host(s)")
        else:
            self._finish(status=f"Complete · {self.host_count} host(s)")

    def _finish(self, status=None, error=None):
        if error and not self.error:
            self.error = error
            self._add(0, error, "bad")
        if status:
            self.status = status
        self.done = True

    def _consume(self, line):
        """Incrementally parse one line of nmap normal output into a row."""
        m = _REPORT_RE.match(line)
        if m:
            ip, host = _report_target(m.group(1).strip())
            self.host_count += 1
            self._cur = ip
            self._curhost = {"ip": ip, "host": host, "up": True,
                             "mac": "", "vendor": "", "ports": [], "info": []}
            with self._lock:
                self.hosts.append(self._curhost)
            label = f"{ip}  {host}".strip()
            self._add(0, label, "accent")
            return
        h = self._curhost
        if line.startswith("Host is up"):
            self._add(1, "up", "ok")
        elif line.startswith("Host seems down"):
            if h is not None:
                h["up"] = False
            self._add(1, "down", "muted")
        elif line.startswith("MAC Address:"):
            mac = line.replace("MAC Address: ", "", 1).strip()
            if h is not None:
                h["mac"] = mac
                vm = re.search(r"\(([^)]+)\)\s*$", mac)   # "…FF (Raspberry Pi Foundation)"
                h["vendor"] = vm.group(1) if vm else ""
            self._add(1, "MAC " + mac, "muted")
        elif line.startswith(("OS details:", "Running:", "Device type:",
                              "OS CPE:", "Aggressive OS")):
            if h is not None:
                h["info"].append(line.strip())
            self._add(1, line.strip()[:60], "muted")
        elif (line.startswith(("No OS matches", "Too many fingerprints"))
              or "OSScan results may be unreliable" in line):
            # Identify tried and couldn't fingerprint — say so, so it's clearly
            # distinct from a plain port list rather than looking like nothing.
            if h is not None and "OS: no confident match" not in h["info"]:
                h["info"].append("OS: no confident match")
            self._add(1, "OS: no confident match", "warn")
        else:
            pm = _PORT_RE.match(line)
            if pm:
                port, proto, state = pm.group(1), pm.group(2), pm.group(3)
                svc = pm.group(4)
                ver = (pm.group(5) or "").strip()
                if h is not None:
                    h["ports"].append({"port": int(port), "proto": proto,
                                       "state": state, "service": svc,
                                       "version": ver})
                txt = f"{port}/{proto}  {state}  {svc}"
                if ver:
                    txt += f"  {ver}"
                color = "ok" if state == "open" else "muted"
                self._add(1, txt[:58], color)


# ------------------------------------------------------- convenience ----
def default_target():
    """Best-effort local subnet in CIDR form (e.g. 192.168.1.0/24) so the
    target field starts on something sensible. Empty string if unknown."""
    from . import system
    out = system.run(["ip", "-j", "addr"])
    try:
        for link in json.loads(out or "[]"):
            if link.get("ifname") == "lo":
                continue
            for a in link.get("addr_info", []):
                if a.get("family") == "inet":
                    ip, pfx = a.get("local"), a.get("prefixlen")
                    if ip and pfx:
                        net = ipaddress.ip_network(f"{ip}/{pfx}", strict=False)
                        return str(net)
    except (ValueError, KeyError):
        pass
    return ""
