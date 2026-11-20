#!/usr/bin/env python3
"""waechter_mqtt.py - QO-100-Schmalband/Breitband-Wächter: neue/verschwundene DATV-Signale per MQTT melden (DG7NDV)

Liest das BATC-Wideband-Spektrum (wss://eshail.batc.org.uk/wb/fft) und erkennt Signale wie die leandvb-GUI
(Kalibrierung 10490,5 MHz + 9 MHz, 5-s-Fenster, neu ab 50 % der Bilder, weg unter 20 % + 10 s Nachlauf, Bake getrennt).
Nur Auslesen - keine Steuerung, kein Zugriff auf Empfaenger.

Aufruf:  ./waechter_mqtt.py [--broker 192.168.0.10] [--port 1883] [--prefix qo100] [--user U --password P] [--quiet]

MQTT-Themen (Prefix Standard "qo100"):
  qo100/waechter/online     "online"/"offline" (retained, Last-Will)
  qo100/signal/neu          Ereignis, JSON: {"ereignis":"neu","freq_mhz":10492.757,"sr_ks":500,"bw_khz":690,"zeit":"2026-11-11T17:34:34"}
  qo100/signal/weg          Ereignis, JSON: {"ereignis":"weg", ...}   (erst nach 10 s Abwesenheit)
  qo100/signal/wieder       Ereignis, JSON: ein als weg markiertes Signal ist zurueck
  qo100/signale             Status ALLER Signale als JSON (retained), nur bei Aenderung (und beim Start/Neuverbinden):
                            {"zeit":..., "anzahl":2, "bake":true,
                             "signale":[{"id":10492.757,"freq_mhz":10492.757,"sr_ks":500,"bw_khz":690,"seit":"17:34:34","dauer_s":312,"zustand":"aktiv"}, ...]}
  qo100/signale/anzahl      Zahl der aktiven Signale (retained, nur bei Aenderung)
"""
import argparse, json, math, signal as sig, struct, sys, threading, time
from collections import deque
from datetime import datetime

import websocket
import paho.mqtt.client as mqtt

START, BW_MHZ, MIN_BW = 10490.5, 9.0, 80          # Kalibrierung wie in leandvb-GUI.py
BEACON, BEACON_TOL, BEACON_NOM = 10491.52, 0.06, 10491.500
SIG_TOL, WINDOW, GONE_S = 0.2, 5.0, 10
SR_STEPS, SR_LIMITS = (250, 333, 500, 1000, 2000), (305, 425, 750, 1500)
WS_URL = "wss://eshail.batc.org.uk/wb/fft"

lock = threading.RLock()
frames, beacon_frames = deque(), deque()
stable = []
state = {}                 # key -> {freq, bw, sr, born, gone_since}
stop = threading.Event()
last_msg = [time.time()]
ARGS = None
client = None


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def say(text):
    if not ARGS.quiet:
        print("%s  %s" % (datetime.now().strftime("%H:%M:%S"), text), flush=True)


def sr_from_bw(bw):
    for lim, sr in zip(SR_LIMITS, SR_STEPS):
        if bw < lim:
            return sr
    return SR_STEPS[-1]


def analyze(fft):
    db = [10.0 * math.log10(v if v > 0 else 1) for v in fft]
    thresh = sorted(db)[int(len(db) * 0.25)] + 2.5
    tmp, hits, in_sig, sx = [], 0, False, 0
    for x in range(len(db)):
        if db[x] > thresh and not in_sig:
            in_sig, sx = True, x
        elif db[x] <= thresh and in_sig:
            in_sig = False
            w = x - sx
            if w > 5:
                f = START + (((sx + w / 2) / len(db)) * BW_MHZ)
                bw = (w / len(db)) * BW_MHZ * 1000
                if bw > MIN_BW:
                    if abs(f - BEACON) > BEACON_TOL:
                        tmp.append((f, bw))
                    else:
                        hits += 1
    t = time.time()
    with lock:
        frames.append((t, tmp))
        while frames and frames[0][0] < t - 30:
            frames.popleft()
        if hits:
            beacon_frames.append(t)
        while beacon_frames and beacon_frames[0] < t - 30:
            beacon_frames.popleft()


def aggregate():
    t = time.time()
    with lock:
        fr = [s for (ts, s) in frames if ts >= t - WINDOW]
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


def pub(topic, payload, retain=False):
    if client is None:
        return
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    client.publish("%s/%s" % (ARGS.prefix, topic), payload, qos=1, retain=retain)


def event(kind, st):
    ev = {"ereignis": kind, "freq_mhz": st["freq"], "sr_ks": st["sr"], "bw_khz": st["bw"], "zeit": now_iso()}
    pub("signal/" + kind, ev)
    say("%-6s %.3f MHz  %d kS  (BW %d kHz)" % (kind.upper(), st["freq"], st["sr"], st["bw"]))


