"""Web mirror backend — the on-device service between kilodash and a browser.

Implements the backend half of `To-DoLists/WEB-PROTOCOL.md` v1 (DRAFT):

    box ──unix socket──► backend ──SSE──► browser
                            ▲               │
                            └─POST /api/input┘

Its whole job is fan-out and relay. It holds **no authority**: the box is the
single source of truth, and this process never invents state, never answers a
command itself, and never tells a browser something the box did not say.

Three responsibilities, and nothing else:
  1. subscribe to the box's event socket, maintain the current screen model by
     applying §4 deltas exactly as a client would, and re-request a snapshot on
     any `rev`/`seq` gap (§5);
  2. fan out to browsers over SSE, sending each new stream a synthesised
     `ScreenSnapshot` FIRST so a client that joins mid-stream is never
     rendering a partial model (§5 — mandatory);
  3. validate the §6 command ENVELOPE and forward to the box. Semantic
     validation is the box's job, deliberately: this process does not know
     what a screen means and must not guess.

Stack matches the house pattern (`tableconv.py`): Flask from apt, stdlib for
everything else, no pip, no broker.

LAN-only posture (§10). No auth in v1, which is acceptable *only* because it
never leaves the local network. WAN exposure is a different project.
"""

import argparse
import json
import os
import queue
import socket
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from . import net
from .eventsock import SOCK_PATH, PROTOCOL_VERSION

VERSION = "1.0"
PORT = int(os.environ.get("KILODASH_WEB_PORT", "80"))

# §6, the closed allow-list. An action not here is 400, never forwarded — this
# is the mechanism that makes the no-new-TX-surface constraint enforced rather
# than merely intended, so it is a positive list and stays that way.
ACTIONS = {
    "tap_tile":         {"tile": str},
    "button_press":     {"button": str},
    "back":             {},
    "home":             {},
    "request_snapshot": {},
}

RECONNECT_MIN_S = 0.5           # box socket retry floor
RECONNECT_MAX_S = 5.0           # …and ceiling: the box may simply be down
SSE_QUEUE_MAX = 64              # per-browser backlog; a stalled tab is dropped
#                                 rather than allowed to grow without bound
SSE_KEEPALIVE_S = 15.0          # comment frame, so idle proxies don't reap us

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "webui")


