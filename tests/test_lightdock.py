"""Unit tests for the Light Dock lane (kilodash/lightdock.py +
tests/fakelight.py) against the shared conformance vectors.

Run from the repo root:  python -m unittest discover -s tests

Three layers, per DOCK-PROTOCOL.md §10:
  1. the CRC check value (a spec MUST) and the §2 scanner edges;
  2. every vector in To-DoLists/dock-vectors.json replayed byte-for-byte
     through the fake Light — the vectors pin the fake, the fake exercises
     the codec;
  3. the whole sync engine run against the fake on a REAL PTY — clock push,
     table diff/push (chunked), log pull/verify/delete, redock idempotence,
     no-SD and version-mismatch degradation, honest-clock refusal, and a
     wedged Light degrading to a truthful log line. No hardware, no
     firmware, no network.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kilodash import lightdock  # noqa: E402
from kilodash.lightdock import (  # noqa: E402
    DockClient, DockRemoteError, DockTimeout, FrameScanner, LightDockSync,
    build_frame, crc16)
import fakelight  # noqa: E402
from fakelight import FakeLight  # noqa: E402

VECTORS = fakelight.load_vectors()
FIXED_EPOCH = 1767225600            # the vectors' test epoch (2026-01-01Z)


class TestCRC(unittest.TestCase):
    def test_check_value(self):
        """§2: both sides MUST assert CRC-16/CCITT-FALSE("123456789")."""
        self.assertEqual(crc16(b"123456789"), 0x29B1)
        self.assertEqual(VECTORS["framing"]["crc"]["check_value"], "0x29b1")


class TestScanner(unittest.TestCase):
    def test_drip_feed_one_byte_at_a_time(self):
        frame = build_frame("HELLO", 7)
        sc = FrameScanner()
        events = []
        for i in range(len(frame)):
            events += sc.feed(frame[i:i + 1])
        self.assertEqual(events, [("frame", 0x01, 7, b"")])

    def test_payload_sof_split_across_feeds(self):
        payload = bytes([0xA5]) * 20
        frame = build_frame("PUT", 9, payload)
        sc = FrameScanner()
        events = sc.feed(frame[:11]) + sc.feed(frame[11:])
        self.assertEqual(events, [("frame", 0x04, 9, payload)])

    def test_unvalidated_len_never_buffers(self):
        sc = FrameScanner(max_payload=64)
        # false SOF claiming a 65535-byte payload, then a real frame
        self.assertEqual(sc.feed(bytes([0xA5, 0xFF, 0x00, 0xFF, 0xFF])
                                 + build_frame("BYE", 3)),
                         [("frame", 0x08, 3, b"")])


class TestVectorConformance(unittest.TestCase):
    """Every vector, byte-for-byte: wire_in through a fresh fake Light must
    produce exactly the expected wire_out concatenation."""

    def test_all_vectors(self):
        for v in VECTORS["vectors"]:
            with self.subTest(vector=v["id"]):
                fake = FakeLight(v.get("light_state"), vectors_doc=VECTORS)
                got = fake.handle_bytes(bytes.fromhex(v["wire_in"]))
                want = b"".join(bytes.fromhex(e["wire_out"])
                                for e in v["expect"])
                self.assertEqual(got.hex(), want.hex())

    def test_every_command_and_error_code_covered(self):
        types_seen = {t for v in VECTORS["vectors"]
                      for t, _ in [(v.get("request", {}).get("type"), 0)] if t}
        self.assertTrue(types_seen.issuperset(
            set(lightdock.TYPES) - {"ERROR"}))
        codes_seen = {e["frame"]["payload"]["code"] for v in VECTORS["vectors"]
                      for e in v["expect"] if e["frame"]["type"] == "ERROR"}
        self.assertEqual(codes_seen, set(lightdock.ERRORS))


class PTYTestCase(unittest.TestCase):
    """Shared plumbing: a fake Light served on a real PTY."""

    def make_fake(self, overrides=None):
        fake = FakeLight(overrides, vectors_doc=VECTORS)
        path = fake.start_pty()
        self.addCleanup(fake.stop_pty)
        return fake, path


class TestClientOverPTY(PTYTestCase):
    def test_hello_reads_negotiated_max_payload(self):
        _, path = self.make_fake()
        client = DockClient(path)
        self.addCleanup(client.close)
        info = client.hello()
        self.assertEqual(info["product"], "Scottina Light")
        self.assertEqual(info["max_payload"], 1024)
        self.assertEqual(client.max_payload, 1024)
        self.assertTrue(info["logging_suspended"])

    def test_error_frame_raises_remote_error(self):
        _, path = self.make_fake()
        client = DockClient(path)
        self.addCleanup(client.close)
        with self.assertRaises(DockRemoteError) as cm:
            client.get("/logs/missing.log", 0, 64)
        self.assertEqual(cm.exception.code_name, "ERR_NOT_FOUND")

    def test_silence_raises_timeout(self):
        import pty
        master, slave = pty.openpty()        # nobody answers on this pair
        self.addCleanup(os.close, master)
        client = DockClient(os.ttyname(slave))
        self.addCleanup(client.close)
        with self.assertRaises(DockTimeout):
            client.request("HELLO", timeout=0.2)

    def test_list_pagination_walks_all_pages(self):
        fake, path = self.make_fake()
        # 60 rolling logs at 24 B/entry: two full pages plus a remainder
        for i in range(60):
            fake.files["/logs/pag%03d.log" % i] = b"x" * (i + 1)
        client = DockClient(path)
        self.addCleanup(client.close)
        client.hello()
        r = client.list_dir("/logs/", False)
        self.assertEqual(r["count"], 62)         # 60 + the 2 fixture logs
        names = [e["name"] for e in r["entries"]]
        self.assertEqual(len(set(names)), 62)    # no dupes, no skips
        self.assertIn("pag059.log", names)
        self.assertIn("raw001.log", names)

    def test_chunked_put_commit_roundtrip(self):
        fake, path = self.make_fake()
        client = DockClient(path)
        self.addCleanup(client.close)
        client.hello()
        content = bytes((i * 7 + 3) % 256 for i in range(2500))
        dest = "/tables/blob.bin"
        size = client.put_chunk_size(dest)
        for off in range(0, len(content), size):
            client.put(dest, off, content[off:off + size])
        client.commit(dest, hashlib.sha256(content).digest())
        self.assertEqual(fake.files[dest], content)

    def test_reject_pass_is_mirrored_prime_side(self):
        client = DockClient.__new__(DockClient)     # builders only
        for bad in ("/tables/../etc", "relative.json", "/logs\\x", "/a\0b"):
            with self.assertRaises(lightdock.DockError):
                lightdock.check_path(bad)


class TestSyncEngine(PTYTestCase):
    """Full sessions against the fake — the lane's finish line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lightdock-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.captures = os.path.join(self.tmp, "captures")
        from tables import store
        self._store, self._old_base = store, store.BASE
        store.BASE = os.path.join(self.tmp, "tables")
        self.addCleanup(self._restore_base)

    def _restore_base(self):
        self._store.BASE = self._old_base

    def _install_table(self, name="engine_test", pgns=40):
        """A real store install; big enough to force chunked PUT (>1017 B)."""
        table = {"PGNs": [{"PGN": 127500 + i, "Name": "Test %d" % i,
                           "Fields": [{"Name": "F", "BitOffset": 0,
                                       "BitLength": 16}]}
                          for i in range(pgns)]}
        self._store.install(name, table, source_doc="unit-test",
                            converter_version="test")
        with open(self._store.table_path(name), "rb") as f:
            return f.read()

    def _engine(self, path, **kw):
        kw.setdefault("captures_dir", self.captures)
        kw.setdefault("clock_source", lambda: (2, "ntp"))
        kw.setdefault("time_fn", lambda: FIXED_EPOCH)
        return LightDockSync(path, **kw)

    def _texts(self, engine):
        return [t for _, t in engine.events]

    def test_full_sync_happy_path(self):
        table_bytes = self._install_table()
        self.assertGreater(len(table_bytes), 1017)   # chunked PUT for real
        fake, path = self.make_fake()
        log_fixtures = {p: c for p, c in fake.files.items()
                        if p.startswith("/logs/")}
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.COMPLETE)

        # clock pushed honestly
        self.assertEqual(fake.state["clock_epoch"], FIXED_EPOCH)
        self.assertEqual(fake.state["clock_quality"], 2)
        # tables landed (both the table and its manifest sidecar, §5 shape)
        self.assertEqual(fake.files["/tables/engine_test.json"], table_bytes)
        self.assertIn("/tables/engine_test.meta.json", fake.files)
        # logs pulled byte-exact, then deleted on Light — and only then
        for p, content in log_fixtures.items():
            dest = os.path.join(self.captures,
                                "light-" + os.path.basename(p))
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), content)
            self.assertNotIn(p, fake.files)
        self.assertEqual(engine.counts["logs_pulled"], len(log_fixtures))
        self.assertEqual(engine.counts["logs_deleted"], len(log_fixtures))
        self.assertEqual(engine.counts["tables_pushed"], 2)
        self.assertIn("session complete", self._texts(engine))
        # §6: the suspension is stated, never silent
        self.assertTrue(any("logging suspended" in t
                            for t in self._texts(engine)))

    def test_multichunk_log_pull(self):
        big = bytes((i * 11 + 5) % 256 for i in range(3000))
        fake, path = self.make_fake()
        fake.files["/logs/big.log"] = big
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.COMPLETE)
        with open(os.path.join(self.captures, "light-big.log"), "rb") as f:
            self.assertEqual(f.read(), big)
        self.assertNotIn("/logs/big.log", fake.files)

    def test_redock_is_just_the_diff_again(self):
        self._install_table()
        fake, path = self.make_fake()
        first = self._engine(path)
        self.assertEqual(first.run(), first.COMPLETE)
        # same Light, docked again: nothing to push, nothing to pull
        second = self._engine(path)
        self.assertEqual(second.run(), second.COMPLETE)
        self.assertEqual(second.counts["tables_pushed"], 0)
        self.assertEqual(second.counts["tables_skipped"], 2)
        self.assertEqual(second.counts["logs_pulled"], 0)

    def test_already_pulled_logs_delete_without_refetch(self):
        fake, path = self.make_fake()
        os.makedirs(self.captures)
        for p, content in list(fake.files.items()):
            if p.startswith("/logs/"):
                with open(os.path.join(self.captures,
                                       "light-" + os.path.basename(p)),
                          "wb") as f:
                    f.write(content)
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.COMPLETE)
        self.assertEqual(engine.counts["logs_pulled"], 0)
        self.assertEqual(engine.counts["logs_deleted"], 2)
        self.assertNotIn("GET", [t for t, _ in fake.requests])

    def test_name_collision_keeps_both(self):
        fake, path = self.make_fake()
        os.makedirs(self.captures)
        # same name and size as Light's raw001, different bytes
        imposter = b"X" * len(fake.files["/logs/raw001.log"])
        with open(os.path.join(self.captures, "light-raw001.log"), "wb") as f:
            f.write(imposter)
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.COMPLETE)
        with open(os.path.join(self.captures, "light-raw001.log"), "rb") as f:
            self.assertEqual(f.read(), imposter)     # never overwritten
        self.assertTrue(os.path.isfile(
            os.path.join(self.captures, "light-raw001.log.1")))

    def test_no_sd_degrades_truthfully(self):
        fake, path = self.make_fake({"sd_present": 0, "files": {}, "flags": 0})
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.COMPLETE)
        self.assertEqual(fake.state["clock_epoch"], FIXED_EPOCH)  # clock still
        self.assertTrue(any("no SD in Light" in t
                            for t in self._texts(engine)))
        self.assertNotIn("LIST", [t for t, _ in fake.requests])

    def test_version_mismatch_degrades_to_clock_only(self):
        fake, path = self.make_fake({"proto_version": 2})
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.COMPLETE)
        self.assertEqual(fake.state["clock_epoch"], FIXED_EPOCH)
        kinds = [t for t, _ in fake.requests]
        self.assertEqual(sorted(set(kinds) - {"HELLO", "SET_CLOCK", "BYE"}),
                         [])
        self.assertTrue(any("degraded to clock-set only" in t
                            for t in self._texts(engine)))

    def test_unsynced_clock_is_never_sent(self):
        fake, path = self.make_fake()
        engine = self._engine(path, clock_source=lambda: (0, "unsynced"),
                              pull_logs=False)
        self.assertEqual(engine.run(), engine.COMPLETE)
        self.assertNotIn("SET_CLOCK", [t for t, _ in fake.requests])
        self.assertEqual(fake.state["clock_epoch"], 0)
        self.assertTrue(any("clock NOT sent" in t
                            for t in self._texts(engine)))

    def test_wedged_light_degrades_to_a_truthful_line(self):
        class WedgedAfterHello(FakeLight):
            def _cmd_list(self, seq, payload):
                return b""                          # dead air

        fake = WedgedAfterHello(vectors_doc=VECTORS)
        path = fake.start_pty()
        self.addCleanup(fake.stop_pty)
        old = lightdock.TIMEOUT_DEFAULT
        lightdock.TIMEOUT_DEFAULT = 0.3
        self.addCleanup(setattr, lightdock, "TIMEOUT_DEFAULT", old)
        engine = self._engine(path)
        self.assertEqual(engine.run(), engine.INTERRUPTED)
        self.assertTrue(any(t.startswith("interrupted:")
                            for t in self._texts(engine)))

    def test_pull_toggle_off_skips_logs(self):
        fake, path = self.make_fake()
        engine = self._engine(path, pull_logs=False)
        self.assertEqual(engine.run(), engine.COMPLETE)
        self.assertEqual(engine.counts["logs_pulled"], 0)
        self.assertIn("/logs/raw000.log", fake.files)
        self.assertTrue(any("auto-pull is off" in t
                            for t in self._texts(engine)))


class TestVectorFileIntegrity(unittest.TestCase):
    def test_fixture_hashes(self):
        for path, fx in VECTORS["defaults"]["light_state"]["files"].items():
            content = bytes.fromhex(fx["content_hex"])
            self.assertEqual(len(content), fx["size"], path)
            self.assertEqual(hashlib.sha256(content).hexdigest(),
                             fx["sha256"], path)

    def test_mirror_matches_json_on_disk(self):
        # guard against hand-edits drifting from the generated asset
        with open(fakelight.VECTORS_PATH) as f:
            self.assertEqual(json.load(f), VECTORS)


if __name__ == "__main__":
    unittest.main()
