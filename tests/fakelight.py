"""Fake Scottina Light — a DOCK-PROTOCOL.md responder driven by the shared
conformance vectors (To-DoLists/dock-vectors.json, §10).

This is the "no firmware in the loop" half of the contract: the vectors pin
this fake's behavior byte-for-byte (tests/test_lightdock.py replays every
vector through it), and the sync engine then runs full sessions against it
on a PTY. Its state model is the vectors' `defaults.light_state` fixture —
an in-memory SD card, staging area and clock.

Standalone bench use (a fake Light on a real PTY for manual driving):

    python3 -m tests.fakelight          # prints its PTY path, serves forever
"""

import copy
import hashlib
import json
import os
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash.lightdock import (  # noqa: E402
    ERROR_CODES, TYPES, TYPE_NAMES, FrameScanner, build_frame, pack_str,
    unpack_str)

VECTORS_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "To-DoLists", "dock-vectors.json")

# Canonical ERROR message strings — the vectors pin these exact bytes.
# (Conformance for real firmware is code-only; the fake replays verbatim.)
ERR_MSG = {
    "ERR_BAD_CRC": "bad crc", "ERR_BAD_FRAME": "bad frame",
    "ERR_UNKNOWN_TYPE": "unknown type", "ERR_NO_SD": "no sd card",
    "ERR_PATH_REJECTED": "path rejected", "ERR_NOT_FOUND": "not found",
    "ERR_IO": "io error", "ERR_HASH_MISMATCH": "hash mismatch",
    "ERR_BUSY": "busy", "ERR_UNSUPPORTED_VER": "unsupported version",
    "ERR_TOO_LARGE": "too large",
}

# §8: the commands every protocol version must keep wire-compatible.
COMPAT_TYPES = {TYPES["HELLO"], TYPES["SET_CLOCK"], TYPES["BYE"]}


def load_vectors(path=VECTORS_PATH):
    with open(path) as f:
        return json.load(f)


