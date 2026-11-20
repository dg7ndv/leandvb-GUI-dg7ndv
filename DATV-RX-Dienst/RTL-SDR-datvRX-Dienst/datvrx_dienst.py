#!/usr/bin/env python3
"""datvrx_dienst.py - RTL-SDR-datvRX-Dienst: QO-100-Signale scannen, mit datvrx am RTL-Stick dekodieren und ohne GUI bereitstellen (DG7NDV)

Ein Programm ohne Oberflaeche, das alles zusammenfasst:
  * Start: zuerst die Bake mit dem vorgegebenen LO suchen ("Bake gelockt", Ablage -> Frequenzkorrektur); ohne gelockte Bake kein Scan und eine
    Fehlermeldung auf der Webseite (Knopf "Bake erneut suchen"; automatisch alle 90 s neu)
  * BATC-Signalerkennung (wie leandvb-GUI), Auto-Scan nur bei neuen Signalen (Schalter auf der Webseite)
  * datvrx am RTL-Stick: Rufzeichen (Dienstname/SDT), Modulation/FEC, Es/N0, MER, Pegel, Bitrate, TEI/CC  (Kern: ../datv_core.py)
  * Stream: HTTP fuer VLC (Playlist /qo100.m3u, "naechstes" schaltet das Signal um) und UDP-Multicast 230.0.0.30:1234 (RTL1), 230.0.0.31:1234 (RTL2)
  * Webseite http://<IP>:8554/ mit Tabelle und Scan-Knopf, JSON /status
  * MQTT (optional, --mqtt HOST): neue/verschwundene Signale, Status aller Signale, Messwerte je Signal

Konfiguration: dienst.conf neben diesem Programm (Vorlage: dienst.conf.beispiel) - LO, ppm, Verstaerkung, Stick-Nummer, Ports, MQTT, Grenzwerte.
               Vorrang: Kommandozeile > dienst.conf > ~/.leandvb-GUI/parameters.json (LO/ppm) > Standardwerte.  Andere Datei: --config DATEI
Aufruf:  ./datvrx_dienst.py [--http 8554] [--mcast 230.0.0.20] [--port 1234] [--mqtt 192.168.0.10[:1883]] [--prefix qo100/dienst]
                            [--dwell 28] [--idle 60] [--lo 9540.054] [--ppm 26] [--quiet]
Start/Stopp bequem:  ./dienst.sh start|stop|status|log

MQTT-Themen (Prefix aus dienst.conf, Vorlage "qo100/dienst/%h" - %h = Hostname des Pis; MQTT-Client-ID "qo100-datvrx-dienst-<Hostname>"):
  <prefix>/online           online / offline (retained, Last-Will)
  <prefix>/signal/neu       {"ereignis":"neu","freq_mhz":..,"sr_ks":..,"zeit":..}
  <prefix>/signal/weg       {"ereignis":"weg", ...}
  <prefix>/cpu              Rechenzeit des Rechners/Programms, Temperatur, Warnung (alle 30 s und bei Aenderung der Warnung)
  <prefix>/bake             Ergebnis der Bake-Pruefung (retained): status ok/fehler/suche, Meldung, Ablage, LO-Korrektur
  <prefix>/signale          Status aller Signale (retained, nur bei Aenderung): aktuelles Signal, Auto-Scan, je Signal die letzte Messung
  <prefix>/messung          eine Messung (nach Scan/Empfang): Rufzeichen, Provider, Modulation, FEC, Es/N0, MER, Pegel, Bitrate, Fehler
Der RTL-Stick gehoert dem Dienst; er laeuft nicht zusammen mit zapper.py / qo100_wacht.py (gleiche Sperrdatei, gleicher Port).
"""
import argparse, configparser, json, os, re, signal, socket, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)            # DATV-RX-Dienst: zapper.py (Motor), datv_core.py, qo100_wacht.py, signal_da.py, datvrx_rx.sh, datvrx

# --- Konfiguration: dienst.conf (INI, Abschnitt [dienst]); Kommandozeile ueberschreibt
pre = argparse.ArgumentParser(add_help=False)
pre.add_argument("--config", default=os.path.join(HERE, "dienst.conf"))
pa, _rest = pre.parse_known_args()
_ORIG_ARGV = list(sys.argv[1:])
cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
cp.read(pa.config)
C = cp["dienst"] if cp.has_section("dienst") else {}


