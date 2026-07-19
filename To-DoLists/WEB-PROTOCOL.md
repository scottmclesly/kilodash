# WEB-PROTOCOL.md — Scottina Web Mirror, Wire Contract v1.0

**Status:** v1.0 — DRAFT, proposed 2026-07-18. Not yet ratified; no producer or
consumer written against it. Freeze before Phase 1 code lands.
**Protocol version:** `1` (the `version` field in every frame; see §9)
**Couples** kilodash (the box, event producer + command sink) and
`kilodash.webmirror` (the on-device web backend, event consumer + command
relay) and the React bundle it serves. All three change together or not at all:
a change here is one commit touching the emitter, the backend, and the
conformance vectors, exactly as `DOCK-PROTOCOL.md` couples Prime and Light.

---

## What this is

The wire contract for the **Web Mirror**: a LAN-reachable web front-end served
from the Pi that renders the *same live state* as the 3.5″ touchscreen, with
symmetric input from either surface.

**The Pi is the single source of truth.** The web is a subscriber. On any
divergence the web resyncs to the box; it never asserts its own state. One
device, one state — a tile change from the web *becomes* the tile on the panel,
and vice versa. There is no independent web navigation session.

The web renders a **structured model the box emits**. It is never a framebuffer
screencap, and this document never describes pixels.

## Scope constraint (hard, both sides)

The web mirror is **diagnostics only**. It commands the same input state machine
the touch panel does and nothing more.

It deliberately **cannot** introduce any transmit surface. Every action
expressible in §6 must already be reachable by a finger on the panel. The CAN
TX/RX exception is unchanged and lives entirely in the link layer
(heartbeat/reply only, never user-expressible); the web adds **no** new TX path.

The command allow-list (§6) is positive and closed: an action not in the table
is rejected, not passed through. That is what makes this enforced rather than
merely intended.

---

## 1. Transport

Two deliberately asymmetric channels. **Do not collapse them.**

| Direction | Channel | Framing |
|---|---|---|
| Events out (box → web) | Unix domain socket, `SOCK_STREAM` | newline-delimited JSON (§2) |
| Events out (backend → browser) | WebSocket | one JSON frame per message |
| Commands in (browser → backend) | HTTP `POST /api/input` | JSON body (§6) |
| Commands in (backend → box) | the same Unix socket, reverse direction | newline-delimited JSON |

**Why the asymmetry.** Events out are the narrative — the box is master and
drives what is true, continuously, at machine pace. Commands in are slow,
discrete, and user-paced; REST fits them and gives a status code per action.
Making the command channel a back-channel over the event socket would let a
stalled subscriber's queue reorder or delay input, and would blur which side is
authoritative. Keep them separate.

**Socket path:** `/run/kilodash/events.sock`, created by
`RuntimeDirectory=kilodash` in `kilodash.service`. Mode `0660`, owned
`root:root` (both units run as root today). `/tmp` is not used — it is
world-writable and survives across the boot in ways a runtime socket should not.

**The box is the server**, the web backend is the client, and the box listens
with a backlog of 1: **at most one subscriber is supported.** A second connect
is accepted and immediately closed with an `Error` frame (§8). One box, one
mirror service — fan-out to many browsers is the backend's job, not the box's.

**Connection is not required.** kilodash boots, runs, and is fully usable with
nothing listening. See §7.

## 2. Event framing (box → web)

One JSON object per line, UTF-8, `\n` terminated, no embedded raw newlines
(JSON string escapes only). No length prefix, no CRC — this is a loopback Unix
socket with kernel-guaranteed ordering and integrity; framing exists only to
delimit objects.

Every frame carries the same envelope:

```json
{
  "v": 1,
  "type": "DataUpdated",
  "seq": 4812,
  "t": 1752871234.518,
  "...": "type-specific fields"
}
```

| Field | Type | Meaning |
|---|---|---|
| `v` | int | protocol version, always `1` in this document (§9) |
| `type` | string | one of §3's frame types; unknown types are ignored, not fatal |
| `seq` | int | monotonic per-connection counter, starts at `1`, never resets while connected |
| `t` | float | `time.time()` at emit, seconds. Advisory — for staleness display only, never for ordering |

