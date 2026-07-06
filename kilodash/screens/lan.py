"""LAN Scan — DIAGNOSTICS ONLY. See LAN-Scan-Refactor-TODO.md for full scope.

This screen answers only: what's alive on my subnet, what services/versions do
hosts run, and is an expected port open on a known host. It is *physically
incapable* of expressing an offensive scan: there is no raw-flag input. The
mode segmented control (Discover · Ports · Services · Identify) is the safety
boundary — every command is assembled from that intent by scan.build_scan_command,
which refuses NSE (--script/-sC), stealth/evasion scans, decoys and spoofing.
No evasion, no NSE, no vuln probing, no spoofing is reachable from here.
"""

from PIL import Image, ImageDraw

from .. import scan, theme as T
from ..widgets import Button, Keyboard, rrect
from .base import Screen, HEADER_H

# Fixed layout bands (480×320). Controls up top, scrolling output below.
TARGET_Y = HEADER_H + 6          # target field + Run/Stop row
FIELD_H = 38
MODE_Y = TARGET_Y + FIELD_H + 6  # mode segmented control
SEG_H = 36
CTRL_Y = MODE_Y + SEG_H + 6      # ports field (Ports mode) / status + host badge
OUT_TOP = CTRL_Y + FIELD_H + 6   # scrolling output pane
LINE_H = 22


