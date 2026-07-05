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

FAST_TICK = 0.05        # ~20 Hz while frames are flowing
IDLE_TICK = 0.5         # guardrail: silent/absent bus doesn't spin the CPU


def _rx_frames(iface):
    """Kernel RX frame counter — one cheap sysfs read, no candump needed."""
    try:
        with open(f"/sys/class/net/{iface}/statistics/rx_packets") as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


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
    glyph = "can"
    tile_color_key = "bad"
    device_key = "can"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = IDLE_TICK
        self.iface = None
        self.rate_idx = 1                 # default 500k
        self.log_proc = None
        self.log_path = None
        self.detect_task = None
        self.status = "Detecting interface…"
        self.rx_count = None              # displayed kernel RX frame counter
        self.rx_rate = 0.0                # frames/s over the last second
        self._rx_hist = []                # (t, count) samples, ≤1 s window
        self._traffic_box = None          # dirty rect for the live counter card

    def on_enter(self):
        self.iface = _can_iface()
        self.status = (f"{self.iface} ready" if self.iface
                       else "No CAN iface (slcan needs slcand)")
        self._rx_hist = []
        self.rx_count = _rx_frames(self.iface) if self.iface else None
        self.rx_rate = 0.0

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
        if self.detect_task:
            if not self.detect_task.done:
                return False              # status text is static while detecting
            br = self.detect_task.result
            if br:
                self.rate_idx = BITRATES.index(br)
                self.status = f"Detected {br//1000}k"
            else:
                self.status = "No frames — bus idle or unpowered"
            self.detect_task = None
            return True                   # full redraw: status + selector moved

        if not self.iface:
            self.tick_interval = IDLE_TICK
            return False

        # live traffic counter (the responsive part of this screen)
        now = time.monotonic()
        rx = _rx_frames(self.iface)
        changed = False
        if rx is not None:
            self._rx_hist.append((now, rx))
            while self._rx_hist and now - self._rx_hist[0][0] > 1.0:
                self._rx_hist.pop(0)
            t0, c0 = self._rx_hist[0]
            rate = (rx - c0) / (now - t0) if now > t0 else 0.0
            if rx != self.rx_count or f"{rate:.0f}" != f"{self.rx_rate:.0f}":
                self.rx_count, self.rx_rate = rx, rate
                changed = True
        # guardrail: only frames flowing keeps the fast tick; a silent or
        # wedged bus drops back to the slow interval automatically
        flowing = (rx is not None and len(self._rx_hist) >= 2
                   and rx != self._rx_hist[0][1])
        self.tick_interval = FAST_TICK if flowing else IDLE_TICK
        if changed and self._traffic_box:
            self.report_dirty(self._traffic_box)
        return changed

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
        y += 48

        # live traffic counter — the only part repainted on the fast tick
        rrect(d, (14, y, w - 14, y + 70), 10, fill=th.card)
        d.text((26, y + 8), "RX FRAMES", font=T.font(11, bold=True),
               fill=th.muted)
        live = self.tick_interval == FAST_TICK   # frames seen in the last second
        d.ellipse((w - 42, y + 10, w - 28, y + 24),
                  fill=th.ok if live else th.card_hi)
        count = "—" if self.rx_count is None else f"{self.rx_count:,}"
        d.text((26, y + 28), count, font=T.font(24, bold=True, mono=True),
               fill=th.fg)
        rate = f"{self.rx_rate:.0f}/s" if live else "idle"
        rf = T.font(16, bold=True, mono=True)
        d.text((w - 28 - d.textlength(rate, font=rf), y + 36), rate, font=rf,
               fill=th.accent if live else th.muted)
        self._traffic_box = (0, y - 2, w, y + 72)

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
