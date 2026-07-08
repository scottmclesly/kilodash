"""CAN bus tool (CANable / gs_usb, slcan, or a WiFi CanTick).

Bring the interface up at a chosen bitrate, sniff a live frame counter, and log
traffic to a timestamped file. Includes a best-effort bitrate autodetect that
listens (listen-only) at each common rate and keeps the one that yields frames.

CanTick (see PROTOCOL.md — diagnostics + normal CAN participation only): while
this screen is open it hosts the supervised SLCAN-over-TCP link so a CanTick
dialing in over WiFi appears as an ordinary `slcan0`, listens (read-only) for
its heartbeat to drive the health card at the bottom, offers a one-time USB
provisioning push when a CanTick is plugged in, and — only when the Pi has no
uplink at all — raises the reversible fallback AP so a remote CanTick can
still reach us. Everything CanTick is torn down on leave, same lifecycle
discipline as logging. Listen-only is enforced on the device and shows in the
health card's `mode`.
"""

import glob
import os
import signal
import subprocess
import time

from PIL import Image, ImageDraw

from .. import cantick, system, theme as T
from ..devices import cantick_tty
from ..widgets import Button, rrect
from .base import Screen, HEADER_H

CAP_DIR = "/opt/kilodash/captures"
BITRATES = [1000000, 500000, 250000, 125000, 100000, 50000]

FAST_TICK = 0.05        # ~20 Hz while frames are flowing
IDLE_TICK = 0.5         # guardrail: silent/absent bus doesn't spin the CPU
AP_CHECK_IV = 5.0       # uplink re-probe cadence while the fallback AP is up


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


