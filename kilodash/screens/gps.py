"""GPS — constellation/fix health for the PA1616S on /dev/gps0.

The across-the-room fix indicator: a phosphor sky plot (az/el polar, one
dot per satellite, sized/shaded by SNR, used-in-fix solid vs visible
hollow) over a status block — fix type, sats, HDOP, position, SOG/COG,
UTC, and the chrony time-source line that answers "am I the time
authority right now".

This tile reads gpsd directly (gps/gpsdio.py listener) for the rich
SKY/TPV detail — the snapshot contract file is for *other* tiles (GPS.md
§5). Hotplug-gated on /dev/gps0 presence (udev pins the PL2303 in port
1-1 to that name). Dirty-rect friendly: the sky plot repaints only when a
new SKY report lands (GSV cadence, ~2 Hz), status lines only on change.

No TX controls live here. The N2K GNSS-source button is on the NMEA2K
tile — sourcing PGNs is a bus action, so it belongs on the bus screen.
"""

import math
import time

from .. import system, theme as T
from ..widgets import rrect
from .base import Screen, HEADER_H

from gps.gpsdio import GpsdListener, MODE_NAMES, STATUS_DGPS

SKY_Y = HEADER_H + 4                 # 48
SKY_H = 252                          # sky plot pane (square-ish on 320 wide)
STATUS_Y = SKY_Y + SKY_H             # 300
FAST_TICK = 0.25
IDLE_TICK = 1.0
CHRONY_EVERY_S = 5.0
MPS_TO_KN = 1.94384


def time_authority_line(runner=system.run):
    """One truthful line about who disciplines the clock right now, from
    chronyc tracking: `time: GPS (chrony)` when our receiver is selected,
    `time: NTP <source>` on network time, else unsynced/no chrony."""
    out = runner(["chronyc", "tracking"]) or ""
    ref = ""
    synced = False
    for line in out.splitlines():
        if line.startswith("Reference ID"):
            ref = line.partition(":")[2].strip()
        elif line.startswith("Leap status"):
            synced = "Normal" in line
    if not out:
        return "time: chrony not running"
    if not synced:
        return "time: unsynced"
    if "(GPS)" in ref:
        return "time: GPS — this box is the time authority"
    name = ref.partition("(")[2].rstrip(")") or ref
    return f"time: NTP {name}"[:40]


