"""Frame executor: parse → registry lookup → validate → arm-gate → enforce →
dispatch → exactly one terse reply (MICROKVM-PROTOCOL.md §§1-5).

Fully headless and unit-testable: every side-effecting seam is injected —
armed_fn (arm gate), info (metric provider), request_tile_fn (UI switch),
popen/run (subprocess), timer_factory (reboot scheduling). The defaults are
the real thing; tests swap in fakes and a fake frame source.

Defense in depth (§5): after a successful registry lookup, _enforce() re-
validates from scratch — arity, every token against its declared domain and
TOKEN_RE, and any resolved argv as list[str] with an allow-listed binary and
shell-safe elements. A registry edit that widens a domain by mistake trips
here instead of executing. No shell, ever: subprocess calls are list[str]
argv; a str command line cannot be expressed.
"""

import collections
import os
import re
import subprocess
import threading
import time

from . import registry as reg

REPLY_MAX = 200         # airtime discipline: one terse line (§2)
ECHO_MAX = 24           # unknown-verb echo cap (§2)
LOG_LINES = 64          # session-log ring for the tile (Phase 4)
CAPTURE_DIR = "/opt/kilodash/captures"

# What a resolved argv element may look like. Anything needing quoting or a
# shell to interpret is refused — mirrors scan.py's reject-list philosophy.
_ARGV_ELEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:=+-]*|-{1,2}[A-Za-z0-9][A-Za-z0-9-]*")


class RejectError(Exception):
    """A frame refused before dispatch; str(exc) is the reply body."""


def _printable(s, cap):
    # keeps space (for logged frames); verb/arg echoes are split tokens and
    # can never contain one
    return "".join(c for c in s if 0x20 <= ord(c) <= 0x7e)[:cap]


