"""Unit tests for the web-mirror event emitter (kilodash/eventsock.py).

Run from the repo root:  python -m unittest discover -s tests

Drives a real Unix socket against a fake App — no panel, no framebuffer, no
screens beyond stubs. What matters here is not that JSON comes out, but that
the invariants WEB-PROTOCOL.md leans on actually hold, because every one of
them is load-bearing for a diagnostics tool that must never lie:

  * §5  handshake is Hello THEN ScreenSnapshot, unprompted, every connection;
  * §4  `rev` is assigned at MODEL-CHANGE time, so a coalesced frame still
        burns its number and the gap stays detectable. This is the one that
        silently corrupts a client if it is got wrong, so it is tested
        directly rather than inferred from behaviour;
  * §4  deltas carry only changed top-level keys, arrays whole;
  * §7  coalescing holds the floor, and merged deltas lose nothing;
  * §1  a second subscriber is refused with `busy`, not silently dropped;
  * §7  an absent or dead subscriber never raises into the caller — the
        panel must not notice the mirror at all.
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

from kilodash import eventsock  # noqa: E402


# ---------------------------------------------------------------- fakes -----
class FakeTheme:
    name = "green"
    bg = (0, 9, 3); card = (3, 26, 10); card_hi = (8, 46, 18)
    fg = (51, 245, 70); muted = (0, 150, 40); accent = (130, 255, 120)
    ok = (51, 235, 80); warn = (255, 190, 40); bad = (255, 75, 60)


class FakeScreen:
    def __init__(self, tile_id, title, model=None, glyph=None, avail=True):
        self.tile_id, self.title, self.glyph = tile_id, title, glyph
        self.device_key = None
        self._model = model or {"kind": "generic", "title": title,
                                "rows": [], "buttons": []}
        self._avail = avail

    def available(self):
        return self._avail

    def model(self):
        return dict(self._model)


class FakeApp:
    version = "test"

    def __init__(self, screens):
        self.theme = FakeTheme()
        self.screens = screens
        self.current = screens[0]

    def is_launcher(self, scr):
        return scr is self.screens[0]


class Client:
    """A subscriber that reads NDJSON frames off the socket."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.sock.settimeout(2.0)
        self.buf = b""

    def frames(self, n, timeout=2.0):
        out, deadline = [], time.time() + timeout
        while len(out) < n and time.time() < deadline:
            while b"\n" in self.buf and len(out) < n:
                line, self.buf = self.buf.split(b"\n", 1)
                if line.strip():
                    out.append(json.loads(line))
            if len(out) >= n:
                break
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            self.buf += chunk
        return out

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class EmitterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "events.sock")
        self.home = FakeScreen("home", "Scottina",
                               {"kind": "home", "tiles": []})
        self.can = FakeScreen("can-bus", "CAN Bus", {
            "kind": "canbus", "iface": "can0", "frame_rate": 800,
            "total": 1, "rows": [], "truncated": False})
        self.app = FakeApp([self.home, self.can])
        self.em = eventsock.EventEmitter(self.app, path=self.path).start()
        self.addCleanup(self.em.stop)
        self.clients = []

    def client(self):
        c = Client(self.path)
        self.clients.append(c)
        self.addCleanup(c.close)
        return c

    def _settle(self, secs=0.3):
        time.sleep(secs)


