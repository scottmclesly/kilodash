# GPS Integration — ToDo (for Claude Code)

> **STATUS 2026-07-18: executed.** All software phases landed and are live
> on the box (udev + pa1616s + gpsd + chrony + snapshotd installed and
> running; module bench-verified at 115200/10 Hz with acks; tile on the
> Home grid; N2K node + TX allow-list + comparison merged; 305 unit tests
> green). Open: the **outdoor fix-acquisition gate below** (needs sky
> view — record TTFF in the README when done), bench-validating the N2K
> node against a real bus, and the Light-side ack of the DOCK-PROTOCOL
> v1.1 `gps` quality flag. Deviation: GSV runs every **5th** fix, not
> 10th — the MTK divisor field caps at 5 (see gps/pa1616s.py docstring).

**Goal:** integrate the Adafruit Ultimate GPS (PA1616S, 66ch, 10 Hz) on a
PL2303 TTL-USB dongle as ecosystem plumbing first, tile second: **gpsd +
chrony** make Scottina Prime a disconnected time authority; a **position
snapshot contract** geotags every capture artifact on the box; a **GPS tile**
shows constellation/fix health; and a **proper NMEA2000 node** (full ISO
address claim) can source GNSS PGNs onto the bus from a button on the NMEA2K
tile.

**Scope constraint (hard):** diagnostics only. CAN TX exception now covers
two things, both stated explicitly: (1) heartbeat/reply behavior required by
bus participation, and (2) the **GNSS source node** in this document —
address claim, claim defense, ISO request responses, and the five GNSS PGNs
listed in Phase 3, started and stopped only by an explicit user action. No
injection, replay, fuzzing, or arbitrary-frame TX is expressible anywhere in
the UI or command builders. Enforced in code (TX allow-list + AST scan +
independent reject pass), not by convention.

**Hardware facts (verified live, 2026-07-18):**
- Module answers at 9600 factory default; valid NMEA, checksums good.
- **No backup battery installed** → every boot is a cold start at factory
  defaults. Baud/rate config must be re-applied every open. (Probe logic
  below still handles the battery-installed future.)
- Dongle is PL2303 (`067b:2303`) with **generic serial string — no unique
  ID**. Hotplug identity must pin by physical USB port path.
- GPS dongle lives in USB port `1-1` (top-level port on the usb1 root hub).
  This port is now *the GPS jack* — plug discipline is a hard requirement.

---

## The position snapshot contract (`GPS.md` — spec this first)

Like TABLES.md and DOCK-PROTOCOL.md, this is the only coupling between the
GPS plumbing and every consumer (tile geotagging, capture stamping, future
Signal K comparison). Consumers never talk to gpsd; they read one file.

- [x] Write `GPS.md` in the repo defining:
      - [x] Snapshot path: `/run/kilodash/gps/position.json` (tmpfs — this is
            volatile state, not data; gone on reboot by design).
      - [x] Schema: `ts` (ISO8601 UTC, from GPS time), `fix` (`none`|`2d`|
            `3d`|`dgps`), `lat`, `lon` (decimal degrees, null when no fix),
            `sog_mps`, `cog_deg_true`, `alt_m`, `hdop`, `sats_used`,
            `sats_visible`, `time_quality` (`gps`|`unsynced`).
      - [x] Write rule: **one writer** (the snapshot service), atomic
            tmp-file + rename, cadence 1 Hz. Consumers only read.
      - [x] Staleness rule: consumers MUST check `ts` age; older than 5 s or
            file absent ⇒ treat as **no fix**. Never trust a stale position.
      - [x] Geotag rule for capture artifacts: when a capture starts
            (candump log, LAN scan, LA session), the capturing screen reads
            one snapshot and embeds it in the capture's sidecar/manifest —
            `null` + reason when no fix. No continuous tracking of captures;
            one stamp at start (+ optionally one at stop).
- [x] Shared reader helper `gps/snapshot.py::read_position()` implementing
      the staleness rule once — no consumer hand-rolls the age check.

## Phase 0 — Hardware bring-up gate

No software phase proceeds until fix acquisition is verified.

- [x] udev rule `setup/99-kilodash-serial.rules`: match PL2303 on
      `KERNELS=="1-1"` → symlink `/dev/gps0`, mode/group readable by the
      kilodash user. Install + `udevadm control --reload` in the phase
      installer. Document the port→device assignment table in the rule file
      header (this file becomes the registry as more per-port dongles land).
