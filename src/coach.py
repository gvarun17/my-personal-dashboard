"""The coaching cards at the top of the dashboard, worked out from your data:

- readiness: should today be a training day, an easy day or a rest day?
- weekly goal: progress this week, and how many weeks in a row you hit it
- milestones: the ladder from your first run to an Ironman, and the next step

Everything here is plain arithmetic on data already downloaded. It's a guide,
not medical advice.
"""
import math
from datetime import date, timedelta

import settings

SCORE = {"good": 2, "ok": 1, "poor": 0}
TOLERANCE = 0.98    # a "5 km" run measured as 4.92 km still counts


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _hm(hours):
    m = round(hours * 60)
    return f"{m // 60}h {m % 60:02d}m"


# ------------------------------------------------------------------ readiness

def hrv_signal(h):
    v, week = _num(h.get("lastNightAvg")), _num(h.get("weeklyAvg"))
    if v is None:
        return {"k": "hrv", "label": "HRV", "state": "none", "value": None, "note": "Not tracked last night"}
    status = (h.get("status") or "").upper()
    if status == "BALANCED":
        state, note = "good", "In your normal range"
    elif status == "UNBALANCED":
        state, note = "ok", "Outside your normal range"
    elif status in ("LOW", "POOR"):
        state, note = "poor", "Below your normal range"
    elif week:
        ratio = v / week
        state = "good" if ratio >= 0.95 else "ok" if ratio >= 0.85 else "poor"
        note = {"good": "Close to", "ok": "A bit below", "poor": "Well below"}[state] + f" your 7-day average ({week:.0f} ms)"
    else:
        state, note = "none", "Still learning your normal range"
    return {"k": "hrv", "label": "HRV", "state": state, "value": f"{v:.0f} ms", "note": note}


def sleep_signal(s):
    hours = (_num(s.get("sleepTimeSeconds")) or 0) / 3600
    if hours <= 0:
        return {"k": "sleep", "label": "Sleep", "state": "none", "value": None, "note": "Not tracked last night"}
    score = _num((((s.get("sleepScores") or {}).get("overall")) or {}).get("value"))
    if score is not None:
        state = "good" if score >= 80 else "ok" if score >= 60 else "poor"
        note = f"Sleep score {score:.0f}"
    else:
        state = "good" if hours >= 7 else "ok" if hours >= 6 else "poor"
        note = "7 hours or more helps you recover"
    return {"k": "sleep", "label": "Sleep", "state": state, "value": _hm(hours), "note": note}


def rhr_signal(today_rhr, history):
    """Resting heart rate today against the average of the 30 days before."""
    if _num(today_rhr) is None:
        return {"k": "rhr", "label": "Resting HR", "state": "none", "value": None, "note": "Not measured yet today"}
    value = f"{today_rhr:.0f} bpm"
    if len(history) < 7:
        return {"k": "rhr", "label": "Resting HR", "state": "none", "value": value,
                "note": "Needs a week of data to know your normal"}
    usual = sum(history) / len(history)
    diff = today_rhr - usual
    if diff <= 2:
        state, note = "good", f"Normal for you (usually {usual:.0f})"
    elif diff <= 5:
        state, note = "ok", f"{diff:.0f} bpm above your usual {usual:.0f}"
    else:
        state, note = "poor", f"{diff:.0f} bpm above your usual {usual:.0f}: tiredness, stress or a cold coming?"
    return {"k": "rhr", "label": "Resting HR", "state": state, "value": value, "note": note}


def battery_signal(peak):
    if _num(peak) is None:
        return {"k": "bb", "label": "Body Battery", "state": "none", "value": None, "note": "Not measured yet today"}
    state = "good" if peak >= 75 else "ok" if peak >= 50 else "poor"
    note = {"good": "Well charged", "ok": "Partly charged", "poor": "Low charge"}[state] + " this morning"
    return {"k": "bb", "label": "Body Battery", "state": state, "value": f"{peak:.0f}", "note": note}


CALLS = {
    "go": ("Good day to train", "Your body looks recovered. A good day for a run, or a longer walk than usual."),
    "easy": ("Take it easy today", "Keep it gentle: a walk, or a relaxed run where you can talk in full sentences."),
    "rest": ("Rest and recover today", "Your body is still recovering. Rest, stretch, or take a short gentle walk."),
}


