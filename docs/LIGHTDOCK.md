# Light Dock — user guide

Scottina's **Light Dock** screen appears the moment **Scottina Light** (the
Wio Terminal sibling) is plugged into a USB port, and runs a fully automatic
sync: it **pushes the wall-clock time**, **pushes your enabled decode
tables**, and **pulls Light's SD logs** into Scottina's captures. You watch;
you don't drive.

Why it exists: Light has no clock battery. Standalone, its black-box logs
are timestamped in "seconds since power-on" — useless for lining up with
anything Scottina recorded. Docking makes Light's logs *alignable* and
delivers decode tables without pulling the SD card. The dock is the sync
moment; Light stays standalone the rest of the time.

The wire protocol behind all of this lives in
[`DOCK-PROTOCOL.md`](../To-DoLists/DOCK-PROTOCOL.md) — nothing here requires
reading it.

---

## The screen, from across the room

The top half is an animation you can read from the helm:

| You see | It means |
|---|---|
| Two devices with **pulses running along the cable** | Syncing. Pulse rate loosely tracks how much data is moving. |
| The two devices **together, steady glow** (the hug) | Done. Everything that could sync, synced — no need to come closer. |
| A **sad face over a broken cable** | The sync didn't finish. Come look at the log — it's "come look", not an emergency. |

The bottom half is the session log: timestamped lines saying exactly what
happened — `clock → set (ntp)`, `tables 2/2 — pushed engine.json`,
`logs: 12 pulled, 12 deleted`. It is **session-only, deliberately**: each
dock clears it. If a problem persists across docks, that's the signal to
drop into SSH and the files on disk; this screen answers *"did the sync
land?"* and nothing more. On an interruption the last lines *are* the
incomplete-state report — the sad face points here.

## The only two controls

| Control | What it does |
|---|---|
| **Re-sync** | Runs the whole sync again. Rarely needed — docking (and re-docking) starts it by itself. |
| **Pull logs: ON/OFF** | The one step that costs time and card space. OFF still syncs clock and tables. Also in **Settings** ("Light Dock: auto-pull logs"). |

## What each sync step means

**Clock.** Scottina sends its time *with an honesty label*: `ntp` only if it
is NTP-synchronized right now, `rtc` if it's running on the Pi's real-time
clock, and if Scottina's own clock is untrustworthy it sends **nothing** —
a bad clock is labeled, never written into Light's logs as truth. Light
stamps every subsequent log file with the time source it was given.

**Tables.** Your enabled decode tables (the same store the Tables tile and
NMEA2K screen use, [`TABLES.md`](../TABLES.md)) are compared with what's on
Light's card and only the missing or stale ones are sent. **Scottina always
wins**: a table on Light is overwritten by Scottina's copy, never merged —
nothing is ever edited on Light. A table push with no visible effect on
Light is normal for now; Light stores tables ahead of the firmware that
will read them.

**Logs (if the toggle is ON).** Every closed log on Light's card is copied
into `/opt/kilodash/captures/` as `light-<name>`, where the **Files**
screen's USB offload picks it up like any other capture
([docs/FILES.md](FILES.md)). Each file is **verified by checksum first,
deleted from Light only after it verifies** — a pull that verifies nothing
would be just a copy, and a delete without proof would risk the black box.
A file that fails verification stays on Light and the log says so.

## Docking pauses Light's recording — briefly, and on the record

While docked, Light stops logging: its storage can only keep one file open
at a time, so serving the dock and recording can't happen at once. This is
stated, never silent — the session log shows *"logging suspended for
dock"* — and the moment you unplug (or the sync ends), Light resumes into a
fresh log file whose header records the gap and the new clock. A dock is a
bench event lasting seconds; the record shows exactly where it happened.

## Interruptions are safe

Yank the cable mid-sync and nothing half-exists: table pushes only take
effect after an atomic verify-then-commit, and log pulls only delete after
proof of receipt. The next dock re-checks everything and transfers only
what's missing — there is no "resume state" to corrupt, on either side.
Light also has its own watchdog: if the dock goes quiet for ten seconds it
returns to standalone logging by itself, so a dead cable can never leave
the black box switched off.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Tile doesn't appear | Light must enumerate on USB (it shows up as "Seeed Wio Terminal"). Try the cable/port; a charge-only cable won't do. |
| "Light not found on USB" on Re-sync | Same as above — the device left the bus between docking and the tap. |
| Sad face, log ends in `interrupted: … no response` | Light stopped answering — reseat the cable; the next dock resumes automatically. |
| `tables skipped, logs skipped — no SD in Light` | No card in Light. Clock still synced; insert a card and re-dock for the rest. |
| `clock NOT sent — Prime is unsynced` | Scottina itself has no trustworthy time (no NTP, no valid RTC). Get it a network (or fit the Pi 5 coin cell), then Re-sync. |
| `degraded to clock-set only` | Firmware and kilodash speak different dock-protocol versions. Update the older side; clock sync still works meanwhile. |
| `verify failed` on a log | The file changed or corrupted in transit. It stays on Light; re-dock to retry. |
