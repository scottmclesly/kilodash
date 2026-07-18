# Scottina v1.0

**A pocket-sized front panel for the diagnostic tools you already use.**

Scottina is a fingertip control panel for a Raspberry Pi 5 running Kali Linux,
driving a 3.5" ILI9486 SPI touchscreen. It boots straight to a tap-driven
dashboard — no keyboard, no mouse, no X server — and fronts your network, radio,
bus, and web-app tooling as finger-sized tiles. It renders directly to
`/dev/fb0` with PIL + numpy and reads touch straight from the ADS7846 evdev node.

It doesn't invent new tools. It gives the ones you already trust a front panel
sized for the one question you're asking: plug a thing in, glance at the 3.5"
screen, get your answer. Scottina's own IP is always in the header, and every
web-app screen shows the exact `URL:port` to open the full interface.

It is **not** a Flipper Zero, a Marauder deployment, or a wardriving toy — it's a
shop multitool that makes well-known diagnostic software glanceable.

## What's inside

**Built-in screens (always present):**

- **LAN Scan** — intent-based `nmap` front end with four modes (Discover /
  Ports / Services / Identify) and **no raw-flag input** — the mode *is* the
  safety boundary. Diagnostics only.
- **Wi-Fi** — enable/scan/connect headless, with an on-screen keyboard for
  secured networks.
- **Pi Health** — temperature, CPU, memory, disk, uptime, Wi-Fi signal,
  throttling.
- **Pomodoro** — focus/break timer that keeps counting on a background thread
  and toasts transitions app-wide.
- **Settings** — every tunable as a card, power actions, and a touch-calibration
  helper. Shows the running **version** in the About row.
- **Micro KVM** — mirror of the off-grid Meshtastic/LoRa command plane (armed
  only while off-network).

**Hotplug device screens (tile appears only while the device is present):**

- **RTL-SDR** — frequency scan (`rtl_power`), device identify (`rtl_433`), and
  IQ capture. **RX-only.**
- **Wi-Fi Sniff** (ALFA) — passive monitor-mode capture with `airodump-ng`; a
  watchdog keeps the Pi's own uplink connected. **Passive only — no injection,
  no deauth.**
- **CAN** (CanTick / CANable / gs_usb) — raw-bus forensics: a seen-IDs table
  (count, rate, changed-byte highlight), per-byte watch alerts, and replayable
  candump `.log` export.
- **NMEA2K** — semantic decode of known PGNs against converter tables:
  fast-packet reassembly → PGN lookup → per-field values with range/appearance
  alerts; JSON-lines export.
- **GPS** (PA1616S) — disconnected time authority (gpsd + chrony), geotag
  snapshots, phosphor sky plot, and the user-triggered N2K GNSS source.
- **I2C Scan** — `i2cdetect` on the Pi's bus with best-guess names.
- **Serial** — lists USB-serial ports and gives a read-only live view at a
  chosen baud.
- **Logic** (FX2LP) — passive 8-channel digital capture + protocol decode
  (UART/I2C/SPI/CAN) via `sigrok-cli`/fx2lafw; every capture saved as `.sr`.
- **Tables** — remote control for the on-device table-converter service plus a
  mirror of installed PGN tables.
- **Files** — offload captures and exchange decode tables to/from a USB stick,
  with a sync-then-eject button. Copies never delete originals.
- **Light Dock** — auto-sync to a Scottina Light on dock: clock push,
  decode-table push, and checksum-verified black-box log pull.

**Web-app launch terminals** (tile appears once the backend is installed) —
each launches the app, confirms its port actually answers, and shows the
`URL:port` to open the full UI:

- **Kismet** (`:2501`), **Node-RED** (`:1880`, with 6 assignable feedback fields
  + 6 trigger buttons), **AIS** (AIS-catcher RX + a hardware-gated TX-test
  control), **Signal K** (`:3000`, paged vitals with a live heartbeat line).

## Scope

**Diagnostics only — no offensive tooling ships.** LAN Scan exposes no raw
flags and rejects NSE/stealth/evasion/spoofing; Wi-Fi Sniff and the SDR are
passive/RX-only with no injection anywhere in the tree.

**One explicit exception, enforced in code (positive allow-list + independent
reject pass, not by convention):** the CAN/NMEA2K side transmits *solely* for
correct bus participation. In-tree, that is exactly one module — `n2k/node.py`,
the user-triggered GNSS source node (ISO address claim, claim defense, ISO
request responses, five GNSS PGNs). The CanTick link-layer heartbeat lives in
the device firmware, off-tree. `tests/test_txscan.py` scans the whole tree every
build to keep any other module from transmitting; no injection, replay, fuzzing,
or arbitrary-frame TX is expressible anywhere in the UI.

## Requirements

- Raspberry Pi 5
- Kali Linux (arm64 Pi image)
- 3.5" ILI9486 SPI touchscreen, 480×320, ADS7846 resistive touch
- Core deps (installed by the base script): `python3-pil`, `python3-numpy`,
  `python3-evdev`, `nmcli`, `arp-scan`, `vcgencmd`
- Optional dongles for their tiles: RTL-SDR, a second Wi-Fi adapter (ALFA),
  a CAN interface / CanTick, GPS, an FX2LP logic analyzer, USB-serial adapters

## Install

```bash
git clone https://github.com/scottmclesly/Scottina.git
cd Scottina
sudo setup/install.sh
sudo reboot        # first time only, for the display overlay
```

The installer is idempotent and takes a fresh image to a running kiosk. Optional
backends are separate phase scripts. Full walkthrough: **[docs/INSTALL.md](docs/INSTALL.md)**.

## Honest limitations

- **RTL-SDR is receive-only hardware** — Scottina scans, identifies, and
  captures IQ, but never transmits on it. (The AIS TX-test needs a separate
  HackRF/Pluto.)
- **No controllable backlight** — this panel exposes no `/sys/class/backlight`
  node, so "dimming" is a software screensaver that darkens the rendered image.
- **NMEA2K decode** is validated against canonical PGN vectors; broad validation
  against captured real-world multi-frame (fast-packet) traffic is ongoing.
- **Resistive touch is discrete-tap only** — no swipe gestures (every screen has
  a Back button; long lists have ▲▼ scroll).

## License

[MIT](LICENSE). Created by Scott McLeslie for the benefit of all living beings.
