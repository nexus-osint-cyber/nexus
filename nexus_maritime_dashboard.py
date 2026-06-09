"""
NEXUS - Maritim-Dashboard (T174)
Eigenständige Web-UI für Küsten/Meerengen-Überwachung:
  - AIS-Transponderdaten (nexus_ais.get_vessels)
  - SAR-Schiffserkennung via Sentinel-1 (nexus_sar.detect_ships)
  - Direkte EO-Browser-Links zum Herunterladen/manuellen Auswerten von
    Satellitenbildern (kein Account nötig) – "menschliches Auge" Funktion,
    die der Nutzer ausdrücklich gewünscht hat (Vorbild: Straße von Hormuz)

Läuft als eigene Seite "/maritime" im nexus_live_server, analog zu
nexus_livemap.py / nexus_timeline.py. Holt Daten live über
"/api/maritime_imagery?region=...".
"""

from __future__ import annotations


def build_maritime_dashboard_html(port: int = 11430) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#00d4ff">
<title>NEXUS · Maritim-Dashboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#060b10;color:#c8d6e0;font-family:'Courier New',monospace;
     font-size:12px;height:100vh;display:flex;flex-direction:column;overflow:hidden}}
#header{{background:linear-gradient(90deg,#04111e,#071a2e);border-bottom:2px solid #00d4ff;
        padding:8px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;flex-shrink:0}}
