"""Shared test helpers: temporary data folders, fake HTTP responses and a fake
Garmin API built from made-up data (no real personal data is used anywhere)."""
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import build
import garmin_api
import pull
import settings

TODAY = date.today()
ISO = "%Y-%m-%d"


def ds(d):
    return d.strftime(ISO)


class FakeResponse:
    def __init__(self, body=b""):
        self.body = body if isinstance(body, bytes) else body.encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code):
    return urllib.error.HTTPError("https://example.test", code, "error", {}, io.BytesIO(b""))


class TempDataCase(unittest.TestCase):
    """Every module's data folder points at a fresh temporary folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.out = self.root / "dashboard.html"
        for target, attr, value in (
            (garmin_api, "DATA", self.data),
            (garmin_api, "TOKEN_FILE", self.data / "token.json"),
            (garmin_api, "ENV_FILE", self.root / ".env.local"),
            (pull, "DATA", self.data),
            (build, "DATA", self.data),
            (build, "OUT", self.out),
            (settings, "DATA", self.data),
        ):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("time.sleep")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, obj):
        (self.data / name).write_text(json.dumps(obj), encoding="utf-8")

    def read(self, name):
        return json.loads((self.data / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------- made-up data

SPORT_TYPES = ["walking", "running", "treadmill_running", "indoor_cycling", "lap_swimming",
               "hiking", "strength_training", "yoga", "golf"]


def make_activity(aid, when, type_key, km=3.0, minutes=30.0, **extra):
    a = {
        "activityId": aid, "activityName": f"Test {type_key} {aid}",
        "startTimeLocal": when.strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": type_key},
        "distance": km * 1000, "duration": minutes * 60, "movingDuration": minutes * 60 - 60,
        "elevationGain": 12.0, "averageHR": 130.0, "maxHR": 160.0, "calories": 250.0,
        "activityTrainingLoad": 45.5, "aerobicTrainingEffect": 2.8, "anaerobicTrainingEffect": 0.4,
        "steps": 3500, "averageRunningCadenceInStepsPerMinute": 150.0, "averageSpeed": 1.8,
        "avgRespirationRate": 30.0,
        **{f"hrTimeInZone_{i}": 60.0 * i for i in range(1, 6)},
    }
    a.update(extra)
    return a


def make_activities(today=TODAY, n_days=70):
    """Newest first, like the Garmin activity list. One today, gaps, all sports."""
    acts = []
    aid = 9000
    for back in range(n_days):
        day = datetime.combine(today - timedelta(days=back), datetime.min.time())
        if back % 4 == 3:
            continue
        type_key = SPORT_TYPES[back % len(SPORT_TYPES)]
        acts.append(make_activity(aid, day.replace(hour=7, minute=30), type_key,
                                  km=0 if type_key in ("strength_training", "yoga") else 2 + back % 7,
                                  minutes=20 + back % 40))
        aid -= 1
    # a second activity today, so the Today panel shows two cards
    acts.insert(0, make_activity(9500, datetime.combine(today, datetime.min.time()).replace(hour=18), "running", km=5.2))
    return acts


def make_daily(day, steps=8000, empty=False):
    if empty:
        return {"calendarDate": ds(day), "movement": {"steps": {}}, "heartRate": {}, "intensityMinutes": {}}
    return {
        "calendarDate": ds(day),
        "movement": {"steps": {"value": steps, "goal": 6000, "distanceInMeters": steps * 0.75},
                     "floorsAscended": {"value": 6, "goal": 10}},
        "calories": {"burnedActive": 500, "burnedTotal": 2300},
        "heartRate": {"restingValue": 60 + day.day % 7, "minValue": 52, "maxValue": 150},
        "intensityMinutes": {"goal": 150, "moderate": 20, "vigorous": 10},
        "stress": {"avgLevel": 30, "maxLevel": 85, "restProportion": 0.2, "lowStressProportion": 0.4,
                   "mediumStressProportion": 0.2, "highStressProportion": 0.1},
        "bodyBattery": {"maxValue": 80, "minValue": 20, "latestValue": 55, "chargedValue": 40, "drainedValue": 35},
        "respiration": {"avgValue": 15},
        "hydration": {"consumedInMl": 1200, "goalInMl": 3000},
        "wellnessChronology": {"endTimestampLocal": f"{ds(day)}T18:45:00.0"},
    }


def make_sleep(day, hours=7.0):
    return {"calendarDate": ds(day), "sleepTimeSeconds": int(hours * 3600),
            "deepSleepSeconds": 3600, "lightSleepSeconds": 12000, "remSleepSeconds": 5400,
            "awakeSleepSeconds": 900, "sleepScores": {"overall": {"value": 78, "qualifierKey": "GOOD"}}}


def make_hrv(day, value=45, status="BALANCED"):
    return {"calendarDate": ds(day), "lastNightAvg": value, "weeklyAvg": value - 1, "status": status}


def make_details(n=60, with_elevation=False):
    keys = ["sumElapsedDuration", "sumDistance", "directHeartRate", "directSpeed", "directDoubleCadence",
            "directRunCadence", "directPower", "directRespirationRate", "directTimestamp"]
    if with_elevation:
        keys.append("directElevation")
    rows = []
    for i in range(n):
        speed = 0.2 if i % 15 == 0 else (2.8 if (i // 10) % 2 else 1.4)
        row = [i * 10.0, i * 20.0, 120 + i % 30, speed, 160 if speed > 2 else 105, 80, 200 if speed > 2 else 100,
               30 + i % 6, 1.7e12 + i * 1e4]
        if with_elevation:
            row.append(500 + i % 9)
        rows.append({"metrics": row})
    return {
        "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
        "activityDetailMetrics": rows,
    }


def make_summary():
    return {"summaryDTO": {"movingDuration": 1500, "minHR": 100, "steps": 3000, "averageRunCadence": 150,
                           "averagePower": 140, "avgRespirationRate": 32, "averageMovingSpeed": 2.0,
                           "trainingEffectLabel": "AEROBIC_BASE"},
            "splitSummaries": [
                {"splitType": "RWD_RUN", "duration": 600, "distance": 1700},
                {"splitType": "RWD_WALK", "duration": 900, "distance": 1200},
                {"splitType": "RWD_STAND", "duration": 60, "distance": 0},
                {"splitType": "INTERVAL_ACTIVE", "duration": 100, "distance": 50},
            ]}


class FakeGarmin:
    """Stands in for garmin_api.get(): answers each endpoint from made-up data."""

    def __init__(self, today=TODAY, activities=None, fail=None):
        self.today = today
        self.activities = make_activities(today) if activities is None else activities
        self.fail = fail or {}      # path prefix -> exception to raise
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        for prefix, exc in self.fail.items():
            if path.startswith(prefix):
                raise exc
        if path.startswith("/activitylist-service"):
            q = dict(kv.split("=") for kv in path.split("?", 1)[1].split("&"))
            start, limit = int(q["start"]), int(q["limit"])
            return self.activities[start:start + limit]
        if path.startswith("/usersummary-service"):
            s, e = self._range(path)
            return {"data": [make_daily(d, empty=(d.day % 11 == 0)) for d in self._days(s, e)]}
        if path.startswith("/sleep-service"):
            s, e = self._range(path)
            return [make_sleep(d) if d.day % 4 else {"calendarDate": ds(d), "sleepTimeSeconds": 0}
                    for d in self._days(s, e)]
        if path.startswith("/hrv-service"):
            parts = path.split("/")
            s, e = datetime.strptime(parts[-2], ISO).date(), datetime.strptime(parts[-1], ISO).date()
            return {"hrvSummaries": [make_hrv(d) if d.day % 5 else {"calendarDate": ds(d)} for d in self._days(s, e)]}
        if path.startswith("/weight-service"):
            return {"dailyWeightSummaries": [{"summaryDate": ds(self.today), "latestWeight": {"weight": 72500}}]}
        if path.startswith("/fitnessage-service"):
            return {"fitnessAge": 30.4, "chronologicalAge": 33}
        if path.startswith("/biometric-service"):
            return [{"zone1Floor": 95, "zone2Floor": 114, "zone3Floor": 133, "zone4Floor": 152,
                     "zone5Floor": 171, "maxHeartRateUsed": 190}]
        if path.startswith("/wellness-service/wellness/dailyHeartRate"):
            midnight = datetime.combine(self.today, datetime.min.time())
            gmt = midnight - timedelta(hours=5, minutes=30)
            base = int(gmt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            return {"startTimestampLocal": midnight.isoformat() + ".0", "startTimestampGMT": gmt.isoformat() + ".0",
                    "restingHeartRate": 61, "lastSevenDaysAvgRestingHeartRate": 62,
                    "heartRateValues": [[base + i * 120000, 70 + i % 40 if i % 50 else None] for i in range(400)]}
        if path.startswith("/wellness-service/wellness/bodyBattery"):
            midnight = datetime.combine(self.today, datetime.min.time()) - timedelta(hours=5, minutes=30)
            base = int(midnight.replace(tzinfo=timezone.utc).timestamp() * 1000)
            return [{"charged": 40, "drained": 30,
                     "bodyBatteryValuesArray": [[base + i * 3600000, 20 + i * 3] for i in range(12)] + [[base, None]]}]
        if "/details?" in path:
            return make_details()
        if path.startswith("/activity-service/activity/"):
            return make_summary()
        raise AssertionError(f"FakeGarmin has no answer for {path}")

    @staticmethod
    def _range(path):
        q = dict(kv.split("=") for kv in path.split("?", 1)[1].split("&"))
        return (datetime.strptime(q["startDate"], ISO).date(), datetime.strptime(q["endDate"], ISO).date())

    @staticmethod
    def _days(s, e):
        out, d = [], s
        while d <= e:
            out.append(d)
            d += timedelta(days=1)
        return out


def write_dataset(case, today=TODAY, with_today=True, activities=None):
    """Fill case.data with a full made-up dataset by running the real pull.py
    against FakeGarmin."""
    fake = FakeGarmin(today, activities=activities)
    with mock.patch.object(pull, "get", fake), mock.patch("sys.argv", ["pull.py"]), \
            mock.patch("builtins.print"), mock.patch("sys.stdout"):
        pull.main()
    if not with_today:
        (case.data / "today.json").unlink()
    return fake
