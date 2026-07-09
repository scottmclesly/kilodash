"""Unit tests for the CanTick bridge core (kilodash/cantick.py).

Run from the repo root:  python -m unittest discover -s tests
Covers the §1 command builders (exact argv, pinned to PROTOCOL.md), the
bitrate->-s map, the §4 CTK1| framing (CRC-16/CCITT-FALSE, base64 creds,
reply parsing + CRC rejection), the §2 heartbeat parsing / freshness /
rx-rate / drop-rising / contract-version warning, the config-block merge,
and the §5 AP config generation + injection guards. No third-party deps —
stdlib unittest only, nothing touches the network or spawns a process.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import cantick  # noqa: E402


class TestBuilders(unittest.TestCase):
    """§1 — exact reference invocations, as argument lists."""

    def test_socat_reference_invocation(self):
        self.assertEqual(
            cantick.build_socat_command(29536),
            ["socat", "TCP-LISTEN:29536,reuseaddr",
             "PTY,link=/dev/cantick0,raw,echo=0"])

    def test_slcand_reference_invocation(self):
        self.assertEqual(
            cantick.build_slcand_command(250000, "slcan0"),
            ["slcand", "-o", "-c", "-s5", "/dev/cantick0", "slcan0"])

    def test_bitrate_codes(self):
        self.assertEqual(cantick.SLCAN_BITRATE_CODES[250000], 5)
        self.assertEqual(cantick.SLCAN_BITRATE_CODES[500000], 6)
        self.assertEqual(cantick.SLCAN_BITRATE_CODES[1000000], 8)

    def test_never_a_shell_string(self):
        for cmd in (cantick.build_socat_command(29536),
                    cantick.build_slcand_command(250000, "slcan0")):
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(a, str) for a in cmd))

    def test_rejects_bad_inputs(self):
        with self.assertRaises(cantick.CanTickError):
            cantick.build_slcand_command(300000, "slcan0")   # not a -s code
        with self.assertRaises(cantick.CanTickError):
            cantick.build_slcand_command(250000, "eth0")     # not slcanN
        with self.assertRaises(cantick.CanTickError):
            cantick.build_slcand_command(250000, "slcan0; rm -rf /")
        with self.assertRaises((cantick.CanTickError, ValueError)):
            cantick.build_socat_command("29536,fork")        # not an int
        with self.assertRaises(cantick.CanTickError):
            cantick.build_socat_command(0)


class TestCrcAndFraming(unittest.TestCase):
    """§4 — CRC-16/CCITT-FALSE and the CTK1| frame grammar."""

    def test_crc_check_value(self):
        # the standard CRC-16/CCITT-FALSE check value
        self.assertEqual(cantick.crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_crc_empty(self):
        self.assertEqual(cantick.crc16_ccitt_false(b""), 0xFFFF)

    def test_frame_layout(self):
        f = cantick.frame("COMMIT")
        self.assertTrue(f.startswith(b"CTK1|COMMIT|CRC="))
        self.assertTrue(f.endswith(b"\n"))
        crc = cantick.crc16_ccitt_false(b"CTK1|COMMIT")
        self.assertEqual(f, b"CTK1|COMMIT|CRC=%04X\n" % crc)

    def test_frame_rejects_delimiters_in_body(self):
        for bad in ("a|b", "a\nb", "a\rb"):
            with self.assertRaises(cantick.CanTickError):
                cantick.frame(bad)

    def test_parse_reply_bare(self):
        kind, fields = cantick.parse_reply(b"ACK slot=primary\n")
        self.assertEqual(kind, "ACK")
        self.assertEqual(fields, {"slot": "primary"})

    def test_parse_reply_framed_good_crc(self):
        line = cantick.frame("STATUS prov=1 mode=listen")
        kind, fields = cantick.parse_reply(line)
        self.assertEqual(kind, "STATUS")
        self.assertEqual(fields, {"prov": "1", "mode": "listen"})

    def test_parse_reply_framed_bad_crc(self):
        kind, fields = cantick.parse_reply(b"CTK1|ACK|CRC=0000\n")
        self.assertIsNone(kind)
        self.assertEqual(fields, {})

    def test_parse_reply_garbage(self):
        self.assertEqual(cantick.parse_reply(b""), (None, {}))
        self.assertEqual(cantick.parse_reply(b"CTK1|"), (None, {}))

    def test_parse_reply_real_firmware(self):
        # verbatim reply observed from fw 0.1.0 on the bench: pipe-separated
        # fields, framed but with NO CRC trailer on replies
        line = (b"CTK1|STATUS|name=cantick-000000|fw=0.1.0|wifi=connected"
                b"|ip=192.168.0.71|prov=1\n")
        kind, fields = cantick.parse_reply(line)
        self.assertEqual(kind, "STATUS")
        self.assertEqual(fields, {"name": "cantick-000000", "fw": "0.1.0",
                                  "wifi": "connected", "ip": "192.168.0.71",
                                  "prov": "1"})

    def test_set_creds_body_base64(self):
        body = cantick.set_creds_body("primary", "MyBoat", "s3cret pw")
        self.assertEqual(body,
                         "SET_CREDS slot=primary ssid=TXlCb2F0 "
                         "psk=czNjcmV0IHB3")
        # raw secrets never appear in the wire body
        self.assertNotIn("s3cret", body)

    def test_set_creds_slot_validated(self):
        with self.assertRaises(cantick.CanTickError):
            cantick.set_creds_body("both", "x", "y")
        with self.assertRaises(cantick.CanTickError):
            cantick.set_creds_body("primary", "", "y")

    def test_set_net_body(self):
        self.assertEqual(cantick.set_net_body(250000, False),
                         "SET_NET bitrate=250000 listen_only=0")
        self.assertEqual(cantick.set_net_body(500000, True),
                         "SET_NET bitrate=500000 listen_only=1")
        with self.assertRaises(cantick.CanTickError):
            cantick.set_net_body(123, False)


class TestHeartbeat(unittest.TestCase):
    """§2 — datagram parsing, freshness, rx/s, drop-rising, version check."""

    def hb(self):
        return cantick.HeartbeatListener(port=0)

    @staticmethod
    def gram(**kw):
        import json
        d = {"name": "ct1", "fw": "1.0", "bitrate": 250000, "mode": "normal",
             "rx": 100, "tx": 1, "drop": 0, "rssi": -60, "v": 1}
        d.update(kw)
        return json.dumps(d).encode()

    def test_parse_and_fresh(self):
        hb = self.hb()
        hb._handle(self.gram())
        self.assertTrue(hb.is_fresh("ct1"))
        rec = hb.latest()
        self.assertEqual(rec["name"], "ct1")
        self.assertEqual(rec["mode"], "normal")
        self.assertEqual(rec["rssi"], -60)
        self.assertTrue(rec["fresh"])
        self.assertIsNone(hb.version_warning)

    def test_stale_after_window(self):
        hb = self.hb()
        hb._handle(self.gram())
        hb._devices["ct1"]["seen"] -= cantick.FRESH_SECS + 1
        self.assertFalse(hb.is_fresh("ct1"))
        self.assertFalse(hb.latest()["fresh"])

    def test_rx_rate_between_heartbeats(self):
        hb = self.hb()
        hb._handle(self.gram(rx=100))
        hb._devices["ct1"]["seen"] -= 2.0        # pretend 2 s passed
        hb._handle(self.gram(rx=300))
        self.assertAlmostEqual(hb.latest()["rx_rate"], 100.0, delta=5.0)

    def test_rx_counter_reset_is_not_negative(self):
        hb = self.hb()
        hb._handle(self.gram(rx=1000))
        hb._devices["ct1"]["seen"] -= 2.0
        hb._handle(self.gram(rx=5))              # device rebooted
        self.assertEqual(hb.latest()["rx_rate"], 0.0)

    def test_drop_rising(self):
        hb = self.hb()
        hb._handle(self.gram(drop=0))
        self.assertFalse(hb.latest()["drop_rising"])
        hb._handle(self.gram(drop=3))
        self.assertTrue(hb.latest()["drop_rising"])

    def test_version_mismatch_warns_but_keeps_running(self):
        hb = self.hb()
        hb._handle(self.gram(v=2))
        self.assertIn("v2", hb.version_warning)
        self.assertTrue(hb.is_fresh("ct1"))      # §7: link keeps running

    def test_garbage_datagrams_ignored(self):
        hb = self.hb()
        hb._handle(b"not json")
        hb._handle(b"[1,2,3]")
        self.assertIsNone(hb.latest())

    def test_unnamed_device_gets_default(self):
        hb = self.hb()
        hb._handle(b'{"v": 1}')
        self.assertEqual(hb.latest()["name"], "cantick")


class TestConfigBlock(unittest.TestCase):
    """§6 — merged defaults survive a partially-saved block."""

    class FakeConfig(dict):
        def set(self, k, v):
            self[k] = v

    def test_defaults_when_missing(self):
        blk = cantick.block(self.FakeConfig())
        self.assertEqual(blk["tcp_port"], 29536)
        self.assertEqual(blk["hb_port"], 29537)
        self.assertEqual(blk["bitrate"], 250000)
        self.assertEqual(blk["fallback_ap_ssid"], "Scottina-CanTick")
        self.assertEqual(blk["expected_contract_version"], 1)

    def test_partial_block_merged(self):
        cfg = self.FakeConfig(cantick={"bitrate": 500000})
        blk = cantick.block(cfg)
        self.assertEqual(blk["bitrate"], 500000)
        self.assertEqual(blk["tcp_port"], 29536)  # default filled in

    def test_fallback_psk_generated_once(self):
        cfg = self.FakeConfig()
        psk1 = cantick.ensure_fallback_psk(cfg)
        psk2 = cantick.ensure_fallback_psk(cfg)
        self.assertEqual(psk1, psk2)
        self.assertGreaterEqual(len(psk1), 20)
        self.assertEqual(cfg["cantick"]["fallback_psk"], psk1)


class TestApConfig(unittest.TestCase):
    """§5 — generated configs and the config-injection guards."""

    def ap(self):
        return cantick.CanTickAP("Scottina-CanTick", "supersecret1",
                                 gateway="192.168.42.1")

    def test_hostapd_conf(self):
        conf = self.ap().hostapd_conf()
        self.assertIn("interface=wlan0", conf)
        self.assertIn("ssid=Scottina-CanTick", conf)
        self.assertIn("wpa=2", conf)
        self.assertIn("wpa_passphrase=supersecret1", conf)

    def test_dnsmasq_conf(self):
        conf = self.ap().dnsmasq_conf()
        self.assertIn("interface=wlan0", conf)
        self.assertIn("dhcp-range=192.168.42.10,192.168.42.100", conf)
        self.assertIn("address=/scottina.local/192.168.42.1", conf)

    def test_never_touches_wlan1(self):
        self.assertEqual(cantick.CanTickAP.IFACE, "wlan0")
        for conf in (self.ap().hostapd_conf(), self.ap().dnsmasq_conf()):
            self.assertNotIn("wlan1", conf)

    def test_newline_injection_rejected(self):
        with self.assertRaises(cantick.CanTickError):
            cantick.CanTickAP("evil\nssid", "supersecret1")
        with self.assertRaises(cantick.CanTickError):
            cantick.CanTickAP("ok", "bad\npsk4567")

    def test_psk_length_enforced(self):
        with self.assertRaises(cantick.CanTickError):
            cantick.CanTickAP("ok", "short")
        with self.assertRaises(cantick.CanTickError):
            cantick.CanTickAP("ok", "x" * 64)

    def test_bad_gateway_rejected(self):
        with self.assertRaises(Exception):
            cantick.CanTickAP("ok", "supersecret1", gateway="not-an-ip")


class TestLinkSafety(unittest.TestCase):
    """The interface-manager only holds the fixed §1 argv — nothing
    device-supplied can reach a command line."""

    def test_link_pins_reference_argv(self):
        link = cantick.CanTickLink(iface="slcan0", tcp_port=29536,
                                   bitrate=250000)
        self.assertEqual(link._socat_cmd,
                         cantick.build_socat_command(29536))
        self.assertEqual(link._slcand_cmd,
                         cantick.build_slcand_command(250000, "slcan0"))

    def test_link_rejects_bad_config(self):
        with self.assertRaises(cantick.CanTickError):
            cantick.CanTickLink(iface="wlan0")
        with self.assertRaises(cantick.CanTickError):
            cantick.CanTickLink(bitrate=999)

    def test_stop_when_never_started_is_safe(self):
        link = cantick.CanTickLink()
        link.stop()                              # idempotent no-op
        self.assertEqual(link.status()["state"], link.STOPPED)
        self.assertFalse(cantick.link_active())


if __name__ == "__main__":
    unittest.main()
