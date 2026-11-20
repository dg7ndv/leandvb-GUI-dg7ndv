#!/usr/bin/env python3
"""dienst_client.py - Client fuer den RTL-SDR-datvRX-Dienst (Pi): Sticks abfragen, Stick auf ein Signal stellen, Messwerte als Status liefern (DG7NDV)

Gemeinsamer Baustein ohne GUI-Abhaengigkeit (wie datv_core.py): die leandvb-GUI bindet die Sticks des Dienstes damit als Empfaenger ein
("RTL-Dienst RTL1 ..."), der MultiPlayer kann ihn ebenfalls nutzen. Alles ueber die HTTP-API des Dienstes (/api/v1/sticks, /manuell/set, /free/N).

  fetch_sticks(server)            -> Liste der Sticks (dict je Stick, wie /api/v1/sticks) oder wirft OSError
  tune(server, nr, freq, sr)      -> Stick nr (1-basiert) fest auf freq MHz / sr kS stellen (sr 0 = automatisch)
  einstellungen(server)           -> Schalter des Dienstes (Verteiler, Auto-Scan, Bake-Rueckfall ...) als dict
  set_einstellungen(server, d)    -> Schalter setzen, z.B. {"verteiler": False} (Verteilfunktion am Pi aus: die GUI verteilt selbst)
  free(server, nr)                -> Stick freigeben (der Autoverteiler darf ihn wieder nehmen)
  fill_status(st, stick)          -> API-Eintrag in das Status-dict der GUI (lock, esn0, mod, rs, ok, bad, mer, pegel, ts) uebersetzen
  Poller(server, nr, st, ...)     -> Thread, der den Stick jede Sekunde abfragt und st fuellt; Poller.proc spielt den Prozess (poll() = None solange erreichbar)
"""
import json, threading, time, urllib.error, urllib.parse, urllib.request
from collections import deque


def _url(server, path):
    return "http://%s%s" % (server if ":" in server else server + ":8554", path)


def fetch_sticks(server, timeout=5):
    with urllib.request.urlopen(_url(server, "/api/v1/sticks"), timeout=timeout) as r:
        return json.load(r).get("sticks", [])


def fetch_host(server, timeout=4):
    """Hostname des Pi: aus der API (dienst.host, neuere Dienste), sonst Rueckwaertsaufloesung der IP; leer, wenn unbekannt"""
    try:
        with urllib.request.urlopen(_url(server, "/api/v1/sticks"), timeout=timeout) as r:
            h = ((json.load(r).get("dienst") or {}).get("host") or "").strip()
        if h:
            return h
    except (OSError, ValueError):
        pass
    try:
        import socket
        return socket.gethostbyaddr(server.rsplit(":", 1)[0])[0].split(".")[0]
    except (OSError, ValueError):
        return ""


def fetch_stick(server, nr, timeout=4):
    with urllib.request.urlopen(_url(server, "/api/v1/stick/%d" % nr), timeout=timeout) as r:
        return json.load(r).get("stick")


