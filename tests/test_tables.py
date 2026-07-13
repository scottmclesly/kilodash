"""Unit tests for the decode-table store contract (TABLES.md).

Run from the repo root:  python -m unittest discover -s tests
Covers the §2 schema validator (subset acceptance, Canboat-spelling
aliases, two-tier failure: bad entries skipped / bad files fatal, unknown
keys ignored), the §3 manifest lifecycle (install/verify/sha256-stale),
the §4 write discipline (atomic install, tile enable-flip only), and the
merged load_enabled() path the NMEA2K screen decodes from. Stdlib
unittest only; everything runs against a temp store.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tables import store, validate  # noqa: E402

GOOD = {
    "PGNs": [
        {
            "PGN": 127508,
            "Name": "Battery Status",
            "FastPacket": False,
            "IgnoredVendorKey": {"anything": True},
            "Fields": [
                {"Name": "Instance", "BitOffset": 0, "BitLength": 8},
                {"Name": "Voltage", "BitOffset": 8, "BitLength": 16,
                 "Resolution": 0.01, "Units": "V"},
                {"Name": "Mode", "BitOffset": 24, "BitLength": 4,
                 "Lookup": {"0": "Off", "1": "On"}},
            ],
        },
    ]
}


class TestValidator(unittest.TestCase):
    def test_accepts_subset(self):
        tables, warns = validate.validate(GOOD)
        self.assertEqual(warns, [])
        t = tables[127508]
        self.assertEqual(t["name"], "Battery Status")
        self.assertFalse(t["fast"])
        self.assertEqual(t["fields"][1]["resolution"], 0.01)
        self.assertEqual(t["fields"][2]["lookup"]["1"], "On")

    def test_canboat_spellings(self):
        obj = {"PGNs": [{
            "PGN": 129029, "Description": "GNSS Position Data",
            "Type": "Fast",
            "Fields": [{"Id": "sid", "BitOffset": 0, "BitLength": 8,
                        "EnumValues": [{"name": "Zero", "value": "0"}]}],
        }]}
        tables, _ = validate.validate(obj)
        t = tables[129029]
        self.assertEqual(t["name"], "GNSS Position Data")
        self.assertTrue(t["fast"])
        self.assertEqual(t["fields"][0]["name"], "sid")
        self.assertEqual(t["fields"][0]["lookup"]["0"], "Zero")

    def test_bad_entry_skipped_not_fatal(self):
        obj = {"PGNs": [
            {"PGN": "nope", "Fields": []},
            {"PGN": 130306, "Fields": [
                {"Name": "Bad", "BitOffset": -1, "BitLength": 8},
                {"Name": "Speed", "BitOffset": 8, "BitLength": 16},
            ]},
        ]}
        tables, warns = validate.validate(obj)
        self.assertEqual(set(tables), {130306})
        self.assertEqual(len(tables[130306]["fields"]), 1)
        self.assertTrue(any("skipped" in w for w in warns))

    def test_bad_file_fatal(self):
        for bad in ({}, {"PGNs": "x"}, {"PGNs": []},
                    {"PGNs": [{"PGN": 1, "Fields": []}]}):
            with self.assertRaises(validate.TableInvalid):
                validate.validate(bad)
        with self.assertRaises(validate.TableInvalid):
            validate.validate_bytes(b"\xff not json")

    def test_bitlength_bounds(self):
        obj = {"PGNs": [{"PGN": 60928, "Fields": [
            {"Name": "TooWide", "BitOffset": 0, "BitLength": 65},
            {"Name": "Ok", "BitOffset": 0, "BitLength": 64},
        ]}]}
        tables, warns = validate.validate(obj)
        self.assertEqual([f["name"] for f in tables[60928]["fields"]], ["Ok"])
        self.assertEqual(len(warns), 1)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tables-test-")
        self._base = store.BASE
        store.BASE = self.tmp
        store.ensure_dirs()

    def tearDown(self):
        store.BASE = self._base
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestStore(StoreCase):
    def test_install_and_list(self):
        store.install("batt", GOOD, source_doc="v.pdf", converter_version="1.0")
        inv = store.list_tables()
        self.assertEqual(len(inv), 1)
        t = inv[0]
        self.assertEqual(t["name"], "batt")
        self.assertTrue(t["verified"])
        self.assertTrue(t["enabled"])
        self.assertEqual(t["pgn_count"], 1)
        self.assertEqual(t["meta"]["source_doc"], "v.pdf")

    def test_stale_sha256_is_unverified_and_disabled(self):
        store.install("batt", GOOD, source_doc="v.pdf", converter_version="1.0")
        with open(store.table_path("batt"), "a") as f:
            f.write("\n")               # hand-edit behind the converter's back
        t = store.list_tables()[0]
        self.assertFalse(t["verified"])
        self.assertFalse(t["enabled"])   # unverified never decodes
        self.assertNotIn(127508, store.load_enabled()[0])

    def test_enable_flip_is_manifest_only(self):
        store.install("batt", GOOD, source_doc="v.pdf", converter_version="1.0")
        with open(store.table_path("batt")) as f:
            before = f.read()
        self.assertFalse(store.set_enabled("batt", False))
        with open(store.table_path("batt")) as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(store.list_tables()[0]["enabled"])
        self.assertTrue(store.set_enabled("batt", True))
        self.assertIsNone(store.set_enabled("ghost", True))

    def test_load_enabled_merges_and_revalidates(self):
        store.install("a", GOOD, source_doc="a.pdf", converter_version="1.0")
        other = {"PGNs": [{"PGN": 130306, "Name": "Wind Data", "Fields": [
            {"Name": "Speed", "BitOffset": 8, "BitLength": 16,
             "Resolution": 0.01, "Units": "m/s"}]}]}
        store.install("b", other, source_doc="b.pdf", converter_version="1.0")
        store.set_enabled("a", False)
        merged, warns = store.load_enabled()
        self.assertEqual(set(merged), {130306})
        # a corrupt-but-manifested file is skipped with a warning, not fatal
        store.install("c", GOOD, source_doc="c.pdf", converter_version="1.0")
        with open(store.table_path("c"), "w") as f:
            f.write("{}")
        meta = store.read_meta("c")
        meta["sha256"] = store.sha256_file(store.table_path("c"))
        store._write_atomic(store.meta_path("c"), meta)
        merged, warns = store.load_enabled()
        self.assertEqual(set(merged), {130306})
        self.assertTrue(any("c:" in w for w in warns))

    def test_remove(self):
        store.install("batt", GOOD, source_doc="v.pdf", converter_version="1.0")
        store.remove("batt")
        self.assertEqual(store.list_tables(), [])
        self.assertFalse(os.path.exists(store.meta_path("batt")))

    def test_names_and_inbox(self):
        for bad in ("", "UPPER", "a/b", "../x", "a" * 65):
            self.assertFalse(store.valid_name(bad))
            with self.assertRaises(ValueError):
                store.install(bad, GOOD, source_doc="x", converter_version="1")
        self.assertEqual(store.slugify("VE.Can Registers (v2).PDF"),
                         "ve_can_registers_v2")
        with open(os.path.join(self.tmp, "dropped.json"), "w") as f:
            json.dump(GOOD, f)
        self.assertEqual(store.inbox_files(), ["dropped.json"])
        # inbox files are invisible to decode
        self.assertEqual(store.load_enabled()[0], {})


if __name__ == "__main__":
    unittest.main()
