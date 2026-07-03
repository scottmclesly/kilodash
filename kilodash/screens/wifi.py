"""Wi-Fi: enable/disable, scan visible SSIDs, tap to connect. Secured networks
prompt for a password via the on-screen keyboard.
"""

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, Keyboard, rrect
from .base import Screen, HEADER_H

ROW_H = 58


def _signal_bars(d, th, x, y, signal):
    heights = [6, 11, 16, 21]
    level = 0 if signal < 20 else 1 if signal < 45 else 2 if signal < 70 else 3
    for i, hgt in enumerate(heights):
        col = th.ok if i <= level else th.card_hi
        d.rectangle((x + i * 7, y + (21 - hgt), x + i * 7 + 5, y + 21), fill=col)


class WifiScreen(Screen):
    title = "Wi-Fi"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.8
        self.nets = []
        self.known = set()
        self.enabled = True
        self.scan_task = None
        self.connect_task = None
        self.status = ""
        self.toggle_btn = None
        self.scan_btn = None
        self._rows = []          # (rect, net)

    def on_enter(self):
        self.enabled = system.wifi_enabled()
        if self.enabled and not self.nets and self.scan_task is None:
            self.start_scan()

    def start_scan(self):
        if self.scan_task and not self.scan_task.done:
            return
        self.status = "Scanning…"
        self.scroll = 0
        self.known = system.known_ssids()
        self.scan_task = system.Task(system.scan_wifi)

    def tick(self):
        changed = False
        self.enabled = system.wifi_enabled()
        if self.scan_task and self.scan_task.done:
            self.nets = self.scan_task.result or []
            self.status = f"{len(self.nets)} networks"
            self.scan_task = None
            changed = True
        if self.connect_task and self.connect_task.done:
            ok, msg = self.connect_task.result or (False, "failed")
            self.app.toast(("Connected" if ok else "Failed") + f": {msg}"[:40])
            self.connect_task = None
            self.known = system.known_ssids()
            changed = True
        if self.scan_task or self.connect_task:
            changed = True
        return changed

    def content_area(self):
        return (0, HEADER_H + 50, self.app.w, self.app.h - HEADER_H - 50)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H + 50
        self._rows = []

        # list surface
        self.content_h = max(len(self.nets) * ROW_H + 8, h - top)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, n in enumerate(self.nets):
            y = i * ROW_H
            hi = n["in_use"]
            rrect(sd, (14, y, w - 14, y + ROW_H - 6), 10,
                  fill=th.card_hi if hi else th.card)
            _signal_bars(sd, th, 24, y + 12, n["signal"])
            ssid = n["ssid"][:20]
            sd.text((66, y + 8), ssid, font=T.font(18, bold=True), fill=th.fg)
            secured = n["security"] and n["security"].lower() != "open"
            sub = f"ch{n['chan']}  {n['security'] or 'open'}"
            sd.text((66, y + 31), sub, font=T.font(13), fill=th.muted)
            if n["ssid"] in self.known:
                sd.text((w - 90, y + 12), "saved", font=T.font(13), fill=th.accent)
            if hi:
                sd.text((w - 40, y + 12), "✓", font=T.font(22, bold=True), fill=th.ok)
            elif secured:
                sd.text((w - 36, y + 14), "🔒", font=T.font(18), fill=th.muted)
            # record absolute (screen-space) hit rect
            self._rows.append((y, n))
        self.paste_list(top, h - top, surf)

        # header controls
        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        bar_y = HEADER_H + 4
        self.toggle_btn = Button((14, bar_y, 150, bar_y + 40),
                                 "Wi-Fi ON" if self.enabled else "Wi-Fi OFF",
                                 kind="primary" if self.enabled else "danger",
                                 font_size=16)
        self.toggle_btn.draw(d, th)
        scanning = self.scan_task is not None
        self.scan_btn = Button((w - 108, bar_y, w - 14, bar_y + 40),
                               "…" if scanning else "Rescan",
                               kind="normal", font_size=16)
        self.scan_btn.enabled = self.enabled and not scanning
        self.scan_btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.toggle_btn and self.toggle_btn.hit(x, y):
            system.set_wifi(not self.enabled)
            self.enabled = system.wifi_enabled()
            if self.enabled:
                self.start_scan()
            else:
                self.nets = []
            return True
        if self.scan_btn and self.scan_btn.hit(x, y):
            self.start_scan()
            return True
        # list rows
        top = HEADER_H + 50
        for y0, n in self._rows:
            sy = top + y0 - self.scroll
            if sy <= y <= sy + ROW_H - 6 and y >= top:
                self._tap_network(n)
                return True
        return False

    def _tap_network(self, n):
        secured = n["security"] and n["security"].lower() != "open"
        if n["ssid"] in self.known or not secured:
            self.app.toast(f"Connecting to {n['ssid']}…")
            self.connect_task = system.Task(system.connect_wifi, n["ssid"], None)
            return
        # prompt for password
        kb = Keyboard(self.app.w, self.app.h, title=f"Password: {n['ssid']}"[:26],
                      secret=True,
                      on_done=lambda pw: self._do_connect(n["ssid"], pw),
                      on_cancel=self.app.close_keyboard)
        self.app.open_keyboard(kb)

    def _do_connect(self, ssid, pw):
        self.app.close_keyboard()
        self.app.toast(f"Connecting to {ssid}…")
        self.connect_task = system.Task(system.connect_wifi, ssid, pw)