- [x] Module config utility `gps/pa1616s.py` (also used by gpsd hook or run
      once pre-gpsd at service start):
      - [x] **Baud probe:** open @115200, read 2 s; valid NMEA (checksum-
            verified sentence) ⇒ already configured. Else reopen @9600.
            Garbage at both ⇒ fail loudly, tile shows fault.
      - [x] If at 9600: `PMTK251,115200` → reopen @115200.
      - [x] `PMTK220,100` (10 Hz), `PMTK314` sentence mix: RMC+GGA+GSA every
            fix (10 Hz), **GSV every 10th fix (1 Hz)** — sky plot doesn't
            need 10 Hz and the bandwidth belongs to position.
      - [x] All PMTK writes checksummed, ack-checked (`PMTK001` reply) with
            timeout+retry. This is serial TX to the GPS module, not the bus
            — no CAN-scan implications, but note it in the module docstring.
- [ ] **Gate check (manual, near a window / outdoors):** `A` flag in RMC,
      real date (not 050180), sats in GSV, `cgps`/`gpsmon` shows 3D fix.
      Record time-to-first-fix cold for the README.

## Phase 1 — Plumbing (gpsd + chrony + snapshot service)

- [x] gpsd from Kali repos (appliance-safe), configured for `/dev/gps0`
      only — no autodiscovery (`USBAUTO="false"`); we control identity via
      udev, not gpsd guessing.
- [x] Baud-config ordering: run `gps/pa1616s.py` **before** gpsd opens the
      port (systemd `ExecStartPre=` on the gpsd unit or a small oneshot unit
      ordered before it). gpsd then opens an already-configured module.
- [x] chrony: add gpsd as a time source via SHM/SOCK refclock. **No PPS on
      Prime** (GPIO buttoned up; PPS is a Scottina Light effort) — expect
      tens-of-ms accuracy, which still beats no-network drift by miles.
      - [x] Refclock marked so chrony prefers NTP when the network exists
            and falls back to GPS when disconnected.
      - [x] **Light Dock tie-in:** extend the dock clock-push quality flag
            enum with `gps` (better than `rtc`/`unsynced`, network-
            independent). Flag ordering: `ntp` ≥ `gps` > `rtc` > `unsynced`.
            Touches DOCK-PROTOCOL.md — version-bump per its rules; Light
            side treats unknown flag values per the protocol's reject pass.
- [x] Snapshot service `gps/snapshotd.py`: connects to gpsd (localhost
      JSON), writes the contract file at 1 Hz, atomic rename, systemd unit.
      Crash-only design: no cleanup needed, staleness rule covers death.
- [x] Hotplug: udev presence of `/dev/gps0` gates everything; unplug stops
      snapshot writes → consumers see staleness → "no fix" everywhere.
      No special teardown paths.

## Phase 2 — GPS tile

- [x] New `Screen` subclass "GPS", hotplug-gated on `/dev/gps0` presence
      (existing pattern). Reads gpsd directly for rich data (SKY/TPV) — the
      snapshot contract is for *other* tiles; this tile is the one place
      full gpsd detail is wanted.
- [x] Layout (portrait 320×480, Scottina interface structure):
      - [x] **Sky plot** (top, square-ish): az/el polar plot, per-sat dots
            sized/shaded by SNR, used-in-fix vs visible distinguished.
            Phosphor/CRT aesthetic per Light Dock precedent — this is an
            across-the-room fix indicator: empty sky = searching, filled =
            locked.
      - [x] **Status block:** fix type, sats used/visible, HDOP, lat/lon,
            SOG (knots)/COG, UTC, time-source line (chrony: which source is
            selected — the "am I the time authority right now" answer).
      - [x] Dirty-rect friendly: sky plot repaints on GSV cadence (1 Hz),
            status lines only on change (KioskSpeedImprovementToDo
            guidance).
