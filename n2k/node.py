"""GNSS source node — the ONE CAN-TX module in the tree (see n2k/__init__).

When (and only when) the user presses "Source GNSS → bus" on the NMEA2K
tile, Scottina Prime becomes a real NMEA2000 bus participant: a full ISO
11783-5 address claim (NAME below, arbitrary-address-capable), claim
defense (lower NAME wins — we defend or move), ISO-request responses for
60928, and five GNSS PGNs sourced from LIVE gpsd data — never from the
snapshot file, whose 1 Hz cadence and 5 s staleness window would stack on
gpsd's own (double-staleness, GPS.md §5):

    126992 System Time            @  1 Hz
    129025 Position, Rapid Update @ 10 Hz
    129026 COG & SOG, Rapid Update@ 10 Hz
    129029 GNSS Position Data     @  1 Hz   (fast-packet, n2k/fastpacket_tx)
    126993 Heartbeat              @ 60 s

Auto-stop on fix loss: fix below 2D or gpsd data stale > 2 s ⇒ PGN TX
ceases immediately (a proper node never sources stale position).

Resume vs full re-claim, defined: on fix loss the node goes
STOPPED_FIX — it KEEPS its claimed address, keeps answering ISO requests
for 60928 and keeps defending the claim, so fix recovery resumes PGN TX
with no new claim (quick resume). Only an explicit user stop (or process
death) surrenders the address; the next start is then a full re-claim.
Deactivation is silence — no "release" message exists in the standard;
going quiet is the correct behavior.

The preferred source address persists across boots in
/opt/kilodash/state/n2k_sa.json (atomic write) so the node re-claims the
same SA — instruments that filter by source keep working between trips.
"""

import hashlib
import json
import os
import socket
import struct
import threading
import time

from .fastpacket_tx import FastPacketTx

CAN_EFF_FLAG = 0x80000000
_FRAME_FMT = "<IB3x8s"
FRAME_SIZE = struct.calcsize(_FRAME_FMT)

PGN_ADDR_CLAIM = 60928          # 0xEE00 (PDU1)
PGN_ISO_REQUEST = 59904         # 0xEA00 (PDU1)
PGN_SYSTEM_TIME = 126992
PGN_POS_RAPID = 129025
PGN_COG_SOG = 129026
PGN_GNSS = 129029
PGN_HEARTBEAT = 126993

GLOBAL_DA = 0xFF
NULL_SA = 0xFE                  # "cannot claim" source address
SA_LIMIT = 252                  # valid claimable SAs are 0..251
CLAIM_WINDOW_S = 0.25
FIX_STALE_S = 2.0

STATE_PATH = "/opt/kilodash/state/n2k_sa.json"
DEFAULT_SA = 145                # nod to NAME Function 145 (GNSS); any 0..251

# --- NAME (ISO 11783-5) ------------------------------------------------
# Industry Group 4 (Marine), Device Class 60 (Navigation), Function 145
# (Ownship Position, GNSS), arbitrary-address-capable. Manufacturer code
# 2046 sits in the unassigned/open top of the 11-bit range — the DIY
# convention; we have no NMEA-assigned code and must not squat on one.
MFR_CODE = 2046
INDUSTRY_GROUP = 4
DEVICE_CLASS = 60
FUNCTION = 145

TX_PERIODS = {PGN_SYSTEM_TIME: 1.0, PGN_POS_RAPID: 0.1, PGN_COG_SOG: 0.1,
              PGN_GNSS: 1.0, PGN_HEARTBEAT: 60.0}
TX_PRIO = {PGN_SYSTEM_TIME: 3, PGN_POS_RAPID: 2, PGN_COG_SOG: 2,
           PGN_GNSS: 3, PGN_HEARTBEAT: 7}

OFF = "off"
CLAIMING = "claiming"
ACTIVE = "sourcing"
CANNOT_CLAIM = "cannot-claim"
STOPPED_FIX = "stopped: fix lost"


def build_name(identity):
    """64-bit NAME with our fixed fields and a 21-bit identity number."""
    n = identity & 0x1FFFFF
    n |= MFR_CODE << 21
    n |= 0 << 32                        # device instance lower
    n |= 0 << 35                        # device instance upper
    n |= FUNCTION << 40
    n |= 0 << 48                        # reserved
    n |= DEVICE_CLASS << 49
    n |= 0 << 56                        # system instance
    n |= INDUSTRY_GROUP << 60
    n |= 1 << 63                        # arbitrary address capable
    return n


