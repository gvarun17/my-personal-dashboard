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


def run(d, km, spd=None, aid=None, **extra):
    return {"d": d, "s": "run", "km": km, "spd": spd, "id": aid, "n": f"Run {aid}", **extra}


class PersonalBestTests(unittest.TestCase):
    def test_running_bests_and_new_badges(self):
        acts = [run("2026-10-01", 3.0, 2.5, 1), run("2026-10-20", 5.1, 2.4, 2, f1k=330),
                run("2026-11-05", 2.0, 2.9, 3), run("2026-11-06", 0.5, 4.0, 4),          # too short for pace
                {"d": "2026-10-02", "s": "walk", "km": 6, "id": 5, "n": "Walk"},
                {"d": "2026-11-03", "s": "strength", "km": 0, "min": 45, "id": 6, "n": "Gym"}]
        days = [{"d": "2026-10-05", "st": 12000, "im": 300, "rhr": 52}, {"d": "2026-10-06", "st": 9000, "im": 20, "rhr": 49},
                {"d": "2026-10-07", "st": None, "im": 0, "rhr": None}]
        sleep = {"2026-10-05": {"sleepScores": {"overall": {"value": 81}}}, "2026-10-06": {}}
        r = coach.personal_bests(acts, days, sleep, DAY)
        b = {x["k"]: x for x in r["bests"]}
        self.assertEqual((b["run_long"]["text"], b["run_long"]["aid"], b["run_long"]["new"]), ("5.1 km", 2, False))
        self.assertEqual((b["run_pace"]["text"], b["run_pace"]["aid"], b["run_pace"]["new"]), ("5:45 /km", 3, True))
        self.assertEqual((b["run_1k"]["text"], b["run_1k"]["sub"]), ("5:30", "fastest split"))
        self.assertEqual((b["run_5k"]["text"], b["run_5k"]["sub"], b["run_5k"]["aid"]), ("34:43", "whole-run average", 2))
        self.assertEqual((b["run_week"]["text"], b["run_week"]["d"]), ("5.1 km", "2026-10-19"))
        self.assertEqual(b["walk_long"]["text"], "6.0 km")
        self.assertEqual((b["strength_long"]["text"], b["strength_long"]["new"]), ("45 min", True))
        self.assertEqual(b["steps_day"]["text"], "12,000")
        self.assertEqual(b["im_week"]["text"], "320 min")
        self.assertEqual(b["rhr_low"]["text"], "49 bpm")
        self.assertEqual(b["sleep_best"]["text"], "81")
        self.assertNotIn("ride_long", b)
        self.assertEqual([(f["s"], f["d"]) for f in r["firsts"]],
                         [("run", "2026-10-01"), ("walk", "2026-10-02"), ("strength", "2026-11-03")])
        self.assertEqual([f["new"] for f in r["firsts"]], [False, False, True])

    def test_long_times_and_nothing_yet(self):
        acts = [run("2026-10-01", 42.2, 3.0, 1)]
        b = {x["k"]: x for x in coach.personal_bests(acts, [], {}, DAY)["bests"]}
        self.assertEqual(b["run_5k"]["text"], "27:47")
        self.assertEqual(coach._mmss(3 * 3600 + 65), "3:01:05")
        r = coach.personal_bests([], [{"d": "2026-10-01", "st": 0, "im": 0, "rhr": None}], {}, DAY)
        self.assertEqual(r, {"bests": [], "firsts": []})


def night(day, bed, wake, hours, score=None, stress=None, need=None, optimal=None):
    """bed/wake as 'HH:MM' local; bed before noon means after midnight."""
    def ms(d, hhmm):
        h, m = map(int, hhmm.split(":"))
        return int((date.fromisoformat(d) - date(1970, 1, 1)).total_seconds() * 1000 + (h * 60 + m) * 60000)
    v = {"sleepTimeSeconds": hours * 3600, "sleepStartTimestampLocal": ms(day, bed), "sleepEndTimestampLocal": ms(day, wake),
         "avgSleepStress": stress, "sleepScores": {"overall": {"value": score}} if score else {}}
    if need:
        v["sleepNeed"] = {"actual": need}
    if optimal:
        v["sleepScores"]["totalDuration"] = {"optimalStart": optimal}
    return v


