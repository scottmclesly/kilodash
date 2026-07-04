"""Base screen for the web-app launch terminal.

A WebAppScreen turns kilodash into a front panel for a bigger package that
serves its own browser UI (Kismet, Node-RED, AIS-catcher…). It gives you the
three things every such app needs, for free:

  1. **Auto-launch on open** — entering the screen starts the app (per spec).
  2. **Positive confirmation** — a status banner that only turns green once the
     app's port actually answers, plus the exact URL:port to open elsewhere.
  3. **A Start/Stop control** and clean hooks for per-app controls + feedback.

Subclass and set `app_name`, `port`, and either `service` or `start_cmd`. Then
override the hooks (`draw_app`, `handle_app_tap`, `poll_app`, `build_start_cmd`)
for the app-specific panel. Tiles auto-hide until the app is installed.
"""

import time

from .. import theme as T, webapp
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

_STATE_STYLE = {
    webapp.UP:       ("✓", "ok",     "Running"),
    webapp.STARTING: ("…", "accent", "Launching…"),
    webapp.ERROR:    ("!",      "bad",    "Problem"),
    webapp.STOPPED:  ("○", "muted",  "Stopped"),
}


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
        """Y where the app-specific panel begins (below banner/URL/controls)."""
        return HEADER_H + 8 + 66 + 8 + 56 + 8 + 42 + 10

    def draw_content(self, d, th):
        w = self.app.w
        self._btns = {}
        top = HEADER_H + 8

        # --- status banner: green only once the port truly answers ---
        icon, colkey, head = _STATE_STYLE[self.web.state]
        col = getattr(th, colkey)
        bh = 66
        rrect(d, (12, top, w - 12, top + bh), 12, fill=th.card)
        cy = top + bh / 2
        d.ellipse((26, cy - 18, 26 + 36, cy + 18), fill=col)
        f = T.font(26, bold=True)
        iw = d.textlength(icon, font=f)
        d.text((44 - iw / 2, cy - 17), icon, font=f, fill=th.ink)
        d.text((78, top + 12), f"{self.app_name} · {head}",
               font=T.font(17, bold=True), fill=th.fg)
        d.text((78, top + 38), self.web.message[:32], font=T.font(12),
               fill=th.muted)

        # --- URL / port card ---
        y = top + bh + 8
        uh = 56
        rrect(d, (12, y, w - 12, y + uh), 12, fill=th.card)
        d.text((22, y + 8), "WEB UI", font=T.font(11, bold=True), fill=th.muted)
        if self.web.state in (webapp.UP, webapp.STARTING):
            d.text((22, y + 24), self.web.url(),
                   font=T.font(15, bold=True, mono=True),
                   fill=th.accent if self.web.state == webapp.UP else th.muted)
        else:
            d.text((22, y + 26), f"port {self.web.port} · not serving",
                   font=T.font(13, mono=True), fill=th.muted)

        # --- Start / Stop ---
        y += uh + 8
        running = self.web.running
        b = Button((12, y, w - 12, y + 42),
                   "Stop app" if running else f"Launch {self.app_name}",
                   kind="danger" if running else "primary", font_size=17)
        b.draw(d, th)
        self._btns["power"] = b

        # --- app-specific panel ---
        self.draw_app(d, th, y + 52)

    def handle_tap(self, x, y):
        b = self._btns.get("power")
        if b and b.hit(x, y):
            if self.web.running:
                self.web.stop()
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
