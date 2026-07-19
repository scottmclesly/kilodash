"""Passive WiFi sniffer on the ALFA (or any second adapter).

Puts the *non-connected* WiFi adapter into monitor mode and channel-hops with
airodump-ng to list every AP and client it hears (SSID, channel, encryption
tag, signal). Passive only — no injection.

Keeping the internal WiFi up: wlan0 and the ALFA are separate radios, so there's
no hardware reason the uplink must drop. We (a) only ever touch the adapter that
is NOT carrying the default route, and (b) run a watchdog that reconnects the
uplink immediately if anything knocks it, so your connection stays put.
"""

import glob
import os
import signal
import subprocess
import threading
import time

from PIL import Image, ImageDraw

from .. import theme as T
from ..widgets import Button, spaced, state_glyph, status_square
from .base import Screen, HEADER_H

CSV_PREFIX = "/opt/kilodash/captures/.wifi_sniff"
ROW_H = 54


def _sh(*cmd, timeout=8):
    subprocess.run(cmd, capture_output=True, timeout=timeout)


def _wifi_ifaces():
    return sorted(os.path.basename(os.path.dirname(p))
                  for p in glob.glob("/sys/class/net/*/phy80211"))


def _default_iface():
    try:
        out = subprocess.run(["ip", "route"], capture_output=True, text=True,
                             timeout=4).stdout
        for line in out.splitlines():
            if line.startswith("default") and " dev " in line:
                return line.split(" dev ")[1].split()[0]
    except Exception:       # noqa: BLE001
        pass
    return None


def _connected(iface):
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "GENERAL.STATE", "device",
                              "show", iface], capture_output=True, text=True,
                             timeout=4).stdout
        return "100" in out          # 100 (connected)
    except Exception:       # noqa: BLE001
        return True


def _parse_csv(prefix):
    path = None
    for p in glob.glob(prefix + "*.csv"):
        path = p
    if not path:
        return [], []
    try:
        text = open(path, errors="ignore").read()
    except OSError:
        return [], []
    aps, stations, section = [], [], None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("BSSID,"):
            section = "ap"
            continue
        if s.startswith("Station MAC,"):
            section = "sta"
            continue
        c = [x.strip() for x in line.split(",")]
        if section == "ap" and len(c) >= 14:
            try:
                pwr = int(c[8])
            except ValueError:
                pwr = -100
            aps.append({"bssid": c[0], "chan": c[3], "enc": c[5] or "OPN",
                        "pwr": pwr, "ssid": c[13] or "<hidden>"})
        elif section == "sta" and len(c) >= 6:
            try:
                pwr = int(c[3])
            except ValueError:
                pwr = -100
            stations.append({"mac": c[0], "pwr": pwr, "bssid": c[5],
                             "probe": c[6] if len(c) > 6 else ""})
    aps.sort(key=lambda a: -a["pwr"])
    return aps, stations


