# LoRa / Meshtastic mesh — canonical radio config

The Scottina mesh: stock Meshtastic firmware + this one config, identical on
every node. This file IS the "note the choice so all nodes match" record —
if a value here changes, it changes for the whole roster at once, via
`tools/provision_mesh.sh`.

**Scope: diagnostics only.** The E-Stop LoRa diagnostic is a separate
raw-radio firmware on its own band (EByte 433) and is deliberately *not*
part of this mesh — nothing here touches it.

## The pinned radio config (every node, no exceptions)

| Setting | Value | Why |
|---|---|---|
| Region | **EU_433** | The T3s are the **433 MHz hardware variant**; EU_433 is Meshtastic's 433 band plan. A 433 front-end set to US-915 transmits nothing (bench fact 2026-07-17 — cost a full e2e run). Region/band mismatch = silent no-mesh, the #1 bring-up failure. Verify first, always. |
| Modem preset | **LONG_SLOW** | Alerts + short commands, not streaming: the slow preset buys link margin over water. Must be identical everywhere — a preset mismatch is as dead as a region mismatch. |
| Firmware | stock Meshtastic ≥ 2.5 | 2.5+ gives PKI remote admin (Prime governing the sensor node over the air). |

## Channels (PSK-encrypted, purpose-separated)

| Slot | Name | PSK | Purpose |
|---|---|---|---|
| 0 (primary) | `ScotTel` | `TEL_PSK` in `mesh-secrets.env` | Telemetry + pager: sensor metrics, CAN-trigger alerts from Light's companion. |
| 1 (secondary) | `ScotCmd` | `CMD_PSK` in `mesh-secrets.env` | **Command plane only** ([MICROKVM-PROTOCOL.md](../To-DoLists/MICROKVM-PROTOCOL.md)). Its PSK membership is the coarse auth boundary around execution — never shared with sensor chatter, never given to a node that doesn't command. |

PSKs are 256-bit, generated once by `tools/provision_mesh.sh` into
`/opt/kilodash/mesh-secrets.env` (git-ignored, per-boat). Which node carries
which channel:

