"""Unit tests for the Tables converter service (kilodash/tableconv.py) and
its extraction worker (kilodash/pdfextract.py).

Run from the repo root:  python -m unittest discover -s tests
Covers the upload gate (extension + magic), the approve → validate →
install flow (rejections never touch the store), the Installed tab
actions (toggle is manifest-only, remove, download, manifest), inbox
ingest (allow-listed filenames, move semantics), the idle/in-flight
Activity bookkeeping behind the timeout, and the PGN-candidate heuristics
of the extractor. Flask test client only — no server, no network, no
subprocess."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kilodash import pdfextract, tableconv  # noqa: E402
from tables import store  # noqa: E402

GOOD_JSON = json.dumps({"PGNs": [{
    "PGN": 130306, "Name": "Wind Data",
    "Fields": [{"Name": "Speed", "BitOffset": 8, "BitLength": 16,
                "Resolution": 0.01, "Units": "m/s"}]}]})


class ConvCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tableconv-test-")
        self._base = store.BASE
        store.BASE = self.tmp
        self.app = tableconv.create_app()
        self.app.testing = True
        self.c = self.app.test_client()

    def tearDown(self):
        store.BASE = self._base
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestUploadGate(ConvCase):
    def test_rejects_wrong_extension_and_magic(self):
        r = self.c.post("/pgn/upload", data={
            "pdf": (io.BytesIO(b"%PDF-1.4 whatever"), "notes.txt")})
        self.assertIn("not+a+PDF", r.headers["Location"])
        r = self.c.post("/pgn/upload", data={
            "pdf": (io.BytesIO(b"MZ\x90\x00 exe"), "evil.pdf")})
        self.assertIn("not+a+PDF", r.headers["Location"])
        self.assertEqual(os.listdir(store.upload_dir()), [])

    def test_no_file(self):
        r = self.c.post("/pgn/upload", data={})
        self.assertIn("err=no", r.headers["Location"])


class TestInstallFlow(ConvCase):
    def test_approve_validate_install(self):
        r = self.c.post("/install", data={
            "uid": "", "name": "wind", "json": GOOD_JSON})
        self.assertIn("/installed", r.headers["Location"])
        inv = store.list_tables()
        self.assertEqual([t["name"] for t in inv], ["wind"])
        self.assertTrue(inv[0]["verified"] and inv[0]["enabled"])
        self.assertEqual(inv[0]["meta"]["converter_version"],
                         tableconv.VERSION)

    def test_invalid_json_rejected_store_untouched(self):
        for bad in ("not json", "{}", '{"PGNs": []}'):
            r = self.c.post("/install", data={
                "uid": "", "name": "bad", "json": bad})
            self.assertIn("rejected", r.headers["Location"])
        self.assertEqual(store.list_tables(), [])

    def test_bad_name_rejected(self):
        r = self.c.post("/install", data={
            "uid": "", "name": "../evil", "json": GOOD_JSON})
        self.assertIn("bad+name", r.headers["Location"])
        self.assertEqual(store.list_tables(), [])


class TestInstalledTab(ConvCase):
    def setUp(self):
        super().setUp()
        self.c.post("/install", data={"uid": "", "name": "wind",
                                      "json": GOOD_JSON})

    def test_toggle_and_remove(self):
        self.c.post("/tables/wind/toggle")
        self.assertFalse(store.list_tables()[0]["enabled"])
        self.c.post("/tables/wind/toggle")
        self.assertTrue(store.list_tables()[0]["enabled"])
        self.c.post("/tables/wind/remove")
        self.assertEqual(store.list_tables(), [])

    def test_download_and_manifest(self):
        r = self.c.get("/tables/wind/download")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.data)["PGNs"][0]["PGN"], 130306)
        r.close()
        r = self.c.get("/tables/wind/manifest")
        self.assertEqual(r.get_json()["name"], "wind")
        self.assertEqual(self.c.get("/tables/ghost/download").status_code,
                         404)
        self.assertEqual(self.c.get("/tables/../x/download").status_code,
                         404)

    def test_pages_render(self):
        for path in ("/pgn", "/installed", "/dbc", "/"):
            r = self.c.get(path, follow_redirects=True)
            self.assertEqual(r.status_code, 200)


class TestInbox(ConvCase):
    def test_ingest_moves_and_installs(self):
        with open(os.path.join(self.tmp, "dropped.json"), "w") as f:
            f.write(GOOD_JSON)
        r = self.c.post("/inbox/ingest", data={"file": "dropped.json"})
        self.assertIn("ingested", r.headers["Location"])
        self.assertEqual(store.inbox_files(), [])          # moved, not copied
        t = store.list_tables()[0]
        self.assertEqual(t["name"], "dropped")
        self.assertEqual(t["meta"]["source_doc"], "inbox:dropped.json")

    def test_ingest_rejects_invalid_keeps_file(self):
        with open(os.path.join(self.tmp, "junk.json"), "w") as f:
            f.write("{}")
        r = self.c.post("/inbox/ingest", data={"file": "junk.json"})
        self.assertIn("rejected", r.headers["Location"])
        self.assertEqual(store.inbox_files(), ["junk.json"])

    def test_ingest_only_allowlisted_names(self):
        self.assertEqual(
            self.c.post("/inbox/ingest",
                        data={"file": "../config.json"}).status_code, 404)


class TestActivity(unittest.TestCase):
    def test_jobs_count_as_activity(self):
        act = tableconv.Activity()
        act._last -= 1000                      # long idle
        self.assertGreater(act.idle_secs(), 900)
        act.job_start()
        self.assertEqual(act.idle_secs(), 0.0)  # in-flight job pins it
        act.job_end()
        self.assertLess(act.idle_secs(), 1.0)   # job end refreshed the clock


class TestExtractHeuristics(unittest.TestCase):
    def test_candidates_plausible_and_ordered(self):
        pages = ["PGN 127508 Battery Status … see also 130306",
                 "serial 1234567 rev 20260712 … PGN: 65280"]
        got = pdfextract.candidates(pages)
        self.assertEqual(got[:2], [127508, 65280])   # labelled first
        self.assertIn(130306, got)
        self.assertNotIn(1234567, got)               # implausible ranges out
        self.assertNotIn(20260712, got)

    def test_skeleton_is_valid_subset(self):
        from tables import validate
        sk = pdfextract.skeleton([127508, 130306])
        tables, warns = validate.validate(sk)
        self.assertEqual(set(tables), {127508, 130306})
        self.assertEqual(warns, [])


if __name__ == "__main__":
    unittest.main()