def readiness(day, daily_raw, sleep_raw, hrv_raw):
    v = daily_raw.get(day) or {}
    d0 = date.fromisoformat(day)
    history = [rhr for back in range(1, 31)
               if (rhr := _num(((daily_raw.get((d0 - timedelta(days=back)).isoformat()) or {}).get("heartRate") or {})
                               .get("restingValue")))]
    signals = [
        hrv_signal(hrv_raw.get(day) or {}),
        sleep_signal(sleep_raw.get(day) or {}),
        rhr_signal((v.get("heartRate") or {}).get("restingValue"), history),
        battery_signal((v.get("bodyBattery") or {}).get("maxValue")),
    ]
    rated = [s["state"] for s in signals if s["state"] in SCORE]
    if len(rated) < 2:
        return {"level": None, "title": "Not enough data yet today",
                "advice": "Wear your watch overnight and this card will tell you how ready you are to train.",
                "signals": signals}
    poor, ok = rated.count("poor"), rated.count("ok")
    level = "rest" if poor >= 2 else "easy" if poor or ok >= 2 else "go"
    title, advice = CALLS[level]
    return {"level": level, "title": title, "advice": advice, "signals": signals}


# ---------------------------------------------------------------- weekly goal

def monday(d):
    return d - timedelta(days=d.weekday())


def weekly_goal(days, acts, cfg, today):
    """days and acts are the dashboard's lists (d = YYYY-MM-DD)."""
    gtype = cfg.get("goal_type") if cfg.get("goal_type") in settings.GOAL_TYPES else "minutes"
    label, unit, default, _ = settings.GOAL_TYPES[gtype]
    target, source = _num(cfg.get("goal_value")), "you"
    if not target:
        garmin = days[-1].get("img") if days and gtype == "minutes" else None
        target, source = (garmin, "garmin") if _num(garmin) else (default, "default")

    def value(start):
        end = (start + timedelta(days=7)).isoformat()
        a, b = start.isoformat(), end
        if gtype == "minutes":
            return sum(d.get("im") or 0 for d in days if a <= d["d"] < b)
        week = [x for x in acts if a <= x["d"] < b]
        if gtype == "km":
            return round(sum(x.get("km") or 0 for x in week), 1)
        runs = [x for x in week if x["s"] == "run"]
        return len(runs) if gtype == "runs" else round(sum(x.get("km") or 0 for x in runs), 1)

    this = monday(today)
    dates = [x["d"] for x in days] + [x["d"] for x in acts]
    first = monday(date.fromisoformat(min(dates))) if dates else this
    weeks = []
    w = max(first, this - timedelta(weeks=104))
    while w <= this:
        v = value(w)
        weeks.append({"w": w.isoformat(), "v": v, "hit": v >= target})
        w += timedelta(days=7)
    done = weeks[:-1]
    streak = 0
    for wk in reversed(done):
        if not wk["hit"]:
            break
        streak += 1
    if weeks[-1]["hit"]:
        streak += 1
    best = run = 0
    for wk in weeks:
        run = run + 1 if wk["hit"] else 0
        best = max(best, run)
    now = weeks[-1]["v"]
    left = 7 - today.weekday()          # days left, including today
    need = max(target - now, 0)
    return {
        "type": gtype, "label": label, "unit": unit, "target": target, "source": source,
        "value": now, "need": round(need, 1) if unit == "km" else math.ceil(need), "left": left,
        "perDay": round(need / left, 1), "streak": streak, "best": best,
        "hits": sum(wk["hit"] for wk in done[-12:]), "of": len(done[-12:]),
        "weeks": weeks[-8:],
    }


# ----------------------------------------------------------------- milestones

STEPS = [
    ("first", "First run", "Any run, even run-walk", None),
    ("1k", "1 km", "In one run", 1.0),
    ("5k", "5K", "5 km in one run", 5.0),
    ("10k", "10K", "10 km in one run", 10.0),
    ("half", "Half marathon", "21.1 km", 21.1),
    ("full", "Marathon", "42.2 km", 42.2),
    ("703", "Half Ironman", "1.9 swim · 90 bike · 21.1 run, one day", (1.9, 90.0, 21.1)),
    ("ironman", "Ironman", "3.8 swim · 180 bike · 42.2 run, one day", (3.8, 180.0, 42.2)),
]


