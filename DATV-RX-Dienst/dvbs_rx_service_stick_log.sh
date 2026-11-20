#!/bin/bash
# dvbs_rx_service_stick_log.sh - alle Logs/Stati rund um die RTL-SDR-Sticks am Pi (RTL-SDR-datvRX-Dienst) auf einmal ausgeben.
# Aufruf: ./dvbs_rx_service_stick_log.sh [-p PI-IP] [-n ZEILEN] [-s ssh-Ziel] [-o DATEI] [-g]
#   -p  IP des Pi (Standard 192.168.0.102)      -n  Zeilen je Abschnitt (Standard 40)
#   -s  ssh-Ziel (Standard dg7ndv@<IP>)           -o  Ausgabe zusaetzlich in diese Datei schreiben
#   -g  auch das GUI-Log des PCs (~/.leandvb-GUI/gui.log) ausgeben
# Abschnitte: Stick-Status, USB-Geraete, USB-Fehler im Kernel-Log (mit Uhrzeit), Unterspannung, Dienst (systemd/journal), Verteil-Protokoll,
#             Fehlerbericht des Dienstes, rtl_test-Liste, optional GUI-Log.
PI=192.168.0.102; N=40; ZIEL=""; OUT=""; GUI=0
while getopts "p:n:s:o:gh" o; do case $o in p) PI=$OPTARG;; n) N=$OPTARG;; s) ZIEL=$OPTARG;; o) OUT=$OPTARG;; g) GUI=1;; *) sed -n '2,9p' "$0"; exit 0;; esac; done
ZIEL=${ZIEL:-dg7ndv@$PI}
HIER=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
R() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$ZIEL" "$1" 2>&1 | grep -v -i "quantum\|vulnerable\|upgraded"; }
alles() {
  echo "##### Stick-Log  $(date '+%d.%m.%Y %H:%M:%S')  Pi $PI #####"
  echo; echo "=== 1. Stick-Status (Dienst + USB) ==="
  "$HIER/dvbs_rx_service_stick_status.py" --pi "$PI" --usb --ssh "$ZIEL" 2>&1
  echo; echo "=== 2. USB-Geraete am Pi (lsusb) ==="
  R "lsusb | grep -i '0bda\|realtek\|nooelec' ; echo; lsusb -t 2>/dev/null | head -20"
  echo; echo "=== 3. USB-Fehler im Kernel-Log (letzte $N, mit Uhrzeit) ==="
  R "sudo dmesg -T 2>/dev/null | grep -i usb | grep -i 'error\|disconnect\|enumerate\|not accepting\|not responding\|new .*device\|SerialNumber\|Product' | tail -$N"
  echo; echo "=== 4. Unterspannung / Drosselung ==="
  R "vcgencmd get_throttled; uptime -p"
  echo; echo "=== 5. Dienst datvrx-dienst (systemd) ==="
  R "systemctl is-active datvrx-dienst; systemctl is-enabled datvrx-dienst; journalctl -u datvrx-dienst --no-pager -n $N | cut -c1-220"
  echo; echo "=== 6. Verteil-Protokoll des Dienstes (letzte $N) ==="
  curl -s -m 8 "http://$PI:8554/protokoll.txt" | tail -"$N" | cut -c1-240; [ "${PIPESTATUS[0]}" = 0 ] || echo "(Dienst nicht erreichbar)"
  echo; echo "=== 7. Fehlerbericht des Dienstes ==="
  curl -s -m 8 "http://$PI:8554/fehler.txt" | head -"$N" | cut -c1-240
  echo; echo "=== 8. rtl_test: erkannte RTL-Geraete (belegte Sticks melden claim-Fehler, das ist normal) ==="
  R "timeout 10 rtl_test -t 2>&1 | sed -n '1,8p'"
  if [ "$GUI" = 1 ]; then
    echo; echo "=== 9. GUI-Log (PC, ohne Spektrum-Zeilen, letzte $N) ==="
    grep -v "Spektrum:" ~/.leandvb-GUI/gui.log 2>/dev/null | tail -"$N" | cut -c1-240
  fi
  echo; echo "##### Ende #####"
}
if [ -n "$OUT" ]; then alles | tee "$OUT"; echo "(auch gespeichert in $OUT)"; else alles; fi
