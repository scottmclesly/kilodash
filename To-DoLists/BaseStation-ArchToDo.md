# BaseStation-Arch.md — Field Base Station (Pre-Staging)

High-level architecture for the Pi5 field base station: local sensor aggregator +
environmental monitoring station + cloud gateway for the WQL logger fleet.

> **STATUS: PRE-STAGING.** This is a structural sketch, not an action list. Hardware
> and sensor variants are still being chosen. Locked decisions are marked **[LOCKED]**;
> everything else is directional. Nothing here is a build ticket yet.

---

## Role

The base station is three things in one box:

1. **Local sink** for WQL loggers — DiveSync "local" mode POSTs dives to the Pi.
2. **Environmental station** — its own multi-sensor suite (soil, air, rain, water, buoy…).
3. **Cloud gateway** — store-and-forward offload of everything to Supabase.

Design ethos, top to bottom: **leave-and-forget, each hop tolerant of the next being
absent.** logger → Pi → cloud. No leg assumes the next is always present; it buffers and
drains when a path appears.

---

## Hardware baseline

- **Compute:** Raspberry Pi 5
- **Display:** ROADOM 10.1" touchscreen (kiosk UI)
- **Wired sensor bus:** USB-RS485 dongle (Modbus RTU) **[LOCKED]**
- **Buoy link:** LoRa HAT/module, US915, point-to-point **[LOCKED]**
- **Uplink radio:** TBD — ethernet and/or USB WiFi dongle and/or cellular (see Topology)
- **GPS:** USB or UART/I2C HAT (`gpsd`)
- **No onboard ADC** — **hard rule: buy Modbus / SDI-12 / digital sensor variants,
  never analog.** Adding an ADC HAT just for a couple of sensors is a non-goal.

---

## Sensor suite roadmap

Full eventual suite. Interface column is the *target* ingest path; unverified variants
noted.

| # | Sensor | Parameters | Target interface | Ingest driver |
|---|---|---|---|---|
| 1 | **WQL Logger** | full dive suite | WiFi/HTTP (DiveSync local) | Pi HTTP endpoint |
| 2 | **Apera PC60** | ORP, pH, EC, temp | **dumb LCD — must RE** | see Apera section |
| 3 | **GPS** | lat/lon/time | USB/UART | `gpsd` |
| 4 | **Soil probe** | pH, temp, moisture, air humidity, ambient light, fertility | RS485/Modbus | modbus poller |
| 5 | **Air probe** | PPM, wind speed, wind dir, temp, humidity, UV | RS485/Modbus | modbus poller |
| 6 | **Rain gauge** | tipping-bucket rainfall | GPIO pulse (reed) | gpio counter |
| 7 | **Noise pollution** | dB SPL | RS485 (source Modbus variant) or I2S | modbus / i2s |
| 8 | **Creek current** | flow/velocity | RS485 or SDI-12 | modbus / sdi-12 |
| 9 | **Buoy current** | speed + velocity (remote, on water) | **LoRa** | lora rx |

**Collapse achieved:** items 4, 5, 7, 8 all target one shared RS485 twisted pair. The
"nine protocols" problem reduces to: one Modbus poller + GPIO (rain) + gpsd + LoRa rx +
the logger HTTP endpoint + the Apera outlier.

---

## Topology — AP + separate uplink

The Pi's single built-in WiFi cannot be the loggers' AP **and** reach the internet on
the same radio. (Concurrent AP+STA on one chip exists via virtual interfaces but is
channel-locked and flaky — not sit-and-forget material.) Same constraint the ESP32 has,
one layer up. Resolved the same way: **separate radio for the separate job.**

```
  WQL loggers ──WiFi──▶ [ wlan0: hostapd AP ]  Pi5  [ uplink iface ]──▶ Internet ──▶ Supabase
                         (dnsmasq, local net)         eth0 / wlan1 / wwan0
```

- **Built-in WiFi (`wlan0`)** = `hostapd` + `dnsmasq`, the AP the loggers join.
- **Uplink** = a *different* interface. Options, not mutually exclusive — carry several,
  use whichever has a route:

| Uplink | Best for | Cost / caveat |
|---|---|---|
| **Ethernet** (`eth0`) | lab / dock with a drop | free, no radio; useless in bare field |
| **USB WiFi dongle** (`wlan1`) | existing site WiFi / tethered hotspot | ~$15; pick mainline ARM64 driver (RTL8812AU-class) |
| **Cellular** (`wwan0`, SIM7600/Quectel) | field with zero infrastructure | SIM + data plan + antenna; the only truly autonomous option |

