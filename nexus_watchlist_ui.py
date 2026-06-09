"""
NEXUS – Visueller Watchlist-Manager (T93)
==========================================
Web-UI zur Verwaltung der NEXUS-Watchlist ohne config.py zu bearbeiten.
Ermöglicht: Einträge hinzufügen, löschen, aktivieren/deaktivieren, sortieren.

Öffentliche API:
  build_watchlist_ui_html(port) -> str   HTML für /watchlist Endpoint

Der Manager kommuniziert mit dem Live-Server über:
  GET  /api/watchlist       → JSON-Liste aller Einträge
  POST /api/watchlist/add   → Eintrag hinzufügen
  POST /api/watchlist/del   → Eintrag löschen
  POST /api/watchlist/toggle → Eintrag aktivieren/deaktivieren
"""

from __future__ import annotations


def build_watchlist_ui_html(port: int = 11430) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS WATCHLIST</title>
<style>
:root {{
  --bg:#060a10; --surface:rgba(10,18,28,0.9); --border:#1e3a4a;
  --accent:#00d4ff; --green:#00ff88; --red:#ff4444; --orange:#ff8800;
  --text:#c8d6e0; --muted:#4a6070; --font:'Courier New',monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);font-size:12px;
  background-image:radial-gradient(ellipse at 15% 30%,rgba(0,40,80,0.35) 0%,transparent 60%);
  min-height:100vh}}
a{{color:var(--accent);text-decoration:none}}

/* Header */
#header{{background:linear-gradient(90deg,#040d18,#07152a,#040d18);
  border-bottom:2px solid var(--accent);padding:8px 16px;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  box-shadow:0 2px 20px rgba(0,212,255,0.1);position:sticky;top:0;z-index:100}}
.logo{{color:var(--accent);font-size:14px;font-weight:bold;letter-spacing:4px;
  text-shadow:0 0 15px rgba(0,212,255,0.4)}}
.hdr-btn{{background:#071a2e;border:1px solid #1e3a4a;color:var(--accent);
  padding:3px 10px;cursor:pointer;font-family:inherit;font-size:10px;border-radius:3px}}
.hdr-btn:hover{{border-color:var(--accent)}}

/* Content */
#content{{max-width:800px;margin:24px auto;padding:0 16px}}

/* Add form */
.add-card{{background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:16px;margin-bottom:20px;
  backdrop-filter:blur(8px);box-shadow:0 0 20px rgba(0,212,255,0.04)}}
.add-card h2{{color:var(--accent);font-size:11px;letter-spacing:3px;
  text-transform:uppercase;margin-bottom:12px;
  text-shadow:0 0 12px rgba(0,212,255,0.3)}}
.form-row{{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}}
.form-grp{{display:flex;flex-direction:column;gap:4px;flex:1;min-width:140px}}
.form-grp label{{font-size:9px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}}
.form-input{{background:#060b10;border:1px solid #1e3a4a;color:var(--text);
  padding:5px 8px;font-family:inherit;font-size:11px;border-radius:3px;width:100%}}
.form-input:focus{{outline:none;border-color:var(--accent)}}
.form-select{{background:#060b10;border:1px solid #1e3a4a;color:var(--text);
  padding:5px 8px;font-family:inherit;font-size:11px;border-radius:3px}}
.btn-add{{background:#003050;border:1px solid var(--accent);color:var(--accent);
  padding:6px 16px;cursor:pointer;font-family:inherit;font-size:11px;border-radius:3px;
  transition:all 0.2s;white-space:nowrap;align-self:flex-end}}