**`seq` is the ordering and gap-detection authority, not `t`.** A subscriber
that receives `seq` out of order or with a gap MUST discard its model and
request a fresh snapshot (§5). Wall-clock can step (the box takes time from
GPS); `seq` cannot. `seq` never resets on a live connection, including across a
mid-stream `Hello` (§3) — only a new connection restarts it at `1`.

**Maximum frame size is 64 KiB.** A model that would exceed it is truncated by
the producer per §4's row caps, never emitted oversized. A consumer receiving a
longer line MUST drop the connection and reconnect rather than buffer it.

## 3. Frame types (box → web)

The complete surface. A positive allow-list.

| `type` | When |
|---|---|
| `Hello` | first frame on every connection, before anything else |
| `ScreenSnapshot` | after `Hello`, and on every `RequestSnapshot` (§6) |
| `TileChanged` | active screen changed (either surface caused it) |
| `DataUpdated` | active screen's model changed (§4) |
| `AlertFired` | an alert edge-triggered on |
| `AlertCleared` | an alert cleared or decayed |
| `Error` | the box rejects something; see §8 |

### `Hello`

```json
{
  "v": 1, "type": "Hello", "seq": 1, "t": 1752871200.0,
  "device": "scottina-prime",
  "kilodash_version": "1.4.2",
  "protocol": 1,
  "theme": {
    "name": "green",
    "bg": [0,9,3], "card": [3,26,10], "card_hi": [8,46,18],
    "fg": [51,245,70], "muted": [0,150,40], "accent": [130,255,120],
    "ok": [51,235,80], "warn": [255,190,40], "bad": [255,75,60]
  }
}
```

**The theme block is normative for the web's palette.** The mirror inherits the
box's active CRT skin (`green` / `amber` / `light`) so both surfaces read as one
instrument. The web MUST derive its colours from these tokens rather than
hardcoding a palette, and MUST honour the same semantics the panel does:

> `bad` (red) is reserved for **actual faults**. Caution amber (`warn`) carries
> stand-by / armed / rest states. `ok` is active-good. Chrome stays monochrome
> in the phosphor colour.

A theme change on the box re-emits `Hello` — the one frame type that may appear
mid-stream — carrying the **current monotonic `seq`**, never `seq: 1` again (the
`seq: 1` in the example above is the first-frame case only). A mid-stream
`Hello` updates the palette **and nothing else**: the consumer re-themes live
and MUST NOT treat it as a new connection, MUST NOT discard its model, and MUST
NOT resync. Only a fresh socket / WebSocket connection begins with `seq: 1` and
the snapshot handshake of §5. Everything else about the Alien /
Semiotic-Standard instrument idiom — hard-edged strips, spaced-caps mono
readouts, segmented gauges, corner registration brackets, hazard striping — is
the web's own presentation concern and is **not** specified here; see
`design-system-alien` and `widgets.py`. This document carries the tokens and the
colour semantics only, because those are shared truth.

### `ScreenSnapshot`

The complete current model. Everything a fresh client needs to render, with no
prior state.

```json
{
  "v": 1, "type": "ScreenSnapshot", "seq": 2, "t": 1752871200.1,
  "tile": "can-bus",
  "nav": ["home", "can-bus"],
  "rev": 118,
  "tiles": [
    {"id": "can-bus", "title": "CAN Bus", "glyph": "can", "available": true},
    {"id": "gps", "title": "GPS", "glyph": "gps", "available": false}
  ],
  "model": { "kind": "canbus", "...": "see §4" },
  "alerts": [ { "...": "see AlertFired" } ]
}
```

`tiles` is the launcher inventory including unavailable (hotplug-absent) tiles,
so the web can render them dimmed exactly as the panel does rather than having
them vanish.

`rev` is the model revision counter — see `DataUpdated`.

### `TileChanged`

```json
{
  "v": 1, "type": "TileChanged", "seq": 4813, "t": 1752871240.0,
  "tile": "n2k", "nav": ["home", "n2k"], "rev": 0,
  "model": { "kind": "n2k", "...": "full model for the new screen" }
}
```

**`TileChanged` always carries the full model of the screen being entered**, and
resets `rev` to `0`. It is a snapshot scoped to one screen; the web never has to
ask for one after a nav.

**`nav` is at most two deep.** kilodash is a two-level star — launcher ↔ one
screen — with a back *hit-box*, not a stack. `nav` is `["home"]` on the launcher
and `["home", "<tile_id>"]` otherwise. It is an array for forward compatibility
only; v1 consumers MUST NOT assume it can grow, and v1 producers MUST NOT emit a
third element.

