#!/usr/bin/env bash
# dienst.sh - RTL-SDR-datvRX-Dienst starten/stoppen/pruefen (DG7NDV)
# Aufruf:  ./dienst.sh start|stop|restart|status|log        (Optionen fuer das Programm in DIENST_OPT, z.B. DIENST_OPT="--mqtt 192.168.0.10")
DIR="$(cd "$(dirname "$0")" && pwd)"; LOG=/tmp/qo100_datvrx_dienst.log; PID=/tmp/qo100_datvrx_dienst.pid
IP=$(ip -4 -o addr show | awk '/192\.168\.20\./{print $4}' | cut -d/ -f1 | head -1); IP=${IP:-localhost}
up() { curl -s -m 2 -o /dev/null http://127.0.0.1:8554/status; }
case "${1:-status}" in
  start)  if up; then echo "laeuft schon -> http://$IP:8554/"; exit 0; fi
          setsid nohup python3 -u "$DIR/datvrx_dienst.py" ${DIENST_OPT:-} </dev/null >>"$LOG" 2>&1 & echo $! >"$PID"
          sleep 3; up && echo "gestartet -> http://$IP:8554/   (Playlist /qo100.m3u)" || echo "Start fehlgeschlagen, siehe $LOG" ;;
  stop)   for p in $(pgrep -f "python3 -u .*[d]atvrx_dienst.py"); do kill -TERM "$p" 2>/dev/null; done; rm -f "$PID"; sleep 2
          up && echo "laeuft noch" || echo "gestoppt" ;;
  restart) "$0" stop; "$0" start ;;
  status) if up; then echo "laeuft -> http://$IP:8554/  $(curl -s -m 2 http://127.0.0.1:8554/status | python3 -c 'import json,sys; d=json.load(sys.stdin); print("| aktuell", d["aktuell"], "| Zuschauer", d["zuschauer"], "| Auto-Scan", d["auto_scan"], "| Signale", len(d["signale"]))' 2>/dev/null)"; else echo "aus"; fi
          pgrep -x rtl_sdr >/dev/null && echo "Stick: belegt" || echo "Stick: frei" ;;
  log)    tail -n 30 -f "$LOG" ;;
  *)      sed -n '2,3p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
