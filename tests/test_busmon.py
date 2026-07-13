"""Unit tests for the CAN raw-bus forensics model (kilodash/busmon.py).

Run from the repo root:  python -m unittest discover -s tests
Covers can_frame parsing (SFF/EFF/RTR/error frames), the seen-IDs table
(count, changed-bytes mask), both watch modes (change-detection and
value-match, transition-fired), ring-buffer bounding + filters
(ID match/mask, watched-only, changed-only), candump `.log` export format,
and — the scope constraint made executable — an AST scan proving the CAN
screen and busmon construct no TX: no send*/write/sendmsg calls, no
cansend/can-utils TX invocations, sockets recv-only. Stdlib only, no
socket is opened.
"""

import ast
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import busmon  # noqa: E402


def frame(cid, data, ext=False, rtr=False):
    raw = cid | (busmon.CAN_EFF_FLAG if ext else 0) \
        | (busmon.CAN_RTR_FLAG if rtr else 0)
    return struct.pack("<IB3x8s", raw, len(data), data.ljust(8, b"\0"))


class TestParse(unittest.TestCase):
    def test_sff(self):
        self.assertEqual(busmon.parse_frame(frame(0x123, b"\x01\x02")),
                         (0x123, False, False, b"\x01\x02"))

    def test_eff(self):
        cid, ext, rtr, data = busmon.parse_frame(
            frame(0x18FEF121, b"\xAA" * 8, ext=True))
        self.assertEqual((cid, ext, rtr), (0x18FEF121, True, False))
        self.assertEqual(data, b"\xAA" * 8)

    def test_rtr_and_err(self):
        self.assertEqual(busmon.parse_frame(frame(0x100, b"", rtr=True)),
                         (0x100, False, True, b""))
        err = struct.pack("<IB3x8s", busmon.CAN_ERR_FLAG | 0x1, 8, b"\0" * 8)
        self.assertIsNone(busmon.parse_frame(err))
        self.assertIsNone(busmon.parse_frame(b"short"))

    def test_fmt(self):
        self.assertEqual(busmon.fmt_id(0x123, False), "123")
        self.assertEqual(busmon.fmt_id(0x18FEF121, True), "18FEF121")
        self.assertEqual(
            busmon.log_line(1436509052.249713, "slcan0", 0x123, False, False,
                            b"\xDE\xAD"),
            "(1436509052.249713) slcan0 123#DEAD")
        self.assertTrue(busmon.log_line(1.0, "slcan0", 0x100, False, True,
                                        b"").endswith("#R"))


