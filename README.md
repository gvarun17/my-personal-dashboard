# my-personal-dashboard

A personal training dashboard built from your Garmin Connect data, using
Garmin's official, read-only **Personal API**. It runs as a small web app in a
Docker container, so it works the same on **macOS, Windows and Linux**
(Intel/AMD and ARM, including Apple Silicon and Raspberry Pi 4/5). Open it on
any device: laptop, phone or tablet.

- **Today**: steps, active minutes, calories, resting heart rate, Body Battery,
  stress, last night's sleep and HRV, and heart rate and Body Battery through
  the day.
- **Today's activities**: one card each, with key stats, run/walk/stand split,
  heart rate zones and synced graphs of heart rate, pace, cadence, power and
  breathing rate.
- **Your training**: weekly active minutes, a "road to the marathon" tracker,
  this week vs recent weeks, trends (resting HR, HRV, sleep, training load),
  records and biggest days.
- Pulls new data **every hour** by itself, plus a **Refresh** button.
- Optional **password** login.

Hover over (or tap) any chart to see exact numbers.

---

## Deploy with Docker (recommended, any OS)

### 1. Install Docker

| Your computer | Install |
|---|---|
| macOS | [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/), then open it once |
| Windows 10/11 | [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) (accept the WSL 2 option), then open it once |
| Linux | [Docker Engine](https://docs.docker.com/engine/install/) and the Compose plugin |

Check it works (in Terminal on macOS/Linux, or PowerShell on Windows):

```bash
docker compose version
```

### 2. Get the code

```bash
git clone https://github.com/gvarun17/my-personal-dashboard.git
cd my-personal-dashboard
```

(No Git? Click **Code → Download ZIP** on GitHub, unzip it, and open a terminal in that folder.)

### 3. Get your Garmin Personal API key

1. Open <https://connect.garmin.com/app/settings/personalApiToken> and log in.
2. Click **Create Key**, accept the terms, choose **1 year** and click **Create**.
3. Copy the key right away. Garmin shows it only once. If you lose it, delete
   it on that page and create a new one.

### 4. Create your settings file

Copy the example file:

```bash
# macOS / Linux
cp .env.example .env.local
```

```powershell
# Windows (PowerShell)
Copy-Item .env.example .env.local
```

Open `.env.local` in any text editor and fill it in:

```ini
GARMIN_PERSONAL_API_KEY=paste-your-key-here
DASHBOARD_PASSWORD=choose-a-password
TZ=Asia/Kolkata
REFRESH_MINUTES=60
```

- No spaces or quotes around the values.
- `TZ` is your time zone, so "today" means your today. Find yours in the
  [list of time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)
  (for example `Europe/London` or `America/New_York`).
- `.env.local` is git-ignored and never copied into the image. Never commit it.

### 5. Start it

```bash
docker compose up -d --build
```

Open **<http://localhost:8080>**. The first download takes a minute or two,
and the page reloads by itself when it's ready. The container restarts by
itself when your computer reboots (once Docker is running).

### 6. Open it from your phone and other devices

By default only the computer running Docker can open the dashboard. Pick one
of these:

**Option A: Tailscale (recommended: private, encrypted, works anywhere)**

[Tailscale](https://tailscale.com/download) is a free private network that
only your own devices can join.

1. Install Tailscale on the computer running the dashboard and on your phone,
   and sign in to the same account on both.
2. On the computer running the dashboard, run:

   ```bash
   tailscale serve --bg 8080
   ```

3. It prints an address like `https://your-computer.your-tailnet.ts.net`.
   Open it on your phone. It works on mobile data too, not just home Wi-Fi.

Nothing is opened to the internet, and `compose.yaml` stays as it is.

**Option B: Home Wi-Fi only**

1. Make sure `DASHBOARD_PASSWORD` is set in `.env.local`.
2. In `compose.yaml`, change `"127.0.0.1:${HOST_PORT:-8080}:8080"` to
   `"${HOST_PORT:-8080}:8080"`.
3. Run `docker compose up -d` again.
4. Find the computer's local IP address (macOS: System Settings → Wi-Fi →
   Details; Windows: `ipconfig`; Linux: `hostname -I`) and open
   `http://THAT-IP:8080` on your phone.

This is plain HTTP, so use it only on a network you trust. Allow port 8080
if your firewall asks.

**Option C: Always-on (cloud server or Raspberry Pi)**

If you want it to work even when your laptop is off, run the same steps 1–5
on an always-on machine: a Raspberry Pi, a home server or a small cloud VM
(any Linux with Docker). Then use **Option A** to reach it. Install Tailscale
on that machine too, and you won't need to open any public ports.

If you must expose it publicly instead, put it behind HTTPS (for example
[Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy):
`caddy reverse-proxy --from your.domain --to localhost:8080`), and always set
`DASHBOARD_PASSWORD`. On a cloud host, your key and data live on that server.

---

## Everyday commands

Run these in the project folder.

| What | Command |
|---|---|
| See if it's running | `docker compose ps` |
| See what it's doing | `docker compose logs -f` |
| Refresh now | Press **Refresh** on the dashboard |
| Pull your whole history (once) | `docker compose exec dashboard python pull.py --full` then press **Refresh** |
| Stop | `docker compose down` |
| Start again | `docker compose up -d` |
| Update to the latest code | `git pull` then `docker compose up -d --build` |
| Back up your data | `docker compose cp dashboard:/data ./backup` |
| Delete all downloaded data | `docker compose down -v` (cannot be undone) |

Your data lives in a Docker volume called `garmin-data`, so it survives
restarts and updates.

### Settings (in `.env.local`)

| Setting | Default | What it does |
|---|---|---|
| `GARMIN_PERSONAL_API_KEY` | (required) | Your Garmin Personal API key |
| `DASHBOARD_PASSWORD` | empty (no login) | Password for the web app. Set it whenever any other device can reach it. |
| `DASHBOARD_USER` | empty (any name) | Optional login username |
| `TZ` | `UTC` | Your time zone |
| `REFRESH_MINUTES` | `60` | How often to pull new data. `0` means only when you press Refresh. |
| `HOST_PORT` | `8080` | Port on your computer (set it in your shell or a `.env` file if 8080 is taken) |

### When the key expires

Garmin keys last up to a year. Create a new key on the same Garmin page,
paste it into `.env.local`, and run `docker compose up -d` to apply it.

---

## Run without Docker

You need Python 3.10 or newer (standard library only, nothing to install).

```bash
# Web app at http://localhost:8080, auto refresh and the Refresh button
python3 server.py

# Or a one-off static file you open directly (no server)
./refresh.sh            # macOS / Linux
refresh.bat             # Windows
```

On Windows, use `py` or `python` instead of `python3`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says "Getting your Garmin data…" for a long time | `docker compose logs` shows the error. Usually the key is missing, mistyped or expired. Fix `.env.local`, then run `docker compose up -d`. |
| "Garmin refused the key (HTTP 401)" | The key is wrong or expired. Create a new one (see above). |
| Port 8080 already in use | Run `HOST_PORT=8090 docker compose up -d` and open `http://localhost:8090`. |
| "Today" shows the wrong day | Set `TZ` in `.env.local`, then run `docker compose up -d`. |
| `refresh.sh: bad interpreter` after cloning on Windows | The repo's `.gitattributes` keeps LF endings. Re-clone, or run `git add --renormalize .`. |
| No HRV or sleep data | Wear your watch to bed. Garmin needs about 3 weeks of nights to set an HRV baseline. |

## How it works

| File | What it does |
|---|---|
| `server.py` | The web app. Serves the dashboard, runs `pull.py` + `build.py` on a timer and when you press Refresh, and asks for a login if a password is set. Routes: `/`, `POST /refresh`, `/status`, `/healthz`. |
| `garmin_api.py` | API client. Swaps the key for a 24-hour token (cached and renewed after 20 hours) and retries on 429/502/503. |
| `pull.py` | Downloads activities, daily summaries, sleep, HRV, weight, fitness age, heart rate zones and today's activity detail. Only fetches what is new. |
| `build.py` | Turns the data into one self-contained `dashboard.html` (data, styles and SVG charts inline; nothing loaded from the internet). |
| `Dockerfile`, `compose.yaml` | The container: `python:3.12-slim`, runs as a non-root user, data in the `/data` volume, health check on `/healthz`. |
| `refresh.sh`, `refresh.bat` | Pull + build + open, without the web app. |
| `test_connection.py` | Lists your 5 latest activities to check the key. |

Notes on Garmin's Personal API:

- Responses over about 256 KB fail with HTTP 500, so requests are chunked
  (60-day daily summaries, 31-day sleep, 20 activities per page) and halved on
  a 500.
- Intraday stress, hourly steps and lap splits aren't available through it.

## Tests

Every test runs offline against made-up Garmin data. Network access is blocked
inside the tests, and your real key, settings and data are never read.

```bash
./run_tests.sh        # macOS / Linux
run_tests.bat         # Windows
```

The first run installs [coverage.py](https://coverage.readthedocs.io/), a
test-only tool (`requirements-dev.txt`). The app itself still uses only the
Python standard library. Or run the steps yourself:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m coverage run -m unittest discover -s tests -t .
python3 -m coverage report      # fails if Python coverage drops below 100%
```

What's covered:

| Area | Tests | Coverage |
|---|---|---|
| Python: `garmin_api.py`, `pull.py`, `build.py`, `server.py`, `test_connection.py` | `tests/test_*.py` (unit and real HTTP requests) | **100%** of lines and branches (enforced) |
| Dashboard JavaScript (charts, hovers, buttons, refresh) | `tests/test_dashboard_js.py` and `tests/js/dashboard.test.cjs` run it in Node.js with a simulated page, across 6 datasets (full, empty, rest day, long ride, sparse, phone/tablet/desktop) | **100%** of lines and functions, 93%+ of branches (enforced) |

The JavaScript tests need Node.js 22+ and are skipped without it. Set
`JS_COVERAGE=1` to print the JavaScript coverage table.

GitHub Actions (`.github/workflows/tests.yml`) runs the whole suite on
**Windows, macOS and Linux** with Python 3.10 and 3.13 on every push. It also
builds the Docker image and checks that it starts, passes its health check and
runs as a regular (not admin) user.

## Privacy

- Read-only: the Personal API can't change anything in your Garmin account.
- Your key is only in `.env.local`, which is git-ignored and Docker-ignored.
  It's passed to the container at run time and is never built into the image.
- Your data stays in the `garmin-data` volume (or `data/` when run without
  Docker). The only outside calls are read-only requests to Garmin.
- Set `DASHBOARD_PASSWORD` before letting any other device reach the app.