class WifiSniffScreen(Screen):
    title = "WiFi Sniff"
    tile_id = "wifi-sniff"
    glyph = "wifisniff"
    tile_color_key = "warn"
    device_key = "wifisniff"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.proc = None
        self.mon_iface = None
        self.guard_iface = None
        self._guard_stop = False
        self._guard = None
        self.mon = False
        self.aps = []
        self.stations = []
        self.status = "Passive sniffer — Start to begin"
        self.toggle_btn = None

    # ------------------------------------------------------- uplink watchdog
    def _watch_loop(self, iface):
        while not self._guard_stop:
            if iface and not _connected(iface):
                _sh("nmcli", "device", "connect", iface, timeout=20)
            for _ in range(15):                 # ~3s, but stop-responsive
                if self._guard_stop:
                    return
                time.sleep(0.2)

    # ------------------------------------------------------------ monitor mode
    def _start(self):
        ifaces = _wifi_ifaces()
        self.guard_iface = _default_iface()
        self.mon_iface = next((i for i in ifaces if i != self.guard_iface), None)
        if not self.mon_iface:
            self.status = "No second WiFi adapter found"
            return
        for f in glob.glob(CSV_PREFIX + "*"):
            try:
                os.remove(f)
            except OSError:
                pass
        _sh("iw", "reg", "set", "US")           # keep channels valid for uplink
        # protect the uplink first, then set up monitor on the OTHER radio only
        self._guard_stop = False
        if self.guard_iface:
            self._guard = threading.Thread(target=self._watch_loop,
                                           args=(self.guard_iface,), daemon=True)
            self._guard.start()
        _sh("nmcli", "device", "set", self.mon_iface, "managed", "no")
        _sh("ip", "link", "set", self.mon_iface, "down")
        _sh("iw", "dev", self.mon_iface, "set", "type", "monitor")
        _sh("ip", "link", "set", self.mon_iface, "up")
        self.mon = True
        try:
            self.proc = subprocess.Popen(
                ["airodump-ng", "--write", CSV_PREFIX, "--output-format", "csv",
                 "--write-interval", "1", self.mon_iface],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status = f"Sniffing on {self.mon_iface}…"
        except FileNotFoundError:
            self.status = "airodump-ng not found"
            self._stop()

    def _stop(self):
        if self.proc:
            try:
                self.proc.send_signal(signal.SIGINT)
                self.proc.wait(timeout=3)
            except Exception:       # noqa: BLE001
                self.proc.kill()
            self.proc = None
        if self.mon and self.mon_iface:
            _sh("ip", "link", "set", self.mon_iface, "down")
            _sh("iw", "dev", self.mon_iface, "set", "type", "managed")
            _sh("ip", "link", "set", self.mon_iface, "up")
            _sh("nmcli", "device", "set", self.mon_iface, "managed", "yes")
            self.mon = False
        self._guard_stop = True                 # let the uplink watchdog exit
        self.status = "Stopped"

    @property
    def running(self):
        return self.proc is not None

    def on_leave(self):
        if self.running:
            self._stop()

    def tick(self):
        if self.running:
            self.aps, self.stations = _parse_csv(CSV_PREFIX)
            uplink = "uplink OK" if (not self.guard_iface or
                                     _connected(self.guard_iface)) else "uplink…"
            self.status = f"{len(self.aps)} APs · {len(self.stations)} sta · {uplink}"
            return True
        return False

    def content_area(self):
        return (0, HEADER_H + 46, self.app.w, self.app.h - HEADER_H - 46)


    def model_rows(self):
        """Capture state. The uplink verdict is already folded into
        self.status — never call _connected(), which shells out."""
        return [
            {"label": "CAPTURE", "value": "RUNNING" if self.running else "STOPPED",
             "state": "ok" if self.running else None},
            {"label": "MONITOR", "value": "ON" if self.mon else "OFF",
             "state": "ok" if self.mon else "caution"},
            {"label": "IFACE", "value": str(self.mon_iface or "—"), "state": None},
            {"label": "UPLINK GUARD", "value": str(self.guard_iface or "—"),
             "state": None},
            {"label": "APS", "value": str(len(self.aps or [])), "state": None},
            {"label": "STATIONS", "value": str(len(self.stations or [])),
             "state": None},
            {"label": "STATUS", "value": str(self.status or "—"), "state": None},
        ]


    def model_buttons(self):
        return [{"id": "capture",
                 "label": "STOP" if self.running else "START",
                 "enabled": True, "confirm": False}]

    def handle_button(self, bid):
        if bid == "capture":
            self._stop() if self.running else self._start()
            return True
        return False

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H + 46
        rows = [("ap", a) for a in self.aps] + [("sta", s) for s in self.stations]
        self.content_h = max(len(rows) * ROW_H + 8, h - top)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, (kind, r) in enumerate(rows):
            y = i * ROW_H
            sd.rectangle((12, y, w - 12, y + ROW_H - 6), fill=th.card,
                         outline=th.card_hi, width=1)
            pwr = r["pwr"]
            pcol = th.ok if pwr > -60 else th.warn if pwr > -75 else th.muted
            # square signal glyph: lit = strong, hollow = faint (never red)
            mode = "lit" if pwr > -60 else "hollow"
            status_square(sd, (20, y + 8, 32, y + 20), mode, pcol)
            sd.text((w - 62, y + 8), f"{pwr}",
                    font=T.font(15, bold=True, mono=True), fill=pcol)
            sd.text((w - 62, y + 28), spaced("DBM"),
                    font=T.font(9, bold=True, mono=True), fill=th.muted)
            if kind == "ap":
                sd.text((42, y + 6), r["ssid"][:20], font=T.font(15, bold=True),
                        fill=th.fg)
                sub = f"AP · CH{r['chan']} · {r['enc'].split(' ')[0].upper()}"
                sd.text((42, y + 30), sub, font=T.font(11, bold=True, mono=True),
                        fill=th.accent)
            else:
                sd.text((42, y + 6), r["mac"], font=T.font(14, mono=True),
                        fill=th.fg)
                probe = (r["probe"] or "").strip().strip(",")
                sub = (f"PROBE {probe[:18]}" if probe
                       else f"STA → {r['bssid'][:17]}")
                sd.text((42, y + 30), sub.upper(), font=T.font(11, mono=True),
                        fill=th.muted)
        self.paste_list(top, h - top, surf)

        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        bar_y = HEADER_H + 4
        # capture-state banner: hard-edged, per-state glyph. Stopping a passive
        # capture is a stand-down (muted), not a fault; only a missing adapter
        # or missing tool is a fault (red).
        running = self.running
        s_low = self.status.lower()
        fault = not running and ("not found" in s_low or "no second" in s_low)
        if running:
            col, glyph = th.ok, "up"
        elif fault:
            col, glyph = th.bad, "fault"
        else:
            col, glyph = th.muted, "standby"
        bx1 = w - 130
        d.rectangle((12, bar_y, bx1, bar_y + 38), fill=th.card, outline=col,
                    width=2)
        state_glyph(d, glyph, 30, bar_y + 19, 9, col)
        d.text((46, bar_y + 12), self.status[:20].upper(),
               font=T.font(11, bold=True, mono=True), fill=col)
        self.toggle_btn = Button((w - 122, bar_y, w - 12, bar_y + 38),
                                 "STOP" if running else "START",
                                 kind="primary",
                                 color=th.warn if running else None,
                                 font_size=17)
        self.toggle_btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.toggle_btn and self.toggle_btn.hit(x, y):
            self._stop() if self.running else self._start()
            return True
        return False
