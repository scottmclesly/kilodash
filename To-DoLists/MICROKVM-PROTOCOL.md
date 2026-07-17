# MICROKVM-PROTOCOL.md — Off-Grid Command Plane, Wire Contract v1.0

The single coupling point between whatever composes commands (phone canned
messages, another Meshtastic node, a thin composer page) and Prime's executor
(`microkvm/executor.py`). Like `PROTOCOL.md` (CanTick) and `DOCK-PROTOCOL.md`
(Light Dock), **this grammar is the only thing both ends may assume.**

Nickname honesty: "micro KVM" has **no video and no interactive I/O**. The
link is ~kbps, duty-cycle-limited, second-scale latency. This is a one-line
command → one-line reply plane, the inverse of the pager.

## 0. Transport

- One command = **one Meshtastic text message** on the dedicated **command
  channel** (see `docs/LORAMESH.md`; channel PSK membership is the coarse
  crypto boundary — never share the command PSK with sensor chatter).
- One reply = **one Meshtastic text message**, sent as a **DM to the sender**
  (delivery-ack for free; no broadcast chatter on the shared channel).
- No app-level ack/CRC/retransmit protocol. Meshtastic's delivery-ack says a
  frame *landed*; the reply body says the verb *worked*. The gap between the
  two is closed by two disciplines: **replies state resulting state** and
  **action verbs are idempotent** (blind re-sends are the *normal* case).
- Prime side: BLE (meshtastic-python) to the Prime radio T3. Never WiFi —
  Prime's WiFi is reserved for the web app.

## 1. Command frame

```
<verb> [arg]...
```

- Single line, printable ASCII, **space-delimited tokens**, no quoting, no
  escaping. Leading/trailing/repeated whitespace is tolerated (phone keyboards
  add it).
- `verb` is **lowercase** (mixed-case input is folded to lowercase before
  lookup — canned messages survive autocapitalize).
- Args are **case-folded to lowercase** too; every arg is a token from a
  **closed enumeration or bounded value** declared below. There is **no
  free-form argument**: no paths, no shell fragments, no user strings. A verb
  whose implementation would need one is a verb this plane does not ship.
- A frame must be composable by hand in the bare Meshtastic app: no JSON, no
  binary, nothing a human can't type from a canned-messages list.

## 2. Reply grammar

Exactly **one** reply per command, single line, ≤ 200 chars, terse:

```
<verb>: <body>
```

The prefix is the verb answered, so a person reading raw channel traffic can
pair request↔reply. Examples:

```
status: up 3h04m, 47.2C, tile=home, armed=yes, rssi=-104/8.5
tile: active=nmea2k
```

### Rejection replies

Every rejection is distinct and says *why* nothing happened:

| Case | Reply |
|---|---|
| off-list verb | `reject: unknown-verb '<token>'` |
| wrong arg count | `<verb>: reject bad-arity want=<n> got=<m>` |
| arg outside its domain | `<verb>: reject bad-arg <name>='<token>'` |
| action verb while disarmed | `<verb>: reject disarmed (<reason>)` |
| sender not on node allow-list | *(silence — frame is logged, never dispatched, never answered)* |

An unknown-verb token is echoed back **truncated to 24 chars** and stripped to
printable ASCII — the reply must never become a vehicle for reflecting junk.

## 3. Arm gate (availability model)

The executor is **armed only when Prime's home network is unreachable**
(`microkvm/armgate.py`). Home is identified *positively*: the configured home
gateway/host answers **and** (if configured) the current SSID matches. A
look-alike SSID with no gateway reachability reads as **armed** — off-grid is
when this plane must exist. Debounced (N consecutive agreeing checks) so a
flapping link doesn't thrash arm↔disarm.

- **Disarmed** (home): every `action` verb is refused with the `disarmed`
  rejection *before dispatch*. `read-only` verbs still answer — harmless, and
  confirms the plane is alive.
