# my-personal-dashboard

A personal training dashboard built from Garmin Connect data, using Garmin's
official, read-only **Personal API**. It downloads your history to your own
computer and builds a single `dashboard.html` file that opens in any browser:
no server, no accounts, nothing to install beyond Python.

## What's on the dashboard

- **Today**: steps, active minutes, calories, resting heart rate, Body Battery,
  stress, last night's sleep and HRV, plus heart rate and Body Battery through
  the day.
- **Today's activities**: one card per activity with key stats, run/walk/stand
  split, heart rate zones, and synced graphs of heart rate, pace, cadence,
  power and breathing rate.
- **Your training**: weekly active minutes against the 150-minute goal, a
  "road to the marathon" progress track, and six headline tiles.
- **This week vs recent weeks**: distance, time and sessions by sport.
- **Trends**: resting heart rate, HRV, sleep stages and training load
  (1, 3, 6 or 12 months).
- **Records and biggest days**.

Hover (or tap on a phone) any chart to see exact numbers.

## Requirements

- Python 3.10 or newer. Only the standard library is used.
- A Garmin Connect account and a Personal API key.

## Setup

1. Create a Personal API key: open
   <https://connect.garmin.com/app/settings/personalApiToken>, click
   **Create Key**, accept the terms, choose an expiration and copy the key.
   Garmin shows it only once.
2. Copy the example settings file and paste your key after the `=`:

   ```bash
   cp .env.example .env.local
   ```

   `.env.local` is listed in `.gitignore` and must never be committed.
3. Check the connection:

   ```bash
   python3 test_connection.py
   ```

## Usage

```bash
./refresh.sh          # pull new data, rebuild, open the dashboard
./refresh.sh --full   # pull your whole history instead of the last 12 months
```

Or run the steps yourself:

```bash
python3 pull.py       # download to data/ (only fetches what is new)
python3 build.py      # write dashboard.html
```

## Files

| File | What it does |
|---|---|
| `garmin_api.py` | Tiny API client: swaps the key for a 24-hour token (cached in `data/`), retries on 429/502/503. |
| `pull.py` | Downloads activities, daily summaries, sleep, HRV, weight, fitness age, heart rate zones, and today's detail into `data/`. |
| `build.py` | Reads `data/` and writes the self-contained `dashboard.html` (data, styles and SVG charts all inline). |
| `refresh.sh` | Runs `pull.py` then `build.py` and opens the result. |
| `test_connection.py` | Lists your 5 latest activities to confirm the key works. |

## Notes on the Garmin Personal API

- Responses larger than about 256 KB fail with HTTP 500, so `pull.py` uses
  60-day ranges for daily summaries, 31 for sleep and 20 activities per page,
  and halves the range on a 500.
- The access token lasts 24 hours and is renewed after 20.
- Not available through this API: intraday stress, hourly steps and lap splits.

## Privacy

Your key lives only in `.env.local`. Your data lives only in `data/` and in the
generated `dashboard.html`. All three are git-ignored. The only network calls
are read-only requests to Garmin.
