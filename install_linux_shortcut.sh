#!/usr/bin/env bash
# Add WiZ Control to the Linux application menu.
set -e
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
if [ -x "$DIR/dist/WizControl" ]; then EXEC="$DIR/dist/WizControl"; else EXEC="$DIR/run.sh"; fi
mkdir -p ~/.local/share/applications ~/.local/share/icons
cp "$DIR/assets/icon.png" ~/.local/share/icons/wizcontrol.png
cat > ~/.local/share/applications/wizcontrol.desktop <<DESK
[Desktop Entry]
Type=Application
Name=WiZ Control
Comment=Control WiZ smart lights on your local network
Exec="$EXEC"
Icon=$HOME/.local/share/icons/wizcontrol.png
Terminal=false
Categories=Utility;
DESK
echo "Installed menu entry -> $EXEC"
