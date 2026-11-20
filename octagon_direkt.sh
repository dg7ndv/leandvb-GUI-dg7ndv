#!/bin/sh
# octagon_direkt.sh - Direktmodus der Octagon (ohne Enigma) von tesla aus steuern.
#
#   ./octagon_direkt.sh start [MHz] [kS/s] [h|v]   dvb_direct.py auf die Box kopieren, Enigma
#                                                  stoppen, Dauerlaeufer starten, Frequenz setzen
#   ./octagon_direkt.sh tune  <MHz> <kS/s> [h|v]   nur neu abstimmen (Dauerlaeufer muss laufen)
#   ./octagon_direkt.sh status                     Lock, SNR, Frequenz, Bytes (kompakt)
#   ./octagon_direkt.sh status2                    alle Werte untereinander (CC, BER, dBm, CNR, UNC ...)
#   ./octagon_direkt.sh stats                      Rohdiagnose der DVBv5-Statistik (warum dBm/CNR leer?)
#   ./octagon_direkt.sh stop                       Tuner freigeben, Enigma wieder starten (init 3)
#
# Beispiel Bake:  ./octagon_direkt.sh start 10491.5 1500 h
# Strom ansehen:  vlc udp://@230.0.0.9:1234
#
# Anpassen per Umgebungsvariable, z.B.  BOX=192.168.0.226 ./octagon_direkt.sh start
BOX=${BOX:-192.168.0.220}
PORT=${PORT:-55600}
LO=${LO:-9540}
UDP=${UDP:-230.0.0.9:1234}
DIR="$(cd "$(dirname "$0")" && pwd)"

# UDP-Kommando per python3 (nc beendet sich nach stdin-EOF, bevor die Antwort da ist)
send() {
  python3 - "$BOX" "$PORT" "$1" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(6)
try:
    s.sendto(sys.argv[3].encode(), (sys.argv[1], int(sys.argv[2])))
    print(s.recvfrom(2048)[0].decode("utf-8", "replace").strip())
except OSError as e:
    print("keine Antwort (%s)" % e)
PY
}

case "$1" in
  start)
    F=${2:-10491.5}; SR=${3:-1500}; POL=${4:-h}
    scp "$DIR/dvb_direct.py" "root@$BOX:/tmp/dvb_direct.py" || { echo "scp fehlgeschlagen"; exit 1; }
    ssh "root@$BOX" "init 4; sleep 5; killall -9 enigma2.sh enigma2 2>/dev/null; sleep 2; \
nohup python3 /tmp/dvb_direct.py --minimal -f 0 --lo $LO --pol $POL --udp $UDP --ctrl-port $PORT \
>/tmp/dvb_direct.log 2>&1 &"
    sleep 4
    echo "PING:   $(send PING)"
    echo "TUNE:   $(send "TUNE $F $SR $POL")"
    sleep 3
    echo "STATUS: $(send STATUS)"
    ;;
  tune)
    [ -n "$2" ] && [ -n "$3" ] || { echo "Aufruf: $0 tune <MHz> <kS/s> [h|v]"; exit 1; }
    echo "TUNE: $(send "TUNE $2 $3 ${4:-h}")"
    ;;
  status)
    echo "STATUS: $(send STATUS)"
    ;;
  stats)
    # Rohdiagnose der DVBv5-Statistik (warum sind dBm/CNR/pre/post/UNC leer?)
    send STATS | sed 's/ | /\n/; s/ /\n/g'
    ;;
  status2)
    # alle Werte untereinander (CC, BER, dBm, CNR, pre/post-BER, UNC, ...)
    send STATUS2 | tr ' ' '\n'
    ;;
  stop)
    echo "STOP:   $(send STOP)"
    sleep 2
    ssh "root@$BOX" "init 3" && echo "Enigma wird neu gestartet (30-60 s bis das Bild da ist)"
    ;;
  *)
    echo "Aufruf: $0 start [MHz] [kS/s] [h|v] | tune <MHz> <kS/s> [h|v] | status | status2 | stats | stop"
    exit 1
    ;;
esac
