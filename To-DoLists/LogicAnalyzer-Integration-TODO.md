# Logic Analyzer Integration — ToDo (for Claude Code)

**Goal:** integrate the Comimark **CY7C68013A-56 (EZ-USB FX2LP)** board as
kilodash's logic-analyzer capture device, driven by the *packaged* `sigrok-cli`,
surfaced as a hotplug device screen alongside CAN / I2C / Serial.

**Scope constraint (hard):** diagnostics only. The analyzer **captures and
decodes**; it never drives signals. fx2lafw is inherently capture-only, but the
command builder still enforces this as a positive allow-list so no future flag
or driver can smuggle in an output/generator mode.

**Target device reality:** this is a *bare* FX2LP dev board, **not** a
pre-flashed "8ch 24 MHz" stick. It has no LA firmware of its own — sigrok
uploads `fx2lafw` into its RAM on scan, and it re-enumerates. Its USB VID:PID is
**not assumed** anywhere below; Phase 0 records what it actually is.

**Specs to design against:** 8 digital channels (D0–D7), ≤24 MHz samplerate,
edge triggers only, no analog/ADC. Plenty for I2C / UART / NMEA0183-at-the-wire /
low-speed SPI on Scottina.

---

## Approach (gate hardware first, then plumb, then UI)

Do Phase 0 on the Pi **before writing any kilodash code** — a bare FX2LP board
that won't enumerate/soft-load is the single biggest bring-up risk, and every
later phase assumes a known-good capture invocation. Phases 3–4 reuse the
argv-command-builder and shared-primitives patterns already established by the
LAN-Scan refactor and the existing device screens.

---

## Phase 0 — Bench bring-up on the Pi (do this first, no repo changes)

- [x] Install the stack (Kali/Debian package names):
      `sudo apt install sigrok-cli sigrok-firmware-fx2lafw`
      *(`sigrok-firmware-fx2lafw` is **mandatory** — it's the firmware sigrok
      soft-loads into the bare board. `libsigrokdecode` + the decoder set come
      in as sigrok-cli deps.)*
- [x] Plug the board in, then `lsusb` and **write down the VID:PID**.
      **Recorded 2026-07-10: `04b4:8613`** (Cypress default bootloader ID,
      already first in `devices.py::FX2LA_IDS`).
      *(Expect the Cypress default `04b4:8613` for a blank/default EEPROM. Some
      modules ship with the EEPROM programmed to a clone ID instead — that's why
      we look instead of hardcoding. Record whatever you see; it feeds Phase 1
      and Phase 2.)*
- [x] `sigrok-cli --scan`
      *(Verified 2026-07-10: lists `fx2lafw - Cypress FX2` with D0-D15.)*
      *(This is what triggers the fx2lafw upload. First scan after each plug adds
      a ~1 s delay while the device re-enumerates. Success = it lists an
      `fx2lafw` logic analyzer with channels D0–D7. If it lists nothing: confirm
      the firmware package is installed, check `dmesg` for the re-enumeration,
      and confirm permissions — see Phase 1.)*
- [ ] Smoke test against a **known** signal. Easiest source is the Pi itself:
      drive one Pi GPIO with a square wave / PWM (or tap a UART TX), wire it to
      D0 + GND, then:
      `sigrok-cli --driver fx2lafw --config samplerate=1m --samples 256 --channels D0 -O bits`
      *(Eyeball the toggling. Mind levels: FX2LP inputs are 3.3 V logic — do NOT
      feed 5 V or anything 12 V-adjacent without a buffer/divider.)*
- [ ] Decode test — prove the payoff, not just edges:
      `sigrok-cli --driver fx2lafw --config samplerate=4m --samples 4096 --channels D0 -P uart:baudrate=115200:rx=D0 -A uart`
      *(Confirm you get decoded bytes, not just a waveform.)*
- [ ] Record the working invocation verbatim (driver name, samplerate syntax,
      channel names, decoder syntax). This becomes the command builder's
      reference output in Phase 3.

## Phase 1 — Non-root capture (udev)

The screen runs as the kilodash service user (`scott`), not root, so capture
must work without `sudo`.

- [x] Install sigrok's shipped udev rules rather than hand-rolling one — they
      already enumerate every LA ID **including the FX2 bootloader and all
      fx2lafw variants**, which covers the "ID changes across firmware upload"
      problem for free. Confirm the file exists (commonly
      `/lib/udev/rules.d/60-libsigrok.rules`, installed by the sigrok packages);
      if absent, pull it from the sigrok-util repo.
- [x] Ensure `scott` is in the group the rules grant (typically `plugdev`):
      `sudo usermod -aG plugdev scott` (log out/in to take effect).
- [x] Reload + replug: `sudo udevadm control --reload && sudo udevadm trigger`,
      then unplug/replug the board.
- [x] Verify: run the Phase 0 capture **as `scott`, no sudo**.
      *(Verified 2026-07-10: 4096-sample capture at 1 MHz to .sr as scott.)* Must succeed.
      *(If it only works with sudo, the rule didn't match the actual ID from
      Phase 0 — add that ID explicitly as a fallback rule.)*

## Phase 2 — Presence detection / tile gating

Match the existing hotplug pattern (`devices.py` drives tile visibility; RTL-SDR
/ CAN / etc. tiles appear only when their hardware is present).

