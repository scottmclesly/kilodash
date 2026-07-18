"""Unit tests for the GNSS source node (n2k/node.py + n2k/fastpacket_tx.py).

Run from the repo root:  python -m unittest discover -s tests
Covers the fast-packet TX splitter round-tripped against our own RX
reassembler (kilodash/n2k.py — the two independent implementations
validate each other), 129029 decoded back through the table-driven field
extractor, arbitration-id building cross-checked against the RX splitter,
NAME construction, the ISO address-claim state machine (win, lose→move,
defense, exhaustion→cannot-claim, ISO-request answers), SA persistence,
the PGN schedule and the auto-stop-on-fix-loss gating. No sockets, no
clock: everything injected.
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import n2k as rx  # noqa: E402  (the RX side of the round-trip)
from n2k import fastpacket_tx, node  # noqa: E402

GNSS_TABLE = {
    "pgn": 129029, "name": "GNSS Position Data", "fast": True, "fields": [
        {"name": "SID", "bit_offset": 0, "bit_length": 8, "resolution": 1,
         "offset": 0, "signed": False, "units": "", "lookup": None},
        {"name": "Latitude", "bit_offset": 56, "bit_length": 64,
         "resolution": 1e-16, "offset": 0, "signed": True, "units": "deg",
         "lookup": None},
        {"name": "Longitude", "bit_offset": 120, "bit_length": 64,
         "resolution": 1e-16, "offset": 0, "signed": True, "units": "deg",
         "lookup": None},
        {"name": "HDOP", "bit_offset": 272, "bit_length": 16,
         "resolution": 0.01, "offset": 0, "signed": True, "units": "",
         "lookup": None},
    ]}

FIX = {"epoch": 1_800_000_000.0, "lat": 51.5001, "lon": -0.1201,
       "sog_mps": 2.5, "cog_deg": 210.0, "alt_m": 33.0, "method": 1,
       "sats": 9, "hdop": 1.1, "pdop": 1.9, "geoidal_sep_m": 47.0}


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class TestFastPacketRoundTrip(unittest.TestCase):
    def test_all_lengths_reassemble(self):
        asm = rx.FastPacketAssembler()
        for n in (1, 5, 6, 7, 13, 42, 100, 222, 223):
            payload = bytes(range(256))[:n] if n <= 256 else b""
            payload = (b"\x5A" * n) if not payload else payload
            frames = fastpacket_tx.split(payload, seq=3)
            got = None
            for f in frames:
                self.assertEqual(len(f), 8)
                got = asm.feed((129029, 0x42), f)
            self.assertEqual(got, payload, f"len {n}")

    def test_seq_rotates_and_reassembler_tracks_restart(self):
        tx = fastpacket_tx.FastPacketTx()
        asm = rx.FastPacketAssembler()
        seqs = set()
        for i in range(10):
            payload = bytes([i]) * 20
            frames = tx.frames(129029, payload)
            seqs.add(frames[0][0] >> 5)
            for f in frames:
                got = asm.feed((129029, 1), f)
            self.assertEqual(got, payload)
        self.assertGreater(len(seqs), 4)        # counter actually rotates

    def test_bad_lengths_raise(self):
        for bad in (b"", b"x" * 224):
            with self.assertRaises(ValueError):
                fastpacket_tx.split(bad, 0)

    def test_dropped_middle_frame_never_reaches_decode(self):
        frames = fastpacket_tx.split(b"\x11" * 40, seq=1)
        asm = rx.FastPacketAssembler()
        self.assertIsNone(asm.feed((1, 1), frames[0]))
        # frame 1 lost; frame 2 arrives — partial must be discarded
        self.assertIsNone(asm.feed((1, 1), frames[2]))
        self.assertEqual(asm.dropped, 1)


class TestEncoders(unittest.TestCase):
    def test_gnss_pgn_decodes_back_through_the_table(self):
        payload = node.encode_gnss(7, FIX["epoch"], FIX["lat"], FIX["lon"],
                                   FIX["alt_m"], 1, 9, 1.1, 1.9, 47.0)
        self.assertEqual(len(payload), 43)
        dec = rx.Decoder({129029: GNSS_TABLE})
        rec = None
        for f in fastpacket_tx.split(payload, 0):
            rec = dec.feed(1.0, node.can_id(3, 129029, 0x42), True, f)
        self.assertIsNotNone(rec)
        by = {fl["name"]: fl["value"] for fl in rec["fields"]}
        self.assertAlmostEqual(by["Latitude"], FIX["lat"], places=9)
        self.assertAlmostEqual(by["Longitude"], FIX["lon"], places=9)
        self.assertAlmostEqual(by["HDOP"], 1.1, places=6)

    def test_position_rapid(self):
        lat, lon = struct.unpack(
            "<ii", node.encode_position_rapid(51.5001, -0.1201))
        self.assertAlmostEqual(lat * 1e-7, 51.5001, places=6)
        self.assertAlmostEqual(lon * 1e-7, -0.1201, places=6)

    def test_cog_sog_sentinels(self):
        raw = node.encode_cog_sog(1, None, None)
        _sid, _ref, cog, sog = struct.unpack("<BBHH", raw[:6])
        self.assertEqual((cog, sog), (0xFFFF, 0xFFFF))
        raw = node.encode_cog_sog(1, 180.0, 2.5)
        _sid, _ref, cog, sog = struct.unpack("<BBHH", raw[:6])
        self.assertAlmostEqual(cog * 1e-4, 3.14159, places=3)
        self.assertEqual(sog, 250)

    def test_system_time_epoch_split(self):
        raw = node.encode_system_time(1, 1_800_000_000.0)
        _sid, _src, days, tod = struct.unpack("<BBHI", raw)
        self.assertEqual(days * 86400 + tod * 1e-4, 1_800_000_000.0)

    def test_can_id_cross_checked_against_rx_splitter(self):
        for pgn, da in ((60928, 0xFF), (59904, 0x42), (129025, 0xFF)):
            cid = node.can_id(6, pgn, 0x91, da)
            got_pgn, got_src, got_prio = rx.split_id(cid)
            self.assertEqual((got_pgn, got_src, got_prio), (pgn, 0x91, 6))

    def test_name_fields(self):
        name = node.build_name(0x12345)
        self.assertEqual(name & 0x1FFFFF, 0x12345)
        self.assertEqual((name >> 21) & 0x7FF, node.MFR_CODE)
        self.assertEqual((name >> 40) & 0xFF, 145)      # Function: GNSS
        self.assertEqual((name >> 49) & 0x7F, 60)       # Class: Navigation
        self.assertEqual((name >> 60) & 0x7, 4)         # Marine
        self.assertEqual(name >> 63, 1)                 # arbitrary-capable


class TestClaimMachine(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sent = []
        self._saved = []
        self._orig_save = node.save_preferred_sa
        node.save_preferred_sa = lambda sa, path=None: self._saved.append(sa)
        self.addCleanup(setattr, node, "save_preferred_sa", self._orig_save)
        self.core = node.NodeCore(
            tx=lambda cid, data: self.sent.append((cid, bytes(data))),
            identity=0x1000, preferred_sa=42, now=self.clock)

    def claims(self):
        return [(rx.split_id(cid)[1], data) for cid, data in self.sent
                if rx.split_id(cid)[0] == node.PGN_ADDR_CLAIM]

    def rival_claim_frame(self, sa, name):
        return node.can_id(6, node.PGN_ADDR_CLAIM, sa), \
            name.to_bytes(8, "little")

    def test_clean_claim(self):
        self.core.activate()
        self.assertEqual(self.core.state, node.CLAIMING)
        self.assertEqual(self.claims(), [(42, self.core.name.to_bytes(
            8, "little"))])
        self.clock.t += 0.3
        self.assertTrue(self.core.poll())
        self.assertEqual(self.core.state, node.ACTIVE)
        self.assertEqual(self._saved, [42])            # SA persisted

    def test_lower_name_wins_we_move(self):
        self.core.activate()
        self.core.on_frame(*self.rival_claim_frame(42, self.core.name - 1))
        self.assertEqual(self.core.sa, 43)             # moved
        self.assertEqual(self.claims()[-1][0], 43)     # and re-claimed
        self.clock.t += 0.3
        self.core.poll()
        self.assertEqual(self.core.state, node.ACTIVE)

    def test_higher_name_loses_we_defend(self):
        self.core.activate()
        self.clock.t += 0.3
        self.core.poll()
        n = len(self.claims())
        self.core.on_frame(*self.rival_claim_frame(42, self.core.name + 1))
        self.assertEqual(self.core.sa, 42)             # held our ground
        self.assertEqual(len(self.claims()), n + 1)    # by re-claiming

    def test_exhaustion_is_cannot_claim(self):
        self.core.activate()
        for _ in range(node.SA_LIMIT + 1):
            self.core.on_frame(
                *self.rival_claim_frame(self.core.sa, self.core.name - 1))
            if self.core.state == node.CANNOT_CLAIM:
                break
        self.assertEqual(self.core.state, node.CANNOT_CLAIM)
        self.assertEqual(self.claims()[-1][0], node.NULL_SA)
        # cannot-claim means silent: no PGN TX
        self.core.tx_due(FIX)
        self.assertEqual(len([1 for cid, _ in self.sent
                              if rx.split_id(cid)[0] == 129025]), 0)

    def test_iso_request_answered_any_time(self):
        self.core.activate()
        self.clock.t += 0.3
        self.core.poll()
        n = len(self.claims())
        req = int(node.PGN_ADDR_CLAIM).to_bytes(3, "little")
        self.core.on_frame(node.can_id(6, node.PGN_ISO_REQUEST, 0x05,
                                       da=42), req)
        self.assertEqual(len(self.claims()), n + 1)
        # requests for a PGN we don't serve are ignored
        self.core.on_frame(node.can_id(6, node.PGN_ISO_REQUEST, 0x05,
                                       da=42), (126996).to_bytes(3, "little"))
        self.assertEqual(len(self.claims()), n + 1)
        # …as are requests addressed to someone else
        self.core.on_frame(node.can_id(6, node.PGN_ISO_REQUEST, 0x05,
                                       da=0x33), req)
        self.assertEqual(len(self.claims()), n + 1)

    def test_own_echo_is_not_a_rival(self):
        self.core.activate()
        self.core.on_frame(*self.rival_claim_frame(42, self.core.name))
        self.assertEqual(self.core.sa, 42)
        self.assertEqual(len(self.claims()), 1)        # no defense either


class TestSchedulerAndFixGate(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sent = []
        self._orig_save = node.save_preferred_sa
        node.save_preferred_sa = lambda sa, path=None: None
        self.addCleanup(setattr, node, "save_preferred_sa", self._orig_save)
        self.core = node.NodeCore(
            tx=lambda cid, data: self.sent.append((cid, bytes(data))),
            identity=0x1000, preferred_sa=42, now=self.clock)
        self.core.activate()
        self.clock.t += 0.3
        self.core.poll()
        self.sent.clear()

    def by_pgn(self):
        out = {}
        for cid, _ in self.sent:
            out[rx.split_id(cid)[0]] = out.get(rx.split_id(cid)[0], 0) + 1
        return out

    def test_rates_over_two_seconds(self):
        t0 = self.clock.t
        while self.clock.t < t0 + 2.0:
            self.core.tx_due(FIX)
            self.clock.t += 0.05
        got = self.by_pgn()
        self.assertEqual(got[129025], 20)              # 10 Hz
        self.assertEqual(got[129026], 20)
        self.assertEqual(got[126992], 2)               # 1 Hz
        self.assertEqual(got[126993], 1)               # 60 s: just the first
        self.assertEqual(got[129029], 2 * 7)           # 1 Hz × 7 fp frames

    def test_fix_loss_stops_tx_and_keeps_address(self):
        self.core.tx_due(FIX)
        self.assertTrue(self.sent)
        self.sent.clear()
        self.core.fix_lost()
        self.assertEqual(self.core.state, node.STOPPED_FIX)
        self.clock.t += 5
        self.core.tx_due(FIX)
        self.assertEqual(self.sent, [])                # silent while stopped
        # …but the claim is still defended (address kept for quick resume)
        self.core.on_frame(node.can_id(6, node.PGN_ADDR_CLAIM, 42),
                           (self.core.name + 1).to_bytes(8, "little"))
        self.assertTrue(self.sent)
        self.sent.clear()
        self.core.fix_restored()
        self.assertEqual(self.core.state, node.ACTIVE)
        self.core.tx_due(FIX)
        self.assertTrue(self.by_pgn().get(129025))

    def test_gnss_43_bytes_needs_7_frames(self):
        self.core.tx_due(FIX)
        frames = [d for cid, d in self.sent
                  if rx.split_id(cid)[0] == 129029]
        self.assertEqual(len(frames), 7)               # 6 + 6×7 ≥ 43


class TestSaPersistence(unittest.TestCase):
    def test_round_trip_and_default(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state", "n2k_sa.json")
            self.assertEqual(node.load_preferred_sa(p), node.DEFAULT_SA)
            node.save_preferred_sa(37, p)
            self.assertEqual(node.load_preferred_sa(p), 37)
            with open(p, "w") as f:
                f.write("{corrupt")
            self.assertEqual(node.load_preferred_sa(p), node.DEFAULT_SA)


if __name__ == "__main__":
    unittest.main()
