# Scottina roadmap — Phases 2 & 3

Phase 1 (shipped): core UI, LAN scan, Wi-Fi scan/connect, Pi health, settings,
boot autostart.

Phases 2–3 turn Scottina into a field tool using the two USB radios and the
Kali toolset already on the box. **Everything offensive here assumes you own the
network or have written authorization** — Scottina gates those actions behind an
explicit confirm and an "authorized use" switch.

---

## Phase 2 — what the hardware unlocks

### ALFA AWUS036ACM (MediaTek MT7612U, dual-band AC1200)
The important part: this chipset does **monitor mode + packet injection on both
2.4 GHz and 5 GHz**, with the in-kernel `mt76x2u` driver (works out-of-the-box on
Kali). Strategy: keep the Pi's built-in `wlan0` **managed** (your connectivity),
and use the ALFA as `wlan1` for monitor mode so you never drop your own link.

Unlocks: full-band Wi-Fi recon, WPA/WPA2 handshake + PMKID capture, deauth
testing, client tracking, Kismet wardriving, and (advanced) rogue-AP / evil-twin.

### RTL-SDR Nooelec NESDR Nano 3 (RTL2832U + R820T2, RX 24–1766 MHz)
Receive-only, but that covers a lot. Unlocks: live spectrum/waterfall, ADS-B
aircraft (1090 MHz), the whole 433/868/915 MHz ISM zoo via `rtl_433` (weather
stations, TPMS, doorbells, remotes), FM/NBFM listening, pagers (POCSAG) and
ACARS, NOAA weather-sat, and general "what's transmitting near me" sweeps.

### Software to add (not yet installed)
`rtl-sdr` (rtl_test/rtl_power/rtl_fm), `rtl-433`, `dump1090-fa` (or mutability),
`hcxdumptool`/`hcxtools`, optionally `soapysdr` + `gqrx`-headless, `gr-gsm`.
Present already: `kismet`, `aircrack-ng`, `airmon-ng`, `nmap`, `arp-scan`,
`tshark`.

---

## Candidate screens

