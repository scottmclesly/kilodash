# CanTick — Scottina (Pi) Side Integration To-Do

**For:** Claude Code, running in the Remote-SSH kilodash session **on the Pi**.
**Repo:** `kilodash` (already git). Work on a branch: `git checkout -b cantick-integration`.
**Authoritative spec:** `PROTOCOL.md` (copy it into the kilodash repo root first —
it is the contract; implement against it, not against the firmware source).

**Project scope:** Diagnostics + *normal* CAN participation ONLY. CanTick tunnels a
CAN bus over WiFi so it appears here as an ordinary SocketCAN interface (`slcan0`).
The Pi side must never introduce an offensive capability. The one allowed TX is
normal node traffic; **listen-only must remain fully enforceable from this side.**

**Guard-rail principle (carried across the project):** safety is enforced in code.
The interface-manager may only ever construct the exact `socat`/`slcand` invocations
in PROTOCOL.md §1 — never an arbitrary shell string built from device-supplied data.

---

## Status — 2026-07-08 (branch `cantick-integration`)

Implemented and verified on this Pi (fake CanTick: local TCP client + UDP
heartbeat): Phases 1, 2, 3, 6 and the code for 4 and 5. Loopback proof passed:
`candump slcan0` shows dialed-in frames; TCP drop → supervised relaunch →
reconnect works; teardown leaves nothing; heartbeat freshness + `v:2` warning
fire. 35 unit tests in `tests/test_cantick.py` pin the §1 argv, CRC/framing,
heartbeat and AP-config generation.

**Deviations / still open (needs the real hardware or a decision):**

- **`PROTOCOL.md` was NOT on this machine** and is not fetchable from here.
  The copy at the repo root is **reconstructed from this TODO** — drop in the
  authoritative one and diff it (CRC-coverage and reply-framing assumptions
  are marked in it).
- **`hostapd` is not installed** (dnsmasq is). Phase-5 code is in place and
  refuses gracefully; `sudo apt install hostapd` (and mask its service) to
  arm it. Not installed silently, per Phase 0.
- Phase-0 findings: `wlan0` is **NetworkManager**-managed (creds path =
  `nmcli`); the PHASE2 "uplink watchdog" is the per-screen guard thread in
  `wifisniff.py`/`kismet.py` (not running while the CAN screen is open) — the
  standing reconnector is NM autoconnect, so AP "pause/resume" =
  unmanage/re-manage `wlan0` with prior state recorded.
- Bench-only items outstanding (§7): provisioning round-trip against a real
  CanTick, AP fallback cycle with a real client, listen-only scope check on
  the transceiver, Node-RED/Signal K reads against a live WiFi bridge.

## 0. Preconditions (do first, verify before writing code)

- [ ] Confirm `PROTOCOL.md` exists at the kilodash repo root; read it fully.
- [ ] Confirm the working tree is clean and you are on the `cantick-integration` branch.
- [ ] Verify required binaries are present (do NOT install without noting it):
      `socat`, `slcand`/`slcan_attach` (from `can-utils`), `candump`, `ip`.
      For Phase 5 only: `hostapd`, `dnsmasq`. Report anything missing; don't
      silently `apt install`.
- [ ] Identify how the Pi manages `wlan0` (NetworkManager vs wpa_supplicant/dhcpcd).
      Record the answer — Phases 4 and 5 depend on it.
- [ ] Locate the existing uplink watchdog referenced in `PHASE2.md` (keeps `wlan0`
      connected; ALFA is `wlan1`). Do NOT modify it yet — note where it lives.

---

## 1. CanTick interface-manager (build + prove this FIRST)

New module `kilodash/cantick.py`. This is the concept-proof: it must make
`candump slcan0` show frames coming from CanTick over WiFi. Everything else
(Node-RED, Signal K) works for free once this does.

- [ ] `class CanTickLink` with `start()` / `stop()` / `status()`.
- [ ] `start()` runs EXACTLY the PROTOCOL.md §1 reference invocation as an
      **argument list** (never a shell string):
  - [ ] `socat TCP-LISTEN:29536,reuseaddr PTY,link=/dev/cantick0,raw,echo=0`
  - [ ] `slcand -o -c -s5 /dev/cantick0 slcan0`  (`-s5` = 250 k)
  - [ ] `ip link set slcan0 up`
- [ ] Bitrate → `-s<code>` mapping table sourced from PROTOCOL.md §1 (250k→5).
- [ ] **Supervise both processes**: if either exits (CanTick WiFi drop closes the
      TCP side), tear `slcan0` down and relaunch the pair so the next dial-in
      re-establishes cleanly. Backoff, don't hot-loop.