def _is_up(iface):
    try:
        return "state UP" in _sh("ip", "-br", "link", "show", iface).stdout \
            or "UP" in open(f"/sys/class/net/{iface}/operstate").read()
    except OSError:
        return False


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
    glyph = "can"
    tile_color_key = "bad"
    device_key = "can"
    scrollable = False

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = IDLE_TICK
        self.iface = None
        self.rate_idx = 1                 # default 500k
        self.log_proc = None
        self.log_path = None
        self.detect_task = None
        self.status = "Detecting interface…"
        self.rx_count = None              # displayed kernel RX frame counter
        self.rx_rate = 0.0                # frames/s over the last second
        self._rx_hist = []                # (t, count) samples, ≤1 s window
        self._traffic_box = None          # dirty rect for the live counter card
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
        if self.link is None:
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

    # --------------------------------------------------------------- lifecycle
    def on_enter(self):
        self.ct_blk = cantick.block(self.app.config)
        if self.ct_blk["enabled"]:
            self._ct_start()
        self.iface = _can_iface()
        self.status = (f"{self.iface} ready" if self.iface
                       else "CanTick listening…" if self.ct_blk["enabled"]
                       else "No CAN iface (slcan needs slcand)")
        self._rx_hist = []
        self.rx_count = _rx_frames(self.iface) if self.iface else None
        self.rx_rate = 0.0
        self._ct_sig = None

    def on_leave(self):
        try:
            if self.logging:
                self._toggle_log()
        finally:
            self._ct_stop()

    # ----------------------------------------------------------- CAN controls
    def _up(self):
        if self.iface:
            _bring_up(self.iface, BITRATES[self.rate_idx])
            self.status = f"{self.iface} up @ {BITRATES[self.rate_idx]//1000}k"

    def _down(self):
        if self.iface:
            _sh("ip", "link", "set", self.iface, "down")
            self.status = f"{self.iface} down"

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

    # ---------------------------------------------------------------- ticking
    def _tick_cantick(self):
        """CanTick housekeeping; True if the health card needs a repaint."""
        if not self.ct_blk["enabled"]:
            return False
        # a CanTick dialing in creates slcan0 after the screen was entered
        if not self.iface:
            self.iface = _can_iface()
            if self.iface:
                self.status = f"{self.iface} ready"
                self.rx_count = _rx_frames(self.iface)
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
            if self._ct_box:
                self.report_dirty(self._ct_box)
            return True
        return False

    def tick(self):
        ct_changed = self._tick_cantick()

        if self.detect_task:
            if not self.detect_task.done:
                return ct_changed         # status text is static while detecting
            br = self.detect_task.result
            if br:
                self.rate_idx = BITRATES.index(br)
                self.status = f"Detected {br//1000}k"
            else:
                self.status = "No frames — bus idle or unpowered"
            self.detect_task = None
            return True                   # full redraw: status + selector moved

        if not self.iface:
            self.tick_interval = IDLE_TICK
            return ct_changed

        # live traffic counter (the responsive part of this screen)
        now = time.monotonic()
        rx = _rx_frames(self.iface)
        changed = False
        if rx is not None:
            self._rx_hist.append((now, rx))
            while self._rx_hist and now - self._rx_hist[0][0] > 1.0:
                self._rx_hist.pop(0)
            t0, c0 = self._rx_hist[0]
            rate = (rx - c0) / (now - t0) if now > t0 else 0.0
            if rx != self.rx_count or f"{rate:.0f}" != f"{self.rx_rate:.0f}":
                self.rx_count, self.rx_rate = rx, rate
                changed = True
        # guardrail: only frames flowing keeps the fast tick; a silent or
        # wedged bus drops back to the slow interval automatically
        flowing = (rx is not None and len(self._rx_hist) >= 2
                   and rx != self._rx_hist[0][1])
        self.tick_interval = FAST_TICK if flowing else IDLE_TICK
        if changed and self._traffic_box:
            self.report_dirty(self._traffic_box)
        return changed or ct_changed

    # --------------------------------------------------------------- drawing
    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        y = HEADER_H + 12
        self._btns = {}

        rrect(d, (14, y, w - 14, y + 54), 10, fill=th.card)
        d.text((26, y + 8), "Interface", font=T.font(13), fill=th.muted)
        d.text((26, y + 26), self.iface or "not found",
                font=T.font(20, bold=True, mono=True),
                fill=th.ok if self.iface else th.bad)
        # CanTick source-mode chip (tap toggles the WiFi link)
        state = self.link.state if (self.ct_blk["enabled"] and self.link) \
            else "off"
        chip_col = {"up": th.ok, "listening": th.warn,
                    "backoff": th.warn}.get(state, th.muted)
        rrect(d, (w - 110, y + 12, w - 24, y + 42), 8, fill=th.card_hi)
        d.ellipse((w - 102, y + 22, w - 92, y + 32), fill=chip_col)
        d.text((w - 86, y + 19), "CanTick", font=T.font(13, bold=True),
               fill=th.fg if state != "off" else th.muted)
        self._btns["cantick"] = (w - 110, y + 12, w - 24, y + 42)
        y += 64

        # bitrate selector
        rrect(d, (14, y, w - 14, y + 50), 10, fill=th.card)
        self._btns["rate_prev"] = (14, y, 58, y + 50)
        self._btns["rate_next"] = (w - 58, y, w - 14, y + 50)
        d.text((28, y + 12), "‹", font=T.font(28, bold=True), fill=th.accent)
        d.text((w - 42, y + 12), "›", font=T.font(28, bold=True), fill=th.accent)
        rate = f"{BITRATES[self.rate_idx]//1000} kbit/s"
        rt = T.font(20, bold=True)
        d.text((w / 2 - d.textlength(rate, font=rt) / 2, y + 13), rate,
               font=rt, fill=th.fg)
        y += 60

        # autodetect — shares the row with Provision when a CanTick is on USB
        provisionable = self.app.devices.has("cantick")
        det_right = (w // 2 - 4) if provisionable else (w - 14)
        det_btn = Button((14, y, det_right, y + 48),
                         "Autodetect" if provisionable else
                         "Autodetect bitrate", kind="normal", font_size=18)
        det_btn.enabled = bool(self.iface) and self.detect_task is None
        det_btn.draw(d, th)
        self._btns["detect"] = det_btn.box if det_btn.enabled else None
        if provisionable:
            busy = self.prov_task is not None and not self.prov_task.done
            prov_btn = Button((w // 2 + 4, y, w - 14, y + 48),
                              "Provisioning…" if busy else "Provision",
                              kind="primary", font_size=18)
            prov_btn.enabled = not busy
            prov_btn.draw(d, th)
            self._btns["provision"] = prov_btn.box if prov_btn.enabled else None
        y += 56

        log_btn = Button((14, y, w - 14, y + 56),
                         "Stop logging" if self.logging else "Start logging",
                         kind="danger" if self.logging else "primary",
                         font_size=20)
        log_btn.enabled = bool(self.iface)
        log_btn.draw(d, th)
        self._btns["log"] = log_btn.box if log_btn.enabled else None
        y += 64

        rrect(d, (14, y, w - 14, y + 40), 8, fill=th.card)
        d.text((26, y + 12), self.status[:34], font=T.font(14), fill=th.muted)
        y += 48

        # live traffic counter — the only part repainted on the fast tick
        rrect(d, (14, y, w - 14, y + 70), 10, fill=th.card)
        d.text((26, y + 8), "RX FRAMES", font=T.font(11, bold=True),
               fill=th.muted)
        live = self.tick_interval == FAST_TICK   # frames seen in the last second
        d.ellipse((w - 42, y + 10, w - 28, y + 24),
                  fill=th.ok if live else th.card_hi)
        count = "—" if self.rx_count is None else f"{self.rx_count:,}"
        d.text((26, y + 28), count, font=T.font(24, bold=True, mono=True),
               fill=th.fg)
        rate = f"{self.rx_rate:.0f}/s" if live else "idle"
        rf = T.font(16, bold=True, mono=True)
        d.text((w - 28 - d.textlength(rate, font=rf), y + 36), rate, font=rf,
               fill=th.accent if live else th.muted)
        self._traffic_box = (0, y - 2, w, y + 72)
        y += 78

        if self.ct_blk["enabled"]:
            self._draw_cantick_card(d, th, y, w)
            self._ct_box = (0, y - 2, w, y + 52)

    def _draw_cantick_card(self, d, th, y, w):
        """Heartbeat health card (PROTOCOL.md §2): device, mode, rssi, live
        rx/s, drop, and the fresh/stale badge (Signal K indicator style)."""
        rrect(d, (14, y, w - 14, y + 50), 10, fill=th.card)
        rec = self.hb.latest() if self.hb else None
        if not rec:
            state = self.link.state if self.link else "off"
            d.ellipse((24, y + 19, 36, y + 31), fill=th.card_hi)
            d.text((44, y + 16), f"no CanTick heartbeat · link {state}",
                   font=T.font(13), fill=th.muted)
            return
        dot = th.ok if rec["fresh"] else th.bad
        d.ellipse((24, y + 9, 36, y + 21), fill=dot)
        head = f"{rec['name']} · {rec['mode'] or '?'} · {rec['rssi']}dBm"
        d.text((44, y + 6), head[:30], font=T.font(14, bold=True), fill=th.fg)
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
               font=T.font(13, mono=True), fill=sub_col)
        # a rising drop counter is the early bus-overrun warning — make it loud
        if rec["drop_rising"]:
            badge = f"DROP {rec['drop']}"
            bf = T.font(13, bold=True)
            bw = d.textlength(badge, font=bf)
            rrect(d, (w - 34 - bw, y + 13, w - 22, y + 37), 6, fill=th.bad)
            d.text((w - 28 - bw, y + 16), badge, font=bf, fill=th.bg)

    # ------------------------------------------------------------------ input
    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def handle_tap(self, x, y):
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
        if self._in("cantick", x, y):
            self._toggle_cantick()
            return True
        if self._in("log", x, y):
            self._toggle_log()
            return True
        return False
