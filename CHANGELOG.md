# Changelog

All notable changes to Scottina are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
