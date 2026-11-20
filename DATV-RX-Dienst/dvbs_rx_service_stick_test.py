#!/usr/bin/env python3
"""dvbs_rx_service_stick_test.py - alle RTL-SDR-Sticks am Raspberry Pi (RTL-SDR-datvRX-Dienst) pruefen.

Aufruf:  ./dvbs_rx_service_stick_test.py [--pi 192.168.0.102] [--port 8554] [--ssh dg7ndv@192.168.0.102]
                         [--sek 25] [--signal MHZ [SR_KS]] [--list] [--stick N|RTLn|KENNUNG] [--nur 2,3] [--keine-usb]
  --list           alle Sticks auflisten (Nr., Name, Kennung, Typ, Stand) und beenden - es wird nichts getestet
  --stick WER      NUR diesen einen Stick testen: Nummer (2), Name (RTL2) oder Kennung/Seriennummer (stx:1090:26)
  --nur 2,3        mehrere ausgewaehlte Sticks testen

Pruefungen:
  1. USB/Pi (per ssh): Zahl der RTL-Geraete (lsusb, rtl_test), USB-Fehler im Kernel-Log, Unterspannung (vcgencmd get_throttled)
  2. Dienst (HTTP): Stick-Liste, Bake-Status und gemerkter Offset je Stick
  3. Empfang je Stick nacheinander ueber den Dienst: Stick auf die Bake (10491.5 MHz, 1500 kS) stellen, bis --sek Sekunden auf Lock warten,
     Es/N0 / MER / Traegerablage lesen; mit --signal zusaetzlich auf ein Signal. Danach wird der Stick wieder freigegeben bzw. auf
     seinen alten Stand gestellt, der Autoverteiler des Pi waehrend des Tests ausgeschaltet und danach wieder auf den alten Wert gesetzt.
Bewertung (Bake): OK ab Es/N0 9 dB, SCHWACH 5..9 dB oder Lock nur zeitweise, FEHLER ohne Lock. Ein SCHWACH/FEHLER-Stick heisst:
Antennenkabel/Stecker/Splitter-Ausgang oder der Stick selbst pruefen (Kabel mit einem guten Stick tauschen, dann erneut testen).
Rueckgabewert: 0 = alle OK, 1 = mindestens ein Stick SCHWACH, 2 = mindestens ein Stick FEHLER.
Hinweis: laeuft die leandvb-GUI und verteilt selbst, kann sie waehrend des Tests Sticks umstellen - dann GUI kurz anhalten oder die Ergebnisse mit Vorsicht lesen.
"""
import argparse, json, os, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    import dvbs_rx_service_stick_status as _st       # Stick-Typ (Hersteller/Modell) aus dem Statusskript
except ImportError:
    _st = None

BAKE = (10491.5, 1500)


def get(base, path, timeout=8):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.load(r)


