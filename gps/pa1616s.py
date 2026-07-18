"""PA1616S (Adafruit Ultimate GPS) module configuration — Phase 0.

Run once before gpsd opens the port (systemd drop-in `ExecStartPre=` on
gpsd.service): baud-probe the module, raise it to 115200, set 10 Hz fixes
and the sentence mix. Idempotent: a module that already speaks valid NMEA
at 115200 (the battery-installed future, or a service restart) is only
re-sent the rate/mix commands, which are cheap. Do not "simplify" the
probe away — with no backup battery fitted today, every boot is a cold
start at the 9600 factory default, but the day a CR1220 goes in the module
boots already-configured at 115200 and the probe handles both.

Why the baud raise is a hard requirement, not an optimization: RMC+GGA+GSA
at 10 Hz is ~2 KB/s ≈ 20 kbit/s on the wire — 9600 baud cannot carry it,
and a module left at 9600 silently drops/garbles sentences. Every PMTK
write here is checksummed and ack-checked ($PMTK001) with timeout+retry so
a failed configuration is loud (non-zero exit → unit failure → tile shows
fault), never silent.

Scope note: this module performs serial TX **to the GPS receiver on
/dev/gps0** — it is module provisioning, not bus traffic. It has no
CAN-scan implications and never touches a CAN socket or the NMEA2000 bus.

Sentence mix (PMTK314): RMC+GGA+GSA every fix (10 Hz), GSV every **5th**
fix (2 Hz). The GPS-Integration TODO asked for every 10th, but the MTK
divisor field's valid range is 0–5 — 5 is the slowest the chip supports.
The sky plot doesn't need 10 Hz and the bandwidth belongs to position.

CLI:  python3 -m gps.pa1616s [--device /dev/gps0]
"""

import sys
import time

DEFAULT_DEVICE = "/dev/gps0"
TARGET_BAUD = 115200
FACTORY_BAUD = 9600
PROBE_WINDOW_S = 2.0
ACK_TIMEOUT_S = 1.5
ACK_RETRIES = 3

CMD_SET_BAUD = f"PMTK251,{TARGET_BAUD}"
CMD_FIX_RATE_10HZ = "PMTK220,100"
# PMTK314 fields: GLL,RMC,VTG,GGA,GSA,GSV + 12 unused + channel count
CMD_SENTENCE_MIX = "PMTK314,0,1,0,1,1,5,0,0,0,0,0,0,0,0,0,0,0,0,0"


class GpsConfigError(Exception):
    """Loud failure: the module could not be probed or configured."""


def checksum(body):
    """NMEA checksum: XOR of the bytes between $ and *."""
    c = 0
    for ch in body.encode("ascii"):
        c ^= ch
    return c


def sentence(body):
    """Full checksummed NMEA/PMTK sentence bytes for a payload body."""
    return f"${body}*{checksum(body):02X}\r\n".encode("ascii")


def valid_sentence(line):
    """True when `line` (bytes) is a checksum-verified NMEA sentence."""
    try:
        text = line.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        return False
    if not text.startswith("$") or "*" not in text:
        return False
    body, _, tail = text[1:].rpartition("*")
    if not body or len(tail) < 2:
        return False
    try:
        want = int(tail[:2], 16)
    except ValueError:
        return False
    return checksum(body) == want


def parse_ack(line):
    """$PMTK001,<cmd>,<flag> → (cmd, flag) or None."""
    if not valid_sentence(line):
        return None
    fields = line.decode("ascii").strip()[1:].rpartition("*")[0].split(",")
    if fields[0] != "PMTK001" or len(fields) < 3:
        return None
    try:
        return int(fields[1]), int(fields[2])
    except ValueError:
        return None


def _open_serial(device, baud):
    import serial                   # pyserial, present on this image
    return serial.Serial(device, baud, timeout=0.25)


def hears_nmea(port, window=PROBE_WINDOW_S, clock=time.monotonic):
    """True when a checksum-valid sentence arrives inside the window."""
    deadline = clock() + window
    while clock() < deadline:
        line = port.readline()
        if line and valid_sentence(line):
            return True
    return False


def probe(device=DEFAULT_DEVICE, open_port=_open_serial,
          window=PROBE_WINDOW_S):
    """(port, baud) with the module speaking valid NMEA — tries 115200
    (already-configured module) then 9600 (factory cold start). Garbage at
    both ⇒ GpsConfigError, fail loudly, tile shows fault."""
    for baud in (TARGET_BAUD, FACTORY_BAUD):
        port = open_port(device, baud)
        if hears_nmea(port, window):
            return port, baud
        port.close()
    raise GpsConfigError(
        f"{device}: no valid NMEA at {TARGET_BAUD} or {FACTORY_BAUD} — "
        "wrong dongle in the GPS jack, or module fault")


def send_cmd(port, body, retries=ACK_RETRIES, timeout=ACK_TIMEOUT_S,
             clock=time.monotonic):
    """Write one PMTK command and wait for its $PMTK001 success ack,
    retrying on timeout. Raises GpsConfigError when the module never acks
    or acks with a failure flag (3 = success per the PMTK spec)."""
    cmd_id = int(body.split(",")[0][4:])
    last = "no ack"
    for _ in range(retries):
        port.write(sentence(body))
        deadline = clock() + timeout
        while clock() < deadline:
            ack = parse_ack(port.readline() or b"")
            if ack is None or ack[0] != cmd_id:
                continue
            if ack[1] == 3:
                return
            last = f"ack flag {ack[1]}"
            break                   # a definitive non-success: retry the cmd
    raise GpsConfigError(f"{body}: {last} after {retries} tries")


def configure(device=DEFAULT_DEVICE, open_port=_open_serial, log=print,
              window=PROBE_WINDOW_S):
    """Full Phase-0 bring-up. Returns the configured baud (always
    TARGET_BAUD on success)."""
    port, baud = probe(device, open_port, window)
    log(f"pa1616s: {device} speaking NMEA at {baud}")
    try:
        if baud != TARGET_BAUD:
            # The baud switch takes effect immediately, so its PMTK001 ack
            # arrives (if at all) at the NEW baud — the reliable ack is
            # simply hearing valid NMEA after reopening at 115200.
            port.write(sentence(CMD_SET_BAUD))
            port.flush()
            time.sleep(0.2)
            port.close()
            port = open_port(device, TARGET_BAUD)
            if not hears_nmea(port, window):
                raise GpsConfigError(
                    f"{device}: silent after baud raise to {TARGET_BAUD}")
            log(f"pa1616s: baud raised to {TARGET_BAUD}")
        send_cmd(port, CMD_FIX_RATE_10HZ)
        send_cmd(port, CMD_SENTENCE_MIX)
        log("pa1616s: 10 Hz fixes, RMC+GGA+GSA every fix, GSV every 5th")
    finally:
        port.close()
    return TARGET_BAUD


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    device = argv[argv.index("--device") + 1] if "--device" in argv \
        else (argv[0] if argv else DEFAULT_DEVICE)
    try:
        configure(device)
    except FileNotFoundError:
        # Absent device is not a failure: gpsd's ExecStartPre must let gpsd
        # start (and wait) with the jack empty; the udev replug hook re-runs
        # this the moment the dongle lands.
        print(f"pa1616s: {device} absent — nothing to configure")
        return 0
    except GpsConfigError as e:
        print(f"pa1616s: FAILED: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"pa1616s: FAILED: {device}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
