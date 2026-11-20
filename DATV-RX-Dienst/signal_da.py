#!/usr/bin/env python3
"""signal_da.py - ist auf einer QO-100-Frequenz ein Signal da? Messung direkt am RTL-Stick (DG7NDV)

Aufruf:  ./signal_da.py FREQ_MHZ SR_KS [--lo MHZ] [--gain DB] [--ppm N] [--index N] [--min DB]
Beispiel: ./signal_da.py 10497.262 333

Nimmt 1 s IQ mit 2,4 MS/s auf (Stick 300 kHz neben die Mitte, wegen der DC-Spitze), vergleicht die Leistung im
Signalband (Breite ~ 1,35 x SR) mit dem Rauschen links und rechts daneben. Ausgabe: Abstand in dB.
Rueckgabewert 0 = Signal da (Abstand >= --min, Standard 3 dB), 1 = kein Signal, 2 = Messfehler.
Der Stick muss frei sein (kein rtl_sdr/datvrx laeuft).
"""
import argparse, json, os, subprocess, sys
import numpy as np

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("freq", type=float)
ap.add_argument("sr", type=float)
ap.add_argument("--lo", type=float)
ap.add_argument("--gain", default="40")
ap.add_argument("--ppm", type=int)
ap.add_argument("--index", default="0")
ap.add_argument("--min", type=float, default=3.0)
a = ap.parse_args()

try:
    cfg = json.load(open(os.path.expanduser("~/.leandvb-GUI/parameters.json")))
except Exception:
    cfg = {}
lo = a.lo if a.lo else float(cfg.get("lnb_lo") or 9540)
ppm = a.ppm if a.ppm is not None else int(cfg.get("ppm") or 0)
rate = 2400000
off = 300e3
rf = (a.freq - lo) * 1e6 if a.freq > lo else a.freq * 1e6
tune = int(round(rf - off))

n = rate * 2                       # 1 s IQ = 2 Byte je Probe -> 2*rate Byte
try:
    r = subprocess.run(["rtl_sdr", "-d", a.index, "-f", str(tune), "-s", str(rate), "-g", a.gain, "-p", str(ppm),
                        "-n", str(rate), "-"], capture_output=True, timeout=20)
except Exception as e:
    print("FEHLER", e); sys.exit(2)
raw = np.frombuffer(r.stdout, dtype=np.uint8)
if raw.size < 200000:
    print("FEHLER keine Daten (Stick belegt?):", r.stderr.decode(errors="replace").strip().splitlines()[-1:]); sys.exit(2)
x = ((raw[: raw.size // 2 * 2].astype(np.float32) - 127.5) / 127.5).reshape(-1, 2)
x = x[:, 0] + 1j * x[:, 1]
nfft = 4096
m = len(x) // nfft
ps = (np.abs(np.fft.fftshift(np.fft.fft(x[: m * nfft].reshape(m, nfft) * np.hanning(nfft), axis=1), axes=1)) ** 2).mean(axis=0)
fr = (np.arange(nfft) - nfft // 2) * rate / nfft          # Hz relativ zur Stick-Mitte
bw = a.sr * 1e3 * 1.35
c = off                                                    # Station liegt +300 kHz ueber der Stick-Mitte
sig = ps[np.abs(fr - c) < bw / 2].mean()
# Rauschen links und rechts vom Signal getrennt messen; es zaehlt die ruhigere Seite (die andere kann im Auslauf der Bake oder eines Nachbarn liegen)
valid = (np.abs(fr) > 60e3) & (np.abs(fr) < rate / 2 * 0.9)
sides = [ps[(np.abs(fr - (c + k * bw)) < bw / 2) & valid] for k in (-1, 1)]
sides = [x.mean() for x in sides if x.size > 8]
noise = min(sides) if sides else ps.mean()
d = 10 * np.log10(sig / noise)
# Pegel in dBFS (Vollaussteuerung = 0 dBFS), Leistung im Signalband bzw. gleich breit im Rauschen daneben - nur relativ, nicht in dBm kalibriert
norm = nfft * float((np.hanning(nfft) ** 2).sum())
nb_sig = int((np.abs(fr - c) < bw / 2).sum())
p_sig = 10 * np.log10(ps[np.abs(fr - c) < bw / 2].sum() / norm)
p_noi = 10 * np.log10(noise * nb_sig / norm)
print("%.3f MHz %g kS: Signal %.1f dB ueber Rauschen (Band %.1f dBFS, Rauschen %.1f dBFS) -> %s" % (a.freq, a.sr, d, p_sig, p_noi, "DA" if d >= a.min else "nicht da"))
sys.exit(0 if d >= a.min else 1)
