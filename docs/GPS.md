# GPS — user guide

The GPS tile appears while the Adafruit Ultimate GPS (PA1616S on the
PL2303 dongle) sits in **USB port 1-1** — that port is *the GPS jack*.
The dongle has no serial number, so the physical port is the identity
(udev pins it to `/dev/gps0`); swapping dongles between jacks silently
swaps device identities. Don't.

Install the plumbing once with `sudo setup/install-gps.sh`, then verify
near a window: `cgps` should reach a 3D fix, and `chronyc sources -v`
should list `GPS` as a selectable time source.

## What the box does with it (no tile open needed)

- **Time authority.** gpsd feeds chrony via shared memory. While the
  network exists, NTP wins; disconnected, the box falls back to GPS time
  (serial-only, no PPS — expect tens of milliseconds, honestly labeled).
  Light Dock clock pushes use the `gps` quality flag when GPS is the
  selected source.
- **Geotagged captures.** Capture artifacts (candump ring exports, decoded
  N2K logs) gain a `.meta.json` sidecar stamping one position snapshot at
  save time — or a truthful `"gps": null` + reason when there's no fix.
  The contract every consumer reads is the repo-root [GPS.md](../GPS.md).

## The tile

- **Sky plot** (top): azimuth/elevation polar view, north up, rings at
  0°/30°/60° elevation. One dot per satellite, sized and brightened by
  SNR; solid dots are used in the fix, hollow ones merely tracked. Empty
  sky = searching; filled = locked — readable across the room.
- **Status block:** fix type (NO FIX / 2D / 3D / DGPS), sats used/visible,
  HDOP, lat/lon, SOG in knots, COG, UTC, and the bottom line: which source
  chrony is actually synced to right now (`time: GPS — this box is the
  time authority` when off-network).

## Sourcing GNSS onto the NMEA2000 bus

The **Source GNSS → bus** button lives on the **NMEA2K tile** (it's a bus
action, so it sits with the bus tools) and appears only when the GPS jack
is occupied *and* the snapshot shows a current fix. Pressing it makes the
box a real bus participant: ISO address claim, claim defense, and GNSS
PGNs (position/COG/SOG/time/heartbeat) from live gpsd data. It stops on a
second tap, automatically on fix loss, and never sources stale position.
While sourcing, your own PGNs echo back into the decode view tagged ▸ —
self-traffic verifies the TX, but the GPS-vs-bus comparison excludes it.

When another GNSS source is on the bus (the boat's own GPS), open its
position row in the NMEA2K decode view: a badge compares it against the
local receiver — green under 10 m, amber under 50 m, red beyond, plus
SOG/COG deltas. That's the diagnostics payoff of a second receiver: it
audits the boat's GPS installation.

## Gotchas

- **Cold start every boot** — no backup battery is fitted, so the module
  wakes at 9600 baud / 1 Hz factory defaults; the config service raises it
  to 115200 / 10 Hz before gpsd opens the port. Time-to-first-fix from
  cold is minutes with clear sky, not seconds.
- **Unplug/replug** re-runs the whole bring-up automatically (udev hook).
- **No PPS.** GPIO is buttoned up; PPS discipline is a Scottina Light
  adventure. Treat GPS time as good-for-logs, not lab-grade.
