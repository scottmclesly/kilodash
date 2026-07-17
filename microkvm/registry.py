"""The verb registry — THE allow-list (MICROKVM-PROTOCOL.md §4).

The executor derives everything from this table, never the reverse: verb
name, arity, per-arg closed domains, read-only|action class, and the exact
list[str] argv (where the verb touches a subprocess at all). Args are closed
enumerations only — the grammar cannot express a free string, path, flag, or
shell fragment (§5). A verb whose implementation would need one is a verb we
don't ship.

Same guard-rail shape as kilodash/scan.py: the registry makes offensive input
*unexpressible*, and executor._enforce() refuses anything that slips past a
future registry edit (defense in depth).
"""

import re
from dataclasses import dataclass, field

READ_ONLY = "read-only"
ACTION = "action"

# Every binary the plane may ever spawn. The reject pass refuses any resolved
# argv whose argv[0] is not in here, whatever the registry says.
ALLOWED_BINARIES = frozenset({"systemctl", "candump"})

# A domain token: short, lowercase, no whitespace, no shell metacharacters.
# Checked again by the reject pass even for tokens that came from a domain.
TOKEN_RE = re.compile(r"[a-z0-9._-]{1,32}")

# ---- closed domains (§4) ----------------------------------------------------
METRICS = frozenset({"temp", "mem", "disk", "load", "uptime", "wifi"})
CAP_OPS = frozenset({"start", "stop"})
CAP_TARGETS = frozenset({"can"})
SVC_OPS = frozenset({"restart"})
SERVICES = frozenset({"kilodash", "signalk", "nodered", "kismet"})
# Tile slugs are the one runtime-resolved domain: the known screen titles,
# lowercased/alnum-only, injected by the host app (a closed enumeration all
# the same — never user input). Headless default keeps the executor testable.
DEFAULT_TILES = frozenset({"home"})

# argv the cap verb wraps (bounded: -n caps frames; -L one line per frame)
CAP_ARGV = {"can": ("candump", "-L", "-n", "100000", "can0")}
REBOOT_ARGV = ("systemctl", "reboot")
REBOOT_DELAY_S = 15


@dataclass(frozen=True)
class Arg:
    name: str
    domain: frozenset          # the closed enumeration this token must be in


@dataclass(frozen=True)
class Verb:
    name: str
    klass: str                                # READ_ONLY | ACTION
    args: tuple = ()                          # tuple[Arg]
    argv: tuple = ()                          # fixed argv template, "{name}"
    #                                           placeholders filled from args
    func: str = ""                            # Executor method: _do_<func>


def build_registry(tiles=None):
    """The authoritative verb table. `tiles` injects the known tile-slug
    domain (from the launcher's screen list); everything else is static."""
    tiles = frozenset(tiles) if tiles else DEFAULT_TILES
    verbs = (
        Verb("status", READ_ONLY, func="status"),
        Verb("health", READ_ONLY, func="health"),
        Verb("snap", READ_ONLY, args=(Arg("metric", METRICS),), func="snap"),
        Verb("tile", ACTION, args=(Arg("name", tiles),), func="tile"),
        Verb("cap", ACTION,
             args=(Arg("op", CAP_OPS), Arg("target", CAP_TARGETS)),
             func="cap"),
        Verb("svc", ACTION,
             args=(Arg("op", SVC_OPS), Arg("name", SERVICES)),
             argv=("systemctl", "restart", "{name}.service"),
             func="svc"),
        Verb("reboot", ACTION, argv=REBOOT_ARGV, func="reboot"),
    )
    return {v.name: v for v in verbs}


def resolve_argv(verb, args):
    """Fill the verb's argv template from validated arg tokens. Returns a
    list[str] (a fresh list — callers may not mutate the registry)."""
    values = {spec.name: tok for spec, tok in zip(verb.args, args)}
    return [part.format(**values) for part in verb.argv]