.btn-add:hover{{background:#004a70;box-shadow:0 0 12px rgba(0,212,255,0.2)}}

/* Stats bar */
#stats-bar{{display:flex;gap:16px;margin-bottom:14px;font-size:10px;
  background:rgba(0,8,20,0.5);border:1px solid #0d2035;padding:8px 12px;border-radius:4px}}
.stat{{display:flex;flex-direction:column;align-items:center;gap:2px}}
.stat-val{{font-size:18px;font-weight:bold;color:var(--accent)}}
.stat-lbl{{color:var(--muted)}}

/* Watchlist table */
.wl-header{{display:grid;grid-template-columns:1fr 80px 90px 70px 120px;
  gap:8px;padding:4px 10px;font-size:9px;color:var(--muted);
  letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #0d2035}}
.wl-item{{display:grid;grid-template-columns:1fr 80px 90px 70px 120px;
  gap:8px;padding:8px 10px;
  background:var(--surface);border:1px solid rgba(30,58,74,0.6);
  border-radius:4px;margin-bottom:4px;align-items:center;
  backdrop-filter:blur(4px);transition:all 0.2s}}
.wl-item:hover{{border-color:rgba(0,212,255,0.3);
  box-shadow:0 2px 12px rgba(0,212,255,0.06)}}
.wl-item.disabled{{opacity:0.45}}
.wl-term{{font-size:11px;font-weight:bold;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
.wl-type{{font-size:9px;padding:2px 6px;border-radius:2px;text-align:center;
  background:rgba(0,212,255,0.1);color:var(--accent);font-weight:bold;
  letter-spacing:1px;align-self:center;white-space:nowrap}}
.wl-type.geo{{background:rgba(0,255,136,0.1);color:var(--green)}}
.wl-type.keyword{{background:rgba(255,136,0,0.1);color:var(--orange)}}
.wl-priority{{text-align:center;font-size:10px}}
.prio-1{{color:var(--red)}} .prio-2{{color:var(--orange)}} .prio-3{{color:#ffcc00}} .prio-4{{color:var(--muted)}}
.wl-actions{{display:flex;gap:4px;justify-content:flex-end}}
.btn-sm{{background:#071a2e;border:1px solid #1e3a4a;color:#8ab0c8;
  padding:2px 7px;cursor:pointer;font-family:inherit;font-size:10px;
  border-radius:2px;transition:all 0.15s}}
.btn-sm:hover{{border-color:var(--accent);color:var(--accent)}}
.btn-sm.del:hover{{border-color:var(--red);color:var(--red)}}
.btn-sm.on{{background:#003050;border-color:var(--accent);color:var(--accent)}}

/* Feedback toast */
#toast{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  background:#071a2e;border:1px solid var(--accent);color:var(--accent);
  padding:8px 20px;border-radius:4px;font-size:11px;opacity:0;
  transition:opacity 0.3s;pointer-events:none;z-index:200}}
#toast.show{{opacity:1}}

/* Empty state */
#empty{{text-align:center;padding:40px;color:var(--muted)}}

/* Scrollbar */
::-webkit-scrollbar{{width:4px}}
::-webkit-scrollbar-track{{background:#04111e}}
::-webkit-scrollbar-thumb{{background:#1e3a4a}}
</style>
</head>
<body>

<div id="header">
  <span class="logo">◈ NEXUS</span>
  <span style="color:#4a6070;font-size:11px;letter-spacing:2px">WATCHLIST</span>
  <button class="hdr-btn" onclick="loadWatchlist()">↻ Aktualisieren</button>
  <button class="hdr-btn" onclick="window.location=location.origin+'/livemap'">⬅ Karte</button>
  <button class="hdr-btn" onclick="exportWatchlist()">⬇ Export</button>
</div>

<div id="content">

  <!-- Stats -->
  <div id="stats-bar">
    <div class="stat"><span class="stat-val" id="s-total">0</span><span class="stat-lbl">Gesamt</span></div>
    <div class="stat"><span class="stat-val" id="s-active" style="color:var(--green)">0</span><span class="stat-lbl">Aktiv</span></div>
    <div class="stat"><span class="stat-val" id="s-prio1" style="color:var(--red)">0</span><span class="stat-lbl">Prio 1</span></div>
    <div class="stat"><span class="stat-val" id="s-geo" style="color:#aaffcc">0</span><span class="stat-lbl">Regionen</span></div>
    <div class="stat"><span class="stat-val" id="s-kw" style="color:var(--orange)">0</span><span class="stat-lbl">Keywords</span></div>
  </div>

  <!-- Eintrag hinzufügen -->
  <div class="add-card">
    <h2>➕ Eintrag hinzufügen</h2>
    <div class="form-row">
      <div class="form-grp" style="flex:2">
        <label>Begriff / Region</label>
        <input id="inp-term" class="form-input" type="text"
          placeholder="z.B. Ukraine, Konflikt Sudan, Explosion..."
          onkeydown="if(event.key==='Enter')addEntry()">
      </div>
      <div class="form-grp">
        <label>Typ</label>
        <select id="inp-type" class="form-select">
          <option value="geo">Region / Geo</option>
          <option value="keyword">Keyword</option>
          <option value="entity">Entität</option>
        </select>
      </div>
      <div class="form-grp">
        <label>Priorität</label>
        <select id="inp-prio" class="form-select">
          <option value="1">1 – Kritisch</option>
          <option value="2" selected>2 – Hoch</option>
          <option value="3">3 – Mittel</option>
          <option value="4">4 – Niedrig</option>
        </select>
      </div>
      <div class="form-grp">
        <label>Alert-Schwelle (%)</label>
        <input id="inp-thresh" class="form-input" type="number"
          min="0" max="100" value="40" style="max-width:70px">
      </div>
      <button class="btn-add" onclick="addEntry()">➕ Hinzufügen</button>
    </div>
  </div>

  <!-- Liste -->
  <div class="wl-header">
    <span>Begriff</span>
    <span>Typ</span>
    <span>Priorität</span>
    <span>Score</span>
    <span style="text-align:right">Aktionen</span>
  </div>
  <div id="wl-list"><div id="empty">⧋ Lade Watchlist...</div></div>

</div>

<div id="toast"></div>

<script>
const PORT = {port};
let entries = [];

// ── API Calls ─────────────────────────────────────────────────────────────────
async function api(path, body=null){{
  const opts = body
    ? {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}
    : {{method:'GET'}};
  const r = await fetch(`${{BASE}}${{path}}`, opts);
  return r.json();
}}

async function loadWatchlist(){{
  try {{
    const data = await api('/api/watchlist');
    entries = Array.isArray(data) ? data : (data.entries||[]);
    render();
  }} catch(e) {{
    // Fallback: localStorage
    try {{
      const saved = localStorage.getItem('nexus_watchlist');
      entries = saved ? JSON.parse(saved) : getSampleEntries();
    }} catch(e2) {{
      entries = getSampleEntries();
    }}
    render();
    toast('⚠ Server nicht erreichbar – lokaler Modus');
  }}
}}

function getSampleEntries(){{
  return [
    {{term:'Ukraine',type:'geo',priority:1,threshold:40,active:true,score:0}},
    {{term:'Naher Osten',type:'geo',priority:1,threshold:40,active:true,score:0}},
    {{term:'Rotes Meer',type:'geo',priority:2,threshold:50,active:true,score:0}},
    {{term:'Explosion',type:'keyword',priority:2,threshold:60,active:true,score:0}},
    {{term:'Nuclear',type:'keyword',priority:1,threshold:30,active:true,score:0}},
  ];
}}

async function addEntry(){{
  const term = document.getElementById('inp-term').value.trim();
  if (!term) {{ toast('⚠ Begriff eingeben!'); return; }}

  const entry = {{
    term,
    type: document.getElementById('inp-type').value,
    priority: parseInt(document.getElementById('inp-prio').value),
    threshold: parseInt(document.getElementById('inp-thresh').value)||40,
    active: true,
    score: 0,
    added: new Date().toISOString(),
  }};

  // Versuche Server, Fallback auf localStorage
  try {{
    await api('/api/watchlist/add', entry);
    await loadWatchlist();
  }} catch(e) {{
    entries.unshift(entry);
    saveLocal();
    render();
  }}

  document.getElementById('inp-term').value = '';
  toast(`✓ "${{term}}" hinzugefügt`);
}}

async function delEntry(term){{
  if (!confirm(`"${{term}}" löschen?`)) return;
  try {{
    await api('/api/watchlist/del', {{term}});
    await loadWatchlist();
  }} catch(e) {{
    entries = entries.filter(e => e.term !== term);
    saveLocal();
    render();
  }}
  toast(`🗑 "${{term}}" gelöscht`);
}}

async function toggleEntry(term){{
  try {{
    await api('/api/watchlist/toggle', {{term}});
    await loadWatchlist();
  }} catch(e) {{
    const e = entries.find(x => x.term === term);
    if(e) e.active = !e.active;
    saveLocal();
    render();
  }}
}}

function saveLocal(){{
  try {{ localStorage.setItem('nexus_watchlist', JSON.stringify(entries)); }} catch(e){{}}
}}

// ── Render ────────────────────────────────────────────────────────────────────
function esc(s){{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;'); }}

function render(){{
  const container = document.getElementById('wl-list');
  if (entries.length === 0) {{
    container.innerHTML = '<div id="empty">📭 Keine Einträge. Begriff oben hinzufügen.</div>';
    updateStats();
    return;
  }}

  let html = '';
  entries.forEach(e => {{
    const pCls = 'prio-' + (e.priority||2);
    const pText = ['','🔴 Kritisch','🟠 Hoch','🟡 Mittel','⚪ Niedrig'][e.priority||2]||'';
    const typCls = {{geo:'geo',keyword:'keyword',entity:''}}[e.type||'geo']||'';
    const typText = {{geo:'GEO',keyword:'KEYWORD',entity:'ENTITÄT'}}[e.type||'geo']||e.type||'?';
    const scoreCol = e.score > 60 ? 'var(--red)' : e.score > 30 ? 'var(--orange)' : 'var(--muted)';
    const activeBtn = e.active
      ? `<button class="btn-sm on" onclick="toggleEntry('${{esc(e.term)}}')">● AN</button>`
      : `<button class="btn-sm" onclick="toggleEntry('${{esc(e.term)}}')">○ AUS</button>`;

    html += `
    <div class="wl-item${{e.active?'':' disabled'}}">
      <div class="wl-term" title="${{esc(e.term)}}">${{esc(e.term)}}</div>
      <div><span class="wl-type ${{typCls}}">${{typText}}</span></div>
      <div class="wl-priority ${{pCls}}">${{pText}}</div>
      <div style="text-align:center;color:${{scoreCol}};font-size:11px">${{e.score||0}}</div>
      <div class="wl-actions">
        ${{activeBtn}}
        <button class="btn-sm" onclick="editThreshold('${{esc(e.term)}}',this)"
          title="Schwelle: ${{e.threshold||40}}%">⚙${{e.threshold||40}}%</button>
        <button class="btn-sm del" onclick="delEntry('${{esc(e.term)}}')">✕</button>
      </div>
    </div>`;
  }});
  container.innerHTML = html;
  updateStats();
}}

function updateStats(){{
  const active = entries.filter(e=>e.active).length;
  const prio1  = entries.filter(e=>e.priority===1).length;
  const geo    = entries.filter(e=>e.type==='geo').length;
  const kw     = entries.filter(e=>e.type==='keyword').length;
  document.getElementById('s-total').textContent  = entries.length;
  document.getElementById('s-active').textContent = active;
  document.getElementById('s-prio1').textContent  = prio1;
  document.getElementById('s-geo').textContent    = geo;
  document.getElementById('s-kw').textContent     = kw;
}}

function editThreshold(term, btn){{
  const entry = entries.find(e=>e.term===term);
  if(!entry) return;
  const val = prompt(`Alert-Schwelle für "${{term}}" (%):`, entry.threshold||40);
  if(val===null) return;
  const n = parseInt(val);
  if(isNaN(n)||n<0||n>100) return;
  entry.threshold = n;
  btn.textContent = `⚙${{n}}%`;
  btn.title = `Schwelle: ${{n}}%`;
  saveLocal();
  // Versuche Server-Sync
  api('/api/watchlist/add', entry).catch(()=>{{}});
  toast(`✓ Schwelle für "${{term}}" auf ${{n}}% gesetzt`);
}}

function exportWatchlist(){{
  const json = JSON.stringify(entries, null, 2);
  const blob = new Blob([json], {{type:'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'nexus_watchlist_' + new Date().toISOString().slice(0,10) + '.json';
  a.click();
  URL.revokeObjectURL(url);
  toast('✓ Watchlist exportiert');
}}

function toast(msg){{
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}}

// Init
loadWatchlist();
</script>
</body>
</html>"""
