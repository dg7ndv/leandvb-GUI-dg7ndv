#!/usr/bin/env bash
# install_dienst_pi.sh - RTL-SDR-datvRX-Dienst auf einem Raspberry Pi installieren, aktualisieren, pruefen, entfernen (DG7NDV)
#
# Aufruf:  ./install_dienst_pi.sh [user@host | host] [Optionen]   Standard-Ziel: pi@192.168.20.10 (Hauspi); jede andere IP/Name und jeder Benutzer geht
#                                                                  (ohne "user@" gilt --user, Standard pi). Voraussetzung: Anmeldung per SSH-Schluessel ohne Passwort.
#   (ohne Option)   Quellcode auf den Pi kopieren, Abhaengigkeiten pruefen, systemd-Dienst "datvrx-dienst" anlegen.
#                   Der Dienst wird NICHT aktiviert und NICHT gestartet (kein Start beim Booten, kein Start jetzt).
#                   Ist er schon installiert, ist derselbe Aufruf das UPDATE (laeuft er gerade, wird er neu gestartet).
#   --enable        Dienst fuer den Start beim Booten aktivieren
#   --start         Dienst jetzt starten
#   --key DATEI     anderen SSH-Schluessel benutzen (ssh -i)       --port N   anderer SSH-Port       --user NAME   Benutzer, wenn keiner im Ziel steht
#   --lo MHZ        LNB-Oszillator dieses Ziels (z.B. 9540.054) ) werden in die dienst.conf auf dem Ziel eingetragen - noetig, wenn der Pi
#   --ppm N         Frequenzkorrektur des Sticks               ) an einem anderen LNB/Stick haengt (spaeter dort auch von Hand aenderbar)
#   --mqtt HOST     MQTT-Broker fuer den Dienst (Standard der Vorlage: 192.168.20.10:1883 = Mosquitto auf dem Hauspi), wird in die dienst.conf eingetragen
# Konfiguration auf dem Ziel: ~/datvrx-dienst/RTL-SDR-datvRX-Dienst/dienst.conf (aus dienst.conf.beispiel; wird bei Updates NIE ueberschrieben)
#   --mqtt-prefix P Themen-Prefix in der dienst.conf (%h = Hostname des Pis, Vorlage: qo100/dienst/%h) - je Pi eindeutig, sonst stoeren sich mehrere Pis am Broker
#   --no-apt        fehlende Pakete (python3-numpy, paho-mqtt, websocket-client) nicht per apt nachinstallieren
#   --no-test       den Geschwindigkeitstest nach der Installation weglassen (braucht ffmpeg auf diesem PC, dauert etwa 30 s)
#   --status        Zustand zeigen: Modell/Bitbreite des Ziels (Pi 4/5, 32/64 Bit) mit Bewertung, installierte Version gegen lokale,
#                   Abhaengigkeiten, datvrx (ARM), Stick, Dienst   (aendert auf dem Ziel nichts - auch zum Pruefen anderer Pis)
#   --stop          Dienst auf dem Ziel jetzt beenden (nichts wird installiert; Autostart-Einstellung bleibt)
#   --restart       Dienst auf dem Ziel neu starten (ohne Update); startet auch einen gestoppten Dienst
#   --entfernen     Dienst und Ordner ~/datvrx-dienst auf dem Pi entfernen
# Ordner auf dem Pi: ~/datvrx-dienst  (Quellcode, Einstellungen, Messwerte)   Dienst: datvrx-dienst.service
# datvrx fuer ARM: je nach System des Ziels arm/ (32-Bit, armhf) oder arm64/ (64-Bit). datvrx.generic laeuft auf jedem Pi dieser Architektur;
# datvrx.native (schneller, nur 32-Bit-System auf dem Pi 4 noetig) wird nur auf dem Modell verwendet, fuer das es gebaut wurde (native.model). Ohne eine ARM-Datei startet der Dienst nicht (Bedingung im Dienst),
# weil das datvrx dieses PCs (x86) auf dem Pi nicht laeuft. Selbst bauen: siehe arm/BAUEN.html.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"; PARENT="$(dirname "$HERE")"; DVN="$(dirname "$PARENT")/DATVision"
ZIEL=192.168.20.10; USERN=pi; ENABLE=0; START=0; MQTT=""; MQPRE=""; APT=1; TEST=1; ACTION=install; KEY=""; PORT=""; LO=""; PPM=""
if [ $# -gt 0 ] && [[ $1 != --* ]]; then ZIEL=$1; shift; fi
while [ $# -gt 0 ]; do
    case "$1" in
        --enable) ENABLE=1 ;; --start) START=1 ;; --mqtt) MQTT=$2; shift ;; --mqtt-prefix) MQPRE=$2; shift ;; --no-apt) APT=0 ;; --no-test) TEST=0 ;;
        --key) KEY=$2; shift ;; --port) PORT=$2; shift ;; --user) USERN=$2; shift ;; --lo) LO=$2; shift ;; --ppm) PPM=$2; shift ;;
        --status) ACTION=status ;; --entfernen) ACTION=remove ;; --stop) ACTION=stop ;; --restart) ACTION=restart ;;
        -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unbekannte Option $1"; exit 1 ;;
    esac; shift