class GpsScreen(Screen):
    title = "GPS"
    glyph = "gps"
    device_key = "gps"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = IDLE_TICK
        self.listener = None
        self.chrony_line = "time: …"
        self._chrony_task = None
        self._chrony_t = 0.0
        self._sky_sig = None
        self._status_sig = None
        self._st = {"tpv": None, "sky": None, "tpv_age": None,
                    "sky_age": None, "connected": False, "error": None}

    # -------------------------------------------------------------- lifecycle
    def on_enter(self):
        self.listener = GpsdListener().start()
        self._sky_sig = self._status_sig = None
        self._chrony_t = 0.0

    def on_leave(self):
        if self.listener:
            self.listener.stop()
            self.listener = None

    # ----------------------------------------------------------------- ticking
    def _fix_name(self):
        tpv = self._st["tpv"] or {}
        stale = self._st["tpv_age"] is not None and self._st["tpv_age"] > 2.0
        mode = 0 if stale else tpv.get("mode", 0)
        fix = MODE_NAMES.get(mode, "none")
        if fix != "none" and tpv.get("status") == STATUS_DGPS:
            fix = "dgps"
        return fix

    def tick(self):
        now = time.monotonic()
        if self._chrony_task and self._chrony_task.done:
            if self._chrony_task.result:
                self.chrony_line = self._chrony_task.result
            self._chrony_task = None
        if now - self._chrony_t >= CHRONY_EVERY_S and not self._chrony_task:
            self._chrony_t = now
            self._chrony_task = system.Task(time_authority_line)
        self._st = self.listener.state() if self.listener else self._st
        tpv = self._st["tpv"] or {}
        sky = self._st["sky"] or {}
        sats = sky.get("satellites") or []
        sky_sig = tuple(sorted(
            (s.get("PRN"), bool(s.get("used")), round(s.get("az") or 0),
             round(s.get("el") or 0), round(s.get("ss") or 0))
            for s in sats))
        status_sig = (self._fix_name(), tpv.get("lat"), tpv.get("lon"),
                      tpv.get("speed"), tpv.get("track"),
                      tpv.get("altMSL", tpv.get("alt")), sky.get("hdop"),
                      len(sats), sum(1 for s in sats if s.get("used")),
                      (tpv.get("time") or "")[:19], self.chrony_line,
                      self._st["connected"], self._st["error"])
        rects = []
        if sky_sig != self._sky_sig:
            self._sky_sig = sky_sig
            rects.append((0, SKY_Y, self.app.w, STATUS_Y))
        if status_sig != self._status_sig:
            self._status_sig = status_sig
            rects.append((0, STATUS_Y, self.app.w, self.app.h))
        self.tick_interval = FAST_TICK if self._st["connected"] else IDLE_TICK
        if rects:
            self.report_dirty(*rects)
            return True
        return False

    # ---------------------------------------------------------------- drawing
    def draw_content(self, d, th):
        self._draw_sky(d, th)
        self._draw_status(d, th)

    def _draw_sky(self, d, th):
        w = self.app.w
        cx, cy = w // 2, SKY_Y + SKY_H // 2 + 2
        R = SKY_H // 2 - 18
        lw = 1
        # horizon + elevation rings (0/30/60°) — the phosphor radar dish
        for el in (0, 30, 60):
            r = R * (90 - el) / 90
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=th.card_hi,
                      width=lw + (1 if el == 0 else 0))
        d.line((cx - R, cy, cx + R, cy), fill=th.card_hi, width=1)
        d.line((cx, cy - R, cx, cy + R), fill=th.card_hi, width=1)
        f = T.font(11, bold=True)
        for label, az in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            lx = cx + (R + 9) * math.sin(math.radians(az))
            ly = cy - (R + 9) * math.cos(math.radians(az))
            tw = d.textlength(label, font=f)
            d.text((lx - tw / 2, ly - 6), label, font=f,
                   fill=th.accent if label == "N" else th.muted)
        sky = self._st["sky"] or {}
        sats = sky.get("satellites") or []
        if not sats:
            msg = "searching…" if self._st["connected"] else \
                (self._st["error"] or "waiting for gpsd")
            fm = T.font(13)
            tw = d.textlength(msg[:34], font=fm)
            d.text((cx - tw / 2, cy - 8), msg[:34], font=fm, fill=th.muted)
            return
        fs = T.font(9, mono=True)
        for s in sats:
            az, el = s.get("az"), s.get("el")
            if az is None or el is None:
                continue
            r = R * (90 - max(0, min(90, el))) / 90
            x = cx + r * math.sin(math.radians(az))
            y = cy - r * math.cos(math.radians(az))
            snr = s.get("ss") or 0
            rad = 3 + min(4, snr / 12)          # 3..7 px by SNR
            if s.get("used"):
                shade = th.fg if snr >= 30 else th.ok
                d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=shade)
            else:
                d.ellipse((x - rad, y - rad, x + rad, y + rad),
                          outline=th.muted, width=1)
            if snr >= 20:
                d.text((x + rad + 1, y - 5), str(s.get("PRN", "")),
                       font=fs, fill=th.muted)

    def _draw_status(self, d, th):
        w, h = self.app.w, self.app.h
        tpv = self._st["tpv"] or {}
        sky = self._st["sky"] or {}
        sats = sky.get("satellites") or []
        used = sum(1 for s in sats if s.get("used"))
        fix = self._fix_name()
        y = STATUS_Y + 2
        rrect(d, (10, y, w - 10, h - 8), 10, fill=th.card)
        x0 = 22
        # headline: fix state, loud
        fixed = fix != "none"
        head = {"none": "NO FIX", "2d": "2D FIX", "3d": "3D FIX",
                "dgps": "DGPS FIX"}[fix]
        d.text((x0, y + 8), head, font=T.font(20, bold=True),
               fill=th.ok if fixed else th.warn)
        sub = f"{used}/{len(sats)} sats"
        hdop = sky.get("hdop")
        if hdop is not None:
            sub += f" · HDOP {hdop:.1f}"
        f14 = T.font(13, bold=True)
        d.text((w - 22 - d.textlength(sub, font=f14), y + 13), sub,
               font=f14, fill=th.fg)
        fm = T.font(13, mono=True)
        rows = []
        if fixed and tpv.get("lat") is not None:
            rows.append(f"{tpv['lat']:+11.6f}°  {tpv['lon']:+11.6f}°")
            sog = tpv.get("speed")
            cog = tpv.get("track")
            alt = tpv.get("altMSL", tpv.get("alt"))
            line = ""
            if sog is not None:
                line += f"SOG {sog * MPS_TO_KN:4.1f} kn  "
            if cog is not None:
                line += f"COG {cog:05.1f}°"
            if line:
                rows.append(line)
            if alt is not None and fix in ("3d", "dgps"):
                rows.append(f"alt {alt:+.0f} m")
        else:
            reason = self._st["error"] or \
                ("acquiring — needs sky view" if self._st["connected"]
                 else "gpsd unreachable")
            rows.append(reason[:38])
        utc = (tpv.get("time") or "")[:19].replace("T", " ")
        if utc:
            rows.append(f"UTC {utc}")
        for i, line in enumerate(rows):
            d.text((x0, y + 40 + i * 20), line[:38], font=fm, fill=th.fg)
        # the time-authority answer, always the bottom line
        d.text((x0, h - 28), self.chrony_line[:40], font=T.font(12),
               fill=th.accent if "GPS" in self.chrony_line else th.muted)
