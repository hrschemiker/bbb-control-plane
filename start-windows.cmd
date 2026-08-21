@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error
".venv\Scripts\python.exe" controller.py
exit /b 0
:error
echo.
echo Startup failed. Install Python 3.11 or later and try again.
pause
exit /b 1
