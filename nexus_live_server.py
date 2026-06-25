"""
NEXUS Live Map Server
Lokaler HTTP-Server auf localhost:11430.
Liefert aktuelle Flug-, Wetter-, Maritime- und Nachrichtendaten als JSON-API.
Die Live-Karte im Browser pollt diesen Server alle 3 Minuten automatisch.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Pfad zum zuletzt generierten HTML-Report
_last_report_path: str = ""

def set_last_report(path: str) -> None:
    global _last_report_path
    _last_report_path = path

# Ordner mit den von nexus_daily.py / nexus_selftest.py erzeugten Berichten.
# Eigener, dauerhafter Prozess (dieser Server) liefert Berichte aus, die ein
# *anderer*, kurzlebiger Cron-Prozess (nexus_daily.py) zuvor auf die Platte
# geschrieben hat — deshalb über den Ordner, nicht über das In-Memory-_last_report_path.
_REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexus_reports")


# ── Watchlist JSON-Store (T93) ───────────────────────────────────────────────
_WL_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "nexus_watchlist_data.json")
_wl_lock = threading.Lock()

def _watchlist_load() -> list:
    with _wl_lock:
        try:
            with open(_WL_JSON_PATH, "r", encoding="utf-8") as _f:
                return json.load(_f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    try:
        from nexus_memory import wl_list  # type: ignore
        entries = wl_list()
        return [
            {
                "term":      e["term"],
                "type":      "geo",
                "priority":  2,
                "threshold": 40,
                "active":    bool(e.get("active", 1)),
                "score":     0,
                "added":     e.get("added_ts", ""),
                "alert_cnt": e.get("alert_cnt", 0),
            }
            for e in entries
        ]
    except Exception:
        return []

def _watchlist_save(entries: list) -> None:
    with _wl_lock:
        try:
            with open(_WL_JSON_PATH, "w", encoding="utf-8") as _f:
                json.dump(entries, _f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def _watchlist_save_entry(entry: dict) -> None:
    entries = _watchlist_load()
    term = (entry.get("term") or "").strip()
    entries = [e for e in entries if e.get("term") != term]
    entries.insert(0, {
        "term":      term,
        "type":      entry.get("type", "keyword"),
        "priority":  int(entry.get("priority", 2)),
        "threshold": int(entry.get("threshold", 40)),
        "active":    bool(entry.get("active", True)),
        "score":     int(entry.get("score", 0)),
        "added":     entry.get("added", datetime.now(timezone.utc).isoformat()),
        "alert_cnt": int(entry.get("alert_cnt", 0)),
    })
    _watchlist_save(entries)

def _watchlist_remove_entry(term: str) -> None:
    entries = _watchlist_load()
    entries = [e for e in entries if e.get("term") != term]
    _watchlist_save(entries)

def _watchlist_toggle_entry(term: str) -> None:
    entries = _watchlist_load()
    for e in entries:
        if e.get("term") == term:
            e["active"] = not bool(e.get("active", True))
            break
    _watchlist_save(entries)

# Port: aus ENV-Variable NEXUS_PORT (Docker) oder Default 11430 (lokal)
LIVE_PORT  = int(os.environ.get("NEXUS_PORT", 11430))
# Host: ENV > config.py > "localhost"
_host_env = os.environ.get("NEXUS_HOST", "").strip()
if not _host_env:
    try:
        import config as _hcfg  # type: ignore
        _host_env = str(getattr(_hcfg, "NEXUS_HOST", "")).strip()
    except ImportError:
        pass
LIVE_HOST  = _host_env if _host_env else "localhost"
CACHE_TTL  = 180  # Sekunden zwischen echten API-Abfragen

# ── Sicherheit ─────────────────────────────────────────────────────────────────
# Optionaler API-Token-Schutz.
# Wenn NEXUS_TOKEN gesetzt ist (empfohlen bei NEXUS_HOST=0.0.0.0),
# muss jede Anfrage den Header "X-Nexus-Token: <token>" enthalten.
# Lokal (localhost) kann NEXUS_TOKEN leer bleiben.
_NEXUS_TOKEN: str = os.environ.get("NEXUS_TOKEN", "").strip()
if not _NEXUS_TOKEN:
    try:
        import config as _cfg  # type: ignore
        _NEXUS_TOKEN = str(getattr(_cfg, "NEXUS_TOKEN", "")).strip()
    except ImportError:
        pass

# CORS: Nur localhost erlauben, es sei denn explizit konfiguriert
_ALLOWED_ORIGIN: str = os.environ.get("NEXUS_CORS_ORIGIN", "http://localhost")
# Vertrauenswürdige IP-Bereiche: localhost + Tailscale + WireGuard/OpenVPN + WLAN-Heimnetz
_TRUSTED_ORIGINS = (
    "http://localhost",
    "http://127.0.0.1",
    "http://100.",    # Tailscale: 100.64.x.x – 100.127.x.x
    "http://10.",     # WireGuard / OpenVPN / ZeroTier (10.x.x.x)
    "http://172.1",   # WireGuard: 172.16–31.x.x
    "http://172.2",   # WireGuard: 172.20–29.x.x
    "http://192.168.", # Heimnetz WLAN – Handy im gleichen WLAN
)

# Query-Länge begrenzen (DoS-Schutz)
_MAX_QUERY_LEN = 200

# Netzwerk-Modus warnen
_NETWORK_EXPOSED = (LIVE_HOST not in ("localhost", "127.0.0.1"))
if _NETWORK_EXPOSED and not _NEXUS_TOKEN:
    print(
        "\n\033[33m[NEXUS HINWEIS]\033[0m "
        f"Server bindet auf {LIVE_HOST}:{LIVE_PORT} ohne Token-Schutz.\n"
        "  → Empfehlung: NEXUS_TOKEN in config.py setzen.\n",
        flush=True,
    )
elif _NETWORK_EXPOSED and _NEXUS_TOKEN:
    print(
        f"\033[32m[NEXUS] Token-Schutz aktiv\033[0m – "
        f"Login-Seite: http://{LIVE_HOST}:{LIVE_PORT}/login\n",
        flush=True,
    )


def _get_watchlist_regions() -> list:
    """Gibt aktuelle Watchlist-Regionen zurück, oder Defaults."""
    DEFAULT = ["Ukraine", "Naher Osten", "Persischer Golf", "Rotes Meer", "Taiwan-Strasse"]
    try:
        from nexus_memory import wl_list  # type: ignore
        wl = wl_list()
        regions = [e["term"] for e in wl] if wl else DEFAULT
        return regions if regions else DEFAULT
    except Exception:
        return DEFAULT


def _build_dashboard_html() -> str:
    """Baut das Multi-Region Dashboard als fertiges HTML."""
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    port = LIVE_PORT
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>NEXUS DASHBOARD</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0a0e14;color:#c8d6e0;font-family:'Courier New',monospace;font-size:12px}}
  .header{{background:linear-gradient(90deg,#0a1a2e,#0d2035);border-bottom:2px solid #00d4ff;
           padding:10px 20px;display:flex;justify-content:space-between;align-items:center}}
  .logo{{color:#00d4ff;font-size:18px;font-weight:bold;letter-spacing:4px}}
  .ts{{color:#4a6070;font-size:10px}}
  .econ-bar{{background:#0a1218;border-bottom:1px solid #1e3a4a;padding:6px 20px;
             display:flex;gap:20px;flex-wrap:wrap;font-size:11px}}
  .econ-item{{display:flex;gap:6px;align-items:center}}
  .econ-val{{color:#00ff88;font-weight:bold}}
  .econ-chg.pos{{color:#00ff88}} .econ-chg.neg{{color:#ff4444}} .econ-chg.neu{{color:#8aa0b0}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
         gap:12px;padding:16px 20px}}
  .card{{background:#111820;border:1px solid #1e3a4a;border-radius:4px;overflow:hidden;
         transition:border-color 0.2s}}
  .card:hover{{border-color:#00d4ff}}
  .card.alert{{border-color:#ff4444}}
  .card-header{{background:#0a1620;padding:8px 12px;display:flex;
               justify-content:space-between;align-items:center}}
  .card-title{{color:#00d4ff;font-weight:bold;letter-spacing:2px;font-size:11px}}
  .card-status.ok{{color:#00ff88;font-size:10px}}
  .card-status.warn{{color:#ff8800;font-size:10px}}
  .card-status.crit{{color:#ff4444;font-size:10px}}
  .card-body{{padding:10px 12px}}
  .row{{display:flex;justify-content:space-between;margin-bottom:4px;font-size:11px}}
  .lbl{{color:#4a6070}}
  .val{{color:#c8d6e0}}
  .val.red{{color:#ff4444}} .val.yellow{{color:#ff8800}} .val.green{{color:#00ff88}}
  .news-item{{font-size:10px;color:#8aa0b0;margin-top:2px;
             border-left:2px solid #1e3a4a;padding-left:6px;
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .refresh-btn{{background:#0a1620;border:1px solid #1e3a4a;color:#00d4ff;
               padding:4px 10px;cursor:pointer;font-family:inherit;font-size:10px;
               border-radius:2px}}
  .refresh-btn:hover{{border-color:#00d4ff}}
  .footer{{border-top:1px solid #1e3a4a;padding:6px 20px;color:#4a6070;font-size:10px;
           display:flex;justify-content:space-between}}
  .osint-signals{{background:#1a0a0a;border-left:3px solid #ff4444;padding:8px 12px;
                 margin:0 20px 12px;font-size:11px;color:#ff8800}}

  /* ── Lade-Fortschrittsbalken ── */
  #loader{{
    display:none;
    position:fixed;bottom:0;left:0;right:0;
    background:#0a1218;border-top:1px solid #1e3a4a;
    padding:8px 20px 10px;z-index:999;
  }}
  #loader.active{{display:block}}
  .ld-row{{display:flex;align-items:center;gap:12px;margin-bottom:5px}}
  .ld-title{{color:#00d4ff;font-size:10px;font-weight:bold;letter-spacing:2px;width:160px;flex-shrink:0}}
  .ld-step{{color:#8aa0b0;font-size:11px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .ld-pct{{color:#00ff88;font-size:10px;width:36px;text-align:right;flex-shrink:0}}
  .ld-time{{color:#4a6070;font-size:10px;width:50px;text-align:right;flex-shrink:0}}
  .ld-track{{background:#0d1c28;border:1px solid #1e3a4a;border-radius:2px;height:6px;flex:1}}
  .ld-fill{{
    height:100%;border-radius:2px;
    background:linear-gradient(90deg,#00d4ff,#00ff88);
    transition:width 0.4s ease;
    width:0%;
  }}
  .ld-fill.done{{background:#00ff88}}
  .ld-fill.error{{background:#ff4444}}
  .ld-steps-row{{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}}
  .ld-dot{{
    width:10px;height:10px;border-radius:2px;
    background:#1e3a4a;flex-shrink:0;
    transition:background 0.3s;
  }}
  .ld-dot.done{{background:#00ff88}}
  .ld-dot.active{{background:#00d4ff;box-shadow:0 0 6px #00d4ff}}
  .ld-dot.error{{background:#ff4444}}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="logo">◈ NEXUS DASHBOARD</div>
    <div class="ts" id="ts">Lädt…</div>
  </div>
  <div style="display:flex;gap:10px;align-items:center">
    <a href="http://localhost:{port}/livemap" target="_blank"
       style="color:#00ff88;font-size:10px;text-decoration:none">🗺 Live-Karte</a>
    <a href="http://localhost:{port}/maritime" target="_blank"
       style="color:#00d4ff;font-size:10px;text-decoration:none">⚓ Maritim-Dashboard</a>
    <a href="http://localhost:{port}/source_health" target="_blank"
       style="color:#00ff88;font-size:10px;text-decoration:none">🩺 Quellen-Gesundheit</a>
    <a href="http://localhost:{port}/report" target="_blank"
       style="color:#00d4ff;font-size:10px;text-decoration:none">📋 Letzter Report</a>
    <button class="refresh-btn" onclick="loadAll()">↻ Aktualisieren</button>
  </div>
</div>

<div class="econ-bar" id="econ-bar">
  <span style="color:#4a6070">Wirtschaft: lädt…</span>
</div>

<div id="osint-signals"></div>
<div class="grid" id="grid">
  <div style="color:#4a6070;padding:20px">Pipeline startet…</div>
</div>

<div class="footer">
  <span>NEXUS OSINT v0.8 | Multi-Region Dashboard | Auto-Refresh: 5min</span>
  <span id="next-refresh"></span>
</div>

<!-- Lade-Fortschrittsbalken (fixed am unteren Rand) -->
<div id="loader">
  <div class="ld-row">
    <span class="ld-title">◈ PIPELINE</span>
    <span class="ld-step" id="ld-step-name">Initialisierung…</span>
    <span class="ld-pct" id="ld-pct">0%</span>
    <span class="ld-time" id="ld-time">0.0s</span>
  </div>
  <div class="ld-row">
    <div class="ld-title" style="color:#4a6070;font-size:9px" id="ld-query"></div>
    <div class="ld-track">
      <div class="ld-fill" id="ld-fill"></div>
    </div>
  </div>
  <div class="ld-steps-row" id="ld-dots"></div>
</div>

<script>
const PORT = {port};
const TOTAL_STEPS = 28;
let countdown = 300;
let pollTimer = null;
let loadStart = 0;

// Dots einmalig erzeugen
(function() {{
  const row = document.getElementById('ld-dots');
  for (let i = 0; i < TOTAL_STEPS; i++) {{
    const d = document.createElement('div');
    d.className = 'ld-dot';
    d.id = 'dot-' + (i+1);
    d.title = 'Schritt ' + (i+1);
    row.appendChild(d);
  }}
}})();

function showLoader(query) {{
  document.getElementById('loader').classList.add('active');
  document.getElementById('ld-query').textContent = '🔍 ' + (query || '…');
  document.getElementById('ld-step-name').textContent = 'Initialisierung…';
  document.getElementById('ld-fill').style.width = '0%';
  document.getElementById('ld-fill').className = 'ld-fill';
  document.getElementById('ld-pct').textContent = '0%';
  document.getElementById('ld-time').textContent = '0.0s';
  for (let i = 1; i <= TOTAL_STEPS; i++) {{
    const d = document.getElementById('dot-' + i);
    if (d) d.className = 'ld-dot';
  }}
  loadStart = Date.now();
}}

function updateLoader(status) {{
  if (!status) return;
  const step  = status.step  || 0;
  const pct   = status.pct   || 0;
  const name  = status.name  || '…';
  const elapsed = status.elapsed || 0;
  const done  = !status.running && pct >= 100;
  const err   = name.includes('Fehler') || name.includes('Error');

  document.getElementById('ld-step-name').textContent = name;
  document.getElementById('ld-pct').textContent = pct + '%';
  document.getElementById('ld-time').textContent = elapsed.toFixed(1) + 's';

  const fill = document.getElementById('ld-fill');
  fill.style.width = pct + '%';
  fill.className = 'ld-fill' + (done ? ' done' : (err ? ' error' : ''));

  // Dots einfärben
  for (let i = 1; i <= TOTAL_STEPS; i++) {{
    const d = document.getElementById('dot-' + i);
    if (!d) continue;
    if (i < step) d.className = 'ld-dot done';
    else if (i === step) d.className = 'ld-dot active';
    else d.className = 'ld-dot';
  }}
}}

function hideLoader() {{
  document.getElementById('loader').classList.remove('active');
  if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
}}

function startPolling() {{
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {{
    try {{
      const r = await fetch(`http://localhost:${{PORT}}/api/status`);
      const s = await r.json();
      updateLoader(s);
      if (!s.running && s.pct >= 100) {{
        setTimeout(hideLoader, 1200);
        clearInterval(pollTimer);
        pollTimer = null;
      }}
    }} catch(e) {{ /* Server busy */ }}
  }}, 400);
}}

function fmt_pct(pct) {{
  if (pct === null || pct === undefined) return '';
  const sign = pct >= 0 ? '+' : '';
  return ` ${{sign}}${{pct.toFixed(1)}}%`;
}}

function render_econ(econ) {{
  if (!econ || !econ.indicators) return;
  const bar = document.getElementById('econ-bar');
  const stress = econ.market_stress || 'NORMAL';
  const stress_color = {{KRITISCH:'#ff4444',ERHÖHT:'#ff8800',LEICHT_ERHÖHT:'#ffaa00',NORMAL:'#00ff88'}}[stress] || '#00ff88';
  const keys = ['wti_oil','gold','vix'];
  const icons = {{wti_oil:'🛢',brent_oil:'🛢',gold:'🥇',vix:'📊',sp500:'📈',copper:'🔧'}};
  let html = keys.map(k => {{
    const ind = econ.indicators[k];
    if (!ind) return '';
    const pct = ind.change_pct;
    const cls = pct > 0.5 ? 'pos' : pct < -0.5 ? 'neg' : 'neu';
    return `<div class="econ-item">
      <span>${{icons[k] || ''}} ${{ind.name}}</span>
      <span class="econ-val">${{ind.price}} ${{ind.unit}}</span>
      ${{pct !== null ? `<span class="econ-chg ${{cls}}">${{fmt_pct(pct)}}</span>` : ''}}
    </div>`;
  }}).join('');
  html += `<div class="econ-item" style="margin-left:auto">
    <span style="color:#4a6070">Marktstress:</span>
    <span style="color:${{stress_color}};font-weight:bold">${{stress}}</span>
  </div>`;
  bar.innerHTML = html;
  const sigs = econ.osint_signals || [];
  const sigDiv = document.getElementById('osint-signals');
  if (sigs.length > 0) {{
    sigDiv.innerHTML = `<div class="osint-signals">
      ⚡ WIRTSCHAFTS-WARNSIGNALE: ${{sigs.join(' &nbsp;|&nbsp; ')}}
    </div>`;
  }} else {{
    sigDiv.innerHTML = '';
  }}
}}

function render_card(region_data) {{
  const r = region_data;
  const fl = r.flights;
  const wd = r.weather;
  const ma = r.maritime;
  const susp = fl ? (fl.suspicious || 0) : 0;
  const ma_alerts = ma ? (ma.alert_count || 0) : 0;
  const is_alert = susp > 0 || ma_alerts > 0;
  const status_cls = susp > 2 || ma_alerts > 3 ? 'crit' : (is_alert ? 'warn' : 'ok');
  const status_txt = is_alert
    ? `⚠ ${{susp > 0 ? susp + ' Flugzeug(e)' : ''}}${{susp > 0 && ma_alerts > 0 ? ' + ' : ''}}${{ma_alerts > 0 ? ma_alerts + ' Maritime' : ''}}`
    : '✅ Ruhig';
  const weather_icon = wd ? (wd.overall === 'rot' ? '🔴' : wd.overall === 'gelb' ? '🟡' : '🟢') : '';
  return `<div class="card ${{is_alert ? 'alert' : ''}}">
    <div class="card-header">
      <span class="card-title">${{r.region.toUpperCase()}}</span>
      <span class="card-status ${{status_cls}}">${{status_txt}}</span>
    </div>
    <div class="card-body">
      ${{fl ? `<div class="row"><span class="lbl">✈ Flugzeuge</span>
        <span class="val ${{susp > 0 ? 'red' : 'green'}}">${{fl.total || 0}} (${{susp}} auffällig)</span></div>` : ''}}
      ${{wd ? `<div class="row"><span class="lbl">${{weather_icon}} Wetter</span>
        <span class="val">${{wd.desc || '?'}} ${{wd.temp_c !== null && wd.temp_c !== undefined ? wd.temp_c + '°C' : ''}}</span></div>` : ''}}
      ${{ma ? `<div class="row"><span class="lbl">⚓ Maritime</span>
        <span class="val ${{ma_alerts > 0 ? 'yellow' : 'green'}}">${{ma_alerts}} Alert(s)</span></div>` : ''}}
      <div class="row"><span class="lbl">📰 Artikel</span>
        <span class="val">${{r.article_count || 0}}</span></div>
      <div class="row"><span class="lbl">🕒 Stand</span>
        <span class="lbl">${{r.timestamp || '–'}}</span></div>
      <div style="margin-top:6px">
        <a href="javascript:void(0)" onclick="openReport('${{r.region}}')"
           style="color:#00d4ff;font-size:10px;text-decoration:none">→ Lagebild öffnen</a>
      </div>
    </div>
  </div>`;
}}

function openReport(region) {{
  window.open(`http://localhost:${{PORT}}/report`, '_blank');
}}

async function loadAll() {{
  // Sofort Loader anzeigen + Polling starten
  showLoader('...');
  startPolling();
  document.getElementById('ts').textContent = 'Lade Pipeline…';
  document.getElementById('grid').innerHTML =
    '<div style="color:#4a6070;padding:20px">Pipeline läuft — Fortschritt unten ↓</div>';

  try {{
    const resp = await fetch(`http://localhost:${{PORT}}/api/dashboard`);
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();

    document.getElementById('ts').textContent =
      `Stand: ${{data.timestamp || new Date().toLocaleTimeString()}}`;
    render_econ(data.economics);

    const grid = document.getElementById('grid');
    if (!data.regions || data.regions.length === 0) {{
      grid.innerHTML = '<div style="color:#4a6070;padding:20px">Keine Watchlist-Regionen. W+ Ukraine eingeben.</div>';
    }} else {{
      grid.innerHTML = data.regions.map(render_card).join('');
    }}
  }} catch(e) {{
    document.getElementById('ts').textContent = '⚠ Fehler: ' + e.message;
    document.getElementById('ld-step-name').textContent = '⚠ Verbindungsfehler: ' + e.message;
    document.getElementById('ld-fill').className = 'ld-fill error';
    setTimeout(hideLoader, 4000);
  }}
  countdown = 300;
}}

// Countdown-Timer
setInterval(() => {{
  countdown--;
  const m = Math.floor(countdown / 60);
  const s = countdown % 60;
  document.getElementById('next-refresh').textContent =
    `Nächste Aktualisierung: ${{m}}:${{s.toString().padStart(2,'0')}}`;
  if (countdown <= 0) loadAll();
}}, 1000);

loadAll();
</script>
</body>
</html>"""

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

