"""
NEXUS - Quellen-Gesundheits-Dashboard UI (T176)
Live-Monitoring-Seite für die wichtigsten externen OSINT-Quellen — ergänzt die
post-mortem /modules-Statusseite (die nur eine einzelne nexus_diagnostic.py-
Stichprobe zeigt) um eine Seite, die bei jedem Laden/Reload AKTIV gegen die
echten Quell-Endpunkte pingt, Antwortzeiten misst und eine rollierende
Uptime-Historie je Quelle anzeigt.

Läuft als eigene Seite "/source_health", analog zu nexus_maritime_dashboard.py.
Holt Daten über "/api/source_health".
"""

from __future__ import annotations


def build_source_health_html(port: int = 11430) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS · Quellen-Gesundheit (Live)</title>
<style>
  :root {{
    --bg:#0a0e1a; --bg2:#111827; --bg3:#1a2235;
    --ok:#00d26a; --warn:#ffcc00; --err:#ff4444;
    --text:#e2e8f0; --muted:#64748b; --border:#2d3748; --accent:#00d4ff;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
          min-height:100vh }}
  header {{ background:var(--bg2); border-bottom:1px solid var(--border); padding:18px 24px;
            display:flex; align-items:center; gap:16px; position:sticky; top:0; z-index:100;
            flex-wrap:wrap }}
  header h1 {{ font-size:1.2rem; font-weight:700; letter-spacing:.5px }}
  header h1 span {{ color:var(--accent) }}
  .nav-links {{ margin-left:auto; display:flex; gap:10px; align-items:center; flex-wrap:wrap }}
  .nav-links a {{ color:var(--muted); text-decoration:none; font-size:.82rem; padding:6px 12px;
                  border-radius:6px; border:1px solid var(--border); transition:all .2s }}
  .nav-links a:hover {{ color:var(--text); border-color:var(--accent) }}
  .live-dot {{ width:8px; height:8px; border-radius:50%; background:var(--ok); display:inline-block;
               margin-right:6px; animation:pulse 2s infinite }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.35}} }}

  .summary-bar {{ display:flex; gap:12px; padding:20px 24px; flex-wrap:wrap }}
  .stat-card {{ background:var(--bg2); border:1px solid var(--border); border-radius:10px;
                padding:14px 22px; min-width:120px; text-align:center }}
  .stat-card .num {{ font-size:2rem; font-weight:800; line-height:1 }}
  .stat-card .lbl {{ font-size:.72rem; color:var(--muted); margin-top:4px; text-transform:uppercase;
                     letter-spacing:.5px }}
  .stat-ok {{ color:var(--ok); border-color:rgba(0,210,106,.3) }}
  .stat-warn {{ color:var(--warn); border-color:rgba(255,204,0,.3) }}
  .stat-err {{ color:var(--err); border-color:rgba(255,68,68,.3) }}

  .controls {{ padding:0 24px 14px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;
               color:var(--muted); font-size:.82rem }}
  .refresh-btn {{ background:var(--accent); color:#000; border:none; border-radius:8px;
                  padding:8px 16px; font-weight:700; font-size:.85rem; cursor:pointer }}
  .refresh-btn:hover {{ opacity:.85 }}
  #countdown {{ color:var(--ok) }}

  table {{ width:calc(100% - 48px); margin:0 24px 32px; border-collapse:collapse;
           background:var(--bg2); border:1px solid var(--border); border-radius:10px;
           overflow:hidden }}
  th, td {{ text-align:left; padding:10px 14px; font-size:.85rem; border-bottom:1px solid var(--border) }}
  th {{ color:var(--muted); text-transform:uppercase; font-size:.7rem; letter-spacing:.5px;
        background:var(--bg3) }}
  tr:last-child td {{ border-bottom:none }}
  tr:hover td {{ background:rgba(255,255,255,.02) }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:5px; font-size:.74rem; font-weight:700 }}
  .b-ok {{ background:rgba(0,210,106,.15); color:var(--ok) }}
  .b-warn {{ background:rgba(255,204,0,.15); color:var(--warn) }}
  .b-err {{ background:rgba(255,68,68,.15); color:var(--err) }}
  .mod {{ color:var(--muted); font-size:.76rem }}
  .lat {{ font-variant-numeric:tabular-nums }}
  .spark {{ display:inline-flex; gap:2px; align-items:flex-end; height:16px; vertical-align:middle }}
  .spark i {{ display:inline-block; width:4px; border-radius:1px }}
  .spark .s-ok {{ background:var(--ok); height:100% }}
  .spark .s-langsam {{ background:var(--warn); height:65% }}
  .spark .s-fehler {{ background:var(--err); height:35% }}
  .err-detail {{ color:var(--err); font-size:.72rem; margin-top:2px }}
  .loading {{ color:var(--muted); padding:40px; text-align:center }}
  .ts-line {{ padding:0 24px 16px; color:var(--muted); font-size:.8rem }}
</style>
</head>
<body>

