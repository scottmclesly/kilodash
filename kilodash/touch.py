"""ADS7846 resistive touch, read straight from evdev.

Yields simple ('down'|'move'|'up', x, y) tuples in *screen* pixel coordinates.
Axis orientation is taken live from Config so the Settings screen (or a hand
edit of config.json) can re-calibrate without touching code or rebooting.
"""

import glob

try:
    import evdev
    from evdev import ecodes
except ImportError:  # pragma: no cover
    evdev = None


def find_device():
    if evdev is None:
        return None
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        name = (dev.name or "")
        if "ADS7846" in name or "Touchscreen" in name or "XPT2046" in name:
            return dev
        dev.close()
    return None


class Touch:
    def __init__(self, config, w, h):
        self.cfg = config
        self.w, self.h = w, h
        self.dev = find_device()
        self.xr = (0, 4095)
        self.yr = (0, 4095)
        if self.dev:
            caps = self.dev.capabilities().get(ecodes.EV_ABS, [])
            for code, absinfo in caps:
                if code == ecodes.ABS_X:
                    self.xr = (absinfo.min, max(absinfo.max, absinfo.min + 1))
                elif code == ecodes.ABS_Y:
                    self.yr = (absinfo.min, max(absinfo.max, absinfo.min + 1))
            self.dev.grab() if False else None  # leave ungrabbed; we poll
        self._x = self._y = 0
        self._down = False

    @property
    def available(self):
        return self.dev is not None

    def _map(self, rx, ry):
        nx = (rx - self.xr[0]) / (self.xr[1] - self.xr[0])
        ny = (ry - self.yr[0]) / (self.yr[1] - self.yr[0])
        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        if self.cfg["touch_invert_x"]:
            nx = 1.0 - nx
        if self.cfg["touch_invert_y"]:
            ny = 1.0 - ny
        if self.cfg["touch_swap_xy"]:
            nx, ny = ny, nx
        if self.cfg["flip_180"]:
            nx, ny = 1.0 - nx, 1.0 - ny
        return nx * self.w, ny * self.h

    def poll(self):
        """Return a list of ('down'|'move'|'up', x, y) since last call."""
        events = []
        if not self.dev:
            return events
        try:
            for e in self.dev.read():   # non-blocking; raises BlockingIOError
                if e.type == ecodes.EV_ABS:
                    if e.code == ecodes.ABS_X:
                        self._x = e.value
                    elif e.code == ecodes.ABS_Y:
                        self._y = e.value
                elif e.type == ecodes.EV_KEY and e.code == ecodes.BTN_TOUCH:
                    x, y = self._map(self._x, self._y)
                    if e.value == 1:
                        self._down = True
                        events.append(("down", x, y))
                    else:
                        self._down = False
                        events.append(("up", x, y))
                elif e.type == ecodes.EV_SYN and self._down:
                    x, y = self._map(self._x, self._y)
                    events.append(("move", x, y))
        except BlockingIOError:
            pass
        except OSError:
            pass
        return events