# ── Hintergrund-Pipeline ──────────────────────────────────────────────────────
# Wenn Cache leer ist, Pipeline im Background-Thread starten + sofort Stub liefern
_bg_running: set[str] = set()
_bg_lock    = threading.Lock()

def _EMPTY_STUB() -> dict:
    """Minimal-Antwort wenn Pipeline noch läuft (kein Cache-Treffer)."""
    return {
        "loading": True,
        "status":  "loading",
        "from_cache": False,
        "ais":             {"vessels": [], "vanishing_vessels": [], "vessel_count": 0},
        "ais_vessels":     [],
        "vanished_vessels":[],
        "flights":         {"aircraft": [], "vanishing_aircraft": [], "aircraft_list": []},
        "vanished_aircraft":[],
        "gdelt_events":    [],
        "acled":           [],
        "acled_events":    [],
        "gdacs_events":    [],
        "nuclear_sites":   [],
        "ransomware_events": [],
        "incidents":       [],
        "fires":           [],
        "seismic_events":  [],
        "notam_data":      [],
        "gpsjam":          [],
        "lightning":       [],
        "fusion_events":   [],
        "fusion_threats":  [],
        "humint_data":     [],
        "humint_markers":  [],
        "vision_data":     [],
        "geo_data":        [],
        "movement_data":   [],
        "webcam_data":     [],
        "sar_data":        [],
        "naval_data":      [],
        "milflights":      [],
        "patrol_anomalies":[],
        "hf_data":         [],
        "draught_alerts":  [],
        "radiation_data":  [],
        "eonet_events":    [],
        "viirs_dark":      [],
        "health_alerts":   [],
        "sanctions_hits":  [],
        "bgp_anomalies":   [],
        "displacement":    [],
        "escalation":      {"score": 0, "level": "UNBEKANNT", "icon": "⟳"},
        "telegram_surges": [],
        "satellite_passes":[],
        "economics":       {},
        "timestamp":       "⟳ Pipeline läuft…",
    }

def _bg_start_pipeline(query: str) -> None:
    """Startet _fetch_live_data() im Hintergrund-Thread (nur 1× pro Query)."""
    q = query.lower()
    with _bg_lock:
        if q in _bg_running:
            return          # Läuft bereits – nichts doppelt starten
        _bg_running.add(q)

    def _run():
        try:
            data = _fetch_live_data(query)
            _set_cache(query, data)
        except Exception as _e:
            import sys
            print(f"[BG-Pipeline] Fehler für '{query}': {_e}", file=sys.stderr)
        finally:
            with _bg_lock:
                _bg_running.discard(q)

    t = threading.Thread(target=_run, name=f"bg-pipeline-{q}", daemon=True)
    t.start()

# ── Transponder/AIS-Off Detektion ──────────────────────────────────────────────
# Flugzeug-Snapshots pro Query
_aircraft_snapshots: dict[str, dict[str, dict]] = {}
# Schiffs-Snapshots pro Query (mmsi/name -> vessel-dict)
_vessel_snapshots: dict[str, dict[str, dict]] = {}
_snapshot_lock = threading.Lock()


# ── Query-Helfer ──────────────────────────────────────────────────────────────
import re as _re

# Bekannte Regionen / Länder (für Geo-API-Extraktion)
_GEO_REGIONS = [
    "Ukraine", "Russland", "Russia", "Belarus", "Moldau", "Moldova",
    "Naher Osten", "Middle East", "Taiwan", "Rotes Meer", "Red Sea",
    "Persischer Golf", "Persian Gulf", "Hormuz", "Israel", "Gaza",
    "Westjordanland", "West Bank", "Libanon", "Lebanon", "Iran",
    "Syrien", "Syria", "Jemen", "Yemen", "Sudan", "Myanmar", "China",
    "Nordkorea", "North Korea", "Korea", "NATO", "Europa", "Europe",
    "Sahel", "Libyen", "Libya", "Somalia", "Äthiopien", "Ethiopia",
    "Niger", "Mali", "Burkina Faso", "Mosambik", "Mozambique",
    "Pakistan", "Afghanistan", "Irak", "Iraq", "Kasachstan",
    "Armenien", "Armenia", "Aserbaidschan", "Azerbaijan",
    "Schwarzes Meer", "Black Sea", "Ostsee", "Baltic Sea",
    "Mittelmeer", "Mediterranean", "Indischer Ozean", "Indian Ocean",
]

def _sanitize_query(text: str) -> str:
    """Entfernt URL-gefährliche Sonderzeichen aus einem Query-String."""
    return _re.sub(r'[#&=<>{}|\\^`\[\]@!]+', '', text or "").strip()

def _extract_geo_region(query: str) -> str:
    """
    Extrahiert den primären Geo-Regionsnamen aus einem Compound-Query.
    'Ukraine aktuell' → 'Ukraine'
    'Lage Naher Osten heute' → 'Naher Osten'
    """
    q = _sanitize_query(query)
    q_lower = q.lower()
    # Längste Übereinstimmung zuerst (z.B. "Naher Osten" vor "Osten")
    for r in sorted(_GEO_REGIONS, key=len, reverse=True):
        if r.lower() in q_lower:
            return r
    # Fallback: erstes Wort mit Großbuchstabe und Mindestlänge
    for w in q.split():
        if w and len(w) >= 3 and (w[0].isupper() or w.istitle()):
            return w.capitalize()
    # Letzter Fallback: erstes Wort
    parts = q.split()
    return parts[0].capitalize() if parts else q

