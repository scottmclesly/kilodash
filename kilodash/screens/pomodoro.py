"""Pomodoro timer — a focus/break cycle that keeps running in the background.

The catch on this screen: it must keep counting even when you're looking at
another screen. Screens only `tick()` while they're the current view, so the
timer can't live in tick(). Instead a daemon thread owns the clock: the running
phase has an absolute end-time (monotonic), the thread advances work → break →
work when it lapses, and toasts the transition app-wide (toasts render over
whatever screen is up). Leaving the screen changes nothing — the thread runs for
the app's lifetime; the screen just draws whatever state it finds.

Classic 25/5, with a longer break after every four focus sessions.
"""

import math
import threading
import time

from .. import theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

LONG_EVERY = 4
PHASES = {
    "work":  {"label": "FOCUS",       "secs": 25 * 60, "col": "ok"},
    "short": {"label": "SHORT BREAK",  "secs": 5 * 60, "col": "bad"},
    "long":  {"label": "LONG BREAK",   "secs": 15 * 60, "col": "bad"},
}


class PomodoroScreen(Screen):
    title = "Pomodoro"
    tile_color_key = "bad"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.2
        self.phase = "work"
        self._left = float(PHASES["work"]["secs"])   # seconds left (authoritative while paused)
        self._end = 0.0                               # monotonic target while running
        self.running = False
        self.work_in_set = 0                          # completed focus blocks toward a long break
        self.completed = 0                            # lifetime focus blocks
        self._last_shown = -1
        self._last_running = None
        self._btns = {}
        # background clock — runs for the app's lifetime so the timer survives
        # leaving the screen. Daemon: dies with the process.
        self._stop = False
        threading.Thread(target=self._run_loop, daemon=True).start()

    # ---- clock ----
    def _remaining(self):
        if self.running:
            return max(0.0, self._end - time.monotonic())
        return self._left

    def _run_loop(self):
        while not self._stop:
            if self.running and time.monotonic() >= self._end:
                self._advance(credit=True, autostart=True, announce=True)
            time.sleep(0.2)

    def _advance(self, credit, autostart, announce=False):
        if self.phase == "work":
            if credit:
                self.completed += 1
            self.work_in_set += 1
            nxt = "long" if self.work_in_set >= LONG_EVERY else "short"
        else:
            if self.work_in_set >= LONG_EVERY:
                self.work_in_set = 0
            nxt = "work"
        self.phase = nxt
        self._left = float(PHASES[nxt]["secs"])
        self.running = autostart
        if autostart:
            self._end = time.monotonic() + self._left
        if announce:
            self.app.toast(f"{PHASES[nxt]['label']} — go!", secs=4)
            self.app.flash()          # no speaker — blink the screen to get attention
        self.app.dirty = True

    # ---- controls ----
    def _toggle(self):
        if self.running:
            self._left = self._remaining()
            self.running = False
        else:
            self._end = time.monotonic() + self._left
            self.running = True

    def _reset(self):
        self.running = False
        self.phase = "work"
        self.work_in_set = 0
        self._left = float(PHASES["work"]["secs"])

    def _skip(self):
        self._advance(credit=False, autostart=self.running)

    # ---- lifecycle ----
    def on_enter(self):
        self._last_shown = -1          # force a fresh draw on entry

    def tick(self):
        secs = int(math.ceil(self._remaining()))
        if secs != self._last_shown or self.running != self._last_running:
            self._last_shown = secs
            self._last_running = self.running
            return True
        return False

    # ---- rendering ----
    def draw_content(self, d, th):
        w = self.app.w
        self._btns = {}
        col = getattr(th, PHASES[self.phase]["col"])
        total = PHASES[self.phase]["secs"]
        remaining = self._remaining()
        frac = max(0.0, min(1.0, remaining / total)) if total else 0.0

        # phase label
        f_lab = T.font(21, bold=True)
        lab = PHASES[self.phase]["label"]
        lw = d.textlength(lab, font=f_lab)
        d.text(((w - lw) / 2, HEADER_H + 18), lab, font=f_lab, fill=col)

        # progress ring (depletes as time passes)
        cx, cy, R, bw = w // 2, HEADER_H + 172, 98, 15
        box = (cx - R, cy - R, cx + R, cy + R)
        d.ellipse((cx - R + bw, cy - R + bw, cx + R - bw, cy + R - bw),
                  fill=th.card)                          # inner face
        d.arc(box, 0, 360, fill=th.card_hi, width=bw)    # faint full ring
        if frac > 0:
            d.arc(box, -90, -90 + 360 * frac, fill=col, width=bw)

        # time mm:ss centred in the ring
        secs = int(math.ceil(remaining))
        txt = f"{secs // 60:02d}:{secs % 60:02d}"
        f_t = T.font(52, bold=True, mono=True)
        tw = d.textlength(txt, font=f_t)
        d.text((cx - tw / 2, cy - 36), txt, font=f_t, fill=th.fg)
        sub = "PAUSED" if not self.running else \
              ("done" if remaining <= 0 else "running")
        f_s = T.font(12, bold=True)
        sw = d.textlength(sub, font=f_s)
        d.text((cx - sw / 2, cy + 22), sub, font=f_s,
               fill=th.muted if self.running else col)

        # session dots + lifetime count
        dy = cy + R + 22
        gap, dr = 26, 8
        x0 = cx - (LONG_EVERY - 1) * gap / 2
        for i in range(LONG_EVERY):
            x = x0 + i * gap
            filled = i < self.work_in_set
            d.ellipse((x - dr, dy - dr, x + dr, dy + dr),
                      fill=col if filled else th.card_hi)
        cnt = f"{self.completed} completed today"
        cw = d.textlength(cnt, font=T.font(12))
        d.text((cx - cw / 2, dy + 18), cnt, font=T.font(12), fill=th.muted)

        # controls
        by = self.app.h - 104
        start = Button((12, by, w - 12, by + 46),
                       "Pause" if self.running else "Start",
                       color=col, font_size=20)      # match the ring's phase colour
        start.draw(d, th)
        self._btns["toggle"] = start
        half = (w - 12 * 2 - 8) / 2
        reset = Button((12, by + 54, 12 + half, by + 96), "Reset",
                       kind="ghost", font_size=17)
        skip = Button((w - 12 - half, by + 54, w - 12, by + 96), "Skip",
                      kind="normal", font_size=17)
        reset.draw(d, th)
        skip.draw(d, th)
        self._btns["reset"] = reset
        self._btns["skip"] = skip

    def handle_tap(self, x, y):
        if self._btns["toggle"].hit(x, y):
            self._toggle()
            return True
        if self._btns["reset"].hit(x, y):
            self._reset()
            return True
        if self._btns["skip"].hit(x, y):
            self._skip()
            return True
        return False
