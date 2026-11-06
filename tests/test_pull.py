import io
import json
import runpy
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import garmin_api
import pull
from tests.helpers import (TODAY, FakeGarmin, TempDataCase, ds, make_activities, make_activity,
                           make_details, make_summary)

ROOT = Path(__file__).resolve().parent.parent
HTTP500 = garmin_api.HTTPStatus(500)


def quiet():
    """Silence progress bars and prints."""
    return mock.patch("sys.stdout", new_callable=io.StringIO)


class HelperTests(TempDataCase):
    def test_dates(self):
        self.assertEqual(pull.d2s(date(2026, 1, 5)), "2026-01-05")
        self.assertEqual(pull.s2d("2026-01-05 07:30:00"), date(2026, 1, 5))

    def test_load_and_save(self):
        self.assertEqual(pull.load("missing.json", {"d": 1}), {"d": 1})
        (self.data / "bad.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(pull.load("bad.json", []), [])
        pull.save("ok.json", {"a": [1, 2]})
        self.assertEqual(pull.load("ok.json", None), {"a": [1, 2]})
        self.assertFalse((self.data / "ok.tmp").exists())

    def test_bar(self):
        with quiet() as out:
            pull.bar("Label", 5, 10, "half")
            pull.bar("Label", 5, 0)
        text = out.getvalue()
        self.assertIn("50%", text)
        self.assertIn("100%", text)
        self.assertIn("half", text)


class ActivityTests(TempDataCase):
    def run_pull(self, fake, start, backfill):
        with mock.patch.object(pull, "get", fake), quiet() as out:
            acts = pull.pull_activities(start, backfill)
        return acts, out.getvalue()

    def test_first_run_stops_at_start_date(self):
        fake = FakeGarmin(activities=make_activities(n_days=70))
        acts, _ = self.run_pull(fake, TODAY - timedelta(days=30), True)
        dates = {a["startTimeLocal"][:10] for a in acts.values()}
        self.assertTrue(all(d >= ds(TODAY - timedelta(days=30)) for d in dates))
        self.assertEqual(self.read("activities.json").keys(), acts.keys())

    def test_whole_list_until_short_page(self):
        fake = FakeGarmin(activities=make_activities(n_days=70))
        acts, out = self.run_pull(fake, date(1990, 1, 1), True)
        self.assertEqual(len(acts), len(fake.activities))
        self.assertIn(f"{len(acts)} new", out)

    def test_incremental_stops_at_known_activity(self):
        all_acts = make_activities(n_days=70)
        self.write("activities.json", {str(a["activityId"]): a for a in all_acts[3:]})
        fake = FakeGarmin(activities=all_acts)
        acts, out = self.run_pull(fake, date(1990, 1, 1), False)
        self.assertEqual(len(acts), len(all_acts))
        self.assertIn("3 new", out)
        self.assertEqual(len(fake.calls), 1)

    def test_backfill_refreshes_known_activities(self):
        all_acts = make_activities(n_days=10)
        self.write("activities.json", {str(a["activityId"]): a for a in all_acts})
        acts, out = self.run_pull(FakeGarmin(activities=all_acts), date(1990, 1, 1), True)
        self.assertIn("0 new", out)

    def test_activity_without_start_time(self):
        weird = make_activity(1, datetime(2026, 1, 1), "walking")
        weird["startTimeLocal"] = None
        acts, _ = self.run_pull(FakeGarmin(activities=[weird]), date(1990, 1, 1), True)
        self.assertIn("1", acts)

    def test_empty_account(self):
        acts, out = self.run_pull(FakeGarmin(activities=[]), TODAY - timedelta(days=5), True)
        self.assertEqual(acts, {})
        self.assertIn("0 total", out)

    def test_too_big_page_is_shrunk(self):
        fake = FakeGarmin(activities=make_activities(n_days=4))
        real = fake.__call__

        def flaky(path):
            if "limit=20" in path:
                raise HTTP500
            return real(path)
        acts, _ = self.run_pull(flaky, date(1990, 1, 1), True)
        self.assertEqual(len(acts), len(fake.activities))

    def test_unreturnable_activity_is_skipped(self):
        acts_list = make_activities(n_days=4)

        def get(path):
            if "start=0&" in path:
                raise HTTP500
            q = dict(kv.split("=") for kv in path.split("?", 1)[1].split("&"))
            s, n = int(q["start"]), int(q["limit"])
            return acts_list[s:s + n]
        acts, out = self.run_pull(get, date(1990, 1, 1), True)
        self.assertIn("skipped an activity", out)
        self.assertEqual(len(acts), len(acts_list) - 1)

    def test_gives_up_after_repeated_failures(self):
        get = mock.Mock(side_effect=HTTP500)
        acts, out = self.run_pull(get, date(1990, 1, 1), True)
        self.assertIn("stopping here", out)
        self.assertEqual(get.call_count, 4 * pull.MAX_SKIPS)

    def test_other_errors_are_raised(self):
        with self.assertRaises(garmin_api.HTTPStatus):
            self.run_pull(mock.Mock(side_effect=garmin_api.HTTPStatus(403)), date(1990, 1, 1), True)


class RangeTests(TempDataCase):
    def fetch(self, get, ranges, chunk=10, key=lambda i: i.get("d")):
        store = {}
        with mock.patch.object(pull, "get", get), quiet():
            pull.fetch_ranges("Test", ranges, chunk, lambda s, e: f"/r?{ds(s)}&{ds(e)}",
                              lambda r: r, key, store)
        return store

    @staticmethod
    def days_in(path):
        a, b = path.split("?")[1].split("&")
        s, e = datetime.strptime(a, "%Y-%m-%d").date(), datetime.strptime(b, "%Y-%m-%d").date()
        return s, e

    def test_chunks_and_merges(self):
        def get(path):
            s, e = self.days_in(path)
            return [{"d": ds(s + timedelta(days=i))} for i in range((e - s).days + 1)]
        s = date(2026, 1, 1)
        store = self.fetch(get, [(s, s + timedelta(days=24))])
        self.assertEqual(len(store), 25)

    def test_too_big_range_is_halved(self):
        def get(path):
            s, e = self.days_in(path)
            if (e - s).days + 1 > 3:
                raise HTTP500
            return [{"d": ds(s)}]
        store = self.fetch(get, [(date(2026, 1, 1), date(2026, 1, 10))])
        self.assertEqual(len(store), 5)   # 10 -> 5 -> 2-day chunks: 1st, 3rd, 5th, 7th, 9th

    def test_unreturnable_days_are_skipped(self):
        for code in (500, 404, 204):
            def get(path, code=code):
                s, _ = self.days_in(path)
                if s.day == 2:
                    raise garmin_api.HTTPStatus(code)
                return [{"d": ds(s)}]
            store = self.fetch(get, [(date(2026, 1, 1), date(2026, 1, 3))], chunk=1)
            self.assertEqual(sorted(store), ["2026-01-01", "2026-01-03"])

    def test_other_errors_are_raised(self):
        with self.assertRaises(garmin_api.HTTPStatus):
            self.fetch(mock.Mock(side_effect=garmin_api.HTTPStatus(403)), [(date(2026, 1, 1), date(2026, 1, 1))])

    def test_empty_responses_and_keys(self):
        store = self.fetch(lambda p: None, [(date(2026, 1, 1), date(2026, 1, 2))])
        self.assertEqual(store, {})
        store = self.fetch(lambda p: [{"d": None}, {"d": "2026-01-01T00:00"}], [(date(2026, 1, 1), date(2026, 1, 1))])
        self.assertEqual(list(store), ["2026-01-01"])
        self.assertEqual(self.fetch(lambda p: [], []), {})

    def test_ranges_for(self):
        today = date(2026, 6, 30)
        start = date(2026, 1, 1)
        self.assertEqual(pull.ranges_for("daily", start, today, {}), [(start, today)])
        state = {"daily": {"start": "2026-03-01", "through": "2026-06-20"}}
        self.assertEqual(pull.ranges_for("daily", date(2026, 3, 1), today, state),
                         [(date(2026, 6, 17), today)])
        self.assertEqual(pull.ranges_for("daily", start, today, state),
                         [(start, date(2026, 2, 28)), (date(2026, 6, 17), today)])
        state = {"daily": {"start": "2026-06-29", "through": "2026-06-30"}}
        self.assertEqual(pull.ranges_for("daily", date(2026, 6, 29), today, state)[0][0], date(2026, 6, 29))

    def test_dataset_definitions(self):
        s, e = date(2026, 1, 1), date(2026, 1, 31)
        by_name = {d[0]: d for d in pull.DATASETS}
        self.assertIn("startDate=2026-01-01&endDate=2026-01-31", by_name["daily"][3](s, e))
        self.assertEqual(by_name["daily"][4]({"data": [1]}), [1])
        self.assertIsNone(by_name["daily"][4](None))
        self.assertEqual(by_name["sleep"][4]([1]), [1])
        self.assertEqual(by_name["sleep"][4]({"x": 1}), [])
        self.assertIsNone(by_name["sleep"][5]({"calendarDate": "d", "sleepTimeSeconds": 0}))
        self.assertEqual(by_name["sleep"][5]({"calendarDate": "d", "sleepTimeSeconds": 5}), "d")
        self.assertTrue(by_name["hrv"][3](s, e).endswith("/2026-01-01/2026-01-31"))
        self.assertIsNone(by_name["hrv"][5]({"calendarDate": "d"}))
        self.assertEqual(by_name["weight"][5]({"summaryDate": "w"}), "w")


class DetailTests(TempDataCase):
    def detail(self, get):
        with mock.patch.object(pull, "get", get):
            return pull.activity_detail(123)

    def test_parses_and_caches(self):
        def get(path):
            return make_details(n=5, with_elevation=True) if "/details?" in path else make_summary()
        out = self.detail(get)
        self.assertEqual(set(out["series"]), {"t", "dist", "hr", "spd", "cad", "cad1", "pw", "rr", "el"})
        self.assertEqual(len(out["series"]["hr"]), 5)
        self.assertEqual(out["summary"]["steps"], 3000)
        self.assertTrue((self.data / "details" / "123.json").exists())
        # second call comes from the cache, no network
        self.assertEqual(self.detail(mock.Mock(side_effect=AssertionError("no network"))), out)

    def test_short_rows_and_empty_metrics(self):
        raw = {"metricDescriptors": [{"metricsIndex": 0, "key": "sumElapsedDuration"},
                                     {"metricsIndex": 1, "key": "directPower"},
                                     {"metricsIndex": 5, "key": "directHeartRate"}],
               "activityDetailMetrics": [{"metrics": [0.0, 0.0]}, {"metrics": [10.0, None]}, {}]}
        out = self.detail(lambda p: raw if "/details?" in p else {})
        self.assertEqual(out["series"], {"t": [0.0, 10.0, None]})
        self.assertEqual(out["summary"], {})
        self.assertEqual(out["splits"], [])

    def test_shrinks_on_500_then_succeeds(self):
        def get(path):
            if "maxChartSize=1000" in path:
                raise HTTP500
            return make_details(n=3) if "/details?" in path else make_summary()
        self.assertIn("hr", self.detail(get)["series"])

    def test_not_ready_yet_is_not_cached(self):
        def get(path):
            if "/details?" in path:
                raise HTTP500
            raise garmin_api.HTTPStatus(404)
        out = self.detail(get)
        self.assertEqual(out, {"series": {}})
        self.assertFalse((self.data / "details" / "123.json").exists())
        self.assertEqual(self.detail(lambda p: {"activityDetailMetrics": []} if "/details?" in p else {})["series"], {})

    def test_other_errors_are_raised(self):
        with self.assertRaises(garmin_api.HTTPStatus):
            self.detail(mock.Mock(side_effect=garmin_api.HTTPStatus(403)))


class TodayTests(TempDataCase):
    def run_today(self, fake, acts):
        with mock.patch.object(pull, "get", fake), quiet() as out:
            pull.pull_today(TODAY, acts)
        return self.read("today.json"), out.getvalue()

    def acts(self):
        return {str(a["activityId"]): a for a in make_activities(n_days=3)}

    def test_full_day(self):
        today, out = self.run_today(FakeGarmin(), self.acts())
        self.assertEqual(today["date"], ds(TODAY))
        self.assertEqual(today["off"], 5.5 * 3600 * 1000)
        self.assertEqual(today["hr"][0], [2.0, 71])   # first reading has no value
        self.assertTrue(all(v for _, v in today["hr"]))
        self.assertEqual(len(today["bb"]), 12)
        self.assertEqual(today["rhr"], 61)
        self.assertEqual(len(today["acts"]), 2)
        self.assertIn("2 activities today", out)

    def test_missing_wellness_and_detail_errors(self):
        fake = FakeGarmin(fail={"/wellness-service": garmin_api.HTTPStatus(404),
                                "/activity-service/activity/9500/details": garmin_api.HTTPStatus(403)})
        today, out = self.run_today(fake, self.acts())
        self.assertEqual(today["hr"], [])
        self.assertIn("could not get details", out)
        self.assertEqual(len(today["acts"]), 1)

    def test_no_timestamps_and_no_body_battery(self):
        def get(path):
            if "dailyHeartRate" in path:
                return {"heartRateValues": [[1, 60]]}
            if "bodyBattery" in path:
                return []
            raise AssertionError(path)
        one = make_activity(1, datetime.combine(TODAY - timedelta(days=1), datetime.min.time()), "walking")
        today, out = self.run_today(get, {"1": one})
        self.assertEqual(today["hr"], [])
        self.assertEqual(today["off"], 0)
        self.assertEqual(today["bb"], [])
        self.assertIn("0 activities today", out)

    def test_one_activity_wording(self):
        acts = {k: v for k, v in self.acts().items() if k == "9500"}
        _, out = self.run_today(FakeGarmin(), acts)
        self.assertIn("1 activity today", out)


class MainTests(TempDataCase):
    def run_main(self, fake, *args):
        with mock.patch.object(pull, "get", fake), mock.patch("sys.argv", ["pull.py", *args]), quiet() as out:
            pull.main()
        return out.getvalue()

    def test_first_run_then_incremental(self):
        fake = FakeGarmin()
        out = self.run_main(fake)
        self.assertIn("Done. What came back:", out)
        state = self.read("state.json")
        self.assertEqual(state["start"], ds(TODAY - timedelta(days=365)))
        for name in ("activities", "daily", "sleep", "hrv", "weight", "today", "fitness_age", "hr_zones"):
            self.assertTrue((self.data / f"{name}.json").exists(), name)
        first_calls = len(fake.calls)
        fake2 = FakeGarmin()
        self.run_main(fake2)
        self.assertLess(len(fake2.calls), first_calls)

    def test_full_history_with_and_without_state(self):
        out = self.run_main(FakeGarmin(), "--full")
        self.assertEqual(self.read("state.json")["start"], min(a["startTimeLocal"][:10] for a in FakeGarmin().activities))
        self.assertIn("Activities:", out)
        self.run_main(FakeGarmin(), "--full")

    def test_full_history_of_empty_account(self):
        out = self.run_main(FakeGarmin(activities=[]), "--full")
        self.assertEqual(self.read("state.json")["start"], ds(TODAY - timedelta(days=5 * 365)))
        self.assertIn("Activities:          0", out)

    def test_extending_back_in_time(self):
        self.run_main(FakeGarmin())
        state = self.read("state.json")
        state["start"] = ds(TODAY - timedelta(days=10))
        for k in ("daily", "sleep", "hrv", "weight"):
            state[k]["start"] = ds(TODAY - timedelta(days=10))
        self.write("state.json", state)
        self.run_main(FakeGarmin(), "--full")
        self.assertLess(self.read("state.json")["start"], ds(TODAY - timedelta(days=10)))

    def test_optional_lookups_may_fail(self):
        fake = FakeGarmin(fail={"/fitnessage-service": garmin_api.HTTPStatus(404),
                                "/biometric-service": garmin_api.GarminError("x")})
        self.run_main(fake)
        self.assertFalse((self.data / "fitness_age.json").exists())


class ScriptTests(TempDataCase):
    def run_script(self, side_effect):
        with mock.patch.object(garmin_api, "get", side_effect=side_effect), \
                mock.patch("sys.argv", ["pull.py"]), quiet() as out:
            with self.assertRaises(SystemExit) as cm:
                runpy.run_path(str(ROOT / "pull.py"), run_name="__main__")
        self.assertEqual(cm.exception.code, 1)
        return out.getvalue()

    def test_stops_cleanly_on_garmin_error(self):
        self.assertIn("Stopped: Garmin said no", self.run_script(garmin_api.GarminError("Garmin said no")))

    def test_stops_cleanly_on_ctrl_c(self):
        self.assertIn("picks up where it left off", self.run_script(KeyboardInterrupt))

    def test_runs_as_script(self):
        with mock.patch.object(garmin_api, "get", FakeGarmin()), mock.patch("sys.argv", ["pull.py"]), quiet() as out:
            runpy.run_path(str(ROOT / "pull.py"), run_name="__main__")
        self.assertIn("Done.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
