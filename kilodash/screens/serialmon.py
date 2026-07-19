"""USB-serial monitor (FTDI / CP210x / CH340).

Lists connected serial ports and gives a read-only live view of one at a chosen
baud — handy for sniffing a device's debug/UART output. Read-only for now.
"""

import collections
import glob
import os
import subprocess
import threading

from PIL import Image, ImageDraw

from .. import theme as T
from ..widgets import Button, brackets, spaced
from .base import Screen, HEADER_H

BAUDS = [115200, 9600, 57600, 38400, 19200, 250000, 460800]


def _ports():
    return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))


class SerialScreen(Screen):
    title = "Serial"
    tile_id = "serial"
    glyph = "serial"
    tile_color_key = "muted"
    device_key = "serial"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.4
        self.ports = []
        self.port_idx = 0
        self.baud_idx = 0
        self.lines = collections.deque(maxlen=200)
        self._fd = None
        self._reader = None
        self._stop = False
        self._buf = ""
        self.open_btn = None
        self._btns = {}

    def on_enter(self):
        self.ports = _ports()

    @property
    def open(self):
        return self._fd is not None

    def _cur_port(self):
        return self.ports[self.port_idx] if self.ports else None

    def _open(self):
        port = self._cur_port()
        if not port:
            return
        baud = BAUDS[self.baud_idx]
        subprocess.run(["stty", "-F", port, "raw", "-echo", str(baud)],
                       capture_output=True)
        try:
            self._fd = os.open(port, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            self.app.toast(f"OPEN FAILED · ERRNO {e.errno}")
            return
        self.lines.clear()
        self._buf = ""
        self._stop = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _close(self):
        self._stop = True
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _read_loop(self):
        import select
        while not self._stop and self._fd is not None:
            try:
                r, _, _ = select.select([self._fd], [], [], 0.2)
                if not r:
                    continue
                data = os.read(self._fd, 4096)
            except OSError:
                break
            if not data:
                continue
            self._buf += data.decode("utf-8", "replace")
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self.lines.append(line.rstrip("\r")[:60])

    def on_leave(self):
        if self.open:
            self._close()

    def tick(self):
        if not self.open:
            self.ports = _ports()
        return True

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        y = HEADER_H + 8
        self._btns = {}

        # port + baud selectors on one row (hard-edged; arrows keep their
        # original tap zones)
        d.rectangle((14, y, w - 14, y + 44), fill=th.card,
                    outline=th.card_hi, width=1)
        d.line((w / 2, y + 6, w / 2, y + 38), fill=th.card_hi, width=1)
        self._btns["port_prev"] = (14, y, 50, y + 44)
        self._btns["port_next"] = (w / 2 - 24, y, w / 2, y + 44)
        d.text((24, y + 9), "‹", font=T.font(26, bold=True), fill=th.accent)
        d.text((w / 2 - 20, y + 9), "›", font=T.font(26, bold=True), fill=th.accent)
        fl = T.font(8, bold=True, mono=True)
        fv = T.font(14, bold=True, mono=True)
        port = self._cur_port()
        pn = os.path.basename(port).upper() if port else "NO PORT"
        d.text((58, y + 6), spaced("PORT"), font=fl, fill=th.muted)
        d.text((58, y + 19), pn[:8], font=fv, fill=th.fg if port else th.muted)
        self._btns["baud_prev"] = (w / 2, y, w / 2 + 24, y + 44)
        self._btns["baud_next"] = (w - 50, y, w - 14, y + 44)
        d.text((w / 2 + 4, y + 9), "‹", font=T.font(26, bold=True), fill=th.accent)
        d.text((w - 40, y + 9), "›", font=T.font(26, bold=True), fill=th.accent)
        d.text((w / 2 + 30, y + 6), spaced("BAUD"), font=fl, fill=th.muted)
        d.text((w / 2 + 30, y + 19), str(BAUDS[self.baud_idx]),
               font=fv, fill=th.fg)
        y += 52

        # monitor viewport: bracket-framed instrument; the feed itself
        # stays raw mono
        mon = (12, y, w - 12, h - 66)
        brackets(d, mon, th.muted)
        fc = T.font(9, bold=True, mono=True)
        d.text((22, y + 7), spaced("RX FEED"), font=fc, fill=th.muted)
        st = spaced("LINK UP") if self.open else spaced("NO LINK")
        stw = d.textlength(st, font=fc)
        d.text((w - 22 - stw, y + 7), st, font=fc,
               fill=th.ok if self.open else th.muted)
        yy = y + 24
        f = T.font(12, mono=True)
        for line in list(self.lines)[-((h - 66 - 6 - yy) // 15):]:
            d.text((20, yy), line, font=f, fill=th.fg)
            yy += 15

        # open/close — closing the link is a stand-down, not a fault: amber
        by = h - 60
        self.open_btn = Button((14, by, w - 14, by + 48),
                               "CLOSE" if self.open else "OPEN",
                               kind="primary",
                               color=th.warn if self.open else None,
                               font_size=18)
        self.open_btn.enabled = bool(port)
        self.open_btn.draw(d, th)
        self._btns["open"] = self.open_btn.box if self.open_btn.enabled else None

    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self.open:
            if self._in("open", x, y):
                self._close()
                return True
            return False
        if self._in("port_prev", x, y) and self.ports:
            self.port_idx = (self.port_idx - 1) % len(self.ports)
            return True
        if self._in("port_next", x, y) and self.ports:
            self.port_idx = (self.port_idx + 1) % len(self.ports)
            return True
        if self._in("baud_prev", x, y):
            self.baud_idx = (self.baud_idx - 1) % len(BAUDS)
            return True
        if self._in("baud_next", x, y):
            self.baud_idx = (self.baud_idx + 1) % len(BAUDS)
            return True
        if self._in("open", x, y):
            self._open()
            return True
        return False
