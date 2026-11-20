#!/usr/bin/env bash
# veroeffentlichen_pruefen.sh - Trockenlauf vor dem Veroeffentlichen auf GitHub: zeigt, was NICHT ausgeschlossen ist und noch Persoenliches enthaelt
# (interne IPs 192.168.x.x, Heimatpfade /home/..., Passwoerter/Token, E-Mail-Adressen). Aendert nichts, committet nichts.
cd "$(dirname "$0")" || exit 1
FILES=$(git ls-files -co --exclude-standard . 2>/dev/null || find . -type f | sed 's|^\./||')
[ -z "$FILES" ] && FILES=$(find . -type f -not -path './bak/*' -not -path '*/__pycache__/*' | sed 's|^\./||')
echo "== Dateien, die veroeffentlicht wuerden: $(echo "$FILES" | wc -l)"
chk() { echo; echo "== $1"; echo "$FILES" | xargs -d '\n' grep -InE "$2" 2>/dev/null | cut -c1-150 | head -${3:-15}; }
chk "Passwort/Token/Schluessel" "(passw|token|secret|api[_-]?key|BEGIN (RSA|OPENSSH))" 15
chk "E-Mail-Adressen" "[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.(com|de|net|org)" 10
chk "Heimatpfade" "/home/[a-z0-9_]+/" 15
chk "interne IPs" "192\.168\.[0-9]+\.[0-9]+" 30
echo; echo "== grosse Dateien (> 1 MB)"; echo "$FILES" | xargs -d '\n' ls -l 2>/dev/null | awk '$5 > 1000000 {print $5, $9}'
