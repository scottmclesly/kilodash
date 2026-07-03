#!/usr/bin/env python3
"""
Kali debug panel - framebuffer status display for the 3.5" SPI panel.

Writes RGB565/XRGB directly to /dev/fb0 (ili9486drmfb), so it is immune to
Xorg/KMSDRM holding the DRM card. Shows network interface IPv4 addresses
and a WiFi on/off toggle. Touch is read straight from the ADS7846 evdev node.

Run as root (needs /dev/fb0, /dev/input/event*, and nmcli):
    sudo python3 fbdash.py

Deps:  python3-pil  python3-numpy  python3-evdev
"""

import json
import os
import select
import struct
import subprocess
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- config ----
FB_DEV   = "/dev/fb0"
POLL_SEC = 2.0            # how often to refresh interface/wifi state
FONT_DIR = "/usr/share/fonts/truetype/dejavu"

# Touch axis mapping. The panel is rotated (rotate=270), the touch controller
# is not, so axes usually need adjusting. If taps land in the wrong place,
# flip these one at a time. Set TOUCH_DEBUG=True to print mapped coords.
SWAP_XY  = True
INVERT_X = True
INVERT_Y = True
TOUCH_DEBUG = False

# theme
BG     = (16, 18, 22)
FG     = (220, 224, 228)
MUTED  = (120, 128, 140)
ACCENT = (60, 170, 255)
GREEN  = (40, 200, 120)
RED    = (220, 70, 70)
INK    = (10, 12, 14)

# evdev codes
EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y   = 0x00, 0x01
BTN_TOUCH      = 0x14a
EVENT_FMT      = "llHHi"               # 64-bit input_event: timeval(2*long)+type+code+value
EVENT_SIZE     = struct.calcsize(EVENT_FMT)


# ----------------------------------------------------------- framebuffer ----
def _sysfs(attr, default=None):
    try:
        with open(f"/sys/class/graphics/fb0/{attr}") as f:
            return f.read().strip()
    except Exception:
        return default


