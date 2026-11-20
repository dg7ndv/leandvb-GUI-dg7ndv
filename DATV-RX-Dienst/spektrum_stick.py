#!/usr/bin/env python3
"""Startet den RTL-SDR-Spektrum-Server von HB9IIU (QO100-RASPI-RECEIVER, GPLv3, separat installieren) auf einem bestimmten Stick.

Der Server selbst oeffnet immer Stick 0 und kennt keine Auswahl. Dieses Skript (Teil des Dienstes, MIT) setzt vor dem Start den
gewuenschten Stick und den LNB-Oszillator und ruft dann dessen main() auf; am Server-Quelltext wird nichts veraendert.

  spektrum_stick.py --server ORDNER --device N|SERIENNUMMER [--lo 9540] [--korr-khz 0] [--port 7681]
Der Ordner enthaelt main_lo9540.py (sonst main.py) und app/. Benoetigt numpy, pyrtlsdr, websockets."""
import argparse, importlib, os, sys

ap = argparse.ArgumentParser()
ap.add_argument("--server", required=True)
ap.add_argument("--device", default="0")
ap.add_argument("--lo", type=float)
ap.add_argument("--korr-khz", type=float, default=0.0)
ap.add_argument("--port", type=int, default=7681)
a = ap.parse_args()

sys.path.insert(0, os.path.abspath(os.path.expanduser(a.server)))
import rtlsdr
_orig = rtlsdr.RtlSdr.__init__
dev = str(a.device).strip()


def _init(self, *args, **kw):
    if dev.isdigit():
        kw["device_index"] = int(dev)
    else:
        kw["serial_number"] = dev
    _orig(self, **kw)


rtlsdr.RtlSdr.__init__ = _init
name = "main_lo9540" if os.path.isfile(os.path.join(a.server, "main_lo9540.py")) else "main"
M = importlib.import_module(name)
if a.lo and hasattr(M, "LNB_LO_MHZ"):
    M.LNB_LO_MHZ = a.lo
sys.argv = [name, "--correction-khz", str(a.korr_khz), "--ws-port", str(a.port)]
raise SystemExit(M.main())
