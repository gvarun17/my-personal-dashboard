"""Serve the dashboard as a small web app (standard library only).

    python3 src/server.py

- GET  /           the dashboard (sends you to /setup until a Garmin key is set up)
- GET  /setup      start page: your name, time zone and Garmin key
- POST /setup      check the key with Garmin and save the settings
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
import html
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import garmin_api
import settings

SRC = Path(__file__).resolve().parent           # where pull.py and build.py live
ROOT = SRC.parent                               # project folder (holds .env.local and data/)


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



def child_env():
    """Environment for pull.py / build.py: same data folder, current time zone."""
    return {**os.environ, "GARMIN_DATA_DIR": str(DATA), "DASHBOARD_OUT": str(OUT)}


def configured():
    return garmin_api.find_key()[0] is not None


def apply_tz(tz):
    """Use this time zone for "today" (here and in the refresh scripts)."""
    if not tz:
        return
    os.environ["TZ"] = tz
    if hasattr(time, "tzset"):   # not on Windows, where TZ is read per process
        time.tzset()


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
                r = subprocess.run(cmd, cwd=SRC, env=child_env(), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=1800)
                if r.returncode != 0:
                    lines = [l.strip() for l in (r.stdout + r.stderr).replace("\r", "\n").splitlines() if l.strip()]
                    reason = next((l for l in reversed(lines) if l.startswith("Stopped")), lines[-1] if lines else None)
                    raise RuntimeError(reason or f"{cmd[1]} failed")
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
    if configured() and (not OUT.exists() or REFRESH_MINUTES > 0):
        refresher.start()
    if REFRESH_MINUTES <= 0:
        return
    while True:
        time.sleep(REFRESH_MINUTES * 60)
        if configured():
            refresher.start()


WAITING_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>My Training</title>
<meta http-equiv="refresh" content="5"><style>body{margin:0;background:#0d0d0d;color:#f0efec;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;display:grid;place-items:center;min-height:100vh}
div{max-width:420px;padding:24px;text-align:center}p{color:#c3c2b7}</style></head><body><div>
<h1>Getting your Garmin data…</h1><p>The first download takes a minute or two.
This page reloads by itself.</p><p style="color:#e66767">%s</p></div></body></html>"""

