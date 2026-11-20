#!/usr/bin/env python3
# Erzeugt bilder/pi_sticks.svg (EIN Raspberry Pi 5 mit 1-4 Sticks, bis 10 moeglich) und bilder/mehrere_pis.svg (ZWEI Pis, MQTT-Broker, MultiPlayer-Client)
import html, os
HERE = os.path.dirname(os.path.abspath(__file__))
COL = ["#f08a00", "#2a7be4", "#8e44ad", "#16a085", "#c0392b", "#6b7b8c", "#b8860b", "#2c3e50", "#d35400", "#7f8c8d"]
def e(s): return html.escape(s, quote=False)

class Svg:
    def __init__(s, w, h, title):
        s.o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="100%%" font-family="DejaVu Sans, Arial, sans-serif" role="img" aria-label="%s">' % (w, h, e(title)),
               '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#46556a"/></marker></defs>',
               '<rect x="0" y="0" width="%d" height="%d" rx="12" fill="#f7f9fb" stroke="#d3d9e0" stroke-width="1.5"/>' % (w, h)]
    def rect(s, x, y, w, h, fill="#fff", stroke="#7a8796", r=8, sw=1.5):
        s.o.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" stroke="%s" stroke-width="%g"/>' % (x, y, w, h, r, fill, stroke, sw))
    def text(s, x, y, t, size=13, fill="#1b1f24", weight="normal", anchor="start"):
        s.o.append('<text x="%g" y="%g" font-size="%g" fill="%s" font-weight="%s" text-anchor="%s">%s</text>' % (x, y, size, fill, weight, anchor, e(t)))
    def line(s, x1, y1, x2, y2, col="#7a8796", w=1.5, arrow=False, dash=""):
        s.o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g" %s %s/>' % (x1, y1, x2, y2, col, w, 'marker-end="url(#ah)"' if arrow else "", ('stroke-dasharray="%s"' % dash) if dash else ""))
    def save(s, name):
        open(os.path.join(HERE, name), "w", encoding="utf-8").write("\n".join(s.o + ["</svg>"]) + "\n")

def pi_box(g, x, y, w, name, host, n, base, extra=""):
    """Raspberry Pi mit n Sticks (RTL1..), UDP ab Basisadresse base (z.B. 30 -> 230.0.0.30)"""
    rows = (n + 1) // 2 if n > 1 else 1
    h = 150 + rows * 56 + 60
    g.rect(x, y, w, h, "#eaf1f8", "#46556a", 12, 2)
    g.text(x + 14, y + 24, "Raspberry Pi 5 – %s" % host, 14, "#1f5fa8", "bold")
    g.text(x + 14, y + 42, "Dienst datvrx-dienst (%d Stick%s)" % (n, "" if n == 1 else "s"), 11, "#5b6573")
    g.rect(x + 14, y + 52, w - 28, 28, "#fff7e0", "#c9a227"); g.text(x + w / 2, y + 71, "LNB → Splitter → je Stick ein Koax", 11, anchor="middle")
    cw = (w - 28 - 10) / 2 if n > 1 else w - 28
    for i in range(n):
        cx = x + 14 + (i % 2) * (cw + 10) if n > 1 else x + 14
        cy = y + 96 + (i // 2) * 56
        g.rect(cx, cy, cw, 48, COL[i % len(COL)], "#222", 8)
        g.text(cx + 10, cy + 20, "RTL%d" % (i + 1), 15, "#fff", "bold")
        g.text(cx + 10, cy + 38, "UDP 230.0.0.%d:1234" % (base + i), 10.5, "#fff")
    ey = y + 96 + rows * 56
    g.rect(x + 14, ey, w - 28, 30, "#dfe6ee", "#46556a"); g.text(x + w / 2, ey + 20, "aktiver USB-3-Hub (eigene 5 V)" if n > 1 else "USB direkt oder Hub", 11, anchor="middle")
    g.text(x + w / 2, ey + 52, extra, 10.5, "#5b6573", anchor="middle")
    return h

# --- Bild 1: ein Pi, 1 bis 4 (bis 10) Sticks
g = Svg(1500, 470, "Ein Raspberry Pi 5 mit 1 bis 4 (bis 10) RTL-SDR-Sticks")
g.text(20, 30, "Ein Raspberry Pi 5 – 1 bis 4 RTL-SDR-Sticks (bis 10 mit Hub und genug Rechenleistung)", 18, weight="bold")
x = 20
for n, lab in ((1, "1 Stick: eine Frequenz"), (2, "2 Sticks: Bake + Signal"), (4, "4 Sticks: Standard")):
    w = 330 if n > 1 else 250
    h = pi_box(g, x, 50, w, "", "Pi %d" % n if False else "datv-rx", n, 30, lab)
    x += w + 24
g.rect(x, 50, 1500 - x - 20, 330, "#fff", "#1f7a3a", 12, 2)
g.text(x + 14, 76, "Dienst auf dem Pi", 14, "#1f7a3a", "bold")
for i, t in enumerate(("• sucht & dekodiert alle Signale", "• Rufzeichen, MER, Pegel, Bitrate", "• Autoverteiler: Signal → freier Stick", "• Web-Interface :8554, JSON-API, MQTT", "• je Stick ein UDP-Multicast-Strom", "• Clients: MultiPlayer, leandvb-GUI, VLC")):
    g.text(x + 14, 104 + i * 24, t, 12)
g.text(20, 440, "Je weiterer Stick eine eigene UDP-Adresse (Basisadresse + Stick-Nr.). Mit \"Pi 5\" ist Echtzeit-Empfang gemessen (Faktor 0,29 bei der Bake), Pi 4 ist zu langsam.", 12, "#5b6573")
g.save("pi_sticks.svg")

# --- Bild 2: zwei Pis, MQTT, Client
g = Svg(1500, 770, "Zwei Raspberry Pi 5 mit Dienst, MQTT-Broker und MultiPlayer-Client")
g.text(20, 30, "Mehrere Pis: jeder Pi meldet seine UDP-Basisadresse per MQTT – der Client findet alle", 18, weight="bold")
pi_box(g, 20, 60, 340, "", "datv-rx (.175)", 4, 40, "UDP-Basis 230.0.0.40 (frei gewaehlt)")
pi_box(g, 20, 410, 340, "", "P5NDI1 (.102)", 2, 30, "UDP-Basis 230.0.0.30")
g.rect(560, 60, 360, 150, "#fff", "#7a4fb0", 12, 2)
g.text(740, 88, "MQTT-Broker (optional)", 15, "#7a4fb0", "bold", "middle")
for i, t in enumerate(("qo100/udp/datv-rx = {basis 230.0.0.40, ip, http}", "qo100/udp/P5NDI1 = {basis 230.0.0.30, ip, http}", "Neuer Pi fragt qo100/udp/+ und nimmt", "die naechste freie Basis (30, 40, 50 …)")):
    g.text(576, 116 + i * 22, t, 11.5)
g.line(360, 150, 560, 130, "#7a4fb0", 2, True, "6 4"); g.line(360, 480, 560, 190, "#7a4fb0", 2, True, "6 4")
g.rect(1060, 60, 420, 330, "#fff", "#1f5fa8", 12, 2)
g.text(1270, 90, "PC: MultiPlayer / leandvb-GUI", 15, "#1f5fa8", "bold", "middle")
g.text(1076, 122, "Menue \"Pis\":", 12.5, weight="bold")
for i, t in enumerate(("☑ datv-rx (192.168.0.175:8554)", "☑ P5NDI1 (192.168.0.102:8554)  [Steuer-Pi]", "Pi von Hand hinzufuegen …", "MQTT-Broker: 192.168.0.10 …", "Jetzt per MQTT suchen")):
    g.text(1090, 148 + i * 22, t, 12)
g.text(1076, 278, "Ein Player je Stick:  RTL1@datv-rx … RTL4@datv-rx,", 12); g.text(1076, 298, "RTL1@P5NDI1, RTL2@P5NDI1", 12)
g.text(1076, 330, "Ohne MQTT: Pi-Liste in ~/.multiplayer.json", 11.5, "#5b6573"); g.text(1076, 350, "oder --also HOST[:PORT] / Handeingabe", 11.5, "#5b6573")
g.line(360, 250, 1060, 250, "#c0392b", 2.5, True); g.text(700, 242, "UDP-Multicast-Streams (Video)  230.0.0.40–43 und .30–31", 12, "#c0392b", anchor="middle")
g.line(360, 550, 1060, 330, "#c0392b", 2.5, True)
g.line(360, 330, 1060, 280, "#46556a", 1.5, True, "3 3"); g.text(760, 322, "HTTP :8554  Status, Steuerung", 11.5, "#46556a", anchor="middle")
g.save("mehrere_pis.svg")
print("pi_sticks.svg, mehrere_pis.svg")
