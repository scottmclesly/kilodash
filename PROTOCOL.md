# CanTick ↔ Scottina Protocol — contract v1

> **⚠ RECONSTRUCTED COPY.** The authoritative `PROTOCOL.md` lives with the
> CanTick firmware and was not present on this machine when the Pi side was
> implemented. This file is reconstructed from the protocol details embedded in
> `CanTick-ScottinaSide-TODO.md`. **Replace it with the authoritative copy and
> diff against this one** — anything marked *(assumed)* below is a documented
> assumption that must be verified against the firmware side. Change process:
> bump the contract version (`v`) on both sides for any breaking change.

Scope: diagnostics + *normal* CAN participation only. CanTick tunnels a CAN bus
over WiFi so it appears on the Pi as an ordinary SocketCAN interface (`slcan0`).
Listen-only is enforced **on the device** (set at provisioning time) and must
remain enforceable from the Pi side; the Pi never introduces an offensive
capability.

## §1 — SLCAN-over-TCP data path

CanTick **dials in** to the Pi on TCP port **29536** and speaks standard SLCAN
(Lawicel) framing over that stream. The Pi terminates the stream on a PTY and
attaches the kernel `slcan` line discipline. Reference invocations (the Pi side
must run exactly these, as argument lists, never shell strings):

```
socat TCP-LISTEN:29536,reuseaddr PTY,link=/dev/cantick0,raw,echo=0
slcand -o -c -s5 /dev/cantick0 slcan0
ip link set slcan0 up
```

Bitrate → SLCAN `-s` code mapping (Lawicel standard):

| bitrate | 10k | 20k | 50k | 100k | 125k | 250k | 500k | 800k | 1M |
|---------|-----|-----|-----|------|------|------|------|------|----|
| `-s`    | 0   | 1   | 2   | 3    | 4    | **5**| 6    | 7    | 8  |

Default bus bitrate: **250000** (`-s5`).

`socat TCP-LISTEN` accepts one connection; when the CanTick's WiFi drops, the
TCP side closes, socat exits and the PTY disappears. The Pi supervises the pair
and relaunches so the next dial-in re-establishes cleanly.

## §2 — Heartbeat

CanTick sends a **send-only** UDP JSON datagram to Pi port **29537** every
**2 s**. One JSON object per datagram *(assumed: flat object)*:

```json
{"name": "cantick-01", "fw": "1.0.3", "bitrate": 250000, "mode": "normal",
 "rx": 12345, "tx": 17, "drop": 0, "rssi": -58, "v": 1}
```

- `name` — device identity (string).
- `fw` — firmware version string.
- `bitrate` — configured bus bitrate.
- `mode` — `normal` | `listen` | `closed`.
- `rx` / `tx` — cumulative frame counters. `drop` — cumulative dropped frames
  (bus out-running the MCP2515/WiFi path).
- `rssi` — WiFi signal, dBm.
- `v` — **contract version**, currently `1`. A mismatch is a warning, not a
  fatal error, on the Pi side.

Freshness: a device is **stale** after **6 s** without a datagram (3 missed).
The Pi listener is strictly read-only — it never sends anything on 29537.

## §4 — USB provisioning (Pi → CanTick, one-time)

CanTick enumerates as USB CDC serial, VID `0x303A` (product string "CanTick"),
**115200** baud. Framing:

```
CTK1|<BODY>|CRC=XXXX\n
```

- `XXXX` — 4 uppercase hex digits of **CRC-16/CCITT-FALSE** (poly `0x1021`,
  init `0xFFFF`, no reflection, xorout `0x0000`) computed over **everything
  before `|CRC=`**, i.e. including the `CTK1|` prefix *(assumed — verify;
  check value for `"123456789"` is `0x29B1`)*.
- `ssid` / `psk` values are **base64-encoded** inside the body.

Commands (BODY):

```
SET_CREDS slot=primary ssid=<b64> psk=<b64>
SET_CREDS slot=fallback ssid=<b64> psk=<b64>
SET_NET bitrate=250000 listen_only=0
COMMIT
GET_STATUS
```

Replies: `ACK …`, `NAK err=<reason>`, `STATUS <k=v …>` — same `CTK1|` framing
*(assumed; the Pi parser also tolerates unframed replies)*. On `NAK err=crc`
the sender retries the command once. `STATUS` after a successful `COMMIT`
reports `prov=1`. `STATUS` never contains a PSK; neither side logs one.

## §5 — AP fallback

When the Pi has **no uplink** (no default route and `wlan0` not associated) and
the CAN screen is open, the Pi hosts a WPA2 AP so a remote CanTick can still
reach it. CanTick's `fallback` credential slot points at this AP:

- SSID `Scottina-CanTick`, WPA2-PSK = the fallback PSK pushed at provisioning.
- `wlan0` static `192.168.42.1/24`; DHCP for clients on `192.168.42.0/24`;
  `scottina.local` resolves to `192.168.42.1`.

The AP is torn down when the CAN screen closes or an uplink returns; `wlan0` is
restored to its prior management state. Never touches `wlan1`.

## Ports summary

| Port  | Proto | Direction        | Purpose             |
|-------|-------|------------------|---------------------|
| 29536 | TCP   | CanTick → Pi     | SLCAN stream        |
| 29537 | UDP   | CanTick → Pi     | heartbeat (2 s)     |
