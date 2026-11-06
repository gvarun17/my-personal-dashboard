import unittest
from datetime import date, timedelta

import coach

DAY = date(2026, 11, 6)          # a Friday
D = DAY.isoformat()


def iso(d):
    return d.isoformat()


def act(d, s, km):
    return {"d": iso(d) if isinstance(d, date) else d, "s": s, "km": km}


class SignalTests(unittest.TestCase):
    def test_hrv_from_garmin_status(self):
        for status, state in (("BALANCED", "good"), ("UNBALANCED", "ok"), ("LOW", "poor"), ("poor", "poor")):
            s = coach.hrv_signal({"lastNightAvg": 40, "status": status})
            self.assertEqual(s["state"], state, status)
            self.assertEqual(s["value"], "40 ms")

    def test_hrv_against_the_weekly_average_while_garmin_learns(self):
        self.assertEqual(coach.hrv_signal({"lastNightAvg": 40, "weeklyAvg": 41, "status": "NONE"})["state"], "good")
        self.assertEqual(coach.hrv_signal({"lastNightAvg": 36, "weeklyAvg": 40})["state"], "ok")
        s = coach.hrv_signal({"lastNightAvg": 30, "weeklyAvg": 40})
        self.assertEqual(s["state"], "poor")
        self.assertIn("Well below your 7-day average (40 ms)", s["note"])

    def test_hrv_missing_or_no_reference(self):
        self.assertEqual(coach.hrv_signal({})["state"], "none")
        self.assertEqual(coach.hrv_signal({"lastNightAvg": True})["state"], "none")   # not a number
        s = coach.hrv_signal({"lastNightAvg": 40, "status": "NONE"})
        self.assertEqual((s["state"], s["note"]), ("none", "Still learning your normal range"))

    def test_sleep_by_score_then_hours(self):
        def night(hours, score=None):
            s = {"sleepTimeSeconds": hours * 3600}
            if score is not None:
                s["sleepScores"] = {"overall": {"value": score}}
            return s
        self.assertEqual(coach.sleep_signal(night(7, 85))["state"], "good")
        self.assertEqual(coach.sleep_signal(night(7, 70))["state"], "ok")
        self.assertEqual(coach.sleep_signal(night(7, 40))["state"], "poor")
        self.assertEqual(coach.sleep_signal(night(7.5))["state"], "good")
        self.assertEqual(coach.sleep_signal(night(6.2))["state"], "ok")
        s = coach.sleep_signal(night(4))
        self.assertEqual((s["state"], s["value"]), ("poor", "4h 00m"))
        self.assertEqual(coach.sleep_signal({})["state"], "none")
        self.assertEqual(coach.sleep_signal({"sleepTimeSeconds": 0, "sleepScores": None})["state"], "none")

    def test_resting_heart_rate_against_your_usual(self):
        usual = [60] * 10
        self.assertEqual(coach.rhr_signal(61, usual)["state"], "good")
        self.assertEqual(coach.rhr_signal(64, usual)["state"], "ok")
        s = coach.rhr_signal(68, usual)
        self.assertEqual(s["state"], "poor")
        self.assertIn("8 bpm above your usual 60", s["note"])
        self.assertEqual(coach.rhr_signal(None, usual)["state"], "none")
        s = coach.rhr_signal(60, [60] * 3)
        self.assertEqual((s["state"], s["value"]), ("none", "60 bpm"))

    def test_body_battery(self):
        self.assertEqual(coach.battery_signal(90)["state"], "good")
        self.assertEqual(coach.battery_signal(60)["state"], "ok")
        self.assertEqual(coach.battery_signal(20)["state"], "poor")
        self.assertEqual(coach.battery_signal(None)["state"], "none")


