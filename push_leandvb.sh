#!/bin/bash
# push_leandvb.sh - leandvb-GUI (QO-100 Tuner Switcher) committen, taggen und zu GitHub pushen
#   Repo: dg7ndv/leandvb-GUI-dg7ndv (Ordner dieses Skripts; Ausschluesse stehen in .gitignore)
# Aufruf: ./push_leandvb.sh --Bemerkung "Text" [--Version 0.6] [--Release] [--trocken] [--force]
#   --Bemerkung "Text"   Commit-Text (ohne Angabe: mit --Version "Version X", sonst Rueckfrage); bei --Release auch der Release-Text
#   --Version X          Tag vX anlegen und pushen (z.B. 0.6); die Version steht auch im Commit-Text
#   --Release            zusaetzlich GitHub-Release zum Tag vX anlegen (braucht --Version)
#   --trocken            nur anzeigen, was passieren wuerde (nichts committen/pushen)
#   --force              Pruefung auf interne IP-Adressen / Geheimnisse uebergehen
set -e
cd "$(dirname "$0")"
VERSION=""; BEM=""; REL=0; TROCKEN=0; FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --Version|--version) VERSION="${2#v}"; shift 2 ;;
        --Bemerkung|--bemerkung) BEM="$2"; shift 2 ;;
        --Release) REL=1; shift ;;
        --trocken) TROCKEN=1; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help|--hilfe) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unbekannte Option: $1 (siehe --help)"; exit 1 ;;
    esac
done
[ "$REL" = 1 ] && [ -z "$VERSION" ] && { echo "--Release braucht --Version"; exit 1; }
# ohne --Bemerkung mit --Version: Text = "Version X" (keine Rueckfrage); ohne beides: Rueckfrage
[ -z "$BEM" ] && [ -n "$VERSION" ] && BEM="Version $VERSION"
[ -z "$BEM" ] && { read -r -p "Bemerkung (Commit-Text): " BEM; }
[ -z "$BEM" ] && { echo "Keine Bemerkung angegeben, Abbruch."; exit 1; }
case "$BEM" in "Version $VERSION") MSG="$BEM" ;; *) MSG="${VERSION:+Version $VERSION: }$BEM" ;; esac

git add -A
echo "=== Zu committende Aenderungen ==="; git status --short | head -60
if [ "$FORCE" != 1 ]; then      # Sicherheitspruefung nur ueber die hinzugefuegten Zeilen
    BAD=$(git diff --cached -U0 | grep -E '^\+[^+]' | grep -E '192\.168\.([1-9][0-9]*)\.|\b10\.[0-9]+\.[0-9]+\.[0-9]+\b|(passw|secret|api[_-]?key|token)[a-z_]*\s*[=:]\s*["'"'"'][^"'"'"']{4,}' | grep -v -E '192\.168\.2\.1\b|--telnet-password|"admin"|passwd = ' | head -5 || true)
    if [ -n "$BAD" ]; then echo; echo "ACHTUNG: moegliche interne IPs/Geheimnisse in neuen Zeilen:"; echo "$BAD"; echo "Pruefen oder mit --force trotzdem pushen."; git reset -q; exit 1; fi
fi
if git diff --cached --quiet && [ -z "$VERSION" ]; then echo "Keine Aenderungen, nichts zu tun."; exit 0; fi
[ "$TROCKEN" = 1 ] && { echo "(trocken) Commit: $MSG${VERSION:+  / Tag v$VERSION}"; git reset -q; exit 0; }
git diff --cached --quiet || git commit -q -m "$MSG"
[ -n "$VERSION" ] && git tag -a "v$VERSION" -m "Version $VERSION${BEM:+: $BEM}"
git push origin HEAD
[ -n "$VERSION" ] && git push origin "v$VERSION"
if [ "$REL" = 1 ]; then gh release create "v$VERSION" --title "Version $VERSION" --notes "$BEM"; fi
echo; echo "=== Fertig ==="; git log --oneline -3
