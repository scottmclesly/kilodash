#!/usr/bin/env bash
#
# Scottina Micro KVM installer — the off-grid command plane's Prime-side deps
# (MICROKVM-PROTOCOL.md; mesh provisioning lives in docs/LORAMESH.md).
#
# Installs meshtastic-python (BLE backend, for the kilodash process itself —
# the pipx CLI used for node provisioning is separate), the BlueZ stack, and
# the microkvm config scaffolding in config.json. No secrets: the command
# channel PSK lives in the Meshtastic node, never on this side. Idempotent:
# safe to re-run. Run as root:
#
#     sudo setup/install-microkvm.sh
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
say "APT dependencies (BlueZ + can-utils for the cap verb)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y bluez python3-pip can-utils
systemctl enable --now bluetooth

# ------------------------------------------------------- meshtastic-python ---
say "meshtastic-python (BLE backend) for the kilodash process"
# The app imports meshtastic lazily (microkvm/link.py); it must live in the
# system interpreter that runs kilodash.service, hence not pipx here.
pip3 install --break-system-packages --upgrade meshtastic

# ------------------------------------------------------ config scaffolding ---
say "microkvm config block"
python3 - "$REPO_DIR" <<'EOF'
import json, sys, os
sys.path.insert(0, sys.argv[1])
from microkvm import CONFIG_DEFAULTS
path = os.path.join(sys.argv[1], "config.json")
try:
    with open(path) as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = {}
block = dict(CONFIG_DEFAULTS)
block.update(cfg.get("microkvm") or {})
cfg["microkvm"] = block
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(tmp, path)
print(f"config block at {path}:")
print(json.dumps(block, indent=2))
EOF

say "Done — now hand-edit config.json before enabling"
cat <<'EOF'
Required before the plane can ever arm (it stays dormant otherwise):
  microkvm.enabled        true
  microkvm.home_host      your home gateway/host IP (positive home identity)
  microkvm.home_ssid      home WiFi SSID (optional second factor)
  microkvm.ble_address    Prime radio T3 BLE address (bluetoothctl devices)
  microkvm.allowed_nodes  ["!<nodeid>", ...] senders allowed to command

Provision the mesh nodes first: docs/LORAMESH.md + tools/provision_mesh.sh.
Then:  systemctl restart kilodash
EOF
