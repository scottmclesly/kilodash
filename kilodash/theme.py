"""Colour palettes and a small cached font factory."""

import os

from PIL import ImageFont

FONT_DIR = "/usr/share/fonts/truetype/dejavu"

PALETTES = {
    "dark": {
        "bg": (16, 18, 22), "card": (28, 32, 38), "card_hi": (38, 43, 51),
        "fg": (223, 227, 231), "muted": (120, 128, 140),
        "accent": (60, 170, 255), "ok": (40, 200, 120),
        "warn": (240, 180, 60), "bad": (224, 70, 70), "ink": (10, 12, 14),
    },
    "midnight": {
        "bg": (8, 10, 20), "card": (18, 22, 40), "card_hi": (28, 34, 58),
        "fg": (210, 220, 240), "muted": (100, 112, 150),
        "accent": (120, 130, 255), "ok": (60, 210, 160),
        "warn": (240, 200, 90), "bad": (240, 90, 120), "ink": (6, 8, 16),
    },
    "amber": {
        "bg": (18, 14, 8), "card": (34, 26, 14), "card_hi": (46, 36, 20),
        "fg": (245, 224, 190), "muted": (150, 126, 90),
        "accent": (255, 176, 60), "ok": (170, 210, 90),
        "warn": (255, 200, 80), "bad": (240, 110, 70), "ink": (14, 10, 4),
    },
}

_font_cache = {}


def font(size, bold=False, mono=False):
    key = (size, bold, mono)
    if key in _font_cache:
        return _font_cache[key]
    if mono:
        name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    else:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        f = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


class Theme:
    def __init__(self, name="dark"):
        self.set(name)

    def set(self, name):
        self.name = name
        self.c = PALETTES.get(name, PALETTES["dark"])

    def __getattr__(self, key):
        # theme.accent -> self.c["accent"]
        try:
            return self.__dict__["c"][key]
        except KeyError:
            raise AttributeError(key)
