"""NMEA2K — semantic decode of known PGNs against the table store.

The sibling of the CAN screen after the split: CAN is raw-bus forensics on
proprietary traffic; this screen is table-driven meaning. It is only as
good as its tables (TABLES.md): enabled + verified PGN tables from
/opt/kilodash/tables/pgn/ are re-validated on load and drive the decode
pipeline — raw frame → fast-packet reassembly → PGN lookup → field
extraction (kilodash/n2k.py).

Live view: one row per (PGN, source) with name, decoded key fields and
rate; tap → full field breakdown with units. Alerts, both non-modal
(status badge + row flash, never a dialog over a live bus view):
  - range-exit: tap a field in the breakdown, enter min,max;
  - appearance: "Alert on sight" in the breakdown (alarm/fault PGNs).
Unknown PGNs are counted and listed — undecoded traffic is signal — and a
tap hands the sample arbitration id over to the CAN screen's seen-IDs
mental model. Decoded records land in a bounded log, exported as JSON
lines to /opt/kilodash/captures/.

RX-only, diagnostics only: this screen opens its own SocketCAN socket on
the CAN iface while active (no shared RX daemon; one tile at a time) and
never transmits — tests/test_n2k.py enforces it with the same AST scan as
the CAN screen. The ONE transmit affordance in the whole UI lives here as
a *control*, not a TX path: the "Source GNSS → bus" button starts/stops
`n2k/node.py` (the allow-listed GNSS source node — address claim, claim
defense, ISO-request replies and five GNSS PGNs from live gpsd). The node
owns its own socket; this module still constructs no TX. While sourcing,
our own PGNs echo back into the decode view and are tagged with ▸ (own
claimed SA) — self-traffic verifies our TX but must never be mistaken for
the boat's GPS, and the GPS-vs-bus comparison excludes it.

While the CanTick WiFi bridge is enabled this screen hosts the supervised
link too (a CanTick needs a listener whichever tile is up); provisioning,
heartbeat health and the fallback AP stay on the CAN screen. No tables →
the tile stays visible and points at the Tables tile.

Presentation follows the ship-instrument look ratified on the Pomodoro and
Tables refactors (Cobb's Semiotic Standard): a hard-edged state banner with
a per-state glyph — the GNSS node's state when the node exists, else the
decode pipeline's; hazard end-caps and red reserved for genuine faults, fix
loss is an amber stand-by — a bracket-framed decode instrument pane,
spaced-caps mono readouts, square alert glyphs and a segmented log-fill
gauge.
"""

import math
import os
import time

from PIL import Image, ImageDraw

from .. import busmon, cantick, n2k, system, theme as T
from ..widgets import (Button, Keyboard, brackets, hazard, seg_row, spaced,
                       state_glyph, status_square)
from .base import Screen, HEADER_H
from .canbus import _can_iface

# repo-root packages (TABLES.md §1 / GPS.md §5 / n2k package docstring);
# run.py and the tests both put the repo root on sys.path
from gps import snapshot as gps_snapshot
from n2k import node as gnss
from tables import store

CAP_DIR = "/opt/kilodash/captures"

FAST_TICK = 0.1
IDLE_TICK = 0.5
READER_RETRY = 2.0

STATUS_Y = HEADER_H + 6          # 50   tables/iface card + alert badge
STATUS_H = 46
CHIP_Y = STATUS_Y + STATUS_H + 6  # 102  view chips (list) / title (detail)
CHIP_H = 30
LIST_TOP = CHIP_Y + CHIP_H + 6   # 138  rows / field-breakdown pane
BOT_H = 46
ROW_H = 40                       # (PGN, source) rows
UROW_H = 34                      # unknown-PGN rows
FROW_H = 30                      # breakdown field rows


