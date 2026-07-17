"""Micro KVM — off-grid command plane over Meshtastic (MICROKVM-PROTOCOL.md).

No video, no interactive I/O: one text frame in, one text frame back, over a
~kbps duty-limited LoRa link. The safety boundary is what can execute on
Prime: a positive allow-list of named diagnostic verbs (registry.py), an
independent reject pass (executor.py), and an arm gate that keeps the whole
plane dormant while Prime's home network is reachable (armgate.py).

Core modules (registry, executor, armgate, link) are stdlib-only and never
import kilodash; service.py is the glue that bridges the two inside the app
process. meshtastic-python is imported lazily in link.py only.
"""

# Defaults for the "microkvm" block in kilodash's config.json (mirrors the
# cantick.CONFIG_DEFAULTS pattern). Operator-editable, not secret-bearing:
# the command-channel PSK lives in the Meshtastic node, never here.
CONFIG_DEFAULTS = {
    "enabled": False,
    # Positive home identity (both checked when both set; see armgate.py).
    # Empty home_host => plane is permanently disarmed (bench-safe default).
    "home_ssid": "",
    "home_host": "",              # home gateway/host IP or name
    # BLE address of the Prime radio T3 (empty = first paired Meshtastic node)
    "ble_address": "",
    "command_channel": "ScotCmd",  # must match docs/LORAMESH.md
    # Sender node IDs allowed to command (e.g. "!a1b2c3d4"). Node IDs are
    # spoofable — the channel PSK is the crypto barrier; this narrows within
    # the trusted channel (MICROKVM-PROTOCOL.md §6).
    "allowed_nodes": [],
    "check_interval_sec": 15,     # arm-gate probe cadence
    "debounce_checks": 4,         # consecutive agreeing probes to flip state
}
