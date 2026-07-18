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
#   tools/provision_mesh.sh prime     [--port /dev/ttyACM0 | --ble AA:BB:..]
#   tools/provision_mesh.sh sensor    [--port ... | --ble ...] [--admin-key <base64>]
#   tools/provision_mesh.sh companion [--port ... | --ble ...]
#   tools/provision_mesh.sh qr        [--port ... | --ble ...]  # join-QR for the phone
#   tools/provision_mesh.sh verify    [--port ... | --ble ...]  # read back --info
#
# PSKs: generated once into /opt/kilodash/mesh-secrets.env (git-ignored,
# per-boat, mode 600). The ScotCmd PSK is the command plane's auth boundary
# (MICROKVM-PROTOCOL.md §6) — it never goes to sensor/companion nodes.
#
# BLE mode (bench facts, 2026-07-16, T3 #1 bring-up): pair + trust in
# bluetoothctl FIRST (random PIN shows on the node's OLED the moment pairing
# starts — ~30 s window). The CLI over BLE hangs on exit AND HOLDS the node's
# single BLE connection, so every call here is capped, killed, and explicitly
# disconnected; success is judged by the radio's "Writing modified..." output,
# never the exit code. Stop kilodash first if it owns the link
# (systemctl stop kilodash), and restart it after.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="$REPO_DIR/mesh-secrets.env"

# ---- the pinned radio config (docs/LORAMESH.md — change it THERE first) ----
REGION="EU_433"   # 433 MHz T3 variants — Meshtastic's 433 band plan
PRESET="LONG_SLOW"
TEL_NAME="ScotTel"          # slot 0 (primary): telemetry + pager
CMD_NAME="ScotCmd"          # slot 1: command plane (prime + phone ONLY)
TELEMETRY_INTERVAL=1800     # 30 min — airtime is shared and duty-limited
# Commander quick-chat: pre-loaded command frames the Meshtastic app shows as
# tappable chips, so the operator PICKS a command instead of typing syntax
# (MICROKVM-PROTOCOL.md). Pipe-separated, ≤200 chars. Action verbs are here
# too — they reply "reject disarmed" at home, which is itself informative.
COMMANDER_CANNED="help|status|health|snap temp|tile pihealth|tile home|reboot"
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
BLE=""
ADMIN_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port)      PORT="$2"; shift 2 ;;
    --ble)       BLE="$2"; shift 2 ;;
    --admin-key) ADMIN_KEY="$2"; shift 2 ;;
    *) die "unknown arg: $1" ;;
  esac
done

find_conn() {
  [ -n "$BLE" ] && return
  [ -n "$PORT" ] && return
  local cands=(/dev/ttyACM* /dev/ttyUSB*)
  local real=()
  for p in "${cands[@]}"; do [ -e "$p" ] && real+=("$p"); done
  [ ${#real[@]} -eq 1 ] \
    || die "need exactly one node on USB (found ${#real[@]}); use --port or --ble"
  PORT="${real[0]}"
}

_ble_cleanup() {
  pkill -f "$(basename "$MESHTASTIC")" 2>/dev/null || true
  sleep 1
  bluetoothctl disconnect "$BLE" >/dev/null 2>&1 || true
  sleep 4
}

m() {
  if [ -z "$BLE" ]; then
    echo "  -> $(basename "$MESHTASTIC") --port $PORT $*"
    "$MESHTASTIC" --port "$PORT" "$@"
    sleep 2      # the node may commit/reboot between writes
    return
  fi
  # BLE path: capped, killed, disconnected; output-judged; 3 attempts.
  local out="${TMPDIR:-/tmp}/mesh-step.$$"
  for att in 1 2 3; do
    echo "  -> (ble $BLE, attempt $att) $*"
    timeout 90 "$MESHTASTIC" --ble "$BLE" "$@" >"$out" 2>&1 || true
    _ble_cleanup
    if grep -qE "Writing modified|Set |Setting canned|Complete URL|Owner|myNodeNum" "$out"; then
      grep -E "Writing modified|Set |Setting canned|Owner" "$out" | head -6 || true
      rm -f "$out"; return 0
    fi
    echo "     (no success marker — radio busy/asleep? retrying)"
    tail -2 "$out"; sleep 10
  done
  rm -f "$out"
  die "radio never confirmed the write over BLE (is kilodash holding the link? systemctl stop kilodash)"
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
  # default to the Prime radio's recorded pubkey (mesh-secrets.env)
  ADMIN_KEY="${ADMIN_KEY:-${PRIME_PUBKEY:-}}"
  if [ -n "$ADMIN_KEY" ]; then
    say "Remote admin: Prime radio's public key -> security.admin_key"
    m --set security.admin_key "base64:${ADMIN_KEY#base64:}"
  else
    warn "No --admin-key: Prime cannot govern this node over the air yet."
    warn "Get it with:  meshtastic --port <prime> --get security.public_key"
  fi
  # Commander duty: Kate doubles as the phone's pager/commander until a
  # dedicated commander radio exists, so load the quick-chat command set.
  say "Commander quick-chat (tappable command chips in the app)"
  m --set canned_message.enabled true
  m --set-canned-message "$COMMANDER_CANNED"
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

# full-output runner for read-back commands (verify / qr)
m_show() {
  if [ -z "$BLE" ]; then
    "$MESHTASTIC" --port "$PORT" "$@"
    return
  fi
  local out="${TMPDIR:-/tmp}/mesh-show.$$"
  timeout 90 "$MESHTASTIC" --ble "$BLE" "$@" >"$out" 2>&1 || true
  _ble_cleanup
  cat "$out"; rm -f "$out"
}

case "$ROLE" in
  prime|sensor|companion)
    ensure_secrets; find_conn
    "role_$ROLE"
    say "Verify"
    m_show --info | sed -n '1,40p'
    echo
    echo "Check: region $REGION, preset $PRESET, channels as expected."
    echo "Record this node's ID (!hex) in docs/LORAMESH.md's table."
    ;;
  qr)
    find_conn
    say "Join QR/URLs (scan with the Meshtastic app; run against the Prime"
    say "radio so the phone gets BOTH channels — it is the commander)"
    m_show --qr-all
    ;;
  verify)
    find_conn
    m_show --info
    ;;
  *)
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
