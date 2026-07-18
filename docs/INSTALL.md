# Installing Scottina

From a fresh Kali Linux image on a Raspberry Pi 5 to a booting touchscreen
dashboard. The base install is one idempotent script; the optional backends are
separate phase scripts you run only if you want that screen.

## What you need

- **Raspberry Pi 5**
- **Kali Linux** (arm64 Pi image) freshly flashed to an SD card
- **3.5" ILI9486 SPI touchscreen** (480×320, ADS7846 resistive touch) seated on
  the GPIO header
- Network for the first run (the installer pulls apt packages)

Optional dongles add their own tiles when plugged in — RTL-SDR, a second Wi-Fi
adapter (ALFA), a CAN interface / CanTick, GPS, an FX2LP logic analyzer,
USB-serial adapters. None are required for the base install.

## Base install

Clone the repo anywhere and run the installer as root:

```bash
git clone https://github.com/scottmclesly/Scottina.git
cd Scottina
sudo setup/install.sh
```

`setup/install.sh` is idempotent — safe to re-run — and does exactly this:

1. Installs apt dependencies: `python3-pil`, `python3-numpy`, `python3-evdev`,
   `network-manager` (`nmcli`), `arp-scan`, `fbset` (`con2fbmap`), and
   `libraspberrypi-bin` (`vcgencmd`).
2. Copies the tree to `/opt/kilodash` (skipped if you're already running it from
   there; per-device `config.json`, `captures/`, and imported `tables/` are
   never clobbered).
3. Adds the display overlay to `/boot/firmware/config.txt` **only if absent**,
   backing the file up to `config.txt.kilodash-bak.<timestamp>` first:

   ```
   dtparam=spi=on
   dtoverlay=piscreen,drm,rotate=90
   ```

4. Installs, `daemon-reload`s, and `enable --now`s the `kilodash` systemd unit.

### Reboot (first time only)

The `rotate=90` overlay is the one display knob that needs a reboot to take
effect. After the first install:

```bash
sudo reboot
```

On reboot you should get the boot splash, then the Home tile grid. Touch axes
are handled in software — if taps land in the wrong place, calibrate from
**Settings → Touch** (see the README's *Touch calibration* section); you never
edit the overlay for that.

### Verify

```bash
systemctl status kilodash      # should be active (running)
journalctl -u kilodash -f      # live logs
```

## Optional backends (phase scripts)

Each is idempotent and installs only what that screen needs. Run the ones you
want, as root, from the repo:

| Script | Adds | Install if you want… |
|---|---|---|
| [`setup/install-phase4.sh`](../setup/install-phase4.sh) | Node-RED, AIS-catcher (RX), Signal K + their units | the web-app launch terminals (Kismet/Node-RED/AIS/Signal K) |
| [`setup/install-logic-analyzer.sh`](../setup/install-logic-analyzer.sh) | `sigrok-cli` + fx2lafw firmware + udev/group setup | the **Logic** screen (FX2LP capture + decode) |
| [`setup/install-gps.sh`](../setup/install-gps.sh) | gpsd + chrony + udev pin | the **GPS** screen (time authority, sky plot, N2K GNSS source) |
| [`setup/install-tables.sh`](../setup/install-tables.sh) | the on-device table-converter service | the **Tables** converter (vendor PDF → PGN tables) |
| [`setup/install-microkvm.sh`](../setup/install-microkvm.sh) | BlueZ + meshtastic-python + config scaffolding | the off-grid **Micro KVM** command plane (see [MICROKVM.md](MICROKVM.md)) |

Kismet, the RTL-SDR tools, and the ALFA Wi-Fi tooling are the Kali pentest/SDR
packages (Phase 2/3) — install those per [ROADMAP.md](../To-DoLists/ROADMAP.md)
and [PHASE2.md](../To-DoLists/PHASE2.md).

## Notes & gotchas

- **`config.txt` location.** The installer targets `/boot/firmware/config.txt`
  (current Pi OS / Kali layout). If your image still uses `/boot/config.txt`,
  the script warns and prints the two overlay lines to add by hand.
- **No controllable backlight.** This panel exposes no `/sys/class/backlight`
  node, so "dimming" is a software screensaver. If you later wire a PWM
  backlight, Scottina auto-detects the sysfs node.
- **Runs as root** — the app owns `/dev/fb0`, reads the ADS7846 evdev node, and
  drives `nmcli`/`arp-scan` (raw sockets). That's why the unit is `User=root`.
- **Re-running is safe.** The overlay line is added only once, `config.txt` is
  backed up only when actually changed, and apt/systemd steps no-op when already
  satisfied.