### `DataUpdated`

The delta. Only the top-level model keys whose value changed since the last
frame for this screen.

```json
{
  "v": 1, "type": "DataUpdated", "seq": 4814, "t": 1752871240.5,
  "tile": "can-bus", "rev": 119,
  "changed": { "rows": [ "..." ], "frame_rate": 812 }
}
```

- `rev` is assigned **at model-change time, before emission.** A frame that is
  coalesced or superseded after its `rev` is assigned (§7) still consumes that
  number, so its absence is a permanent, detectable gap — this is deliberate.
  `rev` increments by exactly 1 per assigned `DataUpdated` for the current
  screen and resets to `0` on `TileChanged`. **A consumer that sees `rev` skip
  MUST request a snapshot** (§6 `request_snapshot`) rather than patch: a gap
  means an intermediate state was merged or lost, and a shallow-merge patch onto
  the wrong base is exactly the silent divergence §9 forbids. Assigning `rev` at
  send time is a conformance error — it makes coalescing invisible.
- `changed` is a **shallow merge** at the top level of `model`. A key present
  replaces that key's value wholesale; a key absent is unchanged. There is no
  deep-merge, no array patching, no JSON Patch. Arrays are always sent whole.
  This is deliberate: row-level patching for a CAN table at bus rate costs more
  to compute and verify than it saves on a loopback socket.
- `tile` is echoed so a consumer can drop a delta that arrives for a screen it
  has already navigated away from.

### `AlertFired` / `AlertCleared`

Mirrors the existing NMEA2K `AlertBook` vocabulary (`kilodash/n2k.py`) rather
than inventing a second one. Alerts are **non-modal** — badge and row flash,
never a dialog — on both surfaces.

```json
{
  "v": 1, "type": "AlertFired", "seq": 4820, "t": 1752871245.0,
  "alert": {
    "id": "n2k:127488:0:range",
    "tile": "n2k",
    "kind": "range",
    "label": "ENGINE RPM",
    "detail": "4210 > 4000",
    "severity": "fault",
    "t_fired": 1752871245.0
  }
}
```

| Field | Notes |
|---|---|
| `id` | stable identity; `AlertCleared` carries the same `id` and nothing else required |
| `kind` | `range` \| `appearance` \| `match` \| `change` — the existing AlertBook + busmon watch kinds |
| `severity` | `fault` \| `caution`. **`fault` is the only thing that may render red** (§3 `Hello`). An armed-but-unfired watch is not an alert and is never emitted |
| `t_fired` | for the decay window; the web fades the flash on the same schedule the panel does |

Alerts are **edge-triggered**. A stuck-bad value fires once and does not refire.
Alerts survive a tile change (they are box-scoped, not screen-scoped) and appear
in `ScreenSnapshot.alerts`.

## 4. Screen models — the `model` object

**`model.kind` selects the renderer.** Every model carries it, and it is a
stable identifier, never a display title.

### The declaration rule (this is the anti-drift mechanism)

Each `Screen` subclass declares the model it emits **in one place** — a
`model()` method on the screen itself, returning a plain JSON-safe dict:

```python
class Screen:
    tile_id = None            # stable id, lowercase-kebab (NEW, §4.1)

    def model(self):
        """The web-mirror model for this screen. JSON-safe dict.
        Base returns the generic fallback (§4.6)."""
```

The emitter is **derived from this method**, never hand-maintained in a second
location. Adding a screen or renaming a field therefore cannot silently desync
the two sides: there is exactly one definition, and the web either knows its
`kind` or falls back (§4.6). A reviewer adding a screen has one place to look.

`model()` MUST be cheap and side-effect free — it reads already-computed screen
state, it does not scan a bus or hit the network. It is called on the render
thread.

### 4.1 `tile_id` — the identity fix

Screens currently have **no stable id**; identity is derived from `title` by two
*incompatible* slug functions (`microkvm/service.py` strips non-alphanumerics →
`microkvm`; `app.py` maps spaces to hyphens → `micro-kvm`).

**v1 adds an explicit `tile_id` class attribute** on `Screen`, and it is the
only identity this protocol uses. Canonical form: lowercase, ASCII, spaces and
punctuation → single `-` (the `KILODASH_OPEN` grammar). The launcher is `home`.

