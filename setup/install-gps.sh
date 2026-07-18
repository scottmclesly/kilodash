#!/usr/bin/env bash
#
# Scottina GPS installer — Adafruit Ultimate GPS (PA1616S) on /dev/gps0.
#
# Ecosystem plumbing per GPS-Integration-TODO: udev port-pinning (the PL2303
# has no serial number — USB port 1-1 IS the identity), gpsd bound to
# /dev/gps0 only (no autodiscovery: gpsd left hunting would grab CanTick's
# console or Light's dock port and speak NMEA at them), module baud/rate
# config as gpsd's ExecStartPre, chrony with the gpsd SHM refclock as the
# no-network fallback time source, and the snapshot daemon that writes the
# GPS.md contract file. Idempotent: safe to re-run. Run as root:
#
#     sudo setup/install-gps.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# ---------------------------------------------------------------- apt deps ---
say "APT packages (gpsd + clients + chrony)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y gpsd gpsd-clients chrony

# --------------------------------------------------------------- udev rule ---
say "udev: pin PL2303 in port 1-1 to /dev/gps0"
install -m644 "$SCRIPT_DIR/99-kilodash-serial.rules" \
  /etc/udev/rules.d/99-kilodash-serial.rules
udevadm control --reload
udevadm trigger --subsystem-match=tty

# ------------------------------------------------------------------- gpsd ----
say "gpsd: /dev/gps0 only, no autodiscovery, ExecStartPre module config"
cat > /etc/default/gpsd <<'EOF'
# Managed by kilodash setup/install-gps.sh — see GPS-Integration docs.
# Identity comes from udev (port 1-1 -> /dev/gps0), never gpsd guessing:
# USBAUTO left on would grab other ttyUSB dongles (CanTick console, Light
# dock port) and speak NMEA at them.
DEVICES="/dev/gps0"
GPSD_OPTIONS="-n"
USBAUTO="false"
START_DAEMON="true"
EOF
mkdir -p /etc/systemd/system/gpsd.service.d
install -m644 "$SCRIPT_DIR/gpsd-kilodash-dropin.conf" \
  /etc/systemd/system/gpsd.service.d/kilodash.conf

# ----------------------------------------------------------------- chrony ----
say "chrony: gpsd SHM refclock (NTP preferred, GPS fallback)"
mkdir -p /etc/chrony/conf.d
install -m644 "$SCRIPT_DIR/chrony-gps.conf" \
  /etc/chrony/conf.d/kilodash-gps.conf

# ---------------------------------------------------------------- systemd ----
say "systemd units (replug hook + snapshot daemon)"
install -m644 "$SCRIPT_DIR/kilodash-gps-replug.service" \
  /etc/systemd/system/kilodash-gps-replug.service
install -m644 "$SCRIPT_DIR/kilodash-gps-snapshot.service" \
  /etc/systemd/system/kilodash-gps-snapshot.service
systemctl daemon-reload
systemctl enable gpsd.service kilodash-gps-snapshot.service
systemctl restart gpsd.service || true   # fails loudly only on config fault
systemctl restart kilodash-gps-snapshot.service
systemctl restart chrony

say "Done"
echo "Gate check (near a window / outdoors):"
echo "    cgps                     # wait for a 3D fix, note time-to-first-fix"
echo "    chronyc sources -v       # GPS appears as a selectable source"
echo "    cat /run/kilodash/gps/position.json"
echo "Plug discipline: the GPS dongle lives in USB port 1-1 — that port is"
echo "now THE GPS jack (the PL2303 has no serial number; port = identity)."