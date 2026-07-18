"""The one shared reader of the position snapshot contract (GPS.md §3).

Every consumer of /run/kilodash/gps/position.json calls read_position()
instead of hand-rolling the staleness check — the 5 s rule lives here and
nowhere else. Read-only: the single writer is gps/snapshotd.py.
"""

import json
import time
from datetime import datetime, timezone

SNAPSHOT_PATH = "/run/kilodash/gps/position.json"
MAX_AGE_S = 5.0

FIXES = ("none", "2d", "3d", "dgps")


def parse_ts(ts):
    """ISO8601 UTC string → unix seconds, or None if unparsable."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")) \
            .astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def read_position(path=SNAPSHOT_PATH, now=None, max_age=MAX_AGE_S):
    """(snapshot_dict, None) when the file is fresh and reports a fix,
    else (None, reason). The reason string is what geotagging embeds as
    `gps_reason` (GPS.md §4) — always truthful, never a stale position."""
    now = time.time() if now is None else now
    try:
        with open(path) as f:
            snap = json.load(f)
    except FileNotFoundError:
        return None, "no snapshot (GPS service not running or no dongle)"
    except (OSError, ValueError) as e:
        return None, f"snapshot unreadable: {e}"
    if not isinstance(snap, dict):
        return None, "snapshot malformed: not an object"
    ts = parse_ts(snap.get("ts"))
    if ts is None:
        return None, "snapshot malformed: bad ts"
    age = now - ts
    if age > max_age:
        return None, f"snapshot stale ({age:.0f}s old)"
    if snap.get("fix") not in ("2d", "3d", "dgps") \
            or snap.get("lat") is None or snap.get("lon") is None:
        return None, "no fix"
    return snap, None


def geotag(path=SNAPSHOT_PATH, now=None):
    """The GPS.md §4 stamp for a capture manifest: {"gps": snap} with a
    fix, {"gps": None, "gps_reason": why} without."""
    snap, reason = read_position(path, now)
    if snap is None:
        return {"gps": None, "gps_reason": reason}
    return {"gps": snap}
