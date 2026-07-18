# Logic Analyser — user guide

Scottina's logic analyser screen drives a **Cypress FX2LP (CY7C68013A-56) dev
board** through the packaged `sigrok-cli` / fx2lafw stack: 8 digital channels
(D0–D7), up to 24 MHz sample rate, edge triggering, and read-only protocol
decoding (UART / I2C / SPI / CAN). It **captures and decodes only** — it can
never drive a signal; the command builder refuses anything but capture at
build time.

---

## ⚠️ Read this first: 3.3 V logic only

The bare FX2LP board has **no input protection**. Its inputs are 3.3 V logic.

- **Never** connect 5 V, 12 V, or anything near Scottina's battery/CAN wiring
  directly to D0–D7.
- Use a series resistor, level shifter, or divider when probing anything you
  aren't sure about. Toasting the $10 board is cheap; back-feeding the Pi
  through its USB port is not.
- Always connect **GND first** — probe ground to circuit ground.

## Hardware setup

1. Plug the FX2LP board into any Pi USB port. The **Logic tile appears on the
   Home screen only while the board is plugged in** (and disappears when you
   unplug it), like the other device tiles.
2. Wire probes: GND to the target's ground, then any of **D0–D7** to the
   signals you want to watch.
3. There is no LA firmware on the board itself — sigrok uploads it
   automatically at the start of each session. The **first capture after each
   plug-in takes ~1 s longer** while the board re-enumerates. This is normal.

If the stack isn't installed yet (fresh SD card), run:

```
sudo setup/install-logic-analyzer.sh
```

which installs `sigrok-cli` + `sigrok-firmware-fx2lafw` and sets up non-root
capture (udev rules + `plugdev` group).

## The screen

Tap the **Logic** tile. Top to bottom:

| Control | What it does |
|---|---|
| **D0 … D7 chips** | Tap to enable/disable each channel. Lit = captured. At least one channel must stay on. |
| **‹ sample rate ›** | 20 kHz … 24 MHz. Rule of thumb: sample at **≥4× the bus clock** (400 kHz I2C → 2 MHz+, 115200 UART → 1 MHz is plenty). |
| **‹ samples ›** | Capture depth: 256 … 1M samples. Depth ÷ rate = captured time window (e.g. 16k @ 1 MHz ≈ 16 ms). |
| **‹ trigger ›** | `trig off` = capture immediately. Or pick a rising `D0 ↑` / falling `D0 ↓` edge on any **enabled** channel — the capture then waits for that edge (up to 2 min, then gives up). |
| **‹ decoder ›** | `no decode`, `UART 115200`, `UART 9600`, `I2C`, `SPI`, `CAN 500k`. |
| **Run capture** | Starts the one-shot capture. Becomes **Stop** while running. |
| Status bar | Shows the current phase: *Capturing… / Waiting for trigger… / Decoding… / Done · \<file\>.sr*. |

Settings are frozen while a capture is running — Stop it first to change them.

### How decoder pins are assigned

Decoders take their pins from the **lowest-numbered enabled channels, in
order**:

| Preset | Pin order → lowest enabled channels |
|---|---|
| UART | RX |
| I2C | SCL, SDA |
| SPI | CLK, MOSI |
| CAN | RX |

So for I2C: wire SCL to D0 and SDA to D1, enable only D0+D1. For UART: wire
the TX line you're sniffing to D0, enable only D0. (Enable extra channels and
the decoder still uses the lowest ones — the rest are just captured.)

## Reading the results

After a capture the lower pane fills in:

- **Activity strips** (one row per enabled channel): a compact view of the
  whole capture window. A line along the **top** = high, along the **bottom**
  = low, a **vertical tick** = edge(s) inside that slice. A flat top line on
  an unconnected input is normal (floating high).
- **Decoded list**: the decoder's annotations (bytes, addresses, frames) as
  scrolling rows — use the ▲▼ scroll buttons for long decodes. A decode
  problem shows as a red row but never destroys the capture itself.

## Every capture is saved

Each run writes a sigrok session to:

```
/opt/kilodash/captures/la_YYYYmmdd-HHMMSS.sr
```

Pull it off over SSH and open it in **PulseView** on a laptop for full
waveform zooming, more decoders, and exports:

```
scp scott@scottina:/opt/kilodash/captures/la_*.sr .
```

The `.sr` file is written even when you chose `no decode` or the decode
failed — the raw capture always survives.

## Typical sessions

**Sniff a UART debug port (115200):**
D0 → target TX, GND → GND. Enable D0 only, rate 1 MHz, 16k samples, trigger
`D0 ↓` (a start bit is a falling edge), decoder `UART 115200`, Run. Decoded
bytes appear in the list.

**Check an I2C bus:**
D0 → SCL, D1 → SDA. Enable D0+D1, rate 2 MHz, 64k samples, trigger `D1 ↓`
(start condition), decoder `I2C`, Run.

**"Is this line doing anything?"**
Enable the channel, decoder `no decode`, trigger off, Run — the activity
strip answers at a glance.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No Logic tile on Home | Board not detected — check `lsusb` for `04b4:8613` (or an fx2lafw ID). If it shows a different ID, add it to `FX2LA_IDS` in `kilodash/devices.py`. |
| `sigrok-cli not installed` in the status bar | Run `sudo setup/install-logic-analyzer.sh`. |
| Capture fails with *no device found* | Unplug/replug the board, wait 2 s, Run again (the firmware re-uploads on the next run). |
| Stuck on *Waiting for trigger…* | The edge never happened. Tap **Stop**, or check the wiring/trigger channel. Times out by itself after 2 min. |
| Works with sudo on the CLI but not in the app | udev/group issue — re-run the installer, then unplug/replug and restart the service. |
| Garbage from the UART decoder | Wrong baud preset, or you're sampling too slow — raise the sample rate. |

## Limits (by design)

- 8 channels, 24 MHz max, **edge triggers only**, no analog — fine for I2C,
  UART, NMEA0183-at-the-wire and low-speed SPI.
- Sustained deep captures at high rates are bounded by USB 2 bandwidth.
- Capture-only: the safety core (`kilodash/la.py`) allow-lists capture
  parameters and read-only decoders and refuses anything else at build time —
  there is no way to express an output/generator mode from this screen.
