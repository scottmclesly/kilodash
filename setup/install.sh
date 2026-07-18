#!/usr/bin/env bash
#
# Scottina base installer — fresh Kali Pi 5 → running kiosk.
#
# Takes a clean image to a booting touchscreen dashboard: apt deps, the tree at
# /opt/kilodash, the SPI/DRM display overlay, and the systemd unit enabled. It
# does NOT install the optional backends (web apps, logic analyzer, GPS, tables,
# micro KVM) — those are separate phase scripts, listed at the end and in
# docs/INSTALL.md.
#
# Idempotent: safe to re-run. It confirms state rather than duplicating it —
# the overlay line is added only if absent, config.txt is backed up only when it
# is actually changed, and apt/systemd steps no-op when already satisfied.
#
#     sudo setup/install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # the checkout this script lives in
DEST="/opt/kilodash"
CONFIG_TXT="/boot/firmware/config.txt"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

# ------------------------------------------------------------------ apt deps ---
# The rendering path is pure Python-to-framebuffer (PIL + numpy, no SDL/X), touch
# is evdev, and the app shells out to nmcli / arp-scan / vcgencmd. con2fbmap (from
# fbset) is used by the unit's ExecStartPre to map the console onto fb0.
say "APT dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y \
  python3 python3-pil python3-numpy python3-evdev \
  network-manager arp-scan fbset libraspberrypi-bin

# --------------------------------------------------------------- place tree ---
say "Install tree at $DEST"
if [ "$SRC_ROOT" = "$DEST" ]; then
  note "already running from $DEST — nothing to copy"
else
  mkdir -p "$DEST"
  # Copy source only; never clobber per-device runtime state or captured data.
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'config.json' \
    --exclude 'captures/*' \
    --exclude 'tables/*' \
    --exclude '__pycache__' \
    "$SRC_ROOT"/ "$DEST"/
  note "copied $SRC_ROOT -> $DEST"
fi

# ----------------------------------------------------------- display overlay ---
# One clean overlay brings up the ILI9486 over SPI/DRM. rotate=90 is the only
# display knob that needs a reboot; touch axes are handled in software.
say "Display overlay in $CONFIG_TXT"
OVERLAY_SPI="dtparam=spi=on"
OVERLAY_SCR="dtoverlay=piscreen,drm,rotate=90"
if [ ! -f "$CONFIG_TXT" ]; then
  note "WARNING: $CONFIG_TXT not found — not a Pi boot partition? Skipping overlay."
  note "Add these two lines to your firmware config.txt by hand, then reboot:"
  note "    $OVERLAY_SPI"
  note "    $OVERLAY_SCR"
elif grep -qF "$OVERLAY_SCR" "$CONFIG_TXT"; then
  note "overlay already present — leaving config.txt untouched"
else
  BAK="${CONFIG_TXT}.kilodash-bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "$CONFIG_TXT" "$BAK"
  note "backed up config.txt -> $BAK"
  {
    echo ""
    echo "# --- Scottina ILI9486 SPI panel (added by setup/install.sh) ---"
    grep -qF "$OVERLAY_SPI" "$CONFIG_TXT" || echo "$OVERLAY_SPI"
    echo "$OVERLAY_SCR"
  } >> "$CONFIG_TXT"
  note "added SPI + piscreen overlay (reboot required to take effect)"
fi

# --------------------------------------------------------------- systemd unit ---
say "systemd service"
install -m644 "$DEST/kilodash.service" /etc/systemd/system/kilodash.service
systemctl daemon-reload
systemctl enable --now kilodash
note "kilodash.service enabled and started"

# ----------------------------------------------------------------- next steps ---
say "Done"
cat <<EOF
   Scottina is installed and the service is running.

   * If the display overlay was just added, REBOOT for the panel to light up:
         sudo reboot

   * Optional backends (install only what you want — see docs/INSTALL.md):
         setup/install-phase4.sh          web apps: Node-RED, AIS, Signal K
         setup/install-logic-analyzer.sh  Logic screen (sigrok + fx2lafw)
         setup/install-gps.sh             GPS screen (gpsd + chrony)
         setup/install-tables.sh          Tables converter service
         setup/install-microkvm.sh        off-grid Micro KVM command plane

   Service status:  systemctl status kilodash
   Logs:            journalctl -u kilodash -f
EOF
