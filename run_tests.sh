#!/bin/sh
# Run every test with coverage (macOS / Linux). Needs Python 3.10+;
# Node.js 22+ adds the dashboard JavaScript tests. Test tools go into a
# private .venv-test folder, so nothing is installed system-wide.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY=$c; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.10 or newer is needed: https://www.python.org/downloads/"; exit 1; }
if [ ! -x .venv-test/bin/python ]; then
  "$PY" -m venv .venv-test || exit 1
  .venv-test/bin/python -m pip install --quiet -r requirements-dev.txt || exit 1
fi
VPY=.venv-test/bin/python
echo "Using $("$VPY" --version)"
"$VPY" -m coverage run -m unittest discover -s tests -t . "$@" && "$VPY" -m coverage report
