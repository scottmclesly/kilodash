"""Raw-bus forensics model for the CAN screen (RX-only, headless, testable).

Everything the reorganized CAN screen shows lives here, decoupled from PIL
and from the socket: the seen-IDs table (one row per arbitration ID: count,
rate, last payload, changed-bytes mask), per-byte watches in both modes
(change-detection and value-match), the bounded ring-buffer log, and the
candump-format export (replayable off-box with can-utils, loadable in
SavvyCAN).

Scope (CAN-N2K-Split-TODO, hard constraint): **diagnostics only — this
module and the CAN screen construct no TX frames and never write to the
bus.** The RxReader socket is used exclusively to recv(); the one TX
exception in the whole system (heartbeat/reply behavior required by bus
participation, e.g. NMEA2000 address claim) lives in the link layer
(CanTick firmware / a future N2K stack), never here and never in any
user-facing control. tests/test_busmon.py asserts this module and the CAN
screen contain no send/write calls at all (positive allow-list + reject
pass, enforced in code, not by convention).
"""

import os
import socket
import struct
import threading
import time

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x7FF

_FRAME_FMT = "<IB3x8s"          # struct can_frame: id, dlc, pad, data[8]
FRAME_SIZE = struct.calcsize(_FRAME_FMT)   # 16

RING_MAX = 50_000               # bounded log (TODO: "e.g. 50k frames")
RATE_WINDOW = 2.0               # seen-IDs rate smoothing window, seconds
ALERT_FLASH_S = 1.5             # how long a watch hit keeps its row flashing

WATCH_CHANGE = "change"         # alert when byte differs from last-seen value
WATCH_MATCH = "match"           # alert when byte becomes the configured value


def parse_frame(buf):
    """(can_id, ext, rtr, data) from one kernel can_frame, or None for
    error frames / short reads."""
    if len(buf) < FRAME_SIZE:
        return None
    raw_id, dlc, data = struct.unpack(_FRAME_FMT, buf[:FRAME_SIZE])
    if raw_id & CAN_ERR_FLAG:
        return None
    ext = bool(raw_id & CAN_EFF_FLAG)
    rtr = bool(raw_id & CAN_RTR_FLAG)
    cid = raw_id & (CAN_EFF_MASK if ext else CAN_SFF_MASK)
    return cid, ext, rtr, b"" if rtr else data[:min(dlc, 8)]


def fmt_id(cid, ext):
    """candump-style arbitration id: 3 hex digits SFF, 8 hex EFF."""
    return f"{cid:08X}" if ext else f"{cid:03X}"


def log_line(ts, iface, cid, ext, rtr, data):
    """One candump `-l` format line: `(ts) iface ID#DATA`."""
    payload = "R" if rtr else data.hex().upper()
    return f"({ts:.6f}) {iface} {fmt_id(cid, ext)}#{payload}"


