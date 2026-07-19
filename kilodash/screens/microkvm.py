"""Micro KVM tile — indicator + session log, no logic (MicroKVM Phase 4).

Renders the command plane's state from microkvm.service.Runtime and nothing
else: ARMED/DORMANT across-the-room banner, BLE link state, last-heard node,
and the bounded session log. The executor, arm gate, and link live in
microkvm/ — this screen is the mirror, matching the Tables-tile "remote
control, not the engine" split. Always visible (software feature, no
hardware gate); its live meaning comes from arm state.

Layout follows the house style: one compact status card whose border colour
is the state (LAN/web-app pattern), a 2-per-row label/value field grid
(Node-RED feedback pattern), and the session log as scrollable cards whose
frame colour encodes ok/fail (Discover-card pattern).
"""

import time

from PIL import Image, ImageDraw

from .. import theme as T
from ..widgets import rrect
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

# Session-log entry cards.
CARD_MARGIN = 12
CARD_H = 62
CARD_GAP = 8


class MicroKvmScreen(Screen):
    title = "Micro KVM"
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

    def draw_content(self, d, th):
        w = self.app.w
        rt = self.rt

        if not rt or not rt.enabled:
            rrect(d, (12, STATUS_Y, w - 12, STATUS_Y + STATUS_H), 12,
                  fill=th.card, outline=th.muted, width=2)
            d.text((22, STATUS_Y + 10), "Command plane disabled",
                   font=T.font(17, bold=True), fill=th.fg)
            d.text((22, STATUS_Y + 38), "config.json: microkvm.enabled + home_host",
                   font=T.font(12, mono=True), fill=th.muted)
            return

        self._draw_log(d, th, w, rt)

        # fixed bands drawn over the (cleared) upper area, LAN-style
        d.rectangle((0, HEADER_H, w, LOG_TOP), fill=th.bg)

        # ---- across-the-room banner: ARMED (off-grid) / DORMANT (home).
        #      One compact card, border colour = state (web-app pattern). ----
        armed, reason = rt.gate.state()
        color = th.warn if armed else th.ok
        rrect(d, (12, STATUS_Y, w - 12, STATUS_Y + STATUS_H), 12,
              fill=th.card, outline=color, width=2)
        d.text((22, STATUS_Y + 6), "ARMED (off-grid)" if armed else "DORMANT (home)",
               font=T.font(22, bold=True), fill=color)
        d.text((22, STATUS_Y + 38), reason[:36],
               font=T.font(12, mono=True), fill=th.muted)

        # ---- BLE link field grid (2x2, Node-RED feedback pattern) ----
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
            rrect(d, (x0, y0, x0 + cw, y0 + FIELD_H), 9, fill=th.card)
            d.text((x0 + 10, y0 + 6), label, font=T.font(11), fill=th.muted)
            d.text((x0 + 10, y0 + 21), value,
                   font=T.font(18, bold=True, mono=True), fill=vcol)

        d.text((14, LOG_LABEL_Y), "SESSION LOG", font=T.font(11, bold=True),
               fill=th.muted)

    # ---- bounded session log (ring, newest first) as scrollable cards ----
    def _draw_log(self, d, th, w, rt):
        view_h = self.app.h - LOG_TOP
        entries = list(rt.executor.log)[::-1]
        if not entries:
            self.content_h = view_h
            d.rectangle((0, LOG_TOP, w, self.app.h), fill=th.bg)
            d.text((22, LOG_TOP + 10), "no commands received",
                   font=T.font(13, mono=True), fill=th.muted)
            return

        self.content_h = max(CARD_GAP + len(entries) * (CARD_H + CARD_GAP),
                             view_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, e in enumerate(entries):
            y0 = CARD_GAP + i * (CARD_H + CARD_GAP)
            frame = th.ok if e["ok"] else th.bad
            rrect(sd, (CARD_MARGIN, y0, w - CARD_MARGIN, y0 + CARD_H), 10,
                  fill=th.card, outline=frame, width=2)
            # line 1: sender (accent) left · time (muted) right
            sd.text((CARD_MARGIN + 12, y0 + 7), e["sender"][:14],
                    font=T.font(13, bold=True, mono=True), fill=th.accent)
            hh = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            hf = T.font(12, mono=True)
            hw = sd.textlength(hh, font=hf)
            sd.text((w - CARD_MARGIN - 12 - hw, y0 + 8), hh, font=hf,
                    fill=th.muted)
            # line 2: command · line 3: reply in ok/fail colour
            sd.text((CARD_MARGIN + 12, y0 + 26), e["line"][:34],
                    font=T.font(12, mono=True), fill=th.fg)
            sd.text((CARD_MARGIN + 12, y0 + 43), e["reply"][:34],
                    font=T.font(12, mono=True), fill=frame)
        self.paste_list(LOG_TOP, view_h, surf)
