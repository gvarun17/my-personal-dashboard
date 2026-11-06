"""Tiny read-only client for Garmin's Personal API (standard library only).

The API key comes from, in order: the start page (saved in
data/settings.json), the GARMIN_PERSONAL_API_KEY environment variable, or
.env.local. It is never printed or logged. Downloaded data goes to
GARMIN_DATA_DIR (default: ./data).
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import settings

ROOT = Path(__file__).resolve().parent.parent   # project folder (holds .env.local and data/)
DATA = Path(os.environ.get("GARMIN_DATA_DIR") or ROOT / "data")
ENV_FILE = ROOT / ".env.local"
TOKEN_FILE = DATA / "token.json"
BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = BASE + "/personalapitoken-service/personal-api/personal-api-key/token"
TOKEN_MAX_AGE = 20 * 3600  # tokens last 24h; renew after 20h
UA = "garmin-dashboard/1.0"


class GarminError(Exception):
    pass


def find_key():
    """(key, where it came from), or (None, None) when no key is set up yet."""
    key = (settings.load().get("key") or "").strip()
    if key:
        return key, "start page"
    key = (os.environ.get("GARMIN_PERSONAL_API_KEY") or "").strip()
    if key:
        return key, "environment"
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("GARMIN_PERSONAL_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key, ".env.local"
    return None, None


def _read_key():
    key, _ = find_key()
    if not key:
        raise GarminError("No Garmin key is set up yet. Open the web app's start page, "
                          "or put GARMIN_PERSONAL_API_KEY in .env.local.")
    return key


def check_key(key):
    """Try a key by swapping it for a token. Raises GarminError if Garmin refuses it."""
    return _new_token(key)


def _new_token(key=None):
    key = key or _read_key()
    req = urllib.request.Request(
        TOKEN_URL, data=b"", method="POST",
        headers={"Authorization": f"ApiKey {key}", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            token = r.read().decode().strip().strip('"')
    except urllib.error.HTTPError as e:
        e.close()
        raise GarminError(
            f"Garmin refused the key (HTTP {e.code}). It may be mistyped or expired: "
            "check it, or create a new one in Garmin Connect."
        ) from None
    except urllib.error.URLError as e:
        raise GarminError(f"Could not reach Garmin: {e.reason}") from None
    if not token:
        raise GarminError("Garmin returned an empty token.")
    DATA.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"token": token, "fetched_at": time.time()}))
    os.chmod(TOKEN_FILE, 0o600)
    return token


def get_token(force=False):
    if not force and TOKEN_FILE.exists():
        try:
            saved = json.loads(TOKEN_FILE.read_text())
            if time.time() - saved["fetched_at"] < TOKEN_MAX_AGE and saved["token"]:
                return saved["token"]
        except (ValueError, KeyError):
            pass
    return _new_token()


class HTTPStatus(GarminError):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def get(path, retries=4):
    """GET a path and return parsed JSON (or None for an empty body)."""
    token = get_token()
    renewed = False
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            BASE + path,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "User-Agent": UA},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 401 and not renewed:
                token, renewed = get_token(force=True), True
                continue
            if e.code in (429, 502, 503) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise HTTPStatus(e.code) from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise GarminError(f"Could not reach Garmin: {e.reason}") from None
    raise GarminError("Gave up after retries.")
