"""Position snapshot writer — the ONE writer of the GPS.md contract file.

Connects to gpsd (localhost JSON via gps/gpsdio.py) and writes
/run/kilodash/gps/position.json at 1 Hz: atomic tmp-file + rename in the
same tmpfs directory. Crash-only design: no teardown path exists or is
needed — if this daemon (or gpsd, or the dongle) dies, the file goes stale
and GPS.md §3 turns that into "no fix" for every consumer.

A snapshot is still written while there is no fix (fix="none", null
position): a *fresh* no-fix file distinguishes "GPS searching" from "GPS
plumbing dead", and both correctly read as no-fix through
gps/snapshot.py::read_position().

Runs as systemd unit kilodash-gps-snapshot.service (setup/install-gps.sh).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from .gpsdio import GpsdListener, MODE_NAMES, STATUS_DGPS
from .snapshot import SNAPSHOT_PATH

CADENCE_S = 1.0
# TPV older than this is not "current position" even if gpsd is up
TPV_FRESH_S = 2.0


def _iso_utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_snapshot(tpv, sky, tpv_age=None, now=None):
    """Pure mapping: latest gpsd TPV/SKY reports → the GPS.md §2 object.
    `now` (unix seconds) stamps ts when GPS time is unavailable."""
    now = time.time() if now is None else now
    tpv = tpv or {}
    sky = sky or {}
    stale = tpv_age is not None and tpv_age > TPV_FRESH_S
    mode = 0 if stale else tpv.get("mode", 0)
    fix = MODE_NAMES.get(mode, "none")
    if fix != "none" and tpv.get("status") == STATUS_DGPS:
        fix = "dgps"
    has_fix = fix != "none" and tpv.get("lat") is not None \
        and tpv.get("lon") is not None
    if not has_fix:
        fix = "none"
    sats = sky.get("satellites") or []
    gps_time = tpv.get("time") if mode >= 2 else None
    return {
        "ts": gps_time if gps_time else _iso_utc(now),
        "fix": fix,
        "lat": tpv.get("lat") if has_fix else None,
        "lon": tpv.get("lon") if has_fix else None,
        "sog_mps": tpv.get("speed") if has_fix else None,
        "cog_deg_true": tpv.get("track") if has_fix else None,
        "alt_m": (tpv.get("altMSL", tpv.get("alt"))
                  if has_fix and mode == 3 else None),
        "hdop": sky.get("hdop"),
        "sats_used": sum(1 for s in sats if s.get("used")),
        "sats_visible": len(sats),
        "time_quality": "gps" if gps_time else "unsynced",
    }


def write_snapshot(snap, path=SNAPSHOT_PATH):
    """Atomic write: tmp file + rename in the same directory (tmpfs)."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f)
    os.replace(tmp, path)


def main():
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    listener = GpsdListener().start()
    print(f"snapshotd: writing {SNAPSHOT_PATH} at {1 / CADENCE_S:.0f} Hz",
          flush=True)
    while True:
        st = listener.state()
        write_snapshot(build_snapshot(st["tpv"], st["sky"],
                                      tpv_age=st["tpv_age"]))
        time.sleep(CADENCE_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
