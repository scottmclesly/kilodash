"""Schema validator for the Canboat-JSON subset defined in TABLES.md §2.

The one validator both ends of the contract run: the converter calls it on
ingest, the NMEA2K screen calls it again on every load — defense in depth,
so a hand-copied file that skipped the converter still gets validated before
it can drive decode.

Two-tier by design (TABLES.md §2): a malformed *file* raises
TableInvalid; a malformed *entry* is skipped with a warning while the rest
of the file loads. Unknown keys are ignored, never fatal.
"""

import json

NAME_MAX = 64
FIELD_BITS_MAX = 64


class TableInvalid(ValueError):
    """The file as a whole is unusable (not JSON / no valid PGN entries)."""


def _norm_lookup(fld):
    """Accept our `Lookup` dict or Canboat's `EnumValues` list; return a
    {raw_decimal_string: label} dict or None."""
    lk = fld.get("Lookup")
    if isinstance(lk, dict):
        out = {}
        for k, v in lk.items():
            try:
                out[str(int(k))] = str(v)
            except (TypeError, ValueError):
                continue
        return out or None
    ev = fld.get("EnumValues")
    if isinstance(ev, list):
        out = {}
        for item in ev:
            if not isinstance(item, dict):
                continue
            try:
                out[str(int(item["value"]))] = str(item["name"])
            except (KeyError, TypeError, ValueError):
                continue
        return out or None
    return None


def _norm_field(fld):
    """Normalize one field dict, or raise ValueError describing the defect."""
    if not isinstance(fld, dict):
        raise ValueError("field is not an object")
    name = fld.get("Name") or fld.get("Id")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("field has no Name")
    try:
        bit_offset = int(fld["BitOffset"])
        bit_length = int(fld["BitLength"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"field {name!r}: BitOffset/BitLength missing or "
                         "not integers")
    if bit_offset < 0:
        raise ValueError(f"field {name!r}: negative BitOffset")
    if not 1 <= bit_length <= FIELD_BITS_MAX:
        raise ValueError(f"field {name!r}: BitLength out of 1..{FIELD_BITS_MAX}")
    try:
        resolution = float(fld.get("Resolution", 1) or 1)
        eng_offset = float(fld.get("Offset", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError(f"field {name!r}: Resolution/Offset not numeric")
    units = fld.get("Units", "")
    return {
        "name": name.strip(),
        "bit_offset": bit_offset,
        "bit_length": bit_length,
        "resolution": resolution,
        "offset": eng_offset,
        "signed": bool(fld.get("Signed", False)),
        "units": str(units) if units is not None else "",
        "lookup": _norm_lookup(fld),
    }


def _fast_packet(entry):
    """`FastPacket` bool, or Canboat `Type: "Fast"`."""
    fp = entry.get("FastPacket")
    if isinstance(fp, bool):
        return fp
    return str(entry.get("Type", "")).lower() == "fast"


def validate(obj):
    """Validate a parsed table object against TABLES.md §2.

    Returns (tables, warnings):
      tables   — {pgn: {"pgn", "name", "fast", "fields": [normalized…]}}
      warnings — human-readable strings for every skipped entry/field
    Raises TableInvalid when nothing usable survives.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("PGNs"), list):
        raise TableInvalid("not a table: no PGNs array")
    tables, warnings = {}, []
    for i, entry in enumerate(obj["PGNs"]):
        if not isinstance(entry, dict):
            warnings.append(f"PGNs[{i}]: not an object — skipped")
            continue
        try:
            pgn = int(entry["PGN"])
            if not 0 < pgn <= 0x3FFFF:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            warnings.append(f"PGNs[{i}]: missing/invalid PGN — skipped")
            continue
        name = entry.get("Name") or entry.get("Description") or f"PGN {pgn}"
        raw_fields = entry.get("Fields", [])
        if not isinstance(raw_fields, list):
            warnings.append(f"PGN {pgn}: Fields is not a list — skipped")
            continue
        fields = []
        for fld in raw_fields:
            try:
                fields.append(_norm_field(fld))
            except ValueError as e:
                warnings.append(f"PGN {pgn}: {e} — field skipped")
        if not fields:
            warnings.append(f"PGN {pgn}: no usable fields — skipped")
            continue
        if pgn in tables:
            warnings.append(f"PGN {pgn}: duplicate entry — later one wins")
        tables[pgn] = {"pgn": pgn, "name": str(name)[:80],
                       "fast": _fast_packet(entry), "fields": fields}
    if not tables:
        raise TableInvalid("no valid PGN entries"
                           + (f" ({warnings[0]})" if warnings else ""))
    return tables, warnings


def validate_bytes(raw):
    """Validate raw file bytes/str. TableInvalid on undecodable input."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        obj = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as e:
        raise TableInvalid(f"not JSON: {e}")
    return validate(obj)


def validate_file(path):
    """Validate a table file on disk. TableInvalid on unreadable input."""
    try:
        with open(path, "rb") as f:
            return validate_bytes(f.read())
    except OSError as e:
        raise TableInvalid(f"unreadable: {e}")