**[LOCKED] Pi-as-AP.** Loggers always have an AP to join.
**OPEN:** which uplink radio(s) ship — a real BOM/enclosure/power decision to make before
mechanical firms up. Lean: ship ethernet **+** one field uplink (USB WiFi or cellular).

**Hard rule — route-agnostic offload:** the offload service watches for *any* default
route to appear; it does not care which interface provides it. Pi buffers to Timescale
when offline, drains when ethernet is plugged / cellular ranges in / a hotspot is
tethered. The station is never required to be *always* online, only *eventually*.

---

## Stack — four layers on an MQTT spine

```
 ┌ acquisition drivers ┐     ┌ spine ┐     ┌ store ┐        ┌ present / ship ┐
 modbus poller ────────┐                                    ┌─ local API ▶ kiosk UI (Chromium)
 gpio rain counter ────┤                  writer ▶ ┌──────┐ ├─ websocket ▶ live tiles
 gpsd ─────────────────┼──▶ Mosquitto ───▶         │Time- │ │
 lora rx ──────────────┤     (MQTT)       live ────▶│scale │─┴─ offload svc ▶ Supabase
 logger HTTP endpoint ─┤                            │  DB  │    (store-and-forward,
 apera bridge ─────────┘                            └──────┘     secret key)
```

**1 — Acquisition drivers.** One small process per protocol family. Each reads its
sensors, normalizes to a common record, and publishes to MQTT. Adding sensor #10 = write
a driver, publish a topic; nothing downstream changes.

Common record shape:
```json
{ "station_id":"base01", "sensor":"soil", "metric":"moisture",
  "value":34.2, "unit":"pct", "ts":"2026-07-10T14:22:00Z",
  "lat":27.99, "lon":-80.62, "source":"modbus" }
```

**2 — Mosquitto (MQTT broker).** The spine. Topics like `station/base01/soil/moisture`.
Decouples producers from consumers — the whole reason the suite can grow without
downstream churn.

**3 — TimescaleDB [LOCKED].** Local time-series store; makes the station autonomous
(buffer-and-forward). Chosen so it **rhymes with Supabase** — both Postgres, so schema,
queries, and the offload path share one mental model. Runs on Pi5/ARM64.
- **Hypertables** for `readings`.
- **Continuous aggregates** (1-min / 1-hour rollups) so the touchscreen hits cheap
  materialized views, not raw scans.

