"""Settings: every adjustable variable as a card. Booleans toggle, integers
step, choices cycle. Also power actions (restart UI / reboot / shutdown) and a
touch calibration helper.
"""

import subprocess

from PIL import Image, ImageDraw

from .. import theme as T
from ..widgets import rrect
from .base import Screen, HEADER_H

ROW_H = 58


class SettingsScreen(Screen):
    title = "Settings"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 60
        self._hits = []          # (x0,y0,x1,y1, callback)

    def content_area(self):
        return (0, HEADER_H, self.app.w, self.app.h - HEADER_H)

    def _cycle_choice(self, key, spec):
        opts = spec["options"]
        i = (opts.index(spec["value"]) + 1) % len(opts)
        self.app.config.set(key, opts[i])
        if key == "theme":
            self.app.theme.set(opts[i])

    def _step(self, key, spec, direction):
        v = spec["value"] + direction * spec.get("step", 1)
        v = max(spec.get("min", 0), min(spec.get("max", 999999), v))
        self.app.config.set(key, v)

    def _toggle(self, key, spec):
        self.app.config.set(key, not spec["value"])

    def _power(self, cmd):
        subprocess.Popen(cmd)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H
        cfg = self.app.config
        self._hits = []

        # build a tall surface; collect hitboxes in surface space then offset
        rows = []
        for group, items in cfg.groups().items():
            rows.append(("header", group, None))
            for key, spec in items:
                rows.append(("setting", key, spec))
        rows.append(("header", "Power", None))
        rows.append(("action", "Restart UI",
                     lambda: self._power(["sudo", "systemctl", "restart", "kilodash"])))
        rows.append(("action", "Reboot Pi", lambda: self._power(["sudo", "reboot"])))
        rows.append(("action", "Shutdown", lambda: self._power(["sudo", "poweroff"])))

        # measure height
        surf_h = 0
        for kind, *_ in rows:
            surf_h += 28 if kind == "header" else ROW_H
        surf_h = max(surf_h + 10, h - top)
        self.content_h = surf_h

        surf = Image.new("RGB", (w, surf_h), th.bg)
        sd = ImageDraw.Draw(surf)
        y = 4
        hits_surf = []           # (x0,y0,x1,y1,cb) in surface space
        for kind, a, b in rows:
            if kind == "header":
                sd.text((16, y + 6), a.upper(), font=T.font(13, bold=True),
                        fill=th.muted)
                y += 28
                continue
            if kind == "action":
                rrect(sd, (14, y, w - 14, y + ROW_H - 6), 10, fill=th.card)
                col = th.bad if a in ("Reboot Pi", "Shutdown") else th.accent
                sd.text((26, y + 16), a, font=T.font(19, bold=True), fill=col)
                hits_surf.append((14, y, w - 14, y + ROW_H - 6, b))
                y += ROW_H
                continue
            # setting row
            key, spec = a, b
            rrect(sd, (14, y, w - 14, y + ROW_H - 6), 10, fill=th.card)
            sd.text((26, y + 8), spec["label"], font=T.font(16, bold=True), fill=th.fg)
            t = spec["type"]
            if t == "bool":
                self._draw_toggle(sd, th, w - 90, y + 14, spec["value"])
                hits_surf.append((w - 92, y, w - 14, y + ROW_H - 6,
                                  lambda k=key, s=spec: self._toggle(k, s)))
            elif t == "choice":
                val = str(spec["value"])
                vw = sd.textlength(val, font=T.font(16, bold=True))
                rrect(sd, (w - 40 - vw, y + 12, w - 20, y + 44), 8, fill=th.card_hi)
                sd.text((w - 30 - vw, y + 16), val, font=T.font(16, bold=True),
                        fill=th.accent)
                hits_surf.append((w - 50 - vw, y, w - 14, y + ROW_H - 6,
                                  lambda k=key, s=spec: self._cycle_choice(k, s)))
            elif t == "int":
                unit = spec.get("unit", "")
                val = f"{spec['value']}{unit}"
                # [-]  value  [+]
                self._stepper_box(sd, th, w - 44, y + 12, "+")
                self._stepper_box(sd, th, w - 150, y + 12, "-")
                vw = sd.textlength(val, font=T.font(16, bold=True))
                sd.text((w - 97 - vw / 2, y + 16), val, font=T.font(16, bold=True),
                        fill=th.fg)
                hits_surf.append((w - 150, y, w - 116, y + ROW_H - 6,
                                  lambda k=key, s=spec: self._step(k, s, -1)))
                hits_surf.append((w - 44, y, w - 10, y + ROW_H - 6,
                                  lambda k=key, s=spec: self._step(k, s, +1)))
            sd.text((26, y + 32),
                    self._hint(key), font=T.font(12), fill=th.muted)
            y += ROW_H

        self.paste_list(top, h - top, surf)

        # offset hitboxes into screen space
        for x0, y0, x1, y1, cb in hits_surf:
            self._hits.append((x0, top + y0 - self.scroll,
                               x1, top + y1 - self.scroll, cb))

    def _hint(self, key):
        return {
            "touch_swap_xy": "flip if taps land on wrong axis",
            "touch_invert_x": "flip if taps mirror left/right",
            "touch_invert_y": "flip if taps mirror up/down",
            "flip_180": "rotate whole UI (no reboot)",
            "dim_timeout_sec": "idle time before screensaver",
        }.get(key, "")

    def _draw_toggle(self, d, th, x, y, on):
        w_, h_ = 64, 30
        rrect(d, (x, y, x + w_, y + h_), 15, fill=th.ok if on else th.card_hi)
        kx = x + w_ - 27 if on else x + 3
        d.ellipse((kx, y + 3, kx + 24, y + 27), fill=th.fg)

    def _stepper_box(self, d, th, x, y, sym):
        rrect(d, (x, y, x + 34, y + 32), 8, fill=th.card_hi)
        f = T.font(22, bold=True)
        tw = d.textlength(sym, font=f)
        d.text((x + 17 - tw / 2, y + 3), sym, font=f, fill=th.accent)

    def handle_tap(self, x, y):
        for x0, y0, x1, y1, cb in self._hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                cb()
                return True
        return False
