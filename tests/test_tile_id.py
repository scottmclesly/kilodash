"""Unit tests for Screen.tile_id — the single wire identity (WEB-PROTOCOL.md §4.1).

Run from the repo root:  python -m unittest discover -s tests

`tile_id` replaced two conflicting slug functions that produced *different*
strings for the same screen (`microkvm` vs `micro-kvm`). The migration's real
risk is not the rename — it is that anything still matching an old no-hyphen
token breaks silently, at runtime, with no import error.

The pre-existing microkvm tests could not catch that: they construct the
executor with a literal `tiles={...}` set and never call the slug function, so
they keep passing while production breaks. These tests close that gap by
asserting on the *derivation* and on the compatibility surfaces:

  - every screen declares a unique, well-formed tile_id;
  - tile_id agrees with web-vectors.json, which is the frozen authority;
  - every legacy alnum token a paired handset might still send resolves.

No radio, no panel, no framebuffer — stdlib only.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash.screens import SCREENS  # noqa: E402
from kilodash.screens.base import Screen  # noqa: E402
from kilodash.screens.calibrate import CalibrationScreen  # noqa: E402
from microkvm import executor, registry  # noqa: E402
from microkvm.service import build_tile_aliases, legacy_tile_slug  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VECTORS = os.path.join(REPO, "To-DoLists", "web-vectors.json")

# Canonical form per WEB-PROTOCOL.md §4.1: lowercase ASCII, digits, hyphens.
TILE_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Screens reachable from the launcher, plus the calibration screen, which is
# held separately on the App (app.py) and is NOT in SCREENS — it still needs an
# id so it can never collide with one.
ALL_SCREEN_CLASSES = list(SCREENS) + [CalibrationScreen]


class TileIdDeclaration(unittest.TestCase):
    def test_base_declares_none(self):
        """The base class must not supply a usable default — a screen that
        forgets tile_id should be visibly absent, not silently aliased."""
        self.assertIsNone(Screen.tile_id)

    def test_every_screen_declares_one(self):
        for cls in ALL_SCREEN_CLASSES:
            with self.subTest(screen=cls.__name__):
                self.assertTrue(getattr(cls, "tile_id", None),
                                f"{cls.__name__} has no tile_id")

    def test_well_formed(self):
        for cls in ALL_SCREEN_CLASSES:
            with self.subTest(screen=cls.__name__):
                self.assertRegex(cls.tile_id, TILE_ID_RE)

    def test_unique(self):
        seen = {}
        for cls in ALL_SCREEN_CLASSES:
            self.assertNotIn(
                cls.tile_id, seen,
                f"{cls.__name__} and {seen.get(cls.tile_id)} share "
                f"tile_id '{cls.tile_id}'")
            seen[cls.tile_id] = cls.__name__

    def test_launcher_is_home(self):
        """WEB-PROTOCOL.md §4.1: the launcher is `home`. Its title is
        'Scottina', so this is exactly the case a title-derived slug got
        wrong — microkvm papered over it with a hardcoded alias."""
        self.assertEqual(SCREENS[0].tile_id, "home")

    def test_passes_the_command_token_regex(self):
        """Hyphenated ids must survive the micro KVM reject pass unchanged."""
        for cls in ALL_SCREEN_CLASSES:
            with self.subTest(screen=cls.__name__):
                m = registry.TOKEN_RE.fullmatch(cls.tile_id)
                self.assertIsNotNone(
                    m, f"tile_id '{cls.tile_id}' is not a legal command token")


class TileIdIsNotDerivedFromTitle(unittest.TestCase):
    """The whole point of tile_id is that it is declared, not computed. These
    pin the specific cases where a derivation would give the wrong answer."""

    def _by_title(self, title):
        for cls in ALL_SCREEN_CLASSES:
            if cls.title == title:
                return cls
        self.fail(f"no screen titled {title!r}")

    def test_known_divergences(self):
        # title -> tile_id, for screens where kebab-of-title is NOT the answer
        for title, want in (("Scottina", "home"), ("NMEA2K", "n2k")):
            with self.subTest(title=title):
                self.assertEqual(self._by_title(title).tile_id, want)

    def test_hyphenated_titles_keep_their_hyphen(self):
        # These were unreachable via KILODASH_OPEN before the migration: the
        # old transform replaced spaces only, so a hyphen in the title could
        # never be produced from any input spelling.
        for title, want in (("Wi-Fi", "wi-fi"), ("RTL-SDR", "rtl-sdr"),
                            ("Node-RED", "node-red")):
            with self.subTest(title=title):
                self.assertEqual(self._by_title(title).tile_id, want)

    def test_tile_id_is_independent_of_glyph_and_device_key(self):
        """glyph and device_key are separate namespaces that collide with
        tile_id by coincidence on some screens. Renaming one must never be
        assumed to rename another — this asserts they are read separately."""
        wifisniff = self._by_title("WiFi Sniff")
        self.assertEqual(wifisniff.tile_id, "wifi-sniff")
        self.assertEqual(wifisniff.glyph, "wifisniff")
        self.assertEqual(wifisniff.device_key, "wifisniff")


class TileIdMatchesFrozenVectors(unittest.TestCase):
    """web-vectors.json is the conformance authority (WEB-PROTOCOL.md §11).
    If a tile_id drifts from a vector, the web mirror's test oracle is
    silently invalidated — so the vectors win, and this catches the drift."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(VECTORS):
            raise unittest.SkipTest("web-vectors.json not present")
        with open(VECTORS) as fh:
            cls.doc = json.load(fh)
        cls.ids = {c.tile_id for c in ALL_SCREEN_CLASSES}

    def _tile_refs(self):
        """Every tile id named anywhere in the vectors."""
        out = set()

        def walk(node, key=None):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, k)
            elif isinstance(node, list):
                for v in node:
                    walk(v, key)
            elif isinstance(node, str) and key in ("tile", "id", "final_tile"):
                out.add(node)
        walk(self.doc.get("vectors", []))
        return out

    def test_vector_tile_ids_all_exist(self):
        # 'id' is overloaded in the vectors (vector ids, alert ids, CAN frame
        # ids, button ids), so only check tokens that look like a tile id and
        # are not obviously something else.
        refs = {r for r in self._tile_refs()
                if TILE_ID_RE.match(r) and not r.startswith("0x")}
        unknown = {r for r in refs if r not in self.ids}
        # Vector ids and button ids live in the same key space; subtract the
        # ones we know are not tiles.
        not_tiles = {v["id"] for v in self.doc["vectors"]} | {"refresh",
                                                             "clear", "pause"}
        unknown -= not_tiles
        self.assertEqual(unknown, set(),
                         f"vectors reference tile ids no screen declares: "
                         f"{sorted(unknown)}")

    def test_rich_model_screens_match(self):
        """The four v1 rich-model screens are named directly in the spec."""
        for want in ("home", "can-bus", "n2k", "light-dock"):
            with self.subTest(tile=want):
                self.assertIn(want, self.ids)


