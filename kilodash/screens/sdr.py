"""RTL-SDR — Flipper-style frequency scanner + IQ capture.

Stage 1: sweep a band with rtl_power and draw the spectrum, mark the peak, and
capture raw IQ around it to a file. The RTL-SDR is receive-only, so there is no
replay here — that needs TX hardware (HackRF / CC1101 / rpitx); the UI says so.
"""

import glob
import os
import subprocess
import time

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

CAP_DIR = "/opt/kilodash/captures"
SCAN_CSV = "/opt/kilodash/captures/.scan.csv"

# (label, low MHz, high MHz, step kHz)
BANDS = [
    ("433 ISM", 433.0, 435.0, 5),
    ("315 ISM", 314.5, 315.5, 5),
    ("868 ISM", 868.0, 870.0, 5),
    ("FM bcast", 88.0, 108.0, 50),
    ("Airband", 118.0, 137.0, 25),
    ("ADS-B", 1089.0, 1091.0, 5),
    ("2m ham", 144.0, 148.0, 10),
    ("70cm ham", 430.0, 440.0, 10),
]


def _scan(low, high, step_khz):
    """Run one rtl_power sweep; return (freqs_hz, dbs) aggregated + sorted."""
    try:
        os.remove(SCAN_CSV)
    except OSError:
        pass
    cmd = ["rtl_power", "-f", f"{low}M:{high}M:{step_khz}k",
           "-i", "1", "-1", "-g", "40", SCAN_CSV]
    subprocess.run(cmd, capture_output=True, timeout=40)
    pts = []
    try:
        with open(SCAN_CSV) as f:
            for line in f:
                p = [c.strip() for c in line.split(",")]
                if len(p) < 7:
                    continue
                f_low, f_step = int(p[2]), float(p[4])
                for i, db in enumerate(p[6:]):
                    try:
                        pts.append((f_low + i * f_step, float(db)))
                    except ValueError:
                        pass
    except OSError:
        pass
    pts.sort()
    return pts


def _capture(freq_hz, secs=4):
    """Record raw IQ around freq to a timestamped file. Returns the path."""
    os.makedirs(CAP_DIR, exist_ok=True)
    rate = 2_048_000
    n = rate * secs
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = f"{CAP_DIR}/iq_{int(freq_hz/1000)}k_{stamp}.cu8"
    subprocess.run(["rtl_sdr", "-f", str(int(freq_hz)), "-s", str(rate),
                    "-n", str(n), path], capture_output=True, timeout=secs + 15)
    return path


