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
- [ ] Add a temporary FPS/frame-time counter to the main loop in `app.py`
      (log ms per `draw_content` + per fb blit). We need numbers before/after,
      not vibes.
- [ ] Record baseline on the CAN/RPM screen at the current interval.
- [ ] Confirm the CPU temp via the existing `vcgencmd` path while measuring, so
      we know we're not trading responsiveness for thermal throttling.

### 2. Per-screen tick rate
- [ ] Verify the main loop honors per-screen `tick_interval` (it's meant to).
      If it sleeps a fixed global interval, change it to sleep `min()` of the
      active screen's interval and any overlay's needs.
- [ ] Set `self.tick_interval = 0.05` on the responsive screen(s) only
      (CANScreen, and SignalKScreen's engine page if/when it exists).
- [ ] Leave `WebAppScreen` and the built-in screens at their current 1.0.
- [ ] Re-measure. Expect smoother numbers but higher CPU on the fast screen —
      that's the trade we're accepting, bounded to just that screen.

### 3. Dirty-rect rendering (the main event)
- [ ] In `framebuffer.py`, add a blit path that takes a list of rects
      `[(x0, y0, x1, y1), …]` and packs/writes only those regions to `/dev/fb0`,
      instead of the whole surface. Keep the existing full-frame blit as the
      fallback / first-frame path.
- [ ] Give `Screen` a way to report dirty rects for the frame (e.g. `tick()` or
      `draw_content` returns/records the changed regions). Full redraw on
      `on_enter`, screen transition, and dimming wake.
- [ ] Update the responsive screen(s) to report tight rects around just the
      changing elements (RPM value/needle box, timestamp, freshness dot).
- [ ] Make sure gestures, transitions, the keyboard overlay, and the dimming
      screensaver still force a full redraw — they legitimately touch the whole
      panel. Don't let dirty-rect logic starve those.
- [ ] Re-measure. This should drop per-frame blit cost sharply on mostly-static
      screens.

### 4. Guardrails
- [ ] Cap the fast tick so a wedged data source can't spin the CPU: if the
      source is stale, fall back to the slow interval automatically (ties into
      the freshness/heartbeat logic we already want).
- [ ] Confirm no tearing/artifacts on partial blits at 20 Hz. If RGB565 partial
      writes tear, align rects to whole rows or double-check the pack stride.
- [ ] Remove (or gate behind a config flag) the temporary FPS counter from
      task 1.

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
