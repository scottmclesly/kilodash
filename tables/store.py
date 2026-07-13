"""Store/manifest helpers for the decode-table store (TABLES.md §1/§3/§4).

Who may call what is the contract's write discipline:
  - the converter service calls install()/remove()/purge_upload() — it is
    THE writer;
  - the Tables tile calls set_enabled() only — the single sanctioned
    non-converter write, and it touches only the manifest;
  - everything else (NMEA2K screen, web Installed tab, Files export) uses
    the read-only helpers so the two views of the store can never disagree.

All writes are tmp-file + os.replace() in the destination directory, so a
killed writer never leaves a half-written table or manifest.
"""

import hashlib
import json
import os
import re
import time

BASE = os.environ.get("KILODASH_TABLES", "/opt/kilodash/tables")
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def pgn_dir():
    return os.path.join(BASE, "pgn")


def dbc_dir():
    return os.path.join(BASE, "dbc")


def upload_dir():
    return os.path.join(BASE, "uploads")


def inbox_dir():
    return BASE


def ensure_dirs():
    for d in (pgn_dir(), dbc_dir(), upload_dir()):
        os.makedirs(d, exist_ok=True)


def valid_name(name):
    return bool(_NAME_RE.match(name or ""))


def slugify(text):
    """Derive a store name from a source-document name (TABLES.md §1)."""
    stem = os.path.splitext(os.path.basename(text or ""))[0].lower()
    slug = re.sub(r"[^a-z0-9_-]+", "_", stem).strip("_")[:64]
    return slug or "table"


def table_path(name):
    return os.path.join(pgn_dir(), name + ".json")


def meta_path(name):
    return os.path.join(pgn_dir(), name + ".meta.json")


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_meta(name):
    try:
        with open(meta_path(name)) as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ writers --
def install(name, table_obj, *, source_doc, converter_version,
            pgn_count=None):
    """Converter only: atomically write <name>.json + its manifest. The
    table object must already have passed tables.validate — install() is
    the mechanism, not the gate. pgn_count goes into the manifest so
    manifest-only readers (the Tables tile) never parse table files."""
    if not valid_name(name):
        raise ValueError(f"bad table name {name!r}")
    ensure_dirs()
    if pgn_count is None:
        pgns = table_obj.get("PGNs") if isinstance(table_obj, dict) else None
        pgn_count = len(pgns) if isinstance(pgns, list) else 0
    _write_atomic(table_path(name), table_obj)
    _write_atomic(meta_path(name), {
        "name": name,
        "source_doc": str(source_doc)[:120],
        "converted": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "converter_version": converter_version,
        "enabled": True,
        "pgn_count": int(pgn_count),
        "sha256": sha256_file(table_path(name)),
    })


def remove(name):
    """Converter only: delete a table and its manifest."""
    if not valid_name(name):
        raise ValueError(f"bad table name {name!r}")
    for p in (table_path(name), meta_path(name)):
        try:
            os.remove(p)
        except OSError:
            pass


def set_enabled(name, enabled):
    """Tile + web app: flip ONLY the manifest `enabled` flag, atomically
    (TABLES.md §4). Returns the new state, or None if there is no manifest
    to flip (unverified tables can't be enabled from here)."""
    meta = read_meta(name)
    if meta is None:
        return None
    meta["enabled"] = bool(enabled)
    _write_atomic(meta_path(name), meta)
    return meta["enabled"]


# ------------------------------------------------------------------ readers --
def list_tables():
    """Inventory of pgn/ for the tile and the web Installed tab — one
    source of truth, so the two views can never disagree. Cheap: reads
    manifests + file sizes, does NOT run the validator (that happens on
    ingest and on decode-load)."""
    out = []
    try:
        names = sorted(fn[:-5] for fn in os.listdir(pgn_dir())
                       if fn.endswith(".json")
                       and not fn.endswith(".meta.json"))
    except OSError:
        return out
    for name in names:
        meta = read_meta(name)
        verified = bool(meta and meta.get("sha256")
                        and meta["sha256"] == sha256_file(table_path(name)))
        count = meta.get("pgn_count") if meta else None
        out.append({
            "name": name,
            "meta": meta,
            "verified": verified,
            "enabled": bool(meta and meta.get("enabled")) and verified,
            # manifest value when the converter wrote one; parsing the
            # table file is the fallback for legacy/unverified files only
            "pgn_count": count if isinstance(count, int)
            else _pgn_count(table_path(name)),
        })
    return out


def _pgn_count(path):
    try:
        with open(path) as f:
            obj = json.load(f)
        return len(obj.get("PGNs", [])) if isinstance(obj, dict) else 0
    except (OSError, ValueError):
        return 0


def inbox_files():
    """Loose files in the store root (TABLES.md §6) — candidates for the
    converter's ingest-from-inbox, never read by consumers."""
    try:
        return sorted(fn for fn in os.listdir(inbox_dir())
                      if os.path.isfile(os.path.join(inbox_dir(), fn))
                      and not fn.startswith(".")
                      and fn.lower().endswith((".json", ".n2k")))
    except OSError:
        return []


def load_enabled():
    """Merged decode dict for the NMEA2K screen: {pgn: entry} from every
    enabled + verified table, RE-VALIDATED on load (TABLES.md §6). Returns
    (tables, warnings); later table names win on PGN collision."""
    from . import validate as _v
    merged, warnings = {}, []
    for t in list_tables():
        if not t["enabled"]:
            continue
        try:
            tables, warns = _v.validate_file(table_path(t["name"]))
        except _v.TableInvalid as e:
            warnings.append(f"{t['name']}: {e} — table skipped")
            continue
        warnings.extend(f"{t['name']}: {w}" for w in warns)
        for pgn in merged.keys() & tables.keys():
            warnings.append(f"{t['name']}: PGN {pgn} overrides an earlier table")
        merged.update(tables)
    return merged, warnings
