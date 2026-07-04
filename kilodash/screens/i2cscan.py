"""I2C bus scanner — i2cdetect on the Pi's onboard bus (i2c-1), with best-guess
device names for the addresses that respond.
"""

import re
import subprocess

from PIL import Image, ImageDraw

from .. import system, theme as T
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

BUS = 1
ROW_H = 46

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
    tile_color_key = "ok"
    device_key = "i2c"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = 1.0
        self.addrs = []
        self.task = None
        self.status = f"Tap Scan (bus i2c-{BUS})"
        self.scan_btn = None

    def on_enter(self):
        if not self.addrs and self.task is None:
            self.start()

    def start(self):
        if self.task and not self.task.done:
            return
        self.status = "Scanning i2c-1…"
        self.task = system.Task(_scan)

    def tick(self):
        if self.task and self.task.done:
            self.addrs = self.task.result or []
            self.status = f"{len(self.addrs)} device(s) on i2c-{BUS}"
            self.task = None
            return True
        return self.task is not None

    def content_area(self):
        return (0, HEADER_H + 46, self.app.w, self.app.h - HEADER_H - 46)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        top = HEADER_H + 46
        self.content_h = max(len(self.addrs) * ROW_H + 8, h - top)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        for i, a in enumerate(self.addrs):
            y = i * ROW_H
            rrect(sd, (12, y, w - 12, y + ROW_H - 6), 9, fill=th.card)
            sd.text((22, y + 8), f"0x{a:02X}", font=T.font(20, bold=True, mono=True),
                    fill=th.ok)
            sd.text((96, y + 12), HINTS.get(a, "unknown device"),
                    font=T.font(15), fill=th.muted)
        self.paste_list(top, h - top, surf)

        d.rectangle((0, HEADER_H, w, top), fill=th.bg)
        bar_y = HEADER_H + 4
        rrect(d, (12, bar_y, w - 120, bar_y + 38), 8, fill=th.card)
        d.text((22, bar_y + 11), self.status[:26], font=T.font(13), fill=th.muted)
        scanning = self.task is not None
        self.scan_btn = Button((w - 112, bar_y, w - 12, bar_y + 38),
                               "…" if scanning else "Scan", kind="primary",
                               font_size=17)
        self.scan_btn.enabled = not scanning
        self.scan_btn.draw(d, th)

    def handle_tap(self, x, y):
        if self.scan_btn and self.scan_btn.hit(x, y):
            self.start()
            return True
        return False
