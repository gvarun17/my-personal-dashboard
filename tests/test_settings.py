import json
import os
import sys
import unittest
from unittest import mock

import settings
from tests.helpers import TempDataCase


class SettingsFileTests(TempDataCase):
    def test_load_missing_corrupt_or_wrong_shape(self):
        self.assertEqual(settings.load(), {})
        settings.path().write_text("{broken", encoding="utf-8")
        self.assertEqual(settings.load(), {})
        settings.path().write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(settings.load(), {})

    def test_save_merges_and_keeps_existing_values(self):
        settings.save(name="Varun", tz="Asia/Kolkata", key="k1")
        d = settings.save(name="V", tz=None, key=None)
        self.assertEqual((d["name"], d["tz"], d["key"]), ("V", "Asia/Kolkata", "k1"))
        self.assertIsInstance(d["updated"], int)
        self.assertEqual(json.loads(settings.path().read_text())["name"], "V")
        self.assertFalse(settings.path().with_suffix(".tmp").exists())

    @unittest.skipIf(os.name == "nt", "file permissions work differently on Windows")
    def test_file_is_private(self):
        settings.save(key="secret")
        self.assertEqual(settings.path().stat().st_mode & 0o777, 0o600)

    def test_creates_the_data_folder(self):
        with mock.patch.object(settings, "DATA", self.root / "new" / "data"):
            settings.save(name="x")
            self.assertTrue(settings.path().exists())

    def test_public_never_includes_the_key(self):
        settings.save(name="Varun", key="secret")
        self.assertNotIn("key", settings.public())
        self.assertEqual(settings.public()["name"], "Varun")


class ValidationTests(unittest.TestCase):
    def test_time_zones(self):
        zones = settings.time_zones()
        if zones:   # a time zone database is installed
            self.assertIn("Europe/London", zones)
        with mock.patch.dict(sys.modules, {"zoneinfo": None}):
            self.assertEqual(settings.time_zones(), [])

    def test_valid_tz(self):
        with mock.patch.object(settings, "time_zones", return_value=["Asia/Kolkata", "UTC"]):
            self.assertTrue(settings.valid_tz("Asia/Kolkata"))
            self.assertTrue(settings.valid_tz("UTC"))
            self.assertFalse(settings.valid_tz("Mars/Olympus"))
        for bad in ("", None, "../etc/passwd", "Asia/Kolkata; rm", " Asia/Kolkata"):
            self.assertFalse(settings.valid_tz(bad), bad)
        with mock.patch.object(settings, "time_zones", return_value=[]):
            self.assertTrue(settings.valid_tz("America/Argentina/Buenos_Aires"))
            self.assertFalse(settings.valid_tz("a/b/c/d"))

    def test_older_time_zone_names(self):
        with mock.patch.object(settings, "time_zones", return_value=["Asia/Kolkata", "America/New_York"]):
            self.assertEqual(settings.normalize_tz(" Asia/Calcutta "), "Asia/Kolkata")
            self.assertEqual(settings.normalize_tz("US/Eastern"), "America/New_York")
            self.assertEqual(settings.normalize_tz("Asia/Kolkata"), "Asia/Kolkata")
            self.assertEqual(settings.normalize_tz("Nowhere/Else"), "Nowhere/Else")
            self.assertEqual(settings.normalize_tz(None), "")
        with mock.patch.object(settings, "time_zones", return_value=["Asia/Calcutta", "Asia/Kolkata"]):
            self.assertEqual(settings.normalize_tz("Asia/Calcutta"), "Asia/Calcutta")   # known here: keep it
        with mock.patch.object(settings, "time_zones", return_value=[]):
            self.assertEqual(settings.normalize_tz("Asia/Calcutta"), "Asia/Calcutta")   # no database: keep it

    def test_every_alias_points_to_a_real_zone(self):
        zones = settings.time_zones()
        if not zones:
            self.skipTest("no time zone database here")
        for old, new in settings.TZ_ALIASES.items():
            self.assertTrue(settings.valid_tz(new) or new not in zones, (old, new))
            self.assertIn(new, zones, (old, new))

    def test_clean_name_and_key(self):
        self.assertEqual(settings.clean_name("  Varun   G \n"), "Varun G")
        self.assertEqual(len(settings.clean_name("x" * 200)), settings.NAME_MAX)
        self.assertEqual(settings.clean_name(None), "")
        self.assertEqual(settings.clean_key(" abc\n def\t"), "abcdef")
        self.assertEqual(settings.clean_key(None), "")

    def test_clean_goal(self):
        self.assertEqual(settings.clean_goal("km", " 12,5 "), ("km", 12.5))
        self.assertEqual(settings.clean_goal("minutes", "200"), ("minutes", 200))
        self.assertEqual(settings.clean_goal("runs", "3.0"), ("runs", 3))
        self.assertEqual(settings.clean_goal("run_km", ""), ("run_km", None))      # suggested target
        self.assertEqual(settings.clean_goal("laps", None), ("minutes", None))     # unknown type
        for gtype, value, message in (("km", "lots", "as a number"), ("km", "0", "above 0 and up to 500 km"),
                                      ("minutes", "9999", "up to 3000 min"), ("runs", "2.5", "whole number")):
            with self.assertRaisesRegex(ValueError, message):
                settings.clean_goal(gtype, value)


if __name__ == "__main__":
    unittest.main()
