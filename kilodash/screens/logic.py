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
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

# Fixed layout bands (480×320). Controls up top, results below.
CHAN_Y = HEADER_H + 6            # 50  channel toggle chips D0-D7
CHIP_H = 34
RATE_Y = CHAN_Y + CHIP_H + 6     # 90  samplerate | sample-count selectors
SEL_H = 36
TRIG_Y = RATE_Y + SEL_H + 6      # 132 trigger | decoder | Run
ROW3_H = 40
STAT_Y = TRIG_Y + ROW3_H + 6     # 178 status card
STAT_H = 26
OUT_TOP = STAT_Y + STAT_H + 4    # 208 scrollable results pane
LINE_H = 16                      # decoded annotation rows
STRIP_H = 14                     # per-channel edge strip rows

# UI display names for the allow-listed samplerate labels
_RATE_NAMES = {"k": "kHz", "m": "MHz"}


def _rate_name(label):
    return f"{label[:-1]} {_RATE_NAMES[label[-1]]}"


class LogicScreen(Screen):
    title = "Logic"
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
        gap = 4
        cw = (w - 24 - gap * 7) / 8
        f = T.font(15, bold=True, mono=True)
        for i, ch in enumerate(la.CHANNELS):
            x0 = 12 + i * (cw + gap)
            box = (x0, CHAN_Y, x0 + cw, CHAN_Y + CHIP_H)
            on = ch in self.enabled
            rrect(d, box, 8, fill=th.accent if on else th.card,
                  outline=th.card_hi, width=1)
            tw = d.textlength(ch, font=f)
            d.text((x0 + cw / 2 - tw / 2, CHAN_Y + 8), ch, font=f,
                   fill=th.ink if on else th.muted)
            self._btns[f"ch{i}"] = box

    def _selector(self, d, th, box, label, key):
        """One ‹ value › cycle selector (serialmon pattern); 40 px arrows."""
        x0, y0, x1, y1 = box
        rrect(d, box, 9, fill=th.card)
        d.text((x0 + 10, y0 + 4), "‹", font=T.font(24, bold=True),
               fill=th.accent)
        d.text((x1 - 22, y0 + 4), "›", font=T.font(24, bold=True),
               fill=th.accent)
        f = T.font(14, bold=True)
        tw = d.textlength(label, font=f)
        d.text(((x0 + x1) / 2 - tw / 2, y0 + (y1 - y0) / 2 - 9), label,
               font=f, fill=th.fg)
        self._btns[f"{key}_prev"] = (x0, y0, x0 + 40, y1)
        self._btns[f"{key}_next"] = (x1 - 40, y0, x1, y1)

    def _draw_selectors(self, d, th, w):
        mid = w / 2
        self._selector(d, th, (12, RATE_Y, mid - 3, RATE_Y + SEL_H),
                       _rate_name(la.SAMPLERATES[self.rate_idx]), "rate")
        self._selector(d, th, (mid + 3, RATE_Y, w - 12, RATE_Y + SEL_H),
                       f"{la.SAMPLE_LABELS[self.samp_idx]} samples", "samp")

    def _draw_trig_row(self, d, th, w):
        if self.trigger is None:
            t_label = "trig off"
        else:
            ch, edge = self.trigger
            t_label = f"{ch} {'↑' if edge == 'r' else '↓'}"
        self._selector(d, th, (12, TRIG_Y, 168, TRIG_Y + ROW3_H),
                       t_label, "trig")
        self._selector(d, th, (174, TRIG_Y, 366, TRIG_Y + ROW3_H),
                       la.DECODER_PRESETS[self.preset_idx]["label"], "dec")
        running = self._running()
        self.run_btn = Button((372, TRIG_Y, w - 12, TRIG_Y + ROW3_H),
                              "Stop" if running else "Run",
                              kind="danger" if running else "primary",
                              font_size=18)
        self.run_btn.draw(d, th)

    def _draw_status(self, d, th, w):
        rrect(d, (12, STAT_Y, w - 12, STAT_Y + STAT_H), 8, fill=th.card)
        if self.job:
            status = self.job.status
        else:
            status = (f"{len(self.enabled)}ch · "
                      f"{_rate_name(la.SAMPLERATES[self.rate_idx])} · "
                      f"{la.SAMPLE_LABELS[self.samp_idx]} — tap Run")
        d.text((22, STAT_Y + 5), status[:52], font=T.font(13), fill=th.muted)

    def _draw_output(self, d, th, w, h):
        pane_h = h - OUT_TOP
        lines = self.job.snapshot() if self.job else []
        bits = self.job.bits if self.job else {}
        strips = [(ch, bits[ch]) for ch in la.CHANNELS if bits.get(ch)]
        if not lines and not strips:
            self.content_h = pane_h
            d.rectangle((0, OUT_TOP, w, h), fill=th.bg)
            hint = ("Captures decode + persist to captures/*.sr · "
                    "3.3 V logic only")
            d.text((22, OUT_TOP + 14), hint, font=T.font(13), fill=th.muted)
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
