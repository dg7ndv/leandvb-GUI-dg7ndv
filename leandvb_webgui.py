#!/usr/bin/env python3
"""leandvb_webgui.py - Seite des neuen, schlanken Web-GUIs des QO-100 Tuner Switchers (im Stil der Zapper-Webseite am Pi).

Wird von leandvb-GUI.py eingebunden (Handler _webgui_handler_cls) und liefert nur die HTML-Seite (PAGE). Die Seite fragt /api/state ab
(Tuner, Signale, Spektrum) und steuert ueber POST-Aufrufe: /api/tune, /api/click, /api/redistribute, /api/redistribute_rest, /api/vlc_server,
/api/stop_player, /api/scan, /api/auto_master, /api/auto_target, /api/mute_all, /api/player_neu. Handy-tauglich, hell/dunkel automatisch.
Das alte Webinterface (Port 8099, nur mit -web oder Haken im Menue Konfig) erreicht man hier unter /alt.
"""

PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QO-100 Tuner Switcher</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--fg:#1b1f24;--mut:#5b6573;--bd:#d3d9e0;--acc:#1f7a3a;--accbg:#e4f3e8;--warn:#e08a00;--warnbg:#fff3d6;--err:#c0392b;--link:#1a5fb4;--blue:#2b7de9}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--card:#1d2126;--fg:#e8ebef;--mut:#9aa5b1;--bd:#363d45;--acc:#3ecf6a;--accbg:#173323;--warn:#f0a020;--warnbg:#3a2e12;--err:#ff6b5b;--link:#7db3ff;--blue:#5aa0ff}}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg);max-width:82em;margin:0 auto;padding:.7em .9em 2em;line-height:1.35}
h2{margin:.2em 0 .5em;font-size:1.35em}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
button,a.pill{display:inline-block;font:inherit;font-size:.92em;padding:.42em 1.05em;margin:.15em .15em .15em 0;border:1px solid var(--bd);border-radius:999px;background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none;transition:background .12s,border-color .12s}
button:hover,a.pill:hover{border-color:var(--acc);background:var(--accbg);text-decoration:none}
button:active{transform:translateY(1px)}button:disabled{opacity:.45;cursor:default}
button.on,a.pill.on{background:var(--acc);border-color:var(--acc);color:#fff}
button.sig.asg{background:var(--blue);border-color:var(--blue);color:#fff}
button.sig.bk{border-style:dashed}
button.warn{background:var(--warnbg);border-color:var(--warn)}
label.chk{display:inline-block;font-size:.92em;padding:.42em 1.05em;margin:.15em .15em .15em 0;border:1px solid var(--bd);border-radius:999px;background:var(--card);cursor:pointer}
label.chk input{margin-right:.5em;vertical-align:-.1em;accent-color:var(--acc)}label.chk.on{background:var(--accbg);border-color:var(--acc)}
.top{display:flex;align-items:center;justify-content:space-between;gap:.6em;flex-wrap:wrap}.top h2{margin:.1em 0}
.bar{display:flex;flex-wrap:wrap;gap:.1em .5em;align-items:center;margin:.3em 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(19em,1fr));gap:.6em}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:.6em .9em}
.card h3{margin:.1em 0 .3em;font-size:1.02em;display:flex;justify-content:space-between;gap:.5em;align-items:center}
.chip{padding:.1em .65em;border-radius:99px;font-size:.78em;background:var(--bg);border:1px solid var(--bd);color:var(--mut);white-space:nowrap}
.chip.lock{background:var(--accbg);border-color:var(--acc);color:var(--acc);font-weight:600}.chip.scan{background:var(--warnbg);border-color:var(--warn)}
.st{font-size:.9em;margin:.2em 0 .4em}.st b{font-size:1.05em}.call{color:var(--acc);font-weight:700}
.mer{display:inline-block;background:#f1c40f;color:#000;border-radius:6px;padding:0 .5em;font-weight:600;font-size:.85em}
.sigs button{margin:.1em .1em .1em 0;padding:.3em .8em;font-size:.86em}
.act{margin-top:.35em;border-top:1px solid var(--bd);padding-top:.35em}
.act button,.act a.pill,.act label.chk{font-size:.82em;padding:.3em .8em}
.mut{color:var(--mut)}small{color:var(--mut)}
canvas#sp{width:100%;height:230px;background:#0d1117;border-radius:10px;display:block;cursor:crosshair}
.lnk{font-size:.85em}
</style></head><body>
<div class="top"><h2>QO-100 Tuner Switcher &ndash; DG7NDV</h2><div class="lnk"><a href="/alt" title="altes Webinterface">altes Interface</a></div></div>
<div class="bar" id="top"></div>
<div class="grid" id="cards"></div>
<div class="card" style="margin-top:.6em">
 <h3><span>BATC-Spektrum</span><span><small>Klick auf ein Signal &rarr; Tuner:</small>
 <select id="tsel"><option value="auto">auto (freier Tuner)</option></select></span></h3>
 <canvas id="sp" width="1200" height="230"></canvas>
 <div class="mut" id="spinfo" style="font-size:.82em;margin-top:.25em"></div>
</div>
<div class="mut" style="margin:.8em 0 .2em;font-size:.82em" id="foot"></div>
<script>
"use strict";
let S = {targets: [], signals: [], auto: false}, SP = {pts: [], sigs: []}, busy = false;
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
async function post(path, d) {
  try { const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(d || {})}); if (!r.ok) alert(path + ": " + await r.text()); }
  catch (e) { alert("Verbindung zum GUI verloren"); }
  setTimeout(load, 400);
}
const fm = f => f ? f.toFixed(3) : "-";
function near(a, b) { return a != null && b != null && Math.abs(a - b) <= 0.02; }
const isAnd = /Android/i.test(navigator.userAgent);
function listHref() {   // Android: VLC direkt per Intent (kein .m3u-Download), sonst normale .m3u
  const q = "/play/list.m3u?t=" + S.targets.map(t => t.id).join(",") + "&mode=http";
  return isAnd ? "intent://" + location.host + q + "#Intent;scheme=http;type=audio/x-mpegurl;package=org.videolan.vlc;end" : q;
}
function topbar() {
  $("top").innerHTML =
    '<button class="' + (S.auto ? "on" : "") + '" onclick="post(\'/api/auto_master\',{on:' + (!S.auto) + '})">Verteil Automatik: ' + (S.auto ? "AN" : "AUS") + '</button>' +
    '<button onclick="post(\'/api/redistribute\')">Neu verteilen</button>' +
    '<button onclick="post(\'/api/redistribute_rest\')">NeuNeuVerteilen</button>' +
    '<button onclick="post(\'/api/player_neu\')">PlayerNeu</button>' +
    '<button onclick="post(\'/api/mute_all\')">Mute all</button>' +
    '<a class="pill" href="' + listHref() + '">Playlist (alle)</a>';
}
function render() {
  topbar();
  const host = location.host;
  $("cards").innerHTML = S.targets.map(t => {
    const lock = t.lock === true;
    const chip = t.scan ? '<span class="chip scan">Scan</span>' : (lock ? '<span class="chip lock">LOCK</span>' : '<span class="chip">' + (t.lock === false ? "kein Lock" : "-") + "</span>");
    const sigs = [{freq: 10491.5, sr: 1500, beacon: true, call: ""}].concat(S.signals.filter(s => s.alive && !s.beacon)).map(s => {
      const asg = near(t.freq, s.freq);
      return '<button class="sig' + (s.beacon ? " bk" : "") + (asg ? " asg" : "") + '" title="' + esc(s.call || "") + '" onclick="post(\'/api/tune\',{target:\'' + t.id + "',freq:" + s.freq + ",sr:" + s.sr + '})">' +
             (s.beacon ? "Bake" : fm(s.freq)) + " &middot; " + s.sr + "K" + (s.call && !s.beacon ? " " + esc(s.call) : "") + "</button>";
    }).join("");
    const hv = t.stream ? '<a class="pill" href="intent://' + host + "/stream/" + t.id + '#Intent;scheme=http;type=video/mp2t;package=org.videolan.vlc;end" title="VLC am Handy (Android)">VLC Handy</a>' +
               '<a class="pill" href="/play/' + t.id + '.m3u?mode=http" title="Stream per HTTP (.m3u)">.m3u</a>' : "";
    return '<div class="card"><h3><span>' + esc(t.label) + "</span>" + chip + "</h3>" +
      '<div class="st"><b>' + fm(t.freq) + " MHz</b> " + (t.sr ? t.sr + "K " : "") + (t.mer ? '<span class="mer">' + esc(t.mer) + "</span> " : "") +
      (t.call ? '<span class="call">' + esc(t.call) + "</span>" : "") + "</div>" +
      '<div class="sigs">' + sigs + "</div>" +
      '<div class="act"><button onclick="post(\'/api/vlc_server\',{target:\'' + t.id + '\'})" title="VLC auf dem GUI-Rechner starten">VLC (PC)</button>' +
      '<button onclick="post(\'/api/stop_player\',{target:\'' + t.id + '\'})">&#9632; Stopp</button>' +
      '<button onclick="post(\'/api/scan\',{target:\'' + t.id + '\'})">Scan</button>' + hv +
      '<label class="chk' + (t.auto ? " on" : "") + '"><input type="checkbox" ' + (t.auto ? "checked" : "") + ' onchange="post(\'/api/auto_target\',{target:\'' + t.id + "',on:this.checked})\">VerteilA</label></div></div>";
  }).join("");
  const sel = $("tsel"), cur = sel.value;
  if (sel.options.length !== S.targets.length + 1) {
    sel.innerHTML = '<option value="auto">auto (freier Tuner)</option>' + S.targets.map(t => '<option value="' + t.id + '">' + esc(t.label) + "</option>").join("");
    sel.value = cur || "auto";
  }
  $("foot").textContent = "Stand " + new Date(S.t * 1000).toLocaleTimeString() + " · " + S.targets.length + " Tuner · " + S.signals.filter(s => s.alive && !s.beacon).length + " Signale";
}
function spec() {
  const c = $("sp"), w = c.clientWidth, h = c.clientHeight, dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const x = c.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, w, h);
  const p = SP.pts || []; if (!p.length) { x.fillStyle = "#8b949e"; x.fillText("warte auf BATC-Spektrum ...", 10, h / 2); return; }
  const g = x.createLinearGradient(0, 0, 0, h); g.addColorStop(0, "#f1c40f"); g.addColorStop(.5, "#2ecc71"); g.addColorStop(1, "#14532d");
  x.beginPath(); x.moveTo(0, h); p.forEach((v, i) => x.lineTo(i / (p.length - 1) * w, h - 22 - Math.min(1, v / 1000) * (h - 40))); x.lineTo(w, h); x.closePath();
  x.globalAlpha = .85; x.fillStyle = g; x.fill(); x.globalAlpha = 1; x.strokeStyle = "#d7f7df"; x.stroke();
  x.fillStyle = "#9aa5b1"; x.font = "11px sans-serif";
  for (let m = Math.ceil(SP.start); m <= SP.start + SP.span; m++) { const px = (m - SP.start) / SP.span * w; x.fillText(m, px - 14, h - 6); x.fillRect(px, 0, 1, h - 22); }
  (SP.sigs || []).forEach(s => {
    const x0 = (s.f0 - SP.start) / SP.span * w, x1 = (s.f1 - SP.start) / SP.span * w;
    x.strokeStyle = s.beacon ? "#58a6ff" : (s.tuners && s.tuners.length ? "#2b7de9" : "#ffd33d"); x.lineWidth = 1.5; x.strokeRect(x0, 6, x1 - x0, h - 30); x.lineWidth = 1;
    x.fillStyle = s.beacon ? "#58a6ff" : "#ffd33d"; x.font = "bold 11px sans-serif"; x.fillText((s.beacon ? "Bake" : (s.call || s.freq.toFixed(2))) + " " + s.sr + "k", x0 + 3, 18);
    if (s.tuners && s.tuners.length) { x.font = "10px sans-serif"; x.fillStyle = "#9ec5ff"; x.fillText(s.tuners.join(", ").replace(/RTL-Dienst /g, ""), x0 + 3, 31); }
  });
}
$("sp").addEventListener("click", e => {
  const r = $("sp").getBoundingClientRect(), f = SP.start + (e.clientX - r.left) / r.width * SP.span;
  post("/api/click", {target: $("tsel").value, freq: f});
});
async function load() {
  if (busy) return; busy = true;
  try {
    const r = await fetch("/api/state"); S = await r.json();
    render();
    const q = await fetch("/api/spectrum"); SP = await q.json(); spec();
    $("spinfo").textContent = (SP.sigs || []).length + " Signale erkannt";
  } catch (e) { $("foot").textContent = "keine Verbindung zum GUI ..."; }
  busy = false;
}
load(); setInterval(load, 1000); window.addEventListener("resize", spec);
</script></body></html>
"""
