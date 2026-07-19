"""RTL-SDR — frequency scanner, signal identifier, and IQ capture.

Two ways to answer "what is this?":
  • Scan     — rtl_power sweep → spectrum + peak (see where energy is).
  • Identify — rtl_433 tunes the band's ISM centre and decodes real packets,
               naming the device (weather sensor, TPMS, gate/car remote, …).
Plus a per-band knowledge hint for signals that can't be decoded (e.g. LoRa).
The RTL-SDR is receive-only, so Capture records IQ; there is no replay.
"""

import json
import os
import subprocess
import time

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, brackets, spaced, status_square
from .base import Screen, HEADER_H

CAP_DIR = "/opt/kilodash/captures"
SCAN_CSV = "/opt/kilodash/captures/.scan.csv"

# label, low MHz, high MHz, step kHz, rtl_433 decode centre Hz (or None), hint
BANDS = [
    {"label": "433 ISM", "lo": 433.0, "hi": 435.0, "step": 5, "dec": 433_920_000,
     "hint": "Gate/car remotes, weather & temp sensors, TPMS, doorbells"},
    {"label": "315 ISM", "lo": 314.5, "hi": 315.7, "step": 5, "dec": 315_000_000,
     "hint": "Car key fobs, garage/gate remotes, TPMS (North America)"},
    {"label": "868 ISM", "lo": 868.0, "hi": 870.0, "step": 5, "dec": 868_300_000,
     "hint": "EU ISM: LoRa/Meshtastic (not decodable), meters, sensors"},
    {"label": "915 ISM", "lo": 914.0, "hi": 916.0, "step": 5, "dec": 915_000_000,
     "hint": "US ISM: LoRa (not decodable), industrial sensors"},
    {"label": "FM bcast", "lo": 88.0, "hi": 108.0, "step": 50, "dec": None,
     "hint": "Broadcast FM radio — listen with rtl_fm"},
    {"label": "Airband", "lo": 118.0, "hi": 137.0, "step": 25, "dec": None,
     "hint": "Aircraft AM voice"},
    {"label": "ADS-B", "lo": 1089.0, "hi": 1091.0, "step": 5, "dec": None,
     "hint": "Aircraft transponders @ 1090 MHz — use dump1090"},
]


def _scan(lo, hi, step_khz):
    try:
        os.remove(SCAN_CSV)
    except OSError:
        pass
    subprocess.run(["rtl_power", "-f", f"{lo}M:{hi}M:{step_khz}k", "-i", "1",
                    "-1", "-g", "40", SCAN_CSV], capture_output=True, timeout=40)
    pts = []
    try:
        for line in open(SCAN_CSV):
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


def _identify(freq_hz, secs=6):
    """rtl_433 decode window → list of human summaries of decoded packets."""
    try:
        p = subprocess.run(["rtl_433", "-f", str(int(freq_hz)), "-F", "json",
                            "-M", "level", "-T", str(secs)],
                           capture_output=True, text=True, timeout=secs + 15)
    except Exception:       # noqa: BLE001
        return []
    seen, out = set(), []
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        model = e.get("model", "unknown")
        key = (model, e.get("id"), e.get("channel"))
        if key in seen:
            continue
        seen.add(key)
        bits = []
        for f in ("temperature_C", "humidity", "battery_ok", "code",
                  "button", "pressure_kPa", "moisture"):
            if f in e:
                bits.append(f"{f.split('_')[0]}={e[f]}")
        if e.get("id") is not None:
            bits.insert(0, f"id={e['id']}")
        out.append((model, "  ".join(bits[:3])))
    return out


def _capture(freq_hz, secs=4):
    os.makedirs(CAP_DIR, exist_ok=True)
    rate = 2_048_000
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = f"{CAP_DIR}/iq_{int(freq_hz/1000)}k_{stamp}.cu8"
    subprocess.run(["rtl_sdr", "-f", str(int(freq_hz)), "-s", str(rate),
                    "-n", str(rate * secs), path], capture_output=True,
                   timeout=secs + 15)
    return path