def post(base, path, data=None, js=None, timeout=70):
    if js is not None:
        req = urllib.request.Request(base + path, data=json.dumps(js).encode(), method="POST", headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(base + path, data=urllib.parse.urlencode(data or {}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def ssh(ziel, cmd, timeout=40):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", ziel, cmd], capture_output=True, text=True, timeout=timeout)
        return "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not re.search(r"quantum|vulnerable|upgraded", l, re.I))
    except (OSError, subprocess.TimeoutExpired) as e:
        return "ssh fehlgeschlagen: %s" % e


def usb_pruefung(ziel):
    print("== 1. USB / Pi")
    out = ssh(ziel, "echo UP=$(uptime -p); echo THR=$(vcgencmd get_throttled 2>/dev/null); echo LSUSB=$(lsusb | grep -ci 0bda:2838); "
                    "echo ERR=$(dmesg 2>/dev/null | grep -ci 'error -110\\|not accepting address\\|unable to enumerate'); timeout 15 rtl_test -t 2>&1 | sed -n '1,8p'")
    if out.startswith("ssh fehlgeschlagen"):
        print("   " + out)
        return None
    for l in out.splitlines():
        print("   " + l)
    m = re.search(r"Found (\d+) device", out)
    n = int(m.group(1)) if m else 0
    thr = re.search(r"THR=throttled=(0x[0-9a-f]+)", out)
    err = re.search(r"ERR=(\d+)", out)
    if thr and thr.group(1) != "0x0":
        print("   ! Unterspannung/Drosselung gemeldet: %s" % thr.group(1))
    if err and int(err.group(1)) > 0:
        print("   ! %s USB-Fehlermeldungen im Kernel-Log (Kabel/Hub/Strom pruefen)" % err.group(1))
    return n


def werte(st):
    e = st.get("empfang") or {}
    return {"lock": bool(st.get("lock")), "esn0": e.get("es_n0_db"), "mer": e.get("mer_db"), "abl": e.get("traeger_ablage_khz"), "mbit": e.get("bitrate_mbit_s"),
            "ok": e.get("frames_ok"), "bad": e.get("frames_bad"), "std": " ".join(x for x in (e.get("standard"), e.get("modulation"), e.get("fec")) if x)}


def messen(base, nr, f, sr, sek):
    """Stick nr auf f/sr stellen, bis sek s warten; beste Werte zurueck"""
    t0 = time.time()
    try:
        post(base, "/manuell/set", {"stick": nr, "freq": "%.3f" % f, "sr": str(sr)})
    except (OSError, urllib.error.URLError) as ex:
        return {"fehler": "Abstimmen fehlgeschlagen: %s" % ex}
    best, lock_t, n_lock, n = None, None, 0, 0
    while time.time() - t0 < sek:
        time.sleep(2)
        try:
            st = get(base, "/api/v1/stick/%d" % nr)["stick"]
        except (OSError, urllib.error.URLError, KeyError, ValueError):
            continue
        n += 1
        w = werte(st)
        if st.get("frei") and n > 2 and not st.get("signal"):
            return {"fehler": "Stick wurde vom Dienst/der GUI freigegeben (anderer Verteiler aktiv?)", **(best or {})}
        if w["lock"]:
            n_lock += 1
            lock_t = lock_t or time.time() - t0
            if best is None or (w["esn0"] or -99) >= (best.get("esn0") or -99):
                best = dict(w)
    if best is None:
        return {"lock": False, "n": n, "n_lock": 0}
    best.update(n=n, n_lock=n_lock, lock_nach=round(lock_t, 1))
    return best


def bewerten(w, ist_bake=True):
    if w.get("fehler") and not w.get("lock"):
        return "FEHLER"
    if not w.get("lock"):
        return "FEHLER"
    e = w.get("esn0")
    if e is None:
        return "SCHWACH"
    grenze_ok, grenze_schwach = (9.0, 5.0) if ist_bake else (6.0, 3.0)
    if e >= grenze_ok and w["n_lock"] >= max(2, int(w["n"] * 0.6)):
        return "OK"
    return "SCHWACH" if e >= grenze_schwach else "FEHLER"


def main():
    ap = argparse.ArgumentParser(description="alle RTL-SDR-Sticks am Pi pruefen")
    ap.add_argument("--pi", default="192.168.0.102")
    ap.add_argument("--port", type=int, default=8554)
    ap.add_argument("--ssh", default=None, help="ssh-Ziel fuer die USB-Pruefung (Standard: dg7ndv@<pi>)")
    ap.add_argument("--sek", type=int, default=25, help="Wartezeit auf Lock je Messung")
    ap.add_argument("--signal", nargs="+", type=float, metavar=("MHZ", "SR_KS"), help="zusaetzlich auf dieses Signal stellen (Downlink MHz, Symbolrate kS, Standard 333)")
    ap.add_argument("--nur", default="", help="nur diese Sticks, z.B. 2,3")
    ap.add_argument("--list", action="store_true", help="alle Sticks auflisten und beenden")
    ap.add_argument("--stick", default="", metavar="WER", help="nur diesen einen Stick testen: Nummer, Name (RTL2) oder Kennung")
    ap.add_argument("--keine-usb", action="store_true", help="USB-Pruefung per ssh auslassen")
    a = ap.parse_args()
    base = "http://%s:%d" % (a.pi, a.port)
    ziel = a.ssh or "dg7ndv@%s" % a.pi

    if a.list:
        try:
            sticks = get(base, "/api/v1/sticks")["sticks"]
        except (OSError, urllib.error.URLError, KeyError, ValueError) as ex:
            print("Dienst nicht erreichbar: %s" % ex)
            return 2
        tl = _st.typen(ziel) if _st and not a.keine_usb else []
        print("%-3s %-5s %-14s %-26s %s" % ("Nr", "Name", "Kennung", "Typ", "Stand"))
        for s_ in sticks:
            sg = s_.get("signal")
            print("%-3s %-5s %-14s %-26s %s" % (s_["nr"], s_["name"], s_.get("kennung"), (_st.typ_von(s_.get("kennung"), tl) if tl else "?"),
                                              ("frei" if s_.get("frei") else "belegt") + ((" auf %.3f MHz" % sg["freq_mhz"]) if sg else "")))
        print("Test eines Sticks:  %s --stick N" % os.path.basename(sys.argv[0]))
        return 0
    if a.stick:
        try:
            sticks = get(base, "/api/v1/sticks")["sticks"]
        except (OSError, urllib.error.URLError, KeyError, ValueError) as ex:
            print("Dienst nicht erreichbar: %s" % ex)
            return 2
        w_ = a.stick.strip()
        tr = [s_["nr"] for s_ in sticks if w_ == str(s_["nr"]) or w_.upper() == s_["name"].upper() or w_ == str(s_.get("kennung"))]
        if not tr:
            print("Stick '%s' nicht gefunden. Vorhanden: %s  (--list zeigt alle)" % (w_, ", ".join("%s=%s" % (s_["name"], s_.get("kennung")) for s_ in sticks)))
            return 2
        a.nur = str(tr[0])
    print("Stick-Test  %s  (%s)" % (time.strftime("%d.%m.%Y %H:%M:%S"), base))
    nusb = None if a.keine_usb else usb_pruefung(ziel)

    print("\n== 2. Dienst")
    try:
        sticks = get(base, "/api/v1/sticks")["sticks"]
        einst = get(base, "/api/v1/einstellungen")
    except (OSError, urllib.error.URLError, KeyError, ValueError) as ex:
        print("   Dienst nicht erreichbar: %s" % ex)
        return 2
    print("   %d Sticks im Dienst, Autoverteiler: %s" % (len(sticks), "AN" if (einst.get("verteiler") or {}).get("an") else "AUS"))
    if nusb is not None and nusb != len(sticks):
        print("   ! Der Pi sieht %d RTL-Geraete, der Dienst kennt %d Sticks" % (nusb, len(sticks)))
    for s in sticks:
        b = s.get("bake") or {}
        print("   %s  Kennung %-12s Bake: %s, Ablage %s kHz  %s" % (s["name"], s.get("kennung"), b.get("status"), b.get("ablage_khz"),
                                                              "frei" if s.get("frei") else "belegt/fest"))
    nur = {int(x) for x in a.nur.split(",") if x.strip().isdigit()}
    vorher_v = (einst.get("verteiler") or {}).get("an")
    alt = {}
    for s in sticks:
        sg = s.get("signal")
        alt[s["nr"]] = (sg["freq_mhz"], sg.get("sr_ks_eingestellt") or 0) if sg and s.get("fest") else None

    print("\n== 3. Empfang je Stick (Bake%s, je bis %d s)" % (" und Signal %.3f MHz" % a.signal[0] if a.signal else "", a.sek))
    erg = []
    try:
        if vorher_v:
            post(base, "/api/v1/einstellungen", js={"verteiler": False}, timeout=10)
        for s in sticks:
            nr = s["nr"]
            if nur and nr not in nur:
                continue
            print("   %s ..." % s["name"], end="", flush=True)
            w = messen(base, nr, BAKE[0], BAKE[1], a.sek)
            urt = bewerten(w, True)
            zeile = {"stick": s["name"], "kennung": s.get("kennung"), "bake": w, "urteil": urt}
            print("  Bake: %-7s %s" % (urt, ("Lock nach %s s, Es/N0 %s dB, MER %s dB, Ablage %s kHz" % (w.get("lock_nach"), w.get("esn0"), None if w.get("mer") is None else round(w["mer"], 1), w.get("abl"))
                                              if w.get("lock") else w.get("fehler", "kein Lock"))))
            if a.signal:
                f, sr = a.signal[0], int(a.signal[1]) if len(a.signal) > 1 else 333
                print("   %s ..." % s["name"], end="", flush=True)
                w2 = messen(base, nr, f, sr, a.sek)
                u2 = bewerten(w2, False)
                zeile["signal"], zeile["urteil_signal"] = w2, u2
                print("  Signal: %-7s %s" % (u2, ("%s, Es/N0 %s dB, MER %s dB, ok/bad %s/%s" % (w2.get("std"), w2.get("esn0"), None if w2.get("mer") is None else round(w2["mer"], 1), w2.get("ok"), w2.get("bad"))
                                                  if w2.get("lock") else w2.get("fehler", "kein Lock"))))
            erg.append(zeile)
            try:                                              # Stick wieder in den alten Zustand
                if alt.get(nr):
                    post(base, "/manuell/set", {"stick": nr, "freq": "%.3f" % alt[nr][0], "sr": str(alt[nr][1] or "auto")}, timeout=70)
                else:
                    post(base, "/free/%d" % nr, {}, timeout=15)
            except (OSError, urllib.error.URLError):
                pass
    finally:
        if vorher_v:
            try:
                post(base, "/api/v1/einstellungen", js={"verteiler": True}, timeout=10)
            except (OSError, urllib.error.URLError):
                print("   ! Autoverteiler konnte nicht wieder eingeschaltet werden (Webseite: Autoverteiler: AN)")

    print("\n== Ergebnis")
    schlecht = 0
    for z in erg:
        urts = [z["urteil"]] + ([z["urteil_signal"]] if "urteil_signal" in z else [])
        print("   %-5s %-12s %s" % (z["stick"], z["kennung"], " / ".join(urts)))
        for u in urts:
            schlecht = max(schlecht, 2 if u == "FEHLER" else 1 if u == "SCHWACH" else 0)
    vgl = [z["bake"].get("esn0") for z in erg if z["bake"].get("esn0") is not None]
    if len(vgl) >= 2 and max(vgl) - min(vgl) > 4:
        print("   Hinweis: Es/N0 der Sticks weicht um %.1f dB ab - der schwaechste Stick/Antennenweg faellt auf (Kabel tauschen und erneut testen)." % (max(vgl) - min(vgl)))
    print("   %s" % ("alle Sticks in Ordnung" if schlecht == 0 else "mindestens ein Stick schwach oder ohne Empfang"))
    return schlecht


if __name__ == "__main__":
    sys.exit(main())
