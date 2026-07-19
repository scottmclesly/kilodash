"""Static guards for the web mirror bundle (kilodash/webui/).

Run from the repo root:  python -m unittest discover -s tests

The bundle is plain HTML/CSS/JS with no build step, so there is no compiler to
catch a regression. These are the invariants worth pinning anyway — each one
is a property the *product* depends on, not a style preference:

  * NO EXTERNAL REQUESTS. Scottina is a diagnostics box that has to work
    off-grid; a webfont or CDN reference is a hard dependency on the one thing
    that may not be there. This is the guard that keeps a convenient
    copy-paste from the reference mock out of the shipped bundle.
  * The renderers cover every model kind the protocol defines, so no screen
    can arrive with nothing to draw it.
  * The palette is taken from Hello, never hardcoded — otherwise a theme
    change on the box would desync the two surfaces.
  * Nothing computes a rate or ETA (WEB-UI-DESIGN §0): both surfaces must
    agree on a figure a user reads aloud.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.join(REPO, "kilodash", "webui")
HTML = os.path.join(WEBUI, "index.html")
APPJS = os.path.join(WEBUI, "app.js")
VECTORS = os.path.join(REPO, "To-DoLists", "web-vectors.json")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


class BundleExists(unittest.TestCase):
    def test_files_present(self):
        for p in (HTML, APPJS):
            self.assertTrue(os.path.isfile(p), f"missing {p}")

    def test_html_loads_only_local_script(self):
        srcs = re.findall(r'<script[^>]*src="([^"]+)"', read(HTML))
        self.assertEqual(srcs, ["app.js"],
                         "the bundle must load exactly one local script")


class NoExternalRequests(unittest.TestCase):
    """Off-grid is the design case, not an edge case."""

    BAD = re.compile(
        r"https?://|//fonts\.|googleapis|gstatic|cdnjs|jsdelivr|unpkg|"
        r"cdn\.|@import\s+url\(\s*['\"]?https?:", re.I)

    def test_no_external_urls(self):
        for p in (HTML, APPJS):
            with self.subTest(file=os.path.basename(p)):
                hits = [ln.strip() for ln in read(p).splitlines()
                        if self.BAD.search(ln)]
                self.assertEqual(hits, [],
                                 f"{p} references an external resource: {hits}")

    def test_no_link_preconnect_or_webfont(self):
        html = read(HTML)
        self.assertNotIn("<link", html.lower(),
                         "no <link> at all — the reference mock's Google Fonts "
                         "preconnect must not survive into the bundle")
        self.assertNotIn("@font-face", html.lower())

    def test_fetch_targets_are_same_origin_paths(self):
        for url in re.findall(r"""fetch\(\s*['"]([^'"]+)""", read(APPJS)):
            with self.subTest(url=url):
                self.assertTrue(url.startswith("/"), f"{url} is not same-origin")
        for url in re.findall(r"""EventSource\(\s*['"]([^'"]+)""", read(APPJS)):
            with self.subTest(url=url):
                self.assertTrue(url.startswith("/"), f"{url} is not same-origin")


class RendererCoverage(unittest.TestCase):
    def setUp(self):
        self.js = read(APPJS)

    def test_every_protocol_model_kind_has_a_renderer(self):
        if not os.path.isfile(VECTORS):
            self.skipTest("vectors absent")
        with open(VECTORS) as fh:
            kinds = json.load(fh)["model_kinds"]
        for k in kinds:
            with self.subTest(kind=k):
                self.assertRegex(self.js, rf"R\.{re.escape(k)}\s*=",
                                 f"no renderer for model kind {k!r}")

    def test_unknown_kind_falls_back(self):
        """§9: an unknown kind renders as generic when it carries rows, else a
        placeholder naming the kind — never blank, never an error."""
        self.assertIn("rendererFor", self.js)
        self.assertIn("NO RENDERER", self.js)

    def test_unknown_glyph_falls_back(self):
        self.assertRegex(self.js, r"G\[name\]\s*\|\|\s*G\.std")


class ProtocolDiscipline(unittest.TestCase):
    def setUp(self):
        self.js = read(APPJS)

    def test_palette_comes_from_hello_not_hardcoded(self):
        """§2: the theme is normative and arrives in Hello. A literal colour in
        the JS would desync the two surfaces on a theme change."""
        self.assertIn("applyTheme", self.js)
        stray = re.findall(r"#[0-9a-fA-F]{6}\b", self.js)
        self.assertEqual(stray, [], f"hardcoded colours in app.js: {stray}")

    def test_only_allowed_actions_are_sent(self):
        """§6 is a closed allow-list; the cut actions must not reappear."""
        sent = set(re.findall(r"""send\(\s*['"](\w+)['"]""", self.js))
        self.assertTrue(sent <= {"tap_tile", "button_press", "back", "home",
                                 "request_snapshot"},
                        f"unexpected action(s): {sent}")
        for gone in ("scroll", "field_set"):
            self.assertNotIn(f"'{gone}'", self.js)

    def test_delta_merge_is_shallow(self):
        """§4: shallow merge at the top level, arrays whole. A deep merge
        would silently diverge from what every other consumer does."""
        self.assertIn("Object.assign(S.model", self.js)

    def test_no_computed_rate_or_eta(self):
        """§0: compute no number the box did not emit.

        Checks CODE, not prose — the file's own comments say "no ETA", and a
        naive substring search flags its own documentation."""
        code = re.sub(r"/\*.*?\*/", "", self.js, flags=re.S)   # block comments
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)      # line comments
        for banned in ("eta", "throughput", "bytesPerSec", "kbps", "remaining"):
            with self.subTest(term=banned):
                self.assertNotRegex(
                    code, rf"\b{banned}\b",
                    f"{banned!r} in code suggests a figure the box never sent")

    def test_lightdock_phases_match_the_spec(self):
        """The spec and the vectors say push/pull. The box was aligned to them
        after this test caught it emitting tables/logs instead."""
        m = re.search(r"const PHASES = \[([^\]]+)\]", self.js)
        self.assertIsNotNone(m)
        phases = re.findall(r"'(\w+)'", m.group(1))
        self.assertEqual(phases, ["hello", "clock", "push", "pull", "done"])

    def test_box_emits_the_same_phases(self):
        """The emitter side of the same agreement — one vocabulary, not two."""
        src = read(os.path.join(REPO, "kilodash", "lightdock.py"))
        for p in ("push", "pull", "clock", "hello", "done"):
            with self.subTest(phase=p):
                self.assertIn(f'self.phase = "{p}"', src)


class Accessibility(unittest.TestCase):
    def setUp(self):
        self.html = read(HTML)

    def test_reduced_motion_guard(self):
        self.assertIn("prefers-reduced-motion", self.html)

    def test_focus_visible_on_controls(self):
        self.assertGreaterEqual(self.html.count(":focus-visible"), 3,
                                "tiles, buttons and BACK all need visible focus")

    def test_touch_targets_have_min_height(self):
        self.assertIn("min-height:44px", self.html)


if __name__ == "__main__":
    unittest.main()
