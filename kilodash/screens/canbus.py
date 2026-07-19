"""CAN — raw-bus forensics on proprietary traffic (CANable / gs_usb, slcan,
or a WiFi CanTick).

The screen for reverse-engineering unknown IDs with unknown semantics. Two
tabs:

  **Bus** — the working view: a seen-IDs table (one row per arbitration ID:
  count, rate, last payload, changed-bytes highlight), tap a row for the
  byte grid to set per-byte watches (change-detection or value-match; alert
  is a status badge + row flash, never modal), a candump-style live list,
  filters (ID match/mask via "filter this ID", watched-only, changed-only),
  and a bounded ring-buffer log exported to /opt/kilodash/captures/ in
  candump `.log` format (replayable with can-utils, loadable in SavvyCAN).

  **Setup** — bring the interface up at a chosen bitrate, best-effort
  bitrate autodetect (listen-only), continuous candump logging, the live
  RX counter, and the CanTick health card.

Scope (CAN-N2K-Split-TODO, hard constraint): diagnostics only — **this
screen constructs no TX frames and has no TX surface**; its SocketCAN
socket (busmon.RxReader, opened on entry, closed on leave — no shared RX
daemon) only ever recv()s. The single system-wide TX exception
(heartbeat/reply behavior required by bus participation, e.g. NMEA2000
address claim / ISO request responses) lives in the link layer (CanTick
firmware / N2K stack), never in any user-facing control here.
tests/test_busmon.py enforces this in code (allow-list + reject pass).

CanTick (see PROTOCOL.md — diagnostics + normal CAN participation only):
while this screen is open it hosts the supervised SLCAN-over-TCP link so a
CanTick dialing in over WiFi appears as an ordinary `slcan0`, listens
(read-only) for its heartbeat to drive the health card, offers a one-time
USB provisioning push when a CanTick is plugged in, and — only when the Pi
has no uplink at all — raises the reversible fallback AP. Everything
CanTick is torn down on leave. Listen-only is enforced on the device.

Presentation follows the ship-instrument look ratified on the Pomodoro
refactor (Cobb's Semiotic Standard): a hard-edged bus-state banner with a
per-state glyph — hazard cap on RX faults only — bracket-framed traffic
pane, caps-mono readouts, square indicators, and a segmented bus-load
gauge. Red stays reserved for genuine faults (a dead RX socket); stopping
a log or striking a watch is an amber stand-down, never red.
"""

import glob
import os
import signal
import subprocess
import time

from PIL import Image, ImageDraw

from .. import busmon, cantick, system, theme as T
from ..devices import cantick_tty
from ..widgets import (Button, Keyboard, brackets, hazard, seg_row, spaced,
                       state_glyph, status_square)
from .base import Screen, HEADER_H

CAP_DIR = "/opt/kilodash/captures"
BITRATES = [1000000, 500000, 250000, 125000, 100000, 50000]

FAST_TICK = 0.1         # ~10 Hz while frames are flowing (dirty-rect band)
IDLE_TICK = 0.5         # guardrail: silent/absent bus doesn't spin the CPU
AP_CHECK_IV = 5.0       # uplink re-probe cadence while the fallback AP is up
READER_RETRY = 2.0      # RX socket restart backoff after an iface drop

# Fixed vertical bands (x derives from app.w at draw time — the panel is
# 320×480 portrait; never hardcode screen x-coords).
IFACE_Y = HEADER_H + 6           # 50   interface card + CanTick chip
IFACE_H = 50
TAB_Y = IFACE_Y + IFACE_H + 6    # 106  [Bus | Setup] segmented control
TAB_H = 32
BODY_Y = TAB_Y + TAB_H + 6       # 144  tab body starts here
CHIP_Y = BODY_Y                  # 144  bus tab: filter chips + alert badge
CHIP_H = 30
LIST_TOP = CHIP_Y + CHIP_H + 6   # 180  bus tab: seen-IDs / live list pane
BOT_H = 46                       #      bus tab: bottom bar (Save + stats)
ROW_H = 34                       # two-line seen-IDs rows (tap targets)
LIVE_ROW_H = 20                  # one-line candump-style live rows

# Banner idiom keyed on what the screen can actually sense: this screen
# never reads controller state (error-passive/bus-off live in netlink, and
# presentation must not add I/O), so a dead RX socket is the fault surface.
BUS_STATES = {
    "listen":  {"label": "LISTENING",    "col": "ok",    "glyph": "up"},
    "standby": {"label": "STANDING BY",  "col": "muted", "glyph": "standby"},
    "fault":   {"label": "RX FAULT",     "col": "bad",   "glyph": "fault"},
    "none":    {"label": "NO INTERFACE", "col": "muted", "glyph": "standby"},
}


def _rx_frames(iface):
    """Kernel RX frame counter — one cheap sysfs read, no candump needed."""
    try:
        with open(f"/sys/class/net/{iface}/statistics/rx_packets") as f:
            return int(f.read())
    except (OSError, ValueError):
        return None


def _sh(*cmd, timeout=6):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _can_iface():
    for p in sorted(glob.glob("/sys/class/net/can*")
                    + glob.glob("/sys/class/net/slcan*")):
        return os.path.basename(p)
    return None


def _bring_up(iface, bitrate, listen_only=False):
    if iface.startswith("slcan"):
        # slcan bitrate is fixed by slcand -s at attach time (PROTOCOL.md §1);
        # `type can bitrate` doesn't apply to the line-discipline driver
        _sh("ip", "link", "set", iface, "up")
        return
    _sh("ip", "link", "set", iface, "down")
    args = ["ip", "link", "set", iface, "type", "can", "bitrate", str(bitrate)]
    if listen_only:
        args += ["listen-only", "on"]
    _sh(*args)
    _sh("ip", "link", "set", iface, "up")


def _count_frames(iface, secs=1.2):
    try:
        p = subprocess.run(["candump", "-n", "5", "-T", str(int(secs * 1000)),
                            iface], capture_output=True, text=True,
                           timeout=secs + 2)
        return len([l for l in p.stdout.splitlines() if l.strip()])
    except Exception:       # noqa: BLE001
        return 0


