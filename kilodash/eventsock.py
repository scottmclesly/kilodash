"""Box-side event emitter + command sink for the web mirror.

Implements the kilodash half of `To-DoLists/WEB-PROTOCOL.md` v1 (DRAFT): a
Unix domain socket carrying newline-delimited JSON out (events) and in
(commands). The web backend is the client; this is the server.

Pocket-monster discipline — no broker, no Redis, no dependency beyond stdlib.

**The touchscreen always wins (§7).** Every design choice here exists so the
web path cannot degrade the panel:

  - the socket is non-blocking and the write is best-effort. EAGAIN / EPIPE
    means DROP THE FRAME and move on — never retry in the loop, never block,
    never buffer unboundedly;
  - at most one pending frame per type. A newer DataUpdated supersedes an
    unsent older one, and because `rev` was already assigned at change time
    the delivered stream carries a detectable gap rather than a silent
    overwrite (§4);
  - if nothing is listening this costs a bool check per tick;
  - accept() and inbound command reads live on a background thread, so a
    stalled or hostile client cannot stall the render loop.

`rev` is assigned at MODEL-CHANGE time, before emission, and a coalesced or
superseded frame still consumes its number. That is what makes a dropped
frame a permanent, detectable gap instead of invisible corruption. Assigning
it at send time is a conformance error (§4).
"""

import errno
import json
import os
import socket
import threading
import time

PROTOCOL_VERSION = 1

# §1: /run is a runtime dir with correct ownership; /tmp is world-writable and
# survives a boot in ways a runtime socket should not.
DEFAULT_SOCK = "/run/kilodash/events.sock"
SOCK_PATH = os.environ.get("KILODASH_EVENT_SOCK") or DEFAULT_SOCK

MAX_FRAME = 64 * 1024          # §2, hard ceiling on one NDJSON line
COALESCE_S = 0.100             # §7 floor, per screen. UNVERIFIED — the open
#                                ledger wants this benched against CAN at full
#                                bus rate; it is a reasoned number, not a
#                                measured one.
SNAPSHOT_MIN_INTERVAL_S = 0.250   # §7, inbound bound: at most one snapshot
#                                   build per subscriber per window
CMD_MAX = 4096                 # a command line longer than this is malice or
#                              a bug; either way it is not a §6 action


def _now():
    return time.time()


