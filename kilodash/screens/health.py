"""Pi health: temperature, CPU, memory, disk, uptime, Wi-Fi signal,
throttling status. Each metric is a labelled bar or value card.
"""

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

        def card(label, value, pct=None, color=None, sub=None):
            nonlocal y
            rrect(d, (14, y, w - 14, y + 60), 10, fill=th.card)
            d.text((26, y + 8), label, font=T.font(15), fill=th.muted)
            f = T.font(22, bold=True)
            vw = d.textlength(value, font=f)
            d.text((w - 26 - vw, y + 6), value, font=f, fill=color or th.fg)
            if pct is not None:
                _bar(d, th, (26, y + 40, w - 26, y + 50), pct, color or th.accent)
            elif sub:
                d.text((26, y + 34), sub, font=T.font(14, mono=True), fill=th.muted)
            y += 68

        temp = m.get("temp_c", "?")
        card("CPU temp", f"{temp}°C", color=self._temp_color(th, temp),
             sub=f"clock {m.get('cpu_mhz', 0)} MHz  load {' '.join(m.get('loadavg', []))}")
        card("Memory", f"{m.get('mem_pct', 0)}%", pct=m.get("mem_pct", 0),
             color=th.accent)
        d.text((26, y - 34), f"{m.get('mem_used_mb',0)} / {m.get('mem_total_mb',0)} MB",
               font=T.font(13, mono=True), fill=th.muted)
        card("Disk /", f"{m.get('disk_pct', 0)}%", pct=m.get("disk_pct", 0),
             color=th.accent)
        d.text((26, y - 34), f"{m.get('disk_used','?')} / {m.get('disk_total','?')} MB",
               font=T.font(13, mono=True), fill=th.muted)

        sig = m.get("wifi_signal", 0)
        scol = th.ok if sig >= 55 else th.warn if sig >= 30 else th.bad
        card("Wi-Fi", f"{sig}%", pct=sig, color=scol if sig else th.muted,
             )
        d.text((26, y - 34), m.get("wifi_ssid", "") or "not connected",
               font=T.font(13), fill=th.muted)

        # bottom row: uptime + throttle
        rrect(d, (14, y, (w - 18) / 2, y + 52), 10, fill=th.card)
        d.text((26, y + 8), "Uptime", font=T.font(13), fill=th.muted)
        d.text((26, y + 26), m.get("uptime", "?"), font=T.font(18, bold=True), fill=th.fg)
        rrect(d, ((w + 4) / 2, y, w - 14, y + 52), 10, fill=th.card)
        thr = m.get("throttled", False)
        d.text(((w + 4) / 2 + 12, y + 8), "Throttle", font=T.font(13), fill=th.muted)
        d.text(((w + 4) / 2 + 12, y + 26),
               "YES" if thr else "OK",
               font=T.font(18, bold=True), fill=th.bad if thr else th.ok)