**4a — Custom kiosk UI [LOCKED].** Chromium in kiosk mode on the ROADOM 10.1", serving a
local web app off the Pi. **Reuses the portal chart renderer** (`parseCsv`, `miniChart`,
`drawCharts`, accent theming from `portal_page.h`) — charting is not rebuilt. Implies a
small **local API server** (FastAPI or Node): history from Timescale over REST, live tiles
over a websocket. (Pattern assumed cloned from the adjacent project's SPA — confirm stack.)

**4b — Offload service.** Subscribes to MQTT (or tails Timescale), batches to Supabase
when a route exists, marks rows synced, retries on failure. Same leave-and-forget ethos
as the logger — just on mains-ish power, not a battery.

---

## RS485 / Modbus [LOCKED]

One USB-RS485 dongle, one `pymodbus` poller, every wired env sensor a slave address on a
shared twisted pair.

**Draft address map** (assign now, even pre-staging):

| Addr | Sensor |
|---|---|
| 0x01 | Soil 7-in-1 (pH / temp / moisture / air-humidity / light / fertility) |
| 0x02 | Air probe (PPM / wind speed / wind dir / temp / humidity / UV) |
| 0x03 | Noise (dB) — *if* a Modbus variant is sourced |
| 0x04 | Creek current — Modbus or SDI-12 variant |

**Wiring rules to lock:**
- 120 Ω termination at **both** ends of the bus.
- Daisy-chain, **not** star.
- Common ground reference across all nodes.
- Biasing (fail-safe) resistors at the master.

---

## LoRa (buoy) [LOCKED]

- **Band: US915** (Florida).
- **Point-to-point, not LoRaWAN** — one buoy → one base needs no network server; a simple
  addressed link is far less overhead. Revisit only if buoy count grows.
- Buoy = LoRa node; Pi = LoRa HAT running the `lora rx` acquisition driver → MQTT like any
  other sensor.

---

## Cloud offload → Supabase

The Pi is a **trusted server**, not a sealed ESP32. So unlike the logger it can hold the
**secret key** (`sb_secret_…`) and write directly via PostgREST or a direct Postgres
connection — **no publishable-key / MAC-allowlist tap-dance** for the station's own data.
(Loggers still use the publishable-key + allowlist path from `DiveSync-To-Do.md` when they
go cloud-direct; that's unchanged.)

**Schema extension — don't overload `dives`:**

```sql
-- keep dives logger-specific (cast / mission / POI semantics env sensors don't have)
--   ... existing dives table from DiveSync-To-Do.md ...

-- new: one row per station
create table public.stations (
  station_id text primary key,   -- 'base01'
  label      text,
  lat double precision, lon double precision,
  added_at   timestamptz default now()
);

-- new: generic wide reading table for the whole env suite
create table public.readings (
  id         bigint generated always as identity primary key,
  station_id text not null references public.stations(station_id),
  sensor     text not null,      -- 'soil' | 'air' | 'rain' | 'creek' | 'buoy' | 'apera' ...
  metric     text not null,      -- 'moisture' | 'ppm' | 'wind_speed' ...
  value      double precision,
  unit       text,
  ts         timestamptz not null,
  lat double precision, lon double precision,
  source     text,               -- 'modbus' | 'gpio' | 'lora' | 'manual' ...
  uploaded_at timestamptz default now()
);
```

Wide `sensor/metric/value/unit` shape absorbs new sensors **without migrations**. The Pi
is just another reporter (`station_id`) in the same cloud the loggers report to. Mirror
this table in Timescale locally as the buffer; offload is a straight `select here → insert
there` because both ends are Postgres.

**Optional local-sink alignment:** have the Pi's logger-ingest HTTP endpoint mirror the
Supabase upload contract (same path shape + JSON body). Then the WQL logger's DiveSync
local-vs-cloud modes differ only by URL + key — same firmware, one config field.

---

## Apera PC60 — reverse-engineering path

Confirmed a **dumb LCD + buttons** unit: no data port, must be reverse-engineered *if* we
want it automated. Decision tree, cheapest-signal-first:

1. **Open + logic-analyze the MCU pins.** Best case: an internal UART/I2C already carries
   live readings, or a cal/debug header. If it streams serial, tap it and done. ~1 hr with
   a $10 analyzer; wins outright or rules itself out. **Do this first.**
2. **Sniff the LCD bus.** If an identifiable I2C/SPI controller drives the glass, capture
   traffic and rebuild the segment→digit map. Dead-ends if it's a glob-top COB driving the
   glass directly (common in cheap pocket meters — no accessible bus).
3. **OCR the LCD with a small camera.** Always-works fallback: tiny cam + seven-segment OCR
   on the Pi (`ssocr`-class). Non-invasive, survives unreadable firmware, no soldering.
   Needs stable mounting + lighting.

**Reality checks before sinking time:**
- A PC60 is a **handheld spot-check tool, not a continuous logger** — auto-off, battery
  life, probes not rated for permanent immersion all fight 24/7 deployment. May be the
  wrong instrument for a continuous slot regardless of how cleanly it reads.
- Its real value may be **manual ground-truth** to validate the fixed Modbus sensors. If
  so, "integration" = a **manual-entry card in the kiosk UI** → `readings` with
  `source='manual'`. Same data, same table, near-zero effort.

**Directive:** run the logic-analyzer look before committing to RE; ship the manual-entry
card either way as the guaranteed fallback.

---

## Open decisions (pre-staging)

| # | Decision | Lean | Blocks |
|---|---|---|---|
| 1 | Uplink radio(s) — eth / USB-WiFi / cellular | eth + one field uplink | enclosure, power, BOM |
| 2 | Custom UI stack = adjacent project's SPA? | assume yes | confirm before local-API build |
| 3 | Noise + creek sensors sourced as Modbus? | yes (hard rule) | address map finalization |
| 4 | Apera: automate (RE) vs manual-entry card | manual card ships; RE optional | Apera teardown result |
| 5 | Cellular carrier / SIM (if cellular uplink) | — | field-autonomy requirement |

---

## Locked summary

**Pi-as-AP** for loggers · **separate route-agnostic uplink** · **store-and-forward at
every hop** · **RS485/Modbus** for wired env sensors (no analog) · **LoRa US915
point-to-point** for the buoy · **custom kiosk UI** over **TimescaleDB** · **secret-key
direct offload** to Supabase · schema extended with `stations` + generic `readings`.

Only genuinely-unknown item in the suite: what the **Apera teardown** reveals.
