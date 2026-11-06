"""Serve the dashboard as a small web app (standard library only).

    python3 server.py

- GET  /           the dashboard (rebuilt after every refresh)
- POST /refresh    pull new Garmin data and rebuild, in the background
- GET  /status     JSON: is a refresh running, when did the last one finish
- GET  /healthz    "ok", for container health checks (no login needed)

Settings come from environment variables (or .env.local when run directly):
  PORT                 port to listen on (default 8080)
  HOST                 address to listen on (default 0.0.0.0)
  REFRESH_MINUTES      how often to pull new data automatically (default 60, 0 = never)
  DASHBOARD_PASSWORD   if set, the browser asks for a login (any username works,
                       unless DASHBOARD_USER is also set)
  DASHBOARD_USER       optional username for the login
  GARMIN_DATA_DIR      where data and the built dashboard live (default ./data)
"""
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env_file():
    """Read .env.local into the environment for local runs (Docker passes env directly)."""
    f = ROOT / ".env.local"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()

PORT = int(os.environ.get("PORT") or 8080)
HOST = os.environ.get("HOST") or "0.0.0.0"
REFRESH_MINUTES = float(os.environ.get("REFRESH_MINUTES") or 60)
PASSWORD = os.environ.get("DASHBOARD_PASSWORD") or ""
USER = os.environ.get("DASHBOARD_USER") or ""
DATA = Path(os.environ.get("GARMIN_DATA_DIR") or ROOT / "data")
OUT = DATA / "dashboard.html"

# Child processes write the dashboard into the data folder.
CHILD_ENV = {**os.environ, "GARMIN_DATA_DIR": str(DATA), "DASHBOARD_OUT": str(OUT)}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


class Refresher:
    """Runs pull.py then build.py, one at a time, in a background thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.last_ok = None
        self.last_error = None
        self.last_run = None

    def status(self):
        return {"running": self.running, "lastOk": self.last_ok, "lastRun": self.last_run,
                "lastError": self.last_error, "hasDashboard": OUT.exists(),
                "autoRefreshMinutes": REFRESH_MINUTES}

    def start(self, full=False):
        with self.lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._run, args=(full,), daemon=True).start()
        return True

    def _run(self, full):
        self.last_run = datetime.now().isoformat(timespec="seconds")
        log("Refreshing Garmin data" + (" (full history)" if full else ""))
        try:
            for cmd in ([sys.executable, "pull.py"] + (["--full"] if full else []),
                        [sys.executable, "build.py"]):
                r = subprocess.run(cmd, cwd=ROOT, env=CHILD_ENV, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=1800)
                if r.returncode != 0:
                    lines = (r.stdout + r.stderr).replace("\r", "\n").strip().splitlines()
                    raise RuntimeError(lines[-1] if lines else f"{cmd[1]} failed")
            self.last_ok = datetime.now().isoformat(timespec="seconds")
            self.last_error = None
            log("Refresh finished")
        except Exception as e:  # report, keep serving the last good dashboard
            self.last_error = str(e)[:300]
            log(f"Refresh failed: {self.last_error}")
        finally:
            self.running = False


refresher = Refresher()


def scheduler():
    if not OUT.exists() or REFRESH_MINUTES > 0:
        refresher.start()
    if REFRESH_MINUTES <= 0:
        return
    while True:
        time.sleep(REFRESH_MINUTES * 60)
        refresher.start()


WAITING_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>My Training</title>
<meta http-equiv="refresh" content="5"><style>body{margin:0;background:#0d0d0d;color:#f0efec;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;display:grid;place-items:center;min-height:100vh}
div{max-width:420px;padding:24px;text-align:center}p{color:#c3c2b7}</style></head><body><div>
<h1>Getting your Garmin data…</h1><p>The first download takes a minute or two.
This page reloads by itself.</p><p style="color:#e66767">%s</p></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "garmin-dashboard"

    def log_message(self, fmt, *args):  # quieter logs, no query strings
        pass

    def _authed(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization") or ""
        if h.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
            except ValueError:
                return False
            ok_pw = hmac.compare_digest(pw.encode(), PASSWORD.encode())
            ok_user = not USER or hmac.compare_digest(user.encode(), USER.encode())
            return ok_pw and ok_user
        return False

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _deny(self):
        self._send(401, "Login required", "text/plain; charset=utf-8",
                   {"WWW-Authenticate": 'Basic realm="My Training", charset="UTF-8"'})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, "ok", "text/plain; charset=utf-8")
        if not self._authed():
            return self._deny()
        if path in ("/", "/index.html", "/dashboard.html"):
            if OUT.exists():
                return self._send(200, OUT.read_bytes())
            err = refresher.last_error or ""
            return self._send(200, WAITING_PAGE % err.replace("<", "&lt;"))
        if path == "/status":
            return self._send(200, json.dumps(refresher.status()), "application/json")
        self._send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._authed():
            return self._deny()
        if path == "/refresh":
            started = refresher.start(full="full=1" in self.path)
            return self._send(202, json.dumps({"started": started, **refresher.status()}), "application/json")
        self._send(404, "Not found", "text/plain; charset=utf-8")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=scheduler, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    where = "localhost" if HOST in ("0.0.0.0", "::") else HOST
    log(f"Dashboard running at http://{where}:{PORT}"
        + (" (password protected)" if PASSWORD else " (no password set)"))
    log("Auto refresh every %g minutes" % REFRESH_MINUTES if REFRESH_MINUTES > 0 else "Auto refresh off")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
