# Pi Health, Pomodoro & Settings — user guide

The three always-present housekeeping screens:

- [Pi Health](#pi-health) — is the Pi hot, full, or throttling?
- [Pomodoro](#pomodoro) — a focus/break timer that keeps counting in the background.
- [Settings](#settings) — every tunable as a card, plus power actions and touch calibration.

---

## Pi Health

A one-glance status of the Raspberry Pi itself. It refreshes every couple of
seconds while open.

| Card | Shows |
|---|---|
| **CPU temp** | Temperature, colour-graded (green < 65 °C, amber 65–75, red ≥ 75), with clock MHz and load average. |
| **Memory** | Used %, a bar, and used / total MB. |
| **Disk /** | Used %, a bar, and used / total MB. *This is where your captures live — watch it before a long logging session.* |
| **Wi-Fi** | Signal %, a bar, and the connected SSID (or "not connected"). |
| **Uptime** | How long the Pi has been up. |
| **Throttle** | **OK** or a red **YES** — the Pi's under-voltage/over-temp throttling flag. A **YES** means an inadequate power supply or cooling. |

Read it as a whole: a red temp *and* a **YES** throttle together point at
power/thermal, not software.

---

## Pomodoro

A classic 25/5 focus-and-break timer, with a longer 15-minute break after every
fourth focus block. Its one trick: **it keeps counting even when you're on
another screen** — a background thread owns the clock, and each transition
toasts app-wide and flashes the screen (there's no speaker) to get your
attention.

| Control | What it does |
|---|---|
| **Start / Pause** | Runs or pauses the current phase (button colour matches the phase). |
| **Reset** | Back to a fresh 25-minute focus block. |
| **Skip** | Jump to the next phase now (no credit for the skipped one). |

The ring depletes as time passes; the four dots track focus blocks toward the
long break; "N completed today" counts lifetime focus blocks. Leaving the screen
doesn't stop the timer — you'll still get the transition toast wherever you are.

---

## Settings

Every adjustable value, rendered from `config.py::DEFAULTS` — booleans toggle,
integers step with `−`/`+`, choices cycle. Changes persist to
`/opt/kilodash/config.json` immediately and survive reboots (you can also
hand-edit that file over SSH if the panel is mis-calibrated).

### System

| Setting | What it does |
|---|---|
| **Status refresh** | How often status data is polled (1–30 s). |
| **Theme** | `green` / `amber` / `light` skin. |
| **Show clock** | Clock in the header. |
| **FPS meter** | Perf overlay (frame time / FPS) for tuning — default off. |

### Display

| Setting | What it does |
|---|---|
| **Flip display 180 (software)** | Rotate the whole UI without a reboot. |
| **Screen dimming** | Enable the idle screensaver. |
| **Dim after** | Idle time before dimming (30–1800 s). |
| **Dim brightness** | How dark the dimmed screen goes. |

> This panel has no controllable hardware backlight, so "dimming" darkens the
> rendered image in software. The first tap after dimming only wakes the screen
> — it doesn't also activate whatever you touched.

### Touch

Calibration for after the 180° flip — flip these **one at a time**:

| Symptom | Toggle |
|---|---|
| taps mirror left/right | **Touch invert X** |
| taps mirror up/down | **Touch invert Y** |
| taps land on the wrong axis entirely | **Touch swap X/Y** |

A **Calibrate touch** helper sits at the bottom of the Touch group. The same
values live in `config.json` if the panel is so far off you can't hit the
toggles — edit it over SSH and restart the service. Defaults target the
`rotate=90` orientation (`swap=on, invert_x=off, invert_y=off`).

### Power

| Action | What it does |
|---|---|
| **Restart UI** | `systemctl restart kilodash` — reload the app without rebooting. |
| **Reboot Pi** | Full reboot. |
| **Shutdown** | Power off. |

### About

Version, license, and credit.

> **Note:** Some values (AIS own-MMSI, Signal K token, the CanTick bridge
> config) are managed from their own screens rather than shown here.