done
[[ $ZIEL == *@* ]] || ZIEL="$USERN@$ZIEL"
SSHO=(-o BatchMode=yes -o ConnectTimeout=8); [ -n "$KEY" ] && SSHO+=(-i "$KEY"); [ -n "$PORT" ] && SSHO+=(-p "$PORT")
SSHCMD="ssh ${SSHO[*]}"
R() { ssh "${SSHO[@]}" "$ZIEL" "$@" 2> >(grep -v -i 'quantum\|vulnerable\|upgraded\|openssh.com/pq' >&2); }
if ! R true 2>/dev/null; then
    echo "FEHLER: keine Anmeldung per SSH-Schluessel an $ZIEL moeglich."
    echo "        Schluessel einrichten:  ssh-copy-id ${KEY:+-i $KEY }${PORT:+-p $PORT }$ZIEL   (danach erneut aufrufen)"; exit 1
fi
RHOME=$(R 'echo $HOME'); RDIR="$RHOME/datvrx-dienst"; UNIT=datvrx-dienst.service
FILES=(zapper.py spektrum_stick.py datv_core.py qo100_wacht.py signal_da.py fehlerbericht.py)

ver() { ( cd "$PARENT"; cat "${FILES[@]}" "$DVN/datvrx_rx.sh" RTL-SDR-datvRX-Dienst/datvrx_dienst.py RTL-SDR-datvRX-Dienst/dienst.sh RTL-SDR-datvRX-Dienst/dienst.conf.beispiel | sha256sum | cut -c1-12 ); }
LOCALVER=$(ver)

pi_info() {   # Modell, Bitbreite und Architektur des Ziels ermitteln und bewerten (setzt MODEL ARCH BITS KARCH PIGEN AD)
    ARCH=$(R 'dpkg --print-architecture 2>/dev/null || uname -m'); KARCH=$(R 'uname -m')
    BITS=$(R 'getconf LONG_BIT 2>/dev/null || echo ?'); MODEL=$(R "tr -d '\\0' </proc/device-tree/model 2>/dev/null || true")
    case "$MODEL" in
        *"Raspberry Pi 5"*) PIGEN=5 ;;
        *"Raspberry Pi 4"*|*"Raspberry Pi 400"*|*"Compute Module 4"*) PIGEN=4 ;;
        *"Raspberry Pi 3"*|*"Zero 2"*) PIGEN=3 ;;
        *"Raspberry Pi 2"*) PIGEN=2 ;;
        *"Raspberry Pi"*) PIGEN=1 ;;
        *) PIGEN=0 ;;
    esac
    case "$ARCH" in arm64|aarch64) AD=arm64 ;; armhf|armv7l) AD=arm ;; *) AD="" ;; esac
    echo "Ziel:   ${MODEL:-unbekanntes Geraet}"
    echo "System: ${BITS}-Bit (Paketarchitektur $ARCH, Kernel $KARCH)"
    local v w=""
    case "$PIGEN-$BITS" in
        5-64) v="Raspberry Pi 5 / 64-Bit: unterstuetzt - gemessen mit echter Bake: Echtzeit-Faktor 0,29 (unter 1 = schneller als das Signal)" ;;
        5-32) v="Raspberry Pi 5 mit 32-Bit-System: laeuft (datvrx.generic), aber deutlich langsamer als mit 64-Bit - 64-Bit-Raspberry-Pi-OS empfohlen"; w=1 ;;
        4-32) v="Raspberry Pi 4 / 32-Bit: datvrx laeuft, ist aber NICHT sicher echtzeitfaehig - gemessen: Bake Faktor ca. 1,0-1,1 (grenzwertig), 500-kS-Signal 1,3-1,65 (Pi 5 empfohlen)"; w=1 ;;
        4-64) v="Raspberry Pi 4 / 64-Bit: datvrx laeuft, ist aber NICHT sicher echtzeitfaehig (mit 64-Bit-Datei auf dem Pi 4 gemessen: Bake Faktor ca. 1,0, 500-kS-Signal 1,26) - Pi 5 empfohlen"; w=1 ;;
        3-*|2-*|1-*) v="Raspberry Pi aelter als Pi 4: nicht echtzeitfaehig (schon der Pi 4 ist zu langsam)"; w=1 ;;
        0-*) v="kein Raspberry Pi erkannt - Paketarchitektur $ARCH; datvrx fuer dieses System ist nur bei armhf/arm64 vorhanden"; w=1 ;;
        *) v="Kombination Pi $PIGEN / ${BITS}-Bit nicht geprueft"; w=1 ;;
    esac
    [ -z "$AD" ] && { v="$v (KEIN datvrx fuer Architektur '$ARCH')"; w=1; }
    [ "$KARCH" = armv6l ] && { v="Raspberry Pi 1/Zero (ARMv6): die datvrx-Dateien sind fuer ARMv7 gebaut und laufen hier NICHT"; w=1; }
    echo "${w:+WARNUNG: }$v"
}