- [x] Add FX2LP detection to `devices.py` using the same USB-id mechanism the
      other device screens use. **Match both** the bootloader ID and any
      post-load ID discovered in Phase 0 (a board that's been scanned once this
      session may be sitting in its fx2lafw-enumerated state).
      *(Keep it a cheap VID/PID check — do not poll `sigrok-cli --scan` on a
      timer; the scan-triggered firmware upload is too heavy for a liveness
      probe.)*
- [x] Gate the new LA screen's tile on that presence (grey/hide when unplugged),
      consistent with the "hardware required" metadata the launcher already uses.

## Phase 3 — Capture command builder (single source of truth + safety)

This is where the project's core safety principle lives — the builder is the
only thing that can express a capture, it emits an **argv array (never a shell
string)**, and its surface is a positive allow-list. Mirror the nmap /
LAN-Scan builder.

- [x] Build a `sigrok-cli` argv assembler. Inputs: driver (`fx2lafw`, fixed),
      samplerate, channel set (subset of D0–D7), capture size (`--samples` or
      `--time`), optional edge trigger (rising/falling on one channel), optional
      protocol decoder + options.
- [x] **Allow-list only** these capabilities: samplerate config, channel
      selection, sample/time limits, edge trigger, and the **read-only protocol
      decoders** (`uart`, `spi`, `i2c`, `can`, `onewire`, …) via `-P`/`-A`, plus
      output to a capture file.
- [x] **Reject-list / never-emit:** anything that drives outputs or reconfigures
      the device beyond capture. fx2lafw exposes no generator, but codify the
      rule so it survives a future device swap (carry forward the Pico
      pattern-generator note): the builder refuses to construct any
      output/generator/transmit mode at build time, not runtime.
- [x] Unit tests:
      - [x] builder returns a `list[str]` argv, never a string;
      - [x] a representative capture+decode call matches the exact argv from
            Phase 0;
      - [x] a disallowed request (e.g. an output/generator mode, or an
            unknown-driver override) raises at construction.

## Phase 4 — LA screen (native panel)

This is a subprocess+parse screen like CAN / I2C / Serial — use
`screens.base.Screen`, **not** `WebAppScreen` (there's no browser UI).

- [x] Subclass `Screen`, implement `draw_content` / `handle_tap`, add a `glyph`
      in `pictograms.py`, register in `screens/__init__.py::SCREENS`.
- [x] Controls, built from the **shared UI primitives** (don't re-invent):
      channel toggles D0–D7, samplerate selector, trigger picker
      (rising/falling + channel), decoder selector, Run/Stop, status badge.
- [x] Run the capture through the long-running-tool pattern (`system.Task` /
      the planned `Service`): spawn the argv from Phase 3, capture is bursty
      (one-shot), so render on completion rather than streaming at 20 Hz. Keep
      `tick_interval` at the default ~1.0; post results back to the main loop
      when the subprocess exits.
- [x] Output rendering:
      - **Primary:** decoded transaction list (like the candump list) from the
        `-A <decoder>` annotation output.
      - **Secondary:** a compact per-channel edge/bit strip for the captured
        window, for when you just want to see line activity.
- [x] Persist captures to `/opt/kilodash/captures/` as sigrok `.sr` sessions
      (`-o <name>.sr`), optionally CSV, so they can be pulled off-box over
      scp/SSH and reopened in PulseView on a laptop.

## Phase 5 — Install script + docs

- [x] Add the apt line (`sigrok-cli sigrok-firmware-fx2lafw`) and the udev/group
      step to the phase installer under `setup/` — idempotent, like
      `install-phase4.sh`.
- [x] README: add a device-table row — **Logic Analyzer | FX2LP (CY7C68013A) |
      passive multi-channel digital capture + protocol decode (I2C/SPI/UART/CAN),
      captures to `/opt/kilodash/captures/`**.
- [x] Document the **input-protection caveat** prominently: bare board, no input
      protection, **3.3 V logic only** — series resistor / buffer before probing
      anything near Scottina's 12 V wiring. Toasting a $10 board is cheap;
      backfeeding the Pi through it is not.

---

## Known gotchas (capture these in the screen/module docstring)

- **Firmware is soft-loaded every session.** `sigrok-firmware-fx2lafw` is not
  optional; without it the board is inert to sigrok. First `--scan` after each
  plug costs ~1 s while it uploads and re-enumerates.
- **USB ID is not fixed.** Blank EEPROM → `04b4:8613`; some modules ship with a
  clone ID programmed in. Phase 0 records the truth; nothing hardcodes it.
- **EEPROM is a rabbit hole you don't need.** For read-only LA use you never
  touch it. (If it's ever mis-programmed, `cycfx2prog` / Cypress tools can
  rewrite it — out of scope here.)
- **Ceilings:** 8 channels, 24 MHz, edge triggers only, no analog. Sustained
  high-rate depth is bounded by USB2 bandwidth. All fine for the target buses
  (sample ≥4× the bus clock: 400 kHz I2C and 115200 UART are trivial).

## Suggested first slice

Phase 0 + Phase 1 tomorrow when the board lands — pure bench work, no repo
changes, and it either proves the hardware end-to-end or tells you exactly
what's wrong before any kilodash code depends on it. Build Phase 3's argv
assembler next (it's testable headless with the board on the bench), then the
Phase 4 screen last.
