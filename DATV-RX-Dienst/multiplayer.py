#!/usr/bin/env python3
"""multiplayer.py - MultiPlayer (DG7NDV): startet fuer jeden aktiven Stream des RTL-SDR-datvRX-Dienstes einen Player auf diesem Rechner

Liest alle 2 s die Webseite des Dienstes (http://<Pi>:8554/status), sieht welche Sticks (RTL1, RTL2, ...) ein Signal empfangen und auf welcher
UDP-Adresse (230.0.0.30:1234, ...) der Strom liegt, und startet je Stick einen Player (vlc, mpv oder ffplay).
Aendert sich das Signal (Frequenz, Datenrate, Modulation/FEC) oder das Videoformat (Codec/Aufloesung, per ffprobe), wird der Player gestoppt und
neu gestartet - wie die Datenraten-Resync-Routine der leandvb-GUI (Stop/Start statt Pause/Play). Ist ein Stick frei, endet sein Player.
Baken (10491,5 MHz) werden nur wiedergegeben, wenn der Haken "Baken auch wiedergeben" gesetzt ist (sonst kein Player).

Aufruf:  ./multiplayer.py [--server 192.168.0.102:8554] [--player vlc|mpv|ffplay] [--quad] [--cli]
Fenster: Knopf Quad = Raster auf dem Hauptbildschirm (2x2 bis 4 Sticks, 3x3 bis 9, sonst 4 Spalten, fester Platz je Stick, ueberlappungsfrei);
  Menue 'Fenster' = 5 Lagen (1, 2, 3, 4, 5+ Fenster): Lagen werden wie in der leandvb-GUI selbst gemerkt (Fenster verschieben, 1 Takt stehen lassen), beim
  Start/Neustart eines Players wiederhergestellt, per Menue von Hand speichern/laden/loeschen. Gespeichert in ~/.multiplayer.json.
  Braucht xdotool + wmctrl (X11/XWayland); Windows: ctypes (EnumWindows/MoveWindow), vlc/mpv/ffplay auch aus Program Files.
Einstellungen (Server, Player, Haken) merkt sich das Programm in ~/.multiplayer.json.   --cli: ohne Fenster, Ausgabe im Terminal.
"""
import argparse, collections, concurrent.futures, json, os, re, shutil, signal, socket, subprocess, sys, threading, time, urllib.error, urllib.request

IS_WIN = os.name == "nt"


