# Kiosk Speed Improvement — ToDo (for Claude Code)

**Goal:** make live, fast-changing screens (CAN RPM, Signal K engine data) feel
responsive without regressing the low-rate screens (Kismet, Wi-Fi, Pi Health).

**Target:** ~20 Hz (50 ms/frame) on the responsive screens; keep everything else
at 1 Hz. No new heavy dependencies. No threading in the first pass.

**Current state:** full-frame render of 480×320 RGB565 to `/dev/fb0` via PIL +
numpy on every tick; global-ish refresh around 1–2 s. That full blit is the
bottleneck for anything responsive.

---

## Approach (three levers, do 1 + 2 first, defer 3)

1. **Per-screen tick rate** — the `Screen` base already carries
   `self.tick_interval`. Set it low only on the responsive screens; leave the
   rest slow. Cheap, isolated, reversible.
2. **Dirty-rect rendering** — stop repainting the whole panel. Track the regions
   that actually changed (needle, numeric readouts, freshness dot) and blit only
   those rects to `/dev/fb0`. This is the real win.
3. **Background CAN reader thread** *(deferred)* — only if 1 + 2 still feel
   choppy. Queue decoded frames, drain in the main loop. Watch PIL thread-safety.

---

## Tasks

### 1. Baseline + measurement (do this first)
- [x] Add a temporary FPS/frame-time counter to the main loop in `app.py`
      (log ms per `draw_content` + per fb blit). We need numbers before/after,
      not vibes. *(`_FpsMeter`; on-panel overlay + journald log, gated behind
      the new `show_fps` setting — Settings → System → "FPS meter".)*
- [x] Record baseline on the CAN/RPM screen at the current interval.
      *(See "Measured results" below. Surprise: the fbdev `write()` returns in
      ~1.35 ms — SPI flushing is deferred/async in `ili9486drmfb`, so the old
      sluggishness was the tick intervals + redundant full redraws, not a
      blocked blit. Dirty rects still shrink the SPI damage the kernel worker
      pushes, which is what bounds actual panel latency.)*
- [x] Confirm the CPU temp via the existing `vcgencmd` path while measuring.
      *(61.5 °C before, 60.9 °C after — no thermal story.)*

### 2. Per-screen tick rate
- [x] Verify the main loop honors per-screen `tick_interval` (it's meant to).
      If it sleeps a fixed global interval, change it to sleep `min()` of the
      active screen's interval and any overlay's needs. *(It honored it, but
      slept a fixed 50 ms; now sleeps `min(0.05, tick_interval / 2)` so fast
      screens actually hit ~20 Hz while slow screens keep today's cadence.)*
- [x] Set `self.tick_interval = 0.05` on the responsive screen(s) only
      (CANScreen, and SignalKScreen — all pages, since the heartbeat is the
      liveness cue). *(CANScreen also grew the live RX frame counter + rate
      readout the module docstring always promised, fed by one sysfs read.)*
- [x] Leave `WebAppScreen` and the built-in screens at their current 1.0.
      *(Bonus: `WebApp.poll()` now returns change-only instead of always-True,
      so idle web-app screens stopped repainting the full panel at 1 Hz, and
      its TCP probe is throttled to every 2 s regardless of tick rate.)*
- [x] Re-measure. *(See below.)*

### 3. Dirty-rect rendering (the main event)
- [x] In `framebuffer.py`, add a blit path that takes a list of rects
      `[(x0, y0, x1, y1), …]` and packs/writes only those regions to `/dev/fb0`,
      instead of the whole surface. Keep the existing full-frame blit as the
      fallback / first-frame path. *(`blit(img, rects=…)`; rects are merged
      into full-width row bands — that's the damage unit the DRM fbdev
      emulation clips to anyway, and it sidesteps stride/pack edge cases.)*
- [x] Give `Screen` a way to report dirty rects for the frame. *(Screens call
      `self.report_dirty(box…)` inside `tick()`; `App` only honors it via the
      tick path, so every other `dirty = True` source stays a full redraw.)*
- [x] Update the responsive screen(s) to report tight rects around just the
      changing elements. *(CAN: the RX-counter card. Signal K: vitals grid +
      heartbeat on each REST fetch, heartbeat bar alone between fetches.)*
- [x] Make sure gestures, transitions, the keyboard overlay, and the dimming
      screensaver still force a full redraw. *(All of those set `App.dirty`,
      whose setter clears any pending rects → full frame. Toast expiry now
      explicitly forces a full repaint so a partial blit can't strand it.)*
- [x] Re-measure. *(See below.)*

### 4. Guardrails
- [x] Cap the fast tick so a wedged data source can't spin the CPU. *(CAN: no
      frame-count change for 1 s → 0.5 s tick until traffic resumes. Signal K:
      app down → 0.5 s tick; REST fetch failing or all data stale (>15 s) →
      fetch backs off 0.25 s → 1.5 s.)*
- [x] Confirm no tearing/artifacts on partial blits at 20 Hz. *(Partial writes
      are whole-row bands by construction; a byte-level test verified partial
      blits leave the fb identical to a full blit on the 32bpp and 16bpp
      paths.)*
- [x] Remove (or gate behind a config flag) the temporary FPS counter from
      task 1. *(Gated behind `show_fps`, default off.)*

---

## Measured results (2026-07-04, Pi 5, ili9486drmfb 320×480 @32bpp)

Blit cost on the real `/dev/fb0` (service stopped for the bench):

| path                                   | avg ms | vs full |
|----------------------------------------|-------:|--------:|
| full frame (baseline)                  |  1.35  |    1×   |
| 220-row band (Signal K vitals grid)    |  0.63  |  2.1×   |
| 74-row band (CAN RX counter card)      |  0.18  |  7.4×   |
| 28-row band (Signal K heartbeat bar)   |  0.09  | 15.3×   |
| pack-only CPU cost, full frame         |  1.13  |    —    |

CPU temp 61.5 °C → 60.9 °C across the run. Previous service session averaged
~1.1 % CPU (12 s over 17.6 min) — compare after some runtime on the fast
screens via `systemctl status kilodash`.

---

## Acceptance criteria
- RPM / live CAN values update smoothly (subjectively ~20 Hz), no visible lag.
- Kismet / Wi-Fi / Pi Health unchanged in cost and behavior.
- Idle CPU temp on the fast screen stays within a sane margin (record the number).
- Transitions, keyboard overlay, and dimming wake all still repaint cleanly.
- No new third-party dependencies.

## Out of scope (this pass)
- Threading / async CAN reader (lever 3) — revisit only if still choppy.
- Any GPU / DRM-plane acceleration. Staying on the PIL→`/dev/fb0` path.

## Notes / footguns
- Full-surface redraw must remain the path for: first frame, `on_enter`, screen
  transitions, overlay draws, and screensaver wake.
- `tick_interval = 0.05` is per-screen on purpose — don't lower the global loop
  rate, or you'll wake the slow screens 20× more often for nothing.
- Measure at each step. If dirty-rect gets complex for marginal gain on a given
  screen, that screen can stay full-frame — the two paths coexist.
