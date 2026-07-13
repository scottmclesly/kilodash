# CAN — user guide

Scottina's **CAN** screen is **raw-bus forensics** for proprietary traffic:
unknown IDs, unknown semantics, you reverse-engineering. It watches the bus
through its own SocketCAN socket (a USB dongle wired to the bus, or a
**CanTick** ESP32 box tunnelling a remote bus over Wi-Fi), aggregates what it
hears per arbitration ID, alerts on the byte positions you're watching, and
exports replayable logs. Traffic with *known* semantics belongs on the
sibling [NMEA2K screen](NMEA2K.md), which decodes against PGN tables.

The **CAN tile appears on Home while a CAN dongle is present** (CANable /
`gs_usb` / `slcan`) — and, once the CanTick WiFi link is enabled, while a
CanTick is dialled in. Needs `iproute2` (+ `can-utils` for continuous
logging).

**Scope: diagnostics only — this screen has no TX surface at all.** Its
socket only ever receives; the single system-wide TX exception (heartbeat /
reply behavior required by bus participation, e.g. NMEA2000 address claim)
lives in the link layer, never in any control here, and the test suite
AST-scans the screen every run to keep it that way.

---

## The screen

Tap the **CAN** tile. The interface card (with the **CanTick chip** — tap to
toggle the WiFi link; grey off, amber listening, green up) sits above two
tabs:

### Bus tab (the working view)

| Element | What it does |
|---|---|
| **IDs / Live chip** | Toggle the seen-IDs table vs. a candump-style live list (newest first). |
| **W chip** | Filter to IDs that have watches. |
| **Δ chip** | Filter to frames/IDs whose bytes changed. |
| **ID chip** | Shows when an exact-ID filter is set (from the byte grid); tap to clear. |
| **⚠ badge** | Total watch hits; lights up while a watch is firing. Alerts are always a badge + row flash — never a modal over a live bus view. |
| **Seen-IDs table** | One row per arbitration ID: id, frame count, rate, last payload with **changed bytes highlighted** since the previous frame, watched positions underlined. Alerting rows flash their border. Tap a row → the byte grid. |
| **Save ring** | Exports the ring buffer (bounded, 50 000 frames) through the current filters to a candump `.log`. |

**The byte grid** (tap a row): eight cells show the last payload byte per
position, watch markers (`Δ` change / `=XX` match), and hit counts. Tap a
byte, then:

