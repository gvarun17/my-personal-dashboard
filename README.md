# my-personal-dashboard

A personal training dashboard built from your Garmin Connect data, using
Garmin's official, read-only **Personal API**. It runs as a small web app in a
Docker container, so it works the same on **macOS, Windows and Linux**
(Intel/AMD and ARM, including Apple Silicon and Raspberry Pi 4/5). Open it on
any device: laptop, phone or tablet.

- **Today's call**: should today be a training day, an easy day or a rest day?
  It compares last night's HRV, your sleep, your resting heart rate (against
  your own 30-day normal) and your morning Body Battery, and says why. A guide
  from your watch data, not medical advice.
- **Today**: steps, active minutes, calories, resting heart rate, Body Battery,
  stress, last night's sleep and HRV, and heart rate and Body Battery through
  the day. Use **‹ / ›** to step back through the last 90 days (graphs through
  the day are kept for the last 7).
- **The day's activities**: a short list. Tap one to open its key stats,
  run/walk/stand split, heart rate zones and synced graphs of heart rate, pace,
  cadence, power and breathing rate.
- **Two views**, switched at the top (your browser remembers the choice):
  - **Running**: everything below.
  - **Health & strength**: steps, resting heart rate, sleep, stress, Body
    Battery and active calories (7-day tiles and daily charts), plus your
    workouts other than running: hours per week by type and your latest
    sessions.
- **Weekly goal and streak**: active minutes (Garmin's goal by default), total
  km, running km or number of runs a week, set in **Settings**. Shows what's
  left this week and how many weeks in a row you've hit it.
- **Milestones, from first run to Ironman**: first run, 1 km, 5K, 10K, half
  marathon, marathon, half Ironman and Ironman, with the date you reached each
  one and an estimate for the next (adding about 10% a week to your longest run).
- **Your training**: this week vs recent weeks, trends (resting HR, HRV, sleep, training load),
  records and biggest days.
- Pulls new data **every hour** by itself, plus a **Refresh** button.
- A **start page** that asks for your name, time zone and Garmin key, and
  checks the key with Garmin before saving it.
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

### 3. Start it

```bash
docker compose up -d --build
```

The container restarts by itself when your computer reboots (once Docker is
running).

### 4. Open it and fill in the start page

Open **<http://localhost:8080>**. The first time, a start page asks for:

1. **Your name**: shown at the top of your dashboard.
2. **Your time zone**: filled in from your device, so "today" means your today.
3. **Your Garmin Personal API key**: the page shows how to get one:
   1. Open <https://connect.garmin.com/app/settings/personalApiToken> and log in.
   2. Click **Create Key**, accept the terms, choose **1 year** and click **Create**.
   3. Copy the key right away (Garmin shows it only once) and paste it in.

Click **Connect to Garmin**. The app checks the key with Garmin first, so a
mistyped or expired key gets a clear message right away. Then it starts
downloading your data. The first download takes a minute or two, and the page
reloads by itself when it's ready.

To change your name, time zone, weekly goal or key later, click **Settings** at
the top of the dashboard. Leave the key box empty to keep your current key, and
the goal number empty for the suggested target.

Where the key is kept: in `settings.json` inside the app's data folder (the
`garmin-data` Docker volume), readable only by the app. It's never shown again
or sent anywhere except to Garmin.

### 5. Optional: a password and other settings

Before you let any other device reach the app (step 6), set a password. Copy
the example settings file:

```bash
# macOS / Linux
cp .env.example .env.local
```

```powershell
# Windows (PowerShell)
Copy-Item .env.example .env.local
```

Open `.env.local` in any text editor, set `DASHBOARD_PASSWORD`, and run
`docker compose up -d` to apply it. Your browser will then ask for it (any
username works). The start page and Settings are behind the same password.