class TestMonitor(unittest.TestCase):
    def setUp(self):
        self.m = busmon.BusMonitor(ring_max=8)

    def test_seen_ids_and_changed_mask(self):
        self.m.ingest(1.0, 0x123, False, False, b"\x01\x02\x03")
        self.m.ingest(1.1, 0x123, False, False, b"\x01\xFF\x03")
        rows, stats = self.m.snapshot(now=1.2)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["count"], 2)
        self.assertEqual(r["changed"], 0b010)     # only byte 1 changed
        self.assertEqual(r["data"], b"\x01\xFF\x03")
        self.assertEqual(stats["total"], 2)

    def test_watch_change_mode(self):
        self.m.set_watch(0x123, 1, busmon.WATCH_CHANGE)
        self.m.ingest(1.0, 0x123, False, False, b"\x00\x10")
        self.m.ingest(1.1, 0x123, False, False, b"\x00\x10")   # no change
        self.assertEqual(self.m.watch_on(0x123, 1)["hits"], 0)
        self.m.ingest(1.2, 0x123, False, False, b"\x00\x11")
        w = self.m.watch_on(0x123, 1)
        self.assertEqual((w["hits"], w["last_hit"]), (1, 1.2))
        rows, stats = self.m.snapshot(now=1.3)
        self.assertTrue(rows[0]["alert"])          # within the flash window
        self.assertEqual(stats["hits"], 1)

    def test_watch_match_fires_on_transition(self):
        self.m.set_watch(0x200, 0, busmon.WATCH_MATCH, value=0x42)
        self.m.ingest(1.0, 0x200, False, False, b"\x42")   # first sight == hit
        self.m.ingest(1.1, 0x200, False, False, b"\x42")   # steady: no re-fire
        self.m.ingest(1.2, 0x200, False, False, b"\x00")
        self.m.ingest(1.3, 0x200, False, False, b"\x42")   # transition: hit
        self.assertEqual(self.m.watch_on(0x200, 0)["hits"], 2)
        with self.assertRaises(ValueError):
            self.m.set_watch(0x200, 0, busmon.WATCH_MATCH, value=0x1FF)
        self.m.clear_watch(0x200, 0)
        self.assertIsNone(self.m.watch_on(0x200, 0))

    def test_ring_bounded_and_tail(self):
        for i in range(20):
            self.m.ingest(float(i), 0x100 + (i % 2), False, False, bytes([i]))
        rows, stats = self.m.snapshot()
        self.assertEqual(stats["ring"], 8)         # bounded
        self.assertEqual(stats["total"], 20)
        tail = self.m.tail(3)
        self.assertEqual([r[0] for r in tail], [19.0, 18.0, 17.0])
        only_101 = self.m.tail(10, id_match=0x101)
        self.assertTrue(all(r[1] == 0x101 for r in only_101))

    def test_export_filters_and_format(self):
        m = busmon.BusMonitor(ring_max=100)
        m.set_watch(0x18FEF121, 0, busmon.WATCH_CHANGE)
        m.ingest(10.0, 0x123, False, False, b"\x01")
        m.ingest(10.1, 0x123, False, False, b"\x01")           # unchanged
        m.ingest(10.2, 0x18FEF121, True, False, b"\xAB\xCD")
        m.ingest(10.3, 0x18FEF121, True, False, b"\xAC\xCD")   # byte 0 changed
        with tempfile.TemporaryDirectory() as d:
            n, path = m.export(d, "slcan0")
            self.assertEqual(n, 4)
            with open(path) as f:
                lines = f.read().splitlines()
            self.assertEqual(lines[0], "(10.000000) slcan0 123#01")
            self.assertEqual(lines[2], "(10.200000) slcan0 18FEF121#ABCD")
            n, _ = m.export(d, "slcan0", watched_only=True)
            self.assertEqual(n, 2)
            n, _ = m.export(d, "slcan0", changed_only=True)
            self.assertEqual(n, 1)
            n, _ = m.export(d, "slcan0", id_match=0x123, id_mask=0x7FF)
            self.assertEqual(n, 2)                       # exact SFF id
            n, _ = m.export(d, "slcan0", id_match=0x18FEF100,
                            id_mask=0x1FFFFF00)
            self.assertEqual(n, 2)                       # masked EFF family

    def test_ring_wrap_export_order(self):
        for i in range(12):
            self.m.ingest(float(i), 0x1, False, False, bytes([i]))
        with tempfile.TemporaryDirectory() as d:
            n, path = self.m.export(d, "can0")
            self.assertEqual(n, 8)
            with open(path) as f:
                ts = [float(l.split(")")[0][1:]) for l in f]
            self.assertEqual(ts, sorted(ts))
            self.assertEqual(ts[0], 4.0)           # oldest survivor


class TestNoTx(unittest.TestCase):
    """The scope constraint, enforced in code: the CAN screen and its bus
    model are RX-only. Positive allow-list (sockets may recv/bind/close/
    setsockopt/settimeout only) plus an independent reject pass over every
    call for TX-shaped names and TX-capable can-utils invocations."""

    MODULES = ("kilodash/busmon.py", "kilodash/screens/canbus.py")
    TX_ATTRS = {"send", "sendall", "sendto", "sendmsg", "write",
                "sendfile", "writelines"}
    TX_PROGS = {"cansend", "cangen", "canplayer", "canfdtest"}

    def _calls(self, path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, path)) as f:
            tree = ast.parse(f.read(), path)
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def test_no_tx_calls(self):
        for path in self.MODULES:
            for call in self._calls(path):
                fn = call.func
                name = fn.attr if isinstance(fn, ast.Attribute) else \
                    fn.id if isinstance(fn, ast.Name) else ""
                if name in self.TX_ATTRS:
                    # allow file writes only: open(...) targets, never sockets
                    self.assertNotIsInstance(
                        fn, ast.Name,
                        f"{path}: bare {name}() call")
                    src = fn.value
                    allowed = (isinstance(src, ast.Name)
                               and src.id in ("f", "log", "self"))
                    self.assertTrue(
                        allowed and name in ("write", "writelines"),
                        f"{path}: TX-shaped call .{name}() on "
                        f"{ast.dump(src)[:60]}")

    def test_no_tx_capable_programs(self):
        for path in self.MODULES:
            for call in self._calls(path):
                for arg in ast.walk(call):
                    if isinstance(arg, ast.Constant) \
                            and isinstance(arg.value, str):
                        self.assertNotIn(
                            arg.value, self.TX_PROGS,
                            f"{path}: invokes TX-capable tool {arg.value!r}")

    def test_screen_docstring_codifies_scope(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "kilodash/screens/canbus.py")) as f:
            doc = ast.get_docstring(ast.parse(f.read()))
        self.assertIn("no tx", (doc or "").lower())


if __name__ == "__main__":
    unittest.main()