# ── Pipeline-Status (Lade-Fortschrittsbalken im Dashboard) ──────────────────
import time as _time_mod
_pipeline_status: dict = {
    "running": False, "step": 0, "total": 28, "name": "–",
    "pct": 0, "query": "", "started": 0.0, "elapsed": 0.0, "errors": [],
}
_pstatus_lock = threading.Lock()

_STEP_NAMES: list[tuple[int, str]] = [
    (1,  "✈ Flugdaten"),
    (2,  "🌤 Wetterdaten"),
    (3,  "⚓ Maritime Lage"),
    (4,  "📰 RSS + Telegram + Reddit"),
    (5,  "📍 Vorfalls-Marker"),
    (6,  "🌐 GDELT Geo-Events"),
    (7,  "🚢 AIS Schiffe"),
    (8,  "🌍 Erdbeben (USGS)"),
    (9,  "✈ NOTAMs / Luftsperren"),
    (10, "🔥 NASA FIRMS Brände"),
    (11, "💥 ACLED Konflikte"),
    (12, "🌪 NASA EONET Naturereignisse"),
    (13, "🛰 Satellit-Überflüge"),
    (14, "⚡ Blitzortung / Artillerie"),
    (15, "📡 GPS-Jamming"),
    (16, "🚢 Schiffs-Tiefgang Delta"),
    (17, "🔴 Eskalations-Score + LLM"),
    (18, "🎯 HUMINT-Extraktion"),
    (19, "🔗 Multi-Signal Fusion"),
    (20, "👁 Vision-KI (LLaVA)"),
    (21, "🗺 Bild-Geolokalisierung"),
    (22, "🚛 Konvoi & Traffic"),
    (23, "📷 Webcam Bewegung"),
    (24, "🛰 SAR Sentinel-1"),
    (25, "📻 WebSDR HF-Aktivität"),
    (26, "🕸 Info-Netz Analyse"),
    (27, "☢ Strahlungs-Monitoring"),
    (28, "🔄 Fusion Re-Run"),
]

def _set_step(num: int, name: str = "") -> None:
    """Aktualisiert den globalen Pipeline-Fortschritt (thread-safe)."""
    label = name or next((n for s, n in _STEP_NAMES if s == num), f"Schritt {num}")
    with _pstatus_lock:
        _pipeline_status["step"]    = num
        _pipeline_status["name"]    = label
        _pipeline_status["total"]   = 28
        _pipeline_status["pct"]     = int(num / 28 * 100)
        _pipeline_status["elapsed"] = round(
            _time_mod.monotonic() - _pipeline_status.get("started", _time_mod.monotonic()), 1)

# Maximales Alter eines Snapshots (nach 10min als "weg" werten)
GHOST_MAX_AGE_S  = 600
# Minimales Alter eines Eintrags im Snapshot bevor er als "weg" gilt
# (verhindert false-positives bei erster/zweiter Abfrage)
GHOST_MIN_AGE_S  = 180   # 3 Minuten – muss mind. 3 Polls im Snapshot gewesen sein
# Maximale Anzahl Ghost-Marker (Cap gegen Spam)
GHOST_MAX_COUNT  = 5


def _now_ts() -> float:
    return time.monotonic()


def _get_cached(query: str) -> dict | None:
    with _cache_lock:
        entry = _cache.get(query.lower())
        if entry and (_now_ts() - entry["ts"]) < CACHE_TTL:
            return entry["data"]
    return None


def _set_cache(query: str, data: dict) -> None:
    with _cache_lock:
        _cache[query.lower()] = {"ts": _now_ts(), "data": data}


# ── Daten-Pipeline ─────────────────────────────────────────────────────────────

