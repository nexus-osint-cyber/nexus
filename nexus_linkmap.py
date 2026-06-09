"""
NEXUS – Link-Analyse UI  (Maltego-Stil)
=======================================
Generiert die HTML-Seite /linkmap mit vis.js Netzwerk-Visualisierung.

Features:
  • Interaktives Netzwerk-Graph (drag, zoom, click)
  • Entitäten-Sidebar mit Suchfeld + Typ-Filter
  • Klick auf Knoten → Detail-Panel (Pattern-of-Life, Timeline)
  • Community-Highlighting per Farbe
  • Entität expandieren (Tiefe 1/2)
  • Export als PNG
  • Mobile-responsive (Bottom-Sheet für Details)
  • Vollständig integriert in NEXUS Dark-Theme

Öffentliche API:
  build_linkmap_html(port)   → str  (vollständige HTML-Seite)
"""

from __future__ import annotations

import json


def build_linkmap_html(port: int = 11430) -> str:
    """Generiert die vollständige Link-Analyse HTML-Seite."""
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEXUS · Link-Analyse</title>

<!-- vis.js Network -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css">

<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
:root {{
  --bg:      #0f172a;
  --surface: #1e293b;
  --border:  #334155;
  --text:    #e2e8f0;
  --muted:   #64748b;
  --accent:  #3b82f6;
  --green:   #4ade80;
  --yellow:  #facc15;
  --red:     #f87171;
}}
body {{ background:var(--bg); color:var(--text); font:13px/1.5 'Segoe UI',system-ui,sans-serif; overflow:hidden; }}

/* Layout */
#app {{ display:grid; grid-template-columns:280px 1fr; grid-template-rows:48px 1fr; height:100vh; }}

/* Header */
#header {{ grid-column:1/-1; background:var(--surface); border-bottom:1px solid var(--border);
           display:flex; align-items:center; gap:12px; padding:0 16px; }}
#header h1 {{ font-size:14px; font-weight:700; color:var(--accent); }}
#header .subtitle {{ font-size:11px; color:var(--muted); }}
#header .spacer {{ flex:1; }}
#btn-layout, #btn-export, #btn-reset {{
  background:rgba(255,255,255,.06); border:1px solid var(--border);
  color:var(--text); padding:5px 10px; border-radius:5px; cursor:pointer;
  font-size:11px; transition:background .15s;
}}
#btn-layout:hover, #btn-export:hover, #btn-reset:hover {{ background:rgba(255,255,255,.12); }}
#stats-bar {{ font-size:11px; color:var(--muted); }}
#stats-bar span {{ color:var(--text); font-weight:600; }}

/* Sidebar */
#sidebar {{ background:var(--surface); border-right:1px solid var(--border);
            display:flex; flex-direction:column; overflow:hidden; }}

#search-box {{ padding:10px; border-bottom:1px solid var(--border); }}
#search-input {{
  width:100%; background:rgba(255,255,255,.05); border:1px solid var(--border);
  color:var(--text); padding:7px 10px; border-radius:6px; font-size:12px;
  outline:none;
}}
#search-input:focus {{ border-color:var(--accent); }}
#search-input::placeholder {{ color:var(--muted); }}

/* Typ-Filter */
#type-filter {{ padding:8px 10px; border-bottom:1px solid var(--border);
               display:flex; gap:4px; flex-wrap:wrap; }}
.tf-btn {{
  font-size:10px; padding:2px 7px; border-radius:10px; cursor:pointer;
  border:1px solid var(--border); background:transparent; color:var(--muted);
  transition:all .15s;
}}
.tf-btn.active {{ border-color:var(--accent); color:var(--text); background:rgba(59,130,246,.15); }}

