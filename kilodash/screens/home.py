"""Launcher: header shows the live IP (rotating WiFi/LAN every 3s) + clock,
below it an adaptive grid of tiles. Fixed tiles are always shown; device tiles
appear only while their dongle is plugged in (see devices.py). All taps.
"""

import time

from .. import system, theme as T
from ..widgets import rrect
from .base import Screen, HEADER_H

IFACE_LABELS = {"wlan0": "WiFi", "eth0": "LAN"}
IFACE_ORDER = ["wlan0", "eth0"]
ROTATE_SEC = 3


class LauncherScreen(Screen):
    title = "kilodash"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.ips = []
        self.tiles = []                   # (box, screen)
        self.tick()

    def tick(self):
        self.app.devices.refresh()        # drives hotplug tile appearance
        found = {}
        for it in system.get_interfaces():
            if it["ip"] and it["ip"] != "--":
                found[it["name"]] = it["ip"]
        ordered = [n for n in IFACE_ORDER if n in found]
        ordered += [n for n in found if n not in IFACE_ORDER]
        self.ips = [(IFACE_LABELS.get(n, n), found[n]) for n in ordered]
        return True

    def _draw_header(self, d, th):
        w = self.app.w
        d.rectangle((0, 0, w, HEADER_H), fill=th.card)
        if self.ips:
            label, ip = self.ips[int(time.time() / ROTATE_SEC) % len(self.ips)]
            d.text((14, 5), label, font=T.font(12, bold=True), fill=th.muted)
            d.text((14, 19), ip, font=T.font(20, bold=True, mono=True),
                   fill=th.accent)
            if len(self.ips) > 1:
                dotx = w - 60
                cur = int(time.time() / ROTATE_SEC) % len(self.ips)
                for i in range(len(self.ips)):
                    d.ellipse((dotx + i * 10, 6, dotx + i * 10 + 5, 11),
                              fill=th.accent if i == cur else th.card_hi)
        else:
            d.text((14, 12), "no network", font=T.font(18, bold=True), fill=th.muted)
        if self.app.config["show_clock"]:
            clk = time.strftime("%H:%M")
            f = T.font(18, bold=True)
            tw = d.textlength(clk, font=f)
            d.text((w - tw - 14, 15), clk, font=f, fill=th.fg)

    def _visible(self):
        return [s for s in self.app.screens[1:]
                if (s.device_key is None or self.app.devices.has(s.device_key))
                and s.available()]

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        tiles = self._visible()
        self.tiles = []
        cols, margin, gap = 2, 12, 10
        top = HEADER_H + 10
        n = max(1, len(tiles))
        rows = (n + 1) // 2
        avail = h - top - 10
        tile_h = min(104, (avail - (rows - 1) * gap) / rows)
        tw = (w - margin * 2 - gap) / cols

        for i, scr in enumerate(tiles):
            r, c = divmod(i, cols)
            x0 = margin + c * (tw + gap)
            y0 = top + r * (tile_h + gap)
            box = (x0, y0, x0 + tw, y0 + tile_h)
            if getattr(th, "sterile", False):
                color = th.muted
            else:
                color = getattr(th, getattr(scr, "tile_color_key", "accent"))
            rrect(d, box, 14, fill=th.card)
            cx = (x0 + x0 + tw) / 2
            cy = y0 + tile_h * 0.34
            d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=color)
            # live badge on device tiles
            if scr.device_key is not None:
                d.ellipse((x0 + tw - 20, y0 + 12, x0 + tw - 12, y0 + 20),
                          fill=th.ok)
            lf = T.font(19, bold=True)
            lw = d.textlength(scr.title, font=lf)
            d.text((cx - lw / 2, y0 + tile_h * 0.55), scr.title, font=lf, fill=th.fg)
            self.tiles.append((box, scr))

    def handle_tap(self, x, y):
        for box, scr in self.tiles:
            x0, y0, x1, y1 = box
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.app.open_screen(scr)
                return True
        return False
