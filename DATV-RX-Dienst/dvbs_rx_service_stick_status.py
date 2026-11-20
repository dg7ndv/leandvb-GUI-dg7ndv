#!/usr/bin/env python3
"""dvbs_rx_service_stick_status.py - Schnellstatus aller RTL-SDR-Sticks am Raspberry Pi (RTL-SDR-datvRX-Dienst). Nur lesen, es wird nichts umgestellt.

Aufruf:  ./dvbs_rx_service_stick_status.py [--pi 192.168.0.102] [--port 8554] [--erwartet 4] [--usb] [--ssh dg7ndv@IP] [--watch SEK] [--json]

Zeigt je Stick: Kennung, Typ (Hersteller/Modell, z.B. NooElec NESDR Nano 2 - per ssh aus sysfs, --kein-typ schaltet es aus), frei/belegt, Signal, Lock, Es/N0, MER, Bitrate, Rufzeichen, Bake-Offset. Mit --usb zusaetzlich (per ssh) die Zahl der
am Pi erkannten RTL-Geraete, USB-Fehler im Kernel-Log und Unterspannung. --watch SEK wiederholt die Anzeige alle SEK Sekunden.
Rueckgabe: 0 = alle erwarteten Sticks da und Dienst laeuft, 1 = ein Stick fehlt/hat Bake-Fehler, 2 = Dienst nicht erreichbar.
"""
import argparse, json, re, subprocess, sys, time, urllib.error, urllib.request


def get(base, path, timeout=6):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.load(r)


def usb(ziel):
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", ziel,
                            "echo L=$(lsusb | grep -ci 0bda:2838); echo T=$(vcgencmd get_throttled 2>/dev/null); "
                            "echo E=$(dmesg 2>/dev/null | grep -ci 'error -110\\|not accepting address\\|unable to enumerate')"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    t = r.stdout
    m = {k: re.search(k + r"=(\S*)", t) for k in ("L", "T", "E")}
    if not m["L"]:
        return None
    return {"geraete": int(m["L"].group(1) or 0), "throttled": (m["T"].group(1) if m["T"] else "").replace("throttled=", "") or "?", "usb_fehler": int(m["E"].group(1) or 0) if m["E"] else None}


def schein(s):
    """Lock ohne brauchbaren Empfang: Es/N0 unter 2 dB UND (fast) kein Transportstrom (Es/N0 allein schwankt, mit Datenstrom ist der Empfang echt)"""
    e = s.get("empfang") or {}
    return bool(s.get("lock") and e.get("es_n0_db") is not None and e["es_n0_db"] < 2 and (e.get("bitrate_mbit_s") or 0) < 0.05)


def typen(ziel):
    """Hersteller/Typ der RTL-Sticks am Pi aus sysfs (nur lesen, kein Geraet wird geoeffnet): Liste (Seriennummer, 'Hersteller Typ')"""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", ziel,
                            "for d in /sys/bus/usb/devices/*; do [ \"$(cat $d/idVendor 2>/dev/null)\" = 0bda ] && echo \"$(cat $d/serial 2>/dev/null)|$(cat $d/manufacturer 2>/dev/null)|$(cat $d/product 2>/dev/null)\"; done"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for l in r.stdout.splitlines():
        p = l.split("|")
        if len(p) == 3 and p[2]:
            out.append((p[0], ("%s %s" % (p[1], p[2])).strip()))
    return out


def typ_von(kennung, tl):
    """Typ zu einer Kennung des Dienstes: Seriennummer, sonst (Kennung = Nummer) das Geraet mit nicht eindeutiger/nummerischer Seriennummer"""
    kennung = str(kennung)
    for sn, t in tl:
        if sn == kennung:
            return t
    sers = [sn for sn, _t in tl]
    unklar = [t for sn, t in tl if sn.isdigit() or sers.count(sn) > 1 or not sn]
    if kennung.isdigit():
        return unklar[0] if len(unklar) == 1 else "?"
    return "NICHT AM USB"                                   # Seriennummer des Dienstes gibt es am Pi nicht (mehr): Stick abgefallen/nicht erkannt


def zeile(s, typ="?"):
    sg, e, b, d = s.get("signal") or {}, s.get("empfang") or {}, s.get("bake") or {}, s.get("dienst") or {}
    f = sg.get("freq_mhz")
    zust = "frei" if s.get("frei") else ("fest" if s.get("fest") else "belegt")
    if schein(s):
        w = "SCHEINLOCK? Es/N0 %s dB, %s Mbit/s - kein brauchbarer Empfang (Stick/Antennenweg pruefen)" % (e.get("es_n0_db"), e.get("bitrate_mbit_s"))
    elif s.get("lock"):
        w = "LOCK %s %s  Es/N0 %s dB  MER %s dB  %s Mbit/s  %s" % (e.get("standard") or "", e.get("modulation") or "", e.get("es_n0_db"),
                                                              None if e.get("mer_db") is None else round(e["mer_db"], 1), e.get("bitrate_mbit_s"), d.get("rufzeichen") or "")
    elif f:
        w = "kein Lock"
    else:
        w = "-"
    bake = "Bake %s%s" % (b.get("status"), (" %+.1f kHz" % b["ablage_khz"]) if b.get("ablage_khz") is not None else "")
    return "%-5s %-13s %-26s %-6s %-9s %-9s %s" % (s["name"], s.get("kennung"), typ, zust, ("%.3f MHz" % f) if f else "-", bake, w)


