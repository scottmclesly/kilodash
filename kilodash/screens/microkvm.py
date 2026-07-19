"""Micro KVM tile — indicator + session log, no logic (MicroKVM Phase 4).

Renders the command plane's state from microkvm.service.Runtime and nothing
else: ARMED/DORMANT across-the-room banner, BLE link state, last-heard node,
and the bounded session log. The executor, arm gate, and link live in
microkvm/ — this screen is the mirror, matching the Tables-tile "remote
control, not the engine" split. Always visible (software feature, no
hardware gate); its live meaning comes from arm state.

Presentation follows the ship-instrument look ratified on the Pomodoro
refactor (Cobb's Semiotic Standard): a hard-edged gate banner with a
per-state glyph — ARMED is the plane up (lit core, green), DORMANT an
amber stand-by (hollow ring + level bar; no hazard caps — those are for
faults only) — a spaced-caps radio/link readout grid, and hard-edged
session-log rows keyed by a square status glyph (lit green = executed,
lit red = failed: an actual fault report).
"""

import time

from PIL import Image, ImageDraw

from .. import theme as T
from ..widgets import spaced, state_glyph, status_square
from .base import Screen, HEADER_H

LINK_COLORS = {"up": "ok", "connecting": "warn", "down": "bad"}

# Fixed bands above the scrolling log (house layout: 12px margins, 8px gap).
STATUS_Y = HEADER_H + 8
STATUS_H = 64
FIELD_H = 46
FIELD_GAP = 8
FIELDS_Y = STATUS_Y + STATUS_H + 10
LOG_LABEL_Y = FIELDS_Y + 2 * FIELD_H + FIELD_GAP + 10
LOG_TOP = LOG_LABEL_Y + 18

# Session-log entry rows.
CARD_MARGIN = 12
CARD_H = 62
CARD_GAP = 8


