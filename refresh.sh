#!/bin/bash
# Pull new Garmin data, rebuild the dashboard and open it.
cd "$(dirname "$0")" || exit 1
PY=/opt/homebrew/bin/python3.12
[ -x "$PY" ] || PY=python3
"$PY" pull.py "$@" && "$PY" build.py && open dashboard.html
