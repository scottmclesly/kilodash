"""Signal K launch panel — the boat's data hub as a helm glance.

Opening the screen attaches to Signal K (adopts it if it autostarted at boot,
per the framework's probe-first launch) and confirms its web UI on :3000. Signal
K is the vessel data hub, so this screen **never stops it on leave**.

The panel pages through vitals groups (tap to cycle) — Nav, Engine, Environment,
Power — with a persistent **heartbeat** line: freshness dot + distinct source
count, e.g. "● 3 feeds · 1.2s ago". That one glance tells you the NMEA2000 → SK
bridge is actually flowing, which is the whole point of a helm check.

Signal K speaks SI base units over `/signalk/v1/api/vessels/self`: m/s, radians,
Kelvin, Pascals, and revolutions in **Hz**. We convert on display (knots,
degrees, °C, bar, RPM). Reads use the token in `config.signalk_token` if SK
security is enabled (blank = open, the default).
"""

import time
from datetime import datetime

from .. import theme as T, webapp
from ..widgets import rrect
from .webapp_base import WebAppScreen

SELF_URL = "http://127.0.0.1:3000/signalk/v1/api/vessels/self"

FETCH_FAST = 0.25       # REST poll while data is flowing (localhost, cheap)
FETCH_SLOW = 1.5        # guardrail: SK down or nothing flowing → back off
HB_REDRAW = 0.1         # heartbeat "x.xs ago" repaint cadence between fetches

# --- SI base -> display conversions (getting these wrong is the classic SK bug)
KN = 1.943844          # m/s   -> knots
DEG = 57.2957795       # rad   -> degrees


def _ms_kn(v):      return v * KN
def _rad_hdg(v):    return (v * DEG) % 360          # heading/COG: 0..360
def _rad_rel(v):    return v * DEG                  # wind angle: signed -180..180
def _rads_degs(v):  return v * DEG                  # rate of turn: °/s
def _k_c(v):        return v - 273.15               # Kelvin -> °C
def _pa_bar(v):     return v / 100000.0             # Pascal -> bar
def _pa_mbar(v):    return v / 100.0                # Pascal -> mbar (baro)
def _hz_rpm(v):     return v * 60.0                 # Hz -> RPM (SK stores rev/s)
def _ratio_pct(v):  return v * 100.0                # 0..1 -> %
def _sec_hr(v):     return v / 3600.0               # seconds -> hours
def _m3s_lph(v):    return v * 3.6e6                # m³/s -> L/h (fuel rate)
def _id(v):         return v


# (label, dotted path (* = first instance), converter, unit, decimals)
# 6 metrics per page = the 3x2 grid the compact header pays for.
PAGES = [
    ("NAV", [
        ("SOG", "navigation.speedOverGround", _ms_kn, "kn", 1),
        ("COG", "navigation.courseOverGroundTrue", _rad_hdg, "°", 0),
        ("HDG", "navigation.headingTrue", _rad_hdg, "°", 0),
        ("STW", "navigation.speedThroughWater", _ms_kn, "kn", 1),
        ("ROT", "navigation.rateOfTurn", _rads_degs, "°/s", 1),
        ("FIX", "navigation.position", None, "", 0),        # special-cased
    ]),
    ("ENGINE", [
        ("RPM", "propulsion.*.revolutions", _hz_rpm, "rpm", 0),
        ("TEMP", "propulsion.*.temperature", _k_c, "°C", 0),
        ("OIL", "propulsion.*.oilPressure", _pa_bar, "bar", 1),
        ("COOL", "propulsion.*.coolantTemperature", _k_c, "°C", 0),
        ("FUEL", "propulsion.*.fuel.rate", _m3s_lph, "L/h", 1),
        ("HOURS", "propulsion.*.runTime", _sec_hr, "h", 0),
    ]),
    ("ENVIRON", [
        ("DEPTH", "environment.depth.belowTransducer", _id, "m", 1),
        ("AWS", "environment.wind.speedApparent", _ms_kn, "kn", 1),
        ("AWA", "environment.wind.angleApparent", _rad_rel, "°", 0),
        ("WATER", "environment.water.temperature", _k_c, "°C", 1),
        ("AIR", "environment.outside.temperature", _k_c, "°C", 1),
        ("BARO", "environment.outside.pressure", _pa_mbar, "mbar", 0),
    ]),
    ("POWER", [
        ("VOLTS", "electrical.batteries.*.voltage", _id, "V", 1),
        ("SOC", "electrical.batteries.*.stateOfCharge", _ratio_pct, "%", 0),
        ("CURR", "electrical.batteries.*.current", _id, "A", 1),
        ("BTEMP", "electrical.batteries.*.temperature", _k_c, "°C", 0),
        ("TTG", "electrical.batteries.*.capacity.timeRemaining", _sec_hr, "h", 1),
        ("SOLAR", "electrical.solar.*.panelPower", _id, "W", 0),
    ]),
]