class BusMonitor:
    """Thread-safe aggregate of everything heard on the bus."""

    def __init__(self, ring_max=RING_MAX):
        self._lock = threading.Lock()
        self._ids = {}              # cid -> entry dict
        self._ring = []             # bounded [(ts, cid, ext, rtr, data, changed)]
        self._ring_max = ring_max
        self._ring_head = 0         # index of oldest entry (circular)
        self.watches = {}           # (cid, pos) -> {mode, value, hits, last_hit}
        self.total = 0

    # ---------------------------------------------------------------- ingest
    def ingest(self, ts, cid, ext, rtr, data):
        with self._lock:
            self.total += 1
            e = self._ids.get(cid)
            if e is None:
                e = self._ids[cid] = {
                    "id": cid, "ext": ext, "count": 0, "last": b"",
                    "changed": 0, "t_last": ts, "rate": 0.0,
                    "_rc": 0, "_rt": ts,       # rate bookkeeping
                }
            prev = e["last"]
            changed = 0
            if not rtr:
                if prev:               # a first sighting is not a "change"
                    for i in range(max(len(prev), len(data))):
                        a = prev[i] if i < len(prev) else None
                        b = data[i] if i < len(data) else None
                        if a != b:
                            changed |= 1 << i
                self._eval_watches(ts, cid, prev, data)
                e["last"] = data
            e["count"] += 1
            e["changed"] = changed
            e["t_last"] = ts
            if len(self._ring) < self._ring_max:
                self._ring.append((ts, cid, ext, rtr, data, changed))
            else:
                self._ring[self._ring_head] = (ts, cid, ext, rtr, data, changed)
                self._ring_head = (self._ring_head + 1) % self._ring_max

    def _eval_watches(self, ts, cid, prev, data):
        for (wid, pos), w in self.watches.items():
            if wid != cid or pos >= len(data):
                continue
            old = prev[pos] if pos < len(prev) else None
            if w["mode"] == WATCH_CHANGE:
                hit = old is not None and data[pos] != old
            else:                       # value-match: fire on the transition
                hit = data[pos] == w["value"] and old != w["value"]
            if hit:
                w["hits"] += 1
                w["last_hit"] = ts

    # --------------------------------------------------------------- watches
    def set_watch(self, cid, pos, mode, value=None):
        if mode not in (WATCH_CHANGE, WATCH_MATCH):
            raise ValueError(mode)
        if mode == WATCH_MATCH and not 0 <= int(value) <= 0xFF:
            raise ValueError("match value must be one byte")
        with self._lock:
            self.watches[(cid, int(pos))] = {
                "mode": mode,
                "value": int(value) if mode == WATCH_MATCH else None,
                "hits": 0, "last_hit": 0.0,
            }

    def clear_watch(self, cid, pos):
        with self._lock:
            self.watches.pop((cid, int(pos)), None)

    def watch_on(self, cid, pos):
        return self.watches.get((cid, int(pos)))

    def watched_ids(self):
        return {cid for cid, _ in self.watches}

    # -------------------------------------------------------------- snapshot
    def snapshot(self, now=None):
        """Render-ready rows sorted by arbitration id + alert summary.
        Rates are computed here over RATE_WINDOW, not per frame, so ingest
        stays cheap at bus speed."""
        now = time.time() if now is None else now
        with self._lock:
            rows = []
            recent = 0
            for cid in sorted(self._ids):
                e = self._ids[cid]
                if now - e["_rt"] >= RATE_WINDOW:
                    e["rate"] = (e["count"] - e["_rc"]) / (now - e["_rt"])
                    e["_rc"], e["_rt"] = e["count"], now
                watch_pos = {p for (i, p) in self.watches if i == cid}
                alert = any(now - w["last_hit"] <= ALERT_FLASH_S
                            for (i, _), w in self.watches.items() if i == cid)
                rows.append({
                    "id": cid, "ext": e["ext"], "count": e["count"],
                    "rate": e["rate"], "data": e["last"],
                    "changed": e["changed"], "watch_pos": watch_pos,
                    "alert": alert, "t_last": e["t_last"],
                })
                recent += alert
            hits = sum(w["hits"] for w in self.watches.values())
            return rows, {"ring": len(self._ring), "total": self.total,
                          "hits": hits, "alerting": recent}

    def tail(self, n, **filters):
        """Newest-first slice of the ring for the candump-style live list."""
        out = []
        with self._lock:
            ln = len(self._ring)
            for k in range(ln):
                idx = (self._ring_head + ln - 1 - k) % ln if ln == self._ring_max \
                    else ln - 1 - k
                rec = self._ring[idx]
                if self._match(rec, filters):
                    out.append(rec)
                    if len(out) >= n:
                        break
        return out

    # ---------------------------------------------------------------- export
    def _match(self, rec, f):
        _ts, cid, _ext, _rtr, _data, changed = rec
        idm = f.get("id_match")
        if idm is not None and (cid & f.get("id_mask", CAN_EFF_MASK)) \
                != (idm & f.get("id_mask", CAN_EFF_MASK)):
            return False
        if f.get("watched_only") and cid not in self.watched_ids():
            return False
        if f.get("changed_only") and not changed:
            return False
        return True

    def export(self, cap_dir, iface, **filters):
        """Write the (filtered) ring as a candump `.log` file; returns
        (frame_count, path). Atomic-ish: written to .tmp then renamed, so a
        half-written export is never mistaken for a capture."""
        os.makedirs(cap_dir, exist_ok=True)
        path = os.path.join(
            cap_dir, f"canring_{time.strftime('%Y%m%d-%H%M%S')}.log")
        with self._lock:
            if len(self._ring) == self._ring_max:
                recs = self._ring[self._ring_head:] + \
                    self._ring[:self._ring_head]
            else:
                recs = list(self._ring)
            recs = [r for r in recs if self._match(r, filters)]
        with open(path + ".tmp", "w") as f:
            for ts, cid, ext, rtr, data, _ch in recs:
                f.write(log_line(ts, iface, cid, ext, rtr, data) + "\n")
        os.replace(path + ".tmp", path)
        return len(recs), path


class RxReader:
    """Background RX loop: one raw SocketCAN socket on the given iface (the
    one-tile-at-a-time model — no shared RX daemon), recv-only, feeding a
    BusMonitor. Never sends. Dies cleanly (with .error set) when the iface
    drops; the screen restarts it when the iface returns."""

    def __init__(self, iface, monitor):
        self.iface = iface
        self.mon = monitor
        self.error = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()
        return self

    @property
    def alive(self):
        return self._t.is_alive()

    def stop(self):
        self._stop.set()
        self._t.join(timeout=1.5)

    def _run(self):
        try:
            s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        except (AttributeError, OSError) as e:
            self.error = f"socket: {e}"
            return
        try:
            try:                    # absorb bursts between UI ticks
                s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            except OSError:
                pass
            s.settimeout(0.25)
            s.bind((self.iface,))
            while not self._stop.is_set():
                try:
                    buf = s.recv(FRAME_SIZE)
                except socket.timeout:
                    continue
                except OSError as e:
                    self.error = f"{self.iface}: {e.strerror or e}"
                    return
                parsed = parse_frame(buf)
                if parsed:
                    cid, ext, rtr, data = parsed
                    self.mon.ingest(time.time(), cid, ext, rtr, data)
        except OSError as e:
            self.error = f"{self.iface}: {e.strerror or e}"
        finally:
            s.close()
