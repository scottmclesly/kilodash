#!/usr/bin/env bash
#
# Scottina tables installer — the CAN/NMEA2K split's converter service.
#
# Installs the converter web app's dependencies (Flask, qrcode, pypdf), the
# kilodash-tables systemd unit, and the decode-table store directories
# (TABLES.md §1) with correct ownership. Idempotent: safe to re-run. Run as
# root:
#
#     sudo setup/install-tables.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# ---------------------------------------------------------------- apt deps ---
say "APT dependencies (Flask + qrcode + pypdf, distro-packaged)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3-flask python3-qrcode python3-pypdf

# ------------------------------------------------------------- table store ---
say "Decode-table store (TABLES.md §1)"
# Ownership follows the repo checkout so bench work over SSH can manage
# tables without sudo; the root-run services can always write regardless.
owner="$(stat -c '%U:%G' "$REPO_DIR")"
for d in tables/pgn tables/dbc tables/uploads captures; do
  install -d -o "${owner%%:*}" -g "${owner##*:}" "$REPO_DIR/$d"
done
echo "store at $REPO_DIR/tables (owner $owner)"

# ----------------------------------------------------------------- systemd ---
say "systemd unit"
install -m644 "$SCRIPT_DIR/kilodash-tables.service" \
  /etc/systemd/system/kilodash-tables.service
systemctl daemon-reload
echo "kilodash-tables.service installed (left disabled — the Tables tile"
echo "starts it on demand; it exits itself after its idle timeout)."

say "Done — restart Scottina to load the new screens"
echo "    systemctl restart kilodash"