- [x] No TX controls on this tile. The N2K source button lives on the
      NMEA2K tile (it's a bus action, so it belongs on the bus screen).

## Phase 3 — N2K proper node (GNSS source)

The big one. Prime becomes a real bus participant when — and only when —
the user presses the button.

- [x] **TX allow-list carve-out (do this first, it gates everything):**
      evolve the AST scan from "no TX anywhere" to a **positive allow-list
      of TX-permitted modules** — `n2k/node.py` (this node) and the existing
      link-layer heartbeat/reply path. Scan hard-fails any send/sendto/write
      on a CAN socket in any other module. Independent reject pass
      unchanged. Update the scan's own tests: an allow-listed module TXing
      passes; a screen module TXing fails the build.
- [x] `n2k/node.py` — ISO address claim state machine (ISO 11783-5):
      - [x] NAME: unassigned/open manufacturer-code range, Industry Group 4
            (Marine), Device Class 60 (Navigation), Function 145 (GNSS),
            arbitrary-address-capable bit set.
      - [x] Preferred SA persisted to `/opt/kilodash/state/n2k_sa.json`
            (atomic write) — re-claim the same address across boots.
      - [x] Claim on activation: send 60928, 250 ms contention window;
            defend (lower NAME wins, we re-claim) or move (compute next SA,
            re-claim); address-exhaustion → cannot-claim state, surfaced in
            UI, no TX.
      - [x] Answer ISO Request (59904) for 60928 at any time while active.
      - [x] Deactivation (button-off or auto-stop): cease PGN TX
            immediately. (No "release" message exists in the standard;
            going silent is correct.)
- [x] **TX PGN set** (all sourced from live gpsd data, never the snapshot
      file — no double-staleness):
      - [x] 126992 System Time @ 1 Hz
      - [x] 129025 Position, Rapid Update @ 10 Hz
      - [x] 129026 COG & SOG, Rapid Update @ 10 Hz
      - [x] 129029 GNSS Position Data @ 1 Hz — **fast-packet TX**, first
            in the ecosystem: frame sequencing/counters outbound.
            Implement as a reusable `n2k/fastpacket_tx.py` (Wio Terminal
            Island will want it) and **round-trip test it against our own
            RX reassembly** — the two implementations validate each other.
      - [x] 126993 Heartbeat @ 60 s
- [x] **Auto-stop on fix loss:** fix degrades below 2D or gpsd data goes
      stale > 2 s ⇒ stop PGN TX (node may keep its address for quick
      resume; define resume-vs-full-reclaim in the module docstring). A
      proper node never sources stale position.
- [x] **NMEA2K tile button:** appears only when `/dev/gps0` present AND
      current snapshot shows a fix. States: `off` → `claiming` →
      `sourcing @SA=x` → (`cannot-claim` | `stopped: fix lost`). Non-modal
      status per house style. Button label makes the action unmistakable:
      "Source GNSS → bus".
- [x] RX interaction: while sourcing, the NMEA2K screen's decode view will
      see our own PGNs (bus echo). Tag own-source rows (match our claimed
      SA) visually — self-traffic is signal for verifying our TX, but must
      not be mistaken for the boat's GPS.

## Phase 4 — Cross-validation + install/docs

- [x] **GPS-vs-bus comparison (diagnostics payoff):** when the NMEA2K
      screen decodes position PGNs from *another* source while we have a
      local fix, compute and display delta: position offset (m), SOG/COG
      disagreement. Threshold badge (green < 10 m, amber < 50 m, red
      beyond). This diagnoses the boat's GPS installation — the whole
      point of a second receiver.
- [x] Phase installer under `setup/`: udev rule, gpsd + chrony packages and
      config, systemd units (config oneshot, gpsd, snapshotd) — idempotent.
- [x] README device-table row: **GPS | PA1616S @ /dev/gps0 (port 1-1) |
      time authority, geotag snapshots, sky plot, N2K GNSS source**.
- [x] Document the widened TX exception verbatim in README scope section.
- [x] `GPS.md` linked from docs; DOCK-PROTOCOL.md version bump PR for the
      `gps` quality flag.

---

## Known gotchas

- **9600 cannot carry 10 Hz.** RMC+GGA+GSA at 10 Hz is ~2 KB/s ≈ 20 kbit/s
  on the wire — baud raise to 115200 is a functional requirement, not an
  optimization. If config silently fails, the module drops/garbles
  sentences; the ack-checked PMTK writes exist to make that failure loud.
- **No battery today, battery someday.** Cold start at 9600 every boot now;
  the day a CR1220 goes in, the module boots already-configured at 115200.
  The baud probe handles both — do not "simplify" it away.
- **PL2303 has no serial number.** Identity is the physical port, full
  stop. Swapping dongles between jacks silently swaps device identities.
  The udev rules file header is the registry; keep it current.
- **Fast-packet TX is new code.** RX reassembly has existed since the
  CAN/N2K split; TX sequencing has not. The round-trip test (our TX → our
  RX) is the cheapest correctness check and also hardens the RX side with
  a second real implementation.
- **Self-echo.** Sourcing onto the bus means decoding our own frames.
  Without own-SA tagging, the GPS-vs-bus comparison in Phase 4 could
  "validate" our own output against itself — comparison must exclude our
  claimed SA.
- **Time authority ≠ PPS accuracy.** Serial-only gpsd time is tens of ms
  and can jitter with USB latency. Fine for log timestamps and dock
  pushes; do not present it as anything more. PPS is Scottina Light's
  adventure.
- **gpsd autodiscovery.** Left enabled, gpsd will grab other ttyUSB
  dongles (CanTick's serial console, Light's dock port) and speak NMEA at
  them. `USBAUTO="false"` + explicit `/dev/gps0` only — this one bites
  ecosystems with many serial devices, which is exactly what Prime is.

## Suggested first slice

`GPS.md` + `gps/snapshot.py` reader (headless, testable, everything reads
it) → Phase 0 udev rule + `pa1616s.py` with the baud probe, then the
manual fix-acquisition gate near a window. Phase 1 plumbing next — chrony
showing GPS as a selectable source is the proof the spine works before any
pixels or bus frames exist. Tile and node can then proceed in either
order; the TX allow-list carve-out lands first-thing in Phase 3 regardless.
