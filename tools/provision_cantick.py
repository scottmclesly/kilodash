#!/usr/bin/env python3
"""
provision_cantick.py — standalone CanTick bring-up / provisioning tool.

Speaks the CTK1 provisioning protocol (PROTOCOL.md §4) over the XIAO's native
USB-CDC port. This is a BRING-UP INSTRUMENT, not part of kilodash: it isolates
one question — does the firmware's provisioning.cpp correctly parse a real,
CRC-checked CTK1| frame and write NVS? — from the whole Pi-side UI stack.

Its main job is to make the one ambiguous seam VISIBLE: the firmware and the Pi
side were implemented from two different copies of the contract, and they had to
guess whether the "CTK1|" prefix is inside the CRC. If they disagree, every frame
fails CRC and nothing provisions. This tool prints the exact bytes it sends and
the raw reply, and can send BOTH CRC conventions so you can see on sight which
one the firmware accepts.

Requires: pyserial  (pip install pyserial)

Examples
--------
# Just watch the device and probe which CRC convention it accepts (writes nothing):
python3 provision_cantick.py --port /dev/ttyACM0 --probe

# Provision primary creds + fallback AP creds, set bitrate, commit, verify:
python3 provision_cantick.py --port /dev/ttyACM0 \
    --ssid BoatLAN --psk 'hunter2secret' \
    --fb-ssid Scottina-CanTick --fb-psk 'apPskGoesHere' \
    --bitrate 250000

# Listen-only device, primary creds only:
python3 provision_cantick.py --port /dev/ttyACM0 \
    --ssid BoatLAN --psk 'hunter2secret' --listen-only
"""

import argparse
import base64
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial not found. Install with:  pip install pyserial")


# ── CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflect, xorout 0x0000) ──
# Matches the reference in PROTOCOL.md appendix and provisioning.cpp.
def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class CrcMode:
    """How much of the line the CRC covers — the ambiguous seam."""
    WITH_PREFIX = "with-prefix"      # CRC over 'CTK1|...'  (everything before '|CRC=')
    WITHOUT_PREFIX = "without-prefix"  # CRC over '...' after the 'CTK1|' prefix


def build_frame(cmd: str, fields: list[tuple[str, str]], crc_mode: str) -> str:
    """
    Assemble one CTK1 line: CTK1|<CMD>|k=v|...|CRC=<hhhh>
    Returns the full line WITHOUT the trailing newline.
    """
    body = "CTK1|" + cmd
    for k, v in fields:
        body += f"|{k}={v}"

    if crc_mode == CrcMode.WITH_PREFIX:
        crc_input = body                      # includes 'CTK1|'
    else:
        crc_input = body[len("CTK1|"):]       # excludes 'CTK1|'

    crc = crc16_ccitt(crc_input.encode("ascii"))
    return f"{body}|CRC={crc:04X}"


def hexdump(label: str, data: bytes) -> None:
    printable = data.decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
    print(f"  {label}: {printable}")
    print(f"  {label} (hex): {data.hex(' ')}")


class CanTick:
    def __init__(self, port: str, baud: int = 115200, verbose: bool = True):
        self.verbose = verbose
        # short read timeout so we can keep draining the WiFi log spam
        self.ser = serial.Serial(port, baud, timeout=0.2)
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        self.ser.close()

    def send_line(self, line: str) -> None:
        raw = (line + "\n").encode("ascii")
        if self.verbose:
            print("→ SEND")
            hexdump("line", raw)
            # show what the CRC was computed over, spelled out
        self.ser.write(raw)
        self.ser.flush()

    def read_replies(self, wait: float = 1.5) -> list[str]:
        """
        Read for `wait` seconds, returning only CTK1| reply lines. Everything
        else (the ESP32 WiFi log spam: NO_AP_FOUND, STA_LEAVING, Preferences
        errors, etc.) is shown dimmed but filtered out of the result.
        """
        deadline = time.time() + wait
        buf = b""
        replies: list[str] = []
        while time.time() < deadline:
            chunk = self.ser.read(256)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", errors="replace").strip("\r")
                if not line:
                    continue
                if line.startswith("CTK1|"):
                    replies.append(line)
                    print(f"← REPLY: {line}")
                elif self.verbose:
                    print(f"    (noise) {line}")
        return replies


# ── high-level command helpers ───────────────────────────────────────────────
def cmd_get_status(dev: CanTick, crc_mode: str) -> list[str]:
    print("\n=== GET_STATUS ===")
    dev.send_line(build_frame("GET_STATUS", [], crc_mode))
    return dev.read_replies()


def cmd_set_creds(dev: CanTick, slot: str, ssid: str, psk: str, crc_mode: str) -> list[str]:
    print(f"\n=== SET_CREDS ({slot}) ===")
    fields = [("slot", slot), ("ssid", b64(ssid)), ("psk", b64(psk))]
    dev.send_line(build_frame("SET_CREDS", fields, crc_mode))
    return dev.read_replies()


def cmd_set_net(dev: CanTick, bitrate: int, listen_only: bool, crc_mode: str) -> list[str]:
    print("\n=== SET_NET ===")
    fields = [("bitrate", str(bitrate)), ("listen_only", "1" if listen_only else "0")]
    dev.send_line(build_frame("SET_NET", fields, crc_mode))
    return dev.read_replies()


def cmd_commit(dev: CanTick, crc_mode: str) -> list[str]:
    print("\n=== COMMIT ===")
    dev.send_line(build_frame("COMMIT", [], crc_mode))
    return dev.read_replies(wait=3.0)  # COMMIT triggers a WiFi (re)connect