class SdrScreen(Screen):
    title = "RTL-SDR"
    tile_color_key = "accent"
    device_key = "sdr"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.5
        self.band = 0
        self.pts = []
        self.peak = None            # (freq_hz, db)
        self.scan_task = None
        self.cap_task = None
        self.status = "Pick a band, then Scan"
        self._btns = {}

    def on_enter(self):
        self.status = "Pick a band, then Scan"

    # ------------------------------------------------------------------ scan
    def start_scan(self):
        if self.scan_task and not self.scan_task.done:
            return
        lbl, lo, hi, st = BANDS[self.band]
        self.status = f"Scanning {lbl}…"
        self.pts = []
        self.peak = None
        self.scan_task = system.Task(_scan, lo, hi, st)

    def start_capture(self):
        if not self.peak or (self.cap_task and not self.cap_task.done):
            return
        self.status = f"Capturing @ {self.peak[0]/1e6:.3f} MHz…"
        self.cap_task = system.Task(_capture, self.peak[0], 4)

    def tick(self):
        changed = False
        if self.scan_task and self.scan_task.done:
            self.pts = self.scan_task.result or []
            if self.pts:
                self.peak = max(self.pts, key=lambda p: p[1])
                self.status = f"{BANDS[self.band][0]}: peak {self.peak[0]/1e6:.3f} MHz"
            else:
                self.status = "No data (is the SDR busy?)"
            self.scan_task = None
            changed = True
        if self.cap_task and self.cap_task.done:
            path = self.cap_task.result
            sz = os.path.getsize(path) // 1024 if path and os.path.exists(path) else 0
            self.app.toast(f"Saved {os.path.basename(path)} ({sz} KB)" if sz
                           else "Capture failed")
            self.status = "Capture saved to /captures" if sz else "Capture failed"
            self.cap_task = None
            changed = True
        if self.scan_task or self.cap_task:
            changed = True
        return changed

    # --------------------------------------------------------------- drawing
    def _spectrum(self, d, th, box):
        x0, y0, x1, y1 = box
        rrect(d, box, 8, fill=th.card)
        if not self.pts:
            d.text((x0 + 12, (y0 + y1) / 2 - 8), "no spectrum yet",
                   font=T.font(14), fill=th.muted)
            return
        w = int(x1 - x0 - 12)
        dbs = [p[1] for p in self.pts]
        lo_db, hi_db = min(dbs), max(dbs)
        span = max(1.0, hi_db - lo_db)
        n = len(self.pts)
        peak_f = self.peak[0] if self.peak else None
        for i in range(w):
            j = int(i * n / w)
            f_hz, db = self.pts[j]
            frac = (db - lo_db) / span
            bh = int(frac * (y1 - y0 - 8))
            col = th.ok if frac > 0.66 else th.warn if frac > 0.4 else th.accent
            xx = x0 + 6 + i
            d.line((xx, y1 - 4, xx, y1 - 4 - bh), fill=col)
        # peak marker
        if peak_f is not None:
            f0 = self.pts[0][0]
            f1 = self.pts[-1][0]
            if f1 > f0:
                px = x0 + 6 + int((peak_f - f0) / (f1 - f0) * w)
                d.line((px, y0 + 2, px, y1 - 4), fill=th.fg, width=1)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        y = HEADER_H + 8
        self._btns = {}

        # band selector row:  [<]  BAND  [>]
        lbl, lo, hi, st = BANDS[self.band]
        rrect(d, (14, y, w - 14, y + 44), 10, fill=th.card)
        self._btns["band_prev"] = (14, y, 58, y + 44)
        self._btns["band_next"] = (w - 58, y, w - 14, y + 44)
        d.text((28, y + 10), "‹", font=T.font(30, bold=True), fill=th.accent)
        d.text((w - 44, y + 10), "›", font=T.font(30, bold=True), fill=th.accent)
        bt = T.font(20, bold=True)
        tw = d.textlength(lbl, font=bt)
        d.text((w / 2 - tw / 2, y + 10), lbl, font=bt, fill=th.fg)
        rng = f"{lo:.0f}-{hi:.0f} MHz"
        rt = T.font(12, mono=True)
        d.text((w / 2 - d.textlength(rng, font=rt) / 2, y + 32), rng,
               font=rt, fill=th.muted)
        y += 54

        # spectrum
        self._spectrum(d, th, (14, y, w - 14, y + 150))
        y += 160

        # readout
        rrect(d, (14, y, w - 14, y + 46), 10, fill=th.card)
        if self.peak:
            d.text((26, y + 6), "PEAK", font=T.font(12), fill=th.muted)
            d.text((26, y + 20), f"{self.peak[0]/1e6:.3f} MHz",
                   font=T.font(20, bold=True, mono=True), fill=th.ok)
            d.text((w - 96, y + 14), f"{self.peak[1]:.0f} dB",
                   font=T.font(18, bold=True, mono=True), fill=th.fg)
        else:
            d.text((26, y + 14), self.status[:34], font=T.font(15), fill=th.muted)
        y += 56

        # actions
        scanning = self.scan_task is not None
        capturing = self.cap_task is not None
        bw = (w - 14 * 2 - 12) / 2
        scan_btn = Button((14, y, 14 + bw, y + 50),
                          "Scanning…" if scanning else "Scan",
                          kind="primary", font_size=20)
        scan_btn.enabled = not scanning and not capturing
        cap_btn = Button((w - 14 - bw, y, w - 14, y + 50),
                         "Capturing…" if capturing else "Capture",
                         kind="normal", font_size=20)
        cap_btn.enabled = bool(self.peak) and not scanning and not capturing
        scan_btn.draw(d, th)
        cap_btn.draw(d, th)
        self._btns["scan"] = scan_btn.box if scan_btn.enabled else None
        self._btns["cap"] = cap_btn.box if cap_btn.enabled else None
        y += 58

        d.text((16, y), f"RX only — no replay. Captures → {CAP_DIR}",
               font=T.font(11), fill=th.muted)

    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self._in("band_prev", x, y):
            self.band = (self.band - 1) % len(BANDS)
            return True
        if self._in("band_next", x, y):
            self.band = (self.band + 1) % len(BANDS)
            return True
        if self._in("scan", x, y):
            self.start_scan()
            return True
        if self._in("cap", x, y):
            self.start_capture()
            return True
        return False
