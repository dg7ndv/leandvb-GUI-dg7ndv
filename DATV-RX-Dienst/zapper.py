#!/usr/bin/env python3
"""zapper.py - QO-100 "Zapper": Signale per VLC (Handy) durchschalten, Messwerte und Scan per Webseite (DG7NDV)

Aufruf:  ./zapper.py [--http 8554] [--mcast 230.0.0.20] [--port 1234] [--dvx-dir <Ordner>]

Am Handy (VLC): Netzwerkstream/Wiedergabeliste oeffnen   http://<IP-dieses-Rechners>:8554/qo100.m3u
  - die Playlist enthaelt je ein Eintrag pro aktuell vorhandenem Signal nach Frequenz (ohne Bake; die steht nur auf der Webseite und unter /z/10491.500/1500)
  - Taste "naechstes" (|>) in VLC schaltet auf das naechste Signal: der Stick wird umgestellt, der Strom laeuft weiter
  - ein Eintrag, dessen Signal inzwischen weg ist, wird mit 404 beantwortet (VLC springt dann weiter) - Playlist neu laden zeigt die aktuelle Liste
Auto-Scan: ein Durchlauf ueber alle Signale, danach Ruhe; nur wenn ein NEUES Signal erscheint (oder eines nach dem Verschwinden wiederkommt),
  werden die neuen gescannt. Schalter auf der Webseite (gespeichert in zapper_einstellungen.json); Zuschauer werden nicht gestoert.
Webseite  http://<IP>:8554/ :
  - Tabelle aller Signale; in jeder Zeile Rufzeichen (Dienstname aus der SDT), Provider, Modulation/FEC, Es/N0, MER, Pegel, Bitrate, Fehler
    und die Uhrzeit der Messung. Das gerade empfangene Signal (live) ist hervorgehoben, die anderen zeigen den Stand der letzten Messung/des Scans.
  - Knopf "Scan": alle Signale nacheinander einstellen, Rufzeichen dekodieren, Werte in die Zeilen eintragen
  - Antippen eines Signals oeffnet den Stream (VLC)
JSON-Status: /status      Scan per Kommandozeile:  curl -X POST http://<IP>:8554/scan/start   (/scan/stop)
Der Transportstrom geht zusaetzlich per UDP-Multicast auf 230.0.0.20:1234 (TS-Analyse, GUI-Player).

Erkennung der Signale wie leandvb-GUI (BATC-Spektrum, siehe qo100_wacht.py). Der Stick gehoert dem Zapper, solange ein Zuschauer verbunden
ist oder ein Scan laeuft (+ 60 s Nachlauf); in der Zeit pausiert der Waechter (Sperrdatei /tmp/qo100_zapper.lock).
"""
import argparse, collections, html, json, math, os, queue, re, shutil, signal, socket, struct, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websocket

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--http", type=int, default=8554)
ap.add_argument("--mobile-port", type=int, default=0, help="zweiter Port mit der schmalen Handy-Seite (Signalliste, Tippen startet VLC); 0 = aus")
ap.add_argument("--mcast", default="230.0.0.20")
ap.add_argument("--port", type=int, default=1234)
ap.add_argument("--dvx-dir", default=os.path.dirname(os.path.abspath(__file__)), help="Ordner mit qo100_wacht.py, signal_da.py, datvrx_rx.sh und datvrx")
ap.add_argument("--idle", type=int, default=60, help="Sekunden ohne Zuschauer, dann Stick freigeben")
ap.add_argument("--dwell", type=int, default=28, help="Scan: maximale Sekunden je Signal")
ap.add_argument("--lo", type=float, help="LNB-Oszillator in MHz (extern vorgegeben; sonst lnb_lo aus ~/.leandvb-GUI/parameters.json, sonst 9540)")
ap.add_argument("--ppm", type=int, help="Frequenzkorrektur des RTL-Sticks (sonst ppm aus parameters.json, sonst 0)")
ap.add_argument("--cpu-warn", type=float, default=80.0, help="Warnung im Browser, wenn Rechenzeit (System oder Programm, in %% der Gesamtleistung) ueber 20 s darueber liegt")
ap.add_argument("--gain", help="Tuner-Verstaerkung des RTL-Sticks in dB (sonst Standard von datvrx_rx.sh = 40)")
ap.add_argument("--index", help="RTL-Stick: Nummer oder Seriennummer (rtl_sdr -d), Standard 0. Eine Seriennummer bleibt stabil, wenn sich die Reihenfolge am USB-Bus aendert (nicht rein numerisch, sonst wird sie als Nummer gelesen)")
ap.add_argument("--sticks", help="alle RTL-Sticks als Liste (Nummer oder Seriennummer, durch Komma getrennt, bis 10): RTL1, RTL2, ... (ersetzt --index/--index2)")
ap.add_argument("--index2", help="zweiter RTL-Stick (RTL2): Nummer oder Seriennummer; leer = nur ein Stick")
ap.add_argument("--mcast2", help="UDP-Multicast-Adresse des zweiten Sticks (Standard: Adresse des ersten, letzte Stelle + 1)")
ap.add_argument("--spek-server", default="", help="Ordner des RTL-SDR-Spektrum-Servers von HB9IIU (main_lo9540.py oder main.py; separat installieren, GPLv3). Leer = kein Stick-Scan als Spektrum-Ersatz")
ap.add_argument("--spek-korr-khz", type=float, default=0.0, help="Frequenzkorrektur des Stick-Scans in kHz (siehe Doku des Servers, --correction-khz)")
ap.add_argument("--spek-intervall", type=int, default=600, help="Stick-Scan, wenn alle Sticks belegt sind: so oft (Sekunden) wird der hoechste Stick kurz fuer einen Scan genommen (0 = nur einmal)")
ap.add_argument("--spek-port", type=int, default=7681, help="WebSocket-Port des Spektrum-Servers (nur lokal)")
ap.add_argument("--auto-scan", default="ja", choices=("ja", "nein"), help="Auto-Scan beim ersten Start (danach gilt der Schalter auf der Webseite)")
ap.add_argument("--title", default="DG7NDV", help="Name/Rufzeichen in der Kopfzeile der Webseite")
ap.add_argument("--bake-wait", type=int, default=25, help="Bake-Pruefung beim Start: Sekunden je Versuch (3 Versuche)")
A = ap.parse_args()
MAX_STICKS = 10


def mcast_n(n):
    """Multicast-Adresse des n-ten Sticks (n=0: RTL1): letzte Stelle + n"""
    p = A.mcast.split(".")
    return ".".join(p[:3] + [str(int(p[3]) + n)])


if A.mcast2 is None:
    A.mcast2 = mcast_n(1)

sys.path.insert(0, A.dvx_dir)
import qo100_wacht as w          # Erkennung (analyze/tick/state)
try:
    import fehlerbericht as fb   # Fehlerbericht: Fehler sammeln, Bericht in die Zwischenablage / als Datei / per Mail
except Exception:                # ohne das Modul laeuft alles weiter, nur ohne Berichte
    class fb:                    # noqa: N801
        ERRORS = collections.deque(maxlen=1)
        add = staticmethod(lambda *a, **k: None)
        clear = staticmethod(lambda: None)
        install_hooks = staticmethod(lambda *a, **k: None)
        report = staticmethod(lambda *a, **k: "(Modul fehlerbericht.py fehlt)\n")
        version_of = staticmethod(lambda *a: "?")
        mailto = staticmethod(lambda *a, **k: "mailto:")
import datv_core as core         # gemeinsamer Kern: TsInfo (Rufzeichen/Bitrate/Fehler), MER, Pegel - derselbe Code wie in der leandvb-GUI

LOGBUF = collections.deque(maxlen=250)       # letzte Konsolenmeldungen (fuer den Fehlerbericht)


class Tee:
    """sys.stdout mitschneiden: jede Zeile landet zusaetzlich in LOGBUF"""
    def __init__(s, out):
        s.out, s.rest = out, ""

    def write(s, t):
        s.rest += t
        while "\n" in s.rest:
            ln, s.rest = s.rest.split("\n", 1)
            if ln.strip():
                LOGBUF.append(ln)
        try:
            return s.out.write(t)
        except Exception:
            return len(t)

    def flush(s):
        try:
            s.out.flush()
        except Exception:
            pass


if not isinstance(sys.stdout, Tee):
    sys.stdout = Tee(sys.stdout)
fb.install_hooks("QO 100 Multi-Zapper")


class QuietServer(ThreadingHTTPServer):
    """meldet Fehler in Anfragen auch an den Fehlerbericht (sonst nur auf der Konsole)"""
    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if e is not None and not isinstance(e, (BrokenPipeError, ConnectionResetError)):
            fb.add("Webseite (Anfrage von %s)" % (client_address[0],), exc=e)
        super().handle_error(request, client_address)


LOCK = "/tmp/qo100_zapper.lock"
DVX = os.path.join(A.dvx_dir, "datvrx_rx.sh")
SIGDA = os.path.join(A.dvx_dir, "signal_da.py")
SETFILE = os.path.join(A.dvx_dir, "zapper_einstellungen.json")
MESSFILE = os.path.join(A.dvx_dir, "zapper_messwerte.json")          # letzte Messung je Signal, ueberlebt einen Neustart


def _DEFAULTS():
    # verteiler: Autoverteiler an/aus; verteilen: je Stick, ob er zum Verteilen benutzt wird
    return {"auto_scan": A.auto_scan != "nein", "verteiler": True, "verteilen": {}, "bake_rueckfall": True, "spektrum_quelle": "auto"}


def load_settings():
    try:
        return dict(_DEFAULTS(), **json.load(open(SETFILE)))
    except Exception:
        return _DEFAULTS()


def save_settings():
    try:
        json.dump(settings, open(SETFILE, "w"))
    except OSError:
        pass


settings = load_settings()           # auto_scan: bei neuem Signal automatisch scannen (Schalter auf der Webseite)


def lo_cfg():
    """LO und ppm wie vorgegeben (--lo/--ppm) oder aus parameters.json der leandvb-GUI, sonst 9540 MHz / 0"""
    try:
        d = json.load(open(os.path.expanduser("~/.leandvb-GUI/parameters.json")))
    except Exception:
        d = {}
    return (A.lo or float(d.get("lnb_lo") or 9540.0)), (A.ppm if A.ppm is not None else int(d.get("ppm") or 0))


# Bake-Pruefung beim Start: ohne gelockte Bake kein Scan und keine anderen Signale. Aus der Ablage der Bake folgt die Frequenzkorrektur (LO).
# Bake je Stick: zap.bake (RTL1 = globales dict 'bake', unten angelegt)


TsInfo = core.TsInfo


class Zap:
    cache_all = None                      # Messwerte je Signal, von allen Sticks gemeinsam genutzt

    def __init__(s, name="RTL1", index=None, mcast=None, port=None, bk=None):
        s.name, s.index = name, index
        s.last_data = 0.0                 # Zeitpunkt des letzten TS-Datenblocks (Display-Ueberwachung: kommt ein Strom an?)
        s.mcast, s.port = mcast or A.mcast, port or A.port
        s.bake = bk if bk is not None else {"status": "suche", "msg": "Bake (10491.500 MHz) wird gesucht ...", "ablage_khz": None,
                                            "lo_cfg": None, "lo_eff": None, "esn0": None, "versuch": 0, "zeit": None}
        s.primary = bk is not None
        s.manual = False                  # von Hand eingestellt (Seite /manuell): freie Frequenz, Symbolrate evtl. automatisch
        s.pin = False                     # per Webseite eingestellt: Stick bleibt auf dem Signal, bis ein anderes gewaehlt/freigegeben wird
        s.lock = threading.RLock()
        s.cur = None                      # (freq, sr) das gerade empfangen wird
        s.rx = None
        s.clients = {}                    # Queue -> (freq, sr)
        s.last_client = time.time()
        s.info = {}
        s.ts = TsInfo()
        s.mer = collections.deque(maxlen=20)
        s.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.gen = 0
        s.hold = False                    # Scan laeuft: Stick nicht freigeben
        if Zap.cache_all is None:
            Zap.cache_all = {}            # (Frequenz auf 10 kHz, kS) -> letzte Messung (fuer die Zeilen der Tabelle)
            try:
                for k, v in json.load(open(MESSFILE)).items():
                    f, sr = k.split("|")
                    Zap.cache_all[(float(f), int(sr))] = v
            except Exception:
                pass
        s.cache = Zap.cache_all

    @staticmethod
    def key(f, sr):
        return (round(f * 100) / 100.0, sr)

    def put(s, f, sr, snap):
        for k in [k for k in s.cache if k[1] == sr and abs(k[0] - f) < 0.06]:
            del s.cache[k]
        s.cache[s.key(f, sr)] = snap
        try:
            json.dump({"%s|%d" % k: v for k, v in s.cache.items()}, open(MESSFILE, "w"), default=str)
        except OSError:
            pass

    def remember(s):
        """Messung des laufenden Empfangs fuer die Tabelle merken (nur wenn gelockt)"""
        if s.cur and s.info.get("lock") and not s.manual:
            snap = s.snapshot()
            snap["stand"] = time.strftime("%d.%m. %H:%M:%S")
            s.put(s.cur[0], s.cur[1], snap)

    def lookup(s, f, sr):
        for (kf, ks), v in s.cache.items():
            if ks == sr and abs(kf - f) < 0.06:
                return v
        return None

    def _kill(s):
        s.remember()
        if s.rx:
            for sg in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(s.rx.pid, sg)
                except Exception:
                    pass
                time.sleep(0.5 if sg == signal.SIGTERM else 0)
            s.rx = None
        s.cur = None
        s.manual = False
        s.info = {}

    def release(s):
        with s.lock:
            s.pin = False
            s._kill()
            if s.primary:
                try:
                    os.remove(LOCK)
                except OSError:
                    pass

    def _pegel(s, f, sr):
        """Pegel am Stick (1 s Messung vor dem Empfang, nur bis 1000 kS) aus datv_core"""
        r = core.pegel(f, sr, lo=(s.bake.get("lo_eff") or A.lo), ppm=A.ppm, index=(s.index or 0), gain=A.gain)
        return {"pegel_db": r["db"], "pegel_dbfs": r["dbfs"], "rausch_dbfs": r["rausch_dbfs"]} if r else {}

    def tune(s, f, sr, tries=12, manual=False):
        """manual: freie Frequenz (unter dem LO = direkt), sr 0 = Symbolrate automatisch; ohne Pegelmessung, nicht in der Signaltabelle"""
        with s.lock:
            if s.cur == (f, sr) and bool(s.manual) == manual and s.rx and s.rx.poll() is None:
                return True
            s._kill()
            if s.primary:
                with open(LOCK, "w") as fh:
                    fh.write(str(os.getpid()))
            pegel = {}
            for _ in range(tries):                     # Stick evtl. noch vom Waechter belegt: erneut versuchen
                if not pegel and not manual:
                    pegel = s._pegel(f, sr)
                extra = (["--lo", "%.4f" % s.bake["lo_eff"]] if s.bake.get("lo_eff") else (["--lo", "%.4f" % A.lo] if A.lo else []))
                extra += (["--ppm", str(A.ppm)] if A.ppm is not None else [])
                extra += (["--gain", str(A.gain)] if A.gain else []) + (["--index", str(s.index)] if s.index is not None else [])
                rx = subprocess.Popen([DVX, "rtl-sdr", "%.3f" % f] + ([str(sr), "--rs"] if sr > 0 else []) + ["--cells", "1024"] + extra, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, start_new_session=True)
                s.gen += 1
                g = s.gen
                s.cur, s.rx = (f, sr), rx
                s.manual = manual
                s.ts = TsInfo()
                s.mer.clear()
                s.info = {"freq": f, "sr": sr, "start": time.time()}
                s.info.update(pegel)
                threading.Thread(target=s._out, args=(rx, g), daemon=True).start()
                threading.Thread(target=s._err, args=(rx, g), daemon=True).start()
                for _ in range(8):                      # 4 s: laeuft die Kette?
                    time.sleep(0.5)
                    if rx.poll() is not None:
                        break
                if rx.poll() is None:
                    return True
                s._kill()
                time.sleep(2)
            return False

    def _out(s, rx, g):
        fd = rx.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data or g != s.gen:
                break
            s.last_data = time.time()
            try:
                s.udp.sendto(data, (s.mcast, s.port))
            except OSError:
                pass
            try:
                s.ts.feed(data)
            except Exception:
                pass
            for q, t in list(s.clients.items()):
                if t == s.cur:
                    try:
                        q.put_nowait(data)
                    except queue.Full:
                        pass

    def _err(s, rx, g):
        for raw in iter(lambda: rx.stderr.readline(), b""):
            for p in raw.decode(errors="replace").replace("\r", "\n").splitlines():
                if g != s.gen:
                    return
                if p.startswith("datvrx cells "):
                    if s.info.get("lock") and s.info.get("mod") == "QPSK":
                        v = core.mer_qpsk(p.split(" ", 2)[2].strip())
                        if v is not None:
                            s.mer.append(v)
                    continue
                m = re.match(r"datvrx carrier ([+-][\d.]+)", p)
                if m:
                    s.info["carrier_hz"] = float(m.group(1)); continue
                m = re.match(r"datvrx \[\s*([\d.]+) s\] (DVB-S2?)\s+(\S+)\s+(\S+)((?: pilots| short)*)\s+([\d.]+) Msym/s\s+Es/N0 ([\d.-]+) dB\s+ok (\d+)\s+bad (\d+)", p)
                if m:
                    if not s.info.get("lock"):
                        s.info["lock_t"] = time.time()
                    s.info.update({"lock": True, "std": m.group(2), "mod": m.group(3), "fec": m.group(4), "extra": m.group(5).strip(),
                                   "esn0": float(m.group(7)), "sr_det": float(m.group(6)), "ok": int(m.group(8)), "bad": int(m.group(9))})
                elif "carrier not found" in p:
                    s.info["lock"] = False

    def mer_now(s):
        return core.mer_mean(s.mer)

    def snapshot(s):
        """aktuelle Messwerte des laufenden Empfangs als dict"""
        i = dict(s.info)
        mbit, null = s.ts.stats()
        return {"freq": i.get("freq"), "sr": i.get("sr"), "lock": bool(i.get("lock")), "mod": i.get("mod"), "fec": i.get("fec"),
                "extra": i.get("extra", ""), "std": i.get("std"), "esn0": i.get("esn0"), "mer": s.mer_now(),
                "pegel_db": i.get("pegel_db"), "pegel_dbfs": i.get("pegel_dbfs"), "rausch_dbfs": i.get("rausch_dbfs"),
                "sr_det": i.get("sr_det"), "carrier_hz": i.get("carrier_hz"), "rufzeichen": s.ts.service, "provider": s.ts.provider, "bitrate": round(mbit, 3), "null_pct": round(null, 1),
                "tei": s.ts.tei, "cc": s.ts.cc_err, "ok": i.get("ok"), "bad": i.get("bad"),
                "lock_s": round(i["lock_t"] - i["start"], 1) if i.get("lock_t") else None}

    def idle_watch(s):
        while True:
            time.sleep(5)
            s.remember()
            if s.rx and not s.clients and not s.hold and not s.pin and time.time() - s.last_client > A.idle:
                print("%s  %s: keine Zuschauer - Stick frei" % (time.strftime("%H:%M:%S"), s.name), flush=True)
                s.release()


bake = {"status": "suche", "msg": "Bake (10491.500 MHz) wird gesucht ...", "ablage_khz": None, "lo_cfg": None, "lo_eff": None,
        "esn0": None, "versuch": 0, "zeit": None}
zap = Zap("RTL1", A.index, A.mcast, A.port, bk=bake)       # RTL1: Scan, Auto-Scan und Playlist laufen auf diesem Stick
zaps = [zap]
_idx = [x.strip() for x in A.sticks.split(",") if x.strip()] if A.sticks else ([A.index2] if A.index2 not in (None, "") else [])
if A.sticks and _idx:
    zap.index = _idx.pop(0)                                # RTL1 aus der Liste
for _n, _ix in enumerate(_idx[:MAX_STICKS - 1], 1):         # RTL2 ... RTL10: von Hand ueber die Webseite, /z2/... oder durch den Verteiler
    zaps.append(Zap("RTL%d" % (_n + 1), _ix, A.mcast2 if _n == 1 else mcast_n(_n), A.port))


def live_zap(f, sr):
    """Stick, der gerade auf diesem Signal steht (RTL1 vor RTL2) oder None"""
    return next((q for q in zaps if q.cur == (f, sr) and not q.manual), None)


def signals(bake=False):
    """aktuell vorhandene Signale nach Frequenz; die Bake nur mit bake=True (Webseite und Stream /z/10491.500/1500); Playlist, Scan und Auto-Scan ohne Bake"""
    with w.lock:
        lst = [(st["freq"], st["sr"]) for st in w.state.values() if st["gone_since"] is None and abs(st["freq"] - w.BEACON_NOM) > 0.5]
    return ([(10491.5, 1500)] if bake else []) + sorted(lst)


def label(f, sr):
    return "%.3f MHz %s%s" % (f, ("%dK" % sr) if sr else "SR auto", " Bake" if abs(f - 10491.5) < 0.5 else "")


class Scan:
    """Alle Signale nacheinander einstellen, bis Rufzeichen und MER da sind (oder Zeit um); Werte landen im Cache und damit in den Zeilen der Tabelle"""

    def __init__(s):
        s.running, s.stop = False, False
        s.rows, s.idx, s.total, s.cur, s.started, s.ended = [], 0, 0, None, None, None
        s.only, s.auto = None, False

    def start(s, only=None, auto=False):
        """only: nur diese Signale (Schluessel aus Zap.key), sonst alle; auto: durch neues Signal ausgeloest"""
        if s.running:
            return False
        s.only, s.auto = only, auto
        s.running, s.stop, s.rows, s.idx = True, False, [], 0
        s.started, s.ended = time.time(), None
        threading.Thread(target=s._run, daemon=True).start()
        return True

    def _run(s):
        try:
            todo = signals()
            if s.only is not None:
                todo = [x for x in todo if auto.has(s.only, *x)]
            s.total = len(todo)
            dist.drop(zap)
            zap.pin = False
            zap.hold = True
            print("%s  SCAN%s: %d Signale" % (time.strftime("%H:%M:%S"), " (neues Signal)" if s.auto else "", s.total), flush=True)
            for k, (f, sr) in enumerate(todo, 1):
                if s.stop:
                    break
                s.idx, s.cur = k, (f, sr)
                row = {"freq": f, "sr": sr, "stand": time.strftime("%d.%m. %H:%M:%S")}
                if not zap.tune(f, sr):
                    row.update({"ergebnis": "Stick belegt"}); s.rows.append(row); auto.retry_after = time.time() + 60; continue
                auto.add(f, sr)
                end, why = time.time() + A.dwell, "Zeit um"
                while time.time() < end and not s.stop:
                    time.sleep(1)
                    snap = zap.snapshot()
                    if abs(f - 10491.5) > 0.5 and not any(abs(ff - f) < 0.03 and ss == sr for ff, ss in signals()):
                        why = "Signal verschwunden"; break
                    waited = time.time() - (zap.info.get("lock_t") or time.time())
                    mer_ok = snap["mod"] != "QPSK" or snap["mer"] is not None
                    if snap["lock"] and snap["rufzeichen"] and mer_ok and waited >= 6:
                        why = "fertig"; break
                snap = zap.snapshot()
                snap.update(row)
                snap["ergebnis"] = ("decodiert" if snap["lock"] and snap["bitrate"] > 0 else ("Lock ohne Daten" if snap["lock"] else "kein Lock")) + (" (%s)" % why if why != "fertig" else "")
                s.rows.append(snap)
                zap.put(f, sr, snap)
                print("%s  SCAN %d/%d %s: %s %s %s Es/N0 %s MER %s" % (time.strftime("%H:%M:%S"), k, s.total, label(f, sr), snap["ergebnis"],
                      snap["rufzeichen"], snap["mod"], snap["esn0"], snap["mer"]), flush=True)
        finally:
            zap.hold = False
            zap.last_client = time.time()
            if zap.cur and abs(zap.cur[0] - 10491.5) < 0.5 and not zap.clients:
                zap.pin = False                                      # Scan fertig: steht RTL1 noch auf der Bake, frei fuer den Verteiler
                dist.bake_hold.discard(zap.name)
            s.running, s.cur, s.ended = False, None, time.time()
            print("%s  SCAN beendet" % time.strftime("%H:%M:%S"), flush=True)


