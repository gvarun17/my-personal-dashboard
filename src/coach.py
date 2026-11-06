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


def build_coach(day, daily_raw, sleep_raw, hrv_raw, days, acts, cfg, today=None):
    return {
        "ready": readiness(day, daily_raw, sleep_raw, hrv_raw),
        "goal": weekly_goal(days, acts, cfg, today or date.today()),
        "ladder": milestones(acts),
    }
