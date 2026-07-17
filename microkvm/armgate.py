"""Arm gate — "is Prime on its home network?" (MICROKVM-PROTOCOL.md §3).

armed = home unreachable. Home is identified POSITIVELY, not as "some network
exists": the configured home gateway/host must answer, and — if a home SSID
is configured — the current SSID must match too. Reachability is the anchor
because SSIDs lie: a captive portal or look-alike SSID gives you the name
without the network, and that must read as *armed* (off-grid is when this
plane is your only reach — a false "home" strands you; a false "away" merely
arms a dormant-anyway plane at the bench... except it can't: an unconfigured
home identity pins the gate to disarmed forever).

Debounced: a state flip needs `need` consecutive probes that agree with each
other and disagree with the current state, so a flapping link doesn't thrash
arm↔disarm. Every transition is logged.

Stdlib-only; the SSID and reachability probes are injectable for tests and
default to nmcli / ping (both argv list[str], no shell).
"""

import logging
import subprocess
import time

log = logging.getLogger("microkvm.armgate")


def _default_ssid():
    """SSID currently associated, or ''. nmcli exits 0 with empty output when
    not associated; any failure reads as 'no SSID'."""
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device",
                            "wifi"], capture_output=True, text=True, timeout=8)
        for line in r.stdout.splitlines():
            active, _, ssid = line.partition(":")
            if active == "yes" and ssid:
                return ssid
    except Exception:
        pass
    return ""


def _default_reach(host):
    """One ICMP echo to the home gateway/host. rc 0 = reachable."""
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "2", host],
                           capture_output=True, timeout=6)
        return r.returncode == 0
    except Exception:
        return False


class ArmGate:
    def __init__(self, home_ssid="", home_host="", need=4,
                 ssid_fn=None, reach_fn=None):
        self.home_ssid = home_ssid or ""
        self.home_host = home_host or ""
        self.need = max(1, int(need))
        self._ssid = ssid_fn or _default_ssid
        self._reach = reach_fn or _default_reach
        # Start DISARMED: the plane must never boot armed on a bench, and if
        # we really are off-grid the first `need` agreeing probes arm it.
        self.armed = False
        self.reason = "starting up"
        self._pending = 0            # consecutive probes disagreeing with state
        self.transitions = []        # [(ts, armed, reason)] for the tile/log
        if not self.home_host:
            self.reason = "home identity unconfigured"

    # ---------------------------------------------------------------- probes --
    def _probe(self):
        """One un-debounced answer: (home: bool, reason)."""
        if not self.home_host:
            # No positive identity to test against => never armed (§3).
            return True, "home identity unconfigured"
        if not self._reach(self.home_host):
            return False, f"home host {self.home_host} unreachable"
        if self.home_ssid:
            ssid = self._ssid()
            if ssid != self.home_ssid:
                # Gateway answered but the SSID is wrong — wired home uplink
                # also lands here with ssid=''. Reachability is the anchor:
                # a reachable home host means home unless SSID says we are on
                # a foreign net that happens to route to it; treat as home
                # only when both agree.
                return False, f"ssid '{ssid or '-'}' is not home"
        return True, "home network reachable"

    def poll(self):
        """Run one debounced check; call every check_interval_sec. Returns
        the current (possibly just-flipped) armed state."""
        home, reason = self._probe()
        want_armed = not home
        if want_armed == self.armed:
            self._pending = 0
            if not self.armed:
                self.reason = reason
            return self.armed
        self._pending += 1
        if self._pending < self.need:
            return self.armed
        # need consecutive agreeing probes reached: flip
        self._pending = 0
        self.armed = want_armed
        self.reason = reason
        self.transitions.append((time.time(), self.armed, reason))
        log.warning("arm state -> %s (%s)",
                    "ARMED" if self.armed else "DORMANT", reason)
        return self.armed

    # ------------------------------------------------------------- executor --
    def state(self):
        """(armed, reason) — the executor's armed_fn."""
        return self.armed, self.reason
