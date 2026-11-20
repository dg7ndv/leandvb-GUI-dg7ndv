#!/usr/bin/env python3
"""fehlerbericht.py - Fehlerbericht fuer die QO-100-Programme (DG7NDV): bei einem Fehler wird ein Text erzeugt, der in die Zwischenablage kopiert,
als Datei gespeichert und spaeter z.B. per Mail verschickt werden kann.

Der Bericht enthaelt: Zeit, Programm und Version, Rechner/System/Python, die gesammelten Fehler (mit Traceback), die letzten Logzeilen und - falls uebergeben -
die Konfiguration (Passwoerter/Schluessel werden unkenntlich gemacht). Gemeinsamer Baustein ohne Abhaengigkeit (nur Standardbibliothek, Tkinter optional):

  fehlerbericht.add("Quelle", exc=ausnahme)               Fehler merken (Ringspeicher, 30 Eintraege)
  fehlerbericht.report("Programm", log_tail=[...], config={...})  -> Text
  fehlerbericht.install_hooks("Programm", on_error=fn)     unbehandelte Ausnahmen (Hauptprogramm und Threads) automatisch merken
  fehlerbericht.install_tk(root, "Programm", log_tail=fn, config=fn)   Tkinter: Fehler im Programm -> Dialog mit Kopieren/Speichern/Mail
  fehlerbericht.show_dialog_tk(root, "Programm", text)     Dialog (auch ohne Fehler aus einem Menue aufrufbar)
  fehlerbericht.save_file(text) -> Pfad                    ~/fehlerberichte/<Programm>_<Zeit>.txt
  fehlerbericht.mailto(betreff, kurztext, empfaenger="")   mailto:-Adresse (das Mailprogramm oeffnet sich; der volle Bericht liegt in der Zwischenablage)
"""
import collections, datetime, getpass, hashlib, os, platform, re, socket, sys, threading, time, traceback, urllib.parse, webbrowser

ERRORS = collections.deque(maxlen=30)
HERE = os.path.dirname(os.path.abspath(__file__))
_SECRET = re.compile(r"(?i)((?:passwort|password|passwd|secret|token|api[_-]?key|schluessel)\w*\s*[=:\"']+\s*)[^\s,\"';}]+")
_hooks = {"on_error": None, "programm": "?"}


def redact(text):
    """Passwoerter, Token und Schluessel unkenntlich machen"""
    return _SECRET.sub(lambda m: m.group(1) + "***", text)


def add(quelle, exc=None, text=None):
    """Fehler merken (Ringspeicher). exc: Ausnahme (Traceback wird mitgenommen) und/oder text."""
    tb = ""
    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    ERRORS.append({"zeit": time.strftime("%Y-%m-%d %H:%M:%S"), "quelle": quelle, "text": text or (str(exc) if exc is not None else ""), "traceback": tb})
    cb = _hooks.get("on_error")
    if cb:
        try:
            cb(ERRORS[-1])
        except Exception:
            pass


def clear():
    ERRORS.clear()


def version_of(*files):
    """Kurze Versionskennung: Hash ueber die angegebenen Programmdateien (Namen relativ zu diesem Ordner)"""
    h = hashlib.sha1()
    for f in files:
        try:
            h.update(open(f if os.path.isabs(f) else os.path.join(HERE, f), "rb").read())
        except OSError:
            pass
    return h.hexdigest()[:12]


def system_info():
    lines = ["Rechner:   %s (%s)" % (socket.gethostname(), getpass.getuser() if hasattr(getpass, "getuser") else "?"),
             "System:    %s %s, %s" % (platform.system(), platform.release(), platform.machine()),
             "Python:    %s" % platform.python_version()]
    try:
        m = open("/proc/device-tree/model", "rb").read().replace(b"\0", b"").decode(errors="replace")
        if m:
            lines.append("Geraet:    %s" % m)
    except OSError:
        pass
    try:
        for ln in open("/etc/os-release"):
            if ln.startswith("PRETTY_NAME="):
                lines.append("Betriebssystem: %s" % ln.split("=", 1)[1].strip().strip('"'))
    except OSError:
        pass
    return lines


