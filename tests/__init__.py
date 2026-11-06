"""Test setup shared by every test module.

Runs before any project module is imported, so tests never see your real
key, settings or data, and can never reach the real Garmin API.
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")          # the app's code lives in src/
if SRC not in sys.path:
    sys.path.insert(0, SRC)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)                 # so "tests.helpers" imports work

# Pin every setting the app reads, so a local .env.local can't leak in
# (server.py only fills in settings that are not already set).
for _k, _v in {
    "GARMIN_PERSONAL_API_KEY": "",
    "DASHBOARD_PASSWORD": "",
    "DASHBOARD_USER": "",
    "REFRESH_MINUTES": "60",
    "PORT": "8080",
    "HOST": "0.0.0.0",
    "GARMIN_DATA_DIR": "",
    "DASHBOARD_OUT": "",
    "HOST_PORT": "8080",
    "TZ": os.environ.get("TZ", "UTC"),
}.items():
    os.environ[_k] = _v


def _network_disabled(*args, **kwargs):
    raise RuntimeError("Network access is disabled in tests; patch urllib.request.urlopen")


urllib.request.urlopen = _network_disabled
