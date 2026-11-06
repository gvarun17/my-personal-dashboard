"""Build dashboard.html from the JSON files in data/.

    python3 src/build.py

Writes one self-contained file: the data, styles and charts are all inside it,
so it opens in any browser with no server and nothing loaded from the internet.
"""
import json
import os
from datetime import date, datetime
from pathlib import Path

import coach
import settings

ROOT = Path(__file__).resolve().parent.parent   # project folder (holds data/ and dashboard.html)
DATA = Path(os.environ.get("GARMIN_DATA_DIR") or ROOT / "data")
OUT = Path(os.environ.get("DASHBOARD_OUT") or ROOT / "dashboard.html")


def load(name, default):
    p = DATA / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except ValueError:
        return default


def g(obj, *path):
    for k in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def sport(type_key):
    k = (type_key or "").lower()
    if "run" in k:
        return "run"
    if "walk" in k:
        return "walk"
    if any(w in k for w in ("cycl", "bik", "ride", "spin")):
        return "cycle"
    if "swim" in k:
        return "swim"
    if "hik" in k:
        return "hike"
    if any(w in k for w in ("strength", "hiit", "fitness", "yoga", "pilates", "cardio")):
        return "strength"
    return "other"


def r(v, n=1):
    return round(v, n) if isinstance(v, (int, float)) else None


def build():
    acts_raw = load("activities.json", {})
    daily_raw = load("daily.json", {})
    sleep_raw = load("sleep.json", {})
    hrv_raw = load("hrv.json", {})
    weight_raw = load("weight.json", {})
    fit = load("fitness_age.json", None)
    fit = fit if isinstance(fit, dict) else {}
    zones = load("hr_zones.json", None) or []
    state = load("state.json", {})

    acts = []
    for a in acts_raw.values():
        when = a.get("startTimeLocal")
        if not when:
            continue
        acts.append({
            "d": when[:10], "ts": when[:16],
            "s": sport(g(a, "activityType", "typeKey")),
            "ty": (g(a, "activityType", "typeKey") or "activity").replace("_", " "),
            "n": a.get("activityName") or "",
            "km": r((a.get("distance") or 0) / 1000, 2),
            "min": r((a.get("duration") or 0) / 60, 1),
            "el": r(a.get("elevationGain") or 0, 0),
            "hr": r(a.get("averageHR"), 0),
            "mx": r(a.get("maxHR"), 0),
            "cal": r(a.get("calories"), 0),
            "ld": r(a.get("activityTrainingLoad"), 0),
            "te": r(a.get("aerobicTrainingEffect"), 1),
            "z": [r((a.get(f"hrTimeInZone_{i}") or 0) / 60, 1) for i in range(1, 6)],
        })
    acts.sort(key=lambda x: x["ts"])

    days = []
    for d, v in sorted(daily_raw.items()):
        steps = g(v, "movement", "steps", "value")
        rhr = g(v, "heartRate", "restingValue")
        mod = g(v, "intensityMinutes", "moderate") or 0
        vig = g(v, "intensityMinutes", "vigorous") or 0
        stress = g(v, "stress", "avgLevel")
        if not steps and not rhr and not (mod or vig):
            continue  # watch off that day
        days.append({
            "d": d, "st": steps, "sg": g(v, "movement", "steps", "goal"),
            "rhr": rhr, "im": mod + 2 * vig, "img": g(v, "intensityMinutes", "goal"),
            "str": stress if isinstance(stress, (int, float)) and stress > 0 else None,
            "bbx": g(v, "bodyBattery", "maxValue"), "bbn": g(v, "bodyBattery", "minValue"),
        })

    sleep = []
    for d, v in sorted(sleep_raw.items()):
        secs = v.get("sleepTimeSeconds") or 0
        if secs <= 0:
            continue
        sleep.append({
            "d": d, "h": r(secs / 3600, 2),
            "dp": r((v.get("deepSleepSeconds") or 0) / 3600, 2),
            "lt": r((v.get("lightSleepSeconds") or 0) / 3600, 2),
            "rm": r((v.get("remSleepSeconds") or 0) / 3600, 2),
            "aw": r((v.get("awakeSleepSeconds") or 0) / 3600, 2),
            "sc": g(v, "sleepScores", "overall", "value"),
        })

    hrv = [{"d": d, "v": v.get("lastNightAvg"), "w": v.get("weeklyAvg"), "st": v.get("status")}
           for d, v in sorted(hrv_raw.items()) if v.get("lastNightAvg")]

    weight = []
    for d, v in sorted(weight_raw.items()):
        w = g(v, "latestWeight", "weight")
        if w:
            weight.append({"d": d, "kg": r(w / 1000, 1)})

    z = zones[0] if isinstance(zones, list) and zones else {}
    td = build_today(load("today.json", {}), daily_raw, sleep_raw, hrv_raw, acts_raw)
    payload = {
        "today": date.today().isoformat(),
        "built": f"{datetime.now().day} {datetime.now():%b %Y, %H:%M}",
        "acts": acts, "days": days, "sleep": sleep, "hrv": hrv, "weight": weight,
        "fitAge": r(fit.get("fitnessAge"), 1), "age": fit.get("chronologicalAge"),
        "zones": [z.get(f"zone{i}Floor") for i in range(1, 6)] + [z.get("maxHeartRateUsed")],
        "start": state.get("start"),
        "name": settings.public().get("name") or "",
        "td": td,
        "coach": coach.build_coach(td["date"], daily_raw, sleep_raw, hrv_raw, days, acts, settings.public()),
    }
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    tmp.replace(OUT)
    print(f"Wrote {OUT}")
    print(f"  {len(acts)} activities, {len(days)} days, {len(sleep)} nights, {len(hrv)} HRV readings")


def build_today(t, daily_raw, sleep_raw, hrv_raw, acts_raw):
    """Everything for the Today panel: the day's summary, last night, the
    intraday curves and full detail for each of today's activities."""
    day = t.get("date") or date.today().isoformat()
    v = daily_raw.get(day, {})
    sl = sleep_raw.get(day, {})
    hv = hrv_raw.get(day, {})
    asof = g(v, "wellnessChronology", "endTimestampLocal")
    out = {
        "date": day, "asof": asof[11:16] if asof else None,
        "steps": g(v, "movement", "steps", "value"), "stepsGoal": g(v, "movement", "steps", "goal"),
        "km": r((g(v, "movement", "steps", "distanceInMeters") or 0) / 1000, 2),
        "floors": g(v, "movement", "floorsAscended", "value"), "floorsGoal": g(v, "movement", "floorsAscended", "goal"),
        "calAct": g(v, "calories", "burnedActive"), "calTot": g(v, "calories", "burnedTotal"),
        "imMod": g(v, "intensityMinutes", "moderate") or 0, "imVig": g(v, "intensityMinutes", "vigorous") or 0,
        "rhr": g(v, "heartRate", "restingValue"), "hrMin": g(v, "heartRate", "minValue"), "hrMax": g(v, "heartRate", "maxValue"),
        "rhr7": t.get("rhr7"),
        "stress": g(v, "stress", "avgLevel"), "stressMax": g(v, "stress", "maxLevel"),
        "stressMix": {k: g(v, "stress", f) or 0 for k, f in (("rest", "restProportion"), ("low", "lowStressProportion"), ("medium", "mediumStressProportion"), ("high", "highStressProportion"))},
        "bbNow": g(v, "bodyBattery", "latestValue"), "bbMax": g(v, "bodyBattery", "maxValue"),
        "bbMin": g(v, "bodyBattery", "minValue"),
        "bbUp": g(v, "bodyBattery", "chargedValue"), "bbDown": g(v, "bodyBattery", "drainedValue"),
        "resp": g(v, "respiration", "avgValue"),
        "waterMl": g(v, "hydration", "consumedInMl"), "waterGoal": g(v, "hydration", "goalInMl"),
        "sleepH": r((sl.get("sleepTimeSeconds") or 0) / 3600, 2) if sl else None,
        "sleepScore": g(sl, "sleepScores", "overall", "value"),
        "sleepQual": g(sl, "sleepScores", "overall", "qualifierKey"),
        "sleepStages": {k: r((sl.get(f) or 0) / 3600, 2) for k, f in (("deep", "deepSleepSeconds"), ("light", "lightSleepSeconds"), ("rem", "remSleepSeconds"), ("awake", "awakeSleepSeconds"))} if sl else None,
        "hrv": hv.get("lastNightAvg"), "hrvWeek": hv.get("weeklyAvg"),
        "hr": t.get("hr") or [], "bb": t.get("bb") or [],
        "acts": [],
    }
    todays = sorted((a for a in acts_raw.values() if (a.get("startTimeLocal") or "")[:10] == day),
                    key=lambda a: a["startTimeLocal"])
    for a in todays:
        det = (t.get("acts") or {}).get(str(a.get("activityId")), {})
        sm = det.get("summary") or {}
        series = {k: vals for k, vals in (det.get("series") or {}).items() if k != "cad1"}
        out["acts"].append({
            "name": a.get("activityName") or "", "s": sport(g(a, "activityType", "typeKey")),
            "ty": (g(a, "activityType", "typeKey") or "activity").replace("_", " "),
            "start": (a.get("startTimeLocal") or "")[11:16],
            "km": r((a.get("distance") or 0) / 1000, 2), "min": r((a.get("duration") or 0) / 60, 1),
            "moving": r((sm.get("movingDuration") or a.get("movingDuration") or 0) / 60, 1),
            "hr": r(a.get("averageHR"), 0), "mx": r(a.get("maxHR"), 0), "mn": r(sm.get("minHR"), 0),
            "cal": r(a.get("calories"), 0), "ld": r(a.get("activityTrainingLoad"), 0),
            "te": r(a.get("aerobicTrainingEffect"), 1), "teLabel": (sm.get("trainingEffectLabel") or "").replace("_", " ").lower(),
            "ate": r(a.get("anaerobicTrainingEffect"), 1),
            "el": r(a.get("elevationGain"), 0), "steps": sm.get("steps") or a.get("steps"),
            "cad": r(sm.get("averageRunCadence") or a.get("averageRunningCadenceInStepsPerMinute"), 0),
            "pw": r(sm.get("averagePower"), 0), "rr": r(sm.get("avgRespirationRate") or a.get("avgRespirationRate"), 0),
            "spd": sm.get("averageMovingSpeed") or sm.get("averageSpeed") or a.get("averageSpeed"),
            "z": [r((a.get(f"hrTimeInZone_{i}") or 0) / 60, 1) for i in range(1, 6)],
            "splits": [{"k": x.get("splitType"), "min": r((x.get("duration") or 0) / 60, 1),
                        "km": r((x.get("distance") or 0) / 1000, 2)}
                       for x in (det.get("splits") or []) if (x.get("splitType") or "").startswith("RWD_")],
            "series": series,
        })
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My Training</title>
<style>
:root{
  color-scheme:dark;
  --page:#0d0d0d; --card:#1a1a19; --raise:#232321;
  --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --accent:#3987e5; --accent-track:#104281;
  --good:#0ca30c; --warn:#e0a21b; --bad:#e66767;
  --font:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 var(--font);}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 48px}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:8px 24px;margin-bottom:20px}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600}