.logo{{color:#00d4ff;font-size:15px;font-weight:bold;letter-spacing:3px}}
.subtitle{{color:#4a8090;font-size:10px}}
#region-bar{{background:#04111e;border-bottom:1px solid #0d2035;padding:6px 16px;
            display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0}}
.reg-btn{{background:#071a2e;border:1px solid #1e3a4a;color:#8ab0c8;padding:5px 12px;
         cursor:pointer;font-family:inherit;font-size:11px;border-radius:3px}}
.reg-btn:hover{{border-color:#00d4ff;color:#00d4ff}}
.reg-btn.active{{border-color:#00ff88;color:#00ff88;background:#0a2418}}
#main{{flex:1;display:flex;overflow:hidden}}
#map{{flex:1.3;z-index:50}}
#panel{{width:380px;flex-shrink:0;background:#070d14;border-left:1px solid #0d2035;
       overflow-y:auto;padding:14px}}
.box{{background:#0a1622;border:1px solid #14283a;border-radius:5px;padding:12px;margin-bottom:12px}}
.box h3{{color:#00d4ff;font-size:12px;letter-spacing:1px;margin-bottom:8px;
        border-bottom:1px solid #14283a;padding-bottom:6px}}
.row{{display:flex;justify-content:space-between;padding:3px 0;font-size:11px}}
.row .k{{color:#4a8090}}
.row .v{{color:#c8d6e0;font-weight:bold}}
.badge{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:bold}}
.b-ok{{background:#0a3320;color:#00ff88}}
.b-warn{{background:#332a0a;color:#ffcc00}}
.b-crit{{background:#330a0a;color:#ff4444}}
.b-info{{background:#0a2035;color:#00d4ff}}
.eo-link{{display:block;text-align:center;background:#00334a;border:1px solid #00d4ff;
         color:#00d4ff;padding:9px;border-radius:4px;text-decoration:none;
         font-weight:bold;font-size:11px;margin-top:8px;letter-spacing:0.5px}}
.eo-link:hover{{background:#00496a}}
.hint{{color:#4a6070;font-size:10px;line-height:1.5;margin-top:6px}}
.ship-item{{border-bottom:1px solid #0d2035;padding:5px 0;font-size:10px}}
.ship-item .conf-bar{{height:4px;background:#1e3a4a;border-radius:2px;margin-top:3px;overflow:hidden}}
.ship-item .conf-fill{{height:100%;background:#00d4ff}}
#filters label{{display:flex;align-items:center;justify-content:space-between;
               font-size:10px;color:#8ab0c8;margin-bottom:6px}}
#filters input[type=range]{{width:140px}}
.loading{{color:#4a6070;font-size:11px;text-align:center;padding:20px}}
.err{{color:#ff6644;font-size:11px}}
#legend{{position:absolute;bottom:14px;left:14px;background:#071a2ecc;border:1px solid #14283a;
        border-radius:4px;padding:8px 12px;font-size:10px;z-index:900;color:#8ab0c8}}
</style>
</head>
<body>

<div id="header">
  <div class="logo">⚓ NEXUS MARITIM</div>
  <div class="subtitle">Küsten &amp; Meerengen · AIS-Transponder + SAR-Satellitenbilder · manuelle Auswertung</div>
</div>

<div id="region-bar"></div>

<div id="main">
  <div style="position:relative;flex:1.3">
    <div id="map"></div>
    <div id="legend">
      <b style="color:#00d4ff">Legende</b><br>
      🛰 SAR-Erkennung &nbsp;|&nbsp; 📡 AIS-Schiff &nbsp;|&nbsp; ⚠ Anomalie
    </div>
  </div>
  <div id="panel">
    <div id="content" class="loading">Region wählen, um Daten zu laden ...</div>
  </div>
</div>

<script>
const PORT   = {port};
const API    = `http://localhost:${{PORT}}/api/maritime_imagery`;

const REGIONS = [
  {{key:"Hormuz-Strasse",            label:"Straße von Hormuz",     icon:"🛢"}},
  {{key:"Rotes Meer / Bab el-Mandeb", label:"Bab el-Mandeb",         icon:"🚢"}},
  {{key:"Suez-Kanal",                label:"Suezkanal",             icon:"🚢"}},
  {{key:"Bosporus",                  label:"Bosporus",              icon:"⚓"}},
  {{key:"Taiwan-Strasse",            label:"Taiwan-Straße",         icon:"⚠"}},
  {{key:"Schwarzes Meer",            label:"Schwarzes Meer",        icon:"⚓"}},
  {{key:"Ostsee",                    label:"Ostsee",                icon:"⚓"}},
];

let map, markersLayer, currentRegion = null;

function initMap() {{
  map = L.map('map', {{zoomControl:true}}).setView([26.5, 56.5], 5);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 18, attribution: '© OpenStreetMap'
  }}).addTo(map);
  map.getContainer().style.background = '#060b10';
  markersLayer = L.layerGroup().addTo(map);
}}

function buildRegionBar() {{
  const bar = document.getElementById('region-bar');
  REGIONS.forEach(r => {{
    const btn = document.createElement('button');
    btn.className = 'reg-btn';
    btn.textContent = `${{r.icon}} ${{r.label}}`;
    btn.onclick = () => loadRegion(r.key, btn);
    bar.appendChild(btn);
  }});
}}

function setActive(btn) {{
  document.querySelectorAll('.reg-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}}

function confColor(c) {{
  if (c > 0.7) return '#ff4444';
  if (c > 0.4) return '#ffcc00';
  return '#00d4ff';
}}

async function loadRegion(regionKey, btn) {{
  currentRegion = regionKey;
  setActive(btn);
  const content = document.getElementById('content');
  content.className = 'loading';
  content.innerHTML = `Lade Daten für <b>${{regionKey}}</b> ...<br><span style="color:#4a6070">AIS + SAR-Satellitenanalyse (kann 10-30s dauern)</span>`;
  markersLayer.clearLayers();

  try {{
    const resp = await fetch(`${{API}}?region=${{encodeURIComponent(regionKey)}}`);
    const d    = await resp.json();
    if (d.error) {{ content.innerHTML = `<div class="err">Fehler: ${{d.error}}</div>`; return; }}
    render(d);
  }} catch (e) {{
    content.innerHTML = `<div class="err">Ladefehler: ${{e}}</div>`;
  }}
}}

function render(d) {{
  const content = document.getElementById('content');
  const sar = d.sar || {{}};
  const ais = d.ais || {{}};

  // Karte zentrieren
  if (d.lat && d.lon) map.setView([d.lat, d.lon], 7);

  // SAR-Marker (Bounding-Box / Region-Mittelpunkt + erkannte Ziele)
  (sar.ships || []).forEach(s => {{
    const m = L.circleMarker([s.lat, s.lon], {{
      radius: 5 + (s.confidence||0)*6,
      color: confColor(s.confidence||0),
      fillColor: confColor(s.confidence||0),
      fillOpacity: 0.6, weight: 2
    }}).bindPopup(
      `<b>🛰 SAR-Ziel</b><br>Konfidenz: ${{Math.round((s.confidence||0)*100)}}%<br>` +
      `Größe: ~${{s.size_px||'?'}}px<br>Kategorie: ${{s.category||'unbekannt'}}`
    );
    markersLayer.addLayer(m);
  }});

  // AIS-Marker
  (ais.vessels || []).slice(0, 80).forEach(v => {{
    if (!v.lat || !v.lon) return;
    const m = L.circleMarker([v.lat, v.lon], {{
      radius: 4, color:'#00ff88', fillColor:'#00ff88', fillOpacity:0.5, weight:1
    }}).bindPopup(
      `<b>📡 ${{v.name||v.mmsi||'Schiff'}}</b><br>Typ: ${{v.type||'?'}}<br>` +
      `Geschwindigkeit: ${{v.speed!==undefined ? v.speed+' kn' : '?'}}<br>` +
      `Kurs: ${{v.course!==undefined ? v.course+'°' : '?'}}`
    );
    markersLayer.addLayer(m);
  }});

  // Region-Mittelpunkt-Marker
  if (d.lat && d.lon) {{
    L.marker([d.lat, d.lon], {{opacity:0.6}})
      .bindPopup(`<b>${{d.region}}</b><br>${{d.desc||''}}`)
      .addTo(markersLayer);
  }}

  // ── Sidebar-Inhalt ────────────────────────────────────────────────
  let html = '';

  // Übersicht
  html += `<div class="box"><h3>📍 ${{d.region}}</h3>
    <div class="row"><span class="k">Beschreibung</span></div>
    <div class="hint">${{d.desc || '–'}}</div>
    <div class="row"><span class="k">Alarm-Schiffsmeldungen</span>
      <span class="v">${{d.alert_count!==undefined ? d.alert_count : '?'}}</span></div>
  </div>`;

  // AIS-Box
  html += `<div class="box"><h3>📡 AIS-Transponderdaten</h3>
    <div class="row"><span class="k">Erkannte Schiffe</span><span class="v">${{ais.vessel_count ?? 0}}</span></div>
    <div class="row"><span class="k">Quelle</span><span class="v">${{ais.source || '–'}}</span></div>
    <div class="hint">${{ais.has_key ? 'Live-Transponderdaten aktiv.' :
      'Kein AIS-Key konfiguriert – evtl. eingeschränkte Abdeckung. AIS zeigt nur Schiffe, die ihren Transponder aktiv haben lassen ("dunkle" Schiffe fehlen hier – siehe SAR unten).'}}</div>
  </div>`;

  // SAR-Box mit EO-Browser-Link (Kernstück: "Bilder selbst herunterladen/auswerten")
  const method = sar.method === 'sentinel-hub' ? 'Sentinel Hub (Bildanalyse aktiv)'
               : sar.method === 'asf-fallback'  ? 'ASF-Metadaten (nur Szenen-Suche, kein Bild-Download via API)'
               : '–';
  html += `<div class="box"><h3>🛰 SAR-Satellitenanalyse (Sentinel-1)</h3>
    <div class="row"><span class="k">Letzte Szene</span><span class="v">${{sar.scene_date || '–'}}</span></div>
    <div class="row"><span class="k">Automatisch erkannte Ziele</span><span class="v">${{sar.ship_count ?? 0}}</span></div>
    <div class="row"><span class="k">Anomalie-Score</span>
      <span class="badge ${{(sar.anomaly_score||0) > 0.7 ? 'b-crit' : (sar.anomaly_score||0) > 0.4 ? 'b-warn' : 'b-ok'}}">
        ${{Math.round((sar.anomaly_score||0)*100)}}%</span></div>
    <div class="row"><span class="k">Methode</span></div>
    <div class="hint">${{method}}</div>
    <div class="hint">${{sar.description || ''}}</div>

    <a class="eo-link" href="${{sar.eo_link || '#'}}" target="_blank" rel="noopener">
      🔭 Satellitenbild im EO-Browser öffnen &amp; herunterladen
    </a>
    <div class="hint">
      Direkter Link zu Sentinel-1 SAR-Aufnahmen (kein Account nötig). Funktioniert
      bei Nacht und durch Wolken hindurch — ideal, um "dunkle" Schiffe ohne
      Transponder zu finden, die unser automatischer Filter eventuell übersehen hat.
      Mit dem menschlichen Auge prüfen: helle Punkte/Streifen im SAR-Bild = mögliche
      Schiffe, Form &amp; Größe von Hand bewerten.
    </div>
  </div>`;

  // Erkannte Ziele Liste
  if ((sar.ships || []).length) {{
    html += `<div class="box"><h3>🎯 Automatisch erkannte SAR-Ziele</h3>`;
    sar.ships.slice(0, 15).forEach(s => {{
      const c = Math.round((s.confidence||0)*100);
      html += `<div class="ship-item">
        <div class="row"><span class="k">${{s.category||'Ziel'}} · ${{s.size_px||'?'}}px</span>
          <span class="v">${{c}}%</span></div>
        <div class="conf-bar"><div class="conf-fill" style="width:${{c}}%;background:${{confColor(s.confidence||0)}}"></div></div>
      </div>`;
    }});
    html += `</div>`;
  }}

  // Filter-Hinweis (clientseitige Konfidenz-Schwelle für die Kartenansicht)
  html += `<div class="box" id="filters"><h3>🔧 Filter (Kartenansicht)</h3>
    <label>Min. SAR-Konfidenz <span id="conf-val">0%</span>
      <input type="range" id="conf-slider" min="0" max="100" value="0"
             oninput="applyFilter(this.value)">
    </label>
    <div class="hint">Reduziert die Marker auf der Karte nach Erkennungs-Konfidenz —
      hilfreich, um bei Bedarf nur die wahrscheinlichsten SAR-Treffer zu sehen,
      oder die Schwelle ganz herunterzufahren, um auch unsichere/schwache Signale
      selbst zu prüfen (das menschliche Auge sieht oft mehr als der Algorithmus).</div>
  </div>`;

  content.className = '';
  content.innerHTML = html;
  window._lastData = d;
}}

function applyFilter(minConfPct) {{
  document.getElementById('conf-val').textContent = minConfPct + '%';
  const d = window._lastData;
  if (!d) return;
  const minConf = minConfPct / 100;
  markersLayer.clearLayers();

  ((d.sar||{{}}).ships || []).forEach(s => {{
    if ((s.confidence||0) < minConf) return;
    const m = L.circleMarker([s.lat, s.lon], {{
      radius: 5 + (s.confidence||0)*6,
      color: confColor(s.confidence||0), fillColor: confColor(s.confidence||0),
      fillOpacity: 0.6, weight: 2
    }}).bindPopup(`<b>🛰 SAR-Ziel</b><br>Konfidenz: ${{Math.round((s.confidence||0)*100)}}%`);
    markersLayer.addLayer(m);
  }});
  ((d.ais||{{}}).vessels || []).slice(0, 80).forEach(v => {{
    if (!v.lat || !v.lon) return;
    const m = L.circleMarker([v.lat, v.lon], {{
      radius: 4, color:'#00ff88', fillColor:'#00ff88', fillOpacity:0.5, weight:1
    }}).bindPopup(`<b>📡 ${{v.name||v.mmsi||'Schiff'}}</b>`);
    markersLayer.addLayer(m);
  }});
  if (d.lat && d.lon) {{
    L.marker([d.lat, d.lon], {{opacity:0.6}}).bindPopup(`<b>${{d.region}}</b>`).addTo(markersLayer);
  }}
}}

initMap();
buildRegionBar();
// Standardregion automatisch laden (Hormuz – das Beispiel des Nutzers)
loadRegion("Hormuz-Strasse", document.querySelector('.reg-btn'));
</script>
</body>
</html>"""
