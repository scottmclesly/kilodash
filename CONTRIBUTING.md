# Contributing to Scottina

Thanks for looking. Scottina is a focused shop tool — a tap-driven front panel
for diagnostic software you already trust — so contributions that keep it small,
glanceable, and honest are the ones that land easily.

## Ground rules (the ones that aren't negotiable)

Scottina runs as root and fronts real tools. A few boundaries are enforced in
code and by tests, and a PR that weakens one will not be merged:

- **LAN Scan stays diagnostics-only.** No raw-flag input, no shell strings, and
  don't re-add any rejected nmap flag "to be helpful." The mode *is* the safety
  boundary. See [docs/LANSCAN.md](docs/LANSCAN.md#why-it-stays-diagnostics-only-the-safety-model).
- **CAN / NMEA2K stay receive-only.** The only TX-permitted module in the whole
  tree is `n2k/node.py` (the GNSS source node); `tests/test_txscan.py` scans the
  repo to enforce that. No injection, replay, or arbitrary-frame TX anywhere in
  the UI. See [docs/CANBUS.md](docs/CANBUS.md#limits-by-design).
- **The Micro KVM plane** passes no free strings/paths/flags to any subprocess —
  `list[str]` argv with domain-checked tokens only, and it stays inert while
  on-network. See [docs/MICROKVM.md](docs/MICROKVM.md#safety-boundaries-why-you-cant-hurt-yourself-with-this).

If your change touches one of these areas, say in the PR how you kept the
invariant, and make sure the relevant test still passes.

## Development setup

Scottina targets a Raspberry Pi 5 with an ILI9486 SPI panel, but most logic runs
and tests fine on any Linux box. Core runtime deps (all `apt` packages on the Pi
image): `python3-pil`, `python3-numpy`, `python3-evdev`, plus `nmcli`,
`arp-scan`, and `vcgencmd`. `python3-pygame` is **not** used — rendering goes
straight to `/dev/fb0`.

To exercise a screen without the panel, the `KILODASH_OPEN` dev seam lets you
launch straight into a screen; see the app entry in [run.py](run.py) and
[kilodash/app.py](kilodash/app.py).

## Running the tests

```bash
python -m unittest discover -s tests
```

The suite is the contract for the safety boundaries above (`test_scan.py`,
`test_busmon.py`, `test_txscan.py`, `test_n2k.py`, …). Run it before you open a
PR, and add tests for new behavior — especially anything near a boundary.

## Adding a screen

1. Subclass `screens.base.Screen`, implement `draw_content` and `handle_tap`.
2. Give it a `glyph` — a Semiotic-Standard-style pictogram in
   [kilodash/pictograms.py](kilodash/pictograms.py) (unknown keys fall back to a
   filled dot).
3. Register it in `screens/__init__.py::SCREENS`.
4. Hotplug tiles appear only while their device is present — see
   [kilodash/devices.py](kilodash/devices.py).
5. For a screen fronting a third-party web app, subclass
   `WebAppScreen` ([screens/webapp_base.py](kilodash/screens/webapp_base.py))
   instead and set `app_name`/`port`/`service`.

New tunables go in `config.py::DEFAULTS` — the Settings screen renders whatever
it finds there, so a knob is a one-line change.

## Style

- Match the surrounding code: its naming, comment density, and idioms. Scottina
  has a consistent voice — read the neighbours before you write.
- Keep the UI **discrete-tap only** (resistive touch can't tell a swipe from a
  drag) and touch targets ≥44 px.
- Fast screens report dirty rects and honour `tick_interval`; don't full-frame
  redraw on every tick. See "Live screens & responsiveness" in the
  [README](README.md).
- User-facing docs live in [docs/](docs/), one guide per screen. If you change
  behavior, update the guide.

## Pull requests

- One focused change per PR; describe what it does and why.
- Note any boundary it touches and how the invariant is preserved.
- Make sure `python -m unittest discover -s tests` is green.

## License

By contributing you agree your work is licensed under the project's
[MIT License](LICENSE).