/* Entitätsliste */
#entity-list {{ flex:1; overflow-y:auto; padding:6px; }}
.ent-item {{
  padding:8px 10px; border-radius:6px; cursor:pointer; margin-bottom:2px;
  display:flex; align-items:center; gap:8px; transition:background .1s;
  border:1px solid transparent;
}}
.ent-item:hover {{ background:rgba(255,255,255,.06); }}
.ent-item.selected {{ background:rgba(59,130,246,.15); border-color:var(--accent); }}
.ent-icon {{ font-size:15px; flex-shrink:0; }}
.ent-info {{ overflow:hidden; }}
.ent-name {{ font-size:12px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.ent-meta {{ font-size:10px; color:var(--muted); }}
.ent-badge {{ margin-left:auto; background:rgba(59,130,246,.25); color:#93c5fd;
              font-size:10px; padding:2px 6px; border-radius:8px; flex-shrink:0; }}

/* Network Canvas */
#network-wrap {{ position:relative; overflow:hidden; }}
#network-canvas {{ width:100%; height:100%; }}
#loading-overlay {{
  position:absolute; inset:0; background:rgba(15,23,42,.85);
  display:flex; align-items:center; justify-content:center;
  flex-direction:column; gap:12px; z-index:10;
}}
.spinner {{ width:32px; height:32px; border:3px solid var(--border);
            border-top-color:var(--accent); border-radius:50%;
            animation:spin .8s linear infinite; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}
#loading-text {{ font-size:12px; color:var(--muted); }}

/* Detail-Panel (rechts) */
#detail-panel {{
  position:absolute; right:0; top:0; bottom:0; width:320px;
  background:var(--surface); border-left:1px solid var(--border);
  transform:translateX(100%); transition:transform .25s ease;
  overflow-y:auto; z-index:20; display:flex; flex-direction:column;
}}
#detail-panel.open {{ transform:translateX(0); }}
#detail-header {{ padding:12px 14px; border-bottom:1px solid var(--border);
                 display:flex; align-items:center; gap:8px; }}
#detail-icon {{ font-size:22px; }}
#detail-name {{ font-size:14px; font-weight:700; }}
#detail-type {{ font-size:11px; color:var(--muted); }}
#detail-close {{ margin-left:auto; cursor:pointer; color:var(--muted); font-size:18px;
                 background:none; border:none; }}
#detail-close:hover {{ color:var(--text); }}
#detail-body {{ padding:12px 14px; flex:1; }}

/* Detail-Sektionen */
.d-section {{ margin-bottom:14px; }}
.d-title {{ font-size:10px; font-weight:700; text-transform:uppercase;
            letter-spacing:.05em; color:var(--muted); margin-bottom:6px; }}
.d-stat {{ display:flex; justify-content:space-between; font-size:12px;
           padding:3px 0; border-bottom:1px solid rgba(255,255,255,.04); }}