class BoxLink:
    """The single subscriber to kilodash's event socket (§1).

    Keeps the authoritative mirror of what the box last said, and hands
    frames to browser streams. Reconnects forever with backoff — the box
    restarting must not require restarting this service."""

    def __init__(self, path=SOCK_PATH):
        self.path = path
        self.sock = None
        self.connected = False
        self._lock = threading.Lock()
        self._subs = []                 # list[queue.Queue] — one per browser
        self._stop = threading.Event()

        # Everything the box has told us. A new browser is served from here.
        self.hello = None
        self.snapshot = None            # last full ScreenSnapshot frame
        self.model = None               # current model, deltas applied
        self.tile = None
        self.nav = None
        self.tiles = []
        self.alerts = {}
        self.rev = None
        self.seq = None

        self.stats = {"frames": 0, "resyncs": 0, "gaps": 0,
                      "box_reconnects": 0, "dropped_subs": 0}

    # ---------------------------------------------------------- box socket --
    def start(self):
        threading.Thread(target=self._run, daemon=True,
                         name="webmirror-box").start()
        return self

    def _run(self):
        delay = RECONNECT_MIN_S
        was_connected = False
        while not self._stop.is_set():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.path)
                s.settimeout(1.0)
                # A NEW box connection restarts seq at 1 (§2), so the old
                # counter must be forgotten. Carrying it over makes the very
                # first frame after every box restart look like a gap and
                # fires a spurious resync — one per restart, forever.
                self.seq = None
                self.rev = None
                self.sock, self.connected = s, True
                was_connected = True
                delay = RECONNECT_MIN_S
                self.stats["box_reconnects"] += 1
                self._read_forever(s)
            except (FileNotFoundError, ConnectionRefusedError,
                    PermissionError, OSError):
                pass
            finally:
                self._on_box_gone()
            # A dropped box link is a state discontinuity for every browser:
            # tell them, so the UI can go to its stale presentation instead of
            # showing frozen numbers as though they were live (§3).
            #
            # EDGE-TRIGGERED. Announcing it once per retry would flood every
            # browser for the whole outage — and "the link is still down" is
            # not news, it is the state they were already told about.
            if was_connected:
                was_connected = False
                self._broadcast({"v": PROTOCOL_VERSION, "type": "Error",
                                 "code": "resync", "detail": "box link lost",
                                 "seq": 0, "t": time.time()})
            self._stop.wait(delay)
            delay = min(RECONNECT_MAX_S, delay * 2)

    def _on_box_gone(self):
        self.connected = False
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        # Do NOT clear the cached model: a browser connecting during an outage
        # should see the last known state, marked stale by the Error above,
        # rather than an empty screen. It is resynced on reconnect anyway.

    def _read_forever(self, s):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            if len(buf) > 1024 * 1024:      # never grow unboundedly
                buf = b""
                self._request_snapshot()
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._on_frame(line)

    def _on_frame(self, line):
        try:
            f = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return
        if f.get("v") != PROTOCOL_VERSION:
            # §9: loud and fatal, never a guess. Refuse the frame rather than
            # partially parsing a shape we do not know.
            print(f"webmirror: protocol mismatch v={f.get('v')} "
                  f"(expected {PROTOCOL_VERSION}) — frame refused")
            return
        self.stats["frames"] += 1
        t = f.get("type")

        seq = f.get("seq")
        if (self.seq is not None and isinstance(seq, int) and seq
                and seq != self.seq + 1 and t != "Error"):
            self.stats["gaps"] += 1
            self._request_snapshot()
        if isinstance(seq, int) and seq:
            self.seq = seq

        with self._lock:
            if t == "Hello":
                self.hello = f
            elif t == "ScreenSnapshot":
                self.snapshot = f
                self.model = dict(f.get("model") or {})
                self.tile, self.nav = f.get("tile"), f.get("nav")
                self.tiles = f.get("tiles") or self.tiles
                self.rev = f.get("rev")
                self.alerts = {a["id"]: a for a in (f.get("alerts") or [])
                               if isinstance(a, dict) and a.get("id")}
            elif t == "TileChanged":
                self.model = dict(f.get("model") or {})
                self.tile, self.nav = f.get("tile"), f.get("nav")
                self.rev = f.get("rev", 0)
            elif t == "DataUpdated":
                gap = (self.rev is not None
                       and f.get("rev") != self.rev + 1)
                self.rev = f.get("rev")
                if self.model is None or gap:
                    # §4: a rev gap means an intermediate state was merged or
                    # lost. Never patch onto the wrong base — resync instead.
                    self.stats["gaps"] += 1
                    self._request_snapshot()
                else:
                    # Shallow merge at the top level, arrays whole (§4).
                    self.model.update(f.get("changed") or {})
            elif t == "AlertFired":
                a = f.get("alert") or {}
                if a.get("id"):
                    self.alerts[a["id"]] = a
            elif t == "AlertCleared":
                a = f.get("alert") or {}
                self.alerts.pop(a.get("id"), None)
        self._broadcast(f)

    def _request_snapshot(self):
        self.stats["resyncs"] += 1
        self.send_command({"action": "request_snapshot"})

    def send_command(self, cmd):
        """Forward a validated §6 action to the box. Returns False if there is
        no box to forward to — the caller turns that into a 503."""
        s = self.sock
        if s is None or not self.connected:
            return False
        try:
            s.sendall((json.dumps(cmd) + "\n").encode())
            return True
        except OSError:
            self._on_box_gone()
            return False

    # ------------------------------------------------------------- fan-out --
    def subscribe(self):
        q = queue.Queue(maxsize=SSE_QUEUE_MAX)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def _broadcast(self, frame):
        dead = []
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                # A browser that cannot keep up is dropped, not buffered. It
                # reconnects (EventSource does that itself) and resyncs from a
                # fresh snapshot — which is cheaper and more correct than
                # replaying a backlog it would only discard.
                dead.append(q)
        for q in dead:
            self.stats["dropped_subs"] += 1
            self.unsubscribe(q)

    def synth_snapshot(self):
        """A ScreenSnapshot built from current state, for a browser joining
        mid-stream. §5 makes this mandatory: without it a fresh client renders
        a partial model, or nothing at all."""
        with self._lock:
            if self.model is None:
                return None
            return {
                "v": PROTOCOL_VERSION,
                "type": "ScreenSnapshot",
                "seq": self.seq or 0,
                "t": time.time(),
                "tile": self.tile,
                "nav": self.nav or (["home"] if self.tile == "home"
                                    else ["home", self.tile]),
                "rev": self.rev if self.rev is not None else 0,
                "tiles": list(self.tiles),
                "model": dict(self.model),
                "alerts": list(self.alerts.values()),
            }