class LegacyAliasCompat(unittest.TestCase):
    """A paired handset holds canned messages containing the OLD tokens. They
    must keep working — off-grid is the worst place to find out they don't."""

    def setUp(self):
        self.aliases = build_tile_aliases(ALL_SCREEN_CLASSES)

    def test_every_changed_slug_has_an_alias(self):
        for cls in ALL_SCREEN_CLASSES:
            legacy = legacy_tile_slug(cls.title)
            if legacy == cls.tile_id:
                continue                      # unchanged, no alias needed
            with self.subTest(screen=cls.__name__, legacy=legacy):
                self.assertEqual(self.aliases.get(legacy), cls.tile_id)

    def test_the_documented_examples(self):
        """The exact tokens in docs/MICROKVM.md and MICROKVM-PROTOCOL.md —
        the strings a user actually typed into their phone."""
        for legacy, want in (("nmea2k", "n2k"), ("lanscan", "lan-scan"),
                             ("pihealth", "pi-health"),
                             ("microkvm", "micro-kvm"),
                             ("signalk", "signal-k"),
                             ("rtlsdr", "rtl-sdr")):
            with self.subTest(legacy=legacy):
                self.assertEqual(self.aliases.get(legacy), want)

    def test_aliases_never_shadow_a_canonical_id(self):
        """An alias that collides with a real tile_id would silently redirect
        a valid command to the wrong screen."""
        ids = {c.tile_id for c in ALL_SCREEN_CLASSES}
        for legacy in self.aliases:
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, ids)

    def test_unchanged_slugs_get_no_alias(self):
        """Screens whose token did not change need no entry — an identity
        alias would be dead weight that hides a future real change."""
        for token in ("gps", "files", "tables", "settings", "kismet"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.aliases)


