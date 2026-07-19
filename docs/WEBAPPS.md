# Web-app screens — user guide

Beyond the built-in tools, Scottina fronts **bigger packages that serve their
own browser UI**: Kismet, Node-RED, AIS-catcher, and Signal K. Opening one of
these screens *launches the app* (or adopts it if it autostarted at boot) and
fronts it with **one compact status card** at the top:

- The card's **border colour is the app state** — **green only once the port
  actually answers** (a real "✓ web UI confirmed", not just "spawned"), accent
  while launching, muted when stopped, red on a problem.
- The card body is the **`URL:port`** to open the full interface from a phone
  or laptop — so you never guess which port a service landed on.
- **The card is the start/stop control:** tap it while running to stop the app
  (a **confirm dialog** asks first — the web UI goes down), tap it while
  stopped to launch again. The `tap: stop` / `tap: launch` hint top-right
  reminds you which you'll get.

Everything below the card is the app's native panel — the controls and live
feedback you actually watch.

**A web-app tile appears on Home only once its backend is installed.** Install
them all with [`setup/install-phase4.sh`](../setup/install-phase4.sh)
(idempotent: Node-RED, AIS-catcher, Signal K + their systemd units).

- [Kismet](#kismet)
- [Node-RED](#node-red)
- [AIS](#ais)
- [Signal K](#signal-k)

---

## Kismet

Launches the Kismet server, confirms its web UI on **`:2501`**, and gives a
**Sniff on/off** toggle plus a live **peer list** colour-coded by device type.

| Control | What it does |
|---|---|
| **Enable / Disable sniffing** | Adds/removes the ALFA (the non-uplink Wi-Fi adapter) as a Kismet monitor datasource. Passive listening only. |
| **Peers list** | Recent devices Kismet has heard — AP (accent), client (green), Bluetooth (amber) — strongest signal first. |

Like [WiFi Sniff](WIFISNIFF.md), a watchdog keeps the Pi's uplink (`wlan0`)
connected the whole time the ALFA is sniffing — the two radios run at once.

The peer list is read from Kismet's REST API using the credentials Kismet
writes to `~/.kismet/kismet_httpd.conf` on first run. **Until you complete
Kismet's one-time web login** the panel shows a hint instead of peers — open
the web UI at the shown URL, set the admin login once, and the on-screen list
populates. For the full survey UI (maps, logging, filters) always use the web
UI; the tile shows the top peers as a glance.

---

## Node-RED

Confirms the Node-RED editor on **`:1880`** and gives **6 assignable feedback
fields + 6 trigger buttons** wired to *your own* flow — a manual CAN/IoT
troubleshooting bench where you press the couple of buttons you're testing and
watch the couple of numbers that matter.

| Control | What it does |
|---|---|
| **6 feedback fields** (3×2) | Show live values your flow publishes (e.g. throttle %, RPM, a door state). Show `—` until the flow provides them. |
| **6 trigger buttons** (3×2) | POST to your flow; a toast reports "sent" or "no handler". |

The contract Scottina speaks (all on localhost):

```
Feedback  GET  http://127.0.0.1:1880/kilodash/state
          → {"fields":  [{"label":"Temp","value":"21.4"}, ...],   # up to 6
             "buttons": [{"label":"Fan"}, ...]}                   # up to 6
Triggers  POST http://127.0.0.1:1880/kilodash/btn/1 .. /btn/6
```

Field and button **labels** come from that same `/state` payload, so your flow
names them. Import the ready-made flow at
[`setup/nodered-kilodash-flow.json`](../setup/nodered-kilodash-flow.json) and
follow [`setup/NODE-RED.md`](../setup/NODE-RED.md) for the wire-up (feedback
comes from flow context `f1..f6`; a flow imported before the 6-slot panel
still works — the extra rows just show defaults). Until the flow exists,
fields show `—` and buttons post harmlessly (404) — the panel still launches
and confirms.

---

## AIS

Two radios, two jobs — receive live vessel AIS, and (with TX hardware) transmit
**test** AIS frames to bench-check your own receiver.

**Listen (works today):** AIS-catcher on the RTL-SDR, confirmed on **`:8100`**.

| Tile | Shows |
|---|---|
| **VESSELS** | How many ships are currently seen. |
| **MESSAGES** | Live AIS message rate (per second). |

**Transmit (needs TX hardware):** generates AIS frames for **your own MMSI** so
a robot/receiver under test can prove it decodes them.

| Control | What it does |
|---|---|
| **MMSI field** | Tap to set your own 9-digit station MMSI (used for the test frames). |
| **Transmit test** | Disabled until a TX-capable SDR (HackRF / Pluto / Lime) **and** `ais-simulator` are both present. **Every transmit is armed with a confirm tap** — tap once to arm, again within 4 s to fire. |

> ⚠️ The RTL-SDR **cannot transmit** — receive is inherently listen-only.
> Transmit is intended strictly for **contained bench testing** of your own
> receiver: minimal power into a small/dummy antenna, indoors, so nothing
> reaches real AIS traffic. A transmitter is never left running when you leave
> the screen.

---

## Signal K

The boat's data hub as a helm glance. Adopts Signal K if it autostarted at boot,
confirms its web UI on **`:3000`**, and **never stops it on leave** (it's the
vessel data hub).

| Control | What it does |
|---|---|
| **Vitals grid** | A page of **six** live values (3×2). **Tap anywhere to cycle** the group: Nav → Engine → Environment → Power (page dots top-right). |
| **Heartbeat line** | Pinned to the bottom edge: a freshness dot + distinct feed count, e.g. `● 3 feeds · 1.2s ago` — one glance tells you the NMEA2000 → Signal K bridge is actually flowing. `no data flowing` in red means it isn't. |

The pages:

| Group | Values |
|---|---|
| **NAV** | SOG (kn), COG (°), HDG (°), STW (kn), rate of turn (°/s), GPS fix |
| **ENGINE** | RPM, temp (°C), oil pressure (bar), coolant temp (°C), fuel rate (L/h), run hours |
| **ENVIRON** | depth (m), apparent wind speed (kn) & angle (°), water temp (°C), air temp (°C), barometer (mbar) |
| **POWER** | battery volts, state-of-charge (%), current (A), battery temp (°C), time remaining (h), solar power (W) |

Signal K stores SI base units (m/s, radians, Kelvin, Pascals, Hz); Scottina
converts on display to the units above. If Signal K security is on, set an
access token in `config.signalk_token` (blank = open, the default).

---

## Adding another web app

The framework lives in `kilodash/webapp.py` (a process/port supervisor + stdlib
HTTP helpers) and `screens/webapp_base.py` (`WebAppScreen`). To add one,
subclass `WebAppScreen`, set `app_name`/`port`/`service` (or `start_cmd`), and
override the `draw_app` / `handle_app_tap` / `poll_app` hooks. See the main
[README](../README.md#web-app-launch-terminal).

## Troubleshooting

| Symptom | Fix |
|---|---|
| No tile for an app | Its backend isn't installed — run [`setup/install-phase4.sh`](../setup/install-phase4.sh). |
| Status card never turns green | The service didn't come up on its port — check its systemd unit / logs; the card shows the port being probed. |
| Stopped an app by accident | Tap the status card again — while stopped it relaunches immediately (no confirm on launch). |
| Kismet peer list won't populate | Finish Kismet's one-time web login at the shown URL to set credentials. |
| Node-RED fields show `—` | The `/kilodash/state` endpoint isn't in your flow yet — import the sample flow and see `setup/NODE-RED.md`. |
| AIS Transmit stays disabled | Needs both a TX-capable SDR and `ais-simulator`; the RTL-SDR alone is receive-only. |
| Signal K shows "no data flowing" | The NMEA2000 → SK bridge isn't feeding — check the source at the SK web UI. |
