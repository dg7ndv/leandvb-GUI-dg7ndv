#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dvb_direct.py - Tuner der Box (z.B. Octagon SF8008, ohne Enigma) DIREKT auf eine Frequenz stellen.

Laeuft AUF der Box (python3). Spricht /dev/dvb/adapterN/frontendM per ioctl an (libdvbv5/udev werden
nicht gebraucht). Vorher Enigma beenden:  init 4; killall -9 enigma2.sh enigma2     (danach: init 3)

  --info                              alle Frontends auflisten (Name/Typ) und beenden
  -f 0 --freq 10497.25 --sr 333       Tunen (RF-Frequenz in MHz, Symbolrate in kS/s), Lock anzeigen
  --lo 9750 --pol h                   LNB-LO (MHz) und Polarisation h=18V / v=13V
  --seconds 20                        Dauer (Lock-Anzeige / Stream), Default 20
  --udp 230.0.0.4:1234                Transportstrom (aus dvrN) per UDP senden, z.B. fuer VLC/leandvb-GUI
  --ts datei.ts                       Transportstrom in eine Datei schreiben
  --demux 0 --dvr 0                   Demux/DVR-Nummer (Default 0)
  --sys dvbs2|dvbs                    Uebertragungsnorm (Default dvbs2)

Dauerbetrieb mit Fernsteuerung (so nutzt die leandvb-GUI das Skript):
  --ctrl-port 55600                   laeuft weiter und nimmt UDP-Kommandos an (Default-Port 55600)
      PING                            -> OK dvb_direct
      TUNE <MHz> <kS/s> [h|v] [dvbs2|dvbs]   neu abstimmen, ohne Neustart
      STATUS                          -> LOCK/SNR/SIG/FREQ/SR/BYTES/PIDS (kompakt)
      STATUS2                         -> alles: zusaetzlich CC/TEI/PKTS/BER/DBM/CNR/PREBER/POSTBER/UNC
      STATS                           -> rohe DVBv5-Statistik (scale:wert) zur Treiberdiagnose
      UDP <ip:port>                   Ziel des Streams aendern
      STOP                            beenden (mit --restart-enigma startet Enigma wieder)
  Beispiel:
      python3 dvb_direct.py --stop-enigma --minimal --udp 230.0.0.9:1234 --ctrl-port 55600
      echo -n "TUNE 10496.201 500 h" | nc -u -w1 192.168.20.220 55600
