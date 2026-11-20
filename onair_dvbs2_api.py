#!/usr/bin/env python3
"""OnAir per Remote-API auf DVB-S2 einstellen (QO-100 / ZF-Frequenz).

Aufruf:  onair_dvbs2_api.py [FREQ [ABTASTRATE]] [Optionen]

FREQ: Standard MHz, auch mit Einheit möglich:
  ./onair_dvbs2_api.py 951.5                # 951,5 MHz
  ./onair_dvbs2_api.py 951,5                # Komma geht auch
  ./onair_dvbs2_api.py 951500k              # kHz
  ./onair_dvbs2_api.py 951500000hz          # Hz (Zahl >= 1e6 ohne Einheit gilt als Hz)

ABTASTRATE: Standard Msps, auch mit Einheit; auto (oder 0) = automatisch:
  ./onair_dvbs2_api.py 951.5 2.4            # 2,4 Msps
  ./onair_dvbs2_api.py 951.5 10             # 10 Msps
  ./onair_dvbs2_api.py 951.5 2400k          # 2,4 Msps
  ./onair_dvbs2_api.py 951.5 auto

Gerät: Standard "sdrplay" (Teilstring von Name/Typ/Board in /devices) oder Index:
  ./onair_dvbs2_api.py 951.5 2.4 --device sdrplay
  ./onair_dvbs2_api.py 951.5 2.4 --device 2
  ./onair_dvbs2_api.py 951.5 10 --hackrf              # Kurzform für --device hackrf
  ./onair_dvbs2_api.py 951.5 2.4 --rtl                # RTL-SDR (--device rtl2832)
  ./onair_dvbs2_api.py --list-devices

SDRplay RSP2 mit Antennenport (jeder Port ist bei OnAir ein eigener Geräteeintrag):
  ./onair_dvbs2_api.py 951.5 2.4 --ant a              # Antenna A
  ./onair_dvbs2_api.py 951.5 2.4 --ant b              # Antenna B
  ./onair_dvbs2_api.py 7.1 2.4 --ant hiz              # Hi-Z-Port (nur 1 kHz bis 30 MHz)
  ./onair_dvbs2_api.py 951.5 2.4 --device 12          # Antenna B direkt per Index
Die Grenzen (Frequenz, Abtastrate, Gain) des gewählten Geräts werden vor dem Start geprüft.

Gain:
  ./onair_dvbs2_api.py 951.5 2.4 --gain 32        # feste Verstärkung in dB, schaltet die AGC aus
  ./onair_dvbs2_api.py 951.5 2.4 --gain auto      # AGC an (experimentell)
  Ohne --gain bleibt die Einstellung in OnAir unverändert. Zum Schluss zeigt das Skript an, was OnAir
  für Gain und AGC meldet. 'auto' ist experimentell: die Doku kennt keinen Befehl zum Einschalten der
  AGC (POST /gain schaltet sie nur aus). Das Skript probiert zwei Varianten und prüft über /status; klappt
  es nicht, kommt eine Warnung. Der Button "Auto-Abgleich" (Verstärkung optimieren) hat in der API
  keine Entsprechung.

Weitere Beispiele:
  ./onair_dvbs2_api.py 739.5 --gain 32
  ./onair_dvbs2_api.py 951.5 --host 192.168.1.20 --token $ONAIR_TOKEN
  ./onair_dvbs2_api.py --list-modes         # Modi mit family-Nummern anzeigen
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_MHZ = 957.639


class DeutscheHilfe(argparse.RawDescriptionHelpFormatter):
    """Hilfeformat mit deutschem 'Aufruf:' statt 'usage:'."""

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, "Aufruf: " if prefix is None else prefix)


class DeutscherParser(argparse.ArgumentParser):
    """Fehlermeldungen mit 'Fehler:' statt 'error:'."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: Fehler: {message}\n")


def parse_freq(s):
    """'951.5', '951,5', '951.5M', '951500k', '951500000', '1.2g' -> MHz"""
    m = re.fullmatch(r"\s*([0-9]+(?:[.,][0-9]+)?)\s*(g|ghz|m|mhz|k|khz|h|hz)?\s*", s.lower())
    if not m:
        raise argparse.ArgumentTypeError(f"Frequenz nicht verständlich: {s!r}")
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit is None:
        unit = "hz" if val >= 1e6 else "mhz"
    mhz = val * {"g": 1e3, "ghz": 1e3, "m": 1, "mhz": 1, "k": 1e-3, "khz": 1e-3, "h": 1e-6, "hz": 1e-6}[unit]
    if mhz <= 0:
        raise argparse.ArgumentTypeError("Frequenz muss > 0 sein")
    return mhz


