"""Pomodoro timer — a focus/break cycle that keeps running in the background.

The catch on this screen: it must keep counting even when you're looking at
another screen. Screens only `tick()` while they're the current view, so the
timer can't live in tick(). Instead a daemon thread owns the clock: the running
phase has an absolute end-time (monotonic), the thread advances work → break →
work when it lapses, and toasts the transition app-wide (toasts render over
whatever screen is up). Leaving the screen changes nothing — the thread runs for
the app's lifetime; the screen just draws whatever state it finds.

Classic 25/5, with a longer break after every four focus sessions.

Presentation follows the ship-instrument look the launcher pictograms set
(Cobb's Semiotic Standard): a hard-edged phase banner with its own glyph —
hazard-striped on rest phases — a 48-segment chronometer ring that
extinguishes as the phase burns down, spaced-caps status readouts, and
corner registration brackets. Work runs on `ok`; rest phases are caution
amber (`warn`), not red — red stays reserved for things that are wrong.
"""

import math
import threading
import time

from .. import theme as T
from ..widgets import Button
from .base import Screen, HEADER_H

LONG_EVERY = 4
PHASES = {
    "work":  {"label": "WORK CYCLE",    "secs": 25 * 60, "col": "ok",
              "glyph": "work"},
    "short": {"label": "REST INTERVAL", "secs": 5 * 60,  "col": "warn",
              "glyph": "rest"},
    "long":  {"label": "EXTENDED REST", "secs": 15 * 60, "col": "warn",
              "glyph": "deeprest"},
}

# instrument geometry (320×480 portrait, header above)
BANNER_Y, BANNER_H = HEADER_H + 8, 38          # 52..90
RING_CY, RING_OUT, RING_IN = 212, 100, 85
SEGS = 48
FRAME = (26, 100, None, 324)                   # bracket frame; x1 filled at draw
DOTS_Y = 344
RING_BAND = (96, 330)                          # dirty band for seconds-only ticks


def _spaced(s):
    return " ".join(s)


