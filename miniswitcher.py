#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""miniswitcher - kleine QO-100-Bedienung fuer das OnAir-Fenster (DG7NDV)

Zeigt nur das BATC-Spektrum (QO-100 Schmalband-Transponder). Ein Klick auf ein Signal:
  * laeuft noch kein OnAir-Fenster: OnAir wird mit Oberflaeche gestartet, per Remote-API auf
    DVB-S/S2 gestellt, Empfang gestartet, Frequenz/Abtastrate/Verstaerkung gesetzt und
    (wenn das OnAir es kann) die UDP-Ausgabe 230.0.0.10:1234 eingeschaltet;
  * laeuft es schon: es wird nur die Frequenz eingestellt.
Der Player (VLC) spielt udp://@230.0.0.10:1234; bei einem Rufzeichenwechsel wird er kurz
gestoppt und neu gestartet (sauberer Neuaufbau bei anderer Datenrate/Codec).

  --only-api    OnAir wird NICHT gestartet/eingerichtet: es wird nur die Frequenz an den
                Standard-Port (127.0.0.1:8765) geschickt.

Aufruf:  ./miniswitcher.py --lo 9570 [--only-api] [--udp 230.0.0.10:1234] [--device sdrplay;rsp2;antenna a]
Hilfe:   miniswitcher.html (im selben Ordner)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

IS_WIN = (os.name == "nt")

# ---------------------------------------------------------------- Modul-Pruefung (Linux und Windows)
# Verwendet werden: argparse, json, os, shutil, subprocess, sys, threading, time, urllib, struct, math
# (alle in Python enthalten), tkinter (Python-Bestandteil, unter Linux eigenes Paket) und als einziges
# Zusatzmodul websocket-client (Modulname "websocket") fuer das BATC-Spektrum.
def _pip_hint(pkg):
    if IS_WIN:
        return "py -m pip install %s" % pkg
    return "pip3 install %s --break-system-packages   (oder: sudo apt install python3-websocket)" % pkg

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("FEHLER: Python-Modul 'tkinter' fehlt.", file=sys.stderr)
    if IS_WIN:
        print("  Windows: Python von python.org neu installieren und dabei 'tcl/tk and IDLE' ankreuzen.", file=sys.stderr)
    else:
        print("  Linux:   sudo apt install python3-tk", file=sys.stderr)
    sys.exit(1)

try:
    import websocket   # pip install websocket-client
    HAVE_WS = True
except ImportError:
    HAVE_WS = False
    print("FEHLER: Python-Modul 'websocket' (Paket websocket-client) fehlt - ohne das kein BATC-Spektrum.\n"
          "  Installieren mit:  %s" % _pip_hint("websocket-client"), file=sys.stderr)


# ---------------------------------------------------------------- Plattform (Linux / Windows)
def onair_settings_path():
    """settings.conf des OnAir-Fensters: Windows %APPDATA%\\OnAir, sonst $XDG_CONFIG_HOME/onair bzw. ~/.config/onair."""
    if IS_WIN:
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OnAir", "settings.conf")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "onair", "settings.conf")


_WIN_PROG_DIRS = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
_WIN_CANDIDATES = {
    "vlc": [os.path.join(d, "VideoLAN", "VLC", "vlc.exe") for d in _WIN_PROG_DIRS if d],
    "onair": [os.path.join(d, sub, "onair.exe") for d in _WIN_PROG_DIRS if d
              for sub in ("OnAir", os.path.join("OnAir", "bin"), os.path.join("Programs", "OnAir"))],
    "ffprobe": [os.path.join(d, "ffmpeg", "bin", "ffprobe.exe") for d in _WIN_PROG_DIRS if d] + [r"C:\ffmpeg\bin\ffprobe.exe"],
    "ffplay": [os.path.join(d, "ffmpeg", "bin", "ffplay.exe") for d in _WIN_PROG_DIRS if d] + [r"C:\ffmpeg\bin\ffplay.exe"],
}


def find_prog(name):
    """Programm im PATH suchen, unter Windows zusaetzlich in den ueblichen Installationsordnern."""
    p = shutil.which(name)
    if p:
        return p
    if IS_WIN:
        for c in _WIN_CANDIDATES.get(name, []):
            if os.path.isfile(c):
                return c
        # winget (z.B. "winget install Gyan.FFmpeg"): Verknuepfung in %LOCALAPPDATA%\Microsoft\WinGet\Links -
        # die steht zwar im PATH, aber erst in neu geoeffneten Fenstern - und das Paket selbst unter ...\Packages
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            import glob
            wg = os.path.join(la, "Microsoft", "WinGet")
            hits = [os.path.join(wg, "Links", name + ".exe")] + \
                sorted(glob.glob(os.path.join(wg, "Packages", "*", "*", "bin", name + ".exe")), reverse=True) + \
                sorted(glob.glob(os.path.join(wg, "Packages", "*", name + ".exe")), reverse=True)
            for c in hits:
                if os.path.isfile(c):
                    return c
    return None


def detached_kwargs():
    """Popen-Argumente, damit gestartete Programme unabhaengig vom miniswitcher weiterlaufen."""
    if IS_WIN:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


INSTALL_HINTS = {
    "vlc": ("https://www.videolan.org (Standard-Installation, wird automatisch gefunden)", "sudo apt install vlc"),
    "ffprobe": ("ffmpeg von https://ffmpeg.org/download.html, Ordner 'bin' in den PATH oder nach C:\\ffmpeg\\bin",
                "sudo apt install ffmpeg"),
    "mpv": ("https://mpv.io", "sudo apt install mpv"),
    "ffplay": ("gehoert zu ffmpeg (siehe ffprobe)", "sudo apt install ffmpeg"),
    "onair": ("OnAir-Installer von https://on-air.dev (oder --onair-bin PFAD)", "OnAir-.deb installieren (oder --onair-bin PFAD)"),
}


def check_programs(a):
    """Externe Programme pruefen - liefert Liste von Hinweistexten fuer fehlende."""
    need = ["ffprobe"]
    if not a.no_player:
        need.append(a.player)
    if not a.only_api and not a.onair_bin:
        need.append("onair")
    out = []
    for n in need:
        if not find_prog(n) and not (n == "onair" and find_prog("dect2")):
            hint = INSTALL_HINTS.get(n, ("", ""))[0 if IS_WIN else 1]
            out.append("%s fehlt - %s" % (n, hint))
    return out

