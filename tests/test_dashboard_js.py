"""Builds dashboards from several made-up datasets and runs their JavaScript in
Node.js (tests/js/dashboard.test.cjs), hovering and clicking everything.

Skipped when Node.js 20+ is not installed. With Node.js 22+, coverage of the
dashboard code is enforced (JS_MIN). Set JS_COVERAGE=1 to print the report."""
import io
import json
import os
import re
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import build
import settings
from tests.helpers import TODAY, TempDataCase, ds, make_activity, make_details, write_dataset

HERE = Path(__file__).resolve().parent
JS_MIN = {"lines": 100, "functions": 100, "branches": 93}
NODE = shutil.which("node")


def node_major():
    if not NODE:
        return 0
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout
    m = re.match(r"v(\d+)", out.strip())
    return int(m.group(1)) if m else 0


@unittest.skipUnless(node_major() >= 20, "Node.js 20+ is needed for the dashboard JavaScript tests")
class DashboardJavaScriptTests(TempDataCase):
    def build_html(self, name):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            build.build()
        target = self.root / f"{name}.html"
        shutil.copy(self.out, target)
        return str(target)

    def reset_data(self):
        for p in self.data.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    def write_sparse(self):
        today, now = ds(TODAY), datetime.combine(TODAY, datetime.min.time())
        lift = make_activity(1, now.replace(hour=7), "strength_training", km=0, minutes=0.5)
        for k in ("averageHR", "maxHR", "activityTrainingLoad", "aerobicTrainingEffect", "calories",
                  "elevationGain", "steps"):
            lift[k] = None
        for i in range(1, 6):
            lift[f"hrTimeInZone_{i}"] = 0
        walk = make_activity(2, now.replace(hour=8), "walking", km=0.4, minutes=6, averageSpeed=None)
        old = make_activity(3, now - timedelta(days=400), "golf", km=0, minutes=200)
        self.write("activities.json", {"1": lift, "2": walk, "3": old})
        self.write("daily.json", {
            today: {"movement": {"steps": {"value": 120}}, "intensityMinutes": {"moderate": 0, "vigorous": 0},
                    "stress": {"avgLevel": -1}},
            ds(TODAY - timedelta(days=3)): {"movement": {"steps": {"value": 120}}, "heartRate": {"restingValue": 55}},
        })
        self.write("sleep.json", {ds(TODAY - timedelta(days=1)): {"sleepTimeSeconds": 3600}})
        self.write("hrv.json", {ds(TODAY - timedelta(days=20)): {"lastNightAvg": 40}})
        self.write("today.json", {"date": today, "hr": [[300, 70]], "bb": [], "acts": {
            "1": {"series": {"t": [0, 0], "hr": [None, None]}},
            "2": {"series": {"t": [0, 10, 20, 30, 40, 50, 60, 70], "hr": [None, 90, 95, None, 99, 101, 103, 104],
                             "spd": [0, 0.1, 1.5, 1.5, 1.6, 1.4, 1.5, 1.5], "el": [5, 5, 5, 5, 5, 5, 5, 5]}},
        }})

    def test_dashboard_javascript(self):
        scenarios = []

        # 1. Full year-ish of data, desktop, opened as a file, name set on the start page
        write_dataset(self)
        settings.save(name="Test Runner", tz="Europe/London", key="not-a-real-key")
        scenarios.append({"name": "full data, desktop, file", "html": self.build_html("full"),
                          "width": 1280, "protocol": "file:",
                          "expect": [["today", "Steps"], ["tiles", "Distance this week"], ["recs", "Longest distance"],
                                     ["ready", "Today's call"], ["ready", "Resting HR"], ["ladder", "Reached"],
                                     ["heroNote", "Goal from Garmin Connect"]],
                          "expectText": [["who", "Test Runner's training log"]]})

        # 2. Same data on a phone, served by server.py: a refresh is running when the page opens
        scenarios.append({"name": "full data, phone, web app", "html": scenarios[0]["html"], "width": 375,
                          "protocol": "http:", "status": "A", "expectReload": True, "expectButton": "Refresh"})

        # 3. Brand-new account: nothing downloaded yet; refresh reports an error
        self.reset_data()
        scenarios.append({"name": "empty account", "html": self.build_html("empty"), "width": 1100,
                          "protocol": "https:", "status": "B", "empty": True, "expectButton": "Refresh failed",
                          "storage": "blocked",                       # private browsing: nothing remembered
                          "expect": [["todayActs", "No activities yet today"]]})

        # 4. Rest day: no activity today, HRV baseline set, weekly goal not reached; server unreachable
        write_dataset(self, activities=[
            make_activity(1, datetime.combine(TODAY - timedelta(days=2), datetime.min.time()), "running", km=21.5),
            make_activity(2, datetime.combine(TODAY - timedelta(days=9), datetime.min.time()), "lap_swimming", km=1.5),
        ])
        hrv = self.read("hrv.json")
        for v in hrv.values():
            v["status"] = "BALANCED"
        self.write("hrv.json", hrv)
        daily = self.read("daily.json")
        for v in daily.values():
            if isinstance(v.get("intensityMinutes"), dict):
                v["intensityMinutes"].update(moderate=1, vigorous=0)
        self.write("daily.json", daily)
        settings.save(goal_type="runs", goal_value=3)          # your own goal: 3 runs a week
        scenarios.append({"name": "rest day, tablet", "html": self.build_html("rest"), "width": 760,
                          "protocol": "http:", "status": "down",
                          "expect": [["todayActs", "No activities yet today"], ["ladTip", "Next: Marathon."],
                                     ["heroNote", "more run"], ["ring", "of 3 runs"]],
                          "expectText": [["goalLab", "This week · number of runs"]]})

        # 5. Long bike ride today with elevation, plus an activity Garmin has no graphs for yet
        self.reset_data()
        now = datetime.combine(TODAY, datetime.min.time())
        ride = make_activity(10, now.replace(hour=6), "road_biking", km=42, minutes=95)
        walk = make_activity(11, now.replace(hour=12), "walking", km=1, minutes=10)
        write_dataset(self, activities=[walk, ride])
        today = self.read("today.json")
        long = make_details(n=420, with_elevation=True)
        today["acts"]["10"]["series"] = {k: [r["metrics"][i] for r in long["activityDetailMetrics"]]
                                         for k, i in (("t", 0), ("dist", 1), ("hr", 2), ("spd", 3), ("pw", 6),
                                                      ("rr", 7), ("el", 9))}
        today["acts"]["11"] = {"series": {}}
        self.write("today.json", today)
        settings.save(goal_type="km", goal_value=400)          # a goal far out of reach: no streak yet
        sleep = self.read("sleep.json")
        sleep[ds(TODAY)] = {"sleepTimeSeconds": 4 * 3600}     # short night, no sleep score
        self.write("sleep.json", sleep)
        scenarios.append({"name": "long ride today", "html": self.build_html("ride"), "width": 1440,
                          "protocol": "file:", "expect": [["act0c", "Speed"], ["act0c", "Elevation"], ["act1c", "no detailed graphs"],
                                                         ["heroNote", "Hit it this week to start a streak"],
                                                         ["ring", "of 400 km"], ["ready", "4h 00m"]]})

        # 6. Sparse, odd data: missing fields everywhere, single readings, tiny screen
        self.reset_data()
        self.write_sparse()
        scenarios.append({"name": "sparse data, small screen, opens on Health & strength",
                          "html": self.build_html("sparse"), "width": 320, "height": 120, "protocol": "file:",
                          "storage": {"view": "health"}, "expect": [["xTable", "Test strength_training 1"]]})

        manifest = self.root / "scenarios.json"
        manifest.write_text(json.dumps(scenarios), encoding="utf-8")
        cmd = [NODE, "--test", "--test-reporter=spec"]
        if node_major() >= 22:
            # Coverage of the dashboard code, enforced: every line and function
            # runs; branches stay at or above the level reached (rare display
            # fallbacks make up the rest).
            cmd += ["--experimental-test-coverage", f"--test-coverage-include={self.root.resolve().as_posix()}/*.js",
                    f"--test-coverage-lines={JS_MIN['lines']}", f"--test-coverage-functions={JS_MIN['functions']}",
                    f"--test-coverage-branches={JS_MIN['branches']}"]
        cmd.append(str(HERE / "js" / "dashboard.test.cjs"))
        env = {**os.environ, "DASHBOARD_SCENARIOS": str(manifest)}
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300, encoding="utf-8")
        if os.environ.get("JS_COVERAGE"):
            print(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"pass {len(scenarios)}", r.stdout)


if __name__ == "__main__":
    unittest.main()
