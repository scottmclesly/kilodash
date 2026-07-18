# Security Policy

Scottina fronts real diagnostic tools (nmap, CAN, SDR, Wi-Fi capture) on a
root-privileged Raspberry Pi. Its safety story is a design commitment, not a
disclaimer — so security reports are genuinely welcome.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

- Preferred: open a private [GitHub Security Advisory](https://github.com/scottmclesly/Scottina/security/advisories/new)
  on this repository.
- Or email **mclesly@gmail.com** with `SECURITY` in the subject.

Include what you can: affected file/screen, how to reproduce, and the impact you
see. You'll get an acknowledgement within a few days. There's no bounty — this
is a personal shop tool — but every valid report gets a fix and a credit in the
changelog unless you'd rather stay anonymous.

## What counts as a vulnerability here

Scottina is **diagnostics-first by construction**, and several boundaries are
enforced in code and covered by tests. A way to *cross one of these boundaries*
is exactly the kind of report worth sending:

- **LAN Scan is diagnostics-only.** Commands are built as argument arrays (no
  shell string), the four modes are the entire attack surface, and a reject-list
  refuses NSE / stealth / evasion / spoofing flags. Any input that reaches a
  scan as a raw flag, a shell fragment, or an injected argument is a bug. See
  [docs/LANSCAN.md](docs/LANSCAN.md#why-it-stays-diagnostics-only-the-safety-model).
- **CAN / NMEA2K are receive-only**, with a single tree-wide TX exception
  (`n2k/node.py`, the GNSS source node); the CanTick link-layer heartbeat lives
  in device firmware, off-tree. Any send-shaped call on a socket outside that
  allow-list is a bug. See [docs/CANBUS.md](docs/CANBUS.md#limits-by-design).
- **The Micro KVM command plane** executes only an allow-listed verb set, passes
  no free strings/paths/flags to any subprocess, and is inert while on-network.
  A way to run an un-listed action, command it while home is reachable, or slip
  a shell fragment past the reject pass is a bug. See
  [docs/MICROKVM.md](docs/MICROKVM.md#safety-boundaries-why-you-cant-hurt-yourself-with-this).

Reports that these invariants can be broken take priority. General hardening
suggestions are welcome too — just as normal issues, not advisories.

## Scope note

Scottina is intended for **authorized** bench diagnostics on networks, buses,
and devices you own or have permission to test. It ships no offensive tooling,
and the boundaries above exist to keep it that way. Please keep your testing on
your own equipment.

## Supported versions

This is a single-branch project: fixes land on `main`. There are no
back-supported release branches — run the current `main`.
