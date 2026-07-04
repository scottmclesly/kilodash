# kilodash

A fingertip control panel for a Raspberry Pi 5 running Kali Linux, driving a
3.5" ILI9486 SPI touchscreen (480×320, ADS7846 resistive touch). It boots
straight to a swipe-navigated dashboard — no keyboard, no mouse, no X server.

![screens](docs/screens.png)

## Why it works the way it does

- **Renders directly to `/dev/fb0`** (the `ili9486drmfb` framebuffer) with PIL +
  numpy, and **reads touch straight from the ADS7846 evdev node**. No SDL, no
  Xorg, nothing that can lose the DRM device. This is the one approach that was
  reliable on this panel.
- **Touch orientation is a runtime setting**, not a compile-time constant, so the
  panel can be re-calibrated from the Settings screen or by editing
  `config.json` over SSH — no reboot, no code edits.
- **Everything tunable lives in `config.py::DEFAULTS`.** The Settings screen
  renders whatever it finds there, so adding a knob is a one-line change.

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
> image. If you later wire a PWM backlight, kilodash auto-detects the sysfs node
> and uses it.

## Install

```bash
sudo cp /opt/kilodash/kilodash.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kilodash
```

Runs as root (needs `/dev/fb0`, evdev, `nmcli`, and `arp-scan`'s raw sockets).

Dependencies (already present on this build): `python3-pygame` is **not**
required; kilodash uses `python3-pil`, `python3-numpy`, `python3-evdev`, plus
`nmcli`, `arp-scan`, and `vcgencmd`.

## Using it

- **Swipe left / right** to move between screens (dots in the header show
  position).
- **Tap** buttons, list rows, and toggles. Targets are ≥44 px for fingers.
- Screens: **Home** · **LAN Scan** · **Wi-Fi** · **Pi Health** · **Settings**.
- **Wi-Fi**: tap a network to connect. Secured networks open an on-screen
  keyboard for the password; saved/open networks connect immediately.
- **Dimming**: after the idle timeout (default 10 min) the screen darkens; the
  next tap only wakes it.
- Quit while testing from a USB keyboard with **ESC** or **q**.

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

## Layout

```
run.py                  entrypoint
kilodash/
  app.py                main loop, gestures, transitions, dimming, keyboard overlay
  framebuffer.py        /dev/fb0 pack + blit (RGB565 / XRGB8888)
  touch.py              ADS7846 evdev reader + axis mapping
  system.py             network / wifi / lan / health data (+ background Task)
  webapp.py             launch/confirm/supervise third-party web apps (Phase 4)
  widgets.py            Button, on-screen Keyboard, helpers
  theme.py              palettes + font cache
  config.py             settings schema + JSON persistence
  screens/              one file per screen
legacy/                 the fbdash.py / kilo_dash.py prototypes this grew from
```

Adding a screen: subclass `screens.base.Screen`, implement `draw_content` and
`handle_tap`, and add it to `screens/__init__.py::SCREENS`.

## Web-app launch terminal

Beyond the built-in screens, kilodash also fronts **bigger packages that serve
their own browser UI**. Opening one of these screens *launches the app*, waits
until its port actually answers (a real "✓ web UI confirmed", not just
"spawned"), and shows the **URL:port** to open the full interface from a phone or
laptop — plus a compact native panel of controls and live feedback.

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

The framework lives in `kilodash/webapp.py` (a `WebApp` process/port supervisor
+ stdlib HTTP helpers) and `screens/webapp_base.py` (`WebAppScreen`). To add
another web app, subclass `WebAppScreen`, set `app_name`/`port`/`service` (or
`start_cmd`), and override the `draw_app`/`handle_app_tap`/`poll_app` hooks.

## Roadmap

Phase 2/3 (Kali pentest tooling, RTL-SDR, ALFA Wi-Fi adapter) and Phase 4 (the
web-app launch terminal above) are tracked in [ROADMAP.md](ROADMAP.md).
