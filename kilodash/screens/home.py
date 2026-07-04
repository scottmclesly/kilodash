"""Launcher: header shows the live IP (rotating WiFi/LAN every 3s) + clock,
below it a grid of big finger-tiles, one per screen. All discrete taps.
"""

import time

from .. import system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

IFACE_LABELS = {"wlan0": "WiFi", "eth0": "LAN"}
IFACE_ORDER = ["wlan0", "eth0"]
ROTATE_SEC = 3


class LauncherScreen(Screen):
    title = "kilodash"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0          # keep clock + IP rotation fresh
        self.ips = []                     # [(label, ip), ...]
        self.wifi = False
        self.tiles = []                   # (box, screen)
        self.wifi_btn = None
        self.tick()

    def tick(self):
        found = {}
        for it in system.get_interfaces():
            if it["ip"] and it["ip"] != "--":
                found[it["name"]] = it["ip"]
        ordered = [n for n in IFACE_ORDER if n in found]
        ordered += [n for n in found if n not in IFACE_ORDER]
        self.ips = [(IFACE_LABELS.get(n, n), found[n]) for n in ordered]
        self.wifi = system.wifi_enabled()
        return True

    # custom header: rotating IP on the left, clock on the right
    def _draw_header(self, d, th):
        w = self.app.w
        d.rectangle((0, 0, w, HEADER_H), fill=th.card)
        if self.ips:
            label, ip = self.ips[int(time.time() / ROTATE_SEC) % len(self.ips)]
            d.text((14, 5), label, font=T.font(12, bold=True), fill=th.muted)
            d.text((14, 19), ip, font=T.font(20, bold=True, mono=True),
                   fill=th.accent)
            if len(self.ips) > 1:                       # rotation indicator
                dotx = w - 60
                for i in range(len(self.ips)):
                    cur = i == int(time.time() / ROTATE_SEC) % len(self.ips)
                    d.ellipse((dotx + i * 10, 6, dotx + i * 10 + 5, 11),
                              fill=th.accent if cur else th.card_hi)
        else:
            d.text((14, 12), "no network", font=T.font(18, bold=True),
                   fill=th.muted)
        if self.app.config["show_clock"]:
            clk = time.strftime("%H:%M")
            f = T.font(18, bold=True)
            tw = d.textlength(clk, font=f)
            d.text((w - tw - 14, 15), clk, font=f, fill=th.fg)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h

        # tile grid (starts right under the header now the status text is gone)
        targets = self.app.screens[1:]
        cols = 2
        margin, gap = 12, 12
        tw = (w - margin * 2 - gap) / cols
        tile_h = 100
        top = HEADER_H + 12
        self.tiles = []
        for i, scr in enumerate(targets):
            r, c = divmod(i, cols)
            x0 = margin + c * (tw + gap)
            y0 = top + r * (tile_h + gap)
            box = (x0, y0, x0 + tw, y0 + tile_h)
            if getattr(th, "sterile", False):
                color = th.muted          # no decorative category colours
            else:
                color = getattr(th, getattr(scr, "tile_color_key", "accent"))
            rrect(d, box, 14, fill=th.card)
            cx = (x0 + x0 + tw) / 2
            d.ellipse((cx - 12, y0 + 22, cx + 12, y0 + 46), fill=color)
            label = scr.title
            lf = T.font(20, bold=True)
            lw = d.textlength(label, font=lf)
            d.text((cx - lw / 2, y0 + 58), label, font=lf, fill=th.fg)
            self.tiles.append((box, scr))

        # wifi quick toggle, full width at the bottom
        self.wifi_btn = Button((12, h - 62, w - 12, h - 12),
                               f"Wi-Fi  {'ON' if self.wifi else 'OFF'}",
                               kind="primary" if self.wifi else "danger",
                               font_size=22)
        self.wifi_btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.wifi_btn and self.wifi_btn.hit(x, y):
            system.set_wifi(not self.wifi)
            self.wifi = system.wifi_enabled()
            self.app.toast("Wi-Fi " + ("enabled" if self.wifi else "disabled"))
            return True
        for box, scr in self.tiles:
            x0, y0, x1, y1 = box
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.app.open_screen(scr)
                return True
        return False