CFG_FILE = os.path.expanduser("~/.leandvb-GUI/miniswitcher.json")
LOG_FILE = os.path.expanduser("~/.leandvb-GUI/miniswitcher.log")
PLAYER_LOG = os.path.expanduser("~/.leandvb-GUI/miniswitcher_player.log")


def logfile_write(line):
    """Alle Meldungen auch in ~/.leandvb-GUI/miniswitcher.log (ab 2 MB -> .1)."""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
            f.write(time.strftime("%Y-%m-%d ") + line + "\n")
    except OSError:
        pass
BEACON_MHZ = 10491.5
SR_STEPS = (250, 333, 500, 1000, 2000)          # wie leandvb-GUI (batc_sr_from_bw)
SR_BW_LIMITS = (305, 425, 750, 1500)            # Signalbreite kHz -> Grenze zwischen den Symbolraten
F_START, F_SPAN = 10490.5, 9.0          # BATC-Schmalband-Spektrum: 10490,5 ... 10499,5 MHz


# ---------------------------------------------------------------- Konfiguration
def cfg_load():
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cfg_save(c):
    try:
        os.makedirs(os.path.dirname(CFG_FILE), exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f, indent=2, sort_keys=True)
    except OSError as e:
        print("Konfig nicht schreibbar: %s" % e, flush=True)


# ---------------------------------------------------------------- OnAir-API
class OnAir:
    def __init__(self, port, token=None):
        self.port = port
        self.token = token

    def call(self, path, body=None, timeout=3.0):
        url = "http://127.0.0.1:%d/api/v1%s" % (self.port, path)
        hdr = {"Content-Type": "application/json"}
        if self.token:
            hdr["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=hdr, method="GET" if body is None else "POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "null")

    def alive(self):
        try:
            self.call("/status", timeout=1.5)
            return True
        except Exception:
            return False


