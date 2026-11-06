#!/bin/sh
# Pull new Garmin data, rebuild the dashboard and open it (macOS / Linux).
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
[ -x /opt/homebrew/bin/python3.12 ] && PY=/opt/homebrew/bin/python3.12
"$PY" pull.py "$@" && "$PY" build.py || exit 1
if command -v open >/dev/null 2>&1; then open dashboard.html
elif command -v xdg-open >/dev/null 2>&1; then xdg-open dashboard.html >/dev/null 2>&1 &
else echo "Open dashboard.html in your browser."; fi
