#!/usr/bin/env python3
"""kilodash entrypoint. Run as root (needs /dev/fb0, evdev, nmcli, arp-scan):

    sudo python3 /opt/kilodash/run.py

Swipe left/right between screens, tap to act, ESC/q on a USB keyboard to quit.
"""

import sys

from kilodash.app import App
from kilodash.screens import SCREENS


def main():
    try:
        app = App(SCREENS)
    except Exception as e:                       # noqa: BLE001
        print(f"kilodash: failed to start: {e}", file=sys.stderr)
        raise
    app.run()


if __name__ == "__main__":
    main()
