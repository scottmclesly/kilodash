"""Light Dock — auto-sync screen for a docked Scottina Light (device_key
"scottinalight", DOCK-PROTOCOL.md; the engine lives in kilodash/lightdock.py).

Two-distance UI, split screen. The top pane is an animation readable from
across the room, drawn in the active theme's phosphor language: while the
sync runs the two device silhouettes draw together with pulses riding the
cable (pulse rate tracks transfer activity); done is the hug — devices
together, steady glow; interrupted is a sad face over a visibly broken
cable — "come look", not red-alarm. The bottom pane is a boring session-only
log: timestamped engine lines rendered verbatim (including the §6
logging-suspended statement), cleared on each dock, newest at the bottom.
If a problem persists across docks, that is the signal to drop into real
interfaces — this screen answers "did the sync land?" and nothing more.

Zero-interaction happy path: dock → sync runs itself; redock reruns it. The
only controls are Re-sync and the auto-pull-logs toggle (persisted in
config.DEFAULTS, so Settings renders it too). Per the KioskSpeed guidance
the animation region is the only dirty-rect repainting between log lines.
"""

import time

from .. import devices, lightdock, system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

# Fixed vertical bands; x-coordinates derive from app.w at draw time
# (the panel is 320×480 portrait — never hardcode widths).
ANIM_Y = HEADER_H + 4            # 48   the across-the-room animation
ANIM_H = 168
CTRL_Y = ANIM_Y + ANIM_H + 6     # 222  Re-sync | auto-pull toggle
CTRL_H = 48
LOG_TOP = CTRL_Y + CTRL_H + 6    # 276  session log fills the rest
ROW_H = 19

FAST_TICK = 0.15                 # animating: budget like the splash player
IDLE_TICK = 1.0                  # settled: watch for a redock, nothing more