class Handshake(EmitterCase):
    def test_hello_then_snapshot_unprompted(self):
        """§5: a client renders nothing until it holds a snapshot, so the box
        must send one without being asked."""
        c = self.client()
        f = c.frames(2)
        self.assertEqual([x["type"] for x in f], ["Hello", "ScreenSnapshot"])
        self.assertEqual([x["seq"] for x in f], [1, 2])

    def test_hello_carries_the_palette(self):
        """§3: the palette is normative — both surfaces must read as one
        instrument, and the web derives colour from this, not a hardcode."""
        hello = self.client().frames(1)[0]
        th = hello["theme"]
        self.assertEqual(th["name"], "green")
        for key in ("bg", "fg", "ok", "warn", "bad", "accent"):
            self.assertEqual(len(th[key]), 3, key)

    def test_snapshot_lists_unavailable_tiles(self):
        """Absent hotplug devices render dimmed, never vanish — the web must
        show the same inventory the panel does."""
        self.can._avail = False
        snap = self.client().frames(2)[1]
        ids = {t["id"]: t for t in snap["tiles"]}
        self.assertIn("can-bus", ids)
        self.assertFalse(ids["can-bus"]["available"])

    def test_second_subscriber_is_told_why(self):
        """§1/§8: one subscriber. A silent close would look like a crash and
        invite a retry storm."""
        self.client().frames(2)
        self._settle()
        c2 = self.client()
        f = c2.frames(1)
        self.assertEqual(f[0]["type"], "Error")
        self.assertEqual(f[0]["code"], "busy")


class RevAssignment(EmitterCase):
    """§4 — the invariant that silently corrupts a client if got wrong."""

    def test_rev_is_assigned_at_change_time_not_send_time(self):
        """Three rapid changes inside one coalescing window produce ONE
        delta, but it must carry rev=3 — the two superseded frames burned
        revs 1 and 2, so the client sees a gap and knows to resync. If rev
        were assigned at send time this delta would say rev=1 and the loss
        would be invisible."""
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)          # rev resets to 0
        for rate in (810, 820, 830):
            self.can._model["frame_rate"] = rate
            self.em.note_model(self.can)
        self.assertEqual(self.em._rev, 3,
                         "rev must advance once per model change, even when "
                         "the frame is coalesced away")

    def test_coalesced_delta_reports_the_latest_rev(self):
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        c.frames(1)                       # TileChanged
        # The first change flushes immediately — the window is clear, and
        # there is no reason to delay a delta nobody is competing with. The
        # next two land inside the window and coalesce into one.
        for rate in (810, 820, 830):
            self.can._model["frame_rate"] = rate
            self.em.note_model(self.can)
        time.sleep(eventsock.COALESCE_S + 0.02)
        self.em.pump()
        deltas = [f for f in c.frames(2) if f["type"] == "DataUpdated"]
        self.assertEqual(len(deltas), 2, "3 changes -> 1 immediate + 1 merged")
        self.assertEqual(deltas[0]["rev"], 1)
        self.assertEqual(deltas[-1]["rev"], 3,
                         "the merged delta reports the LATEST rev, so revs 2 "
                         "and 3 read as a gap and the client resyncs")
        self.assertEqual(deltas[-1]["changed"]["frame_rate"], 830)

    def test_tilechanged_resets_rev_and_carries_full_model(self):
        """§3: a nav is a screen-scoped snapshot, so the client never has to
        ask for one afterwards. rev 0 after a high rev is not a gap."""
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        self.can._model["frame_rate"] = 900
        self.em.note_model(self.can)
        self.em.note_tile(self.home)
        frames = c.frames(3)
        tc = [f for f in frames if f["type"] == "TileChanged"]
        self.assertEqual(tc[-1]["rev"], 0)
        self.assertIn("model", tc[-1])
        self.assertEqual(tc[-1]["nav"], ["home"])

    def test_nav_never_exceeds_two(self):
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        tc = c.frames(1)[0]
        self.assertEqual(tc["nav"], ["home", "can-bus"])