def find_exe(name):
    """Programm im PATH, unter Windows zusaetzlich an den ueblichen Installationsorten"""
    p = shutil.which(name)
    if p or not IS_WIN:
        return p
    cand = {"vlc": [r"C:\Program Files\VideoLAN\VLC\vlc.exe", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"],
            "mpv": [r"C:\Program Files\mpv\mpv.exe", os.path.expanduser(r"~\scoop\apps\mpv\current\mpv.exe")],
            "ffplay": [r"C:\ffmpeg\bin\ffplay.exe"], "ffprobe": [r"C:\ffmpeg\bin\ffprobe.exe"]}.get(name, [])
    return next((c for c in cand if os.path.isfile(c)), None)


CONF = os.path.expanduser("~/.multiplayer.json")
BEACON = 10491.5


try:
    import fehlerbericht as fb          # Fehlerbericht (Zwischenablage / Datei / Mail)
except Exception:
    fb = None


def load():
    try:
        return json.load(open(CONF))
    except Exception:
        return {}


def save(d):
    try:
        json.dump(d, open(CONF, "w"))
    except OSError:
        pass


def norm_addr(a):
    """HOST oder HOST:PORT -> HOST:PORT (Standardport 8554)"""
    a = (a or "").strip().replace("http://", "").rstrip("/")
    return (a if ":" in a else a + ":8554") if a else ""


def mqtt_discover(broker, wait=3.5):
    """Pis mit RTL-SDR-datvRX-Dienst am MQTT-Broker finden: der Dienst veroeffentlicht (retained) qo100/udp/<Hostname> = {basis, port, host, ip, http}.
    Rueckgabe {HOST:PORT: Hostname}; ohne paho-mqtt oder Broker leer. Ohne 'ip' im Eintrag (aelterer Dienst) wird der Hostname aufgeloest (name, name.local)."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return {}, "paho-mqtt fehlt (pip install paho-mqtt)"
    host, _, port = broker.partition(":")
    got = {}

    def on_msg(cl, ud, msg):
        try:
            d = json.loads(msg.payload.decode())
            if d.get("host"):
                got[d["host"]] = d
        except Exception:
            pass
    try:
        try:
            cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="multiplayer-%d" % os.getpid())
        except AttributeError:
            cl = mqtt.Client(client_id="multiplayer-%d" % os.getpid())
        cl.on_message = on_msg
        cl.connect(host, int(port or 1883), 5)
        cl.subscribe("qo100/udp/+", qos=1)
        cl.loop_start()
        time.sleep(wait)
        cl.loop_stop(); cl.disconnect()
    except Exception as e:
        return {}, "MQTT-Broker %s nicht erreichbar (%s)" % (broker, e)
    out, miss = {}, []
    for h, d in got.items():
        ip = d.get("ip") or ""
        if not ip:
            for nm in (h, h + ".local"):
                try:
                    ip = socket.gethostbyname(nm); break
                except OSError:
                    pass
        if ip:
            out["%s:%s" % (ip, d.get("http") or 8554)] = h
        else:
            miss.append(h)
    return out, ("Pi ohne Adresse (manuell eintragen): " + ", ".join(miss)) if miss else ""


def probe(url, timeout=15):
    """Videoformat (Codec, Breite, Hoehe) per ffprobe aus dem Strom oder None"""
    ffp = find_exe("ffprobe")
    if not ffp:
        return None
    try:
        r = subprocess.run([ffp, "-v", "quiet", "-analyzeduration", "5000000", "-probesize", "1000000", "-show_entries",
                            "stream=codec_type,codec_name,width,height", "-of", "json", url], capture_output=True, timeout=timeout)
        v = [x for x in json.loads(r.stdout.decode(errors="replace") or "{}").get("streams", []) if x.get("codec_type") == "video"]
        return "%s %sx%s" % (v[0].get("codec_name"), v[0].get("width"), v[0].get("height")) if v else None
    except Exception:
        return None


def monitor():
    """Hauptbildschirm (x, y, Breite, Hoehe) per xrandr (Windows: GetSystemMetrics); sonst der erste angeschlossene"""
    if IS_WIN:
        try:
            import ctypes
            u = ctypes.windll.user32
            return 0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        except Exception:
            return None
    try:
        out = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for pat in (r"\S+ connected primary (\d+)x(\d+)\+(\d+)\+(\d+)", r"\S+ connected (\d+)x(\d+)\+(\d+)\+(\d+)"):
        m = re.search(pat, out)
        if m:
            w, h, x, y = map(int, m.groups())
            return x, y, w, h
    return None


def monitor_at(px, py):
    """Bildschirm (x, y, Breite, Hoehe), auf dem der Punkt liegt (xrandr, alle angeschlossenen); sonst None"""
    if IS_WIN:
        return monitor()
    try:
        out = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for m in re.finditer(r"\S+ connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", out):
        w, h, x, y = map(int, m.groups())
        if x <= px < x + w and y <= py < y + h:
            return x, y, w, h
    return None


def wm_windows():
    """Fensterliste per 'wmctrl -lpG' (wie die leandvb-GUI): [{id, pid, x, y, w, h, title}]; None ohne wmctrl. Windows: EnumWindows (ctypes)"""
    if IS_WIN:
        return win_windows()
    if not shutil.which("wmctrl"):
        return None
    try:
        out = subprocess.run(["wmctrl", "-lpG"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    res = []
    for line in out.splitlines():
        f = line.split(None, 8)
        if len(f) >= 8:
            try:
                res.append({"id": f[0], "pid": int(f[2]), "x": int(f[3]), "y": int(f[4]), "w": int(f[5]), "h": int(f[6]), "title": f[8] if len(f) > 8 else ""})
            except ValueError:
                pass
    return res


def win_windows():
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    u = ctypes.windll.user32
    res = []

    def cb(hwnd, _l):
        if u.IsWindowVisible(hwnd):
            pid, rc = wintypes.DWORD(), wintypes.RECT()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            u.GetWindowRect(hwnd, ctypes.byref(rc))
            res.append({"id": hwnd, "pid": pid.value, "x": rc.left, "y": rc.top, "w": rc.right - rc.left, "h": rc.bottom - rc.top, "title": ""})
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return res


def win_move(hwnd, g):
    import ctypes
    u = ctypes.windll.user32
    u.ShowWindow(hwnd, 9)                      # SW_RESTORE (nicht maximiert)
    u.MoveWindow(hwnd, int(g[0]), int(g[1]), int(g[2]), int(g[3]), True)


def close_geo(a, b, tol=8):
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


PLAYERS = (("vlc", "VLC"), ("mpv", "mpv"), ("ffplay", "ffplay"), ("system", "System-Standard (xdg-open / start)"))


# Felder der Einblendung im Player: Schluessel -> (Beschriftung im Menue, Beispiel)
MARQ_FIELDS = (("stick", "Stick (RTL1 …)", "RTL1"), ("rufzeichen", "Rufzeichen / Dienstname", "DL1LQ_Joern"), ("provider", "Provider", "DL1LQ"),
               ("freq", "Frequenz", "10498.758 MHz"), ("sr", "Symbolrate", "333 kS"), ("mod", "Modulation / FEC", "QPSK 2/3"), ("mer", "MER", "MER 7.2 dB"),
               ("esn0", "Es/N0", "Es/N0 7.7 dB"), ("pegel", "Pegel über Rauschen", "Pegel 12 dB"), ("bitrate", "Bitrate", "0.44 Mbit/s"), ("udp", "UDP-Adresse", "230.0.0.30:1234"),
               ("lock", "Lock-Status", "gelockt"))
MARQ_POS = ((8, "unten Mitte"), (4, "oben Mitte"), (5, "oben links"), (6, "oben rechts"), (9, "unten links"), (10, "unten rechts"), (0, "Mitte"))
MARQ_DEFAULT = {"on": True, "fields": ["stick", "rufzeichen", "freq", "sr", "mod", "mer", "esn0"], "sep": "  ·  ", "pos": 4, "size": 22, "opacity": 200, "color": "FFFFFF"}


class Detached:
    """Platzhalter fuer einen vom System geoeffneten Player (xdg-open/start): der Prozess endet sofort, das Fenster gehoert dem Standardprogramm und
    laesst sich von hier nicht schliessen; gilt als 'laeuft', bis der Nutzer ihn stoppt"""
    pid = 0

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    kill = terminate


class Slot:
    """ein Stick = ein Player"""

    def __init__(s, name):
        s.name, s.proc, s.key, s.vid, s.vid_new, s.vid_t, s.probing = name, None, None, None, None, 0.0, False
        s.started = 0.0
        s.msg = ""

    def alive(s):
        return s.proc is not None and s.proc.poll() is None


class Multi:
    def __init__(s, server, player, bake_too, log):
        s.server, s.player, s.bake_too, s.log = server, player, bake_too, log
        s.servers = []                    # weitere Pis (HOST:PORT); s.server = Steuer-Pi (Knoepfe Scan/Verteiler/Bake), dort liegt auch die Webseite
        s.found = {}                      # per MQTT gefundene Pis: HOST:PORT -> Hostname
        s.hosts = {}                      # HOST:PORT -> Hostname (aus der API)
        s.mqtt = ""                       # Broker HOST[:PORT]; leer = keine Suche, nur Konfigurationsdatei/Handeingabe
        s.mqtt_auto = True                # alle per MQTT gefundenen Pis automatisch verwenden
        s.warn = ""
        s.disc_msg = ""
        s.festfenster = False             # Festfenster-Modus: alle Streams als Kacheln in EINEM Fenster (libvlc eingebettet), keine einzelnen Player-Fenster
        s.auto = True
        s.quad = False                    # Raster erzwingen (Quad)
        s.remember = True                 # Fensterlagen selbst merken
        s.layouts = {}                    # "1".."5" (Zahl der Player-Fenster, 5 = 5 und mehr) -> {Stick: [x, y, w, h]}
        s.slots = {}
        s.sticks = []
        s.err = ""
        s.on_change = None
        s.status = {}
        s.stick_players = {}              # Stick -> Player (Menue: Player fuer diesen Stick)
        s.muted = False                   # MuteAll: Ton aller Player aus (auch fuer neu gestartete Player)
        s.show_anyway = set()             # Sticks, die am Pi-Monitor haengen, aber trotzdem hier dargestellt werden sollen (Rechtsklick)
        s.marq = dict(MARQ_DEFAULT)       # Einblendung im Player (Menue "Text im Player")
        s.tile = True                     # Kacheln (Routine der leandvb-GUI): Player ohne Ueberlappung, Hauptfenster frei
        s.vscale = 2.0                    # Fenster hoechstens Videoaufloesung x vscale (0 = ganze Kachel)
        s._tile_sig = None
        s.chrome = 101                    # Hoehe von Menue + Bedienleiste des VLC unter/ueber dem Bild (gemessen: 23 + 78 px)
        s.root = None
        s.borderless = False              # Fenster ohne Rahmen (Menue Player: Randlos), gilt ab dem naechsten Start

    def player_of(s, sl):
        """Player dieses Sticks: von Hand gewaehlt (Zeile), sonst fuer den Stick gemerkt, sonst allgemein"""
        return getattr(sl, "use", None) or s.stick_players.get(sl.name) or s.player

    def cmd(s, sl, udp, title):
        url = "udp://@%s" % udp
        pl = s.player_of(sl)
        if pl == "system":
            return ["cmd", "/c", "start", "", url] if IS_WIN else (["open", url] if sys.platform == "darwin" else ["xdg-open", url])
        if pl == "mpv":
            return ["mpv", url, "--title=%s" % title, "--profile=low-latency", "--untimed"] + (["--no-border"] if s.borderless else [])
        if pl == "ffplay":
            return ["ffplay", "-window_title", title, "-fflags", "nobuffer"] + (["-noborder"] if s.borderless else []) + [url]
        q = s.marq
        mq = (["--sub-source=marq", "--marq-file=%s" % s.marq_file(sl.name), "--marq-position=%d" % q["pos"], "--marq-opacity=%d" % q["opacity"], "--marq-size=%d" % q["size"],
               "--marq-color=0x%s" % q["color"], "--marq-refresh=1000"] if q.get("on") else [])
        return ["vlc"] + mq + (["--qt-minimal-view", "--no-video-deco"] if s.borderless else []) + ["--intf=qt", "--extraintf=", "--demux=ts", "--network-caching=1000", "--avcodec-hw=none", "--autoscale", "--no-qt-video-autoresize", "--vout=%s" % ("any" if IS_WIN else "gl,xcb_xv,any"),
                "--video-title=%s" % title, "--no-qt-recentplay", "--playlist-enqueue", url]

    def marq_file(s, name):
        """Textdatei fuer die VLC-Einblendung (wie die leandvb-GUI: --marq-file, VLC liest sie jede Sekunde neu)"""
        d = os.path.join(os.environ.get("TEMP") or "/tmp", "multiplayer_marq")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "%s.txt" % name)

    def marq_text(s, st):
        """Text der Einblendung nach den Einstellungen (Menue 'Text im Player'): gewaehlte Felder in gewaehlter Reihenfolge"""
        m, cur = st.get("messung") or {}, st.get("aktuell")
        if not cur:
            return st["name"] + "  frei"
        f1 = lambda v, fmt: (fmt % v) if v is not None else ""
        val = {"stick": st["name"], "rufzeichen": m.get("rufzeichen") or "", "provider": m.get("provider") or "", "freq": "%.3f MHz" % cur[0],
               "sr": ("%.0f kS" % (m["sr_det"] * 1000)) if m.get("sr_det") else ("%d kS" % cur[1] if cur[1] else ""),
               "mod": ("%s %s" % (m.get("mod") or "", m.get("fec") or "")).strip(), "mer": f1(m.get("mer"), "MER %.1f dB"), "esn0": f1(m.get("esn0"), "Es/N0 %.1f dB"),
               "pegel": f1(m.get("pegel_db"), "Pegel %.0f dB"), "bitrate": f1(m.get("bitrate"), "%.2f Mbit/s"), "udp": st.get("udp", ""), "lock": "gelockt" if m.get("lock") else "Suche"}
        return s.marq["sep"].join(x for x in (val.get(k, "") for k in s.marq["fields"]) if x)

    def update_marq(s):
        for st in s.sticks:
            try:
                with open(s.marq_file(st["name"]), "w", encoding="utf-8") as f:
                    f.write(s.marq_text(st))
            except OSError:
                pass

    def start(s, sl, udp, title):
        s.update_marq()
        argv = s.cmd(sl, udp, title)
        exe = find_exe(argv[0])
        if not exe:
            sl.msg = "%s nicht gefunden" % s.player_of(sl)
            return
        argv[0] = exe
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WIN else {"start_new_session": True}
        pr = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        sl.proc = Detached() if s.player_of(sl) == "system" else pr
        sl.started = time.time()
        sl.hold, sl.ref, sl.last_geo = 0, None, None
        sl.msg = "Player läuft"
        s.log("%s: %s gestartet (PID %d) udp://@%s" % (sl.name, s.player_of(sl), pr.pid, udp))
        if not (s.tile or s.quad):
            s.place(sl)                                # feste/gemerkte Lage oder freier Platz (free_geo); mit Kacheln ordnet tile_players an

    @staticmethod
    def lkey(n):
        return str(min(max(n, 1), 5))

    def n_alive(s):
        return sum(1 for x in s.slots.values() if x.alive())

    def cell(s, name):
        """Rasterzelle dieses Sticks auf dem Hauptbildschirm (Platz je Stick fest, mindestens 2x2)"""
        mon = s.mon()
        if not mon:
            return None
        names = [st["name"] for st in s.sticks]
        i = names.index(name) if name in names else 0
        n = max(len(names), 4)
        cols = 2 if n <= 4 else 3 if n <= 9 else 4
        rows = -(-n // cols)
        x0, y0, w, h = mon
        top = 36                                     # obere Leiste des Desktops
        cw, ch = w // cols - 60, (h - top) // rows - 60          # etwas kleiner: Fensterrahmen/Versatz des Desktops ueberlappen sonst die Nachbarzelle
        return x0 + (i % cols) * cw, y0 + top + (i // cols) * ch, cw, ch

    def mon(s):
        """Bildschirm, auf dem das MultiPlayer-Fenster selbst steht (dort werden auch die Player platziert); sonst der Hauptbildschirm"""
        r = getattr(s, "root", None)
        try:
            if r is not None and not IS_WIN:
                m = monitor_at(r.winfo_rootx() + r.winfo_width() // 2, r.winfo_rooty() + r.winfo_height() // 2)
                if m:
                    return m
        except Exception:
            pass
        return monitor()

    def geo_for(s, name):
        """Soll-Lage: Quad = Raster; sonst gemerkte Lage fuer die aktuelle Fensterzahl, sonst keine (Player bleibt wo er aufgeht)"""
        if s.quad:
            return s.cell(name)
        g = s.layouts.get(s.lkey(s.n_alive()), {}).get(name)
        return tuple(g) if g else None

    def place(s, sl):
        """Fenster des Players an seine Soll-Lage schieben (wmctrl/xdotool, X11/XWayland); wartet im Hintergrund, bis das Fenster da ist"""
        def work():
            if not IS_WIN and not (shutil.which("xdotool") and shutil.which("wmctrl")):
                sl.msg = "Fenster: xdotool/wmctrl fehlen"; return
            proc = sl.proc
            for _ in range(40):
                if proc is None or proc.poll() is not None:
                    return
                if IS_WIN:
                    ids = [str(w["id"]) for w in (win_windows() or []) if w["pid"] == proc.pid and w["w"] >= 200 and w["h"] >= 120]
                else:
                    r = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", str(proc.pid)], capture_output=True, text=True)
                    ids = [x for x in r.stdout.split() if x]
                if ids:
                    g = s.geo_for(sl.name)
                    lock = None
                    if not g and not s.quad:                   # keine feste/gemerkte Lage: freien Platz auf dem Bildschirm suchen (nie ueber ein anderes Player-Fenster)
                        lock = s.pl_lock
                        lock.acquire()
                        g = s.free_geo(sl)
                    if not g:
                        if lock:
                            lock.release()
                        return
                    for wid in ids:
                        if IS_WIN:
                            win_move(int(wid), g)
                            continue
                        hx = "0x%x" % int(wid)
                        subprocess.run(["wmctrl", "-i", "-r", hx, "-b", "remove,maximized_vert,maximized_horz,fullscreen"], capture_output=True)
                        subprocess.run(["wmctrl", "-i", "-r", hx, "-e", "0,%d,%d,%d,%d" % tuple(g)], capture_output=True)
                    sl.hold = time.time() + 8          # Lesefehler durch Fensterrahmen nicht als Verschieben durch den Nutzer werten
                    sl.ref = None
                    s.log("%s: Fenster nach %d,%d (%dx%d)" % ((sl.name,) + tuple(g)))
                    if lock:
                        time.sleep(0.4)                        # Fenster erst sichtbar verschoben, dann darf der naechste Player suchen
                        lock.release()
                    return
                time.sleep(0.5)
            sl.msg = "Fenster nicht gefunden (Wayland?)"
        threading.Thread(target=work, daemon=True).start()

    pl_lock = threading.Lock()

    def free_geo(s, sl):
        """Freier Platz fuer ein neues Player-Fenster: Raster ueber den Hauptbildschirm, erste Stelle ohne Ueberdeckung anderer Player-Fenster;
        ist nichts frei, gestaffelt (kaskadiert) versetzt. None, wenn Bildschirm oder Fenstergroesse unbekannt"""
        mon = s.mon()
        wins = wm_windows() or []
        pids = s.alive_pids()
        me = next((w for w in wins if sl.proc and w["pid"] == sl.proc.pid and w["w"] >= 200 and w["h"] >= 120), None)
        if not mon or not me:
            return None
        x0, y0, mw, mh = mon
        top = 36
        W, H = min(me["w"], mw // 2 - 20), min(me["h"], (mh - top) // 2 - 20)       # nie groesser als ein Viertel des Bildschirms, damit mindestens 2x2 Fenster nebeneinander passen
        others = [(w["x"], w["y"], w["w"], w["h"]) for w in wins if w["pid"] in pids and w["pid"] != sl.proc.pid and w["w"] >= 100 and w["h"] >= 60]

        def free(x, y):
            return not any(x < ox + ow - 8 and ox < x + W - 8 and y < oy + oh - 8 and oy < y + H - 8 for ox, oy, ow, oh in others)
        for x in range(x0, max(x0 + mw - W, x0) + 1, 30):                    # spaltenweise: oben links, darunter (unten links), dann die naechste Spalte
            for y in range(y0 + top, max(y0 + mh - H, y0 + top) + 1, 30):
                if free(x, y):
                    return (x, y, W, H)
        k = len(others)
        return (x0 + 40 * (k % 8), y0 + top + 40 * (k % 8), W, H)

    def place_geo(s, sl, g):
        """Fenster dieses Players an eine bestimmte Lage schieben (Menue: Lage laden)"""
        old = s.geo_for
        s.geo_for = lambda name, g=g: tuple(g)
        try:
            s.place(sl)
        finally:
            threading.Timer(1.0, lambda: setattr(s, "geo_for", old)).start()

    def alive_pids(s):
        return {sl.proc.pid for sl in s.slots.values() if sl.alive() and getattr(sl.proc, "pid", 0)}

    def show_all(s):
        """Knopf ShowAll: alle Player-Fenster in den Vordergrund holen, ohne dem Player die Tastatur zu geben (xdotool windowraise; sonst wmctrl)"""
        pids, n = s.alive_pids(), 0
        if IS_WIN:
            return 0
        wins = wm_windows() or []
        for w in wins:
            if w["pid"] in pids and w["w"] >= 100 and w["h"] >= 60:
                try:
                    if shutil.which("xdotool"):
                        subprocess.run(["xdotool", "windowraise", str(int(w["id"], 16))], capture_output=True, timeout=3)
                    elif shutil.which("wmctrl"):
                        subprocess.run(["wmctrl", "-i", "-a", w["id"]], capture_output=True, timeout=3)
                    n += 1
                except (OSError, subprocess.TimeoutExpired, ValueError):
                    pass
        s.log("ShowAll: %d Player-Fenster nach vorn" % n)
        return n

    def apply_mute(s):
        """Ton aller laufenden Player stumm/laut (PulseAudio/PipeWire: pactl set-sink-input-mute); neu gestartete Player folgen dem Zustand (s.muted)"""
        if IS_WIN or not shutil.which("pactl"):
            return None
        pids = s.alive_pids()
        if not pids:
            return 0
        try:
            out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=4, env=dict(os.environ, LC_ALL="C")).stdout      # englische Feldnamen (auf deutschen Systemen heisst es sonst "Ziel-Eingabe")
        except (OSError, subprocess.TimeoutExpired):
            return None
        n = 0
        for blk in out.split("Sink Input #")[1:]:
            idx = blk.split("\n", 1)[0].strip()
            m = re.search(r'application\.process\.id = "(\d+)"', blk)
            mute = re.search(r"(?:Mute|Stumm): (\w+)", blk)
            if m and int(m.group(1)) in pids and idx.isdigit():
                if mute and ((mute.group(1) in ("yes", "ja")) != s.muted):
                    subprocess.run(["pactl", "set-sink-input-mute", idx, "1" if s.muted else "0"], capture_output=True, timeout=3)
                n += 1
        return n

    def toggle_mute(s):
        s.muted = not s.muted
        n = s.apply_mute()
        s.log("MuteAll: %s (%s Player)" % ("Ton AUS" if s.muted else "Ton an", "?" if n is None else n))
        return s.muted

    def arrange(s):
        if (s.tile or s.quad) and not IS_WIN:
            threading.Thread(target=lambda: s.tile_players(force=True), daemon=True).start()
            return
        for sl in s.slots.values():
            if sl.alive():
                s.place(sl)

    # ---- Kacheln / Quad (Routinen aus der leandvb-GUI: _tile_best, _tile_free_areas, _tile_players) ----
    @staticmethod
    def tile_best(area, n, min_cols=1):
        """(Spalten, Zeilen, Kachelbreite fuer 16:9) mit groesstmoeglichem Videobild fuer n Fenster in area"""
        best = None
        for cols in range(max(1, min(min_cols, n)), n + 1):
            rows = -(-n // cols)
            cw, ch = area["w"] / float(cols), area["h"] / float(rows)
            fit = min(cw, (ch - 40) * 16 / 9.0)
            if best is None or fit > best[2] + 1e-6:
                best = (cols, rows, fit)
        return best

    @staticmethod
    def free_areas(mon, mg):
        """freie Rechtecke des Bildschirms ohne das Hauptfenster (links/rechts/oben/unten davon) - bzw. der ganze Bildschirm"""
        if not mg:
            return [dict(mon)]
        mx0, my0, mx1, my1 = mon["x"], mon["y"], mon["x"] + mon["w"], mon["y"] + mon["h"]
        gx0, gy0 = max(mx0, mg["x"] - 8), max(my0, mg["y"] - 40)
        gx1, gy1 = min(mx1, mg["x"] + mg["w"] + 8), min(my1, mg["y"] + mg["h"] + 8)
        if gx0 >= gx1 or gy0 >= gy1:
            return [dict(mon)]                                   # Hauptfenster nicht auf diesem Bildschirm
        areas = [{"x": mx0, "y": my0, "w": gx0 - mx0, "h": mon["h"]}, {"x": gx1, "y": my0, "w": mx1 - gx1, "h": mon["h"]},
                 {"x": mx0, "y": my0, "w": mon["w"], "h": gy0 - my0}, {"x": mx0, "y": gy1, "w": mon["w"], "h": my1 - gy1}]
        return [a for a in areas if a["w"] >= 200 and a["h"] >= 150]

    def main_window(s, wins):
        """eigenes Hauptfenster in der wmctrl-Liste (per Fenster-ID, sonst PID)"""
        ids = set()
        try:
            ids.add(int(s.root.winfo_id()))
            ids.add(int(str(s.root.wm_frame()), 16))
        except Exception:
            pass
        me = [w for w in wins if int(w["id"], 16) in ids] or [w for w in wins if w["pid"] == os.getpid() and w["w"] >= 200]
        return max(me, key=lambda w: w["w"] * w["h"]) if me else None

    def tile_players(s, force=False):
        """alle Player-Fenster ohne Ueberlappung auf dem Bildschirm des MultiPlayers anordnen (Hauptfenster frei); Quad: Hauptfenster rechts unten,
        Player in festen 2x2-Kacheln. Neu angeordnet wird, wenn ein Fenster dazukommt/wegfaellt. Liefert die Zahl verschobener Fenster"""
        if IS_WIN or not shutil.which("wmctrl"):
            return 0
        wins = wm_windows() or []
        m0 = s.mon()
        if not m0:
            return 0
        mon = {"x": m0[0], "y": m0[1], "w": m0[2], "h": m0[3]}
        pw = []
        for sl in s.slots.values():
            if sl.alive() and getattr(sl.proc, "pid", 0):
                c = [w for w in wins if w["pid"] == sl.proc.pid and w["w"] >= 100 and w["h"] >= 60]
                if c:
                    pw.append((sl, max(c, key=lambda w: w["w"] * w["h"])))
        pw.sort(key=lambda x: x[0].name)
        bl = bool(s.borderless)
        sig = (bool(s.quad), bl, tuple(m0), tuple(w["id"] for _sl, w in pw), s.vscale, s.chrome, tuple(sl.vid for sl, _w in pw))
        if not pw or (sig == s._tile_sig and not force):
            s._tile_sig = sig if pw else None
            return 0
        main = s.main_window(wins) if s.root is not None else None
        mg = {"x": main["x"], "y": main["y"], "w": main["w"], "h": main["h"]} if main else None
        if s.quad and main:
            nx, ny = mon["x"] + mon["w"] - main["w"] - 4, mon["y"] + mon["h"] - main["h"] - 4
            if abs(nx - main["x"]) > 4 or abs(ny - main["y"]) > 4:
                subprocess.run(["wmctrl", "-i", "-r", main["id"], "-e", "0,%d,%d,%d,%d" % (max(mon["x"], nx), max(mon["y"], ny), main["w"], main["h"])], capture_output=True)
            mg = {"x": max(mon["x"], nx), "y": max(mon["y"], ny), "w": main["w"], "h": main["h"]}
        areas = s.free_areas(mon, mg) or [dict(mon)]
        n = len(pw)
        if s.quad:
            def qgrid(a):
                if n <= 4:
                    c, r = (2, 2) if n > 2 else (2, 1) if n == 2 else (1, 1)
                    return c, r, min(a["w"] / float(c), (a["h"] / float(r) - 40) * 16 / 9.0)
                return s.tile_best(a, n, min_cols=2)
            (cols, rows, _f), area = max(((qgrid(a), a) for a in areas), key=lambda x: x[0][2])
        else:
            (cols, rows, _f), area = max(((s.tile_best(a, n), a) for a in areas), key=lambda x: x[0][2])
        cw, ch = area["w"] // cols, area["h"] // rows
        for i, (sl, w) in enumerate(pw):
            geo = (area["x"] + (i % cols) * cw, area["y"] + (i // cols) * ch, cw - (0 if bl else 6), ch - (0 if bl else 40))
            mv = re.search(r"(\d+)\s*[x\u00d7]\s*(\d+)", sl.vid or "")
            vw, vh = (int(mv.group(1)), int(mv.group(2))) if mv else (1280, 720)          # Videoformat (unbekannt: 16:9)
            chrome = 0 if bl else s.chrome                          # Menue/Bedienleiste des Players unter dem Bild
            sc = min(geo[2] / float(vw), (geo[3] - chrome) / float(vh))                      # Fenster genau im Bildformat: keine schwarzen Raender
            if s.vscale > 0 and not s.quad:
                sc = min(sc, s.vscale)                              # nicht groesser als Videoaufloesung x Vergroesserung
            geo = (geo[0], geo[1], max(160, int(vw * sc)), max(90, int(vh * sc) + chrome))
            for args in (["-b", "remove,maximized_vert,maximized_horz,fullscreen"], ["-e", "0,%d,%d,%d,%d" % geo]):
                subprocess.run(["wmctrl", "-i", "-r", w["id"]] + args, capture_output=True)
            sl.hold = time.time() + 8
            sl.ref = None
        s._tile_sig = sig
        s.log("Fenster-Anordnung (%s): %d Player auf Bildschirm %dx%d+%d+%d (%d x %d Kacheln), ohne Ueberlappung, Hauptfenster frei" % (
            "Quad" if s.quad else "Kacheln", n, mon["w"], mon["h"], mon["x"], mon["y"], cols, rows))
        return n

    def autosave(s):
        """Lage der Player-Fenster merken (Routine wie in der leandvb-GUI): eine Lage, die der Nutzer geaendert hat und die einen Takt stehen
        bleibt, wird fuer die aktuelle Fensterzahl (1, 2, 3, 4 = Quad, 5+) und den Stick gespeichert; nicht im Quad-Raster"""
        if not s.remember or s.quad or s.tile:
            return
        wins = wm_windows()
        if not wins:
            return
        key, changed = s.lkey(s.n_alive()), False
        for sl in s.slots.values():
            if not sl.alive() or time.time() < getattr(sl, "hold", 0):
                continue
            w = next((x for x in wins if x["pid"] == sl.proc.pid and x["w"] >= 200 and x["h"] >= 120), None)
            if not w:
                continue
            geo = (w["x"], w["y"], w["w"], w["h"])
            last, sl.last_geo = getattr(sl, "last_geo", None), geo
            if getattr(sl, "ref", None) is None:
                sl.ref = geo                               # Lage nach dem Platzieren = Bezug
                continue
            if last is None or not close_geo(last, geo, 2) or close_geo(sl.ref, geo):
                continue                                   # noch in Bewegung oder nicht verschoben
            s.layouts.setdefault(key, {})[sl.name] = list(geo)
            sl.ref = geo
            s.log("%s: Lage gemerkt (%s Fenster) %d,%d %dx%d" % ((sl.name, key) + geo))
            changed = True
        if changed and s.on_change:
            s.on_change()

    def save_now(s, key):
        """alle laufenden Fenster jetzt als Lage fuer 'key' speichern"""
        wins = wm_windows() or []
        got = {}
        for sl in s.slots.values():
            if sl.alive():
                w = next((x for x in wins if x["pid"] == sl.proc.pid and x["w"] >= 100), None)
                if w:
                    got[sl.name] = [w["x"], w["y"], w["w"], w["h"]]
        if got:
            s.layouts[str(key)] = got
            s.log("Lage '%s' gespeichert (%d Fenster)" % (key, len(got)))
            if s.on_change:
                s.on_change()
        return len(got)

    def stop(s, sl, why=""):
        if isinstance(sl.proc, Detached):
            s.log("%s: System-Player läßt sich von hier nicht schließen (Fenster selbst schließen)" % sl.name)
        elif sl.alive():
            try:
                if IS_WIN:
                    sl.proc.terminate()
                else:
                    os.killpg(sl.proc.pid, signal.SIGTERM)
                sl.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    sl.proc.kill() if IS_WIN else os.killpg(sl.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            except OSError:
                pass
            s.log("%s: Player gestoppt%s" % (sl.name, (" (%s)" % why) if why else ""))
        sl.proc, sl.key, sl.vid = None, None, None
        sl.msg = why or "kein Signal"

    @staticmethod
    def from_api(x):
        """API-Eintrag in die interne Form (aktuell/messung) uebersetzen"""
        sg, em, dn = x.get("signal"), x.get("empfang") or {}, x.get("dienst") or {}
        meas = None
        if sg:
            meas = {"lock": x.get("lock"), "mod": em.get("modulation"), "fec": em.get("fec"), "std": em.get("standard"), "esn0": em.get("es_n0_db"), "mer": em.get("mer_db"),
                    "sr_det": (sg["sr_ks_erkannt"] / 1000.0) if sg.get("sr_ks_erkannt") else None, "rufzeichen": dn.get("rufzeichen"), "provider": dn.get("provider"),
                    "bitrate": em.get("bitrate_mbit_s"), "pegel_db": em.get("pegel_db_ueber_rauschen"), "tei": em.get("tei"), "cc": em.get("cc_fehler")}
        return {"name": x["name"], "udp": x["udp"], "aktuell": [sg["freq_mhz"], sg.get("sr_ks_eingestellt") or 0] if sg else None, "fest": x.get("fest"),
                "messung": meas, "manuell": bool(sg and sg.get("manuell")), "bake": x.get("bake", {}), "display": x.get("display")}

    def post(s, path, data=""):
        """Steuerbefehl an den Dienst (wie die Knoepfe der Webseite); Rueckgabe True bei Erfolg"""
        def work():
            try:
                req = urllib.request.Request("http://%s%s" % (s.server, path), data=data.encode(), method="POST")
                urllib.request.urlopen(req, timeout=30).read()
                s.log("Dienst: %s %s" % (path, data))
            except Exception as e:
                s.log("Dienst: %s fehlgeschlagen (%s)" % (path, getattr(e, "reason", e)))
        threading.Thread(target=work, daemon=True).start()

    def force_start(s, name, player=None):
        """Klick auf eine Zeile: Player fuer diesen Stick sofort starten (auch Bake / ohne Lock / Automatik aus)"""
        if s.festfenster:
            return
        st = next((x for x in s.sticks if x["name"] == name), None)
        if not st:
            return
        sl = s.slots.setdefault(name, Slot(name))
        sl.manual_off = False
        if player:
            if player != s.player_of(sl) and sl.alive():        # anderer Player gewuenscht: laufenden zuerst beenden
                s.stop(sl, "Player gewechselt")
            sl.use = player
        if sl.alive():
            return
        cur = st.get("aktuell")
        s.start(sl, st["udp"], "%s%s" % (name, ("  %.3f MHz" % cur[0]) if cur else ""))
        sl.forced = True
        sl.key, sl.vid, sl.vid_new, sl.vid_t = None, None, None, time.time()

    def force_stop(s, name):
        sl = s.slots.get(name)
        if sl and sl.alive():
            s.stop(sl, "von Hand gestoppt")
            st = next((x for x in s.sticks if x["name"] == name), {})
            sl.forced, sl.manual_off, sl.off_key, sl.use = False, True, st.get("aktuell"), None

    def fetch_one(s, addr):
        """ein Pi: (Sticks, Status, Hostname)"""
        try:                                              # neue API /api/v1/sticks (feste Struktur), sonst die aeltere /status
            with urllib.request.urlopen("http://%s/api/v1/sticks" % addr, timeout=4) as r:
                api = json.load(r)
            d = api.get("dienst", {})
            return ([s.from_api(x) for x in api.get("sticks", [])],
                    {"auto_scan": d.get("auto_scan"), "scan": {"laeuft": d.get("scan_laeuft")}, "verteiler": d.get("verteiler", {}), "cpu": d.get("cpu", {})}, d.get("host") or "")
        except urllib.error.HTTPError:
            with urllib.request.urlopen("http://%s/status" % addr, timeout=4) as r:
                stt = json.load(r)
            return stt.get("sticks", []), stt, ""

    def pis(s):
        """alle verwendeten Pis, Steuer-Pi zuerst"""
        return [s.server] + [a for a in s.servers if a and a != s.server]

    def fetch(s):
        addrs = s.pis()
        multi = len(addrs) > 1
        res = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(addrs)) as ex:         # alle Pis gleichzeitig abfragen (ein toter Pi bremst die anderen nicht)
            futs = {a: ex.submit(s.fetch_one, a) for a in addrs}
            errs = []
            for a, f in futs.items():
                try:
                    res[a] = f.result()
                except Exception as e:
                    errs.append("%s: %s" % (a, getattr(e, "reason", e)))
        if not res:
            s.err = "Dienst nicht erreichbar (%s)" % "; ".join(errs)
            return False
        sticks, status = [], None
        for a in addrs:
            if a not in res:
                continue
            st, stt, host = res[a]
            host = host or s.found.get(a) or s.hosts.get(a) or a.split(":")[0]
            s.hosts[a] = host
            for x in st:
                x["_server"], x["_name0"] = a, x["name"]
                if multi:
                    x["name"] = "%s@%s" % (x["name"], host)               # mehrere Pis: Stickname eindeutig machen (RTL1@datv-rx)
            sticks += st
            if a == s.server or status is None:
                status = stt
        s.sticks, s.status = sticks, status or {}
        s.err = ""
        s.warn = ("nicht erreichbar: " + "; ".join(errs)) if errs else ""
        return True

    def discover(s):
        """Pis per MQTT suchen (Hintergrund); neue werden bei mqtt_auto sofort verwendet"""
        if not s.mqtt:
            return
        found, msg = mqtt_discover(s.mqtt)
        s.disc_msg = msg
        if found:
            for a, h in found.items():
                if a not in s.found:
                    s.log("MQTT: Pi %s gefunden (%s)" % (h, a))
            s.found.update(found)
            if s.mqtt_auto:
                for a in found:
                    if a != s.server and a not in s.servers:
                        s.servers.append(a)
        if msg:
            s.log(msg)

    def _vid(s, sl, udp):
        """Videoformat im Hintergrund pruefen (alle 15 s, solange der Player laeuft)"""
        def work():
            sl.vid_new = probe("udp://%s" % udp)
            sl.vid_t = time.time()
            sl.probing = False
        sl.probing = True
        threading.Thread(target=work, daemon=True).start()

    def step(s):
        if not s.fetch():
            return
        s.update_marq()                                  # Einblendung (Rufzeichen, Frequenz, ...) aktuell halten
        if s.festfenster:                                # die Kacheln des Festfensters spielen selbst; hier keine externen Player
            return
        for st in s.sticks:
            sl = s.slots.setdefault(st["name"], Slot(st["name"]))
            m, cur, udp = st.get("messung") or {}, st.get("aktuell"), st["udp"]
            if getattr(sl, "forced", False):
                if sl.alive():
                    sl.msg = "Player l\u00e4uft (von Hand)"
                    continue
                sl.forced = False
            if getattr(sl, "manual_off", False):                     # von Hand gestoppt: bis das Signal wechselt oder der Stick frei wird nicht neu starten
                if not cur or cur != getattr(sl, "off_key", None):
                    sl.manual_off = False
                else:
                    sl.msg = "von Hand gestoppt (Klick = starten)"
                    continue
            dsp = st.get("display") or {}
            if dsp.get("aktiv") and st["name"] not in s.show_anyway:      # dem Monitor des Pi zugewiesen: hier nicht noch einmal darstellen (Rechtsklick: trotzdem)
                if sl.alive():
                    s.stop(sl, "am Pi-Monitor")
                sl.msg = ("am Pi-Monitor %s (frei)" if not cur else "am Pi-Monitor %s - nicht dargestellt") % dsp.get("ausgang", "") + " \u2013 Rechtsklick: trotzdem darstellen"
                continue
            is_bake = bool(cur) and abs(cur[0] - BEACON) < 0.5
            wish = bool(s.auto and cur and (s.bake_too or not is_bake))
            if wish and not m.get("lock") and sl.alive():            # Lock kurz weg (Nachregeln): Player 10 s behalten
                sl.nolock = getattr(sl, "nolock", None) or time.time()
                want = time.time() - sl.nolock < 10
            else:
                sl.nolock = None
                want = bool(wish and m.get("lock"))
            if cur and is_bake and not s.bake_too:
                sl.msg = "Bake - nicht wiedergegeben"
            if not want:
                if sl.alive():
                    s.stop(sl, "Stick frei" if not cur else ("Bake - nicht wiedergegeben" if is_bake and not s.bake_too else "kein Lock"))
                elif not cur:
                    sl.msg = "frei"
                continue
            key = (round(cur[0], 2), cur[1], m.get("sr_det"), m.get("mod"), m.get("fec"), m.get("std"))
            if sl.alive() and sl.key != key:
                if isinstance(sl.proc, Detached):
                    sl.key = key                      # System-Player: kein Neustart (zweites Fenster vermeiden)
                else:
                    s.stop(sl, "Signal/Datenrate geändert")
                    time.sleep(0.4)
            if not sl.alive():
                s.start(sl, udp, "%s  %.3f MHz" % (st["name"], cur[0]))
                sl.key, sl.vid, sl.vid_new, sl.vid_t = key, None, None, time.time()
            v, sl.vid_new = sl.vid_new, None
            if v:                                           # neues Ergebnis des Videoformat-Tests
                if sl.vid and v != sl.vid:
                    s.stop(sl, "Videoformat ge\u00e4ndert (%s \u2192 %s)" % (sl.vid, v))
                    time.sleep(0.4)
                    s.start(sl, udp, "%s  %.3f MHz" % (st["name"], cur[0]))
                    sl.key, sl.vid_t = key, time.time()
                sl.vid = v
            elif not sl.probing and time.time() - sl.started > 4 and time.time() - sl.vid_t > (5 if sl.vid is None else 15):
                s._vid(sl, udp)
            sl.msg = "Player l\u00e4uft" + ((" \u2013 " + sl.vid) if sl.vid else "")
        if (s.tile or s.quad) and not IS_WIN:
            s.tile_players()
        s.autosave()
        if s.muted:
            s.apply_mute()

    def stop_all(s):
        for sl in s.slots.values():
            s.stop(sl, "beendet")

    def summary(s):
        n = len(s.sticks)
        ips = ", ".join(st["udp"] for st in s.sticks)
        if s.err:
            return s.err
        if not n:
            return "keine Sticks gefunden"
        np_ = len(s.pis())
        return "%s%d Stick%s gefunden mit IPs %s – %s%s" % (("%d Pis, " % np_) if np_ > 1 else "", n, "" if n == 1 else "s", ips, "Player starten" if s.auto else "Automatik aus",
                                                           ("  (%s)" % s.warn) if s.warn else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    c = load()
    ap.add_argument("--server", default=c.get("server", "192.168.0.102:8554"), help="Dienst HOST:PORT (Standard 192.168.0.102:8554)")
    ap.add_argument("--also", action="append", default=[], metavar="HOST[:PORT]", help="weiteren Pi dazunehmen (mehrfach moeglich; wird in ~/.multiplayer.json gemerkt)")
    ap.add_argument("--mqtt", default=c.get("mqtt", ""), metavar="HOST[:PORT]", help="MQTT-Broker: Pis mit Dienst automatisch finden (qo100/udp/+), z.B. 192.168.0.10; ohne = nur Konfigurationsdatei/Handeingabe")
    ap.add_argument("--no-mqtt", action="store_true", help="keine MQTT-Suche (auch wenn in der Konfiguration ein Broker steht)")
    ap.add_argument("--player", default=c.get("player", "vlc"), choices=("vlc", "mpv", "ffplay", "system"))
    ap.add_argument("--bake", action="store_true", default=c.get("bake", False), help="Baken auch wiedergeben")
    ap.add_argument("--quad", action="store_true", default=c.get("quad", False), help="Player im Raster (Quad) anordnen, ueberlappungsfrei")
    ap.add_argument("--cli", action="store_true", help="ohne Fenster")
    A = ap.parse_args()
    A.server = norm_addr(A.server)
    A.mqtt = "" if A.no_mqtt else A.mqtt.strip()

    def setup_pis(m):
        """weitere Pis: Konfigurationsdatei + --also; MQTT-Suche im Hintergrund"""
        m.servers = []
        for a in list(c.get("servers", [])) + A.also:
            a = norm_addr(a)
            if a and a != m.server and a not in m.servers:
                m.servers.append(a)
        m.mqtt, m.mqtt_auto = A.mqtt, bool(c.get("mqtt_auto", True))
        m.found = dict(c.get("found", {}))
        if m.mqtt:
            threading.Thread(target=m.discover, daemon=True).start()
    if A.cli:
        m = Multi(A.server, A.player, A.bake, lambda t: print("%s  %s" % (time.strftime("%H:%M:%S"), t), flush=True))
        setup_pis(m)
        m.quad = A.quad
        m.layouts = c.get("layouts", {})
        m.stick_players = dict(c.get("players", {}))
        m.marq = dict(MARQ_DEFAULT, **c.get("marq", {}))
        m.borderless = bool(c.get("randlos", False))
        m.tile = bool(c.get("kacheln", True)); m.vscale = float(c.get("vscale", 2.0))
        m.show_anyway = set(c.get("trotzdem", []))
        m.on_change = lambda: save(dict(load(), layouts=m.layouts))
        t_disc = [time.time()]
        signal.signal(signal.SIGTERM, lambda *a: (m.stop_all(), os._exit(0)))
        try:
            last = ""
            while True:
                if m.mqtt and time.time() - t_disc[0] > 60:
                    t_disc[0] = time.time(); threading.Thread(target=m.discover, daemon=True).start()
                m.step()
                if m.summary() != last:
                    last = m.summary(); print("%s  %s" % (time.strftime("%H:%M:%S"), last), flush=True)
                time.sleep(2)
        except KeyboardInterrupt:
            m.stop_all()
        return
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk(className="Multiplayer")                       # WM_CLASS passend zum Starter (StartupWMClass)
    try:
        root.iconphoto(True, tk.PhotoImage(file=os.path.join(os.path.dirname(os.path.abspath(__file__)), "multiplayer-icon.png")))
    except (tk.TclError, OSError):
        pass
    root.title("MultiPlayer – QO-100 Streams")
    root.geometry("1180x420")
    lines = []
    hist = collections.deque(maxlen=200)                       # volle Meldungsfolge fuer den Fehlerbericht (lines wird nach dem Anzeigen geleert)

    def glog(t):
        ln = "%s  %s" % (time.strftime("%H:%M:%S"), t)
        lines.append(ln); hist.append(ln)
    m = Multi(A.server, A.player, A.bake, glog)
    setup_pis(m)
    m.festfenster = bool(c.get("festfenster", False))        # gleich von Anfang an, damit beim Start kein einzelnes Player-Fenster aufblitzt
    m.root = root
    m.quad = A.quad
    m.layouts = c.get("layouts", {})
    m.stick_players = dict(c.get("players", {}))
    m.marq = dict(MARQ_DEFAULT, **c.get("marq", {}))
    m.borderless = bool(c.get("randlos", False))
    m.tile = bool(c.get("kacheln", True)); m.vscale = float(c.get("vscale", 2.0))
    m.show_anyway = set(c.get("trotzdem", []))
    top = ttk.Frame(root, padding=6); top.pack(fill="x")
    ttk.Label(top, text="Dienst:").pack(side="left")
    srv = tk.StringVar(value=A.server); ttk.Entry(top, textvariable=srv, width=24).pack(side="left", padx=4)
    ply = tk.StringVar(value=A.player)
    pmb = ttk.Menubutton(top, text="Player: %s \u25be" % A.player); pmb.pack(side="left", padx=4)
    pmenu = tk.Menu(pmb, tearoff=0); pmb["menu"] = pmenu

    def set_player():
        pmb.configure(text="Player: %s \u25be" % ply.get())
        if m.on_change:
            m.on_change()
    for key_, lbl_ in PLAYERS:
        pmenu.add_radiobutton(label=lbl_, variable=ply, value=key_, command=set_player)
    pmenu.add_separator()
    bl = tk.BooleanVar(value=m.borderless)

    def set_border():
        m.borderless = bl.get()
        if m.on_change:
            m.on_change()
        if any(sl.alive() for sl in m.slots.values()):        # laufende Player mit der neuen Einstellung neu starten (Autostart startet sie wieder)
            m.stop_all()
    pmenu.add_checkbutton(label="Fensterlos / randlos (ohne Fensterrahmen) – Player starten neu", variable=bl, command=set_border)
    pimb = ttk.Menubutton(top, text="Pis \u25be"); pimb.pack(side="left", padx=4)
    pimenu = tk.Menu(pimb, tearoff=0); pimb["menu"] = pimenu
    pi_vars = {}

    def pi_label(a):
        h = m.hosts.get(a) or m.found.get(a) or ""
        return ("%s (%s)" % (h, a)) if h else a

    def pi_toggle(a):
        if pi_vars[a].get():
            if a != m.server and a not in m.servers:
                m.servers.append(a)
        elif a in m.servers:
            m.servers.remove(a)
        m.stop_all()
        if m.on_change:
            m.on_change()

    def pi_add():
        from tkinter import simpledialog
        a = simpledialog.askstring("Pi hinzufuegen", "HOST oder HOST:PORT des Pi mit RTL-SDR-datvRX-Dienst:", parent=root)
        a = norm_addr(a or "")
        if a and a != m.server and a not in m.servers:
            m.servers.append(a)
            if m.on_change:
                m.on_change()

    def mqtt_set():
        from tkinter import simpledialog
        b = simpledialog.askstring("MQTT-Broker", "Broker HOST[:PORT] (leer = keine MQTT-Suche, nur Liste/Handeingabe):", initialvalue=m.mqtt, parent=root)
        if b is not None:
            m.mqtt = b.strip()
            if m.mqtt:
                threading.Thread(target=m.discover, daemon=True).start()
            if m.on_change:
                m.on_change()
    mq_auto = tk.BooleanVar(value=m.mqtt_auto)

    def build_pimenu():
        pimenu.delete(0, "end")
        pi_vars.clear()
        allp = m.pis() + [a for a in m.found if a not in m.pis()]
        for a in allp:
            pi_vars[a] = tk.BooleanVar(value=a in m.pis())
            pimenu.add_checkbutton(label=pi_label(a) + ("   [Steuer-Pi]" if a == m.server else ""), variable=pi_vars[a],
                                   state=("disabled" if a == m.server else "normal"), command=lambda a=a: pi_toggle(a))
        pimenu.add_separator()
        pimenu.add_command(label="Pi von Hand hinzufuegen ...", command=pi_add)
        pimenu.add_command(label="MQTT-Broker: %s ..." % (m.mqtt or "keiner"), command=mqtt_set)
        pimenu.add_command(label="Jetzt per MQTT suchen", state=("normal" if m.mqtt else "disabled"), command=lambda: threading.Thread(target=m.discover, daemon=True).start())
        pimenu.add_checkbutton(label="Per MQTT gefundene Pis automatisch verwenden", variable=mq_auto,
                               command=lambda: (setattr(m, "mqtt_auto", mq_auto.get()), m.on_change and m.on_change()))
        pimenu.add_separator()
        pimenu.add_command(label="Steuer-Pi (Dienst: Feld links) fuer Scan/Verteiler/Bake; alle Pis liefern Streams", state="disabled")
    pimenu.configure(postcommand=build_pimenu)
    bk = tk.BooleanVar(value=A.bake); ttk.Checkbutton(top, text="Baken auch wiedergeben", variable=bk).pack(side="left", padx=10)
    au = tk.BooleanVar(value=True); ttk.Checkbutton(top, text="Player automatisch starten", variable=au).pack(side="left", padx=4)
    qd = tk.BooleanVar(value=A.quad)

    def quad_toggle():
        m.quad = qd.get()
        m.arrange()                      # an: Raster; aus: gemerkte Lage der aktuellen Fensterzahl (falls vorhanden)
    ttk.Checkbutton(top, text="Quad", variable=qd, style="Toolbutton", command=quad_toggle).pack(side="left", padx=8)
    ffv = tk.BooleanVar(value=bool(c.get("festfenster", False)))
    ttk.Checkbutton(top, text="Festfenster", variable=ffv, style="Toolbutton", command=lambda: (ff_apply(), m.on_change and m.on_change())).pack(side="left", padx=4)
    ffs = tk.BooleanVar(value=bool(c.get("ff_spektrum", False)))                                 # Spektrum unter den Kacheln (Standard aus)
    ttk.Checkbutton(top, text="Spektrum", variable=ffs, style="Toolbutton", command=lambda: ff_spec_apply()).pack(side="left", padx=2)
    sqv = tk.StringVar(value="")                                                                 # Quelle des Spektrums am Pi: auto / batc / stick (wird vom Pi gelesen)
    sqmb = ttk.Menubutton(top, text="Quelle \u25be"); sqmb.pack(side="left", padx=2)
    sqm = tk.Menu(sqmb, tearoff=0); sqmb["menu"] = sqm
    for _v, _t in (("auto", "Auto: BATC, bei Ausfall eigener Stick"), ("batc", "nur BATC-Server"), ("stick", "eigener RTL-Stick (Bandscan)")):
        sqm.add_radiobutton(label=_t, value=_v, variable=sqv, command=lambda: ff_spec_quelle())
    mb = ttk.Menubutton(top, text="Fenster \u25be"); mb.pack(side="left", padx=4)
    menu = tk.Menu(mb, tearoff=0); mb["menu"] = menu
    rem = tk.BooleanVar(value=c.get("remember", True))
    names = {"1": "1 Fenster", "2": "2 Fenster", "3": "3 Fenster", "4": "4 Fenster (Quad-Lage)", "5": "5 und mehr Fenster"}

    def load_slot(k):
        if m.layouts.get(k):
            m.quad = False; qd.set(False)
            for sl in m.slots.values():
                if sl.alive():
                    g = m.layouts[k].get(sl.name)
                    if g:
                        m.place_geo(sl, g)

    def slot_menu():
        menu.delete(0, "end")
        for k in "12345":
            sub = tk.Menu(menu, tearoff=0)
            sub.add_command(label="jetzt speichern (laufende Fenster)", command=lambda k=k: m.save_now(k))
            sub.add_command(label="Lage laden", command=lambda k=k: load_slot(k))
            sub.add_command(label="l\u00f6schen", command=lambda k=k: (m.layouts.pop(k, None), m.on_change and m.on_change()))
            menu.add_cascade(label="%s%s" % (names[k], ("   \u2713 gemerkt (%d)" % len(m.layouts[k])) if m.layouts.get(k) else "   \u2013 leer"), menu=sub)
        menu.add_separator()
        menu.add_checkbutton(label="Lagen automatisch merken (verschobene Fenster)", variable=rem, command=lambda: setattr(m, "remember", rem.get()))
        menu.add_checkbutton(label="Fensterlos (ohne Rahmen)", variable=bl, command=set_border)
        menu.add_separator()
        menu.add_checkbutton(label="Fenster kacheln: Player ohne Überlappung, Hauptfenster nicht verdecken", variable=tl, command=tile_toggle)
        menu.add_command(label="Fenster jetzt kacheln", command=lambda: threading.Thread(target=lambda: m.tile_players(force=True), daemon=True).start())
        vm = tk.Menu(menu, tearoff=0)
        for lab_, val_ in (("1x (Originalgröße)", "1.0"), ("1,5x", "1.5"), ("2x", "2.0"), ("3x", "3.0"), ("unbegrenzt (ganze Kachel)", "0")):
            vm.add_radiobutton(label=lab_, value=val_, variable=vs, command=vscale_set)
        menu.add_cascade(label="Player-Fenster: max. Vergrößerung des Videos", menu=vm)
    tl = tk.BooleanVar(value=m.tile)
    vs = tk.StringVar(value=str(m.vscale if m.vscale else "0"))

    def tile_toggle():
        m.tile = tl.get()
        if m.on_change:
            m.on_change()
        m._tile_sig = None
        if m.tile:
            m.arrange()

    def vscale_set():
        try:
            m.vscale = float(vs.get())
        except ValueError:
            return
        if m.on_change:
            m.on_change()
        m.arrange()
    menu.configure(postcommand=slot_menu)
    m.remember = rem.get()
    ctl = ttk.Frame(root, padding=(6, 0)); ctl.pack(fill="x")
    ctl_btn = {}

    def mkbtn(key, text, cmd):
        b = ttk.Button(ctl, text=text, command=cmd); b.pack(side="left", padx=3, pady=2); ctl_btn[key] = b
    mkbtn("bake", "Bake neu referenzieren", lambda: m.post("/bake/check/all"))
    mkbtn("scan", "Alle jetzt scannen", lambda: m.post("/scan/stop" if m.status.get("scan", {}).get("laeuft") else "/scan/start"))
    mkbtn("auto", "Auto-Scan", lambda: m.post("/auto/off" if m.status.get("auto_scan") else "/auto/on"))
    mkbtn("vert", "Autoverteiler", lambda: m.post("/verteiler/off" if m.status.get("verteiler", {}).get("an") else "/verteiler/on"))
    mkbtn("neu", "neu verteilen", lambda: m.post("/verteilen/neu"))
    mkbtn("web", "Webseite", lambda: __import__("webbrowser").open("http://%s/" % m.server))
    mkbtn("man", "Manuell\u2026", lambda: __import__("webbrowser").open("http://%s/manuell" % m.server))
    stick_frame = ttk.Frame(ctl); stick_frame.pack(side="left", padx=8)
    stick_vars, stick_names = {}, [None]

    def post_sticks():
        m.post("/verteilen/sticks", "&".join("%s=1" % n for n, v in stick_vars.items() if v.get()))

    def build_sticks(names, state):
        for w in stick_frame.winfo_children():
            w.destroy()
        stick_vars.clear()
        ttk.Label(stick_frame, text="verteilen auf:").pack(side="left")
        for n in names:
            stick_vars[n] = tk.BooleanVar(value=state.get(n, True))
            ttk.Checkbutton(stick_frame, text=n, variable=stick_vars[n], command=post_sticks).pack(side="left", padx=2)
    def marq_dialog():
        dlg = tk.Toplevel(root)
        dlg.title("Text im Player – was wird angezeigt")
        dlg.transient(root)
        q = m.marq
        order = list(q["fields"]) + [k for k, _l, _e in MARQ_FIELDS if k not in q["fields"]]
        on = {k: tk.BooleanVar(value=k in q["fields"]) for k, _l, _e in MARQ_FIELDS}
        lab = {k: (l, e) for k, l, e in MARQ_FIELDS}
        body = ttk.Frame(dlg, padding=8); body.pack(fill="both")
        ttk.Label(body, text="Angezeigte Felder (Häkchen) und Reihenfolge (▲ ▼):", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        rows = ttk.Frame(body); rows.grid(row=1, column=0, columnspan=4, sticky="w", pady=4)
        prev = tk.StringVar()

        def draw():
            for w in rows.winfo_children():
                w.destroy()
            for i, k in enumerate(order):
                ttk.Checkbutton(rows, text="%s   (z. B. %s)" % lab[k], variable=on[k], command=refresh_prev).grid(row=i, column=0, sticky="w")
                ttk.Button(rows, text="▲", width=2, command=lambda i=i: move(i, -1)).grid(row=i, column=1)
                ttk.Button(rows, text="▼", width=2, command=lambda i=i: move(i, 1)).grid(row=i, column=2)
            refresh_prev()

        def move(i, d):
            j = i + d
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
                draw()
        sep = tk.StringVar(value=q["sep"]); pos = tk.StringVar(value=dict(MARQ_POS).get(q["pos"], "oben Mitte"))
        size = tk.IntVar(value=q["size"]); opa = tk.IntVar(value=q["opacity"]); col = tk.StringVar(value=q["color"]); onv = tk.BooleanVar(value=q.get("on", True))

        def cfg():
            return {"on": onv.get(), "fields": [k for k in order if on[k].get()], "sep": sep.get(), "pos": next((v for v, n in MARQ_POS if n == pos.get()), 4),
                    "size": size.get(), "opacity": opa.get(), "color": re.sub(r"[^0-9A-Fa-f]", "", col.get())[:6].upper().ljust(6, "F")}

        def refresh_prev():
            c_ = cfg()
            ex = c_["sep"].join(lab[k][1] for k in c_["fields"])
            prev.set(ex or "(nichts)")
        r = 2
        ttk.Checkbutton(body, text="Text im Player einblenden (nur VLC)", variable=onv, command=refresh_prev).grid(row=r, column=0, columnspan=4, sticky="w"); r += 1
        for txt, w_ in (("Trennzeichen:", ttk.Entry(body, textvariable=sep, width=10)),
                        ("Position:", ttk.Combobox(body, textvariable=pos, values=[n for _v, n in MARQ_POS], width=14, state="readonly")),
                        ("Schriftgröße:", ttk.Spinbox(body, from_=10, to=60, textvariable=size, width=6)),
                        ("Deckkraft (0–255):", ttk.Spinbox(body, from_=0, to=255, textvariable=opa, width=6)),
                        ("Farbe (RRGGBB):", ttk.Entry(body, textvariable=col, width=10))):
            ttk.Label(body, text=txt).grid(row=r, column=0, sticky="w"); w_.grid(row=r, column=1, sticky="w", pady=1); r += 1
        ttk.Label(body, text="Vorschau:").grid(row=r, column=0, sticky="w")
        ttk.Label(body, textvariable=prev, foreground="#06c", wraplength=520).grid(row=r, column=1, columnspan=3, sticky="w"); r += 1
        ttk.Label(body, text="Die Felder gelten sofort (VLC liest die Textdatei jede Sekunde). Position, Größe, Deckkraft und Farbe gelten ab dem nächsten Start des Players.",
                  wraplength=560, foreground="#666").grid(row=r, column=0, columnspan=4, sticky="w", pady=4); r += 1
        btn = ttk.Frame(body); btn.grid(row=r, column=0, columnspan=4, sticky="e")

        def apply(restart=False):
            m.marq = cfg()
            m.update_marq()
            if m.on_change:
                m.on_change()
            if restart:
                for sl in m.slots.values():
                    if sl.alive() and not getattr(sl, "forced", False):
                        m.stop(sl, "Einblendung geändert")      # die Automatik startet ihn mit den neuen Optionen wieder
        ttk.Button(btn, text="Übernehmen", command=apply).pack(side="left", padx=3)
        ttk.Button(btn, text="Übernehmen + Player neu starten", command=lambda: apply(True)).pack(side="left", padx=3)
        ttk.Button(btn, text="Standard", command=lambda: (order.__init__(MARQ_DEFAULT["fields"] + [k for k, _l, _e in MARQ_FIELDS if k not in MARQ_DEFAULT["fields"]]),
                                                         [on[k].set(k in MARQ_DEFAULT["fields"]) for k in on], sep.set(MARQ_DEFAULT["sep"]), draw())).pack(side="left", padx=3)
        ttk.Button(btn, text="Schließen", command=dlg.destroy).pack(side="left", padx=3)
        draw()
    ttk.Button(top, text="Text im Player…", command=marq_dialog).pack(side="left", padx=4)
    ttk.Button(top, text="ShowAll", command=lambda: threading.Thread(target=m.show_all, daemon=True).start()).pack(side="left", padx=4)       # alle Player-Fenster nach vorn
    mute_btn = ttk.Button(top, text="MuteAll")
    mute_btn.configure(command=lambda: (m.toggle_mute(), mute_btn.configure(text=("UnmuteAll" if m.muted else "MuteAll"))))
    mute_btn.pack(side="left", padx=4)
    if fb is not None:
        fbmake = fb.install_tk(root, "MultiPlayer", version=fb.version_of(os.path.abspath(__file__)), log_tail=lambda: list(hist),
                               config=lambda: {"Dienst": m.server, "Weitere Pis": ", ".join(m.servers) or "-", "MQTT": m.mqtt or "-", "Player": m.player, "Baken": m.bake_too, "Quad": m.quad, "Randlos": m.borderless, "Sticks": len(m.sticks), "Fehler beim Abruf": m.err or "-"})
        ttk.Button(top, text="Fehlerbericht", command=lambda: fb.show_dialog_tk(root, "MultiPlayer", fbmake())).pack(side="left", padx=4)
    info = tk.StringVar(); ttk.Label(root, textvariable=info, padding=(8, 2), font=("TkDefaultFont", 10, "bold")).pack(fill="x")
    cols = ("stick", "udp", "signal", "dienst", "mod", "sr", "mer", "esn0", "mbit", "player")
    tv = ttk.Treeview(root, columns=cols, show="headings", height=6)
    for c_, t_, w_ in zip(cols, ("Stick", "UDP-Adresse", "Signal", "Dienst", "Modulation / FEC", "Symbolrate", "MER dB", "Es/N0 dB", "Mbit/s", "Player"), (55, 135, 150, 110, 120, 90, 60, 65, 55, 170)):
        tv.heading(c_, text=t_); tv.column(c_, width=w_)
    tv.tag_configure("pi", foreground="#9aa0a6")            # am Pi-Monitor: hellgrau, aber lesbar
    tv.pack(fill="both", expand=True, padx=6, pady=4)

    def row_name(ev):
        iid = tv.identify_row(ev.y)
        return tv.item(iid, "values")[0] if iid else None
    tv.bind("<Button-1>", lambda ev: (lambda n: n and m.force_start(n))(row_name(ev)))       # Klick: Player mit der UDP-Adresse dieser Zeile starten
    def row_menu(ev):
        n = row_name(ev)
        if not n:
            return
        rm = tk.Menu(root, tearoff=0)
        for key_, lbl_ in PLAYERS:
            rm.add_command(label="Starten mit %s" % lbl_, command=lambda k=key_: m.force_start(n, k))
        rm.add_separator()
        sub = tk.Menu(rm, tearoff=0)
        sv = tk.StringVar(value=m.stick_players.get(n, ""))

        def pick(v):
            if v:
                m.stick_players[n] = v
            else:
                m.stick_players.pop(n, None)
            m.on_change and m.on_change()
        sub.add_radiobutton(label="wie allgemein (%s)" % ply.get(), variable=sv, value="", command=lambda: pick(""))
        for key_, lbl_ in PLAYERS:
            sub.add_radiobutton(label=lbl_, variable=sv, value=key_, command=lambda k=key_: pick(k))
        rm.add_cascade(label="Player für %s (Automatik)" % n, menu=sub)
        rm.add_separator()
        stx = next((x for x in m.sticks if x["name"] == n), {})
        if (stx.get("display") or {}).get("aktiv") or n in m.show_anyway:
            def toggle_pi():
                (m.show_anyway.discard if n in m.show_anyway else m.show_anyway.add)(n)
                m.on_change and m.on_change()
            rm.add_command(label=("Nicht mehr darstellen (am Pi-Monitor %s)" if n in m.show_anyway else "Trotzdem darstellen (h\u00e4ngt am Pi-Monitor %s)") % (stx.get("display") or {}).get("ausgang", ""), command=toggle_pi)
        rm.add_command(label="Stoppen", command=lambda: m.force_stop(n))
        try:
            rm.tk_popup(ev.x_root, ev.y_root)
        finally:
            rm.grab_release()
    tv.bind("<Button-3>", row_menu)                                                           # Rechtsklick: Menü (Player wählen/starten, Player für den Stick, Stoppen)
    hint = ttk.Label(root, text="Klick auf eine Zeile startet den Player mit deren UDP-Adresse, Rechtsklick: Player wählen, für den Stick merken, stoppen.", foreground="#666")
    hint.pack(anchor="w", padx=8)
    logbox = tk.Text(root, height=5, state="disabled"); logbox.pack(fill="x", padx=6, pady=(0, 6))
    busy = [False]
    disc_t = [time.time()]
    ff = {"view": None, "labels": {}, "geo": None, "rec": {}}

    def ff_stick(t):
        return next((x for x in m.sticks if x["name"] == t), None)

    def ff_candidates():
        out = []
        for st in m.sticks:
            cur, mm = st.get("aktuell"), st.get("messung") or {}
            if not cur or not mm.get("lock"):
                continue
            if abs(cur[0] - BEACON) < 0.5 and not bk.get():
                continue
            out.append(st["name"])
        return out

    def ff_info(t):
        st = ff_stick(t)
        if not st:
            return {"label": t}
        cur, mm = st.get("aktuell") or [None, None], st.get("messung") or {}
        return {"label": t, "freq": cur[0], "sr": cur[1], "call": mm.get("rufzeichen") or "",
                "mer": ("MER %.1f dB" % mm["mer"]) if mm.get("mer") is not None else ""}

    def ff_url(t):
        st = ff_stick(t)
        return ("udp://@%s" % st["udp"]) if st else None

    def ff_marq(t):
        st = ff_stick(t)
        return m.marq_text(st) if st else ""

    def ff_snapshot(t):
        d = os.path.expanduser("~/Bilder")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "QO100_%s_%s.png" % (re.sub(r"[^A-Za-z0-9_-]", "_", t), time.strftime("%Y%m%d_%H%M%S")))

    def ff_rec_state(t):
        p = ff["rec"].get(t)
        return bool(p and p.poll() is None)

    def ff_rec(t):
        """Aufnahme des Stick-Stroms als TS-Datei (ffmpeg -c copy); zweiter Aufruf beendet sie"""
        if ff_rec_state(t):
            ff["rec"][t].terminate()
            glog("Aufnahme %s beendet." % t)
            return
        st, exe = ff_stick(t), find_exe("ffmpeg")
        if not st or not exe:
            glog("Aufnahme: ffmpeg nicht gefunden." if st else "Aufnahme: Stick unbekannt.")
            return
        d = os.path.expanduser("~/Videos")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "QO100_%s_%s.ts" % (re.sub(r"[^A-Za-z0-9_-]", "_", t), time.strftime("%Y%m%d_%H%M%S")))
        ff["rec"][t] = subprocess.Popen([exe, "-v", "quiet", "-i", "udp://%s?fifo_size=5000000&overrun_nonfatal=1" % st["udp"], "-c", "copy", path],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        glog("Aufnahme %s -> %s" % (t, path))

    def ff_spec_fetch():
        """BATC-Spektrum von einem Pi holen (/spectrum.json?mode=live), solange das Spektrum an ist; erster erreichbarer Pi gewinnt"""
        while ff["view"] is not None and ff["view"].spec_on:
            for a in m.pis():
                try:
                    with urllib.request.urlopen("http://%s/spectrum.json?mode=live" % norm_addr(a), timeout=4) as r:
                        d = json.loads(r.read().decode("utf-8", "replace"))
                    db = d.get("db") or []
                    if not db:
                        continue
                    lo = sorted(db)[len(db) // 20]
                    hi = max(max(db), lo + 15.0)
                    sigs = []
                    for sg in d.get("sig") or []:
                        half = sg["sr"] / 1000.0 * 0.675
                        sigs.append({"f0": sg["f"] - half, "f1": sg["f"] + half, "beacon": bool(sg.get("bake")),
                                     "label": ("Bake " + (sg.get("call") or "A71A")) if sg.get("bake") else (sg.get("call") or "%.2f" % sg["f"]), "sub": "%dk" % sg["sr"], "f": sg["f"]})
                    ff["spec"] = {"start": d["start"], "span": d["bw"], "pts": [(v - lo) / (hi - lo) for v in db], "sigs": sigs, "t": time.time(), "lo": lo, "hi": hi, "quelle": d.get("quelle", "")}
                    if d.get("quelle_modus") and d["quelle_modus"] != sqv.get():
                        root.after(0, sqv.set, d["quelle_modus"])
                    break
                except Exception:                                  # noqa: BLE001
                    continue
            time.sleep(1.5)
        ff["spec_thr"] = None

    def ff_spec_quelle():
        """Spektrum-Quelle am Pi umschalten (POST /spektrum/quelle); gilt fuer den Steuer-Pi"""
        q = sqv.get()
        def go():
            try:
                req = urllib.request.Request("http://%s/spektrum/quelle" % norm_addr(m.server), data=("q=" + q).encode(), method="POST")
                urllib.request.urlopen(req, timeout=6).read()
                glog("Spektrum-Quelle am Pi: %s" % q)
            except Exception as e:                                  # noqa: BLE001
                glog("Spektrum-Quelle nicht umgestellt: %s" % (e,))
        threading.Thread(target=go, daemon=True).start()

    def ff_spec_get():
        sp = ff.get("spec")
        if not sp or time.time() - sp.get("t", 0) > 8:                # keine alten Daten anzeigen, wenn der Pi nicht mehr antwortet
            return None
        tuned = [(ff_info(st["name"]).get("freq"), st["name"]) for st in m.sticks if st["name"] in ff_candidates()]
        sigs = [dict(sg) for sg in sp["sigs"] if not sg.get("von_stick")]
        for sg in sigs:
            sg["tuners"] = [n for f, n in tuned if f and abs(f - sg["f"]) <= 0.2]
        for f, n in tuned:                                          # Stick steht auf einem Signal, das die Pi-Erkennung nicht kennt: trotzdem einrahmen
            if f and not any(abs(f - sg["f"]) <= 0.2 for sg in sigs):
                inf = ff_info(n)
                sr = (inf.get("sr") or 333) / 1000.0
                sigs.append({"f": f, "f0": f - sr * 0.675, "f1": f + sr * 0.675, "beacon": False, "von_stick": True, "tuners": [n],
                             "label": inf.get("call") or "%.2f" % f, "sub": "%dk" % round(sr * 1000)})
        return dict(sp, sigs=sigs)

    def ff_spec_apply(from_view=False):
        v = ff["view"]
        if v is None:
            return
        v.set_spectrum(ffs.get())
        if ffs.get() and not ff.get("spec_thr"):
            ff["spec_thr"] = threading.Thread(target=ff_spec_fetch, daemon=True)
            ff["spec_thr"].start()
        m.on_change and m.on_change()

    def ff_spec_saved(on):
        if ffs.get() != on:
            ffs.set(on)
            if on and not ff.get("spec_thr"):
                ff["spec_thr"] = threading.Thread(target=ff_spec_fetch, daemon=True)
                ff["spec_thr"].start()
        m.on_change and m.on_change()

    def ff_sync_labels():
        """Stickliste -> Kachel-Auswahl (bei Aenderung)"""
        v = ff["view"]
        if v is None:
            return
        now = {x["name"]: x["name"] for x in m.sticks}
        if now != ff["labels"]:
            ff["labels"] = dict(now)
            v.labels.clear(); v.labels.update(now)
            v.refresh_labels()

    def ff_apply():
        if ffv.get():
            if ff["view"] is None:
                try:
                    try:
                        from leandvb_einfach import EinfachView
                    except ImportError:
                        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
                        from leandvb_einfach import EinfachView
                    ff["view"] = EinfachView(root, logbox, ff_candidates, ff_info, ff_url, {}, glog, get_marquee=ff_marq, snapshot_path=ff_snapshot,
                                             record=ff_rec, rec_state=ff_rec_state, get_spectrum=ff_spec_get, spec_changed=ff_spec_saved)
                except Exception as e:
                    glog("Festfenster nicht verfuegbar: %s (python-vlc und leandvb_einfach.py noetig)" % (e,))
                    ffv.set(False)
                    return
            m.stop_all()
            m.festfenster = True
            tv.pack_forget(); hint.pack_forget()
            ff["geo"] = ff["geo"] or root.geometry()
            root.geometry("1300x860")
            ff_sync_labels()
            ff["view"].tick(once=True)
            if ffs.get():
                ff_spec_apply()
            glog("Festfenster AN - alle Streams als Kacheln in diesem Fenster (Rechtsklick auf eine Kachel: Menue).")
        else:
            m.festfenster = False
            if ff["view"] is not None:
                ff["view"].close()
                ff["view"] = None
            for p in ff["rec"].values():
                if p.poll() is None:
                    p.terminate()
            ff["labels"] = {}
            hint.pack(anchor="w", padx=8, before=logbox)
            tv.pack(fill="both", expand=True, padx=6, pady=4, before=hint)
            if ff["geo"]:
                root.geometry(ff["geo"]); ff["geo"] = None
            glog("Festfenster AUS - einzelne Player-Fenster.")

    def work():
        m.server = norm_addr(srv.get()) or m.server
        if m.mqtt and time.time() - disc_t[0] > 60:
            disc_t[0] = time.time(); threading.Thread(target=m.discover, daemon=True).start()
        if m.player != ply.get():
            m.stop_all(); m.player = ply.get()
        m.bake_too, m.auto = bk.get(), au.get()
        try:
            m.step()
        finally:
            busy[0] = False

    def refresh():
        if not busy[0]:
            busy[0] = True
            threading.Thread(target=work, daemon=True).start()
        info.set(m.summary())
        stt = m.status or {}
        ver = stt.get("verteiler", {})
        ctl_btn["auto"].configure(text="Auto-Scan: %s" % ("AN" if stt.get("auto_scan") else "AUS"))
        ctl_btn["vert"].configure(text="Autoverteiler: %s" % ("AN" if ver.get("an") else "AUS"))
        ctl_btn["scan"].configure(text="Scan stoppen" if stt.get("scan", {}).get("laeuft") else "Alle jetzt scannen")
        n_open = ver.get("ohne_stick", 0)
        ctl_btn["neu"].configure(text="%d neu verteilen" % n_open, state=("normal" if ver.get("an") and n_open else "disabled"))
        ff_sync_labels()
        names_now = [x.get("_name0", x["name"]) for x in m.sticks if x.get("_server", m.server) == m.server]        # Verteiler-Haken nur fuer den Steuer-Pi
        if names_now != stick_names[0]:
            stick_names[0] = names_now
            build_sticks(names_now, ver.get("sticks", {}))
        else:                              # Haken folgen dem Dienst
            for n, v in stick_vars.items():
                if n in ver.get("sticks", {}) and v.get() != ver["sticks"][n]:
                    v.set(ver["sticks"][n])
        tv.delete(*tv.get_children())
        for st in m.sticks:
            mm, cur = st.get("messung") or {}, st.get("aktuell")
            sl = m.slots.get(st["name"])
            sg = ("%.3f MHz%s" % (cur[0], " (Bake)" if abs(cur[0] - BEACON) < 0.5 else "")) if cur else "frei"
            md = ("%s %s" % (mm.get("mod") or "", mm.get("fec") or "")).strip() if cur else ""
            srs = (("%.0f kS" % (mm["sr_det"] * 1000)) if mm.get("sr_det") else ("%d kS" % cur[1] if cur and cur[1] else "")) if cur else ""
            f1 = lambda v: "" if v is None else "%.1f" % v
            pi = bool((st.get("display") or {}).get("aktiv")) and st["name"] not in m.show_anyway      # grau, sobald ein Pi-Monitor zugewiesen ist (auch ohne Signal)
            tv.insert("", "end", tags=(("pi",) if pi else ()), values=(st["name"], st["udp"], sg, mm.get("rufzeichen") or "", md, srs, f1(mm.get("mer")), f1(mm.get("esn0")),
                                         "" if mm.get("bitrate") is None else "%.2f" % mm["bitrate"], sl.msg if sl else ""))
        if lines:
            logbox.configure(state="normal")
            for l in lines[:]:
                logbox.insert("end", l + "\n")
            lines.clear(); logbox.see("end"); logbox.configure(state="disabled")
        root.after(2000, refresh)

    def bye():
        save({"server": m.server, "servers": m.servers, "mqtt": m.mqtt, "mqtt_auto": m.mqtt_auto, "found": m.found, "festfenster": ffv.get(), "ff_spektrum": ffs.get(), "player": ply.get(), "bake": bk.get(), "quad": qd.get(), "remember": rem.get(), "layouts": m.layouts, "players": m.stick_players, "marq": m.marq, "randlos": m.borderless, "kacheln": m.tile, "vscale": m.vscale, "trotzdem": sorted(m.show_anyway)})
        m.stop_all()
        for p_ in ff["rec"].values():
            if p_.poll() is None:
                p_.terminate()
        if ff["view"] is not None:
            ff["view"].close()
        root.destroy()
    m.on_change = lambda: save({"server": m.server, "servers": m.servers, "mqtt": m.mqtt, "mqtt_auto": m.mqtt_auto, "found": m.found, "festfenster": ffv.get(), "ff_spektrum": ffs.get(), "player": ply.get(), "bake": bk.get(), "quad": qd.get(), "remember": rem.get(), "layouts": m.layouts, "players": m.stick_players, "marq": m.marq, "randlos": m.borderless, "kacheln": m.tile, "vscale": m.vscale, "trotzdem": sorted(m.show_anyway)})
    root.protocol("WM_DELETE_WINDOW", bye)
    root.after(200, refresh)
    if ffv.get():
        root.after(600, ff_apply)
    root.mainloop()


if __name__ == "__main__":
    main()
