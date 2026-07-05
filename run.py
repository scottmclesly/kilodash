#!/usr/bin/env python3
"""Scottina entrypoint. Run as root (needs /dev/fb0, evdev, nmcli, arp-scan):

    sudo python3 /opt/kilodash/run.py

Tap tiles to open tools, Back to return home, ESC/q on a USB keyboard to quit.
(Paths and module names keep the historical working name `kilodash`.)
"""

import sys

from kilodash.app import App
from kilodash.screens import SCREENS


def main():
    try:
        app = App(SCREENS)
    except Exception as e:                       # noqa: BLE001
        print(f"scottina: failed to start: {e}", file=sys.stderr)
        raise
    app.run()


if __name__ == "__main__":
    main()