class Deltas(EmitterCase):
    def test_only_changed_keys_are_sent(self):
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        c.frames(1)
        self.can._model["frame_rate"] = 999
        self.em.note_model(self.can)
        time.sleep(eventsock.COALESCE_S + 0.02)
        self.em.pump()
        d = c.frames(1)[0]
        self.assertEqual(set(d["changed"]), {"frame_rate"},
                         "unchanged keys must not be resent")

    def test_no_change_emits_nothing(self):
        """§7: emit on actual change, not on tick. tick() returning True is
        not a change signal — several screens animate."""
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        c.frames(1)
        before = self.em.emitted
        for _ in range(5):
            self.em.note_model(self.can)
            self.em.pump()
        self.assertEqual(self.em.emitted, before)

    def test_merged_delta_loses_nothing(self):
        """Two different keys changing inside one window must both survive
        the merge — a superseding frame merges, it does not replace."""
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        c.frames(1)
        # Prime the window: the first change flushes straight through, so
        # both keys under test have to land AFTER it to exercise the merge.
        self.can._model["frame_rate"] = 1
        self.em.note_model(self.can)
        c.frames(1)
        self.can._model["frame_rate"] = 111
        self.em.note_model(self.can)
        self.can._model["total"] = 222
        self.em.note_model(self.can)
        time.sleep(eventsock.COALESCE_S + 0.02)
        self.em.pump()
        d = c.frames(1)[0]
        self.assertEqual(d["changed"]["frame_rate"], 111)
        self.assertEqual(d["changed"]["total"], 222,
                         "a superseding frame MERGES into the pending delta; "
                         "replacing it would silently drop a changed key")

    def test_coalescing_holds_the_floor(self):
        """§7: a screen changing faster than 10 Hz is capped. This is the
        rule that keeps CAN at bus rate from saturating the socket."""
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)
        c.frames(1)
        sent = self.em.emitted
        for i in range(50):
            self.can._model["frame_rate"] = i
            self.em.note_model(self.can)
        self.assertLessEqual(self.em.emitted - sent, 1,
                             "50 changes inside one window must not produce "
                             "50 frames")


class Alerts(EmitterCase):
    def test_fired_then_cleared(self):
        c = self.client()
        c.frames(2)
        a = {"id": "n2k:127488:0:range", "tile": "n2k", "kind": "range",
             "label": "ENGINE RPM", "severity": "fault"}
        self.em.note_alert(a, fired=True)
        self.em.note_alert(a, fired=False)
        f = c.frames(2)
        self.assertEqual([x["type"] for x in f],
                         ["AlertFired", "AlertCleared"])
        self.assertEqual(f[1]["alert"]["id"], a["id"])

    def test_active_alerts_ride_the_snapshot(self):
        """Alerts are box-scoped, not screen-scoped — a client connecting
        mid-alert must learn about it."""
        c = self.client()
        c.frames(2)
        self.em.note_alert({"id": "x", "severity": "fault"}, fired=True)
        c.frames(1)
        self.em.send_snapshot(force=True)
        snap = c.frames(1)[0]
        self.assertEqual(len(snap["alerts"]), 1)


class Commands(EmitterCase):
    def test_inbound_commands_are_queued_for_the_ui_thread(self):
        """§6: parsed off-thread, APPLIED on the UI thread — a background
        thread must never call into a screen."""
        c = self.client()
        c.frames(2)
        c.send({"action": "tap_tile", "tile": "can-bus"})
        self._settle()
        cmds = self.em.take_commands()
        self.assertEqual(cmds, [{"action": "tap_tile", "tile": "can-bus"}])
        self.assertEqual(self.em.take_commands(), [], "drain must be once-only")

    def test_malformed_commands_are_dropped_not_raised(self):
        c = self.client()
        c.frames(2)
        c.send({"no_action": True})
        self.sock_send_garbage(c)
        self._settle()
        self.assertEqual(self.em.take_commands(), [])

    def sock_send_garbage(self, c):
        c.sock.sendall(b"{not json at all\n")

    def test_snapshot_is_rate_limited(self):
        """§7: request_snapshot is the one command that makes the box do
        real work, so it cannot be used to defeat best-effort emission."""
        c = self.client()
        c.frames(2)
        self.em.send_snapshot(force=True)
        before = self.em.emitted
        for _ in range(10):
            self.em.send_snapshot()          # not forced
        self.assertEqual(self.em.emitted, before,
                         "snapshots inside the window must fold, not queue")


