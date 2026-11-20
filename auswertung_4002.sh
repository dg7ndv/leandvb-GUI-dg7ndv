#!/bin/bash
# Aufruf: ./auswertung_4002.sh [-nostatus | -status [all]] [-text all] [-lo LO_KHZ] [-log 0] [-h|--help]
#   -nostatus:    keine Overlay-Zeile im VLC-Bild einblenden.
#   -status:      Overlay zeigt nur Service (Rufzeichen) + Provider.
#   -status2:     wie -status, zusaetzlich mit Codec (H.264/H.265/H.266/MPEG-2).
#   -status all:  Overlay zeigt alles (Provider/Name/Carrier Freq/Symbol Rate/MODCOD/MER).
#   ohne Parameter: reduziert - Overlay zeigt nur Service (Rufzeichen) + MER + HF Frequenz
#                   (kein Provider; HF Frequenz/Bake/Kanal benoetigen -lo).
#   -text all:    Terminal-Dashboard zeigt zusaetzlich LNB Voltage + LNB Polarisation.
#                 Ohne -text all: Dashboard ohne diese beiden Zeilen (Standard).
#   -lo [LO_KHZ]: LO-Frequenz des LNB in kHz (ohne Wert: Standard 9540000 kHz). Zeigt
#                 zusaetzlich die reale HF-Frequenz (Carrier Freq + LO, in MHz,
#                 1 Nachkommastelle) als "Bake"-Zeile in Dashboard und Bild-Overlay,
#                 sowie den passenden Kanal aus dem Kanalplan.
#   -log [0|1]:   CSV-Log ist standardmaessig AN (schreibt bei jedem Rufzeichen-Wechsel
#                 (Service) eine Zeile in log_4002.csv (im Script-Verzeichnis):
#                 Datum, Uhrzeit, Rufzeichen und alle bekannten Parameter als Spalten).
#                 -log 0 schaltet das Log ab. -log oder -log 1 schaltet es (weiterhin) an.
#   -h, --help:   diese Hilfe anzeigen und beenden.
#
# Waehrend das Script laeuft (im Terminal), stehen zwei Tasten zur Verfuegung:
#   Taste s:      loest per VLC-Telnet einen Snapshot des aktuellen Bildes aus.
#   Taste v:      startet/stoppt (toggle) eine Aufnahme von udp://@230.0.0.3:1234
#                 per cvlc nach .../aufnahme_<Rufzeichen>_<ServiceName>_<SymbolRate>Sps_<Datum_Uhrzeit>.ts
#   Tasten 1-4:   schalten das Bild-Overlay im laufenden Betrieb um (wie -nostatus/-status):
#                 1 = aus (-nostatus)   2 = Standard (ohne Parameter)
#                 3 = Service+Provider (-status)   4 = alles (-status all)
#   Taste r:      erzeugt eine Report-Zeile im Format
#                 "SigReport: <Service>/<Provider> - D<x,x> (<MER> dB) - <SymbolRate> Ks - <Frequenz>",
#                 zeigt sie unten im Dashboard an, schreibt sie nach /tmp/vlc_report_230_0_0_3.txt
#                 und kopiert sie (falls xclip/xsel/wl-copy vorhanden) in die Zwischenablage.
#                 Der D-Wert (MER minus Schwelle) wird eingetragen (REPORT_D=1 weiter unten; 0 = leer lassen).
#   Codec:        wird aus dem ES-Type-Feld erkannt (H.264/AVC, H.265/HEVC, H.266/VVC, MPEG-2)
#                 und im Terminal bei "ES Type:" sowie im Bild-Overlay bei -status all angezeigt.
#                 Sobald H.266/VVC decodiert wird, schreibt das Script zusaetzlich eine Zeile
#                 (Datum, Uhrzeit, Service, Provider) nach log_h266_4002.csv (im Script-Verzeichnis)
#                 und gibt per h266_tone_230_0_0_3.sh einen Ton aus (3x, 10 Sekunden Abstand).
#   Aufloesung:   wird per VLC-Telnet ("info") abgefragt und im Terminal als letzte Zeile
#                 unter "ES Type:" angezeigt (Abfrage alle 5s im Hintergrund, ungetestetes
#                 Textformat - bleibt leer, falls das Auslesen nicht funktioniert).
STATUS_MODE="default"
TEXT_MODE="default"
LO_KHZ=""
LOG_MODE=1
PORT="4002"
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            echo "Verwendung: $0 [-nostatus | -status [all]] [-text all] [-lo LO_KHZ] [-log 0] [-port PORT] [-h|--help]"
            echo ""
            echo "  -nostatus     kein Bild-Overlay (Overlay-Datei bleibt leer)."
            echo "  -status       Bild-Overlay zeigt nur Service (Rufzeichen) + Provider."
            echo "  -status2      wie -status, zusaetzlich mit Codec (H.264/H.265/H.266/MPEG-2)."
            echo "  -status all   Bild-Overlay zeigt alles (Provider/Name/Carrier Freq/"
            echo "                Symbol Rate/MODCOD/MER)."
            echo "  -text all     Terminal-Dashboard zeigt zusaetzlich LNB Voltage +"
            echo "                LNB Polarisation. Ohne -text all: ohne diese Zeilen."
            echo "  -lo [LO_KHZ]  LO-Frequenz des LNB in kHz, z.B. -lo 9540000."
            echo "                Ohne Wert (nur -lo) wird 9540000 kHz voreingestellt verwendet."
            echo "                Zeigt zusaetzlich HF-Frequenz (Carrier Freq + LO) in MHz"
            echo "                (1 Nachkommastelle) als Bake-Zeile in Dashboard + Overlay,"
            echo "                sowie den passenden Kanal aus dem Kanalplan."
            echo "  -log [0|1]    CSV-Log ist standardmaessig AN: schreibt bei jedem"
            echo "                Rufzeichen-Wechsel (Service) eine Zeile in"
            echo "                log_4002.csv (im Script-Verzeichnis): Datum, Uhrzeit,"
            echo "                Rufzeichen und alle bekannten Parameter als Spalten."
            echo "                Datei wird angelegt, Header nur einmal."
            echo "                -log 0 schaltet das CSV-Log ab."
            echo "  -port PORT    UDP-Port, auf dem auf die Longmynd-Statuswerte gelauscht wird"
            echo "                (Default 4002, spaeter aenderbar/mehrere Instanzen moeglich)."
            echo "  -h, --help    diese Hilfe anzeigen und beenden."
            echo ""
            echo "Waehrend das Script im Terminal laeuft:"
            echo "  Taste s       Snapshot des aktuellen VLC-Bildes per Telnet ausloesen."
            echo "  Taste v       Aufnahme von udp://@230.0.0.3:1234 per cvlc starten/stoppen"
            echo "                (Toggle). Dateiname: aufnahme_<Rufzeichen>_<ServiceName>_"
            echo "                <SymbolRate>Sps_<Datum_Uhrzeit>.ts im Script-Verzeichnis."
            echo "  Tasten 1-4    Bild-Overlay umschalten: 1 = aus (-nostatus), 2 = Standard,"
            echo "                3 = Service+Provider (-status), 4 = alles (-status all)."
            echo "  Taste r       Report-Zeile erzeugen (SigReport: Service/Provider - D (MER dB) -"
            echo "                SymbolRate Ks - Frequenz), im Dashboard anzeigen + Zwischenablage."
            echo ""
            echo "Ohne Parameter: normale Auswertung, Bild-Overlay reduziert (Service/MER/HF Frequenz, ohne Provider)."
            exit 0
            ;;
        -nostatus)
            STATUS_MODE="off"
            shift
            ;;
        -status)
            if [ "$2" = "all" ]; then
                STATUS_MODE="full"
                shift 2
            else
                STATUS_MODE="reduced"
                shift
            fi
            ;;
        -status2)
            STATUS_MODE="reduced2"
            shift
            ;;
        -text)
            if [ "$2" = "all" ]; then
                TEXT_MODE="full"
                shift 2
            else
                TEXT_MODE="full"
                shift
            fi
            ;;
        -lo)
            case "$2" in
                ''|-*)
                    LO_KHZ="9540000"
                    shift
                    ;;
                *)
                    LO_KHZ="$2"
                    shift 2
                    ;;
            esac
            ;;
        -log)
            case "$2" in
                0)
                    LOG_MODE=0
                    shift 2
                    ;;
                1)
                    LOG_MODE=1
                    shift 2
                    ;;
                *)
                    LOG_MODE=1
                    shift
                    ;;
            esac
            ;;
        -port)
            case "$2" in
                ''|-*)
                    shift
                    ;;
                *)
                    PORT="$2"
                    shift 2
                    ;;
            esac
            ;;
        *)
            shift
            ;;
    esac