<header>
  <div>🩺</div>
  <h1>NEXUS <span>Quellen-Gesundheit</span> <span class="live-dot"></span><small style="color:var(--muted);font-size:.7rem">LIVE</small></h1>
  <div class="nav-links">
    <a href="http://localhost:{port}/">⬅ Dashboard</a>
    <a href="http://localhost:{port}/modules">📋 Modul-Status (Post-Mortem)</a>
    <a href="http://localhost:{port}/livemap">🗺 Karte</a>
  </div>
</header>

<div class="summary-bar" id="summary">
  <div class="stat-card stat-total"><div class="num" id="s-total">–</div><div class="lbl">Quellen geprüft</div></div>
  <div class="stat-card stat-ok"><div class="num" id="s-ok">–</div><div class="lbl">✅ Erreichbar</div></div>
  <div class="stat-card stat-warn"><div class="num" id="s-langsam">–</div><div class="lbl">🟡 Langsam</div></div>
  <div class="stat-card stat-err"><div class="num" id="s-fehler">–</div><div class="lbl">❌ Down</div></div>
</div>

<div class="controls">
  <button class="refresh-btn" onclick="loadHealth(true)">↻ Jetzt live prüfen</button>
  <span>Auto-Refresh in <b id="countdown">90</b>s · Letzter Check: <span id="last-check">–</span></span>
</div>

<div class="ts-line">
  Live-Pings gegen die echten externen API-Endpunkte (HEAD/GET, kurzer Timeout) –
  kein post-mortem Sample, sondern der aktuelle Zustand jetzt gerade.
  Sparklines zeigen die letzten ~20 Live-Checks je Quelle (grün=ok, gelb=langsam, rot=down).
</div>

<div id="content"><div class="loading">Lade Live-Status der Quellen ...</div></div>

<script>
const PORT = {port};
const API  = `http://localhost:${{PORT}}/api/source_health`;
let countdownT = 90;
let countdownTimer = null;

function badge(status) {{
  if (status === 'ok')      return '<span class="badge b-ok">✅ OK</span>';
  if (status === 'langsam') return '<span class="badge b-warn">🟡 langsam</span>';
  return '<span class="badge b-err">❌ down</span>';
}}

function sparkline(history) {{
  if (!history || !history.length) return '<span class="mod">–</span>';
  const bars = history.map(s => `<i class="s-${{s}}"></i>`).join('');
  return `<span class="spark">${{bars}}</span>`;
}}

async function loadHealth(force) {{
  try {{
    const resp = await fetch(`${{API}}${{force ? '?force=1' : ''}}`);
    const d = await resp.json();
    if (d.error) {{
      document.getElementById('content').innerHTML = `<div class="loading">Fehler: ${{d.error}}</div>`;
      return;
    }}
    render(d);
    countdownT = 90;
  }} catch (e) {{
    document.getElementById('content').innerHTML = `<div class="loading">Ladefehler: ${{e}}</div>`;
  }}
}}

function render(d) {{
  const s = d.summary;
  document.getElementById('s-total').textContent   = s.total;
  document.getElementById('s-ok').textContent      = s.ok;
  document.getElementById('s-langsam').textContent = s.langsam;
  document.getElementById('s-fehler').textContent  = s.fehler;
  document.getElementById('last-check').textContent =
      d.timestamp + (d.from_cache ? ' (gecached, max. ' + d.cache_ttl_s + 's alt)' : ' (frisch geprüft)');

  let rows = '';
  d.sources.forEach(r => {{
    const lat = r.latency_ms !== null && r.latency_ms !== undefined ? r.latency_ms + ' ms' : '–';
    const up  = r.uptime_pct !== null && r.uptime_pct !== undefined ? r.uptime_pct + '%' : '–';
    rows += `<tr>
      <td><b>${{r.name}}</b><div class="mod">${{r.module}}</div></td>
      <td class="mod">${{r.category}}</td>
      <td>${{badge(r.status)}}${{r.error ? '<div class="err-detail">' + r.error + '</div>' : ''}}</td>
      <td class="lat">${{lat}}</td>
      <td class="lat">HTTP ${{r.http_code ?? '–'}}</td>
      <td class="lat">${{up}}</td>
      <td>${{sparkline(r.history)}}</td>
      <td class="mod">${{r.checked_at}}</td>
    </tr>`;
  }});

  document.getElementById('content').innerHTML = `
    <table>
      <thead><tr>
        <th>Quelle</th><th>Kategorie</th><th>Status</th><th>Latenz</th>
        <th>HTTP</th><th>Uptime (Historie)</th><th>Trend (letzte ~20 Checks)</th><th>Geprüft um</th>
      </tr></thead>
      <tbody>${{rows}}</tbody>
    </table>`;
}}

function tickCountdown() {{
  countdownT -= 1;
  if (countdownT <= 0) {{
    loadHealth(false);
    countdownT = 90;
  }}
  document.getElementById('countdown').textContent = countdownT;
}}

loadHealth(true);
countdownTimer = setInterval(tickCountdown, 1000);
</script>
</body>
</html>"""