class FakeLight:
    """Behavioral DOCK-PROTOCOL responder over an in-memory filesystem.

    `state_overrides` mirrors the vectors' per-vector `light_state` blocks:
    plain HELLO fields, `files` (path -> fixture dict with content_hex),
    `staged` (path -> byte count already in its .partial) and `open_path`
    (a file held open, answering ERR_BUSY).
    """

    def __init__(self, state_overrides=None, vectors_doc=None):
        doc = vectors_doc or load_vectors()
        state = copy.deepcopy(doc["defaults"]["light_state"])
        state.update(state_overrides or {})
        self.state = state
        # Fixtures may carry full content (content_hex) or, for LIST-only
        # fixtures like the 45-log pagination set, just size + sha256. The
        # declared sha stays authoritative for hashing; the filler content
        # only ever feeds size-driven paths.
        self.files = {}
        self.fixture_sha = {}
        for path, fx in state["files"].items():
            if "content_hex" in fx:
                self.files[path] = bytes.fromhex(fx["content_hex"])
            else:
                self.files[path] = bytes(fx["size"])
            if fx.get("sha256"):
                self.fixture_sha[path] = fx["sha256"]
        self.staged = {path: bytearray(self.files.get(path, b"")[:n])
                       for path, n in state.get("staged", {}).items()}
        self.open_path = state.get("open_path")
        self.scanner = FrameScanner(state["max_payload"])
        self.requests = []              # (type_name, payload bytes) seen
        self._pty_master = None
        self._thread = None

    # ------------------------------------------------------------ wire I/O --
    def handle_bytes(self, data):
        """Feed raw wire bytes, return the raw response bytes (may be b"")."""
        out = b""
        for ev in self.scanner.feed(data):
            if ev[0] == "badcrc":
                # Pinned by vector err-bad-crc: a complete frame with a bad
                # CRC is answered, then the scanner rescans per §2.
                out += self._error(ev[2], "ERR_BAD_CRC")
                continue
            _, tbyte, seq, payload = ev
            out += self._handle_frame(tbyte, seq, payload)
        return out

    # ------------------------------------------------------------ dispatch --
    def _handle_frame(self, tbyte, seq, payload):
        name = TYPE_NAMES.get(tbyte)
        if name in (None, "ERROR"):     # ERROR is response-only (§4)
            return self._error(seq, "ERR_UNKNOWN_TYPE")
        self.requests.append((name, payload))
        if (self.state["proto_version"] != 1 and tbyte not in COMPAT_TYPES):
            # §8 backstop, pinned by vector err-unsupported-ver
            return self._error(seq, "ERR_UNSUPPORTED_VER")
        try:
            return getattr(self, "_cmd_" + name.lower())(seq, payload)
        except _Reject as r:
            return self._error(seq, r.code_name)
        except Exception:               # noqa: BLE001 — malformed payload
            return self._error(seq, "ERR_BAD_FRAME")

    def _error(self, seq, code_name):
        return build_frame("ERROR", seq,
                           struct.pack("<H", ERROR_CODES[code_name])
                           + pack_str(ERR_MSG[code_name]))

    # ------------------------------------------------------- path policing --
    @staticmethod
    def _reject_pass(path):
        """§7 universal rejections, before any per-command prefix rule."""
        if (not path.startswith("/") or ".." in path or "\\" in path
                or "\0" in path):
            raise _Reject("ERR_PATH_REJECTED")

    def _need_sd(self):
        if not self.state["sd_present"]:
            raise _Reject("ERR_NO_SD")

    def _sha(self, path):
        """Digest of a file — the fixture's declared sha256 wins over the
        (possibly filler) content bytes."""
        declared = self.fixture_sha.get(path)
        if declared:
            return bytes.fromhex(declared)
        return hashlib.sha256(self.files[path]).digest()

    # ------------------------------------------------------------ commands --
    def _cmd_hello(self, seq, payload):
        st = self.state
        p = (struct.pack("<H", st["proto_version"])
             + pack_str(st["product"]) + pack_str(st["fw_version"])
             + bytes([st["sd_present"]])
             + struct.pack("<Q", st["clock_epoch"])
             + bytes([st["clock_quality"], st["clock_set_this_boot"]])
             + struct.pack("<H", st["max_payload"])
             + bytes([st["flags"]]))
        return build_frame("HELLO", seq, p)

    def _cmd_set_clock(self, seq, payload):
        if len(payload) != 9:
            raise _Reject("ERR_BAD_FRAME")
        epoch = struct.unpack_from("<Q", payload)[0]
        quality = payload[8]
        if quality > 2 or (quality == 0 and epoch != 0):
            # pinned by vector set-clock-unsynced-nonzero: Light enforces
            # the §4 honesty rule rather than trusting Prime's discipline
            raise _Reject("ERR_BAD_FRAME")
        self.state.update(clock_epoch=epoch, clock_quality=quality,
                          clock_set_this_boot=1)
        return build_frame("SET_CLOCK", seq,
                           bytes([1]) + struct.pack("<Q", epoch))

    def _cmd_list(self, seq, payload):
        dirname, off = unpack_str(payload, 0)
        if len(payload) != off + 3:
            raise _Reject("ERR_BAD_FRAME")      # v0.1 shape, or garbage
        want_hashes = payload[off]
        start_index = struct.unpack_from("<H", payload, off + 1)[0]
        if dirname not in ("/logs/", "/tables/"):
            raise _Reject("ERR_PATH_REJECTED")
        self._need_sd()
        names = sorted(p for p in self.files
                       if p.startswith(dirname) and p != self.open_path)
        # §4 page fill, matching Light's exactly: greedy while the payload
        # (4-byte header + entries + the trailing `more` byte) still fits
        # max_payload. 42 unhashed 24 B entries = 1013 B against 1024.
        max_payload = self.state["max_payload"]
        body, count = b"", 0
        for p in names[start_index:]:
            content = self.files[p]
            eb = (pack_str(p[len(dirname):])
                  + struct.pack("<I", len(content)) + struct.pack("<Q", 0))
            if want_hashes:
                eb += bytes([1]) + self._sha(p)
            else:
                eb += bytes([0])
            if 4 + len(body) + len(eb) + 1 > max_payload:
                break
            body += eb
            count += 1
        more = 1 if start_index + count < len(names) else 0
        return build_frame("LIST", seq,
                           struct.pack("<H", start_index)
                           + struct.pack("<H", count) + body + bytes([more]))

    @staticmethod
    def _writable(path):
        return path.startswith("/tables/") or path == "/config.json"

    def _cmd_put(self, seq, payload):
        path, off = unpack_str(payload, 0)
        offset = struct.unpack_from("<I", payload, off)[0]
        chunk_len = struct.unpack_from("<H", payload, off + 4)[0]
        chunk = payload[off + 6:]
        if len(chunk) != chunk_len:
            raise _Reject("ERR_BAD_FRAME")
        self._reject_pass(path)
        if not self._writable(path):
            raise _Reject("ERR_PATH_REJECTED")
        self._need_sd()
        if offset == 0:
            self.staged[path] = bytearray(chunk)    # create or truncate
        elif offset == len(self.staged.get(path, b"")):
            self.staged[path].extend(chunk)
        else:
            raise _Reject("ERR_IO")                 # a gap (§4 PUT)
        return build_frame("PUT", seq, bytes([1])
                           + struct.pack("<I", len(self.staged[path])))

    def _cmd_commit(self, seq, payload):
        path, off = unpack_str(payload, 0)
        digest = bytes(payload[off:off + 32])
        if len(digest) != 32:
            raise _Reject("ERR_BAD_FRAME")
        self._reject_pass(path)
        if not self._writable(path):
            raise _Reject("ERR_PATH_REJECTED")
        self._need_sd()
        if path not in self.staged:
            raise _Reject("ERR_NOT_FOUND")
        staged = bytes(self.staged.pop(path))
        if hashlib.sha256(staged).digest() != digest:
            raise _Reject("ERR_HASH_MISMATCH")      # staging file unlinked
        self.files[path] = staged                   # atomic rename
        self.fixture_sha.pop(path, None)            # declared sha now stale
        return build_frame("COMMIT", seq, bytes([1]))

    def _cmd_get(self, seq, payload):
        path, off = unpack_str(payload, 0)
        offset = struct.unpack_from("<I", payload, off)[0]
        max_len = struct.unpack_from("<H", payload, off + 4)[0]
        self._reject_pass(path)
        if not path.startswith("/logs/"):
            raise _Reject("ERR_PATH_REJECTED")
        if max_len > self.state["max_payload"]:
            raise _Reject("ERR_TOO_LARGE")
        self._need_sd()
        if path == self.open_path:
            raise _Reject("ERR_BUSY")
        if path not in self.files:
            raise _Reject("ERR_NOT_FOUND")
        content = self.files[path]
        data = content[offset:offset + max_len]
        eof = 1 if offset + len(data) >= len(content) else 0
        return build_frame("GET", seq, struct.pack("<I", offset)
                           + struct.pack("<H", len(data)) + data
                           + bytes([eof]))

    def _cmd_delete(self, seq, payload):
        path, off = unpack_str(payload, 0)
        digest = bytes(payload[off:off + 32])
        if len(digest) != 32:
            raise _Reject("ERR_BAD_FRAME")
        self._reject_pass(path)
        if not path.startswith("/logs/"):
            raise _Reject("ERR_PATH_REJECTED")
        self._need_sd()
        if path not in self.files:
            raise _Reject("ERR_NOT_FOUND")
        if self._sha(path) != digest:
            raise _Reject("ERR_HASH_MISMATCH")      # file untouched
        del self.files[path]                        # match, and only match
        self.fixture_sha.pop(path, None)
        return build_frame("DELETE", seq, bytes([1]))

    def _cmd_bye(self, seq, payload):
        return build_frame("BYE", seq, bytes([1]))

    # ------------------------------------------------------------ PTY serve --
    def start_pty(self):
        """Serve on a fresh PTY in a daemon thread; returns the slave path
        for the engine to open like a real /dev/ttyACM*."""
        import pty
        self._pty_master, slave = pty.openpty()
        path = os.ttyname(slave)
        self._slave_holdopen = slave    # keeps the master from EOFing when
        self._thread = threading.Thread(  # the client closes and reopens
            target=self._serve, daemon=True)
        self._thread.start()
        return path

    def _serve(self):
        import select
        fd = self._pty_master            # snapshot: stop_pty() nulls the attr
        while True:
            try:
                r, _, _ = select.select([fd], [], [], 0.5)
                if not r:
                    continue
                data = os.read(fd, 4096)
            except (OSError, ValueError):
                return                  # master closed by stop_pty()
            if not data:
                return
            out = self.handle_bytes(data)
            if out:
                os.write(fd, out)

    def stop_pty(self):
        for fd in (self._pty_master, getattr(self, "_slave_holdopen", None)):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._pty_master = None
        if self._thread:
            self._thread.join(timeout=2)


class _Reject(Exception):
    def __init__(self, code_name):
        self.code_name = code_name
        super().__init__(code_name)


if __name__ == "__main__":
    fake = FakeLight()
    pty_path = fake.start_pty()
    print("fake Light serving on", pty_path, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        fake.stop_pty()