def acked(replies: list[str], cmd: str) -> bool:
    return any(r.startswith("CTK1|ACK") and f"cmd={cmd}" in r for r in replies)


def naked_crc(replies: list[str]) -> bool:
    return any(r.startswith("CTK1|NAK") and "err=crc" in r for r in replies)


# ── the CRC-seam prober ───────────────────────────────────────────────────────
def probe_crc_mode(dev: CanTick) -> str | None:
    """
    Send GET_STATUS under BOTH CRC conventions and report which the firmware
    accepts. This is the whole reason this tool exists — it makes the two-repo
    contract ambiguity a five-second observation instead of an hour of guessing.
    """
    print("\n######## CRC-COVERAGE PROBE ########")
    print("Sending GET_STATUS twice — once with 'CTK1|' inside the CRC, once without.")
    print("Watch for which one returns a CTK1|STATUS (or ACK) vs NAK err=crc / silence.\n")

    accepted = None
    for mode in (CrcMode.WITH_PREFIX, CrcMode.WITHOUT_PREFIX):
        print(f"---- trying CRC mode: {mode} ----")
        replies = cmd_get_status(dev, mode)
        got_status = any(r.startswith("CTK1|STATUS") for r in replies)
        got_ack = acked(replies, "GET_STATUS")
        got_nak = naked_crc(replies)
        if got_status or got_ack:
            print(f"  ✔ firmware ACCEPTED CRC mode: {mode}")
            accepted = mode
        elif got_nak:
            print(f"  x firmware rejected this mode with NAK err=crc")
        else:
            print(f"  … no CTK1 reply (firmware may not frame its replies, or wrong mode)")
        time.sleep(0.3)

    print("\n######## PROBE RESULT ########")
    if accepted:
        print(f"Use --crc-mode {accepted}")
        print("If this DIFFERS from what the Pi-side CanTickProvisioner assumes,")
        print("that's your bug: align PROTOCOL.md §4 and both implementations.")
    else:
        print("No CRC mode produced a framed reply.")
        print("Either the firmware doesn't frame replies (check provisioning.cpp reply()),")
        print("or GET_STATUS isn't handled, or the port/baud is wrong.")
    return accepted


def main() -> int:
    p = argparse.ArgumentParser(description="CanTick standalone provisioning / bring-up tool")
    p.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyACM0 or /dev/tty.usbmodem…")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--crc-mode", choices=[CrcMode.WITH_PREFIX, CrcMode.WITHOUT_PREFIX],
                   default=CrcMode.WITH_PREFIX,
                   help="whether the 'CTK1|' prefix is inside the CRC (default: with-prefix)")
    p.add_argument("--probe", action="store_true",
                   help="probe which CRC convention the firmware accepts, then exit (writes nothing)")

    p.add_argument("--ssid", help="primary WiFi SSID")
    p.add_argument("--psk", help="primary WiFi PSK")
    p.add_argument("--fb-ssid", help="fallback AP SSID (default Scottina-CanTick)")
    p.add_argument("--fb-psk", help="fallback AP PSK")
    p.add_argument("--bitrate", type=int, default=250000)
    p.add_argument("--listen-only", action="store_true")
    p.add_argument("--status-only", action="store_true", help="just GET_STATUS and exit")
    args = p.parse_args()

    try:
        dev = CanTick(args.port, args.baud)
    except serial.SerialException as e:
        return f"Could not open {args.port}: {e}"  # printed by sys.exit

    try:
        if args.probe:
            probe_crc_mode(dev)
            return 0

        if args.status_only:
            cmd_get_status(dev, args.crc_mode)
            return 0

        # baseline status before touching anything
        cmd_get_status(dev, args.crc_mode)

        wrote = False
        if args.ssid and args.psk:
            r = cmd_set_creds(dev, "primary", args.ssid, args.psk, args.crc_mode)
            if naked_crc(r):
                print("\n⚠ NAK err=crc on SET_CREDS — CRC coverage mismatch.")
                print("  Run again with --probe to find the mode the firmware accepts.")
                return 1
            wrote = wrote or acked(r, "SET_CREDS")

        if args.fb_ssid or args.fb_psk:
            fb_ssid = args.fb_ssid or "Scottina-CanTick"
            if not args.fb_psk:
                print("⚠ --fb-ssid given without --fb-psk; skipping fallback creds.")
            else:
                cmd_set_creds(dev, "fallback", fb_ssid, args.fb_psk, args.crc_mode)

        cmd_set_net(dev, args.bitrate, args.listen_only, args.crc_mode)

        if wrote or args.fb_psk or args.bitrate or args.listen_only:
            r = cmd_commit(dev, args.crc_mode)
            if not acked(r, "COMMIT"):
                print("\n⚠ COMMIT not ACKed — check replies above.")

        # confirm what stuck (STATUS never echoes a PSK, by contract)
        print("\n=== verifying (GET_STATUS after COMMIT) ===")
        final = cmd_get_status(dev, args.crc_mode)
        status = next((r for r in final if r.startswith("CTK1|STATUS")), None)
        if status:
            print("\n✔ Final device status:")
            print(f"    {status}")
            if "prov=1" in status:
                print("    → device reports provisioned. Watch the monitor: NO_AP_FOUND")
                print("      should stop once it associates with your AP.")
        else:
            print("\nNo STATUS reply — device may not frame replies; check the raw output above.")
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    sys.exit(main())
