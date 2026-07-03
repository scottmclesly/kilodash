"""LAN scan: enumerate hosts on the local subnet (arp-scan) with IP, hostname,
MAC and vendor. Tap Scan to (re)run; list scrolls vertically.
"""

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

ROW_H = 62


class LanScreen(Screen):
    title = "LAN Scan"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.8
        self.hosts = []
        self.iface = ""
        self.task = None
        self.status = "Tap Scan to discover hosts"
        self.scan_btn = None

    def on_enter(self):
        if not self.hosts and self.task is None:
            self.start_scan()

    def start_scan(self):
        if self.task and not self.task.done:
            return
        self.status = "Scanning subnet…"
        self.scroll = 0
        self.task = system.Task(system.lan_scan)

    def tick(self):
        if self.task and self.task.done:
            if self.task.error:
                self.status = f"Scan error: {self.task.error}"
            else:
                res = self.task.result or {}
                self.hosts = res.get("hosts", [])
                self.iface = res.get("iface", "")
                self.status = f"{len(self.hosts)} hosts on {self.iface}"
            self.task = None
            return True
        return self.task is not None

    def content_area(self):
        return (0, HEADER_H + 50, self.app.w, self.app.h - HEADER_H - 50)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H + 50

        # scrollable host list rendered onto its own tall surface
        self.content_h = max(len(self.hosts) * ROW_H + 8, h - top)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, hst in enumerate(self.hosts):
            y = i * ROW_H
            rrect(sd, (14, y, w - 14, y + ROW_H - 6), 10, fill=th.card)
            sd.text((24, y + 8), hst["ip"],
                    font=T.font(19, bold=True, mono=True), fill=th.fg)
            name = hst["host"] or hst["vendor"] or "unknown"
            sd.text((24, y + 34), name[:34], font=T.font(14), fill=th.accent)
            mac = hst["mac"]
            mw = sd.textlength(mac, font=T.font(13, mono=True))
            sd.text((w - 24 - mw, y + 36), mac,
                    font=T.font(13, mono=True), fill=th.muted)
        self.paste_list(top, h - top, surf)

        # status bar + scan button (above the list, drawn on top)
        bar_y = HEADER_H + 4
        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        rrect(d, (14, bar_y, w - 120, bar_y + 40), 8, fill=th.card)
        d.text((24, bar_y + 12), self.status[:26], font=T.font(14), fill=th.muted)
        scanning = self.task is not None
        self.scan_btn = Button((w - 108, bar_y, w - 14, bar_y + 40),
                               "…" if scanning else "Scan",
                               kind="primary", font_size=18)
        self.scan_btn.enabled = not scanning
        self.scan_btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.scan_btn and self.scan_btn.hit(x, y):
            self.start_scan()
            return True
        return False
