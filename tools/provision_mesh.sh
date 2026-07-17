#!/usr/bin/env bash
#
# provision_mesh.sh — Meshtastic node bring-up for the Scottina mesh.
#
# A BRING-UP INSTRUMENT in the provision_cantick.py spirit: applies the ONE
# canonical radio config (docs/LORAMESH.md) to a node plugged into USB, per
# role. Region/preset/channel identity comes from this file + mesh-secrets.env
# so every node matches by construction — a region or preset mismatch is a
# silent no-mesh and the #1 bring-up failure.
#
#   tools/provision_mesh.sh prime     [--port /dev/ttyACM0]
#   tools/provision_mesh.sh sensor    [--port ...] [--admin-key <base64>]
#   tools/provision_mesh.sh companion [--port ...]
#   tools/provision_mesh.sh qr        [--port ...]   # join-QR for the phone
#   tools/provision_mesh.sh verify    [--port ...]   # read back --info
#
# PSKs: generated once into /opt/kilodash/mesh-secrets.env (git-ignored,
# per-boat, mode 600). The ScotCmd PSK is the command plane's auth boundary
# (MICROKVM-PROTOCOL.md §6) — it never goes to sensor/companion nodes.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="$REPO_DIR/mesh-secrets.env"

# ---- the pinned radio config (docs/LORAMESH.md — change it THERE first) ----
REGION="US"
PRESET="LONG_SLOW"
TEL_NAME="ScotTel"          # slot 0 (primary): telemetry + pager
CMD_NAME="ScotCmd"          # slot 1: command plane (prime + phone ONLY)
TELEMETRY_INTERVAL=1800     # 30 min — airtime is shared and duty-limited
SERIAL_BAUD="BAUD_38400"
SERIAL_RXD="${SERIAL_RXD:-13}"   # per board silk — check before wiring
SERIAL_TXD="${SERIAL_TXD:-14}"

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { echo "error: $*" >&2; exit 1; }

MESHTASTIC="$(command -v meshtastic || true)"
[ -z "$MESHTASTIC" ] && [ -x "$HOME/.local/bin/meshtastic" ] \
  && MESHTASTIC="$HOME/.local/bin/meshtastic"
[ -n "$MESHTASTIC" ] || die "meshtastic CLI not found (pipx install meshtastic)"

# ------------------------------------------------------------------ secrets --
ensure_secrets() {
  if [ ! -f "$SECRETS" ]; then
    say "Generating channel PSKs (once, per boat) -> $SECRETS"
    umask 077
    {
      echo "# Scottina mesh channel PSKs — git-ignored, per-boat. Regenerating"
      echo "# means re-provisioning EVERY node and the phone (docs/LORAMESH.md)."
      echo "TEL_PSK=$(openssl rand -base64 32)"
      echo "CMD_PSK=$(openssl rand -base64 32)"
    } > "$SECRETS"
  fi
  # shellcheck disable=SC1090
  . "$SECRETS"
  [ -n "${TEL_PSK:-}" ] && [ -n "${CMD_PSK:-}" ] \
    || die "$SECRETS exists but lacks TEL_PSK/CMD_PSK"
}

# --------------------------------------------------------------------- args --
ROLE="${1:-}"; shift || true
PORT=""
ADMIN_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port)      PORT="$2"; shift 2 ;;
    --admin-key) ADMIN_KEY="$2"; shift 2 ;;
    *) die "unknown arg: $1" ;;
  esac
done

find_port() {
  [ -n "$PORT" ] && return
  local cands=(/dev/ttyACM* /dev/ttyUSB*)
  local real=()
  for p in "${cands[@]}"; do [ -e "$p" ] && real+=("$p"); done
  [ ${#real[@]} -eq 1 ] || die "need exactly one node on USB (found ${#real[@]}); use --port"
  PORT="${real[0]}"
}

m() {
  echo "  -> meshtastic --port $PORT $*"
  "$MESHTASTIC" --port "$PORT" "$@"
  sleep 2      # the node may commit/reboot between writes
}

# ---------------------------------------------------------------- the roles --
common() {   # $1 = long name, $2 = short name
  warn "⚠  ANTENNA BEFORE POWER — if that radio has no antenna attached,"
  warn "   unplug NOW. Transmitting into an open load can cook the PA."
  say "Radio identity: region $REGION, preset $PRESET (must match everywhere)"
  m --set lora.region "$REGION" --set lora.modem_preset "$PRESET"
  say "Primary channel $TEL_NAME (telemetry/pager)"
  m --ch-set name "$TEL_NAME" --ch-set psk "base64:$TEL_PSK" --ch-index 0
  say "Node name: '$1' [$2] (deliberate — never ship the MAC-default name)"
  m --set-owner "$1" --set-owner-short "$2"
}

role_prime() {
  common "Scottina Prime Radio" "PRIM"
  say "Command channel $CMD_NAME (slot 1 — the execution auth boundary)"
  m --ch-add "$CMD_NAME"
  m --ch-set psk "base64:$CMD_PSK" --ch-index 1
  say "WiFi off (Prime's WiFi is the web app's; this node is BLE-only)"
  m --set network.wifi_enabled false
  cat <<'EOF'

Next (docs/MICROKVM.md): bluetoothctl pair/trust this node, put its BLE
address + your phone's node ID into config.json -> microkvm, then
  sudo setup/install-microkvm.sh
Record the node ID in docs/LORAMESH.md's table.
EOF
}

role_sensor() {
  common "Scottina Sensor" "SENS"
  say "Environment telemetry (auto-detects supported I2C sensors at boot)"
  m --set telemetry.environment_measurement_enabled true \
    --set telemetry.environment_update_interval "$TELEMETRY_INTERVAL"
  if [ -n "$ADMIN_KEY" ]; then
    say "Remote admin: Prime radio's public key -> security.admin_key"
    m --set security.admin_key "base64:${ADMIN_KEY#base64:}"
  else
    warn "No --admin-key: Prime cannot govern this node over the air yet."
    warn "Get it with:  meshtastic --port <prime> --get security.public_key"
  fi
}

role_companion() {
  common "Scottina Light Companion" "LGHT"
  say "Serial module TEXTMSG (Light's CAN-trigger strings -> $TEL_NAME)"
  warn "RXD=$SERIAL_RXD TXD=$SERIAL_TXD — check the board silk; override with"
  warn "SERIAL_RXD/SERIAL_TXD env vars. Wire crossed (TX->RX), common ground."
  m --set serial.enabled true --set serial.mode TEXTMSG \
    --set serial.baud "$SERIAL_BAUD" \
    --set serial.rxd "$SERIAL_RXD" --set serial.txd "$SERIAL_TXD"
}

case "$ROLE" in
  prime|sensor|companion)
    ensure_secrets; find_port
    "role_$ROLE"
    say "Verify"
    "$MESHTASTIC" --port "$PORT" --info | sed -n '1,40p'
    echo
    echo "Check: region $REGION, preset $PRESET, channels as expected."
    echo "Record this node's ID (!hex) in docs/LORAMESH.md's table."
    ;;
  qr)
    find_port
    say "Join QR/URLs (scan with the Meshtastic app; run against the Prime"
    say "radio so the phone gets BOTH channels — it is the commander)"
    "$MESHTASTIC" --port "$PORT" --qr-all
    ;;
  verify)
    find_port
    "$MESHTASTIC" --port "$PORT" --info
    ;;
  *)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
