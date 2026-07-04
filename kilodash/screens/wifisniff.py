"""ALFA (wlan1) passive WiFi sniffer.

Puts the ALFA into monitor mode (leaving the Pi's built-in wlan0 alone for
connectivity), channel-hops with airodump-ng, and lists every transmitting AP
and client it hears with encryption/protocol tags and signal. Passive only —
no injection here.
"""

import glob
import os
import signal
import subprocess
import time

from PIL import Image, ImageDraw

from .. import theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

MON_IFACE = "wlan1"
CSV_PREFIX = "/opt/kilodash/captures/.wifi_sniff"
ROW_H = 54


def _sh(*cmd):
    subprocess.run(cmd, capture_output=True, timeout=8)


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
    aps, stations = [], []
    section = None
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
        cols = [c.strip() for c in line.split(",")]
        if section == "ap" and len(cols) >= 14:
            try:
                pwr = int(cols[8])
            except ValueError:
                pwr = -100
            aps.append({"bssid": cols[0], "chan": cols[3], "enc": cols[5] or "OPN",
                        "pwr": pwr, "ssid": cols[13] or "<hidden>"})
        elif section == "sta" and len(cols) >= 6:
            try:
                pwr = int(cols[3])
            except ValueError:
                pwr = -100
            stations.append({"mac": cols[0], "pwr": pwr, "bssid": cols[5],
                             "probe": cols[6] if len(cols) > 6 else ""})
    aps.sort(key=lambda a: -a["pwr"])
    return aps, stations


class WifiSniffScreen(Screen):
    title = "WiFi Sniff"
    tile_color_key = "warn"
    device_key = "wifisniff"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.proc = None
        self.mon = False
        self.aps = []
        self.stations = []
        self.status = "Passive sniffer (ALFA / wlan1)"
        self.toggle_btn = None

    # ------------------------------------------------------------ monitor mode
    def _start(self):
        for f in glob.glob(CSV_PREFIX + "*"):
            try:
                os.remove(f)
            except OSError:
                pass
        _sh("nmcli", "device", "set", MON_IFACE, "managed", "no")
        _sh("ip", "link", "set", MON_IFACE, "down")
        _sh("iw", "dev", MON_IFACE, "set", "type", "monitor")
        _sh("ip", "link", "set", MON_IFACE, "up")
        self.mon = True
        try:
            self.proc = subprocess.Popen(
                ["airodump-ng", "--write", CSV_PREFIX, "--output-format", "csv",
                 "--write-interval", "1", MON_IFACE],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status = "Sniffing… (channel hopping)"
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
        if self.mon:
            _sh("ip", "link", "set", MON_IFACE, "down")
            _sh("iw", "dev", MON_IFACE, "set", "type", "managed")
            _sh("ip", "link", "set", MON_IFACE, "up")
            _sh("nmcli", "device", "set", MON_IFACE, "managed", "yes")
            self.mon = False
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
            self.status = f"{len(self.aps)} APs · {len(self.stations)} clients"
            return True
        return False

    def content_area(self):
        return (0, HEADER_H + 46, self.app.w, self.app.h - HEADER_H - 46)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H + 46

        # scrollable list of APs then clients
        rows = [("ap", a) for a in self.aps] + [("sta", s) for s in self.stations]
        self.content_h = max(len(rows) * ROW_H + 8, h - top)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, (kind, r) in enumerate(rows):
            y = i * ROW_H
            rrect(sd, (12, y, w - 12, y + ROW_H - 6), 9, fill=th.card)
            pwr = r["pwr"]
            pcol = th.ok if pwr > -60 else th.warn if pwr > -75 else th.muted
            sd.text((w - 60, y + 8), f"{pwr}", font=T.font(15, bold=True, mono=True),
                    fill=pcol)
            sd.text((w - 60, y + 28), "dBm", font=T.font(10), fill=th.muted)
            if kind == "ap":
                sd.text((22, y + 6), r["ssid"][:22], font=T.font(16, bold=True),
                        fill=th.fg)
                tag = f"AP · ch{r['chan']} · {r['enc'].split(' ')[0]}"
                sd.text((22, y + 30), tag, font=T.font(12), fill=th.accent)
            else:
                sd.text((22, y + 6), r["mac"], font=T.font(15, mono=True), fill=th.fg)
                probe = (r["probe"] or "").strip().strip(",")
                sub = f"client → {r['bssid'][:17]}"
                if probe:
                    sub = f"probe: {probe[:20]}"
                sd.text((22, y + 30), sub, font=T.font(12), fill=th.muted)
        self.paste_list(top, h - top, surf)

        # header control bar
        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        bar_y = HEADER_H + 4
        rrect(d, (12, bar_y, w - 130, bar_y + 38), 8, fill=th.card)
        d.text((22, bar_y + 11), self.status[:24], font=T.font(13), fill=th.muted)
        self.toggle_btn = Button((w - 122, bar_y, w - 12, bar_y + 38),
                                 "Stop" if self.running else "Start",
                                 kind="danger" if self.running else "primary",
                                 font_size=17)
        self.toggle_btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.toggle_btn and self.toggle_btn.hit(x, y):
            self._stop() if self.running else self._start()
            return True
        return False
