"""Pi health: temperature, CPU, memory, disk, uptime, Wi-Fi signal,
throttling status — hard-edged instrument cards with spaced-caps mono
labels and segmented gauges (Semiotic-Standard look). Red is reserved
for genuinely critical states (thermal ceiling, throttling); shutdown
is an amber stand-down, not a fault.
"""

import subprocess

from .. import system, theme as T
from ..widgets import Button, confirm_dialog, seg_row, spaced
from .base import Screen

GAUGE_SEGS = 20          # 5% per segment


class HealthScreen(Screen):
    title = "Pi Health"
    tile_id = "pi-health"
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
        if t >= 80:              # SoC throttle point — genuinely critical
            return th.bad
        if t >= 65:
            return th.warn
        return th.ok


    def model_rows(self):
        """Generic model rows (WEB-PROTOCOL.md §4.6) — reads tick()'s cached
        `self.d`, never re-polls. Colour semantics match the panel exactly:
        the SoC throttle point is a genuine fault, 65C is caution."""
        m = getattr(self, "d", None) or {}

        def temp_state(v):
            try:
                t = float(v)
            except (TypeError, ValueError):
                return None
            return "fault" if t >= 80 else ("caution" if t >= 65 else "ok")

        def pct_state(v):
            try:
                p = float(str(v).rstrip("%"))
            except (TypeError, ValueError):
                return None
            return "fault" if p >= 90 else ("caution" if p >= 75 else "ok")

        load = m.get("loadavg") or []
        rows = [
            {"label": "TEMP", "value": "%s C" % m.get("temp_c", "—"),
             "state": temp_state(m.get("temp_c"))},
            {"label": "CPU", "value": "%s MHz" % m.get("cpu_mhz", "—"),
             "state": None},
            {"label": "LOAD", "value": " ".join(str(x) for x in load[:3]) or "—",
             "state": None},
            {"label": "MEM", "value": "%s%% (%s/%s MB)"
                                      % (m.get("mem_pct", "—"),
                                         m.get("mem_used_mb", "—"),
                                         m.get("mem_total_mb", "—")),
             "state": pct_state(m.get("mem_pct"))},
            {"label": "DISK", "value": "%s%% (%s/%s MB)"
                                       % (m.get("disk_pct", "—"),
                                          m.get("disk_used", "—"),
                                          m.get("disk_total", "—")),
             "state": pct_state(m.get("disk_pct"))},
            {"label": "UPTIME", "value": str(m.get("uptime", "—")),
             "state": None},
        ]
        if m.get("wifi_ssid"):
            rows.append({"label": "WIFI",
                         "value": "%s (%s%%)" % (m["wifi_ssid"],
                                                 m.get("wifi_signal", "?")),
                         "state": None})
        # `throttled` is a real bool from system.health(); guard against a
        # stringified "False" too, which would otherwise read as a fault.
        thr = m.get("throttled")
        if thr and str(thr).lower() not in ("false", "0", "0x0"):
            rows.append({"label": "THROTTLED",
                         "value": str(m.get("throttled_code", thr)),
                         "state": "fault"})
        return rows

    def draw_content(self, d, th):
        w = self.app.w
        m = self.d
        y = 50

        def card(label, value, pct=None, color=None, sub=None, h=64):
            nonlocal y
            d.rectangle((14, y, w - 14, y + h), fill=th.card,
                        outline=th.card_hi, width=1)
            d.text((26, y + 9), spaced(label),
                   font=T.font(10, bold=True, mono=True), fill=th.muted)
            f = T.font(19, bold=True, mono=True)
            vw = d.textlength(value, font=f)
            d.text((w - 26 - vw, y + 6), value, font=f, fill=color or th.fg)
            if sub:
                d.text((26, y + 25), sub, font=T.font(T.SUB, mono=True),
                       fill=th.muted)
            if pct is not None:
                lit = round(max(0, min(100, pct)) / 100 * GAUGE_SEGS)
                seg_row(d, 26, y + h - 20, lit, GAUGE_SEGS,
                        color or th.accent, th.card_hi,
                        seg_w=11, seg_h=12, gap=2)
            y += h + 6

        def half(col, label, value, color=None):
            x0 = 14 if col == 0 else (w + 4) / 2
            x1 = (w - 18) / 2 if col == 0 else w - 14
            d.rectangle((x0, y, x1, y + 52), fill=th.card,
                        outline=th.card_hi, width=1)
            d.text((x0 + 12, y + 8), spaced(label),
                   font=T.font(9, bold=True, mono=True), fill=th.muted)
            size = 15
            f = T.font(size, bold=True, mono=True)
            while size > 10 and d.textlength(value, font=f) > x1 - x0 - 24:
                size -= 1
                f = T.font(size, bold=True, mono=True)
            d.text((x0 + 12, y + 27), value, font=f, fill=color or th.fg)

        temp = m.get("temp_c", "?")
        card("CPU TEMP", f"{temp}°C", color=self._temp_color(th, temp), h=44)
        mem = m.get("mem_pct", 0)
        card("MEMORY", f"{mem}%", pct=mem,
             color=th.warn if mem >= 85 else th.ok,
             sub=f"{m.get('mem_used_mb',0)} / {m.get('mem_total_mb',0)} MB")
        disk = m.get("disk_pct", 0)
        card("DISK /", f"{disk}%", pct=disk,
             color=th.warn if disk >= 85 else th.ok,
             sub=f"{m.get('disk_used','?')} / {m.get('disk_total','?')} MB")

        # weak signal is degraded, never a fault: amber at worst
        sig = m.get("wifi_signal", 0)
        scol = th.ok if sig >= 55 else th.warn
        card("WI-FI SIGNAL", f"{sig}%", pct=sig,
             color=scol if sig else th.muted,
             sub=(m.get("wifi_ssid", "") or "NOT CONNECTED")[:34])

        half(0, "CLOCK", f"{m.get('cpu_mhz', 0)} MHz")
        half(1, "LOAD", " ".join(m.get("loadavg", [])) or "?")
        y += 58

        thr = m.get("throttled", False)
        half(0, "UPTIME", m.get("uptime", "?"))
        half(1, "THROTTLE", "THROTTLED" if thr else "NOMINAL",
             color=th.bad if thr else th.ok)
        y += 58

        # stand-down control: amber, never red (shutdown is not a fault)
        btn = Button((14, y, w - 14, y + 44), "SHUT DOWN",
                     color=th.warn, font_size=16)
        btn.draw(d, th)
        self._btn_box = btn.box

        if self.confirm:
            self._draw_confirm(d, th)

    def _draw_confirm(self, d, th):
        self._cancel_box, self._ok_box = confirm_dialog(
            d, th, self.app.w, spaced("SHUT DOWN?"),
            ("POWERS OFF THE BOARD.",),
            (("CANCEL", None), ("SHUT DOWN", th.warn)))

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
