// Test ohne GNOME-Shell:  gjs -m test_ws.js [Sekunden]
import GLib from 'gi://GLib';
import Soup from 'gi://Soup';
import {SpectrumTracker} from './peaks.js';
const secs = parseInt(ARGV[0] ?? '25');
const tr = new SpectrumTracker();
const log = (k, s) => print(`${new Date().toTimeString().slice(0, 8)} ${k} ${s.freq.toFixed(3)} MHz ${s.sr} kS (BW ${s.bw})`);
tr.onNew = s => log('NEU ', s); tr.onGone = s => log('WEG ', s); tr.onBack = s => log('WIEDER', s);
const loop = new GLib.MainLoop(null, false);
const session = new Soup.Session();
const msg = Soup.Message.new('GET', 'wss://eshail.batc.org.uk/wb/fft');
let frames = 0;
session.websocket_connect_async(msg, null, ['fft_m0dtslivetune'], GLib.PRIORITY_DEFAULT, null, (s, res) => {
    const conn = s.websocket_connect_finish(res);
    print('verbunden');
    conn.connect('message', (c, type, bytes) => {
        if (type !== Soup.WebsocketDataType.BINARY) return;
        const d = bytes.toArray();
        tr.feed(new Uint16Array(d.slice().buffer, 0, d.length >> 1));
        frames++;
    });
});
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => { tr.tick(); return GLib.SOURCE_CONTINUE; });
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, secs, () => {
    print(`Bilder: ${frames}, Bins: ${tr.n}, Boden ${tr.floor.toFixed(1)} dB, Bake: ${tr.beaconOn()} (vor ${(Date.now()/1000 - tr.beaconT).toFixed(1)} s)`);
    for (const s of tr.signals()) print(`  aktiv: ${s.freq.toFixed(3)} MHz ${s.sr} kS BW ${s.bw}${s.goneSince ? ' (weg)' : ''}`);
    print('Peaks im letzten Bild: ' + tr.segments.map(g => `${g.f.toFixed(3)}${g.beacon ? '(Bake)' : ''}`).join(' '));
    loop.quit(); return GLib.SOURCE_REMOVE;
});
loop.run();