scan = Scan()


class Auto:
    """Auto-Scan: ein Durchlauf ueber alle Signale, danach Ruhe; erst wenn ein NEUES Signal erscheint (oder eines nach Verschwinden wiederkommt),
    werden nur die neuen Signale gescannt. Haupt-Schalter: settings["auto_scan"]."""

    def __init__(s):
        s.known = []              # bereits gescannte, noch vorhandene Signale als (MHz, kS); Vergleich mit Toleranz (Frequenz schwankt um +-5 kHz)
        s.new_since = None
        s.retry_after = 0

    @staticmethod
    def has(lst, f, sr):
        return any(k[1] == sr and abs(k[0] - f) < 0.06 for k in lst)

    def add(s, f, sr):
        if not s.has(s.known, f, sr):
            s.known.append((f, sr))

    def loop(s):
        while True:
            time.sleep(2)
            try:
                if not settings.get("auto_scan") or bake["status"] != "ok" or dist.rtl1_busy():
                    s.new_since = None
                    continue
                cur = signals()
                s.known = [k for k in s.known if s.has(cur, *k)]    # verschwundene Signale vergessen -> beim Wiederkommen wieder "neu"
                new = [x for x in cur if not s.has(s.known, *x)]
                if not new:
                    s.new_since = None
                    continue
                if scan.running or zap.clients or zap.hold or zap.pin or time.time() < s.retry_after:
                    continue                                       # Zuschauer/Scan aktiv: nicht stoeren
                if s.new_since is None:
                    s.new_since = time.time()
                    continue
                if time.time() - s.new_since < 6:                  # kurz sammeln, falls mehrere gleichzeitig kommen
                    continue
                scan.start(only=new, auto=True)
                s.new_since = None
            except Exception as e:
                print("auto:", e, flush=True); fb.add("auto:".strip(":"), exc=e)


auto = Auto()


PROT = collections.deque(maxlen=400)          # Protokoll der Verteilung (neue/weggefallene Signale, Zuweisungen, Gruende fuer "ohne Stick")
PROT_FILE = os.path.join(A.dvx_dir, "verteiler_protokoll.log")


def vlog(typ, text):
    """Verteil-Protokoll: eine Zeile im Speicher (Seite /protokoll), in der Datei verteiler_protokoll.log (ab 1 MB -> .1) und auf der Konsole"""
    ln = "%s  %-9s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), typ, text)
    PROT.append(ln)
    print(ln, flush=True)
    try:
        if os.path.exists(PROT_FILE) and os.path.getsize(PROT_FILE) > 1024 * 1024:
            os.replace(PROT_FILE, PROT_FILE + ".1")
        with open(PROT_FILE, "a", encoding="utf-8") as f:
            f.write(ln + "\n")
    except OSError:
        pass


class Verteiler:
    """Autoverteiler: neue Signale werden auf freie Sticks gelegt (Stick bleibt fest auf dem Signal, bis es verschwindet). Ist kein Stick frei,
    erscheint auf der Webseite der Knopf "N neu verteilen": die benutzten Sticks wechseln auf die noch nicht verteilten Signale.
    Je Stick einstellbar (Checkbox), ob er zum Verteilen benutzt wird; ein von Hand eingestellter oder von einem Zuschauer belegter Stick wird nicht angefasst."""

    def __init__(s):
        s.assign = {}             # Stickname -> (MHz, kS), vom Verteiler gesetzt
        s.done = []               # in dieser Runde schon verteilte Signale
        s.retry = {}              # Stickname -> fruehester neuer Versuch
        s.busy = False
        s.bake_stick = None       # Stick, der gerade als Rueckfall auf der Bake steht (kein anderes Signal da)
        s.bake_hold = set()       # Sticks, die beim Start der Bake zugewiesen wurden (bleiben, bis freigegeben) - Display darf die Bake zeigen
        s.known = []              # zuletzt gesehene Signale (fuer das Protokoll: neu / weg)
        s.last_open = None        # letzter Protokolleintrag "ohne Stick" (nur bei Aenderung neu schreiben)
        s.klock = threading.Lock()   # ein Verteilschritt gleichzeitig (Takt, Bake-Ende, Knopf)
        s.skip = {}               # Signal (MHz, kS) -> bis wann nicht automatisch wieder vergeben (der Nutzer hat den Stick dafuer von Hand freigegeben)

    def on(s):
        return bool(settings.get("verteiler"))

    def use(s, q):
        return settings.get("verteilen", {}).get(q.name, True)

    def usable(s, q):
        return s.use(q) and q.bake["status"] == "ok"

    def free(s, q):
        return (s.usable(q) and not q.pin and not q.clients and not q.hold and not (q is zap and scan.running)
                and q.name not in s.assign and time.time() >= s.retry.get(q.name, 0))

    def rtl1_busy(s):
        """RTL1 gehoert dem Verteiler (dann kein Auto-Scan auf RTL1)"""
        return s.on() and s.use(zap)

    @staticmethod
    def placed(x):
        return any(q.cur and Auto.has([q.cur], *x) and (q.pin or q.clients or q.hold) for q in zaps)    # nur feste/belegte Sticks, ein Nachlauf zaehlt nicht

    def unplaced(s):
        return [x for x in signals() if not s.placed(x)]

    def count_open(s):
        """Signale ohne Stick (Zahl auf dem Knopf)"""
        return len(s.unplaced())

    def n_free(s):
        return sum(1 for q in zaps if s.free(q))

    def drop(s, q):
        s.assign.pop(q.name, None)

    def pending(s):
        """Signale ohne Stick, die in dieser Runde noch nicht dran waren"""
        return len([x for x in s.unplaced() if not Auto.has(s.done, *x)])

    def give_back(s, q):
        """Stick des Verteilers freigeben (Verteiler aus / Stick abgewaehlt): sein Signal ist wieder offen"""
        k = s.assign.pop(q.name, None)
        if k:
            s.done = [x for x in s.done if not Auto.has([k], *x)]
        q.release()

    def _put(s, q, x):
        t0 = time.time()
        ok = q.tune(*x)
        q.pin = bool(ok)
        q.last_client = time.time()
        r = (zap.lookup(*x) or {}).get("rufzeichen") if zap else None
        if ok:
            s.assign[q.name] = x
            s.done.append(x)
            vlog("ZUWEISUNG", "%s <- %s%s (eingestellt in %.1f s)" % (q.name, label(*x), (" " + r) if r else "", time.time() - t0))
        else:
            s.retry[q.name] = time.time() + 30
            vlog("FEHLER", "%s <- %s: Stick lieferte keinen Empfang (belegt/Fehler) - neuer Versuch in 30 s" % (q.name, label(*x)))
        return ok

    def why_not(s, q):
        """Grund, warum dieser Stick gerade kein Signal bekommt (fuer das Protokoll)"""
        if q.bake["status"] != "ok":
            return "Bake %s" % q.bake["status"]
        if not s.use(q):
            return "Haken 'zum Verteilen benutzen' aus"
        if q.name in s.assign:
            return "belegt mit %s" % label(*s.assign[q.name])
        if q.pin:
            return "festgehalten (%s)" % (label(*q.cur) if q.cur else "von Hand")
        if q.clients:
            return "%d Zuschauer" % len(q.clients)
        if q.hold or (q is zap and scan.running):
            return "Messung/Scan laeuft"
        if time.time() < s.retry.get(q.name, 0):
            return "Wartezeit nach Fehler"
        return "frei"

    def log_signals(s, cur):
        """Protokoll: neu gefundene und weggefallene Signale"""
        for x in cur:
            if not Auto.has(s.known, *x):
                r = (zap.lookup(*x) or {}).get("rufzeichen")
                vlog("NEU", "Signal %s%s gefunden" % (label(*x), (" " + r) if r else ""))
        for x in s.known:
            if not Auto.has(cur, *x):
                vlog("WEG", "Signal %s verschwunden" % label(*x))
        s.known = list(cur)

    def log_open(s, cur):
        """Protokoll: Signale ohne Stick samt Grund je Stick (nur bei Aenderung)"""
        rest = [x for x in s.unplaced() if not Auto.has(list(s.skip), *x)]
        txt = ("OHNE STICK: %s | %s" % (", ".join(label(*x) for x in rest), "; ".join("%s: %s" % (q.name, s.why_not(q)) for q in zaps))) if rest else ""
        if txt != s.last_open:
            if txt:
                vlog("OFFEN", txt[len("OHNE STICK: "):] if False else txt)
            elif s.last_open:
                vlog("OK", "alle Signale haben einen Stick")
            s.last_open = txt

    def bake_fallback(s, cur):
        """Kein anderes Signal da: RTL1 auf die Bake (und damit der Player auf HDMI1); sobald ein Signal erscheint, den Stick wieder freigeben (der Verteiler vergibt es dann)"""
        try:
            q = zaps[min(max(int(settings.get("bake_rueckfall_stick", 1)), 1), len(zaps)) - 1]
        except (ValueError, TypeError):
            q = zap
        if s.bake_stick and s.bake_stick != q.name:
            s.bake_stick = None
        if s.bake_stick:
            if cur or not settings.get("bake_rueckfall", True) or not (q.pin and q.cur and abs(q.cur[0] - 10491.5) < 0.5):
                still = q.pin and q.cur and abs(q.cur[0] - 10491.5) < 0.5
                s.bake_stick = None
                if still:
                    print("%s  VERTEILER: %s verlaesst die Bake (%s)" % (time.strftime("%H:%M:%S"), q.name, "Signal da" if cur else "Rueckfall aus"), flush=True)
                    q.release()
            return
        if cur or not settings.get("bake_rueckfall", True) or not s.free(q) or q.cur is not None and q.cur[0] > 0 and abs(q.cur[0] - 10491.5) > 0.5:
            return
        vlog("BAKE", "kein anderes Signal da - %s geht auf die Bake (OnlyBake=>RTL1)" % q.name)
        if s._put(q, (10491.5, 1500)):
            s.assign.pop(q.name, None)
            s.done = [x for x in s.done if x != (10491.5, 1500)]
            s.bake_stick = q.name

    @staticmethod
    def _run_jobs(jobs):
        """Sticks gleichzeitig einstellen (jedes Abstimmen dauert einige Sekunden) statt nacheinander"""
        ts = [threading.Thread(target=lambda q=q, x=x: s_put(q, x), daemon=True) for q, x in jobs]
        for t in ts:
            t.start()
        for t in ts:
            t.join(40)

    def step(s):
        cur = signals()
        s.log_signals(cur)
        s.bake_fallback(cur)
        if cur:                                                     # Signale da: jeder Stick, der nur auf der Bake festgehalten wird (Start-Haken, Rueckfall, nach Scan/Messung), wird frei
            for q in zaps:
                if (s.use(q) and q.pin and q.cur and abs(q.cur[0] - 10491.5) < 0.5 and not q.clients and not q.hold and not (q is zap and scan.running)
                        and any(not s.placed(x) for x in cur)):
                    vlog("BAKE", "%s verlaesst die Bake (Signale ohne Stick) - Stick wird frei" % q.name)
                    q.release(); s.assign.pop(q.name, None); s.bake_hold.discard(q.name)
                    if s.bake_stick == q.name:
                        s.bake_stick = None
        s.done = [k for k in s.done if Auto.has(cur, *k)]
        for name, k in list(s.assign.items()):                      # Signal weg oder Stick von Hand/vom Scan umgestellt
            q = next(q for q in zaps if q.name == name)
            if not Auto.has(cur, *k):
                vlog("FREI", "%s frei (Signal weg: %s)" % (name, label(*k)))
                q.release(); s.assign.pop(name, None)
            elif not q.pin or not (q.cur and Auto.has([q.cur], *k)):
                s.assign.pop(name, None)
        now = time.time()
        s.skip = {k: v for k, v in s.skip.items() if v > now and Auto.has(cur, *k)}
        unplaced = [x for x in s.unplaced() if not Auto.has(list(s.skip), *x)]
        # jedes Signal ohne Stick geht auf einen freien Stick: zuerst Signale, die noch nie dran waren, dann solche aus frueheren Runden
        cand = [x for x in unplaced if not Auto.has(s.done, *x)] + [x for x in unplaced if Auto.has(s.done, *x)]
        jobs = []
        for q in zaps:
            if cand and s.free(q):
                jobs.append((q, cand.pop(0)))
        s._run_jobs(jobs)
        s.log_open(cur)

    def neu(s):
        """Knopf "neu verteilen": benutzte Sticks wechseln auf Signale, die noch keinen Stick hatten (danach wieder von vorn)"""
        if s.busy:
            return
        s.busy = True
        vlog("NEU-VERT", "Knopf 'neu verteilen': benutzte Sticks wechseln auf Signale ohne Stick")
        try:
            mine = [q for q in zaps if s.usable(q) and (q.name in s.assign or s.free(q) or (not q.pin and not q.clients and not q.hold and not (q is zap and scan.running)))]
            for q in mine:
                if q.name in s.assign:
                    q.release(); s.assign.pop(q.name, None)
                s.retry.pop(q.name, None)
            todo = list(mine)
            cand = [x for x in s.unplaced() if not Auto.has(s.done, *x)]
            jobs = []
            while todo:
                if not cand:
                    s.done = []                                      # neue Runde
                    cand = [x for x in s.unplaced() if not any(Auto.has([j[1]], *x) for j in jobs)]
                    if not cand:
                        break
                jobs.append((todo.pop(0), cand.pop(0)))
            s._run_jobs(jobs)
        finally:
            s.busy = False

    def kick(s):
        """Verteilschritt sofort (z.B. nach der Bake-Referenzierung), ohne auf den 2-s-Takt zu warten"""
        if not s.klock.acquire(False):
            return
        try:
            s.step()
        finally:
            s.klock.release()

    def loop(s):
        while True:
            time.sleep(2)
            try:
                if s.on() and not s.busy:
                    s.kick()
            except Exception as e:
                print("verteiler:", e, flush=True); fb.add("verteiler:".strip(":"), exc=e)


dist = Verteiler()
s_put = lambda q, x: dist._put(q, x)


class CpuMon:
    """Rechenzeit des Rechners (z.B. Raspberry Pi) und des Programms (dieser Prozess + rtl_sdr/datvrx als Kindprozesse) ueberwachen"""

    def __init__(s):
        s.n = os.cpu_count() or 1
        s.hist = collections.deque(maxlen=10)            # 10 Werte a 2 s = 20 s Mittel
        s.long = {k: collections.deque(maxlen=300) for k in ("sys", "prog", "temp")}     # 300 Werte a 2 s = 10 min fuer Minimum/Maximum in der Grafik
        s.sys = s.prog = s.prog_core = 0.0
        s.temp = s.load = None
        s.warn = ""
        s._p_sys = s._p_prog = None
        s.tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    @staticmethod
    def _sys_times():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return sum(v), v[3] + (v[4] if len(v) > 4 else 0)        # gesamt, idle + iowait

    def _prog_ticks(self):
        """utime+stime (Ticks) dieses Prozesses und aller Nachkommen"""
        ppid, tick = {}, {}
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % d) as f:
                    t = f.read()
                fl = t[t.rfind(")") + 2:].split()
                ppid[int(d)], tick[int(d)] = int(fl[1]), int(fl[11]) + int(fl[12])
            except (OSError, ValueError, IndexError):
                pass
        me, mine, grow = os.getpid(), {os.getpid()}, True
        while grow:
            grow = False
            for pid, pp in ppid.items():
                if pp in mine and pid not in mine:
                    mine.add(pid); grow = True
        return {p: tick[p] for p in mine if p in tick}

    def loop(s):
        while True:
            time.sleep(2)
            try:
                tot, idle = s._sys_times()
                pt, now = s._prog_ticks(), time.time()
                # Zuwachs je Prozess (nur positive Werte): beendete Kindprozesse (rtl_sdr/datvrx) duerfen die Summe nicht senken
                dtk = sum(max(0, v - (s._p_prog[0].get(p, 0) if s._p_prog else 0)) for p, v in pt.items())
                if s._p_sys is not None:
                    dt, di = tot - s._p_sys[0], idle - s._p_sys[1]
                    sys_pct = 100.0 * (1 - di / dt) if dt > 0 else 0.0
                    prog_core = dtk / s.tck / max(now - s._p_prog[1], 0.001) * 100.0          # in % EINES Kerns
                    prog_pct = prog_core / s.n                                                 # in % der Gesamtleistung
                    s.sys, s.prog, s.prog_core = sys_pct, prog_pct, prog_core
                    s.hist.append((sys_pct, prog_pct))
                    s.long["sys"].append(sys_pct)
                    s.long["prog"].append(prog_core)
                s._p_sys, s._p_prog = (tot, idle), (pt, now)
                try:
                    s.temp = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0
                except (OSError, ValueError):
                    s.temp = None
                s.load = os.getloadavg()[0]
                if s.temp is not None:
                    s.long["temp"].append(s.temp)
                if len(s.hist) >= 5:
                    a_sys = sum(x[0] for x in s.hist) / len(s.hist)
                    a_prog = sum(x[1] for x in s.hist) / len(s.hist)
                    w_ = []
                    if a_sys > A.cpu_warn:
                        w_.append("System %.0f %%" % a_sys)
                    if a_prog > A.cpu_warn:
                        w_.append("Programm %.0f %%" % a_prog)
                    if s.temp is not None and s.temp > 80:
                        w_.append("Temperatur %.0f &deg;C" % s.temp)
                    s.warn = ("WARNUNG: Rechenzeit/Temperatur hoch (%s, Grenze %.0f %%) - Dienst belastet den Rechner" % (", ".join(w_), A.cpu_warn)) if w_ else ""
            except Exception:
                pass

    def info(s):
        return {"system_pct": round(s.sys, 1), "programm_pct": round(s.prog, 1), "programm_kern_pct": round(s.prog_core, 1), "kerne": s.n, "last": s.load, "temp_c": s.temp,
                "grenze_pct": A.cpu_warn, "warnung": s.warn}


cpu = CpuMon()


def bake_check(zp=None):
    """Bake mit dem vorgegebenen LO suchen: gelockt -> Ablage messen, Frequenzkorrektur (LO) berechnen; sonst Fehlermeldung (Webseite).
    Gilt je Stick (jeder Stick hat seinen eigenen Fehler). Der Stick wird danach wieder freigegeben (wenn kein Zuschauer verbunden ist)."""
    zp = zp or zap
    bk = zp.bake
    zp.hold = True                       # waehrend der Pruefung gehoert der Stick der Pruefung (Verteiler/Idle fassen ihn nicht an)
    try:
        _bake_check(zp, bk)
    finally:
        zp.hold = False
    if not zp.clients and not zp.hold and not zp.pin:
        zp.release()
    threading.Thread(target=dist.kick, daemon=True).start()          # Stick ist frei: sofort das erste Signal draufsetzen


