# WiFi Sniff — user guide

Scottina's **WiFi Sniff** screen is a **passive** 802.11 survey: it puts a
*second* Wi-Fi adapter into monitor mode and channel-hops with `airodump-ng`,
listing every access point and client it hears — SSID, channel, encryption,
signal. It is **listen-only; there is no injection or deauth** anywhere on this
screen.

Crucially, **your uplink stays up the whole time.** The Pi's own connection
(`wlan0`) and the sniffing adapter are separate radios, so Scottina only ever
touches the adapter that is *not* carrying the default route, and runs a
watchdog that instantly reconnects the uplink if anything knocks it.

The **WiFi Sniff tile appears on Home only while a second Wi-Fi adapter
(e.g. an ALFA) is plugged in.** Needs `aircrack-ng` (`airodump-ng`) installed.

---

## The screen

Tap the **WiFi Sniff** tile.

| Control | What it does |
|---|---|
| **Start / Stop** | Puts the spare adapter into monitor mode and begins capture; Stop restores it to managed mode. |
| **Status bar** | Live counts + uplink health, e.g. `12 APs · 5 sta · uplink OK`. |
| **Row list** | Access points and clients heard, APs first (strongest signal at top). |

Each row:

- **AP rows** — SSID (`<hidden>` if not broadcast), `AP · ch<n> · <encryption>`,
  and the signal in dBm (green > −60, amber down to −75, then grey).
- **Client (station) rows** — the client MAC, and either the network it's
  associated to or an SSID it's actively probing for.

## What it captures, and where

While running, `airodump-ng` writes a rolling CSV under
`/opt/kilodash/captures/` (a hidden `.wifi_sniff*.csv` working file — not
listed by the [Files](FILES.md) screen, which skips dotfiles). The screen
parses it once a second to refresh the list. If you want the raw CSV, grab it
over SSH while the capture is running.

## How the uplink is protected

1. Scottina identifies which adapter holds the default route (your uplink) and
   **never touches it** — only the *other* Wi-Fi radio is switched to monitor.
2. A background watchdog checks the uplink every ~3 s and immediately runs
   `nmcli device connect` if it ever drops, so your SSH session survives the
   whole session.
3. On **Stop** (or leaving the screen) the monitor adapter is handed back to
   NetworkManager as a managed interface — nothing is left in monitor mode.

## Typical session

**"Who's around, and how busy is this channel?"**
Plug in the ALFA, open **WiFi Sniff**, tap **Start**. Watch the AP list fill
in — channel spread tells you what's congested; the client rows show who's
associated where. Tap **Stop** when done.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No WiFi Sniff tile on Home | No second adapter detected. This screen needs a *spare* Wi-Fi radio beyond the uplink. |
| "No second WiFi adapter found" on Start | The only Wi-Fi radio is carrying your uplink — Scottina won't hijack it. Plug in the ALFA. |
| "airodump-ng not found" | Install `aircrack-ng`. |
| Uplink briefly hiccups | The watchdog reconnects within a few seconds; the status bar shows `uplink…` then `uplink OK`. |
| List stays empty | Give it time to hop channels; a very quiet RF environment genuinely shows little. |

## Limits (by design)

- **Passive only** — capture and list, never inject, deauth, or associate.
- Uses a **second** adapter; the primary uplink is off-limits by construction.
- For a fuller survey UI (peer classification, Bluetooth, logging) use the
  [Kismet](WEBAPPS.md#kismet) screen, which shares the same uplink-safe
  monitor discipline.