- **Alert: change** — fire whenever the byte at (ID, position) *differs*
  from its last-seen value (the primary RE tool: "which byte moves when I
  press the button?").
- **Alert: value…** — fire when the byte *becomes* a hex value you enter.
- **Remove watch** — with its hit count.
- **Filter this ID** — pin the whole Bus tab (and ring exports) to this ID.

### Setup tab

| Control | What it does |
|---|---|
| **‹ bitrate ›** | Pick the bus bitrate: 1M / 500k / 250k / 125k / 100k / 50k (default 500k). |
| **Autodetect** | Listens (listen-only) at each common bitrate and keeps the one that yields frames. |
| **Provision** | Appears when a CanTick is on USB — pushes Wi-Fi creds + bus settings to it (see below). |
| **Start / Stop logging** | Brings the interface up and records continuously to a `candump` log; Stop closes it. |
| **RX FRAMES card** | Live kernel RX counter + frames/s. Green dot = frames seen in the last second. |
| **CanTick health card** | Present when the WiFi link is enabled — see [CanTick](#cantick--can-over-wifi). |

The screen ticks fast (~10 Hz, repainting only the changed rows) while frames
flow and automatically drops back to a slow refresh on a silent bus, so a
wedged or unpowered bus never spins the CPU.

## Every log is saved

Both capture paths write standard candump `.log` format — replayable with
`canplayer`, loadable in SavvyCAN / Wireshark:

```
/opt/kilodash/captures/can_YYYYmmdd-HHMMSS.log       # Setup → Start logging (continuous)
/opt/kilodash/captures/canring_YYYYmmdd-HHMMSS.log   # Bus → Save ring (last 50k frames, filtered)
```

Offload them with the [Files](FILES.md) screen or over SSH. Decoding to
signals uses the NMEA2000/DBC tables in `/opt/kilodash/tables/`
([TABLES.md](../TABLES.md)) — live on the [NMEA2K screen](NMEA2K.md), or
carry tables on a USB stick with
[Files](FILES.md#decode-tables-what-gets-imported).

## Typical sessions

**"Which byte is the headlight switch?"**
Open **CAN**, watch the seen-IDs table settle, flip the switch and look for
the **Δ highlight**. Tap the suspect row, tap the moving byte, **Alert:
change** — now every flip flashes the row and bumps the hit counter. Pin it
with **Filter this ID**, then **Save ring** for the laptop.

**Sniff a wired bus:**
Plug in a CANable, open **CAN**. If you know the rate, pick it on **Setup**;
otherwise tap **Autodetect**. **Start logging** for a continuous file, or
just work the Bus tab and **Save ring** when something interesting happened
— the ring was already recording.

**"Is this bus even alive / what speed?"**
Setup → **Autodetect**. It reports the detected rate, or "No frames — bus
idle or unpowered" if it heard nothing at any rate.

## CanTick — CAN over WiFi

A CanTick is a small ESP32 box that clamps onto a CAN bus somewhere Scottina
isn't and tunnels it over Wi-Fi. The model is deliberately boring: **it just
appears as an ordinary `slcan0`.** While the CAN screen is open with the link
enabled, Scottina listens on TCP, a CanTick dials in, `slcand` attaches it as a
normal SocketCAN interface, and everything downstream (`candump`, Node-RED,
Signal K/canboatjs) reads `slcan0` unchanged. **Listen-only is enforced on the
device itself** and shown in the health card's `mode`.

The link is **supervised** — if the CanTick drops off Wi-Fi it's torn down and
re-armed so the next dial-in reconnects with no restart. Everything CanTick is
torn down when you leave the screen.

### Provisioning a CanTick (one-time)

1. Plug a factory-fresh CanTick into the Pi's USB. A **Provision** button
   appears next to Autodetect.
2. Set the **bitrate** you want the CanTick to use.
3. Tap **Provision**. Scottina pushes, over the USB serial port:
   - its *current* Wi-Fi credentials (primary slot),
   - a generated fallback-AP credential pair (fallback slot),
   - the bus bitrate and the listen-only flag,
   then verifies with a status read. **PSKs are never logged.**
4. Unplug. On its next boot the CanTick joins the network and dials in; enable
   the **CanTick** chip and it shows up as `slcan0`.

### The heartbeat health card

A read-only UDP heartbeat (every ~2 s) drives the card at the bottom:

- **device name · mode (normal / listen / closed) · RSSI**
- a **fresh/stale dot** (green when the heartbeat is current),
- live **rx/s**, and the **drop** counter,
- a loud red **DROP** badge when the drop counter is *rising* — the early
  warning that the bus is out-running the bridge.

### AP fallback (off-grid)

If the Pi has **no uplink at all** when the CAN screen opens, and a fallback PSK
was provisioned, Scottina raises a reversible WPA2 AP (`Scottina-CanTick`,
gateway `192.168.42.1`) on `wlan0` so a provisioned CanTick can still reach it
via its fallback slot. The AP is fully reversible — it's dropped the moment a
real uplink returns or you leave the screen, and `wlan0` is handed back to
NetworkManager. The ALFA (`wlan1`) is never touched. Needs `hostapd` +
`dnsmasq`; without them the fallback just reports itself unavailable.

The full wire protocol (slcan-over-TCP, heartbeat, provisioning, AP fallback)
is in [`To-DoLists/PROTOCOL.md`](../To-DoLists/PROTOCOL.md). Scope is
diagnostics + normal CAN participation only.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No CAN tile on Home | Dongle not detected — check `lsusb` and that `can0`/`slcan0` exists under `/sys/class/net`. |
| Interface shows "not found" | A `slcan` device needs `slcand` attached; for CanTick, enable the chip and wait for the dial-in. |
| Autodetect finds nothing | Bus idle or unpowered, or genuinely no CAN traffic — confirm the bus is live and correctly wired. |
| RX counter stuck at idle | No frames arriving. Check bitrate (try Autodetect), termination, and wiring. |
| "Provisioning failed: …" | Check the CanTick is the one on USB and its serial port is free; re-try. PSKs aren't logged, so the message is safe. |
| No CanTick heartbeat | Link not up yet, wrong network, or the device is off. The card shows the current link state. |
| Fallback AP never appears | You still have an uplink (fallback only triggers with *no* uplink), no fallback PSK was provisioned, or `hostapd`/`dnsmasq` aren't installed. |

## Limits (by design)

- **No TX surface.** No injection, replay, fuzzing, or arbitrary-frame TX is
  expressible anywhere in the UI — the screen's socket is receive-only and
  `tests/test_busmon.py` enforces it in code (allow-list + reject pass over
  the screen's AST). The heartbeat/reply exception lives in the link layer
  only.
- **Listen-only for CanTick is enforced on the device**, not just the UI.
- The bitrate for a `slcan`/CanTick link is fixed at attach time by `slcand`;
  the bitrate arrows apply to native `can*` interfaces.
- Watches and the ring buffer are session state — they survive tile-hopping
  but not a UI restart. Anything worth keeping: **Save ring**.
- Scope is diagnostics and normal CAN participation — see
  [`To-DoLists/PROTOCOL.md`](../To-DoLists/PROTOCOL.md).