.d-stat .val {{ color:var(--accent); font-weight:600; }}
.d-badge {{ display:inline-block; font-size:11px; padding:2px 8px; border-radius:10px;
            background:rgba(59,130,246,.15); color:#93c5fd; margin:2px; }}

/* Timeline */
.t-item {{ padding:6px 0; border-bottom:1px solid rgba(255,255,255,.04); }}
.t-source {{ font-size:10px; color:var(--accent); font-weight:600; }}
.t-ctx {{ font-size:11px; color:var(--muted); margin-top:2px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.t-time {{ font-size:10px; color:var(--muted); }}

/* Stunden-Chart */
#hour-chart {{ display:flex; align-items:flex-end; gap:1px; height:40px; margin-top:4px; }}
.h-bar {{ flex:1; background:var(--accent); border-radius:1px 1px 0 0;
          opacity:.7; transition:opacity .2s; min-height:2px; }}
.h-bar:hover {{ opacity:1; }}

/* Action-Buttons im Detail */
.d-btn {{
  display:block; width:100%; padding:7px; border-radius:6px;
  background:rgba(59,130,246,.15); border:1px solid rgba(59,130,246,.3);
  color:var(--accent); cursor:pointer; font-size:12px; text-align:center;
  margin-bottom:6px; transition:background .15s;
}}
.d-btn:hover {{ background:rgba(59,130,246,.3); }}

/* Tooltip */
#graph-tooltip {{
  position:absolute; background:#1e293b; border:1px solid #334155;
  border-radius:6px; padding:6px 10px; font-size:11px; pointer-events:none;
  opacity:0; transition:opacity .15s; z-index:30; max-width:200px;
}}

/* Mobile */
@media (max-width:768px) {{
  #app {{ grid-template-columns:1fr; grid-template-rows:48px 1fr; }}
  #sidebar {{ display:none; }}
  #detail-panel {{ width:100%; }}
}}
</style>
</head>
<body>

<div id="app">

  <!-- Header -->
  <div id="header">
    <div>
      <h1>🕸️ NEXUS · Link-Analyse</h1>
      <div class="subtitle">Maltego-Style Akteur-Netzwerk</div>
    </div>
    <div class="spacer"></div>
    <div id="stats-bar">
      <span id="stat-nodes">–</span> Knoten ·
      <span id="stat-edges">–</span> Kanten ·
      <span id="stat-comms">–</span> Communities
    </div>
    <button id="btn-layout" onclick="cycleLayout()">⚡ Layout</button>
    <button id="btn-export" onclick="exportPNG()">💾 PNG</button>
    <button id="btn-reset" onclick="resetView()">🔄 Reset</button>
  </div>

  <!-- Sidebar -->
  <div id="sidebar">
    <div id="search-box">
      <input type="text" id="search-input" placeholder="🔍 Entität suchen…" oninput="onSearch(this.value)">
    </div>
    <div id="type-filter">
      <button class="tf-btn active" data-type="ALL" onclick="filterType('ALL',this)">Alle</button>
      <button class="tf-btn" data-type="PERSON" onclick="filterType('PERSON',this)">👤 Person</button>
      <button class="tf-btn" data-type="ORGANIZATION" onclick="filterType('ORGANIZATION',this)">🏛️ Org</button>
      <button class="tf-btn" data-type="LOCATION" onclick="filterType('LOCATION',this)">📍 Ort</button>
      <button class="tf-btn" data-type="VEHICLE" onclick="filterType('VEHICLE',this)">🚗 Fahr.</button>
      <button class="tf-btn" data-type="AIRCRAFT" onclick="filterType('AIRCRAFT',this)">✈️ Flug</button>
      <button class="tf-btn" data-type="VESSEL" onclick="filterType('VESSEL',this)">⚓ Schiff</button>
    </div>
    <div id="entity-list"></div>
  </div>

  <!-- Network Canvas -->
  <div id="network-wrap">
    <div id="network-canvas"></div>
    <div id="loading-overlay">
      <div class="spinner"></div>
      <div id="loading-text">Lade Netzwerk…</div>
    </div>
    <div id="graph-tooltip"></div>
  </div>

</div>

<!-- Detail-Panel (außerhalb grid) -->
<div id="detail-panel">
  <div id="detail-header">
    <div id="detail-icon">❓</div>
    <div>
      <div id="detail-name">–</div>
      <div id="detail-type">–</div>
    </div>
    <button id="detail-close" onclick="closeDetail()">✕</button>
  </div>
  <div id="detail-body">
    <div id="detail-content">
      <div style="color:var(--muted);font-size:12px;text-align:center;padding:20px">
        Entität anklicken für Details
      </div>
    </div>
  </div>
</div>

<script>
const BASE  = window.location.origin;
const PORT  = {port};
let network = null;
let allEntities = [];
let currentFilter = 'ALL';
let currentSearch = '';
let currentFocus  = null;
let layoutMode = 0;

const LAYOUT_MODES = [
  {{ physics: {{ enabled:true, solver:'forceAtlas2Based', forceAtlas2Based:{{ gravitationalConstant:-80, centralGravity:0.01, springLength:140, springConstant:0.08, damping:0.5 }} }} }},
  {{ physics: {{ enabled:true, solver:'repulsion',         repulsion:{{ centralGravity:0.3, springLength:200 }} }} }},
  {{ physics: {{ enabled:false }} }},
];

// ── Netzwerk laden ────────────────────────────────────────────────────────────
async function loadGraph(focusId = null) {{
  document.getElementById('loading-overlay').style.display = 'flex';
  document.getElementById('loading-text').textContent = 'Lade Graphdaten…';
  try {{
    const url = focusId
      ? `${{BASE}}/api/graph?focus=${{encodeURIComponent(focusId)}}&min_strength=0.15&max_nodes=80`
      : `${{BASE}}/api/graph?min_strength=0.2&max_nodes=120`;

    const resp = await fetch(url, {{credentials:'same-origin'}});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();

    document.getElementById('loading-text').textContent = 'Zeichne Graph…';
    renderNetwork(data);

    document.getElementById('stat-nodes').textContent = data.meta?.node_count ?? data.nodes?.length ?? '?';
    document.getElementById('stat-edges').textContent = data.meta?.edge_count ?? data.edges?.length ?? '?';
    document.getElementById('stat-comms').textContent = data.meta?.community_count ?? '?';
  }} catch(e) {{
    document.getElementById('loading-text').textContent = '⚠️ ' + e.message;
    setTimeout(() => document.getElementById('loading-overlay').style.display='none', 3000);
  }}
}}

// ── vis.js Netzwerk ───────────────────────────────────────────────────────────
function renderNetwork(data) {{
  const container = document.getElementById('network-canvas');
  const ds_nodes  = new vis.DataSet(data.nodes);
  const ds_edges  = new vis.DataSet(data.edges);

  const options = {{
    ...LAYOUT_MODES[layoutMode],
    nodes: {{
      shape:'dot', borderWidth:2, shadow:true,
      font: {{ color:'#e2e8f0', size:11, face:'Segoe UI' }},
    }},
    edges: {{
      smooth:{{type:'continuous', roundness:0.2}},
      color: {{ color:'#334155', highlight:'#60a5fa', hover:'#64748b' }},
      font:  {{ color:'#64748b', size:9, align:'middle' }},
      arrows: {{ to:{{ enabled:false }} }},
      width: 1.5,
    }},
    interaction: {{
      hover:true, tooltipDelay:200,
      navigationButtons:false, keyboard:false,
    }},
    height: '100%', width: '100%',
  }};

  if (network) network.destroy();
  network = new vis.Network(container, {{nodes:ds_nodes, edges:ds_edges}}, options);

  network.on('click', params => {{
    if (params.nodes.length > 0) loadEntityDetail(params.nodes[0]);
    else closeDetail();
  }});

  network.on('hoverNode', params => showTooltip(params));
  network.on('blurNode',  ()     => hideTooltip());

  network.on('stabilizationIterationsDone', () => {{
    document.getElementById('loading-overlay').style.display = 'none';
    network.setOptions({{physics:{{enabled:false}}}});
  }});
}}

// ── Tooltip ───────────────────────────────────────────────────────────────────
function showTooltip(params) {{
  const tooltip = document.getElementById('graph-tooltip');
  tooltip.textContent = params.node ?? '';
  tooltip.style.left  = (params.event.center?.x ?? 0) + 12 + 'px';
  tooltip.style.top   = (params.event.center?.y ?? 0) + 12 + 'px';
  tooltip.style.opacity = '1';
}}
function hideTooltip() {{
  document.getElementById('graph-tooltip').style.opacity = '0';
}}

// ── Entitäten-Sidebar ─────────────────────────────────────────────────────────
async function loadEntities() {{
  try {{
    const r = await fetch(`${{BASE}}/api/entities?limit=200`, {{credentials:'same-origin'}});
    allEntities = await r.json();
    renderEntityList();
  }} catch(e) {{
    document.getElementById('entity-list').innerHTML =
      '<div style="padding:10px;color:var(--muted);font-size:11px">Fehler beim Laden</div>';
  }}
}}

function renderEntityList() {{
  const list = document.getElementById('entity-list');
  let filtered = allEntities;
  if (currentFilter !== 'ALL') filtered = filtered.filter(e => e.type === currentFilter);
  if (currentSearch) {{
    const q = currentSearch.toLowerCase();
    filtered = filtered.filter(e => e.name.toLowerCase().includes(q));
  }}

  if (filtered.length === 0) {{
    list.innerHTML = '<div style="padding:10px;color:var(--muted);font-size:11px;text-align:center">Keine Entitäten gefunden</div>';
    return;
  }}

  list.innerHTML = filtered.slice(0, 80).map(e => `
    <div class="ent-item" id="ent-${{e.id}}" onclick="selectEntity('${{e.id}}')">
      <span class="ent-icon">${{e.icon}}</span>
      <div class="ent-info">
        <div class="ent-name">${{escHtml(e.name)}}</div>
        <div class="ent-meta">${{e.type}} · ${{(e.last_seen||'').slice(0,10)}}</div>
      </div>
      <span class="ent-badge">${{e.mentions}}</span>
    </div>
  `).join('');
}}

function filterType(type, btn) {{
  currentFilter = type;
  document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderEntityList();
}}

function onSearch(val) {{
  currentSearch = val;
  renderEntityList();
}}

function selectEntity(id) {{
  document.querySelectorAll('.ent-item').forEach(el => el.classList.remove('selected'));
  const el = document.getElementById('ent-' + id);
  if (el) el.classList.add('selected');

  // Im Graph fokussieren
  if (network) network.focus(id, {{scale:1.2, animation:true}});
  loadEntityDetail(id);
}}

// ── Entität-Detail ─────────────────────────────────────────────────────────────
async function loadEntityDetail(entityId) {{
  document.getElementById('detail-panel').classList.add('open');
  document.getElementById('detail-name').textContent = '…';
  document.getElementById('detail-type').textContent = '';
  document.getElementById('detail-content').innerHTML =
    '<div style="padding:20px;text-align:center"><div class="spinner"></div></div>';

  try {{
    const r = await fetch(`${{BASE}}/api/entities/${{entityId}}`, {{credentials:'same-origin'}});
    const data = await r.json();
    renderDetail(data);
  }} catch(e) {{
    document.getElementById('detail-content').innerHTML =
      `<div style="padding:14px;color:var(--red)">Fehler: ${{e.message}}</div>`;
  }}
}}

function renderDetail(data) {{
  if (data.error) {{
    document.getElementById('detail-content').innerHTML =
      `<div style="padding:14px;color:var(--red)">${{data.error}}</div>`;
    return;
  }}

  const e   = data.entity;
  const pol = data.pattern_of_life || {{}};

  document.getElementById('detail-icon').textContent = e.icon || '❓';
  document.getElementById('detail-name').textContent = e.name;
  document.getElementById('detail-type').textContent = e.type;

  // Stunden-Chart für Pattern-of-Life
  const hours = pol.activity_by_hour || new Array(24).fill(0);
  const maxH  = Math.max(...hours, 1);
  const hourBars = hours.map((h,i) => {{
    const pct = Math.round((h/maxH)*100);
    return `<div class="h-bar" style="height:${{Math.max(2,pct)}}%" title="${{i}}:00 UTC – ${{h}}×"></div>`;
  }}).join('');

  // Verbindungen
  const connHtml = (data.connections||[]).map(c =>
    `<div class="t-item">
       <span class="t-source">${{c.target}}</span>
       <span style="color:var(--muted);font-size:10px"> · ${{c.hops}} Hop(s): ${{c.path.join(' → ')}}</span>
     </div>`
  ).join('') || '<div style="color:var(--muted);font-size:11px">Keine Verbindungen</div>';

  // Häufige Begleiter
  const assocHtml = (pol.frequent_associates||[]).map(a =>
    `<span class="d-badge" onclick="selectEntity('${{a.id}}')" style="cursor:pointer">${{escHtml(a.name)}} (${{a.co_sightings}}×)</span>`
  ).join('') || '–';

  // Timeline
  const timelineHtml = (data.recent_sightings||[]).slice(0,8).map(s => `
    <div class="t-item">
      <div class="t-source">${{escHtml(s.source||'?')}}</div>
      <div class="t-ctx">${{escHtml((s.context||'').slice(0,90))}}</div>
      <div class="t-time">${{(s.ts||'').slice(0,16).replace('T',' ')}} UTC
        ${{s.location ? '· ' + escHtml(s.location) : ''}}</div>
    </div>
  `).join('') || '<div style="color:var(--muted);font-size:11px">Keine Sichtungen</div>';

  document.getElementById('detail-content').innerHTML = `
    <!-- Actions -->
    <div class="d-section">
      <button class="d-btn" onclick="expandInGraph('${{e.id}}')">⊕ Im Graph expandieren</button>
      <button class="d-btn" onclick="focusInGraph('${{e.id}}')">🎯 Auf Entität zentrieren</button>
    </div>

    <!-- Stats -->
    <div class="d-section">
      <div class="d-title">Statistiken</div>
      <div class="d-stat"><span>Sichtungen gesamt</span><span class="val">${{e.mentions}}</span></div>
      <div class="d-stat"><span>Konfidenz</span><span class="val">${{Math.round((e.confidence||0)*100)}}%</span></div>
      <div class="d-stat"><span>Erste Sichtung</span><span class="val">${{(e.first_seen||'–').slice(0,10)}}</span></div>
      <div class="d-stat"><span>Letzte Sichtung</span><span class="val">${{(e.last_seen||'–').slice(0,10)}}</span></div>
      ${{(e.aliases||[]).length > 0 ? `<div class="d-stat"><span>Aliase</span><span class="val">${{e.aliases.join(', ')}}</span></div>` : ''}}
    </div>

    <!-- Pattern-of-Life -->
    ${{pol.total_sightings ? `
    <div class="d-section">
      <div class="d-title">Pattern of Life</div>
      <div class="d-stat"><span>Peak-Zeit</span><span class="val">${{pol.peak_hour_utc}}:00 UTC · ${{pol.peak_weekday||'?'}}</span></div>
      <div class="d-stat"><span>Hauptquellen</span><span class="val">${{(pol.top_sources||[]).slice(0,2).map(s=>s[0]).join(', ')||'–'}}</span></div>
      <div class="d-stat"><span>Hauptorte</span><span class="val">${{(pol.top_locations||[]).slice(0,2).map(l=>l[0]).join(', ')||'–'}}</span></div>
      <div class="d-title" style="margin-top:8px">Aktivität nach Stunde (UTC)</div>
      <div id="hour-chart">${{hourBars}}</div>
    </div>` : ''}}

    <!-- Verbindungen -->
    <div class="d-section">
      <div class="d-title">Kürzeste Verbindungen</div>
      ${{connHtml}}
    </div>

    <!-- Häufige Begleiter -->
    <div class="d-section">
      <div class="d-title">Häufig gemeinsam erwähnt</div>
      ${{assocHtml}}
    </div>

    <!-- Timeline -->
    <div class="d-section">
      <div class="d-title">Letzte Sichtungen (30 Tage)</div>
      ${{timelineHtml}}
    </div>
  `;
}}

function closeDetail() {{
  document.getElementById('detail-panel').classList.remove('open');
}}

async function expandInGraph(entityId) {{
  currentFocus = entityId;
  await loadGraph(entityId);
}}

function focusInGraph(entityId) {{
  if (network) network.focus(entityId, {{scale:1.5, animation:true}});
}}

// ── Layout-Zyklen ─────────────────────────────────────────────────────────────
function cycleLayout() {{
  layoutMode = (layoutMode + 1) % LAYOUT_MODES.length;
  if (!network) return;
  const labels = ['ForceAtlas2', 'Repulsion', 'Statisch'];
  network.setOptions(LAYOUT_MODES[layoutMode]);
  if (layoutMode !== 2) {{
    setTimeout(() => network?.setOptions({{physics:{{enabled:false}}}}), 4000);
  }}
  document.getElementById('btn-layout').textContent = '⚡ ' + labels[layoutMode];
}}

// ── Export ────────────────────────────────────────────────────────────────────
function exportPNG() {{
  if (!network) return;
  const canvas = document.querySelector('#network-canvas canvas');
  if (!canvas) return;
  const a = document.createElement('a');
  a.href = canvas.toDataURL('image/png');
  a.download = 'nexus_linkmap_' + new Date().toISOString().slice(0,10) + '.png';
  a.click();
}}

function resetView() {{
  currentFocus = null;
  loadGraph(null);
  closeDetail();
}}

// ── Hilfsfunktionen ───────────────────────────────────────────────────────────
function escHtml(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('load', async () => {{
  await loadEntities();
  await loadGraph();
}});
</script>
</body>
</html>"""
