# RTL-SDR — user guide

Scottina's **RTL-SDR** screen turns an RTL2832U dongle into a five-second
"what's transmitting nearby?" check: sweep a band for energy, decode real ISM
packets and name the device, or grab a raw IQ capture for offline work. The
RTL-SDR is **receive-only** — there is no replay or transmit path anywhere on
this screen.

The **RTL-SDR tile appears on Home only while the dongle is plugged in** (and
disappears when you pull it), like the other device tiles. It needs the
`rtl-sdr` tools (`rtl_power`, `rtl_433`, `rtl_sdr`) installed.

---

## The screen

Tap the **RTL-SDR** tile. Top to bottom:

| Control | What it does |
|---|---|
| **‹ band ›** | Cycle the preset band (see table below). |
| **"likely:" hint** | A per-band knowledge note — what typically lives here and how to go deeper. |
| **Results area** | The spectrum bar-graph (Scan) or the decoded-device list (Identify). |
| **Status line** | Current phase / peak frequency / decode count. |
| **Scan** | `rtl_power` sweep of the band → spectrum + a marked peak. |
| **Identify** | `rtl_433` listens at the band's ISM centre (~6 s) and decodes packets, naming the device. Disabled on bands with no decoder. |
| **Capture IQ** | Records ~4 s of raw IQ at the peak (or the band centre) to a `.cu8` file. |

## The preset bands

| Band | Range | Identify? | Likely traffic |
|---|---|---|---|
| **433 ISM** | 433–435 MHz | ✓ | Gate/car remotes, weather & temp sensors, TPMS, doorbells |
| **315 ISM** | 314.5–315.7 MHz | ✓ | Car key fobs, garage/gate remotes, TPMS (North America) |
| **868 ISM** | 868–870 MHz | ✓ | EU ISM: LoRa/Meshtastic (not decodable), meters, sensors |
| **915 ISM** | 914–916 MHz | ✓ | US ISM: LoRa (not decodable), industrial sensors |
| **FM bcast** | 88–108 MHz | — | Broadcast FM (listen with `rtl_fm`) |
| **Airband** | 118–137 MHz | — | Aircraft AM voice |
| **ADS-B** | 1089–1091 MHz | — | Aircraft transponders @ 1090 MHz (use `dump1090`) |

"Identify" is only offered where `rtl_433` has a decode centre — the other bands
show the hint instead and point you at the right dedicated tool.

## Reading the results

- **Spectrum (Scan):** a bar per frequency slice, taller/greener = more energy.
  The white vertical line marks the peak; the status line names its frequency.
  A flat floor means nothing's transmitting in that band right now.
- **Decoded list (Identify):** one row per distinct device — the model name
  (e.g. a weather-station or TPMS type) plus a few decoded fields (id,
  temperature, battery, button code…). Duplicate transmissions are collapsed.

## Every IQ capture is saved

**Capture IQ** writes to:

```
/opt/kilodash/captures/iq_<kHz>_YYYYmmdd-HHMMSS.cu8
```

at 2.048 Msps for ~4 s. Pull it off with the [Files](FILES.md) screen or over
SSH and analyse it in GNU Radio / inspectrum / `rtl_433 -r` on a laptop. The
capture targets the last Scan's peak if you have one, otherwise the band's
decode centre.

## Typical sessions

**"Is my 433 MHz sensor transmitting?"**
Band **433 ISM**, tap **Scan** — a peak means something's radiating. Then tap
**Identify**; if it's a supported protocol the model and its fields appear.

**"What's on the air around here?"**
Step through the ISM bands with **Scan**; where you see energy, try
**Identify** to name it.

**"Grab a sample for later."**
Scan to find the peak, then **Capture IQ**, and open the `.cu8` on a laptop.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No RTL-SDR tile on Home | Dongle not detected — check `lsusb` for an RTL2832U (`0bda:2838`/`2832`). Re-seat it. |
| "No packet decoder for this band" | That band (FM/Airband/ADS-B) has no `rtl_433` decoder — use the tool named in the hint. |
| "no known packets — see band note" | Nothing decodable was heard in the window. LoRa/Meshtastic on 868/915 is **not** decodable by `rtl_433` by design. |
| Empty spectrum | Nothing transmitting, wrong band, or antenna not connected. |
| Captures fail | Confirm `rtl_sdr` is installed and the dongle isn't held by another process. |