class LightDockScreen(Screen):
    title = "Light Dock"
    glyph = "lightdock"
    tile_color_key = "accent"
    device_key = "scottinalight"

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = IDLE_TICK
        self.engine = None               # LightDockSync of the current session
        self.task = None                 # system.Task running engine.run()
        self._drawn_events = -1          # last event count painted
        self._tty_present = True         # redock edge detection
        self._pulse_rate = 1.0           # pulses/s on the cable
        self._act_sample = (0.0, 0)      # (monotonic, activity) for the rate
        self._btns = {}

    # -------------------------------------------------------------- lifecycle
    def on_enter(self):
        if not self._running():
            self._start_sync()

    def on_leave(self):
        # a run in flight is left to finish on its Task thread — every
        # request is timeout-bounded, so it ends by itself; the session log
        # persists until the next dock replaces it
        pass

    # ------------------------------------------------------------------ sync
    def _running(self):
        return self.task is not None and not self.task.done

    def _start_sync(self):
        tty = devices.light_tty()
        if not tty:
            return False
        self.engine = lightdock.LightDockSync(
            tty, pull_logs=bool(self.app.config["lightdock_pull_logs"]))
        self.task = system.Task(self.engine.run)
        self._drawn_events = -1
        self._act_sample = (time.monotonic(), 0)
        self.tick_interval = FAST_TICK
        return True

    def tick(self):
        if self.task and self.task.done:
            self.task = None
            self.tick_interval = IDLE_TICK
            if self.engine:
                self.app.toast("Sync complete"
                               if self.engine.state == self.engine.COMPLETE
                               else "Sync interrupted — see log")
            return True
        if not self._running():
            # redock while the screen is open: detection fires, the engine
            # reruns, the animation restarts — no user action
            present = devices.light_tty() is not None
            if present and not self._tty_present:
                self._tty_present = True
                self._start_sync()
                return True
            self._tty_present = present
            return False
        # engine busy: new log lines repaint the frame; otherwise only the
        # animation band goes to the panel
        if self.engine and len(self.engine.events) != self._drawn_events:
            return True
        self.report_dirty((0, ANIM_Y, self.app.w, ANIM_Y + ANIM_H))
        return True

    # -------------------------------------------------------------- activity
    def _pulse_speed(self):
        """Pulses/s, loosely tracking frames moved since the last look."""
        if not (self.engine and self.engine.client):
            return 1.0
        now = time.monotonic()
        t0, a0 = self._act_sample
        a1 = self.engine.client.activity
        if now - t0 >= 0.5:
            rate = (a1 - a0) / (now - t0)
            self._act_sample = (now, a1)
            self._pulse_rate = min(3.0, 0.8 + rate / 12.0)
        return self._pulse_rate

    # --------------------------------------------------------------- drawing
    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        self._draw_anim(d, th, w)
        self._draw_controls(d, th, w)
        self._draw_log(d, th, w, h)
        if self.engine:
            self._drawn_events = len(self.engine.events)

    # ---- top pane: the across-the-room animation ----
    def _draw_anim(self, d, th, w):
        rrect(d, (8, ANIM_Y, w - 8, ANIM_Y + ANIM_H), 12, fill=th.card)
        state = self.engine.state if self.engine else None
        cy = ANIM_Y + ANIM_H // 2
        if state == lightdock.LightDockSync.COMPLETE:
            self._draw_hug(d, th, w, cy)
        elif state == lightdock.LightDockSync.INTERRUPTED:
            self._draw_broken(d, th, w, cy)
        elif state == lightdock.LightDockSync.SYNCING:
            self._draw_syncing(d, th, w, cy)
        else:                            # no Light / no session yet
            self._draw_devices(d, th, w * 0.24, w * 0.76, cy)
            self._center_text(d, w, cy + ANIM_H * 0.34,
                              "waiting for Light…", th.muted)

    def _slab(self, d, th, cx, cy, sw, sh, lit):
        """One device silhouette: outlined slab with a lit screen inside."""
        d.rounded_rectangle((cx - sw / 2, cy - sh / 2, cx + sw / 2,
                             cy + sh / 2), radius=6, outline=th.fg, width=3)
        pad = max(6, sw // 6)
        d.rectangle((cx - sw / 2 + pad, cy - sh / 2 + pad,
                     cx + sw / 2 - pad, cy + sh / 2 - pad),
                    fill=th.accent if lit else th.card_hi)

    def _draw_devices(self, d, th, prime_cx, light_cx, cy, lit=False):
        """Prime's slab (left, larger) and Light's (right, smaller); returns
        the cable endpoints between their inner edges."""
        self._slab(d, th, prime_cx, cy, 58, 96, lit)
        self._slab(d, th, light_cx, cy, 42, 66, lit)
        return prime_cx + 29, light_cx - 21

    def _draw_syncing(self, d, th, w, cy):
        # the silhouettes draw together as bytes move
        p = self.engine.progress
        frac = (p["bytes_done"] / p["bytes_total"]) if p["bytes_total"] else 0.0
        prime_cx = w * (0.22 + 0.10 * frac)
        light_cx = w * (0.78 - 0.10 * frac)
        x0, x1 = self._draw_devices(d, th, prime_cx, light_cx, cy, lit=True)
        d.line((x0, cy, x1, cy), fill=th.muted, width=3)
        # phosphor pulses riding the cable, rate ~ transfer activity
        phase = time.monotonic() * self._pulse_speed()
        for i in range(3):
            f = (phase + i / 3.0) % 1.0
            px = x0 + (x1 - x0) * f
            d.ellipse((px - 4, cy - 4, px + 4, cy + 4), fill=th.accent)

    def _draw_hug(self, d, th, w, cy):
        # devices together, steady glow — unambiguous at 3 m
        cx = w / 2
        self._draw_devices(d, th, cx - 31, cx + 23, cy, lit=True)
        for grow, col in ((14, th.muted), (26, th.card_hi)):
            d.rounded_rectangle((cx - 62 - grow, cy - 48 - grow,
                                 cx + 44 + grow, cy + 48 + grow),
                                radius=14, outline=col, width=2)
        d.ellipse((cx - 9, cy - 5, cx - 1, cy + 3), fill=th.ok)

    def _draw_broken(self, d, th, w, cy):
        # sad face + visibly broken cable — "come look", not an alarm
        x0, x1 = self._draw_devices(d, th, w * 0.22, w * 0.78, cy)
        mid = (x0 + x1) / 2
        d.line((x0, cy, mid - 16, cy), fill=th.muted, width=3)
        d.line((mid + 16, cy, x1, cy), fill=th.muted, width=3)
        for sx, sgn in ((mid - 16, 1), (mid + 16, -1)):     # frayed ends
            d.line((sx, cy, sx + 7 * sgn, cy - 7), fill=th.warn, width=2)
            d.line((sx, cy, sx + 7 * sgn, cy + 7), fill=th.warn, width=2)
        fy = cy - ANIM_H * 0.28
        r = 21
        d.ellipse((mid - r, fy - r, mid + r, fy + r), outline=th.warn, width=3)
        for ex in (-8, 8):
            d.ellipse((mid + ex - 3, fy - 8, mid + ex + 3, fy - 2),
                      fill=th.warn)
        d.arc((mid - 11, fy + 4, mid + 11, fy + 22), 200, 340,
              fill=th.warn, width=3)

    def _center_text(self, d, w, y, text, col, size=14):
        f = T.font(size)
        d.text((w / 2 - d.textlength(text, font=f) / 2, y), text,
               font=f, fill=col)

    # ---- middle: the only two controls ----
    def _draw_controls(self, d, th, w):
        running = self._running()
        resync = Button((14, CTRL_Y, w // 2 - 4, CTRL_Y + CTRL_H),
                        "Syncing…" if running else "Re-sync",
                        kind="primary", font_size=17)
        resync.enabled = not running
        resync.draw(d, th)
        self._btns["resync"] = resync.box if resync.enabled else None

        pull = bool(self.app.config["lightdock_pull_logs"])
        tog = Button((w // 2 + 4, CTRL_Y, w - 14, CTRL_Y + CTRL_H),
                     "Pull logs: %s" % ("ON" if pull else "OFF"),
                     kind="ghost", font_size=15)
        tog.draw(d, th)
        d.rectangle((w // 2 + 12, CTRL_Y + CTRL_H - 8, w - 22,
                     CTRL_Y + CTRL_H - 5), fill=th.ok if pull else th.muted)
        self._btns["pull"] = tog.box

    # ---- bottom pane: the session-only log ----
    def _draw_log(self, d, th, w, h):
        rrect(d, (8, LOG_TOP, w - 8, h - 6), 10, fill=th.card)
        events = list(self.engine.events) if self.engine else []
        if not events:
            d.text((22, LOG_TOP + 12),
                   "Dock Scottina Light to sync" if not self._running()
                   else "starting session…",
                   font=T.font(13), fill=th.muted)
            return
        rows = (h - 6 - LOG_TOP - 12) // ROW_H
        f_t = T.font(11, mono=True)
        f_m = T.font(12, mono=True)
        y = LOG_TOP + 8
        for ts, text in events[-rows:]:              # newest at the bottom
            col = th.fg
            if text.startswith("interrupted") or "FAILED" in text:
                col = th.warn
            elif text.startswith("session complete"):
                col = th.ok
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            d.text((18, y + 1), stamp, font=f_t, fill=th.muted)
            d.text((84, y), text[:60], font=f_m, fill=col)
            y += ROW_H

    # ------------------------------------------------------------------ input
    def handle_tap(self, x, y):
        box = self._btns.get("resync")
        if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            if not self._start_sync():
                self.app.toast("Light not found on USB")
            return True
        box = self._btns.get("pull")
        if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            new = not bool(self.app.config["lightdock_pull_logs"])
            self.app.config.set("lightdock_pull_logs", new)
            self.app.toast("Log pull %s (next sync)"
                           % ("on" if new else "off"))
            return True
        return False