def _leaf(tree, path):
    """Walk a dotted SK path; '*' picks the first instance. Return the leaf
    dict (with value/timestamp/$source) or None."""
    node = tree or {}
    for seg in path.split("."):
        if not isinstance(node, dict):
            return None
        if seg == "*":
            node = next((v for _, v in sorted(node.items())
                         if isinstance(v, dict)), None)
        else:
            node = node.get(seg)
        if node is None:
            return None
    return node if isinstance(node, dict) else None


def _age(ts):
    """Seconds since an SK ISO8601 timestamp, or None if unparseable."""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return max(0.0, time.time() - t.timestamp())
    except (ValueError, AttributeError):
        return None


class SignalKScreen(WebAppScreen):
    title = "Signal K"
    glyph = "signalk"
    tile_color_key = "ok"
    app_name = "Signal K"
    port = 3000
    service = "signalk.service"
    url_path = "/"
    stop_on_leave = False               # boat data hub — never kill on swipe-away
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.model = {}
        self.page = 0
        # The tick is the *paint* rate (dirty-rect bands make it affordable);
        # the REST snapshot poll below is the *data* rate.
        self.tick_interval = 0.05
        self._fetch_iv = FETCH_SLOW
        self._last_fetch = -1e9
        self._last_hb = -1e9
        self._panel_box = None    # vitals grid + heartbeat, below the controls
        self._hb_box = None       # heartbeat bar band alone

    def poll_app(self):
        if self.web.state != webapp.UP:
            return False
        token = self.app.config["signalk_token"] or None
        data = webapp.http_json(SELF_URL, timeout=0.8, token=token)
        if isinstance(data, dict):
            self.model = data
            return True
        return False

    def _min_age(self):
        """Age of the freshest value across all pages, or None if no data."""
        best = None
        for _, metrics in PAGES:
            for _, path, _conv, _unit, _dec in metrics:
                leaf = _leaf(self.model, path)
                a = _age(leaf.get("timestamp")) if leaf else None
                if a is not None and (best is None or a < best):
                    best = a
        return best

    def tick(self):
        if self.web.poll():
            # banner/state changed — repaint everything, and pick the tick
            # cadence for the new state (guardrail: idle fast tick when down)
            self.tick_interval = 0.05 if self.web.state == webapp.UP else 0.5
            return True
        if self.web.state != webapp.UP:
            self.tick_interval = 0.5
            return False
        self.tick_interval = 0.05
        now = time.monotonic()
        if now - self._last_fetch >= self._fetch_iv:
            self._last_fetch = now
            got = self.poll_app()
            age = self._min_age()
            self._fetch_iv = (FETCH_FAST if got and age is not None and age < 15
                              else FETCH_SLOW)
            if got:
                self._last_hb = now
                if self._panel_box:
                    self.report_dirty(self._panel_box)
                return True
        # between fetches, keep only the heartbeat age readout live
        if now - self._last_hb >= HB_REDRAW and self._hb_box:
            self._last_hb = now
            self.report_dirty(self._hb_box)
            return True
        return False

    # ---- value formatting ----
    def _display(self, path, conv, unit, dec):
        leaf = _leaf(self.model, path)
        if path == "navigation.position":
            if leaf and isinstance(leaf.get("value"), dict):
                fresh = (_age(leaf.get("timestamp")) or 1e9) < 15
                return ("OK" if fresh else "OLD", "gps fix")
            return ("—", "no fix")
        if not leaf or leaf.get("value") is None:
            return ("—", unit)
        try:
            v = conv(leaf["value"]) if conv else leaf["value"]
            s = f"{v:.{dec}f}" if isinstance(v, (int, float)) else str(v)
        except (TypeError, ValueError):
            return ("—", unit)
        return (s[:6], unit)

    # ---- rendering ----
    def draw_app(self, d, th, top):
        w = self.app.w
        panel_top = top
        if self.web.state != webapp.UP:
            d.text((16, top + 20), "Waiting for Signal K…",
                   font=T.font(14), fill=th.muted)
            return

        page = self.page % len(PAGES)
        group, metrics = PAGES[page]
        d.text((14, top), group, font=T.font(14, bold=True), fill=th.accent)
        # page dots
        dx = w - 12 - len(PAGES) * 14
        for i in range(len(PAGES)):
            cx = dx + i * 14
            d.ellipse((cx, top + 4, cx + 7, top + 11),
                      fill=th.accent if i == page else th.card_hi)
        top += 24

        # heartbeat stays pinned to the bottom edge; the 3x2 vitals grid
        # stretches to fill whatever sits between it and the page label
        hb_y = self.app.h - 36
        gap = 8
        rows = 3
        cw = (w - 24 - gap) / 2
        ch = (hb_y - 8 - top - (rows - 1) * gap) // rows
        for i, (label, path, conv, unit, dec) in enumerate(metrics):
            r, c = divmod(i, 2)
            x0 = 12 + c * (cw + gap)
            y0 = top + r * (ch + gap)
            rrect(d, (x0, y0, x0 + cw, y0 + ch), 10, fill=th.card)
            d.text((x0 + 10, y0 + 8), label, font=T.font(11, bold=True),
                   fill=th.muted)
            val, unit2 = self._display(path, conv, unit, dec)
            d.text((x0 + 10, y0 + 25), val,
                   font=T.font(26, bold=True, mono=True), fill=th.fg)
            d.text((x0 + 10, y0 + ch - 18), unit2, font=T.font(11),
                   fill=th.muted)

        self._draw_heartbeat(d, th, hb_y, w)
        # row bands for the partial-blit path (full width, small padding)
        self._hb_box = (0, hb_y - 2, w, hb_y + 28)
        self._panel_box = (0, panel_top, w, hb_y + 28)

    def _draw_heartbeat(self, d, th, y, w):
        ages, srcs = [], set()
        for _, metrics in PAGES:
            for (_, path, _c, _u, _dp) in metrics:
                leaf = _leaf(self.model, path)
                if not leaf:
                    continue
                a = _age(leaf.get("timestamp"))
                if a is not None:
                    ages.append(a)
                if leaf.get("$source"):
                    srcs.add(leaf["$source"])
        rrect(d, (12, y, w - 12, y + 26), 7, fill=th.card)
        if ages:
            fresh = min(ages)
            col = th.ok if fresh < 3 else th.warn if fresh < 15 else th.bad
            txt = f"{len(srcs)} feed{'s' if len(srcs) != 1 else ''} · {fresh:.1f}s ago"
        else:
            col, txt = th.bad, "no data flowing"
        d.ellipse((22, y + 8, 33, y + 19), fill=col)
        d.text((44, y + 6), txt, font=T.font(13, bold=True), fill=th.fg)

    def handle_app_tap(self, x, y):
        self.page = (self.page + 1) % len(PAGES)
        return True