def bake_check_all(force=False):
    """Knopf 'Bake neu referenzieren': Sticks, deren Referenz juenger als bake_frisch_min (30 min) ist, werden nicht neu gemessen; der Rest parallel (max. 2).
    Eine von Hand auf der Bake festgehaltene Stick (ohne Zuschauer) wird dabei frei. Danach vergibt der Verteiler sofort die Signale."""
    lim = float(settings.get("bake_frisch_min", 30)) * 60
    lo, _pp = lo_cfg()
    ths = []
    for zp in zaps:
        if (scan.running and zp is zap) or zp.clients or zp.hold:
            continue
        if zp.pin:
            if zp.cur and abs(zp.cur[0] - 10491.5) < 0.5:
                vlog("BAKE", "%s von der Bake freigegeben (Bake neu referenzieren)" % zp.name)
                zp.release(); dist.bake_hold.discard(zp.name); dist.assign.pop(zp.name, None)
            else:
                continue
        c = (settings.get("bake_cache") or {}).get(str(zp.index))
        if not force and c and time.time() - c.get("t", 0) < lim and abs(c.get("lo_cfg", lo) - lo) < 0.0005:
            zp.bake.update(status="ok", ablage_khz=c["ablage_khz"], lo_eff=c["lo_eff"], esn0=c.get("esn0"), zeit=time.strftime("%H:%M:%S", time.localtime(c["t"])), gemerkt=True, ungeprueft=False,
                           msg="Bake-Referenz von %s ist juenger als %d min - nicht neu gemessen" % (time.strftime("%H:%M", time.localtime(c["t"])), lim // 60))
            vlog("BAKE", "%s: Referenz von %s juenger als %d min - nicht neu gemessen" % (zp.name, time.strftime("%H:%M", time.localtime(c["t"])), lim // 60))
            continue
        ths.append(threading.Thread(target=lambda z_=zp: (BAKE_SEM.acquire(), bake_check(z_), BAKE_SEM.release()), daemon=True))
    for t in ths:
        t.start()
    if not ths:
        dist.kick()


def _bake_check(zp, bk):
    lo, pp = lo_cfg()
    bk.update(status="suche", msg="Bake (10491.500 MHz) wird gesucht ...", lo_cfg=lo, lo_eff=None, ablage_khz=None, esn0=None, versuch=0)
    ok, stick_ok = False, False
    for k in range(1, 4):
        bk["versuch"] = k
        bk["msg"] = "Bake (10491.500 MHz) wird gesucht ... Versuch %d/3 (LO %.3f MHz)" % (k, lo)
        if not zp.tune(10491.5, 1500, tries=2):
            continue
        stick_ok = True
        end = time.time() + A.bake_wait
        while time.time() < end:
            time.sleep(1)
            i = zp.info
            if i.get("lock") and zp.ts.stats()[0] > 0 and i.get("carrier_hz") is not None and time.time() - (i.get("lock_t") or time.time()) >= 4:
                ok = True
                break
        if ok:
            break
    if ok:
        i = dict(zp.info)
        dev = i["carrier_hz"] / 1000.0 - 300.0              # erwartet: Versatz +300 kHz neben der Stick-Mitte
        settings.setdefault("bake_cache", {})[str(zp.index)] = {"ablage_khz": round(dev, 1), "lo_eff": round(lo - dev / 1000.0, 4), "esn0": i.get("esn0"), "lo_cfg": lo, "t": time.time()}
        save_settings()
        bk.update(status="ok", ablage_khz=round(dev, 1), lo_eff=round(lo - dev / 1000.0, 4), esn0=i.get("esn0"), zeit=time.strftime("%H:%M:%S"), gemerkt=False, ungeprueft=False,
                  msg="Bake gelockt (Es/N0 %.1f dB). Ablage %+.1f kHz -> LO-Korrektur: %.3f MHz statt %.3f MHz" % (i.get("esn0") or 0, dev, lo - dev / 1000.0, lo))
    else:
        bk.update(status="fehler", zeit=time.strftime("%H:%M:%S"),
                  msg=("FEHLER: Bake (10491.500 MHz) nicht gelockt - LNB, LO (%.3f MHz), Kabel, Antenne oder Stick-Verstaerkung pruefen" % lo) if stick_ok
                  else "FEHLER: RTL-Stick/Empfang startet nicht - ist ein RTL-Stick am USB angeschlossen (und datvrx vorhanden)?")
    say_bake(zp)


def say_bake(zp=None):
    zp = zp or zap
    print("%s  %s BAKE %s: %s" % (time.strftime("%H:%M:%S"), zp.name, zp.bake["status"].upper(), zp.bake["msg"]), flush=True)


BAKE_SEM = threading.Semaphore(2)          # hoechstens zwei Bake-Pruefungen gleichzeitig (Rechenzeit bei vielen Sticks)


def bake_loop(zp=None):
    """beim Start pruefen; bei Fehler alle 90 s erneut (Stick/LNB koennen nachtraeglich angeschlossen werden)"""
    zp = zp or zap
    if zp is not zap:
        time.sleep(3)
    if bake_startup_skip(zp):                      # gemerkter Offset oder Signale vorhanden: nicht auf der Bake sitzen, sondern gleich Signale
        return
    while True:
        try:
            if not (scan.running and zp is zap) and not zp.clients and not zp.pin:
                with BAKE_SEM:
                    bake_check(zp)
        except Exception as e:
            zp.bake.update(status="fehler", msg="FEHLER bei der Bake-Pruefung: %r" % (e,))
        if zp.bake["status"] == "ok":
            return
        for _ in range(90):
            time.sleep(1)
            if zp.bake["status"] == "ok":
                return


def bake_startup_skip(zp):
    """Start: Bake NICHT decodieren, wenn (a) der Offset dieses Sticks von frueher gemerkt ist (settings bake_cache, bis 7 Tage alt) oder (b) schon Signale da sind -
    dann bekommt der Stick gleich ein Signal vom Verteiler (LO wie gemerkt bzw. wie konfiguriert, der Offset wird spaeter im Leerlauf nachgemessen). True = uebersprungen"""
    if settings.get("bake_immer_start", False):        # Test: immer messen, auch mit gemerktem Offset oder vorhandenen Signalen (Seite Zahnrad B)
        return False
    c = (settings.get("bake_cache") or {}).get(str(zp.index)) if settings.get("bake_cache_nutzen", True) else None
    lo, _pp = lo_cfg()
    if c and time.time() - c.get("t", 0) < float(settings.get("bake_cache_tage", 7)) * 86400 and abs(c.get("lo_cfg", lo) - lo) < 0.0005:
        zp.bake.update(status="ok", ablage_khz=c["ablage_khz"], lo_eff=c["lo_eff"], esn0=c.get("esn0"), zeit=time.strftime("%H:%M:%S", time.localtime(c["t"])), gemerkt=True, ungeprueft=False,
                       msg="Bake-Offset gemerkt (%s): Ablage %+.1f kHz -> LO %.3f MHz - nicht neu gemessen" % (time.strftime("%d.%m. %H:%M", time.localtime(c["t"])), c["ablage_khz"], c["lo_eff"]))
        say_bake(zp)
        return True
    if signals():
        zp.bake.update(status="ok", lo_eff=None, ablage_khz=None, zeit=time.strftime("%H:%M:%S"), gemerkt=False, ungeprueft=True,
                       msg="Bake nicht gemessen: Signale vorhanden, Stick geht gleich auf ein Signal (LO wie konfiguriert; Offset wird im Leerlauf nachgemessen)")
        say_bake(zp)
        return True
    return False


def bake_start_assign():
    """Haken 'RTL1 beim Start auf die Bake': RTL1 wird der Bake zugewiesen und bleibt dort (festgehalten, der Verteiler fasst ihn nicht an), bis er freigegeben wird"""
    try:
        with zap.lock:
            if zap.pin or zap.clients or zap.hold:
                return
        if zap.tune(10491.5, 1500):
            zap.pin = True
            zap.last_client = time.time()
            dist.drop(zap)
            dist.bake_hold.add(zap.name)
            print("%s  RTL1 beim Start auf die Bake gestellt (festgehalten bis zur Freigabe)" % time.strftime("%H:%M:%S"), flush=True)
    except Exception as e:
        print("bake_start_assign:", e, flush=True); fb.add("bake_start_assign:".strip(":"), exc=e)


def bake_idle_loop():
    """Sticks ohne gemessenen Offset (Start mit Signalen): sobald keine Signale da sind und der Stick frei ist, die Bake nachmessen"""
    while True:
        time.sleep(30)
        try:
            if signals():
                continue
            for zp in zaps:
                if zp.bake.get("ungeprueft") and zp.cur is None and not zp.pin and not zp.clients and not zp.hold and not (scan.running and zp is zap):
                    with BAKE_SEM:
                        bake_check(zp)
        except Exception as e:
            print("bake_idle:", e, flush=True); fb.add("bake_idle:".strip(":"), exc=e)


def bake_loops():
    """eine Bake-Pruefung je Stick, parallel (nach kurzer Wartezeit, bis die BATC-Signalliste da ist)"""
    time.sleep(8)
    threading.Thread(target=bake_idle_loop, daemon=True).start()
    for zp in zaps[1:]:
        threading.Thread(target=bake_loop, args=(zp,), daemon=True).start()
    bake_loop(zap)
    if settings.get("bake_start_rtl1", False):
        bake_start_assign()


def fmt(v, nd=1):
    return "&ndash;" if v is None else ("%.*f" % (nd, v))


UI_CSS = """
:root{--bg:#f4f6f8;--card:#fff;--fg:#1b1f24;--mut:#5b6573;--bd:#d3d9e0;--acc:#1f7a3a;--accbg:#e4f3e8;--warn:#e08a00;--warnbg:#fff3d6;--err:#c0392b;--link:#1a5fb4}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--card:#1d2126;--fg:#e8ebef;--mut:#9aa5b1;--bd:#363d45;--acc:#3ecf6a;--accbg:#173323;--warn:#f0a020;--warnbg:#3a2e12;--err:#ff6b5b;--link:#7db3ff}}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg);max-width:82em;margin:0 auto;padding:.7em .9em 2em;line-height:1.35}
h2{margin:.2em 0 .5em;font-size:1.35em}h3{margin:1em 0 .4em;font-size:1.05em}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
button,a.pill{display:inline-block;font:inherit;font-size:.92em;padding:.42em 1.05em;margin:.15em .15em .15em 0;border:1px solid var(--bd);border-radius:999px;background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none;transition:background .12s,border-color .12s}
button:hover,a.pill:hover{border-color:var(--acc);background:var(--accbg);text-decoration:none}
button:active,a.pill:active{transform:translateY(1px)}
button:disabled{opacity:.45;cursor:default}
label.chk{display:inline-block;font-size:.92em;padding:.42em 1.05em;margin:.15em .15em .15em 0;border:1px solid var(--bd);border-radius:999px;background:var(--card);cursor:pointer}
label.chk input{margin-right:.5em;vertical-align:-.1em;accent-color:var(--acc)}label.chk.on{background:var(--accbg);border-color:var(--acc)}label.chk:hover{border-color:var(--acc)}
button.on,a.pill.on{background:var(--acc);border-color:var(--acc);color:#fff}
button.warn{background:var(--warnbg);border-color:var(--warn);color:var(--fg)}
button.go{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.top{display:flex;align-items:center;justify-content:space-between;gap:.6em;flex-wrap:wrap}
.top h2{margin:.1em 0}.tools a.pill{font-size:1.05em;padding:.3em .8em}
.bar{display:flex;flex-wrap:wrap;gap:.1em .5em;align-items:center;margin:.3em 0}
.bar form{display:inline}
.banner{padding:.5em .9em;margin:.35em 0;border-radius:12px;font-size:.92em;border:1px solid var(--bd);background:var(--card)}
.b-err{background:var(--err);border-color:var(--err);color:#fff}.b-warn{background:var(--warnbg);border-color:var(--warn)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:.6em .9em;margin:.5em 0}
table{border-collapse:separate;border-spacing:0;background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;width:100%}
th{background:var(--bg);font-weight:600;font-size:.82em;color:var(--mut);text-align:left;padding:.5em .6em;border-bottom:1px solid var(--bd);white-space:nowrap}
td{padding:.42em .6em;border-bottom:1px solid var(--bd);font-size:.92em;vertical-align:middle}
tr:last-child td{border-bottom:0}tr.live td{background:var(--accbg)}tr:hover td{background:rgba(127,127,127,.07)}tr.live:hover td{background:var(--accbg)}
td:first-child{white-space:nowrap}td a.pill{padding:.18em .7em;font-size:.85em;margin:.05em .15em .05em 0}td a.pill.on{font-weight:600}
small,.mut{color:var(--mut)}
input[type=text],input:not([type]),select{font:inherit;padding:.35em .6em;border:1px solid var(--bd);border-radius:10px;background:var(--card);color:var(--fg)}
pre{background:var(--card);border:1px solid var(--bd);border-radius:12px}
label{margin-right:.6em}
.cpu{display:grid;grid-template-columns:auto minmax(8em,1fr) auto;gap:.4em .8em;align-items:center}
.mrow{display:contents}.mrow>span{color:var(--mut);font-size:.88em;white-space:nowrap}
.meter{position:relative;height:1.2em;border-radius:99px;background:var(--bd);overflow:hidden}
.meter .fill{height:100%;border-radius:99px;transition:width .5s;opacity:.9}
.mk{position:absolute;top:0;bottom:0;width:4px;margin-left:-2px;border-radius:2px;display:block}.mk.mn{background:#1e90ff}.mk.mx{background:#ff2d55}
.val{font-size:.88em;white-space:nowrap}.rg{color:var(--mut);font-weight:normal;font-size:.9em}.mnc{color:#1e90ff}.mxc{color:#ff2d55}
.chips{display:flex;flex-wrap:wrap;gap:.4em;margin-top:.1em}.chip{padding:.12em .7em;border-radius:99px;font-size:.82em;background:var(--bg);border:1px solid var(--bd);color:var(--mut)}
.chip.hot{background:var(--warnbg);border-color:var(--warn);color:var(--fg)}.chip.crit{background:var(--err);border-color:var(--err);color:#fff}
.rx{display:grid;grid-template-columns:auto 1fr;gap:.5em .9em;align-items:center}
a.pill.vlc{color:#fff;font-weight:600;border-color:transparent}a.pill.vlc.s1{background:#f08000}a.pill.vlc.s2{background:#2b7de9}a.pill.vlc.s3{background:#8e44ad}a.pill.vlc.s4{background:#16a085}a.pill.vlc.s5{background:#c0392b}
a.pill.vlc:hover{filter:brightness(1.1);border-color:transparent}
a.pill.http{color:var(--fg);border-width:2px}a.pill.http.s1{border-color:#f08000}a.pill.http.s2{border-color:#2b7de9}a.pill.http.s3{border-color:#8e44ad}a.pill.http.s4{border-color:#16a085}a.pill.http.s5{border-color:#c0392b}
.sig{font-size:.9em}.sig .live{color:var(--acc);font-weight:600}
details.info{display:inline-block;vertical-align:middle;margin:.1em .3em}
details.info>summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;width:1.65em;height:1.65em;border-radius:50%;border:1px solid var(--bd);background:var(--card);color:var(--link);font-weight:700;font-family:Georgia,serif;font-style:italic;font-size:.95em;user-select:none}
details.info>summary::-webkit-details-marker{display:none}
details.info>summary:hover{border-color:var(--link)}details.info[open]>summary{background:var(--link);border-color:var(--link);color:#fff}
.bar details.info[open]{flex:1 1 100%}
.infobox{margin:.5em 0 .2em;padding:.7em .95em;border:1px solid var(--bd);border-left:4px solid var(--link);border-radius:10px;background:var(--card);color:var(--fg);font-size:.9em;line-height:1.5;max-width:60em}
code{background:var(--bg);padding:.08em .35em;border-radius:6px;font-size:.92em}
.help h3{margin-top:1.4em}.help ul{padding-left:1.3em}.help li{margin:.25em 0}
.toc{display:flex;flex-wrap:wrap;gap:.3em .4em;margin:.5em 0 1em}
@media(max-width:700px){body{padding:.5em}td,th{padding:.35em .4em}}
"""

def info(text):
    """Info-Knopf: der erklaerende Text bleibt verborgen, bis man auf das i tippt"""
    return '<details class="info"><summary title="Erkl&auml;rung ein-/ausblenden">i</summary><div class="infobox">%s</div></details>' % text


HEAD = ["Stick", "MER dB", "Signal", "Rufzeichen / Dienst", "Provider", "Modulation", "Es/N0 dB", "Pegel dB&uuml;ber Rauschen", "Pegel dBFS", "Mbit/s", "TEI / CC", "Stand"]


_CTX = {"host": None, "android": False}     # Anfrage der Hauptseite (fuer die Signal-Links)


def sig_row(f, sr):
    lz = live_zap(f, sr)
    live = lz is not None
    r = lz.snapshot() if live else zap.lookup(f, sr)
    if abs(f - 10491.5) < 0.5:                                       # Bake: einzelner Stream
        href = "/z/%.3f/%d" % (f, sr)
    elif _CTX["android"]:                                            # Handy: VLC per Intent mit Playlist, angetipptes Signal zuerst
        href = vlc_playlist_link(_CTX["host"], 1, f, sr)
    else:                                                            # PC: Playlist (RTL1) mit diesem Signal zuerst; der Browser oeffnet sie mit VLC
        href = "/qo100.m3u?first=%.3f/%d" % (f, sr)
    link = '<a href="%s">%s</a>' % (href, label(f, sr))
    tune = " ".join('<a class="pill%s" href="/tune/%d/%.3f/%d">%s</a>' % (" on" if q.cur == (f, sr) else "", n, f, sr, q.name) for n, q in enumerate(zaps, 1))
    mer_c = "<td><b>%s</b></td>" % fmt((r or {}).get("mer"))
    head = '<td>%s</td>%s<td>%s</td>' % (tune, mer_c, link)
    if r is None:
        return '<tr>%s%s</tr>' % (head, '<td colspan="9"><em>%s</em></td>' % ("wird nicht gescannt (nur live beim Anh&ouml;ren)" if abs(f - 10491.5) < 0.5 else "noch nicht gemessen"))
    mod = html.escape(("%s %s %s" % (r.get("mod") or "", r.get("fec") or "", r.get("extra") or "")).strip()) or "&ndash;"
    state = ("&#9679; %s live (%s)" % (lz.name, "gelockt" if r.get("lock") else "suche")) if live else html.escape(str(r.get("stand", "")))
    cells = ["<b>%s</b>" % html.escape(r.get("rufzeichen") or "–"), html.escape(r.get("provider") or "–"), mod,
             fmt(r.get("esn0")), fmt(r.get("pegel_db")), fmt(r.get("pegel_dbfs")), fmt(r.get("bitrate"), 2),
             "%s / %s" % (r.get("tei", 0), r.get("cc", 0)), state]
    return '<tr%s>%s%s</tr>' % (' class="live"' if live else "", head, "".join("<td>%s</td>" % c for c in cells))



HELP_SECTIONS = (
 ("uebersicht", "&Uuml;bersicht", """<p>Der <b>QO 100 Multi-Zapper</b> l&auml;uft auf dem Raspberry Pi und h&auml;ngt mehrere RTL-SDR-Sticks an den QO-100-Downlink (10490&ndash;10500 MHz). Er scannt die Signale, dekodiert sie mit <code>datvrx</code> (Rufzeichen, Modulation/FEC, Es/N0, MER, Pegel, Bitrate) und stellt sie bereit: Webseite, Handy-Seite, VLC-Playlisten, UDP-Multicast je Stick (230.0.0.30, .31, &hellip;), HDMI-Vollbild am Pi, JSON-API und MQTT.</p>
<ul><li><b>RTL1, RTL2, &hellip;</b> sind die Sticks. Jeder hat eine eigene Bake-Messung (Offset) und eine eigene Multicast-Adresse.</li>
<li>Die Signale kommen vom <b>BATC-Spektrum</b> (Server eshail.batc.org.uk). Die Bake (10491,5 MHz) steht in der Tabelle, wird aber nicht gescannt.</li></ul>"""),
 ("tabelle", "Signaltabelle und Knöpfe", """<ul><li><b>RTL1 / RTL2 / &hellip;</b> in der ersten Spalte: stellt den Stick auf dieses Signal und h&auml;lt ihn fest, bis du ihn &bdquo;freigibst&ldquo; (Zeile &bdquo;RTL1: &hellip; freigeben&ldquo; oben). Der Autoverteiler fasst festgehaltene Sticks nicht an.</li>
<li><b>Signalname</b> (Spalte 3): &ouml;ffnet VLC mit der Playlist aller Signale, das angeklickte zuerst (Handy: &uuml;ber den VLC-Intent, PC: Browser &ouml;ffnet die <code>.m3u</code>; bei der Bake nur der einzelne Stream).</li>
<li><b>Alle jetzt scannen</b>: jedes Signal nacheinander auf RTL1 einstellen und messen. <b>Auto-Scan</b>: scannt nur, wenn ein neues Signal erscheint.</li>
<li><b>Autoverteiler</b>: neue Signale kommen automatisch auf freie Sticks (Haken je Stick). Ist kein Stick frei, erscheint &bdquo;<i>N</i> neu verteilen&ldquo; (die benutzten Sticks wechseln auf Signale, die noch keinen Stick hatten).</li>
<li><b>OnlyBake=&gt;RTL1</b>: ist kein anderes Signal da, geht RTL1 auf die Bake (HDMI1 zeigt sie); sobald ein Signal erscheint, wird RTL1 freigegeben.</li>
<li><b>Verteil-Protokoll</b> (<a href="/protokoll">&#128221; Protokoll</a>): jede Entscheidung des Verteilers wird mit Zeit festgehalten &ndash; Signal <b>NEU</b>/<b>WEG</b>, <b>ZUWEISUNG</b> (Stick &larr; Signal mit Rufzeichen und Dauer), <b>FREI</b>, <b>OFFEN</b> (Signal ohne Stick mit Grund je Stick) und Handbedienung. So l&auml;sst sich nachvollziehen, warum ein Signal keinen Stick bekam. Datei <code>verteiler_protokoll.log</code>.</li>
<li><b>CPU-Balken</b>: Rechner, Dienst (inkl. <code>rtl_sdr</code>/<code>datvrx</code>) und Temperatur; blauer Strich = Minimum, roter Strich = Maximum der letzten 10 Minuten. Orange Warnung bei hoher Last.</li></ul>"""),
 ("bake", "Bake und Offset (&#9881;B)", """<ul><li>Jeder Stick hat einen Frequenzfehler. Die Bake dient als Referenz: aus ihrer Ablage berechnet der Dienst die LO-Korrektur je Stick.</li>
<li>Der Offset wird <b>gemerkt</b> (Standard 7 Tage) und beim Start nicht neu gemessen. Sind beim Start schon Signale da, geht der Stick gleich auf ein Signal; die Messung folgt im Leerlauf.</li>
<li><b>Bake neu referenzieren</b> misst alle freien Sticks neu. <b>RTL1 beim Start auf die Bake</b> h&auml;lt RTL1 nach dem Start auf der Bake (Test). Auf der Seite <a href="/bake">&#9881;B</a> stehen alle Schalter und die Offset-Tabelle (&bdquo;immer Bake beim Start messen&ldquo;, Cache an/aus, Tage, Einzelmessung, Offsets l&ouml;schen).</li>
<li>Bake nicht gelockt: LNB, LO, Kabel, Antenne und Verst&auml;rkung pr&uuml;fen. <b>Eine aktive 5-m-USB-Verl&auml;ngerung ohne Zusatzspannung geht nicht</b> (Signal sichtbar, aber kein Lock).</li></ul>"""),
 ("displays", "Displays am Pi (&#9881;D)", """<ul><li>Jedem HDMI-Ausgang l&auml;sst sich ein Stick zuordnen (Standard HDMI-A-1 &rarr; RTL1 an, HDMI-A-2 &rarr; RTL2 aus). Ist ein Monitor angeschlossen und der Stick gelockt, l&auml;uft dort ein Vollbild-Player mit dem Stream.</li>
<li>Player w&auml;hlbar (cvlc, vlc, mpv, ffplay; nicht installierte sind gesperrt), <b>Player starten/stoppen</b> von Hand (auch ohne Signal), <b>Bake: an/aus</b> je Ausgang. Beim Aufruf pr&uuml;ft die Seite die installierten Player und die angeschlossenen Monitore neu.</li>
<li><b>Text im Bild</b>: Felder, Reihenfolge, Trenner, Position, Gr&ouml;&szlig;e, Deckkraft und Farbe der Einblendung einstellbar; das <b>Display-Log</b> nennt den Grund, falls der Player nicht erscheint (kein Strom, Monitor weg, Player beendet).</li>
<li>VLC zeigt sein Fenster erst, wenn Bild ankommt &ndash; ohne Signal bleibt der Monitor auf dem Desktop.</li></ul>"""),
 ("spektrum", "BATC-Spektrum", """<p>Unten auf der Hauptseite. <b>Spektrum: AN/AUS</b> blendet es ein. Standard ist das <b>stehende Bild</b>: es wird nur aktualisiert, wenn ein neues Signal erscheint (oder eins verschwindet). <b>Echtzeit</b> (gr&uuml;n) aktualisiert ca. zweimal pro Sekunde. Blau = Bake, gelb = erkannte Signale mit Rufzeichen und Datenrate.</p>"""),
 ("handy", "Handy-Seite und Playlisten", """<ul><li><b>Handy-Seite</b> (Port 8555): gro&szlig;e Zeilen je Signal; ein Tipp &ouml;ffnet VLC (Android) mit der Playlist, das angetippte Signal zuerst, &bdquo;n&auml;chstes&ldquo; schaltet weiter. Oben den Stick w&auml;hlen.</li>
<li><b>Playlisten</b>: <code>/qo100.m3u</code> (RTL1), <code>/qo100_rtl2.m3u</code>, &hellip; mit Rufzeichen, Frequenz, Datenrate und MER im Titel. <b>UDP</b>: <code>udp://@230.0.0.30:1234</code> (RTL1) &ndash; im Heimnetz per VLC; im WLAN geht Multicast oft nicht, dann die HTTP-Streams nehmen.</li></ul>"""),
 ("manuell", "Manuelle Frequenz", """<p>Seite <a href="/manuell">/manuell</a>: Stick, Frequenz (Schnellwahl 2 m, 70 cm, 23 cm; unter dem LO = Direktempfang bis ca. 1766 MHz) und Symbolrate oder <b>auto</b> (datvrx sucht selbst bis etwa 1000 kS). FEC und Modulation erkennt datvrx immer selbst. Der Stick bleibt fest, bis du ihn freigibst.</p>"""),
 ("einstellungen", "Einstellungen (&#9881;)", """<p>Seite <a href="/config">/config</a>: LO, ppm, Verst&auml;rkung, Multicast, Ports, Kopfzeile, Wartezeiten, MQTT, CPU-Grenze und die <b>Stick-Zuweisung</b> (automatisch suchen oder feste Rollen RTL1&hellip;RTL10 nach Seriennummer). &bdquo;Speichern und Dienst neu starten&ldquo; legt vorher eine Sicherung <code>dienst.conf.vor_web_&hellip;</code> an. Kein Passwortschutz &ndash; nur im Heimnetz.</p>"""),
 ("api", "API und Programme", """<ul><li><code>/api/v1/sticks</code>, <code>/api/v1/stick/&lt;Nr&gt;</code>: feste JSON-Schnittstelle (Signal, Lock, Es/N0, MER, Rufzeichen, Bake, Display-Zuweisung). <code>/api/v1/displays</code>, <code>/status</code>, <code>/spectrum.json</code>.</li>
<li><b>Steuern per JSON:</b> <code>POST /api/v1/verteiler</code> mit <code>{"an": false}</code> schaltet die <b>Verteilfunktion</b> aus (die leandvb-GUI macht das beim Verbinden selbst), <code>POST /api/v1/einstellungen</code> setzt mehrere Schalter (Verteiler, Auto-Scan, Bake-R&uuml;ckfall &hellip;). Alle Befehle: <a href="/anleitung#befehle">Anleitung, Abschnitt Befehle</a>.</li>
<li><b>MultiPlayer</b> (<code>multiplayer.py</code> am PC): ein Player je aktivem Stick, Quad/Fensterlagen, Text im Player; Sticks, die an einem Pi-Monitor h&auml;ngen, erscheinen grau (Rechtsklick: trotzdem darstellen).</li>
<li><b>leandvb-GUI</b>: Men&uuml; datvRX &rarr; RTL-Dienst verbinden macht aus jedem Stick eine datvRX-Spalte (voller Tuner).</li></ul>"""),
 ("fehlerbericht", "Fehlerbericht", """<p>Tritt im Dienst ein Fehler auf, erscheint oben auf der Hauptseite eine rote Meldung. Die Seite <a href="/fehler">&#9888; Fehlerbericht</a> zeigt den vollst&auml;ndigen Bericht (Fehler mit Traceback, System, Sticks, Bake, Displays, letzte Meldungen, Konfiguration ohne Passw&ouml;rter). <b>In Zwischenablage kopieren</b> legt ihn ab, danach l&auml;sst er sich z.&nbsp;B. in eine Mail einf&uuml;gen; au&szlig;erdem gibt es den Download als Datei und einen E-Mail-Entwurf. Die Seite ist auch ohne Fehler nutzbar (Systembericht).</p>"""),
 ("fehler", "Fehlersuche", """<ul><li><b>Player erscheint nicht am Monitor</b>: Display-Log auf &#9881;D lesen. H&auml;ufigste Ursache: der Stick hat kein Signal (VLC &ouml;ffnet das Fenster erst mit Bild) oder keine angemeldete Desktop-Sitzung am Pi.</li>
<li><b>Bake nicht gelockt</b>: LNB/Kabel/LO pr&uuml;fen, Stick direkt (ohne Verl&auml;ngerung) anstecken, &bdquo;Bake neu referenzieren&ldquo;.</li>
<li><b>Handy: VLC &ouml;ffnet nicht</b>: Link &uuml;ber die Handy-Seite (Port 8555) nehmen; im Browser ggf. &bdquo;&Ouml;ffnen mit VLC&ldquo; w&auml;hlen.</li>
<li><b>CPU-Warnung</b>: je Stick mit Signal belegt datvrx etwa einen Kern; auf dem Pi 5 sind ca. vier Sticks gleichzeitig m&ouml;glich.</li></ul>"""),
)


def help_page(host=None):
    host = host or _LAST_HOST[0]
    toc = "".join('<a class="pill" href="#%s">%s</a>' % (k, t) for k, t, _b in HELP_SECTIONS)
    body = "".join('<h3 id="%s">%s</h3>%s' % (k, t, b) for k, t, b in HELP_SECTIONS)
    man = ('<p><a class="pill go" href="/anleitung">Ausf&uuml;hrliche Anleitung (Installation, Konfiguration, Messergebnisse)</a></p>' if find_manual() else "")
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hilfe &ndash; %s</title><style></style>'
            '<body class="help"><h2>? Hilfe &ndash; QO 100 Multi-Zapper</h2><p>%s</p><div class="toc">%s</div>%s%s</body>' % (html.escape(A.title), other_links(host, "hilfe"), toc, body, man))


START_T = time.time()


def error_report():
    """kompletter Fehlerbericht des Dienstes (Text): Fehler mit Traceback, System, Sticks, Bake, Displays, letzte Meldungen, Konfiguration ohne Passwoerter"""
    st = []
    for n, q in enumerate(zaps, 1):
        m = q.snapshot() if q.cur else {}
        st.append("%s (%s): %s lock=%s Bake=%s%s" % (q.name, q.index, label(*q.cur) if q.cur else "frei", bool(m.get("lock")), q.bake["status"],
                  (" Ablage %+.1f kHz" % q.bake["ablage_khz"]) if q.bake.get("ablage_khz") is not None else ""))
    extra = {"Laufzeit": "%d s seit Start des Dienstes" % (time.time() - START_T), "Sticks": " | ".join(st),
             "Signale": ", ".join("%.3f/%d" % x for x in signals()) or "-", "CPU": "gesamt %.0f %%, Dienst %.0f %% eines Kerns, %s C" % (cpu.sys, cpu.prog_core, ("%.0f" % cpu.temp) if cpu.temp is not None else "?"),
             "Displays": "; ".join("%s: %s" % (c["name"], displays.msg.get(c["name"], "")) for c in disp_connectors())}
    cfg = {}
    if CONF_PATH:
        try:
            cfg.update(conf_read())
        except Exception:
            pass
    for k, v in settings.items():
        if k not in ("bake_cache",):
            cfg["settings." + k] = json.dumps(v, ensure_ascii=False)[:300]
    me = os.path.abspath(__file__)
    ver = fb.version_of(me, os.path.join(os.path.dirname(me), "RTL-SDR-datvRX-Dienst", "datvrx_dienst.py"), os.path.join(os.path.dirname(me), "datv_core.py"))
    return fb.report("QO 100 Multi-Zapper", version=ver, log_tail=["[Protokoll] " + x for x in list(PROT)[-40:]] + list(LOGBUF)[-60:] + ["[Display-Log] " + x for x in list(displays.log)[-20:]], config=cfg, extra=extra)


def fehler_page(msg="", host=None):
    host = host or _LAST_HOST[0]
    rep = error_report()
    n = len(fb.ERRORS)
    sub = "Fehlerbericht QO 100 Multi-Zapper"
    mail = fb.mailto(sub, "Fehlerbericht QO 100 Multi-Zapper (%s)\n\nDer vollstaendige Bericht liegt in der Zwischenablage - bitte hier einfuegen (Strg+V / Einfuegen).\n\n" % time.strftime("%Y-%m-%d %H:%M"))
    js = """<script>function cp(){var t=document.getElementById('rep');t.focus();t.select();t.setSelectionRange(0,t.value.length);var ok=false;try{ok=document.execCommand('copy')}catch(e){}
if(!ok&&navigator.clipboard){navigator.clipboard.writeText(t.value);ok=true}
document.getElementById('cpst').textContent=ok?'In die Zwischenablage kopiert - jetzt z.B. in eine Mail einfuegen (Strg+V).':'Kopieren ging nicht - der Text ist markiert, bitte Strg+C druecken.';}</script>"""
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fehlerbericht &ndash; %s</title><style></style>'
            '<body><h2>&#9888; Fehlerbericht &ndash; %s</h2><p>%s</p>%s'
            '<div class="card"><p>%s</p><div class="bar"><button class="go" onclick="cp()">In Zwischenablage kopieren</button>'
            '<a class="pill" href="/fehler.txt">Als Datei herunterladen</a><a class="pill" href="%s">E-Mail-Entwurf &ouml;ffnen</a>'
            '<form method="post" action="/fehler/loeschen"><button>Fehler l&ouml;schen</button></form><form method="post" action="/fehler/test"><button title="erzeugt einen Test-Eintrag, um den Weg auszuprobieren">Testfehler</button></form>'
            '<small class="mut" id="cpst"></small></div>'
            '<textarea id="rep" readonly rows="30" style="width:100%%;font:12px/1.35 monospace;margin-top:.5em;border:1px solid var(--bd);border-radius:10px;padding:.6em;background:var(--card);color:var(--fg)">%s</textarea></div>%s</body>'
            % (html.escape(A.title), html.escape(A.title), other_links(host, "fehler"), ('<p style="color:var(--err)"><b>%s</b></p>' % html.escape(msg)) if msg else "",
               ("%d Fehler gemerkt." % n) if n else "Es ist kein Fehler gemerkt &ndash; der Bericht enth&auml;lt trotzdem Systemdaten, Sticks, letzte Meldungen und Konfiguration (ohne Passw&ouml;rter) und l&auml;sst sich f&uuml;r eine Fehlermeldung nutzen.",
               html.escape(mail, quote=True), html.escape(rep), js))


def find_manual():
    for d in (os.path.dirname(CONF_PATH) if CONF_PATH else None, os.path.join(A.dvx_dir, "RTL-SDR-datvRX-Dienst"), A.dvx_dir):
        if d and os.path.isfile(os.path.join(d, "ANLEITUNG.html")):
            return os.path.join(d, "ANLEITUNG.html")
    return None


SPEC_JS = r"""
(function(){
var cv=document.getElementById('spc'); if(!cv) return; var cx=cv.getContext('2d'), cap=document.getElementById('spcap');
function draw(D){
  var W=cv.width,H=cv.height, L=44,R=10,T=18,B=24; cx.clearRect(0,0,W,H); cx.fillStyle='#0d1117'; cx.fillRect(0,0,W,H);
  if(!D||!D.db||!D.db.length){cx.fillStyle='#8b949e';cx.font='14px sans-serif';cx.fillText('warte auf BATC-Spektrum ...',L+10,H/2);return;}
  var db=D.db,n=db.length, s=db.slice().sort(function(a,b){return a-b}), lo=s[Math.floor(n*0.05)]-2, hi=s[n-1]+3; if(hi-lo<15){hi=lo+15}
  var pw=W-L-R, ph=H-T-B; function X(i){return L+pw*i/(n-1)} function Y(v){return T+ph*(1-(Math.min(Math.max(v,lo),hi)-lo)/(hi-lo))}
  cx.font='11px sans-serif'; cx.strokeStyle='#1f2630'; cx.fillStyle='#8b949e'; cx.lineWidth=1;
  for(var m=Math.ceil(D.start); m<=D.start+D.bw; m++){var x=L+pw*(m-D.start)/D.bw; cx.beginPath();cx.moveTo(x,T);cx.lineTo(x,T+ph);cx.stroke(); cx.fillText(String(m),x-14,H-8);}
  for(var k=0;k<=4;k++){var yv=lo+(hi-lo)*k/4,y=Y(yv); cx.beginPath();cx.moveTo(L,y);cx.lineTo(L+pw,y);cx.stroke(); cx.fillText(yv.toFixed(0)+' dB',2,y+4);}
  var g=cx.createLinearGradient(0,T,0,T+ph); g.addColorStop(0,'#f1c40f'); g.addColorStop(0.5,'#2ecc71'); g.addColorStop(1,'#14532d');
  cx.beginPath(); cx.moveTo(X(0),T+ph); for(var i=0;i<n;i++){cx.lineTo(X(i),Y(db[i]))} cx.lineTo(X(n-1),T+ph); cx.closePath(); cx.fillStyle=g; cx.globalAlpha=.85; cx.fill(); cx.globalAlpha=1;
  cx.beginPath(); for(var i=0;i<n;i++){var y=Y(db[i]); if(i==0)cx.moveTo(X(i),y); else cx.lineTo(X(i),y)} cx.strokeStyle='#d7f7df'; cx.lineWidth=1; cx.stroke();
  var lastEnd=-1e9;
  (D.sig||[]).slice().sort(function(a,b){return a.f-b.f}).forEach(function(sg){var x=L+pw*(sg.f-D.start)/D.bw; if(x<L||x>L+pw)return; cx.strokeStyle=sg.bake?'#58a6ff':'#ffd33d'; cx.setLineDash([3,3]); cx.beginPath();cx.moveTo(x,T);cx.lineTo(x,T+ph);cx.stroke();cx.setLineDash([]);
    cx.fillStyle=sg.bake?'#58a6ff':'#ffd33d'; cx.font='bold 11px sans-serif'; var t1=(sg.bake?'Bake':(sg.call||sg.f.toFixed(2))), t2=sg.sr+'k', w1=Math.max(cx.measureText(t1).width,cx.measureText(t2).width), tx0=Math.min(x+3,L+pw-w1-2);
    if(tx0<lastEnd+4){tx0=Math.max(L+2,x-3-w1);}      /* Name und Datenrate untereinander; liegt das Nachbarsignal im Weg, steht der Text links von der Linie */
    cx.fillText(t1,tx0,T+11); cx.fillText(t2,tx0,T+24); lastEnd=Math.max(lastEnd,tx0+w1);});
  if(cap) cap.textContent=(D.quelle||'BATC')+' · '+(D.mode==='live'?'Echtzeit':'still')+' · Stand '+D.t+(D.why&&D.mode!=='live'?' ('+D.why+')':'');
}
window.__spec=draw; if(window.__SPEC0) draw(window.__SPEC0);
var RT=window.__SPEC_RT, last='';
function poll(){fetch('/spectrum.json?mode='+(RT?'live':'still'),{cache:'no-store'}).then(function(r){return r.json()}).then(function(D){var k=D.t+D.mode; if(RT||k!==last){last=k;draw(D)}}).catch(function(){})}
setInterval(poll, RT?500:4000);
})();
"""


def spec_card():
    an, rt = bool(settings.get("spektrum_an", False)), bool(settings.get("spektrum_echtzeit", False))
    ctl = ('<div class="bar"><b>BATC-Spektrum</b> <form method="post" action="/spektrum/an"><button class="%s">Spektrum: %s</button></form>' % ("on" if an else "", "AN" if an else "AUS"))
    qs_ = settings.get("spektrum_quelle", "auto")
    ctl += ('<form method="post" action="/spektrum/quelle"><select name="q" onchange="this.form.submit()" title="Quelle des Spektrums">'
            '<option value="auto"%s>Quelle: Auto (BATC, bei Ausfall eigener Stick)</option><option value="batc"%s>Quelle: nur BATC-Server</option>'
            '<option value="stick"%s>Quelle: eigener RTL-Stick (Bandscan)</option></select></form>'
            % (" selected" if qs_ == "auto" else "", " selected" if qs_ == "batc" else "", " selected" if qs_ == "stick" else ""))
    if an:
        ctl += ('<form method="post" action="/spektrum/echtzeit"><button class="%s" title="an: Bild wird st&auml;ndig aktualisiert (ca. 2x pro Sekunde); aus: Bild steht still, bis ein neues Signal &uuml;ber den BATC-Server kommt">Echtzeit: %s</button></form>'
                % ("on" if rt else "", "AN" if rt else "AUS"))
        ctl += '<small class="mut" id="spcap"></small>'
    ctl += "</div>"
    if an:
        ctl = ctl[:-6] + info("<b>Still</b> (Echtzeit aus): das Bild steht, bis ein neues Signal &uuml;ber den BATC-Server kommt oder eins verschwindet. <b>Echtzeit</b> (gr&uuml;n): ca. 2 Bilder pro Sekunde. "
                              "Blau = Bake, gelb = erkannte Signale mit Rufzeichen und Datenrate.") + "</div>"
    if not an:
        return '<div class="card">%s%s</div>' % (ctl, info("Das Spektrum kommt vom BATC-Server (eshail.batc.org.uk). Standardm&auml;&szlig;ig steht das Bild still und wird nur bei einem neuen Signal aktualisiert; "
                                                              "&bdquo;Echtzeit&ldquo; aktualisiert es etwa zweimal pro Sekunde."))
    data = json.dumps(spec_json("live" if rt else "still")).replace("</", "<\\/")
    return ('<div class="card">%s<canvas id="spc" width="1200" height="260" style="width:100%%;height:auto;border-radius:10px"></canvas></div>'
            '<script>window.__SPEC0=%s;window.__SPEC_RT=%s;%s</script>' % (ctl, data, "true" if rt else "false", SPEC_JS))


def page(host=None, android=False):
    _CTX.update(host=host, android=android)
    sc = scan
    if sc.running:
        prog = "%s l&auml;uft: %d/%d &ndash; %s" % ("Auto-Scan (neues Signal)" if sc.auto else "Scan", sc.idx, sc.total, label(*sc.cur) if sc.cur else "")
    elif sc.ended:
        prog = "Letzter Scan: %s, %d Signale, %d s" % (time.strftime("%H:%M:%S", time.localtime(sc.started)), len(sc.rows), sc.ended - sc.started)
    else:
        prog = ""
    btn = ('<form method="post" action="/scan/stop"><button class="warn">Scan stoppen</button></form>' if sc.running
           else '<form method="post" action="/scan/start"><button>Alle jetzt scannen</button></form>')
    on = settings.get("auto_scan")
    sw = (('<form method="post" action="/auto/%s"><button class="' + ("on" if on else "") + '">Auto-Scan: %s</button></form> <small>%s</small>')
          % ("off" if on else "on", "AN" if on else "AUS",
             "scannt nur, wenn ein neues Signal erscheint" if on else "kein automatischer Scan"))
    col = {"ok": "#2ecc71", "fehler": "#e74c3c"}.get(bake["status"], "#f1c40f")
    banner = ""
    bad = [(n, q) for n, q in enumerate(zaps, 1) if q.bake["status"] != "ok"]
    for n, q in bad:                                               # nur Fehler/Suche gross anzeigen
        bk = q.bake
        col = {"fehler": "#e74c3c"}.get(bk["status"], "#f1c40f")
        banner += ('<div class="banner %s"><b>%s%s</b></div>'
                   % ("b-err" if bk["status"] == "fehler" else "b-warn", (q.name + ": ") if len(zaps) > 1 else "", html.escape(bk["msg"])))
    detail = " | ".join("%s: %s" % (q.name, q.bake["msg"]) for q in zaps)
    n_gem = sum(1 for q in zaps if q.bake.get("gemerkt"))
    n_ung = sum(1 for q in zaps if q.bake.get("ungeprueft"))
    extra = (", %d gemerkt" % n_gem if n_gem else "") + (", %d nicht gemessen" % n_ung if n_ung else "")
    bake_txt = "" if bad else "&#10003; Bake ok (%d Stick%s%s)" % (len(zaps), "" if len(zaps) == 1 else "s", extra)
    st1 = bool(settings.get("bake_start_rtl1", False))
    banner += ('<div class="bar"><form method="post" action="/bake/check/all"><button title="%s">Bake neu referenzieren</button> '
               '<small style="color:var(--acc)">%s</small></form>'
               '<form method="post" action="/bake/start1"><label class="pill chk%s" title="Beim Start des Dienstes geht RTL1 auf die Bake und bleibt dort, bis du ihn freigibst (zum Test)">'
               '<input type="checkbox"%s onchange="this.form.submit()"> RTL1 beim Start auf die Bake</label></form></div>'
               % (html.escape(detail, quote=True), bake_txt, " on" if st1 else "", " checked" if st1 else ""))
    cw = ('<div class="banner b-warn"><b>%s</b></div>' % cpu.warn) if cpu.warn else ""
    if fb.ERRORS:                                                  # Fehler im Dienst aufgetreten: Meldung mit Weg zum Bericht (Zwischenablage, Mail)
        last = fb.ERRORS[-1]
        cw += ('<div class="banner b-err"><b>&#9888; %d Fehler aufgetreten</b> &ndash; zuletzt %s: %s &nbsp; <a class="pill" href="/fehler" style="color:#fff;border-color:#fff;background:transparent">Bericht kopieren / senden</a></div>'
               % (len(fb.ERRORS), html.escape(last["zeit"][11:]), html.escape((last["quelle"] + ": " + last["text"])[:140])))
    def _col(pct, warn=60, crit=85):
        return "var(--err)" if pct >= crit else ("var(--warn)" if pct >= warn else "var(--acc)")

    def _meter(label, pct, text, color, key=None, scale=100.0):
        """Balken mit aktuellem Wert; farbige Striche: Minimum (blau, links) und Maximum (rot, rechts) der letzten 10 Minuten"""
        marks, rng = "", ""
        h = cpu.long.get(key) if key else None
        if h and len(h) > 2:
            lo, hi = min(h), max(h)
            pos = lambda v: max(0.0, min(100.0, v / scale * 100.0))
            marks = ('<i class="mk mn" style="left:%.1f%%" title="Minimum %.0f (10 min)"></i><i class="mk mx" style="left:%.1f%%" title="Maximum %.0f (10 min)"></i>' % (pos(lo), lo, pos(hi), hi))
            rng = ' <span class="rg"><span class="mnc">min %.0f</span> &middot; <span class="mxc">max %.0f</span></span>' % (lo, hi)
        return ('<div class="mrow"><span>%s</span><div class="meter"><div class="fill" style="width:%d%%;background:%s"></div>%s</div><b class="val">%s%s</b></div>'
                % (label, max(1, min(100, int(pct))), color, marks, text, rng))
    ci = '<div class="card cpu">' + _meter("CPU gesamt", cpu.sys, "%.0f %%" % cpu.sys, _col(cpu.sys), "sys")
    ci += _meter("Dienst", min(100, cpu.prog_core), "%.0f %% eines Kerns (= %.0f %% gesamt)" % (cpu.prog_core, cpu.prog), _col(cpu.prog, 40, 70), "prog")
    if cpu.temp is not None:
        ci += _meter("Temperatur", cpu.temp / 85.0 * 100.0, "%.0f &deg;C" % cpu.temp, _col(cpu.temp, 70, 80), "temp", 85.0)
    ci += ('<div class="mrow"><span>Last</span><div class="chips"><span class="chip%s">%.2f (1 min)</span><span class="chip">%d Kerne</span>'
           '<span class="chip"><i class="mk mn" style="position:static;display:inline-block;height:.9em"></i> Minimum &nbsp;<i class="mk mx" style="position:static;display:inline-block;height:.9em"></i> Maximum (10 min)</span></div><span></span></div></div>'
           % (" hot" if (cpu.load or 0) > cpu.n * 0.8 else "", cpu.load or 0, cpu.n))
    pl = '<b>Player starten</b> <span class="mut">&ndash; UDP-Multicast im Heimnetz (VLC), je Stick eine Farbe</span><div class="rx" style="margin-top:.5em">'
    for n, q in enumerate(zaps, 1):
        cls = "s%d" % ((n - 1) % 5 + 1)
        udp = "udp://@%s:%d" % (q.mcast, q.port)
        pl += '<div style="white-space:nowrap"><a class="pill vlc %s" href="%s" title="%s">&#9654; VLC &middot; %s</a>' % (cls, udp, udp, q.name)
        if q.cur:
            pl += '<a class="pill http %s" href="/z%s/%.3f/%d" title="HTTP-Stream (z.B. Handy)">HTTP</a>' % (cls, "" if n == 1 else n, q.cur[0], q.cur[1])
        pl += '</div><div class="sig">%s<br><span class="mut">%s</span></div>' % (('<span class="live">&#9679; %s</span>' % label(*q.cur)) if q.cur else '<span class="mut">frei</span>', udp)
    pl += "</div>"
    vt = ""
    n_open = dist.count_open()
    if dist.on():
        vt = ('<form method="post" action="/verteiler/off"><button class="on">Autoverteiler: AN</button></form>')
        if n_open and not dist.n_free():
            vt += (' <form method="post" action="/verteilen/neu"><button class="warn"><b>%d</b> neu verteilen</button></form>' % n_open)
        elif n_open:
            vt += ' <small>%d Signal(e) ohne Stick &ndash; wird gleich vergeben</small>' % n_open
    else:
        vt = '<form method="post" action="/verteiler/on"><button>Autoverteiler: AUS</button></form>'
    vt += ('<form method="post" action="/verteiler/bake"><button class="%s" title="Ist kein anderes Signal da, geht RTL1 auf die Bake (und HDMI1 zeigt sie)">OnlyBake=&gt;RTL1: %s</button></form>'
           % ("on" if settings.get("bake_rueckfall", True) else "", "AN" if settings.get("bake_rueckfall", True) else "AUS"))
    boxes = " ".join('<label><input type="checkbox" name="%s" value="1"%s onchange="this.form.submit()"> %s</label>' % (q.name, " checked" if dist.use(q) else "", q.name) for q in zaps)
    vt += (' &nbsp; <form method="post" action="/verteilen/sticks" style="display:inline"><b>%d Stick%s:</b> %s <small>zum Verteilen benutzen</small>'
           '<noscript><button>&Uuml;bernehmen</button></noscript></form>' % (len(zaps), "" if len(zaps) == 1 else "s", boxes))
    rows = "".join(sig_row(f, sr) for f, sr in signals(True))          # Bake steht in der Tabelle (zum Anhoeren), wird aber nicht gescannt
    cur = " &nbsp;|&nbsp; ".join("%s: %s" % (q.name, ("frei" if not q.cur else "<b>%s</b>%s, Zuschauer %d &nbsp;<a href=\"/free/%d\">freigeben</a>"
                                                         % (label(*q.cur), " (fest)" if q.pin else "", len(q.clients), n))) for n, q in enumerate(zaps, 1))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="%d">'
            '<title>QO 100 Multi-Zapper &ndash; %s</title><style>body{font-family:sans-serif;max-width:70em;margin:.6em auto;padding:0 .8em}td,th{border:1px solid #999;padding:.2em .4em;text-align:left;font-size:.9em}'
            'table{border-collapse:collapse}button{padding:.35em .8em;font-size:.95em}h2{margin:.3em 0}</style><body><div class="top"><h2>QO 100 Multi-Zapper &ndash; %s</h2><span class="tools"><a class="pill" href="/hilfe" title="Hilfe">?</a><a class="pill" href="/bake" title="Bake: R&uuml;ckfall, Messung beim Start, gemerkte Offsets">&#9881;B</a><a class="pill" href="/display" title="Displays am Pi (HDMI)">&#9881;D</a><a class="pill" href="/config" title="Einstellungen">&#9881;</a></span></div>%s%s<div class="bar">%s %s</div><div class="bar">%s</div><p class="mut">%s</p><div class="card">%s</div>'
            '<div style="overflow-x:auto"><table><tr>%s</tr>%s</table></div>'
            '<div class="bar"><span class="mut">Hilfe zur Signaltabelle</span>%s</div>'
            '%s<div class="bar">%s <a class="pill" href="/manuell">Manuelle Frequenz</a></div><p class="mut">Playlist (m3u) f&uuml;r VLC: %s &middot; <a href="/status">Status (JSON)</a></p><div class="card">%s</div>%s</body>'
            % (3 if sc.running else 5, html.escape(A.title), html.escape(A.title), banner, cw, btn, sw, vt, prog, cur, "".join("<th>%s</th>" % h for h in HEAD), rows,
               info("<b>Signaltabelle:</b> jede Zeile ist ein Signal vom BATC-Spektrum, die gr&uuml;ne Zeile l&auml;uft gerade live auf einem Stick. Die Werte stammen aus der letzten Messung bzw. dem Scan.<br>"
                    "<b>RTL1 / RTL2 / RTL3</b> in der ersten Spalte stellen den jeweiligen Stick auf dieses Signal und halten ihn dort fest, bis du ihn mit &bdquo;freigeben&ldquo; frei gibst. "
                    "Der <b>Signalname</b> &ouml;ffnet bei einem Signal VLC mit der Playlist (angeklicktes Signal zuerst), bei der Bake den einzelnen Stream.<br>"
                    "<b>Scan</b> l&auml;uft auf RTL1; der <b>Autoverteiler</b> legt neue Signale auf freie Sticks (Haken je Stick: &bdquo;zum Verteilen benutzen&ldquo;). "
                    "<b>OnlyBake=&gt;RTL1</b>: ist nichts anderes da, geht RTL1 auf die Bake und HDMI1 zeigt sie. Mehr: <a href=\"/hilfe\">Hilfe</a>."), ci, other_links(host, "main,manuell") or "",
               " &middot; ".join('<a href="/qo100%s.m3u">%s</a>' % ("" if n == 1 else "_rtl%d" % n, q.name) for n, q in enumerate(zaps, 1)), pl, spec_card()))


MAN_PRESETS = ((144.5, "2 m"), (437.0, "70 cm"), (1255.0, "23 cm"), (1291.0, "23 cm"))   # Schnellwahl-Knoepfe (GUI-QRG-Dialog: 144,5 / 437 / 1291; der RTL-Stick reicht bis ca. 1766 MHz)


_LAST_HOST = [None]


# ---- Displays am Raspberry Pi: Stick -> HDMI-Ausgang, Stream im Vollbild (Menue /display, Symbol "gear D" oben rechts) ----
DISPLAY_CONNECTORS = ("HDMI-A-1", "HDMI-A-2")
DISPLAY_DEFAULT = {"HDMI-A-1": {"stick": 1, "an": True}, "HDMI-A-2": {"stick": 2, "an": False}}       # HDMI2 -> RTL2 vorbereitet, nicht aktiviert
PLAYER_CANDIDATES = ("vlc", "cvlc", "mpv", "ffplay", "gst-launch-1.0")


# Einblendung (Text im Bild) auf den Displays: Felder, Reihenfolge, Trenner, Position, Groesse, Farbe - einstellbar auf der Seite /display
MARQ_FIELDS = (("stick", "Stick (RTL1 \u2026)", "RTL1"), ("rufzeichen", "Rufzeichen / Dienstname", "A71A"), ("provider", "Provider", "QARS"),
               ("freq", "Frequenz", "10491.500 MHz"), ("sr", "Symbolrate", "1500 kS"), ("mod", "Modulation / FEC", "QPSK 4/5"), ("mer", "MER", "MER 10.5 dB"),
               ("esn0", "Es/N0", "Es/N0 11.1 dB"), ("pegel", "Pegel \u00fcber Rauschen", "Pegel 12 dB"), ("bitrate", "Bitrate", "2.51 Mbit/s"),
               ("udp", "UDP-Adresse", "230.0.0.30:1234"), ("lock", "Lock-Status", "gelockt"))
MARQ_POS = ((8, "unten Mitte"), (4, "oben Mitte"), (5, "oben links"), (6, "oben rechts"), (9, "unten links"), (10, "unten rechts"), (0, "Mitte"))
MARQ_DEFAULT = {"on": True, "fields": ["stick", "rufzeichen", "freq", "sr", "mod", "mer"], "sep": "  \u00b7  ", "pos": 8, "size": 28, "opacity": 220, "color": "FFFFFF"}


def marq_cfg():
    c = dict(MARQ_DEFAULT, **(settings.get("display_marq") or {}))
    c["fields"] = [k for k in c["fields"] if k in {f[0] for f in MARQ_FIELDS}]
    return c


def marq_text_for(q, c=None):
    c = c or marq_cfg()
    m = q.snapshot() if q.cur else {}
    f1 = lambda v, fmt: (fmt % v) if v is not None else ""
    val = {"stick": q.name, "rufzeichen": m.get("rufzeichen") or "", "provider": m.get("provider") or "", "freq": ("%.3f MHz" % q.cur[0]) if q.cur else "",
           "sr": ("%.0f kS" % (m["sr_det"] * 1000)) if m.get("sr_det") else (("%d kS" % q.cur[1]) if q.cur and q.cur[1] else ""),
           "mod": ("%s %s" % (m.get("mod") or "", m.get("fec") or "")).strip(), "mer": f1(m.get("mer"), "MER %.1f dB"), "esn0": f1(m.get("esn0"), "Es/N0 %.1f dB"),
           "pegel": f1(m.get("pegel_db"), "Pegel %.0f dB"), "bitrate": f1(m.get("bitrate"), "%.2f Mbit/s"), "udp": "%s:%d" % (q.mcast, q.port),
           "lock": "gelockt" if m.get("lock") else "Suche"}
    return c["sep"].join(x for x in (val.get(k, "") for k in c["fields"]) if x)


def disp_cfg(conn):
    d = (settings.get("displays") or {}).get(conn) or {}
    base = DISPLAY_DEFAULT.get(conn, {"stick": 1, "an": False})
    return {"stick": int(d.get("stick", base["stick"])), "an": bool(d.get("an", base["an"])), "player": d.get("player") if d.get("player") in ("vlc", "cvlc", "mpv", "ffplay") else "auto",
            "bake": bool(d.get("bake", settings.get("display_bake", False)))}


def disp_players():
    """installierte Player (Pfad, Version) - bei jedem Aufruf des Menues neu geprueft"""
    out = []
    for n in PLAYER_CANDIDATES:
        pth = shutil.which(n)
        ver = ""
        if pth and n in ("vlc", "mpv", "ffplay"):
            try:
                r = subprocess.run([pth, "--version" if n != "ffplay" else "-version"], capture_output=True, text=True, timeout=5)
                ver = ((r.stdout or r.stderr).splitlines() or [""])[0][:70]
            except (OSError, subprocess.TimeoutExpired):
                pass
        out.append({"name": n, "pfad": pth, "version": ver})
    return out


def disp_env():
    """Umgebung der Desktop-Sitzung am Pi (Dienst laeuft ohne Sitzung): Wayland/XWayland des angemeldeten Benutzers"""
    rt = "/run/user/%d" % os.getuid()
    env = dict(os.environ, XDG_RUNTIME_DIR=rt)
    wl = next((x for x in sorted(os.listdir(rt)) if re.match(r"^wayland-\d+$", x)), None) if os.path.isdir(rt) else None
    if wl:
        env["WAYLAND_DISPLAY"] = wl
    env.setdefault("DISPLAY", ":0")
    return env, bool(wl)


def disp_connectors():
    """HDMI-Anschluesse: angeschlossen (sysfs) und aktueller Modus/Position (wlr-randr, wenn die Sitzung laeuft)"""
    env, has_wl = disp_env()
    geo = {}
    if has_wl and shutil.which("wlr-randr"):
        try:
            txt = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=5, env=env).stdout
        except (OSError, subprocess.TimeoutExpired):
            txt = ""
        cur = None
        for ln in txt.splitlines():
            m = re.match(r"^(\S+) ", ln)
            if m and not ln.startswith(" "):
                cur = m.group(1); geo[cur] = {}
            elif cur:
                m = re.search(r"(\d+)x(\d+) px.*\(current\)", ln)
                if m:
                    geo[cur]["w"], geo[cur]["h"] = int(m.group(1)), int(m.group(2))
                m = re.search(r"Position:\s*(-?\d+),(-?\d+)", ln)
                if m:
                    geo[cur]["x"], geo[cur]["y"] = int(m.group(1)), int(m.group(2))
    res = []
    for c in DISPLAY_CONNECTORS:
        st = None
        try:
            import glob as _g
            for pth in _g.glob("/sys/class/drm/card*-%s/status" % c):
                st = open(pth).read().strip()
        except OSError:
            pass
        g = geo.get(c, {})
        res.append({"name": c, "status": st or "unbekannt", "verbunden": st == "connected", "modus": ("%dx%d" % (g["w"], g["h"])) if g.get("w") else None,
                    "x": g.get("x", 0), "y": g.get("y", 0)})
    return res


