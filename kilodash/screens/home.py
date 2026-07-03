"""Home / overview: hostname, clock, primary IP, quick Wi-Fi toggle."""

import socket
import time

from .. import system, theme as T
from ..widgets import Button, rrect
from .base import Screen


class HomeScreen(Screen):
    title = "kilodash"
    icon = ""

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 2.0
        self.host = socket.gethostname()
        self.ifaces = []
        self.wifi = False
        self.btn = None
        self.tick()

    def tick(self):
        self.ifaces = system.get_interfaces()
        self.wifi = system.wifi_enabled()
        return True

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        # big clock
        clk = time.strftime("%H:%M")
        f = T.font(72, bold=True)
        tw = d.textlength(clk, font=f)
        d.text((w / 2 - tw / 2, 60), clk, font=f, fill=th.fg)
        date = time.strftime("%a %d %b")
        f2 = T.font(20)
        tw2 = d.textlength(date, font=f2)
        d.text((w / 2 - tw2 / 2, 138), date, font=f2, fill=th.muted)
        # hostname
        f3 = T.font(22, bold=True)
        tw3 = d.textlength(self.host, font=f3)
        d.text((w / 2 - tw3 / 2, 178), self.host, font=f3, fill=th.accent)

        # interface cards
        y = 220
        for it in self.ifaces[:3]:
            rrect(d, (14, y, w - 14, y + 46), 10, fill=th.card)
            col = th.ok if it["state"] == "up" else th.muted
            d.ellipse((26, y + 18, 38, y + 30), fill=col)
            d.text((50, y + 6), it["name"], font=T.font(18, bold=True), fill=th.fg)
            d.text((50, y + 25), it["ip"], font=T.font(15, mono=True), fill=th.muted)
            y += 54

        # wifi toggle button
        self.btn = Button((14, h - 66, w - 14, h - 14),
                          f"Wi-Fi  {'ON' if self.wifi else 'OFF'}",
                          kind="primary" if self.wifi else "danger",
                          font_size=22)
        self.btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.btn and self.btn.hit(x, y):
            system.set_wifi(not self.wifi)
            self.wifi = system.wifi_enabled()
            self.app.toast("Wi-Fi " + ("enabled" if self.wifi else "disabled"))
            return True
        return False