class N2kScreen(Screen):
    title = "NMEA2K"
    glyph = "n2k"
    tile_color_key = "warn"
    device_key = "can"
    scrollable = True

    def __init__(self, app):
        super().__init__(app)
        self.tick_interval = IDLE_TICK
        self.iface = None
        self.tables = {}
        self.table_warns = []
        self.mon = None                   # n2k.N2kMonitor (built on enter)
        self.alerts = n2k.AlertBook()     # persists across visits
        self.reader = None
        self._reader_retry = 0.0
        self.link = None                  # hosted CanTick link (if enabled)
        self.ct_blk = None
        self.view_unknown = False
        self.sel = None                   # (pgn, src) breakdown target
        self.status = ""
        self.export_task = None
        self._rows, self._unknown = [], []
        self._stats = {"total": 0, "log": 0, "hits": 0, "alerting": 0,
                       "unknown": 0, "non_n2k": 0, "fp_dropped": 0}
        self._sig = None
        self._btns = {}
        self._vis = []

    # ------------------------------------------------------------- GNSS node
    # The node object outlives this screen deliberately (a bus participant
    # doesn't vanish when the user switches tiles): it hangs off the app and
    # only an explicit stop tap, fix loss (auto-stop) or socket death ends
    # TX. This screen only starts/stops/renders it — n2k/node.py owns the
    # socket and is the sole TX-allow-listed module (tests/test_txscan.py).
    def _node(self):
        return getattr(self.app, "gnss_node", None)

    def _node_state(self):
        node = self._node()
        return node.state if node else gnss.OFF

    def _own_sa(self):
        """Our claimed SA while the node holds one — for ▸ self-tagging
        and for excluding self-traffic from the GPS-vs-bus comparison."""
        node = self._node()
        if node and node.state in (gnss.CLAIMING, gnss.ACTIVE,
                                   gnss.STOPPED_FIX):
            return node.sa
        return None

    def _gnss_eligible(self):
        """Button appears only with the GPS jack occupied AND a current
        fix in the snapshot (GPS.md §3 staleness rule does the honesty)."""
        if not os.path.exists("/dev/gps0"):
            return False
        snap, _reason = gps_snapshot.read_position()
        return snap is not None

    def _toggle_gnss(self):
        node = self._node()
        if node:
            node.stop()
            self.app.gnss_node = None
            self.app.toast("GNSS source stopped — going silent")
            return
        if not self.iface:
            self.app.toast("No CAN iface to source onto")
            return
        if not self._gnss_eligible():
            self.app.toast("No GPS fix — a node never sources stale data")
            return
        self.app.gnss_node = gnss.GnssSourceNode(self.iface).start()
        self.app.toast("Source GNSS → bus: claiming address…")

    # --------------------------------------------------------------- lifecycle
    def _pick_iface(self):
        if (self.ct_blk and self.ct_blk["enabled"] and self.link
                and self.link.state == cantick.CanTickLink.UP
                and os.path.isdir(
                    f"/sys/class/net/{self.ct_blk['slcan_iface']}")):
            return self.ct_blk["slcan_iface"]
        return _can_iface()

    def on_enter(self):
        self.tables, self.table_warns = store.load_enabled()
        mon = n2k.N2kMonitor(self.tables)
        mon.alerts = self.alerts          # alert config survives re-entry
        self.mon = mon
        self.ct_blk = cantick.block(self.app.config)
        if self.ct_blk["enabled"]:
            try:
                self.link = cantick.CanTickLink(
                    iface=self.ct_blk["slcan_iface"],
                    tcp_port=self.ct_blk["tcp_port"],
                    bitrate=self.ct_blk["bitrate"])
                self.link.start()
            except cantick.CanTickError as e:
                self.link = None
                self.status = f"CanTick: {e}"[:34]
        self.iface = self._pick_iface()
        if not self.tables:
            self.status = "No PGN tables — open the Tables tile"
        elif not self.iface:
            self.status = "No CAN iface" + (
                " — CanTick listening…" if self.link else "")
        else:
            self.status = f"{self.iface} · {len(self.tables)} PGNs loaded"
        if self.table_warns:
            self.app.toast(f"{len(self.table_warns)} table warning(s)")
        self.sel = None
        self._sig = None
        self._reader_retry = 0.0
        self._ensure_reader()

    def on_leave(self):
        if self.reader:
            self.reader.stop()
            self.reader = None
        if self.link:
            try:
                self.link.stop()
            except Exception:   # noqa: BLE001 — teardown must run to the end
                pass
            self.link = None

    def _ensure_reader(self):
        if not self.tables or not self.iface \
                or not os.path.isdir(f"/sys/class/net/{self.iface}"):
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
        self.reader = busmon.RxReader(self.iface, self.mon).start()

    # ----------------------------------------------------------------- export
    def _save(self):
        if self.export_task and not self.export_task.done:
            return
        if not self._stats["log"]:
            self.app.toast("Nothing decoded yet")
            return
        pgn, src = self.sel if self.sel else (None, None)
        self.status = "Exporting…"
        self.export_task = system.Task(self.mon.export, CAP_DIR, pgn, src)

    # ---------------------------------------------------------------- ticking
    def tick(self):
        if self.export_task and self.export_task.done:
            res, err = self.export_task.result, self.export_task.error
            self.export_task = None
            if res:
                n, path = res
                self.status = f"Saved {n} records"
                self.app.toast(f"Decoded → {os.path.basename(path)} ({n})")
            else:
                self.status = f"Export failed: {err}"[:34]
            return True
        iface = self._pick_iface()
        if iface != self.iface:
            self.iface = iface
            self._reader_retry = 0.0
            self.status = f"{iface} ready" if iface else "No CAN iface"
            self._ensure_reader()
            return True
        if not self.mon or not self.tables:
            self.tick_interval = IDLE_TICK
            return False
        self._ensure_reader()
        self._rows, self._unknown, self._stats = self.mon.snapshot()
        node = self._node()
        if node and node.error:
            self.status = f"GNSS: {node.error}"[:34]
        elif node:
            sa = self._own_sa()
            self.status = f"GNSS {node.state}" + (
                f" @SA={sa:02X}" if sa is not None
                and node.state == gnss.ACTIVE else "")
        sig = (self._stats["total"], self._stats["hits"],
               self._stats["alerting"], self._stats["unknown"],
               len(self._rows),
               self.reader.error if self.reader else None,
               self._node_state(), self._own_sa(), self._gnss_eligible())
        moved = sig != self._sig
        self.tick_interval = FAST_TICK if moved else IDLE_TICK
        if moved:
            self._sig = sig
            self.report_dirty((0, STATUS_Y, self.app.w, self.app.h))
            return True
        return False

    # --------------------------------------------------------------- drawing
    def content_area(self):
        return (0, LIST_TOP, self.app.w,
                self.app.h - BOT_H - 8 - LIST_TOP)

    def draw_content(self, d, th):
        w, h = self.app.w, self.app.h
        self._btns = {}
        if self.sel is not None:
            self._draw_fields_pane(d, th, w)
        elif self.view_unknown:
            self._draw_unknown(d, th, w)
        else:
            self._draw_rows(d, th, w)
        d.rectangle((0, HEADER_H, w, LIST_TOP), fill=th.bg)
        self._draw_status(d, th, w)
        if self.sel is not None:
            self._draw_detail_head(d, th, w)
        else:
            self._draw_chips(d, th, w)
        # the one framed instrument: the decode pane (feed / breakdown)
        brackets(d, (4, LIST_TOP, w - 4, LIST_TOP + self.content_area()[3]),
                 th.muted, arm=12)
        self._draw_bottom(d, th, w, h)

    def _banner_state(self, th):
        """Dominant state for the banner: the GNSS node when it exists (the
        one transmitter on this screen), else the decode pipeline. Amber is
        stand-by/degraded; red stays reserved for genuine faults."""
        node = self._node()
        if node:
            return {
                gnss.CLAIMING: ("CLAIMING ADDRESS", th.accent, "spin"),
                gnss.ACTIVE: ("GNSS SOURCING", th.ok, "up"),
                gnss.CANNOT_CLAIM: ("GNSS NO ADDRESS", th.bad, "fault"),
                gnss.STOPPED_FIX: ("GNSS FIX LOST", th.warn, "standby"),
            }.get(node.state, ("GNSS STANDING BY", th.muted, "standby"))
        if not self.tables:
            return ("NO PGN TABLES", th.warn, "standby")
        if not self.iface:
            return ("NO BUS", th.muted, "standby")
        if self.reader and self.reader.error:
            return ("RX FAULT", th.bad, "fault")
        return ("DECODING", th.ok, "up")

    def _draw_status(self, d, th, w):
        y0, y1 = STATUS_Y, STATUS_Y + STATUS_H
        label, col, glyph = self._banner_state(th)
        sa = self._own_sa()
        if self._node_state() == gnss.ACTIVE and sa is not None:
            label += f" @{sa:02X}"
        d.rectangle((12, y0, w - 12, y1), fill=th.card, outline=col, width=2)
        hits = self._stats["hits"]
        if glyph == "fault" and not hits:    # faults wear the caution caps
            hazard(d, (w - 86, y0 + 4, w - 18, y1 - 4), col)
        state_glyph(d, glyph, 34, (y0 + y1) // 2, 11, col)
        d.text((52, y0 + 6), label[:20],
               font=T.font(15, bold=True, mono=True), fill=col)
        sub = self.status or \
            f"{len(self.tables)} PGN · {self.iface or 'no iface'}"
        d.text((52, y0 + 27), sub[:29], font=T.font(T.SUB, mono=True),
               fill=th.muted)
        if hits:
            f = T.font(12, bold=True, mono=True)
            n = f"{min(hits, 999)}"
            bw = d.textlength(n, font=f) + 28
            loud = self._stats["alerting"] > 0
            bx = w - 22 - bw
            d.rectangle((bx, y0 + 8, w - 22, y0 + 32),
                        fill=th.warn if loud else th.card_hi)
            status_square(d, (bx + 8, y0 + 16, bx + 16, y0 + 24), "lit",
                          th.ink if loud else th.warn)
            d.text((bx + 21, y0 + 13), n, font=f,
                   fill=th.ink if loud else th.warn)

    def _draw_chips(self, d, th, w):
        y = CHIP_Y
        f = T.font(12, bold=True, mono=True)
        x = 14
        for key, label, on in (
                ("pgns", "PGNS", not self.view_unknown),
                ("unknown", f"UNKNOWN ({len(self._unknown)})",
                 self.view_unknown)):
            cw = d.textlength(label, font=f) + 20
            d.rectangle((x, y, x + cw, y + CHIP_H - 4),
                        fill=th.card_hi if on else th.card,
                        outline=th.accent if on else th.card_hi, width=1)
            d.text((x + 10, y + 6), label, font=f,
                   fill=th.accent if on else th.muted)
            self._btns[f"chip_{key}"] = (x, y, x + cw, y + CHIP_H - 4)
            x += cw + 6
        fr = T.font(9, bold=True, mono=True)
        tail = (f"FP-DROP {self._stats['fp_dropped']}"
                if self._stats["fp_dropped"] else spaced("DECODE"))
        d.text((w - 14 - d.textlength(tail, font=fr), y + 9), tail, font=fr,
               fill=th.warn if self._stats["fp_dropped"] else th.muted)

    def _draw_rows(self, d, th, w):
        pane_h = self.content_area()[3]
        rows = self._rows
        self._vis = rows
        if not rows:
            self.content_h = pane_h
            d.rectangle((0, LIST_TOP, w, LIST_TOP + pane_h), fill=th.bg)
            if not self.tables:
                msg = spaced("NO TABLES ENABLED") \
                    if store.list_tables() else spaced("NO TABLES INSTALLED")
                d.text((24, LIST_TOP + 16), msg,
                       font=T.font(12, bold=True, mono=True), fill=th.warn)
                d.text((24, LIST_TOP + 38),
                       "HOME → TABLES CONVERTS VENDOR PDFS",
                       font=T.font(T.SUB, mono=True), fill=th.muted)
            else:
                msg = ("AWAITING DECODABLE FRAMES"
                       if self.reader and self.reader.alive else
                       (self.reader.error if self.reader and self.reader.error
                        else "NO RX SOCKET"))
                d.text((24, LIST_TOP + 16), msg[:40],
                       font=T.font(11, mono=True), fill=th.muted)
            return
        self.content_h = max(len(rows) * ROW_H + 4, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        fn = T.font(13, bold=True, mono=True)
        fs = T.font(11, mono=True)
        own_sa = self._own_sa()
        for i, r in enumerate(rows):
            y = i * ROW_H
            box = (14, y + 1, w - 14, y + ROW_H - 1)
            ours = own_sa is not None and r["src"] == own_sa
            if r["alert"]:
                sd.rectangle(box, fill=th.card_hi, outline=th.warn, width=2)
            elif ours:
                # bus echo of our own sourced PGNs: signal for verifying
                # our TX, but never mistakable for the boat's GPS
                sd.rectangle(box, fill=th.card, outline=th.accent, width=1)
            else:
                sd.rectangle(box, fill=th.card)
            name = ("▸" + r["name"]) if ours else r["name"]
            sd.text((24, y + 5), name[:24].upper(), font=fn,
                    fill=th.accent if ours else th.fg)
            rate = f"{r['rate']:4.0f}/s"
            sd.text((w - 24 - sd.textlength(rate, font=fs), y + 6), rate,
                    font=fs, fill=th.muted)
            vals = ", ".join(fl["disp"] for fl in r["fields"][:3])
            who = "self" if ours else f"s{r['src']:02X}"
            sub = f"{r['pgn']} {who}  {vals}"
            sd.text((24, y + 22), sub[:44], font=fs, fill=th.muted)
        self.paste_list(LIST_TOP, pane_h, surf)

    def _draw_unknown(self, d, th, w):
        pane_h = self.content_area()[3]
        rows = self._unknown
        self._vis = rows
        if not rows:
            self.content_h = pane_h
            d.rectangle((0, LIST_TOP, w, LIST_TOP + pane_h), fill=th.bg)
            d.text((24, LIST_TOP + 16), "NO UNKNOWN PGNS HEARD",
                   font=T.font(11, mono=True), fill=th.muted)
            return
        self.content_h = max(len(rows) * UROW_H + 4, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        fn = T.font(14, bold=True, mono=True)
        fs = T.font(11, mono=True)
        for i, u in enumerate(rows):
            y = i * UROW_H
            sd.rectangle((14, y + 1, w - 14, y + UROW_H - 1), fill=th.card)
            sd.text((24, y + 3), f"PGN {u['pgn']}", font=fn, fill=th.warn)
            cnt = f"{u['count']:,}"
            sd.text((w - 24 - sd.textlength(cnt, font=fn), y + 3), cnt,
                    font=fn, fill=th.fg)
            srcs = ",".join(f"{s:02X}" for s in u["srcs"][:6])
            sd.text((24, y + 19), f"SRC {srcs} · TAP → CAN SNIFF",
                    font=fs, fill=th.muted)
        self.paste_list(LIST_TOP, pane_h, surf)

    # ---- field breakdown (tap a row) ----
    def _sel_row(self):
        for r in self._rows:
            if (r["pgn"], r["src"]) == self.sel:
                return r
        return None

    def _draw_detail_head(self, d, th, w):
        pgn, src = self.sel
        r = self._sel_row()
        name = r["name"] if r else self.tables.get(pgn, {}).get("name", "?")
        d.text((16, CHIP_Y), name[:25].upper(),
               font=T.font(13, bold=True, mono=True), fill=th.accent)
        sub = f"PGN {pgn} · SRC {src:02X}" + (f" · {r['rate']:.0f}/s" if r
                                              else "")
        d.text((16, CHIP_Y + 18), sub, font=T.font(T.SUB, mono=True),
               fill=th.muted)
        cb = Button((w - 84, CHIP_Y - 4, w - 14, CHIP_Y + CHIP_H), "CLOSE",
                    kind="ghost", font_size=13)
        cb.draw(d, th)
        self._btns["close"] = cb.box

    def _delta_banner(self):
        """GPS-vs-bus comparison for the selected row (Phase 4): only for
        OTHER sources (self-comparison would validate our TX against its
        own origin) and only with a fresh local fix. Returns (text,
        severity_key) or None."""
        pgn, src = self.sel
        own = self._own_sa()
        if own is not None and src == own:
            return None
        r = self._sel_row()
        if not r:
            return None
        snap, _reason = gps_snapshot.read_position()
        if snap is None:
            return None
        delta = n2k.gps_bus_delta(r["fields"], snap)
        if not delta:
            return None
        parts = []
        if "dist_m" in delta:
            parts.append(f"Δpos {delta['dist_m']:,.0f} m")
        if "d_sog_mps" in delta:
            parts.append(f"ΔSOG {delta['d_sog_mps']:+.1f} m/s")
        if "d_cog_deg" in delta:
            parts.append(f"ΔCOG {delta['d_cog_deg']:+.0f}°")
        return "VS GPS  " + "  ".join(parts), n2k.delta_severity(delta)

    def _draw_fields_pane(self, d, th, w):
        pane_h = self.content_area()[3]
        r = self._sel_row()
        fields = r["fields"] if r else []
        self._vis = fields
        banner = self._delta_banner()
        self._has_banner = banner is not None
        n_rows = len(fields) + bool(banner)
        self.content_h = max(n_rows * FROW_H + 4, pane_h)
        surf = Image.new("RGB", (w, self.content_h), th.bg)
        sd = ImageDraw.Draw(surf)
        fn = T.font(12, mono=True)
        fv = T.font(13, bold=True, mono=True)
        fs = T.font(T.SUB, mono=True)
        pgn = self.sel[0]
        y_off = 0
        if banner:
            text, sev = banner
            color = {"ok": th.ok, "warn": th.warn, "bad": th.bad}[sev]
            sd.rectangle((14, 1, w - 14, FROW_H - 1),
                         fill=th.card_hi, outline=color, width=2)
            sd.text((24, 8), text[:40],
                    font=T.font(11, bold=True, mono=True), fill=color)
            y_off = FROW_H
        for i, fl in enumerate(fields):
            y = y_off + i * FROW_H
            watch = self.alerts.ranges.get((pgn, fl["name"]))
            sd.rectangle((14, y + 1, w - 14, y + FROW_H - 1),
                         fill=th.card_hi if watch else th.card)
            sd.text((24, y + 5), fl["name"][:18].upper(), font=fn, fill=th.fg)
            vw = sd.textlength(fl["disp"][:16], font=fv)
            sd.text((w - 24 - vw, y + 6), fl["disp"][:16], font=fv,
                    fill=th.accent if fl["value"] is not None else th.muted)
            if watch:
                lo = "" if watch["min"] is None else f"{watch['min']:g}"
                hi = "" if watch["max"] is None else f"{watch['max']:g}"
                status_square(sd, (24, y + 20, 30, y + 26), "lit", th.warn)
                sd.text((35, y + 16),
                        f"{lo}…{hi} · {watch['hits']} HITS",
                        font=fs, fill=th.warn)
        if not fields:
            sd.text((24, 12), "NOT HEARD — VALUES RETURN WITH NEXT FRAME",
                    font=T.font(T.SUB, mono=True), fill=th.muted)
        self.paste_list(LIST_TOP, pane_h, surf)

    def _draw_bottom(self, d, th, w, h):
        y = h - BOT_H - 4
        d.rectangle((0, y - 2, w, h), fill=th.bg)
        if self.sel is not None:
            pgn, src = self.sel
            sight = (pgn, None) in self.alerts.appear \
                or (pgn, src) in self.alerts.appear
            # an armed alert is caution amber, not red — nothing is wrong yet
            b = Button((14, y, w - 14 - 78, y + BOT_H - 6),
                       "SIGHT ALERT ARMED" if sight else "ALERT ON SIGHT",
                       color=th.warn if sight else None, font_size=14)
            b.draw(d, th)
            self._btns["sight"] = b.box
            fh = T.font(9, mono=True)
            d.text((w - 74, y + 8), "TAP FIELD →", font=fh, fill=th.muted)
            d.text((w - 74, y + 20), "RANGE ALERT", font=fh, fill=th.muted)
            return
        busy = self.export_task and not self.export_task.done
        b = Button((14, y, w // 2 - 4, y + BOT_H - 6),
                   "SAVING…" if busy else "SAVE LOG", kind="primary",
                   font_size=15)
        b.enabled = not busy and self._stats["log"] > 0
        b.draw(d, th)
        self._btns["save"] = b.box if b.enabled else None
        state = self._node_state()
        if state != gnss.OFF or self._gnss_eligible():
            # the one TX affordance in the UI — unmistakable label, and a
            # bus action, so it lives on the bus screen (not the GPS tile).
            # Stop / fix-lost are amber stand-downs; red only for the
            # genuine fault (address claim lost).
            label, kind, col = {
                gnss.OFF: ("SOURCE GNSS → BUS", "primary", None),
                gnss.CLAIMING: ("CLAIMING…", "normal", None),
                gnss.ACTIVE: ("GNSS ■ STOP", "normal", th.warn),
                gnss.CANNOT_CLAIM: ("NO ADDRESS", "danger", None),
                gnss.STOPPED_FIX: ("FIX LOST ■", "normal", th.warn),
            }[state]
            gb = Button((w // 2 + 4, y, w - 14, y + BOT_H - 6), label,
                        kind=kind, color=col, font_size=13)
            gb.draw(d, th)
            self._btns["gnss"] = gb.box
            return
        x0 = w // 2 + 8
        info = f"{self._stats['log']:,} REC"
        if self._stats["non_n2k"]:
            info += " · 11-BIT!"
        d.text((x0, y + 4), info, font=T.font(11, bold=True, mono=True),
               fill=th.warn if self._stats["non_n2k"] else th.muted)
        d.text((x0, y + 24), spaced("LOG"),
               font=T.font(8, bold=True, mono=True), fill=th.muted)
        lit = min(8, math.ceil(self._stats["log"] * 8 / n2k.RECORD_MAX))
        seg_row(d, x0 + 36, y + 22, lit, 8, th.accent, th.card_hi)

    # ------------------------------------------------------------------ input
    def _in(self, key, x, y):
        box = self._btns.get(key)
        return box and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _ask_range(self, pgn, field):
        w = self.alerts.ranges.get((pgn, field))
        hint = "min,max (blank side = open; empty = off)"

        def done(text):
            self.app.close_keyboard()
            t = text.strip()
            if not t:
                self.alerts.clear_range(pgn, field)
                self.app.toast(f"Range alert off: {field}")
                return
            try:
                lo_s, _, hi_s = t.partition(",")
                lo = float(lo_s) if lo_s.strip() else None
                hi = float(hi_s) if hi_s.strip() else None
                if lo is None and hi is None:
                    raise ValueError
            except ValueError:
                self.app.toast("Use min,max — e.g. 0,60 or ,60")
                return
            self.alerts.set_range(pgn, field, lo, hi)
            self.app.toast(f"Range alert set: {field}")
        kb = Keyboard(self.app.w, self.app.h, title=f"{field} — {hint}",
                      secret=False, on_done=done,
                      on_cancel=self.app.close_keyboard)
        kb.numeric = True
        if w:
            lo = "" if w["min"] is None else f"{w['min']:g}"
            hi = "" if w["max"] is None else f"{w['max']:g}"
            kb.text = f"{lo},{hi}"
        self.app.open_keyboard(kb)

    def _handover_to_can(self, sample_id):
        """One tap: unknown PGN → the CAN screen's raw-forensics view,
        pre-filtered to the sample arbitration id."""
        for scr in self.app.screens:
            if scr.__class__.__name__ == "CanScreen":
                scr.tab = "bus"
                scr.view_live = False
                scr.filt_id = (sample_id, True)
                scr.sel = scr.sel_byte = None
                self.app.open_screen(scr)
                return
        self.app.toast("CAN screen unavailable")

    def handle_tap(self, x, y):
        if self.sel is not None:
            if self._in("close", x, y):
                self.sel = None
                self.scroll = 0
                return True
            if self._in("sight", x, y):
                pgn, _src = self.sel
                on = self.alerts.toggle_appearance(pgn)
                self.app.toast(f"Sight alert {'on' if on else 'off'}: "
                               f"PGN {pgn}")
                return True
            area = self.content_area()
            if area[1] <= y < area[1] + area[3]:
                fields = self._vis
                i = int((y - area[1] + self.scroll) // FROW_H)
                i -= bool(getattr(self, "_has_banner", False))
                if 0 <= i < len(fields):
                    self._ask_range(self.sel[0], fields[i]["name"])
                    return True
            return True                    # breakdown swallows stray taps
        if self._in("chip_pgns", x, y):
            self.view_unknown = False
            self.scroll = 0
            return True
        if self._in("chip_unknown", x, y):
            self.view_unknown = True
            self.scroll = 0
            return True
        if self._in("save", x, y):
            self._save()
            return True
        if self._in("gnss", x, y):
            self._toggle_gnss()
            return True
        area = self.content_area()
        if area[1] <= y < area[1] + area[3] and self._vis:
            if self.view_unknown:
                i = int((y - area[1] + self.scroll) // UROW_H)
                if 0 <= i < len(self._vis):
                    self._handover_to_can(self._vis[i]["sample_id"])
                    return True
                return False
            i = int((y - area[1] + self.scroll) // ROW_H)
            if 0 <= i < len(self._vis):
                r = self._vis[i]
                self.sel = (r["pgn"], r["src"])
                self.scroll = 0
                return True
        return False