def cfg(key, typ, default):
    v = (C.get(key) or "").strip() if hasattr(C, "get") else ""
    if v == "":
        return default
    try:
        return typ(v)
    except ValueError:
        print("dienst.conf: Wert von '%s' ungueltig (%r) - Standard %r" % (key, v, default), file=sys.stderr)
        return default


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--config", default=pa.config, help="Konfigurationsdatei (Standard: dienst.conf neben dem Programm)")
ap.add_argument("--http", type=int, default=cfg("http_port", int, 8554))
ap.add_argument("--mobile-port", type=int, default=cfg("handy_port", int, 8555), help="Port der schmalen Handy-Seite (0 = aus)")
ap.add_argument("--mcast", default=cfg("multicast", str, "230.0.0.30"))
ap.add_argument("--port", type=int, default=cfg("multicast_port", int, 1234))
ap.add_argument("--mqtt", default=cfg("mqtt", str, None) or None, help="MQTT-Broker HOST[:PORT] (leer = kein MQTT)")
ap.add_argument("--prefix", default=cfg("mqtt_prefix", str, "qo100/dienst"))
ap.add_argument("--dwell", type=int, default=cfg("scan_sekunden_je_signal", int, 28))
ap.add_argument("--lo", type=float, default=cfg("lo_mhz", float, None), help="LNB-Oszillator MHz (sonst parameters.json, sonst 9540)")
ap.add_argument("--ppm", type=int, default=cfg("ppm", int, None), help="Frequenzkorrektur des Sticks (sonst parameters.json, sonst 0)")
ap.add_argument("--gain", default=cfg("verstaerkung_db", str, None), help="Tuner-Verstaerkung dB (Standard 40)")
ap.add_argument("--index", default=cfg("stick_nr", str, None), help="RTL-Stick: Nummer oder Seriennummer (Standard 0)")
ap.add_argument("--index2", default=cfg("stick2_nr", str, None), help="zweiter RTL-Stick (RTL2): Nummer oder Seriennummer; leer = nur ein Stick")
ap.add_argument("--mcast2", default=cfg("multicast2", str, None), help="Multicast-Adresse von RTL2 (Standard: Adresse von RTL1, letzte Stelle + 1)")
ap.add_argument("--sticks", default=cfg("sticks", str, None), help="RTL-Sticks: 'auto' = beim Start am USB-Bus suchen (bis 10), oder Liste (Nummer/Seriennummer, Komma); leer = stick_nr/stick2_nr")
ap.add_argument("--spek-server", default=cfg("spektrum_server", str, None) or "", help="Ordner des RTL-SDR-Spektrum-Servers von HB9IIU (Ersatz, wenn der BATC-Server ausfaellt); leer = aus")
ap.add_argument("--spek-korr-khz", type=float, default=cfg("spektrum_korr_khz", float, 0.0))
ap.add_argument("--spek-port", type=int, default=cfg("spektrum_port", int, 7681))
ap.add_argument("--bake-wait", type=int, default=cfg("bake_sekunden_je_versuch", int, 25), help="Bake-Pruefung beim Start: Sekunden je Versuch (3 Versuche)")
ap.add_argument("--cpu-warn", type=float, default=cfg("cpu_warnung_prozent", float, 80.0), help="Warnung im Browser (und MQTT), wenn die Rechenzeit ueber 20 s darueber liegt (%%)")
ap.add_argument("--idle", type=int, default=cfg("stick_frei_nach_sekunden", int, 60))
ap.add_argument("--auto-scan", default=cfg("auto_scan", str, "ja"), choices=("ja", "nein"), help="Auto-Scan beim ersten Start (danach gilt der Schalter auf der Webseite)")
ap.add_argument("--title", default=cfg("kopfzeile", str, "DG7NDV"), help="Name/Rufzeichen in der Kopfzeile der Webseite")
ap.add_argument("--quiet", action="store_true", help="keine Konsolenausgabe (Dienstbetrieb)")
A = ap.parse_args()
HOST = re.sub(r"[^A-Za-z0-9_-]", "_", os.uname().nodename) or "pi"
A.prefix = A.prefix.replace("%h", HOST)       # %h im Prefix = Hostname des Pis (mehrere Pis an einem Broker)


