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
from ..widgets import confirm_dialog, hazard, spaced, state_glyph
from .base import Screen, HEADER_H

# Semiotic-Standard state banner styling (kin to Tables' converter banner):
# hazard end-caps on FAULT only; stopping is a stand-down, never red.
_STATES = {
    webapp.UP:       {"label": "UP",          "col": "ok",     "glyph": "up"},
    webapp.STARTING: {"label": "SPINNING UP", "col": "accent", "glyph": "spin"},
    webapp.ERROR:    {"label": "FAULT",       "col": "bad",    "glyph": "fault"},
    webapp.STOPPED:  {"label": "STANDING BY", "col": "muted",  "glyph": "standby"},
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


    def model_rows(self):
        """Service rows shared by every web-app screen (WEB-PROTOCOL §4.6).

        Reads only cached `self.web` attributes. Deliberately does NOT call
        web.url() (composes via lan_ip(), which opens a socket), web.running
        (probes), or available()/installed() (subprocess) — model_rows must
        stay cheap and side-effect free."""
        w = self.web
        st = getattr(w, "state", "?")
        rows = [
            {"label": "SERVICE", "value": str(st).upper(),
             "state": {"up": "ok", "starting": "caution",
                       "error": "fault"}.get(st)},
            {"label": "PORT", "value": str(getattr(w, "port", "—")),
             "state": None},
        ]
        if getattr(w, "service", None):
            rows.append({"label": "UNIT", "value": str(w.service),
                         "state": None})
        msg = getattr(w, "message", "")
        if msg:
            rows.append({"label": "LAST", "value": str(msg), "state": None})
        return rows


    def model_buttons(self):
        """Generic service controls. `stop` is confirm-guarded because the
        panel guards it with a modal dialog — the web must not be the weaker
        path. Gates read cached `self.web.state`, never web.running (probes)
        or available() (subprocess)."""
        up = getattr(self.web, "state", None) == webapp.UP
        starting = getattr(self.web, "state", None) == webapp.STARTING
        return [
            {"id": "start", "label": "START", "enabled": not (up or starting),
             "confirm": False},
            {"id": "stop", "label": "STOP", "enabled": bool(up or starting),
             "confirm": True},
        ]

    def handle_button(self, bid):
        if bid == "start":
            self.web.launch(self.build_start_cmd())
            return True
        if bid == "stop":
            self.web.stop()
            return True
        return False

    def draw_content(self, d, th):
        w = self.app.w
        self._btns = {}
        top = HEADER_H + 8

        # --- the one state banner: border + glyph = app state, green only
        #     once the port truly answers; line two is the uplink URL.
        #     Tap = stop (confirmed) / launch. ---
        st = _STATES[self.web.state]
        col = getattr(th, st["col"])
        self._card_box = (12, top, w - 12, top + CARD_H)
        d.rectangle(self._card_box, fill=th.card, outline=col, width=2)
        fault = self.web.state == webapp.ERROR
        if fault:                        # faults wear the caution end-caps
            hazard(d, (w - 12 - 46, top + 4, w - 16, top + CARD_H - 4), col)
        state_glyph(d, st["glyph"], 34, top + CARD_H // 2, 11, col)
        f = T.font(15, bold=True, mono=True)
        lw = d.textlength(st["label"], font=f)
        d.text(((w - lw) / 2, top + 8), st["label"], font=f, fill=col)
        if not fault:
            hint = "TAP: STOP" if self.web.running else "TAP: LAUNCH"
            fh = T.font(9, bold=True, mono=True)
            hw = d.textlength(hint, font=fh)
            d.text((w - 22 - hw, top + 11), hint, font=fh, fill=th.muted)
        if self.web.state in (webapp.UP, webapp.STARTING):
            url = self.web.url()
            fu = T.font(14, bold=True, mono=True)
            uw = d.textlength(url, font=fu)
            d.text(((w - uw) / 2, top + 31), url, font=fu,
                   fill=th.accent if self.web.state == webapp.UP else th.muted)
        elif fault:
            d.text((52, top + 31), self.web.message[:26],
                   font=T.font(11, mono=True), fill=th.fg)
        else:
            lab = f"PORT {self.web.port} · " + spaced("NO UPLINK")
            fu = T.font(11, bold=True, mono=True)
            uw = d.textlength(lab, font=fu)
            d.text(((w - uw) / 2, top + 32), lab, font=fu, fill=th.muted)

        # --- app-specific panel ---
        self.draw_app(d, th, self.app_top())

        if self.confirm:
            self._draw_confirm(d, th)

    def _draw_confirm(self, d, th):
        # stand-down order: caution amber, not red — red is for faults
        self._cancel_box, self._ok_box = confirm_dialog(
            d, th, self.app.w, spaced("STAND DOWN?"),
            (f"{self.app_name.upper()} WEB UI ON :{self.web.port}",
             "GOES DARK"),
            (("RESUME", None), ("STOP", th.warn)))

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
