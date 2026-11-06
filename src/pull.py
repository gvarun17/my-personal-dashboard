"""Download your Garmin history into data/ as JSON.

    python3 src/pull.py          # last 12 months (first run), then only what's new
    python3 src/pull.py --full   # your whole history

Re-running only fetches what is new (plus the last few days, which Garmin
may still be filling in).
"""
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone

from garmin_api import DATA, GarminError, HTTPStatus, get

PAUSE = 0.3          # seconds between calls
REFETCH_DAYS = 3     # always refresh the last few days
MAX_SKIPS = 5        # give up on the activity list after this many failures in a row
ISO = "%Y-%m-%d"


def d2s(d):
    return d.strftime(ISO)


def s2d(s):
    return datetime.strptime(s[:10], ISO).date()


def load(name, default):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return default


def save(name, obj):
    p = DATA / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def bar(label, done, total, extra=""):
    total = max(total, 1)
    pct = min(done / total, 1)
    filled = int(pct * 24)
    sys.stdout.write(f"\r  {label:<16} [{'#' * filled}{'.' * (24 - filled)}] {pct:4.0%} {extra:<28}")
    sys.stdout.flush()


# ---------------------------------------------------------------- activities

def pull_activities(start, backfill):
    """Page newest-first. Stop at `start`, or (when not backfilling) at the
    first activity we already have."""
    acts = load("activities.json", {})
    known = set(acts)
    new, page, skipped = 0, 0, 0
    total_days = max((date.today() - start).days, 1)
    while True:
        batch = None
        for limit in (20, 10, 5, 1):          # shrink the page if a response is too big
            try:
                batch = get(f"/activitylist-service/activities/search/activities?start={page}&limit={limit}") or []
                break
            except HTTPStatus as e:
                if e.code != 500:
                    raise
        if batch is None:
            skipped += 1
            if skipped >= MAX_SKIPS:      # Garmin keeps failing: stop instead of looping forever
                print(f"\n  ! Garmin could not return activities from position {page}; stopping here")
                break
            print(f"\n  ! skipped an activity Garmin could not return (position {page})")
            page += 1
            continue
        skipped = 0
        time.sleep(PAUSE)
        stop = False
        for a in batch:
            aid = str(a.get("activityId"))
            when = a.get("startTimeLocal") or ""
            if when and s2d(when) < start:
                stop = True
                break
            if aid in known and not backfill:
                stop = True
                break
            if aid not in acts:
                new += 1
            acts[aid] = a
        page += len(batch)
        oldest = (batch[-1].get("startTimeLocal") or "")[:10] if batch else d2s(start)
        bar("Activities", (date.today() - s2d(oldest)).days if oldest else total_days, total_days,
            f"{len(acts)} total, back to {oldest}")
        if stop or len(batch) < limit:
            break
    bar("Activities", 1, 1, f"{len(acts)} total, {new} new")
    print()
    save("activities.json", acts)
    return acts


# ----------------------------------------------------------- date-range data

