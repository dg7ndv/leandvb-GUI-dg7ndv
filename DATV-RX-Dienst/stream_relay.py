#!/usr/bin/env python3
"""stream_relay.py - Multicast-Transportstrom als HTTP-Stream verteilen (z.B. fuers Handy mit VLC) (DG7NDV)

Aufruf:  ./stream_relay.py [--mcast 230.0.0.20] [--port 1234] [--http 8554] [--iface 192.168.0.204]
Handy (VLC, Netzwerkstream oeffnen):  http://<IP-dieses-Rechners>:8554/
Playlist zum Herunterladen/Oeffnen:   http://<IP-dieses-Rechners>:8554/qo100.m3u   (Adresse passt sich der Anfrage an)
Mehrere Zuschauer gleichzeitig moeglich. Ist kein Signal im Multicast, bleibt die Verbindung offen und wartet.
"""
import argparse, queue, socket, struct, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--mcast", default="230.0.0.20")
ap.add_argument("--port", type=int, default=1234)
ap.add_argument("--http", type=int, default=8554)
ap.add_argument("--iface", default="0.0.0.0", help="Netzwerkkarte fuer den Multicast-Empfang (IP)")
a = ap.parse_args()

clients, lock = set(), threading.Lock()


def receiver():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", a.port))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(a.mcast) + socket.inet_aton(a.iface))
    while True:
        data = s.recv(65536)
        with lock:
            for q in list(clients):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass                      # langsamer Zuschauer: Pakete verwerfen


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0].lower().endswith(".m3u"):            # Playlist fuer VLC
            host = self.headers.get("Host") or "%s:%d" % (socket.gethostbyname(socket.gethostname()), a.http)
            body = ("#EXTM3U\n#EXTINF:-1,QO-100 DATV (%s:%d)\nhttp://%s/\n" % (a.mcast, a.port, host)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-mpegurl")
            self.send_header("Content-Disposition", 'attachment; filename="qo100.m3u"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = queue.Queue(maxsize=3000)
        with lock:
            clients.add(q)
        try:
            while True:
                try:
                    self.wfile.write(q.get(timeout=5)); self.wfile.flush()
                except queue.Empty:
                    pass                      # kein Signal: offen halten
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with lock:
                clients.discard(q)

    def log_message(self, *x):
        pass


threading.Thread(target=receiver, daemon=True).start()
print("Relay: udp://%s:%d -> http://0.0.0.0:%d/" % (a.mcast, a.port, a.http), flush=True)
ThreadingHTTPServer(("0.0.0.0", a.http), H).serve_forever()