def udp_basis_abstimmen():
    """Mehrere Dienste an einem Broker: per MQTT fragen, welche UDP-Multicast-Basisadressen andere Dienste schon belegen.
    Jeder Dienst legt beim Start retained "qo100/udp/<Hostname>" = {"basis": ..., "port": ...} ab (Eintrag bleibt als Reservierung stehen).
    Belegt ein anderer Host dieselbe Basis (oder eine im Abstand < 10), wird die letzte Stelle der eigenen Basis in 10er-Schritten
    erhoeht (230.0.0.30 -> 40 -> 50 ...); eine eigene Adresse fuer RTL2 (multicast2) wird mitverschoben. Aus: multicast_auto = nein."""
    if not A.mqtt or (C.get("multicast_auto") or "ja").strip().lower() in ("nein", "aus", "0", "false"):
        return
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return
    def meldung(t):
        if not A.quiet:
            print("UDP-Abstimmung: " + t, file=sys.stderr)
    belegt = {}
    def on_msg(c, u, m, *a):
        try:
            d = json.loads(m.payload.decode())
            h = m.topic.rsplit("/", 1)[1]
            if h != HOST and d.get("basis"):
                belegt[h] = d["basis"]
        except Exception:
            pass                                               # leere/ungueltige Eintraege ignorieren
    host, _, port = A.mqtt.partition(":")
    try:
        try:
            cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="qo100-udp-" + HOST)
        except AttributeError:
            cl = mqtt.Client(client_id="qo100-udp-" + HOST)
        cl.on_message = on_msg
        cl.connect(host, int(port or 1883), 10)
        cl.subscribe("qo100/udp/+", qos=1)
        cl.loop_start()
        time.sleep(3)                                          # retained Eintraege der anderen Dienste abwarten
    except Exception as e:
        meldung("Broker %s nicht erreichbar (%s) - Adresse unveraendert %s" % (A.mqtt, e, A.mcast)); return
    t = A.mcast.split("."); last = int(t[3]); neu = last
    def konflikt(l):
        return any(b.split(".")[:3] == t[:3] and abs(int(b.split(".")[3]) - l) < 10 for b in belegt.values())
    while konflikt(neu) and neu + 10 <= 250:
        neu += 10
    if konflikt(neu):
        meldung("keine freie Basisadresse in %s.x gefunden - bleibe bei %s" % (".".join(t[:3]), A.mcast)); neu = last
    if neu != last:
        meldung("%s ist schon belegt (%s) - nehme %s.%d" % (A.mcast, ", ".join("%s=%s" % kv for kv in sorted(belegt.items())), ".".join(t[:3]), neu))
        if A.mcast2 and A.mcast2.split(".")[:3] == t[:3]:
            A.mcast2 = "%s.%d" % (".".join(t[:3]), int(A.mcast2.split(".")[3]) + neu - last)
        A.mcast = "%s.%d" % (".".join(t[:3]), neu)
    else:
        meldung("Basisadresse %s frei (andere Dienste: %s)" % (A.mcast, ", ".join("%s=%s" % kv for kv in sorted(belegt.items())) or "keine"))
    try:
        ip = ""
        try:                                                   # eigene Adresse im Netz des Brokers (damit Clients den Pi ohne Namensaufloesung finden)
            so = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            so.connect((host, int(port or 1883))); ip = so.getsockname()[0]; so.close()
        except OSError:
            pass
        cl.publish("qo100/udp/" + HOST, json.dumps({"basis": A.mcast, "port": A.port, "host": HOST, "ip": ip, "http": A.http, "zeit": time.strftime("%Y-%m-%d %H:%M:%S")}),
                   qos=1, retain=True).wait_for_publish(3)
    except Exception:
        pass
    cl.loop_stop(); cl.disconnect()


udp_basis_abstimmen()

# Motor (zapper.py) mit unseren Werten laden; er liest sys.argv beim Import
sys.path.insert(0, PARENT)
sys.argv = [os.path.join(PARENT, "zapper.py"), "--http", str(A.http), "--mobile-port", str(A.mobile_port), "--mcast", A.mcast, "--port", str(A.port),
            "--dwell", str(A.dwell), "--idle", str(A.idle), "--dvx-dir", PARENT, "--bake-wait", str(A.bake_wait), "--cpu-warn", str(A.cpu_warn)]
if A.spek_server:
    sys.argv += ["--spek-server", A.spek_server, "--spek-korr-khz", str(A.spek_korr_khz), "--spek-port", str(A.spek_port)]
if A.lo:
    sys.argv += ["--lo", str(A.lo)]
if A.ppm is not None:
    sys.argv += ["--ppm", str(A.ppm)]
