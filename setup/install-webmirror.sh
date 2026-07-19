#!/usr/bin/env bash
# Scottina web mirror — LAN web front-end mirroring the touchscreen.
#
# Installs: python3-flask (if missing), the systemd unit, and the
# RuntimeDirectory drop-in that gives kilodash.service /run/kilodash for the
# event socket (WEB-PROTOCOL.md §1).
#
# Idempotent: safe to re-run.
#   sudo setup/install-webmirror.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

say "Dependencies"
# Distro packages, not pip — same discipline as install-tables.sh. SSE needs
# nothing beyond Flask itself (see WEB-PROTOCOL.md §1 on why not WebSocket).
apt-get install -y python3-flask

say "Runtime directory for the event socket"
# kilodash creates /run/kilodash itself if missing, but RuntimeDirectory= makes
# systemd own it: correct mode on every boot, and cleaned up on stop. /run is
# used rather than /tmp because /tmp is world-writable (§1).
DROPIN=/etc/systemd/system/kilodash.service.d
install -d -m755 "$DROPIN"
cat > "$DROPIN/runtime-dir.conf" <<'EOF'
# Added by setup/install-webmirror.sh — the web mirror's event socket lives
# at /run/kilodash/events.sock (WEB-PROTOCOL.md §1).
[Service]
RuntimeDirectory=kilodash
RuntimeDirectoryMode=0750
EOF

say "systemd unit"
install -m644 "$SCRIPT_DIR/kilodash-webmirror.service" /etc/systemd/system/
systemctl daemon-reload

say "Port check"
# Port 80 is the target. If something already holds it, say so plainly rather
# than letting the unit flap in a restart loop.
if ss -ltn 2>/dev/null | grep -qE ':80\s'; then
  echo "  WARNING: something is already listening on port 80."
  echo "  The mirror will fail to bind. Free it, or run with"
  echo "  KILODASH_WEB_PORT=8080 in the unit's environment."
else
  echo "  port 80 is free"
fi

say "Enable"
systemctl enable kilodash-webmirror.service
systemctl restart kilodash          # picks up RuntimeDirectory=
systemctl restart kilodash-webmirror.service

sleep 2
if systemctl is-active --quiet kilodash-webmirror.service; then
  ADDR="$(python3 -c 'import sys; sys.path.insert(0,"'"$REPO_DIR"'"); from kilodash import net; print(net.advertise_addr())' 2>/dev/null || echo '<ip>')"
  say "Done — http://${ADDR}/  (also http://scottina.local/)"
  echo "  LAN-only by design: no auth in v1, because it never leaves the"
  echo "  local network. WAN exposure is out of scope (WEB-PROTOCOL.md §10)."
  echo "  Logs: journalctl -u kilodash-webmirror -f"
else
  say "Unit is not active — check: journalctl -u kilodash-webmirror -n 40"
  exit 1
fi
