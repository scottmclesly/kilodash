# I2C Scan & Serial — bench bring-up guides

Two small, focused screens for board bring-up — "is it the wiring or my code?"
answered in five seconds, before you write a line of driver.

- [I2C Scan](#i2c-scan) — is the sensor acking on the bus?
- [Serial](#serial) — what's this device printing on its UART?

---

## I2C Scan

Scottina's **I2C Scan** screen runs `i2cdetect` on the Pi's **onboard bus
(`i2c-1`)** and lists every address that responds, with a best-guess part name.
The classic new-board question — *is the sensor actually there at the address I
expect?* — answered at a glance.

The **I2C Scan tile appears when the onboard i2c-1 bus is available.** Needs
`i2c-tools` and I2C enabled on the Pi.

### The screen

| Control | What it does |
|---|---|
| **Scan** | Runs `i2cdetect -y 1`. (Also runs automatically the first time you open the screen.) |
| **Status bar** | `N device(s) on i2c-1`. |
| **Address list** | One row per responding 7-bit address, e.g. `0x76 → BMP/BME280`. |

### Reading it

Each row is a hex address plus a **best-effort hint** at the likely part
(OLED displays, common RTCs, IMUs, ADCs, EEPROMs, environmental sensors…).
Unknown addresses show `unknown device` — the hint is a convenience, not an
identification; two different parts can share an address.

### Typical session

Wire the sensor to the Pi's SDA/SCL + 3V3 + GND, open **I2C Scan**. If your
sensor's address shows up, the wiring and the device are good and any remaining
problem is in your code. If it's absent, it's power/wiring/pull-ups — not your
driver.

### Troubleshooting

| Symptom | Fix |
|---|---|
| No I2C tile | I2C not enabled — enable `i2c-1` on the Pi (`dtparam=i2c_arm=on`) and confirm `i2c-tools` is installed. |
| Device not listed | Check 3V3/GND, SDA↔SDA / SCL↔SCL, and that pull-ups are present (many breakout boards include them). |
| Address shows `UU` behaviour | An address claimed by a kernel driver is skipped by `i2cdetect`; that's expected for in-use devices. |
| Wrong part name | The hint is a guess from a lookup table — trust the address, verify the part from its datasheet. |

---

## Serial

Scottina's **Serial** screen lists connected USB-serial adapters and gives a
**read-only live view** of one at a chosen baud — handy for sniffing a device's
debug/UART output without a laptop. Read-only: it never writes to the port.

The **Serial tile appears while a USB-serial adapter is plugged in** (FTDI /
CP210x / CH340, i.e. `/dev/ttyUSB*` or `/dev/ttyACM*`).

### The screen

| Control | What it does |
|---|---|
| **‹ port ›** (left arrows) | Cycle between connected serial ports. |
| **‹ baud ›** (right arrows) | Cycle the baud rate: 115200, 9600, 57600, 38400, 19200, 250000, 460800. |
| **Monitor viewport** | The live incoming text, newest at the bottom (keeps the last ~200 lines). |
| **Open / Close** | Opens the port at the selected baud and starts streaming; Close stops. |

Selectors are only changeable while the port is **closed** — Close first to
switch port or baud. Leaving the screen closes the port automatically.

### Typical session

**Watch a board's boot log:**
Plug in the USB-serial adapter, open **Serial**, pick the port and set the baud
(115200 is the common default), tap **Open**. The device's UART output scrolls
live; **Close** when done.

### Troubleshooting

| Symptom | Fix |
|---|---|
| No Serial tile | No USB-serial adapter detected — check `ls /dev/ttyUSB* /dev/ttyACM*`. |
| "open failed: …" | Port busy (another program holds it) or a permissions issue. Close the other user of the port. |
| Garbage characters | Wrong baud — cycle the baud selector to match the device (common: 115200 or 9600). |
| Nothing appears | The device may not be transmitting, or TX/RX/GND wiring is off. This screen only *reads*. |
