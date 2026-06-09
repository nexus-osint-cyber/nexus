"""
NEXUS – Theater Web Dashboard
==============================
Erzeugt eine interaktive HTML-Datei mit:
  - D3.js Force-Directed Akteur-Netzwerk (klickbare Nodes)
  - Live-Scores pro Region (Farbe = Eskalationsstufe)
  - 48h-Event-Timeline (ACLED/RSS Ereignisse pro Akteur)
  - Department-Heatmap aller Mitglieder
  - Auto-Refresh alle 5 Minuten über nexus_live_server.py API
  - Vollständig offline nutzbar (eingebettetes JSON als Fallback)

Nutzung:
  python nexus_theater_web.py --theater MiddleEast
  python nexus_theater_web.py --theater EasternEurope --out /tmp/theater.html
  python nexus_theater_web.py --theater MiddleEast --open   # öffnet im Browser
  python nexus_theater_web.py --theater MiddleEast --static # kein Live-Refresh
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# Koordinaten der Regionen (für initiale Node-Positionierung)
# ═══════════════════════════════════════════════════════════════════════════════

REGION_COORDS: dict[str, tuple[float, float]] = {
    # MiddleEast
    "Iran":    (32.0,  53.0),
    "Israel":  (31.0,  34.9),
    "Gaza":    (31.4,  34.3),
    "Lebanon": (33.9,  35.5),
    "Yemen":   (15.6,  48.5),
    "Syria":   (34.8,  38.9),
    "Iraq":    (33.2,  43.7),
    # EasternEurope
    "Ukraine": (49.0,  31.0),
    "Russia":  (55.7,  37.6),
    "Belarus": (53.7,  27.9),
    # AsiaPacific
    "China":       (35.0, 103.0),
    "Taiwan":      (23.7, 120.9),
    "North Korea": (39.0, 125.7),
    "South Korea": (37.5, 127.0),
    "Japan":       (36.2, 138.2),
    # Global actors
    "USA":         (38.9, -77.0),
    "Saudi":       (23.9,  45.1),
    "Russia":      (55.7,  37.6),
}

# Score-Farben (matching Eskalationsstufen)
LEVEL_COLORS = {
    "KRITISCH": "#ef4444",
    "ROT":      "#f97316",
    "ORANGE":   "#f59e0b",
    "GELB":     "#eab308",
    "GRUEN":    "#22c55e",
    "UNKNOWN":  "#64748b",
}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _score_to_level(score: float) -> str:
    if   score >= 81: return "KRITISCH"
    elif score >= 61: return "ROT"
    elif score >= 41: return "ORANGE"
    elif score >= 21: return "GELB"
    else:             return "GRUEN"


def _score_to_color(score: float) -> str:
    return LEVEL_COLORS[_score_to_level(score)]


# ═══════════════════════════════════════════════════════════════════════════════
# Fetch recent events (48h) für Timeline
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_recent_events(region: str, hours: int = 48) -> list[dict]:
    """Holt die letzten `hours` Stunden Ereignisse für eine Region."""
    events = []
    # ACLED
    try:
        from nexus_acled import fetch_ucdp_events
        data = fetch_ucdp_events(region, days=max(1, hours // 24 + 1)) or []
        for e in data[:5]:
            events.append({
                "time":   e.get("date", e.get("timestamp", "")),
                "type":   "conflict",
                "source": "ACLED/UCDP",
                "text":   e.get("headline", e.get("notes", str(e)))[:100],
                "lat":    e.get("latitude"),
                "lon":    e.get("longitude"),
            })
    except Exception:
        pass
    # RSS
    try:
        from nexus_rss import fetch_rss_headlines
        data = fetch_rss_headlines(region, limit=5) or []
        for e in data:
            events.append({
                "time":   e.get("published", e.get("date", "")),
                "type":   "news",
                "source": e.get("feed", "RSS"),
                "text":   e.get("title", "")[:100],
                "lat":    None,
                "lon":    None,
            })
    except Exception:
        pass
    return events[:8]


# ═══════════════════════════════════════════════════════════════════════════════
# Daten vorbereiten
# ═══════════════════════════════════════════════════════════════════════════════

def build_theater_data(theater_name: str, static: bool = False) -> dict:
    """
    Sammelt alle Daten für das Dashboard:
    - Theater-Definition
    - Aktuelle Scores (oder Nullen wenn static=True)
    - 48h Events pro Mitglied
    - Akteur-Ketten für das Netzwerk
    """
    try:
        from nexus_theater import THEATERS, _normalize_theater_name, compute_theater
    except ImportError as e:
        raise ImportError(f"nexus_theater.py nicht gefunden: {e}")

    tn = _normalize_theater_name(theater_name)
    if tn not in THEATERS:
        raise ValueError(f"Unbekanntes Theater: {theater_name}")

    t = THEATERS[tn]
    members = t["members"]

    # Scores
    member_scores: dict[str, float] = {}
    member_levels: dict[str, str]   = {}
    dept_data: dict[str, dict]      = {}

    if not static:
        try:
            result = compute_theater(tn, parallel=True, timeout=90)
            for m in members:
                s = result["member_scores"].get(m, 0.0)
                member_scores[m] = s
                member_levels[m] = _score_to_level(s)
                dept_data[m] = {
                    d: result["members"][m].get("departments", {}).get(d, {}).get("score", -1)
                    for d in ["OSINT", "GEOINT", "SIGINT", "HUMINT", "ECONINT", "HUMANA"]
                }
        except Exception:
            static = True

    if static:
        for m in members:
            member_scores[m] = 0.0
            member_levels[m] = "GRUEN"
            dept_data[m]     = {d: -1 for d in ["OSINT","GEOINT","SIGINT","HUMINT","ECONINT","HUMANA"]}

    # 48h Events
    events_by_region: dict[str, list] = {}
    if not static:
        for m in members:
            events_by_region[m] = _fetch_recent_events(m, hours=48)
    else:
        for m in members:
            events_by_region[m] = []

    # D3-Graph: Nodes
    nodes = []
    for m in members:
        score = member_scores.get(m, 0.0)
        nodes.append({
            "id":      m,
            "label":   m,
            "score":   score,
            "level":   member_levels.get(m, "GRUEN"),
            "color":   _score_to_color(score),
            "weight":  t.get("member_weights", {}).get(m, 0.1),
            "is_driver": m in t.get("driver_regions", []),
            "is_target": m in t.get("primary_targets", []),
            "depts":   dept_data.get(m, {}),
            "events":  events_by_region.get(m, []),
        })

    # D3-Graph: Links
    links = []
    rel_colors = {
        "attacks":        "#ef4444",
        "funds_arms":     "#f97316",
        "transit_support":"#a78bfa",
        "supports":       "#64748b",
        "threatens":      "#eab308",
    }
    for chain in t.get("actor_chains", []):
        links.append({
            "source":   chain["from"],
            "target":   chain["to"],
            "relation": chain.get("relation", ""),
            "via":      chain.get("via", ""),
            "label":    chain.get("label", ""),
            "risk":     chain.get("risk", "low"),
            "color":    rel_colors.get(chain.get("relation",""), "#64748b"),
        })

    return {
        "theater_name": tn,
        "label":        t["label"],
        "description":  t["description"],
        "nodes":        nodes,
        "links":        links,
        "actor_chains": t["actor_chains"],
        "driver_regions": t.get("driver_regions", []),
        "primary_targets": t.get("primary_targets", []),
        "escalation_triggers": t.get("escalation_triggers", []),
        "timestamp":    _ts(),
        "static":       static,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HTML generieren
# ═══════════════════════════════════════════════════════════════════════════════

def generate_html(data: dict, live_server_port: int = 5000) -> str:
    """Erzeugt die vollständige HTML-Seite als String."""

    json_data = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    ts_human  = data.get("timestamp", "?")

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NEXUS · Theater · {data['theater_name']}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
  :root {{
    --bg:        #0d1117;
    --bg2:       #161b22;
    --bg3:       #21262d;
    --border:    #30363d;
    --text:      #e6edf3;
    --dim:       #8b949e;
    --accent:    #4a9eff;
    --red:       #ef4444;
    --orange:    #f97316;
    --yellow:    #eab308;
    --green:     #22c55e;
    --purple:    #a78bfa;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; }}

  /* ── Layout ── */
  .layout {{ display: grid; grid-template-columns: 1fr 340px; height: 100vh; overflow: hidden; }}
  .main-panel {{ display: flex; flex-direction: column; }}
  .side-panel {{ background: var(--bg2); border-left: 1px solid var(--border); overflow-y: auto; display: flex; flex-direction: column; }}

  /* ── Header ── */
  .header {{ padding: 12px 20px; background: var(--bg2); border-bottom: 1px solid var(--border);
             display: flex; align-items: center; justify-content: space-between; }}
  .header-left h1 {{ font-size: 16px; font-weight: 700; letter-spacing: 2px; color: var(--accent); }}
  .header-left p  {{ font-size: 11px; color: var(--dim); margin-top: 2px; }}
  .theater-score {{ text-align: right; }}
  .theater-score .score-val {{ font-size: 28px; font-weight: 700; }}
  .theater-score .score-label {{ font-size: 10px; color: var(--dim); }}
  .refresh-btn {{ background: var(--bg3); border: 1px solid var(--border); color: var(--text);
                  padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 12px; }}
  .refresh-btn:hover {{ border-color: var(--accent); color: var(--accent); }}

  /* ── Network Graph ── */
  #network-container {{ flex: 1; position: relative; }}
  #network-svg {{ width: 100%; height: 100%; }}
  .node-circle {{ cursor: pointer; stroke-width: 3; transition: all 0.2s; }}
  .node-circle:hover {{ stroke-width: 5; filter: brightness(1.3); }}
  .node-label {{ font-size: 11px; font-weight: 600; fill: #e6edf3; pointer-events: none;
                 text-shadow: 0 0 4px #000; }}
  .node-score {{ font-size: 9px; fill: #8b949e; pointer-events: none; }}
  .link-line {{ stroke-width: 2; opacity: 0.75; }}
  .link-label {{ font-size: 8px; fill: #8b949e; pointer-events: none; }}
  .link-arrow {{ }}

  /* ── Legend ── */
  .legend {{ padding: 8px 16px; background: var(--bg2); border-top: 1px solid var(--border);
             display: flex; gap: 20px; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--dim); }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
  .legend-line {{ width: 24px; height: 2px; }}

  /* ── Sidebar Sections ── */
  .side-section {{ padding: 14px 16px; border-bottom: 1px solid var(--border); }}
  .side-section h3 {{ font-size: 11px; font-weight: 700; color: var(--accent); letter-spacing: 1px;
                       text-transform: uppercase; margin-bottom: 10px; }}

  /* ── Member List ── */
  .member-row {{ display: flex; align-items: center; gap: 8px; padding: 5px 0; cursor: pointer; }}
  .member-row:hover {{ opacity: 0.8; }}
  .member-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
  .member-name {{ flex: 1; font-size: 12px; }}
  .member-score {{ font-size: 12px; font-weight: 700; min-width: 34px; text-align: right; }}
  .member-bar-wrap {{ flex: 1; height: 4px; background: var(--bg3); border-radius: 2px; overflow: hidden; }}
  .member-bar {{ height: 100%; border-radius: 2px; transition: width 0.5s; }}
  .role-badge {{ font-size: 8px; padding: 1px 4px; border-radius: 3px; font-weight: 600; }}
  .role-driver {{ background: rgba(239,68,68,0.2); color: var(--red); }}
  .role-target {{ background: rgba(74,158,255,0.2); color: var(--accent); }}

  /* ── Dept Heatmap ── */
  .heatmap-table {{ width: 100%; border-collapse: collapse; font-size: 9px; }}
  .heatmap-table th {{ color: var(--dim); padding: 2px 3px; text-align: center; font-weight: 500; }}
  .heatmap-table td {{ padding: 2px 3px; text-align: center; border-radius: 2px; }}
  .heatmap-cell {{ font-size: 9px; font-weight: 600; }}

  /* ── Timeline ── */
  .timeline-item {{ padding: 6px 0; border-bottom: 1px solid var(--border); }}
  .timeline-item:last-child {{ border-bottom: none; }}
  .timeline-item .ti-meta {{ font-size: 9px; color: var(--dim); }}
  .timeline-item .ti-text {{ font-size: 11px; margin-top: 2px; line-height: 1.4; }}
  .ti-type-conflict {{ border-left: 2px solid var(--red); padding-left: 8px; }}
  .ti-type-news     {{ border-left: 2px solid var(--accent); padding-left: 8px; }}
  .no-events {{ font-size: 11px; color: var(--dim); font-style: italic; }}

  /* ── Active Chains ── */
  .chain-item {{ padding: 6px 0; font-size: 11px; }}
  .chain-item .chain-src {{ color: var(--orange); font-weight: 600; }}
  .chain-item .chain-dst {{ color: var(--red); font-weight: 600; }}
  .chain-item .chain-via {{ color: var(--dim); font-size: 10px; }}

  /* ── Node Detail Popup ── */
  #node-detail {{ display: none; position: absolute; top: 10px; left: 10px; z-index: 100;
                  background: var(--bg2); border: 1px solid var(--border); border-radius: 10px;
                  padding: 14px; width: 260px; box-shadow: 0 8px 30px rgba(0,0,0,0.5); }}
  #node-detail h2 {{ font-size: 14px; font-weight: 700; margin-bottom: 8px; }}
  #node-detail .close-btn {{ position: absolute; top: 10px; right: 12px; cursor: pointer;
                              color: var(--dim); font-size: 16px; }}
  #node-detail .close-btn:hover {{ color: var(--text); }}
  .detail-depts {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; margin-top: 8px; }}
  .detail-dept {{ background: var(--bg3); border-radius: 4px; padding: 4px 6px; text-align: center; }}
  .detail-dept .dd-label {{ font-size: 8px; color: var(--dim); }}
  .detail-dept .dd-score {{ font-size: 12px; font-weight: 700; }}
  .detail-events {{ margin-top: 10px; }}
  .detail-events h4 {{ font-size: 10px; color: var(--dim); margin-bottom: 6px; }}
  .detail-event {{ font-size: 10px; padding: 3px 0; border-bottom: 1px solid var(--border); line-height: 1.3; }}

  /* ── Timestamps ── */
  .ts-bar {{ padding: 6px 16px; font-size: 9px; color: var(--dim); background: var(--bg);
             border-top: 1px solid var(--border); text-align: right; }}

  /* ── Alerts ── */
  .alert-item {{ padding: 6px 8px; border-radius: 6px; margin-bottom: 6px; font-size: 11px; }}
  .alert-critical {{ background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); }}
  .alert-warning  {{ background: rgba(249,115,22,0.15); border: 1px solid rgba(249,115,22,0.3); }}
  .alert-info     {{ background: rgba(74,158,255,0.1);  border: 1px solid rgba(74,158,255,0.2); }}

  /* ── Responsive ── */
  @media (max-width: 900px) {{
    .layout {{ grid-template-columns: 1fr; grid-template-rows: auto 1fr; height: auto; }}
    .side-panel {{ border-left: none; border-top: 1px solid var(--border); max-height: 60vh; }}
  }}
</style>
</head>
<body>

<div class="layout">
  <!-- ── Main Panel: Netzwerk ── -->
  <div class="main-panel">
    <div class="header">
      <div class="header-left">
        <h1>🎯 NEXUS · THEATER · {data['theater_name'].upper()}</h1>
        <p id="header-desc">{data['label']}</p>
      </div>
      <div style="display:flex;align-items:center;gap:12px;">
        <div class="theater-score">
          <div class="score-val" id="theater-score-val">—</div>
          <div class="score-label">THEATER-SCORE</div>
        </div>
        <button class="refresh-btn" onclick="refreshData()">⟳ Aktualisieren</button>
      </div>
    </div>

    <div id="network-container">
      <svg id="network-svg"></svg>
      <!-- Node-Detail Popup -->
      <div id="node-detail">
        <span class="close-btn" onclick="closeDetail()">✕</span>
        <h2 id="detail-title">—</h2>
        <div id="detail-score-bar"></div>
        <div class="detail-depts" id="detail-depts"></div>
        <div class="detail-events" id="detail-events"></div>
      </div>
    </div>

    <div class="legend" id="legend">
      <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div> KRITISCH (81–100)</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f97316"></div> ROT (61–80)</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f59e0b"></div> ORANGE (41–60)</div>
      <div class="legend-item"><div class="legend-dot" style="background:#eab308"></div> GELB (21–40)</div>
      <div class="legend-item"><div class="legend-dot" style="background:#22c55e"></div> GRÜN (0–20)</div>
      <div class="legend-item">── <div class="legend-line" style="background:#ef4444;display:inline-block"></div> Angriff</div>
      <div class="legend-item">── <div class="legend-line" style="background:#f97316;display:inline-block"></div> Versorgung</div>
      <div class="legend-item">── <div class="legend-line" style="background:#64748b;display:inline-block"></div> Support</div>
    </div>
  </div>

  <!-- ── Side Panel ── -->
  <div class="side-panel">

    <!-- Mitglieder -->
    <div class="side-section" id="members-section">
      <h3>📍 Mitglieder-Status</h3>
      <div id="members-list"></div>
    </div>

    <!-- Aktive Ketten -->
    <div class="side-section" id="chains-section">
      <h3>⚠ Aktive Ketten</h3>
      <div id="chains-list"><span class="no-events">Wird berechnet…</span></div>
    </div>

    <!-- Department Heatmap -->
    <div class="side-section">
      <h3>📊 Dept-Heatmap</h3>
      <div id="heatmap-container"></div>
    </div>

    <!-- 48h Timeline -->
    <div class="side-section">
      <h3>🕐 48h Ereignis-Feed</h3>
      <div id="timeline-list"><span class="no-events">Keine Ereignisse geladen</span></div>
    </div>

    <!-- Alerts -->
    <div class="side-section" id="alerts-section" style="display:none;">
      <h3>🚨 Eskalations-Alerts</h3>
      <div id="alerts-list"></div>
    </div>

  </div>
</div>

<div class="ts-bar">
  NEXUS · All-Source Intelligence Fusion ·
  Generiert: {ts_human} ·
  <span id="last-refresh">—</span> ·
  <span id="refresh-status">{'Live (Auto-Refresh 5min)' if not data.get('static') else 'Statisch'}</span>
</div>

<script>
// ── Initiale Daten (eingebettet als JSON-Fallback) ──────────────────────────
const INITIAL_DATA = {json_data};
const LIVE_SERVER  = 'http://localhost:{live_server_port}';
const STATIC_MODE  = {'true' if data.get('static') else 'false'};
const AUTO_REFRESH_MS = 5 * 60 * 1000; // 5 Minuten

let currentData = INITIAL_DATA;
let simulation  = null;
let svg = null, g = null;

// ── Score → Farbe ────────────────────────────────────────────────────────────
function scoreColor(score) {{
  if (score < 0)  return '#374151';
  if (score >= 81) return '#ef4444';
  if (score >= 61) return '#f97316';
  if (score >= 41) return '#f59e0b';
  if (score >= 21) return '#eab308';
  return '#22c55e';
}}

function scoreLevel(score) {{
  if (score < 0)   return '—';
  if (score >= 81) return 'KRITISCH';
  if (score >= 61) return 'ROT';
  if (score >= 41) return 'ORANGE';
  if (score >= 21) return 'GELB';
  return 'GRÜN';
}}

// ── D3 Netzwerk ──────────────────────────────────────────────────────────────
function initNetwork(data) {{
  const container = document.getElementById('network-container');
  const W = container.clientWidth  || 800;
  const H = container.clientHeight || 500;

  d3.select('#network-svg').selectAll('*').remove();

  svg = d3.select('#network-svg')
    .attr('width', W).attr('height', H);

  // Defs: Pfeile
  const defs = svg.append('defs');
  ['attacks','funds_arms','transit_support','supports','threatens'].forEach(rel => {{
    const colors = {{attacks:'#ef4444',funds_arms:'#f97316',
                     transit_support:'#a78bfa',supports:'#64748b',threatens:'#eab308'}};
    defs.append('marker')
      .attr('id', 'arrow-' + rel)
      .attr('viewBox','0 -4 8 8').attr('refX',14).attr('refY',0)
      .attr('markerWidth',6).attr('markerHeight',6).attr('orient','auto')
      .append('path').attr('d','M0,-4L8,0L0,4').attr('fill', colors[rel] || '#64748b');
  }});

  g = svg.append('g');

  // Zoom + Pan
  svg.call(d3.zoom()
    .scaleExtent([0.3, 3])
    .on('zoom', (event) => g.attr('transform', event.transform)));

  // Links
  const linkGroup = g.append('g').attr('class','links');
  const link = linkGroup.selectAll('.link-line')
    .data(data.links).enter().append('line')
    .attr('class','link-line')
    .attr('stroke', d => d.color || '#64748b')
    .attr('stroke-width', d => d.risk === 'critical' ? 3 : d.risk === 'high' ? 2 : 1.5)
    .attr('stroke-dasharray', d => d.relation === 'supports' ? '4,3' : null)
    .attr('marker-end', d => `url(#arrow-${{d.relation || 'supports'}})`);

  const linkLabel = g.append('g').selectAll('.link-label')
    .data(data.links.filter(l => l.via)).enter().append('text')
    .attr('class','link-label').text(d => d.via).attr('dy', -4);

  // Nodes
  const nodeGroup = g.append('g').attr('class','nodes');
  const nodeSize  = d => {{
    const base = d.is_driver ? 32 : d.is_target ? 28 : 22;
    return base + (d.score || 0) / 10;
  }};

  const node = nodeGroup.selectAll('.node')
    .data(data.nodes).enter().append('g')
    .attr('class','node').style('cursor','pointer')
    .on('click', (event, d) => showNodeDetail(d))
    .call(d3.drag()
      .on('start', dragstart)
      .on('drag',  dragged)
      .on('end',   dragend));

  node.append('circle')
    .attr('class','node-circle')
    .attr('r', nodeSize)
    .attr('fill', d => scoreColor(d.score || 0))
    .attr('fill-opacity', 0.85)
    .attr('stroke', d => d.is_driver ? '#ef4444' : d.is_target ? '#4a9eff' : '#ffffff')
    .attr('stroke-width', d => (d.is_driver || d.is_target) ? 3 : 2)
    .attr('stroke-opacity', 0.9);

  // Score-Ring (animierter Rand)
  node.filter(d => (d.score || 0) > 40).append('circle')
    .attr('r', d => nodeSize(d) + 5)
    .attr('fill','none')
    .attr('stroke', d => scoreColor(d.score || 0))
    .attr('stroke-width', 1)
    .attr('stroke-opacity', 0.4)
    .attr('stroke-dasharray', '3,3');

  node.append('text')
    .attr('class','node-label')
    .attr('text-anchor','middle').attr('dy', -4)
    .text(d => d.label);

  node.append('text')
    .attr('class','node-score')
    .attr('text-anchor','middle').attr('dy', 9)
    .text(d => d.score >= 0 ? Math.round(d.score) : '—');

  // Force Simulation
  simulation = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(data.links)
      .id(d => d.id).distance(d => {{
        const risk = d.risk;
        return risk === 'critical' ? 120 : risk === 'high' ? 160 : 200;
      }}).strength(0.4))
    .force('charge',  d3.forceManyBody().strength(-400))
    .force('center',  d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide(d => nodeSize(d) + 18))
    .on('tick', () => {{
      link
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      linkLabel
        .attr('x', d => (d.source.x + d.target.x) / 2)
        .attr('y', d => (d.source.y + d.target.y) / 2);
      node.attr('transform', d => `translate(${{d.x}},${{d.y}})`);
    }});
}}

function dragstart(event, d) {{
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}}
function dragged(event, d) {{ d.fx = event.x; d.fy = event.y; }}
function dragend(event, d) {{
  if (!event.active) simulation.alphaTarget(0);
  d.fx = null; d.fy = null;
}}

// ── Sidebar: Mitglieder ──────────────────────────────────────────────────────
function renderMembers(data) {{
  const el = document.getElementById('members-list');
  el.innerHTML = data.nodes.map(n => {{
    const pct = Math.max(0, Math.min(100, n.score || 0));
    const col = scoreColor(n.score || 0);
    const roleTag = n.is_driver
      ? '<span class="role-badge role-driver">TREIBER</span>'
      : n.is_target
      ? '<span class="role-badge role-target">ZIEL</span>'
      : '';
    return `<div class="member-row" onclick="showNodeDetail(currentData.nodes.find(x=>x.id==='${{n.id}}'))">
      <div class="member-dot" style="background:${{col}}"></div>
      <span class="member-name">${{n.label}} ${{roleTag}}</span>
      <div class="member-bar-wrap"><div class="member-bar" style="width:${{pct}}%;background:${{col}}"></div></div>
      <span class="member-score" style="color:${{col}}">${{n.score >= 0 ? Math.round(n.score) : '—'}}</span>
    </div>`;
  }}).join('');
}}

// ── Theater Score ────────────────────────────────────────────────────────────
function renderTheaterScore(data) {{
  const scores = data.nodes.map(n => n.score || 0).filter(s => s >= 0);
  if (!scores.length) return;
  const weights = {{}};
  data.nodes.forEach(n => weights[n.id] = n.weight || 0.1);
  let totalW = 0, weighted = 0;
  data.nodes.forEach(n => {{
    const w = n.weight || 0.1;
    totalW += w;
    weighted += (n.score || 0) * w;
  }});
  const score = totalW > 0 ? weighted / totalW : 0;
  const el = document.getElementById('theater-score-val');
  el.textContent = Math.round(score);
  el.style.color = scoreColor(score);
}}

// ── Dept Heatmap ─────────────────────────────────────────────────────────────
function renderHeatmap(data) {{
  const depts = ['OSINT','GEOINT','SIGINT','HUMINT','ECONINT','HUMANA'];
  const icons  = {{OSINT:'⚡',GEOINT:'🛰',SIGINT:'📡',HUMINT:'👤',ECONINT:'📊',HUMANA:'🏥'}};
  let html = '<table class="heatmap-table"><thead><tr><th></th>';
  depts.forEach(d => {{ html += `<th title="${{d}}">${{icons[d]||d}}</th>`; }});
  html += '</tr></thead><tbody>';
  data.nodes.forEach(n => {{
    html += `<tr><td style="font-size:10px;color:#8b949e;text-align:left;padding-right:4px">${{n.label}}</td>`;
    depts.forEach(d => {{
      const s = n.depts ? n.depts[d] : -1;
      const col = s >= 0 ? scoreColor(s) : '#374151';
      const txt = s >= 0 ? Math.round(s) : '—';
      html += `<td><span class="heatmap-cell" style="color:${{col}}">${{txt}}</span></td>`;
    }});
    html += '</tr>';
  }});
  html += '</tbody></table>';
  document.getElementById('heatmap-container').innerHTML = html;
}}

// ── Timeline ─────────────────────────────────────────────────────────────────
function renderTimeline(data) {{
  const all = [];
  data.nodes.forEach(n => {{
    (n.events || []).forEach(e => {{
      all.push({{...e, region: n.label}});
    }});
  }});
  all.sort((a,b) => (b.time||'').localeCompare(a.time||''));
  const el = document.getElementById('timeline-list');
  if (!all.length) {{
    el.innerHTML = '<span class="no-events">Keine Ereignisse in den letzten 48h</span>';
    return;
  }}
  el.innerHTML = all.slice(0,15).map(e => {{
    const typeClass = e.type === 'conflict' ? 'ti-type-conflict' : 'ti-type-news';
    return `<div class="timeline-item ${{typeClass}}">
      <div class="ti-meta">${{e.region}} · ${{e.source}} · ${{(e.time||'').substring(0,16)}}</div>
      <div class="ti-text">${{e.text||'—'}}</div>
    </div>`;
  }}).join('');
}}

// ── Aktive Ketten ────────────────────────────────────────────────────────────
function renderChains(data) {{
  const el = document.getElementById('chains-list');
  const active = data.links.filter(l => {{
    const src = data.nodes.find(n => n.id === (l.source.id || l.source));
    const dst = data.nodes.find(n => n.id === (l.target.id || l.target));
    const ss = src ? (src.score || 0) : 0;
    const ds = dst ? (dst.score || 0) : 0;
    return (ss >= 40 || ds >= 40) && ['attacks','funds_arms','threatens'].includes(l.relation);
  }});
  if (!active.length) {{
    el.innerHTML = '<span class="no-events">Keine erhöhten Ketten</span>';
    return;
  }}
  el.innerHTML = active.map(l => {{
    const sid = l.source.id || l.source;
    const did = l.target.id || l.target;
    const src = data.nodes.find(n => n.id === sid);
    const dst = data.nodes.find(n => n.id === did);
    const ss  = src ? Math.round(src.score || 0) : '—';
    const ds  = dst ? Math.round(dst.score || 0) : '—';
    return `<div class="chain-item">
      <span class="chain-src">${{sid}}(${{ss}})</span>
      <span style="color:#8b949e"> → </span>
      <span class="chain-dst">${{did}}(${{ds}})</span>
      <div class="chain-via">via ${{l.via||'?'}} · ${{l.label||''}}</div>
    </div>`;
  }}).join('');
}}

// ── Node Detail ──────────────────────────────────────────────────────────────
function showNodeDetail(d) {{
  if (!d) return;
  document.getElementById('node-detail').style.display = 'block';
  document.getElementById('detail-title').textContent  = d.label;
  document.getElementById('detail-title').style.color  = scoreColor(d.score || 0);

  const depts = ['OSINT','GEOINT','SIGINT','HUMINT','ECONINT','HUMANA'];
  const icons  = {{OSINT:'⚡',GEOINT:'🛰',SIGINT:'📡',HUMINT:'👤',ECONINT:'📊',HUMANA:'🏥'}};
  document.getElementById('detail-depts').innerHTML = depts.map(dep => {{
    const s = d.depts ? d.depts[dep] : -1;
    return `<div class="detail-dept">
      <div class="dd-label">${{icons[dep]}} ${{dep}}</div>
      <div class="dd-score" style="color:${{scoreColor(s)}}">${{s >= 0 ? Math.round(s) : '—'}}</div>
    </div>`;
  }}).join('');

  const evs = d.events || [];
  const evHtml = evs.length
    ? `<h4>Letzte Ereignisse</h4>` + evs.slice(0,4).map(e =>
        `<div class="detail-event"><b style="color:#8b949e">${{e.source}}</b> · ${{e.text||'—'}}</div>`
      ).join('')
    : '<span class="no-events" style="font-size:10px">Keine Ereignisse</span>';
  document.getElementById('detail-events').innerHTML = evHtml;
}}
function closeDetail() {{ document.getElementById('node-detail').style.display = 'none'; }}

// ── Render All ───────────────────────────────────────────────────────────────
function renderAll(data) {{
  currentData = data;
  initNetwork(data);
  renderMembers(data);
  renderTheaterScore(data);
  renderHeatmap(data);
  renderTimeline(data);
  renderChains(data);
  document.getElementById('last-refresh').textContent =
    'Aktualisiert: ' + new Date().toLocaleTimeString('de-DE');
}}

// ── Live Refresh ─────────────────────────────────────────────────────────────
async function refreshData() {{
  if (STATIC_MODE) {{ renderAll(INITIAL_DATA); return; }}
  try {{
    const resp = await fetch(
      `${{LIVE_SERVER}}/api/theater?name=${{encodeURIComponent(INITIAL_DATA.theater_name)}}`,
      {{signal: AbortSignal.timeout(15000)}}
    );
    if (resp.ok) {{
      const fresh = await resp.json();
      renderAll(fresh);
    }} else {{
      renderAll(currentData);
    }}
  }} catch (e) {{
    // Live-Server nicht erreichbar — Fallback auf eingebettete Daten
    renderAll(currentData);
    document.getElementById('refresh-status').textContent = 'Offline (eingebettete Daten)';
  }}
}}

// ── Start ────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {{
  renderAll(INITIAL_DATA);
  if (!STATIC_MODE) {{
    setInterval(refreshData, {live_server_port > 0 and "AUTO_REFRESH_MS" or "999999999"});
  }}
}});

window.addEventListener('resize', () => {{
  if (currentData) initNetwork(currentData);
}});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# Haupt-Funktion
# ═══════════════════════════════════════════════════════════════════════════════

def generate_theater_html(
    theater_name:     str,
    output_path:      Optional[str] = None,
    static:           bool = False,
    open_browser:     bool = False,
    live_server_port: int  = 5000,
) -> str:
    """
    Erstellt das Theater-Dashboard und speichert es.

    Returns
    -------
    Pfad zur erzeugten HTML-Datei
    """
    print(f"  → Lade Theater-Daten für {theater_name}…")
    data = build_theater_data(theater_name, static=static)

    print(f"  → Erzeuge HTML ({len(data['nodes'])} Nodes, {len(data['links'])} Links)…")
    html = generate_html(data, live_server_port=live_server_port)

    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(__file__),
            f"nexus_theater_{theater_name.lower()}.html"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  ✓ Gespeichert: {output_path}")

    if open_browser:
        webbrowser.open(f"file://{os.path.abspath(output_path)}")
        print(f"  ✓ Browser geöffnet")

    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="nexus_theater_web",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--theater", "-t", default="MiddleEast",
        help="Theater-Name (default: MiddleEast)")
    ap.add_argument("--out", "-o", default=None,
        help="Ausgabepfad für die HTML-Datei")
    ap.add_argument("--static", action="store_true",
        help="Kein Live-Refresh (nur eingebettete Daten)")
    ap.add_argument("--open", action="store_true", dest="open_browser",
        help="Datei automatisch im Browser öffnen")
    ap.add_argument("--port", type=int, default=5000,
        help="Port des nexus_live_server.py (default: 5000)")
    ap.add_argument("--list", "-l", action="store_true",
        help="Alle verfügbaren Theater auflisten")

    args = ap.parse_args()

    if args.list:
        try:
            from nexus_theater import list_theaters
            list_theaters()
        except ImportError:
            print("✗ nexus_theater.py nicht gefunden", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    try:
        path = generate_theater_html(
            theater_name=args.theater,
            output_path=args.out,
            static=args.static,
            open_browser=args.open_browser,
            live_server_port=args.port,
        )
        print(f"\n  Öffnen: file://{os.path.abspath(path)}")
    except Exception as e:
        print(f"✗ Fehler: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
