"""Logic analyzer safety core + sigrok-cli runner (FX2LP / fx2lafw).

Scope — DIAGNOSTICS ONLY. The analyzer *captures and decodes*; it never drives
signals. fx2lafw is inherently capture-only, but this builder enforces that as
a positive allow-list so no future flag or device swap can smuggle in an
output/generator/transmit mode (carrying forward the Pico pattern-generator
note): such a command is refused at *build* time, not runtime.
See LogicAnalyzer-Integration-TODO.md.

The guard-rail principle mirrors scan.py: every command is assembled here from
discrete validated intents (channels + samplerate + sample count + optional
edge trigger + optional decoder preset), emitted as an ARGUMENT ARRAY (never a
shell string), and _enforce_rejects() refuses the assembled command if anything
outside the allow-list slipped in (defense in depth).

Known gotchas (from bench reality, Phase 0):
  * Firmware is soft-loaded every session — the bare FX2LP board is inert to
    sigrok without the sigrok-firmware-fx2lafw package; the first scan after
    each plug costs ~1 s while the firmware uploads and it re-enumerates.
  * The USB ID is not fixed: blank EEPROM -> 04b4:8613, some modules ship a
    clone ID. devices.py matches the known set; Phase 0 records the truth.
  * Ceilings: 8 channels (D0-D7), 24 MHz, edge triggers only, no analog.
  * 3.3 V logic only — the bare board has no input protection.
"""

import os
import re
import subprocess
import threading
import time

# ------------------------------------------------------------ allow-lists ----
# These tuples ARE the safety boundary: the builders refuse anything not in
# them, and the UI is built from them, so nothing else is expressible.
DRIVER = "fx2lafw"                      # fixed; never user-selectable
CHANNELS = tuple(f"D{i}" for i in range(8))
# Emitted verbatim into --config samplerate=<label> (sigrok accepts k/m).
SAMPLERATES = ("20k", "100k", "200k", "500k",
               "1m", "2m", "4m", "8m", "12m", "16m", "24m")
SAMPLE_COUNTS = (256, 1024, 4096, 16384, 65536, 262144, 1048576)
SAMPLE_LABELS = ("256", "1k", "4k", "16k", "64k", "256k", "1M")
EDGES = ("r", "f")                      # rising / falling — edge triggers only

# Read-only protocol decoder presets. Pins auto-assign to the LOWEST enabled
# channels in listed order (e.g. I2C with D0+D2 on -> scl=D0, sda=D2). Every
# -P option value is a fixed string from this table — no user text ever
# reaches the decoder spec.
DECODER_PRESETS = (
    {"key": "none",       "label": "no decode",   "decoder": None,
     "pins": (), "opts": ()},
    {"key": "uart115200", "label": "UART 115200", "decoder": "uart",
     "pins": ("rx",), "opts": (("baudrate", "115200"),)},
    {"key": "uart9600",   "label": "UART 9600",   "decoder": "uart",
     "pins": ("rx",), "opts": (("baudrate", "9600"),)},
    {"key": "i2c",        "label": "I2C",         "decoder": "i2c",
     "pins": ("scl", "sda"), "opts": ()},
    {"key": "spi",        "label": "SPI",         "decoder": "spi",
     "pins": ("clk", "mosi"), "opts": ()},
    {"key": "can",        "label": "CAN 500k",    "decoder": "can",
     "pins": ("rx",), "opts": (("nominal_bitrate", "500000"),)},
)

CAP_DIR = "/opt/kilodash/captures"
MAX_LINES = 400          # cap retained output rows to protect Pi memory
CAPTURE_TIMEOUT = 120    # hard ceiling: a trigger that never fires can't hang
STRIP_COLS = 220         # downsampled bit-strip columns (~2 px/col on screen)

# Flags the builder must never emit and must actively refuse. --set writes
# device configuration beyond capture; --continuous breaks the one-shot model.
_REJECT_EXACT = frozenset({"--set", "--continuous"})