class SleepInsightTests(unittest.TestCase):
    def days(self, n, **kw):
        return [(DAY - timedelta(days=i)).isoformat() for i in range(n)]

    def test_short_irregular_sleep(self):
        sleep = {}
        for i, d in enumerate(self.days(8)):
            early = i % 2 == 0
            sleep[d] = night(d, "21:30" if early else "01:00", "05:00", 5, score=80 if early else 55, stress=35)
        sleep[self.days(8)[1]]["sleepNeed"] = {"actual": 450}
        sleep["2026-09-01"] = night("2026-09-01", "22:00", "06:00", 8)      # too old
        sleep[self.days(9)[8]] = {"sleepTimeSeconds": 0}                      # not tracked
        s = coach.sleep_insights(sleep, DAY)
        self.assertEqual((s["n"], s["need"], s["needSource"], s["avg"]), (8, 7.5, "garmin", 5))
        self.assertEqual(s["debt"], 17.5)
        self.assertEqual((coach.hhmm(s["bed"]), s["bedSpread"]), ("23:15", 105))
        self.assertEqual(coach.hhmm(s["wake"]), "05:00")
        self.assertEqual((s["stress"], s["score"]), (35, 68))
        text = " ".join(s["insights"])
        self.assertIn("2h 30m less than the 7h 30m Garmin says you need", text)
        self.assertIn("sleep debt of about 17h 30m", text)
        self.assertIn("bedtime moves around", text)
        self.assertIn("averaged 80 on nights you went to bed before 23:15, against 55", text)
        self.assertIn("Overnight stress averaged 35 (under 25 is restful)", text)
        self.assertEqual(len(s["nights"]), 8)

    def test_good_steady_sleep(self):
        sleep = {d: night(d, "22:30", "06:30", 8, score=85, stress=15, optimal=8 * 3600) for d in self.days(5)}
        s = coach.sleep_insights(sleep, DAY)
        self.assertEqual((s["need"], s["needSource"], s["debt"]), (8, "score", 0))
        text = " ".join(s["insights"])
        self.assertIn("right around the 8h 00m recommended", text)
        self.assertIn("bedtime is steady, usually around 22:30 (give or take 0 min)", text)
        self.assertIn("resting well at night", text)
        self.assertNotIn("sleep debt", text)

    def test_long_sleep_few_nights_no_timestamps(self):
        sleep = {d: {"sleepTimeSeconds": 10 * 3600} for d in self.days(2)}
        s = coach.sleep_insights(sleep, DAY)
        self.assertEqual((s["need"], s["needSource"]), (8.0, "default"))
        self.assertIn("more than the 8h 00m recommended", s["insights"][0])
        self.assertNotIn("bed", s)
        self.assertNotIn("stress", s)
        self.assertNotIn("score", s)
        self.assertEqual(s["nights"][0]["bed"], None)

    def test_no_nights(self):
        s = coach.sleep_insights({}, DAY)
        self.assertEqual(s["n"], 0)
        self.assertIn("Wear your watch to bed", s["insights"][0])


