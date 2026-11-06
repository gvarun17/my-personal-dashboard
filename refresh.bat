@echo off
REM Pull new Garmin data, rebuild the dashboard and open it (Windows).
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% src\pull.py %* || exit /b 1
%PY% src\build.py || exit /b 1
start "" dashboard.html