class Displays:
    """pro HDMI-Anschluss einen Vollbild-Player fuer den zugewiesenen Stick: startet, wenn der Monitor angeschlossen, die Anzeige an und der Stick gelockt ist;
    stoppt bei Abziehen/Aus/Signalverlust (10 s) und startet bei Signalwechsel neu (wie die Resync-Routine der GUI)"""

    def __init__(s):
        s.proc = {}           # connector -> {"p": Popen, "key": ..., "player": ...}
        s.nolock = {}
        s.msg = {}
        s.player = None
        s.lk = threading.RLock()
        s.conns = {}          # letzter Stand der HDMI-Anschluesse (fuer die API, ohne bei jedem Abruf neu zu pruefen)
        s.log = collections.deque(maxlen=80)      # Meldungen (Datei /tmp/dienst_display.log und Seite /display)
        s.last_msg = {}
        s.retry = {}
        s.fails = {}
        s.forced = set()      # Knopf "Player starten": laeuft, bis "Player stoppen" / Monitor weg (unabhaengig von Lock/Bake/an)

    def pick_player(s, want="auto"):
        """gewaehlter Player, sonst (auto oder nicht installiert) der erste installierte aus cvlc, vlc, mpv, ffplay"""
        if want != "auto" and shutil.which(want):
            return want
        for n in ("cvlc", "vlc", "mpv", "ffplay"):
            if shutil.which(n):
                return n
        return None

    def cmd(s, player, conn, geo, q, title, marq):
        url = "udp://@%s:%d" % (q.mcast, q.port)
        if player in ("cvlc", "vlc"):
            mc = marq_cfg()
            c = [shutil.which(player), "--fullscreen", "--no-video-title-show", "--no-osd", "--network-caching=1000", "--demux=ts", "--avcodec-hw=none",
                 "--vout=xcb_x11", "--video-x=%d" % geo.get("x", 0), "--video-y=%d" % geo.get("y", 0), "--no-qt-recentplay"]
            if mc["on"]:
                c += ["--sub-source=marq", "--marq-file=%s" % marq, "--marq-position=%d" % mc["pos"], "--marq-size=%d" % mc["size"], "--marq-opacity=%d" % mc["opacity"],
                      "--marq-color=0x%s" % mc["color"], "--marq-refresh=1000"]
            if player == "vlc":
                c += ["--intf=dummy"]
            return c + [url]
        if player == "mpv":
            return [shutil.which("mpv"), "--fs", "--no-border", "--profile=low-latency", "--untimed", url]
        return [shutil.which("ffplay"), "-fs", "-noborder", "-fflags", "nobuffer", url]

    def dlog(s, txt):
        ln = "%s  %s" % (time.strftime("%H:%M:%S"), txt)
        s.log.append(ln)
        try:
            with open("/tmp/dienst_display.log", "a", encoding="utf-8") as f:
                f.write(ln + "\n")
        except OSError:
            pass

    @staticmethod
    def errfile(conn):
        return "/tmp/dienst_display_%s.err" % conn

    def err_tail(s, conn, n=3):
        try:
            return " | ".join([l.strip() for l in open(s.errfile(conn), errors="replace").read().splitlines() if l.strip()][-n:])[:300]
        except OSError:
            return ""

    def has_window(s, player):
        """gibt es ein X11-Fenster des Players (xwininfo am XWayland)? None = nicht pruefbar"""
        if not shutil.which("xwininfo"):
            return None
        try:
            out = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True, timeout=4, env=disp_env()[0]).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        name = {"cvlc": "VLC media player", "vlc": "VLC media player", "mpv": "mpv", "ffplay": "ffplay"}.get(player, player)
        return name.lower() in out.lower() or ('"udp://' in out)

    def stop(s, conn, why=""):
        e = s.proc.pop(conn, None)
        if e and e["p"].poll() is None:
            try:
                os.killpg(e["p"].pid, signal.SIGTERM)
                e["p"].wait(timeout=3)
            except Exception:
                try:
                    os.killpg(e["p"].pid, signal.SIGKILL)
                except Exception:
                    pass
            s.dlog("Display %s: Player gestoppt%s" % (conn, (" (%s)" % why) if why else ""))
        s.msg[conn] = why or "aus"

    def marq_text(s, q):
        return marq_text_for(q)

    def tick(s):
        with s.lk:
            s._tick()

    def _tick(s):
        conns = {c["name"]: c for c in disp_connectors()}
        s.conns = conns
        env, has_wl = disp_env()
        s.player = s.pick_player()
        for conn in DISPLAY_CONNECTORS:
            cfg = disp_cfg(conn)
            player = s.pick_player(cfg["player"])
            c = conns.get(conn) or {}
            e = s.proc.get(conn)
            if e and e["p"].poll() is not None:                  # Player beendet sich von selbst: Ursache festhalten
                rc, age = e["p"].returncode, time.time() - e.get("t0", time.time())
                s.fails[conn] = s.fails.get(conn, 0) + 1
                s.dlog("Display %s: Player %s beendet (Exit %s) nach %.0f s. Letzte Meldungen: %s" % (conn, e.get("player"), rc, age, s.err_tail(conn) or "-"))
                s.retry[conn] = time.time() + min(60, 10 * s.fails[conn])
                s.proc.pop(conn, None); e = None
            forced = conn in s.forced
            if not c.get("verbunden"):
                s.forced.discard(conn)
                if e: s.stop(conn, "Monitor abgezogen")
                s.msg[conn] = "kein Monitor angeschlossen" if cfg["an"] else "aus"; continue
            if not cfg["an"] and not forced:
                if e: s.stop(conn, "Anzeige aus")
                s.msg[conn] = "aus"; continue
            if not player:
                s.msg[conn] = "kein Player installiert"; continue
            if not has_wl:
                s.msg[conn] = "keine Desktop-Sitzung (Wayland) am Pi"; continue
            if not 1 <= cfg["stick"] <= len(zaps):
                s.msg[conn] = "Stick %d gibt es nicht" % cfg["stick"]; continue
            q = zaps[cfg["stick"] - 1]
            m = q.snapshot() if q.cur else None
            is_bake = bool(q.cur) and abs(q.cur[0] - 10491.5) < 0.5
            want = bool(q.cur and m and m.get("lock") and (cfg["bake"] or q.name == dist.bake_stick or q.name in dist.bake_hold or not is_bake)) or forced
            if not forced and q.cur and m and not m.get("lock") and e:
                s.nolock.setdefault(conn, time.time())
                want = time.time() - s.nolock[conn] < 10
            else:
                s.nolock.pop(conn, None)
            if not want:
                if e: s.stop(conn, "kein Signal")
                s.msg[conn] = "wartet auf Signal von %s" % q.name; continue
            key = (round(q.cur[0], 2), q.cur[1], m.get("sr_det"), m.get("mod"), m.get("fec"), q.mcast) if (q.cur and m) else ("frei", q.mcast)
            if e and (e["key"] != key and not forced or e.get("player") != player):
                s.stop(conn, "Player/Signal geaendert"); time.sleep(0.4); e = None
            marq = "/tmp/dienst_display_%s.txt" % conn
            try:
                open(marq, "w", encoding="utf-8").write(s.marq_text(q))
            except OSError:
                pass
            if not e and time.time() < s.retry.get(conn, 0):
                s.msg[conn] = "Player beendet/startete nicht (siehe Log) - neuer Versuch folgt"; continue
            if not e:
                try:
                    penv = {k: v for k, v in env.items() if k != "WAYLAND_DISPLAY"}        # Player ueber XWayland (X11-Fenster mit fester Lage), nicht nativ unter Wayland
                    p = subprocess.Popen(s.cmd(player, conn, c, q, "%s %s" % (conn, q.name), marq), env=penv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=open(s.errfile(conn), "w"), start_new_session=True)
                    s.proc[conn] = {"p": p, "key": key, "player": player, "t0": time.time()}
                    s.dlog("Display %s: %s gestartet fuer %s %s (PID %d%s)" % (conn, player, q.name, label(*q.cur) if q.cur else "ohne Signal", p.pid, ", von Hand" if forced else ""))
                except OSError as ex:
                    s.dlog("Display %s: Player %s startet nicht: %s" % (conn, player, ex)); s.msg[conn] = "Player startet nicht: %s" % ex; continue
            e = s.proc.get(conn)
            run_s = time.time() - (e or {}).get("t0", time.time())
            streaming = time.time() - q.last_data < 5.0
            if not q.cur or not streaming:
                s.msg[conn] = "Player l\u00e4uft, aber KEIN Videostrom: %s hat %s (VLC zeigt erst mit ankommendem Bild ein Fenster)" % (q.name, "kein Signal" if not q.cur else "keine Daten")
            else:
                win = s.has_window(player) if run_s > 8 else True
                s.msg[conn] = ("%s zeigt %s" % (q.name, label(*q.cur))) if win is not False else "Player l\u00e4uft und Strom kommt an, aber KEIN Fenster auf dem Display (XWayland/Sitzung?)"
                if win is False and s.last_msg.get(conn) != s.msg[conn]:
                    s.dlog("Display %s: %s" % (conn, s.msg[conn]))

    def loop(s):
        while True:
            try:
                s.tick()
                for c, m in list(s.msg.items()):
                    if s.last_msg.get(c) != m:
                        s.dlog("Display %s: %s" % (c, m))
                        s.last_msg[c] = m
            except Exception as ex:
                print("display:", ex, flush=True)
            time.sleep(2)

    def release_all(s):
        for conn in list(s.proc):
            s.stop(conn, "Dienst beendet")


