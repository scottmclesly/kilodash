# Scottina

**The digital Swiss Army knife for hardware developers.**

A fingertip control panel for a Raspberry Pi 5 running Kali Linux, driving a
3.5" ILI9486 SPI touchscreen (480×320, ADS7846 resistive touch). It boots
straight to a tap-driven dashboard — no keyboard, no mouse, no X server — and
fronts your network, radio, bus, and web-app tooling as finger-sized tiles.

![screens](docs/screens.png)

> **Name note:** the product is **Scottina**, hosted at
> [github.com/scottmclesly/Scottina](https://github.com/scottmclesly/Scottina).
> The Python package, install path (`/opt/kilodash`), and systemd unit keep the
> historical working name `kilodash` on purpose — renaming them buys nothing
> and risks breaking the service. Everything you *see* says Scottina.

## Why it works the way it does

- **Renders directly to `/dev/fb0`** (the `ili9486drmfb` framebuffer) with PIL +
  numpy, and **reads touch straight from the ADS7846 evdev node**. No SDL, no
  Xorg, nothing that can lose the DRM device. This is the one approach that was
  reliable on this panel.
- **All navigation is discrete taps.** Resistive touch is too noisy to tell a
  horizontal swipe from a vertical drag, so we don't try: Home is a tile grid,
  every other screen has a Back button, and long lists get real ▲▼ scroll
  buttons.
- **Touch orientation is a runtime setting**, not a compile-time constant, so the
  panel can be re-calibrated from the Settings screen or by editing
  `config.json` over SSH — no reboot, no code edits.
- **Everything tunable lives in `config.py::DEFAULTS`.** The Settings screen
  renders whatever it finds there, so adding a knob is a one-line change.

## Boot splash

On start-up Scottina paints [`ScottinaSplash.png`](ScottinaSplash.png) as a
curtain the instant it owns the framebuffer, so the boot gap reads as an
intentional splash rather than a blank panel. It holds for ~2.5 s (a tap lifts
it early) while the rest of the app initialises, then hands off to Home.

## Hardware / display config

The panel is brought up by a single, clean overlay in
`/boot/firmware/config.txt`:

```
dtparam=spi=on
dtoverlay=piscreen,drm,rotate=90     # rotate=90 == the panel flipped 180° from its old 270°
```

`rotate` is the only display knob that needs a reboot. Touch axes are handled in
software (see below), so you never touch the overlay for calibration. A backup
of the original config is saved next to it as `config.txt.kilodash-bak.*`.

> Note: this panel exposes **no controllable backlight** (`/sys/class/backlight`
> is empty), so "dimming" is a software screensaver that darkens the rendered
> image. If you later wire a PWM backlight, Scottina auto-detects the sysfs node
> and uses it.

## Install

```bash
sudo cp /opt/kilodash/kilodash.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kilodash
```

Runs as root (needs `/dev/fb0`, evdev, `nmcli`, and `arp-scan`'s raw sockets).

Core dependencies (already present on this build): `python3-pil`,
`python3-numpy`, `python3-evdev`, plus `nmcli`, `arp-scan`, and `vcgencmd`.
`python3-pygame` is **not** used. The web-app backends (Node-RED, AIS-catcher,
Signal K) and the SDR/Wi-Fi tools install separately — see
[Web-app launch terminal](#web-app-launch-terminal) and
[`setup/install-phase4.sh`](setup/install-phase4.sh).

## Using it

- **Tap a tile** on Home to open a tool; **tap Back** (top-left) to return.
- **Tap** buttons, list rows, and toggles. Targets are ≥44 px for fingers.
- **Scroll** long lists with the ▲▼ buttons at the lower-right; the percentage
  between them shows your position.
- **Dimming**: after the idle timeout the screen darkens (software); the next
  tap only wakes it, it doesn't also activate what you touched.
- Quit while testing from a USB keyboard with **ESC** or **q**.

### Home tiles & pictograms

Each tool has its own **Semiotic-Standard-inspired pictogram** (the geometric,
monochrome style of Ron Cobb's *Alien* corridor icons) instead of a plain dot,
so tiles are distinguishable at arm's length. Glyphs live in
[`kilodash/pictograms.py`](kilodash/pictograms.py); a screen names its glyph
with a `glyph = "…"` class attribute and unknown keys fall back to a filled dot.

Fixed tiles are always shown. **Device tiles appear only while their dongle is
plugged in** (hotplug, see `devices.py`) and carry a small green "live" badge.
**Web-app tiles appear only once the app is installed.**

## Screens

**Built-in (always present):**

| Screen | What it does |
|---|---|
| **LAN Scan** | `arp-scan` sweep of the local subnet — IP, hostname, MAC, vendor. Tap Scan to (re)run; the list scrolls. |
| **Wi-Fi** | Enable/disable, scan SSIDs, tap to connect. Secured networks open the on-screen keyboard for the password; saved/open ones connect immediately. |
| **Pi Health** | Temperature, CPU, memory, disk, uptime, Wi-Fi signal, throttling — each a labelled bar or value card. |
| **Pomodoro** | Focus/break timer that **keeps counting on a background thread** even when you're on another screen, and toasts each transition app-wide. |
| **Settings** | Every tunable as a card (booleans toggle, ints step, choices cycle), plus power actions (Restart UI / reboot / shutdown) and a touch-calibration helper. |

**Hotplug device screens (tile shows only while the device is present):**

| Screen | Device | What it does |
|---|---|---|
| **RTL-SDR** | RTL2832U dongle | Frequency **Scan** (`rtl_power` sweep → spectrum + peak), **Identify** (`rtl_433` decodes real ISM packets and names the device), per-band knowledge hints, and IQ **Capture** (RX-only, no replay). |
| **WiFi Sniff** | ALFA (2nd adapter) | Passive monitor-mode capture with `airodump-ng` — every AP/client it hears (SSID, channel, encryption, signal). A watchdog keeps the Pi's own uplink (`wlan0`) connected the whole time. Passive only, no injection. |
| **CAN Bus** | CANable / gs_usb / slcan | Bring the interface up at a chosen bitrate, best-effort bitrate **autodetect**, a **live RX-frame counter + frames/s** readout, and logging to a timestamped `candump` file. |
| **I2C Scan** | onboard i2c-1 | `i2cdetect` on the Pi's bus with best-guess names for responding addresses. |
| **Serial** | FTDI / CP210x / CH340 | Lists USB-serial ports and gives a read-only live view of one at a chosen baud — handy for sniffing UART/debug output. |

**Web-app launch terminals** (see below): **Kismet**, **Node-RED**, **AIS**,
**Signal K**.

## Live screens & responsiveness

Fast-changing screens are decoupled from the slow ones. The main loop honours a
per-screen `tick_interval` and only wakes each screen at its own cadence; live
screens tick at ~20 Hz while everything else stays at ~1 Hz. To make that
affordable on the SPI panel, `framebuffer.py` supports **dirty-rect blits** —
a screen reports the boxes that actually changed (via `self.report_dirty(...)`
in `tick()`) and only those row bands are written, 2–15× cheaper than a full
frame. Full-frame redraws still cover first frame, transitions, the keyboard
overlay, and dimming wake.

Guardrails keep a wedged data source from spinning the CPU: the **CAN** counter
and **Signal K** poller drop back to a slow tick automatically when no data is
flowing. A perf overlay (FPS / frame-time) is available behind
**Settings → System → "FPS meter"** (default off). See
[`KioskSpeedImprovementToDo.md`](KioskSpeedImprovementToDo.md) for the measured
before/after numbers.

## Touch calibration

If taps land in the wrong place after the 180° flip, open **Settings → Touch**
and flip these one at a time:

| Symptom | Toggle |
|---|---|
| taps mirror left/right | `Touch invert X` |
| taps mirror up/down | `Touch invert Y` |
| taps land on the wrong axis entirely | `Touch swap X/Y` |

Same values live in `config.json` if the panel is so far off you can't hit the
toggles — edit it over SSH and restart the service. Defaults target the
`rotate=90` orientation (`swap=on, invert_x=off, invert_y=off`); the old
`rotate=270` values were `swap=on, invert_x=on, invert_y=on`.

## Web-app launch terminal

Beyond the built-in screens, Scottina fronts **bigger packages that serve their
own browser UI**. Opening one of these screens *launches the app*, waits until
its port actually answers (a real "✓ web UI confirmed", not just "spawned"),
and shows the **URL:port** to open the full interface from a phone or laptop —
plus a compact native panel of controls and live feedback. If the app was
already serving (autostarted at boot), it's adopted instead of duplicated.

Shipped apps (tiles appear only when the app is installed):

- **Kismet** — launches the server, confirms `:2501`, a Sniff on/off toggle that
  adds the ALFA as an uplink-safe monitor source, and a live peer list
  colour-coded by device type.
- **Node-RED** — confirms the `:1880` editor and gives **4 assignable feedback
  fields + 4 trigger buttons** wired to your own flow. Import
  [`setup/nodered-kilodash-flow.json`](setup/nodered-kilodash-flow.json) and see
  [`setup/NODE-RED.md`](setup/NODE-RED.md) for the wire-up guide.
- **AIS** — AIS-catcher on the RTL-SDR for live vessel/message feedback (RX), plus
  an own-MMSI field and a hardware-gated Transmit-test control for bench-checking
  a robot's AIS receiver (TX needs a HackRF/Pluto + `ais-simulator`).
- **Signal K** — the boat's data hub as a helm glance. Confirms `:3000`, then
  pages through vitals groups (Nav / Engine / Environment / Power, tap to cycle)
  with a live **heartbeat** line — freshness dot + feed count — so one glance
  tells you the NMEA2000→SK bridge is actually flowing. Never stopped on leave.

Install the backends with [`setup/install-phase4.sh`](setup/install-phase4.sh)
(idempotent; installs Node-RED, AIS-catcher, Signal K + their systemd units).

The framework lives in `kilodash/webapp.py` (a `WebApp` process/port supervisor
+ stdlib HTTP helpers) and `screens/webapp_base.py` (`WebAppScreen`). To add
another web app, subclass `WebAppScreen`, set `app_name`/`port`/`service` (or
`start_cmd`), and override the `draw_app`/`handle_app_tap`/`poll_app` hooks.

## Layout

```
run.py                  entrypoint
ScottinaSplash.png      boot curtain art
kilodash/
  app.py                main loop, taps, splash, dirty-rect render, dimming, keyboard overlay
  framebuffer.py        /dev/fb0 pack + blit (RGB565 / XRGB8888), full-frame + dirty-rect paths
  pictograms.py         Semiotic-Standard tile glyphs
  touch.py              ADS7846 evdev reader + axis mapping
  system.py             network / wifi / lan / health data (+ background Task)
  webapp.py             launch/confirm/supervise third-party web apps (Phase 4)
  devices.py            USB / bus hotplug detection (drives tile visibility)
  widgets.py            Button, on-screen Keyboard, helpers
  theme.py              palettes + font cache
  config.py             settings schema + JSON persistence
  screens/              one file per screen
legacy/                 the fbdash.py / kilo_dash.py prototypes this grew from
setup/                  web-app installer, systemd units, Node-RED flow + guide
```

Adding a screen: subclass `screens.base.Screen`, implement `draw_content` and
`handle_tap`, set a `glyph` (see `pictograms.py`), and add it to
`screens/__init__.py::SCREENS`.

## Roadmap

Phase 2/3 (Kali pentest tooling, RTL-SDR, ALFA Wi-Fi adapter) and Phase 4 (the
web-app launch terminal above) are tracked in [ROADMAP.md](ROADMAP.md) and
[PHASE2.md](PHASE2.md).

## License

[MIT](LICENSE). Scottina is created by Scott McLeslie for the benefit of all
living beings — feel free to share and contribute.