"""
import argparse, ctypes, os, select, socket, struct, sys, time

libc = ctypes.CDLL(None, use_errno=True)

def _IOC(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr
def _IO(t, nr):        return _IOC(0, t, nr, 0)
def _IOR(t, nr, sz):   return _IOC(2, t, nr, sz)
def _IOW(t, nr, sz):   return _IOC(1, t, nr, sz)

class dtv_buffer(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("data", ctypes.c_ubyte * 32), ("len", ctypes.c_uint32),
                ("reserved1", ctypes.c_uint32 * 3), ("reserved2", ctypes.c_void_p)]
class dtv_stats(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("scale", ctypes.c_uint8), ("value", ctypes.c_int64)]
class dtv_fe_stats(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("len", ctypes.c_uint8), ("stat", dtv_stats * 4)]
class dtv_union(ctypes.Union):
    _pack_ = 1
    _fields_ = [("data", ctypes.c_uint32), ("buffer", dtv_buffer), ("st", dtv_fe_stats)]
class dtv_property(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("cmd", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 3),
                ("u", dtv_union), ("result", ctypes.c_int)]
class dtv_properties(ctypes.Structure):
    _fields_ = [("num", ctypes.c_uint32), ("props", ctypes.POINTER(dtv_property))]

FE_GET_INFO       = _IOR('o', 61, 168)
FE_SET_TONE       = _IO('o', 66)
FE_SET_VOLTAGE    = _IO('o', 67)
FE_READ_STATUS    = _IOR('o', 69, 4)
FE_READ_SNR       = _IOR('o', 72, 2)
FE_READ_SIGNAL    = _IOR('o', 71, 2)
FE_READ_BER       = _IOR('o', 70, 4)
FE_READ_UNCORRECTED = _IOR('o', 73, 4)   # DVBv3: nicht korrigierbare Bloecke
FE_SET_PROPERTY   = _IOW('o', 82, ctypes.sizeof(dtv_properties))
FE_GET_PROPERTY   = _IOR('o', 83, ctypes.sizeof(dtv_properties))
# DVBv5-Statistik (DTV_STAT_*); scale: 0 n/a, 1 dB (Wert in 0.001 dB), 2 relativ (0..65535), 3 Zaehler
STAT_PROPS = {"SIG": 62, "CNR": 63, "PREERR": 64, "PRETOT": 65, "POSTERR": 66, "POSTTOT": 67,
              "BLKERR": 68, "BLKTOT": 69}
DMX_SET_SOURCE    = _IOW('o', 49, 4)
DMX_START         = _IO('o', 41)
DMX_STOP          = _IO('o', 42)
DMX_SET_PES_FILT  = _IOW('o', 44, 20)
DMX_SET_BUFFER    = _IO('o', 45)

# dmx_output: 0=DECODER 1=TAP 2=TS_TAP 3=TSDEMUX_TAP ; dmx_input: 0=FRONTEND 1=DVR
# dmx_pes_type: 20=DMX_PES_OTHER ; flags: 1=CHECK_CRC 2=ONESHOT 4=IMMEDIATE_START
_DMX_OUT_NAMES = {1: "TAP", 2: "TS_TAP", 3: "TSDEMUX_TAP"}

def dmx_set_source(dmx, frontend=0):
    """Viele Enigma-/Vendor-Demuxe brauchen VOR dem Filter die Quelle (DMX_SOURCE_FRONT0 = 0).
    Fehlt sie, lehnen sie jeden PES-Filter mit EINVAL ab. Treiber ohne diesen Aufruf melden
    einen Fehler, den wir ignorieren."""
    v = ctypes.c_int(frontend)
    try:
        ioctl(dmx, DMX_SET_SOURCE, ctypes.byref(v))
        print("   Demux-Quelle gesetzt: DMX_SOURCE_FRONT%d" % frontend)
        return True
    except OSError as e:
        print("   Demux-Quelle DMX_SOURCE_FRONT%d: %s (wird ignoriert)" % (frontend, e))
        return False

def dmx_setup(dmx, out=None, pid=None, frontend=0):
    """Vollen Transportstrom auf den DVR legen. Welche Ausgabeart/Startweise/PID der Treiber will,
    ist je nach Box verschieden - daher der Reihe nach probieren, bis eine Kombination angenommen
    wird. Liefert die gefundene Beschreibung."""
    dmx_set_source(dmx, frontend)
    outs = [out] if out else [3, 2, 1]
    pids = [pid] if pid is not None else [0x2000, 0x0000]
    for pd in pids:
        for o in outs:
            for flags, start in ((4, False), (0, True)):
                filt = struct.pack("<HxxIIII", pd, 0, o, 20, flags)
                try:
                    ioctl(dmx, DMX_SET_PES_FILT, ctypes.create_string_buffer(filt, 20))
                except OSError as e:
                    print("   Demux PID 0x%04x %s flags=%d: abgelehnt (%s)" %
                          (pd, _DMX_OUT_NAMES.get(o, o), flags, e))
                    continue
                if start:
                    try:
                        ioctl(dmx, DMX_START)
                    except OSError as e:
                        print("   Demux PID 0x%04x %s DMX_START: abgelehnt (%s)" % (pd, _DMX_OUT_NAMES.get(o, o), e))
                        continue
                txt = "%s, PID 0x%04x, %s" % (_DMX_OUT_NAMES.get(o, o), pd,
                                              "IMMEDIATE_START" if flags == 4 else "DMX_START")
                print("   Demux konfiguriert: %s" % txt)
                if pd != 0x2000:
                    print("   Hinweis: nur PID 0x%04x statt des kompletten TS - der Treiber mag 0x2000 nicht." % pd)
                return txt
    raise OSError(22, "Demux nimmt keine der Kombinationen an")

DTV_TUNE, DTV_CLEAR, DTV_FREQUENCY, DTV_MODULATION, DTV_INVERSION = 1, 2, 3, 4, 6
DTV_SYMBOL_RATE, DTV_INNER_FEC, DTV_DELIVERY_SYSTEM, DTV_ROLLOFF, DTV_PILOT = 8, 9, 17, 22, 35
SYS_DVBS, SYS_DVBS2 = 5, 6
QPSK, FEC_AUTO, INVERSION_AUTO, ROLLOFF_AUTO, PILOT_AUTO = 0, 9, 2, 3, 2
SEC_VOLTAGE_13, SEC_VOLTAGE_18, SEC_VOLTAGE_OFF = 0, 1, 2
SEC_TONE_ON, SEC_TONE_OFF = 0, 1
FE_HAS_SIGNAL, FE_HAS_CARRIER, FE_HAS_VITERBI, FE_HAS_SYNC, FE_HAS_LOCK = 1, 2, 4, 8, 16

def ioctl(fd, req, arg=0):
    r = libc.ioctl(fd, ctypes.c_ulong(req), arg)
    if r < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return r

def fe_info(fd):
    buf = ctypes.create_string_buffer(168)
    ioctl(fd, FE_GET_INFO, ctypes.byref(buf))
    name = buf.raw[:128].split(b"\0")[0].decode("latin1")
    typ = struct.unpack("<I", buf.raw[128:132])[0]
    return name, typ     # typ: 0 = QPSK (DVB-S), 1 = QAM (DVB-C), 2 = OFDM (DVB-T), 3 = ATSC

_PROP_NAMES = {1: "DTV_TUNE", 2: "DTV_CLEAR", 3: "DTV_FREQUENCY", 4: "DTV_MODULATION",
               6: "DTV_INVERSION", 8: "DTV_SYMBOL_RATE", 9: "DTV_INNER_FEC",
               17: "DTV_DELIVERY_SYSTEM", 22: "DTV_ROLLOFF", 35: "DTV_PILOT"}

def set_props(fd, items, quiet=False):
    """Setzt eine Gruppe Properties. Scheitert der Block (EINVAL), wird jedes Property EINZELN
    gesetzt, damit sichtbar wird, welches der Treiber nicht annimmt (manche Vendor-Treiber kennen
    z.B. DTV_ROLLOFF/DTV_PILOT nicht)."""
    arr = (dtv_property * len(items))()
    for i, (cmd, val) in enumerate(items):
        arr[i].cmd = cmd
        arr[i].u.data = val
    p = dtv_properties(len(items), ctypes.cast(arr, ctypes.POINTER(dtv_property)))
    try:
        ioctl(fd, FE_SET_PROPERTY, ctypes.byref(p))
        return []
    except OSError as e:
        if quiet:
            raise
        print("Block mit %d Properties abgelehnt (%s) - setze einzeln:" % (len(items), e))
    bad = []
    for cmd, val in items:
        one = (dtv_property * 1)()
        one[0].cmd = cmd
        one[0].u.data = val
        pp = dtv_properties(1, ctypes.cast(one, ctypes.POINTER(dtv_property)))
        nm = _PROP_NAMES.get(cmd, str(cmd))
        try:
            ioctl(fd, FE_SET_PROPERTY, ctypes.byref(pp))
            print("   ok      %-20s = %d" % (nm, val))
        except OSError as e:
            print("   ABGELEHNT %-18s = %d   (%s)" % (nm, val, e))
            bad.append(nm)
    return bad

def get_stats(fd):
    """DVBv5-Statistik lesen -> {name: (scale, wert)}; nicht gelieferte Werte fehlen.
    Erst alle auf einmal, bei Fehler jedes einzeln (Vendor-Treiber kennen nicht alle)."""
    names = list(STAT_PROPS)
    out = {}
    def fetch(nlist):
        arr = (dtv_property * len(nlist))()
        for i, n in enumerate(nlist):
            arr[i].cmd = STAT_PROPS[n]
        pr = dtv_properties(len(nlist), ctypes.cast(arr, ctypes.POINTER(dtv_property)))
        ioctl(fd, FE_GET_PROPERTY, ctypes.byref(pr))
        for i, n in enumerate(nlist):
            st = arr[i].u.st
            if st.len >= 1 and st.stat[0].scale != 0:
                out[n] = (st.stat[0].scale, st.stat[0].value)
    try:
        fetch(names)
    except OSError:
        for n in names:
            try:
                fetch([n])
            except OSError:
                pass
    return out

def stats_debug(fd):
    """Diagnose: jede DVBv5-Statistik EINZELN abfragen und festhalten, WAS der Treiber antwortet
    (Wert, 'len=0', 'scale=0' = nicht verfuegbar oder der ioctl-Fehler) - zeigt, warum ein Wert leer ist."""
    out = []
    for n, cmd in STAT_PROPS.items():
        arr = (dtv_property * 1)()
        arr[0].cmd = cmd
        pr = dtv_properties(1, ctypes.cast(arr, ctypes.POINTER(dtv_property)))
        try:
            ioctl(fd, FE_GET_PROPERTY, ctypes.byref(pr))
        except OSError as e:
            out.append("%s=ioctl:%s" % (n, os.strerror(e.errno).replace(" ", "_") if e.errno else e))
            continue
        st = arr[0].u.st
        if st.len == 0:
            out.append("%s=len0" % n)
        elif st.stat[0].scale == 0:
            out.append("%s=scale0(n/a)" % n)
        else:
            out.append("%s=%d:%d" % (n, st.stat[0].scale, st.stat[0].value))
        if arr[0].result not in (0,):
            out[-1] += "(result=%d)" % arr[0].result
    return out

def stat_fields(fd):
    """Fertige STATUS-Felder (ohne Leerzeichen) aus der DVBv5-Statistik."""
    st = get_stats(fd)
    def db(n):
        v = st.get(n)
        if not v:
            return "-"
        if v[0] == 1:
            return "%.1f" % (v[1] / 1000.0)
        if v[0] == 2:
            return "%.0f%%" % (v[1] / 655.35)
        return "-"
    def ratio(e, t):
        ve, vt = st.get(e), st.get(t)
        if not ve or ve[0] != 3:
            return "-"
        if vt and vt[0] == 3 and vt[1] > 0:
            return "%.2e" % (ve[1] / float(vt[1]))
        return "%d" % ve[1]
    be = st.get("BLKERR")
    if be and be[0] == 3:
        unc = "%d" % be[1]
    else:                       # Treiber ohne DVBv5-Statistik: DVBv3-Zaehler als Ersatz
        u3 = read_u32(fd, FE_READ_UNCORRECTED)
        unc = "-" if u3 is None else "%d" % u3
    return {"DBM": db("SIG"), "CNR": db("CNR"), "PREBER": ratio("PREERR", "PRETOT"),
            "POSTBER": ratio("POSTERR", "POSTTOT"), "UNC": unc, "_raw": st}

def read_u16(fd, req):
    v = ctypes.c_uint16(0)
    try:
        ioctl(fd, req, ctypes.byref(v))
    except OSError:
        return None
    return v.value

def read_u32(fd, req):
    v = ctypes.c_uint32(0)
    try:
        ioctl(fd, req, ctypes.byref(v))
    except OSError:
        return None
    if v.value == 0xFFFFFFFF:      # "unbekannt" bei manchen Treibern
        return None
    return v.value

def open_pid(base, demux_idx, pid, out=3, frontend=0, bufsize=256 * 1024):
    """Oeffnet einen eigenen Demux-Filter fuer EINE PID. Bei DMX_OUT_TSDEMUX_TAP (3) kommen die
    188-Byte-TS-Pakete aus genau diesem Dateideskriptor (NICHT aus dvrN) - mehrere PIDs heisst
    also: mehrere Deskriptoren, die wir selbst zusammenfuehren."""
    fd = os.open("%s/demux%d" % (base, demux_idx), os.O_RDWR | os.O_NONBLOCK)
    try:
        ioctl(fd, DMX_SET_BUFFER, bufsize)
    except OSError:
        pass
    try:
        v = ctypes.c_int(frontend)
        ioctl(fd, DMX_SET_SOURCE, ctypes.byref(v))
    except OSError:
        pass
    filt = struct.pack("<HxxIIII", pid, 0, out, 20, 4)
    ioctl(fd, DMX_SET_PES_FILT, ctypes.create_string_buffer(filt, 20))
    return fd

def read_section(fd, timeout=5.0):
    """Liest so lange TS-Pakete dieser PID, bis eine vollstaendige Sektion beisammen ist."""
    end = time.time() + timeout
    buf = b""
    want = None
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.3)
        if not r:
            continue
        try:
            data = os.read(fd, 188 * 32)
        except OSError:
            continue
        for i in range(0, len(data) - 187, 188):
            pkt = data[i:i + 188]
            if pkt[0] != 0x47:
                continue
            pusi = pkt[1] & 0x40
            afc = (pkt[3] >> 4) & 3
            off = 4
            if afc in (2, 3):
                off += 1 + pkt[4]
            if afc == 2 or off >= 188:
                continue
            payload = pkt[off:]
            if pusi:
                if not payload:
                    continue
                ptr = payload[0]
                payload = payload[1 + ptr:]
                buf = payload
                if len(buf) >= 3:
                    want = ((buf[1] & 0x0F) << 8 | buf[2]) + 3
            elif buf:
                buf += payload
                if want is None and len(buf) >= 3:
                    want = ((buf[1] & 0x0F) << 8 | buf[2]) + 3
            if want and len(buf) >= want:
                return buf[:want]
    return None

def scan_pids(base, demux_idx, frontend, timeout=5.0):
    """PAT lesen -> PMT-PIDs, daraus PCR- und Elementarstrom-PIDs. Liefert die Menge aller PIDs,
    die fuer einen abspielbaren Transportstrom gebraucht werden."""
    pids = {0x0000, 0x0011}      # PAT + SDT (Service-Name, fuer die Rufzeichenanzeige)
    fd = open_pid(base, demux_idx, 0x0000, 3, frontend)
    try:
        pat = read_section(fd, timeout)
    finally:
        os.close(fd)
    if not pat or pat[0] != 0x00:
        print("   PAT nicht lesbar - streame nur PID 0x0000/0x0011")
        return pids, []
    progs = []
    body = pat[8:-4]
    for i in range(0, len(body) - 3, 4):
        prog = body[i] << 8 | body[i + 1]
        pmt = ((body[i + 2] & 0x1F) << 8) | body[i + 3]
        if prog != 0:
            progs.append((prog, pmt))
            pids.add(pmt)
    print("   PAT: %d Programm(e): %s" % (len(progs), ", ".join("Nr %d -> PMT 0x%04x" % p for p in progs)))
    for prog, pmt_pid in progs:
        fd = open_pid(base, demux_idx, pmt_pid, 3, frontend)
        try:
            pmt = read_section(fd, timeout)
        finally:
            os.close(fd)
        if not pmt or pmt[0] != 0x02:
            continue
        pcr = ((pmt[8] & 0x1F) << 8) | pmt[9]
        if pcr != 0x1FFF:
            pids.add(pcr)
        pil = ((pmt[10] & 0x0F) << 8) | pmt[11]
        i = 12 + pil
        es = []
        while i < len(pmt) - 4:
            stype = pmt[i]
            epid = ((pmt[i + 1] & 0x1F) << 8) | pmt[i + 2]
            elen = ((pmt[i + 3] & 0x0F) << 8) | pmt[i + 4]
            pids.add(epid)
            es.append((stype, epid))
            i += 5 + elen
        print("   PMT 0x%04x: PCR 0x%04x, Stroeme: %s" % (pmt_pid, pcr,
              ", ".join("0x%04x (Typ 0x%02x)" % (e[1], e[0]) for e in es)))
    return pids, progs

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-a", "--adapter", type=int, default=0)
    ap.add_argument("-f", "--frontend", type=int, default=0)
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--freq", type=float, help="RF-Frequenz in MHz (z.B. 10497.25)")
    ap.add_argument("--sr", type=float, help="Symbolrate in kS/s (z.B. 333)")
    ap.add_argument("--lo", type=float, default=9750.0, help="LNB-LO in MHz (Default 9750)")
    ap.add_argument("--pol", choices=("h", "v"), default="h")
    ap.add_argument("--sys", choices=("dvbs2", "dvbs"), default="dvbs2")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--udp", help="ip:port")
    ap.add_argument("--ts", help="Ausgabedatei")
    ap.add_argument("--demux", type=int, default=0)
    ap.add_argument("--dvr", type=int, default=0)
    ap.add_argument("--dmx-out", dest="dmx_out", type=int, choices=(1, 2, 3),
                    help="Demux-Ausgabeart erzwingen: 1=TAP 2=TS_TAP 3=TSDEMUX_TAP (sonst der Reihe nach probiert)")
    ap.add_argument("--pid", type=lambda v: int(v, 0), default=None,
                    help="PID fuer den Demux-Filter (Default 0x2000 = kompletter Transportstrom)")
    ap.add_argument("--ctrl-port", dest="ctrl_port", type=int,
                    help="UDP-Fernsteuerport - das Skript laeuft dann dauerhaft und nimmt TUNE/STATUS/STOP an")
    ap.add_argument("--stop-enigma", dest="stop_enigma", action="store_true",
                    help="Enigma vorher beenden (init 4 + killall) - sonst haelt es den Tuner belegt")
    ap.add_argument("--restart-enigma", dest="restart_enigma", action="store_true",
                    help="Enigma am Ende wieder starten (init 3)")
    ap.add_argument("--minimal", action="store_true",
                    help="nur die noetigsten Properties setzen (ohne ROLLOFF/PILOT) - fuer Treiber, die diese ablehnen")
    a = ap.parse_args()
    base = "/dev/dvb/adapter%d" % a.adapter

    if a.stop_enigma:
        # Runlevel-Wechsel ZUERST: in Runlevel 3 startet init enigma2 per respawn sofort neu.
        os.system("init 4")
        time.sleep(5)
        os.system("killall -9 enigma2.sh enigma2 2>/dev/null")
        time.sleep(2)
        print("Enigma gestoppt" if os.system("pidof enigma2 >/dev/null") != 0 else "WARNUNG: enigma2 laeuft noch")

    if a.info:
        for n in range(8):
            path = "%s/frontend%d" % (base, n)
            if not os.path.exists(path):
                continue
            try:
                fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            except OSError as e:
                print("%s: nicht oeffenbar (%s)" % (path, e)); continue
            try:
                name, typ = fe_info(fd)
                print("%s: %s  (Typ %s)" % (path, name, {0: "DVB-S", 1: "DVB-C", 2: "DVB-T", 3: "ATSC"}.get(typ, typ)))
            except OSError as e:
                print("%s: FE_GET_INFO fehlgeschlagen (%s)" % (path, e))
            os.close(fd)
        return 0

    if a.ctrl_port and a.freq is None:
        a.freq, a.sr = 10491.5, 1500.0        # Startwert, bis das erste TUNE ueber die Fernsteuerung kommt
    if a.freq is None or a.sr is None:
        ap.error("--freq und --sr angeben (oder --info)")

    fe = os.open("%s/frontend%d" % (base, a.frontend), os.O_RDWR | os.O_NONBLOCK)
    name, typ = fe_info(fe)
    print("Frontend %d: %s (Typ %s)" % (a.frontend, name, typ))
    if typ != 0:
        print("Das ist kein Satelliten-Frontend - anderes mit -f waehlen (--info zeigt alle).")
        return 2

    cur = {"freq": a.freq, "sr": a.sr, "pol": a.pol, "sys": a.sys}

    def tune():
        """Frontend auf cur[...] stellen. Der Treiber dieser Box lehnt DTV_PILOT ab (--minimal),
        darum werden die Properties bei Bedarf einzeln gesetzt (siehe set_props)."""
        if_khz = int(round((cur["freq"] - a.lo) * 1000.0))
        if if_khz < 600000 or if_khz > 2200000:
            print("Warnung: Zwischenfrequenz %d kHz sieht ungewoehnlich aus (LO %.1f MHz richtig?)" % (if_khz, a.lo))
        ioctl(fe, FE_SET_TONE, SEC_TONE_OFF)
        ioctl(fe, FE_SET_VOLTAGE, SEC_VOLTAGE_18 if cur["pol"] == "h" else SEC_VOLTAGE_13)
        time.sleep(0.2)
        set_props(fe, [(DTV_CLEAR, 0)])
        # Erst die Uebertragungsnorm allein (manche Treiber verlangen das vor allen anderen Werten),
        # dann die Parameter, dann TUNE als eigener Aufruf.
        set_props(fe, [(DTV_DELIVERY_SYSTEM, SYS_DVBS2 if cur["sys"] == "dvbs2" else SYS_DVBS)])
        params = [
            (DTV_FREQUENCY, if_khz),
            (DTV_MODULATION, QPSK),
            (DTV_SYMBOL_RATE, int(round(cur["sr"] * 1000))),
            (DTV_INNER_FEC, FEC_AUTO),
            (DTV_INVERSION, INVERSION_AUTO),
        ]
        if not a.minimal:
            params += [(DTV_ROLLOFF, ROLLOFF_AUTO), (DTV_PILOT, PILOT_AUTO)]
        set_props(fe, params)
        set_props(fe, [(DTV_TUNE, 0)])
        print("Tune: RF %.3f MHz -> ZF %d kHz, SR %d, %s, %s" % (cur["freq"], if_khz, int(cur["sr"] * 1000),
              "18V (H)" if cur["pol"] == "h" else "13V (V)", cur["sys"]), flush=True)

    def wait_lock(seconds=8.0):
        end_t = time.time() + seconds
        while time.time() < end_t:
            st = ctypes.c_uint32(0)
            ioctl(fe, FE_READ_STATUS, ctypes.byref(st))
            if st.value & FE_HAS_LOCK:
                return True
            time.sleep(0.1)
        return False

    out_fd = sock = None
    dest = None
    if a.ts:
        out_fd = open(a.ts, "wb")
    if a.udp:
        host, port = a.udp.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        dest = (host, int(port))

    ts_fds = []
    pids_now = []
    # Continuity-Zaehler: je PID letzter CC-Wert; Fehler = Sprung im 4-Bit-Zaehler (Paketverlust)
    cc_last = {}
    cc_stat = {"err": 0, "tei": 0, "pkts": 0}

    def count_cc(data):
        n = len(data) - (len(data) % 188)
        for o in range(0, n, 188):
            if data[o] != 0x47:
                continue
            b1, b2, b3 = data[o + 1], data[o + 2], data[o + 3]
            pid = ((b1 & 0x1f) << 8) | b2
            if pid == 0x1fff:
                continue
            cc_stat["pkts"] += 1
            if b1 & 0x80:
                cc_stat["tei"] += 1            # Transport Error Indicator (Tuner meldet Fehler)
            afc = (b3 >> 4) & 3
            cc = b3 & 0x0f
            if afc & 2 and data[o + 4] > 0 and data[o + 5] & 0x80:
                cc_last.pop(pid, None)         # Discontinuity-Indicator: Zaehler darf springen
            if not (afc & 1):
                continue                        # kein Payload: CC wird nicht erhoeht
            prev = cc_last.get(pid)
            cc_last[pid] = cc
            if prev is not None and cc != prev and cc != ((prev + 1) & 0x0f):
                cc_stat["err"] += 1

    def close_filters():
        for fd in ts_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        del ts_fds[:]

    def open_filters():
        """PAT/PMT auswerten und fuer jede gebrauchte PID einen Filter oeffnen (PID 0x2000 lehnt
        dieser Treiber ab, darum einzeln)."""
        close_filters()
        del pids_now[:]
        cc_last.clear()
        cc_stat.update(err=0, tei=0, pkts=0)
        if not (out_fd or sock):
            return
        if not wait_lock():
            print("   kein Lock - PIDs koennen nicht gelesen werden", flush=True)
            return
        if a.pid is not None:
            pidset = {a.pid}
            print("   Streame nur PID 0x%04x" % a.pid)
        else:
            pidset, _progs = scan_pids(base, a.demux, a.frontend)
        print("   PIDs: %s" % ", ".join("0x%04x" % x for x in sorted(pidset)), flush=True)
        for pd in sorted(pidset):
            try:
                ts_fds.append(open_pid(base, a.demux, pd, 3, a.frontend))
                pids_now.append(pd)
            except OSError as e:
                print("   PID 0x%04x: Filter abgelehnt (%s)" % (pd, e))

    tune()
    open_filters()

    ctrl = None
    if a.ctrl_port:
        ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ctrl.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ctrl.bind(("0.0.0.0", a.ctrl_port))
        print("Fernsteuerung: UDP 0.0.0.0:%d (TUNE/STATUS/UDP/PING/STOP)" % a.ctrl_port, flush=True)

    t_end = None if a.ctrl_port else time.time() + a.seconds
    last = 0.0
    nbytes = 0
    pend = b""
    stop = [False]
    last_status = {}

    def handle_cmd(line):
        """Ein Fernsteuer-Kommando ausfuehren, Antworttext zurueckgeben."""
        parts = line.strip().split()
        if not parts:
            return "ERR leer"
        cmd = parts[0].upper()
        if cmd == "PING":
            return "OK dvb_direct"
        if cmd == "STOP":
            stop[0] = True
            return "OK STOP"
        if cmd == "STATS":
            # Rohdiagnose je Property: scale:wert (scale 1=dB/1000, 2=relativ 0..65535, 3=Zaehler),
            # sonst Grund (ioctl-Fehler, len0, scale0 = Treiber liefert den Wert nicht)
            return "STATS " + " ".join(stats_debug(fe)) + " | FE_READ_SNR=%s FE_READ_SIGNAL=%s FE_READ_BER=%s FE_READ_UNCORRECTED=%s" % (
                read_u16(fe, FE_READ_SNR), read_u16(fe, FE_READ_SIGNAL), read_u32(fe, FE_READ_BER),
                read_u32(fe, FE_READ_UNCORRECTED))
        if cmd in ("STATUS", "STATUS2"):
            st = ctypes.c_uint32(0)
            ioctl(fe, FE_READ_STATUS, ctypes.byref(st))
            base = ("LOCK=%d SNR=%s SIG=%s FREQ=%.3f SR=%.0f POL=%s SYS=%s BYTES=%d" %
                    (1 if st.value & FE_HAS_LOCK else 0, read_u16(fe, FE_READ_SNR), read_u16(fe, FE_READ_SIGNAL),
                     cur["freq"], cur["sr"], cur["pol"], cur["sys"], nbytes))
            pids = "PIDS=" + ",".join("0x%04x" % x for x in pids_now)
            if cmd == "STATUS":              # kompakt (wie bisher)
                return base + " " + pids
            ber = read_u32(fe, FE_READ_BER)  # STATUS2: alle Werte
            sf = stat_fields(fe)
            return ("%s CC=%d TEI=%d PKTS=%d BER=%s DBM=%s CNR=%s PREBER=%s POSTBER=%s UNC=%s %s" %
                    (base, cc_stat["err"], cc_stat["tei"], cc_stat["pkts"],
                     "-" if ber is None else ber,
                     sf["DBM"], sf["CNR"], sf["PREBER"], sf["POSTBER"], sf["UNC"], pids))
        if cmd == "UDP":
            nonlocal_dest = parts[1] if len(parts) > 1 else ""
            if ":" not in nonlocal_dest:
                return "ERR UDP <ip:port>"
            h, pt = nonlocal_dest.rsplit(":", 1)
            return "OK UDP %s:%s" % (h, pt), (h, int(pt))
        if cmd == "TUNE":
            if len(parts) < 3:
                return "ERR TUNE <MHz> <kS/s> [h|v] [dvbs2|dvbs]"
            try:
                f = float(parts[1]); sr = float(parts[2])
            except ValueError:
                return "ERR TUNE: Zahl erwartet"
            cur["freq"] = f
            cur["sr"] = sr
            if len(parts) > 3 and parts[3].lower() in ("h", "v"):
                cur["pol"] = parts[3].lower()
            if len(parts) > 4 and parts[4].lower() in ("dvbs", "dvbs2"):
                cur["sys"] = parts[4].lower()
            tune()
            open_filters()
            st = ctypes.c_uint32(0)
            ioctl(fe, FE_READ_STATUS, ctypes.byref(st))
            return "OK TUNE %.3f %.0f LOCK=%d" % (f, sr, 1 if st.value & FE_HAS_LOCK else 0)
        return "ERR unbekannt: %s" % cmd

    try:
        while not stop[0] and (t_end is None or time.time() < t_end):
            if time.time() - last >= 1.0:
                last = time.time()
                st = ctypes.c_uint32(0)
                ioctl(fe, FE_READ_STATUS, ctypes.byref(st))
                sv = st.value
                snr = read_u16(fe, FE_READ_SNR)
                sig = read_u16(fe, FE_READ_SIGNAL)
                flags = "".join(c if sv & m else "-" for c, m in (("S", FE_HAS_SIGNAL), ("C", FE_HAS_CARRIER),
                                ("V", FE_HAS_VITERBI), ("Y", FE_HAS_SYNC), ("L", FE_HAS_LOCK)))
                line = "Status %s%s  SNR=%s SIG=%s  TS-Bytes=%d" % (flags, "  LOCK" if sv & FE_HAS_LOCK else "",
                       snr, sig, nbytes)
                # im Dauerbetrieb nur bei Aenderung ausgeben, sonst laeuft das Log voll
                key = (flags, nbytes > last_status.get("bytes", -1))
                if not a.ctrl_port or key != last_status.get("key"):
                    print(line, flush=True)
                    last_status["key"] = key
                last_status["bytes"] = nbytes
            socks = list(ts_fds)
            if ctrl is not None:
                socks.append(ctrl)
            r, _, _ = select.select(socks, [], [], 0.2) if socks else ([], [], [])
            for fd in r:
                if ctrl is not None and fd is ctrl:
                    try:
                        data, addr = ctrl.recvfrom(1024)
                    except OSError:
                        continue
                    res = handle_cmd(data.decode("utf-8", "replace"))
                    if isinstance(res, tuple):
                        res, dest = res[0], res[1]
                        if sock is None:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
                        open_filters()
                    print("Fernsteuerung %s:%d -> %s" % (addr[0], addr[1], res), flush=True)
                    try:
                        ctrl.sendto(res.encode("utf-8"), addr)
                    except OSError:
                        pass
                    continue
                try:
                    data = os.read(fd, 188 * 64)
                except OSError:
                    continue
                if not data:
                    continue
                nbytes += len(data)
                count_cc(data)
                if out_fd:
                    out_fd.write(data)
                if sock and dest:
                    pend += data
                    while len(pend) >= 1316:
                        sock.sendto(pend[:1316], dest)
                        pend = pend[1316:]
            if not socks:
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ioctl(fe, FE_SET_VOLTAGE, SEC_VOLTAGE_OFF)
        except OSError:
            pass
        close_filters()
        if out_fd:
            out_fd.close()
        if a.restart_enigma:
            print("Starte Enigma wieder (init 3) ...")
            os.system("init 3")
    return 0

if __name__ == "__main__":
    sys.exit(main())
