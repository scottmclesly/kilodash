#!/usr/bin/env bash
#
# Scottina logic-analyzer installer — FX2LP (CY7C68013A) via sigrok/fx2lafw.
#
# Installs the packaged sigrok stack and sets up non-root capture (udev rules
# + plugdev group) so the Logic screen works as the kilodash service user.
# Idempotent: safe to re-run. Run as root:
#
#     sudo setup/install-logic-analyzer.sh
#
set -euo pipefail

KILODASH_USER="${KILODASH_USER:-scott}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# ---------------------------------------------------------------- apt deps ---
say "APT packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# sigrok-firmware-fx2lafw is MANDATORY: the bare FX2LP board has no LA
# firmware of its own — sigrok soft-loads it on every scan (the first scan
# after each plug costs ~1 s while the board re-enumerates).
apt-get install -y sigrok-cli sigrok-firmware-fx2lafw

# -------------------------------------------------- udev (non-root capture) --
say "udev rules"
rules=""
for f in /lib/udev/rules.d/60-libsigrok.rules \
         /usr/lib/udev/rules.d/60-libsigrok.rules \
         /etc/udev/rules.d/60-libsigrok.rules; do
  if [ -f "$f" ]; then rules="$f"; break; fi
done
if [ -n "$rules" ]; then
  echo "found: $rules (covers the FX2 bootloader and all fx2lafw ids)"
else
  echo "WARNING: 60-libsigrok.rules not found — pull it from the sigrok-util" >&2
  echo "repo into /etc/udev/rules.d/ before non-root capture will work." >&2
fi

say "group membership"
if id -nG "$KILODASH_USER" | grep -qw plugdev; then
  echo "$KILODASH_USER already in plugdev"
else
  usermod -aG plugdev "$KILODASH_USER"
  echo "added $KILODASH_USER to plugdev — restart the kilodash service"
  echo "(or log out/in) for it to take effect"
fi

say "reload udev"
udevadm control --reload
udevadm trigger

# -------------------------------------------------------------------- done ---
say "Done — plug in the board, then verify as $KILODASH_USER (no sudo):"
echo "    sigrok-cli --scan"
echo "    lsusb        # record the board's VID:PID for devices.py (Phase 0)"
echo ""
echo "CAUTION: the bare board has NO input protection — 3.3 V logic only."
echo "Buffer/divide before probing anything near Scottina's 12 V wiring."
