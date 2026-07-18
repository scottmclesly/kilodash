"""Pi health: temperature, CPU, memory, disk, uptime, Wi-Fi signal,
throttling status. Each metric is a labelled bar or value card.
"""

import subprocess

from .. import system, theme as T
from ..widgets import rrect
from .base import Screen


def _bar(d, th, box, pct, color):
    x0, y0, x1, y1 = box
    rrect(d, box, 6, fill=th.card_hi)
    fillw = x0 + (x1 - x0) * max(0, min(100, pct)) / 100
    if fillw > x0 + 4:
        rrect(d, (x0, y0, fillw, y1), 6, fill=color)


class HealthScreen(Screen):
    title = "Pi Health"
    glyph = "health"
    tile_color_key = "warn"
    icon = ""

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 2.0
        self.d = {}
        self.confirm = False
        self._btn_box = (0, 0, 0, 0)
        self._cancel_box = (0, 0, 0, 0)
        self._ok_box = (0, 0, 0, 0)
        self.tick()

    def tick(self):
        self.d = system.health()
        return True

    def _temp_color(self, th, temp):
        try:
            t = float(temp)
        except ValueError:
            return th.muted
        if t >= 75:
            return th.bad
        if t >= 65:
            return th.warn
        return th.ok

    def draw_content(self, d, th):
        w = self.app.w
        m = self.d
        y = 50

        def card(label, value, pct=None, color=None, sub=None, h=64):
            nonlocal y
            rrect(d, (14, y, w - 14, y + h), 10, fill=th.card)
            d.text((26, y + 8), label, font=T.font(15), fill=th.muted)
            f = T.font(22, bold=True)
            vw = d.textlength(value, font=f)
            d.text((w - 26 - vw, y + 6), value, font=f, fill=color or th.fg)
            if sub:
                d.text((26, y + 28), sub, font=T.font(12, mono=True), fill=th.muted)
            if pct is not None:
                _bar(d, th, (26, y + h - 18, w - 26, y + h - 8),
                     pct, color or th.accent)
            y += h + 8

        def half(col, label, value, color=None):
            x0 = 14 if col == 0 else (w + 4) / 2
            x1 = (w - 18) / 2 if col == 0 else w - 14
            rrect(d, (x0, y, x1, y + 52), 10, fill=th.card)
            d.text((x0 + 12, y + 8), label, font=T.font(13), fill=th.muted)
            size = 18
            f = T.font(size, bold=True)
            while size > 12 and d.textlength(value, font=f) > x1 - x0 - 24:
                size -= 1
                f = T.font(size, bold=True)
            d.text((x0 + 12, y + 26), value, font=f, fill=color or th.fg)

        temp = m.get("temp_c", "?")
        card("CPU temp", f"{temp}°C", color=self._temp_color(th, temp), h=44)
        card("Memory", f"{m.get('mem_pct', 0)}%", pct=m.get("mem_pct", 0),
             color=th.accent,
             sub=f"{m.get('mem_used_mb',0)} / {m.get('mem_total_mb',0)} MB")
        card("Disk /", f"{m.get('disk_pct', 0)}%", pct=m.get("disk_pct", 0),
             color=th.accent,
             sub=f"{m.get('disk_used','?')} / {m.get('disk_total','?')} MB")

        sig = m.get("wifi_signal", 0)
        scol = th.ok if sig >= 55 else th.warn if sig >= 30 else th.bad
        card("Wi-Fi", f"{sig}%", pct=sig, color=scol if sig else th.muted,
             sub=m.get("wifi_ssid", "") or "not connected")

        half(0, "Clock", f"{m.get('cpu_mhz', 0)} MHz")
        half(1, "Load", " ".join(m.get("loadavg", [])) or "?")
        y += 60

        thr = m.get("throttled", False)
        half(0, "Uptime", m.get("uptime", "?"))
        half(1, "Throttle", "YES" if thr else "OK",
             color=th.bad if thr else th.ok)
        y += 60

        self._btn_box = (14, y, w - 14, y + 36)
        rrect(d, self._btn_box, 10, fill=th.card, outline=th.bad)
        f = T.font(17, bold=True)
        tw = d.textlength("Shutdown Pi", font=f)
        d.text(((w - tw) / 2, y + 8), "Shutdown Pi", font=f, fill=th.bad)

        if self.confirm:
            self._draw_confirm(d, th)

    def _draw_confirm(self, d, th):
        w = self.app.w
        x0, y0, x1, y1 = 26, 170, w - 26, 300
        rrect(d, (x0 - 3, y0 - 3, x1 + 3, y1 + 3), 14, fill=th.bg)
        rrect(d, (x0, y0, x1, y1), 12, fill=th.card, outline=th.bad, width=2)
        f = T.font(20, bold=True)
        tw = d.textlength("Shutdown Pi?", font=f)
        d.text(((w - tw) / 2, y0 + 18), "Shutdown Pi?", font=f, fill=th.fg)
        d.text((x0 + 22, y0 + 52), "Powers off the board.",
               font=T.font(14), fill=th.muted)
        by = y1 - 48
        mid = w / 2
        self._cancel_box = (x0 + 14, by, mid - 6, by + 36)
        self._ok_box = (mid + 6, by, x1 - 14, by + 36)
        rrect(d, self._cancel_box, 10, fill=th.card_hi)
        rrect(d, self._ok_box, 10, fill=th.bad)
        fb = T.font(16, bold=True)
        for box, label, col in ((self._cancel_box, "Cancel", th.fg),
                                (self._ok_box, "Shutdown", th.bg)):
            bx0, by0, bx1, _ = box
            lw = d.textlength(label, font=fb)
            d.text(((bx0 + bx1 - lw) / 2, by0 + 9), label, font=fb, fill=col)

    @staticmethod
    def _in(box, x, y):
        x0, y0, x1, y1 = box
        return x0 <= x <= x1 and y0 <= y <= y1

    def handle_tap(self, x, y):
        if self.confirm:
            if self._in(self._ok_box, x, y):
                subprocess.Popen(["sudo", "poweroff"])
            self.confirm = False       # any other tap dismisses
            return True
        if self._in(self._btn_box, x, y):
            self.confirm = True
            return True
        return False
