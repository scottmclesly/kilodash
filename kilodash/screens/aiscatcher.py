"""AIS launch panel — receive live ship AIS, and (with TX hardware) transmit
test AIS frames to bench-check a robot's AIS receiver.

Two radios, two backends:

- **Listen** (works today): AIS-catcher's web server (`AIS-catcher -N 8100`) on the
  RTL-SDR. RX is inherently listen-only. Shows a live vessel count + message rate.
- **Transmit** (needs TX hardware): generates AIS frames for your own MMSI so a
  robot under test can prove it decodes them. The RTL-SDR cannot transmit — this
  needs a TX-capable SDR (HackRF / PlutoSDR / LimeSDR) plus `ais-simulator`. The
  Transmit control stays disabled until both are present, and every transmit is
  armed with a confirm tap.

Intended strictly for **contained bench testing** of your own receiver: minimal
power into a small/dummy antenna, indoors, so nothing reaches real AIS traffic.
"""

import shutil
import subprocess
import time

from .. import theme as T, webapp
from ..widgets import Button, Keyboard, spaced
from .webapp_base import WebAppScreen

BASE = "http://127.0.0.1:8100"
# On-hardware: ais-simulator seeds MMSI/position via its own web UI or args;
# kept as one constant so wiring the exact invocation later is a one-line change.
TX_CMD = ["ais-simulator"]


def _tx_ready():
    """TX path present: a generator tool AND a TX-capable SDR utility."""
    tool = shutil.which("ais-simulator")
    radio = shutil.which("hackrf_info") or shutil.which("SoapySDRUtil") \
        or shutil.which("PlutoSDR") or shutil.which("LimeUtil")
    return bool(tool and radio)


