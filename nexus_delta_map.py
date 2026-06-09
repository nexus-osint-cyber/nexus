"""
NEXUS – Zeitraum-Vergleich (Delta-Karte) (T92)
===============================================
Vergleicht zwei Zeitfenster und zeigt Veränderungen als grün/rot Delta-Karte.
Grün = neue Ereignisse / Verschlechterung. Blau = verschwunden / Entspannung.

Öffentliche API:
  build_delta_map_html(port) -> str   komplettes HTML-Dokument (via Live-Server)
"""

from __future__ import annotations


def build_delta_map_html(port: int = 11430) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS DELTA-KARTE</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root {{
  --bg:#060a10; --border:#1e3a4a; --accent:#00d4ff;
  --green:#00ff88; --red:#ff4444; --text:#c8d6e0;
  --muted:#4a6070; --font:'Courier New',monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);
  font-size:11px;display:flex;flex-direction:column;height:100vh;overflow:hidden}}
#header{{background:linear-gradient(90deg,#040d18,#07152a);
  border-bottom:2px solid var(--accent);padding:8px 14px;
  display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap;
  box-shadow:0 2px 20px rgba(0,212,255,0.1)}}
.logo{{color:var(--accent);font-size:13px;font-weight:bold;letter-spacing:3px;
  text-shadow:0 0 15px rgba(0,212,255,0.4)}}
.hdr-btn{{background:#071a2e;border:1px solid #1e3a4a;color:var(--accent);
  padding:3px 10px;cursor:pointer;font-family:inherit;font-size:10px;border-radius:3px}}
.hdr-btn:hover{{border-color:var(--accent)}}
#ctrl{{background:rgba(4,17,30,0.95);border-bottom:1px solid #0d2035;
  padding:6px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
  flex-shrink:0;backdrop-filter:blur(6px)}}
.ctrl-grp{{display:flex;align-items:center;gap:6px;font-size:10px;color:#8ab0c8}}
.ctrl-grp label{{color:var(--muted);white-space:nowrap}}
select,input[type=date]{{background:#060b10;border:1px solid #1e3a4a;
  color:var(--text);padding:3px 6px;font-family:inherit;font-size:10px;border-radius:2px}}
select:focus,input[type=date]:focus{{outline:none;border-color:var(--accent)}}
#map{{flex:1}}
#legend{{position:fixed;bottom:20px;right:10px;background:rgba(6,10,16,0.9);
  border:1px solid #1e3a4a;padding:10px 14px;font-size:10px;z-index:1000;
  border-radius:4px;backdrop-filter:blur(4px)}}
