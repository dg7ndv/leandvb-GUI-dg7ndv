#!/usr/bin/env python3
"""leandvb_einfach.py - "Einfach"-Ansicht des QO-100 Tuner Switchers: bis zu 4 Videobilder (2x2-Quad) direkt im Hauptfenster.

Die Videos laufen ueber libvlc (Paket python-vlc) in eingebetteten Tk-Frames, ohne einzelne VLC-Fenster. Jede Kachel zeigt Tunername,
Frequenz, Rufzeichen und MER; ein Klick auf die Kopfzeile schaltet den Ton dieser Kachel an (sonst alles stumm). Welcher Tuner in welcher
Kachel laeuft, entscheidet die Auswahl-Funktion der GUI (get_candidates: Tuner mit Lock auf einem lebenden Signal); pro Kachel kann der Tuner
auch von Hand gewaehlt werden. Eingebunden wird das Modul von leandvb-GUI.py (Menue "Einfach", Parameter -einfach).
"""
import ctypes
import re
import time
import tkinter as tk
from tkinter import ttk

try:
    import numpy as np
except Exception:                                  # noqa: BLE001
    np = None

try:
    import vlc
except Exception:                                  # noqa: BLE001
    vlc = None

N = 4                              # Mindestzahl Kacheln (2x2); bei mehr Signalen waechst das Raster: 6 (3+3), 8 (4+4) ... bis MAXN
MAXN = 12


