"""System/network data helpers. Anything slow (Wi-Fi or LAN scans) is run
through Task so the UI thread never blocks.
"""

import json
import socket
import subprocess
import threading


def run(cmd, timeout=8):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


class Task:
    """Run a function in a background thread; poll .done / .result."""

    def __init__(self, fn, *args):
        self.result = None
        self.done = False
        self.error = None
        self._t = threading.Thread(target=self._run, args=(fn, args), daemon=True)
        self._t.start()

    def _run(self, fn, args):
        try:
            self.result = fn(*args)
        except Exception as e:      # noqa: BLE001
            self.error = e
        finally:
            self.done = True


# --------------------------------------------------------------- interfaces --
def get_interfaces():
    out = run(["ip", "-j", "addr"])
    items = []
    try:
        for link in json.loads(out or "[]"):
            name = link.get("ifname", "?")
            if name == "lo":
                continue
            ip4 = next((a.get("local", "") for a in link.get("addr_info", [])
                        if a.get("family") == "inet"), "")
            items.append({"name": name, "ip": ip4 or "--",
                          "state": link.get("operstate", "")})
    except ValueError:
        pass
    return items


def primary_iface():
    """The interface carrying the default route, if any."""
    out = run(["ip", "-j", "route"])
    try:
        for r in json.loads(out or "[]"):
            if r.get("dst") == "default":
                return r.get("dev")
    except ValueError:
        pass
    return None


# ---------------------------------------------------------------------- wifi --
def wifi_enabled():
    return run(["nmcli", "radio", "wifi"]).lower().startswith("enabled")


def set_wifi(on):
    subprocess.run(["nmcli", "radio", "wifi", "on" if on else "off"])


def scan_wifi():
    run(["nmcli", "device", "wifi", "rescan"], timeout=12)
    out = run(["nmcli", "-t", "-f",
               "IN-USE,SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list"],
              timeout=12)
    seen, nets = set(), []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        in_use = parts[0].strip() == "*"
        ssid = parts[1].strip()
        signal = parts[2].strip()
        security = parts[3].strip() or "open"
        chan = parts[4].strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        nets.append({"ssid": ssid, "signal": sig, "security": security,
                     "in_use": in_use, "chan": chan})
    nets.sort(key=lambda n: (not n["in_use"], -n["signal"]))
    return nets


def known_ssids():
    out = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    names = set()
    for line in out.splitlines():
        p = line.split(":")
        if len(p) >= 2 and "wireless" in p[1]:
            names.add(p[0])
    return names


def connect_wifi(ssid, password=None):
    if password:
        cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", password]
    else:
        cmd = ["nmcli", "connection", "up", ssid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        ok = r.returncode == 0
        msg = (r.stdout + r.stderr).strip().splitlines()
        return ok, (msg[-1] if msg else ("connected" if ok else "failed"))
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except Exception as e:              # noqa: BLE001
        return False, str(e)


# ----------------------------------------------------------------- lan scan --
def _rev_dns(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        return ""


def lan_scan(iface=None):
    """arp-scan the local subnet; enrich with reverse DNS. Needs root."""
    iface = iface or primary_iface() or "wlan0"
    out = run(["arp-scan", "--interface", iface, "--localnet",
               "--retry=2", "--timeout=200"], timeout=30)
    hosts, seen = [], set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].count(".") == 3:
            ip, mac = parts[0].strip(), parts[1].strip()
            if ip in seen:
                continue
            seen.add(ip)
            vendor = parts[2].strip() if len(parts) > 2 else ""
            hosts.append({"ip": ip, "mac": mac, "vendor": vendor,
                          "host": _rev_dns(ip)})
    hosts.sort(key=lambda h: tuple(int(o) for o in h["ip"].split(".")))
    return {"iface": iface, "hosts": hosts}


# ------------------------------------------------------------------- health --
def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def health():
    d = {}
    # temperature
    t = run(["vcgencmd", "measure_temp"])          # temp=59.8'C
    d["temp_c"] = t.split("=")[-1].replace("'C", "").strip() if "=" in t else "?"
    # throttling
    thr = run(["vcgencmd", "get_throttled"])       # throttled=0x0
    code = thr.split("=")[-1].strip() if "=" in thr else "0x0"
    d["throttled_code"] = code
    d["throttled"] = code not in ("0x0", "")
    # cpu load / freq
    d["loadavg"] = _read("/proc/loadavg", "").split(" ")[:3]
    khz = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "0")
    try:
        d["cpu_mhz"] = int(khz) // 1000
    except ValueError:
        d["cpu_mhz"] = 0
    # memory
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.strip().split(" ")[0]) if v.strip() else 0
    total = mem.get("MemTotal", 0)
    avail = mem.get("MemAvailable", 0)
    d["mem_total_mb"] = total // 1024
    d["mem_used_mb"] = (total - avail) // 1024
    d["mem_pct"] = round((total - avail) / total * 100) if total else 0
    # disk (root)
    df = run(["df", "-BM", "--output=used,size,pcent", "/"])
    lines = df.splitlines()
    if len(lines) >= 2:
        u, s, p = lines[1].split()
        d["disk_used"] = u.rstrip("M")
        d["disk_total"] = s.rstrip("M")
        d["disk_pct"] = int(p.rstrip("%"))
    # uptime
    up = float(_read("/proc/uptime", "0").split(" ")[0] or 0)
    h, rem = divmod(int(up), 3600)
    m = rem // 60
    d["uptime"] = f"{h}h {m:02d}m"
    # wifi signal
    sig = run(["nmcli", "-t", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi"])
    d["wifi_ssid"], d["wifi_signal"] = "", 0
    for line in sig.splitlines():
        if line.startswith("*"):
            p = line.split(":")
            d["wifi_signal"] = int(p[1]) if len(p) > 1 and p[1].isdigit() else 0
            d["wifi_ssid"] = p[2] if len(p) > 2 else ""
            break
    return d