class LaError(Exception):
    """Raised when a capture cannot be safely assembled or is refused."""


# --------------------------------------------------------------- validation --
def _valid_sr_path(p):
    """Capture files live in CAP_DIR, plain basename, .sr extension — used for
    both -o (capture) and -i (decode read-back) so the builder can only ever
    touch its own capture directory."""
    return bool(re.fullmatch(rf"{CAP_DIR}/[A-Za-z0-9_\-]+\.sr", p or ""))


def capture_path():
    """Timestamped .sr path under CAP_DIR."""
    return f"{CAP_DIR}/la_{time.strftime('%Y%m%d-%H%M%S')}.sr"


def _normalize_channels(channels):
    """Validate + dedupe + sort a channel subset; raise on empty/unknown."""
    chs = list(dict.fromkeys(channels or ()))
    for c in chs:
        if c not in CHANNELS:
            raise LaError(f"unknown channel: {c!r}")
    if not chs:
        raise LaError("no channels selected")
    return sorted(chs, key=CHANNELS.index)


def _preset(key):
    for p in DECODER_PRESETS:
        if p["key"] == key:
            return p
    raise LaError(f"unknown decoder preset: {key!r}")


def _enforce_rejects(args):
    """Defense in depth: refuse the assembled command if anything could drive
    outputs or reconfigure the device beyond capture. The builders can't emit
    these; this catches a value arriving from anywhere else."""
    it = iter(range(len(args)))
    for i in it:
        a = args[i]
        if a in _REJECT_EXACT:
            raise LaError(f"rejected non-capture flag: {a}")
        if a in ("--driver", "-d"):
            if i + 1 >= len(args) or args[i + 1] != DRIVER:
                # e.g. the demo driver is sigrok's *pattern generator* —
                # exactly what the diagnostics-only scope forbids.
                raise LaError("rejected driver override")
        if a in ("--config", "-c"):
            # samplerate is the ONLY device option we ever set. This is what
            # makes generator/pattern modes (pattern=…, output_frequency=…,
            # amplitude=…) unexpressible even after a device swap.
            val = args[i + 1] if i + 1 < len(args) else ""
            key = val.split("=", 1)[0]
            if key != "samplerate":
                raise LaError(f"rejected device config: {val}")
        if a in ("-o", "--output-file", "-i", "--input-file"):
            val = args[i + 1] if i + 1 < len(args) else ""
            if not _valid_sr_path(val):
                raise LaError(f"rejected capture path: {val}")


# ---------------------------------------------------------------- builders ---
def build_capture_command(channels, samplerate, samples, trigger=None,
                          out_path=None):
    """Assemble a one-shot capture as an ARGUMENT ARRAY (never a shell string,
    so there is nothing to inject into). channels: subset of D0-D7;
    samplerate: label from SAMPLERATES; samples: value from SAMPLE_COUNTS;
    trigger: optional (channel, edge) with the channel among the enabled set
    and edge in EDGES; out_path: .sr file under CAP_DIR (default: timestamped).
    Raises LaError on anything outside the allow-lists.

    Reference invocation pinned by tests/test_la.py; Phase 0 bench work
    re-verifies it verbatim against the real board.
    """
    chs = _normalize_channels(channels)
    if samplerate not in SAMPLERATES:
        raise LaError(f"unknown samplerate: {samplerate!r}")
    if samples not in SAMPLE_COUNTS:
        raise LaError(f"unknown sample count: {samples!r}")
    out = out_path or capture_path()
    if not _valid_sr_path(out):
        raise LaError(f"invalid capture path: {out!r}")

    args = ["sigrok-cli", "--driver", DRIVER,
            "--config", f"samplerate={samplerate}",
            "--samples", str(samples),
            "--channels", ",".join(chs)]
    if trigger is not None:
        t_ch, t_edge = trigger
        if t_ch not in chs:
            raise LaError(f"trigger channel {t_ch!r} not enabled")
        if t_edge not in EDGES:
            raise LaError(f"unknown trigger edge: {t_edge!r}")
        args += ["--triggers", f"{t_ch}={t_edge}"]
    args += ["-o", out]
    _enforce_rejects(args)
    return args


