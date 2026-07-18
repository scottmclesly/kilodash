# Micro KVM — off-grid command plane (user guide)

When Scottina Prime is away from its home network — on the water, out of SSH
and web-app reach — this plane is how you talk to it: short text commands
from the Meshtastic app on your phone, over LoRa, answered one terse line at
a time. "Micro KVM" is a nickname, not a promise: **no video, no shell, no
interactive anything** — a ~kbps duty-limited link with second-scale latency
carries one command frame in and one reply back.

The wire contract every side obeys is
[`MICROKVM-PROTOCOL.md`](../To-DoLists/MICROKVM-PROTOCOL.md). The mesh the
plane rides on is provisioned per [`LORAMESH.md`](LORAMESH.md).

## The model in one paragraph

Your phone composes a canned message (`status`, `tile nmea2k`,
`svc restart signalk`, …) on the **command channel**; the Prime radio T3
hears it and hands it to Prime over **BLE** (WiFi stays free for the web
app); Prime's executor checks the verb against a positive allow-list, checks
the sender against a node-ID allow-list, checks the **arm gate** — and only
if Prime really is off-grid does an action verb run. The reply is DM'd back,
stating **resulting state** ("what happened"), not just "ok".

## Armed vs dormant

- **DORMANT (home):** the home gateway (`microkvm.home_host`) answers — you
  have SSH and the web app, so action verbs refuse with
  `... reject disarmed`. Read-only verbs (`status`, `health`, `snap`) still
  answer, which is a handy "is the plane alive" check.
- **ARMED (off-grid):** home is unreachable (debounced ~1 min so a flapping
  link doesn't thrash). Full verb set available.
- **Unconfigured** `home_host` ⇒ permanently dormant. A bench unit can never
  be commanded just because someone holds the channel PSK.

The tile shows the state across the room: amber **ARMED (off-grid)**, green
**DORMANT (home)**, plus BLE link state, last-heard node, and the session
log (every command received: sender, accept/reject reason, reply sent).

## The verbs (v1.0)

| You send | You get back |
|---|---|
| `help` (or `?`, `menu`) | `verbs: status health snap tile cap svc reboot help \| send 'help <verb>' for options` |
| `help tile` | `tile [action]: name={home lanscan nmea2k pihealth signalk settings …}` — the exact options you can pass |
| `status` | `status: up 3h04m, 47.2C, tile=home, armed=yes, rssi=-104/8.5` |
| `health` | `health: svcs kilodash=up signalk=down…, disk 21%, mem 34%, temp 47.2C, armed=yes` |
| `snap temp` (`mem disk load uptime wifi`) | `snap: temp=47.2` |
| `tile nmea2k` (any launcher tile slug, `home` = launcher) | `tile: active=nmea2k` |
| `cap start can` / `cap stop can` | `cap: running target=can pid=812` / `cap: stopped target=can` |
| `svc restart signalk` (`kilodash signalk nodered kismet`) | `svc: restarted signalk state=active` |
| `reboot` | `reboot: scheduled in 15s` (reply first, then it acts) |

**Forgot the syntax?** Send `help` for the verb list, then `help <verb>`
(e.g. `help tile`) to see exactly which options that verb accepts — the
menu is generated from the live registry, so it always matches what the
plane will actually run. Both are read-only, so they answer even while
dormant at home: explore the menu before you ever go off-grid, and save
the ones you want as canned messages.

Everything is idempotent by contract: got no reply? **Send it again** —
that's the designed recovery, never a danger. Delivery-ack (the Meshtastic
checkmark) means the frame reached Prime's radio; the reply body is what
confirms the verb actually ran.

## Setting it up

1. Provision the mesh (channels, PSKs, node names): [`LORAMESH.md`](LORAMESH.md)
   + `tools/provision_mesh.sh`. The command channel is separate from
   telemetry on purpose — its PSK membership is the coarse auth boundary.
2. `sudo setup/install-microkvm.sh` (BlueZ + meshtastic-python + config
   scaffolding).
3. BLE-pair the Prime radio T3 (`bluetoothctl` → scan/pair/trust), put its
   address in `config.json → microkvm.ble_address`.
4. Fill in `home_host` (your home gateway IP — this is the positive home
   identity), optionally `home_ssid`, and `allowed_nodes` (the node IDs of
   your phone/commander nodes, e.g. `"!a1b2c3d4"` — shown in the Meshtastic
   app).
5. Set `microkvm.enabled: true`, `systemctl restart kilodash`.
6. On the phone, save the verbs you'll want as **canned messages** — cold
   fingers on a pitching deck compose nothing.

## Safety boundaries (why you can't hurt yourself with this)

1. Channel PSK — only command-channel members are heard (crypto boundary).
2. Sender node-ID allow-list — unknown nodes are logged and ignored,
   never answered (node IDs are spoofable; this narrows, the PSK guards).
3. Arm gate — action verbs are refused while home is reachable.
4. Verb registry + independent reject pass — there is no verb, arg, or code
   path that passes a free string, path, flag, or shell fragment to
   anything. `list[str]` argv only, binaries allow-listed
   (`systemctl`, `candump`), every token domain-checked twice.

## Troubleshooting

- `status` answers but action verbs say `reject disarmed` — Prime can still
  reach `home_host`; that's the design. Use SSH/web instead.
- **Message "acknowledged" on the phone but no reply** — you sent on the
  wrong channel. Commands only count on **ScotCmd**; a frame on ScotTel
  (the pager channel) is heard by the radio (hence the ack) but dropped
  before the executor, by design — the command-channel boundary is the
  auth boundary (§6), so pager chatter can never execute. In the Meshtastic
  app, switch to the **ScotCmd** chat and send there. The tile's `dropped`
  counter ticks up on each off-channel/unknown-node frame.
- No reply at all: check the tile (BLE link DOWN? node not on
  `allowed_nodes` → dropped silently by contract). The session log names
  every drop reason.
- `tile: reject bad-arg name='…'` — slug is the launcher title lowercased,
  alnum only (`lanscan`, `pihealth`, `nmea2k`), or `home`.
- Sent `reboot` twice in a panic: fine. `reboot: already scheduled`.
