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

Presentation follows the ship-instrument look (Cobb's Semiotic Standard):
a hard-edged sync-state banner with a per-state glyph tops the band —
INTERRUPTED wears caution amber, never red (nothing here is a fault) — the
animation sits in the bracket-framed DOCK LINK instrument with a segmented
transfer gauge, and log rows carry square status glyphs (lit = landed,
slashed amber = interrupted, hollow = routine).
"""

import time

from .. import devices, lightdock, system, theme as T
from ..widgets import (Button, brackets, seg_row, spaced, state_glyph,
                       status_square)
from .base import Screen, HEADER_H

# Fixed vertical bands; x-coordinates derive from app.w at draw time
# (the panel is 320×480 portrait — never hardcode widths).
ANIM_Y = HEADER_H + 4            # 48   dock-state banner + animation band
ANIM_H = 168
BANNER_H = 32                    # hard-edged sync-state banner in the band
INST_TOP = ANIM_Y + BANNER_H + 6  # 86  bracket-framed DOCK LINK instrument
CTRL_Y = ANIM_Y + ANIM_H + 6     # 222  Re-sync | auto-pull toggle
CTRL_H = 48
LOG_TOP = CTRL_Y + CTRL_H + 6    # 276  session log fills the rest
ROW_H = 19

FAST_TICK = 0.15                 # animating: budget like the splash player
IDLE_TICK = 1.0                  # settled: watch for a redock, nothing more

# Semiotic-Standard banner states (kin to Tables' converter banner).
# INTERRUPTED is amber "come look", not a red alarm — no hazard caps.
STATES = {
    lightdock.LightDockSync.SYNCING:     ("SYNCING", "accent", "spin"),
    lightdock.LightDockSync.COMPLETE:    ("SYNC COMPLETE", "ok", "up"),
    lightdock.LightDockSync.INTERRUPTED: ("INTERRUPTED", "warn", "fault"),
    None:                                ("STANDING BY", "muted", "standby"),
}


class LightDockScreen(Screen):
    title = "Light Dock"
    tile_id = "light-dock"
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

    def model(self):
        """WEB-PROTOCOL.md §4.5.

        `link` is derived from the engine plus the cached tty edge state —
        the authoritative check (devices.light_tty()) globs USB sysfs and is
        far too expensive for model(), which must stay cheap.

        Log lines carry no level on the box, so the level is inferred with
        the same string test the panel uses — one rule, both surfaces.

        The draft's `session.done/total` are not emitted: those totals are
        computed inside _sync_tables and never stored, so there is nothing
        truthful to report. Byte progress is real and is emitted."""
        eng = self.engine
        if eng is None:
            link = "detected" if self._tty_present else "absent"
            return {"kind": "lightdock", "link": link, "device": None,
                    "session": {"phase": "idle"}, "log": []}
        link = {"syncing": "docked", "complete": "docked",
                "interrupted": "error"}.get(eng.state, "detected")
        info = eng.info or {}
        prog = eng.progress or {}
        log = []
        for ts, text in list(eng.events)[-32:]:
            if text.startswith("interrupted") or "FAILED" in text:
                level = "warn"
            elif text.startswith("session complete"):
                level = "ok"
            else:
                level = "info"
            log.append({"t": ts, "level": level, "text": text})
        return {
            "kind": "lightdock",
            "link": link,
            "device": ({"product": info.get("product"),
                        "fw": info.get("fw_version"),
                        "sd_present": bool(info.get("sd_present"))}
                       if info else None),
            "session": {
                "phase": getattr(eng, "phase", "idle"),
                "state": eng.state,
                "bytes": prog.get("bytes_done", 0),
                "bytes_total": prog.get("bytes_total", 0),
                "counts": dict(eng.counts or {}),
            },
            "log": log,
        }

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        self._draw_anim(d, th, w)
        self._draw_controls(d, th, w)
        self._draw_log(d, th, w, h)
        if self.engine:
            self._drawn_events = len(self.engine.events)

    # ---- top pane: sync-state banner + the across-the-room animation ----
    def _draw_anim(self, d, th, w):
        d.rectangle((0, ANIM_Y, w, ANIM_Y + ANIM_H), fill=th.bg)
        state = self.engine.state if self.engine else None
        label, ckey, glyph = STATES.get(state, STATES[None])
        col = getattr(th, ckey)
        y0, y1 = ANIM_Y, ANIM_Y + BANNER_H
        d.rectangle((12, y0, w - 12, y1), fill=th.card, outline=col, width=2)
        state_glyph(d, glyph, 30, (y0 + y1) // 2, 9, col)
        f = T.font(14, bold=True, mono=True)
        lw = d.textlength(label, font=f)
        d.text(((w - lw) / 2, y0 + (BANNER_H - 14) / 2 - 2), label,
               font=f, fill=col)

        # the one framed instrument: dock link pictogram + transfer gauge
        iy0, iy1 = INST_TOP, ANIM_Y + ANIM_H
        brackets(d, (12, iy0, w - 12, iy1), th.muted)
        cap = spaced("DOCK LINK")
        fc = T.font(9, bold=True, mono=True)
        d.text((w - 22 - d.textlength(cap, font=fc), iy0 + 6), cap,
               font=fc, fill=th.muted)
        cy = 156
        if state == lightdock.LightDockSync.COMPLETE:
            self._draw_hug(d, th, w, cy)
        elif state == lightdock.LightDockSync.INTERRUPTED:
            self._draw_broken(d, th, w, cy)
        elif state == lightdock.LightDockSync.SYNCING:
            self._draw_syncing(d, th, w, cy)
        else:                            # no Light / no session yet
            self._draw_devices(d, th, w * 0.24, w * 0.76, cy)
            self._center_text(d, w, iy1 - 16, spaced("AWAITING LIGHT"),
                              th.muted)

    def _slab(self, d, th, cx, cy, sw, sh, lit):
        """One device silhouette: hard-edged slab with a lit screen inside."""
        d.rectangle((cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2),
                    outline=th.fg, width=3)
        pad = max(6, sw // 6)
        d.rectangle((cx - sw / 2 + pad, cy - sh / 2 + pad,
                     cx + sw / 2 - pad, cy + sh / 2 - pad),
                    fill=th.accent if lit else th.card_hi)

    def _draw_devices(self, d, th, prime_cx, light_cx, cy, lit=False):
        """Prime's slab (left, larger) and Light's (right, smaller); returns
        the cable endpoints between their inner edges."""
        self._slab(d, th, prime_cx, cy, 48, 76, lit)
        self._slab(d, th, light_cx, cy, 36, 54, lit)
        return prime_cx + 24, light_cx - 18

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
            d.rectangle((px - 3, cy - 3, px + 3, cy + 3), fill=th.accent)
        # transfer gauge: bounded bytes fraction, segmented
        segs = 12
        seg_row(d, 22, INST_TOP + 8, round(frac * segs), segs,
                th.accent, th.card_hi, seg_h=10)
        d.text((22 + segs * 10 + 6, INST_TOP + 8), f"{int(frac * 100):3d}%",
               font=T.font(10, bold=True, mono=True), fill=th.muted)

    def _draw_hug(self, d, th, w, cy):
        # devices together, one steady registration ring — unambiguous at 3 m
        cx = w / 2
        self._draw_devices(d, th, cx - 27, cx + 23, cy, lit=True)
        d.rectangle((cx - 63, cy - 50, cx + 53, cy + 50),
                    outline=th.muted, width=2)
        status_square(d, (cx - 8, cy - 4, cx, cy + 4), "lit", th.ok)

    def _draw_broken(self, d, th, w, cy):
        # sad face + visibly broken cable — "come look", not an alarm
        x0, x1 = self._draw_devices(d, th, w * 0.22, w * 0.78, cy)
        mid = (x0 + x1) / 2
        d.line((x0, cy, mid - 16, cy), fill=th.muted, width=3)
        d.line((mid + 16, cy, x1, cy), fill=th.muted, width=3)
        for sx, sgn in ((mid - 16, 1), (mid + 16, -1)):     # frayed ends
            d.line((sx, cy, sx + 7 * sgn, cy - 7), fill=th.warn, width=2)
            d.line((sx, cy, sx + 7 * sgn, cy + 7), fill=th.warn, width=2)
        fy = cy - 44
        r = 20
        d.ellipse((mid - r, fy - r, mid + r, fy + r), outline=th.warn, width=3)
        for ex in (-8, 8):
            d.ellipse((mid + ex - 3, fy - 8, mid + ex + 3, fy - 2),
                      fill=th.warn)
        d.arc((mid - 11, fy + 4, mid + 11, fy + 22), 200, 340,
              fill=th.warn, width=3)

    def _center_text(self, d, w, y, text, col, size=10):
        f = T.font(size, bold=True, mono=True)
        d.text((w / 2 - d.textlength(text, font=f) / 2, y), text,
               font=f, fill=col)

    # ---- middle: the only two controls ----
    def _draw_controls(self, d, th, w):
        running = self._running()
        resync = Button((14, CTRL_Y, w // 2 - 4, CTRL_Y + CTRL_H),
                        "SYNCING" if running else "RE-SYNC",
                        kind="primary", font_size=16)
        resync.enabled = not running
        resync.draw(d, th)
        self._btns["resync"] = resync.box if resync.enabled else None

        pull = bool(self.app.config["lightdock_pull_logs"])
        tog = Button((w // 2 + 4, CTRL_Y, w - 14, CTRL_Y + CTRL_H),
                     "PULL LOGS %s" % ("ON" if pull else "OFF"),
                     kind="ghost", font_size=14)
        tog.draw(d, th)
        d.rectangle((w // 2 + 12, CTRL_Y + CTRL_H - 8, w - 22,
                     CTRL_Y + CTRL_H - 5), fill=th.ok if pull else th.muted)
        self._btns["pull"] = tog.box

    # ---- bottom pane: the session-only log ----
    def _draw_log(self, d, th, w, h):
        d.rectangle((8, LOG_TOP, w - 8, h - 6), fill=th.card,
                    outline=th.card_hi, width=1)
        d.text((16, LOG_TOP + 5), spaced("SESSION LOG"),
               font=T.font(9, bold=True, mono=True), fill=th.muted)
        events = list(self.engine.events) if self.engine else []
        if not events:
            d.text((16, LOG_TOP + 26),
                   "DOCK SCOTTINA LIGHT TO SYNC" if not self._running()
                   else spaced("OPENING SESSION"),
                   font=T.font(10, bold=True, mono=True), fill=th.muted)
            return
        top = LOG_TOP + 20
        rows = (h - 6 - top - 4) // ROW_H
        f_t = T.font(10, mono=True)
        f_m = T.font(11, mono=True)
        y = top
        for ts, text in events[-rows:]:              # newest at the bottom
            # square row status: lit = landed, slashed amber = interrupted
            mode, scol, col = "hollow", th.muted, th.fg
            if text.startswith("interrupted") or "FAILED" in text:
                mode, scol, col = "slash", th.warn, th.warn
            elif text.startswith("session complete"):
                mode, scol, col = "lit", th.ok, th.ok
            status_square(d, (16, y + 4, 24, y + 12), mode, scol, width=1)
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            d.text((30, y + 1), stamp, font=f_t, fill=th.muted)
            d.text((84, y), text[:33], font=f_m, fill=col)
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