displays = Displays()


def display_state(host=None):
    return {"version": 1, "player_gewaehlt": displays.pick_player(), "player": disp_players(), "sitzung_wayland": disp_env()[1],
            "displays": [dict(c, **disp_cfg(c["name"]), laeuft=(c["name"] in displays.proc and displays.proc[c["name"]]["p"].poll() is None), meldung=displays.msg.get(c["name"], ""), von_hand=(c["name"] in displays.forced),
                                                          player_aktiv=(displays.proc.get(c["name"]) or {}).get("player"))
                         for c in disp_connectors()], "einblendung": marq_cfg(), "log": list(displays.log)[-30:]}


def display_page(msg="", host=None):
    host = host or _LAST_HOST[0]
    st = display_state()
    pl = "".join("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (html.escape(p["name"]), ("&#10003; " + html.escape(p["pfad"])) if p["pfad"] else "<span style=color:#999>nicht installiert</span>", html.escape(p["version"])) for p in st["player"])
    rows = ""
    for d in st["displays"]:
        sel = "".join('<option value="%d"%s>%s</option>' % (n, " selected" if d["stick"] == n else "", q.name) for n, q in enumerate(zaps, 1))
        con = ("&#9679; angeschlossen%s" % ((" (%s)" % d["modus"]) if d["modus"] else "")) if d["verbunden"] else "<span style=color:#999>&#9675; kein Monitor</span>"
        inst = {p["name"]: bool(p["pfad"]) for p in st["player"]}
        popt = '<option value="auto"%s>automatisch (%s)</option>' % (" selected" if d["player"] == "auto" else "", html.escape(st["player_gewaehlt"] or "keiner"))
        popt += "".join('<option value="%s"%s%s>%s%s</option>' % (n, " selected" if d["player"] == n else "", "" if inst.get(n) else " disabled", n, "" if inst.get(n) else " (nicht installiert)") for n in ("cvlc", "vlc", "mpv", "ffplay"))
        btn = ('<button formaction="/display/stop/%s" formmethod="post">Player stoppen</button>' if d["laeuft"] else '<button formaction="/display/start/%s" formmethod="post"%s>Player starten</button>') % ((d["name"],) if d["laeuft"] else (d["name"], "" if d["verbunden"] else " disabled title=\"kein Monitor angeschlossen\""))
        bk = '<button formaction="/display/bake/%s" formmethod="post" class="%s" title="zu Testzwecken: auch die Bake (10491,5 MHz) im Vollbild zeigen">Bake: %s</button>' % (d["name"], "on" if d["bake"] else "", "an" if d["bake"] else "aus")
        rows += ('<tr><td><b>%s</b></td><td>%s</td><td><select name="stick_%s">%s</select></td><td><label><input type="checkbox" name="an_%s" value="1"%s> an</label></td><td>%s%s</td>'
                 '<td style="white-space:nowrap"><select name="player_%s">%s</select> %s %s</td></tr>'
                 % (d["name"], con, d["name"], sel, d["name"], " checked" if d["an"] else "", html.escape(d["meldung"] or ""), (" &#9654; l&auml;uft (%s)" % html.escape(d.get("player_aktiv") or "")) if d["laeuft"] else "", d["name"], popt, btn, bk))
    form = ('<form method="post" action="/display/set"><h3>Displays am Raspberry Pi</h3><table><tr><th>Ausgang</th><th>Monitor</th><th>zeigt Stick</th><th>Anzeige</th><th>Status</th><th>Player</th></tr>%s</table>'
            '<p><button formaction="/display/set" formmethod="post">Speichern</button> &nbsp; %s</p></form>' % (rows, info("<b>Player starten</b> startet den gew&auml;hlten Player sofort im Vollbild (auch ohne Signal) und l&auml;sst ihn laufen, bis du ihn stoppst. "
                 "<b>Bake: an</b> l&auml;sst zu Testzwecken auch die Bake im Vollbild zu (sonst nur andere Signale). Ein angeschlossener Monitor zeigt den Stream des Sticks im Vollbild, "
                 "sobald dieser gelockt ist; bei Signal- oder Datenratenwechsel startet der Player neu. Die Seite pr&uuml;ft Player und Monitore bei jedem Aufruf neu. "
                 "Der MultiPlayer am PC zeigt einen Stick, der einem aktiven Monitor zugewiesen ist, nicht noch einmal (Zeile grau, Rechtsklick: trotzdem darstellen).")))
    mc = marq_cfg()
    order = list(mc["fields"]) + [f[0] for f in MARQ_FIELDS if f[0] not in mc["fields"]]
    lab = {f[0]: (f[1], f[2]) for f in MARQ_FIELDS}
    mrows = "".join('<tr><td><input type="checkbox" name="mf_%s" value="1"%s></td><td><input type="number" name="mo_%s" value="%d" min="1" max="20" style="width:4em"></td><td>%s</td><td class="mut">%s</td></tr>'
                    % (k, " checked" if k in mc["fields"] else "", k, i, html.escape(lab[k][0]), html.escape(lab[k][1])) for i, k in enumerate(order, 1))
    pos = "".join('<option value="%d"%s>%s</option>' % (v, " selected" if v == mc["pos"] else "", n) for v, n in MARQ_POS)
    prev = mc["sep"].join(lab[k][1] for k in mc["fields"]) or "(nichts)"
    marq_form = ('<h3>Text im Bild (Einblendung auf den Displays)</h3><form method="post" action="/display/marq"><div class="card">'
                 '<p><label><input type="checkbox" name="on" value="1"%s> Text einblenden (nur VLC)</label></p>'
                 '<table><tr><th>zeigen</th><th>Reihenfolge</th><th>Feld</th><th>Beispiel</th></tr>%s</table>'
                 '<p>Trennzeichen <input name="sep" size="8" value="%s"> &nbsp; Position <select name="pos">%s</select> &nbsp; Gr&ouml;&szlig;e <input type="number" name="size" value="%d" min="10" max="90" style="width:4.5em"> '
                 '&nbsp; Deckkraft <input type="number" name="opacity" value="%d" min="0" max="255" style="width:4.5em"> &nbsp; Farbe (RRGGBB) <input name="color" size="7" value="%s"></p>'
                 '<p><b>Vorschau:</b> <span style="color:var(--link)">%s</span></p>'
                 '<p><button class="go" formaction="/display/marq" formmethod="post">Speichern</button> <button formaction="/display/marq/restart" formmethod="post">Speichern + Player neu starten</button> '
                 '<small>Die Felder gelten sofort (die Textdatei wird alle 2 s neu geschrieben); Position, Gr&ouml;&szlig;e, Deckkraft und Farbe ab dem n&auml;chsten Start des Players. Sortiert wird nach der Reihenfolge-Zahl.</small></p></div></form>'
                 % (" checked" if mc["on"] else "", mrows, html.escape(mc["sep"], quote=True), pos, mc["size"], mc["opacity"], mc["color"], html.escape(prev)))
    lg = marq_form + "<h3>Display-Log</h3><pre style='padding:.5em;max-height:14em;overflow:auto;font-size:.85em'>%s</pre>" % html.escape("\n".join(st["log"][-20:]) or "(noch keine Meldungen)")
    info_html = lg + ("<h3>Installierte Player</h3><table><tr><th>Player</th><th>Pfad</th><th>Version</th></tr>%s</table><p><small>Gew&auml;hlt: <b>%s</b>. Desktop-Sitzung (Wayland) am Pi: %s</small></p>"
            % (pl, html.escape(st["player_gewaehlt"] or "keiner"), "ja" if st["sitzung_wayland"] else "<b style=color:#c0392b>nein</b>"))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Displays &ndash; %s</title>'
            '<style>body{font-family:sans-serif;max-width:60em;margin:.8em auto;padding:0 .8em}td,th{border:1px solid #999;padding:.2em .5em;text-align:left}table{border-collapse:collapse}button{padding:.45em 1em;font-size:1em}</style>'
            '<body><h2>&#9881;D Displays &ndash; %s</h2><p>%s</p>%s%s%s</body>' % (html.escape(A.title), html.escape(A.title), other_links(host, "display"),
            ('<p style="color:#c0392b"><b>%s</b></p>' % html.escape(msg)) if msg else "", form, info_html))