if [ "$ACTION" = stop ] || [ "$ACTION" = restart ]; then
    VERB=stop; [ "$ACTION" = restart ] && VERB=restart
    R "sudo systemctl $VERB $UNIT" || { echo "FEHLER: systemctl $VERB auf $ZIEL fehlgeschlagen (sudo ohne Passwort?)"; exit 1; }
    echo "Dienst auf $ZIEL: $(R "systemctl is-active $UNIT" 2>/dev/null || true)"; exit 0
fi

if [ "$ACTION" = remove ]; then
    R "sudo systemctl disable --now $UNIT 2>/dev/null; sudo rm -f /etc/systemd/system/$UNIT; sudo systemctl daemon-reload; rm -rf '$RDIR'"
    echo "entfernt (Dienst + $RDIR auf $ZIEL)"; exit 0
fi

if [ "$ACTION" = status ]; then
    echo "Ziel: $ZIEL   lokale Version: $LOCALVER"
    pi_info
    echo "installiert: $(R "cat '$RDIR/VERSION' 2>/dev/null || echo nicht installiert")"
    R "cd '$RDIR' 2>/dev/null || exit 0
       python3 --version
       python3 -c 'import paho.mqtt.client, websocket; print(\"paho + websocket-client: ok\")' 2>&1 | tail -1
       (python3 -c 'import numpy; print(\"numpy (Pegel): ok\")' 2>/dev/null || PYTHONPATH=/usr/lib/python3/dist-packages python3 -c 'import numpy; print(\"numpy (Pegel): ok, nur mit PYTHONPATH (Dienst setzt es)\")' 2>/dev/null || echo 'numpy (Pegel): FEHLT')
       [ -x datvrx ] && echo 'datvrx (ARM): vorhanden' || echo 'datvrx (ARM): FEHLT - Dienst startet erst damit'
       command -v rtl_sdr >/dev/null && echo 'rtl_sdr: ok' || echo 'rtl_sdr: FEHLT'
       lsusb | grep -i -E '0bda:28(38|32)' || echo 'RTL-Stick am Bus: keiner'
       grep -E '^(lo_mhz|ppm|verstaerkung_db|stick_nr|mqtt) *=' RTL-SDR-datvRX-Dienst/dienst.conf 2>/dev/null | sed 's/  */ /g; s/ *#.*//' | tr '\\n' ';'; echo
       echo \"Dienst: \$(systemctl is-active $UNIT) / beim Booten: \$(systemctl is-enabled $UNIT 2>&1)\""
    [ "$(R "cat '$RDIR/VERSION' 2>/dev/null | head -1")" = "$LOCALVER" ] && echo "Stand: aktuell" || echo "Stand: UPDATE verfuegbar (./install_dienst_pi.sh)"
    exit 0
fi