if A.gain:
    sys.argv += ["--gain", str(A.gain)]
if A.index is not None:
    sys.argv += ["--index", str(A.index)]


def find_sticks():
    """alle RTL-Sticks am USB-Bus (rtl_test listet sie auf): Seriennummer, wenn eindeutig und nicht rein numerisch (rtl_sdr liest Ziffern als Nummer), sonst die Nummer"""
    try:
        r = subprocess.run(["rtl_test", "-d", "99"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    devs = re.findall(r"^\s*(\d+):\s+(.*?),\s+SN:\s*(\S*)", r.stdout + r.stderr, re.M)
    sers = [d[2] for d in devs]
    return [(int(n), name, (sn if sn and not sn.isdigit() and sers.count(sn) == 1 else n)) for n, name, sn in devs]


STICKS = None
if (A.sticks or "").strip().lower() == "auto":
    found = find_sticks()
    pref = (A.index or "").strip()
    ids = [str(x[2]) for x in found]
    if pref in ids:                                               # stick_nr bleibt RTL1
        ids.remove(pref); ids.insert(0, pref)
    STICKS = ids[:10]
    print("%s  Stick-Suche: %d RTL-Stick(s): %s" % (time.strftime("%H:%M:%S"), len(found), ", ".join("%s (%s)" % (x[1], x[2]) for x in found) or "keine"), flush=True)
elif A.sticks:
    STICKS = [x.strip() for x in A.sticks.split(",") if x.strip()][:10]
if STICKS:
    sys.argv += ["--sticks", ",".join(STICKS)]
elif A.index2:
    sys.argv += ["--index2", str(A.index2)]
if A.mcast2:
    sys.argv += ["--mcast2", A.mcast2]
sys.argv += ["--auto-scan", A.auto_scan]
sys.argv += ["--title", A.title]
import zapper as z
z.CONF_PATH = os.path.abspath(A.config) if os.path.isfile(A.config) else None
z.RESTART_ARGV = [os.path.abspath(__file__)] + _ORIG_ARGV

if A.quiet:
    sys.stdout = z.Tee(open(os.devnull, "w"))          # keine Konsolenausgabe, aber die Zeilen bleiben fuer den Fehlerbericht erhalten


def say(t):
    print("%s  %s" % (time.strftime("%H:%M:%S"), t), flush=True)


class Mqtt:
    def __init__(s):
        s.cl = None
        if not A.mqtt:
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            say("MQTT: paho-mqtt fehlt (pip install paho-mqtt) - ohne MQTT weiter"); return
        host, _, port = A.mqtt.partition(":")
        try:                                                   # paho 2.x
            s.cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="qo100-datvrx-dienst-" + HOST)
        except AttributeError:                                 # paho 1.x
            s.cl = mqtt.Client(client_id="qo100-datvrx-dienst-" + HOST)
        s.cl.will_set("%s/online" % A.prefix, "offline", qos=1, retain=True)
        s.cl.on_connect = lambda c, u, f, rc, *a: (say("MQTT verbunden (%s)" % A.mqtt), s.pub("online", "online", True), s.force())
        s.cl.reconnect_delay_set(2, 30)
        s.cl.connect_async(host, int(port or 1883), 30)
        s.cl.loop_start()
        s.last = None

    def force(s):
        s.last = None

    def pub(s, topic, payload, retain=False):
        if s.cl is None:
            return
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False, default=str)
        s.cl.publish("%s/%s" % (A.prefix, topic), payload, qos=1, retain=retain)

    def stop(s):
        if s.cl is not None:
            s.pub("online", "offline", True)
            time.sleep(0.5)
            s.cl.loop_stop()


mq = Mqtt()


def _meas(f, sr):
    r = z.zap.snapshot() if z.zap.cur == (f, sr) else z.zap.lookup(f, sr)
    return r