def bake_page(msg="", host=None):
    host = host or _LAST_HOST[0]
    g = lambda k, d: settings.get(k, d)
    sel = "".join('<option value="%d"%s>%s</option>' % (n, " selected" if int(g("bake_rueckfall_stick", 1)) == n else "", q.name) for n, q in enumerate(zaps, 1))
    rows = ""
    for n, q in enumerate(zaps, 1):
        b = q.bake
        art = "gemerkt" if b.get("gemerkt") else ("nicht gemessen" if b.get("ungeprueft") else ("gemessen" if b["status"] == "ok" else b["status"]))
        rows += ('<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><button formaction="/bake/check/%d" formmethod="post">jetzt messen</button></td></tr>'
                 % (q.name, html.escape(str(q.index)), art, ("%+.1f kHz" % b["ablage_khz"]) if b.get("ablage_khz") is not None else "&ndash;",
                    ("%.4f MHz" % b["lo_eff"]) if b.get("lo_eff") else "&ndash;", html.escape(b.get("zeit") or "") + (" &middot; Es/N0 %.1f dB" % b["esn0"] if b.get("esn0") is not None else ""), n))
    form = ('<form method="post" action="/bake/set"><h3>Bake &ndash; Verhalten beim Start und im Leerlauf</h3><div class="card">'
            '<p><label><input type="checkbox" name="rueckfall" value="1"%s> <b>OnlyBake=&gt;RTL1</b> (Bake-R&uuml;ckfall): ist kein anderes Signal da, geht der Stick auf die Bake (und der Display-Player auf HDMI zeigt sie); sobald ein Signal erscheint, wird er freigegeben</label> '
            '&nbsp; Stick: <select name="rstick">%s</select></p>'
            '<p><label><input type="checkbox" name="start1" value="1"%s> <b>RTL1 beim Start auf die Bake</b> (wird der Bake zugewiesen und bleibt dort, bis du ihn freigibst; Display-Player zeigt sie)</label></p>'
            '<p><label><input type="checkbox" name="immer" value="1"%s> <b>Immer Bake beim Start messen</b> (zum Test: auch mit gemerktem Offset und auch wenn schon Signale da sind)</label></p>'
            '<p><label><input type="checkbox" name="cache" value="1"%s> <b>Gemerkten Offset benutzen</b> (Start ohne Bake-Messung, wenn der Offset von fr&uuml;her bekannt ist)</label> '
            '&nbsp; g&uuml;ltig <input type="number" name="tage" value="%s" min="1" max="60" style="width:4.5em"> Tage</p>'
            '<p><b>Frische Referenz:</b> &bdquo;Bake neu referenzieren&ldquo; misst nicht neu, wenn die Messung j&uuml;nger ist als <input type="number" name="frisch" value="%s" min="0" max="1440" style="width:4.5em"> Minuten %s</p>'
            '<p><button class="go">Speichern</button> <small>&Auml;nderungen am Start-Verhalten gelten ab dem n&auml;chsten Start des Dienstes; der R&uuml;ckfall wirkt sofort.</small></p></div>'
            '<h3>Offset der Sticks</h3><table><tr><th>Stick</th><th>Kennung</th><th>Stand</th><th>Ablage</th><th>LO-Korrektur</th><th>Zeit</th><th></th></tr>%s</table>'
            '<p><button formaction="/bake/check/all?force" formmethod="post">Alle Sticks jetzt messen</button> <button formaction="/bake/cache/loeschen" formmethod="post">Gemerkte Offsets l&ouml;schen</button> '
            '<small>Messen belegt einen Stick etwa 30 s auf der Bake (nicht bei festgehaltenen Sticks).</small></p></form>'
            % (" checked" if g("bake_rueckfall", True) else "", sel, " checked" if g("bake_start_rtl1", False) else "", " checked" if g("bake_immer_start", False) else "", " checked" if g("bake_cache_nutzen", True) else "", g("bake_cache_tage", 7), g("bake_frisch_min", 30),
               info("Zeitstempel der Bake-Messung eines Sticks. Dr&uuml;ckst du &bdquo;Bake neu referenzieren&ldquo;, werden Sticks, deren Messung j&uuml;nger ist als diese Minutenzahl, nicht noch einmal auf der Bake gemessen (spart etwa 30 s je Stick); sie stehen sofort f&uuml;r Signale bereit. 0 = immer neu messen. Standard 30 Minuten. Der Knopf &bdquo;Alle Sticks jetzt messen&ldquo; misst immer."), rows))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bake &ndash; %s</title><style></style>'
            '<body><h2>&#9881;B Bake &ndash; %s</h2><p>%s</p>%s%s</body>' % (html.escape(A.title), html.escape(A.title), other_links(host, "bake"),
            ('<p style="color:var(--err)"><b>%s</b></p>' % html.escape(msg)) if msg else "", form))


def bake_save(body):
    from urllib.parse import parse_qs
    f = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
    def num(k, lo, hi, d):
        try:
            return min(max(int(f.get(k, d)), lo), hi)
        except ValueError:
            return d
    settings["bake_rueckfall"] = "rueckfall" in f
    settings["bake_rueckfall_stick"] = num("rstick", 1, len(zaps), 1)
    settings["bake_start_rtl1"] = "start1" in f
    settings["bake_immer_start"] = "immer" in f
    settings["bake_cache_nutzen"] = "cache" in f
    settings["bake_cache_tage"] = num("tage", 1, 60, 7)
    settings["bake_frisch_min"] = num("frisch", 0, 1440, 30)
    save_settings()


def marq_save(body):
    from urllib.parse import parse_qs
    f = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
    chosen = []
    for k, _l, _e in MARQ_FIELDS:
        if ("mf_" + k) in f:
            try:
                o = int(f.get("mo_" + k, "99"))
            except ValueError:
                o = 99
            chosen.append((o, k))
    def num(key, lo, hi, dflt):
        try:
            return min(max(int(f.get(key, dflt)), lo), hi)
        except ValueError:
            return dflt
    col = re.sub(r"[^0-9A-Fa-f]", "", f.get("color", "FFFFFF"))[:6].upper().ljust(6, "F")
    settings["display_marq"] = {"on": "on" in f, "fields": [k for _o, k in sorted(chosen)], "sep": f.get("sep", MARQ_DEFAULT["sep"]) or " ",
                                "pos": num("pos", 0, 10, 8), "size": num("size", 10, 90, 28), "opacity": num("opacity", 0, 255, 220), "color": col}
    save_settings()


def display_save(body):
    from urllib.parse import parse_qs
    f = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
    d = {}
    for c in DISPLAY_CONNECTORS:
        try:
            n = int(f.get("stick_" + c, disp_cfg(c)["stick"]))
        except ValueError:
            n = disp_cfg(c)["stick"]
        d[c] = {"stick": min(max(n, 1), len(zaps)), "an": ("an_" + c) in f, "player": f.get("player_" + c, "auto"), "bake": disp_cfg(c)["bake"]}
    settings["displays"] = d
    save_settings()


# ---- Konfiguration per Webseite (nur im Dienst: dienst.conf; datvrx_dienst.py setzt CONF_PATH und RESTART_ARGV) ----
CONF_PATH = None
RESTART_ARGV = list(sys.argv)
CONF_FIELDS = [("lo_mhz", "LO-Frequenz des LNB (MHz)", "9540.054"), ("ppm", "Frequenzkorrektur ppm", "0"), ("verstaerkung_db", "Tuner-Verst\u00e4rkung (dB)", "40"),
               ("multicast", "Multicast-Adresse RTL1 (weitere: +1, +2 \u2026)", "230.0.0.30"), ("multicast_port", "Multicast-Port", "1234"), ("handy_port", "Port der Handy-Seite (0 = aus)", "8555"),
               ("kopfzeile", "Kopfzeile / Rufzeichen", "DG7NDV"), ("bake_sekunden_je_versuch", "Bake-Pr\u00fcfung: Sekunden je Versuch", "25"),
               ("scan_sekunden_je_signal", "Scan: Sekunden je Signal", "28"), ("stick_frei_nach_sekunden", "Stick freigeben nach (s ohne Zuschauer)", "60"),
               ("mqtt", "MQTT-Broker (leer = aus)", ""), ("cpu_warnung_prozent", "CPU-Warnung ab (%)", "80")]


def conf_read():
    import configparser
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
    cp.read(CONF_PATH)
    return dict(cp["dienst"]) if cp.has_section("dienst") else {}


