"""Base class for all screens. A screen owns a scrollable content area and
draws its own header (title + page dots + clock).
"""

import time

from PIL import Image, ImageDraw

from .. import theme as T

HEADER_H = 44


class Screen:
    title = "Screen"
    icon = ""
    glyph = None               # launcher pictogram key (see pictograms.py)
    scrollable = False
    device_key = None          # set on hotplug screens; tile shows only if present

    def __init__(self, app):
        self.app = app
        self.scroll = 0
        self.content_h = 0        # set by draw_content when scrollable
        self._last_tick = 0.0
        self.tick_interval = 3.0
        self._dirty_rects = None  # boxes this tick changed; None = full frame

    # ---- availability ----
    def available(self):
        """Whether this screen should be offered on the launcher right now.
        Device screens also gate on `device_key`; web-app screens override this
        to hide until their backing app is installed. Default: always shown."""
        return True

    # ---- lifecycle ----
    def on_enter(self):
        pass

    def on_leave(self):
        pass

    def tick(self):
        """Refresh cached data; return True if the screen should redraw."""
        return False

    def maybe_tick(self):
        now = time.monotonic()
        if now - self._last_tick >= self.tick_interval:
            self._last_tick = now
            self._dirty_rects = None
            return self.tick()
        return False

    def report_dirty(self, *rects):
        """Declare that this tick only changed these (x0, y0, x1, y1) boxes
        (full-screen coordinates). Call from tick() before returning True and
        only the covering row bands get written to the panel — the win that
        makes fast tick rates affordable on the SPI framebuffer. Not calling
        this keeps the default: the frame is blitted in full."""
        if self._dirty_rects is None:
            self._dirty_rects = []
        self._dirty_rects.extend(rects)

    def take_dirty_rects(self):
        """Consume this tick's reported boxes (None = full frame)."""
        rects = self._dirty_rects
        self._dirty_rects = None
        return rects

    # ---- input ----
    def handle_tap(self, x, y):
        """Return True if the tap was consumed (forces redraw)."""
        return False

    def scroll_by(self, dy):
        if not self.scrollable:
            return False
        max_scroll = max(0, self.content_h - self.content_area()[3])
        self.scroll = min(max(0, self.scroll + dy), max_scroll)
        return True

    def content_area(self):
        return (0, HEADER_H, self.app.w, self.app.h)

    # ---- rendering ----
    def render(self):
        w, h = self.app.w, self.app.h
        th = self.app.theme
        img = Image.new("RGB", (w, h), th.bg)
        self._img = img
        d = ImageDraw.Draw(img)
        self.draw_content(d, th)      # content first…
        self._draw_header(d, th)      # …header always on top
        return img

    def paste_list(self, top, height, content_img):
        """Paste a scrollable content surface clipped to [top, top+height)."""
        crop = content_img.crop((0, self.scroll, self.app.w,
                                 self.scroll + height))
        self._img.paste(crop, (0, top))

    def _draw_header(self, d, th):
        # ship-instrument chrome: hard-edged strip, caps-mono readouts,
        # hairline rule separating chrome from the instrument area below
        w = self.app.w
        d.rectangle((0, 0, w, HEADER_H), fill=th.card)
        d.rectangle((0, HEADER_H - 2, w, HEADER_H), fill=th.card_hi)
        if self.app.is_launcher(self) or getattr(self, "capture_all_taps", False):
            d.text((14, 11), self.title.upper(),
                   font=T.font(20, bold=True, mono=True), fill=th.fg)
        else:
            # Back button (hit-box lives in app.BACK_HIT)
            d.text((10, 6), "‹", font=T.font(32, bold=True), fill=th.accent)
            d.text((32, 15), "BACK", font=T.font(14, bold=True, mono=True),
                   fill=th.accent)
            f = T.font(16, bold=True, mono=True)
            title = self.title.upper()
            tw = d.textlength(title, font=f)
            d.text((w - tw - 14, 14), title, font=f, fill=th.fg)
            return
        if self.app.config["show_clock"]:
            clk = time.strftime("%H:%M")
            f = T.font(16, bold=True, mono=True)
            tw = d.textlength(clk, font=f)
            d.text((w - tw - 14, 14), clk, font=f, fill=th.muted)

    def draw_content(self, d, th):
        raise NotImplementedError
