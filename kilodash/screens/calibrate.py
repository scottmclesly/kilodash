"""Touch calibration wizard.

Shows a sequence of targets; you tap each one. It records the *raw* controller
values (which are immune to whatever the current — possibly wrong — axis
mapping is), then brute-forces all 8 swap/invert combinations and picks the one
that best reproduces where you actually tapped. No guessing, no reasoning about
rotation: whichever mapping fits the taps wins.
"""

from .. import theme as T
from ..touch import apply_map
from ..widgets import Button
from .base import Screen


class CalibrationScreen(Screen):
    title = "Touch Setup"
    scrollable = False
    capture_all_taps = True          # app routes every tap straight here

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 3600
        self._targets = []
        self.reset()

    def reset(self):
        w, h = self.app.w, self.app.h
        m = 46
        # four well-separated targets in screen coordinates
        self._targets = [(m, m), (w - m, m), (w - m, h - m), (m, h - m)]
        self.i = 0
        self.samples = []            # (u, v, target_x, target_y)
        self.result = None           # (swap, invx, invy, err) once solved
        self.done_btn = None
        self.redo_btn = None

    def on_enter(self):
        self.reset()

    # ------------------------------------------------------------------ solve
    def _solve(self):
        w, h = self.app.w, self.app.h
        flip = self.app.config["flip_180"]
        best = None
        for swap in (False, True):
            for invx in (False, True):
                for invy in (False, True):
                    err = 0.0
                    for u, v, tx, ty in self.samples:
                        px, py = apply_map(u, v, swap, invx, invy, flip, w, h)
                        err += ((px - tx) ** 2 + (py - ty) ** 2) ** 0.5
                    if best is None or err < best[3]:
                        best = (swap, invx, invy, err)
        swap, invx, invy, err = best
        cfg = self.app.config
        cfg.set("touch_swap_xy", swap)
        cfg.set("touch_invert_x", invx)
        cfg.set("touch_invert_y", invy)
        cfg.set("touch_calibrated", True)
        self.result = best

    # ------------------------------------------------------------------ input
    def handle_tap(self, x, y):
        if self.result is not None:
            if self.done_btn and self.done_btn.hit(x, y):
                self.app.go_home()
                return True
            if self.redo_btn and self.redo_btn.hit(x, y):
                self.reset()
                return True
            return True
        # record raw value for the current target
        u, v = self.app.touch.last_raw
        tx, ty = self._targets[self.i]
        self.samples.append((u, v, tx, ty))
        self.i += 1
        if self.i >= len(self._targets):
            self._solve()
        return True

    # ---------------------------------------------------------------- drawing
    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        if self.result is None:
            d.text((0, 90), "", font=T.font(10))
            msg = "Tap the circle"
            f = T.font(22, bold=True)
            tw = d.textlength(msg, font=f)
            d.text((w / 2 - tw / 2, h / 2 - 60), msg, font=f, fill=th.fg)
            prog = f"{self.i + 1} / {len(self._targets)}"
            pf = T.font(16)
            pw = d.textlength(prog, font=pf)
            d.text((w / 2 - pw / 2, h / 2 - 26), prog, font=pf, fill=th.muted)
            # current target crosshair
            tx, ty = self._targets[self.i]
            r = 26
            d.ellipse((tx - r, ty - r, tx + r, ty + r), outline=th.accent, width=3)
            d.ellipse((tx - 4, ty - 4, tx + 4, ty + 4), fill=th.accent)
            d.line((tx - r - 8, ty, tx + r + 8, ty), fill=th.accent, width=1)
            d.line((tx, ty - r - 8, tx, ty + r + 8), fill=th.accent, width=1)
            return

        # result
        swap, invx, invy, err = self.result
        f = T.font(24, bold=True)
        msg = "Calibrated"
        tw = d.textlength(msg, font=f)
        d.text((w / 2 - tw / 2, 80), msg, font=f, fill=th.ok)
        lines = [
            f"swap XY : {'on' if swap else 'off'}",
            f"invert X: {'on' if invx else 'off'}",
            f"invert Y: {'on' if invy else 'off'}",
            f"fit error: {err / len(self.samples):.0f}px avg",
        ]
        y = 140
        for ln in lines:
            d.text((40, y), ln, font=T.font(17, mono=True), fill=th.fg)
            y += 30
        d.text((40, y + 6), "Tap Done to check it, or Redo.",
               font=T.font(14), fill=th.muted)

        self.done_btn = Button((16, h - 130, w - 16, h - 74), "Done",
                               kind="primary", font_size=22)
        self.redo_btn = Button((16, h - 66, w - 16, h - 14), "Redo",
                               kind="normal", font_size=20)
        self.done_btn.draw(d, th)
        self.redo_btn.draw(d, th)