def box_identity():
    """Stable 21-bit identity for this box (hashed machine-id)."""
    try:
        with open("/etc/machine-id") as f:
            seed = f.read().strip()
    except OSError:
        seed = socket.gethostname()
    digest = hashlib.sha256(seed.encode()).digest()
    return int.from_bytes(digest[:3], "big") & 0x1FFFFF


def can_id(prio, pgn, sa, da=GLOBAL_DA):
    """29-bit arbitration id; PDU1 PGNs carry the DA in the PS byte."""
    if (pgn >> 8) & 0xFF < 240:
        pgn = pgn | (da & 0xFF)
    return (prio & 0x7) << 26 | pgn << 8 | (sa & 0xFF)


def split_id(cid):
    src = cid & 0xFF
    pgn = (cid >> 8) & 0x3FFFF
    da = GLOBAL_DA
    if (pgn >> 8) & 0xFF < 240:
        da = pgn & 0xFF
        pgn &= 0x3FF00
    return pgn, src, da


def pack_frame(cid, data):
    return struct.pack(_FRAME_FMT, cid | CAN_EFF_FLAG, len(data),
                       data.ljust(8, b"\xFF")[:8])


# ------------------------------------------------------------- encoders --
_EPOCH_DAY = 86400


def _days_time(epoch):
    days = int(epoch // _EPOCH_DAY)
    tod = int(round((epoch - days * _EPOCH_DAY) / 1e-4))
    return days, tod


def encode_system_time(sid, epoch):
    """126992: SID, source (0 = GPS) + reserved nibble, date u16 (days
    since 1970), time u32 (1e-4 s since midnight UTC)."""
    days, tod = _days_time(epoch)
    return struct.pack("<BBHI", sid, 0xF0 | 0, days, tod)


def encode_position_rapid(lat, lon):
    """129025: lat/lon as i32 × 1e-7 deg."""
    return struct.pack("<ii", round(lat * 1e7), round(lon * 1e7))


def encode_cog_sog(sid, cog_deg, sog_mps):
    """129026: SID, COG reference (0 = true) + reserved, COG u16 1e-4 rad,
    SOG u16 0.01 m/s, 2 reserved bytes. None → not-available sentinels."""
    import math
    cog = 0xFFFF if cog_deg is None \
        else round(math.radians(cog_deg % 360.0) / 1e-4) & 0xFFFF
    sog = 0xFFFF if sog_mps is None else min(0xFFFC, round(sog_mps / 0.01))
    return struct.pack("<BBHH2s", sid, 0xFC | 0, cog, sog, b"\xFF\xFF")


def encode_gnss(sid, epoch, lat, lon, alt_m, method, sats, hdop, pdop,
                geoidal_sep_m):
    """129029 (fast-packet, 43 bytes with zero reference stations): SID,
    date u16, time u32 1e-4 s, lat/lon i64 × 1e-16 deg, alt i64 × 1e-6 m,
    GNSS type (0 = GPS) + method nibbles, integrity + reserved, sats u8,
    HDOP/PDOP i16 × 0.01, geoidal separation i32 × 0.01 m, ref stations."""
    days, tod = _days_time(epoch)
    def _i16(v):
        return 0x7FFF if v is None else round(v / 0.01)
    return struct.pack(
        "<BHIqqqBBBhhiB",
        sid, days, tod,
        round(lat * 1e16), round(lon * 1e16),
        0x7FFFFFFFFFFFFFFF if alt_m is None else round(alt_m * 1e6),
        (method & 0xF) << 4 | 0,        # type 0 = GPS, method in high nibble
        0xFC | 0,                       # integrity 0 (none) + reserved
        0xFF if sats is None else sats,
        _i16(hdop), _i16(pdop),
        0x7FFFFFFF if geoidal_sep_m is None
        else round(geoidal_sep_m / 0.01),
        0)                              # reference station count


def encode_heartbeat(seq, interval_s=60.0):
    """126993: interval u16 × 0.01 s, sequence u8, controller-state bits
    left not-available."""
    return struct.pack("<HB5s", round(interval_s / 0.01), seq & 0xFF,
                       b"\xFF" * 5)


# ------------------------------------------------------------ state file --
def load_preferred_sa(path=STATE_PATH):
    try:
        with open(path) as f:
            sa = json.load(f).get("sa")
        if isinstance(sa, int) and 0 <= sa < SA_LIMIT:
            return sa
    except (OSError, ValueError):
        pass
    return DEFAULT_SA


def save_preferred_sa(sa, path=STATE_PATH):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump({"sa": sa, "identity": box_identity()}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass                            # persistence is best-effort


# ------------------------------------------------------------------ core --
class NodeCore:
    """The address-claim state machine + PGN scheduler, socket-free and
    clock-injected for tests. `tx(cid, data)` is the only way out."""

    def __init__(self, tx, identity=None, preferred_sa=None,
                 now=time.monotonic):
        self.tx = tx
        self.now = now
        self.name = build_name(box_identity() if identity is None
                               else identity)
        self.sa = load_preferred_sa() if preferred_sa is None else preferred_sa
        self.state = OFF
        self._claim_deadline = 0.0
        self._tried = set()
        self._last_tx = {}
        self._sid = 0
        self._hb_seq = 0
        self._fp = FastPacketTx()

    # ---- claim machinery ----
    def _send_claim(self, sa=None):
        self.tx(can_id(6, PGN_ADDR_CLAIM, self.sa if sa is None else sa),
                self.name.to_bytes(8, "little"))

    def activate(self):
        """Button-on: full claim with a 250 ms contention window."""
        self._tried = {self.sa}
        self.state = CLAIMING
        self._claim_deadline = self.now() + CLAIM_WINDOW_S
        self._last_tx = {}
        self._send_claim()

    def deactivate(self):
        """Button-off (or unplug): cease PGN TX immediately and go silent —
        no release message exists in the standard."""
        self.state = OFF

    def _move_or_die(self):
        for cand in list(range(self.sa + 1, SA_LIMIT)) + \
                list(range(0, self.sa)):
            if cand not in self._tried:
                self.sa = cand
                self._tried.add(cand)
                self.state = CLAIMING
                self._claim_deadline = self.now() + CLAIM_WINDOW_S
                self._send_claim()
                return
        # address space exhausted: announce cannot-claim from the null SA
        self._send_claim(sa=NULL_SA)
        self.state = CANNOT_CLAIM

    def on_frame(self, cid, data):
        """Feed every RX frame here while the node is not OFF."""
        if self.state in (OFF, CANNOT_CLAIM):
            return
        pgn, src, da = split_id(cid)
        if pgn == PGN_ADDR_CLAIM and src == self.sa and len(data) >= 8:
            their_name = int.from_bytes(data[:8], "little")
            if their_name == self.name:
                return                  # our own echo
            if their_name < self.name:
                self._move_or_die()     # they win — compute next SA
            else:
                self._send_claim()      # we win — defend by re-claiming
        elif pgn == PGN_ISO_REQUEST and da in (self.sa, GLOBAL_DA) \
                and len(data) >= 3:
            requested = int.from_bytes(data[:3], "little")
            if requested == PGN_ADDR_CLAIM:
                self._send_claim()      # answerable at any time while active

    def poll(self):
        """Advance the claim window; returns True on a state change."""
        if self.state == CLAIMING and self.now() >= self._claim_deadline:
            self.state = ACTIVE
            save_preferred_sa(self.sa)
            return True
        return False

    # ---- fix gating ----
    def fix_lost(self):
        if self.state == ACTIVE:
            self.state = STOPPED_FIX

    def fix_restored(self):
        if self.state == STOPPED_FIX:
            self.state = ACTIVE         # quick resume: address was kept

    # ---- PGN scheduler ----
    def tx_due(self, gnss):
        """Send every PGN whose period elapsed. `gnss` is a dict with
        epoch, lat, lon, sog_mps, cog_deg, alt_m, method, sats, hdop,
        pdop, geoidal_sep_m (Nones allowed where the PGN has sentinels).
        Only runs while ACTIVE — callers gate on fix themselves via
        fix_lost()/fix_restored()."""
        if self.state != ACTIVE:
            return
        now = self.now()
        for pgn, period in TX_PERIODS.items():
            last = self._last_tx.get(pgn)
            if last is not None and now - last < period:
                continue
            # anchor to last+period (drift-free cadence); re-anchor to now
            # after a gap so a stall doesn't burst-replay missed slots
            self._last_tx[pgn] = now if last is None \
                or now - (last + period) >= period else last + period
            prio = TX_PRIO[pgn]
            if pgn == PGN_SYSTEM_TIME:
                self._sid = (self._sid + 1) % 251
                self.tx(can_id(prio, pgn, self.sa),
                        encode_system_time(self._sid, gnss["epoch"]))
            elif pgn == PGN_POS_RAPID:
                self.tx(can_id(prio, pgn, self.sa),
                        encode_position_rapid(gnss["lat"], gnss["lon"]))
            elif pgn == PGN_COG_SOG:
                self.tx(can_id(prio, pgn, self.sa),
                        encode_cog_sog(self._sid, gnss.get("cog_deg"),
                                       gnss.get("sog_mps")))
            elif pgn == PGN_GNSS:
                payload = encode_gnss(
                    self._sid, gnss["epoch"], gnss["lat"], gnss["lon"],
                    gnss.get("alt_m"), gnss.get("method", 1),
                    gnss.get("sats"), gnss.get("hdop"), gnss.get("pdop"),
                    gnss.get("geoidal_sep_m"))
                for frame in self._fp.frames(pgn, payload):
                    self.tx(can_id(prio, pgn, self.sa), frame)
            elif pgn == PGN_HEARTBEAT:
                self._hb_seq = (self._hb_seq + 1) & 0xFF
                self.tx(can_id(prio, pgn, self.sa),
                        encode_heartbeat(self._hb_seq))


# --------------------------------------------------------------- runtime --
class GnssSourceNode:
    """Socket + thread wrapper: one RAW CAN socket (RX for claim defense /
    ISO requests, TX for the carve-out), one gpsd listener, one loop."""

    def __init__(self, iface, listener=None):
        self.iface = iface
        self.core = None
        self.error = None
        self._own_listener = listener is None
        if listener is None:
            from gps.gpsdio import GpsdListener
            listener = GpsdListener().start()
        self.listener = listener
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    # ---- tile-facing status ----
    @property
    def state(self):
        return self.core.state if self.core else OFF

    @property
    def sa(self):
        return self.core.sa if self.core else None

    def start(self):
        self._t.start()
        return self

    def stop(self):
        """Explicit user stop: cease TX immediately, surrender the SA
        (next start is a full re-claim)."""
        self._stop.set()
        self._t.join(timeout=1.5)
        if self._own_listener:
            self.listener.stop()

    # ---- gpsd → PGN data ----
    def _gnss_data(self, st):
        from gps.snapshot import parse_ts
        tpv = st["tpv"] or {}
        sky = st["sky"] or {}
        mode = tpv.get("mode", 0)
        fresh = st["tpv_age"] is not None and st["tpv_age"] <= FIX_STALE_S
        if not fresh or mode < 2 or tpv.get("lat") is None:
            return None
        epoch = parse_ts(tpv.get("time")) or time.time()
        sats = sky.get("satellites") or []
        return {
            "epoch": epoch, "lat": tpv["lat"], "lon": tpv["lon"],
            "sog_mps": tpv.get("speed"), "cog_deg": tpv.get("track"),
            "alt_m": tpv.get("altMSL", tpv.get("alt")) if mode == 3 else None,
            "method": 2 if tpv.get("status") == 2 else 1,
            "sats": sum(1 for s in sats if s.get("used")) or None,
            "hdop": sky.get("hdop"), "pdop": sky.get("pdop"),
            "geoidal_sep_m": tpv.get("geoidSep"),
        }

    def _run(self):
        try:
            s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            s.settimeout(0.02)
            s.bind((self.iface,))
        except (AttributeError, OSError) as e:
            self.error = f"{self.iface}: {e}"
            return
        self.core = NodeCore(
            tx=lambda cid, data: s.send(pack_frame(cid, data)))
        self.core.activate()
        try:
            while not self._stop.is_set():
                try:
                    buf = s.recv(FRAME_SIZE)
                    raw_id, dlc, data = struct.unpack(_FRAME_FMT,
                                                      buf[:FRAME_SIZE])
                    if raw_id & CAN_EFF_FLAG and not raw_id & 0x60000000:
                        self.core.on_frame(raw_id & 0x1FFFFFFF, data[:dlc])
                except socket.timeout:
                    pass
                except OSError as e:
                    self.error = f"{self.iface}: {e.strerror or e}"
                    return
                self.core.poll()
                gnss = self._gnss_data(self.listener.state())
                if gnss is None:
                    self.core.fix_lost()
                else:
                    self.core.fix_restored()
                    try:
                        self.core.tx_due(gnss)
                    except OSError as e:
                        self.error = f"{self.iface}: {e.strerror or e}"
                        return
        finally:
            if self.core:
                self.core.deactivate()
            s.close()
