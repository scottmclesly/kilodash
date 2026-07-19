# WEB-UI-DESIGN.md — Scottina Web Mirror, Presentation Spec (Phase 3)

**Status:** v0.1 — DRAFT, for the web bundle (Phase 3). Presentation only.
**Couples nothing.** This document has no wire authority. `WEB-PROTOCOL.md` is
the sole data contract; if this spec and the protocol ever disagree about a
field, the protocol wins and this is the bug. Every value rendered here arrives
in a frame defined there.
**Companion:** `web-ui-reference.html` — a static, fake-data mock of this idiom.
It is the *look*, not the build: the real bundle renders live SSE frames. Treat
the mock as the visual target and this doc as the rules. (The mock loads a
webfont from a CDN for convenience; the shipped bundle must not — see §3.)

---

## 0. Scope — presentation only, and the discipline that implies

The mirror **renders the model and adds nothing.** The same restraint that
removed `scroll` and `field_set` from the wire applies to the pixels:

- **Compute no number the box didn't emit.** `lightdock` progress renders from
  `done/total` and `bytes/bytes_total` verbatim — no ETA, no throughput rate.
  Both surfaces must agree on the figure a user reads aloud (§4.5).
- **Render no control that needs data the model doesn't carry.** A segmented
  gauge with threshold ticks would be the natural Alien flourish for an `n2k`
  field — but the model carries `value`, `armed`, `alerting`, not the min/max
  bounds those ticks need. So a field is a numeric readout with a state colour,
  **not** a bounded gauge, until the model carries bounds. Same lesson, again:
  don't draw what the box doesn't send.
- **No optimistic apply.** The display changes only when a frame says so (§6).
  A tap gives *input* feedback (a press state), never *state* feedback.
- **Scroll is local.** The web holds the whole capped row set and scrolls it
  itself; it never asks the box to scroll (§6).

## 1. The idiom — MU/TH/UR, not "dark terminal"

The reference points are the Nostromo's working screens: Ron Cobb's **Semiotic
Standard** signage, the MU/TH/UR readouts, segmented industrial meters, corner
registration brackets, spaced-caps monospace on green phosphor. The trap to
avoid is the generic near-black-plus-acid-green hacker terminal, which shares
the palette and none of the character. The difference is **industrial signage
discipline**: everything is a labelled instrument panel with part-number-style
callouts, hard rectangular strips, and hazard/utility framing — a ship's system
readout, not a shell prompt.

Original work in the idiom. We do **not** reproduce the film's actual glyphs or
type; we build our own Semiotic-Standard-*style* set (§8) keyed by the `glyph`
names the protocol sends.

Spend the boldness on the **frame and the phosphor-write** (§4, §5). Everything
else stays quiet.

## 2. Theme & colour — from `Hello`, normative

The palette is **not** chosen here; it arrives in `Hello.theme` (§3 of the
protocol) as nine tokens and the web derives everything from them as CSS custom
properties, re-themed live on a mid-stream `Hello`. Never hardcode a phosphor
colour.

| Token | Role in the UI |
|---|---|
| `bg` | panel ground; the near-black behind everything |
| `card` / `card_hi` | strip fills; `card_hi` is the raised/active strip |
| `fg` | all primary text and chrome — the phosphor |
| `muted` | secondary text, inactive/dimmed rows, unavailable tiles |
| `accent` | the phosphor-write flash (§5); brief, never resting |
| `ok` | active-good values |
| `warn` | **amber**: armed watches, standby, resting/holding states |
| `bad` | **red: actual faults only** — a firing alert, `bus-off`, `link:error` |

**Red is precious and load-bearing.** It appears only where the box has declared
a fault. An armed-but-unfired watch is `warn`, never `bad`. Chrome is always
monochrome in `fg`; colour is reserved for state. If a screen is showing red,
something is actually wrong — that is the whole point of the discipline, and the
across-the-room read depends on it.

## 3. Typography

One utility face, used everywhere, uppercase, tracked. This is a data
instrument; there is no "body" copy to speak of.

- **Face:** a squared, industrial monospace. **Self-hosted or system only — the
  shipped bundle must make no network request at runtime.** Scottina is a
  diagnostics box that works off-grid; a webfont CDN is a hard dependency on
  the one thing that may not be there. The reference mock's Google Fonts link
  is a mock convenience and must not survive into the bundle.
- **Case & tracking:** labels and chrome are `text-transform: uppercase` with
  positive letter-spacing (~0.08–0.14em on labels). Data values (hex, numbers)
  are **not** letter-spaced — they need to align in columns.
- **Scale:** tight and few. A label size, a value size, a large readout size for
  the one hero number per screen (e.g. `frame_rate`, engine RPM). Weight does
  the work, not size sprawl.
- **Alignment:** numeric columns are right-aligned and tabular
  (`font-variant-numeric: tabular-nums`). Hex byte strings are fixed-width and
  space-grouped.

## 4. The instrument frame