done

LOG_FILE="$(dirname "$0")/log_4002.csv"
if [ "$LOG_MODE" = "1" ] && [ ! -f "$LOG_FILE" ]; then
    echo "Datum,Uhrzeit,Rufzeichen,Service Name,State,LNA Gain,Puncture Rate,Carrier Freq (kHz),Symbol Rate (S/s),MODCOD,Short Frames,Pilot Symbols,Viterbi Err Rate (%),Bit Error Rate (%),MER (dB),LDPC Errors,BCH Errors,BCH Uncorrected,Null Ratio (%),ES PID,ES Type,LNB Voltage,LNB Polarisation,HF Frequenz (MHz),Kanal (MHz),Kanal (kS),Uplink (MHz)" > "$LOG_FILE"
fi

# Separates Log: eine Zeile (Datum, Uhrzeit, Service, Provider), sobald H.266/VVC decodiert wird.
H266_LOG_FILE="$(dirname "$0")/log_h266_4002.csv"
if [ ! -f "$H266_LOG_FILE" ]; then
    echo "Datum,Uhrzeit,Service,Provider" > "$H266_LOG_FILE"
fi

echo "=================================================="
echo " Starte Longmynd Status-Auswertung auf Port $PORT"
case "$STATUS_MODE" in
    off)     echo " Bild-Overlay: AUS (-nostatus aktiv)" ;;
    reduced) echo " Bild-Overlay: NUR Service + Provider (-status aktiv)" ;;
    reduced2) echo " Bild-Overlay: Service + Provider + Codec (-status2 aktiv)" ;;
    full)    echo " Bild-Overlay: ALLES (-status all aktiv)" ;;
    default) echo " Bild-Overlay: Service + MER + HF Frequenz, ohne Provider (Standard ohne Parameter)" ;;
esac
case "$TEXT_MODE" in
    full)    echo " Terminal-Dashboard: mit LNB Voltage/Polarisation (-text all aktiv)" ;;
    default) echo " Terminal-Dashboard: ohne LNB Voltage/Polarisation (Standard ohne Parameter)" ;;
esac
if [ -n "$LO_KHZ" ]; then
    echo " HF-Frequenz-Anzeige: AN (LO = $LO_KHZ kHz)"
fi
if [ "$LOG_MODE" = "1" ]; then
    echo " CSV-Log: AN bei Rufzeichen-Wechsel ($LOG_FILE)"
else
    echo " CSV-Log: AUS (-log 0 aktiv)"
fi
echo " Taste 's': VLC-Snapshot ausloesen"
echo " Taste 'v': Aufnahme (udp://@230.0.0.3:1234) starten/stoppen"
echo " Tasten 1-4: Overlay umschalten (1=aus 2=Standard 3=Service+Prov. 4=Alles)"
echo " Taste 'r': Report-Zeile erzeugen (SigReport: ...) + Zwischenablage"
echo " Beenden mit STRG + C"
echo "=================================================="
sleep 1

# PID-Datei des ueberwachten VLC (wird von watchdog_230_0_0_3.sh geschrieben).
# Fuer den anderen Stream einfach auf /tmp/vlc_230_0_0_4.pid aendern
# (und watchdog_230_0_0_4.sh entsprechend um das PIDFILE-Schreiben ergaenzen).
VLC_PIDFILE="/tmp/vlc_230_0_0_3.pid"

# Datei, aus der VLC per marq-Filter die Overlay-Zeile (Provider/Name/Symbol Rate) liest.
# Muss zum --marq-file in watchdog_230_0_0_3.sh passen.
MARQUEE_FILE="/tmp/vlc_marquee_230_0_0_3.txt"

# Script, das per VLC-Telnet-Interface den Stream neu laedt (stop/remove/enqueue/play),
# ohne den VLC-Prozess zu killen.
RELOAD_SCRIPT="$(dirname "$0")/reload_vlc_230_0_0_3.sh"

# Script, das per VLC-Telnet-Interface einen Snapshot des aktuellen Bildes ausloest.
SNAPSHOT_SCRIPT="$(dirname "$0")/snapshot_vlc_230_0_0_3.sh"

# Datei, in die awk laufend die aktuellen Kernwerte schreibt (Rufzeichen|ServiceName|SymbolRate),
# damit die Tastatur-Abfrage (Taste v) unabhaengig vom -status/-nostatus-Overlay den
# aktuellen Stand fuer den Aufnahme-Dateinamen kennt.
STATE_FILE="/tmp/vlc_state_230_0_0_3.txt"

# Datei, ueber die die Tasten 1-4 den Overlay-Modus an awk weiterreichen
# (off | default | reduced | full). Startwert = Kommandozeilenparameter.
MODE_FILE="/tmp/vlc_status_mode_230_0_0_3.txt"
printf '%s\n' "$STATUS_MODE" > "$MODE_FILE"