def parse_rate(s):
    """'2.4', '2,4', '2.4M', '2400k', '2400000', 'auto', '0' -> Hz (0 = automatisch)"""
    t = s.strip().lower()
    if t in ("auto", "automatisch"):
        return 0.0
    m = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)\s*(m|msps|msp|k|ksps|ksp|h|hz|sps)?", t)
    if not m:
        raise argparse.ArgumentTypeError(f"Abtastrate nicht verständlich: {s!r}")
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit is None:
        unit = "hz" if val >= 1e4 else "m"
    return val * {"m": 1e6, "msps": 1e6, "msp": 1e6, "k": 1e3, "ksps": 1e3, "ksp": 1e3,
                  "h": 1.0, "hz": 1.0, "sps": 1.0}[unit]


def fmt_rate(hz):
    return "auto" if not hz else f"{hz / 1e6:g} Msps"


def parse_gain(s):
    """'32', '32,5', 'auto' -> float in dB oder 'auto'"""
    t = s.strip().lower().replace(",", ".")
    if t in ("auto", "agc"):
        return "auto"
    try:
        v = float(t)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Gain nicht verständlich (Zahl in dB oder 'auto'): {s!r}")
    if v < 0:
        raise argparse.ArgumentTypeError("Gain muss >= 0 sein")
    return v