| Screen | `tile_id` |
|---|---|
| LauncherScreen | `home` |
| CanScreen | `can-bus` |
| N2kScreen | `n2k` |
| LightDockScreen | `light-dock` |
| MicroKvmScreen | `micro-kvm` |
| *(all others)* | kebab of `title`, declared explicitly on the class |

Renaming a screen's `title` MUST NOT change its `tile_id`. The id is a wire
identifier with a bookmark's lifetime; the title is a label.

### 4.2 — 4.5 Rich models (v1 scope)

**v1 declares rich models for four screens.** These are the highest-value and
highest-rate surfaces, and they exercise every hard case in the protocol: a tile
grid, a fast table, an alert model, and a session state machine.

Everything else ships on the generic fallback (§4.6) and is promoted to a rich
model one screen at a time. **Promotion is not a version bump** — adding a new
`kind` is backward compatible by §9's unknown-kind rule.

#### 4.2 `home` — the launcher

```json
{
  "kind": "home",
  "tiles": [
    {"id": "can-bus", "title": "CAN Bus", "glyph": "can",
     "available": true, "badge": null},
    {"id": "gps", "title": "GPS", "glyph": "gps",
     "available": false, "badge": null},
    {"id": "light-dock", "title": "Light Dock", "glyph": "lightdock",
     "available": true, "badge": "lit"}
  ]
}
```

`available: false` = hotplug device absent; render dimmed and non-interactive,
matching the panel. `badge` is `null` \| `"lit"` — the device-present square the
launcher already draws. `glyph` names the pictogram key; the web supplies its
own Semiotic-Standard glyph set keyed by that name and MUST render a neutral
placeholder for an unknown key rather than failing.

#### 4.3 `canbus` — the stress case

```json
{
  "kind": "canbus",
  "iface": "can0",
  "bitrate": 250000,
  "state": "up",
  "frame_rate": 812,
  "total": 148223,
  "rows": [
    {"id": "0x0CF00400", "ext": true, "count": 4821, "hz": 100.2,
     "dlc": 8, "data": "FF FF FF 68 13 FF FF FF",
     "name": "EEC1", "alert": false}
  ],
  "truncated": false
}
```

- `rows` is capped at **64 entries**, ordered exactly as the panel orders them.
  `truncated: true` when the cap elided rows — the web MUST surface that, so a
  partial table never reads as a complete one.
- `state`: `up` \| `down` \| `bus-off` \| `error`.
- `data` is the last payload, hex, space-separated, uppercase.
- `name` is the decoded label from the active DBC table, `null` if unmatched.

This is the model that proves the delta discipline. At full bus rate the
underlying counters change every frame; §7's coalescing is what keeps this from
becoming a firehose.

#### 4.4 `n2k` — decoded fields + alerts

```json
{
  "kind": "n2k",
  "iface": "can0",
  "state": "up",
  "sources": [
    {"src": 0, "label": "ENGINE", "last_seen": 0.4}
  ],
  "fields": [
    {"pgn": 127488, "src": 0, "name": "ENGINE RPM",
     "value": 4210, "unit": "rpm", "fmt": "%.0f",
     "armed": true, "alerting": true, "last_seen": 0.2}
  ],
  "truncated": false
}
```

`armed` = a watch is configured on this field (renders caution amber);
`alerting` = it is currently firing (renders `bad`, and has a matching
`AlertFired` in flight). `last_seen` is age in seconds, so the web can grey a
stale row without a clock of its own. `fields` capped at **64**.

#### 4.5 `lightdock` — the session state machine

```json
{
  "kind": "lightdock",
  "link": "docked",
  "device": {"vid": "0x2E8A", "product": "Scottina Light", "fw": "1.0.3"},
  "session": {
    "phase": "pull",
    "detail": "raw004.log",
    "done": 3, "total": 7,
    "bytes": 184320, "bytes_total": 502134
  },
  "log": [
    {"t": 1752871240.0, "level": "info", "text": "CLOCK SET · 3 GPS"}
  ]
}
```

`link`: `absent` \| `detected` \| `docked` \| `error`.
`session.phase`: `idle` \| `hello` \| `clock` \| `push` \| `pull` \| `done` \|
`error`. `log` is the ship-log line list, capped at **32** most-recent entries.