def fetch_ranges(label, ranges, chunk_days, path_fn, items_fn, key_fn, store):
    """Fetch [start, end] ranges in chunks; halve a chunk when Garmin says 500
    (the ~256 KB response limit). Merges items into `store` by date key."""
    total = sum((e - s).days + 1 for s, e in ranges) or 1
    done = 0
    for s, e in ranges:
        cur, size = s, chunk_days
        while cur <= e:
            end = min(cur + timedelta(days=size - 1), e)
            try:
                resp = get(path_fn(cur, end))
            except HTTPStatus as err:
                if err.code == 500 and size > 1:
                    size = max(size // 2, 1)
                    continue
                if err.code in (500, 404, 204):
                    resp = None      # a day Garmin can't return: skip it
                else:
                    raise
            time.sleep(PAUSE)
            for item in items_fn(resp) or []:
                k = key_fn(item)
                if k:
                    store[k[:10]] = item
            done += (end - cur).days + 1
            bar(label, done, total, f"through {d2s(end)}")
            cur = end + timedelta(days=1)
            size = chunk_days
    bar(label, 1, 1, f"{len(store)} days with data")
    print()
    return store


def ranges_for(name, start, today, state):
    """Which date ranges still need fetching for this dataset."""
    prev = state.get(name)
    if not prev:
        return [(start, today)]
    out = []
    p_start, p_through = s2d(prev["start"]), s2d(prev["through"])
    if start < p_start:
        out.append((start, p_start - timedelta(days=1)))
    out.append((max(p_through - timedelta(days=REFETCH_DAYS), p_start), today))
    return out


DATASETS = [
    # name, label, chunk days, path, items in response, date key
    ("daily", "Daily summaries", 60,
     lambda s, e: f"/usersummary-service/v2/dailysummaries?startDate={d2s(s)}&endDate={d2s(e)}",
     lambda r: (r or {}).get("data"), lambda i: i.get("calendarDate")),
    ("sleep", "Sleep", 31,
     lambda s, e: f"/sleep-service/sleep/dailySleepSummariesByDate?startDate={d2s(s)}&endDate={d2s(e)}",
     lambda r: r if isinstance(r, list) else [], lambda i: i.get("calendarDate") if i.get("sleepTimeSeconds") else None),
    ("hrv", "HRV", 365,
     lambda s, e: f"/hrv-service/hrv/daily/{d2s(s)}/{d2s(e)}",
     lambda r: (r or {}).get("hrvSummaries"), lambda i: i.get("calendarDate") if i.get("lastNightAvg") else None),
    ("weight", "Weight", 365,
     lambda s, e: f"/weight-service/weight/range/{d2s(s)}/{d2s(e)}",
     lambda r: (r or {}).get("dailyWeightSummaries"), lambda i: i.get("summaryDate")),
]


# --------------------------------------------------------------- today

DETAIL_KEYS = {          # Garmin metric key -> short name we keep
    "sumElapsedDuration": "t", "sumDistance": "dist", "directHeartRate": "hr",
    "directSpeed": "spd", "directDoubleCadence": "cad", "directRunCadence": "cad1",
    "directPower": "pw", "directElevation": "el", "directRespirationRate": "rr",
}


def activity_detail(aid):
    """Second-by-second graphs for one activity. Cached in data/details/."""
    folder = DATA / "details"
    folder.mkdir(exist_ok=True)
    cache = folder / f"{aid}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raw = None
    for size in (1000, 500, 250, 120):           # stay under the ~256 KB response limit
        try:
            raw = get(f"/activity-service/activity/{aid}/details?maxChartSize={size}&maxPolylineSize=0")
            break
        except HTTPStatus as e:
            if e.code != 500:
                raise
    time.sleep(PAUSE)
    summary = {}
    try:
        full = get(f"/activity-service/activity/{aid}") or {}
        summary = {"summary": full.get("summaryDTO") or {}, "splits": full.get("splitSummaries") or []}
    except HTTPStatus:
        pass
    time.sleep(PAUSE)
    series = {}
    if raw and raw.get("activityDetailMetrics"):
        idx = {m["key"]: m["metricsIndex"] for m in raw.get("metricDescriptors", [])}
        rows = [r.get("metrics") or [] for r in raw["activityDetailMetrics"]]
        for key, short in DETAIL_KEYS.items():
            if key in idx:
                i = idx[key]
                vals = [(row[i] if i < len(row) else None) for row in rows]
                if any(v for v in vals if v is not None):
                    series[short] = [None if v is None else round(v, 2) for v in vals]
    out = {"series": series, **summary}
    if series:                                    # only cache once Garmin has processed it
        cache.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


KEEP_DAYS = 7        # graphs through the day are kept for today and the 6 days before


def fetch_day(day, acts, label):
    """Heart rate and Body Battery through one day, plus full detail for each
    of that day's activities. ok is False when Garmin couldn't be asked."""
    todays = sorted((a for a in acts.values() if (a.get("startTimeLocal") or "")[:10] == day),
                    key=lambda a: a["startTimeLocal"])
    steps = 2 + len(todays)
    out = {"date": day, "hr": [], "bb": [], "acts": {}}
    ok = True
    bar(label, 0, steps)
    try:
        hr = get(f"/wellness-service/wellness/dailyHeartRate?date={day}") or {}
        lt, gt = hr.get("startTimestampLocal"), hr.get("startTimestampGMT")
        off = 0
        if lt and gt:
            off = (datetime.fromisoformat(lt[:19]) - datetime.fromisoformat(gt[:19])).total_seconds() * 1000
        start_local = datetime.fromisoformat(lt[:19]) if lt else None

        def minute(ts):  # GMT epoch ms -> minutes after local midnight
            local = datetime.fromtimestamp((ts + off) / 1000, timezone.utc).replace(tzinfo=None)
            return round((local - start_local).total_seconds() / 60, 1) if start_local else None
        out["off"] = off
        out["hr"] = [[m, v] for m, v in ((minute(t), v) for t, v in (hr.get("heartRateValues") or []) if v) if m is not None]
        out["rhr"] = hr.get("restingHeartRate")
        out["rhr7"] = hr.get("lastSevenDaysAvgRestingHeartRate")
        time.sleep(PAUSE)
        bar(label, 1, steps)
        bb = get(f"/wellness-service/wellness/bodyBattery/reports/daily?startDate={day}&endDate={day}") or []
        if bb:
            out["bb"] = [[m, v] for m, v in ((minute(t), v) for t, v in (bb[0].get("bodyBatteryValuesArray") or []) if v is not None) if m is not None]
            out["bbCharged"], out["bbDrained"] = bb[0].get("charged"), bb[0].get("drained")
        time.sleep(PAUSE)
    except HTTPStatus:
        ok = False
    bar(label, 2, steps)
    for n, a in enumerate(todays, 1):
        try:
            out["acts"][str(a["activityId"])] = activity_detail(a["activityId"])
        except HTTPStatus as e:
            ok = False
            print(f"\n  ! could not get details for {a.get('activityName')} ({e})")
        bar(label, 2 + n, steps)
    bar(label, 1, 1, f"{len(todays)} activit{'y' if len(todays) == 1 else 'ies'}" + (" today" if label == "Today" else ""))
    print()
    return out, ok


def pull_today(today, acts):
    out, _ = fetch_day(d2s(today), acts, "Today")
    save("today.json", out)


def pull_recent(today, acts):
    """The days before today, for the date switcher: each fetched once, after
    the day is over, and saved in data/days/. Older ones are removed."""
    folder = DATA / "days"
    folder.mkdir(exist_ok=True)
    keep = {d2s(today - timedelta(days=back)) for back in range(1, KEEP_DAYS)}
    for old in folder.glob("*.json"):
        if old.stem not in keep:
            old.unlink()
    for day in sorted(keep, reverse=True):
        target = folder / f"{day}.json"
        if target.exists():
            continue
        out, ok = fetch_day(day, acts, day)
        if ok:                                  # try again next time if Garmin didn't answer
            target.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")


def main():
    full = "--full" in sys.argv
    DATA.mkdir(parents=True, exist_ok=True)
    state = load("state.json", {})
    today = date.today()

    first_run = not state
    if full:
        start = None                       # decided after activities
    elif state.get("start"):
        start = s2d(state["start"])
    else:
        start = today - timedelta(days=365)

    print("Pulling your Garmin data (read-only)...\n")

    # Activities. For --full, go all the way back.
    act_start = date(1990, 1, 1) if full else start
    backfill = first_run or full or (start < s2d(state.get("start", d2s(start))))
    acts = pull_activities(act_start, backfill)

    if full:
        dates = [s2d(a["startTimeLocal"]) for a in acts.values() if a.get("startTimeLocal")]
        start = min(dates) if dates else today - timedelta(days=5 * 365)
        if state.get("start"):
            start = min(start, s2d(state["start"]))

    for name, label, chunk, path_fn, items_fn, key_fn in DATASETS:
        store = load(f"{name}.json", {})
        fetch_ranges(label, ranges_for(name, start, today, state), chunk, path_fn, items_fn, key_fn, store)
        save(f"{name}.json", store)
        prev_start = s2d(state[name]["start"]) if state.get(name) else start
        state[name] = {"start": d2s(min(start, prev_start)), "through": d2s(today)}
        state["start"] = d2s(min(start, s2d(state.get("start", d2s(start)))))
        save("state.json", state)

    pull_today(today, acts)
    pull_recent(today, acts)

    # Small one-off lookups
    for fname, path in (("fitness_age.json", f"/fitnessage-service/fitnessage/v2/{d2s(today)}"),
                        ("hr_zones.json", "/biometric-service/heartRateZones/v2")):
        try:
            save(fname, get(path))
        except GarminError:
            pass
        time.sleep(PAUSE)
    state["last_pull"] = datetime.now().isoformat(timespec="seconds")
    save("state.json", state)

    # Summary
    daily, sleep, hrv, weight = (load(f"{n}.json", {}) for n in ("daily", "sleep", "hrv", "weight"))
    act_dates = sorted(a["startTimeLocal"][:10] for a in acts.values() if a.get("startTimeLocal"))
    print("\nDone. What came back:")
    if act_dates:
        print(f"  Activities:      {len(acts):>5}   ({act_dates[0]} to {act_dates[-1]})")
    else:
        print("  Activities:          0")
    for label, store in (("Days of summaries", daily), ("Nights of sleep", sleep),
                         ("HRV readings", hrv), ("Weigh-ins", weight)):
        ks = sorted(store)
        rng = f"({ks[0]} to {ks[-1]})" if ks else ""
        print(f"  {label + ':':<17}{len(store):>5}   {rng}")


if __name__ == "__main__":
    try:
        main()
    except GarminError as e:
        print(f"\n\nStopped: {e}")
        print("Run it again any time; it picks up where it left off.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nStopped. Run it again any time; it picks up where it left off.")
        sys.exit(1)