class ReadinessTests(unittest.TestCase):
    def data(self, rhr=60, bb=85, score=85, hrv="BALANCED"):
        daily = {iso(DAY - timedelta(days=i)): {"heartRate": {"restingValue": 60}} for i in range(1, 31)}
        daily[iso(DAY - timedelta(days=3))] = {"heartRate": {"restingValue": None}}   # a gap
        daily[D] = {"heartRate": {"restingValue": rhr}, "bodyBattery": {"maxValue": bb}}
        sleep = {D: {"sleepTimeSeconds": 8 * 3600, "sleepScores": {"overall": {"value": score}}}}
        hrv = {D: {"lastNightAvg": 45, "status": hrv}}
        return daily, sleep, hrv

    def test_good_day(self):
        r = coach.readiness(D, *self.data())
        self.assertEqual(r["level"], "go")
        self.assertEqual(r["title"], "Good day to train")
        self.assertEqual([s["k"] for s in r["signals"]], ["hrv", "sleep", "rhr", "bb"])

    def test_one_poor_or_two_ok_means_easy(self):
        self.assertEqual(coach.readiness(D, *self.data(bb=20))["level"], "easy")
        self.assertEqual(coach.readiness(D, *self.data(bb=60, score=70))["level"], "easy")
        self.assertEqual(coach.readiness(D, *self.data(bb=60))["level"], "go")    # one ok is fine

    def test_two_poor_means_rest(self):
        r = coach.readiness(D, *self.data(rhr=70, bb=20))
        self.assertEqual(r["level"], "rest")
        self.assertIn("still recovering", r["advice"])

    def test_not_enough_data(self):
        r = coach.readiness(D, {}, {}, {})
        self.assertIsNone(r["level"])
        self.assertIn("Wear your watch overnight", r["advice"])
        self.assertTrue(all(s["state"] == "none" for s in r["signals"]))


class WeeklyGoalTests(unittest.TestCase):
    def days(self, weeks=4, im=40, img=150):
        start = coach.monday(DAY) - timedelta(weeks=weeks - 1)
        out, d = [], start
        while d <= DAY:
            out.append({"d": iso(d), "im": im, "img": img})
            d += timedelta(days=1)
        return out

    def test_garmin_goal_by_default_and_streak(self):
        g = coach.weekly_goal(self.days(), [], {}, DAY)
        self.assertEqual((g["type"], g["target"], g["source"], g["unit"]), ("minutes", 150, "garmin", "min"))
        self.assertEqual(g["value"], 200)            # Monday to Friday at 40 a day
        self.assertEqual((g["need"], g["left"]), (0, 3))
        self.assertEqual((g["streak"], g["best"], g["hits"], g["of"]), (4, 4, 3, 3))
        self.assertEqual(len(g["weeks"]), 4)

    def test_suggested_goal_when_garmin_has_none(self):
        g = coach.weekly_goal(self.days(img=None), [], {}, DAY)
        self.assertEqual((g["target"], g["source"]), (150, "default"))
        g = coach.weekly_goal([], [], {"goal_type": "runs"}, DAY)
        self.assertEqual((g["target"], g["source"], g["value"]), (3, "default", 0))
        self.assertEqual((g["streak"], g["of"], len(g["weeks"])), (0, 0, 1))

    def test_your_goal_not_reached_yet(self):
        g = coach.weekly_goal(self.days(im=10), [], {"goal_type": "minutes", "goal_value": 300}, DAY)
        self.assertEqual((g["source"], g["value"], g["need"]), ("you", 50, 250))
        self.assertEqual(g["perDay"], 83.3)
        self.assertEqual((g["streak"], g["best"]), (0, 0))

    def test_distance_and_run_goals(self):
        mon = coach.monday(DAY)
        acts = [act(mon, "run", 5.25), act(mon + timedelta(days=1), "walk", 3.0),
                act(mon + timedelta(days=2), "run", 4.0), act(mon - timedelta(days=7), "run", 30)]
        g = coach.weekly_goal([], acts, {"goal_type": "km", "goal_value": 15}, DAY)
        self.assertEqual((g["value"], g["need"], g["unit"]), (12.2, 2.8, "km"))
        self.assertEqual((g["streak"], g["best"]), (1, 1))                # last week's 30 km
        g = coach.weekly_goal([], acts, {"goal_type": "run_km", "goal_value": 9}, DAY)
        self.assertEqual((g["value"], g["streak"]), (9.2, 2))
        g = coach.weekly_goal([], acts, {"goal_type": "runs", "goal_value": 3}, DAY)
        self.assertEqual((g["value"], g["need"], g["label"]), (2, 1, "Number of runs"))

    def test_streak_breaks_on_a_missed_week_and_best_is_kept(self):
        mon = coach.monday(DAY)
        acts = [act(mon - timedelta(weeks=w), "run", 5) for w in (1, 2, 4, 5, 6)]
        g = coach.weekly_goal([], acts, {"goal_type": "runs", "goal_value": 1}, DAY)
        self.assertEqual((g["streak"], g["best"]), (2, 3))

    def test_unknown_goal_type_falls_back_to_minutes(self):
        g = coach.weekly_goal(self.days(), [], {"goal_type": "laps", "goal_value": "x"}, DAY)
        self.assertEqual((g["type"], g["source"]), ("minutes", "garmin"))

    def test_history_is_capped_at_two_years(self):
        g = coach.weekly_goal([{"d": "2015-01-01", "im": 500, "img": 150}], [], {}, DAY)
        self.assertEqual(len(g["weeks"]), 8)
        self.assertEqual(g["of"], 12)


