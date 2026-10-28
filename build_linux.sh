#!/usr/bin/env bash
# Build a single-file Linux executable at dist/WizControl
set -e
cd "$(dirname "$(readlink -f "$0")")"
PY="${PYTHON:-python3}"
[ -x .venv-build/bin/python ] || "$PY" -m venv --system-site-packages .venv-build
VPY=.venv-build/bin/python
$VPY -m pip install --upgrade pip
$VPY -m pip install -r requirements.txt pyinstaller
$VPY -m pip install -r requirements-desktop.txt || true
$VPY -m PyInstaller --noconfirm --clean --onefile --windowed --name WizControl \
  --add-data "wizcontrol/web:wizcontrol/web" launcher.py
echo
echo "Built dist/WizControl  (run ./install_linux_shortcut.sh to add it to your app menu)"
