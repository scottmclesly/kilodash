"""Settings: every adjustable variable as a tile, laid out two per row.
Booleans toggle, integers step, choices cycle. Also power actions (restart
UI / reboot / shutdown) and a touch calibration helper.

The tile column deliberately stops short of the right edge: the app-level
▲▼ scroll buttons live in a reserved gutter there, so no control can ever
sit under the scroller and steal (or lose) a tap.
"""

import subprocess

from PIL import Image, ImageDraw

from .. import __version__
from .. import theme as T
from ..widgets import spaced, status_square
from .base import Screen, HEADER_H

MARGIN = 8        # left edge of the tile column
GUTTER_W = 64     # right-edge strip reserved for the app's ▲▼ scroller
GAP = 6           # spacing between tiles
TILE_H = 84       # label (≤2 lines) on top, control zone at the bottom
HEAD_H = 26       # group header band

# Order the setting groups top-to-bottom. Anything not listed falls after these
# (but before the Power actions). Touch sits last — it's rarely changed.
GROUP_ORDER = ["System", "Display", "Touch"]


class SettingsScreen(Screen):
    title = "Settings"
    tile_id = "settings"
    glyph = "settings"
    tile_color_key = "muted"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 60
        self._hits = []          # (x0,y0,x1,y1, callback)

    def content_area(self):
        return (0, HEADER_H, self.app.w, self.app.h - HEADER_H)

    def _cycle_choice(self, key, spec):
        opts = spec["options"]
        i = (opts.index(spec["value"]) + 1) % len(opts)
        self.app.config.set(key, opts[i])
        if key == "theme":
            self.app.theme.set(opts[i])

    def _step(self, key, spec, direction):
        v = spec["value"] + direction * spec.get("step", 1)
        v = max(spec.get("min", 0), min(spec.get("max", 999999), v))
        self.app.config.set(key, v)

    def _toggle(self, key, spec):
        self.app.config.set(key, not spec["value"])

    def _power(self, cmd):
        subprocess.Popen(cmd)

    def _sections(self):
        """(title, caption, [tile...]) — tile = (kind, a, b)."""
        cfg = self.app.config
        sections = []
        groups = sorted(cfg.groups().items(),
                        key=lambda kv: (GROUP_ORDER.index(kv[0])
                                        if kv[0] in GROUP_ORDER else len(GROUP_ORDER)))
        for group, items in groups:
            visible = [(k, s) for k, s in items if s.get("type") != "hidden"]
            if not visible:
                continue
            tiles = [("setting", k, s) for k, s in visible]
            caption = None
            if group == "Touch":
                tiles.append(("action", "Calibrate",
                              lambda: self.app.open_calibration()))
                caption = "Taps landing wrong? Flip these or run Calibrate."
            sections.append((group, caption, tiles))
        sections.append(("Power", None, [
            ("action", "Restart UI",
             lambda: self._power(["sudo", "systemctl", "restart", "kilodash"])),
            ("action", "Reboot Pi", lambda: self._power(["sudo", "reboot"])),
            ("action", "Shutdown", lambda: self._power(["sudo", "poweroff"])),
        ]))
        return sections

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H
        self._hits = []

        col_w = w - GUTTER_W                      # tiles never enter the gutter
        tile_w = (col_w - MARGIN - GAP) // 2
        xs = (MARGIN, MARGIN + tile_w + GAP)
        sections = self._sections()

        # measure (tile height is fixed; about card wraps, so pre-wrap it)
        meas = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        body = self._wrap(meas, "Created by Scott McLeslie for the benefit "
                          "of all living beings.", T.font(13), col_w - MARGIN - 24)
        lic = self._wrap(meas, "MIT License · Feel free to share and "
                         "contribute · 2026", T.font(11), col_w - MARGIN - 24)
        about_h = 12 + 26 + len(body) * 17 + 6 + len(lic) * 14 + 12

        surf_h = 4
        for _, caption, tiles in sections:
            surf_h += HEAD_H + (16 if caption else 0)
            surf_h += ((len(tiles) + 1) // 2) * (TILE_H + GAP)
        surf_h += HEAD_H + about_h
        surf_h = max(surf_h + 8, h - top)
        self.content_h = surf_h

        surf = Image.new("RGB", (w, surf_h), th.bg)
        sd = ImageDraw.Draw(surf)
        hits_surf = []           # (x0,y0,x1,y1,cb) in surface space
        y = 4
        for title, caption, tiles in sections:
            sd.text((MARGIN + 6, y + 8), spaced(title.upper()),
                    font=T.font(10, bold=True, mono=True), fill=th.muted)
            y += HEAD_H
            if caption:
                sd.text((MARGIN + 6, y - 4), caption, font=T.font(11),
                        fill=th.muted)
                y += 16
            for i, tile in enumerate(tiles):
                self._draw_tile(sd, th, xs[i % 2], y, tile_w, tile, hits_surf)
                if i % 2 == 1:
                    y += TILE_H + GAP
            if len(tiles) % 2:
                y += TILE_H + GAP

        # About card (spans the tile column, also clear of the scroller)
        sd.text((MARGIN + 6, y + 8), spaced("ABOUT"),
                font=T.font(10, bold=True, mono=True), fill=th.muted)
        y += HEAD_H
        sd.rectangle((MARGIN, y, col_w, y + about_h - 6), fill=th.card,
                     outline=th.card_hi, width=1)
        sd.text((MARGIN + 12, y + 12), f"SCOTTINA v{__version__}",
                font=T.font(15, bold=True, mono=True), fill=th.accent)
        ty = y + 12 + 26
        for ln in body:
            sd.text((MARGIN + 12, ty), ln, font=T.font(13), fill=th.fg)
            ty += 17
        ty += 6
        for ln in lic:
            sd.text((MARGIN + 12, ty), ln, font=T.font(11), fill=th.muted)
            ty += 14

        self.paste_list(top, h - top, surf)

        # offset hitboxes into screen space
        for x0, y0, x1, y1, cb in hits_surf:
            self._hits.append((x0, top + y0 - self.scroll,
                               x1, top + y1 - self.scroll, cb))

    def _draw_tile(self, sd, th, x, y, tw, tile, hits):
        kind, a, b = tile
        sd.rectangle((x, y, x + tw, y + TILE_H), fill=th.card,
                     outline=th.card_hi, width=1)
        if kind == "action":
            # power actions are stand-downs, not faults: amber, never red
            col = th.warn if a in ("Reboot Pi", "Shutdown") else th.accent
            f = T.font(13, bold=True, mono=True)
            lab = a.upper()
            lw = sd.textlength(lab, font=f)
            sd.text((x + (tw - lw) / 2, y + TILE_H / 2 - 8), lab, font=f,
                    fill=col)
            hits.append((x, y, x + tw, y + TILE_H, b))
            return
        key, spec = a, b
        lf = T.font(10, bold=True, mono=True)
        label = spec["label"].upper()
        for i, ln in enumerate(self._wrap(sd, label, lf, tw - 16)[:2]):
            sd.text((x + 8, y + 8 + i * 14), ln, font=lf, fill=th.fg)
        cy = y + TILE_H - 38                      # control zone top
        t = spec["type"]
        if t == "bool":
            self._draw_toggle(sd, th, x, cy + 6, tw, spec["value"])
            hits.append((x, y, x + tw, y + TILE_H,
                         lambda k=key, s=spec: self._toggle(k, s)))
        elif t == "choice":
            val = str(spec["value"]).upper()
            f = T.font(12, bold=True, mono=True)
            vw = sd.textlength(val, font=f)
            bx0 = x + (tw - vw - 20) / 2
            sd.rectangle((bx0, cy, bx0 + vw + 20, cy + 30), fill=th.card_hi)
            sd.text((bx0 + 10, cy + 8), val, font=f, fill=th.accent)
            hits.append((x, y, x + tw, y + TILE_H,
                         lambda k=key, s=spec: self._cycle_choice(k, s)))
        elif t == "int":
            self._stepper_box(sd, th, x + 6, cy, "-")
            self._stepper_box(sd, th, x + tw - 36, cy, "+")
            val = f"{spec['value']}{spec.get('unit', '')}"
            f = T.font(12, bold=True, mono=True)
            vw = sd.textlength(val, font=f)
            sd.text((x + tw / 2 - vw / 2, cy + 9), val, font=f, fill=th.fg)
            # each half of the tile is one big step target
            hits.append((x, y, x + tw // 2, y + TILE_H,
                         lambda k=key, s=spec: self._step(k, s, -1)))
            hits.append((x + tw // 2, y, x + tw, y + TILE_H,
                         lambda k=key, s=spec: self._step(k, s, +1)))

    def _wrap(self, sd, text, font, maxw):
        lines, cur = [], ""
        for word in text.split():
            cand = (cur + " " + word).strip()
            if cur and sd.textlength(cand, font=font) > maxw:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines

    def _draw_toggle(self, d, th, x, y, tw, on):
        # status-square selected-state idiom: lit = on, hollow = off
        # ("on" uses the theme accent, not status-green, to match each skin)
        col = th.accent if on else th.muted
        f = T.font(12, bold=True, mono=True)
        lab = spaced("ON") if on else spaced("OFF")
        lw = d.textlength(lab, font=f)
        sq = 14
        bx = x + (tw - sq - 8 - lw) / 2
        status_square(d, (bx, y, bx + sq, y + sq), "lit" if on else "hollow",
                      col)
        d.text((bx + sq + 8, y), lab, font=f, fill=col)

    def _stepper_box(self, d, th, x, y, sym):
        d.rectangle((x, y, x + 30, y + 30), fill=th.card_hi)
        f = T.font(20, bold=True, mono=True)
        tw = d.textlength(sym, font=f)
        d.text((x + 15 - tw / 2, y + 2), sym, font=f, fill=th.accent)

    def handle_tap(self, x, y):
        for x0, y0, x1, y1, cb in self._hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                cb()
                return True
        return False