class MilestoneTests(unittest.TestCase):
    def test_no_runs_yet_suggests_run_walk(self):
        m = coach.milestones([act(DAY, "walk", 3), act(DAY, "walk", 2)])
        self.assertEqual(m["next"], 0)
        self.assertIsNone(m["longest"])
        self.assertIn("Your 2 walks are already a good base", m["tip"])
        self.assertIn("chat the whole time", coach.milestones([])["tip"])

    def test_first_runs_and_weeks_to_the_next_step(self):
        acts = [act("2026-10-01", "run", 0.6), act("2026-10-10", "run", 1.2), act("2026-10-20", "run", 2.0)]
        m = coach.milestones(acts)
        dates = {s["k"]: s["date"] for s in m["steps"]}
        self.assertEqual((dates["first"], dates["1k"], dates["5k"]), ("2026-10-01", "2026-10-10", None))
        self.assertEqual((m["next"], m["togo"], m["weeks"], m["runs"]), (2, 3.0, 10, 3))
        self.assertEqual(m["longest"], {"km": 2.0, "d": "2026-10-20"})
        self.assertIn("about 10 weeks", m["tip"])

    def test_gps_tolerance_and_one_week_away(self):
        m = coach.milestones([act("2026-10-01", "run", 4.92), act("2026-10-05", "run", 9.5)])
        self.assertEqual(m["steps"][2]["date"], "2026-10-01")     # 4.92 km counts as 5K
        self.assertEqual((m["next"], m["weeks"]), (3, 1))
        self.assertIn("about 1 week.", m["tip"])

    def test_runs_without_distance(self):
        m = coach.milestones([act("2026-10-01", "run", 0), {"d": "2026-10-02", "s": "run", "km": None}])
        self.assertEqual((m["next"], m["togo"]), (1, 1.0))
        self.assertNotIn("weeks", m)
        self.assertIn("run-walk", m["tip"])

    def test_triathlon_steps_need_all_three_on_one_day(self):
        acts = [act("2026-06-01", "run", 42.2),
                act("2026-07-01", "swim", 1.9), act("2026-07-01", "cycle", 90), act("2026-07-02", "run", 21.1),
                act("2026-08-01", "swim", 1.0), act("2026-08-01", "swim", 0.9), act("2026-08-01", "cycle", 92),
                act("2026-08-01", "run", 21.0), act("2026-08-01", "walk", 5)]
        m = coach.milestones(acts)
        self.assertEqual(m["steps"][6]["date"], "2026-08-01")
        self.assertEqual(m["next"], 7)
        self.assertIn("Longest swim so far 1.9 km, longest ride 92 km", m["tip"])

    def test_whole_ladder_done(self):
        acts = [act("2026-01-01", "run", 42.2), act("2026-09-01", "swim", 3.8),
                act("2026-09-01", "cycle", 180), act("2026-09-01", "run", 42.2)]
        m = coach.milestones(acts)
        self.assertIsNone(m["next"])
        self.assertIn("whole ladder", m["tip"])


class BuildCoachTests(unittest.TestCase):
    def test_all_three_cards(self):
        c = coach.build_coach(D, {}, {}, {}, [], [], {}, today=DAY)
        self.assertEqual(set(c), {"ready", "goal", "ladder"})
        self.assertEqual(coach.build_coach(D, {}, {}, {}, [], [], {})["goal"]["type"], "minutes")


if __name__ == "__main__":
    unittest.main()
