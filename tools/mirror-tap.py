#!/usr/bin/env python3
"""mirror-tap — eyeball the web-mirror event stream from a live box.

The throwaway subscriber from WebUI-Mirror-TODO's first slice: connect to
kilodash's event socket, print real frames, and CHECK THE INVARIANTS that
WEB-PROTOCOL.md says must hold. It is a bench instrument, not a product —
the real consumer is the Python backend (Phase 2).

    sudo tools/mirror-tap.py                 # follow the stream
    sudo tools/mirror-tap.py --raw           # dump raw JSON lines
    sudo tools/mirror-tap.py --stats 10      # 10 s rate/gap report, then exit
    sudo tools/mirror-tap.py --cmd home      # send one §6 command and follow
    sudo tools/mirror-tap.py --cmd tap_tile:light-dock

What it verifies while it runs (§2/§4/§5):
  * `seq` is monotonic with no gaps — a gap means frames were lost in
    transit, which on a loopback Unix socket should never happen;
  * `rev` increments by exactly 1 per DataUpdated and resets to 0 on
    TileChanged. A `rev` GAP IS EXPECTED AND CORRECT under coalescing — it
    is the signal a real client uses to resync — so gaps are counted and
    reported, not treated as errors;
  * a delta never arrives before a snapshot;
  * frames stay under the 64 KiB ceiling.

The inter-frame timing histogram is the point of the exercise: it is what
gives the §7 coalescing floor a measured number instead of a guessed one.
"""

import argparse
import json
import os
import socket
import sys
import time

SOCK = os.environ.get("KILODASH_EVENT_SOCK") or "/run/kilodash/events.sock"
MAX_FRAME = 64 * 1024


def _c(code, s):
    return s if not sys.stdout.isatty() else f"\033[{code}m{s}\033[0m"


DIM, BOLD, RED, GRN, YEL, CYN = "2", "1", "31", "32", "33", "36"


