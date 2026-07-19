#!/usr/bin/env python3
"""mirror-audit — walk EVERY tile and report what the web mirror actually shows.

The check that should have existed before Phase 3 was called done. Verifying
one screen and generalising is how 19 empty panels shipped: `model_rows()`
defaults to `[]`, so any screen that never overrode it renders as a correctly
framed instrument with nothing in it.

    sudo tools/mirror-audit.py                 # audit every tile
    sudo tools/mirror-audit.py --host 1.2.3.4

Drives the real command surface against the real box and reads the real event
stream — no fakes. For each tile it reports the model kind and how much the
renderer will actually have to draw, then flags the empty ones.
"""

import argparse
import json
import socket
import sys
import threading
import time
import urllib.request

DEFAULT_HOST = "127.0.0.1"


def post(host, body, timeout=4):
    req = urllib.request.Request(
        f"http://{host}/api/input", method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception as e:                       # noqa: BLE001
        return getattr(e, "code", 0)


class Stream(threading.Thread):
    """Reads the SSE stream in the background and keeps the newest frame of
    each type, so the main thread can tap a tile and then look at what came
    back."""

    def __init__(self, host):
        super().__init__(daemon=True)
        self.host = host
        self.latest = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.ready = threading.Event()

    def run(self):
        try:
            r = urllib.request.urlopen(f"http://{self.host}/api/stream",
                                       timeout=90)
        except Exception as e:                   # noqa: BLE001
            print(f"cannot open stream: {e}", file=sys.stderr)
            return
        for raw in r:
            if self.stop.is_set():
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            try:
                f = json.loads(line[6:])
            except ValueError:
                continue
            with self.lock:
                self.latest[f.get("type")] = f
            self.ready.set()

    def take(self, kind):
        with self.lock:
            return self.latest.pop(kind, None)

    def wait_for(self, kind, secs=6.0):
        end = time.time() + secs
        while time.time() < end:
            f = self.take(kind)
            if f:
                return f
            time.sleep(0.05)
        return None


def weight(model):
    """How much the renderer actually has to draw. This is the number that
    matters: a model can be present, well-formed, and still leave the screen
    blank."""
    k = model.get("kind")
    if k == "home":
        return len(model.get("tiles") or [])
    if k == "canbus":
        return len(model.get("rows") or [])
    if k == "n2k":
        return len(model.get("fields") or [])
    if k == "lightdock":
        return len(model.get("log") or []) + (1 if model.get("device") else 0)
    return len(model.get("rows") or []) + len(model.get("buttons") or [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--dwell", type=float, default=1.2,
                    help="seconds to sit on each tile")
    args = ap.parse_args()

    st = Stream(args.host)
    st.start()
    if not st.ready.wait(10):
        print("no frames from the stream — is the mirror up?", file=sys.stderr)
        return 2
    time.sleep(1.0)

    snap = st.latest.get("ScreenSnapshot")
    if not snap:
        post(args.host, {"action": "request_snapshot"})
        snap = st.wait_for("ScreenSnapshot", 6)
    if not snap:
        print("no snapshot", file=sys.stderr)
        return 2

    inventory = snap.get("tiles") or []
    print(f"  {len(inventory)} tiles in the launcher inventory\n")
    print(f"  {'TILE':14s} {'AVAIL':6s} {'KIND':10s} {'DRAWS':>6s}  NOTE")
    print(f"  {'-'*14} {'-'*6} {'-'*10} {'-'*6}  {'-'*34}")

    empty, unreachable, ok = [], [], []
    post(args.host, {"action": "home"})
    time.sleep(args.dwell)
    st.take("TileChanged")

    for t in inventory:
        tid = t["id"]
        if tid == "home":
            continue
        code = post(args.host, {"action": "tap_tile", "tile": tid})
        tc = st.wait_for("TileChanged", 5)
        time.sleep(args.dwell)
        # a later TileChanged (e.g. the hotplug guard bouncing us home) wins
        bounce = st.take("TileChanged")
        landed = (bounce or tc)
        if not landed:
            print(f"  {tid:14s} {'?':6s} {'-':10s} {'-':>6s}  no TileChanged (HTTP {code})")
            unreachable.append(tid)
            continue
        if landed.get("tile") != tid:
            print(f"  {tid:14s} {str(t.get('available')):6s} {'-':10s} "
                  f"{'-':>6s}  bounced to {landed.get('tile')} (device absent)")
            unreachable.append(tid)
            continue
        model = landed.get("model") or {}
        w = weight(model)
        note = ""
        if w == 0:
            note = "EMPTY — renders a blank panel"
            empty.append(tid)
        else:
            ok.append(tid)
        print(f"  {tid:14s} {str(t.get('available')):6s} "
              f"{str(model.get('kind')):10s} {w:>6d}  {note}")
        post(args.host, {"action": "home"})
        time.sleep(0.4)
        st.take("TileChanged")

    st.stop.set()
    print()
    print(f"  drawable : {len(ok)}")
    print(f"  EMPTY    : {len(empty)}  {empty}")
    print(f"  skipped  : {len(unreachable)}  (device absent / unreachable)")
    return 1 if empty else 0


if __name__ == "__main__":
    sys.exit(main())