class Executor:
    def __init__(self, armed_fn=None, info=None, request_tile_fn=None,
                 active_tile_fn=None, link_fn=None, tiles=None,
                 popen=None, run=None, timer_factory=None,
                 capture_dir=CAPTURE_DIR):
        # armed_fn() -> (armed: bool, reason: str). Default: never armed —
        # an unwired executor must not execute actions (§3).
        self._armed_fn = armed_fn or (lambda: (False, "arm gate not wired"))
        self._info = info                       # metric()/services() provider
        self._request_tile = request_tile_fn    # slug -> bool (accepted)
        self._active_tile = active_tile_fn or (lambda: "-")
        self._link = link_fn or (lambda: None)  # -> {"rssi":…, "snr":…} | None
        self._popen = popen or subprocess.Popen
        self._run = run or self._run_argv
        self._timer_factory = timer_factory or threading.Timer
        self._capture_dir = capture_dir
        self.registry = reg.build_registry(tiles)
        self._caps = {}                         # target -> (Popen, logfile)
        self._reboot_timer = None
        self.log = collections.deque(maxlen=LOG_LINES)

    # ------------------------------------------------------------- frame in --
    def handle(self, line, sender="?"):
        """One command frame in, exactly one reply line out (§2). Sender-ID
        gating happens upstream in link.py — by the time a frame reaches
        here it is already from an allow-listed node on the command channel."""
        tokens = (line or "").lower().split()
        try:
            reply = self._dispatch(tokens)
            ok = True
        except RejectError as e:
            verb = tokens[0] if tokens else ""
            prefix = verb if verb in self.registry else "reject"
            reply = (f"{prefix}: {e}" if prefix != "reject" else f"reject: {e}")
            ok = False
        reply = reply[:REPLY_MAX]
        self.log.append({"ts": time.time(), "sender": sender,
                         "line": _printable(line or "", 80) or "(empty)",
                         "reply": reply, "ok": ok})
        return reply

    # Friendly aliases for the menu verb — a field operator reaching for the
    # obvious thing (`?`, `menu`) gets the help, not a rejection.
    _HELP_ALIASES = {"?", "menu"}

    def _dispatch(self, tokens):
        if not tokens:
            raise RejectError("unknown-verb ''")
        name = "help" if tokens[0] in self._HELP_ALIASES else tokens[0]
        verb = self.registry.get(name)
        if verb is None:
            raise RejectError(
                f"unknown-verb '{_printable(tokens[0], ECHO_MAX)}' (send help)")
        args = tokens[1:]
        if verb.variadic:
            # help executes nothing — it only reads the registry to format a
            # reply — so it skips strict arity, the arm gate, and the reject
            # pass. Its handler does its own safe (string-only) validation.
            return getattr(self, "_do_" + verb.func)(args, [])
        if len(args) != len(verb.args):
            raise RejectError(f"reject bad-arity want={len(verb.args)} "
                              f"got={len(args)}")
        for spec, tok in zip(verb.args, args):
            if tok not in spec.domain:
                raise RejectError(
                    f"reject bad-arg {spec.name}='{_printable(tok, ECHO_MAX)}'")
        if verb.klass == reg.ACTION:
            armed, reason = self._armed_fn()
            if not armed:
                raise RejectError(f"reject disarmed ({reason})")
        argv = reg.resolve_argv(verb, args) if verb.argv else []
        self._enforce(verb, args, argv)
        return getattr(self, "_do_" + verb.func)(args, argv)

    # ------------------------------------------------- independent reject pass
    def _enforce(self, verb, args, argv):
        """Re-validate everything from scratch (§5). Deliberately does not
        trust the lookup above — this is the scan.py _enforce_rejects pattern
        applied to verbs: a registry mistake trips here, not in a shell."""
        if self.registry.get(verb.name) is not verb:
            raise RejectError("reject enforce (verb not in registry)")
        if len(args) != len(verb.args):
            raise RejectError("reject enforce (arity)")
        for spec, tok in zip(verb.args, args):
            if not isinstance(tok, str) or not reg.TOKEN_RE.fullmatch(tok) \
                    or tok not in spec.domain:
                raise RejectError(f"reject enforce (arg {spec.name})")
        if argv:
            self._enforce_argv(argv)

    @staticmethod
    def _enforce_argv(argv):
        if not isinstance(argv, list) \
                or not all(isinstance(a, str) for a in argv):
            raise RejectError("reject enforce (argv not list[str])")
        if not argv or argv[0] not in reg.ALLOWED_BINARIES:
            raise RejectError("reject enforce (binary not allow-listed)")
        for a in argv:
            if not _ARGV_ELEM_RE.fullmatch(a):
                raise RejectError("reject enforce (argv element)")

    def _run_argv(self, argv, timeout=30):
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()

    # ------------------------------------------------------------- read-only --
    def _metric(self, name):
        try:
            v = self._info.metric(name) if self._info else None
        except Exception:       # a broken probe must not kill the plane
            v = None
        return v if v not in (None, "") else "?"

    def _armed_word(self):
        armed, _ = self._armed_fn()
        return "yes" if armed else "no"

    def _do_status(self, args, argv):
        link = self._link() or {}
        rssi = link.get("rssi", "?")
        snr = link.get("snr", "?")
        return (f"status: up {self._metric('uptime')}, "
                f"{self._metric('temp')}C, tile={self._active_tile()}, "
                f"armed={self._armed_word()}, rssi={rssi}/{snr}")

    def _do_health(self, args, argv):
        try:
            svcs = self._info.services() if self._info else {}
        except Exception:
            svcs = {}
        summary = " ".join(f"{k}={v}" for k, v in svcs.items()) or "?"
        return (f"health: svcs {summary}, disk {self._metric('disk')}%, "
                f"mem {self._metric('mem')}%, temp {self._metric('temp')}C, "
                f"armed={self._armed_word()}")

    def _do_snap(self, args, argv):
        return f"snap: {args[0]}={self._metric(args[0])}"

    # ---- menu (BBS-style: list choices so nothing has to be guessed) -------
    def _describe(self, verb):
        cls = "read-only" if verb.klass == reg.READ_ONLY else "action"
        head = f"{verb.name} [{cls}]" + (f" {verb.hint}" if verb.hint else "")
        if verb.variadic:
            return f"{head}: '{verb.name}' or '{verb.name} <verb>'"
        if not verb.args:
            return f"{head}: no args"
        body = "  ".join(f"{a.name}={{{' '.join(sorted(a.domain))}}}"
                         for a in verb.args)
        full = f"{head}: {body}"
        if len(full) <= REPLY_MAX:
            return full
        # A large domain (the tile list) crowds out the hint — keep the
        # choices, they're the point of the menu; the hint is expendable.
        return f"{verb.name} [{cls}]: {body}"

    def _do_help(self, args, argv):
        if not args:
            # Group by class and gloss each verb, so the bare menu teaches the
            # model — what reports (safe anytime) vs what acts (off-grid only)
            # — instead of listing eight opaque words. Kept to one frame.
            def term(v):
                return f"{v.name}({v.gloss})" if v.gloss else v.name
            reads = [v for v in self.registry.values()
                     if v.klass == reg.READ_ONLY and not v.variadic]
            acts = [v for v in self.registry.values()
                    if v.klass == reg.ACTION]
            return (f"report: {' '.join(term(v) for v in reads)}"
                    f" | act off-grid: {' '.join(term(v) for v in acts)}"
                    " | 'help <verb>' for options")
        target = "help" if args[0] in self._HELP_ALIASES else args[0]
        verb = self.registry.get(target)
        if verb is None:
            return (f"help: no verb '{_printable(args[0], ECHO_MAX)}'. "
                    "verbs: " + " ".join(self.registry))
        return self._describe(verb)

    # --------------------------------------------------------------- actions --
    def _do_tile(self, args, argv):
        name = args[0]
        if not self._request_tile or not self._request_tile(name):
            raise RejectError("reject enforce (ui unavailable)")
        return f"tile: active={name}"

    def _do_cap(self, args, argv):
        op, target = args
        proc_log = self._caps.get(target)
        alive = proc_log and proc_log[0].poll() is None
        if op == "start":
            if alive:                       # idempotent: one process, ever
                return f"cap: running target={target} pid={proc_log[0].pid} (already)"
            cap_argv = list(reg.CAP_ARGV[target])
            self._enforce_argv(cap_argv)
            os.makedirs(self._capture_dir, exist_ok=True)
            fh = open(os.path.join(self._capture_dir,
                                   f"microkvm-{target}.log"), "w")
            try:
                proc = self._popen(cap_argv, stdout=fh,
                                   stderr=subprocess.DEVNULL)
            except OSError as e:
                fh.close()
                raise RejectError(f"reject enforce (spawn: {e})") from e
            self._caps[target] = (proc, fh)
            return f"cap: running target={target} pid={proc.pid}"
        # stop
        if not alive:
            return f"cap: stopped target={target} (was not running)"
        proc, fh = proc_log
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        fh.close()
        del self._caps[target]
        return f"cap: stopped target={target}"

    def _do_svc(self, args, argv):
        _, name = args
        self._run(argv)     # resulting state, not rc, is what the reply says
        state_argv = ["systemctl", "is-active", f"{name}.service"]
        self._enforce_argv(state_argv)
        _rc2, state = self._run(state_argv)
        state = (state.splitlines() or ["unknown"])[0][:16]
        return f"svc: restarted {name} state={state or 'unknown'}"

    def _do_reboot(self, args, argv):
        if self._reboot_timer is not None:
            return "reboot: already scheduled"
        # Ack-then-act: this reply goes out over the link first; the argv
        # fires REBOOT_DELAY_S later — otherwise the operator hears nothing
        # and blind-retries a reboot (gotcha list).
        t = self._timer_factory(reg.REBOOT_DELAY_S, self._run, [list(argv)])
        t.daemon = True
        t.start()
        self._reboot_timer = t
        return f"reboot: scheduled in {reg.REBOOT_DELAY_S}s"