class LanScreen(Screen):
    title = "LAN Scan"
    glyph = "lan"
    tile_color_key = "accent"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 0.3        # responsive while output streams
        self.mode = "Discover"
        self.target = ""
        self.ports = ""
        self.job = None
        self._done_handled = True
        self.status = "Set a target and tap Run"
        # hit boxes recorded each draw
        self._target_box = None
        self._ports_box = None
        self._seg_boxes = []
        self.run_btn = None

    def on_enter(self):
        if not self.target:
            self.target = scan.default_target()

    # ---- scanning ----
    def start_scan(self):
        if not self.target:
            self.status = "Enter a target first"
            self.app.toast("Enter a target first")
            return
        self.scroll = 0
        self._done_handled = False
        self.job = scan.ScanJob(self.mode, self.target, self.ports)

    def stop_scan(self):
        if self.job and not self.job.done:
            self.job.stop()

    def _scanning(self):
        return self.job is not None and not self.job.done

    def tick(self):
        j = self.job
        if j is None:
            return False
        if not j.done:
            return True                 # redraw to show streamed rows
        if not self._done_handled:
            self._done_handled = True
            return True
        return False

    def content_area(self):
        return (0, OUT_TOP, self.app.w, self.app.h - OUT_TOP)

    # ---- rendering ----
    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._draw_output(d, th, w, h)
        # controls drawn on top of the (cleared) upper band
        d.rectangle((0, HEADER_H, w, OUT_TOP), fill=th.bg)
        self._draw_target_row(d, th, w)
        self._draw_mode_control(d, th, w)
        self._draw_ctrl_row(d, th, w)

    def _draw_target_row(self, d, th, w):
        run_w = 96
        self._target_box = (12, TARGET_Y, w - run_w - 18, TARGET_Y + FIELD_H)
        rrect(d, self._target_box, 9, fill=th.card, outline=th.accent, width=1)
        label = self.target or "tap to set target (IP / host / CIDR)"
        fill = th.fg if self.target else th.muted
        d.text((22, TARGET_Y + 10), label[:34],
                font=T.font(16, mono=bool(self.target)), fill=fill)
        scanning = self._scanning()
        self.run_btn = Button((w - run_w - 6, TARGET_Y, w - 8, TARGET_Y + FIELD_H),
                              "Stop" if scanning else "Run",
                              kind="danger" if scanning else "primary",
                              font_size=17)
        self.run_btn.draw(d, th)

    def _draw_mode_control(self, d, th, w):
        self._seg_boxes = []
        n = len(scan.MODES)
        gap = 6
        seg_w = (w - 24 - gap * (n - 1)) / n
        for i, m in enumerate(scan.MODES):
            x0 = 12 + i * (seg_w + gap)
            box = (x0, MODE_Y, x0 + seg_w, MODE_Y + SEG_H)
            active = (m == self.mode)
            rrect(d, box, 8, fill=th.accent if active else th.card,
                  outline=th.card_hi, width=1)
            f = T.font(14, bold=active)
            tw = d.textlength(m, font=f)
            d.text((x0 + seg_w / 2 - tw / 2, MODE_Y + 9), m, font=f,
                   fill=th.ink if active else th.muted)
            self._seg_boxes.append((box, m))

    def _draw_ctrl_row(self, d, th, w):
        badge_w = 120
        # host-count badge (right)
        count = self.job.host_count if self.job else 0
        badge = (w - badge_w - 6, CTRL_Y, w - 8, CTRL_Y + FIELD_H)
        rrect(d, badge, 9, fill=th.card)
        d.text((badge[0] + 12, CTRL_Y + 10), f"{count} host(s)",
               font=T.font(15, bold=True), fill=th.accent)

        if self.mode == "Ports":
            # ports field (left) — visible only in Ports mode
            self._ports_box = (12, CTRL_Y, w - badge_w - 18, CTRL_Y + FIELD_H)
            rrect(d, self._ports_box, 9, fill=th.card, outline=th.accent, width=1)
            shown = self.ports or f"common: {scan.COMMON_PORTS[:22]}…"
            fill = th.fg if self.ports else th.muted
            d.text((22, CTRL_Y + 11), shown[:30],
                   font=T.font(14, mono=True), fill=fill)
        else:
            self._ports_box = None
            status = self.job.status if self.job else self.status
            rrect(d, (12, CTRL_Y, w - badge_w - 18, CTRL_Y + FIELD_H), 9, fill=th.card)
            d.text((22, CTRL_Y + 11), status[:30], font=T.font(14), fill=th.muted)

    def _draw_output(self, d, th, w, h):
        lines = self.job.snapshot() if self.job else []
        if not lines:
            hint = self.status if not self.job else self.job.status
            self.content_h = h - OUT_TOP
            d.rectangle((0, OUT_TOP, w, h), fill=th.bg)
            d.text((22, OUT_TOP + 16), hint, font=T.font(15), fill=th.muted)
            return
        self.content_h = max(len(lines) * LINE_H + 8, h - OUT_TOP)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, (indent, text, color) in enumerate(lines):
            y = i * LINE_H
            x = 20 + indent * 22
            col = getattr(th, color, th.fg)
            bold = indent == 0
            sd.text((x, y), text, font=T.font(14, bold=bold, mono=True), fill=col)
        self.paste_list(OUT_TOP, h - OUT_TOP, surf)

    # ---- input ----
    def handle_tap(self, x, y):
        if self.run_btn and self.run_btn.hit(x, y):
            if self._scanning():
                self.stop_scan()
            else:
                self.start_scan()
            return True
        for box, m in self._seg_boxes:
            if self._in(box, x, y):
                self.mode = m
                return True
        if self._in(self._target_box, x, y):
            self._edit_target()
            return True
        if self._ports_box and self._in(self._ports_box, x, y):
            self._edit_ports()
            return True
        return False

    @staticmethod
    def _in(box, x, y):
        if not box:
            return False
        x0, y0, x1, y1 = box
        return x0 <= x <= x1 and y0 <= y <= y1

    def _edit_target(self):
        kb = Keyboard(self.app.w, self.app.h, title="Target (IP / host / CIDR)",
                      secret=False,
                      on_done=self._set_target, on_cancel=self.app.close_keyboard)
        kb.text = self.target
        self.app.open_keyboard(kb)

    def _set_target(self, text):
        self.app.close_keyboard()
        text = text.strip()
        if text and not scan._valid_target(text):
            self.app.toast("Invalid target")
            return
        self.target = text

    def _edit_ports(self):
        kb = Keyboard(self.app.w, self.app.h, title="Ports (blank = common)",
                      secret=False,
                      on_done=self._set_ports, on_cancel=self.app.close_keyboard)
        kb.text = self.ports
        kb.numeric = True
        self.app.open_keyboard(kb)

    def _set_ports(self, text):
        self.app.close_keyboard()
        text = text.strip()
        if text and not scan._valid_ports(text):
            self.app.toast("Ports: digits, commas, hyphens only")
            return
        self.ports = text