The web renders progress from `done`/`total` and `bytes`/`bytes_total`; it does
**not** compute a rate or an ETA the box hasn't emitted, because the two
surfaces must agree on the number a user reads aloud.

### 4.6 The generic fallback

Every screen without a rich model emits this, automatically, from the `Screen`
base class. **No per-screen work, and no blank tiles in the mirror.**

```json
{
  "kind": "generic",
  "title": "Pi Health",
  "rows": [
    {"label": "CPU", "value": "12%", "state": "ok"},
    {"label": "TEMP", "value": "48.2 C", "state": null},
    {"label": "THROTTLED", "value": "YES", "state": "fault"}
  ],
  "buttons": [
    {"id": "refresh", "label": "REFRESH", "enabled": true}
  ],
  "note": "Rendered from the generic model — this screen has no rich model yet."
}
```

`state`: `null` \| `"ok"` \| `"caution"` \| `"fault"`, carrying the same colour
semantics as everywhere else. `buttons[].id` is what a `button_press` command
(§6) names.

**A consumer MUST render `kind: "generic"`.** It is the compatibility floor: it
is what an old web bundle falls back to when the box gains a screen the bundle
has never heard of (§9).

## 5. Snapshot discipline

**Snapshot-on-connect is mandatory.** The box sends `Hello` then
`ScreenSnapshot` on every accepted socket connection, unprompted. The backend
sends its buffered `ScreenSnapshot` as the **first message on every new
WebSocket connection**, before any delta.

A client renders nothing until it holds a snapshot. Deltas arriving before one
are discarded, not queued — patching from an assumed base state is exactly the
failure this rule exists to prevent.

Resync is always **re-request the snapshot**, never reconcile. The three
triggers:

1. `rev` gap or `seq` gap detected (§3);
2. WebSocket reconnect after a drop;
3. an `Error` frame with `code: "resync"` (§8).

**Resync is coalesced, not per-trigger.** A consumer with a `request_snapshot`
already in flight MUST NOT issue another, and MUST NOT issue more than one per
250 ms. A burst of `rev` gaps under §7 backpressure collapses to a single
pending resync — otherwise the recovery path becomes the load it is recovering
from.

**On resync, buffered web input is never replayed.** The box is truth; the web
re-reads it. An input the user made during a dropped connection is lost, and
that is correct — replaying it would let a stale intent overwrite a state the
box has since moved past.

## 6. Command surface (web → box)

`POST /api/input`, `Content-Type: application/json`, one action per request. One
flat schema; **every action is already reachable on the touchscreen.**

| `action` | Body | Panel equivalent |
|---|---|---|
| `tap_tile` | `{"action":"tap_tile","tile":"can-bus"}` | tapping a launcher tile |
| `button_press` | `{"action":"button_press","button":"refresh"}` | tapping an on-screen button |
| `back` | `{"action":"back"}` | the back hit-box |
| `home` | `{"action":"home"}` | back from a screen |
| `request_snapshot` | `{"action":"request_snapshot"}` | *(no panel equivalent — pure resync)* |

**Scrolling is not a mirrored command.** The web receives each screen's full row
set (up to the §4 caps) and scrolls it locally; the panel scrolls the same set
within its smaller viewport. Neither drives the other — a shared scroll offset
would create exactly the two-surface divergence this design rules out, and buys
nothing, since each surface already holds the whole list. If a screen ever needs
a **box-side selection cursor** (a highlighted row that is real box state, not a
viewport position), that is a `selected` model field plus a `select` action,
introduced with that screen — never a viewport `scroll`.

Responses:

| Status | Meaning |
|---|---|
| `202 Accepted` | queued for the UI thread. **Not** "it happened" — see below |
| `400 Bad Request` | malformed body, or `action` not in the table |
| `409 Conflict` | well-formed but not valid *now* (tile unavailable, button not on the active screen) |
| `503 Service Unavailable` | the box socket is not connected |

**`202` means accepted, not applied.** Commands are asynchronous by
construction: the backend hands the action to the box, the box applies it on the
UI thread, and the *result* arrives as a normal `TileChanged` / `DataUpdated`
event like any other state change. **The web MUST NOT optimistically apply its
own command to local state.** It issues the command and waits to be told what
became true. This is the source-of-truth invariant expressed as a rule about
rendering: there is exactly one path by which the web's display changes, and it
is the event stream.

