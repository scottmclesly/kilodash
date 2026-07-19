# Scottina Web UI (Mirror + Enrich) — ToDo (for Claude Code)

**Goal:** serve a LAN-reachable web front-end from Scottina Prime (kilodash) on
port 80 that **mirrors the live touchscreen state** — same tiles, same active
screen, same controls, one shared state — with symmetric read/write input from
either surface. Two use cases drive it: a **big-screen bench convenience**
(hit `scottina.local`, work from a laptop without unplugging the Pi) and a
**remote-monitor/trigger surface** (stash Scottina under the hood, watch and
poke it from a phone on the LAN). Web renders a *structured model* the Pi
emits — never a framebuffer screencap.

**Scope constraint (hard):** diagnostics only. The CAN TX/RX exception is
unchanged and lives entirely in the link layer (heartbeat/reply only, never
user-expressible) — the web UI adds **no** new TX surface. It commands the same
input state machine the touch panel does and nothing more; any action the web
can trigger must already be reachable from the touchscreen (Tier 1) or be a
config/convenience surface that constructs no bus frames (Tier 2).

**Source-of-truth invariant:** the **Pi is the single source of truth.** The
web is a subscriber. On any divergence, the web **resyncs to the box's state** —
it never asserts its own. One device, one state: a tile change from the web
*becomes* the tile on the local screen, and vice versa. No split personalities,
no independent web navigation session.

**Two tiers:**
- **Tier 1 (this doc, MVP):** faithful *functional* mirror. Not pixel-for-pixel
  — the web uses a proper design system (React/HTML/CSS control surfaces) driven
  by the same variables kilodash generates. Same tiles, same nav, same live
  data, symmetric input.
- **Tier 2 (future, grows organically):** web-only enrichment — configurable
  Node-RED shortcut buttons, quick-tweak config panels, log downloads, anything
  out of scope for the 3.5″ but useful with real screen real estate. The
  touchscreen never shows these. Built incrementally as real use reveals what's
  worth reaching for. **Not specced here** — Tier 1 proves the architecture
  first.

---

## The Web Protocol contract (the coupling point — spec this first)

Same discipline as `DOCK-PROTOCOL.md` / `PROTOCOL.md`: the event schema is the
*only* coupling between kilodash and the web backend. Spec it fully and freeze
it before writing either producer or consumer, so a schema change is a
deliberate versioned act, not an accidental drift.