def build_decode_command(sr_path, preset_key, channels):
    """Second step of the two-step flow (sigrok-cli cannot emit a session file
    and annotations in one run): decode annotations FROM the saved .sr file.
    Preset pins auto-assign to the lowest enabled channels."""
    if not _valid_sr_path(sr_path):
        raise LaError(f"invalid capture path: {sr_path!r}")
    preset = _preset(preset_key)
    dec = preset["decoder"]
    if dec is None:
        raise LaError("preset has no decoder")
    chs = _normalize_channels(channels)
    if len(chs) < len(preset["pins"]):
        raise LaError(f"{preset['label']} needs {len(preset['pins'])} "
                      f"channel(s), {len(chs)} enabled")
    spec = dec
    for k, v in preset["opts"]:
        spec += f":{k}={v}"
    for pin, ch in zip(preset["pins"], chs):
        spec += f":{pin}={ch}"
    args = ["sigrok-cli", "-i", sr_path, "-P", spec, "-A", dec]
    _enforce_rejects(args)
    return args


def build_bits_command(sr_path):
    """Third step: raw bit dump from the .sr file for the edge-strip render."""
    if not _valid_sr_path(sr_path):
        raise LaError(f"invalid capture path: {sr_path!r}")
    args = ["sigrok-cli", "-i", sr_path, "-O", "bits"]
    _enforce_rejects(args)
    return args


# --------------------------------------------------------- output parsing ----
_BITS_RE = re.compile(r"^(D\d):([01 ]+)$")


def parse_bits(text):
    """Parse `-O bits` output into {channel: '0101…'}. sigrok prints wrapped
    chunks like 'D0:10110010 01101100 …', repeating each channel per chunk;
    concatenate them in order."""
    bits = {}
    for line in text.splitlines():
        m = _BITS_RE.match(line.strip())
        if m:
            bits.setdefault(m.group(1), "")
            bits[m.group(1)] += m.group(2).replace(" ", "")
    return bits


