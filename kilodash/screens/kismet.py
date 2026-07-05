"""Kismet launch panel.

Opening the screen launches the Kismet server (`kismet --no-ncurses`), confirms
its web UI on :2501, and shows the URL to open the full interface from a laptop.

Custom panel:
- **Sniff on/off** — adds/removes the ALFA (the non-uplink Wi-Fi adapter) as a
  Kismet monitor-mode datasource. Passive listening only. A watchdog keeps the
  Pi's own uplink (`wlan0`) connected the whole time, exactly like the WiFi Sniff
  screen — the two radios are independent, so both run at once.
- **Live peers** — recent devices Kismet has heard, colour-coded by type
  (AP / client / Bluetooth). Read best-effort from Kismet's REST API using the
  credentials Kismet writes to `~/.kismet/kismet_httpd.conf` on first run; until
  you complete Kismet's one-time web login the list shows a hint instead.
"""

import os
import subprocess
import threading
import time

from .. import theme as T, webapp
from ..widgets import Button, rrect
from .webapp_base import WebAppScreen
from .wifisniff import _wifi_ifaces, _default_iface, _connected

ROW_H = 40
CONF = "/opt/kilodash/captures"


def _kismet_auth():
    """Read (user, pass) from Kismet's httpd conf, or None if not set up yet."""
    for path in ("/root/.kismet/kismet_httpd.conf",
                 os.path.expanduser("~/.kismet/kismet_httpd.conf")):
        try:
            u = p = None
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("httpd_username="):
                        u = line.split("=", 1)[1].strip()
                    elif line.startswith("httpd_password="):
                        p = line.split("=", 1)[1].strip()
            if u and p:
                return (u, p)
        except OSError:
            continue
    return None


def _peer_color(th, dtype):
    t = (dtype or "").lower()
    if "ap" in t:
        return th.accent
    if "client" in t or "device" in t or "bridge" in t:
        return th.ok
    if "bt" in t or "br/edr" in t or "btle" in t:
        return th.warn
    return th.muted


class KismetScreen(WebAppScreen):
    title = "Kismet"
    glyph = "kismet"
    tile_color_key = "warn"
    app_name = "Kismet"
    port = 2501
    url_path = "/"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.sniffing = False
        self.peers = []
        self.peer_note = "Enable sniffing to hear devices"
        self._auth = None
        self._last_rest = -1e9
        # uplink watchdog
        self._guard = None
        self._guard_iface = None
        self._guard_stop = True

    # ---- launch config ----
    def _monitor_iface(self):
        up = _default_iface()
        return next((i for i in _wifi_ifaces() if i != up), None)

    def build_start_cmd(self):
        cmd = ["kismet", "--no-ncurses", "--log-prefix", CONF]
        if self.sniffing:
            iface = self._monitor_iface()
            if iface:
                cmd += ["-c", f"{iface}:name=kilodash"]
        return cmd

    # ---- uplink watchdog (protect wlan0 while the ALFA sniffs) ----
    def _watch(self, iface):
        while not self._guard_stop:
            if iface and not _connected(iface):
                subprocess.run(["nmcli", "device", "connect", iface],
                               capture_output=True, timeout=20)
            for _ in range(15):
                if self._guard_stop:
                    return
                time.sleep(0.2)

    def _start_guard(self):
        self._guard_iface = _default_iface()
        self._guard_stop = False
        if self._guard_iface:
            self._guard = threading.Thread(target=self._watch,
                                           args=(self._guard_iface,), daemon=True)
            self._guard.start()

    def _stop_guard(self):
        self._guard_stop = True

    def _toggle_sniff(self):
        iface = self._monitor_iface()
        if not self.sniffing and not iface:
            self.app.toast("No spare Wi-Fi adapter (ALFA) for monitor mode")
            return
        self.sniffing = not self.sniffing
        self.web.stop()
        self._stop_guard()
        self.web.launch(self.build_start_cmd())
        if self.sniffing:
            self._start_guard()
            self.peer_note = "Listening…"
        else:
            self.peers = []
            self.peer_note = "Sniffing off"

    # ---- REST feedback ----
    def poll_app(self):
        if self.web.state != webapp.UP or not self.sniffing:
            return False
        now = time.monotonic()
        if now - self._last_rest < 2.5:
            return False
        self._last_rest = now
        if self._auth is None:
            self._auth = _kismet_auth() or False
        if not self._auth:
            self.peer_note = "Finish Kismet web login to list peers here"
            return True
        data = webapp.http_json(
            f"http://127.0.0.1:2501/devices/views/all/last-time/-30/devices.json",
            timeout=2.0, auth=self._auth)
        if not isinstance(data, list):
            self.peer_note = "Peers visible in web UI"
            return True
        peers = []
        for dev in data:
            if not isinstance(dev, dict):
                continue
            name = (dev.get("kismet.device.base.commonname")
                    or dev.get("kismet.device.base.macaddr") or "?")
            dtype = dev.get("kismet.device.base.type", "")
            sig = -100
            sd = dev.get("kismet.device.base.signal")
            if isinstance(sd, dict):
                sig = sd.get("kismet.common.signal.last_signal", -100)
            peers.append({"name": str(name)[:22], "type": dtype,
                          "sig": sig})
        peers.sort(key=lambda p: -p["sig"])
        self.peers = peers
        self.peer_note = f"{len(peers)} devices (last 30s)"
        return True

    # ---- rendering ----
    def draw_app(self, d, th, top):
        w = self.app.w
        # sniff toggle
        running = self.web.state == webapp.UP
        b = Button((12, top, w - 12, top + 42),
                   "Disable sniffing" if self.sniffing else "Enable sniffing",
                   kind="danger" if self.sniffing else "primary", font_size=16)
        b.enabled = running
        b.draw(d, th)
        self._btns["sniff"] = b
        top += 48

        d.text((14, top), "PEERS", font=T.font(11, bold=True), fill=th.muted)
        d.text((70, top), self.peer_note, font=T.font(11), fill=th.muted)
        top += 18

        avail = self.app.h - top - 8
        maxrows = max(0, int(avail // ROW_H))
        for i, p in enumerate(self.peers[:maxrows]):
            y = top + i * ROW_H
            rrect(d, (12, y, w - 12, y + ROW_H - 6), 8, fill=th.card)
            d.ellipse((20, y + ROW_H / 2 - 9, 34, y + ROW_H / 2 + 3),
                      fill=_peer_color(th, p["type"]))
            d.text((44, y + 5), p["name"], font=T.font(14, bold=True), fill=th.fg)
            d.text((44, y + 22), (p["type"] or "?")[:20], font=T.font(11),
                   fill=th.muted)
            d.text((w - 56, y + 9), f"{p['sig']}", font=T.font(13, mono=True),
                   fill=th.muted)
        extra = len(self.peers) - maxrows
        if extra > 0:
            d.text((16, top + maxrows * ROW_H), f"+{extra} more — see web UI",
                   font=T.font(11), fill=th.muted)

    def handle_app_tap(self, x, y):
        b = self._btns.get("sniff")
        if b and b.hit(x, y):
            self._toggle_sniff()
            return True
        return False