def milestones(acts):
    runs = [a for a in acts if a["s"] == "run"]
    by_day = {}
    for a in acts:
        if a["s"] in ("swim", "cycle", "run"):
            t = by_day.setdefault(a["d"], {"swim": 0, "cycle": 0, "run": 0})
            t[a["s"]] += a.get("km") or 0

    def reached(goal):
        if goal is None:
            return runs[0]["d"] if runs else None
        if isinstance(goal, tuple):
            need = dict(zip(("swim", "cycle", "run"), goal, strict=True))
            return next((d for d, t in sorted(by_day.items())
                         if all(t[k] >= need[k] * TOLERANCE for k in need)), None)
        return next((a["d"] for a in runs if (a.get("km") or 0) >= goal * TOLERANCE), None)

    steps = [{"k": k, "label": label, "sub": sub, "date": reached(goal)} for k, label, sub, goal in STEPS]
    longest = max(runs, key=lambda a: a.get("km") or 0, default=None)
    long_km = (longest or {}).get("km") or 0
    best = {s: max((a.get("km") or 0 for a in acts if a["s"] == s), default=0) for s in ("swim", "cycle")}
    nxt = next((i for i, s in enumerate(steps) if not s["date"]), None)
    out = {"steps": steps, "next": nxt, "runs": len(runs),
           "longest": {"km": long_km, "d": longest["d"]} if longest else None,
           "walks": sum(a["s"] == "walk" for a in acts)}
    if nxt is None:
        out["tip"] = "You've climbed the whole ladder. Time to pick a new goal!"
        return out
    goal = STEPS[nxt][3]
    if goal is None:
        out["tip"] = ("Try run-walk: 1 minute of easy running, then 2 minutes of walking, 8 times. "
                      + (f"Your {out['walks']} walks are already a good base." if out["walks"] else
                         "Easy enough that you could chat the whole time."))
    elif isinstance(goal, tuple):
        out["tip"] = (f"Build up swimming and cycling alongside your running. Longest swim so far "
                      f"{best['swim']:.1f} km, longest ride {best['cycle']:.0f} km.")
    else:
        out["togo"] = round(max(goal - long_km, 0), 1)
        if long_km > 0:
            out["weeks"] = max(1, math.ceil(math.log(goal / long_km) / math.log(1.1)))
            out["tip"] = (f"Add about 10% to your longest run each week, and you'll get there in "
                          f"about {out['weeks']} week{'s' if out['weeks'] != 1 else ''}.")
        else:
            out["tip"] = "Use run-walk to build up: run a little longer each time before you walk."
    return out




# ------------------------------------------------------------ personal bests

def _week_totals(items, key, value):
    out = {}
    for x in items:
        w = monday(date.fromisoformat(key(x))).isoformat()
        out[w] = out.get(w, 0) + (value(x) or 0)
    return out