def find_key(obj, part):
    """Wert des ersten Schlüssels, der `part` enthält (ohne Groß-/Kleinschreibung), auch verschachtelt."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if part in str(k).lower() and not isinstance(v, (dict, list)):
                return v
        for v in obj.values():
            r = find_key(v, part)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, part)
            if r is not None:
                return r
    return None


def main():
    p = DeutscherParser(description=__doc__, formatter_class=DeutscheHilfe, add_help=False)
    p._positionals.title = "Parameter"
    p._optionals.title = "Optionen"
    p.add_argument("-h", "--hilfe", "--help", action="help", help="diese Hilfe anzeigen und beenden")
    p.add_argument("freq", nargs="?", type=parse_freq, default=None, metavar="FREQ",
                   help=f"Empfangsfrequenz (ZF), Standard MHz, optional mit Einheit k/M/G; "
                        f"ohne Angabe {DEFAULT_MHZ} MHz")
    p.add_argument("rate", nargs="?", type=parse_rate, default=None, metavar="ABTASTRATE",
                   help="Abtastrate, Standard Msps, optional mit Einheit k/M; auto oder 0 = automatisch "
                        "(ohne Angabe: automatisch)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--token", default=os.environ.get("ONAIR_TOKEN"),
                   help="nur nötig bei Zugriff von anderem Rechner (oder Env ONAIR_TOKEN)")
    p.add_argument("--mhz", type=float, default=None, help="wie FREQ, Frequenz in MHz (ältere Schreibweise)")
    p.add_argument("--msps", type=float, default=None,
                   help="wie ABTASTRATE, in Msps (ältere Schreibweise)")
    p.add_argument("--gain", type=parse_gain, default=None, metavar="DB|auto",
                   help="Gain in dB (schaltet AGC aus) oder 'auto' für AGC an (experimentell, siehe oben); "
                        "ohne Angabe bleibt die Einstellung in OnAir unverändert")
    p.add_argument("--device", default="sdrplay",
                   help="Gerät: Namensteil (z. B. sdrplay, rsp2, hackrf) oder Index; Standard sdrplay; "
                        "'keins' = Auswahl in OnAir nicht ändern")
    p.add_argument("--sdrplay", dest="device", action="store_const", const="sdrplay",
                   help="Kurzform für --device sdrplay (Standard)")
    p.add_argument("--hackrf", dest="device", action="store_const", const="hackrf",
                   help="Kurzform für --device hackrf")
    p.add_argument("--rtl", dest="device", action="store_const", const="rtl2832",
                   help="Kurzform für --device rtl2832 (RTL-SDR)")
    p.add_argument("--ant", choices=["a", "b", "hiz", "hi-z"], type=str.lower, default=None,
                   help="SDRplay-Antennenport: a, b oder hiz (wählt den passenden Geräteeintrag, "
                        "z. B. 'SDRplay RSP2 ... antenna B'); Hi-Z nur bis 30 MHz")
    p.add_argument("--list-devices", action="store_true", help="Radios/Quellen anzeigen und beenden")
    p.add_argument("--family", type=int, default=None,
                   help="family-Nummer des Modus DVB-S/S2, falls die Automatik ihn nicht findet")
    p.add_argument("--wait", type=int, default=30, help="Sekunden auf Lock warten (0 = nicht warten)")
    p.add_argument("--list-modes", action="store_true")
    a = p.parse_args()
    if a.freq is not None and a.mhz is not None:
        p.error("Frequenz entweder als FREQ oder mit --mhz angeben, nicht beides")
    a.mhz = a.freq if a.freq is not None else (a.mhz if a.mhz is not None else DEFAULT_MHZ)
    if a.rate is not None and a.msps is not None:
        p.error("Abtastrate entweder als ABTASTRATE oder mit --msps angeben, nicht beides")
    a.rate_hz = a.rate if a.rate is not None else (a.msps * 1e6 if a.msps is not None else 0.0)
    if a.rate_hz < 0:
        p.error("Abtastrate muss >= 0 sein")

    base = f"http://{a.host}:{a.port}/api/v1"

    def call(path, body=None, soft=False):
        """soft=True: HTTP-Fehler nicht abbrechen, sondern {"_error": ...} zurückgeben."""
        headers = {}
        if a.token:
            headers["Authorization"] = f"Bearer {a.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if soft:
                return {"_error": f"HTTP {e.code}: {msg}"}
            sys.exit(f"HTTP {e.code} bei {path}: {msg}")
        except urllib.error.URLError as e:
            sys.exit(f"OnAir nicht erreichbar ({e.reason}). Remote control API unter "
                     f"'Ansicht > Data feeds > Remote control API' aktiviert?")

    def as_list(x):
        if isinstance(x, dict):  # falls die Liste in einem Objekt steckt
            return next((v for v in x.values() if isinstance(v, list)), [])
        return x

    ANT_PHRASES = {"a": "antenna a", "b": "antenna b", "hiz": "hi-z", "hi-z": "hi-z"}

    def haystack(d):
        return " ".join(str(d.get(k, "")) for k in ("name", "kind", "board", "serial")).lower()

    def resolve_device(spec, ant):
        """Gerät aus /devices bestimmen -> (Index, Geräte-Dict oder None).
        Zahl = Index, sonst alle Suchwörter in Name/Typ/Board/Seriennr.;
        ant (a/b/hiz) filtert zusätzlich auf den SDRplay-Antennenport im Namen."""
        devs = as_list(call("/devices"))
        if spec.lower() in ("keins", "none", "-"):
            return None, None
        if spec.isdigit():
            idx = int(spec)
            return idx, next((d for d in devs if d.get("index") == idx), None)
        words = spec.lower().split()
        hits = [d for d in devs if all(w in haystack(d) for w in words)]
        if ant:
            ant_hits = [d for d in hits if ANT_PHRASES[ant] in haystack(d)]
            if hits and not ant_hits:
                names = "\n".join(f"  [{h.get('index')}] {h.get('name')}" for h in hits)
                sys.exit(f"--ant {ant} passt nicht zu {spec!r}: diese Geräte haben keinen Antennenport "
                         f"im Namen (nur SDRplay-Einträge 'antenna A/B', 'Hi-Z'). --ant weglassen.\n{names}")
            hits = ant_hits
        what = repr(spec) + (f" mit Antennenport {ant.upper()}" if ant else "")
        if not hits:
            lines = "\n".join("  " + json.dumps(d, ensure_ascii=False) for d in devs) or "  (keine)"
            sys.exit(f"Kein Gerät passend zu {what} gefunden. Verfügbar:\n{lines}")
        if len(hits) > 1:
            lines = "\n".join(f"  [{h.get('index')}] {h.get('name')}" for h in hits)
            print(f"Hinweis: {len(hits)} Geräte passen zu {what}, nehme das erste. "
                  f"Treffer:\n{lines}\n  -> genauer suchen (mehrere Wörter, --ant a|b|hiz) oder --device INDEX.")
        d = hits[0]
        print(f"Gerät: [{d.get('index')}] {d.get('name')} ({d.get('kind')}, {d.get('board')}, "
              f"Seriennr. {d.get('serial')})")
        return d.get("index"), d

    def check_limits(d):
        """Frequenz/Abtastrate gegen die vom Gerät gemeldeten Grenzen prüfen (0 = unbekannt)."""
        if not d:
            return
        f_hz = a.mhz * 1e6
        lo, hi = d.get("minFreqHz") or 0, d.get("maxFreqHz") or 0
        if hi and not (lo <= f_hz <= hi):
            sys.exit(f"{a.mhz:g} MHz liegt außerhalb des Bereichs dieses Geräts "
                     f"({lo / 1e6:g} bis {hi / 1e6:g} MHz): {d.get('name')}")
        rlo, rhi = d.get("minRateHz") or 0, d.get("maxRateHz") or 0
        if a.rate_hz and rhi and not (rlo <= a.rate_hz <= rhi):
            sys.exit(f"Abtastrate {fmt_rate(a.rate_hz)} liegt außerhalb des Bereichs dieses Geräts "
                     f"({rlo / 1e6:g} bis {rhi / 1e6:g} Msps): {d.get('name')}")
        glo, ghi = d.get("gainMinDb") or 0, d.get("gainMaxDb") or 0
        if isinstance(a.gain, float) and ghi and not (glo <= a.gain <= ghi):
            sys.exit(f"Gain {a.gain:g} dB liegt außerhalb des Bereichs dieses Geräts "
                     f"({glo:g} bis {ghi:g} dB): {d.get('name')}")

    if a.list_devices:
        for d in as_list(call("/devices")):
            print(json.dumps(d, ensure_ascii=False))
        return

    modes = as_list(call("/modes"))

    if a.list_modes:
        for m in modes:
            print(json.dumps(m, ensure_ascii=False))
        return

    family = a.family
    sub = None
    if family is None:
        for m in modes:
            text = json.dumps(m, ensure_ascii=False).lower()
            if "dvb-s2" in text or "dvb-s/s2" in text or "dvb-s" in text:
                family, sub = m.get("family"), m.get("sub")
                print(f"Modus gefunden: {json.dumps(m, ensure_ascii=False)}")
                break
    if family is None:
        sys.exit("DVB-S/S2 nicht in /modes gefunden. Mit --list-modes nachsehen und --family setzen.")

    def agc_is_on():
        v = find_key(call("/status", soft=True), "agc")
        return v if isinstance(v, bool) else None

    def set_agc_on():
        """AGC einschalten (experimentell). Die Doku nennt dafür keinen Befehl, POST /gain schaltet
        die AGC nur aus. Deshalb zwei plausible Varianten probieren und jeweils über /status prüfen.
        Rückgabe: True = AGC bestätigt an, False/None = nicht bestätigt."""
        state = agc_is_on()
        if state is True:
            return True
        for body in ({"agc": True}, {"auto": True}):
            call("/gain", body, soft=True)
            state = agc_is_on()
            if state is True:
                return True
        return state

    dev_index, dev = resolve_device(a.device, a.ant)
    check_limits(dev)

    # Modus lässt sich nur bei gestopptem Receiver ändern
    call("/stop", {})
    time.sleep(0.5)
    call("/mode", {"family": family, **({"sub": sub} if sub else {})})
    call("/frequency", {"mhz": a.mhz})
    call("/samplerate", {"hz": int(round(a.rate_hz))})
    agc_ok = None
    if a.gain == "auto":
        agc_ok = set_agc_on()
    elif a.gain is not None:
        call("/gain", {"db": a.gain})
    call("/start", {"device": dev_index} if dev_index is not None else {})

    if a.gain == "auto":
        gain_txt = ", Gain auto (AGC an)" if agc_ok else ", Gain auto: AGC NICHT bestätigt"
    elif a.gain is not None:
        gain_txt = f", Gain {a.gain:g} dB (AGC aus)"
    else:
        gain_txt = ", Gain/AGC unverändert"
    print(f"Gestartet: {a.mhz:g} MHz, Abtastrate {fmt_rate(a.rate_hz)}{gain_txt}")
    if a.gain == "auto" and not agc_ok:
        print("Warnung: Die Doku nennt keinen API-Befehl, der die AGC einschaltet (POST /gain schaltet sie nur "
              "aus). Bitte in OnAir die Checkbox AGC setzen. Gain wurde ggf. nicht geändert.", file=sys.stderr)

    st = call("/status", soft=True)
    cur_gain = next((st[k] for k in ("gain", "gainDb", "gain_db") if isinstance(st, dict) and k in st), None)
    cur_agc = find_key(st, "agc")
    if cur_gain is not None or isinstance(cur_agc, bool):
        agc_txt = {True: "an", False: "aus"}.get(cur_agc, "?")
        print(f"OnAir meldet: Gain {cur_gain if cur_gain is not None else '?'} dB, AGC {agc_txt}")

    if a.wait <= 0:
        return
    print("Warte auf Lock ...")
    end = time.time() + a.wait
    while time.time() < end:
        t = call("/telemetry")
        if t.get("locked"):
            print(f"LOCKED  SNR {t.get('snrDb')} dB  CFO {t.get('cfoHz')} Hz  "
                  f"FEC ok/bad {t.get('blocksOk')}/{t.get('blocksBad')}")
            return
        time.sleep(1)
    sys.exit("Kein Lock innerhalb der Wartezeit.")


if __name__ == "__main__":
    main()
