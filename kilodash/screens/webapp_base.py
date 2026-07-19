"""Base screen for the web-app launch terminal.

A WebAppScreen turns kilodash into a front panel for a bigger package that
serves its own browser UI (Kismet, Node-RED, AIS-catcher…). It gives you the
three things every such app needs, for free:

  1. **Auto-launch on open** — entering the screen starts the app (per spec).
  2. **Positive confirmation** — one compact card whose border only turns green
     once the app's port actually answers, plus the exact URL:port to open
     elsewhere.
  3. **A Start/Stop control** — tap the card (stop asks first) — and clean
     hooks for per-app controls + feedback.

Subclass and set `app_name`, `port`, and either `service` or `start_cmd`. Then
override the hooks (`draw_app`, `handle_app_tap`, `poll_app`, `build_start_cmd`)
for the app-specific panel. Tiles auto-hide until the app is installed.
"""

import time

from .. import theme as T, webapp
from ..widgets import rrect
from .base import Screen, HEADER_H

_STATE_STYLE = {
    webapp.UP:       ("ok",     "Running"),
    webapp.STARTING: ("accent", "Launching…"),
    webapp.ERROR:    ("bad",    "Problem"),
    webapp.STOPPED:  ("muted",  "Stopped"),
}

CARD_H = 58     # the whole shared header is this one card


class WebAppScreen(Screen):
    # --- subclasses configure these ---
    app_name = "App"
    port = 0
    service = None            # systemd unit, e.g. "nodered.service"
    start_cmd = None          # argv list (alternative to service)
    url_path = "/"
    autostart = True          # launch when the screen opens (the spec's ask)
    stop_on_leave = False     # keep serving so the web UI stays reachable
    tile_color_key = "accent"

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.web = webapp.WebApp(self.app_name, self.port, service=self.service,
                                 start_cmd=self.start_cmd, url_path=self.url_path)
        self._btns = {}
        self._avail = None
        self._avail_t = -1e9
        self._card_box = None
        self.confirm = False       # stop-app confirm dialog up?
        self._ok_box = self._cancel_box = None

    # ---- tile gating: only offer the app once it's installed ----
    def available(self):
        now = time.monotonic()
        if self._avail is None or now - self._avail_t > 5:
            self._avail = self.web.installed()
            self._avail_t = now
        return self._avail

    # ---- lifecycle ----
    def on_enter(self):
        if self.autostart and not self.web.running:
            self.web.launch(self.build_start_cmd())
        self.on_app_enter()

    def on_leave(self):
        if self.stop_on_leave and self.web.running:
            self.web.stop()
        self.on_app_leave()

    def tick(self):
        changed = self.web.poll()
        if self.poll_app():
            changed = True
        return changed

    # ---- rendering ----
    def app_top(self):
        """Y where the app-specific panel begins (below the status card)."""
        return HEADER_H + 8 + CARD_H + 10

    def draw_content(self, d, th):
        w = self.app.w
        self._btns = {}
        top = HEADER_H + 8

        # --- the one status card: border colour = app state, green only once
        #     the port truly answers; body is the URL to open elsewhere.
        #     Tap = stop (confirmed) / launch. ---
        colkey, head = _STATE_STYLE[self.web.state]
        col = getattr(th, colkey)
        self._card_box = (12, top, w - 12, top + CARD_H)
        rrect(d, self._card_box, 12, fill=th.card, outline=col, width=2)
        d.text((22, top + 8), f"{self.app_name} · {head}",
               font=T.font(13, bold=True), fill=col)
        hint = "tap: stop" if self.web.running else "tap: launch"
        fh = T.font(11)
        hw = d.textlength(hint, font=fh)
        d.text((w - 22 - hw, top + 10), hint, font=fh, fill=th.muted)
        if self.web.state in (webapp.UP, webapp.STARTING):
            d.text((22, top + 29), self.web.url(),
                   font=T.font(15, bold=True, mono=True),
                   fill=th.accent if self.web.state == webapp.UP else th.muted)
        elif self.web.state == webapp.ERROR:
            d.text((22, top + 31), self.web.message[:34],
                   font=T.font(12, mono=True), fill=th.muted)
        else:
            d.text((22, top + 31), f"port {self.web.port} · not serving",
                   font=T.font(13, mono=True), fill=th.muted)

        # --- app-specific panel ---
        self.draw_app(d, th, self.app_top())

        if self.confirm:
            self._draw_confirm(d, th)

    def _draw_confirm(self, d, th):
        w = self.app.w
        x0, y0, x1, y1 = 26, 170, w - 26, 300
        rrect(d, (x0 - 3, y0 - 3, x1 + 3, y1 + 3), 14, fill=th.bg)
        rrect(d, (x0, y0, x1, y1), 12, fill=th.card, outline=th.bad, width=2)
        f = T.font(20, bold=True)
        title = f"Stop {self.app_name}?"
        tw = d.textlength(title, font=f)
        d.text(((w - tw) / 2, y0 + 18), title, font=f, fill=th.fg)
        d.text((x0 + 22, y0 + 52), f"Web UI on :{self.web.port} goes down.",
               font=T.font(14), fill=th.muted)
        by = y1 - 48
        mid = w / 2
        self._cancel_box = (x0 + 14, by, mid - 6, by + 36)
        self._ok_box = (mid + 6, by, x1 - 14, by + 36)
        rrect(d, self._cancel_box, 10, fill=th.card_hi)
        rrect(d, self._ok_box, 10, fill=th.bad)
        fb = T.font(16, bold=True)
        for box, label, colr in ((self._cancel_box, "Cancel", th.fg),
                                 (self._ok_box, "Stop", th.bg)):
            bx0, by0, bx1, _ = box
            lw = d.textlength(label, font=fb)
            d.text(((bx0 + bx1 - lw) / 2, by0 + 9), label, font=fb, fill=colr)

    @staticmethod
    def _in(box, x, y):
        if not box:
            return False
        x0, y0, x1, y1 = box
        return x0 <= x <= x1 and y0 <= y <= y1

    def handle_tap(self, x, y):
        if self.confirm:
            if self._in(self._ok_box, x, y):
                self.web.stop()
            self.confirm = False       # any other tap dismisses
            return True
        if self._in(self._card_box, x, y):
            if self.web.running:
                self.confirm = True
            else:
                self.web.launch(self.build_start_cmd())
            return True
        return self.handle_app_tap(x, y)

    # ---- hooks for subclasses ----
    def build_start_cmd(self):
        """Return runtime-computed argv, or None to use the class start_cmd."""
        return None

    def on_app_enter(self):
        pass

    def on_app_leave(self):
        pass

    def poll_app(self):
        """Refresh app-specific feedback; return True to force a redraw."""
        return False

    def draw_app(self, d, th, top):
        """Draw the app-specific controls/feedback below the shared header."""

    def handle_app_tap(self, x, y):
        return False
