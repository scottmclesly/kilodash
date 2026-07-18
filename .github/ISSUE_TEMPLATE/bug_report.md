---
name: Bug report
about: Something on the panel doesn't work the way it should
title: ''
labels: bug
assignees: ''
---

**What happened**
A clear description of the bug.

**Which screen / device**
e.g. LAN Scan, CAN (with a CanTick), GPS tile, Micro KVM. Note any dongle
plugged in at the time.

**Steps to reproduce**
1. …
2. …
3. …

**Expected vs actual**
What you expected to see, and what actually happened on the panel.

**Logs**
If you can, capture:

```
journalctl -u kilodash -n 100 --no-pager
```

**Environment**
- Pi model: Raspberry Pi 5
- OS: Kali (image date / `uname -a`)
- Scottina version: (Settings → About row, or `git rev-parse --short HEAD`)
- Display / touch overlay: default `dtoverlay=piscreen,drm,rotate=90`? y/n

**Anything else**
Photos of the panel are welcome — this is a visual tool.