# Datei, in die ein Hintergrund-Poller die zuletzt per VLC-Telnet ("info") ausgelesene
# Videoaufloesung schreibt (z.B. "1280x720"). Bleibt leer, wenn das Auslesen fehlschlaegt.
RES_FILE="/tmp/vlc_res_230_0_0_3.txt"
: > "$RES_FILE"
# Zugang zum VLC-Telnet-Interface (siehe watchdog_230_0_0_3.sh), nur lesend genutzt (Befehl "info").
RES_TELNET_HOST="127.0.0.1"
RES_TELNET_PORT=4212
RES_TELNET_PASS="admin"
RES_POLL_INTERVAL=5

# Script, das bei erkanntem H.266/VVC einen Ton ausgibt (3x, 10 Sekunden Abstand).
H266_SOUND_SCRIPT="$(dirname "$0")/h266_tone_230_0_0_3.sh"

# Taste r (Report): Anforderung an awk per Datei, Ergebnis wieder als Datei + Zwischenablage.
REPORT_REQ_FILE="/tmp/vlc_report_req_230_0_0_3.txt"
REPORT_FILE="/tmp/vlc_report_230_0_0_3.txt"
: > "$REPORT_REQ_FILE"
# 1 = D-Wert (MER minus Decodierschwelle) im Report eintragen (Standard), 0 = D leer lassen
REPORT_D=1
# Text, der hinten an die Report-Zeile angehaengt wird (leer = nichts anhaengen)
REPORT_SUFFIX="1.8m Dish"
CLIP_CMD=""
if command -v xclip >/dev/null 2>&1; then
    CLIP_CMD="xclip -selection clipboard"
elif command -v xsel >/dev/null 2>&1; then
    CLIP_CMD="xsel --clipboard --input"
elif command -v wl-copy >/dev/null 2>&1; then
    CLIP_CMD="wl-copy"
fi

# PID-Datei der per Taste "v" gestarteten Aufnahme (cvlc), damit sie erkannt/gestoppt
# und beim Script-Ende sauber mitbeendet werden kann.
RECORDING_PIDFILE="/tmp/vlc_recording_230_0_0_3.pid"

# Kommando-Datei fuer externe Ausloeser (z.B. Knoepfe in der leandvb-GUI): ein Schreiben von
# "s" oder "v" in diese Datei loest dieselbe Aktion aus wie die Taste s/v im Terminal.
# Portspezifisch benannt, damit mehrere Instanzen (versch. -port) sich nicht in die Quere kommen.
CMD_FILE="/tmp/vlc_cmd_${PORT}.txt"
: > "$CMD_FILE"

# Mindestabstand in Sekunden zwischen zwei automatischen Aktionen (Debounce je Trigger),
# damit ein flatterndes Signal nicht im Sekundentakt reagiert.
MIN_RESTART_INTERVAL=10
MIN_RELOAD_INTERVAL=15

# Relative Abweichung der Symbol Rate ("Datenrate"), ab der reagiert wird (0.10 = 10%)
SYMRATE_THRESHOLD=0.10

# Bekannte Referenzfrequenz der A71A-Bake in MHz. Wird nur benutzt, wenn -lo gesetzt ist
# und der aktuell empfangene Service Provider "A71A" ist: die Differenz zwischen der
# berechneten HF-Frequenz und dieser Referenz zeigt den aktuellen LO-Drift (in kHz).
REF_A71A_MHZ=10491.500

# Konstanter Frequenzversatz des QO-100 NB-Transponders: Downlink - Uplink (in MHz).
# Verifiziert anhand der kompletten Kanalplan-Tabellen (alle vier Symbolraten): ueberall exakt 8089.5 MHz.
UPLINK_OFFSET_MHZ=8089.5

# Tastatur-Abfrage im Hintergrund:
#   Taste s -> loest per VLC-Telnet einen Snapshot des aktuellen Bildes aus.
#   Taste v -> startet/stoppt (toggle) eine Aufnahme von udp://@230.0.0.3:1234 per cvlc.
# Liest bewusst von /dev/tty (nicht von stdin), da stdin fuer die nc|awk-Pipeline
# unten gebraucht wird. Ohne Terminal (z.B. als Dienst gestartet) wird das uebersprungen.
# Snapshot und Aufnahme-Toggle als Funktionen, damit Taste 's'/'v' im Terminal UND die
# Kommando-Datei (fuer externe Ausloeser wie GUI-Knoepfe) dieselbe Logik benutzen.
do_snapshot() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [auswertung_4002] $1, loese VLC-Snapshot aus..." > "/dev/stderr"
    "$SNAPSHOT_SCRIPT" >/dev/null 2>&1 &
}
do_toggle_recording() {
    if [ -f "$RECORDING_PIDFILE" ] && kill -0 "$(cat "$RECORDING_PIDFILE" 2>/dev/null)" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - [auswertung_4002] $1, stoppe Aufnahme..." > "/dev/stderr"
        kill -TERM "$(cat "$RECORDING_PIDFILE")" 2>/dev/null
        rm -f "$RECORDING_PIDFILE"
    else
        PROVIDER=""; SERVICE=""; SYMRATE=""
        if [ -f "$STATE_FILE" ]; then
            IFS='|' read -r PROVIDER SERVICE SYMRATE < "$STATE_FILE"
        fi
        SAFE_PROVIDER=$(printf '%s' "${PROVIDER:-unbekannt}" | tr ' /\\:' '____')
        SAFE_SERVICE=$(printf '%s' "${SERVICE:-unbekannt}" | tr ' /\\:' '____')
        TS=$(date '+%Y%m%d_%H%M%S')
        REC_FILE="$(dirname "$0")/aufnahme_${SAFE_PROVIDER}_${SAFE_SERVICE}_${SYMRATE:-0}Sps_${TS}.ts"
        echo "$(date '+%Y-%m-%d %H:%M:%S') - [auswertung_4002] $1, starte Aufnahme -> $REC_FILE" > "/dev/stderr"
        cvlc --intf dummy --extraintf="" --demux=ts udp://@230.0.0.3:1234 --sout=file/ts:"$REC_FILE" >/tmp/vlc_recording_230_0_0_3.log 2>&1 &
        echo "$!" > "$RECORDING_PIDFILE"
    fi
}

KEY_LISTENER_PID=""
if ( exec 9</dev/tty ) 2>/dev/null; then
    (
        while true; do
            if IFS= read -rsn1 -t 1 key < /dev/tty 2>/dev/null; then
                if [ "$key" = "s" ] || [ "$key" = "S" ]; then
                    do_snapshot "Taste 's' gedrueckt"
                elif [ "$key" = "1" ] || [ "$key" = "2" ] || [ "$key" = "3" ] || [ "$key" = "4" ]; then
                    case "$key" in
                        1) NEW_MODE="off" ;;
                        2) NEW_MODE="default" ;;
                        3) NEW_MODE="reduced" ;;
                        4) NEW_MODE="full" ;;
                    esac
                    printf '%s\n' "$NEW_MODE" > "$MODE_FILE"
                elif [ "$key" = "r" ] || [ "$key" = "R" ]; then
                    echo 1 > "$REPORT_REQ_FILE"
                elif [ "$key" = "v" ] || [ "$key" = "V" ]; then
                    do_toggle_recording "Taste 'v' gedrueckt"
                fi
            fi
        done
    ) &
    KEY_LISTENER_PID=$!
