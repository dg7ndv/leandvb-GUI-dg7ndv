#!/usr/bin/env bash
# install_pi.sh - QO-100-Waechter auf dem Hauspi installieren (Dienst qo100-waechter, MQTT-Broker 127.0.0.1:1883 dort)
# Aufruf: ./install_pi.sh [pi@192.168.0.10]     (SSH-Schluessel-Login noetig, sudo auf dem Pi)
#         ./install_pi.sh --entfernen [ziel]     Dienst wieder entfernen
set -eu
cd "$(dirname "$0")"
if [ "${1:-}" = "--entfernen" ]; then ZIEL=${2:-pi@192.168.0.10}
    ssh "$ZIEL" 'sudo systemctl disable --now qo100-waechter; sudo rm -f /etc/systemd/system/qo100-waechter.service; sudo systemctl daemon-reload; rm -rf ~/waechter_mqtt'
    echo "entfernt"; exit 0
fi
ZIEL=${1:-pi@192.168.0.10}
ssh "$ZIEL" 'mkdir -p ~/waechter_mqtt'
scp -q waechter_mqtt.py qo100-waechter.service "$ZIEL":waechter_mqtt/
ssh "$ZIEL" 'chmod +x ~/waechter_mqtt/waechter_mqtt.py
  python3 -c "import paho.mqtt.client, websocket" || pip3 install --user paho-mqtt websocket-client
  sudo cp ~/waechter_mqtt/qo100-waechter.service /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now qo100-waechter
  sleep 3; systemctl is-active qo100-waechter'
echo "Pruefen:  mosquitto_sub -h ${ZIEL#*@} -t 'qo100/#' -v"