def fb_geometry():
    """(width, height, bpp, stride_bytes) from sysfs."""
    w, h = (int(v) for v in _sysfs("virtual_size", "480,320").split(","))
    bpp = int(_sysfs("bits_per_pixel", "16"))
    stride = _sysfs("stride")
    stride = int(stride) if stride else w * (bpp // 8)
    return w, h, bpp, stride


def pack_frame(img, bpp):
    """PIL RGB image -> raw bytes in the framebuffer's pixel format."""
    rgb = np.asarray(img, dtype=np.uint8)          # (H, W, 3)
    if bpp == 16:
        r = (rgb[:, :, 0] >> 3).astype(np.uint16)
        g = (rgb[:, :, 1] >> 2).astype(np.uint16)
        b = (rgb[:, :, 2] >> 3).astype(np.uint16)
        return ((r << 11) | (g << 5) | b).astype("<u2").tobytes()
    # assume 32bpp XRGB8888 (little-endian byte order B,G,R,X)
    h, w, _ = rgb.shape
    out = np.zeros((h, w, 4), np.uint8)
    out[:, :, 0] = rgb[:, :, 2]
    out[:, :, 1] = rgb[:, :, 1]
    out[:, :, 2] = rgb[:, :, 0]
    return out.tobytes()


def blit(fb, img, w, h, bpp, stride):
    raw = pack_frame(img, bpp)
    row = w * (bpp // 8)
    fb.seek(0)
    if stride == row:
        fb.write(raw)
    else:                                          # pad each line to stride
        buf = bytearray(stride * h)
        for y in range(h):
            buf[y * stride:y * stride + row] = raw[y * row:(y + 1) * row]
        fb.write(buf)
    fb.flush()


# ------------------------------------------------------------------ data ----
def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def get_interfaces():
    out = run(["ip", "-j", "addr"])
    items = []
    try:
        for link in json.loads(out or "[]"):
            name = link.get("ifname", "?")
            if name == "lo":
                continue
            ip4 = next((a.get("local", "") for a in link.get("addr_info", [])
                        if a.get("family") == "inet"), "")
            items.append((name, ip4 or "--", link.get("operstate", "")))
    except Exception:
        pass
    return items


def wifi_enabled():
    return run(["nmcli", "radio", "wifi"]).lower().startswith("enabled")


def toggle_wifi(on):
    subprocess.run(["nmcli", "radio", "wifi", "off" if on else "on"])


# ----------------------------------------------------------------- touch ----
def find_touch():
    """Return (fd, (xmin, xmax), (ymin, ymax)) for the ADS7846, or None."""
    import glob
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        name = _evdev_name(fd)
        if name and ("ADS7846" in name or "Touchscreen" in name):
            xr = _absinfo(fd, ABS_X)
            yr = _absinfo(fd, ABS_Y)
            return fd, xr, yr
        os.close(fd)
    return None


def _evdev_name(fd):
    import fcntl
    buf = bytearray(256)
    try:
        fcntl.ioctl(fd, 0x80ff4506, buf)           # EVIOCGNAME(256)
        return buf.split(b"\x00", 1)[0].decode(errors="ignore")
    except OSError:
        return None


def _absinfo(fd, axis):
    import fcntl
    # struct input_absinfo { __s32 value, minimum, maximum, fuzz, flat, resolution }
    buf = bytearray(24)
    req = 0x80184540 + axis                         # EVIOCGABS(axis)
    try:
        fcntl.ioctl(fd, req, buf)
        _, mn, mx, *_ = struct.unpack("6i", buf)
        return (mn, mx if mx > mn else mn + 1)
    except OSError:
        return (0, 4095)


def map_touch(rx, ry, xr, yr, w, h):
    nx = (rx - xr[0]) / (xr[1] - xr[0])
    ny = (ry - yr[0]) / (yr[1] - yr[0])
    nx, ny = min(max(nx, 0), 1), min(max(ny, 0), 1)
    if INVERT_X:
        nx = 1 - nx
    if INVERT_Y:
        ny = 1 - ny
    if SWAP_XY:
        nx, ny = ny, nx
    return nx * w, ny * h


# ------------------------------------------------------------------ main ----
def main():
    w, h, bpp, stride = fb_geometry()
    fb = open(FB_DEV, "r+b")

    def font(sz, mono=True):
        fn = "DejaVuSansMono.ttf" if mono else "DejaVuSans-Bold.ttf"
        try:
            return ImageFont.truetype(os.path.join(FONT_DIR, fn), sz)
        except OSError:
            return ImageFont.load_default()

    f_mono, f_small, f_bold = font(22), font(15), font(22, mono=False)

    # WiFi button: bottom strip, full width
    btn = (16, h - 64, w - 16, h - 12)             # (x0, y0, x1, y1)

    touch = find_touch()
    if touch:
        tfd, xr, yr = touch
    else:
        tfd = None
        print("WARN: ADS7846 not found - running display-only (no touch)")

    ifaces = get_interfaces()
    wifi = wifi_enabled()
    last_poll = 0.0
    cur_x = cur_y = 0

    while True:
        now = time.time()

        # ---- touch (non-blocking) ----
        if tfd is not None:
            r, _, _ = select.select([tfd], [], [], 0)
            if r:
                try:
                    data = os.read(tfd, EVENT_SIZE * 64)
                except OSError:
                    data = b""
                for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, etype, code, val = struct.unpack(EVENT_FMT, data[i:i + EVENT_SIZE])
                    if etype == EV_ABS and code == ABS_X:
                        cur_x = val
                    elif etype == EV_ABS and code == ABS_Y:
                        cur_y = val
                    elif etype == EV_KEY and code == BTN_TOUCH and val == 0:
                        x, y = map_touch(cur_x, cur_y, xr, yr, w, h)
                        if TOUCH_DEBUG:
                            print(f"tap raw=({cur_x},{cur_y}) -> screen=({x:.0f},{y:.0f})")
                        if btn[0] <= x <= btn[2] and btn[1] <= y <= btn[3]:
                            toggle_wifi(wifi)
                            wifi = wifi_enabled()
                            last_poll = 0      # force immediate redraw

        if now - last_poll < POLL_SEC:
            time.sleep(0.04)
            continue
        last_poll = now
        ifaces = get_interfaces()
        wifi = wifi_enabled()

        # ---- render ----
        img = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(img)
        d.text((16, 10), "DEBUG PANEL", font=f_bold, fill=ACCENT)
        d.text((w - 96, 14), time.strftime("%H:%M:%S"), font=f_small, fill=MUTED)
        d.line((16, 44, w - 16, 44), fill=MUTED)

        y = 58
        if ifaces:
            for name, ip4, state in ifaces:
                col = GREEN if state == "up" else MUTED
                d.text((16, y), f"{name:<7}", font=f_mono, fill=FG)
                d.text((130, y), ip4, font=f_mono, fill=col)
                y += 30
                if y > btn[1] - 30:
                    break
        else:
            d.text((16, y), "no interfaces", font=f_mono, fill=MUTED)

        bcol = GREEN if wifi else RED
        d.rounded_rectangle(btn, radius=10, fill=bcol)
        label = f"WiFi {'ON' if wifi else 'OFF'}  -  tap to {'disable' if wifi else 'enable'}"
        tw = d.textlength(label, font=f_bold)
        d.text(((btn[0] + btn[2]) / 2 - tw / 2, btn[1] + 14), label, font=f_bold, fill=INK)

        blit(fb, img, w, h, bpp, stride)


if __name__ == "__main__":
    main()