- **Armed** (off-grid): full registry available.
- **Unconfigured** home identity ⇒ permanently **disarmed** (a bench unit can
  never be commanded just by holding the channel PSK).

## 4. Verb registry (v1.0) — THE allow-list

The registry in `microkvm/registry.py` derives from this table, never the
reverse. Executable = exactly these verbs, these arities, these domains.

| Verb | Args | Class | Maps to | Reply (success) |
|---|---|---|---|---|
| `status` | — | read-only | internal: uptime, SoC temp, active tile, armed, last RSSI/SNR | `status: up <t>, <temp>C, tile=<slug>, armed=<yes\|no>, rssi=<dbm>/<snr>` |
| `health` | — | read-only | internal: service summary, disk/temp/mem headroom, armed echoed | `health: svcs <name>=<up\|down>…, disk <n>%, mem <n>%, temp <t>C, armed=<yes\|no>` |
| `snap <metric>` | `metric` ∈ `temp mem disk load uptime wifi` | read-only | internal one-shot metric read | `snap: <metric>=<value>` |
| `tile <name>` | `name` ∈ known tile slugs (launcher screen titles, lowercased, alnum only — e.g. `nmea2k`, `lanscan`, `pihealth` — plus the alias `home` for the launcher) | action | internal: request UI screen switch | `tile: active=<slug>` |
| `cap start\|stop <target>` | `op` ∈ `start stop`; `target` ∈ `can` | action | internal process mgmt around argv `["candump", "-L", "-n", "100000", "can0"]` → `captures/microkvm-<target>.log` | `cap: running target=<t> pid=<n>` / `cap: stopped target=<t>` |
| `svc restart <name>` | `op` ∈ `restart`; `name` ∈ `kilodash signalk nodered kismet` | action | argv `["systemctl", "restart", "<name>.service"]`, then `is-active` | `svc: restarted <name> state=<active\|failed\|…>` |
| `reboot` | — | action | internal: **reply first**, then argv `["systemctl", "reboot"]` after 15 s | `reboot: scheduled in 15s` |

Idempotency (normative, unit-tested):

- `tile X` twice → same reply twice; no error.
- `cap start can` twice → second replies `cap: running target=can pid=<n>
  (already)`; **one** capture process, never two.
- `cap stop can` twice → second replies `cap: stopped target=can (was not
  running)`.
- `svc restart` twice → two restarts (safe repeat, systemd serializes).
- `reboot` twice → second replies `reboot: already scheduled`; one timer.

Airtime discipline: one reply per command. No progress frames, no heartbeats
on the command channel, no retry storms from Prime's side.

## 5. What this grammar cannot express (normative)

There is **no verb and no argument** that passes a free-form string, path,
flag, or shell fragment to anything. Every subprocess the plane can start is
a fixed `list[str]` argv from the table above, with only domain-enumerated
tokens substituted. The executor runs an **independent reject pass** after
registry lookup (the `scan.py` `_enforce_rejects` pattern): resolved argv must
be `list[str]`, its binary must be on the fixed binary allow-list
(`systemctl`, `candump`), every substituted token re-checked against its
domain. A registry edit that widens a domain by mistake is caught there.
**No arbitrary remote shell, ever** — not as a verb, not as an escape hatch.

## 6. Authorization layers (outermost first)

1. **Channel PSK** (crypto boundary) — only command-channel members are heard.
2. **Sender node-ID allow-list** on Prime (defense-in-depth; node IDs are
   spoofable and are **not** the crypto barrier — this narrows *within* the
   trusted channel). Unknown node → logged, ignored, unanswered.
3. **Arm gate** — action verbs execute only off-grid.
4. **Verb registry + reject pass** — what execution even means.

## 7. Versioning

This is v1.0. Verbs may be *added* in later minors; a verb's arity, domains,
class, or reply prefix never changes meaning without a major bump declared
here. Both ends (canned messages on the phone, executor on Prime) cite this
file.
