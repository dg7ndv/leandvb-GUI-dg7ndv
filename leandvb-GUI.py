#!/usr/bin/env python3

# Simple UI voor LEANDVB, DVBS receiver.
# requires sudo apt-get install python-imaging-tk package
# keeps parameters file and helper scripts in ~/.leandvb-GUI (see parameters_path)
# if you add a file called logo.png it will be shown on the right side
# Leandvb by F4DAV (github leansdr)
# Wrapper by pe2jko@540.org

# TODO redirect stdout to test-widget
# TODO remember last 10 frequencies
# TODO remember last 10 sybolrates
# TODO don't kill not existend processes (avoid failure messages)
# TODO leandvb-run as function like leandvb-stop
# TODO leandvb: --tune is broken, use --derotate instead
# TODO provide fast switching between sets of changable favorite settings

from tkinter import *                                   # Konstanten (W, EW, ...), Button, Frame, ...
from tkinter import filedialog
from tkinter import simpledialog, messagebox
from tkinter.filedialog import askdirectory, askopenfilename
#from tkFileDialog import *
from tkinter import ttk
import tkinter as tk
import os
import sys
# Start beschleunigen: Tk spricht ueber XIM mit dem IBus-Daemon; laeuft der (wie hier) mit 100 % CPU, dauert jedes Fenster-Layout Sekunden
# (gemessen: 40 Labels 3,5 s statt 0,02 s, GUI-Start 25-40 s statt 3 s). Ohne XIM keine Compose-/Eingabemethoden-Zeichen in der GUI.
# Wieder einschalten: Umgebungsvariable LEANDVB_XIM=1 (oder besser: "ibus restart").
if not os.environ.get("LEANDVB_XIM"):
    os.environ["XMODIFIERS"] = "@im=none"
import json
import datetime
import collections
import math
import struct
import threading
import time
import stat
from collections import deque
try:
    import websocket   # pip3 install websocket-client --break-system-packages
    HAVE_WEBSOCKET = True
except ImportError:
    HAVE_WEBSOCKET = False
import argparse
import re
import shutil
import shlex
import subprocess
import pty
import fcntl
import termios
import struct
import urllib.request
import urllib.parse
import urllib.error
import socket
import csv
import signal

#===== Kommandozeilen-Parameter (Schritt 2: -octagon / -longmynd) =============
# Ohne Parameter: Klick auf ein BATC-Signal startet wie bisher (Schritt 1) den lokalen rtl_sdr/leandvb.
# -octagon   : Klick auf ein BATC-Signal schaltet stattdessen die Octagon-Box (openATV) um
#              und startet VLC mit Marquee-Einblendung (Frequenz/Symbolrate/MER).
# -longmynd  : Klick auf ein BATC-Signal sendet stattdessen "C<ZF_kHz>,<SR>" per WebSocket
#              an einen LongMynd-Receiver.
_argp = argparse.ArgumentParser(
    prog="leandvb-GUI.py",
    description="leandvb-GUI: DVB-S/S2-Empfang mit rtl_sdr/leandvb, inkl. BATC-QO100-Signalliste.\n"
                 "Ohne Parameter steuert ein Klick auf ein BATC-Signal den lokalen rtl_sdr.\n"
                 "Lokale Empfaenger mit RTL-Stick: RTL-SDR-LeanDVB (rtl_sdr | leandvb, Parameter -rtlsdr) und\n"
                 "RTL-SDR-datvRX (datvrx von HB9IIU, mit MER, Pegel, Rufzeichen; Parameter -datvrx1 ... -datvrx8).",
    epilog="Beispiele:\n"
           "  ./leandvb-GUI.py                         # nur lokaler rtl_sdr (Schritt 1)\n"
           "  ./leandvb-GUI.py -octagon                # BATC-Klick schaltet die Octagon-Box + VLC\n"
           "  ./leandvb-GUI.py -octagon --octagon-ip 192.168.0.226\n"
           "  ./leandvb-GUI.py -longmynd               # BATC-Klick sendet an LongMynd (192.168.0.103)\n"
           "  ./leandvb-GUI.py -longmynd --longmynd-ip 192.168.0.103 --longmynd-port 8080\n"
           "  ./leandvb-GUI.py -longmynd -longmynd2    # zweiter LongMynd (Default 127.0.0.1:8080)\n"
           "  ./leandvb-GUI.py -datvrx1                # nur der neue Empfaenger RTL-SDR-datvRX1 (RTL-Stick 0, datvrx)\n"
           "  ./leandvb-GUI.py -datvrx1 -datvrx2       # zwei Sticks: Stick 1 -> datvRX1, Stick 2 -> datvRX2 (Zuordnung beim Start per USB-Suche)\n"
           "  ./leandvb-GUI.py -rtlsdr -datvrx1        # alter RTL-SDR-LeanDVB und neuer RTL-SDR-datvRX nebeneinander (nicht mit demselben Stick)",
    formatter_class=argparse.RawDescriptionHelpFormatter)
_argp.add_argument("--refreshSpektrum_per_Sekond", dest="spektrum_refresh", type=float, default=5.0, metavar="N",
                   help="Wie oft pro Sekunde das Spektrum hoechstens neu gezeichnet wird, wenn sich nur das FFT-Bild aendert "
                        "(Default 5; weniger = geringere CPU-Last, Maus/Fenstergroesse zeichnen immer sofort)")
_argp.add_argument("-octagonDirektQRG", dest="octagon_direkt_qrg", action="store_true",
                   help="Octagon im Direktmodus starten: Box ohne Enigma als frei abstimmbarer Tuner "
                        "(dvb_direct.py laeuft dort, Umschalten per UDP statt ueber die Kanalliste)")
_argp.add_argument("-lna", action="store_true", help="LNA-Anzeige in der LongMynd-Statuszeile einblenden (sonst ausgeblendet)")
_argp.add_argument("-octagon_all_rx_parameters", "--octagon-all-rx-parameters", dest="octagon_all_rx",
                   action="store_true",
                   help="Octagon: Zeile mit allen Empfangswerten (dBm, CNR, pre/post-BER, UNC) einblenden - "
                        "ohne den Schalter bleibt sie ausgeblendet (der Treiber der SF8008 liefert die "
                        "DVBv5-Statistik nicht)")
_argp.add_argument("-web", "--web", dest="web", action="store_true", help="altes Webinterface (Port 8099) starten (sonst nur ueber den Haken im Menue Konfig)")
_argp.add_argument("-nowebgui", "--nowebgui", dest="nowebgui", action="store_true", help="neues Web-GUI (Port 8100, Zapper-Stil) nicht starten")
_argp.add_argument("-einfach", "--einfach", "--simple", dest="einfach", action="store_true", help="Festfenster-Ansicht beim Start (frueher Einfach): 4 Videos (Quad) im Hauptfenster mit vereinfachter Steuerung (Menue Festfenster schaltet um)")
_argp.add_argument("-mini", "--mini", dest="mini", action="store_true", help="Mini-Anzeige beim Start: nur Spektrum, Signalknoepfe, MER und Rufzeichen (Menue Mini schaltet um)")
_argp.add_argument("-buttonall", action="store_true", help="Alle optionalen Anzeigen/Knoepfe einblenden (u.a. LNA)")
_argp.add_argument("-octagon", action="store_true", help="Octagon-Box (openATV) + VLC als Ziel aktivieren")
_argp.add_argument("-longmynd", action="store_true", help="LongMynd-Receiver (WebSocket) als Ziel aktivieren")
_argp.add_argument("-rtlsdr", action="store_true", help="RTL-SDR-LeanDVB (lokaler rtl_sdr | leandvb) als Ziel aktivieren (zusaetzlich zu -octagon/-longmynd/-datvrxN; ohne jedes Flag ist er automatisch aktiv)")
_argp.add_argument("--octagon-ip", default="192.168.0.226", help="IP/Host der Octagon-Box (Default 192.168.0.226)")
_argp.add_argument("--octagon-ssh-user", default="root",
                    help="SSH-User fuer den Rufzeichen-Abruf per dvbsnoop auf der Octagon-Box (Default root)")
_argp.add_argument("--octagon-stream-port", type=int, default=8001, help="Stream-Port der Box fuer VLC (Default 8001)")
_argp.add_argument("--longmynd-ip", default="192.168.0.103", help="IP des LongMynd-Receivers (Default 192.168.0.103)")
_argp.add_argument("--longmynd-port", type=int, default=8080, help="WebSocket-Port des LongMynd-Receivers (Default 8080)")
_argp.add_argument("--longmynd-stream-ip", default="230.0.0.3", help="Multicast-IP des LongMynd-Videostreams (Default 230.0.0.3)")
_argp.add_argument("--longmynd-stream-port", type=int, default=1234, help="Multicast-Port des LongMynd-Videostreams (Default 1234)")
_argp.add_argument("--no-vlc", action="store_true", help="Kein VLC starten (nur Octagon-Umschaltung)")
_argp.add_argument("--no-vlc-restart", action="store_true", help="VLC nach Beenden nicht automatisch neu starten")
_argp.add_argument("--no-marquee", action="store_true", help="Keine Frequenz/Symbolrate/MER-Einblendung in VLC")
_argp.add_argument("--vlc-delay", type=float, default=1.0, help="Wartezeit (s) nach Signal, bevor VLC startet (Default 1.0)")
_argp.add_argument("--vlc-restart-delay", type=float, default=10.0, help="Wartezeit (s) vor VLC-Neustart nach Beenden (Default 10.0)")
_argp.add_argument("--signal-timeout", type=float, default=20.0, help="Max. Wartezeit (s) auf Box-Signal, bevor VLC startet (Default 20, 0=sofort)")
_argp.add_argument("--vlc-marquee-file", default="/tmp/vlc_marquee_octagon.txt", help="Datei fuer die VLC-Einblendung")
_argp.add_argument("--vlc-http-port", type=int, default=8082, help="VLC HTTP-Interface-Port (Default 8082)")
_argp.add_argument("--vlc-telnet-port", type=int, default=4214, help="VLC Telnet-Interface-Port (Default 4214)")
_argp.add_argument("--longmynd-vlc-http-port", type=int, default=8080,
                    help="HTTP-Port fuer den von der GUI gestarteten VLC (LongMynd, Default 8080)")
_argp.add_argument("--longmyndp4-vlc-http-port", type=int, default=8081,
                    help="HTTP-Port fuer den von der GUI gestarteten VLC (LongMynd P4, Default 8081)")
_argp.add_argument("--rtlsdr-vlc-http-port", type=int, default=8083,
                    help="HTTP-Port fuer den von der GUI gestarteten VLC (rtl_sdr, Default 8083)")
_argp.add_argument("--rtlsdr-vlc-telnet-port", type=int, default=4215,
                    help="Telnet-Port fuer den von der GUI gestarteten VLC (rtl_sdr, Default 4215)")
_argp.add_argument("--status", choices=("1", "mer"), default=None, help="Einblendung kuerzen: 1=Freq/SR/MER in dB, mer=nur MER")
_argp.add_argument("--dry-run", action="store_true", help="Nur anzeigen was passieren wuerde, nichts schalten/senden")
_argp.add_argument("--debug", action="store_true", help="Ausfuehrliche Log-Ausgaben auf der Konsole")
_argp.add_argument("-conBatc", action="store_true", help="Beim Start automatisch mit dem BATC-Server verbinden (wie Klick auf 'Verbinden')")
_argp.add_argument("--umschaltToleranz", type=float, default=1000, metavar="Hz",
                    help="Liegt ein Ziel (rtl_sdr/LongMynd/LongMynd P4) schon innerhalb dieser "
                         "Toleranz (in Hz) auf der gewuenschten Frequenz, wird nicht aktiv "
                         "umgeschaltet (Default 1000 Hz = 1 kHz). Mit 0 wird diese Pruefung "
                         "faktisch abgeschaltet - es wird dann bei jedem Signal immer aktiv "
                         "umgeschaltet. Fuer Octagon gilt stattdessen --octagon-umschaltToleranz.")
_argp.add_argument("--octagon-umschaltToleranz", type=float, default=100, metavar="Hz",
                    dest="octagon_umschalttoleranz",
                    help="Wie --umschaltToleranz, aber eigener (kleinerer) Wert nur fuer Octagon "
                         "(Default 100 Hz) - Octagon schaltet ueber eine feste Kanalliste um, "
                         "daher reicht hier eine deutlich engere Toleranz, bevor als "
                         "'schon auf der Frequenz' gilt. Mit 0 wird immer aktiv umgeschaltet "
                         "(die Pruefung ist dann faktisch abgeschaltet).")
_argp.add_argument("--longmynd-status-script", default=os.path.expanduser("~/batch/longmynd/auswertung_4002.sh"),
                    help="Pfad zum Longmynd-Status-Dashboard-Script (auswertung_4002.sh)")
_argp.add_argument("--longmynd-status-port", type=int, default=4002, metavar="PORT",
                    help="UDP-Port, auf dem auswertung_4002.sh auf die Longmynd-Statuswerte lauscht "
                         "(Default 4002, in einem eigenen Terminal-Fenster)")
_argp.add_argument("--longmynd-status-forward-port", type=int, default=4012, metavar="PORT",
                    help="Die GUI bindet Port 4002 exklusiv und spiegelt jedes empfangene "
                         "Statuspaket unveraendert an diesen lokalen Port weiter (Default 4012) - "
                         "auswertung_4002.sh mit '-port 4012' darauf zeigen lassen, damit GUI und "
                         "Terminal-Dashboard nicht um denselben UDP-Port konkurrieren.")
_argp.add_argument("--no-longmynd-status", action="store_true",
                    help="Kein eigenes Terminal-Fenster mit dem Longmynd-Status-Dashboard oeffnen")
_argp.add_argument("--longmynd_vnc_port", type=int, default=4212, metavar="PORT", dest="longmynd_vnc_port",
                    help="VLC-Telnet-Port fuer den Snapshot-Knopf des LongMynd-Videostreams "
                         "(Default 4212)")
_argp.add_argument("-longmyndterminal", action="store_true",
                    help="Longmynd-Status-Dashboard in einem externen Terminal-Fenster anzeigen "
                         "(alte Darstellung). Ohne dieses Flag laeuft es in einem normalen, in "
                         "die GUI eingebetteten Tkinter-Fenster (Default).")
_argp.add_argument("-longmynd2", action="store_true",
                    help="Zweiten LongMynd-Receiver (WebSocket, Default 127.0.0.1:8080) als zusaetzliches "
                         "Ziel aktivieren - IP/Port per --longmynd2-ip/--longmynd2-port oder in der "
                         "Konfigdatei (app_config.json, Eintrag 'longmynd2' unter 'tuners')")
_argp.add_argument("-longmyndp4", action="store_true",
                    help="LongMynd-P4-Receiver (zweiter Pi, Tuning per SSH) als zusaetzliches Ziel aktivieren")
_argp.add_argument("--longmyndp4-ssh-host", default="192.168.0.242",
                    help="IP/Host des LongMynd-P4-Pi (Default 192.168.0.242)")
_argp.add_argument("--longmyndp4-ssh-user", default="pi",
                    help="SSH-User fuer den LongMynd-P4-Pi (Default pi)")
_argp.add_argument("--longmyndp4-remote-script", default="~/rx_udp.sh",
                    help="Pfad zum Tuning-Script auf dem LongMynd-P4-Pi (Default ~/rx_udp.sh, "
                         "aufgerufen als '<script> <Downlink-MHz> <SR-kHz>')")
# LongMynd P4: derselbe Pi wie --longmyndp4-ssh-host (Default 192.168.0.242), aber per SSH
# als dg7ndv eingeloggt (nicht root - root ist nur fuer die Octagon-Box), auf dem vor jedem
# Tuning-Kommando '/home/dg7ndv/stop.sh' und beim Beenden des Programms
# '/home/dg7ndv/start.sh' aufgerufen wird. Beim Start prueft die GUI
# (_longmyndp4_check_stop_start_scripts, in eigenem Thread) per SSH, ob beide Scripts dort
# vorhanden und ausfuehrbar (chmod +x) sind - fehlt eines, wird es automatisch mit folgendem
# Standardinhalt angelegt (_longmyndp4_create_remote_script):
#   stop.sh:  sudo systemctl stop getty@tty1
#             sudo pkill rpidatvgui
#             sudo pkill -f "scheduler.sh|bandview|langstone|siggen"
#   start.sh: sudo systemctl start getty@tty1
_argp.add_argument("--longmyndp4-stop-ssh-host", default="192.168.0.242",
                    help="IP/Host, auf dem vor jedem LongMynd-P4-Tuning-Kommando per SSH das "
                         "Stop-Script und beim Beenden das Start-Script aufgerufen wird - "
                         "derselbe Pi wie --longmyndp4-ssh-host, als dg7ndv "
                         "eingeloggt (Default 192.168.0.242)")
_argp.add_argument("--longmyndp4-stop-ssh-user", default="dg7ndv",
                    help="SSH-User fuer --longmyndp4-stop-ssh-host (Default dg7ndv - root ist "
                         "nur fuer die Octagon-Box)")
_argp.add_argument("--longmyndp4-stop-script", default="/home/dg7ndv/stop.sh",
                    help="Pfad zum Stop-Script auf --longmyndp4-stop-ssh-host, wird vor jedem "
                         "LongMynd-P4-Tuning-Kommando aufgerufen (Default /home/dg7ndv/stop.sh). "
                         "Fehlt die Datei beim Start-Check, wird sie automatisch mit Standardinhalt "
                         "angelegt und ausfuehrbar gemacht (systemctl stop getty@tty1 / pkill "
                         "rpidatvgui / pkill -f scheduler.sh|bandview|langstone|siggen).")
_argp.add_argument("--longmyndp4-start-script", default="/home/dg7ndv/start.sh",
                    help="Pfad zum Start-Script auf --longmyndp4-stop-ssh-host, wird beim Beenden "
                         "des Programms aufgerufen (Default /home/dg7ndv/start.sh). Fehlt die Datei "
                         "beim Start-Check, wird sie automatisch mit Standardinhalt angelegt und "
                         "ausfuehrbar gemacht (systemctl start getty@tty1).")
_argp.add_argument("--longmyndp4-sr-default", type=int, default=1500, metavar="kHz",
                    help="Symbolrate (kHz), falls keine uebergeben wird (Default 1500, wie im "
                         "urspruenglichen qo100rx-Script)")
_argp.add_argument("--longmyndp4-script-lo", type=float, default=9540.146, metavar="MHz",
                    help="LO (MHz), den das Tuning-Skript auf dem LongMynd-P4-Pi fest verwendet "
                         "(LO=... in ~/rx_udp.sh, Default 9540.146) - noetig fuer Direktempfang "
                         "(z.B. 437 MHz): die GUI uebergibt dann Frequenz+LO, damit das Skript "
                         "auf ZF = Frequenz kommt, und zeigt im QRG-Dialog die richtige ZF an")
_argp.add_argument("--longmyndp4-stream-ip", default="230.0.0.4",
                    help="Multicast-IP des LongMynd-P4-Videostreams (Default 230.0.0.4)")
_argp.add_argument("--longmyndp4-stream-port", type=int, default=1234,
                    help="Multicast-Port des LongMynd-P4-Videostreams (Default 1234)")
_argp.add_argument("--longmyndp4-status-port", type=int, default=4003,
                    help="UDP-Port der rohen Longmynd-Statuswerte (Carrier Frequency/Service, "
                         "wie auswertung_4003.sh) fuer LongMynd P4 (Default 4003)")
_argp.add_argument("--longmyndp4-status-forward-port", type=int, default=4013, metavar="PORT",
                    help="Wie --longmynd-status-forward-port, nur fuer LongMynd P4 (Default 4013) - "
                         "auswertung_4003.sh mit '-port 4013' darauf zeigen lassen.")
_argp.add_argument("--longmyndp4_vnc_port", type=int, default=4213, metavar="PORT", dest="longmyndp4_vnc_port",
                    help="VLC-Telnet-Port fuer den Snapshot-Knopf des LongMynd-P4-Videostreams "
                         "(Default 4213)")
# LongMynd 2: zweiter, per WebSocket gesteuerter LongMynd (Default lokal, 127.0.0.1:8080). IP/Port:
# Kommandozeile hat Vorrang, sonst der Eintrag typ "longmynd2" unter "tuners" in app_config.json,
# sonst 127.0.0.1 / 8080 (siehe _longmynd2_endpoint). ACHTUNG: Stream (230.0.0.4:1234) und
# Status-UDP (4003) sind absichtlich dieselben wie bei LongMynd P4 - LongMynd 2 und LongMynd P4
# koennen daher NICHT gleichzeitig aktiv sein (zweiter Status-Listener auf Port 4003 bekaeme
# keine Pakete, beide VLC/Aufnahmen haengen am selben Stream). Telnet/HTTP/Forward-Ports sind
# dagegen eigene (4216/8084/4014).
_argp.add_argument("--longmynd2-ip", default=None,
                    help="IP/Host des zweiten LongMynd (Default 127.0.0.1, sonst Wert aus app_config.json)")
_argp.add_argument("--longmynd2-port", type=int, default=None,
                    help="WebSocket-Port des zweiten LongMynd (Default 8080, sonst Wert aus app_config.json)")
_argp.add_argument("--longmynd2-stream-ip", default="230.0.0.4",
                    help="Multicast-IP des LongMynd-2-Videostreams (Default 230.0.0.4)")
_argp.add_argument("--longmynd2-stream-port", type=int, default=1234,
                    help="Multicast-Port des LongMynd-2-Videostreams (Default 1234)")
_argp.add_argument("--longmynd2-status-port", type=int, default=4003, metavar="PORT",
                    help="UDP-Port der rohen Longmynd-Statuswerte fuer LongMynd 2 (Default 4003) - der "
                         "zweite longmynd muss seine Status-UDP-Pakete (-I <IP> 4003) genau dorthin senden")
_argp.add_argument("--longmynd2-status-forward-port", type=int, default=4014, metavar="PORT",
                    help="Wie --longmynd-status-forward-port, nur fuer LongMynd 2 (Default 4014)")
_argp.add_argument("--longmynd2_vnc_port", type=int, default=4216, metavar="PORT", dest="longmynd2_vnc_port",
                    help="VLC-Telnet-Port fuer den Snapshot-Knopf des LongMynd-2-Videostreams (Default 4216)")
_argp.add_argument("--longmynd2-vlc-http-port", type=int, default=8084,
                    help="HTTP-Port fuer den von der GUI gestarteten VLC (LongMynd 2, Default 8084)")
_argp.add_argument("--rtlsdr-stream-ip", default="230.0.0.5",
                    help="Multicast-IP, an die der lokale rtl_sdr/leandvb-TS-Strom dauerhaft "
                         "gespiegelt wird, damit tsduck daraus das Rufzeichen lesen kann (Default 230.0.0.5)")
_argp.add_argument("--rtlsdr-stream-port", type=int, default=1234,
                    help="Multicast-Port fuer den gespiegelten rtl_sdr-TS-Strom (Default 1234)")
for _i in range(1, 9):
    _argp.add_argument("-onair%d" % _i, "--onair%d" % _i, action="store_true", dest="onair%d" % _i,
                        help="OnAir-Empfaenger %d als Spalte erzwingen (sonst laut app_config.json/Menue OpenAirRX)" % _i)
for _i in range(1, 9):
    _argp.add_argument("-datvrx%d" % _i, "--datvrx%d" % _i, action="store_true", dest="datvrx%d" % _i,
                        help="RTL-SDR-datvRX-Empfaenger %d (datvrx, RTL-Stick) als Spalte erzwingen; ohne Eintrag in app_config.json "
                             "wird er mit Stick Nr. %d angelegt (ab zwei Empfaengern werden die Sticks beim Start per USB-Suche zugeordnet); "
                             "sonst laut Menue RTL-SDR-datvRX" % (_i, _i - 1))
cliargs = _argp.parse_args()
cliargs.box = cliargs.octagon_ip
cliargs.longmynd_url = "ws://%s:%d" % (cliargs.longmynd_ip, cliargs.longmynd_port)

#===== Tuner-Konfigurationen (Menue Konfig): 10 Plaetze, Platz 1 = Standard (beim Beenden gespeichert, Start ohne Parameter) =====
#===== Mit Ziel-Parametern (-octagon, -longmynd, -onairN, -datvrxN ...) wird der Standard ignoriert und NICHT ueberschrieben. =====
_TARGET_FLAG_NAMES = (["octagon", "longmynd", "longmynd2", "longmyndp4", "rtlsdr"] + ["onair%d" % _i for _i in range(1, 9)] + ["datvrx%d" % _i for _i in range(1, 9)])
_cli_mode = any(getattr(cliargs, _f, False) for _f in _TARGET_FLAG_NAMES)     # True: Ziele per Parameter vorgegeben
# Einmalig (Menue-Neustart nach "Spalte hinzufuegen/entfernen"): die gespeicherten OnAir-/datvRX-Spalten gelten trotz Ziel-Parametern
_once_cfg = bool(os.environ.pop("LEANDVB_ONCE_CONFIG", ""))
_cli_cols_ignore = _cli_mode and not _once_cfg
_PROFILE_SLOTS = 10
_BASE_TARGETS = (("rtl_sdr", "RTL-SDR-LeanDVB", "rtlsdr"), ("octagon", "Octagon", "octagon"), ("longmynd", "LongMynd", "longmynd"),
                 ("longmynd2", "LongMynd 2", "longmynd2"), ("longmynd_p4", "LongMynd P4", "longmyndp4"))

def _profiles_early():
    try:
        with open(os.path.expanduser("~/.leandvb-GUI/app_config.json"), "r", encoding="utf-8") as _f:
            _d = json.load(_f).get("tuner_profiles") or {}
        return _d if isinstance(_d, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}

_profile_mode = False         # True: Ziele stammen aus Platz 1 (Standard)
_p1_early = _profiles_early().get("1") if not _cli_mode else None
if isinstance(_p1_early, dict) and isinstance(_p1_early.get("targets"), dict):
    _profile_mode = True
    _tg_ = _p1_early["targets"]
    cliargs.rtlsdr = bool(_tg_.get("rtl_sdr"))
    cliargs.octagon = bool(_tg_.get("octagon"))
    cliargs.longmynd = bool(_tg_.get("longmynd"))
    cliargs.longmynd2 = bool(_tg_.get("longmynd2"))
    cliargs.longmyndp4 = bool(_tg_.get("longmynd_p4"))
    if _p1_early.get("octagon_direkt") and not getattr(cliargs, "octagon_direkt_qrg", False):
        cliargs.octagon_direkt_qrg = True

# Mehrere Ziele koennen gleichzeitig aktiv sein (-octagon -longmynd -rtlsdr beliebig kombinierbar).
# Ohne jedes Flag bleibt es wie bisher: nur der lokale rtl_sdr ist aktiv.
_active_targets = []
if cliargs.rtlsdr or not (cliargs.octagon or cliargs.longmynd or cliargs.longmynd2 or cliargs.longmyndp4
                          or any(getattr(cliargs, "onair%d" % _i, False) for _i in range(1, 9))
                          or any(getattr(cliargs, "datvrx%d" % _i, False) for _i in range(1, 9))):
    _active_targets.append("rtl_sdr")
if cliargs.octagon:
    _active_targets.append("octagon")
if cliargs.longmynd:
    _active_targets.append("longmynd")
if cliargs.longmynd2:
    _active_targets.append("longmynd2")
if cliargs.longmyndp4:
    _active_targets.append("longmynd_p4")
if _profile_mode and not cliargs.rtlsdr and "rtl_sdr" in _active_targets:
    _active_targets.remove("rtl_sdr")            # Standard-Konfiguration ohne RTL-SDR-LeanDVB

_TARGET_LABEL = {"octagon": "Octagon", "longmynd": "LongMynd", "rtl_sdr": "RTL-SDR-LeanDVB",
                  "longmynd_p4": "LongMynd P4", "longmynd2": "LongMynd 2"}

# Alle Ziele, die einen Longmynd-Statusstrom (UDP) liefern - wird ueberall dort verwendet, wo
# frueher _LM_TARGETS fest eingetragen war.
_LM_TARGETS = ("longmynd", "longmynd2", "longmynd_p4")

#===== OnAir-Empfaenger (Menue "OpenAirRX"): Spalten onair1..onair8 - aus app_config.json =====
#===== ["onair_receivers"] (Eintrag N-1 = OnAirN, "enabled") oder per -onairN erzwungen.   =====
#===== app_config wird erst viel spaeter geladen, daher hier direkt aus der Datei gelesen. =====
_ONAIR_MAX = 8

def _onair_cfg_early():
    try:
        with open(os.path.expanduser("~/.leandvb-GUI/app_config.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        return [r for r in (d.get("onair_receivers") or []) if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return []

def _onair_num(t):
    return int(str(t)[5:])

def _onair_dev_short(e):
    d = e.get("device", "hackrf")
    if d == "hackrf":
        return "HackRF"
    if d == "rtl_tcp":
        return "rtl #%s" % e.get("rtl_index", 0)
    if d == "net":
        return "%s %s" % (e.get("net_type") or "net", e.get("net_host") or "")
    if d == "sdrplay":
        a = e.get("sdr_ant") or ""
        return "SDRplay" + ((" " + a) if a and a != "-" else "")
    return "eigene"

def _onair_udp(t):
    u = str((_ONAIR_RX.get(t) or {}).get("udp") or "").strip()
    if not u or ":" not in u:
        u = "230.0.0.%d:1234" % (9 + _onair_num(t))
    ip, _sep, port = u.rpartition(":")
    try:
        return ip, int(port)
    except ValueError:
        return ip, 1234

def _onair_port_cfg(t, key, base):
    try:
        return int((_ONAIR_RX.get(t) or {}).get(key) or 0) or (base + _onair_num(t))
    except (TypeError, ValueError):
        return base + _onair_num(t)

def _onair_api_port(t):
    return _onair_port_cfg(t, "api_port", 8770)

def _onair_telnet_port(t):
    return _onair_port_cfg(t, "vlc_telnet_port", 4220)

def _onair_http_port(t):
    return _onair_port_cfg(t, "vlc_http_port", 8090)

def _onair_lo(t):
    try:
        return float((_ONAIR_RX.get(t) or {}).get("lo") or 9540.0)
    except (TypeError, ValueError):
        return 9540.0

_ONAIR_RX = {}    # target ("onair1", ...) -> Konfig-Eintrag
_onair_list_early = _onair_cfg_early()
for _i in range(1, _ONAIR_MAX + 1):
    _flag = bool(getattr(cliargs, "onair%d" % _i, False))
    _entry = _onair_list_early[_i - 1] if _i <= len(_onair_list_early) else None
    if _entry is None and _flag:
        _entry = {"device": "hackrf", "enabled": True}
    if _entry is None or not (_flag or (_entry.get("enabled", True) and not _cli_cols_ignore)):
        continue
    _ot = "onair%d" % _i
    _ONAIR_RX[_ot] = _entry
    _active_targets.append(_ot)
    _TARGET_LABEL[_ot] = "OnAir%d (%s)" % (_i, _onair_dev_short(_entry))
_ONAIR_TARGETS = tuple(_ONAIR_RX.keys())

#===== datvRX-Empfaenger (Menue "datvRX"): Spalten datvrx1..datvrx8 - Quelle (RTL-Stick, HackRF, Pluto, SDRplay, Soapy) =====
#===== | datvrx (HB9IIU DATVision, DVB-S/S2 von OnAir) ueber DATVision/datvrx_rx.sh; TS per UDP an eine eigene Adresse.   =====
_DATVRX_MAX = 8
_DATVRX_SRC_LABEL = {"rtl-sdr": "RTL", "hackrf": "HackRF", "pluto": "Pluto", "sdrplay": "SDRplay", "soapy": "Soapy", "dienst": "RTL-Dienst"}

def _dvx_cfg_early():
    try:
        with open(os.path.expanduser("~/.leandvb-GUI/app_config.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        return [r for r in (d.get("datvrx_receivers") or []) if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return []

def _dvx_num(t):
    return int(str(t)[6:])

def _dvx_dev_short(e):
    src = e.get("source", "rtl-sdr")
    lab = _DATVRX_SRC_LABEL.get(src, src)
    if src == "rtl-sdr":
        return "%s #%s" % (lab, e.get("index", 0))
    if src == "dienst":                  # Stick des RTL-SDR-datvRX-Dienstes (Pi): Menue datvRX -> RTL-Dienst verbinden
        return "%s RTL%s" % (lab, e.get("stick", 1))
    if src == "pluto":
        return "%s %s" % (lab, e.get("host") or "192.168.2.1")
    if src == "sdrplay":
        return "%s %s" % (lab, e.get("ant") or "A")
    return lab

def _dvx_udp(t):
    u = str((_DATVRX_RX.get(t) or {}).get("udp") or "").strip()
    if not u or ":" not in u:
        u = "230.0.0.%d:1234" % (20 + _dvx_num(t))
    ip, _sep, port = u.rpartition(":")
    try:
        return ip, int(port)
    except ValueError:
        return ip, 1234

def _dvx_telnet_port(t):
    return 4230 + _dvx_num(t)

def _dvx_http_port(t):
    return 8100 + _dvx_num(t)

_DATVRX_RX = {}    # target ("datvrx1", ...) -> Konfig-Eintrag
_dvx_cfg_list = _dvx_cfg_early()[:_DATVRX_MAX]
for _i in range(1, _DATVRX_MAX + 1):
    _forced = bool(getattr(cliargs, "datvrx%d" % _i, False))                    # -datvrxN auf der Kommandozeile
    _entry = _dvx_cfg_list[_i - 1] if _i <= len(_dvx_cfg_list) else None
    if _entry is None:
        if not _forced:
            continue
        _entry = {"source": "rtl-sdr", "index": _i - 1, "gain": 40}             # nur per Kommandozeile erzwungen (nicht in app_config.json)
    elif (not _entry.get("enabled", True) or _cli_cols_ignore) and not _forced:
        continue
    _dt = "datvrx%d" % _i
    _DATVRX_RX[_dt] = _entry
    _active_targets.append(_dt)
    if _entry.get("source", "rtl-sdr") == "rtl-sdr":        # RTL-SDR-Stick: Nummer = Empfaenger (spaeter mehrere Sticks: 2, 3, 4 ...)
        _TARGET_LABEL[_dt] = "RTL-SDR-datvRX%d (Stick #%s)" % (_i, _entry.get("index", 0))
    elif _entry.get("source") == "dienst":
        _TARGET_LABEL[_dt] = "RTL-Dienst RTL%s (datvRX%d)" % (_entry.get("stick", 1), _i)
    else:
        _TARGET_LABEL[_dt] = "datvRX%d (%s)" % (_i, _dvx_dev_short(_entry))
_DATVRX_TARGETS = tuple(_DATVRX_RX.keys())
_UDPRX_TARGETS = _ONAIR_TARGETS + _DATVRX_TARGETS     # Empfaenger mit eigenem UDP-Strom (OnAir + datvRX)

def _rx_udp(t):
    return _dvx_udp(t) if t in _DATVRX_TARGETS else _onair_udp(t)

def _rx_telnet_port(t):
    return _dvx_telnet_port(t) if t in _DATVRX_TARGETS else _onair_telnet_port(t)

def _rx_http_port(t):
    return _dvx_http_port(t) if t in _DATVRX_TARGETS else _onair_http_port(t)


# FEC-Auswahl: DVB-S nutzt --cr, DVB-S2 wird auf eine --modcods-Maske (QPSK) abgebildet
# "auto"   -> DVB-S2: --modcods weglassen (leandvb erkennt selbst)
# "manual" -> DVB-S2: modcods-Feld aus den Settings verwenden
FEC_VALUES     = ("auto","manual","1/4","1/3","2/5","1/2","3/5","2/3","3/4","4/5","5/6","6/7","7/8","8/9","9/10")
DVBS_FEC       = ("1/2","2/3","3/4","5/6","6/7","7/8")
S2_QPSK_MODCOD = {"1/4":1,"1/3":2,"2/5":3,"1/2":4,"3/5":5,"2/3":6,"3/4":7,"4/5":8,"5/6":9,"8/9":10,"9/10":11}

# QO-100 Schmalband-Bandplan (Downlink MHz, uebliche Symbolraten dort)
# (Downlink MHz, Anzeigetext, "primaere" Symbolrate kS/s = hoechste dort uebliche Rate)
QO100_CHANNELS = (
    (10491.50, "Bake 1500 kS", 1500),
    (10492.75, "500/333/250 kS", 500),
    (10493.00, "125 kS", 125),
    (10493.25, "1000 kS W1 / 500/333/250/125 kS", 1000),
    (10493.50, "125 kS", 125),
    (10493.75, "500/333/250/125 kS", 500),
    (10494.00, "125 kS", 125),
    (10494.25, "500/333/250/125 kS", 500),
    (10494.50, "125 kS", 125),
    (10494.75, "1000 kS W2 / 500/333/250/125 kS", 1000),
    (10495.00, "125 kS", 125),
    (10495.25, "500/333/250/125 kS", 500),
    (10495.50, "125 kS", 125),
    (10495.75, "500/333/250/125 kS", 500),
    (10496.00, "125 kS", 125),
    (10496.25, "1000 kS W3 / 500/333/250/125 kS", 1000),
    (10496.50, "125 kS", 125),
    (10496.75, "500/333/250/125 kS", 500),
    (10497.00, "125 kS", 125),
    (10497.25, "333/250/125 kS", 333),
    (10497.50, "125 kS", 125),
    (10497.75, "333/250/125 kS", 333),
    (10498.00, "125 kS", 125),
    (10498.25, "333/250/125 kS", 333),
    (10498.50, "125 kS", 125),
    (10498.75, "333/250/125 kS", 333),
    (10499.00, "125 kS", 125),
    (10499.25, "333/250 kS Netz / 125 kS", 333),
)

# grobe, sichere HF-Bandbreite (kHz) je Symbolrate (Faktor ca. 1,6, wie bei der Bake 1500->2400)
QO100_BW_BY_SR = {1500: 2400, 1000: 1600, 500: 800, 333: 550, 250: 400, 125: 250}

QO100_LABELS = ["%9.3f MHz   %s" % (f, l) for (f, l, sr) in QO100_CHANNELS]
QO100_INFO_BY_LABEL = dict(zip(QO100_LABELS, [(f, sr) for (f, l, sr) in QO100_CHANNELS]))

# Uplink = Downlink - 8089,5 MHz (z.B. 10491.500 -> 2402.000; Schmalband 10489.550 -> 2400.050)
QO100_UPLINK_OFFSET_MHZ = 8089.5
# Direktempfang (ohne LNB): Schnellwahl-Frequenzen im QRG-Dialog (MHz, Bandname)
QRG_DIRECT_PRESETS = ((144.5, "2 m"), (437.0, "70 cm"), (1291.0, "23 cm"), (2355.0, "13 cm"), (2380.0, "13 cm"))
_LM_DIRECT_RANGE_MHZ = (143.0, 2450.0)   # LongMynd-Tuner: Direktempfang von 143 bis 2450 MHz
_DIRECT_BELOW_MHZ = 5000.0   # alles darunter kann kein Ku-Band-Downlink sein -> Direktempfang

def _is_direct_freq(freq_mhz):
    """True fuer Direktempfang (z.B. 437 MHz): kein LNB, die Tuner-ZF ist gleich der Frequenz."""
    return freq_mhz is not None and freq_mhz < _DIRECT_BELOW_MHZ

def _target_lo_mhz(target, freq_mhz=None):
    """LNB-LO (MHz), mit dem ein Longmynd-Ziel auf freq_mhz tunt - 0 bei Direktempfang."""
    if _is_direct_freq(freq_mhz):
        return 0.0
    return float(lnblo.get())

# settings for auxiliary files (parameters, run, stop)
parameters_path = os.path.expanduser("~/") + ".leandvb-GUI/"
parameters_file = parameters_path + "parameters.json"
run_script      = parameters_path + "run.sh"
presets_file    = parameters_path + "presets.json"
stop_script     = parameters_path + "stop.sh"

# show settings for auxiliary files (parameters, run, stop)
print ("parameters path:", parameters_path)
print ("parameters file:",  parameters_file)
print ("run script     :",  run_script)
print ("stop script    :",  stop_script)

# create parameters path if not existend
if not os.path.exists(parameters_path):
    print( "create " + parameters_path)
    os.mkdir(parameters_path)

# check max pipe size and adjust if needed

max_needed = 32000000

fd = open("/proc/sys/fs/pipe-max-size", "r")
max_current = int(fd.readline())
fd.close()

if (max_current < max_needed):
    print( "max pipe size  :", max_current, ", will be set to", max_needed)
    cmd = "bash -c 'echo " + str(max_needed) + " > /proc/sys/fs/pipe-max-size'"
    os.system("pkexec " + cmd)
else:
    print( "max pipe size  :", max_current, ", this is ok")

#===== handle parameters (save, load, default) ================================

parameters = dict()

def _safe_num(getter, cast, fallback):
    """Wandelt den Wert eines Tk-Feldes (Entry/Combobox) sicher in float/int um - faellt bei
    ungueltigem Inhalt (z.B. versehentlich eingegebener/eingefuegter Text statt einer Zahl) auf
    den letzten bekannten guten Wert zurueck, statt beim Speichern/Beenden abzustuerzen."""
    try:
        return cast(getter())
    except (ValueError, tk.TclError) as e:
        dlog("Ungueltiger Feldwert (%s) - verwende zuletzt gespeicherten Wert %r: %s" % (getter, fallback, e))
        return fallback

def parameters_save():
    print ("save parameters to file")
    parameters["frequency"      ] = _safe_num(frequency.get, float, parameters.get("frequency", 10491.500))
    parameters["symbolrate"     ] = _safe_num(symbolrate.get, int, parameters.get("symbolrate", 1500))
    parameters["fec"            ] = fec.get()
    parameters["tune"           ] = _safe_num(tune.get, int, parameters.get("tune", 0))
    parameters["fastlock"       ] = bool(fastlock.get())
    parameters["bandwidth"      ] = _safe_num(bandwidth.get, int, parameters.get("bandwidth", 2400))
    parameters["viterbi"        ] = bool(viterbi.get())
    parameters["gui"            ] = bool(gui.get())
    parameters["maxsens"        ] = bool(maxsens.get())
    # Die beiden rtl_sdr-Options-Checkboxen ("FEC von LongMynd"/"Offset korrigieren", siehe
    # GUI-Aufbau weiter unten) sind nur bei rtl_sdr aktiv angelegt - ausserhalb (z.B. andere
    # Ziel-Kombination) bleibt der zuletzt gespeicherte Wert einfach unveraendert stehen.
    parameters["offset_correct"    ] = bool(_offset_correct_var.get()) if _offset_correct_var is not None else bool(parameters.get("offset_correct", False))
    parameters["fec_sync_longmynd" ] = bool(_fec_sync_var.get()) if _fec_sync_var is not None else bool(parameters.get("fec_sync_longmynd", False))
    parameters["hardmetric"     ] = bool(hardmetric.get())
    parameters["leandvb_path"   ] = leandvb_path.get()
    parameters["leandvb_file"   ] = leandvb_file.get()
    parameters["ppm"            ] = _safe_num(ppm.get, int, parameters.get("ppm", 0))
    parameters["gain"           ] = gain.get()
    parameters["viewer_path"    ] = viewer_path.get()
    parameters["viewer_file"    ] = viewer_file.get()
    parameters["rolloff"        ] = _safe_num(rolloff.get, float, parameters.get("rolloff", 0.2))
    parameters["rrcrej"         ] = _safe_num(rrcrej.get, float, parameters.get("rrcrej", 30))
    parameters["nhelpers"       ] = _safe_num(nhelpers.get, int, parameters.get("nhelpers", 0))
    parameters["inpipe"         ] = inpipe.get()
    parameters["modcods"        ] = modcods.get()
    parameters["framesizes"     ] = framesizes.get()
    parameters["lnb_lo"         ] = _safe_num(lnblo.get, float, parameters.get("lnb_lo", 9750.0))
    parameters["rtldongle"      ] = rtldongle.get()
    parameters["standard"       ] = standard.get()
    parameters["sampler"        ] = sampler.get()
    parameters["strongpls"      ] = bool(strongpls.get())
    parameters["fastdrift"      ] = bool(fastdrift.get())
    parameters["ldpc_bf"        ] = _safe_num(ldpc_bf.get, int, parameters.get("ldpc_bf", 0))
    parameters["ldpchelper_path"] = ldpchelper_path.get()
    parameters["ldpchelper_file"] = ldpchelper_file.get()
    parameters["ldpchelper_enable"] = bool(ldpchelper_enable.get())
    parameters["freq_offset_48k"] = bool(freq_offset_48k.get())
    parameters["constellation"  ] = const.get()
    parameters["debug"          ] = debug.get()
    parameters["rtlsdr_path"    ] = rtlsdr_path.get()
    parameters["rtlsdr_file"    ] = rtlsdr_file.get()
    parameters["batc_auto"      ] = {t: bool(v.get()) for t, v in _batc_auto.items() if v is not None}
    parameters["batc_auto_master"] = bool(batc_auto_master.get())
    parameters["spectrum_lines" ] = list(_spectrum_line_config)

    file = open(parameters_file, "w")
    file.write(json.dumps(parameters, indent=4, sort_keys=True))
    file.close()

def parameters_load():
    global parameters
    print( "load parameters from file")
    file = open(parameters_file, "r")
    parameters = json.load(file)
    file.close()
    guivars_init()

def parameters_default():
    print ("load parameters with defaults")
    parameters["frequency"      ] = 10491.500
    parameters["symbolrate"     ] = 1500
    parameters["fec"            ] = "1/2"
    parameters["tune"           ] = 0
    parameters["fastlock"       ] = False
    parameters["bandwidth"      ] = 2400
    parameters["viterbi"        ] = False
    parameters["gui"            ] = True
    parameters["maxsens"        ] = False
    parameters["offset_correct"    ] = False
    parameters["fec_sync_longmynd" ] = False
    parameters["hardmetric"     ] = False
    parameters["leandvb_path"   ] = "./"
    parameters["leandvb_file"   ] = "leandvb"
    parameters["ppm"            ] = 0
    parameters["gain"           ] = 36
    parameters["viewer_path"    ] = ""
    parameters["viewer_file"    ] = "ffplay -v 0"
    parameters["rolloff"        ] = 0.35
    parameters["rrcrej"         ] = 30.0
    parameters["nhelpers"       ] = 6
    parameters["inpipe"         ] = 32000000
    parameters["modcods"        ] = "0x0040"
    parameters["framesizes"     ] = "0x01"
    parameters["lnb_lo"         ] = 9750.0
    parameters["rtldongle"      ] = 0
    parameters["standard"       ] = "DVB-S2"
    parameters["sampler"        ] = "rrc"
    parameters["strongpls"      ] = False
    parameters["fastdrift"      ] = False
    parameters["ldpc_bf"        ] = 0
    parameters["ldpchelper_path"] = "./"
    parameters["ldpchelper_file"] = "ldpc_tool"
    parameters["ldpchelper_enable"] = True
    parameters["freq_offset_48k"] = False
    parameters["constellation"  ] = "QPSK"
    parameters["debug"          ] = "all"
    parameters["rtlsdr_path"    ] = ""
    parameters["rtlsdr_file"    ] = "rtl_sdr"
    parameters["batc_auto"      ] = {}
    parameters["batc_auto_master"] = True
    parameters["spectrum_lines" ] = ["callsign", "fec", "off", "off"]
    guivars_init()

def guivars_init():
    print( "initialize GUI variables")
    bandwidth      .set(parameters["bandwidth"])
    const          .set(parameters["constellation"])
    debug          .set(parameters["debug"])
    fastdrift      .set(parameters["fastdrift"])
    fastlock       .set(parameters["fastlock"])
    fec            .set(parameters["fec"])
    framesizes     .set(parameters["framesizes"])
    frequency      .set(parameters["frequency"])
    gain           .set(parameters["gain"])
    gui            .set(parameters["gui"])
    hardmetric     .set(parameters["hardmetric"])
    inpipe         .set(parameters["inpipe"])
    ldpc_bf        .set(parameters["ldpc_bf"])
    ldpchelper_file.set(parameters["ldpchelper_file"])
    ldpchelper_path.set(parameters["ldpchelper_path"])
    ldpchelper_enable.set(parameters.get("ldpchelper_enable", True))
    freq_offset_48k.set(parameters.get("freq_offset_48k", False))
    leandvb_file   .set(parameters["leandvb_file"])
    leandvb_path   .set(parameters["leandvb_path"])
    lnblo          .set(parameters["lnb_lo"])
    maxsens        .set(parameters["maxsens"])
    modcods        .set(parameters["modcods"])
    nhelpers       .set(parameters["nhelpers"])
    ppm            .set(parameters["ppm"])
    rolloff        .set(parameters["rolloff"])
    rrcrej         .set(parameters["rrcrej"])
    rtldongle      .set(parameters["rtldongle"])
    rtlsdr_file    .set(parameters["rtlsdr_file"])
    rtlsdr_path    .set(parameters["rtlsdr_path"])
    sampler        .set(parameters["sampler"])
    standard       .set(parameters["standard"])
    strongpls      .set(parameters["strongpls"])
    symbolrate     .set(parameters["symbolrate"])
    tune           .set(parameters["tune"])
    viewer_file    .set(parameters["viewer_file"])
    viewer_path    .set(parameters["viewer_path"])
    viterbi        .set(parameters["viterbi"])
    global _spectrum_line_config
    _spectrum_line_config = list(parameters.get("spectrum_lines", ["callsign", "fec", "off", "off"]))
    while len(_spectrum_line_config) < 4:
        _spectrum_line_config.append("off")

#===== BATC QO-100 Signal-Scanner (Kernroutinen aus octagon_autom.py uebernommen) =============
# WebSocket-FFT des BATC-Wideband-Empfaengers: liefert laufend Spektren, daraus werden aktive
# Signale erkannt und rechts als Buttons angezeigt. Ein Klick uebernimmt Frequenz/Symbolrate/
# Bandbreite und startet rtl_sdr+leandvb neu. (Noch OHNE -octagon/-longmynd; das ist Schritt 2.)

BATC_FFT_URL       = "wss://eshail.batc.org.uk/wb/fft"
BATC_START_FREQ    = 10490.5     # MHz, linker Rand des BATC-Spektrumbilds
BATC_BANDWIDTH     = 9.0         # MHz, Breite des BATC-Spektrumbilds
BATC_MIN_BW_KHZ    = 80          # schmalere "Signale" sind Rauschspitzen
BATC_BEACON_MHZ    = 10491.52    # die Bake wird aus der normalen Signalliste ausgeblendet
BATC_BEACON_TOL    = 0.06
BATC_BEACON_NOMINAL_MHZ = 10491.500   # offizielle Bake-Frequenz (wie im Bake-Knopf-Text) - Referenz fuer die Abweichungsanzeige
BATC_SIG_TOL       = 0.2         # MHz: Signale dichter dran gelten als dasselbe Signal
BATC_WINDOW        = 5.0         # s: ueber dieses Zeitfenster werden BATC-Bilder zusammengefasst
BATC_SR_STEPS      = (250, 333, 500, 1000, 2000)
BATC_SR_BW_LIMITS  = (305, 425, 750, 1500)   # Signalbreite (kHz) -> Grenze zwischen den Symbolraten

def _sr_for_freq(freq, default):
    """Symbolrate aus der Spektrum-Signalerkennung (kennt auch 125/66 kS), sonst default (BATC-Breite).
    Die Breiten-Tabelle der BATC-Liste kennt nur 250..2000 kS - schmale 125-kS-Signale wurden dadurch als
    250 kS eingestellt und lockten nie."""
    try:
        best = None
        for sg in (_spectrum_last_detected or []):
            d = abs(sg["freq"] - freq)
            if d <= 0.1 and (best is None or d < best[0]):
                best = (d, int(round(sg["sr_mhz"] * 1000)))
        if best and best[1] in (35, 66, 125, 250, 333, 500, 1000, 1500, 2000):
            return best[1]
    except Exception:
        pass
    return default

def batc_sr_from_bw(bw_khz):
    for limit, sr in zip(BATC_SR_BW_LIMITS, BATC_SR_STEPS):
        if bw_khz < limit:
            return sr
    return BATC_SR_STEPS[-1]

_batc_lock    = threading.RLock()
_batc_last_fft = None            # letztes rohes FFT-Bild (Tupel von uint16) - fuer die Spektrumanzeige
_batc_frames  = deque()          # (Zeit, [(f, bw), ...]) je BATC-Bild
_batc_stable  = []                # aktuelle, entprellte Signalliste [(f, bw), ...]
_batc_started = False
_batc_ws = [None]              # aktuelle WebSocketApp (fuer Watchdog/Refresh: close() erzwingt Neuverbindung)
_batc_last_msg = [0.0]         # Zeitpunkt der letzten empfangenen BATC-FFT-Nachricht
# Spektrum-Quelle: "batc" (BATC-Server), "stick" (eigener RTL-Stick, HB9IIU-Server) oder "auto" (BATC, bei Ausfall Stick). "active" = woher die Bilder gerade kommen.
_spq = {"mode": "batc", "active": "batc", "auto_started": False, "batc_t": 0.0, "t0": time.time()}

_batc_beacon_frames = deque()    # (Zeit, f) je im BATC-Spektrum erkannter Bake-Peak (separat von
                                  # der normalen Signalliste, aus der die Bake ausgeblendet wird)

BATC_NEW_GREEN_S = 30    # s: neu erkanntes Signal bleibt so lange gruen
BATC_GONE_RED_S  = 10    # s: verschwundenes Signal bleibt so lange rot, dann Loeschung aus der Liste

# Zusatz-Status je Signal fuer die Ampel-Faerbung der Knopfliste:
# key (gerundete Frequenz bei Ersterkennung) -> {"freq","bw","sr","born","gone_since"}
_batc_sig_state = {}

# Automatik: je Ziel ein Haekchen (tk.BooleanVar, unten in der GUI angelegt) und der Key des
# Signals, auf das dieses Ziel zuletzt gestellt wurde (manuell oder automatisch) - fuer die
# Frei-Erkennung ("frei" = noch nie getuned, Signal weg/rot, oder Signal ganz aus der Liste).
_batc_auto        = {t: None for t in _active_targets}   # wird unten mit tk.BooleanVar() befuellt
_target_last_tuned = {t: None for t in _active_targets}
_target_cur_freq  = {t: None for t in _active_targets}    # zuletzt an dieses Ziel geschickte Frequenz (MHz)
_target_cur_sr    = {t: None for t in _active_targets}    # zuletzt an dieses Ziel geschickte Symbolrate (kS/s) - fuer Scan-Rueckkehr

VERTEILER_LOG = os.path.join(parameters_path, "verteiler.log")
OCTAGON_FEHLER_LOG = os.path.join(parameters_path, "octagon_fehler.log")

# Allgemeine, von Hand editierbare Konfigdatei (getrennt von parameters.json, das die
# GUI-Tk-Variablen spiegelt) - fuer Einstellungen, die nicht an ein bestimmtes GUI-Feld
# gebunden sind. Bisher nur der VLC-Snapshot-Ordner, es kommen laut Nutzer noch weitere dazu.
APP_CONFIG_FILE = os.path.join(parameters_path, "app_config.json")
app_config = {}

def app_config_default():
    """Tuner-Reihenfolge wie in der GUI/den Spalten: 1=LongMynd, 2=LongMynd 2, 3=LongMynd P4,
    4=Octagon, 5=rtl_sdr (lokal, daher ohne IP/Port). "lo" (LNB-Lokaloszillator in MHz) ist pro Tuner
    editierbar; leer ("") heisst: LO von Tuner 1 (LongMynd) wird uebernommen - siehe
    tuner_lo_for(). IP/Port werden mit den aktuellen Kommandozeilen-Werten vorbelegt, damit
    hier beim ersten Start nichts von dem abweicht, was gerade per CLI-Flags laeuft; diese
    Konfigdatei wird fuer die LongMynd-Tuner (IP/Ports, siehe tuner_config_apply) ausgelesen;
    Kommandozeilen-Optionen haben dabei Vorrang. "lo" wird noch nicht ausgewertet."""
    return {
        "vlc_snapshot_path": os.path.expanduser("~/Bilder/Bildschirmfotos/QO100"),
        # OnAir-Empfaenger (Menue OpenAirRX): Eintrag N-1 = Spalte OnAirN
        "onair_receivers": [],
        "onair_dir": "/home/dg7ndv/batch/onair",
        "onair_script": "onair_dvbs2_cli.py",
        "onair_abgleich_zeit": 10,     # Sekunden je Probe beim Auto-Abgleich (9 Proben, Skript verlangt min. 10)
        "onair_bw_faktor": 1.4,
        "onair_show_dbm": True,        # dBm-Schaetzung im Pegel-Chip der Spalte
        "onair_dbm_offset": 0.0,       # Kalibrierwert: dBm ~ dBFS - Gain + Offset
        "onair_app_port": 8765,
        "onair_app_autostart": False,  # (alt) eigenes Fenster+Konfig je Empfaenger
        "onair_app_start": True,
        "onair_app_variant": "auto",   # auto | installiert | gepatcht (Menue Konfig > OnAir-Programm)
        "onair_app_patched_bin": "",   # leer = <GUI-Ordner>/OnAir/build/dect2       # GUI startet bei Bedarf EIN gemeinsames OnAir-Fenster (normale Konfig)
        "onair_app_rate_auto": True,   # API-Modus: Abtastrate aus der Symbolrate setzen (SR x Faktor, 2..10 Msps)
        "onair_app_rate_faktor": 4.0,  # True: GUI startet je Empfaenger ein eigenes OnAir-Fenster (eigene Konfig)        # Remote-API des OnAir-Fensters (API-Modus)
        "onair_app_bin": "",           # Programm des OnAir-Fensters (leer = onair/dect2 im PATH suchen)        # ohne AutoBW: --bw = Symbolrate x Faktor (500 kS -> 0,7 MHz)
        # Schaltbare Steckdose fuer den SDR (Delock mit Tasmota-Firmware): HTTP-Kommando
        # <url>/cm?cmnd=Power%20On|Off|Toggle ; "topic" ist nur der MQTT-Name (Info, wird nicht benoetigt)
        "sdr_power_url": "http://192.168.0.130",
        "sdr_power_topic": "delock4",
        # Octagon-Direktmodus: Box ohne Enigma als frei abstimmbarer Tuner (dvb_direct.py laeuft
        # dort als Dauerlaeufer und nimmt per UDP "TUNE <MHz> <kS/s>" entgegen). Damit entfaellt
        # die Bindung an die Kanalliste - der Fehler "Kein passender Kanal" kann nicht mehr auftreten.
        "octagon_direct": False,
        "stick_spectrum_script": "~/sdr/dvbs_tx_dg7ndv/DATVreceiver/rtl-sdr-server-source/main_lo9540.py",
        "stick_spectrum_corr_khz": 111.0,
        "stick_spectrum_port": 7681,
        "spectrum_gradient": False,   # Farbverlauf im BATC-Spektrum (oben); das Scan-Spektrum unten ist immer farbig
        "stick_spectrum_min_contrast_db": 2.5,   # Signale im Scan-Spektrum unter diesem Abstand zum lokalen Rauschen ausblenden
        "octagon_direct_script": "/tmp/dvb_direct.py",
        "octagon_direct_ctrl_port": 55600,
        "octagon_direct_udp": "230.0.0.9:1234",
        "octagon_direct_lo": 9540.0,
        "octagon_direct_pol": "h",
        "octagon_direct_frontend": 0,
        "tuners": [
            # LongMynd, LongMynd 2 und LongMynd P4 sind funktional identisch (nur die Frequenz-
            # uebergabe unterscheidet sich: WebSocket bzw. SSH-Skript) - darum haben alle drei
            # denselben Satz Schluessel, je Tuner getrennt editierbar:
            #   ip/port ............ WebSocket-Ziel (P4: ip = SSH-Host des Pi, port leer)
            #   stream_ip/_port .... Multicast-Videostream
            #   status_port ........ UDP-Port der rohen Longmynd-Statuswerte (GUI lauscht dort)
            #   status_forward_port  lokaler Spiegel-Port fuer auswertung_*.sh
            #   vnc_port ........... VLC-Telnet-Port (Snapshot/Mute/Resync)
            #   vlc_http_port ...... HTTP-Port des von der GUI gestarteten VLC
            # Kommandozeilen-Optionen haben Vorrang vor diesen Werten (siehe tuner_config_apply).
            {"typ": "longmynd",    "ip": cliargs.longmynd_ip,       "port": cliargs.longmynd_port,
             "stream_ip": cliargs.longmynd_stream_ip, "stream_port": cliargs.longmynd_stream_port,
             "status_port": cliargs.longmynd_status_port,
             "status_forward_port": cliargs.longmynd_status_forward_port,
             "vnc_port": cliargs.longmynd_vnc_port, "vlc_http_port": cliargs.longmynd_vlc_http_port,
             "lo": ""},
            {"typ": "longmynd2",   "ip": cliargs.longmynd2_ip or "127.0.0.1",
             "port": cliargs.longmynd2_port or 8080,
             "stream_ip": cliargs.longmynd2_stream_ip, "stream_port": cliargs.longmynd2_stream_port,
             "status_port": cliargs.longmynd2_status_port,
             "status_forward_port": cliargs.longmynd2_status_forward_port,
             "vnc_port": cliargs.longmynd2_vnc_port, "vlc_http_port": cliargs.longmynd2_vlc_http_port,
             "lo": "",
             # Betriebsart (Menue Konfig > LongMynd 2): "lokal" = longmynd laeuft auf diesem Rechner (127.0.0.1),
             # wird bei Bedarf per Startskript in eigenem Terminal gestartet; "extern" = anderer Rechner
             "modus": "lokal",
             "lokal_ip": "127.0.0.1", "lokal_port": 8080,
             "extern_ip": "192.168.0.103", "extern_port": 8081,
             "lokal_autostart": True,
             "lokal_dir": "/home/dg7ndv/sdr/ur8us_longmynd", "lokal_script": "start_longmynd2.sh"},
            {"typ": "longmynd_p4", "ip": cliargs.longmyndp4_ssh_host, "port": "",
             "stream_ip": cliargs.longmyndp4_stream_ip, "stream_port": cliargs.longmyndp4_stream_port,
             "status_port": cliargs.longmyndp4_status_port,
             "status_forward_port": cliargs.longmyndp4_status_forward_port,
             "vnc_port": cliargs.longmyndp4_vnc_port, "vlc_http_port": cliargs.longmyndp4_vlc_http_port,
             "script_lo": cliargs.longmyndp4_script_lo,
             "lo": ""},
            {"typ": "octagon",     "ip": cliargs.octagon_ip,        "port": cliargs.octagon_stream_port,  "lo": ""},
            {"typ": "rtl_sdr",     "ip": "",                        "port": "",                           "lo": ""},
        ],
        # Rufzeichenerkennung bei LongMynd/LongMynd P4: "longmynd_udp" liest das vom
        # auswertung_4002.sh-Statusstrom bereits decodierte Service-Feld (ID 13, siehe
        # _lm_status_vals) - schnell, kein eigener ffprobe-Aufruf noetig, daher auch beim
        # Scan-Knopf bevorzugt. "stream_ip" probt stattdessen selbst per ffprobe den
        # Multicast-Videostream (langsamer, aber unabhaengig vom Status-Stream) - siehe
        # _poll_service_name_once() und das Auswahlfeld in _spectrum_settings_open().
        "lm_callsign_source": "longmynd_udp",
        # Fester Zusatz am Ende des "Report"-Knopfs (SigReport, siehe _signal_report_copy) -
        # entspricht REPORT_SUFFIX in auswertung_4002.sh, hier aber in der GUI-Konfig editierbar.
        "report_suffix": "1.8m Dish",
        # Zusatzwerte in der VLC-Marquee-Einblendung (rtl_sdr/LongMynd/LongMynd P4), siehe
        # _marquee_text_for()/_marquee_settings_open() - Rufzeichen steht immer mit dabei,
        # unabhaengig von dieser Liste.
        "marquee_fields": ["mer", "dwert"],
        # Einblendung (Marquee) im VLC der Ziele rtl_sdr/LongMynd/LongMynd P4 ueberhaupt anzeigen (Standard: aus,
        # das Rufzeichen steht schon in der Fenstertitelzeile) - Konfig -> Status...
        "marquee_on": False,
        # QRG-Dialog (LongMynd/LongMynd 2/LongMynd P4/Octagon): 10 gemeinsame Speicher
        # {"freq": MHz, "sr": kS/s} bzw. null, und ob eine Auswahl sofort gesetzt wird.
        "qrg_memory": [None] * 10,
        "qrg_auto_apply": True,
        # Player-Ueberwachung (Auto-Start/Neustart, ps -ax + Port-/UDP-Check) und
        # Fenster-Anordnung der Player auf dem rechten Monitor (Menue Konfig -> Fenster-Anordnung).
        "player_watchdog": True,
        "window_auto_arrange": True,
        "window_main_monitor": 3,   # Monitor (Nr./Name) fuer das GUI-Hauptfenster: rechts unten in der Ecke; leer = nicht verschieben
        "window_monitor": 3,   # Ziel-Monitor fuer die Player: Nummer (1-basiert, Reihenfolge wie im Dialog) oder Ausgangsname (z.B. "HDMI-1"); leer = erster Monitor
        "window_auto_scheme": "",
        "window_layout_auto": True,     # Player-Fensterlagen je Monitoranzahl selbst merken ("System 3 Monitore")
        "window_schemes": {},
    }

def tuner_lo_for(index):
    """LO (MHz) fuer den Tuner an Position 'index' (0-basiert, siehe app_config["tuners"]) -
    der dort eingetragene Wert, oder - wenn dort leer gelassen - der LO von Tuner 1
    (Index 0, LongMynd). Liefert None, wenn auch Tuner 1 keinen gueltigen LO eingetragen hat."""
    tuners = app_config.get("tuners") or []
    def _as_float(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    if 0 <= index < len(tuners):
        own = _as_float(tuners[index].get("lo"))
        if own is not None:
            return own
    if tuners:
        return _as_float(tuners[0].get("lo"))
    return None

def app_config_load():
    """Laedt app_config.json, ergaenzt fehlende Schluessel (z.B. nach einem Update, das neue
    Einstellungen hinzufuegt) mit ihren Defaults, und schreibt die Datei gleich wieder weg -
    so sieht man beim ersten Start sofort alle verfuegbaren Schluessel in der Datei stehen,
    auch wenn man noch nichts darin geaendert hat."""
    global app_config
    merged = app_config_default()
    try:
        with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
            merged.update(json.load(f))
    except (OSError, ValueError):
        pass
    # Neu hinzugekommene Tuner-Typen (z.B. longmynd2) in eine aeltere, schon gespeicherte
    # Tuner-Liste nachtragen (merged.update() ersetzt sonst die ganze Liste durch die gespeicherte).
    if isinstance(merged.get("tuners"), list):
        have = {t.get("typ"): t for t in merged["tuners"] if isinstance(t, dict)}
        for i, d in enumerate(app_config_default()["tuners"]):
            if d["typ"] not in have:
                merged["tuners"].insert(min(i, len(merged["tuners"])), d)
            else:
                # aeltere Eintraege um neu hinzugekommene Schluessel (Ports etc.) ergaenzen
                for k, v in d.items():
                    have[d["typ"]].setdefault(k, v)
    app_config = merged
    app_config_save()
    return app_config

# Tuner-Typ -> {Config-Schluessel: (cliargs-Attribut, Typ)} - was aus app_config.json in cliargs
# uebernommen wird (LongMynd 2: ip/port laufen ueber _longmynd2_endpoint).
_TUNER_CFG_MAP = {
    "longmynd": {"ip": ("longmynd_ip", str), "port": ("longmynd_port", int),
                 "stream_ip": ("longmynd_stream_ip", str), "stream_port": ("longmynd_stream_port", int),
                 "status_port": ("longmynd_status_port", int),
                 "status_forward_port": ("longmynd_status_forward_port", int),
                 "vnc_port": ("longmynd_vnc_port", int), "vlc_http_port": ("longmynd_vlc_http_port", int)},
    "longmynd2": {"stream_ip": ("longmynd2_stream_ip", str), "stream_port": ("longmynd2_stream_port", int),
                  "status_port": ("longmynd2_status_port", int),
                  "status_forward_port": ("longmynd2_status_forward_port", int),
                  "vnc_port": ("longmynd2_vnc_port", int), "vlc_http_port": ("longmynd2_vlc_http_port", int)},
    "longmynd_p4": {"ip": ("longmyndp4_ssh_host", str), "script_lo": ("longmyndp4_script_lo", float),
                    "stream_ip": ("longmyndp4_stream_ip", str), "stream_port": ("longmyndp4_stream_port", int),
                    "status_port": ("longmyndp4_status_port", int),
                    "status_forward_port": ("longmyndp4_status_forward_port", int),
                    "vnc_port": ("longmyndp4_vnc_port", int), "vlc_http_port": ("longmyndp4_vlc_http_port", int)},
}

def _cli_given(attr):
    """True, wenn die Option zu diesem cliargs-Attribut ausdruecklich auf der Kommandozeile stand
    (dann gilt sie, nicht der Wert aus der Konfigdatei)."""
    import sys
    names = {"--" + attr, "--" + attr.replace("_", "-")}
    return any(a.split("=", 1)[0] in names for a in sys.argv[1:])

def tuner_config_apply():
    """Uebernimmt IP/Ports der LongMynd-Tuner aus app_config.json["tuners"] in cliargs - ausser
    die jeweilige Option wurde auf der Kommandozeile angegeben (die hat Vorrang)."""
    for entry in (app_config.get("tuners") or []):
        mapping = _TUNER_CFG_MAP.get(entry.get("typ")) if isinstance(entry, dict) else None
        if not mapping:
            continue
        for key, (attr, conv) in mapping.items():
            val = entry.get(key)
            if val in (None, "") or _cli_given(attr):
                continue
            try:
                setattr(cliargs, attr, conv(val))
            except (TypeError, ValueError):
                print("WARNUNG: app_config.json: ungueltiger Wert %r fuer %s/%s - ignoriert." %
                      (val, entry.get("typ"), key))
    cliargs.longmynd_url = "ws://%s:%d" % (cliargs.longmynd_ip, cliargs.longmynd_port)

def _longmynd2_endpoint():
    """(IP, Port) des zweiten LongMynd: Kommandozeile hat Vorrang, sonst der Eintrag typ
    'longmynd2' unter 'tuners' in app_config.json, sonst 127.0.0.1 / 8080."""
    ip, port = cliargs.longmynd2_ip, cliargs.longmynd2_port
    for t in (app_config.get("tuners") or []):
        if isinstance(t, dict) and t.get("typ") == "longmynd2":
            modus = t.get("modus")
            if modus in ("lokal", "extern"):
                if not ip:
                    ip = t.get(modus + "_ip") or None
                if not port:
                    try:
                        port = int(t.get(modus + "_port"))
                    except (TypeError, ValueError):
                        port = None
            if not ip:
                ip = t.get("ip") or None
            if not port:
                try:
                    port = int(t.get("port"))
                except (TypeError, ValueError):
                    port = None
            break
    return (ip or "127.0.0.1", port or 8080)

#----- RF-Eingang des MiniTiouners (longmynd UR8US) NUR ANZEIGEN, nichts schalten: longmynd meldet ihn im
#      monitor-WebSocket als packet.rx.rfport (0 = oben/Port 1, 1 = unten/Port 2). Kein Speichern, kein T-Befehl. -----
def _lm_control_url(target):
    if target == "longmynd2":
        ip, port = _longmynd2_endpoint()
        return "ws://%s:%d" % (ip, port)
    if target == "longmynd":
        return cliargs.longmynd_url
    return None

def _lm_rfport_send(target, port_idx):
    """RF-Eingang schalten wie der Knopf 'RF Port Top/Bottom' im Webinterface: T0 = oben, T1 = unten. Wird nicht gespeichert."""
    url = _lm_control_url(target)
    label = _TARGET_LABEL.get(target, target)
    if not url:
        return False
    try:
        from websocket import create_connection
        ws = create_connection(url, subprotocols=["control"], timeout=3)
        ws.send("T%d" % port_idx)
        ws.close()
        olog("%s: RF-Eingang auf Port %d (%s) geschaltet (T%d)." % (label, port_idx + 1,
             "oben" if port_idx == 0 else "unten", port_idx))
        return True
    except Exception as e:
        olog("%s: RF-Eingang umschalten fehlgeschlagen (%s): %s" % (label, url, e))
        return False

def _lm_rfport_read(target):
    """RF-Port aus dem monitor-WebSocket lesen. Rueckgabe 0/1 oder None (nicht erreichbar / kein Feld)."""
    url = _lm_control_url(target)
    if not url:
        return None
    try:
        from websocket import create_connection
        ws = create_connection(url, subprotocols=["monitor"], timeout=2)
        try:
            t0 = time.time()
            while time.time() - t0 < 2.5:
                msg = ws.recv()
                try:
                    d = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                rx = ((d.get("packet") or {}).get("rx") or {}) if isinstance(d, dict) else {}
                if "rfport" in rx:
                    return int(rx["rfport"])
        finally:
            ws.close()
    except Exception:
        return None
    return None

def _lm2_entry():
    for t in (app_config.get("tuners") or []):
        if isinstance(t, dict) and t.get("typ") == "longmynd2":
            return t
    return None

def _lm2_proc_pid(port):
    """PID des longmynd-Prozesses mit Web-Steuerung auf diesem Port (ps), sonst None.
    Sucht das longmynd-Programm selbst - nicht den Skriptnamen (der steht nach dem Ende von longmynd
    weiter in der Befehlszeile der Terminal-Shell)."""
    for l in _ps_lines():
        parts = l.split()
        if len(parts) < 2:
            continue
        exe = os.path.basename(parts[1])
        if exe != "longmynd":
            continue
        args = parts[2:]
        if "-W" in args:
            i = args.index("-W")
            if i + 1 < len(args) and args[i + 1] != str(port):
                continue
        return parts[0]
    return None

_lm2_wd = {"nopoll": 0, "warned": False, "down": False, "tries": 0, "next": 0.0}
_LM2_RETRY_S = (60, 60, 120, 300)        # Abstaende der Neustartversuche, danach alle 5 min - endlos

def _lm2_watchdog_loop():
    """LongMynd 2 lokal: alle 30 s pruefen, ob longmynd noch laeuft (Web-Port, sonst ps) - wenn nicht: neu starten.
    Meldung nur EINMAL pro Ausfall; die Neustartversuche laufen danach still weiter (60 s, 60 s, 2 min, dann alle 5 min),
    bis longmynd wieder antwortet - dann eine Meldung 'laeuft wieder'."""
    time.sleep(40)
    while True:
        try:
            t = _lm2_entry() or {}
            if ("longmynd2" in _active_targets and t.get("modus", "lokal") == "lokal"
                    and t.get("lokal_autostart", True)):
                ip, port = _longmynd2_endpoint()
                now = time.time()
                if _is_port_open("127.0.0.1", port, timeout=0.5):
                    if _lm2_wd["down"]:
                        olog("LongMynd 2 (lokal): longmynd laeuft wieder (Web-Steuerung Port %d erreichbar%s)." % (
                            port, (", nach %d Neustartversuch(en)" % _lm2_wd["tries"]) if _lm2_wd["tries"] else ""))
                    _lm2_wd.update(nopoll=0, warned=False, down=False, tries=0, next=0.0)
                    _lm2_start_err["shown"] = False
                else:
                    pid = _lm2_proc_pid(port)
                    if pid:
                        _lm2_wd["nopoll"] += 1
                        if _lm2_wd["nopoll"] >= 3 and not _lm2_wd["warned"]:
                            _lm2_wd["warned"] = True
                            olog("LongMynd 2 (lokal): longmynd laeuft laut ps (PID %s), Web-Steuerung Port %d "
                                 "antwortet aber seit ~90 s nicht (haengt?)." % (pid, port))
                    elif now >= _lm2_wd["next"]:
                        first = not _lm2_wd["down"]
                        _lm2_wd["down"] = True
                        _lm2_wd["tries"] += 1
                        _lm2_wd["next"] = now + _LM2_RETRY_S[min(_lm2_wd["tries"] - 1, len(_LM2_RETRY_S) - 1)]
                        if first:
                            olog("LongMynd 2 (lokal): longmynd laeuft nicht - wird neu gestartet "
                                 "(weitere Versuche laufen still im Hintergrund, bis es wieder geht).")
                        else:
                            dlog("LongMynd 2 (lokal): Neustartversuch %d." % _lm2_wd["tries"])
                        _lm2_local_ensure(force=True, quiet=not first)
        except Exception as e:
            dlog("LongMynd 2 Watchdog: %r" % (e,))
        time.sleep(30)

def _lm2_local_ensure(force=False, quiet=False):
    """LongMynd 2 im Modus 'lokal': laeuft longmynd hier (Web-Steuerung 127.0.0.1:Port erreichbar oder das
    Startskript im Prozess-Liste)? Wenn nein: Startskript in eigenem Terminal starten
    (cd <lokal_dir> && ./<lokal_script>). Abschaltbar ueber 'lokal_autostart' (Menue Konfig > LongMynd 2)."""
    t = _lm2_entry() or {}
    if "longmynd2" not in _active_targets or t.get("modus", "lokal") != "lokal":
        return
    if not force and not t.get("lokal_autostart", True):
        return
    script = t.get("lokal_script") or "start_longmynd2.sh"
    d = t.get("lokal_dir") or "/home/dg7ndv/sdr/ur8us_longmynd"
    ip, port = _longmynd2_endpoint()
    if _is_port_open("127.0.0.1", port, timeout=0.5):
        olog("LongMynd 2 (lokal): Web-Steuerung 127.0.0.1:%d erreichbar - laeuft schon." % port)
        return
    pid = _lm2_proc_pid(port)
    if pid:
        olog("LongMynd 2 (lokal): longmynd laeuft schon (PID %s, Port %d antwortet noch nicht)." % (pid, port))
        return
    path = os.path.join(d, script)
    if not os.path.isfile(path):
        for alt in ("start_longmynd2.sh", "start_longmand2.sh"):      # Schreibweise des Skripts bei DG7NDV
            if os.path.isfile(os.path.join(d, alt)):
                script, path = alt, os.path.join(d, alt)
                break
    if not os.path.isfile(path):
        olog("LongMynd 2 (lokal): Startskript %s nicht gefunden - bitte im Menue Konfig > LongMynd 2 pruefen." % path)
        return
    (dlog if quiet else olog)("LongMynd 2 (lokal): starte %s in eigenem Terminal ..." % path)
    logf = os.path.join(parameters_path, "longmynd2_start.log")
    try:
        open(logf, "w").close()
    except OSError:
        pass
    # Ausgabe zusaetzlich in eine Datei (tee) - zeilenweise (stdbuf), damit die GUI den Start mitlesen kann
    sb = "stdbuf -oL -eL " if shutil.which("stdbuf") else ""
    _tail = "; exec bash" if not quiet else "; sleep 3"   # Wiederholungsversuch: Fenster schliesst sich nach Abbruch
    _spawn_terminal("cd %s && %s./%s 2>&1 | tee -a %s; echo; echo '--- %s beendet ---' | tee -a %s%s" %
                    (shlex.quote(d), sb, shlex.quote(script), shlex.quote(logf), script, shlex.quote(logf), _tail),
                    geometry=None, title=None)
    # title=None: Terminal wird nicht verschoben und beim Beenden der GUI NICHT geschlossen (longmynd laeuft weiter)
    threading.Thread(target=_lm_start_watch, args=("LongMynd 2", logf, script), daemon=True).start()

_lm2_start_err = {"shown": False, "logged": set()}

def _lm_start_watch(label, logf, script, timeout=120.0):
    """Liest die Startausgabe von longmynd mit (Datei von tee): alles nach gui.log, Erfolg/Fehler in die Statuszeile.
    Erfolg: 'Flow: STV0910 start scan'. Fehler: 'ERROR: ...' (z.B. MiniTiouner nicht am USB)."""
    t0 = time.time()
    pos = 0
    errors = []
    done = False
    while time.time() - t0 < timeout and not done:
        time.sleep(0.5)
        try:
            with open(logf, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
        except OSError:
            continue
        for ln in chunk.splitlines():
            raw = ln.rstrip()
            if not raw.strip():
                continue
            _gui_log_write("[%s longmynd] %s" % (label, raw))
            st = raw.strip()
            if st.startswith("ERROR"):
                errors.append(st)
                if st not in _lm2_start_err["logged"]:      # gleiche Fehlermeldung nur einmal in die Statuszeile
                    _lm2_start_err["logged"].add(st)
                    olog("%s: longmynd-Start FEHLER: %s" % (label, st))
                else:
                    dlog("%s: longmynd-Start FEHLER (wiederholt): %s" % (label, st))
            elif errors and not st.startswith(("Flow:", "Status:")) and len(errors) < 6:
                errors.append(st)                       # Folgezeile einer Fehlermeldung ("Is the USB cable plugged in?")
                if ("  " + st) not in _lm2_start_err["logged"]:
                    _lm2_start_err["logged"].add("  " + st)
                    olog("%s:   %s" % (label, st))
            elif "Status: found new NIM" in st:
                olog("%s: longmynd - MiniTiouner (NIM) gefunden." % label)
            elif "STV0910 start scan" in st:
                olog("%s: longmynd erfolgreich gestartet (Tuner initialisiert, scannt)." % label)
                _lm2_start_err["logged"].clear()
                done = True
            elif "beendet ---" in st:
                (dlog if _lm2_start_err["shown"] else olog)("%s: %s wurde beendet%s." % (
                    label, script, " - siehe Fehler oben" if errors else ""))
                done = True
        if errors and not done and time.time() - t0 > 3 and "ERROR: FTDI init" in " ".join(errors):
            done = True
    if errors and not _lm2_start_err["shown"]:
        _lm2_start_err["shown"] = True             # nur einmal pro Ausfall - zurueckgesetzt, wenn longmynd wieder laeuft
        root.after(0, _lm_start_error_popup, label, script, errors)
    elif done and not errors:
        _lm2_start_err["shown"] = False
    elif not done:
        olog("%s: longmynd-Start - nach %.0f s keine Meldung 'STV0910 start scan' (siehe Terminal)." % (label, timeout))

def _lm_start_error_popup(label, script, errors):
    w = tk.Toplevel(root)
    w.title("%s: longmynd-Start fehlgeschlagen" % label)
    w.attributes("-topmost", True)
    txt = "%s: %s meldet Fehler:\n\n%s" % (label, script, "\n".join(errors[:8]))
    if any("VID and PID" in e or "USB" in e for e in errors):
        txt += "\n\nMiniTiouner nicht gefunden - USB-Kabel/Strom pruefen, dann ueber\nKonfig > LongMynd 2 > 'jetzt pruefen/starten' neu starten."
    tk.Label(w, text=txt, justify="left", anchor="w", padx=16, pady=12, fg="#a00000").pack(fill="both", expand=True)
    ttk.Button(w, text="OK", command=w.destroy).pack(pady=(0, 12))

def app_config_save():
    try:
        with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(app_config, indent=4, sort_keys=True))
    except OSError as e:
        print("WARNUNG: Konfigdatei (%s) konnte nicht geschrieben werden: %s" % (APP_CONFIG_FILE, e))

def _vlcrc_apply_snapshot_path(path):
    """Traegt snapshot-path in ~/.config/vlc/vlcrc ein, damit ALLE VLC-Instanzen (die von
    dieser GUI fuer Octagon gestartete UND die vom Nutzer selbst gestartete fuer LongMynd/
    LongMynd P4, die wir nur per Telnet fernsteuern) Snapshots an derselben, hier konfigurierten
    Stelle ablegen - legt den Zielordner bei Bedarf an."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        print("WARNUNG: Snapshot-Ordner (%s) konnte nicht angelegt werden: %s" % (path, e))
    vlcrc = os.path.expanduser("~/.config/vlc/vlcrc")
    try:
        os.makedirs(os.path.dirname(vlcrc), exist_ok=True)
        lines = []
        if os.path.isfile(vlcrc):
            with open(vlcrc, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        new_line = "snapshot-path=%s\n" % path
        for i, line in enumerate(lines):
            if line.strip().lstrip("#").startswith("snapshot-path="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
        with open(vlcrc, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError as e:
        print("WARNUNG: vlcrc (%s) konnte nicht angepasst werden: %s" % (vlcrc, e))

# Spektrum-Signal-Log: ein Eintrag je im nativen BATC-Spektrum erkanntem Signal (Bake
# ausgenommen) - die Zeile wird SOFORT bei der ersten Erkennung geschrieben (noch ohne
# Rufzeichen), sobald dann ein Rufzeichen decodiert wird dieselbe Zeile mit Rufzeichen und den
# dann aktuellen dB-/D-Wert-Angaben nachtraeglich editiert, und sobald das Signal wieder
# verschwindet der Ende-Zeitpunkt nachgetragen (siehe _spectrum_log_track/_spectrum_log_update_row).
# Getrennt von signal_log.csv (das protokolliert Umschalt-Vorgaenge je Ziel, nicht die im
# Spektrum sichtbaren Signale selbst).
SPECTRUM_SIGNAL_LOG = os.path.join(parameters_path, "spektrum_signale.csv")
_SPECTRUM_LOG_HEADER = ["Start-Datum", "Start-Zeit", "Ende-Datum", "Ende-Zeit", "Rufzeichen",
                        "Frequenz (MHz)", "Symbolrate (kS/s)", "dB (Bake-relativ, wie Cursor)",
                        "D-Wert (dB)", "dB ueber Rauschen (absolut)", "dB ueber Rauschen (relativ)", "FEC"]
_spectrum_log_lock = threading.Lock()

def _csv_ensure_header(path, header):
    """Kopfzeile einer vorhandenen Log-Datei um neue Spalten am Ende erweitern (alte Zeilen bleiben
    unveraendert, ihre fehlenden Felder sind leer). Einmal pro Datei ein Backup <datei>.bak_vor_fec."""
    try:
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            first = f.readline()
            rest = f.read()
        cur = next(csv.reader([first.replace("\0", "")]), [])
        if cur == header or cur != header[:len(cur)]:
            return                     # schon aktuell, oder fremdes Format - nicht anfassen
        bak = path + ".bak_vor_fec"
        if not os.path.exists(bak):
            import shutil as _sh
            _sh.copy2(path, bak)
        import io as _io
        buf = _io.StringIO()
        csv.writer(buf).writerow(header)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue() + rest)
    except OSError as e:
        print("Log-Kopfzeile nicht aktualisierbar (%s): %s" % (path, e), flush=True)
# Frequenz(gerundet per Toleranz) -> {"start_date","start_time","freq","sr_mhz","logged_name"} -
# verfolgt jedes im Spektrum sichtbare (Nicht-Bake-)Signal, solange es da ist.
_spectrum_sig_track = {}

_csv_ensure_header(SPECTRUM_SIGNAL_LOG, _SPECTRUM_LOG_HEADER)

def _spectrum_log_append(row):
    try:
        with _spectrum_log_lock:
            new_file = not os.path.isfile(SPECTRUM_SIGNAL_LOG)
            with open(SPECTRUM_SIGNAL_LOG, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(_SPECTRUM_LOG_HEADER)
                w.writerow(row)
    except OSError as e:
        dlog("Spektrum-Signal-Log konnte nicht geschrieben werden: %s" % e)

def _spectrum_log_update_row(start_date, start_time_s, freq_mhz, updates):
    """Sucht die zu (Start-Datum, Start-Zeit, Frequenz) gehoerige Zeile - das ist pro
    Signal-Auftreten eindeutig genug, da sofort bei der ersten Erkennung geschrieben wird
    (siehe _spectrum_log_track) - und ueberschreibt die in 'updates' angegebenen Spalten
    (Schluessel = Spaltenname aus _SPECTRUM_LOG_HEADER). Genutzt sowohl um Rufzeichen+dB-Werte
    nachzutragen, sobald ein Rufzeichen decodiert wird, als auch um das Ende einzutragen."""
    try:
        with _spectrum_log_lock:
            if not os.path.isfile(SPECTRUM_SIGNAL_LOG):
                return False
            with open(SPECTRUM_SIGNAL_LOG, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if len(rows) < 2:
                return False
            header, body = rows[0], rows[1:]
            freq_txt = "%.3f" % freq_mhz
            changed = False
            for r in reversed(body):
                if len(r) >= 6 and r[0] == start_date and r[1] == start_time_s and r[5] == freq_txt:
                    for col_name, val in updates.items():
                        idx = _SPECTRUM_LOG_HEADER.index(col_name)
                        while len(r) <= idx:
                            r.append("")
                        r[idx] = val
                    changed = True
                    break
            if not changed:
                return False
            with open(SPECTRUM_SIGNAL_LOG, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(body)
            return True
    except OSError as e:
        dlog("Spektrum-Signal-Log (Update) konnte nicht geschrieben werden: %s" % e)
        return False

def _spectrum_fec_for_freq(freq_mhz):
    """FEC/MODCOD-Name (wie im FEC-Chip, siehe _update_lm_status_chips) fuer das Ziel, das
    gerade auf dieser Frequenz steht - nur LongMynd/LongMynd P4. None, wenn nicht ermittelbar."""
    for t in _LM_TARGETS:
        f = _target_cur_freq.get(t)
        if f is None or abs(f - freq_mhz) > _SPECTRUM_CALLSIGN_TOL:
            continue
        with _lm_status_lock:
            vals = dict(_lm_status_vals.get(t, {}))
        try:
            state_id = int(vals.get(1))
            modcod = int(vals.get(18))
            mer = float(vals.get(12)) / 10.0
        except (TypeError, ValueError):
            continue
        if mer <= 0.0:
            # kein echter Empfang (z.B. "searching") - Longmynd liefert dann nur den
            # Platzhalter-MODCOD 0 ("DummyPL"), keine echte FEC-Information.
            continue
        if state_id == 3:
            return _MODCOD_NAMES_DVBS.get(modcod, str(modcod))
        return _MODCOD_NAMES_DVBS2.get(modcod, str(modcod))
    return None

def _spectrum_dwert_for_freq(freq_mhz):
    """D-Wert (Decodier-Marge in dB, siehe _lm_extra_fields) fuer das Ziel, das gerade auf
    dieser Frequenz steht - nur LongMynd/LongMynd P4 liefern das (vom auswertung_4002.sh-
    Statusstrom). None, wenn kein passendes Ziel dort steht oder kein D-Wert bekannt ist."""
    for t in _LM_TARGETS:
        f = _target_cur_freq.get(t)
        if f is not None and abs(f - freq_mhz) <= _SPECTRUM_CALLSIGN_TOL:
            _mer, dwert, _codec = _lm_extra_fields(t)
            if dwert is not None:
                return dwert
    return None

def _spectrum_provider_for_freq(freq_mhz):
    """Provider-Name fuer das Ziel, das gerade auf dieser Frequenz steht - bevorzugt LongMynd/
    LongMynd P4 (Feld 14 aus dem Statusstrom, am schnellsten), sonst der von tsduck/ffprobe
    erkannte Provider aus _target_video_info (alle Ziele, auch rtl_sdr/Octagon). None, wenn
    kein passendes Ziel dort steht oder (noch) kein Provider bekannt ist."""
    for t in _LM_TARGETS:
        f = _target_cur_freq.get(t)
        if f is None or abs(f - freq_mhz) > _SPECTRUM_CALLSIGN_TOL:
            continue
        with _lm_status_lock:
            prov = (_lm_status_vals.get(t, {}) or {}).get(14)
        if prov:
            return prov
        prov = (_target_video_info.get(t) or {}).get("provider")
        if prov:
            return prov
    for t in _active_targets:
        if t in _LM_TARGETS:
            continue
        f = _target_cur_freq.get(t)
        if f is not None and abs(f - freq_mhz) <= _SPECTRUM_CALLSIGN_TOL:
            prov = (_target_video_info.get(t) or {}).get("provider")
            if prov:
                return prov
    return None

def _spectrum_log_track(detected, beacon_raw, baseline_y, db_per_pixel, plot_h, width):
    """Pro Zeichenzyklus: neue Signale (ausser der Bake) SOFORT als Zeile loggen (noch ohne
    Rufzeichen), beim spaeteren Decodieren eines Rufzeichens dieselbe Zeile mit Rufzeichen +
    aktuellen dB-Werten nachtragen/editieren, und bei verschwundenen Signalen das Ende
    nachtragen - alles ueber _spectrum_log_update_row anhand (Start-Datum, Start-Zeit, Frequenz)."""
    live_keys = []
    for sig in detected:
        f = sig["freq"]
        if abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
            continue
        key = next((k for k in _spectrum_sig_track if abs(k - f) <= _SPECTRUM_CALLSIGN_TOL), None)
        is_new = key is None
        if is_new:
            now = time.localtime()
            key = f
            _spectrum_sig_track[key] = {
                "start_date": time.strftime("%Y-%m-%d", now),
                "start_time": time.strftime("%H:%M:%S", now),
                "freq": f, "sr_mhz": sig["sr_mhz"], "logged_name": None,
            }
        track = _spectrum_sig_track[key]
        track["freq"] = f
        track["sr_mhz"] = sig["sr_mhz"]
        live_keys.append(key)

        def _db_values():
            dbb = None
            if beacon_raw is not None and db_per_pixel:
                beacon_y = _spectrum_y_for_raw(beacon_raw, plot_h)
                dbb = (beacon_y - sig["top_px"]) / db_per_pixel
            db_abs = None
            if sig["raw"] > 0 and _SPECTRUM_NOISE_LEVEL > 0:
                db_abs = 10.0 * math.log10(sig["raw"] / float(_SPECTRUM_NOISE_LEVEL))
            db_rel = None
            if db_per_pixel:
                db_rel = (baseline_y - sig["top_px"]) / db_per_pixel
            dwert = _spectrum_dwert_for_freq(f)
            return dbb, db_abs, db_rel, dwert

        if is_new:
            dbb, db_abs, db_rel, dwert = _db_values()
            _spectrum_log_append([
                track["start_date"], track["start_time"], "", "", "",
                "%.3f" % f, "%d" % int(round(sig["sr_mhz"] * 1000)),
                ("%+.1f" % dbb) if dbb is not None else "",
                ("%+.1f" % dwert) if dwert is not None else "",
                ("%+.1f" % db_abs) if db_abs is not None else "",
                ("%+.1f" % db_rel) if db_rel is not None else "",
                (_spectrum_fec_for_freq(f) or ""),
            ])
            track["logged_fec"] = _spectrum_fec_for_freq(f)

        name = _spectrum_callsign_for_signal(sig, width)
        if name and track["logged_name"] != name:
            track["logged_name"] = name
            dbb, db_abs, db_rel, dwert = _db_values()
            _spectrum_log_update_row(track["start_date"], track["start_time"], f, {
                "Rufzeichen": name,
                "dB (Bake-relativ, wie Cursor)": ("%+.1f" % dbb) if dbb is not None else "",
                "D-Wert (dB)": ("%+.1f" % dwert) if dwert is not None else "",
                "dB ueber Rauschen (absolut)": ("%+.1f" % db_abs) if db_abs is not None else "",
                "dB ueber Rauschen (relativ)": ("%+.1f" % db_rel) if db_rel is not None else "",
            })

        # FEC nachtragen, sobald (oder falls sie sich aendert) ein Empfaenger darauf steht und dekodiert
        _fec_now = _spectrum_fec_for_freq(f)
        if _fec_now and track.get("logged_fec") != _fec_now:
            track["logged_fec"] = _fec_now
            _spectrum_log_update_row(track["start_date"], track["start_time"], f, {"FEC": _fec_now})

    for key in list(_spectrum_sig_track.keys()):
        if key in live_keys:
            continue
        track = _spectrum_sig_track.pop(key)
        now = time.localtime()
        _spectrum_log_update_row(track["start_date"], track["start_time"], track["freq"], {
            "Ende-Datum": time.strftime("%Y-%m-%d", now),
            "Ende-Zeit": time.strftime("%H:%M:%S", now),
        })

def _octagon_fehler_log(freq_mhz, sr, grund):
    """Haengt einen Zeitstempel + Grund an octagon_fehler.log an, immer wenn process_signal()
    fuer Octagon keinen Kanal zuweisen konnte (Kanalliste noch nicht geladen, oder kein
    passender Kanal fuer Frequenz/Symbolrate gefunden) - damit das auch nachvollziehbar bleibt,
    wenn gerade niemand auf das Terminal schaut (dort steht es nur kurz per olog())."""
    try:
        with open(OCTAGON_FEHLER_LOG, "a") as f:
            f.write("%s\t%.3f MHz\t%s kS/s\t%s\n" %
                    (time.strftime("%Y-%m-%d %H:%M:%S"), freq_mhz, sr, grund))
    except OSError as e:
        dlog("octagon_fehler.log konnte nicht geschrieben werden: %s" % e)

def _verteiler_log(target, freq, sr):
    """Haengt einen Umschalt-Vorgang mit Zeitstempel an die verteiler.log an."""
    try:
        with open(VERTEILER_LOG, "a") as f:
            f.write("%s\t%s\t%.3f\t%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), target, freq, sr))
    except OSError:
        pass

def _verteiler_load():
    """Liest beim Start die letzte Zuteilung je Ziel aus verteiler.log (letzte Zeile je Ziel
    gewinnt). Wird beim naechsten Automatik-Durchlauf genutzt, um - falls das damalige Signal
    noch online ist - dieselbe Verteilung wiederherzustellen, statt neu zu verteilen."""
    saved = {}
    try:
        with open(VERTEILER_LOG, "r") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    saved[parts[1]] = float(parts[2])
    except (OSError, ValueError):
        pass
    return saved

_verteiler_saved = _verteiler_load()   # {target: freq_mhz} - wird nach Wiederherstellung je Ziel entfernt

def batc_analyze_signals(fft_data):
    """Ein BATC-Bild (Liste von FFT-Bin-Werten) -> Liste (Frequenz MHz, Breite kHz) gefundener Signale.
    Unveraendert aus octagon_autom.py: einfache Schwellwert-Segmentierung ueber dem 25%-Perzentil+2.5dB."""
    global _batc_last_fft
    _batc_last_fft = fft_data
    db = [10.0 * math.log10(v if v > 0 else 1) for v in fft_data]
    thresh = sorted(db)[int(len(db) * 0.25)] + 2.5
    tmp, in_sig, start_x = [], False, 0
    beacon_hits = []
    for x in range(len(db)):
        if db[x] > thresh and not in_sig:
            in_sig, start_x = True, x
        elif db[x] <= thresh and in_sig:
            in_sig = False
            w = x - start_x
            if w > 5:
                f  = BATC_START_FREQ + (((start_x + (w / 2)) / len(db)) * BATC_BANDWIDTH)
                bw = (w / len(db)) * BATC_BANDWIDTH * 1000
                if bw > BATC_MIN_BW_KHZ:
                    if abs(f - BATC_BEACON_MHZ) > BATC_BEACON_TOL:
                        tmp.append((f, bw))
                    else:
                        beacon_hits.append(f)
    now = time.time()
    with _batc_lock:
        _batc_frames.append((now, tmp))
        while _batc_frames and _batc_frames[0][0] < now - 30:
            _batc_frames.popleft()
        for bf in beacon_hits:
            _batc_beacon_frames.append((now, bf))
        while _batc_beacon_frames and _batc_beacon_frames[0][0] < now - 30:
            _batc_beacon_frames.popleft()

def batc_aggregate():
    """Fasst die BATC-Bilder der letzten BATC_WINDOW Sekunden zu Signal-Clustern zusammen."""
    now = time.time()
    with _batc_lock:
        fr = [sigs for (t, sigs) in _batc_frames if t >= now - BATC_WINDOW]
    if not fr:
        return None
    clusters = []
    for sigs in fr:
        used = set()
        for f, bw in sigs:
            best = None
            for i, c in enumerate(clusters):
                if i not in used and abs(c["f"] - f) <= BATC_SIG_TOL and (best is None or abs(c["f"] - f) < abs(clusters[best]["f"] - f)):
                    best = i
            if best is None:
                clusters.append({"f": f, "bw": bw, "n": 1})
                used.add(len(clusters) - 1)
            else:
                c = clusters[best]
                c["f"]  = (c["f"] * c["n"] + f) / (c["n"] + 1)
                c["bw"] = (c["bw"] * c["n"] + bw) / (c["n"] + 1)
                c["n"] += 1
                used.add(best)
    return len(fr), clusters

_auto_new_msgs = []     # "Neues Signal ..."-Meldungen aus batc_tick (ausserhalb des Locks ausgegeben)
_spec_only_cnt = {}     # nur im Spektrum erkannte (schmale) Signale -> Anzahl Takte in Folge

#----- Verteil-Protokoll ~/.leandvb-GUI/verteil_ereignisse.csv (zum spaeteren Auswerten):
#      signal_neu / signal_weg (BATC) -> eingestellt (Tuner) -> lock / stream / player / rufzeichen -> versorgt (Zeit seit
#      Erkennung) bzw. lock_weg; alle 60 s ein Abgleich BATC-Liste <-> Tuner (unversorgt, doppelt, frei). -----
VLOG_FILE = os.path.join(parameters_path, "verteil_ereignisse.csv")
_vlog_lock = threading.Lock()

def _vlog(ereignis, freq=None, sr=None, tuner="", rufzeichen="", details=""):
    try:
        with _vlog_lock:
            new = not os.path.exists(VLOG_FILE)
            if not new and os.path.getsize(VLOG_FILE) > 5 * 1024 * 1024:
                os.replace(VLOG_FILE, VLOG_FILE + ".1")
                new = True
            with open(VLOG_FILE, "a", encoding="utf-8") as fh:
                if new:
                    fh.write("zeit;ereignis;freq_mhz;sr_ks;tuner;rufzeichen;details\n")
                fh.write("%s;%s;%s;%s;%s;%s;%s\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), ereignis,
                    ("%.3f" % freq) if isinstance(freq, (int, float)) else "",
                    sr if sr else "", tuner, (rufzeichen or "").replace(";", ","), str(details).replace(";", ",")))
    except OSError:
        pass

def batc_tick():
    """Hysterese gegen Flackern (wie octagon_autom.py): neues Signal ab 50% der Bilder im Fenster,
    ein bekanntes bleibt bis unter 20%. Aktualisiert _batc_stable UND die Ampel-Zustaende
    (_batc_sig_state: neu -> 30 s gruen, verschwunden -> 10 s rot, dann geloescht).
    Rueckgabe: True bei einer Aenderung, die einen Struktur-Neuaufbau der Knopfliste braucht
    (neues Signal aufgetaucht, oder ein rotes Signal ist nach Ablauf entgueltig entfernt worden)."""
    now = time.time()
    res = batc_aggregate()
    struct_changed = False
    new_signal_keys = []   # fuer Auto-Scan (AScan) - siehe unten, ausserhalb von _batc_lock ausgewertet
    with _batc_lock:
        if res is not None:
            n, clusters = res
            new = []
            for c in sorted(clusters, key=lambda c: c["f"]):
                known = any(abs(f - c["f"]) <= BATC_SIG_TOL for f, _ in _batc_stable)
                if c["n"] / n >= (0.2 if known else 0.5) and not any(abs(f - c["f"]) <= BATC_SIG_TOL for f, _ in new):
                    # gerundet wie die Anzeige (%.3f MHz / %.0f kHz), sonst zaehlt jede winzige
                    # Nachkomma-Schwankung des gleitenden Mittelwerts als "Aenderung" und die
                    # Knopfliste wird jede Sekunde komplett neu aufgebaut (Geflacker).
                    new.append((round(c["f"], 3), round(c["bw"])))
            # Schmale Signale (z.B. 66 kS), die die BATC-Liste (Mindestbreite BATC_MIN_BW_KHZ) verwirft, die
            # Spektrum-Erkennung aber sieht: nach 3 Takten in Folge (~6 s) ebenfalls in die Liste aufnehmen
            seen_spec = set()
            for sg in (_spectrum_last_detected or []):
                f = round(sg["freq"], 3)
                if abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5 or any(abs(ff - f) <= BATC_SIG_TOL for ff, _ in new):
                    continue
                k = round(f, 1)
                seen_spec.add(k)
                _spec_only_cnt[k] = _spec_only_cnt.get(k, 0) + 1
                if _spec_only_cnt[k] >= 3:
                    new.append((f, round(sg["sr_mhz"] * 1350)))
            for k in [k for k in _spec_only_cnt if k not in seen_spec]:
                del _spec_only_cnt[k]
            new.sort(key=lambda x: x[0])
            _batc_stable[:] = new

            seen_keys = set()
            for f, bw in new:
                key = next((k for k, st in _batc_sig_state.items()
                            if abs(st["freq"] - f) <= BATC_SIG_TOL), None)
                if key is None:
                    key = f
                    _batc_sig_state[key] = {"freq": f, "bw": bw, "sr": _sr_for_freq(f, batc_sr_from_bw(bw)),
                                             "born": now, "gone_since": None}
                    struct_changed = True
                    if abs(f - BATC_BEACON_NOMINAL_MHZ) > 0.5:
                        new_signal_keys.append(key)
                        _auto_new_msgs.append("Neues Signal: %.3f MHz  %d kS/s" % (f, _batc_sig_state[key]["sr"]))
                        _vlog("signal_neu", f, _batc_sig_state[key]["sr"], "", "", "BATC-Liste")
                else:
                    st = _batc_sig_state[key]
                    if st["gone_since"] is not None:
                        # war schon "rot markiert" und ist wieder da -> als neu (gruen) zaehlen
                        st["born"] = now
                        st["gone_since"] = None
                    st["freq"], st["bw"] = f, bw
                    st["sr"] = _sr_for_freq(f, batc_sr_from_bw(bw))
                seen_keys.add(key)

            for key, st in _batc_sig_state.items():
                if key not in seen_keys and st["gone_since"] is None:
                    st["gone_since"] = now

        # Rot-Timeout abgelaufen -> jetzt endgueltig aus der Liste entfernen
        for key in [k for k, st in _batc_sig_state.items()
                    if st["gone_since"] is not None and now - st["gone_since"] >= BATC_GONE_RED_S]:
            _vlog("signal_weg", _batc_sig_state[key]["freq"], _batc_sig_state[key]["sr"], "", "", "aus BATC-Liste entfernt")
            del _batc_sig_state[key]
            struct_changed = True

    # Auto-Scan (AScan): bei jedem neu aufgetauchten Signal (nicht die Bake) fuer alle Ziele mit
    # eingeschaltetem Auto-Scan automatisch einen Scan ausloesen - ausserhalb von _batc_lock,
    # da _start_scan() selbst wieder _batc_lock braucht (sonst Deadlock). _start_scan() scannt
    # ohnehin ALLE gerade sichtbaren, noch unbekannten Signale, ein Trigger pro Tick reicht also.
    while _auto_new_msgs:
        olog(_auto_new_msgs.pop(0))
    if new_signal_keys:
        if _ascan_global[0]:
            _gt = _scan_pick_target()
            if _gt:
                _start_scan(_gt)
            else:
                _ascan_pending[0] = True      # es laeuft schon ein Scan -> danach nochmal
        else:
            for _t in _active_targets:
                if _ascan_active.get(_t):
                    _start_scan(_t)      # es scannt immer nur EIN Tuner - der erste mit AScan; ein weiterer
                                         # Aufruf waehrend des Laufs merkt sich nur "danach nochmal"
                    break

    return struct_changed

def batc_beacon_current():
    """Mittelt die zuletzt (innerhalb von BATC_WINDOW Sekunden) im BATC-Spektrum erkannten
    Bake-Peaks und liefert die aktuell gemessene Bake-Frequenz (MHz) - oder None, wenn die Bake
    im Spektrum gerade nicht sichtbar ist."""
    now = time.time()
    with _batc_lock:
        while _batc_beacon_frames and _batc_beacon_frames[0][0] < now - 30:
            _batc_beacon_frames.popleft()
        freqs = [f for (t, f) in _batc_beacon_frames if t >= now - BATC_WINDOW]
    if not freqs:
        return None
    return sum(freqs) / len(freqs)

#----- Spektrumanzeige (rechts unten): zeichnet das rohe BATC-FFT-Bild als Canvas-Grafik und
# erlaubt per Klick direkt auf eine Frequenz im Spektrum, LongMynd dorthin zu schalten - wie
# beim Original-Longmynd-Webinterface (click.html), nur nativ in dieser Tkinter-GUI statt als
# eigenes Browserfenster. Nutzt exakt dieselben BATC-FFT-Daten (_batc_last_fft), die auch
# batc_analyze_signals() fuer die normale Signalliste auswertet - keine zweite Verbindung noetig.
#
# Die Kurven-, Gitter- und dB-Berechnung ist 1:1 aus dem tatsaechlichen BATC-Web-Original
# portiert (eshail.batc.org.uk/wb/index.js, Stand wie vom Nutzer eingesehen): das Original
# rechnet NICHT logarithmisch, sondern rein LINEAR (Rohwert/65536, gekappt bei 1.0) und nutzt
# eine empirisch kalibrierte Pixel-pro-dB-Konstante nur fuer die "X dBb"-Anzeige relativ zur
# Bake - kein echtes log10. Diese Portierung uebernimmt exakt dieselben Formeln/Konstanten.
_spectrum_canvas = None
_SPECTRUM_HEIGHT = 420   # Start-Hoehe schon gross, damit das Spektrum von Anfang an bis unten reicht
_SPECTRUM_BOTTOM = 14    # Platz unten fuer die MHz-Beschriftung
_SPECTRUM_BEACON_LABEL = "A71A DATV Beacon\n10491.500\n(1.5MS/s QPSK, 4/5)"
_spectrum_hover_x = None     # aktuelle Mausposition im Spektrum (None = Maus nicht drueber)
_spectrum_manual_var = None  # tk.BooleanVar: an = alte manuelle Balken-Anzeige (freie Frequenz-
                              # wahl per Klick, wie vor Einfuehrung der Signal-Rechtecke), aus
                              # (Default) = Rechteck nur ueber einem erkannten Signal, Klick
                              # schaltet dann auf dessen Mittenfrequenz

# Konfigurierbare Zusatzzeilen UEBER der bisherigen Beschriftung (Rufzeichen/SR-Freq, siehe
# _spectrum_draw) - per Einstellungsfenster (_spectrum_settings_open) waehlbar und in den
# Parametern gespeichert. Index 0 ist konzeptionell "direkt ueber der Bestandsanzeige" (meist
# das Rufzeichen, das aber weiterhin ueber den bestehenden, unveraenderten Mechanismus gezeichnet
# wird - hier nur ausgelassen, um Dopplungen zu vermeiden), jeder weitere aktive Eintrag stapelt
# sich eine Zeile hoeher. "off" = Zeile nicht aktiv.
_spectrum_line_config = ["callsign", "fec", "off", "off"]
_SPECTRUM_LINE_OPTIONS = ["off", "callsign", "dwert", "freq", "sr", "fec", "provider"]
_SPECTRUM_LINE_LABELS = {"off": "Aus", "callsign": "Rufzeichen", "dwert": "D-Wert",
                         "freq": "Frequenz", "sr": "Datenrate", "fec": "FEC",
                         "provider": "Provider"}

_SPECTRUM_FULLSCALE     = 65536.0  # Rohwert-Vollskala der BATC-FFT-Bins (uint16), wie im Original
_SPECTRUM_NOISE_LEVEL   = 11000    # wie im BATC-Original (detect_signals())
_SPECTRUM_SIGNAL_THRESH = 16000    # wie im BATC-Original (detect_signals()) - Rohwert-Schwelle
_SPECTRUM_DRAW_THRESH   = 1.0 / 8.0   # unterhalb dieses Bruchteils der Vollskala wird nichts
                                       # gezeichnet (wie im Original: "if(sample > 1/8)")
_SPECTRUM_SCALE_DB      = 3276.8   # Rohwert-Differenz pro "dB" (Naeherung wie im Original,
                                    # nur fuer die Overpower-Erkennung)

def _spectrum_freq_to_x(freq_mhz, width):
    return int(round(((freq_mhz - BATC_START_FREQ) / BATC_BANDWIDTH) * width))

def _spectrum_x_to_freq(x, width):
    return BATC_START_FREQ + (x / max(1, width)) * BATC_BANDWIDTH

def _spectrum_frac(v):
    """Rohwert -> linearer Anteil der Vollskala (0..1), wie im BATC-Original
    (sample/65536.0, gekappt bei 1.0) - KEIN Logarithmus."""
    return min(v / _SPECTRUM_FULLSCALE, 1.0)

def _spectrum_y_for_raw(v, plot_h):
    """Canvas-Y-Position fuer einen rohen FFT-Wert, 1:1 wie im BATC-Original
    (canvasHeight - sample*canvasHeight)."""
    return plot_h - _spectrum_frac(v) * plot_h

def _spectrum_raw_peak_at(x, width):
    """Hoechster Rohwert (Spalten-Maximum) an einer Canvas-X-Position im aktuellen FFT-Bild,
    oder None, wenn noch kein Bild da ist."""
    fft = _batc_last_fft
    if not fft or width <= 1:
        return None
    n = len(fft)
    idx0 = min(n - 1, int(x / width * n))
    idx1 = max(idx0 + 1, min(n, int((x + 1) / width * n)))
    return max(fft[idx0:idx1])

def _spectrum_align_sr_mhz(width_mhz):
    """1:1-Portierung von align_symbolrate() aus dem BATC-Original: rundet eine gemessene
    Signalbreite (MHz) auf die naechste tatsaechlich genutzte Symbolrate."""
    if width_mhz < 0.022:
        return 0
    if width_mhz < 0.060:
        return 0.035
    if width_mhz < 0.086:
        return 0.066
    if width_mhz < 0.185:
        return 0.125
    if width_mhz < 0.277:
        return 0.250
    if width_mhz < 0.388:
        return 0.333
    if width_mhz < 0.700:
        return 0.500
    if width_mhz < 1.2:
        return 1.000
    if width_mhz < 1.6:
        return 1.500
    if width_mhz < 2.2:
        return 2.000
    return round(width_mhz * 5) / 5.0

def _spectrum_print_sr(sr_mhz):
    """1:1-Portierung von print_symbolrate()."""
    if sr_mhz < 0.7:
        return "%dKS" % round(sr_mhz * 1000)
    return "%gMS" % (round(sr_mhz * 10) / 10.0)

def _spectrum_print_freq(freq_mhz, sr_mhz):
    """1:1-Portierung von print_frequency() (rundet auf ein zur Symbolrate passendes Raster)."""
    if sr_mhz < 0.7:
        return "'%.3f" % (round(freq_mhz * 80) / 80.0)
    return "'%.3f" % (round(freq_mhz * 40) / 40.0)

def _spectrum_is_overpower(beacon_raw, signal_raw, sr_mhz):
    """1:1-Portierung von is_overpower()."""
    if beacon_raw and beacon_raw != 0:
        if sr_mhz < 0.4:
            return False
        if signal_raw > (beacon_raw - 0.75 * _SPECTRUM_SCALE_DB):
            return True
    return False

def _spectrum_detect_signals(fft, width, plot_h):
    """1:1-Portierung von detect_signals() aus dem BATC-Original: gleitender 3er-Mittelwert
    ueber _SPECTRUM_SIGNAL_THRESH markiert grob ein Signal, danach werden Start/Ende auf 75%
    der mittleren Signalstaerke ueber dem Rauschgrund verfeinert, damit nur das flache
    Plateau im Rechteck landet statt der schraegen Flanken. Anders als im Original wird die
    Bake hier NICHT aus der Liste ausgeblendet (die GUI soll beim Ueberfahren auch dort ein
    Rechteck zeigen). Liefert eine Liste von Dicts: start_px, end_px, top_px, freq, sr_mhz, raw."""
    n = len(fft)
    out = []
    in_signal = False
    start_i = 0
    i = 2
    while i < n:
        avg3 = (fft[i] + fft[i - 1] + fft[i - 2]) / 3.0
        if not in_signal:
            if avg3 > _SPECTRUM_SIGNAL_THRESH:
                in_signal = True
                start_i = i
        else:
            if avg3 < _SPECTRUM_SIGNAL_THRESH:
                end_i = i
                a0 = int(start_i + 0.3 * (end_i - start_i))
                a1 = max(a0 + 1, int(start_i + 0.7 * (end_i - start_i)))
                seg = fft[a0:a1] or [fft[start_i]]
                strength = sum(seg) / len(seg)

                thr_frac = 0.75 * (strength - _SPECTRUM_NOISE_LEVEL)
                j = start_i
                while j < end_i and (fft[j] - _SPECTRUM_NOISE_LEVEL) < thr_frac:
                    j += 1
                start_i = j
                j = end_i
                while j > start_i and (fft[j] - _SPECTRUM_NOISE_LEVEL) < thr_frac:
                    j -= 1
                end_i = j

                mid = start_i + (end_i - start_i) / 2.0
                freq = BATC_START_FREQ + ((mid + 1) / n) * BATC_BANDWIDTH
                width_mhz = ((end_i - start_i) / n) * BATC_BANDWIDTH
                sr_mhz = _spectrum_align_sr_mhz(width_mhz)
                if sr_mhz != 0:
                    out.append({
                        "start_px": int(round((start_i / n) * width)),
                        "end_px":   int(round((end_i / n) * width)),
                        "top_px":   _spectrum_y_for_raw(strength, plot_h),
                        "freq":     freq,
                        "sr_mhz":   sr_mhz,
                        "raw":      strength,
                    })
                in_signal = False
        i += 1
    return out

# Rufzeichen-Gedaechtnis: Frequenz (MHz, gerundet auf den Kanal) -> zuletzt dort decodiertes
# Rufzeichen. Haengt bewusst an der FREQUENZ (dem Signal/Kanal), nicht am Ziel - ein einmal auf
# einem Kanal decodiertes Rufzeichen bleibt dort im Spektrum stehen, auch wenn das Ziel
# inzwischen umgeschaltet hat, und verschwindet erst wieder, wenn das Signal selbst aus dem
# Spektrum verschwindet (siehe _spectrum_prune_callsigns, in _spectrum_draw() aufgerufen).
_spectrum_known_callsigns = {}
_SPECTRUM_CALLSIGN_TOL = 0.15   # MHz - wie BATC_SIG_TOL, zum zusammenfassen/Wiederfinden desselben Kanals
_spectrum_last_detected = []    # letzte _spectrum_detect_signals()-Liste, fuer Staerke-Lookup beim Merken

# Persistenter Cache (Frequenz -> {Rufzeichen, Staerke, Zeitstempel}) ueber einen GUI-Neustart
# hinweg - beim Laden werden nur Eintraege juenger als 60s uebernommen (siehe
# _spectrum_callsign_cache_load()), damit nach einem Neustart sofort wieder das zuletzt bekannte
# Rufzeichen im Spektrum steht, waehrend der erste frische Poll noch laeuft.
SPECTRUM_CALLSIGN_CACHE_FILE = os.path.join(parameters_path, "spectrum_callsigns_cache.json")
_SPECTRUM_CALLSIGN_CACHE_MAXAGE = 60.0   # Sekunden

def _spectrum_callsign_cache_save():
    try:
        data = {"%.3f" % f: {"name": n, "staerke_db": s, "ts": t}
                for f, (n, s, t) in _spectrum_callsign_cache.items()}
        with open(SPECTRUM_CALLSIGN_CACHE_FILE, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
    except OSError as e:
        dlog("Rufzeichen-Cache konnte nicht gespeichert werden: %s" % e)

def _spectrum_callsign_cache_load():
    """Beim Start: zuletzt gemerkte Rufzeichen (mit Frequenz/Staerke/Zeitstempel) aus
    SPECTRUM_CALLSIGN_CACHE_FILE lesen und nur die noch nicht aelter als
    _SPECTRUM_CALLSIGN_CACHE_MAXAGE Sekunden direkt wieder in _spectrum_known_callsigns
    eintragen, damit sie sofort im Spektrum erscheinen, noch bevor der erste frische
    Rufzeichen-Poll nach dem Neustart durch ist."""
    global _spectrum_callsign_cache
    _spectrum_callsign_cache = {}
    try:
        with open(SPECTRUM_CALLSIGN_CACHE_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return
    now = time.time()
    restored = 0
    for fstr, entry in raw.items():
        try:
            f = float(fstr)
            name = entry.get("name")
            staerke = entry.get("staerke_db")
            ts = float(entry.get("ts"))
        except (TypeError, ValueError):
            continue
        if _beacon_cs_misplaced(f, name):
            continue        # alte falsche A71A-Eintraege beim Laden wegwerfen
        _spectrum_callsign_cache[f] = (name, staerke, ts)
        if name and (now - ts) <= _SPECTRUM_CALLSIGN_CACHE_MAXAGE:
            _spectrum_known_callsigns[f] = name
            restored += 1
    if restored:
        olog("Rufzeichen-Cache: %d Rufzeichen aus dem letzten Start uebernommen (< %gs alt)." %
             (restored, _SPECTRUM_CALLSIGN_CACHE_MAXAGE))

_spectrum_callsign_cache = {}   # Frequenz -> (Rufzeichen, Staerke_dB_oder_None, Zeitstempel)

def _spectrum_strength_for_freq(freq):
    """Staerke (dB ueber Rauschen, wie im Signal-Log) fuer eine Frequenz aus der zuletzt
    gezeichneten Signal-Liste, oder None wenn gerade kein FFT-Bild/Signal dort vorliegt."""
    for s in _spectrum_last_detected:
        if abs(s["freq"] - freq) <= _SPECTRUM_CALLSIGN_TOL:
            try:
                if s["raw"] > 0 and _SPECTRUM_NOISE_LEVEL > 0:
                    return 10.0 * math.log10(s["raw"] / float(_SPECTRUM_NOISE_LEVEL))
            except (TypeError, ValueError):
                pass
    return None

_SPECTRUM_CALLSIGN_DUPLICATE_MAXAGE = 15.0   # Sekunden - siehe _spectrum_remember_callsign

_BEACON_CALLSIGNS = ("A71A",)

def _beacon_cs_misplaced(freq, name):
    """Das Rufzeichen der Bake (A71A) gehoert nur zur Bake - auf einer anderen Frequenz ist es ein alter
    Poll-Treffer bzw. eine falsche Frequenz-Zuordnung (z.B. Tuner stand noch auf der Bake)."""
    return bool(name) and freq is not None and name.strip().upper() in _BEACON_CALLSIGNS and \
        abs(float(freq) - BATC_BEACON_NOMINAL_MHZ) > 0.3

def _spectrum_remember_callsign(freq, name):
    """Merkt ein decodiertes Rufzeichen auf dieser Frequenz - mit einer Plausibilitaetspruefung:
    steht dasselbe Rufzeichen bereits bei einer ANDEREN Frequenz, ist das unwahrscheinlich (ein
    Rufzeichen sendet nicht gleichzeitig auf zwei Peaks) - dann wird geprueft, wie alt dieser
    andere Eintrag ist (_spectrum_callsign_cache-Zeitstempel): ist er noch frisch (< 15s, also
    vermutlich noch aktiv), wird diese neue Zuordnung verworfen (wahrscheinlich ein veralteter
    Poll-Treffer); ist er schon aelter, war das wohl ein alter Kanal - der Eintrag wird dort
    entfernt und das Rufzeichen zur neuen Frequenz verschoben."""
    if freq is None or not name:
        return
    if _beacon_cs_misplaced(freq, name):
        dlog("Rufzeichen %s bei %.3f MHz verworfen (Baken-Rufzeichen nur auf der Bake)." % (name, freq))
        return
    now = time.time()
    for f, cached in list(_spectrum_callsign_cache.items()):
        if abs(f - freq) <= _SPECTRUM_CALLSIGN_TOL:
            continue
        other_name, _staerke, other_ts = cached
        if other_name != name:
            continue
        age = now - other_ts
        if age < _SPECTRUM_CALLSIGN_DUPLICATE_MAXAGE:
            dlog("Rufzeichen %s steht schon bei %.3f MHz (vor %.0fs) - Zuordnung zu %.3f MHz "
                 "verworfen (unwahrscheinlich, dass dasselbe Rufzeichen auf zwei Peaks steht)." %
                 (name, f, age, freq))
            return
        dlog("Rufzeichen %s bei %.3f MHz ist veraltet (%.0fs) - wird zu %.3f MHz verschoben." %
             (name, f, age, freq))
        _spectrum_known_callsigns.pop(f, None)
        del _spectrum_callsign_cache[f]
    for f in list(_spectrum_known_callsigns.keys()):
        if abs(f - freq) <= _SPECTRUM_CALLSIGN_TOL:
            del _spectrum_known_callsigns[f]
    _spectrum_known_callsigns[freq] = name
    staerke = _spectrum_strength_for_freq(freq)
    for f in list(_spectrum_callsign_cache.keys()):
        if abs(f - freq) <= _SPECTRUM_CALLSIGN_TOL:
            del _spectrum_callsign_cache[f]
    _spectrum_callsign_cache[freq] = (name, staerke, now)
    _spectrum_callsign_cache_save()

def _spectrum_prune_callsigns(detected):
    """Entfernt gemerkte Rufzeichen, deren Kanal gerade nicht (mehr) im Spektrum zu sehen ist -
    nur aufgerufen, wenn wirklich ein frisches FFT-Bild da ist (siehe Aufrufstelle), damit ein
    einzelner fehlender Frame nicht gleich alles loescht."""
    for f in list(_spectrum_known_callsigns.keys()):
        if not any(abs(s["freq"] - f) <= _SPECTRUM_CALLSIGN_TOL for s in detected):
            del _spectrum_known_callsigns[f]

def _spectrum_callsign_for_signal(sig, width):
    """Liefert das fuer dieses Signal gemerkte Rufzeichen (siehe _spectrum_known_callsigns)
    oder None, wenn auf diesem Kanal noch nichts decodiert wurde."""
    for f, name in _spectrum_known_callsigns.items():
        if abs(f - sig["freq"]) <= _SPECTRUM_CALLSIGN_TOL:
            return name
    return None

def _spectrum_extra_line_text(kind, sig):
    """Liefert den Text fuer eine per Einstellungsfenster gewaehlte Zusatzzeile, oder None,
    wenn fuer diesen Typ gerade nichts anzuzeigen ist. "callsign" liefert bewusst immer None -
    das Rufzeichen wird weiterhin unveraendert ueber den bestehenden Mechanismus gezeichnet
    (siehe _spectrum_callsign_for_signal/_spectrum_known_callsigns), hier nur um Dopplungen zu
    vermeiden, die Zeile zaehlt aber weiterhin als (leerer) Stapel-Platz."""
    if kind in ("off", "callsign"):
        return None
    if kind == "dwert":
        d = _spectrum_dwert_for_freq(sig["freq"])
        return ("D%+.1f" % d) if d is not None else None
    if kind == "freq":
        return _spectrum_print_freq(sig["freq"], sig["sr_mhz"])
    if kind == "sr":
        return _spectrum_print_sr(sig["sr_mhz"])
    if kind == "fec":
        return _spectrum_fec_for_freq(sig["freq"])
    if kind == "provider":
        return _spectrum_provider_for_freq(sig["freq"])
    return None

_spec_scale = [1.0]
def _sf(size, bold=False):
    """Schrift fuer das Spektrum, mit der Canvasbreite skaliert (Mini-Ansicht: kleiner, nie unter 6 pt)"""
    return ('', max(6, int(round(size * _spec_scale[0]))), 'bold') if bold else ('', max(6, int(round(size * _spec_scale[0]))))

def _spectrum_draw_extra_lines(sig, mid_x, base_y):
    """Zeichnet die in _spectrum_line_config aktivierten Zusatzzeilen oberhalb von base_y
    (der Hoehe, auf der bisher das Rufzeichen steht) - gestapelt von unten nach oben, eine
    Zeile pro aktivem Eintrag. Aendert nichts an der bisherigen Anzeige (Rufzeichen/SR-Freq/
    Rechteck/dBb bleiben exakt wie vorher, siehe Aufrufstellen)."""
    y = base_y
    for kind in _spectrum_line_config:
        text = _spectrum_extra_line_text(kind, sig)
        if not text:
            continue
        y -= 15
        _spectrum_canvas.create_text(mid_x, y, text=text, fill="#ffffff", font=_sf(8))

_spectrum_draw_sig = None
_spectrum_draw_time = 0.0
_spec_cols_cache = {}

_spec_auto = {"rate": None, "ema": None, "logged": None, "last_full": 0.0, "sigset": None}

def _spectrum_rate_base():
    """Eingestellte Refresh-Rate (Zahl beim Spektrum, sonst --refreshSpektrum_per_Sekond)."""
    try:
        r = float(app_config.get("spectrum_refresh") or cliargs.spektrum_refresh)
    except (TypeError, ValueError):
        r = 5.0
    return min(20.0, max(0.2, r))

def _spectrum_rate():
    """Wirksame Rate: eingestellt, aber automatisch vermindert, wenn das Zeichnen zu viel Zeit kostet."""
    if app_config.get("spectrum_realtime"):
        return 20.0                      # Echtzeit: jedes FFT-Bild zeichnen (BATC liefert ca. 10/s), keine automatische Absenkung
    base = _spectrum_rate_base()
    a = _spec_auto["rate"]
    return base if a is None else min(base, a)

def _spectrum_adapt(dt_ms):
    """Zeichendauer mitteln; braucht ein Bild mehr als ~25 % der Zeit bis zum naechsten, Rate senken,
    bei viel Luft (< 10 %) langsam wieder bis zur eingestellten Rate erhoehen."""
    if app_config.get("spectrum_realtime"):
        _spec_auto["rate"] = None
        return
    e = _spec_auto["ema"]
    e = dt_ms if e is None else e * 0.8 + dt_ms * 0.2
    _spec_auto["ema"] = e
    base = _spectrum_rate_base()
    rate = _spectrum_rate()
    budget = 1000.0 / rate
    if e > 0.25 * budget and rate > 0.2:
        _spec_auto["rate"] = max(0.2, rate * 0.8)
    elif e < 0.10 * budget and rate < base:
        _spec_auto["rate"] = min(base, rate * 1.1)
        if _spec_auto["rate"] >= base:
            _spec_auto["rate"] = None
    eff = _spectrum_rate()
    shown = None if eff >= base - 0.01 else round(eff, 1)
    if shown != _spec_auto["logged"]:
        if shown is not None and (_spec_auto["logged"] is None or abs(shown - _spec_auto["logged"]) >= 0.5):
            olog("Spektrum: Zeichnen dauert %.0f ms - Refresh automatisch auf %.1f/s vermindert (eingestellt %.1f/s)." %
                 (e, eff, base))
        elif shown is None and _spec_auto["logged"] is not None:
            olog("Spektrum: Refresh wieder auf eingestellte %.1f/s." % base)
        _spec_auto["logged"] = shown
        try:
            _spec_rate_info.set("" if shown is None else "(auto %.1f)" % shown)
        except NameError:
            pass

def _spectrum_min_interval():
    return 0.9 / _spectrum_rate()     # 10 % Toleranz, damit ein Tick-Jitter keinen Zeichenzyklus verschluckt

def _spectrum_draw(force=False):
    """Zeichnet nur dann neu, wenn sich etwas geaendert hat (neues FFT-Bild, Fenstergroesse, Maus-
    position, Anzahl bekannter Rufzeichen) - sonst hoechstens 1x pro Sekunde (fuer Zustaende, die
    hier nicht in der Signatur stehen). Das volle Neuzeichnen (Pixel-Schleife ueber die ganze
    Breite + Signalerkennung) lief vorher alle 200 ms und bei jeder Mausbewegung und bremste die GUI."""
    global _spectrum_draw_sig, _spectrum_draw_time
    if _spectrum_canvas is None:
        return
    now = time.monotonic()
    only_new = bool(app_config.get("spectrum_only_new")) and not app_config.get("spectrum_realtime")     # Echtzeit hat Vorrang vor OnlyNewSignals
    if only_new:
        # OnlyNewSignals: neu zeichnen nur, wenn ein Signal dazukommt/wegfaellt (bzw. Maus/Groesse/Rufzeichen),
        # sicherheitshalber alle 10 s einmal komplett
        with _batc_lock:
            ss = tuple(sorted(round(st["freq"], 1) for st in _batc_sig_state.values() if st["gone_since"] is None))
        fid = ss if now - _spec_auto["last_full"] < 10.0 else (ss, int(now))
    else:
        fid = id(_batc_last_fft)
    sig = (fid, _spectrum_canvas.winfo_width(), _spectrum_canvas.winfo_height(),
           _spectrum_hover_x, len(_spectrum_known_callsigns))
    if not force and sig == _spectrum_draw_sig and (now - _spectrum_draw_time < 1.0 or only_new):
        return
    # Aendert sich NUR das FFT-Bild (Mausposition/Groesse/Rufzeichen gleich), reicht ~3x/s statt 5x/s.
    if (not force and _spectrum_draw_sig is not None and sig[1:] == _spectrum_draw_sig[1:]
            and now - _spectrum_draw_time < _spectrum_min_interval()):
        return
    _spectrum_draw_sig = sig
    _spectrum_draw_time = now
    _spec_auto["last_full"] = now
    t0 = time.perf_counter()
    _spectrum_draw_impl()
    _spectrum_adapt((time.perf_counter() - t0) * 1000.0)

_spec_grid = {"key": None, "last": 0}

def _spectrum_draw_grid(width, plot_h):
    """dB- und Frequenz-Gitter mit Beschriftung des Spektrums (aus _spectrum_draw_impl ausgelagert)"""
    # dB-Gitter (horizontal) - feste Pixel-Positionen wie im Original, KEIN Autorange:
    # 4 Hauptlinien (15/10/5/0dB) plus feine Zwischenlinien alle 0.75dB.
    for i in range(1, 5):
        y = i * (plot_h / 4.0) - (plot_h / 6.0)
        if 0 <= y <= plot_h:
            _spectrum_canvas.create_line(35, y, width - 35, y, fill="#888888", dash=(5, 10))
            if i != 4:
                lbl = "%ddB" % (5 * (4 - i))
                _spectrum_canvas.create_text(20, y, text=lbl, fill="#ffffff", font=_sf(8), anchor='c')
                _spectrum_canvas.create_text(width - 20, y, text=lbl, fill="#ffffff", font=_sf(8), anchor='c')
    for i in range(1, 20):
        if i % 5 != 0:
            y = i * (plot_h / 20.0) - (plot_h / 6.0)
            if 0 <= y <= plot_h:
                _spectrum_canvas.create_line(10, y, width - 10, y, fill="#666666", dash=(1, 10))

    # Frequenz-Gitter (vertikal) - 9 Linien bei den vollen MHz 10491..10499, wie im Original
    # (keine feineren Zwischenlinien).
    for i in range(0, 18, 2):
        f = BATC_START_FREQ + (1 + i) * 0.5
        x = _spectrum_freq_to_x(f, width)
        _spectrum_canvas.create_line(x, 0, x, plot_h * (7.0 / 8.0), fill="#888888", dash=(5, 20))
        _spectrum_canvas.create_text(x, 10, text="%.3f" % f, fill="#ffffff", font=_sf(8, True), anchor='c')

def _spectrum_draw_impl():
    """Zeichnet das zuletzt empfangene BATC-FFT-Bild neu (Spektrumsflaeche, MHz-/dB-Gitter,
    Bake-Beschriftung, Signal-Rechtecke) - Formeln/Konstanten 1:1 aus dem BATC-Web-Original
    portiert (siehe Kommentar oben), nur nativ in Tkinter statt HTML5-Canvas."""
    global _spectrum_last_detected
    if _spectrum_canvas is None:
        return
    width  = _spectrum_canvas.winfo_width()
    _spec_scale[0] = max(0.6, min(1.0, width / 1100.0))
    height = _spectrum_canvas.winfo_height()
    if width <= 1 or height <= 1:
        _spectrum_canvas.delete("all")
        _spec_grid["key"] = None
        return
    plot_h = height - _SPECTRUM_BOTTOM
    baseline_y = plot_h * (7.0 / 8.0)   # "Rauschkante" - wie im Original canvasHeight*(7/8)
    fft = _batc_last_fft

    # Gitter und Beschriftung aendern sich nur mit der Fenstergroesse: einmal zeichnen und stehen lassen (Canvas-Items mit hoeheren Nummern
    # sind das Spektrum/die Signale und werden jedes Mal ersetzt) - spart je Zeichnung rund 50 Canvas-Aufrufe (v.a. create_text).
    gkey = (width, height, id(_spectrum_canvas))
    if _spec_grid.get("key") != gkey:
        _spectrum_canvas.delete("all")
        _spectrum_draw_grid(width, plot_h)
        ids = _spectrum_canvas.find_all()
        _spec_grid["key"], _spec_grid["last"] = gkey, (max(ids) if ids else 0)
    else:
        dyn = [i for i in _spectrum_canvas.find_all() if i > _spec_grid["last"]]
        if dyn:
            _spectrum_canvas.delete(*dyn)

    bx = _spectrum_freq_to_x(BATC_BEACON_NOMINAL_MHZ, width)

    detected = []
    beacon_raw = None
    beacon_sig = None
    if fft:
        detected = _spectrum_detect_signals(fft, width, plot_h)
        _spectrum_last_detected = detected
        _spectrum_prune_callsigns(detected)
        if detected:
            cand = min(detected, key=lambda s: abs(s["freq"] - BATC_BEACON_NOMINAL_MHZ))
            if abs(cand["freq"] - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
                beacon_raw = cand["raw"]
                beacon_sig = cand

    if fft:
        n = len(fft)
        ck = (n, width)
        if _spec_cols_cache.get("key") != ck:
            cols = []
            for x in range(0, width, 2):
                i0 = min(n - 1, int(x / width * n))
                i1 = max(i0 + 1, min(n, int((x + 2) / width * n)))
                cols.append((x, i0, i1))
            _spec_cols_cache["key"] = ck
            _spec_cols_cache["cols"] = cols
        full = float(_SPECTRUM_FULLSCALE); thr = _SPECTRUM_DRAW_THRESH
        pts = [0, baseline_y]
        ext = pts.extend
        for x, i0, i1 in _spec_cols_cache["cols"]:
            frac = max(fft[i0:i1]) / full
            if frac > 1.0:
                frac = 1.0
            ext((x, plot_h - frac * plot_h) if frac > thr else (x, baseline_y))
        pts.extend([width, baseline_y])
        if app_config.get("spectrum_gradient"):
            _gradient_fill(_spectrum_canvas, pts[0::2], pts[1::2], baseline_y, plot_h * 11.0 / 20.0)
        else:
            _spectrum_canvas.create_polygon(pts, fill="#1a7a1a", outline="#33cc33")
    else:
        _spectrum_canvas.create_text(width // 2, plot_h // 2,
                                      text="Warte auf BATC-Spektrum ...", fill="#888888", font=_sf(9))

    # Bake-Markierung (offizielle Nominalfrequenz).
    _spectrum_canvas.create_line(bx, 0, bx, plot_h, fill="#888833", dash=(2, 2))

    # Beschriftungsblock der Bake, im Vordergrund (nach der Spektrumsflaeche gezeichnet) mit
    # dunklem Hintergrundkasten, damit die Schrift auch auf gruenem Signal lesbar bleibt.
    _spectrum_canvas.create_rectangle(2, height - _SPECTRUM_BOTTOM - 34, 108, height - _SPECTRUM_BOTTOM,
                                       fill="#000000", outline="#888833")
    _spectrum_canvas.create_text(6, height - _SPECTRUM_BOTTOM - 2, text=_SPECTRUM_BEACON_LABEL,
                                  fill="#ffffff", font=_sf(7, True), anchor='sw', justify='left')

    # Fuer die Bake gemerktes Rufzeichen (falls je eines unter ihrer Frequenz decodiert wurde)
    # IMMER ueber ihrem Signal anzeigen - unabhaengig vom Mauszeiger, bleibt stehen solange die
    # Bake selbst im Spektrum zu sehen ist (wie bei jedem anderen Signal, siehe Schleife unten).
    if beacon_sig is not None:
        _bc_call = _spectrum_callsign_for_signal(beacon_sig, width)
        _bc_mid_x = (beacon_sig["start_px"] + beacon_sig["end_px"]) // 2
        if _bc_call:
            _spectrum_canvas.create_text(_bc_mid_x, beacon_sig["top_px"] - 26, text=_bc_call,
                                          fill="#2ecc71", font=_sf(13, True))
        _spectrum_draw_extra_lines(beacon_sig, _bc_mid_x, beacon_sig["top_px"] - 26)

    db_per_pixel = (plot_h * (7.0 / 8.0) - plot_h / 12.0) / 15.0
    manual_mode = bool(_spectrum_manual_var is not None and _spectrum_manual_var.get())

    if fft:
        # Signal-Log (spektrum_signale.csv) unabhaengig vom Anzeigemodus (Rechteck/Manuell) -
        # verfolgt reale, im Spektrum erkannte Signale, nicht nur was gerade angezeigt wird.
        _spectrum_log_track(detected, beacon_raw, baseline_y, db_per_pixel, plot_h, width)

    if manual_mode:
        # Alte, einfache Anzeige: nur ein senkrechter Balken unter der Maus, keine
        # Signalerkennung/-rechteck - freie Frequenzwahl wie vor Einfuehrung der Rechtecke.
        if _spectrum_hover_x is not None and 0 <= _spectrum_hover_x <= width:
            hx = _spectrum_hover_x
            freq = _spectrum_x_to_freq(hx, width)
            _spectrum_canvas.create_line(hx, 0, hx, plot_h, fill="#ffffff")
            _spectrum_canvas.create_text(hx, 14, text="'%.3f" % freq, fill="#ffffff", font=_sf(8), anchor='n')
    else:
        # SR/Frequenz-Beschriftung ("333KS, '10497.250") steht IMMER ueber jedem erkannten
        # Signal, sobald es auftaucht - unabhaengig vom Mauszeiger (anders als das Rechteck,
        # das weiterhin nur beim Hover gezeichnet wird, siehe unten). Die Bake hat bereits
        # ihren eigenen festen Beschriftungskasten (siehe oben) und wird hier ausgelassen.
        for sig in detected:
            if abs(sig["freq"] - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
                continue
            sx0, sx1, top = sig["start_px"], sig["end_px"], sig["top_px"]
            mid_x = (sx0 + sx1) // 2
            if _spectrum_is_overpower(beacon_raw, sig["raw"], sig["sr_mhz"]):
                _spectrum_canvas.create_text(mid_x, top - 10, text="[over-power]", fill="#ffffff", font=_sf(8))
            else:
                label = "%s\n%s" % (_spectrum_print_sr(sig["sr_mhz"]), _spectrum_print_freq(sig["freq"], sig["sr_mhz"]))      # Symbolrate und Frequenz in zwei Zeilen (nebeneinanderliegende Signale ueberlappen sonst)
                _spectrum_canvas.create_text(mid_x, top - 4, text=label, fill="#ffffff", font=_sf(8), anchor="s", justify="center")
            call = _spectrum_callsign_for_signal(sig, width)
            if call:
                _spectrum_canvas.create_text(mid_x, top - 40, text=call, fill="#2ecc71", font=_sf(13, True))
            _spectrum_draw_extra_lines(sig, mid_x, top - 40)

        # Rechteck nur fuer das Signal UNTER DER MAUS (wie render_signal_box() im Original -
        # nicht dauerhaft fuer alle Signale gleichzeitig): offene Box aus 3 Linien von der
        # Rauschkante bis zum Signalgipfel. Beschriftung steht jetzt schon durch die Schleife
        # oben da, hier nur noch bei Bedarf durch "[over-power]" ersetzt (fuer die Bake, die
        # oben ausgelassen wurde) und die gelbe dBb-Zeile ergaenzt.
        if _spectrum_hover_x is not None and 0 <= _spectrum_hover_x <= width:
            hx = _spectrum_hover_x
            sig = next((s for s in detected if s["start_px"] <= hx <= s["end_px"]), None)
            if sig is not None:
                sx0, sx1, top = sig["start_px"], sig["end_px"], sig["top_px"]
                _spectrum_canvas.create_line(sx0, baseline_y, sx0, top, fill="#ffffff")
                _spectrum_canvas.create_line(sx0, top, sx1, top, fill="#ffffff")
                _spectrum_canvas.create_line(sx1, baseline_y, sx1, top, fill="#ffffff")
                mid_x = (sx0 + sx1) // 2
                # Die Bake hat bereits ihren eigenen festen Beschriftungskasten (Rufzeichen,
                # Frequenz, SR/Modulation) - eine zusaetzliche SR/Freq-Zeile beim Hover ueber ihr
                # Rechteck waere nur eine optisch stoerende Dopplung und wird daher ausgelassen.
                if beacon_raw is not None and sx0 > width / 8.0:
                    beacon_y = _spectrum_y_for_raw(beacon_raw, plot_h)
                    diff = (beacon_y - top) / db_per_pixel
                    _spectrum_canvas.create_text(mid_x, top + 12, text="%+.1f dBb" % diff, fill="#ffff00", font=_sf(8))

def _spectrum_settings_open():
    """Einstellungsfenster fuer die konfigurierbaren Zusatzzeilen ueber jedem Signal im
    Spektrum (_spectrum_line_config) - 4 Zeilen, jede waehlbar aus _SPECTRUM_LINE_OPTIONS.
    Speichern uebernimmt die Auswahl sofort (naechster Zeichenzyklus) und schreibt sie in die
    Parameterdatei, damit sie beim naechsten Start erhalten bleibt."""
    win = tk.Toplevel(root)
    win.title("Spektrum-Anzeige: Zusatzzeilen")
    win.resizable(False, False)
    win.transient(root)
    ttk.Label(win, text="Zusaetzliche Zeilen ueber jedem Signal (Zeile 1 = direkt ueber dem\n"
                         "Rufzeichen, jede weitere aktive Zeile stapelt sich eine Position hoeher):",
              justify='left').grid(row=0, column=0, columnspan=2, sticky='w', padx=8, pady=(8, 4))
    label_by_value = _SPECTRUM_LINE_LABELS
    value_by_label = {v: k for k, v in label_by_value.items()}
    combos = []
    for i in range(4):
        ttk.Label(win, text="Zeile %d:" % (i + 1)).grid(row=1 + i, column=0, sticky='w', padx=8, pady=2)
        cur = _spectrum_line_config[i] if i < len(_spectrum_line_config) else "off"
        var = tk.StringVar(value=label_by_value.get(cur, "Aus"))
        cb = ttk.Combobox(win, textvariable=var, state='readonly', width=16,
                           values=[label_by_value[k] for k in _SPECTRUM_LINE_OPTIONS])
        cb.grid(row=1 + i, column=1, sticky='w', padx=8, pady=2)
        combos.append(var)

    ttk.Separator(win, orient='horizontal').grid(row=5, column=0, columnspan=2, sticky='ew', padx=8, pady=(8, 4))

    ttk.Label(win, text="Snapshot-Ordner (VLC-Telnet + mpv-IPC):"
              ).grid(row=6, column=0, columnspan=2, sticky='w', padx=8, pady=(2, 2))
    snap_path_var = tk.StringVar(value=app_config.get("vlc_snapshot_path", ""))
    ttk.Entry(win, textvariable=snap_path_var, width=48
              ).grid(row=7, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 4))

    ttk.Label(win, text="Rufzeichenerkennung bei LongMynd/LongMynd P4:"
              ).grid(row=8, column=0, columnspan=2, sticky='w', padx=8, pady=(2, 2))
    _LM_SRC_LABELS = {"longmynd_udp": "longmynd_udp (schnell, aus Statusstrom Port 4002/4012)",
                       "stream_ip": "Datenstrom IP (ffprobe auf den Videostream, langsamer)"}
    _lm_src_value_by_label = {v: k for k, v in _LM_SRC_LABELS.items()}
    lm_src_var = tk.StringVar(value=_LM_SRC_LABELS.get(
        app_config.get("lm_callsign_source", "longmynd_udp"), _LM_SRC_LABELS["longmynd_udp"]))
    ttk.Combobox(win, textvariable=lm_src_var, state='readonly', width=46,
                 values=list(_LM_SRC_LABELS.values())
                 ).grid(row=9, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 4))

    ttk.Label(win, text="Report-Zusatz (SigReport-Knopf, wie REPORT_SUFFIX in auswertung_4002.sh):"
              ).grid(row=10, column=0, columnspan=2, sticky='w', padx=8, pady=(2, 2))
    report_suffix_var = tk.StringVar(value=app_config.get("report_suffix", "1.8m Dish"))
    ttk.Entry(win, textvariable=report_suffix_var, width=48
              ).grid(row=11, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 4))

    def _save():
        global _spectrum_line_config
        _spectrum_line_config = [value_by_label.get(v.get(), "off") for v in combos]
        new_snap_path = snap_path_var.get().strip() or app_config.get("vlc_snapshot_path")
        if new_snap_path != app_config.get("vlc_snapshot_path"):
            app_config["vlc_snapshot_path"] = new_snap_path
            _vlcrc_apply_snapshot_path(new_snap_path)
        app_config["lm_callsign_source"] = _lm_src_value_by_label.get(lm_src_var.get(), "longmynd_udp")
        app_config["report_suffix"] = report_suffix_var.get().strip()
        app_config_save()
        parameters_save()
        win.destroy()

    btns = ttk.Frame(win)
    btns.grid(row=12, column=0, columnspan=2, pady=(8, 8))
    ttk.Button(btns, text="Speichern", command=_save).pack(side='left', padx=4)
    ttk.Button(btns, text="Abbrechen", command=win.destroy).pack(side='left', padx=4)

def _spectrum_tick():
    """Eigener root.after()-Zyklus (alle 200 ms, unabhaengig vom BATC-Signal-Poll), der
    _spectrum_draw() am Laufen haelt, auch wenn sich die Maus gerade nicht bewegt."""
    _spectrum_draw()
    root.after(int(min(500, max(30, 500.0 / _spectrum_rate()))), _spectrum_tick)

def _spectrum_on_motion(event):
    global _spectrum_hover_x
    _spectrum_hover_x = event.x
    _spectrum_draw()   # durch Signatur/Cache nur bei geaenderter Mausposition

def _spectrum_on_leave(_event):
    global _spectrum_hover_x
    _spectrum_hover_x = None
    _spectrum_draw()

def _verteil_ok(t, need_master=True):
    """Darf die Automatik diesen Tuner umstellen? Nur mit 'Verteil Automatik' (oben rechts) UND 'VerteilA' am Tuner."""
    try:
        if need_master and not batc_auto_master.get():
            return False
        v = _batc_auto.get(t)
        return bool(v is not None and v.get())
    except Exception:
        return False

def _auto_pick_target(freq, sr):
    """'auto': naechster freier Tuner fuer dieses Signal. Steht schon einer darauf -> der. Frei = steht auf
    keinem lebenden Signal oder hat keinen Lock. Sonst der am laengsten nicht umgeschaltete. None = keiner."""
    with _batc_lock:
        alive = [st["freq"] for st in _batc_sig_state.values() if st["gone_since"] is None]
    cands = []
    for t in _active_targets:
        if t == "rtl_sdr" or not _target_available(t) or _scan_active.get(t):
            continue
        if not _verteil_ok(t, need_master=False):
            continue                    # Tuner mit 'VerteilA' aus wird von 'auto' nie umgestellt
        if t == "octagon" and not _octagon_channel_available(freq, sr):
            continue
        cands.append(t)
    if not cands:
        return None
    for t in cands:
        cf = _target_cur_freq.get(t)
        if cf is not None and abs(cf - freq) <= BATC_SIG_TOL:
            return t
    free = []
    for t in cands:
        cf = _target_cur_freq.get(t)
        on_sig = cf is not None and any(abs(cf - f) <= BATC_SIG_TOL for f in alive)
        if not on_sig or _fw_locked(t) is False:
            free.append(t)
    if free:
        return free[0]
    return min(cands, key=lambda t: _target_dispatch_t.get(t, 0.0))

def _spectrum_dispatch_click(event, target):
    """Gemeinsame Klick-Logik fuers Spektrum, fuer ein beliebiges Ziel (statt nur LongMynd):
    rechnet die X-Position in eine QO-100-Downlink-Frequenz um und schaltet das gegebene Ziel
    dorthin. Manuell-Modus (Haken an): freie Frequenzwahl wie bisher, mit Schnappen auf ein
    nahes bekanntes Signal aus _batc_stable. Rechteck-Modus (Standard): nur innerhalb eines
    gerade erkannten Signals wirksam, dann wird dessen MITTENFREQUENZ uebernommen statt der
    rohen Klickposition - wie render_signal_selected_box()+signal_tune() im BATC-Original.

    Welches Ziel per Maustaste gilt, ist aktuell fest verdrahtet (Linksklick = per Knopfreihe
    gewaehltes Ziel, Rechtsklick = immer Octagon, Strg+Klick = immer rtl_sdr) - TODO/spaeter:
    diese Zuordnung in eine Konfigdatei auslagern, damit sie ohne Codeaenderung umstellbar ist."""
    auto = (target == "auto")
    if not auto and target not in _active_targets:
        olog("Spektrum-Klick: %s ist nicht aktiv (fehlendes Flag beim Programmstart)." %
             _TARGET_LABEL.get(target, target))
        return
    width = _spectrum_canvas.winfo_width()
    manual_mode = bool(_spectrum_manual_var is not None and _spectrum_manual_var.get())
    label = _TARGET_LABEL.get(target, target)

    if manual_mode:
        freq = _spectrum_x_to_freq(event.x, width)
        sr = cliargs.longmyndp4_sr_default
        key = None
        with _batc_lock:
            for f, bw in _batc_stable:
                if abs(f - freq) <= 0.15:
                    freq = f
                    sr = batc_sr_from_bw(bw)
                    key = next((k for k, st in _batc_sig_state.items() if abs(st["freq"] - f) <= BATC_SIG_TOL), None)
                    break
        if auto:
            target = _auto_pick_target(freq, sr)
            if target is None:
                olog("Spektrum-Klick (auto): kein freier Tuner fuer %.3f MHz." % freq)
                return
            label = "auto -> " + _TARGET_LABEL.get(target, target)
        olog("Spektrum-Klick @%.3f MHz -> %s (%s kS/s)" % (freq, label, sr))
        target_dispatch(target, round(freq, 3), sr, key)
        return

    fft = _batc_last_fft
    if not fft:
        olog("Spektrum-Klick: noch kein BATC-Spektrum empfangen.")
        return
    height = _spectrum_canvas.winfo_height()
    plot_h = height - _SPECTRUM_BOTTOM
    sig = next((s for s in _spectrum_detect_signals(fft, width, plot_h)
                if s["start_px"] <= event.x <= s["end_px"]), None)
    if sig is None:
        olog("Spektrum-Klick: an dieser Stelle ist gerade kein Signal erkannt.")
        return
    freq = round(sig["freq"], 3)
    sr = int(round(sig["sr_mhz"] * 1000))
    key = None
    with _batc_lock:
        key = next((k for k, st in _batc_sig_state.items() if abs(st["freq"] - freq) <= BATC_SIG_TOL), None)
    if auto:
        target = _auto_pick_target(freq, sr)
        if target is None:
            olog("Spektrum-Klick (auto): kein freier Tuner fuer %.3f MHz." % freq)
            return
        label = "auto -> " + _TARGET_LABEL.get(target, target)
    olog("Spektrum-Klick @Signalmitte %.3f MHz -> %s (%s kS/s)" % (freq, label, sr))
    target_dispatch(target, freq, sr, key)

def _spectrum_on_click(event):
    """Normaler Linksklick: schaltet das Ziel, das per Knopfreihe unter dem Spektrum
    ausgewaehlt ist (gruener Knopf) - ausser Strg ist gedrueckt, dann immer rtl_sdr."""
    if event.state & 0x0004:   # Control-Taste (X11/Windows-Modifier-Bit fuer Control)
        _spectrum_dispatch_click(event, "rtl_sdr")
        return
    target = _spectrum_target_var.get() if _spectrum_target_var is not None else "longmynd"
    _spectrum_dispatch_click(event, target)

def _spectrum_on_rightclick(event):
    """Rechtsklick ins Spektrum: schaltet immer Octagon, unabhaengig von der Knopfreihen-Auswahl."""
    _spectrum_dispatch_click(event, "octagon")

def batc_start():
    """Startet die BATC-Websocket-Verbindung einmalig im Hintergrund (daemon-Thread)."""
    global _batc_started
    if _batc_started or not HAVE_WEBSOCKET:
        return
    _batc_started = True
    def run():
        while True:
            try:
                def _on_msg(ws, msg):
                    _batc_last_msg[0] = time.time()
                    _spq["batc_t"] = time.time()
                    if _spq["active"] in ("stick", "onair"):          # eigene Quelle (Stick/OnAir) ist aktiv: BATC-Bilder nur als Lebenszeichen
                        return
                    try:
                        batc_analyze_signals(struct.unpack("<%dH" % (len(msg) // 2), msg))
                    except Exception as e:
                        _poll_log("Fehler in batc_analyze_signals: %r" % (e,))
                _app = websocket.WebSocketApp(
                    BATC_FFT_URL, header=["Sec-WebSocket-Protocol: fft_m0dtslivetune"],
                    on_message=_on_msg)
                _batc_ws[0] = _app
                _batc_last_msg[0] = time.time()
                # Ping/Pong: eine halboffene Verbindung (Server weg, aber kein FIN) wird sonst nie
                # bemerkt - run_forever() blockierte dann endlos, die Signalliste fror ein.
                _app.run_forever(ping_interval=15, ping_timeout=8)
            except Exception:
                pass
            time.sleep(5)          # Verbindung verloren -> nach 5 s neu versuchen
    threading.Thread(target=run, daemon=True).start()
    lbl_batc_status.config(text="BATC: verbunden, warte auf Signale ...")
    batc_poll()
    root.after(8000, _poll_watchdog)

def _batc_force_reconnect(grund):
    """Schliesst die BATC-Websocket-Verbindung; die Schleife in batc_start() baut sie nach 5 s neu auf."""
    _poll_log("BATC-Verbindung wird neu aufgebaut (%s)" % grund)
    ws = _batc_ws[0]
    _batc_last_msg[0] = time.time()    # Watchdog nicht sofort erneut ausloesen
    if ws is not None:
        def _close():
            try:
                ws.close(timeout=1)
            except TypeError:
                ws.close()
            except Exception:
                pass
        threading.Thread(target=_close, daemon=True).start()

def batc_refresh():
    """'Refresh BATC': verwirft die lokal gemerkte Signalliste komplett, sodass sie beim
    naechsten Poll wieder frisch aus den fortlaufend eintreffenden BATC-Server-Daten aufgebaut
    wird (z.B. wenn sich der Server-Zustand stark geaendert hat oder die Anzeige haengt).
    Baut bei Bedarf auch die Verbindung ueberhaupt erst auf (wie Klick auf 'Verbinden')."""
    global _batc_struct_sig
    with _batc_lock:
        _batc_sig_state.clear()
    _batc_struct_sig = None
    for _t in _active_targets:
        for w in _target_siglist_frames[_t].winfo_children():
            w.destroy()
    _batc_row_cache.clear()
    olog("BATC-Signalliste wird neu eingelesen ...")
    if not _batc_started:
        batc_start()
    else:
        _batc_force_reconnect("Refresh BATC")
        _poll_log("Poll-Kette neu gestartet (Refresh BATC)")
        batc_poll()      # neue Generation - die alte Kette beendet sich selbst

def batc_redistribute():
    """'Neu verteilen': markiert alle Ziele wieder als frei, damit die Automatik-Verteilung
    (batc_auto_assign) beim naechsten Durchlauf komplett neu von vorne beginnt - unabhaengig
    davon, was vorher zugeteilt war."""
    for t in _active_targets:
        _target_last_tuned[t] = None
    if not batc_auto_master.get():
        olog("Neu verteilen: 'Verteil Automatik' (oben rechts) ist AUS - es wird nichts verteilt.")
        return
    _batc_redistribute_full()

#----- Prioritaeten (Menue Prio): Rang je Tuner-Klasse - eine Reihenfolge fuer die VERGABE neuer Signale, eine fuer das ABSCHALTEN doppelter Signale -----
_PRIO_CLASSES = (("octagon", "Octagon"), ("longmynd", "LongMynd (1, 2, P4)"), ("onair", "OnAir (SDRplay, HackRF ...)"),
                 ("rtlsdr", "RTL-SDR (LeanDVB, datvRX lokal)"), ("dienst", "RTL-Dienst (Sticks am Pi)"))
_PRIO_DEFAULT = {"octagon": 1, "longmynd": 1, "onair": 2, "rtlsdr": 3, "dienst": 3}      # kleinere Zahl = hoeherer Rang; gleiche Zahl = gleicher Rang

def _tuner_class(t):
    if t == "octagon":
        return "octagon"
    if t in ("longmynd", "longmynd2", "longmynd_p4"):
        return "longmynd"
    if str(t).startswith("onair"):
        return "onair"
    if str(t).startswith("datvrx"):
        try:
            return "dienst" if _dvx_is_dienst(t) else "rtlsdr"
        except NameError:
            return "rtlsdr"
    return "rtlsdr"

def _prio_rank(t, which):
    """Rang des Tuners t: which = 'vergabe' (wer bekommt neue Signale zuerst) oder 'doppelt' (wer behaelt bei doppeltem Signal, wer wird abgeschaltet)"""
    try:
        cfg = app_config.get("prio_" + which) or {}
    except NameError:
        cfg = {}
    c = _tuner_class(t)
    try:
        return int(cfg.get(c, _PRIO_DEFAULT[c]))
    except (TypeError, ValueError):
        return _PRIO_DEFAULT[c]

def _redistribute_plan(live, targets, cur, rest=False, oct_ok=None, dispatch_t=None, tol=0.2, rank=None):
    """Reine Planung (ohne Seiteneffekte, testbar):
    live: [(key, {"freq","sr","born"})], targets: [Tuner], cur: {Tuner: Frequenz oder None}.
    rest=False ('Neu verteilen'): jedes Signal hoechstens ein Tuner; bei Ueberzahl die NEUESTEN; Tuner, die allein
              auf einem gewuenschten Signal stehen, bleiben.
    rest=True  ('NeuNeuVerteilen'): die gerade NICHT versorgten Signale (neueste zuerst) kommen auf die Tuner -
              zuerst freie/doppelte Tuner, dann die am laengsten unveraenderten.
    Liefert (keep {t:key}, plan [(t,key)], unversorgt [key], weggelassen [key])."""
    oct_ok = oct_ok or (lambda t, st: True)
    dispatch_t = dispatch_t or {}
    rank = rank or (lambda t: 0)
    info = dict(live)
    def key_at(f):
        best = None
        for k, st in live:
            d = abs(st["freq"] - f)
            if d <= tol and (best is None or d < best[0]):
                best = (d, k)
        return best[1] if best else None
    on = {t: (key_at(cur[t]) if cur.get(t) is not None else None) for t in targets}
    newest = sorted(live, key=lambda kv: kv[1].get("born", 0), reverse=True)
    keep, used, plan = {}, set(), []
    if not rest:
        wanted = [k for k, _ in newest[:len(targets)]]
        for t in targets:
            k = on[t]
            if k is not None and k in wanted and k not in used:
                keep[t] = k
                used.add(k)
        order = [k for k in wanted if k not in used] + [k for k, _ in newest[len(targets):]]
        free = [t for t in targets if t not in keep]
    else:
        counts = {}
        for t in targets:
            if on[t] is not None:
                counts[on[t]] = counts.get(on[t], 0) + 1
        unserved = [k for k, _ in newest if k not in counts]
        # Reihenfolge der Tuner, die wechseln: frei -> doppelt belegt -> am laengsten unveraendert
        def prio(t):
            k = on[t]
            return (0 if k is None else (1 if counts.get(k, 0) > 1 else 2), rank(t), dispatch_t.get(t, 0.0))
        # je unversorgtem Signal (neueste zuerst) den passendsten Tuner waehlen, der es auch empfangen kann
        cand = sorted(targets, key=prio)
        for k in unserved:
            t = next((t for t in cand if oct_ok(t, info[k])), None)
            if t is None:
                continue
            cand.remove(t)
            plan.append((t, k))
            used.add(k)
        free = []
        for t in sorted(cand, key=lambda t: dispatch_t.get(t, 0.0)):   # alle anderen bleiben, wo sie sind ...
            if on[t] is not None and on[t] not in used:
                keep[t] = on[t]
                used.add(on[t])
            else:
                free.append(t)                               # ... ausser frei oder zweiter auf demselben Signal
        # diese Tuner nehmen, was noch unversorgt bzw. gerade frei geworden ist (neueste zuerst)
        order = [k for k, _ in newest if k not in used]
    free.sort(key=lambda t: (rank(t), 0 if t == "octagon" else 1))        # Rang aus dem Menue Prio (Vergabe); darin Octagon zuerst (braucht passenden Kanal)
    order = list(order)
    for t in free:
        k = next((k for k in order if oct_ok(t, info[k])), None)
        if k is None:
            continue
        order.remove(k)
        plan.append((t, k))
        used.add(k)
    planned = {t for t, _ in plan}
    for t in free:                       # bekam nichts Passendes (z.B. Octagon ohne Kanal): bleibt auf seinem Signal
        if t not in planned and on.get(t) is not None and on[t] not in used:
            keep[t] = on[t]
            used.add(on[t])
    unv = [k for k, _ in newest if k not in used and k not in [kk for kk in keep.values() if kk]]
    dropped = [] if rest else [k for k in unv if k not in [k2 for k2, _ in newest[:len(targets)]]]
    return keep, plan, unv, dropped

def _batc_redistribute_full(rest=False):
    """'Neu verteilen' (rest=False) bzw. 'NeuNeuVerteilen' (rest=True) - Planung in _redistribute_plan()."""
    L = lambda t: _TARGET_LABEL.get(t, t)
    name = "NeuNeuVerteilen" if rest else "Neu verteilen"
    with _batc_lock:
        live = [(k, dict(st)) for k, st in _batc_sig_state.items()
                if st["gone_since"] is None and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5]
    targets, skipped = [], []
    for t in _active_targets:
        var = _batc_auto.get(t)
        if var is None or not var.get():
            skipped.append("%s: VerteilA aus" % L(t))
        elif not _target_available(t):
            skipped.append("%s: nicht verfuegbar" % L(t))
        elif _scan_active.get(t):
            skipped.append("%s: Scan laeuft" % L(t))
        elif _max.get("t") == t:
            skipped.append("%s: maximiert" % L(t))
        else:
            targets.append(t)
    if not live or not targets:
        olog("%s: %s." % (name, "keine Signale im Spektrum" if not live else
                          "kein Tuner mit VerteilA (%s)" % "; ".join(skipped)))
        return
    info = dict(live)
    keep, plan, unv, dropped = _redistribute_plan(
        live, targets, {t: _target_cur_freq.get(t) for t in targets}, rest=rest,
        oct_ok=lambda t, st: t != "octagon" or _octagon_channel_available(st["freq"], st["sr"]),
        dispatch_t=dict(_target_dispatch_t), tol=BATC_SIG_TOL, rank=lambda t: _prio_rank(t, "vergabe"))
    for t, k in keep.items():
        if k is not None:
            _target_last_tuned[t] = k
    for t, k in plan:
        st = info[k]
        _target_last_tuned[t] = k
        root.after(0, lambda tt=t, f=st["freq"], sr=st["sr"], kk=k: target_dispatch(tt, f, sr, kk))
    msg = ["%s: %d Signal(e), %d Tuner" % (name, len(live), len(targets))]
    kept = [(t, k) for t, k in keep.items() if k is not None]
    if kept:
        msg.append("bleiben: " + ", ".join("%s %.3f" % (L(t), info[k]["freq"]) for t, k in kept))
    msg.append(("neu: " + ", ".join("%s -> %.3f MHz" % (L(t), info[k]["freq"]) for t, k in plan)) if plan
               else ("keine unversorgten Signale - nichts zu tun" if rest else "keine Aenderung noetig - alle Signale versorgt"))
    if unv:
        msg.append("noch unversorgt: " + ", ".join("%.3f" % info[k]["freq"] for k in unv)
                   + ("  (NeuNeuVerteilen zeigt sie)" if not rest else ""))
    if skipped:
        msg.append("; ".join(skipped))
    olog(" | ".join(msg))
    _vlog("neuneuverteilen" if rest else "neu_verteilen", None, None, "", "",
          "plan: %s | unversorgt: %s" % (", ".join("%s->%.3f" % (L(t), info[k]["freq"]) for t, k in plan) or "-",
                                          ", ".join("%.3f" % info[k]["freq"] for k in unv) or "-"))

def batc_redistribute_rest():
    """Knopf 'NeuNeuVerteilen': die noch nicht versorgten Signale auf die Tuner (bei mehr Signalen als Tunern)."""
    if not batc_auto_master.get():
        olog("NeuNeuVerteilen: 'Verteil Automatik' (oben rechts) ist AUS - es wird nichts verteilt.")
        return
    _batc_redistribute_full(rest=True)

def batc_take(freq, sr):
    """Klick auf ein BATC-Signal: Frequenz/Symbolrate/Bandbreite/FEC uebernehmen und neu starten.
    Bei der Bake (Frequenz nahe BATC_BEACON_NOMINAL_MHZ) ist die FEC fest bekannt (QPSK 4/5,
    siehe _SPECTRUM_BEACON_LABEL) - das direkt setzen statt "auto" spart leandvb das Durchprobieren
    und bringt den Lock schneller/zuverlaessiger."""
    frequency.set(round(freq, 3))
    symbolrate.set(sr)
    bandwidth.set(QO100_BW_BY_SR.get(sr, sr * 2))
    fec.set("4/5" if abs(freq - BATC_BEACON_NOMINAL_MHZ) <= 0.5 else "auto")
    on_stop()
    root.after(400, on_start)

def _batc_make_row(text, freq, sr, key=None):
    """Baut fuer ein Signal (Bake oder erkannter Kanal) EINEN Knopf je aktivem Ziel - jeder Knopf
    landet in der eigenen Signal-Liste seines Ziels (_target_siglist_frames[target]), nicht mehr
    in einer gemeinsamen, spaltenbasierten Liste (das fuehrte dazu, dass die Knoepfe eines Ziels
    optisch unter der falschen Kopfzeile landeten, sobald die Ziele unterschiedlich breite
    Kontrollelemente hatten). Reihenfolge der Aufrufe in batc_rebuild_list() (Bake zuerst, dann
    nach Frequenz sortiert) bestimmt die Reihenfolge von oben nach unten je Ziel-Spalte."""
    state = {"freq": freq, "sr": sr}
    buttons = []
    for target in _active_targets:
        b = ttk.Button(_target_siglist_frames[target], text=text, width=(_MINI_SIG_W if globals().get("_mini", {}).get("on") else 34),
                        command=lambda t=target, st=state, k=key: target_dispatch(t, st["freq"], st["sr"], k))
        b.pack(side='top', anchor='w', fill='x', pady=1)
        buttons.append(b)
    return {"text": text, "state": state, "buttons": buttons, "key": key}

_batc_struct_sig = None    # (Anzahl Ziele, Anzahl Zeilen) - aendert sich nur bei neuem/verschwundenem Signal
_batc_row_cache  = []      # Zeilen-Widgets der letzten Darstellung (fuer reines Text-Update ohne Neuaufbau)

def _batc_style_for(target, key):
    """Ampel-Style fuer eine Signal-Zelle (Ziel + Signal): blau = genau dieses Ziel ist gerade auf
    dieses Signal gestellt (manuell oder Automatik), rot = Signal verschwunden (< 10 s), gruen =
    neu erkannt (< 30 s), sonst normal. Blau hat Vorrang, damit auf einen Blick sichtbar ist,
    wohin die Automatik/manuelle Auswahl gerade verteilt hat."""
    if key is None:
        return "TButton"
    with _batc_lock:
        st = _batc_sig_state.get(key)
        if st is None:
            return "TButton"
        now = time.time()
        if _target_last_tuned.get(target) == key and st["gone_since"] is None:
            return "SigAssigned.TButton"
        if st["gone_since"] is not None:
            return "SigGone.TButton"
        if now - st["born"] < BATC_NEW_GREEN_S:
            return "SigNew.TButton"
        return "TButton"

def batc_apply_colors():
    """Aktualisiert nur die Hintergrundfarbe der bestehenden Knoepfe (blau/gruen/rot/normal),
    ohne Struktur-Neuaufbau - wird bei jedem Poll aufgerufen, damit sich die Farbe auch ohne
    neues/verschwundenes Signal aktualisiert (z.B. nach Ablauf der 30 s/10 s, oder sobald die
    Automatik/ein Klick ein Ziel neu zuteilt)."""
    for cache_row in _batc_row_cache:
        key = cache_row["key"]
        for target, b in zip(_active_targets, cache_row["buttons"]):
            style = _batc_style_for(target, key)
            cur = str(b.cget("style")) or "TButton"
            if cur != style:
                b.configure(style=style)

def _octagon_channel_available(freq_mhz, sr):
    """Prueft (ohne umzuschalten), ob fuer diese Frequenz/Symbolrate ein passender Octagon-Kanal
    existiert - fuer die Automatik, damit sie bei einer Luecke im Kanalplan (Octagon braucht dafuer
    fest angelegte Kanaele, kann nicht wie LongMynd/rtl_sdr frei tunen) dieses Signal einem anderen,
    frei tunbaren Ziel gibt, statt Octagon erfolglos/dauerhaft darauf zu setzen."""
    if _oct_direct():
        return True          # Direktmodus: frei abstimmbar, keine Kanalliste noetig
    if not _octagon_channels:
        return False
    x = freq_to_channel_mhz(normalize_freq_mhz(freq_mhz))
    ch, _why = choose_channel(_octagon_channels, x, normalize_sr_ksps(sr))
    return ch is not None

def batc_auto_assign(report=False):
    """Verteilt neu gefundene Signale automatisch auf alle Ziele, deren Automatik-Haken gesetzt
    ist UND deren Tuner gerade frei ist (noch nie getuned, oder das zuletzt getunte Signal ist
    weg/rot). Es werden dabei immer die ersten freien Signale (nach Frequenz sortiert) genommen,
    die nicht schon einem anderen (noch aktiven) Ziel zugeteilt sind - je ein Signal pro freiem
    Automatik-Ziel, in der Reihenfolge der Spalten (rtl_sdr, Octagon, LongMynd).
    Der globale Hauptschalter 'autom. Verteilen' (batc_auto_master) schaltet die gesamte
    Funktion generell aus, unabhaengig von den einzelnen Ziel-Haken."""
    if not batc_auto_master.get():
        return
    if _hold["active"]:
        return                                        # Start-Pruefung laeuft: Tuner und bestehende Player nicht veraendern
    with _batc_lock:
        candidates = [k for k, st in sorted(_batc_sig_state.items(), key=lambda kv: kv[1]["freq"])
                      if st["gone_since"] is None]
        busy = set()

        def _key_at(freq):
            """Schluessel des lebenden Signals, auf dem eine Frequenz liegt (naechstes innerhalb BATC_SIG_TOL)."""
            best = None
            for k in candidates:
                d = abs(_batc_sig_state[k]["freq"] - freq)
                if d <= BATC_SIG_TOL and (best is None or d < best[0]):
                    best = (d, k)
            return best[1] if best else None
        # Was ein Tuner TATSAECHLICH eingestellt hat, zaehlt - auch wenn es per QRG-Dialog, Spektrum-Klick
        # oder Scan geschah (dort ist key=None und _target_last_tuned wurde nie gesetzt): das Signal ist
        # dann belegt, und der Tuner gilt nicht als frei. Vorher konnten dadurch zwei Tuner auf dasselbe
        # Signal laufen und andere Signale unverteilt bleiben.
        tuned = {}
        for t in _active_targets:
            cf = _target_cur_freq.get(t)
            tuned[t] = _key_at(cf) if cf else None
            if tuned[t] is not None:
                busy.add(tuned[t])
            elif cf is None:
                # noch nie eine Frequenz geschickt (z.B. gerade erst zugeteilt): letzte Zuteilung zaehlt.
                # Ist die tatsaechliche Frequenz bekannt, aber weg vom Signal, ist die Zuteilung veraltet
                # (vorher blockierte so ein Eintrag das Signal fuer alle anderen Tuner).
                lv = _target_last_tuned.get(t)
                if lv is not None and lv in candidates:
                    busy.add(lv)
        avail = [k for k in candidates if k not in busy]
        _trace = []
        _assigned = []

        for target in sorted(_active_targets, key=lambda t: _prio_rank(t, "vergabe")):      # Reihenfolge der Vergabe: Menue Prio (stabil: sonst Spaltenreihenfolge)
            var = _batc_auto.get(target)
            if var is None or not var.get():
                _trace.append("%s: VerteilA aus" % _TARGET_LABEL.get(target, target))
                continue
            if not _target_available(target):
                _trace.append("%s: nicht verfuegbar" % _TARGET_LABEL.get(target, target))
                continue
            last = _target_last_tuned.get(target)
            free = (tuned.get(target) is None) and not _scan_active.get(target)
            if not free:
                _trace.append("%s: bleibt (%s)" % (_TARGET_LABEL.get(target, target), "Scan laeuft" if _scan_active.get(target) else
                              "%.3f MHz" % _batc_sig_state[tuned[target]]["freq"]))
                continue

            key = None
            # Beim allerersten Zuteilen dieses Ziels in dieser Sitzung: falls aus verteiler.log
            # bekannt ist, worauf es zuletzt stand, und genau dieses Signal noch online ist,
            # dieselbe Verteilung wiederherstellen (zusaetzliche/andere Signale dabei ignorieren).
            # Ist es nicht mehr online, ganz normal (naechstes freies Signal) neu verteilen.
            if last is None and target in _verteiler_saved:
                saved_freq = _verteiler_saved.pop(target)
                key = next((k for k in avail if abs(_batc_sig_state[k]["freq"] - saved_freq) <= BATC_SIG_TOL), None)
                if key is not None and target == "octagon":
                    st_s = _batc_sig_state[key]
                    if not _octagon_channel_available(st_s["freq"], st_s["sr"]):
                        dlog("Automatik: gespeicherte Octagon-Zuteilung (%.3f MHz) hat keinen "
                             "passenden Kanal mehr - ignoriere sie." % st_s["freq"])
                        key = None

            if key is None:
                if target == "octagon":
                    # Signale in Frequenz-Reihenfolge durchgehen, aber nur eins nehmen, fuer das
                    # auch wirklich ein Kanal existiert - dazwischenliegende, fuer Octagon
                    # ungeeignete Signale NICHT aus avail entfernen, damit sie in dieser (oder
                    # der naechsten) Runde einem anderen, frei tunbaren Ziel zufallen koennen.
                    key = next((k for k in avail if _octagon_channel_available(
                        _batc_sig_state[k]["freq"], _batc_sig_state[k]["sr"])), None)
                    if key is None:
                        _trace.append("Octagon: frei, aber kein Signal mit passendem Kanal (frei: %s)" %
                                      ", ".join("%.3f" % _batc_sig_state[k]["freq"] for k in avail))
                        continue
                else:
                    if not avail:
                        _trace.append("%s: frei, aber kein freies Signal uebrig" % _TARGET_LABEL.get(target, target))
                        continue
                    key = avail[0]

            avail.remove(key)
            _trace.append("%s -> %.3f MHz" % (_TARGET_LABEL.get(target, target), _batc_sig_state[key]["freq"]))
            _assigned.append(_trace[-1])

            st = _batc_sig_state[key]
            freq, sr = st["freq"], st["sr"]
            root.after(0, lambda t=target, f=freq, s=sr, k=key: target_dispatch(t, f, s, k))

        # Statuszeile: jede echte Zuteilung immer melden, die komplette Begruendung bei "Neu verteilen"
        # oder wenn sie sich geaendert hat (nicht alle 2 s dasselbe)
        global _auto_trace_last
        sig_txt = ", ".join("%.3f" % _batc_sig_state[k]["freq"] for k in candidates
                            if abs(_batc_sig_state[k]["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5) or "-"
        txt = "Verteilung: Signale %s | %s" % (sig_txt, "; ".join(_trace) or "kein Tuner mit VerteilA")
        cmp = re.sub(r"(\d+\.\d\d)\d", r"\1", txt)     # 1-kHz-Zappeln der Frequenzen nicht als Aenderung werten
        if report or _assigned or cmp != _auto_trace_last:
            _auto_trace_last = cmp
            root.after(0, olog, txt)

_auto_trace_last = ""

def _call_for_freq(f):
    """Gemerktes (decodiertes) Rufzeichen auf dieser Frequenz oder None."""
    try:
        for ff, name in list(_spectrum_known_callsigns.items()):
            if abs(ff - f) <= _SPECTRUM_CALLSIGN_TOL:
                return name
    except Exception:
        pass
    return None

_call_sig_last = [None]
def _call_sig_changed():
    sig = tuple(sorted(_spectrum_known_callsigns.items()))
    if sig != _call_sig_last[0]:
        _call_sig_last[0] = sig
        return True
    return False

def batc_rebuild_list():
    global _batc_struct_sig, _batc_row_cache
    with _batc_lock:
        sigs = sorted(_batc_sig_state.items(), key=lambda kv: kv[1]["freq"])
    rows_wanted = [("Bake 10491.500 MHz", 10491.500, 1500, None)]
    for key, st in sigs:
        f, bw, sr = st["freq"], st["bw"], st["sr"]
        tag = "  [weg]" if st["gone_since"] is not None else ""
        _cs = _call_for_freq(f)
        txt = "%9.3f MHz   %d kS/s%s%s" % (f, sr, ("  " + _cs) if _cs else "", tag)
        rows_wanted.append((txt, f, sr, key))

    sig = (len(_active_targets), len(rows_wanted))
    if sig != _batc_struct_sig:
        # Anzahl Ziele oder Anzahl Zeilen (Signale) hat sich geaendert - hier bleibt nur der
        # komplette Neuaufbau. Das ist der seltene, echte Aenderungsfall (neues/weg-gefallenes
        # Signal), nicht das staendige Nachzittern der Frequenz eines bestehenden Signals.
        for _t in _active_targets:
            for w in _target_siglist_frames[_t].winfo_children():
                w.destroy()
        _batc_row_cache = []
        for text, freq, sr, key in rows_wanted:
            _batc_row_cache.append(_batc_make_row(text, freq, sr, key))
        if not sigs and _active_targets:
            ttk.Label(_target_siglist_frames[_active_targets[0]],
                      text="(keine weiteren Signale erkannt)").pack(side='top', anchor='w', pady=4)
        _batc_struct_sig = sig
        batc_apply_colors()
        _longmyndp4_apply_state(force=True)
        return

    # Struktur unveraendert: Knoepfe bleiben stehen, nur betroffene Zeilen bekommen neuen Text/Werte.
    for cache_row, (text, freq, sr, key) in zip(_batc_row_cache, rows_wanted):
        if cache_row["text"] != text:
            cache_row["text"] = text
            cache_row["state"]["freq"] = freq
            cache_row["state"]["sr"] = sr
            for b in cache_row["buttons"]:
                b.config(text=text)
    batc_apply_colors()

_poll_gen = [0]          # Generation der Poll-Kette (verhindert doppelte Ketten nach Neustart)
_poll_last = [0.0]       # Zeitpunkt des letzten kompletten Durchlaufs (Herzschlag)
_poll_errors = [0]
_verify_bad = [0]        # aufeinanderfolgende Pruefungen mit unvollstaendiger Verteilung

def _poll_log(msg):
    print("[Verteilung] %s" % msg, flush=True)
    try:
        with open(VERTEILER_LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), "# " + msg))
    except OSError:
        pass

def _step(name, fn):
    """Jeder Schritt einzeln abgesichert: eine Ausnahme darf die Verteil-Schleife NIE beenden
    (frueher fiel batc_poll bei einer Ausnahme vor root.after() aus - dann lief die Automatik nie wieder)."""
    try:
        return fn()
    except Exception:
        import traceback
        _poll_errors[0] += 1
        _poll_log("Fehler in %s (Nr. %d): %s" % (name, _poll_errors[0], traceback.format_exc().strip().splitlines()[-1]))
        if cliargs.debug:
            traceback.print_exc()
        return None

def _auto_verify():
    """Prueft, ob die Verteilung zu den aktuellen Signalen passt: gibt es lebende Signale, die kein Tuner
    bedient, waehrend ein Automatik-Tuner frei und verfuegbar ist? Dann wird nach 3 Pruefungen (~15 s)
    gemeldet und die veralteten Zuteilungen werden verworfen."""
    if not batc_auto_master.get():
        _verify_bad[0] = 0
        return
    with _batc_lock:
        alive = {k: st["freq"] for k, st in _batc_sig_state.items() if st["gone_since"] is None}
    served = set()
    free_targets = []
    for t in _active_targets:
        cf = _target_cur_freq.get(t)
        hit = None
        if cf:
            for k, f in alive.items():
                if abs(f - cf) <= BATC_SIG_TOL:
                    hit = k
                    break
        if hit is not None:
            served.add(hit)
        else:
            var = _batc_auto.get(t)
            if var is not None and var.get() and _target_available(t) and not _scan_active.get(t):
                free_targets.append(t)
    unserved = [k for k in alive if k not in served]
    if unserved and free_targets:
        _verify_bad[0] += 1
        if _verify_bad[0] >= 3:
            _poll_log("Verteilung unvollstaendig: frei=%s, unversorgt=%s -> Zuteilungen zurueckgesetzt" % (
                ",".join(free_targets), ",".join("%.3f" % alive[k] for k in unserved)))
            for t in free_targets:
                _target_last_tuned[t] = None
            _verify_bad[0] = 0
            batc_auto_assign()
    else:
        _verify_bad[0] = 0

def batc_poll(gen=None):
    if gen is None:
        _poll_gen[0] += 1
        gen = _poll_gen[0]
    if gen != _poll_gen[0]:
        return   # veraltete Kette (nach Watchdog-Neustart)
    try:
        def _a():
            if batc_tick() | _call_sig_changed():
                batc_rebuild_list()
            else:
                # keine Struktur-Aenderung, aber Ampel-Farben koennen trotzdem faellig sein
                # (30 s gruen bzw. 10 s rot laufen unabhaengig von neuen/verschwundenen Signalen ab)
                batc_apply_colors()
        _step("batc_tick/rebuild", _a)
        # Bake-Abweichung/LO-Korrektur laeuft eigenstaendig 1x/Sekunde ueber _lo_correct_tick()
        _step("longmynd_p4_state", _longmyndp4_apply_state)
        _step("auto_assign", batc_auto_assign)
        if int(time.time()) % 5 < 2:
            _step("auto_verify", _auto_verify)
        _poll_last[0] = time.time()
    finally:
        root.after(2000, lambda g=gen: batc_poll(g))

def _poll_watchdog():
    """Faengt den Fall ab, dass die Poll-Kette nicht mehr laeuft (Herzschlag aelter als 12 s)."""
    try:
        # Datenstrom-Watchdog: laeuft die Poll-Kette, aber es kommen keine BATC-Bilder mehr an
        # (Websocket haengt), friert die Signalliste ein - dann Verbindung neu aufbauen.
        if _batc_started and _batc_last_msg[0] and time.time() - _batc_last_msg[0] > 20:
            _batc_force_reconnect("seit %.0f s keine BATC-Daten" % (time.time() - _batc_last_msg[0]))
        if _poll_last[0] and time.time() - _poll_last[0] > 12:
            _poll_log("batc_poll lief %.0f s nicht mehr -> neu gestartet" % (time.time() - _poll_last[0]))
            _poll_last[0] = time.time()
            batc_poll()
    finally:
        root.after(5000, _poll_watchdog)

#===== Octagon (openATV) + LongMynd Steuerung (Schritt 2, Routinen aus octagon_autom.py) ======
# Aktiv nur mit -octagon bzw. -longmynd; ohne Parameter bleibt batc_take() (lokaler rtl_sdr) aktiv.

GUI_LOG = os.path.join(parameters_path, "gui.log")
_gui_log_lock = threading.Lock()

def _gui_log_write(line):
    """Alles, was auf der Konsole erscheint, auch in ~/.leandvb-GUI/gui.log (ab 5 MB -> gui.log.1)."""
    try:
        with _gui_log_lock:
            if os.path.exists(GUI_LOG) and os.path.getsize(GUI_LOG) > 5 * 1024 * 1024:
                os.replace(GUI_LOG, GUI_LOG + ".1")
            with open(GUI_LOG, "a", encoding="utf-8", errors="replace") as fh:
                fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass

def olog(msg):
    """Log-Ausgabe: immer auf der Konsole, kurze Fassung zusaetzlich im Statuslabel der GUI."""
    print(msg, flush=True)
    _gui_log_write(str(msg))
    try:
        first = str(msg).strip().split("\n")[0][:70]
        root.after(0, lambda: lbl_batc_status.config(text=first))
    except Exception:
        pass

def dlog(msg):
    """Zusaetzliche, ausfuehrliche Log-Zeile - nur wenn -debug (cliargs.debug) gesetzt ist.
    Erscheint NICHT im GUI-Statuslabel (nur auf der Konsole), damit das Statuslabel bei
    -debug nicht mit technischen Details ueberschrieben wird."""
    if cliargs.debug:
        print("[debug] %s" % msg, flush=True)
        _gui_log_write("[debug] %s" % msg)

OCTAGON_DEBUG_LOG = os.path.join(parameters_path, "octagon_debug.log")
_octagon_dbg_lock = threading.Lock()

def _odbg(msg):
    """Dauerhaftes Octagon-Debug-Log (~/.leandvb-GUI/octagon_debug.log), unabhaengig von -debug:
    Signal -> Kanalwahl -> Zap -> Pruefung -> Status. Zusaetzlich bei -debug auf der Konsole."""
    line = "%s\t%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with _octagon_dbg_lock:
            try:
                if os.path.getsize(OCTAGON_DEBUG_LOG) > 2_000_000:
                    os.replace(OCTAGON_DEBUG_LOG, OCTAGON_DEBUG_LOG + ".1")
            except OSError:
                pass
            with open(OCTAGON_DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass
    dlog("[octagon] %s" % msg)

def set_status(text, gen=None):
    if gen is not None and gen != _switch_gen:
        return
    olog(text)

def switched(kind, freq_mhz, sr, name, status):
    set_status(status)

def fm(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")

def normalize_freq_mhz(value):
    v = float(value)
    if v > 1e8:
        v /= 1e6
    elif v > 1e6:
        v /= 1e3
    return v

def freq_to_channel_mhz(freq_mhz):
    return freq_mhz - 10000.0 if freq_mhz >= 9000 else freq_mhz

def normalize_sr_ksps(value):
    s = float(value)
    if s >= 100000:
        s /= 1000.0
    elif 0 < s <= 20:
        s *= 1000.0
    return int(round(s))

# Octagon-Erreichbarkeit: wird zentral in http_get() mitgefuehrt (JEDER HTTP-Request an die
# Box laeuft dort durch), damit nicht bei jedem einzelnen fehlgeschlagenen Aufruf (powerstate,
# zap, tunersignal, ...) eine eigene Fehlerzeile im Log/GUI-Statuslabel auftaucht - stattdessen
# genau eine klare Meldung beim Uebergang "erreichbar" -> "nicht erreichbar" und zurueck.
_octagon_fail_count = 0
_octagon_reachable = True
_OCTAGON_FAIL_THRESHOLD = 2  # ab der 2. aufeinanderfolgenden fehlgeschlagenen Anfrage in Folge

def _octagon_mark_unreachable(e):
    global _octagon_fail_count, _octagon_reachable
    _octagon_fail_count += 1
    if _octagon_reachable and _octagon_fail_count >= _OCTAGON_FAIL_THRESHOLD:
        _octagon_reachable = False
        olog("WARNUNG: Octagon-Box nicht erreichbar (%s) - bitte Box pruefen/neu starten." % e)

def _octagon_mark_reachable():
    global _octagon_fail_count, _octagon_reachable
    _octagon_fail_count = 0
    if not _octagon_reachable:
        _octagon_reachable = True
        olog("Octagon-Box wieder erreichbar.")

def http_get(url, timeout=5):
    # Zentrale Stelle fuer JEDEN HTTP-Request an die Octagon-Box (powerstate, zap,
    # tunersignal, getallservices, statusinfo, remotecontrol, ...) - dadurch reicht diese
    # eine dlog()-Zeile, um bei -debug ausnahmslos jeden gesendeten Befehl (URL + Timeout)
    # auf der Konsole zu sehen, ohne jeden einzelnen Aufrufer separat anzupassen. Erfasst
    # ausserdem zentral die Erreichbarkeit der Box (siehe _octagon_mark_*oben).
    dlog("HTTP GET (Timeout %ss): %s" % (timeout, url))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        _octagon_mark_unreachable(e)
        raise
    _octagon_mark_reachable()
    return data

def http_get_json(url, timeout=5):
    return json.loads(http_get(url, timeout))

CH_RE = re.compile(r"(\d{1,3})\s*-\s*(\d+|beacon)\s*@\s*(\d+)", re.I)
STATIC_NAMES = [
    "01-250@496", "02-250@497", "03-250@498", "04-250@499", "05-250@500",
    "06-333@496", "07-333@497", "08-333@498", "09-333@499", "10-333@500",
    "11-500@494", "12-500@495", "13-500@496", "14-500@497", "15-500@498", "16-500@499",
    "17-1000@494", "18-1000@495", "19-1000@496", "20-1000@497",
    "21-2000@496", "22-Beacon@492",
]
BEACON_FREQ = 491.4
BEACON_TOL = 0.7
BEACON_SR = 1500

def _walk(obj, out):
    if isinstance(obj, dict):
        name = next((obj[k] for k in ("servicename", "sname", "name") if isinstance(obj.get(k), str)), None)
        ref = next((obj[k] for k in ("servicereference", "sref", "serviceref", "ref") if isinstance(obj.get(k), str)), None)
        if name and ref:
            out.append((name, ref))
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)

def _to_channels(pairs):
    chans, seen = [], set()
    for name, sref in pairs:
        m = CH_RE.search(name)
        if not m or sref.startswith("1:7:") or name in seen:
            continue
        seen.add(name)
        beacon = m.group(2).lower() == "beacon"
        chans.append({
            "num": int(m.group(1)),
            "sr": 0 if beacon else int(m.group(2)),
            "freq": int(m.group(3)),
            "fx": BEACON_FREQ if beacon else float(m.group(3)),
            "beacon": beacon,
            "name": name.strip(),
            "sref": sref,
        })
    return sorted(chans, key=lambda c: c["num"])

def load_channels(box):
    base = "http://%s" % box
    olog("    Lese Kanalliste von %s ..." % box)
    pairs = []
    try:
        _walk(http_get_json(base + "/api/getallservices"), pairs)
    except Exception as e:
        olog("    getallservices nicht nutzbar: %s" % e)
    chans = _to_channels(pairs)
    if not chans:
        try:
            bouquets = http_get_json(base + "/api/bouquets").get("bouquets", [])
            for b in bouquets:
                if isinstance(b, list) and len(b) >= 2:
                    data = http_get_json(base + "/api/epgnow?bRef=" + urllib.parse.quote(b[0], safe=""))
                    _walk(data.get("events", []), pairs)
            chans = _to_channels(pairs)
        except Exception as e:
            olog("    bouquets/epgnow nicht nutzbar: %s" % e)
    if chans:
        olog("    %d Kanaele der Form NN-SR@MHz auf der Box gefunden." % len(chans))
        return chans
    olog("    WARNUNG: Keine passenden Kanaele von der Box erhalten - nutze die eingebaute Liste (ohne sRef).")
    return _to_channels([(n, "") for n in STATIC_NAMES])

CHANNEL_MAX_DEV = 0.5
CHANNEL_MATCH = "nearest"

def choose_channel(channels, x_mhz, sr_ksps):
    for b in (c for c in channels if c["beacon"]):
        d = abs(b["fx"] - x_mhz)
        if d <= BEACON_TOL + 1e-9 and sr_ksps in (BEACON_SR, 0):
            return b, "Bake passt (Frequenz und Symbolrate)"
    normal = [c for c in channels if not c["beacon"]]
    if not normal:
        return None, "keine Kanaele vorhanden"
    srs = sorted({c["sr"] for c in normal})
    sr_sel = min(srs, key=lambda s: (abs(s - sr_ksps), s))
    _odbg("Kanalwahl: Signal %.3f MHz / %s kS/s; auf der Box vorhandene Symbolraten: %s -> gewaehlt: %s kS/s%s" % (
        x_mhz, sr_ksps, srs, sr_sel,
        "" if sr_sel == sr_ksps else "  *** ABWEICHUNG: kein Kanal mit %s kS/s auf der Box, naechste Rate gewaehlt - Lock unwahrscheinlich ***" % sr_ksps))
    cands = [c for c in normal if c["sr"] == sr_sel]
    freqs = sorted(c["freq"] for c in cands)
    best = min(cands, key=lambda c: (abs(c["fx"] - x_mhz), c["num"]))
    d = abs(best["fx"] - x_mhz)

    # Nachbarkanaele (naechster mit fx <= x_mhz bzw. fx >= x_mhz) fuer die Terminal-Ausgabe,
    # damit bei Kanaelen dicht an der Mitte zwischen zwei Nummern (z.B. 495,5 MHz zwischen
    # Kanal 12@495 und 13@496) sofort ersichtlich ist, wieso genau dieser Kanal gewaehlt wurde.
    lower = max((c for c in cands if c["fx"] <= x_mhz), key=lambda c: c["fx"], default=None)
    upper = min((c for c in cands if c["fx"] >= x_mhz), key=lambda c: c["fx"], default=None)

    def _neighbor_txt():
        parts = []
        if lower is not None:
            parts.append("unten Kanal %s@%s (Abstand %s MHz)" % (lower["num"], fm(lower["fx"]), fm(abs(x_mhz - lower["fx"]))))
        if upper is not None and (lower is None or upper["num"] != lower["num"]):
            parts.append("oben Kanal %s@%s (Abstand %s MHz)" % (upper["num"], fm(upper["fx"]), fm(abs(upper["fx"] - x_mhz))))
        return " / ".join(parts)

    if d > CHANNEL_MAX_DEV + 1e-9:
        return None, ("Frequenz @%s liegt %s MHz neben dem naechsten Kanal @%s (vorhanden bei %s kS/s: %s)"
                       % (fm(x_mhz), fm(d), best["freq"], sr_sel, freqs))

    olog("Frequenz angefordert %s MHz - schalte auf Kanal %s wegen Abstand zum oberen und unteren Kanal (%s)"
         % (fm(x_mhz), best["num"], _neighbor_txt()))

    return best, "Frequenz/Symbolrate passen zu Kanal %s (%s)" % (best["num"], best["name"])

DIGIT_KEYS = {"1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11}
KEY_OK = 352

def zap_sref(box, sref):
    url = "http://%s/api/zap?sRef=%s" % (box, urllib.parse.quote(sref, safe=""))
    dlog("Zap-URL: %s" % url)
    res = http_get(url)
    olog("    Zap-Antwort: %s" % res.strip())
    _odbg("Zap: %s -> Antwort: %s" % (url, res.strip()[:200]))
    return res

def zap_number(box, num):
    olog("    Kein sRef bekannt - sende Kanalnummer %s als Tasten" % num)
    for d in str(num):
        http_get("http://%s/api/remotecontrol?command=%s" % (box, DIGIT_KEYS[d]))
        time.sleep(0.3)
    http_get("http://%s/api/remotecontrol?command=%s" % (box, KEY_OK))

def _ref_core(ref):
    return ":".join((ref or "").strip().split(":")[:10]).upper()

def current_service(box):
    err = []
    for ep in ("statusinfo", "getcurrent"):
        try:
            d = http_get_json("http://%s/api/%s" % (box, ep))
        except Exception as e:
            err.append("%s: %s" % (ep, e))
            continue
        if not isinstance(d, dict):
            continue
        info = d.get("info") if isinstance(d.get("info"), dict) else {}
        ref = d.get("currservice_serviceref") or info.get("ref") or info.get("serviceref")
        name = d.get("currservice_name") or info.get("name") or info.get("servicename")
        if ref:
            return ref, name or "?", ep
        err.append("%s: keine Service-Angabe" % ep)
    return None, None, "; ".join(err)

def log_power(box):
    try:
        d = http_get_json("http://%s/api/powerstate" % box)
        standby = d.get("instandby")
        if standby:
            olog("    WARNUNG: Box ist im Standby - Umschalten kann wirkungslos bleiben.")
        return bool(standby)
    except Exception as e:
        dlog("Powerstate nicht lesbar: %s" % e)  # kein eigenes olog() mehr noetig -
                                                     # http_get() meldet "nicht erreichbar" zentral
        return None

def verify_zap(box, ch, tries=4, wait=1.0):
    want = _ref_core(ch["sref"])
    got_ref = got_name = None
    for i in range(1, tries + 1):
        time.sleep(wait)
        got_ref, got_name, src = current_service(box)
        dlog("verify_zap Versuch %d/%d: soll=%s ist=%s (%s), Quelle=%s" %
             (i, tries, want, got_ref, got_name, src))
        if got_ref is None:
            continue
        if _ref_core(got_ref) == want:
            olog("Umgeschaltet: Box steht auf Kanal %s (%s)" % (ch["num"], ch["name"]))
            return True
    olog("FEHLER: Box steht NICHT auf Kanal %s (%s) - Soll %s, Ist %s (%s)" % (ch["num"], ch["name"], ch["sref"], got_ref, got_name))
    return False

_vlc_proc = None

def stream_url(box, sref, port):
    host = box.split(":")[0]
    return "http://%s:%s/%s" % (host, port, sref)

def find_vlc_pids(args, sref):
    if not sref:
        return []
    core = ":".join(sref.strip().split(":")[:10]).lower()
    needle = ("%s:%s/%s" % (args.box.split(":")[0], args.octagon_stream_port, core)).lower()
    try:
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        cmd = cmd.strip()
        exe = [os.path.basename(t).lower() for t in cmd.split()[:2]]
        if any(e.startswith("vlc") for e in exe) and needle in cmd.lower():
            pids.append(int(pid))
    return pids

def stop_vlc():
    global _vlc_proc
    if _vlc_proc is not None and _vlc_proc.poll() is None:
        olog("    Beende vorheriges VLC (PID %s) ..." % _vlc_proc.pid)
        _vlc_proc.terminate()
        try:
            _vlc_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _vlc_proc.kill()
    _vlc_proc = None

def vlc_command(vlc, url, args):
    cmd = [vlc, "--extraintf=telnet,http", "--network-caching=1000", url,
           "--http-host=0.0.0.0", "--http-port=%s" % args.vlc_http_port,
           "--telnet-host=127.0.0.1", "--telnet-port=%s" % args.vlc_telnet_port, "--telnet-password=admin",
           "--avcodec-hw=none", "--playlist-enqueue",
           "--autoscale", "--no-qt-video-autoresize", "--vout=gl,xcb_xv,any",   # skalierende Videoausgabe: Bild fensterfuellend (xcb_x11 kann nicht skalieren)
           "--snapshot-path=%s" % app_config.get("vlc_snapshot_path", os.path.expanduser("~/Pictures"))]
    if not args.no_marquee:
        cmd += ["--sub-source=marq", "--marq-file=%s" % args.vlc_marquee_file, "--marq-position=4",
                "--marq-opacity=200", "--marq-size=20", "--marq-color=0xFFFFFF", "--marq-refresh=1000"]
    cmd += ["--http-reconnect"]
    return cmd

def marquee_text(ch, t, mode=None):
    if t is None:
        mer = "MER: keine Daten"
    elif mode == "1":
        mer = "MER %.1f dB" % t["snr_db"]
    else:
        mer = "MER %.1f dB (%g %%)" % (t["snr_db"], t["snr"])
    if mode == "mer":
        return mer
    parts = []
    if ch.get("sig_freq"):
        parts.append("%s MHz" % fm(ch["sig_freq"]))
    if ch.get("sig_sr"):
        parts.append("%g kS/s" % ch["sig_sr"])
    parts.append(mer)
    return " | ".join(parts)

def write_marquee(args, ch, t):
    path = args.vlc_marquee_file
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(marquee_text(ch, t, getattr(args, "status", None)))
        os.replace(tmp, path)
    except OSError as e:
        olog("WARNUNG: Marquee-Datei %s nicht schreibbar: %s" % (path, e))
        return False
    return True

def marquee_loop(args, ch, gen):
    while gen == _switch_gen:
        t0 = time.time()
        t, _err = tuner_signal(args.box)
        if gen != _switch_gen:
            return
        if not write_marquee(args, ch, t):
            return
        time.sleep(max(0.05, 1.0 - (time.time() - t0)))

def start_vlc(args, ch):
    global _vlc_proc
    url = stream_url(args.box, ch["sref"], args.octagon_stream_port)
    vlc = shutil.which("vlc")
    if not vlc:
        olog("FEHLER: VLC nicht gefunden (sudo apt install vlc). Stream-URL: %s" % url)
        return None
    cmd = vlc_command(vlc, url, args)
    stop_vlc()
    olog("Starte VLC fuer Kanal %s (%s)" % (ch["num"], ch["name"]))
    dlog("VLC-Kommandozeile: %s" % " ".join(cmd))
    try:
        _vlc_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    except OSError as e:
        olog("FEHLER: VLC konnte nicht gestartet werden: %s (URL: %s)" % (e, url))
        return None
    time.sleep(0.7)
    if _vlc_proc.poll() is None:
        olog("VLC laeuft (PID %s) - Stream: %s" % (_vlc_proc.pid, url))
    else:
        olog("FEHLER: VLC hat sich sofort beendet. Zum Testen von Hand: %s" % " ".join(cmd))
    return _vlc_proc

_switch_gen = 0
_wait_thread = None
_vlc_first_start = threading.Event()

def cancel_vlc_wait():
    global _switch_gen
    _switch_gen += 1

_tuner_sig_cache = {"t": 0.0, "val": None}

def tuner_signal(box):
    # Nicht erreichbare Box: hoechstens alle 15 s einen neuen Versuch (jeder Versuch kostet bis
    # zum Timeout), dazwischen das zuletzt gelieferte Ergebnis zurueckgeben.
    if not _octagon_reachable and time.time() - _tuner_sig_cache["t"] < 15.0 and _tuner_sig_cache["val"] is not None:
        return _tuner_sig_cache["val"]
    _res = _tuner_signal_raw(box)
    _tuner_sig_cache["t"] = time.time()
    _tuner_sig_cache["val"] = _res
    return _res

def _tuner_signal_raw(box):
    try:
        d = http_get_json("http://%s/api/tunersignal" % box, timeout=3)
        dlog("tunersignal roh: %s" % d)
    except Exception as e:
        return None, str(e)
    def num(v):
        try:
            return float(str(v).replace("%", "").replace("dB", "").strip())
        except (TypeError, ValueError):
            return 0.0
    # Lock-Status: je nach OpenWebif-Version unter "lock", "tunerlocked" oder "haslock" -
    # alle gaengigen Varianten abdecken, True wenn irgendeine davon vorhanden und wahr ist.
    lock = None
    for key in ("lock", "tunerlocked", "haslock"):
        if key in d:
            lock = bool(d.get(key))
            break
    return {"snr": num(d.get("snr")), "snr_db": num(d.get("snr_db")), "agc": num(d.get("agc")),
            "ber": d.get("ber"), "lock": lock}, None

def _sleep_gen(seconds, gen):
    end = time.time() + seconds
    while time.time() < end:
        if gen != _switch_gen:
            return False
        time.sleep(0.1)
    return gen == _switch_gen

def supervise_vlc(args, ch, gen):
    fast_fails = 0
    first = True
    while gen == _switch_gen:
        delay = args.vlc_delay if first else args.vlc_restart_delay
        if not _sleep_gen(delay, gen):
            return
        t0 = time.time()
        pids = find_vlc_pids(args, ch["sref"])
        if pids:
            set_status("VLC laeuft schon (PID %s), kein neuer Start" % pids[0], gen)
            if first:
                first = False
                _vlc_first_start.set()
            while gen == _switch_gen and find_vlc_pids(args, ch["sref"]):
                if not _sleep_gen(1.5, gen):
                    return
        else:
            proc = start_vlc(args, ch)
            if first:
                first = False
                _vlc_first_start.set()
            if proc is None:
                return
            if proc.poll() is None:
                set_status("VLC laeuft", gen)
            while gen == _switch_gen and proc.poll() is None:
                time.sleep(0.5)
        if gen != _switch_gen:
            return
        dur = time.time() - t0
        if args.no_vlc_restart:
            set_status("VLC beendet (kein Neustart)", gen)
            return
        set_status("VLC beendet, Neustart in %g s" % args.vlc_restart_delay, gen)
        fast_fails = fast_fails + 1 if dur < 3 else 0
        if fast_fails >= 5:
            olog("VLC beendet sich immer wieder sofort - Neustart-Versuche eingestellt.")
            return

def wait_signal_then_vlc(args, ch, gen):
    try:
        _wait_signal_then_vlc(args, ch, gen)
    finally:
        _vlc_first_start.set()

def _wait_signal_then_vlc(args, ch, gen):
    timeout = args.signal_timeout
    olog("Warte auf Signal (max. %g s) ..." % timeout)
    t0 = time.time()
    while True:
        if gen != _switch_gen:
            return
        t, err = tuner_signal(args.box)
        dlog("Signal-Polling (%.1f s): t=%s err=%s" % (time.time() - t0, t, err))
        if t is not None and (t["snr"] > 0 or t["snr_db"] > 0):
            set_status("Signal OK (MER %.1f dB), VLC startet" % t["snr_db"], gen)
            dlog("Signal-Details beim Start: %s" % t)
            if gen == _switch_gen:
                supervise_vlc(args, ch, gen)
            return
        if time.time() - t0 >= timeout:
            set_status("Kein Signal nach %g s - VLC nicht gestartet" % timeout, gen)
            return
        time.sleep(1.0)

def schedule_vlc(args, ch, gen):
    global _wait_thread
    _vlc_first_start.clear()
    if not args.no_marquee:
        write_marquee(args, ch, None)
        threading.Thread(target=marquee_loop, args=(args, ch, gen), daemon=True).start()
    if find_vlc_pids(args, ch["sref"]):
        target = lambda: (supervise_vlc(args, ch, gen), _vlc_first_start.set())
    elif args.signal_timeout <= 0:
        target = lambda: (supervise_vlc(args, ch, gen), _vlc_first_start.set())
    else:
        target = lambda: wait_signal_then_vlc(args, ch, gen)
    _wait_thread = threading.Thread(target=target, daemon=True)
    _wait_thread.start()

def _odbg_tuner(box):
    """Kurzer Tuner-Status der Box als Text fuer das Debug-Log (Lock, MER, Frequenz)."""
    try:
        t, err = tuner_signal(box)
    except Exception as e:
        return "Abfrage-Fehler %r" % (e,)
    if t is None:
        return "keine Antwort (%s)" % err
    return "lock=%s snr=%.1f dB agc=%s ber=%s freq=%s" % (t.get("lock"), t.get("snr_db") or 0.0,
                                                          t.get("agc"), t.get("ber"), t.get("freq"))

def _odbg_followup(box, ch, freq_mhz, sr):
    """Im Hintergrund: 3, 8 und 15 s nach dem Zap den Tuner-Status ins Debug-Log schreiben -
    zeigt, ob und wann die Box auf dem Signal einrastet."""
    def work():
        t0 = time.time()
        for wait in (3.0, 8.0, 15.0):
            time.sleep(max(0.0, wait - (time.time() - t0)))
            _odbg("Status %2.0f s nach Zap (Kanal %s, soll %.3f MHz / %s kS/s): %s" % (
                time.time() - t0, ch.get("num"), freq_mhz, sr, _odbg_tuner(box)))
    threading.Thread(target=work, daemon=True).start()

def process_signal(sig, channels, args):
    """Waehlt anhand Frequenz/Symbolrate den passenden Octagon-Kanal, schaltet um und startet VLC."""
    freq_mhz = normalize_freq_mhz(sig["freq"])
    sr = normalize_sr_ksps(sig["sr"])
    x = freq_to_channel_mhz(freq_mhz)
    olog("BATC-Signal: %s MHz / %s kS/s -> Kanalsuche @%s" % (freq_mhz, sig["sr"], fm(x)))
    _odbg("=== Umschalten Octagon: Signal %s MHz, SR roh %s -> normiert %s kS/s, Kanalfrequenz %s, Kanalliste: %d Eintraege, direkt=%s" % (
        freq_mhz, sig["sr"], sr, fm(x), len(channels or []), _oct_direct()))

    if not channels:
        olog("Octagon-Kanalliste noch nicht geladen - bitte kurz warten und erneut klicken.")
        _octagon_fehler_log(freq_mhz, sr, "Kanalliste noch nicht geladen")
        return
    ch, why = choose_channel(channels, x, sr)
    if not ch:
        olog("Kein passender Octagon-Kanal: %s" % why)
        _octagon_note_set("State: Kein Kanal")
        _octagon_fehler_log(freq_mhz, sr, "Kein passender Kanal: %s" % why)
        return
    olog("Kanal %s (%s) gewaehlt: %s" % (ch["num"], ch["name"], why))
    _odbg("Kanal gewaehlt: Nr %s, Name '%s', Kanal-SR %s, Kanal-Freq %s, sRef %s | Grund: %s" % (
        ch["num"], ch["name"], ch.get("sr"), ch.get("freq"), ch.get("sref") or "-", why))
    dlog("Kanal-Details: sref=%s num=%s name=%s" % (ch.get("sref"), ch.get("num"), ch.get("name")))

    if args.dry_run:
        olog("--dry-run: wuerde Octagon %s auf Kanal %s schalten (sRef=%s)" % (args.box, ch["num"], ch["sref"] or "unbekannt"))
        return

    ch = dict(ch, sig_freq=freq_mhz, sig_sr=sr)
    if ch["sref"]:
        _octagon_last_stream["url"] = stream_url(args.box, ch["sref"], args.octagon_stream_port)
        olog("    Octagon-Stream-URL: %s" % _octagon_last_stream["url"])
    else:
        olog("    Octagon: kein sRef fuer Kanal %s bekannt - keine Stream-URL." % ch["num"])
    cancel_vlc_wait()
    gen = _switch_gen
    existing_vlc = find_vlc_pids(args, ch["sref"]) if not args.no_vlc else []
    if not args.no_vlc and not (_vlc_proc is not None and _vlc_proc.poll() is None and _vlc_proc.pid in existing_vlc):
        stop_vlc()
    standby = log_power(args.box)
    before = current_service(args.box)
    already = bool(ch["sref"] and before[0] and _ref_core(before[0]) == _ref_core(ch["sref"]) and standby is not True)
    dlog("Vor dem Umschalten: standby=%s aktueller Service=%s bereits_auf_Kanal=%s existing_vlc_pids=%s" %
         (standby, before, already, existing_vlc))
    _odbg("Vor dem Umschalten: standby=%s, aktueller Service=%s, bereits auf Kanal=%s, VLC-PIDs=%s, Tuner-Status=%s" % (
        standby, before, already, existing_vlc, _odbg_tuner(args.box)))
    try:
        if already:
            switched("sig", freq_mhz, sr, ch["name"], "Receiver stand schon auf dem Kanal")
        elif ch["sref"]:
            zap_sref(args.box, ch["sref"])
            _zok = verify_zap(args.box, ch)
            _odbg("Nach Zap: Pruefung %s; Tuner-Status sofort: %s" % ("OK" if _zok else "FEHLGESCHLAGEN", _odbg_tuner(args.box)))
            _odbg_followup(args.box, ch, freq_mhz, sr)
            if _zok:
                switched("sig", freq_mhz, sr, ch["name"], "Umgeschaltet, warte auf Signal" if not args.no_vlc else "Umgeschaltet")
        else:
            zap_number(args.box, ch["num"])
            switched("sig", freq_mhz, sr, ch["name"], "Umgeschaltet (nicht geprueft)")
    except (urllib.error.URLError, OSError) as e:
        olog("FEHLER beim Umschalten: %s" % e)
        _octagon_fehler_log(freq_mhz, sr, "Fehler beim Umschalten auf Kanal %s: %s" % (ch["num"], e))

    if args.no_vlc:
        pass
    elif not ch["sref"]:
        olog("Kein VLC: ohne sRef gibt es keine Stream-URL.")
    else:
        schedule_vlc(args, ch, gen)

_ws_err_win = {}     # label -> offenes Hinweisfenster "Web-Steuerung nicht erreichbar"
_ws_err_shown = set()
_ws_err_logged = set()     # label -> Sendefehler schon einmal in der Statuszeile gemeldet

def _ws_error_popup(label, url, freq, err):
    """Nicht-modales Hinweisfenster, einmal je Tuner, bis wieder erfolgreich gesendet wurde."""
    w = _ws_err_win.get(label)
    try:
        if w is not None and w.winfo_exists():
            w._msg.configure(text=w._fmt(freq, err))      # nur aktualisieren, kein neues Fenster
            return
    except tk.TclError:
        pass
    if label in _ws_err_shown:
        return                       # schon gezeigt und weggeklickt - erst nach Erfolg wieder
    _ws_err_shown.add(label)
    w = tk.Toplevel(root)
    w.title("%s: Web-Steuerung nicht erreichbar" % label)
    w.attributes("-topmost", True)
    hint = ""
    if label == "LongMynd 2":
        try:
            e = _lm2_entry() or {}
            if (e.get("modus") or "lokal") == "lokal":
                hint = ("\n\nModus 'Lokal': longmynd muss mit Web-Steuerung laufen, z.B.\n"
                        "  %s/%s\n  (./longmynd -W %s ...)\n"
                        "Oder unter Konfig > LongMynd 2 auf 'Extern' stellen." %
                        (e.get("lokal_dir") or "", e.get("lokal_script") or "start_longmynd2.sh", e.get("lokal_port") or 8080))
            else:
                hint = "\n\nModus 'Extern': laeuft longmynd auf %s:%s mit -W %s?" % (
                    e.get("extern_ip"), e.get("extern_port"), e.get("extern_port"))
        except Exception:
            pass
    w._fmt = lambda f, ex: ("%s: Frequenz %s MHz NICHT eingestellt.\n\nWeb-Steuerung %s antwortet nicht:\n%s%s"
                            % (label, fm(f), url, ex, hint))
    w._msg = tk.Label(w, text=w._fmt(freq, err), justify="left", anchor="w", padx=16, pady=12, fg="#a00000")
    w._msg.pack(fill="both", expand=True)
    tk.Label(w, text="(Dieses Fenster erscheint nur einmal - bis wieder erfolgreich gesendet wurde.)",
             font=('', 8), fg="#555555").pack(padx=16)
    ttk.Button(w, text="OK", command=w.destroy).pack(pady=(6, 12))
    _ws_err_win[label] = w

def _ws_error_clear(label):
    _ws_err_shown.discard(label)
    w = _ws_err_win.pop(label, None)
    try:
        if w is not None and w.winfo_exists():
            w.destroy()
    except tk.TclError:
        pass

def send_to_longmynd(freq, sr, args, lo_khz, url=None, label="LongMynd"):
    """Sendet C<ZF_kHz>,<SR> per WebSocket an den LongMynd-Receiver. lo_khz kommt aus dem
    LNB-LO-Feld der GUI, damit beide Wege (rtl_sdr und LongMynd) dieselbe LO nutzen. url/label
    nur fuer einen weiteren LongMynd (LongMynd 2) - Default ist der erste LongMynd."""
    url = url or args.longmynd_url
    zf = int(freq * 1000) - lo_khz
    olog("%s: ZF=%s kHz (Freq %s MHz - LO %s kHz), Symbolrate=%s" % (label, zf, fm(freq), lo_khz, sr))
    dlog("%s-Ziel: %s (Subprotokoll 'control', Timeout 2s)" % (label, url))
    if args.dry_run:
        olog("--dry-run: wuerde an %s senden: C%s,%s" % (url, zf, sr))
        return
    try:
        from websocket import create_connection
        ws = create_connection(url, subprotocols=["control"], timeout=2)
        ws.send("C%s,%s" % (zf, sr))
        ws.close()
        switched("sig", freq, sr, None, "An %s gesendet: C%s,%s" % (label, zf, sr))
        root.after(0, _ws_error_clear, label)
        if label in _ws_err_logged:
            _ws_err_logged.discard(label)
            olog("%s: Web-Steuerung wieder erreichbar - Frequenz %s MHz eingestellt." % (label, fm(freq)))
    except Exception as e:
        root.after(0, _ws_error_popup, label, url, freq, e)
        (dlog if label in _ws_err_logged else olog)(
            "FEHLER beim Senden an %s %s: %s - Frequenz %s MHz NICHT eingestellt (Web-Steuerung von %s laeuft nicht?)%s"
            % (label, url, e, fm(freq), label, "" if label in _ws_err_logged else
               " - weitere Fehler nur noch im Debug-Log, bis es wieder geht"))
        _ws_err_logged.add(label)
        dlog("%s-Fehlerdetail: %r" % (label, e))
        # Ziel-Frequenz wieder vergessen: sonst meldet der naechste Versuch "steht schon auf ... - kein Umschalten",
        # obwohl der Tuner nie umgeschaltet hat
        _tgt = {"LongMynd": "longmynd", "LongMynd 2": "longmynd2"}.get(label)
        if _tgt and abs((_target_cur_freq.get(_tgt) or 0) - freq) < 0.001:
            _target_cur_freq[_tgt] = None

_octagon_channels = []
_octagon_channels_loaded = False
_octagon_last_stream = {"url": None}   # zuletzt gezappte Octagon-Stream-URL, fuer die Aufnahme

#===== Aufnahme ("Save Video") je Ziel: Octagon (ffmpeg vom Stream), LongMynd (ffmpeg vom =====
#===== Multicast), rtl_sdr (tee in der lokalen Pipeline, siehe on_start())              =====
RECORDINGS_DIR = os.path.join(parameters_path, "recordings")
try:
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
except OSError:
    pass

_rec_active = {"octagon": False, "longmynd": False, "rtl_sdr": False, "longmynd_p4": False, "longmynd2": False}   # Aufnahme gerade aktiv?
_rec_proc   = {"octagon": None,  "longmynd": None,  "longmynd_p4": None, "longmynd2": None}   # ffmpeg-Prozess (nur octagon/longmynd/longmynd_p4)
_rec_file   = {"octagon": None,  "longmynd": None,  "rtl_sdr": None,  "longmynd_p4": None, "longmynd2": None}   # zuletzt verwendete Datei
_rec_buttons = {}                                                       # target -> Button (fuer Text/Farbe)
for _ot in _UDPRX_TARGETS:
    _rec_active[_ot] = False
    _rec_proc[_ot] = None
    _rec_file[_ot] = None

def _rec_filename(target):
    return os.path.join(RECORDINGS_DIR, "%s_%s.ts" % (target, time.strftime("%Y%m%d_%H%M%S")))

def _rec_set_button(target):
    btn = _rec_buttons.get(target)
    if btn is None:
        return
    if _rec_active[target]:
        btn.configure(text="\u25a0 Aufnahme laeuft", style="RecOn.TButton")
    else:
        btn.configure(text="Save Video", style="TButton")

def rec_toggle(target):
    """Knopf 'Save Video' je Ziel: an/aus, Datei octagon_<Datum>.ts / longmynd_<Datum>.ts /
    rtl_sdr_<Datum>.ts in ~/.leandvb-GUI/recordings/. Bei erneutem Klick wird gestoppt."""
    if _rec_active[target]:
        if target in ("octagon",) + _LM_TARGETS + _UDPRX_TARGETS:
            proc = _rec_proc.get(target)
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            _rec_proc[target] = None
        _rec_active[target] = False
        olog("Aufnahme gestoppt (%s): %s" % (_TARGET_LABEL[target], _rec_file[target]))
        if target == "rtl_sdr":
            olog("Lokale Kette wird neu gestartet, damit die Aufnahme (tee) entfernt wird ...")
            on_stop()
            root.after(400, on_start)
        _rec_set_button(target)
        return

    fname = _rec_filename(target)
    if target == "octagon":
        url = _octagon_last_stream.get("url")
        if _oct_direct():
            # Direktmodus: Stream kommt als UDP-Multicast von dvb_direct.py (kein Enigma-Zap-URL)
            _dip, _dport = _oct_direct_stream()
            url = "udp://%s:%d?fifo_size=5000000&overrun_nonfatal=1" % (_dip, _dport)
        if not url:
            olog("Keine Octagon-Stream-URL bekannt - erst ein Signal anklicken, dann aufnehmen.")
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            olog("ffmpeg nicht gefunden (sudo apt install ffmpeg).")
            return
        try:
            _rec_proc["octagon"] = subprocess.Popen(
                [ffmpeg, "-y", "-i", url, "-c", "copy", fname],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True)
        except OSError as e:
            olog("FEHLER beim Start von ffmpeg: %s" % e)
            return
        olog("Aufnahme gestartet (Octagon, %s): %s" % (url, fname))
    elif target == "longmynd":
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            olog("ffmpeg nicht gefunden (sudo apt install ffmpeg).")
            return
        url = "udp://@%s:%s" % (cliargs.longmynd_stream_ip, cliargs.longmynd_stream_port)
        try:
            _rec_proc["longmynd"] = subprocess.Popen(
                [ffmpeg, "-y", "-i", url, "-c", "copy", fname],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True)
        except OSError as e:
            olog("FEHLER beim Start von ffmpeg: %s" % e)
            return
        olog("Aufnahme gestartet (LongMynd, %s): %s" % (url, fname))
    elif target == "longmynd_p4":
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            olog("ffmpeg nicht gefunden (sudo apt install ffmpeg).")
            return
        url = "udp://@%s:%s" % (cliargs.longmyndp4_stream_ip, cliargs.longmyndp4_stream_port)
        try:
            _rec_proc["longmynd_p4"] = subprocess.Popen(
                [ffmpeg, "-y", "-i", url, "-c", "copy", fname],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True)
        except OSError as e:
            olog("FEHLER beim Start von ffmpeg: %s" % e)
            return
        olog("Aufnahme gestartet (LongMynd P4, %s): %s" % (url, fname))
    elif target == "longmynd2":
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            olog("ffmpeg nicht gefunden (sudo apt install ffmpeg).")
            return
        url = "udp://@%s:%s" % (cliargs.longmynd2_stream_ip, cliargs.longmynd2_stream_port)
        try:
            _rec_proc["longmynd2"] = subprocess.Popen(
                [ffmpeg, "-y", "-i", url, "-c", "copy", fname],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True)
        except OSError as e:
            olog("FEHLER beim Start von ffmpeg: %s" % e)
            return
        olog("Aufnahme gestartet (LongMynd 2, %s): %s" % (url, fname))
    elif target in _UDPRX_TARGETS:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            olog("ffmpeg nicht gefunden (sudo apt install ffmpeg).")
            return
        url = "udp://@%s:%s" % _rx_udp(target)
        try:
            _rec_proc[target] = subprocess.Popen(
                [ffmpeg, "-y", "-i", url, "-c", "copy", fname],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True)
        except OSError as e:
            olog("FEHLER beim Start von ffmpeg: %s" % e)
            return
        olog("Aufnahme gestartet (%s, %s): %s" % (_TARGET_LABEL.get(target, target), url, fname))
    else:  # rtl_sdr: kein eigener Prozess - stattdessen tee in der laufenden Pipeline (siehe on_start())
        olog("Aufnahme gestartet (rtl_sdr): %s - lokale Kette wird neu gestartet (tee) ..." % fname)

    _rec_file[target] = fname
    _rec_active[target] = True
    _rec_set_button(target)
    if target == "rtl_sdr":
        on_stop()
        root.after(400, on_start)

def octagon_ensure_channels():
    global _octagon_channels, _octagon_channels_loaded
    if _octagon_channels_loaded:
        return
    if _oct_direct():            # Direktmodus: frei abstimmbar, die Box hat kein Enigma - keine Kanalliste lesen (spart Start-Zeit; wird beim Wechsel zum Enigma-Modus nachgeholt)
        return
    _octagon_channels_loaded = True
    def run():
        global _octagon_channels
        try:
            _octagon_channels = load_channels(cliargs.box)
        except Exception as e:
            olog("Octagon-Kanalliste: Fehler (%s)" % e)
    threading.Thread(target=run, daemon=True).start()

#===== Longmynd-Status-Dashboard (auswertung_4002.sh) in eigenem Terminal-Fenster =====

_longmynd_status_proc = None

# Groesse/Position des Longmynd-Status-Terminalfensters. "--geometry" wird von vielen
# Terminal-Emulatoren fuer NEUE Fenster schlicht ignoriert, wenn schon ein Server-Prozess laeuft
# (bekannter gnome-terminal-Bug) - deshalb setzen wir zusaetzlich per xdotool (falls installiert)
# nach dem Start aktiv Groesse+Position, ueber einen eindeutigen Fenstertitel gefunden.
LONGMYND_STATUS_GEOMETRY = "160x46+0-0"
LONGMYND_STATUS_TITLE    = "LongmyndStatus"
LONGMYND_STATUS_WIDTH_PX  = 1500
LONGMYND_STATUS_HEIGHT_PX = 900
LONGMYND_STATUS_X_PX      = 0

_spawned_terminals = []   # [(title, subprocess.Popen), ...] - alle per _spawn_terminal geoeffneten Fenster

def _kill_all_spawned_terminals():
    """Beendet beim Schliessen der GUI alle per _spawn_terminal() geoeffneten Terminal-Fenster
    (aktuell: das Longmynd-Status-Dashboard) samt der darin laufenden Prozesse - sonst bleiben
    verwaiste auswertung_4002.sh/nc/awk-Prozesse und ein offenes Terminal-Fenster zurueck."""
    xdotool = shutil.which("xdotool")
    for title, proc in list(_spawned_terminals):
        # a) das im Terminal laufende Script beenden - dessen eigener trap (EXIT/INT/TERM) raeumt
        #    dann selbststaendig seine Kindprozesse (nc/awk/Polling-Loops) mit auf.
        try:
            subprocess.run(["pkill", "-TERM", "-f", os.path.basename(cliargs.longmynd_status_script)],
                            check=False)
        except Exception as e:
            dlog("pkill fuer Terminal '%s' fehlgeschlagen: %s" % (title, e))
        # b) das Terminal-Fenster selbst schliessen - ueber den beim Start vergebenen eindeutigen
        #    Fenstertitel gefunden (der Terminal-Client-Prozess selbst ist bei manchen Emulatoren,
        #    z.B. gnome-terminal mit Server-Modell, schon lange beendet und daher nicht killbar).
        if title and xdotool:
            try:
                out = subprocess.check_output([xdotool, "search", "--name", title],
                                               stderr=subprocess.DEVNULL).decode().split()
                for win_id in out:
                    subprocess.run([xdotool, "windowkill", win_id], check=False)
            except subprocess.CalledProcessError:
                pass
            except Exception as e:
                dlog("xdotool windowkill fuer '%s' fehlgeschlagen: %s" % (title, e))
        # c) falls der urspruengliche Terminal-Client-Prozess (z.B. bei xterm) doch noch laeuft
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    _spawned_terminals.clear()

def _spawn_terminal(cmdline_str, geometry=LONGMYND_STATUS_GEOMETRY, title=None):
    """Oeffnet ein neues Terminal-Fenster, das cmdline_str per bash -c ausfuehrt. Probiert
    der Reihe nach gaengige Terminal-Emulatoren durch, je nachdem was auf tesla installiert ist.
    geometry (X11-Format "SPALTENxZEILEN+X+Y", Y negativ = von unten) und title (eindeutiger
    Fenstertitel, u.a. fuer die spaetere xdotool-Positionierung) werden - soweit vom jeweiligen
    Terminal-Emulator unterstuetzt - mitgegeben."""
    term = (shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or
            shutil.which("xfce4-terminal") or shutil.which("konsole") or shutil.which("xterm"))
    if not term:
        olog("Kein Terminal-Emulator gefunden - Longmynd-Status-Fenster wird nicht geoeffnet.")
        return None
    base = os.path.basename(term)
    if base == "gnome-terminal":
        argv = [term]
        if geometry:
            argv.append("--geometry=%s" % geometry)
        if title:
            argv.append("--title=%s" % title)
        argv += ["--", "bash", "-c", cmdline_str]
    elif base in ("xfce4-terminal", "konsole"):
        # x-terminal-emulator/xfce4-terminal/konsole erwarten bei "-e" separate argv-Elemente
        # (Kommando + Argumente), NICHT einen zusammengeklebten String - sonst versucht das
        # Terminal ein Programm mit Leerzeichen/Anfuehrungszeichen im Namen zu starten, scheitert
        # lautlos und bleibt mit leerem Fenster stehen. --geometry= wird von beiden verstanden.
        argv = [term]
        if geometry:
            argv.append("--geometry=%s" % geometry)
        if title:
            argv.append("--title=%s" % title)
        argv += ["-e", "bash", "-c", cmdline_str]
    elif base == "xterm":
        # xterm erwartet die Geometrie als eigenes "-geometry VALUE"-Argumentpaar, nicht "--geometry=".
        argv = [term]
        if geometry:
            argv += ["-geometry", geometry]
        if title:
            argv += ["-T", title]
        argv += ["-e", "bash", "-c", cmdline_str]
    else:
        argv = [term, "-e", "bash", "-c", cmdline_str]
    print("[longmynd-status] Terminal-Emulator: %s" % term)
    print("[longmynd-status] argv: %s" % argv)
    try:
        proc = subprocess.Popen(argv)
        print("[longmynd-status] Terminal-Prozess gestartet, PID %s" % proc.pid)
        if title:
            _spawned_terminals.append((title, proc))
            _pin_terminal_window(title, LONGMYND_STATUS_WIDTH_PX, LONGMYND_STATUS_HEIGHT_PX,
                                  LONGMYND_STATUS_X_PX)
        return proc
    except OSError as e:
        olog("Fehler beim Oeffnen des Terminal-Fensters: %s" % e)
        return None

def _pin_terminal_window(title, width_px, height_px, x_px):
    """Setzt Groesse/Position eines Fensters mit gegebenem Titel per xdotool (falls installiert) -
    zuverlaessiger als der --geometry-Schalter der Terminal-Emulatoren selbst, der bei vielen
    (v.a. gnome-terminal mit laufendem Server-Prozess) fuer neue Fenster ignoriert wird."""
    xdotool = shutil.which("xdotool")
    if not xdotool:
        olog("xdotool nicht installiert - Longmynd-Status-Fenster startet in Standardgroesse "
             "(fuer automatische Groesse/Position: sudo apt install xdotool).")
        return
    def worker():
        win_id = None
        for _ in range(20):
            time.sleep(0.3)
            try:
                out = subprocess.check_output([xdotool, "search", "--name", title],
                                               stderr=subprocess.DEVNULL).decode().strip()
            except subprocess.CalledProcessError:
                continue
            ids = [x for x in out.splitlines() if x.strip()]
            if ids:
                win_id = ids[-1]
                break
        if not win_id:
            olog("Longmynd-Status-Fenster ('%s') per xdotool nicht gefunden - "
                 "Groesse/Position wurden nicht gesetzt." % title)
            return
        scr_h = None
        try:
            scr = subprocess.check_output([xdotool, "getdisplaygeometry"]).decode().split()
            scr_h = int(scr[1])
        except Exception:
            pass
        y_px = (scr_h - height_px) if scr_h else 0
        try:
            subprocess.run([xdotool, "windowsize", win_id, str(width_px), str(height_px)], check=False)
            subprocess.run([xdotool, "windowmove", win_id, str(x_px), str(y_px)], check=False)
            dlog("Longmynd-Status-Fenster (xdotool-ID %s) auf %dx%d px @ (%d,%d) gesetzt" %
                 (win_id, width_px, height_px, x_px, y_px))
        except Exception as e:
            olog("xdotool windowsize/windowmove fehlgeschlagen: %s" % e)
    threading.Thread(target=worker, daemon=True).start()

_ANSI_COLOR_FG = {
    30: "#000000", 31: "#e05050", 32: "#50c050", 33: "#c0b040",
    34: "#4da6ff", 35: "#c060c0", 36: "#40b0b0", 37: "#d0d0d0",
    90: "#888888", 91: "#ff6666", 92: "#66ff66", 93: "#ffff66",
    94: "#6699ff", 95: "#ff66ff", 96: "#66ffff", 97: "#ffffff",
}

class _AnsiTextView:
    """Minimaler ANSI-Terminal-Emulator fuer ein tk.Text-Widget: interpretiert genug vom
    ANSI-Vokabular des Longmynd-Status-Dashboards (Clear-Screen + SGR-Farben), um dessen Ausgabe
    lesbar in einem normalen Tkinter-Fenster darzustellen - ohne externen Terminal-Emulator.
    Rendert bei jedem Flush einfach IMMER neu (Widget loeschen, aktuellen Puffer-Inhalt komplett
    neu einfuegen) - das ist die Version, die zuverlaessig funktioniert hat."""
    _ESC_RE   = re.compile(r'\x1b\[([0-9;?]*)([a-zA-Z])|\x1bc')
    _CLEAR_RE = re.compile(r'\x1b\[2J|\x1bc')
    _DEBOUNCE_MS = 150   # Wartezeit nach dem letzten empfangenen Datenhappen, bevor gerendert wird

    def __init__(self, text_widget, tk_root):
        self.w = text_widget
        self.root = tk_root
        self.cur_tag = None
        self._buf = ""
        self._debounce_id = None
        for code, color in _ANSI_COLOR_FG.items():
            self.w.tag_configure("fg%d" % code, foreground=color)
        self.w.tag_configure("bold", font=("DejaVu Sans Mono", 10, "bold"))

    def feed(self, chunk):
        # Nicht sofort in das Widget schreiben: Clear-Code und der zugehoerige Bildschirminhalt
        # kommen oft in getrennten Haeppchen (getrennte os.read()-Aufrufe) an - wuerde man sofort
        # bei jedem Clear loeschen, sieht man kurz ein leeres Fenster aufblitzen, bevor der neue
        # Inhalt nachkommt. Stattdessen sammeln und erst rendern, wenn 150ms lang nichts Neues
        # mehr kam (= ein komplettes Frame ist angekommen).
        self._buf += chunk
        if self._debounce_id is not None:
            try:
                self.root.after_cancel(self._debounce_id)
            except Exception:
                pass
        self._debounce_id = self.root.after(self._DEBOUNCE_MS, self._flush)

    def _flush(self):
        self._debounce_id = None
        buf = self._buf
        self._buf = ""
        if not buf:
            return
        # Nur das letzte vollstaendige Frame anzeigen (alles ab dem letzten Clear-Code) - alles
        # davor gehoert zu einem bereits ueberholten Zyklus.
        idx = None
        for m in self._CLEAR_RE.finditer(buf):
            idx = m.end()
        frame = buf[idx:] if idx is not None else buf
        self._render(frame)

    def _render(self, frame):
        self.w.delete("1.0", "end")
        self.cur_tag = None
        pos = 0
        for m in self._ESC_RE.finditer(frame):
            if m.start() > pos:
                self._insert_plain(frame[pos:m.start()])
            params, letter = m.group(1), m.group(2)
            if letter == 'm':
                self._apply_sgr(params or '')
            # Clear-Codes sind hier schon konsumiert (siehe _flush), andere Sequenzen
            # (Cursor Home/Sichtbarkeit/etc.) werden ignoriert.
            pos = m.end()
        if pos < len(frame):
            self._insert_plain(frame[pos:])

    def _apply_sgr(self, params):
        codes = [int(p) for p in params.split(";") if p.isdigit()] if params else [0]
        for c in codes:
            if c == 0:
                self.cur_tag = None
            elif c == 1:
                self.cur_tag = "bold"
            elif c in _ANSI_COLOR_FG:
                self.cur_tag = "fg%d" % c

    def _insert_plain(self, text):
        if not text:
            return
        tags = (self.cur_tag,) if self.cur_tag else ()
        self.w.insert("end", text, tags)
        self.w.see("end")

def longmynd_status_open_window():
    """Wie longmynd_status_open(), zeigt die Ausgabe von auswertung_4002.sh aber in einem
    normalen, in die GUI eingebetteten Tkinter-Fenster (statt in einem externen Terminal-
    Emulator) - per Pseudo-Terminal (pty), damit Farben/Clear-Screen genauso funktionieren wie
    in einem echten Terminal. Das ist seit -longmyndterminal der Default; mit diesem Flag wird
    stattdessen weiterhin die alte Darstellung (echtes Terminal-Fenster) verwendet."""
    script = cliargs.longmynd_status_script
    print("[longmynd-status] Script-Pfad: %s" % script)
    if not os.path.isfile(script):
        olog("Longmynd-Status-Script nicht gefunden: %s" % script)
        return
    if not os.access(script, os.X_OK):
        olog("Longmynd-Status-Script nicht ausfuehrbar (chmod +x fehlt?): %s" % script)
    lo_khz = int(round(float(lnblo.get()) * 1000))
    port = cliargs.longmynd_status_port
    argv = [script, "-lo", str(lo_khz), "-port", str(port)]
    print("[longmynd-status] argv (eingebettetes Fenster): %s" % argv)

    # Direkt unter dem Hauptfenster oeffnen (gleicher Monitor!) statt an einer festen
    # Bildschirmposition - bei mehreren Monitoren landet "unten am Bildschirmrand" sonst leicht
    # auf einem ganz anderen Monitor als dem, auf dem die GUI tatsaechlich laeuft.
    root.update_idletasks()
    win_x = root.winfo_x()
    win_y = root.winfo_y() + root.winfo_height()
    print("[longmynd-status] Hauptfenster bei (%d,%d), Groesse %dx%d -> Status-Fenster bei (%d,%d)" %
          (root.winfo_x(), root.winfo_y(), root.winfo_width(), root.winfo_height(), win_x, win_y))

    win = tk.Toplevel(root)
    win.title("Longmynd Status (Port %d)" % port)
    win.geometry("%dx%d+%d+%d" % (LONGMYND_STATUS_WIDTH_PX, LONGMYND_STATUS_HEIGHT_PX,
                                   win_x, win_y))
    txt = tk.Text(win, bg="black", fg="#d0d0d0", insertbackground="white",
                   font=("DejaVu Sans Mono", 10), wrap="none")
    txt.pack(fill="both", expand=True)
    view = _AnsiTextView(txt, root)

    try:
        master_fd, slave_fd = pty.openpty()
    except OSError as e:
        olog("Fehler beim Anlegen des Pseudo-Terminals fuer den Longmynd-Status: %s" % e)
        win.destroy()
        return
    # Ein rohes pty setzt (anders als ein echter Terminal-Emulator) weder TERM noch eine
    # Fenstergroesse - ohne TERM bricht "clear" im Script sofort mit "TERM environment variable
    # not set" ab und das Fenster bleibt schwarz. Beides hier explizit nachziehen:
    pty_cols, pty_rows = 200, 60
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", pty_rows, pty_cols, 0, 0))
    except OSError as e:
        dlog("TIOCSWINSZ fehlgeschlagen: %s" % e)
    child_env = dict(os.environ)
    child_env.setdefault("TERM", "xterm-256color")
    try:
        proc = subprocess.Popen(argv, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                                 close_fds=True, env=child_env)
    except OSError as e:
        olog("Fehler beim Starten von auswertung_4002.sh: %s" % e)
        os.close(master_fd)
        os.close(slave_fd)
        win.destroy()
        return
    os.close(slave_fd)
    _spawned_terminals.append((None, proc))   # damit on_exit() diesen Prozess mit beendet

    def reader():
        while True:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            try:
                root.after(0, view.feed, text)
            except RuntimeError:
                break   # GUI schon beendet

    threading.Thread(target=reader, daemon=True).start()

    def on_close():
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

def longmynd_status_open():
    """Startet auswertung_4002.sh (Longmynd-Status-Dashboard) in einem eigenen Terminal-Fenster,
    mit dem konfigurierten UDP-Port und der aktuellen LNB-LO fuer die HF-Frequenz-/Bake-Anzeige."""
    global _longmynd_status_proc
    script = cliargs.longmynd_status_script
    print("[longmynd-status] Script-Pfad: %s" % script)
    if not os.path.isfile(script):
        olog("Longmynd-Status-Script nicht gefunden: %s" % script)
        return
    if not os.access(script, os.X_OK):
        olog("Longmynd-Status-Script nicht ausfuehrbar (chmod +x fehlt?): %s" % script)
    lo_khz = int(round(float(lnblo.get()) * 1000))
    port = cliargs.longmynd_status_port
    print("[longmynd-status] LO = %d kHz, Port = %d" % (lo_khz, port))
    # echo vor/nach dem eigentlichen Aufruf + Exit-Code + Pause am Ende, damit im Terminal-Fenster
    # IMMER etwas zu sehen ist - auch wenn das Script selbst sofort abbricht/keine Ausgabe macht.
    cmd = ('echo "[longmynd-status] starte: %s -lo %d -port %d"; '
           '"%s" -lo %d -port %d; '
           'echo "[longmynd-status] Script beendet, Exit-Code $?"; '
           'exec bash') % (script, lo_khz, port, script, lo_khz, port)
    print("[longmynd-status] Kommandozeile im Terminal: %s" % cmd)
    _longmynd_status_proc = _spawn_terminal(cmd, title=LONGMYND_STATUS_TITLE)

def _vlc_snapshot_path_hint():
    """Ordner, in den VLC Snapshots (Taste/Telnet-Kommando "snapshot") speichert - kommt jetzt
    aus app_config.json (siehe _vlcrc_apply_snapshot_path, das diesen Pfad beim Start auch in
    der vlcrc eingetragen hat), nicht mehr aus einer Vermutung ueber VLCs eigenen Default."""
    return app_config.get("vlc_snapshot_path") or os.path.expanduser("~/Pictures")

def _sanitize_filename_part(s):
    """Ersetzt alles, was in einem Dateinamen stoeren wuerde (Leerzeichen, Slash, Umlaute-
    Sonderzeichen etc.) durch '-', fuer die Snapshot-Dateinamen unten."""
    return re.sub(r'[^A-Za-z0-9.+-]+', '-', str(s)).strip('-')

def _snapshot_name_parts(target):
    """Sammelt, was fuer diesen Ziel-Tuner gerade bekannt ist, fuer den Snapshot-Dateinamen:
    Rufzeichen, Service/Provider, Symbolrate (kS/s), MER, D-Wert, FEC - fehlende Teile werden
    im Dateinamen einfach ausgelassen, nicht als Platzhalter geschrieben."""
    cs_var = _target_callsign_var.get(target)
    name = cs_var.get() if cs_var is not None else ""
    vinfo = _target_video_info.get(target) or {}
    provider = vinfo.get("service_provider") or ""
    sr = _target_cur_sr.get(target)
    mer = None
    dwert = None
    fec_name = None
    if target in _LM_TARGETS:
        mer, dwert, _codec = _lm_extra_fields(target)
        f = _target_cur_freq.get(target)
        if f is not None:
            fec_name = _spectrum_fec_for_freq(f)
    elif target == "octagon":
        try:
            t, _err = tuner_signal(cliargs.box)
            if t is not None:
                mer = t.get("snr_db")
        except Exception:
            pass
    return name, provider, sr, mer, dwert, fec_name

def _snapshot_filename_stub(target):
    """Baut den Dateinamen-Stamm (ohne Endung) fuer einen Snapshot: Rufzeichen ganz vorne,
    dann Service, Symbolrate, MER, D-Wert, FEC, zum Schluss ein Zeitstempel (eindeutig/sortierbar)."""
    name, provider, sr, mer, dwert, fec_name = _snapshot_name_parts(target)
    parts = []
    if name:
        parts.append(_sanitize_filename_part(name))
    if provider:
        parts.append(_sanitize_filename_part(provider))
    if sr:
        try:
            parts.append("%dkS" % int(round(float(sr))))
        except (TypeError, ValueError):
            pass
    if mer is not None:
        parts.append("MER%.1f" % mer)
    if dwert is not None:
        parts.append("D%+.1f" % dwert)
    if fec_name:
        parts.append(_sanitize_filename_part(fec_name))
    parts.append(time.strftime("%Y%m%d-%H%M%S"))
    return "_".join(parts)

def _vlc_snapshot_rename_latest(target, snap_dir, after_ts):
    """Laeuft verzoegert (root.after) nach einem VLC-Telnet-Snapshot: sucht die neueste seit
    after_ts neu entstandene Datei im Snapshot-Ordner (VLC benennt sie selbst "vlcsnap-...") und
    benennt sie in unser Schema um (Rufzeichen_Service_SR_MER_D-Wert_FEC_Zeitstempel), damit das
    Rufzeichen wie gewuenscht ganz vorne im Dateinamen steht."""
    label = _TARGET_LABEL.get(target, target)
    try:
        candidates = [os.path.join(snap_dir, f) for f in os.listdir(snap_dir)]
        candidates = [p for p in candidates if os.path.isfile(p) and os.path.getmtime(p) > after_ts]
        if not candidates:
            dlog("%s: Snapshot-Umbenennung - keine neue Datei in %s gefunden." % (label, snap_dir))
            return
        newest = max(candidates, key=os.path.getmtime)
        ext = os.path.splitext(newest)[1] or ".png"
        new_path = os.path.join(snap_dir, _snapshot_filename_stub(target) + ext)
        if os.path.abspath(newest) == os.path.abspath(new_path):
            return
        n = 2
        while os.path.exists(new_path):
            new_path = os.path.join(snap_dir, "%s_%d%s" % (_snapshot_filename_stub(target), n, ext))
            n += 1
        os.rename(newest, new_path)
        olog("%s: Snapshot gespeichert als: %s" % (label, new_path))
    except OSError as e:
        olog("%s: Snapshot-Umbenennung fehlgeschlagen: %s" % (label, e))

def longmynd_snapshot_telnet(port=None, target=None):
    """Loest per VLC-Telnet-Interface einen Snapshot aus - direkt per nc/Telnet, unabhaengig
    davon ob ein auswertung-Dashboard laeuft. port=None -> LongMynd-Standardport (4212);
    fuer LongMynd P4 wird cliargs.longmyndp4_vnc_port (4213) uebergeben. Ist 'target' bekannt,
    wird die von VLC selbst vergebene Datei kurz danach in unser Rufzeichen-vorne-Schema
    umbenannt (siehe _vlc_snapshot_rename_latest)."""
    host = "127.0.0.1"
    if port is None:
        port = cliargs.longmynd_vnc_port
    passwd = "admin"
    logfile = "/tmp/vlc_telnet_snapshot.log"
    cmd = (
        '{ echo "$(date \'+%%Y-%%m-%%d %%H:%%M:%%S\') - Snapshot ausgeloest"; '
        'sleep 0.3; echo "%s"; sleep 0.3; echo "snapshot"; sleep 0.3; } '
        '| nc -v -q1 "%s" "%s" >> "%s" 2>&1'
    ) % (passwd, host, port, logfile)
    dlog("Snapshot-Telnet-Kommando (Port %d): %s" % (port, cmd))
    try:
        subprocess.Popen(["bash", "-c", cmd])
        olog("Snapshot ausgeloest (VLC-Telnet Port %d) - gespeichert in: %s" %
             (port, _vlc_snapshot_path_hint()))
        if target is not None:
            snap_dir = _vlc_snapshot_path_hint()
            root.after(1500, lambda: _vlc_snapshot_rename_latest(target, snap_dir, time.time() - 2.0))
    except OSError as e:
        olog("Fehler beim Ausloesen des Snapshots (Telnet Port %d): %s" % (port, e))

def _vlc_telnet_send(port, command, label=""):
    """Sendet ein Kommando ueber das VLC-Telnet-Interface (Login mit Passwort 'admin', dann das
    Kommando) - fuer Mute etc., unabhaengig davon ob ein auswertung-Dashboard laeuft."""
    host = "127.0.0.1"
    passwd = "admin"
    logfile = "/tmp/vlc_telnet_cmd.log"
    cmd = (
        '{ echo "$(date \'+%%Y-%%m-%%d %%H:%%M:%%S\') - %s: %s"; '
        'sleep 0.3; echo "%s"; sleep 0.3; echo "%s"; sleep 0.3; } '
        '| nc -v -q1 "%s" "%s" >> "%s" 2>&1'
    ) % (label, command, passwd, command, host, port, logfile)
    dlog("VLC-Telnet-Kommando (Port %d, %s): %s" % (port, command, cmd))
    try:
        subprocess.Popen(["bash", "-c", cmd])
    except OSError as e:
        olog("Fehler beim VLC-Telnet-Kommando (Port %d, %s): %s" % (port, command, e))

def _vlc_controllable_players():
    """Liefert Liste [(Label, Telnet-Port), ...] aller VLC-Player, die per Telnet-Interface
    angesteuert werden koennen - LongMynd/LongMynd P4 haben von Anfang an einen eigenen
    Telnet-Port (4212/4213), und Octagon startet sein VLC ebenfalls schon immer mit
    --extraintf=telnet (Port ueber --vlc-telnet-port, Default 4214) - je nachdem, welche
    Ziele gerade aktiv sind."""
    players = []
    if "longmynd" in _active_targets:
        players.append(("LongMynd", cliargs.longmynd_vnc_port))
    if "longmynd2" in _active_targets:
        players.append(("LongMynd 2", cliargs.longmynd2_vnc_port))
    if "longmynd_p4" in _active_targets:
        players.append(("LongMynd P4", cliargs.longmyndp4_vnc_port))
    if "octagon" in _active_targets and not cliargs.no_vlc:
        players.append(("Octagon", cliargs.vlc_telnet_port))
    return players

_player_sr = {}           # target -> Symbolrate, mit der der Player zuletzt synchronisiert wurde
_resync_hard_until = {}   # target -> Zeitpunkt, bis zu dem ein Resync hart (Stop/Start) sein muss
_callsign_block_until = {}   # target -> Zeitpunkt: Rufzeichen-Abfragen, die vorher begonnen haben, werden verworfen
_lm_last_sr_seen = {}     # target -> zuletzt gemeldete Symbolrate (Status-ID 9) - Datenrate-Wechsel erkennen

def _vlc_resync(target, reason, hard=None):
    """Pausiert den Player dieses Ziels kurz und setzt ihn fort (kein Stop), damit er sich nach einem Wechsel von
    Frequenz/Datenrate/Codec/Rufzeichen (z.B. neues Programm im selben Multiplex, gleicher UDP-Port)
    sauber auf die neuen Stream-Parameter neu synchronisiert - der Player haengt sonst manchmal noch
    am alten Format/Bild. VLC: Telnet 'pause' dann 'play' (auch ein extern/manuell gestarteter, solange
    dessen Telnet-Port erreichbar ist). mpv/ffplay (haben kein Telnet-Interface): der von der GUI
    gestartete Prozess wird beendet und nach kurzer Pause neu gestartet. Ein extern gestarteter
    mpv/ffplay ist nicht steuerbar und bleibt unberuehrt."""
    if _hold["active"]:
        return                                        # Start-Pruefung laeuft: bestehende Player nicht anfassen
    player = _player_active.get(target)
    label = _TARGET_LABEL.get(target, target)
    if hard is None:
        hard = time.time() < _resync_hard_until.get(target, 0.0)
        # Zustandsbasiert statt nur zeitfensterbasiert: weicht die Symbolrate von der ab, mit der der Player
        # zuletzt (hart) synchronisiert wurde, ist IMMER Stop/Start noetig - Pause/Play reicht dann nicht.
        _csr = _target_cur_sr.get(target)
        _psr = _player_sr.get(target)
        if _csr is not None and _psr is not None and int(_psr) != int(_csr):
            hard = True
    _csr = _target_cur_sr.get(target)
    if _csr is not None and (hard or target not in _player_sr):
        _player_sr[target] = _csr
    if player == "vlc":
        port = _player_telnet_port(target)
        if port is None or not _is_port_open("127.0.0.1", port):
            if port is not None and _player_wanted.get(target):
                root.after(0, _player_restart, target, "vlc", "Resync (%s), aber VLC lief nicht" % reason)
            return
        first = "stop" if hard else "pause"
        olog("%s: VLC-Resync (%s) - kurz %s/play%s." % (label, reason, first,
             " (andere Datenrate)" if hard else ""))
        _vlc_telnet_send(port, first, label)
        root.after(700, lambda: _vlc_telnet_send(port, "play", label))
        return
    if player in ("mpv", "ffplay"):
        proc = _player_proc.get(target)
        if proc is None or proc.poll() is not None:
            return
        if hard:
            olog("%s: %s-Resync (%s) - Stop/Start (andere Datenrate)." % (label, player, reason))
            root.after(0, lambda: _stop_player(target, on_done=lambda: root.after(300, _start_player, target, player)))
            return
        olog("%s: %s-Resync (%s) - kurz pause/play (SIGSTOP/SIGCONT)." % (label, player, reason))
        def _cont():
            try:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGCONT)
            except OSError:
                pass
            _player_paused[target] = False
        try:
            proc.send_signal(signal.SIGSTOP)
            _player_paused[target] = True
        except OSError:
            return
        root.after(700, _cont)

#----- Manuelle Player-Knoepfe (VLC/MPV/FFplay) je Spalte, unter "Save Video" -----------------
# Testphase (spaeter automatisiert): startet den gewaehlten Player mit der Stream-Adresse dieses
# Ziels, sofern nicht schon einer laeuft (bei VLC per Telnet-Port-Check erkannt) - Start-Parameter
# fuer VLC 1:1 aus dem vom Nutzer verwendeten watchdog_230_0_0_3.sh uebernommen (--demux=ts,
# udp://@ip:port, Telnet+HTTP+Marquee), auf alle vier Ziele mit je eigenem Telnet/HTTP-Port
# verallgemeinert. Octagon nutzt weiterhin seine eigene http://box:port/sref-Stream-URL.
_player_active = {t: ("vlc" if t != "rtl_sdr" else None) for t in _active_targets}
# Soll der Player laufen? (Player-Ueberwachung startet nur "gewollte" Player neu - ein Klick auf
# den Stop-Knopf nimmt das Ziel aus der Ueberwachung, bis es wieder gestartet wird.) LongMynd-
# Ziele sind von Anfang an gewollt; Octagon hat eine eigene VLC-Verwaltung, rtl_sdr startet erst
# auf Knopfdruck.
_player_wanted = {t: (t in _LM_TARGETS or t in _UDPRX_TARGETS) for t in _active_targets}
_player_started_at = {}     # target -> time.time() des letzten GUI-Starts
_player_last_restart = {}   # target -> time.time() des letzten Neustart-Versuchs der Ueberwachung
_player_udp_miss = {}       # target -> Anzahl aufeinanderfolgender Pruefungen ohne UDP-Socket
_player_proc = {}      # target -> von der GUI gestarteter subprocess.Popen (fuer Stop)
_player_paused = {}    # target -> bool (nur fuer von der GUI gestartete mpv/ffplay-Prozesse)
_player_buttons = {}   # target -> {"vlc": Button, "mpv": Button, "ffplay": Button}

def _player_telnet_port(target):
    return {"octagon": cliargs.vlc_telnet_port,
            "longmynd": cliargs.longmynd_vnc_port,
            "longmynd2": cliargs.longmynd2_vnc_port,
            "longmynd_p4": cliargs.longmyndp4_vnc_port,
            "rtl_sdr": cliargs.rtlsdr_vlc_telnet_port}.get(target, _rx_telnet_port(target) if target in _UDPRX_TARGETS else None)

def _player_http_port(target):
    return {"octagon": cliargs.vlc_http_port,
            "longmynd": cliargs.longmynd_vlc_http_port,
            "longmynd2": cliargs.longmynd2_vlc_http_port,
            "longmynd_p4": cliargs.longmyndp4_vlc_http_port,
            "rtl_sdr": cliargs.rtlsdr_vlc_http_port}.get(target, _rx_http_port(target) if target in _UDPRX_TARGETS else None)

def _mpv_ipc_path(target):
    return "/tmp/mpv_ipc_%s.sock" % target

def _mpv_ipc_send(target, command_list, label=""):
    """Schickt ein JSON-IPC-Kommando an den von der GUI gestarteten mpv dieses Ziels (ueber den
    bei --input-ipc-server angelegten Unix-Socket) - fuer Snapshot per Knopf, analog zum
    VLC-Telnet-Snapshot (longmynd_snapshot_telnet)."""
    ipc_path = _mpv_ipc_path(target)
    if not os.path.exists(ipc_path):
        olog("%s: kein mpv-IPC-Socket gefunden (%s) - laeuft mpv (von der GUI gestartet)?" %
             (label or target, ipc_path))
        return
    try:
        import json as _json
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(ipc_path)
            s.sendall((_json.dumps({"command": command_list}) + "\n").encode("utf-8"))
    except OSError as e:
        olog("Fehler beim mpv-IPC-Kommando (%s, %s): %s" % (label or target, command_list, e))

def mpv_snapshot(target):
    """Loest per mpv-IPC einen Screenshot aus, mit explizitem Dateinamen (Rufzeichen ganz vorne,
    siehe _snapshot_filename_stub) - landet im konfigurierten Ordner (app_config['vlc_snapshot_path'])."""
    label = _TARGET_LABEL.get(target, target)
    snap_dir = app_config.get("vlc_snapshot_path") or os.path.expanduser("~/Pictures")
    try:
        os.makedirs(snap_dir, exist_ok=True)
    except OSError:
        pass
    fname = os.path.join(snap_dir, _snapshot_filename_stub(target) + ".png")
    _mpv_ipc_send(target, ["screenshot-to-file", fname], label)
    olog("%s: mpv-Snapshot gespeichert als: %s" % (label, fname))

def _snapshot_for_target(target, vlc_port):
    """Snapshot-Knopf je Ziel: je nachdem, welcher Player gerade aktiv ist (_player_active) -
    VLC per Telnet (wie bisher, mit nachtraeglicher Umbenennung) oder mpv per IPC-Socket
    (mpv_snapshot, benennt die Datei direkt beim Schreiben passend)."""
    if _player_active.get(target) == "mpv":
        mpv_snapshot(target)
    else:
        longmynd_snapshot_telnet(vlc_port, target=target)

def _player_marquee_file(target):
    if target == "octagon":
        return cliargs.vlc_marquee_file
    return "/tmp/vlc_marquee_%s.txt" % target

#===== Marquee-Einblendung fuer rtl_sdr/LongMynd/LongMynd P4 (Octagon hat mit marquee_loop()/   =====
#===== process_signal() schon seine eigene) - die Datei wird in _start_player() beim VLC-Start   =====
#===== nur LEER angelegt, beschrieben wurde sie bisher nirgends mehr, seit die alte, gemeinsame   =====
#===== Pipeline durch die Pro-Ziel-Spalten ersetzt wurde (daher "kein Status mehr eingeblendet"). =====
#===== Lief VLC fuer dieses Ziel gerade, schreibt _marquee_tick() hier 1x/Sekunde Rufzeichen +    =====
#===== "MER: x.x dB  Dx.x" rein, wie es vorher ueber den alten Octagon-Pfad aussah.               =====
def _write_marquee_file(path, text):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        dlog("Marquee-Datei %s nicht schreibbar: %s" % (path, e))

# Welche Zusatzwerte (neben dem Rufzeichen, das IMMER an erster Stelle steht) in die Marquee-
# Einblendung sollen, ist ueber "Konfig -> Status..." einstellbar (siehe _marquee_settings_open
# weiter unten) - app_config["marquee_fields"] haelt die angehakten Schluessel, die feste
# Reihenfolge hier (_MARQUEE_FIELD_ORDER) bestimmt dann die Reihenfolge in der Einblendung
# (an- bzw. abgehakt wird nur, OB ein Feld erscheint, nicht WO).
_MARQUEE_FIELD_ORDER  = ["mer", "dwert", "freq", "sr", "fec", "provider"]
_MARQUEE_FIELD_LABELS = {"mer": "MER", "dwert": "D-Wert", "freq": "Frequenz",
                          "sr": "Symbolrate", "fec": "FEC", "provider": "Provider"}

def _marquee_mer_dwert(target):
    """Liefert (MER in dB, D-Wert) fuer dieses Ziel, oder (None, None) wenn (noch) nicht
    bekannt - LongMynd/LongMynd P4 aus dem Status-UDP, rtl_sdr aus der --fd-info-Auswertung
    (kein eigenes D-Wert-Konzept dort, bleibt also immer None)."""
    if target in _LM_TARGETS:
        with _lm_status_lock:
            vals = dict(_lm_status_vals.get(target, {}))
        try:
            mer = float(vals.get(12)) / 10.0
        except (TypeError, ValueError):
            mer = None
        _mer2, dwert, _codec2 = _lm_extra_fields(target)
        return mer, dwert
    if target == "rtl_sdr":
        with _leandvb_info_lock:
            return _leandvb_info.get("mer_db"), None
    if target == "octagon":
        res = _octagon_poll_result[0]
        t = res[0] if res else None
        if t and t.get("lock") is not False:
            return t.get("snr_db"), None
    return None, None

def _marquee_field_value(key, target):
    """Formatierter Textbaustein fuer EIN Feld (oder None, wenn fuer dieses Ziel gerade/generell
    nicht verfuegbar) - dieselben Quellen wie die Status-Chips der jeweiligen Spalte."""
    if key == "mer":
        mer, _d = _marquee_mer_dwert(target)
        return ("MER: %.1f dB" % mer) if mer is not None else None
    if key == "dwert":
        _m, dwert = _marquee_mer_dwert(target)
        return ("D%+.1f" % dwert) if dwert is not None else None
    if key == "freq":
        if target in _LM_TARGETS:
            freq = _lm_measured_rf_mhz(target)
        elif target == "rtl_sdr":
            try:
                freq = float(frequency.get())
            except (TypeError, ValueError):
                freq = None
        elif target == "octagon":
            freq = _target_cur_freq.get("octagon")
        else:
            freq = None
        return ("%.3f MHz" % freq) if freq is not None else None
    if key == "sr":
        sr = _target_cur_sr.get(target)
        return ("%d kS" % sr) if sr else None
    if key == "fec":
        if target in _LM_TARGETS:
            fec_name = _lm_last_fec.get(target)
        elif target == "rtl_sdr":
            fec_name = fec.get() or None
        else:
            fec_name = None
        return fec_name
    if key == "provider":
        prov = None
        if target in _LM_TARGETS:
            with _lm_status_lock:
                prov = (_lm_status_vals.get(target, {}) or {}).get(14)
        if not prov:
            prov = (_target_video_info.get(target) or {}).get("provider")
        return prov or None
    return None

def _marquee_text_for(target):
    """Baut den Marquee-Text fuer ein Ziel: Rufzeichen immer zuerst, danach die in "Konfig ->
    Status..." angehakten Zusatzwerte (_MARQUEE_FIELD_ORDER-Reihenfolge) - Standard ist MER +
    D-Wert. Alles in EINER Zeile, durch " | " getrennt (wie beim Octagon-Marquee): VLCs marq
    stellt aus der Datei nur die erste Zeile dar, ein Zeilenumbruch schnitt alle Werte ab."""
    cs_var = _target_callsign_var.get(target)
    callsign = (cs_var.get() if cs_var is not None else "") or "-"
    fields = app_config.get("marquee_fields", ["mer", "dwert"])
    parts = []
    for key in _MARQUEE_FIELD_ORDER:
        if key not in fields:
            continue
        val = _marquee_field_value(key, target)
        if val:
            parts.append(val)
    if app_config.get("qrz_enabled", False) and callsign != "-":
        qi = _qrz_info_text(callsign)
        if qi:
            return " | ".join([callsign + "  " + qi] + parts)
    return " | ".join([callsign] + parts)

#----- QRZ.com: Rufzeichen nachschlagen (XML-Schnittstelle, Zugang in Konfig > QRZ.com-Zugang...) -----------
#      Ergebnis (Name, Ort, Land, Locator) wird in der VLC-Einblendung hinter dem Rufzeichen angezeigt.
_QRZ_URL = "https://xmldata.qrz.com/xml/current/"
_qrz = {"key": None, "cache": {}, "pending": set(), "err_t": 0.0, "lock": threading.Lock()}
_QRZ_CALL_RE = re.compile(r"\b([A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z])\b")

_QRZ_CC = {"germany": "DE", "fed. rep. of germany": "DE", "france": "FR", "netherlands": "NL", "belgium": "BE",
           "italy": "IT", "spain": "ES", "portugal": "PT", "england": "GB", "scotland": "GB", "wales": "GB",
           "northern ireland": "GB", "united kingdom": "GB", "ireland": "IE", "austria": "AT", "switzerland": "CH",
           "poland": "PL", "czech republic": "CZ", "slovak republic": "SK", "slovakia": "SK", "hungary": "HU",
           "denmark": "DK", "sweden": "SE", "norway": "NO", "finland": "FI", "turkey": "TR", "greece": "GR",
           "malta": "MT", "croatia": "HR", "slovenia": "SI", "serbia": "RS", "romania": "RO", "bulgaria": "BG",
           "luxembourg": "LU", "liechtenstein": "LI", "monaco": "MC", "cyprus": "CY", "israel": "IL",
           "madeira islands": "PT", "azores": "PT", "canary islands": "ES", "balearic islands": "ES",
           "sardinia": "IT", "sicily": "IT", "corsica": "FR", "brazil": "BR", "argentina": "AR",
           "united states": "US", "usa": "US", "canada": "CA", "south africa": "ZA", "egypt": "EG",
           "qatar": "QA", "united arab emirates": "AE", "saudi arabia": "SA", "kuwait": "KW", "oman": "OM",
           "india": "IN", "thailand": "TH", "ukraine": "UA", "lithuania": "LT", "latvia": "LV", "estonia": "EE",
           "morocco": "MA", "tunisia": "TN", "algeria": "DZ", "jordan": "JO", "lebanon": "LB", "iceland": "IS"}

def _qrz_country_code(country):
    """Laendername (QRZ.com) -> Kuerzel (DE, FR, ...); unbekannt: die ersten 3 Buchstaben."""
    c = (country or "").strip()
    if not c:
        return ""
    return _QRZ_CC.get(c.lower(), c[:3].upper())

def _qrz_base_call(text):
    """Rufzeichen aus einem Servicenamen ("PA5EN_Mark", "DL1ABC/P", "TV F1EJP") herausloesen."""
    t = re.sub(r"[_/\-]", " ", (text or "").upper())
    for m in _QRZ_CALL_RE.finditer(t):
        c = m.group(1)
        if re.search(r"[A-Z]", c[:3]) or c[:1].isdigit():
            return c
    return None

def _qrz_req(params):
    import urllib.parse, xml.etree.ElementTree as ET
    url = _QRZ_URL + "?" + ";".join("%s=%s" % (k, urllib.parse.quote(str(v))) for k, v in params.items())
    with urllib.request.urlopen(url, timeout=8) as r:
        root_el = ET.fromstring(r.read())
    ns = ""
    if root_el.tag.startswith("{"):
        ns = root_el.tag.split("}")[0] + "}"
    def find(path):
        el = root_el.find(path.replace("/", "/" + ns).replace("^", ns))
        return el.text.strip() if el is not None and el.text else ""
    sess = {k: find("^Session/" + k) for k in ("Key", "Error", "Message")}
    cs = {k: find("^Callsign/" + k) for k in ("call", "fname", "name", "addr2", "state", "country", "grid", "land")}
    return sess, cs

QRZ_CRED_FILE = os.path.join(parameters_path, "qrz.conf")

def _qrz_cred_get():
    """Login/Passwort aus der eigenen Datei ~/.leandvb-GUI/qrz.conf (Zeilen user=... / password=...)."""
    user = pw = ""
    try:
        with open(QRZ_CRED_FILE, encoding="utf-8") as fh:
            for ln in fh:
                k, _, v = ln.strip().partition("=")
                if k.strip() == "user":
                    user = v.strip()
                elif k.strip() == "password":
                    pw = v.strip()
    except OSError:
        pass
    return user, pw

def _qrz_cred_set(user, pw):
    try:
        os.makedirs(parameters_path, exist_ok=True)
        fd = os.open(QRZ_CRED_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# QRZ.com-Zugang fuer leandvb-GUI (nur fuer diesen Benutzer lesbar)\nuser=%s\npassword=%s\n" % (user, pw))
        os.chmod(QRZ_CRED_FILE, 0o600)
    except OSError as e:
        olog("QRZ.com: Zugangsdatei %s nicht schreibbar: %s" % (QRZ_CRED_FILE, e))

def _qrz_cred_template():
    """Beim Start: fehlt qrz.conf, eine leere Vorlage anlegen (Rechte 600) - zum Ausfuellen von Hand."""
    if os.path.exists(QRZ_CRED_FILE):
        return
    try:
        os.makedirs(parameters_path, exist_ok=True)
        fd = os.open(QRZ_CRED_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# QRZ.com-Zugang fuer leandvb-GUI (nur fuer diesen Benutzer lesbar)\n"
                     "# Rufzeichen und Passwort von qrz.com eintragen:\nuser=\npassword=\n")
    except OSError:
        pass

_qrz_cred_template()

def _qrz_login():
    user, pw = _qrz_cred_get()
    if not user or not pw:
        return None, "kein QRZ.com-Zugang eingetragen (Konfig > QRZ.com-Zugang...)"
    sess, _cs = _qrz_req({"username": user, "password": pw, "agent": "leandvb-GUI-DG7NDV"})
    if sess["Key"]:
        _qrz["key"] = sess["Key"]
        return sess["Key"], None
    return None, sess["Error"] or "Anmeldung fehlgeschlagen"

def _qrz_lookup(call):
    """Hintergrund-Thread: Rufzeichen nachschlagen und im Cache ablegen."""
    try:
        for attempt in range(2):
            key = _qrz["key"]
            if not key:
                key, err = _qrz_login()
                if not key:
                    if time.time() - _qrz["err_t"] > 300:
                        _qrz["err_t"] = time.time()
                        root.after(0, olog, "QRZ.com: %s" % err)
                    _qrz["cache"][call] = (time.time(), None)
                    return
            sess, cs = _qrz_req({"s": key, "callsign": call})
            if sess["Error"] and ("Session" in sess["Error"] or "Invalid session" in sess["Error"]) and attempt == 0:
                _qrz["key"] = None                    # Sitzung abgelaufen -> neu anmelden
                continue
            if cs["call"]:
                name = " ".join(x for x in (cs["fname"], cs["name"]) if x)
                place = ", ".join(x for x in (cs["addr2"], cs["country"]) if x)
                txt = " - ".join(x for x in (name, place, cs["grid"]) if x)
                land = cs["country"] or cs["land"]
                short = ", ".join(x for x in (cs["addr2"], _qrz_country_code(land)) if x)
                _qrz["cache"][call] = (time.time(), short or None)
                root.after(0, olog, "QRZ.com: %s = %s" % (call, txt or "(keine Angaben)"))
            else:
                _qrz["cache"][call] = (time.time(), None)
                dlog("QRZ.com: %s nicht gefunden (%s)" % (call, sess["Error"]))
            return
    except Exception as e:
        _qrz["cache"][call] = (time.time() - 3000, None)    # Netzfehler: in ~10 min erneut
        dlog("QRZ.com-Abfrage %s: %r" % (call, e))
    finally:
        with _qrz["lock"]:
            _qrz["pending"].discard(call)

def _qrz_info_text(callsign):
    """Text aus dem Cache (oder None); stoesst bei Bedarf die Abfrage im Hintergrund an (blockiert nie)."""
    call = _qrz_base_call(callsign)
    if not call:
        return None
    hit = _qrz["cache"].get(call)
    if hit and time.time() - hit[0] < (86400 if hit[1] else 3600):
        return hit[1]
    with _qrz["lock"]:
        if call in _qrz["pending"]:
            return hit[1] if hit else None
        _qrz["pending"].add(call)
    threading.Thread(target=_qrz_lookup, args=(call,), daemon=True).start()
    return hit[1] if hit else None

_qrz_disp_set = {}   # target -> zuletzt von uns gesetzter Anzeigetext im Rufzeichenfeld

def _qrz_disp_tick():
    """Hauptthread, alle 2 s: QRZ.com-Info auch im Rufzeichenfeld der Spalte (so viel wie hineinpasst,
    voller Text als Tooltip-Ersatz im Log/VLC). Wird nur gesetzt, solange das Feld das reine Rufzeichen zeigt."""
    try:
        on = app_config.get("qrz_enabled", False)
        for t, var in list(_target_callsign_var.items()):
            disp = _target_callsign_disp.get(t)
            lbl = _target_callsign_label.get(t)
            if disp is None or lbl is None:
                continue
            name = var.get()
            cur = disp.get()
            ours = _qrz_disp_set.get(t)
            if not name or (cur != name and cur != ours):
                _qrz_disp_set.pop(t, None)
                continue                       # leer oder anders dekoriert (z.B. "last: ...") - nicht anfassen
            info = _qrz_info_text(name) if on else None
            want = name
            if info:
                try:
                    maxc = int(lbl.cget("width")) - 1
                except (tk.TclError, ValueError):
                    maxc = 33
                want = "%s  %s" % (name, info)
                if len(want) > maxc:
                    want = want[:maxc - 1] + "\u2026"
            if cur != want:
                disp.set(want)
                _qrz_disp_set[t] = want
    except Exception as e:
        dlog("QRZ-Anzeige: %r" % (e,))
    root.after(2000, _qrz_disp_tick)

#----- Doppeltes Rufzeichen: zeigen zwei Tuner dieselbe Station, wird einer freigegeben (Player gestoppt,
#      Tuner gilt als frei fuer die Automatik). Sein Player kommt wieder, sobald er auf einem anderen Signal lockt. -----
_dup = {"since": {}, "closed": {}}     # since: Rufzeichen -> erstes Auftreten; closed: target -> Frequenz beim Freigeben

def _dup_tick():
    try:
        if app_config.get("dup_callsign_release", True):
            _dup_check()
        _dup_reopen()
    except Exception as e:
        dlog("Doppeltes Rufzeichen: %r" % (e,))
    root.after(5000, _dup_tick)

def _dup_check():
    now = time.time()
    by_call = {}
    for t in _active_targets:
        if t in _dup["closed"] or _scan_active.get(t) or _max.get("t") == t:
            continue
        var = _target_callsign_var.get(t)
        name = (var.get() if var is not None else "") or ""
        f = _target_cur_freq.get(t)
        if not name or f is None or abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5 or _callsign_stale.get(t):
            continue
        key = (_qrz_base_call(name) or name).upper()
        by_call.setdefault(key, []).append(t)
    # Doppelte Zuweisung auch ohne Rufzeichen: mehrere Tuner auf demselben Signal (Frequenz innerhalb der Toleranz, gleiche Datenrate)
    _grp = []
    for t in _active_targets:
        f = _target_cur_freq.get(t)
        if (t in _dup["closed"] or _scan_active.get(t) or _max.get("t") == t or f is None or abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5
                or any(t in v for v in by_call.values())):
            continue
        for g in _grp:
            if abs(g["f"] - f) <= BATC_SIG_TOL and g["sr"] == _target_cur_sr.get(t):
                g["ts"].append(t); break
        else:
            _grp.append({"f": f, "sr": _target_cur_sr.get(t), "ts": [t]})
    for g in _grp:
        if len(g["ts"]) > 1:
            by_call["%.2f MHz" % g["f"]] = g["ts"]
    for key in list(_dup["since"]):
        if len(by_call.get(key, [])) < 2:
            del _dup["since"][key]
    for key, ts in by_call.items():
        if len(ts) < 2:
            continue
        t0 = _dup["since"].setdefault(key, now)
        if now - t0 < 10.0:
            continue                       # erst wenn es 10 s so bleibt (kein kurzes Umschalten)
        del _dup["since"][key]
        # behalten: wer auf der Frequenz steht, wo das Spektrum das Rufzeichen kennt - sonst der zuerst eingestellte
        known = [ff for ff, nm in list(_spectrum_known_callsigns.items())
                 if (_qrz_base_call(nm) or nm or "").upper() == key]
        def score(t):
            f = _target_cur_freq.get(t) or 0
            on_known = any(abs(f - kf) <= _SPECTRUM_CALLSIGN_TOL for kf in known)
            return (_prio_rank(t, "doppelt"), 0 if on_known else 1, _target_dispatch_t.get(t, 0.0))     # hoechster Rang (Menue Prio) behaelt, die anderen werden freigegeben
        ts.sort(key=score)
        # freigeben darf die Automatik nur Tuner mit 'Verteil Automatik' + 'VerteilA' - die anderen bleiben unberuehrt
        movable = [t for t in ts if _verteil_ok(t)]
        if not movable:
            continue
        keep = next((t for t in ts if t not in movable), ts[0])
        drop = [t for t in ts if t != keep and t in movable]
        for t in drop:
            _dup["closed"][t] = _target_cur_freq.get(t)
            olog("Doppeltes Signal/Rufzeichen %s auf %s und %s - %s wird freigegeben (Player gestoppt, Tuner frei fuer neue Signale)." % (
                key, _TARGET_LABEL.get(keep, keep), _TARGET_LABEL.get(t, t), _TARGET_LABEL.get(t, t)))
            _stop_player(t)
            try:
                if _dvx_is_dienst(t):
                    _dvx_stop(t)                # Stick des Pi-Dienstes: dort ebenfalls freigeben (sonst bleibt er festgehalten und die Automatik kann ihn nicht neu belegen)
            except Exception as e:
                dlog("Doppelt: Dienst-Stick %s nicht freigegeben: %r" % (t, e))
            _target_last_tuned[t] = None
            _target_cur_freq[t] = None          # gilt fuer die Verteilung als frei
        batc_auto_assign(report=True)

def _dup_reopen():
    """Freigegebener Tuner: Player wieder oeffnen, sobald er auf einem ANDEREN Signal Lock hat."""
    for t, f_old in list(_dup["closed"].items()):
        f = _target_cur_freq.get(t)
        if f is None or (f_old is not None and abs(f - f_old) <= BATC_SIG_TOL):
            continue
        if abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
            continue                        # steht der freigegebene Tuner (wieder) auf der Bake: Player bleibt aus, Tuner bleibt frei fuer neue Signale
        if _fw_locked(t) is not True and t not in ("octagon",):
            continue
        del _dup["closed"][t]
        olog("%s: neues Signal %.3f MHz - Player wird wieder geoeffnet." % (_TARGET_LABEL.get(t, t), f))
        _player_wanted[t] = True
        _start_player(t, _player_active.get(t) or "vlc")

def _qrz_open_page(target):
    """Klick auf das Rufzeichenfeld: QRZ.com-Seite des Rufzeichens im Browser oeffnen."""
    var = _target_callsign_var.get(target)
    name = var.get() if var is not None else ""
    call = _qrz_base_call(name)
    if not call:
        olog("QRZ.com: im Feld '%s' ist kein Rufzeichen erkennbar." % (name or "-"))
        return
    url = "https://www.qrz.com/db/%s" % call
    olog("QRZ.com: oeffne %s im Browser." % url)
    def work():
        try:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import webbrowser
                webbrowser.open(url)
        except Exception as e:
            root.after(0, olog, "QRZ.com: Browser nicht startbar: %r" % (e,))
    threading.Thread(target=work, daemon=True).start()

def _qrz_settings_open():
    win = tk.Toplevel(root)
    win.title("QRZ.com-Zugang")
    win.resizable(False, False)
    win.transient(root)
    ttk.Label(win, text="Zugang fuer die QRZ.com-XML-Abfrage (vollstaendige Daten nur mit XML-Abo;\n"
                         "ohne Abo liefert QRZ.com nur einen Teil, z.B. Name/Land):", justify='left'
              ).grid(row=0, column=0, columnspan=2, sticky='w', padx=8, pady=(8, 6))
    _u0, _p0 = _qrz_cred_get()
    vu = tk.StringVar(value=_u0)
    vp = tk.StringVar(value=_p0)
    ttk.Label(win, text="Benutzer (Rufzeichen):").grid(row=1, column=0, sticky='e', padx=8)
    ttk.Entry(win, textvariable=vu, width=22).grid(row=1, column=1, sticky='w', padx=8, pady=2)
    ttk.Label(win, text="Passwort:").grid(row=2, column=0, sticky='e', padx=8)
    ttk.Entry(win, textvariable=vp, width=22, show="*").grid(row=2, column=1, sticky='w', padx=8, pady=2)
    res = tk.StringVar(value="")
    ttk.Label(win, textvariable=res, foreground="#555555").grid(row=3, column=0, columnspan=2, sticky='w', padx=8)
    ttk.Label(win, text="Gespeichert in %s (nur fuer dich lesbar)" % QRZ_CRED_FILE, font=('', 8),
              foreground="#777777").grid(row=5, column=0, columnspan=2, sticky='w', padx=8, pady=(0, 6))

    def _save(test=False):
        _qrz_cred_set(vu.get().strip(), vp.get())
        _qrz["key"] = None
        _qrz["cache"].clear()
        if test:
            res.set("pruefe ...")
            def work():
                try:
                    key, err = _qrz_login()
                    msg = "Anmeldung OK." if key else "Fehler: %s" % err
                except Exception as e:
                    msg = "Fehler: %r" % (e,)
                root.after(0, res.set, msg)
            threading.Thread(target=work, daemon=True).start()
        else:
            win.destroy()
    bf = ttk.Frame(win); bf.grid(row=4, column=0, columnspan=2, pady=8)
    ttk.Button(bf, text="Testen", command=lambda: _save(True)).pack(side='left', padx=4)
    ttk.Button(bf, text="Speichern", command=_save).pack(side='left', padx=4)
    ttk.Button(bf, text="Abbrechen", command=win.destroy).pack(side='left', padx=4)

def _marquee_settings_open():
    """Konfig -> Status...: alle verfuegbaren Zusatzwerte als Checkbox, angehakte erscheinen in
    der Marquee-Einblendung (VLC, rtl_sdr/LongMynd/LongMynd P4) - Reihenfolge immer Rufzeichen
    zuerst, danach die angehakten Felder in der festen _MARQUEE_FIELD_ORDER-Reihenfolge."""
    win = tk.Toplevel(root)
    win.title("Status-Einblendung (VLC-Marquee)")
    win.resizable(False, False)
    win.transient(root)
    ttk.Label(win, text="Zusatzwerte in der VLC-Einblendung (rtl_sdr/LongMynd/LongMynd P4) -\n"
                         "das Rufzeichen steht immer an erster Stelle, angehakte Felder danach:",
              justify='left').grid(row=0, column=0, sticky='w', padx=8, pady=(8, 6))
    cur_fields = set(app_config.get("marquee_fields", ["mer", "dwert"]))
    on_var = tk.BooleanVar(value=app_config.get("marquee_on", False))
    ttk.Checkbutton(win, text="Einblendung im VLC anzeigen (gilt ab dem naechsten Player-Start)", variable=on_var
                     ).grid(row=1 + len(_MARQUEE_FIELD_ORDER) + 1, column=0, sticky='w', padx=8, pady=(6, 0))
    field_vars = {}
    for i, key in enumerate(_MARQUEE_FIELD_ORDER):
        var = tk.BooleanVar(value=(key in cur_fields))
        ttk.Checkbutton(win, text=_MARQUEE_FIELD_LABELS[key], variable=var
                         ).grid(row=1 + i, column=0, sticky='w', padx=16, pady=1)
        field_vars[key] = var

    def _save():
        app_config["marquee_fields"] = [k for k in _MARQUEE_FIELD_ORDER if field_vars[k].get()]
        app_config["marquee_on"] = bool(on_var.get())
        app_config_save()
        win.destroy()

    btns = ttk.Frame(win)
    btns.grid(row=1 + len(_MARQUEE_FIELD_ORDER) + 2, column=0, pady=(8, 8))
    ttk.Button(btns, text="Speichern", command=_save).pack(side='left', padx=4)
    ttk.Button(btns, text="Abbrechen", command=win.destroy).pack(side='left', padx=4)

def _mer_style(mer):
    """Farbe der MER-Anzeige: gruen, wenn der MER ausreicht (app_config['mer_ok_db'], Standard 5 dB), sonst grau; ohne Wert grau"""
    try:
        thr = float(app_config.get("mer_ok_db", 5.0))
    except (TypeError, ValueError):
        thr = 5.0
    if mer is not None and mer >= thr:
        return {"background": "#2ecc71", "foreground": "white"}
    return {"background": "#d9d9d9", "foreground": "black"}

def _mer_from_text(txt):
    m = re.search(r"(-?\d+(?:[.,]\d+)?)", txt or "")
    try:
        return float(m.group(1).replace(",", ".")) if m else None
    except ValueError:
        return None

def _callsign_signal_gone(target):
    """True, wenn auf der eingestellten Frequenz dieses Ziels gerade KEIN lebendes BATC-Signal
    mehr steht (verschwundene Signale behaelt batc_tick noch 10 s als "weg" in der Liste)."""
    f = _target_cur_freq.get(target)
    if f is None:
        return False
    if abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
        # Die Bake wird aus der BATC-Signalliste ausgeblendet (siehe BATC_BEACON_MHZ) und steht dort
        # nie drin - sie gilt daher NIE als "verschwunden"; es zaehlt nur die zeitliche Alterung.
        return False
    with _batc_lock:
        for st in _batc_sig_state.values():
            if abs(st["freq"] - f) <= BATC_SIG_TOL:
                return st["gone_since"] is not None
    return True          # Frequenz taucht in der BATC-Liste gar nicht mehr auf

def _callsign_fade_tick():
    """Rufzeichen klein und grau darstellen, sobald seit CALLSIGN_STALE_S nichts mehr decodiert
    wurde ODER der BATC-Server das Signal als verschwunden meldet - mit der Uhrzeit der letzten
    Erkennung ("last: hh:mm:ss"), damit man sieht, wann zuletzt wirklich jemand da war."""
    now = time.time()
    for t in _active_targets:
        lbl = _target_callsign_label.get(t)
        disp = _target_callsign_disp.get(t)
        var = _target_callsign_var.get(t)
        if lbl is None or disp is None or var is None:
            continue
        name = var.get()
        if not name:
            continue
        seen, hhmmss = _callsign_seen.get(t, (0.0, "?"))
        cyc = _callsign_poll_cycle.get(t, 5.0)
        age = now - seen
        # grau: seit 10 s (+ Abfragezyklus) nichts mehr dekodiert, ODER BATC meldet das Signal als weg und
        # es gab im letzten Zyklus keine frische Bestaetigung (sonst flackerte es bei jeder Abfrage)
        stale = (age > CALLSIGN_STALE_S + cyc) or (age > cyc + 2.0 and _callsign_signal_gone(t))
        if stale == bool(_callsign_stale.get(t)):
            continue                      # nichts zu aendern - kein unnoetiges Neuzeichnen
        _callsign_stale[t] = stale
        if stale:
            disp.set("%s   last: %s" % (name, hhmmss))
            lbl.configure(background="#d9d9d9", foreground="#777777", font=_CALLSIGN_FONT_OLD)
        else:
            disp.set(name)
            lbl.configure(background="#2ecc71", foreground="black", font=_CALLSIGN_FONT)

def _marquee_tick():
    """Einmal pro Sekunde: aktualisiert die Marquee-Datei jedes Ziels, bei dem gerade VLC als
    Player aktiv ist (_player_active) - laeuft dauerhaft, unabhaengig davon, ob/wann VLC gerade
    gestartet wird (schreibt dann einfach erst, sobald ein Player aktiv ist)."""
    _callsign_fade_tick()
    for _t in _active_targets:
        if _t == "octagon" and not _oct_direct():
            continue   # hat eigene Marquee-Aktualisierung, siehe marquee_loop()/process_signal()
        if _player_active.get(_t) != "vlc" or not app_config.get("marquee_on", False):
            continue
        _write_marquee_file(_player_marquee_file(_t), _marquee_text_for(_t))
    root.after(1000, _marquee_tick)

def _player_stream_url(target):
    """Stream-Adresse fuer diesen Player-Start - udp://@ip:port (Multicast, wie im watchdog-Skript
    des Nutzers) fuer rtl_sdr/LongMynd/LongMynd P4, die aktuelle http://box:port/sref-URL fuer
    Octagon (per current_service() ermittelt, damit auch ohne vorherigen Zap ueber die GUI aktuell)."""
    if target == "octagon" and _oct_direct():
        ip, port = _oct_direct_stream()
        return "udp://@%s:%s" % (ip, port)
    if target == "octagon":
        ref, _name, _src = current_service(cliargs.box)
        if not ref:
            olog("    Octagon: aktueller Service nicht ermittelbar (%s) - keine Stream-URL." % _src)
            return None
        url = stream_url(cliargs.box, ref, cliargs.octagon_stream_port)
        olog("    Octagon-Stream-URL: %s" % url)
        return url
    get_stream = _TARGET_STREAM.get(target)
    _ep = get_stream() if get_stream else None
    if not _ep:
        return None
    ip, port = _ep
    return "udp://@%s:%s" % (ip, port)

def _is_port_open(host, port, timeout=0.4):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def _update_player_buttons(target):
    active = _player_active.get(target)
    for name, btn in _player_buttons.get(target, {}).items():
        btn.configure(style="RecOn.TButton" if name == active else "TButton")

# Start-Pruefung: nach dem Programmstart werden zuerst die BATC-Signale mit den gemerkten Player-Fenstern verglichen (app_config["last_players"]);
# bis dahin startet KEIN Player (auch nicht fuer die Bake). Danach: kein automatischer Player fuer eine Frequenz ohne lebendes Signal (AutoClose an).
_hold = {"active": True, "t0": time.time(), "first": None, "logged": set(), "nolive_logged": set(), "bake_logged": set(), "nodata_logged": set()}
_einfach = {"on": False, "view": None, "mini_was": False, "root_pack": None}

def _onair_probe_other_udp(target):
    """OnAir sendet manchmal auf einer anderen Multicast-Adresse als die GUI erwartet (z.B. 230.0.0.11 statt .12): Nachbaradressen pruefen und melden."""
    try:
        exp = _onair_udp(target)
        base = exp[0].rsplit(".", 1)[0]
        for n in range(10, 14):
            ip = "%s.%d" % (base, n)
            if (ip, exp[1]) == tuple(exp):
                continue
            if _stream_has_data(ip, exp[1], 1.0):
                root.after(0, olog, "%s: auf %s:%d kommen Daten, die GUI erwartet %s:%d - falls das dieses OnAir ist, im OnAir-Fenster (Tab Stream) die Adresse auf %s:%d aendern." % (
                    _TARGET_LABEL.get(target, target), ip, exp[1], exp[0], exp[1], exp[0], exp[1]))
                return
    except Exception as e:
        dlog("OnAir-Adresssuche: %r" % (e,))

def _start_player(target, player, user=False):
    """Startet den gewaehlten Player (vlc/mpv/ffplay) fuer dieses Ziel - bei vlc wird zuerst per
    Telnet-Port-Check geprueft, ob schon einer laeuft (z.B. manuell zu Testzwecken gestartet),
    dann NICHT neu gestartet. user=True: Knopfdruck des Nutzers - nie zurueckgestellt."""
    if _einfach["on"]:
        return                                        # Einfach-Ansicht: die Videos laufen im Hauptfenster, keine einzelnen VLC-Fenster
    if not user:
        if _hold["active"]:
            if target not in _hold["logged"]:
                _hold["logged"].add(target)
                root.after(0, olog, "%s: Player-Start zurueckgestellt - die Start-Pruefung der gemerkten Signale laeuft noch." % _TARGET_LABEL.get(target, target))
            return
        if app_config.get("autoclose", False) and _autoclose_endpoint(target) and _no_live_signal(target):
            if target not in _hold["nolive_logged"]:
                _hold["nolive_logged"].add(target)
                root.after(0, olog, "%s: kein Player - auf %.3f MHz meldet BATC kein Signal." % (_TARGET_LABEL.get(target, target), _target_cur_freq.get(target) or 0.0))
            return
        _hold["nolive_logged"].discard(target)
        _cf = _target_cur_freq.get(target)
        if ((_cf is not None and abs(_cf - BATC_BEACON_NOMINAL_MHZ) <= 0.5) or (_cf is None and target == "octagon")) and not app_config.get("bake_player", False):
            # Octagon (Direktmodus) ohne bekannte Frequenz steht nach dem Start auf der Bake: erst Player, wenn ihm ein Signal zugeteilt wurde
            if target not in _hold["bake_logged"]:
                _hold["bake_logged"].add(target)
                root.after(0, olog, "%s: kein automatischer Player fuer die Bake (Knopf im Tuner-Menue/Player-Knopf startet ihn von Hand)." % _TARGET_LABEL.get(target, target))
            return
        _hold["bake_logged"].discard(target)
        _st = _tsrate_state.get(target)
        if _st is not None and _autoclose_endpoint(target) and time.monotonic() - _st.get("last_rx", 0.0) > 4.0:
            if target not in _hold["nodata_logged"]:
                _hold["nodata_logged"].add(target)
                root.after(0, olog, "%s: Player noch nicht gestartet - es kommt kein Stream (UDP) an (OnAir: Haken 'stream' pruefen)." % _TARGET_LABEL.get(target, target))
                if target in _ONAIR_TARGETS:
                    threading.Thread(target=_onair_probe_other_udp, args=(target,), daemon=True).start()
            return
        _hold["nodata_logged"].discard(target)
    stream_url_ = _player_stream_url(target)
    if not stream_url_:
        olog("%s: keine Stream-Adresse bekannt - %s nicht gestartet." %
             (_TARGET_LABEL.get(target, target), player))
        return

    _prev_start = _player_started_at.get(target, 0.0)
    _player_active[target] = player
    _player_wanted[target] = True
    _player_started_at[target] = time.time()
    _wm_kick()
    _update_player_buttons(target)

    if player == "vlc":
        port = _player_telnet_port(target)
        if port is not None and _is_port_open("127.0.0.1", port):
            olog("%s: VLC laeuft schon (Telnet-Port %d) - kein Neustart." %
                 (_TARGET_LABEL.get(target, target), port))
            return
        # Doppelstart verhindern: ein gerade gestarteter VLC hat seinen Telnet-Port evtl. noch nicht offen
        if port is not None:
            tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port)
            running = [l.split()[0] for l in _ps_lines() if "vlc" in l.lower() and any(t in l for t in tags)]
            if running:
                olog("%s: VLC fuer Telnet-Port %d laeuft schon (PID %s) - kein zweiter Start." % (
                    _TARGET_LABEL.get(target, target), port, ", ".join(running)))
                return
        if time.time() - _prev_start < 5.0 and _player_proc.get(target) is not None and _player_proc[target].poll() is None:
            return
        http_port = _player_http_port(target)
        # HTTP-Port des VLC darf nicht mit der Web-Steuerung eines lokalen longmynd (8080) oder dem
        # Webinterface der GUI kollidieren - sonst startet longmynd 2 ohne Web-Steuerung ("Connection refused")
        try:
            _reserved = {int((_lm2_entry() or {}).get("lokal_port") or 8080), int(app_config.get("web_port") or 8099)}
        except (TypeError, ValueError):
            _reserved = {8080, 8099}
        _hp0 = http_port
        while http_port is not None and int(http_port) in _reserved:
            http_port = int(http_port) + 2
        if http_port != _hp0:
            dlog("%s: VLC-HTTP-Port %s belegt/reserviert -> %s" % (target, _hp0, http_port))
        marquee_file = _player_marquee_file(target)
        try:
            open(marquee_file, "a").close()
        except OSError:
            pass
        _bl = ["--qt-minimal-view"] if app_config.get("vlc_borderless", False) else []
        cmd = ["vlc", "--extraintf=telnet,http", "--network-caching=1000", "--demux=ts", stream_url_] + _bl + [
               "--http-host=0.0.0.0", "--http-port=%s" % http_port,
               "--telnet-host=127.0.0.1", "--telnet-port=%s" % port, "--telnet-password=admin",
               "--avcodec-hw=none", "--playlist-enqueue",
               "--autoscale", "--no-qt-video-autoresize", "--vout=gl,xcb_xv,any",   # skalierende Videoausgabe: Bild fensterfuellend (xcb_x11 kann nicht skalieren)
               ] + (["--sub-source=marq", "--marq-file=%s" % marquee_file, "--marq-position=4",
                     "--marq-opacity=200", "--marq-size=20", "--marq-color=0xFFFFFF", "--marq-refresh=1000"]
                    if app_config.get("marquee_on", False) else [])
    elif player == "mpv":
        ipc_path = _mpv_ipc_path(target)
        try:
            os.remove(ipc_path)
        except OSError:
            pass
        snap_dir = app_config.get("vlc_snapshot_path") or os.path.expanduser("~/Pictures")
        try:
            os.makedirs(snap_dir, exist_ok=True)
        except OSError:
            pass
        cmd = ["mpv", stream_url_, "--title=%s" % _TARGET_LABEL.get(target, target)] + \
              (["--no-border"] if app_config.get("vlc_borderless", False) else []) + [
               "--input-ipc-server=%s" % ipc_path,
               "--screenshot-directory=%s" % snap_dir,
               "--screenshot-template=mpv_%s_%%tY-%%tm-%%td_%%tH-%%tM-%%tS" % target]
    elif player == "ffplay":
        cmd = ["ffplay", "-window_title", _TARGET_LABEL.get(target, target), stream_url_]
    else:
        return

    exe = shutil.which(cmd[0])
    if not exe:
        olog("%s nicht gefunden - kann fuer %s nicht gestartet werden." %
             (cmd[0], _TARGET_LABEL.get(target, target)))
        return
    try:
        _focus_guard()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        _player_proc[target] = proc
        olog("%s: %s gestartet (PID %s) - %s" %
             (_TARGET_LABEL.get(target, target), player, proc.pid, stream_url_))
    except OSError as e:
        olog("Fehler beim Start von %s fuer %s: %s" % (player, _TARGET_LABEL.get(target, target), e))

def _stop_player(target, on_done=None):
    """Stoppt den aktuell aktiven Player dieses Ziels - einen von der GUI gestarteten Prozess
    sauber per terminate()/kill(), einen (noch) extern/manuell gestarteten VLC per Telnet-
    'shutdown', falls dessen Telnet-Port erreichbar ist. Auf das Prozessende wird in einem
    Hintergrund-Thread gewartet (vorher blockierte proc.wait() die GUI bis zu 3 s); on_done
    wird danach im Hauptthread aufgerufen (z.B. Neustart)."""
    _player_wanted[target] = False
    player = _player_active.get(target)
    label = _TARGET_LABEL.get(target, target)
    proc = _player_proc.get(target)
    if proc is not None and proc.poll() is None:
        _player_proc[target] = None
        try:
            proc.terminate()
        except OSError:
            pass
        def _wait():
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            root.after(0, olog, "%s: %s (PID %s) gestoppt." % (label, player, proc.pid))
            if on_done:
                root.after(0, on_done)
        threading.Thread(target=_wait, daemon=True).start()
        return
    if player == "vlc":
        port = _player_telnet_port(target)
        tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port) if port is not None else ()
        if port is not None and (_is_port_open("127.0.0.1", port, timeout=0.4) or
                                 any("vlc" in l.lower() and any(t in l for t in tags) for l in _ps_lines())):
            _vlc_telnet_send(port, "shutdown", label)
            olog("%s: VLC per Telnet beendet (shutdown)." % label)
            def _ensure_gone():
                # 'shutdown' beendet VLC nicht immer ganz (Fenster bleibt, Steuerung tot) - dann hart beenden
                time.sleep(2.0)
                _vlc_kill_port(port, label)
                if on_done:
                    root.after(0, on_done)
            threading.Thread(target=_ensure_gone, daemon=True).start()
            return
    olog("%s: kein laufender Player bekannt zum Stoppen." % label)
    if on_done:
        root.after(0, on_done)

def _pause_player(target):
    """Pause/Weiter umschalten: bei VLC per Telnet-Kommando 'pause' (togglet dort selbst), bei
    einem von der GUI gestarteten mpv/ffplay per SIGSTOP/SIGCONT (die haben kein Telnet-Interface).
    Bei einem nicht von der GUI gestarteten mpv/ffplay (z.B. manuell) ist das nicht steuerbar."""
    player = _player_active.get(target)
    label = _TARGET_LABEL.get(target, target)

    if player == "vlc":
        port = _player_telnet_port(target)
        if port is not None and _is_port_open("127.0.0.1", port):
            _vlc_telnet_send(port, "pause", label)
            olog("%s: VLC Pause/Weiter (Telnet) umgeschaltet." % label)
        else:
            olog("%s: kein VLC per Telnet erreichbar zum Pausieren." % label)
        return

    proc = _player_proc.get(target)
    if proc is None or proc.poll() is not None:
        olog("%s: kein laufender Player bekannt zum Pausieren." % label)
        return
    paused = _player_paused.get(target, False)
    try:
        if paused:
            proc.send_signal(signal.SIGCONT)
            _player_paused[target] = False
            olog("%s: %s fortgesetzt." % (label, player))
        else:
            proc.send_signal(signal.SIGSTOP)
            _player_paused[target] = True
            olog("%s: %s pausiert." % (label, player))
    except OSError as e:
        olog("Fehler beim Pausieren von %s (%s): %s" % (player, label, e))

_vlc_muted = False   # Umschalt-Zustand des "Mute all"-Knopfes

def vlc_mute_all():
    """'Mute all'-Knopf: Umschalter. 1. Druck -> alle steuerbaren VLC-Player (LongMynd,
    LongMynd P4, Octagon - je nachdem welche Ziele aktiv sind) per Telnet stummschalten
    (Lautstaerke 0) und Knopf gruen faerben. 2. Druck -> wieder entstummen (Lautstaerke 256,
    VLC-Standard) und Knopf wieder grau/normal."""
    global _vlc_muted
    players = _vlc_controllable_players()
    if not players:
        olog("Mute all: kein steuerbarer VLC-Player aktiv.")
        return
    if not _vlc_muted:
        for label, port in players:
            _vlc_telnet_send(port, "volume 0", label)
        olog("Mute all: Lautstaerke auf 0 gesetzt bei " +
             ", ".join("%s (Port %d)" % (l, p) for l, p in players))
        _vlc_muted = True
        btn_batc_mute_all.configure(style="RecOn.TButton")
    else:
        for label, port in players:
            _vlc_telnet_send(port, "volume 256", label)
        olog("Mute all: wieder entstummt bei " +
             ", ".join("%s (Port %d)" % (l, p) for l, p in players))
        _vlc_muted = False
        btn_batc_mute_all.configure(style="TButton")

def vlc_unmute_all():
    """'Ton alle an': Lautstaerke aller steuerbaren VLC-Player auf 256 (VLC-Standard) setzen - unabhaengig
    vom Zustand des Mute-all-Umschalters, der danach wieder auf 'normal' steht."""
    global _vlc_muted
    players = _vlc_controllable_players()
    if not players:
        olog("Ton alle an: kein steuerbarer VLC-Player aktiv.")
        return
    for label, port in players:
        _vlc_telnet_send(port, "volume 256", label)
    olog("Ton alle an: Lautstaerke 256 bei " + ", ".join("%s (Port %d)" % (l, p) for l, p in players))
    _vlc_muted = False
    btn_batc_mute_all.configure(style="TButton")

def longmynd_cmd(key):
    """Schickt 's' (Snapshot) oder 'v' (Aufnahme starten/stoppen) an das laufende
    auswertung_4002.sh - genau wie das Druecken der Taste in dessen Terminal-Fenster."""
    try:
        with open("/tmp/vlc_cmd_%d.txt" % cliargs.longmynd_status_port, "w") as f:
            f.write(key)
    except OSError as e:
        olog("Fehler beim Schreiben der Longmynd-Kommando-Datei: %s" % e)

_target_callsign_var = {}   # target -> tk.StringVar mit dem REINEN Rufzeichen (Snapshot-Name, Marquee)
_target_callsign_disp = {}  # target -> tk.StringVar fuer die Anzeige (ggf. mit "last: hh:mm:ss")
_callsign_seen = {}         # target -> (Zeitstempel, "hh:mm:ss") der letzten Erkennung
_callsign_stale = {}        # target -> True, solange das Signal als weg gilt

CALLSIGN_STALE_S = 10.0     # Sekunden ohne Erkennung, nach denen das Rufzeichen grau/klein wird
                            # (plus die gemessene Dauer eines Abfragezyklus, siehe _callsign_poll_cycle)
_callsign_poll_cycle = {}   # target -> Sekunden zwischen zwei Abfragen (ffprobe-Dauer + Pause)
_CALLSIGN_FONT      = ('', 11, 'bold')
_CALLSIGN_FONT_OLD  = ('', 8)

_TARGET_STREAM = {
    "longmynd":    lambda: (cliargs.longmynd_stream_ip, cliargs.longmynd_stream_port),
    "longmynd2":   lambda: (cliargs.longmynd2_stream_ip, cliargs.longmynd2_stream_port),
    "longmynd_p4": lambda: (cliargs.longmyndp4_stream_ip, cliargs.longmyndp4_stream_port),
    "rtl_sdr":     lambda: (cliargs.rtlsdr_stream_ip, cliargs.rtlsdr_stream_port),
}
for _ot in _ONAIR_TARGETS:
    _TARGET_STREAM[_ot] = (lambda t=_ot: _onair_udp(t))
for _ot in _DATVRX_TARGETS:
    _TARGET_STREAM[_ot] = (lambda t=_ot: _dvx_udp(t))
_TARGET_STREAM["octagon"] = lambda: (_oct_direct_stream() if _oct_direct() else None)
_tsrate_canvas = {}   # target -> tk.Canvas der Datenstrom-Kurve
_tsrate_state = {}    # target -> {"hist": deque((Mbit gesamt, Mbit netto)), "acc", "t0", "last_rx"}

TSA_DIR = "/home/dg7ndv/prog/tsanalyse"
TSA_DEFAULT_CTRL_PORT = 55555   # Standard-Fernsteuerport (UDP) der tsanalyse
_tsa_procs = {}

def _tsa_ctrl_port(target):
    """Eigener Fernsteuerport je Ziel (mehrere tsanalyse parallel): app_config["tsa_ctrl_ports"][ziel],
    sonst 55555, 55556, ... in der Reihenfolge der Spalten."""
    custom = (app_config.get("tsa_ctrl_ports") or {}).get(target)
    try:
        if custom:
            return int(custom)
    except (TypeError, ValueError):
        pass
    return TSA_DEFAULT_CTRL_PORT + (_active_targets.index(target) if target in _active_targets else 0)

def _tsa_send(port, cmd, wait=0.6):
    """UDP-Kommando an die tsanalyse-Fernsteuerung (127.0.0.1:port); liefert die Antwort (str) oder None."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sk.settimeout(wait)
        sk.sendto((cmd + "\n").encode(), ("127.0.0.1", port))
        try:
            data, _a = sk.recvfrom(4096)
            return data.decode(errors="replace").strip()
        except (socket.timeout, OSError):
            return None
    except OSError:
        return None
    finally:
        sk.close()

def _tsa_reset(target, reason=""):
    """RESET (Zaehler/Verlauf/Tabellen) an die tsanalyse dieses Ziels - z.B. bei neuem Rufzeichen,
    weil mehrere Programme ueber denselben UDP-Port laufen. Ohne laufende tsanalyse passiert nichts."""
    proc = _tsa_procs.get(target)
    if proc is None or proc.poll() is not None:
        return
    port = _tsa_ctrl_port(target)
    def work():
        _tsa_send(port, "RESET", wait=0.3)
        dlog("TSA %s: RESET gesendet (%s)." % (_TARGET_LABEL.get(target, target), reason))
    threading.Thread(target=work, daemon=True).start()

def _tsa_lm_setup(target, ctrl_port):
    """LongMynd-Status-Empfang der tsanalyse auf den Weiterleitungs-Statusport dieses Tuners stellen
    (LMPORT) - so streiten sich TSA und GUI-Listener nicht um 4002/4003."""
    if target not in _LM_TARGETS:
        return
    # Statusport laut Tuner-Konfig (longmynd 4002, longmynd2/P4 4003). Den echten Port haelt die GUI
    # exklusiv (sonst bekaeme nur einer der beiden die Pakete) - die TSA liest deshalb standardmaessig
    # den Spiegelport (status_forward_port), der dieselben Pakete liefert. Mit
    # app_config["tsa_lm_use_status_port"] = true bekommt sie den echten Statusport (dann sollte die
    # GUI-eigene Auswertung nicht parallel auf demselben Port lauschen).
    if app_config.get("tsa_lm_use_status_port"):
        st = {"longmynd": cliargs.longmynd_status_port, "longmynd2": cliargs.longmynd2_status_port,
              "longmynd_p4": cliargs.longmyndp4_status_port}.get(target)
        kind = "Statusport"
    else:
        st = {"longmynd": cliargs.longmynd_status_forward_port, "longmynd2": cliargs.longmynd2_status_forward_port,
              "longmynd_p4": cliargs.longmyndp4_status_forward_port}.get(target)
        kind = "Spiegelport des Statusports"
    try:
        st = int(st)
    except (TypeError, ValueError):
        return
    if st > 0:
        reply = _tsa_send(ctrl_port, "LMPORT %d 127.0.0.1" % st, wait=0.6)
        print("TSA %s: Fernsteuerung 127.0.0.1:%d <- LMPORT %d 127.0.0.1  (%s; Antwort: %s)" %
              (_TARGET_LABEL.get(target, target), ctrl_port, st, kind, reply), flush=True)

def _tsa_setup_ctrl_port(target):
    """Nach dem Start: tsanalyse auf den eigenen Fernsteuerport (CTRLPORT) umstellen, damit mehrere
    Instanzen nicht auf 55555 kollidieren. Wartet bis zu 10 s, bis die Instanz antwortet."""
    label = _TARGET_LABEL.get(target, target)
    own = _tsa_ctrl_port(target)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        proc = _tsa_procs.get(target)
        if proc is None or proc.poll() is not None:
            return
        if _tsa_send(own, "PING", wait=0.4) is not None:
            _tsa_lm_setup(target, own)
            return                      # lauscht schon auf dem eigenen Port
        if _tsa_send(TSA_DEFAULT_CTRL_PORT, "PING", wait=0.4) is not None:
            _tsa_send(TSA_DEFAULT_CTRL_PORT, "CTRLPORT %d" % own, wait=0.6)
            time.sleep(0.5)
            if _tsa_send(own, "PING", wait=0.6) is not None:
                root.after(0, olog, "%s: TSA-Fernsteuerung auf UDP-Port %d umgestellt." % (label, own))
                return
        time.sleep(0.5)
    root.after(0, olog, "%s: TSA antwortet nicht auf der Fernsteuerung (Port %d / %d)." %
               (label, own, TSA_DEFAULT_CTRL_PORT))

def _tsa_start(target):
    """TSA-Knopf: ./tsanalyse.py <Stream-IP> <Stream-UDP-Port> im Verzeichnis TSA_DIR starten -
    mit dem Videostream (UDP) dieses Ziels; danach eigener Fernsteuerport per CTRLPORT."""
    label = _TARGET_LABEL.get(target, target)
    get_stream = _TARGET_STREAM.get(target)
    _ep = get_stream() if get_stream else None
    if not _ep:
        olog("%s: TSA nicht moeglich - kein UDP-Videostream bekannt." % label)
        return
    ip, port = _ep
    prog = os.path.join(TSA_DIR, "tsanalyse.py")
    if not os.path.isfile(prog):
        olog("%s: TSA - %s nicht gefunden." % (label, prog))
        return
    old = _tsa_procs.get(target)
    if old is not None and old.poll() is None:
        olog("%s: TSA laeuft schon (PID %d)." % (label, old.pid))
        return
    cmd = ([prog] if os.access(prog, os.X_OK) else [sys.executable or "python3", prog]) + [str(ip), str(port),
                                                                                 "--ctrl-port", str(_tsa_ctrl_port(target))]
    print("TSA-Aufruf (%s): cd %s && %s" % (label, TSA_DIR, " ".join(shlex.quote(c) for c in
          (["./tsanalyse.py"] + cmd[(1 if os.access(prog, os.X_OK) else 2):]))), flush=True)
    try:
        _tsa_procs[target] = subprocess.Popen(cmd, cwd=TSA_DIR, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              start_new_session=True)
        olog("%s: TSA gestartet: %s %s %s (Fernsteuerport %d)" % (label, "./tsanalyse.py", ip, port,
                                                                  _tsa_ctrl_port(target)))
    except OSError as e:
        olog("%s: TSA-Start fehlgeschlagen: %s" % (label, e))
        return
    threading.Thread(target=_tsa_setup_ctrl_port, args=(target,), daemon=True).start()

_SERVICE_NAME_RE = re.compile(r'^\s*Service:\s*"([^"]*)"', re.MULTILINE)
_OCTAGON_CALL_RE = re.compile(r'^Call:\s*(.*)$', re.MULTILINE)

def _octagon_service_name():
    """Liest per SSH+dvbsnoop auf der Octagon-Box (openATV) das aktuelle Rufzeichen (Service-Name
    aus der SDT, Tabellen-ID 0x42, PID 0x11) aus - tsduck ueber den HTTP-Stream liefert bei
    openATV keine Provider-/Service-Info mehr, dvbsnoop direkt auf der Box aber schon."""
    ssh = shutil.which("ssh")
    if not ssh:
        return None
    host = cliargs.octagon_ip
    user = cliargs.octagon_ssh_user
    remote_cmd = ('dvbsnoop -n 1 -nph -f 0x42 -m 0xff 0x11 | '
                  'sed -n \'s/.*service_provider_name: "\\(.*\\)".*/Provider: \\1/p; '
                  's/.*[Ss]ervice_name: "\\(.*\\)".*/Call:     \\1/p\'')
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), remote_cmd]
    dlog("Octagon Rufzeichen-Kommando: %s" % argv)
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
    except (subprocess.TimeoutExpired, OSError) as e:
        dlog("Octagon dvbsnoop-Aufruf fehlgeschlagen (%s@%s): %s" % (user, host, e))
        return None
    out = result.stdout.decode(errors="replace")
    m = _OCTAGON_CALL_RE.search(out)
    if not m or not m.group(1).strip():
        dlog("Octagon: kein Rufzeichen gefunden (%s@%s) - Rohausgabe: %s" % (user, host, out[:500]))
        return None
    return m.group(1).strip()

_target_video_info = {}   # target -> {"provider":, "codec":, "resolution":} - fuer Signal-Log

_FFPROBE_SHOW_ENTRIES = ("program_stream=codec_type,codec_name,width,height:"
                         "program_tags=service_name,service_provider")

def _parse_ffprobe_json(raw):
    """Wertet die -of json-Ausgabe eines ffprobe-Aufrufs (siehe _FFPROBE_SHOW_ENTRIES) aus:
    Service-Name/-Provider (SDT-Tags, bei DATV/Amateurfunk das Rufzeichen), Codec und
    Bildaufloesung des ersten Videostreams. Gibt ein dict zurueck (fehlende Werte als None),
    oder None, wenn raw kein gueltiges JSON ist."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None

    programs = data.get("programs") or []
    if programs:
        prog = programs[0]
        streams = prog.get("streams") or []
        tags = prog.get("tags") or {}
    else:
        streams = data.get("streams") or []
        tags = {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    resolution = ("%sx%s" % (video["width"], video["height"])) if video and video.get("width") else None

    return {
        "service_name":     (tags.get("service_name") or None),
        "service_provider": (tags.get("service_provider") or None),
        "codec":            (video.get("codec_name") if video else None),
        "audio_codec":      (audio.get("codec_name") if audio else None),
        "resolution":       resolution,
    }

def _ffprobe_probe(url, timeout=20):
    """Liest per ffprobe (ein einziger lokaler Aufruf, hier auf tesla) aus dem Stream:
    Service-Name/-Provider, Codec sowie Bildaufloesung des ersten Videostreams - analog zum
    vom Nutzer verwendeten stream_probe.sh. None, wenn ffprobe fehlt oder der Stream nicht
    lesbar war."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    argv = [ffprobe, "-v", "quiet", "-analyzeduration", "5000000", "-probesize", "1000000",
            "-show_entries", _FFPROBE_SHOW_ENTRIES, "-of", "json", url]
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        dlog("ffprobe-Aufruf fehlgeschlagen (%s): %s" % (url, e))
        return None
    info = _parse_ffprobe_json(result.stdout.decode(errors="replace"))
    if info is None:
        dlog("ffprobe: Antwort nicht als JSON lesbar (%s)" % url)
    return info

def _octagon_video_info():
    """Fragt Aufloesung/Codec fuer Octagon per ffprobe DIREKT AUF DER BOX ab (per SSH, lokal
    ueber 127.0.0.1 auf den gerade aktiven Service, per current_service() ermittelt) - ffprobe
    ist auf der Box bestaetigt vorhanden/funktionsfaehig, das ist zuverlaessiger als der Zugriff
    von tesla aus ueber das Netz auf die zuletzt gezappte Stream-URL."""
    ssh = shutil.which("ssh")
    if not ssh:
        return None
    ref, _name, _src = current_service(cliargs.box)
    if not ref:
        return None
    host = cliargs.octagon_ip
    user = cliargs.octagon_ssh_user
    url = "http://127.0.0.1:%s/%s" % (cliargs.octagon_stream_port, ref)
    remote_cmd = ("ffprobe -v quiet -analyzeduration 5000000 -probesize 1000000 "
                  "-show_entries \"%s\" -of json '%s'" % (_FFPROBE_SHOW_ENTRIES, url))
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), remote_cmd]
    dlog("Octagon ffprobe-Kommando: %s" % argv)
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
    except (subprocess.TimeoutExpired, OSError) as e:
        dlog("Octagon ffprobe-Aufruf fehlgeschlagen (%s@%s): %s" % (user, host, e))
        return None
    info = _parse_ffprobe_json(result.stdout.decode(errors="replace"))
    if info is None:
        dlog("Octagon ffprobe: Antwort nicht als JSON lesbar - Rohausgabe: %s" %
             result.stdout.decode(errors="replace")[:500])
    return info

def _tsduck_service_name(stream_ip, stream_port):
    """Liest per tsduck (tsp + tables-Plugin) kurz aus dem Multicast-Videostream die SDT und gibt
    den darin enthaltenen Service-Namen zurueck (bei DATV/Amateurfunk ueblicherweise das Rufzeichen
    des Senders, z.B. HB9IIU) - oder None, wenn nichts gefunden wurde/tsduck fehlt."""
    tsp = shutil.which("tsp")
    if not tsp:
        return None
    argv = [tsp, "-I", "ip", "%s:%s" % (stream_ip, stream_port),
            "-P", "until", "--seconds", "3",
            "-P", "tables",
            "-O", "drop"]
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
    except (subprocess.TimeoutExpired, OSError) as e:
        dlog("tsduck-Aufruf fehlgeschlagen (%s:%s): %s" % (stream_ip, stream_port, e))
        return None
    out = result.stdout.decode(errors="replace")
    m = _SERVICE_NAME_RE.search(out)
    if not m:
        dlog("tsduck: kein Service-Name gefunden (%s:%s) - Rohausgabe: %s" %
             (stream_ip, stream_port, out[:500]))
        return None
    return m.group(1).strip()

_target_callsign_label = {}  # target -> tk.Label (Knopf-Optik), nur fuer longmynd/longmynd_p4

_SERVICE_HISTORY_MAX = 8
_service_history = []          # zuletzt erkannte Rufzeichen/Service-Namen, chronologisch, ohne
                                # direkt aufeinanderfolgende Wiederholungen - zielunabhaengig
_service_history_lock = threading.Lock()
_service_history_var = None    # tk.StringVar fuer die Service-Verlauf-Zeile, siehe GUI-Aufbau
_bake_deviation_var  = None    # tk.StringVar fuer die Bake-Abweichung/LO-Zeile, siehe GUI-Aufbau
_service_history_line1 = "Service-Verlauf:"
_service_history_line2 = "Bake-Abweichung:"

def _service_box_refresh():
    """Aktualisiert die beiden eigenen Zeilen (Service-Verlauf, Bake-Abweichung/LO) ganz unten
    im Fenster - je eine volle Zeile, kein Umbruch mehr wie vorher im schmalen Feld."""
    if _service_history_var is not None:
        _service_history_var.set(_service_history_line1)
    if _bake_deviation_var is not None:
        _bake_deviation_var.set(_service_history_line2)

def _service_history_add(name):
    """Haengt ein NEU erkanntes Rufzeichen an die (kurze) gemeinsame Historie an - ueber alle
    Ziele hinweg. Bereits vorhandene Rufzeichen werden nicht erneut angehaengt (kein Nachrutschen
    ans Ende), damit im Verlauf wirklich nur einmal je Rufzeichen auftaucht und nicht bei jedem
    erneuten Empfang wieder nach vorn wandert."""
    global _service_history_line1
    with _service_history_lock:
        if name not in _service_history:
            _service_history.append(name)
            del _service_history[:-_SERVICE_HISTORY_MAX]
            _service_history_line1 = "Service-Verlauf:  " + ", ".join(_service_history)
        else:
            return
    root.after(0, _service_box_refresh)

_lo_autocorrect_baseline = None   # lnblo-Wert (MHz) zum Zeitpunkt des Einschaltens von "LO korrigieren"

def _lo_autocorrect_toggled():
    """Callback der 'LO korrigieren'-Checkbox: beim Einschalten den aktuellen LNB-LO-Wert als
    Basis merken, ab der die laufende Korrektur (siehe _update_beacon_deviation) rechnet."""
    global _lo_autocorrect_baseline
    if lo_autocorrect.get():
        _lo_autocorrect_baseline = float(lnblo.get())
        olog("LO-Korrektur aktiviert (Basis: %.6f MHz) - LNB LO wird anhand der Bake-Abweichung "
             "dauerhaft nachgefuehrt." % _lo_autocorrect_baseline)
    else:
        olog("LO-Korrektur deaktiviert - LNB LO bleibt beim zuletzt gesetzten Wert (%.6f MHz)." % float(lnblo.get()))

#----- Live-Bake-Erkennung/LO-Korrektur ueber die rohen Longmynd-Status-UDP-Pakete -----------
# Protokoll (siehe Longmynd-Doku): Zeilen der Form "$n,m" - n=Status-ID, m=Wert. Wichtig hier:
# ID 6 = Carrier Frequency (kHz, beim Lock die tatsaechlich gemessene IF), ID 14 = Service-Name
# (bei der Bake exakt "A71A"). Das ist derselbe UDP-Port, auf dem auch auswertung_4002.sh/_4003.sh
# lauschen (nc -lu) - beides gleichzeitig auf demselben Port geht nicht (nur ein UDP-Empfaenger
# je Port), daher NICHT gleichzeitig mit -longmyndterminal fuer denselben Empfaenger nutzen.
_LM_STATUS_LINE_RE = re.compile(r'^\$?(\d+),(.*)$')
_lm_status_lock = threading.Lock()
_lm_status_vals = {"longmynd": {}, "longmynd2": {}, "longmynd_p4": {}}   # target -> {status_id(int): value(str)}
_lm_last_packet_time = {"longmynd": 0.0, "longmynd2": 0.0, "longmynd_p4": 0.0}   # target -> time.time() des letzten Pakets

def _local_ips():
    ips = {"127.0.0.1"}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        so.connect(("192.168.0.1", 9))
        ips.add(so.getsockname()[0])
        so.close()
    except OSError:
        pass
    return ips

def _lm_expected_senders(target):
    """Von welchen IP-Adressen Statuspakete dieses Tuners kommen duerfen (None = alle).
    Mehrere longmynd koennen versehentlich auf denselben Status-Port senden (z.B. LongMynd 2 lokal und
    LongMynd P4 beide auf 4003) - dann mischten sich die Werte, der Tuner schien 'von aussen umgestimmt'."""
    try:
        e = next((t for t in (app_config.get("tuners") or []) if isinstance(t, dict) and t.get("typ") == target), {})
        if target == "longmynd2":
            if (e.get("modus") or "lokal") == "lokal":
                return _local_ips()
            ip = e.get("extern_ip")
        else:
            ip = e.get("ip")
        if ip and ip not in ("127.0.0.1", "localhost"):
            return {socket.gethostbyname(ip)}
        if ip:
            return _local_ips()
    except OSError:
        pass
    return None

def _lm_status_listener(target, port, forward_port=None):
    """Laeuft als Daemon-Thread: empfaengt roh die Longmynd-Status-UDP-Pakete und pflegt die
    zuletzt bekannten Werte je Status-ID fuer dieses Ziel. Bindet den echten Port EXKLUSIV (ein
    zweiter Konsument auf demselben Port - z.B. per Hand gestartete auswertung_*.sh - wuerde dem
    zuletzt gebundenen Socket alle kuenftigen Pakete abspenstig machen und den anderen Socket
    dauerhaft leer lassen). Spiegelt deshalb jedes empfangene Paket unveraendert per UDP an
    127.0.0.1:<forward_port> weiter - auswertung_4002.sh/_4003.sh mit '-port <forward_port>'
    darauf zeigen lassen, damit Dashboard und GUI parallel funktionieren, ohne um den Port zu
    konkurrieren."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        olog("Longmynd-Status-UDP (%s, Port %d) konnte nicht geoeffnet werden (%s) - laeuft "
             "evtl. schon -longmyndterminal/auswertung_*.sh auf diesem Port? Live-Bake-Erkennung "
             "fuer %s bleibt ohne Daten." % (target, port, e, target))
        return
    fwd_sock = None
    if forward_port:
        fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    olog("Longmynd-Status-UDP (%s) auf Port %d gestartet - wartet auf Live-Statuspakete "
         "(Carrier/Service) fuer die Bake-Erkennung.%s"
         % (target, port, (" Spiegelung nach 127.0.0.1:%d fuer auswertung_*.sh (-port %d)."
                            % (forward_port, forward_port)) if forward_port else ""))
    first_packet_seen = False
    buf = ""
    expected = _lm_expected_senders(target)
    exp_t = time.time()
    foreign_logged = set()
    while True:
        try:
            data, _addr = sock.recvfrom(4096)
        except OSError:
            continue
        if time.time() - exp_t > 60:
            expected, exp_t = _lm_expected_senders(target), time.time()     # Modus lokal/extern kann wechseln
        if expected and _addr[0] not in expected:
            if _addr[0] not in foreign_logged:
                foreign_logged.add(_addr[0])
                olog("ACHTUNG %s: Statuspakete auf Port %d kommen auch von %s (erwartet: %s) - dort sendet ein "
                     "anderer longmynd (z.B. LongMynd P4) auf denselben Port. Diese Pakete werden ignoriert; "
                     "bitte dort einen eigenen Status-Port (-I ... <port>) einstellen." % (
                         _TARGET_LABEL.get(target, target), port, _addr[0], ", ".join(sorted(expected))))
            continue
        if not first_packet_seen:
            first_packet_seen = True
            olog("Longmynd-Status-UDP (%s): erstes Datenpaket empfangen - Live-Bake-Erkennung "
                 "laeuft." % target)
        _lm_last_packet_time[target] = time.time()
        if fwd_sock is not None:
            try:
                fwd_sock.sendto(data, ("127.0.0.1", forward_port))
            except OSError:
                pass
        buf += data.decode(errors="replace")
        while True:
            idxs = [i for i in (buf.find("\r"), buf.find("\n")) if i != -1]
            if not idxs:
                break
            cut = min(idxs)
            line, buf = buf[:cut].strip(), buf[cut + 1:]
            if not line:
                continue
            m = _LM_STATUS_LINE_RE.match(line)
            if not m:
                continue
            with _lm_status_lock:
                _lm_status_vals[target][int(m.group(1))] = m.group(2)

_MODCOD_NAMES_DVBS = {0: "QPSK 1/2", 1: "QPSK 2/3", 2: "QPSK 3/4", 3: "QPSK 5/6",
                      4: "QPSK 6/7", 5: "QPSK 7/8"}
_MODCOD_NAMES_DVBS2 = {0: "DummyPL", 1: "QPSK 1/4", 2: "QPSK 1/3", 3: "QPSK 2/5", 4: "QPSK 1/2",
                       5: "QPSK 3/5", 6: "QPSK 2/3", 7: "QPSK 3/4", 8: "QPSK 4/5", 9: "QPSK 5/6",
                       10: "QPSK 8/9", 11: "QPSK 9/10", 12: "8PSK 3/5", 13: "8PSK 2/3",
                       14: "8PSK 3/4", 15: "8PSK 5/6", 16: "8PSK 8/9", 17: "8PSK 9/10",
                       18: "16APSK 2/3", 19: "16APSK 3/4", 20: "16APSK 4/5", 21: "16APSK 5/6",
                       22: "16APSK 8/9", 23: "16APSK 9/10", 24: "32APSK 3/4", 25: "32APSK 4/5",
                       26: "32APSK 5/6", 27: "32APSK 8/9", 28: "32APSK 9/10"}

_lm_status_chips = {}   # target -> {"udp": Label, "state": Label, "freq": Label}
_octagon_status_chips = {}   # {"state": Label, "mer": Label} - per HTTP-Poll befuellt, siehe _octagon_status_tick()


#===== Octagon-Wiederherstellung: Box nicht erreichbar -> Ping pruefen; Ping ok = Receiver laeuft,   =====
#===== dann Enigma2-GUI (openATV) pruefen/starten/neu starten; hilft das nicht -> kompletter Reboot. =====
#===== Abschaltbar: app_config["octagon_recovery"] = false (nur Reboot: "octagon_recovery_reboot").  =====
def _ping_host(host, timeout_s=2):
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(timeout_s), host], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout_s + 3)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def _octagon_ssh(cmd, timeout=25):
    """(rc, Ausgabe) eines Kommandos auf der Box per SSH; rc None bei Fehler/Timeout."""
    ssh = shutil.which("ssh")
    if not ssh:
        return None, "ssh fehlt"
    argv = [ssh, "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "%s@%s" % (cliargs.octagon_ssh_user, cliargs.octagon_ip), cmd]
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return r.returncode, r.stdout.decode(errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)

# ===== Octagon-Direktmodus: Box ohne Enigma frei abstimmen (dvb_direct.py auf der Box) =========
_oct_direct_running = [False]
_oct_direct_status = [None]      # zuletzt empfangene STATUS-Antwort als dict

def _oct_direct_cli_apply():
    """-octagonDirektQRG schaltet den Direktmodus ein, unabhaengig davon, was zuletzt gespeichert war."""
    if getattr(cliargs, "octagon_direkt_qrg", False) and not app_config.get("octagon_direct"):
        app_config["octagon_direct"] = True
        app_config_save()

def _oct_direct():
    """True, wenn der Direktmodus eingeschaltet ist (dann laeuft auf der Box kein Enigma, sondern
    dvb_direct.py; Umschalten geht ueber den UDP-Fernsteuerport statt ueber die Kanalliste)."""
    return bool(app_config.get("octagon_direct"))

def _oct_direct_stream():
    """(ip, port) des UDP-Streams, den dvb_direct.py auf der Box ausgibt."""
    txt = str(app_config.get("octagon_direct_udp") or "230.0.0.9:1234")
    ip, _, port = txt.rpartition(":")
    try:
        return ip, int(port)
    except ValueError:
        return "230.0.0.9", 1234

def _oct_direct_send(cmd, timeout=6.0):
    """Ein Kommando an dvb_direct.py auf der Box schicken und auf die Antwort warten.
    Laeuft im aufrufenden Thread - niemals direkt aus dem GUI-Thread aufrufen."""
    port = int(app_config.get("octagon_direct_ctrl_port", 55600))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(cmd.encode("utf-8"), (cliargs.octagon_ip, port))
        data, _ = s.recvfrom(2048)
        return data.decode("utf-8", "replace").strip()
    except OSError as e:
        dlog("Octagon-Direkt '%s': %s" % (cmd, e))
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass

def _oct_direct_copy_script():
    """Aktuelle dvb_direct.py neben der GUI auf die Box kopieren (damit dort nie eine alte Fassung
    liegt). Ohne scp wird stillschweigend die vorhandene Fassung auf der Box benutzt."""
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dvb_direct.py")
    scp = shutil.which("scp")
    if not (scp and os.path.isfile(local)):
        return False
    dest = str(app_config.get("octagon_direct_script", "/tmp/dvb_direct.py"))
    argv = [scp, "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            local, "%s@%s:%s" % (cliargs.octagon_ssh_user, cliargs.octagon_ip, dest)]
    try:
        return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def _oct_direct_player_on():
    """Hauptthread: im Direktmodus ist Octagon ein normaler UDP-Empfaenger - Player starten und wie die
    anderen ueberwachen (Player-Ueberwachung, AutoClose, Bild-Waechter, Kacheln)."""
    _player_wanted["octagon"] = True
    _player_active["octagon"] = _player_active.get("octagon") or "vlc"
    if not _autoclose_player_running("octagon"):
        olog("Octagon-Direktmodus: Player fuer udp://@%s:%d wird gestartet." % _oct_direct_stream())
        _start_player("octagon", _player_active["octagon"])

def _oct_direct_start():
    """Enigma auf der Box beenden und dvb_direct.py als Dauerlaeufer starten. Hintergrund-Thread."""
    ip, port = _oct_direct_stream()
    script = str(app_config.get("octagon_direct_script", "/tmp/dvb_direct.py"))
    if _oct_direct_send("PING", timeout=2.0) == "OK dvb_direct":
        _oct_direct_running[0] = True
        root.after(0, olog, "Octagon-Direktmodus: dvb_direct.py laeuft bereits auf der Box.")
        root.after(1500, _oct_direct_player_on)
        return True
    root.after(0, olog, "Octagon-Direktmodus: starte dvb_direct.py auf der Box ...")
    _oct_direct_copy_script()
    cmd = ("nohup python3 %s --stop-enigma --minimal -f %d --lo %s --pol %s --udp %s:%d --ctrl-port %d "
           ">/tmp/dvb_direct.log 2>&1 &") % (
        script, int(app_config.get("octagon_direct_frontend", 0)),
        app_config.get("octagon_direct_lo", 9540.0), app_config.get("octagon_direct_pol", "h"),
        ip, port, int(app_config.get("octagon_direct_ctrl_port", 55600)))
    _octagon_ssh(cmd, timeout=20)
    for _ in range(20):
        time.sleep(1.0)
        if _oct_direct_send("PING", timeout=2.0) == "OK dvb_direct":
            _oct_direct_running[0] = True
            root.after(0, olog, "Octagon-Direktmodus aktiv - Stream udp://@%s:%d" % (ip, port))
            root.after(1500, _oct_direct_player_on)
            return True
    root.after(0, olog, "Octagon-Direktmodus: dvb_direct.py antwortet nicht (Log auf der Box: /tmp/dvb_direct.log)")
    return False

def _oct_direct_stop():
    """dvb_direct.py beenden und Enigma wieder starten. Hintergrund-Thread."""
    _oct_direct_send("STOP", timeout=3.0)
    _oct_direct_running[0] = False
    _octagon_ssh("init 3", timeout=15)
    root.after(0, olog, "Octagon-Direktmodus aus - Enigma wird wieder gestartet.")

def _oct_direct_toggle():
    """Knopf in der Octagon-Spalte: zwischen Normalbetrieb (Enigma + Kanalliste) und Direktmodus
    (Enigma aus, freie Frequenz) umschalten."""
    new = bool(_oct_direct_var_ref[0].get()) if _oct_direct_var_ref[0] is not None else not _oct_direct()
    app_config["octagon_direct"] = new
    app_config_save()
    _oct_direct_button_update()
    if new:
        _octagon_wait_begin()      # sofort Status abfragen, bis zur Antwort "warte auf Rueckmeldung"
    def work():
        if new:
            _oct_direct_start()
            f = _target_cur_freq.get("octagon"); sr = _target_cur_sr.get("octagon")
            if f and sr:
                _oct_direct_tune(f, sr)
        else:
            _oct_direct_stop()
        root.after(0, _oct_direct_button_update)
        root.after(0, _octagon_status_kick)
    threading.Thread(target=work, daemon=True).start()

_oct_direct_btn = [None]        # Checkbutton oben neben AScan
_oct_direct_var_ref = [None]    # dessen BooleanVar

def _oct_direct_button_update():
    """Haken und Beschriftung nachfuehren: "Direkt" = aus, "Direkt *" = eingeschaltet, aber
    dvb_direct.py antwortet (noch) nicht, "Direkt ok" = laeuft."""
    b = _oct_direct_btn[0]
    if b is None:
        return
    on = _oct_direct()
    if _oct_direct_var_ref[0] is not None and bool(_oct_direct_var_ref[0].get()) != on:
        _oct_direct_var_ref[0].set(on)
    b.config(text="Direkt ok" if (on and _oct_direct_running[0]) else ("Direkt *" if on else "Direkt"))

def _oct_player_sync(stage):
    """Hauptthread, nach dem Abstimmen im Octagon-Direktmodus. Stufe 1: Player hart neu synchronisieren
    (VLC: stop/play; lief er nicht, aber ist gewollt: neu starten). Stufe 2 (einige Sekunden spaeter):
    VLC nochmals 'play' (harmlos, wenn er schon spielt) bzw. fehlenden Player starten."""
    if not _oct_direct():
        return
    player = _player_active.get("octagon")
    if player not in ("vlc", "mpv", "ffplay"):
        return
    wanted = bool(_player_wanted.get("octagon"))
    if player == "vlc":
        port = _player_telnet_port("octagon")
        alive = port is not None and _is_port_open("127.0.0.1", port)
    else:
        proc = _player_proc.get("octagon")
        alive = proc is not None and proc.poll() is None
    _odbg("Player-Sync Stufe %d: Player=%s, laeuft=%s, gewollt=%s" % (stage, player, alive, wanted))
    if not alive:
        if wanted:
            _player_restart("octagon", player, "Octagon-Direkt: Player lief nach dem Abstimmen nicht")
        return
    if stage == 1:
        _vlc_resync("octagon", "Octagon-Direkt: neu abgestimmt", hard=True)
    elif player == "vlc":
        _vlc_telnet_send(_player_telnet_port("octagon"), "play", "Octagon")

def _oct_direct_tune(freq, sr):
    """Frequenz/Symbolrate an dvb_direct.py schicken. Hintergrund-Thread."""
    _octagon_wait[0] = time.time()      # waehrend des Abstimmens (bis ~20 s) antwortet STATUS nicht
    if not _oct_direct_running[0] and not _oct_direct_start():
        return False
    pol = app_config.get("octagon_direct_pol", "h")
    _cmd = "TUNE %.3f %.0f %s" % (freq + _lo_offset_get("octagon") / 1000.0, sr, pol)      # Offset aus dem LO-Abgleich (Menue LO-Abgleich)
    _odbg("=== Octagon-Direkt: sende '%s' an %s:%s (LO laut Konfig %s MHz, Ziel-ZF %.0f kHz) | Status vorher: %s" % (
        _cmd, cliargs.octagon_ip, app_config.get("octagon_direct_ctrl_port", 55600),
        app_config.get("octagon_direct_lo"), (freq - float(app_config.get("octagon_direct_lo") or 0)) * 1000.0,
        _oct_direct_send("STATUS2", timeout=2.0)))
    _t0 = time.time()
    res = _oct_direct_send(_cmd, timeout=20.0)
    _odbg("Antwort nach %.1f s: %r" % (time.time() - _t0, res))
    if res is None:
        _oct_direct_running[0] = False
        root.after(0, olog, "Octagon-Direkt: keine Antwort auf TUNE %.3f MHz" % freq)
        root.after(0, _oct_direct_button_update)
        return False
    root.after(0, olog, "Octagon-Direkt: %s" % res)
    root.after(0, _octagon_status_kick)
    _callsign_block_until["octagon"] = time.time() + 4.0    # Stream liegt jetzt am neuen Kanal an
    def _sync():
        try:
            t0 = time.time()
            locked = res.rstrip().endswith("LOCK=1")
            while not locked and time.time() - t0 < 20.0:
                time.sleep(1.5)
                st = _oct_direct_send("STATUS2", timeout=3.0)
                locked = bool(st and "LOCK=1" in st)
            _odbg("Player-Sync: Lock=%s nach %.1f s - Player wird neu synchronisiert" % (locked, time.time() - t0))
            time.sleep(1.5)
            root.after(0, _oct_player_sync, 1)
            time.sleep(5.0)
            root.after(0, _oct_player_sync, 2)
        except Exception as _e:
            _odbg("Player-Sync Fehler: %r" % (_e,))
    threading.Thread(target=_sync, daemon=True).start()
    def _follow():
        _tt = time.time()
        for _w in (2.0, 5.0, 10.0, 20.0, 40.0):
            time.sleep(max(0.0, _w - (time.time() - _tt)))
            _odbg("  %2.0f s nach TUNE %.3f MHz / %.0f kS/s: %s" % (time.time() - _tt, freq, sr,
                                                                    _oct_direct_send("STATUS2", timeout=3.0)))
    threading.Thread(target=_follow, daemon=True).start()
    return res.startswith("OK")

def _octagon_recovery_loop():
    host = cliargs.octagon_ip
    def say(msg):
        print("Octagon-Wiederherstellung: " + msg, flush=True)
        root.after(0, olog, "Octagon: " + msg)
    down_since = None
    last_action = 0.0
    gui_tries = 0
    last_reboot = 0.0
    nopings = 0
    time.sleep(20)
    while True:
        time.sleep(10)
        try:
            if not app_config.get("octagon_recovery", True):
                continue
            if _oct_direct():
                continue     # im Direktmodus ist Enigma absichtlich aus - nicht "wiederherstellen"
            if _octagon_reachable:
                if down_since is not None:
                    say("Box wieder erreichbar.")
                down_since = None; gui_tries = 0; nopings = 0
                continue
            now = time.time()
            if down_since is None:
                down_since = now
            if now - last_action < 90.0 or now - last_reboot < 180.0:
                continue                      # nach einer Aktion Zeit zum (Neu-)Starten lassen
            if now - down_since < 40.0:
                continue                      # kurze Aussetzer abwarten
            if not _ping_host(host):
                nopings += 1
                if nopings in (1, 30):
                    say("Ping %s ohne Antwort - Receiver aus oder startet noch? (Strom/Netzwerk pruefen)" % host)
                continue
            nopings = 0
            # Ping ok: Receiver laeuft. Laeuft die Enigma2-GUI?
            rc, out = _octagon_ssh("pidof enigma2")
            if rc is None or (rc == 255):
                gui_tries += 1
                say("Ping ok, aber SSH nicht moeglich (%s) - GUI-Start nicht pruefbar (Versuch %d)." % (out[:80], gui_tries))
                last_action = now
            elif rc != 0 or not out:
                gui_tries += 1
                say("Ping ok, Enigma2-GUI laeuft NICHT - starte GUI (Versuch %d)." % gui_tries)
                _octagon_ssh("systemctl start enigma2 2>/dev/null || init 3", timeout=30)
                last_action = now
            else:
                gui_tries += 1
                say("Ping ok, Enigma2 laeuft, antwortet aber nicht per Web - GUI-Neustart (Versuch %d)." % gui_tries)
                _octagon_ssh("(init 4; sleep 3; init 3) >/dev/null 2>&1 &", timeout=15)
                last_action = now
            if gui_tries >= 3 and app_config.get("octagon_recovery_reboot", True) and now - last_reboot > 1800.0:
                say("GUI-Neustart hilft nicht - Receiver wird komplett neu gestartet (reboot).")
                _octagon_ssh("reboot", timeout=10)
                last_reboot = now
                gui_tries = 0
                down_since = now
        except Exception as e:
            dlog("Octagon-Wiederherstellung: %r" % (e,))


#===== Statistik: Auswertung der Logdateien (Menue "Statistik"), Meldung bei NEUEM Rufzeichen und     =====
#===== automatischer Snapshot, sobald das Bild steht (LongMynd: gelockt + MER > 0, sonst nach 6 s).  =====
NEW_CALL_LOG = os.path.join(parameters_path, "neue_rufzeichen.log")
_known_callsigns = set()
_auto_snap_done = {}   # (Ziel, Rufzeichen) -> Zeitpunkt des letzten Auto-Snapshots

def _read_csv_rows(path):
    """CSV-Zeilen ohne Kopfzeile; robust gegen NUL-Bytes/kaputte Zeilen (z.B. nach einem Absturz
    waehrend des Schreibens) - OSError wenn die Datei fehlt."""
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        text = f.read().replace("\0", "")
    rd = csv.reader(text.splitlines())
    next(rd, None)
    return [r for r in rd]

def _known_callsigns_load():
    """Bisher gesehene Rufzeichen aus signal_log.csv (Spalte 'Rufzeichen') - alles, was dort noch
    nicht vorkommt, gilt als NEU."""
    try:
        for row in _read_csv_rows(_SIGNAL_LOG_FILE):
            if len(row) > 3 and row[3].strip():
                _known_callsigns.add(row[3].strip().lower())
    except OSError:
        pass

def _snap_port_for(target):
    return {"longmynd": cliargs.longmynd_vnc_port, "longmynd2": cliargs.longmynd2_vnc_port,
            "longmynd_p4": cliargs.longmyndp4_vnc_port, "octagon": cliargs.vlc_telnet_port,
            "rtl_sdr": cliargs.rtlsdr_vlc_telnet_port}.get(target, _rx_telnet_port(target) if target in _UDPRX_TARGETS else None)

def _toast(text, seconds=10):
    """Kleines Hinweisfenster rechts unten (schliesst sich selbst), nimmt den Fokus nicht."""
    try:
        w = tk.Toplevel(root)
        w.title("Neues Rufzeichen")
        w.attributes("-topmost", True)
        tk.Label(w, text=text, font=("", 12, "bold"), bg="#2e9e5b", fg="white", padx=18, pady=12,
                 justify="left").pack()
        w.update_idletasks()
        x = root.winfo_screenx() if False else root.winfo_rootx()
        w.geometry("+%d+%d" % (max(0, root.winfo_rootx() + 40), max(0, root.winfo_rooty() + 40)))
        w.after(seconds * 1000, w.destroy)
    except tk.TclError:
        pass

def _new_callsign_event(target, name):
    """Hauptthread: neues, noch nie geloggtes Rufzeichen - melden, protokollieren, Snapshot planen."""
    label = _TARGET_LABEL.get(target, target)
    f = _target_cur_freq.get(target); sr = _target_cur_sr.get(target)
    msg = "NEUES RUFZEICHEN: %s  (%s, %s MHz, %s kS/s)" % (name, label, ("%.3f" % f) if f else "?", sr or "?")
    olog(msg)
    if app_config.get("new_call_notify", True):
        try:
            root.bell()
        except tk.TclError:
            pass
        _toast("Neues Rufzeichen\n%s\n%s  %s MHz" % (name, label, ("%.3f" % f) if f else "?"))
    try:
        with open(NEW_CALL_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), label, name,
                                               ("%.3f" % f) if f else "", sr or ""))
    except OSError:
        pass
    if app_config.get("auto_snapshot_new_call", True):
        threading.Thread(target=_auto_snapshot_when_stable, args=(target, name), daemon=True).start()

def _lm_picture_stable(target):
    """LongMynd-Ziele: gelockt (State >= 3) und MER > 0 - sonst (rtl_sdr/Octagon) immer True."""
    if target not in _LM_TARGETS:
        return True
    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    try:
        return int(vals.get(1)) >= 3 and float(vals.get(12)) / 10.0 > 0.0
    except (TypeError, ValueError):
        return False

def _auto_snapshot_when_stable(target, name):
    """Wartet (max. 30 s), bis das Bild 3 s lang steht, und loest dann den Snapshot aus."""
    label = _TARGET_LABEL.get(target, target)
    key = (target, name.lower())
    if time.time() - _auto_snap_done.get(key, 0.0) < 600.0:
        return
    t0 = time.time(); ok_since = None
    while time.time() - t0 < 30.0:
        if _resync_last_callsign.get(target) != name:
            return                                       # inzwischen anderes Rufzeichen
        player = _player_active.get(target)
        player_ok = (player in ("mpv", "ffplay") and _player_proc.get(target) is not None
                     and _player_proc[target].poll() is None) or \
                    (player == "vlc" and _snap_port_for(target) is not None
                     and _is_port_open("127.0.0.1", _snap_port_for(target)))
        if player_ok and _lm_picture_stable(target) and time.time() - t0 >= (3.0 if target in _LM_TARGETS else 6.0):
            ok_since = ok_since or time.time()
            if time.time() - ok_since >= 3.0:
                _auto_snap_done[key] = time.time()
                root.after(0, lambda: (olog("%s: Bild steht - automatischer Snapshot (%s)." % (label, name)),
                                       _snapshot_for_target(target, _snap_port_for(target))))
                return
        else:
            ok_since = None
        time.sleep(1.0)
    root.after(0, olog, "%s: Auto-Snapshot fuer %s abgebrochen - Bild stand nicht / Player nicht erreichbar." % (label, name))

# ---- Auswertung ------------------------------------------------------------------------------
def _stat_bar(n, mx, width=28):
    return ("#" * max(1 if n else 0, int(round(width * n / float(mx))))) if mx else ""

def _stat_num(txt):
    try:
        return float(str(txt).replace(",", ".").replace("+", ""))
    except ValueError:
        return None

def _stat_avg(v):
    return ("%.1f" % (sum(v) / len(v))) if v else "-"

def _stat_hist(counter, keys, title, label=lambda k: str(k)):
    mx = max([counter.get(k, 0) for k in keys] or [0])
    lines = [title]
    for k in keys:
        n = counter.get(k, 0)
        lines.append("  %-12s %5d  %s" % (label(k), n, _stat_bar(n, mx)))
    return lines

def _stat_hours(times):
    c = collections.Counter()
    for t in times:
        try:
            c[int(t[:2])] += 1
        except ValueError:
            pass
    return _stat_hist(c, range(24), "Nach Tageszeit (Stunde):", lambda h: "%02d-%02d Uhr" % (h, h + 1))

def _stat_days(dates, last=14):
    c = collections.Counter(dates)
    keys = sorted(c)[-last:]
    return _stat_hist(c, keys, "Pro Tag (letzte %d Tage mit Eintraegen):" % last)

def _stat_fec_ranking(fecs):
    """Rangliste NUR der FEC-Werte (z.B. 'QPSK 2/3'): Platz, FEC, Anzahl, Anteil."""
    fecs = [f.strip() for f in fecs if f and f.strip() and f.strip() != "-"]
    out = ["Haeufigste FECs (Rangliste):"]
    if not fecs:
        return out + ["  (noch keine FEC im Log - wird ab jetzt mitgeschrieben)"]
    c = collections.Counter(fecs)
    tot = float(sum(c.values()))
    for i, (f, n) in enumerate(c.most_common(), 1):
        out.append("  %2d. %-14s %6d  (%4.1f %%)" % (i, f, n, 100.0 * n / tot))
    return out

_STAT_VCODEC_NAMES = {"hevc": "H.265/HEVC", "h265": "H.265/HEVC", "h.265/hevc": "H.265/HEVC",
                      "h264": "H.264/AVC", "h.264/avc": "H.264/AVC", "avc": "H.264/AVC",
                      "vvc": "H.266/VVC", "h266": "H.266/VVC", "h.266/vvc": "H.266/VVC",
                      "mpeg2video": "MPEG-2", "mpeg-2": "MPEG-2"}

def _stat_by_symbolrate(rows):
    """Je Symbolrate (aus den Signal-Log-Zeilen MIT Symbolrate-Spalte): Meldungen, Rufzeichen und die
    haeufigsten Video-Codecs, Audio-Codecs und FECs. Zeilen aelter als diese Spalten tragen keine
    Symbolrate und fehlen hier."""
    def top(counter, k=3):
        return ", ".join("%s (%d)" % (n, c) for n, c in counter.most_common(k)) or "-"
    per = {}
    for r in rows:
        sr = r[14].strip() if len(r) > 14 else ""
        if not sr:
            continue
        d = per.setdefault(sr, {"n": 0, "calls": set(), "v": collections.Counter(),
                                "a": collections.Counter(), "f": collections.Counter()})
        d["n"] += 1
        d["calls"].add(r[3].strip())
        vc = r[11].strip().lower() if len(r) > 11 else ""
        if vc:
            d["v"][_STAT_VCODEC_NAMES.get(vc, r[11].strip())] += 1
        if len(r) > 15 and r[15].strip():
            d["a"][r[15].strip().upper()] += 1
        if len(r) > 13 and r[13].strip():
            d["f"][r[13].strip()] += 1
    out = ["Haeufigste Symbolraten mit Video-/Audio-Codec und FEC (nur Meldungen mit Symbolrate im Log):"]
    if not per:
        return out + ["  (noch keine Symbolrate im Log - wird ab jetzt mitgeschrieben)"]
    for i, (sr, d) in enumerate(sorted(per.items(), key=lambda kv: -kv[1]["n"])[:8], 1):
        out.append("  %d. %5s kS/s   %4d Meldungen, %3d Rufzeichen" % (i, sr, d["n"], len(d["calls"])))
        out.append("       Video: %s" % top(d["v"]))
        out.append("       Audio: %s" % top(d["a"]))
        out.append("       FEC:   %s" % top(d["f"], 4))
    return out

def _stat_signal_log(path=None):
    path = path or _SIGNAL_LOG_FILE
    try:
        rows = [r for r in _read_csv_rows(path) if len(r) >= 4]
    except OSError:
        return "signal_log.csv nicht gefunden:\n%s" % path
    if not rows:
        return "signal_log.csv ist leer."
    calls = {}
    tg = collections.Counter()
    for r in rows:
        name = r[3].strip()
        tg[r[2]] += 1
        if not name:
            continue
        c = calls.setdefault(name, {"n": 0, "first": r[0] + " " + r[1], "last": "", "t": collections.Counter(),
                                    "mer": [], "d": [], "f": collections.Counter(),
                                    "maxmer": None, "maxwhen": "", "maxt": "", "maxf": ""})
        c["n"] += 1; c["last"] = r[0] + " " + r[1]; c["t"][r[2]] += 1
        if len(r) > 5 and _stat_num(r[5]):
            c["mer"].append(_stat_num(r[5]))
            if c["maxmer"] is None or _stat_num(r[5]) > c["maxmer"]:
                c["maxmer"], c["maxwhen"], c["maxt"] = _stat_num(r[5]), r[0] + " " + r[1], r[2]
                c["maxf"] = r[7][:8] if len(r) > 7 else ""
        if len(r) > 6 and _stat_num(r[6]) is not None and r[6].strip(): c["d"].append(_stat_num(r[6]))
        if len(r) > 7 and r[7].strip(): c["f"][r[7][:8]] += 1
    out = ["signal_log.csv  -  %s" % path,
           "Zeitraum: %s  bis  %s" % (rows[0][0] + " " + rows[0][1], rows[-1][0] + " " + rows[-1][1]),
           "Eintraege: %d    verschiedene Rufzeichen: %d" % (len(rows), len(calls)), ""]
    out.append("Meldungen je Tuner:")
    for t, n in tg.most_common():
        out.append("  %-14s %5d" % (t, n))
    out += [""] + _stat_fec_ranking([r[13] for r in rows if len(r) > 13])
    out += [""] + _stat_by_symbolrate(rows)
    # Staerkste Signale: hoechster je gemeldeter MER pro Rufzeichen (ohne die Bake A71A, die als
    # Referenzsignal immer vorn laege); Ø = Mittel aller MER-Werte dieses Rufzeichens im Log
    strong = [(n, c) for n, c in calls.items() if c["maxmer"] is not None and n.strip().upper() != "A71A"]
    out += ["", "Staerkste Signale nach MER (Top 10, ohne Bake A71A):",
            "  %2s  %-22s %7s %7s %5s  %-19s %-14s %s" % ("", "Rufzeichen", "max MER", "O MER", "Anz.", "Zeitpunkt max", "Tuner", "Frequenz")]
    for i, (n, c) in enumerate(sorted(strong, key=lambda kv: -kv[1]["maxmer"])[:10], 1):
        out.append("  %2d. %-22s %5.1f dB %7s %5d  %-19s %-14s %s" % (
            i, n[:22], c["maxmer"], _stat_avg(c["mer"]), len(c["mer"]), c["maxwhen"], c["maxt"][:14], c["maxf"]))
    if not strong:
        out.append("  (noch keine MER-Werte im Log)")
    # robuster gegen Ausreisser: beste DURCHSCHNITTS-MER, nur Rufzeichen mit mindestens 5 Meldungen
    steady = [(n, c) for n, c in strong if len(c["mer"]) >= 5]
    out += ["", "Beste Durchschnitts-MER (mind. 5 Meldungen, ohne Bake A71A):"]
    for i, (n, c) in enumerate(sorted(steady, key=lambda kv: -(sum(kv[1]["mer"]) / len(kv[1]["mer"])))[:10], 1):
        out.append("  %2d. %-22s O %4.1f dB   max %4.1f dB   %5d Meldungen   %s" % (
            i, n[:22], sum(c["mer"]) / len(c["mer"]), c["maxmer"], len(c["mer"]), ", ".join(sorted(c["t"]))[:30]))
    if not steady:
        out.append("  (noch zu wenige Meldungen)")
    out += ["", "Haeufigste Rufzeichen (Top 20):",
            "  %-22s %5s  %-19s %-19s %6s %6s %6s  %s" % ("Rufzeichen", "Anz.", "erstmals", "zuletzt", "O MER", "max", "O D", "Tuner / Frequenzen")]
    for name, c in sorted(calls.items(), key=lambda kv: -kv[1]["n"])[:20]:
        fr = ", ".join(f for f, _n in c["f"].most_common(2))
        out.append("  %-22s %5d  %-19s %-19s %6s %6s %6s  %s | %s" % (
            name[:22], c["n"], c["first"], c["last"], _stat_avg(c["mer"]),
            ("%.1f" % max(c["mer"])) if c["mer"] else "-", _stat_avg(c["d"]),
            ",".join(sorted(c["t"])), fr))
    firsts = collections.Counter(c["first"][:10] for c in calls.values())
    out += [""] + _stat_days([r[0] for r in rows]) + [""] + _stat_hist(firsts, sorted(firsts)[-14:], "Neue Rufzeichen pro Tag (Erstsichtung):") + [""] + _stat_hours([r[1] for r in rows])
    return "\n".join(out)

def _stat_parse_dt(d, t):
    try:
        return datetime.datetime.strptime(d + " " + t, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def _stat_spektrum(path=None):
    path = path or SPECTRUM_SIGNAL_LOG
    try:
        rows = [r for r in _read_csv_rows(path) if len(r) >= 7]
    except OSError:
        return "spektrum_signale.csv nicht gefunden:\n%s" % path
    if not rows:
        return "spektrum_signale.csv ist leer."
    calls = {}; srs = collections.Counter(); freqs = collections.Counter(); open_n = 0; nocall = 0
    total = datetime.timedelta(0)
    for r in rows:
        a = _stat_parse_dt(r[0], r[1]); b = _stat_parse_dt(r[2], r[3]) if r[2] else None
        dur = (b - a) if (a and b and b >= a) else None
        if not b: open_n += 1
        name = r[4].strip()
        srs[r[6]] += 1; freqs[r[5][:8]] += 1
        if dur: total += dur
        if not name:
            nocall += 1; continue
        c = calls.setdefault(name, {"n": 0, "dur": datetime.timedelta(0), "max": datetime.timedelta(0), "f": collections.Counter(), "sr": collections.Counter()})
        c["n"] += 1; c["f"][r[5][:8]] += 1; c["sr"][r[6]] += 1
        if dur:
            c["dur"] += dur; c["max"] = max(c["max"], dur)
    out = ["spektrum_signale.csv  -  %s" % path,
           "Zeitraum: %s  bis  %s" % (rows[0][0] + " " + rows[0][1], rows[-1][0] + " " + rows[-1][1]),
           "Signale: %d   davon mit Rufzeichen: %d   ohne: %d   noch offen (kein Ende): %d" % (len(rows), len(rows) - nocall, nocall, open_n),
           "Summe der erfassten Sichtbarkeit: %s" % str(total).split(".")[0], "",
           "Rufzeichen nach Sichtbarkeitsdauer (Top 20):",
           "  %-22s %5s  %-12s %-12s  %s" % ("Rufzeichen", "Anz.", "Summe", "laengste", "Frequenzen / Symbolraten")]
    for name, c in sorted(calls.items(), key=lambda kv: -kv[1]["dur"])[:20]:
        out.append("  %-22s %5d  %-12s %-12s  %s | %s kS" % (
            name[:22], c["n"], str(c["dur"]).split(".")[0], str(c["max"]).split(".")[0],
            ", ".join(f for f, _n in c["f"].most_common(2)), ", ".join(s for s, _n in c["sr"].most_common(2))))
    out += ["", "Symbolraten:"] + ["  %6s kS/s  %5d" % (s, n) for s, n in srs.most_common(8)]
    out += [""] + _stat_fec_ranking([r[11] for r in rows if len(r) > 11])
    out += ["", "Haeufigste Frequenzen (MHz):"] + ["  %-10s %5d" % (f, n) for f, n in freqs.most_common(10)]
    out += [""] + _stat_days([r[0] for r in rows]) + [""] + _stat_hours([r[1] for r in rows])
    return "\n".join(out)

def _stat_verteiler(path=None):
    path = path or VERTEILER_LOG
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip()]
    except OSError:
        return "verteiler.log nicht gefunden:\n%s" % path
    rows = [r for r in rows if len(r) >= 4]
    if not rows:
        return "verteiler.log ist leer."
    tg = collections.Counter(r[1] for r in rows)
    fr = collections.Counter("%s kS  %s MHz" % (r[3], r[2]) for r in rows)
    out = ["verteiler.log  -  %s" % path, "Zeitraum: %s  bis  %s" % (rows[0][0], rows[-1][0]),
           "Umschaltungen: %d" % len(rows), "", "Je Tuner:"]
    out += ["  %-14s %5d" % (t, n) for t, n in tg.most_common()]
    out += ["", "Haeufigste Kanaele (Top 12):"] + ["  %-26s %5d" % (k, n) for k, n in fr.most_common(12)]
    out += [""] + _stat_days([r[0][:10] for r in rows]) + [""] + _stat_hours([r[0][11:] for r in rows])
    return "\n".join(out)

def _stat_octagon_fehler(path=None):
    path = path or OCTAGON_FEHLER_LOG
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip()]
    except OSError:
        return "octagon_fehler.log nicht gefunden:\n%s" % path
    rows = [r for r in rows if len(r) >= 4]
    if not rows:
        return "octagon_fehler.log ist leer."
    def kind(msg):
        for k in ("Fehler beim Umschalten", "Kein passender Kanal"):
            if msg.startswith(k):
                return k
        return msg[:40]
    kinds = collections.Counter(kind(r[3]) for r in rows)
    fr = collections.Counter(r[1] for r in rows)
    out = ["octagon_fehler.log  -  %s" % path, "Zeitraum: %s  bis  %s" % (rows[0][0], rows[-1][0]),
           "Fehler: %d" % len(rows), "", "Art der Fehler:"]
    out += ["  %-42s %5d" % (k, n) for k, n in kinds.most_common()]
    out += ["", "Betroffene Frequenzen (Top 10):"] + ["  %-16s %5d" % (k, n) for k, n in fr.most_common(10)]
    out += [""] + _stat_days([r[0][:10] for r in rows]) + [""] + _stat_hours([r[0][11:] for r in rows])
    return "\n".join(out)

def _stat_new_calls(path=None):
    path = path or NEW_CALL_LOG
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip()]
    except OSError:
        return "Noch keine neuen Rufzeichen gemeldet (neue_rufzeichen.log fehlt)."
    out = ["neue_rufzeichen.log  -  %d neue Rufzeichen seit Beginn dieser Funktion" % len(rows), "",
           "%-19s  %-12s %-24s %-10s %s" % ("Zeit", "Tuner", "Rufzeichen", "MHz", "kS/s")]
    for r in rows[-60:][::-1]:
        r = (r + [""] * 5)[:5]
        out.append("%-19s  %-12s %-24s %-10s %s" % (r[0], r[1], r[2][:24], r[3], r[4]))
    return "\n".join(out)

_STAT_TABS = (("Rufzeichen (signal_log)", _stat_signal_log), ("Spektrum-Signale", _stat_spektrum),
              ("Umschaltungen", _stat_verteiler), ("Octagon-Fehler", _stat_octagon_fehler),
              ("Neue Rufzeichen", _stat_new_calls))
_stat_win = {"win": None}

def _stat_dialog(select=0):
    if _stat_win["win"] is not None:
        try:
            _stat_win["win"].lift(); _stat_win["nb"].select(select); return
        except tk.TclError:
            _stat_win["win"] = None
    win = tk.Toplevel(root); win.title("Statistik der Logdateien")
    nb = ttk.Notebook(win); nb.pack(fill="both", expand=True)
    _stat_win.update(win=win, nb=nb)
    win.protocol("WM_DELETE_WINDOW", lambda: (_stat_win.update(win=None), win.destroy()))
    texts = []
    def refresh(i):
        def work():
            try:
                txt = _STAT_TABS[i][1]()
            except Exception as e:
                txt = "Fehler bei der Auswertung: %r" % (e,)
            def put():
                t = texts[i]; t.configure(state="normal"); t.delete("1.0", "end"); t.insert("end", txt); t.configure(state="disabled")
            root.after(0, put)
        threading.Thread(target=work, daemon=True).start()
    for i, (title, _fn) in enumerate(_STAT_TABS):
        fr = ttk.Frame(nb); nb.add(fr, text=title)
        top = ttk.Frame(fr); top.pack(fill="x")
        ttk.Button(top, text="Aktualisieren", command=lambda i=i: refresh(i)).pack(side="left", padx=4, pady=3)
        t = tk.Text(fr, width=130, height=36, font=("TkFixedFont", 9), wrap="none", state="disabled")
        sb = ttk.Scrollbar(fr, command=t.yview); t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); t.pack(fill="both", expand=True)
        texts.append(t); refresh(i)
    nb.select(select)

_octagon_poll_busy = [False]
_octagon_poll_result = [None]
_OCT_WAIT_S = 45.0               # solange nach Einschalten/Abstimmen "warte auf Rueckmeldung" statt "nicht erreichbar"
_octagon_wait = [time.time()]    # Beginn des Wartefensters (beim GUI-Start ebenfalls)
_octagon_tick_after = [None]     # after-Id des geplanten naechsten Status-Ticks
_octagon_fast = [False]          # True: nach der laufenden Abfrage sofort noch einmal anzeigen

def _octagon_status_kick():
    """Status der Octagon SOFORT abfragen und anzeigen (statt auf den 5-s-Takt zu warten).
    Aus jedem Thread aufrufbar."""
    _octagon_fast[0] = True
    try:
        root.after(0, _octagon_status_tick)
    except Exception:
        pass

def _octagon_wait_begin():
    """Wartefenster starten (Direkt eingeschaltet / neu abgestimmt): bis zu _OCT_WAIT_S Sekunden
    wird ohne Antwort "warte auf Rueckmeldung" (orange) gezeigt, erst danach rot "nicht erreichbar"."""
    _octagon_wait[0] = time.time()
    _octagon_status_kick()

_octagon_note = ["", 0.0]    # (Text, gueltig bis) - Hinweis im State-Chip der Octagon, z.B. "Kein Kanal 10492.755"

def _octagon_note_apply():
    chips = _octagon_status_chips
    if chips and time.time() < _octagon_note[1]:
        chips["state"].config(text=_octagon_note[0], background="#c0392b", foreground="white")

def _octagon_note_set(text, seconds=60):
    """Thread-sicher: Hinweis (rot) im State-Chip der Octagon fuer einige Sekunden anzeigen."""
    _octagon_note[0] = text
    _octagon_note[1] = time.time() + seconds
    try:
        root.after(0, _octagon_note_apply)
    except Exception:
        pass

def _octagon_stat_chips_update(stats):
    """DVBv5-Statistik (nur Direktmodus): Signal in dBm, CNR in dB, Bitfehlerrate vor (pre) und nach
    (post) der Fehlerkorrektur, nicht korrigierbare Bloecke (UNC). '-' = Treiber liefert den Wert nicht."""
    chips = _octagon_status_chips
    if "dbm" not in chips:
        return
    _u2 = chips.get("unc_row2")
    if _u2 is not None:
        unc2 = (stats or {}).get("UNC", "-")
        if unc2 in (None, "", "-"):
            _u2.pack_forget()
        else:
            if not _u2.winfo_ismapped():
                _u2.pack(side='left', padx=1, pady=1, fill='x', expand=True)
            if unc2 == "0":
                _u2.config(text="UNC 0", background="#2ecc71", foreground="white")
            else:
                _u2.config(text="UNC %s" % unc2, background="#c0392b", foreground="white")
    def v(k):
        x = (stats or {}).get(k, "-")
        return x if x not in (None, "") else "-"
    def short(x):      # 1.23e-04 -> 1e-04 (passt besser in den schmalen Chip)
        try:
            return "%.0e" % float(x) if "e" in x else x
        except ValueError:
            return x
    chips["dbm"].config(text="%s dBm" % v("DBM") if v("DBM") != "-" else "dBm: -")
    chips["cnr"].config(text="CNR %s dB" % v("CNR") if v("CNR") != "-" else "CNR: -")
    chips["pre"].config(text="pre %s" % short(v("PREBER")))
    chips["post"].config(text="post %s" % short(v("POSTBER")))
    unc = v("UNC")
    if unc == "-":
        chips["unc"].config(text="UNC: -", background="#f1c40f", foreground="black")
    elif unc == "0":
        chips["unc"].config(text="UNC 0", background="#2ecc71", foreground="white")
    else:
        chips["unc"].config(text="UNC %s" % unc, background="#c0392b", foreground="white")

_octagon_cc_prev = [None, 0.0]    # (letzter CC-Wert, Zeitpunkt des letzten Anstiegs)

def _octagon_cc_chip_update(cc):
    """Continuity-Fehler (Paketverluste im Transportstrom, nur Direktmodus): gruen = 0,
    rot = in den letzten 10 s gestiegen, orange = frueher aufgetreten (Zaehler seit letztem TUNE)."""
    chip = _octagon_status_chips.get("cc")
    if not chip:
        return
    if cc is None:
        chip.config(text="CC: -", background="#f1c40f", foreground="black")
        _octagon_cc_prev[0] = None
        return
    prev, t_up = _octagon_cc_prev
    if prev is not None and cc > prev:
        t_up = time.time()
    _octagon_cc_prev[0], _octagon_cc_prev[1] = cc, t_up
    if cc == 0:
        chip.config(text="CC: 0", background="#2ecc71", foreground="white")
    elif time.time() - t_up < 10.0:
        chip.config(text="CC: %d" % cc, background="#c0392b", foreground="white")
    else:
        chip.config(text="CC: %d" % cc, background="#e67e22", foreground="white")

def _octagon_freq_chip_update(t, has_signal):
    """Frequenz-Chip der Octagon: orange = Zielfrequenz wurde gesendet, aber noch nicht bestaetigt;
    gruen = Box meldet Lock auf dieser Frequenz (Direktmodus: STATUS-FREQ == Soll, Enigma: Lock)."""
    chip = _octagon_status_chips.get("freq")
    if not chip:
        return
    soll = _target_cur_freq.get("octagon")
    if soll is None:
        chip.config(text="-", background="#d9d9d9", foreground="black")
        return
    ok = False
    if t is not None and has_signal:
        rf = t.get("freq")
        ok = True if rf is None else abs(rf - soll) <= 0.5
    chip.config(text="%.3f MHz" % soll, background=("#2ecc71" if ok else "#e67e22"), foreground="white")

def _octagon_status_tick():
    """Alle 2s: fragt /api/tunersignal der Octagon-Box ab und aktualisiert State-/MER-Chip
    (siehe _octagon_status_chips) - das ist das HTTP-Gegenstueck zu _lo_correct_tick() fuer
    LongMynd (das ueber UDP-Statuspakete laeuft, die Octagon nicht sendet)."""
    chips = _octagon_status_chips
    if _octagon_tick_after[0] is not None:
        try:
            root.after_cancel(_octagon_tick_after[0])    # nie zwei Takt-Ketten gleichzeitig
        except Exception:
            pass
        _octagon_tick_after[0] = None
    if chips:
        # HTTP-Abfrage im Hintergrund-Thread: bei nicht erreichbarer Box dauert sie bis zum Timeout
        # (3 s) und blockierte vorher bei jedem Takt die ganze GUI. Angezeigt wird immer das zuletzt
        # gelieferte Ergebnis.
        if not _octagon_poll_busy[0]:
            _octagon_poll_busy[0] = True
            def _work():
                try:
                    if _oct_direct():
                        # Enigma laeuft nicht - Werte kommen von dvb_direct.py (STATUS)
                        res = _oct_direct_send("STATUS2", timeout=3.0)
                        if res and "LOCK=" in res:
                            _oct_direct_running[0] = True
                            kv = dict(x.split("=", 1) for x in res.split() if "=" in x)
                            _octagon_poll_result[0] = ({
                                "lock": kv.get("LOCK") == "1",
                                "snr_db": float(kv.get("SNR") or 0) / 100.0,
                                "agc": float(kv.get("SIG") or 0) / 655.35,
                                "ber": (int(kv["BER"]) if kv.get("BER", "-").isdigit() else ""),
                                "freq": float(kv.get("FREQ") or 0) or None,
                                "cc": int(kv["CC"]) if "CC" in kv else None,
                                "stats": {k: kv.get(k, "-") for k in ("DBM", "CNR", "PREBER", "POSTBER", "UNC")},
                                "direct": True,
                            }, None)
                        else:
                            _oct_direct_running[0] = False
                            _octagon_poll_result[0] = (None, "dvb_direct antwortet nicht")
                        root.after(0, _oct_direct_button_update)
                    else:
                        _octagon_poll_result[0] = tuner_signal(cliargs.box)
                except Exception as _e:
                    _octagon_poll_result[0] = (None, repr(_e))
                finally:
                    _octagon_poll_busy[0] = False
                    if _octagon_fast[0]:             # Ergebnis gleich anzeigen, nicht erst im naechsten Takt
                        _octagon_fast[0] = False
                        try:
                            root.after(0, _octagon_status_tick)
                        except Exception:
                            pass
            threading.Thread(target=_work, daemon=True).start()
        t, err = _octagon_poll_result[0] if _octagon_poll_result[0] is not None else (None, "noch keine Antwort")
        if t is None:
            if _octagon_poll_result[0] is None or time.time() - _octagon_wait[0] < _OCT_WAIT_S:
                chips["state"].config(text="State: warte auf Rückmeldung", background="#e67e22",
                                      foreground="white")
            else:
                chips["state"].config(text="State: nicht erreichbar", background="#c0392b", foreground="white")
            chips["mer"].config(text="MER: -", **_mer_style(None))
            if "agc" in chips:
                chips["agc"].config(text=("SIG: -" if _oct_direct() else "AGC: -"),
                                    background="#f1c40f", foreground="black")
            if "ber" in chips:
                chips["ber"].config(text="BER: -", background="#f1c40f", foreground="black")
            _octagon_cc_chip_update(None)
            _octagon_stat_chips_update(None)
            _octagon_freq_chip_update(None, False)
        else:
            locked = t.get("lock")
            has_signal = (locked is True) or (locked is None and t["snr_db"] > 0)
            if has_signal:
                chips["state"].config(text="State: Signal", background="#2ecc71", foreground="white")
            else:
                chips["state"].config(text="State: kein Signal", background="#e67e22", foreground="white")
            chips["mer"].config(text="MER: %.1f dB" % t["snr_db"], **_mer_style(t["snr_db"]))
            if "agc" in chips:
                # Direktmodus: SIG = Signalstaerke (Eingangspegel) des Tuners in % (0-100);
                # Enigma-Modus: AGC-Wert der Box. MER bleibt das Qualitaetsmass.
                chips["agc"].config(text=("SIG: %.0f %%" if t.get("direct") else "AGC: %.0f") % t["agc"],
                                    background="#f1c40f", foreground="black")
            _octagon_freq_chip_update(t, has_signal)
            _octagon_cc_chip_update(t.get("cc"))
            _octagon_stat_chips_update(t.get("stats"))
            if "ber" in chips:
                ber = t.get("ber")
                if isinstance(ber, int) and ber >= 100000:
                    ber = "%.1e" % ber
                chips["ber"].config(text="BER: %s" % (ber if ber not in (None, "") else "-"),
                                     background="#f1c40f", foreground="black")
    _octagon_note_apply()
    # seltener abfragen: 5 s im Normalbetrieb, 15 s solange die Box nicht erreichbar ist
    _octagon_tick_after[0] = root.after(5000 if _octagon_reachable else 15000, _octagon_status_tick)
_lm_status_chips2 = {}  # target -> {"mer": Label, "fec": Label, "lna": Label, "bch": Label}

_lm_last_fec = {}   # target -> zuletzt angezeigter FEC/MODCOD-Name, fuer VLC-Resync bei Wechsel

def _update_lm_status_chips(target):
    """Aktualisiert die drei Status-Chips (UDP-Empfang / State / Frequenz-Abgleich) fuer
    LongMynd bzw. LongMynd P4 - einmal pro Sekunde aus _lo_correct_tick() aufgerufen."""
    chips = _lm_status_chips.get(target)
    if not chips:
        return

    last = _lm_last_packet_time.get(target, 0.0)
    udp_alive = (time.time() - last) < 5.0 if last else False
    chips["udp"].config(text="U", background=("#2ecc71" if udp_alive else "#c0392b"),
                         foreground="white")

    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    try:
        state_id = int(vals.get(1))
        state_name = _LM_STATE_NAMES[state_id]
    except (TypeError, ValueError, IndexError):
        state_id, state_name = None, None
    if state_id is None:
        chips["state"].config(text="State: -", background="#d9d9d9", foreground="black")
    elif state_id >= 3:
        chips["state"].config(text="State: locked %s" % ("DVB-S2" if state_id == 4 else "DVB-S"),
                               background="#2ecc71", foreground="white")
    else:
        chips["state"].config(text="State: %s" % state_name, background="#e67e22", foreground="white")

    rf = _lm_measured_rf_mhz(target)
    soll = _target_cur_freq.get(target)
    manual = bool(_spectrum_manual_var is not None and _spectrum_manual_var.get())
    if manual and (rf is not None or soll is not None):
        # Manuelle (freie) Frequenzwahl: Telekom-Magenta, mit der gemessenen (sonst Soll-)Frequenz
        chips["freq"].config(text="%.3f MHz" % (rf if rf is not None else soll),
                              background="#E20074", foreground="white")
    elif rf is None or soll is None:
        chips["freq"].config(text="-", background=("#E20074" if manual else "#d9d9d9"),
                              foreground=("white" if manual else "black"))
    else:
        match = abs(rf - soll) <= 0.5
        chips["freq"].config(text="%.1f MHz" % rf,
                              background=("#2ecc71" if match else "#c0392b"), foreground="white")

    chips2 = _lm_status_chips2.get(target)
    if not chips2:
        return

    # MER (ID 12, *10 dB) + D-Wert (Decodier-Marge gegenueber der MODCOD-Schwelle, siehe
    # _lm_extra_fields/_MODCOD_THRESH_DB) direkt mit angezeigt, wie gewuenscht ("Dx.x").
    mer = None
    try:
        mer = float(vals.get(12)) / 10.0
        _mer2, dwert, _codec2 = _lm_extra_fields(target)
        dtxt = (" / D%+.1f" % dwert) if dwert is not None else ""
        chips2["mer"].config(text="MER: %.1f dB%s" % (mer, dtxt), **_mer_style(mer))
    except (TypeError, ValueError):
        chips2["mer"].config(text="MER: -", **_mer_style(None))

    # FEC/MODCOD (ID 18) - Namenstabelle je nach State (DVB-S vs. DVB-S2). Bei MER<=0 dB (kein
    # echter Empfang, z.B. "searching") liefert Longmynd ohnehin nur den Platzhalter-MODCOD 0
    # ("DummyPL") - der ist keine echte FEC-Information und wird dann durch "-" ersetzt, statt
    # verwirrend "FEC: DummyPL" anzuzeigen.
    try:
        modcod = int(vals.get(18))
        if mer is not None and mer <= 0.0:
            chips2["fec"].config(text="FEC: -", background="#f1c40f", foreground="black")
            _lm_last_fec[target] = None
        else:
            if state_id == 3:
                fec_name = _MODCOD_NAMES_DVBS.get(modcod, str(modcod))
            else:
                fec_name = _MODCOD_NAMES_DVBS2.get(modcod, str(modcod))
            chips2["fec"].config(text="FEC: %s" % fec_name, background="#f1c40f", foreground="black")
            try:
                sr_now = int(float(vals.get(9)))
                sr_prev = _lm_last_sr_seen.get(target)
                if sr_prev and sr_now and abs(sr_now - sr_prev) > 0.03 * sr_prev:
                    _resync_hard_until[target] = time.time() + 90.0
                if sr_now:
                    _lm_last_sr_seen[target] = sr_now
            except (TypeError, ValueError):
                pass
            prev_fec = _lm_last_fec.get(target)
            if prev_fec and prev_fec != fec_name:
                _vlc_resync(target, "FEC/Codec-Wechsel %s -> %s" % (prev_fec, fec_name))
            _lm_last_fec[target] = fec_name
    except (TypeError, ValueError):
        chips2["fec"].config(text="FEC: -", background="#f1c40f", foreground="black")

    # LNA Gain (ID 2, roher Wert)
    try:
        lna = int(vals.get(2))
        chips2["lna"].config(text="LNA: %d" % lna, background="#f1c40f", foreground="black")
    except (TypeError, ValueError):
        chips2["lna"].config(text="LNA: -", background="#f1c40f", foreground="black")

    # BCH-Fehler (ID 22) - gruen wenn 0, sonst rot
    try:
        bch = int(vals.get(22))
        chips2["bch"].config(text="BCH: %d" % bch,
                              background=("#2ecc71" if bch == 0 else "#c0392b"), foreground="white")
    except (TypeError, ValueError):
        chips2["bch"].config(text="BCH: -", background="#f1c40f", foreground="black")

_LM_STATE_NAMES = ["initialising", "searching", "found_headers", "locked_DVB-S", "locked_DVB-S2"]

def _lm_measured_rf_mhz(target):
    """Wie _lm_beacon_rf_mhz(), aber OHNE die Bedingung 'Service == A71A' - liefert die aktuell
    gemessene RF-Frequenz (Carrier+LO) immer, sobald ein Carrier-Wert bekannt ist. Fuer die
    allgemeine Status-Anzeige (Frequenz-Abgleich-Chip), nicht fuer die Bake-Erkennung/LO-Korrektur."""
    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    try:
        carrier_khz = float(vals.get(6))
    except (TypeError, ValueError):
        return None
    lo_khz = _target_lo_mhz(target, _target_cur_freq.get(target)) * 1000.0
    return (carrier_khz + lo_khz) / 1000.0

def _lm_beacon_rf_mhz(target):
    """Liefert die aktuell vom Empfaenger gemessene RF-Frequenz (MHz, Carrier+LO) zurueck, wenn
    dieser gerade wirklich die Bake empfaengt (Service == 'A71A') - sonst None. Der LO kommt aus
    dem von dieser GUI verwalteten lnblo-Feld - fuer LongMynd P4 wird dabei angenommen, dass er
    an derselben Antenne/demselben LNB haengt wie LongMynd/rtl_sdr (gemeinsamer LO). Nur bei
    'longmynd' fliesst dieser Wert zusaetzlich in die aktive LO-Korrektur ein; bei 'longmynd_p4'
    dient er ausschliesslich der Anzeige (siehe _refresh_beacon_button_text)."""
    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    if vals.get(13) != "A71A":
        return None
    try:
        carrier_khz = float(vals.get(6))
    except (TypeError, ValueError):
        return None
    lo_khz = _target_lo_mhz(target, _target_cur_freq.get(target)) * 1000.0
    return (carrier_khz + lo_khz) / 1000.0

def _refresh_beacon_button_text(target):
    """Ergaenzt den Rufzeichen-Knopf (LongMynd/LongMynd P4) um die errechnete Bake-Frequenz
    (Carrier + LO), solange dieses Ziel gerade wirklich die Bake (A71A) empfaengt - der naechste
    tsduck-Poll (alle 5 s, siehe _service_name_poll_loop) setzt den Text bei Bedarf wieder auf
    den reinen Service-Namen zurueck, falls die Bake nicht mehr aktiv ist."""
    var = _target_callsign_var.get(target)
    if var is None:
        return
    rf = _lm_beacon_rf_mhz(target)
    if rf is not None:
        var.set("A71A   %.4f MHz" % rf)

def _update_bake_button_text():
    """Schreibt in die 'Bake'-Zeile der BATC-Signalliste (erste Zeile, immer vorhanden) fuer
    die Spalten LongMynd/LongMynd P4 die tatsaechlich errechnete Bake-Frequenz (Carrier + LO)
    statt der festen 10491.500 MHz - aber nur, solange dieses Ziel die Bake gerade wirklich
    empfaengt (Service == A71A). Empfaengt es die Bake nicht (mehr), steht dort wieder der
    normale, feste Bake-Text (identisch zu den anderen Spalten)."""
    if not _batc_row_cache or _batc_row_cache[0]["key"] is not None:
        return
    bake_row = _batc_row_cache[0]
    for target in _LM_TARGETS:
        if target not in _active_targets:
            continue
        col = _active_targets.index(target)
        if col >= len(bake_row["buttons"]):
            continue
        btn = bake_row["buttons"][col]
        rf = _lm_beacon_rf_mhz(target)
        if rf is not None:
            btn.config(text="Bake %.4f MHz  (A71A)" % rf)
        else:
            btn.config(text=bake_row["text"])

def _lo_correct_tick():
    """Einmal pro Sekunde: prueft, ob LongMynd gerade die Bake empfaengt, zeigt die Abweichung
    von 10491.500 MHz an und korrigiert - wenn 'LO korrigieren' aktiv ist - lnblo laufend so,
    dass die gemessene RF-Frequenz exakt auf die Bake-Nominalfrequenz konvergiert (absolut
    ausgehend von der beim Einschalten gemerkten Basis, nicht aufaddierend). Empfaengt LongMynd
    die Bake gerade nicht, bleibt lnblo unveraendert - der zuletzt erreichte Korrekturwert steht
    einfach weiter."""
    global _service_history_line2
    rf = _lm_beacon_rf_mhz("longmynd")

    if _lo_autocorrect_baseline is None:
        corr_txt = "Korrigiert um:  -"
    else:
        corr_txt = "Korrigiert um:  %+.1f kHz" % ((float(lnblo.get()) - _lo_autocorrect_baseline) * 1000.0)

    if rf is None:
        _service_history_line2 = "Bake-Abweichung:  (nicht empfangen)    " + corr_txt
    else:
        dev_khz = (rf - BATC_BEACON_NOMINAL_MHZ) * 1000.0
        cur_lo = float(lnblo.get())
        # WICHTIG: die neue LO IMMER vom AKTUELLEN lo aus berechnen, nicht von der beim
        # Einschalten eingefrorenen Basis - rf/dev_khz enthalten ja bereits den aktuellen
        # (schon korrigierten) lo (siehe _lm_beacon_rf_mhz: rf = carrier + aktueller_lo).
        # Mit der Basis statt cur_lo gerechnet ergab sich ein Ping-Pong: sobald dev_khz durch
        # eine vorherige Korrektur auf 0 konvergiert war, rechnete die Formel beim naechsten
        # Zyklus trotzdem wieder "Basis - 0" = Basis aus und sprang so jeden Zyklus zwischen
        # Basis- und korrigiertem Wert hin und her, statt beim konvergierten Wert zu bleiben.
        # Die Basis wird weiterhin nur fuer die "Korrigiert um"-Anzeige gebraucht (siehe oben).
        would_be_lo = round(cur_lo - dev_khz / 1000.0, 6)
        olog("Bake erkannt (A71A, LongMynd): RF=%.4f MHz, Abweichung=%+.1f kHz" % (rf, dev_khz))
        olog("Berechne neue LO: aktuell %.6f MHz -> Korrektur waere %.6f MHz (Delta %+.1f kHz)"
             % (cur_lo, would_be_lo, (would_be_lo - cur_lo) * 1000.0))
        if lo_autocorrect.get() and _lo_autocorrect_baseline is not None:
            lnblo.set(would_be_lo)
            corr_txt = "Korrigiert um:  %+.1f kHz" % ((float(lnblo.get()) - _lo_autocorrect_baseline) * 1000.0)
            olog("-> LO korrigiert auf %.6f MHz ('LO korrigieren' ist angehakt)." % would_be_lo)
        else:
            olog("-> NICHT angewandt (Haken 'LO korrigieren' ist nicht gesetzt).")
        _service_history_line2 = ("Bake-Abweichung:  %+.1f kHz  (%.4f MHz)    %s"
                                   % (dev_khz, rf, corr_txt))
    _service_box_refresh()

    for _lmt in _LM_TARGETS:
        _refresh_beacon_button_text(_lmt)
    _update_bake_button_text()
    for _lmt in _LM_TARGETS:
        _update_lm_status_chips(_lmt)

    root.after(1000, _lo_correct_tick)

#----- Signal-Log: schreibt bei jeder Rufzeichen-Erkennung/-Wechsel eine Zeile in signal_log.csv --
# (Datum/Uhrzeit, Ziel, Rufzeichen, Provider, MER, D-Wert, HF/ZF/LO, Codec, Bake-Abweichung).
# Aufloesung wird aktuell nicht ausgelesen (bleibt leer) - dafuer waere eine zusaetzliche
# VLC-Telnet-Abfrage noetig, wie sie auswertung_4002.sh per read_res() macht.
_SIGNAL_LOG_FILE = parameters_path + "signal_log.csv"
_SIGNAL_LOG_HEADER = ["Datum", "Uhrzeit", "Ziel", "Rufzeichen", "Service Provider",
                      "MER (dB)", "D-Wert (dB)", "Frequenz HF (MHz)", "ZF (MHz)", "LO (MHz)",
                      "Aufloesung", "Codec", "Bake-Abweichung (kHz)", "FEC",
                      "Symbolrate (kS/s)", "Audio-Codec"]
_signal_log_lock = threading.Lock()
_csv_ensure_header(_SIGNAL_LOG_FILE, _SIGNAL_LOG_HEADER)
_signal_log_last = {}   # target -> zuletzt geloggtes Rufzeichen (fuer Wechsel-Erkennung)
_known_callsigns_load()   # bisher gesehene Rufzeichen (fuer die Meldung bei NEUEN Rufzeichen)

# Decodierschwelle (Es/N0 in dB, ETSI EN 302 307, normal frames) je MODCOD 1..28 - fuer D-Wert
_MODCOD_THRESH_DB = {1: -2.35, 2: -1.24, 3: -0.30, 4: 1.00, 5: 2.23, 6: 3.10, 7: 4.03, 8: 4.68,
                     9: 5.18, 10: 6.20, 11: 6.42, 12: 5.50, 13: 6.62, 14: 7.91, 15: 9.35,
                     16: 10.69, 17: 10.98, 18: 8.97, 19: 10.21, 20: 11.03, 21: 11.61, 22: 12.89,
                     23: 13.13, 24: 12.73, 25: 13.64, 26: 14.28, 27: 15.69, 28: 16.05}
_ES_TYPE_CODEC = {27: "H.264/AVC", 36: "H.265/HEVC", 51: "H.266/VVC", 1: "MPEG-2", 2: "MPEG-2"}

def _lm_extra_fields(target):
    """MER/D-Wert/Codec aus den rohen Longmynd-Statuswerten (nur longmynd/longmynd_p4)."""
    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    mer = None
    if 12 in vals:
        try:
            mer = float(vals[12]) / 10.0
        except (TypeError, ValueError):
            mer = None
    dwert = None
    if mer is not None and 18 in vals:
        try:
            thr = _MODCOD_THRESH_DB.get(int(vals[18]))
            if thr is not None:
                dwert = mer - thr
        except (TypeError, ValueError):
            pass
    codec = None
    if 17 in vals:
        try:
            codec = _ES_TYPE_CODEC.get(int(vals[17]))
        except (TypeError, ValueError):
            codec = None
    return mer, dwert, codec

def _signal_log_write_async(target, name):
    """Wie _signal_log_write, aber im Hintergrund-Thread (tuner_signal()/Dateizugriff blockierten
    den Hauptthread bis ~570 ms bei jedem Rufzeichenwechsel). Tk-Variablen werden hier im
    Hauptthread gelesen."""
    try:
        lo_raw = lnblo.get()
    except Exception:
        lo_raw = None
    threading.Thread(target=lambda: _signal_log_write(target, name, lo_raw), daemon=True).start()

def _signal_log_write(target, name, lo_raw="__tk__"):
    """Schreibt eine Zeile in signal_log.csv mit allen aktuell verfuegbaren Empfangsdaten fuer
    dieses Ziel - aufgerufen bei jeder Rufzeichen-Erkennung/-Wechsel (siehe _set_callsign_display).
    Nicht jedes Feld ist fuer jedes Ziel verfuegbar (z.B. MER nur LongMynd/LongMynd P4/Octagon,
    Codec/D-Wert nur LongMynd/LongMynd P4) - dann bleibt die jeweilige Spalte leer."""
    now = time.localtime()
    provider = ""
    mer = None
    dwert = None
    codec = ""

    if target in _LM_TARGETS:
        with _lm_status_lock:
            vals = dict(_lm_status_vals.get(target, {}))
        provider = vals.get(14, "") or ""
        mer, dwert, codec = _lm_extra_fields(target)
    elif target == "octagon":
        try:
            t, _err = tuner_signal(cliargs.box)
            if t is not None:
                mer = t.get("snr_db")
        except Exception:
            pass

    # Provider/Codec/Aufloesung per ffprobe (siehe _poll_service_name_once) - ergaenzt fehlende
    # Werte, ueberschreibt aber nicht, was aus den Longmynd-Statuspaketen schon bekannt ist.
    vinfo = _target_video_info.get(target) or {}
    if not provider:
        provider = vinfo.get("service_provider") or ""
    if not codec:
        codec = vinfo.get("codec") or ""
    resolution = vinfo.get("resolution") or ""

    hf = _target_cur_freq.get(target)
    try:
        lo = float(lnblo.get() if lo_raw == "__tk__" else lo_raw)
    except (ValueError, TypeError, tk.TclError):
        lo = None
    if hf is not None and _is_direct_freq(hf):
        lo = 0.0
    zf = (hf - lo) if (hf is not None and lo is not None) else None

    abweichung_khz = ""
    if target in _LM_TARGETS:
        rf = _lm_beacon_rf_mhz(target)
        if rf is not None:
            abweichung_khz = "%.1f" % ((rf - BATC_BEACON_NOMINAL_MHZ) * 1000.0)

    fec_txt = ""
    if target in _LM_TARGETS:
        fec_txt = _lm_last_fec.get(target) or _spectrum_fec_for_freq(hf if hf is not None else 0.0) or ""
    elif target == "rtl_sdr":
        try:
            fec_txt = (fec.get() or "").strip()
        except Exception:
            fec_txt = ""

    row = [
        time.strftime("%Y-%m-%d", now), time.strftime("%H:%M:%S", now),
        _TARGET_LABEL.get(target, target), name, provider,
        ("%.1f" % mer) if mer is not None else "",
        ("%.1f" % dwert) if dwert is not None else "",
        ("%.3f" % hf) if hf is not None else "",
        ("%.3f" % zf) if zf is not None else "",
        ("%.6f" % lo) if lo is not None else "",
        resolution, codec or "", abweichung_khz, fec_txt,
        ("%d" % round(_target_cur_sr.get(target))) if _target_cur_sr.get(target) else "",
        vinfo.get("audio_codec") or "",
    ]
    try:
        os.makedirs(os.path.dirname(_SIGNAL_LOG_FILE), exist_ok=True)
        with _signal_log_lock:
            new_file = not os.path.isfile(_SIGNAL_LOG_FILE)
            with open(_SIGNAL_LOG_FILE, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(_SIGNAL_LOG_HEADER)
                w.writerow(row)
        dlog("Signal-Log: %s / %s -> %s" % (_TARGET_LABEL.get(target, target), name, _SIGNAL_LOG_FILE))
    except OSError as e:
        olog("Signal-Log konnte nicht geschrieben werden (%s): %s" % (_SIGNAL_LOG_FILE, e))

_resync_last_callsign = {}   # target -> zuletzt erkanntes Rufzeichen (fuer _vlc_resync)

def _set_callsign_display(target, name):
    """Aktualisiert Text UND Optik des Rufzeichen-'Knopfes': gruen + Rufzeichen wenn erkannt,
    sonst grau/leer - und pflegt bei einer Erkennung zusaetzlich die gemeinsame Service-Historie."""
    var = _target_callsign_var.get(target)
    lbl = _target_callsign_label.get(target)
    if var is None or lbl is None:
        return
    if name:
        # Gegen das zuletzt ERKANNTE Rufzeichen vergleichen (nicht gegen den Knopf-Text): der wird
        # bei jedem Frequenzwechsel geleert (dann gaebe es nie ein "vorheriges" Rufzeichen) und
        # bei der Bake mit der Frequenz dekoriert ("A71A   10491.5 MHz" != "A71A").
        prev = _resync_last_callsign.get(target)
        if prev != name:
            _resync_last_callsign[target] = name
            _tsa_reset(target, "neues Rufzeichen %s" % name)
            _vlc_resync(target, "neues Rufzeichen %s%s" % (name, (" (vorher %s)" % prev) if prev else ""))
        var.set(name)
        _callsign_seen[target] = (time.time(), time.strftime("%H:%M:%S"))
        _callsign_stale[target] = False
        d = _target_callsign_disp.get(target)
        if d is not None:
            d.set(name)
        lbl.configure(background="#2ecc71", foreground="black", font=_CALLSIGN_FONT)
        _service_history_add(name)
        _ck = name.strip().lower()
        if _ck and _ck not in _known_callsigns:
            _known_callsigns.add(_ck)
            _new_callsign_event(target, name)
        if _signal_log_last.get(target) != name:
            _signal_log_last[target] = name
            try:
                _signal_log_write_async(target, name)
            except Exception as _e:
                olog("Signal-Log FEHLER (%s, %s): %r" % (_TARGET_LABEL.get(target, target), name, _e))
        _spectrum_remember_callsign(_target_cur_freq.get(target), name)
    else:
        var.set("")
        d = _target_callsign_disp.get(target)
        if d is not None:
            d.set("")
        _callsign_stale.pop(target, None)
        _callsign_seen.pop(target, None)
        lbl.configure(background="#d9d9d9", foreground="black", font=_CALLSIGN_FONT)
        _signal_log_last[target] = None

def _poll_service_name_once(target):
    """Ermittelt einmalig den aktuellen Rufzeichen-/Service-Namen fuer das gegebene Ziel und
    pflegt nebenbei _target_video_info (Provider/Codec/Aufloesung, fuer das Signal-Log) -
    LongMynd/LongMynd P4/rtl_sdr per ffprobe aus dem Multicast-Videostream (ein Aufruf liefert
    Service+Provider+Codec+Aufloesung zusammen, wie in stream_probe.sh), Octagon-Rufzeichen per
    SSH+dvbsnoop direkt auf der Box (kein direkt erreichbarer Multicast-Stream dafuer), die
    Octagon-Aufloesung/-Codec best-effort per ffprobe auf die zuletzt gezappte Stream-URL."""
    if target == "octagon" and _oct_direct():
        # Enigma laeuft nicht - dvbsnoop per SSH geht also nicht. Stattdessen wie bei LongMynd
        # ffprobe auf den UDP-Stream, den dvb_direct.py ausgibt.
        ip, port = _oct_direct_stream()
        info = _ffprobe_probe("udp://%s:%s" % (ip, port))
        if info is None:
            return None
        _target_video_info["octagon"] = info
        return info.get("service_name")

    if target == "octagon":
        name = _octagon_service_name()
        info = _octagon_video_info()
        if info is not None:
            _target_video_info["octagon"] = info
        return name

    if target in _LM_TARGETS and app_config.get("lm_callsign_source") == "longmynd_udp":
        # Schneller Weg: Service-Name kommt direkt aus dem auswertung_4002.sh-Statusstrom
        # (Feld 13, siehe _lm_status_vals/_lm_beacon_rf_mhz) - kein eigener ffprobe-Aufruf noetig,
        # dadurch v.a. beim Scan-Knopf deutlich schneller. Umschaltbar in den Spektrum-Einstellungen
        # (_spectrum_settings_open) bzw. app_config["lm_callsign_source"].
        with _lm_status_lock:
            vals = dict(_lm_status_vals.get(target, {}))
        return vals.get(13) or None

    get_stream = _TARGET_STREAM.get(target)
    _ep = get_stream() if get_stream else None
    if not _ep:
        return None
    stream_ip, stream_port = _ep
    info = _ffprobe_probe("udp://%s:%s" % (stream_ip, stream_port))
    if info is None:
        # ffprobe nicht verfuegbar oder Stream gerade nicht lesbar - tsduck als Rueckfall
        # nur fuers Rufzeichen (Provider/Codec/Aufloesung bleiben dann leer).
        return _tsduck_service_name(stream_ip, stream_port)
    _target_video_info[target] = info
    return info.get("service_name")

def _lm_decoding(target):
    """True, wenn LongMynd/P4 gerade wirklich dekodiert (State locked, MER > 0). Der zuletzt empfangene
    Service-Name bleibt im Statusstrom auch im State 'searching' stehen und darf dann nicht als
    'gerade gesehen' gelten."""
    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    try:
        return int(vals.get(1)) >= 3 and float(vals.get(12)) > 0.0
    except (TypeError, ValueError):
        return False

def _service_name_poll_loop(target):
    """Fragt alle paar Sekunden das Rufzeichen/den Service-Namen des aktuell empfangenen
    DATV-Streams ab und zeigt es als Knopf unter der Automatik-Checkbox der jeweiligen Spalte
    an (LongMynd/LongMynd P4/Octagon) - gruen wenn erkannt, sonst grau/leer."""
    var = _target_callsign_var.get(target)
    if var is None:
        return
    last_t = None
    while True:
        t0 = time.time()
        name = _poll_service_name_once(target)
        if last_t is not None:
            _callsign_poll_cycle[target] = max(0.0, t0 - last_t)
        last_t = t0
        if t0 < _callsign_block_until.get(target, 0.0):
            pass     # Abfrage begann vor Ende der Umschaltung: kann noch den alten Kanal gesehen haben - verwerfen
        elif name and target in _LM_TARGETS and not _lm_decoding(target):
            pass     # alter Name bei State 'searching' o.ae.: nicht auffrischen - das Rufzeichen altert (grau, last:)
        elif name or not (target == "octagon" and _oct_direct()):
            root.after(0, _set_callsign_display, target, name)
        # Direktmodus: ein Fehlschlag von ffprobe (Stream kurz nicht lesbar) loescht das Rufzeichen
        # nicht - es wird dann ueber die Alterung grau ("last: ...") statt zu flackern.
        time.sleep(3 if (target == "octagon" and _oct_direct()) else 5)

def _longmyndp4_process_running():
    """Prueft per SSH, ob auf dem LongMynd-P4-Pi der longmynd-Prozess laeuft (ps -ax | grep
    longmynd, ohne den grep-Prozess selbst per [l]ongmynd-Trick auszuschliessen). Liefert True/False,
    oder None, wenn der Check selbst fehlgeschlagen ist (kein ssh, Timeout, Verbindungsfehler)."""
    ssh = shutil.which("ssh")
    if not ssh:
        return None
    host = cliargs.longmyndp4_ssh_host
    user = cliargs.longmyndp4_ssh_user
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), "ps -ax | grep -i '[l]ongmynd'"]
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
    except (subprocess.TimeoutExpired, OSError) as e:
        dlog("LongMynd P4: Prozess-Check fehlgeschlagen (%s@%s): %s" % (user, host, e))
        return None
    out = result.stdout.decode(errors="replace").strip()
    dlog("LongMynd P4: ps-Check-Ausgabe: %s" % (out or "(leer)"))
    return bool(out)

def _longmyndp4_send_stop():
    """Wird vor jedem LongMynd-P4-Tuning-Kommando aufgerufen: schickt per SSH ein Stop-Kommando
    an denselben LongMynd-P4-Pi wie --longmyndp4-ssh-host, als dg7ndv
    (Default dg7ndv@192.168.0.242 '/home/dg7ndv/stop.sh')."""
    host = cliargs.longmyndp4_stop_ssh_host
    user = cliargs.longmyndp4_stop_ssh_user
    script = cliargs.longmyndp4_stop_script
    ssh = shutil.which("ssh")
    if not ssh:
        return
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), script]
    dlog("LongMynd P4 Stop-SSH-Kommando: %s" % argv)
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        if result.returncode != 0:
            olog("LongMynd P4: Stop-Kommando (%s@%s %s) endete mit Exit-Code %d" %
                 (user, host, script, result.returncode))
        if result.stdout:
            dlog("LongMynd P4 Stop-SSH-Ausgabe: %s" % result.stdout.decode(errors="replace").strip())
    except subprocess.TimeoutExpired:
        olog("Fehler: Stop-SSH-Kommando zu %s@%s hat zu lange gedauert (Timeout)." % (user, host))
    except OSError as e:
        olog("Fehler beim Stop-SSH-Aufruf (%s@%s): %s" % (user, host, e))

def _longmyndp4_send_start():
    """Wird beim Beenden des Programms aufgerufen (Gegenstueck zu _longmyndp4_send_stop):
    schickt per SSH ein Start-Kommando an denselben LongMynd-P4-Pi, als dg7ndv
    (Default dg7ndv@192.168.0.242 '/home/dg7ndv/start.sh')."""
    host = cliargs.longmyndp4_stop_ssh_host
    user = cliargs.longmyndp4_stop_ssh_user
    script = cliargs.longmyndp4_start_script
    ssh = shutil.which("ssh")
    if not ssh:
        return
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), script]
    dlog("LongMynd P4 Start-SSH-Kommando (beim Beenden): %s" % argv)
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        if result.returncode != 0:
            olog("LongMynd P4: Start-Kommando (%s@%s %s) endete mit Exit-Code %d" %
                 (user, host, script, result.returncode))
        if result.stdout:
            dlog("LongMynd P4 Start-SSH-Ausgabe: %s" % result.stdout.decode(errors="replace").strip())
    except subprocess.TimeoutExpired:
        olog("Fehler: Start-SSH-Kommando zu %s@%s hat zu lange gedauert (Timeout)." % (user, host))
    except OSError as e:
        olog("Fehler beim Start-SSH-Aufruf (%s@%s): %s" % (user, host, e))

_LONGMYNDP4_START_SH_CONTENT = "sudo systemctl start getty@tty1\n"
_LONGMYNDP4_STOP_SH_CONTENT = ('sudo systemctl stop getty@tty1\n'
                               'sudo pkill rpidatvgui\n'
                               'sudo pkill -f "scheduler.sh|bandview|langstone|siggen"\n')

def _longmyndp4_create_remote_script(host, user, path, content):
    """Legt per SSH eine Datei mit dem gegebenen Inhalt auf host/user an und macht sie
    ausfuehrbar (chmod +x) - benutzt fuer das automatische Nachlegen von stop.sh/start.sh,
    falls _longmyndp4_check_stop_start_scripts() sie dort nicht vorfindet."""
    ssh = shutil.which("ssh")
    if not ssh:
        return False
    remote_cmd = "cat > %s << 'EOF_LEANDVB_GUI' && chmod +x %s\n%sEOF_LEANDVB_GUI\n" % (
        shlex.quote(path), shlex.quote(path), content)
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), remote_cmd]
    dlog("Lege fehlendes Script per SSH an: %s@%s:%s" % (user, host, path))
    dlog("Inhalt, der nach %s geschrieben wird:\n%s" % (path, content))
    dlog("Vollstaendiges SSH-Kommando: %s" % argv)
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        dlog("Exit-Code des Anlege-Kommandos (%s@%s:%s): %d"
             % (user, host, path, result.returncode))
        if result.stdout:
            dlog("SSH-Ausgabe (Anlegen %s): %s"
                 % (path, result.stdout.decode(errors="replace").strip()))
        if result.returncode == 0:
            olog("LongMynd P4: %s auf %s@%s wurde automatisch angelegt und ausfuehrbar gemacht."
                 % (path, user, host))
            return True
        olog("FEHLER: Anlegen von %s auf %s@%s fehlgeschlagen (Exit-Code %d)."
             % (path, user, host, result.returncode))
        return False
    except subprocess.TimeoutExpired:
        olog("FEHLER: Anlegen von %s auf %s@%s hat zu lange gedauert (Timeout)." % (path, user, host))
        return False
    except OSError as e:
        olog("FEHLER: Anlegen von %s auf %s@%s fehlgeschlagen: %s" % (path, user, host, e))
        return False

def _longmyndp4_check_stop_start_scripts():
    """Wird einmal beim Start aufgerufen (in eigenem Thread, damit ein langsames/nicht
    erreichbares SSH-Ziel den GUI-Start nicht blockiert): prueft per SSH, ob Stop- und
    Start-Script auf --longmyndp4-stop-ssh-host vorhanden und ausfuehrbar sind. Fehlt eines
    (oder beide), wird es automatisch mit dem bekannten Standardinhalt angelegt und per
    chmod +x ausfuehrbar gemacht (_longmyndp4_create_remote_script) - Inhalt siehe
    _LONGMYNDP4_STOP_SH_CONTENT/_LONGMYNDP4_START_SH_CONTENT."""
    host = cliargs.longmyndp4_stop_ssh_host
    user = cliargs.longmyndp4_stop_ssh_user
    stop_script = cliargs.longmyndp4_stop_script
    start_script = cliargs.longmyndp4_start_script
    ssh = shutil.which("ssh")
    if not ssh:
        olog("ssh nicht gefunden - Stop-/Start-Kommando fuer LongMynd P4 (%s@%s) kann nicht "
             "geprueft werden." % (user, host))
        return
    remote_cmd = ("[ -x '%s' ] && echo STOP_OK || echo STOP_FEHLT; "
                  "[ -x '%s' ] && echo START_OK || echo START_FEHLT") % (stop_script, start_script)
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), remote_cmd]
    dlog("Pruefe Stop-/Start-Script auf %s@%s (Stop=%s, Start=%s): %s"
         % (user, host, stop_script, start_script, argv))
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        out = result.stdout.decode(errors="replace") if result.stdout else ""
        dlog("Rohe SSH-Ausgabe der Pruefung (%s@%s): %r" % (user, host, out.strip()))
        stop_ok = "STOP_OK" in out
        start_ok = "START_OK" in out
        dlog("Ergebnis Pruefung: Stop-Script vorhanden=%s, Start-Script vorhanden=%s"
             % (stop_ok, start_ok))
        if stop_ok and start_ok:
            olog("LongMynd P4: Stop-Script (%s) und Start-Script (%s) auf %s@%s vorhanden und "
                 "ausfuehrbar." % (stop_script, start_script, user, host))
            return
        if not stop_ok:
            olog("LongMynd P4: Stop-Script (%s) fehlt auf %s@%s - wird automatisch angelegt."
                 % (stop_script, user, host))
            dlog("Stop-Script fehlt: %s@%s:%s (Exit-Code der Pruefung: %d, Rohausgabe: %r)"
                  % (user, host, stop_script, result.returncode, out.strip()))
            stop_ok = _longmyndp4_create_remote_script(host, user, stop_script,
                                                        _LONGMYNDP4_STOP_SH_CONTENT)
        if not start_ok:
            olog("LongMynd P4: Start-Script (%s) fehlt auf %s@%s - wird automatisch angelegt."
                 % (start_script, user, host))
            dlog("Start-Script fehlt: %s@%s:%s (Exit-Code der Pruefung: %d, Rohausgabe: %r)"
                  % (user, host, start_script, result.returncode, out.strip()))
            start_ok = _longmyndp4_create_remote_script(host, user, start_script,
                                                         _LONGMYNDP4_START_SH_CONTENT)
        if not (stop_ok and start_ok):
            fehlend = []
            if not stop_ok:
                fehlend.append("Stop-Script (%s)" % stop_script)
            if not start_ok:
                fehlend.append("Start-Script (%s)" % start_script)
            olog("FEHLER: LongMynd P4: %s auf %s@%s konnte nicht angelegt werden - Umschalten "
                 "bzw. Beenden wird trotzdem versucht, das SSH-Kommando schlaegt dann fehl."
                 % (" und ".join(fehlend), user, host))
    except subprocess.TimeoutExpired:
        olog("FEHLER: Pruefung von Stop-/Start-Script auf %s@%s hat zu lange gedauert (Timeout) - "
             "Geraet evtl. nicht erreichbar." % (user, host))
    except OSError as e:
        olog("FEHLER: Pruefung von Stop-/Start-Script auf %s@%s fehlgeschlagen: %s" % (user, host, e))

def send_to_longmynd_p4(freq, sr):
    """Stellt den LongMynd-P4-Receiver (zweiter Pi) per SSH auf die gewuenschte Downlink-Frequenz
    (MHz) und Symbolrate (kHz) ein - ruft dort '<remote-script> <Downlink-MHz> <SR-kHz>' auf,
    genau wie das urspruengliche qo100rx-Script (ssh pi@<host> "~/rx_udp.sh $1 ${2:-1500}").
    Prueft vorher per 'ps -ax | grep longmynd', ob der longmynd-Prozess dort ueberhaupt laeuft -
    falls nicht, wird eine Fehlermeldung ausgegeben und das Kommando trotzdem gesendet (das
    Remote-Skript startet longmynd dabei neu). Schickt VORHER immer ein Stop-Kommando an ein
    separates Geraet (siehe _longmyndp4_send_stop)."""
    host = cliargs.longmyndp4_ssh_host
    user = cliargs.longmyndp4_ssh_user
    script = cliargs.longmyndp4_remote_script
    sr_khz = int(round(sr)) if sr else cliargs.longmyndp4_sr_default
    ssh = shutil.which("ssh")
    if not ssh:
        olog("ssh nicht gefunden - LongMynd P4 kann nicht angesteuert werden.")
        return

    _longmyndp4_send_stop()

    running = _longmyndp4_process_running()
    if running is False:
        olog("FEHLER: longmynd-Prozess laeuft NICHT auf LongMynd P4 (%s@%s) - sende Kommando "
             "erneut (Neustart per Remote-Skript)." % (user, host))

    # Direktempfang (z.B. 437 MHz, ohne LNB): das Pi-Skript rechnet ZF = (Frequenz - LO) mit seinem
    # festen LO - damit ZF = Frequenz herauskommt, wird der LO hier dazuaddiert (--longmyndp4-script-lo).
    arg_freq = freq + float(cliargs.longmyndp4_script_lo) if _is_direct_freq(freq) else freq
    remote_cmd = "%s %.3f %d" % (script, arg_freq, sr_khz)
    argv = [ssh, "-o", "ConnectTimeout=5", "%s@%s" % (user, host), remote_cmd]
    dlog("LongMynd P4 SSH-Kommando: %s" % argv)
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        if result.returncode == 0:
            olog("LongMynd P4 auf %.3f MHz / %d kHz gestellt (ssh %s@%s)" % (freq, sr_khz, user, host))
        else:
            olog("LongMynd P4: ssh-Kommando endete mit Exit-Code %d (%s@%s)" %
                 (result.returncode, user, host))
        if result.stdout:
            dlog("LongMynd P4 ssh-Ausgabe: %s" % result.stdout.decode(errors="replace").strip())
    except subprocess.TimeoutExpired:
        olog("Fehler: SSH zu LongMynd P4 (%s@%s) hat zu lange gedauert (Timeout)." % (user, host))
    except OSError as e:
        olog("Fehler beim SSH-Aufruf zu LongMynd P4: %s" % e)

#===== Tuner-Pruefung LongMynd P4 (zweistufig, jede Minute): Stufe 1 = Pi per SSH erreichbar,  =====
#===== Stufe 2 = dort per lsusb ein FT2232 (MiniTiouner-USB-Interface) gelistet. Fehlt eines  =====
#===== davon, meldet die Hauptmaske das (rotes Label oben), die LongMynd-P4-Spalte wird        =====
#===== ausgegraut (alle Bedienelemente inkl. Scan/Signalknoepfe) und weder Automatik noch      =====
#===== Klick/Scan schicken dorthin etwas. Sobald wieder alles da ist, wird reaktiviert.        =====
_longmyndp4_ready = True            # False solange Pi/Tuner nicht gefunden wurden
_longmyndp4_state_applied = True    # Zustand, der gerade an den Widgets eingestellt ist
_longmyndp4_checked_once = False

def _target_available(target):
    """False nur fuer LongMynd P4, solange dort der Tuner fehlt/der Pi nicht erreichbar ist."""
    return not (target == "longmynd_p4" and not _longmyndp4_ready)

def _longmyndp4_tuner_check():
    """Ein SSH-Aufruf ('lsusb'). Liefert ("ok", ""), ("pi", Grund) = Stufe 1 gescheitert oder
    ("tuner", Grund) = Pi erreichbar, aber kein FT2232 gelistet."""
    ssh = shutil.which("ssh")
    if not ssh:
        return ("pi", "ssh nicht gefunden")
    host = cliargs.longmyndp4_ssh_host
    user = cliargs.longmyndp4_ssh_user
    argv = [ssh, "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "%s@%s" % (user, host), "lsusb"]
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12)
    except subprocess.TimeoutExpired:
        return ("pi", "SSH-Timeout")
    except OSError as e:
        return ("pi", str(e))
    err = [l for l in r.stderr.decode(errors="replace").splitlines() if l.strip() and not l.startswith("**")]
    if r.returncode == 255:
        return ("pi", err[-1] if err else "SSH-Verbindung fehlgeschlagen")
    if r.returncode != 0:
        return ("tuner", "lsusb endete mit Exit-Code %d" % r.returncode)
    if "ft2232" not in r.stdout.decode(errors="replace").lower():
        return ("tuner", "kein FT2232 in lsusb")
    return ("ok", "")

def _longmyndp4_set_widgets(enabled):
    """Setzt alle Widgets der LongMynd-P4-Spalte (rekursiv, inkl. Signalknoepfe) auf aktiv bzw.
    ausgegraut. Reine Anzeige-Chips (tk.Label) bleiben unberuehrt."""
    col = _target_col_frames.get("longmynd_p4")
    if col is None:
        return
    def walk(w):
        for c in w.winfo_children():
            walk(c)
            try:
                if isinstance(c, ttk.Widget):
                    c.state(["!disabled"] if enabled else ["disabled"])
                elif isinstance(c, (tk.Button, tk.Checkbutton, tk.Entry, tk.Radiobutton)):
                    c.configure(state=("normal" if enabled else "disabled"))
            except tk.TclError:
                pass
    walk(col)
    for fr in _target_extra_frames.get("longmynd_p4", []):
        walk(fr)

def _longmyndp4_apply_state(force=False):
    """Idempotent - auch aus batc_poll()/batc_rebuild_list() aufrufbar, denn neu aufgebaute
    Signalknoepfe entstehen aktiv und muessen bei fehlendem Tuner sofort wieder ausgegraut werden
    (force=True nach einem Neuaufbau). Ohne Zustandswechsel kein Widget-Durchlauf - vorher lief der
    bei fehlendem Tuner alle 2 s ueber die ganze Spalte."""
    global _longmyndp4_state_applied
    if not force and _longmyndp4_state_applied == _longmyndp4_ready:
        return
    _longmyndp4_set_widgets(_longmyndp4_ready)
    _longmyndp4_state_applied = _longmyndp4_ready

def _longmyndp4_tuner_apply(status, detail):
    """Hauptthread: Ergebnis von _longmyndp4_tuner_check() anwenden (Banner, Log, Widgets)."""
    global _longmyndp4_ready, _longmyndp4_checked_once
    ready = (status == "ok")
    changed = (ready != _longmyndp4_ready) or not _longmyndp4_checked_once
    first = not _longmyndp4_checked_once
    _longmyndp4_checked_once = True
    _longmyndp4_ready = ready
    if ready:
        lbl_lmp4_warn.config(text="")
        if changed:
            olog("LongMynd P4: Pi erreichbar, Tuner (FT2232) gefunden%s." %
                 ("" if first else " - Bedienelemente reaktiviert"))
    else:
        txt = ("Pi nicht erreichbar" if status == "pi" else "Tuner (FT2232) nicht gefunden")
        lbl_lmp4_warn.config(text="LongMynd P4: %s - gesperrt" % txt)
        _scan_active["longmynd_p4"] = False
        _update_scan_button("longmynd_p4")
        if changed:
            olog("LongMynd P4: %s (%s) - Spalte gesperrt, naechster Versuch in 1 min." % (txt, detail))
    _longmyndp4_apply_state()

def _longmyndp4_tuner_loop():
    while True:
        status, detail = _longmyndp4_tuner_check()
        root.after(0, _longmyndp4_tuner_apply, status, detail)
        time.sleep(60)

_target_dispatch_t = {}      # target -> Zeitpunkt des letzten Umschaltbefehls der GUI
_lm_ext = {}                 # target -> {"last": MHz, "changes": [Zeitpunkte], "warn_t": ...}

def _lm_external_tune_tick():
    """Erkennt, wenn ein LongMynd von AUSSEN umgestimmt wird (z.B. 'Tuning Mode: Scan' im LongMynd-Webinterface
    bzw. BATC-Wideband-Viewer, oder ein zweites Programm): die vom Tuner gemeldete Frequenz springt mehrfach,
    ohne dass die GUI etwas geschickt hat. Folge waeren dauernde Player-Resyncs und ein blockierter Scan.
    Die GUI kann den Scan im Browser nicht abschalten - sie meldet es deutlich (hoechstens alle 2 min)."""
    now = time.time()
    for t in _LM_TARGETS:
        if t not in _active_targets:
            continue
        rf = _lm_measured_rf_mhz(t)
        if rf is None:
            continue
        d = _lm_ext.setdefault(t, {"last": rf, "changes": [], "warn_t": 0.0})
        if abs(rf - d["last"]) > 0.15:
            d["last"] = rf
            # nur zaehlen, wenn die GUI in den letzten 12 s NICHT selbst umgeschaltet hat (auch kein eigener Scan)
            if now - _target_dispatch_t.get(t, 0.0) > 12.0 and not _scan_active.get(t):
                d["changes"].append(now)
        d["changes"] = [x for x in d["changes"] if now - x < 90.0]
        if len(d["changes"]) >= 3 and now - d["warn_t"] > 120.0:
            d["warn_t"] = now
            olog("ACHTUNG %s: wird von AUSSEN umgestimmt (%d Frequenzwechsel in 90 s ohne Befehl der GUI, zuletzt "
                 "%.3f MHz) - laeuft im LongMynd-Webinterface 'Tuning Mode: Scan' oder ein anderes Programm? "
                 "Bitte dort auf 'Click' stellen." % (_TARGET_LABEL.get(t, t), len(d["changes"]), rf))
    root.after(1000, _lm_external_tune_tick)

def target_dispatch(target, freq, sr, key=None):
    """Klick auf einen der Signal-Knoepfe (oder Automatik-Zuteilung): schickt Frequenz/Symbolrate
    an genau EIN Ziel (Octagon+VLC, LongMynd oder lokaler rtl_sdr) - unabhaengig davon, welche
    weiteren Ziele gerade ebenfalls aktiv sind (dafuer gibt es dann eine eigene Spalte je Ziel).
    key: interner Schluessel des Signals in _batc_sig_state, fuer die Automatik-Frei-Erkennung
    (None bei der Bake-Zeile - die zaehlt nicht als "gefundener Kanal")."""
    if not _target_available(target):
        olog("%s: Tuner nicht verfuegbar - Umschalten uebersprungen." % _TARGET_LABEL.get(target, target))
        return
    if target in ("longmynd", "longmynd2") and not _is_direct_freq(freq):
        # QO-100-Signal (Knopf/Bake/Automatik): LNB haengt an Port 1 - steht longmynd auf Port 2, auf Port 1 zurueck.
        # Nur wenn wirklich Port 2 gelesen wird (T-Befehl loest im Tuner eine Neu-Initialisierung aus).
        def _rf_back(t=target):
            if _lm_rfport_read(t) == 1:
                olog("%s: QO-100-Signal gewaehlt, Tuner stand auf Port 2 - schalte auf Port 1." % _TARGET_LABEL.get(t, t))
                _lm_rfport_send(t, 0)
        threading.Thread(target=_rf_back, daemon=True).start()
    cur = _target_cur_freq.get(target)
    tol_hz = cliargs.octagon_umschalttoleranz if target == "octagon" else cliargs.umschaltToleranz
    # LongMynd P4 bekommt das Signal IMMER erneut gesendet (kein "schon dort"-Ueberspringen) -
    # damit ein z.B. nach einem Absturz/Reboot nicht mehr laufender longmynd-Prozess dort
    # zuverlaessig ueber das naechste Signal wieder gestartet wird (siehe send_to_longmynd_p4).
    already_there = (target != "longmynd_p4" and cur is not None
                      and abs((cur - freq) * 1e6) <= tol_hz)

    if key is not None:
        _target_last_tuned[target] = key
    _target_dispatch_t[target] = time.time()
    _vlog("eingestellt", freq, sr, _TARGET_LABEL.get(target, target), "",
          ("Automatik/Verteilung" if key is not None else "Klick/Scan/Web") + ("" if not already_there else ", stand schon dort"))
    freq_changed = (cur is None) or (abs((cur - freq) * 1e6) > tol_hz)
    _prev_sr = _target_cur_sr.get(target)
    _target_cur_freq[target] = freq
    _target_cur_sr[target] = sr
    if _prev_sr is not None and sr is not None and int(_prev_sr) != int(sr):
        # andere Datenrate: der Player braucht ein echtes Stop/Start (Pause/Play reicht nicht) -
        # gilt fuer alle Resyncs der naechsten 90 s (Rufzeichen-/FEC-Wechsel kommen erst spaeter)
        _resync_hard_until[target] = time.time() + 180.0
    if freq_changed and target in _target_callsign_var:
        # Rufzeichen-Knopf sofort leeren - das evtl. noch angezeigte alte Rufzeichen gehoerte
        # zum vorherigen Kanal und darf nicht so wirken, als sei es schon auf dem neuen
        # Kanal decodiert worden (das im Spektrum gemerkte Rufzeichen bleibt davon unberuehrt,
        # siehe _spectrum_remember_callsign/_spectrum_known_callsigns - das haengt an der
        # Frequenz, nicht am Ziel).
        root.after(0, _set_callsign_display, target, None)
    if freq_changed and target in _UDPRX_TARGETS:
        _callsign_block_until[target] = time.time() + 15.0
    if freq_changed and target == "octagon":
        # altes Rufzeichen darf nicht zurueckkommen, bis der neue Kanal anliegt und neu erkannt wurde
        _callsign_block_until["octagon"] = time.time() + (60.0 if _oct_direct() else 12.0)

    if already_there:
        olog("%s steht schon auf %.3f MHz (Toleranz %g Hz) - kein Umschalten" %
             (_TARGET_LABEL.get(target, target), freq, tol_hz))
        return

    if freq_changed and not (target == "octagon" and _oct_direct()) and target not in _UDPRX_TARGETS:
        _vlc_resync(target, "Frequenz-/Kanalwechsel")
    # (Octagon-Direkt: Resync erst NACH dem Abstimmen, siehe _oct_direct_tune/_oct_player_sync - das TUNE
    #  dauert 5-8 s, ein Pause/Play davor laeuft ins Leere.)

    _verteiler_log(target, freq, sr)

    if target == "octagon":
        if _oct_direct():
            threading.Thread(target=lambda: _oct_direct_tune(freq, sr), daemon=True).start()
        else:
            octagon_ensure_channels()
            threading.Thread(target=lambda: process_signal({"freq": freq, "sr": sr}, _octagon_channels, cliargs),
                              daemon=True).start()
    elif target == "longmynd":
        lo_khz = int(round(_target_lo_mhz(target, freq) * 1000))
        threading.Thread(target=lambda: send_to_longmynd(freq, sr, cliargs, lo_khz), daemon=True).start()
    elif target == "longmynd2":
        lo_khz = int(round(_target_lo_mhz(target, freq) * 1000))
        _ip2, _port2 = _longmynd2_endpoint()
        _url2 = "ws://%s:%d" % (_ip2, _port2)
        threading.Thread(target=lambda: send_to_longmynd(freq, sr, cliargs, lo_khz, _url2, "LongMynd 2"),
                          daemon=True).start()
    elif target == "longmynd_p4":
        threading.Thread(target=lambda: send_to_longmynd_p4(freq, sr), daemon=True).start()
    elif target in _ONAIR_TARGETS:
        threading.Thread(target=_onair_tune, args=(target, freq, sr), daemon=True).start()
    elif target in _DATVRX_TARGETS:
        threading.Thread(target=_dvx_start, args=(target, freq, sr), daemon=True).start()
    else:
        batc_take(freq, sr)

#===== Scan-Knopf je Ziel: faehrt mit diesem Tuner alle aktuell im Spektrum sichtbaren Signale =====
#===== (ausser der Bake) der Reihe nach an, wartet kurz auf ein decodiertes Rufzeichen, merkt   =====
#===== es sich fuer den jeweiligen Peak, und springt danach zur Ausgangsfrequenz zurueck.       =====
_SCAN_DECODE_TIMEOUT = 8.0   # Sekunden Wartezeit je Peak auf ein decodiertes Rufzeichen
_SCAN_SKIP_KNOWN_MAXAGE = 120.0   # Sekunden - siehe _scan_target: frisch genug bekannte Rufzeichen
                                   # werden beim Scan uebersprungen, veraltete/unsichere erneut geprueft
_SCAN_LOCK_TIMEOUT = 10.0         # Sekunden: so lange auf einen Lock warten (LongMynd-Statusstrom)
_SCAN_NAME_AFTER_LOCK = 20.0      # Sekunden: nach dem Lock so lange auf den Service-Namen warten
_SCAN_NOLOCK_COOLDOWN = 150.0     # Sekunden: Signal ohne Lock beim Auto-Scan nicht erneut versuchen
_scan_nolock_until = {}           # {freq_mhz: Zeitpunkt} - Signale, die zuletzt nie gelockt haben
_ascan_pending = [False]          # waehrend eines Scans kam ein neues Signal -> danach nochmal scannen
_scan_active = {t: False for t in _active_targets}
_scan_buttons = {}   # target -> Button (fuer Text/Farbe waehrend des Scans)
_ascan_active = {t: False for t in _active_targets}   # Auto-Scan: bei jedem neuen Signal selbststaendig scannen
_ascan_buttons = {}   # target -> Button (fuer Farbe, ob Auto-Scan an/aus)

def _update_scan_button(target):
    btn = _scan_buttons.get(target)
    if btn is None:
        return
    if _scan_active.get(target):
        btn.configure(text="Scan: Stop", style="RecOn.TButton")
    else:
        btn.configure(text="Scan", style="TButton")

def _update_ascan_button(target):
    btn = _ascan_buttons.get(target)
    if btn is None:
        return
    btn.configure(style="RecOn.TButton" if _ascan_active.get(target) else "TButton")

def _toggle_ascan(target):
    """AScan-Knopf: schaltet Auto-Scan fuer dieses Ziel an/aus - bei "an" startet batc_tick()
    automatisch einen Scan, sobald ein NEUES Signal im Spektrum auftaucht (siehe dortigen Hook)."""
    _ascan_active[target] = not _ascan_active.get(target)
    _update_ascan_button(target)
    olog("%s: Auto-Scan %s." % (_TARGET_LABEL.get(target, target),
                                 "eingeschaltet" if _ascan_active[target] else "ausgeschaltet"))

#===== Checkbox "FEC von LongMynd" (nur rtl_sdr): reines Setzen einer Option, wie "Offset       =====
#===== korrigieren" - Anhaken macht fuer sich allein nichts. Erst der naechste tatsaechliche    =====
#===== Start (on_start()) schaltet dann LongMynd kurz auf die rtl_sdr-Frequenz, liest die        =====
#===== gemessene genaue RF-Frequenz + FEC/MODCOD ab, uebernimmt beides und startet danach erst   =====
#===== die eigentliche rtl_sdr-Pipeline (_on_start_real()). Der Haken bleibt bestehen, bis der    =====
#===== Nutzer ihn selbst wieder abhakt - wirkt also bei jedem weiteren Start erneut.             =====
_FEC_FRACTION_RE = re.compile(r"(\d+/\d+)$")
_FEC_SYNC_TIMEOUT = 8.0   # Sekunden Wartezeit auf FEC/Frequenz-Messung von LongMynd
_fec_sync_var = None   # tk.BooleanVar, siehe GUI-Aufbau weiter unten (_target == "rtl_sdr")

def _fec_sync_from_longmynd_then_start():
    """Wird von on_start() anstelle eines direkten _on_start_real() aufgerufen, wenn die Checkbox
    'FEC von LongMynd' angehakt ist - lagert LongMynd kurz um, wartet asynchron (eigener Thread,
    damit die GUI dabei nicht einfriert) auf eine FEC/Frequenz-Messung und ruft danach selbst
    _on_start_real() auf (im Hauptthread, per root.after)."""
    if "longmynd" not in _active_targets:
        olog("FEC von LongMynd: LongMynd ist nicht aktiv (fehlendes Flag beim Programmstart) - "
             "starte ohne FEC-Abgleich.")
        _apply_offset_correction_if_requested()
        _on_start_real()
        return
    try:
        want_freq = round(float(frequency.get()), 3)
        want_sr = int(symbolrate.get())
    except (TypeError, ValueError):
        olog("FEC von LongMynd: ungueltige rtl_sdr-Frequenz/Symbolrate - starte ohne Abgleich.")
        _apply_offset_correction_if_requested()
        _on_start_real()
        return

    orig_freq = _target_cur_freq.get("longmynd")
    orig_sr = _target_cur_sr.get("longmynd")
    orig_key = _target_last_tuned.get("longmynd")

    def worker():
        try:
            olog("FEC von LongMynd: schalte LongMynd kurz auf %.3f MHz (%d kS/s) ..." % (want_freq, want_sr))
            root.after(0, target_dispatch, "longmynd", want_freq, want_sr, None)
            deadline = time.time() + _FEC_SYNC_TIMEOUT
            fec_name = None
            rf = None
            state_id = None
            while time.time() < deadline:
                time.sleep(0.3)
                rf = _lm_measured_rf_mhz("longmynd")
                fec_name = _lm_last_fec.get("longmynd")
                with _lm_status_lock:
                    vals = dict(_lm_status_vals.get("longmynd", {}))
                try:
                    state_id = int(vals.get(1))
                except (TypeError, ValueError):
                    state_id = None
                if rf is not None and fec_name is not None:
                    break
            if rf is None or fec_name is None:
                olog("FEC von LongMynd: kein Lock/keine FEC innerhalb von %.0fs - starte rtl_sdr "
                     "mit unveraenderten Werten." % _FEC_SYNC_TIMEOUT)
                return
            m = _FEC_FRACTION_RE.search(fec_name)
            fraction = m.group(1) if m else None
            std = "DVB-S" if state_id == 3 else "DVB-S2"
            olog("FEC von LongMynd: %.4f MHz, FEC %s (%s) gemessen - uebernehme fuer rtl_sdr."
                 % (rf, fec_name, std))

            def apply():
                frequency.set(round(rf, 3))
                standard.set(std)
                # Bandbreite passend zur (bei LongMynd ja unveraendert gebliebenen) Symbolrate
                # nachziehen - dieselbe Tabelle/derselbe Faktor (~1,6x) wie beim BATC-Klick in
                # batc_take(), damit rtl_sdr nicht mit einer zu dieser Symbolrate unpassenden
                # (z.B. noch von einem ganz anderen Signal stammenden) Bandbreite neu startet.
                bandwidth.set(QO100_BW_BY_SR.get(want_sr, want_sr * 2))
                if fraction:
                    fec.set(fraction)
                else:
                    olog("FEC von LongMynd: FEC-Bezeichnung '%s' nicht als Bruch erkannt - FEC "
                         "bleibt unveraendert." % fec_name)
            root.after(0, apply)
        finally:
            if orig_freq is not None and orig_sr is not None:
                root.after(0, target_dispatch, "longmynd", orig_freq, orig_sr, orig_key)
                olog("FEC von LongMynd: LongMynd zurueck auf %.3f MHz." % orig_freq)
            def _finish():
                _apply_offset_correction_if_requested()
                _on_start_real()
            root.after(50, _finish)

    threading.Thread(target=worker, daemon=True).start()

def on_start():
    """Oeffentlicher Start-Einstieg (Start-Knopf, Bake-/Scan-Klicks, Snapshot-Resync, ...): prueft
    die beiden Options-Checkboxen (FEC von LongMynd / Offset korrigieren, siehe oben) und wendet
    sie bei Bedarf an, bevor die eigentliche Pipeline (_on_start_real()) angestossen wird. Beide
    Checkboxen bleiben nach dem Start angehakt - sie sind Dauereinstellungen, keine Einmalaktionen."""
    if _fec_sync_var is not None and _fec_sync_var.get():
        _fec_sync_from_longmynd_then_start()
        return
    _apply_offset_correction_if_requested()
    _on_start_real()

#===== Checkbox "Offset korrigieren" (nur rtl_sdr): reines Setzen einer Option - macht beim    =====
#===== Anhaken selbst NICHTS, keine sofortige Aktion/kein Neustart. Erst der naechste           =====
#===== tatsaechliche Start (on_start(), z.B. per Start-Knopf oder einem der anderen Knoepfe,    =====
#===== die intern on_start() aufrufen) rechnet dann den zuletzt von leandvb per --fd-info        =====
#===== gemessenen Traegerversatz (FREQ/"Offset", siehe _leandvb_info oben) einmalig in die       =====
#===== rtl_sdr-Frequenz ein, bevor die Pipeline gestartet wird (siehe Anfang von on_start()).     =====
_offset_correct_var = None   # tk.BooleanVar, siehe GUI-Aufbau weiter unten (_target == "rtl_sdr")

def _apply_offset_correction_if_requested():
    """Wird ganz am Anfang von on_start() aufgerufen: solange die Checkbox 'Offset korrigieren'
    angehakt ist, wird bei JEDEM Start der zuletzt gemessene Offset in die Frequenz eingerechnet
    (bleibt also an, bis der Nutzer sie wieder abhakt - kein einmaliges Selbst-Abhaken, da die
    Korrektur bewusst erst beim naechsten Start passieren soll, nicht sofort beim Anhaken)."""
    if _offset_correct_var is None or not _offset_correct_var.get():
        return
    with _leandvb_info_lock:
        offset_khz = _leandvb_info.get("offset_khz")
    if offset_khz is None:
        olog("Offset korrigieren: kein gemessener Offset-Wert vorhanden (leandvb lief seit dem "
             "letzten Stop nicht lange genug) - Frequenz bleibt unveraendert.")
        return
    try:
        cur_freq = float(frequency.get())
    except (TypeError, ValueError):
        return
    new_freq = round(cur_freq + offset_khz / 1000.0, 3)
    olog("Offset korrigieren: %.3f MHz, zuletzt gemessener Offset %+.3f kHz -> neue Frequenz %.3f MHz."
         % (cur_freq, offset_khz, new_freq))
    frequency.set(new_freq)

#===== SigReport-Knopf (nur LongMynd/LongMynd P4): baut denselben Signal-Report wie Taste "R" =====
#===== in auswertung_4002.sh (build_report()) aus den schon bekannten Status-UDP-Werten        =====
#===== (_lm_status_vals, Port 4002/4012) und kopiert ihn in die Zwischenablage.                =====
def _copy_to_clipboard(text):
    for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]):
        if shutil.which(cmd[0]):
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                p.communicate(input=text.encode("utf-8"), timeout=3)
                return True
            except Exception:
                pass
    # Fallback: Tk-eigene Zwischenablage, falls kein xclip/xsel/wl-copy installiert ist - haelt
    # den Inhalt aber nur, solange die GUI offen bleibt (X11-Eigenheit, wie bei xclip ohne -d).
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        return True
    except Exception:
        return False

def _signal_report_copy(target):
    with _lm_status_lock:
        vals = dict(_lm_status_vals.get(target, {}))
    if not vals:
        olog("%s: SigReport - noch keine Statusdaten vom Port 4002/4012 empfangen."
             % _TARGET_LABEL.get(target, target))
        return

    def _de(fmt, x):
        return (fmt % x).replace(".", ",")

    svc = vals.get(13) or ""
    prov = vals.get(14) or ""
    who = (svc + "/" + prov) if (svc and prov) else (svc or prov)
    _mer2, dwert, _codec2 = _lm_extra_fields(target)
    dpart = ("D" + _de("%.1f", dwert)) if dwert is not None else "D"
    report = "SigReport: %s - %s" % (who, dpart)
    try:
        mer = float(vals.get(12)) / 10.0
        report += " (%s dB)" % _de("%.1f", mer)
    except (TypeError, ValueError):
        pass
    try:
        srk = float(vals.get(9)) / 1000.0
        report += " - %d Ks" % round(srk)
    except (TypeError, ValueError):
        pass
    rf = _lm_measured_rf_mhz(target)
    if rf is not None:
        report += " - %s" % _de("%.2f", rf)
    # Zusatz ganz am Ende, wie REPORT_SUFFIX in auswertung_4002.sh - hier konfigurierbar ueber
    # app_config["report_suffix"] (siehe _spectrum_settings_open()), Default "1.8m Dish".
    suffix = app_config.get("report_suffix", "").strip()
    if suffix:
        report += " - " + suffix

    ok = _copy_to_clipboard(report)
    olog("%s: %s%s" % (_TARGET_LABEL.get(target, target), report,
                        "  (in Zwischenablage kopiert)" if ok else
                        "  (keine Zwischenablage verfuegbar - xclip/xsel/wl-copy fehlt)"))

_ascan_global = [False]     # genereller AScan-Knopf (oben): bei neuem Signal scannt irgendein freier Tuner

def _scan_target_busy(t):
    """Tuner steht gerade auf einem aktiven (nicht-Baken-)Signal der BATC-Liste."""
    cf = _target_cur_freq.get(t)
    if cf is None or abs(cf - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
        return False
    return _scan_signal_alive_near(cf)

def _scan_pick_target():
    """Welcher Tuner scannt beim generellen AScan: ein freier Tuner (LongMynd-Ziele zuerst, dann Octagon
    im Direktmodus); sind alle belegt, ein LongMynd (der danach wieder auf sein Signal zurueckschaltet).
    None, wenn gerade schon gescannt wird oder kein geeigneter Tuner aktiv ist."""
    if _scan_other_running(None):
        return None
    # generelles AScan ist Automatik: nur Tuner mit 'Verteil Automatik' + 'VerteilA'
    lm = [t for t in _LM_TARGETS if t in _active_targets and _target_available(t) and _verteil_ok(t)]
    others = [t for t in ("octagon",) if t in _active_targets and _target_available(t) and _oct_direct()
              and _verteil_ok(t)]
    for t in lm + others:
        if not _scan_target_busy(t):
            return t
    return lm[0] if lm else None

CALLCHECK_INTERVAL_S = 90
CALLCHECK_RETRY_S = 600
_callcheck_tried = {}     # freq -> Zeitpunkt des letzten Pruef-Scans

def _callsign_check_tick():
    """Auch ohne neue BATC-Signale: haben alle Signale im Spektrum ein Rufzeichen? Wenn nein, scannt
    LongMynd oder LongMynd 2 (ein freier zuerst) genau die Signale ohne Rufzeichen."""
    try:
        if app_config.get("callsign_check", True) and not _scan_other_running(None):
            with _batc_lock:
                live = sorted(st["freq"] for st in _batc_sig_state.values()
                              if st["gone_since"] is None and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5
                              and time.time() - st.get("born", 0) > 20)
            now = time.time()
            def _cool(f):
                return any(until > now and abs(ff - f) <= BATC_SIG_TOL for ff, until in list(_scan_nolock_until.items()))
            for ff in [ff for ff, t0 in _callcheck_tried.items() if now - t0 > CALLCHECK_RETRY_S]:
                _callcheck_tried.pop(ff, None)
            def _tried(f):          # schon versucht (z.B. Lock, aber kein Name gesendet): erst nach 10 min wieder
                return any(abs(ff - f) <= BATC_SIG_TOL for ff in _callcheck_tried)
            missing = [f for f in live if not _call_for_freq(f) and not _cool(f) and not _tried(f)]
            if missing:
                cands = [t for t in ("longmynd", "longmynd2") if t in _active_targets and _target_available(t)
                         and not _scan_active.get(t) and _verteil_ok(t)]     # nur mit Verteil Automatik + VerteilA
                free = [t for t in cands if not _scan_target_busy(t)]
                tgt = (free or cands or [None])[0]
                if tgt:
                    olog("Rufzeichen-Pruefung: %d Signal(e) ohne Rufzeichen (%s) -> Scan mit %s." %
                         (len(missing), ", ".join("%.3f" % f for f in missing), _TARGET_LABEL.get(tgt, tgt)))
                    for f in missing:
                        _callcheck_tried[f] = now
                    _start_scan(tgt, only_freqs=missing)
    except Exception as e:
        dlog("Rufzeichen-Pruefung: %r" % (e,))
    root.after(CALLCHECK_INTERVAL_S * 1000, _callsign_check_tick)

def _ascan_global_toggle():
    _ascan_global[0] = not _ascan_global[0]
    try:
        btn_batc_ascan.configure(style="RecOn.TButton" if _ascan_global[0] else "TButton")
    except NameError:
        pass
    olog("Generelles AScan %s." % ("eingeschaltet - bei neuem Signal scannt ein freier Tuner (sonst LongMynd)"
                                    if _ascan_global[0] else "ausgeschaltet"))

def _scan_other_running(target):
    """Name des Ziels, auf dem gerade ein ANDERER Scan laeuft (es scannt immer nur ein Tuner)."""
    for t, act in _scan_active.items():
        if act and t != target:
            return t
    return None

def _scan_target(target):
    """Scan-Knopf: nochmaliger Klick waehrend eines laufenden Scans bricht ihn ab (gleiche
    Funktion dient als Start/Stop-Umschalter, siehe _update_scan_button). Startet sonst einen
    Scan ueber _start_scan() - einmal das Band mit genau diesem Tuner."""
    if _scan_active.get(target):
        olog("%s: Scan wird abgebrochen ..." % _TARGET_LABEL.get(target, target))
        _scan_active[target] = False
        return
    other = _scan_other_running(target)
    if other:
        olog("%s: Scan nicht gestartet - es scannt gerade %s (immer nur ein Tuner)." %
             (_TARGET_LABEL.get(target, target), _TARGET_LABEL.get(other, other)))
        return
    _start_scan(target, manual=True)

def _scan_signal_alive_near(freq):
    """True, wenn nahe dieser Frequenz ein aktives (nicht verschwundenes) Signal steht - die Bake
    zaehlt immer als aktiv (sie steht nie in der BATC-Liste)."""
    if freq is None:
        return False
    if abs(freq - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
        return True
    with _batc_lock:
        return any(st["gone_since"] is None and abs(st["freq"] - freq) <= BATC_SIG_TOL
                   for st in _batc_sig_state.values())

def _scan_lm_state(target, freq):
    """Fuer LongMynd-Ziele mit schnellem Statusstrom: True, wenn der Tuner JETZT auf genau dieser
    Frequenz dekodiert (State locked, MER > 0, gemessene Frequenz passt). Nur dann gehoert ein im
    Statusstrom stehender Service-Name wirklich zu diesem Signal (sonst: alter Name vom Vorgaenger)."""
    if not _lm_decoding(target):
        return False
    rf = _lm_measured_rf_mhz(target)
    return rf is not None and abs(rf - freq) <= 0.15

def _start_scan(target, manual=False, only_freqs=None):
    """Eigentlicher Scan-Start, getrennt von _scan_target()'s Stop-Umschaltung. Es laeuft immer nur
    EIN Scan gleichzeitig (ein Tuner). Auto-Scan (manual=False) ruft hier bei neuem Signal auf;
    laeuft schon ein Scan, wird nur vermerkt, dass danach noch einmal gescannt werden soll."""
    if not _target_available(target):
        return
    running = _scan_other_running(None)
    if running or _scan_active.get(target):
        if not manual:
            _ascan_pending[0] = True      # neues Signal waehrend des Scans -> danach noch ein Durchlauf
        return

    with _batc_lock:
        sigs = [(k, dict(st)) for k, st in _batc_sig_state.items()
                if abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5 and st["gone_since"] is None]
    sigs.sort(key=lambda kv: kv[1]["freq"])
    if not sigs:
        olog("%s: kein gueltiges Signal im Spektrum gefunden - Scan nicht gestartet." %
             _TARGET_LABEL.get(target, target))
        return

    # Nur unbekannte (bzw. nicht mehr sicher aktuelle) Peaks scannen - wo das Rufzeichen schon
    # frisch genug bekannt ist (_spectrum_callsign_cache, < _SCAN_SKIP_KNOWN_MAXAGE alt), ist
    # ein erneuter Scan unnoetig.
    now = time.time()
    def _known_recent(freq):
        for f, (name, _s, ts) in _spectrum_callsign_cache.items():
            if name and abs(f - freq) <= _SPECTRUM_CALLSIGN_TOL:
                return (now - ts) < _SCAN_SKIP_KNOWN_MAXAGE
        return False
    def _cooling_down(freq):
        # Auto-Scan: Signale, die zuletzt nie gelockt haben (z.B. schmale Stoerer), nicht gleich wieder
        for f, until in list(_scan_nolock_until.items()):
            if until < now:
                _scan_nolock_until.pop(f, None)
            elif abs(f - freq) <= BATC_SIG_TOL:
                return True
        return False
    sigs_all = sigs
    if only_freqs:
        # Rufzeichen-Pruefung: genau diese Signale (im Spektrum ohne Rufzeichen), Pausen nach "kein Lock" beachten
        sigs = [(k, st) for k, st in sigs_all
                if any(abs(st["freq"] - f) <= BATC_SIG_TOL for f in only_freqs) and not _cooling_down(st["freq"])]
    else:
        sigs = [(k, st) for k, st in sigs_all
                if not _known_recent(st["freq"]) and (manual or not _cooling_down(st["freq"]))]
    skipped = len(sigs_all) - len(sigs)
    if not sigs:
        olog("%s: alle %d Signal(e) bekannt oder pausiert - Scan nicht noetig." %
             (_TARGET_LABEL.get(target, target), len(sigs_all)))
        return

    # Ausgangszustand: nur zurueckschalten, wenn dort ein aktives Signal stand
    orig_freq = _target_cur_freq.get(target)
    orig_sr = _target_cur_sr.get(target)
    orig_key = _target_last_tuned.get(target)
    orig_was_active = _scan_signal_alive_near(orig_freq)

    _scan_active[target] = True
    _ascan_pending[0] = False
    _update_scan_button(target)

    def worker():
        label = _TARGET_LABEL.get(target, target)
        try:
            olog("%s: Scan gestartet - %d Signal(e)%s." %
                 (label, len(sigs),
                  (" (%d bekannt/pausiert uebersprungen)" % skipped) if skipped else ""))
            fast = (target in _LM_TARGETS and
                    app_config.get("lm_callsign_source") == "longmynd_udp")
            poll_interval = 0.2 if fast else 1.0
            for key, st in sigs:
                if not _scan_active.get(target):
                    olog("%s: Scan abgebrochen." % label)
                    break
                freq, sr = st["freq"], st["sr"]
                if not _scan_signal_alive_near(freq):
                    continue                      # Signal ist inzwischen verschwunden
                root.after(0, lambda t=target, f=freq, s=sr, k=key: target_dispatch(t, f, s, k))
                time.sleep(1.0)                   # Umschalten abwarten, alte Statuswerte verfallen lassen
                name = None
                t0 = time.time()
                lock_at = None
                while _scan_active.get(target):
                    now2 = time.time()
                    if fast:
                        if lock_at is None and _scan_lm_state(target, freq):
                            lock_at = now2
                        if lock_at is None and now2 - t0 > _SCAN_LOCK_TIMEOUT:
                            break                 # kein Lock -> naechstes Signal
                        if lock_at is not None and now2 - lock_at > _SCAN_NAME_AFTER_LOCK:
                            break                 # gelockt, aber kein Name
                    elif now2 - t0 > _SCAN_DECODE_TIMEOUT:
                        break
                    time.sleep(poll_interval)
                    if fast and not _scan_lm_state(target, freq):
                        continue                  # Name nur zaehlen, solange wirklich dekodiert wird
                    name = _poll_service_name_once(target)
                    if name:
                        break
                if name:
                    root.after(0, _set_callsign_display, target, name)
                    olog("%s: Scan %.3f MHz -> %s" % (label, freq, name))
                else:
                    if fast and lock_at is None:
                        _scan_nolock_until[freq] = time.time() + _SCAN_NOLOCK_COOLDOWN
                        olog("%s: Scan %.3f MHz -> kein Lock." % (label, freq))
                    else:
                        olog("%s: Scan %.3f MHz -> kein Rufzeichen decodiert." % (label, freq))
        finally:
            # zurueck auf das Signal von vorher - aber nur, wenn es ein aktives Signal war und
            # es das jetzt noch ist (sonst uebernimmt die Automatik-Verteilung)
            taken = any(t2 != target and _target_cur_freq.get(t2) is not None and
                        abs(_target_cur_freq[t2] - (orig_freq or 0)) <= BATC_SIG_TOL and
                        abs((orig_freq or 0) - BATC_BEACON_NOMINAL_MHZ) > 0.5
                        for t2 in _active_targets)
            if (orig_freq is not None and orig_sr is not None and orig_was_active
                    and _scan_signal_alive_near(orig_freq) and not taken):
                root.after(0, lambda t=target, f=orig_freq, s=orig_sr, k=orig_key:
                           target_dispatch(t, f, s, k))
                olog("%s: Scan beendet - zurueck auf %.3f MHz." % (label, orig_freq))
            else:
                _target_last_tuned[target] = None
                olog("%s: Scan beendet (vorheriges Signal nicht mehr aktiv - Automatik uebernimmt)." % label)
            _scan_active[target] = False
            root.after(0, _update_scan_button, target)
            # Auto-Scan: waehrend des Durchlaufs ist ein neues Signal aufgetaucht -> noch einmal
            if _ascan_pending[0] and _ascan_global[0]:
                _ascan_pending[0] = False
                def _again():
                    _gt2 = _scan_pick_target()
                    if _gt2:
                        _start_scan(_gt2)
                root.after(1500, _again)
            elif _ascan_pending[0] and _ascan_active.get(target):
                _ascan_pending[0] = False
                root.after(1500, lambda t=target: _start_scan(t))

    threading.Thread(target=worker, daemon=True).start()

#===== favorite presets (komplette Parametersaetze, z.B. "QO-100 Bake") =======

presets = dict()

def presets_load_all():
    global presets
    if os.path.isfile(presets_file):
        file = open(presets_file, "r")
        presets = json.load(file)
        file.close()
    else:
        presets = dict()

def presets_save_all():
    file = open(presets_file, "w")
    file.write(json.dumps(presets, indent=4, sort_keys=True))
    file.close()

def presets_refresh_combobox():
    names = sorted(presets.keys())
    cmb_preset["values"] = names
    if preset.get() not in names:
        preset.set("")

def preset_save():
    parameters_save()                    # aktuelle GUI-Werte nach "parameters" uebernehmen
    name = simpledialog.askstring("Preset speichern", "Name fuer dieses Preset:", parent=root, initialvalue=preset.get())
    if not name:
        return
    presets[name] = dict(parameters)
    presets_save_all()
    presets_refresh_combobox()
    preset.set(name)
    print( "preset gespeichert:", name)

def preset_apply(name=None):
    global parameters
    if name is None:
        name = preset.get()
    if name not in presets:
        return
    parameters = dict(presets[name])
    guivars_init()
    preset.set(name)
    print( "preset geladen:", name)

def on_preset_selected(event=None):
    preset_apply(preset.get())

def on_qo100_selected(event=None):
    info = QO100_INFO_BY_LABEL.get(qo100.get())
    if info is None:
        return
    f, sr = info
    frequency.set(f)
    symbolrate.set(sr)
    bandwidth.set(QO100_BW_BY_SR.get(sr, sr * 2))
    fec.set("auto")

def preset_delete():
    name = preset.get()
    if name not in presets:
        return
    if not messagebox.askyesno("Preset loeschen", "Preset \"" + name + "\" wirklich loeschen?", parent=root):
        return
    del presets[name]
    presets_save_all()
    presets_refresh_combobox()
    print( "preset geloescht:", name)

#===== settings dialog ========================================================

def _ds_tab_files(tab_files, on_leandvb_path, on_leandvb_file, on_ldpchelper_path, on_ldpchelper_file, on_rtlsdr_path, on_rtlsdr_file, on_viewer_path, on_viewer_file):
    """Settings-Dialog, Reiter 'files' (aus dlg_settings ausgelagert)"""

   #----- tab_files -----
    tab_files.columnconfigure((0,1),   pad=4, weight=1)
    tab_files.rowconfigure   ((0,1),   pad=4, weight=0)
    tab_files.rowconfigure   ((2,3,4), pad=4, weight=1)

        #----- label -----
    lbl_files = ttk.Label(tab_files, text="Setting of files and directories")
    lbl_files.grid (row=0, column=0, columnspan=2)

    lbl_files_separator = Frame (tab_files, height=1, bg="grey")
    lbl_files_separator.grid (row=1, column=0, columnspan=4, sticky=EW)

        #----- frame leandvb -----
    frm_files_leandvb = ttk.LabelFrame (tab_files, text="leandvb", borderwidth=2, padding=4)
    frm_files_leandvb.grid (row=2, column=0)

    lbl_leandvb_path = ttk.Label(frm_files_leandvb, text="Path : ")
    ent_leandvb_path = ttk.Entry(frm_files_leandvb, width=30, textvariable=leandvb_path)
    btn_leandvb_path = Button(frm_files_leandvb, padx=0, pady=0, text="...", command=on_leandvb_path)
    lbl_leandvb_path.grid (row=0, column=0, sticky=W)
    ent_leandvb_path.grid (row=0, column=1, sticky=W)
    btn_leandvb_path.grid (row=0, column=2)

    lbl_leandvb_file = ttk.Label(frm_files_leandvb, text="File : ")
    ent_leandvb_file = ttk.Entry(frm_files_leandvb, width=30, textvariable=leandvb_file)
    btn_leandvb_file = Button(frm_files_leandvb, padx=0, pady=0, text="...", command=on_leandvb_file)
    lbl_leandvb_file.grid (row=1, column=0, sticky=W)
    ent_leandvb_file.grid (row=1, column=1, sticky=W)
    btn_leandvb_file.grid (row=1, column=2)

        #----- frame LDCP helper -----
    frm_files_ldcphelper = ttk.LabelFrame (tab_files, text="LDCP helper", borderwidth=2, padding=4)
    frm_files_ldcphelper.grid (row=3, column=0)

    lbl_ldpchelper_path = ttk.Label(frm_files_ldcphelper, text="Path : ")
    ent_ldpchelper_path = ttk.Entry(frm_files_ldcphelper, width=30, textvariable=ldpchelper_path)
    btn_ldpchelper_path = Button(frm_files_ldcphelper, padx=0, pady=0, text="...", command=on_ldpchelper_path)
    lbl_ldpchelper_path.grid (row=0, column=0, sticky=W)
    ent_ldpchelper_path.grid (row=0, column=1, sticky=W)
    btn_ldpchelper_path.grid (row=0, column=2)

    lbl_ldpchelper_file = ttk.Label(frm_files_ldcphelper, text="File : ")
    ent_ldpchelper_file = ttk.Entry(frm_files_ldcphelper, width=30, textvariable=ldpchelper_file)
    btn_ldpchelper_file = Button(frm_files_ldcphelper, padx=0, pady=0, text="...", command=on_ldpchelper_file)
    lbl_ldpchelper_file.grid (row=1, column=0, sticky=W)
    ent_ldpchelper_file.grid (row=1, column=1, sticky=W)
    btn_ldpchelper_file.grid (row=1, column=2)

        #----- frame rtl_sdr -----
    frm_files_rtlsdr = ttk.LabelFrame (tab_files, text="rtl_sdr", borderwidth=2, padding=4)
    frm_files_rtlsdr.grid (row=2, column=1)

    lbl_rtlsdr_path = ttk.Label(frm_files_rtlsdr, text="Path : ")
    ent_rtlsdr_path = ttk.Entry(frm_files_rtlsdr, width=30, textvariable=rtlsdr_path)
    btn_rtlsdr_path = Button(frm_files_rtlsdr, padx=0, pady=0, text="...", command=on_rtlsdr_path)
    lbl_rtlsdr_path.grid (row=0, column=0, sticky=W)
    ent_rtlsdr_path.grid (row=0, column=1, sticky=W)
    btn_rtlsdr_path.grid (row=0, column=2)

    lbl_rtlsdr_file = ttk.Label(frm_files_rtlsdr, text="File : ")
    ent_rtlsdr_file = ttk.Entry(frm_files_rtlsdr, width=30, textvariable=rtlsdr_file)
    btn_rtlsdr_file = Button(frm_files_rtlsdr, padx=0, pady=0, text="...", command=on_rtlsdr_file)
    lbl_rtlsdr_file.grid (row=1, column=0, sticky=W)
    ent_rtlsdr_file.grid (row=1, column=1, sticky=W)
    btn_rtlsdr_file.grid (row=1, column=2)

        #----- frame viewer -----
    frm_files_viewer = ttk.LabelFrame (tab_files, text="viewer", borderwidth=2, padding=4)
    frm_files_viewer.grid (row=3, column=1)

    lbl_viewer_path = ttk.Label(frm_files_viewer, text="Path : ")
    ent_viewer_path = ttk.Entry(frm_files_viewer, width=30, textvariable=viewer_path)
    btn_viewer_path = Button(frm_files_viewer, padx=0, pady=0, text="...", command=on_viewer_path)
    lbl_viewer_path.grid (row=0, column=0, sticky=W)
    ent_viewer_path.grid (row=0, column=1, sticky=W)
    btn_viewer_path.grid (row=0, column=2)

    lbl_viewer_file = ttk.Label(frm_files_viewer, text="File : ")
    ent_viewer_file = ttk.Entry(frm_files_viewer, width=30, textvariable=viewer_file)
    btn_viewer_file = Button(frm_files_viewer, padx=0, pady=0, text="...", command=on_viewer_file)
    lbl_viewer_file.grid (row=1, column=0, sticky=W)
    ent_viewer_file.grid (row=1, column=1, sticky=W)
    btn_viewer_file.grid (row=1, column=2)

def _ds_tab_rtlsdr(tab_rtlsdr):
    """Settings-Dialog, Reiter 'rtl_sdr' (aus dlg_settings ausgelagert)"""

     #----- tab_rtlsdr -----
    tab_rtlsdr.columnconfigure((0,1),   pad=4, weight=1)
    tab_rtlsdr.rowconfigure   ((0,1,2), pad=4, weight=0)

        #----- label -----
    lbl_rtlsdr = ttk.Label(tab_rtlsdr, text="Settings for rtl_sdr program")
    lbl_rtlsdr.grid (row=0, column=0, columnspan=2)

    lbl_rtlsdr_separator = Frame (tab_rtlsdr, height=1, bg="grey")
    lbl_rtlsdr_separator.grid (row=1, column=0, columnspan=2, sticky=EW)

        #----- frame options -----
    frm_rtlsdr = ttk.Frame(tab_rtlsdr, borderwidth=4, padding=4)
    frm_rtlsdr.grid (row=2, column=0, columnspan=2)

        #----- options -----
    lbl_ppm = ttk.Label(frm_rtlsdr, text="ppm-error")
    ent_ppm = ttk.Entry(frm_rtlsdr, width=10, textvariable=ppm)
    lbl_ppm.grid (row=0, column=0, sticky=W)
    ent_ppm.grid (row=0, column=1, sticky=W)

    lbl_gain = ttk.Label(frm_rtlsdr, text="gain")
    ent_gain = ttk.Entry(frm_rtlsdr, width=10, textvariable=gain)
    lbl_gain.grid (row=1, column=0, sticky=W)
    ent_gain.grid (row=1, column=1, sticky=W)

    lbl_rtldongle = ttk.Label(frm_rtlsdr, text="rtldongle")
    ent_rtldongle = ttk.Entry(frm_rtlsdr, width=10, textvariable=rtldongle)
    lbl_rtldongle.grid (row=2, column=0, sticky=W)
    ent_rtldongle.grid (row=2, column=1, sticky=W)

def _ds_tab_leandvb(tab_leandvb, set_visibility_dvb_options):
    """Settings-Dialog, Reiter 'leandvb'; liefert (frm_blank, options_dvbs, options_dvbs2) fuer set_visibility_dvb_options() (aus dlg_settings ausgelagert)"""

    #----- tab_leandvb -----
    tab_leandvb.columnconfigure((0,1),   pad=4, weight=1)
    tab_leandvb.rowconfigure   ((0,1,2), pad=4, weight=0)

        #----- label -----
    lbl_leandvb = ttk.Label (tab_leandvb, text="Settings for leandvb program")
    lbl_leandvb.grid (row=0, column=0, columnspan=2)

    lbl_leandvb_separator = Frame (tab_leandvb, height=1, bg="grey")
    lbl_leandvb_separator.grid (row=1, column=0, columnspan=2, sticky=EW)

        #----- frame 'common options' -----
    frm_common_options = ttk.Frame (tab_leandvb, borderwidth=4, padding=4)
    frm_common_options.grid (row=2, column=0, sticky=N)

        #----- frame 'dvb options' -----
    frm_dvb_options = ttk.Frame (tab_leandvb, borderwidth=4, padding=4)
    frm_dvb_options.grid (row=2, column=1, sticky=N)
    frm_dvb_options.columnconfigure((0,1), weight=1)
    options_dvbs  = [] # for collecting all DVB-S  options
    options_dvbs2 = [] # for collecting all DVB-S2 options

        #----- for covering current options when switching to new options -----
        #----- see set_visibility_dvb_options() -----
    frm_blank = ttk.Frame (frm_dvb_options)
    frm_blank.grid (row=1, column=0, rowspan=6, columnspan=3, sticky=NSEW)

    #----- tab_leandvb frm_common_options -----
    lbl_inpipe = ttk.Label (frm_common_options,           text="inpipe")
    ent_inpipe = ttk.Entry (frm_common_options, width=10, textvariable=inpipe)
    lbl_inpipe.grid (row=0, column=0, sticky=W)
    ent_inpipe.grid (row=0, column=1, sticky=W)

    lbl_sampler = ttk.Label (frm_common_options, text="sampler")
    cmb_sampler = ttk.Combobox(frm_common_options, width=10, textvariable=sampler, state="readonly")
    cmb_sampler ["values"] = ("nearest","linear","rrc")
    lbl_sampler.grid (row=1, column=0, sticky=W)
    cmb_sampler.grid (row=1, column=1, sticky=W)

    lbl_rolloff = ttk.Label (frm_common_options,           text="roll off")
    ent_rolloff = ttk.Entry (frm_common_options, width=10, textvariable=rolloff)
    lbl_rolloff.grid (row=2, column=0, sticky=W)
    ent_rolloff.grid (row=2, column=1, sticky=W)

    lbl_rrcrej = ttk.Label (frm_common_options,           text="rrc rej")
    ent_rrcrej = ttk.Entry (frm_common_options, width=10, textvariable=rrcrej)
    lbl_rrcrej.grid (row=3, column=0, sticky=W)
    ent_rrcrej.grid (row=3, column=1, sticky=W)

    lbl_fastlock = ttk.Label (frm_common_options, text="fastlock")
    chk_fastlock = Checkbutton (frm_common_options, variable=fastlock)
    lbl_fastlock.grid (row=4, column=0, sticky=W)
    chk_fastlock.grid (row=4, column=1, sticky=W)

    lbl_sensitivity = ttk.Label (frm_common_options, text="max sensitivity")
    chk_sensitivity = Checkbutton (frm_common_options, variable=maxsens)
    lbl_sensitivity.grid (row=5, column=0, sticky=W)
    chk_sensitivity.grid (row=5, column=1, sticky=W)

    lbl_debug = ttk.Label (frm_common_options, text="debug info")
    cmb_debug = ttk.Combobox(frm_common_options, width=10, textvariable=debug, state="readonly")
    cmb_debug ["values"] = ("all","operation","startup","none")
    lbl_debug.grid (row=6, column=0, sticky=W)
    cmb_debug.grid (row=6, column=1, sticky=W)

    lbl_gui = ttk.Label (frm_common_options, text="GUI")
    chk_gui = Checkbutton (frm_common_options, variable=gui)
    lbl_gui.grid (row=7, column=0, sticky=W)
    chk_gui.grid (row=7, column=1, sticky=W)

    #----- tab_leandvb frm_dvb_options (control) -----
    lbl_standard = ttk.Label    (frm_dvb_options, text="DVB standard")
    cmb_standard = ttk.Combobox (frm_dvb_options, width=10, textvariable=standard, state="readonly")
    cmb_standard ["values"] = ("DVB-S","DVB-S2")
    cmb_standard.bind("<<ComboboxSelected>>", set_visibility_dvb_options)
    lbl_standard.grid (row=0, column=0, sticky=W)
    cmb_standard.grid (row=0, column=1, sticky=W)

    #----- tab_leandvb frm_dvb_options (DVB-S) -----
    lbl_const = ttk.Label    (frm_dvb_options, text="constellation")
    cmb_const = ttk.Combobox (frm_dvb_options, width=10, textvariable=const, state="readonly")
    cmb_const ["values"] = ("QPSK","BPSK")
    lbl_const.grid (row=1, column=0, sticky=W)
    cmb_const.grid (row=1, column=1, sticky=W)
    options_dvbs.append(lbl_const)
    options_dvbs.append(cmb_const)

    lbl_fec = ttk.Label   (frm_dvb_options, text="code rate")
    cmb_fec = ttk.Combobox(frm_dvb_options, width=10, textvariable=fec, state="readonly")
    cmb_fec ["values"] = FEC_VALUES
    lbl_fec.grid (row=2, column=0, sticky=W)
    cmb_fec.grid (row=2, column=1, sticky=W)
    options_dvbs.append(lbl_fec)
    options_dvbs.append(cmb_fec)

    lbl_viterbi = ttk.Label   (frm_dvb_options, text="viterbi")
    chk_viterbi = Checkbutton (frm_dvb_options, variable=viterbi)
    lbl_viterbi.grid (row=3, column=0, sticky=W)
    chk_viterbi.grid (row=3, column=1, sticky=W)
    options_dvbs.append(lbl_viterbi)
    options_dvbs.append(chk_viterbi)

    lbl_hardmetric = ttk.Label   (frm_dvb_options, text="hard-metric")
    chk_hardmetric = Checkbutton (frm_dvb_options, variable=hardmetric)
    lbl_hardmetric.grid (row=4, column=0, sticky=W)
    chk_hardmetric.grid (row=4, column=1, sticky=W)
    options_dvbs.append(lbl_hardmetric)
    options_dvbs.append(chk_hardmetric)

    #----- tab_leandvb frm_dvb_options (DVB-S2) -----
    lbl_strongpls = ttk.Label   (frm_dvb_options, text="strongpls")
    chk_strongpls = Checkbutton (frm_dvb_options, variable=strongpls)
    lbl_strongpls.grid (row=1, column=0, sticky=W)
    chk_strongpls.grid (row=1, column=1, sticky=W)
    options_dvbs2.append(lbl_strongpls)
    options_dvbs2.append(chk_strongpls)

    lbl_modcods = ttk.Label (frm_dvb_options, text="modcods")
    ent_modcods = ttk.Entry (frm_dvb_options, width=10, textvariable=modcods)
    lb2_modcods = ttk.Label (frm_dvb_options, text="empty entry omits parameter")
    lbl_modcods.grid (row=2, column=0, sticky=W)
    ent_modcods.grid (row=2, column=1, sticky=W)
    lb2_modcods.grid (row=2, column=2, sticky=W)
    options_dvbs2.append(lbl_modcods)
    options_dvbs2.append(ent_modcods)
    options_dvbs2.append(lb2_modcods)

    lbl_framesizes = ttk.Label (frm_dvb_options, text="framesizes")
    ent_framesizes = ttk.Entry (frm_dvb_options, width=10, textvariable=framesizes)
    lb2_framesizes = ttk.Label (frm_dvb_options, text="empty entry omits parameter")
    lbl_framesizes.grid (row=3, column=0, sticky=W)
    ent_framesizes.grid (row=3, column=1, sticky=W)
    lb2_framesizes.grid (row=3, column=2, sticky=W)
    options_dvbs2.append(lbl_framesizes)
    options_dvbs2.append(ent_framesizes)
    options_dvbs2.append(lb2_framesizes)

    lbl_fastdrift = ttk.Label   (frm_dvb_options, text="fastdrift")
    chk_fastdrift = Checkbutton (frm_dvb_options, variable=fastdrift)
    lbl_fastdrift.grid (row=4, column=0, sticky=W)
    chk_fastdrift.grid (row=4, column=1, sticky=W)
    options_dvbs2.append(lbl_fastdrift)
    options_dvbs2.append(chk_fastdrift)

    lbl_ldpc_bf = ttk.Label (frm_dvb_options, text="max. LDPC bitflips")
    ent_ldpc_bf = ttk.Entry (frm_dvb_options, width=10, textvariable=ldpc_bf)
    lbl_ldpc_bf.grid (row=5, column=0, sticky=W)
    ent_ldpc_bf.grid (row=5, column=1, sticky=W)
    options_dvbs2.append(lbl_ldpc_bf)
    options_dvbs2.append(ent_ldpc_bf)

    lbl_nhelpers = ttk.Label (frm_dvb_options, text="number of decoders")
    ent_nhelpers = ttk.Entry (frm_dvb_options, width=10, textvariable=nhelpers)
    lbl_nhelpers.grid (row=6, column=0, sticky=W)
    ent_nhelpers.grid (row=6, column=1, sticky=W)
    options_dvbs2.append(lbl_nhelpers)
    options_dvbs2.append(ent_nhelpers)
    return frm_blank, options_dvbs, options_dvbs2


def dlg_settings():

    #----- local functions -----
    def set_visibility_dvb_options(dummy):

        # cover current options
        frm_blank.lift()

        # show options according to 'standard' widget
        if standard.get() == "DVB-S":
            for x in options_dvbs:
                x.lift()
        elif standard.get() == "DVB-S2":
            for x in options_dvbs2:
                x.lift()

    def on_leandvb_path():
        d = askdirectory(parent=dlg, initialdir=leandvb_path.get())
        if len(d) > 0:
            leandvb_path.set(d)

    def on_leandvb_file():
        f = askopenfilename(parent=dlg, initialdir=leandvb_path.get(), initialfile=leandvb_file.get())
        if len(f) > 0:
            f = os.path.basename(f)
            leandvb_file.set(f)

    def on_ldpchelper_path():
        d = askdirectory(parent=dlg, initialdir=ldpchelper_path.get())
        if len(d) > 0:
            ldpchelper_path.set(d)

    def on_ldpchelper_file():
        f = askopenfilename(parent=dlg, initialdir=ldpchelper_path.get(), initialfile=ldpchelper_file.get())
        if len(f) > 0:
            f = os.path.basename(f)
            ldpchelper_file.set(f)

    def on_rtlsdr_path():
        d = askdirectory(parent=dlg, initialdir=rtlsdr_path.get())
        if len(d) > 0:
            rtlsdr_path.set(d)

    def on_rtlsdr_file():
        f = askopenfilename(parent=dlg, initialdir=rtlsdr_path.get(), initialfile=rtlsdr_file.get())
        if len(f) > 0:
            f = os.path.basename(f)
            rtlsdr_file.set(f)

    def on_viewer_path():
        d = askdirectory(parent=dlg, initialdir=viewer_path.get())
        if len(d) > 0:
            viewer_path.set(d)

    def on_viewer_file():
        f = askopenfilename(parent=dlg, initialdir=viewer_path.get(), initialfile=viewer_file.get())
        if len(f) > 0:
            f = os.path.basename(f)
            viewer_file.set(f)

    def on_save():
        parameters_save()
        dlg.destroy()

    def on_cancel():
        parameters_load()
        dlg.destroy()

    def on_default():
        parameters_default()

    #----- dialog properties -----
    dlg = tk.Toplevel(root, borderwidth=4)
    dlg.title("Settings")
    dlg.transient(root)
    dlg.resizable(height = False, width = False)
    dlg.grab_set()

    #----- tabs container -----
    ntb = ttk.Notebook (dlg)
    ntb.grid (row=0, column=0, columnspan=3)

    tab_leandvb = ttk.Frame (ntb, padding=10)
    ntb.add(tab_leandvb, text="leandvb")

    tab_rtlsdr = ttk.Frame (ntb, padding=10)
    ntb.add(tab_rtlsdr, text="rtl_sdr")

    tab_files = ttk.Frame (ntb, padding=10)
    ntb.add(tab_files, text="files")
    #----- tab 'files', 'rtl_sdr' und 'leandvb' (siehe _ds_tab_*) -----
    _ds_tab_files(tab_files, on_leandvb_path, on_leandvb_file, on_ldpchelper_path, on_ldpchelper_file, on_rtlsdr_path, on_rtlsdr_file, on_viewer_path, on_viewer_file)
    _ds_tab_rtlsdr(tab_rtlsdr)
    frm_blank, options_dvbs, options_dvbs2 = _ds_tab_leandvb(tab_leandvb, set_visibility_dvb_options)   # von set_visibility_dvb_options() benutzt


    #----- buttons -----
    btn_save    = ttk.Button (dlg, text="save",     command=on_save)
    btn_save   .grid (row=1, column=0)

    btn_cancel  = ttk.Button (dlg, text="cancel",   command=on_cancel)
    btn_cancel .grid (row=1, column=1)

    btn_default = ttk.Button (dlg, text="defaults", command=on_default)
    btn_default.grid (row=1, column=2)

    set_visibility_dvb_options(None)

#===== root window ============================================================

print("Logdateien in: %s  (signal_log.csv, spektrum_signale.csv, verteiler.log, slow_callbacks.log)" % parameters_path, flush=True)
root = tk.Tk(className="leandvb-GUI")   # WM_CLASS fuer die Zuordnung zum Starter (.desktop: StartupWMClass)

#----- Fehlerbericht: Fehler in der GUI werden gemerkt, ein Dialog zeigt den Bericht (Zwischenablage / Datei / Mail-Entwurf); Menue Konfig -> Fehlerbericht -----
_fb = None
_fb_make = None
try:
    _drx_fb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATV-RX-Dienst")
    if _drx_fb not in sys.path:
        sys.path.insert(0, _drx_fb)
    import fehlerbericht as _fb

    def _gui_log_tail(n=80):
        try:
            with open(GUI_LOG, "r", encoding="utf-8", errors="replace") as _f:
                return [x.rstrip("\n") for x in _f.readlines()[-n:]]
        except OSError:
            return []

    def _gui_fb_config():
        c = {"aktive Ziele": ", ".join(_active_targets), "Startparameter": " ".join(sys.argv[1:]) or "(keine)", "Parameter-Modus": str(_cli_mode)}
        try:
            for k in ("datvrx_receivers", "onair_receivers", "tuner_profiles", "dienst_server", "prio_vergabe", "prio_doppelt", "batc_auto_verbinden", "bake_beim_start"):
                if k in app_config:
                    c[k] = json.dumps(app_config[k], ensure_ascii=False)[:400]
        except Exception:
            pass
        return c
    _fb_make = _fb.install_tk(root, "leandvb-GUI", version=_fb.version_of(os.path.abspath(__file__)), log_tail=_gui_log_tail, config=_gui_fb_config)
except Exception as _ex_fb:
    print("fehlerbericht nicht verfuegbar (%r)" % (_ex_fb,))

# ---- Nur EINE GUI gleichzeitig: Sperrdatei ~/.leandvb-GUI/gui.lock (flock). Laeuft schon eine, fragen:
#      alte beenden und diese starten, oder diese gleich wieder beenden (sonst liefe alles doppelt).
def _single_instance():
    os.makedirs(parameters_path, exist_ok=True)
    fh = open(os.path.join(parameters_path, "gui.lock"), "a+")
    def _try():
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    if not _try():
        fh.seek(0)
        other = (fh.read().strip() or "?").split()[0]
        root.withdraw()
        yes = messagebox.askyesno(
            "leandvb-GUI laeuft schon",
            "Es laeuft bereits eine leandvb-GUI (PID %s).\n\nJa = die laufende beenden und diese starten\n"
            "Nein = diese hier wieder beenden" % other)
        if not yes:
            print("Es laeuft bereits eine GUI (PID %s) - diese Instanz wird beendet." % other, flush=True)
            sys.exit(0)
        try:
            os.kill(int(other), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        for _i in range(40):
            time.sleep(0.25)
            if _try():
                break
        else:
            try:
                os.kill(int(other), signal.SIGKILL)
            except (OSError, ValueError):
                pass
            time.sleep(1.0)
            if not _try():
                messagebox.showerror("leandvb-GUI", "Die laufende GUI (PID %s) liess sich nicht beenden." % other)
                sys.exit(1)
        print("Alte GUI (PID %s) beendet - diese Instanz uebernimmt." % other, flush=True)
        root.deiconify()
    fh.seek(0)
    fh.truncate()
    fh.write("%d\n" % os.getpid())
    fh.flush()
    return fh
_gui_lock_fh = _single_instance()

# ---- Monitor fuer langsame GUI-Aufrufe: misst jeden Tk-Callback (after, Knopf, Binding) im Hauptthread.
# Alles > 40 ms landet in ~/.leandvb-GUI/slow_callbacks.log, die Summen je Funktion zeigt
# Konfig -> "Langsame GUI-Aufrufe anzeigen...". So sieht man, welche Funktion die GUI traege macht.
_slow_stats = {}          # Name -> [Anzahl, Summe s, Maximum s]
_slow_log_last = [0.0]
_slow_orig_call = tk.CallWrapper.__call__
def _slow_timed_call(self, *args):
    t0 = time.perf_counter()
    try:
        return _slow_orig_call(self, *args)
    finally:
        dt = time.perf_counter() - t0
        fn = self.func
        name = getattr(fn, "__qualname__", None) or getattr(getattr(fn, "func", None), "__qualname__", None) or repr(fn)
        if name.endswith("callit"):    # root.after(): die eigentliche Funktion steckt im Closure des Wrappers
            for _c in (getattr(fn, "__closure__", None) or ()):
                try:
                    _v = _c.cell_contents
                except ValueError:
                    continue
                if callable(_v) and getattr(_v, "__qualname__", "").find("callit") < 0 and hasattr(_v, "__name__"):
                    name = "after:" + getattr(_v, "__qualname__", _v.__name__)
                    break
        st = _slow_stats.setdefault(name, [0, 0.0, 0.0])
        st[0] += 1; st[1] += dt; st[2] = max(st[2], dt)
        if dt > 0.04:
            try:
                with open(os.path.join(os.path.expanduser("~/"), ".leandvb-GUI", "slow_callbacks.log"), "a") as _f:
                    _f.write("%s\t%.0f ms\t%s\n" % (time.strftime("%H:%M:%S"), dt * 1000.0, name))
            except OSError:
                pass
tk.CallWrapper.__call__ = _slow_timed_call

def _slow_dialog():
    win = tk.Toplevel(root); win.title("Langsame GUI-Aufrufe (Hauptthread)")
    txt = tk.Text(win, width=100, height=28, font=("TkFixedFont", 9)); txt.pack(fill="both", expand=True)
    def fill():
        txt.delete("1.0", "end")
        rows = sorted(_slow_stats.items(), key=lambda kv: -kv[1][1])[:40]
        txt.insert("end", "%-62s %7s %10s %9s\n" % ("Funktion", "Aufr.", "Summe ms", "Max ms"))
        for n, (c, tot, mx) in rows:
            txt.insert("end", "%-62s %7d %10.0f %9.0f\n" % (n[:62], c, tot * 1000, mx * 1000))
        txt.insert("end", "\nEinzelne Aufrufe > 40 ms: ~/.leandvb-GUI/slow_callbacks.log")
        win.after(2000, fill) if win.winfo_exists() else None
    fill()

#----- window properties -----
root.title('QO-100 Tuner Switcher DG7NDV')
try:
    _icon_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leandvb-GUI-icon.png")
    if os.path.isfile(_icon_file):
        _app_icon = tk.PhotoImage(file=_icon_file)
        root.iconphoto(True, _app_icon)
except Exception as _e:
    print("Fenster-Icon nicht geladen:", _e)
root.resizable(height = True, width = False)

#----- Menueleiste -----
#===== OnAir-Empfaenger (Menue "OpenAirRX"): je Empfaenger eine Spalte onair1, onair2, ...       =====
#===== Gestartet wird onair_dvbs2_cli.py (Verzeichnis app_config["onair_dir"]) mit eigenem       =====
#===== Steuer-Port (--api-port 8770+N) und eigenem UDP-Multicast (230.0.0.(9+N):1234). Das       =====
#===== Skript startet onair-cli bei jeder Frequenzaenderung neu (~6 s). Status kommt aus der     =====
#===== API (/status, /telemetry) und aus der Statuszeile von onair-cli (Es/N0, ok/bad, ...).     =====
#===== OnAir-Logfenster: eigenes Fenster NUR mit OnAir-Meldungen (GUI-Meldungen + Ausgabe von onair-cli) =====
_onair_logq = deque(maxlen=5000)     # (zeit, target, text, art) - Threads schreiben, Fenster liest
_onair_logwin = {"w": None, "txt": None, "status": None, "udp": None, "pause": None, "filt": None}

def _oolog(msg):
    """olog() fuer OnAir: Konsole + Statuszeile wie bisher UND ins OnAir-Logfenster. Beginnt die Meldung mit dem
    Namen eines Empfaengers ("OnAir2 (SDRplay A): ..."), gehoert sie diesem (Filter im Fenster) und wird auch in
    dessen Logdatei onairN.log geschrieben (im API-Modus gibt es sonst keine Ausgabe dort)."""
    olog(msg)
    m = str(msg)
    tgt = next((x for x in _ONAIR_TARGETS if m.startswith(_TARGET_LABEL.get(x, x) + ":")), None)
    _onair_logq.append((time.time(), tgt, m, "gui"))
    if tgt:
        try:
            with open(os.path.join(parameters_path, "%s.log" % tgt), "a", encoding="utf-8", errors="replace") as fh:
                fh.write("[GUI %s] %s\n" % (time.strftime("%H:%M:%S"), m))
        except OSError:
            pass

def _onair_logwin_open(target=None):
    """target: Empfaenger vorauswaehlen (Log-Knopf einer Spalte), None = alle (Menue)."""
    w = _onair_logwin.get("w")
    if w is not None:
        try:
            w.deiconify()
            w.lift()
            if _onair_logwin.get("filt") is not None:
                _onair_logwin["filt"].set(target or "alle")
            return
        except tk.TclError:
            pass
    w = tk.Toplevel(root)
    w.title("OnAir-Log")
    w.geometry(app_config.get("onair_logwin_geometry") or "900x320")
    top = ttk.Frame(w)
    top.pack(side='top', fill='x')
    v_status = tk.BooleanVar(value=bool(app_config.get("onair_logwin_status", True)))
    v_udp = tk.BooleanVar(value=False)
    v_pause = tk.BooleanVar(value=False)
    v_filt = tk.StringVar(value=target or "alle")
    ttk.Checkbutton(top, text="Statuszeilen (level/Es/N0)", variable=v_status).pack(side='left')
    ttk.Checkbutton(top, text="udp-Zeilen", variable=v_udp).pack(side='left', padx=(6, 0))
    ttk.Checkbutton(top, text="Anhalten", variable=v_pause).pack(side='left', padx=(6, 0))
    ttk.Label(top, text="Empfaenger:").pack(side='left', padx=(10, 2))
    ttk.Combobox(top, textvariable=v_filt, width=8, state='readonly',
                 values=("alle",) + tuple(_ONAIR_TARGETS)).pack(side='left')
    txt = tk.Text(w, wrap='none', font=('Monospace', 8), height=16)
    sb = ttk.Scrollbar(w, orient='vertical', command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    def clear():
        txt.configure(state='normal')
        txt.delete('1.0', 'end')
        txt.configure(state='disabled')
    ttk.Button(top, text="Leeren", command=clear).pack(side='right')
    ttk.Button(top, text="Logdatei", command=lambda: _onair_open_logfile(v_filt.get())).pack(side='right', padx=(0, 4))
    sb.pack(side='right', fill='y')
    txt.pack(side='left', fill='both', expand=True)
    txt.tag_configure("gui", foreground="#1a5fb4")
    txt.tag_configure("err", foreground="#c0392b")
    txt.tag_configure("lock", foreground="#1e8449")
    txt.tag_configure("abgl", foreground="#9a7d0a")
    txt.configure(state='disabled')
    def on_close():
        try:
            app_config["onair_logwin_geometry"] = w.geometry()
            app_config["onair_logwin_open"] = False
            app_config_save()
        except Exception:
            pass
        _onair_logwin["w"] = None
        w.destroy()
    w.protocol("WM_DELETE_WINDOW", on_close)
    _onair_logwin.update({"w": w, "txt": txt, "status": v_status, "udp": v_udp, "pause": v_pause, "filt": v_filt})
    app_config["onair_logwin_open"] = True
    app_config_save()

def _onair_logwin_tick():
    lw = _onair_logwin
    txt = lw.get("txt")
    if lw.get("w") is not None and txt is not None and not lw["pause"].get():
        try:
            filt = lw["filt"].get()
            first = (txt.yview()[1] >= 0.999)
            txt.configure(state='normal')
            n = 0
            while _onair_logq and n < 400:
                ts, t, s, kind = _onair_logq.popleft()
                n += 1
                if filt != "alle" and t is not None and t != filt:
                    continue
                if kind == "cli":
                    st = s.lstrip()
                    if st.startswith("udp:") and not lw["udp"].get():
                        continue
                    if st.startswith("level") and not lw["status"].get():
                        continue
                low = s.lower()
                tag = ("gui" if kind == "gui" else "")
                if "fehler" in low or "error" in low or "nicht" in low or "lost" in low or "kein lock" in low:
                    tag = "err"
                elif "abgleich" in low:
                    tag = "abgl"
                elif "ts lock" in low or "lock nach" in low:
                    tag = "lock"
                pre = time.strftime("%H:%M:%S ", time.localtime(ts)) + ("%-7s " % t if t else "")
                txt.insert('end', pre + s + "\n", tag)
            lines = int(txt.index('end-1c').split('.')[0])
            if lines > 3000:
                txt.delete('1.0', '%d.0' % (lines - 3000))
            txt.configure(state='disabled')
            if first:
                txt.see('end')
        except tk.TclError:
            pass
    elif lw.get("w") is None and len(_onair_logq) > 4000:
        for _i in range(1000):
            _onair_logq.popleft()
    root.after(300, _onair_logwin_tick)

def _onair_open_logfile(filt):
    t = filt if filt in _ONAIR_TARGETS else (_ONAIR_TARGETS[0] if _ONAIR_TARGETS else None)
    if t:
        _onair_open_log(t)

_onair_procs = {}       # target -> Popen (onair_dvbs2_cli.py)
_onair_rtltcp = {}      # target -> Popen (rtl_tcp, nur Geraet "rtl_tcp")
_onair_st = {t: {"rf": None, "line": "", "line_t": 0.0, "esn0": None, "ok": None, "bad": None,
                 "pk": None, "mod": None, "sr": None, "lock_line": False, "api": None, "tel": None,
                 "api_t": 0.0, "tuned_at": 0.0, "started": 0.0, "exit": None}
             for t in _ONAIR_TARGETS}
_onair_chips = {}       # target -> {key: Label}
_onair_lock = {t: threading.Lock() for t in _ONAIR_TARGETS}   # Start/Stop je Empfaenger nacheinander
_ONAIR_BEACON_MHZ = 10491.5        # beim GUI-Start immer zuerst auf die Bake
ONAIR_DEBUG_LOG = os.path.join(parameters_path, "onair_debug.log")
_onair_last_state = {}

def _onair_dbg(t, msg):
    """Nur mit --debug: eigene Datei ~/.leandvb-GUI/onair_debug.log (alle OnAir-Empfaenger)."""
    if not cliargs.debug:
        return
    try:
        with open(ONAIR_DEBUG_LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write("%s.%03d %-7s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), int(time.time() * 1000) % 1000,
                                           t or "-", msg))
    except OSError:
        pass

def _onair_kill_orphans(t):
    """Beendet uebrig gebliebene Prozesse dieses Empfaengers (z.B. nach Absturz/Strg+C der GUI - die laufen
    in eigener Session weiter und blockieren API-Port und HackRF): onair_dvbs2_cli.py mit demselben
    --api-port und onair-cli mit demselben --udp-Ziel."""
    api = "--api-port %d" % _onair_api_port(t)
    udp = "--udp %s:%d" % _onair_udp(t)
    own = {p.pid for p in (_onair_procs.get(t),) if p is not None}
    pids = []
    for line in _ps_lines():
        pid_s, _sp, args = line.partition(" ")
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid in own or pid == os.getpid():
            continue
        if ("onair_dvbs2_cli.py" in args and api in args) or (("onair-cli" in args or "dect2cli" in args) and udp in args):
            pids.append((pid, args))
    if not pids:
        return
    for pid, args in pids:
        _oolog("%s: alter Prozess %d laeuft noch - wird beendet: %s" % (_TARGET_LABEL.get(t, t), pid, args[:120]))
        _onair_dbg(t, "Waise beenden: %d %s" % (pid, args))
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    t_end = time.time() + 6.0
    while time.time() < t_end:
        alive = []
        for pid, _a in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except OSError:
                pass
        if not alive:
            break
        time.sleep(0.3)
    else:
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
                _onair_dbg(t, "SIGKILL %d" % pid)
            except OSError:
                pass
        time.sleep(0.5)
_onair_found_devices = []   # zuletzt per --list-devices gefundene Zeilen
_ONAIR_RE_CFO = re.compile(r"carrier found:\s*([0-9.]+)\s*Msym/s,\s*([+-]?[0-9]+)\s*kHz from the centre")
_ONAIR_RE_ES = re.compile(r"(?:Es/N0|SNR|MER)\s+(-?[0-9.]+)\s*dB")
_ONAIR_RE_OKBAD = re.compile(r"\bok\s+(\d+)\s+bad\s+(\d+)")
_ONAIR_RE_PK = re.compile(r"packets\s+(\d+)")
_ONAIR_RE_LEVEL = re.compile(r"level\s+(-?[0-9.]+)\s*dBFS")
_ONAIR_RE_MOD = re.compile(r"\b(BPSK|QPSK|8PSK|16APSK|32APSK)\b(?:[\s,]+(\d+/\d+))?", re.I)
_ONAIR_RE_SR = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(Msym|Msps|MS/s|MBd|ksym|ksps|kS/s|kBd)", re.I)
_ONAIR_NET_TYPES = ("rtltcp", "spyserver", "soapyremote", "sdrpp", "kiwisdr", "redpitaya", "hl2", "nrsp")

def _onair_dir():
    return app_config.get("onair_dir") or "/home/dg7ndv/batch/onair"

def _onair_script():
    return os.path.join(_onair_dir(), app_config.get("onair_script") or "onair_dvbs2_cli.py")

def _onair_log_file(t):
    return os.path.join(parameters_path, "%s.log" % t)

def _onair_lo_corr_mhz(t):
    """LO-Korrektur (aus der Bake gemessen, siehe _onair_reader): kHz -> MHz."""
    try:
        return float(_ONAIR_RX.get(t, {}).get("lo_corr_khz") or 0.0) / 1000.0
    except (TypeError, ValueError):
        return 0.0

def _onair_if(t, rf):
    """ZF (MHz), die onair-cli bekommt: RF - LO + LO-Korrektur (Direktempfang z.B. 437 MHz: RF selbst)."""
    if _is_direct_freq(rf):
        return float(rf)
    return float(rf) - _onair_lo(t) + _onair_lo_corr_mhz(t)

def _onair_cfg_list():
    lst = app_config.get("onair_receivers")
    if not isinstance(lst, list):
        lst = []
        app_config["onair_receivers"] = lst
    return lst

def _onair_bind_config():
    """Nach app_config_load(): die Spalten-Eintraege auf die Dicts in app_config zeigen lassen (damit
    z.B. die zuletzt eingestellte Frequenz gespeichert wird). Per -onairN erzwungene, aber noch nicht
    konfigurierte Empfaenger werden dabei in die Konfig uebernommen."""
    lst = _onair_cfg_list()
    changed = False
    for t in _ONAIR_TARGETS:
        i = _onair_num(t) - 1
        while len(lst) <= i:
            lst.append({"device": "hackrf", "enabled": False})
            changed = True
        if lst[i] is not _ONAIR_RX[t]:
            for k, v in _ONAIR_RX[t].items():
                lst[i].setdefault(k, v)
            _ONAIR_RX[t] = lst[i]
    if changed:
        app_config_save()

def _onair_cmd(t, rf, sr=None):
    e = _ONAIR_RX[t]
    ip, port = _onair_udp(t)
    script = _onair_script()
    cmd = ([shutil.which("stdbuf"), "-oL", "-eL"] if shutil.which("stdbuf") else []) + \
          [sys.executable, "-u", script, "%.4f" % _onair_if(t, rf),
           "--udp", "%s:%d" % (ip, port), "--api-port", str(_onair_api_port(t))]
    dev = e.get("device", "hackrf")
    if dev == "hackrf":
        cmd.append("--hackrf")
    elif dev == "rtl_tcp":
        cmd += ["--net-radio", "rtltcp,127.0.0.1,%d" % _onair_rtltcp_port(t)]
    elif dev == "net":
        cmd += ["--net-radio", "%s,%s,%s" % (e.get("net_type") or "rtltcp", e.get("net_host") or "127.0.0.1",
                                             e.get("net_port") or 1234)]
    lna, vga = e.get("lna", 40), e.get("vga", 12)    # Vorgabe: bester Wert aus dem Bake-Test
    if e.get("agc"):
        pass    # AGC: keine --lna/--vga/--gain - die Regelung bestimmt onair-cli selbst
    elif dev == "hackrf" and (lna not in (None, "") or vga not in (None, "")):
        if lna not in (None, ""):
            cmd += ["--lna", str(lna)]
        if vga not in (None, ""):
            cmd += ["--vga", str(vga)]
        if e.get("amp"):
            cmd.append("--amp")
    else:
        g = str(e.get("gain") or "").strip()
        if g:
            cmd += ["--gain", g]
    if e.get("autobw"):
        cmd.append("--autobw")
    elif sr:
        # feste Kanalbreite aus der Symbolrate - ohne --bw schaetzt onair-cli 500 kS z.B. auf 0,25 Msym/s
        try:
            fak = float(app_config.get("onair_bw_faktor") or 1.4)
            cmd += ["--bw", "%.3f" % (float(sr) * fak / 1000.0)]
        except (TypeError, ValueError):
            pass
    try:
        cmd += shlex.split(str(e.get("extra") or ""))
    except ValueError as ex:
        _oolog("%s: Zusatz-Parameter nicht lesbar (%s) - ignoriert." % (_TARGET_LABEL.get(t, t), ex))
    return cmd

def _onair_rtltcp_port(t):
    e = _ONAIR_RX.get(t, {})
    try:
        return int(e.get("rtltcp_port") or 0) or (12340 + _onair_num(t))
    except (TypeError, ValueError):
        return 12340 + _onair_num(t)

def _onair_rtltcp_start(t):
    exe = shutil.which("rtl_tcp")
    if not exe:
        _oolog("%s: rtl_tcp nicht gefunden (sudo apt install rtl-sdr) - RTL-SDR geht nur ueber rtl_tcp." %
             _TARGET_LABEL.get(t, t))
        return False
    p = _onair_rtltcp.get(t)
    if p is not None and p.poll() is None:
        return True
    e = _ONAIR_RX[t]
    cmd = [exe, "-a", "127.0.0.1", "-p", str(_onair_rtltcp_port(t)), "-d", str(e.get("rtl_index", 0))]
    try:
        logf = open(_onair_log_file(t) + ".rtl_tcp", "w")
        _onair_rtltcp[t] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                            start_new_session=True)
    except OSError as ex:
        _oolog("%s: rtl_tcp-Start fehlgeschlagen: %s" % (_TARGET_LABEL.get(t, t), ex))
        return False
    _oolog("%s: rtl_tcp gestartet: %s" % (_TARGET_LABEL.get(t, t), " ".join(cmd)))
    time.sleep(1.5)
    return True

def _onair_reader(t, proc):
    st = _onair_st[t]
    try:
        logf = open(_onair_log_file(t), "a", encoding="utf-8", errors="replace")
    except OSError:
        logf = None
    for line in proc.stdout:
        if logf:
            try:
                logf.write(line)
                logf.flush()
            except OSError:
                pass
        s = line.strip()
        if not s:
            continue
        _onair_dbg(t, "cli> " + s)
        _onair_logq.append((time.time(), t, s, "cli"))
        mc = _ONAIR_RE_CFO.search(s)
        if mc:
            st["cfo_sr"], st["cfo_khz"], st["cfo_t"] = float(mc.group(1)), int(mc.group(2)), time.time()
        if _ONAIR_RE_PROBE.search(s) or s.startswith("Auto-Abgleich"):
            _onair_abgl_line(t, s)
            continue
        m = _ONAIR_RE_ES.search(s)
        mod = _ONAIR_RE_MOD.search(s)
        if m or mod or "TS lock" in s:
            now = time.time()
            st["line"], st["line_t"] = s, now
            if m:
                st["esn0"] = float(m.group(1))
            m2 = _ONAIR_RE_OKBAD.search(s)
            if m2:
                st["ok"], st["bad"] = int(m2.group(1)), int(m2.group(2))
            ml = _ONAIR_RE_LEVEL.search(s)
            if ml:
                st["level"] = float(ml.group(1))
            m3 = _ONAIR_RE_PK.search(s)
            if m3:
                st["pk"] = int(m3.group(1))
            if mod:
                st["mod"] = mod.group(1).upper() + ((" " + mod.group(2)) if mod.group(2) else "")
            m4 = _ONAIR_RE_SR.search(s)
            if m4:
                st["sr"] = m4.group(1)      # nur die Zahl (Msym/s) - Chip ist schmal
            st["lock_line"] = ("TS lock" in s)
            if st["lock_line"]:
                st["lock_t"] = now
                _onair_beacon_lo_check(t)
        elif "Abgleich" in s or _ONAIR_RE_PROBE.search(s):
            _onair_abgl_line(t, s)
        elif s.startswith("Kommando:") or "Fehler" in s or "error" in s.lower() or "endete" in s:
            _oolog("%s: %s" % (_TARGET_LABEL.get(t, t), s[:200]))
    if logf:
        logf.close()

def _onair_app_rate(sr_ks):
    """API-Modus: Abtastrate fuers OnAir-Fenster aus der Symbolrate (SR x onair_app_rate_faktor, mind. 2 Msps,
    max. 10). Mit "Auto (10 Msps)" schaetzt OnAir schmale Signale falsch (333 kS -> 4,7 Msym/s) und die CPU
    laeuft ueber (ring overflow). None = nichts aendern."""
    if not app_config.get("onair_app_rate_auto", True):
        return None
    try:
        f = float(app_config.get("onair_app_rate_faktor") or 4.0)
        return round(max(2.0, min(10.0, float(sr_ks) * f / 1000.0)), 2)
    except (TypeError, ValueError):
        return None

_onair_variant_note = {"last": None}

def _onair_installed_bin():
    for n in ("onair", "OnAir"):
        p = shutil.which(n)
        if p:
            return p
    return None

def _onair_patched_bin():
    """Selbst gebautes OnAir mit UDP-API-Patch: app_config onair_app_patched_bin (bzw. alt: onair_app_bin),
    sonst <Ordner der GUI>/OnAir/build/dect2."""
    b = app_config.get("onair_app_patched_bin") or app_config.get("onair_app_bin") or \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "OnAir", "build", "dect2")
    b = os.path.expanduser(b)
    return b if os.path.isfile(b) and os.access(b, os.X_OK) else None

def _onair_app_bin():
    """Welches OnAir-Programm gestartet wird (Menue Konfig > OnAir-Programm, app_config onair_app_variant):
    "installiert" = /usr/bin/onair (Stream-Haken von Hand), "gepatcht" = selbst gebautes mit /api/v1/x/udp,
    "auto" (Standard) = das gepatchte nur, wenn es vorhanden UND nicht aelter als das installierte ist - wird ein
    neues OnAir installiert, ist das Build aelter und es wird automatisch wieder das installierte genommen."""
    var = app_config.get("onair_app_variant") or "auto"
    inst, pat = _onair_installed_bin(), _onair_patched_bin()
    if var == "installiert":
        choice, why = inst, "Einstellung 'installiert'"
    elif var == "gepatcht":
        choice, why = (pat, "Einstellung 'selbst gebaut'") if pat else (inst, "selbst gebautes OnAir fehlt")
    else:
        if pat and inst:
            try:
                newer = os.path.getmtime(inst) > os.path.getmtime(pat) + 60
            except OSError:
                newer = False
            choice, why = (inst, "installiertes OnAir ist neuer als das selbst gebaute") if newer else (pat, "selbst gebautes OnAir vorhanden")
        else:
            choice, why = (pat or inst), ("selbst gebautes OnAir vorhanden" if pat else "kein selbst gebautes OnAir")
    note = "%s (%s)" % (choice, why)
    if note != _onair_variant_note["last"]:
        _onair_variant_note["last"] = note
        _oolog("OnAir-Programm: %s" % note)
    return choice

def _onair_app_prefs_udp(t):
    """Jeder OnAir-Empfaenger im API-Modus bekommt ein EIGENES OnAir-Fenster mit eigener Konfig:
    ~/.leandvb-GUI/onair_cfg/onairN/onair/settings.conf (OnAir liest XDG_CONFIG_HOME). Beim ersten Mal wird die
    normale ~/.config/onair/settings.conf als Vorlage kopiert; danach werden vor jedem Start gesetzt: Remote-API an
    (apiOn/apiPort), UDP-Ziel (udpHost/udpPort) und - sobald bekannt - das Geraet (devName). Den Haken "stream"
    speichert OnAir NICHT - der muss im Fenster (Tab Stream) nach jedem Start gesetzt werden."""
    f = os.path.join(_onair_app_cfgdir(t), "onair", "settings.conf")
    if not os.path.isfile(f):
        tpl = os.path.expanduser("~/.config/onair/settings.conf")
        try:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            if os.path.isfile(tpl):
                shutil.copyfile(tpl, f)
                _oolog("%s: eigene OnAir-Konfig angelegt (Vorlage %s): %s" % (_TARGET_LABEL.get(t, t), tpl, f))
        except OSError as ex:
            _oolog("%s: eigene OnAir-Konfig nicht anlegbar: %s" % (_TARGET_LABEL.get(t, t), ex))
    ip, port = _onair_udp(t)
    try:
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    want = {"udpHost": ip, "udpPort": str(port), "apiOn": "1", "apiPort": str(_onair_app_port(t)), "apiLan": "0"}
    if (_ONAIR_RX.get(t) or {}).get("app_dev_name"):
        want["devName"] = _ONAIR_RX[t]["app_dev_name"]
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in want:
            out.append("%s=%s" % (k, want[k]))
            seen.add(k)
        else:
            out.append(ln)
    for k, v in want.items():
        if k not in seen:
            out.append("%s=%s" % (k, v))
    if out != lines:
        try:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out) + "\n")
            _oolog("%s: OnAir-Konfig: API-Port %d, UDP-Ziel %s:%d%s (Haken 'stream' im Fenster setzen!)." % (
                _TARGET_LABEL.get(t, t), _onair_app_port(t), ip, port,
                (", Geraet " + want["devName"]) if "devName" in want else ""))
        except OSError as ex:
            _oolog("%s: settings.conf nicht schreibbar: %s" % (_TARGET_LABEL.get(t, t), ex))

_onair_app_procs = {}     # target -> Popen des eigenen OnAir-Fensters

def _onair_prefs_set(path, want):
    """key=value-Eintraege in einer OnAir-settings.conf setzen (nur wenn OnAir dort gerade NICHT laeuft - es
    ueberschreibt die Datei beim Beenden)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in want:
            out.append("%s=%s" % (k, want[k]))
            seen.add(k)
        else:
            out.append(ln)
    out += ["%s=%s" % (k, v) for k, v in want.items() if k not in seen]
    if out == lines:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return True

def _onair_app_running_pids():
    """PIDs laufender OnAir-Fenster (Programm 'onair' bzw. 'dect2')."""
    res = []
    for line in _ps_lines():
        pid_s, _sp, args = line.partition(" ")
        prog = os.path.basename(args.split()[0]) if args.split() else ""
        if prog in ("onair", "dect2"):
            try:
                res.append(int(pid_s))
            except ValueError:
                pass
    return res

def _onair_app_start_shared(t):
    """Standard (onair_app_start=true): EIN gemeinsames OnAir-Fenster mit der normalen Konfig (~/.config/onair).
    Laeuft schon eines, wird nur auf dessen API gewartet (kein zweites Fenster!). Sonst: vorher in settings.conf
    Remote-API (apiOn/apiPort) und UDP-Ziel der Spalte eintragen, dann 'onair' starten. Das Geraet (SDRplay)
    stellt danach _onair_app_start_locked per API ein: Stopp -> Modus -> Start mit Geraet -> Frequenz/Gain."""
    label = _TARGET_LABEL.get(t, t)
    port = _onair_app_port(t)
    pids = _onair_app_running_pids()
    if pids:
        t0 = time.time()
        while time.time() - t0 < 20.0:
            try:
                _onair_api(t, "/api/v1/status", timeout=1.5)
                return True
            except Exception:
                time.sleep(1.0)
        now = time.time()
        if now - _onair_st[t].get("app_warn_t", 0) > 60.0:
            _onair_st[t]["app_warn_t"] = now
            _oolog("%s: OnAir laeuft (PID %s), antwortet aber nicht auf Port %d - im Fenster View > Data feeds > "
                   "Remote control API einschalten." % (label, ",".join(map(str, pids)), port))
        return False
    last = _onair_st[t].get("app_launch_t", 0)
    if time.time() - last < 90.0:
        return False
    _onair_st[t]["app_launch_t"] = time.time()
    exe = _onair_app_bin()
    if not exe:
        _oolog("%s: OnAir-Programm nicht gefunden (app_config: onair_app_bin)." % label)
        return False
    ip, uport = _onair_udp(t)
    try:
        if _onair_prefs_set(os.path.expanduser("~/.config/onair/settings.conf"),
                            {"apiOn": "1", "apiPort": str(port), "udpHost": ip, "udpPort": str(uport)}):
            _oolog("%s: OnAir-Konfig: Remote-API Port %d, UDP %s:%d eingetragen." % (label, port, ip, uport))
    except OSError as ex:
        _oolog("%s: ~/.config/onair/settings.conf nicht schreibbar: %s" % (label, ex))
    _oolog("%s: starte OnAir-Fenster: %s" % (label, exe))
    try:
        _onair_app_procs[t] = subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as ex:
        _oolog("%s: Start fehlgeschlagen: %s" % (label, ex))
        return False
    t0 = time.time()
    while time.time() - t0 < 40.0:
        time.sleep(1.0)
        try:
            _onair_api(t, "/api/v1/status", timeout=1.5)
            _oolog("%s: OnAir-Fenster bereit nach %.0f s." % (label, time.time() - t0))
            return True
        except Exception:
            pass
    _oolog("%s: OnAir-Fenster antwortet nicht auf Port %d." % (label, port))
    return False

def _onair_app_udp_on(t):
    """UDP-Ausgabe im OnAir-Fenster einschalten: POST /api/v1/x/udp - gibt es nur im gepatchten OnAir
    (leandvb-GUI/OnAir-udp-api.patch). Beim Original-OnAir (404) einmal Hinweis, Haken 'stream' von Hand."""
    label = _TARGET_LABEL.get(t, t)
    ip, port = _onair_udp(t)
    try:
        r = _onair_api(t, "/api/v1/x/udp", {"on": True, "host": ip, "port": port, "ttl": 1}, timeout=5.0)
        _onair_st[t]["app_udp_api"] = True
        _oolog("%s: UDP-Ausgabe im OnAir-Fenster eingeschaltet: %s:%d (%s)." % (label, ip, port, json.dumps(r)))
        return True
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            # "alter" Modus: dieses OnAir (z.B. ein neu installiertes Original) kennt /api/v1/x/udp nicht
            _onair_st[t]["app_udp_api"] = False
            _oolog("%s: dieses OnAir kann die UDP-Ausgabe nicht per API schalten (installiertes Original) - "
                   "Haken 'stream' im Fenster (Tab Stream, %s:%d) bitte von Hand setzen." % (label, ip, port))
        else:
            _oolog("%s: UDP per API fehlgeschlagen: HTTP %s" % (label, ex.code))
    except Exception as ex:
        _oolog("%s: UDP per API fehlgeschlagen: %s" % (label, ex))
    return False

def _onair_app_ensure(t):
    """OnAir-Fenster erreichbar? Sonst das Programm starten und bis 40 s auf die API warten."""
    try:
        _onair_api(t, "/api/v1/status", timeout=1.5)
        return True
    except Exception:
        pass
    if not app_config.get("onair_app_autostart", False) and app_config.get("onair_app_start", True):
        return _onair_app_start_shared(t)
    if not app_config.get("onair_app_autostart", False):
        # onair_app_start=false: die GUI startet KEIN OnAir-Fenster selbst - sie benutzt nur ein vom Nutzer gestartetes
        now = time.time()
        if now - _onair_st[t].get("app_warn_t", 0) > 60.0:
            _onair_st[t]["app_warn_t"] = now
            _oolog("%s: OnAir-Fenster antwortet nicht auf Port %d - bitte OnAir selbst starten und dort "
                   "View > Data feeds > Remote control API einschalten." % (_TARGET_LABEL.get(t, t), _onair_app_port(t)))
        return False
    # nicht bei jedem Umschalten ein neues Fenster oeffnen: hoechstens alle 90 s ein Startversuch, und nicht,
    # solange das selbst gestartete Fenster noch laeuft (dann ist nur die API noch nicht oben / abgeschaltet)
    ap = _onair_app_procs.get(t)
    if ap is not None and ap.poll() is None:
        _oolog("%s: eigenes OnAir-Fenster laeuft, antwortet aber nicht auf Port %d (Remote-API aus?)." % (
            _TARGET_LABEL.get(t, t), _onair_app_port(t)))
        return False
    last = _onair_st[t].get("app_launch_t", 0)
    if time.time() - last < 90.0:
        return False
    _onair_st[t]["app_launch_t"] = time.time()
    exe = _onair_app_bin()
    if not exe:
        _oolog("%s: OnAir-Fenster laeuft nicht (API Port %d) und das Programm wurde nicht gefunden "
               "(app_config: onair_app_bin)." % (_TARGET_LABEL.get(t, t), _onair_app_port(t)))
        return False
    _onair_app_prefs_udp(t)
    _oolog("%s: starte OnAir-Fenster: %s" % (_TARGET_LABEL.get(t, t), exe))
    try:
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = _onair_app_cfgdir(t)      # eigenes settings.conf je Empfaenger
        _onair_app_procs[t] = subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    except OSError as ex:
        _oolog("%s: Start fehlgeschlagen: %s" % (_TARGET_LABEL.get(t, t), ex))
        return False
    t0 = time.time()
    while time.time() - t0 < 40.0:
        time.sleep(1.0)
        try:
            _onair_api(t, "/api/v1/status", timeout=1.5)
            return True
        except Exception:
            pass
    _oolog("%s: OnAir-Fenster antwortet nicht auf Port %d - Remote-API einschalten: "
           "View > Data feeds > Remote control API." % (_TARGET_LABEL.get(t, t), _onair_app_port(t)))
    return False

def _onair_app_start_locked(t, rf, sr=None):
    """API-Modus: CLI-Prozesse dieses Empfaengers beenden (HackRF freigeben), OnAir-Fenster sicherstellen,
    dort DVB-S/S2 waehlen, Geraet starten, Frequenz und Verstaerkung setzen."""
    label = _TARGET_LABEL.get(t, t)
    e = _ONAIR_RX[t]
    st = _onair_st[t]
    # ein OnAir-Fenster kann nur EINEN Empfaenger bedienen: SDRplay hat Vorrang, sonst die kleinere Nummer
    rivals = [x for x in _ONAIR_TARGETS if x != t and _onair_is_app(x) and _onair_app_port(x) == _onair_app_port(t)]
    def _prio(x):
        return (0 if _ONAIR_RX[x].get("device") == "sdrplay" else 1, _onair_num(x))
    if rivals and min(rivals, key=_prio) and _prio(min(rivals, key=_prio)) < _prio(t):
        _oolog("%s: das OnAir-Fenster gehoert schon %s - %s laeuft deshalb im CLI-Modus." % (
            label, _TARGET_LABEL.get(min(rivals, key=_prio)), label))
        e["mode"] = "cli"
        try:
            app_config_save()
        except Exception:
            pass
        root.after(0, _onair_mode_button, t)
        return _onair_start_locked(t, rf, False, sr)
    p = _onair_procs.pop(t, None)
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            p.wait(8)
        except Exception:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
    _onair_kill_orphans(t)
    if not _onair_app_ensure(t):
        st["app_ok"] = False
        return False
    want = str(e.get("app_device") or {"hackrf": "hackrf", "rtl_tcp": "rtl", "sdrplay": "sdrplay"}.get(e.get("device", "hackrf"), "")).lower()
    terms = [x.strip() for x in want.split(";") if x.strip()]     # alle Teilstrings muessen passen
    # OnAir sucht seine Radios nach dem Start erst im Hintergrund (SDRplay dauert einige Sekunden): die
    # Geraeteliste bis zu 45 s lang wiederholt abfragen, bis das gewuenschte Geraet darin steht.
    modes, devs, dev = [], [], None
    t_dev = time.time()
    while True:
        try:
            modes = _onair_api(t, "/api/v1/modes", timeout=3.0) or []
            devs = _onair_api(t, "/api/v1/devices", timeout=5.0) or []
            # Fehlerantworten ({"error": ...}) oder unerwartete Formen nicht als Liste durchlaufen
            modes = [m for m in modes if isinstance(m, dict)] if isinstance(modes, list) else []
            devs = [d for d in devs if isinstance(d, dict)] if isinstance(devs, list) else []
        except Exception as ex:
            _oolog("%s: OnAir-Fenster-API Fehler: %s" % (label, ex))
            return False
        dev = next((d for d in devs if terms and all(x in ((d.get("name") or "") + " " + (d.get("board") or "")).lower()
                                                       for x in terms)), None)
        if dev is not None or not terms or time.time() - t_dev > 45.0:
            break
        if not devs or time.time() - t_dev < 1.0:
            _onair_dbg(t, "Geraeteliste noch leer/ohne Treffer (%d Eintraege) - warte" % len(devs))
        time.sleep(2.0)
    if dev is not None and time.time() - t_dev > 2.0:
        _oolog("%s: Geraet im OnAir-Fenster nach %.0f s gefunden: %s" % (label, time.time() - t_dev, dev.get("name")))
    mode = next((m for m in modes if (m.get("sub") or "") == "dvbs"), None) or \
           next((m for m in modes if "DVB-S" in (m.get("name") or "")), None)
    if dev is None and terms:
        _oolog("%s: Geraete im OnAir-Fenster: %s" % (label, "; ".join("%s=%s" % (d.get("index"), d.get("name")) for d in devs)))
    if dev is not None and e.get("app_dev_name") != dev.get("name"):
        e["app_dev_name"] = dev.get("name")      # beim naechsten Fensterstart gleich als devName vorgeben
        try:
            app_config_save()
        except Exception:
            pass
    if mode is None or dev is None:
        _oolog("%s: im OnAir-Fenster nicht gefunden: %s%s" % (label, "" if mode else "Modus DVB-S ",
                                                              "" if dev else "Geraet '%s'" % want))
        return False
    try:
        _onair_api(t, "/api/v1/stop", {}, timeout=3.0)
    except Exception:
        pass
    steps = [("/api/v1/mode", dict({"family": mode["family"]}, **({"sub": mode["sub"]} if mode.get("sub") else {}))),
             ("/api/v1/start", {"device": dev["index"]}),
             ("/api/v1/frequency", {"mhz": round(_onair_if(t, rf), 4)})]
    _r = _onair_app_rate(sr if sr else (1500 if abs(float(rf) - _ONAIR_BEACON_MHZ) < 0.01 else None))
    if _r:
        steps.insert(2, ("/api/v1/samplerate", {"msps": _r}))
    try:
        if e.get("device", "hackrf") == "hackrf":
            g = float(e.get("lna", 40) or 0) + float(e.get("vga", 12) or 0)
            steps.append(("/api/v1/gain", {"db": g}))
        elif str(e.get("gain") or "").strip() or e.get("device") == "sdrplay":
            # SDRplay: Vorgabe 59 dB (im OnAir-Fenster mit RSP2 auf QO-100 bewaehrt)
            steps.append(("/api/v1/gain", {"db": float(str(e.get("gain") or "59").replace(",", "."))}))
    except (TypeError, ValueError):
        pass
    for path, body in steps:
        try:
            _onair_api(t, path, body, timeout=8.0)
            _onair_dbg(t, "APP %s %s ok" % (path, json.dumps(body)))
        except Exception as ex:
            _oolog("%s: OnAir-Fenster %s %s: %s" % (label, path, json.dumps(body), ex))
            if path == "/api/v1/start":
                _oolog("%s: Geraet '%s' liess sich nicht starten - ist es noch von einem anderen Programm belegt "
                       "(z.B. einem zweiten OnAir-Fenster oder OnAir1-CLI)?" % (label, dev.get("name")))
            if path in ("/api/v1/start", "/api/v1/mode"):
                return False
    _target_cur_freq[t] = float(rf)        # sonst landet ein auf der Bake decodiertes Rufzeichen bei der alten Frequenz
    if sr:
        _target_cur_sr[t] = sr
    st.update({"rf": rf, "sr_set": sr, "start_sr": sr, "started": time.time(), "tuned_at": time.time(),
               "exit": None, "app_ok": True, "line": "", "esn0": None, "lock_line": False, "app_rate": _r,
               "app_init_done": True})
    _oolog("%s: API-Modus - OnAir-Fenster empfaengt mit %s auf %.3f MHz (ZF %.3f)." % (label, dev.get("name"), rf, _onair_if(t, rf)))
    _onair_app_udp_on(t)
    if e.get("agc"):
        _onair_app_agc(t, True)
    return True

def _onair_mode_toggle(t, api=None):
    """Knopf "API" / Menue: zwischen CLI-Modus (grau) und API-Modus ueber das OnAir-Fenster (gruen) wechseln."""
    e = _ONAIR_RX[t]
    st = _onair_st[t]
    if st.get("abgleich") or st.get("abgl_wait_cs"):
        _oolog("%s: Abgleich laeuft - Umschalten danach." % _TARGET_LABEL.get(t, t))
        return
    new = (not _onair_is_app(t)) if api is None else bool(api)
    if new:
        other = [x for x in _ONAIR_TARGETS if x != t and _onair_is_app(x) and _onair_app_port(x) == _onair_app_port(t)]
        if other:
            _oolog("%s: API-Modus nicht moeglich - das OnAir-Fenster (Port %d) wird schon von %s benutzt "
                   "(ein Fenster = ein Empfaenger)." % (_TARGET_LABEL.get(t, t), _onair_app_port(t), _TARGET_LABEL.get(other[0], other[0])))
            root.after(0, _onair_mode_button, t)
            return
    if not new and e.get("device") == "sdrplay":
        _oolog("%s: SDRplay geht nur ueber das OnAir-Fenster (API-Modus) - bleibt auf API." % _TARGET_LABEL.get(t, t))
        root.after(0, _onair_mode_button, t)
        return
    old_app = _onair_is_app(t)
    if new == old_app:
        _onair_mode_button(t)
        return
    if old_app:
        try:
            _onair_api(t, "/api/v1/stop", {}, timeout=2.0)   # HackRF im OnAir-Fenster freigeben
        except Exception:
            pass
        st["app_ok"] = False
    e["mode"] = "api" if new else "cli"
    app_config_save()
    _onair_mode_button(t)
    _oolog("%s: %s" % (_TARGET_LABEL.get(t, t), "API-Modus (OnAir-Fenster, schnelles Umschalten)" if new
                       else "CLI-Modus (onair-cli, Neustart je Umschalten)"))
    threading.Thread(target=_onair_start, args=(t, st.get("rf") or _ONAIR_BEACON_MHZ),
                     kwargs={"sr": st.get("sr_set")}, daemon=True).start()

_onair_mode_buttons = {}

def _onair_mode_button(t):
    b = _onair_mode_buttons.get(t)
    if b is not None:
        try:
            b.configure(style=("RecOn.TButton" if _onair_is_app(t) else "TButton"))
        except tk.TclError:
            pass

_ONAIR_MODCOD_IDX = {}
for _i, _cr in enumerate(("1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10")):
    _ONAIR_MODCOD_IDX["QPSK " + _cr] = 1 + _i
for _i, _cr in enumerate(("3/5", "2/3", "3/4", "5/6", "8/9", "9/10")):
    _ONAIR_MODCOD_IDX["8PSK " + _cr] = 12 + _i
for _i, _cr in enumerate(("2/3", "3/4", "4/5", "5/6", "8/9", "9/10")):
    _ONAIR_MODCOD_IDX["16APSK " + _cr] = 18 + _i
for _i, _cr in enumerate(("3/4", "4/5", "5/6", "8/9", "9/10")):
    _ONAIR_MODCOD_IDX["32APSK " + _cr] = 24 + _i

def _onair_modcod_thr(mod):
    """Decodierschwelle (Es/N0 dB) fuer z.B. 'QPSK 2/3' (DVB-S2), sonst None."""
    i = _ONAIR_MODCOD_IDX.get((mod or "").strip().upper().replace("APSK", "APSK"))
    return _MODCOD_THRESH_DB.get(i) if i else None

def _onair_dbm(t, dbfs, gain_db):
    """GROBE Schaetzung der Eingangsleistung am SDR: dBm ~ dBFS - Verstaerkung + onair_dbm_offset.
    dBFS ist die Gesamtleistung am ADC ueber die ganze Abtastbreite (10 MHz), nicht nur das eine Signal.
    Den Offset einmal gegen ein Messgeraet / den dBm-Wert der Octagon abgleichen (app_config: onair_dbm_offset)."""
    if dbfs is None or not isinstance(gain_db, (int, float)):
        return None
    try:
        off = float(app_config.get("onair_dbm_offset") or 0.0)
    except (TypeError, ValueError):
        off = 0.0
    return float(dbfs) - float(gain_db) + off

#----- Werte-Fenster ("extra Maske") je OnAir-Empfaenger: alle Werte gross untereinander -----
_onair_valwins = {}    # target -> (Toplevel, {key: Label})
_ONAIR_VAL_ROWS = (("state", "Zustand"), ("rf", "Frequenz (RF)"), ("if", "ZF an onair-cli"), ("locorr", "LO-Korrektur"),
                   ("mode", "Modus"), ("mod", "MODCOD"), ("sr", "Symbolrate"), ("mer", "MER (Es/N0)"),
                   ("d", "D (Abstand zur Schwelle)"), ("okbad", "FEC-Bloecke ok / bad"), ("pk", "TS-Pakete"),
                   ("gain", "Verstaerkung"), ("level", "ADC-Pegel"), ("dbm", "Eingang (Schaetzung)"),
                   ("bw", "Kanalbreite"), ("cs", "Rufzeichen"),
                   ("q", "Qualitaet (OnAir)"), ("clip", "Clipping / Spitze"), ("dev", "Geraet / Abtastrate"),
                   ("cfo", "Traeger-Versatz"), ("line", "letzte Statuszeile"))

def _onair_valwin_open(t):
    w0 = _onair_valwins.get(t)
    if w0 is not None:
        try:
            w0[0].deiconify()
            w0[0].lift()
            return
        except tk.TclError:
            pass
    w = tk.Toplevel(root)
    w.title("%s - Werte" % _TARGET_LABEL.get(t, t))
    frm = ttk.Frame(w, padding=8)
    frm.pack(fill='both', expand=True)
    labs = {}
    for i, (k, txt) in enumerate(_ONAIR_VAL_ROWS):
        ttk.Label(frm, text=txt + ":").grid(row=i, column=0, sticky='e', padx=(0, 8), pady=1)
        lb = ttk.Label(frm, text="-", font=('', 10, 'bold'), wraplength=520, justify='left')
        lb.grid(row=i, column=1, sticky='w', pady=1)
        labs[k] = lb
    v = tk.BooleanVar(value=bool(app_config.get("onair_show_dbm", True)))
    def tog():
        app_config["onair_show_dbm"] = bool(v.get())
        app_config_save()
    ttk.Checkbutton(frm, text="dBm-Schaetzung auch in der Spalte zeigen", variable=v, command=tog
                    ).grid(row=len(_ONAIR_VAL_ROWS), column=0, columnspan=2, sticky='w', pady=(6, 0))
    off = tk.StringVar(value=str(app_config.get("onair_dbm_offset") or 0))
    of = ttk.Frame(frm)
    of.grid(row=len(_ONAIR_VAL_ROWS) + 1, column=0, columnspan=2, sticky='w', pady=(2, 0))
    ttk.Label(of, text="dBm-Kalibrierwert (dB):").pack(side='left')
    ttk.Entry(of, textvariable=off, width=7).pack(side='left', padx=4)
    def setoff():
        try:
            app_config["onair_dbm_offset"] = float(off.get().replace(",", "."))
            app_config_save()
        except ValueError:
            pass
    ttk.Button(of, text="uebernehmen", command=setoff).pack(side='left')
    def on_close():
        _onair_valwins.pop(t, None)
        w.destroy()
    w.protocol("WM_DELETE_WINDOW", on_close)
    _onair_valwins[t] = (w, labs)
    _onair_valwin_tick(t)

def _onair_valwin_tick(t):
    w0 = _onair_valwins.get(t)
    if w0 is None:
        return
    w, labs = w0
    st = _onair_st[t]
    e = _ONAIR_RX.get(t, {})
    api = st.get("api") or {}
    now = time.time()
    fresh = now - st.get("line_t", 0) < 5.0
    rf = st.get("rf")
    es = st.get("esn0") if fresh else None
    thr = _onair_modcod_thr(st.get("mod"))
    g = api.get("gainDb")
    vals = {
        "state": _onair_last_state.get(t, "-"),
        "rf": ("%.3f MHz" % rf) if rf else "-",
        "if": ("%.4f MHz" % _onair_if(t, rf)) if rf else "-",
        "locorr": "%+d kHz (LO %.1f MHz)" % (int(e.get("lo_corr_khz") or 0), _onair_lo(t)),
        "mode": "API (OnAir-Fenster)" if _onair_is_app(t) else "CLI (onair-cli)",
        "mod": st.get("mod") or "-",
        "sr": ("%s Msym/s" % st["sr"]) if st.get("sr") else "-",
        "mer": ("%.1f dB" % es) if es is not None else "-",
        "d": ("%+.1f dB (Schwelle %.2f dB)" % (es - thr, thr)) if (es is not None and thr is not None) else "-",
        "okbad": "%s / %s" % (st.get("ok", "-"), st.get("bad", "-")),
        "pk": str(st.get("pk") if st.get("pk") is not None else "-"),
        "gain": ("%g dB (LNA %s / VGA %s%s)" % (g, api.get("lnaDb", e.get("lna")), api.get("vgaDb", e.get("vga")),
                                                " +Amp" if api.get("amp") else "")) if isinstance(g, (int, float)) else "-",
        "level": ("%.1f dBFS" % st["level"]) if (fresh and st.get("level") is not None) else "-",
        "dbm": ("%.1f dBm  (grob: dBFS - Gain %+g)" % (st["dbm"], float(app_config.get("onair_dbm_offset") or 0)))
               if st.get("dbm") is not None else "-",
        "bw": "automatisch (--autobw)" if e.get("autobw") else ("%.3f MHz (SR x %s)" % (
              float(st.get("start_sr") or 0) * float(app_config.get("onair_bw_faktor") or 1.4) / 1000.0,
              app_config.get("onair_bw_faktor") or 1.4) if st.get("start_sr") else "ohne"),
        "cs": (_target_callsign_var.get(t).get() if _target_callsign_var.get(t) is not None else "") or "-",
        "line": (st.get("line") or "-") if fresh else "-",
    }
    tel = st.get("tel") or {}
    q = tel.get("quality") or {}
    det = tel.get("details") or {}
    if q:
        vals["q"] = "%s %% - %s (benoetigt %.1f dB, Marge %+.1f dB)" % (
            ("%.0f" % q["percent"]) if isinstance(q.get("percent"), (int, float)) else "-", q.get("label") or "-",
            q.get("requiredDb") or 0, q.get("marginDb") or 0)
    else:
        vals["q"] = "-" if not _onair_is_app(t) else "(OnAir liefert gerade keine)"
    if _onair_is_app(t) and isinstance(det.get("merDb"), (int, float)):
        vals["mer"] = "%s  (Es/N0 %s, MER %.1f dB)" % (vals["mer"], ("%.1f" % det["snrDb"]) if isinstance(det.get("snrDb"), (int, float)) else "-", det["merDb"])
    cl = st.get("clip")
    vals["clip"] = ("%.2f %%%s" % (cl * 100.0, "  UEBERSTEUERT" if cl > 0.001 else "")) if isinstance(cl, (int, float)) else "-"
    if isinstance(st.get("spec_peak"), (int, float)):
        vals["clip"] += "  Spitze %.2f" % st["spec_peak"]
    vals["dev"] = "%s, %s" % (api.get("device") or e.get("device", "-"),
                              ("%.2f Msps" % (api["sampleRateHz"] / 1e6)) if isinstance(api.get("sampleRateHz"), (int, float)) else "-")
    cfo = tel.get("cfoHz") if isinstance(tel.get("cfoHz"), (int, float)) else (
          (st.get("cfo_khz") * 1000.0) if isinstance(st.get("cfo_khz"), (int, float)) else None)
    vals["cfo"] = ("%+.1f kHz" % (cfo / 1000.0)) if cfo is not None else "-"
    try:
        for k, lb in labs.items():
            lb.configure(text=vals.get(k, "-"))
    except tk.TclError:
        _onair_valwins.pop(t, None)
        return
    root.after(1000, _onair_valwin_tick, t)

def _onair_beacon_lo_check(t):
    """Reader-Thread, bei jedem TS lock: steht der Empfaenger auf der Bake und hat onair-cli beim Einrasten
    einen Traeger-Versatz gemeldet ("carrier found: 1.500 Msym/s, -79 kHz from the centre"), wird dieser
    Versatz als LO-Korrektur uebernommen (LNB-Drift). Ohne Korrektur liegen 500-kS-Signale ~80 kHz neben
    der Fenstermitte und onair-cli rastet auf einen falschen Traeger (0,25 Msym/s) ein."""
    st = _onair_st[t]
    rf = st.get("rf")
    if rf is None or abs(float(rf) - _ONAIR_BEACON_MHZ) > 0.01:
        return
    if st.get("cfo_t", 0) < st.get("started", 0) or st.get("cfo_done") == st.get("cfo_t"):
        return
    st["cfo_done"] = st.get("cfo_t")
    cfo = st.get("cfo_khz") or 0
    if abs(cfo) < 3 or abs(float(st.get("cfo_sr") or 0) - 1.5) > 0.1:
        return
    e = _ONAIR_RX[t]
    try:
        old = float(e.get("lo_corr_khz") or 0.0)
    except (TypeError, ValueError):
        old = 0.0
    new = round(old + cfo)
    if abs(new) > 1000:
        _oolog("%s: Bake %+d kHz neben der Mitte - unplausibel, LO-Korrektur NICHT uebernommen." % (_TARGET_LABEL.get(t, t), cfo))
        return
    e["lo_corr_khz"] = new
    try:
        app_config_save()
    except Exception:
        pass
    msg = "%s: Bake %+d kHz neben der Mitte - LO-Korrektur %+d -> %+d kHz gespeichert (gilt ab dem naechsten Umschalten)." % (
        _TARGET_LABEL.get(t, t), cfo, old, new)
    _oolog(msg)
    _onair_dbg(t, msg)

def _onair_start(t, rf=None, abgleich=False, sr=None):
    """Startet onair_dvbs2_cli.py fuer diesen Empfaenger (ein evtl. laufender wird vorher beendet).
    abgleich=True: mit --abgleich (Auto-Abgleich LNA/VGA, nur HackRF) - danach laeuft das Skript mit den
    besten Werten normal weiter (API kommt erst nach dem Abgleich hoch)."""
    with _onair_lock[t]:
        return _onair_start_locked(t, rf, abgleich, sr)

def _onair_start_locked(t, rf=None, abgleich=False, sr=None):
    e = _ONAIR_RX[t]
    label = _TARGET_LABEL.get(t, t)
    if rf is None:
        try:
            rf = float(e.get("last_rf") or 10491.5)
        except (TypeError, ValueError):
            rf = 10491.5
    if _onair_is_app(t):
        return _onair_app_start_locked(t, rf, sr)
    _onair_stop(t, quiet=True)
    _onair_kill_orphans(t)
    script = _onair_script()
    if not os.path.isfile(script):
        _oolog("%s: %s nicht gefunden (app_config: onair_dir/onair_script)." % (label, script))
        return False
    if e.get("device") == "rtl_tcp" and not _onair_rtltcp_start(t):
        return False
    if sr is None:
        sr = 1500 if abs(float(rf) - _ONAIR_BEACON_MHZ) < 0.01 else _onair_st[t].get("sr_set")
    cmd = _onair_cmd(t, rf, sr)
    if abgleich:
        # --abgleich schliesst --lna/--vga/--gain aus (der Abgleich bestimmt sie selbst)
        _c, _skip = [], False
        for _x in cmd:
            if _skip:
                _skip = False
                continue
            if _x in ("--lna", "--vga", "--gain"):
                _skip = True
                continue
            _c.append(_x)
        cmd = _c
        cmd += ["--abgleich", "--abgleich-zeit", str(max(10, int(app_config.get("onair_abgleich_zeit") or 10)))]
    try:
        with open(_onair_log_file(t), "a") as f:
            f.write("\n# %s  %s\n# %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), label, shlex.join(cmd)))
        proc = subprocess.Popen(cmd, cwd=_onair_dir(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
                                start_new_session=True)
    except OSError as ex:
        _oolog("%s: Start fehlgeschlagen: %s" % (label, ex))
        return False
    _onair_procs[t] = proc
    st = _onair_st[t]
    _target_cur_freq[t] = float(rf)        # sonst landet ein auf der Bake decodiertes Rufzeichen bei der alten Frequenz
    if sr:
        _target_cur_sr[t] = sr
    st.update({"rf": rf, "sr_set": sr, "start_sr": sr, "started": time.time(), "tuned_at": time.time(), "exit": None,
               "line": "", "esn0": None, "ok": None, "bad": None, "pk": None, "lock_line": False})
    threading.Thread(target=_onair_reader, args=(t, proc), daemon=True).start()
    _onair_dbg(t, "START pid %d rf %.3f zf %.4f: %s" % (proc.pid, rf, _onair_if(t, rf), shlex.join(cmd)))
    _oolog("%s: gestartet (%.3f MHz, ZF %.3f MHz, UDP %s:%d, API %d): %s" %
         (label, rf, _onair_if(t, rf), _onair_udp(t)[0], _onair_udp(t)[1], _onair_api_port(t), shlex.join(cmd)))
    return True

def _onair_stop(t, quiet=False):
    if _onair_is_app(t) and not quiet:
        try:
            _onair_api(t, "/api/v1/stop", {}, timeout=3.0)
            _oolog("%s: Empfang im OnAir-Fenster gestoppt." % _TARGET_LABEL.get(t, t))
        except Exception as ex:
            _oolog("%s: Stop im OnAir-Fenster fehlgeschlagen: %s" % (_TARGET_LABEL.get(t, t), ex))
        _onair_st[t]["app_ok"] = False
    p = _onair_procs.pop(t, None)
    if p is not None and p.poll() is None:
        try:
            p.terminate()          # Skript faengt SIGTERM ab und beendet onair-cli selbst
            p.wait(10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                p.kill()
        except OSError:
            pass
        try:
            os.killpg(p.pid, signal.SIGTERM)    # evtl. noch lebendes onair-cli-Kind derselben Gruppe
        except OSError:
            pass
        _onair_dbg(t, "STOP pid %d (Code %s)" % (p.pid, p.poll()))
        if not quiet:
            _oolog("%s: gestoppt." % _TARGET_LABEL.get(t, t))
    r = _onair_rtltcp.get(t)
    if r is not None and r.poll() is None and not quiet:
        try:
            r.terminate()
            r.wait(5)
        except Exception:
            r.kill()
        _onair_rtltcp.pop(t, None)

def _onair_stop_all():
    for t in _ONAIR_TARGETS:
        if _onair_is_app(t) and _onair_st[t].get("app_ok"):
            try:
                _onair_api(t, "/api/v1/stop", {}, timeout=2.0)
            except Exception:
                pass
    for t in list(_onair_procs.keys()):
        _onair_stop(t, quiet=True)
    for t, r in list(_onair_rtltcp.items()):
        try:
            if r.poll() is None:
                r.terminate()
        except Exception:
            pass

def _onair_is_app(t):
    """True = API-Modus: das OnAir-Fenster (Desktop-Programm mit Remote-API, Port onair_app_port) empfaengt,
    Umschalten geht dort live per POST /frequency ohne Neustart (schnell). False = CLI-Modus (onair-cli ueber
    onair_dvbs2_cli.py, jede Aenderung startet onair-cli neu, ~6 s)."""
    return (_ONAIR_RX.get(t) or {}).get("mode") == "api"

def _onair_app_port(t=None):
    """Remote-API-Port des OnAir-Fensters: app_port im Eintrag, sonst app_config onair_app_port (8765) - also das
    vom Nutzer selbst gestartete OnAir-Fenster. Eigene Fenster je Empfaenger nur mit onair_app_autostart=true."""
    try:
        dflt = int(app_config.get("onair_app_port") or 8765)
    except (TypeError, ValueError):
        dflt = 8765
    if t is None:
        return dflt
    try:
        return int((_ONAIR_RX.get(t) or {}).get("app_port") or 0) or dflt
    except (TypeError, ValueError):
        return dflt

def _onair_app_cfgdir(t):
    """Eigenes Konfig-Verzeichnis je Empfaenger (XDG_CONFIG_HOME fuer das OnAir-Fenster) - darin onair/settings.conf."""
    return os.path.join(parameters_path, "onair_cfg", t)

def _onair_running(t):
    if _onair_is_app(t):
        return bool(_onair_st[t].get("app_ok"))
    p = _onair_procs.get(t)
    return p is not None and p.poll() is None

_onair_tok_cache = {"t": 0.0, "v": ""}

def _onair_app_token():
    """apiToken aus ~/.config/onair/settings.conf (alle 30 s neu gelesen)."""
    if time.time() - _onair_tok_cache["t"] < 30.0:
        return _onair_tok_cache["v"]
    v = str(app_config.get("onair_app_token") or "")
    if not v:
        try:
            with open(os.path.expanduser("~/.config/onair/settings.conf"), encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    if ln.startswith("apiToken="):
                        v = ln.split("=", 1)[1].strip()
                        break
        except OSError:
            pass
    _onair_tok_cache.update({"t": time.time(), "v": v})
    return v

def _onair_api(t, path, body=None, timeout=1.5):
    port = _onair_app_port(t) if _onair_is_app(t) else _onair_api_port(t)
    url = "http://127.0.0.1:%d%s" % (port, path)
    data = None if body is None else json.dumps(body).encode()
    hdr = {"Content-Type": "application/json"}
    if _onair_is_app(t):
        tok = _onair_app_token()
        if tok:
            hdr["Authorization"] = "Bearer " + tok    # noetig nur, wenn "Let other computers connect" an ist
    req = urllib.request.Request(url, data=data, method=("GET" if body is None else "POST"), headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")

def _onair_api_loop():
    while True:
        for t in _ONAIR_TARGETS:
            st = _onair_st[t]
            if _onair_is_app(t):
                try:
                    st["api"] = _onair_api(t, "/api/v1/status")
                    st["tel"] = _onair_api(t, "/api/v1/telemetry")
                    st["api_t"] = time.time()
                    st["app_ok"] = bool(st["api"].get("running"))
                    if not st.get("app_init_done") and not st.get("app_init_busy") and not st.get("user_stopped") and \
                            app_config.get("bake_beim_start", True) and time.time() - st.get("app_init_t", 0) > 30.0:
                        st["app_init_t"] = time.time()
                        # OnAir-Fenster ist (jetzt) erreichbar, der Empfaenger wurde aber noch nicht eingerichtet
                        # (z.B. Fenster erst nach der GUI geoeffnet): einmal wie beim HackRF auf der Bake starten
                        st["app_init_busy"] = True
                        _oolog("%s: OnAir-Fenster erreichbar - starte auf der Bake %.3f MHz." % (_TARGET_LABEL.get(t, t), _ONAIR_BEACON_MHZ))
                        def _init(tt=t):
                            try:
                                _onair_start(tt, _ONAIR_BEACON_MHZ)
                            finally:
                                _onair_st[tt]["app_init_busy"] = False
                        threading.Thread(target=_init, daemon=True).start()
                    try:
                        sp = _onair_api(t, "/api/v1/spectrum?bins=16")
                        if isinstance(sp, dict) and sp.get("running") is not False:
                            st["level"] = sp.get("rmsDbfs")
                            st["clip"] = sp.get("clipFraction")
                            st["spec_peak"] = sp.get("peak")
                            st["line_t"] = time.time()      # damit der Pegel-Chip den Wert als frisch nimmt
                    except Exception:
                        pass
                    # LO-Korrektur auch im API-Modus: einmal je Start, sobald die Bake gelockt ist (cfoHz der Telemetrie)
                    tel = st.get("tel") or {}
                    if tel.get("locked") and isinstance(tel.get("cfoHz"), (int, float)) and \
                            st.get("app_cfo_for") != st.get("started") and time.time() - st.get("started", 0) > 3.0:
                        st["app_cfo_for"] = st.get("started")
                        st["cfo_khz"], st["cfo_sr"], st["cfo_t"] = int(round(tel["cfoHz"] / 1000.0)), 1.5, time.time()
                        _onair_beacon_lo_check(t)
                except Exception as ex:
                    if st.get("app_ok"):
                        _onair_dbg(t, "OnAir-Fenster-API nicht erreichbar: %r" % (ex,))
                    st["api"] = st["tel"] = None
                    st["app_ok"] = False
                continue
            if not _onair_running(t):
                st["api"] = st["tel"] = None
                continue
            try:
                st["api"] = _onair_api(t, "/api/v1/status")
                st["tel"] = _onair_api(t, "/api/v1/telemetry")
                st["api_t"] = time.time()
                if cliargs.debug and int(time.time()) % 10 < 2:
                    _onair_dbg(t, "api status=%s tel=%s" % (json.dumps(st["api"]), json.dumps(st["tel"])))
            except Exception as ex:
                if st.get("api") is not None or not st.get("api_err"):
                    _onair_dbg(t, "API nicht erreichbar: %r" % (ex,))
                st["api_err"] = repr(ex)
                st["api"] = st["tel"] = None
        time.sleep(1.5)

def _onair_locked(t):
    st = _onair_st[t]
    tel = st.get("tel") or {}
    if tel.get("locked"):
        return True
    return st.get("lock_line") and time.time() - st.get("line_t", 0) < 4.0

def _onair_tune(t, freq, sr):
    """Aus target_dispatch (eigener Thread): neue Frequenz an den OnAir-Empfaenger."""
    label = _TARGET_LABEL.get(t, t)
    e = _ONAIR_RX[t]
    if _onair_st[t].get("user_stopped"):
        # mit "Stopp" angehalten: Automatik/Klick startet ihn NICHT wieder (erst "Neustart")
        _oolog("%s: gestoppt (Knopf Stopp) - %.3f MHz nicht eingestellt, erst 'Neustart' druecken." % (label, freq))
        return
    if _onair_st[t].get("abgleich") or _onair_st[t].get("abgl_wait_cs"):
        _onair_st[t]["pending_rf"] = float(freq)
        _oolog("%s: Abgleich laeuft - %.3f MHz wird danach eingestellt." % (label, freq))
        _onair_dbg(t, "TUNE waehrend Abgleich zurueckgestellt: %.3f" % freq)
        return
    e["last_rf"] = float(freq)
    try:
        app_config_save()
    except Exception:
        pass
    ifm = _onair_if(t, freq)
    _oolog("%s: %.3f MHz (ZF %.3f MHz, SR %s, %s)" % (label, freq, ifm, sr,
           "AutoBW" if e.get("autobw") else ("Kanalbreite aus SR" if sr else "ohne Kanalbreite")))
    t_tune = time.time()
    _onair_dbg(t, "TUNE rf %.3f zf %.4f sr %s" % (freq, ifm, sr))
    ok = False
    _cur = _onair_st[t].get("rf")
    try:
        _sr_change = (not _onair_is_app(t)) and (not e.get("autobw")) and sr is not None and \
                     int(sr) != int(_onair_st[t].get("start_sr") or 0)
    except (TypeError, ValueError):
        _sr_change = False
    if _onair_running(t) and _cur is not None and abs(float(_cur) - float(freq)) < 0.001 and not _sr_change:
        # gleiche Frequenz: KEIN API-Aufruf - das Skript wuerde onair-cli sonst unnoetig neu starten (~6 s)
        _onair_dbg(t, "TUNE uebersprungen - laeuft schon auf %.3f" % freq)
        _oolog("%s: laeuft schon auf %.3f MHz - kein Neustart." % (label, freq))
        return
    if _sr_change:
        # andere Symbolrate -> andere --bw: das geht nur ueber einen Neustart des Skripts
        _oolog("%s: Symbolrate %s -> %s - Neustart mit neuer Kanalbreite." % (label, _onair_st[t].get("start_sr"), sr))
    elif _onair_running(t):
        try:
            if _onair_is_app(t):
                _r = _onair_app_rate(sr)
                if _r and _r != _onair_st[t].get("app_rate"):
                    try:
                        _onair_api(t, "/api/v1/samplerate", {"msps": _r}, timeout=5.0)
                        _onair_st[t]["app_rate"] = _r
                        _oolog("%s: Abtastrate im OnAir-Fenster %.1f Msps (SR %s kS)." % (label, _r, sr))
                    except Exception as ex:
                        _oolog("%s: Abtastrate setzen fehlgeschlagen: %s" % (label, ex))
            _onair_api(t, "/api/v1/frequency", {"mhz": round(ifm, 4)}, timeout=3.0)
            ok = True
            _onair_st[t]["rf"] = float(freq)
            _onair_st[t]["sr_set"] = sr
            _onair_st[t]["tuned_at"] = t_tune
            _onair_st[t]["lock_line"] = False
        except Exception as ex:
            _oolog("%s: API-Aufruf fehlgeschlagen (%s) - starte neu." % (label, ex))
    if not ok:
        _onair_start(t, freq, sr=sr)
        t_tune = _onair_st[t].get("tuned_at", t_tune)
    # Player erst nach dem Neustart von onair-cli (~6 s) und Lock neu synchronisieren
    deadline = t_tune + 30.0
    time.sleep(4.0)
    while time.time() < deadline:
        if _onair_st[t].get("tuned_at", 0) > t_tune + 0.5:
            return          # inzwischen neu abgestimmt - der neuere Aufruf kuemmert sich
        st = _onair_st[t]
        if (st.get("lock_line") and st.get("lock_t", 0) > t_tune + 2.0) or \
           ((st.get("tel") or {}).get("locked") and time.time() - t_tune > (3.0 if _onair_is_app(t) else 8.0)):
            _oolog("%s: Lock nach %.1f s - Player wird neu synchronisiert." % (label, time.time() - t_tune))
            _onair_dbg(t, "LOCK nach %.1f s" % (time.time() - t_tune))
            root.after(0, _vlc_resync, t, "OnAir neu abgestimmt (Lock)")
            return
        time.sleep(0.5)
    _oolog("%s: kein Lock innerhalb 30 s auf %.3f MHz." % (label, freq))
    _onair_dbg(t, "KEIN LOCK in 30 s auf %.3f" % freq)

def _onair_chip(t, key, text, bg):
    if key == "state" and _onair_last_state.get(t) != text:
        _onair_last_state[t] = text
        _onair_dbg(t, "Zustand: %s" % text)
    lb = (_onair_chips.get(t) or {}).get(key)
    if lb is not None:
        try:
            lb.configure(text=text, background=bg)
        except tk.TclError:
            pass

def _onair_status_tick():
    GREY, GREEN, ORANGE, RED, YEL = "#d9d9d9", "#2ecc71", "#f39c12", "#e74c3c", "#f1c40f"
    now = time.time()
    for t in _ONAIR_TARGETS:
        st = _onair_st[t]
        p = _onair_procs.get(t)
        alive = p is not None and p.poll() is None
        if _onair_is_app(t):
            alive = bool(st.get("app_ok"))
            p = None
        if p is not None and not alive and st.get("exit") is None:
            st["exit"] = p.returncode
            _oolog("%s: onair_dvbs2_cli.py beendet (Code %s) - Log: %s" % (_TARGET_LABEL.get(t, t), p.returncode,
                                                                         _onair_log_file(t)))
        api = st.get("api") or {}
        _onair_chip(t, "proc", "P", GREEN if alive and api else (ORANGE if alive else RED))
        if st.get("abgleich"):
            _onair_chip(t, "state", "%s %d s" % ("Optimiere" if st.get("opt") else "Abgleich",
                                                  now - st.get("abgl_t", now)), YEL)
        elif not alive:
            _onair_chip(t, "state", "aus" if st.get("exit") is None else "beendet (%s)" % st["exit"], GREY)
        elif _onair_locked(t):
            _onair_chip(t, "state", "LOCK", GREEN)
        elif _onair_is_app(t) and (st.get("tel") or {}).get("state") == 1:
            _onair_chip(t, "state", "rastet ein", ORANGE)
        elif now - st.get("tuned_at", 0) < 10:
            _onair_chip(t, "state", "stimmt ab ...", ORANGE)
        elif api and not api.get("running", True):
            _onair_chip(t, "state", "cli steht", RED)
        else:
            _onair_chip(t, "state", "sucht", ORANGE)
        rf = st.get("rf")
        if rf:
            fhz = api.get("frequencyHz")
            match = fhz is not None and abs(fhz / 1e6 - _onair_if(t, rf)) < 0.002
            _onair_chip(t, "freq", "%.3f" % rf, GREEN if match else ORANGE)
        else:
            _onair_chip(t, "freq", "-", GREY)
        g = api.get("gainDb")
        lv = st.get("level") if (now - st.get("line_t", 0) < 5.0) else None
        _onair_chip(t, "gain", "G%s" % (("%g" % g) if isinstance(g, (int, float)) else "-"), GREY)
        dbm = _onair_dbm(t, lv, g)
        st["dbm"] = dbm
        if lv is None:
            _onair_chip(t, "level", "-", GREY)
        else:
            _onair_chip(t, "level", ("%.1fdBFS" % lv) + ((" %+.0fdBm" % dbm) if (dbm is not None and app_config.get("onair_show_dbm", True)) else ""),
                        RED if lv > -8.0 else GREY)
        fresh = now - st.get("line_t", 0) < 5.0
        es = st.get("esn0")
        if _onair_is_app(t):
            tel = st.get("tel") or {}
            det = tel.get("details") or {}
            fresh = bool(tel)
            es = tel.get("snrDb") if tel.get("snrDb") is not None else det.get("snrDb")
            if not tel.get("locked"):
                es = None          # ohne Lock liefert OnAir Unsinn (z.B. 38 dB beim Einrasten)
            st["ok"], st["bad"] = tel.get("blocksOk"), tel.get("blocksBad")
            srv = det.get("symbolRate")
            st["mod"] = "SR"
            st["sr"] = ("%.3f" % (srv / 1e6 if srv and srv > 1000 else srv)) if isinstance(srv, (int, float)) and srv else None
            st["pk"] = None
        elif es is None:
            es = (st.get("tel") or {}).get("snrDb")
        # Es/N0 von onair-cli entspricht dem MER von LongMynd (Rauschabstand der Konstellation) - Anzeige daher
        # wie dort: "MER: x dB / D+y" mit D = Abstand zur DVB-S2-Decodierschwelle der MODCOD (_MODCOD_THRESH_DB)
        if es is not None and fresh:
            thr = _onair_modcod_thr(st.get("mod"))
            dval = (es - thr) if thr is not None else None
            q = (st.get("tel") or {}).get("quality") or {}
            if _onair_is_app(t):
                # OnAir-Fenster: Marge aus quality nur, wenn dort eine Schwelle bekannt ist (requiredDb > 0) -
                # sonst liefert es 0.0 und der Chip waere faelschlich rot
                req = q.get("requiredDb")
                if isinstance(req, (int, float)) and req > 0:
                    dval = es - req
                else:
                    dval = None
            _onair_chip(t, "esn0", "MER: %.1f dB%s" % (es, (" / D%+.1f" % dval) if dval is not None else ""),
                        _mer_style(es)["background"])
        else:
            _onair_chip(t, "esn0", "MER: -", _mer_style(None)["background"])
        _onair_chip(t, "mod", (st.get("mod") or "MOD -") + ((" " + st["sr"]) if st.get("sr") else ""), YEL)
        okv, badv = st.get("ok"), st.get("bad")
        # rot nur, wenn "bad" in den letzten 10 s gestiegen ist (der Zaehler ist kumulativ)
        if badv is not None:
            if badv != st.get("bad_last"):
                if st.get("bad_last") is not None and badv > st["bad_last"]:
                    st["bad_up_t"] = now
                st["bad_last"] = badv
        _onair_chip(t, "okbad", "ok %s bad %s" % (okv if okv is not None else "-", badv if badv is not None else "-"),
                    RED if (fresh and now - st.get("bad_up_t", 0) < 10.0) else YEL)
        _onair_chip(t, "pk", "Pkt %s" % (st.get("pk") if st.get("pk") is not None else "-"), YEL)
        raw = _onair_chips.get(t, {}).get("raw")
        if raw is not None:
            try:
                raw.configure(text=(st.get("line") or "")[:140] if fresh else "")
            except tk.TclError:
                pass
    root.after(1000, _onair_status_tick)

def _onair_open_log(t):
    f = _onair_log_file(t)
    for prog in ("xdg-open", "mousepad", "gedit"):
        exe = shutil.which(prog)
        if exe:
            subprocess.Popen([exe, f], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
    _oolog("Log: %s" % f)

#----- Auto-Abgleich (HackRF): Knopf "Abgl" neben AScan -----
_ONAIR_RE_PROBE = re.compile(r"LNA\s+(\d+)\s*dB\s+VGA\s+(\d+)\s*dB\s*\.\.\.\s*(.*)")
_ONAIR_RE_BEST = re.compile(r"bestes Ergebnis LNA\s+(\d+)\s*dB,\s*VGA\s+(\d+)\s*dB")
_onair_abgl_buttons = {}
_onair_opt_buttons = {}
_ONAIR_OPT_STEPS = (16, 24, 32, 40, 48, 56, 64, 72, 80, 88)   # Gesamtverstaerkung dB (Skript teilt in LNA/VGA)

def _onair_optimise(t):
    """Knopf "Opt" = "Verstaerkung optimieren" wie im OnAir-Fenster (GainOptimiser), nachgebaut ueber die API:
    Gesamtverstaerkung in Stufen durchfahren (POST /api/v1/gain {db}), je Stufe nach dem Neustart von onair-cli
    auf Lock warten und Es/N0 + Pegel mitteln. Stufen mit Pegel ueber -8 dBFS (Uebersteuerung) zaehlen nicht.
    Bestes Es/N0 gewinnt, innerhalb 0,5 dB die kleinste Verstaerkung (mehr Reserve). Ohne Lock: Pegel am
    naechsten an -16 dBFS. Laeuft auf der aktuellen Frequenz; gespeichert wird wie beim Abgleich erst, wenn
    danach ein Rufzeichen decodiert wird."""
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    if st.get("abgleich") or st.get("abgl_wait_cs"):
        _oolog("%s: Abgleich/Optimierung laeuft bereits." % label)
        return
    if _onair_is_app(t):
        return _onair_app_sweep_start(t, refine=False)
    if _ONAIR_RX[t].get("device", "hackrf") != "hackrf":
        _oolog("%s: Verstaerkung optimieren gibt es im CLI-Modus nur fuer HackRF." % label)
        return
    if not _onair_running(t):
        _oolog("%s: laeuft nicht - erst starten." % label)
        return
    st.update({"abgleich": True, "opt": True, "abgl_t": time.time(), "abgl_wait_cs": 0.0,
               "abgl_return_rf": st.get("rf"), "pending_rf": None})
    _onair_abgl_button(t)
    _set_callsign_display(t, None)
    _oolog("%s: Verstaerkung optimieren auf %.3f MHz - %d Stufen, je ~10-14 s ..." % (label, st.get("rf") or 0, len(_ONAIR_OPT_STEPS)))
    _onair_dbg(t, "OPT Start rf %s" % st.get("rf"))
    threading.Thread(target=_onair_optimise_run, args=(t,), daemon=True).start()

def _onair_optimise_run(t):
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    pts = []
    try:
        old = _onair_api(t, "/api/v1/status") or {}
    except Exception:
        old = {}
    for g in _ONAIR_OPT_STEPS:
        if not st.get("opt"):
            break
        try:
            _onair_api(t, "/api/v1/gain", {"db": g}, timeout=3.0)
        except Exception as ex:
            _oolog("%s: Optimieren - API-Fehler bei %d dB: %s" % (label, g, ex))
            break
        t0 = time.time()
        time.sleep(3.0)
        # warten bis onair-cli neu laeuft und (falls moeglich) lockt
        while time.time() - t0 < 12.0 and not (st.get("lock_line") and st.get("lock_t", 0) > t0 + 2.0):
            time.sleep(0.3)
        es, lv, nlock, nall = [], [], 0, 0
        t1 = time.time()
        while time.time() - t1 < 3.0:
            if st.get("line_t", 0) > t1 - 1.0:
                nall += 1
                if st.get("level") is not None:
                    lv.append(st["level"])
                if st.get("lock_line") and st.get("esn0") is not None:
                    nlock += 1
                    es.append(st["esn0"])
            time.sleep(1.0)
        p = {"g": g, "es": (sum(es) / len(es)) if es else None, "lv": (max(lv) if lv else None),
             "lock": nlock > 0 and nlock * 2 >= max(1, nall)}
        pts.append(p)
        msg = "%s: Optimieren %2d dB -> %s, Pegel %s dBFS" % (label, g,
              ("Es/N0 %.1f dB" % p["es"]) if p["lock"] and p["es"] is not None else "kein Lock",
              ("%.1f" % p["lv"]) if p["lv"] is not None else "-")
        _oolog(msg)
        _onair_dbg(t, msg)
    best = None
    safe = [p for p in pts if p["lv"] is None or p["lv"] <= -8.0]
    locked = [p for p in safe if p["lock"] and p["es"] is not None]
    how = ""
    if locked:
        top = max(p["es"] for p in locked)
        best = min((p for p in locked if p["es"] >= top - 0.5), key=lambda p: p["g"])
        how = "bestes Es/N0 %.1f dB" % best["es"]
    elif safe and any(p["lv"] is not None for p in safe):
        best = min((p for p in safe if p["lv"] is not None), key=lambda p: abs(p["lv"] + 16.0))
        how = "kein Lock - Pegel am naechsten an -16 dBFS"
    if best is None or not st.get("opt"):
        _oolog("%s: Optimieren ohne Ergebnis - zurueck auf die vorherigen Werte." % label)
        try:
            if old.get("lnaDb") is not None:
                _onair_api(t, "/api/v1/gain", {"lna": old.get("lnaDb"), "vga": old.get("vgaDb")}, timeout=3.0)
        except Exception:
            pass
        st["abgleich"] = False
        root.after(0, _onair_abgl_finish, t)
        return
    try:
        _onair_api(t, "/api/v1/gain", {"db": best["g"]}, timeout=3.0)
        time.sleep(1.0)
        now_st = _onair_api(t, "/api/v1/status") or {}
    except Exception:
        now_st = {}
    lna = int(round(now_st.get("lnaDb") or 0))
    vga = int(round(now_st.get("vgaDb") or 0))
    st["abgleich"] = False
    st["abgl_wait_cs"] = time.time()
    msg = "%s: Optimieren fertig nach %d s: %d dB (LNA %d / VGA %d), %s - warte auf Rufzeichen zum Speichern" % (
        label, time.time() - st.get("abgl_t", time.time()), best["g"], lna, vga, how)
    _oolog(msg)
    _onair_dbg(t, msg)
    root.after(0, _onair_abgl_button, t)
    root.after(1000, _onair_abgl_cs_watch, t, lna, vga)

def _onair_abgl_line(t, s):
    """Reader-Thread: Ausgabe des Auto-Abgleichs auswerten und melden."""
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    m = _ONAIR_RE_PROBE.search(s)
    if m:
        msg = "%s: Abgleich LNA %s / VGA %s -> %s" % (label, m.group(1), m.group(2), m.group(3).strip())
        _oolog(msg)
        _onair_dbg(t, msg)
        return
    m = _ONAIR_RE_BEST.search(s)
    if m:
        st["abgl_result"] = (int(m.group(1)), int(m.group(2)))
        st["abgleich"] = False
        _onair_dbg(t, "ABGLEICH Ergebnis: " + s)
        return
    if "keiner Einstellung ein Lock" in s:
        st["abgl_fail"] = True
        st["abgleich"] = False
    _oolog("%s: %s" % (label, s[:160]))
    _onair_dbg(t, "ABGLEICH: " + s)

def _onair_abgl_button(t):
    b = _onair_abgl_buttons.get(t)
    if b is None:
        return
    st = _onair_st[t]
    busy = (st.get("abgleich") or st.get("abgl_wait_cs")) and not st.get("opt")
    try:
        b.configure(text=("Abgl..." if busy else "Abgl"), style=("AbglRun.TButton" if busy else "Abgl.TButton"))
    except tk.TclError:
        pass
    b2 = _onair_opt_buttons.get(t)
    if b2 is not None:
        busy2 = bool(st.get("opt"))
        try:
            b2.configure(text=("Opt..." if busy2 else "Opt"), style=("AbglRun.TButton" if busy2 else "Abgl.TButton"))
        except tk.TclError:
            pass

def _onair_app_agc(t, on):
    """API-Modus: AGC im OnAir-Fenster schalten. Gepatchtes OnAir: POST /api/v1/x/agc. Original: AUS geht
    ueber POST /api/v1/gain (setzt die feste Verstaerkung, schaltet die AGC ab), AN geht nur im Fenster."""
    label = _TARGET_LABEL.get(t, t)
    try:
        _onair_api(t, "/api/v1/x/agc", {"on": bool(on)}, timeout=4.0)
        _oolog("%s: AGC im OnAir-Fenster %s." % (label, "an" if on else "aus"))
        return True
    except urllib.error.HTTPError as ex:
        if ex.code != 404:
            _oolog("%s: AGC per API fehlgeschlagen: HTTP %s" % (label, ex.code))
            return False
    except Exception as ex:
        _oolog("%s: AGC per API fehlgeschlagen: %s" % (label, ex))
        return False
    if not on:
        try:
            g = float(str(_ONAIR_RX[t].get("gain") or "59").replace(",", "."))
            _onair_api(t, "/api/v1/gain", {"db": g}, timeout=4.0)
            _oolog("%s: AGC aus - feste Verstaerkung %g dB gesetzt." % (label, g))
            return True
        except Exception as ex:
            _oolog("%s: AGC aus fehlgeschlagen: %s" % (label, ex))
            return False
    _oolog("%s: dieses OnAir kann die AGC nicht per API einschalten (Original) - im Fenster den Haken 'AGC' "
           "setzen, oder das gepatchte OnAir nehmen (Konfig > OnAir-Programm)." % label)
    return False

_onair_agc_buttons = {}

def _onair_agc_button(t):
    b = _onair_agc_buttons.get(t)
    if b is not None:
        on = bool(_ONAIR_RX.get(t, {}).get("agc"))
        try:
            b.configure(text=("AGC an" if on else "AGC"), style=("AbglRun.TButton" if on else "Abgl.TButton"))
        except tk.TclError:
            pass

def _onair_agc_toggle(t):
    """Knopf "AGC" (gelb, orange = an): automatische Verstaerkungsregelung an/aus."""
    e = _ONAIR_RX[t]
    st = _onair_st[t]
    new = not bool(e.get("agc"))
    if st.get("abgleich") or st.get("abgl_wait_cs"):
        _oolog("%s: Abgleich laeuft - AGC danach." % _TARGET_LABEL.get(t, t))
        return
    if _onair_is_app(t):
        def run():
            if _onair_app_agc(t, new):
                e["agc"] = new
                app_config_save()
            root.after(0, _onair_agc_button, t)
        threading.Thread(target=run, daemon=True).start()
        return
    e["agc"] = new
    app_config_save()
    _onair_agc_button(t)
    _oolog("%s: AGC %s - onair-cli wird %s neu gestartet." % (_TARGET_LABEL.get(t, t), "an" if new else "aus",
           "ohne LNA/VGA" if new else "mit LNA %s / VGA %s" % (e.get("lna", 40), e.get("vga", 12))))
    threading.Thread(target=_onair_start, args=(t, st.get("rf") or _ONAIR_BEACON_MHZ),
                     kwargs={"sr": st.get("sr_set")}, daemon=True).start()

#----- Hilfe beim Darueberfahren: Bild aus dem OnAir-Fenster mit umrahmter Option + Text -----
_ONAIR_HELP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "onair_help")
_ONAIR_HELP = {
    "abgl": ("onair_help_abgl.png",
             "Abgl = 'Auto-Abgleich' im OnAir-Fenster (gelb umrahmt).\n"
             "CLI/HackRF: LNA/VGA-Proben auf der Bake (--abgleich, ~1,5-2 min).\n"
             "API-Modus (z.B. SDRplay): Gain-Sweep live ueber die API + Feinabstimmung +-4 dB.\n"
             "Gespeichert wird erst, wenn danach ein Rufzeichen decodiert wird."),
    "opt": ("onair_help_opt.png",
            "Opt = 'Verstaerkung optimieren' im OnAir-Fenster (gelb umrahmt).\n"
            "Faehrt die Gesamtverstaerkung in 10 Stufen durch, misst Es/N0 und Uebersteuerung,\n"
            "waehlt das beste Es/N0 (bei Gleichstand +-0,5 dB die kleinste Verstaerkung).\n"
            "Laeuft auf der aktuellen Frequenz; Speichern erst nach decodiertem Rufzeichen."),
    "agc": ("onair_help_agc.png",
            "AGC = Haken 'AGC' im OnAir-Fenster (gelb umrahmt): automatische Verstaerkungsregelung.\n"
            "Orange = an. CLI/HackRF: onair-cli startet ohne feste LNA/VGA neu.\n"
            "API-Modus: gepatchtes OnAir schaltet an/aus, Original-OnAir nur aus (an im Fenster)."),
}
_onair_help_imgs = {}
_onair_help_win = [None]

def _onair_help_show(widget, key):
    _onair_help_hide()
    fn, txt = _ONAIR_HELP.get(key, (None, ""))
    try:
        w = tk.Toplevel(root)
        w.wm_overrideredirect(True)
        w.attributes("-topmost", True)
        fr = tk.Frame(w, background="#ffffe0", borderwidth=1, relief='solid')
        fr.pack()
        img = _onair_help_imgs.get(fn)
        if img is None and fn and os.path.isfile(os.path.join(_ONAIR_HELP_DIR, fn)):
            try:
                img = tk.PhotoImage(file=os.path.join(_ONAIR_HELP_DIR, fn))
                _onair_help_imgs[fn] = img
            except tk.TclError:
                img = None
        if img is not None:
            tk.Label(fr, image=img, background="#ffffe0").pack(padx=4, pady=(4, 2))
        tk.Label(fr, text=txt, justify='left', background="#ffffe0", font=('', 9)).pack(padx=6, pady=(0, 4))
        x = widget.winfo_rootx() + 10
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        w.geometry("+%d+%d" % (x, y))
        _onair_help_win[0] = w
    except tk.TclError:
        pass

def _onair_help_hide(*_a):
    w = _onair_help_win[0]
    _onair_help_win[0] = None
    if w is not None:
        try:
            w.destroy()
        except tk.TclError:
            pass

def _onair_help_bind(widget, key):
    job = [None]
    def enter(_e):
        job[0] = root.after(500, lambda: _onair_help_show(widget, key))   # erst nach 0,5 s Verweilen
    def leave(_e):
        if job[0]:
            root.after_cancel(job[0])
            job[0] = None
        _onair_help_hide()
    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", leave, add="+")

def _onair_app_sweep_start(t, refine):
    """API-Modus (OnAir-Fenster, z.B. SDRplay): Abgl/Opt nachgebaut wie "Verstaerkung optimieren" im Fenster -
    die Gesamtverstaerkung wird per POST /api/v1/gain LIVE umgestellt (kein Neustart), je Stufe 1,2 s warten,
    dann ~2 s Es/N0 (Telemetrie, nur bei Lock) und Clipping (Spektrum clipFraction) messen. Stufen mit Clipping
    > 0,1 % zaehlen nicht; bestes Es/N0 gewinnt, innerhalb 0,5 dB die kleinste Verstaerkung. refine=True (Abgl):
    danach noch +-4 dB in 2-dB-Schritten um den Gewinner. Gespeichert (gain) erst nach decodiertem Rufzeichen."""
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    if not _onair_running(t):
        _oolog("%s: Empfang im OnAir-Fenster laeuft nicht - erst starten." % label)
        return
    st.update({"abgleich": True, "opt": not refine, "abgl_t": time.time(), "abgl_wait_cs": 0.0,
               "abgl_return_rf": st.get("rf"), "pending_rf": None})
    _onair_abgl_button(t)
    _set_callsign_display(t, None)
    _oolog("%s: %s ueber die API auf %.3f MHz ..." % (label, "Auto-Abgleich" if refine else "Verstaerkung optimieren",
                                                     st.get("rf") or 0))
    threading.Thread(target=_onair_app_sweep_run, args=(t, refine), daemon=True).start()

def _onair_app_sweep_run(t, refine):
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    try:
        stat = _onair_api(t, "/api/v1/status") or {}
    except Exception:
        stat = {}
    g0 = stat.get("gainDb")
    gmin = stat.get("gainMinDb") if isinstance(stat.get("gainMinDb"), (int, float)) else 0
    gmax = stat.get("gainMaxDb") if isinstance(stat.get("gainMaxDb"), (int, float)) and stat.get("gainMaxDb") > gmin else 60
    pts = {}

    def measure(g):
        try:
            _onair_api(t, "/api/v1/gain", {"db": g}, timeout=4.0)
        except Exception as ex:
            _oolog("%s: Gain %d dB setzen fehlgeschlagen: %s" % (label, g, ex))
            return None
        time.sleep(1.2)
        es, cl, nlock, nall = [], 0.0, 0, 0
        for _i in range(4):
            try:
                tel = _onair_api(t, "/api/v1/telemetry") or {}
                sp = _onair_api(t, "/api/v1/spectrum?bins=16") or {}
            except Exception:
                tel, sp = {}, {}
            nall += 1
            if isinstance(sp.get("clipFraction"), (int, float)):
                cl = max(cl, sp["clipFraction"])
            if tel.get("locked") and isinstance(tel.get("snrDb"), (int, float)):
                nlock += 1
                es.append(tel["snrDb"])
            time.sleep(0.5)
        p = {"g": g, "es": (sum(es) / len(es)) if es else None, "clip": cl, "lock": nlock * 2 >= nall and nlock > 0}
        msg = "%s: %s %2d dB -> %s, Clipping %.2f %%" % (label, "Abgleich" if refine else "Optimieren", g,
              ("Es/N0 %.1f dB" % p["es"]) if p["lock"] else "kein Lock", cl * 100.0)
        _oolog(msg)
        _onair_dbg(t, msg)
        return p

    steps = sorted({int(round(gmin + (gmax - gmin) * i / 9.0)) for i in range(10)})
    for g in steps:
        if not st.get("abgleich"):
            break
        p = measure(g)
        if p:
            pts[g] = p

    def choose():
        ok = [p for p in pts.values() if p["lock"] and p["clip"] <= 0.001]
        if not ok:
            return None
        top = max(p["es"] for p in ok)
        return min((p for p in ok if p["es"] >= top - 0.5), key=lambda p: p["g"])

    best = choose()
    if best and refine and st.get("abgleich"):
        for g in range(int(best["g"]) - 4, int(best["g"]) + 5, 2):
            if g < gmin or g > gmax or g in pts or not st.get("abgleich"):
                continue
            p = measure(g)
            if p:
                pts[g] = p
        best = choose()
    st["abgleich"] = False
    if best is None:
        _oolog("%s: kein Lock ohne Clipping gefunden - zurueck auf %s dB." % (label, g0))
        try:
            if isinstance(g0, (int, float)):
                _onair_api(t, "/api/v1/gain", {"db": g0}, timeout=4.0)
        except Exception:
            pass
        root.after(0, _onair_abgl_finish, t)
        return
    try:
        _onair_api(t, "/api/v1/gain", {"db": best["g"]}, timeout=4.0)
    except Exception:
        pass
    st["abgl_wait_cs"] = time.time()
    msg = "%s: %s fertig nach %d s: %d dB (Es/N0 %.1f dB, vorher %s dB) - warte auf Rufzeichen zum Speichern" % (
        label, "Abgleich" if refine else "Optimieren", time.time() - st.get("abgl_t", time.time()), best["g"], best["es"], g0)
    _oolog(msg)
    _onair_dbg(t, msg)
    root.after(0, _onair_abgl_button, t)
    root.after(1000, _onair_gain_cs_watch, t, int(best["g"]))

def _onair_gain_cs_watch(t, g):
    """Wie _onair_abgl_cs_watch, aber fuer die Gesamtverstaerkung (API-Modus): Speichern erst bei Rufzeichen."""
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    var = _target_callsign_var.get(t)
    cs = (var.get() if var is not None else "").strip()
    if cs:
        e = _ONAIR_RX[t]
        alt = e.get("gain")
        e["gain"] = str(g)
        app_config_save()
        _oolog("%s: Rufzeichen %s decodiert - Gain %d dB gespeichert (vorher %s)." % (label, cs, g, alt))
        _onair_abgl_finish(t)
        return
    if time.time() - st.get("abgl_wait_cs", 0) > 60:
        _oolog("%s: kein Rufzeichen innerhalb 60 s - Gain %d dB NICHT gespeichert (bleibt bis zum Neustart eingestellt). "
               "Laeuft die UDP-Ausgabe im OnAir-Fenster?" % (label, g))
        _onair_abgl_finish(t)
        return
    root.after(1000, _onair_gain_cs_watch, t, g)

def _onair_abgleich(t):
    """Knopf "Abgl": Auto-Abgleich von LNA/VGA auf der Bake (bis ~1 min). Die Werte werden erst gespeichert,
    wenn danach ein Rufzeichen decodiert wird; anschliessend zurueck auf die vorherige Frequenz."""
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    if st.get("abgleich") or st.get("abgl_wait_cs"):
        _oolog("%s: Abgleich laeuft bereits." % label)
        return
    if _onair_is_app(t):
        return _onair_app_sweep_start(t, refine=True)
    if _ONAIR_RX[t].get("device", "hackrf") != "hackrf":
        _oolog("%s: Auto-Abgleich gibt es im CLI-Modus nur fuer HackRF." % label)
        return
    st.update({"abgleich": True, "abgl_t": time.time(), "abgl_result": None, "abgl_fail": False,
               "abgl_wait_cs": 0.0, "abgl_return_rf": st.get("rf"), "pending_rf": None})
    _onair_abgl_button(t)
    _set_callsign_display(t, None)      # altes Rufzeichen weg - gespeichert wird erst bei NEUER Decodierung
    n = max(10, int(app_config.get("onair_abgleich_zeit") or 10))
    _oolog("%s: Auto-Abgleich startet auf der Bake %.3f MHz (bis zu 9 Proben a %d s) ..." % (label, _ONAIR_BEACON_MHZ, n))
    _onair_dbg(t, "ABGLEICH Start auf %.3f, %d s je Probe, bisher LNA %s VGA %s" %
               (_ONAIR_BEACON_MHZ, n, _ONAIR_RX[t].get("lna", 40), _ONAIR_RX[t].get("vga", 12)))
    threading.Thread(target=_onair_start, args=(t, _ONAIR_BEACON_MHZ, True), daemon=True).start()
    root.after(2000, _onair_abgl_watch, t)

def _onair_abgl_watch(t):
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    if st.get("abgleich"):
        age = time.time() - st.get("abgl_t", 0)
        if age > 240 or (age > 10 and not _onair_running(t)):
            st["abgleich"] = False
            st["abgl_fail"] = True
        else:
            root.after(1000, _onair_abgl_watch, t)
            return
    dur = time.time() - st.get("abgl_t", time.time())
    res = st.get("abgl_result")
    if res:
        lna, vga = res
        msg = "%s: Abgleich fertig nach %d s: LNA %d dB, VGA %d dB (gesamt %d dB) - warte auf Rufzeichen zum Speichern" % (
            label, dur, lna, vga, lna + vga)
        _oolog(msg)
        _onair_dbg(t, msg)
        st["abgl_wait_cs"] = time.time()
        _onair_abgl_button(t)
        root.after(1000, _onair_abgl_cs_watch, t, lna, vga)
        return
    msg = "%s: Abgleich ohne Ergebnis nach %d s (kein Lock) - Neustart mit den bisherigen Werten." % (label, dur)
    _oolog(msg)
    _onair_dbg(t, msg)
    _onair_abgl_button(t)
    threading.Thread(target=_onair_start, args=(t, st.get("pending_rf") or st.get("abgl_return_rf") or _ONAIR_BEACON_MHZ),
                     daemon=True).start()

def _onair_abgl_cs_watch(t, lna, vga):
    st = _onair_st[t]
    label = _TARGET_LABEL.get(t, t)
    var = _target_callsign_var.get(t)
    cs = (var.get() if var is not None else "").strip()
    if cs:
        e = _ONAIR_RX[t]
        alt = (e.get("lna", 40), e.get("vga", 12))
        e["lna"], e["vga"] = lna, vga
        app_config_save()
        msg = "%s: Rufzeichen %s decodiert - LNA %d / VGA %d gespeichert (vorher %s / %s), gilt auch beim naechsten Start." % (
            label, cs, lna, vga, alt[0], alt[1])
        _oolog(msg)
        _onair_dbg(t, msg)
        _onair_abgl_finish(t)
        return
    if time.time() - st.get("abgl_wait_cs", 0) > 60:
        msg = "%s: kein Rufzeichen innerhalb 60 s - LNA %d / VGA %d NICHT gespeichert (laufen aber bis zum naechsten Neustart)." % (
            label, lna, vga)
        _oolog(msg)
        _onair_dbg(t, msg)
        _onair_abgl_finish(t)
        return
    root.after(1000, _onair_abgl_cs_watch, t, lna, vga)

def _onair_abgl_finish(t):
    st = _onair_st[t]
    st["abgl_wait_cs"] = 0.0
    st["opt"] = False
    _onair_abgl_button(t)
    rf = st.get("pending_rf") or st.get("abgl_return_rf")
    st["pending_rf"] = None
    if rf and abs(float(rf) - _ONAIR_BEACON_MHZ) > 0.001:
        _oolog("%s: zurueck auf %.3f MHz." % (_TARGET_LABEL.get(t, t), rf))
        threading.Thread(target=_onair_tune, args=(t, float(rf), _target_cur_sr.get(t)), daemon=True).start()

def _onair_autobw_toggle(t, on):
    """Checkbox "AutoBW": an = --autobw (onair-cli misst die Kanalbreite, ~8-9 s bis Lock), aus = feste
    --bw aus der Symbolrate (SR x onair_bw_faktor, ~6 s bis Lock). Sofort mit der neuen Einstellung neu abstimmen."""
    st = _onair_st[t]
    _ONAIR_RX[t]["autobw"] = bool(on)
    app_config_save()
    if st.get("abgleich") or st.get("abgl_wait_cs"):
        _oolog("%s: AutoBW %s gespeichert - gilt nach dem Abgleich." % (_TARGET_LABEL.get(t, t), "an" if on else "aus"))
        return
    rf = st.get("rf") or _ONAIR_BEACON_MHZ
    _oolog("%s: AutoBW %s - stimme %.3f MHz neu ab." % (_TARGET_LABEL.get(t, t), "an" if on else "aus", rf))
    _onair_dbg(t, "AutoBW %s, Neustart rf %.3f sr %s" % (on, rf, st.get("sr_set")))
    def run():
        _onair_start(t, rf, sr=st.get("sr_set"))
        t0 = st.get("tuned_at", time.time())
        time.sleep(4.0)
        while time.time() < t0 + 30.0:
            if st.get("tuned_at", 0) > t0 + 0.5:
                return
            if st.get("lock_line") and st.get("lock_t", 0) > t0 + 2.0:
                _oolog("%s: Lock nach %.1f s (AutoBW %s)." % (_TARGET_LABEL.get(t, t), time.time() - t0, "an" if on else "aus"))
                root.after(0, _vlc_resync, t, "OnAir neu abgestimmt (AutoBW)")
                return
            time.sleep(0.5)
        _oolog("%s: kein Lock innerhalb 30 s (AutoBW %s)." % (_TARGET_LABEL.get(t, t), "an" if on else "aus"))
    threading.Thread(target=run, daemon=True).start()

def _onair_kill_all():
    """GUI-Start: ALLE noch laufenden onair_dvbs2_cli.py und onair-cli beenden, ohne Rueckfrage (egal von
    wem/welchem Port - eine verwaiste onair-cli haelt sonst den HackRF fest und der neue Start bekommt
    kein Signal). Erst SIGTERM, nach 6 s SIGKILL."""
    pids = []
    for line in _ps_lines():
        pid_s, _sp, args = line.partition(" ")
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        prog = os.path.basename(args.split()[0]) if args.split() else ""
        if "onair_dvbs2_cli.py" in args or "onair_dvbs2_api.py" in args or prog in ("onair-cli", "dect2cli"):
            pids.append((pid, args))
    if not pids:
        _onair_dbg(None, "Start: keine alten OnAir-Prozesse")
        return
    for pid, args in pids:
        _oolog("OnAir: alter Prozess %d wird beendet: %s" % (pid, args[:140]))
        _onair_dbg(None, "Start: beende %d %s" % (pid, args))
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    t_end = time.time() + 6.0
    alive = [p for p, _a in pids]
    while alive and time.time() < t_end:
        time.sleep(0.3)
        nxt = []
        for pid in alive:
            try:
                os.kill(pid, 0)
                nxt.append(pid)
            except OSError:
                pass
        alive = nxt
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
            _oolog("OnAir: Prozess %d mit SIGKILL beendet." % pid)
            _onair_dbg(None, "Start: SIGKILL %d" % pid)
        except OSError:
            pass
    time.sleep(0.8)    # HackRF-USB freigeben lassen

def _onair_startup():
    _onair_bind_config()
    def run():
        _onair_kill_all()
        for t in _ONAIR_TARGETS:
            threading.Thread(target=_onair_start, args=(t, _ONAIR_BEACON_MHZ), daemon=True).start()
    threading.Thread(target=run, daemon=True).start()
    threading.Thread(target=_onair_api_loop, daemon=True).start()
    root.after(1500, _onair_status_tick)
    root.after(300, _onair_logwin_tick)
    if app_config.get("onair_logwin_open", True):
        root.after(2500, lambda: _onair_logwin_open(None))

#===== datvRX: Laufzeit (Pipeline "Quelle | datvrx" ueber DATVision/datvrx_rx.sh, Statusauswertung, UDP-Ausgabe) =====
DATVRX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATVision")
try:        # gemeinsamer Kern fuer Rufzeichen (SDT), MER, Pegel, Bitrate - Ordner DATV-RX-Dienst (auch vom Programm ohne GUI benutzt)
    _drx_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATV-RX-Dienst")
    if os.path.isdir(_drx_dir) and _drx_dir not in sys.path:
        sys.path.insert(0, _drx_dir)
    import datv_core as _dcore
except Exception as _ex:
    _dcore = None
    print("datv_core nicht verfuegbar (%r) - datvRX ohne MER/Pegel/Rufzeichen-Anzeige" % (_ex,))

try:        # Client fuer den RTL-SDR-datvRX-Dienst (Pi): Sticks als Empfaenger einbinden
    import dienst_client as _dsc
except Exception as _ex:
    _dsc = None
    print("dienst_client nicht verfuegbar (%r) - keine RTL-Dienst-Empfaenger" % (_ex,))

def _dvx_is_dienst(t):
    return (_DATVRX_RX.get(t) or {}).get("source") == "dienst"

_dvx_st = {t: {"proc": None, "freq": None, "sr": None, "lock": False, "t_status": 0.0, "line": "", "esn0": None,
               "mod": "", "rs": None, "ok": None, "bad": None, "carrier_hz": None, "user_stopped": False,
               "start_t": 0.0, "lock_t": None, "fails": 0, "log": deque(maxlen=400), "gen": 0,
               "mer": deque(maxlen=24), "ts": None, "pegel": {}}
           for t in _DATVRX_TARGETS}
_dvx_chips = {}          # target -> {key: Label}
_dvx_lock = {t: threading.Lock() for t in _DATVRX_TARGETS}
_DVX_RE_STATUS = re.compile(r"datvrx \[\s*([\d.]+) s\]\s+(.*)")

def _dvx_cmd(t, freq, sr):
    """Kommandozeile fuer datvrx_rx.sh (cd ins Verzeichnis, Quelle als Parameter)."""
    e = _DATVRX_RX.get(t) or {}
    src = e.get("source", "rtl-sdr")
    try:
        lo = float(e.get("lo") or lnblo.get())
    except (TypeError, ValueError, NameError):
        lo = 9540.0
    try:
        lo -= float(e.get("lo_corr_khz") or 0.0) / 1000.0              # Korrektur aus dem LO-Abgleich (Bake-Offset)
    except (TypeError, ValueError):
        pass
    a = ["./datvrx_rx.sh", src, "%.3f" % float(freq), str(int(sr or 0)), "--lo", "%.4f" % lo, "--cells", "1024"]
    if e.get("rate"):
        a += ["--rate", str(e["rate"])]
    if src == "hackrf":
        a += ["--lna", str(e.get("lna", 32)), "--vga", str(e.get("vga", 30))]
        if e.get("amp"):
            a += ["--amp"]
        if e.get("serial"):
            a += ["--serial", str(e["serial"])]
    else:
        a += ["--gain", str(e.get("gain", 40))]
    if src == "rtl-sdr":
        try:
            _ppm0 = int(ppm.get())
        except (TypeError, ValueError, NameError):
            _ppm0 = 0
        a += ["--index", str(_dvx_dev_index(t)), "--ppm", str(e.get("ppm", _ppm0))]
    elif src == "pluto":
        a += ["--host", str(e.get("host") or "192.168.2.1")]
    elif src == "sdrplay":
        a += ["--ant", str(e.get("ant") or "A")]
    elif src == "soapy":
        a += ["--args", str(e.get("args") or "")]
    if str(e.get("carrier", "")).strip() not in ("", "auto"):
        a += ["--carrier", str(e["carrier"]).strip()]
    if e.get("use_sr"):
        a += ["--rs"]
    return a

_dvx_assign = {}       # target -> Stick-dict {"index","name","serial"} (oder False = kein freier Stick); leer = nicht automatisch zugeordnet
_dvx_head_lbl = {}     # target -> Label der Spaltenueberschrift (wird nach der Zuordnung mit der Stick-Nummer aktualisiert)

def _dvx_plan_sticks(rx_entries, sticks):
    """Reine Planung (ohne Hardware, testbar): rx_entries = [(target, entry)] der RTL-Empfaenger in Reihenfolge der Nummer,
    sticks = [{"index","name","serial"}] in Bus-Reihenfolge. 1) Empfaenger mit Seriennummer bekommen genau diesen Stick (nur wenn die Seriennummer
    auf dem Bus eindeutig ist), 2) alle anderen der Reihe nach den naechsten freien Stick (erster Stick -> erster Empfaenger, zweiter -> zweiter ...).
    Rueckgabe {target: Stick-dict oder False (kein Stick mehr frei bzw. gewaehlte Seriennummer nicht am Bus)}."""
    free, plan = list(sticks), {}
    for t, e in rx_entries:
        sn = (e.get("serial") or "").strip()
        if sn and sum(1 for x in sticks if x["serial"] == sn) == 1:
            m = [x for x in free if x["serial"] == sn]
            if m:
                plan[t] = m[0]
                free.remove(m[0])
    for t, e in rx_entries:
        if t in plan:
            continue
        sn = (e.get("serial") or "").strip()
        if sn and not any(x["serial"] == sn for x in sticks):
            plan[t] = False                 # ausdruecklich gewaehlter Stick haengt nicht am Bus: lieber nicht starten als einen fremden nehmen
        else:
            plan[t] = free.pop(0) if free else False
    return plan

def _dvx_enum_rtl():
    """RTL-Sticks auf dem USB-Bus (rtl_test -t, wie die Geraetesuche im Dialog): [{"index","name","serial"}]; [] wenn nicht lesbar."""
    if not shutil.which("rtl_test"):
        return []
    try:
        r = subprocess.run(["timeout", "4", "rtl_test", "-t"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace", timeout=12)
        out = r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for m in re.finditer(r"^\s*(\d+):\s+(.+)$", out, re.M):
        sn = re.search(r"SN:\s*(\S+)", m.group(2))
        found.append({"index": int(m.group(1)), "name": m.group(2).strip(), "serial": sn.group(1) if sn else ""})
    return found

def _dvx_assign_sticks():
    """Beim Start (Hintergrund-Thread): bei zwei oder mehr RTL-SDR-datvRX (oder angegebener Seriennummer) die Sticks auf dem Bus suchen und
    den Empfaengern zuordnen. Ein einzelner Empfaenger ohne Seriennummer behaelt seine eingestellte Stick-Nr. Abschaltbar: app_config["dvx_stick_auto"] = false."""
    if not app_config.get("dvx_stick_auto", True):
        return
    rx = [(t, _DATVRX_RX[t]) for t in _DATVRX_TARGETS if (_DATVRX_RX[t].get("source", "rtl-sdr") == "rtl-sdr")]
    if len(rx) < 2 and not any((e.get("serial") or "").strip() for _t, e in rx):
        return
    sticks = _dvx_enum_rtl()
    if not sticks:
        root.after(0, olog, "RTL-SDR-datvRX: keine RTL-Sticks auf dem Bus gefunden (rtl_test) - Stick-Nummern wie eingestellt.")
        return
    plan = _dvx_plan_sticks(rx, sticks)
    _dvx_assign.update(plan)
    for t, st_ in plan.items():
        n = _dvx_num(t)
        if st_:
            txt = "RTL-SDR-datvRX%d (Stick #%d)" % (n, st_["index"])
            root.after(0, olog, "%s: Stick #%d zugeordnet (%s)" % (txt.split(" (")[0], st_["index"], st_["name"]))
        else:
            txt = "RTL-SDR-datvRX%d (kein Stick)" % n
            _dvx_st[t]["user_stopped"] = True          # nicht dauernd neu starten (Watchdog)
            root.after(0, olog, "RTL-SDR-datvRX%d: kein freier RTL-Stick auf dem Bus (%d Stick(s), %d Empfaenger) - nicht gestartet." % (n, len(sticks), len(rx)))
        _TARGET_LABEL[t] = txt
        lb = _dvx_head_lbl.get(t)
        if lb is not None:
            root.after(0, lambda l=lb, x=txt: l.configure(text=x))

def _dvx_dev_index(t):
    """Stick-Nummer fuer rtl_sdr -d: zugeordneter Stick, sonst die im Empfaenger eingestellte Nummer."""
    a = _dvx_assign.get(t)
    return a["index"] if a else (_DATVRX_RX.get(t) or {}).get("index", 0)

def _dvx_pegel(t, freq, sr):
    """Pegel am RTL-Stick vor dem Start (1 s, dB ueber Rauschen + dBFS); nur Quelle rtl-sdr und bis 1000 kS, sonst {}."""
    e = _DATVRX_RX.get(t) or {}
    if _dcore is None or e.get("source", "rtl-sdr") != "rtl-sdr" or not sr or int(sr) > 1000:
        return {}
    try:
        lo = float(e.get("lo") or lnblo.get())
    except (TypeError, ValueError, NameError):
        lo = 9540.0
    try:
        pp = int(e.get("ppm", ppm.get()))
    except (TypeError, ValueError, NameError):
        pp = 0
    time.sleep(0.4)                       # rtl_sdr des vorigen Empfangs muss den Stick freigegeben haben
    return _dcore.pegel(float(freq), int(sr), lo=lo, ppm=pp, index=_dvx_dev_index(t), gain=e.get("gain", 40))

def _dvx_log(t, line):
    st = _dvx_st[t]
    st["log"].append("%s %s" % (time.strftime("%H:%M:%S"), line))

def _dvx_stop(t, quiet=False):
    st = _dvx_st.get(t)
    if not st:
        return
    if _dvx_is_dienst(t):                       # Stick des Dienstes: nur die Abfrage beenden; nicht leise (Knopf) = Stick am Pi freigeben
        st["proc"] = None
        st["lock"] = False
        e = _DATVRX_RX.get(t) or {}
        if not quiet and _dsc is not None:
            threading.Thread(target=lambda: (_dsc.free(e.get("server", ""), int(e.get("stick", 1))),
                                             root.after(0, olog, "%s: Stick am Dienst freigegeben." % _TARGET_LABEL.get(t, t))), daemon=True).start()
        return
    p = st.get("proc")
    st["proc"] = None
    st["lock"] = False
    if p is not None and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        if not quiet:
            root.after(0, olog, "%s: datvrx gestoppt." % _TARGET_LABEL.get(t, t))

def _dvx_stop_all():
    for t in _DATVRX_TARGETS:
        _dvx_st[t]["user_stopped"] = True
        _dvx_stop(t, quiet=True)

def _dvx_start(t, freq=None, sr=None):
    """Pipeline (neu) starten - auch zum Umstimmen (datvrx stimmt nicht im Lauf um). Hintergrund-Thread."""
    st = _dvx_st[t]
    if _dvx_assign.get(t) is False:
        return                              # kein freier RTL-Stick fuer diesen Empfaenger (siehe _dvx_assign_sticks)
    if _dvx_is_dienst(t):
        return _dvs_start(t, freq, sr)
    with _dvx_lock[t]:
        freq = freq if freq is not None else (st.get("freq") or BATC_BEACON_NOMINAL_MHZ)
        sr = sr if sr is not None else (st.get("sr") or 1500)
        _dvx_stop(t, quiet=True)
        if not os.path.isfile(os.path.join(DATVRX_DIR, "datvrx_rx.sh")) or not os.path.isfile(os.path.join(DATVRX_DIR, "datvrx")):
            root.after(0, olog, "%s: %s/datvrx bzw. datvrx_rx.sh fehlt." % (_TARGET_LABEL.get(t, t), DATVRX_DIR))
            return
        pg = _dvx_pegel(t, freq, sr) if app_config.get("dvx_pegel", True) else {}
        cmd = _dvx_cmd(t, freq, sr)
        st.update(freq=float(freq), sr=sr, user_stopped=False, lock=False, lock_t=None, t_status=0.0,
                  esn0=None, mod="", rs=None, ok=None, bad=None, carrier_hz=None, line="", start_t=time.time())
        st["mer"].clear()
        st["ts"] = _dcore.TsInfo() if _dcore is not None else None
        st["pegel"] = pg
        st["gen"] += 1
        gen = st["gen"]
        try:
            p = subprocess.Popen(cmd, cwd=DATVRX_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        except OSError as ex:
            root.after(0, olog, "%s: datvrx nicht startbar: %s" % (_TARGET_LABEL.get(t, t), ex))
            return
        st["proc"] = p
        _dvx_log(t, "START cd %s && %s" % (DATVRX_DIR, " ".join(cmd)))
        root.after(0, olog, "%s: %.3f MHz / %s kS - datvrx gestartet." % (_TARGET_LABEL.get(t, t), float(freq), sr))
    threading.Thread(target=_dvx_ts_reader, args=(t, p, gen), daemon=True).start()
    threading.Thread(target=_dvx_err_reader, args=(t, p, gen), daemon=True).start()

#----- Verteilfunktion (Autoverteiler) am Pi: die GUI verteilt selbst, deshalb den Verteiler des Dienstes abschalten (API /api/v1/einstellungen) -----
_dvs_prev = {}          # Dienst -> Zustand des Pi-Verteilers vor dem Abschalten (True = war an), fuer das Zurueckschalten beim Beenden

def _dvs_servers():
    return sorted({(_DATVRX_RX[t] or {}).get("server", "") for t in _DATVRX_TARGETS if _dvx_is_dienst(t) and (_DATVRX_RX[t] or {}).get("server")})

def _dvs_verteiler_set(server, an, remember=False):
    """Verteilfunktion des Dienstes (Pi) ein-/ausschalten; remember=True merkt den alten Zustand fuer das Zurueckschalten beim Beenden. Blockiert kurz (Netz)."""
    if _dsc is None:
        return None
    try:
        if remember and server not in _dvs_prev:
            _dvs_prev[server] = bool(_dsc.einstellungen(server)["verteiler"]["an"])
        r = _dsc.set_einstellungen(server, {"verteiler": bool(an)})
        root.after(0, olog, "RTL-Dienst %s: Verteilfunktion am Pi %s." % (server, "AN" if r["verteiler"]["an"] else "AUS (die GUI verteilt)"))
        return bool(r["verteiler"]["an"])
    except (OSError, ValueError, KeyError) as ex:
        root.after(0, olog, "RTL-Dienst %s: Verteilfunktion konnte nicht umgeschaltet werden (%s)" % (server, getattr(ex, "reason", ex)))
        return None

def _dvs_verteiler_startup():
    """beim Start: Pi-Verteiler aus (Haken 'Verteilfunktion am Pi abschalten' im Dialog RTL-Dienst, Standard an)"""
    if app_config.get("dienst_verteiler_aus", True):
        for srv in _dvs_servers():
            _dvs_verteiler_set(srv, False, remember=True)

def _dvs_verteiler_restore():
    """beim echten Beenden der GUI: Pi-Verteiler wieder so stellen wie vorher (nicht beim GUI-Neustart)"""
    for srv, was_on in list(_dvs_prev.items()):
        try:
            if was_on and _dsc is not None:
                _dsc.set_einstellungen(srv, {"verteiler": True}, timeout=3)
        except (OSError, ValueError):
            pass

def _dvs_start(t, freq=None, sr=None, tune=True):
    """Stick des RTL-SDR-datvRX-Dienstes als Empfaenger: tune=True stellt ihn per /manuell/set auf freq/sr (Stick bleibt fest), danach fragt ein Poller
    jede Sekunde /api/v1/stick/N ab und fuellt dieselben Statusfelder wie die lokale Pipeline; der Strom kommt vom Pi per UDP-Multicast (Adresse des Eintrags)."""
    st = _dvx_st[t]
    e = _DATVRX_RX.get(t) or {}
    if _dsc is None:
        root.after(0, olog, "%s: dienst_client.py fehlt." % _TARGET_LABEL.get(t, t))
        return
    server, nr = e.get("server", ""), int(e.get("stick", 1))
    with _dvx_lock[t]:
        _dvx_stop(t, quiet=True)
        st["gen"] += 1
        gen = st["gen"]
        st.update(user_stopped=False, lock=False, lock_t=None, t_status=0.0, esn0=None, mod="", rs=None, ok=None, bad=None, carrier_hz=None, line="", start_t=time.time())
        if freq is not None:
            st.update(freq=float(freq), sr=sr)
        if tune and freq is not None:
            try:
                ok = _dsc.tune(server, nr, freq, int(sr or 0))
            except (OSError, ValueError) as ex:
                root.after(0, olog, "%s: Dienst %s nicht erreichbar (%s)" % (_TARGET_LABEL.get(t, t), server, getattr(ex, "reason", ex)))
                ok = False
            if ok:
                root.after(0, olog, "%s: %.3f MHz / %s kS - Stick am Dienst eingestellt." % (_TARGET_LABEL.get(t, t), float(freq), sr or "auto"))
            else:
                root.after(0, olog, "%s: Dienst konnte den Stick nicht einstellen (Bake nicht gelockt / Stick belegt?)" % _TARGET_LABEL.get(t, t))
        pol = _dsc.Poller(server, nr, st, gen, on_lock=lambda: root.after(0, _dvx_on_lock, t), on_log=lambda m: root.after(0, olog, "%s: %s" % (_TARGET_LABEL.get(t, t), m)))
        pol.start()

def _dvx_ts_reader(t, p, gen):
    """TS von datvrx (stdout) in 1316-Byte-Datagrammen (7 x 188) an die UDP-Adresse des Empfaengers."""
    ip, port = _dvx_udp(t)
    so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        so.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        so.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    except OSError:
        pass
    buf = b""
    ts = _dvx_st[t].get("ts")             # Rufzeichen (SDT), Bitrate, TEI/CC aus dem Strom
    try:
        while True:
            d = p.stdout.read(65536) if hasattr(p.stdout, "read") else b""
            if not d:
                break
            if ts is not None:
                try:
                    ts.feed(d)
                except Exception:
                    pass
            buf += d
            # auf Paketanfang (0x47) ausrichten
            if buf and buf[0] != 0x47:
                i = buf.find(b"\x47")
                buf = buf[i:] if i >= 0 else b""
            while len(buf) >= 1316:
                so.sendto(buf[:1316], (ip, port))
                buf = buf[1316:]
    except (OSError, ValueError):
        pass
    finally:
        so.close()

def _dvx_err_reader(t, p, gen):
    """Statuszeilen von datvrx (stderr) auswerten: Lock, Es/N0, MODCOD, Symbolrate, ok/bad, Traegerversatz."""
    st = _dvx_st[t]
    pend = b""
    try:
        while True:
            d = p.stderr.read(4096)
            if not d:
                break
            pend += d
            parts = re.split(rb"[\r\n]", pend)
            pend = parts.pop()
            for raw in parts:
                ln = raw.decode("utf-8", "replace").strip()
                if not ln:
                    continue
                if ln.startswith("datvrx cells"):
                    if _dcore is not None and gen == st["gen"] and st.get("lock") and "QPSK" in (st.get("mod") or ""):
                        _mv = _dcore.mer_qpsk(ln[13:])
                        if _mv is not None:
                            st["mer"].append(_mv)
                    continue
                if ln.startswith("datvrx carrier"):
                    try:
                        st["carrier_hz"] = float(ln.split()[2])
                    except (IndexError, ValueError):
                        pass
                    continue
                if gen != st["gen"]:
                    return
                m = _DVX_RE_STATUS.match(ln)
                if m:
                    body = m.group(2)
                    st["line"] = body
                    st["t_status"] = time.time()
                    lock = "TS lock" in body
                    if lock and not st["lock"]:
                        st["lock_t"] = time.time()
                        root.after(0, _dvx_on_lock, t)
                    st["lock"] = lock
                    mm = re.search(r"Es/N0\s+(-?[\d.]+)\s*dB", body) or re.search(r"SNR\s+(-?[\d.]+)\s*dB", body)
                    st["esn0"] = float(mm.group(1)) if mm else None
                    mm = re.search(r"(DVB-S2?)\s+(\w+\s+\d+/\d+(?:\s+(?:pilots|short))*)", body)
                    st["mod"] = ("%s %s" % (mm.group(1), mm.group(2))) if mm else ""
                    mm = re.search(r"([\d.]+)\s+Msym/s", body)
                    st["rs"] = float(mm.group(1)) * 1000 if mm else None
                    mm = re.search(r"ok\s+(\d+)\s+bad\s+(\d+)", body)
                    if mm:
                        st["ok"], st["bad"] = int(mm.group(1)), int(mm.group(2))
                    continue
                _dvx_log(t, ln)
                if ln.startswith("[quelle]") and re.search(r"(?i)no supported devices|failed|error|usb_claim|busy|not found", ln):
                    root.after(0, olog, "%s: Quelle meldet: %s" % (_TARGET_LABEL.get(t, t), ln[8:].strip()))
                elif ln.startswith("datvrx_rx: FEHLER"):
                    root.after(0, olog, "%s: %s" % (_TARGET_LABEL.get(t, t), ln))
    except (OSError, ValueError):
        pass
    if gen == st["gen"]:
        st["lock"] = False
        _dvx_log(t, "ENDE (Exit %s)" % p.poll())

def _dvx_on_lock(t):
    st = _dvx_st[t]
    olog("%s: Lock nach %.1f s - %s" % (_TARGET_LABEL.get(t, t), time.time() - st["start_t"], st.get("mod") or "?"))
    _vlc_resync(t, "datvrx neu abgestimmt (Lock)")

def _dvx_watch_loop():
    """Absturz/Ende der Pipeline: still neu starten (5 s, dann 30 s, dann alle 2 min), bis der Nutzer stoppt."""
    while True:
        time.sleep(5)
        for t in _DATVRX_TARGETS:
            if _dvx_is_dienst(t):
                continue                    # der Poller des Dienst-Sticks kuemmert sich selbst um Wiederverbinden
            st = _dvx_st[t]
            p = st.get("proc")
            if st.get("user_stopped") or (p is not None and p.poll() is None):
                if p is not None and p.poll() is None and st.get("lock"):
                    st["fails"] = 0
                continue
            if time.time() - st.get("start_t", 0) < (5 if st["fails"] == 0 else 30 if st["fails"] < 3 else 120):
                continue
            st["fails"] += 1
            if st["fails"] == 1:
                last = list(st["log"])[-3:]
                root.after(0, olog, "%s: datvrx laeuft nicht (mehr) - neu starten. Letzte Meldungen: %s" % (
                    _TARGET_LABEL.get(t, t), " | ".join(x[9:] for x in last) or "-"))
            _dvx_start(t)

def _dvx_chip_tick():
    """Hauptthread 1x/s: Statusfelder der datvRX-Spalten."""
    try:
        for t, oc in _dvx_chips.items():
            st = _dvx_st[t]
            p = st.get("proc")
            run = p is not None and p.poll() is None
            fresh = time.time() - st.get("t_status", 0) < 4.0
            oc["proc"].configure(text="P", background=("#2ecc71" if run else "#c0392b"), foreground="white")
            if not run:
                oc["state"].configure(text="gestoppt" if st.get("user_stopped") else "aus", background="#d9d9d9")
            elif st.get("lock") and fresh:
                oc["state"].configure(text="LOCK", background="#2ecc71")
            elif fresh and "carrier found" in st.get("line", ""):
                oc["state"].configure(text="Traeger da", background="#f39c12")
            else:
                oc["state"].configure(text="sucht", background="#e67e22")
            oc["freq"].configure(text=("%.3f" % st["freq"]) if st.get("freq") else "-",
                                 background=("#2ecc71" if (run and st.get("lock") and fresh) else "#d9d9d9"))      # gelockt: Frequenz gruen
            e = _DATVRX_RX.get(t) or {}
            ua = st.get("udp_api")
            if e.get("source") == "dienst" and ua and ua != e.get("udp") and ":" in str(ua):       # Pi sendet jetzt auf einer anderen Adresse: Spalte nachziehen
                olog("%s: UDP-Adresse am Pi geaendert %s -> %s - Spalte angepasst." % (_TARGET_LABEL.get(t, t), e.get("udp"), ua))
                e["udp"] = ua
                for r_ in (app_config.get("datvrx_receivers") or []):
                    if isinstance(r_, dict) and r_.get("source") == "dienst" and r_.get("server") == e.get("server") and int(r_.get("stick", 0)) == int(e.get("stick", 0)):
                        r_["udp"] = ua
                app_config_save()
            oc["gain"].configure(text=("RTL%s" % e.get("stick", 1)) if e.get("source") == "dienst" else ("G%s" % e.get("gain", 40)) if e.get("source") != "hackrf"
                                 else "L%s/V%s" % (e.get("lna", 32), e.get("vga", 30)))
            ch = st.get("carrier_hz")
            oc["level"].configure(text=("Offs %+.1f kHz" % ((ch or 0) / 1000.0 - _dvx_offset_khz(t))) if (ch is not None and st.get("lock")) else "Offs -")
            oc["esn0"].configure(text=("Es/N0 %.1f dB" % st["esn0"]) if (st.get("esn0") is not None and fresh) else "Es/N0 -")
            oc["mod"].configure(text=(st.get("mod") or "MOD -") + ((" %.0fk" % st["rs"]) if st.get("rs") else ""))
            oc["okbad"].configure(text=("ok %s bad %s" % (st["ok"], st["bad"])) if st.get("ok") is not None else "ok - bad -")
            if "mer" in oc:
                if _dvx_is_dienst(t):          # Pi liefert pro Abfrage nur EINEN (schon gemittelten) Wert -> mer_mean braucht >= 10 Werte und waere immer leer
                    mm_ = st["mer"][-1] if (st["mer"] and st.get("lock") and fresh) else None
                else:
                    mm_ = _dcore.mer_mean(st["mer"]) if (_dcore is not None and st.get("lock") and fresh) else None
                oc["mer"].configure(text=("MER %.1f" % mm_) if mm_ is not None else "MER -", **_mer_style(mm_))
                pg_ = st.get("pegel") or {}
                oc["pegel"].configure(text=("Pegel %+.1f dB" % pg_["db"]) if "db" in pg_ else "Pegel -")
                ts_ = st.get("ts")
                if ts_ is not None and run:
                    mb_, nu_ = ts_.stats()
                    oc["rate"].configure(text="%.2f Mb/s" % mb_)
                    oc["err"].configure(text="T%d C%d" % (ts_.tei, ts_.cc_err))
                    oc["rufz"].configure(text=(ts_.service + (("  [%s]" % ts_.provider) if ts_.provider else "")) if ts_.service else "Rufz. -",
                                         background=("#2ecc71" if ts_.service else "#d9d9d9"))
                else:
                    oc["rate"].configure(text="- Mb/s")
                    oc["err"].configure(text="T- C-")
                    oc["rufz"].configure(text="Rufz. -", background="#d9d9d9")
            oc["raw"].configure(text=st.get("line", "")[:110])
    except Exception as ex:
        dlog("datvRX-Anzeige: %r" % (ex,))
    root.after(1000, _dvx_chip_tick)

def _dvs_hostnames_apply():
    """Pi-Name statt datvRX-Nummer in Klammern: 'RTL-Dienst RTL1 (P5NDI1)' (Name aus der API des Pi, sonst DNS); im Hintergrund, damit der Start nicht wartet"""
    def work():
        cache = {}
        for t in _DATVRX_TARGETS:
            e = _DATVRX_RX.get(t) or {}
            if e.get("source") != "dienst" or _dsc is None:
                continue
            sv = e.get("server", "")
            if sv not in cache:
                cache[sv] = _dsc.fetch_host(sv)
            h = cache[sv]
            if not h:
                continue
            txt = "RTL-Dienst RTL%s (%s)" % (e.get("stick", 1), h)
            _TARGET_LABEL[t] = txt
            lb = _dvx_head_lbl.get(t)
            if lb is not None:
                root.after(0, lambda l=lb, x=txt: l.configure(text=x))
    threading.Thread(target=work, daemon=True).start()

def _dvx_offset_khz(t):
    """Versatz, mit dem datvrx_rx.sh den Stick neben die Station legt (wie dort berechnet)."""
    e = _DATVRX_RX.get(t) or {}
    c = str(e.get("carrier", "")).strip()
    if c not in ("", "auto"):
        try:
            return float(c)
        except ValueError:
            return 0.0
    rate = float(e.get("rate") or (2.4 if e.get("source", "rtl-sdr") == "rtl-sdr" else 4.0))
    sr = float(_dvx_st[t].get("sr") or 1000)
    return 300.0 if sr * 0.675 + 360 < rate * 500 else 0.0

_dvx_bake_off = {}      # target -> gemessener Bake-Offset (kHz)
_dvx_bake_t0 = [0.0]

def _dvx_bake_offset_tick():
    """'Bake beim Start anzeigen' an: sobald ein datvRX-/RTL-Dienst-Tuner auf der Bake gelockt ist, den Traegerversatz (Offs) pruefen und melden;
    grosse Abweichung = LO/ppm des Sticks bzw. LNB pruefen. Je Tuner einmal, hoechstens 3 Minuten lang."""
    if not _dvx_bake_t0[0]:
        _dvx_bake_t0[0] = time.time()
    try:
        for t in _DATVRX_TARGETS:
            if t in _dvx_bake_off:
                continue
            st = _dvx_st[t]
            if not (st.get("lock") and st.get("freq") and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) <= 0.5 and st.get("carrier_hz") is not None):
                continue
            off = st["carrier_hz"] / 1000.0 - _dvx_offset_khz(t)
            _dvx_bake_off[t] = off
            olog("Bake-Offset %s: %+.1f kHz%s" % (_TARGET_LABEL.get(t, t), off,
                 "  - GROSS: LO/ppm des Sticks bzw. LNB pruefen" if abs(off) > 150 else ("  (ok)" if abs(off) <= 100 else "  (erhoeht)")))
    except Exception as ex:
        dlog("Bake-Offset-Pruefung: %r" % (ex,))
    if any(t not in _dvx_bake_off for t in _DATVRX_TARGETS) and time.time() - _dvx_bake_t0[0] < 180:
        root.after(5000, _dvx_bake_offset_tick)

def _dvx_startup():
    """Beim GUI-Start: alle aktiven datvRX auf der Bake starten (danach verteilt die Automatik)."""
    def _go():
        try:
            _dvx_assign_sticks()            # Sticks auf dem Bus den RTL-SDR-datvRX-Empfaengern zuordnen (ab 2 Empfaengern)
        except Exception as ex:
            dlog("datvRX Stick-Zuordnung: %r" % (ex,))
        threading.Thread(target=_dvs_verteiler_startup, daemon=True).start()
        for i, t in enumerate(_DATVRX_TARGETS):
            if _dvx_is_dienst(t):          # Stick des Dienstes: nicht umstellen, nur Status anzeigen (der Pi verteilt selbst)
                threading.Thread(target=_dvs_start, args=(t,), kwargs={"tune": False}, daemon=True).start()
                continue
            if _dvx_assign.get(t) is False:
                continue
            if not app_config.get("bake_beim_start", True):
                continue                   # Konfig: 'Bake beim Start anzeigen' aus - lokale datvRX bleiben gestoppt, bis ihnen ein Signal zugeteilt wird
            root.after(1500 * i, lambda tt=t: threading.Thread(
                target=_dvx_start, args=(tt, BATC_BEACON_NOMINAL_MHZ, 1500), daemon=True).start())
    threading.Thread(target=_go, daemon=True).start()
    threading.Thread(target=_dvx_watch_loop, daemon=True).start()
    root.after(2000, _dvx_chip_tick)
    root.after(3000, _dvs_hostnames_apply)
    if app_config.get("bake_beim_start", True) and _DATVRX_TARGETS:
        root.after(15000, _dvx_bake_offset_tick)      # Bake-Offset der datvRX-/RTL-Dienst-Tuner pruefen

#----- Log-Fenster -----
def _dvx_logwin_open(t=None):
    win = tk.Toplevel(root)
    win.title("datvRX-Log" + ((" - " + _TARGET_LABEL.get(t, t)) if t else " (alle)"))
    txt = tk.Text(win, width=120, height=30, font=("TkFixedFont", 9))
    txt.pack(fill="both", expand=True)
    def fill():
        if not win.winfo_exists():
            return
        rows = []
        for tt in ([t] if t else _DATVRX_TARGETS):
            st = _dvx_st.get(tt) or {}
            rows += ["[%s] %s" % (_TARGET_LABEL.get(tt, tt), x) for x in list(st.get("log", []))[-200:]]
            if st.get("line"):
                rows.append("[%s] Status: %s" % (_TARGET_LABEL.get(tt, tt), st["line"]))
        txt.delete("1.0", "end")
        txt.insert("end", "\n".join(rows[-400:]) or "(noch keine Meldungen)")
        txt.see("end")
        win.after(1500, fill)
    fill()

#----- Geraete suchen / Empfaenger anlegen + bearbeiten -----
def _dvx_find_devices(cb):
    """rtl_test -t, hackrf_info, SoapySDRUtil --find - Ergebnis [(Text, Eintrag)] an cb im Hauptthread."""
    def run():
        found = []
        def sh(args, timeout=12):
            if not shutil.which(args[0]):
                return None
            try:
                r = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", timeout=timeout)
                return r.stdout
            except (OSError, subprocess.TimeoutExpired):
                return ""
        out = sh(["timeout", "4", "rtl_test", "-t"])
        if out is not None:
            for m in re.finditer(r"^\s*(\d+):\s+(.+)$", out, re.M):
                found.append(("RTL-Stick #%s: %s" % (m.group(1), m.group(2).strip()), {"source": "rtl-sdr", "index": int(m.group(1))}))
        out = sh(["hackrf_info"])
        if out:
            for m in re.finditer(r"Serial number:\s*(\S+)", out):
                found.append(("HackRF %s" % m.group(1)[-8:], {"source": "hackrf", "serial": m.group(1)}))
        out = sh(["SoapySDRUtil", "--find"], timeout=20)
        if out:
            for blk in out.split("Found device")[1:]:
                kv = dict(re.findall(r"(\w+)\s*=\s*(.+)", blk))
                drv = (kv.get("driver") or "").strip()
                if drv == "plutosdr":
                    host = (kv.get("hostname") or "192.168.2.1").strip()
                    found.append(("PlutoSDR %s" % host, {"source": "pluto", "host": host}))
                elif drv == "sdrplay":
                    lab = (kv.get("label") or "SDRplay").strip()
                    for a in ("A", "B", "C"):
                        found.append(("%s Antenne %s" % (lab, a), {"source": "sdrplay", "ant": a}))
                elif drv and drv not in ("rtlsdr", "hackrf", "audio"):
                    args = ",".join("%s=%s" % (k, v.strip()) for k, v in kv.items() if k in ("driver", "serial", "label")[:2])
                    found.append(("Soapy %s" % drv, {"source": "soapy", "args": args or "driver=%s" % drv}))
        tools = ", ".join("%s %s" % (x, "ok" if shutil.which(x) else "fehlt") for x in ("rtl_sdr", "hackrf_transfer", "rx_sdr"))
        root.after(0, cb, found, tools)
    threading.Thread(target=run, daemon=True).start()

def _dvx_rx_dialog(idx=None):
    """Empfaenger idx (0-basiert in app_config['datvrx_receivers']) bearbeiten, None = neuen anlegen."""
    lst = list(app_config.get("datvrx_receivers") or [])
    if idx is None and len(lst) >= _DATVRX_MAX:
        messagebox.showinfo("datvRX", "Maximal %d datvRX-Empfaenger." % _DATVRX_MAX)
        return
    e = dict(lst[idx]) if idx is not None and idx < len(lst) else {"source": "rtl-sdr", "enabled": True}
    num = (idx + 1) if idx is not None else len(lst) + 1
    win = tk.Toplevel(root)
    win.title("datvRX%d - Empfaenger %s" % (num, "bearbeiten" if idx is not None else "hinzufuegen"))
    win.transient(root)
    v = {k: tk.StringVar(value=str(e.get(k, d))) for k, d in (
        ("source", "rtl-sdr"), ("index", 0), ("serial", ""), ("host", "192.168.2.1"), ("ant", "A"), ("args", ""),
        ("rate", ""), ("gain", 40), ("lna", 32), ("vga", 30), ("ppm", ppm.get() if "ppm" in globals() else 0),
        ("lo", ""), ("udp", ""), ("carrier", "auto"))}
    ven = tk.BooleanVar(value=bool(e.get("enabled", True)))
    vrs = tk.BooleanVar(value=bool(e.get("use_sr", False)))
    vamp = tk.BooleanVar(value=bool(e.get("amp", False)))
    frm = ttk.Frame(win, padding=10)
    frm.pack(fill="both", expand=True)
    r = 0
    ttk.Label(frm, text="Gefundene Geraete:").grid(row=r, column=0, sticky="w")
    found_var = tk.StringVar(value="suche ...")
    cmb = ttk.Combobox(frm, textvariable=found_var, width=46, state="readonly")
    cmb.grid(row=r, column=1, columnspan=3, sticky="we")
    found_map = {}
    def on_found(found, tools):
        if not win.winfo_exists():
            return
        found_map.clear()
        found_map.update({txt: ent for txt, ent in found})
        cmb.configure(values=list(found_map) or ["(keine gefunden)"])
        found_var.set("bitte waehlen ..." if found_map else "(keine gefunden)")
        tools_var.set("Programme: " + tools)
    def on_pick(_e=None):
        ent = found_map.get(found_var.get())
        if ent:
            for k, val in ent.items():
                if k in v:
                    v[k].set(str(val))
    cmb.bind("<<ComboboxSelected>>", on_pick)
    r += 1
    tools_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=tools_var, font=('', 8), foreground="#666666").grid(row=r, column=0, columnspan=4, sticky="w")
    r += 1
    def row(label, key, w=18, hint=""):
        nonlocal r
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=1)
        ttk.Entry(frm, textvariable=v[key], width=w).grid(row=r, column=1, sticky="w", pady=1)
        if hint:
            ttk.Label(frm, text=hint, font=('', 8), foreground="#666666").grid(row=r, column=2, columnspan=2, sticky="w")
        r += 1
    ttk.Label(frm, text="Quelle:").grid(row=r, column=0, sticky="w")
    ttk.Combobox(frm, textvariable=v["source"], values=["rtl-sdr", "hackrf", "pluto", "sdrplay", "soapy"],
                 width=10, state="readonly").grid(row=r, column=1, sticky="w")
    r += 1
    row("RTL-Stick Nr.:", "index", 6, "rtl-sdr")
    row("ppm:", "ppm", 6, "rtl-sdr")
    row("HackRF-Seriennr.:", "serial", 24, "hackrf (leer = erster)")
    row("Pluto-Adresse:", "host", 18, "pluto")
    row("SDRplay-Antenne:", "ant", 4, "sdrplay: A, B oder C (Hi-Z)")
    row("Soapy-Argumente:", "args", 24, "soapy, z.B. driver=lime")
    row("Abtastrate MS/s:", "rate", 6, "leer = rtl-sdr 2.4, sonst 4")
    row("Gain dB:", "gain", 6, "rtl-sdr/pluto/sdrplay/soapy")
    row("HackRF LNA / VGA:", "lna", 6, "")
    ttk.Entry(frm, textvariable=v["vga"], width=6).grid(row=r - 1, column=2, sticky="w")
    ttk.Checkbutton(frm, text="HackRF-Verstaerker (Amp)", variable=vamp).grid(row=r, column=1, columnspan=2, sticky="w")
    r += 1
    row("LNB-LO MHz:", "lo", 10, "leer = LO aus dem Hauptfenster")
    row("UDP-Ausgabe:", "udp", 18, "leer = 230.0.0.%d:1234" % (20 + num))
    row("Versatz kHz:", "carrier", 6, "auto = 300 kHz, wenn es passt")
    ttk.Checkbutton(frm, text="Symbolrate vorgeben (--rs)", variable=vrs).grid(row=r, column=1, columnspan=2, sticky="w")
    r += 1
    ttk.Checkbutton(frm, text="aktiv", variable=ven).grid(row=r, column=1, sticky="w")
    r += 1
    def save(restart_gui=False):
        ne = {"source": v["source"].get().strip() or "rtl-sdr", "enabled": bool(ven.get()),
              "use_sr": bool(vrs.get()), "amp": bool(vamp.get())}
        for k in ("index", "ppm", "gain", "lna", "vga"):
            try:
                ne[k] = int(float(v[k].get().replace(",", ".")))
            except ValueError:
                pass
        for k in ("serial", "host", "ant", "args", "rate", "lo", "udp", "carrier"):
            val = v[k].get().strip()
            if val:
                ne[k] = val
        lst2 = list(app_config.get("datvrx_receivers") or [])
        if idx is not None and idx < len(lst2):
            lst2[idx] = ne
        else:
            lst2.append(ne)
        app_config["datvrx_receivers"] = lst2
        app_config_save()
        t = "datvrx%d" % num
        if t in _DATVRX_RX and not restart_gui:
            _DATVRX_RX[t].clear()
            _DATVRX_RX[t].update(ne)
            olog("datvRX%d gespeichert - Pipeline wird mit den neuen Werten neu gestartet." % num)
            threading.Thread(target=_dvx_start, args=(t,), daemon=True).start()
        else:
            olog("datvRX%d gespeichert - %s." % (num, "GUI startet neu" if restart_gui else
                                                "als Spalte nach dem naechsten GUI-Neustart"))
        win.destroy()
        if restart_gui:
            root.after(300, _gui_restart)
    bf = ttk.Frame(frm)
    bf.grid(row=r, column=0, columnspan=4, sticky="we", pady=(8, 0))
    ttk.Button(bf, text="Abbrechen", command=win.destroy).pack(side="right")
    ttk.Button(bf, text="Speichern", command=lambda: save(False)).pack(side="right", padx=4)
    ttk.Button(bf, text="Speichern + GUI neu starten", command=lambda: save(True)).pack(side="right")
    _dvx_find_devices(on_found)

def _dvs_dialog():
    """Menue datvRX -> RTL-Dienst verbinden: oben die am Pi gefundenen Sticks, unten die zugewiesenen datvRX-Spalten.
    Mehrere Pis moeglich: 'Hinzufuegen' haengt Sticks an, 'Entfernen' nimmt einzelne Spalten wieder weg (nichts wird mehr ueberschrieben)."""
    if _dsc is None:
        messagebox.showerror("RTL-Dienst", "dienst_client.py fehlt (DATV-RX-Dienst).", parent=root)
        return
    win = tk.Toplevel(root)
    win.title("RTL-Dienst verbinden")
    win.transient(root)
    frm = ttk.Frame(win, padding=10)
    frm.pack(fill="both", expand=True)
    cur = next((e for e in (app_config.get("datvrx_receivers") or []) if e.get("source") == "dienst"), {})
    srv = tk.StringVar(value=app_config.get("dienst_server") or cur.get("server") or "192.168.0.102:8554")
    ttk.Label(frm, text="Dienst (Pi) HOST:PORT:").grid(row=0, column=0, sticky="w")
    ent_srv = ttk.Entry(frm, textvariable=srv, width=26)
    ent_srv.grid(row=0, column=1, sticky="w", padx=4)
    ent_srv.bind("<Return>", lambda _e: abfragen())          # Adresse eingetippt + Enter = Sticks dieses Pi abfragen
    ent_srv.focus_set()
    vaus = tk.BooleanVar(value=bool(app_config.get("dienst_verteiler_aus", True)))
    ttk.Checkbutton(frm, text="Verteilfunktion des Pi abschalten, solange die GUI verbunden ist (die GUI verteilt selbst; beim Beenden wieder an)", variable=vaus,
                    command=lambda: (app_config.__setitem__("dienst_verteiler_aus", bool(vaus.get())), app_config_save())).grid(row=1, column=0, columnspan=3, sticky="w")
    ttk.Label(frm, text="Gefunden am Pi (oben waehlen, Hinzufuegen):", font=("", 9, "bold")).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
    cols = ("nr", "name", "udp", "signal", "bake", "stat")
    tv = ttk.Treeview(frm, columns=cols, show="headings", height=5, selectmode="extended")
    for c_, t_, w_ in zip(cols, ("Nr", "Stick", "UDP-Adresse", "aktuelles Signal", "Bake", "Status"), (40, 150, 140, 180, 60, 110)):
        tv.heading(c_, text=t_); tv.column(c_, width=w_)
    tv.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=3)
    ttk.Label(frm, text="Zugewiesene datvRX-Spalten (Tuner in der GUI):", font=("", 9, "bold")).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
    cols2 = ("rx", "dev", "server", "udp")
    tz = ttk.Treeview(frm, columns=cols2, show="headings", height=5, selectmode="extended")
    for c_, t_, w_ in zip(cols2, ("Spalte", "Geraet", "Pi (Dienst)", "UDP-Adresse"), (70, 150, 190, 200)):
        tz.heading(c_, text=t_); tz.column(c_, width=w_)
    tz.heading("dev", text="Geraet")
    tz.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=3)
    info = tk.StringVar(value="Sticks abfragen, markieren und Hinzufuegen: jeder Stick wird eine datvRX-Spalte (voller Tuner) mit eigener UDP-Adresse.")
    ttk.Label(frm, textvariable=info, wraplength=700).grid(row=7, column=0, columnspan=3, sticky="w")
    found = []
    state = {"changed": False}

    def rxs():
        return [e for e in (app_config.get("datvrx_receivers") or []) if isinstance(e, dict)]

    def zeigen():
        tv.delete(*tv.get_children())
        tz.delete(*tz.get_children())
        have = {(e.get("server"), int(e.get("stick", 0))) for e in rxs() if e.get("source") == "dienst"}
        for x in found:
            sg = x.get("signal")
            tv.insert("", "end", iid=str(x["nr"]), values=(x["nr"], "%s (%s)" % (x["name"], x.get("kennung")), x["udp"],
                      ("%.3f MHz %s kS" % (sg["freq_mhz"], sg.get("sr_ks_eingestellt") or "auto")) if sg else "frei", (x.get("bake") or {}).get("status", ""),
                      "zugewiesen" if (srv.get().strip(), int(x["nr"])) in have else ""))
        for i, e in enumerate(rxs()):
            tz.insert("", "end", iid=str(i), values=("datvRX%d" % (i + 1), _dvx_dev_short(e), e.get("server", "") if e.get("source") == "dienst" else "-", e.get("udp", "")))

    def abfragen():
        try:
            lst = _dsc.fetch_sticks(srv.get().strip())
        except (OSError, ValueError) as ex:
            info.set("Dienst nicht erreichbar: %s" % getattr(ex, "reason", ex)); return
        found[:] = lst
        zeigen()
        info.set("%d Stick(s) am Pi %s gefunden." % (len(lst), srv.get().strip()))

    def hinzu(alle=False):
        if not found:
            info.set("Erst Sticks abfragen."); return
        sel = [x for x in found if alle or str(x["nr"]) in tv.selection()]
        if not sel:
            info.set("Oben erst einen oder mehrere Sticks markieren."); return
        lst = rxs()
        server = srv.get().strip()
        have = {(e.get("server"), int(e.get("stick", 0))) for e in lst if e.get("source") == "dienst"}
        udps = {str(e.get("udp", "")) for e in lst if e.get("udp")}
        add, skip = [], []
        for x in sel:
            if (server, int(x["nr"])) in have:
                continue
            if str(x["udp"]) in udps:
                skip.append("RTL%s (%s)" % (x["nr"], x["udp"])); continue
            add.append({"source": "dienst", "server": server, "stick": x["nr"], "udp": x["udp"], "enabled": True})
            udps.add(str(x["udp"]))
        if len(lst) + len(add) > _DATVRX_MAX:
            messagebox.showinfo("RTL-Dienst", "Maximal %d datvRX-Spalten (%d vorhanden + %d neu)." % (_DATVRX_MAX, len(lst), len(add)), parent=win); return
        if add:
            app_config["datvrx_receivers"] = lst + add
            app_config["dienst_server"] = server
            app_config_save()
            state["changed"] = True
        zeigen()
        msg = "%d Stick(s) hinzugefuegt." % len(add) if add else "Nichts hinzugefuegt."
        if skip:
            msg += " NICHT hinzugefuegt: %s - diese UDP-Adresse hat schon eine Spalte (sonst mischen sich die Streams). Der Dienst auf dem Pi waehlt beim Start selbst eine freie Basisadresse (30, 40, 50 ... ueber MQTT qo100/udp/+): Dienst am Pi %s auf den neuesten Stand bringen/neu starten, dann Sticks abfragen." % (", ".join(skip), server)
        info.set(msg)

    def entfernen():
        sel = sorted((int(i) for i in tz.selection()), reverse=True)
        lst = rxs()
        sel = [i for i in sel if i < len(lst) and lst[i].get("source") == "dienst"]
        if not sel:
            info.set("Unten erst eine Dienst-Spalte markieren (andere Empfaenger ueber das datvRX-Menue)."); return
        for i in sel:
            del lst[i]
        app_config["datvrx_receivers"] = lst
        app_config_save()
        state["changed"] = True
        zeigen()
        info.set("%d Spalte(n) entfernt." % len(sel))

    def schliessen():
        win.destroy()
        if state["changed"] and messagebox.askyesno("RTL-Dienst", "Die Spalten wurden geaendert. GUI jetzt neu starten (damit sie wirksam werden)?", parent=root):
            _gui_restart()
    bf = ttk.Frame(frm); bf.grid(row=8, column=0, columnspan=3, sticky="e", pady=6)
    ttk.Button(bf, text="Sticks abfragen", command=abfragen).pack(side="left", padx=3)
    ttk.Button(bf, text="Markierte hinzufuegen", command=lambda: hinzu(False)).pack(side="left", padx=3)
    ttk.Button(bf, text="Alle hinzufuegen", command=lambda: hinzu(True)).pack(side="left", padx=3)
    ttk.Button(bf, text="Markierte entfernen", command=entfernen).pack(side="left", padx=3)
    ttk.Button(bf, text="Pi-Verteilfunktion umschalten", command=lambda: threading.Thread(target=lambda: (lambda cur: _dvs_verteiler_set(srv.get().strip(), not cur, remember=True))(
        bool(_dsc.einstellungen(srv.get().strip())["verteiler"]["an"])), daemon=True).start()).pack(side="left", padx=3)
    ttk.Button(bf, text="Schliessen", command=schliessen).pack(side="left", padx=3)
    win.protocol("WM_DELETE_WINDOW", schliessen)
    zeigen()
    abfragen()

def _multiplayer_start():
    """kleines Uebersichtsfenster (multiplayer.py) starten"""
    mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATV-RX-Dienst", "multiplayer.py")
    try:
        subprocess.Popen([sys.executable, mp], stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as ex:
        messagebox.showerror("MultiPlayer", "nicht startbar: %s" % ex, parent=root)

def _dvx_menu_fill():
    _menu_dvx.delete(0, 'end')
    _menu_dvx.add_command(label="RTL-Dienst verbinden (Sticks des Pi als Tuner) ...", command=_dvs_dialog)
    _menu_dvx.add_command(label="MultiPlayer-Fenster (Uebersicht/Player der Dienst-Sticks)", command=_multiplayer_start)
    for _srv in _dvs_servers():
        _menu_dvx.add_command(label="Verteilfunktion am Pi %s umschalten (aus = die GUI verteilt)" % _srv,
                              command=lambda sv=_srv: threading.Thread(target=lambda: _dvs_verteiler_set(sv, not bool(_dsc.einstellungen(sv)["verteiler"]["an"]), remember=True), daemon=True).start())
    _menu_dvx.add_separator()
    _menu_dvx.add_command(label="Empfaenger hinzufuegen (Geraete suchen) ...", command=lambda: _dvx_rx_dialog(None))
    _menu_dvx.add_separator()
    for i, e in enumerate(app_config.get("datvrx_receivers") or []):
        t = "datvrx%d" % (i + 1)
        mark = "aktiv" if t in _DATVRX_TARGETS else ("aus" if not e.get("enabled", True) else "nach Neustart")
        _menu_dvx.add_command(label="datvRX%d (%s) - %s - bearbeiten ..." % (i + 1, _dvx_dev_short(e), mark),
                              command=lambda ii=i: _dvx_rx_dialog(ii))
    if _DATVRX_TARGETS:
        _menu_dvx.add_separator()
        _menu_dvx.add_command(label="Alle datvRX neu starten",
                              command=lambda: [threading.Thread(target=_dvx_start, args=(tt,), daemon=True).start()
                                               for tt in _DATVRX_TARGETS])
        _menu_dvx.add_command(label="Alle datvRX stoppen",
                              command=lambda: threading.Thread(target=_dvx_stop_all, daemon=True).start())
        _menu_dvx.add_separator()
        _menu_dvx.add_command(label="datvRX-Log-Fenster (alle)", command=lambda: _dvx_logwin_open(None))
    _menu_dvx.add_separator()
    ok = os.path.isfile(os.path.join(DATVRX_DIR, "datvrx")) and os.path.isfile(os.path.join(DATVRX_DIR, "datvrx_rx.sh"))
    _menu_dvx.add_command(label="datvrx: %s" % (DATVRX_DIR if ok else "FEHLT in " + DATVRX_DIR), state="disabled")
    _menu_dvx.add_command(label="GUI neu starten (neue/entfernte datvRX-Spalten)", command=_gui_restart)


#----- GUI-Neustart (fuer neue/entfernte OnAir-Spalten) -----
_gui_restart_requested = [False]

def _gui_restart(once=True):
    _gui_restart_requested[0] = True
    if once:
        os.environ["LEANDVB_ONCE_CONFIG"] = "1"  # naechster Start einmalig: gespeicherte Spalten (OnAir/datvRX) auch bei Ziel-Parametern uebernehmen
    on_exit(keep_direct=True)

#----- Tuner-Konfigurationen: 10 Plaetze (Platz 1 = Standard) -----
_restart_argv = [None]            # Neustart mit diesen Parametern (None = wie gestartet)
_prof_skip_autosave = [False]     # beim Laden/Bearbeiten nicht mit dem alten Stand ueberschreiben

def _prof_strip_argv():
    """Startparameter ohne Ziel-Parameter (-octagon, -longmynd, -onairN, -datvrxN ...): danach gilt der Standard (Platz 1)"""
    names = set(_TARGET_FLAG_NAMES)
    return [sys.argv[0]] + [a for a in sys.argv[1:] if a.lstrip("-") not in names]

def _prof_current():
    """aktuelle Tuner-Konfiguration: Ziele, Octagon-Direktmodus und die OnAir-/datvRX-Listen mit 'enabled' wie gerade aktiv"""
    import copy
    tg = {k: (k in _active_targets) for k, _l, _f in _BASE_TARGETS}
    oa = copy.deepcopy([e for e in (app_config.get("onair_receivers") or []) if isinstance(e, dict)])
    for i, e in enumerate(oa):
        e["enabled"] = ("onair%d" % (i + 1)) in _ONAIR_TARGETS
    dv = copy.deepcopy([e for e in (app_config.get("datvrx_receivers") or []) if isinstance(e, dict)])
    for i, e in enumerate(dv):
        e["enabled"] = ("datvrx%d" % (i + 1)) in _DATVRX_TARGETS
    return {"targets": tg, "octagon_direkt": bool(app_config.get("octagon_direct")) or bool(getattr(cliargs, "octagon_direkt_qrg", False)),
            "onair_receivers": oa, "datvrx_receivers": dv}

def _prof_describe(p):
    """Text: welche Tuner sind in dieser Konfiguration aktiv"""
    if not p:
        return "(leer)"
    parts = []
    for k, lab, _f in _BASE_TARGETS:
        if (p.get("targets") or {}).get(k):
            parts.append(lab + (" (direkt)" if k == "octagon" and p.get("octagon_direkt") else ""))
    for i, e in enumerate(p.get("onair_receivers") or []):
        if e.get("enabled", True):
            parts.append("OnAir%d (%s)" % (i + 1, _onair_dev_short(e)))
    dienst = [e for e in (p.get("datvrx_receivers") or []) if e.get("enabled", True) and e.get("source") == "dienst"]
    for i, e in enumerate(p.get("datvrx_receivers") or []):
        if e.get("enabled", True) and e.get("source") != "dienst":
            parts.append("datvRX%d (%s)" % (i + 1, _dvx_dev_short(e)))
    if dienst:
        parts.append("RTL-Dienst " + ", ".join("RTL%s" % e.get("stick", "?") for e in dienst))
    return ", ".join(parts) or "(keine Tuner)"

def _prof_store(k, name=None):
    profiles = app_config.setdefault("tuner_profiles", {})
    p = _prof_current()
    old = profiles.get(str(k)) or {}
    p["name"] = name or old.get("name") or ("Standard" if k == 1 else "Konfig %d" % k)
    p["saved"] = time.strftime("%Y-%m-%d %H:%M:%S")
    profiles[str(k)] = p
    app_config_save()

def _prof_save_default():
    """beim Beenden: Standard (Platz 1) mit der aktuellen Konfiguration ueberschreiben - nicht bei Start mit Ziel-Parametern"""
    if _cli_mode or _prof_skip_autosave[0]:
        return
    try:
        _prof_store(1)
    except Exception as ex:
        dlog("Standard-Konfiguration speichern: %r" % (ex,))

def _prof_apply_and_restart(p):
    """Konfiguration p wird zum Standard (Platz 1), OnAir-/datvRX-Listen werden uebernommen, GUI startet ohne Ziel-Parameter neu"""
    import copy
    profiles = app_config.setdefault("tuner_profiles", {})
    q = copy.deepcopy(p)
    q["name"] = "Standard"
    q["saved"] = time.strftime("%Y-%m-%d %H:%M:%S")
    profiles["1"] = q
    app_config["onair_receivers"] = copy.deepcopy(p.get("onair_receivers") or app_config.get("onair_receivers") or [])
    app_config["datvrx_receivers"] = copy.deepcopy(p.get("datvrx_receivers") or app_config.get("datvrx_receivers") or [])
    app_config["octagon_direct"] = bool(p.get("octagon_direkt"))
    app_config_save()
    _prof_skip_autosave[0] = True
    _restart_argv[0] = _prof_strip_argv()
    _gui_restart()

def _prof_load_ask(k):
    """Platz k laden: wird zum Standard (Platz 1), GUI startet ohne Ziel-Parameter neu"""
    p = (app_config.get("tuner_profiles") or {}).get(str(k))
    if not p:
        return
    if messagebox.askyesno("Konfiguration laden", "Platz %d laden?\n%s\n\nSie wird zum Standard (Platz 1), die GUI startet neu." % (k, _prof_describe(p)), parent=root):
        _prof_apply_and_restart(p)

def _tuner_active_list():
    """alle gerade aktiven Tuner als [(Anzeigename, Art, Schluessel)] - Art base/onair/datvrx"""
    out = []
    for k, lab, _f in _BASE_TARGETS:
        if k in _active_targets:
            out.append((lab, "base", k))
    for t in _ONAIR_TARGETS:
        out.append((_TARGET_LABEL.get(t, t), "onair", t))
    for t in _DATVRX_TARGETS:
        out.append((_TARGET_LABEL.get(t, t), "datvrx", t))
    return out

def _tuner_remove(label, kind, key):
    """aktiven Tuner entfernen und die GUI neu starten. Mit Ziel-Parametern: Neustart mit den gleichen Parametern ohne diesen Tuner (Standard bleibt
    unberuehrt); sonst: Standard (Platz 1) ohne diesen Tuner speichern und ohne Parameter neu starten."""
    if len(_tuner_active_list()) <= 1:
        messagebox.showinfo("Tuner entfernen", "Mindestens ein Tuner muss aktiv bleiben.", parent=root)
        return
    if not messagebox.askyesno("Tuner entfernen", "'%s' entfernen und die GUI neu starten?\n(Der Tuner bleibt in der Liste und laesst sich wieder aktivieren.)" % label, parent=root):
        return
    if _cli_mode:
        flag = {"rtl_sdr": "rtlsdr", "longmynd_p4": "longmyndp4"}.get(key, key) if kind == "base" else key      # onair2 / datvrx1 / octagon ...
        keep = {("rtlsdr" if k == "rtl_sdr" else "longmyndp4" if k == "longmynd_p4" else k) for _l, kd, k in _tuner_active_list() if kd == "base"}
        keep |= {k for _l, kd, k in _tuner_active_list() if kd in ("onair", "datvrx")}
        keep.discard(flag)
        argv = _prof_strip_argv() + ["-" + f for f in sorted(keep)]       # alle jetzt aktiven Tuner ausser dem entfernten
        _restart_argv[0] = argv
        _prof_skip_autosave[0] = True
        _gui_restart(once=False)
        return
    p = _prof_current()
    if kind == "base":
        p["targets"][key] = False
    else:
        i = int(key[6:] if kind == "datvrx" else key[5:]) - 1
        lst = p["datvrx_receivers"] if kind == "datvrx" else p["onair_receivers"]
        if 0 <= i < len(lst):
            lst[i]["enabled"] = False
    _prof_apply_and_restart(p)

def _ladekonfig_fill():
    """Menue Ladekonfig: die 10 Plaetze mit den aktiven Tunern - Klick laedt (Neustart); Speichern/Verwalten darunter"""
    m = _menu_ladekonfig
    m.delete(0, 'end')
    profiles = app_config.get("tuner_profiles") or {}
    for k in range(1, _PROFILE_SLOTS + 1):
        p = profiles.get(str(k))
        nm = (p or {}).get("name") or ("Standard" if k == 1 else "(frei)")
        if p:
            m.add_command(label="%d  %s:  %s" % (k, nm, _prof_describe(p)), command=lambda kk=k: _prof_load_ask(kk))
        else:
            m.add_command(label="%d  %s" % (k, nm), state="disabled")
    m.add_separator()
    m.add_command(label="Aktuelle Tuner als Standard speichern (Platz 1)", command=lambda: (_prof_store(1), olog("Tuner-Konfiguration: Standard (Platz 1) gespeichert - %s" % _prof_describe(_prof_current()))))
    sub = tk.Menu(m, tearoff=False)
    for k in range(2, _PROFILE_SLOTS + 1):
        def _sv(kk=k):
            old = (app_config.get("tuner_profiles") or {}).get(str(kk)) or {}
            nm = simpledialog.askstring("Konfiguration speichern", "Name fuer Platz %d:" % kk, initialvalue=old.get("name") or "Konfig %d" % kk, parent=root)
            if nm is not None:
                _prof_store(kk, nm)
                olog("Tuner-Konfiguration: Platz %d '%s' gespeichert - %s" % (kk, nm, _prof_describe(_prof_current())))
        sub.add_command(label="Platz %d%s" % (k, (" (" + profiles[str(k)].get("name", "") + ")") if profiles.get(str(k)) else " (frei)"), command=_sv)
    m.add_cascade(label="Aktuelle in Platz speichern ...", menu=sub)
    m.add_separator()
    rm = tk.Menu(m, tearoff=False)
    act = _tuner_active_list()
    for lab, kind, key in act:
        rm.add_command(label=lab, command=lambda l=lab, kd=kind, ky=key: _tuner_remove(l, kd, ky))
    m.add_cascade(label="Aktiven Tuner entfernen ...  (%d aktiv)" % len(act), menu=rm, state=("normal" if len(act) > 1 else "disabled"))
    m.add_command(label="Verwalten: Namen, Loeschen, Tuner an/aus ...", command=_prof_dialog)
    m.add_command(label=("Gestartet mit Parametern: Standard wird beim Beenden NICHT ueberschrieben" if _cli_mode else "Standard (Platz 1) wird beim Beenden gespeichert"), state="disabled")

#----- Menue LO-Abgleich: Bake als Referenz, Offset je Tuner messen und eintragen (RTL-Dienst: Offset kommt vom Dienst -> ignoriert) -----
def _lo_offset_get(key):
    try:
        return float(((app_config.get("lo_abgleich") or {}).get(key) or {}).get("offset_khz") or 0.0)
    except (TypeError, ValueError, NameError):
        return 0.0

def _lo_offset_set(key, off):
    app_config.setdefault("lo_abgleich", {})[key] = {"offset_khz": round(float(off), 1), "zeit": time.strftime("%Y-%m-%d %H:%M:%S")}
    app_config_save()

def _lo_log(txt):
    root.after(0, olog, "LO-Abgleich: " + txt)

def _lo_abgleich_octagon():
    """Octagon-Direkt: die Box meldet keinen Traegerversatz - deshalb Lock-Bereich um die Bake abtasten (50 kHz, an den Raendern 25 kHz); die Mitte ist der Offset.
    Danach stimmt die GUI die Octagon um diesen Offset versetzt ab (TUNE-Frequenz + Offset)."""
    if not (_oct_direct() and _oct_direct_running[0]):
        _lo_log("Octagon: Direktmodus laeuft nicht - uebersprungen.")
        return None
    prev_off = _lo_offset_get("octagon")
    prev_f, prev_sr = _target_cur_freq.get("octagon"), _target_cur_sr.get("octagon")
    pol = app_config.get("octagon_direct_pol", "h")
    _octagon_wait[0] = time.time()
    def probe(d):
        _octagon_wait[0] = time.time()
        res = _oct_direct_send("TUNE %.3f 1500 %s" % (BATC_BEACON_NOMINAL_MHZ + d / 1000.0, pol), timeout=25.0)
        ok = bool(res and res.rstrip().endswith("LOCK=1"))
        _lo_log("Octagon: Bake %+d kHz -> %s" % (d, "LOCK" if ok else "kein Lock"))
        return ok
    d0 = None
    for d in (0, 50, -50, 100, -100, 150, -150, 200, -200, 300, -300, 400, -400, 600, -600):
        if probe(d):
            d0 = d
            break
    if d0 is None:
        _lo_log("Octagon: auf der Bake kein Lock im Bereich +-600 kHz - Antenne/LNB/Box pruefen.")
        return None
    hi = lo = d0
    while hi < 900 and probe(hi + 50):
        hi += 50
    if probe(hi + 25):
        hi += 25
    while lo > -900 and probe(lo - 50):
        lo -= 50
    if probe(lo - 25):
        lo -= 25
    off = (hi + lo) / 2.0
    _lo_offset_set("octagon", off)
    _lo_log("Octagon-Direkt: Lock-Bereich %+d ... %+d kHz -> Offset %+.1f kHz eingetragen (vorher %+.1f)." % (lo, hi, off, prev_off))
    if prev_f:                                    # zurueck auf das vorherige Signal (mit neuem Offset)
        threading.Thread(target=_oct_direct_tune, args=(prev_f, prev_sr or 1500), daemon=True).start()
    return off

def _lo_abgleich_dvx(t):
    """lokaler datvRX (RTL-Stick/HackRF/...): auf die Bake stellen, nach Lock den Traegerversatz messen und als Korrektur (LO) eintragen"""
    e = _DATVRX_RX.get(t) or {}
    st = _dvx_st[t]
    prev_f, prev_sr = st.get("freq"), st.get("sr")
    _dvx_start(t, BATC_BEACON_NOMINAL_MHZ, 1500)
    t0 = time.time()
    while time.time() - t0 < 60:
        time.sleep(1.0)
        if st.get("lock") and st.get("carrier_hz") is not None and time.time() - (st.get("lock_t") or time.time()) >= 4.0:
            break
    else:
        _lo_log("%s: kein Lock auf der Bake - uebersprungen." % _TARGET_LABEL.get(t, t))
        return None
    vals = []
    for _ in range(5):
        time.sleep(0.6)
        if st.get("carrier_hz") is not None:
            vals.append(st["carrier_hz"] / 1000.0 - _dvx_offset_khz(t))
    if not vals:
        return None
    off = sum(vals) / len(vals)
    new = float(e.get("lo_corr_khz") or 0.0) + off          # gemessen mit der bisherigen Korrektur -> Summe
    e["lo_corr_khz"] = round(new, 1)
    try:
        lst = app_config.get("datvrx_receivers") or []
        lst[_dvx_num(t) - 1]["lo_corr_khz"] = round(new, 1)
    except (IndexError, KeyError, TypeError):
        pass
    _lo_offset_set(t, new)
    _lo_log("%s: Bake-Offset %+.1f kHz gemessen -> Korrektur %+.1f kHz eingetragen." % (_TARGET_LABEL.get(t, t), off, new))
    if prev_f and abs(prev_f - BATC_BEACON_NOMINAL_MHZ) > 0.5:
        _dvx_start(t, prev_f, prev_sr)
    else:
        _dvx_start(t, BATC_BEACON_NOMINAL_MHZ, 1500)
    return new

def _lo_abgleich_longmynd():
    """LongMynd: wenn er die Bake empfaengt, die LO einmal so korrigieren, dass die gemessene Bake auf 10491.500 MHz liegt (wie 'LO korrigieren', aber einmalig)"""
    rf = _lm_beacon_rf_mhz("longmynd")
    if rf is None:
        _lo_log("LongMynd: empfaengt die Bake gerade nicht (erst auf die Bake stellen) - uebersprungen.")
        return None
    dev = (rf - BATC_BEACON_NOMINAL_MHZ) * 1000.0
    new_lo = round(float(lnblo.get()) - dev / 1000.0, 6)
    root.after(0, lambda: lnblo.set(new_lo))
    _lo_offset_set("longmynd", dev)
    _lo_log("LongMynd: Bake-Abweichung %+.1f kHz -> LO %.6f MHz gesetzt." % (dev, new_lo))
    return dev

def _lo_abgleich_run(only=None):
    """alle (only=None) oder einen Tuner abgleichen; RTL-Dienst-Tuner nie (Offset kommt vom Dienst)"""
    def work():
        for t in list(_active_targets):
            if only and t != only:
                continue
            try:
                if t == "octagon":
                    _lo_abgleich_octagon()
                elif t == "longmynd":
                    _lo_abgleich_longmynd()
                elif t.startswith("onair"):
                    if only or not _onair_st[t].get("abgleich"):
                        _lo_log("%s: Abgleich gestartet (LO/Verstaerkung auf der Bake, siehe OnAir-Log)." % _TARGET_LABEL.get(t, t))
                        root.after(0, _onair_abgleich, t)
                elif t.startswith("datvrx"):
                    if _dvx_is_dienst(t):
                        _lo_log("%s: Offset kommt vom Dienst - ignoriert." % _TARGET_LABEL.get(t, t))
                    else:
                        _lo_abgleich_dvx(t)
            except Exception as ex:
                _lo_log("%s: Fehler %r" % (_TARGET_LABEL.get(t, t), ex))
        _lo_log("fertig.")
    threading.Thread(target=work, daemon=True).start()

def _lo_menu_fill():
    m = _menu_loabgl
    m.delete(0, 'end')
    m.add_command(label="Alle Tuner abgleichen (Bake als Referenz; RTL-Dienst ausgenommen)",
                  command=lambda: messagebox.askyesno("LO-Abgleich", "Alle Tuner nacheinander auf die Bake stellen und den Offset eintragen?\nDie Streams werden dabei kurz unterbrochen (Octagon bis ca. 2 min).", parent=root) and _lo_abgleich_run())
    m.add_separator()
    for t in _active_targets:
        lab = _TARGET_LABEL.get(t, t)
        if t == "octagon":
            m.add_command(label="%s: Offset abgleichen (Direktmodus)   [gespeichert %+.1f kHz]" % (lab, _lo_offset_get("octagon")),
                          command=lambda: _lo_abgleich_run("octagon"), state=("normal" if _oct_direct() else "disabled"))
        elif t == "longmynd":
            m.add_command(label="%s: LO einmal auf die Bake korrigieren   [zuletzt %+.1f kHz]" % (lab, _lo_offset_get("longmynd")), command=lambda: _lo_abgleich_run("longmynd"))
        elif t.startswith("onair"):
            m.add_command(label="%s: Abgleich (LO/Verstaerkung auf der Bake)" % lab, command=lambda tt=t: _lo_abgleich_run(tt))
        elif t.startswith("datvrx"):
            if _dvx_is_dienst(t):
                m.add_command(label="%s: Offset kommt vom Dienst (%s)" % (lab, ("%+.1f kHz" % ((_dvx_st[t].get("carrier_hz") or 0) / 1000.0 - 300.0)) if _dvx_st[t].get("lock") else "kein Lock"), state="disabled")
            else:
                m.add_command(label="%s: Offset abgleichen   [Korrektur %+.1f kHz]" % (lab, float((_DATVRX_RX.get(t) or {}).get("lo_corr_khz") or 0.0)), command=lambda tt=t: _lo_abgleich_run(tt))
    m.add_separator()
    m.add_command(label="Octagon-Offset auf 0 zuruecksetzen", command=lambda: (_lo_offset_set("octagon", 0.0), olog("LO-Abgleich: Octagon-Offset 0.")))

def _prio_dialog():
    """Menue Prio: je Tuner-Klasse einen Rang (1 = hoechste Prioritaet, gleiche Zahl = gleicher Rang) fuer die Vergabe neuer Signale und fuer das Abschalten doppelter Signale"""
    win = tk.Toplevel(root)
    win.title("Prioritaeten der Tuner")
    win.transient(root)
    frm = ttk.Frame(win, padding=10)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Rang je Tuner-Klasse: 1 = hoechste Prioritaet, gleiche Zahl = gleicher Rang. Gilt sofort.", foreground="#444").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
    ttk.Label(frm, text="Tuner-Klasse", font=("TkDefaultFont", 9, "bold")).grid(row=1, column=0, sticky="w")
    ttk.Label(frm, text="Vergabe neuer Signale\n(wer bekommt zuerst)", font=("TkDefaultFont", 9, "bold")).grid(row=1, column=1, padx=10)
    ttk.Label(frm, text="Doppelte Signale\n(niedrigster Rang wird abgeschaltet)", font=("TkDefaultFont", 9, "bold")).grid(row=1, column=2, padx=10)
    ttk.Label(frm, text="aktive Tuner", font=("TkDefaultFont", 9, "bold")).grid(row=1, column=3, sticky="w")
    vv, dv = {}, {}
    cur_v, cur_d = app_config.get("prio_vergabe") or {}, app_config.get("prio_doppelt") or {}
    for i, (c, lab) in enumerate(_PRIO_CLASSES, 2):
        ttk.Label(frm, text=lab).grid(row=i, column=0, sticky="w", pady=2)
        vv[c] = tk.IntVar(value=int(cur_v.get(c, _PRIO_DEFAULT[c])))
        dv[c] = tk.IntVar(value=int(cur_d.get(c, _PRIO_DEFAULT[c])))
        ttk.Spinbox(frm, from_=1, to=5, width=4, textvariable=vv[c]).grid(row=i, column=1)
        ttk.Spinbox(frm, from_=1, to=5, width=4, textvariable=dv[c]).grid(row=i, column=2)
        act = [_TARGET_LABEL.get(t, t) for t in _active_targets if _tuner_class(t) == c]
        ttk.Label(frm, text=", ".join(act) or "-", foreground="#666", wraplength=380).grid(row=i, column=3, sticky="w", padx=6)

    def save():
        app_config["prio_vergabe"] = {c: int(vv[c].get()) for c, _l in _PRIO_CLASSES}
        app_config["prio_doppelt"] = {c: int(dv[c].get()) for c, _l in _PRIO_CLASSES}
        app_config_save()
        olog("Prioritaeten gespeichert - Vergabe: %s | Doppelte: %s" % (
            ", ".join("%s=%d" % (l.split(" (")[0], vv[c].get()) for c, l in _PRIO_CLASSES), ", ".join("%s=%d" % (l.split(" (")[0], dv[c].get()) for c, l in _PRIO_CLASSES)))
        win.destroy()

    def standard():
        for c, _l in _PRIO_CLASSES:
            vv[c].set(_PRIO_DEFAULT[c]); dv[c].set(_PRIO_DEFAULT[c])
    bf = ttk.Frame(frm); bf.grid(row=len(_PRIO_CLASSES) + 2, column=0, columnspan=4, sticky="e", pady=(10, 0))
    ttk.Button(bf, text="Standard (Octagon/LongMynd 1, OnAir 2, RTL 3)", command=standard).pack(side="left", padx=3)
    ttk.Button(bf, text="Speichern", command=save).pack(side="left", padx=3)
    ttk.Button(bf, text="Abbrechen", command=win.destroy).pack(side="left", padx=3)

def _prof_dialog():
    """Menue Konfig -> Tuner-Konfigurationen: 10 Plaetze (1 = Standard), Laden/Speichern/Loeschen, daneben die aktiven Tuner; darunter die aktiven Tuner bearbeiten"""
    win = tk.Toplevel(root)
    win.title("Tuner-Konfigurationen")
    win.transient(root)
    frm = ttk.Frame(win, padding=10)
    frm.pack(fill="both", expand=True)
    if _cli_mode:
        note = "Gestartet mit Ziel-Parametern: der Standard (Platz 1) wird ignoriert und beim Beenden NICHT ueberschrieben. 'Aktuelle als Standard speichern' uebernimmt die jetzt aktiven Tuner."
    else:
        note = "Gestartet ohne Ziel-Parameter: Platz 1 (Standard) wird beim Beenden gespeichert und beim naechsten Start ohne Parameter benutzt."
    ttk.Label(frm, text=note, wraplength=860, foreground="#444").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 6))
    box = ttk.Frame(frm); box.grid(row=1, column=0, columnspan=6, sticky="nsew")

    def draw():
        for w in box.winfo_children():
            w.destroy()
        profiles = app_config.get("tuner_profiles") or {}
        for k in range(1, _PROFILE_SLOTS + 1):
            p = profiles.get(str(k))
            ttk.Label(box, text="%d%s" % (k, " *" if k == 1 else ""), width=4).grid(row=k, column=0, sticky="w")
            ttk.Label(box, text=(p or {}).get("name") or ("Standard" if k == 1 else "(frei)"), width=16, font=("TkDefaultFont", 9, "bold")).grid(row=k, column=1, sticky="w")
            ttk.Label(box, text=_prof_describe(p), width=78, wraplength=520, justify="left").grid(row=k, column=2, sticky="w", padx=4)
            b = ttk.Frame(box); b.grid(row=k, column=3, sticky="e")
            if k >= 2:
                ttk.Button(b, text="Laden", width=7, state=("normal" if p else "disabled"), command=lambda k=k: load(k)).pack(side="left", padx=1)
            ttk.Button(b, text="Speichern" if k >= 2 else "Aktuelle als Standard", command=lambda k=k: save(k)).pack(side="left", padx=1)
            if k >= 2:
                ttk.Button(b, text="Loeschen", width=8, state=("normal" if p else "disabled"), command=lambda k=k: delete(k)).pack(side="left", padx=1)

    def save(k):
        name = None
        if k >= 2:
            old = (app_config.get("tuner_profiles") or {}).get(str(k)) or {}
            name = simpledialog.askstring("Konfiguration speichern", "Name fuer Platz %d:" % k, initialvalue=old.get("name") or "Konfig %d" % k, parent=win)
            if name is None:
                return
        _prof_store(k, name)
        draw()

    def load(k):
        p = (app_config.get("tuner_profiles") or {}).get(str(k))
        if p and messagebox.askyesno("Konfiguration laden", "Platz %d laden (%s)?\nSie wird zum Standard (Platz 1), die GUI startet neu." % (k, _prof_describe(p)), parent=win):
            win.destroy()
            _prof_apply_and_restart(p)

    def delete(k):
        if messagebox.askyesno("Loeschen", "Platz %d loeschen?" % k, parent=win):
            (app_config.get("tuner_profiles") or {}).pop(str(k), None)
            app_config_save()
            draw()
    draw()
    # --- aktive Tuner bearbeiten ---
    ed = ttk.LabelFrame(frm, text="Aktive Tuner bearbeiten (wirkt als neuer Standard nach dem Neustart)", padding=8)
    ed.grid(row=2, column=0, columnspan=6, sticky="ew", pady=8)
    cur = _prof_current()
    bv = {k: tk.BooleanVar(value=bool(cur["targets"].get(k))) for k, _l, _f in _BASE_TARGETS}
    for i, (k, lab, _f) in enumerate(_BASE_TARGETS):
        ttk.Checkbutton(ed, text=lab, variable=bv[k]).grid(row=0, column=i, sticky="w", padx=6)
    dirv = tk.BooleanVar(value=bool(cur.get("octagon_direkt")))
    ttk.Checkbutton(ed, text="Octagon Direktmodus", variable=dirv).grid(row=1, column=1, sticky="w", padx=6)
    ov = [tk.BooleanVar(value=bool(e.get("enabled", True))) for e in cur["onair_receivers"]]
    for i, e in enumerate(cur["onair_receivers"]):
        ttk.Checkbutton(ed, text="OnAir%d (%s)" % (i + 1, _onair_dev_short(e)), variable=ov[i]).grid(row=2 + i // 4, column=i % 4, sticky="w", padx=6)
    r0 = 2 + (len(ov) + 3) // 4
    dvv = [tk.BooleanVar(value=bool(e.get("enabled", True))) for e in cur["datvrx_receivers"]]
    for i, e in enumerate(cur["datvrx_receivers"]):
        ttk.Checkbutton(ed, text="datvRX%d (%s)" % (i + 1, _dvx_dev_short(e)), variable=dvv[i]).grid(row=r0 + i // 4, column=i % 4, sticky="w", padx=6)

    def apply_edit():
        p = _prof_current()
        for k, _l, _f in _BASE_TARGETS:
            p["targets"][k] = bv[k].get()
        p["octagon_direkt"] = dirv.get()
        for i, e in enumerate(p["onair_receivers"]):
            e["enabled"] = ov[i].get()
        for i, e in enumerate(p["datvrx_receivers"]):
            e["enabled"] = dvv[i].get()
        if messagebox.askyesno("Neustart", "Als Standard speichern (%s) und GUI neu starten?" % _prof_describe(p), parent=win):
            win.destroy()
            _prof_apply_and_restart(p)
    bf = ttk.Frame(frm); bf.grid(row=3, column=0, columnspan=6, sticky="e")
    ttk.Button(bf, text="Als Standard uebernehmen + Neustart", command=apply_edit).pack(side="left", padx=3)
    ttk.Button(bf, text="Schliessen", command=win.destroy).pack(side="left", padx=3)

#----- Dialog: OnAir-Empfaenger anlegen/bearbeiten -----
def _onair_list_devices(cb):
    """onair_dvbs2_cli.py --list-devices (dauert einige Sekunden) - cb(zeilen) im Hauptthread."""
    def run():
        lines = []
        try:
            r = subprocess.run([sys.executable, "-u", _onair_script(), "--list-devices"], cwd=_onair_dir(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", timeout=120)
            for ln in r.stdout.splitlines():
                s = ln.strip()
                if s.startswith("[") and "]" in s:
                    lines.append(s.split("]", 1)[1].strip())
            if not lines:
                lines = ["(keine Geraete gefunden - Rohausgabe im Terminal)"]
                print(r.stdout, flush=True)
        except Exception as ex:
            lines = ["Fehler bei der Suche: %s" % ex]
        _onair_found_devices[:] = lines
        root.after(0, cb, lines)
    threading.Thread(target=run, daemon=True).start()

def _onair_rx_dialog(t=None):
    """t=None: neuen Empfaenger anlegen (OnAirN = naechste freie Nummer), sonst Empfaenger t bearbeiten."""
    lst = _onair_cfg_list()
    if t is None:
        # freie (geloeschte) Nummer wiederverwenden, sonst hinten anhaengen
        idx = next((i for i, x in enumerate(lst) if x.get("deleted")), len(lst))
        if idx >= _ONAIR_MAX:
            messagebox.showinfo("OpenAirRX", "Maximal %d OnAir-Empfaenger." % _ONAIR_MAX)
            return
        e = {"device": "hackrf", "enabled": True}
    else:
        idx = _onair_num(t) - 1
        while len(lst) <= idx:
            lst.append({"device": "hackrf", "enabled": False})
        e = lst[idx]
    n = idx + 1
    w = tk.Toplevel(root)
    w.title("OpenAirRX - OnAir%d" % n)
    w.transient(root)
    frm = ttk.Frame(w, padding=8)
    frm.pack(fill='both', expand=True)
    ttk.Label(frm, text="OnAir%d - Empfaenger waehlen" % n, font=('', 10, 'bold')).grid(row=0, column=0, columnspan=4, sticky='w')
    ttk.Label(frm, text="Gefundene Geraete (onair-cli):").grid(row=1, column=0, columnspan=4, sticky='w', pady=(6, 0))
    lb = tk.Listbox(frm, height=7, width=70)
    lb.grid(row=2, column=0, columnspan=4, sticky='ew')
    hint = ttk.Label(frm, text="", foreground="#c0392b", wraplength=520, justify='left')
    hint.grid(row=3, column=0, columnspan=4, sticky='w')

    v_dev = tk.StringVar(value={"hackrf": "HackRF", "rtl_tcp": "RTL-SDR (rtl_tcp lokal)", "net": "Netz-Radio",
                                "sdrplay": "SDRplay (OnAir-Fenster)", "custom": "Eigene Parameter"}.get(e.get("device", "hackrf"), "HackRF"))
    v_ant = tk.StringVar(value=e.get("sdr_ant") or "A")
    v_model = tk.StringVar(value=e.get("sdr_model") or "rsp2")
    v_rtl = tk.StringVar(value=str(e.get("rtl_index", 0)))
    v_ntype = tk.StringVar(value=e.get("net_type") or "rtltcp")
    v_nhost = tk.StringVar(value=e.get("net_host") or "127.0.0.1")
    v_nport = tk.StringVar(value=str(e.get("net_port") or 1234))
    v_lo = tk.StringVar(value=str(e.get("lo") if e.get("lo") not in (None, "") else 9540.0))
    v_gain = tk.StringVar(value=str(e.get("gain") or ""))
    v_lna = tk.StringVar(value=str(e.get("lna", 40) if e.get("lna", 40) is not None else ""))
    v_vga = tk.StringVar(value=str(e.get("vga", 12) if e.get("vga", 12) is not None else ""))
    v_amp = tk.BooleanVar(value=bool(e.get("amp", False)))
    v_autobw = tk.BooleanVar(value=bool(e.get("autobw", False)))
    v_extra = tk.StringVar(value=str(e.get("extra") or ""))
    v_udp = tk.StringVar(value=e.get("udp") or "230.0.0.%d:1234" % (9 + n))
    v_api = tk.StringVar(value=str(e.get("api_port") or (8770 + n)))
    v_en = tk.BooleanVar(value=bool(e.get("enabled", True)))

    rows = (("Geraet:", ttk.Combobox(frm, textvariable=v_dev, state='readonly', width=24,
                                      values=("HackRF", "RTL-SDR (rtl_tcp lokal)", "Netz-Radio", "SDRplay (OnAir-Fenster)", "Eigene Parameter"))),
            ("Antenne (SDRplay):", ttk.Combobox(frm, textvariable=v_ant, state='readonly', width=6, values=("A", "B", "Hi-Z", "-"))),
            ("Modell (Teilstring):", ttk.Entry(frm, textvariable=v_model, width=12)),
            ("RTL-Index (-d):", ttk.Entry(frm, textvariable=v_rtl, width=6)),
            ("Netz-Typ:", ttk.Combobox(frm, textvariable=v_ntype, values=_ONAIR_NET_TYPES, width=14)),
            ("Netz-Host:", ttk.Entry(frm, textvariable=v_nhost, width=18)),
            ("Netz-Port:", ttk.Entry(frm, textvariable=v_nport, width=8)),
            ("LNB-LO (MHz):", ttk.Entry(frm, textvariable=v_lo, width=10)),
            ("LNA dB (0-40, 8er):", ttk.Combobox(frm, textvariable=v_lna, width=6,
                                                  values=("", "0", "8", "16", "24", "32", "40"))),
            ("VGA dB (0-62, 2er):", ttk.Entry(frm, textvariable=v_vga, width=6)),
            ("Amp (+14 dB):", ttk.Checkbutton(frm, variable=v_amp)),
            ("Gain dB (leer=Std):", ttk.Entry(frm, textvariable=v_gain, width=8)),
            ("Zusatz-Parameter:", ttk.Entry(frm, textvariable=v_extra, width=40)),
            ("UDP-Ziel:", ttk.Entry(frm, textvariable=v_udp, width=20)),
            ("API-Port:", ttk.Entry(frm, textvariable=v_api, width=8)))
    # ein Feld je Zeile, damit ausgeblendete Felder keine Luecken lassen
    _field_widgets = {}
    for i, (txt, wd) in enumerate(rows):
        lab = ttk.Label(frm, text=txt)
        lab.grid(row=4 + i, column=0, sticky='e', padx=(0, 4), pady=2)
        wd.grid(row=4 + i, column=1, columnspan=3, sticky='w', pady=2)
        _field_widgets[txt] = (lab, wd)
    r0 = 4 + len(rows)
    ttk.Checkbutton(frm, text="Kanalbreite automatisch (--autobw)", variable=v_autobw).grid(row=r0, column=0, columnspan=2, sticky='w')
    ttk.Checkbutton(frm, text="aktiv (Spalte beim Start anzeigen)", variable=v_en).grid(row=r0, column=2, columnspan=2, sticky='w')

    def _update_fields(*_a):
        # nur die Felder zeigen, die fuer das gewaehlte Geraet wirklich gebraucht werden
        d = v_dev.get()
        need_rtl = (d == "RTL-SDR (rtl_tcp lokal)")
        need_net = (d == "Netz-Radio")
        hack = (d == "HackRF")
        sdr = (d == "SDRplay (OnAir-Fenster)")
        if sdr and not v_gain.get().strip():
            v_gain.set("59")
        for key, show in (("Antenne (SDRplay):", sdr), ("Modell (Teilstring):", sdr), ("API-Port:", not sdr),
                          ("Zusatz-Parameter:", not sdr),
                          ("LNA dB (0-40, 8er):", hack), ("VGA dB (0-62, 2er):", hack), ("Amp (+14 dB):", hack),
                          ("Gain dB (leer=Std):", not hack),
                          ("RTL-Index (-d):", need_rtl), ("Netz-Typ:", need_net),
                          ("Netz-Host:", need_net), ("Netz-Port:", need_net)):
            for wdg in _field_widgets[key]:
                if show:
                    wdg.grid()
                else:
                    wdg.grid_remove()
    v_dev.trace_add("write", _update_fields)
    _update_fields()

    def fill(lines):
        try:
            lb.delete(0, 'end')
            for s in lines:
                lb.insert('end', s)
        except tk.TclError:
            pass

    def search():
        fill(["Suche laeuft (onair-cli prueft alle Treiber, bis ~30 s) ..."])
        _onair_list_devices(fill)

    def on_pick(_ev=None):
        sel = lb.curselection()
        if not sel:
            return
        s = lb.get(sel[0]).lower()
        hint.configure(text="")
        if "sdrplay" in s:
            v_dev.set("SDRplay (OnAir-Fenster)")
            mm = re.search(r"\b(rsp[0-9a-z]*)", s)
            if mm:
                v_model.set(mm.group(1))
            v_ant.set("A" if "antenna a" in s else "B" if "antenna b" in s else "Hi-Z" if "hi-z" in s else "-")
            hint.configure(text="SDRplay laeuft ueber das OnAir-Fenster (API-Modus, die CLI kann ihn nicht waehlen). "
                                "Im OnAir-Fenster einmal: Remote control API einschalten und die UDP-Ausgabe auf %s stellen. "
                                "Ein evtl. laufender CLI-Prozess dieses Empfaengers wird vorher gestoppt." % v_udp.get())
        elif "hackrf" in s:
            v_dev.set("HackRF")
        elif "rtl" in s:
            v_dev.set("RTL-SDR (rtl_tcp lokal)")
            rtl_lines = [x for x in range(lb.size()) if "rtl" in lb.get(x).lower()]
            v_rtl.set(str(rtl_lines.index(sel[0]) if sel[0] in rtl_lines else 0))
            hint.configure(text="RTL-SDR: onair-cli kann den Stick nur ueber das Netz ansprechen - die GUI "
                                "startet dafuer selbst 'rtl_tcp -d <Index>' auf Port %d." % (12340 + n))
        else:
            v_dev.set("Eigene Parameter")
            hint.configure(text="Dieses Geraet (z.B. SDRplay/Pluto/B210) ist in onair-cli nicht waehlbar (die CLI kennt nur "
                                "--hackrf, --file, --synthetic, --net-radio). Weg: API-Modus ueber das OnAir-Fenster "
                                "(Knopf 'API' in der Spalte) - oder ein Netz-Radio (soapyremote/nrsp).")
    lb.bind("<<ListboxSelect>>", on_pick)

    def save(restart):
        dev = {"HackRF": "hackrf", "RTL-SDR (rtl_tcp lokal)": "rtl_tcp", "Netz-Radio": "net",
               "SDRplay (OnAir-Fenster)": "sdrplay", "Eigene Parameter": "custom"}.get(v_dev.get(), "hackrf")
        try:
            new = {"deleted": False, "device": dev, "rtl_index": int(v_rtl.get() or 0), "net_type": v_ntype.get().strip(),
                   "net_host": v_nhost.get().strip(), "net_port": int(v_nport.get() or 1234),
                   "lo": float(v_lo.get().replace(",", ".")), "gain": v_gain.get().strip(),
                   "lna": (float(v_lna.get().replace(",", ".")) if v_lna.get().strip() else None),
                   "vga": (float(v_vga.get().replace(",", ".")) if v_vga.get().strip() else None),
                   "amp": bool(v_amp.get()),
                   "autobw": bool(v_autobw.get()), "extra": v_extra.get().strip(), "udp": v_udp.get().strip(),
                   "api_port": int(v_api.get() or (8770 + n)), "enabled": bool(v_en.get()),
                   "sdr_ant": v_ant.get(), "sdr_model": v_model.get().strip().lower()}
            if dev == "sdrplay":
                ant = {"A": "antenna a", "B": "antenna b", "Hi-Z": "hi-z"}.get(v_ant.get(), "")
                new["mode"] = "api"
                new["app_device"] = ";".join(x for x in ("sdrplay", new["sdr_model"], ant) if x)
            elif e.get("device") == "sdrplay":
                new["mode"] = "cli"
                new["app_device"] = ""
        except ValueError as ex:
            messagebox.showerror("OpenAirRX", "Ungueltige Eingabe: %s" % ex, parent=w)
            return
        if idx < len(lst):
            lst[idx].update(new)
        else:
            lst.append(new)
        app_config_save()
        _oolog("OpenAirRX: OnAir%d gespeichert (%s)." % (n, _onair_dev_short(new)))
        w.destroy()
        if restart:
            _gui_restart()
        elif t is not None and t in _ONAIR_TARGETS:
            threading.Thread(target=_onair_start, args=(t,), daemon=True).start()

    def remove():
        if idx >= len(lst):
            w.destroy()
            return
        if not messagebox.askyesno("OpenAirRX", "OnAir%d deaktivieren (Spalte nach Neustart weg)?" % n, parent=w):
            return
        lst[idx]["enabled"] = False
        app_config_save()
        w.destroy()

    def delete():
        # Eintrag loeschen. Damit die Nummern (und UDP/Ports) der anderen OnAir gleich bleiben, wird ein
        # Eintrag in der Mitte nur als geloescht markiert (Nummer wird beim naechsten Hinzufuegen wieder
        # vergeben); geloeschte Eintraege am Ende der Liste werden ganz entfernt.
        if idx >= len(lst):
            w.destroy()
            return
        if not messagebox.askyesno("OpenAirRX", "OnAir%d (%s) wirklich loeschen?\n(Spalte verschwindet nach "
                                   "dem Neustart der GUI)" % (n, _onair_dev_short(lst[idx])), parent=w):
            return
        if t is not None and t in _ONAIR_TARGETS:
            threading.Thread(target=_onair_stop, args=(t,), daemon=True).start()
        lst[idx] = {"device": "hackrf", "enabled": False, "deleted": True}
        while lst and lst[-1].get("deleted"):
            lst.pop()
        app_config_save()
        _oolog("OpenAirRX: OnAir%d geloescht." % n)
        w.destroy()
        if t is not None and t in _ONAIR_TARGETS and \
                messagebox.askyesno("OpenAirRX", "GUI jetzt neu starten (Spalte entfernen)?"):
            _gui_restart()

    bf = ttk.Frame(frm)
    bf.grid(row=r0 + 1, column=0, columnspan=4, sticky='ew', pady=(8, 0))
    ttk.Button(bf, text="Geraete suchen", command=search).pack(side='left')
    ttk.Button(bf, text="Speichern + GUI neu starten", command=lambda: save(True)).pack(side='right')
    ttk.Button(bf, text="Speichern", command=lambda: save(False)).pack(side='right', padx=(0, 4))
    if t is not None:
        ttk.Button(bf, text="Loeschen", command=delete).pack(side='right', padx=(0, 4))
        ttk.Button(bf, text="Deaktivieren", command=remove).pack(side='right', padx=(0, 4))
    ttk.Button(bf, text="Abbrechen", command=w.destroy).pack(side='right', padx=(0, 4))
    if _onair_found_devices:
        fill(_onair_found_devices)
    else:
        search()

_onair_menu_vars = {}

def _onair_menu_fill():
    _menu_onair.delete(0, 'end')
    _menu_onair.add_command(label="Empfaenger hinzufuegen (Geraete suchen) ...", command=lambda: _onair_rx_dialog(None))
    _menu_onair.add_separator()
    lst = _onair_cfg_list() if app_config else _onair_list_early
    for i, e in enumerate(lst):
        if e.get("deleted"):
            continue
        t = "onair%d" % (i + 1)
        mark = "aktiv" if t in _ONAIR_TARGETS else ("aus" if not e.get("enabled", True) else "nach Neustart")
        _menu_onair.add_command(label="OnAir%d (%s) - %s - bearbeiten ..." % (i + 1, _onair_dev_short(e), mark),
                                command=lambda tt=t: _onair_rx_dialog(tt))
    for tt in _ONAIR_TARGETS:
        _v = tk.BooleanVar(value=_onair_is_app(tt))
        _onair_menu_vars[tt] = _v
        _menu_onair.add_checkbutton(label="%s: schnell umschalten ueber OnAir-Fenster (API)" % _TARGET_LABEL.get(tt, tt),
                                    variable=_v, command=lambda x=tt, v=_v: _onair_mode_toggle(x, v.get()))
    if _ONAIR_TARGETS:
        _menu_onair.add_separator()
        _menu_onair.add_command(label="Alle OnAir neu starten",
                                command=lambda: [threading.Thread(target=_onair_start, args=(tt,), daemon=True).start()
                                                 for tt in _ONAIR_TARGETS])
        _menu_onair.add_command(label="Alle OnAir stoppen",
                                command=lambda: threading.Thread(target=_onair_stop_all, daemon=True).start())
    _menu_onair.add_separator()
    for tt in _ONAIR_TARGETS:
        _menu_onair.add_command(label="%s - Werte-Fenster" % _TARGET_LABEL.get(tt, tt),
                                command=lambda x=tt: _onair_valwin_open(x))
    _vd = tk.BooleanVar(value=bool(app_config.get("onair_show_dbm", True)))
    _onair_menu_vars["__dbm__"] = _vd
    _menu_onair.add_checkbutton(label="dBm-Schaetzung in der Spalte zeigen", variable=_vd,
                                command=lambda v=_vd: (app_config.__setitem__("onair_show_dbm", bool(v.get())), app_config_save()))
    _menu_onair.add_command(label="OnAir-Log-Fenster (alle)", command=lambda: _onair_logwin_open(None))
    _menu_onair.add_command(label="GUI neu starten", command=_gui_restart)


_menubar = tk.Menu(root)
_menu_konfig = tk.Menu(_menubar, tearoff=False)
_menu_konfig.add_command(label="Spektrum-Anzeige...", command=_spectrum_settings_open)
_menu_konfig.add_command(label="Status...", command=_marquee_settings_open)
_menubar.add_cascade(label="Konfig", menu=_menu_konfig)
_menu_ladekonfig = tk.Menu(_menubar, tearoff=False, postcommand=lambda: _ladekonfig_fill())
_menubar.add_cascade(label="Ladekonfig", menu=_menu_ladekonfig)
_menu_loabgl = tk.Menu(_menubar, tearoff=False, postcommand=lambda: _lo_menu_fill())
_menubar.add_cascade(label="LO-Abgleich", menu=_menu_loabgl)
_menu_prio = tk.Menu(_menubar, tearoff=False)
_menu_prio.add_command(label="Prioritaeten: Vergabe neuer Signale / doppelte Signale abschalten ...", command=_prio_dialog)
_menubar.add_cascade(label="Prio", menu=_menu_prio)
_menu_onair = tk.Menu(_menubar, tearoff=False, postcommand=lambda: _onair_menu_fill())
_menubar.add_cascade(label="OpenAirRX", menu=_menu_onair)
_menu_dvx = tk.Menu(_menubar, tearoff=False, postcommand=lambda: _dvx_menu_fill())
_menubar.add_cascade(label="RTL-SDR-datvRX", menu=_menu_dvx)
root.config(menu=_menubar)
frm_root = ttk.Frame(root, borderwidth=8)
frm_root.pack(fill='both', expand=True)
frm_root.rowconfigure(0, weight=1)   # Zeile 0 traegt frm_batc (sticky='ns', jetzt einzige Spalte,
                                      # die RTL_SDR-Konfiguration ist ausgelagert, siehe frm_rtlsdr_cfg) -
                                      # zusaetzliche Fensterhoehe fliesst dort hinein.

#----- 'declare' user interface variables -----
bandwidth       = tk.IntVar()
const           = tk.StringVar()
debug           = tk.StringVar()
fastdrift       = tk.IntVar()
fastlock        = tk.IntVar()
fec             = tk.StringVar()
framesizes      = tk.StringVar()
frequency       = tk.DoubleVar()
gain            = tk.IntVar()
gui             = tk.IntVar()
hardmetric      = tk.IntVar()
inpipe          = tk.IntVar()
ldpc_bf         = tk.IntVar()
ldpchelper_file = tk.StringVar()
ldpchelper_path = tk.StringVar()
ldpchelper_enable = tk.BooleanVar()
freq_offset_48k = tk.BooleanVar()
leandvb_file    = tk.StringVar()
leandvb_path    = tk.StringVar()
lnblo           = tk.DoubleVar()
zf_freq         = tk.DoubleVar()   # ZF (IF) - Eingabefeld; Downlink (frequency) wird daraus berechnet: frequency = zf_freq + lnblo
maxsens         = tk.IntVar()
modcods         = tk.StringVar()
nhelpers        = tk.IntVar()
ppm             = tk.IntVar()
rolloff         = tk.DoubleVar()
rrcrej          = tk.DoubleVar()
rtldongle       = tk.IntVar()
rtlsdr_file     = tk.StringVar()
rtlsdr_path     = tk.StringVar()
sampler         = tk.StringVar()
standard        = tk.StringVar()
strongpls       = tk.IntVar()
symbolrate      = tk.IntVar()
preset          = tk.StringVar()
qo100           = tk.StringVar()
tune            = tk.IntVar()
viewer_file     = tk.StringVar()
viewer_path     = tk.StringVar()
viterbi         = tk.IntVar()

#----- allgemeine Konfigdatei (app_config.json) laden und anwenden -----
app_config_load()
tuner_config_apply()

def _player_watchdog_toggle():
    app_config["player_watchdog"] = bool(_player_watchdog_var.get())
    app_config_save()
    olog("Player-Ueberwachung %s." % ("eingeschaltet" if app_config["player_watchdog"] else "ausgeschaltet"))

_player_watchdog_var = tk.BooleanVar(value=bool(app_config.get("player_watchdog", True)))
_menu_konfig.add_command(label="Fenster-Anordnung...", command=lambda: _window_dialog())
_menu_konfig.add_command(label="Fenster speichern (SystemSave#Monitoranzahl)",
                         command=lambda: threading.Thread(target=_layout_save_manual, daemon=True).start())
_bake_start_var = tk.BooleanVar(value=bool(app_config.get("bake_beim_start", True)))
def _bake_start_toggle():
    app_config["bake_beim_start"] = bool(_bake_start_var.get())
    app_config_save()
    olog("Bake beim Start anzeigen: %s (gilt ab dem naechsten Start)" % ("JA - Tuner starten auf der Bake, Offset wird geprueft" if _bake_start_var.get() else "NEIN"))
_menu_konfig.add_checkbutton(label="Bake beim Start anzeigen (Tuner auf der Bake, Offset pruefen)", variable=_bake_start_var, command=_bake_start_toggle)
_menu_konfig.add_command(label="Fehlerbericht: in Zwischenablage / Datei / Mail ...", state=("normal" if _fb_make else "disabled"),
                         command=lambda: _fb.show_dialog_tk(root, "leandvb-GUI", _fb_make()))
_menu_konfig.add_command(label="Player pruefen: doppelte/alte Fenster melden", command=lambda: threading.Thread(target=_players_check, daemon=True).start())
_menu_konfig.add_command(label="Alle Player in den Vordergrund",
                         command=lambda: threading.Thread(target=_raise_players, daemon=True).start())
_menu_konfig.add_command(label="Langsame GUI-Aufrufe anzeigen...", command=lambda: _slow_dialog())
_menu_onairprog = tk.Menu(_menu_konfig, tearoff=False)
_onair_variant_var = tk.StringVar(value="auto")
def _onair_variant_set():
    app_config["onair_app_variant"] = _onair_variant_var.get()
    app_config_save()
    _onair_variant_note["last"] = None
    _onair_app_bin()     # meldet die Wahl im OnAir-Log
    _oolog("OnAir-Programm gilt beim naechsten Start des OnAir-Fensters (laufendes Fenster ggf. schliessen).")
for _lbl, _val in (("Automatisch (selbst gebaut, wenn vorhanden und aktuell)", "auto"),
                   ("Installiertes OnAir (Stream-Haken von Hand)", "installiert"),
                   ("Selbst gebautes OnAir (Stream per API)", "gepatcht")):
    _menu_onairprog.add_radiobutton(label=_lbl, value=_val, variable=_onair_variant_var, command=_onair_variant_set)
_menu_lm2 = tk.Menu(_menu_konfig, tearoff=False)
_lm2_modus_var = tk.StringVar(value="lokal")
_lm2_auto_var = tk.BooleanVar(value=True)
def _lm2_menu_refresh():
    t = _lm2_entry() or {}
    _lm2_modus_var.set(t.get("modus", "lokal"))
    _lm2_auto_var.set(bool(t.get("lokal_autostart", True)))
    _menu_lm2.entryconfigure(0, label="Lokal (%s:%s, Startskript %s)" % (t.get("lokal_ip", "127.0.0.1"),
                             t.get("lokal_port", 8080), t.get("lokal_script", "start_longmynd2.sh")))
    _menu_lm2.entryconfigure(1, label="Extern (%s:%s)" % (t.get("extern_ip", "192.168.0.103"), t.get("extern_port", 8081)))
def _lm2_modus_set():
    t = _lm2_entry()
    if t is None:
        return
    t["modus"] = _lm2_modus_var.get()
    app_config_save()
    ip, port = _longmynd2_endpoint()
    olog("LongMynd 2: Modus %s - Steuerung jetzt ws://%s:%d (gilt ab sofort)." % (t["modus"], ip, port))
    if t["modus"] == "lokal":
        threading.Thread(target=_lm2_local_ensure, daemon=True).start()
def _lm2_auto_set():
    t = _lm2_entry()
    if t is None:
        return
    t["lokal_autostart"] = bool(_lm2_auto_var.get())
    app_config_save()
    olog("LongMynd 2: Startskript beim Programmstart automatisch starten: %s" % ("ein" if t["lokal_autostart"] else "aus"))
_menu_lm2.add_radiobutton(label="Lokal", value="lokal", variable=_lm2_modus_var, command=_lm2_modus_set)
_menu_lm2.add_radiobutton(label="Extern", value="extern", variable=_lm2_modus_var, command=_lm2_modus_set)
_menu_lm2.add_separator()
_menu_lm2.add_checkbutton(label="Lokal: Startskript automatisch starten (wenn nicht aktiv)", variable=_lm2_auto_var,
                          command=_lm2_auto_set)
_menu_lm2.add_command(label="Lokal: jetzt pruefen / starten",
                      command=lambda: threading.Thread(target=_lm2_local_ensure, kwargs={"force": True}, daemon=True).start())
_menu_lm2.configure(postcommand=_lm2_menu_refresh)
_menu_konfig.add_cascade(label="LongMynd 2", menu=_menu_lm2)
_menu_konfig.add_cascade(label="OnAir-Programm", menu=_menu_onairprog,
                         postcommand=None)
root.after(3000, lambda: _onair_variant_var.set(app_config.get("onair_app_variant") or "auto"))
_menu_konfig.add_separator()
_menu_konfig.add_command(label="GUI beenden - Octagon-Direkttuner weiterlaufen lassen",
                          command=lambda: on_exit(keep_direct=True))

_menu_stat = tk.Menu(_menubar, tearoff=False)
_menubar.add_cascade(label="Statistik", menu=_menu_stat)
_menubar.add_command(label="Mini: AUS", command=lambda: _mini_toggle())      # Mini-Anzeige (siehe _mini_apply)
_menubar.add_command(label="Festfenster: AUS", command=lambda: _einfach_toggle())  # Einfach-Ansicht: 4 Videos im Fenster (siehe _einfach_apply)
for _i, (_title, _fn) in enumerate(_STAT_TABS):
    _menu_stat.add_command(label=_title + "...", command=lambda i=_i: _stat_dialog(i))
_menu_stat.add_separator()
_new_call_notify_var = tk.BooleanVar(value=bool(app_config.get("new_call_notify", True)))
_auto_snap_var = tk.BooleanVar(value=bool(app_config.get("auto_snapshot_new_call", True)))
def _stat_opts_save():
    app_config["new_call_notify"] = bool(_new_call_notify_var.get())
    app_config["auto_snapshot_new_call"] = bool(_auto_snap_var.get())
    app_config_save()
_menu_stat.add_checkbutton(label="Neues Rufzeichen melden (Hinweisfenster + Ton)", variable=_new_call_notify_var, command=_stat_opts_save)
_menu_stat.add_checkbutton(label="Automatischer Snapshot, wenn das Bild steht", variable=_auto_snap_var, command=_stat_opts_save)
_menu_konfig.add_checkbutton(label="Player ueberwachen (Auto-Start/Neustart)",
                              variable=_player_watchdog_var, command=_player_watchdog_toggle)
_vscale_var = tk.StringVar(value=str(app_config.get("video_max_scale", 2.0)))
def _vscale_set():
    try:
        app_config["video_max_scale"] = float(_vscale_var.get())
    except ValueError:
        return
    app_config_save()
    v = app_config["video_max_scale"]
    olog("Player-Fenster: hoechstens %s der Videoaufloesung (beim Kacheln)." %
         ("unbegrenzt" if v <= 0 else "%gx" % v))
    threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start()
_menu_vscale = tk.Menu(_menu_konfig, tearoff=0)
for _lab, _val in (("1x (Originalgroesse)", "1.0"), ("1,5x", "1.5"), ("2x", "2.0"), ("3x", "3.0"),
                   ("unbegrenzt (ganze Kachel)", "0")):
    _menu_vscale.add_radiobutton(label=_lab, value=_val, variable=_vscale_var, command=_vscale_set)
if _vscale_var.get() == "0.0":
    _vscale_var.set("0")
elif _vscale_var.get() not in ("1.0", "1.5", "2.0", "3.0", "0"):
    _vscale_var.set("2.0")
_menu_konfig.add_cascade(label="Player-Fenster: max. Vergroesserung des Videos", menu=_menu_vscale)
_vlc_res_var = tk.BooleanVar(value=bool(app_config.get("vlc_restart_on_res", False)))
def _vlc_res_toggle():
    app_config["vlc_restart_on_res"] = bool(_vlc_res_var.get())
    app_config_save()
_menu_konfig.add_checkbutton(label="VLC bei neuer Bildgroesse neu starten (Bild fensterfuellend)",
                              variable=_vlc_res_var, command=_vlc_res_toggle)
_frame_watch_var = tk.BooleanVar(value=bool(app_config.get("frame_watch", True)))
def _frame_watch_toggle():
    app_config["frame_watch"] = bool(_frame_watch_var.get())
    app_config_save()
    olog("Bild-Waechter %s." % ("ein (steht das Bild bei laufendem Stream: Stop/Play, dann Neustart)"
                                if app_config["frame_watch"] else "aus"))
_menu_konfig.add_checkbutton(label="Bild-Waechter: stehendes Bild erkennen (Stop/Play, dann Neustart)",
                              variable=_frame_watch_var, command=_frame_watch_toggle)
_tile_var = tk.BooleanVar(value=bool(app_config.get("window_tile", True)))
def _tile_toggle():
    app_config["window_tile"] = bool(_tile_var.get())
    if not app_config["window_tile"]:
        app_config["window_quad"] = False
        _quad_button_update()
    app_config_save()
    olog("Fenster kacheln %s." % ("ein - Player ohne Ueberlappung auf dem Ziel-Monitor, Hauptfenster bleibt frei"
                                   if app_config["window_tile"] else "aus - gemerkte Fensterlagen gelten wieder"))
    if app_config["window_tile"]:
        threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start()
_menu_konfig.add_checkbutton(label="Fenster kacheln: Player ohne Ueberlappung, Hauptfenster nicht verdecken",
                              variable=_tile_var, command=_tile_toggle)
_menu_konfig.add_command(label="Fenster jetzt kacheln",
                         command=lambda: threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start())
_bl_var = tk.BooleanVar(value=bool(app_config.get("vlc_borderless", False)))
def _bl_toggle():
    app_config["vlc_borderless"] = bool(_bl_var.get())
    app_config_save()
    olog("Player ohne Rand %s - %s" % (("ein", "Fensterrahmen weg; VLC-Menue/Bedienleiste erst nach Neustart des Players aus")
                                       if app_config["vlc_borderless"] else ("aus", "Rahmen wieder an")))
    threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start()
_menu_konfig.add_checkbutton(label="Player ohne Rand (kein Fensterrahmen, VLC ohne Menue/Bedienleiste)",
                              variable=_bl_var, command=_bl_toggle)
_bakefrei_var = tk.BooleanVar(value=bool(app_config.get("bake_tuner_frei", True)))
def _bakefrei_toggle():
    app_config["bake_tuner_frei"] = bool(_bakefrei_var.get())
    app_config_save()
_menu_konfig.add_checkbutton(label="Tuner auf der Bake freigeben (Player stoppen, Tuner frei fuer Signale)",
                              variable=_bakefrei_var, command=_bakefrei_toggle)
_vorschlag_var = tk.BooleanVar(value=bool(app_config.get("tuner_vorschlag", True)))
def _vorschlag_toggle():
    app_config["tuner_vorschlag"] = bool(_vorschlag_var.get())
    app_config_save()
_menu_konfig.add_checkbutton(label="Tuner vorschlagen, wenn mehr Signale als Tuner mit VerteilA da sind",
                              variable=_vorschlag_var, command=_vorschlag_toggle)
_dup_var = tk.BooleanVar(value=bool(app_config.get("dup_callsign_release", True)))
def _dup_toggle():
    app_config["dup_callsign_release"] = bool(_dup_var.get())
    app_config_save()
_menu_konfig.add_checkbutton(label="Doppeltes Rufzeichen: zweiten Tuner freigeben (Player stoppen)",
                              variable=_dup_var, command=_dup_toggle)
_solo_var = tk.BooleanVar(value=bool(app_config.get("solo_rightclick", True)))
def _solo_opt_toggle():
    app_config["solo_rightclick"] = bool(_solo_var.get())
    app_config_save()
    if not app_config["solo_rightclick"] and _solo["t"]:
        _solo_set(_solo["t"])
_menu_konfig.add_checkbutton(label="Solo-Ton: Rechtsklick auf einen Player = nur dieser mit Ton",
                              variable=_solo_var, command=_solo_opt_toggle)
_qrz_var = tk.BooleanVar(value=bool(app_config.get("qrz_enabled", False)))
def _qrz_toggle():
    app_config["qrz_enabled"] = bool(_qrz_var.get())
    app_config_save()
    olog("QRZ.com-Info im VLC %s." % ("ein" if app_config["qrz_enabled"] else "aus"))
    if app_config["qrz_enabled"] and not all(_qrz_cred_get()):
        _qrz_settings_open()
_menu_konfig.add_checkbutton(label="QRZ.com: Rufzeichen nachschlagen, Info im VLC einblenden",
                              variable=_qrz_var, command=_qrz_toggle)
_menu_konfig.add_command(label="QRZ.com-Zugang...", command=_qrz_settings_open)
_tsr_var = tk.BooleanVar(value=bool(app_config.get("ts_rate_graph", True)))
def _tsr_toggle():
    app_config["ts_rate_graph"] = bool(_tsr_var.get())
    app_config_save()
    olog("Datenstrom-Kurve %s." % ("ein (unter den Signal-Knoepfen, nur bei Empfang)" if app_config["ts_rate_graph"] else "aus"))
_menu_konfig.add_checkbutton(label="Datenstrom-Kurve (TS-Bitrate) unter den Signal-Knoepfen",
                              variable=_tsr_var, command=_tsr_toggle)
_callcheck_var = tk.BooleanVar(value=bool(app_config.get("callsign_check", True)))
def _callcheck_toggle():
    app_config["callsign_check"] = bool(_callcheck_var.get())
    app_config_save()
    olog("Rufzeichen-Pruefung %s." % (("ein (alle %d s: Signale ohne Rufzeichen scannt LongMynd/LongMynd 2)" % CALLCHECK_INTERVAL_S)
                                      if app_config["callsign_check"] else "aus"))
_menu_konfig.add_checkbutton(label="Rufzeichen-Pruefung: Signale ohne Rufzeichen mit LongMynd/LongMynd 2 scannen",
                              variable=_callcheck_var, command=_callcheck_toggle)
_beacon_mute_var = tk.BooleanVar(value=bool(app_config.get("beacon_mute", True)))
def _beacon_mute_toggle():
    app_config["beacon_mute"] = bool(_beacon_mute_var.get())
    app_config_save()
    olog("Bake stumm %s." % ("ein (Player auf der Bake ohne Ton)" if app_config["beacon_mute"] else "aus"))
_menu_konfig.add_checkbutton(label="Bake stumm: Player auf der Bake stummschalten (Wechsel: Ton an)",
                              variable=_beacon_mute_var, command=_beacon_mute_toggle)
_vlcrc_apply_snapshot_path(app_config["vlc_snapshot_path"])

# ----- SDR-Strom (Delock/Tasmota-Steckdose) -----
def _sdr_power_cmd(cmd):
    """cmd: 'Power' (Status), 'Power On', 'Power Off', 'Power Toggle'. Liefert 'ON'/'OFF'/None.
    Laeuft im Hintergrund-Thread (Netzwerk darf die GUI nicht blockieren)."""
    import urllib.request, urllib.parse
    base = (app_config.get("sdr_power_url") or "").rstrip("/")
    if not base:
        return None
    try:
        with urllib.request.urlopen("%s/cm?cmnd=%s" % (base, urllib.parse.quote(cmd)), timeout=3) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        v = str(data.get("POWER", data.get("POWER1", ""))).upper()
        return v if v in ("ON", "OFF") else None
    except Exception as e:
        dlog("SDR-Strom '%s': %s" % (cmd, e))
        return None

_sdr_power_state = [None]
def _sdr_power_label():
    st = _sdr_power_state[0]
    return "SDR: " + {"ON": "AN", "OFF": "AUS"}.get(st, "?")
def _sdr_power_show():
    if _sdr_power_idx[0] is None:
        return
    try:
        _menubar.entryconfigure(_sdr_power_idx[0], label=_sdr_power_label())
    except Exception:
        pass
def _sdr_power_run(cmd):
    def work():
        res = _sdr_power_cmd(cmd)
        _sdr_power_state[0] = res
        try:
            root.after(0, _sdr_power_show)
        except Exception:
            pass
        if res is None and cmd != "Power":
            try:
                root.after(0, lambda: set_status("SDR-Strom: keine Antwort von %s" % app_config.get("sdr_power_url")))
            except Exception:
                pass
    threading.Thread(target=work, daemon=True).start()
def _sdr_power_toggle():
    # Aus -> kurzer Rueckfrage-Schutz: nur EIN Klick schaltet um, der Zustand steht im Menuetext
    _sdr_power_run("Power Toggle")
def _sdr_power_poll():
    _sdr_power_run("Power")
    root.after(30000, _sdr_power_poll)

# Menuepunkt "SDR: AN/AUS" (Steckdose schalten): nur noch mit app_config["sdr_power_menu"] = true (Standard: aus = Menuepunkt und 30-s-Abfrage entfallen)
_sdr_power_idx = [None]
if app_config.get("sdr_power_menu", False):
    _menubar.add_command(label=_sdr_power_label(), command=_sdr_power_toggle)
    _sdr_power_idx[0] = _menubar.index("end")
    root.after(1500, _sdr_power_poll)

#----- initialize parameters dictionary -----
if os.path.isfile(parameters_file):
    parameters_load()
else:
    parameters_default()

#----- initialize presets -----
presets_load_all()

#----- ZF (IF) <-> Downlink-Frequenz: durchgehende Synchronisierung -----
# Die Combobox oben links nimmt jetzt die ZF entgegen (das, was tatsaechlich am SDR/Tuner
# ankommt); die Downlink-Frequenz (frequency - nach wie vor die im gesamten restlichen Code
# verwendete Variable, z.B. fuer on_start()/BATC/Octagon/Presets) wird daraus per + LNB-LO
# berechnet und nur noch als Text angezeigt. Umgekehrt: aendert sich frequency oder lnblo von
# irgendwoher (BATC-Klick, Preset, QO100-Auswahl, LO-Korrektur), wird die ZF-Anzeige
# nachgefuehrt. Ein Guard verhindert, dass sich beide Richtungen gegenseitig aufschaukeln.
_zf_sync_guard = False

def _sync_zf_from_frequency(*_args):
    global _zf_sync_guard
    if _zf_sync_guard:
        return
    _zf_sync_guard = True
    try:
        zf_freq.set(round(float(frequency.get()) - float(lnblo.get()), 6))
    except (ValueError, tk.TclError):
        pass
    finally:
        _zf_sync_guard = False

def _sync_frequency_from_zf(*_args):
    global _zf_sync_guard
    if _zf_sync_guard:
        return
    _zf_sync_guard = True
    try:
        frequency.set(round(float(zf_freq.get()) + float(lnblo.get()), 6))
    except (ValueError, tk.TclError):
        pass
    finally:
        _zf_sync_guard = False

_sync_zf_from_frequency()   # Initialwert direkt nach dem Laden der Parameter
frequency.trace_add("write", _sync_zf_from_frequency)
lnblo.trace_add("write", _sync_zf_from_frequency)
zf_freq.trace_add("write", _sync_frequency_from_zf)

#----- user interface action functions -----
_pipeline_proc = None

#===== leandvb --fd-info Auswertung (nur rtl_sdr/lokaler Empfang) =============================
# leandvb kann auf Wunsch periodisch (Standard 5x/s) Klartext-Statuszeilen (FREQ/SS/MER/LOCK/CNR)
# auf einen eigenen Dateideskriptor schreiben (--fd-info <fd>) - genau dieselben Werte, die sonst
# nur in seinem eigenen "timeline"-Anzeigefenster (--gui, Offset/SS/MER) zu sehen sind. Die GUI
# haengt dafuer fd 3 von leandvb per Shell-Redirect (in on_start(), "3>FIFO") an eine benannte
# Pipe (FIFO) und liest sie hier dauerhaft in einem eigenen Thread aus, um die Werte als Chip
# neben den anderen Zielen (Octagon/LongMynd haben ja auch eine MER-Anzeige) darzustellen.
_leandvb_info_fifo  = os.path.join(parameters_path, "leandvb_info.fifo")
_leandvb_info_lock  = threading.Lock()
_leandvb_info       = {}   # "offset_khz"/"ss"/"mer_db"/"cnr_db"/"lock" -> letzter Wert
_leandvb_status_chips = {}   # Label-Widgets, siehe GUI-Aufbau weiter unten (_target == "rtl_sdr")

def _leandvb_info_ensure_fifo():
    try:
        if os.path.exists(_leandvb_info_fifo) and not stat.S_ISFIFO(os.stat(_leandvb_info_fifo).st_mode):
            os.remove(_leandvb_info_fifo)
        if not os.path.exists(_leandvb_info_fifo):
            os.mkfifo(_leandvb_info_fifo)
    except OSError as e:
        print("FEHLER beim Anlegen der leandvb-Info-FIFO:", e)

def _update_leandvb_status_chips():
    chips = _leandvb_status_chips
    if not chips:
        return
    with _leandvb_info_lock:
        info = dict(_leandvb_info)
    locked    = info.get("lock")
    mer       = info.get("mer_db")
    offset    = info.get("offset_khz")
    ss        = info.get("ss")
    cnr       = info.get("cnr_db")
    locktime  = info.get("locktime_s")
    vber      = info.get("vber")
    framelock = info.get("framelock")
    bg = "#2ecc71" if locked else "#f1c40f"
    chips["mer"].config(text=("MER: %.1f dB" % mer) if mer is not None else "MER: -", **_mer_style(mer))
    chips["offset"].config(text=("Offset: %+.1f kHz" % offset) if offset is not None else "Offset: -",
                            background=bg, foreground="black")
    chips["ss"].config(text=("SS: %.1f" % ss) if ss is not None else "SS: -",
                        background=bg, foreground="black")
    if locked is None:
        chips["lock"].config(text="Lock: -", background="#f1c40f", foreground="black")
    else:
        chips["lock"].config(text=("Lock: Ja" if locked else "Lock: Nein"), background=bg, foreground="black")
    # CNR nur belastbar, wenn --cnr aktiv war (siehe opt_cnr in on_start - braucht Abtastrate >
    # 3x Symbolrate) - sonst bleibt der Wert einfach aus und der Chip zeigt "-".
    chips["cnr"].config(text=("CNR: %.1f dB" % cnr) if cnr is not None else "CNR: -",
                         background=bg, foreground="black")
    # LOCKTIME zaehlt in Sekunden seit Lock-Beginn hoch (0, solange kein Lock) - direkt so wie
    # von leandvb geliefert anzeigen, ohne eigene Umrechnung.
    chips["locktime"].config(text=("Lock seit: %ds" % locktime) if locktime is not None else "Lock seit: -",
                              background=bg, foreground="black")
    chips["vber"].config(text=("VBER: %.1e" % vber) if vber is not None else "VBER: -",
                          background=bg, foreground="black")
    if framelock is None:
        chips["framelock"].config(text="PL-Sync: -", background="#f1c40f", foreground="black")
    else:
        chips["framelock"].config(text=("PL-Sync: Ja" if framelock else "PL-Sync: Nein"),
                                   background=("#2ecc71" if framelock else "#f1c40f"), foreground="black")

_LEANDVB_INFO_LINE_RE = re.compile(r"^([A-Z]+)\s+(.+)$")

def _leandvb_info_reader():
    """Laeuft dauerhaft als ein einziger Daemon-Thread (kein eigener Start/Stop pro rtl_sdr-Lauf
    noetig): oeffnet die FIFO zum Lesen - das blockiert, bis bash/leandvb sie zum Schreiben
    oeffnet (also solange rtl_sdr nicht laeuft, einfach wartend) - liest Zeile fuer Zeile, bis
    leandvb beendet wird (EOF), setzt die Chips dann auf "-" zurueck und oeffnet fuer den
    naechsten Start erneut."""
    _leandvb_info_ensure_fifo()
    while True:
        try:
            with open(_leandvb_info_fifo, "r") as f:
                for line in f:
                    m = _LEANDVB_INFO_LINE_RE.match(line.strip())
                    if not m:
                        continue
                    key, val = m.group(1), m.group(2)
                    try:
                        with _leandvb_info_lock:
                            if key == "FREQ":
                                _leandvb_info["offset_khz"] = float(val) / 1000.0
                            elif key == "SS":
                                _leandvb_info["ss"] = float(val)
                            elif key == "MER":
                                _leandvb_info["mer_db"] = float(val)
                            elif key == "CNR":
                                _leandvb_info["cnr_db"] = float(val)
                            elif key == "LOCK":
                                _leandvb_info["lock"] = (int(val) != 0)
                            elif key == "FRAMELOCK":
                                _leandvb_info["framelock"] = (int(val) != 0)
                            elif key == "LOCKTIME":
                                _leandvb_info["locktime_s"] = int(val)
                            elif key == "VBER":
                                _leandvb_info["vber"] = float(val)
                            else:
                                continue
                    except ValueError:
                        continue
                    root.after(0, _update_leandvb_status_chips)
        except OSError as e:
            print("FEHLER beim Lesen der leandvb-Info-FIFO:", e)
            time.sleep(1.0)
        with _leandvb_info_lock:
            _leandvb_info.clear()
        root.after(0, _update_leandvb_status_chips)

threading.Thread(target=_leandvb_info_reader, daemon=True).start()

def _rtl_valid_rate(hz):
    """Gueltige RTL2832U-Abtastrate: nur 225001-300000 Hz oder 900001-3200000 Hz. Werte dazwischen (z.B. 400/550/
    800 kS/s aus QO100_BW_BY_SR fuer 250/333/500 kS) lehnt rtl_sdr ab bzw. liefern Murks - deshalb lief bisher nur die
    Bake (2,4 MS/s). Zwischenwerte werden auf 1,024 MS/s angehoben (leandvb kommt mit hoeherem Verhaeltnis gut klar)."""
    hz = int(hz)
    if hz <= 225000:
        return 250000
    if hz <= 300000:
        return hz
    if hz <= 900000:
        return 1024000
    return min(hz, 3200000)

# Feste RTL-Abtastrate je QO-100-Symbolrate (nur diese fuenf sind interessant): immer gueltig fuer den
# RTL2832U und mindestens ~2x Symbolrate fuer leandvb (1000 kS bisher nur 1,6 MS/s = zu knapp).
_RTL_RATE_BY_SR = {125: 1024000, 333: 1024000, 500: 1200000, 1000: 2400000, 1500: 2400000}

#----- rtl_sdr-Empfang wahlweise mit datvrx (HB9IIU DATVision, DVB-S/S2-Code von OnAir) statt leandvb.
#      datvrx sucht Traeger, Symbolrate, Modulation und FEC selbst. Aufruf laut docs/receiver.md:
#      rtl_sdr -f F -s 2400000 -g 40 - | datvrx - --rate 2.4 --format cu8 | ffplay -
#      Der RTL-Stick wird 300 kHz unter die Station gelegt (--carrier 300), damit die Mittenspitze nicht auf dem
#      Traeger liegt; bei 1500 kS passt das nicht in 2,4 MS/s, dort direkt auf die Station. -----
def _datvrx_bin():
    p = (app_config.get("datvrx_path") or "").strip()
    cands = [p] if p else []
    cands += [os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATVision", "datvrx"),   # mitgeliefert
              shutil.which("datvrx") or "", os.path.expanduser("~/DATVision/build/datvrx"),
              os.path.expanduser("~/sdr/DATVision/build/datvrx"), "/usr/local/bin/datvrx"]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None

def _datvrx_pipeline(sr_k, tee_stage, viewer_sub):
    """rtl_sdr-Spalte mit datvrx: cd ins DATVision-Verzeichnis und datvrx_rx.sh mit 'rtl-sdr' als Quelle."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATVision")
    if not (os.path.isfile(os.path.join(d, "datvrx_rx.sh")) and os.path.isfile(os.path.join(d, "datvrx"))):
        olog("rtl_sdr: %s/datvrx bzw. datvrx_rx.sh fehlt." % d)
        return None
    args = "rtl-sdr %s %d --lo %s --gain %s --ppm %s --index %s --gui%s" % (
        frequency.get(), int(sr_k or 0), lnblo.get(), gain.get(), ppm.get(), rtldongle.get(),
        " --rs" if app_config.get("datvrx_use_sr", False) else "")
    sub = "cd \"%s\" && ./datvrx_rx.sh %s%s | %s \n" % (d, args, tee_stage, viewer_sub)
    olog("rtl_sdr mit datvrx: cd %s && ./datvrx_rx.sh %s" % (d, args))
    return sub

def _on_start_real():
    _bw_req = int(bandwidth.get() * 1000)
    try:
        _sr_k = int(symbolrate.get())
    except (TypeError, ValueError):
        _sr_k = 0
    _rtl_rate = _RTL_RATE_BY_SR.get(_sr_k) or _rtl_valid_rate(_bw_req)
    if _rtl_rate != _bw_req:
        olog("rtl_sdr: Symbolrate %d kS -> Abtastrate %d S/s (statt %d; rtl_sdr -s und leandvb -f)." % (
            _sr_k, _rtl_rate, _bw_req))
    _fs_save = None
    if standard.get() == "DVB-S2" and framesizes.get() in ("0x01", "1") and fec.get() == "auto":
        # viele QO-100-Stationen senden SHORT frames (z.B. "QPSK 3/4 short") - nur normale Frames (0x01) lockt nie
        _fs_save = framesizes.get()
        framesizes.set("0x03")
        olog("rtl_sdr: FEC auto - Rahmengroessen normal UND short zulassen (--framesizes 0x03 statt 0x01).")
    opt_inpipe     = " --inpipe "   + str(inpipe.get())
    opt_sampler    = " --sampler "  + sampler.get()
    opt_rolloff    = " --roll-off " + str(rolloff.get())
    opt_rrcrej     = " --rrc-rej "  + str(rrcrej.get())
    opt_bandwidth  = " -f "         + str(_rtl_rate)
    opt_symbolrate = " --sr "       + str(symbolrate.get() * 1000)
    opt_tune       = " --tune "     + str(tune.get())
    opt_standard   = " --standard " + standard.get()
    opt_fastlock   = " --fastlock" if fastlock.get() == True else ""
    opt_gui        = " --gui"      if gui.get()      == True else ""
    opt_maxsens    = " --hq"       if maxsens.get()  == True else ""
    opt_const      = (" --const " + const.get())                   if standard.get() == "DVB-S" else ""
    opt_fec        = (" --cr "    + fec.get()  )                   if standard.get() == "DVB-S" and fec.get() in DVBS_FEC else ""
    if standard.get() == "DVB-S2":
        if fec.get() in S2_QPSK_MODCOD:
            modcods.set("0x%04x" % (1 << S2_QPSK_MODCOD[fec.get()]))
        elif fec.get() == "auto":
            modcods.set("")
        elif fec.get() != "manual":
            print("FEC " + fec.get() + " gibt es in DVB-S2 nicht - verwende modcods aus den Settings")
    opt_viterbi    = " --viterbi"     if viterbi.get()    == True and standard.get() == "DVB-S" else ""
    opt_hardmetric = " --hard-metric" if hardmetric.get() == True and standard.get() == "DVB-S" else ""
    opt_strongpls  = " --strongpls"   if strongpls.get()  == True and standard.get() == "DVB-S2" else ""  # Achtung: leandvb 1.2.0 bricht mit "--strongpls is broken" ab
    opt_modcods    = (" --modcods "    + modcods.get())    if modcods.get()    != "" and standard.get() == "DVB-S2" else ""
    opt_framesizes = (" --framesizes " + framesizes.get()) if framesizes.get() != "" and standard.get() == "DVB-S2" else ""
    opt_fastdrift  = " --fastdrift" if fastdrift.get() == True and standard.get() == "DVB-S2" else ""
    opt_ldpc_bf    = (" --ldpc-bf " + str(ldpc_bf.get()))       if standard.get() == "DVB-S2" else ""
    opt_nhelpers   = (" --nhelpers " + str(nhelpers.get()))     if standard.get() == "DVB-S2" else ""
    opt_ldpc_helper= (" --ldpc-helper " + "\"" + ldpchelper_path.get() + "/" + ldpchelper_file.get() + "\"") \
                      if ldpchelper_enable.get() and standard.get() == "DVB-S2" else ""
    opt_debug_v    = " -v" if debug.get() == "all" or debug.get() == "startup"   else ""
    opt_debug_d    = " -d" if debug.get() == "all" or debug.get() == "operation" else ""
    # --fd-info 3: leandvb schreibt Offset/SS/MER/Lock/CNR/... als Klartext auf fd 3 (siehe
    # _leandvb_info_reader() oben) - die FIFO dafuer wird unten per Shell-Redirect "3>" angehaengt.
    opt_fdinfo     = " --fd-info 3"
    # --cnr: eigene CNR-Messung, braucht laut leandvb Abtastrate > 3x Symbolrate - bei unserer
    # ueblichen Bandbreite (Faktor ca. 1,6-2x, siehe QO100_BW_BY_SR) normalerweise nicht erfuellt,
    # daher nur zuschalten, wenn die aktuell eingestellte Bandbreite das hergibt (sonst bleibt der
    # CNR-Chip einfach auf "-" stehen statt mit unbrauchbaren Werten).
    opt_cnr        = " --cnr" if _rtl_rate > 3 * (symbolrate.get() * 1000) else ""

    leandvb_opt = opt_inpipe + opt_sampler + opt_rolloff + opt_rrcrej \
                + opt_bandwidth + opt_symbolrate + opt_tune + opt_standard \
                + opt_fastlock + opt_gui + opt_maxsens \
                + opt_const + opt_fec + opt_viterbi + opt_hardmetric \
                + opt_strongpls + opt_modcods + opt_framesizes + opt_fastdrift \
                + opt_ldpc_bf + opt_nhelpers + opt_ldpc_helper \
                + opt_debug_v + opt_debug_d + opt_fdinfo + opt_cnr
    leandvb_sub = "\"" + leandvb_path.get() + "/" + leandvb_file.get() + "\"" + leandvb_opt

    rtlsdr_opt = ""
    rtlsdr_sub = "\"" + rtlsdr_path.get()  + rtlsdr_file.get()  + "\"" + rtlsdr_opt

    viewer_opt = " -"
    viewer_sub = "\"" + viewer_path.get() + "\"" + viewer_file.get()  + viewer_opt

    print
    print( "leandvb:", leandvb_sub)
    print
    print( "rtlsdr:", rtlsdr_sub)
    print
    print( "viewer:", viewer_sub)
    print

    # "Save Video" (rtl_sdr): tee dupliziert den TS-Strom zwischen leandvb und dem Viewer
    # zusaetzlich in eine Datei - kein eigener ffmpeg-Prozess noetig, da leandvb bereits
    # einen fertigen MPEG-TS-Strom auf stdout liefert. Zusaetzlich wird der Strom IMMER per
    # UDP an eine feste Multicast-Adresse gespiegelt (Prozess-Substitution, daher bash statt
    # sh), damit tsduck daraus - wie bei LongMynd/LongMynd P4 - das Rufzeichen lesen kann.
    tee_rec_flag = ""
    if _rec_active.get("rtl_sdr") and _rec_file.get("rtl_sdr"):
        tee_rec_flag = " -a \"" + _rec_file["rtl_sdr"] + "\""
    tee_mcast = ">(nc -u -q0 %s %s >/dev/null 2>&1)" % (cliargs.rtlsdr_stream_ip, cliargs.rtlsdr_stream_port)
    tee_stage = " | tee%s %s" % (tee_rec_flag, tee_mcast)
    # fd 3 von leandvb (siehe --fd-info oben) auf die FIFO umleiten - die Redirection steht hier
    # direkt hinter leandvb_sub/vor der Pipe, gilt also nur fuer den leandvb-Teil der Pipeline.
    _leandvb_info_ensure_fifo()
    fdinfo_redirect = " 3>\"" + _leandvb_info_fifo + "\""

    sub = rtlsdr_sub + \
          " -d " + str( rtldongle.get() ) + \
          " -f " + str( int( ( float(frequency.get()) - float(lnblo.get()) ) * 1000000 ) + (48000 if freq_offset_48k.get() else 0) ) + \
          " -g " + str( gain.get() ) +  \
          " -s " + str( _rtl_rate ) + \
          " -p " + str( ppm.get() ) + \
          " -" + \
          " | " + \
          leandvb_sub + \
          fdinfo_redirect + \
          tee_stage + \
          " | " + \
          viewer_sub + \
          " \n"

    if app_config.get("rtl_decoder", "leandvb") == "datvrx":
        dsub = _datvrx_pipeline(_sr_k, tee_stage, viewer_sub)
        if dsub is None:
            if _fs_save is not None:
                framesizes.set(_fs_save)
            return
        sub = dsub

    print()
    print("Kommandozeile:", sub)
    print()

    if _fs_save is not None:
        framesizes.set(_fs_save)        # Einstellung des Nutzers nicht dauerhaft aendern
    parameters_save()

    file = open(run_script, "w")
    file.write("#!/bin/bash \n\n")
    file.write("\n\n")
    file.write(sub)
    file.close()

    global _pipeline_proc
    # Erst eine evtl. noch laufende alte Pipeline sauber beenden - sonst haelt z.B. ein
    # uebrig gebliebener rtl_sdr-Prozess das USB-Geraet noch fest ("usb_claim_interface
    # error -6", "Failed to open rtlsdr device") und der neue rtl_sdr kann es nicht oeffnen.
    on_stop()
    time.sleep(0.3)
    try:
        # stdout/stderr werden NICHT mehr von Python mitgelesen - laeuft wie gewohnt frei
        # ins Terminal durch (das Mitlesen fuer die RX-Statuszeile hat die Ausgabe ausgebremst
        # und teils haengen lassen, und die gewuenschten Werte gab es dort ohnehin nicht als Text).
        _pipeline_proc = subprocess.Popen(["bash", run_script], start_new_session=True)
    except OSError as e:
        print("FEHLER beim Start der Pipeline:", e)

def on_stop():
    # eigene rtl_sdr-Pipeline gezielt (Prozessgruppe) beenden - "killall rtl_sdr/datvrx" wuerde auch die
    # Quellen der datvRX-Spalten abschiessen
    p = globals().get("_pipeline_proc")
    if p is not None and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
    if _DATVRX_TARGETS:
        return
    file = open(stop_script, "w")
    file.write("#!/bin/sh \n")
    file.write("\n")
    file.write("killall rtl_sdr\n")
    file.write("killall ffplay\n")
    file.write("killall leandvb\n")
    file.write("killall datvrx\n")
    file.write("killall basicRX\n")
    file.write("killall tee\n")
    file.write("\n")
    file.write("exit 0\n")
    file.close()
    os.system("sh " + stop_script)

def on_exit(keep_direct=False):
    """keep_direct=True: dvb_direct.py laeuft auf der Box WEITER (Enigma bleibt aus, der Stream
    laeuft) - fuer "GUI beenden, QO-Tuner laeuft weiter"."""
    if not keep_direct and _oct_direct() and _oct_direct_running[0]:
        threading.Thread(target=_oct_direct_stop, daemon=True).start()
        time.sleep(1.0)
    try:
        _layout_autosave_tick(force=True)     # Fensterlagen der Player vor dem Beenden merken
    except Exception as e:
        dlog("Fensterlagen beim Beenden: %r" % (e,))
    try:
        _players_state_save(sync=True)        # Tuner-Frequenzen und offene Player merken (Start-Pruefung beim naechsten Start)
    except Exception as e:
        dlog("Player-Zustand beim Beenden: %r" % (e,))
    try:
        _stick_spec_stop()                    # Stick-Spektrum-Server beenden, Stick freigeben
    except Exception:
        pass
    if not keep_direct:
        _dvs_verteiler_restore()          # Pi-Verteiler wieder an, falls die GUI ihn abgeschaltet hatte (nicht beim GUI-Neustart)
    _prof_save_default()                  # Tuner-Konfiguration (Platz 1 = Standard) fuer den naechsten Start ohne Parameter
    try:                                  # BATC 'Verbinden' aktiv? Dann beim naechsten Start automatisch verbinden (sonst nicht)
        _bw = bool(_batc_started)
        if bool(app_config.get("batc_auto_verbinden")) != _bw:
            app_config["batc_auto_verbinden"] = _bw
            app_config_save()
    except Exception as e:
        dlog("BATC-Auto-Verbinden speichern: %r" % (e,))
    parameters_save()
    on_stop()
    stop_vlc()
    try:
        _onair_stop_all()
    except Exception:
        pass
    try:
        _dvx_stop_all()
    except Exception:
        pass
    if "longmynd_p4" in _active_targets:
        _longmyndp4_send_start()
    _kill_all_spawned_terminals()
    root.destroy()

#W = tk.StringVar(value="Standard-Pfad")

#----- user interface -----
# "Frequency"-Feld ist jetzt die ZF (IF) - das, was tatsaechlich am SDR/Tuner ankommt. Die
# Downlink-Frequenz (weiterhin ueberall sonst im Code "frequency") wird daraus berechnet und
# nur als Text rechts daneben angezeigt (siehe ZF<->Downlink-Sync oben).
_DOWNLINK_PRESETS_MHZ = ("10491.500","1252","1257","1260","436","437","1255","1252.600","1280","1250","1253")

#----- RTL_SDR/leandvb-Konfigurationsfenster: eigenes, zunaechst verstecktes Toplevel-Fenster -----
# (bisher der komplette linke Bereich direkt in frm_root) - per "QRG Eingabe"-Knopf bei der
# rtl_sdr-Spalte ein-/ausblendbar (siehe _toggle_rtlsdr_cfg_window unten); "schliessen" (X)
# blendet nur aus (withdraw), damit die eingegebenen Werte/Variablen erhalten bleiben.
frm_rtlsdr_cfg = tk.Toplevel(root)
frm_rtlsdr_cfg.title("RTL-SDR / leandvb Konfiguration")
frm_rtlsdr_cfg.protocol("WM_DELETE_WINDOW", frm_rtlsdr_cfg.withdraw)
frm_rtlsdr_cfg.withdraw()

#===== Neuer QRG-Dialog fuer LongMynd / LongMynd 2 / LongMynd P4 / Octagon (rtl_sdr behaelt das =====
#===== RTL-SDR/leandvb-Konfigfenster): QO-100-Kanal-Pulldown mit Uplink-Spalte, Direktempfang   =====
#===== (437/1291/2355/2380 MHz ohne LNB), 10 gemeinsame Speicher, Anzeige von ZF und Uplink.     =====
_qrg_windows = {}      # target -> Toplevel
_qrg_closers = {}      # target -> Funktion zum Schliessen
_qrg_refreshers = {}   # target -> Funktion, die die Speicherknoepfe neu beschriftet
_QRG_SR_CHOICES = ("125", "250", "333", "500", "1000", "1500", "2000")
_QRG_C = {"head": "#17324d", "head2": "#9fb8d0", "bg": "#eef2f6", "card": "#ffffff",
          "text": "#1f2933", "muted": "#6b7a89", "accent": "#2d7dd2", "go": "#2e9e5b",
          "go_h": "#37b46a", "preset": "#2d7dd2", "preset_h": "#4a93e0", "mem": "#dde6ef",
          "mem_h": "#cbd8e6", "memfull": "#fff1c2", "memfull_h": "#ffe699", "off": "#c9d1d9",
          "uplink": "#c2570c", "store_on": "#d9480f"}

def _qrg_memory_get():
    mem = app_config.get("qrg_memory")
    if not isinstance(mem, list):
        mem = []
    return (mem + [None] * 10)[:10]

def _qrg_parse(freq_txt, sr_txt):
    """(Frequenz MHz, Symbolrate kS/s oder None) aus den Eingabefeldern, None bei ungueltiger Frequenz."""
    try:
        f = float(str(freq_txt).replace(",", "."))
    except ValueError:
        return None
    try:
        sr = int(float(str(sr_txt).replace(",", ".")))
    except ValueError:
        sr = None
    return f, sr

def _qrg_zf(target, f):
    """Tuner-ZF (MHz) fuer Frequenz f: Direktempfang = f, sonst f - LO (P4: LO des Pi-Skripts)."""
    if _is_direct_freq(f):
        return f
    lo = float(cliargs.longmyndp4_script_lo) if target == "longmynd_p4" else float(lnblo.get())
    return f - lo

def _qrg_toggle(target):
    closer = _qrg_closers.get(target)
    if closer is not None:
        closer()
    else:
        _qrg_dialog(target)

def _qrg_card(body, C, small, title):
    """eine Karte im QRG-Dialog (aus _qrg_dialog ausgelagert); liefert den inneren Frame"""
    outer = tk.Frame(body, bg=C["card"], highlightbackground=C["off"], highlightthickness=1)
    outer.pack(fill="x", pady=(0, 10))
    tk.Label(outer, text=title, bg=C["card"], fg=C["muted"], font=small, anchor="w"
             ).pack(fill="x", padx=12, pady=(8, 2))
    inner = tk.Frame(outer, bg=C["card"]); inner.pack(fill="x", padx=12, pady=(0, 10))
    return inner

def _qrg_window(target):
    """QRG-Dialog: Fenster, Farben, Schriften (aus _qrg_dialog ausgelagert); liefert (C, label, is_oct, win, mono, big, mid, small)"""
    import tkinter.font as tkfont
    C = _QRG_C
    label = _TARGET_LABEL.get(target, target)
    is_oct = (target == "octagon")
    win = tk.Toplevel(root)
    win.title("QRG Eingabe - %s" % label)
    win.configure(bg=C["bg"])
    win.resizable(False, False)
    mono = tkfont.nametofont("TkFixedFont").copy(); mono.configure(size=10)
    big = tkfont.nametofont("TkDefaultFont").copy(); big.configure(size=18, weight="bold")
    mid = tkfont.nametofont("TkDefaultFont").copy(); mid.configure(size=11, weight="bold")
    small = tkfont.nametofont("TkDefaultFont").copy(); small.configure(size=9)
    win._qrg_fonts = (mono, big, mid, small)   # Referenz halten, sonst werden die Fonts verworfen
    win.option_add("*qrgchannel*Listbox.font", mono)
    return C, label, is_oct, win, mono, big, mid, small

def _qrg_header(win, label, C, big, mid, small):
    """QRG-Dialog: Kopfzeile und Koerper (aus _qrg_dialog ausgelagert); liefert (cur_var, body)"""

    # ---- Kopf ----
    head = tk.Frame(win, bg=C["head"]); head.pack(fill="x")
    tk.Label(head, text="QRG Eingabe", bg=C["head"], fg="white", font=big).pack(side="left", padx=(16, 10), pady=10)
    tk.Label(head, text=label, bg=C["head"], fg=C["head2"], font=mid).pack(side="left", pady=10)
    cur_var = tk.StringVar(value="Aktuell: -")
    tk.Label(head, textvariable=cur_var, bg=C["head"], fg=C["head2"], font=small).pack(side="right", padx=16)

    body = tk.Frame(win, bg=C["bg"]); body.pack(fill="both", padx=14, pady=12)
    return cur_var, body

def _qrg_rfport_row(target, win, c2, C, mid, small):
    """QRG-Dialog: RF-Port-Zeile (nur LongMynd/LongMynd 2) (aus _qrg_dialog ausgelagert)"""

    # RF-Eingang des MiniTiouners (nur LongMynd / LongMynd 2): Anzeige aus longmynd gelesen, Knopf schaltet
    # nur auf Klick um (T0/T1) - nichts gespeichert, beim Start wird nichts geschickt (longmynd startet mit Port 1)
    if target in ("longmynd", "longmynd2"):
        rf_row = tk.Frame(c2, bg=C["card"]); rf_row.pack(fill="x", pady=(8, 0))
        tk.Label(rf_row, text="RF-Port:", bg=C["card"], fg=C["text"], font=mid).pack(side="left")
        rf_state = {"p": None}
        rf_var = tk.StringVar(value="wird gelesen ...")

        def rf_show(p):
            rf_state["p"] = p
            try:
                rf_var.set("unbekannt (longmynd nicht erreichbar)" if p is None else
                           "RF-Eingang oben (T0)" if p == 0 else "RF-Eingang unten (T1)")
                rf_btn.configure(text=("Port ?" if p is None else "Port %d" % (p + 1)),
                                 bg=("#27ae60" if p == 1 else "#2d7dd2"),
                                 activebackground=("#1e8449" if p == 1 else "#2364a8"))
            except tk.TclError:
                pass

        def rf_click():
            new = 1 if rf_state["p"] in (None, 0) else 0
            def work():
                if _lm_rfport_send(target, new):
                    time.sleep(1.0)
                    p = _lm_rfport_read(target)
                    root.after(0, rf_show, new if p is None else p)
                    root.after(0, _vlc_resync, target, "RF-Eingang gewechselt")
            threading.Thread(target=work, daemon=True).start()

        rf_btn = tk.Button(rf_row, text="Port ?", font=mid, fg="white", bg="#2d7dd2", relief="flat",
                           activeforeground="white", padx=16, pady=2, cursor="hand2", command=rf_click)
        rf_btn.pack(side="left", padx=(8, 8))
        tk.Label(rf_row, textvariable=rf_var, bg=C["card"], fg=C["muted"], font=small).pack(side="left")

        def rf_poll():
            def work():
                p = _lm_rfport_read(target)
                try:
                    root.after(0, rf_show, p)
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()
            try:
                win.after(5000, rf_poll)
            except tk.TclError:
                pass
        win.after(300, rf_poll)

def _qrg_presets(c3, C, mid, is_oct, fill):
    """QRG-Dialog: Direktempfang-Voreinstellungen (aus _qrg_dialog ausgelagert)"""

    prow = tk.Frame(c3, bg=C["card"]); prow.pack(fill="x")
    for i, (f, band) in enumerate(QRG_DIRECT_PRESETS):
        b = tk.Button(prow, text="%g MHz\n%s" % (f, band), width=9, font=mid, bg=C["preset"],
                      fg="white", activebackground=C["preset_h"], activeforeground="white",
                      disabledforeground="#eef2f6", relief="flat", pady=6, cursor="hand2",
                      command=lambda f=f: fill(f, None, True))
        if (is_oct and not (950.0 <= f <= 2150.0)) or ((not is_oct) and not (_LM_DIRECT_RANGE_MHZ[0] <= f <= _LM_DIRECT_RANGE_MHZ[1])):
            b.configure(state="disabled", bg=C["off"], cursor="arrow")
        b.pack(side="left", padx=(0 if i == 0 else 8, 0), expand=True, fill="x")

def _qrg_display_card(c4, C, small, big, dl_var, zf_var, ul_var):
    """QRG-Dialog: Anzeige-Karte (aus _qrg_dialog ausgelagert)"""
    arow = tk.Frame(c4, bg=C["card"]); arow.pack(fill="x")
    for i, (cap, var, col) in enumerate((("Downlink (RF)", dl_var, C["accent"]),
                                          ("ZF am Tuner", zf_var, C["text"]),
                                          ("Uplink (2400er)", ul_var, C["uplink"]))):
        blk = tk.Frame(arow, bg=C["card"]); blk.pack(side="left", expand=True, fill="x")
        tk.Label(blk, text=cap, bg=C["card"], fg=C["muted"], font=small, anchor="w").pack(fill="x")
        tk.Label(blk, textvariable=var, bg=C["card"], fg=col, font=big, anchor="w").pack(fill="x")

def _qrg_memory_card(target, c5, C, mid, small, freq_var, sr_var, store_var, is_oct, fill):
    """QRG-Dialog: Speicher-Karte mit 10 Speicherplaetzen (aus _qrg_dialog ausgelagert)"""
    mrow = tk.Frame(c5, bg=C["card"]); mrow.pack(fill="x")
    mem_btns = []

    def mem_refresh():
        mem = _qrg_memory_get()
        for i, b in enumerate(mem_btns):
            m = mem[i]
            if isinstance(m, dict) and m.get("freq"):
                b.configure(text="%d\n%.3f\n%s kS" % (i + 1, float(m["freq"]), m.get("sr", "")),
                            bg=C["memfull"], activebackground=C["memfull_h"])
            else:
                b.configure(text="%d\n-\nleer" % (i + 1), bg=C["mem"], activebackground=C["mem_h"])

    def mem_store(i):
        p = _qrg_parse(freq_var.get(), sr_var.get())
        if p is None or p[0] <= 0 or not p[1] or p[1] <= 0:
            olog("QRG-Speicher: keine gueltige Frequenz/Symbolrate zum Speichern.")
            return
        mem = _qrg_memory_get()
        mem[i] = {"freq": round(p[0], 3), "sr": int(p[1])}
        app_config["qrg_memory"] = mem
        app_config_save()
        olog("QRG-Speicher %d: %.3f MHz / %d kS/s gespeichert." % (i + 1, p[0], p[1]))
        store_var.set(False)
        for fn in list(_qrg_refreshers.values()):
            fn()

    def mem_click(i):
        m = _qrg_memory_get()[i]
        full = isinstance(m, dict) and m.get("freq")
        if store_var.get() or not full:
            mem_store(i)
        elif is_oct and _is_direct_freq(float(m["freq"])) and not (950.0 <= float(m["freq"]) <= 2150.0):
            olog("QRG-Speicher %d: Octagon-Tuner kann nur 950-2150 MHz." % (i + 1))
        else:
            fill(float(m["freq"]), int(m.get("sr") or 0) or None, send=True)

    for i in range(10):
        b = tk.Button(mrow, text="", width=10, height=3, font=small, fg=C["text"],
                      relief="flat", cursor="hand2", command=lambda i=i: mem_click(i))
        b.bind("<Button-3>", lambda e, i=i: mem_store(i))
        b.grid(row=i // 5, column=i % 5, padx=3, pady=3, sticky="nsew")
        mem_btns.append(b)
    for c in range(5):
        mrow.columnconfigure(c, weight=1)
    mem_refresh()
    _qrg_refreshers[target] = mem_refresh
    def _store_look(*_a):
        if store_var.get():
            store_btn.configure(text="\u25cf  SPEICHERN AKTIV - jetzt einen Speicher anklicken",
                                bg=C["store_on"], activebackground=C["store_on"], fg="white")
        else:
            store_btn.configure(text="Speichern  (aktuelle QRG im naechsten Klick ablegen)",
                                bg=C["mem"], activebackground=C["mem_h"], fg=C["text"])
    def _store_toggle():
        store_var.set(not store_var.get()); _store_look()
    store_btn = tk.Button(c5, font=mid, relief="flat", pady=6, cursor="hand2", command=_store_toggle)
    store_btn.pack(fill="x", pady=(8, 0))
    store_var.trace_add("write", _store_look)
    _store_look()

def _qrg_footer(target, win, C, small):
    """QRG-Dialog: Fusszeile, Schliessen und Registrierung des Fensters (aus _qrg_dialog ausgelagert)"""

    # ---- Fuss ----
    foot = tk.Frame(win, bg=C["bg"]); foot.pack(fill="x", padx=14, pady=(0, 12))

    def close():
        _qrg_windows.pop(target, None)
        _qrg_closers.pop(target, None)
        _qrg_refreshers.pop(target, None)
        try:
            win.destroy()
        except tk.TclError:
            pass
    tk.Button(foot, text="Schliessen", font=small, bg=C["mem"], fg=C["text"], activebackground=C["mem_h"],
              relief="flat", padx=16, pady=4, cursor="hand2", command=close).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", close)
    win.bind("<Escape>", lambda e: close())
    _qrg_windows[target] = win
    _qrg_closers[target] = close

def _qrg_active_channels(target, win, act_row, C, small, fill, cur_var):
    """QRG-Dialog: Karte der aktiven BATC-Kanaele und 2-s-Takt; liefert tick() (aus _qrg_dialog ausgelagert)"""
    act_state = {"sig": None}


    def act_refresh():
        with _batc_lock:
            items = sorted((st["freq"], st["sr"]) for st in _batc_sig_state.values()
                           if st.get("gone_since") is None)
        rows = []
        for f, sr in items:
            call = _spectrum_callsign_for_signal({"freq": f}, None)
            rows.append((f, sr, call))
        sig = tuple((round(f, 3), sr, c) for f, sr, c in rows)
        if sig == act_state["sig"]:
            return
        act_state["sig"] = sig
        for w in act_row.winfo_children():
            w.destroy()
        if not rows:
            tk.Label(act_row, text="(keine aktiven Signale erkannt)", bg=C["card"], fg=C["muted"],
                     font=small).pack(anchor="w")
            return
        for i, (f, sr, call) in enumerate(rows):
            txt = "%.3f\n%s  %d kS\nUL %.3f" % (f, call if call else "?", sr, f - QO100_UPLINK_OFFSET_MHZ)
            b = tk.Button(act_row, text=txt, font=small, width=14, relief="flat", pady=3, cursor="hand2",
                          bg=(C["memfull"] if call else C["mem"]), activebackground=C["memfull_h"] if call else C["mem_h"],
                          fg=C["text"], command=lambda f=f, sr=sr: fill(f, sr, True))
            b.grid(row=i // 4, column=i % 4, padx=3, pady=3, sticky="nsew")
        for c in range(4):
            act_row.columnconfigure(c, weight=1)

    def tick():
        if _qrg_windows.get(target) is not win:
            return
        try:
            act_refresh()
        except Exception as e:
            dlog("QRG aktive Kanaele: %r" % (e,))
        f = _target_cur_freq.get(target); sr = _target_cur_sr.get(target)
        cur_var.set(("Aktuell: %.3f MHz / %s kS/s" % (f, sr)) if f else "Aktuell: -")
        win.after(2000, tick)
    return tick

def _qrg_dialog(target):
    C, label, is_oct, win, mono, big, mid, small = _qrg_window(target)

    # ---- Kopf ----
    cur_var, body = _qrg_header(win, label, C, big, mid, small)

    def card(title):
        return _qrg_card(body, C, small, title)

    freq_var = tk.StringVar(); sr_var = tk.StringVar(value="1500")
    ch_var = tk.StringVar()
    ul_line_var = tk.StringVar(value="Uplink: -")
    dl_var = tk.StringVar(value="-"); zf_var = tk.StringVar(value="-"); ul_var = tk.StringVar(value="-")
    auto_var = tk.BooleanVar(value=bool(app_config.get("qrg_auto_apply", True)))
    store_var = tk.BooleanVar(value=False)

    # ---- Karte 1: QO-100-Kanal (Downlink | Belegung | Uplink) ----
    c1 = card("QO-100 KANAL")
    ch_labels = ["%10.3f MHz   %-32s   %9.3f MHz" % (f, l, f - QO100_UPLINK_OFFSET_MHZ)
                 for (f, l, sr) in QO100_CHANNELS]
    ch_info = dict(zip(ch_labels, [(f, sr) for (f, l, sr) in QO100_CHANNELS]))
    tk.Label(c1, text="%-14s   %-32s   %s" % ("Downlink", "Belegung / Symbolraten", "Uplink (2,4 GHz)"),
             bg=C["card"], fg=C["muted"], font=mono, anchor="w").pack(fill="x", padx=(5, 0))
    cmb = ttk.Combobox(c1, textvariable=ch_var, values=ch_labels, state="readonly",
                       width=len(ch_labels[0]) + 3, font=mono, height=min(len(ch_labels), 20),
                       name="qrgchannel")
    cmb.pack(fill="x", pady=(2, 0))

    # ---- Karte: aktive Kanaele vom BATC-Server (Spektrum), Rufzeichen wenn bekannt ----
    c_act = card("AKTIVE KANAELE (BATC-Server)  -  Klick: Kanal setzen")
    act_row = tk.Frame(c_act, bg=C["card"]); act_row.pack(fill="x")

    # ---- Karte 2: Frequenz, Symbolrate, Setzen ----
    c2 = card("FREQUENZ & SYMBOLRATE")
    ul_big = tk.Label(c2, textvariable=ul_line_var, bg=C["card"], fg=C["uplink"], font=big, anchor="w")
    ul_big.pack(fill="x", pady=(0, 6))
    row = tk.Frame(c2, bg=C["card"]); row.pack(fill="x")
    ent = tk.Entry(row, textvariable=freq_var, width=10, font=big, justify="right", relief="flat",
                   bg="#f1f5f9", highlightthickness=1, highlightbackground=C["off"],
                   highlightcolor=C["accent"])
    ent.pack(side="left")
    tk.Label(row, text="MHz", bg=C["card"], fg=C["text"], font=mid).pack(side="left", padx=(6, 22))
    ttk.Combobox(row, textvariable=sr_var, values=_QRG_SR_CHOICES, width=6, font=mid
                 ).pack(side="left")
    tk.Label(row, text="kS/s", bg=C["card"], fg=C["text"], font=mid).pack(side="left", padx=(6, 0))

    def do_send(event=None):
        p = _qrg_parse(freq_var.get(), sr_var.get())
        if p is None or p[0] <= 0 or not p[1] or p[1] <= 0:
            olog("QRG-Dialog (%s): ungueltige Frequenz/Symbolrate." % label)
            return
        f, sr = p
        if is_oct and _is_direct_freq(f) and not (950.0 <= f <= 2150.0):
            olog("QRG-Dialog (Octagon): Tuner kann nur 950-2150 MHz - %.3f MHz nicht moeglich." % f)
            return
        if (not is_oct) and _is_direct_freq(f) and not (_LM_DIRECT_RANGE_MHZ[0] <= f <= _LM_DIRECT_RANGE_MHZ[1]):
            olog("QRG-Dialog (%s): Direktempfang nur %g-%g MHz - %.3f MHz nicht moeglich." %
                 (label, _LM_DIRECT_RANGE_MHZ[0], _LM_DIRECT_RANGE_MHZ[1], f))
            return
        olog("QRG-Dialog: %s -> %.3f MHz / %d kS/s" % (label, f, sr))
        target_dispatch(target, f, sr, None)

    go = tk.Button(row, text="Setzen", font=mid, bg=C["go"], fg="white", activebackground=C["go_h"],
                   activeforeground="white", relief="flat", padx=28, pady=6, cursor="hand2",
                   command=do_send)
    go.pack(side="right")

    def save_auto():
        app_config["qrg_auto_apply"] = bool(auto_var.get())
        app_config_save()
    tk.Checkbutton(c2, text="Sofort setzen bei Auswahl (Kanal / Direktfrequenz / Speicher)",
                   variable=auto_var, command=save_auto, bg=C["card"], activebackground=C["card"],
                   fg=C["text"], font=small, anchor="w", selectcolor="#ffffff"
                   ).pack(fill="x", pady=(8, 0))

    _qrg_rfport_row(target, win, c2, C, mid, small)

    # ---- Karte 3: Direktempfang ----
    c3 = card("DIREKTEMPFANG (ohne LNB)" + ("  -  Octagon: nur 950-2150 MHz, Programm in der Kanalliste noetig" if is_oct
          else "  -  %g bis %g MHz, z.B. 144.500" % _LM_DIRECT_RANGE_MHZ))

    def refresh_info(*_a):
        p = _qrg_parse(freq_var.get(), sr_var.get())
        if p is None or p[0] <= 0:
            dl_var.set("-"); zf_var.set("-"); ul_var.set("-")
            ul_line_var.set("Uplink: -")
            ch_var.set("")
            return
        f = p[0]
        dl_var.set("%.3f MHz" % f)
        zf_var.set("-" if is_oct else "%.3f MHz" % _qrg_zf(target, f))
        ul_var.set("Direktempfang" if _is_direct_freq(f) else "%.3f MHz" % (f - QO100_UPLINK_OFFSET_MHZ))
        ul_line_var.set("Uplink: Direktempfang (kein QO-100)" if _is_direct_freq(f)
                        else "Uplink: %.3f MHz" % (f - QO100_UPLINK_OFFSET_MHZ))
        match = next((lb for lb, (cf, _s) in ch_info.items() if abs(cf - f) < 0.0005), "")
        if ch_var.get() != match:
            ch_var.set(match)

    def fill(f, sr=None, send=False):
        freq_var.set("%.3f" % f)
        if sr:
            sr_var.set(str(sr))
        refresh_info()
        if send and auto_var.get():
            do_send()

    def on_channel(event=None):
        info = ch_info.get(ch_var.get())
        if info:
            fill(info[0], info[1], send=True)

    cmb.bind("<<ComboboxSelected>>", on_channel)
    freq_var.trace_add("write", refresh_info)
    ent.bind("<Return>", do_send)

    _qrg_presets(c3, C, mid, is_oct, fill)

    # ---- Karte 4: Anzeige ----
    c4 = card("ANZEIGE")
    _qrg_display_card(c4, C, small, big, dl_var, zf_var, ul_var)

    # ---- Karte 5: Speicher ----
    c5 = card("SPEICHER (gemeinsam fuer alle Tuner)  -  Klick: abrufen  |  Rechtsklick oder 'Speichern': aktuelle QRG ablegen")
    _qrg_memory_card(target, c5, C, mid, small, freq_var, sr_var, store_var, is_oct, fill)

    _qrg_footer(target, win, C, small)

    tick = _qrg_active_channels(target, win, act_row, C, small, fill, cur_var)

    f0 = _target_cur_freq.get(target) or 10491.5
    s0 = _target_cur_sr.get(target) or 1500
    fill(f0, s0, send=False)
    tick()
    win.update_idletasks()
    win.minsize(max(win.winfo_reqwidth(), 760), win.winfo_reqheight())
    win.geometry("+%d+%d" % (root.winfo_rootx() + 60, root.winfo_rooty() + 40))
    ent.focus_set()

#===== Player-Ueberwachung: sind die gewollten Player gestartet? (ps -ax, Telnet-Port, UDP-Socket =====
#===== des Streams per 'ss -uln') - wenn nicht, werden sie neu gestartet. Umschaltbar ueber        =====
#===== Konfig -> "Player ueberwachen" (app_config["player_watchdog"]).                              =====
def _ps_lines():
    """Ausgabe von 'ps -ax -o pid=,args=' als Zeilenliste (leer bei Fehler)."""
    try:
        out = subprocess.run(["ps", "-ax", "-o", "pid=,args="], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=4).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]

def _udp_listening(ip, port):
    """True/False, ob ein UDP-Socket auf ip:port gebunden ist ('ss -uln'); None wenn nicht ermittelbar."""
    ss = shutil.which("ss")
    if not ss:
        return None
    try:
        out = subprocess.run([ss, "-uln"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=4).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    needle = "%s:%d" % (ip, port)
    return any(needle in line for line in out.splitlines())

def _player_restart(target, player, reason):
    """Hauptthread: Player dieses Ziels (neu) starten - ein noch laufender/haengender wird vorher beendet."""
    _player_last_restart[target] = time.time()
    label = _TARGET_LABEL.get(target, target)
    olog("%s: Player-Ueberwachung - %s: starte %s." % (label, reason, player))
    proc = _player_proc.get(target)
    start = lambda: root.after(300, lambda: _start_player(target, player))
    if proc is not None and proc.poll() is None:
        _stop_player(target, on_done=start)       # Neustart erst, wenn der alte Prozess wirklich weg ist
        return
    if player == "vlc":
        port = _player_telnet_port(target)
        if port is not None and _is_port_open("127.0.0.1", port, timeout=0.4):
            _stop_player(target, on_done=start)
            return
    root.after(300, lambda: _start_player(target, player))

#----- Player pruefen / alle schliessen und neu starten (Knopf "PlayerNeu" neben Quad) -----
def _players_scan():
    """Alle laufenden Player (vlc/mpv/ffplay) mit Stream-Adresse: [{pid, kind, url, target, age}]. target = Ziel, dessen Strom oder VLC-Telnet-Port in der
    Kommandozeile steht (sonst None = alte Sitzung/fremder Player)."""
    ends = {}
    for t in _active_targets:
        try:
            g = _TARGET_STREAM.get(t)
            ep = g() if g else None
            if ep:
                ends["%s:%s" % (ep[0], ep[1])] = t
        except Exception:
            pass
    ports = {}
    for t in _active_targets:
        try:
            pp = _player_telnet_port(t)
            if pp is not None:
                ports[str(pp)] = t
        except Exception:
            pass
    out = []
    try:
        raw = subprocess.run(["ps", "-ax", "-o", "pid=,etimes=,stat=,args="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return out
    for ln in raw.splitlines():
        f = ln.split(None, 3)
        if len(f) < 4 or f[2].startswith("Z"):
            continue                                   # Zombies (beendet) zaehlen nicht
        exe = os.path.basename(f[3].split()[0]).lower()
        if exe not in ("vlc", "mpv", "ffplay"):
            continue
        m = re.search(r"udp://@?([\d.]+:\d+)", f[3])
        url = m.group(1) if m else ""
        tgt = ends.get(url)
        if tgt is None:
            mt = re.search(r"--telnet-port[= ](\d+)", f[3])
            tgt = ports.get(mt.group(1)) if mt else None
        try:
            out.append({"pid": int(f[0]), "age": int(f[1]), "kind": exe, "url": url, "target": tgt})
        except ValueError:
            pass
    return out

def _players_check(report=True):
    """Doppelte (gleiche Stream-Adresse mehrfach) und alte/fremde Player (kein Ziel) suchen; Liste (doppelt, ohne Ziel) und Meldung im Log"""
    pl = _players_scan()
    by = {}
    for x in pl:
        by.setdefault(x["url"] or ("pid%d" % x["pid"]), []).append(x)
    dup = {u: v for u, v in by.items() if len(v) > 1}
    stray = [x for x in pl if x["target"] is None]
    if report:
        if not dup and not stray:
            olog("Player-Pruefung: %d Player, keine doppelten, keine alten/fremden." % len(pl))
        else:
            for u, v in dup.items():
                olog("Player-Pruefung: DOPPELT %s - %d Fenster (PID %s)" % (u, len(v), ", ".join("%d (%ds)" % (x["pid"], x["age"]) for x in v)))
            for x in stray:
                olog("Player-Pruefung: ohne Ziel/alt: %s PID %d %s (laeuft seit %d s)" % (x["kind"], x["pid"], x["url"] or "-", x["age"]))
    return dup, stray

def _players_restart_all():
    """Knopf PlayerNeu: ALLE Player dieser GUI-Ziele (auch doppelte, alte Sitzungen) schliessen und die gewollten neu starten"""
    def work():
        _focus_protect(25.0)
        dup, stray = _players_check(report=True)
        pl = _players_scan()
        mine = [x for x in pl if x["target"] is not None]          # Player mit Stream-Adresse/Telnet-Port dieser GUI-Ziele (doppelte und alte Sitzungen eingeschlossen); fremde Player ohne Ziel bleiben
        root.after(0, olog, "PlayerNeu: %d Player werden geschlossen%s." % (len(mine), (" (davon %d doppelt/alt)" % (sum(len(v) - 1 for v in dup.values()) + len(stray))) if (dup or stray) else ""))
        for x in mine:
            try:
                os.kill(x["pid"], signal.SIGTERM)
            except OSError:
                pass
        time.sleep(2.0)
        for x in mine:
            try:
                os.kill(x["pid"], signal.SIGKILL)       # nur noch falls er nicht beendet hat (ESRCH sonst)
            except OSError:
                pass
        for t in _active_targets:
            _player_proc[t] = None
        time.sleep(0.8)
        n = 0
        for t in _active_targets:
            if not _player_wanted.get(t) or _player_active.get(t) is None:
                continue
            if not _player_stream_url(t):
                continue
            _f = _target_cur_freq.get(t)
            if _f is None or (t != "octagon" and _fw_locked(t) is not True):
                continue                                  # nur dort neu starten, wo ein Signal anliegt (gelockt)
            root.after(400 * n, lambda tt=t: _start_player(tt, _player_active.get(tt) or "vlc"))
            n += 1
        root.after(0, olog, "PlayerNeu: %d Player werden neu gestartet (nur Tuner mit Signal)." % n)
    threading.Thread(target=work, daemon=True).start()

def _vlc_kill_port(port, label=""):
    """Alle VLC-Prozesse mit diesem Telnet-Port beenden (SIGTERM, nach 2 s SIGKILL). Hintergrund-Thread."""
    tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port)
    def pids():
        out = []
        for l in _ps_lines():
            if "vlc" in l.lower() and any(t in l for t in tags):
                try:
                    out.append(int(l.split()[0]))
                except ValueError:
                    pass
        return out
    p0 = pids()
    for p in p0:
        try:
            os.kill(p, signal.SIGTERM)
        except OSError:
            pass
    for _i in range(8):
        if not pids():
            break
        time.sleep(0.25)
    for p in pids():
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    if p0:
        root.after(0, olog, "%s: VLC (PID %s, Telnet-Port %d) beendet." % (label, ", ".join(map(str, p0)), port))
    return p0

def _player_watch_once(target):
    if _einfach["on"]:
        return                                        # Einfach-Ansicht: keine externen Player zu ueberwachen
    player = _player_active.get(target)
    if not _player_wanted.get(target) or player not in ("vlc", "mpv", "ffplay"):
        return
    now = time.time()
    if now - _player_last_restart.get(target, 0.0) < 20.0 or now - _player_started_at.get(target, 0.0) < 8.0:
        return
    label = _TARGET_LABEL.get(target, target)
    proc = _player_proc.get(target)
    proc_alive = proc is not None and proc.poll() is None
    if player == "vlc":
        port = _player_telnet_port(target)
        alive = port is not None and _is_port_open("127.0.0.1", port, timeout=1.0)
        if not alive and port is not None:
            tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port)
            ps_found = any(("vlc" in l.lower()) and any(t in l for t in tags) for l in _ps_lines())
            if ps_found:
                # Prozess da, Telnet antwortet nicht: laeuft noch hoch - oder haengt (z.B. nach 'shutdown'
                # nur halb beendet: Fenster mit VLC-Kegel bleibt offen, Steuerung tot). Nach 25 s eingreifen,
                # auch bei einem VLC aus einer frueheren Sitzung: Prozess beenden, dann neu starten.
                if now - max(_player_started_at.get(target, 0.0), _player_last_restart.get(target, 0.0)) > 25.0:
                    _vlc_kill_port(port, label)
                    root.after(1500, _player_restart, target, player,
                               "VLC laeuft laut ps, Telnet-Port %d antwortet aber nicht (beendet)" % port)
                return
    else:
        alive = proc_alive
    if not alive:
        _f0 = _target_cur_freq.get(target)
        if _f0 is None or (target != "octagon" and _fw_locked(target) is False):
            return                    # kein Signal bzw. kein Lock an diesem Tuner: keinen Player (und kein Fenster) oeffnen - sonst oeffnen sich beim Start Fenster fuer alle Tuner (Status unbekannt = wie frueher neu starten)
        root.after(0, _player_restart, target, player, "Player nicht gestartet/beendet (ps -ax + Port-Check)")
        return
    get_stream = _TARGET_STREAM.get(target)
    _ep = get_stream() if get_stream else None
    if not _ep:
        return
    ip, sport = _ep
    if _udp_listening(ip, sport) is False:
        miss = _player_udp_miss.get(target, 0) + 1
        _player_udp_miss[target] = miss
        if miss == 3 and player == "vlc":
            tport = _player_telnet_port(target)
            if tport is not None:
                root.after(0, lambda: (olog("%s: kein UDP-Socket auf %s:%d - sende 'play'." % (label, ip, sport)),
                                       _vlc_telnet_send(tport, "play", label)))
        elif miss >= 6:
            _player_udp_miss[target] = 0
            root.after(0, _player_restart, target, player, "kein UDP-Socket auf %s:%d" % (ip, sport))
    else:
        _player_udp_miss[target] = 0

#===== Fenster-Anordnung der Player: bei 3 Monitoren (davon 2 grosse) alle Player-Fenster auf den   =====
#===== rechten Monitor, links oben beginnend (Raster in der Reihenfolge der Spalten). Schemata      =====
#===== (Fensterpositionen je Ziel) lassen sich speichern/laden - Konfig -> Fenster-Anordnung....    =====
#===== Technik: xrandr (Monitore), wmctrl -lpG / -ir -e (Fenster per PID suchen, verschieben).      =====
_wm_placed = set()   # (Ziel, Fenster-ID) - was die Automatik schon einmal platziert hat
_wm_mon_cache = {"t": 0.0, "val": None}

def _monitors():
    """Angeschlossene, aktive Monitore als [{"name","x","y","w","h"}] (xrandr --query), [] wenn unbekannt."""
    xr = shutil.which("xrandr")
    if not xr:
        return []
    try:
        out = subprocess.run([xr, "--query"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=4).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    mons = []
    for line in out.splitlines():
        m = re.match(r'^(\S+) connected(?: primary)? (\d+)x(\d+)\+(\d+)\+(\d+)', line)
        if m:
            mons.append({"name": m.group(1), "w": int(m.group(2)), "h": int(m.group(3)),
                         "x": int(m.group(4)), "y": int(m.group(5))})
    return mons

def _pick_monitor(mons):
    """Ziel-Monitor aus app_config["window_monitor"]: Nummer (1-basiert) oder Ausgangsname;
    leer, ungueltig oder nicht vorhanden -> der erste Monitor."""
    if not mons:
        return None
    val = str(app_config.get("window_monitor", 3)).strip()
    if val:
        try:
            i = int(val)
            if 1 <= i <= len(mons):
                return mons[i - 1]
        except ValueError:
            for m in mons:
                if m["name"].lower() == val.lower():
                    return m
    return mons[0]

def _monitor_summary():
    """(alle Monitore, grosse Monitore (>= 1900x1000), drei-Monitore-Bedingung erfuellt?, rechter
    Ziel-Monitor = der am weitesten rechts liegende grosse, sonst der am weitesten rechts liegende)."""
    mons = _monitors()
    large = [m for m in mons if m["w"] >= 1900 and m["h"] >= 1000]
    three_ok = len(mons) >= 3 and len(large) >= 2
    right = _pick_monitor(mons)   # Ziel-Monitor laut Konfig (window_monitor), leer/ungueltig = erster
    return mons, large, three_ok, right

def _wm_windows():
    """Fensterliste per 'wmctrl -lpG' als [{"id","pid","x","y","w","h","title"}]; None ohne wmctrl."""
    wm = shutil.which("wmctrl")
    if not wm:
        return None
    try:
        out = subprocess.run([wm, "-lpG"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=4).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    wins = []
    for line in out.splitlines():
        parts = line.split(None, 8)   # id desktop pid x y w h host title
        if len(parts) < 8:
            continue
        try:
            wins.append({"id": parts[0], "pid": int(parts[2]), "x": int(parts[3]), "y": int(parts[4]),
                         "w": int(parts[5]), "h": int(parts[6]), "title": parts[8] if len(parts) > 8 else ""})
        except ValueError:
            continue
    return wins

def _player_pids(target):
    """PIDs der Player-Prozesse dieses Ziels: von der GUI gestartete + (VLC) alle mit dem passenden
    --telnet-port in der Kommandozeile (auch extern/vom Watchdog-Skript gestartete)."""
    pids = set()
    proc = _player_proc.get(target)
    if proc is not None and proc.poll() is None:
        pids.add(proc.pid)
    if target == "octagon" and _vlc_proc is not None and _vlc_proc.poll() is None:
        pids.add(_vlc_proc.pid)
    port = _player_telnet_port(target)
    if port is not None:
        tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port)
        for line in _ps_lines():
            if "vlc" in line.lower() and any(t in line for t in tags):
                try:
                    pids.add(int(line.split()[0]))
                except ValueError:
                    pass
    return pids

def _player_window(target, wins):
    pids = _player_pids(target)
    cands = [w for w in (wins or []) if w["pid"] in pids]
    if not cands:
        # mpv/ffplay, die schon vor dem GUI-Start liefen (Titel = Name des Ziels, siehe _start_player)
        label = _TARGET_LABEL.get(target, target)
        cands = [w for w in (wins or []) if w["title"] == label and _proc_comm(w["pid"]) in ("mpv", "ffplay")]
    return max(cands, key=lambda w: w["w"] * w["h"]) if cands else None

def _grid_geometry(target, mon):
    """Feld dieses Ziels auf dem Monitor mon: nebeneinander, ueberlappungsfrei, links oben beginnend,
    zeilenweise in der Reihenfolge der Spalten (_active_targets). Die Spaltenzahl wird so gewaehlt,
    dass ein 16:9-Videobild in der Kachel moeglichst gross wird. Rand fuer Fensterrahmen/Titelleiste
    ist abgezogen, damit sich die Rahmen nicht ueberdecken."""
    n = max(1, len(_active_targets))
    best = (0.0, 1)
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        cw, ch = mon["w"] / cols, mon["h"] / rows
        fit = min(cw, ch * 16 / 9.0)
        if fit >= best[0] - 1e-6:
            best = (fit, cols)
    cols = best[1]
    rows = -(-n // cols)
    idx = _active_targets.index(target)
    cw, ch = mon["w"] // cols, mon["h"] // rows
    return (mon["x"] + (idx % cols) * cw, mon["y"] + (idx // cols) * ch, cw - 6, ch - 40)

def _wm_place(win_id, x, y, w, h):
    wm = shutil.which("wmctrl")
    if not wm:
        return
    for args in (["-ir", win_id, "-b", "remove,maximized_vert,maximized_horz,fullscreen"],
                 ["-ir", win_id, "-e", "0,%d,%d,%d,%d" % (x, y, w, h)]):
        try:
            subprocess.run([wm] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Rueckmessung: viele Fenstermanager liefern/erwarten Rahmen-versetzte Werte - einmal nachkorrigieren,
    # damit die gemeldete Lage der gespeicherten entspricht (sonst wandert ein Fenster bei jedem Laden).
    try:
        time.sleep(0.5)
        for ww in (_wm_windows() or []):
            if ww["id"] == win_id:
                dx, dy, dw, dh = ww["x"] - x, ww["y"] - y, ww["w"] - w, ww["h"] - h
                if (max(abs(dx), abs(dy), abs(dw), abs(dh)) > 3) and max(abs(dx), abs(dy)) < 120 \
                        and max(abs(dw), abs(dh)) < 120:
                    subprocess.run([wm, "-ir", win_id, "-e", "0,%d,%d,%d,%d" % (x - dx, y - dy, w - dw, h - dh)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
                break
    except Exception as e:
        dlog("Fensterlage nachkorrigieren: %r" % (e,))

_wm_main_done = [False]
_wm_main_for = [None]     # Monitoranzahl, fuer die das Hauptfenster zuletzt platziert wurde
_wm_last_n = [None]       # Monitoranzahl beim letzten Takt (Wechsel -> alles neu platzieren)

def _main_own_window(wins):
    """Hauptfenster der GUI in der wmctrl-Liste: per Fenster-ID (Tk liefert sie), sonst per PID, sonst per
    Titel. (Tk setzt nicht auf jedem Fenstermanager _NET_WM_PID - dann war das Fenster nie auffindbar.)"""
    wins = wins or []
    ids = set()
    try:
        ids.add(int(root.winfo_id()))
    except Exception:
        pass
    try:
        ids.add(int(str(root.wm_frame()), 16))
    except Exception:
        pass
    me = []
    for w in wins:
        try:
            if int(w["id"], 16) in ids:
                me.append(w)
        except ValueError:
            pass
    if not me:
        me = [w for w in wins if w["pid"] == os.getpid()]
    if not me:
        try:
            ttl = root.title()
        except Exception:
            ttl = ""
        if ttl:
            me = [w for w in wins if w["title"] == ttl]
    return max(me, key=lambda x: x["w"] * x["h"]) if me else None

def _place_main_window(force=False, saved_only=False):
    """Eigenes Hauptfenster platzieren. Ist fuer die aktuelle Monitoranzahl eine Lage gemerkt
    (Schema-Eintrag 'main'), wird sie wiederhergestellt (Position UND Groesse); sonst - ausser bei
    saved_only - in die rechte untere Ecke des Monitors window_main_monitor (Groesse bleibt).
    Automatik: einmal pro Programmstart und nach jedem Wechsel der Monitoranzahl."""
    mons0 = _monitors()
    n0 = len(mons0)
    if _wm_main_done[0] and not force and _wm_main_for[0] == n0:
        return False
    if app_config.get("window_layout_auto", True) and n0:
        _schs = app_config.get("window_schemes") or {}
        # erst das gueltige Schema ("SystemSave#n" von Hand, sonst "System n Monitore"), dann das jeweils andere:
        # ein manuelles Schema ohne Hauptfenster-Eintrag darf den selbst gemerkten nicht verdecken
        mg = None
        for _nm in (_active_scheme_name(n0), _manual_scheme_name(n0), _sys_scheme_name(n0)):
            mg = (_schs.get(_nm) or {}).get("main")
            if mg:
                break
        if mg:
            w0 = _main_own_window(_wm_windows())
            if w0 is None:
                return False
            try:
                geo = (int(mg["x"]), int(mg["y"]), int(mg["w"]), int(mg["h"]))
            except (KeyError, TypeError, ValueError):
                geo = None
            if geo and _main_req[0] and geo[2] < _main_req[0]:
                geo = (geo[0], geo[1], _main_req[0], geo[3])      # nie schmaler als der Inhalt braucht
            if geo and _main_req[1] and geo[3] < _main_req[1]:
                geo = (geo[0], geo[1], geo[2], _main_req[1])      # nie niedriger: Spektrum unten muss zu sehen sein
            if geo:
                _wm_place(w0["id"], *geo)
                _wm_main_done[0] = True
                _wm_main_for[0] = n0
                _layout_hold["__main__"] = time.time() + 12.0
                _layout_last.pop("__main__", None)
                root.after(0, olog, "Fenster-Anordnung: Hauptfenster an gemerkte Lage gesetzt (%dx%d+%d+%d, %d Monitor%s)." % (
                    geo[2], geo[3], geo[0], geo[1], n0, "" if n0 == 1 else "e"))
                return True
    if saved_only:
        return False
    val = str(app_config.get("window_main_monitor", 3)).strip()
    if not val:
        return False
    wins = _wm_windows()
    mons = _monitors()
    if not wins or not mons:
        return False
    w = _main_own_window(wins)
    if w is None:
        return False
    mon = None
    try:
        i = int(val)
        if 1 <= i <= len(mons):
            mon = mons[i - 1]
    except ValueError:
        mon = next((m for m in mons if m["name"].lower() == val.lower()), None)
    if mon is None:
        mon = mons[0]
    x = mon["x"] + mon["w"] - w["w"] - 4
    y = mon["y"] + mon["h"] - w["h"] - 4
    _wm_place(w["id"], max(mon["x"], x), max(mon["y"], y), w["w"], w["h"])
    _wm_main_done[0] = True
    _wm_main_for[0] = len(mons)
    return True

def _arrange_players(scheme=None, only_new=False, main_geo=None):
    """Ordnet die Player-Fenster an: mit scheme ({Ziel: {x,y,w,h}}) nach diesem Schema, sonst als
    Raster auf dem rechten Monitor. only_new: nur Fenster, die die Automatik noch nicht platziert
    hat. main_geo ({x,y,w,h}): Lage des Hauptfensters aus dem Schema. Liefert (Anzahl verschoben, Meldung)."""
    if not shutil.which("wmctrl"):
        return 0, "wmctrl fehlt (sudo apt install wmctrl) - Fenster koennen nicht verschoben werden."
    wins = _wm_windows() or []
    right = None if scheme else _monitor_summary()[3]
    if not scheme and right is None:
        return 0, "Kein Monitor ermittelbar (xrandr fehlt?)."
    moved = 0
    dlog("Fenster-Anordnung: %s, %s, Monitor-Ziel: %s" % (
        "nur neue Fenster" if only_new else "alle Fenster",
        ("Schema mit %d Eintraegen" % len(scheme)) if scheme else "Raster",
        ("%s %dx%d+%d+%d" % (right["name"], right["w"], right["h"], right["x"], right["y"])) if right else "-"))
    if not only_new:
        try:
            _g = None
            if main_geo:
                try:
                    _g = (int(main_geo["x"]), int(main_geo["y"]), int(main_geo["w"]), int(main_geo["h"]))
                except (KeyError, TypeError, ValueError):
                    _g = None
            if _g:
                _w0 = _main_own_window(wins)
                if _w0 is not None:
                    _wm_place(_w0["id"], *_g)
                    _layout_hold["__main__"] = time.time() + 12.0
                    _layout_last.pop("__main__", None)
                    moved += 1
            elif _place_main_window(force=True):
                moved += 1
        except Exception as e:
            dlog("Hauptfenster platzieren: %r" % (e,))
    for t in _active_targets:
        w = _player_window(t, wins)
        if w is None:
            continue
        key = (t, w["id"])
        if only_new and key in _wm_placed:
            continue
        if scheme and t in scheme:
            g = scheme[t]
            geo = (int(g["x"]), int(g["y"]), int(g["w"]), int(g["h"]))
        elif right is not None:
            geo = _grid_geometry(t, right)
        else:
            continue
        dlog("Fenster-Anordnung: %s (PID %d, Fenster %s, Titel '%s') von %dx%d+%d+%d -> %dx%d+%d+%d [%s]" %
             (_TARGET_LABEL.get(t, t), w["pid"], w["id"], w["title"][:40], w["w"], w["h"], w["x"], w["y"],
              geo[2], geo[3], geo[0], geo[1], "Schema" if (scheme and t in scheme) else "Raster"))
        _wm_place(w["id"], *geo)
        _wm_placed.add(key)
        _layout_hold[t] = time.time() + 12.0     # eigene Platzierung nicht als "Nutzer hat verschoben" speichern
        _layout_last.pop(t, None)
        moved += 1
    if moved:
        _raise_players()                          # alle offenen Player nach vorn
    return moved, "%d Player-Fenster angeordnet." % moved

def _focus_active_id():
    """ID des Fensters, das gerade die Tastatur hat (_NET_ACTIVE_WINDOW), oder None."""
    xp = shutil.which("xprop")
    if not xp:
        return None
    try:
        out = subprocess.run([xp, "-root", "_NET_ACTIVE_WINDOW"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=2).stdout.decode(errors="replace")
        m = re.search(r"window id # (0x[0-9a-fA-F]+)", out)
        if m and int(m.group(1), 16) != 0:
            return m.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None

def _raise_window(win_id):
    """Fenster nach vorn holen OHNE ihm die Tastatur zu geben (xdotool windowraise); ohne xdotool
    wmctrl -ia und Tastatur sofort zurueck an das vorher aktive Fenster."""
    xd = shutil.which("xdotool")
    if xd:
        try:
            subprocess.run([xd, "windowraise", str(int(win_id, 16))], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=3)
            return
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    wm = shutil.which("wmctrl")
    if not wm:
        return
    keep = _focus_active_id()
    try:
        subprocess.run([wm, "-ia", win_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if keep and int(keep, 16) != int(win_id, 16):
        try:
            subprocess.run([wm, "-ia", keep], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass

_undecorated = set()
_undecorated_warned = [False]

def _wm_undecorate(win_id, on=True):
    """Rahmen/Titelleiste des Fenstermanagers ab- bzw. wieder anschalten (_MOTIF_WM_HINTS)."""
    xp = shutil.which("xprop")
    if not xp:
        if not _undecorated_warned[0]:
            _undecorated_warned[0] = True
            root.after(0, olog, "Player ohne Rand: 'xprop' fehlt (sudo apt install x11-utils).")
        return
    try:
        subprocess.run([xp, "-id", win_id, "-f", "_MOTIF_WM_HINTS", "32c", "-set", "_MOTIF_WM_HINTS",
                        "2, 0, %d, 0, 0" % (0 if on else 1)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3)
        (_undecorated.add if on else _undecorated.discard)(win_id)
        dlog("Rahmen %s fuer Fenster %s (xprop _MOTIF_WM_HINTS)" % ("aus" if on else "an", win_id))
    except (OSError, subprocess.TimeoutExpired) as e:
        dlog("Rahmen %s: xprop fehlgeschlagen: %r" % (win_id, e))

def _focus_restore(win_id):
    """Tastatur an das vorher aktive Fenster zurueckgeben, falls inzwischen ein Player sie hat."""
    wm = shutil.which("wmctrl")
    if not win_id or not wm:
        return
    now = _focus_active_id()
    if not now or int(now, 16) == int(win_id, 16):
        return
    pids = set()
    for t in _active_targets:
        pids |= _player_pids(t)
    wins = _wm_windows() or []
    cur = next((w for w in wins if int(w["id"], 16) == int(now, 16)), None)
    if cur is not None and cur["pid"] not in pids:
        return                     # der Nutzer hat selbst ein anderes Fenster angeklickt - nicht eingreifen
    try:
        subprocess.run([wm, "-ia", win_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass

#----- Fokusschutz: nach Player-Start/-Neustart und beim Verschieben/Kacheln der Fenster bekommt der Nutzer die Tastatur zurueck -----
# Merkt das zuletzt aktive Fenster, das KEIN Player ist (nicht das, was beim ersten von mehreren Starts schon ein Player ist), und holt es zurueck,
# solange ein Player die Tastatur hat. Waehrend des Schutzfensters folgt es dem Nutzer: klickt er ein anderes Fenster an, gilt dieses.
_focus_keep = [None]
_focus_protect_until = [0.0]
_focus_watch_on = [False]

def _focus_is_player(win_id):
    try:
        pids = set()
        for t in _active_targets:
            pids |= _player_pids(t)
        w = next((x for x in (_wm_windows() or []) if int(x["id"], 16) == int(win_id, 16)), None)
        return w is not None and w["pid"] in pids
    except Exception:
        return False

def _focus_watch():
    while True:
        time.sleep(0.4 if time.time() < _focus_protect_until[0] else 1.0)
        if time.time() >= _focus_protect_until[0]:
            continue
        try:
            act = _focus_active_id()
            if not act:
                continue
            if _focus_is_player(act):
                if _focus_keep[0] and _focus_keep[0] != act:
                    wm = shutil.which("wmctrl")
                    if wm:
                        subprocess.run([wm, "-ia", _focus_keep[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            else:
                _focus_keep[0] = act                    # Nutzerfenster (auch ein neu angeklicktes) merken
        except Exception as ex:
            dlog("Fokusschutz: %r" % (ex,))

def _focus_protect(seconds=8.0):
    act = _focus_active_id()
    if act and not _focus_is_player(act):
        _focus_keep[0] = act
    _focus_protect_until[0] = max(_focus_protect_until[0], time.time() + seconds)
    if not _focus_watch_on[0]:
        _focus_watch_on[0] = True
        threading.Thread(target=_focus_watch, daemon=True).start()

def _focus_guard(seconds=(1.0, 2.5, 4.0, 7.0, 10.0)):
    """Nach einem Player-Start/-Neustart die Tastatur dem Nutzer lassen (VLC holt sich beim Oeffnen sonst den Fokus)."""
    _focus_protect(max(seconds) + 2.0)

def _raise_players(include_main=False):
    """Alle offenen Player-Fenster (VLC/MPV/ffplay) in den Vordergrund holen (wmctrl -ia). Liefert die
    Anzahl. Laeuft im Hintergrund-Thread aufrufbar (kein Tk-Zugriff)."""
    wm = shutil.which("wmctrl")
    if not wm:
        return 0
    wins = _wm_windows() or []
    keep = _focus_active_id()              # Tastatur danach dem vorher aktiven Fenster zurueckgeben
    n = 0
    if _max.get("id"):
        return 0                           # ein Player ist maximiert: die anderen bleiben im Hintergrund
    for t in _active_targets:
        w = _player_window(t, wins)
        if w is None or w["w"] < 100 or w["h"] < 60:
            continue
        _raise_window(w["id"])
        n += 1
    if _max.get("id"):
        try:          # ein maximierter Player bleibt vorne
            subprocess.run([wm, "-ia", _max["id"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if n and keep:
        _focus_restore(keep)
    return n

def _capture_main():
    """Aktuelle Lage des Hauptfensters als {x,y,w,h} (None, wenn nicht ermittelbar)."""
    w = _main_own_window(_wm_windows() or [])
    return {"x": w["x"], "y": w["y"], "w": w["w"], "h": w["h"]} if w else None

def _capture_layout():
    """Aktuelle Fensterpositionen der Player je Ziel: {Ziel: {x,y,w,h}}."""
    wins = _wm_windows() or []
    out = {}
    for t in _active_targets:
        w = _player_window(t, wins)
        if w:
            out[t] = {"x": w["x"], "y": w["y"], "w": w["w"], "h": w["h"]}
    return out

# ---- Fensterlagen je Monitoranzahl selbst merken ("System 1 Monitor", "System 2 Monitore", ...) ----
# Gespeichert wird unter app_config["window_schemes"] - dieselben Schemata wie in Konfig ->
# Fenster-Anordnung (dort laden/loeschen). Automatik: Lage aendert sich und bleibt zwei Takte (10 s)
# stehen -> speichern. Beim Programmstart: Player, die schon vor der GUI liefen, pruefen - passt die Lage,
# bleiben sie offen, sonst beenden und an der gespeicherten Position neu starten.
_layout_hold = {}       # Ziel -> Zeitpunkt, bis zu dem NICHT gespeichert wird (nach eigener Platzierung/Neustart)
_layout_last = {}       # Ziel -> (Fenster-ID, (x,y,w,h)) vom letzten Takt
_layout_startup_done = [False]
_layout_t0 = time.time()

def _proc_comm(pid):
    try:
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip()
    except OSError:
        return ""

def _sys_scheme_name(n):
    return "System %d Monitor%s" % (n, "" if n == 1 else "e")

def _mon_count():
    now = time.time()
    if _wm_mon_cache["val"] is None or now - _wm_mon_cache["t"] > 60:
        _wm_mon_cache["val"] = _monitor_summary()
        _wm_mon_cache["t"] = now
    return len(_wm_mon_cache["val"][0])

def _manual_scheme_name(n):
    return "SystemSave#%d" % n

def _active_scheme_name(n):
    """Welches Schema gilt fuer n Monitore: ein von Hand gespeichertes 'SystemSave#n' hat Vorrang vor
    dem selbst gemerkten 'System n Monitor(e)'."""
    schemes = app_config.get("window_schemes") or {}
    man = _manual_scheme_name(n)
    if (schemes.get(man) or {}).get("targets"):
        return man
    return _sys_scheme_name(n)

def _layout_save_manual():
    """Knopf 'Fenster speichern': aktuelle Lage aller Player-Fenster als 'SystemSave#<Monitoranzahl>'
    ablegen (jedes Mal ueberschrieben). Merkt die Monitoranzahl und -geometrie mit."""
    n = _mon_count()
    wins = _wm_windows()
    if n < 1 or not wins:
        root.after(0, olog, "Fenster speichern: keine Fenster/Monitore ermittelbar (wmctrl/xrandr installiert?).")
        return 0
    targets, players = {}, {}
    for t in _active_targets:
        w = _player_window(t, wins)
        if w is None or w["w"] < 100 or w["h"] < 60:
            continue
        targets[t] = {"x": w["x"], "y": w["y"], "w": w["w"], "h": w["h"]}
        pl = _proc_comm(w["pid"])
        players[t] = pl if pl in ("vlc", "mpv", "ffplay") else (_player_active.get(t) or "vlc")
    if not targets:
        root.after(0, olog, "Fenster speichern: keine Player-Fenster gefunden - nichts gespeichert.")
        return 0
    name = _manual_scheme_name(n)
    schemes = dict(app_config.get("window_schemes") or {})
    _mw = _main_own_window(wins)
    schemes[name] = {"targets": targets, "player": players, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "monitor_count": n, "manual": True,
                     "main": ({"x": _mw["x"], "y": _mw["y"], "w": _mw["w"], "h": _mw["h"]} if _mw else None),
                     "monitors": ["%s %dx%d+%d+%d" % (m["name"], m["w"], m["h"], m["x"], m["y"])
                                  for m in _wm_mon_cache["val"][0]]}
    app_config["window_schemes"] = schemes
    app_config["window_monitor_count"] = n
    app_config_save()
    root.after(0, olog, "Fenster-Anordnung: %d Player-Fenster als '%s' gespeichert (%d Monitor%s)." %
               (len(targets), name, n, "" if n == 1 else "e"))
    return len(targets)

def _geo_tuple(g):
    if isinstance(g, dict):
        return (int(g["x"]), int(g["y"]), int(g["w"]), int(g["h"]))
    return tuple(int(v) for v in g)

def _geo_close(a, b, pos_tol=40, size_tol=80):
    a, b = _geo_tuple(a), _geo_tuple(b)
    return (abs(a[0] - b[0]) <= pos_tol and abs(a[1] - b[1]) <= pos_tol and
            abs(a[2] - b[2]) <= size_tol and abs(a[3] - b[3]) <= size_tol)

def _layout_autosave_tick(force=False):
    """Fensterlagen der Player (VLC/MPV/ffplay) fuer die aktuelle Monitoranzahl merken."""
    if not app_config.get("window_layout_auto", True):
        return
    n = _mon_count()
    wins = _wm_windows()
    if n < 1 or not wins:
        return
    name = _sys_scheme_name(n)
    schemes = dict(app_config.get("window_schemes") or {})
    sc = dict(schemes.get(name) or {})
    targets = dict(sc.get("targets") or {})
    players = dict(sc.get("player") or {})
    now = time.time()
    changed = []
    for t in _active_targets:
        w = _player_window(t, wins)
        if w is None or w["w"] < 200 or w["h"] < 120:
            _layout_last.pop(t, None)
            continue
        geo = (w["x"], w["y"], w["w"], w["h"])
        prev = _layout_last.get(t)
        _layout_last[t] = (w["id"], geo)
        if now < _layout_hold.get(t, 0.0):
            continue
        if not force and prev != (w["id"], geo):
            continue                      # erst speichern, wenn die Lage einen Takt unveraendert ist
        saved = targets.get(t)
        if saved and _geo_close(saved, geo, 6, 12):
            continue
        targets[t] = {"x": geo[0], "y": geo[1], "w": geo[2], "h": geo[3]}
        pl = _proc_comm(w["pid"])
        players[t] = pl if pl in ("vlc", "mpv", "ffplay") else (_player_active.get(t) or "vlc")
        changed.append(_TARGET_LABEL.get(t, t))
    wm_main = _main_own_window(wins)
    if (wm_main is not None and wm_main["w"] >= 300 and wm_main["h"] >= 200
            and not (_main_req[0] and wm_main["w"] < _main_req[0] - 20)):    # abgeschnittene Lage nicht merken
        geo = (wm_main["x"], wm_main["y"], wm_main["w"], wm_main["h"])
        prev = _layout_last.get("__main__")
        _layout_last["__main__"] = (wm_main["id"], geo)
        if now >= _layout_hold.get("__main__", 0.0) and (force or prev == (wm_main["id"], geo)):
            saved = sc.get("main")
            if not (saved and _geo_close(saved, geo, 6, 12)):
                sc["main"] = {"x": geo[0], "y": geo[1], "w": geo[2], "h": geo[3]}
                changed.append("Hauptfenster")
    if changed:
        sc.update({"targets": targets, "player": players, "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "monitor_count": n,
                   "monitors": ["%s %dx%d+%d+%d" % (m["name"], m["w"], m["h"], m["x"], m["y"])
                                for m in _wm_mon_cache["val"][0]], "auto": True})
        schemes[name] = sc
        app_config["window_schemes"] = schemes
        app_config["window_monitor_count"] = n
        app_config_save()
        root.after(0, olog, "Fenster-Anordnung: Lage von %s gemerkt ('%s')." % (", ".join(changed), name))

def _layout_startup_check():
    """Einmal nach dem Programmstart: Player, die schon vor der GUI liefen, mit der gespeicherten Lage
    dieser Monitoranzahl vergleichen - gleich: offen lassen; abweichend: beenden und neu starten (die
    Automatik setzt das neue Fenster dann an die gespeicherte Position)."""
    if (not app_config.get("window_auto_arrange", True) or not app_config.get("window_layout_auto", True)
            or app_config.get("window_tile", True)):
        return                       # Anordnung per Knopf 'Auto-Anord' ausgeschaltet: laufende Player-Fenster bleiben unberuehrt
    n = _mon_count()
    name = _active_scheme_name(n)
    sc = (app_config.get("window_schemes") or {}).get(name) or {}
    targets = sc.get("targets") or {}
    if not targets:
        root.after(0, olog, "Fenster-Anordnung: fuer '%s' ist noch keine Lage gespeichert." % _sys_scheme_name(n))
        return
    wins = _wm_windows() or []
    for t in _active_targets:
        saved = targets.get(t)
        w = _player_window(t, wins)
        if not saved or w is None:
            continue
        proc = _player_proc.get(t)
        if proc is not None and proc.pid == w["pid"]:
            continue                      # in dieser Sitzung gestartet - die Automatik platziert es
        label = _TARGET_LABEL.get(t, t)
        geo = (w["x"], w["y"], w["w"], w["h"])
        _layout_hold[t] = time.time() + 25.0
        if _geo_close(saved, geo):
            _wm_placed.add((t, w["id"]))
            root.after(0, olog, "Fenster-Anordnung: %s liegt wie gespeichert ('%s') - bleibt offen." % (label, name))
            continue
        pl = _proc_comm(w["pid"])
        pl = pl if pl in ("vlc", "mpv", "ffplay") else (_player_active.get(t) or "vlc")
        root.after(0, olog, "Fenster-Anordnung: %s weicht von der gespeicherten Lage ab (%dx%d+%d+%d statt "
                   "%dx%d+%d+%d) - %s wird beendet und neu gestartet." % (
                       label, geo[2], geo[3], geo[0], geo[1], saved["w"], saved["h"], saved["x"], saved["y"], pl))
        try:
            os.kill(w["pid"], signal.SIGTERM)
        except OSError as e:
            dlog("Fensterlage: Player %d nicht beendbar: %r" % (w["pid"], e))
            continue
        _wm_placed.discard((t, w["id"]))
        root.after(1800, _start_player, t, pl)
    _raise_players()      # die offen gebliebenen Player nach vorn (neu gestartete folgen durch die Anordnung)

#----- Kachel-Modus: alle offenen Player auf dem Ziel-Monitor so anordnen, dass sie sich nicht ueberlappen
#      und das Hauptfenster frei bleibt. Neu angeordnet wird, sobald ein Player-Fenster dazukommt/wegfaellt.
#      Quad (Knopf "Quad"): Hauptfenster rechts unten in die Ecke, Player in festen, gleich grossen Kacheln
#      (2 x 2 bei bis zu 4 Empfaengern) links davon ueber die volle Hoehe. -----
_tile_sig = [None]

def _tile_best(area, n, min_cols=1):
    """(Spalten, Zeilen, Kachelbreite fuer 16:9) mit groesstmoeglichem Videobild fuer n Fenster in area."""
    best = None
    for cols in range(max(1, min(min_cols, n)), n + 1):
        rows = -(-n // cols)
        cw, ch = area["w"] / float(cols), area["h"] / float(rows)
        fit = min(cw, (ch - 40) * 16 / 9.0)
        if best is None or fit > best[2] + 1e-6:
            best = (cols, rows, fit)
    return best

def _tile_free_areas(mon, mg):
    """Freie Rechtecke des Monitors ohne das Hauptfenster (links/rechts/oben/unten davon) - bzw. der ganze Monitor."""
    if not mg:
        return [dict(mon)]
    mx0, my0, mx1, my1 = mon["x"], mon["y"], mon["x"] + mon["w"], mon["y"] + mon["h"]
    gx0, gy0 = max(mx0, mg["x"] - 8), max(my0, mg["y"] - 40)         # Rahmen/Titelleiste des Hauptfensters
    gx1, gy1 = min(mx1, mg["x"] + mg["w"] + 8), min(my1, mg["y"] + mg["h"] + 8)
    if gx0 >= gx1 or gy0 >= gy1:
        return [dict(mon)]                                            # Hauptfenster nicht auf diesem Monitor
    areas = [{"x": mx0, "y": my0, "w": gx0 - mx0, "h": mon["h"]},      # links
             {"x": gx1, "y": my0, "w": mx1 - gx1, "h": mon["h"]},      # rechts
             {"x": mx0, "y": my0, "w": mon["w"], "h": gy0 - my0},      # oben
             {"x": mx0, "y": gy1, "w": mon["w"], "h": my1 - gy1}]      # unten
    return [a for a in areas if a["w"] >= 200 and a["h"] >= 150]

_tile_lock = threading.Lock()
_tile_pending = [False]
_TILE_MODES = ("kacheln", "oben_h", "unten_h", "quad", "voll")
_ui_mode = ["kacheln"]            # zuletzt angewendeter Modus (fuer Knoepfe/Menue)

def _mode_key(n):
    """Schluessel 'Monitoranzahl|Playerzahl' (1, 2, 3, 4, 5 = 5 und mehr) fuer die gemerkte Anordnung"""
    return "%d|%d" % (_mon_count(), min(max(int(n), 1), 5))

def _eff_mode(n):
    """Anordnung fuer n Player-Fenster: die zu (Monitoranzahl, Playerzahl) gemerkte (app_config['window_mode_by_count']), sonst die allgemeine Einstellung"""
    m = (app_config.get("window_mode_by_count") or {}).get(_mode_key(n))
    if m in _TILE_MODES:
        return m
    if app_config.get("window_quad"):
        return "quad"
    if app_config.get("window_full"):
        return "voll"
    m = app_config.get("window_mode")
    return m if m in ("oben_h", "unten_h") else "kacheln"

def _tile_players(force=False):
    """Kacheln/Quad/Voll/Reihe: liefert Anzahl verschobener Fenster (0 = nichts zu tun). Laeuft nie doppelt gleichzeitig; ein Aufruf mit force waehrend
    eines Laufs merkt sich einen weiteren Durchlauf direkt danach (statt doppelter wmctrl-Aufrufe aus mehreren Threads)."""
    if not _tile_lock.acquire(False):
        if force:
            _tile_pending[0] = True
        return 0
    try:
        n = _tile_players_impl(force)
        while _tile_pending[0]:
            _tile_pending[0] = False
            n = _tile_players_impl(True)
        return n
    finally:
        _tile_lock.release()

def _tile_players_impl(force=False):
    """eigentliche Anordnung (nur ueber _tile_players aufrufen)"""
    if not shutil.which("wmctrl"):
        return 0
    _focus_protect(10.0)                       # das Verschieben darf dem Nutzer nicht die Tastatur nehmen
    mons = _monitors()
    mon = _pick_monitor(mons)
    if mon is None:
        return 0
    wins = _wm_windows() or []
    pw = []
    for t in _active_targets:
        w = _player_window(t, wins)
        if w is not None and not (_max["t"] == t and _max["id"] == w["id"]):     # maximierter Player bleibt, wie er ist
            pw.append((t, w))
    mode = _eff_mode(len(pw))                          # Anordnung fuer diese Zahl Player (und Monitoranzahl)
    quad, full = (mode == "quad"), (mode == "voll")
    app_config["window_quad"], app_config["window_full"] = quad, full      # (nur im Speicher) damit andere Stellen dieselbe Anordnung sehen
    if _ui_mode[0] != mode:
        _ui_mode[0] = mode
        root.after(0, _quad_button_update)
    vmax = float(app_config.get("video_max_scale", 2.0) or 0)
    sig = (quad, bool(app_config.get("vlc_borderless", False)), mon["name"], mon["w"], mon["h"], tuple(w["id"] for _t, w in pw),
           vmax, _window_zoom(), tuple(_fw_res.get(_t) for _t, _w in pw), mode)
    if not pw or (sig == _tile_sig[0] and not force):
        _tile_sig[0] = sig if pw else None
        return 0
    main = _main_own_window(wins)
    mg = {"x": main["x"], "y": main["y"], "w": main["w"], "h": main["h"]} if main else None
    if quad and main is not None:
        # Hauptfenster in die rechte untere Ecke des Ziel-Monitors (Groesse bleibt)
        nx = mon["x"] + mon["w"] - main["w"] - 4
        ny = mon["y"] + mon["h"] - main["h"] - 4
        if abs(nx - main["x"]) > 4 or abs(ny - main["y"]) > 4:
            _wm_place(main["id"], max(mon["x"], nx), max(mon["y"], ny), main["w"], main["h"])
            _layout_hold["__main__"] = time.time() + 12.0
        mg = {"x": max(mon["x"], nx), "y": max(mon["y"], ny), "w": main["w"], "h": main["h"]}
    areas = _tile_free_areas(mon, mg) or [dict(mon)]
    n = len(pw)
    if full:
        # Knopf 'Voll': den ganzen Bildschirm nutzen (Hauptfenster wird ueberdeckt) - bei 1, 2, 3, 4 Playern grosse Zellen (1 | 2 nebeneinander | 2x2), ab 5 neu unterteilt
        cols, rows, _fit = _tile_best(dict(mon), n)
        area = dict(mon)
    elif quad:
        # Flaeche links bzw. ueber dem Hauptfenster - die mit dem groessten Bild in festem Raster
        def _qgrid(a):
            if n <= 4:
                c, r = (2, 2) if n > 2 else (2, 1) if n == 2 else (1, 1)
                return c, r, min(a["w"] / float(c), (a["h"] / float(r) - 40) * 16 / 9.0)
            return _tile_best(a, n, min_cols=2)
        (cols, rows, _fit), area = max(((_qgrid(a), a) for a in areas), key=lambda x: x[0][2])
    elif mode == "oben_h":
        # Anordnung 1: alle Player nebeneinander in EINER Reihe oben, jedes im Bildformat des Videos; das Hauptfenster bleibt darunter frei
        top_a = (next((a for a in areas if a["y"] == mon["y"] and a["w"] >= mon["w"] - 20), None)
                 or (max(areas, key=lambda a: a["w"] * a["h"]) if mg else None) or {"x": mon["x"], "y": mon["y"], "w": mon["w"], "h": max(200, mon["h"] // 2)})   # oben kein Platz (Hauptfenster ganz oben): groesste freie Flaeche statt das Hauptfenster zu ueberdecken
        cols, rows, area = n, 1, top_a
    elif mode == "unten_h":
        bot_a = (next((a for a in areas if a["y"] > mon["y"] and a["w"] >= mon["w"] - 20), None)
                 or (max(areas, key=lambda a: a["w"] * a["h"]) if mg else None) or {"x": mon["x"], "y": mon["y"] + mon["h"] // 2, "w": mon["w"], "h": mon["h"] // 2})
        cols, rows, area = n, 1, bot_a
    else:
        scored = [(_tile_best(a, n), a) for a in areas]
        (cols, rows, _fit), area = max(scored, key=lambda x: x[0][2])
    cw, ch = area["w"] // cols, area["h"] // rows
    bl = bool(app_config.get("vlc_borderless", False))
    for i, (t, w) in enumerate(pw):
        if bl and w["id"] not in _undecorated:
            _wm_undecorate(w["id"], True)
        elif not bl and w["id"] in _undecorated:
            _wm_undecorate(w["id"], False)
        geo = (area["x"] + (i % cols) * cw, area["y"] + (i // cols) * ch,
               cw - (0 if bl else 6), ch - (0 if bl else 40))
        # Fenster genau im Bildformat des Videos (keine schwarzen Raender): groesstmoeglich in der Kachel, Menue/Bedienleiste von VLC (ca. 101 px) eingerechnet;
        # Video unbekannt = 16:9. Begrenzung: hoechstens Videoaufloesung x max. Vergroesserung (nicht im Quad-Raster)
        res = _fw_res.get(t)
        rw_, rh_ = (res[0], res[1]) if res and res[0] and res[1] else (1280, 720)
        chrome = 0 if bl else 101
        sc = min(geo[2] / float(rw_), (geo[3] - chrome) / float(rh_))
        if not quad and not full:
            zoom = _window_zoom()
            if vmax > 0:
                sc = min(sc, vmax * zoom)          # Strg + : Vergroesserungsgrenze waechst bis zur Kachelgroesse
            if zoom < 1.0:
                sc = sc * zoom                     # Strg - : alle Fenster der Anordnung kleiner
        _gw, _gh = geo[2], geo[3]
        geo = (geo[0], geo[1], max(160, int(rw_ * sc)), max(90, int(rh_ * sc) + chrome))
        if full:
            geo = (geo[0] + max(0, (_gw - geo[2]) // 2), geo[1] + max(0, (_gh - geo[3]) // 2), geo[2], geo[3])      # in der Zelle zentrieren
        _wm_place(w["id"], *geo)
        _wm_placed.add((t, w["id"]))
        _layout_hold[t] = time.time() + 12.0
        _layout_last.pop(t, None)
    _tile_sig[0] = sig
    _mode_txt = ("Quad" if quad else "Voll (ganzer Bildschirm, Hauptfenster wird ueberdeckt)" if full else
                 "Reihe oben" if mode == "oben_h" else "Reihe unten" if mode == "unten_h" else "Kacheln")
    root.after(0, olog, "Fenster-Anordnung (%s): %d Player auf Monitor %s (%d x %d, je %dx%d)%s." % (
        _mode_txt, n, mon["name"], cols, rows, cw - 6, ch - 40, "" if full else " - ohne Ueberlappung, Hauptfenster frei"))
    _raise_players()
    return n

def _window_zoom():
    try:
        return min(4.0, max(0.25, float(app_config.get("window_zoom", 1.0))))
    except (TypeError, ValueError):
        return 1.0

def _window_zoom_step(d):
    """Strg + / Strg - / Strg 0: alle Player-Fenster der Anordnung groesser (d>0) / kleiner (d<0) / Standard (d=0); gilt fuer Reihe oben/unten und Kacheln
    (Quad und Voll haben feste Zellen). Vergroessern wirkt, solange das Video kleiner als seine Kachel ist; die Kachel selbst ist die Obergrenze."""
    z = 1.0 if d == 0 else _window_zoom() * (1.15 if d > 0 else 1 / 1.15)
    z = min(4.0, max(0.25, round(z, 3)))
    app_config["window_zoom"] = z
    app_config_save()
    mode = _eff_mode(_n_players_now())
    olog("Player-Fenster: Groesse %d %% (%s)%s." % (round(z * 100), _MODE_NAMES.get(mode, mode),
         "" if mode not in ("quad", "voll") else " - in Quad/Voll ohne Wirkung (feste Zellen)"))
    threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start()

_MODE_NAMES = {"kacheln": "Kacheln (Raster)", "oben_h": "Reihe oben (horizontal)", "unten_h": "Reihe unten (horizontal)",
               "quad": "Quad (Hauptfenster rechts unten)", "voll": "Voll (ganzer Bildschirm)"}

def _n_players_now():
    """Zahl der Player-Fenster, die gerade offen sind (mindestens 1)"""
    return len(_capture_layout()) or 1

def _set_mode(m):
    """Anordnung waehlen: gilt sofort und wird fuer (Monitoranzahl, Zahl der Player-Fenster 1/2/3/4/5+) in app_config['window_mode_by_count'] gemerkt"""
    if m not in _TILE_MODES:
        return
    n = _n_players_now()
    d = dict(app_config.get("window_mode_by_count") or {})
    d[_mode_key(n)] = m
    app_config["window_mode_by_count"] = d
    app_config["window_quad"], app_config["window_full"] = (m == "quad"), (m == "voll")
    if m in ("kacheln", "oben_h", "unten_h"):
        app_config["window_mode"] = m
    app_config["window_tile"] = True
    try:
        _tile_var.set(True)
    except NameError:
        pass
    _ui_mode[0] = m
    app_config_save()
    _quad_button_update()
    olog("Fenster-Anordnung fuer %d Player bei %d Monitor%s: %s (wird gemerkt)." % (n, _mon_count(), "" if _mon_count() == 1 else "en", _MODE_NAMES.get(m, m)))
    threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start()

def _quad_toggle():
    _set_mode("kacheln" if _eff_mode(_n_players_now()) == "quad" else "quad")

def _quad_button_update():
    m = _ui_mode[0]
    try:
        btn_batc_quad.configure(style="RecOn.TButton" if m == "quad" else "TButton")
        btn_batc_voll.configure(style="RecOn.TButton" if m == "voll" else "TButton")
        _anord_var.set(m)
    except (NameError, tk.TclError):
        pass

_main_req = [0, 0]      # vom Inhalt benoetigte Breite/Hoehe des Hauptfensters (Hauptthread misst)
_main_spec_logged = [False]

def _main_minsize_tick():
    """Hauptthread, alle 5 s: Mindestbreite des Hauptfensters = Breite, die der Inhalt braucht; ist es
    schmaler (gemerkte alte Lage, Fenstermanager), wird es verbreitert - nichts wird mehr abgeschnitten."""
    try:
        rw, rh = root.winfo_reqwidth(), root.winfo_reqheight()
        if _mini["on"] and _mini.get("cols") and not _einfach["on"]:
            rw = int(_MINI_COL_MIN * int(_mini["cols"]) + 10 * (int(_mini["cols"]) - 1) + 40)      # Mini: nur die schmalste sinnvolle Breite (Spektrum passt sich an)
        if rw > 200:
            _main_req[0], _main_req[1] = rw, rh
            root.minsize(rw, min(rh, 500))
            cw, ch = root.winfo_width(), root.winfo_height()
            if cw > 1 and cw < rw - 10:
                root.geometry("%dx%d" % (rw, ch))
                olog("Hauptfenster war zu schmal (%d px, Inhalt braucht %d px) - verbreitert." % (cw, rw))
            # Hoehe: der ganze Inhalt inkl. Spektrum unten muss passen - und das Fenster ganz auf seinem Monitor liegen
            mons = (_wm_mon_cache.get("val") or [[]])[0] or []
            x, y = root.winfo_rootx(), root.winfo_rooty()
            mon = next((m for m in mons if m["x"] <= x < m["x"] + m["w"] and m["y"] <= y < m["y"] + m["h"]), None)
            if mon and cw > 1 and ch > 1:
                maxh = mon["h"] - 60
                nh = min(max(ch, rh), maxh)
                ny = y
                if ny + nh > mon["y"] + mon["h"] - 10:
                    ny = max(mon["y"] + 30, mon["y"] + mon["h"] - nh - 10)
                if nh != ch or ny != y:
                    root.geometry("%dx%d+%d+%d" % (max(cw, rw), nh, root.winfo_x(), root.winfo_y() + (ny - y)))
                    if not _main_spec_logged[0]:
                        _main_spec_logged[0] = True
                        olog("Hauptfenster angepasst (%dx%d), damit das Spektrum ganz zu sehen ist." % (max(cw, rw), nh))
    except tk.TclError:
        pass
    root.after(5000, _main_minsize_tick)

def _wm_auto_tick():
    """Hintergrund-Thread: neue Player-Fenster automatisch platzieren - mit der gemerkten Lage der aktuellen
    Monitoranzahl ("System N Monitore"), sonst nur bei 3 Monitoren (2 grosse) als Raster."""
    if not app_config.get("window_auto_arrange", True):
        dlog("Fenster-Anordnung: Automatik ist ausgeschaltet (Konfig).")
        return
    now = time.time()
    if _wm_mon_cache["val"] is None or now - _wm_mon_cache["t"] > 60:
        _wm_mon_cache["val"] = _monitor_summary()
        _wm_mon_cache["t"] = now
    _n_now = len(_wm_mon_cache["val"][0])
    if _wm_last_n[0] is not None and _wm_last_n[0] != _n_now:
        _wm_placed.clear()
        _layout_last.clear()
        _wm_main_done[0] = False
        for _t in list(_active_targets) + ["__main__"]:
            _layout_hold[_t] = now + 15.0
        olog("Fenster-Anordnung: Monitoranzahl hat sich geaendert (%s -> %d) - Fenster werden nach der "
             "gemerkten Lage fuer %d Monitor%s neu platziert." % (_wm_last_n[0], _n_now, _n_now, "" if _n_now == 1 else "e"))
    _wm_last_n[0] = _n_now
    if app_config.get("window_tile", True):
        if not app_config.get("window_quad", False):
            try:
                _place_main_window(saved_only=True)
            except Exception as e:
                dlog("Hauptfenster (gemerkte Lage): %r" % (e,))
        _tile_players()
        return
    if app_config.get("window_layout_auto", True):
        try:
            _place_main_window(saved_only=True)
        except Exception as e:
            dlog("Hauptfenster (gemerkte Lage): %r" % (e,))
        _sysname = _active_scheme_name(len(_wm_mon_cache["val"][0]))
        _sys = ((app_config.get("window_schemes") or {}).get(_sysname) or {})
        if _sys.get("targets"):
            moved, _msg = _arrange_players(_sys["targets"], only_new=True)
            if moved:
                olog("Fenster-Anordnung: %d Player-Fenster an die gemerkte Lage gesetzt ('%s')." % (moved, _sysname))
            return
    if not _wm_mon_cache["val"][2]:
        dlog("Fenster-Anordnung: Automatik pausiert - %d Monitore, davon %d gross (gebraucht: 3 Monitore, 2 grosse)."
             % (len(_wm_mon_cache["val"][0]), len(_wm_mon_cache["val"][1])))
        return
    if _place_main_window():
        olog("Fenster-Anordnung: Hauptfenster rechts unten platziert.")
    name = app_config.get("window_auto_scheme") or ""
    scheme = ((app_config.get("window_schemes") or {}).get(name) or {}).get("targets") if name else None
    moved, _msg = _arrange_players(scheme or None, only_new=True)
    if moved:
        olog("Fenster-Anordnung: %d neue(s) Player-Fenster automatisch platziert." % moved)

def _wm_kick(delay=3.0):
    """Nach dem Start eines Players: nach kurzer Wartezeit (Fenster muss erst erscheinen) neue
    Player-Fenster sofort anordnen, statt auf den naechsten 5-s-Takt zu warten."""
    def work():
        time.sleep(delay)
        try:
            dlog("Fenster-Anordnung: Pruefung nach Player-Start.")
            _wm_auto_tick()
        except Exception as e:
            dlog("Fenster-Anordnung (kick): %r" % (e,))
    threading.Thread(target=work, daemon=True).start()

def _player_watchdog_loop():
    time.sleep(3)
    while True:
        try:
            if app_config.get("player_watchdog", True):
                for t in _active_targets:
                    if t != "octagon" or _oct_direct():
                        _player_watch_once(t)
            _wm_auto_tick()
            if not _layout_startup_done[0] and time.time() - _layout_t0 > 12.0:
                _layout_startup_done[0] = True
                _layout_startup_check()
                _raise_players()          # beim Start alle offenen Player nach vorn
            elif _layout_startup_done[0]:
                _layout_autosave_tick()
        except Exception as e:
            dlog("Player-Ueberwachung/Fensteranordnung: %r" % (e,))
        time.sleep(5)

#----- Bild-Waechter: erkennt, ob VLC/mpv wirklich Bilder zeigt oder das Bild steht -----------------
# VLC: Telnet-Kommando "stats" -> "frames displayed"; mpv: IPC-Property "estimated-frame-number".
# Steht der Zaehler >= FRAMEWATCH_FROZEN_S, obwohl auf dem UDP-Stream Daten ankommen:
#   Stufe 1: Stop/Play (VLC per Telnet, mpv per loadfile)  ->  hilft das nach FRAMEWATCH_FROZEN_S nicht:
#   Stufe 2: Player-Neustart. Ohne Stream-Daten (kein Signal) wird nichts gemacht (das ist Sache von AutoClose).
FRAMEWATCH_PERIOD_S = 5
FRAMEWATCH_FROZEN_S = 20
_fw_state = {}     # target -> {"cnt", "chg", "stage", "stage_t"}

def _vlc_telnet_query(port, command, wait=0.8):
    """Kommando an das VLC-Telnet-Interface schicken und die Antwort als Text liefern (None bei Fehler)."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2.0) as so:
            so.settimeout(0.5)
            def _drain(t):
                buf = b""
                t_end = time.time() + t
                while time.time() < t_end:
                    try:
                        d = so.recv(4096)
                        if not d:
                            break
                        buf += d
                    except socket.timeout:
                        if buf:
                            break
                return buf
            _drain(0.6)
            so.sendall(b"admin\n")
            _drain(0.6)
            so.sendall((command + "\n").encode())
            out = _drain(wait)
            try:
                so.sendall(b"logout\n")
            except OSError:
                pass
            return out.decode("utf-8", "replace")
    except OSError:
        return None

def _mpv_ipc_query(target, command_list, timeout=1.5):
    ipc_path = _mpv_ipc_path(target)
    if not os.path.exists(ipc_path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as so:
            so.settimeout(timeout)
            so.connect(ipc_path)
            so.sendall((json.dumps({"command": command_list, "request_id": 77}) + "\n").encode())
            buf = b""
            t_end = time.time() + timeout
            while time.time() < t_end:
                buf += so.recv(4096)
                for ln in buf.split(b"\n"):
                    try:
                        d = json.loads(ln.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if d.get("request_id") == 77:
                        return d.get("data") if d.get("error") == "success" else None
    except (OSError, ValueError):
        return None
    return None

def _fw_frames(target, player):
    """Anzahl bisher angezeigter Bilder oder None (nicht lesbar)."""
    if player == "vlc":
        port = _player_telnet_port(target)
        if port is None:
            return None
        txt = _vlc_telnet_query(port, "stats")
        if not txt:
            return None                                  # Telnet nicht erreichbar -> Sache der Player-Ueberwachung
        m = re.search(r"(?:frames displayed|displayed frames|displayed)\s*:\s*(\d+)", txt, re.I)
        if m:
            return int(m.group(1))
        # keine Statistik: VLC-Fenster offen, aber gestoppt/Eingang beendet -> spielt nichts ab (zaehlt als "steht")
        st = _vlc_telnet_query(port, "status") or ""
        if re.search(r"state\s+(stopped|paused|ended)", st, re.I) or "new input" not in st:
            return 0
        return None
    if player == "mpv":
        v = _mpv_ipc_query(target, ["get_property", "estimated-frame-number"])
        if v is None:
            v = _mpv_ipc_query(target, ["get_property", "time-pos"])
            return None if v is None else int(float(v) * 10)
        return int(v)
    return None

def _fw_stop_play(target, player):
    label = _TARGET_LABEL.get(target, target)
    if player == "vlc":
        port = _player_telnet_port(target)
        _vlc_telnet_send(port, "stop", label)
        root.after(800, lambda: _vlc_telnet_send(port, "play", label))
    elif player == "mpv":
        path = _mpv_ipc_query(target, ["get_property", "path"])
        if path:
            _mpv_ipc_query(target, ["loadfile", path, "replace"])

NOSIG_CLOSE_S = 30       # s ohne Lock (Tuner meldet kein Signal) -> Player schliessen (nur mit AutoClose an)
_fw_nosig_closed = set() # Ziele, deren Player wegen "kein Signal" geschlossen wurde
_fw_nolock_since = {}    # target -> Zeitpunkt, seit dem der Tuner keinen Lock hat

def _fw_locked(target):
    """True = Tuner hat Lock, False = Tuner meldet kein Signal, None = unbekannt (kein Status)."""
    if target in _LM_TARGETS:
        last = _lm_last_packet_time.get(target, 0.0)
        if not last or time.time() - last > 5.0:
            return None
        with _lm_status_lock:
            v = (_lm_status_vals.get(target) or {}).get(1)
        try:
            return int(v) >= 3          # 3/4 = locked DVB-S/S2
        except (TypeError, ValueError):
            return None
    if target == "octagon":
        res = _octagon_poll_result[0]
        t0 = res[0] if res else None
        if not isinstance(t0, dict) or t0.get("lock") is None:
            return None
        return bool(t0.get("lock"))
    if target in _ONAIR_TARGETS:
        tel = (_onair_st.get(target) or {}).get("tel")
        if not isinstance(tel, dict):
            return None
        return bool(tel.get("locked"))
    if target in _DATVRX_TARGETS:
        st = _dvx_st.get(target) or {}
        if not st.get("proc") or time.time() - st.get("t_status", 0) > 4.0:
            return None if st.get("proc") else False
        return bool(st.get("lock"))
    return None

def _no_live_signal(target):
    """True: der Tuner steht im QO-100-Schmalband (10490-10500 MHz) auf einer Frequenz, auf der der BATC-Server gerade KEIN Signal meldet
    (die Bake ist ausgenommen) - ein "Lock" dort ist Rauschen (Scheinlock), das Bild steht. Nur gueltig, solange die BATC-Daten frisch sind;
    sonst (unbekannt) False, damit bei BATC-Ausfall keine Player geschlossen werden."""
    f = _target_cur_freq.get(target)
    if f is None or not (10490.0 <= f <= 10500.0) or abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
        return False
    if not _batc_last_msg[0] or time.time() - _batc_last_msg[0] > 15:
        return False
    return _callsign_signal_gone(target)

def _fw_nosig(target, locked):
    """AutoClose-Erweiterung: Tuner ohne Signal -> Player schliessen, bei Lock wieder oeffnen.
    'Ohne Signal' = Tuner meldet keinen Lock ODER er steht auf einer Frequenz ohne lebendes BATC-Signal (Scheinlock auf Rauschen).
    Player ohne UDP-Stream (z.B. Octagon ueber HTTP) werden nie angefasst."""
    if not app_config.get("autoclose", False):
        _fw_nosig_closed.discard(target)
        _fw_nolock_since.pop(target, None)
        return
    if not _autoclose_endpoint(target):
        return                                        # kein UDP-Stream: nicht pruefbar, Player nicht anfassen
    label = _TARGET_LABEL.get(target, target)
    now = time.time()
    if locked is True and _no_live_signal(target):
        locked = False
    if locked is True:
        _fw_nolock_since.pop(target, None)
        if target in _fw_nosig_closed:
            _fw_nosig_closed.discard(target)
            _autoclose_closed.discard(target)
            olog("AutoClose %s: Tuner hat wieder Lock - Player wird geoeffnet." % label)
            root.after(0, lambda tt=target: (_player_wanted.__setitem__(tt, True),
                                             _start_player(tt, _player_active.get(tt) or "vlc")))
        return
    if locked is not False or _scan_active.get(target) or target in _fw_nosig_closed or _max["t"] == target:
        return
    since = _fw_nolock_since.setdefault(target, now)
    since = max(since, _target_dispatch_t.get(target, 0.0))      # nach dem Umschalten neu zaehlen
    if now - since < NOSIG_CLOSE_S or not _autoclose_player_running(target):
        return
    try:
        _layout_autosave_tick(force=True)
    except Exception as e:
        dlog("AutoClose: Fensterlage merken: %r" % (e,))
    _fw_nosig_closed.add(target)
    _autoclose_closed.add(target)
    _fw_state.pop(target, None)
    olog("AutoClose %s: Tuner seit %.0f s ohne Signal - Fensterlage gemerkt, Player wird gestoppt." % (label, now - since))
    root.after(0, _stop_player, target)

_fw_res = {}      # target -> (Breite, Hoehe) zuletzt gelesene Bildgroesse
_fw_res_t = {}    # target -> Zeitpunkt der letzten Abfrage

def _vlc_video_res(target):
    """Bildgroesse des laufenden VLC (Telnet 'info'), deutsch oder englisch beschriftet; None wenn unbekannt."""
    port = _player_telnet_port(target)
    if port is None:
        return None
    txt = _vlc_telnet_query(port, "info", wait=0.8) or ""
    m = re.search(r"(?:Video resolution|Videoaufl\S*sung|Aufl\S*sung|Resolution)\s*:\s*(\d+)\s*x\s*(\d+)", txt, re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None

def _fw_res_check(target, player):
    """Alle 10 s: Bildgroesse lesen; bei neuer Bildgroesse melden und VLC neu starten (Option), weil VLC nach einem
    Wechsel der Aufloesung im laufenden Strom das Bild manchmal nur noch in Originalgroesse (klein) zeigt."""
    if player != "vlc" or time.time() - _fw_res_t.get(target, 0.0) < 10.0:
        return
    _fw_res_t[target] = time.time()
    res = _vlc_video_res(target)
    if not res:
        return
    old = _fw_res.get(target)
    _fw_res[target] = res
    if old == res:
        return
    label = _TARGET_LABEL.get(target, target)
    if old is None:
        root.after(0, olog, "%s: Bildgroesse %dx%d." % (label, res[0], res[1]))
        return
    restart = app_config.get("vlc_restart_on_res", False)
    root.after(0, olog, "%s: Bildgroesse geaendert %dx%d -> %dx%d%s" % (
        label, old[0], old[1], res[0], res[1], " - VLC wird neu gestartet (Bild wieder fensterfuellend)." if restart else "."))
    if restart:
        root.after(0, _player_restart, target, "vlc", "neue Bildgroesse %dx%d" % res)

def _fw_check(target):
    if _einfach["on"]:
        return
    locked = _fw_locked(target)
    _fw_nosig(target, locked)
    if locked is True and app_config.get("autoclose", False) and _no_live_signal(target):
        locked = False                      # Scheinlock ohne lebendes Signal: kein Bild-Waechter (Stop/Play/Neustart), AutoClose schliesst den Player
    if locked is not True:
        # ohne Lock (oder ohne Tuner-Status) steht das Bild natuerlich - kein Stop/Play/Neustart
        _fw_state.pop(target, None)
        return
    player = _player_active.get(target)
    if player not in ("vlc", "mpv") or not _player_wanted.get(target) or _player_paused.get(target):
        _fw_state.pop(target, None)
        return
    now = time.time()
    st = _fw_state.setdefault(target, {"cnt": None, "chg": now, "stage": 0, "stage_t": 0.0})
    # direkt nach Start/Neustart/Umschalten keine Bewertung (Player synchronisiert sich noch)
    if (now - _player_started_at.get(target, 0.0) < 15 or now - _player_last_restart.get(target, 0.0) < 15
            or now - _target_dispatch_t.get(target, 0.0) < 15 or _scan_active.get(target)):
        st["chg"] = now
        st["cnt"] = None
        return
    try:
        _fw_res_check(target, player)
    except Exception as e:
        dlog("Bildgroesse %s: %r" % (target, e))
    c = _fw_frames(target, player)
    if c is None:
        return
    label = _TARGET_LABEL.get(target, target)
    if st["cnt"] is None or c < st["cnt"]:          # erster Wert oder Zaehler nach Stop/Play zurueckgesetzt
        st["cnt"] = c
        st["chg"] = now
        return
    if c > st["cnt"]:
        st["cnt"] = c
        st["chg"] = now
        if st["stage"]:
            olog("%s: Bild-Waechter - %s zeigt wieder Bilder." % (label, player.upper()))
            st["stage"] = 0
        return
    frozen = now - st["chg"]
    if frozen < FRAMEWATCH_FROZEN_S:
        return
    ep = _autoclose_endpoint(target)
    if not ep or _stream_has_data(ep[0], ep[1], wait=2.0) is not True:
        return                                        # kein Stream -> kein Player-Problem
    if st["stage"] == 0:
        olog("%s: Bild-Waechter - %s-Bild steht seit %.0f s, Stream %s:%s liefert Daten -> Stop/Play." %
             (label, player.upper(), frozen, ep[0], ep[1]))
        root.after(0, _fw_stop_play, target, player)
        st.update(stage=1, stage_t=now, chg=now, cnt=None)
    elif st["stage"] == 1:
        olog("%s: Bild-Waechter - Stop/Play hat nicht geholfen -> %s wird neu gestartet." % (label, player.upper()))
        root.after(0, _player_restart, target, player, "Bild steht (Bild-Waechter)")
        st.update(stage=2, stage_t=now, chg=now, cnt=None)
    elif now - st["stage_t"] >= 60:
        olog("%s: Bild-Waechter - Bild steht auch nach dem Neustart, neuer Versuch." % label)
        st.update(stage=0, chg=now, cnt=None)

#----- Bake stumm: steht ein Tuner auf der Bake, wird sein Player stummgeschaltet; beim Wechsel auf ein anderes
#      Signal wieder Ton an (nur wenn diese Funktion selbst stummgeschaltet hat und 'Mute all' nicht aktiv ist) -----
_bm_sent = {}      # target -> (stumm?, Player-Startzeit) zuletzt gesendet
_bm_muted = set()  # von der Bake-Funktion stummgeschaltete Ziele

def _bm_is_beacon(target):
    f = _target_cur_freq.get(target)
    return f is not None and abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5

def _bm_send(target, mute):
    player = _player_active.get(target)
    label = _TARGET_LABEL.get(target, target)
    if player == "vlc":
        port = _player_telnet_port(target)
        if port is None or not _is_port_open("127.0.0.1", port, timeout=0.4):
            return False
        _vlc_telnet_send(port, "volume 0" if mute else "volume 256", label)
        return True
    if player == "mpv":
        if not os.path.exists(_mpv_ipc_path(target)):
            return False
        _mpv_ipc_query(target, ["set_property", "mute", bool(mute)])
        return True
    return False

#----- Solo-Ton: Rechtsklick auf ein Player-Fenster (oder das Rufzeichenfeld der Spalte) -> nur dieser Player
#      spielt Ton, alle anderen stumm. Nochmal Rechtsklick auf denselben -> alle wieder mit Ton. -----
_solo = {"t": None, "sent": {}}    # sent: target -> (stumm?, Player-Startzeit) zuletzt gesendet

def _solo_set(t):
    if t not in _active_targets:
        return
    label = _TARGET_LABEL.get(t, t)
    if _solo["t"] == t:
        _solo["t"] = None
        olog("Solo-Ton aus - alle Player wieder mit Ton (ausser Bake/Mute all).")
    else:
        _solo["t"] = t
        olog("Solo-Ton: nur %s spielt Ton, alle anderen Player stumm (Rechtsklick nochmal = aus)." % label)
    _solo["sent"].clear()
    threading.Thread(target=lambda: [_solo_tick(x) for x in list(_active_targets)], daemon=True).start()

def _solo_tick(target):
    """Hintergrund: Solo-Zustand auf diesen Player anwenden (auch nach einem Player-Neustart)."""
    if _player_active.get(target) not in ("vlc", "mpv"):
        return
    solo = _solo["t"]
    if solo is None:
        if target in _solo["sent"]:
            # Solo beendet: Ton wieder an, ausser Bake-Stumm oder Mute all gelten
            if not _vlc_muted and not (app_config.get("beacon_mute", True) and _bm_is_beacon(target)):
                if _bm_send(target, False):
                    _solo["sent"].pop(target, None)
            else:
                _solo["sent"].pop(target, None)
        return
    mute = (target != solo)
    key = (mute, _player_started_at.get(target, 0.0))
    if _solo["sent"].get(target) == key or time.time() - _player_started_at.get(target, 0.0) < 3.0:
        return
    if _bm_send(target, mute):
        _solo["sent"][target] = key

def _solo_pointer_check(kind="solo"):
    """Hintergrund: welches Player-Fenster liegt unter dem Mauszeiger? -> Solo (rechts) bzw. Maximieren (Mausrad)."""
    try:
        x, y = _solo_ptr[0]
        wins = _wm_windows() or []
        for t in _active_targets:
            w = _player_window(t, wins)
            if w and w["x"] <= x < w["x"] + w["w"] and w["y"] - 40 <= y < w["y"] + w["h"]:
                if kind == "max":
                    _max_toggle(t, w)
                else:
                    root.after(0, _solo_set, t)
                return
    except Exception as e:
        dlog("Solo (Zeiger): %r" % (e,))

_solo_ptr = [(0, 0)]

#----- Mausrad-Klick auf einen Player: maximieren + Solo-Ton; bleibt offen (kein AutoClose, kein Kacheln),
#      bis wieder Mausrad-Klick darauf oder das Fenster von Hand geschlossen wird. -----
_max = {"t": None, "id": None}

def _max_toggle(t, w):
    """Hintergrund-Thread."""
    wm = shutil.which("wmctrl")
    if not wm:
        return
    label = _TARGET_LABEL.get(t, t)
    def run(*a):
        try:
            subprocess.run([wm] + list(a), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if _max["t"] == t:
        run("-ir", w["id"], "-b", "remove,above")
        run("-ir", w["id"], "-b", "remove,maximized_vert,maximized_horz")
        _max.update(t=None, id=None)
        root.after(0, olog, "%s: Maximierung aufgehoben - Kacheln/AutoClose wieder normal." % label)
        if _solo["t"] == t:
            root.after(0, _solo_set, t)                 # Solo aus
        _tile_sig[0] = None
        _tile_players(force=True)
        return
    if _max["t"] and _max["id"]:
        run("-ir", _max["id"], "-b", "remove,above")
        run("-ir", _max["id"], "-b", "remove,maximized_vert,maximized_horz")
    _max.update(t=t, id=w["id"])
    run("-ir", w["id"], "-b", "add,maximized_vert,maximized_horz")
    run("-ir", w["id"], "-b", "add,above")      # immer im Vordergrund: neu geoeffnete/umsortierte Player blitzen nicht davor auf
    _raise_window(w["id"])
    root.after(0, olog, "%s: maximiert, nur dieser Player mit Ton - bleibt offen bis Mausrad-Klick oder Schliessen." % label)
    if _solo["t"] != t:
        root.after(0, _solo_set, t)

def _max_check():
    """Im Takt: wurde das maximierte Fenster von Hand geschlossen? Dann Zustand + Solo zuruecksetzen."""
    t = _max["t"]
    if not t:
        return
    wins = _wm_windows() or []
    if not any(w["id"] == _max["id"] for w in wins):
        _max.update(t=None, id=None)
        root.after(0, olog, "%s: maximiertes Fenster geschlossen - Solo-Ton aus, normale Anordnung." % _TARGET_LABEL.get(t, t))
        if _solo["t"] == t:
            root.after(0, _solo_set, t)
        _tile_sig[0] = None

def _solo_listener():
    """Rechtsklicks systemweit mitlesen (xinput test-xi2 --root, ohne die Klicks abzufangen)."""
    xi = shutil.which("xinput")
    if not xi:
        root.after(0, olog, "Solo-Ton: 'xinput' fehlt (sudo apt install xinput) - Rechtsklick nur im Rufzeichenfeld der GUI.")
        return
    while True:
        try:
            p = subprocess.Popen([xi, "test-xi2", "--root"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, bufsize=1)
            press = False
            for ln in p.stdout:
                if "RawButtonPress" in ln:
                    press = True
                elif press and "detail:" in ln:
                    press = False
                    btn = ln.split("detail:")[1].strip().split()[0] if ln.split("detail:")[1].strip() else ""
                    if btn == "3" and app_config.get("solo_rightclick", True):
                        root.after(0, _solo_capture_pointer)
                    elif btn == "2" and app_config.get("solo_rightclick", True):
                        root.after(0, _solo_capture_pointer, "max")
                elif "EVENT type" in ln:
                    press = False
        except Exception as e:
            dlog("Solo-Listener: %r" % (e,))
        time.sleep(5)

def _solo_capture_pointer(kind="solo"):
    try:
        _solo_ptr[0] = root.winfo_pointerxy()
    except tk.TclError:
        return
    threading.Thread(target=_solo_pointer_check, args=(kind,), daemon=True).start()

def _bm_tick(target):
    if _solo["t"] is not None:
        _solo_tick(target)          # Solo-Ton hat Vorrang vor "Bake stumm"
        return
    if target in _solo["sent"]:
        _solo_tick(target)
    if not app_config.get("beacon_mute", True):
        if target in _bm_muted and not _vlc_muted:
            if _bm_send(target, False):
                _bm_muted.discard(target)
        _bm_sent.pop(target, None)
        return
    want = _bm_is_beacon(target)
    if not want and target not in _bm_muted:
        _bm_sent.pop(target, None)
        return
    if not want and _vlc_muted:
        return                                    # 'Mute all' aktiv: Ton bleibt aus
    key = (want, _player_started_at.get(target, 0.0))
    if _bm_sent.get(target) == key:
        return                                    # schon gesendet (neu senden nach Player-Neustart)
    if time.time() - _player_started_at.get(target, 0.0) < 3.0:
        return                                    # Player startet noch
    if _bm_send(target, want):
        _bm_sent[target] = key
        label = _TARGET_LABEL.get(target, target)
        if want:
            _bm_muted.add(target)
            olog("%s: steht auf der Bake - Player stummgeschaltet." % label)
        else:
            _bm_muted.discard(target)
            olog("%s: Bake verlassen - Ton wieder an." % label)

def _framewatch_loop():
    time.sleep(10)
    while True:
        try:
            try:
                _max_check()
            except Exception as e:
                dlog("Maximieren: %r" % (e,))
            for t in list(_active_targets):
                try:
                    _bm_tick(t)
                except Exception as e:
                    dlog("Bake stumm (%s): %r" % (t, e))
                if t == "octagon" and not _oct_direct():
                    continue
                if app_config.get("frame_watch", True):
                    _fw_check(t)
                else:
                    _fw_nosig(t, _fw_locked(t))
        except Exception as e:
            dlog("Bild-Waechter: %r" % (e,))
        time.sleep(FRAMEWATCH_PERIOD_S)

_window_dlg = {"win": None}

def _window_dialog():
    """Konfig -> Fenster-Anordnung...: Monitor-Info, 'Jetzt anordnen', Schemata speichern/laden/loeschen."""
    if _window_dlg["win"] is not None:
        try:
            _window_dlg["win"].lift()
            return
        except tk.TclError:
            _window_dlg["win"] = None
    win = tk.Toplevel(root)
    _window_dlg["win"] = win
    win.title("Fenster-Anordnung (Player)")
    win.resizable(False, False)
    def _closed():
        _window_dlg["win"] = None
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", _closed)
    pad = {"padx": 10}

    info_var = tk.StringVar()
    def refresh_info():
        mons, large, three_ok, right = _monitor_summary()
        _wm_mon_cache["val"] = (mons, large, three_ok, right); _wm_mon_cache["t"] = time.time()
        lines = ["Monitor %d: %s  %dx%d  +%d+%d%s" % (mons.index(m) + 1, m["name"], m["w"], m["h"], m["x"], m["y"],
                                          "   <- Ziel" if right is m else "") for m in mons]
        head = ("%d Monitore erkannt, davon %d gross - Bedingung 'drei Monitore (2 grosse)' %s." %
                (len(mons), len(large), "erfuellt" if three_ok else "NICHT erfuellt (Automatik pausiert)"))
        if not shutil.which("wmctrl"):
            head += "\nwmctrl fehlt: sudo apt install wmctrl"
        info_var.set(head + "\n" + "\n".join(lines))
    ttk.Label(win, textvariable=info_var, justify="left").grid(row=0, column=0, columnspan=4, sticky="w", pady=(10, 4), **pad)
    ttk.Button(win, text="Monitore neu pruefen", command=refresh_info).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8), **pad)
    ttk.Label(win, text="Ziel-Monitor (Nr./Name, leer = erster):").grid(row=1, column=2, sticky="e", pady=(0, 8))
    mon_var = tk.StringVar(value=str(app_config.get("window_monitor", 3)))
    mon_ent = ttk.Entry(win, textvariable=mon_var, width=8)
    mon_ent.grid(row=1, column=3, sticky="w", pady=(0, 8), padx=(4, 10))
    def save_mon(*_a):
        app_config["window_monitor"] = mon_var.get().strip()
        app_config_save(); refresh_info()
    mon_ent.bind("<Return>", save_mon); mon_ent.bind("<FocusOut>", save_mon)

    def run_bg(fn):
        def work():
            moved, msg = fn()
            root.after(0, lambda: olog("Fenster-Anordnung: " + msg))
        threading.Thread(target=work, daemon=True).start()

    ttk.Button(win, text="Jetzt anordnen (Ziel-Monitor, nebeneinander, links oben)",
               command=lambda: run_bg(lambda: _arrange_players(None))
               ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10), **pad)

    def show_windows():
        def work():
            wins = _wm_windows() or []
            mons, _l, _t, right = _monitor_summary()
            lines = ["Monitore:"] + ["  %d: %s %dx%d+%d+%d%s" % (mons.index(m) + 1, m["name"], m["w"], m["h"], m["x"], m["y"],
                                                              "  <- Ziel" if m is right else "") for m in mons]
            lines.append("Player-Fenster (nur vlc/mpv/ffplay der Tuner):")
            found = 0
            for t in _active_targets:
                w = _player_window(t, wins)
                if w:
                    found += 1
                    where = next((mons.index(m) + 1 for m in mons if m["x"] <= w["x"] < m["x"] + m["w"]
                                  and m["y"] <= w["y"] < m["y"] + m["h"]), "?")
                    lines.append("  %-12s PID %-7d %dx%d+%d+%d  auf Monitor %s  '%s'" %
                                 (_TARGET_LABEL.get(t, t), w["pid"], w["w"], w["h"], w["x"], w["y"], where, w["title"][:40]))
            if not found:
                lines.append("  (keine gefunden - Player gestartet? wmctrl installiert?)")
            for l in lines:
                print(l, flush=True)
            root.after(0, lambda: olog("Fenster-Anordnung: %d Player-Fenster, Details im Terminal." % found))
        threading.Thread(target=work, daemon=True).start()
    ttk.Button(win, text="Alle Player in den Vordergrund",
               command=lambda: run_bg(lambda: (_raise_players(), "Player-Fenster in den Vordergrund geholt."))
               ).grid(row=10, column=0, columnspan=4, sticky="ew", pady=(0, 10), **pad)
    ttk.Button(win, text="Fensterlage anzeigen (Terminal)", command=show_windows
               ).grid(row=2, column=2, columnspan=2, sticky="ew", pady=(0, 10), padx=(0, 10))

    ttk.Separator(win, orient="horizontal").grid(row=3, column=0, columnspan=4, sticky="ew", pady=4)
    ttk.Label(win, text="Schema:").grid(row=4, column=0, sticky="w", **pad)
    scheme_var = tk.StringVar()
    cmb = ttk.Combobox(win, textvariable=scheme_var, width=28)
    cmb.grid(row=4, column=1, columnspan=3, sticky="w", pady=2, padx=(0, 10))
    def names():
        return sorted((app_config.get("window_schemes") or {}).keys())
    def refresh_names():
        cmb["values"] = names()
        auto_cmb["values"] = ["(Raster Ziel-Monitor)"] + names()
    def save_scheme():
        name = scheme_var.get().strip()
        if not name:
            olog("Fenster-Anordnung: bitte einen Schema-Namen eingeben."); return
        layout = _capture_layout()
        if not layout:
            olog("Fenster-Anordnung: keine Player-Fenster gefunden (wmctrl/Player gestartet?) - nichts gespeichert."); return
        schemes = dict(app_config.get("window_schemes") or {})
        schemes[name] = {"targets": layout, "main": _capture_main(), "monitor_count": _mon_count(),
                         "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
        app_config["window_schemes"] = schemes
        app_config_save(); refresh_names()
        olog("Fenster-Anordnung: Schema '%s' gespeichert (%s%s)." % (name, ", ".join(_TARGET_LABEL.get(t, t) for t in layout),
                                                                   ", Hauptfenster" if schemes[name].get("main") else ""))
    def load_scheme():
        name = scheme_var.get().strip()
        _full = (app_config.get("window_schemes") or {}).get(name) or {}
        sc = _full.get("targets")
        if not sc:
            olog("Fenster-Anordnung: Schema '%s' nicht gefunden." % name); return
        _mg = _full.get("main")
        run_bg(lambda: _arrange_players(sc, main_geo=_mg))
    def delete_scheme():
        name = scheme_var.get().strip()
        schemes = dict(app_config.get("window_schemes") or {})
        if name in schemes:
            del schemes[name]; app_config["window_schemes"] = schemes
            if app_config.get("window_auto_scheme") == name:
                app_config["window_auto_scheme"] = ""
            app_config_save(); refresh_names(); scheme_var.set("")
            olog("Fenster-Anordnung: Schema '%s' geloescht." % name)
    ttk.Button(win, text="Speichern", command=save_scheme).grid(row=5, column=1, sticky="ew", pady=(2, 8))
    ttk.Button(win, text="Laden", command=load_scheme).grid(row=5, column=2, sticky="ew", pady=(2, 8), padx=4)
    ttk.Button(win, text="Loeschen", command=delete_scheme).grid(row=5, column=3, sticky="ew", pady=(2, 8), padx=(0, 10))

    ttk.Separator(win, orient="horizontal").grid(row=6, column=0, columnspan=4, sticky="ew", pady=4)
    auto_var = tk.BooleanVar(value=bool(app_config.get("window_auto_arrange", True)))
    def save_auto():
        app_config["window_auto_arrange"] = bool(auto_var.get())
        sel = auto_cmb.get()
        app_config["window_auto_scheme"] = "" if sel in ("", "(Raster Ziel-Monitor)") else sel
        app_config_save()
    ttk.Checkbutton(win, text="Neue Player-Fenster automatisch anordnen (nur bei 3 Monitoren)",
                    variable=auto_var, command=save_auto).grid(row=7, column=0, columnspan=4, sticky="w", **pad)
    lay_var = tk.BooleanVar(value=bool(app_config.get("window_layout_auto", True)))
    def save_lay():
        app_config["window_layout_auto"] = bool(lay_var.get())
        app_config_save()
    ttk.Checkbutton(win, text="Fensterlagen je Monitoranzahl selbst merken, beim Start pruefen\n"
                              "(gespeichert als 'System 1 Monitor', 'System 2 Monitore', ...)",
                    variable=lay_var, command=save_lay).grid(row=9, column=0, columnspan=4, sticky="w", pady=(0, 10), **pad)
    ttk.Label(win, text="Automatik verwendet:").grid(row=8, column=0, sticky="w", pady=(4, 10), **pad)
    auto_cmb = ttk.Combobox(win, width=28, state="readonly")
    auto_cmb.grid(row=8, column=1, columnspan=3, sticky="w", pady=(4, 10), padx=(0, 10))
    auto_cmb.bind("<<ComboboxSelected>>", lambda e: save_auto())
    refresh_names()
    auto_cmb.set(app_config.get("window_auto_scheme") or "(Raster Ziel-Monitor)")
    refresh_info()

def _toggle_rtlsdr_cfg_window():
    if frm_rtlsdr_cfg.state() == "withdrawn":
        frm_rtlsdr_cfg.deiconify()
        frm_rtlsdr_cfg.lift()
    else:
        frm_rtlsdr_cfg.withdraw()

frm_freq_row  = ttk.Frame (frm_rtlsdr_cfg)
lbl_frequency = ttk.Label (frm_freq_row, text="Frequency (ZF)")
cmb_frequency = ttk.Combobox (frm_freq_row, width=13, textvariable=zf_freq)

def _refresh_zf_presets(*_args):
    """Presets sind als Downlink-MHz hinterlegt - in der jetzt ZF-basierten Combobox werden sie
    passend zum aktuellen LNB-LO in ZF umgerechnet angezeigt (Auswahl liefert exakt wieder die
    beabsichtigte Downlink-Frequenz, unabhaengig vom aktuellen LO-Wert)."""
    try:
        lo = float(lnblo.get())
    except (ValueError, tk.TclError):
        return
    cmb_frequency["values"] = tuple("%.3f" % (float(p) - lo) for p in _DOWNLINK_PRESETS_MHZ)

_refresh_zf_presets()
lnblo.trace_add("write", _refresh_zf_presets)

downlink_display_var = tk.StringVar(value="")   # formatierte Downlink-Anzeige (aus zf_freq + lnblo)

def _refresh_downlink_display(*_args):
    try:
        downlink_display_var.set("%.3f MHz" % float(frequency.get()))
    except (ValueError, tk.TclError):
        downlink_display_var.set("")

_refresh_downlink_display()
frequency.trace_add("write", _refresh_downlink_display)

# Eigene, gepackte Zeile (statt geteilter Grid-Spalten) - so bleibt "MHz" IMMER direkt neben
# der ZF-Combobox, unabhaengig davon, wie breit z.B. die QO100-Combobox darunter die Spalten
# des frm_rtlsdr_cfg-Grids aufspannt.
lb2_frequency = ttk.Label (frm_freq_row, text="MHz", foreground='#1a5fb4')
lbl_downlink  = ttk.Label (frm_freq_row, textvariable=downlink_display_var, foreground='#1a5fb4')
chk_freq48    = ttk.Checkbutton (frm_freq_row, text="+48 kHz", variable=freq_offset_48k)
frm_freq_row.grid (row=0, column=0, columnspan=3, sticky='W')
lbl_frequency.pack (side='left', padx=5)
cmb_frequency.pack (side='left')
lb2_frequency.pack (side='left', padx=(4, 0))
lbl_downlink.pack  (side='left', padx=(12, 0))
chk_freq48.pack    (side='left', padx=(12, 0))

lbl_qo100 = ttk.Label (frm_rtlsdr_cfg, text="QO100")
cmb_qo100 = ttk.Combobox (frm_rtlsdr_cfg, width=32, textvariable=qo100, state="readonly")
cmb_qo100 ["values"] = QO100_LABELS
cmb_qo100.bind ("<<ComboboxSelected>>", on_qo100_selected)
lbl_qo100.grid (row=1, column=0, sticky='W', padx=5)
cmb_qo100.grid (row=1, column=1, sticky='W', columnspan=2)

lbl_symbolrate = ttk.Label (frm_rtlsdr_cfg, text="Symbolrate")
cmb_symbolrate = ttk.Combobox (frm_rtlsdr_cfg, width=10, textvariable=symbolrate)
cmb_symbolrate ["values"] = ("33","66","125","150","250","333","400","500","600","750","1000","1500","2000","2083","3000","4000","4340","5000")
lb2_symbolrate = ttk.Label (frm_rtlsdr_cfg, text="kHz")
lbl_symbolrate.grid (row=2, column=0, sticky='W', padx=5)
cmb_symbolrate.grid (row=2, column=1, sticky='W')
lb2_symbolrate.grid (row=2, column=2, sticky='W', padx=5)

lbl_bandwidth = ttk.Label (frm_rtlsdr_cfg, text="Bandwidth")
cmb_bandwidth = ttk.Combobox (frm_rtlsdr_cfg, width=10, textvariable=bandwidth)
cmb_bandwidth ["values"] = ("2400","2000","1000","500")
lb2_bandwidth = ttk.Label (frm_rtlsdr_cfg, text="kHz")
lbl_bandwidth.grid (row=3, column=0, sticky='W', padx=5)
cmb_bandwidth.grid (row=3, column=1, sticky='W')
lb2_bandwidth.grid (row=3, column=2, sticky='W', padx=5)

lbl_tune = ttk.Label (frm_rtlsdr_cfg, text="Tune")
cmb_tune = ttk.Combobox (frm_rtlsdr_cfg, width=10, textvariable=tune)
cmb_tune ["values"] = ("100","500","1000","2000","5000","10000","-100","-500","-1000","-2000","-5000","-10000")
lb2_tune = ttk.Label (frm_rtlsdr_cfg, text="Hz")
lbl_tune.grid (row=4, column=0, sticky='W', padx=5)
cmb_tune.grid (row=4, column=1, sticky='W')
lb2_tune.grid (row=4, column=2, sticky='W', padx=5)

lbl_lnblo = ttk.Label (frm_rtlsdr_cfg, text="LNB LO")
ent_lnblo = ttk.Entry (frm_rtlsdr_cfg, width=10, textvariable=lnblo)
lb2_lnblo = ttk.Label (frm_rtlsdr_cfg, text="MHz")
lbl_lnblo.grid (row=5, column=0, sticky='W')
ent_lnblo.grid (row=5, column=1, sticky='W')
lb2_lnblo.grid (row=5, column=2, sticky='W', padx=5)

lbl_fec = ttk.Label (frm_rtlsdr_cfg, text="FEC")
cmb_fec = ttk.Combobox (frm_rtlsdr_cfg, width=10, textvariable=fec)
cmb_fec ["values"] = FEC_VALUES
lb2_fec = ttk.Label (frm_rtlsdr_cfg, text="Div")
lbl_fec.grid (row=6, column=0, sticky='W', padx=5)
cmb_fec.grid (row=6, column=1, sticky='W')
lb2_fec.grid (row=6, column=2, sticky='W', padx=5)

lbl_gain = ttk.Label (frm_rtlsdr_cfg, text="Gain")
ent_gain_main = ttk.Entry (frm_rtlsdr_cfg, width=10, textvariable=gain)
lb2_gain = ttk.Label (frm_rtlsdr_cfg, text="dB (0 = auto)")
lbl_gain.grid (row=7, column=0, sticky='W', padx=5)
ent_gain_main.grid (row=7, column=1, sticky='W')
lb2_gain.grid (row=7, column=2, sticky='W', padx=5)

lbl_preset = ttk.Label (frm_rtlsdr_cfg, text="Preset")
cmb_preset = ttk.Combobox (frm_rtlsdr_cfg, width=25, textvariable=preset, state="readonly")
cmb_preset ["values"] = sorted(presets.keys())
cmb_preset.bind ("<<ComboboxSelected>>", on_preset_selected)
btn_preset_save   = ttk.Button (frm_rtlsdr_cfg, text='Save',   width=6, command=preset_save)
btn_preset_delete = ttk.Button (frm_rtlsdr_cfg, text='Delete', width=6, command=preset_delete)
lbl_preset.grid        (row=8, column=0, sticky='W', padx=5)
cmb_preset.grid        (row=8, column=1, sticky='W')
btn_preset_save.grid   (row=8, column=2, sticky='W')
btn_preset_delete.grid (row=8, column=3, sticky='W')

lbl_separator = tk.Frame (frm_rtlsdr_cfg, height=1, bg="grey")
lbl_separator.grid (row=9, column=0, sticky='EW', columnspan=6, pady=6)

btn_start = ttk.Button (frm_rtlsdr_cfg, text='START', command=on_start)
btn_start.grid (row=10, column=0)
# Decoder-Wahl (leandvb / datvrx) - unter Start/Stop
_dec_var = tk.StringVar(value=app_config.get("rtl_decoder", "leandvb"))
_dvx_path_var = tk.StringVar(value=app_config.get("datvrx_path", ""))
_dvx_rs_var = tk.BooleanVar(value=bool(app_config.get("datvrx_use_sr", False)))
def _dec_save(*_a):
    app_config["rtl_decoder"] = _dec_var.get()
    app_config["datvrx_path"] = _dvx_path_var.get().strip()
    app_config["datvrx_use_sr"] = bool(_dvx_rs_var.get())
    app_config_save()
    b = _datvrx_bin()
    _dvx_state.set(("gefunden: %s" % b) if b else "datvrx nicht gefunden")
frm_dec = ttk.Frame(frm_rtlsdr_cfg)
frm_dec.grid(row=11, column=0, columnspan=6, sticky='W', padx=5, pady=(6, 2))
ttk.Label(frm_dec, text="Decoder:").pack(side='left')
ttk.Radiobutton(frm_dec, text="leandvb", value="leandvb", variable=_dec_var, command=_dec_save).pack(side='left', padx=(4, 0))
ttk.Radiobutton(frm_dec, text="datvrx (DATVision/OnAir)", value="datvrx", variable=_dec_var, command=_dec_save).pack(side='left', padx=(4, 0))
ttk.Checkbutton(frm_dec, text="Symbolrate vorgeben (--rs)", variable=_dvx_rs_var, command=_dec_save).pack(side='left', padx=(8, 0))
frm_dec2 = ttk.Frame(frm_rtlsdr_cfg)
frm_dec2.grid(row=12, column=0, columnspan=6, sticky='W', padx=5, pady=(0, 4))
ttk.Label(frm_dec2, text="datvrx-Pfad:").pack(side='left')
_ent_dvx = ttk.Entry(frm_dec2, width=40, textvariable=_dvx_path_var)
_ent_dvx.pack(side='left', padx=(4, 0))
_ent_dvx.bind("<FocusOut>", _dec_save)
_ent_dvx.bind("<Return>", _dec_save)
_dvx_state = tk.StringVar(value="")
ttk.Label(frm_dec2, textvariable=_dvx_state, font=('', 8)).pack(side='left', padx=(6, 0))
root.after(1000, _dec_save)

btn_stop = ttk.Button (frm_rtlsdr_cfg, text='STOP', command=on_stop)
btn_stop.grid (row=10, column=1)

def show_status_info():
    rows = [
        ("_", "S2 frame received without errors", "Frame fehlerfrei empfangen - bester Fall"),
        (".", "error-corrected S2 frame", "Frame hatte Fehler, LDPC konnte sie korrigieren"),
        ("C", "TS packet with bad CRC", "TS-Paket mit fehlerhafter Pruefsumme"),
        ("!", "S2 frame with remaining errors", "Frame nicht vollstaendig korrigierbar"),
        (":", "frame corrupted (undokumentiert)", "Nicht rettbar - leansdr/dvbs2.h ~Zeile 2171, meist zu schwaches Signal"),
    ]

    win = tk.Toplevel(root)
    win.title("Status-Zeichen (leandvb / ffplay)")
    win.resizable(False, False)

    ttk.Label(win, text="Waehrend des Empfangs gibt leandvb pro S2-Frame ein Zeichen aus:",
              font=('', 9, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', padx=10, pady=(10, 8))

    mono = ('Courier New', 11, 'bold')
    for i, (ch, en, de) in enumerate(rows, start=1):
        tile = tk.Frame(win, relief='solid', borderwidth=1, background='#e6e6e6')
        tile.grid(row=i, column=0, sticky='w', padx=(10, 6), pady=3)
        tk.Label(tile, text="'%s'" % ch, font=mono, background='#e6e6e6', width=4,
                  anchor='center').pack(padx=6, pady=4)
        ttk.Label(win, text=en).grid(row=i, column=1, sticky='w', padx=(0, 10), pady=3)
        ttk.Label(win, text="(" + de + ")", foreground='#555555').grid(
            row=i, column=2, sticky='w', padx=(0, 10), pady=3)

    ttk.Label(win, text="Viele '_' = sauberer Empfang, viele ':' = Signal zu schwach oder falsches FEC/MODCOD.\n"
                         "Kein Bild in ffplay heisst meist, dass noch keine gueltigen ('_'/'.') Frames ankommen.",
              justify='left').grid(row=len(rows) + 1, column=0, columnspan=3, sticky='w', padx=10, pady=(8, 6))

    ttk.Button(win, text="OK", command=win.destroy).grid(
        row=len(rows) + 2, column=0, columnspan=3, pady=(4, 10))

    win.transient(root)
    win.grab_set()

btn_info = ttk.Button (frm_rtlsdr_cfg, text='Info', command=show_status_info)
btn_info.grid (row=10, column=2)

btn_settings = ttk.Button (frm_rtlsdr_cfg, text='Settings', command=dlg_settings)
btn_settings.grid (row=10, column=3, columnspan=2)

chk_ldpchelper = ttk.Checkbutton (frm_rtlsdr_cfg, text="LDPC-Helper (--ldpc-helper) verwenden", variable=ldpchelper_enable)
chk_ldpchelper.grid (row=11, column=0, columnspan=4, sticky='W', pady=(4, 0))

# Weitere RX-Optionen, untereinander unter dem LDPC-Helper - dieselben Variablen
# wie im Settings-Dialog, also automatisch mit dem aktuell gestarteten Stand befuellt.
chk_fastlock_main = ttk.Checkbutton (frm_rtlsdr_cfg, text="Fastlock (--fastlock)", variable=fastlock)
chk_fastlock_main.grid (row=12, column=0, columnspan=4, sticky='W')

chk_viterbi_main = ttk.Checkbutton (frm_rtlsdr_cfg, text="Viterbi (--viterbi, nur DVB-S)", variable=viterbi)
chk_viterbi_main.grid (row=13, column=0, columnspan=4, sticky='W')

chk_fastdrift_main = ttk.Checkbutton (frm_rtlsdr_cfg, text="Drift (--fastdrift, nur DVB-S2)", variable=fastdrift)
chk_fastdrift_main.grid (row=14, column=0, columnspan=4, sticky='W')

chk_gui_main = ttk.Checkbutton (frm_rtlsdr_cfg, text="GUI (--gui, leandvb-Debugfenster)", variable=gui)
chk_gui_main.grid (row=15, column=0, columnspan=4, sticky='W')

def _on_maxsens_toggle():
    """--hq schaltet laut leandvb selbst Fastlock, Viterbi und den RRC-Sampler ein - beim
    Anhaken hier also dieselben Variablen mitsetzen, damit die GUI den tatsaechlichen Zustand
    zeigt (der RRC-Sampler ist keine eigene Checkbox, sondern die "sampler"-Combobox im
    Settings-Dialog - "rrc" ist dort ohnehin schon der Standardwert)."""
    if maxsens.get():
        fastlock.set(True)
        viterbi.set(True)
        sampler.set("rrc")

chk_maxsens_main = ttk.Checkbutton (frm_rtlsdr_cfg,
    text="Maximale Empfindlichkeit (--hq: Fastlock + Viterbi + RRC-Sampler)",
    variable=maxsens, command=_on_maxsens_toggle)
chk_maxsens_main.grid (row=16, column=0, columnspan=4, sticky='W')

# Service-Verlauf und Bake-Abweichung/LO-Korrektur - je eine eigene, volle Zeile ganz unten im
# Fenster (statt vorher zusammengequetscht in einem schmalen Feld rechts, wo die Bake-Abweichung
# bei laengerem Verlauf in die naechste Zeile umgebrochen ist).
_service_history_var = tk.StringVar(value="Service-Verlauf:")
frm_service_row = ttk.Frame (frm_rtlsdr_cfg)
frm_service_row.grid (row=17, column=0, columnspan=7, sticky='w', pady=(6, 0))
lbl_service_history = tk.Label (frm_service_row, textvariable=_service_history_var,
                                 anchor='w', justify='left',
                                 relief='groove', borderwidth=1,
                                 background="#d9d9d9", foreground="black", padx=6, pady=3)
lbl_service_history.pack (side='left')

_bake_deviation_var = tk.StringVar(value="Bake-Abweichung:")
frm_lo_row = ttk.Frame (frm_rtlsdr_cfg)
frm_lo_row.grid (row=18, column=0, columnspan=7, sticky='w', pady=(2, 4))
lbl_bake_deviation = tk.Label (frm_lo_row, textvariable=_bake_deviation_var,
                                anchor='w', justify='left',
                                relief='groove', borderwidth=1,
                                background="#d9d9d9", foreground="black", padx=6, pady=3)
lbl_bake_deviation.pack (side='left')

lo_autocorrect = tk.BooleanVar(value=False)
chk_lo_autocorrect = ttk.Checkbutton (frm_lo_row, text="LO korrigieren", variable=lo_autocorrect,
                                       command=_lo_autocorrect_toggled)
chk_lo_autocorrect.pack (side='left', padx=(8, 0))


if os.path.isfile("logo.png"):
    img_logo = tk.PhotoImage(file="logo.png")
else:
    img_logo = None
lbl_logo = tk.Label(frm_rtlsdr_cfg, image=img_logo)
lbl_logo.grid (row=0, column=3, sticky='nesw', columnspan=2, rowspan=7, padx=5, pady=5)

#----- BATC QO-100 Signal-Scanner -----
# Jetzt die einzige Spalte in frm_root - die RTL_SDR/leandvb-Konfiguration (vorher links
# daneben) wurde in ein eigenes, zunaechst verstecktes Toplevel-Fenster ausgelagert
# (siehe frm_rtlsdr_cfg/_toggle_rtlsdr_cfg_window oben).
frm_batc = ttk.Frame (frm_root, borderwidth=1, relief='groove', padding=5)
frm_batc.grid (row=0, column=0, sticky='ns')

_batc_mode_parts = []
for _t in _active_targets:
    if _t == "octagon":
        _batc_mode_parts.append("Octagon+VLC (%s)" % cliargs.box)
    elif _t == "longmynd":
        _batc_mode_parts.append("LongMynd (%s)" % cliargs.longmynd_ip)
    elif _t == "longmynd2":
        _batc_mode_parts.append("LongMynd 2 (%s:%d)" % _longmynd2_endpoint())
    elif _t == "longmynd_p4":
        _batc_mode_parts.append("LongMynd P4 (%s)" % cliargs.longmyndp4_ssh_host)
    elif _t in _UDPRX_TARGETS:
        _batc_mode_parts.append("%s UDP %s:%d" % ((_TARGET_LABEL[_t],) + _rx_udp(_t)))
    else:
        _batc_mode_parts.append("lokaler rtl_sdr")
_batc_mode_txt = "Aktiv: " + "  +  ".join(_batc_mode_parts)

frm_batc_titlerow = ttk.Frame (frm_batc)
frm_batc_titlerow.pack (anchor='w', fill='x')
lbl_batc_title  = ttk.Label (frm_batc_titlerow, text="BATC QO-100 Signale", font=('', 10, 'bold'))
lbl_batc_title.pack (side='left', anchor='w')
batc_auto_master = tk.BooleanVar(value=bool(parameters.get("batc_auto_master", True)))
ttk.Style().configure("Small.TCheckbutton", font=('', 8))
chk_batc_auto_master = ttk.Checkbutton (frm_batc_titlerow, text="Verteil Automatik", variable=batc_auto_master,
                                         command=parameters_save)
chk_batc_auto_master.pack (side='right', anchor='e')
lbl_lmp4_warn = ttk.Label (frm_batc_titlerow, text="", foreground="#c0392b", font=('', 9, 'bold'))
lbl_lmp4_warn.pack (side='left', anchor='w', padx=(14, 0))

lbl_batc_mode   = ttk.Label (frm_batc, text=_batc_mode_txt, font=('', 9, 'bold'), foreground='#1a5fb4')
frm_batc_connectrow = ttk.Frame (frm_batc)
#----- AutoClose: alle 2 min pruefen, ob auf dem Stream eines Players noch Daten kommen - wenn nicht, die
#      Fensterlage merken und den Player schliessen; kommt der Stream wieder, wird der Player neu geoeffnet -----
AUTOCLOSE_INTERVAL_S = 120
_autoclose_closed = set()     # Ziele, deren Player AutoClose geschlossen hat (fuer das Wiederoeffnen)

def _stream_has_data(ip, port, wait=3.0):
    """True, wenn innerhalb von 'wait' Sekunden mindestens ein UDP-Paket auf ip:port ankommt (Multicast wird
    beigetreten; andere Empfaenger auf demselben Port stoeren nicht - SO_REUSEADDR)."""
    s2 = None
    try:
        s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s2.bind(("", int(port)))
        first = int(str(ip).split(".")[0]) if str(ip)[0].isdigit() else 0
        if 224 <= first <= 239:
            mreq = struct.pack("4s4s", socket.inet_aton(ip), socket.inet_aton("0.0.0.0"))
            s2.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s2.settimeout(wait)
        s2.recvfrom(2048)
        return True
    except socket.timeout:
        return False
    except OSError as e:
        dlog("AutoClose: Pruefung %s:%s nicht moeglich: %s" % (ip, port, e))
        return None          # unbekannt -> nichts tun
    finally:
        if s2 is not None:
            try:
                s2.close()
            except OSError:
                pass

def _autoclose_endpoint(t):
    if t == "octagon":
        return _oct_direct_stream() if _oct_direct() else None    # Octagon ueber HTTP: nicht pruefbar
    g = _TARGET_STREAM.get(t)
    return g() if g else None

def _autoclose_player_running(t):
    proc = _player_proc.get(t)
    if proc is not None and proc.poll() is None:
        return True
    if _player_active.get(t) == "vlc":
        port = _player_telnet_port(t)
        return port is not None and _is_port_open("127.0.0.1", port, timeout=0.4)
    return False

def _autoclose_check():
    """Hintergrund-Thread: alle Ziele mit UDP-Stream pruefen."""
    if _einfach["on"]:
        return
    for t in list(_active_targets):
        ep = _autoclose_endpoint(t)
        if not ep:
            continue
        running = _autoclose_player_running(t)
        if not running and t not in _autoclose_closed:
            continue
        data = _stream_has_data(ep[0], ep[1])
        label = _TARGET_LABEL.get(t, t)
        if data is False and running:
            try:
                _layout_autosave_tick(force=True)      # Fensterlage vor dem Schliessen merken
            except Exception as e:
                dlog("AutoClose: Fensterlage merken: %r" % (e,))
            _autoclose_closed.add(t)
            olog("AutoClose %s: kein Stream auf %s:%s - Fensterlage gemerkt, Player wird geschlossen." % (label, ep[0], ep[1]))
            root.after(0, _stop_player, t)
        elif data is True and t in _autoclose_closed and not running and t not in _fw_nosig_closed:
            _autoclose_closed.discard(t)
            olog("AutoClose %s: Stream auf %s:%s ist wieder da - Player wird geoeffnet." % (label, ep[0], ep[1]))
            root.after(0, lambda tt=t: (_player_wanted.__setitem__(tt, True),
                                        _start_player(tt, _player_active.get(tt) or "vlc")))

def _autoclose_tick():
    if app_config.get("autoclose", False):
        threading.Thread(target=_autoclose_check, daemon=True).start()
    root.after(AUTOCLOSE_INTERVAL_S * 1000, _autoclose_tick)

def _autoclose_toggle():
    app_config["autoclose"] = not bool(app_config.get("autoclose", False))
    app_config_save()
    _autoclose_button_update()
    if app_config["autoclose"]:
        olog("AutoClose ein: alle %d s wird geprueft, ob die Streams der Player aktiv sind - ohne Stream wird das "
             "Fenster (Lage gemerkt) geschlossen und bei Rueckkehr wieder geoeffnet." % AUTOCLOSE_INTERVAL_S)
        threading.Thread(target=_autoclose_check, daemon=True).start()
    else:
        _autoclose_closed.clear()
        olog("AutoClose aus.")

def _autoclose_button_update():
    try:
        btn_batc_autoclose.configure(style="RecOn.TButton" if app_config.get("autoclose", False) else "TButton")
    except (NameError, tk.TclError):
        pass

btn_batc_start       = ttk.Button (frm_batc_connectrow, text="Verbinden", command=batc_start)
btn_batc_refresh     = ttk.Button (frm_batc_connectrow, text="Refresh BATC", command=batc_refresh)
btn_batc_redistribute = ttk.Button (frm_batc_connectrow, text="Neu verteilen", command=batc_redistribute)
btn_batc_redistribute_rest = ttk.Button (frm_batc_connectrow, text="NeuNeuVerteilen", command=batc_redistribute_rest)
btn_batc_mute_all    = ttk.Button (frm_batc_connectrow, text="Mute all", command=vlc_mute_all)
btn_batc_ascan       = ttk.Button (frm_batc_connectrow, text="AScan", command=_ascan_global_toggle)
btn_batc_raise       = ttk.Button (frm_batc_connectrow, text="Fenster nach oben \u2191",
                                   command=lambda: threading.Thread(target=_raise_players, daemon=True).start())
btn_batc_savewin     = ttk.Button (frm_batc_connectrow, text="Fenster speichern",
                                   command=lambda: threading.Thread(target=_layout_save_manual, daemon=True).start())
btn_batc_start.pack        (side='left')
btn_batc_start.update_idletasks()   # reqwidth erst nach dem Packen zuverlaessig bekannt
_gap = ttk.Frame (frm_batc_connectrow, width=btn_batc_start.winfo_reqwidth())
_gap.pack_propagate(False)
_gap.pack (side='left')   # 1 Knopfbreite Luecke
btn_batc_refresh.pack       (side='left')
btn_batc_redistribute.pack  (side='left', padx=(3, 0))
btn_batc_redistribute_rest.pack (side='left', padx=(3, 0))
btn_batc_mute_all.pack      (side='left', padx=(3, 0))
btn_batc_ascan.pack         (side='left', padx=(3, 0))
btn_batc_raise.pack         (side='right')
btn_batc_savewin.pack       (side='left', padx=(3, 0))
btn_batc_autoclose = ttk.Button (frm_batc_connectrow, text="AutoClose", command=_autoclose_toggle)
btn_batc_autoclose.pack     (side='left', padx=(3, 0))
btn_batc_quad = ttk.Button (frm_batc_connectrow, text="Quad", width=6, command=_quad_toggle)
btn_batc_quad.pack          (side='left', padx=(3, 0))
btn_batc_playerneu = ttk.Button (frm_batc_connectrow, text="PlayerNeu", width=9, command=_players_restart_all)      # alle Player schliessen (auch doppelte/alte) und neu starten
btn_batc_playerneu.pack     (side='left', padx=(3, 0))
_anord_var = tk.StringVar(value="kacheln")
def _anord_set():
    _set_mode(_anord_var.get())
def _voll_toggle():
    if _eff_mode(_n_players_now()) == "voll":
        _set_mode(app_config.get("window_mode") if app_config.get("window_mode") in ("oben_h", "unten_h") else "kacheln")
    else:
        _set_mode("voll")
def _autoanord_update():
    on = bool(app_config.get("window_auto_arrange", True))
    try:
        btn_batc_autoanord.configure(text="Auto-Anord: %s" % ("AN" if on else "AUS"), style="RecOn.TButton" if on else "TButton")
    except (NameError, tk.TclError):
        pass
def _autoanord_toggle():
    on = not bool(app_config.get("window_auto_arrange", True))
    app_config["window_auto_arrange"] = on
    app_config_save()
    _autoanord_update()
    olog("Fenster automatisch anordnen: %s." % ("AN - Player-Fenster werden nach dem gemerkten Schema angeordnet" if on else "AUS - die Player-Fenster bleiben, wo sie sind"))
    if on:
        threading.Thread(target=_wm_auto_tick, daemon=True).start()
btn_batc_autoanord = ttk.Button(frm_batc_connectrow, text="Auto-Anord: AN", width=14, command=_autoanord_toggle)
btn_batc_autoanord.pack(side='left', padx=(3, 0))
btn_batc_voll = ttk.Button(frm_batc_connectrow, text="Voll", width=5, command=_voll_toggle)
btn_batc_voll.pack(side='left', padx=(3, 0))
_anord_mb = ttk.Menubutton(frm_batc_connectrow, text="Anordnung \u25be", width=11)
_anord_mb.pack(side='left', padx=(3, 0))
_anord_menu = tk.Menu(_anord_mb, tearoff=False)
_anord_mb["menu"] = _anord_menu
for _lab, _val in (("1  Oben horizontal (eine Reihe, Bildformat)", "oben_h"), ("2  Kacheln (Raster)", "kacheln"),
                   ("3  Quad (Hauptfenster rechts unten)", "quad"), ("4  Unten horizontal", "unten_h")):
    _anord_menu.add_radiobutton(label=_lab, value=_val, variable=_anord_var, command=_anord_set)
_anord_menu.add_separator()
_anord_menu.add_separator()
_mon_menu = tk.Menu(_anord_menu, tearoff=False)
_mon_var = tk.StringVar()
def _mon_set():
    app_config["window_monitor"] = _mon_var.get()
    app_config_save()
    _wm_mon_cache["val"] = None
    olog("Fenster-Anordnung: Bildschirm fuer die Player = %s." % _mon_var.get())
    threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start()
def _mon_menu_fill():
    """Bildschirmliste frisch einlesen (bei jedem Oeffnen des Menues)"""
    mons = _monitors()
    val = str(app_config.get("window_monitor", 3)).strip()
    if not val.isdigit():
        val = next((str(i) for i, m in enumerate(mons, 1) if m["name"].lower() == val.lower()), "1")
    _mon_var.set(val)
    for _mm in (_mon_menu, globals().get("_mon_menu_top")):
        if _mm is None:
            continue
        _mm.delete(0, "end")
        for i, m in enumerate(mons, 1):
            _mm.add_radiobutton(label="%d  %s  %dx%d" % (i, m["name"], m["w"], m["h"]), value=str(i), variable=_mon_var, command=_mon_set)
_anord_menu.add_cascade(label="Bildschirm fuer die Player", menu=_mon_menu)
_anord_menu.add_command(label="Jetzt anordnen", command=lambda: threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start())
_anord_menu.configure(postcommand=_mon_menu_fill)

# eigenes Menue "Anordnung" in der Menueleiste des Hauptfensters (gleiche Schemata wie das Knopf-Menue, dazu Fenstergroesse Strg + / Strg -)
_menu_anord_top = tk.Menu(_menubar, tearoff=False)
for _val in ("oben_h", "kacheln", "quad", "unten_h", "voll"):
    _menu_anord_top.add_radiobutton(label=_MODE_NAMES[_val], value=_val, variable=_anord_var, command=_anord_set)
_menu_anord_top.add_separator()
_menu_anord_top.add_command(label="Alle Player-Fenster groesser   (Strg +)", command=lambda: _window_zoom_step(1))
_menu_anord_top.add_command(label="Alle Player-Fenster kleiner   (Strg -)", command=lambda: _window_zoom_step(-1))
_menu_anord_top.add_command(label="Fenstergroesse zuruecksetzen   (Strg 0)", command=lambda: _window_zoom_step(0))
_menu_anord_top.add_separator()
_menu_anord_top.add_command(label="Jetzt anordnen", command=lambda: threading.Thread(target=lambda: _tile_players(force=True), daemon=True).start())
_mon_menu_top = tk.Menu(_menu_anord_top, tearoff=False)
_menu_anord_top.add_cascade(label="Bildschirm fuer die Player", menu=_mon_menu_top)
_menu_anord_top.configure(postcommand=_mon_menu_fill)
_menubar.insert_cascade(_menubar.index("Statistik"), label="Anordnung", menu=_menu_anord_top)
for _seq, _dd in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1), ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1), ("<Control-0>", 0)):
    root.bind_all(_seq, lambda e, d=_dd: (_window_zoom_step(d), "break")[1])
_ui_mode[0] = _eff_mode(1)
_quad_button_update()
_autoanord_update()
root.after(1500, _autoclose_button_update)
root.after(AUTOCLOSE_INTERVAL_S * 1000, _autoclose_tick)
lbl_batc_status = ttk.Label (frm_batc, text="nicht verbunden" if HAVE_WEBSOCKET else
                              "websocket-client fehlt:\nsudo pip3 install websocket-client --break-system-packages")
lbl_batc_mode.pack       (anchor='w', pady=(0, 4))
frm_batc_connectrow.pack (anchor='w', fill='x', pady=(2, 4))
lbl_batc_status.pack     (anchor='w', pady=(0, 6))

#----- "Save Video" - eine Aufnahmetaste je aktivem Ziel, gruen wenn aktiv -----
_rec_style = ttk.Style()
_rec_style.configure("RecOn.TButton", background="#2ecc71", foreground="black")
_rec_style.map("RecOn.TButton", background=[("active", "#27ae60"), ("!disabled", "#2ecc71")])

# Ampel-Faerbung fuer neue/verschwundene BATC-Signale
_rec_style.configure("SigNew.TButton",  background="#2ecc71", foreground="black")
_rec_style.map("SigNew.TButton",  background=[("active", "#27ae60"), ("!disabled", "#2ecc71")])
_rec_style.configure("SigGone.TButton", background="#e74c3c", foreground="black")
_rec_style.map("SigGone.TButton", background=[("active", "#c0392b"), ("!disabled", "#e74c3c")])

# Blau: dieses Ziel ist gerade (manuell oder per Automatik) auf dieses Signal gestellt
_rec_style.configure("SigAssigned.TButton", background="#3498db", foreground="white")
_rec_style.map("SigAssigned.TButton", background=[("active", "#2980b9"), ("!disabled", "#3498db")])

frm_batc_rec = ttk.Frame (frm_batc)
frm_batc_rec.pack (anchor='w', pady=(0, 2))
_batc_auto_saved = parameters.get("batc_auto", {})
# Jedes Ziel bekommt EINEN eigenen vertikalen Container (_target_col_frames[target]), in dem
# ALLES zu diesem Ziel gehoerige - Kopfzeile, Automatik/Scan, Save Video/Snapshot/QRG, Player-
# Knoepfe, Status-Chips, Rufzeichen UND die Signal-Zuordnungs-Knopfliste (_target_siglist_frames)
# - von oben nach unten gepackt wird (statt vorher zwei getrennte grid()-Raster mit potenziell
# unterschiedlichen Spaltenbreiten, wodurch z.B. LongMynds Signal-Knoepfe optisch unter Octagon
# gelandet sind). So bleibt pro Ziel alles garantiert in einer Spalte untereinander.
_target_col_frames = {}
_target_extra_frames = {}   # target -> weitere Frames der Spalte (Rufzeichen-/Signal-Zeile), fuer Ausgrauen
_target_siglist_frames = {}
def _bc_frames(_col, _target):
    """Spalten-Container und Zeilen-Frames dieses Ziels; liefert (_col_frame, _cs_row_frame) (aus _build_columns ausgelagert)"""
    _col_frame = ttk.Frame(frm_batc_rec)
    _col_frame.grid(row=0, column=_col, sticky='nw', padx=(0 if _col == 0 else 10, 0))
    # Rufzeichen (Zeile 1) und Signal-Knoepfe (Zeile 2) liegen in eigenen Grid-Zeilen von frm_batc_rec - so
    # beginnen sie in allen Spalten auf gleicher Hoehe, auch wenn eine Spalte oben mehr Bedienelemente hat.
    _cs_row_frame = ttk.Frame(frm_batc_rec)
    _cs_row_frame.grid(row=1, column=_col, sticky='new', padx=(0 if _col == 0 else 10, 0))
    _target_extra_frames[_target] = [_cs_row_frame]
    _target_col_frames[_target] = _col_frame
    return _col_frame, _cs_row_frame

def _bc_head_auto(_target, _col_frame):
    """Kopfzeile, Automatik-/Scan-Zeile (OnAir-, LongMynd-, Octagon-, rtl_sdr-Zusatzknoepfe) (aus _build_columns ausgelagert)"""
    global _offset_correct_var, _fec_sync_var   # werden in parameters_save()/on_start() gelesen

    # Kopfzeile (Label + Automatik) jetzt ganz oben, ueber "Save Video".
    _hl = ttk.Label (_col_frame, text=_TARGET_LABEL[_target], font=('', 9, 'bold'))
    _hl.pack(side='top', anchor='w')
    _dvx_head_lbl[_target] = _hl
    _batc_auto[_target] = tk.BooleanVar(value=bool(_batc_auto_saved.get(_target, False)))
    _batc_auto[_target].trace_add("write", lambda *_a: parameters_save())
    _row_auto = ttk.Frame(_col_frame)
    _row_auto.pack(side='top', anchor='w', fill='x', pady=(0, 4))
    ttk.Checkbutton (_row_auto, text="VerteilA", style="Small.TCheckbutton",
                     variable=_batc_auto[_target]).pack(side='left')
    _scan_btn = ttk.Button(_row_auto, text="Scan", width=8, command=lambda t=_target: _scan_target(t))
    _scan_btn.pack(side='left', padx=(6, 0))
    _scan_buttons[_target] = _scan_btn
    _ascan_btn = ttk.Button(_row_auto, text="AScan", width=8, command=lambda t=_target: _toggle_ascan(t))
    _ascan_btn.pack(side='left', padx=(2, 0))
    _ascan_buttons[_target] = _ascan_btn
    if _target in _ONAIR_TARGETS:
        _rec_style.configure("Abgl.TButton", background="#f1c40f", foreground="black")
        _rec_style.map("Abgl.TButton", background=[("active", "#d4ac0d"), ("!disabled", "#f1c40f")])
        _rec_style.configure("AbglRun.TButton", background="#e67e22", foreground="black")
        _rec_style.map("AbglRun.TButton", background=[("active", "#ca6f1e"), ("!disabled", "#e67e22")])
        _ab = ttk.Button(_row_auto, text="Abgl", width=5, style="Abgl.TButton",
                         command=lambda t=_target: _onair_abgleich(t))
        _ab.pack(side='left', padx=(2, 0))
        _onair_abgl_buttons[_target] = _ab
        _ob = ttk.Button(_row_auto, text="Opt", width=4, style="Abgl.TButton",
                         command=lambda t=_target: _onair_optimise(t))
        _ob.pack(side='left', padx=(2, 0))
        _onair_opt_buttons[_target] = _ob
        _agb = ttk.Button(_row_auto, text="AGC", width=6, style="Abgl.TButton",
                          command=lambda t=_target: _onair_agc_toggle(t))
        _agb.pack(side='left', padx=(2, 0))
        _onair_agc_buttons[_target] = _agb
        root.after(600, _onair_agc_button, _target)
        _onair_help_bind(_ab, "abgl")
        _onair_help_bind(_ob, "opt")
        _onair_help_bind(_agb, "agc")
        _abw_var = tk.BooleanVar(value=bool(_ONAIR_RX[_target].get("autobw", False)))
        ttk.Checkbutton(_row_auto, text="ABW", variable=_abw_var,
                        command=lambda t=_target, v=_abw_var: _onair_autobw_toggle(t, v.get())
                        ).pack(side='left', padx=(4, 0))
    if _target in _LM_TARGETS:
        ttk.Button(_row_auto, text="Report", width=8, command=lambda t=_target: _signal_report_copy(t)
                   ).pack(side='left', padx=(2, 0))
    if _target == "octagon":
        # Direktmodus: gilt fuer JEDES Umschalten dieses Ziels - Klick auf ein Signal genauso wie
        # die automatische Verteilung (beide laufen durch target_dispatch, siehe dort).
        _oct_direct_var = tk.BooleanVar(value=_oct_direct())
        _oct_direct_chk = ttk.Checkbutton(_row_auto, text="Direkt", variable=_oct_direct_var,
                                           command=_oct_direct_toggle)
        _oct_direct_chk.pack(side='left', padx=(2, 0))
        _oct_direct_btn[0] = _oct_direct_chk
        _oct_direct_var_ref[0] = _oct_direct_var
        _oct_direct_button_update()

    if _target == "rtl_sdr":
        # Zwei Options-Checkboxen, rechtsbuendig ueber dem "QRG Eingabe"-Knopf (siehe _row_ctrl
        # weiter unten) - beide sind reine Dauereinstellungen (kein Sofort-Effekt beim Anhaken,
        # keine Selbst-Abhakung): sie wirken erst beim naechsten echten Start (siehe on_start()/
        # _apply_offset_correction_if_requested()/_fec_sync_from_longmynd_then_start()) und werden
        # ueber parameters.json ("fec_sync_longmynd"/"offset_correct") ueber Programmstarts hinweg
        # gemerkt, wie alle anderen Einstellungen auch.
        _offset_correct_var = tk.BooleanVar(value=bool(parameters.get("offset_correct", False)))
        _offset_correct_var.trace_add("write", lambda *_a: parameters_save())
        _chk_offset = ttk.Checkbutton(_row_auto, text="Offset korrigieren",
                                       variable=_offset_correct_var)
        _chk_offset.pack(side='right')
        _fec_sync_var = tk.BooleanVar(value=bool(parameters.get("fec_sync_longmynd", False)))
        _fec_sync_var.trace_add("write", lambda *_a: parameters_save())
        _chk_fec_sync = ttk.Checkbutton(_row_auto, text="FEC von LongMynd",
                                         variable=_fec_sync_var)
        _chk_fec_sync.pack(side='right', padx=(0, 6))

def _bc_controls(_target, _col_frame):
    """Save Video/Snapshot/QRG/TSA und Player-Knoepfe (aus _build_columns ausgelagert)"""

    # "Save Video" + Snapshot-Knopf gleich breit nebeneinander, bei allen vier Zielen - jedes
    # laeuft (sobald VLC als Player aktiv ist) ueber ein VLC mit Telnet-Interface, der
    # Snapshot-Knopf loest also ueberall per longmynd_snapshot_telnet(port) aus, nur mit dem
    # jeweils passenden Telnet-Port (4212/4213/--vlc-telnet-port 4214 fuer Octagon/--rtlsdr-vlc-
    # telnet-port fuer rtl_sdr). Ganz rechts, als kleinster Knopf, "QRG Eingabe" - blendet das
    # ausgelagerte RTL_SDR/leandvb-Konfigurationsfenster ein/aus (siehe frm_rtlsdr_cfg), fuer
    # alle Spalten gleichermassen erreichbar, nicht nur bei rtl_sdr.
    _snap_port = {"longmynd": cliargs.longmynd_vnc_port,
                  "longmynd2": cliargs.longmynd2_vnc_port,
                  "longmynd_p4": cliargs.longmyndp4_vnc_port,
                  "octagon": cliargs.vlc_telnet_port,
                  "rtl_sdr": cliargs.rtlsdr_vlc_telnet_port}.get(_target) or _player_telnet_port(_target)
    # Alle Knoepfe dieses Blocks (Save Video/Snapshot/QRG Eingabe UND VLC/MPV/FFplay/Stop/Pause/
    # Play) in EINEM gemeinsamen 6-Spalten-Grid mit uniform=True - dadurch sind alle Knoepfe exakt
    # gleich breit (die oberen drei spannen je 2 Spalten, die unteren sechs je 1 Spalte), statt
    # mit geschaetzten Zeichen-width()-Werten uneinheitlich auszusehen.
    _row_ctrl = ttk.Frame(_col_frame)
    _row_ctrl.pack(side='top', anchor='w')
    for _gc in range(6):
        _row_ctrl.columnconfigure(_gc, weight=1, uniform="ctrlbtn")

    _b = ttk.Button(_row_ctrl, text="Save Video", width=10, command=lambda t=_target: rec_toggle(t))
    _b.grid(row=0, column=0, columnspan=2, sticky='ew', padx=(0, 2), pady=(0, 2))
    _rec_buttons[_target] = _b
    ttk.Button(_row_ctrl, text="Snapshot", width=10, command=lambda t=_target, p=_snap_port: _snapshot_for_target(t, p)
               ).grid(row=0, column=2, columnspan=2, sticky='ew', padx=2, pady=(0, 2))
    ttk.Button(_row_ctrl, text="QRG", width=5,
               command=((lambda t=_target: _onair_rx_dialog(t)) if _target in _ONAIR_TARGETS
                        else (lambda t=_target: _qrg_toggle(t)) if _target in _LM_TARGETS + ("octagon",)
                        else _toggle_rtlsdr_cfg_window)
               ).grid(row=0, column=4, sticky='ew', padx=(2, 0), pady=(0, 2))
    ttk.Button(_row_ctrl, text="TSA", width=5, command=lambda t=_target: _tsa_start(t)
               ).grid(row=0, column=5, sticky='ew', padx=(2, 0), pady=(0, 2))

    # Player-Knoepfe (VLC/MPV/FFplay) + Stop/Pause/Play, direkt unter "Save Video" (Testphase,
    # siehe _start_player()/_stop_player()) - je ein Knopf pro Player, gruen wenn gerade aktiv.
    _player_buttons[_target] = {}
    for _gc, (_pname, _plabel) in enumerate((("vlc", "VLC"), ("mpv", "MPV"), ("ffplay", "FFplay"))):
        _pbtn = ttk.Button(_row_ctrl, text=_plabel, width=5, command=lambda t=_target, p=_pname: _start_player(t, p, user=True))
        _pbtn.grid(row=1, column=_gc, sticky='ew', padx=(0 if _gc == 0 else 2, 0))
        _player_buttons[_target][_pname] = _pbtn
    ttk.Button(_row_ctrl, text="■", width=5, command=lambda t=_target: _stop_player(t)
               ).grid(row=1, column=3, sticky='ew', padx=2)
    ttk.Button(_row_ctrl, text="⏸", width=5, command=lambda t=_target: _pause_player(t)
               ).grid(row=1, column=4, sticky='ew', padx=2)
    ttk.Button(_row_ctrl, text="▶", width=5, command=lambda t=_target: _start_player(t, _player_active.get(t) or "vlc", user=True)
               ).grid(row=1, column=5, sticky='ew')
    _update_player_buttons(_target)

def _bc_lm_status(_target, _col_frame):
    """LongMynd: Status-Zeilen (aus _build_columns ausgelagert)"""

    if _target in _LM_TARGETS:
        # Minimale Status-Zeile direkt unter der Player-Zeile: (1) laeuft der Status-Listener
        # gerade / kommen Pakete rein (Buchstabe "U", gruen/rot), (2) aktueller Longmynd-State
        # als Badge ("State: locked DVB-S2" o.ae., gruen sobald gelockt), (3) ob die gemessene
        # RF-Frequenz (Carrier+LO) zur Soll-Frequenz des Ziels passt (gruen wenn ja).
        _row_status = ttk.Frame(_col_frame)
        _row_status.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        _chip_udp = tk.Label(_row_status, text="U", width=2, anchor='center', relief='solid',
                              borderwidth=1, background="#d9d9d9", foreground="black")
        _chip_udp.pack(side='left', padx=(0, 2))
        _chip_state = tk.Label(_row_status, text="State: -", width=14, anchor='center', relief='solid',
                                borderwidth=1, background="#d9d9d9", foreground="black")
        _chip_state.pack(side='left', padx=(0, 2), fill='x', expand=True)
        _chip_freq = tk.Label(_row_status, text="-", width=11, anchor='center', relief='solid',
                               borderwidth=1, background="#d9d9d9", foreground="black")
        _chip_freq.pack(side='left', padx=(0, 2), fill='x', expand=True)
        _chip_bch = tk.Label(_row_status, text="BCH: -", width=7, anchor='center', relief='solid',
                              borderwidth=1, background="#f1c40f", foreground="black")
        _chip_bch.pack(side='left', fill='x', expand=True)
        _lm_status_chips[_target] = {"udp": _chip_udp, "state": _chip_state, "freq": _chip_freq}

        # Zweite Status-Zeile (gelb umrandet): MER, FEC/MODCOD, LNA Gain, BCH-Fehler.
        _row_status2 = tk.Frame(_col_frame, highlightbackground="#f1c40f",
                                 highlightcolor="#f1c40f", highlightthickness=2, bd=0)
        _row_status2.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        _show_lna = bool(cliargs.lna or cliargs.buttonall)
        _chip_mer = tk.Label(_row_status2, text="MER: -", width=(14 if _show_lna else 18), anchor='center',
                              background="#f1c40f", foreground="black")
        _chip_mer.pack(side='left', padx=1, pady=1, fill='x', expand=True)
        _chip_fec = tk.Label(_row_status2, text="FEC: -", width=(12 if _show_lna else 16), anchor='center',
                              background="#f1c40f", foreground="black")
        _chip_fec.pack(side='left', padx=1, pady=1, fill='x', expand=True)
        _chip_lna = tk.Label(_row_status2, text="LNA: -", width=8, anchor='center',
                              background="#f1c40f", foreground="black")
        if _show_lna:     # LNA nur mit -lna bzw. -buttonall sichtbar (Label bleibt fuers Update bestehen)
            _chip_lna.pack(side='left', padx=1, pady=1, fill='x', expand=True)
        _lm_status_chips2[_target] = {"mer": _chip_mer, "fec": _chip_fec, "lna": _chip_lna, "bch": _chip_bch}

def _bc_octagon_status(_target, _col_frame):
    """Octagon: Status-Zeilen (aus _build_columns ausgelagert)"""

    if _target == "octagon":
        # Octagon hat keine laufenden UDP-Statuspakete wie LongMynd - Status kommt stattdessen
        # per HTTP-Poll von /api/tunersignal (siehe _octagon_status_tick): ob die Box ueberhaupt
        # erreichbar ist/etwas empfaengt (State) und die gemessene Signalqualitaet (MER).
        _row_status = ttk.Frame(_col_frame)
        _row_status.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        _chip_ostate = tk.Label(_row_status, text="State: -", width=16, anchor='center', relief='solid',
                                 borderwidth=1, background="#d9d9d9", foreground="black")
        _chip_ostate.pack(side='left', padx=(0, 2), fill='x', expand=True)
        # Frequenz-Chip wie bei LongMynd: orange = Zielfrequenz gesendet, gruen = Box hat sie bestaetigt
        _chip_ofreq = tk.Label(_row_status, text="-", width=11, anchor='center', relief='solid',
                                borderwidth=1, background="#d9d9d9", foreground="black")
        _chip_ofreq.pack(side='left', fill='x', expand=True)
        _row_status2 = tk.Frame(_col_frame, highlightbackground="#f1c40f",
                                 highlightcolor="#f1c40f", highlightthickness=2, bd=0)
        _row_status2.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        # Anordnung wie bei LongMynd (MER/FEC/LNA/BCH) - Octagon liefert statt FEC/LNA/BCH per
        # /api/tunersignal nur AGC und BER, die an dieselben Positionen (Verstaerkung/Fehler)
        # gesetzt werden.
        _chip_omer = tk.Label(_row_status2, text="MER: -", width=11, anchor='center',
                               background="#f1c40f", foreground="black")
        _chip_omer.pack(side='left', padx=1, pady=1, fill='x', expand=True)
        _chip_oagc = tk.Label(_row_status2, text="AGC: -", width=10, anchor='center',
                               background="#f1c40f", foreground="black")
        _chip_oagc.pack(side='left', padx=1, pady=1, fill='x', expand=True)
        _chip_ober = tk.Label(_row_status2, text="BER: -", width=8, anchor='center',
                               background="#f1c40f", foreground="black")
        _chip_ober.pack(side='left', padx=1, pady=1, fill='x', expand=True)
        _octagon_status_chips["state"] = _chip_ostate
        _octagon_status_chips["freq"] = _chip_ofreq
        _octagon_status_chips["mer"] = _chip_omer
        _octagon_status_chips["agc"] = _chip_oagc
        _octagon_status_chips["ber"] = _chip_ober
        # CC (Continuity-Fehler) sitzt in der oberen Zeile, wie BCH bei LongMynd
        _chip_occ = tk.Label(_row_status, text="CC: -", width=7, anchor='center', relief='solid',
                              borderwidth=1, background="#f1c40f", foreground="black")
        _chip_occ.pack(side='left', padx=(2, 0), fill='x', expand=True)
        _octagon_status_chips["cc"] = _chip_occ
        # Dritte Zeile: DVBv5-Statistik des Tuners (nur im Direktmodus mit Werten gefuellt)
        _row_status3 = tk.Frame(_col_frame, highlightbackground="#f1c40f",
                                 highlightcolor="#f1c40f", highlightthickness=2, bd=0)
        if (cliargs.octagon_all_rx or cliargs.buttonall):
            _row_status3.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _k, _t in (("dbm", "dBm: -"), ("cnr", "CNR: -"), ("pre", "pre -"),
                       ("post", "post -"), ("unc", "UNC: -")):
            _lb = tk.Label(_row_status3, text=_t, anchor='center', background="#f1c40f", foreground="black")
            _lb.pack(side='left', padx=1, pady=1, fill='x', expand=True)
            _octagon_status_chips[_k] = _lb
        if not (cliargs.octagon_all_rx or cliargs.buttonall):
            # Standard: nur UNC (nicht korrigierbare Bloecke) als fuenftes Chip in der MER/SIG/BER/CC-Zeile -
            # und nur, solange der Treiber einen Wert liefert (sonst unsichtbar)
            _chip_ounc = tk.Label(_row_status2, text="UNC: -", width=8, anchor='center',
                                   background="#f1c40f", foreground="black")
            _octagon_status_chips["unc_row2"] = _chip_ounc

def _bc_rtl_status(_target, _col_frame):
    """rtl_sdr/leandvb: Status-Zeilen (aus _build_columns ausgelagert)"""

    if _target == "rtl_sdr":
        # Status-Zeile (gelb umrandet, wie bei den anderen Zielen) mit den Werten, die leandvb
        # selbst per --fd-info ausgibt (siehe _leandvb_info_reader() oben) - MER/Offset/SS/Lock,
        # dieselben Groessen, die sonst nur im eigenen "timeline"-Fenster (--gui) zu sehen sind.
        _row_status2 = tk.Frame(_col_frame, highlightbackground="#f1c40f",
                                 highlightcolor="#f1c40f", highlightthickness=2, bd=0)
        _row_status2.pack(side='top', anchor='w', pady=(2, 0))
        _chip_lmer = tk.Label(_row_status2, text="MER: -", width=14, anchor='center',
                               background="#f1c40f", foreground="black")
        _chip_lmer.pack(side='left', padx=1, pady=1)
        _chip_loffset = tk.Label(_row_status2, text="Offset: -", width=16, anchor='center',
                                  background="#f1c40f", foreground="black")
        _chip_loffset.pack(side='left', padx=1, pady=1)
        _chip_lss = tk.Label(_row_status2, text="SS: -", width=10, anchor='center',
                              background="#f1c40f", foreground="black")
        _chip_lss.pack(side='left', padx=1, pady=1)
        _chip_llock = tk.Label(_row_status2, text="Lock: -", width=10, anchor='center',
                                background="#f1c40f", foreground="black")
        _chip_llock.pack(side='left', padx=1, pady=1)
        _leandvb_status_chips["mer"] = _chip_lmer
        _leandvb_status_chips["offset"] = _chip_loffset
        _leandvb_status_chips["ss"] = _chip_lss
        _leandvb_status_chips["lock"] = _chip_llock

        # Zweite Zeile: CNR (nur belastbar mit --cnr, siehe opt_cnr in on_start), Lock-Dauer,
        # VBER (Bitfehlerrate vor der FEC) und PL-Frame-Sync (nur DVB-S2) - ebenfalls direkt aus
        # leandvbs --fd-info-Ausgabe (siehe _leandvb_info_reader() oben).
        _row_status3 = tk.Frame(_col_frame, highlightbackground="#f1c40f",
                                 highlightcolor="#f1c40f", highlightthickness=2, bd=0)
        _row_status3.pack(side='top', anchor='w', pady=(2, 0))
        _chip_lcnr = tk.Label(_row_status3, text="CNR: -", width=14, anchor='center',
                               background="#f1c40f", foreground="black")
        _chip_lcnr.pack(side='left', padx=1, pady=1)
        _chip_llocktime = tk.Label(_row_status3, text="Lock seit: -", width=14, anchor='center',
                                    background="#f1c40f", foreground="black")
        _chip_llocktime.pack(side='left', padx=1, pady=1)
        _chip_lvber = tk.Label(_row_status3, text="VBER: -", width=14, anchor='center',
                                background="#f1c40f", foreground="black")
        _chip_lvber.pack(side='left', padx=1, pady=1)
        _chip_lframelock = tk.Label(_row_status3, text="PL-Sync: -", width=12, anchor='center',
                                     background="#f1c40f", foreground="black")
        _chip_lframelock.pack(side='left', padx=1, pady=1)
        _leandvb_status_chips["cnr"] = _chip_lcnr
        _leandvb_status_chips["locktime"] = _chip_llocktime
        _leandvb_status_chips["vber"] = _chip_lvber
        _leandvb_status_chips["framelock"] = _chip_lframelock

def _bc_onair_status(_target, _col_frame):
    """OnAir: Status-Zeilen und Knoepfe (aus _build_columns ausgelagert)"""

    if _target in _ONAIR_TARGETS:
        # OnAir: Zeile 1 = Prozess/API (P), Zustand, Frequenz, Gain; Zeile 2 (gelb) = Werte aus der
        # onair-cli-Statuszeile; darunter Neustart/Stopp/Log/Geraet und die rohe Statuszeile (klein).
        _oc = {}
        _row_status = ttk.Frame(_col_frame)
        _row_status.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _k, _tx, _w in (("proc", "P", 2), ("state", "State: -", 9), ("freq", "-", 9), ("gain", "G -", 4),
                            ("level", "-", 13)):
            _lb = tk.Label(_row_status, text=_tx, width=_w, anchor='center', relief='solid', borderwidth=1,
                           background="#d9d9d9", foreground="black")
            _lb.pack(side='left', padx=(0, 2), fill='x', expand=(_k != "proc"))
            _oc[_k] = _lb
        _row_status2 = tk.Frame(_col_frame, highlightbackground="#f1c40f", highlightcolor="#f1c40f",
                                highlightthickness=2, bd=0)
        _row_status2.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _k, _tx, _w in (("esn0", "MER: -", 16), ("mod", "MOD -", 14), ("okbad", "ok - bad -", 11)):
            _lb = tk.Label(_row_status2, text=_tx, width=_w, anchor='center', background="#f1c40f", foreground="black",
                           font=('', 8))
            _lb.pack(side='left', padx=1, pady=1, fill='x', expand=True)
            _oc[_k] = _lb
        _row_oc = ttk.Frame(_col_frame)
        _row_oc.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _gc in range(5):
            _row_oc.columnconfigure(_gc, weight=1, uniform="ocbtn")
        for _gc, (_tx, _fn) in enumerate((("Neustart", lambda t=_target: (_onair_st[t].__setitem__("user_stopped", False),
                                                                           threading.Thread(target=_onair_start, args=(t,), daemon=True).start())),
                                          ("Stopp", lambda t=_target: (_onair_st[t].__setitem__("user_stopped", True),
                                                                        threading.Thread(target=_onair_stop, args=(t,), daemon=True).start())),
                                          ("Log", lambda t=_target: _onair_logwin_open(t)),
                                          ("Geraet", lambda t=_target: _onair_rx_dialog(t)))):
            ttk.Button(_row_oc, text=_tx, width=6, command=_fn).grid(row=0, column=_gc, sticky='ew', padx=(0 if _gc == 0 else 2, 0))
        _mb = ttk.Button(_row_oc, text="API", width=4, command=lambda t=_target: _onair_mode_toggle(t))
        _mb.grid(row=0, column=4, sticky='ew', padx=(2, 0))
        _onair_mode_buttons[_target] = _mb
        root.after(500, _onair_mode_button, _target)
        _oc["raw"] = tk.Label(_col_frame, text="", font=('', 7), anchor='w', justify='left', wraplength=330)
        _oc["raw"].pack(side='top', anchor='w', fill='x')
        _onair_chips[_target] = _oc
        for _k in ("state", "level", "esn0"):     # Doppelklick -> Werte-Fenster
            _oc[_k].bind("<Double-Button-1>", lambda _e, t=_target: _onair_valwin_open(t))

def _bc_dvx_status(_target, _col_frame):
    """datvRX: Status-Zeilen und Knoepfe (aus _build_columns ausgelagert)"""

    if _target in _DATVRX_TARGETS:
        # datvRX: Zeile 1 = Prozess (P), Zustand, Frequenz, Gain, Traegerversatz; Zeile 2 (gelb) = Werte aus der
        # datvrx-Statuszeile; darunter Neustart/Stopp/Log/Geraet und die rohe Statuszeile (klein).
        _dc = {}
        _row_status = ttk.Frame(_col_frame)
        _row_status.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _k, _tx, _w in (("proc", "P", 2), ("state", "-", 9), ("freq", "-", 9), ("gain", "G -", 6),
                            ("level", "Offs -", 12)):
            _lb = tk.Label(_row_status, text=_tx, width=_w, anchor='center', relief='solid', borderwidth=1,
                           background="#d9d9d9", foreground="black")
            _lb.pack(side='left', padx=(0, 2), fill='x', expand=(_k != "proc"))
            _dc[_k] = _lb
        _row_status2 = tk.Frame(_col_frame, highlightbackground="#f1c40f", highlightcolor="#f1c40f",
                                highlightthickness=2, bd=0)
        _row_status2.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _k, _tx, _w in (("esn0", "Es/N0 -", 14), ("mod", "MOD -", 18), ("okbad", "ok - bad -", 11)):
            _lb = tk.Label(_row_status2, text=_tx, width=_w, anchor='center', background="#f1c40f", foreground="black",
                           font=('', 8))
            _lb.pack(side='left', padx=1, pady=1, fill='x', expand=True)
            _dc[_k] = _lb
        # MER (aus der Konstellation), Pegel am Stick, Bitrate, TEI/CC-Fehler und Rufzeichen (Dienstname aus der SDT) - aus datv_core
        _row_status3 = tk.Frame(_col_frame, highlightbackground="#5dade2", highlightcolor="#5dade2", highlightthickness=2, bd=0)
        _row_status3.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _k, _tx, _w in (("mer", "MER -", 9), ("pegel", "Pegel -", 15), ("rate", "- Mb/s", 9), ("err", "T- C-", 8)):
            _lb = tk.Label(_row_status3, text=_tx, width=_w, anchor='center', background="#5dade2", foreground="black", font=('', 8))
            _lb.pack(side='left', padx=1, pady=1, fill='x', expand=True)
            _dc[_k] = _lb
        _dc["rufz"] = tk.Label(_col_frame, text="Rufz. -", anchor='center', relief='solid', borderwidth=1, background="#d9d9d9",
                               foreground="black", font=('', 9, 'bold'))
        _dc["rufz"].pack(side='top', anchor='w', fill='x', pady=(2, 0))
        _row_dc = ttk.Frame(_col_frame)
        _row_dc.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        for _gc in range(4):
            _row_dc.columnconfigure(_gc, weight=1, uniform="dcbtn")
        for _gc, (_tx, _fn) in enumerate((("Neustart", lambda t=_target: threading.Thread(target=_dvx_start, args=(t,), daemon=True).start()),
                                          ("Stopp", lambda t=_target: (_dvx_st[t].__setitem__("user_stopped", True),
                                                                        threading.Thread(target=_dvx_stop, args=(t,), daemon=True).start())),
                                          ("Log", lambda t=_target: _dvx_logwin_open(t)),
                                          ("Geraet", lambda t=_target: _dvx_rx_dialog(_dvx_num(t) - 1)))):
            ttk.Button(_row_dc, text=_tx, width=7, command=_fn).grid(row=0, column=_gc, sticky='ew', padx=(0 if _gc == 0 else 2, 0))
        _dc["raw"] = tk.Label(_col_frame, text="", font=('', 7), anchor='w', justify='left', wraplength=330)
        _dc["raw"].pack(side='top', anchor='w', fill='x')
        _dvx_chips[_target] = _dc

def _bc_callsign(_target, _col_frame, _cs_row_frame):
    """Rufzeichen-Feld (aus _build_columns ausgelagert)"""

    if _target in _LM_TARGETS + ("octagon", "rtl_sdr") + _UDPRX_TARGETS:
        # Rufzeichen/Service-Name des aktuell empfangenen DATV-Streams (LongMynd/P4/rtl_sdr per
        # tsduck, Octagon per SSH+dvbsnoop), ganz unten in der Spalte - als Knopf-Optik
        # (grau/leer solange nichts erkannt, gruen mit Rufzeichen sobald gefunden).
        _target_callsign_var[_target] = tk.StringVar(value="")
        _target_callsign_disp[_target] = tk.StringVar(value="")
        if _target != "octagon" and (cliargs.octagon_all_rx or cliargs.buttonall):
            # Octagon hat (mit -octagon_all_rx_parameters) drei Status-Zeilen (State/Frequenz, MER/SIG/BER/CC, dBm/CNR/pre/post/UNC),
            # die anderen Spalten nur zwei: Leerzeile derselben Hoehe einfuegen, damit die
            # Rufzeichenfelder aller Spalten auf gleicher Hoehe stehen.
            try:
                _bg = ttk.Style().lookup('TFrame', 'background') or "#d9d9d9"
            except tk.TclError:
                _bg = "#d9d9d9"
            _spacer = tk.Frame(_col_frame, highlightbackground=_bg, highlightcolor=_bg,
                                highlightthickness=2, bd=0)
            _spacer.pack(side='top', anchor='w', fill='x', pady=(2, 0))
            tk.Label(_spacer, text=" ", anchor='center').pack(side='left', padx=1, pady=1)
        _cs_lbl = tk.Label (_cs_row_frame, textvariable=_target_callsign_disp[_target], font=_CALLSIGN_FONT,
                             width=34, anchor='center', relief='solid', borderwidth=1,
                             background="#d9d9d9", foreground="black")
        _cs_lbl.pack(side='top', anchor='w', fill='x', pady=(2, 0))
        _target_callsign_label[_target] = _cs_lbl
        _cs_lbl.bind("<Button-3>", lambda e, t=_target: _solo_set(t))
        _cs_lbl.bind("<Button-1>", lambda e, t=_target: _qrz_open_page(t))
        _cs_lbl.configure(cursor="hand2")

def _bc_siglist(_col, _target):
    """Signal-Zuordnungsliste und Datenstrom-Kurve (aus _build_columns ausgelagert)"""

    # Signal-Zuordnungs-Knopfliste fuer dieses Ziel (Bake + erkannte Signale) - eigener,
    # separat leerbarer Unter-Container, damit batc_rebuild_list() nur DIESEN Teil neu aufbaut
    # und nicht die festen Bedienelemente oben (siehe _target_siglist_frames).
    _siglist_frame = ttk.Frame(frm_batc_rec)
    _siglist_frame.grid(row=2, column=_col, sticky='new', padx=(0 if _col == 0 else 10, 0), pady=(4, 0))
    _target_extra_frames[_target].append(_siglist_frame)
    _target_siglist_frames[_target] = _siglist_frame
    # Datenstrom-Kurve (aus tsanalyse uebernommen) unter den Auswahlknoepfen - nur sichtbar bei Empfang
    if _target in _TARGET_STREAM:
        _tsr_c = tk.Canvas(frm_batc_rec, height=48, bg="#101418", highlightthickness=1, highlightbackground="#3a3a3a")
        _tsr_c.grid(row=3, column=_col, sticky='new', padx=(0 if _col == 0 else 10, 0), pady=(3, 0))
        _tsr_c.grid_remove()
        _tsr_c.bind("<Configure>", lambda e, t=_target: _tsrate_draw(t))
        _tsrate_canvas[_target] = _tsr_c

def _build_columns():
    """Baut je aktivem Ziel die Spalte im Hauptfenster (Kopf, Automatik/Scan, Knoepfe, Status-Chips,
    Rufzeichen- und Signalzeile); die Abschnitte stehen als _bc_*-Funktionen darueber."""
    for _col, _target in enumerate(_active_targets):
        _col_frame, _cs_row_frame = _bc_frames(_col, _target)
        _bc_head_auto(_target, _col_frame)
        _bc_controls(_target, _col_frame)
        _bc_lm_status(_target, _col_frame)
        _bc_octagon_status(_target, _col_frame)
        _bc_rtl_status(_target, _col_frame)
        _bc_onair_status(_target, _col_frame)
        _bc_dvx_status(_target, _col_frame)
        _bc_callsign(_target, _col_frame, _cs_row_frame)
        _bc_siglist(_col, _target)

_build_columns()

#----- Datenstrom-Kurve je Empfaenger (Routine aus tsanalyse: Bytes je Sekunde aus den TS-Paketen des UDP-
#      Stroms, gesamt und ohne Null-Pakete PID 0x1FFF). Sichtbar nur, wenn der Tuner Lock hat und Daten kommen. -----
def _tsrate_reader(target):
    import collections
    st = _tsrate_state[target]
    sock = None
    cur_ep = None
    while True:
        try:
            ep = _TARGET_STREAM[target]()
            want = app_config.get("ts_rate_graph", True)
            if not want or not ep or not (str(ep[0])[:1].isdigit() and 224 <= int(str(ep[0]).split(".")[0]) <= 239):
                if sock is not None:
                    sock.close()
                    sock = None
                time.sleep(2)
                continue
            if sock is None or ep != cur_ep:
                if sock is not None:
                    sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except (AttributeError, OSError):
                    pass
                sock.bind((ep[0], int(ep[1])))          # an die Gruppe binden: nur dieser Strom (gleicher Port bei allen)
                mreq = struct.pack("4s4s", socket.inet_aton(ep[0]), socket.inet_aton("0.0.0.0"))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                sock.settimeout(0.5)
                cur_ep = ep
            try:
                data = sock.recv(65536)
            except socket.timeout:
                data = b""
            now = time.monotonic()
            if data:
                st["last_rx"] = now
                n = len(data) // 188
                nul = 0
                for k in range(n):
                    o = k * 188
                    if data[o] == 0x47 and ((data[o + 1] & 0x1F) << 8 | data[o + 2]) == 0x1FFF:
                        nul += 1
                st["acc"][0] += n * 188
                st["acc"][1] += (n - nul) * 188
            if now - st["t0"] >= 1.0:
                dt = now - st["t0"]
                st["hist"].append((st["acc"][0] * 8 / dt / 1e6, st["acc"][1] * 8 / dt / 1e6))
                st["acc"] = [0, 0]
                st["t0"] = now
        except Exception as e:
            dlog("Datenstrom-Kurve %s: %r" % (target, e))
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass
            sock = None
            time.sleep(3)

def _tsrate_draw(target, canvas=None):
    """Datenstrom-Kurve zeichnen; canvas: fremde Zeichenflaeche (Festfenster-Kachel) statt der Spalte im Hauptfenster"""
    c = canvas if canvas is not None else _tsrate_canvas.get(target)
    st = _tsrate_state.get(target)
    if c is None:
        return
    try:
        if not c.winfo_ismapped():
            return
        w, h = c.winfo_width(), c.winfo_height()
    except tk.TclError:
        return
    c.delete("all")
    if st is None:
        return
    vals = list(st["hist"])
    if not vals or w < 40:
        return
    tot = [v[0] for v in vals]
    net = [v[1] for v in vals]
    hi = max(max(tot), 0.1) * 1.15
    top, bot, x0 = 14, h - 4, 4
    n = st["hist"].maxlen
    dx = (w - 10) / float(n - 1)
    off = n - len(vals)
    for series, col in ((tot, "#5a6a7a"), (net, "#2ecc71")):
        pts = []
        for k, v in enumerate(series):
            pts += [x0 + (off + k) * dx, bot - v / hi * (bot - top)]
        if len(pts) >= 4:
            c.create_line(*pts, fill=col, width=1)
    x, y = x0 + (n - 1) * dx, bot - net[-1] / hi * (bot - top)
    c.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#e74c3c", outline="#e74c3c")
    nullp = 100.0 * (1.0 - net[-1] / tot[-1]) if tot[-1] > 0 else 0.0
    c.create_text(w - 6, 2, anchor="ne", fill="#e0e0e0", font=('', 8),
                  text="TS %.2f Mbit/s  netto %.2f  Null %.0f %%  (max %.2f)" % (tot[-1], net[-1], nullp, max(net)))

def _tsrate_tick():
    """1x/s: Kurve zeigen/zeichnen, wenn Lock + Daten, sonst ausblenden."""
    try:
        now = time.monotonic()
        on = app_config.get("ts_rate_graph", True) and not _mini["on"]
        for t, c in _tsrate_canvas.items():
            st = _tsrate_state.get(t)
            lk = _fw_locked(t)
            # LongMynd/OnAir: nur mit Lock; Ziele ohne Tuner-Status (rtl_sdr): wenn Daten kommen
            need_lock = t in _LM_TARGETS or t in _UDPRX_TARGETS
            active = bool(on and st and now - st["last_rx"] < 2.0 and (lk is True if need_lock else lk is not False))
            if active:
                if not c.winfo_ismapped():
                    c.grid()
                _tsrate_draw(t)
            elif c.winfo_ismapped():
                c.grid_remove()
                if st:
                    st["hist"].clear()
    except Exception as e:
        dlog("Datenstrom-Kurve (Anzeige): %r" % (e,))
    root.after(1000, _tsrate_tick)

#----- Spektrumanzeige unten im BATC-Panel (vor der Signalliste gepackt mit side='bottom',
# damit sie unten "andockt" und die Signalliste darueber den restlichen Platz bekommt). -----
frm_batc_spectrum = ttk.Frame (frm_batc)
frm_batc_spectrum.pack (side='bottom', fill='both', expand=True, pady=(4, 0))
frm_batc_spectrum_head = ttk.Frame (frm_batc_spectrum)
frm_batc_spectrum_head.pack (anchor='w', fill='x')
ttk.Label (frm_batc_spectrum_head, text="QO-100 Spektrum (BATC) - Klick schaltet LongMynd",
           font=('', 8)).pack (side='left')
_spectrum_manual_var = tk.BooleanVar(value=False)
ttk.Checkbutton (frm_batc_spectrum_head, text="Manuell (freie Frequenzwahl, ohne Signal-Rechteck)",
                 variable=_spectrum_manual_var).pack (side='left', padx=(12, 0))
def _spectrum_gradient_toggle():
    app_config["spectrum_gradient"] = not bool(app_config.get("spectrum_gradient"))
    app_config_save()
    btn_spec_gradient.configure(style="RecOn.TButton" if app_config["spectrum_gradient"] else "TButton")
    try:
        _spectrum_draw(force=True)
    except Exception:
        pass

btn_spec_gradient = ttk.Button (frm_batc_spectrum_head, text="Farbverlauf", command=_spectrum_gradient_toggle,
                                style="RecOn.TButton" if app_config.get("spectrum_gradient") else "TButton")
btn_spec_gradient.pack (side='left', padx=(10, 0))
# Refresh-Rate (Bilder/s) als Zahl beim Spektrum + OnlyNewSignals
ttk.Label (frm_batc_spectrum_head, text="Refresh/s", font=('', 8)).pack (side='left', padx=(10, 2))
_spec_rate_var = tk.StringVar(value=("%g" % _spectrum_rate_base()))
def _spec_rate_apply(*_a):
    try:
        v = float(_spec_rate_var.get().replace(",", "."))
    except ValueError:
        return
    v = min(20.0, max(0.2, v))
    if abs(v - float(app_config.get("spectrum_refresh") or 0)) > 1e-6:
        app_config["spectrum_refresh"] = v
        app_config_save()
        _spec_auto["rate"] = None
        olog("Spektrum: Refresh auf %.1f/s eingestellt." % v)
_spec_rate_sb = ttk.Spinbox (frm_batc_spectrum_head, from_=0.2, to=20, increment=0.5, width=4,
                             textvariable=_spec_rate_var, command=_spec_rate_apply)
_spec_rate_sb.pack (side='left')
_spec_rate_sb.bind("<Return>", _spec_rate_apply)
_spec_rate_sb.bind("<FocusOut>", _spec_rate_apply)
_spec_rate_info = tk.StringVar(value="")
ttk.Label (frm_batc_spectrum_head, textvariable=_spec_rate_info, font=('', 8), foreground="#b35900").pack (side='left', padx=(3, 0))
_spec_only_new_var = tk.BooleanVar(value=bool(app_config.get("spectrum_only_new", False)))
def _spec_only_new_toggle():
    app_config["spectrum_only_new"] = bool(_spec_only_new_var.get())
    app_config_save()
    olog("Spektrum OnlyNewSignals %s." % ("an - neu gezeichnet nur bei neuem/weggefallenem Signal (sonst alle 10 s)"
                                          if app_config["spectrum_only_new"] else "aus"))
    _spectrum_draw(force=True)
ttk.Checkbutton (frm_batc_spectrum_head, text="OnlyNewSignals", variable=_spec_only_new_var,
                 command=_spec_only_new_toggle).pack (side='left', padx=(8, 0))
_spectrum_manual_var.trace_add("write", lambda *_a: root.after(0, lambda: [_update_lm_status_chips(_t) for _t in _LM_TARGETS if _t in _active_targets]))
# eigener "Anzeige..."-Knopf entfernt - dieselbe Funktion (_spectrum_settings_open) ist bereits
# ueber das Menue Konfig -> "Spektrum-Anzeige..." erreichbar (siehe _menu_konfig weiter oben).

# Zielauswahl-Knopfreihe UNTER dem Spektrum: ein Knopf je aktivem Ziel (gleiche Spalten wie
# oben), gruen hervorgehoben = aktuell per Linksklick geschaltetes Ziel. Rechtsklick schaltet
# immer Octagon, Strg+Klick immer rtl_sdr, unabhaengig von dieser Auswahl - siehe
# _spectrum_dispatch_click(). TODO/spaeter: diese Maustasten-Zuordnung in eine Konfigdatei
# auslagern, damit sie ohne Codeaenderung umgestellt werden kann (vom Nutzer so gewuenscht).
_spectrum_target_default = "longmynd" if "longmynd" in _active_targets else (_active_targets[0] if _active_targets else "longmynd")
_spectrum_target_var = tk.StringVar(value=_spectrum_target_default)
_spectrum_target_buttons = {}
_spectrum_target_default_bg = None  # wird beim Erzeugen des ersten Knopfes mit dessen
                                     # plattform-eigener Standardfarbe belegt (kein fest
                                     # verdrahteter Name wie "SystemButtonFace", der unter
                                     # X11/Linux nicht existiert)

def _spectrum_select_target(t):
    _spectrum_target_var.set(t)
    if app_config.get("spectrum_click_target") != t:
        app_config["spectrum_click_target"] = t
        app_config_save()
    for tt, btn in _spectrum_target_buttons.items():
        btn.configure(bg=("#2ecc40" if tt == t else _spectrum_target_default_bg))

frm_batc_spectrum_targets = ttk.Frame (frm_batc_spectrum)
frm_batc_spectrum_targets.pack (side='bottom', fill='x', pady=(2, 0))
ttk.Label (frm_batc_spectrum_targets, text="Linksklick-Ziel:", font=('', 8)).pack (side='left')
for _t in _active_targets:
    _btn = tk.Button (frm_batc_spectrum_targets, text=_TARGET_LABEL.get(_t, _t), font=('', 8),
                       command=(lambda t=_t: _spectrum_select_target(t)))
    _btn.pack (side='left', padx=(4, 0))
    if _spectrum_target_default_bg is None:
        _spectrum_target_default_bg = _btn.cget("bg")
    _spectrum_target_buttons[_t] = _btn
_btn = tk.Button (frm_batc_spectrum_targets, text="auto", font=('', 8),
                   command=lambda: _spectrum_select_target("auto"))
_btn.pack (side='left', padx=(4, 0))
_spectrum_target_buttons["auto"] = _btn
def _spectrum_realtime_update():
    on = bool(app_config.get("spectrum_realtime"))
    try:
        btn_spec_realtime.configure(text="Echtzeit: %s" % ("AN" if on else "AUS"), bg=("#2ecc40" if on else _spectrum_target_default_bg))
    except (NameError, tk.TclError):
        pass
def _spectrum_realtime_toggle():
    app_config["spectrum_realtime"] = not bool(app_config.get("spectrum_realtime"))
    app_config_save()
    _spec_auto["rate"] = None
    _spectrum_realtime_update()
    olog("Spektrum Echtzeit %s." % ("AN - jedes FFT-Bild wird gezeichnet (mehr CPU)" if app_config["spectrum_realtime"]
                                    else "AUS - %.1f Bilder/s" % _spectrum_rate_base()))
    try:
        _spectrum_draw(force=True)
    except Exception:
        pass
btn_spec_realtime = tk.Button (frm_batc_spectrum_targets, text="Echtzeit: AUS", font=('', 8), command=_spectrum_realtime_toggle)
btn_spec_realtime.pack (side='right', padx=(0, 2))
_spectrum_realtime_update()
_spectrum_select_target(app_config.get("spectrum_click_target") if app_config.get("spectrum_click_target") in
                        list(_active_targets) + ["auto"] else _spectrum_target_default)

#===== Einfach-Ansicht (Menue "Einfach: AN/AUS", Parameter -einfach): 4 Videos im Hauptfenster + Mini-Steuerung darunter =====
# Die Videos laufen ueber libvlc (leandvb_einfach.py, Paket python-vlc) eingebettet; die einzelnen VLC-Fenster werden in dieser Ansicht nicht gestartet
# (_start_player, Player-Ueberwachung, Bild-Waechter und AutoClose ruhen). Ausschalten bringt die Fenster-Player wieder.

def _einfach_candidates():
    """Tuner, die ein Video zeigen sollen, in Tuner-Reihenfolge: mit Stream-Adresse, Lock auf einem lebenden Signal, NICHT die Bake (auch der Octagon
    ohne gemerkte Frequenz: Rufzeichen A71A) und nur, wenn gerade UDP-Daten ankommen (OnAir ohne eingeschalteten Stream liefert nichts); dieselbe
    Frequenz nur einmal."""
    out, freqs, calls = [], [], set()
    now = time.monotonic()
    for t in _active_targets:
        try:
            if not _player_stream_url(t):
                continue
            if _fw_locked(t) is not True or _no_live_signal(t):
                continue
            f = _target_cur_freq.get(t)
            if f is not None and abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
                continue
            cv = _target_callsign_var.get(t)
            if cv is not None and (cv.get() or "").upper().startswith("A71A"):
                continue
            st = _tsrate_state.get(t)
            if st is not None and now - st.get("last_rx", -1e9) > 4.0:
                continue                                   # keine UDP-Daten: Kachel bliebe schwarz
            if f is not None and any(abs(f - x) <= BATC_SIG_TOL for x in freqs):
                continue                                   # dasselbe Signal schon in einer Kachel
            cs_now = ((cv.get() if cv is not None else "") or "").strip().upper()
            if cs_now and cs_now in calls:
                continue                                   # gleiches Rufzeichen schon in einer Kachel (z.B. Octagon ohne gemeldete Frequenz)
            if cs_now:
                calls.add(cs_now)
            if f is not None:
                freqs.append(f)
            out.append(t)
        except Exception:
            continue
    return out

def _einfach_info(t):
    cs = ""
    try:
        v = _target_callsign_var.get(t)
        cs = v.get() if v is not None else ""
    except Exception:
        pass
    return {"label": _TARGET_LABEL.get(t, t), "freq": _target_cur_freq.get(t), "sr": _target_cur_sr.get(t), "call": cs or "", "mer": _web_mer_text(t)}

def _einfach_signals():
    """Alle lebenden BATC-Signale fuer das Rechtsklick-Menue der Kacheln: Liste (Text, Schluessel), nach Frequenz"""
    out = []
    with _batc_lock:
        sigs = sorted(_batc_sig_state.items(), key=lambda kv: kv[1]["freq"])
    for key, st in sigs:
        if st["gone_since"] is not None:
            continue
        cs = _call_for_freq(st["freq"])
        out.append(("%.3f MHz  %d kS/s%s" % (st["freq"], st["sr"], ("  " + cs) if cs else ""), key))
    return out

def _einfach_set_count(n):
    v = _einfach.get("view")
    if v is None:
        olog("Festfenster ist aus - Kachelzahl wird erst bei eingeschaltetem Festfenster gesetzt.")
        return
    v.set_fixed(n)

def _einfach_snapshot_path(t):
    d = _vlc_snapshot_path_hint()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, _snapshot_filename_stub(t) + ".png")

def _einfach_release(t):
    """Kachel-Rechtsklick 'Tuner freigeben': Player stoppen, Tuner (auch Pi-Stick) frei fuer neue Signale, Verteilung neu"""
    _dup["closed"][t] = _target_cur_freq.get(t)
    _stop_player(t)
    try:
        if _dvx_is_dienst(t):
            _dvx_stop(t)
    except Exception as e:
        dlog("Festfenster: Dienst-Stick %s nicht freigegeben: %r" % (t, e))
    _target_last_tuned[t] = None
    _target_cur_freq[t] = None
    olog("%s: aus dem Festfenster freigegeben - Tuner frei fuer neue Signale." % _TARGET_LABEL.get(t, t))
    batc_auto_assign(report=True)

def _einfach_tune(t, key):
    st = _batc_sig_state.get(key)
    if st is None:
        return
    target_dispatch(t, st["freq"], st["sr"], key)

def _einfach_menu_update():
    try:
        last = _menubar.index("end")
    except tk.TclError:
        return
    for i in range((last or 0) + 1):
        try:
            if str(_menubar.entrycget(i, "label")).startswith(("Einfach", "Festfenster")):
                _menubar.entryconfigure(i, label="Festfenster: AN" if _einfach["on"] else "Festfenster: AUS")
        except tk.TclError:
            continue

def _einfach_apply(on):
    if bool(on) == _einfach["on"]:
        return
    if on:
        try:
            if _einfach_dir not in sys.path:
                sys.path.insert(0, _einfach_dir)
            import leandvb_einfach as _ev
            view = _ev.EinfachView(root, frm_root, _einfach_candidates, _einfach_info, _player_stream_url, dict(_TARGET_LABEL),
                                   lambda m: root.after(0, olog, m), get_marquee=_marquee_text_for,
                                   get_signals=_einfach_signals, tune=_einfach_tune, draw_rate=_tsrate_draw, release=_einfach_release,
                                   snapshot_path=_einfach_snapshot_path, record=rec_toggle, rec_state=lambda t: bool(_rec_active.get(t)))
        except Exception as e:
            olog("Einfach-Ansicht nicht moeglich: %s" % (e,))
            _einfach_menu_update()
            return
        _einfach["view"] = view
        _einfach["on"] = True
        _einfach["mini_was"] = _mini["on"]
        try:
            _einfach["root_pack"] = frm_root.pack_info()
            _einfach["root_pack"].pop("in", None)
            frm_root.pack_configure(expand=False, fill="x")
        except tk.TclError:
            pass
        _mini_apply(True)                                  # Steuerung unten = Mini-Anzeige
        for t in list(_active_targets):                    # laufende Einzelfenster schliessen
            try:
                if _autoclose_player_running(t):
                    _player_wanted[t] = False
                    _stop_player(t)
            except Exception:
                pass
        try:
            w = min(root.winfo_screenwidth() - 160, 2560)
            h = min(root.winfo_screenheight() - 100, 1500)
            root.geometry("%dx%d+40+30" % (w, h))
        except tk.TclError:
            pass
        olog("Einfach-Ansicht AN - 4 Videos im Hauptfenster, Steuerung darunter (Klick auf die Kopfzeile einer Kachel = Ton).")
    else:
        _einfach["on"] = False
        v = _einfach["view"]
        _einfach["view"] = None
        if v is not None:
            v.close()
        try:
            if _einfach["root_pack"]:
                frm_root.pack_forget()
                frm_root.pack(**_einfach["root_pack"])
        except tk.TclError:
            pass
        _mini_apply(_einfach["mini_was"])
        try:
            root.geometry("")
        except tk.TclError:
            pass
        for t in list(_active_targets):
            _player_wanted[t] = True                       # die Player-Ueberwachung oeffnet die Fenster wieder (nur bei Lock)
        olog("Einfach-Ansicht AUS - Einzelfenster-Player wieder aktiv.")
    _einfach_menu_update()

_einfach_dir = os.path.dirname(os.path.abspath(__file__))

def _einfach_toggle():
    on = not _einfach["on"]
    _einfach_apply(on)
    if _einfach["on"] == on:
        app_config["einfach_mode"] = on
        app_config_save()

if cliargs.einfach or app_config.get("einfach_mode"):
    root.after(1800, _einfach_apply, True)

#===== Mini-Anzeige (Menue "Mini: AN/AUS", Parameter -mini): nur BATC-Spektrum, Signalknoepfe je Tuner, MER und Rufzeichen =====
# Alles andere (Kopfzeilen, Knopfreihen, Status-Chips, Datenstrom-Kurve, Spektrum-Einstellungen) wird nur ausgeblendet (pack_forget) und
# beim Ausschalten in der alten Reihenfolge mit den alten Optionen wieder eingeblendet; die Tuner laufen unveraendert weiter.
_MINI_SIG_W = 27                      # Zeichenbreite der Signalknoepfe in der Mini-Ansicht (sonst 34)
_MINI_COL_MIN = 175                   # schmalste Tunerspalte in Mini (Pixel)
_mini = {"on": False, "saved": {}, "mer": {}, "bar": {}, "menu_t": {}, "plus": None}

def _mini_src(t):
    """Anzeige-Label, aus dem das MER dieses Tuners gelesen wird (die bestehenden gelben Chips)"""
    try:
        if t in _LM_TARGETS:
            return _lm_status_chips2[t]["mer"]
        if t == "octagon":
            return _octagon_status_chips["mer"]
        if t == "rtl_sdr":
            return _leandvb_status_chips["mer"]
        if t in _ONAIR_TARGETS:
            return _onair_chips[t]["esn0"]
        if t in _DATVRX_TARGETS:
            return _dvx_chips[t]["mer"]
    except KeyError:
        pass
    return None

# Menues der Mini-/Einfach-Ansicht: je Tuner ein Menue (Knopf ☰ neben dem MER) mit ALLEN Knoepfen dieser Spalte, die in der Mini-Ansicht ausgeblendet sind,
# und ein Menue "+" ganz links in der Titelzeile mit den allgemeinen Funktionen (Verteiler, Quad, Anordnung, Spektrum ...). Die Eintraege rufen die
# verborgenen Original-Knoepfe auf (invoke) - es gibt also nichts doppelt zu pflegen; die Listen entstehen beim Oeffnen des Menues neu.
_MINI_TXT = {"■": "Player stoppen", "⏸": "Pause", "▶": "Play", "VLC": "VLC starten", "MPV": "MPV starten", "FFplay": "FFplay starten", "Scan": "Scan",
             "AScan": "Auto-Scan", "QRG": "QRG-Eingabe", "TSA": "TS-Analyse", "Neustart": "Tuner neu starten", "Stopp": "Tuner stoppen", "Geraet": "Geraet waehlen ...",
             "VerteilA": "VerteilA (Automatik fuer diesen Tuner)", "Direkt *": "Direktmodus", "Direkt": "Direktmodus"}

def _mini_collect(widget, out):
    for c in widget.winfo_children():
        if isinstance(c, (ttk.Button, tk.Button, ttk.Checkbutton, tk.Checkbutton)):
            try:
                txt = str(c.cget("text"))
            except tk.TclError:
                txt = ""
            if txt.strip():
                out.append((txt, c))
        else:
            _mini_collect(c, out)

def _mini_invoke(w):
    try:
        w.invoke()
    except tk.TclError:
        pass

def _mini_fill(menu, groups, extra=()):
    menu.delete(0, "end")
    for gi, items in enumerate(g for g in groups if g):
        if gi:
            menu.add_separator()
        for txt, w in items:
            menu.add_command(label=_MINI_TXT.get(txt, txt), command=lambda w=w: _mini_invoke(w))
    if extra:
        menu.add_separator()
        for lab, fn in extra:
            menu.add_command(label=lab, command=fn)

def _mini_groups_of(parent):
    """Knoepfe der in der Mini-Ansicht ausgeblendeten Kinder von parent (Gruppenweise, in Original-Reihenfolge)"""
    groups = []
    for w, _info in (_mini["saved"].get(parent) or []):
        if w.winfo_manager():                      # sichtbar geblieben (Kopf, Checkbox, Spektrum ...): nicht aufnehmen
            continue
        items = []
        if isinstance(w, (ttk.Button, tk.Button, ttk.Checkbutton, tk.Checkbutton)):
            try:
                if str(w.cget("text")).strip():
                    items.append((str(w.cget("text")), w))
            except tk.TclError:
                pass
        else:
            _mini_collect(w, items)
        groups.append(items)
    return groups

def _mini_menu_tuner(t, menu):
    _mini_fill(menu, _mini_groups_of(_target_col_frames.get(t)))

def _mini_menu_general(menu):
    groups = _mini_groups_of(frm_batc) + _mini_groups_of(frm_batc_spectrum)
    extra = [("Mini-Anzeige AUS", lambda: _mini_toggle()), ("Festfenster AN/AUS", lambda: _einfach_toggle())]
    extra += [("Festfenster: %s" % ("Kacheln automatisch" if n is None else "%d Kacheln" % n), lambda n=n: _einfach_set_count(n)) for n in (None, 4, 5, 6, 8)]
    _mini_fill(menu, groups, extra)

def _mini_build():
    for t in _active_targets:
        cf = _target_col_frames.get(t)
        if cf is not None:
            bar = tk.Frame(cf)
            lb = tk.Label(bar, text="MER: -", anchor='center', background="#f1c40f", foreground="black", font=('', 9, 'bold'))
            lb.pack(side='left', fill='x', expand=True)
            if _batc_auto.get(t) is not None:                                # Haken VerteilA wie in der Hauptansicht (gleiche Variable)
                ttk.Checkbutton(bar, text="VerteilA", style="Small.TCheckbutton", variable=_batc_auto[t]).pack(side='left', padx=(4, 0))
            mb = ttk.Menubutton(bar, text="\u2630", width=2)                  # ☰ Menue mit allen ausgeblendeten Knoepfen dieses Tuners
            mb.pack(side='right', padx=(3, 0))
            mn = tk.Menu(mb, tearoff=False, postcommand=lambda t=t: _mini_menu_tuner(t, _mini["menu_t"][t]))
            mb["menu"] = mn
            _mini["menu_t"][t] = mn
            _mini["bar"][t] = bar
            _mini["mer"][t] = lb

def _tuner_occupancy():
    """(belegt, gesamt): belegt = Zahl der lebenden (nicht Bake) BATC-Signale, auf denen ein verfuegbarer Tuner steht (je Signal einmal); gesamt = verfuegbare Tuner"""
    with _batc_lock:
        live = [st["freq"] for st in _batc_sig_state.values() if st["gone_since"] is None and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5]
    tot = 0
    served = set()                  # versorgte Signale (jedes nur einmal: zwei Tuner auf demselben Signal zaehlen nicht doppelt)
    for t in _active_targets:
        try:
            if not _target_available(t):
                continue
        except Exception:
            pass
        tot += 1
        f = _target_cur_freq.get(t)
        if f is None or abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:      # Bake zaehlt nie als belegt
            continue
        for i, x in enumerate(live):
            if abs(f - x) <= BATC_SIG_TOL:
                served.add(i)
                break
    return len(served), tot

def _unserved_count():
    """Zahl der lebenden (nicht Bake) Signale, auf denen gerade kein Tuner steht"""
    with _batc_lock:
        live = [st["freq"] for st in _batc_sig_state.values() if st["gone_since"] is None and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5]
    cur = [_target_cur_freq.get(t) for t in _active_targets if _target_cur_freq.get(t) is not None]
    return sum(1 for f in live if not any(abs(f - c) <= BATC_SIG_TOL for c in cur))

def _mini_rest_click():
    """Knopf 'Restliche verteilen' (Kopfzeile, nur sichtbar bei unversorgten Signalen): fragt, ob Tuner ohne 'VerteilA' (verfuegbar, aber
    nicht fuer die Automatik freigegeben) mit freigegeben werden sollen, und verteilt dann die restlichen Signale (wie NeuNeuVerteilen)."""
    n = _unserved_count()
    if n <= 0:
        return
    if not batc_auto_master.get():
        olog("Restliche verteilen: 'Verteil Automatik' ist AUS - es wird nichts verteilt.")
        return
    sperr = []
    for t in _active_targets:
        v = _batc_auto.get(t)
        try:
            ok = _target_available(t)
        except Exception:
            ok = True
        if v is not None and not v.get() and ok and not _scan_active.get(t):
            sperr.append(t)
    if sperr:
        ans = messagebox.askyesnocancel(
            "Restliche verteilen",
            "%d Signal(e) ohne Tuner.\n\nNicht fuer die Automatik freigegeben (VerteilA aus):\n  %s\n\n"
            "Diese Tuner mit freigeben und die restlichen Signale darauf verteilen?\n"
            "Ja = freigeben + verteilen,  Nein = nur mit den schon freigegebenen verteilen,  Abbrechen = nichts tun."
            % (n, ", ".join(_TARGET_LABEL.get(t, t) for t in sperr)), parent=root)
        if ans is None:
            return
        if ans:
            for t in sperr:
                _batc_auto[t].set(True)
            olog("Restliche verteilen: VerteilA freigegeben fuer " + ", ".join(_TARGET_LABEL.get(t, t) for t in sperr))
    batc_redistribute_rest()

def _mini_tick():
    """1x/s: MER-Text aus dem jeweiligen Chip in die Mini-Anzeige uebernehmen"""
    try:
        if _mini["on"]:
            if _mini.get("occ") is not None:
                _u, _n = _tuner_occupancy()
                _mini["occ"].configure(text="%d Tuner von %d belegt" % (_u, _n), foreground=("#1a7f37" if _u else "#666666"))
                _rb, _un = _mini.get("rest"), _unserved_count()
                if _rb is not None:
                    if _un > 0:
                        _rb.configure(text="Restliche verteilen (%d)" % _un)
                        if not _rb.winfo_ismapped():
                            _rb.pack(side="left", padx=(6, 0))
                    elif _rb.winfo_ismapped():
                        _rb.pack_forget()
            for t, lb in _mini["mer"].items():
                src = _mini_src(t)
                try:
                    txt = src.cget("text") if src is not None else "MER: -"
                except tk.TclError:
                    txt = "MER: -"
                lb.configure(text=txt, **_mer_style(_mer_from_text(txt)))
    except Exception as e:
        dlog("Mini-Anzeige: %r" % (e,))
    root.after(1000, _mini_tick)

def _mini_hide(parent, keep=()):
    """Reihenfolge und Pack-Optionen aller Kinder merken, alle ausser keep ausblenden"""
    order = []
    for w in parent.pack_slaves():
        info = w.pack_info()
        info.pop("in", None)
        order.append((w, info))
    _mini["saved"][parent] = order
    for w, _i in order:
        if w not in keep:
            w.pack_forget()

def _mini_restore(parent):
    for w, info in _mini["saved"].pop(parent, []):
        w.pack_forget()
        w.pack(**info)

_mini_fit_job = [None]
def _mini_fit(_e=None):
    """Mini: Fenster in der Breite verstellbar - Tunerspalten und Spektrum (Seitenverhaeltnis 2,1:1) passen sich an (entprellt)"""
    if _e is not None and _e.widget is not root:
        return
    if not _mini["on"] or not _mini.get("cols"):
        return
    if _mini_fit_job[0] is not None:
        try:
            root.after_cancel(_mini_fit_job[0])
        except (tk.TclError, ValueError):
            pass
    _mini_fit_job[0] = root.after(150, _mini_fit_now)

def _mini_fit_now():
    _mini_fit_job[0] = None
    try:
        if time.time() - _mini.get("t_apply", 0.0) < 1.3:           # Fenster schrumpft nach dem Einschalten gerade auf die natuerliche Groesse: erst danach anpassen
            _mini_fit_job[0] = root.after(1400, _mini_fit_now)
            return
        if not _mini["on"] or not _mini.get("cols") or _einfach["on"]:
            return
        cols = int(_mini["cols"])
        span = int(_mini.get("span", 2))
        avail = root.winfo_width() - 40
        cw = max(_MINI_COL_MIN, int((avail - 10 * (cols - 1)) / cols))
        for c in range(cols):
            frm_batc_rec.grid_columnconfigure(c, minsize=cw)             # gleich breite Spalten (uniform greift bei ueberspannenden Widgets nicht)
        sw = cw * span + 10 * (span - 1) - 8
        sh = max(120, int(sw / float(app_config.get("mini_spectrum_ratio", 2.1))))
        if abs(_spectrum_canvas.winfo_width() - sw) > 3 or abs(_spectrum_canvas.winfo_height() - sh) > 3:
            _spectrum_canvas.configure(width=sw, height=sh)
    except tk.TclError:
        pass

def _mini_apply(on):
    if bool(on) == _mini["on"]:
        return
    _mini["on"] = bool(on)
    _mini["t_apply"] = time.time()
    if on:
        _row_title = chk_batc_auto_master.master            # Titelzeile: nur das Haekchen "Verteil Automatik" bleibt sichtbar
        _mini_hide(frm_batc, keep=(_row_title, frm_batc_rec, frm_batc_spectrum))
        _mini_hide(_row_title, keep=(chk_batc_auto_master,))
        _mini["row_title"] = _row_title
        _pl = ttk.Menubutton(_row_title, text="+ Menue", width=8)           # allgemeine Funktionen (ausgeblendete Knoepfe der Kopfzeilen, Anordnung ...)
        _pm = tk.Menu(_pl, tearoff=False, postcommand=lambda: _mini_menu_general(_mini["plus_menu"]))
        _pl["menu"] = _pm
        _pl.pack(side="left", padx=(0, 8))
        chk_batc_auto_master.pack_configure(side="left", padx=(0, 8))      # "Verteil Automatik" direkt neben das Menue (beim Ausschalten wird der alte Platz wiederhergestellt)
        _mini["plus"], _mini["plus_menu"] = _pl, _pm
        _mini["occ"] = ttk.Label(_row_title, text="", font=('', 9, 'bold'))                 # Status: wie viele Tuner auf einem Signal stehen
        _mini["occ"].pack(side="left", padx=(8, 0))
        _mini["rest"] = ttk.Button(_row_title, text="Restliche verteilen", command=_mini_rest_click)   # erscheint nur, wenn Signale ohne Tuner da sind
        # kleinere Knoepfe (Signalknoepfe u.a.): ttk-Stil TButton (die Signal-Stile erben davon); Original merken
        _stl = ttk.Style()
        _mini["btn_style"] = {"font": _stl.lookup("TButton", "font"), "padding": _stl.lookup("TButton", "padding")}
        _mini["mb_style"] = {"font": _stl.lookup("TMenubutton", "font"), "padding": _stl.lookup("TMenubutton", "padding")}
        _stl.configure("TButton", font=('', 7), padding=(2, 0))
        _stl.configure("TMenubutton", font=('', 7), padding=(2, 0))
        # Spektrum schmaler (ueber die ersten Spalten) und die letzten Tuner rechts daneben, darunter eine zweite Bandzeile
        try:
            _pi = _spectrum_canvas.pack_info()
            _pi.pop("in", None)
            _fp = frm_batc_spectrum.pack_info()
            _fp.pop("in", None)
            _mini["spec"] = {"pack": _pi, "w": _spectrum_canvas.cget("width"), "h": _spectrum_canvas.cget("height"), "fpack": _fp}
            _n = len(_active_targets)
            _span = min(int(app_config.get("mini_spectrum_span", 4)), max(1, _n))                           # Spektrum ueber 4 Tunerspalten (mini_spectrum_span), Seitenverhaeltnis wie die BATC-Seite (~2:1)
            _links = app_config.get("mini_layout", "links") != "unten"
            if _links:
                _band1 = -(-_n // 2)                              # Spektrum links ueber die volle Hoehe, alle Tuner rechts daneben in zwei Baendern
                _top = _span + _band1
            else:
                _top = max(_span, -(-(_n + _span) // 2))          # alt: obere Zeile; der Rest steht unten rechts neben dem Spektrum
            _moved = []
            _tg = list(_active_targets)
            for i, t in enumerate(_tg if _links else _tg[_top:]):
                if _links:
                    band, j = (0, i) if i < _band1 else (1, i - _band1)
                    roff, ccol = 4 * band, _span + j
                else:
                    roff, ccol = 4, _span + i
                for w in [_target_col_frames.get(t)] + list(_target_extra_frames.get(t, [])):
                    if w is None:
                        continue
                    gi = w.grid_info()
                    gi.pop("in", None)
                    _moved.append((w, gi))
                    ng = dict(gi)
                    ng["row"] = int(gi["row"]) + roff
                    ng["column"] = ccol
                    ng["padx"] = (10, 0)
                    w.grid(**ng)
            _mini["moved"] = _moved
            if not _links:
                frm_batc_rec.grid_rowconfigure(6, weight=1)          # Zusatzhoehe des Spektrums nur an die letzte Zeile, damit die Tunerzeilen daneben dicht bleiben
            _mini["cols"] = _top
            _mini["span"] = _span
            for c in range(_top):
                frm_batc_rec.grid_columnconfigure(c, weight=1)          # Spalten teilen sich die Fensterbreite (Fenster ist in Mini links/rechts verstellbar)
            frm_batc_rec.pack_configure(fill="x")
            frm_batc.grid_configure(sticky="nsew")                      # Rahmen folgt der Fensterbreite
            frm_root.grid_columnconfigure(0, weight=1)
            root.resizable(True, True)
            _sw = int(app_config.get("mini_spectrum_w", 560))
            _spectrum_canvas.configure(width=_sw, height=int(_sw / float(app_config.get("mini_spectrum_ratio", 2.1))))    # Seitenverhaeltnis wie die BATC-Seite
            _spectrum_canvas.pack_configure(fill="none", expand=False, anchor="nw")
            if _links:
                frm_batc_spectrum.grid(in_=frm_batc_rec, row=0, column=0, columnspan=_span, rowspan=7, sticky="nw", pady=(0, 0))
            else:
                frm_batc_spectrum.grid(in_=frm_batc_rec, row=4, column=0, columnspan=_span, rowspan=3, sticky="nw", pady=(4, 0))
            for _row in list(_batc_row_cache):                          # schmalere Signalknoepfe (Neuaufbau nutzt _MINI_SIG_W)
                for _b in _row["buttons"]:
                    try:
                        _b.configure(width=_MINI_SIG_W)
                    except tk.TclError:
                        pass
            _mini["call_saved"] = {}
            for t, lb in _target_callsign_label.items():
                _mini["call_saved"][t] = (lb.cget("width"), lb.cget("font"))
                lb.configure(width=int(app_config.get("mini_call_width", 22)), font=('', 9, 'bold'))
        except tk.TclError as e:
            dlog("Mini-Layout: %r" % (e,))
        for t in _active_targets:
            cf = _target_col_frames.get(t)
            if cf is None:
                continue
            _mini_hide(cf, keep=(_dvx_head_lbl.get(t),))
            if t in _mini["bar"]:
                _mini["bar"][t].pack(side='top', anchor='w', fill='x', pady=(2, 0))
        _mini_hide(frm_batc_spectrum, keep=(_spectrum_canvas, frm_batc_spectrum_targets))
        # Zielzeile "Linksklick-Ziel" (Spektrum-Klick: je Tuner ein Knopf + auto) bleibt sichtbar, aber mit Kurznamen, damit sie schmal ist
        _mini["tgt_saved"] = {}
        for _tt, _bb in list(_spectrum_target_buttons.items()):
            try:
                _mini["tgt_saved"][_tt] = (_bb.cget("text"), _bb.cget("font"))
                if _tt != "auto":
                    _m = re.search(r"RTL\d", _TARGET_LABEL.get(_tt, _tt))
                    _bb.configure(text=_m.group(0) if _m else _TARGET_LABEL.get(_tt, _tt).split(" ")[0], font=('', 7), padx=2, pady=0)
                else:
                    _bb.configure(font=('', 7), padx=2, pady=0)
            except tk.TclError:
                pass
        for _w in frm_batc_spectrum_targets.winfo_children():
            if isinstance(_w, ttk.Label):
                _mini["tgt_lbl"] = (_w, _w.cget("text"))
                _w.configure(text="Klick:")
        try:
            btn_spec_realtime.configure(font=('', 7), padx=2, pady=0)
        except (NameError, tk.TclError):
            pass
        for c in _tsrate_canvas.values():
            c.grid_remove()
    else:
        for t in _active_targets:
            cf = _target_col_frames.get(t)
            if cf is None:
                continue
            if t in _mini["bar"]:
                _mini["bar"][t].pack_forget()
            _mini_restore(cf)
        _mini_restore(frm_batc_spectrum)
        if _mini.get("occ") is not None:
            _mini["occ"].destroy()
            _mini["occ"] = None
        if _mini.get("rest") is not None:
            _mini["rest"].destroy()
            _mini["rest"] = None
        if _mini.get("plus") is not None:
            _mini["plus"].destroy()
            _mini["plus"] = None
        if _mini.get("row_title") is not None:
            _mini_restore(_mini["row_title"])
        _mini_restore(frm_batc)
        try:
            _stl = ttk.Style()
            _bs = _mini.get("btn_style") or {}
            _stl.configure("TButton", font=_bs.get("font") or "TkDefaultFont", padding=_bs.get("padding") or "")
            _ms = _mini.get("mb_style") or {}
            _stl.configure("TMenubutton", font=_ms.get("font") or "TkDefaultFont", padding=_ms.get("padding") or "")
        except tk.TclError:
            pass
        try:
            _sp = _mini.get("spec")
            if _sp:
                for w, gi in _mini.pop("moved", []):
                    w.grid(**gi)
                frm_batc_rec.grid_rowconfigure(6, weight=0)
                for c in range(int(_mini.get("cols", 0))):
                    frm_batc_rec.grid_columnconfigure(c, weight=0, minsize=0, uniform="")
                _mini["cols"] = 0
                frm_batc.grid_configure(sticky="ns")
                frm_root.grid_columnconfigure(0, weight=0)
                root.resizable(True, False)
                for c in range(int(_mini.get("cols", 0))):
                    frm_batc_rec.grid_columnconfigure(c, uniform="")
                for _row in list(_batc_row_cache):
                    for _b in _row["buttons"]:
                        try:
                            _b.configure(width=34)
                        except tk.TclError:
                            pass
                for t, (cw, cfont) in _mini.pop("call_saved", {}).items():
                    _target_callsign_label[t].configure(width=cw, font=cfont)
                for _tt, (_tx, _fo) in _mini.pop("tgt_saved", {}).items():
                    try:
                        _spectrum_target_buttons[_tt].configure(text=_tx, font=_fo, padx=1, pady=1)
                    except tk.TclError:
                        pass
                if _mini.get("tgt_lbl"):
                    _mini["tgt_lbl"][0].configure(text=_mini["tgt_lbl"][1])
                    _mini["tgt_lbl"] = None
                try:
                    btn_spec_realtime.configure(font=('', 8), padx=1, pady=1)
                except (NameError, tk.TclError):
                    pass
                frm_batc_spectrum.grid_forget()
                frm_batc_spectrum.pack(**_sp["fpack"])
                _spectrum_canvas.configure(width=_sp["w"], height=_sp["h"])
                _spectrum_canvas.pack_forget()
                _spectrum_canvas.pack(**_sp["pack"])
        except tk.TclError as e:
            dlog("Mini-Layout zurueck: %r" % (e,))
    try:
        root.update_idletasks()
        root.geometry("")                       # Fenster auf die neue Inhaltsgroesse bringen
    except tk.TclError:
        pass
    if on:
        root.after(1500, _mini_fit_now)
    _mini_menu_update()

def _mini_menu_update():
    try:
        last = _menubar.index("end")
    except tk.TclError:
        return
    for i in range((last or 0) + 1):
        try:
            if str(_menubar.entrycget(i, "label")).startswith("Mini"):      # (Eintrag 0 kann ein Tearoff ohne Label sein)
                _menubar.entryconfigure(i, label="Mini: AN" if _mini["on"] else "Mini: AUS")
        except tk.TclError:
            continue

def _mini_toggle():
    on = not _mini["on"]
    _mini_apply(on)
    app_config["mini_mode"] = on
    app_config_save()
    olog("Mini-Anzeige %s." % ("AN - nur Spektrum, Signalknoepfe, MER und Rufzeichen" if on else "AUS - volle Anzeige"))

_mini_build()
root.bind("<Configure>", _mini_fit, add="+")
root.after(1000, _mini_tick)
if cliargs.mini or app_config.get("mini_mode"):
    root.after(800, _mini_apply, True)


#===== "Scan-Spektrum": zweites Spektrum vom RTL-Stick (HB9IIU rtl-sdr-server), unter dem BATC-Spektrum =====
# Der Knopf startet den Server (main_lo9540.py, Pfad/Korrektur/Port in app_config:
# stick_spectrum_script / stick_spectrum_corr_khz / stick_spectrum_port), verbindet sich per WebSocket
# (gleiches 922-Punkte-Format wie BATC) und zeigt das Ergebnis darunter an. Ausschalten beendet den Server
# wieder, damit der Stick frei wird. Laeuft schon ein Server auf dem Port, wird er nur genutzt (nicht beendet).
_stick = {"on": False, "proc": None, "owned": False, "fft": None, "gen": 0, "ws": None,
          "msg_time": 0.0, "drawn": None, "status": ""}
_stick_canvas = None
STICK_SPEC_HEIGHT = 230

def _stick_port():
    try:
        return int(app_config.get("stick_spectrum_port") or 7681)
    except (TypeError, ValueError):
        return 7681

def _stick_port_open():
    try:
        with socket.create_connection(("127.0.0.1", _stick_port()), timeout=0.5):
            return True
    except OSError:
        return False

def _stick_rtl_busy():
    """True, wenn gerade ein rtl_sdr-Prozess (Empfangspipeline) den Stick belegt."""
    try:
        return subprocess.run(["pgrep", "-x", "rtl_sdr"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False

def _stick_ws_loop(gen):
    """Hintergrund-Thread: haelt die WebSocket-Verbindung zum Stick-Server, baut sie bei Bedarf neu auf."""
    while _stick["on"] and _stick["gen"] == gen:
        try:
            def _on_msg(ws, msg):
                if len(msg) == 1844:
                    _stick["fft"] = struct.unpack("<922H", msg)
                    _stick["msg_time"] = time.time()
                    if _spq["active"] == "stick":          # Stick ersetzt das BATC-Spektrum: gleiche Signalerkennung
                        try:
                            batc_analyze_signals(_stick["fft"])
                        except Exception as e:
                            _poll_log("Fehler in batc_analyze_signals (Stick): %r" % (e,))
            app = websocket.WebSocketApp("ws://127.0.0.1:%d" % _stick_port(),
                                         subprotocols=["fft_fast"], on_message=_on_msg)
            _stick["ws"] = app
            app.run_forever(ping_interval=15, ping_timeout=8)
        except Exception:
            pass
        for _ in range(20):                      # 2 s warten, dabei auf Abschalten achten
            if not _stick["on"] or _stick["gen"] != gen:
                return
            time.sleep(0.1)

def _stick_find_python():
    """Python-Interpreter finden, der die Module des Spektrum-Servers hat (websockets, numpy, rtlsdr).
    Reihenfolge: app_config 'stick_spectrum_python', Python der GUI, python3 im PATH, /usr/bin/python3,
    python3 aus aktiven/ueblichen venvs. Ergebnis wird gemerkt."""
    cached = _stick.get("python")
    if cached and os.path.isfile(cached):
        return cached
    cands = []
    cfg = os.path.expanduser(str(app_config.get("stick_spectrum_python") or "").strip())
    if cfg:
        cands.append(cfg)
    cands += [sys.executable, shutil.which("python3"), "/usr/bin/python3", "/usr/local/bin/python3"]
    ve = os.environ.get("VIRTUAL_ENV")
    if ve:
        cands.append(os.path.join(ve, "bin", "python3"))
    for sub in ("~/venv", "~/.venv", "~/sdr/venv", "~/sdr/.venv", "~/sdr/leandvb-GUI/venv", "~/sdr/leandvb-GUI/.venv"):
        cands.append(os.path.join(os.path.expanduser(sub), "bin", "python3"))
    seen = set()
    for c in cands:
        if not c or c in seen or not os.path.isfile(c):
            continue
        seen.add(c)
        try:
            r = subprocess.run([c, "-c", "import websockets, numpy, rtlsdr"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            _stick["python"] = c
            olog("Scan-Spektrum: verwende Python %s" % c)
            return c
    return None

def _stick_spec_start():
    if not HAVE_WEBSOCKET:
        olog("Scan-Spektrum: websocket-client fehlt (pip3 install websocket-client).")
        return False
    owned = False
    if not _stick_port_open():
        if _stick_rtl_busy():
            olog("Scan-Spektrum: der RTL-Stick wird gerade vom rtl_sdr-Empfang belegt - bitte erst den "
                 "rtl_sdr-Empfang beenden.")
            return False
        script = os.path.expanduser(str(app_config.get("stick_spectrum_script") or ""))
        if not os.path.isfile(script):
            olog("Scan-Spektrum: Server-Skript nicht gefunden: %s (app_config: stick_spectrum_script)" % script)
            return False
        py = _stick_find_python()
        if not py:
            olog("Scan-Spektrum: kein Python mit den Modulen websockets, numpy und rtlsdr gefunden. Installieren: "
                 "pip3 install websockets numpy pyrtlsdr --break-system-packages  (oder app_config "
                 "'stick_spectrum_python' auf das passende python3 setzen).")
            return False
        logf = os.path.join(parameters_path, "stick_spektrum.log")
        try:
            lf = open(logf, "ab")
            _stick["proc"] = subprocess.Popen(
                [py, script, "--correction-khz", "%g" % float(app_config.get("stick_spectrum_corr_khz") or 0),
                 "--ws-port", str(_stick_port())],
                cwd=os.path.dirname(script), stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                close_fds=True, start_new_session=True)
        except OSError as e:
            olog("Scan-Spektrum: Server konnte nicht gestartet werden: %s" % e)
            return False
        owned = True
        olog("Scan-Spektrum: Server gestartet (Log: %s)." % logf)
    else:
        olog("Scan-Spektrum: Server laeuft bereits auf Port %d - verbinde nur." % _stick_port())
    _stick["owned"] = owned
    _stick["on"] = True
    _stick["fft"] = None
    _stick["gen"] += 1
    threading.Thread(target=_stick_ws_loop, args=(_stick["gen"],), daemon=True).start()
    return True

def _stick_spec_stop():
    was_on = _stick["on"]
    _stick["on"] = False
    _stick["gen"] += 1
    ws = _stick.get("ws")
    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass
    proc = _stick.get("proc")
    if proc is not None and proc.poll() is None and _stick.get("owned"):
        try:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass
    _stick["proc"] = None
    _stick["fft"] = None
    return was_on

def _spq_set(mode, save=True):
    """Menue "Spektrum": Quelle waehlen (batc / stick / auto)"""
    _spq["mode"] = mode
    if save:
        app_config["spektrum_quelle"] = mode
        app_config_save()
    try:
        _spq_var.set(mode)
    except NameError:
        pass
    olog("Spektrum-Quelle: %s." % {"batc": "nur BATC-Server", "stick": "eigener RTL-Stick (Scan)", "auto": "Auto (BATC, bei Ausfall eigener Stick)", "onair": "OnAir (SDRplay A)", "onair_pluto": "OnAir (PlutoSDR)"}[mode])
    _spq_tick(once=True)

def _spq_tick(once=False):
    try:
        now, mode = time.time(), _spq["mode"]
        batc_ok = now - _spq["batc_t"] < 8
        if mode in ("onair", "onair_pluto"):
            kind = "pluto" if mode == "onair_pluto" else "sdrplay"
            if _spq["active"] == "onair" and _spo["kind"] != kind:      # Wechsel SDRplay <-> Pluto
                _spo["gen"] += 1
                _spq["active"] = "batc"
            if _spq["active"] != "onair" and now - _spo["fail_t"] > 60:
                if _spq["active"] == "stick" and _stick["on"] and _spq["auto_started"]:
                    _stick_spec_stop()
                _spq["active"] = "onair"
                _spo_start("pluto" if mode == "onair_pluto" else "sdrplay")
            return
        if _spq["active"] == "onair":                      # OnAir verlassen
            _spq["active"] = "batc"
            _spo_stop()
            olog("Spektrum: OnAir-Quelle beendet.")
        if mode == "stick" or (mode == "auto" and _spq["active"] == "batc" and now - _spq["t0"] > 25 and now - _spq["batc_t"] > 20):
            if _spq["active"] != "stick":
                if not _stick["on"]:
                    if not _stick_spec_start():
                        _spq["active"] = "batc"
                        return
                    _spq["auto_started"] = True
                _spq["active"] = "stick"
                olog("Spektrum: Quelle ist jetzt der eigene RTL-Stick%s." % (" (BATC ohne Daten)" if mode == "auto" else ""))
        elif _spq["active"] == "stick" and (mode == "batc" or (mode == "auto" and batc_ok)):
            _spq["active"] = "batc"
            if _spq["auto_started"] and _stick["on"] and _stick_canvas.winfo_manager() == "":
                _stick_spec_stop()                         # Server nur beenden, wenn das Scan-Spektrum nicht ohnehin angezeigt wird
            _spq["auto_started"] = False
            olog("Spektrum: Quelle ist wieder der BATC-Server.")
        if _spq["active"] == "stick" and not _stick["on"]:
            _spq["active"] = "batc"                        # Scan-Spektrum von Hand ausgeschaltet: naechste Runde startet es bei Bedarf neu
        if _spq["active"] == "stick" and _stick["on"] and _stick.get("owned") and _stick.get("proc") is not None and _stick["proc"].poll() is not None:
            _stick_spec_stop()                             # Server ist abgestuerzt: zurueck auf BATC
            _spq["active"] = "batc"
            olog("Spektrum: Stick-Server beendet - zurueck auf den BATC-Server.")
    except Exception as e:
        dlog("Spektrum-Quelle: %r" % (e,))
    if not once:
        root.after(3000, _spq_tick)


#----- Quelle "OnAir (SDRplay)": Spektrum ueber die Remote-API eines eigenen OnAir-Fensters (zwei versetzte 8-MHz-Durchlaeufe) -----
# OnAir liefert GET /api/v1/spectrum (dBFS, Mitte, Abtastrate). Die nutzbare Breite einer 8-MS/s-Aufnahme ist ~6,8 MHz, das Band ist 9 MHz breit:
# darum zwei Aufnahmen mit versetzter Mitte (ZF), im Ueberlappungsbereich auf gleichen Pegel gebracht und auf das 922-Punkte-Raster des BATC-Bilds gelegt.
# Die API kennt keine AGC-Einschaltung (nur "gain": dB schaltet sie aus) - "Pegel automatisch" regelt deshalb die GUI selbst nach (Median auf SPO_TARGET_DB).
_spo = {"kind": "sdrplay", "rate": 8.0, "centers": (955.0,), "usable": 6.8, "gen": 0, "proc": None, "gain": None, "lo": 9540.0, "fail_t": 0.0, "last_cal": 0.0, "started_by_us": False}
SPO_KIND = {"sdrplay": {"name": "SDRplay", "ant": "antenna A", "rate": 8.0, "label": "SDRplay A"},      # Zielrate in MS/s (SDRplay: Filter ~8 MHz)
            "pluto":   {"name": "Pluto",   "ant": "usb",       "rate": 12.0, "label": "PlutoSDR"}}     # Pluto: 12 MS/s decken die 9 MHz in EINER Aufnahme
SPO_USABLE_FRAC = 0.85            # nutzbarer Anteil der Abtastrate (Filterflanken weglassen)
SPO_TARGET_DB = -70.0             # Pegel-Ziel: Median des Rauschbodens in dBFS (Bake dann ~ -60)
SPO_OFFSET = 106.0                # dBFS + Offset = "BATC-dB" (Rauschen ~36, Bake ~46, max 48 = uint16)

def _spo_port():
    try:
        return int(app_config.get("spektrum_onair_port") or 8790)
    except (TypeError, ValueError):
        return 8790

def _spo_api(method, path, body=None, timeout=8):
    """OnAir-Remote-API; POST immer mit JSON-Koerper (auch leer), Fehlerantworten (4xx) kommen als {"error": ...} zurueck, None = nicht erreichbar"""
    try:
        data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
        req = urllib.request.Request("http://127.0.0.1:%d/api/v1%s" % (_spo_port(), path), data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return {"error": "HTTP %s" % e.code}
    except Exception:
        return None

def _spo_launch():
    """eigenes OnAir-Fenster (eigene Konfig, eigener API-Port) starten, falls noch keines antwortet"""
    if _spo_api("GET", "/status") is not None:
        return True
    binp = _onair_app_bin()
    if not binp:
        olog("Spektrum (OnAir): kein OnAir-Programm gefunden.")
        return False
    cfgdir = os.path.join(parameters_path, "onair_cfg", "spektrum")
    f = os.path.join(cfgdir, "onair", "settings.conf")
    try:
        os.makedirs(os.path.dirname(f), exist_ok=True)
        if not os.path.isfile(f) and os.path.isfile(os.path.expanduser("~/.config/onair/settings.conf")):
            shutil.copyfile(os.path.expanduser("~/.config/onair/settings.conf"), f)
        _onair_prefs_set(f, {"apiOn": "1", "apiPort": str(_spo_port()), "apiLan": "0"})
        env = dict(os.environ, XDG_CONFIG_HOME=cfgdir)
        lf = open(os.path.join(parameters_path, "onair_spektrum.log"), "ab")
        _spo["proc"] = subprocess.Popen([binp], env=env, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, close_fds=True, start_new_session=True)
        _spo["started_by_us"] = True
    except OSError as e:
        olog("Spektrum (OnAir): Start fehlgeschlagen: %s" % e)
        return False
    for _ in range(60):
        time.sleep(0.5)
        if _spo_api("GET", "/status") is not None:
            return True
    olog("Spektrum (OnAir): OnAir antwortet nicht auf Port %d (API in der Konfig aktiv?)." % _spo_port())
    return False

def _spo_stop_proc():
    p = _spo.get("proc")
    _spo["proc"] = None
    if p is not None and p.poll() is None and _spo.get("started_by_us"):
        try:
            _spo_api("POST", "/stop")
            p.terminate()
            p.wait(timeout=8)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    _spo["started_by_us"] = False

def _spo_plan(dev):
    """Abtastrate (aus den Grenzen des Geraets laut /devices) und Mitten der Aufnahmen so waehlen, dass das ganze Band 10490,5-10499,5 MHz (ZF = RF - LO) abgedeckt ist"""
    k = SPO_KIND[_spo["kind"]]
    lo_hz, hi_hz = float(dev.get("minRateHz") or 0), float(dev.get("maxRateHz") or 0)
    rate = k["rate"] * 1e6
    if hi_hz:
        rate = min(rate, hi_hz)
    rate = max(rate, lo_hz)
    usable = SPO_USABLE_FRAC * rate / 1e6
    f0, f1 = BATC_START_FREQ - _spo["lo"], BATC_START_FREQ + BATC_BANDWIDTH - _spo["lo"]
    n = max(1, int(math.ceil((f1 - f0) / (usable - 0.5))))               # 0,5 MHz Ueberlappung zum Pegelabgleich
    if n == 1:
        centers = ((f0 + f1) / 2.0,)
    else:
        a, b = f0 + usable / 2.0, f1 - usable / 2.0
        centers = tuple(a + (b - a) * i / (n - 1) for i in range(n))
    _spo["rate"], _spo["usable"], _spo["centers"] = rate / 1e6, usable, centers
    olog("Spektrum (OnAir %s): Rate %.1f MS/s (Geraet %.2f-%.2f), %d Aufnahme(n) bei %s MHz ZF." % (
        k["label"], rate / 1e6, lo_hz / 1e6, hi_hz / 1e6, n, ", ".join("%.2f" % c for c in centers)))

def _spo_capture(center, gen):
    """Mitte setzen und eine saubere Spektrumzeile holen (erst nach Umschalten + Einschwingen)"""
    st0 = _spo_api("GET", "/spectrum?bins=911") or {}
    seq0 = st0.get("seq", 0)
    _spo_api("POST", "/frequency", {"mhz": center})
    for _ in range(30):
        if _spo["gen"] != gen:
            return None
        time.sleep(0.12)
        d = _spo_api("GET", "/spectrum?bins=911")
        if d and abs(d.get("centerHz", 0) / 1e6 - center) < 0.001 and d.get("seq", 0) >= seq0 + 3 and d.get("dbfs"):
            return d
    return None

def _spo_median(d):
    v = sorted(d["dbfs"][len(d["dbfs"]) // 10: -len(d["dbfs"]) // 10])
    return v[len(v) // 2]

def _spo_calibrate(gen, why=""):
    """Verstaerkung so einstellen, dass der Rauschboden bei SPO_TARGET_DB liegt (max. 5 Schritte)"""
    stt = _spo_api("GET", "/status") or {}
    gmin, gmax = stt.get("gainMinDb", 0), stt.get("gainMaxDb", 100)
    g = _spo["gain"] if _spo["gain"] is not None else (stt.get("gainDb") or 50)
    for _ in range(5):
        if _spo["gen"] != gen:
            return
        _spo_api("POST", "/gain", {"db": g})
        d = _spo_capture(_spo['centers'][len(_spo['centers']) // 2], gen)
        if not d:
            break
        err = SPO_TARGET_DB - _spo_median(d)
        if abs(err) < 1.5 or d.get("clipFraction", 0) > 0.01 and err > 0:
            break
        g = max(gmin, min(gmax, g + err))
    _spo["gain"], _spo["last_cal"] = g, time.time()
    app_config["spektrum_onair_gain"] = round(g, 1)
    olog("Spektrum (OnAir): Pegel abgeglichen%s - Verstaerkung %.0f dB." % ((" (" + why + ")") if why else "", g))

def _spo_stitch(caps):
    """Aufnahmen -> 922 Punkte auf dem BATC-Raster (RF 10490,5 + 9 MHz), Werte wie BATC (10*log10(v)); Nachbaraufnahmen im Ueberlappungsbereich pegelgleich"""
    lo = _spo["lo"]
    n = 922
    half = _spo["usable"] / 2.0
    caps = sorted(caps, key=lambda d: d["centerHz"])
    def val(d, if_mhz):
        nb = len(d["dbfs"])
        x = (if_mhz - (d["centerHz"] / 1e6 - d["sampleRateHz"] / 2e6)) / (d["sampleRateHz"] / 1e6) * nb
        return d["dbfs"][max(0, min(nb - 1, int(x)))]
    corr = [0.0]
    for k in range(1, len(caps)):
        a, b = caps[k - 1], caps[k]
        lo_f, hi_f = b["centerHz"] / 1e6 - half, a["centerHz"] / 1e6 + half
        ov = [lo_f + x * 0.1 for x in range(int(max(0.0, hi_f - lo_f) * 10))]
        if len(ov) > 5:
            dd = sorted((val(a, f) + corr[k - 1]) - val(b, f) for f in ov)
            corr.append(dd[len(dd) // 2])
        else:
            corr.append(corr[k - 1])
    out = []
    for i in range(n):
        if_mhz = BATC_START_FREQ + i * BATC_BANDWIDTH / n - lo
        k = min(range(len(caps)), key=lambda q: abs(if_mhz - caps[q]["centerHz"] / 1e6))
        db = val(caps[k], if_mhz) + corr[k]
        out.append(int(max(1.0, min(65535.0, 10 ** ((db + SPO_OFFSET) / 10.0)))))
    return tuple(out)

def _spo_loop(gen):
    try:
        if not _spo_launch():
            raise RuntimeError("OnAir nicht erreichbar")
        devs = _spo_api("GET", "/devices") or []
        k = SPO_KIND[_spo["kind"]]
        cand = [x for x in devs if k["name"] in x.get("name", "")]
        dev = next((x for x in cand if k["ant"] in x.get("name", "")), None) or (cand[0] if cand else None)
        if dev is None:
            raise RuntimeError("kein %s in OnAir gefunden" % k["name"])
        idx = dev["index"]
        _spo_plan(dev)
        stt = _spo_api("GET", "/status") or {}
        if not stt.get("running") or stt.get("deviceIndex") != idx:
            if stt.get("running"):
                _spo_api("POST", "/stop")
                time.sleep(2)
            r = _spo_api("POST", "/start", {"device": idx})
            if not r or "error" in r:
                raise RuntimeError("%s nicht startbar (belegt?): %s" % (k["label"], r))
            time.sleep(3)
        _spo_api("POST", "/samplerate", {"msps": _spo["rate"]})
        time.sleep(1)
        _spo["gain"] = app_config.get("spektrum_onair_gain")
        _spo_calibrate(gen, "Start")
        olog("Spektrum: Quelle ist jetzt OnAir (%s), %d Aufnahme(n)." % (k["label"], len(_spo["centers"])))
        while _spo["gen"] == gen:
            caps = []
            for c in _spo["centers"]:
                d = _spo_capture(c, gen)
                if not d:
                    break
                caps.append(d)
            if len(caps) == len(_spo["centers"]):
                batc_analyze_signals(_spo_stitch(caps))
            else:
                time.sleep(1)
            if str(app_config.get("spektrum_onair_pegel") or "fest") == "auto" and time.time() - _spo["last_cal"] > 40:
                _spo_calibrate(gen, "automatisch")
            if _spo.get("recal"):
                _spo["recal"] = False
                _spo_calibrate(gen, "von Hand")
    except Exception as e:
        olog("Spektrum (OnAir): %s - zurueck auf BATC." % (e,))
        _spo["fail_t"] = time.time()
        root.after(0, _spq_set, "batc", False)
    finally:
        if _spo["gen"] == gen:
            _spq["active"] = "batc" if _spq["active"] == "onair" else _spq["active"]

def _spo_start(kind="sdrplay"):
    _spo["gen"] += 1
    _spo["kind"] = kind
    try:
        _spo["lo"] = float(lnblo.get())
    except Exception:
        pass
    threading.Thread(target=_spo_loop, args=(_spo["gen"],), daemon=True).start()

def _spo_stop():
    _spo["gen"] += 1
    threading.Thread(target=_spo_stop_proc, daemon=True).start()

def _spo_recal():
    _spo["recal"] = True

_spq_var = tk.StringVar(value="batc")
_menu_spq = tk.Menu(_menubar, tearoff=False)
_menubar.add_cascade(label="Spektrum", menu=_menu_spq)
for _v, _t in (("batc", "BATC-Server (Standard)"), ("stick", "RTL-Stick-Scan (HB9IIU-Server)"), ("auto", "Auto: BATC, bei Ausfall RTL-Stick-Scan"),
               ("onair", "OnAir (SDRplay A, 2 Durchlaeufe)"), ("onair_pluto", "OnAir (PlutoSDR, 1 Durchlauf)")):
    _menu_spq.add_radiobutton(label=_t, value=_v, variable=_spq_var, command=lambda: _spq_set(_spq_var.get()))
_menu_spq.add_separator()
_spo_pegel_var = tk.StringVar(value=str(app_config.get("spektrum_onair_pegel") or "fest"))
def _spo_pegel_set():
    app_config["spektrum_onair_pegel"] = _spo_pegel_var.get()
    app_config_save()
    olog("Spektrum (OnAir) Pegel: %s." % ("automatisch nachregeln" if _spo_pegel_var.get() == "auto" else "einmal abgleichen, dann fest"))
_menu_spq.add_radiobutton(label="OnAir-Pegel: einmal abgleichen (fest)", value="fest", variable=_spo_pegel_var, command=_spo_pegel_set)
_menu_spq.add_radiobutton(label="OnAir-Pegel: automatisch nachregeln", value="auto", variable=_spo_pegel_var, command=_spo_pegel_set)
_menu_spq.add_command(label="OnAir-Pegel jetzt neu abgleichen", command=lambda: _spo_recal())
_spq["mode"] = str(app_config.get("spektrum_quelle") or "batc")
if _spq["mode"] not in ("batc", "stick", "auto", "onair", "onair_pluto"):
    _spq["mode"] = "batc"
_spq_var.set(_spq["mode"])
root.after(5000, _spq_tick)

def _stick_spec_toggle():
    if _stick["on"]:
        _stick_spec_stop()
        try:
            _stick_canvas.pack_forget()
            btn_stick_spec.configure(style="TButton")
        except Exception:
            pass
        olog("Scan-Spektrum ausgeschaltet.")
        return
    if not _stick_spec_start():
        return
    # gleiche Hoehe wie das BATC-Spektrum oben (mindestens 300 px)
    try:
        _stick_canvas.configure(height=max(300, _spectrum_canvas.winfo_height()))
    except Exception:
        pass
    _stick_canvas.pack(fill='x', pady=(4, 0))
    btn_stick_spec.configure(style="RecOn.TButton")
    _stick["drawn"] = None
    root.after(500, _stick_spec_tick)

def _stick_spec_tick():
    if not _stick["on"]:
        return
    try:
        # Server beendet sich selbst (z.B. Stick nicht gefunden)? -> melden und ausschalten
        proc = _stick.get("proc")
        if proc is not None and _stick.get("owned") and proc.poll() is not None:
            olog("Scan-Spektrum: Server beendet (Exit %s) - siehe stick_spektrum.log." % proc.returncode)
            try:
                with open(os.path.join(parameters_path, "stick_spektrum.log"), "rb") as _lf:
                    _lf.seek(0, os.SEEK_END)
                    _lf.seek(max(0, _lf.tell() - 1500))
                    _tail = [l.strip() for l in _lf.read().decode("utf-8", "replace").splitlines() if l.strip()][-4:]
                for _l in _tail:
                    olog("Scan-Spektrum-Server: " + _l[:200])
                    print("Scan-Spektrum-Server: " + _l[:200], flush=True)
            except OSError:
                pass
            _stick_spec_toggle()
            return
        _stick_spec_draw()
    except Exception as e:
        dlog("Scan-Spektrum Zeichnen: %r" % (e,))
    root.after(500, _stick_spec_tick)

_GRAD_STOPS = [(0.0, (11, 31, 138)), (0.2, (11, 111, 216)), (0.4, (9, 192, 200)),
               (0.55, (60, 200, 60)), (0.72, (232, 224, 32)), (0.86, (245, 140, 20)), (1.0, (232, 28, 28))]

def _grad_color(t):
    """Farbe des Spektrum-Verlaufs: unten dunkelblau, ueber cyan/gruen/gelb/orange nach rot an der Spitze."""
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(_GRAD_STOPS, _GRAD_STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return "#%02x%02x%02x" % tuple(int(c0[i] + (c1[i] - c0[i]) * f) for i in range(3))
    return "#%02x%02x%02x" % _GRAD_STOPS[-1][1]

_grad_cache = {}
def _gradient_fill(c, xs, ys, baseline_y, ref_h, band_px=3.0):
    """Fuellt die Flaeche unter der Kurve (xs/ys in Canvas-Koordinaten) mit einem senkrechten, praktisch
    stufenlosen Farbverlauf (Baender von ~band_px Pixeln Hoehe). Rot erreicht die Farbe bei der Hoehe
    ref_h ueber der Basislinie; die Farbe haengt nur von der absoluten Hoehe ab (gleiche Hoehe = gleiche
    Farbe, wie im Original). Jedes Band wird nur ueber den Spalten gezeichnet, die es erreichen - dadurch
    bleibt die Zeichenzeit klein, obwohl es viele Baender gibt."""
    n = len(xs)
    if n < 2:
        return
    hs = [max(0.0, baseline_y - y) for y in ys]
    maxh = max(hs)
    if maxh <= 0.5:
        return
    ref_h = max(20.0, ref_h)
    band_h = max(1.5, float(band_px), maxh / 60.0)       # hoechstens ~60 Baender (weniger Polygone)
    nb = int(maxh // band_h) + 1
    active = [i for i in range(n) if hs[i] > 0.0]        # schrumpft von Band zu Band
    ckey = (round(band_h, 2), round(ref_h, 1))
    if _grad_cache.get("key") != ckey:
        _grad_cache.clear()
        _grad_cache["key"] = ckey
    for k in range(nb):
        lo = k * band_h
        hi = lo + band_h
        color = _grad_cache.get(k)
        if color is None:
            color = _grad_cache[k] = _grad_color((lo + band_h / 2.0) / ref_h)
        active = [i for i in active if hs[i] > lo]
        if not active:
            break
        a = 0
        na = len(active)
        while a < na:
            i = active[a]
            b = a
            while b + 1 < na and active[b + 1] == active[b] + 1:
                b += 1
            j = active[b] + 1
            a = b + 1
            # linke/rechte Kante: Schnittpunkt der Flanke mit der Bandunterkante
            if i > 0:
                d = hs[i] - hs[i - 1]
                xl = xs[i - 1] + ((lo - hs[i - 1]) / d if d else 0.0) * (xs[i] - xs[i - 1])
            else:
                xl = xs[0]
            if j < n:
                d = hs[j - 1] - hs[j]
                xr = xs[j - 1] + ((hs[j - 1] - lo) / d if d else 0.0) * (xs[j] - xs[j - 1])
            else:
                xr = xs[n - 1]
            ylo = baseline_y - lo
            pts = [xl, ylo]
            for m in range(i, j):
                pts.append(xs[m])
                pts.append(baseline_y - (hs[m] if hs[m] < hi else hi))
            pts.extend([xr, ylo, xr, ylo + 1.0, xl, ylo + 1.0])     # 1 px Ueberlappung nach unten: keine Nahtlinien
            c.create_polygon(pts, fill=color, outline="")
            i = j

def _stick_spec_draw():
    c = _stick_canvas
    fft = _stick["fft"]
    width, height = c.winfo_width(), c.winfo_height()
    sig = (id(fft), width, height)
    if sig == _stick["drawn"]:
        return
    _stick["drawn"] = sig
    c.delete("all")
    if width <= 1 or height <= 1:
        return
    plot_h = height - _SPECTRUM_BOTTOM
    baseline_y = plot_h * (7.0 / 8.0)
    for i in range(1, 5):
        y = i * (plot_h / 4.0) - (plot_h / 6.0)
        if 0 <= y <= plot_h:
            c.create_line(35, y, width - 35, y, fill="#888888", dash=(5, 10))
            if i != 4:
                lbl = "%ddB" % (5 * (4 - i))
                c.create_text(20, y, text=lbl, fill="#ffffff", font=('', 8), anchor='c')
                c.create_text(width - 20, y, text=lbl, fill="#ffffff", font=('', 8), anchor='c')
    for i in range(0, 18, 2):
        f = BATC_START_FREQ + (1 + i) * 0.5
        x = _spectrum_freq_to_x(f, width)
        c.create_line(x, 0, x, plot_h * (7.0 / 8.0), fill="#888888", dash=(5, 20))
        c.create_text(x, 10, text="%.3f" % f, fill="#ffffff", font=('', 8, 'bold'), anchor='c')
    c.create_line(_spectrum_freq_to_x(BATC_BEACON_NOMINAL_MHZ, width), 0,
                  _spectrum_freq_to_x(BATC_BEACON_NOMINAL_MHZ, width), plot_h, fill="#888833", dash=(2, 2))
    if not fft:
        c.create_text(width // 2, plot_h // 2, text="Scan-Spektrum: warte auf RTL-Stick ...",
                      fill="#888888", font=('', 9))
        c.create_text(8, height - 4, text="RTL-Stick", fill="#03d7fc", font=('', 8, 'bold'), anchor='sw')
        return
    n = len(fft)
    full = float(_SPECTRUM_FULLSCALE)
    xs, ys = [0], [baseline_y]
    for x in range(0, width, 2):
        i0 = min(n - 1, int(x / width * n))
        i1 = max(i0 + 1, min(n, int((x + 2) / width * n)))
        frac = min(max(fft[i0:i1]) / full, 1.0)
        xs.append(x)
        ys.append(plot_h - frac * plot_h if frac > _SPECTRUM_DRAW_THRESH else baseline_y)
    xs.append(width); ys.append(baseline_y)
    # Scan-Spektrum: immer mit Farbverlauf (rot ab 11 dB ueber der Basislinie)
    _gradient_fill(c, xs, ys, baseline_y, plot_h * 11.0 / 20.0)
    try:
        min_db = float(app_config.get("stick_spectrum_min_contrast_db") or 0.0)
    except (TypeError, ValueError):
        min_db = 2.5
    for sg in _spectrum_detect_signals(fft, width, plot_h):
        # Rauschhuegel/Stoerspitzen ausblenden: Pegel muss genug ueber dem lokalen Rauschen (unteres
        # Quartil der Umgebung neben dem Signal) liegen
        i0 = int(sg["start_px"] / width * n); i1 = max(i0 + 1, int(sg["end_px"] / width * n))
        env = list(fft[max(0, i0 - 60):i0]) + list(fft[i1:min(n, i1 + 60)])
        if env:
            env.sort()
            floor = env[len(env) // 4]
            if (sg["raw"] - floor) / _SPECTRUM_SCALE_DB < min_db:
                continue
        mid = (sg["start_px"] + sg["end_px"]) / 2.0
        top = sg["top_px"]
        c.create_rectangle(sg["start_px"], top, sg["end_px"], baseline_y, outline="#ffffff")
        c.create_text(mid, top - 12, text="%s %.3f" % (_spectrum_print_sr(sg["sr_mhz"]), sg["freq"]),
                      fill="#ffffff", font=('', 8))
    c.create_text(8, height - 4, text="RTL-Stick (Scan-Spektrum)", fill="#03d7fc", font=('', 8, 'bold'), anchor='sw')

_stick_canvas = tk.Canvas (frm_batc_spectrum, height=STICK_SPEC_HEIGHT, bg="#0b0f2b", highlightthickness=0)
btn_stick_spec = ttk.Button (frm_batc_spectrum_head, text="Scan-Spektrum", command=_stick_spec_toggle)
# ganz links in der Kopfzeile ueber dem Spektrum (dort immer sichtbar)
btn_stick_spec.pack (side='left', padx=(0, 4), before=frm_batc_spectrum_head.winfo_children()[0])
# "Farbverlauf" direkt rechts neben "Scan-Spektrum"
btn_spec_gradient.pack_forget()
btn_spec_gradient.pack (side='left', padx=(0, 10), after=btn_stick_spec)

_spectrum_canvas = tk.Canvas (frm_batc_spectrum, height=_SPECTRUM_HEIGHT, bg="black", highlightthickness=0)
_spectrum_canvas.pack (fill='both', expand=True)
_spectrum_canvas.bind ("<Button-1>", _spectrum_on_click)
_spectrum_canvas.bind ("<Button-3>", _spectrum_on_rightclick)
_spectrum_canvas.bind ("<Motion>", _spectrum_on_motion)
_spectrum_canvas.bind ("<Leave>", _spectrum_on_leave)
root.after(200, _spectrum_tick)

# Bake-Knopf sofort anzeigen, auch OHNE Klick auf "Verbinden" (batc_rebuild_list wird sonst
# erst durch batc_poll() nach batc_start() aufgerufen) - danach uebernimmt batc_poll() weiter.
# (Die Signal-Knoepfe landen jetzt direkt in den pro-Ziel-Containern _target_siglist_frames,
# siehe deren Aufbau weiter oben - kein eigener frm_batc_list mehr noetig.)
batc_rebuild_list()

# Live-Bake-Erkennung/LO-Korrektur: rohe Longmynd-Status-UDP-Pakete mitlesen (Port 4002/4003)
# und einmal pro Sekunde pruefen/ggf. lnblo korrigieren.
if "longmynd" in _active_targets:
    threading.Thread(target=_lm_status_listener,
                      args=("longmynd", cliargs.longmynd_status_port, cliargs.longmynd_status_forward_port),
                      daemon=True).start()
if "longmynd2" in _active_targets:
    threading.Thread(target=_lm_status_listener,
                      args=("longmynd2", cliargs.longmynd2_status_port, cliargs.longmynd2_status_forward_port),
                      daemon=True).start()
if "longmynd_p4" in _active_targets:
    threading.Thread(target=_lm_status_listener,
                      args=("longmynd_p4", cliargs.longmyndp4_status_port, cliargs.longmyndp4_status_forward_port),
                      daemon=True).start()
    threading.Thread(target=_longmyndp4_check_stop_start_scripts, daemon=True).start()
    threading.Thread(target=_longmyndp4_tuner_loop, daemon=True).start()
if "octagon" in _active_targets:
    _octagon_status_tick()
_lo_correct_tick()
root.after(5000, _lm_external_tune_tick)
_marquee_tick()
threading.Thread(target=_player_watchdog_loop, daemon=True).start()
threading.Thread(target=_framewatch_loop, daemon=True).start()
if "octagon" in _active_targets:
    threading.Thread(target=_octagon_recovery_loop, daemon=True).start()

def _octagon_startcheck():
    """Beim Start: ist die Octagon wirklich bedienbar? Bis zu ~45 s warten auf: Ping ok UND (Web-Schnittstelle
    antwortet ODER dvb_direct auf der Box antwortet). Sonst Meldung ausgeben und den Automatik-Haken des
    Octagon abschalten (sonst wuerde die Verteilung auf eine tote Box tunen)."""
    host = cliargs.octagon_ip
    t0 = time.time()
    ping_ok = False
    web_ok = False
    while time.time() - t0 < 45.0:
        ping_ok = _ping_host(host, timeout_s=2)
        if ping_ok:
            web_ok = bool(_octagon_reachable)
            if not web_ok:
                try:
                    web_ok = (_oct_direct_send("PING", timeout=2.0) == "OK dvb_direct")
                except Exception:
                    web_ok = False
            if web_ok:
                break
        time.sleep(3.0)
    if ping_ok and web_ok:
        print("Octagon %s: erreichbar." % host, flush=True)
        return
    grund = "Ping ohne Antwort" if not ping_ok else "Ping ok, aber Web-Schnittstelle antwortet nicht"
    msg = "Octagon %s beim Start NICHT erreichbar (%s) - Automatik fuer Octagon wird abgeschaltet." % (host, grund)
    print(msg, flush=True)
    def _apply():
        olog(msg)
        v = _batc_auto.get("octagon")
        if v is not None:
            v.set(False)
            parameters_save()
    root.after(0, _apply)

if "octagon" in _active_targets:
    threading.Thread(target=_octagon_startcheck, daemon=True).start()

cmb_frequency.focus_set()

print("Aktive Ziele: " + ", ".join(_TARGET_LABEL[t] for t in _active_targets))
if "longmynd" in _active_targets:
    print("[longmynd-status] VLC-Snapshots (Telnet Port %d) werden gespeichert in: %s" %
          (cliargs.longmynd_vnc_port, _vlc_snapshot_path_hint()))
if "longmynd2" in _active_targets:
    print("[longmynd2-status] VLC-Snapshots (Telnet Port %d) werden gespeichert in: %s" %
          (cliargs.longmynd2_vnc_port, _vlc_snapshot_path_hint()))
if "longmynd_p4" in _active_targets:
    print("[longmynd-p4-status] VLC-Snapshots (Telnet Port %d) werden gespeichert in: %s" %
          (cliargs.longmyndp4_vnc_port, _vlc_snapshot_path_hint()))
_spectrum_callsign_cache_load()
for _svc_target in _LM_TARGETS + ("octagon", "rtl_sdr") + _UDPRX_TARGETS:
    if _svc_target in _active_targets:
        threading.Thread(target=_service_name_poll_loop, args=(_svc_target,), daemon=True).start()
if "octagon" in _active_targets:
    octagon_ensure_channels()
if "longmynd" in _active_targets and cliargs.longmyndterminal:
    # Status-Dashboard nur noch bei explizitem -longmyndterminal starten - ohne dieses Flag
    # (Default) wird gar kein Status-Fenster mehr geoeffnet (das eingebettete Tkinter-Fenster
    # ist erstmal zurueckgestellt, das externe Terminal-Fenster laeuft nur noch auf Wunsch).
    longmynd_status_open()
if cliargs.conBatc or app_config.get("batc_auto_verbinden"):
    print("Verbinde automatisch mit dem BATC-Server (%s) ..." % ("-conBatc" if cliargs.conBatc else "beim letzten Beenden war 'Verbinden' aktiv"))
    batc_start()

# Octagon-Direktmodus: -octagonDirektQRG bzw. der zuletzt gespeicherte Zustand - dvb_direct.py
# auf der Box hochfahren (unabhaengig davon, ob die BATC-Verbindung steht).
_oct_direct_cli_apply()
if "longmynd2" in _active_targets:
    root.after(2000, lambda: threading.Thread(target=_lm2_local_ensure, daemon=True).start())
    threading.Thread(target=_lm2_watchdog_loop, daemon=True).start()
root.after(60000, _callsign_check_tick)
root.after(2000, _main_minsize_tick)

def _nnv_label_tick():
    """Knopf 'NeuNeuVerteilen' zeigt die Zahl der gerade nicht versorgten Signale (nur wenn > 0)."""
    try:
        with _batc_lock:
            live = [st["freq"] for st in _batc_sig_state.values()
                    if st["gone_since"] is None and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5]
        cur = [_target_cur_freq.get(t) for t in _active_targets if _target_cur_freq.get(t) is not None]
        n = sum(1 for f in live if not any(abs(f - c) <= BATC_SIG_TOL for c in cur))
        txt = "NeuNeuVerteilen (%d)" % n if n > 0 else "NeuNeuVerteilen"
        if btn_batc_redistribute_rest.cget("text") != txt:
            btn_batc_redistribute_rest.configure(text=txt)
    except Exception as e:
        dlog("NeuNeuVerteilen-Anzeige: %r" % (e,))
    root.after(2000, _nnv_label_tick)
root.after(3000, _nnv_label_tick)

_vl = {"t": {}, "born": {}, "served": set(), "last_cmp": 0.0}

def _vlog_loop():
    """Hintergrund, alle 2 s: Zustandswechsel je Tuner protokollieren + alle 60 s Abgleich mit der BATC-Liste."""
    time.sleep(15)
    while True:
        try:
            snap = _web.get("snap") or {}
            tl = {x["id"]: x for x in snap.get("targets", [])}
            now = time.time()
            for t, x in tl.items():
                st = _tsrate_state.get(t)
                data = bool(st and time.monotonic() - st["last_rx"] < 3.0)
                player = _autoclose_player_running(t)
                fws = _fw_state.get(t) or {}
                cur = {"freq": round(x["freq"], 2) if x.get("freq") else None, "lock": x.get("lock"),
                       "data": data, "player": player, "call": x.get("call") or "",
                       "bild": bool(fws and now - fws.get("chg", 0) < 15)}
                old = _vl["t"].get(t)
                lab = x["label"]
                f, sr = x.get("freq"), x.get("sr")
                if old is not None:
                    if cur["lock"] is True and old["lock"] is not True:
                        _vlog("lock", f, sr, lab, cur["call"], "%.0f s nach Einstellen" % (now - _target_dispatch_t.get(t, now)))
                    elif cur["lock"] is False and old["lock"] is True:
                        _vlog("lock_weg", f, sr, lab, old["call"], "")
                    if cur["data"] != old["data"]:
                        _vlog("stream" if cur["data"] else "stream_weg", f, sr, lab, cur["call"],
                              ("UDP-Daten kommen an" if cur["data"] else "keine UDP-Daten"))
                    if cur["player"] != old["player"]:
                        _vlog("player_an" if cur["player"] else "player_aus", f, sr, lab, cur["call"], "")
                    if cur["bild"] and not old["bild"]:
                        _vlog("bild", f, sr, lab, cur["call"], "Player zeigt Bilder")
                    if cur["call"] and cur["call"] != old["call"]:
                        _vlog("rufzeichen", f, sr, lab, cur["call"], "")
                _vl["t"][t] = cur
            # versorgt: lebendes Signal, auf dem ein Tuner Lock + Stream hat
            with _batc_lock:
                live = {k: (st2["freq"], st2["sr"], st2.get("born", now)) for k, st2 in _batc_sig_state.items()
                        if st2["gone_since"] is None and abs(st2["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5}
            for k in list(_vl["served"]):
                if k not in live:
                    _vl["served"].discard(k)
            for k, (f, sr, born) in live.items():
                if k in _vl["served"]:
                    continue
                for t, x in tl.items():
                    if x.get("freq") and abs(x["freq"] - f) <= BATC_SIG_TOL and x.get("lock") is True \
                            and (_vl["t"].get(t) or {}).get("data"):
                        _vl["served"].add(k)
                        _vlog("versorgt", f, sr, x["label"], x.get("call"), "%.0f s nach Erkennung" % (now - born))
                        break
            if now - _vl["last_cmp"] >= 60:
                _vl["last_cmp"] = now
                on = {}
                for t, x in tl.items():
                    if x.get("freq"):
                        k = next((k for k, v in live.items() if abs(v[0] - x["freq"]) <= BATC_SIG_TOL), None)
                        if k is not None:
                            on.setdefault(k, []).append(x["label"])
                unv = ["%.3f" % live[k][0] for k in live if k not in on]
                dup = ["%.3f:%s" % (live[k][0], "+".join(v)) for k, v in on.items() if len(v) > 1]
                frei = [x["label"] for t, x in tl.items()
                        if not (x.get("freq") and any(abs(v[0] - x["freq"]) <= BATC_SIG_TOL for v in live.values()))]
                _vlog("abgleich", None, None, "", "", "BATC %d Signale | versorgt %d | unversorgt: %s | doppelt: %s | Tuner frei: %s" % (
                    len(live), len(on), ",".join(unv) or "-", " ".join(dup) or "-", ",".join(frei) or "-"))
        except Exception as e:
            dlog("Verteil-Protokoll: %r" % (e,))
        time.sleep(2)
threading.Thread(target=_vlog_loop, daemon=True).start()
root.after(20000, _dup_tick)

#----- Tuner-Vorschlag: mehr Signale als Tuner mit 'VerteilA', aber es gibt noch Tuner ohne 'VerteilA' -> vorschlagen, sie dazuzunehmen.
#      Antworten: Ja (VerteilA bei so vielen Tunern wie noetig einschalten) / Nein, 20 Minuten nicht fragen / Nein, generell nicht fragen (Menue Konfig schaltet es wieder an). -----
_vorschlag = {"since": None, "until": 0.0, "open": False}

def _vorschlag_lage():
    """(Zahl der Signale OHNE Tuner, Tuner mit VerteilA die frei sind, freie Tuner ohne VerteilA nach Prio-Rang) - ohne Bake.
    Ein Signal gilt als versorgt, wenn irgendein Tuner (auch ohne VerteilA) darauf steht; angeboten werden nur Tuner, die auf keinem lebenden Signal stehen."""
    with _batc_lock:
        sig = [st2["freq"] for st2 in _batc_sig_state.values() if st2["gone_since"] is None and abs(st2["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5]
    def _on_signal(t):
        f = _target_cur_freq.get(t)
        return f is not None and any(abs(f - x) <= BATC_SIG_TOL for x in sig)
    offen = [x for x in sig if not any(_on_signal(t) and abs(_target_cur_freq[t] - x) <= BATC_SIG_TOL for t in _active_targets)]
    on = [t for t in _active_targets if _verteil_ok(t, need_master=False)]
    frei_on = [t for t in on if not _on_signal(t)]
    off = [t for t in _active_targets if t not in on and not _on_signal(t) and not (t == "octagon" and not _oct_direct())]
    off.sort(key=lambda t: _prio_rank(t, "vergabe"))
    return len(offen), frei_on, off

def _vorschlag_tick():
    try:
        if (app_config.get("tuner_vorschlag", True) and not _vorschlag["open"] and time.time() >= _vorschlag["until"]
                and batc_auto_master.get()):
            n, on, off = _vorschlag_lage()
            if n > len(on) and off:                     # mehr unversorgte Signale als freie Tuner mit VerteilA, und es gibt freie Tuner ohne VerteilA
                if _vorschlag["since"] is None:
                    _vorschlag["since"] = time.time()
                elif time.time() - _vorschlag["since"] >= 30:          # erst wenn es 30 s so bleibt (Signale kommen und gehen)
                    _vorschlag["since"] = None
                    _vorschlag_dialog(n, on, off)
            else:
                _vorschlag["since"] = None
    except Exception as e:
        dlog("Tuner-Vorschlag: %r" % (e,))
    root.after(10000, _vorschlag_tick)

def _vorschlag_dialog(n, on, off):
    need = min(n - len(on), len(off))
    pick = off[:need]
    _vorschlag["open"] = True
    top = tk.Toplevel(root)
    top.title("Tuner dazunehmen?")
    top.transient(root)
    txt = ("%d Signal(e) ohne Tuner, aber nur %d freie(r) Tuner mit VerteilA (%s).\n\nFrei zur Verteilung wuerde sich anbieten: %s\n\n"
           "Soll bei diesen %d Tuner(n) VerteilA eingeschaltet werden?" % (
               n, len(on), ", ".join(_TARGET_LABEL.get(t, t) for t in on) or "keiner",
               ", ".join(_TARGET_LABEL.get(t, t) for t in pick), len(pick)))
    ttk.Label(top, text=txt, justify="left", padding=12).pack()
    fr = ttk.Frame(top, padding=(12, 0, 12, 12)); fr.pack(fill="x")
    def close(kind):
        _vorschlag["open"] = False
        if kind == "ja":
            for t in pick:
                try:
                    _batc_auto[t].set(True)
                except Exception:
                    pass
            olog("Tuner-Vorschlag angenommen: VerteilA eingeschaltet bei %s." % ", ".join(_TARGET_LABEL.get(t, t) for t in pick))
            threading.Thread(target=lambda: batc_auto_assign(report=True), daemon=True).start()
        elif kind == "20":
            _vorschlag["until"] = time.time() + 20 * 60
            olog("Tuner-Vorschlag: 20 Minuten nicht mehr fragen.")
        else:
            app_config["tuner_vorschlag"] = False
            app_config_save()
            try:
                _vorschlag_var.set(False)
            except NameError:
                pass
            olog("Tuner-Vorschlag: generell aus (Menue Konfig: 'Tuner vorschlagen ...' schaltet ihn wieder an).")
        top.destroy()
    ttk.Button(fr, text="Ja, Tuner dazunehmen", command=lambda: close("ja")).pack(side="left", padx=3)
    ttk.Button(fr, text="Nein, 20 Minuten nicht fragen", command=lambda: close("20")).pack(side="left", padx=3)
    ttk.Button(fr, text="Nein, generell nicht", command=lambda: close("nie")).pack(side="left", padx=3)
    top.protocol("WM_DELETE_WINDOW", lambda: close("20"))
    olog("Tuner-Vorschlag: %d Signal(e) ohne Tuner, %d freie(r) Tuner mit VerteilA - vorgeschlagen: %s." % (n, len(on), ", ".join(_TARGET_LABEL.get(t, t) for t in pick)))

root.after(40000, _vorschlag_tick)

#----- Tuner auf der Bake freigeben: steht ein Tuner mit 'VerteilA' laenger auf der Bake (Rest nach Scan/Start/Rueckfall), wird sein Player gestoppt und
#      der Tuner freigegeben (Pi-Stick: auch am Dienst), damit die Automatik ihn fuer ein Signal nimmt. Menue Konfig schaltet es aus. -----
_bake_idle = {}

def _bake_frei_tick():
    try:
        if app_config.get("bake_tuner_frei", True) and batc_auto_master.get():
            now = time.time()
            for t in list(_active_targets):
                f = _target_cur_freq.get(t)
                on_bake = f is not None and abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5
                if not on_bake and f is None:
                    # Tuner hat nie von der GUI eine Frequenz bekommen (z.B. Octagon/Stick stand schon beim Start auf der Bake): am Rufzeichen der Bake (A71A) erkennen
                    _cv = _target_callsign_var.get(t)
                    _nm = ((_cv.get() if _cv is not None else "") or "").upper()
                    if _nm.startswith("A71A") and _fw_locked(t) is not False:
                        on_bake, f = True, BATC_BEACON_NOMINAL_MHZ
                if (not on_bake or t in _dup["closed"] or _scan_active.get(t) or _max.get("t") == t or now - _app_t0 < 60):
                    _bake_idle.pop(t, None)
                    continue
                t0 = _bake_idle.setdefault(t, now)
                if now - t0 < 45:
                    continue                          # erst wenn der Tuner 45 s auf der Bake steht
                _bake_idle.pop(t, None)
                auto_t = _verteil_ok(t)
                olog("%s steht auf der Bake - Player gestoppt%s." % (_TARGET_LABEL.get(t, t), ", Tuner freigegeben (frei fuer neue Signale)" if auto_t else " (kein VerteilA: Tuner bleibt, wird nicht umgestellt)"))
                _dup["closed"][t] = f
                _stop_player(t)
                if not auto_t:
                    continue
                try:
                    if _dvx_is_dienst(t):
                        _dvx_stop(t)
                except Exception as e:
                    dlog("Bake frei: Dienst-Stick %s: %r" % (t, e))
                _target_last_tuned[t] = None
                _target_cur_freq[t] = None
                batc_auto_assign(report=True)
    except Exception as e:
        dlog("Bake frei: %r" % (e,))
    root.after(5000, _bake_frei_tick)


#----- Pi-Sticks, die schon beim Start auf einem Signal stehen (Zustand des Dienstes): Frequenz/Datenrate als aktuellen Stand uebernehmen, damit die
#      Verteilung das Signal als versorgt kennt und nicht noch einen zweiten Tuner (z.B. LongMynd, RTL2) darauf setzt (doppelte Anzeigen) -----
def _dienst_sync_tick():
    try:
        for t in list(_active_targets):
            if t not in _DATVRX_TARGETS or not _dvx_is_dienst(t):
                continue
            st = _dvx_st.get(t) or {}
            if _target_cur_freq.get(t) is None and st.get("proc") and st.get("lock") and st.get("freq"):
                _target_cur_freq[t] = float(st["freq"])
                if st.get("sr"):
                    _target_cur_sr[t] = st["sr"]
                olog("%s: steht auf %.3f MHz (Stand des Dienstes uebernommen)." % (_TARGET_LABEL.get(t, t), float(st["freq"])))
    except Exception as e:
        dlog("Dienst-Stand: %r" % (e,))
    root.after(2000, _dienst_sync_tick)

root.after(3000, _dienst_sync_tick)
_app_t0 = time.time()
root.after(30000, _bake_frei_tick)
threading.Thread(target=_solo_listener, daemon=True).start()

def _restart_outdated_vlcs():
    """Einmal nach dem Start: VLCs, die noch aus einer frueheren Sitzung mit alten Startoptionen laufen
    (ohne skalierende Videoausgabe --vout=gl...), beenden und mit den aktuellen Optionen neu starten."""
    lines = _ps_lines()
    for t in _active_targets:
        if t == "octagon" and not _oct_direct():
            continue
        port = _player_telnet_port(t)
        if port is None:
            continue
        tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port)
        old = [l for l in lines if "vlc" in l.lower() and any(x in l for x in tags) and "--vout=gl" not in l]
        if not old:
            continue
        pids = []
        for l in old:
            try:
                os.kill(int(l.split()[0]), signal.SIGTERM)
                pids.append(l.split()[0])
            except (OSError, ValueError):
                pass
        if pids:
            root.after(0, olog, "%s: VLC aus frueherer Sitzung mit alten Optionen (PID %s) beendet - wird neu gestartet." % (
                _TARGET_LABEL.get(t, t), ", ".join(pids)))
            root.after(2500, lambda tt=t: (_player_wanted.__setitem__(tt, True), _start_player(tt, "vlc")))
# (_restart_outdated_vlcs laeuft erst nach der Start-Pruefung, siehe _startup_player_check - bestehende Player zunaechst in Ruhe lassen)

def _startup_inventory():
    """Einmal nach dem Start ins Log: was laeuft schon (wird uebernommen statt doppelt gestartet)."""
    lines = _ps_lines()
    out = []
    lm = [l for l in lines if len(l.split()) > 1 and os.path.basename(l.split()[1]) == "longmynd"]
    for l in lm:
        p = l.split()
        out.append("longmynd PID %s (%s)" % (p[0], " ".join(p[2:5])))
    for t in _active_targets:
        port = _player_telnet_port(t)
        if port is None:
            continue
        tags = ("--telnet-port=%d" % port, "--telnet-port %d" % port)
        v = [l.split()[0] for l in lines if "vlc" in l.lower() and any(x in l for x in tags)]
        if v:
            out.append("%s-VLC PID %s%s" % (_TARGET_LABEL.get(t, t), ", ".join(v), "  (DOPPELT!)" if len(v) > 1 else ""))
    try:
        if _is_port_open("127.0.0.1", int(app_config.get("onair_app_port") or 8765), timeout=0.3):
            out.append("OnAir-Fenster (API-Port %s)" % (app_config.get("onair_app_port") or 8765))
    except (TypeError, ValueError):
        pass
    term = [l for l in lines if "gnome-terminal" in l and "start_longmynd" in l]
    if len(term) > 1:
        out.append("%d Terminal-Fenster von start_longmynd2.sh (alte koennen geschlossen werden)" % len(term))
    root.after(0, olog, "Beim Start schon aktiv (wird uebernommen, nicht doppelt gestartet): %s" %
               ("; ".join(out) if out else "nichts"))
root.after(3000, lambda: threading.Thread(target=_startup_inventory, daemon=True).start())
root.after(4000, _qrz_disp_tick)
import collections as _collections
for _t in list(_tsrate_canvas):
    _tsrate_state[_t] = {"hist": _collections.deque(maxlen=60), "acc": [0, 0], "t0": time.monotonic(), "last_rx": 0.0}
    threading.Thread(target=_tsrate_reader, args=(_t,), daemon=True).start()
root.after(3000, _tsrate_tick)
if "octagon" in _active_targets and _oct_direct():
    _oct_direct_button_update()
    root.after(1500, lambda: threading.Thread(target=_oct_direct_start, daemon=True).start())

if _DATVRX_TARGETS:
    _dvx_startup()
if _ONAIR_TARGETS:
    _onair_startup()
else:
    threading.Thread(target=_onair_kill_all, daemon=True).start()

#----- stop user interface -----
root.protocol("WM_DELETE_WINDOW", on_exit)

#----- start user interface -----

#===== Start-Pruefung der gemerkten Player-Fenster =====
# Zustand (Frequenz, Datenrate, Player offen) je Tuner wird alle 30 s und beim Beenden in app_config["last_players"] gemerkt (nicht waehrend der Start-Pruefung).
# Beim Start wartet die GUI, bis die BATC-Signalliste da ist (hoechstens 45 s), vergleicht sie mit den gemerkten Fenstern und gibt dann erst
# das Starten von Playern frei (_start_player ist bis dahin gesperrt, auch fuer die Bake).
_hold_state_sig = [None]

def _players_state_now():
    out = {}
    for t in _active_targets:
        try:
            out[t] = {"freq": _target_cur_freq.get(t), "sr": _target_cur_sr.get(t), "player": bool(_autoclose_player_running(t))}
        except Exception:
            out[t] = {"freq": _target_cur_freq.get(t), "sr": _target_cur_sr.get(t), "player": False}
    return out

def _players_state_save(sync=False):
    """Zustand merken (nur wenn sich etwas geaendert hat und die Start-Pruefung vorbei ist)"""
    if _hold["active"]:
        return
    def work():
        st = _players_state_now()
        sig = repr(sorted((t, round(v["freq"] or 0, 3), v["sr"], v["player"]) for t, v in st.items()))
        if sig == _hold_state_sig[0]:
            return
        _hold_state_sig[0] = sig
        app_config["last_players"] = {"t": time.time(), "targets": st}
        app_config_save()
    if sync:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()

def _startup_player_check(live):
    """Nach der Start-Pruefung: bereits offene Player (aus der frueheren Sitzung) bleiben bis hierher unangetastet. Jetzt je Player pruefen,
    ob ein Stream darueber laeuft und die Frequenz ein lebendes Signal hat - sonst beenden (Player ohne UDP-Stream wie Octagon/HTTP bleiben).
    Erst danach duerfen weitere Player fuer noch nicht gezeigte Signale geoeffnet werden."""
    try:
        keep, closed = [], []
        for t in list(_active_targets):
            if not _autoclose_player_running(t):
                continue
            ep = _autoclose_endpoint(t)
            label = _TARGET_LABEL.get(t, t)
            if not ep:
                keep.append("%s (ohne UDP-Stream, nicht pruefbar)" % label)
                continue
            data = _stream_has_data(ep[0], ep[1])
            if data is False:
                grund = "kein Stream"
            elif _no_live_signal(t):
                grund = "kein lebendes Signal"
            else:
                keep.append(label)
                continue
            closed.append("%s (%s)" % (label, grund))
            root.after(0, lambda tt=t: _stop_player(tt))
        root.after(0, olog, "Start-Pruefung Player: %d lebende Signale | bleiben offen: %s | beendet: %s | weitere Player nur fuer noch nicht gezeigte Signale." % (
            len(live), ", ".join(keep) or "-", ", ".join(closed) or "-"))
        time.sleep(3.0)
        _restart_outdated_vlcs()
    except Exception as e:
        dlog("Start-Pruefung Player: %r" % (e,))

def _hold_finish(grund):
    """Start-Pruefung beenden: gemerkte Fenster mit den lebenden BATC-Signalen vergleichen, danach duerfen Player starten"""
    mem = (app_config.get("last_players") or {}).get("targets") or {}
    with _batc_lock:
        live = [st["freq"] for st in _batc_sig_state.values() if st["gone_since"] is None and abs(st["freq"] - BATC_BEACON_NOMINAL_MHZ) > 0.5]
    ok, weg = [], []
    for t, v in mem.items():
        if t not in _active_targets or not v.get("player") or v.get("freq") is None:
            continue
        f = v["freq"]
        if abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5:
            weg.append("%s (Bake)" % _TARGET_LABEL.get(t, t))
        elif any(abs(f - x) <= BATC_SIG_TOL for x in live):
            ok.append("%s %.3f" % (_TARGET_LABEL.get(t, t), f))
        else:
            weg.append("%s %.3f (Signal weg)" % (_TARGET_LABEL.get(t, t), f))
    _hold["active"] = False
    threading.Thread(target=_startup_player_check, args=(live,), daemon=True).start()
    olog("Start-Pruefung (%s): %d lebende Signale; gemerkte Fenster mit Signal: %s | ohne Signal (kein Player): %s." % (
        grund, len(live), ", ".join(ok) or "-", ", ".join(weg) or "-"))

def _hold_tick():
    try:
        if _hold["active"]:
            now = time.time()
            if _batc_last_msg[0] and _batc_last_msg[0] > _hold["t0"] and _hold["first"] is None:
                _hold["first"] = now
            if _hold["first"] is not None and now - _hold["first"] >= 8.0:
                _hold_finish("BATC-Signalliste da")
            elif not _batc_started and now - _hold["t0"] >= 10.0:
                _hold_finish("BATC nicht verbunden")
            elif now - _hold["t0"] >= 45.0:
                _hold_finish("Zeit abgelaufen")
        elif time.time() - _hold.get("saved", 0.0) >= 30.0:
            _hold["saved"] = time.time()
            _players_state_save()
    except Exception as e:
        dlog("Start-Pruefung: %r" % (e,))
        _hold["active"] = False
    root.after(1000, _hold_tick)

root.after(1000, _hold_tick)

#===== Webinterface (Port 8099): laufende Verteilung im Browser (auch Windows/Handy), Umschalten, VLC starten. =====
#  /               Seite (aktualisiert sich jede Sekunde)
#  /api/status     JSON: Empfaenger + Signale
#  /api/tune       POST {target,freq,sr}      Empfaenger umschalten
#  /api/redistribute POST                     "Neu verteilen"
#  /api/vlc_server POST {target}              VLC auf DIESEM Rechner starten
#  /play/<t>.m3u?mode=mc|http                 ein Stream als .m3u (oeffnet VLC auf dem Browser-Rechner)
#  /play/list.m3u?t=a,b&mode=..               Playlist mehrerer Streams (in VLC durchzappen: N / P)
#  /play/grid.bat|grid.sh?t=a,b&sw=..&sh=..&mode=..   startet je Stream ein VLC-Fenster, passend gekachelt
#  /stream/<t>                                Weiterleitung des Multicast-TS per HTTP (geht auch ueber WLAN/Router)
_web = {"srv": None, "snap": {"targets": [], "signals": [], "t": 0}}

def _web_mer_text(t):
    """MER-Text des Tuners fuer das Web-GUI (aus dem gelben MER-Chip, siehe _mini_src); leer ohne Wert"""
    try:
        src = _mini_src(t)
        txt = src.cget("text") if src is not None else ""
    except Exception:
        return ""
    txt = re.sub(r"^\s*(MER|Es/N0)\s*:?\s*", "", txt or "").strip()
    return "" if txt in ("", "-") else "MER " + txt

def _web_snapshot_tick():
    """Hauptthread (Tk-Variablen!): Momentaufnahme fuer den Webserver, 1x/s."""
    try:
        tl = []
        for t in _active_targets:
            cs = ""
            try:
                v = _target_callsign_var.get(t)
                cs = v.get() if v is not None else ""
            except Exception:
                pass
            ep = None
            g = _TARGET_STREAM.get(t)
            if g:
                try:
                    ep = g()
                except Exception:
                    ep = None
            octurl = _octagon_last_stream.get("url") if t == "octagon" else None
            tl.append({"id": t, "label": _TARGET_LABEL.get(t, t), "freq": _target_cur_freq.get(t),
                       "sr": _target_cur_sr.get(t), "call": cs or "", "lock": _fw_locked(t),
                       "auto": bool(_batc_auto.get(t).get()) if _batc_auto.get(t) is not None else False,
                       "scan": bool(_scan_active.get(t)), "player": _player_active.get(t), "mer": _web_mer_text(t),
                       "stream": ("%s:%s" % ep) if ep else (octurl or ""),
                       "beacon": bool(_target_cur_freq.get(t) and abs(_target_cur_freq.get(t) - BATC_BEACON_NOMINAL_MHZ) <= 0.5)})
        with _batc_lock:
            sigs = sorted(((st["freq"], st["sr"], st["gone_since"] is None) for st in _batc_sig_state.values()),
                          key=lambda x: x[0])
        sl = [{"freq": f, "sr": sr, "alive": a, "call": _call_for_freq(f) or "",
               "beacon": abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5} for f, sr, a in sigs]
        _web["snap"] = {"targets": tl, "signals": sl, "t": time.time(),
                        "auto": bool(batc_auto_master.get())}
        fft = _batc_last_fft
        if fft and id(fft) != _web.get("fft_id"):
            n = len(fft)
            m = 600
            pts = [int(max(fft[int(i * n / m):max(int(i * n / m) + 1, int((i + 1) * n / m))]) * 1000 / 65536) for i in range(m)]
            on = {}
            for tt in tl:
                if tt["freq"]:
                    on.setdefault(round(tt["freq"], 1), []).append(tt["label"])
            ss = []
            for sg in (_spectrum_last_detected or []):
                f = sg["freq"]
                ss.append({"freq": round(f, 3), "sr": int(round(sg["sr_mhz"] * 1000)), "call": _call_for_freq(f) or "",
                           "beacon": abs(f - BATC_BEACON_NOMINAL_MHZ) <= 0.5,
                           "f0": round(f - sg["sr_mhz"] * 0.675, 3), "f1": round(f + sg["sr_mhz"] * 0.675, 3),
                           "tuners": [lab for tt in tl if tt["freq"] and abs(tt["freq"] - f) <= BATC_SIG_TOL
                                      for lab in [tt["label"]]]})
            _web["fft_id"] = id(fft)
            _web["spec"] = {"start": BATC_START_FREQ, "span": BATC_BANDWIDTH, "pts": pts, "sigs": ss}
    except Exception as e:
        dlog("Webinterface-Snapshot: %r" % (e,))
    root.after(1000, _web_snapshot_tick)

def _web_mc_ep(t):
    g = _TARGET_STREAM.get(t)
    try:
        return g() if g else None
    except Exception:
        return None

def _web_url(t, mode, host):
    if t == "octagon" and not _web_mc_ep(t):
        return _octagon_last_stream.get("url") or ""
    ep = _web_mc_ep(t)
    if not ep:
        return ""
    if mode == "http":
        return "http://%s/stream/%s" % (host, t)
    return "udp://@%s:%s" % ep

_WEB_PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QO-100 Verteilung DG7NDV</title>
<style>
:root{--bg:#f2f4f7;--card:#fff;--tx:#1c2430;--mut:#6b7684;--ok:#27ae60;--bad:#c0392b;--sel:#2d7dd2;--bd:#d5dbe3}
@media (prefers-color-scheme:dark){:root{--bg:#14181d;--card:#1e242b;--tx:#e6eaf0;--mut:#93a0ae;--bd:#333c46}}
body{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--tx)}
header{padding:10px 16px;background:#1f3a5f;color:#fff;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
header h1{font-size:18px;margin:0 12px 0 0}
main{padding:12px 16px;max-width:1500px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:10px}
.card h2{font-size:15px;margin:0 0 6px;display:flex;justify-content:space-between;align-items:center}
.call{font-size:20px;font-weight:700;text-align:center;padding:6px;border-radius:6px;background:#888;color:#fff;margin:4px 0}
.call.lock{background:var(--ok)}.call.nolock{background:var(--bad)}
.mut{color:var(--mut);font-size:12px}
button,select{font:inherit;padding:6px 10px;border-radius:6px;border:1px solid var(--bd);background:var(--card);color:var(--tx);cursor:pointer}
button.pri{background:var(--sel);color:#fff;border-color:var(--sel)}
.row{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.sig{width:100%;text-align:left;font-size:13px;padding:4px 8px}
.sig.on{background:var(--sel);color:#fff}
.sig.gone{opacity:.5}
label{font-size:13px}
body.mobile header{padding:6px 8px;gap:6px}
body.mobile header h1{font-size:15px;margin:0 6px 0 0}
body.mobile main{padding:6px 8px}
body.mobile .grid{grid-template-columns:1fr;gap:6px}
body.mobile .card{padding:6px 8px}
body.mobile .card h2{font-size:14px;margin:0}
body.mobile .call{font-size:16px;padding:3px;margin:3px 0}
body.mobile .hide-m{display:none}
body.mobile #spec{height:170px!important}
body.mobile button{padding:8px 10px}
details summary{cursor:pointer;font-size:13px;color:var(--mut);margin-top:4px}
</style></head><body class="__BODYCLASS__">
<header><h1>QO-100 Verteilung</h1>
<button onclick="post('/api/redistribute',{})">Neu verteilen</button>
<label>Abspielen per <select id="mode"><option value="mc">Multicast direkt (LAN)</option>
<option value="http">HTTP-Weiterleitung (WLAN/Router)</option></select></label>
<label style="color:#fff"><input type="checkbox" id="selall"> Alle</label>
<button onclick="playSel('list')">Auswahl als Playlist (.m3u, durchzappen)</button>
<button class="hide-m" onclick="playSel('grid')">Auswahl gekachelt starten</button>
<a id="viewsw" style="color:#cfd8e3;font-size:12px"></a>
<a id="newsw" style="color:#9fe0a8;font-size:13px;font-weight:600" title="neues Web-GUI im Zapper-Stil">Neues Interface</a>
<label style="color:#fff"><input type="checkbox" id="specon"> Spektrum</label>
<span id="st" class="mut" style="color:#cfd8e3"></span></header>
<main><div id="specbox" class="card" style="display:none;margin-bottom:12px">
<div class="row" id="tsel" style="margin:0 0 6px"></div>
<canvas id="spec" style="width:100%;height:260px;background:#0b0f14;border-radius:6px;cursor:crosshair"></canvas>
<div class="mut">Klick auf ein Signal schaltet den gewaehlten Tuner (auto = naechster freier).</div></div>
<div class="grid" id="g"></div></main>
<script>
let sel=JSON.parse(localStorage.getItem('sel')||'{}');
function q(s){return document.querySelector(s)}
function post(u,b){return fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
function mode(){return q('#mode').value}
// Android: VLC direkt per Intent starten (kein .m3u-Download); PC/Windows: wie bisher .m3u bzw. Kacheln
function isAndroid(){return /Android/i.test(navigator.userAgent)}
function vlcIntent(path,type){return 'intent://'+location.host+path+'#Intent;scheme=http;type='+type+';package=org.videolan.vlc;end'}
function playOne(id){ if(isAndroid()){location.href=vlcIntent('/stream/'+id,'video/mp2t');return} location.href='/play/'+id+'.m3u?mode='+mode() }
function playList(t){ if(isAndroid()){location.href=vlcIntent('/play/list.m3u?t='+t.join(',')+'&mode=http','audio/x-mpegurl');return} location.href='/play/list.m3u?t='+t.join(',')+'&mode='+mode() }
function isWin(){return /Windows/i.test(navigator.userAgent)}
function playSel(kind){
  const t=Object.keys(sel).filter(k=>sel[k]); if(!t.length){alert('Bitte Empfaenger ankreuzen.');return}
  if(kind==='list'){playList(t);return}
  const sw=screen.availWidth*(window.devicePixelRatio||1), sh=screen.availHeight*(window.devicePixelRatio||1);
  location.href='/play/grid.'+(isWin()?'bat':'sh')+'?t='+t.join(',')+'&mode='+mode()+'&sw='+Math.round(sw)+'&sh='+Math.round(sh);
}
function render(d){
  if(JSON.stringify(d.targets.map(t=>t.id))!==JSON.stringify(lastTargets.map(t=>t.id))){lastTargets=d.targets.map(t=>({id:t.id,label:t.label}));tsel()}
  q('#st').textContent=new Date(d.t*1000).toLocaleTimeString()+(d.auto?'  ·  Verteil-Automatik an':'');
  const g=q('#g'); let h='';
  for(const t of d.targets){
    const cls=t.lock===true?'lock':(t.lock===false?'nolock':'');
    h+='<div class="card"><h2><span>'+t.label+'</span><label><input type="checkbox" data-t="'+t.id+'" '+(sel[t.id]?'checked':'')+'> Auswahl</label></h2>';
    h+='<div class="call '+cls+'">'+(t.call||'-')+'</div>';
    h+='<div class="mut">'+(t.freq?t.freq.toFixed(3)+' MHz':'-')+(t.sr?' · '+t.sr+' kS/s':'')+(t.beacon?' · Bake (stumm)':'')+(t.scan?' · Scan':'')+'<br>'+(t.stream||'')+'</div>';
    h+='<div class="row">';
    if(t.stream){h+='<button class="pri" onclick="playOne(\''+t.id+'\')">Hier abspielen</button>';}
    h+='<button onclick="post(\'/api/vlc_server\',{target:\''+t.id+'\'})">VLC am Server</button></div>';
    const nsig=d.signals.length;
    h+=MOB?'<details><summary>Signale ('+nsig+') - antippen zum Umschalten</summary><div class="row">':'<div class="row">';
    for(const s of d.signals){
      const on=t.freq&&Math.abs(t.freq-s.freq)<0.2;
      h+='<button class="sig'+(on?' on':'')+(s.alive?'':' gone')+'" onclick="post(\'/api/tune\',{target:\''+t.id+'\',freq:'+s.freq+',sr:'+s.sr+'})">'+
         (s.beacon?'Bake ':'')+s.freq.toFixed(3)+' MHz '+s.sr+' kS/s '+(s.call||'')+(s.alive?'':' [weg]')+'</button>';
    }
    h+=MOB?'</div></details></div>':'</div></div>';
  }
  const open=[...g.querySelectorAll('details')].map(x=>x.open);
  g.innerHTML=h;
  g.querySelectorAll('details').forEach((x,i)=>{if(open[i])x.open=true});
  g.querySelectorAll('input[data-t]').forEach(c=>c.onchange=()=>{sel[c.dataset.t]=c.checked;localStorage.setItem('sel',JSON.stringify(sel));selAllSync()});
  selAllSync();
}
let ctgt=localStorage.getItem('ctgt')||'auto', spec=null, lastTargets=[];
function selAllSync(){const ids=lastTargets.map(t=>t.id); const n=ids.filter(i=>sel[i]).length;
  const c=q('#selall'); c.checked=ids.length>0&&n===ids.length; c.indeterminate=n>0&&n<ids.length}
q('#selall').onchange=()=>{const on=q('#selall').checked; for(const t of lastTargets)sel[t.id]=on;
  localStorage.setItem('sel',JSON.stringify(sel)); document.querySelectorAll('input[data-t]').forEach(c=>c.checked=on); selAllSync()};
const MOB=document.body.classList.contains('mobile');
q('#viewsw').textContent=MOB?'Desktop-Ansicht':'Handy-Ansicht';
q('#newsw').href=(location.port==String(__NEWPORT__))?'/':(location.protocol+'//'+location.hostname+':'+__NEWPORT__+'/');
q('#viewsw').href=MOB?'/?view=desktop':'/?view=mobile';
function tsel(){
  const b=q('#tsel'); let h='<span class="mut" style="align-self:center">Klick-Ziel:</span>';
  for(const t of lastTargets.concat([{id:'auto',label:'auto'}])){
    h+='<button class="'+(t.id===ctgt?'pri':'')+'" data-c="'+t.id+'">'+t.label+'</button>';}
  b.innerHTML=h; b.querySelectorAll('button[data-c]').forEach(x=>x.onclick=()=>{ctgt=x.dataset.c;localStorage.setItem('ctgt',ctgt);tsel()});
}
function drawSpec(){
  const c=q('#spec'); if(!spec||!spec.pts){return}
  const W=c.clientWidth,H=c.clientHeight; c.width=W*devicePixelRatio; c.height=H*devicePixelRatio;
  const g=c.getContext('2d'); g.scale(devicePixelRatio,devicePixelRatio); g.clearRect(0,0,W,H);
  const n=spec.pts.length, base=H-18, top=8, X=f=>(f-spec.start)/spec.span*W;
  g.strokeStyle='#2a3440'; g.fillStyle='#8899aa'; g.font='10px system-ui';
  for(let f=Math.ceil(spec.start);f<spec.start+spec.span;f++){const x=X(f);g.beginPath();g.moveTo(x,top);g.lineTo(x,base);g.stroke();g.fillText(f.toFixed(0),x+2,H-5)}
  const grd=g.createLinearGradient(0,base,0,top); grd.addColorStop(0,'#123c8c');grd.addColorStop(.35,'#14b4c8');grd.addColorStop(.55,'#2ecc71');grd.addColorStop(.75,'#f1c40f');grd.addColorStop(1,'#e74c3c');
  g.beginPath(); g.moveTo(0,base);
  for(let i=0;i<n;i++){const v=spec.pts[i]/1000, fr=Math.max(0,(v-0.125)/0.875); g.lineTo(i/(n-1)*W, base-fr*(base-top)*1.25)}
  g.lineTo(W,base); g.closePath(); g.fillStyle=grd; g.fill();
  g.textAlign='center';
  for(const s of spec.sigs){const x=X(s.freq); let y=30;
    g.fillStyle='#ffffff'; g.font='11px system-ui'; g.fillText((s.beacon?'Bake ':'')+s.sr+'kS '+s.freq.toFixed(3),x,y+28);
    if(s.call){g.fillStyle='#2ecc71'; g.font='bold 15px system-ui'; g.fillText(s.call,x,y+12)}
    if(s.tuners.length){g.fillStyle='#ffd27f'; g.font='10px system-ui'; g.fillText(s.tuners.join(', '),x,y-4)}}
  g.textAlign='left';
}
q('#spec').onclick=e=>{ if(!spec)return; const r=e.target.getBoundingClientRect();
  const f=spec.start+(e.clientX-r.left)/r.width*spec.span;
  post('/api/click',{target:ctgt,freq:f}).then(r=>{if(!r.ok)r.text().then(t=>q('#st').textContent=t)})};
q('#specon').checked=localStorage.getItem('specon')==='1';
function specVis(){q('#specbox').style.display=q('#specon').checked?'block':'none'}
q('#specon').onchange=()=>{localStorage.setItem('specon',q('#specon').checked?'1':'0');specVis()}; specVis();
async function specPoll(){ if(q('#specon').checked){try{spec=await (await fetch('/api/spectrum')).json();drawSpec()}catch(e){}} setTimeout(specPoll,1000)}
specPoll(); window.onresize=drawSpec;
async function poll(){try{const r=await fetch('/api/status');render(await r.json())}catch(e){q('#st').textContent='keine Verbindung zur GUI'}setTimeout(poll,1500)}
poll();
</script></body></html>"""

def _web_grid_geo(n, sw, sh):
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = max(1, int(math.ceil(n / float(cols))))
    cw, ch = sw // cols, (sh - 40) // rows
    w = min(cw - 8, int((ch - 40) * 16 / 9))
    h = int(w * 9 / 16)
    return [((i % cols) * cw, (i // cols) * ch, w, h) for i in range(n)]

def _web_handler_cls():
    import http.server
    import urllib.parse as up

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *a):
            dlog("Web %s: %s" % (self.client_address[0], fmt % a))

        def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _host(self):
            return self.headers.get("Host") or "%s:%d" % (self.server.server_address[0], self.server.server_address[1])

        def do_GET(self):
            u = up.urlparse(self.path)
            qs = up.parse_qs(u.query)
            p = u.path
            if p in ("/", "/index.html"):
                view = (qs.get("view") or [""])[0]
                ua = self.headers.get("User-Agent") or ""
                mobile = view == "mobile" or (view != "desktop" and re.search(r"Mobi|Android|iPhone|iPod|Windows Phone", ua) is not None)
                return self._send(200, _WEB_PAGE.replace("__BODYCLASS__", "mobile" if mobile else "").replace("__NEWPORT__", str(int(app_config.get("webgui_port") or 8100))),
                                  "text/html; charset=utf-8")
            if p == "/api/status":
                return self._send(200, json.dumps(_web["snap"]), "application/json")
            if p == "/api/spectrum":
                return self._send(200, json.dumps(_web.get("spec") or {}), "application/json")
            mode = (qs.get("mode") or ["mc"])[0]
            if p.startswith("/play/") and p.endswith(".m3u") and p not in ("/play/list.m3u",):
                t = p[len("/play/"):-4]
                url = _web_url(t, mode, self._host())
                if not url:
                    return self._send(404, "kein Stream fuer %s" % t)
                body = "#EXTM3U\n#EXTINF:-1,%s\n%s\n" % (_TARGET_LABEL.get(t, t), url)
                return self._send(200, body, "audio/x-mpegurl", {"Content-Disposition": 'attachment; filename="%s.m3u"' % t})
            ts = [x for x in ((qs.get("t") or [""])[0]).split(",") if x in _active_targets]
            if p == "/play/list.m3u":
                lines = ["#EXTM3U"]
                for t in ts:
                    url = _web_url(t, mode, self._host())
                    if url:
                        lines += ["#EXTINF:-1,%s" % _TARGET_LABEL.get(t, t), url]
                return self._send(200, "\n".join(lines) + "\n", "audio/x-mpegurl",
                                  {"Content-Disposition": 'attachment; filename="qo100_auswahl.m3u"'})
            if p in ("/play/grid.bat", "/play/grid.sh"):
                try:
                    sw = int((qs.get("sw") or ["1920"])[0]); sh = int((qs.get("sh") or ["1080"])[0])
                except ValueError:
                    sw, sh = 1920, 1080
                items = [(t, _web_url(t, mode, self._host())) for t in ts]
                items = [(t, u2) for t, u2 in items if u2]
                geos = _web_grid_geo(len(items), sw, sh)
                opts = "--no-one-instance --no-playlist-enqueue --intf=dummy --dummy-quiet --no-embedded-video --network-caching=1000"
                if p.endswith(".bat"):
                    out = ["@echo off", "rem QO-100: je Stream ein VLC-Fenster, gekachelt fuer %dx%d (DG7NDV)" % (sw, sh),
                           'set VLC="%ProgramFiles%\\VideoLAN\\VLC\\vlc.exe"',
                           'if not exist %VLC% set VLC="%ProgramFiles(x86)%\\VideoLAN\\VLC\\vlc.exe"']
                    for (t, url), (x, y, w, h) in zip(items, geos):
                        out.append('start "" %%VLC%% %s --video-x=%d --video-y=%d --width=%d --height=%d '
                                   '--video-title="%s" "%s"' % (opts, x, y, w, h, _TARGET_LABEL.get(t, t), url))
                    body = "\r\n".join(out) + "\r\n"
                    return self._send(200, body, "application/octet-stream",
                                      {"Content-Disposition": 'attachment; filename="qo100_kacheln.bat"'})
                out = ["#!/bin/sh", "# QO-100: je Stream ein VLC-Fenster, gekachelt fuer %dx%d (DG7NDV)" % (sw, sh)]
                for (t, url), (x, y, w, h) in zip(items, geos):
                    out.append('vlc %s --video-x=%d --video-y=%d --width=%d --height=%d --video-title="%s" "%s" &' %
                               (opts, x, y, w, h, _TARGET_LABEL.get(t, t), url))
                return self._send(200, "\n".join(out) + "\n", "application/x-sh",
                                  {"Content-Disposition": 'attachment; filename="qo100_kacheln.sh"'})
            if p.startswith("/stream/"):
                return self._relay(p[len("/stream/"):])
            return self._send(404, "nicht gefunden")

        def _relay(self, t):
            ep = _web_mc_ep(t)
            if not ep:
                return self._send(404, "kein Multicast-Stream fuer %s" % t)
            s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except (AttributeError, OSError):
                    pass
                s2.bind((ep[0], int(ep[1])))
                s2.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                              struct.pack("4s4s", socket.inet_aton(ep[0]), socket.inet_aton("0.0.0.0")))
                s2.settimeout(5.0)
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                root.after(0, olog, "Webinterface: %s leitet %s per HTTP an %s weiter." % (
                    _TARGET_LABEL.get(t, t), "%s:%s" % ep, self.client_address[0]))
                while True:
                    try:
                        data = s2.recv(65536)
                    except socket.timeout:
                        continue
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                s2.close()
                dlog("Webinterface: Weiterleitung %s an %s beendet." % (t, self.client_address[0]))

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, OSError):
                return self._send(400, "ungueltig")
            p = self.path.split("?")[0]
            who = self.client_address[0]
            if p == "/api/tune":
                t = d.get("target")
                try:
                    f = float(d.get("freq")); sr = int(d.get("sr"))
                except (TypeError, ValueError):
                    return self._send(400, "freq/sr fehlt")
                if t not in _active_targets:
                    return self._send(404, "unbekannter Empfaenger")
                root.after(0, olog, "Webinterface (%s): %s -> %.3f MHz / %d kS/s" % (who, _TARGET_LABEL.get(t, t), f, sr))
                root.after(0, target_dispatch, t, f, sr, None)
                return self._send(200, "ok")
            if p == "/api/click":
                t = d.get("target") or "auto"
                try:
                    f = float(d.get("freq"))
                except (TypeError, ValueError):
                    return self._send(400, "freq fehlt")
                sp = _web.get("spec") or {}
                sg = next((x for x in sp.get("sigs", []) if x["f0"] <= f <= x["f1"]), None)
                if sg is None:
                    return self._send(404, "an dieser Stelle kein Signal")
                def _do(t=t, sg=sg):
                    tt = _auto_pick_target(sg["freq"], sg["sr"]) if t == "auto" else t
                    if tt not in _active_targets:
                        olog("Webinterface (%s): Spektrum-Klick %.3f MHz - kein freier Tuner." % (who, sg["freq"]))
                        return
                    olog("Webinterface (%s): Spektrum-Klick %.3f MHz -> %s%s" % (
                        who, sg["freq"], "auto -> " if t == "auto" else "", _TARGET_LABEL.get(tt, tt)))
                    with _batc_lock:
                        key = next((k for k, st in _batc_sig_state.items() if abs(st["freq"] - sg["freq"]) <= BATC_SIG_TOL), None)
                    target_dispatch(tt, sg["freq"], sg["sr"], key)
                root.after(0, _do)
                return self._send(200, "ok")
            if p == "/api/redistribute":
                root.after(0, olog, "Webinterface (%s): Neu verteilen." % who)
                root.after(0, batc_redistribute)
                return self._send(200, "ok")
            if p == "/api/vlc_server":
                t = d.get("target")
                if t not in _active_targets:
                    return self._send(404, "unbekannter Empfaenger")
                root.after(0, olog, "Webinterface (%s): VLC fuer %s am Server starten." % (who, _TARGET_LABEL.get(t, t)))
                root.after(0, lambda: (_player_wanted.__setitem__(t, True), _start_player(t, _player_active.get(t) or "vlc", user=True)))
                return self._send(200, "ok")
            return self._send(404, "nicht gefunden")
    return H

def _web_start():
    if _web["srv"] is not None:
        return
    import http.server
    port = int(app_config.get("web_port") or 8099)
    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), _web_handler_cls())
    except OSError as e:
        olog("Webinterface: Port %d nicht nutzbar: %s" % (port, e))
        return
    srv.daemon_threads = True
    _web["srv"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ip = "127.0.0.1"
    try:
        so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        so.connect(("192.168.0.1", 9))
        ip = so.getsockname()[0]
        so.close()
    except OSError:
        pass
    olog("Webinterface laeuft: http://%s:%d/  (im Browser, auch Windows/Handy)" % (ip, port))

def _web_stop():
    srv = _web["srv"]
    if srv is None:
        return
    _web["srv"] = None
    threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
    olog("Webinterface beendet.")


#===== Neues Web-GUI (Port 8100, Stil wie die Zapper-Webseite am Pi): Anzeige UND Steuerung im Browser/Handy =====
# Erbt alle Routen des alten Webinterface (Streams, .m3u, /api/tune, /api/click, /api/redistribute, /api/vlc_server) und ersetzt nur die
# Startseite; das alte Interface bleibt hier unter /alt erreichbar. Neu: /api/state, /api/auto_master, /api/auto_target, /api/stop_player,
# /api/scan, /api/mute_all, /api/player_neu, /api/redistribute_rest. Quelle der Seite: leandvb_webgui.py.
_webgui = {"srv": None}

def _webgui_handler_cls():
    Base = _web_handler_cls()

    class H2(Base):
        def do_GET(self):
            import urllib.parse as up
            u = up.urlparse(self.path)
            p = u.path
            if p in ("/", "/index.html"):
                try:
                    _d = os.path.dirname(os.path.abspath(__file__))
                    if _d not in sys.path:
                        sys.path.insert(0, _d)
                    import leandvb_webgui as _wg
                except ImportError as e:
                    return self._send(500, "leandvb_webgui.py fehlt: %s" % e)
                return self._send(200, _wg.PAGE, "text/html; charset=utf-8")
            if p == "/alt":
                ua = self.headers.get("User-Agent") or ""
                mobile = re.search(r"Mobi|Android|iPhone|iPod|Windows Phone", ua) is not None
                return self._send(200, _WEB_PAGE.replace("__BODYCLASS__", "mobile" if mobile else "").replace("__NEWPORT__", str(int(app_config.get("webgui_port") or 8100))), "text/html; charset=utf-8")
            if p == "/api/state":
                return self._send(200, json.dumps(_web["snap"]), "application/json")
            return Base.do_GET(self)

        def do_POST(self):
            p = self.path.split("?")[0]
            who = self.client_address[0]
            acts = {"/api/auto_master", "/api/auto_target", "/api/stop_player", "/api/scan", "/api/mute_all", "/api/player_neu", "/api/redistribute_rest"}
            if p not in acts:
                return Base.do_POST(self)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, OSError):
                return self._send(400, "ungueltig")
            t = d.get("target")
            if p in ("/api/auto_target", "/api/stop_player", "/api/scan") and t not in _active_targets:
                return self._send(404, "unbekannter Empfaenger")
            lab = _TARGET_LABEL.get(t, t)
            if p == "/api/auto_master":
                on = bool(d.get("on"))
                root.after(0, olog, "Web-GUI (%s): Verteil Automatik %s." % (who, "AN" if on else "AUS"))
                root.after(0, lambda: batc_auto_master.set(on))
            elif p == "/api/auto_target":
                on = bool(d.get("on"))
                root.after(0, olog, "Web-GUI (%s): VerteilA %s fuer %s." % (who, "AN" if on else "AUS", lab))
                root.after(0, lambda: _batc_auto[t].set(on))
            elif p == "/api/stop_player":
                root.after(0, olog, "Web-GUI (%s): Player von %s stoppen." % (who, lab))
                root.after(0, _stop_player, t)
            elif p == "/api/scan":
                root.after(0, olog, "Web-GUI (%s): Scan fuer %s." % (who, lab))
                root.after(0, _scan_target, t)
            elif p == "/api/mute_all":
                root.after(0, olog, "Web-GUI (%s): Mute all." % who)
                root.after(0, vlc_mute_all)
            elif p == "/api/player_neu":
                root.after(0, olog, "Web-GUI (%s): PlayerNeu." % who)
                threading.Thread(target=_players_restart_all, daemon=True).start()
            elif p == "/api/redistribute_rest":
                root.after(0, olog, "Web-GUI (%s): NeuNeuVerteilen." % who)
                root.after(0, batc_redistribute_rest)
            return self._send(200, "ok")
    return H2

def _webgui_start():
    if _webgui["srv"] is not None:
        return
    import http.server
    port = int(app_config.get("webgui_port") or 8100)
    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), _webgui_handler_cls())
    except OSError as e:
        olog("Web-GUI: Port %d nicht nutzbar: %s" % (port, e))
        return
    srv.daemon_threads = True
    _webgui["srv"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ip = "127.0.0.1"
    try:
        so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        so.connect(("192.168.0.1", 9))
        ip = so.getsockname()[0]
        so.close()
    except OSError:
        pass
    olog("Web-GUI laeuft: http://%s:%d/  (Anzeige und Steuerung im Browser/Handy; altes Interface unter /alt)" % (ip, port))

def _webgui_stop():
    srv = _webgui["srv"]
    if srv is None:
        return
    _webgui["srv"] = None
    threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
    olog("Web-GUI beendet.")

_webgui_var = tk.BooleanVar(value=bool(app_config.get("webgui_enabled", True)) and not cliargs.nowebgui)
def _webgui_toggle():
    app_config["webgui_enabled"] = bool(_webgui_var.get())
    app_config_save()
    (_webgui_start if app_config["webgui_enabled"] else _webgui_stop)()
_menu_konfig.add_checkbutton(label="Web-GUI (Port %d, Zapper-Stil): Anzeige und Steuerung im Browser/Handy" % int(app_config.get("webgui_port") or 8100),
                              variable=_webgui_var, command=_webgui_toggle)

_web_var = tk.BooleanVar(value=bool(app_config.get("web_enabled", False)) or cliargs.web)
def _web_toggle():
    app_config["web_enabled"] = bool(_web_var.get())
    app_config_save()
    (_web_start if app_config["web_enabled"] else _web_stop)()
_menu_konfig.add_checkbutton(label="Altes Webinterface (Port %d): nur mit diesem Haken oder Parameter -web" % int(app_config.get("web_port") or 8099),
                              variable=_web_var, command=_web_toggle)
root.after(1500, _web_snapshot_tick)
if app_config.get("web_enabled", False) or cliargs.web:
    root.after(2500, _web_start)
if _webgui_var.get():
    root.after(2600, _webgui_start)

root.mainloop()

if _gui_restart_requested[0]:
    print("GUI-Neustart ...", flush=True)
    os.execv(sys.executable, [sys.executable] + (_restart_argv[0] or sys.argv))

