# DiveSync-To-Do.md — Autonomous Dive Data Offload

Autonomous sync of logged dives to a base station (local or cloud) + deep sleep on
surface idle. Fieldwerx water-quality logger, v1.x feature (post-v1.0).

## Overview

Users select a **data offload destination** at mission setup:

- **None** — manual offload only (default; no WiFi sync, no auto-sleep behavior change).
  Required option for kits without a base Pi or usable network on site.
- **Local Base Station** — logger auto-syncs to a remembered local AP (Raspberry Pi
  or similar sit-and-forget server running its own AP).
- **Cloud/Online** — logger auto-syncs to a remembered internet WiFi + cloud endpoint.

Once synced, the logger enters **deep sleep** (WiFi off, sensors sleep, backlight off,
ESP light sleep) after an idle timeout, and wakes on button press or timer. This is
aimed squarely at the "diver forgets to hit anything, walks away" case.

## Hard constraint

**DiveSync state machine fires ONLY when:**

- Dive is complete — `!g_logging && !g_submerged` (surfaced, not actively recording)
- A base AP is in range and reachable (WiFi scan succeeds)
- User selected **Local** or **Cloud** (not **None**)

**The dive loop itself is untouched.** No WiFi polling, no scanning, no sleep timers
during active logging. This is a surface-only state, layered on top of the existing
five-sub-machine RUN mode without touching the sample/logging path. Matches the
existing rule that the portal is torn down once `g_logging` starts — DiveSync only
ever activates in the window *after* that teardown.

---

## Phase 1: Portal UI (SETTINGS card)

**New card: "Data Offload"**

- Dropdown: `None | Local Base Station | Cloud/Online`
- **None**: no further fields. This is the default for existing/upgraded units.
- **Local Base Station**:
  - Text: "Logger will auto-sync when it sees this network on the surface."
  - SSID field (scan button → live list, same pattern as portal WiFi scan elsewhere)
  - Assume open or WPA2-PSK; password field only if not open
  - Status dot: green if AP was seen in the last scan, grey if not
- **Cloud/Online**:
  - SSID input (scan button → live list)
  - Password input (masked)
  - Cloud endpoint URL field
  - "Test Connection" button → probes the endpoint, reports reachable/unreachable
  - Status dot: green = reachable, red = failed, grey = untested

