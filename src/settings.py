"""Your settings from the start page: name, time zone and Garmin key.

Saved in the data folder as settings.json (git-ignored, and inside the Docker
volume when run in a container). The file is readable only by your user
account. The key is never sent back to the browser or printed.
"""
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # project folder
DATA = Path(os.environ.get("GARMIN_DATA_DIR") or ROOT / "data")
NAME_MAX = 60
_TZ_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")


def path():
    return DATA / "settings.json"


def load():
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def save(**changes):
    """Merge changes (None means keep) into settings.json, written privately."""
    d = load()
    d.update({k: v for k, v in changes.items() if v is not None})
    d["updated"] = int(time.time())
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, p)
    return d


def public():
    """Settings safe to show in a page: everything except the key."""
    return {k: v for k, v in load().items() if k != "key"}


def time_zones():
    try:
        import zoneinfo
        return sorted(zoneinfo.available_timezones())
    except Exception:   # very old or stripped-down Python
        return []


# Older names that browsers still report, and their current names.
TZ_ALIASES = {
    "Asia/Calcutta": "Asia/Kolkata", "Asia/Saigon": "Asia/Ho_Chi_Minh", "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon", "Asia/Dacca": "Asia/Dhaka", "Europe/Kiev": "Europe/Kyiv",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires", "America/Indianapolis": "America/Indiana/Indianapolis",
    "US/Eastern": "America/New_York", "US/Central": "America/Chicago", "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles", "US/Alaska": "America/Anchorage", "US/Hawaii": "Pacific/Honolulu",
    "Australia/ACT": "Australia/Sydney", "Australia/NSW": "Australia/Sydney", "GMT": "Etc/GMT",
}


def normalize_tz(tz):
    """Current name for a time zone, when the given (older) name isn't known here."""
    tz = (tz or "").strip()
    zones = time_zones()
    if tz in TZ_ALIASES and zones and tz not in zones:
        return TZ_ALIASES[tz]
    return tz


def valid_tz(tz):
    if not tz or not _TZ_SHAPE.match(tz):
        return False
    zones = time_zones()
    return tz in zones if zones else True   # no time zone database here: accept well-formed names


def clean_name(name):
    return " ".join((name or "").split())[:NAME_MAX]


def clean_key(key):
    return "".join((key or "").split())