echo "== Installation auf $ZIEL ($RDIR) =="
S=$(mktemp -d); trap 'rm -rf "$S"' EXIT
mkdir -p "$S/RTL-SDR-datvRX-Dienst"
for f in "${FILES[@]}"; do cp "$PARENT/$f" "$S/"; done
cp "$DVN/datvrx_rx.sh" "$S/datvrx_rx.sh"                                  # echte Datei (auf dem PC ist es ein Link)
cp "$HERE/datvrx_dienst.py" "$HERE/dienst.sh" "$HERE/dienst.conf.beispiel" "$S/RTL-SDR-datvRX-Dienst/"
# datvrx fuer die Architektur des Ziels: armhf (32-Bit-System) -> arm/, arm64 (64-Bit-System) -> arm64/
pi_info
if [ -n "$AD" ]; then
    [ -f "$HERE/$AD/datvrx.generic" ] && cp "$HERE/$AD/datvrx.generic" "$S/datvrx.generic" && echo "datvrx.generic ($AD) wird mitkopiert"
    # schnelle Variante nur fuer das Modell, auf dem sie gebaut und getestet wurde (arm/native.model), sonst nur die allgemeine
    if [ -f "$HERE/$AD/datvrx.native" ] && [ -f "$HERE/$AD/native.model" ] && [[ "$MODEL" == *"$(cat "$HERE/$AD/native.model")"* ]]; then
        cp "$HERE/$AD/datvrx.native" "$S/datvrx.native" && echo "datvrx.native ($AD, $(cat "$HERE/$AD/native.model")) wird mitkopiert"; fi
    [ -f "$HERE/$AD/dvbstool" ] && cp "$HERE/$AD/dvbstool" "$S/dvbstool"
else echo "FEHLER: kein datvrx fuer Architektur '$ARCH' (nur armhf = 32-Bit und arm64 = 64-Bit)"; exit 1; fi
printf '%s\n%s  von %s\n' "$LOCALVER" "$(date '+%d.%m.%Y %H:%M')" "$(hostname)" > "$S/VERSION"
if [ "$TEST" = 1 ] && command -v ffmpeg >/dev/null; then
    ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=640x360:rate=25 -f lavfi -i sine=frequency=1000 -t 8 -c:v libx264 -preset veryfast -b:v 500k -maxrate 500k \
        -bufsize 500k -g 25 -c:a aac -b:a 32k -f mpegts -muxrate 1000k "$S/selftest.ts" 2>/dev/null || rm -f "$S/selftest.ts"
fi
R "mkdir -p '$RDIR'"
rsync -a -e "$SSHCMD" --exclude '*.json' --exclude 'dienst.conf' "$S/" "$ZIEL:$RDIR/" 2> >(grep -v -i 'quantum\|vulnerable\|upgraded\|openssh.com/pq' >&2)
R "cd '$RDIR' && chmod +x *.py *.sh RTL-SDR-datvRX-Dienst/*.py RTL-SDR-datvRX-Dienst/*.sh; for f in datvrx datvrx.native datvrx.generic dvbstool; do [ -f \$f ] && chmod +x \$f; done; true"
# datvrx fuer die CPU des Ziels waehlen: schnelle Variante (native, Pi 4) wenn sie dort laeuft, sonst die allgemeine (generic)
R "cd '$RDIR'; for v in datvrx.native datvrx.generic; do if [ -f \$v ] && ./\$v --help 2>&1 | grep -q usage; then cp -f \$v datvrx && echo \"datvrx: \$v laeuft auf diesem Geraet -> wird verwendet\" && break; else [ -f \$v ] && echo \"datvrx: \$v laeuft hier nicht\"; fi; done; true"

