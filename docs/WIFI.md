# Wi-Fi — user guide

Scottina's **Wi-Fi** screen joins the Pi to a wireless network **headless** —
no keyboard, no HDMI, no `nmcli` over a serial console. It's the screen that
closes the "pulled a Pi off the rack and need to get it online" gap: connect
here, and the moment you're on, **the IP appears in the header** to SSH
straight to.

It's always on the Home screen (built-in). Wi-Fi is managed through
NetworkManager (`nmcli`) under the hood.

---

## The screen

Tap the **Wi-Fi** tile. Top to bottom:

| Control | What it does |
|---|---|
| **Wi-Fi ON / OFF** | Toggles the radio. Turning it on triggers a scan; off clears the list. |
| **Rescan** | Re-scans visible networks (disabled while a scan is in flight). |
| **Network list** | Every SSID heard, strongest first. Each row shows signal bars, channel, security, a **saved** tag for known networks, and a **✓** on the one you're connected to. Secured networks show a 🔒. |

## Connecting

Tap a network row:

- **Open or already-saved network** → connects immediately (a "Connecting…"
  toast, then "Connected" / "Failed").
- **New secured network** → the on-screen keyboard opens for the passphrase
  (masked). Enter it and confirm; Scottina saves the connection so next time
  it's a one-tap reconnect.

Signal bars are colour-graded (green = strong). The connected network floats to
the top with a ✓.

## Typical session

**Join a rack Pi to the bench Wi-Fi:**
Open **Wi-Fi**, make sure it says **Wi-Fi ON**, tap your SSID, type the
password on the on-screen keyboard, confirm. When the ✓ appears the header
shows the Pi's new IP — SSH to it directly.

## Notes

- The list refreshes on a slow cadence while the screen is open; **Rescan**
  forces a fresh sweep.
- Saved networks persist in NetworkManager, so a known SSID reconnects with a
  single tap (no password re-entry).
- This screen manages the Pi's **uplink** radio (`wlan0`). Passive monitoring
  of *other* networks lives on the separate [WiFi Sniff](WIFISNIFF.md) screen
  and uses a second adapter — the two never fight over the same radio.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No networks listed | Confirm the toggle reads **Wi-Fi ON**, then **Rescan**. A weak antenna or a 5 GHz-only AP out of range shows nothing. |
| "Failed: …" toast | Wrong passphrase, or the AP rejected the association. Tap the row again to re-enter the password. |
| Connected but no IP in header | DHCP may still be assigning; give it a few seconds. Check the AP hands out addresses. |
| Can't hit the right row after the 180° flip | Calibrate touch in **Settings → Touch** (see the main README). |