def watch_loop():
    """Aenderungen beobachten: neue/verschwundene Signale, Status bei Aenderung, jede neue Messung einzeln melden"""
    known, stands = [], {}
    mq.last = None
    while True:
        time.sleep(1)
        try:
            cur = z.signals()
            for f, sr in cur:                                        # neu
                if not z.auto.has(known, f, sr):
                    mq.pub("signal/neu", {"ereignis": "neu", "freq_mhz": f, "sr_ks": sr, "zeit": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    say("MQTT/Signal NEU   %.3f MHz %d kS" % (f, sr))
            for f, sr in known:                                      # weg
                if not z.auto.has(cur, f, sr):
                    mq.pub("signal/weg", {"ereignis": "weg", "freq_mhz": f, "sr_ks": sr, "zeit": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    say("MQTT/Signal WEG   %.3f MHz %d kS" % (f, sr))
            known = list(cur)
            for (kf, ks), v in list(z.zap.cache.items()):            # neue Messung einzeln
                if v.get("stand") and stands.get((kf, ks)) != v.get("stand"):
                    stands[(kf, ks)] = v.get("stand")
                    mq.pub("messung", dict(v, freq_mhz=kf, sr_ks=ks))
            sigs = []
            for f, sr in z.signals(True):
                m = _meas(f, sr)
                sigs.append({"freq_mhz": f, "sr_ks": sr, "live": z.zap.cur == (f, sr), "bake": abs(f - 10491.5) < 0.5, "messung": m})
            ci = z.cpu.info()
            if time.time() - getattr(watch_loop, "cpu_t", 0) > 30 or ci["warnung"] != getattr(watch_loop, "cpu_w", ""):
                watch_loop.cpu_t, watch_loop.cpu_w = time.time(), ci["warnung"]
                mq.pub("cpu", ci)
            bk = json.dumps(z.bake, default=str)
            if bk != getattr(watch_loop, "bk", None):
                watch_loop.bk = bk
                mq.pub("bake", z.bake, True)
            sticks = [{"name": q.name, "aktuell": q.cur, "fest": q.pin, "zuschauer": len(q.clients), "bake": q.bake["status"], "udp": "%s:%d" % (q.mcast, q.port)} for q in z.zaps]
            bk2 = json.dumps([q.bake for q in z.zaps[1:]], default=str)
            if z.zaps[1:] and bk2 != getattr(watch_loop, "bk2", None):
                watch_loop.bk2 = bk2
                for q in z.zaps[1:]:
                    mq.pub("bake_%s" % q.name.lower(), q.bake, True)
            status = {"zeit": time.strftime("%Y-%m-%dT%H:%M:%S"), "bake": z.bake["status"], "aktuell": z.zap.cur, "zuschauer": len(z.zap.clients),
                      "auto_scan": z.settings.get("auto_scan"), "scan_laeuft": z.scan.running, "sticks": sticks, "verteiler": {"an": z.dist.on(), "ohne_stick": z.dist.count_open()}, "signale": sigs}
            key = json.dumps([[x["freq_mhz"], x["sr_ks"], x["live"], (x["messung"] or {}).get("stand"), (x["messung"] or {}).get("rufzeichen")]
                              for x in sigs] + [[(q.cur, q.pin) for q in z.zaps], status["aktuell"], status["auto_scan"], status["scan_laeuft"], status["bake"]], default=str)
            if key != mq.last:
                mq.last = key
                mq.pub("signale", status, True)
        except Exception as e:
            say("watch: %r" % (e,))


def main():
    for fn in [z.ws_loop, z.tick_loop, z.auto.loop, z.dist.loop, z.spec_watch, z.displays.loop, z.bake_loops, z.cpu.loop, watch_loop] + [q.idle_watch for q in z.zaps]:
        threading.Thread(target=fn, daemon=True).start()

    def bye(*_a):
        z.displays.release_all(); [q.release() for q in z.zaps]; mq.stop(); os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    lo_, pp_ = z.lo_cfg()
    say("Konfiguration: %s%s  |  LO %.3f MHz, ppm %s, Stick %s, Verstaerkung %s" % (A.config, "" if os.path.isfile(A.config) else " (nicht vorhanden - Standardwerte)", lo_, pp_,
                                                                                   A.index if A.index is not None else 0, A.gain or "40"))
    for q in z.zaps:
        say("%s: Stick %s -> UDP %s:%d" % (q.name, q.index if q.index is not None else 0, q.mcast, q.port))
    say("RTL-SDR-datvRX-Dienst: http://0.0.0.0:%d/  (Playlist /qo100.m3u)%s" % (A.http, ("  MQTT %s/%s" % (A.mqtt, A.prefix)) if A.mqtt else ""))
    z.start_mobile()
    try:
        z.QuietServer(("0.0.0.0", A.http), z.H).serve_forever()
    except KeyboardInterrupt:
        pass
    [q.release() for q in z.zaps]; mq.stop()


if __name__ == "__main__":
    main()