| Node | ScotTel | ScotCmd |
|---|---|---|
| Prime radio (T3 #1) | ✔ | ✔ |
| Sensor node (T3 #2) | ✔ | — |
| Light's companion (ESP32) | ✔ | — |
| Pager/commander (phone) | ✔ | ✔ |

Expected node IDs (fill in at bench bring-up; the ScotCmd column feeds
`config.json → microkvm.allowed_nodes`):

| Node | Node ID | On ScotCmd? |
|---|---|---|
| Prime radio | `!ea244ad4` (BLE `E8:6B:EA:24:4A:D6`, TLORA_V2_1_1P6 **433 MHz**, fw 2.7.26) | yes (the executor's own radio) |
| Sensor node "Kate" | `!ea245c80` (BLE `E8:6B:EA:24:5C:82`, TLORA_V2_1_1P6 **433 MHz**) | **yes — dual duty**: sensor node AND the phone's pager/commander seat until a dedicated commander radio exists; her ID is in `microkvm.allowed_nodes`. Owner name kept as "Kate (KATE)" (deliberately named — the roster's "Scottina Sensor" was a placeholder). |
| Light companion | `!________` | no |
| Phone | rides Kate (no radio of its own) | via Kate |

E2E proven 2026-07-17: `status` / `snap` answered and `tile` correctly
rejected `disarmed` over the air (Kate → LoRa 433 → PRIM → BLE → executor);
remote admin read of Kate's telemetry interval via PRIM with the PKI admin
key. When a dedicated commander radio arrives: move ScotCmd + allowed_nodes
membership off Kate.

Prime radio public key (2026-07-16, feeds the sensor node's
`security.admin_key` for over-the-air admin):
`C+JFlde3fGzpUUMgVGRsDgbsraZwbmcG+BG3FsOfr2s=`

## Node roster + names

Set names deliberately before deploy — never ship the MAC-default cosmetic
name (the CanTick naming trap).

| Node | Long name | Short | Role |
|---|---|---|---|
| T3 #1 | `Scottina Prime Radio` | `PRIM` | BLE-linked to Prime; carries the command plane and relays pager/telemetry. WiFi **off** — Prime's WiFi is reserved for the web app; this node talks BLE only. |
| T3 #2 | `Scottina Sensor` | `SENS` | Standalone; exposed I2C port for drop-in sensors, remote-governed from Prime over the air. Doubles as Light's temporary UART companion during bench work. |
| ESP32 + LoRa | `Scottina Light Companion` | `LGHT` | Wired to Scottina Light over UART, Serial module in TEXTMSG mode. Light (SAMD51 — **cannot** run Meshtastic itself) formats CAN-trigger strings; this node puts them on `ScotTel`. |
| Phone | (Meshtastic app default) | — | Pager (alerts in) + off-grid commander (canned command frames out). |

## Sensor node specifics

- `telemetry.environment_measurement_enabled true` — Meshtastic
  **auto-detects supported I2C sensors at startup**: plug into the exposed
  I2C port, reboot the node, confirm it appears. Power metrics likewise
  (`power_measurement_enabled`) for voltage/current sensors.
- Send interval: **1800 s (30 min) default** — airtime is shared and
  duty-limited; loose telemetry starves the alerts and commands that
  actually matter. Tighten deliberately, per sensor, not by habit.
- **Remote admin from Prime:** the sensor node's `security.admin_key` is set
  to the Prime radio's public key, so Prime can enable/disable telemetry and
  change intervals **over the air** (`meshtastic --ble <prime> --dest
  '!<sensor>' --set …`). That's the "adequate user control" path for drop-in
  sensors.
- Exotic (unsupported) sensor → Meshtastic custom-I2C firmware boilerplate;
  that's a firmware sub-task, only if a real sensor forces it.

## Light's companion specifics

- Serial module: `serial.enabled true`, `serial.mode TEXTMSG`, 38400 baud;
  RX/TX pins per board silk (script defaults GPIO 13/14 — check before
  wiring).
- Wire Light ↔ companion UART crossed (TX→RX both ways), common ground.
- The trigger-string format (short `key=value` pairs, one line) is the
  Light→mesh contract; it lives with Light's CAN trigger logic — keep both
  ends pointing at the same note so they don't drift.
- Bench shortcut: T3 #2 can serve as the companion (same config, UART broken
  out) until the dedicated ESP32 exists — then move it, and re-check names.

## Pager (phone)

- Join both channels by QR from the Prime radio (`provision_mesh.sh qr`).
- Alerts that matter → **DM to your node** (delivery-acks); broadcast is for
  the merely-informative. Save command frames as canned messages.

## Bring-up order (per node)

1. **⚠ ANTENNA BEFORE POWER — always.** Transmitting into an open load can
   cook the PA. Bench habit, not optional.
2. Flash stock Meshtastic (web flasher), plug into the Pi by USB.
3. `tools/provision_mesh.sh <prime|sensor|companion> --port /dev/ttyACM0`
4. Verify: `meshtastic --port /dev/ttyACM0 --info` → region US, preset
   LONG_SLOW, expected channels; record the node ID in the table above.
5. End-to-end sanity: phone → `ScotCmd` → Prime radio → BLE → `status` reply
   (executor side: [MICROKVM.md](MICROKVM.md)); sensor metric visible on
   phone; Light trigger string arrives as text.

## Prime-side tiles (cross-refs)

- Telemetry/pager metrics as kilodash tiles: autopopulate when streamed, with
  a **freshness indicator** (reuse the Signal K freshness pattern — LoRa is
  slow and bursty; live-vs-stale matters more here than on a wired bus).
  Not built yet — tracked in `To-DoLists/LoRa-Mesh-Nodes-Setup.md`.
- The command-plane tile (armed/dormant + session log) ships with
  [MICROKVM.md](MICROKVM.md).

## Known gotchas (the short list that costs evenings)

- **Region/preset mismatch = silent no-mesh.** Check both, on every node,
  before debugging anything else.
- **SAMD51 can't run Meshtastic** — that's *why* Light has a companion; no
  "just flash Light" shortcuts.
- **Channel PSK is the auth boundary.** Command channel: separate, strong,
  never on sensor nodes.
- **Airtime is duty-limited.** Default intervals long; tighten deliberately.
- **Antenna before power.** Repeated because it kills hardware.
- **Names before deploy.** MAC-default names are how you end up with three
  nodes called `Meshtastic 4f2a`.
