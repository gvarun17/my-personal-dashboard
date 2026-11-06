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
    line = next(ln for ln in html.splitlines() if ln.startswith("const D = "))
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
        td = d["tdays"][0]
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
        self.assertEqual(d["tdays"][0]["acts"], [])
        self.assertIsNone(d["tdays"][0]["sleepH"])
        self.assertIsNone(d["tdays"][0]["sleepStages"])

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
        self.assertEqual(d["tdays"][0]["acts"][0]["series"], {})   # no detail downloaded

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
        self.assertTrue(d["tdays"][0]["acts"][0]["series"].get("hr"))   # graphs from the details cache
        self.assertEqual(d["tdays"][0]["hr"], [])
        for p in (self.data / "details").iterdir():
            p.unlink()
        _, d, _ = self.build()
        self.assertIsNone(d["tdays"][0]["acts"][0]["series"].get("hr"))

    def test_earlier_days_for_the_date_switcher(self):
        write_dataset(self)
        yesterday = ds(TODAY - timedelta(days=1))
        self.assertEqual(len(list((self.data / "days").glob("*.json"))), 6)   # saved by the download
        (self.data / "days" / f"{ds(TODAY - timedelta(days=2))}.json").unlink()
        (self.data / "days" / f"{yesterday}.json").write_text(
            json.dumps({"date": "ignored", "hr": [[60, 55]], "bb": [[60, 80]], "rhr7": 58, "acts": {}}), encoding="utf-8")
        _, d, _ = self.build()
        days = d["tdays"]
        self.assertEqual(len(days), build.HISTORY_DAYS)
        self.assertEqual([x["date"] for x in days[:2]], [ds(TODAY), yesterday])
        self.assertEqual((days[1]["hr"], days[1]["rhr7"]), ([[60, 55]], 58))
        self.assertEqual([x["graphs"] for x in days[:8]], [True] * build.GRAPH_DAYS + [False])
        self.assertEqual(days[2]["hr"], [])                                  # no saved file for that day
        self.assertTrue(days[2]["acts"])                                      # its activities still listed
        self.assertTrue(all(x["date"] == ds(TODAY - timedelta(days=i)) for i, x in enumerate(days)))
        self.assertIn("cal", d["days"][-1])

    def test_today_uses_saved_date(self):
        yesterday = TODAY - timedelta(days=1)
        self.write("today.json", {"date": ds(yesterday), "hr": [[0, 60]], "bb": [], "acts": {}})
        _, d, _ = self.build()
        self.assertEqual(d["tdays"][0]["date"], ds(yesterday))
        self.assertIsNone(d["tdays"][0]["asof"])

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
