# kilodash Phase 2 — Dongle integration

Turns kilodash into a hotplug tool bench: plug a supported USB device in and its
tile appears on the Home grid; unplug it and the tile (and any running capture)
goes away. Detection is cheap sysfs polling in [devices.py](kilodash/devices.py);
each device maps to a screen via its `device_key`.

## Hardware detected on this Pi

| Device | Bus id / iface | Status | Notes |
|---|---|---|---|
| RTL-SDR Nooelec NESDR Nano 3 | USB `0bda:2838` | **working** | R820T tuner, 24–1766 MHz, **RX only** |
| ALFA AWUS036ACM | USB `0e8d:7612` → `wlan1` | **working** | MT7612U, monitor + injection capable |
| DSD TECH CANable | USB `1d50:606f`→`canN` / slcan | detects on plug | not currently connected |
| FTDI / CH340 / CP210x serial | `/dev/ttyUSB*` | detects on plug | "DTFI" adapters |
| Onboard I2C (i2c-1) | `/dev/i2c-1` | **enabled — needs one reboot** | `dtparam=i2c_arm=on` added |

The Wi-Fi ON button was removed from Home to make room; the toggle still lives on
the Wi-Fi screen. Home now holds up to 8+ tiles and reflows automatically.

---

## What's built now (stage 1 of each)

### RTL-SDR — frequency scanner + capture  ✅
[screens/sdr.py](kilodash/screens/sdr.py). Flipper-style: pick a band preset
(433/315/868 ISM, FM, airband, ADS-B, 2m/70cm ham), **Scan** runs an `rtl_power`
sweep and draws the live spectrum with a peak marker + readout, **Capture**
records raw IQ around the peak to `/opt/kilodash/captures/*.cu8`.

> **Replay is not possible with the RTL-SDR — it's receive-only hardware.** A
> Flipper can retransmit because it has a TX chip; the RTL2832U cannot transmit
> at all. To add replay later you need TX hardware: **HackRF One** (full SDR TX),
> a **CC1101** sub-GHz module (cheap, 300–928 MHz OOK/FSK — closest to Flipper),
> or **rpitx** (GPIO-pin crude TX, RX-band dependent). Capture works today; replay
> is gated on adding one of those.

### ALFA — passive WiFi sniffer  ✅
[screens/wifisniff.py](kilodash/screens/wifisniff.py). Puts `wlan1` in monitor
mode (leaves the Pi's `wlan0` uplink untouched), channel-hops with `airodump-ng`,
and lists every AP (SSID, channel, encryption tag, signal) and client (MAC,
probe/associated BSSID, signal) it hears. Start/Stop; auto-restores managed mode
on exit.

### CAN bus  ✅ (works when plugged)
[screens/canbus.py](kilodash/screens/canbus.py). Bitrate picker, **Autodetect
bitrate** (listen-only sweep of 1M→50k, keeps the rate that yields frames),
**Start/Stop logging** to `/opt/kilodash/captures/can_*.log` via `candump -l`.

### I2C scanner  ✅ (after reboot)
[screens/i2cscan.py](kilodash/screens/i2cscan.py). `i2cdetect` on i2c-1, lists
responding addresses with best-guess part names (BME280, SSD1306, DS3231…).

### USB-serial monitor  ✅ (works when plugged)
[screens/serialmon.py](kilodash/screens/serialmon.py). Port + baud picker,
read-only live view of a device's UART/debug output.

---

## Roadmap — deepening each (stages 2/3)

**RTL-SDR**
- Waterfall history view; frequency bookmarks/presets you can save.
- `rtl_433` **decode mode** — live-decode 433/315/868 sensors, TPMS, remotes,
  doorbells into a readable event list (very Flipper-like).
- `dump1090` **ADS-B** screen — nearby aircraft (callsign, altitude, distance).
- NOAA APT weather-satellite capture.
- **TX/replay** once TX hardware is added (see caveat above).

**ALFA WiFi**
- Kismet backend for richer device tracking + logging.
- Per-frame protocol dissection via `tshark` (EAPOL, mDNS, WPS tags).
- **Handshake / PMKID capture** and **deauth** — behind an "authorized use"
  confirm gate (own networks / pentest only).
- GPS dongle → wardriving with coordinates.

**CAN**
- Live frame decode table + `cansniffer` grouped view.
- DBC signal decoding; OBD-II PID dashboard (RPM, speed, temps).
- **Frame replay / injection** behind the safety gate.

**I2C / buses**
- Per-address register read/write and known-device probes.
- SPI device scan; onboard bus health.

**Serial**
- Two-way console (TX), line logging, baud autodetect.

---

## Things worth adding that you didn't list

- **Bluetooth / BLE scanner** — the Pi 5 has onboard BT; `bluetoothctl`/`btmgmt`
  can list nearby BLE beacons, names, RSSI, and services. No dongle needed —
  could be an always-on tile.
- **NFC/RFID** — a PN532 module (I2C or UART) gives Flipper-style 13.56 MHz
  read/emulate; slots right into the I2C or Serial plumbing already here.
- **USB inspector** — a general "what's plugged in" tile (lsusb + descriptors),
  useful when a dongle isn't recognized.
- **nmap host detail** — tap a host on the LAN Scan screen → service/port scan.
- **GPS** — pairs with wardriving and geotagging captures.

## Safety gating (applies to the offensive stage-2/3 items)

Deauth, WiFi injection, and CAN frame injection will sit behind a global
**"authorized use" toggle + two-step confirm**, reused across every screen, so
they can't be triggered by accident. Passive sniffing/scanning (everything built
today) needs no gate.

## Captures

Everything lands in `/opt/kilodash/captures/` (IQ, pcap/csv, CAN logs) for
pulling off-box over scp/ssh.