# ------------------------------------------------------------------- app -----
def create_app(link):
    app = Flask(__name__, static_folder=None)

    def sse(payload, event=None):
        out = ""
        if event:
            out += f"event: {event}\n"
        return out + "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"

    @app.route("/api/stream")
    def stream():
        """The live channel (§1, SSE). Every new stream gets Hello then a
        synthesised ScreenSnapshot before any delta — a client must never be
        asked to patch a model it does not have."""
        q = link.subscribe()

        def gen():
            try:
                if link.hello:
                    yield sse(link.hello)
                snap = link.synth_snapshot()
                if snap:
                    yield sse(snap)
                elif not link.connected:
                    yield sse({"v": PROTOCOL_VERSION, "type": "Error",
                               "code": "resync", "seq": 0, "t": time.time(),
                               "detail": "box link down"})
                last = time.time()
                while True:
                    try:
                        frame = q.get(timeout=1.0)
                        yield sse(frame)
                    except queue.Empty:
                        if time.time() - last > SSE_KEEPALIVE_S:
                            last = time.time()
                            yield ": keepalive\n\n"
            finally:
                link.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        })

    @app.route("/api/input", methods=["POST"])
    def api_input():
        """§6 command surface. ENVELOPE validation only — `action` is in the
        closed allow-list and its fields are the right JSON category. Semantic
        validation belongs to the box, which owns screen meaning; guessing it
        here would put two authorities in the system."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="body must be a JSON object"), 400
        action = body.get("action")
        if action not in ACTIONS:
            return jsonify(error=f"unknown action {action!r}"), 400
        for field, typ in ACTIONS[action].items():
            if field not in body:
                return jsonify(error=f"{action} requires {field!r}"), 400
            if not isinstance(body[field], typ):
                return jsonify(error=f"{field!r} must be "
                                     f"{typ.__name__}"), 400
        cmd = {"action": action}
        cmd.update({k: body[k] for k in ACTIONS[action]})
        if not link.send_command(cmd):
            return jsonify(error="box link down"), 503
        # 202, not 200: accepted, NOT applied. The result arrives as a normal
        # event. A client must never optimistically apply its own command —
        # there is exactly one path by which the mirror changes, and it is the
        # event stream.
        return jsonify(accepted=cmd), 202

    @app.route("/api/state")
    def api_state():
        """Debug/health. Not part of the protocol — do not build a client on
        it; it exists so `curl` can answer "is the box talking?"."""
        snap = link.synth_snapshot()
        return jsonify(
            version=VERSION,
            protocol=PROTOCOL_VERSION,
            box_connected=link.connected,
            tile=link.tile,
            rev=link.rev,
            seq=link.seq,
            subscribers=len(link._subs),
            stats=link.stats,
            has_snapshot=snap is not None,
        )

    @app.route("/")
    @app.route("/<path:path>")
    def ui(path="index.html"):
        """Serves the React bundle (Phase 3). Until it exists, a holding page
        that is itself useful: it proves the stream works."""
        full = os.path.join(STATIC_DIR, path)
        if os.path.isfile(full):
            return send_from_directory(STATIC_DIR, path)
        if os.path.isfile(os.path.join(STATIC_DIR, "index.html")):
            return send_from_directory(STATIC_DIR, "index.html")
        return Response(_PLACEHOLDER, mimetype="text/html")

    return app


_PLACEHOLDER = """<!doctype html>
<meta charset="utf-8"><title>Scottina — Web Mirror</title>
<style>
 body{background:#000903;color:#33f546;font:14px/1.5 monospace;margin:0;padding:18px}
 h1{font-size:15px;letter-spacing:.3em;border-bottom:1px solid #083;padding-bottom:8px}
 #s{color:#096628} .t{color:#82ff78} pre{white-space:pre-wrap;word-break:break-all}
</style>
<h1>S C O T T I N A &nbsp;·&nbsp; W E B &nbsp; M I R R O R</h1>
<p id="s">connecting…</p>
<p class="t">tile: <b id="tile">—</b> &nbsp; rev: <b id="rev">—</b></p>
<pre id="log"></pre>
<script>
// Phase-3 placeholder. Deliberately not a UI — it exists to prove the SSE
// stream, the snapshot-first rule and the command path end to end.
const log=document.getElementById('log'), s=document.getElementById('s');
let n=0;
const es=new EventSource('/api/stream');
es.onopen=()=>s.textContent='stream open';
es.onerror=()=>s.textContent='stream down — EventSource will retry';
es.onmessage=(e)=>{
  const f=JSON.parse(e.data);
  if(f.type==='ScreenSnapshot'||f.type==='TileChanged'){
    document.getElementById('tile').textContent=f.tile;
  }
  if(f.rev!==undefined) document.getElementById('rev').textContent=f.rev;
  log.textContent=(`${String(++n).padStart(4)} ${f.type} `+
    (f.changed?`changed=${Object.keys(f.changed)}`:'')+
    (f.model?`kind=${f.model.kind}`:'')+'\\n')+log.textContent.slice(0,4000);
};
</script>
"""


def main():
    ap = argparse.ArgumentParser(description="Scottina web mirror backend")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--sock", default=SOCK_PATH)
    args = ap.parse_args()

    link = BoxLink(args.sock).start()
    app = create_app(link)
    addr = net.advertise_addr()
    print(f"webmirror {VERSION} — http://{addr}:{args.port}/  "
          f"(box socket {args.sock})")
    # threaded: each SSE stream holds a worker for its lifetime, so the
    # single-threaded server would serve exactly one browser.
    app.run(host="0.0.0.0", port=args.port, threaded=True,
            debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