def onair_token():
    try:
        with open(onair_settings_path(), encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith("apiToken="):
                    return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def onair_running_pids():
    if IS_WIN:
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=6).stdout.decode(errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids = []
        for ln in out.splitlines():
            parts = [x.strip('"') for x in ln.split('","')]
            if len(parts) >= 2 and parts[0].lower() in ("onair.exe", "dect2.exe"):
                try:
                    pids.append(int(parts[1].strip('"')))
                except ValueError:
                    pass
        return pids
    try:
        out = subprocess.run(["ps", "-ax", "-o", "pid=,args="], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=4).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = []
    for ln in out.splitlines():
        ln = ln.strip()
        pid, _s, args = ln.partition(" ")
        prog = os.path.basename(args.split()[0]) if args.split() else ""
        if prog in ("onair", "dect2"):
            try:
                pids.append(int(pid))
            except ValueError:
                pass
    return pids


def prefs_set(path, want):
    """key=value in OnAirs settings.conf setzen (nur solange OnAir nicht laeuft)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in want:
            out.append("%s=%s" % (k, want[k]))
            seen.add(k)
        else:
            out.append(ln)
    out += ["%s=%s" % (k, v) for k, v in want.items() if k not in seen]
    if out != lines:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------- Signal-Erkennung im Spektrum
def sr_from_bw(bw_khz):
    for limit, sr in zip(SR_BW_LIMITS, SR_STEPS):
        if bw_khz < limit:
            return sr
    return SR_STEPS[-1]


def detect_signals(vals):
    """Wie batc_analyze_signals() der leandvb-GUI: dB-Werte, Schwelle = 25%-Perzentil + 2,5 dB, Segmente
    breiter als 5 Bins und 80 kHz. Liefert [(mitte_MHz, breite_MHz, sr_kS, spitze_roh)]."""
    import math
    n = len(vals)
    if n < 50:
        return []
    db = [10.0 * math.log10(v if v > 0 else 1) for v in vals]
    thr = sorted(db)[int(n * 0.25)] + 2.5
    sigs, in_sig, sx = [], False, 0
    for x in range(n + 1):
        above = x < n and db[x] > thr
        if above and not in_sig:
            in_sig, sx = True, x
        elif not above and in_sig:
            in_sig = False
            w = x - sx
            if w > 5:
                f = F_START + ((sx + w / 2.0) / n) * F_SPAN
                bw_khz = (w / n) * F_SPAN * 1000.0
                if bw_khz > 80:
                    sigs.append((f, bw_khz / 1000.0, 1500 if abs(f - BEACON_MHZ) < 0.1 else sr_from_bw(bw_khz),
                                 max(vals[sx:x])))
    return sigs


# ---------------------------------------------------------------- Hauptfenster
class Mini:
    def __init__(self, a):
        self.a = a
        self.cfg = cfg_load()
        self.api = OnAir(a.port, onair_token())
        self.vals = []
        self.sigs = []
        self.rx_rf = None           # zuletzt eingestellte RF
        self.rx_sr = None
        self.cs = ""                # aktuelles Rufzeichen
        self.cs_by_freq = {}        # MHz (gerundet) -> Rufzeichen
        self.tel = {}
        self.stat = {}
        self.spec = {}
        self._bad_last = None
        self.player_fast = 0          # wie oft der Player direkt nach dem Start wieder weg war
        self.player_pause_t = 0.0
        self._bad_up_t = 0.0
        self.player = None
        self.player_wanted = not a.no_player
        self.ev_lock = threading.Lock()
        self.frames = []            # (zeit, [(MHz, SR), ...]) je BATC-Bild
        self.stable = {}            # entprellte Signale: MHz -> SR
        self.events = []            # letzte Ereignisse fuer die Statuszeile
        self.busy = False
        self.lo_corr_khz = float(self.cfg.get("lo_corr_khz", 0.0))
        # LNB-LO: --lo hat Vorrang, sonst der zuletzt im Fenster eingestellte Wert, sonst 9540
        self.lo_from_cli = a.lo is not None
        if a.lo is None:
            try:
                a.lo = float(self.cfg.get("lo", 9540.0))
            except (TypeError, ValueError):
                a.lo = 9540.0
        elif float(self.cfg.get("lo", a.lo)) != a.lo:
            self.lo_corr_khz = 0.0            # anderer LNB -> alte Korrektur passt nicht mehr
            self.cfg["lo_corr_khz"] = 0.0
        self.cfg["lo"] = a.lo
        cfg_save(self.cfg)
        self.cfo_done_for = None
        self.started_t = 0.0

        self.root = tk.Tk()
        self.root.title("miniswitcher - QO-100 / OnAir%s" % (" (nur API)" if a.only_api else ""))
        self.root.geometry(self.cfg.get("geometry") or "1100x420")
        top = ttk.Frame(self.root, padding=(6, 4))
        top.pack(side="top", fill="x")
        self.lbl_rx = tk.Label(top, text="OnAir: -", font=("", 10, "bold"), anchor="w")
        self.lbl_rx.pack(side="left")
        self.lbl_cs = tk.Label(top, text="", font=("", 12, "bold"), fg="white", bg="#7f8c8d", padx=10)
        self.lbl_cs.pack(side="left", padx=(12, 0))
        # Player-Zeile: welcher Player, welche Stream-Adresse (IP/UDP-Port), laeuft er?
        self.lbl_pl = tk.Label(top, text="", font=("", 9, "bold"), padx=8, relief="solid", borderwidth=1)
        self.lbl_pl.pack(side="left", padx=(12, 0))
        ttk.Button(top, text="Bake", command=lambda: self.tune(BEACON_MHZ, 1500)).pack(side="right")
        # LNB-LO sichtbar und aenderbar - ein falscher LO ist der haeufigste Grund fuer "kein Lock"
        lof = tk.Frame(top, bg="#f1c40f", padx=4, pady=1)
        lof.pack(side="right", padx=(0, 8))
        tk.Label(lof, text="LNB-LO", bg="#f1c40f", font=("", 9, "bold")).pack(side="left")
        self.v_lo = tk.StringVar(value="%.3f" % a.lo)
        e_lo = tk.Entry(lof, textvariable=self.v_lo, width=9, font=("", 9))
        e_lo.pack(side="left", padx=(4, 2))
        e_lo.bind("<Return>", lambda _e: self.set_lo())
        tk.Label(lof, text="MHz", bg="#f1c40f", font=("", 9)).pack(side="left")
        self.lbl_locorr = tk.Label(lof, text="", bg="#f1c40f", font=("", 8))
        self.lbl_locorr.pack(side="left", padx=(4, 0))
        tk.Button(lof, text="setzen", font=("", 8), command=self.set_lo, pady=0).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="Player Stop", command=self.player_stop_user).pack(side="right", padx=(0, 4))
        ttk.Button(top, text="Player Start", command=self.player_start_user).pack(side="right", padx=(0, 4))
        # Zweite Zeile: OnAir-Werte als Chips in EINER Zeile (wie die OnAir-Spalte der leandvb-GUI)
        chips = ttk.Frame(self.root, padding=(6, 0, 6, 4))
        chips.pack(side="top", fill="x")
        self.chips = {}
        for k, txt, w in (("p", "P", 2), ("state", "-", 10), ("freq", "-", 10), ("gain", "G-", 5), ("level", "-", 16),
                          ("mer", "MER: -", 16), ("sr", "SR -", 9), ("okbad", "ok - bad -", 14), ("cfo", "CFO -", 11)):
            lb = tk.Label(chips, text=txt, width=w, relief="solid", borderwidth=1, bg="#d9d9d9", fg="black",
                          font=("", 9))
            lb.pack(side="left", padx=(0, 2), fill="x", expand=(k not in ("p", "gain")))
            self.chips[k] = lb
        # Signal-Knoepfe (wie im Hauptprogramm): je Signal ein Knopf, gruen = neu (30 s), blau = eingestellt,
        # rot = verschwunden (10 s, danach wird der Knopf geloescht). Klick stellt das Signal ein.
        self.frm_sig = ttk.Frame(self.root, padding=(6, 0, 6, 4))
        self.frm_sig.pack(side="top", fill="x")
        self.sig_state = {}     # key (MHz) -> {"freq", "sr", "born", "gone_since"}
        self.sig_btns = {}      # key -> tk.Button
        # Statuszeilen unten ZUERST packen (sonst nimmt das Spektrum mit expand allen Platz):
        # oben die letzte Meldung, darunter die letzten Signal-Ereignisse (neu / beendet / Bake)
        self.lbl_status = tk.Label(self.root, text="", anchor="w", font=("", 9))
        self.lbl_events = tk.Label(self.root, text="", anchor="w", justify="left", font=("TkFixedFont", 9),
                                   bg="#1e1e1e", fg="#dddddd", padx=6)
        self.lbl_events.pack(side="bottom", fill="x")
        # dezent unten rechts in der Ereignis-Zeile
        tk.Label(self.lbl_events, text="(c) DG7NDV (10.2026)", bg="#1e1e1e", fg="#777777",
                 font=("", 7)).place(relx=1.0, rely=1.0, anchor="se", x=-4, y=-2)
        self.lbl_status.pack(side="bottom", fill="x")
        self.cv = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.cv.pack(side="top", fill="both", expand=True)
        self.cv.bind("<Button-1>", self.on_click)
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

        threading.Thread(target=self.batc_loop, daemon=True).start()
        threading.Thread(target=self.status_loop, daemon=True).start()
        threading.Thread(target=self.callsign_loop, daemon=True).start()
        if self.player_wanted:
            self.root.after(1500, self.player_start)
        self.root.after(1000, self.ui_tick)
        self.root.after(3000, self.player_watch)
        self.root.after(2000, self.signal_tick)

    # ------------------------------------------------ Signal-Ereignisse (neu / beendet), entprellt
    def signal_tick(self):
        """Wie die leandvb-GUI: Bilder der letzten 5 s zu Signalen zusammenfassen; neu ab 50 % der Bilder,
        ein bekanntes Signal bleibt bis unter 20 %. Aenderungen erscheinen unten als Ereignis."""
        now = time.time()
        with self.ev_lock:
            while self.frames and self.frames[0][0] < now - 5.0:
                self.frames.pop(0)
            fr = [sg for (_t, sg) in self.frames]
        if fr:
            clusters = []
            for sigs in fr:
                for f, sr in sigs:
                    c = next((c for c in clusters if abs(c["f"] - f) <= 0.2), None)
                    if c is None:
                        clusters.append({"f": f, "sr": sr, "n": 1})
                    else:
                        c["f"] = (c["f"] * c["n"] + f) / (c["n"] + 1)
                        c["n"] += 1
                        c["sr"] = sr
            n = len(fr)
            new = {}
            for c in clusters:
                known = next((k for k in self.stable if abs(k - c["f"]) <= 0.2), None)
                if c["n"] / n >= (0.2 if known is not None else 0.5):
                    new[round(c["f"], 3) if known is None else known] = c["sr"]
            for f, sr in new.items():
                if f not in self.stable:
                    self.event("%s %.3f MHz  %d kS" % ("Bake da     " if abs(f - BEACON_MHZ) < 0.3 else "Neues Signal",
                                                        f, sr))
            for f, sr in self.stable.items():
                if f not in new:
                    cs = self.cs_by_freq.get(round(f, 2))
                    self.event("%s %.3f MHz  %d kS%s" % ("Bake WEG    " if abs(f - BEACON_MHZ) < 0.3 else "Signal weg  ",
                                                         f, sr, ("  (%s)" % cs) if cs else ""))
            self.stable = new
        self.update_sig_buttons(now)
        self.root.after(1000, self.signal_tick)

    def update_sig_buttons(self, now):
        """Ampel-Zustaende pflegen und die Knopfreihe aktualisieren (neu gruen 30 s, weg rot 10 s -> loeschen)."""
        seen = set()
        for f, sr in self.stable.items():
            key = next((k for k, st in self.sig_state.items() if abs(st["freq"] - f) <= 0.2), None)
            if key is None:
                key = f
                self.sig_state[key] = {"freq": f, "sr": sr, "born": now, "gone_since": None}
            else:
                st = self.sig_state[key]
                if st["gone_since"] is not None:      # war rot und ist wieder da -> wieder "neu"
                    st["born"], st["gone_since"] = now, None
                st["freq"], st["sr"] = f, sr
            seen.add(key)
        for key, st in self.sig_state.items():
            if key not in seen and st["gone_since"] is None:
                st["gone_since"] = now
        for key in [k for k, st in self.sig_state.items() if st["gone_since"] and now - st["gone_since"] >= 10.0]:
            del self.sig_state[key]
        # Knoepfe: fehlende anlegen, ueberzaehlige entfernen, alle neu einfaerben/beschriften
        order = sorted(self.sig_state, key=lambda k: self.sig_state[k]["freq"])
        if list(self.sig_btns) != order:
            for b in self.sig_btns.values():
                b.destroy()
            self.sig_btns = {}
            for key in order:
                b = tk.Button(self.frm_sig, font=("", 9), relief="raised", padx=6,
                              command=lambda k=key: self.sig_click(k))
                b.pack(side="left", padx=(0, 3))
                self.sig_btns[key] = b
        for key in order:
            st = self.sig_state[key]
            is_b = abs(st["freq"] - BEACON_MHZ) < 0.3
            cs = self.cs_by_freq.get(round(st["freq"], 2))
            txt = ("Bake %.3f" % BEACON_MHZ) if is_b else "%.3f MHz  %d kS" % (st["freq"], st["sr"])
            if cs and not is_b:
                txt += "  " + cs
            tuned = self.rx_rf is not None and abs(self.rx_rf - (BEACON_MHZ if is_b else st["freq"])) <= 0.2
            if st["gone_since"]:
                bg, fg = "#e74c3c", "black"            # verschwunden (rot)
            elif tuned:
                bg, fg = "#3498db", "white"            # gerade eingestellt (blau)
            elif now - st["born"] < 30.0 and not is_b:
                bg, fg = "#2ecc71", "black"            # neu (gruen)
            else:
                bg, fg = "#e0e0e0", "black"
            self.sig_btns[key].configure(text=txt, bg=bg, fg=fg, activebackground=bg)

    def sig_click(self, key):
        st = self.sig_state.get(key)
        if not st:
            return
        if abs(st["freq"] - BEACON_MHZ) < 0.3:
            self.tune(BEACON_MHZ, 1500)
        else:
            self.tune(round(st["freq"], 3), st["sr"])

    def event(self, txt):
        line = time.strftime("%H:%M:%S  ") + txt
        print(line, flush=True)
        logfile_write(line)
        self.events.insert(0, line)
        del self.events[4:]
        self.lbl_events.configure(text="\n".join(self.events))

    def player_watch(self):
        """Player automatisch (neu) starten, solange er gewollt ist (z.B. Fenster geschlossen / abgestuerzt)."""
        p = self.player
        now = time.time()
        if self.player_wanted and (p is None or p.poll() is not None):
            if p is not None:
                rc = p.returncode
                ran = now - getattr(self, "player_t0", now)
                if ran < 5.0:
                    self.player_fast += 1
                    tail = ""
                    try:
                        with open(PLAYER_LOG, encoding="utf-8", errors="replace") as f:
                            tail = " | ".join(x.strip() for x in f.read().splitlines()[-3:] if x.strip())
                    except OSError:
                        pass
                    self.log("Player beendete sich nach %.1f s (Code %s): %s" % (ran, rc, tail[:300]))
                else:
                    self.player_fast = 0
            if self.player_fast >= 3 and now - self.player_pause_t < 60.0:
                pass        # 3x sofort beendet: 60 s Pause, nicht im 5-s-Takt weiterstarten
            else:
                if self.player_fast >= 3:
                    self.log("Player startete 3x nicht - Pause 60 s vorbei, neuer Versuch (Log: %s)" % PLAYER_LOG)
                    self.player_pause_t = now
                    self.player_fast = 0
                self.log("Player laeuft nicht - starte ihn (%s)" % self.a.udp)
                self.player_start()
                if self.player_fast >= 2:
                    self.player_pause_t = now
        self.root.after(5000, self.player_watch)

    # ------------------------------------------------ Meldungen
    def log(self, msg):
        print(time.strftime("%H:%M:%S ") + msg, flush=True)
        logfile_write(time.strftime("%H:%M:%S ") + msg)
        self.root.after(0, lambda: self.lbl_status.configure(text=msg[:200]))

    # ------------------------------------------------ BATC-Spektrum
    def batc_loop(self):
        if not HAVE_WS:
            self.log("websocket-client fehlt: sudo pip3 install websocket-client --break-system-packages")
            return
        while True:
            try:
                ws = websocket.create_connection(self.a.batc_url, timeout=10,
                                                 subprotocols=["fft_m0dtslivetune"])   # wie leandvb-GUI
                self.log("BATC-Spektrum verbunden (%s)" % self.a.batc_url)
                while True:
                    msg = ws.recv()
                    self.on_fft(msg)
            except Exception as e:
                self.log("BATC-Spektrum: %s - neuer Versuch in 5 s" % e)
                time.sleep(5)

    def on_fft(self, msg):
        vals = None
        if isinstance(msg, (bytes, bytearray)):
            if len(msg) >= 200 and len(msg) % 2 == 0:
                import struct
                vals = list(struct.unpack("<%dH" % (len(msg) // 2), msg))
        else:
            try:
                d = json.loads(msg)
                if isinstance(d, list):
                    vals = [float(x) for x in d]
                elif isinstance(d, dict):
                    for k in ("fft", "data", "spectrum"):
                        if isinstance(d.get(k), list):
                            vals = [float(x) for x in d[k]]
                            break
            except (ValueError, TypeError):
                pass
        if vals:
            self.vals = vals
            self.sigs = detect_signals(vals)
            with self.ev_lock:
                self.frames.append((time.time(), [(m, sr) for (m, _bw, sr, _pk) in self.sigs]))

    # ------------------------------------------------ Zeichnen
    @staticmethod
    def color(frac):
        frac = max(0.0, min(1.0, frac))
        stops = ((0.0, (10, 30, 140)), (0.35, (20, 170, 200)), (0.6, (60, 200, 60)),
                 (0.8, (240, 220, 30)), (1.0, (240, 60, 30)))
        for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
            if frac <= f1:
                t = (frac - f0) / (f1 - f0)
                return "#%02x%02x%02x" % tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
        return "#f03c1e"

    def x_of(self, f, w):
        return (f - F_START) / F_SPAN * w

    def draw(self):
        cv = self.cv
        cv.delete("all")
        w, h = max(100, cv.winfo_width()), max(80, cv.winfo_height())
        axis = 18
        hh = h - axis
        for f in range(10491, 10500):
            x = self.x_of(f, w)
            cv.create_line(x, 0, x, hh, fill="#333333", dash=(2, 4))
            cv.create_text(x, hh + 9, text="%d.000" % f, fill="white", font=("", 8))
        vals = self.vals
        if vals:
            n = len(vals)
            lo, hi = 0.0, 65536.0          # linear wie das BATC-Original (Rohwert/65536)
            span = hi - lo
            step = max(1, n // w) if n > w else 1
            for i in range(0, n, step):
                frac = (vals[i] - lo) / span
                x = i / n * w
                y = hh - frac * (hh - 30)
                cv.create_line(x, hh, x, y, fill=self.color(frac), width=max(1, int(w / n) + 1))
            for (mid, bw, sr, pk) in self.sigs:
                x = self.x_of(mid, w)
                y = hh - (pk - lo) / span * (hh - 30) - 8
                is_b = abs(mid - BEACON_MHZ) < 0.3
                txt = "Bake" if is_b else "%dKS" % sr
                cs = self.cs_by_freq.get(round(mid, 2))
                if cs:
                    txt += "\n" + cs
                cv.create_text(x, y, text=txt, fill="white", font=("", 8), anchor="s")
        if self.rx_rf:
            x = self.x_of(self.rx_rf, w)
            cv.create_line(x, 0, x, hh, fill="#2ecc71", width=2)
            cv.create_text(x + 4, 4, text="%.3f" % self.rx_rf, fill="#2ecc71", anchor="nw", font=("", 9, "bold"))

    def set_lo(self):
        """LNB-LO im Fenster aendern (gespeichert fuer den naechsten Start; --lo hat beim Start Vorrang)."""
        try:
            lo = float(self.v_lo.get().replace(",", "."))
        except ValueError:
            self.log("LNB-LO ungueltig: %r" % self.v_lo.get())
            return
        if not 9000.0 <= lo <= 11000.0:
            self.log("LNB-LO %.3f MHz unplausibel (erwartet z.B. 9540, 9570, 9750)" % lo)
            return
        if lo != self.a.lo:
            self.a.lo = lo
            self.lo_corr_khz = 0.0
            self.cfg["lo"], self.cfg["lo_corr_khz"] = lo, 0.0
            cfg_save(self.cfg)
            self.log("LNB-LO jetzt %.3f MHz (LO-Korrektur zurueckgesetzt - wird auf der Bake neu gemessen)" % lo)
            if self.rx_rf:
                self.tune(self.rx_rf, self.rx_sr)

    # ------------------------------------------------ Klick
    def on_click(self, ev):
        w = max(100, self.cv.winfo_width())
        f = F_START + ev.x / w * F_SPAN
        best = None
        for (mid, bw, sr, pk) in self.sigs:
            if abs(f - mid) <= max(bw / 2.0, 0.05) and (best is None or abs(f - mid) < abs(f - best[0])):
                best = (mid, sr)
        if best:
            mid, sr = best
            if abs(mid - BEACON_MHZ) < 0.3:
                mid, sr = BEACON_MHZ, 1500
            self.tune(round(mid, 3), sr)
        else:
            self.tune(round(f, 3), None)

    # ------------------------------------------------ Abstimmen
    def zf(self, rf):
        return rf - self.a.lo + self.lo_corr_khz / 1000.0

    def tune(self, rf, sr):
        if self.busy:
            self.log("bin noch beschaeftigt - Klick ignoriert")
            return
        self.busy = True
        threading.Thread(target=self._tune, args=(rf, sr), daemon=True).start()

    def _tune(self, rf, sr):
        try:
            zf = round(self.zf(rf), 4)
            if self.a.only_api:
                self.api.call("/frequency", {"mhz": zf})
                self.log("Frequenz %.3f MHz (ZF %.4f) gesendet (nur API, Port %d)" % (rf, zf, self.a.port))
            else:
                running = False
                if self.api.alive():
                    try:
                        running = bool(self.api.call("/status").get("running"))
                    except Exception:
                        running = False
                else:
                    if not self.start_onair():
                        return
                if running:
                    if sr and sr != self.rx_sr:
                        self.set_rate(sr)
                    self.api.call("/frequency", {"mhz": zf})
                    self.log("Frequenz %.3f MHz (ZF %.4f, SR %s) eingestellt" % (rf, zf, sr))
                else:
                    self.setup_receiver(zf, sr)
                    self.log("OnAir eingerichtet: %.3f MHz (ZF %.4f, SR %s)" % (rf, zf, sr))
            if abs(rf - (self.rx_rf or 0)) > 0.001:
                self.cs = ""
                self.root.after(0, self.show_cs)
                self.started_t = time.time()
            self.rx_rf, self.rx_sr = rf, sr
            self.root.after(0, self.draw)
        except urllib.error.URLError as e:
            self.log("OnAir-API nicht erreichbar (Port %d): %s" % (self.a.port, e))
        except Exception as e:
            self.log("Fehler beim Abstimmen: %s" % e)
        finally:
            self.busy = False

    def set_rate(self, sr):
        if self.a.no_rate:
            return
        msps = round(max(2.0, min(10.0, sr * 4.0 / 1000.0)), 2)
        try:
            self.api.call("/samplerate", {"msps": msps}, timeout=6)
        except Exception as e:
            self.log("Abtastrate setzen fehlgeschlagen: %s" % e)

    def start_onair(self):
        if onair_running_pids():
            self.log("OnAir laeuft, Remote-API antwortet aber nicht auf Port %d - im Fenster View > Data feeds > "
                     "Remote control API einschalten" % self.a.port)
            return False
        exe = self.a.onair_bin or find_prog("onair") or find_prog("dect2")
        if not exe:
            self.log("OnAir-Programm nicht gefunden (--onair-bin)")
            return False
        ip, port = self.a.udp.rsplit(":", 1)
        try:
            prefs_set(onair_settings_path(),
                      {"apiOn": "1", "apiPort": str(self.a.port), "udpHost": ip, "udpPort": port})
        except OSError as e:
            self.log("settings.conf nicht schreibbar: %s" % e)
        self.log("starte OnAir: %s" % exe)
        subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **detached_kwargs())
        t0 = time.time()
        while time.time() - t0 < 40:
            time.sleep(1)
            if self.api.alive():
                self.log("OnAir bereit nach %.0f s" % (time.time() - t0))
                return True
        self.log("OnAir antwortet nicht auf Port %d" % self.a.port)
        return False

    def setup_receiver(self, zf, sr):
        modes = self.api.call("/modes") or []
        mode = next((m for m in modes if isinstance(m, dict) and (m.get("sub") or "") == "dvbs"), None) or \
            next((m for m in modes if isinstance(m, dict) and "DVB-S" in (m.get("name") or "")), None)
        dev_idx = None
        terms = [x.strip().lower() for x in (self.a.device or "").split(";") if x.strip()]
        if terms:
            t0 = time.time()
            while time.time() - t0 < 45:      # OnAir sucht seine Radios erst im Hintergrund
                devs = self.api.call("/devices", timeout=5) or []
                for d in devs if isinstance(devs, list) else []:
                    hay = ((d.get("name") or "") + " " + (d.get("board") or "")).lower()
                    if all(x in hay for x in terms):
                        dev_idx = d.get("index")
                        break
                if dev_idx is not None:
                    break
                time.sleep(2)
            if dev_idx is None:
                self.log("Geraet '%s' im OnAir nicht gefunden - nehme das dort eingestellte" % self.a.device)
        try:
            self.api.call("/stop", {})
        except Exception:
            pass
        if mode:
            body = {"family": mode["family"]}
            if mode.get("sub"):
                body["sub"] = mode["sub"]
            self.api.call("/mode", body)
        self.api.call("/start", {"device": dev_idx} if dev_idx is not None else {}, timeout=10)
        if sr:
            self.set_rate(sr)
        self.api.call("/frequency", {"mhz": zf})
        if self.a.gain is not None:
            try:
                self.api.call("/gain", {"db": self.a.gain})
            except Exception as e:
                self.log("Gain setzen fehlgeschlagen: %s" % e)
        ip, port = self.a.udp.rsplit(":", 1)
        try:
            self.api.call("/x/udp", {"on": True, "host": ip, "port": int(port), "ttl": 1}, timeout=5)
            self.log("UDP-Ausgabe %s eingeschaltet" % self.a.udp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.log("Dieses OnAir kann UDP nicht per API schalten - im Fenster Tab Stream: %s, Haken 'stream'"
                         % self.a.udp)

    # ------------------------------------------------ Status / LO-Korrektur
    def status_loop(self):
        while True:
            try:
                self.stat = self.api.call("/status", timeout=1.5) or {}
                self.tel = self.api.call("/telemetry", timeout=1.5) or {}
                try:
                    self.spec = self.api.call("/spectrum?bins=16", timeout=1.5) or {}
                except Exception:
                    self.spec = {}
                self.beacon_cfo()
            except Exception:
                self.stat, self.tel, self.spec = {}, {}, {}
            time.sleep(1.5)

    def beacon_cfo(self):
        """Steht der Empfaenger auf der Bake und lockt, den Traeger-Versatz einmal als LO-Korrektur uebernehmen."""
        if self.a.only_api or not self.rx_rf or abs(self.rx_rf - BEACON_MHZ) > 0.01:
            return
        if not self.tel.get("locked") or self.cfo_done_for == self.started_t:
            return
        cfo = self.tel.get("cfoHz")
        if not isinstance(cfo, (int, float)) or abs(cfo) < 3000 or abs(cfo) > 1e6:
            self.cfo_done_for = self.started_t
            return
        self.cfo_done_for = self.started_t
        self.lo_corr_khz = round(self.lo_corr_khz + cfo / 1000.0)
        self.cfg["lo_corr_khz"] = self.lo_corr_khz
        cfg_save(self.cfg)
        self.log("Bake %+.0f kHz neben der Mitte - LO-Korrektur jetzt %+.0f kHz" % (cfo / 1000.0, self.lo_corr_khz))

    def ui_tick(self):
        try:
            self.lbl_locorr.configure(text=("Korr %+.0f kHz" % self.lo_corr_khz) if self.lo_corr_khz else "")
        except tk.TclError:
            pass
        ip, port = self.a.udp.rsplit(":", 1)
        alive = self.player is not None and self.player.poll() is None
        self.lbl_pl.configure(text="%s  udp://@%s:%s  %s" % (self.a.player.upper(), ip, port,
                                                            "laeuft" if alive else ("aus" if not self.player_wanted else "startet ...")),
                              bg="#27ae60" if alive else ("#bdc3c7" if not self.player_wanted else "#f39c12"),
                              fg="white" if alive else "black")
        st, tel = self.stat, self.tel
        if not st:
            self.lbl_rx.configure(text="OnAir: nicht erreichbar (Port %d)" % self.a.port, fg="#c0392b")
        else:
            det = tel.get("details") or {}
            srv = det.get("symbolRate")
            txt = "OnAir: %s  %.3f MHz ZF  %s  SNR %s  SR %s  %s" % (
                "LOCK" if tel.get("locked") else ("laeuft" if st.get("running") else "gestoppt"),
                (st.get("frequencyHz") or 0) / 1e6, st.get("device", ""),
                ("%.1f dB" % tel["snrDb"]) if isinstance(tel.get("snrDb"), (int, float)) else "-",
                ("%.3f" % (srv / 1e6 if srv and srv > 1000 else srv)) if isinstance(srv, (int, float)) and srv else "-",
                ("ok %s bad %s" % (tel.get("blocksOk", "-"), tel.get("blocksBad", "-"))))
            self.lbl_rx.configure(text=txt, fg="#1e8449" if tel.get("locked") else "#d35400")
        self.update_chips()
        self.draw()
        self.root.after(1000, self.ui_tick)

    def chip(self, k, text, bg):
        self.chips[k].configure(text=text, bg=bg, fg="black")

    def update_chips(self):
        GREY, GREEN, ORANGE, RED, YEL = "#d9d9d9", "#2ecc71", "#f39c12", "#e74c3c", "#f1c40f"
        st, tel, sp = self.stat, self.tel, self.spec
        if not st:
            self.chip("p", "P", RED)
            for k, t in (("state", "nicht erreichbar"), ("freq", "-"), ("gain", "G-"), ("level", "-"),
                         ("mer", "MER: -"), ("sr", "SR -"), ("okbad", "ok - bad -"), ("cfo", "CFO -")):
                self.chip(k, t, GREY if k in ("state", "freq", "gain", "level") else YEL)
            return
        self.chip("p", "P", GREEN if st.get("running") else ORANGE)
        locked = bool(tel.get("locked"))
        stt = tel.get("state")
        self.chip("state", "LOCK" if locked else ("rastet ein" if stt == 1 else ("sucht" if st.get("running") else "gestoppt")),
                  GREEN if locked else (ORANGE if st.get("running") else GREY))
        fhz = st.get("frequencyHz")
        if fhz:
            rf = fhz / 1e6 + self.a.lo - self.lo_corr_khz / 1000.0
            match = self.rx_rf is not None and abs(rf - self.rx_rf) < 0.002
            self.chip("freq", "%.3f" % rf, GREEN if match else ORANGE)
        g = st.get("gainDb")
        self.chip("gain", "G%s" % (("%g" % g) if isinstance(g, (int, float)) else "-"), GREY)
        lv = sp.get("rmsDbfs") if isinstance(sp, dict) else None
        if isinstance(lv, (int, float)):
            txt = "%.1fdBFS" % lv
            if isinstance(g, (int, float)):
                txt += " %+.0fdBm" % (lv - g + float(self.cfg.get("dbm_offset", 0.0)))
            clip = sp.get("clipFraction")
            self.chip("level", txt, RED if (isinstance(clip, (int, float)) and clip > 0.001) or lv > -8 else GREY)
        else:
            self.chip("level", "-", GREY)
        # MER/SNR nur bei Lock anzeigen (ohne Lock liefert OnAir Unsinn, z.B. 38 dB)
        es = tel.get("snrDb") if locked else None
        q = tel.get("quality") or {}
        req = q.get("requiredDb")
        if isinstance(es, (int, float)):
            d = (es - req) if isinstance(req, (int, float)) and req > 0 else None
            self.chip("mer", "MER: %.1f dB%s" % (es, (" / D%+.1f" % d) if d is not None else ""),
                      RED if (d is not None and d < 0.5) else YEL)
        else:
            self.chip("mer", "MER: -", YEL)
        srv = (tel.get("details") or {}).get("symbolRate")
        self.chip("sr", ("SR %.3f" % (srv / 1e6 if srv > 1000 else srv)) if isinstance(srv, (int, float)) and srv else "SR -", YEL)
        okv, badv = tel.get("blocksOk"), tel.get("blocksBad")
        now = time.time()
        if isinstance(badv, (int, float)):
            if self._bad_last is not None and badv > self._bad_last:
                self._bad_up_t = now
            self._bad_last = badv
        self.chip("okbad", "ok %s bad %s" % (okv if okv is not None else "-", badv if badv is not None else "-"),
                  RED if now - self._bad_up_t < 10 else YEL)
        cfo = tel.get("cfoHz")
        self.chip("cfo", ("CFO %+.1f kHz" % (cfo / 1000.0)) if isinstance(cfo, (int, float)) and locked else "CFO -", YEL)

    # ------------------------------------------------ Rufzeichen (ffprobe auf dem UDP-Strom)
    def callsign_loop(self):
        ffprobe = find_prog("ffprobe")
        if not ffprobe:
            self.log("ffprobe fehlt (%s) - keine Rufzeichen-Erkennung" % INSTALL_HINTS["ffprobe"][0 if IS_WIN else 1])
            return
        ip, port = self.a.udp.rsplit(":", 1)
        url = "udp://@%s:%s?timeout=3000000" % (ip, port)
        while True:
            name = ""
            try:
                r = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_programs",
                                    "-analyzeduration", "2000000", "-probesize", "1000000", "-i", url],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
                d = json.loads(r.stdout.decode(errors="replace") or "{}")
                for p in d.get("programs") or []:
                    tags = p.get("tags") or {}
                    name = (tags.get("service_name") or tags.get("service_provider") or "").strip()
                    if name:
                        break
            except (subprocess.TimeoutExpired, ValueError, OSError):
                pass
            if name and name != self.cs:
                old = self.cs
                self.cs = name
                if self.rx_rf:
                    self.cs_by_freq[round(self.rx_rf, 2)] = name
                self.log("Rufzeichen: %s%s" % (name, (" (vorher %s)" % old) if old else ""))
                _rf = self.rx_rf
                self.root.after(0, lambda n=name, f=_rf: self.event("Rufzeichen  %s%s" % (n, ("  auf %.3f MHz" % f) if f else "")))
                self.root.after(0, self.show_cs)
                if self.player_wanted:
                    self.root.after(0, self.player_restart)   # Rufzeichenwechsel: Player stoppen + neu starten
            time.sleep(3)

    def show_cs(self):
        self.lbl_cs.configure(text=self.cs or "", bg="#27ae60" if self.cs else "#7f8c8d")

    # ------------------------------------------------ Player
    def player_cmd(self):
        ip, port = self.a.udp.rsplit(":", 1)
        url = "udp://@%s:%s" % (ip, port)
        p = self.a.player
        if p == "mpv":
            return ["mpv", "--title=miniswitcher", "--profile=low-latency", url]
        if p == "ffplay":
            return ["ffplay", "-window_title", "miniswitcher", "-fflags", "nobuffer", url]
        # --no-one-instance/--no-playlist-enqueue: sonst uebergibt VLC (Einstellung "nur eine Instanz") die
        # Adresse an ein schon laufendes VLC (z.B. das der leandvb-GUI) und beendet sich sofort wieder
        # --extraintf= (leer): die in vlcrc eingeschaltete Kommandozeilen-Schnittstelle (cli) abschalten - die
        # beendet VLC sonst sofort ("[cli] lua interface: Requested shutdown"), sobald sie keine Konsole hat
        return ["vlc", "--intf=qt", "--extraintf=", "--no-one-instance", "--no-playlist-enqueue",
                "--network-caching=1000", "--demux=ts", url,
                "--meta-title=miniswitcher", "--no-video-title-show"]

    def player_start(self):
        if self.player is not None and self.player.poll() is None:
            return
        cmd = self.player_cmd()
        exe = find_prog(cmd[0])
        if not exe:
            self.log("%s nicht gefunden - %s" % (cmd[0], INSTALL_HINTS.get(cmd[0], ("", ""))[0 if IS_WIN else 1]))
            return
        cmd[0] = exe
        try:
            lf = open(PLAYER_LOG, "a", encoding="utf-8", errors="replace")
            lf.write("\n# %s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(cmd)))
            lf.flush()
            # stdin als offene Pipe: ist im VLC eine Kommandozeilen-Schnittstelle (cli/rc) eingeschaltet, liest die
            # von stdin - bei /dev/null kommt sofort EOF und VLC beendet sich ("lua interface: Requested shutdown")
            self.player = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=lf, stderr=subprocess.STDOUT,
                                           **detached_kwargs())
            self.player_t0 = time.time()
            self.log("Player gestartet: %s" % " ".join(cmd))
        except OSError as e:
            self.log("Player-Start fehlgeschlagen: %s" % e)

    def player_stop(self):
        p = self.player
        self.player = None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def player_restart(self):
        self.log("Player: Stop/Start (Rufzeichenwechsel)")
        threading.Thread(target=lambda: (self.player_stop(), time.sleep(0.8),
                                         self.root.after(0, self.player_start)), daemon=True).start()

    def player_start_user(self):
        self.player_wanted = True
        self.player_start()

    def player_stop_user(self):
        self.player_wanted = False
        self.player_stop()

    # ------------------------------------------------ Ende
    def on_exit(self):
        try:
            self.cfg["geometry"] = self.root.geometry()
            cfg_save(self.cfg)
        except Exception:
            pass
        self.player_stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    p = argparse.ArgumentParser(description="miniswitcher - BATC-Spektrum + OnAir-Fenster (DG7NDV)")
    p.add_argument("--only-api", "--only_api", action="store_true", dest="only_api",
                   help="OnAir nicht starten/einrichten, nur die Frequenz an den Standard-Port schicken")
    p.add_argument("--port", type=int, default=8765, help="Remote-API-Port des OnAir-Fensters (Standard 8765)")
    p.add_argument("--lo", type=float, default=None,
                   help="LNB-LO in MHz - UNBEDINGT passend zum eigenen LNB setzen (z.B. --lo 9570 oder 9750; "
                        "ohne Angabe: zuletzt im Fenster eingestellter Wert, sonst 9540)")
    p.add_argument("--udp", default="230.0.0.10:1234", help="UDP-Ziel des Streams (Standard 230.0.0.10:1234)")
    p.add_argument("--device", default="", help="Geraet im OnAir, Teilstrings mit ';' (z.B. 'sdrplay;rsp2;antenna a'); "
                                                "leer = das im OnAir eingestellte")
    p.add_argument("--gain", type=float, default=None, help="Verstaerkung in dB beim Einrichten (leer = unveraendert)")
    p.add_argument("--player", choices=("vlc", "mpv", "ffplay"), default="vlc", help="Player (Standard vlc)")
    p.add_argument("--no-player", action="store_true", help="keinen Player starten")
    p.add_argument("--no-rate", action="store_true", help="Abtastrate nicht aus der Symbolrate setzen")
    p.add_argument("--onair-bin", default="", help="OnAir-Programm (Standard: onair im PATH)")
    p.add_argument("--batc-url", default="wss://eshail.batc.org.uk/wb/fft", help="BATC-Spektrum-WebSocket")
    a = p.parse_args()
    m = Mini(a)
    probs = check_programs(a)
    if not HAVE_WS:
        probs.insert(0, "Python-Modul 'websocket' fehlt (kein BATC-Spektrum) - installieren mit:  %s"
                     % _pip_hint("websocket-client"))
    for x in probs:
        m.log("FEHLT: " + x)
    m.log("LNB-LO %.3f MHz%s - muss zum eigenen LNB passen (aendern: oben im Feld LNB-LO oder Start mit --lo 9570 / 9750 ...)"
          % (a.lo, " (von --lo)" if m.lo_from_cli else " (gespeicherter/Standard-Wert)"))
    if probs:
        m.root.after(800, lambda: messagebox.showwarning(
            "miniswitcher - fehlende Komponenten",
            "Folgendes fehlt (%s):\n\n- %s" % ("Windows" if IS_WIN else "Linux", "\n- ".join(probs)), parent=m.root))
    m.run()


if __name__ == "__main__":
    main()
