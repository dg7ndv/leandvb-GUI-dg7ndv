#!/usr/bin/env python3
# Erzeugt bilder/ueberblick.svg (Raspberry Pi 5 mit 6 RTL-SDR-Sticks, Web-Interface, MultiPlayer-Client)
import html
o = []
def e(s): return html.escape(s, quote=False)
def rect(x, y, w, h, fill="#fff", stroke="#7a8796", r=8, sw=1.5, extra=""):
    o.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" stroke="%s" stroke-width="%g" %s/>' % (x, y, w, h, r, fill, stroke, sw, extra))
def text(x, y, s, size=13, fill="#1b1f24", weight="normal", anchor="start"):
    o.append('<text x="%g" y="%g" font-size="%g" fill="%s" font-weight="%s" text-anchor="%s">%s</text>' % (x, y, size, fill, weight, anchor, e(s)))
def line(x1, y1, x2, y2, col="#7a8796", w=1.5, arrow=False, dash=""):
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g" %s %s/>' % (x1, y1, x2, y2, col, w, 'marker-end="url(#ah)"' if arrow else "", ('stroke-dasharray="%s"' % dash) if dash else ""))
def pill(x, y, w, s, fill="#fff", fg="#1b1f24", h=20, size=11):
    rect(x, y, w, h, fill, "#9aa5b1", h / 2, 1)
    text(x + w / 2, y + h / 2 + 4, s, size, fg, anchor="middle")

W, H = 1700, 900
o.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="100%%" font-family="DejaVu Sans, Arial, sans-serif" role="img" aria-label="Raspberry Pi 5 mit 6 RTL-SDR-Sticks, Web-Interface und MultiPlayer-Client">' % (W, H))
o.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#46556a"/></marker></defs>')
rect(0, 0, W, H, "#f7f9fb", "#d3d9e0", 12)
text(20, 32, "QO 100 Multi-Zapper: Raspberry Pi 5 mit 6 RTL-SDR-Sticks → Web-Interface → Client (MultiPlayer)", 19, weight="bold")

# --- Spalte 1: Raspberry Pi
text(20, 62, "1  Raspberry Pi 5 (ohne Bildschirm, Dienst datvrx-dienst)", 15, "#1f5fa8", "bold")
rect(20, 72, 470, 810, "#eaf1f8", "#46556a", 12, 2)
rect(40, 88, 430, 58, "#fff7e0", "#c9a227"); text(255, 111, "Antenne → LNB → Splitter (6 Ausgänge)", 14, weight="bold", anchor="middle")
text(255, 132, "QO-100 10,49 GHz, Bake 10491,5 MHz, Signale bis 10500 MHz", 11, "#5b6573", anchor="middle")
line(145, 146, 145, 160, "#c9a227", 1.5); line(365, 146, 365, 160, "#c9a227", 1.5); text(255, 170, "Koax je Stick", 10, "#8a6d00", anchor="middle")
cols = ["#f08a00", "#2a7be4", "#8e44ad", "#16a085", "#c0392b", "#6b7b8c"]
for i, c in enumerate(cols):
    x = 40 + (i % 2) * 220; y = 180 + (i // 2) * 84
    if i < 2: line(x + 105, 160, x + 105, y, "#c9a227", 1.5)
    rect(x, y, 210, 66, c, "#222", 8, 1.5)
    text(x + 12, y + 26, "RTL%d" % (i + 1), 17, "#fff", "bold")
    text(x + 12, y + 46, "Stick dg7ndv%02d" % (i + 1), 11, "#fff")
    text(x + 12, y + 60, "UDP 230.0.0.%d:1234" % (30 + i), 11, "#fff")
rect(40, 434, 430, 40, "#dfe6ee", "#46556a"); text(255, 459, "aktiver USB-3-Hub (eigene 5-V-Versorgung) am Pi", 13, anchor="middle")
for x in (140, 360): line(x, 428, x, 434, "#46556a", 2)
rect(40, 494, 430, 214, "#fff", "#1f7a3a", 10, 2)
text(255, 518, "Dienst datvrx_dienst.py + zapper.py", 14, "#1f7a3a", "bold", "middle")
for k, s in enumerate(["• BATC-Spektrum liest Signale (Internet)", "• Auto-Scan: Rufzeichen, MER, Pegel (datvrx)", "• Autoverteiler: neues Signal → freier Stick",
                       "• Bake-Offset je Stick (Cache 7 Tage)", "• OnlyBake=>RTL1, Display-Steuerung", "• Verteil-Protokoll, Fehlerbericht", "• JSON-API und MQTT (optional)"]):
    text(56, 542 + k * 22, s, 12.5)
rect(40, 724, 430, 56, "#fff", "#7a8796"); text(255, 746, "HDMI-A-1 / HDMI-A-2 → Monitor", 13, weight="bold", anchor="middle")
text(255, 766, "VLC im Vollbild (RTL1 oder Signal), Rufzeichen-Einblendung", 11, "#5b6573", anchor="middle")
rect(40, 796, 430, 70, "#fff", "#7a8796"); text(255, 820, "Netzwerk des Pi (LAN)", 13, weight="bold", anchor="middle")
text(255, 840, "HTTP :8554 Web + API   ·   HTTP :8555 Handy-Seite", 11.5, anchor="middle")
text(255, 858, "UDP-Multicast 230.0.0.30 … 230.0.0.35 : 1234", 11.5, anchor="middle")

# --- Spalte 2: Web-Interface
text(530, 62, "2  Web-Interface auf dem Pi (Browser am PC oder Handy)", 15, "#1f5fa8", "bold")
rect(530, 72, 550, 346, "#fff", "#46556a", 10, 2)
text(546, 100, "Hauptmaske  http://<Pi>:8554/", 11, "#5b6573")
text(546, 124, "QO 100 Multi-Zapper – DG7NDV", 16, weight="bold")
for k, s in enumerate(["?", "⚙B", "⚙D", "⚙"]): pill(930 + k * 36, 108, 32, s, "#fff", h=22)
pill(546, 138, 140, "Bake neu referenzieren"); pill(692, 138, 140, "RTL1 beim Start: Bake"); pill(838, 138, 90, "Alle scannen"); pill(934, 138, 80, "Auto-Scan")
pill(546, 164, 100, "Autoverteiler: AN", "#1f7a3a", "#fff"); pill(652, 164, 100, "OnlyBake=>RTL1"); text(764, 179, "4 Sticks  ☑RTL1 ☑RTL2 ☑RTL3 ☑RTL4 ☑RTL5 ☑RTL6", 11)
rect(546, 192, 518, 20, "#fff", "#d3d9e0", 4); text(554, 206, "RTL1: 10497.745 MHz 333K (fest)  |  RTL2: 10498.753 MHz  |  RTL3: frei", 11)
for j, h in enumerate(["Stick", "MER", "Signal", "Rufzeichen", "Provider", "Modul.", "Es/N0", "Pegel"]):
    text(550 + [0, 120, 160, 230, 300, 385, 440, 480][j], 232, h, 10.5, "#5b6573", "bold")
for r, (a, b, cc) in enumerate([("A71A", "Bake", "#fff"), ("CU3AT", "QPSK 2/5", "#fff"), ("DK0PU", "QPSK 3/4", "#e4f3e8")]):
    y = 238 + r * 30
    rect(546, y, 518, 28, cc, "#d3d9e0", 3, 1)
    for k in range(4): pill(550 + k * 28, y + 7, 26, "R%d" % (k + 1), "#1f7a3a" if (r == 2 and k == 1) else "#fff", "#fff" if (r == 2 and k == 1) else "#1b1f24", 15, 8)
    text(670, y + 18, "7.8", 11); text(710, y + 18, "10497.7", 11); text(780, y + 18, a, 11, weight="bold"); text(850, y + 18, "ESP32-DATV", 11); text(935, y + 18, b, 11)
rect(546, 336, 518, 36, "#f4f6f8", "#d3d9e0", 6, 1)
text(556, 353, "CPU gesamt", 10.5); rect(630, 346, 300, 8, "#d3d9e0", "none", 4, 0); rect(630, 346, 90, 8, "#2e8b4a", "none", 4, 0); text(940, 354, "17 %  min/max", 10.5)
text(556, 367, "Dienst / Temperatur / Last", 10.5, "#5b6573")
pill(546, 380, 72, "VLC·RTL1", "#f08a00", "#fff", 20, 9); pill(624, 380, 72, "VLC·RTL2", "#2a7be4", "#fff", 20, 9)
text(706, 394, "Player (UDP) · Playlist je Stick · BATC-Spektrum", 10.5)
# Handy
rect(530, 436, 150, 330, "#222", "#222", 20, 2); rect(540, 458, 130, 286, "#fff", "none", 6, 0)
text(605, 478, "Handy-Seite :8555", 10.5, anchor="middle", weight="bold")
for k in range(3):
    c = cols[k]; rect(550, 490 + k * 44, 110, 34, c, "none", 17, 0); text(605, 512 + k * 44, "▶ VLC · RTL%d" % (k + 1), 11, "#fff", "bold", "middle")
text(605, 640, "Signalliste mit", 10.5, anchor="middle"); text(605, 654, "Rufzeichen, MER", 10.5, anchor="middle"); text(605, 668, "Klick = VLC starten", 10.5, anchor="middle")
text(605, 726, "Link zur Hauptseite", 10, "#1a5fb4", anchor="middle")
# Menues
menus = [("⚙  Einstellungen", "/config", "LO, Sticks, Verteilen, MQTT"), ("⚙D  Displays", "/display", "HDMI, Player, Einblendung"),
         ("⚙B  Bake", "/bake", "Offset, Rückfall, Start-Haken"), ("?  Hilfe / Anleitung", "/hilfe, /anleitung", "Kurzhilfe und diese Anleitung"),
         ("📝  Protokoll", "/protokoll", "NEU, ZUWEISUNG, OFFEN"), ("⚠  Fehlerbericht", "/fehler", "Zwischenablage, Datei, Mail"),
         ("Manuelle Frequenz", "/manuell", "beliebige QRG auf einen Stick"), ("Status / API", "/status, /api/v1/…", "JSON, Playlist /qo100.m3u")]
for k, (t1, t2, t3) in enumerate(menus):
    x = 696 + (k % 2) * 194; y = 436 + (k // 2) * 83
    rect(x, y, 186, 76, "#fff", "#7a8796", 8, 1.5)
    text(x + 10, y + 22, t1, 12.5, weight="bold"); text(x + 10, y + 40, t2, 11, "#1a5fb4"); text(x + 10, y + 58, t3, 10, "#5b6573")

# --- Spalte 3: Client
text(1130, 62, "3  Client (PC / Notebook / Handy)", 15, "#1f5fa8", "bold")
rect(1130, 72, 560, 410, "#fff", "#46556a", 10, 2)
text(1146, 98, "MultiPlayer  (multiplayer.py, Tk)", 16, weight="bold")
for k, s in enumerate(["Start", "ShowAll", "MuteAll", "Quad", "Fehlerbericht"]): pill(1146 + k * 82, 110, 76, s, "#fff", h=22)
for k in range(6):
    x = 1146 + (k % 3) * 178; y = 148 + (k // 3) * 150
    rect(x, y, 168, 138, "#14171a", cols[k], 6, 3)
    rect(x + 8, y + 8, 152, 82, "#22313f", "none", 3, 0); text(x + 84, y + 54, "Videobild", 12, "#9aa5b1", anchor="middle")
    text(x + 10, y + 108, "VLC RTL%d" % (k + 1), 12, "#fff", "bold"); text(x + 10, y + 126, "udp://@230.0.0.%d:1234" % (30 + k), 10, "#9aa5b1")
text(1146, 462, "ein Player je Stick, Neustart bei Signalwechsel; Zeile grau = Stick hängt am Pi-Monitor", 11, "#5b6573")
rect(1130, 498, 560, 120, "#fff", "#46556a", 10, 2)
text(1146, 524, "leandvb-GUI (Tuner Switcher)", 15, weight="bold")
text(1146, 546, "Pi-Sticks als datvRX-Tunerspalten, Profile, Prioritäten, LO-Abgleich", 12)
text(1146, 566, "schaltet die Verteilfunktion des Pi aus (API /api/v1/verteiler)", 12)
text(1146, 586, "steuert die Sticks über dienst_client.py (HTTP :8554)", 12)
rect(1130, 634, 560, 100, "#fff", "#46556a", 10, 2)
text(1146, 660, "Handy / Tablet / anderer PC", 15, weight="bold")
text(1146, 682, "VLC mit Playlist http://<Pi>:8554/qo100.m3u (Taste «nächstes»)", 12)
text(1146, 702, "oder Handy-Seite :8555, direkter Stream http://<Pi>:8554/z/<MHz>/<kS>", 12)
text(1146, 722, "Browser am PC: Hauptmaske, Protokoll, Einstellungen", 12)

# Pfeile
line(490, 600, 530, 330, "#46556a", 2.5, True); text(500, 470, "", 10)
line(1080, 250, 1130, 250, "#46556a", 2.5, True); text(1105, 285, "HTTP", 10, "#46556a", "bold", "middle")
line(1080, 580, 1130, 560, "#46556a", 2.5, True)
line(1080, 690, 1130, 690, "#46556a", 2.5, True)
line(490, 830, 1100, 830, "#d9534f", 3.5, True)
line(1100, 830, 1100, 480, "#d9534f", 3.5, True); line(1100, 480, 1130, 480, "#d9534f", 3.5, True)
text(500, 818, "UDP-Multicast: ein Transportstrom je Stick (230.0.0.30 … 35 : 1234)", 13, "#c0392b", "bold")
text(500, 856, "rot = Videostrom (UDP) · grau = Steuerung und Anzeige (HTTP/JSON, Ports 8554 / 8555)", 12, "#5b6573")
o.append("</svg>")
open("bilder/ueberblick.svg", "w", encoding="utf-8").write("\n".join(o))
print("ok", len("\n".join(o)))