def lauf(a, base, ziel):
    print("Stick-Status  %s  (%s)" % (time.strftime("%d.%m.%Y %H:%M:%S"), base))
    u = usb(ziel) if a.usb else None
    if a.usb:
        if u is None:
            print("USB (ssh): nicht erreichbar")
        else:
            print("USB: %d RTL-Geraet(e) am Pi, USB-Fehler im Log: %s, Drosselung: %s" % (u["geraete"], u["usb_fehler"], u["throttled"]))
    try:
        sticks = get(base, "/api/v1/sticks")["sticks"]
        einst = get(base, "/api/v1/einstellungen")
    except (OSError, urllib.error.URLError, KeyError, ValueError) as ex:
        print("Dienst nicht erreichbar: %s" % ex)
        return 2
    fehlen = []
    if a.json:
        print(json.dumps({"sticks": sticks, "usb": u, "einstellungen": einst}, ensure_ascii=False, indent=1))
    else:
        print("Dienst: %d Sticks, Autoverteiler %s" % (len(sticks), "AN" if (einst.get("verteiler") or {}).get("an") else "AUS"))
        tl = [] if a.kein_typ else typen(ziel)
        print("%-5s %-13s %-26s %-6s %-9s %-9s %s" % ("Stick", "Kennung", "Typ", "Stand", "Signal", "Bake", "Empfang"))
        for s in sticks:
            print(zeile(s, typ_von(s.get("kennung"), tl) if tl else "?"))
        fehlen = [s["name"] for s in sticks if tl and typ_von(s.get("kennung"), tl) == "NICHT AM USB"]
    nlock = sum(1 for s in sticks if s.get("lock") and not schein(s))
    probleme = []
    probleme += ["%s: Stick nicht mehr am USB-Bus des Pi (abgefallen - Kabel/Stick/Port pruefen)" % n for n in fehlen]
    if len(sticks) < a.erwartet:
        probleme.append("nur %d von %d Sticks im Dienst" % (len(sticks), a.erwartet))
    if u and u["geraete"] < a.erwartet:
        probleme.append("der Pi sieht nur %d von %d RTL-Geraeten" % (u["geraete"], a.erwartet))
    probleme += ["%s: Lock ohne brauchbares Signal (Es/N0 %s dB)" % (s["name"], (s.get("empfang") or {}).get("es_n0_db")) for s in sticks if schein(s)]
    probleme += ["%s: Bake-Status %s" % (s["name"], (s.get("bake") or {}).get("status")) for s in sticks if (s.get("bake") or {}).get("status") != "ok"]
    if u and u["usb_fehler"]:
        probleme.append("%d USB-Fehlermeldungen im Kernel-Log" % u["usb_fehler"])
    print("-> %s  (%d mit Lock, %d frei)" % ("ALLE %d STICKS DA" % len(sticks) if not probleme else "PROBLEM: " + "; ".join(probleme), nlock, sum(1 for s in sticks if s.get("frei"))))
    return 1 if probleme else 0


def main():
    ap = argparse.ArgumentParser(description="Schnellstatus der RTL-Sticks am Pi")
    ap.add_argument("--pi", default="192.168.0.102")
    ap.add_argument("--port", type=int, default=8554)
    ap.add_argument("--erwartet", type=int, default=4, help="erwartete Zahl der Sticks")
    ap.add_argument("--usb", action="store_true", help="zusaetzlich USB-Pruefung per ssh")
    ap.add_argument("--ssh", default=None)
    ap.add_argument("--watch", type=int, default=0, metavar="SEK", help="alle SEK Sekunden wiederholen (Strg+C beendet)")
    ap.add_argument("--json", action="store_true", help="Rohdaten als JSON")
    ap.add_argument("--kein-typ", action="store_true", help="Stick-Typ (Hersteller/Modell, per ssh aus sysfs) nicht abfragen")
    a = ap.parse_args()
    base, ziel = "http://%s:%d" % (a.pi, a.port), a.ssh or "dg7ndv@%s" % a.pi
    if not a.watch:
        return lauf(a, base, ziel)
    try:
        while True:
            print("\033[2J\033[H", end="")
            lauf(a, base, ziel)
            time.sleep(a.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
