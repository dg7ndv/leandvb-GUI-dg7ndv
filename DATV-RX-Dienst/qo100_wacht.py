#!/usr/bin/env python3
"""qo100_wacht.py - neue/verschwundene QO-100-Signale melden und optional automatisch decodieren (DG7NDV)

Die Signalerkennung ist die der leandvb-GUI (batc_analyze_signals/batc_aggregate/batc_tick):
BATC-Spektrum per WebSocket, 5-s-Fenster, neu ab 50 % der Bilder, weg unter 20 %, Bake ausgeblendet.

Aufruf:  ./qo100_wacht.py [--decode] [--secs N] [--udp ADRESSE:PORT] [--log DATEI]
  (ohne Option)  nur Ereignisse ausgeben:  NEU / WEG mit Frequenz und Symbolrate
  --decode       fuer jedes aktuell vorhandene Signal nacheinander: erst mit signal_da.py am Stick pruefen, ob es
                 noch da ist, dann datvrx_rx.sh (Standard 30 s); Ergebnis Lock/Kein Lock mit Es/N0 melden.
                 Verschwindet das Signal waehrend des Tests, wird abgebrochen. Der Stick muss frei sein.
  --secs N       Testdauer je Signal in Sekunden (Standard 30)
  --udp A:P      Transportstrom waehrend des Tests per UDP ausgeben (Standard 230.0.0.20:1234, "aus" = nicht)
  --log DATEI    Ereignisse zusaetzlich an diese Datei anhaengen
Beenden mit Strg+C.
"""
import argparse, math, os, re, signal, struct, subprocess, sys, threading, time
from collections import deque

import websocket

START, BW_MHZ, MIN_BW = 10490.5, 9.0, 80          # Kalibrierung wie in leandvb-GUI.py
BEACON, BEACON_TOL, BEACON_NOM = 10491.52, 0.06, 10491.500
SIG_TOL, WINDOW, GONE_S = 0.2, 5.0, 10
SR_STEPS, SR_LIMITS = (250, 333, 500, 1000, 2000), (305, 425, 750, 1500)
HERE = os.path.dirname(os.path.abspath(__file__))

lock = threading.RLock()
frames = deque()
stable = []
state = {}            # key -> {freq, bw, sr, born, gone_since}
stop = threading.Event()
logfile = None


def ev(text):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    if logfile:
        with open(logfile, "a") as f:
            f.write(time.strftime("%Y-%m-%d ") + line + "\n")


def sr_from_bw(bw):
    for lim, sr in zip(SR_LIMITS, SR_STEPS):
        if bw < lim:
            return sr
    return SR_STEPS[-1]


def analyze(fft):
    db = [10.0 * math.log10(v if v > 0 else 1) for v in fft]
    thresh = sorted(db)[int(len(db) * 0.25)] + 2.5
    tmp, in_sig, sx = [], False, 0
    for x in range(len(db)):
        if db[x] > thresh and not in_sig:
            in_sig, sx = True, x
        elif db[x] <= thresh and in_sig:
            in_sig = False
            w = x - sx
            if w > 5:
                f = START + (((sx + w / 2) / len(db)) * BW_MHZ)
                bw = (w / len(db)) * BW_MHZ * 1000
                if bw > MIN_BW and abs(f - BEACON) > BEACON_TOL:
                    tmp.append((f, bw))
    now = time.time()
    with lock:
        frames.append((now, tmp))
        while frames and frames[0][0] < now - 30:
            frames.popleft()


def aggregate():
    now = time.time()
    with lock:
        fr = [s for (t, s) in frames if t >= now - WINDOW]
    if not fr:
        return None
    clusters = []
    for sigs in fr:
        used = set()
        for f, bw in sigs:
            best = None
            for i, c in enumerate(clusters):
                if i not in used and abs(c["f"] - f) <= SIG_TOL and (best is None or abs(c["f"] - f) < abs(clusters[best]["f"] - f)):
                    best = i
            if best is None:
                clusters.append({"f": f, "bw": bw, "n": 1}); used.add(len(clusters) - 1)
            else:
                c = clusters[best]
                c["f"] = (c["f"] * c["n"] + f) / (c["n"] + 1)
                c["bw"] = (c["bw"] * c["n"] + bw) / (c["n"] + 1)
                c["n"] += 1; used.add(best)
    return len(fr), clusters