h1{font-size:28px;line-height:1.15;margin:4px 0 0;font-weight:650;letter-spacing:-.01em}
h2{font-size:16px;margin:0;font-weight:620}
.q{color:var(--muted);font-size:13px;margin:2px 0 14px}
.meta{color:var(--muted);font-size:12px;text-align:right}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px 18px 16px;min-width:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.sec{margin-top:14px}
.sechead{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin:30px 2px 12px}
.sechead h2{font-size:19px}
/* hero */
.hero{display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:26px;align-items:center;padding:22px}
.ringbox{display:flex;flex-wrap:wrap;align-items:center;gap:16px 20px}
.ringbox>div:last-child{flex:1 1 200px;min-width:0}
.ring{position:relative;width:168px;height:168px;flex:none}
.ring svg{display:block}
.ring .c{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.hero-num{font-size:52px;font-weight:650;line-height:1;letter-spacing:-.02em}
.hero-of{color:var(--muted);font-size:13px;margin-top:4px}
.dots{display:flex;gap:7px;margin-top:12px}
.dot{width:24px;text-align:center;font-size:10px;color:var(--muted)}
.dot i{display:block;width:12px;height:12px;border-radius:50%;margin:0 auto 4px;border:2px solid var(--axis)}
.dot.on i{background:var(--accent);border-color:var(--accent)}
.dot.fut i{border-style:solid;border-color:var(--grid)}
.hero-note{color:var(--ink2);font-size:13px;margin:10px 0 0;max-width:30ch}
.road h3{margin:0 0 2px;font-size:15px;font-weight:620}
.road-facts{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:10px;color:var(--ink2);font-size:13px}
.road-facts b{color:var(--ink);font-weight:600}
/* milestone ladder */
.ladder{list-style:none;margin:14px 0 0;padding:0;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:repeat(4,auto);grid-auto-flow:column;gap:2px 18px}
.ladder li{display:flex;align-items:center;gap:10px;padding:5px 0;min-width:0}
.ladder li i{flex:none;width:20px;height:20px;border-radius:50%;border:2px solid var(--accent-track);display:flex;align-items:center;justify-content:center;font-size:11px;font-style:normal;font-weight:800;color:#fff}
.ladder li.done i{background:var(--accent);border-color:var(--accent)}
.ladder li.next i{border-color:var(--accent);box-shadow:0 0 0 3px rgba(57,135,229,.28)}
.ladder li div{min-width:0}
.ladder b{display:block;font-size:13px;font-weight:600}
.ladder li.fut b{color:var(--ink2);font-weight:500}
.ladder span{display:block;font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ladder li.next span{color:var(--ink2)}
.ladtip{background:var(--raise);border-radius:12px;padding:10px 12px;margin:12px 0 0;font-size:13px;color:var(--ink2)}
.ladtip b{color:var(--ink)}
/* readiness */
.ready{display:grid;grid-template-columns:minmax(240px,320px) 1fr;gap:16px 26px;align-items:center;margin-bottom:14px}
.call{display:flex;gap:14px;align-items:flex-start}
.badge,.ic{flex:none;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-weight:800;color:#0d0d0d;font-style:normal}
.badge{width:42px;height:42px;font-size:20px}
.ic{width:16px;height:16px;font-size:10.5px}
.badge.go,.ic.good{background:var(--good)} .badge.easy,.ic.ok{background:var(--warn)} .badge.rest,.ic.poor{background:var(--bad)}
.badge.none,.ic.none{background:var(--axis);color:var(--ink2)}
.call h2{font-size:20px;font-weight:650}
.call p{color:var(--ink2);margin:4px 0 0;font-size:13.5px}
.call .disc{color:var(--muted);font-size:11.5px;margin-top:6px}
.sigs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.sig{background:var(--raise);border-radius:12px;padding:10px 12px;min-width:0}
.sig .lab{font-size:11.5px;color:var(--ink2);font-weight:550;display:flex;align-items:center;gap:6px}
.sig .val{font-size:20px;font-weight:650;margin-top:3px;white-space:nowrap}
.sig .sub{font-size:11.5px;color:var(--muted);line-height:1.35;margin-top:2px}
/* tiles */
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-top:14px}
.tile{padding:14px 14px 10px}
.tile .lab{font-size:12px;color:var(--ink2);font-weight:550}
.tile .val{font-size:28px;font-weight:650;margin-top:4px;letter-spacing:-.01em;white-space:nowrap}
.tile .val small{font-size:13px;color:var(--muted);font-weight:500;margin-left:3px}
.tile .dl{font-size:12px;color:var(--muted);margin-top:2px;min-height:18px}
.up-good,.down-good{color:var(--good)} .up-bad,.down-bad{color:var(--bad)}
.spark{margin-top:6px;height:34px}
/* controls */
.seg{display:inline-flex;background:var(--page);border:1px solid var(--border);border-radius:10px;padding:3px;gap:2px}
.seg button{all:unset;cursor:pointer;font-size:12.5px;padding:5px 11px;border-radius:7px;color:var(--ink2)}
.seg button[aria-pressed=true]{background:var(--raise);color:var(--ink);font-weight:600}
.seg button:focus-visible{outline:2px solid var(--accent)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--ink2);margin:10px 0 0}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;border-radius:3px;display:inline-block}
.legend i.ln{height:2px;width:14px;border-radius:1px}
.chartbox{width:100%;min-height:60px}
svg.chart{display:block;touch-action:pan-y;user-select:none;-webkit-user-select:none;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}
.base{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--muted);font-size:11px;font-family:var(--font);font-variant-numeric:tabular-nums}
.reflab{fill:var(--ink2);font-size:11px;font-family:var(--font)}
.xh{stroke:var(--ink2);stroke-width:1}
.empty{color:var(--muted);font-size:13px;padding:30px 0;text-align:center}
/* tables */
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:550;font-size:11.5px;padding:6px 8px;border-bottom:1px solid var(--axis)}
td{padding:7px 8px;border-bottom:1px solid var(--grid);white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.tot td{font-weight:600;border-bottom:0}
.sw{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:7px}
.tablewrap{overflow-x:auto;margin-top:14px}
/* zones */
.zbar{display:flex;gap:2px;height:28px;margin-top:6px}
.zbar div{border-radius:4px;display:flex;align-items:center;padding-left:7px;font-size:11.5px;font-weight:600;overflow:hidden;white-space:nowrap;min-width:0}
/* records */
.recs{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.rec .lab{font-size:12px;color:var(--ink2)}
.rec .val{font-size:22px;font-weight:650;margin-top:3px}
.rec .sub{font-size:12px;color:var(--muted);margin-top:1px}
/* heatmap */
.hmlegend{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--muted);margin-top:10px;flex-wrap:wrap}
.hmlegend i{width:12px;height:12px;border-radius:3px;display:inline-block}
footer{color:var(--muted);font-size:12px;margin-top:30px;text-align:center}
.hdr-r{display:flex;align-items:center;gap:10px}
a.rbtn{text-decoration:none}a.rbtn[hidden]{display:none}
.rbtn{all:unset;cursor:pointer;font-size:13px;font-weight:600;padding:7px 14px;border-radius:10px;background:var(--raise);border:1px solid var(--border);color:var(--ink)}
.rbtn:hover{background:#2c2c2a}.rbtn:focus-visible{outline:2px solid var(--accent)}
.rbtn[disabled]{opacity:.6;cursor:default}
/* today */
.today{margin-bottom:14px}
.todayhead{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 16px;margin-bottom:12px}
.todayhead h2{font-size:22px;font-weight:650}
.tgrid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}
.tm{background:var(--raise);border-radius:12px;padding:11px 12px;min-width:0}
.tm .lab{font-size:11.5px;color:var(--ink2);font-weight:550}
.tm .val{font-size:22px;font-weight:650;margin-top:2px;white-space:nowrap}
.tm .val small{font-size:12px;color:var(--muted);font-weight:500;margin-left:3px}
.tm .sub{font-size:11.5px;color:var(--muted);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mbar{height:4px;border-radius:2px;background:var(--accent-track);margin-top:7px;overflow:hidden}
.mbar i{display:block;height:100%;border-radius:2px;background:var(--accent)}
.thin{display:flex;gap:2px;height:6px;margin-top:8px}
.thin i{border-radius:3px;display:block}
.tcharts{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:16px}
.tcharts h3,.act h3{font-size:13.5px;margin:0 0 6px;font-weight:600}
.act{margin-top:14px}
.acthead{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}
.acthead h2{font-size:18px}
.pill{font-size:11.5px;color:var(--ink2);background:var(--raise);border-radius:999px;padding:2px 9px}
.astats{display:grid;grid-template-columns:repeat(8,1fr);gap:10px;margin:14px 0 4px}
.abars{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:12px 0 6px}
.acharts .ac{margin-top:14px}
.acharts .ac h3 span{color:var(--muted);font-weight:500;margin-left:6px}
@media (max-width:1000px){.tgrid{grid-template-columns:repeat(4,1fr)}.astats{grid-template-columns:repeat(4,1fr)}}
@media (max-width:760px){.tcharts{grid-template-columns:1fr}.abars{grid-template-columns:1fr}}
@media (max-width:520px){.tgrid{grid-template-columns:repeat(2,1fr);gap:8px}.astats{grid-template-columns:repeat(2,1fr)}.tm .val{font-size:20px}}

/* tooltip */
#tip{position:fixed;z-index:10;pointer-events:none;background:#262624;border:1px solid var(--border);border-radius:10px;padding:8px 10px;font-size:12px;line-height:1.5;color:var(--ink);box-shadow:0 8px 24px rgba(0,0,0,.45);max-width:250px;opacity:0;transition:opacity .08s}
#tip .t{color:var(--ink2);font-weight:600;margin-bottom:2px}
#tip .r{display:flex;justify-content:space-between;gap:14px}
#tip .r span:last-child{font-variant-numeric:tabular-nums;font-weight:600}
#tip .k{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px}
@media (max-width:1000px){.tiles{grid-template-columns:repeat(3,1fr)}.recs{grid-template-columns:repeat(2,1fr)}}
@media (max-width:920px){.hero{grid-template-columns:1fr}.ready{grid-template-columns:1fr}}
@media (max-width:760px){.grid2{grid-template-columns:1fr}.sigs{grid-template-columns:repeat(2,1fr)}}
@media (max-width:520px){
  header{align-items:flex-start}.hdr-r{width:100%;justify-content:space-between}
  .wrap{padding:20px 12px 40px}
  h1{font-size:23px}
  .tiles{grid-template-columns:repeat(2,1fr);gap:10px}
  .tile .val{font-size:23px}
  .card{padding:14px;border-radius:14px}
  .ring{width:136px;height:136px}.hero-num{font-size:48px}
  .ladder{grid-template-columns:1fr;grid-template-rows:none;grid-auto-flow:row}
  .sig .val{font-size:18px}
  .meta{text-align:left}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <div class="eyebrow" id="who">Training log</div>
      <h1>Building the engine</h1>
    </div>
    <div class="hdr-r"><div class="meta" id="meta"></div><a id="settingsLink" class="rbtn" href="setup" hidden>Settings</a><button id="refreshBtn" class="rbtn" hidden>Refresh</button></div>
  </header>

  <section class="card ready" id="ready" aria-live="polite"></section>
  <section class="card today" id="today"></section>
  <div id="todayActs"></div>

  <div class="sechead" style="margin-top:26px"><h2>Your training</h2></div>
  <section class="card hero">
    <div>
      <div class="eyebrow" id="goalLab">This week</div>
      <div class="ringbox" style="margin-top:12px">
        <div class="ring" id="ring"></div>
        <div>
          <div class="dots" id="dots"></div>
          <p class="hero-note" id="heroNote"></p>
        </div>
      </div>
    </div>
    <div class="road">
      <div class="eyebrow">The long game</div>
      <h3>From first run to Ironman</h3>
      <ol class="ladder" id="ladder"></ol>
      <p class="ladtip" id="ladTip"></p>
      <div class="road-facts" id="roadFacts"></div>
    </div>
  </section>

  <section class="tiles" id="tiles"></section>

  <div class="sechead"><h2>This week vs recent weeks</h2>
    <div class="seg" id="wkMetric" role="group" aria-label="Measure">
      <button aria-pressed="true" data-m="km">Distance</button><button aria-pressed="false" data-m="min">Time</button><button aria-pressed="false" data-m="n">Sessions</button>
    </div>
  </div>
  <div class="grid2">
    <div class="card">
      <h2 id="wkTitle">Distance by sport, last 12 weeks</h2>
      <p class="q">Am I doing more than I usually do?</p>
      <div class="chartbox" id="wkChart"></div>
      <div class="legend" id="wkLegend"></div>
      <div class="tablewrap" id="wkTable"></div>
    </div>
    <div class="card">
      <h2>Active minutes per week</h2>
      <p class="q">Am I hitting the 150 minutes a week that health guidelines recommend? Vigorous minutes count double, as in Garmin Connect.</p>
      <div class="chartbox" id="imChart"></div>
      <h2 style="margin-top:22px">Steps, every day</h2>
      <p class="q">How consistent have I been over the year?</p>
      <div class="chartbox" id="heat"></div>
      <div class="hmlegend" id="hmLegend"></div>
    </div>
  </div>

  <div class="sechead"><h2>Trends</h2>
    <div class="seg" id="range" role="group" aria-label="Time range">
      <button aria-pressed="false" data-r="1">1M</button><button aria-pressed="true" data-r="3">3M</button><button aria-pressed="false" data-r="6">6M</button><button aria-pressed="false" data-r="12">12M</button>
    </div>
  </div>
  <div class="grid2">
    <div class="card"><h2>Resting heart rate</h2><p class="q">Is my heart getting fitter? Lower is generally better.</p>
      <div class="chartbox" id="rhrChart"></div><div class="legend" id="rhrLegend"></div></div>
    <div class="card"><h2>HRV, overnight</h2><p class="q">How recovered am I? Compare against your own trend, not other people.</p>
      <div class="chartbox" id="hrvChart"></div><div class="legend" id="hrvNote"></div></div>
    <div class="card"><h2>Sleep</h2><p class="q">Am I sleeping enough to recover?</p>
      <div class="chartbox" id="sleepChart"></div><div class="legend" id="sleepLegend"></div></div>
    <div class="card"><h2>Training load per week</h2><p class="q">How much is my training asking of my body?</p>
      <div class="chartbox" id="loadChart"></div><div class="legend" id="loadLegend"></div></div>
  </div>

  <section class="card sec">
    <h2>How hard were the last 4 weeks?</h2>
    <p class="q" id="zoneQ">Time in each heart rate zone, across all activities.</p>
    <div class="zbar" id="zbar"></div>
    <div class="legend" id="zLegend"></div>
  </section>

  <div class="sechead"><h2>Records and biggest days</h2></div>
  <section class="recs" id="recs"></section>
  <section class="card sec">
    <h2>Biggest days</h2>
    <p class="q">Your five longest sessions.</p>
    <div class="tablewrap" id="bigTable" style="margin-top:0"></div>
  </section>

  <footer id="foot"></footer>
</div>
<div id="tip" role="tooltip"></div>

<script>
const D = __DATA__;
const DAY = 864e5, CARD = '#1a1a19', MUTED = '#898781';
const P = s => Date.UTC(+s.slice(0,4), +s.slice(5,7)-1, +s.slice(8,10));
const TODAY = P(D.today);
const MON = t => t - ((new Date(t).getUTCDay()+6)%7)*DAY;
const W0 = MON(TODAY);
const fd = (t,o={day:'numeric',month:'short'}) => new Date(t).toLocaleDateString('en-GB',{...o,timeZone:'UTC'});
const fdy = t => fd(t,{weekday:'short',day:'numeric',month:'short'});
const n0 = v => v==null ? '–' : Math.round(v).toLocaleString('en');
const n1 = v => v==null ? '–' : (Math.round(v*10)/10).toLocaleString('en',{minimumFractionDigits:1,maximumFractionDigits:1});
const hm = m => { m=Math.round(m||0); const h=Math.floor(m/60), r=m%60; return h ? `${h}h ${String(r).padStart(2,'0')}m` : `${r}m`; };
const avg = a => { a=a.filter(v=>v!=null&&!isNaN(v)); return a.length ? a.reduce((x,y)=>x+y,0)/a.length : null; };
const sum = a => a.reduce((x,y)=>x+(y||0),0);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// Sports: fixed colour per sport (validated categorical order), never by rank.
const SPORTS = [
  {k:'walk',  name:'Walk',     c:'#3987e5'},
  {k:'run',   name:'Run',      c:'#d95926'},
  {k:'cycle', name:'Cycle',    c:'#199e70'},
  {k:'swim',  name:'Swim',     c:'#c98500'},
  {k:'hike',  name:'Hike',     c:'#d55181'},
  {k:'strength',name:'Strength',c:'#008300'},
  {k:'other', name:'Other',    c:'#9085e9'},
];
const SP = Object.fromEntries(SPORTS.map(s=>[s.k,s]));
const ONFOOT = new Set(['walk','run','hike']);

D.acts.forEach(a => { a.t = P(a.d); });
D.days.forEach(d => { d.t = P(d.d); });
D.sleep.forEach(d => { d.t = P(d.d); });
D.hrv.forEach(d => { d.t = P(d.d); });
const dayMap = Object.fromEntries(D.days.map(d=>[d.t,d]));
const actsOn = t => D.acts.filter(a=>a.t===t);
const usedSports = SPORTS.filter(s => D.acts.some(a=>a.s===s.k));

// ---------- tooltip ----------
const tip = document.getElementById('tip');
function showTip(html, ev){
  tip.innerHTML = html; tip.style.opacity = 1;
  const r = tip.getBoundingClientRect(), pad = 14;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - pad;
  tip.style.left = Math.max(8,x)+'px'; tip.style.top = Math.max(8,y)+'px';
}
function hideTip(){ tip.style.opacity = 0; }
document.addEventListener('pointerdown', e => { if(!e.target.closest('svg.chart,.zbar,.thin')) hideTip(); });
addEventListener('scroll', hideTip, {passive:true});
const row = (k,v,c) => `<div class="r"><span>${c?`<i class="k" style="background:${c}"></i>`:''}${k}</span><span>${v}</span></div>`;
function hover(el, move, leave){
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerdown', move);
  el.addEventListener('pointerleave', e => { if(e.pointerType==='mouse'){ hideTip(); leave&&leave(); } });
}
const svgX = (svg, ev, W) => { const r = svg.getBoundingClientRect(); return (ev.clientX - r.left) * W / r.width; };
const svgY = (svg, ev, H) => { const r = svg.getBoundingClientRect(); return (ev.clientY - r.top) * H / r.height; };

// ---------- axes ----------
function nice(lo, hi, n=4){
  if (hi === lo){ hi = lo + 1; lo = Math.max(0, lo - 1); }
  const raw = (hi-lo)/n, p = 10**Math.floor(Math.log10(raw));
  const st = [1,2,2.5,5,10].map(k=>k*p).find(s=>s>=raw);
  const a = Math.floor(lo/st)*st, b = Math.ceil(hi/st)*st, t = [];
  for (let v=a; v<=b+st/2; v+=st) t.push(+v.toFixed(6));
  return t;
}
function monthTicks(x0, x1, X, minGap=40){
  const out = []; let d = new Date(x0), t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth()+(d.getUTCDate()===1?0:1), 1), last = -1e9;
  while (t <= x1){
    const px = X(t);
    if (px - last >= minGap){ out.push([px, fd(t,{month:'short'})]); last = px; }
    d = new Date(t); t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth()+1, 1);
  }
  return out;
}
function weekTicks(x0, x1, X, minGap=46){
  const out = []; let last = -1e9;
  for (let t = MON(x0)+7*DAY; t <= x1; t += 7*DAY){ const px = X(t); if (px-last>=minGap){ out.push([px, fd(t)]); last = px; } }
  return out;
}
const nearest = (arr, v) => { let b = arr[0]; for (const a of arr) if (Math.abs(a-v) < Math.abs(b-v)) b = a; return b; };

// ---------- line chart ----------
function lineChart(el, o){
  const W = Math.max(el.clientWidth, 240), H = o.h, m = {l:38, r:12, t:12, b:24};
  const inR = p => p[0] >= o.x0 && p[0] <= o.x1;
  const all = o.series.flatMap(s => s.pts.filter(inR));
  if (!all.length){ el.innerHTML = `<div class="empty">${o.empty || 'No data in this range yet.'}</div>`; return; }
  let lo = Math.min(...all.map(p=>p[1])), hi = Math.max(...all.map(p=>p[1]));
  const sp = (hi-lo) || Math.abs(hi)*0.1 || 1; lo -= sp*0.1; hi += sp*0.1;
  const tk = nice(lo, hi, 4); lo = tk[0]; hi = tk[tk.length-1];
  const X = t => m.l + (t-o.x0)/(o.x1-o.x0)*(W-m.l-m.r);
  const Y = v => m.t + (1-(v-lo)/(hi-lo))*(H-m.t-m.b);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  tk.forEach(v => { s += `<line class="grid" x1="${m.l}" x2="${W-m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text class="tick" x="${m.l-6}" y="${Y(v)+4}" text-anchor="end">${o.fmt ? o.fmt(v) : v}</text>`; });
  const xt = (o.x1-o.x0) <= 45*DAY ? weekTicks(o.x0,o.x1,X) : monthTicks(o.x0,o.x1,X);
  xt.forEach(([px,l]) => { s += `<text class="tick" x="${px}" y="${H-6}" text-anchor="middle">${l}</text>`; });
  o.series.forEach(se => {
    const p = se.pts.filter(inR);
    if (se.line !== false && p.length > 1){
      let d = '', prev = null;
      p.forEach(q => { d += (prev===null || q[0]-prev > (o.gap||3)*DAY ? 'M' : 'L') + X(q[0]).toFixed(1) + ',' + Y(q[1]).toFixed(1); prev = q[0]; });
      s += `<path d="${d}" fill="none" stroke="${se.c}" stroke-width="${se.w||2}" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    if (se.dots) p.forEach(q => { s += `<circle cx="${X(q[0])}" cy="${Y(q[1])}" r="4" fill="${se.c}" stroke="${CARD}" stroke-width="2"/>`; });
  });
  s += `<line class="xh" y1="${m.t}" y2="${H-m.b}" visibility="hidden"/><g class="hv"></g></svg>`;
  el.innerHTML = s;
  const svg = el.querySelector('svg'), xh = svg.querySelector('.xh'), hv = svg.querySelector('.hv');
  const keys = [...new Set(all.map(p=>p[0]))].sort((a,b)=>a-b);
  hover(svg, ev => {
    const t = o.x0 + (svgX(svg,ev,W)-m.l)/(W-m.l-m.r)*(o.x1-o.x0), k = nearest(keys, t);
    xh.setAttribute('x1', X(k)); xh.setAttribute('x2', X(k)); xh.setAttribute('visibility','visible');
    hv.innerHTML = o.series.map(se => { const q = se.pts.find(q=>q[0]===k); return q ? `<circle cx="${X(k)}" cy="${Y(q[1])}" r="5" fill="${se.c}" stroke="${CARD}" stroke-width="2"/>` : ''; }).join('');
    showTip(o.tip(k), ev);
  }, () => { xh.setAttribute('visibility','hidden'); hv.innerHTML = ''; });
}

// ---------- column chart (optionally stacked) ----------
function colChart(el, o){
  const W = Math.max(el.clientWidth, 240), H = o.h, m = {l:38, r:o.ref?44:12, t:14, b:24};
  const tot = [...Array(o.n)].map((_,i) => sum(o.stacks.map(s=>s.v[i])));
  const top0 = Math.max(...tot, o.ref ? o.ref.v : 0);
  if (!top0){ el.innerHTML = `<div class="empty">${o.empty || 'No data in this range yet.'}</div>`; return; }
  const tk = nice(0, top0*1.04, 4), top = tk[tk.length-1];
  const slot = (W-m.l-m.r)/o.n, bw = Math.max(1, Math.min(24, slot*(o.n>40 ? 0.8 : 0.62)));
  const Y = v => m.t + (1-v/top)*(H-m.t-m.b), base = Y(0);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  tk.forEach(v => { s += `<line class="${v?'grid':'base'}" x1="${m.l}" x2="${W-m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text class="tick" x="${m.l-6}" y="${Y(v)+4}" text-anchor="end">${o.fmt ? o.fmt(v) : v}</text>`; });
  s += `<rect class="hb" y="${m.t}" height="${base-m.t}" width="${slot}" fill="#ffffff" opacity="0" rx="4"/>`;
  if (o.hl != null) s += `<rect x="${m.l+o.hl*slot}" y="${m.t}" width="${slot}" height="${base-m.t}" fill="#ffffff" opacity=".04" rx="4"/>`;
  for (let i = 0; i < o.n; i++){
    const x = m.l + i*slot + (slot-bw)/2; let acc = 0;
    const segs = o.stacks.map(st => ({c:st.c, v:st.v[i]||0})).filter(q=>q.v>0);
    segs.forEach((q, j) => {
      const yb = Y(acc) - (j ? 2 : 0), yt = Y(acc+q.v); acc += q.v;
      if (yb - yt < 0.6) return;
      const r = j === segs.length-1 ? Math.min(4, bw/2, yb-yt) : 0;
      s += r ? `<path d="M${x},${yb}V${yt+r}Q${x},${yt} ${x+r},${yt}H${x+bw-r}Q${x+bw},${yt} ${x+bw},${yt+r}V${yb}Z" fill="${q.c}"/>`
             : `<rect x="${x}" y="${yt}" width="${bw}" height="${yb-yt}" fill="${q.c}"/>`;
    });
  }
  if (o.ref){
    const yr = Y(o.ref.v);
    s += `<line x1="${m.l}" x2="${W-m.r}" y1="${yr}" y2="${yr}" stroke="#c3c2b7" stroke-width="1"/><text class="reflab" x="${W-m.r+4}" y="${yr+4}">${o.ref.label}</text>`;
  }
  o.xt.forEach(([i,l]) => { s += `<text class="tick" x="${m.l+(i+0.5)*slot}" y="${H-6}" text-anchor="middle">${l}</text>`; });
  s += `</svg>`;
  el.innerHTML = s;
  const svg = el.querySelector('svg'), hb = svg.querySelector('.hb');
  hover(svg, ev => {
    const i = Math.floor((svgX(svg,ev,W)-m.l)/slot);
    if (i < 0 || i >= o.n){ hideTip(); hb.setAttribute('opacity',0); return; }
    hb.setAttribute('x', m.l+i*slot); hb.setAttribute('opacity', .07);
    showTip(o.tip(i), ev);
  }, () => hb.setAttribute('opacity',0));
}
const idxTicks = (labels, slotPx, minGap=46) => { const out=[]; const every=Math.max(1,Math.ceil(minGap/slotPx)); for(let i=labels.length-1;i>=0;i-=every) out.push([i,labels[i]]); return out; };

// ---------- sparkline ----------
function spark(el, vals, labels, fmt, color){
  const W = Math.max(el.clientWidth, 80), H = 34, pad = 5;
  const pts = vals.map((v,i)=>[i,v]).filter(p=>p[1]!=null);
  if (pts.length < 2){ el.innerHTML = ''; return; }
  const lo = Math.min(...pts.map(p=>p[1])), hi = Math.max(...pts.map(p=>p[1])), sp = (hi-lo)||1;
  const X = i => pad + i/(vals.length-1)*(W-2*pad), Y = v => pad + (1-(v-lo)/sp)*(H-2*pad);
  const d = pts.map((p,j)=>(j?'L':'M')+X(p[0]).toFixed(1)+','+Y(p[1]).toFixed(1)).join('');
  const lp = pts[pts.length-1];
  el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"><path d="${d}" fill="none" stroke="#5a5955" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle class="sd" r="4" fill="${color}" stroke="${CARD}" stroke-width="2" cx="${X(lp[0])}" cy="${Y(lp[1])}"/></svg>`;
  const svg = el.querySelector('svg'), dot = svg.querySelector('.sd');
  hover(svg, ev => {
    const i = Math.round((svgX(svg,ev,W)-pad)/(W-2*pad)*(vals.length-1)), k = nearest(pts.map(p=>p[0]), i);
    dot.setAttribute('cx', X(k)); dot.setAttribute('cy', Y(vals[k]));
    showTip(`<div class="t">${labels[k]}</div>${fmt(vals[k])}`, ev);
  }, () => { dot.setAttribute('cx', X(lp[0])); dot.setAttribute('cy', Y(lp[1])); });
}

// ---------- weeks ----------
const NW = 12;
const WEEKS = [...Array(NW)].map((_,i) => W0 - (NW-1-i)*7*DAY);
const wkLabel = w => w === W0 ? 'This wk' : fd(w);
function weekAgg(w){
  const out = {}; SPORTS.forEach(s => out[s.k] = {km:0, min:0, n:0, ld:0});
  D.acts.filter(a => a.t >= w && a.t < w+7*DAY).forEach(a => { const o = out[a.s]; o.km += a.km; o.min += a.min; o.n++; o.ld += a.ld||0; });
  return out;
}
const daysIn = (a, b) => D.days.filter(d => d.t >= a && d.t < b);
function weekly(fn){ return WEEKS.map(w => fn(w, w+7*DAY)); }

// ---------- readiness ----------
const MARK = {go:'✓', easy:'–', rest:'!', good:'✓', ok:'–', poor:'!', none:'·'};
function renderReady(){
  const R = D.coach.ready, lv = R.level || 'none';
  document.getElementById('ready').innerHTML =
    `<div class="call"><i class="badge ${lv}" aria-hidden="true">${MARK[lv]}</i><div>
      <div class="eyebrow">Today's call</div><h2>${esc(R.title)}</h2><p>${esc(R.advice)}</p>
      <p class="disc">A guide from your watch data, not medical advice.</p></div></div>
    <div class="sigs">${R.signals.map(g => `<div class="sig"><div class="lab"><i class="ic ${g.state}" aria-label="${g.state==='none'?'no data':g.state}">${MARK[g.state]}</i>${esc(g.label)}</div>
      <div class="val">${g.value==null?'–':esc(g.value)}</div><div class="sub">${esc(g.note)}</div></div>`).join('')}</div>`;
}

// ---------- hero: weekly goal and milestones ----------
function renderHero(){
  const G = D.coach.goal, km = G.unit === 'km', f = km ? n1 : n0;
  const im = G.value, goal = G.target, hit = im >= goal;
  const tg = (Number.isInteger(goal) ? n0 : n1)(goal);
  const pct = Math.min(im/goal, 1), size = document.getElementById('ring').clientWidth || 168, sw = 12, R = (size-sw)/2, C = 2*Math.PI*R;
  document.getElementById('goalLab').textContent = 'This week · ' + G.label.toLowerCase();
  document.getElementById('ring').innerHTML =
    `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${f(im)} of ${tg} ${G.unit} this week">
      <circle cx="${size/2}" cy="${size/2}" r="${R}" fill="none" stroke="var(--accent-track)" stroke-width="${sw}"/>
      <circle cx="${size/2}" cy="${size/2}" r="${R}" fill="none" stroke="${hit?'#0ca30c':'#3987e5'}" stroke-width="${sw}" stroke-linecap="round"
        stroke-dasharray="${C*pct} ${C}" transform="rotate(-90 ${size/2} ${size/2})"/></svg>
     <div class="c"><div class="hero-num">${f(im)}</div><div class="hero-of">of ${tg} ${G.unit}</div></div>`;
  const names = ['M','T','W','T','F','S','S'];
  document.getElementById('dots').innerHTML = names.map((n,i) => {
    const t = W0 + i*DAY, d = dayMap[t], active = actsOn(t).length || (d && d.im >= 10);
    const cls = t > TODAY ? 'fut' : (active ? 'on' : '');
    return `<div class="dot ${cls}" title="${fdy(t)}"><i></i>${n}</div>`;
  }).join('');
  const days = `${G.left} day${G.left===1?'':'s'} left`;
  const todo = hit ? 'Goal reached this week. Nice work.'
    : G.type === 'runs' ? `<b>${G.need} more run${G.need===1?'':'s'}</b> with ${days}.`
    : `<b>${f(G.need)} ${G.unit} to go</b> with ${days}, about ${f(G.perDay)} a day.`;
  const streak = G.streak ? `<b>${G.streak}-week streak</b>${G.best > G.streak ? ` (best ${G.best})` : ''}.` : 'Hit it this week to start a streak.';
  const src = G.source === 'you' ? '' : `<br><span style="color:var(--muted)">${G.source==='garmin'?'Goal from Garmin Connect.':'Suggested goal.'} Set your own in Settings.</span>`;
  document.getElementById('heroNote').innerHTML = `${todo}<br>${streak}`
    + (G.of ? ` You hit it in ${G.hits} of the last ${G.of} full week${G.of===1?'':'s'}.` : '') + src;

  // Milestones
  const L = D.coach.ladder;
  document.getElementById('ladder').innerHTML = L.steps.map((st,i) => {
    const cls = st.date ? 'done' : i === L.next ? 'next' : 'fut';
    const sub = st.date ? 'Reached ' + fd(P(st.date), {day:'numeric', month:'short', year:'numeric'})
      : i === L.next && L.togo != null ? `Next · ${n1(L.togo)} km to go` : (i === L.next ? 'Next · ' : '') + st.sub;
    return `<li class="${cls}"><i aria-hidden="true">${st.date ? '✓' : ''}</i><div><b>${esc(st.label)}</b><span>${esc(sub)}</span></div></li>`;
  }).join('');
  const nx = L.next == null ? null : L.steps[L.next];
  document.getElementById('ladTip').innerHTML = (nx ? `<b>Next: ${esc(nx.label)}.</b> ` : '') + esc(L.tip);
  const last28 = D.acts.filter(a => a.t > TODAY-28*DAY);
  document.getElementById('roadFacts').innerHTML =
    `<span>Longest run <b>${L.longest ? n1(L.longest.km)+' km' : '–'}</b>${L.longest ? ` (${fd(P(L.longest.d))})` : ''}</span>`
    + `<span>Runs so far <b>${L.runs}</b></span>`
    + `<span>Sessions, last 4 weeks <b>${last28.length}</b></span>`;
}

// ---------- tiles ----------
function delta(v, ref, upGood, fmt, word){
  if (v == null || ref == null) return '';
  const d = v - ref; if (Math.abs(d) < 1e-9) return `level with ${word}`;
  const cls = upGood == null ? '' : ((d>0) === upGood ? 'up-good' : 'up-bad');
  return `<span class="${cls}">${d>0?'▲':'▼'} ${fmt(Math.abs(d))}</span> vs ${word}`;
}
function renderTiles(){
  const wkLabels = WEEKS.map(w => 'Week of ' + fd(w));
  const last7 = [TODAY-7*DAY, TODAY];               // 7 complete days before today
  const prev4 = [W0-28*DAY, W0];
  const T = [];

  const kmW = weekly((a,b)=>sum(D.acts.filter(x=>x.t>=a&&x.t<b).map(x=>x.km)));
  const dIdx = Math.round((TODAY-W0)/DAY);   // same point in earlier weeks (Mon..today's weekday)
  const km4 = sum([1,2,3,4].map(k => sum(D.acts.filter(x=>x.t>=W0-k*7*DAY && x.t<=W0-k*7*DAY+dIdx*DAY).map(x=>x.km))))/4;
  T.push({lab:'Distance this week', val:n1(kmW[NW-1]), u:'km', dl:delta(kmW[NW-1], km4, true, n1, 'usual by now'), sp:kmW, f:v=>n1(v)+' km'});

  const stepsW = weekly((a,b)=>avg(daysIn(a,b).map(d=>d.st)));
  const st7 = avg(daysIn(...last7).map(d=>d.st)), sg = (D.days.length && D.days[D.days.length-1].sg) || null;
  T.push({lab:'Steps, 7-day avg', val:n0(st7), u:'', dl: sg ? delta(st7, sg, true, n0, 'goal') : '', sp:stepsW, f:v=>n0(v)+' steps/day'});

  const rhrW = weekly((a,b)=>avg(daysIn(a,b).map(d=>d.rhr)));
  const r7 = avg(daysIn(TODAY-6*DAY, TODAY+DAY).map(d=>d.rhr)), r90 = avg(daysIn(TODAY-97*DAY, TODAY-6*DAY).map(d=>d.rhr));
  T.push({lab:'Resting HR, 7-day', val:n0(r7), u:'bpm', dl:delta(r7!=null?Math.round(r7):null, r90!=null?Math.round(r90):null, false, n0, '90-day avg'), sp:rhrW, f:v=>n0(v)+' bpm'});

  const hrvIn = (a,b) => D.hrv.filter(h=>h.t>=a&&h.t<b).map(h=>h.v);
  const hrvW = weekly((a,b)=>avg(hrvIn(a,b)));
  const h7 = avg(hrvIn(TODAY-6*DAY, TODAY+DAY)), hLast = D.hrv[D.hrv.length-1];
  const building = hLast && (!hLast.st || hLast.st === 'NONE');
  T.push({lab:'HRV, 7-night avg', val:n0(h7), u:'ms', dl: h7==null ? 'No readings this week' : (building ? 'Garmin is still building your baseline' : esc((hLast.st||'').toLowerCase())), sp:hrvW, f:v=>n0(v)+' ms'});

  const slIn = (a,b) => D.sleep.filter(s=>s.t>=a&&s.t<b);
  const slW = weekly((a,b)=>avg(slIn(a,b).map(s=>s.h)));
  const s7 = slIn(TODAY-6*DAY, TODAY+DAY), sh = avg(s7.map(s=>s.h)), sc = avg(s7.map(s=>s.sc));
  T.push({lab:'Sleep, last 7 nights', val: sh==null ? '–' : hm(sh*60).replace(' ',''), u:'', dl:`${s7.length} of 7 nights tracked${sc!=null?` · score ${n0(sc)}`:''}`, sp:slW, f:v=>hm(v*60)+' per night'});

  const ldW = weekly((a,b)=>sum(D.acts.filter(x=>x.t>=a&&x.t<b).map(x=>x.ld)));
  const l7 = sum(D.acts.filter(x=>x.t>TODAY-7*DAY).map(x=>x.ld)), l4 = sum(D.acts.filter(x=>x.t>=prev4[0]&&x.t<prev4[1]).map(x=>x.ld))/4;
  T.push({lab:'Training load, 7-day', val:n0(l7), u:'', dl:delta(l7, l4, null, n0, '4-wk avg'), sp:ldW, f:v=>'load '+n0(v)});

  const el = document.getElementById('tiles');
  el.innerHTML = T.map((t,i) => `<div class="card tile"><div class="lab">${t.lab}</div><div class="val">${t.val}${t.u?`<small>${t.u}</small>`:''}</div><div class="dl">${t.dl}</div><div class="spark" id="sp${i}"></div></div>`).join('');
  T.forEach((t,i) => spark(document.getElementById('sp'+i), t.sp.map(v => v || v===0 ? v : null), wkLabels, t.f, '#3987e5'));
}

// ---------- this week vs recent weeks ----------
let wkMetric = 'km';
const MET = {km:{t:'Distance', f:v=>n1(v)+' km', ax:v=>v}, min:{t:'Time', f:v=>hm(v*60), ax:v=>v+'h'}, n:{t:'Sessions', f:v=>n0(v), ax:v=>v}};
function renderWeeks(){
  const aggs = WEEKS.map(weekAgg), M = MET[wkMetric];
  const V = (a,k) => wkMetric === 'min' ? a[k].min/60 : a[k][wkMetric];
  document.getElementById('wkTitle').textContent = `${M.t} by sport, last ${NW} weeks`;
  const sps = usedSports.filter(s => wkMetric !== 'km' || aggs.some(a=>a[s.k].km>0));
  const el = document.getElementById('wkChart');
  const slotPx = (el.clientWidth-50)/NW;
  colChart(el, {
    n: NW, hl: NW-1, h: 210,
    stacks: sps.map(s => ({c:s.c, v:aggs.map(a=>V(a,s.k))})),
    fmt: M.ax, xt: idxTicks(WEEKS.map(wkLabel), slotPx, 52), empty: 'No activities yet.',
    tip: i => `<div class="t">${i===NW-1?'This week':'Week of '+fd(WEEKS[i])}</div>` +
      sps.filter(s=>aggs[i][s.k].n).map(s => row(s.name, M.f(V(aggs[i],s.k)), s.c)).join('') +
      row('Total', M.f(sum(sps.map(s=>V(aggs[i],s.k))))),
  });
  document.getElementById('wkLegend').innerHTML = sps.map(s => `<span><i style="background:${s.c}"></i>${s.name}</span>`).join('');
  const prev = aggs.slice(NW-5, NW-1), cur = aggs[NW-1];
  const rows = usedSports.filter(s => cur[s.k].n || prev.some(a=>a[s.k].n)).map(s => {
    const a = k => sum(prev.map(p=>p[s.k][k]))/4;
    return `<tr><td><span class="sw" style="background:${s.c}"></span>${s.name}</td><td class="n">${cur[s.k].n}</td><td class="n">${n1(a('n'))}</td><td class="n">${n1(cur[s.k].km)}</td><td class="n">${n1(a('km'))}</td><td class="n">${hm(cur[s.k].min)}</td><td class="n">${hm(a('min'))}</td></tr>`;
  });
  const T = k => sum(usedSports.map(s=>cur[s.k][k])), A = k => sum(prev.flatMap(p=>usedSports.map(s=>p[s.k][k])))/4;
  document.getElementById('wkTable').innerHTML = `<table><thead><tr><th></th><th class="n">Sessions</th><th class="n">4-wk avg</th><th class="n">km</th><th class="n">4-wk avg</th><th class="n">Time</th><th class="n">4-wk avg</th></tr></thead><tbody>${rows.join('')}
    <tr class="tot"><td>This week</td><td class="n">${T('n')}</td><td class="n">${n1(A('n'))}</td><td class="n">${n1(T('km'))}</td><td class="n">${n1(A('km'))}</td><td class="n">${hm(T('min'))}</td><td class="n">${hm(A('min'))}</td></tr></tbody></table>`;
}
function renderIM(){
  const el = document.getElementById('imChart'), goal = (D.days.length && D.days[D.days.length-1].img) || 150;
  const v = weekly((a,b)=>sum(daysIn(a,b).map(d=>d.im)));
  colChart(el, {
    n: NW, hl: NW-1, h: 170, ref: {v: goal, label: goal+' goal'},
    stacks: [{c:'#3987e5', v}], xt: idxTicks(WEEKS.map(wkLabel), (el.clientWidth-80)/NW, 52),
    tip: i => `<div class="t">${i===NW-1?'This week':'Week of '+fd(WEEKS[i])}</div>${row('Active minutes', n0(v[i]))}${row('Goal', goal)}`,
  });
}

// ---------- heatmap ----------
const HM = [{max:2500,c:'#184f95'},{max:5000,c:'#256abf'},{max:7500,c:'#3987e5'},{max:10000,c:'#6da7ec'},{max:Infinity,c:'#b7d3f6'}];
const hmColor = st => st ? HM.find(b => st < b.max).c : '#262624';
function renderHeat(){
  const el = document.getElementById('heat'), Wd = Math.max(el.clientWidth, 240);
  const cols = Wd < 560 ? 26 : 53, lab = 18, top = 16;
  const cell = Math.min(15, Math.floor((Wd - lab) / cols)), sz = cell - 2;
  const first = W0 - (cols-1)*7*DAY, H = top + 7*cell;
  let s = `<svg class="chart" viewBox="0 0 ${Wd} ${H}" width="${Wd}" height="${H}">`;
  ['M','','W','','F','',''].forEach((d,i) => { if(d) s += `<text class="tick" x="0" y="${top+i*cell+sz-1}">${d}</text>`; });
  let lastM = -1, lastX = -99;
  for (let c = 0; c < cols; c++){
    const w = first + c*7*DAY, mo = new Date(w+6*DAY).getUTCMonth(), x = lab + c*cell;
    if (mo !== lastM && x - lastX > 26){ s += `<text class="tick" x="${x}" y="11">${fd(w+6*DAY,{month:'short'})}</text>`; lastX = x; }
    lastM = mo;
    for (let r = 0; r < 7; r++){
      const t = w + r*DAY; if (t > TODAY) continue;
      const d = dayMap[t];
      s += `<rect x="${x}" y="${top+r*cell}" width="${sz}" height="${sz}" rx="3" fill="${hmColor(d&&d.st)}"/>`;
    }
  }
  s += `<rect class="hl" width="${sz+2}" height="${sz+2}" rx="4" fill="none" stroke="#f0efec" stroke-width="1.5" visibility="hidden"/></svg>`;
  el.innerHTML = s;
  const svg = el.querySelector('svg'), hl = svg.querySelector('.hl');
  hover(svg, ev => {
    const c = Math.floor((svgX(svg,ev,Wd)-lab)/cell), r = Math.floor((svgY(svg,ev,H)-top)/cell);
    const t = first + c*7*DAY + r*DAY;
    if (c<0||c>=cols||r<0||r>6||t>TODAY){ hideTip(); hl.setAttribute('visibility','hidden'); return; }
    hl.setAttribute('x', lab+c*cell-1); hl.setAttribute('y', top+r*cell-1); hl.setAttribute('visibility','visible');
    const d = dayMap[t], acts = actsOn(t);
    showTip(`<div class="t">${fdy(t)}</div>` + (d ? row('Steps', n0(d.st)) + row('Active min', n0(d.im)) + (d.rhr?row('Resting HR', d.rhr+' bpm'):'') : '<div>No watch data</div>')
      + acts.map(a => row(SP[a.s].name, a.km ? n1(a.km)+' km' : hm(a.min), SP[a.s].c)).join(''), ev);
  }, () => hl.setAttribute('visibility','hidden'));
  document.getElementById('hmLegend').innerHTML = `<span style="margin-right:4px">Steps</span><i style="background:#262624"></i> none `
    + HM.map((b,i) => `<i style="background:${b.c}"></i> ${i===HM.length-1?'10k+':'<'+(b.max/1000)+'k'}`).join(' ');
}

// ---------- trends ----------
let rangeM = 3;
function renderTrends(){
  const x1 = TODAY, x0 = Date.UTC(new Date(TODAY).getUTCFullYear(), new Date(TODAY).getUTCMonth()-rangeM, new Date(TODAY).getUTCDate());

  // Resting HR
  const rhr = D.days.filter(d=>d.rhr).map(d=>[d.t, d.rhr]);
  const roll = rhr.map(([t]) => [t, avg(rhr.filter(q=>q[0]>t-7*DAY && q[0]<=t).map(q=>q[1]))]);
  const rollMap = new Map(roll);
  lineChart(document.getElementById('rhrChart'), {x0, x1, h:190, gap:4,
    series:[{c:'#5a5955', w:1.5, pts:rhr}, {c:'#3987e5', w:2.5, pts:roll}], fmt:v=>v,
    tip: t => { const d = dayMap[t]; return `<div class="t">${fdy(t)}</div>${row('Resting HR', (d&&d.rhr)+' bpm','#5a5955')}${row('7-day avg', n1(rollMap.get(t))+' bpm','#3987e5')}`; }});
  document.getElementById('rhrLegend').innerHTML = `<span><i class="ln" style="background:#5a5955"></i>Daily</span><span><i class="ln" style="background:#3987e5"></i>7-day average</span>`;

  // HRV
  const hv = D.hrv.map(h=>[h.t, h.v]), hmap = Object.fromEntries(D.hrv.map(h=>[h.t,h]));
  lineChart(document.getElementById('hrvChart'), {x0, x1, h:190, gap:3, empty:'No HRV readings in this range. Wear your watch to bed to get them.',
    series:[{c:'#3987e5', pts:hv, dots:true}], fmt:v=>v,
    tip: t => { const h = hmap[t]; return `<div class="t">${fdy(t)}</div>${row('Overnight HRV', h.v+' ms')}${h.w?row('7-day avg', h.w+' ms'):''}${h.st&&h.st!=='NONE'?row('Status', esc(h.st.toLowerCase())):''}`; }});
  const nIn = D.hrv.filter(h=>h.t>=x0).length;
  document.getElementById('hrvNote').innerHTML = `<span>${nIn} night${nIn===1?'':'s'} with a reading in this range${D.hrv.length && (!D.hrv[D.hrv.length-1].st || D.hrv[D.hrv.length-1].st==='NONE') ? '. Garmin needs about 3 weeks of nightly wear to set your baseline.' : ''}</span>`;

  // Sleep, stacked by stage
  const nd = Math.round((x1-x0)/DAY)+1, dts = [...Array(nd)].map((_,i)=>x0+i*DAY);
  const smap = Object.fromEntries(D.sleep.map(s=>[s.t,s]));
  const ST = [{k:'dp',n:'Deep',c:'#3987e5'},{k:'rm',n:'REM',c:'#d95926'},{k:'lt',n:'Light',c:'#199e70'},{k:'aw',n:'Awake',c:'#c98500'}];
  const sEl = document.getElementById('sleepChart'), slot = (sEl.clientWidth-50)/nd;
  const mt = []; let lastPx = -99;
  dts.forEach((t,i) => { const d = new Date(t); const isTick = (nd<=45 ? d.getUTCDay()===1 : d.getUTCDate()===1); if (isTick && (i*slot-lastPx)>=44){ mt.push([i, nd<=45?fd(t):fd(t,{month:'short'})]); lastPx=i*slot; } });
  colChart(sEl, {n:nd, h:190, fmt:v=>v+'h', xt:mt, empty:'No nights tracked in this range.',
    stacks: ST.map(st => ({c:st.c, v:dts.map(t=>smap[t]?smap[t][st.k]:0)})),
    tip: i => { const s = smap[dts[i]]; return `<div class="t">Night of ${fdy(dts[i]-DAY)}</div>` + (s ? row('Asleep', hm(s.h*60)) + ST.map(st=>row(st.n, hm(s[st.k]*60), st.c)).join('') + (s.sc?row('Sleep score', s.sc):'') : 'Not tracked'); }});
  const nN = D.sleep.filter(s=>s.t>=x0).length;
  document.getElementById('sleepLegend').innerHTML = ST.map(st=>`<span><i style="background:${st.c}"></i>${st.n}</span>`).join('') + `<span style="color:var(--muted)">${nN} of ${nd} nights tracked</span>`;

  // Training load per week, by sport
  const ws = []; for (let w = MON(x0); w <= W0; w += 7*DAY) ws.push(w);
  const ag = ws.map(weekAgg), lEl = document.getElementById('loadChart');
  const lsp = usedSports.filter(s => ag.some(a=>a[s.k].ld>0));
  colChart(lEl, {n:ws.length, hl:ws.length-1, h:190, empty:'No training load in this range.',
    stacks: lsp.map(s=>({c:s.c, v:ag.map(a=>a[s.k].ld)})),
    xt: idxTicks(ws.map(w=>w===W0?'This wk':fd(w)), (lEl.clientWidth-50)/ws.length, 52),
    tip: i => `<div class="t">${ws[i]===W0?'This week':'Week of '+fd(ws[i])}</div>` + lsp.filter(s=>ag[i][s.k].ld).map(s=>row(s.name, n0(ag[i][s.k].ld), s.c)).join('') + row('Total', n0(sum(lsp.map(s=>ag[i][s.k].ld))))});
  document.getElementById('loadLegend').innerHTML = lsp.map(s=>`<span><i style="background:${s.c}"></i>${s.name}</span>`).join('');
}

// ---------- zones ----------
function renderZones(){
  const acts = D.acts.filter(a => a.t > TODAY-28*DAY);
  const z = [0,1,2,3,4].map(i => sum(acts.map(a=>a.z[i])));
  const tot = sum(z), el = document.getElementById('zbar');
  const ZC = ['#1c5cab','#2a78d6','#5598e7','#86b6ef','#b7d3f6'], ZI = ['#ffffff','#ffffff','#0b0b0b','#0b0b0b','#0b0b0b'];
  const ZN = ['Zone 1 · warm-up','Zone 2 · easy','Zone 3 · aerobic','Zone 4 · threshold','Zone 5 · max'];
  const fl = D.zones;
  if (!tot){ el.innerHTML = '<div class="empty" style="width:100%">No heart rate data from activities in the last 4 weeks.</div>'; return; }
  el.innerHTML = z.map((v,i) => v ? `<div style="flex:${v};background:${ZC[i]};color:${ZI[i]}" data-i="${i}">${v/tot>=0.08?Math.round(v/tot*100)+'%':''}</div>` : '').join('');
  el.setAttribute('aria-label', z.map((v,i)=>`${ZN[i]} ${Math.round(v/tot*100)}%`).join(', '));
  el.querySelectorAll('div').forEach(d => hover(d, ev => { const i = +d.dataset.i;
    showTip(`<div class="t">${ZN[i]}</div>${fl[i]?row('Heart rate', fl[i]+'–'+((fl[i+1]||fl[5]||'')-(i<4?1:0))+' bpm'):''}${row('Time', hm(z[i]))}${row('Share', Math.round(z[i]/tot*100)+'%')}`, ev); }));
  document.getElementById('zLegend').innerHTML = ZN.map((n,i)=>`<span><i style="background:${ZC[i]}"></i>${n}${fl[i]?` (${fl[i]}+)`:''}</span>`).join('');
  const easy = Math.round((z[0]+z[1])/tot*100);
  document.getElementById('zoneQ').innerHTML = `Time in each heart rate zone, across ${acts.length} activities (${hm(tot)}). <b style="color:var(--ink)">${easy}%</b> was easy (zones 1–2). Endurance training is usually built mostly from easy work.`;
}

// ---------- records ----------
function renderRecords(){
  const maxBy = (arr, f) => arr.reduce((b,x) => (f(x)??-Infinity) > (b?f(b)??-Infinity:-Infinity) ? x : b, null);
  const minBy = (arr, f) => arr.reduce((b,x) => (f(x)??Infinity) < (b?f(b)??Infinity:Infinity) ? x : b, null);
  const R = [];
  const A = (lab, a, v) => a && R.push({lab, val:v(a), sub:`${fd(a.t,{day:'numeric',month:'short',year:'numeric'})} · ${esc(a.n||a.ty)}`});
  A('Longest distance', maxBy(D.acts,a=>a.km), a=>n1(a.km)+' km');
  A('Longest session', maxBy(D.acts,a=>a.min), a=>hm(a.min));
  A('Most climbing', maxBy(D.acts.filter(a=>a.el>0),a=>a.el), a=>n0(a.el)+' m');
  A('Hardest session', maxBy(D.acts.filter(a=>a.ld),a=>a.ld), a=>'load '+n0(a.ld));
  const sd = maxBy(D.days,d=>d.st); sd && R.push({lab:'Most steps in a day', val:n0(sd.st), sub:fd(sd.t,{weekday:'short',day:'numeric',month:'short',year:'numeric'})});
  const allW = []; if (D.acts.length) for (let w = MON(D.acts[0].t); w <= W0; w += 7*DAY) allW.push([w, sum(D.acts.filter(a=>a.t>=w&&a.t<w+7*DAY).map(a=>a.km))]);
  const bw = maxBy(allW, x=>x[1]); bw && R.push({lab:'Biggest week', val:n1(bw[1])+' km', sub:'Week of '+fd(bw[0],{day:'numeric',month:'short',year:'numeric'})});
  const lr = minBy(D.days.filter(d=>d.rhr), d=>d.rhr); lr && R.push({lab:'Lowest resting HR', val:lr.rhr+' bpm', sub:fd(lr.t,{day:'numeric',month:'short',year:'numeric'})});
  const bs = maxBy(D.sleep.filter(s=>s.sc), s=>s.sc); bs && R.push({lab:'Best sleep score', val:bs.sc, sub:`${hm(bs.h*60)} · ${fd(bs.t,{day:'numeric',month:'short',year:'numeric'})}`});
  document.getElementById('recs').innerHTML = R.map(r=>`<div class="card rec"><div class="lab">${r.lab}</div><div class="val">${r.val}</div><div class="sub">${r.sub}</div></div>`).join('');
  const big = [...D.acts].sort((a,b)=>b.km-a.km || b.min-a.min).slice(0,5);
  document.getElementById('bigTable').innerHTML = big.length ? `<table><thead><tr><th>Date</th><th>Activity</th><th class="n">Distance</th><th class="n">Time</th><th class="n">Pace</th><th class="n">Avg HR</th><th class="n">Load</th></tr></thead><tbody>`
    + big.map(a => { const pace = a.km>0 ? a.min/a.km : null;
      return `<tr><td>${fd(a.t,{day:'numeric',month:'short'})}</td><td><span class="sw" style="background:${SP[a.s].c}"></span>${esc(a.n||a.ty)}</td><td class="n">${n1(a.km)} km</td><td class="n">${hm(a.min)}</td><td class="n">${pace&&ONFOOT.has(a.s)?Math.floor(pace)+':'+String(Math.round(pace%1*60)).padStart(2,'0')+' /km':'–'}</td><td class="n">${a.hr||'–'}</td><td class="n">${a.ld||'–'}</td></tr>`; }).join('')
    + `</tbody></table>` : '<div class="empty">No activities yet.</div>';
}

// ---------- today ----------
const hhmm = m => { m = Math.round(m); return String(Math.floor(m/60)%24).padStart(2,'0') + ':' + String(m%60).padStart(2,'0'); };
const mmss = s => { s = Math.round(s); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); };
const paceStr = minPerKm => minPerKm == null ? '–' : mmss(minPerKm*60);
const tm = (lab, val, unit, sub, extra='') => `<div class="tm"><div class="lab">${lab}</div><div class="val">${val}${unit?`<small>${unit}</small>`:''}</div>${sub?`<div class="sub">${sub}</div>`:''}${extra}</div>`;
const meter = (v, goal) => goal ? `<div class="mbar"><i style="width:${Math.min(100, v/goal*100)}%;${v>=goal?'background:#0ca30c':''}"></i></div>` : '';

function renderToday(){
  const T = D.td;
  const el = document.getElementById('today'), t0 = P(T.date);
  const im = T.imMod + 2*T.imVig;
  const st = T.stressMix, stTot = (st.rest+st.low+st.medium+st.high) || 1;
  const SM = [['rest','Rest','#1c5cab'],['low','Low','#5598e7'],['medium','Medium','#c98500'],['high','High','#d95926']];
  const stressBar = `<div class="thin">${SM.map(([k,,c]) => st[k] ? `<i style="flex:${st[k]};background:${c}"></i>` : '').join('')}</div>`;
  const wk = sum(daysIn(W0, W0+7*DAY).map(d=>d.im));
  const ss = T.sleepStages;
  const sleepBar = ss ? `<div class="thin">${[['deep','#3987e5'],['rem','#d95926'],['light','#199e70'],['awake','#c98500']].map(([k,c]) => ss[k] ? `<i style="flex:${ss[k]};background:${c}"></i>` : '').join('')}</div>` : '';
  el.innerHTML = `
    <div class="todayhead"><div><div class="eyebrow">Today</div><h2>${fd(t0,{weekday:'long',day:'numeric',month:'long'})}</h2></div>
      <div class="meta">${T.asof ? `Watch last synced ${T.asof}` : ''}${T.acts.length ? ` · ${T.acts.length} activit${T.acts.length===1?'y':'ies'}` : ''}</div></div>
    <div class="tgrid">
      ${tm('Steps', n0(T.steps), '', T.stepsGoal ? `goal ${n0(T.stepsGoal)} · ${n1(T.km)} km` : `${n1(T.km)} km`, meter(T.steps, T.stepsGoal))}
      ${tm('Active minutes', n0(im), 'min', `${T.imMod} mod · ${T.imVig} vig (×2)`, meter(wk, 150))}
      ${tm('Calories', n0(T.calAct), 'active', `${n0(T.calTot)} total`)}
      ${tm('Resting HR', T.rhr ?? '–', 'bpm', T.rhr7 ? `7-day avg ${T.rhr7}` : '')}
      ${tm('Heart rate range', T.hrMin && T.hrMax ? `${T.hrMin}–${T.hrMax}` : '–', 'bpm', 'low – high today')}
      ${tm('Body Battery', T.bbNow ?? '–', 'now', T.bbUp!=null ? `+${T.bbUp} / −${T.bbDown} today` : '')}
      ${tm('Stress', T.stress && T.stress>0 ? T.stress : '–', 'avg', `${Math.round((st.rest+st.low)/stTot*100)}% calm · peak ${T.stressMax ?? '–'}`, stressBar)}
      ${tm('Sleep last night', T.sleepH ? hm(T.sleepH*60) : '–', '', T.sleepScore ? `score ${T.sleepScore}${T.sleepQual?' · '+T.sleepQual.toLowerCase():''}` : 'not tracked', sleepBar)}
      ${tm('HRV last night', T.hrv ?? '–', T.hrv ? 'ms' : '', T.hrvWeek ? `7-day avg ${T.hrvWeek} ms` : (T.hrv ? '' : 'not tracked'))}
      ${tm('Breathing', T.resp ?? '–', T.resp ? 'brpm' : '', 'average today')}
      ${tm('Floors climbed', T.floors ?? '–', '', T.floorsGoal ? `goal ${T.floorsGoal}` : '', meter(T.floors||0, T.floorsGoal))}
      ${tm('Water', T.waterMl ? n1(T.waterMl/1000) : '0', 'L', T.waterGoal ? `goal ${n1(T.waterGoal/1000)} L` : '')}
    </div>
    <div class="tcharts">
      <div><h3>Heart rate through the day</h3><div class="chartbox" id="tHR"></div></div>
      <div><h3>Body Battery</h3><div class="chartbox" id="tBB"></div></div>
    </div>
    <div class="legend" id="tLegend"></div>`;

  // Shade activity windows on the day charts
  const windows = T.acts.map(a => { const [h,m] = a.start.split(':').map(Number); return {a, x0: h*60+m, x1: h*60+m+a.min}; });
  const endMin = Math.max(60, ...T.hr.map(p=>p[0]), ...T.bb.map(p=>p[0]));
  dayLine(document.getElementById('tHR'), T.hr, {c:'#e66767', h:170, x1: Math.min(1440, Math.ceil(endMin/60)*60), windows,
    fmt: v => v, tip: (m, v) => `<div class="t">${hhmm(m)}</div>${row('Heart rate', v+' bpm')}` + windows.filter(w=>m>=w.x0&&m<=w.x1).map(w=>row(SP[w.a.s].name, esc(w.a.name))).join('')});
  dayLine(document.getElementById('tBB'), T.bb, {c:'#3987e5', h:170, x1: Math.min(1440, Math.ceil(endMin/60)*60), windows, step:true, lo:0, hi:100,
    fmt: v => v, tip: (m, v) => `<div class="t">${hhmm(m)}</div>${row('Body Battery', v)}`});
  document.getElementById('tLegend').innerHTML = windows.length ? `<span><i style="background:rgba(255,255,255,.12)"></i>Shaded: your activities</span>` : '';

  // Activity cards
  document.getElementById('todayActs').innerHTML = T.acts.length
    ? T.acts.map((a,i) => `<section class="card act" id="act${i}"></section>`).join('')
    : `<section class="card act"><div class="empty">No activities yet today. Detailed graphs show up here after your watch syncs one.</div></section>`;
  T.acts.forEach((a,i) => renderActivity(document.getElementById('act'+i), a));
}

// A day-long line (x = minutes after midnight)
function dayLine(el, pts, o){
  const W = Math.max(el.clientWidth, 240), H = o.h, m = {l:34, r:10, t:10, b:24};
  if (!pts.length){ el.innerHTML = '<div class="empty">No data yet today.</div>'; return; }
  const vals = pts.map(p=>p[1]);
  let lo = o.lo ?? Math.min(...vals), hi = o.hi ?? Math.max(...vals);
  if (o.lo == null){ const sp = (hi-lo)||10; lo -= sp*.1; hi += sp*.1; }
  const tk = nice(lo, hi, 4); lo = tk[0]; hi = tk[tk.length-1];
  const x1 = o.x1, X = v => m.l + v/x1*(W-m.l-m.r), Y = v => m.t + (1-(v-lo)/(hi-lo))*(H-m.t-m.b);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  o.windows.forEach(w => { s += `<rect x="${X(w.x0)}" y="${m.t}" width="${Math.max(2, X(w.x1)-X(w.x0))}" height="${H-m.t-m.b}" fill="#ffffff" opacity=".08" rx="3"/>`; });
  tk.forEach(v => { s += `<line class="grid" x1="${m.l}" x2="${W-m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text class="tick" x="${m.l-6}" y="${Y(v)+4}" text-anchor="end">${o.fmt(v)}</text>`; });
  const every = [60,120,180,240,360].find(e => (W-m.l-m.r)*e/x1 >= 50) || 720;
  for (let h = 0; h <= x1; h += every){ const px = X(h); if (px > W-m.r-10 && h) continue; s += `<text class="tick" x="${px}" y="${H-6}" text-anchor="middle">${hhmm(h)}</text>`; }
  let d = '', prev = null;
  pts.forEach((p,j) => {
    const gap = prev && p[0]-prev[0] > 30 && !o.step;
    if (o.step && prev) d += `L${X(p[0]).toFixed(1)},${Y(prev[1]).toFixed(1)}`;
    d += (j===0 || gap ? 'M' : 'L') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1); prev = p;
  });
  s += `<path d="${d}" fill="none" stroke="${o.c}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  const last = pts[pts.length-1];
  s += `<circle cx="${X(last[0])}" cy="${Y(last[1])}" r="4" fill="${o.c}" stroke="${CARD}" stroke-width="2"/>`;
  s += `<line class="xh" y1="${m.t}" y2="${H-m.b}" visibility="hidden"/><circle class="hd" r="5" fill="${o.c}" stroke="${CARD}" stroke-width="2" visibility="hidden"/></svg>`;
  el.innerHTML = s;
  const svg = el.querySelector('svg'), xh = svg.querySelector('.xh'), hd = svg.querySelector('.hd');
  hover(svg, ev => {
    const mm = (svgX(svg,ev,W)-m.l)/(W-m.l-m.r)*x1;
    let p = pts[0]; for (const q of pts) if (Math.abs(q[0]-mm) < Math.abs(p[0]-mm)) p = q;
    if (o.step){ const before = pts.filter(q=>q[0]<=mm); if (before.length) p = [mm < 0 ? 0 : Math.min(mm, last[0]), before[before.length-1][1]]; }
    xh.setAttribute('x1', X(p[0])); xh.setAttribute('x2', X(p[0])); xh.setAttribute('visibility','visible');
    hd.setAttribute('cx', X(p[0])); hd.setAttribute('cy', Y(p[1])); hd.setAttribute('visibility','visible');
    showTip(o.tip(p[0], p[1]), ev);
  }, () => { xh.setAttribute('visibility','hidden'); hd.setAttribute('visibility','hidden'); });
}

const smooth = (a, k) => a.map((_,i) => { let s = 0, n = 0; for (let j = Math.max(0,i-k); j <= Math.min(a.length-1,i+k); j++) if (a[j] != null){ s += a[j]; n++; } return n ? s/n : null; });
const pct = (a, p) => { const b = a.filter(v=>v!=null).sort((x,y)=>x-y); return b.length ? b[Math.min(b.length-1, Math.floor(p*(b.length-1)))] : null; };

function renderActivity(el, a){
  const S = a.series, sp = SP[a.s];
  const end = (() => { const [h,m] = a.start.split(':').map(Number); return hhmm(h*60+m+a.min); })();
  const pace = a.spd ? 1000/a.spd/60 : null;
  const onFoot = ONFOOT.has(a.s);
  const stats = [
    ['Distance', a.km ? n1(a.km) : '–', a.km ? 'km' : '', ''],
    ['Time', hm(a.min), '', a.moving && Math.abs(a.moving-a.min) > 0.5 ? `${hm(a.moving)} moving` : ''],
    onFoot ? ['Avg pace', paceStr(pace), '/km', 'while moving'] : ['Avg speed', a.spd ? n1(a.spd*3.6) : '–', 'km/h', ''],
    ['Heart rate', a.hr ?? '–', 'bpm avg', a.mx ? `max ${a.mx}` : ''],
    ['Calories', n0(a.cal), '', ''],
    ['Training load', a.ld ?? '–', '', ''],
    ['Aerobic effect', a.te != null ? n1(a.te) : '–', '/ 5', a.teLabel || ''],
    onFoot ? ['Cadence', a.cad ?? '–', 'spm', a.steps ? `${n0(a.steps)} steps` : ''] : ['Elevation gain', n0(a.el), 'm', ''],
  ];
  if (a.pw) stats.push(['Power', a.pw, 'W avg', '']);
  if (a.rr) stats.push(['Breathing', a.rr, 'brpm', 'average']);
  // Run / walk / stand split
  const SK = {RWD_RUN:['Running','#d95926'], RWD_WALK:['Walking','#3987e5'], RWD_STAND:['Standing','#5a5955']};
  const spl = a.splits.filter(x => SK[x.k] && x.min > 0), splTot = sum(spl.map(x=>x.min));
  const zTot = sum(a.z), ZC = ['#1c5cab','#2a78d6','#5598e7','#86b6ef','#b7d3f6'], ZI = ['#fff','#fff','#0b0b0b','#0b0b0b','#0b0b0b'];
  const segBar = (parts) => `<div class="zbar">${parts.map(p => p.v ? `<div style="flex:${p.v};background:${p.c};color:${p.ink||'#fff'}" data-tip="${esc(p.tip)}">${p.share>=0.12?Math.round(p.share*100)+'%':''}</div>` : '').join('')}</div>`;
  const bars = [];
  if (spl.length) bars.push(`<div><h3>Run, walk and stand</h3>${segBar(spl.map(x => ({v:x.min, c:SK[x.k][1], share:x.min/splTot, tip:`<div class="t">${SK[x.k][0]}</div>${row('Time', hm(x.min))}${x.km?row('Distance', n1(x.km)+' km'):''}${row('Share', Math.round(x.min/splTot*100)+'%')}`})))}<div class="legend">${spl.map(x=>`<span><i style="background:${SK[x.k][1]}"></i>${SK[x.k][0]} ${hm(x.min)}</span>`).join('')}</div></div>`);
  if (zTot) bars.push(`<div><h3>Time in heart rate zones</h3>${segBar(a.z.map((v,i) => ({v, c:ZC[i], ink:ZI[i], share:v/zTot, tip:`<div class="t">Zone ${i+1}</div>${D.zones[i]?row('Heart rate', D.zones[i]+'+ bpm'):''}${row('Time', hm(v))}${row('Share', Math.round(v/zTot*100)+'%')}`})))}<div class="legend">${a.z.map((v,i)=>v?`<span><i style="background:${ZC[i]}"></i>Z${i+1} ${hm(v)}</span>`:'').join('')}</div></div>`);

  el.innerHTML = `
    <div class="acthead"><span class="sw" style="background:${sp.c};width:12px;height:12px"></span><h2>${esc(a.name || a.ty)}</h2>
      <span class="pill">${esc(a.ty)}</span><span class="pill">${a.start}–${end}</span></div>
    <div class="astats">${stats.map(x => tm(...x)).join('')}</div>
    ${bars.length ? `<div class="abars">${bars.join('')}</div>` : ''}
    <div class="acharts" id="${el.id}c"></div>`;
  el.querySelectorAll('.zbar div').forEach(d => hover(d, ev => showTip(d.dataset.tip.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&amp;/g,'&'), ev)));
  actCharts(document.getElementById(el.id+'c'), a);
}

// Synced small multiples for one activity: hover one, all follow.
function actCharts(root, a){
  const S = a.series, t = S.t;
  if (!t || !t.length){ root.innerHTML = '<div class="empty">Garmin has no detailed graphs for this activity yet. Run refresh again later.</div>'; return; }
  const spd = S.spd ? smooth(S.spd.map(v => v > (ONFOOT.has(a.s) ? 0.8 : 0.3) ? v : null), 6) : null;
  const onFoot = ONFOOT.has(a.s);
  const M = [
    {k:'hr', name:'Heart rate', unit:'bpm', c:'#e66767', v:S.hr && S.hr.map(v=>v>0?v:null), f:v=>Math.round(v)+' bpm', ax:v=>v, h:150, zones:true},
    onFoot ? {k:'pace', name:'Pace', unit:'min/km, faster is higher', c:'#3987e5', v:spd && spd.map(v=>v?1000/v/60:null), f:v=>paceStr(v)+' /km', ax:v=>paceStr(v), invert:true, clip:true}
           : {k:'speed', name:'Speed', unit:'km/h', c:'#3987e5', v:spd && spd.map(v=>v?v*3.6:null), f:v=>n1(v)+' km/h', ax:v=>v},
    {k:'cad', name:'Cadence', unit:'steps/min', c:'#199e70', v:S.cad && smooth(S.cad.map(v=>v>0?v:null), 3), f:v=>Math.round(v)+' spm', ax:v=>v},
    {k:'pw', name:'Power', unit:'W', c:'#c98500', v:S.pw && smooth(S.pw.map(v=>v>0?v:null), 3), f:v=>Math.round(v)+' W', ax:v=>v},
    {k:'el', name:'Elevation', unit:'m', c:'#008300', v:S.el, f:v=>Math.round(v)+' m', ax:v=>v},
    {k:'rr', name:'Breathing rate', unit:'breaths/min', c:'#9085e9', v:S.rr && S.rr.map(v=>v>0?v:null), f:v=>Math.round(v)+' brpm', ax:v=>v},
  ].filter(m => m.v && m.v.filter(v=>v!=null).length > 5);
  root.innerHTML = M.map((m,i) => `<div class="ac"><h3>${m.name}<span>${m.unit}</span></h3><div class="chartbox" id="${root.id}m${i}"></div></div>`).join('');
  const tMax = t[t.length-1] || 1, charts = [];
  M.forEach((mt, i) => {
    const el = document.getElementById(`${root.id}m${i}`), W = Math.max(el.clientWidth, 240), H = mt.h || 118, m = {l:44, r:mt.zones?28:10, t:8, b:22};
    let lo = mt.clip ? pct(mt.v, 0.02) : Math.min(...mt.v.filter(v=>v!=null)), hi = mt.clip ? pct(mt.v, 0.95) : Math.max(...mt.v.filter(v=>v!=null));
    const sp = (hi-lo) || 1; lo -= sp*.08; hi += sp*.08;
    const tk = nice(lo, hi, 3); lo = tk[0]; hi = tk[tk.length-1];
    const X = s => m.l + s/tMax*(W-m.l-m.r);
    const Y = v => { const f = (Math.min(hi, Math.max(lo, v))-lo)/(hi-lo); return m.t + (mt.invert ? f : 1-f)*(H-m.t-m.b); };
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
    tk.forEach(v => { s += `<line class="grid" x1="${m.l}" x2="${W-m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text class="tick" x="${m.l-6}" y="${Y(v)+4}" text-anchor="end">${mt.ax(v)}</text>`; });
    if (mt.zones) D.zones.slice(1,5).forEach((z,j) => { if (z && z > lo && z < hi) s += `<line x1="${m.l}" x2="${W-m.r}" y1="${Y(z)}" y2="${Y(z)}" stroke="#4a4945" stroke-width="1"/><text class="tick" x="${W-m.r+4}" y="${Y(z)+4}">Z${j+2}</text>`; });
    const step = tMax <= 1200 ? 300 : tMax <= 3600 ? 600 : 1200;
    for (let x = 0; x <= tMax; x += step){ const px = X(x); if (px > W-m.r-14 && x) continue; s += `<text class="tick" x="${px}" y="${H-5}" text-anchor="${x?'middle':'start'}">${Math.round(x/60)}m</text>`; }
    let d = '', pen = false;
    mt.v.forEach((v, j) => { if (v == null || t[j] == null){ pen = false; return; } d += (pen ? 'L' : 'M') + X(t[j]).toFixed(1) + ',' + Y(v).toFixed(1); pen = true; });
    s += `<path d="${d}" fill="none" stroke="${mt.c}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    s += `<line class="xh" y1="${m.t}" y2="${H-m.b}" visibility="hidden"/><circle class="hd" r="5" fill="${mt.c}" stroke="${CARD}" stroke-width="2" visibility="hidden"/></svg>`;
    el.innerHTML = s;
    const svg = el.querySelector('svg');
    charts.push({svg, W, m, X, Y, mt, xh: svg.querySelector('.xh'), hd: svg.querySelector('.hd')});
  });
  const at = j => charts.forEach(c => {
    c.xh.setAttribute('x1', c.X(t[j])); c.xh.setAttribute('x2', c.X(t[j])); c.xh.setAttribute('visibility','visible');
    const v = c.mt.v[j]; if (v != null){ c.hd.setAttribute('cx', c.X(t[j])); c.hd.setAttribute('cy', c.Y(v)); c.hd.setAttribute('visibility','visible'); } else c.hd.setAttribute('visibility','hidden');
  });
  const clear = () => charts.forEach(c => { c.xh.setAttribute('visibility','hidden'); c.hd.setAttribute('visibility','hidden'); });
  charts.forEach(c => hover(c.svg, ev => {
    const sec = (svgX(c.svg, ev, c.W) - c.m.l)/(c.W - c.m.l - c.m.r)*tMax;
    let j = 0; for (let k = 0; k < t.length; k++) if (Math.abs(t[k]-sec) < Math.abs(t[j]-sec)) j = k;
    at(j);
    showTip(`<div class="t">${mmss(t[j])} in${S.dist && S.dist[j] ? ` · ${n1(S.dist[j]/1000)} km`:''}</div>` + charts.map(c2 => c2.mt.v[j] != null ? row(c2.mt.name, c2.mt.f(c2.mt.v[j]), c2.mt.c) : '').join(''), ev);
  }, clear));
}

// ---------- page ----------
function renderAll(){
  renderReady(); renderToday(); renderHero(); renderTiles(); renderWeeks(); renderIM(); renderHeat(); renderTrends(); renderZones(); renderRecords();
}
if (D.name){ document.getElementById('who').textContent = D.name + "'s training log"; document.title = D.name + "'s training"; }
document.getElementById('meta').innerHTML = `Updated ${esc(D.built)}<br>${D.acts.length} activities · ${D.days.length} days · ${D.sleep.length} nights`;
document.getElementById('foot').innerHTML = `Data from Garmin Connect, read-only, stored on this computer only.${D.fitAge?` · Garmin fitness age ${n1(D.fitAge)}${D.age?` (actual ${D.age})`:''}`:''}${D.weight.length?` · Last weigh-in ${n1(D.weight[D.weight.length-1].kg)} kg`:''}`;
function seg(id, attr, onPick){
  const g = document.getElementById(id);
  g.addEventListener('click', e => { const b = e.target.closest('button'); if(!b) return;
    g.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x===b)); onPick(b.dataset[attr]); });
}
seg('wkMetric', 'm', v => { wkMetric = v; renderWeeks(); });
seg('range', 'r', v => { rangeM = +v; renderTrends(); });
renderAll();

// Refresh button: only when served by server.py (not when opened as a file).
(function(){
  if (!location.protocol.startsWith('http')) return;
  document.getElementById('settingsLink').hidden = false;
  const b = document.getElementById('refreshBtn'); b.hidden = false;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  async function poll(before){
    for (let i = 0; i < 400; i++){
      await wait(3000);
      const s = await fetch('status', {cache:'no-store'}).then(r => r.json()).catch(() => null);
      if (!s) continue;
      if (!s.running){
        if (s.lastError){ b.textContent = 'Refresh failed'; b.title = s.lastError; b.disabled = false; return; }
        if (s.lastOk !== before) location.reload();
        else { b.textContent = 'Refresh'; b.disabled = false; }
        return;
      }
    }
  }
  b.addEventListener('click', async () => {
    b.disabled = true; b.textContent = 'Refreshing…'; b.title = '';
    const before = await fetch('status', {cache:'no-store'}).then(r => r.json()).then(s => s.lastOk).catch(() => null);
    await fetch('refresh', {method:'POST'}).catch(() => null);
    poll(before);
  });
  fetch('status', {cache:'no-store'}).then(r => r.json()).then(s => { if (s.running){ b.disabled = true; b.textContent = 'Refreshing…'; poll(s.lastOk); } }).catch(() => {});
})();
let rz, lastW = innerWidth; addEventListener('resize', () => { if (innerWidth === lastW) return; lastW = innerWidth; clearTimeout(rz); rz = setTimeout(renderAll, 120); });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