last_status = [None]
T0 = time.time()


def status(force=False):
    """Status aller Signale; veroeffentlicht nur bei Aenderung (oder force)"""
    t = time.time()
    if t - T0 < 6:                      # Einlaufzeit: erst die ersten BATC-Bilder auswerten
        return
    with lock:
        lst = [{"id": k, "freq_mhz": st["freq"], "sr_ks": st["sr"], "bw_khz": st["bw"],
                "seit": datetime.fromtimestamp(st["born"]).strftime("%H:%M:%S"),
                "dauer_s": int(t - st["born"]),
                "zustand": "weg" if st["gone_since"] is not None else "aktiv"}
               for k, st in sorted(state.items(), key=lambda kv: kv[1]["freq"]) if abs(st["freq"] - BEACON_NOM) > 0.5]
        bake = len(beacon_frames) > 0
    key = json.dumps([(s["id"], s["sr_ks"], s["zustand"]) for s in lst] + [bake])
    if not force and key == last_status[0]:
        return
    last_status[0] = key
    aktiv = sum(1 for s in lst if s["zustand"] == "aktiv")
    pub("signale", {"zeit": now_iso(), "anzahl": aktiv, "bake": bake, "signale": lst}, retain=True)
    pub("signale/anzahl", str(aktiv), retain=True)


def tick():
    t = time.time()
    res = aggregate()
    events = []
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
                    state[key] = {"freq": f, "bw": bw, "sr": sr_from_bw(bw), "born": t, "gone_since": None}
                    if abs(f - BEACON_NOM) > 0.5:
                        events.append(("neu", dict(state[key])))
                else:
                    st = state[key]
                    if st["gone_since"] is not None:
                        st["gone_since"] = None; st["born"] = t
                        events.append(("wieder", dict(st)))
                    st["freq"], st["bw"], st["sr"] = f, bw, sr_from_bw(bw)
                seen.add(key)
            for key, st in state.items():
                if key not in seen and st["gone_since"] is None:
                    st["gone_since"] = t
        for key in [k for k, st in state.items() if st["gone_since"] is not None and t - st["gone_since"] >= GONE_S]:
            st = state.pop(key)
            if abs(st["freq"] - BEACON_NOM) > 0.5:
                events.append(("weg", dict(st)))
    for kind, st in events:
        event(kind, st)
    status()


def on_connect(cl, userdata, flags, rc, *a):
    ok = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
    say("MQTT %s (%s:%d)" % ("verbunden" if ok else "Fehler %s" % rc, ARGS.broker, ARGS.port))
    if ok:
        pub("waechter/online", "online", retain=True)
        status(force=True)


def ws_loop():
    def on_msg(ws, msg):
        last_msg[0] = time.time()
        try:
            analyze(struct.unpack("<%dH" % (len(msg) // 2), msg))
        except Exception as e:
            say("Fehler analyze: %r" % (e,))
    while not stop.is_set():
        try:
            app = websocket.WebSocketApp(WS_URL, header=["Sec-WebSocket-Protocol: fft_m0dtslivetune"], on_message=on_msg)
            app.run_forever(ping_interval=15, ping_timeout=8)
        except Exception:
            pass
        time.sleep(5)


def main():
    global ARGS, client
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default="192.168.0.10")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--prefix", default="qo100")
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--quiet", action="store_true", help="keine Konsolenausgabe (Dienstbetrieb)")
    ARGS = ap.parse_args()
    try:                                                   # paho 2.x
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="qo100-waechter")
    except AttributeError:                                 # paho 1.x
        client = mqtt.Client(client_id="qo100-waechter")
    if ARGS.user:
        client.username_pw_set(ARGS.user, ARGS.password)
    client.will_set("%s/waechter/online" % ARGS.prefix, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.reconnect_delay_set(2, 30)
    client.connect_async(ARGS.broker, ARGS.port, 30)
    client.loop_start()
    threading.Thread(target=ws_loop, daemon=True).start()
    sig.signal(sig.SIGTERM, lambda *a: stop.set())
    say("Waechter laeuft (Broker %s:%d, Prefix %s)" % (ARGS.broker, ARGS.port, ARGS.prefix))
    try:
        while not stop.is_set():
            tick()
            if time.time() - last_msg[0] > 60:             # BATC liefert nichts mehr: Neustart durch systemd
                say("seit 60 s keine BATC-Daten - beende (systemd startet neu)")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    pub("waechter/online", "offline", retain=True)
    time.sleep(0.5)
    client.loop_stop()


if __name__ == "__main__":
    main()