def _hazard(d, box, col, step=9, width=3):
    """45° caution striping kept strictly inside `box` (PIL has no clipping,
    so only full-height strokes are drawn; partial edge stripes are dropped)."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    x = x0
    while x + h <= x1:
        d.line((x, y1, x + h, y0), fill=col, width=width)
        x += step


def _phase_glyph(d, key, cx, cy, r, c):
    """Banner-sized Semiotic-Standard companions to the launcher pictograms."""
    lw = max(2, round(r / 5))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=c, width=lw)
    if key == "work":       # chronometer: elapsed sector filled
        s = r * 0.68
        d.pieslice((cx - s, cy - s, cx + s, cy + s), 270, 30, fill=c)
    elif key == "rest":     # stand-down bars
        bw, bh = r * 0.16, r * 0.5
        for dx in (-r * 0.3, r * 0.3):
            d.rectangle((cx + dx - bw, cy - bh, cx + dx + bw, cy + bh), fill=c)
    else:                   # deeprest: below the horizon
        s = r * 0.68
        d.pieslice((cx - s, cy - s, cx + s, cy + s), 0, 180, fill=c)


class PomodoroScreen(Screen):
    title = "Pomodoro"
    glyph = "pomodoro"
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
        self._last_phase = None
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
            verb = "resume duty" if nxt == "work" else "stand down"
            self.app.toast(f"{PHASES[nxt]['label']} — {verb}", secs=4)
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
        self._last_phase = None

    def tick(self):
        # phase/pause flips repaint everything (banner, buttons, striping);
        # a plain seconds tick only touches the chronometer band.
        if self.running != self._last_running or self.phase != self._last_phase:
            self._last_running = self.running
            self._last_phase = self.phase
            self._last_shown = int(math.ceil(self._remaining()))
            return True
        secs = int(math.ceil(self._remaining()))
        if secs != self._last_shown:
            self._last_shown = secs
            self.report_dirty((0, RING_BAND[0], self.app.w, RING_BAND[1]))
            return True
        return False

    # ---- rendering ----
    def draw_content(self, d, th):
        w = self.app.w
        self._btns = {}
        ph = PHASES[self.phase]
        col = getattr(th, ph["col"])
        total = ph["secs"]
        remaining = self._remaining()
        frac = max(0.0, min(1.0, remaining / total)) if total else 0.0

        self._draw_banner(d, th, ph, col)
        self._draw_ring(d, th, col, frac, remaining)
        self._draw_cycles(d, th, col)

        # controls
        by = self.app.h - 104
        start = Button((12, by, w - 12, by + 46),
                       "HOLD" if self.running else "START",
                       color=col, font_size=20)      # match the ring's phase colour
        start.draw(d, th)
        self._btns["toggle"] = start
        half = (w - 12 * 2 - 8) / 2
        reset = Button((12, by + 54, 12 + half, by + 96), "RESET",
                       kind="ghost", font_size=17)
        skip = Button((w - 12 - half, by + 54, w - 12, by + 96), "SKIP",
                      kind="normal", font_size=17)
        reset.draw(d, th)
        skip.draw(d, th)
        self._btns["reset"] = reset
        self._btns["skip"] = skip

    def _draw_banner(self, d, th, ph, col):
        w = self.app.w
        y0, y1 = BANNER_Y, BANNER_Y + BANNER_H
        d.rectangle((12, y0, w - 12, y1), fill=th.card, outline=col, width=2)
        if ph["col"] == "warn":       # rest phases wear caution end-caps
            for zx in (50, w - 16 - 46):    # left cap clears the phase glyph
                _hazard(d, (zx, y0 + 4, zx + 46, y1 - 4), col)
        _phase_glyph(d, ph["glyph"], 34, (y0 + y1) // 2, 11, col)
        f = T.font(17, bold=True, mono=True)
        lab = ph["label"]
        lw = d.textlength(lab, font=f)
        d.text(((w - lw) / 2, y0 + (BANNER_H - 17) / 2 - 2), lab,
               font=f, fill=col)

    def _draw_ring(self, d, th, col, frac, remaining):
        w = self.app.w
        cx, cy = w // 2, RING_CY

        # corner registration brackets
        fx0, fy0, fx1, fy1 = FRAME[0], FRAME[1], w - FRAME[0], FRAME[3]
        bl = 14
        for x, sx in ((fx0, 1), (fx1, -1)):
            for y, sy in ((fy0, 1), (fy1, -1)):
                d.line((x, y, x + sx * bl, y), fill=th.muted, width=2)
                d.line((x, y, x, y + sy * bl), fill=th.muted, width=2)

        # 48-segment chronometer: lit segments = time left, extinguishing
        # clockwise-backwards toward 12 o'clock as the phase burns down
        lit = math.ceil(frac * SEGS)
        for i in range(SEGS):
            a = math.radians(-90 + i * 360 / SEGS)
            x0 = cx + RING_IN * math.cos(a)
            y0 = cy + RING_IN * math.sin(a)
            x1 = cx + RING_OUT * math.cos(a)
            y1 = cy + RING_OUT * math.sin(a)
            d.line((x0, y0, x1, y1), fill=col if i < lit else th.card_hi,
                   width=4)
        r = RING_IN - 8
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=th.card_hi, width=1)

        # time mm:ss centred in the ring
        secs = int(math.ceil(remaining))
        txt = f"{secs // 60:02d}:{secs % 60:02d}"
        f_t = T.font(52, bold=True, mono=True)
        tw = d.textlength(txt, font=f_t)
        d.text((cx - tw / 2, cy - 40), txt, font=f_t, fill=th.fg)
        sub = "HOLD" if not self.running else \
              ("COMPLETE" if remaining <= 0 else "RUNNING")
        f_s = T.font(11, bold=True, mono=True)
        sub = _spaced(sub)
        sw = d.textlength(sub, font=f_s)
        d.text((cx - sw / 2, cy + 24), sub, font=f_s,
               fill=th.muted if self.running else col)

    def _draw_cycles(self, d, th, col):
        w = self.app.w
        cx = w // 2
        gap, s = 30, 8                 # square half-size — blocks, not dots
        x0 = cx - (LONG_EVERY - 1) * gap / 2
        for i in range(LONG_EVERY):
            x = x0 + i * gap
            box = (x - s, DOTS_Y - s, x + s, DOTS_Y + s)
            if i < self.work_in_set:
                d.rectangle(box, fill=col)
            else:
                d.rectangle(box, outline=th.card_hi, width=2)
        cnt = f"{self.completed:02d} CYCLES LOGGED"
        f_c = T.font(11, mono=True)
        cw = d.textlength(cnt, font=f_c)
        d.text((cx - cw / 2, DOTS_Y + 16), cnt, font=f_c, fill=th.muted)

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