class AisCatcherScreen(WebAppScreen):
    title = "AIS"
    tile_id = "ais"
    glyph = "ais"
    tile_color_key = "accent"
    app_name = "AIS-catcher"
    port = 8100
    url_path = "/"
    device_key = "sdr"                     # RX needs the RTL-SDR present
    start_cmd = ["AIS-catcher", "-N", "8100"]

    def __init__(self, app):
        super().__init__(app)
        self.vessels = None
        self.msg_rate = None
        self.tx_proc = None
        self._tx_arm = 0.0                 # monotonic deadline for the confirm tap

    def available(self):
        if super().available():
            return True
        return shutil.which("ais-catcher") is not None

    @property
    def mmsi(self):
        return self.app.config["ais_own_mmsi"]

    @property
    def transmitting(self):
        return self.tx_proc is not None and self.tx_proc.poll() is None

    def on_app_leave(self):
        # never leave a transmitter running unattended
        self._stop_tx()

    # ---- RX feedback ----
    def poll_app(self):
        changed = False
        if self.transmitting:               # process may have exited on its own
            changed = True
        if self.web.state != webapp.UP:
            return changed
        geo = webapp.http_json(f"{BASE}/geojson", timeout=1.0)
        if isinstance(geo, dict) and isinstance(geo.get("features"), list):
            self.vessels = len(geo["features"])
            changed = True
        stat = webapp.http_json(f"{BASE}/stat.json", timeout=1.0)
        if isinstance(stat, dict):
            for k in ("msg_rate", "rate", "messages_per_second"):
                if k in stat:
                    self.msg_rate = stat[k]
                    changed = True
                    break
        return changed

    # ---- TX control ----
    def _start_tx(self):
        try:
            self.tx_proc = subprocess.Popen(TX_CMD, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                            stdin=subprocess.DEVNULL)
            self.app.toast(f"Transmitting AIS test (MMSI {self.mmsi or '—'})")
        except Exception as e:              # noqa: BLE001
            self.app.toast(f"TX failed: {e}"[:40])
            self.tx_proc = None

    def _stop_tx(self):
        if self.tx_proc:
            try:
                self.tx_proc.terminate()
                self.tx_proc.wait(timeout=4)
            except Exception:               # noqa: BLE001
                try:
                    self.tx_proc.kill()
                except Exception:           # noqa: BLE001
                    pass
            self.tx_proc = None

    def _edit_mmsi(self):
        kb = Keyboard(self.app.w, self.app.h, title="Own AIS MMSI (9 digits)",
                      secret=False, on_done=self._save_mmsi,
                      on_cancel=self.app.close_keyboard)
        kb.numeric = True
        kb.text = self.mmsi
        self.app.open_keyboard(kb)

    def _save_mmsi(self, text):
        self.app.config.set("ais_own_mmsi", "".join(c for c in text
                                                     if c.isdigit())[:9])
        self.app.close_keyboard()

    # ---- rendering ----
    def draw_app(self, d, th, top):
        w = self.app.w
        gap = 8
        cw = (w - 12 * 2 - gap) / 2

        # RX feedback readouts — hard-edged instrument cells
        tile_h = 58
        for i, (label, val, unit) in enumerate((
                ("VESSELS", self.vessels, "SEEN NOW"),
                ("MESSAGES", self.msg_rate, "PER SEC"))):
            x0 = 12 + i * (cw + gap)
            d.rectangle((x0, top, x0 + cw, top + tile_h), fill=th.card,
                        outline=th.card_hi, width=1)
            d.text((x0 + 10, top + 5), spaced(label),
                   font=T.font(9, bold=True, mono=True), fill=th.muted)
            shown = "—" if val is None else str(val)
            d.text((x0 + 10, top + 19), shown,
                   font=T.font(22, bold=True, mono=True), fill=th.accent)
            d.text((x0 + 10, top + 44), spaced(unit),
                   font=T.font(9, bold=True, mono=True), fill=th.muted)
        top += tile_h + 10

        # own station + TX
        d.text((14, top), spaced("OWN STATION · TX"),
               font=T.font(10, bold=True, mono=True), fill=th.muted)
        top += 18
        # MMSI field (tap to edit) — same hit box, hard-edged cell
        fh = 40
        d.rectangle((12, top, w - 12, top + fh), fill=th.card,
                    outline=th.accent, width=1)
        d.text((22, top + 5), spaced("MMSI"),
               font=T.font(9, bold=True, mono=True), fill=th.muted)
        d.text((22, top + 18), self.mmsi or spaced("TAP TO SET"),
               font=T.font(16, bold=True, mono=True),
               fill=th.fg if self.mmsi else th.muted)
        self._btns["mmsi"] = Button((12, top, w - 12, top + fh), "", font_size=1)
        top += fh + 8

        # Transmit toggle (gated on TX hardware + a confirm/arm tap);
        # armed/transmitting wear caution amber — red stays for faults
        ready = _tx_ready()
        armed = time.monotonic() < self._tx_arm
        if self.transmitting:
            label, color = "TRANSMITTING · STOP", th.warn
        elif armed:
            label, color = "CONFIRM TRANSMIT", th.warn
        else:
            label, color = "TRANSMIT TEST", None
        b = Button((12, top, w - 12, top + 42), label, kind="primary",
                   color=color, font_size=15)
        b.enabled = ready or self.transmitting
        b.draw(d, th)
        self._btns["tx"] = b
        top += 48
        if not ready and not self.transmitting:
            d.text((16, top), "NEEDS TX SDR (HACKRF/PLUTO) + AIS-SIMULATOR",
                   font=T.font(T.SUB, mono=True), fill=th.muted)
        else:
            d.text((16, top), "CONTAINED BENCH TEST · LOW POWER, SMALL ANT",
                   font=T.font(T.SUB, mono=True), fill=th.muted)

    def handle_app_tap(self, x, y):
        if self._btns.get("mmsi") and self._btns["mmsi"].hit(x, y):
            self._edit_mmsi()
            return True
        b = self._btns.get("tx")
        if b and b.hit(x, y):
            if self.transmitting:
                self._stop_tx()
            elif time.monotonic() < self._tx_arm:
                self._tx_arm = 0.0
                self._start_tx()
            else:
                self._tx_arm = time.monotonic() + 4     # arm; tap again to fire
                self.app.toast("Tap again within 4s to transmit")
            return True
        return False
