#!/usr/bin/env python3
"""OnAir per Kommandozeile (onair-cli) auf DVB-S/S2 einstellen und als UDP-Stream ausgeben.

Baut aus den Parametern die Kommandozeile für onair-cli und startet sie:

  onair-cli --hackrf --standard dvbs --freq 951.5 --udp 230.0.0.6:1234 ...

Im Unterschied zu onair_dvbs2_api.py steuert dieses Programm KEIN laufendes OnAir-Fenster,
sondern startet eine eigene Instanz ohne GUI (Strg+C beendet sie).

Aufruf:  onair_dvbs2_cli.py [FREQ] [Optionen] [-- weitere onair-cli-Optionen]

FREQ: Standard MHz, auch mit Einheit möglich:
  ./onair_dvbs2_cli.py 951.5                  # 951,5 MHz
  ./onair_dvbs2_cli.py 951,5                  # Komma geht auch
  ./onair_dvbs2_cli.py 951500k                # kHz
  ./onair_dvbs2_cli.py 951500000hz            # Hz (Zahl >= 1e6 ohne Einheit gilt als Hz)

Weitere Beispiele:
  ./onair_dvbs2_cli.py 951.5 --lna 24 --vga 20 --amp      # HackRF-Verstärkung
  ./onair_dvbs2_cli.py 951.5 --udp 239.1.1.1:5000         # anderes Ziel
  ./onair_dvbs2_cli.py --list-devices                     # gefundene Radios anzeigen
  ./onair_dvbs2_cli.py 951.5 --abgleich                   # Auto-Abgleich (HackRF), dann Dauerlauf
  ./onair_dvbs2_cli.py 951.5 --api-port 8765              # Steuerung per HTTP, Neustart bei Änderung
  ./onair_dvbs2_cli.py 951.5 --secs 30                    # 30 Sekunden Testlauf (auch 5m, 2h, 1d)
  ./onair_dvbs2_cli.py 951.5                              # ohne --secs: dauerhaft, Strg+C beendet
  ./onair_dvbs2_cli.py 951.5 --dry-run                    # nur Kommandozeile anzeigen
  ./onair_dvbs2_cli.py 951.5 -- --sopt key wert           # alles nach -- geht unverändert an onair-cli

Gain:
  ./onair_dvbs2_cli.py 951.5 --gain auto             # keine Gain-Option, onair-cli regelt selbst
  ./onair_dvbs2_cli.py 951.5 --gain 32               # Gesamtverstärkung (Radios ohne LNA/VGA)
  ./onair_dvbs2_cli.py 951.5 --abgleich              # Auto-Abgleich: LNA/VGA automatisch suchen (HackRF)
  onair-cli selbst hat keinen "Auto-Abgleich" wie die GUI. --abgleich macht ihn im Skript nach: kurze
  Probeläufe (je 20 s, --abgleich-zeit) mit verschiedenen LNA- und VGA-Werten, bester Es/N0 gewinnt
  (bei Gleichstand die kleinere Verstärkung). Danach startet der normale Lauf mit diesen Werten.

Hinweise:
  * Alle Optionen, die dieses Programm an onair-cli weitergibt, gibt es in /usr/bin/onair-cli
    (geprüft an den Optionsnamen in der Binärdatei, onair-cli hat kein --help).
  * Die CLI kennt KEINE Optionen für SDRplay, Antennenport, Symbolrate bei DVB-S, AGC oder
    UDP-TTL. Dafür ist onair_dvbs2_api.py zuständig (steuert das laufende OnAir-Fenster).
  * Standard-Ziel ist 230.0.0.6:1234.
  * Steuerung im Betrieb: --api-port PORT öffnet eine kleine HTTP-Schnittstelle (nur dieser Rechner), mit der
    Frequenz und Verstärkung geändert werden (onair-cli startet dabei neu, ca. 6 s). Auch das API-Skript
    kann sie ansprechen: ./onair_dvbs2_api.py 437 --device keins --port 8765. Die Befehle (POST /frequency,
    /gain mit lna, vga, amp oder db, /stop, /start; GET /status, /telemetry) stehen in onair_anleitung.html.
  * Gerät: Standard --hackrf. Ein Netzwerk-Radio wählst du mit --net-radio TYP,HOST,PORT
    (zum Beispiel rtltcp,192.168.0.197,1234), dann entfällt --hackrf automatisch.
    Mit --no-hackrf und --radio schlüssel=wert (oder Optionen nach --) geht noch mehr.
  * Das Programm onair-cli wird gesucht: --bin, Umgebungsvariable ONAIR_CLI, PATH
    (onair-cli, dect2cli), dann neben diesem Skript.
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys

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
DEFAULT_UDP = "230.0.0.6:1234"
DEFAULT_STANDARD = "dvbs"
# onair-cli beendet sich ohne --secs von selbst nach 10 s (so beobachtet). "Dauerhaft" heißt daher: eine
# sehr lange Laufzeit übergeben, 7 Tage. Bewusst nicht größer, damit nichts überläuft, falls onair-cli
# die Zeit intern in Millisekunden in einer 32-Bit-Zahl hält.
ENDLESS_SECS = 7 * 24 * 3600
BIN_NAMES = ("onair-cli", "dect2cli")


# Auto-Abgleich: onair-cli hat keinen eigenen Abgleich (der Button der GUI fehlt in der CLI). Das Skript
# macht ihn nach: kurze Probeläufe mit verschiedenen LNA/VGA-Werten, ausgewertet wird die Statuszeile
# "Es/N0 11.8 dB  ok 97  bad 0  packets 2770  TS lock".
ABGLEICH_SECS = 20
ABGLEICH_LNA = [0, 8, 16, 24, 32, 40]       # Stufe 1: LNA durchfahren (VGA fest)
ABGLEICH_VGA = [10, 20, 30, 40]             # Stufe 2: VGA durchfahren (mit bestem LNA)
ABGLEICH_VGA_START = 20
ABGLEICH_TOLERANZ_DB = 0.3                  # innerhalb davon gewinnt die kleinere Gesamtverstärkung

STATUS_RE = re.compile(r"(?:Es/N0|SNR|MER)\s+(-?[0-9.]+)\s*dB(?:\s+ok\s+(\d+)\s+bad\s+(\d+))?")


def probe(base, lna, vga, amp, tail, secs):
    """Ein Probelauf: liefert (Es/N0 in dB oder None, bad-Blöcke)."""
    cmd = base + ["--lna", fmt_num(lna), "--vga", fmt_num(vga)] + (["--amp"] if amp else []) \
        + tail + ["--secs", str(secs)]
    best = None
    bad = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace")
    try:
        for line in proc.stdout:
            m = STATUS_RE.search(line)
            if m and "TS lock" in line:
                best = float(m.group(1))
                bad = int(m.group(3)) if m.group(3) else 0
    except KeyboardInterrupt:
        proc.terminate()
        raise
    finally:
        proc.wait()
    return best, bad


def abgleich(base, amp, tail, secs):
    """Gibt (lna, vga) der besten Einstellung zurück oder None, wenn nirgends ein Lock kam."""
    results = {}

    def run(lna, vga):
        if (lna, vga) in results:
            return
        print(f"  LNA {lna:>2} dB  VGA {vga:>2} dB ... ", end="", file=sys.stderr, flush=True)
        snr, bad = probe(base, lna, vga, amp, tail, secs)
        results[(lna, vga)] = (snr, bad)
        print("kein Lock" if snr is None else f"Es/N0 {snr:.1f} dB  (bad {bad})", file=sys.stderr)

    def pick():
        ok = {k: v for k, v in results.items() if v[0] is not None}
        if not ok:
            return None
        top = max(v[0] - 0.5 * v[1] for v in ok.values())
        near = [k for k, v in ok.items() if v[0] - 0.5 * v[1] >= top - ABGLEICH_TOLERANZ_DB]
        return min(near, key=lambda k: (k[0] + k[1], k[0]))

    print(f"Auto-Abgleich, Stufe 1: LNA ({secs} s je Probe)", file=sys.stderr)
    for lna in ABGLEICH_LNA:
        run(lna, ABGLEICH_VGA_START)
    best = pick()
    if best is None:
        return None
    print(f"Auto-Abgleich, Stufe 2: VGA bei LNA {best[0]} dB", file=sys.stderr)
    for vga in ABGLEICH_VGA:
        run(best[0], vga)
    return pick()



# --- Steuerungs-Port ---------------------------------------------------------------------------------
# onair-cli hat selbst keine Fernsteuerung (--serve ist nur ein Netzwerk-Tuner: lineup.m3u, /stream/1,
# HLS). Dieser Modus startet deshalb einen kleinen HTTP-Server, der die wichtigsten Befehle der
# GUI-Remote-API nachbildet. Bei jeder Änderung von Frequenz oder Verstärkung wird onair-cli mit den
# neuen Werten neu gestartet (rund 6 s, der UDP-Stream setzt kurz aus). So lässt sich auch
# onair_dvbs2_api.py mit --port gegen diese Instanz benutzen.

OPT_WITH_VALUE = ("--freq", "--lna", "--vga", "--gain")


def cmd_with(cmd, mhz, lna, vga, amp):
    """Kopie der Kommandozeile mit neuen Werten für Frequenz, LNA, VGA und Amp."""
    out, skip = [], False
    for x in cmd:
        if skip:
            skip = False
            continue
        if x in OPT_WITH_VALUE:
            skip = True
            continue
        if x == "--amp":
            continue
        out.append(x)
    i = out.index("--standard") + 2 if "--standard" in out else 1
    new = ["--freq", fmt_num(mhz)]
    if lna is not None:
        new += ["--lna", fmt_num(lna)]
    if vga is not None:
        new += ["--vga", fmt_num(vga)]
    if amp:
        new.append("--amp")
    return out[:i] + new + out[i:]


def split_gain(db):
    """Gesamtverstärkung in LNA (8-dB-Stufen, max 40) und VGA (2-dB-Stufen, max 62) aufteilen."""
    lna = min(40, 8 * round(min(40, db * 0.6) / 8))
    vga = max(0, min(62, 2 * round((db - lna) / 2)))
    return float(lna), float(vga)


class Steuerung:
    def __init__(self, cmd, mhz, lna, vga, amp):
        self.cmd, self.mhz, self.lna, self.vga, self.amp = cmd, mhz, lna, vga, amp
        self.lock = threading.Lock()
        self.proc = None
        self.want_run = True
        self.dirty = True            # True = (neu) starten
        self.started = 0.0
        self.last = {"t": 0.0, "snr": None, "locked": False, "ok": 0, "bad": 0}
        self.msg = ""

    # --- Prozess ---
    def _reader(self, proc):
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            m = STATUS_RE.search(line)
            if m:
                with self.lock:
                    self.last = {"t": time.time(), "snr": float(m.group(1)),
                                 "locked": "TS lock" in line,
                                 "ok": int(m.group(2) or 0), "bad": int(m.group(3) or 0)}

    def _stop_proc(self):
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(8)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self.proc = None
        with self.lock:
            self.last = {"t": 0.0, "snr": None, "locked": False, "ok": 0, "bad": 0}

    def loop(self):
        """Überwacht den Prozess: (Neu-)Start bei Änderungen, Meldung bei vorzeitigem Ende."""
        while True:
            time.sleep(0.2)
            with self.lock:
                dirty, want = self.dirty, self.want_run
                self.dirty = False
                mhz, lna, vga, amp = self.mhz, self.lna, self.vga, self.amp
            if dirty:
                self._stop_proc()
                if want:
                    cmd = cmd_with(self.cmd, mhz, lna, vga, amp)
                    print("Kommando:", shlex.join(cmd), file=sys.stderr)
                    self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                 text=True, errors="replace")
                    self.started = time.time()
                    threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
            elif self.proc and self.proc.poll() is not None:
                rc = self.proc.returncode
                self.proc = None
                if time.time() - self.started < 10:
                    print(f"onair-cli endete nach {time.time() - self.started:.0f} s mit Code {rc}; "
                          f"gestoppt. Neu starten mit POST /api/v1/start.", file=sys.stderr)
                    with self.lock:
                        self.want_run = False
                else:   # Laufzeit (7 Tage) abgelaufen: einfach weiter
                    with self.lock:
                        self.dirty = True

    def shutdown(self):
        self.want_run = False
        self._stop_proc()

    # --- API ---
    def gain_db(self):
        return (self.lna or 16) + (self.vga or 20) + (14 if self.amp else 0)

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def get(self, path):
        p = path.split("?")[0].rstrip("/")
        if p == "/api/v1/status":
            return {"running": self.running(), "frequencyHz": int(round(self.mhz * 1e6)),
                    "mode": "dvbs", "agc": False, "gainDb": self.gain_db(),
                    "lnaDb": self.lna, "vgaDb": self.vga, "amp": self.amp, "source": "onair-cli"}
        if p == "/api/v1/telemetry":
            with self.lock:
                l = dict(self.last)
            fresh = time.time() - l["t"] < 4
            return {"locked": bool(l["locked"] and fresh), "snrDb": l["snr"],
                    "cfoHz": None, "blocksOk": l["ok"], "blocksBad": l["bad"]}
        if p == "/api/v1/devices":
            return [{"index": 0, "name": "HackRF One (onair-cli)", "kind": "cli", "board": "hackrf",
                     "serial": "", "minRateHz": 10000000, "maxRateHz": 10000000, "gainMinDb": 0,
                     "gainMaxDb": 116, "minFreqHz": 1000000, "maxFreqHz": 6000000000, "selected": True}]
        if p == "/api/v1/modes":
            return [{"family": 0, "sub": "dvbs", "name": "DVB-S/S2"}]
        return None

    def post(self, path, body):
        p = path.split("?")[0].rstrip("/")
        with self.lock:
            if p == "/api/v1/stop":
                self.want_run, self.dirty = False, True
            elif p == "/api/v1/start":
                self.want_run, self.dirty = True, True
            elif p in ("/api/v1/mode", "/api/v1/samplerate"):
                pass        # Modus und Rate sind in onair-cli fest (dvbs, 10 Msps)
            elif p == "/api/v1/frequency":
                if "hz" in body:
                    mhz = float(body["hz"]) / 1e6
                elif "mhz" in body:
                    mhz = float(body["mhz"])
                else:
                    return 400, {"error": "hz oder mhz fehlt"}
                if not 1 <= mhz <= 6000:
                    return 400, {"error": "Frequenz außerhalb 1-6000 MHz"}
                self.mhz = mhz
                self.dirty = self.dirty or self.want_run
            elif p == "/api/v1/gain":
                if "db" in body:
                    self.lna, self.vga = split_gain(float(body["db"]))
                    self.amp = False
                else:
                    if "lna" in body:
                        self.lna = float(body["lna"])
                    if "vga" in body:
                        self.vga = float(body["vga"])
                    if "amp" in body:
                        self.amp = bool(body["amp"])
                if self.lna is not None and not 0 <= self.lna <= 40 or \
                   self.vga is not None and not 0 <= self.vga <= 62:
                    return 400, {"error": "LNA 0-40 dB, VGA 0-62 dB"}
                self.dirty = self.dirty or self.want_run
            else:
                return 404, {"error": "unbekannt"}
        return 200, {"ok": True}

    def serve(self, host, port):
        st = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                r = st.get(self.path)
                self._send(200 if r is not None else 404, r if r is not None else {"error": "unbekannt"})

            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    return self._send(400, {"error": "JSON-Objekt erwartet"})
                try:
                    code, obj = st.post(self.path, body)
                except (ValueError, TypeError):
                    code, obj = 400, {"error": "ungültiger Wert"}
                self._send(code, obj)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer((host, port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


DEVICE_LINE_RE = re.compile(r"(?i)\bfound\b|^device:|radio\(s\)|hackrf")
DEVICE_SKIP_RE = re.compile(r"(?i)radio library|not found|RtApi|^\[INFO\]|tuner$")


def list_devices(binary):
    """onair-cli ohne Optionen sucht alle Radios und beendet sich; Geräte-Zeilen sofort anzeigen."""
    if binary is None:
        sys.exit("onair-cli nicht gefunden. Mit --bin PFAD angeben oder ONAIR_CLI setzen.")
    print("Suche Radios (onair-cli prüft alle Treiber, das dauert einige Sekunden) ...", flush=True)
    t0 = time.time()
    seen, raw = [], []
    proc = subprocess.Popen([binary], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", stdin=subprocess.DEVNULL)
    timer = threading.Timer(90, proc.kill)
    timer.start()
    try:
        for ln in proc.stdout:
            ln = ln.strip()
            raw.append(ln)
            if ln and DEVICE_LINE_RE.search(ln) and not DEVICE_SKIP_RE.search(ln) and ln not in seen:
                seen.append(ln)
                print(f"  [{time.time() - t0:4.1f} s] {ln}", flush=True)
    except KeyboardInterrupt:
        proc.kill()
        print("\nAbgebrochen.")
        return 130
    finally:
        timer.cancel()
        proc.wait()
    if not seen:
        print("Keine Geräte gefunden. Rohausgabe von onair-cli:")
        print("\n".join(x for x in raw if x))
        return 1
    print(f"\nFertig nach {time.time() - t0:.1f} s. Auswahl in der CLI: --hackrf (Standard), "
          f"--net-radio TYP,HOST,PORT. SDRplay, RTL-SDR und Antennenport lassen sich in der CLI nicht "
          f"wählen (dafür das OnAir-Fenster mit onair_dvbs2_api.py --list-devices).")
    return 0


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


def parse_udp(s):
    """'230.0.0.6:1234' -> '230.0.0.6:1234' (geprüft)"""
    m = re.fullmatch(r"([^:\s]+):([0-9]{1,5})", s.strip())
    if not m or not (1 <= int(m.group(2)) <= 65535):
        raise argparse.ArgumentTypeError(f"UDP-Ziel muss die Form adresse:port haben (Port 1-65535): {s!r}")
    return f"{m.group(1)}:{int(m.group(2))}"


def parse_radio(s):
    if "=" not in s or s.startswith("="):
        raise argparse.ArgumentTypeError(f"--radio erwartet schlüssel=wert: {s!r}")
    return s


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


def parse_net_radio(s):
    """'rtltcp,192.168.0.197,1234' -> unverändert (geprüft)"""
    m = re.fullmatch(r"([A-Za-z0-9_-]+),([^,\s]+),([0-9]{1,5})", s.strip())
    if not m or not (1 <= int(m.group(3)) <= 65535):
        raise argparse.ArgumentTypeError(
            f"--net-radio erwartet TYP,HOST,PORT (zum Beispiel rtltcp,192.168.0.197,1234): {s!r}")
    return f"{m.group(1)},{m.group(2)},{int(m.group(3))}"


def parse_secs(s):
    """'30', '90s', '5m', '2h', '1d', 'endlos', '0' -> Sekunden (endlos/0 = ENDLESS_SECS)"""
    t = s.strip().lower()
    if t in ("endlos", "dauerhaft", "inf", "0"):
        return ENDLESS_SECS
    m = re.fullmatch(r"([0-9]+)\s*(s|m|h|d)?", t)
    if not m or int(m.group(1)) == 0:
        raise argparse.ArgumentTypeError(f"Laufzeit nicht verständlich (Sekunden, 5m, 2h, 1d oder endlos): {s!r}")
    secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}[m.group(2)]
    if secs > ENDLESS_SECS:
        raise argparse.ArgumentTypeError(f"Laufzeit zu lang (höchstens 7d, oder 'endlos'): {s!r}")
    return secs


def fmt_num(x):
    return f"{x:.6f}".rstrip("0").rstrip(".")


def find_bin(explicit):
    """Pfad zu onair-cli bestimmen oder None."""
    cands = [explicit] if explicit else []
    env = os.environ.get("ONAIR_CLI")
    if env:
        cands.append(env)
    cands.extend(BIN_NAMES)
    here = os.path.dirname(os.path.abspath(__file__))
    cands.extend(os.path.join(here, n) for n in BIN_NAMES)
    for c in cands:
        path = shutil.which(c)
        if path:
            return path
    return None


def main():
    p = DeutscherParser(description=__doc__, formatter_class=DeutscheHilfe, add_help=False)
    p._positionals.title = "Parameter"
    p._optionals.title = "Optionen"
    p.add_argument("-h", "--hilfe", "--help", action="help", help="diese Hilfe anzeigen und beenden")
    p.add_argument("freq", nargs="?", type=parse_freq, default=None, metavar="FREQ",
                   help=f"Empfangsfrequenz (ZF), Standard MHz, optional mit Einheit k/M/G; "
                        f"ohne Angabe {DEFAULT_MHZ} MHz")
    p.add_argument("--standard", default=DEFAULT_STANDARD, metavar="ID",
                   help=f"Mode-ID für onair-cli (Standard {DEFAULT_STANDARD})")

    g = p.add_argument_group("Gerät und Verstärkung")
    g.add_argument("--hackrf", action=argparse.BooleanOptionalAction, default=True,
                   help="HackRF verwenden (Standard an; --no-hackrf lässt die Option weg)")
    g.add_argument("--net-radio", type=parse_net_radio, default=None, metavar="TYP,HOST,PORT",
                   help="Netzwerk-Radio statt HackRF, z. B. rtltcp,192.168.0.197,1234 "
                        "(ersetzt --hackrf)")
    g.add_argument("--radio", action="append", type=parse_radio, default=[], metavar="SCHLÜSSEL=WERT",
                   help="Geräteeinstellung an onair-cli weiterreichen (mehrfach möglich)")
    g.add_argument("--lna", type=float, default=None, metavar="DB", help="HackRF-LNA-Verstärkung in dB")
    g.add_argument("--vga", type=float, default=None, metavar="DB", help="HackRF-VGA-Verstärkung in dB")
    g.add_argument("--amp", action="store_true", help="HackRF-Vorverstärker (14 dB) einschalten")
    g.add_argument("--gain", type=parse_gain, default=None, metavar="DB|auto",
                   help="Gesamtverstärkung in dB für Radios ohne LNA/VGA-Stufen; 'auto' übergibt gar keine "
                        "Verstärkungs-Option, die Regelung bestimmt dann onair-cli selbst "
                        "(ob das einer AGC entspricht, nennt die Doku nicht)")

    s = p.add_argument_group("Signal")
    s.add_argument("--bw", type=float, default=None, metavar="MHZ", help="Kanalbreite in MHz festlegen")
    s.add_argument("--autobw", action="store_true", help="Kanalbreite automatisch bestimmen")
    s.add_argument("--compute", choices=["cpu", "gpu", "auto"], default=None,
                   help="wo die Fehlerkorrektur läuft")

    o = p.add_argument_group("Ausgabe")
    o.add_argument("--udp", type=parse_udp, default=DEFAULT_UDP, metavar="ADRESSE:PORT",
                   help=f"Ziel für den Transportstrom (Standard {DEFAULT_UDP})")
    o.add_argument("--no-udp", action="store_true", help="keinen UDP-Stream senden")
    o.add_argument("--rtp", action="store_true", help="per RTP statt rohem UDP senden")
    o.add_argument("--no-null", action="store_true", help="Null-Pakete weglassen")
    o.add_argument("--ts", metavar="DATEI", default=None, help="Transportstrom zusätzlich in Datei schreiben")

    g.add_argument("--abgleich", "--auto-abgleich", action="store_true", dest="abgleich",
                   help="Auto-Abgleich vor dem Start (nur HackRF): probiert LNA/VGA-Stufen kurz durch, "
                        "misst Es/N0 und startet dann mit der besten Einstellung")
    g.add_argument("--abgleich-zeit", type=int, default=ABGLEICH_SECS, metavar="SEK",
                   help=f"Dauer je Probelauf beim Abgleich in Sekunden (Standard {ABGLEICH_SECS}, "
                        f"davon gehen rund 6 s fürs Starten ab)")

    r = p.add_argument_group("Ablauf")
    r.add_argument("--secs", type=parse_secs, default=None, metavar="ZEIT",
                   help="Laufzeit: Sekunden oder mit Einheit (90s, 5m, 2h, 1d); 'endlos' = bis Strg+C. "
                        "Standard: endlos (onair-cli selbst stoppt sonst nach 10 s)")
    r.add_argument("--api-port", "--steuerung", type=int, default=None, metavar="PORT", dest="api_port",
                   help="Steuerungs-Port öffnen (z. B. 8765): Frequenz und Verstärkung lassen sich im "
                        "laufenden Betrieb ändern (onair-cli startet dabei neu); auch mit "
                        "onair_dvbs2_api.py --port PORT --device keins")
    r.add_argument("--api-host", default="127.0.0.1", metavar="ADRESSE",
                   help="Adresse für --api-port (Standard 127.0.0.1, nur dieser Rechner; die API hat "
                        "KEINEN Token, 0.0.0.0 nur in vertrauenswürdigen Netzen)")
    r.add_argument("--list-devices", "--list", action="store_true", dest="list_devices",
                   help="gefundene Radios/Geräte anzeigen und beenden (onair-cli ohne Optionen)")
    r.add_argument("--bin", default=None, metavar="PFAD", help="Pfad zu onair-cli")
    r.add_argument("--dry-run", action="store_true", help="nur die Kommandozeile anzeigen, nichts starten")
    p.epilog = "Alles nach einem einzelnen -- wird unverändert an onair-cli angehängt."

    # Zusatzargumente selbst am ersten "--" abtrennen (argparse.REMAINDER würde sonst
    # auch normale Optionen hinter FREQ verschlucken)
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)

    if a.no_udp and a.rtp:
        p.error("--rtp braucht ein UDP-Ziel, geht nicht zusammen mit --no-udp")
    if a.gain == "auto" and (a.lna is not None or a.vga is not None or a.amp):
        p.error("--gain auto geht nicht zusammen mit --lna, --vga oder --amp")
    if a.abgleich:
        if a.net_radio or not a.hackrf:
            p.error("--abgleich gibt es nur für den HackRF (LNA/VGA)")
        if a.lna is not None or a.vga is not None or a.gain is not None:
            p.error("--abgleich schließt --lna, --vga und --gain aus (der Abgleich bestimmt LNA/VGA selbst)")
        if a.abgleich_zeit < 10:
            p.error("--abgleich-zeit muss mindestens 10 Sekunden sein")
    if a.bw is not None and a.autobw:
        p.error("--bw und --autobw schließen sich aus")
    if a.bw is not None and a.bw <= 0:
        p.error("--bw muss > 0 sein")

    mhz = a.freq if a.freq is not None else DEFAULT_MHZ

    binary = find_bin(a.bin)
    if binary is None and not a.dry_run:
        sys.exit("onair-cli nicht gefunden. Mit --bin PFAD angeben, Umgebungsvariable ONAIR_CLI setzen "
                 "oder das Programm in den PATH legen (Quellcodebaum: dect2cli).")

    if a.list_devices:
        return list_devices(binary)

    cmd = [binary or BIN_NAMES[0]]
    if a.net_radio:
        cmd += ["--net-radio", a.net_radio]
    elif a.hackrf:
        cmd.append("--hackrf")
    for kv in a.radio:
        cmd += ["--radio", kv]
    cmd += ["--standard", a.standard, "--freq", fmt_num(mhz)]
    if a.bw is not None:
        cmd += ["--bw", fmt_num(a.bw)]
    if a.autobw:
        cmd.append("--autobw")
    if a.abgleich:
        tail = (["--compute", a.compute] if a.compute else []) + extra
        n = len(ABGLEICH_LNA) + len(ABGLEICH_VGA) - 1
        if a.dry_run:
            print(f"Auto-Abgleich (nicht ausgeführt): bis zu {n} Probeläufe à {a.abgleich_zeit} s, "
                  f"LNA {ABGLEICH_LNA}, danach VGA {ABGLEICH_VGA}", file=sys.stderr)
        else:
            try:
                best = abgleich(cmd, a.amp, tail, a.abgleich_zeit)
            except KeyboardInterrupt:
                print("\nAbgleich abgebrochen.", file=sys.stderr)
                return 130
            if best is None:
                sys.exit("Auto-Abgleich: bei keiner Einstellung ein Lock. Frequenz, Antenne, Kabel und "
                         "--amp prüfen (HackRF nicht von einem anderen Programm belegt?).")
            a.lna, a.vga = float(best[0]), float(best[1])
            print(f"Auto-Abgleich: bestes Ergebnis LNA {best[0]} dB, VGA {best[1]} dB "
                  f"(später fest per --lna {best[0]} --vga {best[1]}).", file=sys.stderr)
    if a.lna is not None:
        cmd += ["--lna", fmt_num(a.lna)]
    if a.vga is not None:
        cmd += ["--vga", fmt_num(a.vga)]
    if a.amp:
        cmd.append("--amp")
    if a.gain == "auto":
        print("Hinweis: --gain auto übergibt keine Verstärkungs-Option, die Regelung bestimmt onair-cli.",
              file=sys.stderr)
    elif a.gain is not None:
        cmd += ["--gain", fmt_num(a.gain)]
    if a.compute:
        cmd += ["--compute", a.compute]
    if not a.no_udp:
        cmd += ["--udp", a.udp]
        if a.rtp:
            cmd.append("--rtp")
    if a.ts:
        cmd += ["--ts", a.ts]
    if a.no_null:
        cmd.append("--no-null")
    secs = a.secs if a.secs is not None else ENDLESS_SECS
    cmd += ["--secs", str(secs)]
    if secs == ENDLESS_SECS:
        print("Läuft dauerhaft, beenden mit Strg+C.", file=sys.stderr)
    cmd += extra

    print("Kommando:", shlex.join(cmd), file=sys.stderr)
    if a.dry_run:
        return 0

    if a.api_port is not None:
        if not 1 <= a.api_port <= 65535:
            p.error("--api-port: Port 1-65535")
        if a.gain is not None and a.gain != "auto":
            a.lna, a.vga = split_gain(a.gain)
        st = Steuerung(cmd, mhz, a.lna, a.vga, a.amp)
        try:
            st.serve(a.api_host, a.api_port)
        except OSError as e:
            sys.exit(f"Steuerungs-Port {a.api_host}:{a.api_port} nicht verfügbar: {e}")
        print(f"Steuerung auf http://{a.api_host}:{a.api_port}/api/v1/  "
              f"(z. B. ./onair_dvbs2_api.py 437 --device keins --port {a.api_port})", file=sys.stderr)
        def _term(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, _term)
        try:
            st.loop()
        except KeyboardInterrupt:
            pass
        finally:
            st.shutdown()
        return 130

    # Strg+C geht an die ganze Prozessgruppe, onair-cli beendet sich selbst; wir warten nur.
    proc = subprocess.Popen(cmd)
    while True:
        try:
            rc = proc.wait()
            break
        except KeyboardInterrupt:
            continue
    if rc < 0:
        return 128 - rc if rc != -signal.SIGINT else 130
    return rc


if __name__ == "__main__":
    sys.exit(main())
