"""Main application: screen manager, gesture recognition, swipe transitions,
dimming/screensaver, and the modal keyboard overlay.
"""

import os
import select
import sys
import termios
import time
import tty

from PIL import Image, ImageDraw, ImageEnhance

from . import theme as T
from .config import Config
from .framebuffer import Framebuffer
from .touch import Touch

# gesture thresholds (pixels)
LOCK = 14           # movement before we commit to swipe vs scroll vs tap
TAP_MAX_MOVE = 16
PAGE_SNAP_FRAC = 0.30


class App:
    def __init__(self, screen_classes):
        self.config = Config()
        self.theme = T.Theme(self.config["theme"])
        self.fb = Framebuffer()
        self.w, self.h = self.fb.w, self.fb.h
        self.touch = Touch(self.config, self.w, self.h)
        self.screens = [cls(self) for cls in screen_classes]
        self.idx = 0
        self.running = True
        self.dirty = True

        # gesture state
        self._g = None
        self.drag_dx = 0        # live horizontal drag during a page swipe
        self._anim = None       # (from_idx, to_idx, start_t, dur, from_dx)

        # dimming
        self.last_activity = time.monotonic()
        self.dimmed = False

        # modal keyboard
        self.keyboard = None

        self.backlight = self._find_backlight()
        self.screens[self.idx].on_enter()

    # ---------------------------------------------------------------- backlight
    def _find_backlight(self):
        base = "/sys/class/backlight"
        try:
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if os.path.exists(os.path.join(p, "brightness")):
                    return p
        except OSError:
            pass
        return None

    def _set_backlight(self, pct):
        if not self.backlight:
            return False
        try:
            mx = int(open(os.path.join(self.backlight, "max_brightness")).read())
            val = max(0, min(mx, round(mx * pct / 100)))
            with open(os.path.join(self.backlight, "brightness"), "w") as f:
                f.write(str(val))
            return True
        except OSError:
            return False

    # ----------------------------------------------------------------- keyboard
    def open_keyboard(self, kb):
        self.keyboard = kb
        self.dirty = True

    def close_keyboard(self):
        self.keyboard = None
        self.dirty = True

    # -------------------------------------------------------------------- toast
    def toast(self, msg, secs=2.5):
        self._toast = (msg, time.monotonic() + secs)
        self.dirty = True

    _toast = None

    # ------------------------------------------------------------------ nav
    def go_to(self, idx, animate=True):
        idx = max(0, min(len(self.screens) - 1, idx))
        if idx == self.idx:
            self.drag_dx = 0
            self.dirty = True
            return
        if animate:
            self._anim = (self.idx, idx, time.monotonic(), 0.18, self.drag_dx)
        self.screens[self.idx].on_leave()
        self.screens[idx].on_enter()
        self.idx = idx
        self.drag_dx = 0
        self.dirty = True

    # -------------------------------------------------------------- activity
    def _wake(self):
        self.last_activity = time.monotonic()
        if self.dimmed:
            self.dimmed = False
            if self.backlight:
                self._set_backlight(100)
            self.dirty = True
            return True    # consumed: this touch only wakes the screen
        return False

    def _update_dim(self):
        if not self.config["dim_enabled"]:
            if self.dimmed:
                self.dimmed = False
                self.dirty = True
            return
        idle = time.monotonic() - self.last_activity
        if not self.dimmed and idle >= self.config["dim_timeout_sec"]:
            self.dimmed = True
            if self.backlight:
                self._set_backlight(self.config["dim_level"])
            self.dirty = True

    # ------------------------------------------------------------- gestures
    def _on_down(self, x, y):
        self._g = {"sx": x, "sy": y, "lx": x, "ly": y,
                   "t": time.monotonic(), "mode": None}

    def _on_move(self, x, y):
        g = self._g
        if not g:
            return
        dx, dy = x - g["sx"], y - g["sy"]
        if g["mode"] is None:
            if abs(dx) > LOCK and abs(dx) >= abs(dy):
                g["mode"] = "h"
            elif abs(dy) > LOCK and abs(dy) > abs(dx):
                g["mode"] = "v"
        if g["mode"] == "h" and self.keyboard is None:
            self.drag_dx = dx
            self.dirty = True
        elif g["mode"] == "v":
            if self.screens[self.idx].scroll_by(g["ly"] - y):
                self.dirty = True
        g["lx"], g["ly"] = x, y

    def _on_up(self, x, y):
        g = self._g
        self._g = None
        if not g:
            return
        dx = x - g["sx"]
        dy = y - g["sy"]
        moved = abs(dx) + abs(dy)
        if self.keyboard is not None:
            if moved <= TAP_MAX_MOVE:
                self.keyboard.tap(x, y)
                self.dirty = True
            return
        if g["mode"] == "h":
            if dx <= -self.w * PAGE_SNAP_FRAC:
                self.go_to(self.idx + 1)
            elif dx >= self.w * PAGE_SNAP_FRAC:
                self.go_to(self.idx - 1)
            else:
                self.drag_dx = 0
                self.dirty = True
        elif g["mode"] == "v":
            pass    # scroll already applied
        elif moved <= TAP_MAX_MOVE:
            if self.screens[self.idx].handle_tap(x, y):
                self.dirty = True

    # ------------------------------------------------------------- rendering
    def _compose(self):
        th = self.theme
        if self.keyboard is not None:
            img = Image.new("RGB", (self.w, self.h), th.bg)
            d = ImageDraw.Draw(img)
            self.keyboard.draw(d, th)
        elif self._anim:
            img = self._render_anim()
        elif self.drag_dx:
            img = self._render_drag(self.drag_dx)
        else:
            img = self.screens[self.idx].render()

        # toast overlay
        if self._toast:
            msg, until = self._toast
            if time.monotonic() < until:
                self._draw_toast(img, msg)
            else:
                self._toast = None

        # software 180 flip
        if self.config["flip_180"]:
            img = img.transpose(Image.ROTATE_180)

        # dim overlay when no hardware backlight control is available
        if self.dimmed and not self.backlight:
            factor = max(0.05, self.config["dim_level"] / 100)
            img = ImageEnhance.Brightness(img).enhance(factor)
        return img

    def _render_drag(self, dx):
        th = self.theme
        img = Image.new("RGB", (self.w, self.h), th.bg)
        cur = self.screens[self.idx].render()
        img.paste(cur, (int(dx), 0))
        neighbor = self.idx + (1 if dx < 0 else -1)
        if 0 <= neighbor < len(self.screens):
            nb = self.screens[neighbor].render()
            off = int(dx + (self.w if dx < 0 else -self.w))
            img.paste(nb, (off, 0))
        return img

    def _render_anim(self):
        fr, to, t0, dur, from_dx = self._anim
        p = (time.monotonic() - t0) / dur
        if p >= 1.0:
            self._anim = None
            return self.screens[self.idx].render()
        # ease out
        p = 1 - (1 - p) ** 2
        direction = 1 if to > fr else -1
        start = from_dx
        end = -direction * self.w
        dx = start + (end - start) * p
        th = self.theme
        img = Image.new("RGB", (self.w, self.h), th.bg)
        img.paste(self.screens[fr].render(), (int(dx), 0))
        img.paste(self.screens[to].render(), (int(dx + direction * self.w), 0))
        return img

    def _draw_toast(self, img, msg):
        th = self.theme
        d = ImageDraw.Draw(img)
        f = T.font(16, bold=True)
        tw = d.textlength(msg, font=f)
        pad = 14
        bw = min(self.w - 20, tw + pad * 2)
        x0 = (self.w - bw) / 2
        y0 = self.h - 70
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + 40), radius=10, fill=th.card_hi)
        d.text((self.w / 2 - tw / 2, y0 + 11), msg, font=f, fill=th.fg)

    # ------------------------------------------------------------------- loop
    def _read_keyboard_quit(self):
        """Allow ESC/q from a USB keyboard to quit during testing."""
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                ch = sys.stdin.read(1)
                if ch in ("\x1b", "q"):
                    self.running = False
        except (OSError, ValueError):
            pass

    def run(self):
        stdin_raw = sys.stdin.isatty()
        old = None
        if stdin_raw:
            try:
                old = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except (termios.error, ValueError):
                stdin_raw = False
        try:
            self._loop()
        finally:
            if stdin_raw and old:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
            self.fb.close()

    def _loop(self):
        while self.running:
            for kind, x, y in self.touch.poll():
                if kind == "down":
                    if self._wake():          # first touch after dim only wakes
                        self._g = None
                        continue
                    self._wake()
                    self._on_down(x, y)
                elif kind == "move":
                    self._on_move(x, y)
                elif kind == "up":
                    self._on_up(x, y)

            self._read_keyboard_quit()
            self._update_dim()

            # periodic data refresh for the visible screen
            if not self.dimmed and self.keyboard is None:
                if self.screens[self.idx].maybe_tick():
                    self.dirty = True

            animating = self._anim is not None
            if self.dirty or animating:
                self.fb.blit(self._compose())
                self.dirty = False

            time.sleep(0.02 if (animating or self.drag_dx or self._g) else 0.05)