if [ "$TEST" = 1 ]; then
    echo "== Geschwindigkeitstest auf dem Ziel (simuliertes DVB-S2: 1 MS/s, QPSK 2/3, 8 s Signal; echte Signale brauchen etwa das 2- bis 3-fache) =="
    if [ ! -f "$S/selftest.ts" ]; then echo "(uebersprungen: ffmpeg fehlt auf diesem PC fuer das Testvideo)"
    else
        BENCH=$(R "cd '$RDIR' && bash -s" <<'EOS'
T=$(mktemp -d)
./dvbstool gen $T/sig.cs8 --secs 8 --rate 2.4 --std s2 --mod qpsk --fec 2/3 --rs 1.0 --roll 0.25 --pilots --snr 8 --cfo -35000 --ts selftest.ts --format cs8 >/dev/null 2>&1 || { echo "BENCH -1 0"; rm -rf $T selftest.ts; exit 0; }
s=$(date +%s.%N); ./datvrx $T/sig.cs8 --rate 2.4 --format cs8 -o $T/out.ts >$T/log 2>&1; e=$(date +%s.%N)
python3 -c "print('BENCH %.2f %d' % (($e-$s)/8.0, $(grep -c 'TS lock' $T/log)))"
rm -rf $T selftest.ts
EOS
)
        F=$(echo "$BENCH" | awk '/^BENCH /{print $2}'); L=$(echo "$BENCH" | awk '/^BENCH /{print $3}')
        if [ -z "$F" ] || [ "$F" = "-1" ]; then echo "WARNUNG: Test nicht moeglich (dvbstool/datvrx laeuft auf dem Ziel nicht?)"
        elif [ "${L:-0}" = 0 ]; then echo "FEHLER: datvrx lockt auf dem Testsignal nicht - Datei/Architektur pruefen"
        else
            # Eichung an Messungen mit echten Signalen (Aufnahmen mit Lock): Bake ~ 1,45 x Testfaktor (Pi 5: 0,21 -> 0,29 gemessen; Pi 4: 0,74 -> 1,07 gemessen), schmale Signale ~ 2,2 x Testfaktor (Pi 4: 0,74 -> 1,65 gemessen)
            V=$(awk -v f="$F" 'BEGIN{ b=f*1.45; n=f*2.2; if (b<=0.9) printf "echtzeitfaehig auch fuer die Bake (geschaetzter Faktor fuer die Bake %.2f)", b; else if (n<=0.9) printf "nur schmale Signale in Echtzeit (geschaetzt %.2f), die Bake nicht (geschaetzt %.2f)", n, b; else printf "NICHT echtzeitfaehig (geschaetzter Faktor Bake %.2f, schmale Signale %.2f): Daten gehen verloren - schnelleren Pi (Pi 5) verwenden", b, n }')
            echo "Testsignal-Faktor $F auf $MODEL -> $V"
        fi
    fi
fi

echo "== Abhaengigkeiten =="
if ! R "python3 -c 'import paho.mqtt.client, websocket' 2>/dev/null"; then
    if [ "$APT" = 1 ]; then echo "paho-mqtt/websocket-client fehlen - installiere per apt (python3-paho-mqtt python3-websocket) ..."
        R "sudo -n apt-get install -y python3-paho-mqtt python3-websocket >/dev/null 2>&1 && echo 'paho/websocket: installiert' || echo 'paho/websocket: Installation fehlgeschlagen (pip3 install --user paho-mqtt websocket-client)'"
    else echo "paho-mqtt/websocket-client fehlen - auf dem Ziel: sudo apt install python3-paho-mqtt python3-websocket"; fi
else echo "paho + websocket-client: ok"; fi
PYENV=""
numpy_check() {                                   # 0 = normal nutzbar, 1 = nur ueber den System-Suchpfad, 2 = gar nicht
    if R "python3 -c 'import numpy' 2>/dev/null"; then return 0
    elif R "PYTHONPATH=/usr/lib/python3/dist-packages python3 -c 'import numpy' 2>/dev/null"; then return 1; fi
    return 2
}
NS=0; numpy_check || NS=$?
if [ "$NS" = 2 ] && [ "$APT" = 1 ]; then
    echo "python3-numpy fehlt (Pegelmessung) - installiere per apt ..."
    R "sudo -n apt-get install -y python3-numpy >/dev/null 2>&1 && echo 'numpy: installiert' || echo 'numpy: Installation fehlgeschlagen'"
    NS=0; numpy_check || NS=$?
fi
case "$NS" in
    0) echo "numpy: ok" ;;
    1) echo "numpy: nur ueber den System-Suchpfad nutzbar (ein defektes pip-numpy in ~/.local verdeckt das apt-Paket) -> der Dienst bekommt PYTHONPATH=/usr/lib/python3/dist-packages"
       PYENV="Environment=PYTHONPATH=/usr/lib/python3/dist-packages" ;;
    *) echo "numpy fehlt (Pegel ohne Wert) - auf dem Ziel: sudo apt install python3-numpy" ;;
esac
R "command -v rtl_sdr >/dev/null && echo 'rtl_sdr: ok' || echo 'rtl_sdr FEHLT: sudo apt install rtl-sdr'"
R "[ -x '$RDIR/datvrx' ] && echo 'datvrx (ARM): vorhanden' || echo 'datvrx (ARM): FEHLT - Dienst startet erst, wenn $RDIR/datvrx existiert (aarch64-Build)'"

