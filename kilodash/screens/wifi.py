"""Wi-Fi: enable/disable, scan visible SSIDs, tap to connect. Secured networks
prompt for a password via the on-screen keyboard.
"""

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, Keyboard, seg_row, spaced, status_square
from .base import Screen, HEADER_H

ROW_H = 58


def _signal_gauge(d, th, x, y, signal):
    """Small segmented signal gauge: weak = amber (degraded, never red)."""
    level = 0 if signal < 20 else 1 if signal < 45 else 2 if signal < 70 else 3
    col = th.ok if level >= 2 else th.warn
    seg_row(d, x, y, level + 1 if signal else 0, 4, col, th.card_hi,
            seg_w=7, seg_h=11, gap=2)


class WifiScreen(Screen):
    title = "Wi-Fi"
    tile_id = "wi-fi"
    glyph = "wifi"
    tile_color_key = "ok"
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


    def model_rows(self):
        """Radio state and the connected network, from tick()'s cached scan."""
        nets = self.nets or []
        cur = next((n for n in nets if n.get("in_use")), None)
        rows = [
            {"label": "RADIO", "value": "ON" if self.enabled else "OFF",
             "state": "ok" if self.enabled else "caution"},
            {"label": "SSID",
             "value": str(cur["ssid"]) if cur else "NOT CONNECTED",
             "state": "ok" if cur else "caution"},
        ]
        if cur:
            rows.append({"label": "SIGNAL", "value": f"{cur.get('signal', 0)}%",
                         "state": "ok" if (cur.get("signal") or 0) >= 50
                                  else "caution"})
            if cur.get("chan"):
                rows.append({"label": "CHANNEL", "value": str(cur["chan"]),
                             "state": None})
            if cur.get("security"):
                rows.append({"label": "SECURITY", "value": str(cur["security"]),
                             "state": None})
        rows.append({"label": "IN RANGE", "value": str(len(nets)), "state": None})
        rows.append({"label": "SAVED", "value": str(len(self.known or ())),
                     "state": None})
        rows.append({"label": "STATUS", "value": str(self.status or "—"),
                     "state": None})
        return rows

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
            sd.rectangle((14, y, w - 14, y + ROW_H - 6),
                         fill=th.card_hi if hi else th.card,
                         outline=th.card_hi, width=1)
            _signal_gauge(sd, th, 24, y + 20, n["signal"])
            ssid = n["ssid"][:18].upper()
            sd.text((66, y + 8), ssid, font=T.font(14, bold=True, mono=True),
                    fill=th.fg)
            sub = f"CH {n['chan']} · {(n['security'] or 'OPEN').upper()}"[:24]
            sd.text((66, y + 29), sub, font=T.font(T.SUB, mono=True),
                    fill=th.muted)
            if n["ssid"] in self.known:
                sd.text((w - 94, y + 12), spaced("SAVED"),
                        font=T.font(9, bold=True, mono=True), fill=th.accent)
            # square link-status glyph: lit = connected, hollow = not
            status_square(sd, (w - 38, y + 12, w - 26, y + 24),
                          "lit" if hi else "hollow",
                          th.ok if hi else th.muted)
            # record absolute (screen-space) hit rect
            self._rows.append((y, n))
        self.paste_list(top, h - top, surf)

        # header controls
        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        bar_y = HEADER_H + 4
        # radio off is a stand-down, not a fault: amber, never red
        self.toggle_btn = Button((14, bar_y, 150, bar_y + 40),
                                 "WIFI ON" if self.enabled else "WIFI OFF",
                                 kind="primary" if self.enabled else "normal",
                                 color=None if self.enabled else th.warn,
                                 font_size=16)
        self.toggle_btn.draw(d, th)
        scanning = self.scan_task is not None
        self.scan_btn = Button((w - 108, bar_y, w - 14, bar_y + 40),
                               "…" if scanning else "RESCAN",
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
