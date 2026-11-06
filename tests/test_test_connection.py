import io
import runpy
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import garmin_api
from tests.helpers import make_activity

SCRIPT = str(Path(__file__).resolve().parent.parent / "src" / "test_connection.py")


class ConnectionCheckTests(unittest.TestCase):
    def test_lists_latest_activities(self):
        acts = [make_activity(1, datetime(2026, 11, 6, 9, 31), "treadmill_running", km=2.04),
                {"activityName": None, "activityType": None, "distance": None}]
        with mock.patch.object(garmin_api, "get", return_value=acts), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            runpy.run_path(SCRIPT, run_name="__main__")
        text = out.getvalue()
        self.assertIn("Connected. Your 2 latest activities", text)
        self.assertIn("2026-11-06 09:31  treadmill running", text)
        self.assertIn("2.04 km", text)
        self.assertIn("1.27 mi", text)

    def test_no_activities(self):
        with mock.patch.object(garmin_api, "get", return_value=None), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            runpy.run_path(SCRIPT, run_name="__main__")
        self.assertIn("Your 0 latest activities", out.getvalue())

    def test_failure_exits_with_error(self):
        with mock.patch.object(garmin_api, "get", side_effect=garmin_api.GarminError("key expired")), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            with self.assertRaises(SystemExit) as cm:
                runpy.run_path(SCRIPT, run_name="__main__")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Connection failed: key expired", out.getvalue())


if __name__ == "__main__":
    unittest.main()