### A. Wi-Fi recon / attack (ALFA, monitor mode)
| # | Screen | Backend | Notes |
|---|---|---|---|
| A1 | **Monitor toggle** — put ALFA in/out of monitor, pick band/channel | `airmon-ng`, `iw` | prerequisite for the rest |
| A2 | **Airodump live** — scrolling AP + client list (BSSID, chan, enc, signal, #clients) | `airodump-ng` CSV tail | the flagship recon view |
| A3 | **Handshake / PMKID capture** — pick AP, capture to .pcap, optional deauth to speed it | `hcxdumptool` / `aireplay-ng` | gated behind authorized-use |
| A4 | **Kismet wardrive** — start/stop, live AP count, optional GPS | `kismet` REST API | good passive mode |
| A5 | **Rogue AP / evil twin** (advanced) | `hostapd` + `dnsmasq` | gated, confirm-twice |

### B. SDR (RTL-SDR)
| # | Screen | Backend | Notes |
|---|---|---|---|
| B1 | **Spectrum / waterfall** — sweep a tunable band, render heatmap | `rtl_power` | fits the 320-wide panel well |
| B2 | **ADS-B aircraft** — nearby planes: callsign, alt, speed, distance | `dump1090 --net` JSON | very satisfying demo |
| B3 | **rtl_433 sensors** — decode ISM devices around you | `rtl_433 -F json` | weather/TPMS/doorbells |
| B4 | **FM / scanner** — tune & listen | `rtl_fm` + `aplay` | needs audio out |
| B5 | **Pagers / ACARS** (optional) | `multimon-ng`, `acarsdec` | niche |

### C. Network recon (no extra hardware)
| # | Screen | Backend | Notes |
|---|---|---|---|
| C1 | **Host detail / port scan** — tap a LAN host → service scan | `nmap -sV` | natural extension of LAN Scan |
| C2 | **Ping / traceroute / speedtest** | `mtr`, `speedtest` | connectivity triage |
| C3 | **Packet capture** — short tshark grab to .pcap | `tshark` | share off-box later |
| C4 | **Bluetooth scan** | `bluetoothctl` / `btmgmt` | if BT is used |

### D. System / utility
| # | Screen | Backend | Notes |
|---|---|---|---|
| D1 | **Services** — SSH / VPN / Tailscale / hostapd toggles | `systemctl`, `nmcli` | |
| D2 | **GPS** — fix, sats, coords | `gpsd` | pairs with A4/wardriving |
| D3 | **Log viewer** — tail journal / capture files | `journalctl` | |

---

## Phase 3 — implementation plan

Ordered by value-to-effort. Each milestone is independently shippable.

### Framework work first (needed before the fun screens)
1. **Long-running tool manager.** Extend `system.Task` into a `Service` that
   spawns a process (airodump, kismet, dump1090, rtl_power), tails its
   JSON/CSV/stdout, parses incrementally, and exposes latest state + start/stop.
   Everything in Phase 3 depends on this.
2. **Screen grouping / launcher.** 5 screens swipe fine; ~18 do not. Add a Home
   **launcher grid** (tap a tile to jump to a screen) and keep swipe for
   left/right within the current group. Groups: *Net · Wi-Fi · SDR · System*.
   Vertical swipe switches group, horizontal swipes within it.
3. **Interface arbitration + safety gate.** Auto-detect the ALFA as the monitor
   NIC (keep `wlan0` for uplink); add the global **"authorized use"** toggle and
   a two-step confirm dialog reused by every offensive action.
4. **Radio presence detection.** Detect RTL-SDR (`rtl_test`) and ALFA
   (`iw dev` / USB id `0e8d:7612`) at runtime; grey out screens whose hardware
   isn't plugged in.

### Milestone 1 — Wi-Fi recon (highest value, hardware you'll use most)
A1 Monitor toggle → A2 Airodump live → A4 Kismet. Ship A3 (handshake/deauth)
behind the safety gate once A1/A2 are solid.

### Milestone 2 — SDR quick wins
B2 ADS-B (easy, self-contained JSON) → B3 rtl_433 sensors → B1 spectrum
waterfall (most rendering work). B4/B5 only if you want audio.

### Milestone 3 — Network depth
C1 host detail/port scan (extends LAN Scan — tap a host you already list) → C2
ping/traceroute/speedtest → C3 capture.

### Milestone 4 — System polish
D1 service toggles → D2 GPS (unlocks wardriving maps) → D3 log viewer.

### Cross-cutting
- **Install script** (`setup/install-phase2.sh`) to pull `rtl-sdr rtl-433
  dump1090-fa hcxdumptool hcxtools` and udev rules for non-root SDR access.
- **Capture output dir** (`/opt/kilodash/captures/`) for .pcap/.csv, surfaced in
  the log viewer for pulling off-box over SSH/scp.
- **Per-screen "hardware required" metadata** so the launcher can grey/hide.

### Suggested first slice to build next
A1 + A2 (monitor toggle + live airodump) on top of the new `Service` manager and
the safety gate. That’s the smallest change that makes the ALFA useful and
exercises every framework piece the rest of Phase 3 reuses.

---

## Phase 4 — Web-app launch terminal  🟡 in progress

Beyond the built-in scanners, Scottina also fronts *bigger packages that ship
their own browser UI*. Opening one of these screens **launches the app**, waits
for it to actually answer, and shows the **URL:port** to open the full UI from a
phone/laptop — plus a compact native panel of app-specific controls + feedback.

### Framework (built) — `webapp.py` + `screens/webapp_base.py`
- **`WebApp`** (`kilodash/webapp.py`): supervises one app. Launch via a systemd
  unit *or* a `Popen` argv; **positive confirmation is a TCP probe of the port**
  (green only once it truly answers, not just "spawned"). `launch()` first probes
  and *adopts* an already-running instance instead of duplicating it. State
  machine: `stopped → starting → up → error`, with process-exit and ready-timeout
  detection. Also stdlib-only HTTP helpers (`http_json`/`http_post`) so screens
  can read their app's REST/JSON endpoints — first HTTP code in the project.
- **`WebAppScreen`** base: auto-launch on open, the status banner + URL card +
  Start/Stop, and hooks (`build_start_cmd`, `poll_app`, `draw_app`,
  `handle_app_tap`). Tiles **auto-hide until the app is installed** via a new
  `Screen.available()` gate (launcher `_visible()` now checks it too).

### Apps (this pass)
| App | Port | Backend | Panel | Status |
|---|---|---|---|---|
| **Kismet** | 2501 | `kismet --no-ncurses` | Sniff on/off (adds ALFA as uplink-safe monitor source + uplink watchdog); live peer list colour-coded by device type via REST | installed on box ✅ |
| **Node-RED** | 1880 | `nodered.service` | 4 assignable debug feedback fields + 4 trigger buttons over an HTTP contract | **installed ✅** (node v24 via apt, node-red via npm, systemd unit, verified serving :1880 in ~3s) |
| **AIS** | 8100 | `AIS-catcher -N 8100` (RX) + `ais-simulator` (TX) | vessel/msg feedback; own-MMSI field; gated Transmit-test toggle | **not packaged** — no apt/npm build; source compile blocked pending explicit user OK |

### Integration contracts / notes (define per app as we go)
- **Kismet peers**: reads `/devices/views/all/last-time/-30/devices.json` with the
  credentials Kismet auto-writes to `~/.kismet/kismet_httpd.conf`. **Needs Kismet's
  one-time web login done first**; until then the panel shows a hint and the full
  list stays in the web UI. Colour: AP=accent, client=ok, BT=warn. *On-hardware
  TODO: confirm the exact device-summary endpoint/fields on this Kismet build.*
- **Node-RED**: Scottina speaks a tiny contract you wire in a flow —
  `GET /kilodash/state → {fields:[{label,value}×4], buttons:[{label}×4]}` and
  `POST /kilodash/btn/1..4`. Feedback fields read flow context `f1..f4` (labels
  `f1_label..`), so any node feeds a field by writing that key. **Wire-up guide:
  `setup/NODE-RED.md`; import `setup/nodered-kilodash-flow.json`** (ships a live
  demo). Until the flow exists, fields show "—" and buttons post harmlessly.
- **AIS RX**: AIS-catcher web UI; vessel count from `/geojson` features, rate from
  `/stat.json` (best-effort key match). Install: build AIS-catcher, gate is the
  `AIS-catcher`/`ais-catcher` binary + RTL-SDR present.
- **AIS TX (bench receiver testing)**: purpose is proving a **robot's AIS receiver**
  decodes frames — contained indoor test, minimal power into a small/dummy antenna,
  never reaching real AIS traffic. **The RTL-SDR is RX-only** (same wall as the SDR
  replay caveat in PHASE2), so TX needs TX-capable SDR hardware — **HackRF One**
  (best fit), PlutoSDR, or LimeSDR — plus a frame generator (`ais-simulator`,
  GNU Radio + gr-osmosdr). The screen already has the **own-MMSI field** (persisted
  in `config.ais_own_mmsi`) and a **Transmit-test toggle**; it stays disabled until
  `_tx_ready()` sees both the tool and a TX radio, and every transmit is **armed**
  (tap-to-arm, tap-again-to-fire). *TODO once HackRF is on the box: finalise the
  `TX_CMD` invocation (seed MMSI/position) and surface ais-simulator's own web UI.*

### Candidate next web apps
Extended CAN screeen

### Install / packaging TODO
- `setup/install-phase4.sh`: node-red, AIS-catcher, dump1090-fa, and (optional,
  for TX) HackRF host tools + ais-simulator + GNU Radio; udev rules for HackRF.
- Consider persisting per-app settings (ports, Node-RED field/button labels) in
  `config.json` like `ais_own_mmsi`.