class ExecutorAcceptsLegacyTokens(unittest.TestCase):
    """End-to-end through the command plane: an old token on the wire reaches
    the right screen, and the reply teaches the operator the new one."""

    def setUp(self):
        self.requested = []
        ids = {c.tile_id for c in ALL_SCREEN_CLASSES}
        self.ex = executor.Executor(
            armed_fn=lambda: (True, ""),
            request_tile_fn=lambda s: (self.requested.append(s) or True),
            active_tile_fn=lambda: "home",
            tiles=ids,
            tile_aliases=build_tile_aliases(ALL_SCREEN_CLASSES))

    def test_canonical_token_works(self):
        self.assertEqual(self.ex.handle("tile n2k"), "tile: active=n2k")
        self.assertEqual(self.requested, ["n2k"])

    def test_legacy_token_is_normalised(self):
        """`tile nmea2k` is what is sitting in the handset's canned messages."""
        self.assertEqual(self.ex.handle("tile nmea2k"), "tile: active=n2k")
        self.assertEqual(self.requested, ["n2k"],
                         "legacy token must resolve to the canonical id "
                         "before it reaches the UI")

    def test_legacy_reply_echoes_canonical(self):
        """The reply is how an off-grid operator discovers the new token —
        from a command that worked, not from a rejection."""
        self.assertNotIn("nmea2k", self.ex.handle("tile nmea2k"))

    def test_hyphenated_token_survives_the_reject_pass(self):
        self.assertEqual(self.ex.handle("tile micro-kvm"),
                         "tile: active=micro-kvm")

    def test_help_advertises_canonical_only(self):
        """Aliases are accepted, never advertised — otherwise the grammar
        doubles in size and the old spelling looks endorsed."""
        out = self.ex.handle("help tile")
        self.assertIn("n2k", out)
        self.assertNotIn("nmea2k", out)

    def test_unknown_tile_still_rejects(self):
        self.assertIn("bad-arg", self.ex.handle("tile nosuchscreen"))


class ScreenApiIsNotShadowed(unittest.TestCase):
    """No screen may assign an instance attribute over the Screen web-mirror
    API. SignalKScreen did exactly this — it kept its REST snapshot in
    `self.model`, which shadowed the `model()` method, so calling it raised
    TypeError, the emitter caught it, and that screen silently mirrored as an
    EMPTY generic panel. Nothing failed; it just rendered nothing.

    That is invisible to every other kind of test, so it is pinned here by
    parsing the source: an assignment is a shadow whether or not it is ever
    exercised."""

    API = {"model", "model_rows", "model_buttons", "handle_button",
           "tile_id", "available", "tick", "render", "draw_content"}

    def test_no_instance_attribute_shadows_a_screen_method(self):
        import ast
        import pathlib
        screens = pathlib.Path(REPO) / "kilodash" / "screens"
        for path in sorted(screens.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for t in node.targets:
                    if (isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                            and t.attr in self.API):
                        self.fail(
                            f"{path.name}:{t.lineno} assigns self.{t.attr}, "
                            f"shadowing the Screen API method of the same "
                            f"name — that screen will mirror as an empty "
                            f"generic panel with no error")

    def test_every_screen_can_build_a_model_row_list(self):
        """model_rows() must be declared as a method on every screen that
        overrides it — a callable, never a value."""
        for cls in ALL_SCREEN_CLASSES:
            with self.subTest(screen=cls.__name__):
                self.assertTrue(callable(getattr(cls, "model", None)))
                self.assertTrue(callable(getattr(cls, "model_rows", None)))


if __name__ == "__main__":
    unittest.main()
