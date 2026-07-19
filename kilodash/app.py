"""Main application: a tap-driven launcher + screen router (no swipe nav),
reliable ▲▼ scroll buttons for long lists, a screensaver dimmer, and the modal
keyboard overlay.

Navigation is deliberately all discrete taps: resistive touch (ADS7846) is too
noisy to distinguish a horizontal swipe from a vertical drag reliably, so we
don't try. Home is a tile grid; every other screen has a Back button.
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
from .devices import Devices
from .framebuffer import Framebuffer
from .screens.calibrate import CalibrationScreen
from .touch import Touch

# Resistive touch is noisy. A gesture is a TAP unless the finger's net travel
# from its settled landing point exceeds this; only then is it a scroll/drag.
# One unified threshold removes the tap-vs-scroll ambiguity entirely.
DRAG_SLOP = 30          # px of net travel that separates a tap from a drag
BACK_HIT = (0, 0, 100, 46)

SPLASH_GIF = os.path.join(os.path.dirname(__file__), "..", "ScottinaSplash.gif")
SPLASH_PNG = os.path.join(os.path.dirname(__file__), "..", "ScottinaSplash.png")
SPLASH_SECS = 2.5       # PNG-fallback curtain time; the GIF plays through once


class _FpsMeter:
    """Rolling frame-time stats (KioskSpeedImprovementToDo task 1). Always
    accumulated (it's a few adds); drawn/logged only when `show_fps` is on."""

    def __init__(self):
        self.text = ""
        self._t0 = time.monotonic()
        self._n = 0
        self._compose = 0.0
        self._blit = 0.0
        self._partial = 0

    def frame(self, compose_s, blit_s, partial, log=False):
        self._n += 1
        self._compose += compose_s
        self._blit += blit_s
        self._partial += bool(partial)
        dt = time.monotonic() - self._t0
        if dt < 2.0:
            return
        n = self._n
        self.text = (f"{n / dt:.1f}fps c{self._compose / n * 1000:.0f}"
                     f" b{self._blit / n * 1000:.0f}ms {self._partial}/{n}part")
        if log:
            print(f"[scottina fps] {n / dt:.1f} fps · compose "
                  f"{self._compose / n * 1000:.1f} ms · blit "
                  f"{self._blit / n * 1000:.1f} ms · "
                  f"{self._partial}/{n} partial", flush=True)
        self._t0 = time.monotonic()
        self._n = 0
        self._compose = self._blit = 0.0
        self._partial = 0


class App:
    def __init__(self, screen_classes):
        self.config = Config()
        self.theme = T.Theme(self.config["theme"])
        self.fb = Framebuffer()
        self.w, self.h = self.fb.w, self.fb.h
        self._show_splash()             # curtain up while the rest boots
        self.touch = Touch(self.config, self.w, self.h)
        self.devices = Devices()
        self.devices.refresh(force=True)
        self.screens = [cls(self) for cls in screen_classes]
        self.launcher = self.screens[0]
        self.calibration = CalibrationScreen(self)
        # Force calibration on first run (or after a config wipe).
        self.current = (self.launcher if self.config["touch_calibrated"]
                        else self.calibration)
        self.running = True
        self._fps = _FpsMeter()
        self.dirty = True

        self._g = None                  # in-progress gesture
        self._up_btn = self._down_btn = None

        self.last_activity = time.monotonic()
        self.dimmed = False
        self.keyboard = None
        self._toast = None
        self._flash = None              # (start, until, period) full-screen attention flash
        self._overlay = None            # (drawer, start, until) animated interstitial
        self.backlight = self._find_backlight()

        # Micro KVM off-grid command plane (MICROKVM-PROTOCOL.md). The BLE/
        # arm-gate threads live in microkvm/; the app only serves two seams:
        # the active-tile name for the status verb, and the tile-switch
        # request the main loop applies on the UI thread (never from BLE).
        self.microkvm = None
        try:
            from microkvm.service import Runtime, tile_slug
            self.microkvm = Runtime(self.config["microkvm"],
                                    screens=self.screens).start()
            self.microkvm.wire_ui(lambda: tile_slug(self.current.title))
        except Exception as e:          # noqa: BLE001 — plane is optional
            print(f"microkvm: not started: {e}", file=sys.stderr)

        self.current.on_enter()

    # ------------------------------------------------------------ dirty state
    # `dirty = True` (the pattern used all over) means "repaint everything".
    # Only the active screen's tick can narrow that to dirty rects, and a
    # pending full repaint is never downgraded by one.
    @property
    def dirty(self):
        return self._dirty

    @dirty.setter
    def dirty(self, value):
        self._dirty = value
        self._dirty_rects = None

    def _mark_tick_dirty(self, rects):
        """Redraw request from the active screen's tick; rects is a list of
        changed (x0, y0, x1, y1) boxes, or None for a full frame."""
        if self._dirty:
            if rects is None:
                self._dirty_rects = None
            elif self._dirty_rects is not None:
                self._dirty_rects.extend(rects)
            return
        self._dirty = True
        self._dirty_rects = list(rects) if rects is not None else None

    # ----------------------------------------------------------- boot curtain
    def _fit_splash(self, art, resample):
        """Scale a splash frame to fit and center it on a black canvas."""
        scale = min(self.w / art.width, self.h / art.height)
        art = art.resize((round(art.width * scale), round(art.height * scale)),
                         resample)
        img = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        img.paste(art, ((self.w - art.width) // 2, (self.h - art.height) // 2))
        if self.config["flip_180"]:
            img = img.transpose(Image.ROTATE_180)
        return img

    def _show_splash(self):
        """Put the Scottina splash up the moment we own the framebuffer, so
        the boot gap reads as an intentional curtain. With the animated GIF
        present, only its first frame goes up here (keeps __init__ fast);
        run() plays the rest through once — tap to skip — before the first
        real frame. Without it, the PNG holds for SPLASH_SECS."""
        self._splash_until = 0.0
        self._splash_gif = None
        try:
            gif = Image.open(SPLASH_GIF)
            self.fb.blit(self._fit_splash(gif.convert("RGB"), Image.BILINEAR))
            self._splash_gif = gif
            return
        except (OSError, ValueError):
            pass
        try:
            art = Image.open(SPLASH_PNG).convert("RGB")
        except (OSError, ValueError):
            return
        self.fb.blit(self._fit_splash(art, Image.LANCZOS))
        self._splash_until = time.monotonic() + SPLASH_SECS

    def _hold_splash(self):
        if self._splash_gif is not None:
            self._play_splash_gif()
            return
        while self.running and time.monotonic() < self._splash_until:
            if any(kind == "down" for kind, _x, _y in self.touch.poll()):
                return                   # tap lifts the curtain early
            self._read_keyboard_quit()
            time.sleep(0.05)

    def _play_splash_gif(self):
        """Play the animated splash through once at its authored frame
        timing; a tap (or q/Esc) skips it. BILINEAR keeps decode+resize
        inside the ~40 ms frame budget (LANCZOS doesn't on this Pi); if a
        frame still runs late we drop its blit rather than let the whole
        animation drag."""
        gif = self._splash_gif
        self._splash_gif = None
        try:
            frames = gif.n_frames
        except (AttributeError, OSError):
            frames = 1
        due = time.monotonic() + gif.info.get("duration", 40) / 1000
        try:
            for i in range(1, frames):   # frame 0 is already on the panel
                while self.running:
                    if any(k == "down" for k, _x, _y in self.touch.poll()):
                        return           # tap lifts the curtain early
                    self._read_keyboard_quit()
                    remaining = due - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.02, remaining))
                if not self.running:
                    return
                # seek even when the blit is skipped: GIF frames composite
                gif.seek(i)
                duration = gif.info.get("duration", 40) / 1000
                behind = time.monotonic() - due
                if behind <= duration:
                    self.fb.blit(self._fit_splash(gif.convert("RGB"),
                                                  Image.BILINEAR))
                due += duration
        except (OSError, ValueError):
            pass                         # truncated GIF: just start the UI
        finally:
            gif.close()

    # -------------------------------------------------------------- navigation
    def is_launcher(self, scr):
        return scr is self.launcher

    def open_screen(self, scr):
        if scr is self.current:
            return
        self.current.on_leave()
        scr.scroll = 0
        scr.on_enter()
        self.current = scr
        self.dirty = True

    def go_home(self):
        self.open_screen(self.launcher)

    def open_named_screen(self):
        """Dev seam: KILODASH_OPEN=<title-slug> (e.g. `signal-k`) jumps
        straight to that screen after the splash, so a UI change can be
        eyeballed over SSH without tapping the panel."""
        want = os.environ.get("KILODASH_OPEN", "").strip().lower()
        if not want:
            return
        for scr in self.screens:
            if scr.title.lower().replace(" ", "-") == want and scr.available():
                self.open_screen(scr)
                return
        print(f"KILODASH_OPEN: no screen '{want}'", file=sys.stderr)

    def open_calibration(self):
        self.calibration.reset()
        self.open_screen(self.calibration)

    # -------------------------------------------------------------- backlight
    def _find_backlight(self):
        base = "/sys/class/backlight"
        try:
            for name in os.listdir(base):
                if os.path.exists(os.path.join(base, name, "brightness")):
                    return os.path.join(base, name)
        except OSError:
            pass
        return None

    def _set_backlight(self, pct):
        if not self.backlight:
            return
        try:
            mx = int(open(os.path.join(self.backlight, "max_brightness")).read())
            with open(os.path.join(self.backlight, "brightness"), "w") as f:
                f.write(str(max(0, min(mx, round(mx * pct / 100)))))
        except OSError:
            pass

    # --------------------------------------------------------------- keyboard
    def open_keyboard(self, kb):
        self.keyboard = kb
        self.dirty = True

    def close_keyboard(self):
        self.keyboard = None
        self.dirty = True

    # ------------------------------------------------------------------ toast
    def toast(self, msg, secs=2.5):
        self._toast = (msg, time.monotonic() + secs)
        self.dirty = True

    # ------------------------------------------------------------------ flash
    def flash(self, times=3, period=0.18):
        """Blink the whole screen a few times to grab attention (no speaker).
        Wakes the dimmer so it's visible even if the screen had gone idle. Safe
        to call from a background thread."""
        now = time.monotonic()
        self._flash = (now, now + times * 2 * period, period)
        self.dimmed = False
        self._set_backlight(100)
        self.last_activity = now
        self.dirty = True

    # ---------------------------------------------------------------- overlay
    def show_overlay(self, drawer, secs=3.0):
        """Full-screen animated interstitial drawn over whatever screen is up
        (e.g. the Pomodoro phase-change splash). `drawer(img, th, t)` paints
        one frame, t = seconds since the overlay went up. Wakes the dimmer;
        a tap dismisses it early. Safe to call from a background thread."""
        now = time.monotonic()
        self._overlay = (drawer, now, now + secs)
        self.dimmed = False
        self._set_backlight(100)
        self.last_activity = now
        self.dirty = True

    # -------------------------------------------------------------- dimming
    def _wake(self):
        self.last_activity = time.monotonic()
        if self.dimmed:
            self.dimmed = False
            self._set_backlight(100)
            self.dirty = True
            return True
        return False

    def _update_dim(self):
        if not self.config["dim_enabled"]:
            if self.dimmed:
                self.dimmed = False
                self.dirty = True
            return
        if not self.dimmed and \
                time.monotonic() - self.last_activity >= self.config["dim_timeout_sec"]:
            self.dimmed = True
            self._set_backlight(self.config["dim_level"])
            self.dirty = True

    # ------------------------------------------------------ scroll-button geom
    def _scroll_buttons(self):
        """Return (up_rect, down_rect) if the current screen can scroll, else None."""
        scr = self.current
        if not getattr(scr, "scrollable", False):
            return None
        view_h = scr.content_area()[3]
        if scr.content_h <= view_h:
            return None
        w, h = self.w, self.h
        up = (w - 60, h - 124, w - 8, h - 70)
        down = (w - 60, h - 64, w - 8, h - 10)
        return up, down

    def _scroll_page(self, direction):
        scr = self.current
        step = int(scr.content_area()[3] * 0.7)
        if scr.scroll_by(direction * step):
            self.dirty = True

    # ------------------------------------------------------------- gestures
    def _on_down(self, x, y):
        # n counts move samples so we can discard the noisy touchdown sample.
        self._g = {"sx": x, "sy": y, "lx": x, "ly": y, "n": 0, "scrolling": False}

    def _on_move(self, x, y):
        g = self._g
        if not g or self.keyboard is not None:
            return
        if getattr(self.current, "capture_all_taps", False):
            return                       # calibration: never scroll
        g["n"] += 1
        if g["n"] == 1:
            # Resistive panels report a bogus first position as the finger
            # settles. Re-baseline the gesture start to this settled point.
            g["sx"], g["sy"] = x, y
            g["lx"], g["ly"] = x, y
            return
        dx, dy = x - g["sx"], y - g["sy"]
        if not g["scrolling"] and (dx * dx + dy * dy) ** 0.5 > DRAG_SLOP \
                and abs(dy) >= abs(dx):
            g["scrolling"] = True
            g["ly"] = y                  # avoid a jump when scrolling engages
        if g["scrolling"] and self.current.scroll_by(g["ly"] - y):
            self.dirty = True
        g["lx"], g["ly"] = x, y

    def _on_up(self, x, y):
        g = self._g
        self._g = None
        if not g:
            return
        if self.keyboard is not None:
            if abs(x - g["sx"]) + abs(y - g["sy"]) <= DRAG_SLOP:
                self.keyboard.tap(g["lx"], g["ly"])
                self.dirty = True
            return
        if getattr(self.current, "capture_all_taps", False):
            if self.current.handle_tap(x, y):
                self.dirty = True
            return
        # Classify by NET displacement at release, not transient jitter.
        dx, dy = x - g["sx"], y - g["sy"]
        if not g["scrolling"] and (dx * dx + dy * dy) ** 0.5 <= DRAG_SLOP:
            self._dispatch_tap(g["lx"], g["ly"])

    def _dispatch_tap(self, x, y):
        # Back button (every screen except the launcher)
        if not self.is_launcher(self.current):
            bx0, by0, bx1, by1 = BACK_HIT
            if bx0 <= x <= bx1 and by0 <= y <= by1:
                self.go_home()
                return
        # scroll buttons
        btns = self._scroll_buttons()
        if btns:
            up, down = btns
            if up[0] <= x <= up[2] and up[1] <= y <= up[3]:
                self._scroll_page(-1)
                return
            if down[0] <= x <= down[2] and down[1] <= y <= down[3]:
                self._scroll_page(+1)
                return
        # screen-specific
        if self.current.handle_tap(x, y):
            self.dirty = True

    # ------------------------------------------------------------- rendering
    def _compose(self):
        th = self.theme
        if self.keyboard is not None:
            img = Image.new("RGB", (self.w, self.h), th.bg)
            self.keyboard.draw(ImageDraw.Draw(img), th)
        else:
            img = self.current.render()
            self._draw_scroll_buttons(img)

        if self._toast:
            msg, until = self._toast
            if time.monotonic() < until:
                self._draw_toast(img, msg)
            else:
                self._toast = None

        if self._overlay:
            drawer, start, until = self._overlay
            now = time.monotonic()
            if now < until:
                drawer(img, th, now - start)
            else:
                self._overlay = None     # expiry frame is already a full repaint

        if self._flash:
            start, until, period = self._flash
            now = time.monotonic()
            if now >= until:
                self._flash = None
            elif int((now - start) / period) % 2 == 0:
                img = Image.new("RGB", (self.w, self.h), th.fg)   # bright flash frame

        if self.config["show_fps"] and self._fps.text:
            fx0, fy0, fx1, fy1 = self._fps_rect()
            d = ImageDraw.Draw(img)
            d.rectangle((fx0, fy0, fx1, fy1), fill=th.card)
            d.text((fx0 + 4, fy0 + 2), self._fps.text,
                   font=T.font(11, mono=True), fill=th.warn)

        if self.config["flip_180"]:
            img = img.transpose(Image.ROTATE_180)
        if self.dimmed and not self.backlight:
            factor = max(0.05, self.config["dim_level"] / 100)
            img = ImageEnhance.Brightness(img).enhance(factor)
        return img

    def _draw_scroll_buttons(self, img):
        btns = self._scroll_buttons()
        if not btns:
            return
        th = self.theme
        d = ImageDraw.Draw(img)
        scr = self.current
        view_h = scr.content_area()[3]
        max_scroll = max(1, scr.content_h - view_h)
        frac = min(1.0, scr.scroll / max_scroll)
        for rect, sym, active in (
                (btns[0], "▲", scr.scroll > 0),
                (btns[1], "▼", scr.scroll < max_scroll - 1)):
            d.rounded_rectangle(rect, radius=12, fill=th.card_hi)
            f = T.font(24, bold=True)
            tw = d.textlength(sym, font=f)
            cx = (rect[0] + rect[2]) / 2
            cy = (rect[1] + rect[3]) / 2
            d.text((cx - tw / 2, cy - 16), sym, font=f,
                   fill=th.accent if active else th.muted)
        # position hint between the buttons
        d.text((btns[0][0] + 20, (btns[0][3] + btns[1][1]) / 2 - 7),
               f"{int(frac * 100)}%", font=T.font(12, bold=True), fill=th.muted)

    def _draw_toast(self, img, msg):
        th = self.theme
        d = ImageDraw.Draw(img)
        f = T.font(16, bold=True)
        tw = d.textlength(msg, font=f)
        bw = min(self.w - 20, tw + 28)
        x0 = (self.w - bw) / 2
        y0 = self.h - 76
        d.rounded_rectangle((x0, y0, x0 + bw, y0 + 40), radius=10, fill=th.card_hi)
        d.text((self.w / 2 - tw / 2, y0 + 11), msg, font=f, fill=th.fg)

    # ------------------------------------------------------------------- loop
    def _read_keyboard_quit(self):
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
        self._hold_splash()
        self.open_named_screen()
        while self.running:
            for kind, x, y in self.touch.poll():
                if kind == "down":
                    if self._overlay:        # tap anywhere lifts the interstitial
                        self._overlay = None
                        self._g = None
                        self._wake()
                        self.dirty = True
                        continue
                    if self._wake():
                        self._g = None
                        continue
                    self._on_down(x, y)
                elif kind == "move":
                    self._on_move(x, y)
                elif kind == "up":
                    self._on_up(x, y)

            self._read_keyboard_quit()
            self._update_dim()

            # Micro KVM `tile` verb: apply a pending switch on the UI thread
            if self.microkvm:
                scr = self.microkvm.take_tile_request()
                if scr:
                    self._wake()
                    self.open_screen(scr)

            # hotplug: if the device behind the current screen was unplugged,
            # bail back to Home so we don't sit on a dead screen.
            self.devices.refresh()
            if self.current.device_key and \
                    not self.devices.has(self.current.device_key):
                self.go_home()

            if not self.dimmed and self.keyboard is None:
                if self.current.maybe_tick():
                    self._mark_tick_dirty(self.current.take_dirty_rects())

            # expired toast: force a full repaint that erases it (a partial
            # blit would leave it on the panel forever)
            if self._toast and time.monotonic() >= self._toast[1]:
                self._toast = None
                self.dirty = True

            if self._flash or self._overlay:   # keep redrawing so animations run
                self.dirty = True

            if self._dirty:
                self._render_frame()

            # Sleep the active screen's cadence (fast screens tick ~20 Hz)
            # but never more than 50 ms, so touch stays responsive; slow
            # screens keep today's rate — they aren't woken any more often.
            # Dimmed / keyboard-covered screens don't tick, so don't spin.
            if self._g:
                time.sleep(0.02)
            elif self.dimmed or self.keyboard is not None:
                time.sleep(0.05)
            else:
                time.sleep(min(0.05, max(0.01, self.current.tick_interval / 2)))

    def _fps_rect(self):
        return (0, self.h - 16, 190, self.h)

    def _render_frame(self):
        t0 = time.perf_counter()
        img = self._compose()
        t1 = time.perf_counter()
        rects = self._dirty_rects
        if rects is not None:
            if self.config["show_fps"]:
                rects = rects + [self._fps_rect()]
            if self.config["flip_180"]:
                # boxes are y1-inclusive: row y lands on h-1-y after rotation
                rects = [(self.w - 1 - x1, self.h - 1 - y1,
                          self.w - 1 - x0, self.h - 1 - y0)
                         for (x0, y0, x1, y1) in rects]
        self.fb.blit(img, rects)
        self._fps.frame(t1 - t0, time.perf_counter() - t1,
                        partial=rects is not None,
                        log=self.config["show_fps"])
        self.dirty = False