def tick():
    now = time.time()
    res = aggregate()
    with lock:
        if res is not None:
            n, clusters = res
            new = []
            for c in sorted(clusters, key=lambda c: c["f"]):
                known = any(abs(f - c["f"]) <= SIG_TOL for f, _ in stable)
                if c["n"] / n >= (0.2 if known else 0.5) and not any(abs(f - c["f"]) <= SIG_TOL for f, _ in new):
                    new.append((round(c["f"], 3), round(c["bw"])))
            stable[:] = new
            seen = set()
            for f, bw in new:
                key = next((k for k, st in state.items() if abs(st["freq"] - f) <= SIG_TOL), None)
                if key is None:
                    key = f
                    state[key] = {"freq": f, "bw": bw, "sr": sr_from_bw(bw), "born": now, "gone_since": None, "done": False}
                    if abs(f - BEACON_NOM) > 0.5:
                        ev("NEU   %.3f MHz  %d kS" % (f, state[key]["sr"]))
                else:
                    st = state[key]
                    if st["gone_since"] is not None:
                        st["gone_since"] = None; st["done"] = False
                        ev("WIEDER %.3f MHz  %d kS" % (f, st["sr"]))
                    st["freq"], st["bw"], st["sr"] = f, bw, sr_from_bw(bw)
                seen.add(key)
            for key, st in state.items():
                if key not in seen and st["gone_since"] is None:
                    st["gone_since"] = now
        for key in [k for k, st in state.items() if st["gone_since"] is not None and now - st["gone_since"] >= GONE_S]:
            st = state.pop(key)
            if abs(st["freq"] - BEACON_NOM) > 0.5:
                ev("WEG   %.3f MHz  %d kS" % (st["freq"], st["sr"]))


def still_there(freq):
    with lock:
        return any(abs(st["freq"] - freq) <= SIG_TOL and st["gone_since"] is None for st in state.values())


LM_HOST = "192.168.0.103:8080"
MODCOD = {1: "QPSK 1/4", 2: "QPSK 1/3", 3: "QPSK 2/5", 4: "QPSK 1/2", 5: "QPSK 3/5", 6: "QPSK 2/3", 7: "QPSK 3/4",
          8: "QPSK 4/5", 9: "QPSK 5/6", 10: "QPSK 8/9", 11: "QPSK 9/10", 12: "8PSK 3/5", 13: "8PSK 2/3", 14: "8PSK 3/4",
          15: "8PSK 5/6", 16: "8PSK 8/9", 17: "8PSK 9/10"}