**Commands are validated against the active screen.** A `button_press` for a
button not currently present is `409`, not a no-op — the box does not
synthesise input for a screen that is not showing.

### Injection point (normative)

The box injects commands into the **same path as the touch driver**:
`App._dispatch_tap()` and the existing `open_screen()` / `go_home()` entry
points, serviced on the UI thread via a thread-safe pending-action queue drained
in `App._loop()` — the mechanism `microkvm`'s pending tile switch already uses.

A command arriving off-thread MUST NOT call into a screen directly. The device
cannot tell a web tap from a panel tap, and that identity is the whole design.

### Input contention: last-input-wins

State is singular, so **last-input-wins is correct and intended.** There is no
lock, no lease, no "who's driving" arbitration.

A web view following the box **will** jump when someone touches the panel. That
is the truth surfacing, not a bug, and it is not to be engineered around —
arbitration would reintroduce exactly the two-states problem this design rules
out.

## 7. Best-effort emission (the box is never slowed)

**The touchscreen wins, always.** Every rule here exists to guarantee the web
path cannot degrade the panel.

- The emit is **off the hot path**: `model()` is called only when the screen has
  already reported a change, and serialisation + write happen after the frame is
  blitted, never before.
- The socket is **non-blocking**. `EAGAIN` / `EWOULDBLOCK` / `EPIPE` on write →
  **drop the frame and move on.** Never retry in the loop, never block, never
  buffer unboundedly.
- The producer holds **at most one pending frame per type**. A newer
  `DataUpdated` supersedes an unsent older one; because `rev` was already
  assigned to the superseded frame at change time (§4), the delivered stream
  carries a `rev` gap — never a silent overwrite — and that gap is what tells
  the consumer to resync.
- **A slow or absent subscriber cannot back-pressure the device.** If nothing is
  listening the emitter is a few wasted comparisons per tick, and if the
  subscriber stalls it is dropped frames — in both cases the panel's render
  budget is untouched.
- **Emit on change, not on tick.** `tick()` returning `True` is *not* a change
  signal — several screens return `True` for animation frames only. The emitter
  compares a model signature (the `tables.py` `_sig` pattern) and emits only on
  an actual difference.
- **Coalescing floor: 100 ms per screen.** A screen changing faster than 10 Hz
  (CAN at bus rate, Signal K at its 0.05 s tick) has its emissions coalesced to
  one frame per 100 ms carrying the merged delta. A human reading a phone does
  not perceive faster, and it caps socket load at a bounded rate regardless of
  bus traffic.

**The inbound path is bounded too.** `request_snapshot` (§6) is the one command
that makes the box do real work — a full model built on the UI thread — so it is
the one way the *command* channel could defeat best-effort emission. The box
services at most one snapshot per 250 ms per subscriber; excess requests fold
into the next scheduled build rather than queueing. With the consumer coalescing
rule (§5), a resync storm cannot form.

If serving events ever competes with the panel's render budget, the emitter is
what gets cut. Verify this on the CAN bus at full rate — that is the case that
would break it.

## 8. `Error` frames and rejection

```json
{"v": 1, "type": "Error", "seq": 91, "t": 1752871300.0,
 "code": "resync", "detail": "model signature reset"}
```

| `code` | Meaning | Consumer action |
|---|---|---|
| `version_mismatch` | peer `v` is not `1` | log loudly, disconnect, do not degrade (§9) |
| `busy` | a subscriber is already connected (§1) | do not retry-storm; back off ≥5 s |
| `bad_command` | malformed or disallowed action | surface to the user; do not retry |
| `resync` | producer state is discontinuous | discard model, `request_snapshot` |

## 9. Versioning

The protocol version is the integer `v` in every frame, and it is `1`.

**Mismatch is loud and fatal, never a guess.** A peer receiving a frame whose
`v` it does not implement MUST log the mismatch, emit/expect `version_mismatch`
(§8), and refuse the connection. It MUST NOT attempt partial parsing: a frame
shape it does not know is a frame it cannot safely act on, and silently
degrading is how a mirror starts lying about a diagnostic tool's state.

**The one permanent compatibility promise:** `Hello`, `ScreenSnapshot`, and the
`generic` model (§4.6) keep their v1 shape forever. Any future version may add
fields to them but never removes or retypes one. This guarantees a v1 web bundle
can always connect, identify the box, and render *something* truthful.

