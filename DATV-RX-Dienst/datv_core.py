#!/usr/bin/env python3
"""datv_core.py - gemeinsamer Kern fuer die datvrx-Auswertung (DG7NDV): von der leandvb-GUI (datvRX-Spalten) und den Diensten ohne GUI benutzt.
Keine GUI-Abhaengigkeit, nur Standardbibliothek.

  TsInfo        Transportstrom auswerten: Dienstname + Provider (SDT, das "Rufzeichen"), Bitrate, Null-Anteil, TEI- und CC-Fehler
  mer_qpsk(hex) MER in dB aus einer "datvrx cells"-Zeile (QPSK)
  parse_status  datvrx-Statuszeile -> dict (std, mod, fec, extra, sr, esn0, ok, bad, lock)
  pegel(...)    Pegel am RTL-Stick in dB ueber Rauschen und dBFS (ruft signal_da.py auf; Stick muss frei sein)
"""
import collections, math, os, re, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
SIGDA = os.path.join(HERE, "signal_da.py")

STATUS_RE = re.compile(r"(DVB-S2?)\s+(\S+)\s+(\S+)((?: pilots| short)*)\s+([\d.]+)\s+Msym/s\s+Es/N0\s+(-?[\d.]+)\s*dB\s+ok\s+(\d+)\s+bad\s+(\d+)")


def parse_status(body):
    """body = Teil der datvrx-Statuszeile nach '[ t s]'; None, wenn es keine Lock-Zeile mit Es/N0 ist"""
    m = STATUS_RE.search(body)
    if not m:
        return None
    return {"std": m.group(1), "mod": m.group(2), "fec": m.group(3), "extra": m.group(4).strip(), "sr": float(m.group(5)) * 1000,
            "esn0": float(m.group(6)), "ok": int(m.group(7)), "bad": int(m.group(8)), "lock": "TS lock" in body}


def mer_qpsk(hexs):
    """MER (dB) aus den I/Q-Bytes einer 'datvrx cells'-Zeile (128 + 64 x Wert); Betraege -> keine Entscheidungsfehler bei QPSK"""
    try:
        b = bytes.fromhex(hexs.strip())
    except ValueError:
        return None
    v = [(x - 128) / 64.0 for x in b]
    if len(v) < 200:
        return None
    a = sum(abs(x) for x in v) / len(v)
    err = sum((abs(x) - a) ** 2 for x in v) / (len(v) / 2.0)
    return 10 * math.log10((2 * a * a) / err) if err > 0 else None


def mer_mean(values, skip=4, last=16, need=6):
    """Mittel der letzten Messwerte (die ersten nach dem Lock ueberspringen); None, solange zu wenige da sind"""
    m = list(values)[skip:]
    return sum(m[-last:]) / len(m[-last:]) if len(m) >= need else None


def _text(b):
    b = bytes(b)
    if b and b[0] < 0x20:                      # DVB-Zeichensatzkennung weglassen
        b = b[1:]
    return b.decode("utf-8", errors="replace").strip()


class TsInfo:
    """einfache Auswertung des Transportstroms: Dienstname/Provider (SDT), Bitrate, Null-Anteil, TEI, CC-Fehler"""

    def __init__(s):
        s.carry = b""
        s.service, s.provider = "", ""
        s.n = s.null = s.tei = s.cc_err = 0
        s.last_cc = {}
        s.win = collections.deque()          # (Zeit, Pakete, Null) fuer die Bitrate der letzten Sekunden
        s.sdt = None

    def feed(s, data):
        d = s.carry + data
        i = 0
        while len(d) - i >= 188:
            if d[i] != 0x47:
                j = d.find(b"\x47", i + 1)
                if j < 0:
                    i = len(d); break
                i = j
                continue
            s._pkt(d[i:i + 188]); i += 188
        s.carry = d[i:]
        t = time.time()
        s.win.append((t, s.n, s.null))
        while s.win and s.win[0][0] < t - 6:
            s.win.popleft()

    def _pkt(s, p):
        s.n += 1
        pid = ((p[1] & 0x1F) << 8) | p[2]
        if p[1] & 0x80:
            s.tei += 1
        afc, cc = (p[3] >> 4) & 3, p[3] & 15
        if pid == 0x1FFF:
            s.null += 1; return
        if afc & 1:
            last = s.last_cc.get(pid)
            if last is not None and cc != last and cc != (last + 1) & 15:
                s.cc_err += 1
            s.last_cc[pid] = cc
        if pid == 0x11 and afc & 1:
            off = 4 + (1 + p[4] if afc & 2 else 0)
            pl = p[off:]
            if p[1] & 0x40:
                s.sdt = bytearray(pl[1 + pl[0]:]) if pl else None
            elif s.sdt is not None:
                s.sdt += pl
            if s.sdt is not None and len(s.sdt) >= 3:
                ln = ((s.sdt[1] & 0x0F) << 8) | s.sdt[2]
                if len(s.sdt) >= 3 + ln:
                    s._parse_sdt(bytes(s.sdt[:3 + ln])); s.sdt = None

    def _parse_sdt(s, b):
        if b[0] not in (0x42, 0x46) or len(b) < 15:
            return
        i, end = 11, len(b) - 4
        while i + 5 <= end:
            dl = ((b[i + 3] & 0x0F) << 8) | b[i + 4]
            j, jend = i + 5, min(i + 5 + dl, end)
            while j + 2 <= jend:
                tag, ln = b[j], b[j + 1]
                if tag == 0x48 and ln >= 3:
                    k = j + 3
                    pl = b[k]; prov = b[k + 1:k + 1 + pl]
                    k += 1 + pl
                    nl = b[k]; name = b[k + 1:k + 1 + nl]
                    if not s.service:
                        s.service, s.provider = _text(name), _text(prov)
                j += 2 + ln
            i = jend

    def stats(s):
        """(Mbit/s brutto, Null-Anteil in %) ueber die letzten Sekunden"""
        if len(s.win) < 2:
            return 0.0, 0.0
        (t0, n0, z0), (t1, n1, z1) = s.win[0], s.win[-1]
        dt = max(t1 - t0, 0.001)
        return (n1 - n0) * 188 * 8 / dt / 1e6, 100.0 * (z1 - z0) / max(n1 - n0, 1)


def pegel(freq_mhz, sr_ks, lo=None, ppm=None, index=0, gain=None, timeout=15):
    """Pegel am RTL-Stick (1 s Messung): {"db": dB ueber Rauschen, "dbfs": Signalband, "rausch_dbfs": Rauschen} oder {} (nur bis 1000 kS)"""
    if sr_ks > 1000 or not os.path.isfile(SIGDA):
        return {}
    cmd = [SIGDA, "%.3f" % freq_mhz, str(int(sr_ks)), "--index", str(index)]
    if lo:
        cmd += ["--lo", "%.3f" % lo]
    if ppm is not None:
        cmd += ["--ppm", str(int(ppm))]
    if gain is not None:
        cmd += ["--gain", str(gain)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return {}
    m = re.search(r"Signal (-?[\d.]+) dB ueber Rauschen \(Band (-?[\d.]+) dBFS, Rauschen (-?[\d.]+) dBFS\)", r.stdout)
    return {"db": float(m.group(1)), "dbfs": float(m.group(2)), "rausch_dbfs": float(m.group(3))} if m else {}
