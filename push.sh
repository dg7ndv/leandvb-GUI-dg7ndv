#!/bin/bash
# push.sh - Änderungen im leandvb-GUI Repo committen und zu GitHub pushen
set -e

cd "$(dirname "$0")"

echo "=== Git Status ==="
git status
echo

echo "=== Diff (getrackte Dateien) ==="
git diff
echo

# Nur Änderungen an bereits getrackten Dateien aufnehmen (keine neuen/untracked Dateien).
git add -u

# Zusätzlich übergebene Dateien explizit aufnehmen: ./push.sh datei1 datei2 ...
if [ "$#" -gt 0 ]; then
    git add "$@"
fi

if git diff --cached --quiet; then
    echo "Keine Änderungen zum Committen."
    exit 0
fi

echo "=== Zu committende Änderungen ==="
git status --short

read -p "Commit-Nachricht: " msg
if [ -z "$msg" ]; then
    echo "Keine Commit-Nachricht angegeben, breche ab."
    exit 1
fi

git commit -m "$msg"
git push

echo
echo "=== Fertig ==="
git log --oneline -5
git status