Persistent chrome around every screen — this is the signature, and it's what
makes a `generic` screen still read as an instrument rather than a fallback.

- **Header strip** (`card_hi`): device id (`scottina-prime`), `kilodash`
  version, the active theme name as a small callout, and a **link lozenge** at
  the right — `LINK` (steady `ok`) / `HOLDING` (pulsing `warn`, §6). A blinking
  `fg` cursor block sits somewhere in the header as a liveness tell.
- **Nav band**: the breadcrumb from `nav` — `HOME` on the launcher,
  `HOME ▸ CAN-BUS` on a screen — with a hard-edged **BACK** hit-box that issues
  the `back` command. `nav` is at most two deep (§3); never render a third crumb.
- **Corner registration brackets**: L-shaped `fg` marks at the four corners of
  the content panel (⌐ ¬ and their mirrors). Thin, precise, slightly inset.
  These are the single most recognisable Alien-panel cue; get them crisp.
- **Footer strip**: a status line — a `seq`/`rev` readout (tiny, right), and a
  hazard-striped band that only appears to carry a transient rejection notice
  (§9). Otherwise the footer is quiet.
- **CRT treatment**: subtle horizontal scanlines and a faint phosphor bloom on
  text (`text-shadow` in `fg` at low alpha). A gentle vignette. **No barrel
  distortion** — legibility across a room beats fisheye novelty. All CRT effects
  sit behind a `prefers-reduced-motion` / intensity guard (§11).

## 5. Motion — the phosphor-write

Motion is deliberate and mechanical. The box updates in discrete steps; so do
we. There is no smooth tweening of values.

- **Delta = a write flash.** When a `DataUpdated` changes a cell, that cell
  flashes to `accent` (or `card_hi` ground) for ~60ms then decays back to its
  resting colour over ~350–500ms — phosphor persistence. This *is* the delta
  made visible, and it's the core reward of the whole event design: you see
  exactly what changed. In `canbus`, flash **per changed byte**, not per row —
  the byte-level change is the RE signal.
- **Alert fire:** the row/badge snaps to `bad` and flashes, then holds `bad`
  while `alerting`, and fades on `AlertCleared` on the `t_fired` decay schedule
  the box sends. Non-modal, always — badge + row flash, never a dialog (§3).
- **Tile change:** a fast scanline sweep or one-frame flicker as the new screen
  "boots in." Keep it sub-200ms; responsiveness is the point of the mirror.
- **Cursor:** one blinking block, header or active readout, ~1Hz. Liveness.

Restraint: the flash and the alert are the motion budget. Resist ambient
animation elsewhere — drifting scanlines everywhere reads as AI-generated set
dressing, not an instrument.

## 6. The stale-link state — the signature behaviour

When the SSE stream drops, the **entire UI** goes muted, not modal:

- Root filter: desaturate + drop contrast + slight dim — roughly
  `saturate(0.35) contrast(0.8) brightness(0.9)` — with a slow ~2s breathing
  pulse on brightness. Content stays fully readable; it just reads as *frozen*.
- The header lozenge switches to **`HOLDING`** in pulsing `warn`.
- Nothing is hidden and nothing is cleared — the last known state stays on
  screen, desaturated. Across the room this reads instantly as "still alive,
  link down," never as a crash, reboot, or blank.
- On reconnect → snapshot → full colour restores. The pulse stops. (Per §5 of
  the protocol, reconnect always re-requests the snapshot; the web never patches
  a stale base.)

This is distinct from any box-side idle: `HOLDING` is a *link* state the web
owns, not a box state. Keep the two vocabularies separate.

## 7. Screen renderers

`model.kind` selects the renderer. Each is a panel inside the frame (§4).

### 7.1 `home` — the launcher
A grid of tiles. Each tile: the Semiotic glyph (§8) large and centred, the
`title` in spaced caps beneath, framed as a bordered cell. `available:false` →
rendered in `muted`, non-interactive, unmistakably dimmed (hotplug-absent), not
hidden. `badge:"lit"` → a small `ok` square in the corner (device present). Tap
issues `tap_tile`. Unknown `glyph` → neutral placeholder glyph, never broken.

### 7.2 `canbus` — the stress case
The dense table, up to 64 rows, **ordered exactly as the model sends them**
(never re-sorted client-side). Columns: `id` (hex, ext-flagged), `count`, `hz`,
`dlc`, `data` (space-grouped hex, uppercase), `name` (decoded label or blank).
- **Per-byte write flash** on change (§5) — the headline interaction.
- A **segmented bar meter** for `frame_rate` in the header region of the panel —
  discrete lit blocks, industrial. This gauge is legitimate: `frame_rate` is a
  single emitted scalar with a sensible visual ceiling, no missing bounds.
- `state` strip: `up` (`ok`) / `down` (`muted`) / `bus-off` / `error` (both
  `bad`). `truncated:true` → a hard `warn` band: `TABLE TRUNCATED · 64 OF N` so
  a partial never reads as whole.