echo "== Pruefung auf dem Pi (Syntax und Import-Selbsttest) =="
R "cd '$RDIR' && python3 -m py_compile zapper.py spektrum_stick.py datv_core.py qo100_wacht.py signal_da.py fehlerbericht.py RTL-SDR-datvRX-Dienst/datvrx_dienst.py && echo 'Syntax: ok' ; find . -name __pycache__ -prune -exec rm -rf {} +
   python3 -c \"import sys; sys.path.insert(0,'.'); sys.argv=['z','--dvx-dir','.']; import zapper; print('Import zapper/datv_core/qo100_wacht: ok')\" 2>&1 | tail -2; find . -name __pycache__ -prune -exec rm -rf {} +"

echo "== Konfiguration (dienst.conf) =="
CONF="$RDIR/RTL-SDR-datvRX-Dienst/dienst.conf"
R "[ -f '$CONF' ] && echo 'dienst.conf vorhanden - bleibt unveraendert (nur angegebene Optionen werden eingetragen)' || { cp '$RDIR/RTL-SDR-datvRX-Dienst/dienst.conf.beispiel' '$CONF' && echo 'dienst.conf aus der Vorlage angelegt'; }"
if [ -n "$LO$PPM$MQTT$MQPRE" ]; then
    R "python3 - '$CONF' '$LO' '$PPM' '$MQTT' '$MQPRE' <<'PYX'
import re, sys
p, lo, ppm, mq, mp = sys.argv[1:6]
t = open(p).read()
def setkey(t, k, v):
    return re.sub(r'(?m)^(%s\s*=)[^#;\n]*' % k, lambda m: '%s %s ' % (m.group(1), v), t, count=1)
if lo:  t = setkey(t, 'lo_mhz', lo)
if ppm: t = setkey(t, 'ppm', ppm)
if mq:  t = setkey(t, 'mqtt', mq)
if mp:  t = setkey(t, 'mqtt_prefix', mp)
open(p, 'w').write(t)
print('eingetragen: ' + ', '.join(x for x in (('lo_mhz=' + lo) if lo else '', ('ppm=' + ppm) if ppm else '', ('mqtt=' + mq) if mq else '', ('mqtt_prefix=' + mp) if mp else '') if x))
PYX"
fi
R "grep -E '^(lo_mhz|ppm|verstaerkung_db|stick_nr|mqtt|mqtt_prefix|http_port) *=' '$CONF' | sed 's/  */ /g; s/ *#.*//' | tr '\n' ';'; echo"
[ -z "$LO" ] && echo "(LO/ppm stehen noch auf den Werten der Vorlage - fuer dieses LNB/diesen Stick in der dienst.conf anpassen oder mit --lo MHZ --ppm N setzen)"

echo "== systemd-Dienst $UNIT =="
UNITTXT="[Unit]
Description=RTL-SDR-datvRX-Dienst (QO-100 scannen und dekodieren, Zapper, MQTT) DG7NDV
After=network-online.target
Wants=network-online.target
ConditionPathExists=$RDIR/datvrx

[Service]
Type=simple
User=$(R 'id -un')
WorkingDirectory=$RDIR/RTL-SDR-datvRX-Dienst
ExecStart=/usr/bin/python3 -u $RDIR/RTL-SDR-datvRX-Dienst/datvrx_dienst.py --quiet
Restart=on-failure
RestartSec=15
KillMode=control-group
$PYENV

[Install]
WantedBy=multi-user.target"
WAS_ACTIVE=$(R "systemctl is-active $UNIT 2>/dev/null || true")
printf '%s\n' "$UNITTXT" | R "sudo tee /etc/systemd/system/$UNIT >/dev/null && sudo systemctl daemon-reload"
[ "$ENABLE" = 1 ] && R "sudo systemctl enable $UNIT 2>&1 | tail -1"
if [ "$START" = 1 ]; then R "sudo systemctl restart $UNIT; sleep 3; systemctl is-active $UNIT"   # restart: startet auch neu, wenn er schon lief (sonst bliebe nach einem Update der alte Code aktiv)
elif [ "$WAS_ACTIVE" = active ]; then echo "Dienst lief -> wird fuer das Update neu gestartet"; R "sudo systemctl restart $UNIT; sleep 3; systemctl is-active $UNIT"
else echo "Dienst installiert, NICHT gestartet (starten: ssh $ZIEL 'sudo systemctl start $UNIT'  oder  ./install_dienst_pi.sh --start)"; fi
echo "Version $LOCALVER installiert.  Pruefen: ./install_dienst_pi.sh --status"