SETUP_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Set up · My Training</title>
<style>
:root{color-scheme:dark;--page:#0d0d0d;--card:#1a1a19;--raise:#232321;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
--border:rgba(255,255,255,.12);--accent:#3987e5;--bad:#e66767;--good:#0ca30c}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:560px;margin:0 auto;padding:40px 16px 56px}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600}
h1{font-size:28px;margin:6px 0 6px;font-weight:650}
.lead{color:var(--ink2);margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px}
label{display:block;font-weight:600;margin:0 0 6px}
.field{margin-bottom:20px}
.hint{color:var(--muted);font-size:13px;margin:6px 0 0}
input{width:100%;font:inherit;color:var(--ink);background:var(--page);border:1px solid var(--border);border-radius:10px;padding:11px 12px}
input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
.keyrow{display:flex;gap:8px}
.keyrow input{flex:1;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px}
button{font:inherit;font-weight:600;cursor:pointer;border-radius:10px;border:1px solid var(--border);padding:11px 16px;background:var(--raise);color:var(--ink)}
button.primary{width:100%;background:var(--accent);border-color:var(--accent);color:#fff;padding:13px 16px}
button:disabled{opacity:.6;cursor:default}
.steps{margin:10px 0 0;padding-left:20px;color:var(--ink2);font-size:13.5px}
.steps li{margin:3px 0}
a{color:#86b6ef}
.error{background:rgba(230,103,103,.12);border:1px solid rgba(230,103,103,.4);color:#f3b3b3;border-radius:10px;padding:10px 12px;margin-bottom:18px}
.ok{color:var(--good);font-size:13px;margin:6px 0 0}
.privacy{color:var(--muted);font-size:12.5px;margin:16px 2px 0}
.back{display:inline-block;margin-top:18px;font-size:14px}
</style></head><body><div class="wrap">
<div class="eyebrow">Training log</div>
<h1>{title}</h1>
<p class="lead">{lead}</p>
<form class="card" method="post" action="setup" id="f" autocomplete="off">
{error}
<div class="field"><label for="name">Your name</label>
<input id="name" name="name" required maxlength="60" value="{name}" placeholder="e.g. Varun" autocomplete="given-name">
<p class="hint">Shown at the top of your dashboard.</p></div>
<div class="field"><label for="tz">Your time zone</label>
<input id="tz" name="tz" required value="{tz}" list="zones" placeholder="e.g. Asia/Kolkata" spellcheck="false">
<datalist id="zones">{zones}</datalist>
<p class="hint">So "today" means your today. Filled in from this device; change it if it's wrong.</p></div>
<div class="field"><label for="key">Garmin Personal API key</label>
<div class="keyrow"><input id="key" name="key" type="password" {key_required} placeholder="{key_placeholder}" spellcheck="false" autocapitalize="off">
<button type="button" id="show" aria-pressed="false">Show</button></div>
{key_status}
<ol class="steps">
<li>Open <a href="https://connect.garmin.com/app/settings/personalApiToken" target="_blank" rel="noopener noreferrer">Personal API Key in Garmin Connect</a> and log in.</li>
<li>Click <b>Create Key</b>, accept the terms, choose <b>1 year</b> and click <b>Create</b>.</li>
<li>Copy the key right away (Garmin shows it only once) and paste it above.</li>
</ol></div>
<button class="primary" type="submit" id="go">{button}</button>
<p class="privacy">Read-only: this app can't change anything in your Garmin account. Your key is checked with
Garmin, then saved only on this computer and never shown again.</p>
</form>
{back}
</div>
<script>
(function(){
  var tz = document.getElementById('tz');
  if (!tz.value) { try { tz.value = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {} }
  var key = document.getElementById('key'), show = document.getElementById('show');
  show.addEventListener('click', function(){
    var hidden = key.type === 'password';
    key.type = hidden ? 'text' : 'password';
    show.textContent = hidden ? 'Hide' : 'Show';
    show.setAttribute('aria-pressed', hidden);
  });
  document.getElementById('f').addEventListener('submit', function(){
    var b = document.getElementById('go'); b.disabled = true; b.textContent = 'Checking your key with Garmin…';
  });
})();
</script></body></html>"""


def setup_page(name="", tz="", error=""):
    """The start page. Never contains the key itself."""
    saved = settings.public()
    key, source = garmin_api.find_key()
    first = key is None
    name = name or saved.get("name") or ""
    tz = tz or saved.get("tz") or os.environ.get("TZ", "")
    if tz == "UTC" and not saved.get("tz"):
        tz = ""   # let the browser fill in the real one
    esc = html.escape
    fields = {
        "title": "Welcome! Let's set up your dashboard" if first else "Settings",
        "lead": ("Three quick things and your Garmin data starts downloading."
                 if first else "Change your name, time zone or Garmin key."),
        "error": f'<div class="error" role="alert">{esc(error)}</div>' if error else "",
        "name": esc(name), "tz": esc(tz),
        "zones": "".join(f'<option value="{esc(z)}">' for z in settings.time_zones()),
        "key_required": "required" if first else "",
        "key_placeholder": "Paste your key here" if first else "Leave empty to keep your current key",
        "key_status": "" if first else f'<p class="ok">✓ A key is set up (from the {esc(source)}).</p>',
        "button": "Connect to Garmin" if first else "Save",
        "back": "" if first else '<a class="back" href="./">← Back to the dashboard</a>',
    }
    page = SETUP_PAGE
    for k, v in fields.items():
        page = page.replace("{" + k + "}", v)
    return page


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

    def _same_origin(self):
        """Block form posts and refreshes sent from other websites."""
        if (self.headers.get("Sec-Fetch-Site") or "") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == (self.headers.get("Host") or "")

    def _redirect(self, where):
        self._send(303, "", "text/plain; charset=utf-8", {"Location": where})

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
        if path in ("/setup", "/setup/"):
            return self._send(200, setup_page())
        if path in ("/", "/index.html", "/dashboard.html"):
            if not configured():
                return self._redirect("setup")
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
        if not self._same_origin():
            return self._send(403, "Forbidden", "text/plain; charset=utf-8")
        if path == "/setup":
            return self._post_setup()
        if path == "/refresh":
            started = refresher.start(full="full=1" in self.path)
            return self._send(202, json.dumps({"started": started, **refresher.status()}), "application/json")
        self._send(404, "Not found", "text/plain; charset=utf-8")

    def _post_setup(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= 20000:
            return self._send(413, "Too large", "text/plain; charset=utf-8")
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        field = lambda k: (form.get(k) or [""])[0]
        name, tz, key = settings.clean_name(field("name")), settings.normalize_tz(field("tz")), settings.clean_key(field("key"))
        error = ""
        if not name:
            error = "Please enter your name."
        elif not settings.valid_tz(tz):
            error = "Please pick your time zone from the list (for example Asia/Kolkata)."
        elif not key and not configured():
            error = "Please paste your Garmin Personal API key."
        elif key:
            try:
                garmin_api.check_key(key)
            except garmin_api.GarminError as e:
                error = str(e)
        if error:
            return self._send(400, setup_page(name, tz, error))
        settings.save(name=name, tz=tz, key=key or None)
        apply_tz(tz)
        log("Settings saved" + (" with a new Garmin key" if key else ""))
        refresher.start()
        self._redirect("./")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    apply_tz(settings.load().get("tz"))
    threading.Thread(target=scheduler, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    where = "localhost" if HOST in ("0.0.0.0", "::") else HOST
    log(f"Dashboard running at http://{where}:{PORT}"
        + (" (password protected)" if PASSWORD else " (no password set)"))
    log("Auto refresh every %g minutes" % REFRESH_MINUTES if REFRESH_MINUTES > 0 else "Auto refresh off")
    if not configured():
        log(f"Open http://{where}:{PORT} to set up your name and Garmin key")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
