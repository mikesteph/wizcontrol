@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")

if not exist ".venv-build\Scripts\python.exe" (
  %PY% -m venv .venv-build || (echo Python 3.10+ is required & pause & exit /b 1)
)
set "VPY=.venv-build\Scripts\python.exe"
%VPY% -m pip install --upgrade pip
%VPY% -m pip install -r requirements.txt pyinstaller || (pause & exit /b 1)
%VPY% -m pip install -r requirements-desktop.txt || echo pywebview unavailable - building without the native window (Edge app window will be used)

%VPY% -m PyInstaller --noconfirm --clean --onefile --windowed --name WizControl --icon assets\icon.ico --add-data "wizcontrol\web;wizcontrol\web" launcher.py || (pause & exit /b 1)

echo.
echo ============================================================
echo  Built dist\WizControl.exe - copy it anywhere and run it.
echo ============================================================
pause