def conf_write(updates):
    """Werte in dienst.conf ersetzen (Kommentare und Reihenfolge bleiben), fehlende Schluessel am Ende anhaengen; vorher Sicherung"""
    import shutil as _sh
    _sh.copy2(CONF_PATH, CONF_PATH + ".vor_web_" + time.strftime("%Y%m%d_%H%M%S"))
    lines = open(CONF_PATH).read().split("\n")
    done = set()
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*=\s*)([^#;]*?)(\s+[#;].*)?$", ln)
        if m and m.group(2) in updates and not ln.lstrip().startswith(("#", ";")):
            k = m.group(2)
            val = updates[k]
            tail = m.group(5) or ""
            lines[i] = "%s%s%s%s%s" % (m.group(1), k, m.group(3), val, (" " * max(1, 36 - len(k) - len(val) - 3) + tail.strip()) if tail else "")
            done.add(k)
    while lines and lines[-1] == "":
        lines.pop()
    for k, v in updates.items():
        if k not in done:
            lines.append("%s = %s" % (k, v))
    open(CONF_PATH, "w").write("\n".join(lines) + "\n")


def list_rtl():
    """alle RTL-Sticks am USB-Bus: [(Nr, Name, Seriennummer, Kennung)]; Kennung = Seriennummer wenn eindeutig und nicht rein numerisch, sonst die Nummer"""
    try:
        r = subprocess.run(["rtl_test", "-d", "99"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    devs = re.findall(r"^\s*(\d+):\s+(.*?),\s+SN:\s*(\S*)", r.stdout + r.stderr, re.M)
    sers = [d[2] for d in devs]
    return [(int(n), name, sn, (sn if sn and not sn.isdigit() and sers.count(sn) == 1 else n)) for n, name, sn in devs]


def restart_self():
    """Dienst neu starten: Sticks freigeben, dann den Prozess durch sich selbst ersetzen (gleiche PID, systemd merkt nichts)"""
    def go():
        time.sleep(1.0)
        for q in zaps:
            try:
                q.release()
            except Exception:
                pass
        os.execv(sys.executable, [sys.executable, "-u"] + RESTART_ARGV)
    threading.Thread(target=go, daemon=True).start()


def config_page(msg="", host=None):
    host = host or _LAST_HOST[0]
    if not CONF_PATH:
        return "<!doctype html><meta charset=utf-8><p>Konfiguration nur im RTL-SDR-datvRX-Dienst (dienst.conf).</p>"
    cfg = conf_read()
    devs = list_rtl()
    cur_sticks = (cfg.get("sticks") or "").strip()
    auto = cur_sticks.lower() == "auto"
    chosen = [x.strip() for x in cur_sticks.split(",") if x.strip()] if cur_sticks and not auto else [str(q.index) for q in zaps if q.index is not None]
    pref = (cfg.get("stick_nr") or "").strip()
    inp = "".join('<tr><td>%s</td><td><input name="f_%s" size="22" value="%s"></td></tr>' % (html.escape(lbl), k, html.escape(cfg.get(k, dflt) or "")) for k, lbl, dflt in CONF_FIELDS)
    rows = ""
    for n, name, sn, ident in devs:
        role = next((i + 1 for i, c in enumerate(chosen) if c == str(ident)), "")
        opts = '<option value="">nicht benutzen</option>' + "".join('<option value="%d"%s>RTL%d</option>' % (r_, " selected" if role == r_ else "", r_) for r_ in range(1, MAX_STICKS + 1))
        rows += ('<tr><td>%d</td><td>%s</td><td><code>%s</code></td><td>%s</td><td><select name="role_%s">%s</select></td></tr>'
                 % (n, html.escape(name), html.escape(sn or "&ndash;"), "<code>%s</code>" % html.escape(str(ident)), html.escape(str(ident)), opts))
    popt = '<option value="">(keiner)</option>' + "".join('<option value="%s"%s>%s</option>' % (html.escape(str(i)), " selected" if pref == str(i) else "", html.escape(str(i))) for _n, _nm, _sn, i in devs)
    form = ('<form method="post" action="/config/save"><h3>Empfang und Dienst</h3><table style="border:0">%s</table>'
            '<h3>RTL-Sticks</h3><p><label><input type="radio" name="mode" value="auto"%s> beim Start automatisch suchen (bis %d Sticks)</label> &nbsp; bevorzugter RTL1: <select name="pref">%s</select><br>'
            '<label><input type="radio" name="mode" value="list"%s> feste Zuweisung (Rolle je Stick unten; Reihenfolge = RTL1, RTL2 &hellip;)</label></p>'
            '<table><tr><th>Nr</th><th>Stick</th><th>Seriennummer</th><th>Kennung</th><th>Rolle</th></tr>%s</table>'
            '<p><small>Die Kennung ist die Seriennummer, bei doppelten oder rein numerischen Seriennummern die Nummer am USB-Bus (kann sich beim Umstecken &auml;ndern). '
            'Eine eigene Seriennummer l&auml;sst sich mit <code>rtl_eeprom -s &hellip;</code> eintragen.</small></p>'
            '<p><button>Speichern und Dienst neu starten</button> &nbsp; <small>Sicherung der alten dienst.conf liegt daneben (.vor_web_&hellip;). Kein Passwortschutz &ndash; nur im Heimnetz.</small></p></form>'
            % (inp, " checked" if auto else "", MAX_STICKS, popt, "" if auto else " checked", rows or '<tr><td colspan="5"><em>keine RTL-Sticks gefunden (rtl_test)</em></td></tr>'))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Einstellungen &ndash; %s</title>'
            '<style>body{font-family:sans-serif;max-width:60em;margin:.8em auto;padding:0 .8em}td,th{border:1px solid #999;padding:.2em .4em;text-align:left}table{border-collapse:collapse}'
            'button{padding:.45em 1em;font-size:1em}input{font-size:1em}</style><body><h2>&#9881; Einstellungen &ndash; %s</h2><p>%s</p>%s%s</body>'
            % (html.escape(A.title), html.escape(A.title), other_links(host, "config"), ('<p style="color:#c0392b"><b>%s</b></p>' % html.escape(msg)) if msg else "", form))


def config_save(body):
    from urllib.parse import parse_qs
    f = {k: v[0].strip() for k, v in parse_qs(body, keep_blank_values=True).items()}
    up = {}
    for k, lbl, dflt in CONF_FIELDS:
        v = f.get("f_" + k, "")
        if k in ("lo_mhz",):
            try:
                float(v.replace(",", "."))
            except ValueError:
                return "Ung\u00fcltiger Wert: %s" % lbl
            v = v.replace(",", ".")
        elif k in ("ppm", "verstaerkung_db", "multicast_port", "handy_port", "bake_sekunden_je_versuch", "scan_sekunden_je_signal", "stick_frei_nach_sekunden", "cpu_warnung_prozent"):
            try:
                float(v)
            except ValueError:
                return "Ung\u00fcltiger Wert: %s" % lbl
        elif k == "multicast":
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", v):
                return "Multicast-Adresse ung\u00fcltig"
        up[k] = v
    if f.get("mode") == "list":
        roles = sorted((int(v), k[5:]) for k, v in f.items() if k.startswith("role_") and v.isdigit())
        nums = [r for r, _i in roles]
        if not roles or len(set(nums)) != len(nums):
            return "Feste Zuweisung: jede Rolle (RTL1 \u2026) nur einmal, mindestens ein Stick"
        up["sticks"] = ",".join(i for _r, i in roles)
        up["stick_nr"] = roles[0][1]
    else:
        up["sticks"] = "auto"
        up["stick_nr"] = f.get("pref", "")
    conf_write(up)
    return ""


def manual_page(msg="", host=None):
    host = host or _LAST_HOST[0]
    st = settings.get("manuell", {})
    opts = "".join('<option value="%d"%s>%s%s</option>' % (n, " selected" if str(n) == str(st.get("stick", 1)) else "", q.name,
                    " (belegt: %s)" % label(*q.cur) if q.cur else "") for n, q in enumerate(zaps, 1))
    pre = "".join('<button type="button" style="margin-right:.4em" onclick="document.getElementById(\'f\').value=\'%.3f\'">%s &nbsp;%g</button>' % (v, b, v) for v, b in MAN_PRESETS)
    form = ('<form method="post" action="/manuell/set"><table style="border:0"><tr><td>Stick</td><td><select name="stick">%s</select></td></tr>'
            '<tr><td>Frequenz MHz</td><td><input id="f" name="freq" size="10" value="%s"> &nbsp; <small>unter dem LO = direkt, sonst Downlink (10491&ndash;10500)</small><br>%s</td></tr>'
            '<tr><td>Symbolrate kS</td><td><input name="sr" size="8" value="%s"> &nbsp; <small>leer oder &bdquo;auto&ldquo; = datvrx sucht sie selbst (bis etwa 1000 kS)</small></td></tr>'
            '<tr><td>FEC</td><td><b>automatisch</b> <small>(DVB-S und DVB-S2, FEC und Modulation erkennt datvrx selbst; vorgeben geht nicht)</small></td></tr>'
            '<tr><td></td><td><button>Einstellen</button></td></tr></table></form>' % (opts, html.escape(str(st.get("freq", "437.000"))), pre, html.escape(str(st.get("sr", "auto")))))
    rows = ""
    for n, q in enumerate(zaps, 1):
        if not (q.cur and q.manual):
            continue
        r = q.snapshot()
        mod = html.escape(("%s %s %s" % (r.get("mod") or "", r.get("fec") or "", r.get("extra") or "")).strip()) or "&ndash;"
        rows += ('<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td>'
                 '<td><a href="udp://@%s:%d">udp://@%s:%d</a> &middot; <a href="/m/%d">HTTP</a> &middot; <a href="/free/%d?m=1">freigeben</a></td></tr>'
                 % (q.name, "%.3f MHz" % q.cur[0], ("%.3f MS/s erkannt" % r["sr_det"]) if r.get("sr_det") else "suche", "gelockt" if r.get("lock") else "suche",
                    mod, fmt(r.get("esn0")), fmt(r.get("mer")), html.escape(r.get("rufzeichen") or "–"), fmt(r.get("bitrate"), 2), q.mcast, q.port, q.mcast, q.port, n, n))
    tab = ('<table><tr><th>Stick</th><th>Frequenz</th><th>Symbolrate</th><th>Lock</th><th>Modulation / FEC</th><th>Es/N0 dB</th><th>MER dB</th><th>Rufzeichen / Dienst</th><th>Mbit/s</th><th>Strom</th></tr>%s</table>' % rows
           if rows else "<p><em>Kein Stick ist von Hand eingestellt.</em></p>")
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">'
            '<title>QO-100 manuell &ndash; %s</title><style>body{font-family:sans-serif;max-width:70em;margin:1em auto;padding:0 1em}td,th{border:1px solid #999;padding:.25em .5em;text-align:left}'
            'table{border-collapse:collapse}button{padding:.5em 1em;font-size:1em}</style><body><h2>Manuelle Frequenz &ndash; %s</h2><p><a href="/">&laquo; zur&uuml;ck zur Signalliste</a> &nbsp;|&nbsp; %s</p>%s%s'
            '<div class="bar"><span class="mut">Hinweis</span>%s</div>%s</body>'
            % (html.escape(A.title), html.escape(A.title), other_links(host, "manuell,main"), ('<p style="color:#c0392b"><b>%s</b></p>' % html.escape(msg)) if msg else "", form,
               info("Der Stick bleibt auf dieser Frequenz, bis er freigegeben wird; der Autoverteiler fasst ihn nicht an. Die Werte stehen nicht in der Signaltabelle. "
                    "Symbolrate <b>auto</b> sucht datvrx selbst (bis etwa 1000 kS); FEC und Modulation erkennt datvrx immer selbst."), tab))


def api_stick(n, q, host=None):
    """ein Stick fuer die API /api/v1: feste, versionierte Struktur (Aenderungen nur durch neue Version)"""
    m = q.snapshot() if q.cur else None
    hn = (host or "%s:%d" % (A.mcast, A.http))
    sig = None
    if q.cur:
        sig = {"freq_mhz": q.cur[0], "sr_ks_eingestellt": q.cur[1] or None, "sr_ks_erkannt": round(m["sr_det"] * 1000, 1) if m and m.get("sr_det") else None,
               "bake": abs(q.cur[0] - 10491.5) < 0.5, "manuell": bool(q.manual)}
    disp = None
    for conn in DISPLAY_CONNECTORS:                               # haengt dieser Stick an einem HDMI-Monitor des Pi? (MultiPlayer zeigt ihn dann nicht noch einmal)
        cfg = disp_cfg(conn)
        if cfg["stick"] == n:
            c = displays.conns.get(conn) or {}
            disp = {"ausgang": conn, "an": cfg["an"], "verbunden": bool(c.get("verbunden")), "aktiv": bool(cfg["an"] and c.get("verbunden")),
                    "laeuft": conn in displays.proc and displays.proc[conn]["p"].poll() is None}
            break
    return {"nr": n, "name": q.name, "kennung": q.index, "udp": "%s:%d" % (q.mcast, q.port), "display": disp,
            "http_stream": ("http://%s/z%s/%.3f/%d" % (hn, "" if n == 1 else n, q.cur[0], q.cur[1])) if q.cur and not q.manual else (("http://%s/m/%d" % (hn, n)) if q.cur else None),
            "frei": q.cur is None, "fest": bool(q.pin), "zuschauer": len(q.clients), "signal": sig,
            "lock": bool(m and m.get("lock")),
            "empfang": ({"standard": m.get("std"), "modulation": m.get("mod"), "fec": m.get("fec"), "zusatz": m.get("extra"), "es_n0_db": m.get("esn0"), "mer_db": m.get("mer"),
                         "pegel_db_ueber_rauschen": m.get("pegel_db"), "pegel_dbfs": m.get("pegel_dbfs"), "bitrate_mbit_s": m.get("bitrate"), "null_pakete_prozent": m.get("null_pct"),
                         "traeger_ablage_khz": (round(m["carrier_hz"] / 1000.0 - 300.0, 1) if m.get("lock") and m.get("carrier_hz") is not None else None),
                         "tei": m.get("tei"), "cc_fehler": m.get("cc"), "frames_ok": m.get("ok"), "frames_bad": m.get("bad"), "lock_nach_s": m.get("lock_s")} if m else None),
            "dienst": ({"rufzeichen": m.get("rufzeichen"), "provider": m.get("provider")} if m else None),
            "bake": {"status": q.bake["status"], "ablage_khz": q.bake.get("ablage_khz"), "es_n0_db": q.bake.get("esn0"), "lo_korrigiert_mhz": q.bake.get("lo_eff")}}


def api_settings():
    """Schalter des Dienstes als JSON (Verteiler, Auto-Scan, Bake-Rueckfall ...) - Gegenstueck zu den Knoepfen der Webseite"""
    return {"version": 1, "verteiler": {"an": dist.on(), "sticks": {q.name: dist.use(q) for q in zaps}, "ohne_stick": dist.count_open()},
            "auto_scan": bool(settings.get("auto_scan")), "bake_rueckfall": bool(settings.get("bake_rueckfall", True)),
            "bake_rueckfall_stick": int(settings.get("bake_rueckfall_stick", 1)), "bake_start_rtl1": bool(settings.get("bake_start_rtl1", False)),
            "spektrum_an": bool(settings.get("spektrum_an", False)), "spektrum_echtzeit": bool(settings.get("spektrum_echtzeit", False))}


def api_settings_apply(d):
    """Teil-Einstellungen aus JSON uebernehmen: {"verteiler": false} oder {"verteiler": {"an": false, "sticks": {"RTL1": true}}}, "auto_scan", "bake_rueckfall", "bake_start_rtl1", ... (wie die Knoepfe der Webseite)"""
    v = d.get("verteiler")
    if v is not None:
        an, sticks = (v.get("an"), v.get("sticks")) if isinstance(v, dict) else (v, None)
        if an is not None:
            vlog("SCHALTER", "Autoverteiler %s (API/GUI)" % ("AN" if an else "AUS"))
            settings["verteiler"] = bool(an)
            if not an:                                         # Verteiler aus: vom Verteiler belegte Sticks freigeben (wie der Knopf)
                for q in zaps:
                    if q.name in dist.assign:
                        dist.give_back(q)
        if isinstance(sticks, dict):
            settings["verteilen"] = {q.name: bool(sticks.get(q.name, dist.use(q))) for q in zaps}
    for key in ("auto_scan", "bake_rueckfall", "bake_start_rtl1", "spektrum_an", "spektrum_echtzeit"):
        if key in d:
            settings[key] = bool(d[key])
    if "bake_rueckfall_stick" in d:
        try:
            settings["bake_rueckfall_stick"] = min(max(int(d["bake_rueckfall_stick"]), 1), len(zaps))
        except (TypeError, ValueError):
            pass
    save_settings()
    return api_settings()


def prot_page(host=None):
    host = host or _LAST_HOST[0]
    cls = {"NEU": "var(--acc)", "ZUWEISUNG": "var(--link)", "WEG": "var(--mut)", "FREI": "var(--mut)", "OFFEN": "var(--warn)", "FEHLER": "var(--err)", "MANUELL": "#8e44ad", "SCHALTER": "#8e44ad", "BAKE": "#1e90ff", "NEU-VERT": "var(--warn)", "OK": "var(--acc)"}
    rows = ""
    for ln in reversed(list(PROT)[-250:]):
        parts = ln.split(None, 3)
        if len(parts) >= 4:
            rows += '<tr><td style="white-space:nowrap" class="mut">%s %s</td><td style="color:%s;font-weight:600">%s</td><td>%s</td></tr>' % (parts[0][5:], parts[1], cls.get(parts[2], "var(--fg)"), html.escape(parts[2]), html.escape(parts[3]))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5"><title>Protokoll &ndash; %s</title><style></style>'
            '<body><h2>&#128221; Verteil-Protokoll &ndash; %s</h2><p>%s</p><div class="bar"><a class="pill" href="/protokoll.txt">Als Datei herunterladen</a>%s</div>'
            '<table><tr><th>Zeit</th><th>Ereignis</th><th>Text</th></tr>%s</table></body>'
            % (html.escape(A.title), html.escape(A.title), other_links(host, "protokoll"),
               info("<b>Was steht hier?</b> <b>NEU</b>/<b>WEG</b>: Signal im BATC-Spektrum erkannt / verschwunden. <b>ZUWEISUNG</b>: Stick auf ein Signal gestellt (mit Dauer). <b>FREI</b>: Stick frei, weil das Signal weg ist. "
                    "<b>OFFEN</b>: Signal(e) ohne Stick, mit dem Grund je Stick (belegt, festgehalten, Haken aus, Bake nicht ok, &hellip;). <b>MANUELL</b>/<b>SCHALTER</b>: Bedienung von Hand. <b>FEHLER</b>: Abstimmen schlug fehl. "
                    "Die Datei <code>verteiler_protokoll.log</code> neben dem Dienst enth&auml;lt alles (ab 1 MB wird sie rotiert)."), rows))


def api_all(host=None):
    return {"version": 1, "zeit": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "dienst": {"host": re.sub(r"[^A-Za-z0-9_-]", "_", os.uname().nodename) or "pi", "auto_scan": settings.get("auto_scan"), "scan_laeuft": scan.running, "verteiler": {"an": dist.on(), "ohne_stick": dist.count_open(), "frei": dist.n_free(),
                       "sticks": {q.name: dist.use(q) for q in zaps}}, "cpu": cpu.info()},
            "sticks": [api_stick(n, q, host) for n, q in enumerate(zaps, 1)]}


def play_title(f, sr, n=1):
    """Titel eines Playlist-Eintrags: Rufzeichen/Dienst, Frequenz, Datenrate, Modulation, MER (aus der letzten Messung)"""
    lz = live_zap(f, sr)
    r = lz.snapshot() if lz else zap.lookup(f, sr)
    r = r or {}
    parts = [r.get("rufzeichen") or "", "%.3f MHz" % f, "%d kS" % sr, ("%s %s" % (r.get("mod") or "", r.get("fec") or "")).strip(),
             ("MER %.1f dB" % r["mer"]) if r.get("mer") is not None else ""]
    return " \u00b7 ".join(x for x in parts if x) + " (RTL%d)" % n


def vlc_playlist_link(host, stick, f, sr):
    """Android-Intent: Playlist des Sticks in VLC oeffnen, das angeklickte Signal steht vorn (wird zuerst gespielt), die anderen folgen"""
    hn = (host or "localhost").split(":")[0]
    return "intent://%s:%d/qo100%s.m3u?first=%.3f/%d#Intent;scheme=http;type=audio/x-mpegurl;package=org.videolan.vlc;end" % (hn, A.http, "" if stick == 1 else "_rtl%d" % stick, f, sr)


def other_links(host, skip=""):
    """Querverweise zwischen Hauptseite, Handy-Seite und Seite 'manuell' (Host aus der Anfrage, jeweils eigener Port)"""
    hn = (host or "localhost").split(":")[0]
    items = [("main", "http://%s:%d/" % (hn, A.http), "Hauptseite"), ("manuell", "http://%s:%d/manuell" % (hn, A.http), "Manuelle Frequenz")]
    if CONF_PATH:
        items.append(("config", "http://%s:%d/config" % (hn, A.http), "&#9881; Einstellungen"))
    items.append(("display", "http://%s:%d/display" % (hn, A.http), "&#9881;D Displays"))
    items.append(("bake", "http://%s:%d/bake" % (hn, A.http), "&#9881;B Bake"))
    items.append(("hilfe", "http://%s:%d/hilfe" % (hn, A.http), "? Hilfe"))
    items.append(("protokoll", "http://%s:%d/protokoll" % (hn, A.http), "&#128221; Protokoll"))
    items.append(("fehler", "http://%s:%d/fehler" % (hn, A.http), "&#9888; Fehlerbericht"))
    if A.mobile_port:
        items.append(("handy", "http://%s:%d/" % (hn, A.mobile_port), "Handy-Seite"))
    return " ".join('<a class="pill" href="%s">%s</a>' % (u, t) for k, u, t in items if k not in skip.split(","))


def mobile_page(host, stick):
    """schmale Handy-Seite: Signale als grosse Zeilen, Tippen oeffnet den HTTP-Stream in VLC (Android-Intent); Stick waehlbar"""
    stick = stick if 1 <= stick <= len(zaps) else 1
    hostname = host.split(":")[0]
    zz = "" if stick == 1 else str(stick)
    rows = ""
    for f, sr in signals(True):
        lz = live_zap(f, sr)
        r = lz.snapshot() if lz else zap.lookup(f, sr)
        name = html.escape((r or {}).get("rufzeichen") or ("Bake" if abs(f - 10491.5) < 0.5 else "–"))
        mod = html.escape(("%s %s" % ((r or {}).get("mod") or "", (r or {}).get("fec") or "")).strip())
        peg = (r or {}).get("pegel_db")
        url = "%s:%d/z%s/%.3f/%d" % (hostname, A.http, zz, f, sr)
        if abs(f - 10491.5) < 0.5:                       # Bake: nicht in der Playlist, nur der einzelne Stream
            href = "intent://%s#Intent;scheme=http;type=video/mp2t;package=org.videolan.vlc;end" % url
        else:                                            # Signal: Playlist aller Signale, dieses zuerst
            href = vlc_playlist_link(host, stick, f, sr)
        rows += ('<a class="r%s" href="%s"><span class="p">&#9654;</span>'
                 '<span><b>%s</b><br><small>%.3f MHz &middot; %d kS%s%s</small></span></a>'
                 % (" live" if lz else "", href, name, f, sr, (" &middot; " + mod) if mod else "", (" &middot; %.0f dB" % peg) if peg is not None else ""))
    tabs = " ".join('<a class="t%s" href="/?s=%d">%s</a>' % (" on" if n == stick else "", n, q.name) for n, q in enumerate(zaps, 1))
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">'
            '<title>QO-100 Handy &ndash; %s</title><style>body{font-family:sans-serif;margin:0;padding:.5em;background:#fff;color:#111}h1{font-size:1.1em;margin:.2em 0}'
            'a{text-decoration:none;color:inherit}.t{display:inline-block;padding:.3em .8em;border:1px solid #888;border-radius:1em;margin:.1em}.on{background:#273;color:#fff}'
            '.r{display:flex;align-items:center;gap:.8em;padding:.7em;margin:.35em 0;border:1px solid #ccc;border-radius:.5em}.live{background:#e6f4e6}.p{font-size:1.6em;color:#273}'
            'small{color:#555}@media(prefers-color-scheme:dark){body{background:#111;color:#eee}.r{border-color:#444}.live{background:#1d3a24}small{color:#aaa}.t{border-color:#666}}</style>'
            '<body><h1>QO-100 &ndash; %s</h1><div><b>Stick w&auml;hlen:</b> %s</div>%s<p><small>Stick 1&ndash;%d: Ein Tipp auf ein Signal &ouml;ffnet VLC am Handy mit der Playlist aller Signale (RTL%d): das angetippte spielt zuerst, mit &bdquo;n&auml;chstes&ldquo; geht es weiter. Die gr&uuml;ne Zeile l&auml;uft gerade.</small></p>'
            '<p><small>%s</small></p></body>'
            % (html.escape(A.title), html.escape(A.title), tabs, rows or "<p>keine Signale</p>", len(zaps), stick, other_links(host, "handy")))


class HM(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        if u.path != "/":
            b = b"nicht gefunden"; self.send_response(404)
        else:
            try:
                st = int(parse_qs(u.query).get("s", ["1"])[0])
            except ValueError:
                st = 1
            b = mobile_page(self.headers.get("Host") or "localhost", st).encode(); self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *x):
        pass


def start_mobile():
    """schmale Handy-Seite auf eigenem Port (--mobile-port), im Hintergrund"""
    if not A.mobile_port:
        return
    try:
        srv = QuietServer(("0.0.0.0", A.mobile_port), HM)
    except OSError as e:
        print("Handy-Seite: Port %d nicht frei (%s)" % (A.mobile_port, e), flush=True); return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("Handy-Seite: http://0.0.0.0:%d/" % A.mobile_port, flush=True)


