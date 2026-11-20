#!/usr/bin/env bash
# qo100_dienste.sh - QO-100 Zapper (VLC-Umschalter) und Waechter (Test/Protokoll) starten, stoppen, pruefen (DG7NDV)
# Aufruf:  ./qo100_dienste.sh start|stop|restart|status|log  [zapper|waechter|all]     (Standard: status all)
#   zapper    http://<IP>:8554/qo100.m3u  - Handy-VLC schaltet mit "naechstes" durch die Signale, Stick folgt
#   waechter  testet alle neuen Signale am Stick, schreibt qo100_protokoll.csv  (pausiert, solange der Zapper den Stick hat)
# Beide teilen sich einen RTL-Stick. Logs: /tmp/qo100_zapper.log  /tmp/qo100_waechter.log
DIR="$(cd "$(dirname "$0")" && pwd)"; DVX="$DIR"
ZLOG=/tmp/qo100_zapper.log; WLOG=/tmp/qo100_waechter.log; ZPID=/tmp/qo100_zapper.pid; WPID=/tmp/qo100_waechter.pid
IP=$(ip -4 -o addr show | awk '/192\.168\.20\./{print $4}' | cut -d/ -f1 | head -1); IP=${IP:-localhost}
cmd=${1:-status}; what=${2:-all}

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
zapper_up() { curl -s -m 2 -o /dev/null "http://127.0.0.1:8554/status"; }

start_zapper() {
    if zapper_up; then echo "Zapper laeuft schon  -> http://$IP:8554/qo100.m3u"; return; fi
    setsid nohup python3 -u "$DIR/zapper.py" </dev/null >>"$ZLOG" 2>&1 & echo $! >"$ZPID"
    sleep 2; zapper_up && echo "Zapper gestartet     -> http://$IP:8554/qo100.m3u   (Web: http://$IP:8554/)" || echo "Zapper: Start fehlgeschlagen, siehe $ZLOG"
}
start_waechter() {
    if pgrep -f "python3 -u .*[q]o100_wacht.py" >/dev/null; then echo "Waechter laeuft schon"; return; fi
    setsid nohup python3 -u "$DVX/qo100_wacht.py" --decode --secs 35 --log "$WLOG" </dev/null >>"$WLOG.out" 2>&1 & echo $! >"$WPID"
    sleep 1; alive "$WPID" && echo "Waechter gestartet   -> Protokoll $DVX/qo100_protokoll.csv" || echo "Waechter: Start fehlgeschlagen, siehe $WLOG.out"
}
stop_zapper() {
    alive "$ZPID" && kill -TERM "$(cat $ZPID)" 2>/dev/null
    for p in $(pgrep -f "python3 -u .*[z]apper.py"); do kill -TERM "$p" 2>/dev/null; done
    rm -f "$ZPID"; sleep 1; zapper_up && echo "Zapper laeuft noch" || echo "Zapper gestoppt"
}
stop_waechter() {
    for p in $(pgrep -f "python3 -u .*[q]o100_wacht.py"); do kill -TERM "$p" 2>/dev/null; done
    sleep 3
    for p in $(pgrep -f "python3 -u .*[q]o100_wacht.py"); do kill -KILL "$p" 2>/dev/null; done       # haengt er noch in einem Test: hart beenden
    rm -f "$WPID"; echo "Waechter gestoppt"
}
status() {
    if zapper_up; then
        echo "Zapper:   laeuft  http://$IP:8554/   $(curl -s -m 2 http://127.0.0.1:8554/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print("aktuell", d["aktuell"], "| Zuschauer", d["zuschauer"], "| Signale", len(d["signale"]))' 2>/dev/null)"
    else echo "Zapper:   aus"; fi
    if pgrep -f "python3 -u .*[q]o100_wacht.py" >/dev/null; then echo "Waechter: laeuft  (Protokoll $DVX/qo100_protokoll.csv, Zeilen: $(( $(wc -l < $DVX/qo100_protokoll.csv 2>/dev/null || echo 1) - 1 )))"; else echo "Waechter: aus"; fi
    pgrep -x rtl_sdr >/dev/null && echo "Stick:    belegt (rtl_sdr laeuft)" || echo "Stick:    frei"
}
case "$cmd" in
    start)   [[ $what =~ zapper|all ]] && start_zapper; [[ $what =~ waechter|all ]] && start_waechter ;;
    stop)    [[ $what =~ waechter|all ]] && stop_waechter; [[ $what =~ zapper|all ]] && stop_zapper ;;
    restart) "$0" stop "$what"; "$0" start "$what" ;;
    status)  status ;;
    log)     tail -n 30 -f $( [[ $what == waechter ]] && echo "$WLOG.out" || echo "$ZLOG" ) ;;
    *)       sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