class EinfachView:
    def __init__(self, root, before, get_candidates, get_info, get_url, labels, log, get_marquee=None, get_signals=None, tune=None, draw_rate=None, release=None, snapshot_path=None, record=None, rec_state=None, get_spectrum=None, spec_changed=None):
        """root: Tk-Hauptfenster; before: Widget, vor dem der Quad-Bereich eingefuegt wird; get_candidates() -> Liste Tuner-IDs mit Signal;
        get_info(t) -> dict(label, freq, sr, call, mer); get_url(t) -> Stream-Adresse; labels: {Tuner-ID: Name}; log(text)"""
        if vlc is None:
            raise RuntimeError("python-vlc fehlt (pip install --user --break-system-packages python-vlc)")
        self.root, self.get_candidates, self.get_info, self.get_url, self.labels, self.log = root, get_candidates, get_info, get_url, labels, log
        self.snapshot_path, self.record, self.rec_state = snapshot_path, record, rec_state     # Snapshot-Dateiname (Tuner) / Aufnahme an-aus (Tuner) / laeuft Aufnahme? (Tuner)
        self.get_spectrum, self.spec_changed = get_spectrum, spec_changed     # () -> {start, span, pts[0..1], sigs[{f0,f1,label,tuners,beacon}]} bzw. None; spec_changed(an) merkt den Schalter
        self.spec_on, self.spec_frame, self.spec_cv = False, None, None
        self.release_cb = release                           # Tuner freigeben (GUI: Player stoppen, Tuner frei fuer neue Signale)
        self.blocked = {}                                   # Tuner -> Zeit, bis zu der er nicht wieder in eine Kachel kommt (nach "Freigeben")
        self.draw_rate = draw_rate                          # (Tuner, Canvas): Datenstrom-Kurve wie im Hauptfenster unter jeder Kachel
        self.get_signals, self.tune = get_signals, tune     # Rechtsklick-Menue: alle Signale (Liste (Text, Schluessel)) / Tuner auf ein Signal stellen
        self.get_marquee = get_marquee                # t -> Einblendungstext (Rufzeichen | MER | ...) wie in den Einzelfenstern
        self.inst = vlc.Instance("--no-video-title-show", "--quiet", "--network-caching=800", "--avcodec-hw=none", "--no-osd", "--no-snapshot-preview", "--no-mouse-events", "--no-keyboard-events")
        self.frame = tk.Frame(root, bg="#0b0f14")
        self.frame.pack(side="top", fill="both", expand=True, before=before)
        self.frame.grid_propagate(False)
        for i in range(2):
            self.frame.grid_rowconfigure(i, weight=1, uniform="r")
            self.frame.grid_columnconfigure(i, weight=1, uniform="c")
        self.cells = []
        self.n = N                        # aktuell sichtbare Kacheln
        self.fixed_n = None               # feste Kachelzahl (None = automatisch)
        self.target = [None] * MAXN          # was laeuft in der Kachel
        self.manual = [None] * MAXN          # von Hand gewaehlter Tuner (None = automatisch)
        self.pos = list(range(MAXN))         # Rasterplatz je Kachel (0 oben links, 1 oben rechts, 2 unten links, 3 unten rechts)
        self.zoomed = None                # Kachel im Grossbild (Doppelklick)
        self.solo = None                  # Kachel mit Ton (Klick auf die Kopfzeile)
        self.vol = [70] * MAXN               # Lautstaerke je Kachel 0-100 (Regler, Mausrad ueber der Kopfzeile, Tasten +/-)
        self.muted = [True] * MAXN           # stumm (Taste m, solange der Mauszeiger ueber der Kachel steht)
        self.alive = True
        names = ["automatisch"] + [labels[t] for t in labels]
        self.ids = [None] + list(labels)
        for i in range(N):
            self._make_cell(i)
        for ev in ("<KeyPress-m>", "<KeyPress-M>", "<KeyPress-space>", "<KeyPress-p>", "<KeyPress-P>", "<KeyPress-s>", "<KeyPress-S>", "<KeyPress-i>", "<Control-j>", "<Control-J>", "<KeyPress-plus>", "<KeyPress-minus>", "<KeyPress-Up>", "<KeyPress-Down>", "<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.root.bind_all(ev, self._key, add="+")
        self._layout()
        self.root.after(300, self.tick)
        self.root.after(1000, self._spec_tick)
        self.root.after(600, self._meter_tick)
        self.root.after(900, self._rec_tick)

    def _make_cell(self, i):
        names = ["automatisch"] + [self.labels[t] for t in self.labels]
        cell = tk.Frame(self.frame, bg="#0b0f14", highlightthickness=1, highlightbackground="#2a323c")
        head = tk.Frame(cell, bg="#1d2530")
        head.pack(side="top", fill="x")
        def _btn(txt, fg, cmd):
            b = tk.Label(head, text=txt, bg="#2a3340", fg=fg, font=("", 10, "bold"), cursor="hand2", padx=4, pady=1, relief="raised", bd=1)
            b.bind("<Button-1>", lambda e, k=i: cmd(k))
            return b
        stop_b = _btn("\u25a0 Stop", "#e8ebef", self.release)               # Player stoppen + Tuner frei
        snap_b = _btn("\U0001F4F7 Foto", "#e8ebef", self.snapshot)
        rec = _btn("\u25cf Video", "#ffffff", self.toggle_record)           # weiss; nur bei laufender Aufnahme rot hinterlegt (REC)
        ic = tk.Canvas(head, width=20, height=20, bg="#1d2530", highlightthickness=0, cursor="hand2")
        ic.pack(side="right", padx=(0, 4))
        ic.bind("<Button-1>", lambda e, k=i: self.toggle_mute(k))
        sc = ttk.Scale(head, from_=0, to=100, orient="horizontal", length=70, command=lambda v, k=i: self.set_vol(k, float(v)))
        sc.set(self.vol[i])
        sc.pack(side="right", padx=(0, 4))
        cb = ttk.Combobox(head, values=names, width=9, state="readonly")
        cb.set("automatisch")
        cb.pack(side="right", padx=4, pady=2)
        stop_b.pack(side="right", padx=2, pady=2)
        snap_b.pack(side="right", padx=2, pady=2)
        rec.pack(side="right", padx=2, pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e, k=i: self.pick(k))
        lbl = tk.Label(head, text="frei", anchor="w", bg="#2d4f8f", fg="#ffe066", font=("", 15, "bold"), cursor="hand2", padx=8)     # Rufzeichen: gross, farbig abgesetzt
        lbl.pack(side="left", padx=(4, 0), pady=2)
        lbl2 = tk.Label(head, text="", anchor="w", bg="#1d2530", fg="#e8ebef", font=("", 10, "bold"), cursor="hand2")
        lbl2.pack(side="left", padx=(4, 6), pady=2)
        lbl.bind("<Button-1>", lambda e, k=i: self.set_solo(k))
        lbl2.bind("<Button-1>", lambda e, k=i: self.set_solo(k))
        rate = tk.Canvas(cell, height=46, bg="#101418", highlightthickness=1, highlightbackground="#3a3a3a")
        rate.pack(side="bottom", fill="x", padx=2, pady=(2, 0))
        rcol = tk.Frame(cell, width=40, bg="#101418")
        rcol.pack(side="right", fill="y", padx=(2, 2), pady=2)
        rcol.pack_propagate(False)
        merc = tk.Canvas(rcol, bg="#101418", highlightthickness=1, highlightbackground="#3a3a3a")
        merc.place(relx=0, rely=0, relwidth=1, relheight=0.2)              # MER-Balken: oben, 1/5 der Hoehe
        meter = tk.Canvas(rcol, bg="#101418", highlightthickness=1, highlightbackground="#3a3a3a")
        meter.place(relx=0, rely=0.2, relwidth=1, relheight=0.8)           # Tonanzeige (kommt Ton an?) - siehe _meter_tick
        area = tk.Frame(cell, bg="#0b0f14")
        area.pack(side="top", fill="both", expand=True)
        video = tk.Frame(area, bg="black")
        empty = tk.Label(area, text="kein Signal", bg="#0b0f14", fg="#566273", font=("", 12))
        empty.place(relx=0.5, rely=0.5, anchor="center")
        area.bind("<Configure>", lambda e, k=i: self.fit(k))
        player = self.inst.media_player_new()
        try:
            player.video_set_mouse_input(False)
            player.video_set_key_input(False)
        except Exception:                      # noqa: BLE001
            pass
        player.audio_set_mute(True)
        for w in (area, video, empty):
            w.bind("<Double-Button-1>", lambda e, k=i: self.toggle_zoom(k))
            w.bind("<Button-3>", lambda e, k=i: self.context(k, e))
        lbl.bind("<Button-3>", lambda e, k=i: self.context(k, e))
        self.cells.append({"cell": cell, "lbl": lbl, "lbl2": lbl2, "merc": merc, "mer": 0.0, "cb": cb, "area": area, "video": video, "empty": empty, "player": player, "xid": None, "mrl": None, "sc": sc, "ic": ic, "rec": rec, "rate": rate, "meter": meter, "abuf": None, "ton": 0.0, "mp": None, "lvl": -90.0, "disp": -90.0, "peak": -90.0, "peak_t": 0.0})
        self._meter_player(i)
        self._icon(i)

    # --- Raster, Grossbild, Position, Rechtsklick-Menue ---
    def set_spectrum(self, on):
        """Spektrum unter den Kacheln ein/aus (Standard aus): Kurve + Rahmen je erkanntem Signal (Rufzeichen, Symbolrate, Tuner)"""
        on = bool(on) and self.get_spectrum is not None
        if on == self.spec_on:
            return
        self.spec_on = on
        if on:
            self.spec_frame = tk.Frame(self.root, bg="#0b0f14", height=170)
            self.spec_frame.pack_propagate(False)
            self.spec_frame.pack(side="top", fill="x", after=self.frame)
            self.spec_cv = tk.Canvas(self.spec_frame, bg="#0b0f14", highlightthickness=0)
            self.spec_cv.pack(fill="both", expand=True)
            self.spec_cv.bind("<Button-3>", lambda e: self.set_spectrum(False))
            self.spec_cv.bind("<Configure>", lambda e: self._spec_draw())
            self._spec_draw()
        else:
            if self.spec_frame is not None:
                self.spec_frame.destroy()
            self.spec_frame = self.spec_cv = None
        self.log("Festfenster: Spektrum %s." % ("AN" if on else "AUS"))
        if self.spec_changed:
            try:
                self.spec_changed(on)
            except Exception:                                      # noqa: BLE001
                pass
        self.root.after(100, lambda: [self.fit(k) for k in range(len(self.cells))])

    def _spec_tick(self):
        if not self.alive:
            return
        if self.spec_on:
            self._spec_draw()
        self.root.after(1000, self._spec_tick)

    def _spec_draw(self):
        c = self.spec_cv
        if c is None:
            return
        try:
            sp = self.get_spectrum() if self.get_spectrum else None
        except Exception:                                          # noqa: BLE001
            sp = None
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 40:
            return
        pts = (sp or {}).get("pts") or []
        if not pts:
            c.create_text(10, h // 2, anchor="w", fill="#8b949e", text="warte auf BATC-Spektrum ...")
            return
        start, span = sp["start"], sp["span"]
        L = 34 if sp.get("hi") is not None else 0                  # links Platz fuer die dB-Skala (wie auf der Pi-Webseite)
        base, top = h - 20, 6
        pw = w - L
        n = len(pts)
        def X(f):
            return L + (f - start) / span * pw
        def Y(v):
            return base - min(1.0, max(0.0, v)) * (base - top - 4)
        if L:                                                      # waagerechte Hilfslinien mit dB-Werten
            lo, hi = sp["lo"], sp["hi"]
            for q in range(5):
                fr = q / 4.0
                y = Y(fr)
                c.create_line(L, y, w, y, fill="#1b2430")
                c.create_text(L - 4, y, anchor="e", fill="#7d8a99", font=("TkDefaultFont", 8), text="%d dB" % round(lo + fr * (hi - lo)))
        if sp.get("quelle"):
            c.create_text(w - 6, 4, anchor="ne", fill="#7d8a99", font=("TkDefaultFont", 8), text="Quelle: %s" % sp["quelle"])
        for lvl, col in ((1.0, "#2fbf6a"), (0.8, "#27a85c"), (0.6, "#1f8f4c"), (0.4, "#18733d"), (0.2, "#125a31")):   # Verlauf: oben hell, unten dunkel
            poly = [L, base]
            for k, v in enumerate(pts):
                poly += [L + k / (n - 1) * pw, Y(min(v, lvl))]
            poly += [w, base]
            c.create_polygon(poly, fill=col, outline="")
        line = []
        for k, v in enumerate(pts):
            line += [L + k / (n - 1) * pw, Y(v)]
        c.create_line(line, fill="#d7f7df")
        for mhz in range(int(start) + 1, int(start + span) + 1):
            x = X(mhz)
            c.create_line(x, top, x, base, fill="#1b2430")
            c.create_text(x, h - 9, text=str(mhz), fill="#9aa5b1", font=("TkDefaultFont", 8))
        for sg in sp.get("sigs") or []:
            cx = X((sg["f0"] + sg["f1"]) / 2.0)                    # gestrichelte Linie in der Signalmitte, daneben Name / Symbolrate / Stick untereinander
            tun = sg.get("tuners") or []
            col = "#58a6ff" if sg.get("beacon") else ("#4fa0ff" if tun else "#ffd33d")
            c.create_line(cx, top, cx, base, fill=col, dash=(2, 3))
            lines = [(sg.get("label") or "", col, ("TkDefaultFont", 9, "bold"))]
            if sg.get("sub"):
                lines.append((sg["sub"], col, ("TkDefaultFont", 9, "bold")))
            if tun:
                lines.append((", ".join(tun), "#9ec5ff", ("TkDefaultFont", 8)))
            for n_, (txt, fc, fnt) in enumerate(lines):
                c.create_text(cx + 4, top + n_ * 13, anchor="nw", fill=fc, font=fnt, text=txt)

    def _shape(self):
        """(Kacheln oben, Kacheln unten): 5 -> 3+2, 6 -> 3+3, 7 -> 4+3, 8 -> 4+4 ... (2 Zeilen)"""
        top = (self.n + 1) // 2
        return top, self.n - top

    def _norm_pos(self):
        order = sorted(range(self.n), key=lambda k: self.pos[k])
        for rank, k in enumerate(order):
            self.pos[k] = rank

    def _layout(self):
        import math
        self._norm_pos()
        top, bot = self._shape()
        units = top if not bot else top * bot // math.gcd(top, bot)       # gemeinsames Raster, damit 3+2 beide Zeilen vollstaendig fuellt
        for i in range(24):
            self.frame.grid_columnconfigure(i, weight=1 if i < units else 0, uniform="c" if i < units else "")
        rows = 2 if bot else 1
        for i in range(2):
            self.frame.grid_rowconfigure(i, weight=1 if i < rows else 0, uniform="r" if i < rows else "")
        for k, c in enumerate(self.cells):
            if k >= self.n:
                c["cell"].grid_remove()
            elif self.zoomed is None:
                p = self.pos[k]
                if p < top:
                    r, idx, span = 0, p, units // top
                else:
                    r, idx, span = 1, p - top, units // bot
                c["cell"].grid(row=r, column=idx * span, rowspan=1, columnspan=span, sticky="nsew", padx=2, pady=2)
            elif self.zoomed == k:
                c["cell"].grid(row=0, column=0, rowspan=rows, columnspan=units, sticky="nsew", padx=2, pady=2)
                c["cell"].lift()
            else:
                c["cell"].grid_remove()
        self.root.after(80, lambda: [self.fit(k) for k in range(len(self.cells))])

    def set_fixed(self, n):
        """Kachelzahl fest vorgeben (z.B. 5-fach) oder None = automatisch nach Zahl der Signale"""
        self.fixed_n = n
        self.log("Festfenster: Kachelzahl %s." % ("automatisch" if n is None else n))
        self.tick(once=True)

    def set_count(self, n):
        """Kachelzahl aendern: fehlende Kacheln anlegen, ueberzaehlige leeren und ausblenden"""
        n = max(1, min(MAXN, n))
        if n == self.n:
            return
        while len(self.cells) < n:
            self._make_cell(len(self.cells))
        for k in range(n, self.n):
            self.target[k] = None
            self.manual[k] = None
            self._play(k, None)
        self.n = n
        if self.zoomed is not None and self.zoomed >= n:
            self.zoomed = None
        self._layout()
        self.log("Festfenster: %d Kacheln (%d + %d)." % ((n,) + self._shape()))

    def toggle_zoom(self, k):
        self.zoomed = None if self.zoomed == k else k
        self._layout()
        self.log("Festfenster: Kachel %d %s." % (k + 1, "gross" if self.zoomed is not None else "wieder normal"))

    def move_to(self, k, position):
        other = next(j for j in range(self.n) if self.pos[j] == position)
        self.pos[other], self.pos[k] = self.pos[k], self.pos[other]
        self.zoomed = None
        self._layout()

    def pick_position(self, k):
        """kleines Fenster mit 2x2-Raster: den Platz fuer diese Kachel anklicken"""
        win = tk.Toplevel(self.root)
        win.title("Position von Kachel %d" % (k + 1))
        win.transient(self.root)
        win.resizable(False, False)
        top, bot = self._shape()
        for position in range(self.n):
            who = next(j for j in range(self.n) if self.pos[j] == position)
            t = self.target[who]
            r, col = (0, position) if position < top else (1, position - top)
            txt = "%s %d\n%s" % ("oben" if r == 0 else "unten", col + 1, (self.labels.get(t, "frei") if t else "frei"))
            cur = (who == k)
            b = tk.Button(win, text=txt, width=14, height=4, bg=("#2ecc71" if cur else "#e8ebef"), relief="sunken" if cur else "raised",
                          command=lambda p=position: (self.move_to(k, p), win.destroy()))
            b.grid(row=r, column=col, padx=3, pady=3)
        try:
            win.grab_set()
        except tk.TclError:
            pass

    def context(self, k, e=None):
        t = self.target[k]
        m = tk.Menu(self.root, tearoff=False)
        inf = (self.get_info(t) or {}) if t else {}
        head = "Kachel %d: %s%s" % (k + 1, self.labels.get(t, "frei"), ("  " + inf["mer"]) if inf.get("mer") else "")
        sm = tk.Menu(m, tearoff=False)
        sigs = []
        try:
            sigs = list(self.get_signals() or []) if self.get_signals else []
        except Exception:                                          # noqa: BLE001
            pass
        if sigs and self.tune:
            for text, key in sigs:
                sm.add_command(label=text, command=lambda key=key: self.pick_signal(k, key))
        else:
            sm.add_command(label="(keine Signale)", state="disabled")
        m.add_cascade(label="Signal waehlen", menu=sm)
        m.add_separator()
        m.add_command(label=head, state="disabled")
        m.add_separator()
        m.add_command(label="Bild gross / normal  (Doppelklick)", command=lambda: self.toggle_zoom(k))
        m.add_command(label="Snapshot speichern", command=lambda: self.snapshot(k))
        rec_on = bool(t and self.rec_state and self.rec_state(t))
        m.add_command(label="\u25a0 Aufnahme stoppen" if rec_on else "\u25cf Video aufnehmen", command=lambda: self.toggle_record(k),
                      state="normal" if t else "disabled", foreground=("#c0392b" if rec_on else "black"))
        m.add_command(label="Position waehlen ...", command=lambda: self.pick_position(k))
        m.add_command(label="\u25a0 Stop: Player stoppen, Bild loeschen, Tuner freigeben", command=lambda: self.release(k))
        m.add_command(label="Ton an / stumm  (m)", command=lambda: self.toggle_mute(k))
        m.add_command(label="Pause / Weiter  (Leertaste)", command=lambda: self._key_cmd(k, "space"))
        m.add_separator()
        tm = tk.Menu(m, tearoff=False)
        tm.add_command(label="automatisch", command=lambda: self._set_manual(k, None))
        for tid in self.ids[1:]:
            tm.add_command(label=self.labels.get(tid, tid), command=lambda tid=tid: self._set_manual(k, tid))
        m.add_cascade(label="Tuner dieser Kachel", menu=tm)
        nm = tk.Menu(m, tearoff=False)
        nm.add_command(label="automatisch", command=lambda: self.set_fixed(None))
        for n_ in (2, 3, 4, 5, 6, 7, 8, 9, 10, 12):
            nm.add_command(label="%d Kacheln" % n_, command=lambda n_=n_: self.set_fixed(n_))
        m.add_cascade(label="Kachelzahl", menu=nm)
        if self.get_spectrum:
            m.add_command(label="Spektrum aus" if self.spec_on else "Spektrum ein (unter den Kacheln)", command=lambda: self.set_spectrum(not self.spec_on))
        try:
            x, y = (e.x_root, e.y_root) if e is not None else self.root.winfo_pointerxy()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def snapshot(self, k):
        t = self.target[k]
        if t is None or not self.cells[k]["mrl"]:
            self.log("Festfenster: Kachel %d hat kein Bild - kein Snapshot." % (k + 1))
            return
        try:
            path = self.snapshot_path(t) if self.snapshot_path else None
            if not path:
                self.log("Festfenster: kein Snapshot-Ordner bekannt.")
                return
            ok = self.cells[k]["player"].video_take_snapshot(0, path, 0, 0)
            self.log("Festfenster: Snapshot %s: %s" % ("gespeichert" if ok == 0 else "fehlgeschlagen", path))
        except Exception as e:                                     # noqa: BLE001
            self.log("Festfenster: Snapshot fehlgeschlagen (%r)" % (e,))

    def toggle_record(self, k):
        t = self.target[k]
        if t is None or self.record is None:
            return
        try:
            self.record(t)
        except Exception as e:                                     # noqa: BLE001
            self.log("Festfenster: Aufnahme (%r)" % (e,))
        self._rec_icons()

    def _rec_icons(self):
        for k, c in enumerate(self.cells):
            t = self.target[k] if k < self.n else None
            on = False
            try:
                on = bool(t and self.rec_state and self.rec_state(t))
            except Exception:                                      # noqa: BLE001
                pass
            c["rec"].configure(text="\u25a0 REC" if on else "\u25cf Video", bg="#c0392b" if on else "#2a3340", fg="#ffffff")

    def release(self, k):
        """Rechtsklick 'Player stoppen': Kachel leeren, Tuner freigeben (GUI stoppt den Player, der Tuner wird frei fuer neue Signale)"""
        t = self.target[k] or self.manual[k]
        self.manual[k] = None
        self.target[k] = None
        try:
            self.cells[k]["cb"].set("automatisch")
        except tk.TclError:
            pass
        self._play(k, None)
        if t is not None:
            self.blocked[t] = time.time() + 25.0
            self.log("Festfenster: Kachel %d gestoppt - %s freigegeben." % (k + 1, self.labels.get(t, t)))
            if self.release_cb:
                try:
                    self.release_cb(t)
                except Exception as e:                             # noqa: BLE001
                    self.log("Festfenster: Freigabe fehlgeschlagen (%r)" % (e,))
        self.tick(once=True)

    def _key_cmd(self, k, ks):
        class _E:
            widget = self.root
            keysym = ks
            num = 0
            delta = 0
        old = self._cell_at_pointer
        self._cell_at_pointer = lambda: k
        try:
            self._key(_E())
        finally:
            self._cell_at_pointer = old

    def refresh_labels(self):
        """Stickliste hat sich geaendert (Labels wurden in self.labels ersetzt): Auswahlfelder und Menue-Liste auffrischen"""
        self.ids = [None] + list(self.labels)
        names = ["automatisch"] + [self.labels[t] for t in self.labels]
        for c in self.cells:
            try:
                c["cb"].configure(values=names)
            except tk.TclError:
                pass

    def _set_manual(self, k, tid):
        self.manual[k] = tid
        names = ["automatisch"] + [self.labels[t] for t in self.labels]
        try:
            self.cells[k]["cb"].set(self.labels.get(tid, "automatisch") if tid else "automatisch")
        except tk.TclError:
            pass
        self.tick(once=True)

    def pick_signal(self, k, key):
        """Signal direkt waehlen: der Tuner der Kachel (sonst der erste freie) wird darauf abgestimmt, die Kachel bleibt bei diesem Tuner"""
        t = self.target[k] or self.manual[k]
        if t is None:
            busy = set(x for x in self.target if x) | set(x for x in self.manual if x)
            t = next((x for x in self.ids[1:] if x not in busy), None)
        if t is None:
            self.log("Festfenster: kein freier Tuner fuer dieses Signal.")
            return
        self._set_manual(k, t)
        self.tune(t, key)

    # --- Lautstaerke / Stumm ---
    def _cell_at_pointer(self):
        """Kachel unter dem Mauszeiger (nach Bildschirmkoordinaten, auch ueber dem eingebetteten Video) oder None"""
        try:
            px, py = self.root.winfo_pointerxy()
            for k, c in enumerate(self.cells):
                w = c["cell"]
                if not w.winfo_ismapped():
                    continue
                x, y = w.winfo_rootx(), w.winfo_rooty()
                if x <= px < x + w.winfo_width() and y <= py < y + w.winfo_height():
                    return k
        except tk.TclError:
            pass
        return None

    def _key(self, e):
        if not self.alive:
            return
        k = self._cell_at_pointer()
        if k is None:
            return
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        ks, num = getattr(e, "keysym", ""), getattr(e, "num", 0)
        if ks in ("space", "p", "P"):                       # Pause / Weiter wie im VLC
            try:
                self.cells[k]["player"].pause()
            except Exception:                              # noqa: BLE001
                pass
        elif ks in ("s", "S"):                             # Stopp + sofort neu starten (Stream neu aufbauen)
            self._play(k, self.target[k])
        elif ks in ("i", "j", "J"):                        # Strg+J / i: Medieninformation der Kachel
            self._info(k)
        elif ks in ("m", "M"):
            self.muted[k] = not self.muted[k]
            self._audio(k)
        elif ks in ("plus", "Up") or num == 4 or getattr(e, "delta", 0) > 0:
            self.set_vol(k, self.vol[k] + 5, from_key=True)
        elif ks in ("minus", "Down") or num == 5 or getattr(e, "delta", 0) < 0:
            self.set_vol(k, self.vol[k] - 5, from_key=True)

    def _info(self, k):
        from tkinter import messagebox
        c = self.cells[k]
        t = self.target[k]
        try:
            pl = c["player"]
            w, h = pl.video_get_size(0)
            txt = "Kachel %d: %s\nStream: %s\nZustand: %s\nBild: %sx%s\nLautstaerke: %d%% (%s)" % (
                k + 1, self.labels.get(t, "frei"), c["mrl"] or "-", str(pl.get_state()).split(".")[-1], w, h, self.vol[k], "stumm" if self.muted[k] else "an")
        except Exception as e:                             # noqa: BLE001
            txt = "Kachel %d: keine Information (%r)" % (k + 1, e)
        messagebox.showinfo("Medieninformation", txt, parent=self.root)

    def set_vol(self, k, v, from_key=False):
        v = max(0, min(100, int(round(v))))
        if v == self.vol[k] and not from_key:
            return
        self.vol[k] = v
        if v > 0 and from_key:
            self.muted[k] = False
        if from_key:
            try:
                self.cells[k]["sc"].set(v)
            except tk.TclError:
                pass
        self._audio(k)

    def toggle_mute(self, k):
        self.muted[k] = not self.muted[k]
        self._audio(k)

    def _icon(self, k):
        """Stumm-Anzeige in der Kopfzeile: roter Kreis mit Strich (stumm) bzw. gruener Lautsprecher mit Pegelstrichen (Ton an)"""
        ic = self.cells[k]["ic"]
        ic.delete("all")
        if self.muted[k]:
            ic.create_oval(2, 2, 18, 18, outline="#e74c3c", width=2)
            ic.create_line(4, 16, 16, 4, fill="#e74c3c", width=2)
        else:
            ic.create_polygon(3, 8, 7, 8, 11, 4, 11, 16, 7, 12, 3, 12, fill="#2ecc71", outline="#2ecc71")
            n = 1 + int(self.vol[k] / 34)                          # 1-3 Pegelbogen
            for j in range(n):
                ic.create_arc(8 - 2 * j, 10 - 3 - 3 * j, 12 + 2 * j + 3, 10 + 3 + 3 * j, start=-50, extent=100, style="arc", outline="#2ecc71", width=1)

    def _audio(self, k):
        self._icon(k)
        c = self.cells[k]
        try:
            c["player"].audio_set_volume(self.vol[k])
            c["player"].audio_set_mute(bool(self.muted[k]))
        except Exception:                                         # noqa: BLE001
            pass

    # --- Kachel-Hilfen ---
    def fit(self, k):
        """Videoflaeche im Bildformat 16:9 so gross wie moeglich in der Kachel"""
        c = self.cells[k]
        w, h = c["area"].winfo_width(), c["area"].winfo_height()
        if w < 20 or h < 20:
            return
        vw, vh = (w, int(w * 9 / 16)) if int(w * 9 / 16) <= h else (int(h * 16 / 9), h)
        c["video"].place(relx=0.5, rely=0.5, anchor="center", width=vw, height=vh)

    def pick(self, k):
        i = self.cells[k]["cb"].current()
        self.manual[k] = self.ids[i] if 0 <= i < len(self.ids) else None
        self.tick(once=True)

    def set_solo(self, k):
        self.solo = None if self.solo == k else k
        for j in range(len(self.cells)):
            self.muted[j] = (j != self.solo)
            self._audio(j)
        self.log("Einfach: Ton %s." % ("aus" if self.solo is None else "von Kachel %d (%s)" % (k + 1, self.labels.get(self.target[k], "-"))))

    def _play(self, k, t):
        c = self.cells[k]
        c["player"].stop()
        c["mrl"] = None
        self._meter_media(k, None)
        if t is None:
            c["video"].place_forget()
            c["empty"].place(relx=0.5, rely=0.5, anchor="center")
            return
        url = self.get_url(t)
        if not url:
            c["empty"].place(relx=0.5, rely=0.5, anchor="center")
            return
        c["empty"].place_forget()
        self.fit(k)
        self.root.update_idletasks()
        xid = c["video"].winfo_id()
        c["player"].set_xwindow(xid)
        media = self.inst.media_new(url)
        c["player"].set_media(media)
        c["player"].play()
        self._audio(k)
        c["mrl"] = url
        self._marq_init(c["player"])
        self._meter_media(k, url)

    def _marq_init(self, player):
        """Einblendung (Marquee) oben links: weiss, 18 Pixel, immer an; der Text wird im Takt gesetzt"""
        try:
            o = vlc.VideoMarqueeOption
            player.video_set_marquee_int(o.Enable, 1)
            player.video_set_marquee_int(o.Position, 5)          # oben links
            player.video_set_marquee_int(o.Size, 18)
            player.video_set_marquee_int(o.Color, 0xFFFFFF)
            player.video_set_marquee_int(o.Opacity, 255)
            player.video_set_marquee_int(o.Refresh, 1000)
            player.video_set_marquee_int(o.X, 6)
            player.video_set_marquee_int(o.Y, 4)
        except Exception:                                         # noqa: BLE001
            pass

    def _marq_text(self, k, t):
        if self.get_marquee is None:
            return
        try:
            txt = self.get_marquee(t) or ""
            self.cells[k]["player"].video_set_marquee_string(vlc.VideoMarqueeOption.Text, txt)
        except Exception:                                         # noqa: BLE001
            pass

    def _meter_player(self, k):
        """Zweiter, unsichtbarer libvlc-Player je Kachel (nur Audio) auf demselben Stream: Audio-Callback liefert die PCM-Daten, daraus wird
        der Pegel der Summe (L+R)/2 in dBFS berechnet - unabhaengig davon, ob die Kachel stumm ist. Kein Ton wird ausgegeben."""
        if np is None:
            return
        c = self.cells[k]
        try:
            mp = self.inst.media_player_new()

            def play(_opaque, samples, count, _pts, k=k):
                try:
                    a = np.frombuffer(ctypes.string_at(samples, count * 4), dtype=np.int16).astype(np.float32).reshape(-1, 2)
                    sm = (a[:, 0] + a[:, 1]) * (0.5 / 32768.0)
                    pk = float(np.max(np.abs(sm))) if len(sm) else 0.0
                    self.cells[k]["lvl"] = 20.0 * np.log10(pk) if pk > 1e-5 else -90.0
                except Exception:                                  # noqa: BLE001
                    pass

            noop = lambda *a, **kw: None                           # noqa: E731
            D = vlc.CallbackDecorators
            cbs = (D.AudioPlayCb(play), D.AudioPauseCb(noop), D.AudioResumeCb(noop), D.AudioFlushCb(noop), D.AudioDrainCb(noop))
            mp.audio_set_callbacks(*cbs, None)
            mp.audio_set_format("S16N", 48000, 2)
            c["mp"], c["mcb"] = mp, cbs                            # Callbacks referenziert halten
        except Exception as e:                                     # noqa: BLE001
            self.log("Festfenster: Pegelmesser nicht moeglich (%r)" % (e,))

    def _meter_media(self, k, url):
        c = self.cells[k]
        mp = c.get("mp")
        if mp is None:
            return
        try:
            mp.stop()
            c["lvl"] = -90.0
            if url:
                med = self.inst.media_new(url)
                med.add_option(":no-video")
                mp.set_media(med)
                mp.play()
        except Exception:                                          # noqa: BLE001
            pass

    mer_max = 11.0                                                # MER-Skala 0...11 dB (Bake-MER + 2; z.B. 9 dB -> 11); per view.mer_max aenderbar
    DB_MIN = -60.0

    def _meter_tick(self):
        """Pegelbalken je Kachel (alle 100 ms): Summenpegel in dBFS (-60 ... 0), Fallgeschwindigkeit begrenzt, Spitzenmarke (hellblau) 2 s gehalten"""
        if not self.alive:
            return
        now = time.time()
        for k, c in enumerate(self.cells):
            m = c["meter"]
            try:
                self._mer_draw(k, c)
            except Exception:                                          # noqa: BLE001
                pass
            try:
                lv = c["lvl"] if (c["mrl"] and self.target[k]) else -90.0
                c["disp"] = max(lv, c["disp"] - 2.4)                   # ~24 dB/s Rueckgang
                if lv >= c["peak"] or now - c["peak_t"] > 2.0:
                    c["peak"], c["peak_t"] = max(lv, c["disp"]), now
                h, w = m.winfo_height(), m.winfo_width()
                m.delete("all")
                if h < 40:
                    continue
                top, bot = 6, h - 6
                span = float(bot - top)

                def y_of(db):
                    return bot - (max(self.DB_MIN, min(0.0, db)) - self.DB_MIN) / (0.0 - self.DB_MIN) * span
                x0, x1 = 24, w - 3
                for db in (0, -6, -12, -20, -30, -40, -60):
                    m.create_text(2, y_of(db), text=str(db), anchor="w", fill="#e0b030", font=("", 6))
                n = 40
                sh = span / n
                lvl = c["disp"]
                for j in range(n):
                    dbj = self.DB_MIN + (j + 1) * (0.0 - self.DB_MIN) / n
                    yy = bot - j * sh
                    if dbj <= lvl:
                        col = "#2ecc71" if dbj < -9 else ("#f1c40f" if dbj < -3 else "#e74c3c")
                    else:
                        col = "#22282f"
                    m.create_rectangle(x0, yy - sh + 1, x1, yy, fill=col, outline="")
                if c["peak"] > self.DB_MIN:
                    yp = y_of(c["peak"])
                    m.create_rectangle(x0, yp - 1, x1, yp + 1, fill="#7fd4ff", outline="")
            except Exception:                                          # noqa: BLE001
                pass
        self.root.after(100, self._meter_tick)

    def _mer_draw(self, k, c):
        """MER-Balken (0...MER_MAX = 11 dB, rot < 4, gelb < 7, gruen darueber) oben ueber dem Tonbalken"""
        m = c["merc"]
        h, w = m.winfo_height(), m.winfo_width()
        m.delete("all")
        if h < 20:
            return
        v = c.get("mer") if (self.target[k] and c["mrl"]) else None
        top, bot = (12 if h > 40 else 2), h - 3
        mx = float(self.mer_max)
        span = float(bot - top)
        if h > 40:
            m.create_text(2, 1, text="MER", anchor="nw", fill="#e0b030", font=("", 6))
        for db in ((0, int(mx / 2), int(mx)) if h > 70 else ()):
            m.create_text(2, bot - db / mx * span, text=str(db), anchor="w", fill="#e0b030", font=("", 6))
        n = int(mx)
        sh = span / n
        for j in range(n):
            dbj = (j + 1) * mx / n
            yy = bot - j * sh
            col = "#22282f"
            if v is not None and dbj <= v + 0.5:
                col = "#e74c3c" if dbj < 4 else ("#f1c40f" if dbj < 7 else "#2ecc71")
            m.create_rectangle(24, yy - sh + 1, w - 3, yy, fill=col, outline="")

    def _rec_tick(self):
        if not self.alive:
            return
        self._rec_icons()
        self.root.after(1000, self._rec_tick)

    def _rate(self, k, t):
        if self.draw_rate is None:
            return
        try:
            self.draw_rate(t, self.cells[k]["rate"])
        except Exception:                                         # noqa: BLE001
            pass

    # --- Takt: Zuordnung Tuner -> Kachel und Kopfzeilen ---
    def tick(self, once=False):
        if not self.alive:
            return
        try:
            now_b = time.time()
            self.blocked = {t: u for t, u in self.blocked.items() if u > now_b}
            cand = [t for t in self.get_candidates() if t in self.labels and t not in self.blocked]
            nwant = len([1 for t in cand]) + len([1 for k in range(self.n) if self.manual[k] and self.manual[k] not in cand])
            self.set_count(self.fixed_n if self.fixed_n else max(N, 2 * ((nwant + 1) // 2)))      # automatisch: 1-4 Signale 2x2, 5-6: 3+3, 7-8: 4+4 ...; sonst feste Zahl
            want = [None] * len(self.cells)
            used = set()
            for k in range(self.n):                               # von Hand gewaehlte zuerst
                if self.manual[k]:
                    want[k] = self.manual[k]
                    used.add(self.manual[k])
            for k in range(self.n):                               # laufende Zuordnung behalten, solange der Tuner noch ein Signal hat
                if want[k] is None and self.target[k] in cand and self.target[k] not in used:
                    want[k] = self.target[k]
                    used.add(self.target[k])
            rest = [t for t in cand if t not in used]
            for k in range(self.n):
                if want[k] is None and rest and not self.manual[k]:
                    want[k] = rest.pop(0)
            for k in range(self.n):
                if want[k] != self.target[k]:
                    self.target[k] = want[k]
                    self._play(k, want[k])
                c = self.cells[k]
                t = self.target[k]
                if t is None:
                    c["lbl"].configure(text="Kachel %d - frei" % (k + 1), bg="#1d2530", fg="#e8ebef")
                    c["lbl2"].configure(text="")
                    c["rate"].delete("all")
                else:
                    inf = self.get_info(t) or {}
                    name = inf.get("label", t)
                    kurz = name.replace("RTL-Dienst ", "")
                    head = inf["call"] if inf.get("call") else kurz        # Rufzeichen gross, Tunername klein dahinter
                    txt = "%s%s%s" % (("(%s)  " % kurz) if inf.get("call") else "", ("%.3f MHz" % inf["freq"]) if inf.get("freq") else "-", ("   %s" % inf["mer"]) if inf.get("mer") else "")
                    c["lbl"].configure(text=head, bg="#2d4f8f", fg="#ffe066")
                    c["lbl2"].configure(text=txt + ("   stumm" if self.muted[k] else "   \u266a %d%%" % self.vol[k]))
                    mm = re.search(r"(-?\d+(?:[.,]\d+)?)\s*dB", str(inf.get("mer") or ""))
                    c["mer"] = float(mm.group(1).replace(",", ".")) if mm else None
                    self._marq_text(k, t)
                    self._rate(k, t)
                    # Wiedergabe haengt (Stream weg/Fehler)? kurz neu starten
                    st = c["player"].get_state()
                    if st in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped) and c["mrl"]:
                        self._play(k, t)
        except Exception as e:                                    # noqa: BLE001
            self.log("Einfach: %r" % (e,))
        if not once:
            self.root.after(1500, self.tick)

    def close(self):
        self.alive = False
        if self.spec_frame is not None:
            self.spec_frame.destroy()
            self.spec_frame = self.spec_cv = None
        self.spec_on = False
        for c in self.cells:
            try:
                if c.get("mp") is not None:
                    c["mp"].stop()
                    c["mp"].release()
                c["player"].stop()
                c["player"].release()
            except Exception:                                     # noqa: BLE001
                pass
        try:
            self.inst.release()
        except Exception:                                         # noqa: BLE001
            pass
        self.frame.destroy()