class TrainingPlanTests(unittest.TestCase):
    def plan(self, acts, cfg=None, today=DAY):
        return coach.training_plan(acts, coach.milestones(acts), cfg or {}, today)

    def test_first_runs(self):
        p = self.plan([])
        self.assertEqual((p["target"], p["goal"], p["perWeek"]), ("First run", 1.0, 3))
        self.assertEqual(p["week"]["long"], 1.0)
        self.assertEqual(p["week"]["intervals"], "Run 1 min, walk 2 min, repeat")
        self.assertEqual([(x["kind"], x["day"], x["done"]) for x in p["sessions"]],
                         [("Easy run", "Tue", False), ("Easy run", "Thu", False), ("Long run", "Sat", False)])
        self.assertEqual(len(p["past"]), 4)
        self.assertEqual(len(p["ahead"]), 4)

    def test_building_to_5k_and_this_weeks_runs(self):
        mon = coach.monday(DAY)
        acts = [run(iso(mon - timedelta(days=10)), 2.0, 2.5, 1), run(iso(mon), 1.4, 2.5, 2),
                run(iso(mon + timedelta(days=2)), 2.3, 2.5, 3)]
        p = self.plan(acts, {"goal_type": "runs", "goal_value": 4})
        w = p["week"]
        self.assertEqual((p["target"], p["perWeek"]), ("5K", 4))
        self.assertEqual((w["long"], w["easy"], w["doneRuns"], w["doneLong"], w["longOk"]), (2.2, 1.3, 2, 2.3, True))
        self.assertEqual(w["intervals"], "Run 2 min, walk 1 min, repeat")
        self.assertEqual([x["done"] for x in p["sessions"]], [True, False, False, True])
        self.assertEqual([x["day"] for x in p["sessions"]], ["Mon", "Wed", "Fri", "Sun"])
        self.assertEqual(p["ahead"][0]["long"], 2.5)                 # builds on the 2.3 km already run
        self.assertFalse(p["ready"])

    def test_runs_per_week_is_kept_sensible(self):
        self.assertEqual(self.plan([], {"goal_type": "runs", "goal_value": 9})["perWeek"], 5)
        self.assertEqual(self.plan([], {"goal_type": "runs", "goal_value": 1})["perWeek"], 2)
        self.assertEqual(self.plan([], {"goal_type": "km", "goal_value": 20})["perWeek"], 3)

    def test_easier_weeks_and_ready(self):
        mon = coach.monday(DAY)
        acts = [run(iso(mon - timedelta(days=d)), 5.0, 2.5, d) for d in (3, 10, 17)]
        acts.append(run(iso(mon), 5.0, 2.5, 99))
        p = self.plan(acts, today=DAY)
        self.assertEqual(p["target"], "10K")
        easier = [w for w in p["ahead"] if w["easier"]]
        self.assertEqual(len(easier), 1)
        i = p["ahead"].index(easier[0])
        if i:
            self.assertEqual(easier[0]["long"], p["ahead"][i - 1]["long"])   # no growth in an easier week
        acts = [run(iso(mon - timedelta(days=3)), 10.0, 2.5, 1), run(iso(mon), 9.6, 2.5, 2)]
        p = self.plan(acts[:1] + [run("2026-01-01", 21.1, 2.5, 3), run("2026-01-02", 42.2, 2.5, 4)], today=DAY)
        self.assertEqual(p["target"], "Marathon")                      # triathlons next: keep building to the marathon
        p = coach.training_plan([run(iso(mon - timedelta(days=3)), 5.0, 2.5, 1)],
                                {"steps": [{"k": "5k", "date": None}], "next": 0}, {}, DAY)
        self.assertTrue(p["ready"])
        self.assertEqual(p["week"]["intervals"], None)

    def test_run_walk_steps(self):
        self.assertEqual(coach.run_walk(3.0), "Run 4 min, walk 1 min, repeat")
        self.assertIsNone(coach.run_walk(4.0))


class BuildCoachTests(unittest.TestCase):
    def test_all_cards(self):
        c = coach.build_coach(D, {}, {}, {}, [], [], {}, today=DAY)
        self.assertEqual(set(c), {"ready", "goal", "ladder", "records", "sleep", "plan"})
        self.assertEqual(coach.build_coach(D, {}, {}, {}, [], [], {})["goal"]["type"], "minutes")


if __name__ == "__main__":
    unittest.main()