class NeverHarmsThePanel(EmitterCase):
    """§7 — the whole point. None of these may raise into the caller."""

    def test_no_subscriber_is_harmless(self):
        for _ in range(100):
            self.em.note_model(self.can)
            self.em.note_tile(self.can)
            self.em.pump()
        self.assertEqual(self.em.emitted, 0)

    def test_dead_subscriber_does_not_raise(self):
        c = self.client()
        c.frames(2)
        c.close()
        self._settle()
        for i in range(50):
            self.can._model["frame_rate"] = i
            self.em.note_model(self.can)
            self.em.note_tile(self.can)
            self.em.pump()

    def test_model_that_raises_is_contained(self):
        """A screen's model() must never be able to take the panel down."""
        class Exploding(FakeScreen):
            def model(self):
                raise RuntimeError("boom")

        bad = Exploding("bad", "Bad")
        c = self.client()
        c.frames(2)
        self.em.note_tile(bad)
        f = c.frames(1)[0]
        self.assertEqual(f["model"]["kind"], "generic")
        self.assertIn("RuntimeError", f["model"]["note"])

    def test_unserialisable_model_is_dropped_not_raised(self):
        self.can._model["rows"] = [{"bad": object()}]
        c = self.client()
        c.frames(2)
        self.em.note_tile(self.can)       # must not raise

    def test_oversized_frame_is_dropped(self):
        """§2: a producer never emits over 64 KiB — the row caps truncate
        first. Reaching the ceiling is an upstream bug, so drop rather than
        ship a frame the consumer is required to disconnect over."""
        c = self.client()
        c.frames(2)
        self.can._model["rows"] = ["x" * 1000 for _ in range(100)]
        before, dropped = self.em.emitted, self.em.dropped
        self.em.note_tile(self.can)
        self.assertEqual(self.em.emitted, before)
        self.assertEqual(self.em.dropped, dropped + 1)


class HomeModelInvariants(unittest.TestCase):
    """The launcher model against the real LauncherScreen — the one screen
    whose model encodes a rule that is easy to get subtly wrong."""

    def setUp(self):
        """Exercises LauncherScreen.model() against stub screens. The rule
        under test lives in the launcher, not in the individual screens, and
        instantiating all 22 real ones would need a whole App (config, CAN
        blocks, hardware probes) to test a dict comprehension."""
        from kilodash.screens.home import LauncherScreen

        present = {"can"}          # pretend exactly one device is plugged in

        class FakeDevices:
            def has(self, key):
                return key in present

            def refresh(self, force=False):
                pass

        class Stub:
            """A screen as the launcher sees it."""
            def __init__(self, tid, title, glyph=None, device_key=None):
                self.tile_id, self.title = tid, title
                self.glyph, self.device_key = glyph, device_key

            def available(self):
                return True

        class StubApp:
            def __init__(self):
                self.devices = FakeDevices()
                self.theme = FakeTheme()
                self.screens = []

        self.app = StubApp()
        self.launcher = LauncherScreen.__new__(LauncherScreen)
        self.launcher.app = self.app
        self.app.screens = [
            self.launcher,                                   # index 0
            Stub('can-bus', 'CAN Bus', 'can', 'can'),        # device PRESENT
            Stub('rtl-sdr', 'RTL-SDR', 'sdr', 'sdr'),        # device ABSENT
            Stub('lan-scan', 'LAN Scan', 'lan'),             # not a device tile
        ]

    def test_badge_implies_available(self):
        """`badge:"lit"` means THE DEVICE IS PRESENT, not merely that this is
        a device screen. The panel gets that free (it only draws present
        tiles); the web shows absent tiles dimmed, so an ungated badge would
        render dimmed-and-badged, which contradicts itself."""
        for t in self.launcher.model()["tiles"]:
            if t["badge"] == "lit":
                with self.subTest(tile=t["id"]):
                    self.assertTrue(t["available"],
                                    f"{t['id']} is badged present but marked "
                                    f"unavailable")

    def test_absent_device_tiles_are_listed_not_hidden(self):
        """Hotplug-absent screens render dimmed, never vanish — the web must
        show the same inventory the operator sees."""
        tiles = self.launcher.model()["tiles"]
        self.assertTrue(any(t["available"] is False for t in tiles),
                        "expected at least one absent device tile")

    def test_launcher_excludes_itself(self):
        ids = {t["id"] for t in self.launcher.model()["tiles"]}
        self.assertNotIn("home", ids, "the launcher must not list itself")

    def test_every_tile_carries_a_wire_id(self):
        for t in self.launcher.model()["tiles"]:
            with self.subTest(tile=t.get("title")):
                self.assertTrue(t.get("id"))