- [ ] `stop()` cleanly kills slcand + socat, `ip link set slcan0 down`, removes
      `/dev/cantick0`.
- [ ] Idempotent: `start()` when already up is a no-op; `stop()` when down is safe.
- [ ] Log state transitions; never leave a half-torn-down interface on crash.

**Verify before moving on:** with a CanTick (or a laptop faking the SLCAN-over-TCP
client) connected, `candump slcan0` prints frames. Commit.

---

## 2. Heartbeat listener + freshness

Per PROTOCOL.md §2: CanTick sends send-only UDP JSON to port 29537 every 2 s.

- [ ] `class HeartbeatListener` binds `udp/29537`, parses one JSON datagram per read.
- [ ] Track per-device (`name`) last-seen timestamp + latest fields
      (`fw, bitrate, mode, rx, tx, drop, rssi, v`).
- [ ] Expose `is_fresh(name)` → False if no datagram for > 6 s (3 missed).
- [ ] **Contract check:** if `v` != expected (`1`), flag a version-mismatch warning
      for the UI; keep running.
- [ ] The listener is read-only: it MUST NOT send anything back on 29537.
- [ ] Run it in a background thread with the same deferred-thread pattern used
      elsewhere in kilodash; drop to a slow idle when no CanTick is present.

---

## 3. CAN screen integration (`kilodash/screens/canbus.py`)

The screen already handles slcan interfaces generically; add CanTick as a source
and surface its health. Reuse existing shared primitives — no bespoke widgets.

- [ ] Add a **CanTick source mode** to the screen. When selected (or when a
      heartbeat is seen), call `CanTickLink.start()` on `on_enter` and
      `stop()` on `on_exit` — same lifecycle discipline as logging.
- [ ] Health card from the heartbeat: device `name`, `mode` (normal/listen/closed),
      `rssi`, live `rx`/s, `drop` counter, and a **fresh/stale badge** (reuse the
      Signal K freshness-indicator style).
- [ ] Keep the existing bitrate picker / RX-frame counter / logging working against
      `slcan0` unchanged.
- [ ] If `drop > 0` and rising, show it prominently — it's the early warning that
      the bus is out-running the MCP2515/WiFi path.
- [ ] Respect `tick_interval`: fast tick while frames flow, idle when quiet
      (guardrail already established for CAN/Signal K screens).

---

## 4. USB provisioning push (Pi → CanTick, one-time)

Per PROTOCOL.md §4. When a CanTick is plugged into the Pi's USB, push WiFi creds.

- [ ] Extend the `devices.py` hotplug pattern to detect CanTick by USB VID `0x303A`
      (optionally match a product string "CanTick"); map it to a provisioning action.
- [ ] `class CanTickProvisioner` opens the CDC serial (`/dev/ttyACM*`, 115200).
- [ ] Implement the `CTK1|` framing from PROTOCOL.md §4:
  - [ ] CRC-16/CCITT-FALSE over the body before `|CRC=` (see PROTOCOL.md appendix).
  - [ ] base64-encode `ssid`/`psk` values.
  - [ ] Commands: `SET_CREDS slot=primary`, `SET_CREDS slot=fallback`,
        `SET_NET bitrate=… listen_only=…`, `COMMIT`, then `GET_STATUS` to verify.
  - [ ] Ignore/accept `ACK`/`NAK`/`STATUS` replies; retry a `NAK err=crc`.
- [ ] **Primary creds:** read the Pi's *current* WiFi SSID + PSK
  - [ ] NetworkManager: `nmcli -s -g 802-11-wireless-security.psk connection show <name>`
        (needs root; `-s` reveals the secret).
  - [ ] wpa_supplicant path: parse `/etc/wpa_supplicant/wpa_supplicant.conf`.
  - [ ] Pick the branch matching the Phase-0 finding.
- [ ] **Fallback AP creds:** generate a strong PSK **once**, store it in kilodash
      `config.json` (`cantick.fallback_psk`), and push the same pair to every CanTick.
      This PSK is what the Phase-5 AP will host.
- [ ] Never log PSKs. `STATUS` from the device won't contain one; keep it that way.
- [ ] Surface provisioning as an explicit user action (button/prompt on the CAN
      screen when a CanTick is detected) — not an automatic silent write.

---

## 5. AP fallback (do LAST — riskiest; must be reversible)

