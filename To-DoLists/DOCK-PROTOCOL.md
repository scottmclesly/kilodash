# DOCK-PROTOCOL.md

**Status:** v0.1 — DRAFT, awaiting ratification by both sides.
**Protocol version:** `1`
**Mirrored verbatim** in [Scottina-Light](https://github.com/scottmclesly/Scottina-Light)
(firmware, responder) and kilodash (Prime, initiator). Neither side changes this
file alone; a change is a PR against both, exactly as `PROTOCOL.md` couples
kilodash and CanTick.

---

## What this is

The wire contract for **Light Dock**: when Scottina Light is plugged into
Scottina Prime over USB, Prime pushes wall-clock time and decode tables, and
pulls Light's SD logs.

**Prime always wins.** Nothing is ever edited on Light; conflicts are
overwritten, never merged.

## Scope constraint (hard, both sides)

This protocol is **provisioning + retrieval only**. It can set the clock, write
tables and Tier-3 config, and read *closed* log files.

It deliberately **cannot**: trigger transmission, start or stop a capture
remotely, execute anything, or read Tier-2 config. There is no command that
does, and adding one is a scope violation — see Light's `include/scope.h`.

The reject pass (§7) exists so this is enforced, not merely intended.

---

## 1. Transport

USB CDC (ACM). Native full speed; **the nominal baud rate is ignored** — set
anything.

- **Prime is the only initiator.** Light never speaks unsolicited.
- **Strictly sequential.** Exactly one request may be outstanding. No pipelining,
  no interleaving. This is what lets both implementations stay simple.
- Light is identified by **USB VID + product string**, never by ttyACM index.
  (CanTick is also a CDC device on the same bench. See Known Gotchas in
  `LightDock-TODO.md`.) The concrete VID:PID is recorded in Phase 0 — this doc
  does not hardcode it.

## 2. Framing

Sync-safe. Never bare characters — on a `SL_TEST_HOOK=1` dev build, single
serial bytes are button presses.

```
  offset  size  field
  0       1     SOF    = 0xA5
  1       1     TYPE   (see §4)
  2       1     SEQ    (request; the response echoes it)
  3       2     LEN    payload length, uint16 little-endian
  5       LEN   PAYLOAD
  5+LEN   2     CRC16  over TYPE..PAYLOAD inclusive, uint16 little-endian
```

Frame overhead is 7 bytes.

**`SOF = 0xA5` is chosen with the high bit set** so it can never be produced by
the test hook's ASCII alphabet (`a b c u d l r p`), never appears in Light's
ASCII `SMOKE` boot output, and cannot be typed by accident in a serial monitor.

**CRC-16/CCITT-FALSE**: poly `0x1021`, init `0xFFFF`, no input/output
reflection, no final XOR. Check value for the ASCII string `123456789` is
`0x29B1` — both sides MUST assert this in a unit test.

**Resync rule (bounded reads, both sides).** Scan for `SOF`. If `LEN` exceeds
the peer's advertised `max_payload`, or the CRC fails, discard exactly one byte
and rescan from the next. Never allocate or block on an unvalidated `LEN`.

**Endianness is little for every multi-byte integer in this document.**

### Strings

Length-prefixed, `uint8` length, UTF-8, **no NUL terminator**. Max 255 bytes.
Paths are absolute, `/`-separated (`/logs/raw000.log`).

## 3. Payload size is negotiated, not fixed

Light advertises `max_payload` in its `HELLO` response. Prime MUST NOT send a
frame whose payload exceeds it, and MUST NOT request a `GET` chunk larger than
it.

This is deliberate: it decouples the wire format from the Phase-0 throughput
measurement. The bench numbers set the *progress-bar math* and Prime's chosen
chunk size — they do not change this contract, and raising Light's buffer later
is not a protocol version bump.

Light's v1 value is expected to be **1024**; Prime must read it, not assume it.

## 4. Command set

The complete surface. A positive allow-list: anything not in this table is
`ERR_UNKNOWN_TYPE`.

| Type   | Name        | Direction        |
|--------|-------------|------------------|
| `0x01` | `HELLO`     | Prime → Light    |
| `0x02` | `SET_CLOCK` | Prime → Light    |
| `0x03` | `LIST`      | Prime → Light    |
| `0x04` | `PUT`       | Prime → Light    |
| `0x05` | `COMMIT`    | Prime → Light    |
| `0x06` | `GET`       | Prime → Light    |
| `0x07` | `DELETE`    | Prime → Light    |
| `0x08` | `BYE`       | Prime → Light    |
| `0xEF` | `ERROR`     | Light → Prime    |

A response reuses the request's `TYPE` and `SEQ`. Direction disambiguates —
Prime only sends requests, Light only sends responses. Any request may instead
be answered with `ERROR`.

### `0x01 HELLO`

Request: empty.

Response:

```
  u16     proto_version          (1)
  str     product                ("Scottina Light")
  str     fw_version             ("v1-foundation")
  u8      sd_present             0 | 1
  u64     clock_epoch            unix seconds; 0 if never set
  u8      clock_quality          0 unsynced | 1 rtc | 2 ntp
  u8      clock_set_this_boot    0 | 1
  u16     max_payload            bytes (see §3)
  u8      flags                  bit0: logging was active at dock
                                 bit1: logging is currently suspended by dock
```

`clock_set_this_boot` is the honest signal Prime needs: Light has **no
battery-backed RTC**. The SAMD51 RTC survives a reset but not a power cycle, so
a nonzero `clock_epoch` with `clock_set_this_boot = 0` means "this survived a
warm reset and is probably still good"; power-on gives `epoch = 0,
quality = unsynced`.

`HELLO` is the only command every protocol version must support (§8).

### `0x02 SET_CLOCK`

Request:

```
  u64     epoch                  unix seconds, UTC
  u8      quality                0 unsynced | 1 rtc | 2 ntp
```

Response: `u8 ok`, `u64 epoch_echo`.

**Prime MUST send its honest quality.** Pi 5's RTC only holds through power-off
with the coin cell fitted; Prime must not claim `ntp` unless NTP is
*synchronized right now*. A bad clock is labeled, never laundered into Light's
logs as truth.

Light records the quality alongside the epoch and writes both into the header
of every subsequent log file, so forensics knows what it is trusting.

Prime MUST NOT send `quality = 0` (unsynced) with a nonzero epoch — if Prime's
clock is untrustworthy it should say so and let Light stay at zero rather than
stamp logs with a lie.

### `0x03 LIST`

Request:

```
  str     dir                    "/logs/" or "/tables/" only
  u8      want_hashes            0 | 1
```

Response:

```
  u16     count
  count × {
    str   name                   leaf name, no directory part
    u32   size
    u64   mtime                  unix seconds; 0 if unknown (see below)
    u8    sha256_present         0 | 1
    32B   sha256                 present only when sha256_present = 1
  }
```

`want_hashes` exists because the two directories have different economics.
Tables are kilobytes — Prime diffs them by name + sha256, so it asks for hashes
and gets a cheap, exact answer. Logs are megabytes — hashing every one on every
`LIST` would cost seconds for nothing, so Prime asks without hashes and diffs by
name + size first, taking the hash it computes during the pull itself (§`GET`).

Light MAY return `sha256_present = 0` for any entry it declines to hash.

`mtime` is **advisory only**. FAT mtimes are meaningless on a card that was
written while Light's clock was unset, and the underlying FS layer may not
expose them at all. Prime MUST NOT make correctness decisions from `mtime`;
the diff is name + size + sha256.

**The currently-open log file is never listed.** See §6.

### `0x04 PUT`

Request:

```
  str     path                   destination; must match a writable prefix (§7)
  u32     offset
  u16     chunk_len
  chunk_len bytes
```

Response: `u8 ok`, `u32 total_bytes_staged`.

`offset = 0` creates or truncates the staging file. Chunks MUST arrive in
ascending, contiguous order; a gap is `ERR_IO`.

**Writes are staged, never live.** Chunks land in a staging file
(`<path>.partial`), not at `path`. The destination does not exist, and is not
modified, until `COMMIT` succeeds. Power loss mid-`PUT` leaves only a
`.partial`, which Light sweeps at boot.

### `0x05 COMMIT`

Request:

```
  str     path
  32B     sha256                 of the complete intended file contents
```

Response: `u8 ok`.

Light hashes the staged file, compares, and **only then** atomically renames it
to `path`. Mismatch → `ERR_HASH_MISMATCH`, and the staging file is unlinked.

**No commit, no file.** This is the whole of the interruption story: there is no
resume state persisted anywhere, on either side. A yanked cable leaves a
`.partial` and nothing else; the next dock reruns the diff and re-pushes what is
missing. Atomicity replaces bookkeeping.

Atomic rename is the strongest primitive FAT offers — there is no journal. That
is why `COMMIT` verifies before renaming rather than after.

### `0x06 GET`

Request:

```
  str     path                   under /logs/ only
  u32     offset
  u16     max_len                MUST be ≤ max_payload
```

Response:

```
  u32     offset                 echoed
  u16     len
  len bytes
  u8      eof                    1 when this chunk reaches end of file
```

Reads **closed files only**. The logger's active file is never served (§6).

**Light hashes as it serves.** While streaming a file from `offset = 0` to
`eof`, Light computes the sha256 of exactly the bytes it sent and caches
`(path, size, digest)`. This is what makes `DELETE` cheap — see below.

### `0x07 DELETE`

Request:

```
  str     path                   under /logs/ only
  32B     sha256                 the digest Prime computed over what it RECEIVED
```

Response: `u8 ok`.

Light compares Prime's digest against its own for that file. **Match, and only
match, unlinks.** Mismatch → `ERR_HASH_MISMATCH`, file untouched.

If Light has a cached digest from the `GET` that just streamed the file, it uses
it — the cached digest is over exactly the bytes it put on the wire, which is
precisely the claim being checked. With no cached digest (a fresh dock session,
say), Light recomputes by reading the file. Caching is an optimization; the
check is not optional.

This is the scary step and it is deliberately the most defended. **A pull that
verifies nothing is a copy; a delete that verifies nothing is data loss on a
black box.**

### `0x08 BYE`

Request: empty. Response: `u8 ok`.

Light closes any open handle, resumes standalone behavior, and **resumes logging
if the dock suspended it** (§6).

### `0xEF ERROR`

Response only:

```
  u16     code
  str     message                human-readable; for Prime's session log
```

| Code     | Name                  | Meaning |
|----------|-----------------------|---------|
| `0x0001` | `ERR_BAD_CRC`         | frame CRC failed |
| `0x0002` | `ERR_BAD_FRAME`       | malformed / truncated / LEN over max |
| `0x0003` | `ERR_UNKNOWN_TYPE`    | not in the allow-list |
| `0x0004` | `ERR_NO_SD`           | no card mounted |
| `0x0005` | `ERR_PATH_REJECTED`   | failed the reject pass (§7) |
| `0x0006` | `ERR_NOT_FOUND`       | path does not exist |
| `0x0007` | `ERR_IO`              | read/write/rename failed |
| `0x0008` | `ERR_HASH_MISMATCH`   | COMMIT or DELETE digest mismatch |
| `0x0009` | `ERR_BUSY`            | file in use (e.g. the active log) |
| `0x000A` | `ERR_UNSUPPORTED_VER` | protocol version mismatch (§8) |
| `0x000B` | `ERR_TOO_LARGE`       | payload or requested chunk over max |

`ERR_NO_SD` is a first-class answer, not a timeout. With no card, `HELLO` says
so and `LIST`/`PUT`/`GET`/`DELETE` return it cleanly, so Prime's session log can
tell the truth: *"clock synced; tables skipped — no SD in Light."*

## 5. Timeouts

Prime puts a timeout on **every** request. A wedged Light must degrade to a
truthful log line, never a hung screen.

| Request              | Prime timeout |
|----------------------|---------------|
| `HELLO`              | 2 s |
| `SET_CLOCK`, `LIST`, `PUT`, `GET`, `BYE` | 2 s |
| `DELETE`             | 30 s (may need to rehash a multi-MB file) |
| `COMMIT`             | 30 s (hashes the staged file) |

**Light-side dock watchdog: 10 s.** If no valid frame arrives while a dock
session is open, Light performs an implicit `BYE` — closes handles, resumes
logging, returns to standalone. A yanked cable must never leave Light with
logging switched off. This is the one place Light acts without being asked, and
it exists to protect the black box.

## 6. Logging during a dock session

**A dock session suspends logging for its duration.**

This is forced, not chosen. Light's FS layer permits **exactly one open file at
a time** (`src/core_storage.cpp`) — the logger owns the handle while a capture
runs, and the existing code already refuses config writes rather than yank it
away. The dock cannot serve a `GET` while the logger holds that handle, so
"rotate and keep logging" is not available.

The rule, therefore:

1. On the first valid framed request, Light closes the active log file (if any)
   and marks logging **suspended**. The closed file is complete and immediately
   listable.
2. `HELLO`'s `flags` report both that logging *was* active and that it is *now*
   suspended — Prime shows this in the session log. The suspension is stated,
   never silent.
3. On `BYE`, or on the 10 s watchdog, logging resumes into a **fresh file**.
4. That fresh file's header records the gap: the dock window, and the new clock
   epoch + quality if `SET_CLOCK` ran.

A dock is a bench event lasting seconds, and the gap is written into the record.
This is the honest version of the same cost USB mass-storage would have imposed
silently — and unlike MSC, there is no two-writer corruption hazard, because
firmware mediates every byte.

## 7. Path rules

**Writable prefixes** (allow-list, enforced Light-side):

- `/tables/`
- `/config.json` (Tier 3, exact path)

**Readable prefixes:**

- `/logs/` (`GET`, `LIST`)
- `/tables/` (`LIST`)

**Deletable prefix:**

- `/logs/` only, and only after a verified pull.

### The reject pass (defense in depth, independent of the allow-list)

Every one of these is `ERR_PATH_REJECTED`, and every one is **logged on Light**:

- Any path containing `..`, a backslash, a NUL, or a non-`/`-rooted prefix.
- Any path escaping the two roots after normalization.
- Any **write** to `/logs/` — the black box is append-only from the outside.
- Any **read** of `/config/` — Tier-2 files are the user's on-device choices and
  are none of Prime's business.
- Any unknown type code.

The allow-list alone would be sufficient. The reject pass exists because
"sufficient" is not the standard for the one component that can delete a black
box.

## 8. Versioning

`HELLO` carries `proto_version`. On mismatch, Prime **degrades to clock-set
only** — `HELLO` + `SET_CLOCK` + `BYE`, the operations every version must
support — and says so in the session log. It does not attempt tables or logs.

Every future version keeps `HELLO` and `SET_CLOCK` wire-compatible. That is the
only compatibility promise.

## 9. Table shape on Light's SD

`/tables/` is a flat directory of the same Canboat-subset JSON as Prime's table
store. **This is the SD-export shape already promised in kilodash's
`TABLES.md`** — that document is the schema's single source of truth. Do not
restate it here; when it changes, it changes there.

Light stores what it is given. As of `v1-foundation` the decode layer is not yet
written, so nothing on Light *reads* `/tables/` — the dock provisions ahead of
its consumer, deliberately. A successful table push with no visible effect on
Light is correct behavior, not a bug.

## 10. Conformance vectors

`dock-vectors.json` — committed to **both** repos, next to this file — is the
shared conformance asset. It holds hex-encoded frames and expected responses:
every command's happy path, every error code, and the framing edge cases
(bad CRC, oversized `LEN`, garbage-before-SOF resync, a bare `a` from the test
hook's alphabet arriving mid-stream).

Both sides test against it **before** the two devices ever meet on a bench:

- **Light** runs the vectors through its frame codec as a unit test.
- **Prime** runs its sync engine against a fake Light that replays the vectors,
  with no firmware in the loop.

The vectors are the reason the two lanes can be built in parallel and still
meet. A change to this document that does not update the vectors is incomplete.

---

## Deliberately deferred

**Live streaming and sensor fusion.** Streaming IMU/CAN over the dock was
considered and rejected for v1: standalone logging plus clock sync covers the
forensic case, which is the case that exists. If real-time fusion ever earns its
keep, it is a new protocol version — not a bolt-on to this one.

## Open decisions (resolve before v1.0)

- [ ] **VID:PID and product string** — Phase 0 records what actually enumerates.
      Nothing is hardcoded from memory.
- [ ] **`max_payload`** — Light's v1 value, confirmed against the Phase-0
      throughput measurement.
- [ ] **Ratification** — both sides sign off, this file drops to `v1.0`, and the
      DRAFT banner comes off.