def ts_status():
    """Status der laufenden TS-Analyse (UDP 55556) als dict, sonst {}"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
        s.sendto(b"STATUS", ("127.0.0.1", 55556))
        txt = s.recv(4096).decode(errors="replace")
        return dict(kv.split("=", 1) for kv in txt.split() if "=" in kv)
    except Exception:
        return {}


def lm_probe(freq, sr, secs=20):
    """LongMynd (RF-Port TOP) auf freq/sr stellen und ModCod/Frequenz/Symbolrate ablesen - nur bei Nicht-Decodierung"""
    try:
        import json
        c = websocket.create_connection("ws://%s/" % LM_HOST, subprotocols=["control"], timeout=5)
        c.send("T0"); time.sleep(0.3)
        c.send("C%d,%d" % (round(freq * 1000 - 9540000), sr)); time.sleep(0.3); c.close()
        m = websocket.create_connection("ws://%s/" % LM_HOST, subprotocols=["monitor"], timeout=8)
        t0, best = time.time(), None
        while time.time() - t0 < secs:
            d = json.loads(m.recv())["packet"]; r = d["rx"]; ts = d.get("ts", {})
            if r["demod_state"] >= 3:
                best = (r, ts)
                if r["demod_state"] == 4:
                    break
        m.close()
        if not best:
            return "LongMynd: kein Lock (State %s, gemeldet %.0f kHz IF, SR %.0f)" % (r["demod_state"], r["frequency"], r["symbolrate"] / 1000.0)
        r, ts = best
        return "LongMynd: State %s, %s%s, SR %.0f kS, Frequenz %.3f MHz, MER %.1f, Dienst %r" % (
            r["demod_state"], MODCOD.get(int(r["modcod"]), "ModCod %s" % r["modcod"]), " mit Pilots" if r["pilot_symbols"] else "",
            r["symbolrate"] / 1000.0, (r["frequency"] + 9540000) / 1000.0, r["mer"] / 10.0, ts.get("service_name"))
    except Exception as e:
        return "LongMynd-Abfrage fehlgeschlagen: %s" % e


PROTO = os.path.join(HERE, "qo100_protokoll.csv")
PROTO_COLS = ["Datum", "Uhrzeit", "Frequenz_BATC_MHz", "Frequenz_RX_MHz", "SR_BATC_kS", "SR_RX_kS", "Standard", "Modulation", "FEC",
              "Pilots", "Framegroesse", "Rolloff", "EsN0_dB", "MER_dB", "Stick_dB_ueber_Rauschen", "Pegel_Signal_dBFS", "Pegel_Rauschen_dBFS", "Bake_Ablage_kHz", "Rahmen_ok", "Rahmen_bad", "TS_Pakete", "Lock_nach_s",
              "Dienst", "Provider", "Bitrate_Mbit", "Null_pct", "PCR_Mbit", "CC", "TEI", "CRC", "Video", "Audio",
              "LM_ModCod", "LM_MER_dB", "LM_VBER", "LM_BER", "LM_BCH_unkorr", "LM_LDPC", "Ergebnis", "Hinweis"]


def proto_write(row):
    if os.path.exists(PROTO) and open(PROTO, encoding="utf-8").readline().strip() != ";".join(PROTO_COLS):
        os.replace(PROTO, PROTO.replace(".csv", "_alt_%s.csv" % time.strftime("%Y%m%d-%H%M%S")))
    new = not os.path.exists(PROTO)
    with open(PROTO, "a", encoding="utf-8") as f:
        if new:
            f.write(";".join(PROTO_COLS) + "\n")
        f.write(";".join(str(row.get(c, "")).replace(";", ",") for c in PROTO_COLS) + "\n")


def mer_qpsk(hexs):
    """MER (dB) einer 'datvrx cells'-Zeile fuer QPSK: Abstand der Punkte zur idealen Lage (+-a, +-a), Betraege -> keine Entscheidungsfehler"""
    try:
        b = bytes.fromhex(hexs)
    except ValueError:
        return None
    v = [(x - 128) / 64.0 for x in b]
    if len(v) < 200:
        return None
    a = sum(abs(x) for x in v) / len(v)
    err = sum((abs(x) - a) ** 2 for x in v) / (len(v) / 2.0)
    return 10 * math.log10((2 * a * a) / err) if err > 0 else None


bake_dev = [None]       # Ablage der Bake am Stick in kHz (gemessen - Soll); Referenz fuer die Frequenzangabe


def bake_ref():
    """Bake 12 s empfangen und die Ablage des Traegers (gegen die erwarteten +300 kHz) merken"""
    p = subprocess.Popen([os.path.join(HERE, "datvrx_rx.sh"), "rtl-sdr", "10491.500", "1500", "--rs", "--cells", "64"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    offs = []

    def rd():
        for raw in iter(lambda: p.stderr.readline(), b""):
            for part in raw.decode(errors="replace").replace("\r", "\n").splitlines():
                m = re.match(r"datvrx carrier ([+-][\d.]+)", part)
                if m:
                    offs.append(float(m.group(1)))
    threading.Thread(target=rd, daemon=True).start()
    time.sleep(14)
    for sg in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sg)
        except Exception:
            pass
        time.sleep(1.5)
    if len(offs) >= 8:
        offs = sorted(offs[-20:]); med = offs[len(offs) // 2]
        bake_dev[0] = med / 1000.0 - 300.0
        ev("BAKE  Ablage am Stick %+.1f kHz (Traeger %+.1f kHz statt +300)" % (bake_dev[0], med / 1000.0))
    else:
        ev("BAKE  keine Referenz (kein Lock)")


def probe_av(udp):
    """Video-/Audio-Codec aus dem laufenden Strom (ffprobe)"""
    if not udp:
        return "", ""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-analyzeduration", "8M", "-probesize", "4M", "-show_entries",
                            "stream=codec_name,codec_type,width,height,avg_frame_rate,sample_rate,channels", "-of", "compact",
                            "udp://%s?fifo_size=5000000&overrun_nonfatal=1" % udp], capture_output=True, text=True, timeout=16)
    except Exception:
        return "", ""
    vid, aud = "", ""
    for ln in r.stdout.splitlines():
        d = dict(kv.split("=", 1) for kv in ln.split("|")[1:] if "=" in kv)
        if d.get("codec_type") == "video" and not vid:
            fr = d.get("avg_frame_rate", "0/1").split("/")
            fps = (float(fr[0]) / float(fr[1])) if len(fr) == 2 and float(fr[1]) else 0
            vid = "%s %sx%s %.0ffps" % (d.get("codec_name"), d.get("width"), d.get("height"), fps)
        if d.get("codec_type") == "audio" and not aud:
            aud = "%s %sHz %sch" % (d.get("codec_name"), d.get("sample_rate"), d.get("channels"))
    return vid, aud


def decode_one(st, secs, udp):
    """testet ein Signal; liefert (kurztext, Protokollzeile)"""
    f, sr = st["freq"], st["sr"]
    row = {"Datum": time.strftime("%Y-%m-%d"), "Uhrzeit": time.strftime("%H:%M:%S"), "Frequenz_BATC_MHz": "%.3f" % f, "SR_BATC_kS": sr}
    if sr <= 1000:                                    # signal_da.py misst nur, was in 2,4 MS/s passt
        r = subprocess.run([os.path.join(HERE, "signal_da.py"), "%.3f" % f, str(sr)], capture_output=True, text=True)
        ev("PRUEF %s" % (r.stdout.strip() or r.stderr.strip()))
        m = re.search(r"Signal (-?[\d.]+) dB ueber Rauschen \(Band (-?[\d.]+) dBFS, Rauschen (-?[\d.]+) dBFS\)", r.stdout)
        if m:
            row["Stick_dB_ueber_Rauschen"], row["Pegel_Signal_dBFS"], row["Pegel_Rauschen_dBFS"] = m.group(1), m.group(2), m.group(3)
        if r.returncode == 2:
            return "Messfehler", row
        if r.returncode == 1:             # BATC sieht das Signal, der Stick im 1-s-Fenster nicht (gepulst/schwach): trotzdem versuchen
            ev("      Stick misst nichts, BATC sieht es -> decodiere trotzdem (%d s)" % min(secs, 25))
            secs = min(secs, 25)
    cmd = [os.path.join(HERE, "datvrx_rx.sh"), "rtl-sdr", "%.3f" % f, str(sr), "--rs", "--cells", "1024"]
    sink = ["socat", "-u", "-", "UDP-DATAGRAM:%s,ip-multicast-ttl=1" % udp] if udp else ["cat"]
    try:
        import socket
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"RESET", ("127.0.0.1", 55556))
    except Exception:
        pass
    rx = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    sk = subprocess.Popen(sink, stdin=rx.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    info = {"last": "", "lock": None, "offset": None, "sr": None, "roll": None, "lock_t": None, "lines": [], "mer": [], "carrier_hz": None}
    err = rx.stderr

    def reader():
        for raw in iter(lambda: err.readline(), b""):
            for part in raw.decode(errors="replace").replace("\r", "\n").splitlines():
                if part.startswith("datvrx cells "):
                    if info["lock"]:
                        mv = mer_qpsk(part.split(" ", 2)[2].strip())
                        if mv is not None:
                            info["mer"].append(mv)
                    continue
                m = re.match(r"datvrx carrier ([+-][\d.]+)", part)
                if m:
                    info["carrier_hz"] = float(m.group(1)); continue
                if part.startswith("datvrx ["):
                    info["last"] = part
                    if "TS lock" in part and info["lock"] is None:
                        info["lock"] = part
                        info["lock_t"] = time.time()
                m = re.search(r"carrier found: ([\d.]+) Msym/s, ([+-]?\d+) kHz", part)
                if m:
                    info["sr"], info["offset"] = float(m.group(1)) * 1000, float(m.group(2))
                m = re.search(r"roll-off ([\d.]+), carrier ([+-]?[\d.]+) kHz", part)
                if m:
                    info["roll"], info["offset"] = m.group(1), float(m.group(2))
    threading.Thread(target=reader, daemon=True).start()
    t0 = time.time()
    why, q = "Zeit um", {}
    while time.time() - t0 < secs and not stop.is_set():
        time.sleep(1)
        if info["lock"] and time.time() - t0 > 8:
            q = ts_status()
            if q.get("signal") == "yes" and float(q.get("bitrate_mbit", 0) or 0) > 0:
                why = "TS-Analyse: %s / %s, %s Mbit/s, CC %s TEI %s" % (q.get("service"), q.get("provider"), q.get("bitrate_mbit"), q.get("cc_10s"), q.get("tei_10s"))
                break
        if not still_there(f):
            why = "Signal verschwunden"; break
    if q and q.get("signal") == "yes":
        row["Video"], row["Audio"] = probe_av(udp)
    for p in (rx, sk):
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except Exception:
            pass
    time.sleep(2)
    for p in (rx, sk):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
    s = info["lock"] or info["last"]
    m = re.search(r"datvrx \[\s*([\d.]+) s\] (DVB-S2?)\s+(\S+)\s+(\S+)((?: pilots| short)*)\s+([\d.]+) Msym/s\s+Es/N0 ([\d.-]+) dB\s+ok (\d+)\s+bad (\d+)\s+packets \d+\s+TS lock, TS packets (\d+)", s)
    if m:
        row.update({"Standard": m.group(2), "Modulation": m.group(3), "FEC": m.group(4), "Pilots": "ja" if "pilots" in m.group(5) else "nein", "Framegroesse": "kurz" if "short" in m.group(5) else "normal",
                    "SR_RX_kS": "%.0f" % (float(m.group(6)) * 1000), "EsN0_dB": m.group(7), "Rahmen_ok": m.group(8),
                    "Rahmen_bad": m.group(9), "TS_Pakete": m.group(10)})
    if info["sr"]:
        row.setdefault("SR_RX_kS", "%.0f" % info["sr"])
    off_khz = info["carrier_hz"] / 1000.0 if info["carrier_hz"] is not None else info["offset"]
    if off_khz is not None:
        dev = bake_dev[0] or 0.0          # Bake-Ablage herausrechnen (LNB/Stick), falls gemessen
        row["Frequenz_RX_MHz"] = "%.4f" % (f - 0.3 + (off_khz - dev) / 1000.0)
        if bake_dev[0] is not None:
            row["Bake_Ablage_kHz"] = "%.1f" % bake_dev[0]
    if len(info["mer"]) >= 8:             # QPSK: Mittel der Zeilen nach dem Einschwingen
        mv = info["mer"][4:]
        row["MER_dB"] = "%.1f" % (sum(mv) / len(mv))
    if info["roll"]:
        row["Rolloff"] = info["roll"]
    if info["lock_t"]:
        row["Lock_nach_s"] = "%.0f" % (info["lock_t"] - t0)
    if q:
        row.update({"Dienst": q.get("service", ""), "Provider": q.get("provider", ""), "Bitrate_Mbit": q.get("bitrate_mbit", ""),
                    "Null_pct": q.get("null_pct", ""), "PCR_Mbit": q.get("pcr_mbit", ""), "CC": q.get("cc_10s", ""),
                    "TEI": q.get("tei_10s", ""), "CRC": q.get("crc_10s", "")})
    ok = bool(info["lock"]) and "TS-Analyse" in why
    row["Ergebnis"] = "decodiert" if ok else ("Lock ohne TS-Bestaetigung" if info["lock"] else "kein Lock")
    row["Hinweis"] = why
    mm = re.search(r"datvrx \[\s*([\d.]+) s\] (.*)", s)
    return ("LOCK  " if info["lock"] else "kein Lock  ") + (mm.group(2) if mm else "(keine Statuszeile)") + "  [%s]" % why, row


tested = [0]
summary = []


def worker(secs, udp):
    last_ref = 0
    while not stop.is_set():
        try:                                             # Zapper (VLC-Umschalter) hat den Stick: Waechter pausiert
            if os.path.exists("/tmp/qo100_zapper.lock"):
                os.kill(int(open("/tmp/qo100_zapper.lock").read().strip() or 0), 0)
                time.sleep(3)
                continue
        except (OSError, ValueError):
            pass
        if time.time() - last_ref > 1800:            # Bake-Referenz: beim Start und alle 30 min
            last_ref = time.time()
            bake_ref()
        todo = None
        with lock:
            for st in sorted(state.values(), key=lambda s: s["born"]):
                if not st["done"] and st["gone_since"] is None and abs(st["freq"] - BEACON_NOM) > 0.5:
                    todo = st; break
        if not todo:
            time.sleep(1); continue
        ev("TEST  %.3f MHz  %d kS" % (todo["freq"], todo["sr"]))
        res, row = decode_one(todo, secs, udp)
        ev("ERGEBNIS %.3f MHz %d kS: %s" % (todo["freq"], todo["sr"], res))
        if res.startswith("kein Lock") and not stop.is_set():
            lm = lm_probe(todo["freq"], todo["sr"])
            ev("      " + lm)
            row["Hinweis"] = row.get("Hinweis", "") + " | " + lm
        if res == "Messfehler":          # Stick belegt o.ae.: spaeter noch einmal versuchen
            time.sleep(10)
            continue
        if row.get("Ergebnis"):
            proto_write(row)
            tested[0] += 1
            summary.append((row.get("Ergebnis"), row.get("Dienst", ""), row.get("SR_BATC_kS"), row.get("Frequenz_BATC_MHz")))
            ev("TESTS %d" % tested[0])
        todo["done"] = True


def main():
    global logfile
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decode", action="store_true")
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--udp", default="230.0.0.20:1234")
    ap.add_argument("--log")
    a = ap.parse_args()
    logfile = a.log
    signal.signal(signal.SIGINT, signal.default_int_handler)       # aus dem Hintergrund gestartet wird SIGINT sonst ignoriert
    signal.signal(signal.SIGTERM, lambda *x: stop.set())
    udp = None if a.udp == "aus" else a.udp

    def run():
        while not stop.is_set():
            try:
                app = websocket.WebSocketApp("wss://eshail.batc.org.uk/wb/fft",
                                             header=["Sec-WebSocket-Protocol: fft_m0dtslivetune"],
                                             on_message=lambda ws, msg: analyze(struct.unpack("<%dH" % (len(msg) // 2), msg)))
                app.run_forever(ping_interval=15, ping_timeout=8)
            except Exception:
                pass
            time.sleep(5)
    threading.Thread(target=run, daemon=True).start()
    if a.decode:
        threading.Thread(target=worker, args=(a.secs, udp), daemon=True).start()
    ev("Wächter läuft%s (Strg+C beendet)" % (", mit Decodierung" if a.decode else ""))
    try:
        while not stop.is_set():
            tick(); time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
    if a.decode and tested[0]:
        ok = sum(1 for r in summary if r[0] == "decodiert")
        ev("ZUSAMMENFASSUNG: %d Tests, %d decodiert, %d nicht (Protokoll %s)" % (tested[0], ok, tested[0] - ok, PROTO))
        for r in summary:
            if r[0] != "decodiert":
                ev("   nicht decodiert: %s MHz %s kS (%s)" % (r[3], r[2], r[0]))


if __name__ == "__main__":
    main()