Per PROTOCOL.md §5. When the CAN screen opens and the Pi has **no uplink**, host a
WPA2 AP on `wlan0` so a remote CanTick can still reach the Pi. Tear it down on close.

> This touches the radio the uplink normally uses. Treat every step as reversible
> and coordinate with the existing uplink watchdog — do not fight it, gate it.

- [ ] Detect "no uplink": no default route AND `wlan0` not associated. Only then
      may the AP come up. If an uplink exists, do nothing (CanTick joins the LAN).
- [ ] Before starting the AP: **pause** the uplink watchdog (find it from Phase 0);
      record prior `wlan0` management state so it can be restored exactly.
- [ ] Bring up the AP from generated config (write to a kilodash-owned temp path,
      not system defaults):
  - [ ] `wlan0` static `192.168.42.1/24`.
  - [ ] `hostapd`: SSID `Scottina-CanTick`, WPA2, PSK = `config.json` fallback_psk.
  - [ ] `dnsmasq`: DHCP on `192.168.42.0/24`, gateway `.1`, and
        `address=/scottina.local/192.168.42.1` so discovery resolves.
- [ ] On CAN-screen close (or uplink returns): stop dnsmasq + hostapd, flush the
      static IP, **restore** prior `wlan0` management, and **resume** the watchdog.
- [ ] Guarantee teardown on crash/exception (context manager / finally). Never
      strand `wlan0` in AP mode.
- [ ] Do NOT touch `wlan1`/ALFA at any point.

---

## 6. Config + wiring

- [ ] Add a `cantick` block to `config.json`: `enabled`, `slcan_iface` (slcan0),
      `tcp_port` (29536), `hb_port` (29537), `bitrate` (250000),
      `fallback_ap_ssid` (Scottina-CanTick), `fallback_psk`, `ap_gateway`
      (192.168.42.1), `expected_contract_version` (1).
- [ ] All ports/names come from config, defaulting to the PROTOCOL.md values.
- [ ] Wire `CanTickLink`, `HeartbeatListener`, and (Phase 5) the AP manager into the
      CAN screen's enter/exit lifecycle.

---

## 7. Testing & verification

- [ ] **Concept proof:** `candump slcan0` shows CanTick frames over WiFi (Phase 1).
- [ ] **Downstream unchanged:** Node-RED SocketCAN node binds `slcan0`; Signal K /
      canboatjs reads `slcan0` for NMEA2000 (native addon already built in
      `install-phase4.sh`). No new config beyond selecting the interface.
- [ ] **Reconnect:** drop CanTick power / walk it out of range; confirm `slcan0`
      recovers with no kilodash restart.
- [ ] **Heartbeat/freshness:** kill the heartbeat; badge flips to stale within 6 s.
- [ ] **Contract mismatch:** send `v:2`; confirm the warning fires, link still runs.
- [ ] **Provisioning round-trip:** factory-clear a CanTick, plug USB, push creds,
      confirm `GET_STATUS` reports `prov=1` and it connects on next boot.
- [ ] **AP fallback cycle:** boot with no uplink, open CAN screen → AP up, CanTick
      joins, frames flow; close screen → AP down, `wlan0` restored, watchdog resumed.
- [ ] **Negative/safety:** with the device in listen-only, confirm no `t/T/r/R`
      the host sends results in bus traffic (scope the transceiver if possible);
      confirm the interface-manager never builds a shell string from device data.

---

## 8. Documentation

- [ ] Top-of-module comment in `cantick.py` + `canbus.py` stating diagnostics-only
      scope and linking `PROTOCOL.md`.
- [ ] README section: what CanTick is, the "it's just `slcan0`" model, provisioning
      quick-start, and the AP-fallback behaviour.
- [ ] Note in `PROTOCOL.md` change process: bump the contract version on both sides.

---

## Definition of Done

- [ ] `candump slcan0`, Node-RED, and Signal K all read a WiFi-bridged CanTick with
      zero interface-specific changes downstream.
- [ ] Interface-manager is supervised, idempotent, and only ever emits the fixed
      PROTOCOL.md invocations (no shell-string construction from device data).
- [ ] Heartbeat drives a fresh/stale indicator and a contract-version check.
- [ ] USB provisioning writes primary + fallback creds without logging secrets.
- [ ] AP fallback is fully reversible, coordinates with the uplink watchdog, and
      never strands `wlan0` or touches the ALFA.
- [ ] Listen-only is enforceable end-to-end; scope documented in code + README.
