"""Micro KVM tile — indicator + session log, no logic (MicroKVM Phase 4).

Renders the command plane's state from microkvm.service.Runtime and nothing
else: ARMED/DORMANT across-the-room banner, BLE link state, last-heard node,
and the bounded session log. The executor, arm gate, and link live in
microkvm/ — this screen is the mirror, matching the Tables-tile "remote
control, not the engine" split. Always visible (software feature, no
hardware gate); its live meaning comes from arm state.
"""

import time

from .. import theme as T
from ..widgets import rrect
from .base import Screen

LINK_COLORS = {"up": "ok", "connecting": "warn", "down": "bad"}


class MicroKvmScreen(Screen):
    title = "Micro KVM"
    glyph = "microkvm"

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
        y = 50

        if not rt or not rt.enabled:
            rrect(d, (14, y, w - 14, y + 74), 10, fill=th.card)
            d.text((26, y + 10), "Command plane disabled",
                   font=T.font(17, bold=True), fill=th.fg)
            d.text((26, y + 38), "config.json: microkvm.enabled + home_host",
                   font=T.font(13, mono=True), fill=th.muted)
            return

        # ---- across-the-room banner: ARMED (off-grid) / DORMANT (home) ----
        armed, reason = rt.gate.state()
        color = th.warn if armed else th.ok
        rrect(d, (14, y, w - 14, y + 74), 10, fill=th.card)
        d.rectangle((14, y, 20, y + 74), fill=color)
        d.text((32, y + 8), "ARMED (off-grid)" if armed else "DORMANT (home)",
               font=T.font(24, bold=True), fill=color)
        d.text((32, y + 44), reason[:44], font=T.font(14), fill=th.muted)
        y += 82

        # ---- BLE link card ----
        rrect(d, (14, y, w - 14, y + 58), 10, fill=th.card)
        lcol = getattr(th, LINK_COLORS.get(rt.link.state, "bad"))
        d.text((26, y + 8), "BLE link", font=T.font(14), fill=th.muted)
        f = T.font(18, bold=True)
        tw = d.textlength(rt.link.state.upper(), font=f)
        d.text((w - 26 - tw, y + 6), rt.link.state.upper(), font=f, fill=lcol)
        heard = rt.link.last_heard or "-"
        d.text((26, y + 32), f"last heard {heard}   dropped {rt.link.dropped}",
               font=T.font(13, mono=True), fill=th.muted)
        y += 66

        # ---- bounded session log (ring, newest first) ----
        d.text((18, y), "Session log", font=T.font(14), fill=th.muted)
        y += 22
        fit = max(0, (self.app.h - y - 8) // 34)   # newest entries that fit
        entries = list(rt.executor.log)[::-1][:fit]
        if not entries:
            d.text((26, y), "no commands received",
                   font=T.font(13, mono=True), fill=th.muted)
            y += 20
        for e in entries:
            hh = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            col = th.ok if e["ok"] else th.bad
            d.text((18, y), hh, font=T.font(12, mono=True), fill=th.muted)
            d.text((88, y), f"{e['sender'][:10]} {e['line'][:24]}",
                   font=T.font(12, mono=True), fill=th.fg)
            d.text((88, y + 15), e["reply"][:38],
                   font=T.font(12, mono=True), fill=col)
            y += 34
