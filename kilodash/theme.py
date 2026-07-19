"""Colour palettes and a small cached font factory."""

import os

from PIL import ImageFont

FONT_DIR = "/usr/share/fonts/truetype/dejavu"

# Three CRT-inspired skins. Chrome is monochrome (one phosphor colour + its
# shades); the traffic-light status colours (ok/warn/bad) are kept vivid across
# all three so warnings always read. Blue channel is kept low in green/amber so
# the phosphor reads true, not teal.
PALETTES = {
    # Classic green phosphor / "Matrix": bright #33FF46, dim #008F11 on black.
    "green": {
        "bg": (0, 9, 3), "card": (3, 26, 10), "card_hi": (8, 46, 18),
        "fg": (51, 245, 70), "muted": (0, 150, 40),
        "accent": (130, 255, 120), "ok": (51, 235, 80),
        "warn": (255, 190, 40), "bad": (255, 75, 60), "ink": (0, 9, 3),
    },
    # P3 amber phosphor / Fallout Pip-Boy: #FFB641 on warm black.
    "amber": {
        "bg": (12, 6, 0), "card": (32, 19, 2), "card_hi": (52, 33, 6),
        "fg": (255, 182, 66), "muted": (170, 112, 28),
        "accent": (255, 214, 110), "ok": (150, 225, 90),
        "warn": (255, 226, 90), "bad": (255, 96, 66), "ink": (12, 6, 0),
    },
    # Sterile clinical white. Chrome is greyscale (accent is a neutral dark
    # slate, not a colour); only ok/warn/bad carry colour, for meaning alone.
    "light": {
        "bg": (238, 240, 243), "card": (255, 255, 255), "card_hi": (222, 226, 231),
        "fg": (22, 26, 32), "muted": (120, 130, 142),
        "accent": (44, 50, 60), "ok": (22, 160, 82),
        "warn": (200, 140, 0), "bad": (208, 55, 48), "ink": (255, 255, 255),
        "sterile": True,
    },
}

_font_cache = {}

# Shared type scale for the instrument idiom. SUB is the secondary/sub line
# under a row's primary readout (e.g. "CH 1 · WPA2" beneath an SSID) — kept
# one knob so the whole dash stays legible at arm's length on the 320x480
# panel. HINT is the smaller all-caps legend/footer strip.
SUB = 11
HINT = 10


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
    def __init__(self, name="green"):
        self.set(name)

    def set(self, name):
        self.name = name
        self.c = PALETTES.get(name, PALETTES["green"])

    def __getattr__(self, key):
        # theme.accent -> self.c["accent"]
        try:
            return self.__dict__["c"][key]
        except KeyError:
            raise AttributeError(key)
