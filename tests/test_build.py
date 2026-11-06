import io
import json
import os
import re
import runpy
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import build
from tests.helpers import TODAY, TempDataCase, ds, make_activity, write_dataset

SRC = Path(__file__).resolve().parent.parent / "src"


def payload_of(html):
    """The JSON data embedded in the dashboard."""
    line = next(l for l in html.splitlines() if l.startswith("const D = "))
    return json.loads(line[len("const D = "):].rstrip(";").replace("<\\/", "</"))


class HelperTests(unittest.TestCase):
    def test_g(self):
        self.assertEqual(build.g({"a": {"b": 2}}, "a", "b"), 2)
        self.assertIsNone(build.g({"a": 1}, "a", "b"))
        self.assertIsNone(build.g({}, "a"))

    def test_sport(self):
        cases = {"running": "run", "treadmill_running": "run", "walking": "walk", "indoor_cycling": "cycle",
                 "mountain_biking": "cycle", "virtual_ride": "cycle", "spinning": "cycle", "lap_swimming": "swim",
                 "hiking": "hike", "strength_training": "strength", "hiit": "strength", "yoga": "strength",
                 "golf": "other", None: "other", "": "other"}
        for key, want in cases.items():
            self.assertEqual(build.sport(key), want, key)

    def test_r(self):
        self.assertEqual(build.r(1.26), 1.3)
        self.assertEqual(build.r(1.234, 2), 1.23)
        self.assertIsNone(build.r(None))
        self.assertIsNone(build.r("7"))


class BuildTests(TempDataCase):
    def build(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            build.build()
        html = self.out.read_text(encoding="utf-8")
        return html, payload_of(html), out.getvalue()

    def test_full_dataset(self):
        write_dataset(self)
        html, d, out = self.build()
        self.assertNotIn("__DATA__", html)
        self.assertIn("Wrote", out)
        self.assertEqual(d["today"], ds(TODAY))
        self.assertTrue(d["acts"])
        self.assertEqual(d["acts"], sorted(d["acts"], key=lambda a: a["ts"]))
        self.assertEqual({a["s"] for a in d["acts"]}, {"walk", "run", "cycle", "swim", "hike", "strength", "other"})
        # empty days (watch off), zero-second nights and missing HRV are dropped
        raw_daily = self.read("daily.json")
        self.assertLess(len(d["days"]), len(raw_daily))
        self.assertTrue(all(s["h"] > 0 for s in d["sleep"]))
        self.assertTrue(all(h["v"] for h in d["hrv"]))
        self.assertEqual(d["weight"], [{"d": ds(TODAY), "kg": 72.5}])
        self.assertEqual(d["fitAge"], 30.4)
        self.assertEqual(d["zones"], [95, 114, 133, 152, 171, 190])
        # vigorous minutes count double
        day = next(x for x in d["days"] if x["st"])
        self.assertEqual(day["im"], 20 + 2 * 10)
        td = d["td"]
        self.assertEqual(td["date"], ds(TODAY))
        self.assertEqual(td["asof"], "18:45")
        self.assertEqual(len(td["acts"]), 2)
        act = td["acts"][0]
        self.assertEqual({s["k"] for s in act["splits"]}, {"RWD_RUN", "RWD_WALK", "RWD_STAND"})
        self.assertNotIn("cad1", act["series"])
        self.assertEqual(act["teLabel"], "aerobic base")
        self.assertIsNotNone(td["stressMix"]["low"])

    def test_empty_data_folder(self):
        html, d, _ = self.build()
        self.assertEqual(d["acts"], [])
        self.assertEqual(d["days"], [])
        self.assertIsNone(d["fitAge"])
        self.assertEqual(d["zones"], [None] * 6)
        self.assertEqual(d["td"]["acts"], [])
        self.assertIsNone(d["td"]["sleepH"])
        self.assertIsNone(d["td"]["sleepStages"])

    def test_odd_inputs(self):
        self.write("fitness_age.json", [1, 2])          # not a dict
        self.write("hr_zones.json", {"not": "a list"})
        (self.data / "daily.json").write_text("{broken", encoding="utf-8")
        weird = make_activity(1, datetime.combine(TODAY, datetime.min.time()), "walking",
                              activityName=None, distance=None, duration=None, elevationGain=None)
        weird["activityType"] = None
        no_time = make_activity(2, datetime(2026, 1, 1), "walking")
        no_time["startTimeLocal"] = None
        self.write("activities.json", {"1": weird, "2": no_time})
        self.write("weight.json", {"2026-01-01": {"latestWeight": {}}})
        self.write("sleep.json", {ds(TODAY): {"sleepTimeSeconds": None}})
        _, d, _ = self.build()
        self.assertEqual(len(d["acts"]), 1)
        self.assertEqual(d["acts"][0]["ty"], "activity")
        self.assertEqual(d["acts"][0]["n"], "")
        self.assertEqual(d["weight"], [])
        self.assertEqual(d["sleep"], [])
        self.assertIsNone(d["fitAge"])
        self.assertEqual(d["td"]["acts"][0]["series"], {})   # no detail downloaded

    def test_html_in_names_cannot_break_out_of_the_script(self):
        evil = make_activity(1, datetime.combine(TODAY, datetime.min.time()), "walking",
                             activityName="</script><script>alert(1)</script>")
        self.write("activities.json", {"1": evil})
        html, d, _ = self.build()
        script = html.split("<script>", 1)[1]
        self.assertNotIn("</script><script>alert", script.split("</script>")[0])
        self.assertEqual(d["acts"][0]["n"], evil["activityName"])

    def test_build_today_without_saved_today(self):
        write_dataset(self, with_today=False)
        _, d, _ = self.build()
        self.assertIsNone(d["td"]["acts"][0]["series"].get("hr"))
        self.assertEqual(d["td"]["hr"], [])

    def test_today_uses_saved_date(self):
        yesterday = TODAY - timedelta(days=1)
        self.write("today.json", {"date": ds(yesterday), "hr": [[0, 60]], "bb": [], "acts": {}})
        _, d, _ = self.build()
        self.assertEqual(d["td"]["date"], ds(yesterday))
        self.assertIsNone(d["td"]["asof"])

    def test_output_paths_from_environment(self):
        target = self.root / "nested" / "out.html"
        data = self.root / "other-data"
        with mock.patch.dict(os.environ, {"GARMIN_DATA_DIR": str(data), "DASHBOARD_OUT": str(target)}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            runpy.run_path(str(SRC / "build.py"), run_name="__main__")
        self.assertTrue(target.exists())
        self.assertFalse(target.with_suffix(".tmp").exists())

    def test_template_is_self_contained(self):
        # Nothing is loaded from the internet: no external scripts, styles or fonts.
        self.assertIsNone(re.search(r"<(script|link)[^>]+(src|href)=", build.TEMPLATE))
        self.assertNotIn("http://", build.TEMPLATE.replace("http://localhost", ""))


if __name__ == "__main__":
    unittest.main()