### 7.3 `n2k` — decoded fields
A `sources` strip (src, label, `last_seen` age) and a `fields` list: `pgn`,
`name`, `value`+`unit` formatted per `fmt`, age. `armed` → an amber watch marker
on the row; `alerting` → row goes `bad` and flashes with its matching
`AlertFired`. Stale rows grey toward `muted` in proportion to `last_seen` — the
box gives the age so the web needs no clock. **Numeric readouts, not bounded
gauges** (§0). `truncated:true` handled as in `canbus`.

### 7.4 `lightdock` — the session machine
Honour the box's own dock idiom (converging silhouettes = syncing, complete,
interrupted) but drive it entirely from fields: `link`
(`absent`/`detected`/`docked`/`error` — `error` is `bad`), `session.phase`
(`idle`→`hello`→`clock`→`push`→`pull`→`done`/`error`) as a **stepped stage
readout** with the reached stages lit. Progress bar from `done/total` and
`bytes/bytes_total` **verbatim — no rate, no ETA** (§0). The `log` (capped 32)
renders as a classic ship-log terminal scroll: newest at bottom, mono,
timestamped, level-coloured (`info`=`fg`, `warn`=amber, `error`=`bad`).

### 7.5 `generic` — the compatibility floor
Must render, and must still look like a standard-issue panel, not a fallback.
`title` as the panel header; `rows` as a labelled band list (label left, `value`
right-aligned, `state` colouring the value: `ok`/`caution`(amber)/`fault`(red));
`buttons` as hard-edged labelled controls (disabled → `muted`), tap issues
`button_press`. The `note` renders small and `muted` — honest that this screen
has no rich model yet, without looking broken.

## 8. The Semiotic-Standard glyph set — construction rules

Don't hand-draw 22 unrelated icons; define the system and generate consistently.
- **Grid & weight:** each glyph on a common square (e.g. 64u), bold uniform
  stroke (~6u), geometric — circles, bars, chevrons, hazard diagonals. High
  contrast, single-colour `fg`, no gradients, no fine detail (must read at tile
  size and across a room).
- **Framing:** each glyph optionally sits in corner brackets echoing the panel
  brackets (§4), so the set feels issued-as-standard.
- **Vocabulary examples:** `can` → a bus/node bracket motif; `gps` → a fix
  crosshair/satellite arc; `lightdock` → two chevrons meeting a bar (dock);
  `sdr`/`lora` → concentric emission arcs; a generic `std` mark for the unknown
  fallback. Build the rest to these rules.
- **Delivery:** inline SVG, keyed by `glyph` name; unknown key → the `std`
  placeholder, never a missing-image gap.

## 9. Command feedback — the no-ack reality

There is no `409` and no correlation id (§6, deferred acks). So:
- **Tap gives input feedback only:** a press-down state on the control, local
  and immediate. The screen does **not** change on tap; it changes when the
  resulting `TileChanged`/`DataUpdated` arrives. Round-trip is loopback-fast, so
  this feels instant without any optimistic apply.
- **No pending spinners.** There is no ack to wait for, so a spinner would spin
  forever on a rejected command. Don't build one.
- **A bounced command** surfaces as an async `Error{bad_command}` the client
  can't tie to a specific POST. The honest treatment: a brief, transient
  rejection flash in the footer hazard band (§4) — `INPUT REJECTED` — that
  decays on its own. The truthful message is "the box didn't move," not "your
  action failed at 14:32:07 on tile X."

## 10. Layout & responsive

One centred instrument frame, working **portrait (phone)** and **landscape
(laptop)**. Header/nav/footer strips are fixed; the content panel scrolls
locally (§0). On a phone it's a tall single-column instrument; on a laptop it's
a centred console with margin to spare — leave the frame able to grow a right-
hand rail later without reflow, because **Tier 2 enrichment lands there** and
shouldn't force a redesign. Tier 2 is out of scope for this build; just don't
paint yourself out of it.

## 11. Quality floor

- `prefers-reduced-motion`: kill the phosphor pulse, the write-flash decay
  (snap instead), the cursor blink, and the CRT scanline motion. State stays
  legible; only motion goes.
- Visible keyboard focus on every control (tiles, buttons, BACK) in `accent`.
- Legibility is the hard constraint: this must be readable across a garage from
  a phone under a hood light. CRT effects never win over contrast — if bloom or
  scanlines fight the text, dial them down.
- Touch targets sized for a phone; the mirror is used on glass, not just a
  trackpad.

## Deliberately not built (presentation discipline)

- Bounded/threshold gauges for `n2k` fields — model carries no bounds (§0).
- Any computed rate/ETA on `lightdock` — box emits the figures (§0).
- Optimistic state, pending spinners, per-action acks — no ack exists (§9).
- Mirrored scroll — local only (§0).
- Reproductions of the film's actual glyphs or type — original idiom only (§1).
- Any runtime network fetch, webfonts included — off-grid is the design case (§3).