You can also put `GARMIN_PERSONAL_API_KEY` and `TZ` in `.env.local` instead of
using the start page. A key saved on the start page takes priority.
`.env.local` is git-ignored and never copied into the image.

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
| Pull your whole history (once) | `docker compose exec dashboard python src/pull.py --full` then press **Refresh** |
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
| `GARMIN_PERSONAL_API_KEY` | empty | Your Garmin key, if you'd rather not use the start page (a key saved on the start page wins) |
| `DASHBOARD_PASSWORD` | empty (no login) | Password for the web app. Set it whenever any other device can reach it. |
| `DASHBOARD_USER` | empty (any name) | Optional login username |
| `TZ` | `UTC` | Your time zone (the start page's choice wins) |
| `REFRESH_MINUTES` | `60` | How often to pull new data. `0` means only when you press Refresh. |
| `HOST_PORT` | `8080` | Port on your computer (set it in your shell or a `.env` file if 8080 is taken) |

### When the key expires

Garmin keys last up to a year. Create a new key on the same Garmin page, then
open **Settings** on the dashboard and paste it in. (If you keep your key in
`.env.local` instead, update it there and run `docker compose up -d`.)

---

## Run without Docker

You need Python 3.10 or newer (standard library only, nothing to install).

```bash
# Check your key: lists your 5 latest activities
python3 src/test_connection.py

# Web app at http://localhost:8080, auto refresh and the Refresh button
python3 src/server.py

# Or a one-off static file you open directly (no server)
./refresh.sh            # macOS / Linux
refresh.bat             # Windows
```

On Windows, use `py` or `python` instead of `python3`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says "Getting your Garmin data…" for a long time | `docker compose logs` shows the error. Usually the key is mistyped or expired: open `/setup` (the **Settings** button) and paste a new one. |
| "Garmin refused the key (HTTP 401)" | The key is wrong or expired. Create a new one (see above). |
| Port 8080 already in use | Run `HOST_PORT=8090 docker compose up -d` and open `http://localhost:8090`. |
| "Today" shows the wrong day | Fix your time zone in **Settings**. |
| `refresh.sh: bad interpreter` after cloning on Windows | The repo's `.gitattributes` keeps LF endings. Re-clone, or run `git add --renormalize .`. |
| No HRV or sleep data | Wear your watch to bed. Garmin needs about 3 weeks of nights to set an HRV baseline. |

## How it works

```
my-personal-dashboard/
├── src/                  the app (Python standard library only)
│   ├── server.py         web app: dashboard, timed + button refresh, optional login
│   ├── garmin_api.py     Garmin Personal API client (token, retries)
│   ├── pull.py           downloads your data into data/
│   ├── build.py          turns data/ into dashboard.html
│   ├── settings.py       your name, time zone, weekly goal and key from the start page
│   ├── coach.py          today's call, weekly goal and streak, milestones
│   └── test_connection.py  checks your key
├── tests/                offline test suite (Python + dashboard JavaScript)
├── .github/              pull request checks and Dependabot
├── Dockerfile, compose.yaml
├── refresh.sh, refresh.bat, run_tests.sh, run_tests.bat
├── .env.example          copy to .env.local (git-ignored) for your key and settings
├── data/                 your downloaded data (git-ignored, created on first run;
│                         days/ holds the last week's graphs through the day)
└── dashboard.html        the built dashboard (git-ignored)
```

| File | What it does |
|---|---|
| `src/server.py` | The web app. Serves the dashboard and the start page, runs `pull.py` + `build.py` on a timer and when you press Refresh, and asks for a login if a password is set. Rejects form posts from other websites. Routes: `/`, `/setup`, `POST /setup`, `POST /refresh`, `/status`, `/healthz`. |
| `src/garmin_api.py` | API client. Finds the key (start page, then environment, then `.env.local`), swaps it for a 24-hour token (cached and renewed after 20 hours) and retries on 429/502/503. |
| `src/coach.py` | Works out the top cards from your data: today's call (training, easy or rest day), the weekly goal and streak, and the milestone ladder. |
| `src/settings.py` | Saves the start page's name, time zone, weekly goal and key in `data/settings.json`, readable only by your user account. |
| `src/pull.py` | Downloads activities, daily summaries, sleep, HRV, weight, fitness age, heart rate zones, and heart rate, Body Battery and activity detail through each of the last 7 days. Only fetches what is new (each past day once). |
| `src/build.py` | Turns the data into one self-contained `dashboard.html` (data, styles and SVG charts inline; nothing loaded from the internet). |
| `src/test_connection.py` | Lists your 5 latest activities to check the key. |
| `Dockerfile`, `compose.yaml` | The container: `python:3.12-slim`, runs as a non-root user, code in `/app/src`, data in the `/data` volume, health check on `/healthz`. |
| `refresh.sh`, `refresh.bat` | Pull + build + open, without the web app. |

The scripts work out their paths from where the files are, so you can run
them from any folder. `.env.local`, `data/` and `dashboard.html` always live in
the project folder, next to `src/`.

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
| Python: everything in `src/` | `tests/test_*.py` (unit and real HTTP requests) | **100%** of lines and branches (enforced) |
| Dashboard JavaScript (charts, hovers, buttons, refresh) | `tests/test_dashboard_js.py` and `tests/js/dashboard.test.cjs` run it in Node.js with a simulated page, across 6 datasets (full, empty, rest day, long ride, sparse, phone/tablet/desktop) | **100%** of lines and functions, 93%+ of branches (enforced) |

The JavaScript tests need Node.js 22+ and are skipped without it. Set
`JS_COVERAGE=1` to print the JavaScript coverage table.

The first run also installs [ruff](https://docs.astral.sh/ruff/), the Python
linter the PR checks use. To lint locally: `.venv-test/bin/python -m ruff check src tests`.

## Pull request checks

Every pull request into `main` runs these checks on GitHub
(`.github/workflows/pr-checks.yml`). Merge only when all of them are green.

| Check | What it does | Fails when | How to fix a failure |
|---|---|---|---|
| **Tests** | All tests on Windows, macOS and Linux, with Python 3.10 and 3.13 | A test fails, or Python coverage drops below 100% | Run `./run_tests.sh` and fix what it reports |
| **Lint** | `ruff` checks the Python, `hadolint` checks the Dockerfile | Either reports a problem | The check's log names the file, line and rule |
| **SonarCloud** | Bugs, vulnerabilities, security hotspots and code quality, with test coverage | Sonar's quality gate fails | Open the SonarCloud link in the check's log |
| **CodeQL** | GitHub's security scan of the Python, JavaScript and the workflow file | A security problem is found | See the repo's Security tab, Code scanning |
| **Secret scan** | `gitleaks` looks for keys, tokens and passwords in every commit | Anything that looks like a secret | Remove it, then create a new key in Garmin Connect: once pushed, treat it as leaked |
| **Dependency review** | Checks dependencies a PR adds or updates | One has a known vulnerability | Use a fixed version |
| **App build, run and image scan** | Builds the Docker image, starts a fresh install and checks the health check, the start page, the cross-site form block, the non-root user and that no `.env.local` is inside. Then `trivy` scans the image | The app doesn't start or behave, or the image has a high or critical vulnerability that has a fix | Usually rebuilding on the newest base image fixes the scan; Dependabot opens that PR |

Dependabot (`.github/dependabot.yml`) opens weekly PRs that update the CI tools,
the Docker base image and the test tools. Each runs these checks too.

One-time setup on GitHub and SonarCloud:

1. **SonarCloud.** Sign in at [sonarcloud.io](https://sonarcloud.io) with GitHub
   and import this repo. Under Administration, Analysis Method, turn off
   *Automatic Analysis*. Check that the organization and project keys match
   `sonar-project.properties`. Create a token (My Account, Security) and save it
   in GitHub under **Settings, Secrets and variables, Actions** as
   `SONAR_TOKEN`. Until then the SonarCloud check fails with a message saying so.
2. **Block merging until the checks pass.** Under **Settings, Rules, Rulesets**,
   add a rule for `main` that requires a pull request and requires the checks
   above to pass.
3. **Optional, free:** under **Settings, Code security**, turn on secret
   scanning, push protection and Dependabot alerts.

## Privacy

- Read-only: the Personal API can't change anything in your Garmin account.
- Your key lives in `data/settings.json` (from the start page) or `.env.local`.
  Both are git-ignored and Docker-ignored, never built into the image, and
  never shown in a page or log.
- Your data stays in the `garmin-data` volume (or `data/` when run without
  Docker). The only outside calls are read-only requests to Garmin.
- Set `DASHBOARD_PASSWORD` before letting any other device reach the app.