Two changes that are explicitly **not** version bumps:

- **Adding a new `model.kind`.** Consumers MUST render an unknown `kind` as if
  it were `generic` if the payload carries `rows`, and otherwise show a
  "no renderer" placeholder naming the `kind`. This is what makes §4's
  screen-by-screen promotion a routine commit.
- **Adding a field to an existing model.** Consumers MUST ignore unknown fields.

Changes that **are** a version bump: removing or retyping any field, changing
`changed`'s merge semantics, altering `seq`/`rev` meaning, or adding an
`action` to §6.

## 10. Security posture

**LAN-only. This is load-bearing, not a default.**

- The backend binds port 80 on the LAN interface selected by the existing
  `net.py::advertise_addr()` helper (eth0-if-up-else-wlan0). That selection
  logic is **not** duplicated here. Served at `scottina.local` (mDNS) and the
  raw IP.
- **No auth in v1**, and that is acceptable *only because it never leaves the
  local network* — the same posture Node-RED and Signal K already run under
  on-box.
- **WAN exposure is out of scope and is a different project.** Auth, TLS, and a
  real look at the exposure surface would all come first. A port-forward is not
  an upgrade path; it is the failure mode this paragraph exists to name.
- The backend inherits the untrusted-input discipline already codified in
  `tableconv.py`: validate against the closed §6 allow-list, `list[str]` argv
  only, never `shell=True`, and never construct a filesystem path from client
  input.
- The command surface constructs **no bus frames**, by §0's scope constraint.
  That is the security property that matters most here: a hostile actor on the
  LAN can navigate the diagnostics UI and can not transmit on the vehicle bus,
  because no code path exists that would let them.

## 11. Conformance vectors

`web-vectors.json` — committed next to this file, as `dock-vectors.json` is —
holds example frames for every `type` in §3, every `kind` in §4, and every
`action` in §6, each with its expected parse result or rejection.

Both sides unit-test against it. The React bundle can be developed entirely
against these vectors with no Pi attached — which is the point: it makes the
front-end buildable before the emitter exists.

**A change to this document that does not update the vectors is incomplete.**

## Deliberately deferred

Considered and rejected for v1. Revisiting any of these is a new version, not a
bolt-on.

- **Multiple simultaneous subscribers on the box socket.** The backend fans out;
  the box does not. One subscriber keeps the producer's drop logic trivial.
- **Deep-merge / JSON Patch deltas.** Shallow top-level merge with whole arrays
  is cheaper to compute and *far* cheaper to verify. Revisit only if a real
  measured socket load justifies it.
- **Command acknowledgement / correlation ids.** `202` plus "wait for the event"
  is the source-of-truth invariant in action. An ack would tempt optimistic
  local rendering, which is the thing that creates two states.
- **Web-initiated screen state the panel cannot express.** That is Tier 2, it is
  additive-only, and it does not touch this contract.
- **`field_set` and a settable-field surface.** No v1 screen exposes an on-screen
  editor, so no `field_set` action can satisfy §6's panel-equivalence rule (every
  action must already be reachable by a finger on the panel). Rather than ship an
  action with no legal success path — or design a speculative settable-field
  declaration ahead of any consumer — `field_set` is out of v1. It returns in the
  version that ships the first real editor, introduced **with** that screen's
  `fields` declaration: one declaration per settable field (type, domain, current
  value), two readers — the box validates against it, the web renders the control
  (spinner / dropdown / toggle) from it, no guessing on either side. That is the
  §4 anti-drift shape, designed against a concrete editor rather than in advance.
  Adding the action is a §9 version bump, which is correct: expanding the command
  surface is a safety-relevant act and belongs behind a ratification gate. CAN
  bitrate is the likely first candidate.
- **Auth, TLS, WAN.** §10.

## Open decisions (ledger)

- [ ] **Ratify.** Nothing is written against this yet; freeze before Phase 1.
- [ ] `tile_id` must be added to all 22 screens (mechanical, §4.1) and the two
      existing slug functions retired in favour of it.
- [ ] Confirm the 100 ms coalescing floor (§7) against a live CAN bus at full
      rate — it is a reasoned starting number, not a measured one.
- [ ] Confirm 64 KiB max frame and the 64-row caps hold for the worst real
      NMEA2K source count on the bench.
