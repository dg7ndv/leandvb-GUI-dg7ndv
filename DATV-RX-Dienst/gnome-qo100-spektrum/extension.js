// QO-100 Spektrum - GNOME-Shell-Erweiterung (DG7NDV)
// Liest das BATC-Wideband-Spektrum und zeigt nur die Peaks (ohne Rauschen) in der oberen Leiste.
import GObject from 'gi://GObject';
import St from 'gi://St';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup';
import Clutter from 'gi://Clutter';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {SpectrumTracker, START, BW_MHZ, BEACON_NOM} from './peaks.js';

const URL = 'wss://eshail.batc.org.uk/wb/fft';
const MAX_DB = 15;          // Hoehe der Skala (dB ueber Rauschboden), wie die BATC-Seite

const Indicator = GObject.registerClass(
class Qo100Indicator extends PanelMenu.Button {
    _init(tracker) {
        super._init(0.5, 'QO-100 Spektrum');
        this._t = tracker;
        this._small = new St.DrawingArea({width: 200, height: 22, y_align: Clutter.ActorAlign.CENTER});
        this._small.connect('repaint', a => this._paint(a, false));
        this.add_child(this._small);

        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._big = new St.DrawingArea({width: 620, height: 190});
        this._big.connect('repaint', a => this._paint(a, true));
        item.add_child(this._big);
        this.menu.addMenuItem(item);
        this._list = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._list);
        this.rebuildList();
    }

    redraw() {
        this._small.queue_repaint();
        if (this.menu.isOpen) this._big.queue_repaint();
    }

    setOffline(off) {
        if (off) this.add_style_class_name('qo100-off'); else this.remove_style_class_name('qo100-off');
    }

    flash(sec = 8) {
        this.add_style_class_name('qo100-new');
        if (this._flashId) GLib.source_remove(this._flashId);
        this._flashId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, sec, () => {
            this.remove_style_class_name('qo100-new'); this._flashId = 0; return GLib.SOURCE_REMOVE;
        });
    }

    rebuildList() {
        this._list.removeAll();
        const head = new PopupMenu.PopupMenuItem(
            `Bake ${this._t.beaconOn() ? 'da' : 'nicht gesehen'}  ·  ${this._t.signals().filter(s => s.goneSince === null).length} Signale aktiv`,
            {reactive: false});
        this._list.addMenuItem(head);
        for (const s of this._t.signals()) {
            const dauer = Math.round(Date.now() / 1000 - s.born);
            const txt = `${s.freq.toFixed(3)} MHz   ${String(s.sr).padStart(4)} kS   ${s.goneSince !== null ? 'weg' : dauer + ' s'}`;
            const it = new PopupMenu.PopupMenuItem(txt, {reactive: false});
            it.label.add_style_class_name('qo100-list');
            this._list.addMenuItem(it);
        }
    }

    _paint(area, big) {
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        const t = this._t;
        const padB = big ? 18 : 2, padT = big ? 16 : 2;
        const base = h - padB, span = base - padT;
        const X = f => ((f - START) / BW_MHZ) * w;
        if (big) {                                   // Raster und Frequenzachse
            cr.setLineWidth(1);
            cr.selectFontFace('sans', 0, 0); cr.setFontSize(10);
            for (let f = 10491; f <= 10499; f++) {
                cr.setSourceRGBA(0.5, 0.5, 0.5, 0.25);
                cr.moveTo(X(f) + 0.5, padT); cr.lineTo(X(f) + 0.5, base); cr.stroke();
                cr.setSourceRGBA(0.8, 0.8, 0.8, 0.9);
                cr.moveTo(X(f) - 14, h - 4); cr.showText((f / 1000).toFixed(3)); cr.stroke();
            }
            for (let d = 5; d <= MAX_DB; d += 5) {
                const y = base - span * d / MAX_DB;
                cr.setSourceRGBA(0.5, 0.5, 0.5, 0.25);
                cr.moveTo(0, y + 0.5); cr.lineTo(w, y + 0.5); cr.stroke();
                cr.setSourceRGBA(0.8, 0.8, 0.8, 0.9);
                cr.moveTo(2, y - 2); cr.showText(d + ' dB'); cr.stroke();
            }
        }
        cr.setSourceRGBA(0.6, 0.6, 0.6, big ? 0.6 : 0.35);          // Grundlinie
        cr.moveTo(0, base + 0.5); cr.lineTo(w, base + 0.5); cr.stroke();

        const sm = t.smooth, mk = t.mask, n = t.n;
        if (sm && mk && n) {
            const hh = i => Math.min(MAX_DB, Math.max(0, sm[i] - t.floor)) * mk[i];   // Rauschen: Maske 0 -> keine Hoehe
            let run = false;
            const bake = i => Math.abs(START + ((i + 0.5) / n) * BW_MHZ - BEACON_NOM) < 0.8;
            for (let pass = 0; pass < 2; pass++) {                    // erst Peaks, dann die Bake in anderer Farbe
                if (pass === 0) cr.setSourceRGBA(0.2, 0.75, 0.25, 0.95); else cr.setSourceRGBA(0.3, 0.55, 0.95, 0.95);
                let start = -1;
                for (let i = 0; i <= n; i++) {
                    const on = i < n && hh(i) > 0.4 && (bake(i) === (pass === 1));
                    if (on && start < 0) { start = i; cr.moveTo(X(START + (i / n) * BW_MHZ), base); }
                    if (on) cr.lineTo(X(START + ((i + 0.5) / n) * BW_MHZ), base - span * hh(i) / MAX_DB);
                    if (!on && start >= 0) { cr.lineTo(X(START + ((i - 0.5) / n) * BW_MHZ), base); cr.closePath(); cr.fill(); start = -1; }
                }
            }
            if (big) {                                                   // Beschriftung der Peaks
                cr.setSourceRGBA(1, 1, 1, 0.95); cr.selectFontFace('sans', 0, 1); cr.setFontSize(10);
                for (const s of t.signals()) {
                    if (s.goneSince !== null) continue;
                    const x = X(s.freq), tx = Math.min(w - 70, Math.max(2, x - 30));
                    cr.moveTo(tx, padT + 8); cr.showText(`${s.sr}K '${s.freq.toFixed(3).slice(3)}`); cr.stroke();
                }
            }
        }
        cr.$dispose();
    }
});

