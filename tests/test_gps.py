"""Unit tests for the GPS plumbing (gps/ package — GPS.md).

Run from the repo root:  python -m unittest discover -s tests
Covers the shared snapshot reader (the ONE implementation of the GPS.md §3
staleness rule: fresh fix, stale file, missing file, malformed JSON, no-fix
snapshots, the §4 geotag stamp), the PA1616S config utility (NMEA checksum
build/verify, PMTK001 ack parsing, the two-baud probe order, ack-checked
command retry, the cold-start baud-raise flow) and the snapshot daemon's
pure gpsd-report → contract-object mapping plus its atomic write. No
serial port, no socket, no gpsd in the loop.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps import pa1616s, snapshot, snapshotd  # noqa: E402
from gps.gpsdio import GpsdListener  # noqa: E402

NOW = 1_800_000_000.0           # arbitrary test epoch
GOOD_SNAP = {
    "ts": snapshotd._iso_utc(NOW - 1), "fix": "3d",
    "lat": 51.5, "lon": -0.12, "sog_mps": 0.4, "cog_deg_true": 210.0,
    "alt_m": 33.0, "hdop": 1.1, "sats_used": 8, "sats_visible": 11,
    "time_quality": "gps",
}

RMC = b"$GPRMC,120000.000,A,5130.000,N,00007.200,W,0.4,210.0,180726,,,A*7B"


def _valid_rmc():
    body = RMC.decode()[1:].rpartition("*")[0]
    return f"${body}*{pa1616s.checksum(body):02X}\r\n".encode()


class FakePort:
    def __init__(self, lines=()):
        self.lines = list(lines)
        self.written = []
        self.closed = False

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def write(self, data):
        self.written.append(bytes(data))

    def flush(self):
        pass

    def close(self):
        self.closed = True


class TestSnapshotReader(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "position.json")

    def _write(self, obj):
        with open(self.path, "w") as f:
            if isinstance(obj, str):
                f.write(obj)
            else:
                json.dump(obj, f)

    def test_fresh_fix(self):
        self._write(GOOD_SNAP)
        snap, reason = snapshot.read_position(self.path, now=NOW)
        self.assertIsNone(reason)
        self.assertEqual((snap["lat"], snap["fix"]), (51.5, "3d"))

    def test_stale_is_no_fix(self):
        self._write(GOOD_SNAP)
        snap, reason = snapshot.read_position(self.path, now=NOW + 10)
        self.assertIsNone(snap)
        self.assertIn("stale", reason)

    def test_missing_file(self):
        snap, reason = snapshot.read_position(self.path, now=NOW)
        self.assertIsNone(snap)
        self.assertIn("no snapshot", reason)

    def test_malformed(self):
        for bad in ("{not json", '"a string"', '{"ts": "garbage"}'):
            self._write(bad)
            snap, reason = snapshot.read_position(self.path, now=NOW)
            self.assertIsNone(snap, bad)

    def test_fresh_but_no_fix(self):
        self._write({**GOOD_SNAP, "fix": "none", "lat": None, "lon": None})
        snap, reason = snapshot.read_position(self.path, now=NOW)
        self.assertIsNone(snap)
        self.assertEqual(reason, "no fix")

    def test_geotag_stamp_shapes(self):
        self._write(GOOD_SNAP)
        tag = snapshot.geotag(self.path, now=NOW)
        self.assertEqual(tag["gps"]["lat"], 51.5)
        self.assertNotIn("gps_reason", tag)
        tag = snapshot.geotag(self.path, now=NOW + 60)
        self.assertIsNone(tag["gps"])
        self.assertIn("stale", tag["gps_reason"])


class TestPa1616s(unittest.TestCase):
    def test_checksum_and_sentence(self):
        # known-good: $PMTK220,100*2F from the PMTK datasheet
        self.assertEqual(pa1616s.checksum("PMTK220,100"), 0x2F)
        self.assertEqual(pa1616s.sentence("PMTK220,100"),
                         b"$PMTK220,100*2F\r\n")

    def test_valid_sentence(self):
        self.assertTrue(pa1616s.valid_sentence(_valid_rmc()))
        self.assertFalse(pa1616s.valid_sentence(b"$GPRMC,junk*00\r\n"))
        self.assertFalse(pa1616s.valid_sentence(b"\xff\xfe binary noise"))
        self.assertFalse(pa1616s.valid_sentence(b"no dollar*33\r\n"))

    def test_parse_ack(self):
        ok = pa1616s.sentence("PMTK001,220,3")
        self.assertEqual(pa1616s.parse_ack(ok), (220, 3))
        self.assertIsNone(pa1616s.parse_ack(_valid_rmc()))
        self.assertIsNone(pa1616s.parse_ack(b""))

    def test_probe_prefers_target_baud(self):
        opened = []

        def open_port(dev, baud):
            opened.append(baud)
            return FakePort([_valid_rmc()])

        port, baud = pa1616s.probe("/dev/x", open_port, window=0.05)
        self.assertEqual(baud, pa1616s.TARGET_BAUD)
        self.assertEqual(opened, [115200])

    def test_probe_falls_back_to_9600_then_fails_loudly(self):
        opened = []

        def garbage_then_valid(dev, baud):
            opened.append(baud)
            return FakePort([b"\xff\x00garbage"] if baud == 115200
                            else [_valid_rmc()])

        _port, baud = pa1616s.probe("/dev/x", garbage_then_valid, window=0.05)
        self.assertEqual(baud, 9600)
        self.assertEqual(opened, [115200, 9600])

        def all_garbage(dev, baud):
            return FakePort([b"\xff\x00garbage"])

        with self.assertRaises(pa1616s.GpsConfigError):
            pa1616s.probe("/dev/x", all_garbage, window=0.05)

    def test_send_cmd_ack_and_retry(self):
        # first try times out silently, second try acked
        port = FakePort()

        def readline():
            return pa1616s.sentence("PMTK001,220,3") \
                if len(port.written) >= 2 else b""
        port.readline = readline
        pa1616s.send_cmd(port, "PMTK220,100", retries=2, timeout=0.05)
        self.assertEqual(port.written,
                         [pa1616s.sentence("PMTK220,100")] * 2)

    def test_send_cmd_failure_flag_raises(self):
        port = FakePort([pa1616s.sentence("PMTK001,220,2")] * 3)
        with self.assertRaises(pa1616s.GpsConfigError):
            pa1616s.send_cmd(port, "PMTK220,100", retries=1, timeout=0.05)

    def test_configure_cold_start_raises_baud(self):
        """Factory module at 9600: PMTK251 written, port reopened at
        115200, then rate + sentence mix ack-checked at the new baud."""
        ports = {}

        def open_port(dev, baud):
            if baud == 115200 and 9600 not in ports:
                return FakePort([b"\x00 garbage"])          # probe try 1
            if baud == 9600:
                ports[9600] = FakePort([_valid_rmc()])
                return ports[9600]
            ports[115200] = FakePort(
                [_valid_rmc(),
                 pa1616s.sentence("PMTK001,220,3"),
                 pa1616s.sentence("PMTK001,314,3")])
            return ports[115200]

        pa1616s.configure("/dev/x", open_port, log=lambda *a: None,
                          window=0.05)
        self.assertEqual(ports[9600].written,
                         [pa1616s.sentence(pa1616s.CMD_SET_BAUD)])
        self.assertEqual(
            ports[115200].written,
            [pa1616s.sentence(pa1616s.CMD_FIX_RATE_10HZ),
             pa1616s.sentence(pa1616s.CMD_SENTENCE_MIX)])
        self.assertTrue(ports[9600].closed)


class TestGeotagSidecar(unittest.TestCase):
    def test_bus_export_gets_geotag_sidecar(self):
        """GPS.md §4: every capture artifact gains a .meta.json stamp —
        with a null + reason when there is no fix (the test box's normal
        state), never a stale or invented position."""
        from kilodash import busmon
        m = busmon.BusMonitor(ring_max=10)
        m.ingest(1.0, 0x123, False, False, b"\x01")
        with tempfile.TemporaryDirectory() as d:
            _n, path = m.export(d, "can0")
            with open(path + ".meta.json") as f:
                meta = json.load(f)
        self.assertEqual(meta["artifact"], os.path.basename(path))
        self.assertIn("gps", meta)
        if meta["gps"] is None:
            self.assertTrue(meta["gps_reason"])


class TestSnapshotDaemon(unittest.TestCase):
    TPV = {"class": "TPV", "mode": 3, "status": 0,
           "time": "2027-01-15T12:00:00.000Z",
           "lat": 51.5, "lon": -0.12, "speed": 0.4, "track": 210.0,
           "altMSL": 33.0}
    SKY = {"class": "SKY", "hdop": 1.1, "satellites": [
        {"PRN": i, "used": i < 8, "az": 40 * i % 360, "el": 10 + i, "ss": 30}
        for i in range(11)]}

    def test_build_3d_fix(self):
        snap = snapshotd.build_snapshot(self.TPV, self.SKY, now=NOW)
        self.assertEqual(snap["fix"], "3d")
        self.assertEqual(snap["ts"], self.TPV["time"])   # GPS time wins
        self.assertEqual(snap["time_quality"], "gps")
        self.assertEqual(snap["alt_m"], 33.0)
        self.assertEqual(snap["sats_used"], 8)
        self.assertEqual(snap["sats_visible"], 11)

    def test_build_dgps(self):
        tpv = {**self.TPV, "status": 2}
        self.assertEqual(snapshotd.build_snapshot(tpv, self.SKY,
                                                  now=NOW)["fix"], "dgps")

    def test_build_no_fix_and_no_data(self):
        snap = snapshotd.build_snapshot({"class": "TPV", "mode": 1},
                                        None, now=NOW)
        self.assertEqual(snap["fix"], "none")
        self.assertIsNone(snap["lat"])
        self.assertEqual(snap["time_quality"], "unsynced")
        # ts must still parse and be "now" so the file reads as fresh no-fix
        self.assertAlmostEqual(snapshot.parse_ts(snap["ts"]), NOW, places=2)
        snap = snapshotd.build_snapshot(None, None, now=NOW)
        self.assertEqual(snap["fix"], "none")

    def test_stale_tpv_is_no_fix(self):
        snap = snapshotd.build_snapshot(self.TPV, self.SKY,
                                        tpv_age=10.0, now=NOW)
        self.assertEqual(snap["fix"], "none")
        self.assertIsNone(snap["lat"])

    def test_2d_fix_has_no_altitude(self):
        tpv = {**self.TPV, "mode": 2}
        snap = snapshotd.build_snapshot(tpv, self.SKY, now=NOW)
        self.assertEqual(snap["fix"], "2d")
        self.assertIsNone(snap["alt_m"])

    def test_atomic_write_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "position.json")
            snap = snapshotd.build_snapshot(self.TPV, self.SKY, now=NOW)
            snapshotd.write_snapshot(snap, path)
            self.assertEqual(os.listdir(d), ["position.json"])   # no .tmp
            got, reason = snapshot.read_position(
                path, now=snapshot.parse_ts(self.TPV["time"]) + 1)
            self.assertIsNone(reason)
            self.assertEqual(got["lat"], 51.5)

    def test_listener_feed_keeps_dop_only_sky_from_blanking(self):
        lst = GpsdListener()
        lst.feed(self.SKY, now=1.0)
        lst.feed({"class": "SKY", "hdop": 2.0}, now=2.0)   # DOP-only
        st = lst.state(now=2.0)
        self.assertEqual(len(st["sky"]["satellites"]), 11)
        lst.feed(self.TPV, now=3.0)
        self.assertEqual(lst.state(now=4.0)["tpv_age"], 1.0)


if __name__ == "__main__":
    unittest.main()
