#!/bin/sh
# Installiert den Starter mit Icon in die Ubuntu-Anwendungsliste (~/.local/share/applications).
# Start-Parameter bei Bedarf unten in leandvb-GUI.desktop.in (Zeile Exec=) anpassen.
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
sed "s|@DIR@|$DIR|g" "$DIR/leandvb-GUI.desktop.in" > "$HOME/.local/share/applications/leandvb-GUI.desktop"
chmod +x "$HOME/.local/share/applications/leandvb-GUI.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
echo "Starter installiert: $HOME/.local/share/applications/leandvb-GUI.desktop"
