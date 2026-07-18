<!--
Thanks for contributing to Scottina. Keep it small, glanceable, and honest.
See CONTRIBUTING.md for the ground rules.
-->

## What this changes
A short description of the change and why.

## Safety boundary touched?
Scottina is diagnostics-only, enforced in code. Check any this PR touches, and
say how the invariant is preserved:

- [ ] **LAN Scan** — no raw-flag input, no shell strings, no re-added rejected flag
- [ ] **CAN / NMEA2K** — stays RX-only; the only TX-permitted module is `n2k/node.py`
- [ ] **Micro KVM** — `list[str]` argv only, no free strings to subprocesses, inert on-network
- [ ] None of the above

If you checked one, explain how you kept it (and which test still proves it).

## Tests
- [ ] `python -m unittest discover -s tests` passes
- [ ] Added/updated tests for new behavior (especially near a boundary)

## Docs
- [ ] Updated the relevant `docs/*.md` guide if behavior changed
- [ ] Links resolve

## Notes
Anything reviewers should know — screenshots of the panel welcome.
