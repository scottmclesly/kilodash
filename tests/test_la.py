"""Unit tests for the logic-analyzer safety core (kilodash/la.py).

Run from the repo root:  python -m unittest discover -s tests
Covers the capture/decode builders (exact argv), the capture-only reject
rules (defense in depth), the -O bits / -A annotation parsers, and the
capture-path validator. No third-party deps — stdlib unittest only.

The exact argv strings pin the design-time reference invocations; Phase 0
bench work re-verifies them verbatim against the real board and these tests
change together with la.py if reality differs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import la  # noqa: E402

OUT = f"{la.CAP_DIR}/la_test.sr"


class TestCaptureBuilder(unittest.TestCase):
    """§1 — the capture builder produces the exact expected arg array."""

    def test_basic_capture(self):
        self.assertEqual(
            la.build_capture_command(["D0"], "1m", 256, out_path=OUT),
            ["sigrok-cli", "--driver", "fx2lafw",
             "--config", "samplerate=1m", "--samples", "256",
             "--channels", "D0", "-o", OUT])

    def test_capture_with_trigger(self):
        self.assertEqual(
            la.build_capture_command(["D0", "D1"], "4m", 4096,
                                     trigger=("D0", "r"), out_path=OUT),
            ["sigrok-cli", "--driver", "fx2lafw",
             "--config", "samplerate=4m", "--samples", "4096",
             "--channels", "D0,D1", "--triggers", "D0=r", "-o", OUT])

    def test_multi_channel_sorted(self):
        cmd = la.build_capture_command(["D3", "D0"], "1m", 256, out_path=OUT)
        self.assertIn("D0,D3", cmd)

    def test_never_a_shell_string(self):
        cmd = la.build_capture_command(["D0"], "1m", 256, out_path=OUT)
        self.assertIsInstance(cmd, list)
        self.assertTrue(all(isinstance(a, str) for a in cmd))

    def test_default_out_path(self):
        cmd = la.build_capture_command(["D0"], "1m", 256)
        out = cmd[cmd.index("-o") + 1]
        self.assertTrue(out.startswith(la.CAP_DIR + "/"))
        self.assertTrue(out.endswith(".sr"))

    def test_unknown_samplerate_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_capture_command(["D0"], "3m", 256, out_path=OUT)

    def test_raw_int_samplerate_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_capture_command(["D0"], 1000000, 256, out_path=OUT)

    def test_unknown_sample_count_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_capture_command(["D0"], "1m", 5000, out_path=OUT)

    def test_empty_channels_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_capture_command([], "1m", 256, out_path=OUT)

    def test_unknown_channel_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_capture_command(["D8"], "1m", 256, out_path=OUT)
        with self.assertRaises(la.LaError):
            la.build_capture_command(["A0"], "1m", 256, out_path=OUT)

    def test_bad_edge_rejected(self):
        for edge in ("e", "both", "0", "1"):
            with self.assertRaises(la.LaError):
                la.build_capture_command(["D0"], "1m", 256,
                                         trigger=("D0", edge), out_path=OUT)

    def test_trigger_on_disabled_channel_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_capture_command(["D0"], "1m", 256,
                                     trigger=("D1", "r"), out_path=OUT)

    def test_out_path_escapes_rejected(self):
        for bad in ("/tmp/x.sr", f"{la.CAP_DIR}/../x.sr",
                    f"{la.CAP_DIR}/x.txt", "x.sr"):
            with self.assertRaises(la.LaError):
                la.build_capture_command(["D0"], "1m", 256, out_path=bad)


class TestDecodeBuilder(unittest.TestCase):
    """§2 — decode/bits read-back builders (two-step flow)."""

    def test_uart115200_exact(self):
        self.assertEqual(
            la.build_decode_command(OUT, "uart115200", ["D0"]),
            ["sigrok-cli", "-i", OUT,
             "-P", "uart:baudrate=115200:rx=D0", "-A", "uart"])

    def test_i2c_pin_autoassign(self):
        cmd = la.build_decode_command(OUT, "i2c", ["D2", "D0"])
        self.assertIn("i2c:scl=D0:sda=D2", cmd)

    def test_insufficient_channels_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_decode_command(OUT, "i2c", ["D0"])

    def test_none_preset_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_decode_command(OUT, "none", ["D0"])

    def test_unknown_preset_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_decode_command(OUT, "modbus", ["D0"])

    def test_bits_command_exact(self):
        self.assertEqual(la.build_bits_command(OUT),
                         ["sigrok-cli", "-i", OUT, "-O", "bits"])

    def test_bad_input_path_rejected(self):
        with self.assertRaises(la.LaError):
            la.build_decode_command("/etc/passwd", "uart115200", ["D0"])
        with self.assertRaises(la.LaError):
            la.build_bits_command("/etc/passwd")


class TestRejectList(unittest.TestCase):
    """§3 — capture-only, codified. Each rule proves the builder refuses if
    the flag ever reached the assembled args (defense in depth)."""

    def _assert_rejected(self, args):
        with self.assertRaises(la.LaError):
            la._enforce_rejects(args)

    def test_reject_driver_override(self):
        # demo is sigrok's *pattern generator* — the exact thing the
        # diagnostics-only scope forbids.
        self._assert_rejected(["sigrok-cli", "--driver", "demo",
                               "--samples", "256"])

    def test_reject_generator_config(self):
        self._assert_rejected(["sigrok-cli", "--driver", "fx2lafw",
                               "--config", "pattern=sigrok"])

    def test_reject_output_frequency_config(self):
        self._assert_rejected(["sigrok-cli", "--driver", "fx2lafw",
                               "--config", "output_frequency=1000"])

    def test_reject_set(self):
        self._assert_rejected(["sigrok-cli", "--driver", "fx2lafw", "--set"])

    def test_reject_continuous(self):
        self._assert_rejected(["sigrok-cli", "--driver", "fx2lafw",
                               "--continuous"])

    def test_reject_outfile_escape(self):
        self._assert_rejected(["sigrok-cli", "--driver", "fx2lafw",
                               "-o", "/tmp/x.sr"])
        self._assert_rejected(["sigrok-cli", "--driver", "fx2lafw",
                               "-o", f"{la.CAP_DIR}/../x.sr"])

    def test_allowed_argv_survives(self):
        la._enforce_rejects(
            ["sigrok-cli", "--driver", "fx2lafw",
             "--config", "samplerate=24m", "--samples", "4096",
             "--channels", "D0,D7", "--triggers", "D7=f", "-o", OUT])

    def test_capture_only_provably_unreachable(self):
        """No preset, over every builder, can ever emit a generator/output
        mode, a foreign driver, or a non-samplerate device config."""
        chans = list(la.CHANNELS)
        for preset in la.DECODER_PRESETS:
            cmds = [la.build_capture_command(chans, "24m", 4096,
                                             ("D0", "f"), OUT),
                    la.build_bits_command(OUT)]
            if preset["decoder"] is not None:
                cmds.append(la.build_decode_command(OUT, preset["key"], chans))
            for cmd in cmds:
                self.assertNotIn("--set", cmd)
                self.assertNotIn("--continuous", cmd)
                if "--driver" in cmd:
                    self.assertEqual(cmd[cmd.index("--driver") + 1], "fx2lafw")
                if "--config" in cmd:
                    cfg = cmd[cmd.index("--config") + 1]
                    self.assertTrue(cfg.startswith("samplerate="))


class TestParseBits(unittest.TestCase):
    def test_single_chunk(self):
        self.assertEqual(la.parse_bits("D0:1010"), {"D0": "1010"})

    def test_spaces_stripped(self):
        self.assertEqual(la.parse_bits("D0:10110010 01101100"),
                         {"D0": "1011001001101100"})

    def test_wrapped_chunks_concatenated(self):
        text = "D0:1111\nD1:0000\nD0:0000\nD1:1111\n"
        self.assertEqual(la.parse_bits(text),
                         {"D0": "11110000", "D1": "00001111"})

    def test_noise_ignored(self):
        text = "libsigrok warning\nD0:1010\n\nAcquisition done\n"
        self.assertEqual(la.parse_bits(text), {"D0": "1010"})

    def test_downsample_values(self):
        self.assertEqual(la.downsample_bits("0000", cols=2), [0, 0])
        self.assertEqual(la.downsample_bits("1111", cols=2), [1, 1])
        self.assertEqual(la.downsample_bits("0110", cols=2), [2, 2])
        self.assertEqual(la.downsample_bits("0011", cols=2), [0, 1])

    def test_downsample_length(self):
        self.assertEqual(len(la.downsample_bits("01" * 500, cols=220)), 220)

    def test_downsample_short_input(self):
        self.assertEqual(la.downsample_bits("10", cols=220), [1, 0])
        self.assertEqual(la.downsample_bits("", cols=220), [])


class TestParseAnnotations(unittest.TestCase):
    SAMPLE = ("uart-1: 55\nuart-1: AA\nuart-1: Frame error\n\n")

    def test_rows(self):
        rows = la.parse_annotations(self.SAMPLE)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], (1, "uart-1: 55", "fg"))

    def test_error_line_colored(self):
        rows = la.parse_annotations(self.SAMPLE)
        self.assertEqual(rows[2][2], "warn")

    def test_cap(self):
        rows = la.parse_annotations("x: 1\n" * (la.MAX_LINES + 50))
        self.assertEqual(len(rows), la.MAX_LINES)

    def test_empty(self):
        self.assertEqual(la.parse_annotations(""), [])


class TestPaths(unittest.TestCase):
    def test_capture_path_valid(self):
        self.assertTrue(la._valid_sr_path(la.capture_path()))

    def test_rejects(self):
        for bad in ("", "/opt/kilodash/captures/a b.sr",
                    "/opt/kilodash/captures/x.sr; reboot",
                    "/opt/kilodash/captures/$(x).sr",
                    "relative.sr", "/opt/kilodash/x.sr"):
            self.assertFalse(la._valid_sr_path(bad))


class TestCaptureJobRefusal(unittest.TestCase):
    """A bad request never spawns a process — the job refuses at construction
    and surfaces the reason as an output row (same UX as ScanJob)."""

    def test_refuses_bad_channels(self):
        job = la.CaptureJob([], "1m", 256, None, "none")
        self.assertTrue(job.done)
        self.assertIsNotNone(job.error)

    def test_refuses_underprovisioned_decoder(self):
        job = la.CaptureJob(["D0"], "1m", 256, None, "i2c")
        self.assertTrue(job.done)
        self.assertIn("I2C", job.error)


if __name__ == "__main__":
    unittest.main()
