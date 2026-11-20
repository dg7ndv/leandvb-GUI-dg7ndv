#!/bin/sh
# rtl_dienst_pi.sh HOST start|stop - datvrx-dienst auf einem Pi per ssh starten/beenden, Ergebnis als Desktop-Meldung (fuer die Desktop-Symbole)
H="${1:?Host}"; A="${2:?start|stop}"
if ssh -o BatchMode=yes -o ConnectTimeout=8 "pi@$H" "sudo systemctl $A datvrx-dienst"; then
    notify-send "RTL-Dienst $H" "$A ok"
else
    notify-send -u critical "RTL-Dienst $H" "$A fehlgeschlagen (ssh/sudo?)"
fi