fi

# Kommando-Datei-Poller: laeuft unabhaengig vom Terminal/Tastenlistener (auch ohne echtes
# TTY, z.B. wenn das Script von der GUI gestartet wird) - fuehrt dieselben Aktionen aus wie
# Taste s/v, ausgeloest durch ein Schreiben von "s"/"v" in $CMD_FILE (z.B. von GUI-Knoepfen).
CMD_POLL_PID=""
(
    while true; do
        if [ -s "$CMD_FILE" ]; then
            CMD_KEY=$(head -c1 "$CMD_FILE" 2>/dev/null)
            : > "$CMD_FILE"
            case "$CMD_KEY" in
                s|S) do_snapshot "GUI-Knopf 'Snapshot'" ;;
                v|V) do_toggle_recording "GUI-Knopf 'Aufnahme'" ;;
            esac
        fi
        sleep 0.5
    done
) &
CMD_POLL_PID=$!

# Hintergrund-Poller: fragt periodisch per VLC-Telnet ("info") die Videoaufloesung ab und
# schreibt sie nach RES_FILE. Laeuft unabhaengig vom Terminal/Tastenlistener (auch als Dienst).
# Nur lesend (kein "quit"/"shutdown" an VLC), kurzer Timeout, damit ein haengendes Telnet
# den Poller nicht blockiert.
RES_POLL_PID=""
(
    while true; do
        RES_RAW=$( { sleep 0.2; printf '%s\n' "$RES_TELNET_PASS"; sleep 0.2; printf 'info\n'; sleep 0.4; } \
            | timeout 2 nc -w 2 "$RES_TELNET_HOST" "$RES_TELNET_PORT" 2>/dev/null )
        RES_MATCH=$(printf '%s\n' "$RES_RAW" | grep -io '[0-9][0-9][0-9]*x[0-9][0-9][0-9]*' | head -n1)
        if [ -n "$RES_MATCH" ]; then
            printf '%s\n' "$RES_MATCH" > "$RES_FILE"
        fi
        sleep "$RES_POLL_INTERVAL"
    done
) &
RES_POLL_PID=$!

trap '
    [ -n "$KEY_LISTENER_PID" ] && kill "$KEY_LISTENER_PID" 2>/dev/null
    [ -n "$RES_POLL_PID" ] && kill "$RES_POLL_PID" 2>/dev/null
    [ -n "$CMD_POLL_PID" ] && kill "$CMD_POLL_PID" 2>/dev/null
    rm -f "$CMD_FILE"
    if [ -f "$RECORDING_PIDFILE" ]; then
        kill -TERM "$(cat "$RECORDING_PIDFILE" 2>/dev/null)" 2>/dev/null
        rm -f "$RECORDING_PIDFILE"
    fi
' EXIT INT TERM

nc -lu 192.168.20.204 "$PORT" | awk -F',' \
    -v port="$PORT" \
    -v pidfile="$VLC_PIDFILE" \
    -v reload_script="$RELOAD_SCRIPT" \
    -v min_interval="$MIN_RESTART_INTERVAL" \
    -v min_reload_interval="$MIN_RELOAD_INTERVAL" \
    -v symrate_threshold="$SYMRATE_THRESHOLD" \
    -v marquee_file="$MARQUEE_FILE" \
    -v status_mode="$STATUS_MODE" \
    -v mode_file="$MODE_FILE" \
    -v req_file="$REPORT_REQ_FILE" \
    -v report_file="$REPORT_FILE" \
    -v report_d="$REPORT_D" \
    -v report_suffix="$REPORT_SUFFIX" \
    -v clip_cmd="$CLIP_CMD" \
    -v text_mode="$TEXT_MODE" \
    -v lo_khz="$LO_KHZ" \
    -v ref_a71a_mhz="$REF_A71A_MHZ" \
    -v uplink_offset_mhz="$UPLINK_OFFSET_MHZ" \
    -v log_mode="$LOG_MODE" \
    -v log_file="$LOG_FILE" \
    -v state_file="$STATE_FILE" \
    -v h266_log_file="$H266_LOG_FILE" \
    -v h266_sound_script="$H266_SOUND_SCRIPT" \
    -v res_file="$RES_FILE" \
    -v key_help="${KEY_LISTENER_PID:+1}" '
