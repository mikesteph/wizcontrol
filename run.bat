@echo off
setlocal
cd /d "%~dp0"
title WiZ Control

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
  echo Setting up WiZ Control for the first time - this takes a minute...
  %PY% -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
  ".venv\Scripts\python.exe" -m pip install -r requirements-desktop.txt || echo pywebview could not be installed - the app will open in an Edge app window instead.
)

".venv\Scripts\python.exe" -m wizcontrol %*
if errorlevel 1 pause
goto :eof

:nopython
echo.
echo Python 3.10 or newer is required.
echo Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:fail
echo.
echo Installing dependencies failed - see the messages above.
rmdir /s /q .venv
pause
exit /b 1
