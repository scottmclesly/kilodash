"""Logic analyzer — FX2LP (CY7C68013A) via sigrok-cli/fx2lafw. CAPTURE ONLY.

One-shot digital capture on D0-D7 (≤24 MHz, edge triggers only, no analog)
with optional read-only protocol decode (UART/I2C/SPI/CAN). Every capture
persists to /opt/kilodash/captures/*.sr for PulseView on a laptop. The la.py
builder is the only thing that can express a capture — no raw flags here.

Bench gotchas: sigrok soft-loads the fx2lafw firmware on every plug (first
run costs ~1 s while the board re-enumerates), and the bare board has NO input
protection — 3.3 V logic only; buffer/divide before probing anything near
Scottina's 12 V wiring.
"""

from PIL import Image, ImageDraw

from .. import la, theme as T
from ..widgets import Button, brackets, spaced
from .base import Screen, HEADER_H

# Fixed vertical bands; all x-coordinates derive from app.w at draw time
# (the panel is 320×480 portrait — never hardcode widths).
CHAN_Y = HEADER_H + 6            # 50  channel toggle chips D0-D7
CHIP_H = 34
RATE_Y = CHAN_Y + CHIP_H + 6     # 90  samplerate | sample-count selectors
SEL_H = 36
TRIG_Y = RATE_Y + SEL_H + 6     # 132 trigger | decoder selectors
RUN_Y = TRIG_Y + SEL_H + 6      # 174 full-width Run/Stop
RUN_H = 48
STAT_Y = RUN_Y + RUN_H + 6      # 228 status card
STAT_H = 26
OUT_TOP = STAT_Y + STAT_H + 4   # 258 scrollable results pane
LINE_H = 16                      # decoded annotation rows
STRIP_H = 14                     # per-channel edge strip rows

# UI display names for the allow-listed samplerate labels
_RATE_NAMES = {"k": "kHz", "m": "MHz"}


def _rate_name(label):
    return f"{label[:-1]} {_RATE_NAMES[label[-1]]}"


