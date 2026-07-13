# Changelog

All notable changes to Scottina are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **CAN / NMEA2K split + Tables converter** (contract in `TABLES.md`):
  - **CAN screen refactor** — raw-bus forensics for reverse-engineering:
    seen-IDs table (count/rate/last payload/changed-bytes highlight since
    the previous frame), tap-a-row **byte grid** with per-byte watches in
    two modes (change-detection and value-match; non-modal badge + row
    flash), candump-style live list, bounded 50k-frame ring log with
    ID/watched/changed filters exported as replayable candump `.log`.
    RX-only enforced in code: `tests/test_busmon.py` AST-scans the screen
    and its model (`kilodash/busmon.py`) for TX-shaped calls every run.
    Existing controls (bitrate/autodetect/logging/CanTick hosting) live on
    the Setup tab unchanged.
  - **NMEA2K screen** (`kilodash/screens/n2k.py`, decode core
    `kilodash/n2k.py`) — table-driven semantic decode: fast-packet
    reassembly → PGN lookup → field extraction; per-(PGN, source) rows with
    tap-through field breakdown; **range-exit** (transition-fired) and
    **appearance** alerts; unknown PGNs counted + one-tap handover to the
    CAN screen; bounded decoded log exported as JSON lines.
  - **Table store contract** (`TABLES.md`, `tables/validate.py`,
    `tables/store.py`) — Canboat-JSON subset, per-table manifest sidecar
    (sha256, enabled flag, pgn_count), consumers-read/converter-writes
    discipline, shared validator run on ingest *and* on load, flat
    SD-export shape feeding Wio Terminal Island.
  - **Tables converter web app** (`kilodash/tableconv.py`, Flask, port
    8735) — PDF → side-by-side human review → validate → atomic install;
    Installed tab (enable/disable/remove/download/manifest + inbox
    ingest); DBC tab stubbed. On-demand systemd unit
    (`kilodash-tables.service`) with a 15-min idle self-shutdown that
    counts in-flight conversions as activity; uploads size-capped +
    magic-checked, PDF parsing crash-isolated in a subprocess
    (`kilodash/pdfextract.py`).
  - **Tables tile** (`kilodash/screens/tables.py`) — thin remote control +
    mirror: service status with idle countdown, URL **+ QR code** at the
    advertised address, manifest-only inventory (tap = atomic enabled
    flip, ✕ = remove). `kilodash/net.py::advertise_addr()` (eth0-preferred)
    is the shared address helper the CanTick work reuses.
  - Files screen **Tables → USB** now exports the installed `pgn/` store
    (tables + manifests) flat — the Wio Terminal Island SD shape.
  - Installer `setup/install-tables.sh`; user guide `docs/NMEA2K.md`.

- **CanTick WiFi-CAN bridge** (`kilodash/cantick.py`, contract in
  `PROTOCOL.md`): a CanTick dialing in over WiFi appears as an ordinary
  `slcan0` — supervised `socat`+`slcand` pair on TCP 29536 with automatic
  relaunch/backoff, read-only UDP 29537 heartbeat (freshness badge, live rx/s,
  drop-rising warning, contract-version check), one-time USB provisioning
  push (CTK1 framing, CRC-16/CCITT-FALSE, base64 creds, PSKs never logged),
  and a reversible `hostapd`+`dnsmasq` fallback AP on `wlan0` for
  no-uplink diagnostics (`Scottina-CanTick` @ 192.168.42.1, bench-proven
  full up/restore cycle). CAN screen gains a CanTick source chip, a
  heartbeat health card, and a Provision button when a CanTick is on USB.

## [1.0.0] — 2026-07-05

First release. **Scottina** — the digital Swiss Army knife for hardware
developers: a tap-driven dashboard for a Raspberry Pi 5 with a 3.5" ILI9486
SPI touchscreen (480×320, ADS7846 resistive touch), rendering directly to
`/dev/fb0` with no X server.

### Added

**Core**
- Framebuffer UI engine (PIL + numpy straight to `/dev/fb0`) with a tile-grid
  Home screen, per-screen Back buttons, and ▲▼ scroll buttons for long lists.
- Touch input read directly from the ADS7846 evdev node, with runtime
  orientation settings (swap/invert axes, 180° flip) and an on-screen
  calibration helper.
- Config-driven Settings screen: every entry in `config.py::DEFAULTS` renders
  automatically as a toggle, stepper, or choice cycler. Includes power actions
  (restart UI / reboot / shutdown) and an About card.
- Themeable look with a boot splash (animated `ScottinaSplash.gif`) shown the
  moment the app owns the framebuffer, plus an idle screensaver dim timeout.
- `kilodash.service` systemd unit and setup scripts for kiosk boot.

**Screens**
- **System health** — CPU, memory, temperature, and disk at a glance.
- **LAN tools** — network status and host discovery.
- **Wi-Fi** — scanning, plus a monitor-mode sniffer that keeps the uplink
  alive while capturing.
- **RTL-SDR** — spectrum capture with signal identification.
- **I²C scanner** — bus probing for attached devices.
- **CAN bus** — traffic monitor.
- **Serial monitor** — live view of serial ports.
- **Pomodoro timer** — because hardware work needs breaks too.

**Web-app launch panels** (Phase 4 framework)
- **Kismet**, **Node-RED**, **AIS-catcher**, and **Signal K** screens that
  start/stop the backing service and front its web UI.

**Project**
- MIT license — created by Scott McLeslie for the benefit of all living
  beings; free to share and contribute.
- README, roadmap, and install scripts for side-loaded apps.

[1.0.0]: https://github.com/scottmclesly/Scottina/releases/tag/v1.0.0
