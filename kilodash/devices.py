"""USB / bus hotplug detection.

Cheap sysfs polling (no pyudev dependency): the launcher calls refresh() a few
times a second and the tile grid shows a device's tile only while it's present.
Each detectable device maps to a screen via its `device_key`.
"""

import glob
import os
import time

# (vendor, product) USB ids we care about
SDR_IDS = {(0x0bda, 0x2838), (0x0bda, 0x2832)}      # RTL2832U dongles
ALFA_IDS = {(0x0e8d, 0x7612)}                       # MediaTek MT7612U (ALFA ACM)
CANABLE_IDS = {(0x1d50, 0x606f), (0x16d0, 0x117e),  # candleLight / gs_usb
               (0xad50, 0x60c4)}
FTDI_IDS = {(0x0403, 0x6001), (0x0403, 0x6015),     # FTDI
            (0x10c4, 0xea60),                        # CP210x
            (0x1a86, 0x7523), (0x1a86, 0x55d4)}      # CH340 / CH9102
# FX2LP logic analyzer (sigrok/fx2lafw). The ID is NOT fixed: match both the
# bare-bootloader ID and the post-firmware-load IDs — a board scanned once
# this session sits in its fx2lafw-enumerated state. Phase 0 bench work
# records the board's true ID; if it enumerates as something else, add the
# pair here. Deliberately a cheap VID/PID check — never poll
# `sigrok-cli --scan` for liveness (its firmware upload is too heavy).
FX2LA_IDS = {(0x04b4, 0x8613),                      # Cypress FX2 bootloader
             (0x1d50, 0x608c), (0x1d50, 0x608d),    # fx2lafw (post-load)
             (0x0925, 0x3881),                      # Saleae Logic clone EEPROM
             (0x08a9, 0x0014)}                      # USBee AX clone EEPROM


def _usb_ids():
    ids = set()
    for vp in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            base = os.path.dirname(vp)
            v = int(open(vp).read().strip(), 16)
            p = int(open(os.path.join(base, "idProduct")).read().strip(), 16)
            ids.add((v, p))
        except (OSError, ValueError):
            continue
    return ids


def _iface(name):
    return os.path.exists(f"/sys/class/net/{name}")


def _can_present(ids):
    if glob.glob("/sys/class/net/can*"):
        return True
    return bool(ids & CANABLE_IDS)


def _serial_present(ids):
    if glob.glob("/dev/ttyUSB*"):
        return True
    return bool(ids & FTDI_IDS)


class Devices:
    """Tracks which hotplug devices are currently present."""

    def __init__(self, interval=2.0):
        self.present = set()
        self.interval = interval
        self._last = -1e9

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now - self._last < self.interval:
            return self.present
        self._last = now
        ids = _usb_ids()
        p = set()
        if ids & SDR_IDS:
            p.add("sdr")
        if _iface("wlan1") or (ids & ALFA_IDS):
            p.add("wifisniff")
        if _can_present(ids):
            p.add("can")
        if _serial_present(ids):
            p.add("serial")
        if os.path.exists("/dev/i2c-1"):
            p.add("i2c")
        if ids & FX2LA_IDS:
            p.add("la")
        self.present = p
        return p

    def has(self, key):
        return key in self.present