class LogicScreen(Screen):
    title = "Logic"
    tile_id = "logic"
    glyph = "logic"
    tile_color_key = "warn"
    device_key = "la"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0          # bursty one-shot; no fast streaming
        self.enabled = {"D0"}             # channel subset, D0 on by default
        self.rate_idx = la.SAMPLERATES.index("1m")
        self.samp_idx = la.SAMPLE_COUNTS.index(4096)
        self.trigger = None               # None or (channel, edge)
        self.preset_idx = 0               # into la.DECODER_PRESETS
        self.job = None
        self._done_handled = True
        self._btns = {}
        self.run_btn = None

    # ---- capture ----
    def _channels(self):
        return sorted(self.enabled, key=la.CHANNELS.index)

    def _running(self):
        return self.job is not None and not self.job.done

    def start(self):
        self.scroll = 0
        self._done_handled = False
        self.job = la.CaptureJob(self._channels(),
                                 la.SAMPLERATES[self.rate_idx],
                                 la.SAMPLE_COUNTS[self.samp_idx],
                                 self.trigger,
                                 la.DECODER_PRESETS[self.preset_idx]["key"])

    def on_leave(self):
        if self._running():
            self.job.stop()

    def tick(self):
        j = self.job
        if j is None:
            return False
        if not j.done:
            return True                   # status text advances per phase
        if not self._done_handled:
            self._done_handled = True
            return True
        return False

    # ---- trigger options (edge triggers on enabled channels only) ----
    def _trig_options(self):
        opts = [None]
        for ch in self._channels():
            opts += [(ch, "r"), (ch, "f")]
        return opts

    def _cycle_trigger(self, step):
        opts = self._trig_options()
        i = opts.index(self.trigger) if self.trigger in opts else 0
        self.trigger = opts[(i + step) % len(opts)]

    # ---- rendering ----
    def content_area(self):
        return (0, OUT_TOP, self.app.w, self.app.h - OUT_TOP)


    def model_rows(self):
        """Capture configuration and job state. Reads job attributes directly
        — never job.snapshot(), which takes a lock and copies."""
        j = self.job
        trig = (f"{self.trigger[0]} {'RISE' if self.trigger[1] == 'r' else 'FALL'}"
                if self.trigger else "NONE")
        rows = [
            {"label": "CHANNELS", "value": ",".join(self._channels()) or "—",
             "state": None},
            {"label": "RATE", "value": str(la.SAMPLERATES[self.rate_idx][0]),
             "state": None},
            {"label": "SAMPLES", "value": str(la.SAMPLE_COUNTS[self.samp_idx][0]),
             "state": None},
            {"label": "TRIGGER", "value": trig, "state": None},
            {"label": "DECODER",
             "value": str(la.DECODER_PRESETS[self.preset_idx].get("label", "—")),
             "state": None},
        ]
        if j is not None:
            rows.append({"label": "CAPTURE",
                         "value": "DONE" if j.done else "RUNNING",
                         "state": "ok" if j.done else "caution"})
            if getattr(j, "status", ""):
                rows.append({"label": "STATUS", "value": str(j.status),
                             "state": None})
            if getattr(j, "error", None):
                rows.append({"label": "ERROR", "value": str(j.error),
                             "state": "fault"})
        return rows


    def model_buttons(self):
        """Everything except `run` freezes while a capture is in flight, as
        it does on the panel (logic.py handle_tap)."""
        running = self._running()
        return [
            {"id": "run", "label": "STOP" if running else "RUN",
             "enabled": True, "confirm": False},
            {"id": "rate_prev", "label": "RATE -", "enabled": not running,
             "confirm": False},
            {"id": "rate_next", "label": "RATE +", "enabled": not running,
             "confirm": False},
            {"id": "samp_prev", "label": "DEPTH -", "enabled": not running,
             "confirm": False},
            {"id": "samp_next", "label": "DEPTH +", "enabled": not running,
             "confirm": False},
        ]

    def handle_button(self, bid):
        if bid == "run":
            if self._running():
                self.job.stop()
            else:
                self.start()
            return True
        if self._running():
            return False
        if bid == "rate_prev":
            self.rate_idx = (self.rate_idx - 1) % len(la.SAMPLERATES); return True
        if bid == "rate_next":
            self.rate_idx = (self.rate_idx + 1) % len(la.SAMPLERATES); return True
        if bid == "samp_prev":
            self.samp_idx = (self.samp_idx - 1) % len(la.SAMPLE_COUNTS); return True
        if bid == "samp_next":
            self.samp_idx = (self.samp_idx + 1) % len(la.SAMPLE_COUNTS); return True
        return False

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        self._draw_output(d, th, w, h)
        d.rectangle((0, HEADER_H, w, OUT_TOP), fill=th.bg)
        self._draw_channels(d, th, w)
        self._draw_selectors(d, th, w)
        self._draw_trig_row(d, th, w)
        self._draw_status(d, th, w)

    def _draw_channels(self, d, th, w):
        gap = 3
        cw = (w - 24 - gap * 7) / 8
        f = T.font(13, bold=True, mono=True)
        for i, ch in enumerate(la.CHANNELS):
            x0 = 12 + i * (cw + gap)
            box = (x0, CHAN_Y, x0 + cw, CHAN_Y + CHIP_H)
            on = ch in self.enabled
            d.rectangle(box, fill=th.accent if on else th.card,
                        outline=th.card_hi, width=1)
            tw = d.textlength(ch, font=f)
            d.text((x0 + cw / 2 - tw / 2, CHAN_Y + 9), ch, font=f,
                   fill=th.ink if on else th.muted)
            self._btns[f"ch{i}"] = box

    def _selector(self, d, th, box, label, key):
        """One ‹ value › cycle selector (serialmon pattern)."""
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=th.card, outline=th.card_hi, width=1)
        d.text((x0 + 8, y0 + 5), "‹", font=T.font(22, bold=True),
               fill=th.accent)
        d.text((x1 - 18, y0 + 5), "›", font=T.font(22, bold=True),
               fill=th.accent)
        f = T.font(13, bold=True, mono=True)
        label = label.upper()
        tw = d.textlength(label, font=f)
        d.text(((x0 + x1) / 2 - tw / 2, y0 + (y1 - y0) / 2 - 8), label,
               font=f, fill=th.fg)
        self._btns[f"{key}_prev"] = (x0, y0, x0 + 32, y1)
        self._btns[f"{key}_next"] = (x1 - 32, y0, x1, y1)

    def _draw_selectors(self, d, th, w):
        mid = w / 2
        self._selector(d, th, (12, RATE_Y, mid - 3, RATE_Y + SEL_H),
                       _rate_name(la.SAMPLERATES[self.rate_idx]), "rate")
        self._selector(d, th, (mid + 3, RATE_Y, w - 12, RATE_Y + SEL_H),
                       f"{la.SAMPLE_LABELS[self.samp_idx]} samples", "samp")

    def _draw_trig_row(self, d, th, w):
        mid = w / 2
        if self.trigger is None:
            t_label = "trig off"
        else:
            ch, edge = self.trigger
            t_label = f"{ch} {'↑' if edge == 'r' else '↓'}"
        self._selector(d, th, (12, TRIG_Y, mid - 3, TRIG_Y + SEL_H),
                       t_label, "trig")
        self._selector(d, th, (mid + 3, TRIG_Y, w - 12, TRIG_Y + SEL_H),
                       la.DECODER_PRESETS[self.preset_idx]["label"], "dec")
        running = self._running()
        # stopping a one-shot capture is a stand-down (amber), not a fault
        self.run_btn = Button((12, RUN_Y, w - 12, RUN_Y + RUN_H),
                              "STOP" if running else "RUN CAPTURE",
                              kind="primary",
                              color=th.warn if running else None,
                              font_size=20)
        self.run_btn.draw(d, th)

    def _draw_status(self, d, th, w):
        d.rectangle((12, STAT_Y, w - 12, STAT_Y + STAT_H), fill=th.card,
                    outline=th.card_hi, width=1)
        if self.job:
            status = self.job.status
        else:
            status = (f"{len(self.enabled)}CH · "
                      f"{_rate_name(la.SAMPLERATES[self.rate_idx])} · "
                      f"{la.SAMPLE_LABELS[self.samp_idx]} — TAP RUN")
        d.text((22, STAT_Y + 6), status[:38].upper(),
               font=T.font(12, bold=True, mono=True), fill=th.muted)

    def _draw_output(self, d, th, w, h):
        pane_h = h - OUT_TOP
        lines = self.job.snapshot() if self.job else []
        bits = self.job.bits if self.job else {}
        strips = [(ch, bits[ch]) for ch in la.CHANNELS if bits.get(ch)]
        if not lines and not strips:
            self.content_h = pane_h
            d.rectangle((0, OUT_TOP, w, h), fill=th.bg)
            d.text((22, OUT_TOP + 14), "Captures persist to captures/*.sr",
                   font=T.font(13), fill=th.muted)
            d.text((22, OUT_TOP + 34), "3.3 V logic only — no protection",
                   font=T.font(13), fill=th.warn)
            return

        strips_h = len(strips) * STRIP_H + (6 if strips else 0)
        self.content_h = max(strips_h + len(lines) * LINE_H + 8, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)

        # secondary: compact per-channel edge/activity strip
        lf = T.font(10, mono=True)
        sx0, sx1 = 46, w - 16
        for r, (ch, vals) in enumerate(strips):
            y = r * STRIP_H
            sd.text((16, y + 2), ch, font=lf, fill=th.muted)
            step = (sx1 - sx0) / max(1, len(vals))
            for i, v in enumerate(vals):
                x = sx0 + i * step
                if v == 2:      # edge inside this bucket
                    sd.line((x, y + 1, x, y + STRIP_H - 3), fill=th.accent)
                elif v == 1:    # high
                    sd.line((x, y + 2, x + step, y + 2), fill=th.ok)
                else:           # low
                    sd.line((x, y + STRIP_H - 4, x + step, y + STRIP_H - 4),
                            fill=th.muted)

        # primary: decoded transaction list (candump-style rows)
        for i, (indent, text, color) in enumerate(lines):
            y = strips_h + i * LINE_H
            col = getattr(th, color, th.fg)
            sd.text((20 + indent * 18, y), text,
                    font=T.font(13, bold=indent == 0, mono=True), fill=col)
        self.paste_list(OUT_TOP, pane_h, surf)

    # ---- input ----
    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
        if self.run_btn and self.run_btn.hit(x, y):
            if self._running():
                self.job.stop()
            else:
                self.start()
            return True
        if self._running():
            return False                  # settings frozen mid-capture
        for i, ch in enumerate(la.CHANNELS):
            if self._in(f"ch{i}", x, y):
                if ch in self.enabled:
                    if len(self.enabled) == 1:
                        self.app.toast("Keep at least one channel")
                        return True
                    self.enabled.discard(ch)
                    if self.trigger and self.trigger[0] == ch:
                        self.trigger = None
                else:
                    self.enabled.add(ch)
                return True
        if self._in("rate_prev", x, y):
            self.rate_idx = (self.rate_idx - 1) % len(la.SAMPLERATES)
            return True
        if self._in("rate_next", x, y):
            self.rate_idx = (self.rate_idx + 1) % len(la.SAMPLERATES)
            return True
        if self._in("samp_prev", x, y):
            self.samp_idx = (self.samp_idx - 1) % len(la.SAMPLE_COUNTS)
            return True
        if self._in("samp_next", x, y):
            self.samp_idx = (self.samp_idx + 1) % len(la.SAMPLE_COUNTS)
            return True
        if self._in("trig_prev", x, y):
            self._cycle_trigger(-1)
            return True
        if self._in("trig_next", x, y):
            self._cycle_trigger(1)
            return True
        if self._in("dec_prev", x, y):
            self.preset_idx = (self.preset_idx - 1) % len(la.DECODER_PRESETS)
            return True
        if self._in("dec_next", x, y):
            self.preset_idx = (self.preset_idx + 1) % len(la.DECODER_PRESETS)
            return True
        return False