def downsample_bits(bitstr, cols=STRIP_COLS):
    """Reduce a bit string to `cols` buckets: 0 = low throughout, 1 = high
    throughout, 2 = edge/activity inside the bucket. This is what the screen
    draws as the compact per-channel strip."""
    n = len(bitstr)
    if n == 0:
        return []
    cols = min(cols, n)
    out = []
    for i in range(cols):
        seg = bitstr[i * n // cols:(i + 1) * n // cols] or bitstr[-1]
        if "0" in seg and "1" in seg:
            out.append(2)
        else:
            out.append(1 if seg[0] == "1" else 0)
    return out


def parse_annotations(text):
    """Parse `-A <decoder>` output into (indent, text, color) rows. Kept
    deliberately tolerant — the exact annotation format is the softest
    assumption here (Phase 0 confirms it), so a surprise degrades to plain
    rows, never a crash."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        color = "warn" if ("error" in low or "warning" in low) else "fg"
        rows.append((1, line[:58], color))
        if len(rows) >= MAX_LINES:
            break
    return rows


# ------------------------------------------------------------- capture job ---
class CaptureJob:
    """Run one capture end-to-end in a background thread; poll .done and read
    .lines / .bits / .status each tick. Capture is bursty (one-shot), so
    nothing streams — results post on completion.

    Three sequential subprocesses, all argv from the builders above:
      A) capture to .sr   (always persisted — pull over scp, open in PulseView)
      B) decode from .sr  (skipped for the 'no decode' preset)
      C) bit dump from .sr for the per-channel edge strip
    Capture success and decode failure are reported independently: the .sr
    file survives even when B/C fail.
    """

    def __init__(self, channels, samplerate, samples, trigger, preset_key):
        self.lines = []                 # (indent, text, color) rows
        self.bits = {}                  # {channel: [0|1|2, …]} strip buckets
        self.sr_path = None
        self.done = False
        self.error = None
        self.status = "Starting…"
        self._lock = threading.Lock()
        self._proc = None
        self._stopped = False
        self._preset_key = preset_key
        self._channels = None

        try:
            self._channels = _normalize_channels(channels)
            self.sr_path = capture_path()
            self.cmd = build_capture_command(self._channels, samplerate,
                                             samples, trigger, self.sr_path)
            _preset(preset_key)         # validate before anything runs
            if preset_key != "none":
                # fail fast on e.g. I2C with one channel enabled
                build_decode_command(self.sr_path, preset_key, self._channels)
        except LaError as e:
            self._refuse(str(e))
            return
        self._wait_msg = ("Waiting for trigger…" if trigger
                          else "Capturing…")
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    # ---- public control ----
    def stop(self):
        self._stopped = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def snapshot(self):
        """Thread-safe copy of the current lines for rendering."""
        with self._lock:
            return list(self.lines)

    # ---- internals ----
    def _refuse(self, msg, status=None):
        self.error = msg
        self.status = status or f"Refused: {msg}"
        self._add(0, msg, "bad")
        self.done = True

    def _add(self, indent, text, color):
        with self._lock:
            self.lines.append((indent, text, color))
            if len(self.lines) > MAX_LINES:
                del self.lines[0:len(self.lines) - MAX_LINES]

    def _exec(self, cmd):
        """Run one builder argv; return (ok, stdout). Populates self.error on
        the not-installed / failed cases."""
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            out, err = self._proc.communicate(timeout=CAPTURE_TIMEOUT)
        except FileNotFoundError:
            return False, "sigrok-cli not installed"
        except subprocess.TimeoutExpired:
            try:
                self._proc.terminate()
                self._proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            return False, "timed out"
        except OSError as e:
            return False, str(e)
        if self._stopped:
            return False, "stopped"
        if self._proc.returncode != 0:
            # "No devices found" is the common failure when the board vanished
            tail = (err or out or "failed").strip().splitlines()
            return False, (tail[-1] if tail else "failed")[:58]
        return True, out

    def _run(self):
        os.makedirs(CAP_DIR, exist_ok=True)

        # A — capture to .sr
        self.status = self._wait_msg
        ok, out = self._exec(self.cmd)
        if not ok:
            if self._stopped:
                self._finish(status="Stopped")
            else:
                self._finish(error=out, status=out[:34])
            return

        # B — decode annotations (optional)
        if self._preset_key != "none":
            self.status = "Decoding…"
            try:
                cmd = build_decode_command(self.sr_path, self._preset_key,
                                           self._channels)
            except LaError as e:
                cmd, ok, out = None, False, str(e)
            if cmd:
                ok, out = self._exec(cmd)
            if self._stopped:
                self._finish(status="Stopped")
                return
            if ok:
                rows = parse_annotations(out)
                label = _preset(self._preset_key)["label"]
                self._add(0, f"{label} — {len(rows)} annotation(s)", "accent")
                for r in rows:
                    self._add(*r)
            else:
                # decode failed but the capture itself is safe on disk
                self._add(0, f"decode failed: {out}", "bad")

        # C — bit dump for the edge strip
        self.status = "Reading bits…"
        ok, out = self._exec(build_bits_command(self.sr_path))
        if self._stopped:
            self._finish(status="Stopped")
            return
        if ok:
            self.bits = {ch: downsample_bits(b)
                         for ch, b in parse_bits(out).items()}

        self._finish(status=f"Done · {os.path.basename(self.sr_path)}")

    def _finish(self, status=None, error=None):
        if error and not self.error:
            self.error = error
            self._add(0, error, "bad")
        if status:
            self.status = status
        self.done = True
