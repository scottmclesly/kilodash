"""BLE bridge to the Prime radio T3 (MICROKVM-PROTOCOL.md §0, Phase 3).

Receive loop: inbound text frames on the command channel, from allow-listed
sender node IDs only, feed the executor; the one reply goes back as a DM to
the sender (delivery-ack for free). BLE on purpose — Prime's WiFi is reserved
for the web app; moving this link to WiFi would silently re-couple the
command plane to the web app's interface.

Sender gating (§6): the channel PSK is the cryptographic boundary; the
node-ID allow-list narrows *within* the trusted channel. Node IDs are
spoofable and are NOT auth — an unknown node on the channel is logged and
ignored, never dispatched, never answered.

meshtastic-python (bleak backend) is imported lazily inside connect() so the
rest of the plane — and its tests — need no radio stack. Frame filtering is
a pure function (`filter_frame`) so it is unit-testable without one either.
"""

import logging
import threading
import time

log = logging.getLogger("microkvm.link")

RECONNECT_MIN_S = 5          # backoff floor after a BLE drop
RECONNECT_MAX_S = 120        # ...and ceiling (duty discipline: no storms)
TEXT_PORTNUM = "TEXT_MESSAGE_APP"


def filter_frame(packet, command_index, allowed_nodes):
    """Decide whether an rx packet is a command frame we may dispatch.

    Returns (sender_id, text) to dispatch, or (None, reason) to drop. Pure —
    no radio objects — so the gating rules are provable in unit tests.
    """
    packet = packet or {}
    decoded = packet.get("decoded") or {}
    if decoded.get("portnum") != TEXT_PORTNUM:
        return None, "not a text frame"
    if packet.get("channel", 0) != command_index:
        return None, "not the command channel"
    sender = packet.get("fromId") or ""
    if not sender:
        return None, "no sender id"
    if sender not in allowed_nodes:
        # silence by contract (§2): logged, never dispatched, never answered
        return None, f"node {sender} not on allow-list"
    text = decoded.get("text") or ""
    if not text.strip():
        return None, "empty frame"
    return sender, text


class MeshLink:
    """Owns the BLE connection lifecycle and the rx→executor→reply loop."""

    def __init__(self, executor, ble_address="", channel_name="ScotCmd",
                 allowed_nodes=(), on_change=None):
        self.executor = executor
        self.ble_address = ble_address or None   # None = first paired node
        self.channel_name = channel_name
        self.allowed_nodes = set(allowed_nodes)
        self.on_change = on_change or (lambda: None)
        self.state = "down"                       # down | connecting | up
        self.detail = "not started"
        self.last_heard = ""                      # last allow-listed sender
        self.last_rssi = None
        self.last_snr = None
        self.dropped = 0                          # frames refused by gating
        self._iface = None
        self._command_index = None
        self._stop = threading.Event()
        self._thread = None

    # ---------------------------------------------------------- executor fn --
    def link_info(self):
        """Injected into Executor as link_fn (status verb's rssi field)."""
        if self.last_rssi is None:
            return None
        return {"rssi": self.last_rssi, "snr": self.last_snr}

    # ----------------------------------------------------------- lifecycle --
    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="microkvm-ble")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def _set_state(self, state, detail):
        self.state, self.detail = state, detail
        log.info("link %s (%s)", state, detail)
        self.on_change()

    def _close(self):
        iface, self._iface = self._iface, None
        if iface:
            try:
                iface.close()
            except Exception:
                pass

    def _advertising(self, timeout=10):
        """True if the node is currently advertising. A Meshtastic node with
        its single BLE slot taken (e.g. a phone connected to it) does NOT
        advertise — and a GATT connect attempted in that state hangs forever
        rather than failing (bench fact, 2026-07-16), wedging this thread
        inside a dead attempt. So: never connect blind; scans time out."""
        if not self.ble_address:
            return True                # scan-any mode: let connect discover
        try:
            import asyncio
            from bleak import BleakScanner

            async def scan():
                return await BleakScanner.find_device_by_address(
                    self.ble_address, timeout=timeout)
            return asyncio.run(scan()) is not None
        except Exception:               # noqa: BLE001 — treat as not seen
            return False

    def _loop(self):
        backoff = RECONNECT_MIN_S
        while not self._stop.is_set():
            try:
                if not self._advertising():
                    raise ConnectionError("node not advertising "
                                          "(slot busy or out of range)")
                self._set_state("connecting", self.ble_address or "scan")
                self._connect()
                backoff = RECONNECT_MIN_S
                # _connect returns when the interface drops (or stop is set)
            except Exception as e:                  # noqa: BLE001
                self._set_state("down", str(e)[:60] or e.__class__.__name__)
            self._close()
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, RECONNECT_MAX_S)
        self._set_state("down", "stopped")

    def _connect(self):
        # Lazy: the only import of the radio stack in the whole plane.
        from meshtastic.ble_interface import BLEInterface
        from pubsub import pub

        disconnected = threading.Event()

        def on_receive(packet, interface=None):
            try:
                self._handle_packet(packet)
            except Exception:                        # noqa: BLE001
                log.exception("rx handler")

        def on_disconnect(interface=None):
            disconnected.set()

        pub.subscribe(on_receive, "meshtastic.receive.text")
        pub.subscribe(on_disconnect, "meshtastic.connection.lost")
        try:
            self._iface = BLEInterface(self.ble_address)
            self._command_index = self._find_channel_index()
            self._set_state("up", f"ch[{self._command_index}]="
                                  f"{self.channel_name}")
            while not self._stop.is_set() and not disconnected.is_set():
                time.sleep(0.5)
            if disconnected.is_set():
                raise ConnectionError("BLE link lost")
        finally:
            pub.unsubscribe(on_receive, "meshtastic.receive.text")
            pub.unsubscribe(on_disconnect, "meshtastic.connection.lost")

    def _find_channel_index(self):
        """Index of the command channel on the connected node. A node without
        the command channel is a mis-provisioned node — refuse loudly rather
        than dispatch from channel 0."""
        node = self._iface.localNode
        for ch in getattr(node, "channels", None) or []:
            settings = getattr(ch, "settings", None)
            if settings and getattr(settings, "name", "") == self.channel_name:
                return ch.index
        raise RuntimeError(f"node has no '{self.channel_name}' channel "
                           "(see docs/LORAMESH.md provisioning)")

    # ------------------------------------------------------------------- rx --
    def _handle_packet(self, packet):
        if packet.get("rxRssi") is not None:
            self.last_rssi = packet.get("rxRssi")
            self.last_snr = packet.get("rxSnr")
        sender, text = filter_frame(packet, self._command_index,
                                    self.allowed_nodes)
        if sender is None:
            self.dropped += 1
            log.info("dropped frame: %s", text)
            self.on_change()
            return
        self.last_heard = sender
        reply = self.executor.handle(text, sender=sender)
        try:
            # One reply, DM'd to the sender on the command channel (§0).
            self._iface.sendText(reply, destinationId=sender,
                                 channelIndex=self._command_index,
                                 wantAck=True)
        except Exception:                            # noqa: BLE001
            # The client re-sends blind on silence; verbs are idempotent by
            # contract, so a lost reply is safe (§4). Never retry-storm.
            log.exception("reply send failed")
        self.on_change()
