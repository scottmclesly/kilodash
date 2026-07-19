"""Runtime glue: arm gate + executor + BLE link, hosted inside the kilodash
app process (the only module in microkvm/ allowed to import kilodash).

The tile (kilodash/screens/microkvm.py) renders this object's state and holds
no logic of its own; the app's main loop services `take_tile_request()` so
the `tile` verb switches screens on the UI thread, never from the BLE thread.
"""

import threading
import time

from . import CONFIG_DEFAULTS
from .armgate import ArmGate
from .executor import Executor
from .link import MeshLink


class _HostInfo:
    """Metric provider for status/health/snap, backed by kilodash.system."""

    SERVICES = ("kilodash", "signalk", "nodered", "kismet")

    def __init__(self):
        from kilodash import system
        self._system = system
        self._cache = (0.0, None)

    def _health(self):
        # health() shells out a fair bit; one command frame may read it
        # several times. Cache briefly.
        ts, data = self._cache
        if data is None or time.monotonic() - ts > 5:
            data = self._system.health()
            self._cache = (time.monotonic(), data)
        return data

    def metric(self, name):
        h = self._health()
        return {
            "temp": h.get("temp_c", "?"),
            "mem": str(h.get("mem_pct", "?")),
            "disk": str(h.get("disk_pct", "?")),
            "load": " ".join(h.get("loadavg", []) or ["?"]),
            "uptime": h.get("uptime", "?").replace(" ", ""),
            "wifi": f"{h.get('wifi_ssid') or '-'}/{h.get('wifi_signal', 0)}",
        }.get(name, "?")

    def services(self):
        out = {}
        for name in self.SERVICES:
            state = self._system.run(["systemctl", "is-active",
                                      f"{name}.service"], timeout=5)
            out[name] = "up" if state == "active" else "down"
        return out


def legacy_tile_slug(title):
    """Pre-`tile_id` command token ('LAN Scan' -> 'lanscan').

    RETIRED as an identity source — `Screen.tile_id` (WEB-PROTOCOL.md §4.1) is
    now the only one. Kept solely to generate the back-compat alias table
    below, because these strings are sitting in canned messages on paired
    handsets. Do not call it for anything else."""
    return "".join(c for c in title.lower() if c.isalnum())


def build_tile_aliases(screens):
    """Legacy alnum slug -> canonical tile_id, for tokens that changed.

    `tile nmea2k` was typed into a phone's canned messages before `tile_id`
    existed; off-grid is the worst possible place to discover it now answers
    `reject bad-arg`. The executor normalises these at ingress, so the grammar
    domain and `help tile` stay canonical-only — the alias is accepted, never
    advertised. Generated, not hand-listed, so a new screen cannot forget one."""
    aliases = {}
    for s in screens or ():
        tid = getattr(s, "tile_id", None)
        if not tid:
            continue
        legacy = legacy_tile_slug(s.title)
        if legacy and legacy != tid:
            aliases[legacy] = tid
    return aliases


class Runtime:
    """One per app. Build with the app's screen list, then start()."""

    def __init__(self, cfg_block, screens=None):
        cfg = dict(CONFIG_DEFAULTS)
        cfg.update(cfg_block or {})
        self.enabled = bool(cfg["enabled"])
        self._screens = {s.tile_id: s for s in (screens or [])
                         if getattr(s, "tile_id", None)}
        if screens:
            # the launcher is always addressable as plain `tile home`
            # (LauncherScreen.tile_id is "home", so this is belt-and-braces
            # for a host app that ships a launcher without one)
            self._screens.setdefault("home", screens[0])
        self._tile_aliases = build_tile_aliases(screens)
        self._tile_request = None           # slug, picked up by the UI loop
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active_tile = lambda: "-"

        self.gate = ArmGate(home_ssid=cfg["home_ssid"],
                            home_host=cfg["home_host"],
                            need=cfg["debounce_checks"])
        self.check_interval = max(5, int(cfg["check_interval_sec"]))
        try:
            info = _HostInfo()
        except Exception:                    # headless import trouble
            info = None
        self.executor = Executor(
            armed_fn=self.gate.state,
            info=info,
            request_tile_fn=self._request_tile,
            active_tile_fn=lambda: self._active_tile(),
            tiles=set(self._screens) or None,
            tile_aliases=self._tile_aliases)
        self.link = MeshLink(
            self.executor,
            ble_address=cfg["ble_address"],
            channel_name=cfg["command_channel"],
            allowed_nodes=cfg["allowed_nodes"])
        self.executor._link = self.link.link_info

    # ------------------------------------------------------------ app hooks --
    def wire_ui(self, active_tile_fn):
        self._active_tile = active_tile_fn

    def _request_tile(self, slug):
        if slug not in self._screens:
            return False
        with self._lock:
            self._tile_request = slug
        return True

    def take_tile_request(self):
        """UI loop: screen object to switch to, or None. Thread-safe."""
        with self._lock:
            slug, self._tile_request = self._tile_request, None
        return self._screens.get(slug) if slug else None

    # ------------------------------------------------------------ lifecycle --
    def start(self):
        if not self.enabled:
            return self
        threading.Thread(target=self._gate_loop, daemon=True,
                         name="microkvm-armgate").start()
        self.link.start()
        return self

    def stop(self):
        self._stop.set()
        self.link.stop()

    def _gate_loop(self):
        while not self._stop.wait(self.check_interval):
            try:
                self.gate.poll()
            except Exception:                # a broken probe must not kill it
                pass
