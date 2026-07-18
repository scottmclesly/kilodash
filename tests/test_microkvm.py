"""Unit tests for the Micro KVM command plane (microkvm/, MICROKVM-PROTOCOL.md).

Run from the repo root:  python -m unittest discover -s tests
Covers the executor against a fake frame source (registry round-trip, every
rejection path, the independent reject pass, arm-gate refusal, idempotency of
every action verb, reboot ack-then-act ordering) and the arm-gate's debounce
and home-identification rules. No radio, no BLE, no subprocess — stdlib only.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from microkvm import armgate, executor, link, registry  # noqa: E402


# ---------------------------------------------------------------- fakes -----
class FakeInfo:
    def metric(self, name):
        return {"uptime": "3h04m", "temp": "47.2", "mem": "34",
                "disk": "21", "load": "0.42", "wifi": "BoatLAN/72"}[name]

    def services(self):
        return {"kilodash": "up", "signalk": "down"}


class FakeProc:
    def __init__(self, pid=812):
        self.pid = pid
        self.terminated = False

    def poll(self):
        return 1 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class FakeTimer:
    """Records scheduling; fires only when the test says so."""
    instances = []

    def __init__(self, delay, fn, args):
        self.delay, self.fn, self.args = delay, fn, args
        self.started = False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def fire(self):
        self.fn(*self.args)


def make_executor(armed=True, **kw):
    """Executor wired to fakes; armed by default so action verbs dispatch."""
    FakeTimer.instances = []
    runs = []

    def run(argv, timeout=30):
        runs.append(list(argv))
        return 0, "active"

    ex = executor.Executor(
        armed_fn=lambda: (armed, "home host 10.0.0.1 unreachable" if armed
                          else "home network reachable"),
        info=FakeInfo(),
        request_tile_fn=lambda name: True,
        active_tile_fn=lambda: "home",
        link_fn=lambda: {"rssi": -104, "snr": 8.5},
        tiles={"home", "nmea2k", "lanscan"},
        popen=lambda argv, stdout=None, stderr=None: FakeProc(),
        run=run,
        timer_factory=FakeTimer,
        capture_dir=kw.pop("capture_dir", tempfile.mkdtemp()),
        **kw)
    ex._test_runs = runs
    return ex


# ------------------------------------------------------------- round trip ---
class TestReadOnlyVerbs(unittest.TestCase):
    def test_status(self):
        ex = make_executor()
        self.assertEqual(
            ex.handle("status"),
            "status: up 3h04m, 47.2C, tile=home, armed=yes, rssi=-104/8.5")

    def test_health(self):
        ex = make_executor(armed=False)
        self.assertEqual(
            ex.handle("health"),
            "health: svcs kilodash=up signalk=down, disk 21%, mem 34%, "
            "temp 47.2C, armed=no")

    def test_snap_every_metric(self):
        ex = make_executor()
        self.assertEqual(ex.handle("snap temp"), "snap: temp=47.2")
        self.assertEqual(ex.handle("snap uptime"), "snap: uptime=3h04m")
        self.assertEqual(ex.handle("snap wifi"), "snap: wifi=BoatLAN/72")

    def test_mixed_case_and_whitespace_folded(self):
        ex = make_executor()
        self.assertEqual(ex.handle("  Snap   TEMP "), "snap: temp=47.2")

    def test_read_only_answers_while_disarmed(self):
        ex = make_executor(armed=False)
        self.assertTrue(ex.handle("status").startswith("status: up"))

    def test_broken_info_provider_degrades_to_question_marks(self):
        ex = make_executor()
        ex._info = None
        self.assertIn("?C", ex.handle("status"))

    def test_reply_is_one_line_within_cap(self):
        ex = make_executor()
        for frame in ("status", "health", "snap disk"):
            reply = ex.handle(frame)
            self.assertNotIn("\n", reply)
            self.assertLessEqual(len(reply), executor.REPLY_MAX)


class TestHelpMenu(unittest.TestCase):
    """BBS-style menu: list choices so syntax is never guessed. Read-only, so
    it answers while disarmed too."""

    def test_menu_lists_every_verb(self):
        ex = make_executor(armed=False)
        reply = ex.handle("help")
        for verb in ("status", "health", "snap", "tile", "cap", "svc",
                     "reboot", "help"):
            self.assertIn(verb, reply)
        self.assertIn("help <verb>", reply)

    def test_menu_aliases(self):
        ex = make_executor()
        self.assertEqual(ex.handle("?"), ex.handle("help"))
        self.assertEqual(ex.handle("menu"), ex.handle("help"))

    def test_help_verb_lists_its_domain(self):
        ex = make_executor()
        self.assertIn("temp", ex.handle("help snap"))
        self.assertIn("mem", ex.handle("help snap"))
        svc = ex.handle("help svc")
        self.assertIn("kilodash", svc)
        self.assertIn("signalk", svc)
        tile = ex.handle("help tile")
        self.assertIn("nmea2k", tile)

    def test_help_shows_class_and_hint(self):
        ex = make_executor()
        self.assertIn("read-only", ex.handle("help status"))
        self.assertIn("action", ex.handle("help reboot"))

    def test_help_unknown_verb_points_back_to_menu(self):
        ex = make_executor()
        reply = ex.handle("help frobnicate")
        self.assertIn("no verb", reply)
        self.assertIn("status", reply)      # falls back to the verb list

    def test_help_answers_while_disarmed(self):
        ex = make_executor(armed=False)
        self.assertTrue(ex.handle("help").startswith("verbs:"))

    def test_unknown_verb_suggests_help(self):
        ex = make_executor()
        self.assertIn("send help", ex.handle("wat"))

    def test_all_help_replies_within_airtime_cap(self):
        ex = make_executor()
        frames = ["help", "?", "menu"] + [f"help {v}" for v in ex.registry]
        for f in frames:
            reply = ex.handle(f)
            self.assertLessEqual(len(reply), executor.REPLY_MAX,
                                 f"{f!r} -> {len(reply)} chars: {reply}")
            self.assertNotIn("\n", reply)


class TestActionVerbs(unittest.TestCase):
    def test_tile(self):
        ex = make_executor()
        self.assertEqual(ex.handle("tile nmea2k"), "tile: active=nmea2k")

    def test_tile_idempotent(self):
        ex = make_executor()
        self.assertEqual(ex.handle("tile nmea2k"), ex.handle("tile nmea2k"))

    def test_svc_restart_argv_exact(self):
        ex = make_executor()
        self.assertEqual(ex.handle("svc restart kilodash"),
                         "svc: restarted kilodash state=active")
        self.assertEqual(ex._test_runs[0],
                         ["systemctl", "restart", "kilodash.service"])
        self.assertEqual(ex._test_runs[1],
                         ["systemctl", "is-active", "kilodash.service"])

    def test_svc_restart_send_twice_is_safe_repeat(self):
        ex = make_executor()
        r1 = ex.handle("svc restart kilodash")
        r2 = ex.handle("svc restart kilodash")
        self.assertEqual(r1, r2)

    def test_cap_start_stop_and_idempotency(self):
        ex = make_executor()
        self.assertEqual(ex.handle("cap start can"),
                         "cap: running target=can pid=812")
        # second start: same process, flagged, never a second spawn
        self.assertEqual(ex.handle("cap start can"),
                         "cap: running target=can pid=812 (already)")
        self.assertEqual(ex.handle("cap stop can"), "cap: stopped target=can")
        self.assertEqual(ex.handle("cap stop can"),
                         "cap: stopped target=can (was not running)")

    def test_reboot_ack_then_act(self):
        ex = make_executor()
        reply = ex.handle("reboot")
        self.assertEqual(reply, "reboot: scheduled in 15s")
        t = FakeTimer.instances[0]
        self.assertTrue(t.started)
        self.assertEqual(t.delay, registry.REBOOT_DELAY_S)
        # the reply exists but nothing has run yet — ack precedes act
        self.assertEqual(ex._test_runs, [])
        t.fire()
        self.assertEqual(ex._test_runs, [["systemctl", "reboot"]])

    def test_reboot_idempotent(self):
        ex = make_executor()
        ex.handle("reboot")
        self.assertEqual(ex.handle("reboot"), "reboot: already scheduled")
        self.assertEqual(len(FakeTimer.instances), 1)


# -------------------------------------------------------------- rejections --
class TestRejections(unittest.TestCase):
    def test_unknown_verb(self):
        ex = make_executor()
        self.assertEqual(ex.handle("frobnicate"),
                         "reject: unknown-verb 'frobnicate' (send help)")

    def test_unknown_verb_echo_sanitized_and_capped(self):
        ex = make_executor()
        reply = ex.handle("x" * 100 + "\x07\x1b")
        self.assertEqual(reply,
                         f"reject: unknown-verb '{'x' * 24}' (send help)")

    def test_empty_frame(self):
        ex = make_executor()
        self.assertEqual(ex.handle(""), "reject: unknown-verb ''")
        self.assertEqual(ex.handle(None), "reject: unknown-verb ''")

    def test_bad_arity(self):
        ex = make_executor()
        self.assertEqual(ex.handle("status now"),
                         "status: reject bad-arity want=0 got=1")
        self.assertEqual(ex.handle("snap"),
                         "snap: reject bad-arity want=1 got=0")

    def test_bad_arg(self):
        ex = make_executor()
        self.assertEqual(ex.handle("snap voltage"),
                         "snap: reject bad-arg metric='voltage'")
        self.assertEqual(ex.handle("svc restart sshd"),
                         "svc: reject bad-arg name='sshd'")
        self.assertEqual(ex.handle("tile doom"),
                         "tile: reject bad-arg name='doom'")

    def test_every_action_verb_gated_while_disarmed(self):
        ex = make_executor(armed=False)
        for frame, verb in (("tile home", "tile"),
                            ("cap start can", "cap"),
                            ("svc restart kilodash", "svc"),
                            ("reboot", "reboot")):
            self.assertEqual(
                ex.handle(frame),
                f"{verb}: reject disarmed (home network reachable)")
        self.assertEqual(ex._test_runs, [])      # nothing dispatched

    def test_free_form_is_unexpressible(self):
        """No token with shell metacharacters survives the domain check."""
        ex = make_executor()
        self.assertIn("reject bad-arg", ex.handle("svc restart 'kilodash;id'"))
        self.assertIn("reject", ex.handle("snap $(reboot)"))


class TestIndependentRejectPass(unittest.TestCase):
    """A registry edit that widens a domain by mistake trips _enforce, not a
    subprocess — the scan.py defense-in-depth pattern (§5)."""

    def _widen(self, ex, verb_name, **changes):
        v = ex.registry[verb_name]
        ex.registry[verb_name] = registry.Verb(
            name=v.name, klass=v.klass,
            args=changes.get("args", v.args),
            argv=changes.get("argv", v.argv), func=v.func)

    def test_widened_domain_with_shell_metachar_is_refused(self):
        ex = make_executor()
        bad = "kilodash;rm"
        self._widen(ex, "svc", args=(registry.Arg("op", registry.SVC_OPS),
                                     registry.Arg("name", frozenset({bad}))))
        self.assertEqual(ex.handle(f"svc restart {bad}"),
                         "svc: reject enforce (arg name)")
        self.assertEqual(ex._test_runs, [])

    def test_non_allowlisted_binary_is_refused(self):
        ex = make_executor()
        self._widen(ex, "reboot", argv=("rm", "-rf", "/"))
        self.assertEqual(ex.handle("reboot"),
                         "reboot: reject enforce (binary not allow-listed)")
        self.assertEqual(ex._test_runs, [])


# ------------------------------------------------------------- session log --
class TestSessionLog(unittest.TestCase):
    def test_log_records_sender_frame_reply_verdict(self):
        ex = make_executor()
        ex.handle("snap temp", sender="!a1b2c3d4")
        ex.handle("nope", sender="!a1b2c3d4")
        ok, bad = ex.log[0], ex.log[1]
        self.assertEqual((ok["sender"], ok["line"], ok["ok"]),
                         ("!a1b2c3d4", "snap temp", True))
        self.assertFalse(bad["ok"])

    def test_log_is_ring_buffered(self):
        ex = make_executor()
        for i in range(executor.LOG_LINES + 10):
            ex.handle("status")
        self.assertEqual(len(ex.log), executor.LOG_LINES)


# ------------------------------------------------------------- link gating --
class TestLinkFilter(unittest.TestCase):
    """filter_frame is the sender-node-ID gate (§6): PSK is the crypto
    boundary upstream; this narrows within the trusted channel. Unknown node
    => dropped silently, never dispatched, never answered."""

    ALLOWED = {"!a1b2c3d4"}

    def _pkt(self, sender="!a1b2c3d4", channel=1, text="status",
             portnum="TEXT_MESSAGE_APP"):
        return {"fromId": sender, "channel": channel,
                "decoded": {"portnum": portnum, "text": text}}

    def test_allowed_command_frame_passes(self):
        self.assertEqual(link.filter_frame(self._pkt(), 1, self.ALLOWED),
                         ("!a1b2c3d4", "status"))

    def test_unknown_node_is_dropped(self):
        sender, why = link.filter_frame(self._pkt(sender="!deadbeef"),
                                        1, self.ALLOWED)
        self.assertIsNone(sender)
        self.assertIn("not on allow-list", why)

    def test_wrong_channel_is_dropped(self):
        sender, _ = link.filter_frame(self._pkt(channel=0), 1, self.ALLOWED)
        self.assertIsNone(sender)

    def test_non_text_port_is_dropped(self):
        sender, _ = link.filter_frame(self._pkt(portnum="TELEMETRY_APP"),
                                      1, self.ALLOWED)
        self.assertIsNone(sender)

    def test_missing_sender_or_empty_text_is_dropped(self):
        self.assertIsNone(link.filter_frame(self._pkt(sender=""),
                                            1, self.ALLOWED)[0])
        self.assertIsNone(link.filter_frame(self._pkt(text="  "),
                                            1, self.ALLOWED)[0])
        self.assertIsNone(link.filter_frame({}, 1, self.ALLOWED)[0])
        self.assertIsNone(link.filter_frame(None, 1, self.ALLOWED)[0])


# ---------------------------------------------------------------- arm gate --
def make_gate(results, ssids=None, **kw):
    """Gate whose reachability (and optionally SSID) probes replay lists."""
    seq = list(results)
    ssids = list(ssids) if ssids is not None else None

    def reach(host):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def ssid():
        return ssids.pop(0) if ssids and len(ssids) > 1 else \
            (ssids[0] if ssids else "BoatLAN")

    return armgate.ArmGate(home_host="10.0.0.1", ssid_fn=ssid,
                           reach_fn=reach, **kw)


class TestArmGate(unittest.TestCase):
    def test_home_present_stays_dormant(self):
        g = make_gate([True], need=2)
        for _ in range(5):
            self.assertFalse(g.poll())
        self.assertEqual(g.transitions, [])

    def test_home_absent_arms_after_debounce(self):
        g = make_gate([False], need=3)
        self.assertFalse(g.poll())      # 1st disagreeing probe
        self.assertFalse(g.poll())      # 2nd
        self.assertTrue(g.poll())       # 3rd: flip
        self.assertEqual(len(g.transitions), 1)
        armed, reason = g.state()
        self.assertTrue(armed)
        self.assertIn("unreachable", reason)

    def test_flapping_link_does_not_thrash(self):
        g = make_gate([False, True, False, True, False, True], need=3)
        for _ in range(6):
            g.poll()
        self.assertFalse(g.armed)
        self.assertEqual(g.transitions, [])

    def test_lookalike_ssid_without_gateway_reads_armed(self):
        """Captive portal / evil twin: SSID matches home, gateway does not
        answer — must arm (gotcha list: identify home by reachability)."""
        g = armgate.ArmGate(home_ssid="BoatLAN", home_host="10.0.0.1",
                            need=2, ssid_fn=lambda: "BoatLAN",
                            reach_fn=lambda h: False)
        g.poll()
        self.assertTrue(g.poll())

    def test_reachable_gateway_with_foreign_ssid_reads_armed(self):
        g = armgate.ArmGate(home_ssid="BoatLAN", home_host="10.0.0.1",
                            need=2, ssid_fn=lambda: "CoffeeShop",
                            reach_fn=lambda h: True)
        g.poll()
        self.assertTrue(g.poll())

    def test_unconfigured_home_never_arms(self):
        g = armgate.ArmGate(home_host="", need=1,
                            ssid_fn=lambda: "", reach_fn=lambda h: False)
        for _ in range(10):
            self.assertFalse(g.poll())
        self.assertEqual(g.reason, "home identity unconfigured")

    def test_recovery_disarms_after_debounce(self):
        g = make_gate([False, False, False, True], need=3)
        for _ in range(3):
            g.poll()
        self.assertTrue(g.armed)
        for _ in range(3):
            g.poll()
        self.assertFalse(g.armed)
        self.assertEqual(len(g.transitions), 2)


if __name__ == "__main__":
    unittest.main()