**Storage** — extend `state.json` (same POST-the-full-DOM pattern as `/api/deploy`,
so START MISSION and SETTINGS continue to share one save model without wiping each
other's fields):

```json
{
  "divesync": {
    "mode": "none|local|cloud",
    "local_ssid": "BasePi-AP",
    "local_pass": "",
    "cloud_ssid": "HomeWiFi",
    "cloud_pass": "...",
    "cloud_endpoint": "https://api.example.com/upload"
  }
}
```

---

## Phase 2: Firmware state machine (main.cpp + shared.h)

**New globals / struct (shared.h):**

```cpp
enum SyncMode { SYNC_NONE, SYNC_LOCAL, SYNC_CLOUD };

struct DiveSyncState {
  SyncMode mode;
  char     local_ssid[32], local_pass[64];
  char     cloud_ssid[32], cloud_pass[64], cloud_endpoint[128];
  uint32_t idleDeadlineMs;   // millis() target for sleep trigger (10-30 min window)
  uint8_t  phase;            // 0=idle 1=scan 2=connecting 3=syncing 4=sleep-armed
};
extern DiveSyncState diveSync;
```

**State transitions:**

```
RUN mode, diving (g_logging || g_submerged):
  -> unchanged. Sample loop only. DiveSync never polls here.

RUN mode, surfaced (!g_logging && !g_submerged):
  -> if diveSync.mode == SYNC_NONE: no-op, normal RUN behavior (existing dim/off timers apply)
  -> else: diveSyncCheck() runs once per loop tick
       1. WiFi scan for target SSID (local_ssid or cloud_ssid)
       2. if found: connect, then diveSyncPost() all /dive*.csv not yet in the
          sync manifest
       3. on completion (success or 20s timeout): start idle deadline timer
       4. if button pressed at any point: cancel, resume normal RUN, reset timer
  -> idle deadline reached with no button press: deepSleepNow()
```

**Key functions (new, main.cpp):**

- `diveSyncCheck()` — called once per loop when surfaced + mode != NONE; drives the
  scan → connect → sync sequence as a non-blocking `millis()` state machine (per
  hard rule: no `delay()` in the run path).
- `diveSyncPost()` — chunked, non-blocking POST of each unsynced CSV; yields back to
  `loop()` between chunks rather than blocking on the whole file.
- `diveSyncManifestMark(path)` — records a file as synced (SD-resident manifest, see
  Phase 3) so a reboot mid-cycle doesn't re-upload everything.
- `deepSleepNow()` — WiFi off, sensors to sleep/stop-polling, backlight PWM detached,
  `esp_light_sleep_start()` with RTC timer armed.

---

## Phase 3: Sleep / wake sequencing

**Post-sync shutdown sequence (before sleep):**

1. Close log file if still open, flush SD
2. Mark synced files in the on-SD manifest (append-only CSV: filename, epoch, status)
3. `WiFi.mode(WIFI_OFF)`
4. Sensors to sleep where supported (POET, BAR30, Celsius, ADS1015 for Cyclops) —
   otherwise just stop polling them; most go quiescent on their own when unpolled
5. `ledcDetach(PIN_BL)` — kill backlight PWM
6. Arm idle deadline: **10–30 min**, tunable, no button press cancels the countdown
7. `esp_sleep_enable_timer_wakeup()` + `esp_light_sleep_start()`

**Wake triggers:**

- **Button press** (any time before or after sleep) → resume normal RUN loop.
  WiFi stays off until the next surfaced check — don't reconnect just because the
  user glanced at the screen.
- **RTC timer wakeup** → wake briefly, check state, re-arm sleep if nothing to do.

**Known limitation without a real RTC:** wake timing is relative (`millis()` /
`esp_sleep_enable_timer_wakeup()`), not wall-clock. "Sleep until Monday" style logic
needs the DS3231 (already scaffolded via `nowUnix()` / `g_timeSynced` / `g_timeApprox`,
deferred to next board rev). For v1.x, "sleep N minutes, then re-check" is sufficient
and requires **zero additional hardware** — confirmed against rev 2.0 board, no budget
for a wake-cut load switch or added RTC this cycle.

---

## Phase 4: Cloud integration — Supabase

Backend = **Supabase** (existing account). Base functionality: **upload dives + view
dives**. Deliberately minimal — no serverless code at MVP. One dive = one file upload
+ one metadata row, both issued by the device itself over the station-mode WiFi leg.

**Local Base Station (Phase 4 local variant) is still the field MVP** — validate sync +
sleep against a sit-and-forget Pi first. The Supabase path below reuses the same
`diveSyncPost()` plumbing (swap endpoint + headers).

### Why device-direct upload

The device POSTs straight to Supabase over its own internet connection. This sidesteps
the **mixed-content wall** (an HTTPS cloud page cannot fetch from the logger's HTTP
SoftAP) that killed the browser-mediated pull in earlier planning. No cross-origin leg,
no CORS, no captive-portal HTTPS problem.

### Identity + auth model (two SEPARATE problems)

1. **Device → cloud (upload).** Keyed by **ESP32-C6 base MAC** as `device_id`.
   - **MVP gate:** MAC allowlist table + FK constraint. A MAC not on the list can't
     insert. This is a **soft gate** — MACs ride in the payload and are spoofable, so it
     filters *casual* junk, not a determined actor holding the publishable key. Acceptable
     for a small trusted fleet.
   - **Hardening (later, no rewrite):** per-device secret baked into firmware /
     `state.json`; `allowed_devices.secret_hash` stores `sha256(secret)`; a thin Edge
     Function checks MAC+secret before the write. Real "our devices only" auth without
     OAuth's token-refresh baggage on a sealed ESP32.
2. **Human → cloud (view).** Supabase Auth (email/password or Google **OAuth**) on the
   viewer app. Separate from the device path — OAuth belongs *here*, not on the device.

### API key model (current Supabase, 2026)

Use the **new publishable key** (`sb_publishable_…`), not the legacy `anon` JWT — legacy
keys are slated for deprecation end of 2026. **Gotcha:** publishable/secret keys must be
sent on the **`apikey` header only**; putting them in `Authorization: Bearer` is rejected
as an invalid JWT. Secret key (`sb_secret_…`) NEVER goes on the device — dashboard /
Edge Function only.

### Supabase objects

**Storage bucket:** `dives` (private).

**Tables** (schema as a CLI migration, committed to the repo):

```sql
-- known units; pre-seed with our traced MACs
create table public.allowed_devices (
  mac         text primary key,     -- base MAC, lowercase hex no colons, e.g. 'f412fa123456'
  label       text,                 -- 'Unit 03'
  secret_hash text,                 -- nullable now; sha256(per-device secret) for hardening
  added_at    timestamptz default now()
);

-- one row per uploaded dive
create table public.dives (
  id            uuid primary key default gen_random_uuid(),
  device_id     text not null references public.allowed_devices(mac),  -- FK = allowlist gate
  filename      text not null,
  storage_path  text not null,
  cast_num      int,
  mission text, operator text, site text, water_type text,
  lat double precision, lon double precision,
  utc_start     timestamptz, time_source text,
  cal_ph bool, cal_ec bool, cal_orp bool, cal_cyc bool,
  cyclops_units text,
  row_count     int,
  uploaded_at   timestamptz default now(),
  unique (device_id, filename)      -- idempotent re-upload
);
```

**RLS** — the **FK does the allowlist gate** (FK validation is not subject to RLS, so we
don't need an anon-readable allowlist, which would leak the MAC list):

```sql
alter table public.dives           enable row level security;
alter table public.allowed_devices enable row level security;

-- device (publishable/anon role) may insert; FK rejects unknown MACs
create policy dives_insert_anon on public.dives
  for insert to anon with check (true);

-- authenticated humans read everything
create policy dives_read_auth on public.dives
  for select to authenticated using (true);

-- allowlist stays opaque to anon; managed via dashboard / service role
create policy devices_read_auth on public.allowed_devices
  for select to authenticated using (true);
```

**Storage policies** (`storage.objects`) — anon may upload into the bucket; only authed
users may read. Path is not allowlist-gated at MVP (the `dives` table FK is the real
gate); the hardening Edge Function closes that if needed:

```sql
create policy dive_upload_anon on storage.objects
  for insert to anon        with check (bucket_id = 'dives');
create policy dive_read_auth  on storage.objects
  for select to authenticated using (bucket_id = 'dives');
```

### Device request format (2 HTTPS calls per dive, surface-only, streamed)

**1 — raw CSV to Storage:**

```
POST https://<ref>.supabase.co/storage/v1/object/dives/<mac>/dive0007.csv
apikey: sb_publishable_xxx
Content-Type: text/csv
x-upsert: true
<body: raw CSV bytes, STREAMED from SD — do not buffer whole file in RAM>
```

**2 — metadata row via PostgREST (upsert):**

```
POST https://<ref>.supabase.co/rest/v1/dives
apikey: sb_publishable_xxx
Content-Type: application/json
Prefer: resolution=merge-duplicates      -- upsert on unique(device_id, filename)

{ "device_id":"<mac>", "filename":"dive0007.csv",
  "storage_path":"<mac>/dive0007.csv",
  "cast_num":7, "mission":"...", "operator":"...", "site":"...",
  "water_type":"...", "lat":27.861, "lon":-80.446,
  "utc_start":"2026-06-28T10:02:00Z", "time_source":"PHONE",
  "cal_ph":true, "cal_ec":true, "cal_orp":true, "cal_cyc":true,
  "cyclops_units":"ppb", "row_count":300 }
```

The device already holds every one of these fields in the `deploy` struct + the CSV
meta header (`writeMetaHeader()` in `main.cpp`), so building the JSON is cheap — no
on-device CSV re-parsing needed.

### ESP32-C6 TLS notes

- `WiFiClientSecure`. MVP: `setInsecure()` (skips cert validation — flag as MVP-only);
  hardening: bundle the Supabase root CA.
- Stream the CSV body off SD in chunks; TLS + full-file buffering would blow RAM.
- Non-blocking, surface-only, per the Phase 2 hard constraint. Cache-then-upload, same
  as OTA — never simultaneous with the SoftAP.

### Allowlist seeding

Insert our known/traced MACs into `allowed_devices` up front (dashboard SQL editor or a
seed migration). `secret_hash` left null until the hardening pass.

### Viewer app (base "view dives")

Static site (host anywhere — even a Supabase Storage bucket). Supabase Auth login →
list `dives` rows → signed URL to pull the CSV → render charts by **reusing the portal
SVG chart renderer** (`showChart`/`drawCharts`/`miniChart`/`decimate`/`parseCsv` from
`portal_page.h`). Already CDN-free and parses this exact CSV format — on-device and
cloud viewers share one renderer.

### Build tooling (Claude Code)

- **Schema = Supabase CLI migrations**, committed to the repo (`supabase/migrations/`),
  applied via `supabase db push`. Source of truth, PR-reviewable, matches the existing
  git/release discipline.
- **Supabase MCP server** (`https://mcp.supabase.com/mcp`, http type in `.mcp.json`)
  optional for interactive query/inspection. Add it **read-only** (`?read_only=true`) —
  it connects with service-role-level access that bypasses RLS; do schema writes on a
  branch, not against prod.

### Hardening upgrade (noted, not built at MVP)

Per-device secret → Edge Function upload proxy verifying `mac + secret` against
`allowed_devices.secret_hash` before writing Storage + row. Deploy via
`supabase functions deploy`. Only step that turns the soft MAC gate into real auth.

---

## Non-blocking / safety notes

- No blocking POST loops — chunked reads, yields to `loop()` between chunks (hard rule 5).
- No WiFi scan during a dive — surfaced + not logging, full stop.
- WiFi unreachable → 20s timeout, skip sync, proceed to idle-then-sleep anyway. Never
  hang the unit waiting on a network that isn't there.
- SD-resident sync manifest prevents duplicate uploads across reboots.
- Matches existing SoftAP-vs-internet mutual exclusivity constraint — DiveSync's WiFi
  use is cache-then-upload just like OTA, never simultaneous with anything else needing
  the radio.

---

## Acceptance criteria (v1.x)

- [ ] Portal SETTINGS card: None / Local / Cloud all selectable and persisted via
      the existing `/api/deploy` unified save model
- [ ] Firmware detects base AP only when surfaced and not logging
- [ ] All unsynced `/dive*.csv` files POST successfully, or the unit skips gracefully
      on timeout without hanging
- [ ] Sync manifest prevents re-upload of already-synced files across reboots
- [ ] Idle timer (10–30 min, no button press) triggers deep sleep
- [ ] Deep sleep sequence: WiFi off, sensors quiesced, backlight off, light sleep
      with RTC timer wake armed
- [ ] Button press wakes immediately at any point in the sequence
- [ ] **Dive loop is provably untouched** — no new WiFi/sleep behavior triggers while
      `g_logging || g_submerged`
- [ ] Field test: diver docks unit near base AP, walks away without touching
      anything, unit syncs and sleeps unattended

**Cloud (Supabase) base functionality:**

- [ ] `allowed_devices` + `dives` tables and RLS applied via CLI migration; known MACs seeded
- [ ] Storage bucket `dives` (private) with anon-insert / authed-read policies
- [ ] Device uploads raw CSV to `dives/<mac>/<file>` using publishable key on `apikey` header
- [ ] Device posts metadata row via PostgREST; upsert idempotent on `(device_id, filename)`
- [ ] Unknown MAC is rejected by the FK (allowlist gate proven)
- [ ] Viewer app: Supabase Auth login, lists dives, renders charts via reused portal renderer
- [ ] Uploaded dives flagged synced in the on-SD manifest and KEPT (no deletion)