class MicroKvmScreen(Screen):
    title = "Micro KVM"
    tile_id = "micro-kvm"
    glyph = "microkvm"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self._seen = None

    @property
    def rt(self):
        return getattr(self.app, "microkvm", None)

    def available(self):
        """Launcher philosophy: only offer tiles Scottina can work with.
        The gate is CONFIGURED (microkvm.enabled), not link-up: an enabled
        plane with a dead BLE link must stay visible — DOWN on this tile is
        an alarm to act on, not a device absence to hide."""
        rt = self.rt
        return bool(rt and rt.enabled)

    def content_area(self):
        return (0, LOG_TOP, self.app.w, self.app.h - LOG_TOP)

    def tick(self):
        rt = self.rt
        if not rt:
            return False
        state = (rt.gate.armed, rt.gate.reason, rt.link.state, rt.link.detail,
                 rt.link.last_heard, rt.link.dropped, len(rt.executor.log),
                 rt.executor.log[-1]["ts"] if rt.executor.log else 0)
        if state == self._seen:
            return False
        self._seen = state
        return True


    def model_rows(self):
        """Command-plane state. Reads gate.state() (a plain accessor) and the
        link's cached fields — never gate.poll(), which probes SSID and host."""
        rt = self.rt
        if rt is None:
            return [{"label": "PLANE", "value": "NOT WIRED", "state": "caution"}]
        rows = [{"label": "PLANE",
                 "value": "ENABLED" if rt.enabled else "DISABLED",
                 "state": "ok" if rt.enabled else "caution"}]
        try:
            armed, reason = rt.gate.state()
            rows.append({"label": "ARM", "value": "ARMED" if armed else "DORMANT",
                         "state": "ok" if armed else "caution"})
            if reason:
                rows.append({"label": "REASON", "value": str(reason),
                             "state": None})
        except Exception:                       # noqa: BLE001
            pass
        link = getattr(rt, "link", None)
        if link is not None:
            st = getattr(link, "state", "?")
            rows.append({"label": "LINK", "value": str(st).upper(),
                         "state": {"up": "ok", "connecting": "caution",
                                   "down": "fault"}.get(st)})
            if getattr(link, "detail", ""):
                rows.append({"label": "DETAIL", "value": str(link.detail),
                             "state": None})
            if getattr(link, "last_heard", ""):
                rows.append({"label": "LAST HEARD", "value": str(link.last_heard),
                             "state": None})
            if getattr(link, "dropped", 0):
                rows.append({"label": "DROPPED", "value": str(link.dropped),
                             "state": "caution"})
        ex = getattr(rt, "executor", None)
        log = getattr(ex, "log", None) if ex is not None else None
        if log:
            last = log[-1]
            rows.append({"label": "COMMANDS", "value": str(len(log)),
                         "state": None})
            rows.append({"label": "LAST CMD", "value": str(last.get("line", "—")),
                         "state": None if last.get("ok") else "fault"})
        return rows

    def draw_content(self, d, th):
        w = self.app.w
        rt = self.rt

        if not rt or not rt.enabled:
            d.rectangle((12, STATUS_Y, w - 12, STATUS_Y + STATUS_H),
                        fill=th.card, outline=th.muted, width=2)
            state_glyph(d, "standby", 34, STATUS_Y + STATUS_H // 2, 12,
                        th.muted)
            d.text((54, STATUS_Y + 12), "COMMAND PLANE DISABLED",
                   font=T.font(12, bold=True, mono=True), fill=th.fg)
            d.text((54, STATUS_Y + 32), "config.json: microkvm.enabled",
                   font=T.font(T.SUB, mono=True), fill=th.muted)
            d.text((54, STATUS_Y + 46), "+ home_host",
                   font=T.font(T.SUB, mono=True), fill=th.muted)
            return

        self._draw_log(d, th, w, rt)

        # fixed bands drawn over the (cleared) upper area
        d.rectangle((0, HEADER_H, w, LOG_TOP), fill=th.bg)

        # ---- across-the-room gate banner: ARMED = the plane up (lit
        #      core), DORMANT = amber stand-by. Hazard caps are for
        #      faults only, so the banner never wears them. ----
        armed, reason = rt.gate.state()
        col = th.ok if armed else th.warn
        y0, y1 = STATUS_Y, STATUS_Y + STATUS_H
        d.rectangle((12, y0, w - 12, y1), fill=th.card, outline=col, width=2)
        state_glyph(d, "up" if armed else "standby", 34, (y0 + y1) // 2, 12,
                    col)
        label = "ARMED · OFF-GRID" if armed else "DORMANT · HOME"
        f = T.font(17, bold=True, mono=True)
        lw = d.textlength(label, font=f)
        d.text((max(54, (w - lw) / 2), y0 + 10), label, font=f, fill=col)
        sub = reason[:38].upper()
        fs = T.font(T.SUB, mono=True)
        sw = d.textlength(sub, font=fs)
        d.text((max(54, (w - sw) / 2), y0 + 38), sub, font=fs, fill=th.muted)

        # ---- BLE link readout grid (2x2, spaced-caps labels) ----
        lcol = getattr(th, LINK_COLORS.get(rt.link.state, "bad"))
        age = rt.link.rx_age()
        fields = (
            ("BLE LINK", rt.link.state.upper(), lcol),
            ("LAST HEARD", rt.link.last_heard[:10] or "—", th.accent),
            ("RX AGE", f"{int(age)}s" if age is not None else "—", th.accent),
            ("DROPPED", str(rt.link.dropped),
             th.warn if rt.link.dropped else th.fg),
        )
        cw = (w - 12 * 2 - FIELD_GAP) / 2
        for i, (label, value, vcol) in enumerate(fields):
            r, c = divmod(i, 2)
            x0 = 12 + c * (cw + FIELD_GAP)
            y0 = FIELDS_Y + r * (FIELD_H + FIELD_GAP)
            d.rectangle((x0, y0, x0 + cw, y0 + FIELD_H), fill=th.card,
                        outline=th.card_hi, width=1)
            d.text((x0 + 10, y0 + 6), spaced(label),
                   font=T.font(9, bold=True, mono=True), fill=th.muted)
            d.text((x0 + 10, y0 + 21), value,
                   font=T.font(18, bold=True, mono=True), fill=vcol)

        d.text((14, LOG_LABEL_Y), spaced("SESSION LOG"),
               font=T.font(10, bold=True, mono=True), fill=th.muted)

    # ---- bounded session log (ring, newest first) as scrollable rows ----
    def _draw_log(self, d, th, w, rt):
        view_h = self.app.h - LOG_TOP
        entries = list(rt.executor.log)[::-1]
        if not entries:
            self.content_h = view_h
            d.rectangle((0, LOG_TOP, w, self.app.h), fill=th.bg)
            d.text((22, LOG_TOP + 10), spaced("NO COMMANDS RECEIVED"),
                   font=T.font(11, bold=True, mono=True), fill=th.muted)
            return

        self.content_h = max(CARD_GAP + len(entries) * (CARD_H + CARD_GAP),
                             view_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, e in enumerate(entries):
            y0 = CARD_GAP + i * (CARD_H + CARD_GAP)
            col = th.ok if e["ok"] else th.bad     # failed = fault: red earned
            sd.rectangle((CARD_MARGIN, y0, w - CARD_MARGIN, y0 + CARD_H),
                         fill=th.card, outline=th.card_hi, width=1)
            status_square(sd, (CARD_MARGIN + 10, y0 + 8, CARD_MARGIN + 22,
                               y0 + 20), "lit", col)
            # line 1: sender (accent) left · time (muted) right
            sd.text((CARD_MARGIN + 30, y0 + 7), e["sender"][:12].upper(),
                    font=T.font(13, bold=True, mono=True), fill=th.accent)
            hh = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            hf = T.font(12, mono=True)
            hw = sd.textlength(hh, font=hf)
            sd.text((w - CARD_MARGIN - 12 - hw, y0 + 8), hh, font=hf,
                    fill=th.muted)
            # line 2: command · line 3: reply in ok/fault colour
            sd.text((CARD_MARGIN + 30, y0 + 26), e["line"][:32],
                    font=T.font(12, mono=True), fill=th.fg)
            sd.text((CARD_MARGIN + 30, y0 + 43), e["reply"][:32],
                    font=T.font(12, mono=True), fill=col)
        self.paste_list(LOG_TOP, view_h, surf)
