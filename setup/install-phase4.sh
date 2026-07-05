#!/usr/bin/env bash
#
# kilodash Phase 4 installer — the web-app launch-terminal backends.
#
# Installs Node-RED, AIS-catcher (RX), and Signal K, plus their systemd units,
# reproducing the steps that were first done by hand. Idempotent: safe to re-run
# (it skips anything already present). Run as root:
#
#     sudo setup/install-phase4.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root:  sudo $0" >&2
  exit 1
fi

# sudo's secure_path usually omits /usr/local/bin, where npm/make install these
# binaries — without this the "already installed?" checks below all miss and the
# script needlessly reinstalls (and briefly wipes the Signal K native addon).
export PATH="/usr/local/bin:/usr/local/sbin:$PATH"

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# ---------------------------------------------------------------- apt deps ---
say "APT build + runtime dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y \
  nodejs npm build-essential cmake git pkg-config \
  librtlsdr-dev libairspy-dev libairspyhf-dev libhackrf-dev \
  libzmq3-dev libsoapysdr-dev

# ---------------------------------------------------------------- Node-RED ---
say "Node-RED"
if command -v node-red >/dev/null 2>&1; then
  echo "already installed: $(command -v node-red)"
else
  npm install -g --unsafe-perm node-red
fi
install -m644 "$SCRIPT_DIR/nodered.service" /etc/systemd/system/nodered.service
echo "flow + guide: import setup/nodered-kilodash-flow.json (see setup/NODE-RED.md)"

# ------------------------------------------------------------- AIS-catcher ---
# Not packaged anywhere; build the maintainer's source. RX-only on the RTL-SDR.
say "AIS-catcher (build from source)"
if command -v AIS-catcher >/dev/null 2>&1; then
  echo "already installed: $(AIS-catcher -h 2>&1 | head -1)"
else
  build="$(mktemp -d)"
  git clone --depth 1 https://github.com/jvde-github/AIS-catcher.git "$build/src"
  cmake -S "$build/src" -B "$build/src/build"
  make -C "$build/src/build" -j"$(nproc)"
  make -C "$build/src/build" install
  rm -rf "$build"
  echo "installed: $(command -v AIS-catcher)"
fi

# ---------------------------------------------------------------- Signal K ---
say "Signal K"
if command -v signalk-server >/dev/null 2>&1; then
  echo "already installed: $(command -v signalk-server)"
else
  npm install -g --unsafe-perm signalk-server
fi

# npm 11 blocks package install scripts; build the native CAN addon directly
# (enables NMEA2000-over-SocketCAN in SK), and opt out of scarf telemetry.
sk_dir="$(npm root -g)/signalk-server"
canboat="$sk_dir/node_modules/@canboat/canboatjs"
if [ -f "$canboat/binding.gyp" ] && [ ! -f "$canboat/build/Release/canSocket.node" ]; then
  echo "building canboatjs native CAN addon…"
  ( cd "$canboat" && HOME=/root npx --yes node-gyp rebuild )
fi
# Cover every flagged install script so npm stops nagging on future runs. The
# two that matter are already handled (serialport ships a prebuilt binding;
# canboat is built above), @scarf/scarf is install telemetry we don't want, and
# the rest are cosmetic postinstalls — so denying (not running) them is correct.
# npm's `approve-scripts` no-ops on global installs (no lockfile to pin), so a
# fresh `npm install -g signalk-server` resets this; re-deny every run.
if [ -d "$sk_dir" ]; then
  ( cd "$sk_dir" && npm deny-scripts \
      @scarf/scarf core-js es5-ext storage-engine \
      @serialport/bindings-cpp @canboat/canboatjs >/dev/null 2>&1 || true )
fi
install -m644 "$SCRIPT_DIR/signalk.service" /etc/systemd/system/signalk.service

# ----------------------------------------------------------------- systemd ---
say "systemd"
systemctl daemon-reload
echo "Units installed (left disabled — kilodash launches them on demand):"
echo "    nodered.service   signalk.service"
echo "For an always-on hub (e.g. Signal K on a boat):  systemctl enable --now signalk"

say "Done — restart kilodash to load the new screens"
echo "    systemctl restart kilodash"