.leg-row{{display:flex;align-items:center;gap:6px;margin-bottom:4px}}
.leg-dot{{width:12px;height:12px;border-radius:50%;flex-shrink:0}}
#status{{margin-left:auto;font-size:10px;color:var(--muted)}}
#delta-counts{{display:flex;gap:16px;font-size:10px}}
.dc-new{{color:var(--red)}} .dc-gone{{color:#4488ff}} .dc-same{{color:var(--muted)}}
</style>
</head>
<body>

<div id="header">
  <span class="logo">◈ NEXUS</span>
  <span style="color:#4a6070;letter-spacing:2px;font-size:11px">DELTA-KARTE</span>
  <div id="delta-counts">
    <span class="dc-new">🔴 Neu: <span id="cnt-new">0</span></span>
    <span class="dc-gone">🔵 Weg: <span id="cnt-gone">0</span></span>
    <span class="dc-same" style="color:#ffaa00">🟡 Verschärft: <span id="cnt-esc">0</span></span>
  </div>
  <button class="hdr-btn" onclick="window.location=location.origin+'/livemap'">⬅ Karte</button>
  <div id="status">–</div>
</div>

<div id="ctrl">
  <div class="ctrl-grp">
    <label>Region:</label>
    <select id="sel-region" onchange="doCompare()">
      <option>Ukraine</option>
      <option>Naher Osten</option>
      <option>Rotes Meer</option>
      <option>Persischer Golf</option>
      <option>Taiwan</option>
      <option>Europa</option>
      <option>Global</option>
    </select>
  </div>
  <div class="ctrl-grp">
    <label>Fenster A:</label>
    <select id="sel-a">
      <option value="120">2h</option>
      <option value="360">6h</option>
      <option value="720" selected>12h</option>
      <option value="1440">24h</option>
      <option value="4320">3 Tage</option>
    </select>
  </div>
  <div class="ctrl-grp">
    <label>Fenster B:</label>
    <select id="sel-b">
      <option value="120">2h</option>
      <option value="360">6h</option>
      <option value="720" selected>12h</option>
      <option value="1440">24h</option>
      <option value="4320">3 Tage</option>
    </select>
    <span style="color:var(--muted)">(B = Vergangenheit, A = Jetzt)</span>
  </div>
  <div class="ctrl-grp">
    <label>Layer:</label>
    <select id="sel-layer">
      <option value="all">Alle</option>
      <option value="acled">ACLED Konflikte</option>
      <option value="fires">FIRMS Brände</option>
      <option value="seismic">Seismik</option>
      <option value="fusion">Fusion Threats</option>
    </select>
  </div>
  <button class="hdr-btn" onclick="doCompare()">▶ Vergleichen</button>
  <button class="hdr-btn" onclick="toggleHeatdiff()" id="heat-btn">🌡 Diff-Heat</button>
</div>

<div id="map"></div>

<div id="legend">
  <div style="color:var(--accent);font-weight:bold;margin-bottom:6px;font-size:10px;letter-spacing:2px">LEGENDE</div>
  <div class="leg-row"><div class="leg-dot" style="background:#ff2244"></div>Neues Ereignis (jetzt)</div>
  <div class="leg-row"><div class="leg-dot" style="background:#4488ff"></div>Verschwunden (früher)</div>
  <div class="leg-row"><div class="leg-dot" style="background:#ffaa00"></div>Verschärft (höhere Fatalities)</div>
  <div class="leg-row"><div class="leg-dot" style="background:#00ff88;opacity:0.6"></div>Unverändert</div>
</div>

<script>
const BASE = window.location.origin;
const PORT = {port};
const map = L.map('map', {{center:[49,32],zoom:5,zoomControl:true,attributionControl:false}});
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',
  {{subdomains:'abcd',maxZoom:19}}).addTo(map);

const LYR_NEW  = L.layerGroup().addTo(map);
const LYR_GONE = L.layerGroup().addTo(map);
const LYR_ESC  = L.layerGroup().addTo(map);
const LYR_SAME = L.layerGroup().addTo(map);
let heatdiff = null;
let heatOn = false;

function mkDot(lat,lon,color,radius,title,body){{
  return L.circleMarker([lat,lon],{{
    radius,color,fillColor:color,fillOpacity:0.7,weight:1.5,
  }}).bindPopup(`<div style="font-size:11px;color:#c8d6e0;font-family:'Courier New',monospace">
    <div style="color:${{color}};font-weight:bold;margin-bottom:4px">${{title}}</div>
    <div>${{body}}</div></div>`);
}}

async function fetchData(region){{
  const r = await fetch(`${{BASE}}/api/data?query=${{encodeURIComponent(region)}}`,
    {{cache:'no-store'}});
  return r.json();
}}

function extractPoints(d, layer){{
  const pts = [];
  function add(lat,lon,weight,label,meta){{
    if(lat&&lon) pts.push({{lat,lon,weight:weight||0.5,label,meta}});
  }}
  if(layer==='all'||layer==='acled')
    (d.acled||[]).forEach(a=>add(a.lat,a.lon,Math.min(1,(a.fatalities||0)*0.05+0.3),
      'ACLED: '+esc(a.event_type||''),esc(a.title||'')));
  if(layer==='all'||layer==='fires')
    (d.fires||[]).forEach(f=>add(f.lat,f.lon,Math.min(1,(f.frp||1)/200+0.2),
      'Brand FRP:'+f.frp,''));
  if(layer==='all'||layer==='seismic')
    (d.earthquakes||[]).forEach(q=>add(q.lat,q.lon,
      Math.min(1,((q.magnitude||1)-1)*0.2),
      'M'+(q.magnitude||q.mag||'?'),q.place||''));
  if(layer==='all'||layer==='fusion')
    (d.fusion_threats||[]).forEach(ft=>add(ft.lat,ft.lon,
      {{KRITISCH:1,HOCH:0.75,MITTEL:0.5,NIEDRIG:0.25}}[ft.severity||'MITTEL']||0.5,
      ft.title||'Fusion',ft.text||''));
  return pts;
}}

function esc(s){{return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}}

function geoKey(pt){{
  return `${{Math.round(pt.lat*20)/20}},${{Math.round(pt.lon*20)/20}}`;
}}

async function doCompare(){{
  const region = document.getElementById('sel-region').value;
  const layer  = document.getElementById('sel-layer').value;
  document.getElementById('status').textContent = '⧋ Vergleiche...';

  // Wir haben nur eine API-Quelle (Echtzeit), simulieren A vs B
  // durch Vergleich mit gespeichertem Snapshot aus localStorage
  let dataA, dataB;
  try {{
    dataA = await fetchData(region);
  }} catch(e) {{
    document.getElementById('status').textContent = '✗ ' + e.message;
    return;
  }}

  const snapKey = 'nexus_delta_snap_' + region.toLowerCase().replace(/\s/g,'_');
  const saved = localStorage.getItem(snapKey);
  if (saved) {{
    try {{ dataB = JSON.parse(saved); }} catch(e) {{ dataB = dataA; }}
  }} else {{
    dataB = {{}}; // kein Snapshot = alles neu
  }}

  // Punkte extrahieren
  const ptsA = extractPoints(dataA, layer);
  const ptsB = extractPoints(dataB, layer);

  // Karte leeren
  LYR_NEW.clearLayers(); LYR_GONE.clearLayers();
  LYR_ESC.clearLayers(); LYR_SAME.clearLayers();
  if(heatdiff && map.hasLayer(heatdiff)) map.removeLayer(heatdiff);

  // Schlüssel-Map aufbauen
  const mapB = {{}};
  ptsB.forEach(p => {{ mapB[geoKey(p)] = p; }});
  const mapA = {{}};
  ptsA.forEach(p => {{ mapA[geoKey(p)] = p; }});

  let nNew=0, nGone=0, nEsc=0;
  const heatPts = [];

  // Neu in A (nicht in B) = rot
  ptsA.forEach(p => {{
    const k = geoKey(p);
    if (!mapB[k]) {{
      mkDot(p.lat,p.lon,'#ff2244',7,'🔴 NEU',p.label+'<br>'+p.meta).addTo(LYR_NEW);
      heatPts.push([p.lat,p.lon,p.weight]);
      nNew++;
    }} else if(p.weight > mapB[k].weight + 0.15) {{
      mkDot(p.lat,p.lon,'#ffaa00',7,'🟠 VERSCHÄRFT',p.label+'<br>'+p.meta).addTo(LYR_ESC);
      heatPts.push([p.lat,p.lon,p.weight*0.7]);
      nEsc++;
    }} else {{
      mkDot(p.lat,p.lon,'#00ff88',4,'🟢 UNVERÄNDERT',p.label).addTo(LYR_SAME);
    }}
  }});

  // In B, nicht mehr in A = blau (entspannt)
  ptsB.forEach(p => {{
    const k = geoKey(p);
    if (!mapA[k]) {{
      mkDot(p.lat,p.lon,'#4488ff',6,'🔵 WEGGEFALLEN',p.label+'<br>'+p.meta).addTo(LYR_GONE);
      nGone++;
    }}
  }});

  // Zähler aktualisieren
  document.getElementById('cnt-new').textContent  = nNew;
  document.getElementById('cnt-gone').textContent = nGone;
  document.getElementById('cnt-esc').textContent  = nEsc;

  // Diff-Heatmap (nur neue + verschärfte)
  if(heatPts.length > 0) {{
    heatdiff = L.heatLayer ? L.heatLayer(heatPts, {{
      radius:28,blur:20,max:1.0,
      gradient:{{0:'#001a33',0.4:'#aa2200',0.8:'#ff4400',1:'#ff0044'}}
    }}) : null;
    if(heatdiff && heatOn) heatdiff.addTo(map);
  }}

  // Snapshot speichern (für nächsten Vergleich)
  try {{ localStorage.setItem(snapKey, JSON.stringify(dataA)); }} catch(e){{}}

  const t = new Date().toLocaleTimeString('de-DE');
  document.getElementById('status').textContent =
    `✓ ${{t}} | ${{ptsA.length}} jetzt · ${{ptsB.length}} früher`;
}}

function toggleHeatdiff(){{
  heatOn = !heatOn;
  document.getElementById('heat-btn').style.borderColor = heatOn ? 'var(--accent)' : '';
  if (!heatdiff) return;
  heatOn ? (!map.hasLayer(heatdiff) && map.addLayer(heatdiff))
         : (map.hasLayer(heatdiff) && map.removeLayer(heatdiff));
}}

// Init
doCompare();
</script>
</body>
</html>"""
