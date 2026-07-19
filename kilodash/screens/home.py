"""Launcher: header shows the live IP (rotating WiFi/LAN every 3s) + clock,
below it an adaptive grid of tiles. Fixed tiles are always shown; device tiles
appear only while their dongle is plugged in (see devices.py). All taps.
"""

import time

from .. import pictograms, system, theme as T
from ..widgets import spaced
from .base import Screen, HEADER_H

IFACE_LABELS = {"wlan0": "WiFi", "eth0": "LAN"}
IFACE_ORDER = ["wlan0", "eth0"]
ROTATE_SEC = 3


class LauncherScreen(Screen):
    title = "Scottina"
    tile_id = "home"
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
        d.rectangle((0, HEADER_H - 2, w, HEADER_H), fill=th.card_hi)
        if self.ips:
            label, ip = self.ips[int(time.time() / ROTATE_SEC) % len(self.ips)]
            d.text((14, 5), spaced(label.upper()),
                   font=T.font(9, bold=True, mono=True), fill=th.muted)
            d.text((14, 18), ip, font=T.font(20, bold=True, mono=True),
                   fill=th.accent)
            if len(self.ips) > 1:
                # square uplink-select pips, lit = the shown interface
                dotx = w - 60
                cur = int(time.time() / ROTATE_SEC) % len(self.ips)
                for i in range(len(self.ips)):
                    box = (dotx + i * 10, 6, dotx + i * 10 + 5, 11)
                    if i == cur:
                        d.rectangle(box, fill=th.accent)
                    else:
                        d.rectangle(box, outline=th.card_hi, width=1)
        else:
            d.text((14, 14), spaced("NO UPLINK"),
                   font=T.font(14, bold=True, mono=True), fill=th.muted)
        if self.app.config["show_clock"]:
            clk = time.strftime("%H:%M")
            f = T.font(16, bold=True, mono=True)
            tw = d.textlength(clk, font=f)
            d.text((w - tw - 14, 16), clk, font=f, fill=th.fg)

    def _visible(self):
        return [s for s in self.app.screens[1:]
                if (s.device_key is None or self.app.devices.has(s.device_key))
                and s.available()]


    def model(self):
        """WEB-PROTOCOL.md §4.2 — the launcher.

        `available` mirrors the panel exactly: a hotplug screen whose device
        is absent renders dimmed and non-interactive, never hidden, so the
        web shows the same inventory the operator sees."""
        tiles = []
        for s in self.app.screens[1:]:
            if not s.tile_id:
                continue
            present = (s.device_key is None
                       or self.app.devices.has(s.device_key))
            tiles.append({
                "id": s.tile_id,
                "title": s.title,
                "glyph": s.glyph,
                "available": bool(present and s.available()),
                "badge": "lit" if s.device_key else None,
            })
        return {"kind": "home", "tiles": tiles}

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
            d.rectangle(box, fill=th.card, outline=th.card_hi, width=1)
            cx = (x0 + x0 + tw) / 2
            cy = y0 + tile_h * 0.34
            # semiotic-standard pictogram, one per subsystem (pictograms.py)
            pictograms.draw(d, getattr(scr, "glyph", None), cx, cy,
                            min(16, tile_h * 0.22), color)
            # live badge on device tiles: lit square, the row-status idiom
            if scr.device_key is not None:
                d.rectangle((x0 + tw - 20, y0 + 12, x0 + tw - 12, y0 + 20),
                            fill=th.ok)
            lf = T.font(14, bold=True, mono=True)
            label = scr.title.upper()
            lw = d.textlength(label, font=lf)
            d.text((cx - lw / 2, y0 + tile_h * 0.58), label, font=lf,
                   fill=th.fg)
            self.tiles.append((box, scr))

    def handle_tap(self, x, y):
        for box, scr in self.tiles:
            x0, y0, x1, y1 = box
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.app.open_screen(scr)
                return True
        return False
