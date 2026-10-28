#!/usr/bin/env bash
# Start WiZ Control on Linux (first run creates a virtualenv and installs dependencies).
set -e
cd "$(dirname "$(readlink -f "$0")")"
PY="${PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
  echo "Setting up WiZ Control for the first time..."
  # --system-site-packages lets pywebview use the distro's GTK/WebKit bindings if installed
  "$PY" -m venv --system-site-packages .venv || {
    echo "Could not create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m pip install -r requirements-desktop.txt \
    || echo "(pywebview not installed - the app will use a Chromium app window or your browser)"
fi

exec .venv/bin/python -m wizcontrol "$@"