def _mmss(seconds):
    s = round(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def personal_bests(acts, days, sleep_raw, today):
    """Your bests and firsts. new = set in the last 7 days."""
    week_ago = (today - timedelta(days=6)).isoformat()
    bests = []

    def add(k, label, item, text, sub=""):
        if item:
            d = item["d"]
            bests.append({"k": k, "label": label, "text": text, "d": d, "new": d >= week_ago,
                          "aid": item.get("id"), "name": item.get("n") or item.get("ty") or "", "sub": sub})

    def best(items, f, lowest=False):
        items = [x for x in items if f(x)]
        return (min if lowest else max)(items, key=f, default=None)

    runs = [a for a in acts if a["s"] == "run"]
    a = best(runs, lambda x: x.get("km"))
    add("run_long", "Longest run", a, a and f"{a['km']:.1f} km")
    a = best([x for x in runs if (x.get("km") or 0) >= 1], lambda x: x.get("spd"))
    add("run_pace", "Fastest run pace", a, a and f"{_mmss(1000 / a['spd'])} /km", "average for the whole run")
    for k, label, metres, split in (("run_1k", "Fastest 1 km", 1000, "f1k"), ("run_5k", "Fastest 5K", 5000, "f5k")):
        # Garmin's own fastest split when it has one, otherwise a whole run of at least that distance
        timed = [dict(x, _t=x[split]) for x in runs if x.get(split)]
        timed += [dict(x, _t=metres / x["spd"]) for x in runs
                  if not x.get(split) and x.get("spd") and (x.get("km") or 0) * 1000 >= metres * TOLERANCE]
        a = best(timed, lambda x: x["_t"], lowest=True)
        add(k, label, a, a and _mmss(a["_t"]), a and ("fastest split" if a.get(split) else "whole-run average"))
    weeks = _week_totals(runs, lambda x: x["d"], lambda x: x.get("km"))
    if weeks and max(weeks.values()) > 0:
        w = max(weeks, key=weeks.get)
        add("run_week", "Biggest running week", {"d": w}, f"{weeks[w]:.1f} km", "week of " + w)
    for s, k, label in (("walk", "walk_long", "Longest walk"), ("cycle", "ride_long", "Longest ride"),
                        ("hike", "hike_long", "Longest hike"), ("swim", "swim_long", "Longest swim")):
        a = best([x for x in acts if x["s"] == s], lambda x: x.get("km"))
        add(k, label, a, a and f"{a['km']:.1f} km")
    a = best([x for x in acts if x["s"] == "strength"], lambda x: x.get("min"))
    add("strength_long", "Longest strength session", a, a and f"{a['min']:.0f} min")
    d = best(days, lambda x: x.get("st"))
    add("steps_day", "Most steps in a day", d, d and f"{d['st']:,}")
    weeks = _week_totals(days, lambda x: x["d"], lambda x: x.get("im"))
    if weeks and max(weeks.values()) > 0:
        w = max(weeks, key=weeks.get)
        add("im_week", "Most active minutes in a week", {"d": w}, f"{weeks[w]:,} min", "week of " + w)
    d = best(days, lambda x: x.get("rhr"), lowest=True)
    add("rhr_low", "Lowest resting heart rate", d, d and f"{d['rhr']} bpm")
    scored = [{"d": k, "sc": _num((((v.get("sleepScores") or {}).get("overall")) or {}).get("value"))}
              for k, v in sleep_raw.items()]
    d = best(scored, lambda x: x["sc"])
    add("sleep_best", "Best sleep score", d, d and f"{d['sc']:.0f}")

    firsts, seen = [], set()
    for a in acts:                                  # oldest first
        if a["s"] not in seen:
            seen.add(a["s"])
            firsts.append({"s": a["s"], "d": a["d"], "name": a.get("n") or a.get("ty") or "",
                           "new": a["d"] >= week_ago})
    return {"bests": bests, "firsts": firsts}


# -------------------------------------------------------------- sleep insights

def _clock(ms):
    """Minutes after 18:00 for a Garmin 'local' timestamp, so bedtimes either
    side of midnight average properly."""
    return ((ms // 60000) % 1440 - 18 * 60) % 1440


def _spread(vals):
    m = sum(vals) / len(vals)
    return m, math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def hhmm(after6pm):
    m = round(after6pm + 18 * 60) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def sleep_insights(sleep_raw, today, nights=14):
    start = (today - timedelta(days=nights - 1)).isoformat()
    rows = []
    for d, v in sorted(sleep_raw.items()):
        secs = _num(v.get("sleepTimeSeconds")) or 0
        if d < start or secs <= 0:
            continue
        bed, wake = _num(v.get("sleepStartTimestampLocal")), _num(v.get("sleepEndTimestampLocal"))
        rows.append({"d": d, "h": round(secs / 3600, 2),
                     "bed": _clock(bed) if bed else None, "wake": _clock(wake) if wake else None,
                     "sc": _num((((v.get("sleepScores") or {}).get("overall")) or {}).get("value")),
                     "stress": _num(v.get("avgSleepStress")), "v": v})
    # How much you need: Garmin's own figure when it has one, else its score target, else 8 hours
    need, source = 8.0, "default"
    for r in reversed(rows):
        n = _num((r["v"].get("sleepNeed") or {}).get("actual"))
        if n:
            need, source = round(n / 60, 2), "garmin"
            break
    else:
        for r in reversed(rows):
            n = _num(((r["v"].get("sleepScores") or {}).get("totalDuration") or {}).get("optimalStart"))
            if n:
                need, source = round(n / 3600, 2), "score"
                break
    out = {"need": need, "needSource": source, "n": len(rows), "nights": [
        {k: r[k] for k in ("d", "h", "bed", "wake", "sc")} for r in rows]}
    if not rows:
        out["insights"] = ["No nights tracked in the last two weeks. Wear your watch to bed to get sleep insights."]
        return out
    avg = sum(r["h"] for r in rows) / len(rows)
    week = [r for r in rows if r["d"] >= (today - timedelta(days=6)).isoformat()]
    debt = max(sum(need - r["h"] for r in week), 0)
    out.update(avg=round(avg, 2), debt=round(debt, 1))
    tips = []
    gap = need - avg
    need_txt = _hm(need) + (" Garmin says you need" if source == "garmin" else " recommended")
    if abs(gap) <= 0.25:
        tips.append(f"You averaged {_hm(avg)} a night, right around the {need_txt}.")
    elif gap > 0:
        tips.append(f"You averaged {_hm(avg)} a night, {_hm(gap)} less than the {need_txt}.")
    else:
        tips.append(f"You averaged {_hm(avg)} a night, more than the {need_txt}. Good for recovery.")
    if debt >= 2:
        tips.append(f"Over the last 7 days that's a sleep debt of about {_hm(debt)}. "
                    "An earlier night or two this week would help you recover from training.")
    beds = [r["bed"] for r in rows if r["bed"] is not None]
    wakes = [r["wake"] for r in rows if r["wake"] is not None]
    if len(beds) >= 3:
        mean, sd = _spread(beds)
        out.update(bed=round(mean), bedSpread=round(sd))
        if sd <= 30:
            tips.append(f"Your bedtime is steady, usually around {hhmm(mean)} (give or take {sd:.0f} min).")
        else:
            tips.append(f"Your bedtime moves around: usually {hhmm(mean)}, give or take {sd:.0f} min. "
                        "Going to bed within the same half hour each night tends to improve sleep.")
        scored = [r for r in rows if r["bed"] is not None and r["sc"] is not None]
        early = [r["sc"] for r in scored if r["bed"] <= mean]
        late = [r["sc"] for r in scored if r["bed"] > mean]
        if len(early) >= 3 and len(late) >= 3:
            e, la = sum(early) / len(early), sum(late) / len(late)
            if abs(e - la) >= 5:
                better = "before" if e > la else "after"
                tips.append(f"Your sleep score averaged {max(e, la):.0f} on nights you went to bed {better} "
                            f"{hhmm(mean)}, against {min(e, la):.0f} otherwise.")
    if len(wakes) >= 3:
        mean, sd = _spread(wakes)
        out.update(wake=round(mean), wakeSpread=round(sd))
    stress = [r["stress"] for r in rows if r["stress"] is not None]
    if stress:
        s = sum(stress) / len(stress)
        out["stress"] = round(s)
        if s > 25:
            tips.append(f"Overnight stress averaged {s:.0f} (under 25 is restful). Late meals, alcohol, "
                        "screens or hard evening workouts can keep it up.")
        else:
            tips.append(f"Overnight stress averaged {s:.0f}: your body is resting well at night.")
    scores = [r["sc"] for r in rows if r["sc"] is not None]
    if scores:
        out["score"] = round(sum(scores) / len(scores))
    out["insights"] = tips
    return out


# --------------------------------------------------------------- training plan

PLAN_DAYS = {2: ["Wed", "Sat"], 3: ["Tue", "Thu", "Sat"], 4: ["Mon", "Wed", "Fri", "Sun"],
             5: ["Mon", "Tue", "Thu", "Fri", "Sun"]}
START_KM = 1.0      # the first long run when you haven't run recently


def run_walk(km):
    """Interval advice while runs are still short."""
    if km < 1.5:
        return "Run 1 min, walk 2 min, repeat"
    if km < 2.5:
        return "Run 2 min, walk 1 min, repeat"
    if km < 3.5:
        return "Run 4 min, walk 1 min, repeat"
    return None


def _week_plan(base, target, easier):
    """One week: the long run grows ~10% on your recent longest run (no growth
    in an easier week), easy runs are ~60% of it."""
    long_km = max(base, START_KM) if easier else max(base * 1.1, START_KM)
    long_km = round(min(long_km, target), 1)
    return long_km, round(max(long_km * 0.6, 0.8 if long_km >= 0.8 else long_km), 1)


def training_plan(acts, ladder, cfg, today):
    steps = {k: goal for k, _, _, goal in STEPS}
    nxt = ladder["steps"][ladder["next"]]["k"] if ladder["next"] is not None else None
    goal = steps.get(nxt) if nxt else None
    if nxt == "first":
        goal = 1.0
    if not isinstance(goal, float):           # triathlon steps or all done: keep building on the marathon
        goal, nxt = 42.2, "full"
    label = {k: lab for k, lab, _, _ in STEPS}[nxt]
    per = int(cfg.get("goal_value") or 3) if cfg.get("goal_type") == "runs" else 3
    per = min(max(per, 2), 5)
    runs = [a for a in acts if a["s"] == "run"]

    def base_for(w):                          # longest run in the 4 weeks before week w
        a, b = (w - timedelta(days=28)).isoformat(), w.isoformat()
        return max((x.get("km") or 0 for x in runs if a <= x["d"] < b), default=0)

    def week(w):
        """Planned vs done for the week starting on Monday w."""
        easier = w.isocalendar()[1] % 4 == 0
        long_km, easy_km = _week_plan(base_for(w), goal, easier)
        done = [x for x in runs if w.isoformat() <= x["d"] < (w + timedelta(days=7)).isoformat()]
        longest = max((x.get("km") or 0 for x in done), default=0)
        return {"w": w.isoformat(), "long": long_km, "easy": easy_km, "runs": per, "easier": easier,
                "intervals": run_walk(max(long_km, longest)),      # advice follows what you can already run
                "doneRuns": len(done), "doneKm": round(sum(x.get("km") or 0 for x in done), 1),
                "doneLong": round(longest, 1), "longOk": longest >= long_km * 0.95,
                "ok": len(done) >= per and longest >= long_km * 0.95}

    this = monday(today)
    now = week(this)
    sessions = [{"kind": "Easy run", "km": now["easy"]} for _ in range(per - 1)] + [{"kind": "Long run", "km": now["long"]}]
    easy_done = now["doneRuns"] - (1 if now["longOk"] else 0)
    for i, s in enumerate(sessions):
        s["day"] = PLAN_DAYS[per][i]
        s["done"] = now["longOk"] if s["kind"] == "Long run" else i < easy_done
    # The weeks ahead, assuming each long run is done as planned (or what you already ran, if longer)
    ahead, base = [], max(now["long"], min(now["doneLong"], goal))
    for k in range(1, 5):
        w = this + timedelta(weeks=k)
        long_km, easy_km = _week_plan(base, goal, w.isocalendar()[1] % 4 == 0)
        ahead.append({"w": w.isoformat(), "long": long_km, "easy": easy_km, "easier": w.isocalendar()[1] % 4 == 0})
        base = long_km
    ready = now["long"] >= goal and base_for(this) >= goal * 0.9
    return {"goal": goal, "target": label, "perWeek": per, "week": now, "sessions": sessions,
            "past": [week(this - timedelta(weeks=k)) for k in range(4, 0, -1)], "ahead": ahead,
            "ready": ready}


def build_coach(day, daily_raw, sleep_raw, hrv_raw, days, acts, cfg, today=None):
    today = today or date.today()
    ladder = milestones(acts)
    return {
        "ready": readiness(day, daily_raw, sleep_raw, hrv_raw),
        "goal": weekly_goal(days, acts, cfg, today),
        "ladder": ladder,
        "records": personal_bests(acts, days, sleep_raw, today),
        "sleep": sleep_insights(sleep_raw, today),
        "plan": training_plan(acts, ladder, cfg, today),
    }