def _fetch_live_data(query: str) -> dict:
    """Ruft alle verfügbaren Quellen für eine Query ab und gibt JSON-Dict zurück."""
    # Query bereinigen (entfernt # und URL-schädliche Sonderzeichen)
    query = _sanitize_query(query).strip()
    if not query:
        query = "Ukraine"

    # Geo-Region für API-Abfragen extrahieren (z.B. "Ukraine aktuell" → "Ukraine")
    geo_region = _extract_geo_region(query)

    # Pipeline-Status initialisieren
    with _pstatus_lock:
        _pipeline_status.update({
            "running": True, "step": 0, "pct": 0,
            "query": query, "started": _time_mod.monotonic(),
            "errors": [], "name": "⏳ Initialisierung…"
        })

    result: dict = {
        "query":      query,
        "geo_region": geo_region,
        "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        "flights":   None,
        "weather":   None,
        "maritime":  None,
        "incidents": [],
        "articles":  [],
    }

    _set_step(1)
    # 1. Flugdaten
    try:
        from nexus_flights import get_flights  # type: ignore
        fd = get_flights(geo_region)
        if fd and "error" not in fd:
            result["flights"] = {
                "region":     fd.get("region", query),
                "total":      fd.get("total", 0),
                "airborne":   fd.get("airborne", 0),
                "suspicious": len(fd.get("suspicious", [])),
                "center_lat": fd.get("center_lat"),
                "center_lon": fd.get("center_lon"),
                "aircraft":   [
                    {
                        "lat":         a.get("lat"),
                        "lon":         a.get("lon"),
                        "callsign":    a.get("callsign", "(kein)"),
                        "origin":      a.get("origin", "?"),
                        "altitude_ft": a.get("altitude_ft"),
                        "velocity_kmh":a.get("velocity_kmh"),
                        "track":       a.get("track"),
                        "suspicious":  a.get("suspicious", ""),
                    }
                    for a in (fd.get("aircraft") or [])
                    if a.get("lat") and a.get("lon")
                ],
            }
    except Exception as e:
        result["flights"] = {"error": str(e)}

    # 1b. Transponder-Off Detektion
    # Key-Logik: ICAO24 (eindeutig, ändert sich nie) > Callsign > Fallback-Verzicht
    # Kein lat/lon-Key mehr – Flugzeuge bewegen sich, das erzeugt false-positives!
    result["vanished_aircraft"] = []
    try:
        current_ac = (result.get("flights") or {}).get("aircraft") or []
        qkey = geo_region.lower()

        with _snapshot_lock:
            prev_snapshot = _aircraft_snapshots.get(qkey, {})

        if current_ac and prev_snapshot:
            # Aktuelle IDs sammeln: ICAO24 bevorzugt, sonst Callsign
            current_keys: set[str] = set()
            for ac in current_ac:
                icao = (ac.get("icao24") or "").strip().lower()
                cs   = (ac.get("callsign") or "").strip().upper()
                if icao:
                    current_keys.add(f"icao:{icao}")
                if cs and cs not in ("(KEIN)", "KEIN", ""):
                    current_keys.add(f"cs:{cs}")

            now_ts = _now_ts()
            vanished = []
            for key, prev in prev_snapshot.items():
                snap_age = now_ts - prev.get("_snap_ts", 0)
                # Zu alt oder zu frisch → überspringen
                if snap_age > GHOST_MAX_AGE_S:
                    continue
                if snap_age < GHOST_MIN_AGE_S:
                    continue  # Muss mind. 3min im Snapshot sein
                # Prüfe ob ICAO24 oder Callsign noch sichtbar
                if key in current_keys:
                    continue
                # Flugzeug war in der Luft (altitude > 500ft) und ist verschwunden
                alt = prev.get("altitude_ft") or 0
                if alt < 500:
                    continue
                # Zusatz: nur wenn Speed > 50km/h (kein langsames Manöver am Boden)
                spd = prev.get("velocity_kmh") or 0
                if spd < 50:
                    continue
                vanished.append({
                    "lat":         prev.get("lat"),
                    "lon":         prev.get("lon"),
                    "callsign":    prev.get("callsign", "(kein)"),
                    "icao24":      prev.get("icao24", ""),
                    "origin":      prev.get("origin", "?"),
                    "altitude_ft": prev.get("altitude_ft"),
                    "velocity_kmh":prev.get("velocity_kmh"),
                    "track":       prev.get("track"),
                    "suspicious":  prev.get("suspicious", ""),
                    "snap_age_s":  int(snap_age),
                })
                if len(vanished) >= GHOST_MAX_COUNT:
                    break  # Cap – nie mehr als 5 Ghost-Marker

            result["vanished_aircraft"] = vanished

        # Neuen Snapshot speichern – Keys: ICAO24 bevorzugt
        if current_ac:
            now_ts2   = _now_ts()
            new_snapshot: dict[str, dict] = {}
            for ac in current_ac:
                icao = (ac.get("icao24") or "").strip().lower()
                cs   = (ac.get("callsign") or "").strip().upper()
                lat  = ac.get("lat")
                lon  = ac.get("lon")
                if not lat or not lon:
                    continue
                # Eintrag bei ICAO24-Key speichern (primär)
                if icao:
                    new_snapshot[f"icao:{icao}"] = {**ac, "_snap_ts": now_ts2}
                # Zusätzlich bei Callsign-Key (für Flugzeuge die ICAO wechseln, sehr selten)
                elif cs and cs not in ("(KEIN)", "KEIN", ""):
                    new_snapshot[f"cs:{cs}"] = {**ac, "_snap_ts": now_ts2}
                # Kein lat/lon-Key mehr!
            with _snapshot_lock:
                _aircraft_snapshots[qkey] = new_snapshot

    except Exception as _e:
        result["vanished_aircraft"] = []

    _set_step(2)
    # 2. Wetterdaten
    try:
        from nexus_weather import get_weather  # type: ignore
        wd = get_weather(geo_region)
        if wd and "error" not in wd:
            ops = wd.get("ops", {})
            result["weather"] = {
                "location":      wd.get("location", query),
                "temperature_c": wd.get("temperature_c"),
                "wind_kmh":      wd.get("wind_kmh"),
                "weather_desc":  wd.get("weather_desc", "?"),
                "overall":       ops.get("overall", "gruen"),
                "dust_warning":  wd.get("dust_warning", False),
            }
    except Exception as e:
        result["weather"] = {"error": str(e)}

    _set_step(3)
    # 3. Maritime / Ereignisse
    try:
        from nexus_maritime import get_maritime_situation  # type: ignore
        md = get_maritime_situation(geo_region)
        if md and "error" not in md:
            result["maritime"] = {
                "region":      md.get("region", query),
                "alert_count": md.get("alert_count", 0),
                "alerts": [
                    {"title": a.get("title", "")[:80], "url": a.get("url", "#"),
                     "source": a.get("source", ""), "date": a.get("date", "")}
                    for a in (md.get("alerts") or [])[:6]
                ],
            }
    except Exception as e:
        result["maritime"] = {"error": str(e)}

    _set_step(4)
    # 4. RSS-News + Telegram + Reddit zusammenführen
    articles: list = []
    try:
        from nexus_rss import fetch_news  # type: ignore
        articles = fetch_news(fast=False, keyword_filter=query[:30]) or []
    except Exception:
        pass

    # 4a. Telegram OSINT-Kanäle + Surge-Detektion
    try:
        from nexus_telegram import fetch_osint_channels as _tg, detect_surges  # type: ignore
        tg_arts = _tg(keyword_filter=query, limit_per_channel=6, max_channels=4)
        # T175: NLP-Anreicherung für nicht-englische Telegram-Nachrichten
        try:
            from nexus_nlp import enrich_telegram_messages
            tg_arts = enrich_telegram_messages(tg_arts)
        except Exception:
            pass
        existing = {a.get("title", "") for a in articles}
        for a in tg_arts:
            if a.get("title", "") not in existing:
                articles.append(a)
                existing.add(a["title"])
        # Surge-Alerts
        surges = detect_surges(keyword_filter=query, top_n=5)
        result["telegram_surges"] = surges
    except Exception:
        result["telegram_surges"] = []

    # 4b. Reddit OSINT-Subreddits
    try:
        from nexus_reddit import fetch_osint_reddit as _reddit  # type: ignore
        reddit_arts = _reddit(keyword_filter=query, limit_per_sub=10, max_subs=3)
        existing = {a.get("title", "") for a in articles}
        for a in reddit_arts:
            if a.get("title", "") not in existing:
                articles.append(a)
                existing.add(a["title"])
    except Exception:
        pass

    # 4c. Social Media — Bluesky + Mastodon + VK
    try:
        from nexus_social import fetch_bluesky, fetch_mastodon  # type: ignore
        _soc_existing = {a.get("title", "") for a in articles}
        for _soc_fn in (fetch_bluesky, fetch_mastodon):
            try:
                _soc_arts = _soc_fn(query, limit=10)
                for _sa in _soc_arts:
                    if _sa.get("title", "") not in _soc_existing:
                        articles.append(_sa)
                        _soc_existing.add(_sa["title"])
            except Exception:
                pass
    except Exception:
        pass

    # ── Deduplication: gleiche Ereignisse aus mehreren Quellen zusammenführen ──
    articles_all_raw = sorted(articles, key=lambda x: x.get("age_min", 9999))
    try:
        from nexus_dedup import deduplicate as _dedup  # type: ignore
        articles_deduped = _dedup(articles_all_raw)
        result["dedup_stats"] = {
            "total_raw":   len(articles_all_raw),
            "total_deduped": len([a for a in articles_deduped if a.get("is_canonical")]),
            "duplicates_removed": len(articles_all_raw) - len([a for a in articles_deduped if a.get("is_canonical")]),
        }
    except Exception:
        articles_deduped = [dict(a, is_canonical=True, cluster_id=i,
                                  confidence="EINZELMELDUNG", corroborating=[],
                                  corroborating_count=0, cluster_size=1)
                             for i, a in enumerate(articles_all_raw)]
        result["dedup_stats"] = {"total_raw": len(articles_all_raw), "total_deduped": len(articles_all_raw), "duplicates_removed": 0}

    # ── Quellen-Glaubwürdigkeit enrichen (vor result["articles"]) ─────────────
    try:
        from nexus_credibility import enrich_articles as _cred_enrich  # type: ignore
        articles_deduped = _cred_enrich(articles_deduped)
    except Exception:
        pass

    # ── Konfidenz-Scoring enrichen ────────────────────────────────────────────
    try:
        from nexus_confidence import score_articles as _conf_score  # type: ignore
        articles_deduped = _conf_score(articles_deduped)
    except Exception:
        pass

    result["articles"] = [
        {
            "title":               a.get("title", "")[:100],
            "summary":             a.get("summary", "")[:200],
            "url":                 a.get("url", "#"),
            "source":              a.get("source", ""),
            "date":                a.get("date", ""),
            "age_min":             a.get("age_min", 9999),
            "credibility_label":   a.get("credibility_label", ""),
            "credibility_score":   a.get("credibility_score"),
            # Dedup-Metadaten
            "is_canonical":        a.get("is_canonical", True),
            "confidence":          a.get("confidence", "EINZELMELDUNG"),
            "corroborating":       a.get("corroborating", []),
            "corroborating_count": a.get("corroborating_count", 0),
            "cluster_size":        a.get("cluster_size", 1),
            "cluster_id":          a.get("cluster_id"),
        }
        for a in articles_deduped[:60]
    ]

    _set_step(5)
    # 5. Vorfalls-Marker geocodieren
    try:
        from nexus_report import _extract_incident_markers  # type: ignore
        result["incidents"] = _extract_incident_markers(result["articles"])
    except Exception:
        pass

    _set_step(6)
    # 6. GDELT Geo-Events
    try:
        from nexus_gdelt import fetch_gdelt_geo_events  # type: ignore
        gdelt_raw = fetch_gdelt_geo_events(geo_region, hours=24, max_points=30)
        # T175: NLP-Anreicherung für GDELT-Events
        try:
            from nexus_nlp import enrich_gdelt_events
            gdelt_raw = enrich_gdelt_events(gdelt_raw)
        except Exception:
            pass
        result["gdelt_points"] = gdelt_raw
    except Exception:
        result["gdelt_points"] = []

    _set_step(7)
    # 7. AIS Schiffspositionen
    try:
        from nexus_ais import vessels_for_map  # type: ignore
        result["ais_vessels"] = vessels_for_map(geo_region)
    except Exception as _e:
        import sys as _sys
        print(f"[AIS-Server] Fehler: {_e}", file=_sys.stderr)
        result["ais_vessels"] = []

    # 7b. AIS-Dark Detektion (Schiffe die vom Radar verschwinden)
    result["vanished_vessels"] = []
    try:
        current_vessels = result.get("ais_vessels") or []
        qkey = geo_region.lower()

        with _snapshot_lock:
            prev_vsnap = _vessel_snapshots.get(qkey, {})

        if current_vessels and prev_vsnap:
            # Aktuelle Schiffe als Set (Schluessel: MMSI wenn vorhanden, sonst Name)
            current_vkeys: set[str] = set()
            for v in current_vessels:
                mmsi = (v.get("mmsi") or "").strip()
                name = (v.get("name") or "").strip()
                k = mmsi if mmsi else (name if name and name != "(unbekannt)" else f"{v.get('lat',0):.2f},{v.get('lon',0):.2f}")
                current_vkeys.add(k)

            now_ts = _now_ts()
            vanished_v = []
            for key, prev in prev_vsnap.items():
                snap_age = now_ts - prev.get("_snap_ts", 0)
                if snap_age > GHOST_MAX_AGE_S:
                    continue
                if key in current_vkeys:
                    continue
                # War das Schiff in Fahrt? (Geschwindigkeit > 0.5 kn)
                spd = prev.get("speed") or prev.get("speed_kn") or 0
                if spd < 0.5:
                    continue  # lag vor Anker → kein Ghost
                vanished_v.append({
                    "lat":       prev.get("lat"),
                    "lon":       prev.get("lon"),
                    "name":      prev.get("name", "(unbekannt)"),
                    "mmsi":      prev.get("mmsi", ""),
                    "type":      prev.get("type", ""),
                    "flag":      prev.get("flag", ""),
                    "speed":     spd,
                    "heading":   prev.get("heading"),
                    "snap_age_s": int(snap_age),
                })
            result["vanished_vessels"] = vanished_v

        # Neuen Schiffs-Snapshot speichern
        if current_vessels:
            new_vsnap: dict[str, dict] = {}
            snap_ts = _now_ts()
            for v in current_vessels:
                mmsi = (v.get("mmsi") or "").strip()
                name = (v.get("name") or "").strip()
                k = mmsi if mmsi else (name if name and name != "(unbekannt)" else f"{v.get('lat',0):.2f},{v.get('lon',0):.2f}")
                new_vsnap[k] = {**v, "_snap_ts": snap_ts}
            with _snapshot_lock:
                _vessel_snapshots[qkey] = new_vsnap

    except Exception:
        result["vanished_vessels"] = []

    # result["ais"] mit echten Daten befüllen (JS liest d.ais.vessels)
    result["ais"] = {
        "vessels":           result.get("ais_vessels", []),
        "vanishing_vessels": result.get("vanished_vessels", []),
        "vessel_count":      len(result.get("ais_vessels", [])),
    }

    _set_step(8)
    # 8. Erdbeben (USGS)
    try:
        from nexus_seismic import earthquakes_for_map  # type: ignore
        result["earthquakes"] = earthquakes_for_map(geo_region, hours=48)
    except Exception:
        result["earthquakes"] = []

    _set_step(9)
    # 9. NOTAMs
    try:
        from nexus_notam import notams_for_map  # type: ignore
        result["notams"] = notams_for_map(geo_region)
    except Exception:
        result["notams"] = []

    _set_step(10)
    # 10. NASA FIRMS Brände (benötigt FIRMS_MAP_KEY in config.py)
    try:
        from nexus_firms import fires_for_map  # type: ignore
        result["fires"] = fires_for_map(geo_region)
    except Exception:
        result["fires"] = []

    _set_step(11)
    # 11. ACLED/UCDP Konfliktereignisse mit GPS (ACLED 403 → UCDP-Fallback)
    try:
        from nexus_acled import acled_for_map  # type: ignore
        _acled_pts = acled_for_map(geo_region, days=14)
        result["acled"]        = _acled_pts  # für Heatmap (nexus_livemap updateHeatmap)
        result["acled_events"] = _acled_pts  # für Marker (nexus_livemap rAcled)
    except Exception:
        result["acled"]        = []
        result["acled_events"] = []

    _set_step(12)
    # 12. NASA EONET – Naturereignisse (kostenlos, kein Key)
    try:
        from nexus_eonet import eonet_for_map  # type: ignore
        result["eonet"] = eonet_for_map(geo_region)
    except Exception:
        result["eonet"] = []

    # 12b. GDACS – UN OCHA Katastrophenwarnungen (kostenlos, kein Key)
    try:
        from nexus_gdacs import fetch_gdacs_events  # type: ignore
        result["gdacs_events"] = fetch_gdacs_events(geo_region, days=365)
    except Exception:
        result["gdacs_events"] = []

    # 12c. Nuklearanlagen-Karte (statische IAEA-Daten, kein Key)
    try:
        from nexus_nuclear import nuclear_for_map  # type: ignore
        result["nuclear_sites"] = nuclear_for_map(geo_region, include_shutdown=False)
    except Exception:
        result["nuclear_sites"] = []

    # 12d. Ransomware-Opfer (opt-in: RANSOMWARE_LIVE_ENABLED = True in config.py)
    try:
        from nexus_ransomware import fetch_ransomware_events  # type: ignore
        result["ransomware_events"] = fetch_ransomware_events(geo_region, days=30)
    except Exception:
        result["ransomware_events"] = []

    _set_step(13)
    # 13. Satellit-Ueberflug-Timer
    try:
        from nexus_satellite_timing import next_passes  # type: ignore
        result["satellite_passes"] = next_passes(geo_region)
    except Exception:
        result["satellite_passes"] = []

    _set_step(14)
    # 14. Blitzortung / Artillerie-Signal
    try:
        from nexus_lightning import lightning_for_map  # type: ignore
        result["lightning_signals"] = lightning_for_map(geo_region)
    except Exception:
        result["lightning_signals"] = []

    _set_step(15)
    # 15b. GPS-Jamming Detektion
    try:
        from nexus_gpsjam import gpsjam_for_map  # type: ignore
        result["gpsjam_zones"] = gpsjam_for_map(geo_region)
    except Exception:
        result["gpsjam_zones"] = []

    _set_step(16)
    # 15. Schiffs-Tiefgang Delta (AIS Ladungsveraenderung)
    try:
        from nexus_draught import record_draught, draught_for_map  # type: ignore
        ais_vessels = result.get("ais_vessels") or []
        maritime    = result.get("maritime") or {}
        mt_vessels  = (maritime.get("vessels") or maritime.get("alerts") or [])
        all_vessels = ais_vessels + mt_vessels
        new_draught_alerts        = record_draught(all_vessels)
        result["draught_alerts"]  = draught_for_map(geo_region)
        result["new_draught_hits"] = len(new_draught_alerts)
    except Exception:
        result["draught_alerts"]  = []
        result["new_draught_hits"] = 0

    _set_step(17)
    # 16. Eskalations-Score (alle Signale fusionieren) + LLM-Anreicherung (Ebene 4)
    try:
        from nexus_escalation import compute_escalation_with_llm  # type: ignore
        esc = compute_escalation_with_llm(result, geo_region)
        result["escalation"] = esc
        # Score-History für Predictive Analytics (Ebene 4)
        try:
            from nexus_predict import auto_record_from_escalation  # type: ignore
            auto_record_from_escalation(esc)
        except Exception:
            pass
    except ImportError:
        try:
            from nexus_escalation import compute_escalation  # type: ignore
            result["escalation"] = compute_escalation(result, query)
        except Exception:
            result["escalation"] = {"score": 0, "level": "GRUEN", "color": "#00ff88",
                                    "icon": "🟢", "signal_count": 0, "signal_details": []}
    except Exception:
        result["escalation"] = {"score": 0, "level": "GRUEN", "color": "#00ff88",
                                "icon": "🟢", "signal_count": 0, "signal_details": []}

    _set_step(18)
    # 17. HUMINT-Extraktion aus Artikeln (Ebene 4)
    try:
        from nexus_humint import humint_for_map  # type: ignore
        articles_all = (result.get("articles") or [])
        result["humint_markers"] = humint_for_map(articles_all, query)
    except Exception:
        result["humint_markers"] = []

    _set_step(19)
    # 18. Multi-Signal Fusion (Ebene 4)
    try:
        from nexus_fusion import fusion_for_map, fusion_context  # type: ignore
        result["fusion_threats"] = fusion_for_map(result, query)
        # Fusion-Kontext für LLM-Analyse anhängen
        fctx = fusion_context(result, query)
        if fctx:
            result["fusion_context"] = fctx
    except Exception:
        result["fusion_threats"] = []
        result["fusion_context"] = ""

    # ── Ebene 4 Erweiterungsmodule ────────────────────────────────────────────

    _set_step(20)
    # 19. Vision-Analyse – LLaVA Bild-KI (Fahrzeuge, Einheiten, Schäden)
    try:
        from nexus_vision import vision_for_map  # type: ignore
        articles_v = result.get("articles") or []
        result["vision_markers"] = vision_for_map(articles_v, query, max_images=8)
    except Exception:
        result["vision_markers"] = []

    _set_step(21)
    # 20. Bild-Geolokalisierung (EXIF + Schatten + OCR)
    try:
        from nexus_geolocate import geolocate_articles  # type: ignore
        articles_g = result.get("articles") or []
        result["geo_markers"] = geolocate_articles(articles_g, query)
    except Exception:
        result["geo_markers"] = []

    _set_step(22)
    # 21. Konvoi & Traffic-Anomalie-Detektion
    try:
        from nexus_movement import movement_for_map  # type: ignore
        result["movement_alerts"] = movement_for_map(geo_region)
    except Exception:
        result["movement_alerts"] = []

    _set_step(23)
    # 22. Öffentliche Kameras + Bewegungsdetektion
    try:
        from nexus_webcam import webcam_for_map  # type: ignore
        result["webcam_alerts"] = webcam_for_map(geo_region)
    except Exception:
        result["webcam_alerts"] = []

    _set_step(24)
    # 23. Sentinel-1 SAR – Fahrzeug-/Metalldetektion
    try:
        from nexus_sar import sar_for_map  # type: ignore
        result["sar_passes"] = sar_for_map(geo_region)
    except Exception:
        result["sar_passes"] = []

    # 23b. Sentinel-2 Satellitenszene (Copernicus)
    try:
        from nexus_sentinel import sentinel_summary as _sent_sum, get_latest_scene_info as _sent_scene  # type: ignore
        from nexus_flights import REGIONS as _SENT_REGS  # type: ignore
        _slat = _slon = 0.0
        if geo_region and geo_region in _SENT_REGS:
            _b = _SENT_REGS[geo_region]
            _slat = (_b[0] + _b[2]) / 2.0
            _slon = (_b[1] + _b[3]) / 2.0
        if _slat or _slon:
            try:
                import config as _sc  # type: ignore
                _cid  = getattr(_sc, "COPERNICUS_CLIENT_ID",     "")
                _csec = getattr(_sc, "COPERNICUS_CLIENT_SECRET", "")
            except ImportError:
                _cid, _csec = "", ""
            result["sentinel"] = {
                "scene_info":    _sent_scene(_slat, _slon, _cid, _csec),
                "summary":       _sent_sum(geo_region or query, _slat, _slon, _cid, _csec),
                "lat":           _slat,
                "lon":           _slon,
            }
        else:
            result["sentinel"] = {}
    except Exception:
        result["sentinel"] = {}

    _set_step(25)
    # 24. WebSDR HF-Aktivitäts-Monitor
    try:
        from nexus_websdr import websdr_for_map  # type: ignore
        result["hf_signals"] = websdr_for_map(geo_region)
    except Exception:
        result["hf_signals"] = []

    _set_step(26)
    # 25. Informations-Netzwerk-Analyse (Astroturfing, Surge, Propagation)
    try:
        from nexus_netgraph import analyze_propagation, netgraph_summary  # type: ignore
        articles_ng = result.get("articles") or []
        ng_result = analyze_propagation(articles_ng)
        result["netgraph"] = {
            "surge_topics":         ng_result.surge_topics,
            "astroturfing_alerts":  ng_result.astroturfing_alerts,
            "top_origin_channels":  ng_result.top_origin_channels,
            "story_count":          len(ng_result.story_clusters),
            "summary":              netgraph_summary(ng_result),
        }
    except Exception:
        result["netgraph"] = {}

    _set_step(27)
    # 26. Strahlungs-Monitoring (EPA + IAEA + EURDEP + Safecast)
    try:
        from nexus_radnet import radiation_for_map  # type: ignore
        result["radiation_alerts"] = radiation_for_map(geo_region)
    except Exception:
        result["radiation_alerts"] = []

    _set_step(28)

    # ── VIIRS Nachtlichter ────────────────────────────────────────────────────
    try:
        from nexus_viirs import get_viirs_for_map  # type: ignore
        result["viirs_dark"] = get_viirs_for_map(geo_region)
    except Exception:
        result["viirs_dark"] = []

    # ── Gesundheits-Frühwarnung (WHO/ProMED) ──────────────────────────────────
    try:
        from nexus_health import get_health_for_map  # type: ignore
        result["health_alerts"] = get_health_for_map(geo_region)
    except Exception:
        result["health_alerts"] = []

    # ── Sanktions-Abgleich (OFAC/EU/UN) – AIS-Schiffe ─────────────────────────
    try:
        from nexus_sanctions import get_sanctions_for_map  # type: ignore
        result["sanctions_hits"] = get_sanctions_for_map(
            result.get("ais_vessels", [])
        )
    except Exception:
        result["sanctions_hits"] = []

    # ── BGP-Routing-Anomalien ─────────────────────────────────────────────────
    try:
        from nexus_bgp import get_bgp_for_map  # type: ignore
        result["bgp_anomalies"] = get_bgp_for_map()
    except Exception:
        result["bgp_anomalies"] = []

    # ── Vertreibungs-Tracking (UNHCR/IOM) ────────────────────────────────────
    try:
        from nexus_displacement import get_displacement_for_map  # type: ignore
        result["displacement"] = get_displacement_for_map(geo_region)
    except Exception:
        result["displacement"] = []

    # ── Frontlinien-GeoJSON (DeepStateMap / ISW) ────────────────────────────
    try:
        from nexus_frontline import fetch_frontline  # type: ignore
        result["frontline_geojson"] = fetch_frontline()
    except Exception:
        result["frontline_geojson"] = None

    # ── Wissens-Graph: Artikel einpflegen + Karten-Daten ─────────────────────
    try:
        from nexus_knowledge import ingest_articles as _kg_ingest, get_map_data as _kg_map  # type: ignore
        if articles:
            _kg_ingest(articles)
        result["knowledge_map"] = _kg_map(hours=48)
    except Exception:
        result["knowledge_map"] = {"active_units": [], "hotspots": [], "unit_movements": []}

    # ── Netzwerk-Propagations-Analyse (T130) ──────────────────────────────────
    try:
        from nexus_netprop import analyze_articles_propagation as _np_analyze  # type: ignore
        if articles:
            _np_result = _np_analyze(articles)
            result["netprop"] = {
                "coordination_alerts": _np_result.get("coordination_alerts", []),
                "state_amplification_events": _np_result.get("state_amplification_events", []),
                "top_first_reporters": _np_result.get("top_first_reporters", []),
                "global_stats": _np_result.get("global_stats", {}),
            }
        else:
            result["netprop"] = {}
    except Exception:
        result["netprop"] = {}

    # ── Video-Analyse (T128) — Telegram Video-Posts ───────────────────────────
    try:
        from nexus_video import analyze_video_url as _vid_url, videos_for_llm as _vid_llm  # type: ignore
        _video_results = []
        for _art in (articles or [])[:15]:
            _art_url = _art.get("url", "")
            _art_src = _art.get("source", "")
            if ("t.me" in _art_url or "telegram" in _art_src.lower()) and _art_url:
                _vr = _vid_url(_art_url)
                if _vr.get("analysis") and not _vr.get("error"):
                    _video_results.append(_vr["analysis"])
        result["video_summary"] = _vid_llm(_video_results) if _video_results else ""
        result["video_count"] = len(_video_results)
    except Exception:
        result["video_summary"] = ""
        result["video_count"] = 0

    # ── Timeline-Analyse (T141) — Ereignis-Chronologie ───────────────────────
    try:
        from nexus_timeline import timeline_for_llm as _tl_llm, build_and_render as _tl_render  # type: ignore
        if articles:
            result["timeline_context"] = _tl_llm(articles, max_events=20)
            # HTML-Timeline als Datei speichern (optional, kein auto_open im Server)
            try:
                import os as _os
                _tl_save = _os.path.dirname(_os.path.abspath(__file__))
                _tl_render(articles, topic=query[:30], save_dir=_tl_save, auto_open=False)
            except Exception:
                pass
        else:
            result["timeline_context"] = ""
    except Exception:
        result["timeline_context"] = ""

    # ── WHOIS / Domain-Attribution (T141) ────────────────────────────────────
    try:
        from nexus_whois import analyze_article_sources as _whois_src, whois_for_llm as _whois_fmt  # type: ignore
        if articles:
            _whois_data = _whois_src(articles, max_domains=8)
            result["whois"] = {
                "domain_analyses": _whois_data.get("domain_analyses", []),
                "disinfo_count": len([
                    d for d in _whois_data.get("domain_analyses", [])
                    if d.get("risk_score", 0) >= 50
                ]),
                "llm_context": _whois_fmt(_whois_data),
            }
        else:
            result["whois"] = {}
    except Exception:
        result["whois"] = {}

    # ── Dokument-Metadaten OSINT (T141) — PDFs/DOCX in Artikel-Links ─────────
    try:
        from nexus_docmeta import analyze_document_url as _doc_url, docmeta_for_llm as _doc_fmt  # type: ignore
        _doc_hits = []
        for _art in (articles or [])[:30]:
            _aurl = _art.get("url", "")
            if _aurl and any(_aurl.lower().endswith(ext)
                             for ext in (".pdf", ".docx", ".xlsx", ".pptx")):
                _dr = _doc_url(_aurl)
                if not _dr.get("error"):
                    _doc_hits.append(_dr)
        result["docmeta"] = {
            "documents": _doc_hits,
            "count": len(_doc_hits),
            "llm_context": _doc_fmt(_doc_hits) if _doc_hits else "",
        }
    except Exception:
        result["docmeta"] = {"documents": [], "count": 0, "llm_context": ""}

    # ── Übersetzung fremdsprachiger Artikel (T145) ───────────────────────────
    try:
        from nexus_translate import enrich_articles_with_translation as _tr_enrich, translation_status as _tr_status  # type: ignore
        _tr_arts = result.get("articles", [])
        if _tr_arts:
            _tr_enriched = _tr_enrich([dict(a) for a in _tr_arts])
            # Translation-Status für Dashboard
            result["translation_stats"] = _tr_status()
            # Übersetzte Titel/Zusammenfassungen in result["articles"] zurückschreiben
            _tr_map = {a.get("url", ""): a for a in _tr_enriched}
            result["articles"] = [
                dict(a, **{
                    "title_translated": _tr_map.get(a.get("url", ""), {}).get("title_translated", ""),
                    "summary_translated": _tr_map.get(a.get("url", ""), {}).get("summary_translated", ""),
                    "lang": _tr_map.get(a.get("url", ""), {}).get("lang", ""),
                }) for a in result["articles"]
            ]
        else:
            result["translation_stats"] = {}
    except Exception:
        result["translation_stats"] = {}

    # ── Wikipedia-Hintergrundkontext (T145) ──────────────────────────────────
    try:
        from nexus_wiki import wiki_inject_for_query as _wiki_ctx  # type: ignore
        result["wiki_context"] = _wiki_ctx(query)
    except Exception:
        result["wiki_context"] = ""

    # ── T149: Fehlende Anreicherungs-Module ──────────────────────────────────

    # NER — Named Entity Recognition → enrichiert Artikel + gibt Entitäts-Kontext
    try:
        from nexus_ner import enrich_articles_with_ner as _ner_enrich, ner_context_for_llm as _ner_ctx_fn  # type: ignore
        if result.get("articles"):
            result["articles"] = _ner_enrich(result["articles"])
        result["ner_context"] = _ner_ctx_fn(result.get("articles", []))
    except Exception:
        result["ner_context"] = ""

    # Bild-Metadaten OSINT (EXIF + ELA + Sonnenwinkel)
    try:
        from nexus_imgmeta import analyze_image_url as _imgm_url, imgmeta_for_llm as _imgm_fmt  # type: ignore
        _imgm_hits = []
        for _art in (result.get("articles") or [])[:20]:
            _aurl = _art.get("image_url") or _art.get("url", "")
            if _aurl and any(_aurl.lower().endswith(ext)
                             for ext in (".jpg", ".jpeg", ".png", ".webp")):
                _ir = _imgm_url(_aurl)
                if not _ir.get("error") and _ir.get("verdict"):
                    _imgm_hits.append(_ir)
        result["imgmeta"] = _imgm_hits
        result["imgmeta_context"] = _imgm_fmt(_imgm_hits) if _imgm_hits else ""
    except Exception:
        result["imgmeta"] = []
        result["imgmeta_context"] = ""

    # Reisesicherheits-Bewertung
    try:
        from nexus_travel_safety import travel_safety_report as _ts_report, format_travel_brief as _ts_fmt  # type: ignore
        _ts_r = _ts_report(
            query,
            escalation_score=result.get("escalation", {}).get("score", 0),
        )
        result["travel_safety"] = {
            "report": _ts_r,
            "brief": _ts_fmt(_ts_r),
        }
    except Exception:
        result["travel_safety"] = {}

    # Hintergrund-Watchlist — Status + aktive Terme
    try:
        from nexus_watchlist import show as _wl_show, is_running as _wl_running  # type: ignore
        result["watchlist_status"] = {
            "running": _wl_running(),
            "terms": _wl_show(),
        }
    except Exception:
        result["watchlist_status"] = {}

    # Maritime Anomalie-Detektion (AIS → Chokepoints + STS + Stop-Anomalien)
    try:
        from nexus_maritime_anomaly import analyse_vessels as _maa_fn, anomaly_text_summary as _maa_txt  # type: ignore
        _maa_v = result.get("ais_vessels", [])
        if _maa_v:
            _maa_res = _maa_fn(_maa_v)
            result["maritime_anomaly"] = {
                "result": _maa_res,
                "summary": _maa_txt(_maa_res),
            }
        else:
            result["maritime_anomaly"] = {}
    except Exception:
        result["maritime_anomaly"] = {}

    # HF Maritime — Kurzwellen-Schiffsfrequenzen
    try:
        from nexus_hf_maritime import hf_activity_for_region as _hfm_fn  # type: ignore
        result["hf_maritime"] = _hfm_fn(geo_region or query)
    except Exception:
        result["hf_maritime"] = {}

    # Iridium / Inmarsat — Satelliten-Kommunikations-Aktivität
    try:
        from nexus_iridium import satellite_comms_for_region as _irid_fn  # type: ignore
        result["iridium"] = _irid_fn(geo_region or query)
    except Exception:
        result["iridium"] = {}

    # ── NEXUS SitRep (T147) — vollständiger Intelligence Brief ───────────────
    try:
        from nexus_sitrep import generate_sitrep as _sr_gen, sitrep_to_html as _sr_html  # type: ignore
        _sr_full = generate_sitrep(result, query)  # type: ignore[name-defined] # noqa
        _sr_full = _sr_gen(result, query)
        result["sitrep"] = _sr_full
        # HTML-Brief als Datei speichern
        try:
            import os as _os2
            _sr_path = _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)),
                                      "nexus_sitrep_latest.html")
            _sr_html(_sr_full, save_path=_sr_path)
        except Exception:
            pass
    except Exception:
        result["sitrep"] = {}

    _set_step(29)
    # Nach allen neuen Signalen: Fusion nochmals aktualisieren
    try:
        from nexus_fusion import fusion_for_map as _ffm, fusion_context as _fctx  # type: ignore
        result["fusion_threats"] = _ffm(result, query)
        fctx2 = _fctx(result, query)
        if fctx2:
            result["fusion_context"] = fctx2
    except Exception:
        pass

    # Wirtschaftsindikatoren hinzufügen (T165: Livekarte Wirtschafts-Layer)
    try:
        from nexus_economics import get_economic_indicators  # type: ignore
        result["economics"] = get_economic_indicators()
    except Exception:
        result["economics"] = {}

    # T171: Militär-Versorgungsschiff Tracking (USNS/NATO/CHN/RUS)
    try:
        from nexus_usns import naval_ships_for_map, naval_summary  # type: ignore
        result["naval_supply"] = naval_ships_for_map()
        result["naval_summary"] = naval_summary()
    except Exception:
        result["naval_supply"] = []
        result["naval_summary"] = {}

    # T172: Militärflüge als Blockade-Indikator
    try:
        from nexus_milflights import milflights_for_map, milflights_summary  # type: ignore
        result["milflights"] = milflights_for_map()
        result["milflights_summary"] = milflights_summary()
    except Exception:
        result["milflights"] = []
        result["milflights_summary"] = {}

    # T173: Zeitreihen-Snapshot speichern
    try:
        from nexus_timeseries import record_refresh_snapshot
        record_refresh_snapshot(result)
    except Exception:
        pass

    # T177: FININT — Sanktions-Check für AIS-Schiffe
    try:
        from nexus_finint import finint_for_map, finint_summary
        result["finint_alerts"]  = finint_for_map(result.get("ais_ships", []))
        result["finint_summary"] = finint_summary()
    except Exception:
        result["finint_alerts"]  = []
        result["finint_summary"] = {}

    # T178: Calibration — Auto-Log wenn Score >= MITTEL
    try:
        from nexus_calibration import auto_log_from_escalation
        esc_data = result.get("escalation_report") or {}
        if esc_data:
            auto_log_from_escalation(esc_data)
    except Exception:
        pass

    # T174: Pattern-of-Life Anomalien
    try:
        from nexus_patrol import patrol_for_map, patrol_summary
        result["patrol_anomalies"] = patrol_for_map()
        result["patrol_summary"]   = patrol_summary()
    except Exception:
        result["patrol_anomalies"] = []
        result["patrol_summary"]   = {}

    # T176: Dark Web OSINT Monitor
    try:
        from nexus_darkweb import darkweb_for_map, darkweb_summary, tor_status
        dw_status = tor_status()
        if dw_status.get("tor_online"):
            result["darkweb_alerts"]  = darkweb_for_map()
            result["darkweb_summary"] = darkweb_summary()
        else:
            result["darkweb_alerts"]  = []
            result["darkweb_summary"] = {"tor_online": False, "level": "NORMAL"}
    except Exception:
        result["darkweb_alerts"]  = []
        result["darkweb_summary"] = {}

    # ── AIS-Daten in die von der Livekarte erwartete Struktur verpacken ─────────
    # nexus_livemap.js liest: d.ais.vessels  und  d.ais.vanishing_vessels
    # Ohne dieses Mapping kommen Schiffe NIEMALS auf der Karte an!
    result["ais"] = {
        "vessels":           result.get("ais_vessels", []),
        "vanishing_vessels": result.get("vanished_vessels", []),
        "vessel_count":      len(result.get("ais_vessels", [])),
    }

    # Pipeline abgeschlossen
    with _pstatus_lock:
        _pipeline_status.update({
            "running": False, "step": 28, "pct": 100,
            "name": "✅ Fertig",
            "elapsed": round(_time_mod.monotonic() - _pipeline_status.get("started", 0), 1),
        })
    return result


