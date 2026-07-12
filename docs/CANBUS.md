# CAN Bus — user guide

Scottina's **CAN Bus** screen brings a CAN interface up, shows a **live RX-frame
counter**, and logs traffic to a timestamped file — for a USB dongle wired to
the bus, or a **CanTick** ESP32 box that tunnels a remote bus over Wi-Fi. It
also hosts one-time **provisioning** for a CanTick and a **heartbeat health
card** for the WiFi link.

The **CAN Bus tile appears on Home while a CAN dongle is present** (CANable /
`gs_usb` / `slcan`) — and, once the CanTick WiFi link is enabled, while a
CanTick is dialled in. Needs `can-utils` (`candump`) and `iproute2`.

---

## The screen

Tap the **CAN Bus** tile. Top to bottom:

| Control | What it does |
|---|---|
| **Interface card** | The selected CAN interface (`can0` / `slcan0`), green when found. |
| **CanTick chip** | Tap to toggle the WiFi-CAN link on/off. Dot colour = link state (grey off, amber listening/backing-off, green up). |
| **‹ bitrate ›** | Pick the bus bitrate: 1M / 500k / 250k / 125k / 100k / 50k (default 500k). |
| **Autodetect** | Listens (listen-only) at each common bitrate and keeps the one that yields frames. |
| **Provision** | Appears when a CanTick is on USB — pushes Wi-Fi creds + bus settings to it (see below). |
| **Start / Stop logging** | Brings the interface up and records to a `candump` log; Stop closes it. |
| **Status line** | What's happening right now. |
| **RX FRAMES card** | Live kernel RX counter + frames/s. Green dot = frames seen in the last second; "idle" when the bus is quiet. |
| **CanTick health card** | Present when the WiFi link is enabled — see [CanTick](#cantick--can-over-wifi). |

The RX counter is the responsive part: it ticks at ~20 Hz while frames flow and
automatically drops back to a slow refresh on a silent bus, so a wedged or
unpowered bus never spins the CPU.

## Every log is saved

**Start logging** writes a standard `candump` log to:

```
/opt/kilodash/captures/can_YYYYmmdd-HHMMSS.log
```

Offload it with the [Files](FILES.md) screen or over SSH and open it in
SavvyCAN / Wireshark / `canplayer` on a laptop. Decoding to signals uses the
DBC/NMEA2000 tables in `/opt/kilodash/tables/` — carry those on a USB stick
with [Files](FILES.md#decode-tables-what-gets-imported).

## Typical sessions

**Sniff a wired bus:**
Plug in a CANable, open **CAN Bus**. If you know the rate, pick it with the
bitrate arrows; otherwise tap **Autodetect**. Tap **Start logging** — the RX
counter climbs and frames/s shows the load. **Stop logging** to close the file.

**"Is this bus even alive / what speed?"**
Tap **Autodetect**. It reports the detected rate, or "No frames — bus idle or
unpowered" if it heard nothing at any rate.

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

- **Listen-only for CanTick is enforced on the device**, not just the UI.
- The bitrate for a `slcan`/CanTick link is fixed at attach time by `slcand`;
  the bitrate arrows apply to native `can*` interfaces.
- Scope is diagnostics and normal CAN participation — see
  [`To-DoLists/PROTOCOL.md`](../To-DoLists/PROTOCOL.md).
