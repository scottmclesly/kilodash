"""Reusable finger-sized widgets: buttons, list rows, and an on-screen
keyboard for entering Wi-Fi passwords without a physical keyboard.
"""

from . import theme as T

MIN_TOUCH = 44          # never draw a tappable target smaller than this


# ---- Semiotic-Standard instrument idioms (shared across screens) ----

def spaced(s):
    """Spaced-caps instrument readout: "RUNNING" -> "R U N N I N G"."""
    return " ".join(s)


def hazard(d, box, col, step=9, width=3):
    """45° caution striping kept strictly inside `box` (PIL has no clipping,
    so only full-height strokes are drawn; partial edge stripes are dropped)."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    x = x0
    while x + h <= x1:
        d.line((x, y1, x + h, y0), fill=col, width=width)
        x += step


def brackets(d, box, col, arm=14, width=2):
    """Corner registration brackets framing an instrument area."""
    x0, y0, x1, y1 = box
    for x, sx in ((x0, 1), (x1, -1)):
        for y, sy in ((y0, 1), (y1, -1)):
            d.line((x, y, x + sx * arm, y), fill=col, width=width)
            d.line((x, y, x, y + sy * arm), fill=col, width=width)


def state_glyph(d, key, cx, cy, r, c):
    """Banner-sized Semiotic-Standard service-state marks (ringed):
    up (core lit) / spin (spin-up sector) / fault (exclamation) /
    standby (level bar). Kin to the Pomodoro phase glyphs."""
    lw = max(2, round(r / 5))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=c, width=lw)
    if key == "up":         # power core lit
        s = r * 0.5
        d.ellipse((cx - s, cy - s, cx + s, cy + s), fill=c)
    elif key == "spin":     # spin-up sector
        s = r * 0.68
        d.pieslice((cx - s, cy - s, cx + s, cy + s), 270, 60, fill=c)
    elif key == "fault":    # exclamation mark
        d.rectangle((cx - lw / 2, cy - r * 0.55, cx + lw / 2, cy + r * 0.1),
                    fill=c)
        d.rectangle((cx - lw / 2, cy + r * 0.32, cx + lw / 2, cy + r * 0.55),
                    fill=c)
    else:                   # standby: level bar
        d.rectangle((cx - r * 0.5, cy - lw / 2, cx + r * 0.5, cy + lw / 2),
                    fill=c)


def status_square(d, box, mode, col, width=2):
    """Square row-status glyph for inventory/list rows:
    'lit' = active, 'hollow' = inactive, 'slash' = degraded/unverified
    (draw slashes in caution amber; red stays reserved for faults)."""
    if mode == "lit":
        d.rectangle(box, fill=col)
    else:
        d.rectangle(box, outline=col, width=width)
        if mode == "slash":
            d.line((box[0], box[3], box[2], box[1]), fill=col, width=width)


def seg_row(d, x, y, lit, total, col, off_col,
            seg_w=8, seg_h=11, gap=2):
    """Horizontal segmented gauge: `lit` of `total` cells filled left to
    right; unlit cells stay as hollow outlines (extinguished, not absent)."""
    for i in range(total):
        box = (x + i * (seg_w + gap), y, x + i * (seg_w + gap) + seg_w,
               y + seg_h)
        if i < lit:
            d.rectangle(box, fill=col)
        else:
            d.rectangle(box, outline=off_col, width=1)


def confirm_dialog(d, th, w, title, body, buttons):
    """Modal stand-down prompt: hazard-capped amber frame, spaced-caps title,
    centred body lines, and a two-button footer. Laid out top-down from one
    geometry so the body can never grow into the buttons — pass `body` as a
    list of short lines. Returns the two button boxes, left to right.

    `buttons` is ((left_label, left_colour_key), (right_label, right_colour)).
    """
    x0, y0, x1, y1 = 26, 150, w - 26, 310
    d.rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), fill=th.bg)
    d.rectangle((x0, y0, x1, y1), fill=th.card, outline=th.warn, width=2)
    hazard(d, (x0 + 2, y0 + 2, x1 - 2, y0 + 16), th.warn)

    f = T.font(16, bold=True, mono=True)
    tw = d.textlength(title, font=f)
    d.text(((w - tw) / 2, y0 + 26), title, font=f, fill=th.warn)

    fb = T.font(T.SUB, bold=True, mono=True)
    for i, line in enumerate(body):
        bw = d.textlength(line, font=fb)
        d.text(((w - bw) / 2, y0 + 56 + i * 16), line, font=fb, fill=th.muted)

    by = y1 - 58                      # 44px targets, 14px below the frame
    mid = w // 2
    left_box = (x0 + 12, by, mid - 6, by + 44)
    right_box = (mid + 6, by, x1 - 12, by + 44)
    for box, (label, col) in ((left_box, buttons[0]), (right_box, buttons[1])):
        Button(box, label, color=col, font_size=15).draw(d, th)
    return left_box, right_box


def rrect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width)


class Button:
    def __init__(self, box, label, on_tap=None, kind="normal", font_size=20,
                 color=None):
        self.box = box            # (x0, y0, x1, y1)
        self.label = label
        self.on_tap = on_tap
        self.kind = kind          # normal | primary | danger | ghost
        self.font_size = font_size
        self.color = color        # explicit fill (overrides kind) — e.g. a phase colour
        self.enabled = True

    def hit(self, x, y):
        x0, y0, x1, y1 = self.box
        return self.enabled and x0 <= x <= x1 and y0 <= y <= y1

    def draw(self, d, th):
        x0, y0, x1, y1 = self.box
        if self.color is not None:
            fill, fg = self.color, th.ink
        elif self.kind == "primary":
            fill, fg = th.accent, th.ink
        elif self.kind == "danger":
            fill, fg = th.bad, th.ink
        elif self.kind == "ghost":
            fill, fg = th.card, th.fg
        else:
            fill, fg = th.card_hi, th.fg
        if not self.enabled:
            fg = th.muted
        rrect(d, self.box, 10, fill=fill)
        f = T.font(self.font_size, bold=True)
        tw = d.textlength(self.label, font=f)
        d.text(((x0 + x1) / 2 - tw / 2, (y0 + y1) / 2 - self.font_size / 2 - 2),
               self.label, font=f, fill=fg)


# QWERTY layout for the on-screen keyboard; last row handled specially.
_ROWS_LOWER = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_ROWS_UPPER = [r.upper() for r in _ROWS_LOWER]
_ROWS_NUM = ["1234567890", "-/:;()$&@\"", ".,?!'#%*+="]


class Keyboard:
    """Modal text entry. Call draw(); feed taps to tap(); read .text.
    on_done(text) called on Enter, on_cancel() on Cancel.
    """

    def __init__(self, w, h, title="Password", secret=True,
                 on_done=None, on_cancel=None):
        self.w, self.h = w, h
        self.title = title
        self.secret = secret
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.text = ""
        self.shift = False
        self.numeric = False
        self.reveal = False
        self._keys = []          # (box, action) built each draw

    def _rows(self):
        if self.numeric:
            return _ROWS_NUM
        return _ROWS_UPPER if self.shift else _ROWS_LOWER

    def draw(self, d, th):
        w, h = self.w, self.h
        d.rectangle((0, 0, w, h), fill=th.bg)
        # entry field
        d.text((12, 8), self.title, font=T.font(16, bold=True), fill=th.muted)
        field = (10, 30, w - 10, 66)
        rrect(d, field, 8, fill=th.card, outline=th.accent, width=1)
        shown = self.text if (self.reveal or not self.secret) else "*" * len(self.text)
        d.text((18, 40), shown + "_", font=T.font(20, mono=True), fill=th.fg)

        self._keys = []
        rows = self._rows()
        top = 74
        kh = 40
        gap = 4
        for ri, row in enumerate(rows):
            n = len(row)
            kw = (w - gap) / n - gap
            y0 = top + ri * (kh + gap)
            for ci, ch in enumerate(row):
                x0 = gap + ci * (kw + gap)
                box = (x0, y0, x0 + kw, y0 + kh)
                rrect(d, box, 6, fill=th.card_hi)
                f = T.font(20, bold=True)
                tw = d.textlength(ch, font=f)
                d.text((x0 + kw / 2 - tw / 2, y0 + 8), ch, font=f, fill=th.fg)
                self._keys.append((box, ("char", ch)))

        # bottom control row
        y0 = top + len(rows) * (kh + gap)
        ctrls = [
            ("123" if not self.numeric else "abc", ("mode",), th.card_hi, 0.16),
            ("shift", ("shift",), th.accent if self.shift else th.card_hi, 0.16),
            ("space", ("char", " "), th.card_hi, 0.30),
            ("del", ("back",), th.card_hi, 0.14),
            ("OK", ("done",), th.ok, 0.24),
        ]
        x = gap
        for label, action, fill, frac in ctrls:
            kw = (w - gap) * frac - gap
            box = (x, y0, x + kw, y0 + kh)
            rrect(d, box, 6, fill=fill)
            f = T.font(16, bold=True)
            tw = d.textlength(label, font=f)
            d.text((x + kw / 2 - tw / 2, y0 + 10), label, font=f, fill=th.ink
                   if fill in (th.ok, th.accent) else th.fg)
            self._keys.append((box, action))
            x += kw + gap

        # cancel strip
        cy = y0 + kh + gap
        cbox = (gap, cy, w - gap, min(cy + 34, h - 2))
        rrect(d, cbox, 6, fill=th.card)
        f = T.font(15, bold=True)
        lbl = "Cancel"
        tw = d.textlength(lbl, font=f)
        d.text((w / 2 - tw / 2, cy + 8), lbl, font=f, fill=th.muted)
        self._keys.append((cbox, ("cancel",)))

    def tap(self, x, y):
        for box, action in self._keys:
            x0, y0, x1, y1 = box
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._do(action)
                return True
        return False

    def _do(self, action):
        kind = action[0]
        if kind == "char":
            self.text += action[1]
        elif kind == "back":
            self.text = self.text[:-1]
        elif kind == "shift":
            self.shift = not self.shift
        elif kind == "mode":
            self.numeric = not self.numeric
        elif kind == "done":
            if self.on_done:
                self.on_done(self.text)
        elif kind == "cancel":
            if self.on_cancel:
                self.on_cancel()
