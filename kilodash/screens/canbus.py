"""CAN bus tool (CANable / gs_usb or slcan).

Bring the interface up at a chosen bitrate, sniff a live frame counter, and log
traffic to a timestamped file. Includes a best-effort bitrate autodetect that
listens (listen-only) at each common rate and keeps the one that yields frames.
"""

import glob
import os
import signal
import subprocess
import time

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

CAP_DIR = "/opt/kilodash/captures"
BITRATES = [1000000, 500000, 250000, 125000, 100000, 50000]


def _sh(*cmd, timeout=6):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _can_iface():
    for p in sorted(glob.glob("/sys/class/net/can*")):
        return os.path.basename(p)
    return None


def _is_up(iface):
    try:
        return "state UP" in _sh("ip", "-br", "link", "show", iface).stdout \
            or "UP" in open(f"/sys/class/net/{iface}/operstate").read()
    except OSError:
        return False


def _bring_up(iface, bitrate, listen_only=False):
    _sh("ip", "link", "set", iface, "down")
    args = ["ip", "link", "set", iface, "type", "can", "bitrate", str(bitrate)]
    if listen_only:
        args += ["listen-only", "on"]
    _sh(*args)
    _sh("ip", "link", "set", iface, "up")


def _count_frames(iface, secs=1.2):
    try:
        p = subprocess.run(["candump", "-n", "5", "-T", str(int(secs * 1000)),
                            iface], capture_output=True, text=True,
                           timeout=secs + 2)
        return len([l for l in p.stdout.splitlines() if l.strip()])
    except Exception:       # noqa: BLE001
        return 0


def _autodetect(iface):
    best = (0, None)
    for br in BITRATES:
        _bring_up(iface, br, listen_only=True)
        n = _count_frames(iface, 1.2)
        if n > best[0]:
            best = (n, br)
        if n >= 5:
            break
    _sh("ip", "link", "set", iface, "down")
    return best[1]


class CanScreen(Screen):
    title = "CAN Bus"
    tile_color_key = "bad"
    device_key = "can"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.7
        self.iface = None
        self.rate_idx = 1                 # default 500k
        self.log_proc = None
        self.log_path = None
        self.detect_task = None
        self.status = "Detecting interface…"

    def on_enter(self):
        self.iface = _can_iface()
        self.status = (f"{self.iface} ready" if self.iface
                       else "No CAN iface (slcan needs slcand)")

    def _up(self):
        if self.iface:
            _bring_up(self.iface, BITRATES[self.rate_idx])
            self.status = f"{self.iface} up @ {BITRATES[self.rate_idx]//1000}k"

    def _down(self):
        if self.iface:
            _sh("ip", "link", "set", self.iface, "down")
            self.status = f"{self.iface} down"

    def _detect(self):
        if not self.iface or (self.detect_task and not self.detect_task.done):
            return
        self.status = "Autodetecting bitrate…"
        self.detect_task = system.Task(_autodetect, self.iface)

    def _toggle_log(self):
        if not self.iface:
            return
        if self.log_proc:
            self.log_proc.send_signal(signal.SIGINT)
            self.log_proc = None
            self.app.toast(f"Log saved: {os.path.basename(self.log_path or '')}")
            self.status = "Logging stopped"
            return
        self._up()
        os.makedirs(CAP_DIR, exist_ok=True)
        self.log_path = f"{CAP_DIR}/can_{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.log_proc = subprocess.Popen(
            ["candump", "-l", self.iface], cwd=CAP_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status = "Logging CAN traffic…"

    @property
    def logging(self):
        return self.log_proc is not None

    def on_leave(self):
        if self.logging:
            self._toggle_log()

    def tick(self):
        if self.detect_task and self.detect_task.done:
            br = self.detect_task.result
            if br:
                self.rate_idx = BITRATES.index(br)
                self.status = f"Detected {br//1000}k"
            else:
                self.status = "No frames — bus idle or unpowered"
            self.detect_task = None
            return True
        return self.detect_task is not None

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        y = HEADER_H + 12
        self._btns = {}

        rrect(d, (14, y, w - 14, y + 54), 10, fill=th.card)
        d.text((26, y + 8), "Interface", font=T.font(13), fill=th.muted)
        d.text((26, y + 26), self.iface or "not found",
                font=T.font(20, bold=True, mono=True),
                fill=th.ok if self.iface else th.bad)
        y += 64

        # bitrate selector
        rrect(d, (14, y, w - 14, y + 50), 10, fill=th.card)
        self._btns["rate_prev"] = (14, y, 58, y + 50)
        self._btns["rate_next"] = (w - 58, y, w - 14, y + 50)
        d.text((28, y + 12), "‹", font=T.font(28, bold=True), fill=th.accent)
        d.text((w - 42, y + 12), "›", font=T.font(28, bold=True), fill=th.accent)
        rate = f"{BITRATES[self.rate_idx]//1000} kbit/s"
        rt = T.font(20, bold=True)
        d.text((w / 2 - d.textlength(rate, font=rt) / 2, y + 13), rate,
               font=rt, fill=th.fg)
        y += 60

        det_btn = Button((14, y, w - 14, y + 48), "Autodetect bitrate",
                         kind="normal", font_size=18)
        det_btn.enabled = bool(self.iface) and self.detect_task is None
        det_btn.draw(d, th)
        self._btns["detect"] = det_btn.box if det_btn.enabled else None
        y += 56

        log_btn = Button((14, y, w - 14, y + 56),
                         "Stop logging" if self.logging else "Start logging",
                         kind="danger" if self.logging else "primary",
                         font_size=20)
        log_btn.enabled = bool(self.iface)
        log_btn.draw(d, th)
        self._btns["log"] = log_btn.box if log_btn.enabled else None
        y += 64

        rrect(d, (14, y, w - 14, y + 40), 8, fill=th.card)
        d.text((26, y + 12), self.status[:34], font=T.font(14), fill=th.muted)

    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self._in("rate_prev", x, y):
            self.rate_idx = (self.rate_idx - 1) % len(BITRATES)
            return True
        if self._in("rate_next", x, y):
            self.rate_idx = (self.rate_idx + 1) % len(BITRATES)
            return True
        if self._in("detect", x, y):
            self._detect()
            return True
        if self._in("log", x, y):
            self._toggle_log()
            return True
        return False
