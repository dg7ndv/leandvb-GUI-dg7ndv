// peaks.js - Peak-Erkennung fuer das BATC-QO-100-Wideband-Spektrum (Logik wie leandvb-GUI / waechter_mqtt.py)
export const START = 10490.5, BW_MHZ = 9.0, MIN_BW = 80;
export const BEACON = 10491.52, BEACON_TOL = 0.06, BEACON_NOM = 10491.5;
export const SIG_TOL = 0.2, WINDOW = 5.0, GONE_S = 10;
const SR_STEPS = [250, 333, 500, 1000, 2000], SR_LIMITS = [305, 425, 750, 1500];

export function srFromBw(bw) {
    for (let i = 0; i < SR_LIMITS.length; i++)
        if (bw < SR_LIMITS[i]) return SR_STEPS[i];
    return SR_STEPS[SR_STEPS.length - 1];
}

export class SpectrumTracker {
    constructor() {
        this.n = 0;
        this.smooth = null;     // geglaettetes Spektrum (dB) fuer die Anzeige
        this.floor = 0;         // Rauschboden (25-%-Perzentil)
        this.segments = [];     // aktuelle Peaks: {a, b, f, bw, beacon}
        this.mask = null;       // 0..1 je Bin: gehoert zu einem Peak (mit Nachleuchten), Rauschen = 0
        this.frames = [];       // [zeit, [[f, bw], ...]] ohne Bake
        this.stable = [];
        this.state = new Map(); // id -> {freq, bw, sr, born, goneSince}
        this.beaconT = 0;
        this.onNew = null; this.onGone = null; this.onBack = null;
    }

    feed(u16) {
        const n = u16.length;
        const db = new Float32Array(n);
        for (let i = 0; i < n; i++) db[i] = 10 * Math.log10(u16[i] > 0 ? u16[i] : 1);
        if (!this.smooth || this.smooth.length !== n) this.smooth = Float32Array.from(db);
        else for (let i = 0; i < n; i++) this.smooth[i] += 0.3 * (db[i] - this.smooth[i]);
        this.n = n;
        const sorted = Float32Array.from(db).sort();
        const thresh = sorted[Math.floor(n * 0.25)] + 2.5;
        this.floor = sorted[Math.floor(n * 0.25)];
        const segs = [], list = [];
        let inSig = false, sx = 0, beacon = false;
        for (let x = 0; x <= n; x++) {
            const hi = x < n && db[x] > thresh;
            if (hi && !inSig) { inSig = true; sx = x; }
            else if (!hi && inSig) {
                inSig = false;
                const w = x - sx;
                if (w > 5) {
                    const f = START + ((sx + w / 2) / n) * BW_MHZ;
                    const bw = (w / n) * BW_MHZ * 1000;
                    if (bw > MIN_BW) {
                        const isB = Math.abs(f - BEACON) <= BEACON_TOL;
                        segs.push({a: sx, b: x, f, bw, beacon: isB});
                        if (isB) beacon = true; else list.push([f, bw]);
                    }
                }
            }
        }
        this.segments = segs;
        if (!this.mask || this.mask.length !== n) this.mask = new Float32Array(n);
        const inSeg = new Uint8Array(n);
        for (const g of segs) for (let i = g.a; i < g.b; i++) inSeg[i] = 1;
        for (let i = 0; i < n; i++) this.mask[i] = inSeg[i] ? 1 : this.mask[i] * 0.9;   // Peaks blenden weich aus
        const t = Date.now() / 1000;
        if (beacon) this.beaconT = t;
        this.frames.push([t, list]);
        while (this.frames.length && this.frames[0][0] < t - 30) this.frames.shift();
    }

    _aggregate(t) {
        const fr = this.frames.filter(x => x[0] >= t - WINDOW);
        if (!fr.length) return null;
        const cl = [];
        for (const [, sigs] of fr) {
            const used = new Set();
            for (const [f, bw] of sigs) {
                let best = -1;
                cl.forEach((c, i) => {
                    if (!used.has(i) && Math.abs(c.f - f) <= SIG_TOL && (best < 0 || Math.abs(c.f - f) < Math.abs(cl[best].f - f))) best = i;
                });
                if (best < 0) { cl.push({f, bw, n: 1}); used.add(cl.length - 1); }
                else { const c = cl[best]; c.f = (c.f * c.n + f) / (c.n + 1); c.bw = (c.bw * c.n + bw) / (c.n + 1); c.n++; used.add(best); }
            }
        }
        return [fr.length, cl];
    }

    // einmal pro Sekunde aufrufen; liefert true bei Aenderung der Signalliste
    tick() {
        const t = Date.now() / 1000;
        const res = this._aggregate(t);
        let changed = false;
        if (res) {
            const [n, cl] = res;
            const nw = [];
            for (const c of cl.sort((a, b) => a.f - b.f)) {
                const known = this.stable.some(([f]) => Math.abs(f - c.f) <= SIG_TOL);
                if (c.n / n >= (known ? 0.2 : 0.5) && !nw.some(([f]) => Math.abs(f - c.f) <= SIG_TOL))
                    nw.push([Math.round(c.f * 1000) / 1000, Math.round(c.bw)]);
            }
            this.stable = nw;
            const seen = new Set();
            for (const [f, bw] of nw) {
                let key = null;
                for (const [k, st] of this.state) if (Math.abs(st.freq - f) <= SIG_TOL) { key = k; break; }
                if (key === null) {
                    key = f;
                    this.state.set(key, {freq: f, bw, sr: srFromBw(bw), born: t, goneSince: null});
                    changed = true;
                    if (Math.abs(f - BEACON_NOM) > 0.5 && this.onNew) this.onNew(this.state.get(key));
                } else {
                    const st = this.state.get(key);
                    if (st.goneSince !== null) { st.goneSince = null; st.born = t; changed = true; if (this.onBack) this.onBack(st); }
                    st.freq = f; st.bw = bw; st.sr = srFromBw(bw);
                }
                seen.add(key);
            }
            for (const [k, st] of this.state) if (!seen.has(k) && st.goneSince === null) { st.goneSince = t; changed = true; }
        }
        for (const [k, st] of [...this.state]) {
            if (st.goneSince !== null && t - st.goneSince >= GONE_S) {
                this.state.delete(k); changed = true;
                if (Math.abs(st.freq - BEACON_NOM) > 0.5 && this.onGone) this.onGone(st);
            }
        }
        return changed;
    }

    // aktive Signale (ohne Bake), nach Frequenz sortiert
    signals() {
        return [...this.state.values()].filter(s => Math.abs(s.freq - BEACON_NOM) > 0.5).sort((a, b) => a.freq - b.freq);
    }
    beaconOn() { return Date.now() / 1000 - this.beaconT < 30; }
}
