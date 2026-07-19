"""I2C bus scanner — i2cdetect on the Pi's onboard bus (i2c-1), with best-guess
device names for the addresses that respond.
"""

import re
import subprocess

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, brackets, spaced, status_square
from .base import Screen, HEADER_H

BUS = 1
ROW_H = 46

# address-matrix instrument (drawn at the top of the scroll surface):
# 8 rows × 16 columns, the i2cdetect layout — lit square = device answered,
# hollow = silent, blank = outside the probeable 0x03..0x77 range
GRID_FRAME_Y = 4
GRID_CELLS_Y = GRID_FRAME_Y + 42          # first cell row (below caption/header)
CELL, PITCH = 11, 15                      # cell square + column pitch
ROW_PITCH = 16
GRID_X = 52                               # first cell column x
GRID_FRAME_H = 42 + 8 * ROW_PITCH + 10    # caption + 8 rows + pad
LIST_Y = GRID_FRAME_Y + GRID_FRAME_H + 10  # found-device rows start here

# common 7-bit address → likely part (best-effort hint)
HINTS = {
    0x0c: "compass", 0x1e: "HMC5883 mag", 0x20: "PCF8574/MCP23017",
    0x23: "BH1750 light", 0x27: "LCD backpack", 0x29: "VL53L0X/TSL2561",
    0x3c: "SSD1306 OLED", 0x3d: "SSD1306 OLED", 0x40: "INA219/PCA9685",
    0x48: "ADS1115/LM75", 0x50: "EEPROM", 0x51: "PCF8563 RTC",
    0x53: "ADXL345", 0x57: "EEPROM/MAX30102", 0x5a: "MLX90614/MPR121",
    0x68: "MPU6050/DS3231 RTC", 0x69: "MPU6050", 0x76: "BMP/BME280",
    0x77: "BMP/BME280",
}


def _scan():
    try:
        out = subprocess.run(["i2cdetect", "-y", str(BUS)],
                             capture_output=True, text=True, timeout=8).stdout
    except Exception:       # noqa: BLE001
        return []
    found = []
    for line in out.splitlines()[1:]:
        m = re.match(r"^([0-9a-f]{2}):\s*(.*)$", line)
        if not m:
            continue
        row = int(m.group(1), 16)
        for i, cell in enumerate(m.group(2).split()):
            if cell not in ("--", "UU") and re.fullmatch(r"[0-9a-f]{2}", cell):
                found.append(int(cell, 16))
    return sorted(set(found))


class I2cScreen(Screen):
    title = "I2C Scan"
    tile_id = "i2c-scan"
    glyph = "i2c"
    tile_color_key = "ok"
    device_key = "i2c"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.addrs = []
        self.task = None
        self.status = f"STANDING BY · I2C-{BUS}"
        self.scan_btn = None

    def on_enter(self):
        if not self.addrs and self.task is None:
            self.start()

    def start(self):
        if self.task and not self.task.done:
            return
        self.status = f"SCANNING I2C-{BUS}"
        self.task = system.Task(_scan)

    def tick(self):
        if self.task and self.task.done:
            self.addrs = self.task.result or []
            self.status = f"{len(self.addrs)} DEVICE(S) · I2C-{BUS}"
            self.task = None
            return True
        return self.task is not None

    def content_area(self):
        return (0, HEADER_H + 46, self.app.w, self.app.h - HEADER_H - 46)


    def model_rows(self):
        """Bus scan results from tick()."""
        addrs = self.addrs or []
        rows = [
            {"label": "BUS", "value": f"I2C-{BUS}", "state": None},
            {"label": "DEVICES", "value": str(len(addrs)),
             "state": "ok" if addrs else "caution"},
            {"label": "STATUS", "value": str(self.status or "—"), "state": None},
        ]
        for a in addrs[:12]:
            hint = HINTS.get(a)
            rows.append({"label": f"0x{a:02X}", "value": str(hint or "UNKNOWN"),
                         "state": None if hint else "caution"})
        return rows

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H + 46
        self.content_h = max(LIST_Y + len(self.addrs) * ROW_H + 8, h - top)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        self._draw_grid(sd, th, w)
        fn = T.font(18, bold=True, mono=True)
        fh = T.font(11, mono=True)
        for i, a in enumerate(self.addrs):
            y = LIST_Y + i * ROW_H
            sd.rectangle((12, y, w - 12, y + ROW_H - 6),
                         fill=th.card, outline=th.card_hi, width=1)
            status_square(sd, (22, y + 14, 34, y + 26), "lit", th.ok)
            sd.text((44, y + 9), f"0x{a:02X}", font=fn, fill=th.fg)
            sd.text((104, y + 14), HINTS.get(a, "unidentified").upper()[:19],
                    font=fh, fill=th.muted)
        self.paste_list(top, h - top, surf)

        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        bar_y = HEADER_H + 4
        d.rectangle((12, bar_y, w - 120, bar_y + 38),
                    fill=th.card, outline=th.card_hi, width=1)
        d.text((22, bar_y + 13), self.status[:23],
               font=T.font(11, bold=True, mono=True), fill=th.muted)
        scanning = self.task is not None
        self.scan_btn = Button((w - 112, bar_y, w - 12, bar_y + 38),
                               "…" if scanning else "SCAN", kind="primary",
                               font_size=16)
        self.scan_btn.enabled = not scanning
        self.scan_btn.draw(d, th)

    def _draw_grid(self, sd, th, w):
        """Bracket-framed address matrix: the screen's one instrument.
        i2cdetect geometry — row base down the left, hex column across
        the top; a lit square is a device answering at that address."""
        frame = (16, GRID_FRAME_Y, w - 16, GRID_FRAME_Y + GRID_FRAME_H)
        brackets(sd, frame, th.muted)
        sd.text((28, GRID_FRAME_Y + 8), spaced("ADDRESS MATRIX"),
                font=T.font(10, bold=True, mono=True), fill=th.muted)
        fh = T.font(8, mono=True)
        for c in range(16):
            x = GRID_X + c * PITCH
            sd.text((x + CELL / 2 - 2, GRID_CELLS_Y - 12), f"{c:X}",
                    font=fh, fill=th.muted)
        found = set(self.addrs)
        for r in range(8):
            y = GRID_CELLS_Y + r * ROW_PITCH
            sd.text((28, y + 1), f"{r * 16:02x}", font=fh, fill=th.muted)
            for c in range(16):
                a = r * 16 + c
                if not 0x03 <= a <= 0x77:
                    continue
                x = GRID_X + c * PITCH
                box = (x, y, x + CELL, y + CELL)
                if a in found:
                    status_square(sd, box, "lit", th.ok)
                else:
                    status_square(sd, box, "hollow", th.card_hi, width=1)

    def handle_tap(self, x, y):
        if self.scan_btn and self.scan_btn.hit(x, y):
            self.start()
            return True
        return False
