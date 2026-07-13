# TABLES.md — the decode-table store contract

Like [`PROTOCOL.md`](To-DoLists/PROTOCOL.md) between the CanTick firmware and kilodash,
this file is the **only** coupling between the pieces that touch decode
tables:

| Party | Role |
|---|---|
| **Converter service** (`kilodash/tableconv.py`, Tables tile → web app) | **the writer** — ingest, validate, install, remove |
| **NMEA2K screen** (`kilodash/screens/n2k.py`) | reader — enabled PGN tables drive live decode |
| **DBC screen** (future) | reader — `tables/dbc/` |
| **Wio Terminal Island** | reader — same JSON, exported flat onto SD/USB |
| **Tables tile** (`kilodash/screens/tables.py`) | mirror — reads the store; its only write is the atomic manifest `enabled` flip |

Spec first, consumers second: anything not written here is not part of the
contract, and no consumer may rely on it.

## 1. Directory layout

Repo `tables/` — runtime `/opt/kilodash/tables/` (override for tests with
`KILODASH_TABLES`):

```
tables/
├── pgn/                    # NMEA2000 PGN tables (Canboat-style JSON)
│   ├── <name>.json         # the table (§2)
│   └── <name>.meta.json    # its manifest sidecar (§3)
├── dbc/                    # raw .dbc signal databases (future DBC screen)
├── uploads/                # converter scratch (uploaded PDFs); never read
│                           # by any consumer, purged by the converter
└── *                       # loose files in the root are the INBOX (§6)
```

`<name>` is `[a-z0-9_-]{1,64}` — derived from the source document, unique
within `pgn/`.

## 2. The Canboat-JSON subset we consume

A table file is a JSON object with a `PGNs` array. Consumers use **only** the
keys below; anything else (extra Canboat keys, vendor annotations) is
**ignored, never fatal**. Canonical Canboat spellings are accepted where
noted.

```jsonc
{
  "PGNs": [
    {
      "PGN": 127508,                  // required, int
      "Name": "Battery Status",       // or Canboat "Description"
      "FastPacket": false,            // or Canboat "Type": "Fast"/"Single"
      "Fields": [
        {
          "Name": "Voltage",          // required, string
          "BitOffset": 8,             // required, int ≥0 (LSB-first packing)
          "BitLength": 16,            // required, int 1..64
          "Resolution": 0.01,         // default 1
          "Offset": 0,                // engineering offset, default 0
          "Signed": false,            // default false
          "Units": "V",               // default ""
          "Lookup": {"0": "Off"}      // or Canboat "EnumValues":
                                      //   [{"name": "Off", "value": "0"}]
        }
      ]
    }
  ]
}
```

Decode semantics (what the NMEA2K screen does with this):

- Fields are extracted from the **assembled** payload (fast-packet
  reassembly happens *before* table lookup), LSB-first:
  `raw = (payload_as_little_endian_int >> BitOffset) & ((1<<BitLength)-1)`.
- `Signed` fields are two's-complement over `BitLength`.
- The all-ones raw value of an unsigned field (and max-positive of a signed
  one, per N2K convention) means **not available** → rendered `—`, never fed
  to alerts. Applies to fields wider than 1 bit — a 1-bit flag's `1` is a
  real value.
- Display value = `raw * Resolution + Offset`; `Lookup` (keyed by the *raw*
  value as a decimal string) wins over numeric rendering when it matches.

Validation is **two-tier**: a malformed *file* (not JSON, no usable `PGNs`)
is rejected outright; a malformed *entry* (bad field, missing `PGN`) is
skipped with a warning while the rest of the file loads. A skipped entry
must never take the file — or the screen — down with it.

## 3. The manifest sidecar — `<name>.meta.json`

Written atomically by the converter next to every installed table:

```jsonc
{
  "name": "victron_battery",          // == file stem
  "source_doc": "VE.Can-registers.pdf", // what it was converted from
  "converted": "2026-07-12T14:03:00Z", // ISO-8601 UTC conversion time
  "converter_version": "1.0",          // kilodash.tableconv.VERSION
  "enabled": true,                     // the ONLY key any non-converter
                                       // party may flip (tile, atomically)
  "pgn_count": 12,                     // valid entries at ingest — so
                                       // manifest-only readers (the tile)
                                       // never parse table files
  "sha256": "…"                        // hex digest of <name>.json as written
}
```

- A table with **no manifest** or a **stale `sha256`** is shown in
  inventories as *unverified* and is **not** loaded for decode until the
  converter re-ingests it.
- `enabled: false` removes the table from decode without deleting anything.

## 4. Who reads, who writes

**Consumers only read; the converter only writes.**

- The NMEA2K screen (and every future reader) never mutates the store — not
  even to "fix" a file. The converter never decodes live traffic.
- The Tables tile's enable/disable toggle is the single sanctioned
  exception: it rewrites only the manifest, tmp-file + `os.replace()`
  atomic, never the table itself.
- **No third writer, ever.** The Files screen's USB import drops files in
  the root inbox (§6); it does not write `pgn/`.
- All converter writes are tmp-file + atomic rename in the same directory,
  so a killed service (idle timeout, power pull) never leaves a
  half-written table or manifest.

## 5. SD-export shape (Wio Terminal Island)

One conversion effort feeds both devices. The export is the same JSON,
**flat** (no subdirectories):

```
<media>/scottina/tables/
├── victron_battery.json
├── victron_battery.meta.json
└── …
```

Producers of this shape: the Files screen's *Tables → USB* export and the
web app's per-table *download*. Wio Terminal Island reads `*.json`
(skipping `*.meta.json`), applying §2 verbatim — including the
ignore-unknown-keys and skip-bad-entries rules.

## 6. Validation & the inbox

`tables/validate.py` is the **shared schema validator** — the same module
runs in the converter (on ingest) and in the NMEA2K screen (on every load).
Defense in depth: a hand-copied file that skips the converter still gets
validated before it can drive decode.

Loose files in the `tables/` root (USB imports from the Files screen,
`scp`-ed files) are the **inbox**: inert until the converter's *Installed*
tab ingests them (validate → move into `pgn/` → write manifest). Consumers
never read the inbox.

## 7. Consumers of this contract

- kilodash NMEA2K screen — [`kilodash/screens/n2k.py`](kilodash/screens/n2k.py)
- kilodash Tables tile — [`kilodash/screens/tables.py`](kilodash/screens/tables.py)
- Converter service — [`kilodash/tableconv.py`](kilodash/tableconv.py)
- Wio Terminal Island — SD reader (see that repo's spec; it links back here)
- Future DBC screen — `tables/dbc/`, same manifest scheme, format TBD there

Changing anything in §1–§6 means updating **all** of the above in one
change, or not making the change.
