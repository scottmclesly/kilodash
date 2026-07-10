# LAN Scan Screen — Refactoring To-Do

**Project scope:** Diagnostics ONLY. No offensive or attack tooling.
**Target hardware:** 3.5" TFT, 480×320, touch.
**Guard-rail principle:** The UI must be *physically incapable* of expressing an offensive scan. Safety is enforced in the command-builder code, not by convention or user discipline.

> **Status: IMPLEMENTED (2026-07-05).** Safety core in `kilodash/scan.py`, UI in `kilodash/screens/lan.py`, tests in `tests/test_scan.py` (40 passing), docs in README. Run tests: `python -m unittest discover -s tests`.

---

## 0. Scope Definition (do this first)

- [x] Confirm the LAN Scan screen answers only these questions:
  - *What devices are alive on my subnet?*
  - *What services/versions are they running?*
  - *Is an expected port open on a known host?*
- [x] Explicitly document what this screen is NOT: no evasion, no NSE scripts, no vuln probing, no spoofing.
- [x] Add a one-line scope banner in code comments at the top of the screen module referencing this file.

---

## 1. Command Builder Refactor (the safety core)

The single most important change. All scan commands must be assembled by a builder from discrete intents — never from a free-text flag string.

- [x] Create `buildScanCommand(mode, target, ports?)` that returns an **argument array**, never a shell string (avoids injection).
- [x] Map the four allowed modes to fixed argument lists:
  - [x] `Discover` → `-sn` (host discovery, no port scan)
  - [x] `Ports` → TCP connect scan (`-sT`), common ports, optional `-p` from ports field
  - [x] `Services` → `-sV`
  - [x] `Identify` → `-O`
- [x] Default the `Ports` mode to a curated common-port list when the ports field is blank.
- [x] Validate `target` as IP / hostname / CIDR before it reaches the builder; reject anything else.
- [x] Validate `ports` field as digits, commas, and hyphens only.

## 2. Reject-List Enforcement (defense in depth)

Even though the UI can't emit these, the builder must actively refuse them in case a value arrives from anywhere else.

- [x] Hard-reject if any of these appear in the assembled args:
  - [x] `--script`, `-sC` (NSE — the primary offensive subsystem; **top priority to block**)
  - [x] `-sS`, `-sF`, `-sX`, `-sN` (stealth/half-open/evasion scans)
  - [x] `-A` (aggressive; bundles NSE)
  - [x] `-D`, `-S`, `--spoof-mac` (decoys / identity spoofing)
  - [x] `-f`, `--mtu`, `--data-length` (firewall-evasion fragmentation)
  - [x] `-T4`, `-T5` (evasion-tuned timing)
- [x] On rejection: refuse to run, log the rejected token, show a neutral error in the output pane.
- [x] Add a unit test per rejected flag proving the builder throws/refuses.
- [x] Add a unit test per allowed mode proving it produces the exact expected arg array.

## 3. Privilege Handling

- [x] Default to `-sT` (connect scan) so unprivileged operation works.
- [x] Detect when `-O` / `-sV` require root; if not privileged, degrade gracefully with a clear message rather than failing silently.
- [x] Never auto-escalate; surface the requirement to the user.

---

## 4. UI Refactor (480×320)

Replace any raw-flag or free-text command entry with intent-based controls.

- [x] **Top bar:** `LAN Scan` title + back/home button (≥44px touch target).
- [x] **Target field:** IP / hostname / CIDR (e.g. `192.168.1.0/24`); tap opens on-screen keyboard.
- [x] **Mode segmented control** (this IS the safety boundary, front and center):
  - `Discover` · `Ports` · `Services` · `Identify`
- [x] **Ports field:** visible only in `Ports` mode; placeholder shows common-port default.
- [x] **Run / Stop button:** single big action, toggles state.
- [x] **Output pane:** scrolling monospace rows — `IP · state · port/service`.
- [x] **Host-count badge:** live count of discovered hosts.
- [x] Remove any pre-existing free-text command input entirely.

## 5. Output Handling

- [x] Parse nmap normal output into structured rows (IP, state, ports, service/version).
- [x] Stream results into the pane as they arrive; don't block on full completion.
- [x] Cap retained lines to protect memory on the Pi.
- [x] Show a clear "scan complete / N hosts" terminal state.

---

## 6. Consistency With Shared Primitives

- [x] Reuse the common Top bar, Target field, Run/Stop, Output pane, and Status badge primitives — no bespoke widgets.
- [x] Match iface/target field behavior to other diagnostic screens (ping, arp-scan, dig).

## 7. Testing & Verification

- [x] Unit tests: builder output per mode (§1) and reject-list per flag (§2).
- [x] Integration test: run each mode against localhost / a known lab host; confirm structured rows.
- [x] Manual test on-device: touch targets, keyboard, scroll performance at 480×320.
- [x] Negative test: attempt to inject a blocked flag via the ports/target fields; confirm rejection.

## 8. Documentation

- [x] Inline comment block at top of screen module stating diagnostics-only scope + link to this file.
- [x] Short README section: what each mode does, in plain language, no flag syntax exposed to end users.
- [x] Record the rejected-flag rationale so future contributors don't "helpfully" re-add `-A` or `--script`.

---

## Definition of Done

- [x] UI exposes only the four safe modes; no path to raw flags.
- [x] Command builder emits arg arrays and refuses every listed offensive flag, with passing tests.
- [x] NSE (`--script` / `-sC`) is provably unreachable.
- [x] Screen runs and renders results cleanly on the 3.5" target.
- [x] Scope documented in code and README.