function fmt_state(v,   states) {
    split("initialising searching found_headers locked_DVB-S locked_DVB-S2", states, " ");
    return states[v+1]
}
function fmt_modcod(v,   modcod) {
    # MODCOD 0 (DummyPL) bzw. leerer Wert -> nichts ausgeben, leer lassen
    if (v == "" || (v+0) == 0) return ""
    split("DummyPL QPSK_1/4 QPSK_1/3 QPSK_2/5 QPSK_1/2 QPSK_3/5 QPSK_2/3 QPSK_3/4 QPSK_4/5 QPSK_5/6 QPSK_8/9 QPSK_9/10 8PSK_3/5 8PSK_2/3 8PSK_3/4 8PSK_5/6 8PSK_8/9 8PSK_9/10 16APSK_2/3 16APSK_3/4 16APSK_4/5 16APSK_5/6 16APSK_8/9 16APSK_9/10 32APSK_3/4 32APSK_4/5 32APSK_5/6 32APSK_8/9 32APSK_9/10", modcod, " ");
    return modcod[v+1]
}
function fmt_estype(v) {
    # MPEG-TS stream_type (ES Type), siehe ISO/IEC 13818-1 / FFmpeg mpegts.h
    if (v == 27) return "H.264/AVC"
    if (v == 36) return "H.265/HEVC"
    if (v == 51) return "H.266/VVC"
    if (v == 1 || v == 2) return "MPEG-2"
    return ""
}
function fmt_estype_short(v,   n) {
    n = fmt_estype(v)
    if (n == "H.264/AVC") return "H264"
    if (n == "H.265/HEVC") return "H265"
    if (n == "H.266/VVC") return "H266"
    if (n == "MPEG-2") return "MPEG2"
    return ""
}
function fmt_thousands(n,   s, out, len, i, cnt) {
    s = sprintf("%d", n)
    len = length(s)
    out = ""
    cnt = 0
    for (i = len; i >= 1; i--) {
        out = substr(s, i, 1) out
        cnt++
        if (cnt % 3 == 0 && i != 1) out = "." out
    }
    return out
}
function hf_mhz_precise() {
    if (lo_khz == "" || !(6 in vals)) return ""
    return ((vals[6]+0) + (lo_khz+0)) / 1000.0
}
function hf_mhz(   raw) {
    raw = hf_mhz_precise()
    if (raw == "") return ""
    return sprintf("%.2f", raw)
}
function carrier_hf_mhz_precise(   eff_lo) {
    if (!(6 in vals)) return ""
    eff_lo = (lo_khz != "" ? lo_khz : 9540000)
    return ((vals[6]+0) + (eff_lo+0)) / 1000.0
}
function carrier_hf_mhz(   raw) {
    raw = carrier_hf_mhz_precise()
    if (raw == "") return ""
    return sprintf("%.1f", raw)
}
function terminal_bake_line(no_dev,   eff_lo, raw, f, drift_mhz, line) {
    if (!(6 in vals)) return ""
    eff_lo = (lo_khz != "" ? lo_khz : 9540000)
    raw = ((vals[6]+0) + (eff_lo+0)) / 1000.0
    f = sprintf("%.2f", raw)
    line = f " MHz"
    if ((13 in vals) && vals[13] == "A71A") {
        line = line " Bake " vals[13]
        if (ref_a71a_mhz != "" && !no_dev) {
            drift_mhz = raw - ref_a71a_mhz
            line = line sprintf(" (Abw. %+.3f MHz)", drift_mhz)
        }
    }
    return line
}
function bake_line(   f, precise, drift_mhz, line) {
    f = hf_mhz()
    if (f == "") return ""
    line = f " MHz"
    if ((13 in vals) && vals[13] == "A71A") {
        line = line " Bake " vals[13]
        if (ref_a71a_mhz != "") {
            precise = hf_mhz_precise()
            drift_mhz = precise - ref_a71a_mhz
            line = line sprintf(" (Abw. %+.3f MHz)", drift_mhz)
            last_drift_mhz = drift_mhz
            have_drift = 1
        }
    }
    return line
}
function corrected_hf_mhz(   raw) {
    raw = hf_mhz_precise()
    if (raw == "") return ""
    if (have_drift) return raw - last_drift_mhz
    return raw
}
function match_symrate_bucket(sr_khz,   buckets, i, best, bestdiff, diff) {
    split("33 66 125 250 333 500 1000 1500", buckets, " ")
    best = ""
    bestdiff = 999999
    for (i = 1; i <= 8; i++) {
        diff = sr_khz - buckets[i]
        if (diff < 0) diff = -diff
        if (diff < bestdiff) { bestdiff = diff; best = buckets[i] }
    }
    if (best == "" || (bestdiff / best) > 0.10) return ""
    return best
}
function match_channel(freq_mhz, bucket,   list, n, i, best, bestdiff, diff, tol) {
    tol = 0.1
    n = 0
    if (bucket == "1000" || bucket == "1500") {
        # Wide DATV: 1000/1500 kS (3 Kanaele)
        n = split("10493.25 10494.75 10496.25", list, " ")
    } else if (bucket == "500" || bucket == "333" || bucket == "250") {
        # Narrow DATV: 500/333/250 kS (14 Kanaele, 500-kHz-Raster)
        n = split("10492.75 10493.25 10493.75 10494.25 10494.75 10495.25 10495.75 10496.25 10496.75 10497.25 10497.75 10498.25 10498.75 10499.25", list, " ")
    } else if (bucket == "125" || bucket == "66" || bucket == "33") {
        # Very Narrow DATV: 125/66/33 kS (27 Kanaele, 250-kHz-Raster)
        n = split("10492.75 10493.00 10493.25 10493.50 10493.75 10494.00 10494.25 10494.50 10494.75 10495.00 10495.25 10495.50 10495.75 10496.00 10496.25 10496.50 10496.75 10497.00 10497.25 10497.50 10497.75 10498.00 10498.25 10498.50 10498.75 10499.00 10499.25", list, " ")
    } else {
        return ""
    }
    best = ""
    bestdiff = 999999
    for (i = 1; i <= n; i++) {
        diff = freq_mhz - (list[i]+0)
        if (diff < 0) diff = -diff
        if (diff < bestdiff) { bestdiff = diff; best = list[i] }
    }
    if (best == "" || bestdiff > tol) return ""
    return sprintf("%.2f", best+0)
}
function channel_line(   freq, bucket, ch, sr_khz, uplink) {
    if (!(9 in vals)) return ""
    freq = corrected_hf_mhz()
    if (freq == "") return ""
    sr_khz = (vals[9]+0) / 1000.0
    bucket = match_symrate_bucket(sr_khz)
    if (bucket == "") return ""
    ch = match_channel(freq, bucket)
    if (ch == "") return ""
    uplink = sprintf("%.2f", (ch+0) - uplink_offset_mhz)
    return ch " MHz (" bucket " kS) / Uplink " uplink " MHz"
}
function terminal_channel_line(plain,   freq, bucket, ch, sr_khz, uplink, dev, c1, c2) {
    if (!(6 in vals)) return ""
    freq = carrier_hf_mhz_precise()
    if (freq == "") return ""
    c1 = (plain ? "" : "\033[32m")
    c2 = (plain ? "" : "\033[0m")
    if ((13 in vals) && vals[13] == "A71A" && ref_a71a_mhz != "") {
        dev = freq - (ref_a71a_mhz+0)
        return sprintf("%.3f", ref_a71a_mhz+0) " MHz (Bake A71A) " c1 "(Abw. " sprintf("%+.3f", dev) " MHz)" c2
    }
    if (!(9 in vals)) return ""
    sr_khz = (vals[9]+0) / 1000.0
    bucket = match_symrate_bucket(sr_khz)
    if (bucket == "") return ""
    ch = match_channel(freq, bucket)
    if (ch == "") return ""
    uplink = sprintf("%.2f", (ch+0) - uplink_offset_mhz)
    dev = freq - (ch+0)
    return ch " MHz (" bucket " kS) / Uplink " uplink " MHz " c1 "(Abw. " sprintf("%+.3f", dev) " MHz)" c2
}
function csv_esc(v) {
    if (v == "") return ""
    if (index(v, ",") > 0 || index(v, "\"") > 0) {
        gsub(/"/, "\"\"", v)
        return "\"" v "\""
    }
    return v
}
function log_h266(   key) {
    # Wird nur bei erkanntem H.266/VVC aufgerufen (siehe Aufrufstellen unten).
    # Schreibt nur bei Wechsel von Service/Provider eine neue Zeile, kein Log pro Zyklus.
    if (!(13 in vals) && !(14 in vals)) return
    key = ((13 in vals) ? vals[13] : "") "|" ((14 in vals) ? vals[14] : "")
    if (key == h266_last_key) return
    h266_last_key = key
    print strftime("%Y-%m-%d") "," strftime("%H:%M:%S") "," \
          csv_esc((13 in vals) ? vals[13] : "") "," csv_esc((14 in vals) ? vals[14] : "") >> h266_log_file
    close(h266_log_file)
    if (h266_sound_script != "") system(h266_sound_script " >/dev/null 2>&1 &")
}
function log_csv(new_provider,   freq, bucket, ch, uplink, sr_khz, row) {
    if (log_mode != "1") return
    freq = corrected_hf_mhz()
    bucket = ""; ch = ""; uplink = ""
    if (9 in vals) {
        sr_khz = (vals[9]+0) / 1000.0
        bucket = match_symrate_bucket(sr_khz)
        if (bucket != "" && freq != "") {
            ch = match_channel(freq, bucket)
            if (ch != "") uplink = sprintf("%.2f", (ch+0) - uplink_offset_mhz)
        }
    }
    row = strftime("%Y-%m-%d") "," strftime("%H:%M:%S") "," csv_esc(new_provider) "," \
          csv_esc((14 in vals) ? vals[14] : "") "," \
          csv_esc((1 in vals) ? fmt_state(vals[1]) : "") "," \
          csv_esc((2 in vals) ? vals[2] : "") "," \
          csv_esc((3 in vals) ? (vals[3] "/" (vals[3]+1)) : "") "," \
          csv_esc((6 in vals) ? vals[6] : "") "," \
          csv_esc((9 in vals) ? vals[9] : "") "," \
          csv_esc((18 in vals) ? fmt_modcod(vals[18]) : "") "," \
          csv_esc((19 in vals) ? (vals[19]==1 ? "yes" : "no") : "") "," \
          csv_esc((20 in vals) ? (vals[20]==1 ? "yes" : "no") : "") "," \
          csv_esc((10 in vals) ? (vals[10]/100) : "") "," \
          csv_esc((11 in vals) ? (vals[11]/100) : "") "," \
          csv_esc((12 in vals) ? (vals[12]/10) : "") "," \
          csv_esc((21 in vals) ? vals[21] : "") "," \
          csv_esc((22 in vals) ? vals[22] : "") "," \
          csv_esc((23 in vals) ? (vals[23]==1 ? "YES" : "no") : "") "," \
          csv_esc((15 in vals) ? vals[15] : "") "," \
          csv_esc((16 in vals) ? vals[16] : "") "," \
          csv_esc((17 in vals) ? vals[17] : "") "," \
          csv_esc((24 in vals) ? (vals[24]==1 ? "on" : "off") : "") "," \
          csv_esc((25 in vals) ? (vals[25]==1 ? "H(18V)" : "V(13V)") : "") "," \
          csv_esc(freq) "," csv_esc(ch) "," csv_esc(bucket) "," csv_esc(uplink)
    print row >> log_file
    close(log_file)
}
function modcod_thr(v,   t) {
    # Decodierschwelle (Es/N0 in dB, ETSI EN 302 307, normal frames) je MODCOD 1..28
    if (v < 1 || v > 28) return ""
    split("x -2.35 -1.24 -0.30 1.00 2.23 3.10 4.03 4.68 5.18 6.20 6.42 5.50 6.62 7.91 9.35 10.69 10.98 8.97 10.21 11.03 11.61 12.89 13.13 12.73 13.64 14.28 15.69 16.05", t, " ")
    return t[v+1] + 0
}
function d_value(   thr) {
    # D-Wert = MER minus Schwelle des aktuellen MODCOD (Reserve in dB). Leer ohne DVB-S2-MODCOD / ohne Lock.
    if (!(12 in vals) || !(18 in vals)) return ""
    if ((vals[12] + 0) <= 0) return ""
    thr = modcod_thr(vals[18] + 0)
    if (thr == "") return ""
    return (vals[12] / 10) - thr
}
function d_str(   d) {
    d = d_value()
    if (d == "") return ""
    return sprintf("D%.1f", d)
}
function d_line(   d, thr, col) {
    d = d_value()
    if (d == "") return ""
    thr = modcod_thr(vals[18] + 0)
    col = (d < 0 ? "\033[31m" : "\033[32m")
    return col sprintf("D%.1f", d) "\033[0m" sprintf("  (Reserve %.1f dB, Schwelle %.2f dB)", d, thr)
}
function write_marquee(   m, bl, cl, tbl, tcl, ds) {
    if (status_mode == "off") {
        print " " > marquee_file
        close(marquee_file)
        return
    }
    m = ""
    if (13 in vals) m = vals[13]
    if (status_mode != "default" && (14 in vals)) m = (m == "" ? vals[14] : m " - " vals[14])
    if (status_mode == "full") {
        if (9 in vals)  m = (m == "" ? fmt_thousands(vals[9]) "KS" : m " - " fmt_thousands(vals[9]) "KS")
        if ((18 in vals) && fmt_modcod(vals[18]) != "") m = (m == "" ? fmt_modcod(vals[18]) : m " - " fmt_modcod(vals[18]))
        if ((17 in vals) && fmt_estype_short(vals[17]) != "") m = (m == "" ? fmt_estype_short(vals[17]) : m " - " fmt_estype_short(vals[17]))
        if (12 in vals) m = (m == "" ? (vals[12]/10) "\302\240dB\302\240MER" : m " - " (vals[12]/10) "\302\240dB\302\240MER")
        ds = d_str()
        if (ds != "") m = m " - " ds
        if (21 in vals) m = (m == "" ? "LDPC Errors: " fmt_thousands(vals[21]) : m " - LDPC Errors: " fmt_thousands(vals[21]))
        tbl = terminal_bake_line()
        if (tbl != "") m = (m == "" ? "HF Frequenz: " tbl : m " - HF Frequenz: " tbl)
        if (6 in vals)  m = (m == "" ? fmt_thousands(vals[6]) " kHz" : m " - " fmt_thousands(vals[6]) " kHz")
        tcl = terminal_channel_line(1)
        if (tcl != "") m = m "\nKanal: " tcl
    }
    if (status_mode == "default") {
        if (12 in vals) m = (m == "" ? (vals[12]/10) "\302\240dB\302\240MER" : m " - " (vals[12]/10) "\302\240dB\302\240MER")
        ds = d_str()
        if (ds != "") m = m " - " ds
        bl = bake_line()
        if (bl != "") m = (m == "" ? bl : m " - " bl)
        cl = channel_line()
        if (cl != "") m = m "\nKanal " cl
    }
    if (status_mode == "reduced") {
        if (12 in vals) m = (m == "" ? (vals[12]/10) "\302\240dB\302\240MER" : m " - " (vals[12]/10) "\302\240dB\302\240MER")
        ds = d_str()
        if (ds != "") m = m " - " ds
        tbl = terminal_bake_line(1)
        if (tbl != "") m = (m == "" ? "HF Frequenz: " tbl : m " - HF Frequenz: " tbl)
    }
    if (status_mode == "reduced2") {
        if (12 in vals) m = (m == "" ? (vals[12]/10) "\302\240dB\302\240MER" : m " - " (vals[12]/10) "\302\240dB\302\240MER")
        ds = d_str()
        if (ds != "") m = m " - " ds
        if ((17 in vals) && fmt_estype_short(vals[17]) != "") m = (m == "" ? fmt_estype_short(vals[17]) : m " - " fmt_estype_short(vals[17]))
        tbl = terminal_bake_line(1)
        if (tbl != "") m = (m == "" ? "HF Frequenz: " tbl : m " - HF Frequenz: " tbl)
    }
    print m > marquee_file
    close(marquee_file)
}
function write_state_file(   provider, service, symrate) {
    provider = (13 in vals) ? vals[13] : ""
    service  = (14 in vals) ? vals[14] : ""
    symrate  = (9 in vals) ? vals[9] : ""
    print provider "|" service "|" symrate > state_file
    close(state_file)
}
function bch_avg_str(   cur_service, avg) {
    cur_service = (13 in vals) ? vals[13] : ""
    if (bch_avg_service != cur_service) {
        bch_avg_service = cur_service
        bch_avg_sum = 0
        bch_avg_count = 0
    }
    bch_avg_sum += vals[22] + 0
    bch_avg_count++
    avg = bch_avg_sum / bch_avg_count
    return sprintf(" \033[33m(\303\230 %.1f)\033[0m", avg)
}
function mer_avg_str(   cur_service, avg) {
    cur_service = (13 in vals) ? vals[13] : ""
    if (mer_avg_service != cur_service) {
        mer_avg_service = cur_service
        mer_avg_sum = 0
        mer_avg_count = 0
    }
    mer_avg_sum += (vals[12] / 10)
    mer_avg_count++
    avg = mer_avg_sum / mer_avg_count
    return sprintf(" \033[33m(\303\230 %.1f dB)\033[0m", avg)
}
function ldpc_avg_str(   cur_service, avg) {
    cur_service = (13 in vals) ? vals[13] : ""
    if (ldpc_avg_service != cur_service) {
        ldpc_avg_service = cur_service
        ldpc_avg_sum = 0
        ldpc_avg_count = 0
    }
    ldpc_avg_sum += vals[21] + 0
    ldpc_avg_count++
    avg = ldpc_avg_sum / ldpc_avg_count
    return sprintf(" \033[33m(\303\230 %.1f)\033[0m", avg)
}
function provider_history_str(   cur_provider) {
    cur_provider = (13 in vals) ? vals[13] : ""
    if (cur_provider != "" && cur_provider != provider_hist_last) {
        provider_hist_last = cur_provider
        if (!(cur_provider in provider_hist_seen)) {
            provider_hist_seen[cur_provider] = 1
            provider_hist_str = (provider_hist_str == "" ? cur_provider : provider_hist_str ", " cur_provider)
        }
    }
    return provider_hist_str
}
function report_num(x, digits,   r) {
    r = sprintf("%." digits "f", x)
    sub(/\./, ",", r)
    return r
}
function build_report(   svc, prov, who, dpart, mer_s, srk, bucket, freq, ch, fstr, r) {
    svc = (13 in vals) ? vals[13] : ""
    prov = (14 in vals) ? vals[14] : ""
    who = svc
    if (prov != "") who = (who == "" ? prov : who "/" prov)
    dpart = "D"
    if (report_d == "1" && d_value() != "") dpart = "D" report_num(d_value(), 1)
    r = "SigReport: " who " - " dpart
    if (12 in vals) {
        mer_s = (vals[12] / 10) ""
        sub(/\./, ",", mer_s)
        r = r " (" mer_s " dB)"
    }
    bucket = ""
    if (9 in vals) {
        srk = (vals[9] + 0) / 1000.0
        bucket = match_symrate_bucket(srk)
        r = r " - " (bucket != "" ? bucket : sprintf("%d", srk + 0.5)) " Ks"
    }
    freq = carrier_hf_mhz_precise()
    if (freq != "") {
        ch = ""
        if (bucket != "") ch = match_channel(freq, bucket)
        fstr = (ch != "" ? ch : sprintf("%.2f", freq))
        sub(/\./, ",", fstr)
        r = r " - " fstr
    }
    if (report_suffix != "") r = r " - " report_suffix
    return r
}
function read_report_request(   line) {
    # Taste r: Anforderung aus der Datei lesen, Report bauen, anzeigen, speichern, kopieren
    if (req_file == "") return
    line = ""
    if ((getline line < req_file) > 0 && line != "") {
        close(req_file)
        printf "" > req_file
        close(req_file)
        report_text = build_report()
        print report_text > report_file
        close(report_file)
        if (clip_cmd != "") {
            printf "%s", report_text | clip_cmd
            close(clip_cmd)
            report_status = "in Zwischenablage kopiert"
        } else {
            report_status = "keine Zwischenablage (xclip/xsel/wl-copy fehlt)"
        }
        return
    }
    close(req_file)
}
function read_res(   r) {
    # Aufloesung aus RES_FILE lesen (wird vom Hintergrund-Poller per VLC-Telnet geschrieben).
    if (res_file == "") return
    r = ""
    if ((getline r < res_file) > 0 && r ~ /^[0-9]+x[0-9]+$/) video_res = r
    close(res_file)
}
function read_mode(   m) {
    # Overlay-Modus aus der Mode-Datei lesen (wird von den Tasten 1-4 gesetzt)
    if (mode_file == "") return
    m = ""
    if ((getline m < mode_file) > 0) {
        if (m == "off" || m == "default" || m == "reduced" || m == "reduced2" || m == "full") status_mode = m
    }
    close(mode_file)
}
function mode_keys_str(   keys, names, i, lbl, out) {
    split("off default reduced full", keys, " ")
    split("aus Standard Service+Prov Alles", names, " ")
    out = ""
    for (i = 1; i <= 4; i++) {
        lbl = i "=" names[i]
        if (keys[i] == status_mode) lbl = "\033[7m" lbl "\033[0m"
        out = out (i > 1 ? "  " : "") lbl
    }
    return out
}
function outln(s) {
    printf "%s\033[K\n", s
}
function draw(   hfm, bchavg, meravg, ldpcavg, provhist, estype_name) {
    read_mode()
    read_res()
    read_report_request()
    write_state_file()
    write_marquee()
    printf "\033[H"
    outln("==================================================")
    outln(" Longmynd Status  (Port " port ")   " strftime("%H:%M:%S"))
    outln("==================================================")
    if (1 in vals)  outln("State:             " fmt_state(vals[1]))
    if (2 in vals)  outln("LNA Gain:          " vals[2])
    if (3 in vals)  outln("Puncture Rate:     " vals[3] "/" (vals[3]+1))
    if (6 in vals) outln("HF Frequenz:       " terminal_bake_line())
    if (6 in vals) {
        hfm = carrier_hf_mhz()
        outln("Carrier Freq:      " fmt_thousands(vals[6]) " kHz" (hfm != "" ? "  (" hfm " MHz)" : ""))
    }
    outln("Kanal:             " (terminal_channel_line() != "" ? terminal_channel_line() : "-"))
    if (9 in vals)  outln("Symbol Rate:       " fmt_thousands(vals[9]) " S/s")
    if (18 in vals) outln("MODCOD:            " fmt_modcod(vals[18]))
    if (19 in vals) outln("Short Frames:      " (vals[19]==1 ? "yes" : "no"))
    if (20 in vals) outln("Pilot Symbols:     " (vals[20]==1 ? "yes" : "no"))
    outln("--------------------------------------------------")
    if (10 in vals) outln("Viterbi Err Rate:  " (vals[10]/100) "%")
    if (11 in vals) outln("Bit Error Rate:    " (vals[11]/100) "%")
    if (12 in vals) {
        meravg = mer_avg_str()
        outln("MER:               " (vals[12]/10) " dB" meravg)
        if (d_line() != "") outln("D-Wert:            " d_line())
    }
    if (21 in vals) {
        ldpcavg = ldpc_avg_str()
        outln("LDPC Errors:       " fmt_thousands(vals[21]) ldpcavg)
    }
    if (22 in vals) {
        bchavg = bch_avg_str()
        outln("BCH Errors:        " fmt_thousands(vals[22]) bchavg)
    }
    if (23 in vals) outln("BCH Uncorrected:   " (vals[23]==1 ? "YES !" : "no"))
    if (15 in vals) outln("Null Ratio:        " vals[15] "%")
    outln("--------------------------------------------------")
    if (13 in vals) outln("Service:           " vals[13])
    if (14 in vals) outln("Provider:          " vals[14])
    if (16 in vals) outln("ES PID:            " fmt_thousands(vals[16]))
    if (17 in vals) {
        estype_name = fmt_estype(vals[17])
        outln("ES Type:           " vals[17] (estype_name != "" ? " (" estype_name ")" : ""))
    }
    if (video_res != "") outln("Aufloesung:        " video_res)
    outln("--------------------------------------------------")
    if (text_mode == "full") {
        if (24 in vals) outln("LNB Voltage:       " (vals[24]==1 ? "on" : "off"))
        if (25 in vals) outln("LNB Polarisation:  " (vals[25]==1 ? "H (18V)" : "V (13V)"))
    }
    if (key_help == "1") outln(" Tasten: s=Snapshot v=Aufnahme r=Report STRG+C=Ende")
    if (key_help == "1") outln(" Overlay: " mode_keys_str())
    outln("==================================================")
    provhist = provider_history_str()
    if (provhist != "") outln("Service-Verlauf:   " provhist)
    if (report_text != "") {
        outln("Report:            " report_text)
        outln("                   (" report_status ")")
    }
    printf "\033[J"
    fflush(stdout)
}
BEGIN {
    last_id = -1
    last_restart = 0; last_restart_info = ""
    last_reload = 0;  last_reload_info = ""
    have_drift = 0; last_drift_mhz = 0
    log_started = 0; last_logged_provider = ""
    bch_avg_service = ""; bch_avg_sum = 0; bch_avg_count = 0
    mer_avg_service = ""; mer_avg_sum = 0; mer_avg_count = 0
    ldpc_avg_service = ""; ldpc_avg_sum = 0; ldpc_avg_count = 0
    provider_hist_last = ""; provider_hist_str = ""
    h266_last_key = ""
    system("clear")
    if (status_mode == "off") {
        print "" > marquee_file
        close(marquee_file)
    }
}
{
    gsub(/\$/, "", $1);
    id = $1 + 0;
    val = $2;
    gsub(/\r/, "", val);

    # Symbol Rate (ID 9 / "Datenrate") um mehr als SYMRATE_THRESHOLD (relativ) abgewichen?
    # -> ueberwachtes VLC hart neu starten (kill, Watchdog startet neu)
    if (id == 9 && (9 in vals) && (vals[9]+0) != 0) {
        old_rate = vals[9] + 0
        new_rate = val + 0
        rel_diff = (new_rate - old_rate) / old_rate
        if (rel_diff < 0) rel_diff = -rel_diff
        if (rel_diff > symrate_threshold) {
            now = systime()
            if (last_restart == 0 || (now - last_restart) >= min_interval) {
                print strftime("%Y-%m-%d %H:%M:%S") " [auswertung_4002] Symbol Rate Abweichung " int(rel_diff*100) "% (" old_rate " -> " new_rate " S/s) - kille VLC hart (PID aus " pidfile ")" > "/dev/stderr"
                fflush("/dev/stderr")
                system("test -f " pidfile " && kill -9 $(cat " pidfile ") 2>/dev/null")
                last_restart_info = strftime("%H:%M:%S") " (" vals[9] " -> " val " S/s, " int(rel_diff*100) "%)"
                last_restart = now
            }
        }
    }

    # Service (13) oder Provider (14) geaendert? -> Stream per Telnet
    # sanft neu laden (stop/remove/enqueue/play), ohne VLC selbst zu beenden.
    if ((id == 13 || id == 14) && (id in vals) && val != vals[id]) {
        now = systime()
        if (last_reload == 0 || (now - last_reload) >= min_reload_interval) {
            print strftime("%Y-%m-%d %H:%M:%S") " [auswertung_4002] " (id==13 ? "Service" : "Provider") " geaendert: " vals[id] " -> " val " - lade Stream per Telnet neu" > "/dev/stderr"
            fflush("/dev/stderr")
            system(reload_script " >/dev/null 2>&1 &")
            last_reload_info = strftime("%H:%M:%S") " (" (id==13 ? "Service" : "Provider") ": " vals[id] " -> " val ")"
            last_reload = now
        }
    }

    # neuer Zyklus erkannt: ID faengt wieder von vorne an -> Screen clearen und alten Stand zeigen.
    # Genau hier (nicht sofort bei ID 13) ist der richtige Moment fuers CSV-Log: an dieser
    # Stelle sind ALLE Werte des GERADE ABGESCHLOSSENEN Zyklus bereits konsistent in vals[]
    # (Rufzeichen UND Service Name UND Symbol Rate etc. gehoeren garantiert zusammen).
    if (id < last_id) {
        draw()
        if (log_mode == "1" && (13 in vals) && (!log_started || vals[13] != last_logged_provider)) {
            log_csv(vals[13])
            last_logged_provider = vals[13]
            log_started = 1
        }
        if ((17 in vals) && (vals[17] + 0) == 51) log_h266()
    }

    vals[id] = val;
    last_id = id;
}
END {
    draw()
    if (log_mode == "1" && (13 in vals) && (!log_started || vals[13] != last_logged_provider)) {
        log_csv(vals[13])
    }
    if ((17 in vals) && (vals[17] + 0) == 51) log_h266()
}
'