# ── Modul-Status HTML-Generator ───────────────────────────────────────────────

def _build_modules_html(port: int = 11430) -> str:
    """Erzeugt die /modules Statusseite für alle 83 NEXUS-Module."""
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEXUS — Modul-Status</title>
<style>
  :root {{
    --bg: #0a0e1a; --bg2: #111827; --bg3: #1a2235;
    --ok: #00d26a; --leer: #4a9eff; --fehler: #ff4444;
    --text: #e2e8f0; --muted: #64748b; --border: #2d3748;
    --accent: #00d26a;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif;
          min-height: 100vh; }}
  header {{ background: var(--bg2); border-bottom: 1px solid var(--border);
            padding: 18px 24px; display: flex; align-items: center; gap: 16px;
            position: sticky; top: 0; z-index: 100; }}
  header h1 {{ font-size: 1.25rem; font-weight: 700; letter-spacing: .5px; }}
  header h1 span {{ color: var(--accent); }}
  .nav-links {{ margin-left: auto; display: flex; gap: 12px; }}
  .nav-links a {{ color: var(--muted); text-decoration: none; font-size: .85rem;
                  padding: 6px 12px; border-radius: 6px; border: 1px solid var(--border);
                  transition: all .2s; }}
  .nav-links a:hover {{ color: var(--text); border-color: var(--accent); }}

  .summary-bar {{ display: flex; gap: 12px; padding: 20px 24px; flex-wrap: wrap; }}
  .stat-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
                padding: 14px 20px; min-width: 130px; text-align: center; }}
  .stat-card .num {{ font-size: 2rem; font-weight: 800; line-height: 1; }}
  .stat-card .lbl {{ font-size: .75rem; color: var(--muted); margin-top: 4px; text-transform: uppercase;
                     letter-spacing: .5px; }}
  .stat-ok {{ color: var(--ok); border-color: rgba(0,210,106,.3); }}
  .stat-leer {{ color: var(--leer); border-color: rgba(74,158,255,.3); }}
  .stat-fehler {{ color: var(--fehler); border-color: rgba(255,68,68,.3); }}
  .stat-total {{ color: var(--text); }}

  .progress-wrap {{ padding: 0 24px 16px; }}
  .progress-bg {{ background: var(--bg3); border-radius: 999px; height: 8px; overflow: hidden; }}
  .progress-fill {{ height: 100%; background: linear-gradient(90deg, var(--ok), #00a855);
                    border-radius: 999px; transition: width .8s ease; }}
  .progress-label {{ font-size: .78rem; color: var(--muted); margin-top: 6px; }}

  .controls {{ padding: 0 24px 16px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
  .search-box {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
                 padding: 8px 14px; color: var(--text); font-size: .9rem; width: 280px;
                 outline: none; }}
  .search-box:focus {{ border-color: var(--accent); }}
  .filter-btn {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px;
                 padding: 8px 14px; color: var(--muted); font-size: .85rem; cursor: pointer;
                 transition: all .2s; }}
  .filter-btn.active, .filter-btn:hover {{ color: var(--text); border-color: var(--accent); }}
  .run-btn {{ margin-left: auto; background: var(--accent); color: #000; border: none;
              border-radius: 8px; padding: 9px 18px; font-weight: 700; font-size: .9rem;
              cursor: pointer; transition: opacity .2s; }}
  .run-btn:hover {{ opacity: .85; }}
  .run-btn:disabled {{ opacity: .4; cursor: not-allowed; }}

  .section-title {{ padding: 8px 24px 4px; font-size: .7rem; color: var(--muted);
                    text-transform: uppercase; letter-spacing: 1px; font-weight: 700; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
           gap: 10px; padding: 4px 24px 24px; }}

  .card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
           padding: 14px 16px; display: flex; flex-direction: column; gap: 6px;
           transition: border-color .2s, transform .15s; cursor: default; }}
  .card:hover {{ border-color: rgba(255,255,255,.2); transform: translateY(-1px); }}
  .card.ok   {{ border-left: 3px solid var(--ok); }}
  .card.leer {{ border-left: 3px solid var(--leer); }}
  .card.fehler {{ border-left: 3px solid var(--fehler); }}
  .card-header {{ display: flex; align-items: center; gap: 8px; }}
  .badge {{ font-size: .65rem; font-weight: 800; padding: 2px 7px; border-radius: 999px;
            text-transform: uppercase; letter-spacing: .5px; }}
  .badge-ok    {{ background: rgba(0,210,106,.15); color: var(--ok); }}
  .badge-leer  {{ background: rgba(74,158,255,.15); color: var(--leer); }}
  .badge-fehler {{ background: rgba(255,68,68,.15); color: var(--fehler); }}
  .mod-name {{ font-size: .88rem; font-weight: 600; color: var(--text); flex: 1;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .mod-time {{ font-size: .75rem; color: var(--muted); }}
  .mod-label {{ font-size: .78rem; color: var(--muted); white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; }}
  .error-msg {{ font-size: .75rem; color: var(--fehler); background: rgba(255,68,68,.1);
                padding: 5px 8px; border-radius: 5px; word-break: break-word;
                max-height: 60px; overflow-y: auto; }}
  .sample-box {{ font-size: .72rem; color: var(--muted); background: rgba(255,255,255,.03);
                 padding: 5px 8px; border-radius: 5px; max-height: 48px; overflow-y: auto;
                 word-break: break-word; display: none; }}
  .card:hover .sample-box {{ display: block; }}

  .timestamp {{ padding: 0 24px 6px; font-size: .78rem; color: var(--muted); }}
  .hidden {{ display: none !important; }}

  .spinner {{ display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(0,210,106,.3);
              border-top-color: var(--ok); border-radius: 50%; animation: spin .6s linear infinite;
              vertical-align: middle; margin-right: 6px; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>

<header>
  <div>🛰</div>
  <h1>NEXUS <span>Modul-Status</span></h1>
  <div class="nav-links">
    <a href="http://localhost:{port}/">⬅ Dashboard</a>
    <a href="http://localhost:{port}/livemap">🗺 Karte</a>
    <a href="http://localhost:{port}/timeline">📅 Timeline</a>
    <a href="http://localhost:{port}/maritime">⚓ Maritim</a>
    <a href="http://localhost:{port}/source_health">🩺 Quellen-Live</a>
  </div>
</header>

<div class="summary-bar" id="summaryBar">
  <div class="stat-card stat-total"><div class="num" id="numTotal">–</div><div class="lbl">Module</div></div>
  <div class="stat-card stat-ok">   <div class="num" id="numOk">–</div>   <div class="lbl">✅ OK</div></div>
  <div class="stat-card stat-leer"> <div class="num" id="numLeer">–</div>  <div class="lbl">🔵 Kein Signal</div></div>
  <div class="stat-card stat-fehler"><div class="num" id="numFehler">–</div><div class="lbl">❌ Fehler</div></div>
</div>

<div class="progress-wrap">
  <div class="progress-bg"><div class="progress-fill" id="progressBar" style="width:0%"></div></div>
  <div class="progress-label" id="progressLabel">Lade Diagnose-Daten…</div>
</div>

<div class="controls">
  <input class="search-box" type="text" id="searchBox" placeholder="🔍  Modul suchen…" oninput="filterCards()">
  <button class="filter-btn active" id="btnAll"    onclick="setFilter('all')">Alle</button>
  <button class="filter-btn"        id="btnOk"     onclick="setFilter('ok')">✅ OK</button>
  <button class="filter-btn"        id="btnLeer"   onclick="setFilter('leer')">🔵 Kein Signal</button>
  <button class="filter-btn"        id="btnFehler" onclick="setFilter('fehler')">❌ Fehler</button>
  <button class="run-btn" id="runBtn" onclick="triggerDiag()">▶ Diagnose starten</button>
</div>

<div id="timestamp" class="timestamp"></div>
<div id="cardContainer"></div>

<script>
const API = '/api/modules';
let allData = {{}};
let currentFilter = 'all';

const CATEGORIES = {{
  'Datenquellen': ['nexus_rss','nexus_flights','nexus_maritime','nexus_ais','nexus_seismic',
    'nexus_firms','nexus_acled','nexus_frontline','nexus_gpsjam','nexus_lightning',
    'nexus_eonet','nexus_notam','nexus_weather','nexus_gdelt','nexus_reddit','nexus_telegram',
    'nexus_social','nexus_radnet','nexus_satellite_timing','nexus_draught','nexus_viirs',
    'nexus_bgp','nexus_displacement','nexus_health','nexus_sanctions','nexus_economics',
    'nexus_humint','nexus_movement','nexus_websdr','nexus_hf_maritime','nexus_iridium',
    'nexus_maritime_anomaly'],
  'Analyse & KI': ['nexus_sar','nexus_sentinel','nexus_wiki','nexus_translate','nexus_ner',
    'nexus_dedup','nexus_confidence','nexus_credibility','nexus_escalation','nexus_predict',
    'nexus_whois','nexus_timeline','nexus_docmeta','nexus_imgmeta','nexus_video',
    'nexus_vision','nexus_sitrep','nexus_travel_safety'],
  'Intelligence': ['nexus_watchlist','nexus_llm','nexus_netprop','nexus_knowledge',
    'nexus_delta','nexus_correlate','nexus_fusion','nexus_search','nexus_entities',
    'nexus_netgraph','nexus_spire','nexus_sar_learner','nexus_escalation_watchlist',
    'nexus_memory','nexus_webcam','nexus_geolocate','nexus_imgcheck'],
  'UI & System': ['nexus_alert','nexus_voice','nexus_local','nexus_graph','nexus_pdf_export',
    'nexus_report','nexus_livemap','nexus_linkmap','nexus_watchlist_ui','nexus_delta_map',
    'nexus_tailscale_ip','nexus_auth','nexus_daily','nexus_sar_classify','nexus_demo',
    'nexus_brain']
}};

function statusClass(s) {{
  if (s === 'OK') return 'ok';
  if (s === 'FEHLER' || s === 'TIMEOUT') return 'fehler';
  return 'leer';
}}

function badgeHtml(s) {{
  const cls = statusClass(s);
  const labels = {{OK:'✅ OK', LEER:'🔵 Kein Signal', FEHLER:'❌ Fehler', TIMEOUT:'⏱ Timeout'}};
  return `<span class="badge badge-${{cls}}">${{labels[s] || s}}</span>`;
}}

function buildCards(data) {{
  const container = document.getElementById('cardContainer');
  container.innerHTML = '';
  const modules = data.modules || {{}};

  for (const [catName, catKeys] of Object.entries(CATEGORIES)) {{
    const catCards = [];

    for (const [label, info] of Object.entries(modules)) {{
      const modKey = label.split(/\\s+/)[0];
      if (!catKeys.includes(modKey)) continue;

      const sc = statusClass(info.status);
      const shortLabel = label.replace(modKey, '').trim();
      const time = info.elapsed_s ? `${{info.elapsed_s.toFixed(1)}}s` : '';
      const countInfo = info.count > 0 ? ` · count=${{info.count}}` : '';
      const sampleText = (info.sample || '').substring(0, 120);
      const errText = info.error ? info.error.substring(0, 180) : '';

      catCards.push(`
        <div class="card ${{sc}}" data-status="${{sc}}" data-label="${{label.toLowerCase()}}">
          <div class="card-header">
            ${{badgeHtml(info.status)}}
            <span class="mod-name" title="${{label}}">${{modKey}}</span>
            <span class="mod-time">${{time}}${{countInfo}}</span>
          </div>
          <div class="mod-label">${{shortLabel}}</div>
          ${{errText ? `<div class="error-msg">${{errText}}</div>` : ''}}
          ${{sampleText ? `<div class="sample-box">${{sampleText}}</div>` : ''}}
        </div>`);
    }}

    if (catCards.length > 0) {{
      const titleDiv = document.createElement('div');
      titleDiv.className = 'section-title';
      titleDiv.textContent = catName;
      container.appendChild(titleDiv);

      const grid = document.createElement('div');
      grid.className = 'grid';
      grid.innerHTML = catCards.join('');
      container.appendChild(grid);
    }}
  }}
}}

function updateSummary(data) {{
  const s = data.summary || {{}};
  document.getElementById('numTotal').textContent = s.total || 0;
  document.getElementById('numOk').textContent = s.ok || 0;
  document.getElementById('numLeer').textContent = s.leer || 0;
  document.getElementById('numFehler').textContent = s.fehler || 0;

  const pct = s.total > 0 ? Math.round((s.ok / s.total) * 100) : 0;
  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressLabel').textContent =
    `${{pct}}% operational (${{s.ok}} von ${{s.total}} Modulen OK)`;

  if (data.timestamp) {{
    const d = new Date(data.timestamp);
    document.getElementById('timestamp').textContent =
      `Letzter Diagnoselauf: ${{d.toLocaleString('de-DE')}}`;
  }}
}}

function setFilter(f) {{
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('btn' + f.charAt(0).toUpperCase() + f.slice(1)).classList.add('active');
  filterCards();
}}

function filterCards() {{
  const q = document.getElementById('searchBox').value.toLowerCase();
  document.querySelectorAll('.card').forEach(card => {{
    const matchFilter = currentFilter === 'all' || card.dataset.status === currentFilter;
    const matchSearch = !q || card.dataset.label.includes(q);
    card.classList.toggle('hidden', !(matchFilter && matchSearch));
  }});
  // Section titles: hide if all cards hidden
  document.querySelectorAll('.section-title').forEach(title => {{
    const grid = title.nextElementSibling;
    const visible = grid && [...grid.querySelectorAll('.card')].some(c => !c.classList.contains('hidden'));
    title.classList.toggle('hidden', !visible);
    if (grid) grid.classList.toggle('hidden', !visible);
  }});
}}

function triggerDiag() {{
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Diagnose läuft (~90s)…';
  // Poll every 5s for updated results
  const startTs = allData.timestamp || '';
  const poll = setInterval(() => {{
    fetch(API).then(r => r.json()).then(d => {{
      if (d.timestamp && d.timestamp !== startTs) {{
        clearInterval(poll);
        allData = d;
        updateSummary(d);
        buildCards(d);
        filterCards();
        btn.disabled = false;
        btn.innerHTML = '▶ Diagnose starten';
      }}
    }}).catch(() => {{}});
  }}, 5000);
  // Timeout after 120s
  setTimeout(() => {{
    clearInterval(poll);
    btn.disabled = false;
    btn.innerHTML = '▶ Diagnose starten';
  }}, 120000);
  // Show instruction
  document.getElementById('progressLabel').textContent =
    '⚡ Bitte "python nexus_diagnostic.py ukraine" im Terminal ausführen — Seite aktualisiert automatisch.';
}}

async function load() {{
  try {{
    const r = await fetch(API);
    const d = await r.json();
    allData = d;
    if (d.error) {{
      document.getElementById('progressLabel').textContent = '⚠ ' + d.error;
      return;
    }}
    updateSummary(d);
    buildCards(d);
  }} catch(e) {{
    document.getElementById('progressLabel').textContent = '❌ Konnte /api/modules nicht laden: ' + e;
  }}
}}

load();
setInterval(load, 60000);  // Auto-Refresh jede Minute
</script>
</body>
</html>"""


# ── HTTP-Handler ───────────────────────────────────────────────────────────────

class _NexusHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Kein Apache-Log im Terminal

    # ── Sicherheits-Hilfsmethoden ──────────────────────────────────────────────

    def _check_token(self) -> bool:
        """Prüft Token via Header, Cookie oder Query-Parameter."""
        if not _NEXUS_TOKEN:
            return True
        # 1. Header (JS fetch)
        if self.headers.get("X-Nexus-Token", "").strip() == _NEXUS_TOKEN:
            return True
        # 2. Cookie (Browser nach Login)
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip() == "nexus_token" and v.strip() == _NEXUS_TOKEN:
                return True
        # 3. Query-Parameter
        qs = parse_qs(urlparse(self.path).query)
        return (qs.get("token") or [""])[0].strip() == _NEXUS_TOKEN

    def _send_json(self, data: dict, status: int = 200) -> None:
        # Sets → lists (JSON-sicher)
        class _SE(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, (set, frozenset)):
                    return sorted(o)
                return super().default(o)
        try:
            body = json.dumps(data, ensure_ascii=False, cls=_SE).encode("utf-8")
        except Exception:
            body = json.dumps({"error": "Serialisierungsfehler"}).encode("utf-8")
            status = 500
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # CORS: localhost → Wildcard; VPN/Heimnetz → spezifischer Origin-Echo
            origin = self.headers.get("Origin", "")
            if not _NETWORK_EXPOSED:
                self.send_header("Access-Control-Allow-Origin", "*")
            elif any(origin.startswith(t) for t in _TRUSTED_ORIGINS):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            # Chrome Private Network Access – erlaubt Requests von file:// Seiten
            if self.headers.get("Access-Control-Request-Private-Network"):
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass  # Browser hat Verbindung geschlossen – kein Fehler

    def _send_text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _safe_error(self, e: Exception, status: int = 500) -> None:
        """Sendet einen sicheren Fehler ohne interne Details zu leaken."""
        # Im Debug-Modus (localhost) voller Fehlertext, sonst generisch
        if not _NETWORK_EXPOSED:
            self._send_json({"error": str(e)}, status)
        else:
            self._send_json({"error": "Interner Serverfehler"}, status)

    _PAGE_ROUTES = {"/", "/livemap", "/dashboard", "/timeline",
                    "/delta", "/watchlist", "/report", "/linkmap", "/modules",
                    "/maritime", "/source_health"}

    def _is_browser_route(self, path: str) -> bool:
        """True für Routen, die bei fehlendem Token zur Login-Seite umleiten
        sollen (Browser/Handy-Aufruf), statt eine rohe 401-JSON-Antwort zu senden."""
        return path in self._PAGE_ROUTES or path.startswith("/reports/")

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        # Login-Seite immer erreichbar (kein Token nötig)
        if parsed.path == "/login":
            from nexus_auth import build_login_html  # type: ignore
            nxt = (qs.get("next") or ["/livemap"])[0]
            html = build_login_html(next_path=nxt).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if not self._check_token():
            if self._is_browser_route(parsed.path):
                # Seite → Login-Seite anzeigen
                self.send_response(302)
                self.send_header("Location", f"/login?next={parsed.path}")
                self.end_headers()
            else:
                self._send_json({"error": "Unauthorized"}, 401)
            return

        if parsed.path == "/api/data":
            query = (qs.get("query") or [""])[0].strip()
            if not query:
                self._send_json({"error": "query parameter required"}, 400)
                return
            # Eingabe-Validierung: Länge begrenzen (DoS-Schutz)
            if len(query) > _MAX_QUERY_LEN:
                self._send_json({"error": "query too long"}, 400)
                return
            cached = _get_cached(query)
            if cached:
                cached["from_cache"] = True
                self._send_json(cached)
                return
            # Kein Cache: Pipeline im Hintergrund starten, sofort Stub liefern
            # → Browser-Timeout wird nie ausgelöst (Report hat 15s Timeout!)
            _bg_start_pipeline(query)
            self._send_json(_EMPTY_STUB())

        elif parsed.path == "/api/status":
            # Pipeline-Fortschritts-Endpoint für den Lade-Balken im Dashboard
            with _pstatus_lock:
                snap = dict(_pipeline_status)
            self._send_json(snap)

        elif parsed.path in ("/api/ping", "/health"):
            # /health wird vom Docker-Healthcheck aufgerufen
            self._send_json({
                "status": "ok",
                "service": "nexus-osint",
                "time": datetime.now(timezone.utc).strftime("%H:%M UTC"),
                "port": LIVE_PORT,
            })

        elif parsed.path == "/api/cache_clear":
            with _cache_lock:
                _cache.clear()
            self._send_json({"cleared": True})

        # ── Predictive Analytics (Ebene 4) ──────────────────────────────────
        elif parsed.path == "/api/predict":
            region = (qs.get("region") or [""])[0].strip() or (qs.get("query") or [""])[0].strip()
            hours  = int((qs.get("hours") or ["72"])[0])
            if not region:
                self._send_json({"error": "region parameter required"}, 400)
                return
            try:
                from nexus_predict import predict, get_sparkline, detect_anomalies, get_trend_summary  # type: ignore
                self._send_json({
                    "region":   region,
                    "predict":  predict(region, hours_back=hours),
                    "sparkline":get_sparkline(region, hours_back=hours),
                    "anomalies":detect_anomalies(region, hours_back=hours),
                    "summary":  get_trend_summary(region),
                })
            except Exception as e:
                self._safe_error(e)

        # ── Eskalations-Watchlist (Ebene 4) ──────────────────────────────────
        elif parsed.path == "/api/esc_watchlist":
            try:
                from nexus_escalation_watchlist import handle_api_get  # type: ignore
                self._send_json(handle_api_get())
            except Exception as e:
                self._safe_error(e)


        # ── Timeseries API (T173) ───────────────────────────────────────────
        elif parsed.path == "/api/timeseries":
            try:
                from nexus_timeseries import (
                    query_range, compute_delta, get_score_trend,
                    escalation_history, db_stats
                )
                params = parse_qs(parsed.query)
                action  = (params.get("action", ["scores"])[0])
                region  = (params.get("region", [""])[0])
                hours   = float(params.get("hours", ["48"])[0])

                if action == "scores":
                    rows = query_range("nexus_escalation","escalation_score",hours,region)
                    self._send_json({"rows": rows, "region": region, "hours": hours})
                elif action == "trend":
                    trend = get_score_trend(region, hours)
                    self._send_json({"trend": trend, "region": region})
                elif action == "delta":
                    d = compute_delta("nexus_escalation","escalation_score",hours,6,region)
                    self._send_json(d)
                elif action == "alerts":
                    alerts = escalation_history(region, hours)
                    self._send_json({"alerts": alerts})
                elif action == "stats":
                    self._send_json(db_stats())
                else:
                    self._send_json({"error": f"Unbekannte action: {action}"})
            except Exception as e:
                self._safe_error(e)

        # ── Watchlist API GET (T93) ─────────────────────────────────────────
        elif parsed.path == "/api/watchlist":
            try:
                self._send_json(_watchlist_load())
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/api/dashboard":
            # Liefert Zusammenfassungs-Daten für alle Watchlist-Regionen
            try:
                regions = _get_watchlist_regions()
                summaries = []
                for region in regions[:8]:   # Max 8 Kacheln
                    cached = _get_cached(region)
                    if cached:
                        d = cached
                    else:
                        d = _fetch_live_data(region)
                        _set_cache(region, d)
                    summaries.append({
                        "region":    region,
                        "timestamp": d.get("timestamp", ""),
                        "flights":   {
                            "total":      (d.get("flights") or {}).get("total", 0),
                            "suspicious": (d.get("flights") or {}).get("suspicious", 0),
                        } if d.get("flights") else None,
                        "weather": {
                            "desc":    (d.get("weather") or {}).get("weather_desc", "?"),
                            "temp_c":  (d.get("weather") or {}).get("temperature_c"),
                            "overall": (d.get("weather") or {}).get("overall", "gruen"),
                        } if d.get("weather") else None,
                        "maritime": {
                            "alert_count": (d.get("maritime") or {}).get("alert_count", 0),
                        } if d.get("maritime") else None,
                        "article_count": len(d.get("articles") or []),
                        "from_cache": True,
                    })
                # Wirtschaftsindikatoren hinzufügen
                econ = {}
                try:
                    from nexus_economics import get_economic_indicators  # type: ignore
                    econ = get_economic_indicators()
                except Exception:
                    pass
                self._send_json({"regions": summaries, "economics": econ,
                                 "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")})
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/dashboard":
            # Liefert das fertige Dashboard-HTML
            html = _build_dashboard_html()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/livemap":
            # Persistentes Lagebild – standalone Self-Updating Map
            try:
                from nexus_livemap import build_livemap_html  # type: ignore
                html = build_livemap_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_livemap.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/timeline":
            # T88: Chronologische Ereignis-Zeitleiste
            try:
                from nexus_timeline import build_timeline_html  # type: ignore
                html = build_timeline_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_timeline.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/nexus_sw.js":
            # T89: PWA Service Worker
            sw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexus_sw.js")
            try:
                with open(sw_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Service-Worker-Allowed", "/")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._safe_error(FileNotFoundError("nexus_sw.js nicht gefunden"))

        elif parsed.path == "/nexus_manifest.json":
            # T89: PWA Web App Manifest
            mf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexus_manifest.json")
            try:
                with open(mf_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._safe_error(FileNotFoundError("nexus_manifest.json nicht gefunden"))

        elif parsed.path == "/api/pdf_export":
            # T90: PDF-Lagebericht Export
            qs = parse_qs(parsed.query)
            region = qs.get("region", ["Global"])[0][:100]
            try:
                from nexus_pdf_export import export_report_pdf  # type: ignore
                # Hole aktuelle Daten aus dem Cache
                cached = _DATA_CACHE.get("last", {})
                articles = cached.get("articles", [])
                text = cached.get("analysis", "")
                pdf_path = export_report_pdf(
                    topic=region, text=text, articles=articles
                )
                with open(pdf_path, "rb") as f:
                    body = f.read()
                fn = os.path.basename(pdf_path)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                msg = f"PDF-Export Fehler: {e}".encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

        elif parsed.path == "/report":
            # Letzten generierten HTML-Report als HTTP-Seite ausliefern
            html_path = _last_report_path
            if html_path and os.path.exists(html_path):
                try:
                    with open(html_path, "r", encoding="utf-8") as f:
                        content_html = f.read()
                    body = content_html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    self._safe_error(e)
            else:
                body = b"<h2>Kein Report vorhanden. NEXUS Lagebild zuerst starten (L).</h2>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        elif parsed.path.startswith("/reports/"):
            # T-NTFY-Report: einzelnen, namentlich per NTFY-Link verschickten
            # Bericht ausliefern (z.B. von nexus_selftest.py/nexus_daily.py
            # erzeugte Cron-Berichte, NICHT nur den zuletzt erzeugten).
            # Nur Basisname zulassen — kein Path-Traversal über ../.
            raw_name  = parsed.path[len("/reports/"):]
            safe_name = os.path.basename(raw_name)
            ext_ok    = safe_name.lower().endswith((".html", ".pdf"))
            file_path = os.path.join(_REPORTS_DIR, safe_name)
            if (
                ext_ok
                and safe_name
                and os.path.commonpath([os.path.abspath(file_path), os.path.abspath(_REPORTS_DIR)])
                    == os.path.abspath(_REPORTS_DIR)
                and os.path.isfile(file_path)
            ):
                try:
                    is_pdf = safe_name.lower().endswith(".pdf")
                    mode   = "rb" if is_pdf else "r"
                    with open(file_path, mode, **({} if is_pdf else {"encoding": "utf-8"})) as f:
                        content = f.read()
                    body = content if is_pdf else content.encode("utf-8")
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "application/pdf" if is_pdf else "text/html; charset=utf-8",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    self._safe_error(e)
            else:
                body = b"<h2>Bericht nicht gefunden.</h2>"
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        elif parsed.path == "/delta":
            # T92: Delta-Karte – Zeitraum-Vergleich
            try:
                from nexus_delta_map import build_delta_map_html  # type: ignore
                html = build_delta_map_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_delta_map.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/watchlist":
            # T93: Watchlist-Manager Web-UI
            try:
                from nexus_watchlist_ui import build_watchlist_ui_html  # type: ignore
                html = build_watchlist_ui_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_watchlist_ui.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ── Entity-Tracking API (T96/T97) ────────────────────────────────────────
        elif parsed.path == "/api/entities":
            try:
                from nexus_entities import get_tracker  # type: ignore
                etype  = (qs.get("type") or [None])[0]
                limit  = int((qs.get("limit") or ["100"])[0])
                query  = (qs.get("q") or [""])[0].strip()
                t      = get_tracker()
                if query:
                    data = t.search_entities(query, entity_type=etype, limit=limit)
                else:
                    data = t.get_all_entities(entity_type=etype, limit=limit)
                self._send_json(data)
            except Exception as e:
                self._safe_error(e)

        elif parsed.path.startswith("/api/entities/"):
            entity_id = parsed.path.split("/api/entities/", 1)[1].strip("/")
            if not entity_id:
                self._send_json({"error": "entity_id required"}, 400)
                return
            sub = (qs.get("sub") or ["detail"])[0]
            try:
                from nexus_graph import get_graph  # type: ignore
                g = get_graph()
                if sub == "timeline":
                    from nexus_entities import get_tracker  # type: ignore
                    data = get_tracker().get_entity_timeline(entity_id, days=30)
                elif sub == "pol":
                    from nexus_entities import get_tracker  # type: ignore
                    data = get_tracker().get_pattern_of_life(entity_id)
                else:
                    data = g.analyze_entity(entity_id)
                self._send_json(data)
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/api/graph":
            try:
                from nexus_graph import get_graph  # type: ignore
                focus  = (qs.get("focus") or [None])[0]
                min_s  = float((qs.get("min_strength") or ["0.2"])[0])
                max_n  = int((qs.get("max_nodes") or ["120"])[0])
                g      = get_graph()
                data   = g.get_vis_data(focus_entity=focus, min_strength=min_s,
                                        max_nodes=max_n)
                self._send_json(data)
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/api/entity_stats":
            try:
                from nexus_graph import get_graph  # type: ignore
                self._send_json(get_graph().get_network_stats())
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/api/conflict_events":
            # Unified: ACLED + UCDP + ReliefWeb
            region  = (qs.get("region") or (qs.get("query") or [""])[0:1])[0].strip()
            days    = int((qs.get("days") or ["7"])[0])
            sources = (qs.get("sources") or ["auto"])[0]
            if not region:
                self._send_json({"error": "region required"}, 400)
                return
            try:
                from nexus_acled import fetch_conflict_events, conflict_status  # type: ignore
                events = fetch_conflict_events(region, days=days, sources=sources)
                self._send_json({
                    "region":  region,
                    "events":  events,
                    "count":   len(events),
                    "sources": conflict_status(),
                })
            except Exception as e:
                self._safe_error(e)

        # ── T174: Maritim-Dashboard – AIS + SAR-Satellitenbilder ─────────────
        elif parsed.path == "/api/maritime_imagery":
            region = (qs.get("region") or [""])[0].strip()
            if not region:
                self._send_json({"error": "region required"}, 400)
                return
            try:
                from nexus_maritime import MARITIME_REGIONS  # type: ignore

                # Beschreibung + Mittelpunkt aus MARITIME_REGIONS, falls bekannt
                meta = MARITIME_REGIONS.get(region, {})
                lat, lon = meta.get("center", (None, None))
                desc = meta.get("desc", "")

                # AIS-Transponderdaten
                ais_data: dict = {}
                try:
                    from nexus_ais import get_vessels  # type: ignore
                    ais_data = get_vessels(region)
                except Exception as exc:
                    ais_data = {"vessel_count": 0, "vessels": [],
                                "source": f"Fehler: {exc}", "has_key": False}

                # SAR-Schiffserkennung via Sentinel-1 + EO-Browser-Link
                sar_data: dict = {}
                try:
                    from nexus_sar import detect_ships  # type: ignore
                    det = detect_ships(region)
                    sar_data = {
                        "scene_date":    det.scene_date,
                        "scene_id":      det.scene_id,
                        "ships":         det.ships,
                        "ship_count":    det.ship_count,
                        "anomaly_score": det.anomaly_score,
                        "method":        det.method,
                        "eo_link":       det.eo_link,
                        "description":   det.description,
                    }
                    if lat is None:
                        lat, lon = det.lat, det.lon
                except Exception as exc:
                    sar_data = {"error": str(exc), "ships": [], "ship_count": 0,
                                "anomaly_score": 0.0, "method": "fehler",
                                "eo_link": "", "description": f"SAR-Fehler: {exc}"}

                # Maritime-Lage (Alarme via Schlüsselwörter aus Nachrichtenlage)
                alert_count = None
                try:
                    from nexus_maritime import get_maritime_situation  # type: ignore
                    msit = get_maritime_situation(region)
                    alert_count = (msit or {}).get("alert_count")
                except Exception:
                    pass

                self._send_json({
                    "region":      region,
                    "desc":        desc,
                    "lat":         lat,
                    "lon":         lon,
                    "alert_count": alert_count,
                    "ais":         ais_data,
                    "sar":         sar_data,
                    "timestamp":   datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                })
            except Exception as e:
                self._safe_error(e)

        # ── T176: Quellen-Gesundheits-Dashboard – Live-Monitoring ────────────
        elif parsed.path == "/api/source_health":
            force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
            try:
                from nexus_source_health import check_all_sources  # type: ignore
                data = check_all_sources(force=force)
                self._send_json(data)
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/source_health":
            try:
                from nexus_source_health_dashboard import build_source_health_html  # type: ignore
                html = build_source_health_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_source_health_dashboard.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif parsed.path == "/maritime":
            # T174: Maritim-Dashboard – Küsten/Meerengen, AIS + SAR-Satellitenbilder
            try:
                from nexus_maritime_dashboard import build_maritime_dashboard_html  # type: ignore
                html = build_maritime_dashboard_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_maritime_dashboard.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ── Modul-Status Seite ───────────────────────────────────────────────
        elif parsed.path == "/api/modules":
            # Liest nexus_diag_results.json aus und liefert es als JSON-API
            import json as _json
            _diag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "nexus_diag_results.json")
            try:
                with open(_diag_path, "r", encoding="utf-8") as _f:
                    _diag = _json.load(_f)
                self._send_json(_diag)
            except FileNotFoundError:
                self._send_json({"error": "Noch kein Diagnose-Lauf. Bitte 'python nexus_diagnostic.py ukraine' ausführen.",
                                 "modules": {}, "summary": {"ok": 0, "leer": 0, "fehler": 0, "total": 0}})
            except Exception as _e:
                self._send_json({"error": str(_e), "modules": {}}, 500)

        elif parsed.path == "/modules":
            # Modul-Status HTML-Seite (liest /api/modules)
            html = _build_modules_html(LIVE_PORT)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ── Link-Analyse UI (T98) ────────────────────────────────────────────
        elif parsed.path == "/linkmap":
            try:
                from nexus_linkmap import build_linkmap_html  # type: ignore
                html = build_linkmap_html(port=LIVE_PORT)
            except Exception as e:
                html = f"<h2>nexus_linkmap.py Fehler: {e}</h2>"
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self._send_json({"error": "Not found"}, 404)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_POST(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        # Login-Formular verarbeiten
        if parsed.path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            form   = self.rfile.read(length).decode("utf-8", errors="replace")
            params = parse_qs(form)
            submitted = (params.get("token") or [""])[0].strip()
            nxt = (qs.get("next") or ["/livemap"])[0]
            if submitted == _NEXUS_TOKEN and _NEXUS_TOKEN:
                # Richtig → Cookie 30 Tage setzen + weiterleiten
                self.send_response(302)
                self.send_header("Location", nxt)
                self.send_header(
                    "Set-Cookie",
                    f"nexus_token={_NEXUS_TOKEN}; Path=/; "
                    "Max-Age=2592000; HttpOnly; SameSite=Strict"
                )
                self.end_headers()
            else:
                from nexus_auth import build_login_html  # type: ignore
                html = build_login_html(next_path=nxt, error=True).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            return

        # Alle anderen POST-Endpunkte: Token prüfen
        if not self._check_token():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        body   = self._read_json_body()

        if parsed.path == "/api/esc_watchlist":
            try:
                from nexus_escalation_watchlist import handle_api_post  # type: ignore
                self._send_json(handle_api_post(body), 201)
            except Exception as e:
                self._safe_error(e)
        elif parsed.path == "/api/watchlist/add":
            try:
                term = (body.get("term") or "").strip()
                if not term:
                    self._send_json({"error": "term required"}, 400)
                    return
                try:
                    from nexus_memory import wl_add  # type: ignore
                    wl_add(term)
                except Exception:
                    pass
                _watchlist_save_entry(body)
                self._send_json({"ok": True})
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/api/watchlist/del":
            try:
                term = (body.get("term") or "").strip()
                if not term:
                    self._send_json({"error": "term required"}, 400)
                    return
                try:
                    from nexus_memory import wl_remove  # type: ignore
                    wl_remove(term)
                except Exception:
                    pass
                _watchlist_remove_entry(term)
                self._send_json({"ok": True})
            except Exception as e:
                self._safe_error(e)

        elif parsed.path == "/api/watchlist/toggle":
            try:
                term = (body.get("term") or "").strip()
                if not term:
                    self._send_json({"error": "term required"}, 400)
                    return
                _watchlist_toggle_entry(term)
                self._send_json({"ok": True})
            except Exception as e:
                self._safe_error(e)

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        if parsed.path == "/api/esc_watchlist":
            try:
                entry_id = int((qs.get("id") or ["0"])[0])
                from nexus_escalation_watchlist import handle_api_delete  # type: ignore
                self._send_json(handle_api_delete(entry_id))
            except Exception as e:
                self._safe_error(e)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        body   = self._read_json_body()

        if parsed.path == "/api/esc_watchlist":
            try:
                entry_id = int((qs.get("id") or ["0"])[0])
                from nexus_escalation_watchlist import handle_api_put  # type: ignore
                self._send_json(handle_api_put(entry_id, body))
            except Exception as e:
                self._safe_error(e)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        """CORS Preflight (inkl. Chrome Private Network Access)."""
        self.send_response(204)
        if not _NETWORK_EXPOSED:
            self.send_header("Access-Control-Allow-Origin", "*")
        else:
            origin = self.headers.get("Origin", "")
            if any(origin.startswith(t) for t in _TRUSTED_ORIGINS):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Nexus-Token")
        # Chrome Private Network Access (PNA) – erlaubt Zugriff von file:// und lokalen Seiten
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()


_server_thread: threading.Thread | None = None
_httpd: ThreadingHTTPServer | None = None


def _print_startup_diagnostics() -> None:
    """Zeigt beim Start welche API-Keys fehlen und was kostenlos verfügbar ist."""
    try:
        import config  # type: ignore
    except ImportError:
        return

    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    RESET  = "\033[0m"

    print(f"\n{CYAN}{'─'*55}{RESET}", flush=True)
    print(f"{CYAN}  NEXUS Modul-Status{RESET}", flush=True)
    print(f"{CYAN}{'─'*55}{RESET}", flush=True)

    checks = [
        ("AIS Schiffe",      getattr(config, "AISSTREAM_KEY",     ""),
         "aisstream.io/account → kostenlos → config.py: AISSTREAM_KEY"),
   