> **Status 2026-07-18:** `To-DoLists/WEB-PROTOCOL.md` v1.0 **drafted** — the
> full contract below is spec'd and awaiting ratification. Decisions taken
> while writing it (all recorded in the doc, don't re-litigate here):
> - **v1 rich models = 4 screens only** (`home`, `can-bus`, `n2k`,
>   `light-dock`); every other screen ships on the auto-derived `generic`
>   fallback so nothing is blank. Promoting a screen to a rich model is
>   explicitly **not** a version bump — that's the v1.5 lane, after the design
>   system is massaged.
> - **New `Screen.tile_id`** class attribute is the only wire identity; retires
>   the two conflicting slug functions (`microkvm/service.py` vs `app.py`).
> - **`Hello` carries the theme palette** so the web inherits the box's CRT skin
>   and the Alien / Semiotic-Standard colour semantics (red = faults only).
> - Delta = **shallow top-level merge, arrays whole**; `rev` counter is the gap
>   detector; **100 ms coalescing floor** per screen (unverified number — bench
>   it against CAN at full rate).
> - **§6 is five actions**: `tap_tile`, `button_press`, `back`, `home`,
>   `request_snapshot`. Both `scroll` and `field_set` were cut in the
>   pre-ratification pass — `scroll` because a viewport offset is not mirrored
>   box state, `field_set` because no v1 screen has an editor behind it.

**When the first settable-field editor lands (likely CAN bitrate):**
`field_set` returns to §6 of `WEB-PROTOCOL.md` as a §9 version bump, introduced
*together* with that screen's `fields` model block — one declaration per
settable field (type, domain, current value), read by both the box-validator
and the web-renderer, no guessing on either side. That change also needs its
own `web-vectors.json` additions for the value-rejection cases pulled from the
v1 set: wrong JSON category → `400`, out-of-domain → `409`, field-not-settable
→ `409`. The design is already written down in `WEB-PROTOCOL.md`'s deferred
ledger — don't rediscover it.

- [x] Write `WEB-PROTOCOL.md` in the kilodash repo defining the **event stream
      (kilodash → web)** and the **command surface (web → kilodash)** as two
      distinct, deliberately asymmetric channels:
      - **Events out** — kilodash publishes; web subscribes and relays. Define
        exact JSON payloads for at minimum:
        - [ ] `TileChanged` — active tile id + navigation stack;
        - [ ] `DataUpdated` — screen-scoped field deltas (the variables the
              active screen exposes: CAN seen-IDs rows, NMEA2K decoded fields,
              Light Dock session state, etc.);
        - [ ] `AlertFired` / `AlertCleared` — badge state, non-modal, matching
              the on-box alert model;
        - [ ] `ScreenSnapshot` — full current-screen model, sent on
              client connect and on resync (see stale-connection handling);
        - [ ] a protocol `version` field + reject-on-mismatch rule.
      - **Commands in** — web POSTs; kilodash treats identically to a touch
        event. Define the `/api/input` body shape:
        - [ ] `{"action": "tap_tile", "tile": "..."}`,
              `{"action": "button_press", "button": "..."}`,
              `{"action": "field_set", "field": "...", "value": ...}`, `back`,
              `home`, etc. — one flat schema, every action already reachable
              on-box.
- [ ] **Rationale to codify in the doc:** events out are the narrative (Pi is
      master, drives what's true); commands in are slow, discrete,
      user-paced — REST fits them. Do **not** make the command channel a
      back-channel over the event socket. Keep the asymmetry.
- [ ] Each kilodash `Screen` declares the model it emits (the `DataUpdated`
      field set) in one place, so adding a screen can't silently desync the web
      — the emitter is derived from the screen's own declared model, not
      hand-maintained in a second location.

## Phase 1 — kilodash event emitter (Unix socket)

Keep it a pocket monster: no Redis, no broker. Local Unix socket + JSON frames.

- [ ] Event emitter in kilodash's main loop publishing `WEB-PROTOCOL.md`
      events as newline-framed JSON to a Unix domain socket
      (`/tmp/kilodash-events.sock`, or `/run/kilodash/events.sock` with correct
      ownership).
- [ ] Emit on the events that matter: tile change, active-screen data tick,
      alert fire/clear. Tie the data-tick emission to the screen's existing
      `tick_interval` / dirty-rect model (KioskSpeedImprovementToDo) — **emit
      only on actual change**, not every frame, so the socket carries deltas
      not a firehose.
- [ ] Emitter must be **non-blocking and best-effort**: if no web backend is
      listening, or the socket buffer is full, kilodash **drops the event and
      moves on** — the touchscreen is never stalled or slowed by the web path.
      A slow/absent subscriber cannot back-pressure the device.
- [ ] Command sink: an input adapter that accepts `/api/input`-shaped actions
      (from the web backend, Phase 2) and injects them into the **same input
      queue as the touch driver** — identical code path, identical state
      machine. The device cannot tell a web tap from a panel tap.
- [ ] **Input contention:** state is singular, so **last-input-wins** is
      correct and intended. No lock. Web view following the box may jump when
      the local panel changes tiles — that's the truth surfacing, not a bug.
      Document it; don't engineer around it.

## Phase 2 — web backend (Python service on the Pi)

On-device, no cloud, no WAN leakage. Reachable on the LAN only.

- [ ] Small Python service (Flask/FastAPI — match the stack; the Tables
      converter app already picks one, reuse it) that:
      - [ ] connects to `/tmp/kilodash-events.sock`, deserializes events,
            **buffers the latest full screen model** in memory;
      - [ ] broadcasts events to connected browsers over **WebSocket** (this is
            the live channel — sub-second, event-driven, no polling);
      - [ ] on a new WS connection, sends the buffered `ScreenSnapshot` first so
            a fresh client is immediately in sync, then streams deltas;
      - [ ] exposes **REST `/api/input`**, validates against the command schema,
            forwards to kilodash's command sink (Phase 1).
- [ ] Bind on port 80. **Address advertising:** reuse the shared
      `net.py::advertise_addr()` helper from the Tables/CanTick work
      (eth0-if-up-else-wlan0) — do not duplicate the selection logic. Serve at
      `scottina.local` (mDNS) and the raw IP.
- [ ] Serve the built React bundle (Phase 3) as static assets from the same
      service — one process, one port, simple deploy.
- [ ] LAN-only posture: bind to the LAN interface, no auth for v1 (trusted
      local network, same model as Node-RED / Signal K on-box). Note in the doc
      that WAN exposure is explicitly **out of scope** and would need auth +
      TLS + a rethink before it's ever considered.

## Phase 3 — React front-end (the mirror)

Renders the emitted model as native web control surfaces. **No screencap, ever.**

- [ ] React app that consumes the WebSocket stream, holds the current screen
      model in state, and renders:
      - [ ] the **tile grid / home**, with tap → `tap_tile` command;
      - [ ] the **active screen** rebuilt from its `DataUpdated` model —
            CAN seen-IDs table, NMEA2K decode rows, Light Dock session view,
            etc. — using a shared component set, not per-screen bespoke markup
            where avoidable;
      - [ ] **nav controls** (back/home) wired to commands;
      - [ ] **alerts** as non-modal badges/flashes matching the on-box
            presentation.
- [ ] **Stale-connection state (the signature behavior):** when the WebSocket
      drops, the *entire UI* goes **desaturated + reduced contrast + slow
      pulse** — content stays visible and readable, but unmistakably "this is
      frozen, not live." The pulse says *the app didn't crash, didn't reboot,
      you're still in it — the link is just down.* On reconnect: request a fresh
      `ScreenSnapshot`, resync to the box, restore full color. (CSS filter over
      the root — `saturate()` + `contrast()` + a keyframed opacity/brightness
      pulse — so it needs no per-component work.)
- [ ] Reconnect logic: auto-retry with backoff, resync-from-snapshot on
      success (never replay buffered web inputs — the box is truth, just
      re-read it).
- [ ] Keep it lightweight: this rides on a Pi 5 serving a phone/laptop, but the
      point is the 3.5″ and its framebuffer aren't in the loop at all.

## Phase 4 — install + docs

- [ ] Installer under `setup/`: service deps, systemd unit for the web backend
      (enabled at boot, restarts on failure), socket path + ownership,
      React bundle build/copy — idempotent.
- [ ] README: new "Web UI" section — access via `scottina.local`, the
      mirror/one-state model, LAN-only posture, and the explicit note that the
      web adds **no TX surface** (scope constraint restated).
- [ ] `WEB-PROTOCOL.md` linked from the repo docs alongside DOCK-PROTOCOL /
      PROTOCOL, flagged as the frozen contract between kilodash and the web
      service.

---

## Known gotchas

- **The web path must never slow the box.** The emitter is best-effort and
  drop-on-full. If serving events ever competes with the touchscreen's own
  render budget, the touchscreen wins — verify the emit is off the hot path and
  the socket write can't block the main loop.
- **Emit deltas, not a firehose.** Tie emission to actual state change /
  dirty-rect, not to every tick. A CAN screen at full bus rate will otherwise
  saturate the socket and the WebSocket with redundant frames.
- **Schema drift is the silent killer.** kilodash and the web backend agree
  *only* via `WEB-PROTOCOL.md`. Derive the emitter from each screen's declared
  model so a new screen or renamed field can't desync the two sides unnoticed.
  Version the protocol; reject on mismatch loudly.
- **Snapshot-on-connect is mandatory.** Without a full `ScreenSnapshot` on
  every WS (re)connect, a client that joins mid-stream — or reconnects after a
  drop — renders a partial or stale model. Resync = re-request snapshot, never
  patch from assumptions.
- **Last-input-wins is a feature.** With one shared state, a web view *will*
  jump when someone touches the panel. Don't add locks or "who's driving"
  arbitration — that reintroduces the two-states problem you explicitly ruled
  out.
- **LAN-only means LAN-only.** No auth in v1 is fine *because* it never leaves
  the local network. The moment anyone wants remote/WAN access, this is a
  different project (auth, TLS, exposure surface) — don't let it creep in
  through a port-forward without that work.

## Suggested first slice

`WEB-PROTOCOL.md` first — headless, reviewable, and both sides depend on it.
Then the Phase 1 emitter + a throwaway socket-reader script to eyeball real
event frames from a live tile (CAN on the CanTick bus you already have up is a
good stress test for the delta/firehose behavior). Only once the event stream
is proven do the web backend and React mirror come up — backend first (verify
snapshot + delta + `/api/input` round-trip with `curl` and `wscat`), then the
React surface last, since it's the layer that depends on everything under it.
Tier 2 stays out entirely until Tier 1 is living on the bench.
