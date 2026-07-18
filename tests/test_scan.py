"""Unit tests for the LAN Scan safety core (kilodash/scan.py).

Run from the repo root:  python -m unittest discover -s tests
Covers §1 (builder output per mode), §2 (reject-list per flag), and the
target/ports validators. No third-party deps — stdlib unittest only.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import scan  # noqa: E402


class TestAllowedModes(unittest.TestCase):
    """§1 — each allowed mode produces the exact expected arg array."""

    def test_discover(self):
        self.assertEqual(
            scan.build_scan_command("Discover", "192.168.1.0/24"),
            ["nmap", "-sn", "192.168.1.0/24"])

    def test_ports_default(self):
        self.assertEqual(
            scan.build_scan_command("Ports", "192.168.1.5"),
            ["nmap", "-sT", "-p", scan.COMMON_PORTS, "192.168.1.5"])

    def test_ports_explicit(self):
        self.assertEqual(
            scan.build_scan_command("Ports", "192.168.1.5", "22,80,443"),
            ["nmap", "-sT", "-p", "22,80,443", "192.168.1.5"])

    def test_services(self):
        self.assertEqual(
            scan.build_scan_command("Services", "host.local"),
            ["nmap", "-sT", "-sV", "-p", scan.COMMON_PORTS,
             "--host-timeout", scan.HOST_TIMEOUT, "host.local"])

    def test_identify(self):
        self.assertEqual(
            scan.build_scan_command("Identify", "10.0.0.1"),
            ["nmap", "-sT", "-O", "-p", scan.COMMON_PORTS,
             "--host-timeout", scan.HOST_TIMEOUT, "10.0.0.1"])

    def test_never_a_shell_string(self):
        cmd = scan.build_scan_command("Services", "192.168.1.5")
        self.assertIsInstance(cmd, list)
        self.assertTrue(all(isinstance(a, str) for a in cmd))

    def test_unknown_mode_rejected(self):
        with self.assertRaises(scan.ScanError):
            scan.build_scan_command("Vuln", "192.168.1.5")


class TestRejectList(unittest.TestCase):
    """§2 — one test per rejected flag. Each proves the builder refuses if the
    flag ever reached the assembled args (defense in depth)."""

    def _assert_rejected(self, flag):
        with self.assertRaises(scan.ScanError):
            scan._enforce_rejects(["nmap", flag, "192.168.1.5"])

    def test_reject_script(self):        # NSE — top priority
        self._assert_rejected("--script")

    def test_reject_script_with_args(self):
        self._assert_rejected("--script=http-enum")

    def test_reject_sc(self):
        self._assert_rejected("-sC")

    def test_reject_ss(self):
        self._assert_rejected("-sS")

    def test_reject_sf(self):
        self._assert_rejected("-sF")

    def test_reject_sx(self):
        self._assert_rejected("-sX")

    def test_reject_null_scan(self):
        self._assert_rejected("-sN")

    def test_reject_aggressive(self):
        self._assert_rejected("-A")

    def test_reject_decoy(self):
        self._assert_rejected("-D")

    def test_reject_source_spoof(self):
        self._assert_rejected("-S")

    def test_reject_spoof_mac(self):
        self._assert_rejected("--spoof-mac")

    def test_reject_fragment(self):
        self._assert_rejected("-f")

    def test_reject_mtu(self):
        self._assert_rejected("--mtu")

    def test_reject_data_length(self):
        self._assert_rejected("--data-length")

    def test_reject_t4(self):
        self._assert_rejected("-T4")

    def test_reject_t5(self):
        self._assert_rejected("-T5")

    def test_nse_provably_unreachable(self):
        """No allowed mode, over every field, can ever emit NSE."""
        for mode in scan.MODES:
            cmd = scan.build_scan_command(mode, "192.168.1.5",
                                          "22,80" if mode == "Ports" else None)
            self.assertNotIn("--script", cmd)
            self.assertNotIn("-sC", cmd)
            self.assertNotIn("-A", cmd)

    def test_allowed_flags_survive(self):
        """Our own -sn / -sT / -sV are NOT confused with rejected variants."""
        scan._enforce_rejects(["nmap", "-sn", "-sT", "-sV", "-O", "-p", "80"])


class TestTargetValidation(unittest.TestCase):
    def test_accepts_ipv4(self):
        self.assertTrue(scan._valid_target("192.168.1.5"))

    def test_accepts_cidr(self):
        self.assertTrue(scan._valid_target("10.0.0.0/8"))

    def test_accepts_hostname(self):
        self.assertTrue(scan._valid_target("router.local"))

    def test_rejects_flag_lookalike(self):
        self.assertFalse(scan._valid_target("-sS"))

    def test_rejects_shell_injection(self):
        self.assertFalse(scan._valid_target("192.168.1.5; rm -rf /"))
        self.assertFalse(scan._valid_target("$(reboot)"))
        self.assertFalse(scan._valid_target("a b"))

    def test_rejects_empty(self):
        self.assertFalse(scan._valid_target(""))

    def test_builder_rejects_bad_target(self):
        with self.assertRaises(scan.ScanError):
            scan.build_scan_command("Discover", "192.168.1.5; rm -rf /")


class TestPortsValidation(unittest.TestCase):
    def test_accepts_list(self):
        self.assertTrue(scan._valid_ports("22,80,443"))

    def test_accepts_range(self):
        self.assertTrue(scan._valid_ports("8000-8100"))

    def test_rejects_letters(self):
        self.assertFalse(scan._valid_ports("22,http"))

    def test_rejects_injection(self):
        self.assertFalse(scan._valid_ports("80;reboot"))

    def test_builder_rejects_bad_ports(self):
        with self.assertRaises(scan.ScanError):
            scan.build_scan_command("Ports", "192.168.1.5", "80;reboot")


class TestParseNmap(unittest.TestCase):
    SAMPLE = (
        "Starting Nmap 7.94\n"
        "Nmap scan report for router.local (192.168.1.1)\n"
        "Host is up (0.0021s latency).\n"
        "PORT   STATE SERVICE VERSION\n"
        "22/tcp open  ssh     OpenSSH 8.4\n"
        "80/tcp open  http    nginx 1.18.0\n"
        "MAC Address: AA:BB:CC:DD:EE:FF (Acme)\n"
        "Nmap scan report for 192.168.1.9\n"
        "Host is up (0.0100s latency).\n"
        "443/tcp closed https\n"
        "Nmap done: 2 IP addresses (2 hosts up)\n"
    )

    def test_two_hosts(self):
        hosts = scan.parse_nmap(self.SAMPLE)
        self.assertEqual(len(hosts), 2)

    def test_first_host_fields(self):
        h = scan.parse_nmap(self.SAMPLE)[0]
        self.assertEqual(h["ip"], "192.168.1.1")
        self.assertEqual(h["host"], "router.local")
        self.assertTrue(h["up"])
        self.assertEqual(len(h["ports"]), 2)
        self.assertEqual(h["ports"][0]["service"], "ssh")
        self.assertEqual(h["ports"][0]["version"], "OpenSSH 8.4")

    def test_closed_port_captured(self):
        h = scan.parse_nmap(self.SAMPLE)[1]
        self.assertEqual(h["ip"], "192.168.1.9")
        self.assertEqual(h["ports"][0]["state"], "closed")


if __name__ == "__main__":
    unittest.main()
