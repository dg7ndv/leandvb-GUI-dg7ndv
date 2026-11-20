#!/bin/sh
# Installiert den Starter "QO 100 MultiPlayer" mit Radio-Icon: Anwendungsliste (~/.local/share/applications) und Desktop-Symbol.
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="$HOME/.local/share/applications"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$APPS"
sed "s|@DIR@|$DIR|g" "$DIR/multiplayer.desktop.in" > "$APPS/multiplayer.desktop"
chmod +x "$APPS/multiplayer.desktop"
if [ -d "$DESK" ]; then
    cp "$APPS/multiplayer.desktop" "$DESK/multiplayer.desktop"
    chmod +x "$DESK/multiplayer.desktop"
    gio set "$DESK/multiplayer.desktop" metadata::trusted true 2>/dev/null     # Ubuntu: Symbol als vertrauenswuerdig markieren (sonst "Starten erlauben")
fi
update-desktop-database "$APPS" 2>/dev/null
echo "Starter installiert: $APPS/multiplayer.desktop"
[ -d "$DESK" ] && echo "Desktop-Symbol:      $DESK/multiplayer.desktop"