class Tap:
    def __init__(self, path=SOCK):
        self.path = path
        self.seq = None
        self.rev = None
        self.tile = None
        self.have_snapshot = False
        self.n = 0
        self.by_type = {}
        self.seq_gaps = 0
        self.rev_gaps = 0
        self.deltas_before_snapshot = 0
        self.oversized = 0
        self.bytes = 0
        self.gaps_detail = []
        self.intervals = []          # seconds between consecutive DataUpdated
        self._last_data_t = None

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.path)
        # A timeout, not blocking: an idle stream is the NORMAL case (the
        # emitter only speaks on change), and without this the --stats
        # deadline is never reached because the loop sits in recv() forever.
        s.settimeout(0.5)
        self.sock = s
        return s

    def send_command(self, action, **kw):
        body = dict(action=action, **kw)
        self.sock.sendall((json.dumps(body) + "\n").encode())
        return body

    def frames(self, deadline=None):
        buf = b""
        while True:
            if deadline and time.time() > deadline:
                return
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue            # idle is normal — emit is on change only
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                self.bytes += len(line)
                if len(line) > MAX_FRAME:
                    self.oversized += 1
                try:
                    yield json.loads(line), line
                except ValueError as e:
                    print(_c(RED, f"  !! unparseable frame: {e}"))

    def check(self, f):
        """Invariant checks. Returns a list of note strings."""
        notes = []
        self.n += 1
        t = f.get("type", "?")
        self.by_type[t] = self.by_type.get(t, 0) + 1

        seq = f.get("seq")
        if self.seq is not None and seq != self.seq + 1:
            self.seq_gaps += 1
            notes.append(_c(RED, f"SEQ GAP {self.seq} -> {seq}"))
        self.seq = seq

        if t == "ScreenSnapshot":
            self.have_snapshot = True
            self.tile, self.rev = f.get("tile"), f.get("rev")
        elif t == "TileChanged":
            self.tile, self.rev = f.get("tile"), f.get("rev")
            if f.get("rev") != 0:
                notes.append(_c(RED, "TileChanged rev != 0"))
            if "model" not in f:
                notes.append(_c(RED, "TileChanged without full model"))
            if len(f.get("nav") or []) > 2:
                notes.append(_c(RED, "nav deeper than 2"))
        elif t == "DataUpdated":
            if not self.have_snapshot:
                self.deltas_before_snapshot += 1
                notes.append(_c(RED, "DELTA BEFORE SNAPSHOT"))
            rev = f.get("rev")
            if self.rev is not None and rev != self.rev + 1:
                self.rev_gaps += 1
                # EXPECTED under §7 coalescing — this is the resync signal.
                notes.append(_c(YEL, f"rev gap {self.rev} -> {rev} "
                                     f"(coalesced; a real client resyncs)"))
                self.gaps_detail.append((self.rev, rev))
            self.rev = rev
            now = time.time()
            if self._last_data_t is not None:
                self.intervals.append(now - self._last_data_t)
            self._last_data_t = now
        return notes

    def describe(self, f):
        t = f.get("type")
        if t == "Hello":
            th = f.get("theme") or {}
            return (f"device={f.get('device')} v{f.get('kilodash_version')} "
                    f"proto={f.get('protocol')} theme={th.get('name')}")
        if t == "ScreenSnapshot":
            m = f.get("model") or {}
            return (f"tile={f.get('tile')} rev={f.get('rev')} "
                    f"kind={m.get('kind')} tiles={len(f.get('tiles') or [])} "
                    f"alerts={len(f.get('alerts') or [])}")
        if t == "TileChanged":
            m = f.get("model") or {}
            return (f"tile={f.get('tile')} nav={f.get('nav')} "
                    f"kind={m.get('kind')}")
        if t == "DataUpdated":
            ch = f.get("changed") or {}
            return (f"tile={f.get('tile')} rev={f.get('rev')} "
                    f"changed={sorted(ch)}")
        if t in ("AlertFired", "AlertCleared"):
            a = f.get("alert") or {}
            return f"{a.get('id')} {a.get('severity') or ''} {a.get('label') or ''}"
        if t == "Error":
            return f"code={f.get('code')} {f.get('detail')}"
        return json.dumps(f)[:120]

    def report(self):
        print()
        print(_c(BOLD, "── mirror-tap summary ─────────────────────────────"))
        print(f"  frames        {self.n}  ({self.bytes/1024:.1f} KiB)")
        for t, n in sorted(self.by_type.items(), key=lambda kv: -kv[1]):
            print(f"    {t:16s} {n}")
        print(f"  seq gaps      {self.seq_gaps} "
              f"{_c(RED,'(BUG — loopback must not lose frames)') if self.seq_gaps else _c(GRN,'(clean)')}")
        print(f"  rev gaps      {self.rev_gaps} "
              f"{_c(DIM,'(expected: coalescing)') if self.rev_gaps else ''}")
        if self.gaps_detail[:6]:
            print(f"    e.g. {self.gaps_detail[:6]}")
        print(f"  delta-before-snapshot  {self.deltas_before_snapshot} "
              f"{_c(RED,'(SPEC VIOLATION)') if self.deltas_before_snapshot else _c(GRN,'(clean)')}")
        print(f"  oversized     {self.oversized} "
              f"{_c(RED,'(SPEC VIOLATION)') if self.oversized else _c(GRN,'(clean)')}")
        iv = sorted(self.intervals)
        if iv:
            def pct(p):
                return iv[min(len(iv) - 1, int(len(iv) * p))] * 1000
            print()
            print(_c(BOLD, "  DataUpdated inter-frame interval (ms) "
                           "— this is the §7 coalescing evidence"))
            print(f"    n={len(iv)}  min={iv[0]*1000:.0f}  "
                  f"p50={pct(.5):.0f}  p90={pct(.9):.0f}  "
                  f"p99={pct(.99):.0f}  max={iv[-1]*1000:.0f}")
            floor = sum(1 for x in iv if x < 0.095)
            if floor:
                print(_c(RED, f"    {floor} interval(s) under the 100 ms "
                              f"floor — the coalescer is not holding"))
            else:
                print(_c(GRN, "    no interval under the 100 ms floor — "
                              "coalescer holding"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default=SOCK)
    ap.add_argument("--raw", action="store_true", help="dump raw JSON lines")
    ap.add_argument("--stats", type=float, metavar="SECS",
                    help="run for SECS then print the report and exit")
    ap.add_argument("--cmd", metavar="ACTION[:ARG]",
                    help="send a §6 command after connecting, e.g. "
                         "home, back, request_snapshot, tap_tile:light-dock")
    args = ap.parse_args()

    tap = Tap(args.sock)
    try:
        tap.connect()
    except (FileNotFoundError, ConnectionRefusedError, PermissionError) as e:
        print(f"cannot connect to {args.sock}: {e}", file=sys.stderr)
        print("  is kilodash running? try sudo, or set KILODASH_EVENT_SOCK",
              file=sys.stderr)
        return 2
    print(_c(DIM, f"connected to {args.sock}"))

    if args.cmd:
        action, _, arg = args.cmd.partition(":")
        kw = {"tile": arg} if action == "tap_tile" and arg else (
             {"button": arg} if action == "button_press" and arg else {})
        print(_c(CYN, f"→ command {tap.send_command(action, **kw)}"))

    deadline = time.time() + args.stats if args.stats else None
    t0 = time.time()
    try:
        for f, raw in tap.frames(deadline):
            notes = tap.check(f)
            if args.raw:
                print(raw.decode("utf-8", "replace"))
            else:
                t = f.get("type", "?")
                colour = {"Error": RED, "AlertFired": YEL,
                          "TileChanged": CYN, "Hello": GRN,
                          "ScreenSnapshot": GRN}.get(t, DIM)
                print(f"{time.time()-t0:7.2f}s {_c(colour, f'{t:16s}')} "
                      f"{_c(DIM, tap.describe(f))}")
            for n in notes:
                print(f"         {n}")
            if deadline and time.time() > deadline:
                break
    except KeyboardInterrupt:
        pass
    finally:
        tap.report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