def report(programm, version="", fehler=None, log_tail=None, config=None, extra=None, max_log=60):
    """Fehlerbericht als Text. fehler: Liste (Standard: gemerkte Fehler); log_tail: Liste/Funktion mit den letzten Logzeilen; config: dict/Funktion."""
    fehler = list(ERRORS) if fehler is None else fehler
    out = ["=== Fehlerbericht %s ===" % programm,
           "Erstellt:  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "Version:   %s" % (version or "-")] + system_info()
    for k, v in (extra or {}).items():
        out.append("%-10s %s" % (k + ":", v))
    out.append("")
    out.append("--- Fehler (%d) ---" % len(fehler))
    if not fehler:
        out.append("(keine Fehler gemerkt)")
    for e in fehler:
        out.append("[%s] %s: %s" % (e["zeit"], e["quelle"], e["text"]))
        if e.get("traceback"):
            out.append(e["traceback"].rstrip())
        out.append("")
    try:
        tail = log_tail() if callable(log_tail) else (log_tail or [])
    except Exception as ex:
        tail = ["(Log nicht lesbar: %r)" % (ex,)]
    if tail:
        out += ["--- letzte Logzeilen ---"] + [str(x) for x in list(tail)[-max_log:]] + [""]
    try:
        cfg = config() if callable(config) else config
    except Exception as ex:
        cfg = {"fehler": repr(ex)}
    if cfg:
        out.append("--- Konfiguration ---")
        for k in sorted(cfg):
            out.append("%s = %s" % (k, cfg[k]))
        out.append("")
    return redact("\n".join(out)).rstrip() + "\n"


def save_file(text, programm="fehler"):
    d = os.path.expanduser("~/fehlerberichte")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s_%s.txt" % (re.sub(r"\W+", "_", programm), time.strftime("%Y%m%d_%H%M%S")))
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def mailto(betreff, kurztext, empfaenger=""):
    """mailto:-Adresse; der Text bleibt kurz (viele Mailprogramme begrenzen die Laenge) - den ganzen Bericht fuegt man aus der Zwischenablage ein"""
    return "mailto:%s?%s" % (urllib.parse.quote(empfaenger), urllib.parse.urlencode({"subject": betreff, "body": kurztext}, quote_via=urllib.parse.quote))


def install_hooks(programm, on_error=None):
    """unbehandelte Ausnahmen im Hauptprogramm und in Threads automatisch merken (und an on_error melden)"""
    _hooks["on_error"], _hooks["programm"] = on_error, programm
    old_sys, old_thr = sys.excepthook, getattr(threading, "excepthook", None)

    def sys_hook(t, v, tb):
        if not issubclass(t, KeyboardInterrupt):
            v.__traceback__ = tb
            add("Hauptprogramm", exc=v)
        old_sys(t, v, tb)
    sys.excepthook = sys_hook
    if old_thr is not None:
        def thr_hook(args):
            if args.exc_type is not SystemExit:
                args.exc_value.__traceback__ = args.exc_traceback
                add("Thread %s" % (args.thread.name if args.thread else "?"), exc=args.exc_value)
            old_thr(args)
        threading.excepthook = thr_hook


# ---------------- Tkinter ----------------
def copy_tk(root, text):
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()                      # die Zwischenablage bleibt auch nach dem Schliessen des Dialogs erhalten (Zwischenablage-Manager)


def show_dialog_tk(root, programm, text, betreff=None, mail_to=""):
    """Dialog mit dem Bericht: In Zwischenablage kopieren (wird beim Oeffnen schon einmal automatisch gemacht), als Datei speichern, E-Mail-Entwurf"""
    import tkinter as tk
    from tkinter import ttk
    win = tk.Toplevel(root)
    win.title("Fehlerbericht – %s" % programm)
    frm = ttk.Frame(win, padding=8); frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Der Bericht ist schon in der Zwischenablage (Strg+V zum Einfügen, z. B. in eine Mail). Er enthält keine Passwörter.", wraplength=640).pack(anchor="w")
    box = tk.Text(frm, width=100, height=26, wrap="none"); sb = ttk.Scrollbar(frm, command=box.yview); box.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y"); box.pack(fill="both", expand=True, pady=6)
    box.insert("1.0", text)
    info = tk.StringVar()
    bf = ttk.Frame(win, padding=(8, 0, 8, 8)); bf.pack(fill="x")

    def do_copy():
        copy_tk(root, box.get("1.0", "end-1c")); info.set("In die Zwischenablage kopiert.")

    def do_save():
        info.set("Gespeichert: %s" % save_file(box.get("1.0", "end-1c"), programm))

    def do_mail():
        do_copy()
        webbrowser.open(mailto(betreff or "Fehlerbericht %s" % programm, "Fehlerbericht %s (%s)\n\nDer vollständige Bericht liegt in der Zwischenablage - bitte hier einfügen (Strg+V):\n\n" % (programm, time.strftime("%Y-%m-%d %H:%M")), mail_to))
        info.set("Mailprogramm geöffnet - Bericht mit Strg+V einfügen.")
    for t, c in (("In Zwischenablage kopieren", do_copy), ("Als Datei speichern", do_save), ("E-Mail-Entwurf öffnen", do_mail), ("Schließen", win.destroy)):
        ttk.Button(bf, text=t, command=c).pack(side="left", padx=3)
    ttk.Label(bf, textvariable=info, foreground="#1a7a3a").pack(side="left", padx=8)
    try:
        copy_tk(root, text)
    except Exception:
        pass
    return win


def install_tk(root, programm, version="", log_tail=None, config=None, extra=None, dialog=True):
    """Tkinter-Programme: Fehler in Callbacks und Threads werden gemerkt; mit dialog=True oeffnet sich der Berichtsdialog (hoechstens einmal je 30 s)"""
    last = [0.0]

    def make():
        return report(programm, version=version, log_tail=log_tail, config=config, extra=extra)

    def show():
        if dialog and time.time() - last[0] > 30:
            last[0] = time.time()
            try:
                show_dialog_tk(root, programm, make())
            except Exception:
                pass

    def on_err(_e):
        try:
            root.after(0, show)
        except Exception:
            pass

    def cb_exc(t, v, tb):
        v.__traceback__ = tb
        add("Programm (Tk)", exc=v)
        sys.stderr.write("".join(traceback.format_exception(t, v, tb)))
        show()
    install_hooks(programm, on_error=on_err)
    root.report_callback_exception = cb_exc
    return make