def tune(server, nr, freq, sr, timeout=40):
    """Stick auf freq (Downlink MHz) / sr (kS, 0 = automatisch) stellen; die Seite /manuell/set akzeptiert jede Frequenz (auch ohne BATC-Signal)"""
    data = urllib.parse.urlencode({"stick": nr, "freq": "%.3f" % float(freq), "sr": "auto" if not sr else str(int(sr))}).encode()
    req = urllib.request.Request(_url(server, "/manuell/set"), data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:          # folgt dem 303 auf /manuell; ein Fehler steht rot auf der Seite
        body = r.read().decode(errors="replace")
    return ("color:#c0392b" not in body)


def einstellungen(server, timeout=5):
    with urllib.request.urlopen(_url(server, "/api/v1/einstellungen"), timeout=timeout) as r:
        return json.load(r)


def set_einstellungen(server, d, timeout=10):
    req = urllib.request.Request(_url(server, "/api/v1/einstellungen"), data=json.dumps(d).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def free(server, nr, timeout=10):
    urllib.request.urlopen(_url(server, "/free/%d" % nr), timeout=timeout).read()


class RemoteProc:
    """spielt die Rolle des Pipeline-Prozesses der GUI: laeuft (poll() = None), solange der Dienst erreichbar ist"""
    pid = 0

    def __init__(s):
        s.alive = True

    def poll(s):
        return None if s.alive else 1


class TsLike:
    """Rufzeichen/Bitrate/Fehler aus der API in der Form von datv_core.TsInfo"""
    def __init__(s):
        s.service, s.provider, s.tei, s.cc_err, s.mbit, s.null = "", "", 0, 0, 0.0, 0.0

    def stats(s):
        return s.mbit, s.null


def fill_status(st, x):
    """API-Eintrag eines Sticks in das Status-dict der GUI uebertragen (Felder wie die lokale datvrx-Pipeline)"""
    sg, em, dn = x.get("signal"), x.get("empfang") or {}, x.get("dienst") or {}
    st["t_status"] = time.time()
    st["lock"] = bool(x.get("lock"))
    if sg:
        st["freq"] = sg["freq_mhz"]
        st["sr"] = sg.get("sr_ks_eingestellt") or (round(sg["sr_ks_erkannt"]) if sg.get("sr_ks_erkannt") else st.get("sr"))
        st["rs"] = sg.get("sr_ks_erkannt")
    ab = em.get("traeger_ablage_khz")                    # Abweichung des Traegers von der Soll-Lage (300 kHz neben der Stickmitte), wie "Offs" der lokalen datvRX
    st["carrier_hz"] = (ab + 300.0) * 1000.0 if ab is not None else None
    st["udp_api"] = x.get("udp")                         # Adresse, auf der der Pi diesen Stick sendet (kann sich nach einem Dienst-Neustart aendern)
    st["esn0"] = em.get("es_n0_db")
    st["mod"] = " ".join(p for p in (em.get("standard"), em.get("modulation"), em.get("fec"), em.get("zusatz")) if p)
    st["ok"], st["bad"] = em.get("frames_ok"), em.get("frames_bad")
    st["line"] = ("%s %s Es/N0 %s dB ok %s bad %s" % ("TS lock" if st["lock"] else "kein Lock", st["mod"], em.get("es_n0_db"), em.get("frames_ok"), em.get("frames_bad"))) if sg else "Stick frei"
    mer = em.get("mer_db")
    st["mer"].clear()
    if mer is not None and st["lock"]:
        st["mer"].append(mer)
    pg = em.get("pegel_db_ueber_rauschen")
    st["pegel"] = {"db": pg, "dbfs": em.get("pegel_dbfs")} if pg is not None else {}
    ts = st.get("ts")
    if isinstance(ts, TsLike):
        ts.service, ts.provider = dn.get("rufzeichen") or "", dn.get("provider") or ""
        ts.mbit, ts.null = em.get("bitrate_mbit_s") or 0.0, em.get("null_pakete_prozent") or 0.0
        ts.tei, ts.cc_err = em.get("tei") or 0, em.get("cc_fehler") or 0


class Poller(threading.Thread):
    """fragt den Stick jede Sekunde ab und haelt das Status-dict der GUI aktuell; meldet Lock-Wechsel; endet, wenn gen nicht mehr stimmt"""

    def __init__(s, server, nr, st, gen, on_lock=None, on_log=None):
        super().__init__(daemon=True)
        s.server, s.nr, s.st, s.gen, s.on_lock, s.on_log = server, nr, st, gen, on_lock, on_log
        s.proc = RemoteProc()
        st["proc"] = s.proc
        st["ts"] = TsLike()
        if not isinstance(st.get("mer"), deque):
            st["mer"] = deque(maxlen=24)

    def run(s):
        err_t = 0
        while s.st.get("gen") == s.gen and s.proc is s.st.get("proc"):
            try:
                x = fetch_stick(s.server, s.nr)
                was = s.st.get("lock")
                if x is None:
                    raise OSError("Stick %d unbekannt" % s.nr)
                s.proc.alive = True
                fill_status(s.st, x)
                if s.st["lock"] and not was:
                    s.st["lock_t"] = time.time()
                    if s.on_lock:
                        s.on_lock()
                err_t = 0
            except (OSError, ValueError, urllib.error.URLError) as e:
                s.proc.alive = False
                s.st["lock"] = False
                s.st["line"] = "Dienst nicht erreichbar"
                if s.on_log and time.time() - err_t > 60:
                    err_t = time.time()
                    s.on_log("Dienst %s: nicht erreichbar (%s)" % (s.server, getattr(e, "reason", e)))
            time.sleep(1.0)
