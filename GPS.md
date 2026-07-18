# GPS.md — the position snapshot contract

**Status: v1.0.** Like `TABLES.md` and `DOCK-PROTOCOL.md`, this file is the
*only* coupling between the GPS plumbing and every consumer on the box
(tile geotagging, capture stamping, future Signal K comparison). Consumers
never talk to gpsd; they read one file. When this contract changes, it
changes here first.

## 1. The snapshot file

```
/run/kilodash/gps/position.json
```

- **tmpfs, by design.** This is volatile *state*, not data — it is gone on
  reboot and that is correct. Nothing may persist it or copy it anywhere
  as if it were a log.
- **One writer:** the snapshot service (`gps/snapshotd.py`, systemd unit
  `kilodash-gps-snapshot.service`). It connects to gpsd on localhost and
  writes the file at **1 Hz** via atomic tmp-file + `os.replace()` rename
  in the same directory. Consumers only ever read.
- Crash-only: the writer needs no teardown. If it dies, the file goes
  stale and rule §3 turns that into "no fix" everywhere.

## 2. Schema

One JSON object:

| key            | type            | meaning                                       |
|----------------|-----------------|-----------------------------------------------|
| `ts`           | string          | ISO8601 UTC (`…Z`), **from GPS time** when the receiver reports it, else the system clock |
| `fix`          | string          | `none` \| `2d` \| `3d` \| `dgps`              |
| `lat`          | number \| null  | decimal degrees, null when no fix             |
| `lon`          | number \| null  | decimal degrees, null when no fix             |
| `sog_mps`      | number \| null  | speed over ground, m/s                        |
| `cog_deg_true` | number \| null  | course over ground, degrees true              |
| `alt_m`        | number \| null  | altitude, m (3D fix only)                     |
| `hdop`         | number \| null  | horizontal dilution of precision              |
| `sats_used`    | int             | satellites used in the fix                    |
| `sats_visible` | int             | satellites currently tracked                  |
| `time_quality` | string          | `gps` when `ts` came from GPS time, else `unsynced` |

Unknown extra keys MUST be ignored by readers (the writer may add fields
in a future revision; removing or retyping one is a contract change).

## 3. Staleness rule (hard)

Consumers **MUST** check the age of `ts`:

- `ts` older than **5 seconds**, file absent, or file unparsable
  ⇒ treat as **no fix**. Never trust a stale position.

The one shared implementation of this rule is
`gps/snapshot.py::read_position()` — consumers call it and do **not**
hand-roll the age check. It returns the parsed snapshot dict only when the
file is fresh *and* reports a fix; otherwise `(None, reason)`.

## 4. Geotag rule for capture artifacts

When a capture starts (candump log, LAN scan, logic-analyzer session,
decoded-N2K export, …), the capturing screen reads **one** snapshot via
`read_position()` and embeds it in the capture's sidecar/manifest:

- with a fix: the snapshot object as-is, under a `"gps"` key;
- without one: `"gps": null` plus `"gps_reason": "<why>"` (the reason
  string from `read_position()`).

No continuous tracking of captures — one stamp at start, optionally one
more at stop. A capture is a bus/RF/network artifact, not a track log.

## 5. What reads gpsd directly (the two exceptions)

Two consumers deliberately bypass the snapshot and speak gpsd JSON:

- the **GPS tile** (`kilodash/screens/gps.py`) — it wants full SKY/TPV
  detail (per-satellite az/el/SNR) that the snapshot intentionally omits;
- the **N2K GNSS source node** (`n2k/node.py`) — sourcing PGNs from the
  snapshot file would stack its 1 Hz cadence and 5 s staleness window on
  top of gpsd's own (double-staleness); a bus node sources live data or
  goes silent.

Everything else reads the file.
