#!/usr/bin/env bash
# install.sh - GNOME-Erweiterung "QO-100 Spektrum" in ~/.local/share/gnome-shell/extensions installieren und aktivieren
# Unter Wayland erst nach erneutem An-/Abmelden sichtbar.  Entfernen:  ./install.sh --entfernen
set -eu
UUID=qo100-spektrum@dg7ndv; E=~/.local/share/gnome-shell/extensions/$UUID; cd "$(dirname "$0")"
if [ "${1:-}" = "--entfernen" ]; then gnome-extensions disable $UUID 2>/dev/null || true; rm -rf "$E"; echo entfernt; exit 0; fi
mkdir -p "$E"; cp metadata.json extension.js peaks.js stylesheet.css "$E"/
cur=$(gsettings get org.gnome.shell enabled-extensions)
case "$cur" in *"$UUID"*) ;; *) gsettings set org.gnome.shell enabled-extensions "$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1]); l.append('$UUID'); print(l)" "$cur")";; esac
echo "installiert in $E - bitte einmal ab- und wieder anmelden (Wayland)"
