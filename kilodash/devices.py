"""USB / bus hotplug detection.

Cheap sysfs polling (no pyudev dependency): the launcher calls refresh() a few
times a second and the tile grid shows a device's tile only while it's present.
Each detectable device maps to a screen via its `device_key`.
"""

import glob
import os
import time

from . import cantick

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
# CanTick (ESP32-family CDC, PROTOCOL.md §4). Espressif's VID is shared by
# every ESP32 board, so match the product string — but bench reality: the
# ESP32-S3's built-in USB serial reports the FIXED hardware string
# "USB JTAG/serial debug unit" (PID 0x1001), not the firmware's name, so
# that string counts as a CanTick too. A string-less descriptor also counts.
CANTICK_VID = 0x303a
_CANTICK_PRODUCTS = ("cantick", "usb jtag/serial")
# Scottina Light (Wio Terminal, Light Dock — DOCK-PROTOCOL.md). Phase 0
# bench fact (2026-07-12, this unit): enumerates as 2886:802d with the STOCK
# descriptor string "Seeed Wio Terminal" — the v1-foundation firmware does
# not rename the CDC product. Seeed's VID + product-string match, never the
# ttyACM index (CanTick is also CDC on this bench); accept a future firmware
# that does rename itself.
LIGHT_VID = 0x2886
_LIGHT_PRODUCTS = ("wio terminal", "scottina light")


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


# Where the Files screen mounts an offload stick (see screens/files.py)
USB_MOUNT = "/media/usb"


def _mounted_devs():
    """dev node -> mountpoint for every mounted block device."""
    m = {}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                dev, mnt = line.split()[:2]
                if dev.startswith("/dev/"):
                    m[dev] = mnt
    except OSError:
        pass
    return m


def usb_stick_partitions():
    """Candidate partitions of the first free USB mass-storage disk (the
    whole disk if unpartitioned), or []. A disk already backing a system
    mount — a USB-SSD rootfs, say — is never offered: only free media count
    as offload sticks. The Files screen tries the candidates in order and
    keeps the first that mounts writable (installer sticks lead with a
    read-only iso9660 partition)."""
    mounts = _mounted_devs()
    for disk in sorted(glob.glob("/sys/block/sd*")):
        if "/usb" not in os.path.realpath(disk):
            continue
        name = os.path.basename(disk)
        parts = sorted(glob.glob(os.path.join(disk, name + "*")))
        nodes = ["/dev/" + os.path.basename(p) for p in parts] \
            or ["/dev/" + name]
        if any(mounts.get(n) not in (None, USB_MOUNT) for n in nodes):
            continue
        return nodes
    return []


def usb_stick_partition():
    """First candidate partition (presence check for the `usbstick` key)."""
    parts = usb_stick_partitions()
    return parts[0] if parts else None


def _cantick_usb_base():
    """sysfs device dir of a plugged-in CanTick (VID 0x303A + product match),
    or None."""
    for vp in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            if int(open(vp).read().strip(), 16) != CANTICK_VID:
                continue
        except (OSError, ValueError):
            continue
        base = os.path.dirname(vp)
        try:
            product = open(os.path.join(base, "product")).read().strip()
        except OSError:
            product = ""
        if not product or any(p in product.lower()
                              for p in _CANTICK_PRODUCTS):
            return base
    return None


def cantick_tty():
    """/dev path of the CanTick's CDC serial port, or None."""
    return _cdc_tty(_cantick_usb_base())


def _light_usb_base():
    """sysfs device dir of a docked Scottina Light (Seeed VID + product
    string — a string-less Seeed device is NOT assumed to be a Light), or
    None."""
    for vp in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            if int(open(vp).read().strip(), 16) != LIGHT_VID:
                continue
        except (OSError, ValueError):
            continue
        base = os.path.dirname(vp)
        try:
            product = open(os.path.join(base, "product")).read().strip()
        except OSError:
            continue
        if any(p in product.lower() for p in _LIGHT_PRODUCTS):
            return base
    return None


def light_tty():
    """/dev path of Scottina Light's CDC serial port, or None."""
    return _cdc_tty(_light_usb_base())


def _cdc_tty(base):
    """tty node under a sysfs USB device dir (VID/product matched first —
    never keyed on the ACM index)."""
    if not base:
        return None
    for pat in ("/*/tty/ttyACM*", "/*/ttyACM*"):
        for p in glob.glob(base + pat):
            return "/dev/" + os.path.basename(p)
    return None


def _can_present(ids):
    # slcan* covers a WiFi CanTick once slcand has attached; link_active()
    # keeps the CAN screen alive while the link is still WAITING for a
    # dial-in (no kernel iface exists yet) — without it app.py's hotplug
    # check would bounce the user back to Home mid-session.
    if glob.glob("/sys/class/net/can*") or glob.glob("/sys/class/net/slcan*"):
        return True
    if cantick.link_active():
        return True
    if _cantick_usb_base():
        return True         # CanTick on USB: screen must be reachable to
                            # provision it (and to host the WiFi link)
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
        if _cantick_usb_base():
            p.add("cantick")            # provisioning affordance (CAN screen)
        if _light_usb_base():
            p.add("scottinalight")      # Light Dock screen (auto-sync)
        if usb_stick_partition():
            p.add("usbstick")           # offload media (Files screen)
        self.present = p
        return p

    def has(self, key):
        return key in self.present
