@echo off
REM Run every test with coverage (Windows). Needs Python 3.10+;
REM Node.js 22+ adds the dashboard JavaScript tests. Test tools go into a
REM private .venv-test folder, so nothing is installed system-wide.
cd /d "%~dp0"
if not exist .venv-test\Scripts\python.exe (
  where py >nul 2>nul && (py -3 -m venv .venv-test) || (python -m venv .venv-test)
  if errorlevel 1 exit /b 1
  .venv-test\Scripts\python.exe -m pip install --quiet -r requirements-dev.txt || exit /b 1
)
.venv-test\Scripts\python.exe -m coverage run -m unittest discover -s tests -t . %* || exit /b 1
.venv-test\Scripts\python.exe -m coverage report