class H(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str) and ctype.startswith("text/html"):
            body = re.sub(r"<style>.*?</style>", lambda _m: "<style>%s</style>" % UI_CSS, body, count=1, flags=re.S)       # einheitliches Design fuer alle Seiten
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, to="/"):
        self.send_response(303); self.send_header("Location", to); self.send_header("Content-Length", "0"); self.end_headers()

    def _manual_set(self, body):
        from urllib.parse import parse_qs
        _LAST_HOST[0] = self.headers.get("Host")
        f_ = {k: v[0].strip() for k, v in parse_qs(body).items()}
        try:
            n = int(f_.get("stick", "1")); zp = zaps[n - 1]
            freq = float((f_.get("freq") or "").replace(",", "."))
            srt = (f_.get("sr") or "auto").lower().replace(",", ".")
            sr = 0 if srt in ("", "auto", "0") else int(float(srt))
        except (ValueError, IndexError):
            return self._send(200, "text/html; charset=utf-8", manual_page("Eingabe nicht lesbar (Stick, Frequenz, Symbolrate)"))
        lo_, _p = lo_cfg()
        if not (24.0 <= freq <= 1766.0 or 10400.0 <= freq <= 10700.0):
            return self._send(200, "text/html; charset=utf-8", manual_page("Frequenz au\u00dferhalb des Bereichs (24\u20131766 MHz direkt oder 10400\u201310700 MHz Downlink)"))
        if sr and not (20 <= sr <= 2000):
            return self._send(200, "text/html; charset=utf-8", manual_page("Symbolrate 20\u20132000 kS oder auto"))
        if zp is zap and scan.running:
            return self._send(200, "text/html; charset=utf-8", manual_page("RTL1: Scan l\u00e4uft - erst stoppen oder einen anderen Stick nehmen"))
        settings["manuell"] = {"stick": n, "freq": "%.3f" % freq, "sr": srt or "auto"}
        save_settings()
        dist.drop(zp)
        print("%s  %s manuell -> %.3f MHz %s" % (time.strftime("%H:%M:%S"), zp.name, freq, ("%d kS" % sr) if sr else "SR auto"), flush=True)
        zp.hold = True                                   # waehrend des Abstimmens (mehrere Sekunden) gehoert der Stick dieser Anforderung - sonst greift ihn der Autoverteiler dazwischen
        try:
            ok = zp.tune(freq, sr, manual=True)
            zp.pin = bool(ok)
            zp.last_client = time.time()
        finally:
            zp.hold = False
        if not ok:
            return self._send(200, "text/html; charset=utf-8", manual_page("%s: Stick belegt / kein Empfang" % zp.name))
        return self._redirect("/manuell")

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode(errors="replace") if n else ""
        if path in ("/api/v1/einstellungen", "/api/v1/verteiler"):          # JSON-Steuerung (z.B. durch die leandvb-GUI): Verteiler/Auto-Scan/Bake-Rueckfall schalten
            try:
                d = json.loads(body or "{}")
                if not isinstance(d, dict):
                    d = {"verteiler": d} if path.endswith("verteiler") else {}
                elif path.endswith("verteiler") and "verteiler" not in d:
                    d = {"verteiler": d}
            except ValueError:
                return self._send(400, "application/json", json.dumps({"fehler": "kein gueltiges JSON"}))
            return self._send(200, "application/json; charset=utf-8", json.dumps(api_settings_apply(d), ensure_ascii=False), {"Access-Control-Allow-Origin": "*"})
        if path == "/bake/check/all":
            threading.Thread(target=bake_check_all, args=(self.path.endswith("?force"),), daemon=True).start()
            return self._redirect("/")
        mb = re.match(r"^/bake/check(?:/(\d))?$", path)
        if mb:
            zp = zaps[int(mb.group(1) or 1) - 1] if int(mb.group(1) or 1) <= len(zaps) else zap
            if not (scan.running and zp is zap) and not zp.clients:
                threading.Thread(target=bake_check, args=(zp,), daemon=True).start()
        elif path == "/scan/start":
            ok = scan.start() if bake["status"] == "ok" else False
            if "curl" in (self.headers.get("User-Agent") or "").lower():
                return self._send(200, "text/plain", "Scan gestartet" if ok else ("Scan nicht moeglich: Bake nicht gelockt" if bake["status"] != "ok" else "Scan laeuft schon"))
        elif path in ("/verteiler/on", "/verteiler/off"):
            vlog("SCHALTER", "Autoverteiler %s (Webseite)" % ("AN" if path.endswith("on") else "AUS"))
            settings["verteiler"] = path.endswith("on")
            save_settings()
            if not settings["verteiler"]:                     # Verteiler aus: Sticks, die er belegt hat, freigeben
                for q in zaps:
                    if q.name in dist.assign:
                        dist.give_back(q)
        elif path == "/display/set":
            _LAST_HOST[0] = self.headers.get("Host")
            display_save(body)
            return self._redirect("/display")
        elif path == "/fehler/loeschen":
            fb.clear()
            return self._redirect("/fehler")
        elif path == "/fehler/test":
            fb.add("Testfehler", text="Dies ist ein Testfehler (Knopf auf der Seite Fehlerbericht) - kein echter Fehler.")
            return self._redirect("/fehler")
        elif path == "/spektrum/quelle":
            from urllib.parse import parse_qs
            q_ = (parse_qs(body).get("q") or [""])[0]
            if q_ in ("auto", "batc", "stick"):
                settings["spektrum_quelle"] = q_
                save_settings()
        elif path in ("/spektrum/an", "/spektrum/echtzeit"):
            k = "spektrum_an" if path.endswith("an") else "spektrum_echtzeit"
            settings[k] = not settings.get(k, False)
            save_settings()
        elif path == "/bake/start1":
            settings["bake_start_rtl1"] = not settings.get("bake_start_rtl1", False)
            save_settings()
        elif path == "/bake/set":
            bake_save(body)
            return self._redirect("/bake")
        elif path == "/bake/cache/loeschen":
            settings["bake_cache"] = {}
            save_settings()
            return self._redirect("/bake")
        elif path in ("/display/marq", "/display/marq/restart"):
            marq_save(body)
            if path.endswith("restart"):
                for conn in list(displays.proc):
                    displays.stop(conn, "Einblendung ge\u00e4ndert")
            threading.Thread(target=displays.tick, daemon=True).start()
            return self._redirect("/display")
        elif path.startswith("/display/bake/"):
            conn = path.rsplit("/", 1)[1]
            if conn in DISPLAY_CONNECTORS:
                cfgd = settings.setdefault("displays", {})
                c0 = disp_cfg(conn)
                cfgd[conn] = dict(c0, bake=not c0["bake"])
                for other in DISPLAY_CONNECTORS:
                    cfgd.setdefault(other, disp_cfg(other))
                save_settings()
                threading.Thread(target=displays.tick, daemon=True).start()
            return self._redirect("/display")
        elif path.startswith("/display/start/") or path.startswith("/display/stop/"):
            conn = path.rsplit("/", 1)[1]
            if conn in DISPLAY_CONNECTORS:
                _LAST_HOST[0] = self.headers.get("Host")
                if "stick_" + conn in body:
                    display_save(body)                      # gewaehlter Player/Stick gilt sofort
                if path.startswith("/display/start/"):
                    displays.forced.add(conn)
                else:
                    displays.forced.discard(conn)
                    displays.stop(conn, "von Hand gestoppt")
                threading.Thread(target=displays.tick, daemon=True).start()
            return self._redirect("/display")
        elif path == "/config/save" and CONF_PATH:
            _LAST_HOST[0] = self.headers.get("Host")
            err = config_save(body)
            if err:
                return self._send(200, "text/html; charset=utf-8", config_page(err))
            restart_self()
            return self._send(200, "text/html; charset=utf-8", '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="25;url=/"><body style="font-family:sans-serif"><h3>Gespeichert &ndash; Dienst startet neu &hellip;</h3><p>Die Seite l&auml;dt in 25 s neu (Bake-Pr&uuml;fung braucht danach noch etwa eine Minute).</p></body>')
        elif path == "/manuell/set":
            return self._manual_set(body)
        elif path == "/verteiler/bake":
            settings["bake_rueckfall"] = not settings.get("bake_rueckfall", True)
            save_settings()
        elif path == "/verteilen/neu":
            threading.Thread(target=dist.neu, daemon=True).start()
        elif path == "/verteilen/sticks":
            from urllib.parse import parse_qs
            chk = parse_qs(body)
            settings["verteilen"] = {q.name: (q.name in chk) for q in zaps}
            save_settings()
            for q in zaps:
                if not settings["verteilen"][q.name] and q.name in dist.assign:
                    dist.give_back(q)
        elif path == "/scan/stop":
            scan.stop = True
        elif path in ("/auto/on", "/auto/off"):
            settings["auto_scan"] = path.endswith("on")
            save_settings()
            if not settings["auto_scan"]:
                scan.stop = scan.auto                         # laufenden Auto-Scan abbrechen, manueller Scan laeuft weiter
        else:
            return self._send(404, "text/plain", "nicht gefunden")
        ref = self.headers.get("Referer") or ""
        self.send_response(303)
        self.send_header("Location", "/bake" if re.search(r"/bake/?$", ref.split("?")[0]) else "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        host = self.headers.get("Host") or "localhost:%d" % A.http
        mp = re.match(r"^/qo100(?:_rtl(\d+))?\.m3u$", path.lower())
        if mp and 1 <= int(mp.group(1) or 1) <= len(zaps):                # Playlist je Stick: /qo100.m3u = RTL1, /qo100_rtl2.m3u = RTL2 ...
            n = int(mp.group(1) or 1)
            lst = signals()
            qs = self.path.partition("?")[2]
            mf = re.search(r"(?:^|&)first=([\d.]+)/(\d+)", qs)           # ?first=<MHz>/<kS>: dieses Signal steht vorn und wird zuerst gespielt, danach die uebrigen der Reihe nach
            if mf:
                f0, s0 = float(mf.group(1)), int(mf.group(2))
                k = next((i for i, (ff, ss) in enumerate(lst) if abs(ff - f0) < 0.03 and ss == s0), None)
                if k is not None:
                    lst = lst[k:] + lst[:k]
            body = "#EXTM3U\n" + "".join("#EXTINF:-1,%s\nhttp://%s/z%s/%.3f/%d\n" % (play_title(f, sr, n), host, "" if n == 1 else n, f, sr) for f, sr in lst)
            android = "android" in (self.headers.get("User-Agent") or "").lower()
            # Handy: ohne Dateiname-Kopf (kein Download-Zwang); PC: mit Dateiname, damit der Browser die Datei mit VLC oeffnen kann
            return self._send(200, "audio/x-mpegurl", body, None if android else {"Content-Disposition": 'inline; filename="qo100.m3u"'})
        if path == "/api/v1/sticks" or re.match(r"^/api/v1/stick/\d+$", path):
            data = api_all(host)
            if path != "/api/v1/sticks":
                k = int(path.rsplit("/", 1)[1])
                if not 1 <= k <= len(zaps):
                    return self._send(404, "application/json", json.dumps({"fehler": "Stick %d gibt es nicht" % k}))
                data = {"version": 1, "zeit": data["zeit"], "stick": data["sticks"][k - 1]}
            return self._send(200, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False, default=str), {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"})
        if path == "/status":
            sigs = []
            for f, sr in signals(True):
                r = zap.snapshot() if zap.cur == (f, sr) else zap.lookup(f, sr)
                sigs.append({"freq_mhz": f, "sr_ks": sr, "live": zap.cur == (f, sr), "messung": r})
            return self._send(200, "application/json", json.dumps({
                "aktuell": zap.cur, "zuschauer": len(zap.clients), "signale": sigs,
                "sticks": [{"name": q.name, "index": q.index, "aktuell": q.cur, "fest": q.pin, "zuschauer": len(q.clients), "bake": q.bake,
                            "udp": "%s:%d" % (q.mcast, q.port), "manuell": bool(q.manual), "messung": (q.snapshot() if q.cur else None)} for q in zaps],
                "verteiler": {"an": dist.on(), "sticks": {q.name: dist.use(q) for q in zaps}, "ohne_stick": dist.count_open(), "frei": dist.n_free()},
                "auto_scan": settings.get("auto_scan"), "bake": bake, "cpu": cpu.info(), "scan": {"laeuft": scan.running, "index": scan.idx, "gesamt": scan.total}}, default=str))
        if path == "/":
            return self._send(200, "text/html; charset=utf-8", page(host, "android" in (self.headers.get("User-Agent") or "").lower()))
        mt = re.match(r"^/tune/(\d)/([\d.]+)/(\d+)$", path)
        if mt and 1 <= int(mt.group(1)) <= len(zaps):                    # Stick von Hand auf ein Signal stellen (bleibt dort)
            zp, f, sr = zaps[int(mt.group(1)) - 1], float(mt.group(2)), int(mt.group(3))
            target = next(((ff, ss) for ff, ss in signals(True) if abs(ff - f) < 0.03 and ss == sr), None)
            if not target:
                return self._send(404, "text/plain", "Signal nicht (mehr) vorhanden")
            if zp.bake["status"] != "ok" and abs(target[0] - 10491.5) > 0.5:
                return self._send(503, "text/plain", "%s: Bake nicht gelockt - erst Bake-Pruefung abwarten/Fehler beheben" % zp.name)
            if zp is zap and scan.running:
                return self._send(409, "text/plain", "RTL1: Scan laeuft - erst stoppen oder RTL2 nehmen")
            print("%s  %s von Hand -> %s (%s)" % (time.strftime("%H:%M:%S"), zp.name, label(*target), self.client_address[0]), flush=True)
            dist.drop(zp)
            vlog("MANUELL", "%s von Hand auf %s gestellt (festgehalten, Verteiler fasst ihn nicht an)" % (zp.name, label(*target)))
            zp.hold = True                                   # Autoverteiler darf den Stick waehrend des Abstimmens nicht umstellen
            try:
                ok = zp.tune(*target)
                zp.pin = bool(ok)
                zp.last_client = time.time()
            finally:
                zp.hold = False
            if not ok:
                return self._send(503, "text/plain", "%s: Stick belegt / kein Empfang" % zp.name)
            self.send_response(303); self.send_header("Location", "/"); self.send_header("Content-Length", "0"); self.end_headers()
            return
        if path == "/manuell":
            return self._send(200, "text/html; charset=utf-8", manual_page(host=host))
        if path == "/fehler":
            _LAST_HOST[0] = host
            return self._send(200, "text/html; charset=utf-8", fehler_page(host=host))
        if path == "/fehler.txt":
            return self._send(200, "text/plain; charset=utf-8", error_report(), {"Content-Disposition": 'attachment; filename="fehlerbericht_multizapper_%s.txt"' % time.strftime("%Y%m%d_%H%M%S")})
        if path == "/protokoll":
            _LAST_HOST[0] = host
            return self._send(200, "text/html; charset=utf-8", prot_page(host))
        if path == "/protokoll.txt":
            return self._send(200, "text/plain; charset=utf-8", "\n".join(PROT) + "\n", {"Content-Disposition": 'attachment; filename="verteiler_protokoll_%s.txt"' % time.strftime("%Y%m%d_%H%M%S")})
        if path == "/api/v1/protokoll":
            return self._send(200, "application/json; charset=utf-8", json.dumps({"version": 1, "zeilen": list(PROT)[-200:]}, ensure_ascii=False), {"Access-Control-Allow-Origin": "*"})
        if path == "/hilfe":
            _LAST_HOST[0] = host
            return self._send(200, "text/html; charset=utf-8", help_page(host))
        if path == "/anleitung":
            f = find_manual()
            if not f:
                return self._send(404, "text/plain", "ANLEITUNG.html nicht vorhanden")
            doc = open(f, encoding="utf-8").read().replace("<body>", '<body><p style="margin:0 0 1em"><a class="pill" href="/">&laquo; Hauptseite</a> <a class="pill" href="/hilfe">? Hilfe</a></p>', 1)
            return self._send(200, "text/html; charset=utf-8", doc)
        if path == "/spectrum.json":
            mode = "live" if "mode=live" in self.path else "still"
            return self._send(200, "application/json; charset=utf-8", json.dumps(spec_json(mode)), {"Cache-Control": "no-store"})
        if path == "/bake":
            _LAST_HOST[0] = host
            return self._send(200, "text/html; charset=utf-8", bake_page(host=host))
        if path == "/display":
            _LAST_HOST[0] = host
            return self._send(200, "text/html; charset=utf-8", display_page(host=host))
        if path in ("/api/v1/einstellungen", "/api/v1/verteiler"):
            d = api_settings()
            return self._send(200, "application/json; charset=utf-8", json.dumps(d if path.endswith("einstellungen") else d["verteiler"], ensure_ascii=False), {"Access-Control-Allow-Origin": "*"})
        if path == "/api/v1/displays":
            return self._send(200, "application/json; charset=utf-8", json.dumps(display_state(host), ensure_ascii=False), {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"})
        if path == "/config":
            if not CONF_PATH:
                return self._send(404, "text/plain", "Konfiguration nur im Dienst (dienst.conf)")
            return self._send(200, "text/html; charset=utf-8", config_page(host=host))
        mm = re.match(r"^/m/(\d)$", path)
        if mm and 1 <= int(mm.group(1)) <= len(zaps) and zaps[int(mm.group(1)) - 1].manual and zaps[int(mm.group(1)) - 1].cur:
            zp = zaps[int(mm.group(1)) - 1]
            target = zp.cur
            q = queue.Queue(maxsize=3000)
            zp.clients[q] = target
            try:
                self.send_response(200); self.send_header("Content-Type", "video/mp2t"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
                while zp.cur == target:
                    try:
                        self.wfile.write(q.get(timeout=2)); self.wfile.flush()
                    except queue.Empty:
                        pass
                    zp.last_client = time.time()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                zp.clients.pop(q, None)
                zp.last_client = time.time()
            return
        mf = re.match(r"^/free/(\d)$", path)
        if mf and 1 <= int(mf.group(1)) <= len(zaps):
            zf = zaps[int(mf.group(1)) - 1]
            if zf.cur and abs(zf.cur[0] - 10491.5) > 0.5:
                dist.skip[(zf.cur[0], zf.cur[1])] = time.time() + 600          # von Hand freigegeben: dieses Signal 10 min nicht gleich wieder auf einen Stick legen
            vlog("MANUELL", "%s von Hand freigegeben (%s)%s" % (zf.name, label(*zf.cur) if zf.cur else "-", " - Signal 10 min nicht automatisch wieder vergeben" if zf.cur and abs(zf.cur[0] - 10491.5) > 0.5 else ""))
            dist.drop(zf)
            dist.bake_hold.discard(zf.name)
            zf.release()
            self.send_response(303); self.send_header("Location", "/manuell" if "m=1" in self.path else "/"); self.send_header("Content-Length", "0"); self.end_headers()
            return
        m = re.match(r"^/z(\d?)/([\d.]+)/(\d+)$", path)
        if m and 1 <= int(m.group(1) or 1) <= len(zaps):
            zap_ = zaps[int(m.group(1) or 1) - 1]
            f, sr = float(m.group(2)), int(m.group(3))
            target = next(((ff, ss) for ff, ss in signals(True) if abs(ff - f) < 0.03 and ss == sr), None)
            if not target:
                return self._send(404, "text/plain", "Signal nicht (mehr) vorhanden")
            if zap_.bake["status"] != "ok" and abs(target[0] - 10491.5) > 0.5:
                return self._send(503, "text/plain", "Bake nicht gelockt - erst Bake-Pruefung abwarten/Fehler beheben")
            print("%s  %s Zuschauer %s -> %s" % (time.strftime("%H:%M:%S"), zap_.name, self.client_address[0], label(*target)), flush=True)
            q = queue.Queue(maxsize=3000)
            zap_.clients[q] = target
            zap_.last_client = time.time()
            try:
                if not zap_.tune(*target):
                    return self._send(503, "text/plain", "Stick belegt / kein Empfang")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                while zap_.cur == target:                # Umschalten durch einen anderen Zuschauer beendet diesen Strom
                    try:
                        self.wfile.write(q.get(timeout=2)); self.wfile.flush()
                    except queue.Empty:
                        pass
                    zap_.last_client = time.time()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                zap_.clients.pop(q, None)
                zap_.last_client = time.time()
            return
        self._send(404, "text/plain", "nicht gefunden")

    def log_message(self, *x):
        pass


SPEC = {"live": None, "live_t": 0.0, "frozen": None, "frozen_t": 0.0, "why": "", "sigkey": None}      # BATC-Spektrum: letzter Frame / eingefroren beim letzten neuen Signal


def _on_fft(ws, msg):
    vals = struct.unpack("<%dH" % (len(msg) // 2), msg)
    SPEC["batc_t"] = time.time()                              # BATC lebt (auch wenn gerade der eigene Stick die Quelle ist)
    if SPEC.get("quelle_aktiv") == "stick":
        return
    w.analyze(vals)
    SPEC["live"], SPEC["live_t"] = vals, time.time()


class SpekStick:
    """Spektrum-Ersatz, wenn der BATC-Server ausfaellt (oder von Hand gewaehlt): ein freier RTL-Stick laeuft als Bandscanner (HB9IIU-Server,
    setzt 2,4-MHz-Portionen zu 922 Punkten zusammen, gleiches Format wie BATC). Der Stick ist dabei reserviert (hold) und empfaengt nichts."""

    def __init__(s):
        s.proc, s.zp, s.ws_gen, s.status = None, None, 0, ""
        s.t0 = time.time()
        s.frame_n = 0                 # Zahl der Bilder seit dem Start des Servers
        s.kurz_busy, s.kurz_t, s.kurz_name = False, 0.0, ""      # kurzer Scan laeuft / Zeit des letzten / auf welchem Stick

    @staticmethod
    def verfuegbar():
        return bool(A.spek_server) and os.path.isdir(os.path.expanduser(A.spek_server))

    def wunsch(s):
        q = settings.get("spektrum_quelle", "auto")
        if q == "batc" or not s.verfuegbar():
            return False
        if q == "stick":
            return True
        return time.time() - s.t0 > 25 and time.time() - SPEC.get("batc_t", 0.0) > 20          # auto: BATC seit 20 s stumm

    def frei_stick(s):
        for q in reversed(zaps):                                      # hinterster freier Stick (RTL1 bleibt fuer Scan/Bake)
            if q.rx is None and not q.pin and not q.clients and not q.hold and q.index is not None:
                return q
        for q in reversed(zaps):                                      # sonst einen benutzten Stick ohne Zuschauer freigeben
            if not q.pin and not q.clients and not q.hold and q.index is not None and q is not zaps[0]:
                q.release()
                return q
        return None

    def popen_server(s, q):
        """Spektrum-Server auf Stick q starten; True bei Erfolg"""
        py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spektrum_stick.py")
        cmd = [sys.executable, py, "--server", os.path.expanduser(A.spek_server), "--device", str(q.index), "--lo", str(A.lo or 9540.0),
               "--korr-khz", str(A.spek_korr_khz), "--port", str(A.spek_port)]
        try:
            s.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            s.status = "Start fehlgeschlagen: %s" % e
            return False
        q.hold = True
        s.zp = q
        s.frame_n = 0
        s.ws_gen += 1
        threading.Thread(target=s.ws_loop, args=(s.ws_gen,), daemon=True).start()
        return True

    def start(s):
        q = s.frei_stick()
        if q is None:
            return False
        if not s.popen_server(q):
            return True
        s.status = "Stick-Scan auf %s" % q.name
        SPEC["quelle_aktiv"] = "stick"
        print("%s  Spektrum: BATC ohne Daten - Stick-Scan auf %s gestartet" % (time.strftime("%H:%M:%S"), q.name), flush=True)
        return True

    def kurz_scan(s):
        """Alle Sticks belegt: der hoechste Stick wird kurz fuer EINEN Spektrum-Durchlauf genommen, das Bild bleibt stehen, danach
        bekommt der Stick seine alte Aufgabe zurueck (Frequenz, Symbolrate, Festhalten)"""
        s.kurz_busy = True
        q = next((z for z in reversed(zaps) if z.index is not None), None)
        if q is None:
            s.kurz_busy = False
            return
        prev = (q.cur, q.pin, q.manual, q.hold)
        s.status = "kurzer Scan (%s)" % q.name
        SPEC["quelle_aktiv"] = "stick"
        print("%s  Spektrum: alle Sticks belegt - kurzer Scan auf %s (danach zurueck auf %s)" % (
            time.strftime("%H:%M:%S"), q.name, label(*prev[0]) if prev[0] else "frei"), flush=True)
        ok = False
        try:
            q.hold = True
            q.release()
            if s.popen_server(q):
                t0 = time.time()
                while time.time() - t0 < 60 and s.frame_n < 1 and s.proc is not None and s.proc.poll() is None:
                    time.sleep(0.3)
                ok = s.frame_n >= 1
        finally:
            s.ws_gen += 1
            if s.proc and s.proc.poll() is None:
                s.proc.terminate()
                try:
                    s.proc.wait(5)
                except subprocess.TimeoutExpired:
                    s.proc.kill()
            s.proc, s.zp = None, None
            time.sleep(1.5)                                             # Stick freigeben lassen
            q.hold = prev[3]
            if prev[0]:
                try:
                    if q.tune(prev[0][0], prev[0][1], manual=prev[2]):
                        q.pin = prev[1]
                except Exception as e:
                    print("kurzer Scan: Stick %s nicht zurueckgestellt: %s" % (q.name, e), flush=True)
            s.kurz_t, s.kurz_name = time.time(), q.name
            s.status = "" if ok else "kurzer Scan ohne Bild (%s)" % q.name
            s.kurz_busy = False
            print("%s  Spektrum: kurzer Scan %s, %s wieder %s" % (time.strftime("%H:%M:%S"), "fertig" if ok else "ohne Bild", q.name,
                                                                  label(*prev[0]) if prev[0] else "frei"), flush=True)

    def quelle_txt(s):
        if SPEC.get("quelle_aktiv") != "stick":
            return "BATC"
        if s.kurz_busy:
            return "kurzer Scan (%s)" % (s.zp.name if s.zp else s.kurz_name or "?")
        if s.proc is None and s.kurz_t:
            return "Stick-Scan, Bild von %s (kurzer Scan %s)" % (time.strftime("%H:%M", time.localtime(s.kurz_t)), s.kurz_name)
        return "Stick-Scan (%s)" % (s.zp.name if s.zp else "?")

    def stop(s, grund=""):
        s.ws_gen += 1
        if s.proc and s.proc.poll() is None:
            s.proc.terminate()
            try:
                s.proc.wait(5)
            except subprocess.TimeoutExpired:
                s.proc.kill()
        s.proc = None
        if s.zp is not None:
            s.zp.hold = False
            s.zp = None
        if SPEC.get("quelle_aktiv") == "stick":
            SPEC["quelle_aktiv"] = "batc"
            print("%s  Spektrum: Stick-Scan beendet (%s)" % (time.strftime("%H:%M:%S"), grund or "BATC wieder da"), flush=True)
        s.status = ""

    def ws_loop(s, gen):
        while s.ws_gen == gen:
            def on_msg(ws, msg):
                if s.ws_gen != gen or len(msg) != 1844:
                    return
                vals = struct.unpack("<922H", msg)
                w.analyze(vals)
                SPEC["live"], SPEC["live_t"] = vals, time.time()
                s.frame_n += 1
            try:
                websocket.WebSocketApp("ws://127.0.0.1:%d" % A.spek_port, subprotocols=["fft_fast"], on_message=on_msg).run_forever(ping_interval=15, ping_timeout=8)
            except Exception:
                pass
            for _ in range(20):
                if s.ws_gen != gen:
                    return
                time.sleep(0.1)

    def loop(s):
        while True:
            time.sleep(2)
            try:
                want = s.wunsch()
                if s.kurz_busy:
                    continue
                if want and (s.proc is None):
                    if not s.start():                                   # kein freier Stick: alle 4 belegt -> kurzer Scan auf dem hoechsten Stick
                        if not s.kurz_t or (A.spek_intervall > 0 and time.time() - s.kurz_t > A.spek_intervall):
                            threading.Thread(target=s.kurz_scan, daemon=True).start()
                        else:
                            SPEC["quelle_aktiv"] = "stick"
                elif not want and s.proc is not None:
                    s.stop()
                elif not want and SPEC.get("quelle_aktiv") == "stick":      # BATC wieder da / auf BATC gestellt: Bild wieder von BATC
                    SPEC["quelle_aktiv"] = "batc"
                    s.status = ""
                elif s.proc is not None and s.proc.poll() is not None:
                    s.stop("Server beendet - Stick frei")
                if s.proc is None and SPEC.get("quelle_aktiv") != "stick":
                    SPEC["quelle_aktiv"] = "batc"
                    if not want and settings.get("spektrum_quelle") == "stick" and not s.verfuegbar():
                        s.status = "kein Spektrum-Server installiert (--spek-server)"
            except Exception as e:
                print("spekstick:", e, flush=True)


spek_stick = SpekStick()


def spec_watch():
    """Spektrum 'still': das Bild wird nur eingefroren, wenn ein NEUES Signal kommt (oder eines verschwindet); dazwischen bleibt es stehen"""
    while True:
        time.sleep(1.5)
        try:
            key = tuple(sorted((round(f, 1), sr) for f, sr in signals(True)))
            if SPEC["live"] is not None and (SPEC["frozen"] is None or key != SPEC["sigkey"]):
                old = set(SPEC["sigkey"] or ())
                SPEC["why"] = "Start" if SPEC["frozen"] is None else ("neues Signal" if set(key) - old else "Signal weg")
                SPEC["frozen"], SPEC["frozen_t"], SPEC["sigkey"] = SPEC["live"], time.time(), key
        except Exception as e:
            print("spec_watch:", e, flush=True); fb.add("spec_watch:".strip(":"), exc=e)


def spec_json(mode):
    """Spektrum als JSON fuer die Seite: dB-Werte (922 Bins von 10490.5 MHz, 9 MHz breit), Zeit, erkannte Signale"""
    vals = SPEC["live"] if mode == "live" else (SPEC["frozen"] or SPEC["live"])
    t = SPEC["live_t"] if mode == "live" else (SPEC["frozen_t"] or SPEC["live_t"])
    sigs = []
    for f, sr in signals(True):
        r = zap.lookup(f, sr) or {}
        sigs.append({"f": round(f, 3), "sr": sr, "call": r.get("rufzeichen") or "", "bake": abs(f - 10491.5) < 0.5})
    db = [round(10.0 * math.log10(v if v > 0 else 1), 1) for v in vals] if vals else []
    return {"start": w.START, "bw": w.BW_MHZ, "db": db, "t": time.strftime("%H:%M:%S", time.localtime(t)) if t else "", "mode": mode,
            "why": SPEC["why"] if mode != "live" else "Echtzeit", "sig": sigs,
            "quelle": spek_stick.quelle_txt(), "quelle_status": spek_stick.status, "quelle_modus": settings.get("spektrum_quelle", "auto")}


def ws_loop():
    while True:
        try:
            app = websocket.WebSocketApp("wss://eshail.batc.org.uk/wb/fft", header=["Sec-WebSocket-Protocol: fft_m0dtslivetune"],
                                         on_message=_on_fft)
            app.run_forever(ping_interval=15, ping_timeout=8)
        except Exception:
            pass
        time.sleep(5)


def tick_loop():
    while True:
        try:
            w.tick()
        except Exception as e:
            print("tick:", e, flush=True); fb.add("tick:".strip(":"), exc=e)
        time.sleep(1)


if __name__ == "__main__":
    threading.Thread(target=ws_loop, daemon=True).start()
    threading.Thread(target=tick_loop, daemon=True).start()
    for q in zaps:
        threading.Thread(target=q.idle_watch, daemon=True).start()
    threading.Thread(target=auto.loop, daemon=True).start()
    threading.Thread(target=dist.loop, daemon=True).start()
    threading.Thread(target=spec_watch, daemon=True).start()
    threading.Thread(target=spek_stick.loop, daemon=True).start()
    threading.Thread(target=displays.loop, daemon=True).start()
    threading.Thread(target=bake_loops, daemon=True).start()
    threading.Thread(target=cpu.loop, daemon=True).start()
    start_mobile()
    signal.signal(signal.SIGTERM, lambda *a: (displays.release_all(), spek_stick.stop("Dienst beendet"), [q.release() for q in zaps], os._exit(0)))
    print("Zapper: http://0.0.0.0:%d/  (Playlist /qo100.m3u)  Ordner %s" % (A.http, A.dvx_dir), flush=True)
    try:
        QuietServer(("0.0.0.0", A.http), H).serve_forever()
    except KeyboardInterrupt:
        pass
    [q.release() for q in zaps]