def _autodetect(iface):
    best = (0, None)
    for br in BITRATES:
        _bring_up(iface, br, listen_only=True)
        n = _count_frames(iface, 1.2)
        if n > best[0]:
            best = (n, br)
        if n >= 5:
            break
    _sh("ip", "link", "set", iface, "down")
    return best[1]


def _provision(port, config, blk):
    """Background worker: push primary (current WiFi) + fallback creds over
    USB per PROTOCOL.md §4. Returns (ok, message); never logs PSKs."""
    try:
        primary = cantick.wifi_creds_nm()
        fallback = (blk["fallback_ap_ssid"], cantick.ensure_fallback_psk(config))
        prov = cantick.CanTickProvisioner(port)
        return prov.provision(primary, fallback,
                              blk["bitrate"], blk["listen_only"])
    except cantick.CanTickError as e:
        return False, str(e)


class CanScreen(Screen):
    title = "CAN Bus"
    tile_id = "can-bus"
    glyph = "can"
    tile_color_key = "bad"
    device_key = "can"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = IDLE_TICK
        self.iface = None
        self.rate_idx = 1                 # default 500k
        self.log_proc = None
        self.log_path = None
        self.detect_task = None
        self.export_task = None
        self.status = "Detecting interface…"
        self.rx_count = None              # displayed kernel RX frame counter
        self.rx_rate = 0.0                # frames/s over the last second
        self._rx_hist = []                # (t, count) samples, ≤1 s window
        self._traffic_box = None          # dirty rect for the live counter card
        # ---- bus forensics model (persists across visits: RE state is
        # precious — watches and the ring survive tab-hopping) ----
        self.mon = busmon.BusMonitor()
        self.reader = None                # busmon.RxReader (RX-only socket)
        self._reader_retry = 0.0
        self.tab = "bus"
        self.view_live = False            # seen-IDs table vs candump live list
        self.watched_only = False
        self.changed_only = False
        self.filt_id = None               # (id, ext) exact-ID filter
        self.sel = None                   # (id, ext) byte-grid overlay target
        self.sel_byte = None
        self._rows = []
        self._vis_rows = []
        self._stats = {"ring": 0, "total": 0, "hits": 0, "alerting": 0}
        self._bus_sig = None
        self._btns = {}
        # ---- CanTick (built lazily on first enable; config-driven) ----
        self.ct_blk = cantick.block(app.config)
        self.link = None                  # cantick.CanTickLink
        self.hb = None                    # cantick.HeartbeatListener
        self.ap = None                    # cantick.CanTickAP (fallback only)
        self.ap_task = None               # background AP start
        self.prov_task = None             # background provisioning push
        self._ct_box = None               # dirty rect for the health card
        self._ct_sig = None               # last-drawn health card signature
        self._ap_checked = 0.0

    # ------------------------------------------------------ CanTick lifecycle
    def _ct_start(self):
        blk = self.ct_blk
        if self.hb is None:
            self.hb = cantick.HeartbeatListener(
                port=blk["hb_port"],
                expected_version=blk["expected_contract_version"])
        if self.link is None or self.link.state == cantick.CanTickLink.STOPPED:
            # rebuild from current config — bitrate/port edits apply on the
            # next screen entry, not only after a UI restart
            try:
                self.link = cantick.CanTickLink(iface=blk["slcan_iface"],
                                                tcp_port=blk["tcp_port"],
                                                bitrate=blk["bitrate"])
            except cantick.CanTickError as e:
                self.status = f"CanTick config: {e}"
                return
        self.hb.start()
        self.link.start()
        # AP fallback (§5): ONLY when there is no uplink at all, and only if a
        # fallback PSK was ever provisioned (no PSK -> nothing could join)
        psk = blk.get("fallback_psk")
        if psk and self.ap is None:
            try:
                self.ap = cantick.CanTickAP(ssid=blk["fallback_ap_ssid"],
                                            psk=psk,
                                            gateway=blk["ap_gateway"])
            except cantick.CanTickError:
                self.ap = None
        if self.ap and not self.ap.active and self.ap_task is None:
            self.ap_task = system.Task(self._ap_start_if_needed, self.ap)

    @staticmethod
    def _ap_start_if_needed(ap):
        if ap.uplink_present():
            return None                   # normal case: LAN carries CanTick
        return ap.start()

    def _ct_stop(self):
        """Tear down everything CanTick — must never strand wlan0 or slcan0."""
        for label, fn in (("link", self.link and self.link.stop),
                          ("hb", self.hb and self.hb.stop),
                          ("ap", self.ap and self.ap.stop)):
            if not fn:
                continue
            try:
                fn()
            except Exception:   # noqa: BLE001 — teardown must run to the end
                pass
        self.ap_task = None

    def _toggle_cantick(self):
        blk = dict(self.ct_blk)
        blk["enabled"] = not blk["enabled"]
        self.ct_blk = blk
        self.app.config.set("cantick", blk)
        if blk["enabled"]:
            self._ct_start()
            self.status = "CanTick link listening…"
        else:
            self._ct_stop()
            self.status = "CanTick link off"

    def _start_provision(self):
        if self.prov_task and not self.prov_task.done:
            return
        port = cantick_tty()
        if not port:
            self.app.toast("CanTick USB port not found")
            return
        self.status = "Provisioning CanTick…"
        self.prov_task = system.Task(_provision, port, self.app.config,
                                     self.ct_blk)

    def _pick_iface(self):
        """The interface this screen watches. While the CanTick WiFi link is
        up, its slcan iface IS the selected source — a USB dongle may coexist
        on the bench (and would otherwise win the alphabetical glob)."""
        if (self.ct_blk["enabled"] and self.link
                and self.link.state == cantick.CanTickLink.UP
                and os.path.isdir(
                    f"/sys/class/net/{self.ct_blk['slcan_iface']}")):
            return self.ct_blk["slcan_iface"]
        return _can_iface()

    # ---------------------------------------------------------- RX socket
    def _ensure_reader(self):
        """(Re)start the RX-only SocketCAN reader when the iface exists and
        the previous reader is gone/dead/wrong-iface. Throttled so an absent
        bus doesn't churn threads."""
        if not self.iface or not os.path.isdir(f"/sys/class/net/{self.iface}"):
            return
        r = self.reader
        if r and r.alive and r.iface == self.iface:
            return
        now = time.monotonic()
        if now - self._reader_retry < READER_RETRY:
            return
        self._reader_retry = now
        if r:
            r.stop()
            if r.error:
                self.status = r.error[:34]
        self.reader = busmon.RxReader(self.iface, self.mon).start()

    def _stop_reader(self):
        if self.reader:
            self.reader.stop()
            self.reader = None

    # --------------------------------------------------------------- lifecycle
    def on_enter(self):
        self.ct_blk = cantick.block(self.app.config)
        if self.ct_blk["enabled"]:
            self._ct_start()
        self.iface = self._pick_iface()
        self.status = (f"{self.iface} ready" if self.iface
                       else "CanTick listening…" if self.ct_blk["enabled"]
                       else "No CAN iface (slcan needs slcand)")
        self._rx_hist = []
        self.rx_count = _rx_frames(self.iface) if self.iface else None
        self.rx_rate = 0.0
        self._ct_sig = None
        self._bus_sig = None
        self.sel = self.sel_byte = None
        self._reader_retry = 0.0
        self._ensure_reader()

    def on_leave(self):
        try:
            if self.logging:
                self._toggle_log()
        finally:
            self._stop_reader()
            self._ct_stop()

    # ----------------------------------------------------------- CAN controls
    def _up(self):
        if self.iface:
            _bring_up(self.iface, BITRATES[self.rate_idx])
            self.status = f"{self.iface} up @ {BITRATES[self.rate_idx]//1000}k"

    def _detect(self):
        if not self.iface or (self.detect_task and not self.detect_task.done):
            return
        self.status = "Autodetecting bitrate…"
        self.detect_task = system.Task(_autodetect, self.iface)

    def _toggle_log(self):
        if not self.iface:
            return
        if self.log_proc:
            self.log_proc.send_signal(signal.SIGINT)
            self.log_proc = None
            self.app.toast(f"Log saved: {os.path.basename(self.log_path or '')}")
            self.status = "Logging stopped"
            return
        self._up()
        os.makedirs(CAP_DIR, exist_ok=True)
        self.log_path = f"{CAP_DIR}/can_{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.log_proc = subprocess.Popen(
            ["candump", "-l", self.iface], cwd=CAP_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status = "Logging CAN traffic…"

    @property
    def logging(self):
        return self.log_proc is not None

    def _filters(self):
        f = {"watched_only": self.watched_only,
             "changed_only": self.changed_only}
        if self.filt_id is not None:
            f["id_match"] = self.filt_id[0]
            f["id_mask"] = busmon.CAN_EFF_MASK
        return f

    def _save_ring(self):
        if self.export_task and not self.export_task.done:
            return
        if not self._stats["ring"]:
            self.app.toast("Ring buffer is empty")
            return
        self.status = "Exporting ring…"
        self.export_task = system.Task(self._export_worker)

    def _export_worker(self):
        return self.mon.export(CAP_DIR, self.iface or "can?", **self._filters())

    # ---------------------------------------------------------------- ticking
    def _tick_cantick(self):
        """CanTick housekeeping; True if the health card needs a repaint."""
        if not self.ct_blk["enabled"]:
            return False
        # a CanTick dialing in (or dropping) can change the watched iface
        # after the screen was entered
        iface = self._pick_iface()
        if iface != self.iface:
            self.iface = iface
            self._rx_hist = []
            self.rx_count = _rx_frames(iface) if iface else None
            self._reader_retry = 0.0      # switch the RX socket immediately
            if iface:
                self.status = f"{iface} ready"
            return True
        # provisioning result
        if self.prov_task and self.prov_task.done:
            res = self.prov_task.result or (False, str(self.prov_task.error))
            self.prov_task = None
            ok, msg = res
            self.app.toast(("Provisioned ✓ " if ok else "Provision failed: ")
                           + msg)
            self.status = msg[:34]
            return True
        # fallback AP: surface the background start result, and drop the AP
        # as soon as a real uplink returns (probed on a slow cadence)
        if self.ap_task and self.ap_task.done:
            res = self.ap_task.result
            self.ap_task = None
            if res:
                ok, msg = res
                self.status = msg[:34]
                if ok:
                    self.app.toast(msg)
                return True
        now = time.monotonic()
        if (self.ap and self.ap.active
                and now - self._ap_checked >= AP_CHECK_IV):
            self._ap_checked = now
            if self.ap.uplink_present():
                self.ap.stop()
                self.status = "Uplink back — fallback AP down"
                return True
        # health card: repaint only when the displayed facts change
        rec = self.hb.latest() if self.hb else None
        link_state = self.link.state if self.link else "off"
        sig = (link_state, self.hb and self.hb.version_warning)
        if rec:
            sig += (rec["name"], rec["mode"], rec["rssi"], rec["drop"],
                    rec["fresh"], rec["drop_rising"], round(rec["rx_rate"]))
        if sig != self._ct_sig:
            self._ct_sig = sig
            if self.tab == "setup" and self._ct_box:
                self.report_dirty(self._ct_box)
            return self.tab == "setup"
        return False

    def tick(self):
        ct_changed = self._tick_cantick()

        if self.detect_task and self.detect_task.done:
            br = self.detect_task.result
            if br:
                self.rate_idx = BITRATES.index(br)
                self.status = f"Detected {br//1000}k"
            else:
                self.status = "No frames — bus idle or unpowered"
            self.detect_task = None
            return True                   # full redraw: status + selector moved

        if self.export_task and self.export_task.done:
            res, err = self.export_task.result, self.export_task.error
            self.export_task = None
            if res:
                n, path = res
                self.status = f"Saved {n} frames"
                self.app.toast(f"Ring → {os.path.basename(path)} ({n})")
            else:
                self.status = f"Export failed: {err}"[:34]
                self.app.toast("Ring export failed")
            return True

        if not self.iface:
            self.tick_interval = IDLE_TICK
            return ct_changed
        self._ensure_reader()

        # kernel RX counter: drives the Setup traffic card AND the
        # fast-tick/idle-tick guardrail on both tabs
        now = time.monotonic()
        rx = _rx_frames(self.iface)
        counter_changed = False
        if rx is not None:
            self._rx_hist.append((now, rx))
            while self._rx_hist and now - self._rx_hist[0][0] > 1.0:
                self._rx_hist.pop(0)
            t0, c0 = self._rx_hist[0]
            rate = (rx - c0) / (now - t0) if now > t0 else 0.0
            if rx != self.rx_count or f"{rate:.0f}" != f"{self.rx_rate:.0f}":
                self.rx_count, self.rx_rate = rx, rate
                counter_changed = True
        flowing = (rx is not None and len(self._rx_hist) >= 2
                   and rx != self._rx_hist[0][1])
        self.tick_interval = FAST_TICK if flowing else IDLE_TICK

        if self.tab == "setup":
            if counter_changed and self._traffic_box:
                self.report_dirty(self._traffic_box)
            return counter_changed or ct_changed

        # bus tab: snapshot the model; repaint the body band when anything
        # the table shows moved (alert decay included — `alerting` is
        # derived from now, so row flashes fade even on a quiet bus)
        self._rows, self._stats = self.mon.snapshot()
        sig = (self._stats["total"], self._stats["hits"],
               self._stats["alerting"], len(self._rows),
               self.reader.error if self.reader else None)
        if sig != self._bus_sig:
            self._bus_sig = sig
            self.report_dirty((0, CHIP_Y, self.app.w, self.app.h))
            return True
        return ct_changed

    # --------------------------------------------------------------- drawing
    def content_area(self):
        h = self.app.h
        return (0, LIST_TOP, self.app.w, h - BOT_H - 8 - LIST_TOP)


    def model(self):
        """WEB-PROTOCOL.md §4.3. Reads tick()'s cached rows — it never
        re-snapshots the monitor, which takes a lock and mutates rate state.

        Diverges from the drafted §4.3 in three places, in every case because
        the box does not have the data the draft assumed:
          * `name` is always None. There is no DBC decode anywhere in
            kilodash; CAN rows are nameless by design and semantic naming
            lives on the NMEA2K screen.
          * `dlc` is derived from the payload length — busmon does not store
            DLC separately, and a remote frame carries no bytes at all.
          * `state` is this screen's own presentation state, not a controller
            read. canbus.py deliberately performs no I/O for presentation, so
            `bus-off` and `error-passive` are not observable from here.
        These are spec amendments, not omissions — see the ratification note.
        """
        rows = []
        src = self._rows or []
        for r in src[:64]:
            data = r.get("data") or b""
            rows.append({
                "id": ("0x%08X" % r["id"]) if r.get("ext") else ("0x%03X" % r["id"]),
                "ext": bool(r.get("ext")),
                "count": r.get("count", 0),
                "hz": round(r.get("rate", 0.0), 1),
                "dlc": len(data),
                "data": " ".join("%02X" % b for b in data),
                "name": None,
                "alert": bool(r.get("alert")),
            })
        st = self._stats or {}
        return {
            "kind": "canbus",
            "iface": self.iface,
            "bitrate": BITRATES[self.rate_idx],
            "state": self._bus_state(),
            "frame_rate": round(self.rx_rate or 0.0, 1),
            "total": st.get("total", 0),
            "rows": rows,
            "truncated": len(src) > 64,
            "buttons": self.model_buttons(),
        }


    def model_buttons(self):
        """`provision` is confirm-guarded: it modifies the host system. The
        panel gates detect/provision via Button.enabled during draw, and
        handle_button never hit-tests, so those gates are restated here."""
        busy = bool(self.detect_task) or bool(getattr(self, "prov_task", None))
        st = self._stats or {}
        return [
            {"id": "detect", "label": "DETECT",
             "enabled": bool(self.iface) and self.detect_task is None,
             "confirm": False},
            {"id": "provision", "label": "PROVISION", "enabled": not busy,
             "confirm": True},
            {"id": "log", "label": "LOG STOP" if self.logging else "LOG START",
             "enabled": bool(self.iface), "confirm": False},
            {"id": "save", "label": "SAVE RING",
             "enabled": not busy and bool(st.get("ring")), "confirm": False},
        ]

    def handle_button(self, bid):
        if bid == "detect":
            if self.iface and self.detect_task is None:
                self._detect()
            return True
        if bid == "provision":
            self._start_provision(); return True
        if bid == "log":
            if self.iface:
                self._toggle_log()
            return True
        if bid == "save":
            if (self._stats or {}).get("ring"):
                self._save_ring()
            return True
        return False

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        self._row_hits = []
        if self.tab == "bus" and self.sel is None:
            self._draw_table(d, th, w, h)     # under the fixed chrome
        d.rectangle((0, HEADER_H, w, LIST_TOP), fill=th.bg)
        self._draw_iface_card(d, th, w)
        self._draw_tabs(d, th, w)
        if self.tab == "setup":
            self.content_h = 0
            d.rectangle((0, BODY_Y, w, h), fill=th.bg)
            self._draw_setup(d, th, w, h)
        elif self.sel is not None:
            self.content_h = 0
            d.rectangle((0, BODY_Y, w, h), fill=th.bg)
            self._draw_byte_grid(d, th, w, h)
        else:
            self._draw_chips(d, th, w)
            self._draw_bottom(d, th, w, h)

    # ---- shared chrome ----
    def _bus_state(self):
        if not self.iface:
            return "none"
        r = self.reader
        if r and r.error and not r.alive:
            return "fault"
        if r and r.alive:
            return "listen"
        return "standby"

    def _draw_iface_card(self, d, th, w):
        """Bus-state banner: glyph + caps-mono state + iface at left, the
        tappable CanTick source chip at right. Hazard cap on RX fault only."""
        y = IFACE_Y
        st = BUS_STATES[self._bus_state()]
        col = getattr(th, st["col"])
        d.rectangle((14, y, w - 14, y + IFACE_H), fill=th.card,
                    outline=col, width=2)
        if st["col"] == "bad":            # fault wears the caution cap
            hazard(d, (150, y + 6, 204, y + IFACE_H - 6), col)
        state_glyph(d, st["glyph"], 34, y + IFACE_H // 2, 11, col)
        d.text((52, y + 8), st["label"],
               font=T.font(14, bold=True, mono=True), fill=col)
        d.text((52, y + 27), self.iface or spaced("NO IFACE"),
               font=T.font(11, bold=True, mono=True),
               fill=th.fg if self.iface else th.muted)
        # CanTick source-mode chip (tap toggles the WiFi link)
        state = self.link.state if (self.ct_blk["enabled"] and self.link) \
            else "off"
        chip_box = (w - 110, y + 10, w - 24, y + IFACE_H - 10)
        d.rectangle(chip_box, fill=th.card_hi)
        sq = (w - 102, y + 20, w - 92, y + 30)
        if state == "up":
            status_square(d, sq, "lit", th.ok)
        elif state in ("listening", "backoff"):
            status_square(d, sq, "slash", th.warn)
        else:
            status_square(d, sq, "hollow", th.muted)
        d.text((w - 86, y + 18), "CANTICK",
               font=T.font(11, bold=True, mono=True),
               fill=th.fg if state != "off" else th.muted)
        self._btns["cantick"] = chip_box

    def _draw_tabs(self, d, th, w):
        y = TAB_Y
        half = (w - 28) // 2
        f = T.font(12, bold=True, mono=True)
        for key, label, x0 in (("bus", "BUS", 14),
                               ("setup", "SETUP", 14 + half)):
            active = self.tab == key
            box = (x0, y, x0 + half, y + TAB_H)
            d.rectangle(box, fill=th.card_hi if active else th.card,
                        outline=th.accent if active else None, width=1)
            lab = spaced(label)
            tw = d.textlength(lab, font=f)
            d.text((x0 + half / 2 - tw / 2, y + 9), lab, font=f,
                   fill=th.accent if active else th.muted)
            self._btns[f"tab_{key}"] = box

    # ---- bus tab ----
    def _draw_chips(self, d, th, w):
        y = CHIP_Y
        d.rectangle((0, y - 2, w, LIST_TOP - 2), fill=th.bg)
        chips = [
            ("live", "LIVE" if self.view_live else "IDS", True),
            ("watched", "W", self.watched_only),
            ("changed", "Δ", self.changed_only),
        ]
        if self.filt_id is not None:
            chips.append(("idfilt",
                          busmon.fmt_id(*self.filt_id)[:8] + " ×", True))
        x = 14
        f = T.font(12, bold=True, mono=True)
        for key, label, on in chips:
            cw = max(40, d.textlength(label, font=f) + 18)
            box = (x, y, x + cw, y + CHIP_H - 4)
            d.rectangle(box, fill=th.card_hi if on else th.card,
                        outline=th.accent if on else th.card_hi, width=1)
            d.text((x + 9, y + 6), label, font=f,
                   fill=th.accent if on else th.muted)
            self._btns[f"chip_{key}"] = box
            x += cw + 6
        # alert badge — the non-modal alarm surface (badge + row flash)
        hits = self._stats["hits"]
        if hits:
            label = f"⚠ {min(hits, 999)}"
            bw = d.textlength(label, font=f) + 16
            loud = self._stats["alerting"] > 0
            d.rectangle((w - 14 - bw, y, w - 14, y + CHIP_H - 4),
                        fill=th.warn if loud else th.card,
                        outline=th.warn, width=1)
            d.text((w - 14 - bw + 8, y + 6), label, font=f,
                   fill=th.ink if loud else th.warn)

    def _visible_rows(self):
        if self.filt_id is not None:
            return [r for r in self._rows if (r["id"], r["ext"]) ==
                    self.filt_id]
        rows = self._rows
        if self.watched_only:
            watched = self.mon.watched_ids()
            rows = [r for r in rows if r["id"] in watched]
        if self.changed_only:
            rows = [r for r in rows if r["changed"]]
        return rows

    def _draw_table(self, d, th, w, h):
        pane_h = self.content_area()[3]
        if self.view_live:
            self._draw_live(d, th, w, pane_h)
            return
        rows = self._visible_rows()
        self._vis_rows = rows
        if not rows:
            self.content_h = pane_h
            d.rectangle((0, LIST_TOP, w, LIST_TOP + pane_h), fill=th.bg)
            msg = (spaced("AWAITING FRAMES")
                   if self.reader and self.reader.alive
                   else (self.reader.error if self.reader and self.reader.error
                         else spaced("NO RX SOCKET")))
            d.text((24, LIST_TOP + 14), msg[:40],
                   font=T.font(11, bold=True, mono=True), fill=th.muted)
            brackets(d, (4, LIST_TOP + 1, w - 4, LIST_TOP + pane_h - 1),
                     th.muted, arm=10)
            return
        self.content_h = max(len(rows) * ROW_H + 4, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        fid = T.font(14, bold=True, mono=True)
        fsm = T.font(11, mono=True)
        fpl = T.font(13, bold=True, mono=True)
        byte_w = (w - 48) / 8
        for i, r in enumerate(rows):
            y = i * ROW_H
            box = (14, y + 1, w - 14, y + ROW_H - 1)
            if r["alert"]:
                sd.rectangle(box, fill=th.card_hi, outline=th.warn, width=2)
            else:
                sd.rectangle(box, fill=th.card)
            watched = bool(r["watch_pos"])
            sd.text((24, y + 3), busmon.fmt_id(r["id"], r["ext"]),
                    font=fid, fill=th.accent if watched else th.fg)
            meta = f"{min(r['count'], 9_999_999):>7} {r['rate']:5.0f}/s"
            mw = sd.textlength(meta, font=fsm)
            sd.text((w - 24 - mw, y + 5), meta, font=fsm, fill=th.muted)
            # payload: per-byte so changed bytes highlight + watches mark
            for p in range(len(r["data"])):
                bx = 24 + p * byte_w
                col = th.warn if (r["changed"] >> p) & 1 else th.fg
                sd.text((bx, y + 17), f"{r['data'][p]:02X}", font=fpl,
                        fill=col)
                if p in r["watch_pos"]:
                    sd.rectangle((bx - 1, y + 31, bx + 17, y + 32),
                                 fill=th.accent)
            if not r["data"]:
                sd.text((24, y + 17), "R (remote frame)", font=fsm,
                        fill=th.muted)
        self.paste_list(LIST_TOP, pane_h, surf)
        # registration brackets frame the traffic pane (fixed, rows scroll under)
        brackets(d, (4, LIST_TOP + 1, w - 4, LIST_TOP + pane_h - 1),
                 th.muted, arm=10)

    def _draw_live(self, d, th, w, pane_h):
        n = max(4, int(pane_h // LIVE_ROW_H) + 6)
        recs = self.mon.tail(n, **self._filters())
        self.content_h = pane_h              # newest-first, no scroll needed
        d.rectangle((0, LIST_TOP, w, LIST_TOP + pane_h), fill=th.bg)
        d.text((16, LIST_TOP + 3), spaced("LIVE TAP"),
               font=T.font(9, bold=True, mono=True), fill=th.muted)
        f = T.font(12, mono=True)
        y = LIST_TOP + 18
        for ts, cid, ext, rtr, data, changed in recs:
            if y > LIST_TOP + pane_h - LIVE_ROW_H:
                break
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            line = f"{stamp} {busmon.fmt_id(cid, ext)}#" \
                   + ("R" if rtr else data.hex().upper())
            d.text((16, y), line[:44], font=f,
                   fill=th.warn if changed else th.fg)
            y += LIVE_ROW_H
        if not recs:
            d.text((24, LIST_TOP + 24), spaced("RING EMPTY"),
                   font=T.font(11, bold=True, mono=True), fill=th.muted)
        brackets(d, (4, LIST_TOP + 1, w - 4, LIST_TOP + pane_h - 1),
                 th.muted, arm=10)

    def _draw_bottom(self, d, th, w, h):
        y = h - BOT_H - 4
        d.rectangle((0, y - 2, w, h), fill=th.bg)
        busy = self.export_task and not self.export_task.done
        b = Button((14, y, w // 2 - 4, y + BOT_H - 6),
                   "SAVING…" if busy else "SAVE RING", kind="primary",
                   font_size=15)
        b.enabled = not busy and self._stats["ring"] > 0
        b.draw(d, th)
        self._btns["save"] = b.box if b.enabled else None
        info = f"{self._stats['ring']:,} BUF"
        if self.logging:
            info += " · REC"
        # REC is a deliberate capture, not a fault — amber, never red
        d.text((w // 2 + 8, y + 4), info, font=T.font(12, mono=True),
               fill=th.warn if self.logging else th.muted)
        d.text((w // 2 + 8, y + 20), self.status[:22], font=T.font(11),
               fill=th.muted)

    # ---- byte-grid overlay (watch config; in-screen panel, alerts stay
    # non-modal — this is only ever opened by an explicit row tap) ----
    def _sel_row(self):
        for r in self._rows:
            if (r["id"], r["ext"]) == self.sel:
                return r
        return None

    def _draw_byte_grid(self, d, th, w, h):
        cid, ext = self.sel
        row = self._sel_row()
        data = row["data"] if row else b""
        y = BODY_Y
        d.text((16, y + 2), busmon.fmt_id(cid, ext),
               font=T.font(20, bold=True, mono=True), fill=th.accent)
        d.text((16, y + 26), f"{row['count']:,} FRAMES · "
               f"{row['rate']:.0f}/S" if row else spaced("SIGNAL LOST"),
               font=T.font(11, mono=True), fill=th.muted)
        cb = Button((w - 84, y, w - 14, y + 36), "CLOSE", kind="ghost",
                    font_size=14)
        cb.draw(d, th)
        self._btns["grid_close"] = cb.box
        # 2×4 grid of byte cells
        y += 44
        gap = 6
        cw = (w - 28 - 3 * gap) / 4
        ch = 46
        fhex = T.font(18, bold=True, mono=True)
        fsm = T.font(10, mono=True)
        for p in range(8):
            r_, c_ = divmod(p, 4)
            x0 = 14 + c_ * (cw + gap)
            y0 = y + r_ * (ch + gap)
            box = (x0, y0, x0 + cw, y0 + ch)
            selected = self.sel_byte == p
            has = p < len(data)
            w_ = self.mon.watch_on(cid, p)
            d.rectangle(box, fill=th.card_hi if selected else th.card,
                        outline=th.accent if w_ else th.card_hi,
                        width=2 if w_ else 1)
            d.text((x0 + 8, y0 + 4), str(p), font=fsm, fill=th.muted)
            d.text((x0 + cw / 2 - 10, y0 + 16),
                   f"{data[p]:02X}" if has else "··", font=fhex,
                   fill=th.fg if has else th.muted)
            if w_:
                mark = "Δ" if w_["mode"] == busmon.WATCH_CHANGE \
                    else f"={w_['value']:02X}"
                d.text((x0 + cw - 8 - d.textlength(mark, font=fsm), y0 + 4),
                       mark, font=fsm, fill=th.accent)
            self._btns[f"cell_{p}"] = box
        # the byte register is this mode's framed instrument
        brackets(d, (4, y - 6, w - 4, y + 2 * ch + gap + 6), th.muted, arm=10)
        y += 2 * ch + gap + 10
        # watch actions for the selected byte
        if self.sel_byte is not None:
            p = self.sel_byte
            w_ = self.mon.watch_on(cid, p)
            half = (w - 28 - 8) // 2
            b1 = Button((14, y, 14 + half, y + 42), "ALERT ON Δ",
                        kind="primary", font_size=13)
            b2 = Button((22 + half, y, w - 14, y + 42), "ALERT ON VALUE…",
                        kind="normal", font_size=13)
            b1.draw(d, th)
            b2.draw(d, th)
            self._btns["w_change"] = b1.box
            self._btns["w_match"] = b2.box
            y += 50
            if w_:
                hits = f" ({w_['hits']} HIT{'S' if w_['hits'] != 1 else ''})"
                # striking a watch is a stand-down, not a fault — amber
                b3 = Button((14, y, w - 14, y + 40),
                            "REMOVE WATCH" + hits, color=th.warn,
                            font_size=14)
                b3.draw(d, th)
                self._btns["w_clear"] = b3.box
                y += 48
        else:
            d.text((16, y + 4), "TAP A BYTE TO ARM A WATCH",
                   font=T.font(10, bold=True, mono=True), fill=th.muted)
            y += 30
        filt_here = self.filt_id == self.sel
        fb = Button((14, y, w - 14, y + 40),
                    "CLEAR ID FILTER" if filt_here else "FILTER THIS ID",
                    kind="ghost", font_size=14)
        fb.draw(d, th)
        self._btns["grid_filt"] = fb.box

    # ---- setup tab (the pre-split controls, same behavior) ----
    def _draw_setup(self, d, th, w, h):
        y = BODY_Y
        # bitrate selector
        d.rectangle((14, y, w - 14, y + 50), fill=th.card,
                    outline=th.card_hi, width=1)
        self._btns["rate_prev"] = (14, y, 58, y + 50)
        self._btns["rate_next"] = (w - 58, y, w - 14, y + 50)
        d.text((28, y + 11), "‹", font=T.font(28, bold=True), fill=th.accent)
        d.text((w - 42, y + 11), "›", font=T.font(28, bold=True),
               fill=th.accent)
        fl = T.font(9, bold=True, mono=True)
        lab = spaced("BITRATE")
        d.text((w / 2 - d.textlength(lab, font=fl) / 2, y + 6), lab,
               font=fl, fill=th.muted)
        rate = f"{BITRATES[self.rate_idx]//1000} KBIT/S"
        rt = T.font(18, bold=True, mono=True)
        d.text((w / 2 - d.textlength(rate, font=rt) / 2, y + 21), rate,
               font=rt, fill=th.fg)
        y += 56

        # autodetect — shares the row with Provision when a CanTick is on USB
        provisionable = self.app.devices.has("cantick")
        det_right = (w // 2 - 4) if provisionable else (w - 14)
        det_btn = Button((14, y, det_right, y + 46),
                         "AUTODETECT" if provisionable else
                         "AUTODETECT BITRATE", kind="normal", font_size=15)
        det_btn.enabled = bool(self.iface) and self.detect_task is None
        det_btn.draw(d, th)
        self._btns["detect"] = det_btn.box if det_btn.enabled else None
        if provisionable:
            busy = self.prov_task is not None and not self.prov_task.done
            prov_btn = Button((w // 2 + 4, y, w - 14, y + 46),
                              "PROVISIONING…" if busy else "PROVISION",
                              kind="primary", font_size=15)
            prov_btn.enabled = not busy
            prov_btn.draw(d, th)
            self._btns["provision"] = prov_btn.box if prov_btn.enabled else None
        y += 52

        # stopping a log is a stand-down (amber), not a fault
        log_btn = Button((14, y, w - 14, y + 48),
                         "STOP LOGGING" if self.logging else "START LOGGING",
                         kind="primary", font_size=16,
                         color=th.warn if self.logging else None)
        log_btn.enabled = bool(self.iface)
        log_btn.draw(d, th)
        self._btns["log"] = log_btn.box if log_btn.enabled else None
        y += 54

        d.rectangle((14, y, w - 14, y + 32), fill=th.card)
        d.text((26, y + 8), self.status[:34], font=T.font(13), fill=th.muted)
        y += 38

        # live traffic counter — repainted alone on the fast tick
        d.rectangle((14, y, w - 14, y + 66), fill=th.card,
                    outline=th.card_hi, width=1)
        d.text((26, y + 7), spaced("RX FRAMES"),
               font=T.font(10, bold=True, mono=True), fill=th.muted)
        live = self.tick_interval == FAST_TICK   # frames seen recently
        status_square(d, (w - 40, y + 8, w - 28, y + 20),
                      "lit" if live else "hollow",
                      th.ok if live else th.muted)
        count = "—" if self.rx_count is None else f"{self.rx_count:,}"
        d.text((26, y + 28), count, font=T.font(20, bold=True, mono=True),
               fill=th.fg)
        rate = f"{self.rx_rate:.0f}/S" if live else spaced("IDLE")
        rf = T.font(13, bold=True, mono=True)
        d.text((w - 28 - d.textlength(rate, font=rf), y + 26), rate, font=rf,
               fill=th.accent if live else th.muted)
        # est. bus load vs the selected bitrate (~112 bits/stuffed frame);
        # bounded, so it gets the segmented gauge
        load = self.rx_rate * 112 / BITRATES[self.rate_idx]
        seg_x = w - 24 - (8 * 10 - 2)
        llab = spaced("LOAD")
        d.text((seg_x - 8 - d.textlength(llab, font=fl), y + 48), llab,
               font=fl, fill=th.muted)
        seg_row(d, seg_x, y + 46, min(8, round(load * 8)) if live else 0, 8,
                th.warn if load >= 0.8 else th.ok, th.card_hi,
                seg_w=8, seg_h=10)
        self._traffic_box = (0, y - 2, w, y + 68)
        y += 72

        if self.ct_blk["enabled"] and y + 50 < h:
            self._draw_cantick_card(d, th, y, w)
            self._ct_box = (0, y - 2, w, y + 52)

    def _draw_cantick_card(self, d, th, y, w):
        """Heartbeat health card (PROTOCOL.md §2): device, mode, rssi, live
        rx/s, drop, and the fresh/stale square (lit ok / slashed amber —
        a stale heartbeat is degraded, not a fault)."""
        d.rectangle((14, y, w - 14, y + 50), fill=th.card,
                    outline=th.card_hi, width=1)
        rec = self.hb.latest() if self.hb else None
        if not rec:
            state = self.link.state if self.link else "off"
            status_square(d, (24, y + 19, 36, y + 31), "hollow", th.muted)
            d.text((44, y + 17), f"NO HEARTBEAT · LINK {state.upper()}"[:27],
                   font=T.font(11, bold=True, mono=True), fill=th.muted)
            return
        status_square(d, (24, y + 9, 36, y + 21),
                      "lit" if rec["fresh"] else "slash",
                      th.ok if rec["fresh"] else th.warn)
        head = f"{rec['name']} · {rec['mode'] or '?'} · {rec['rssi']}dBm"
        d.text((44, y + 6), head[:26], font=T.font(12, bold=True, mono=True),
               fill=th.fg)
        warn = self.hb.version_warning
        if warn:
            sub = warn
            sub_col = th.warn
        else:
            age = f"{rec['age']:.0f}s ago" if not rec["fresh"] else \
                f"{rec['rx_rate']:.0f} rx/s"
            sub = f"{age} · drop {rec['drop']}"
            sub_col = th.muted
        d.text((44, y + 27), sub[:20 if rec["drop_rising"] else 32],
               font=T.font(12, mono=True), fill=sub_col)
        # a rising drop counter is the early bus-overrun warning — loud,
        # but it's a warning, not a fault: amber
        if rec["drop_rising"]:
            badge = f"DROP {rec['drop']}"
            bf = T.font(13, bold=True)
            bw = d.textlength(badge, font=bf)
            d.rectangle((w - 34 - bw, y + 13, w - 22, y + 37), fill=th.warn)
            d.text((w - 28 - bw, y + 16), badge, font=bf, fill=th.ink)

    # ------------------------------------------------------------------ input
    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _ask_match_value(self, cid, ext, pos):
        def done(text):
            self.app.close_keyboard()
            try:
                val = int(text.strip(), 16)
                self.mon.set_watch(cid, pos, busmon.WATCH_MATCH, value=val)
                self.app.toast(f"Watch {busmon.fmt_id(cid, ext)}[{pos}] "
                               f"== {val:02X}")
            except (ValueError, TypeError):
                self.app.toast("Not a hex byte (00–FF)")
        kb = Keyboard(self.app.w, self.app.h,
                      title=f"Match value (hex) — byte {pos}", secret=False,
                      on_done=done, on_cancel=self.app.close_keyboard)
        kb.numeric = True
        self.app.open_keyboard(kb)

    def _tap_byte_grid(self, x, y):
        cid, ext = self.sel
        if self._in("grid_close", x, y):
            self.sel = self.sel_byte = None
            return True
        for p in range(8):
            if self._in(f"cell_{p}", x, y):
                self.sel_byte = None if self.sel_byte == p else p
                return True
        if self.sel_byte is not None:
            p = self.sel_byte
            if self._in("w_change", x, y):
                self.mon.set_watch(cid, p, busmon.WATCH_CHANGE)
                self.app.toast(f"Watch {busmon.fmt_id(cid, ext)}[{p}] Δ")
                return True
            if self._in("w_match", x, y):
                self._ask_match_value(cid, ext, p)
                return True
            if self._in("w_clear", x, y):
                self.mon.clear_watch(cid, p)
                return True
        if self._in("grid_filt", x, y):
            self.filt_id = None if self.filt_id == self.sel else self.sel
            self.sel = self.sel_byte = None
            return True
        return True                       # overlay swallows stray taps

    def handle_tap(self, x, y):
        if self._in("cantick", x, y):
            self._toggle_cantick()
            return True
        if self._in("tab_bus", x, y):
            self.tab = "bus"
            self._bus_sig = None
            return True
        if self._in("tab_setup", x, y):
            self.tab = "setup"
            self.sel = self.sel_byte = None
            return True
        if self.tab == "bus" and self.sel is not None:
            return self._tap_byte_grid(x, y)
        if self.tab == "bus":
            return self._tap_bus(x, y)
        return self._tap_setup(x, y)

    def _tap_bus(self, x, y):
        if self._in("chip_live", x, y):
            self.view_live = not self.view_live
            self.scroll = 0
            return True
        if self._in("chip_watched", x, y):
            self.watched_only = not self.watched_only
            return True
        if self._in("chip_changed", x, y):
            self.changed_only = not self.changed_only
            return True
        if self._in("chip_idfilt", x, y):
            self.filt_id = None
            return True
        if self._in("save", x, y):
            self._save_ring()
            return True
        area = self.content_area()
        if area[1] <= y < area[1] + area[3]:
            if self.view_live:
                return False              # live list rows aren't tap targets
            rows = getattr(self, "_vis_rows", [])
            i = int((y - area[1] + self.scroll) // ROW_H)
            if 0 <= i < len(rows):
                self.sel = (rows[i]["id"], rows[i]["ext"])
                self.sel_byte = None
                return True
        return False

    def _tap_setup(self, x, y):
        if self._in("rate_prev", x, y):
            self.rate_idx = (self.rate_idx - 1) % len(BITRATES)
            return True
        if self._in("rate_next", x, y):
            self.rate_idx = (self.rate_idx + 1) % len(BITRATES)
            return True
        if self._in("detect", x, y):
            self._detect()
            return True
        if self._in("provision", x, y):
            self._start_provision()
            return True
        if self._in("log", x, y):
            self._toggle_log()
            return True
        return False
