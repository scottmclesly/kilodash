# LAN Scan — user guide

Scottina's **LAN Scan** screen answers three network questions without a laptop
or a memorised `nmap` incantation: *what's alive on my subnet, what services do
those hosts run, and is an expected port open on a known host.* It is
**diagnostics-only by construction** — there is no raw-flag input, so no
offensive scan can be expressed from this screen (see
[Why it stays diagnostics-only](#why-it-stays-diagnostics-only)).

It's always on the Home screen (built-in, no dongle required).

---

## The screen

Tap the **LAN Scan** tile. Top to bottom:

| Control | What it does |
|---|---|
| **Target field** | Tap to type an **IP**, **hostname**, or **CIDR** (e.g. `192.168.1.0/24`). Pre-filled with your current subnet on first open. Invalid targets are rejected with a toast. |
| **Run / Stop** | Starts the scan; becomes **Stop** while running. |
| **Mode control** (Discover · Ports · Services · Identify) | The four things the tool can do — see below. The mode *is* the safety boundary. |
| **Ports field** | Appears **only in Ports mode**. Blank = a curated list of common ports; or type your own (`22,80,443` or `1-1024` — digits, commas, hyphens only). |
| **Host badge** | Counts hosts discovered by the running/last scan. |
| **Status line** | Current phase, or the last result. |
| **Output pane** | Results stream in as scrolling rows (▲▼ to scroll long output). |

## The four modes

| Mode | Question it answers | Notes |
|---|---|---|
| **Discover** | Which devices are alive on the subnet? | Point it at a CIDR (`…/24`) to sweep the whole network. |
| **Ports** | Is an expected port open on this host? | Uses the Ports field (blank = common ports). |
| **Services** | What service + version is each open port running? | Service/version detection on the target. |
| **Identify** | What OS is this host, best-effort? | Needs root (Scottina runs as root); refuses gracefully otherwise. |

## Typical sessions

**"What's on this network?"**
Target `192.168.1.0/24`, mode **Discover**, Run. The host badge counts live
devices as they answer; each shows in the output pane.

**"Is the Pi's SSH up?"**
Target the host's IP, mode **Ports**, Ports field `22`, Run.

**"What's this box running?"**
Target the IP, mode **Services**, Run — you get service names and versions per
open port.

## Why it stays diagnostics-only

Every command is assembled from the **mode + validated target + validated
ports** into an argument array — never a shell string, so there is nothing to
inject into. The UI has no free-text flag entry, and a defense-in-depth
reject-list refuses NSE scripting (`--script`, `-sC`), stealth/evasion scans
(`-sS`, `-sF`, `-sX`, `-sN`), aggressive mode (`-A`), decoys/spoofing (`-D`,
`-S`, `--spoof-mac`), fragmentation (`-f`, `--mtu`, `--data-length`) and
evasion timing (`-T4`, `-T5`) even if a value somehow arrived from elsewhere.

The full rationale (and the tests that prove each mode's exact arguments) is in
the main README's [LAN Scan safety model](../README.md#lan-scan-safety-model-why-the-rejected-flags-stay-rejected).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Invalid target" toast | Target must be a plain IP, hostname, or CIDR — no ports or flags in this field. |
| "Ports: digits, commas, hyphens only" | The Ports field takes `22,80,443` or `1-1024` — nothing else. |
| Identify refuses / returns little | OS detection needs raw sockets (root). It degrades gracefully rather than erroring. |
| Discover finds nothing on a `/24` | Confirm you're on the network (IP in the header) and the CIDR matches your subnet. |
| Scan seems stuck | Tap **Stop**; large CIDR sweeps and Services detection simply take time. |
