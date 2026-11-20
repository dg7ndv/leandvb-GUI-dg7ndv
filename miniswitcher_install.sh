#!/usr/bin/env bash
# miniswitcher_install.sh - richtet alles fuer miniswitcher.py unter Linux (Debian/Ubuntu) ein (DG7NDV)
#
#   ./miniswitcher_install.sh            installieren (fragt per sudo nach dem Passwort)
#   ./miniswitcher_install.sh --check    nur pruefen, nichts installieren
#
# Benoetigt werden:
#   Python 3 mit  argparse json os sys shutil subprocess threading time urllib struct math glob  (enthalten)
#   tkinter            -> Paket python3-tk
#   websocket-client   -> Paket python3-websocket (sonst pip)
#   VLC                -> Player
#   ffprobe (ffmpeg)   -> Rufzeichen-Erkennung
#   OnAir              -> Empfaenger (onair-.deb von https://on-air.dev, nicht per apt)

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

ok()   { printf '  \033[32m[ok]\033[0m    %s\n' "$*"; }
miss() { printf '  \033[31m[fehlt]\033[0m %s\n' "$*"; }
info() { printf '\n\033[1m%s\033[0m\n' "$*"; }

check_all() {
    MISSING_APT=()
    info "Python-Module"
    if ! command -v python3 >/dev/null 2>&1; then
        miss "python3"; MISSING_APT+=(python3)
    else
        ok "python3 $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
        for m in argparse json os sys shutil subprocess threading time urllib.request struct math glob; do
            if python3 -c "import $m" 2>/dev/null; then ok "$m"; else miss "$m (Python-Installation defekt?)"; fi
        done
        if python3 -c "import tkinter" 2>/dev/null; then ok "tkinter"; else miss "tkinter"; MISSING_APT+=(python3-tk); fi
        if python3 -c "import websocket" 2>/dev/null; then ok "websocket (websocket-client)";
        else miss "websocket (websocket-client)"; MISSING_APT+=(python3-websocket); fi
    fi
    info "Programme"
    if command -v vlc >/dev/null 2>&1; then ok "vlc"; else miss "vlc"; MISSING_APT+=(vlc); fi
    if command -v ffprobe >/dev/null 2>&1; then ok "ffprobe (ffmpeg)"; else miss "ffprobe (ffmpeg)"; MISSING_APT+=(ffmpeg); fi
    if command -v onair >/dev/null 2>&1 || command -v dect2 >/dev/null 2>&1; then
        ok "OnAir ($(command -v onair || command -v dect2))"
    else
        miss "OnAir - .deb von https://on-air.dev installieren: sudo apt install ./onair_<version>_amd64.deb"
    fi
}

check_all

if [ "$CHECK_ONLY" = 1 ]; then
    info "Nur geprueft (--check)."
    [ ${#MISSING_APT[@]} -gt 0 ] && echo "Fehlend (apt): ${MISSING_APT[*]}"
    exit 0
fi

if [ ${#MISSING_APT[@]} -gt 0 ]; then
    info "Installiere: ${MISSING_APT[*]}"
    sudo apt-get update && sudo apt-get install -y "${MISSING_APT[@]}"
    # Falls es python3-websocket in der Distribution nicht gibt: per pip
    if ! python3 -c "import websocket" 2>/dev/null; then
        echo "python3-websocket nicht verfuegbar - nehme pip:"
        pip3 install --user websocket-client 2>/dev/null || pip3 install websocket-client --break-system-packages
    fi
    check_all
fi

info "Dateien"
if [ -f "$HERE/miniswitcher.py" ]; then
    chmod +x "$HERE/miniswitcher.py" && ok "miniswitcher.py ausfuehrbar"
else
    miss "miniswitcher.py nicht im Ordner $HERE"
fi

info "Fertig. Noch einmal in OnAir einrichten (siehe miniswitcher.html):"
cat <<'EOT'
  1. Ansicht -> Datafeeds -> Fernsteuerungs-API einschalten, Port 8765
  2. Tab Stream -> Outputs -> UDP 230.0.0.10 / 1234, "Apply", Haken "stream"
Start (LO passend zum LNB!):
  ./miniswitcher.py --lo 9750      # nicht umgebauter Universal-LNB
  ./miniswitcher.py --lo 9540      # umgebauter LNB (Beispiel)
EOT
