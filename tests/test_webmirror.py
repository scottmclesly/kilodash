"""Unit tests for the web mirror backend (kilodash/webmirror.py).

Run from the repo root:  python -m unittest discover -s tests

Drives BoxLink against a FAKE box socket and the Flask app through its test
client. No browser, no real kilodash.

The backend's defining property is that it holds **no authority** — it relays
and fans out, and never invents state. Most of what is tested here is that
property in its various disguises:

  * the §6 envelope allow-list is closed, and the two deliberately-cut actions
    (`scroll`, `field_set`) stay rejected;
  * semantic validation is NOT attempted here — an unknown tile is forwarded
    with 202 and refused asynchronously by the box, because guessing screen
    meaning in this process would create a second authority;
  * a browser joining mid-stream gets a synthesised snapshot first (§5);
  * deltas shallow-merge, and a `rev` gap resyncs instead of patching onto a
    base that skipped a state.

Two regressions are pinned deliberately, both found by restarting the box
under a live stream rather than by reading the code:
  * `seq` tracking must reset on a NEW box connection (§2 restarts it at 1),
    or every box restart looks like a gap and fires a spurious resync;
  * the "box link lost" notice must be EDGE-triggered, or a long outage
    floods every browser once per reconnect attempt.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import webmirror  # noqa: E402
from kilodash.eventsock import PROTOCOL_VERSION  # noqa: E402


class FakeBox:
    """A stand-in for kilodash's event socket: accepts one subscriber, lets
    the test push frames at it, and records commands received."""

    def __init__(self, path):
        self.path = path
        self.commands = []
        self.conn = None
        self._stop = threading.Event()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(1)
        self.sock.settimeout(0.3)
        self.ready = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            self.conn = conn
            self.ready.set()
            buf = b""
            conn.settimeout(0.3)
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            self.commands.append(json.loads(line))
                        except ValueError:
                            pass
            self.conn = None

    def push(self, frame):
        frame.setdefault("v", PROTOCOL_VERSION)
        frame.setdefault("t", time.time())
        c = self.conn
        if c:
            c.sendall((json.dumps(frame) + "\n").encode())
            time.sleep(0.08)          # let the reader thread pick it up

    def drop(self):
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None

    def stop(self):
        self._stop.set()
        self.drop()
        try:
            self.sock.close()
        except OSError:
            pass


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "events.sock")
        self.box = FakeBox(self.path)
        self.addCleanup(self.box.stop)
        self.link = webmirror.BoxLink(self.path).start()
        self.addCleanup(self.link._stop.set)
        self.app = webmirror.create_app(self.link)
        self.client = self.app.test_client()
        self.assertTrue(self.box.ready.wait(3), "backend never connected")
        time.sleep(0.2)

    def handshake(self, tile="home", model=None):
        self.box.push({"type": "Hello", "seq": 1, "device": "x",
                       "protocol": PROTOCOL_VERSION,
                       "theme": {"name": "green"}})
        self.box.push({"type": "ScreenSnapshot", "seq": 2, "tile": tile,
                       "nav": ["home"], "rev": 0, "tiles": [],
                       "model": model or {"kind": "home", "tiles": []},
                       "alerts": []})


class CommandEnvelope(Case):
    def post(self, body):
        return self.client.post("/api/input", json=body)

    def test_valid_actions_are_accepted_and_forwarded(self):
        self.handshake()
        for body in ({"action": "home"}, {"action": "back"},
                     {"action": "request_snapshot"},
                     {"action": "tap_tile", "tile": "can-bus"},
                     {"action": "button_press", "button": "refresh"}):
            with self.subTest(action=body["action"]):
                r = self.post(body)
                self.assertEqual(r.status_code, 202)
        time.sleep(0.2)
        actions = [c["action"] for c in self.box.commands]
        for want in ("home", "back", "tap_tile", "button_press"):
            self.assertIn(want, actions)

    def test_202_means_accepted_not_applied(self):
        """The response must not claim the action happened — the result
        arrives as an event, and only as an event."""
        self.handshake()
        r = self.post({"action": "home"})
        self.assertEqual(r.status_code, 202)
        self.assertIn("accepted", r.get_json())

    def test_unknown_action_rejected(self):
        self.assertEqual(self.post({"action": "reboot"}).status_code, 400)

    def test_cut_actions_stay_rejected(self):
        """`scroll` and `field_set` were deliberately removed from v1.
        Reinstating either must be a deliberate act, not a regression."""
        for body in ({"action": "scroll", "dy": 10},
                     {"action": "field_set", "field": "bitrate",
                      "value": 250000}):
            with self.subTest(action=body["action"]):
                self.assertEqual(self.post(body).status_code, 400)

    def test_wrong_type_rejected(self):
        r = self.post({"action": "tap_tile", "tile": 42})
        self.assertEqual(r.status_code, 400)

    def test_missing_field_rejected(self):
        self.assertEqual(self.post({"action": "tap_tile"}).status_code, 400)

    def test_non_object_body_rejected(self):
        self.assertEqual(self.client.post("/api/input", json=[1, 2]).status_code,
                         400)

    def test_semantic_validity_is_NOT_judged_here(self):
        """An unknown tile is well-formed. The backend forwards it and the BOX
        refuses it — attempting the judgement here would need a synchronous
        round-trip and would put a second authority in the system."""
        self.handshake()
        r = self.post({"action": "tap_tile", "tile": "no-such-screen"})
        self.assertEqual(r.status_code, 202)
        time.sleep(0.2)
        self.assertIn({"action": "tap_tile", "tile": "no-such-screen"},
                      self.box.commands)

    def test_503_when_box_is_down(self):
        self.box.drop()
        time.sleep(0.5)
        self.assertEqual(self.post({"action": "home"}).status_code, 503)


class ModelMaintenance(Case):
    def test_snapshot_populates_state(self):
        self.handshake(tile="can-bus",
                       model={"kind": "canbus", "frame_rate": 800,
                              "total": 5})
        self.assertEqual(self.link.tile, "can-bus")
        self.assertEqual(self.link.model["frame_rate"], 800)

    def test_delta_shallow_merges(self):
        self.handshake(tile="can-bus",
                       model={"kind": "canbus", "frame_rate": 800,
                              "total": 5, "rows": ["a"]})
        self.box.push({"type": "DataUpdated", "seq": 3, "tile": "can-bus",
                       "rev": 1, "changed": {"frame_rate": 812}})
        self.assertEqual(self.link.model["frame_rate"], 812)
        self.assertEqual(self.link.model["total"], 5, "untouched key must "
                                                      "survive the merge")
        self.assertEqual(self.link.model["rows"], ["a"])

    def test_arrays_replace_whole(self):
        self.handshake(tile="can-bus",
                       model={"kind": "canbus", "rows": ["a", "b"]})
        self.box.push({"type": "DataUpdated", "seq": 3, "tile": "can-bus",
                       "rev": 1, "changed": {"rows": ["c"]}})
        self.assertEqual(self.link.model["rows"], ["c"],
                         "arrays are sent whole; there is no array patching")

    def test_rev_gap_triggers_resync_not_patch(self):
        """§4: a gap means an intermediate state was merged or lost. Patching
        onto the wrong base is the silent divergence the protocol forbids."""
        self.handshake(tile="can-bus",
                       model={"kind": "canbus", "frame_rate": 800})
        self.box.commands.clear()
        self.box.push({"type": "DataUpdated", "seq": 3, "tile": "can-bus",
                       "rev": 5, "changed": {"frame_rate": 999}})
        time.sleep(0.3)
        self.assertIn({"action": "request_snapshot"}, self.box.commands)
        self.assertNotEqual(self.link.model.get("frame_rate"), 999,
                            "a gapped delta must not be applied")

    def test_tilechanged_replaces_model_and_resets_rev(self):
        self.handshake()
        self.box.push({"type": "TileChanged", "seq": 3, "tile": "n2k",
                       "nav": ["home", "n2k"], "rev": 0,
                       "model": {"kind": "n2k", "fields": []}})
        self.assertEqual(self.link.tile, "n2k")
        self.assertEqual(self.link.rev, 0)
        self.assertEqual(self.link.model["kind"], "n2k")

    def test_alerts_tracked_across_tiles(self):
        self.handshake()
        self.box.push({"type": "AlertFired", "seq": 3,
                       "alert": {"id": "a1", "severity": "fault"}})
        self.assertIn("a1", self.link.alerts)
        snap = self.link.synth_snapshot()
        self.assertEqual(len(snap["alerts"]), 1)
        self.box.push({"type": "AlertCleared", "seq": 4,
                       "alert": {"id": "a1"}})
        self.assertNotIn("a1", self.link.alerts)

    def test_version_mismatch_frame_is_refused(self):
        """§9: loud and fatal, never a partial parse."""
        self.handshake()
        before = dict(self.link.model)
        self.box.push({"v": 99, "type": "TileChanged", "seq": 3,
                       "tile": "evil", "model": {"kind": "x"}})
        self.assertEqual(self.link.model, before)
        self.assertNotEqual(self.link.tile, "evil")


class SnapshotFirst(Case):
    def test_synth_snapshot_reflects_applied_deltas(self):
        """A browser joining mid-stream must get CURRENT state, not the last
        thing the box happened to send as a snapshot."""
        self.handshake(tile="can-bus",
                       model={"kind": "canbus", "frame_rate": 800})
        self.box.push({"type": "DataUpdated", "seq": 3, "tile": "can-bus",
                       "rev": 1, "changed": {"frame_rate": 812}})
        snap = self.link.synth_snapshot()
        self.assertEqual(snap["type"], "ScreenSnapshot")
        self.assertEqual(snap["model"]["frame_rate"], 812)
        self.assertEqual(snap["tile"], "can-bus")

    def test_no_snapshot_before_box_speaks(self):
        self.assertIsNone(self.link.synth_snapshot())

    def test_state_endpoint_reports_health(self):
        self.handshake()
        d = self.client.get("/api/state").get_json()
        self.assertTrue(d["box_connected"])
        self.assertEqual(d["protocol"], PROTOCOL_VERSION)
        self.assertTrue(d["has_snapshot"])


class BoxRestartRegressions(Case):
    """Both found by restarting the box under a live stream, not by reading
    the code. Neither is visible in a single-connection test."""

    def test_seq_resets_on_new_box_connection(self):
        """§2: a NEW connection restarts seq at 1. Carrying the old counter
        over makes the first frame after every box restart look like a gap."""
        self.handshake()
        self.box.push({"type": "DataUpdated", "seq": 3, "tile": "home",
                       "rev": 1, "changed": {"tiles": []}})
        self.assertEqual(self.link.seq, 3)
        self.box.drop()
        time.sleep(1.2)                       # let it reconnect
        self.assertIsNone(self.link.seq,
                          "seq tracking must be cleared for the new "
                          "connection, not carried across it")
        self.box.commands.clear()
        self.assertTrue(self.box.ready.wait(3))
        time.sleep(0.2)
        self.handshake()
        time.sleep(0.3)
        self.assertNotIn({"action": "request_snapshot"}, self.box.commands,
                         "a clean reconnect must not look like a gap")

    def test_link_lost_notice_is_edge_triggered(self):
        """Announcing the outage once per retry would flood every browser for
        its whole duration — and "still down" is not news."""
        self.handshake()
        q = self.link.subscribe()
        self.box.stop()                       # gone, and staying gone
        time.sleep(2.5)                       # several reconnect attempts
        errs = []
        while not q.empty():
            f = q.get_nowait()
            if f.get("type") == "Error" and f.get("code") == "resync":
                errs.append(f)
        self.assertEqual(len(errs), 1,
                         f"expected exactly one link-lost notice, got "
                         f"{len(errs)} across several retries")


class FanOut(Case):
    def test_frames_reach_every_subscriber(self):
        self.handshake()
        a, b = self.link.subscribe(), self.link.subscribe()
        self.box.push({"type": "AlertFired", "seq": 3,
                       "alert": {"id": "x", "severity": "fault"}})
        for q in (a, b):
            got = [q.get_nowait() for _ in range(q.qsize())]
            self.assertTrue(any(f["type"] == "AlertFired" for f in got))

    def test_slow_subscriber_is_dropped_not_buffered(self):
        """A tab that cannot keep up is dropped and resyncs on reconnect —
        cheaper and more correct than replaying a backlog it would discard."""
        self.handshake()
        q = self.link.subscribe()
        for i in range(webmirror.SSE_QUEUE_MAX + 20):
            self.link._broadcast({"type": "DataUpdated", "seq": i, "rev": i})
        self.assertNotIn(q, self.link._subs)
        self.assertGreaterEqual(self.link.stats["dropped_subs"], 1)


if __name__ == "__main__":
    unittest.main()
