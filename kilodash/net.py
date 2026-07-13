"""Shared network-address helpers.

advertise_addr() is the ONE place that decides which of the Pi's addresses
gets shown/QR-coded to another device (the Tables converter URL, and the
CanTick work needs the same logic — do not duplicate it): **eth0's IP if
eth0 is up and addressed, else wlan0's**, else whatever else has an
address.

NOTE (dual-NIC): this preference *sidesteps*, not solves, the asymmetric-
routing issue — with both NICs up, a client that resolves the *other*
interface (e.g. via mDNS) can still see SYN in eth0 / RST out wlan0. The
real routing fix (policy routing / per-iface rp_filter) stays on the
horizon list; when it lands, this helper is the consumer.
"""

from . import system

PREFERRED = ("eth0", "wlan0")

# The Tables converter service port (kilodash/tableconv.py). Lives here so
# the tile and the screens can know it without importing Flask.
TABLECONV_PORT = 8735


def advertise_addr():
    """Best IP to advertise for LAN-facing services, eth0-preferred."""
    ifaces = {it["name"]: it for it in system.get_interfaces()
              if it["ip"] not in ("", "--")}
    for name in PREFERRED:
        it = ifaces.get(name)
        if it and it["state"].lower() != "down":
            return it["ip"]
    for it in ifaces.values():
        if it["state"].lower() != "down":
            return it["ip"]
    return "127.0.0.1"
