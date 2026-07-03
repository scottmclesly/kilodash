"""Direct /dev/fb0 output for the ILI9486 DRM framebuffer.

Packs a PIL RGB image into the panel's native pixel format (RGB565 or
XRGB8888) and writes it in one shot. Geometry is read from sysfs so the same
code works whatever rotation the overlay is set to.
"""

import numpy as np


def _sysfs(attr, default=None):
    try:
        with open(f"/sys/class/graphics/fb0/{attr}") as f:
            return f.read().strip()
    except OSError:
        return default


class Framebuffer:
    def __init__(self, dev="/dev/fb0"):
        self.dev = dev
        w, h = (int(v) for v in _sysfs("virtual_size", "320,480").split(","))
        self.w, self.h = w, h
        self.bpp = int(_sysfs("bits_per_pixel", "16"))
        stride = _sysfs("stride")
        self.stride = int(stride) if stride else w * (self.bpp // 8)
        self._fb = open(dev, "r+b")
        self._row = w * (self.bpp // 8)

    def _pack(self, img):
        rgb = np.asarray(img, dtype=np.uint8)
        if self.bpp == 16:
            r = (rgb[:, :, 0] >> 3).astype(np.uint16)
            g = (rgb[:, :, 1] >> 2).astype(np.uint16)
            b = (rgb[:, :, 2] >> 3).astype(np.uint16)
            return ((r << 11) | (g << 5) | b).astype("<u2").tobytes()
        h, w, _ = rgb.shape
        out = np.zeros((h, w, 4), np.uint8)
        out[:, :, 0] = rgb[:, :, 2]   # B
        out[:, :, 1] = rgb[:, :, 1]   # G
        out[:, :, 2] = rgb[:, :, 0]   # R
        return out.tobytes()          # X byte left 0

    def blit(self, img):
        raw = self._pack(img)
        self._fb.seek(0)
        if self.stride == self._row:
            self._fb.write(raw)
        else:
            buf = bytearray(self.stride * self.h)
            for y in range(self.h):
                buf[y * self.stride:y * self.stride + self._row] = \
                    raw[y * self._row:(y + 1) * self._row]
            self._fb.write(buf)
        self._fb.flush()

    def close(self):
        try:
            self._fb.close()
        except OSError:
            pass
