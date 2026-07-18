"""Minimal gpsd JSON-socket client (localhost:2947) — the one gpsd
transport in the tree.

Deliberately NOT the `gps` python bindings (see gps/__init__.py): a raw
line-oriented socket keeps the dependency surface at zero and the parsing
in our hands. Three consumers share this: the snapshot writer
(gps/snapshotd.py), the GPS tile (rich SKY/TPV detail) and the N2K GNSS
source node (live data, never the snapshot file — GPS.md §5).

GpsdListener is crash-only: a background thread that keeps the latest TPV
and SKY reports with receive timestamps, reconnecting with backoff while
gpsd is down. Consumers poll state(); nobody blocks on the socket.
"""

import json
import socket
import threading
import time

GPSD_HOST = "127.0.0.1"
GPSD_PORT = 2947
WATCH = b'?WATCH={"enable":true,"json":true}\n'
RECONNECT_S = 2.0

MODE_NAMES = {0: "none", 1: "none", 2: "2d", 3: "3d"}
# TPV status: gpsd ≥ 3.20 reports 2 for a DGPS fix when it knows
STATUS_DGPS = 2


class GpsdListener:
    """Background gpsd watcher; .state() is always safe to call."""

    def __init__(self, host=GPSD_HOST, port=GPSD_PORT):
        self.host, self.port = host, port
        self._lock = threading.Lock()
        self._tpv = None
        self._tpv_t = 0.0
        self._sky = None
        self._sky_t = 0.0
        self.connected = False
        self.error = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2.0)

    # ------------------------------------------------------------- ingest
    def feed(self, report, now=None):
        """Take one parsed gpsd report dict (exposed for tests and for the
        snapshot daemon's build step)."""
        now = time.monotonic() if now is None else now
        cls = report.get("class")
        with self._lock:
            if cls == "TPV":
                self._tpv = report
                self._tpv_t = now
            elif cls == "SKY":
                # gpsd emits satellite-less SKY reports (DOP-only) between
                # full ones — don't let those blank the sky plot
                if "satellites" in report or self._sky is None:
                    self._sky = report
                    self._sky_t = now

    def state(self, now=None):
        """{tpv, tpv_age, sky, sky_age, connected, error} — ages in
        seconds, None report when nothing has arrived yet."""
        now = time.monotonic() if now is None else now
        with self._lock:
            return {
                "tpv": self._tpv,
                "tpv_age": (now - self._tpv_t) if self._tpv else None,
                "sky": self._sky,
                "sky_age": (now - self._sky_t) if self._sky else None,
                "connected": self.connected,
                "error": self.error,
            }

    # --------------------------------------------------------------- loop
    def _run(self):
        while not self._stop.is_set():
            try:
                s = socket.create_connection((self.host, self.port),
                                             timeout=2.0)
            except OSError as e:
                self.connected = False
                self.error = f"gpsd: {e.strerror or e}"
                self._stop.wait(RECONNECT_S)
                continue
            try:
                s.settimeout(1.0)
                s.sendall(WATCH)
                self.connected = True
                self.error = None
                buf = b""
                while not self._stop.is_set():
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise OSError("gpsd closed the connection")
                    buf += chunk
                    while b"\n" in buf:
                        line, _, buf = buf.partition(b"\n")
                        try:
                            self.feed(json.loads(line))
                        except ValueError:
                            continue
                    if len(buf) > 1 << 20:
                        buf = b""       # bounded: never grow on garbage
            except OSError as e:
                self.connected = False
                self.error = f"gpsd: {e}"
            finally:
                try:
                    s.close()
                except OSError:
                    pass
            self._stop.wait(RECONNECT_S)