export default class Qo100Extension extends Extension {
    enable() {
        this._tracker = new SpectrumTracker();
        this._ind = new Indicator(this._tracker);
        Main.panel.addToStatusArea(this.uuid, this._ind, 0, 'right');
        this._tracker.onNew = s => {
            Main.notify('QO-100 · New QRG', `${s.sr}K  ${s.freq.toFixed(3)} MHz`);
            this._ind.flash();
        };
        this._cancel = new Gio.Cancellable();
        this._session = new Soup.Session();
        this._lastFrame = 0; this._lastDraw = 0;
        this._connect();
        this._tickId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            const changed = this._tracker.tick();
            this._n = (this._n ?? 0) + 1;
            if (changed || this._ind.menu.isOpen) this._ind.rebuildList();
            // Zeichnen: sofort bei Aenderung (neues/verschwundenes Signal), bei offenem Menue 1x/s, sonst alle 10 s
            if (changed || this._ind.menu.isOpen || this._n % 10 === 0) this._ind.redraw();
            this._ind.setOffline(Date.now() / 1000 - this._lastFrame > 15);
            if (Date.now() / 1000 - this._lastFrame > 30 && this._conn === null && !this._retryId) this._retry();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _connect() {
        const msg = Soup.Message.new('GET', URL);
        this._conn = null;
        this._session.websocket_connect_async(msg, null, ['fft_m0dtslivetune'], GLib.PRIORITY_DEFAULT, this._cancel, (s, res) => {
            let conn;
            try { conn = s.websocket_connect_finish(res); } catch (e) { this._retry(); return; }
            this._conn = conn;
            conn.set_keepalive_interval(15);
            conn.connect('message', (c, type, bytes) => {
                if (type !== Soup.WebsocketDataType.BINARY) return;
                const d = bytes.toArray();
                this._tracker.feed(new Uint16Array(d.slice().buffer, 0, d.length >> 1));
                const now = Date.now() / 1000;
                this._lastFrame = now;        // nur merken; gezeichnet wird in der Sekundenuhr bei Aenderung
            });
            conn.connect('closed', () => { this._conn = null; this._retry(); });
            conn.connect('error', () => { this._conn = null; });
        });
    }

    _retry() {
        if (this._retryId || !this._cancel || this._cancel.is_cancelled()) return;
        this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 10, () => {
            this._retryId = 0; this._connect(); return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._tickId) { GLib.source_remove(this._tickId); this._tickId = 0; }
        if (this._retryId) { GLib.source_remove(this._retryId); this._retryId = 0; }
        this._cancel?.cancel(); this._cancel = null;
        try { this._conn?.close(1000, null); } catch (e) { /* ignorieren */ }
        this._conn = null; this._session = null;
        this._ind?.destroy(); this._ind = null;
        this._tracker = null;
    }
}