class SdrScreen(Screen):
    title = "RTL-SDR"
    glyph = "sdr"
    tile_color_key = "accent"
    device_key = "sdr"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.5
        self.band = 0
        self.mode = "spectrum"          # or "decode"
        self.pts = []
        self.peak = None
        self.events = []
        self.scan_task = None
        self.id_task = None
        self.cap_task = None
        self.status = "Scan for energy, Identify to decode"
        self._btns = {}

    def _busy(self):
        return any(t and not t.done for t in
                   (self.scan_task, self.id_task, self.cap_task))

    def start_scan(self):
        if self._busy():
            return
        b = BANDS[self.band]
        self.mode = "spectrum"
        self.status = f"Scanning {b['label']}…"
        self.pts, self.peak = [], None
        self.scan_task = system.Task(_scan, b["lo"], b["hi"], b["step"])

    def start_identify(self):
        if self._busy():
            return
        b = BANDS[self.band]
        if not b["dec"]:
            self.app.toast("No packet decoder for this band")
            return
        self.mode = "decode"
        self.events = []
        self.status = f"Listening @ {b['dec']/1e6:.2f} MHz…"
        self.id_task = system.Task(_identify, b["dec"], 6)

    def start_capture(self):
        if self._busy():
            return
        freq = self.peak[0] if self.peak else BANDS[self.band]["dec"] \
            or int(BANDS[self.band]["lo"] * 1e6)
        self.status = f"Capturing @ {freq/1e6:.3f} MHz…"
        self.cap_task = system.Task(_capture, freq, 4)

    def tick(self):
        ch = False
        if self.scan_task and self.scan_task.done:
            self.pts = self.scan_task.result or []
            if self.pts:
                self.peak = max(self.pts, key=lambda p: p[1])
                self.status = f"peak {self.peak[0]/1e6:.3f} MHz — Identify to decode"
            else:
                self.status = "No spectrum data"
            self.scan_task = None
            ch = True
        if self.id_task and self.id_task.done:
            self.events = self.id_task.result or []
            self.status = (f"decoded {len(self.events)} device(s)"
                           if self.events else "no known packets — see band note")
            self.id_task = None
            ch = True
        if self.cap_task and self.cap_task.done:
            path = self.cap_task.result
            sz = os.path.getsize(path) // 1024 if path and os.path.exists(path) else 0
            self.app.toast(f"Saved {os.path.basename(path)} ({sz} KB)" if sz
                           else "Capture failed")
            self.cap_task = None
            ch = True
        return ch or self._busy()

    # --------------------------------------------------------------- drawing
    def _draw_spectrum(self, d, th, box):
        x0, y0, x1, y1 = box
        if not self.pts:
            msg = spaced("SCAN FOR SPECTRUM")
            f = T.font(12, bold=True, mono=True)
            d.text((x0 + 12, (y0 + y1) / 2 - 8), msg, font=f, fill=th.muted)
            return
        w = int(x1 - x0 - 12)
        dbs = [p[1] for p in self.pts]
        lo_db, span = min(dbs), max(1.0, max(dbs) - min(dbs))
        n = len(self.pts)
        for i in range(w):
            f_hz, db = self.pts[int(i * n / w)]
            frac = (db - lo_db) / span
            bh = int(frac * (y1 - y0 - 10))
            col = th.ok if frac > 0.66 else th.warn if frac > 0.4 else th.accent
            d.line((x0 + 6 + i, y1 - 4, x0 + 6 + i, y1 - 4 - bh), fill=col)
        if self.peak:
            f0, f1 = self.pts[0][0], self.pts[-1][0]
            if f1 > f0:
                px = x0 + 6 + int((self.peak[0] - f0) / (f1 - f0) * w)
                d.line((px, y0 + 2, px, y1 - 4), fill=th.fg)

    def _draw_decode(self, d, th, box):
        x0, y0, x1, y1 = box
        if not self.events:
            msg = spaced("LISTENING" if self.id_task else "IDENTIFY TO DECODE")
            d.text((x0 + 12, y0 + 10), msg,
                   font=T.font(12, bold=True, mono=True), fill=th.muted)
            return
        yy = y0 + 8
        for model, info in self.events[:6]:
            status_square(d, (x0 + 10, yy + 4, x0 + 20, yy + 14), "lit", th.ok)
            d.text((x0 + 28, yy), model[:24].upper(),
                   font=T.font(14, bold=True, mono=True), fill=th.fg)
            if info:
                d.text((x0 + 28, yy + 18), info[:30], font=T.font(11, mono=True),
                       fill=th.muted)
            yy += 40

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        b = BANDS[self.band]
        self._btns = {}
        y = HEADER_H + 6

        # band selector — hard-edged grouping card
        d.rectangle((14, y, w - 14, y + 42), fill=th.card, outline=th.card_hi,
                    width=1)
        self._btns["prev"] = (14, y, 56, y + 42)
        self._btns["next"] = (w - 56, y, w - 14, y + 42)
        d.text((26, y + 8), "‹", font=T.font(28, bold=True), fill=th.accent)
        d.text((w - 42, y + 8), "›", font=T.font(28, bold=True), fill=th.accent)
        bf = T.font(19, bold=True, mono=True)
        d.text((w / 2 - d.textlength(b["label"].upper(), font=bf) / 2, y + 9),
               b["label"].upper(), font=bf, fill=th.fg)
        y += 46

        # band knowledge hint
        d.text((16, y), ("LIKELY: " + b["hint"])[:45],
               font=T.font(T.SUB, mono=True), fill=th.muted)
        y += 18

        # results area (spectrum or decode) — the bracket-framed instrument
        area = (14, y, w - 14, y + 172)
        brackets(d, area, th.muted)
        cap_lbl = spaced("DECODE" if self.mode == "decode" else "SPECTRUM")
        d.text((area[0] + 10, y + 4), cap_lbl,
               font=T.font(10, bold=True, mono=True), fill=th.muted)
        inner = (area[0], y + 20, area[2], area[3])
        if self.mode == "decode":
            self._draw_decode(d, th, inner)
        else:
            self._draw_spectrum(d, th, inner)
        y += 180

        # status line
        d.rectangle((14, y, w - 14, y + 34), fill=th.card, outline=th.card_hi,
                    width=1)
        d.text((24, y + 10), self.status[:38].upper(),
               font=T.font(12, bold=True, mono=True), fill=th.muted)
        y += 42

        # actions: [SCAN] [IDENTIFY]  then  [CAPTURE]
        bw = (w - 28 - 10) / 2
        scan = Button((14, y, 14 + bw, y + 46), "SCAN", kind="primary", font_size=18)
        idb = Button((w - 14 - bw, y, w - 14, y + 46), "IDENTIFY",
                     kind="normal", font_size=18)
        idb.enabled = b["dec"] is not None and not self._busy()
        scan.enabled = not self._busy()
        scan.draw(d, th)
        idb.draw(d, th)
        self._btns["scan"] = scan.box if scan.enabled else None
        self._btns["identify"] = idb.box if idb.enabled else None
        y += 52
        cap = Button((14, y, w - 14, y + 42), "CAPTURE IQ", kind="ghost",
                     font_size=17)
        cap.enabled = not self._busy()
        cap.draw(d, th)
        self._btns["cap"] = cap.box if cap.enabled else None

    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self._in("prev", x, y):
            self.band = (self.band - 1) % len(BANDS)
            return True
        if self._in("next", x, y):
            self.band = (self.band + 1) % len(BANDS)
            return True
        if self._in("scan", x, y):
            self.start_scan()
            return True
        if self._in("identify", x, y):
            self.start_identify()
            return True
        if self._in("cap", x, y):
            self.start_capture()
            return True
        return False