class EventEmitter:
    """One per app. Construct, `start()`, then call the note_* hooks from the
    UI thread and `pump()` once per loop iteration."""

    def __init__(self, app, path=SOCK_PATH, enabled=True):
        self.app = app
        self.path = path
        self.enabled = enabled
        self.sock = None                # listening socket
        self.conn = None                # the one subscriber (§1)
        self._wlock = threading.Lock()  # serialises writes; conn swapped under it
        self._cmd_lock = threading.Lock()
        self._commands = []             # inbound, drained on the UI thread
        self._stop = threading.Event()
        self._thread = None

        self._seq = 0                   # monotonic per connection, from 1
        self._rev = 0                   # per screen, reset on TileChanged
        self._tile = None
        self._last_model = None         # last model we told the client about
        self._pending = None            # (rev, merged_changed) awaiting flush
        self._last_emit = 0.0
        self._last_snapshot = 0.0
        self._alerts = {}               # id -> alert dict, box-scoped (§3)
        self.dropped = 0                # frames lost to a full/absent socket
        self.emitted = 0

    # ------------------------------------------------------------ lifecycle --
    def start(self):
        if not self.enabled:
            return self
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            if os.path.exists(self.path):
                os.unlink(self.path)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(self.path)
            os.chmod(self.path, 0o660)
            s.listen(1)                 # §1: exactly one subscriber
            s.settimeout(0.5)
            self.sock = s
        except Exception as e:          # noqa: BLE001 — the mirror is optional
            print(f"eventsock: disabled ({e})")
            self.enabled = False
            return self
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="eventsock")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        for s in (self.conn, self.sock):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        self.conn = self.sock = None
        try:
            if os.path.exists(self.path):
                os.unlink(self.path)
        except OSError:
            pass

    # --------------------------------------------------------- accept/read --
    def _serve(self):
        """Background: accept one subscriber, then read commands from it.
        Never touches screen state — it only queues bytes for the UI thread."""
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if self.conn is not None:
                # §1/§8: one subscriber. Tell the newcomer why, then close —
                # a silent drop would look like a crash and invite a retry storm.
                self._reject_busy(conn)
                continue
            conn.setblocking(False)
            with self._wlock:
                self.conn = conn
                self._seq = 0
            self._handshake()
            # Read commands on their OWN thread: this loop must stay in
            # accept(), or a second subscriber never gets its `busy` Error
            # (§1/§8) — it would just sit in the listen backlog looking like
            # a hung box, which is exactly the retry-storm invitation the
            # explicit rejection exists to prevent.
            threading.Thread(target=self._read_commands, args=(conn,),
                             daemon=True, name="eventsock-rx").start()

    def _reject_busy(self, conn):
        try:
            conn.sendall((json.dumps({
                "v": PROTOCOL_VERSION, "type": "Error", "seq": 1, "t": _now(),
                "code": "busy", "detail": "subscriber already connected",
            }) + "\n").encode())
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _read_commands(self, conn):
        """Inbound §6 actions, NDJSON, same socket reversed. Parsed here but
        APPLIED on the UI thread — a background thread must never call into a
        screen (that is the microkvm pending-tile-switch rule)."""
        buf = b""
        while not self._stop.is_set() and self.conn is conn:
            try:
                chunk = conn.recv(4096)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            except OSError:
                break
            if not chunk:
                break                       # clean disconnect
            buf += chunk
            if len(buf) > CMD_MAX:
                buf = b""                   # never grow unboundedly
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._queue_command(line)
        self._drop_conn(conn)

    def _queue_command(self, line):
        try:
            cmd = json.loads(line.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(cmd, dict) and isinstance(cmd.get("action"), str):
            with self._cmd_lock:
                if len(self._commands) < 32:     # bounded; excess is dropped
                    self._commands.append(cmd)

    def _drop_conn(self, conn):
        with self._wlock:
            if self.conn is conn:
                self.conn = None
                self._last_model = None
                self._pending = None
        try:
            conn.close()
        except OSError:
            pass

    # ------------------------------------------------------------- sending --
    def _send(self, obj):
        """Best-effort, non-blocking, drop-on-anything. Returns True if the
        bytes reached the kernel buffer."""
        conn = self.conn
        if conn is None:
            return False
        self._seq += 1
        obj["v"] = PROTOCOL_VERSION
        obj["seq"] = self._seq
        obj.setdefault("t", _now())
        try:
            line = (json.dumps(obj, separators=(",", ":"),
                               default=str) + "\n").encode()
        except (TypeError, ValueError):
            self.dropped += 1
            return False
        if len(line) > MAX_FRAME:
            # §2/§4: a producer never emits oversized — the row caps truncate
            # first. Reaching here is a bug upstream, so drop rather than
            # ship a frame the consumer is required to disconnect over.
            self.dropped += 1
            return False
        with self._wlock:
            if self.conn is not conn:
                return False
            try:
                conn.sendall(line)
            except (BlockingIOError, InterruptedError):
                self.dropped += 1        # buffer full: the subscriber is slow.
                return False             # Drop and move on — never back-pressure
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    self.dropped += 1
                    return False
                threading.Thread(target=self._drop_conn, args=(conn,),
                                 daemon=True).start()
                return False
        self.emitted += 1
        return True

    # ------------------------------------------------------------- handshake --
    def _handshake(self):
        """§5: Hello then ScreenSnapshot, unprompted, on every connection."""
        self._send(self._hello())
        self.send_snapshot(force=True)

    def _hello(self):
        th = self.app.theme
        keys = ("bg", "card", "card_hi", "fg", "muted", "accent",
                "ok", "warn", "bad")
        return {
            "type": "Hello",
            "device": "scottina-prime",
            "kilodash_version": getattr(self.app, "version", "0"),
            "protocol": PROTOCOL_VERSION,
            # §3: the palette is normative for the web, so both surfaces read
            # as one instrument and red stays reserved for actual faults.
            "theme": dict({"name": th.name},
                          **{k: list(getattr(th, k)) for k in keys}),
        }

    def _tiles(self):
        """The launcher inventory carried in ScreenSnapshot.

        `available` MUST fold in hotplug presence, exactly as the home model
        does. Reporting only `s.available()` made the snapshot claim
        available:true for screens whose device is absent — so the web offered
        a tile that, when tapped, bounced straight back to home via the
        hotplug guard, and the two tile lists in the same frame disagreed
        with each other."""
        out = []
        devices = getattr(self.app, "devices", None)
        for s in self.app.screens:
            if not s.tile_id:
                continue
            present = (s.device_key is None or
                       (devices is not None and devices.has(s.device_key)))
            out.append({"id": s.tile_id, "title": s.title,
                        "glyph": s.glyph,
                        "available": bool(present and s.available())})
        return out

    # ------------------------------------------------------- emit interface --
    def send_snapshot(self, force=False):
        """§5 full resync. Rate-limited (§7) so the command channel cannot
        defeat best-effort emission by making the box rebuild constantly."""
        if self.conn is None:
            return False
        now = _now()
        if not force and now - self._last_snapshot < SNAPSHOT_MIN_INTERVAL_S:
            return False                 # folds into the next build
        self._last_snapshot = now
        scr = self.app.current
        model = self._model_of(scr)
        self._tile = scr.tile_id
        self._last_model = model
        self._pending = None
        return self._send({
            "type": "ScreenSnapshot",
            "tile": scr.tile_id,
            "nav": self._nav(scr),
            "rev": self._rev,
            "tiles": self._tiles(),
            "model": model,
            "alerts": list(self._alerts.values()),
        })

    def note_tile(self, screen):
        """Active screen changed — from either surface. Carries the full model
        and resets rev to 0 (§3), so the web never asks for a snapshot after
        a nav."""
        if self.conn is None:
            self._tile = screen.tile_id
            return False
        model = self._model_of(screen)
        self._rev = 0
        self._tile = screen.tile_id
        self._last_model = model
        self._pending = None
        return self._send({
            "type": "TileChanged",
            "tile": screen.tile_id,
            "nav": self._nav(screen),
            "rev": 0,
            "model": model,
        })

    def note_model(self, screen):
        """The active screen's model may have changed. Diffs against what the
        client was last told and emits only changed top-level keys.

        Called after the frame is blitted, and only when the screen already
        reported a change — `tick()` returning True is NOT a change signal
        (several screens return True for animation frames only)."""
        if self.conn is None or screen.tile_id != self._tile:
            return False
        model = self._model_of(screen)
        base = self._last_model or {}
        changed = {k: v for k, v in model.items() if base.get(k) != v}
        if not changed:
            return False
        self._last_model = model
        # rev is assigned HERE — at change time, before emission. A frame
        # coalesced away below still burns this number, so its absence is a
        # permanent gap the consumer can detect (§4).
        self._rev += 1
        if self._pending:
            prev_changed = self._pending[1]
            prev_changed.update(changed)     # shallow merge, newest wins
            self._pending = (self._rev, prev_changed)
        else:
            self._pending = (self._rev, dict(changed))
        return self._flush()

    def note_alert(self, alert, fired=True):
        """§3 AlertFired / AlertCleared. Alerts are box-scoped, not screen-
        scoped: they survive a tile change and ride in ScreenSnapshot."""
        aid = alert.get("id") if isinstance(alert, dict) else None
        if not aid:
            return False
        if fired:
            self._alerts[aid] = alert
            return self._send({"type": "AlertFired", "alert": alert})
        self._alerts.pop(aid, None)
        return self._send({"type": "AlertCleared", "alert": {"id": aid}})

    def send_error(self, code, detail=""):
        """§8. The box's rejection channel — distinct from the backend's HTTP
        4xx, because a command can pass the backend's envelope check and still
        be refused here (the active screen changed between POST and dispatch:
        last-input-wins in action)."""
        return self._send({"type": "Error", "code": code, "detail": detail})

    def note_theme(self):
        """Theme changed: re-emit Hello mid-stream with the CURRENT seq. The
        consumer re-themes live and must NOT resync or discard its model."""
        if self.conn is None:
            return False
        return self._send(self._hello())

    def pump(self):
        """Call once per UI loop iteration: flushes a coalesced frame whose
        window has expired. Without this a pending delta could sit unsent
        until the next change, which on an idle screen is forever."""
        if self._pending and self.conn is not None:
            self._flush()

    # ----------------------------------------------------------- internals --
    def _flush(self):
        if not self._pending:
            return False
        if _now() - self._last_emit < COALESCE_S:
            return False                # still inside the window; supersede
        rev, changed = self._pending
        self._pending = None
        self._last_emit = _now()
        return self._send({
            "type": "DataUpdated",
            "tile": self._tile,
            "rev": rev,
            "changed": changed,
        })

    def _nav(self, screen):
        """§3: kilodash is a two-level star with a back hit-box, not a stack.
        Never emit a third element."""
        if self.app.is_launcher(screen):
            return ["home"]
        return ["home", screen.tile_id]

    def _model_of(self, screen):
        """A screen's model() must never be able to take the panel down."""
        try:
            m = screen.model()
            if isinstance(m, dict) and m.get("kind"):
                return m
        except Exception as e:           # noqa: BLE001
            return {"kind": "generic", "title": screen.title, "rows": [],
                    "buttons": [],
                    "note": f"model() raised: {type(e).__name__}"}
        return {"kind": "generic", "title": screen.title, "rows": [],
                "buttons": [], "note": "model() returned no kind"}

    # ------------------------------------------------------- command sink --
    def take_commands(self):
        """UI thread: drain queued §6 actions. Returns a list (possibly
        empty). The caller applies them through the SAME path as a touch
        event — the device cannot tell a web tap from a panel tap."""
        with self._cmd_lock:
            cmds, self._commands = self._commands, []
        return cmds