class GenericModelInvariants(unittest.TestCase):
    """Rules the generic fallback has to hold for every screen."""

    def test_note_only_appears_on_a_genuinely_empty_screen(self):
        """The note explains a panel with NOTHING on it. On a screen with real
        rows and working controls it reads as breakage rather than honesty —
        which is exactly how it was reported from the field."""
        from kilodash.screens.base import Screen

        class Empty(Screen):
            tile_id = "empty"

        class Full(Screen):
            tile_id = "full"

            def model_rows(self):
                return [{"label": "A", "value": "1", "state": None}]

        class ButtonsOnly(Screen):
            tile_id = "btn"

            def model_buttons(self):
                return [{"id": "go", "label": "GO", "enabled": True,
                         "confirm": False}]

        # __new__: Screen.__init__ wants an app, and none of this needs one.
        self.assertIn("note", Screen.model(Empty.__new__(Empty)))
        self.assertNotIn("note", Screen.model(Full.__new__(Full)),
                         "a screen with rows must not be labelled unfinished")
        self.assertNotIn("note", Screen.model(ButtonsOnly.__new__(ButtonsOnly)),
                         "a screen with controls must not be labelled "
                         "unfinished")


class LanModelUsesLiveJobState(unittest.TestCase):
    """LanScreen.status is a STATIC hint set once in __init__ ("Set a target
    and tap Run"). The live status lives on the job. Reading the screen field
    made the mirror show a stale prompt for the entire duration of a scan
    while the panel showed real progress."""

    def _screen(self, job):
        from kilodash.screens.lan import LanScreen
        s = LanScreen.__new__(LanScreen)
        s.mode, s.target, s.ports = "Discover", "10.0.0.0/24", ""
        s.status = "Set a target and tap Run"      # the static hint
        s.selected_ip = None
        s.job = job
        return s

    def test_status_row_tracks_the_job_not_the_static_hint(self):
        class Job:
            done = False
            status = "Scanning…"
            host_count = 3
            error = None

            def hosts_snapshot(self):
                return []

        rows = {r["label"]: r["value"] for r in self._screen(Job()).model_rows()}
        self.assertEqual(rows["STATUS"], "Scanning…")
        self.assertNotIn("Set a target", " ".join(str(v) for v in rows.values()),
                         "the static hint must not be shown as scan status")
        self.assertEqual(rows["SCAN"], "RUNNING")

    def test_discovered_hosts_are_emitted_not_just_counted(self):
        """The hosts are the point of this screen; a count alone is not a
        mirror of it."""
        class Job:
            done = True
            status = "Complete · 2 host(s)"
            host_count = 2
            error = None

            def hosts_snapshot(self):
                return [{"ip": "10.0.0.5", "host": "nas", "up": True,
                         "mac": "AA:BB", "ports": [22, 80]},
                        {"ip": "10.0.0.9", "host": "", "up": True,
                         "mac": "", "ports": []}]

        rows = self._screen(Job()).model_rows()
        labels = [r["label"] for r in rows]
        self.assertIn("10.0.0.5", labels)
        self.assertIn("10.0.0.9", labels)
        row = next(r for r in rows if r["label"] == "10.0.0.5")
        self.assertIn("nas", row["value"])

    def test_no_job_yet_is_reported_as_idle(self):
        rows = {r["label"]: r["value"] for r in self._screen(None).model_rows()}
        self.assertEqual(rows["SCAN"], "IDLE")


if __name__ == "__main__":
    unittest.main()
