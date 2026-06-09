"""
NEXUS - HTML-Lagebild-Report Generator v2
Interaktive Leaflet-Karte + Wetter + Schiffe + Nachrichten.
"""

from __future__ import annotations

import json
import os
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional


# ======================================================
# HTML-Template
# ======================================================

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NEXUS Lagebild – {timestamp}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
/* Leaflet Kritisch-CSS Fallback */
.leaflet-pane,.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow,
.leaflet-tile-container,.leaflet-zoom-box,.leaflet-image-layer,.leaflet-layer{{
  position:absolute;left:0;top:0
}}
.leaflet-container{{overflow:hidden;position:relative}}
.leaflet-tile{{visibility:hidden}}
.leaflet-tile-loaded{{visibility:visible}}
.leaflet-zoom-animated{{-webkit-transform-origin:0 0;transform-origin:0 0}}
.leaflet-tile-pane{{z-index:200}}
.leaflet-overlay-pane{{z-index:400}}
.leaflet-shadow-pane{{z-index:500}}
.leaflet-marker-pane{{z-index:600}}
.leaflet-popup-pane{{z-index:700}}
.leaflet-pane{{z-index:400}}
.leaflet-top,.leaflet-bottom{{position:absolute;z-index:1000;pointer-events:none}}
.leaflet-top{{top:0}}
.leaflet-bottom{{bottom:0}}
.leaflet-left{{left:0}}
.leaflet-right{{right:0}}
.leaflet-control{{pointer-events:auto;float:left;clear:both}}
.leaflet-right .leaflet-control{{float:right}}
.leaflet-popup{{position:absolute;text-align:center;margin-bottom:20px}}
.leaflet-popup-content-wrapper{{padding:1px;text-align:left;border-radius:12px;background:#fff;color:#333}}
.leaflet-popup-content{{margin:13px 24px 13px 20px;line-height:1.3;font-size:13px}}
.leaflet-popup-tip-container{{margin:0 auto;width:40px;height:20px;position:relative;overflow:hidden}}
.leaflet-popup-tip{{background:#fff;width:17px;height:17px;padding:1px;margin:-10px auto 0;transform:rotate(45deg)}}
.leaflet-popup-close-button{{position:absolute;top:0;right:0;border:none;width:24px;height:24px;
  font:16px/24px Tahoma,sans-serif;color:#757575;text-decoration:none;background:transparent}}
.leaflet-div-icon{{background:#fff;border:1px solid #666}}
</style>
<style>
  :root {{
    --bg:#060a10; --surface:rgba(10,18,28,0.85); --border:#1e3a4a;
    --accent:#00d4ff; --green:#00ff88; --yellow:#ffd700;
    --red:#ff4444; --orange:#ff8800; --text:#c8d6e0;
    --muted:#4a6070; --font:'Courier New',monospace;
    --glass:rgba(0,212,255,0.04); --glow-accent:0 0 20px rgba(0,212,255,0.15);
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;line-height:1.5;
    background-image:radial-gradient(ellipse at 20% 20%,rgba(0,40,80,0.4) 0%,transparent 60%),
                     radial-gradient(ellipse at 80% 80%,rgba(0,20,40,0.3) 0%,transparent 50%)}}
  a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}

  .header{{background:linear-gradient(90deg,#040d18,#07152a,#040d18);
    border-bottom:2px solid var(--accent);padding:12px 20px;
    display:flex;justify-content:space-between;align-items:center;
    box-shadow:0 2px 30px rgba(0,212,255,0.12);position:relative;overflow:hidden}}
  .header::before{{content:'';position:absolute;inset:0;
    background:linear-gradient(90deg,transparent,rgba(0,212,255,0.04),transparent);
    animation:hdr-sweep 5s ease-in-out infinite}}
  @keyframes hdr-sweep{{0%,100%{{transform:translateX(-100%)}}50%{{transform:translateX(100%)}}}}
  .header-title{{color:var(--accent);font-size:18px;font-weight:bold;letter-spacing:4px;
    text-shadow:0 0 20px rgba(0,212,255,0.5)}}
  .header-sub{{color:var(--muted);font-size:10px;letter-spacing:2px}}
  .header-time{{color:var(--green);font-size:13px;text-align:right;
    text-shadow:0 0 10px rgba(0,255,136,0.4)}}

  .statusbar{{background:rgba(6,12,18,0.9);border-bottom:1px solid var(--border);padding:5px 20px;
    display:flex;gap:20px;font-size:11px;flex-wrap:wrap;backdrop-filter:blur(4px)}}
  .si{{display:flex;align-items:center;gap:5px}}
  .dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
  .dg{{background:var(--green);box-shadow:0 0 5px var(--green)}}
  .dy{{background:var(--yellow);box-shadow:0 0 5px var(--yellow)}}
  .dr{{background:var(--red);box-shadow:0 0 5px var(--red)}}
  .dm{{background:var(--muted)}}

  /* Haupt-Grid – Glassmorphism */
  .main-grid{{display:grid;grid-template-columns:1fr 1fr;gap:2px;background:rgba(0,0,0,0.4)}}
  .panel{{background:var(--surface);padding:16px;overflow:auto;position:relative;
    backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
    border:1px solid rgba(30,58,74,0.6);
    transition:box-shadow 0.3s ease}}
  .panel:hover{{box-shadow:inset 0 0 30px rgba(0,212,255,0.03),0 0 1px rgba(0,212,255,0.2)}}
  .panel-full{{grid-column:1/-1}}
  .ph{{display:flex;justify-content:space-between;align-items:center;
    border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:12px}}
  .pt{{color:var(--accent);font-size:10px;letter-spacing:3px;text-transform:uppercase;
    text-shadow:0 0 12px rgba(0,212,255,0.4)}}
  .pb{{background:rgba(30,58,74,0.6);color:var(--text);padding:2px 7px;border-radius:3px;
    font-size:10px;backdrop-filter:blur(4px)}}
  .pb-alert{{background:rgba(58,21,0,0.8);color:var(--orange);border:1px solid rgba(255,136,0,0.3)}}
  .pb-ok{{background:rgba(0,58,21,0.8);color:var(--green);border:1px solid rgba(0,255,136,0.3)}}
  .pb-blue{{background:rgba(0,48,80,0.8);color:var(--accent);border:1px solid rgba(0,212,255,0.3)}}

  /* Nachrichten */
  .ni{{border-left:2px solid var(--border);padding:7px 10px;margin-bottom:8px;
    background:rgba(0,212,255,0.02);border-radius:0 4px 4px 0;
    transition:all 0.2s ease}}
  .ni:hover{{border-left-color:var(--accent);background:rgba(0,212,255,0.05);
    transform:translateX(2px)}}
  .nt{{font-weight:bold;margin-bottom:2px}}
  .nm{{color:var(--muted);font-size:10px;margin-bottom:3px}}
  .nb{{color:#8aa0b0;font-size:11px}}
  .tag{{display:inline-block;padding:1px 5px;border-radius:2px;font-size:9px;margin-right:3px}}
  .tnew{{background:#2a1500;color:var(--orange)}}
  .tok{{background:#002a10;color:var(--green)}}

  /* Karte */
  #nexus-map{{height:500px;width:100%;background:#0a1020}}
  .map-legend{{background:rgba(10,14,20,0.9);border:1px solid var(--border);
    padding:8px 12px;font-size:11px;line-height:1.8}}
  .map-legend .li{{display:flex;align-items:center;gap:6px;margin-bottom:3px}}

  /* Wetter */
  .weather-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}}
  .wstat{{background:rgba(0,20,40,0.6);border:1px solid rgba(30,58,74,0.8);padding:10px;border-radius:6px;
    text-align:center;backdrop-filter:blur(4px);transition:all 0.2s;
    box-shadow:inset 0 1px 0 rgba(0,212,255,0.05)}}
  .wstat:hover{{border-color:rgba(0,212,255,0.3);box-shadow:0 0 15px rgba(0,212,255,0.08)}}
  .wstat-val{{font-size:22px;font-weight:bold;color:var(--accent);display:block}}
  .wstat-lbl{{font-size:10px;color:var(--muted)}}
  .ops-item{{display:flex;justify-content:space-between;padding:5px 0;
    border-bottom:1px solid #0d1a24;font-size:12px}}
  .ops-ok{{color:var(--green)}} .ops-warn{{color:var(--yellow)}} .ops-bad{{color:var(--red)}}
  .dust-warn{{background:#2a1000;border:1px solid var(--orange);
    color:var(--orange);padding:8px;border-radius:4px;margin:8px 0;font-size:12px}}

  /* Maritime */
  .ship-item{{display:flex;justify-content:space-between;padding:5px 0;
    border-bottom:1px solid #0d1a24;font-size:12px}}
  .ship-alert{{color:var(--orange)}}

  /* Analyse */
  .analysis-text{{white-space:pre-wrap;color:var(--text);font-size:12px;line-height:1.8;
    background:rgba(0,12,24,0.7);padding:14px;border:1px solid rgba(30,58,74,0.8);
    border-radius:6px;backdrop-filter:blur(4px);
    box-shadow:inset 0 0 40px rgba(0,212,255,0.02)}}
  .asec{{color:var(--accent);font-weight:bold;display:block;margin-top:10px;letter-spacing:1px}}

  /* Forecast */
  .fc-row{{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}}
  .fc-item{{background:rgba(0,16,32,0.7);border:1px solid rgba(30,58,74,0.8);padding:8px 12px;
    border-radius:6px;min-width:100px;text-align:center;flex-shrink:0;
    backdrop-filter:blur(4px);transition:all 0.2s ease;cursor:default}}
  .fc-item:hover{{border-color:rgba(0,212,255,0.4);transform:translateY(-2px);
    box-shadow:0 4px 20px rgba(0,212,255,0.1)}}
  .fc-time{{color:var(--accent);font-size:10px;margin-bottom:4px}}
  .fc-desc{{font-size:11px;margin-bottom:3px}}
  .fc-wind{{color:var(--muted);font-size:10px}}

  /* Live-Indikator */
  .live-bar{{background:#060c12;border-bottom:1px solid var(--border);
    padding:4px 20px;display:flex;align-items:center;gap:12px;font-size:11px}}
  .live-dot{{width:8px;height:8px;border-radius:50%;background:var(--red);
    animation:pulse 1.5s ease-in-out infinite}}
  @keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(255,68,68,.6)}}
    50%{{opacity:.7;box-shadow:0 0 0 5px rgba(255,68,68,0)}}}}
  .live-dot.offline{{background:var(--muted);animation:none}}
  .new-marker{{animation:newflash .8s ease-out}}
  @keyframes newflash{{0%{{transform:scale(2.5);opacity:.5}}100%{{transform:scale(1);opacity:1}}}}
  @keyframes userpulse{{0%,100%{{box-shadow:0 0 0 0 rgba(68,136,255,.7)}}50%{{box-shadow:0 0 0 9px rgba(68,136,255,0)}}}}
  .user-dot{{width:14px;height:14px;background:#4488ff;border-radius:50%;border:2px solid #aaccff;animation:userpulse 2s ease-in-out infinite}}
  .gdelt-dot{{width:10px;height:10px;background:#ffd700;border-radius:50%;border:1px solid #ffee88;box-shadow:0 0 6px #ffd700;opacity:.85}}

  .footer{{border-top:1px solid var(--border);padding:6px 20px;color:var(--muted);
    font-size:10px;display:flex;justify-content:space-between}}

  ::-webkit-scrollbar{{width:5px;height:5px}}
  ::-webkit-scrollbar-track{{background:var(--bg)}}
  ::-webkit-scrollbar-thumb{{background:var(--border);border-radius:3px}}

  /* ── LIGHT MODE ─────────────────────────────────────── */
  body.light-mode {{
    --bg:#f0f4f8; --surface:#ffffff; --border:#c8d6e0;
    --accent:#0066aa; --green:#007733; --yellow:#aa8800;
    --red:#cc2222; --orange:#cc5500; --text:#1a2a3a;
    --muted:#5a7080; --font:'Courier New',monospace;
  }}
  body.light-mode .header{{background:linear-gradient(90deg,#c0d8f0,#d4e8ff);border-bottom-color:#0066aa}}
  body.light-mode .header-title{{color:#0066aa}}
  body.light-mode .header-time{{color:#007733}}
  body.light-mode .statusbar{{background:#e8f0f8;border-bottom-color:#c8d6e0}}
  body.light-mode .live-bar{{background:#e8f0f8;border-bottom-color:#c8d6e0}}
  body.light-mode #nexus-map{{background:#d0e0f0}}
  body.light-mode .map-legend{{background:rgba(240,244,248,0.95)}}
  body.light-mode .analysis-text{{background:rgba(248,252,255,0.9);border-color:#c8d6e0}}
  body.light-mode .wstat{{background:rgba(240,248,255,0.9);border-color:#c8d6e0}}
  body.light-mode .panel{{background:rgba(255,255,255,0.85);backdrop-filter:blur(8px)}}
  body.light-mode .fc-item{{background:rgba(240,248,255,0.8);border-color:#c8d6e0}}
  body.light-mode .fc-item{{background:#f0f8ff;border-color:#c8d6e0}}
  body.light-mode #esc-bar{{background:#f8f0ff;border-bottom-color:#8800aa}}
  body.light-mode #surge-banner{{background:#fff8e0;border-bottom-color:#aa6600}}
  body.light-mode #sat-ticker{{background:#e8f4ff;border-bottom-color:#0055aa}}
  body.light-mode .ni{{border-left-color:#c8d6e0}}
  /* ── LLM-Box ── */
  .llm-box{{background:#060d18;border:1px solid #1a3a5c;border-left:3px solid #3b82f6;
    border-radius:6px;padding:12px 16px;margin-top:8px;font-family:'Courier New',monospace}}
  .llm-box.llm-offline{{border-left-color:#334155;opacity:0.6}}
  .llm-label{{font-size:9px;letter-spacing:2px;color:#3b82f6;font-weight:700;
    text-transform:uppercase;margin-bottom:6px;display:flex;align-items:center;gap:8px}}
  .llm-label.offline{{color:#475569}}
  .llm-expl{{font-size:12px;color:#94a3b8;line-height:1.6;margin-bottom:6px}}
  .llm-brief{{font-size:11px;color:#64748b;line-height:1.5;border-top:1px solid #1e293b;
    padding-top:6px;margin-top:4px}}
  body.light-mode .llm-box{{background:#f0f7ff;border-color:#bcd4ec;border-left-color:#3b82f6}}
  body.light-mode .llm-expl{{color:#334155}}
  body.light-mode .llm-brief{{color:#64748b;border-top-color:#dce8f0}}
  body.light-mode .ni:hover{{border-left-color:#0066aa}}
  body.light-mode ::-webkit-scrollbar-track{{background:#e8f0f8}}
  body.light-mode ::-webkit-scrollbar-thumb{{background:#c8d6e0}}

  /* Toggle-Button */
  #theme-toggle{{
    background:transparent;border:1px solid var(--border);
    color:var(--text);padding:3px 10px;border-radius:3px;
    cursor:pointer;font-family:var(--font);font-size:11px;
    transition:all .2s;white-space:nowrap;
  }}
  #theme-toggle:hover{{border-color:var(--accent);color:var(--accent)}}

  /* ── MOBILE RESPONSIVE ──────────────────────────────── */
  @media(max-width:768px){{
    .header{{flex-direction:column;gap:8px;padding:10px 14px;text-align:center}}
    .header-title{{font-size:15px;letter-spacing:2px}}
    .header-time{{text-align:center}}
    .statusbar{{padding:4px 10px;gap:10px;font-size:10px}}
    .live-bar{{padding:4px 10px;gap:8px;font-size:10px;flex-wrap:wrap}}
    #surge-banner,#sat-ticker,#esc-bar{{padding:4px 10px;font-size:10px}}
    .main-grid{{grid-template-columns:1fr!important}}
    .panel-full{{grid-column:1!important}}
    .panel{{padding:10px}}
    #nexus-map{{height:320px}}
    .weather-grid{{grid-template-columns:1fr 1fr}}
    .wstat-val{{font-size:16px}}
    .map-legend{{font-size:10px}}
    .fc-item{{min-width:80px;padding:6px 8px}}
    .footer{{flex-direction:column;gap:2px;font-size:10px}}
    button[onclick]{{font-size:10px;padding:3px 8px}}
  }}
  @media(max-width:420px){{
    #nexus-map{{height:260px}}
    .weather-grid{{grid-template-columns:1fr}}
    .header-title{{font-size:13px;letter-spacing:1px}}
  }}
  /* ── Sparkline + Refresh-Controls ───────────────────── */
  .sparkline-panel{{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:8px 10px}}
  #nexus-sparkline-wrap{{position:relative;height:120px}}
  #sparkline-footer{{font-size:10px;color:var(--muted);padding:3px 2px;font-family:'Courier New',monospace;letter-spacing:.3px}}
  .refresh-ctrl{{display:flex;align-items:center;gap:4px;margin-left:8px}}
  .refresh-ctrl button{{background:#0d1a2a;border:1px solid #1a3050;color:#6a90a8;font-family:'Courier New',monospace;font-size:10px;padding:1px 5px;border-radius:3px;cursor:pointer}}
  .refresh-ctrl button:hover{{background:#1a3050;color:#c8d6e0}}
  #refresh-countdown{{font-size:10px;color:var(--muted);min-width:60px;font-family:'Courier New',monospace}}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="header-title">◈ NEXUS LAGEBILD</div>
    <div class="header-sub">OSINT INTELLIGENCE DASHBOARD</div>
  </div>
  <div class="header-time">
    <div>{timestamp}</div>
    <div style="color:var(--muted);font-size:10px">UTC {timestamp_utc}</div>
    <button id="theme-toggle" onclick="toggleTheme()" style="margin-top:5px">☀ HELL / 🌙 DUNKEL</button>
  </div>
</div>

<div class="live-bar">
  <span class="live-dot" id="live-dot"></span>
  <span id="live-status" style="color:var(--muted)">Verbinde mit NEXUS Live...</span>
  <span style="color:var(--border)">|</span>
  <span id="refresh-countdown" title="Nächster Auto-Refresh"></span>
  <div class="refresh-ctrl" title="Refresh-Intervall">
    <button onclick="adjustRefresh(-1)">−</button>
    <span id="refresh-interval-label" style="color:#6a90a8;font-family:'Courier New',monospace;font-size:10px">3 min</span>
    <button onclick="adjustRefresh(+1)">+</button>
  </div>
  <span style="margin-left:auto;color:var(--muted)">Live-API: <span id="live-api-host">…</span>:11430</span>
  <script>document.getElementById('live-api-host').textContent=window.location.hostname;</script>
</div>
<div id="surge-banner" style="display:none;background:#1a0800;border-bottom:2px solid #ff4444;padding:5px 18px;font-family:'Courier New',monospace;font-size:11px;color:#ff8800;letter-spacing:0.5px">📡 TELEGRAM SURGE:&nbsp;</div>
<div id="sat-ticker" style="display:none;background:#020d1a;border-bottom:1px solid #003366;padding:4px 18px;font-family:'Courier New',monospace;font-size:10px;color:#4488cc;letter-spacing:0.5px">🛰 SATELLIT:&nbsp;</div>
<div id="esc-bar" style="display:none;background:#0a0010;border-bottom:2px solid #880022;padding:5px 18px;font-family:'Courier New',monospace;font-size:11px;letter-spacing:0.5px;display:flex;align-items:center;gap:12px">
  <span id="esc-icon" style="font-size:16px">🟢</span>
  <span id="esc-label" style="font-weight:bold;letter-spacing:2px">GRUEN</span>
  <div style="flex:1;background:#111;border-radius:3px;height:8px;overflow:hidden;margin:0 4px">
    <div id="esc-fill" style="height:100%;background:#00ff88;border-radius:3px;transition:width .5s,background .5s;width:0%"></div>
  </div>
  <span id="esc-score" style="color:#8aa0b0;font-size:10px">0/100</span>
  <span id="esc-signals" style="color:#4a6070;font-size:10px"></span>
</div>

<div class="statusbar">{status_items}</div>

<!-- ROW 1: Karte (voll) -->
<div class="main-grid">
  <div class="panel panel-full">
    <div class="ph">
      <span class="pt">🗺 LAGE-KARTE – {query_display}</span>
      <span class="pb pb-blue">Leaflet · Live</span>
    </div>
    <div id="nexus-map"></div>
    <div style="margin-top:8px;display:flex;gap:20px;flex-wrap:wrap">
      <div class="map-legend">
        <div class="li"><span style="color:#ff4444">✈</span> Auffälliges Flugzeug</div>
        <div class="li"><span style="color:#00ff88">✈</span> Normaler Verkehr</div>
        <div class="li"><span style="color:#ff9900">🚁</span> Hubschrauber</div>
        <div class="li"><span style="color:#ff8800">⚓</span> Maritime Alarm</div>
        <div class="li"><span style="color:#00d4ff">⚓</span> Maritime Ruhig</div>
        <div class="li"><span style="color:#ff2222">⬤</span> Vorfall aus Nachrichten</div>
        <div class="li"><span style="color:#ffd700">⬤</span> GDELT-Ereignis</div>
        <div class="li"><span style="color:#4488ff">⬤</span> Mein Standort</div>
        <div class="li"><span style="color:#ff6600">🌍</span> Erdbeben</div>
        <div class="li"><span style="color:#ff0044">💥</span> Mgl. Detonation</div>
        <div class="li"><span style="color:#ff8800">⚡</span> Artillerie-Signal</div>
        <div class="li"><span style="color:#cc66ff">🚢</span> Tiefgang-Delta</div>
        <div class="li"><span style="color:#cc44ff">🔎</span> ISR-Aufklärer</div>
        <div class="li"><span style="color:#ff2255">⚡</span> GPS-Jamming-Zone</div>
        <div class="li"><span style="color:#ff9900">▣</span> NOTAM-Sperrzone</div>
        <div class="li"><span style="color:#ff00ff">⚡</span> Korreliertes Ereignis</div>
        <div class="li"><span style="color:#ff4400">🔥</span> NASA FIRMS Brand</div>
        <div class="li"><span style="color:#ff0000">💥</span> ACLED Konflikt (GPS)</div>
        <div class="li"><span style="color:#00d4ff">🛰</span> Klick → Sentinel-2</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;margin-left:10px">
        <button onclick="goToTarget()" style="background:#001a10;color:#00ff88;border:1px solid #00ff88;
          padding:4px 12px;border-radius:3px;cursor:pointer;font-family:monospace;font-size:11px">
          🎯 Ziel: {query_display}</button>
        <button onclick="locateMe()" style="background:#001830;color:#4488ff;border:1px solid #4488ff;
          padding:4px 12px;border-radius:3px;cursor:pointer;font-family:monospace;font-size:11px">
          📍 Mein Standort</button>
        <button onclick="openGmaps()" style="background:#001a30;color:#34a853;border:1px solid #34a853;
          padding:4px 12px;border-radius:3px;cursor:pointer;font-family:monospace;font-size:11px">
          🗺 Google Satellit</button>
        <button onclick="openCopernicus()" style="background:#001a30;color:#00d4ff;border:1px solid #00d4ff;
          padding:4px 12px;border-radius:3px;cursor:pointer;font-family:monospace;font-size:11px">
          🛰 zoom.earth Sat</button>
      </div>
    </div>
  </div>
</div>

<!-- ROW 2: Flug-Details + Wetter -->
<div class="main-grid" style="margin-top:1px">

  <div class="panel">
    <div class="ph">
      <span class="pt">✈ FLUGLAGEBILD</span>
      <span class="pb {flights_badge_class}">{flights_badge}</span>
    </div>
    {flights_html}
  </div>

  <div class="panel">
    <div class="ph">
      <span class="pt">⛅ WETTER + OPERATIVE LAGE</span>
      <span class="pb {weather_badge_class}">{weather_badge}</span>
    </div>
    {weather_html}
  </div>

</div>

<!-- ROW 3: Analyse (voll) -->
<div class="main-grid" style="margin-top:1px">
  <div class="panel panel-full">
    <div class="ph">
      <span class="pt">◈ NEXUS ANALYSE</span>
      <span class="pb pb-ok">KI-Synthese aller Quellen</span>
    </div>
    <div class="analysis-text">{analysis_html}</div>
  </div>
</div>

<!-- LLM-KI-Analyse Box (Ebene 4) -->
<div id="llm-analysis-row" style="margin-top:1px">{llm_box_html}</div>

<!-- ROW 4b: Score-Verlauf + 48h-Vorschau -->
<div class="main-grid" style="margin-top:1px" id="sparkline-row">
  <div class="panel panel-full sparkline-panel">
    <div class="ph">
      <span class="pt">📈 ESKALATIONS-TREND</span>
      <span class="pb pb-blue" id="sparkline-subtitle">72h Verlauf · 48h Vorschau</span>
    </div>
    <div id="nexus-sparkline-wrap"><canvas id="nexus-sparkline"></canvas></div>
    <div id="sparkline-footer">Lade Verlaufsdaten…</div>
  </div>
</div>

<!-- ROW 5: Timeline (voll) -->
<div class="main-grid" style="margin-top:1px">
  <div class="panel panel-full">
    <div class="ph">
      <span class="pt">⏱ EREIGNIS-TIMELINE</span>
      <span class="pb pb-blue">Letzte 24h · chronologisch</span>
    </div>
    <div id="timeline-container" style="overflow-x:auto;padding-bottom:6px">
      <div id="timeline" style="position:relative;min-height:80px;padding:10px 0 20px 0"></div>
    </div>
  </div>
</div>

<!-- ROW 6: Maritime / Ereignisse (voll) -->
<div class="main-grid" style="margin-top:1px">
  <div class="panel panel-full">
    <div class="ph">
      <span class="pt">{events_panel_title}</span>
      <span class="pb {maritime_badge_class}">{maritime_badge}</span>
    </div>
    {maritime_html}
  </div>
</div>

<!-- ROW 7: Nachrichten (voll, ganz unten) -->
<div class="main-grid" style="margin-top:1px">
  <div class="panel panel-full">
    <div class="ph">
      <span class="pt">▶ NACHRICHTENLAGE</span>
      <span class="pb">{news_count} Artikel</span>
    </div>
    {news_html}
  </div>
</div>

<div class="footer">
  <span>NEXUS OSINT v0.6 | Nur für informatorische Zwecke | Quellen immer direkt prüfen</span>
  <span>{timestamp}</span>
</div>

<script>
// ── Globaler Fehler-Catcher (Debug) ──────────────────────────────────────────
window.onerror = function(msg, src, line, col, err) {{
  const d = document.getElementById('nexus-map');
  if (d) d.innerHTML = '<div style="padding:20px;font-family:monospace;color:#ff4444;font-size:12px">'
    + '<b>⚠ JavaScript-Fehler (Karte konnte nicht geladen werden):</b><br><br>'
    + msg + '<br>Zeile: ' + line + '</div>';
  const lb = document.getElementById('live-status');
  if (lb) lb.textContent = 'JS-Fehler: ' + msg.substring(0,80);
  return false;
}};

// ── Leaflet-Karte ────────────────────────────────────────────────────────────
if (typeof L === 'undefined') {{
  document.getElementById('nexus-map').innerHTML =
    '<div style="color:#ff4444;padding:30px;font-family:monospace;text-align:center">' +
    '<div style="font-size:20px;margin-bottom:10px">&#9888; Leaflet nicht geladen</div>' +
    '<div style="color:#8aa0b0;font-size:12px">CDN blockiert. Internetverbindung prüfen.</div></div>';
}}
// mapData Base64-kodiert – UTF-8 sicher dekodieren (Kyrillisch, CJK etc.)
let mapData = {{}};
try {{
  const _b64bytes = Uint8Array.from(atob('{map_data_b64}'), c => c.charCodeAt(0));
  mapData = JSON.parse(new TextDecoder('utf-8').decode(_b64bytes));
}} catch(e) {{
  console.error('mapData Parse-Fehler:', e);
  const d = document.getElementById('nexus-map');
  if (d) d.innerHTML = '<div style="padding:20px;font-family:monospace;color:#ff8800;font-size:11px">⚠ Kartendaten konnten nicht geladen werden.<br>Fehler: ' + e + '</div>';
}}

const map = L.map('nexus-map', {{
  zoomControl: true,
  attributionControl: false,
  maxZoom: 19,
}}).setView([mapData.center_lat || 49.0, mapData.center_lon || 31.0], mapData.zoom || 6);
window._nexusMap = map;

// InvalidateSize – mehrfach mit Verzögerung für sicheres Rendering
[50, 200, 600, 1200, 2500].forEach(function(ms) {{
  setTimeout(function() {{ map.invalidateSize(true); }}, ms);
}});
window.addEventListener('load', function() {{ map.invalidateSize(true); }});

// ── Base Layers ───────────────────────────────────────────────────────────────
// CartoDB Dark Tiles – natives Dunkelmodus-Design, kein CSS-Filter nötig
const darkLayer = L.tileLayer(
  'https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',
  {{maxZoom: 19, subdomains: 'abcd',
    attribution: '© OpenStreetMap contributors © CARTO'}}
);
// OSM als Fallback (normales helles Design)
const cartoLayer = L.tileLayer(
  'https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{maxZoom: 19, attribution: '© OpenStreetMap contributors'}}
);
const satelliteLayer = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{maxZoom: 19, attribution: '© Esri World Imagery'}}
);
const satelliteLabels = L.tileLayer(
  'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}',
  {{maxZoom: 19, opacity: 0.8}}
);
const sentinelLayer = L.tileLayer(
  'https://tiles.maps.eox.at/wmts?layer=s2cloudless-2020_3857&style=default&tilematrixset=GoogleMapsCompatible&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image%2Fjpeg&TileMatrix={{z}}&TileCol={{x}}&TileRow={{y}}',
  {{maxZoom: 19, maxNativeZoom: 14, attribution: '© EOX Sentinel-2 cloudless 2020 (EPSG:3857)'}}
);

// ── Overlay Layers ────────────────────────────────────────────────────────────
// NASA GIBS: VIIRS Thermal Anomalies (Brände/Feuer, kein Key nötig)
const firmsLayer = L.tileLayer(
  'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_NOAA20_Thermal_Anomalies_375m_All/default/default/GoogleMapsCompatible_Level8/{{z}}/{{y}}/{{x}}.png',
  {{opacity: 0.85, attribution: 'NASA FIRMS/GIBS'}}
);
let firmsVisible = false;

// Aktueller Base-Layer-Modus
let _baseMode = 'dark';
darkLayer.addTo(map);

// ── Layer-Toggle-Button ───────────────────────────────────────────────────────
const layerBtn = L.control({{position: 'topright'}});
layerBtn.onAdd = function() {{
  const div = L.DomUtil.create('div', '');
  div.innerHTML =
    '<div style="display:flex;flex-direction:column;gap:4px">' +
    '<button id="btn-dark"     onclick="setBase(&quot;dark&quot;)"     style="' + _btnStyle(true)  + '">Karte</button>' +
    '<button id="btn-sat"      onclick="setBase(&quot;sat&quot;)"      style="' + _btnStyle(false) + '">Satellit</button>' +
    '<button id="btn-sentinel" onclick="setBase(&quot;sentinel&quot;)" style="' + _btnStyle(false) + '">Sentinel-2</button>' +
    '<button id="btn-fire"     onclick="toggleFire()"                  style="' + _btnStyle(false) + '">Braende</button>' +
    '</div>';
  L.DomEvent.disableClickPropagation(div);
  return div;
}};
layerBtn.addTo(map);

function _btnStyle(active) {{
  return 'background:' + (active ? '#00d4ff22' : '#0a0e14') + ';' +
         'color:' + (active ? '#00d4ff' : '#4a6070') + ';' +
         'border:1px solid ' + (active ? '#00d4ff' : '#1e3a4a') + ';' +
         'padding:3px 8px;border-radius:3px;cursor:pointer;' +
         'font-family:monospace;font-size:10px;text-align:left;width:100%';
}}

function setBase(mode) {{
  map.removeLayer(darkLayer);
  map.removeLayer(satelliteLayer);
  map.removeLayer(satelliteLabels);
  map.removeLayer(sentinelLayer);
  _baseMode = mode;
  if (mode === 'dark') {{
    darkLayer.addTo(map);
  }} else if (mode === 'sat') {{
    satelliteLayer.addTo(map);
    satelliteLabels.addTo(map);
  }} else if (mode === 'sentinel') {{
    sentinelLayer.addTo(map);
  }}
  // Buttons aktualisieren
  ['dark','sat','sentinel'].forEach(function(m) {{
    const b = document.getElementById('btn-' + m);
    if (b) b.style.cssText = _btnStyle(m === mode);
  }});
}}

function toggleFire() {{
  firmsVisible = !firmsVisible;
  if (firmsVisible) {{
    firmsLayer.addTo(map);
  }} else {{
    map.removeLayer(firmsLayer);
  }}
  const b = document.getElementById('btn-fire');
  if (b) b.style.cssText = _btnStyle(firmsVisible);
}}

// ── Ziel-Marker (angefragter Ort) ────────────────────────────────────────────
const TARGET_LAT = mapData.center_lat;
const TARGET_LON = mapData.center_lon;
const TARGET_NAME = '{query_display}';

const targetIcon = L.divIcon({{
  className: '',
  html: '<div style="width:20px;height:20px;position:relative">' +
        '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);' +
        'width:14px;height:14px;border:2px solid #00ff88;border-radius:50%;' +
        'box-shadow:0 0 8px #00ff88"></div>' +
        '<div style="position:absolute;top:50%;left:0;right:0;height:1px;background:#00ff88;opacity:.6"></div>' +
        '<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:#00ff88;opacity:.6"></div>' +
        '</div>',
  iconSize: [20,20], iconAnchor: [10,10],
}});
const targetMarker = L.marker([TARGET_LAT, TARGET_LON], {{icon: targetIcon}}).addTo(map);
targetMarker.bindPopup(
  '<div style="font-family:monospace;min-width:160px">' +
  '<b style="color:#00ff88">🎯 Ziel: ' + TARGET_NAME + '</b><br>' +
  '<span style="color:#8aa0b0;font-size:10px">' +
  TARGET_LAT.toFixed(4) + '°, ' + TARGET_LON.toFixed(4) + '°</span></div>'
);

function goToTarget() {{
  map.setView([TARGET_LAT, TARGET_LON], mapData.zoom || 7);
  targetMarker.openPopup();
}}

function openCopernicus() {{
  // zoom.earth – kein Login, aktuelle Satellitendaten sofort sichtbar
  const c   = map.getCenter();
  const z   = Math.min(map.getZoom() + 1, 17);
  const url = zoomEarthLink(c.lat, c.lng, z);
  window.open(url, '_blank');
}}
function openGmaps() {{
  const c   = map.getCenter();
  const z   = Math.min(map.getZoom() + 4, 20);
  const url = gmapsLink(c.lat, c.lng, z);
  window.open(url, '_blank');
}}

// ── Satelliten-Link Helper ───────────────────────────────────────────────────
function copLink(lat, lon, zoom) {{
  // Copernicus Browser (Login für Vollzugriff, Vorschau ohne Login)
  zoom = zoom || 12;
  return 'https://browser.dataspace.copernicus.eu/?zoom=' + zoom +
         '&lat=' + lat.toFixed(5) + '&lng=' + lon.toFixed(5) +
         '&themeId=DEFAULT-THEME&datasetId=S2_L2A_CDAS&demSource3D=MAPZEN';
}}
function gmapsLink(lat, lon, zoom) {{
  // Google Maps Satellit – @lat,lon,Nz/data=!3m1!1e3  (direkter Satellite-Link)
  zoom = zoom || 16;
  return 'https://www.google.com/maps/@' + lat.toFixed(6) + ',' + lon.toFixed(6) + ',' + zoom + 'z/data=!3m1!1e3';
}}
function zoomEarthLink(lat, lon, zoom) {{
  // zoom.earth – kein Login, aktuelle Satellitendaten
  zoom = zoom || 15;
  return 'https://zoom.earth/#view=' + lat.toFixed(5) + ',' + lon.toFixed(5) + ',' + zoom + 'z/layers=satellite';
}}
function copBtn(lat, lon, zoom) {{
  const gLink = gmapsLink(lat, lon, zoom ? zoom + 3 : 16);
  const zLink = zoomEarthLink(lat, lon, zoom || 13);
  const cLink = copLink(lat, lon, zoom || 13);
  const btnStyle = 'display:inline-block;margin-top:6px;padding:3px 8px;font-size:10px;border-radius:3px;text-decoration:none';
  return '<div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">' +
    '<a href="' + gLink + '" target="_blank" style="' + btnStyle + ';background:#001a30;border:1px solid #34a853;color:#34a853">' +
    '🗺 Google Sat &#8599;</a>' +
    '<a href="' + zLink + '" target="_blank" style="' + btnStyle + ';background:#001a30;border:1px solid #ff8800;color:#ff8800">' +
    '🌍 Zoom.Earth &#8599;</a>' +
    '<a href="' + cLink + '" target="_blank" style="' + btnStyle + ';background:#001a30;border:1px solid #00d4ff;color:#00d4ff">' +
    '🛰 Sentinel-2 &#8599;</a>' +
  '</div>';
}}

// ── Icon-Builder ─────────────────────────────────────────────────────────────
function flightIcon(color, susp, isHeli) {{
  const glow = susp ? 'filter:drop-shadow(0 0 5px ' + color + ');' : '';
  const sym  = isHeli ? '🚁' : '✈';
  const sz   = isHeli ? '16' : (susp ? '18' : '14');
  return L.divIcon({{
    className: '',
    html: '<span style="color:' + color + ';font-size:' + sz + 'px;' + glow + '">' + sym + '</span>',
    iconSize: [22, 22], iconAnchor: [11, 11],
  }});
}}

function shipIcon(color, hasAlert) {{
  const size  = hasAlert ? '20' : '16';
  const glow  = hasAlert ? 'filter:drop-shadow(0 0 6px ' + color + ');' : '';
  return L.divIcon({{
    className: '',
    html: '<span style="color:' + color + ';font-size:' + size + 'px;' + glow + '">⚓</span>',
    iconSize: [24, 24], iconAnchor: [12, 12],
  }});
}}

// ── Flugzeuge einzeichnen ────────────────────────────────────────────────────
(mapData.aircraft || []).forEach(function(ac) {{
  if (!ac.lat || !ac.lon) return;
  const isSusp = ac.suspicious && ac.suspicious.length > 0;
  const isHeli = !!ac.helicopter;
  const noCs   = !ac.callsign || ac.callsign === '(kein)';
  const color  = isSusp ? '#ff4444' : (isHeli ? '#ff9900' : (noCs ? '#ffd700' : '#00ff88'));
  const marker = L.marker([ac.lat, ac.lon], {{icon: flightIcon(color, isSusp, isHeli)}}).addTo(map);

  // Kurs-Linie (länger bei auffälligen)
  if (ac.track !== null && ac.track !== undefined) {{
    const dist   = isSusp ? 0.6 : 0.35;
    const endLat = ac.lat + dist * Math.cos(ac.track * Math.PI / 180);
    const endLon = ac.lon + dist * Math.sin(ac.track * Math.PI / 180);
    L.polyline([[ac.lat, ac.lon], [endLat, endLon]], {{
      color: color, weight: isSusp ? 2 : 1,
      opacity: isSusp ? 0.8 : 0.4, dashArray: isSusp ? '6,3' : '3,5'
    }}).addTo(map);
  }}

  const alt    = ac.altitude_ft ? ac.altitude_ft.toLocaleString() + ' ft' : 'Boden';
  const spd    = ac.velocity_kmh ? ac.velocity_kmh + ' km/h' : '?';
  const susp   = ac.suspicious || '';
  const typeLabel = isHeli
    ? '<div style="color:#ff9900;font-size:11px;margin-bottom:3px">🚁 Hubschrauber</div>'
    : '';
  marker.bindPopup(
    '<div style="min-width:180px;font-family:monospace">' +
    typeLabel +
    '<b style="color:#00d4ff;font-size:14px">' + (ac.callsign || '(kein Callsign)') + '</b><br>' +
    '<span style="color:#8aa0b0;font-size:11px">Herkunft: ' + (ac.origin || '?') + '</span><br>' +
    (ac.registration ? '<span style="color:#8aa0b0;font-size:10px">Reg: ' + ac.registration + (ac.ac_type ? ' · Typ: ' + ac.ac_type : '') + '</span><br>' : '') +
    '<span style="color:#c8d6e0">📏 ' + alt + ' &nbsp;|&nbsp; 🚀 ' + spd + '</span><br>' +
    (susp
      ? '<div style="margin-top:5px;padding:4px;background:#2a0800;border-left:3px solid #ff4444;color:#ff8800;font-size:11px">⚠ ' + susp + '</div>'
      : '<div style="color:#00ff88;margin-top:4px;font-size:11px">✅ Keine Auffälligkeiten</div>'
    ) +
    '<div style="color:#3a5060;font-size:9px;margin-top:3px">Quelle: ' + (ac.data_source || 'OpenSky') + '</div>' +
    '</div>'
  );
}});

// ── Vorfalls-Marker aus Nachrichten ─────────────────────────────────────────
(mapData.incident_markers || []).forEach(function(inc) {{
  if (!inc.lat || !inc.lon) return;
  const icon = L.divIcon({{
    className: '',
    html: '<div style="width:14px;height:14px;background:#ff2222;border-radius:50%;border:2px solid #ff8888;box-shadow:0 0 8px #ff0000"></div>',
    iconSize: [14, 14], iconAnchor: [7, 7],
  }});
  const marker = L.marker([inc.lat, inc.lon], {{icon: icon}}).addTo(map);
  L.circle([inc.lat, inc.lon], {{
    radius: 12000, color: '#ff2222', weight: 1,
    opacity: 0.5, fillOpacity: 0.10,
  }}).addTo(map);
  marker.bindPopup(
    '<div style="min-width:220px;font-family:monospace">' +
    '<div style="color:#ff4444;font-weight:bold;margin-bottom:4px">📍 VORFALL: ' + inc.place + '</div>' +
    '<a href="' + inc.url + '" target="_blank" style="color:#ffd700;font-size:11px">' + inc.title + '</a>' +
    '<div style="color:#8aa0b0;font-size:10px;margin-top:3px">' + inc.source + '</div>' +
    copBtn(inc.lat, inc.lon, 13) +
    '</div>'
  );
}});

// ── Maritime Punkte ──────────────────────────────────────────────────────────
(mapData.maritime_points || []).forEach(function(pt) {{
  const hasAlert = pt.alerts > 0;
  const color    = hasAlert ? '#ff8800' : '#00d4ff';
  const marker   = L.marker([pt.lat, pt.lon], {{icon: shipIcon(color, hasAlert)}}).addTo(map);

  // Alarm-Kreis bei Vorfällen
  if (hasAlert) {{
    L.circle([pt.lat, pt.lon], {{
      radius: 120000, color: '#ff4444', weight: 1,
      opacity: 0.5, fillOpacity: 0.07, dashArray: '6,6'
    }}).addTo(map);
  }}

  // Alarm-Titel aufbereiten
  let alertHtml = '';
  if (hasAlert && pt.alert_titles && pt.alert_titles.length > 0) {{
    alertHtml = '<div style="margin-top:6px;border-top:1px solid #1e3a4a;padding-top:5px">' +
      '<div style="color:#ff8800;font-size:10px;margin-bottom:3px">⚠ AKTUELLE MELDUNGEN:</div>';
    pt.alert_titles.forEach(function(t) {{
      if (t) alertHtml += '<div style="color:#ffd700;font-size:10px;margin:2px 0;padding-left:6px">• ' + t + '</div>';
    }});
    alertHtml += '</div>';
  }} else if (!hasAlert) {{
    alertHtml = '<div style="color:#00ff88;font-size:11px;margin-top:5px">✅ Keine aktuellen Alarme</div>';
  }}

  marker.bindPopup(
    '<div style="min-width:220px;font-family:monospace">' +
    '<b style="color:' + color + ';font-size:13px">⚓ ' + pt.name + '</b><br>' +
    '<span style="color:#8aa0b0;font-size:10px">' + pt.desc + '</span><br>' +
    (hasAlert
      ? '<div style="background:#2a1000;border-left:3px solid #ff4444;padding:3px 6px;margin-top:4px;color:#ff8800">' +
        '⚠ <b>' + pt.alerts + ' Alarm-Meldungen</b></div>'
      : ''
    ) +
    alertHtml + '</div>'
  );
}});

// ── GDELT-Ereignisse einzeichnen ─────────────────────────────────────────────
(mapData.gdelt_points || []).forEach(function(pt) {{
  if (!pt.lat || !pt.lon) return;
  const icon = L.divIcon({{
    className: '',
    html: '<div class="gdelt-dot"></div>',
    iconSize: [10,10], iconAnchor: [5,5],
  }});
  const m = L.marker([pt.lat, pt.lon], {{icon: icon}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:180px;font-family:monospace">' +
    '<div style="color:#ffd700;font-weight:bold;margin-bottom:3px">📡 GDELT-Ereignis</div>' +
    '<div style="font-size:11px">' + (pt.name||'?') + '</div>' +
    (pt.count > 1 ? '<div style="color:#8aa0b0;font-size:10px;margin-top:2px">' + pt.count + ' Meldungen</div>' : '') +
    '</div>'
  );
}});

// ── AIS-Schiffe einzeichnen ───────────────────────────────────────────────────
(mapData.ais_vessels || []).forEach(function(v) {{
  if (!v.lat || !v.lon) return;
  const icon = L.divIcon({{
    className: '',
    html: '<span style="color:#00aaff;font-size:14px;filter:drop-shadow(0 0 4px #00aaff)">🚢</span>',
    iconSize: [18,18], iconAnchor: [9,9],
  }});
  const m = L.marker([v.lat, v.lon], {{icon: icon}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:180px;font-family:monospace">' +
    '<b style="color:#00aaff">🚢 ' + (v.name||'?') + '</b><br>' +
    '<span style="color:#8aa0b0;font-size:10px">Flagge: ' + (v.flag||'?') + ' | Typ: ' + (v.type||'?') + '</span><br>' +
    (v.speed ? '<span style="color:#c8d6e0;font-size:11px">⚡ ' + v.speed + ' kn</span>' : '') +
    '</div>'
  );
}});

// ── Erdbeben einzeichnen ─────────────────────────────────────────────────────────
(mapData.earthquakes || []).forEach(function(q) {{
  if (!q.lat || !q.lon) return;
  const mag      = q.mag || 0;
  const detConf  = q.det_confidence || '';
  const isDet    = detConf !== '';

  // Detonations-Kandidaten: eigenes Icon + Farb-Schema
  if (isDet) {{
    const confColor = {{high:'#ff0044', medium:'#ff6600', low:'#ffaa00'}}[detConf] || '#ff6600';
    const confLabel = {{high:'⚠ HOCH', medium:'~ MITTEL', low:'~ NIEDRIG'}}[detConf] || '';
    const pulseSize = 28;
    const detIcon = L.divIcon({{
      className: '',
      html: '<div style="position:relative;width:' + pulseSize + 'px;height:' + pulseSize + 'px">' +
            '<div style="position:absolute;inset:0;border-radius:50%;background:' + confColor +
            ';opacity:.25;animation:pulse 1.5s infinite"></div>' +
            '<div style="position:absolute;inset:4px;border-radius:50%;background:' + confColor +
            ';display:flex;align-items:center;justify-content:center;' +
            'font-size:13px;box-shadow:0 0 10px ' + confColor + '">💥</div>' +
            '</div>',
      iconSize: [pulseSize, pulseSize], iconAnchor: [pulseSize/2, pulseSize/2],
    }});
    const dm = L.marker([q.lat, q.lon], {{icon:detIcon, zIndexOffset:1000}}).addTo(map);
    dm.bindPopup(
      '<div style="min-width:250px;font-family:monospace">' +
      '<b style="color:' + confColor + ';font-size:13px">💥 MOEGLICHE DETONATION</b>' +
      '<div style="background:#1a0005;border-left:3px solid ' + confColor +
      ';padding:4px 7px;margin:5px 0;color:' + confColor + ';font-size:11px">' +
      '<b>Konfidenz: ' + confLabel + '</b><br>' + (q.det_hint||'') + '</div>' +
      '<div style="color:#c8d6e0;font-size:11px">' + (q.place||'?') + '</div>' +
      '<div style="color:#8aa0b0;font-size:10px;margin-top:2px">' +
      'M' + mag + (q.depth_km != null ? ' | Tiefe: ' + q.depth_km + 'km' : '') +
      ' | ' + (q.timestamp||'') + '</div>' +
      (q.conflict_zone ? '<div style="color:#ff8800;font-size:10px">Zone: ' + q.conflict_zone + '</div>' : '') +
      '<a href="' + (q.url||'#') + '" target="_blank" style="font-size:10px;color:#00d4ff">USGS &#8594;</a>' +
      copBtn(q.lat, q.lon, 12) +
      '</div>'
    );
    // Unsicherheitskreis um Detonationsort
    L.circle([q.lat,q.lon],{{
      radius: 8000, color: confColor, weight: 1.5,
      opacity: .5, fillOpacity: .08, dashArray: '4,4'
    }}).addTo(map);
    return;  // Kein normales Erdbeben-Icon zusaetzlich
  }}

  // Normale Erdbeben
  const size  = Math.max(8, Math.min(28, mag * 5));
  const color = mag >= 6.0 ? '#ff2222' : (mag >= 4.5 ? '#ff6600' : '#ff9900');
  const icon  = L.divIcon({{
    className: '',
    html: '<div style="width:' + size + 'px;height:' + size + 'px;background:' + color +
          ';border-radius:50%;border:2px solid rgba(255,255,255,.2);opacity:.85;' +
          (mag >= 4.5 ? 'box-shadow:0 0 8px ' + color + ';' : '') + '"></div>',
    iconSize: [size,size], iconAnchor: [size/2,size/2],
  }});
  const m = L.marker([q.lat, q.lon], {{icon:icon}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:220px;font-family:monospace">' +
    '<b style="color:' + color + ';font-size:14px">🌍 M' + mag + '</b>' +
    (q.osint_hint ? '<div style="background:#2a1000;border-left:3px solid #ff6600;padding:3px 6px;margin:4px 0;color:#ff8800;font-size:11px">' + q.osint_hint + '</div>' : '') +
    '<div style="color:#c8d6e0;font-size:11px">' + (q.place||'?') + '</div>' +
    '<div style="color:#8aa0b0;font-size:10px;margin-top:2px">' + (q.timestamp||'') +
    (q.depth_km != null ? ' | Tiefe: ' + q.depth_km + 'km' : '') + '</div>' +
    '<a href="' + (q.url||'#') + '" target="_blank" style="font-size:10px;color:#00d4ff">USGS Details &#8594;</a>' +
    copBtn(q.lat, q.lon, 12) +
    '</div>'
  );
  if (mag >= 3.5) {{
    L.circle([q.lat,q.lon],{{radius:mag*15000,color:color,weight:1,opacity:.3,fillOpacity:.04}}).addTo(map);
  }}
}});

// ── NOTAM-Sperrgebiete einzeichnen ────────────────────────────────────────────
(mapData.notams || []).forEach(function(n) {{
  if (!n.lat || !n.lon) return;
  const isAlert = n.osint && (n.osint.includes('Sperrgebiet') || n.osint.includes('Milit'));
  const color   = isAlert ? '#ff9900' : '#ffcc44';
  L.circle([n.lat, n.lon], {{
    radius: 80000, color: color, weight: 1,
    opacity: .6, fillOpacity: .07, dashArray: '8,5'
  }}).addTo(map);
  const icon = L.divIcon({{
    className: '',
    html: '<span style="color:' + color + ';font-size:14px">&#9635;</span>',
    iconSize:[16,16], iconAnchor:[8,8],
  }});
  const m = L.marker([n.lat, n.lon], {{icon:icon}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:200px;font-family:monospace">' +
    '<b style="color:' + color + '">&#9635; NOTAM ' + (n.icao||'') + '</b>' +
    (n.osint ? '<div style="color:#ff8800;font-size:11px;margin-top:3px">' + n.osint + '</div>' : '') +
    '<div style="color:#8aa0b0;font-size:10px;margin-top:4px">' + (n.text||'').substring(0,150) + '</div>' +
    '</div>'
  );
}});

// ── Korrelierte Ereignisse (Multi-Quellen-Cluster) ────────────────────────────
(mapData.correlations || []).forEach(function(c) {{
  if (!c.lat || !c.lon) return;
  const isHigh = c.confidence === 'HOCH';
  const isMid  = c.confidence === 'MITTEL';
  const color  = isHigh ? '#ff00ff' : (isMid ? '#ff8800' : '#ffd700');
  const size   = isHigh ? 20 : (isMid ? 16 : 13);
  const glow   = isHigh ? 'filter:drop-shadow(0 0 8px #ff00ff)' : '';

  const icon = L.divIcon({{
    className: '',
    html: '<div style="width:' + size + 'px;height:' + size + 'px;' +
          'background:' + color + '22;border:2px solid ' + color + ';' +
          'border-radius:50%;' + glow + ';' +
          'display:flex;align-items:center;justify-content:center;' +
          'font-size:' + Math.round(size*0.65) + 'px">⚡</div>',
    iconSize: [size, size], iconAnchor: [size/2, size/2],
  }});

  // Pulsierender Ring bei hoher Konfidenz
  if (isHigh) {{
    L.circle([c.lat, c.lon], {{
      radius: 40000, color: color, weight: 2,
      opacity: 0.6, fillOpacity: 0.04, dashArray: '4,4'
    }}).addTo(map);
  }}

  const marker = L.marker([c.lat, c.lon], {{icon: icon, zIndexOffset: 500}}).addTo(map);
  marker.bindPopup(
    '<div style="min-width:240px;font-family:monospace">' +
    '<div style="color:' + color + ';font-weight:bold;font-size:13px">⚡ KORRELATION [' + c.confidence + ']</div>' +
    '<div style="color:#c8d6e0;font-size:11px;margin:4px 0">' + (c.topic_str||'').toUpperCase() + '</div>' +
    '<div style="color:#8aa0b0;font-size:10px;margin-bottom:6px">' + c.n_sources + ' unabhängige Quellen · ' + (c.source_types||[]).join(', ') + '</div>' +
    '<div style="border-left:2px solid ' + color + ';padding-left:8px;font-size:10px;color:#c8d6e0">' +
    (c.newest_title||'').substring(0,100) + '</div>' +
    copBtn(c.lat, c.lon, 12) +
    '</div>'
  );
}});

// ── NASA FIRMS Brandpunkte (Echtzeit-Satellitendaten) ─────────────────────────
(mapData.fires || []).forEach(function(f) {{
  if (!f.lat || !f.lon) return;
  const frp    = f.frp || 0;
  const size   = frp > 500 ? 18 : (frp > 100 ? 14 : 10);
  const color  = frp > 500 ? '#ff2200' : (frp > 100 ? '#ff5500' : '#ff8800');
  const icon   = L.divIcon({{
    className: '',
    html: '<div style="font-size:' + size + 'px;filter:drop-shadow(0 0 4px ' + color + ')">🔥</div>',
    iconSize: [size, size], iconAnchor: [size/2, size/2],
  }});
  const marker = L.marker([f.lat, f.lon], {{icon: icon}}).addTo(map);
  const age_s  = f.age_min < 120 ? f.age_min + 'min' : Math.round(f.age_min/60) + 'h';
  const frpClass = frp > 500 ? '🔴 EXTREM' : (frp > 100 ? '🟠 STARK' : '🟡 MITTEL');
  marker.bindPopup(
    '<div style="font-family:monospace;min-width:240px">' +
    '<b style="color:' + color + '">🔥 NASA FIRMS Branderkennung</b><br>' +
    '<span style="color:#c8d6e0;font-size:11px">Intensität: ' + frpClass + ' | FRP: ' + frp.toFixed(0) + ' MW</span><br>' +
    '<span style="color:#c8d6e0;font-size:11px">Erkannt vor: ' + age_s + '</span><br>' +
    '<div style="color:#00d4ff;font-size:10px;margin-top:4px;font-weight:bold">' +
      '📍 GPS: ' + f.lat.toFixed(5) + ', ' + f.lon.toFixed(5) + '</div>' +
    (f.osint ? '<div style="color:#ff8800;font-size:10px;margin-top:3px">' + f.osint + '</div>' : '') +
    '<div style="color:#4a6070;font-size:9px;margin-top:3px">Quelle: NASA VIIRS/MODIS Satellit</div>' +
    copBtn(f.lat, f.lon, 13) +
    '</div>'
  );
}});

// ── ACLED Konfliktereignisse (GPS-verifiziert) ────────────────────────────────
(mapData.acled_points || []).forEach(function(ac) {{
  if (!ac.lat || !ac.lon) return;
  const color = ac.color || '#ff6600';
  const icon  = L.divIcon({{
    className: '',
    html: '<div style="font-size:16px;filter:drop-shadow(0 0 5px ' + color + ');cursor:pointer">' + (ac.icon || '⚡') + '</div>',
    iconSize: [20, 20], iconAnchor: [10, 10],
  }});
  const marker = L.marker([ac.lat, ac.lon], {{icon: icon, zIndexOffset: 500}}).addTo(map);
  const fatStr = ac.fatalities > 0
    ? '<div style="color:#ff4444;font-weight:bold;margin-top:3px">☠ ' + ac.fatalities + ' Todesopfer</div>' : '';
  const actorStr = ac.actor1
    ? '<div style="color:#c8d6e0;font-size:10px;margin-top:2px">Akteur: ' + ac.actor1 + '</div>' : '';
  const notesStr = ac.notes
    ? '<div style="color:#8fa0b0;font-size:10px;margin-top:3px;font-style:italic">' + ac.notes + '</div>' : '';
  marker.bindPopup(
    '<div style="font-family:monospace;min-width:260px">' +
    '<b style="color:' + color + '">' + (ac.icon||'⚡') + ' ' + (ac.event_type||'Konfliktereignis') + '</b><br>' +
    '<span style="color:#fff;font-size:11px">' + (ac.location||'') + '</span><br>' +
    '<span style="color:#c8d6e0;font-size:10px">' + (ac.date||'') + '</span>' +
    fatStr + actorStr + notesStr +
    '<div style="color:#00d4ff;font-size:10px;margin-top:4px;font-weight:bold">' +
      '📍 GPS: ' + ac.lat.toFixed(5) + ', ' + ac.lon.toFixed(5) + '</div>' +
    '<div style="color:#4a6070;font-size:9px;margin-top:2px">Quelle: ACLED — GPS-verifiziert, kuratiert</div>' +
    copBtn(ac.lat, ac.lon, 13) +
    '</div>'
  );
}});

// ── NASA EONET Naturereignisse ────────────────────────────────────────────────
(mapData.eonet_points || []).forEach(function(ev) {{
  if (!ev.lat || !ev.lon) return;
  const isConflict = ev.conflict_zone && ev.conflict_zone.length > 0;
  const color      = ev.color || '#888888';
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative">' +
          '<span style="font-size:' + (isConflict ? '18' : '14') + 'px;' +
          'filter:' + (isConflict ? 'drop-shadow(0 0 5px ' + color + ')' : 'none') + '">' +
          (ev.icon || '📍') + '</span>' +
          (isConflict ? '<span style="position:absolute;top:-4px;right:-10px;background:#cc0000;color:#fff;font-size:7px;font-weight:bold;border-radius:2px;padding:0 2px">!</span>' : '') +
          '</div>',
    iconSize: [22, 22], iconAnchor: [11, 11],
  }});
  const m = L.marker([ev.lat, ev.lon], {{icon: icon}}).addTo(map);

  if (isConflict) {{
    L.circle([ev.lat, ev.lon], {{
      radius: 30000, color: color, weight: 1,
      opacity: 0.4, fillOpacity: 0.06, dashArray: '6,8',
    }}).addTo(map);
  }}

  const conflictBadge = isConflict
    ? '<div style="margin-top:5px;padding:4px;background:#1a0800;border-left:3px solid #ff8800;color:#ff8800;font-size:10px">⚠ KONFLIKTZONE: ' + ev.conflict_zone + '</div>'
    : '';
  const osintDiv = ev.osint
    ? '<div style="margin-top:4px;padding:3px 6px;background:#0a1218;border-left:2px solid ' + color + ';color:#c8d6e0;font-size:10px">' + ev.osint + '</div>'
    : '';

  m.bindPopup(
    '<div style="min-width:200px;font-family:monospace">' +
    '<b style="color:' + color + ';font-size:12px">' + (ev.icon||'') + ' ' + ev.title + '</b><br>' +
    '<span style="color:#8aa0b0;font-size:10px">NASA EONET | ' + (ev.date||'') + '</span>' +
    conflictBadge + osintDiv +
    (ev.url ? '<br><a href="' + ev.url + '" target="_blank" style="color:#00d4ff;font-size:10px">→ NASA Quelle</a>' : '') +
    copBtn(ev.lat, ev.lon, 9) +
    '</div>'
  );
}});

// ── Artillerie-Signal-Marker (Lightning/Blitzortung) ─────────────────────────
(mapData.lightning_signals || []).forEach(function(sig) {{
  if (!sig.lat || !sig.lon) return;
  const confColor = {{high:'#ff0066', medium:'#ff8800', low:'#ffdd00'}}[sig.confidence] || '#ff8800';
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:30px;height:30px">' +
          '<div style="position:absolute;inset:0;border-radius:50%;background:' + confColor +
          ';opacity:.15;animation:pulse 2s infinite"></div>' +
          '<div style="position:absolute;inset:5px;display:flex;align-items:center;justify-content:center;' +
          'font-size:16px;filter:drop-shadow(0 0 6px ' + confColor + ')">⚡</div>' +
          '</div>',
    iconSize: [30, 30], iconAnchor: [15, 15],
  }});
  const sm = L.marker([sig.lat, sig.lon], {{icon: icon, zIndexOffset: 900}}).addTo(map);
  sm.bindPopup(
    '<div style="min-width:240px;font-family:monospace">' +
    '<b style="color:' + confColor + ';font-size:13px">⚡ ARTILLERIE-SIGNAL</b>' +
    '<div style="background:#1a0800;border-left:3px solid ' + confColor +
    ';padding:4px 7px;margin:5px 0;color:' + confColor + ';font-size:11px">' +
    '<b>Konfidenz: ' + (sig.confidence||'?').toUpperCase() + '</b>' +
    (sig.count > 0 ? ' | ' + sig.count + ' Signale/60min' : '') + '</div>' +
    '<div style="color:#c8d6e0;font-size:11px">' + (sig.hint||'Keine Details') + '</div>' +
    (sig.region ? '<div style="color:#8aa0b0;font-size:10px;margin-top:3px">Zone: ' + sig.region + '</div>' : '') +
    copBtn(sig.lat, sig.lon, 9) +
    '</div>'
  );
  L.circle([sig.lat, sig.lon], {{
    radius: 40000, color: confColor, weight: 1.5,
    opacity: .4, fillOpacity: .05, dashArray: '5,7'
  }}).addTo(map);
}});

// ── GPS-Jamming Zonen ────────────────────────────────────────────────────────
(mapData.gpsjam_zones || []).forEach(function(z) {{
  if (!z.lat || !z.lon) return;
  const intColor = {{HOCH:'#ff2255', MITTEL:'#ff8800', NIEDRIG:'#ffdd00'}}[z.intensity] || '#ffdd00';
  const intAlpha = {{HOCH:0.18,      MITTEL:0.12,      NIEDRIG:0.07}}[z.intensity] || 0.08;

  // Bbox-Rechteck als halbtransparenter Overlay
  if (z.lat_min && z.lon_min && z.lat_max && z.lon_max) {{
    L.rectangle([[z.lat_min, z.lon_min],[z.lat_max, z.lon_max]], {{
      color: intColor, weight: 1.5, opacity: 0.5,
      fillColor: intColor, fillOpacity: intAlpha,
      dashArray: '6,5',
    }}).bindTooltip(
      '⚡ GPS-JAMMING: ' + (z.zone||'?') + ' [' + (z.intensity||'?') + ']',
      {{permanent: false, direction: 'center',
        className: 'jam-tooltip'}}
    ).addTo(map);
  }}

  // Zentrum-Marker mit Popup
  const jIcon = L.divIcon({{
    className: '',
    html: '<div style="background:' + intColor +
          ';border-radius:3px;padding:1px 4px;font-size:9px;font-weight:bold;' +
          'color:#000;opacity:.85;white-space:nowrap">⚡GPS-JAM</div>',
    iconSize: [60, 16], iconAnchor: [30, 8],
  }});
  const jm = L.marker([z.lat, z.lon], {{icon: jIcon, zIndexOffset: 100}}).addTo(map);
  jm.bindPopup(
    '<div style="min-width:220px;font-family:monospace">' +
    '<b style="color:' + intColor + ';font-size:13px">⚡ GPS-JAMMING</b>' +
    '<div style="background:#1a0010;border-left:3px solid ' + intColor +
    ';padding:4px 7px;margin:5px 0;color:' + intColor + ';font-size:11px">' +
    '<b>' + (z.zone||'?') + '</b> | Intensitaet: ' + (z.intensity||'?') + '</div>' +
    '<div style="color:#c8d6e0;font-size:10px">' + (z.source||'') + '</div>' +
    '<div style="color:#8aa0b0;font-size:10px;margin-top:3px">' +
    'GPS-Daten in dieser Zone koennen verfaelscht sein. ' +
    'ADS-B-Positionen mit Vorsicht interpretieren.</div>' +
    '</div>'
  );
}});

// ── Tiefgang-Delta-Marker (AIS Ladungsveraenderung) ──────────────────────────
(mapData.draught_alerts || []).forEach(function(da) {{
  if (!da.lat || !da.lon) return;
  const confColor = {{high:'#ff44aa', medium:'#cc66ff', low:'#8888ff'}}[da.confidence] || '#cc66ff';
  const evtIcon   = {{STS:'🚢', ENTLADUNG:'📦', BELADUNG:'⬆', BUNKER_VERDACHT:'⛽'}}[da.event_type] || '⚓';
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:26px;height:26px">' +
          '<div style="position:absolute;inset:0;border-radius:50%;background:' + confColor +
          ';opacity:.2;animation:pulse 2.5s infinite"></div>' +
          '<div style="position:absolute;inset:3px;border-radius:50%;background:' + confColor +
          ';opacity:.5;display:flex;align-items:center;justify-content:center;font-size:12px">' +
          evtIcon + '</div>' +
          '</div>',
    iconSize: [26, 26], iconAnchor: [13, 13],
  }});
  const dm = L.marker([da.lat, da.lon], {{icon: icon, zIndexOffset: 800}}).addTo(map);
  dm.bindPopup(
    '<div style="min-width:240px;font-family:monospace">' +
    '<b style="color:' + confColor + ';font-size:13px">' + evtIcon + ' TIEFGANG-DELTA</b>' +
    ' <span style="color:#8aa0b0;font-size:10px">[' + (da.event_type||'?') + ']</span>' +
    '<div style="background:#0a001a;border-left:3px solid ' + confColor +
    ';padding:4px 7px;margin:5px 0;color:' + confColor + ';font-size:11px">' +
    '<b>Konfidenz: ' + (da.confidence||'?').toUpperCase() + '</b>' +
    ' | Delta: <b>' + (da.delta_m > 0 ? '+' : '') + (da.delta_m||0).toFixed(1) + 'm</b></div>' +
    '<div style="color:#c8d6e0;font-size:11px">' + (da.name||'Unbekannt') +
    (da.mmsi ? ' <span style="color:#4a6070">(MMSI: ' + da.mmsi + ')</span>' : '') + '</div>' +
    '<div style="color:#8aa0b0;font-size:10px;margin-top:3px">' + (da.hint||'') + '</div>' +
    '<div style="color:#4a6070;font-size:10px">' + (da.timestamp||'') + '</div>' +
    copBtn(da.lat, da.lon, 9) +
    '</div>'
  );
}});

// ── STS-Transfer Alerts ──────────────────────────────────────────────────────
(mapData.sts_alerts || []).forEach(function(a) {{
  if (!a.lat || !a.lon) return;
  const col = a.severity === 'critical' ? '#ff2255' : '#ff8800';
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:30px;height:30px">' +
          '<div style="position:absolute;inset:0;border-radius:50%;background:' + col +
          ';opacity:.25;animation:pulse 1.8s infinite"></div>' +
          '<div style="position:absolute;inset:3px;border-radius:50%;background:' + col +
          ';opacity:.6;display:flex;align-items:center;justify-content:center;font-size:14px">⛽</div>' +
          '</div>',
    iconSize: [30, 30], iconAnchor: [15, 15],
  }});
  const m = L.marker([a.lat, a.lon], {{icon: icon, zIndexOffset: 900}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:260px;font-family:monospace">' +
    '<b style="color:' + col + ';font-size:13px">' + (a.title||'STS-Alert') + '</b>' +
    '<div style="background:#1a0010;border-left:3px solid ' + col +
    ';padding:5px 8px;margin:5px 0;font-size:11px;color:#f0c0d0">' + (a.summary||'') + '</div>' +
    '<div style="font-size:10px;color:#8aa0b0">' +
    '🚢 ' + (a.ship_a&&a.ship_a.name||'?') + ' ' + (a.ship_a&&a.ship_a.flag||'') +
    ' &nbsp;↔&nbsp; ' + (a.ship_b&&a.ship_b.name||'?') + ' ' + (a.ship_b&&a.ship_b.flag||'') +
    '</div>' +
    '<div style="color:#4a6070;font-size:10px;margin-top:3px">Abstand: ' + (a.distance_m||'?') + 'm</div>' +
    copBtn(a.lat, a.lon, 9) +
    '</div>'
  );
}});

// ── Unexpected Stop / Dark Rendezvous Alerts ─────────────────────────────────
(mapData.stop_alerts || []).forEach(function(a) {{
  if (!a.lat || !a.lon) return;
  const col = a.severity === 'critical' ? '#cc00ff' : '#ff8800';
  const ico = a.in_chokepoint ? '🚧' : '🔴';
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:28px;height:28px">' +
          '<div style="position:absolute;inset:0;border-radius:4px;background:' + col +
          ';opacity:.2;animation:pulse 2s infinite"></div>' +
          '<div style="position:absolute;inset:3px;border-radius:3px;background:' + col +
          ';opacity:.5;display:flex;align-items:center;justify-content:center;font-size:13px">' + ico + '</div>' +
          '</div>',
    iconSize: [28, 28], iconAnchor: [14, 14],
  }});
  const m = L.marker([a.lat, a.lon], {{icon: icon, zIndexOffset: 850}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:240px;font-family:monospace">' +
    '<b style="color:' + col + ';font-size:13px">' + (a.title||'Stop-Alert') + '</b>' +
    '<div style="background:#0d001a;border-left:3px solid ' + col +
    ';padding:5px 8px;margin:5px 0;font-size:11px;color:#e0c0f0">' + (a.summary||'') + '</div>' +
    '<div style="font-size:10px;color:#8aa0b0">' + (a.type||'🚢') +
    ' | SOG: ' + (a.speed_kn||0).toFixed(1) + ' kn | MMSI: ' + (a.mmsi||'?') + '</div>' +
    copBtn(a.lat, a.lon, 9) +
    '</div>'
  );
}});

// ── HUMINT Taktische Meldungen ───────────────────────────────────────────────
(mapData.humint_markers || []).forEach(function(h) {{
  if (!h.lat || !h.lon) return;
  const col = h.color || '#ff8800';
  const ico = h.icon || '📍';
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:28px;height:28px">' +
          '<div style="position:absolute;inset:0;border-radius:3px;background:' + col +
          ';opacity:.15;animation:pulse 2s infinite"></div>' +
          '<div style="position:absolute;inset:2px;border-radius:3px;background:' + col +
          ';opacity:.7;display:flex;align-items:center;justify-content:center;font-size:14px">' +
          ico + '</div></div>',
    iconSize: [28,28], iconAnchor: [14,14],
  }});
  const hm = L.marker([h.lat, h.lon], {{icon:icon, zIndexOffset:900}}).addTo(map);
  const confPct = Math.round((h.confidence||0)*100);
  const unitsHtml = (h.units||[]).length ?
    '<div style="color:#ffcc00;font-size:10px">Einheit: ' + h.units.slice(0,2).join(' · ') + '</div>' : '';
  hm.bindPopup(
    '<div style="min-width:240px;font-family:monospace">' +
    '<b style="color:' + col + ';font-size:13px">' + ico + ' HUMINT – ' + (h.title||'Kontaktmeldung') + '</b>' +
    '<div style="background:#0a001a;border-left:3px solid ' + col +
    ';padding:4px 7px;margin:5px 0;font-size:11px;color:' + col + '">' +
    'Konfidenz: <b>' + confPct + '%</b>' +
    (h.weapon_cat ? ' | Waffe: <b>' + h.weapon_cat.toUpperCase() + '</b>' : '') +
    (h.contact ? ' | <b>⚡ KONTAKT</b>' : '') + '</div>' +
    unitsHtml +
    '<div style="color:#c8d6e0;font-size:10px;margin-top:3px;max-height:60px;overflow:hidden">' +
    (h.text||'').replace(/</g,'&lt;').substring(0,160) + '</div>' +
    '<div style="color:#4a6070;font-size:9px;margin-top:2px">Quelle: ' + (h.source||'telegram') +
    ' · Typ: ' + (h.coord_type||'?') + '</div>' +
    copBtn(h.lat, h.lon, 14) +
    '</div>'
  );
}});

// ── Fusion Threat Assessments ─────────────────────────────────────────────────
(mapData.fusion_threats || []).forEach(function(ft) {{
  if (!ft.lat || !ft.lon) return;
  const col = ft.color || '#ff0044';
  const confPct = Math.round((ft.confidence||0)*100);
  const icon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:36px;height:36px">' +
          '<div style="position:absolute;inset:0;border-radius:50%;background:' + col +
          ';opacity:.1;animation:pulse 1.8s infinite"></div>' +
          '<div style="position:absolute;inset:0;border-radius:50%;border:2px solid ' + col +
          ';opacity:.6;animation:pulse 1.8s infinite 0.9s"></div>' +
          '<div style="position:absolute;inset:6px;border-radius:50%;background:' + col +
          ';opacity:.8;display:flex;align-items:center;justify-content:center;font-size:13px">' +
          (ft.icon||'⚠') + '</div></div>',
    iconSize: [36,36], iconAnchor: [18,18],
  }});
  const fm = L.marker([ft.lat, ft.lon], {{icon:icon, zIndexOffset:1000}}).addTo(map);
  const sigHtml = (ft.signals||[]).map(function(s) {{
    return '<span style="background:#1a2a3a;border:1px solid ' + col + ';border-radius:3px;' +
           'padding:1px 5px;font-size:9px;color:' + col + '">' + s + '</span>';
  }}).join(' ');
  fm.bindPopup(
    '<div style="min-width:260px;font-family:monospace">' +
    '<b style="color:' + col + ';font-size:14px">' + (ft.title||ft.pattern||'FUSION ALERT') + '</b>' +
    '<div style="background:#0a001a;border-left:3px solid ' + col +
    ';padding:5px 8px;margin:6px 0;font-size:11px">' +
    '<span style="color:' + col + '">Konfidenz: <b>' + confPct + '%</b></span>' +
    ' | Schwere: <b style="color:' + col + '">' + (ft.severity||'?') + '</b>' +
    ' | <b>' + (ft.signal_count||0) + ' Signale</b></div>' +
    '<div style="margin:4px 0">' + sigHtml + '</div>' +
    '<div style="color:#c8d6e0;font-size:10px;margin-top:4px;line-height:1.5">' +
    (ft.text||ft.description||'').substring(0,200) + '</div>' +
    copBtn(ft.lat, ft.lon, 11) +
    '</div>'
  );
  // Bedrohungs-Radius
  L.circle([ft.lat, ft.lon], {{
    radius: 20000, color: col, weight: 1,
    opacity: 0.4, fillOpacity: 0.04, dashArray: '4,4'
  }}).addTo(map);
}});

// ── Frontlinien-Layer (DeepStateMap / ISW) ────────────────────────────────────
(function() {{
  var fl = mapData.frontline_geojson;
  if (!fl || !fl.features || !fl.features.length) return;
  var src = fl._nexus_source || 'OSINT';
  var dt  = (fl._nexus_fetched || '').substring(0, 10);
  L.geoJSON(fl, {{
    style: function(feature) {{
      return {{
        color:     '#ff3333',
        weight:    3,
        opacity:   0.90,
        dashArray: '10, 5',
        lineCap:   'round',
      }};
    }},
    onEachFeature: function(feature, layer) {{
      var p    = feature.properties || {{}};
      var fsrc = p.source || src;
      var fdt  = p.date   || dt;
      layer.bindPopup(
        '<div style="font-family:monospace;min-width:180px">' +
        '<b style="color:#ff3333">⚔ Frontlinie</b>' +
        '<div style="color:#8aa0b0;font-size:10px;margin-top:4px">Quelle: ' + fsrc + '</div>' +
        '<div style="color:#8aa0b0;font-size:10px">Stand: ' + fdt + '</div>' +
        '</div>'
      );
    }}
  }}).addTo(map);

  // Legende unten rechts
  var frontLegend = L.control({{position: 'bottomright'}});
  frontLegend.onAdd = function() {{
    var d = L.DomUtil.create('div');
    d.style.cssText = 'background:rgba(10,15,20,.85);padding:5px 10px;' +
                      'border-radius:4px;color:#fff;font-size:11px;' +
                      'font-family:monospace;border:1px solid #ff3333;' +
                      'pointer-events:none;margin-bottom:4px';
    d.innerHTML = '<span style="display:inline-block;width:22px;' +
                  'border-bottom:2px dashed #ff3333;vertical-align:middle;' +
                  'margin-right:5px"></span>Frontlinie (' + src + ')';
    return d;
  }};
  frontLegend.addTo(map);
}})();

// ── Wissens-Graph Layer (aktive Einheiten + Hotspots) ─────────────────────────
(function() {{
  var kg = mapData.knowledge_map;
  if (!kg) return;

  // Hotspot-Marker (bekannte Orte mit Aktivitaet)
  var hsMarkers = (kg.hotspots || []).filter(function(h) {{
    return h.lat || false;
  }});
  // Aktive Einheiten mit letzter GPS-Position (aus Beobachtungen)
  // Einheiten ohne GPS werden nur im Panel gezeigt

  // Hotspot-Kreise für bekannte Konflikt-Orte (approximative Positionen)
  var knownPositions = {{
    "Avdiivka":    [48.140, 37.749],
    "Bakhmut":     [48.596, 37.998],
    "Chasiv Yar":  [48.576, 37.861],
    "Pokrovsk":    [48.281, 37.179],
    "Selydove":    [48.153, 37.310],
    "Kurakhove":   [47.996, 37.274],
    "Robotyne":    [47.454, 34.899],
    "Toretsk":     [48.413, 37.851],
    "Verbove":     [47.384, 35.055],
    "Orikhiv":     [47.570, 35.786],
    "Zaporizhzhia":[47.839, 35.143],
    "Kherson":     [46.636, 32.616],
    "Kharkiv":     [49.992, 36.230],
    "Kreminna":    [49.056, 38.218],
    "Kupyansk":    [49.713, 37.607],
    "Lyman":       [48.997, 37.812],
    "Vuhledar":    [47.771, 37.245],
    "Marinka":     [47.981, 37.514],
    "Melitopol":   [46.849, 35.362],
    "Berdiansk":   [46.755, 36.812],
  }};

  var kgLayer = L.layerGroup();
  (kg.hotspots || []).forEach(function(h) {{
    var pos = knownPositions[h.location];
    if (!pos) return;
    var r = Math.min(8000 + h.obs_count * 2000, 25000);
    var circle = L.circle(pos, {{
      radius: r,
      color: '#ffaa00',
      weight: 1.5,
      opacity: 0.7,
      fillColor: '#ffaa00',
      fillOpacity: 0.08,
    }});
    circle.bindPopup(
      '<div style="font-family:monospace;font-size:11px">' +
      '<b style="color:#ffaa00">🔥 Hotspot: ' + h.location + '</b><br>' +
      'Aktivitaet: ' + h.obs_count + 'x<br>' +
      'Letzte Meldung: ' + h.last_seen + '<br>' +
      '<span style="color:#8aa0b0;font-size:10px">' + (h.sources || '').substring(0,60) + '</span>' +
      '</div>'
    );
    kgLayer.addLayer(circle);
  }});

  // Einheiten-Bewegungspfeile
  (kg.unit_movements || []).forEach(function(um) {{
    var mvs = um.movements || [];
    if (mvs.length < 1) return;
    mvs.forEach(function(m) {{
      var fromPos = knownPositions[m.from];
      var toPos   = knownPositions[m.to];
      if (!fromPos || !toPos) return;
      var arrow = L.polyline([fromPos, toPos], {{
        color: '#00ccff',
        weight: 2,
        opacity: 0.7,
        dashArray: '5, 3',
      }});
      arrow.bindPopup(
        '<div style="font-family:monospace;font-size:11px">' +
        '<b style="color:#00ccff">→ Einheit: ' + um.unit + '</b><br>' +
        m.from + ' → ' + m.to + '<br>' +
        'Richtung: ' + m.direction + ' | ' + m.distance_km + ' km<br>' +
        '<span style="color:#8aa0b0">' + m.when + '</span>' +
        '</div>'
      );
      kgLayer.addLayer(arrow);
    }});
  }});

  kgLayer.addTo(map);
}})();

// ── Benutzer-Standort ─────────────────────────────────────────────────────────
let _userMarker = null, _userCircle = null;
function locateMe() {{
  if (!navigator.geolocation) {{
    setLiveStatus(false, '📍 Geolocation nicht verfügbar');
    return;
  }}
  navigator.geolocation.getCurrentPosition(function(pos) {{
    const lat = pos.coords.latitude;
    const lon = pos.coords.longitude;
    const acc = pos.coords.accuracy;
    if (_userMarker) {{ try {{ map.removeLayer(_userMarker); map.removeLayer(_userCircle); }} catch(e){{}} }}
    const icon = L.divIcon({{
      className: '',
      html: '<div class="user-dot"></div>',
      iconSize: [14,14], iconAnchor: [7,7],
    }});
    _userMarker = L.marker([lat, lon], {{icon: icon}}).addTo(map);
    _userMarker.bindPopup(
      '<b style="color:#4488ff">📍 Mein Standort</b><br>' +
      '<span style="font-size:10px;color:#8aa0b0">±' + Math.round(acc) + 'm | ' +
      lat.toFixed(5) + ', ' + lon.toFixed(5) + '</span>'
    ).openPopup();
    _userCircle = L.circle([lat, lon], {{
      radius: acc, color: '#4488ff', weight: 1, opacity: .4, fillOpacity: .05
    }}).addTo(map);
    map.setView([lat, lon], 12);
  }}, function(err) {{
    setLiveStatus(false, '📍 Standort: Zugriff verweigert (Browser-Einstellung)');
  }}, {{enableHighAccuracy: true, timeout: 10000}});
}}

// ── EREIGNIS-TIMELINE ────────────────────────────────────────────────────────
(function buildTimeline() {{
  const tl = document.getElementById('timeline');
  if (!tl) return;

  // Alle Ereignisse sammeln (Artikel + Vorfälle + Erdbeben + Brände)
  const events = [];
  const now = Date.now();

  (mapData.incident_markers || []).forEach(function(e) {{
    events.push({{
      label: e.title || e.place,
      type: 'vorfall', color: '#ff2222',
      age_min: 0, url: e.url || '#',
    }});
  }});

  (mapData.fires || []).forEach(function(f) {{
    events.push({{
      label: 'Brand FRP:' + (f.frp||0).toFixed(0) + 'MW',
      type: 'feuer', color: '#ff6600',
      age_min: f.age_min || 0, url: '#',
    }});
  }});

  (mapData.earthquakes || []).forEach(function(q) {{
    events.push({{
      label: 'M' + q.mag + ' ' + (q.place||'').substring(0,30),
      type: 'erdbeben', color: '#ff9900',
      age_min: 0, url: q.url || '#',
    }});
  }});

  const tlArticles = {known_titles_json};
  tlArticles.forEach(function(t) {{
    events.push({{
      label: (t||'').substring(0,60),
      type: 'news', color: '#00d4ff',
      age_min: 0, url: '#',
    }});
  }});

  if (!events.length) {{
    tl.innerHTML = '<div style="color:var(--muted);padding:10px;font-size:11px">Keine Zeitdaten verfügbar</div>';
    return;
  }}

  // Timeline-Linie
  const W = Math.max(tl.parentElement.clientWidth - 40, 800);
  const MAX_MIN = 1440; // 24h
  tl.style.width = W + 'px';

  // Hintergrundlinie
  const line = document.createElement('div');
  line.style.cssText = 'position:absolute;top:40px;left:20px;right:20px;height:2px;background:var(--border)';
  tl.appendChild(line);

  // Zeitmarken (0h, 6h, 12h, 18h, 24h)
  [0,6,12,18,24].forEach(function(h) {{
    const pct = h / 24;
    const x = 20 + pct * (W - 40);
    const tick = document.createElement('div');
    tick.style.cssText = 'position:absolute;top:32px;width:1px;height:18px;background:var(--border);left:' + x + 'px';
    tl.appendChild(tick);
    const lbl = document.createElement('div');
    lbl.style.cssText = 'position:absolute;top:52px;font-size:9px;color:var(--muted);transform:translateX(-50%);left:' + x + 'px';
    lbl.textContent = h === 0 ? 'jetzt' : '-' + h + 'h';
    tl.appendChild(lbl);
  }});

  // Events einzeichnen (gestaffelt in 2 Reihen)
  events.slice(0, 30).forEach(function(ev, i) {{
    const age = Math.min(ev.age_min || (i * 45), MAX_MIN);
    const pct = age / MAX_MIN;
    const x = 20 + (1 - pct) * (W - 40);
    const row = i % 2;
    const y = row === 0 ? 8 : 22;

    const dot = document.createElement('a');
    dot.href = ev.url;
    dot.target = '_blank';
    dot.title = ev.label;
    dot.style.cssText = 'position:absolute;width:10px;height:10px;border-radius:50%;' +
      'background:' + ev.color + ';border:1px solid rgba(255,255,255,.3);' +
      'box-shadow:0 0 4px ' + ev.color + ';cursor:pointer;' +
      'left:' + (x - 5) + 'px;top:' + y + 'px;text-decoration:none';
    dot.addEventListener('mouseover', function(e2) {{
      showTip(e2, ev.label, ev.type, ev.color);
    }});
    dot.addEventListener('mouseout', hideTip);
    tl.appendChild(dot);
  }});

  // Legende
  const leg = document.createElement('div');
  leg.style.cssText = 'position:absolute;top:68px;right:10px;display:flex;gap:10px;font-size:9px';
  [['#ff2222','Vorfall'],['#ff6600','Brand'],['#ff9900','Erdbeben'],['#00d4ff','News']].forEach(function(pair) {{
    const s = document.createElement('span');
    s.innerHTML = '<span style="color:' + pair[0] + '">■</span> ' + pair[1];
    leg.appendChild(s);
  }});
  tl.appendChild(leg);
  tl.style.height = '90px';
}})();

// Tooltip für Timeline
var _tip = null;
function showTip(e, label, type, color) {{
  hideTip();
  _tip = document.createElement('div');
  _tip.style.cssText = 'position:fixed;background:#0a1620;border:1px solid ' + color +
    ';color:#c8d6e0;padding:5px 8px;border-radius:4px;font-size:10px;font-family:monospace;' +
    'z-index:9999;max-width:280px;pointer-events:none;white-space:nowrap';
  _tip.textContent = '[' + type.toUpperCase() + '] ' + label;
  document.body.appendChild(_tip);
  moveTip(e);
}}
function moveTip(e) {{
  if (_tip) {{ _tip.style.left = (e.clientX + 12) + 'px'; _tip.style.top = (e.clientY - 20) + 'px'; }}
}}
function hideTip() {{ if (_tip) {{ _tip.remove(); _tip = null; }} }}
document.addEventListener('mousemove', moveTip);

// ── NEXUS LIVE UPDATE ────────────────────────────────────────────────────────
const LIVE_QUERY   = '{live_query}';
// Dynamische API-URL: funktioniert auf PC (localhost) UND Handy via VPN (echte IP)
const LIVE_API     = 'http://' + window.location.hostname + ':11430/api/data?query=' + encodeURIComponent(LIVE_QUERY);
// ── Konfigurierbares Refresh-Intervall ──────────────────────────────────────
const _REFRESH_STEPS = [1,2,3,5,10]; // Minuten
let   _refreshIdx    = (function(){{
  const saved = localStorage.getItem('nexus_refresh_min');
  const i = _REFRESH_STEPS.indexOf(Number(saved));
  return i >= 0 ? i : 2; // Standard: 3 min (Index 2)
}})();
let   REFRESH_MS     = _REFRESH_STEPS[_refreshIdx] * 60 * 1000;
let   lastUpdate     = Date.now();
let   liveMarkers    = [];
let   knownTitles    = new Set({known_titles_json});

function adjustRefresh(delta) {{
  _refreshIdx = Math.max(0, Math.min(_REFRESH_STEPS.length - 1, _refreshIdx + delta));
  REFRESH_MS  = _REFRESH_STEPS[_refreshIdx] * 60 * 1000;
  localStorage.setItem('nexus_refresh_min', _REFRESH_STEPS[_refreshIdx]);
  const lbl = document.getElementById('refresh-interval-label');
  if (lbl) lbl.textContent = _REFRESH_STEPS[_refreshIdx] + ' min';
  // Live-Interval neu starten
  clearInterval(_refreshTimer);
  _refreshTimer = setInterval(fetchLiveUpdate, REFRESH_MS);
  clearInterval(_refreshSparkTimer);
  _refreshSparkTimer = setInterval(fetchSparkline, REFRESH_MS);
}}

(function initRefreshLabel(){{
  const lbl = document.getElementById('refresh-interval-label');
  if (lbl) lbl.textContent = _REFRESH_STEPS[_refreshIdx] + ' min';
}})();

function updateCountdown() {{
  const msLeft = REFRESH_MS - (Date.now() - lastUpdate);
  const sec    = Math.max(0, Math.round(msLeft / 1000));
  const el     = document.getElementById('refresh-countdown');
  if (!el) return;
  if (sec < 10) {{
    el.style.color = 'var(--red)';
    el.textContent = 'Refresh in ' + sec + 's';
  }} else if (sec < 30) {{
    el.style.color = '#ff8800';
    el.textContent = 'Refresh in ' + sec + 's';
  }} else {{
    el.style.color = 'var(--muted)';
    el.textContent = 'Refresh in ' + (sec >= 60 ? Math.ceil(sec/60)+'min' : sec+'s');
  }}
}}
setInterval(updateCountdown, 1000);

function setLiveStatus(online, msg) {{
  const dot = document.getElementById('live-dot');
  const st  = document.getElementById('live-status');
  if (dot) dot.className = 'live-dot' + (online ? '' : ' offline');
  if (st)  st.textContent = msg;
  if (st)  st.style.color = online ? 'var(--green)' : 'var(--muted)';
}}

function addLiveIncident(inc) {{
  if (!inc.lat || !inc.lon) return;
  const icon = L.divIcon({{
    className: 'new-marker',
    html: '<div style="width:14px;height:14px;background:#ff2222;border-radius:50%;border:2px solid #ff8888;box-shadow:0 0 10px #ff0000"></div>',
    iconSize: [14,14], iconAnchor: [7,7],
  }});
  const m = L.marker([inc.lat, inc.lon], {{icon:icon}}).addTo(map);
  L.circle([inc.lat, inc.lon], {{radius:12000,color:'#ff2222',weight:1,opacity:.5,fillOpacity:.1}}).addTo(map);
  m.bindPopup(
    '<div style="min-width:220px;font-family:monospace">' +
    '<div style="color:#ff4444;font-weight:bold;margin-bottom:4px">🆕 NEU: ' + (inc.place||'?') + '</div>' +
    '<a href="' + (inc.url||'#') + '" target="_blank" style="color:#ffd700;font-size:11px">' + inc.title + '</a>' +
    '<div style="color:#8aa0b0;font-size:10px;margin-top:3px">' + (inc.source||'') + '</div>' +
    (inc.lat && inc.lon ? copBtn(inc.lat, inc.lon, 13) : '') +
    '</div>'
  );
  liveMarkers.push(m);
}}

function addLiveAircraft(ac) {{
  if (!ac.lat || !ac.lon) return;
  const isSusp = ac.suspicious && ac.suspicious.length > 0;
  const isHeli = !!ac.helicopter;
  const isISR  = !!ac.is_isr;
  const noCs   = !ac.callsign || ac.callsign==='(kein)';

  // ISR hat eigene Farbe (violett/magenta) + Typ-Badge
  const color = isISR   ? '#cc44ff'
              : isSusp  ? '#ff4444'
              : isHeli  ? '#ff9900'
              : noCs    ? '#ffd700'
              :           '#00ff88';
  const sym   = isHeli ? '🚁' : (isISR ? '🔎' : '✈');
  const sz    = isHeli ? '16' : (isSusp || isISR ? '18' : '14');

  // ISR-Icon: Symbol + kleines Typ-Label
  const htmlIcon = isISR
    ? '<div style="position:relative;display:inline-block">' +
      '<span style="color:' + color + ';font-size:18px;filter:drop-shadow(0 0 4px ' + color + ')">' + sym + '</span>' +
      '<span style="position:absolute;top:-6px;left:18px;background:#220033;color:' + color +
      ';font-size:7px;font-weight:bold;border-radius:2px;padding:0 2px;white-space:nowrap">' +
      (ac.isr_role || 'ISR') + '</span></div>'
    : '<span style="color:' + color + ';font-size:' + sz + 'px">' + sym + '</span>';

  const icon = L.divIcon({{
    className: 'new-marker',
    html: htmlIcon,
    iconSize:[28,22], iconAnchor:[11,11],
  }});
  const m = L.marker([ac.lat, ac.lon], {{icon:icon, zIndexOffset: isISR ? 500 : 0}}).addTo(map);

  // ISR: Popup mit Details
  if (isISR) {{
    m.bindPopup(
      '<div style="min-width:230px;font-family:monospace">' +
      '<b style="color:' + color + ';font-size:13px">🔎 ISR-AUFKLÄRER</b>' +
      '<div style="background:#110022;border-left:3px solid ' + color +
      ';padding:4px 7px;margin:5px 0;font-size:11px;color:' + color + '">' +
      '<b>' + (ac.isr_type||'Unbekannt') + '</b> [' + (ac.isr_role||'?') + ']<br>' +
      (ac.isr_note||'') + '</div>' +
      '<div style="font-size:11px;color:#c8d6e0">Callsign: <b>' + (ac.callsign||'?') + '</b>' +
      (ac.icao24 ? ' | ICAO: ' + ac.icao24 : '') + '</div>' +
      '<div style="font-size:10px;color:#8aa0b0">' +
      (ac.altitude_ft ? ac.altitude_ft + 'ft' : '?ft') +
      (ac.velocity_kmh ? ' | ' + ac.velocity_kmh + 'km/h' : '') +
      (ac.origin ? ' | ' + ac.origin : '') + '</div>' +
      '<div style="font-size:10px;color:#cc44ff;margin-top:3px">Konfidenz: ' + (ac.isr_conf||'?').toUpperCase() + '</div>' +
      copBtn(ac.lat, ac.lon, 9) +
      '</div>'
    );
  }}

  liveMarkers.push(m);
}}

// ── Ghost-Marker (Transponder AUS) ──────────────────────────────────────────
let ghostMarkers = [];

// Konvertierung: km -> Grad (Breiten- und Laengengrad)
function kmToLatDeg(km) {{ return km / 111.0; }}
function kmToLonDeg(km, lat) {{ return km / (111.0 * Math.cos(lat * Math.PI / 180)); }}

// Berechnet Koordinaten-Offset in Fahrtrichtung
function projectPos(lat, lon, trackDeg, distKm) {{
  const trackRad = (trackDeg - 90) * Math.PI / 180; // 0=Nord, 90=Ost
  const dLat = (distKm / 111.0) * Math.sin((90 - trackDeg) * Math.PI / 180);
  const dLon = (distKm / (111.0 * Math.cos(lat * Math.PI / 180))) * Math.sin(trackDeg * Math.PI / 180);
  return [lat + dLat, lon + dLon];
}}

// Schaetzt Restreichweite nach Geschwindigkeit (Heuristik)
function estimateRange(vel_kmh) {{
  if (!vel_kmh || vel_kmh < 50) return {{ km: 200, type: 'Kleinflugzeug', color: '#ffaa44' }};
  if (vel_kmh < 320) return {{ km: 400,  type: 'Propeller/Kleinflugzeug', color: '#ffaa44' }};
  if (vel_kmh < 500) return {{ km: 1800, type: 'Regionaljet/Turboprop',   color: '#ffd700' }};
  if (vel_kmh < 700) return {{ km: 4000, type: 'Mittelstreckenjet',       color: '#ff8800' }};
  return {{ km: 7000, type: 'Langstreckenjet', color: '#ff4444' }};
}}

function addGhostAircraft(ac) {{
  if (!ac.lat || !ac.lon) return;
  const vel      = ac.velocity_kmh || 0;
  const ageS     = ac.snap_age_s   || 0;
  const ageMin   = Math.round(ageS / 60);
  const hasTrack = ac.track !== null && ac.track !== undefined;
  const range    = estimateRange(vel);

  // Geschaetzte Entfernung seit Verschwinden
  const traveledKm = vel > 0 ? vel * (ageS / 3600) : 0;

  // Geschaetzter jetziger Aufenthaltsort
  let estLat = ac.lat, estLon = ac.lon;
  if (hasTrack && traveledKm > 0) {{
    const proj = projectPos(ac.lat, ac.lon, ac.track, traveledKm);
    estLat = proj[0]; estLon = proj[1];
  }}

  // 1. Ghost-Marker an letzter bekannter Position
  const ghostIcon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:30px;height:30px">' +
          '<span style="color:#505868;font-size:16px;opacity:0.8;' +
          'filter:drop-shadow(0 0 5px #ff3333)">✈</span>' +
          '<span style="position:absolute;top:-5px;right:-6px;background:#cc0000;' +
          'color:#fff;font-size:7px;font-weight:bold;border-radius:2px;' +
          'padding:1px 3px;line-height:11px;letter-spacing:0.5px">TX</span>' +
          '</div>',
    iconSize: [30, 30], iconAnchor: [15, 15],
  }});
  const lastPosMarker = L.marker([ac.lat, ac.lon], {{icon: ghostIcon, opacity: 0.85}}).addTo(map);

  // 2. Kleiner Kreis um letzte bekannte Position
  const lastCircle = L.circle([ac.lat, ac.lon], {{
    radius: 8000, color: '#ff3333', weight: 1.5, opacity: 0.7,
    fillColor: '#ff0000', fillOpacity: 0.08, dashArray: '4,6',
  }}).addTo(map);

  // 3. Gestrichelte Linie: letzte Position → geschaetzte Jetztposition
  if (hasTrack && traveledKm > 0) {{
    const pathLine = L.polyline([[ac.lat, ac.lon], [estLat, estLon]], {{
      color: '#ff4444', weight: 1.5, opacity: 0.55, dashArray: '10,7',
    }}).addTo(map);
    ghostMarkers.push(pathLine);
    liveMarkers.push(pathLine);
  }}

  // 4. Marker an geschaetzter Jetztposition (Fragezeichen-Flieger)
  if (traveledKm > 5) {{
    const estIcon = L.divIcon({{
      className: '',
      html: '<div style="position:relative;width:30px;height:30px">' +
            '<span style="color:#ff6666;font-size:15px;opacity:0.65;' +
            'filter:drop-shadow(0 0 6px #ff0000)">✈</span>' +
            '<span style="position:absolute;top:-5px;right:-8px;background:#880000;' +
            'color:#ffaaaa;font-size:9px;font-weight:bold;border-radius:2px;' +
            'padding:0 2px;line-height:12px">?</span>' +
            '</div>',
      iconSize: [30, 30], iconAnchor: [15, 15],
    }});
    const estMarker = L.marker([estLat, estLon], {{icon: estIcon, opacity: 0.6}}).addTo(map);

    // Kurze Linie als Kurs-Pfeil ab geschaetzter Position
    const fwdProj = projectPos(estLat, estLon, ac.track, 80);
    const fwdArrow = L.polyline([[estLat, estLon], [fwdProj[0], fwdProj[1]]], {{
      color: '#ff6666', weight: 1, opacity: 0.4, dashArray: '6,8',
    }}).addTo(map);

    // Popup geschaetzte Position
    estMarker.bindPopup(
      '<div style="min-width:190px;font-family:monospace">' +
      '<div style="background:#1e0808;border:1px solid #ff6666;border-radius:3px;' +
      'padding:3px 8px;margin-bottom:5px;text-align:center">' +
      '<span style="color:#ff6666;font-weight:bold;font-size:12px">GESCHAETZTE POSITION</span></div>' +
      '<b style="color:#cc8888">' + (ac.callsign || '(kein)') + '</b><br>' +
      '<span style="color:#8aa0b0;font-size:11px">Reiste ~' + Math.round(traveledKm) + ' km seit TX-Verlust</span><br>' +
      '<span style="color:#c8d6e0;font-size:11px">Kurs: ' + Math.round(ac.track || 0) + '° &nbsp;|&nbsp; ' + vel + ' km/h</span><br>' +
      '<span style="color:#606878;font-size:10px">Unsicherheitsradius steigt mit Zeit!</span>' +
      '</div>'
    );
    ghostMarkers.push(estMarker);
    ghostMarkers.push(fwdArrow);
    liveMarkers.push(estMarker);
    liveMarkers.push(fwdArrow);

    // 5. Restreichweiten-Kreis ab geschaetzter Position
    const rangeCircle = L.circle([estLat, estLon], {{
      radius: range.km * 1000,
      color: range.color, weight: 1, opacity: 0.3,
      fillColor: range.color, fillOpacity: 0.03,
      dashArray: '12,10',
    }}).addTo(map);
    rangeCircle.bindTooltip(
      '<span style="font-family:monospace;font-size:11px">' +
      '⛽ Restreichweite: ~' + range.km + ' km (' + range.type + ')</span>',
      {{permanent: false, direction: 'top'}}
    );
    ghostMarkers.push(rangeCircle);
    liveMarkers.push(rangeCircle);
  }}

  // Hauptpopup an letzter bekannter Position
  const alt      = ac.altitude_ft ? ac.altitude_ft.toLocaleString() + ' ft' : '?';
  const spd      = vel ? vel + ' km/h' : '?';
  const suspHtml = ac.suspicious
    ? '<div style="margin-top:5px;padding:4px;background:#2a0808;border-left:3px solid #ff4444;' +
      'color:#ff8800;font-size:11px">⚠ ' + ac.suspicious + '</div>'
    : '';

  lastPosMarker.bindPopup(
    '<div style="min-width:210px;font-family:monospace">' +
    '<div style="background:#1a0000;border:1px solid #ff4444;border-radius:3px;' +
    'padding:4px 8px;margin-bottom:6px;text-align:center">' +
    '<span style="color:#ff4444;font-weight:bold;font-size:13px">⚡ TRANSPONDER AUS</span></div>' +
    '<b style="color:#aaaaaa;font-size:13px">' + (ac.callsign || '(kein Callsign)') + '</b><br>' +
    '<span style="color:#8aa0b0;font-size:11px">Herkunft: ' + (ac.origin || '?') + '</span><br>' +
    '<span style="color:#c8d6e0;font-size:11px">📏 ' + alt + ' &nbsp;|&nbsp; 🚀 ' + spd + '</span><br>' +
    '<span style="color:#ffd700;font-size:11px">⏱ Kein Signal seit: ~' + ageMin + ' min</span><br>' +
    (traveledKm > 5 ? '<span style="color:#ff8800;font-size:10px">→ Seitdem geschaetzte Strecke: ~' + Math.round(traveledKm) + ' km</span><br>' : '') +
    '<span style="color:#aaaaaa;font-size:10px">Typ: ' + range.type + ' | Max-Reichweite: ~' + range.km + ' km</span>' +
    suspHtml +
    copBtn(ac.lat, ac.lon, 11) +
    '</div>'
  );

  ghostMarkers.push(lastPosMarker);
  ghostMarkers.push(lastCircle);
  liveMarkers.push(lastPosMarker);
  liveMarkers.push(lastCircle);
}}

// ── Ghost-Schiffe (AIS DUNKEL) ───────────────────────────────────────────────

// Schiffstyp-Klassifizierung nach Geschwindigkeit (in Knoten)
function vesselTypeBySpeed(spd_kn) {{
  if (!spd_kn || spd_kn < 1)  return {{ label: 'Unbekannt',             color: '#888888' }};
  if (spd_kn < 6)              return {{ label: 'VLCC / Bulker (langsam)', color: '#ff8800' }};
  if (spd_kn < 14)             return {{ label: 'Tanker / Frachtschiff',  color: '#ff6600' }};
  if (spd_kn < 22)             return {{ label: 'Containerschiff / RoRo', color: '#ff4400' }};
  return                              {{ label: 'Schnellfaehre / Militaer', color: '#ff2200' }};
}}

function addGhostVessel(v) {{
  if (!v.lat || !v.lon) return;
  const spd_kn   = v.speed || 0;
  const spd_kmh  = spd_kn * 1.852;
  const ageS     = v.snap_age_s || 0;
  const ageMin   = Math.round(ageS / 60);
  const hasHead  = v.heading !== null && v.heading !== undefined;
  const vtype    = vesselTypeBySpeed(spd_kn);

  // Zurueckgelegte Strecke seit Verschwinden (in km)
  const traveledKm = spd_kmh > 0 ? spd_kmh * (ageS / 3600) : 0;

  // Geschaetzte Jetztposition
  let estLat = v.lat, estLon = v.lon;
  if (hasHead && traveledKm > 0) {{
    const proj = projectPos(v.lat, v.lon, v.heading, traveledKm);
    estLat = proj[0]; estLon = proj[1];
  }}

  // 1. Letzter bekannter Ankerplatz: grauer Anker + oranger AIS-Badge
  const ghostShipIcon = L.divIcon({{
    className: '',
    html: '<div style="position:relative;width:30px;height:30px">' +
          '<span style="color:#505868;font-size:17px;opacity:0.85;' +
          'filter:drop-shadow(0 0 5px #ff8800)">⚓</span>' +
          '<span style="position:absolute;top:-5px;right:-8px;background:#994400;' +
          'color:#ffcc88;font-size:7px;font-weight:bold;border-radius:2px;' +
          'padding:1px 2px;line-height:11px;letter-spacing:0.5px">AIS</span>' +
          '</div>',
    iconSize: [30, 30], iconAnchor: [15, 15],
  }});
  const lastPosMarker = L.marker([v.lat, v.lon], {{icon: ghostShipIcon, opacity: 0.9}}).addTo(map);

  // 2. Kleiner gestrichelter Kreis um letzte bekannte Position
  const lastCircle = L.circle([v.lat, v.lon], {{
    radius: 18000, color: '#ff8800', weight: 1.5, opacity: 0.6,
    fillColor: '#ff6600', fillOpacity: 0.07, dashArray: '5,7',
  }}).addTo(map);

  // 3. Strecken-Linie: letzte Position → geschaetzte Jetztposition
  if (hasHead && traveledKm > 0) {{
    const pathLine = L.polyline([[v.lat, v.lon], [estLat, estLon]], {{
      color: '#ff8800', weight: 1.5, opacity: 0.5, dashArray: '12,8',
    }}).addTo(map);
    ghostMarkers.push(pathLine);
    liveMarkers.push(pathLine);
  }}

  // 4. Marker an geschaetzter Jetztposition + Kurs-Pfeil
  if (traveledKm > 1) {{
    const estShipIcon = L.divIcon({{
      className: '',
      html: '<div style="position:relative;width:30px;height:30px">' +
            '<span style="color:#cc6600;font-size:16px;opacity:0.6;' +
            'filter:drop-shadow(0 0 5px #ff6600)">⚓</span>' +
            '<span style="position:absolute;top:-5px;right:-8px;background:#662200;' +
            'color:#ffaa66;font-size:9px;font-weight:bold;border-radius:2px;' +
            'padding:0 2px;line-height:12px">?</span>' +
            '</div>',
      iconSize: [30, 30], iconAnchor: [15, 15],
    }});
    const estMarker = L.marker([estLat, estLon], {{icon: estShipIcon, opacity: 0.6}}).addTo(map);

    // Kurzer Kurs-Pfeil weiter in Fahrtrichtung (50 km)
    if (hasHead) {{
      const fwdProj = projectPos(estLat, estLon, v.heading, 50);
      const fwdArrow = L.polyline([[estLat, estLon], [fwdProj[0], fwdProj[1]]], {{
        color: '#ff9900', weight: 1, opacity: 0.35, dashArray: '8,10',
      }}).addTo(map);
      ghostMarkers.push(fwdArrow);
      liveMarkers.push(fwdArrow);
    }}

    // 5. Unsicherheits-Kreis: waechst mit Zeit + moeglicher Kursabweichung
    // Ein Schiff koennte in 1h 20° Kurs abweichen → seitlicher Fehler steigt
    const uncertKm = Math.max(traveledKm * 0.25, 15);
    const uncertCircle = L.circle([estLat, estLon], {{
      radius: uncertKm * 1000,
      color: '#ff8800', weight: 1, opacity: 0.25,
      fillColor: '#ff6600', fillOpacity: 0.04, dashArray: '14,12',
    }}).addTo(map);
    uncertCircle.bindTooltip(
      '<span style="font-family:monospace;font-size:11px">' +
      '📍 Unsicherheitsradius: ~' + Math.round(uncertKm) + ' km (' + vtype.label + ')</span>',
      {{permanent: false, direction: 'top'}}
    );

    estMarker.bindPopup(
      '<div style="min-width:190px;font-family:monospace">' +
      '<div style="background:#1e0e00;border:1px solid #ff8800;border-radius:3px;' +
      'padding:3px 8px;margin-bottom:5px;text-align:center">' +
      '<span style="color:#ff8800;font-weight:bold;font-size:12px">GESCHAETZTE POSITION</span></div>' +
      '<b style="color:#cc9966">' + (v.name || '(unbekannt)') + '</b><br>' +
      '<span style="color:#8aa0b0;font-size:11px">Reiste ~' + Math.round(traveledKm) + ' km seit AIS-Verlust</span><br>' +
      '<span style="color:#c8d6e0;font-size:11px">Kurs: ' + Math.round(v.heading || 0) + '° | ' + spd_kn + ' kn</span><br>' +
      '<span style="color:#606878;font-size:10px">Unsicherheitsradius: ~' + Math.round(uncertKm) + ' km</span>' +
      '</div>'
    );

    ghostMarkers.push(estMarker);
    ghostMarkers.push(uncertCircle);
    liveMarkers.push(estMarker);
    liveMarkers.push(uncertCircle);
  }}

  // Popup letzte bekannte Position
  const flagStr  = v.flag ? ' ' + v.flag : '';
  const typeStr  = v.type ? v.type + ' | ' : '';
  const mmsiStr  = v.mmsi ? '<span style="color:#4a6070;font-size:10px">MMSI: ' + v.mmsi + '</span><br>' : '';

  lastPosMarker.bindPopup(
    '<div style="min-width:215px;font-family:monospace">' +
    '<div style="background:#1a0800;border:1px solid #ff8800;border-radius:3px;' +
    'padding:4px 8px;margin-bottom:6px;text-align:center">' +
    '<span style="color:#ff8800;font-weight:bold;font-size:13px">⚡ AIS DUNKEL</span></div>' +
    '<b style="color:#ccaa88;font-size:13px">' + (v.name || '(unbekannt)') + flagStr + '</b><br>' +
    '<span style="color:#8aa0b0;font-size:11px">' + typeStr + vtype.label + '</span><br>' +
    mmsiStr +
    '<span style="color:#c8d6e0;font-size:11px">🚢 ' + spd_kn + ' kn (' + Math.round(spd_kmh) + ' km/h)</span>' +
    (hasHead ? ' &nbsp;|&nbsp; <span style="color:#c8d6e0;font-size:11px">Kurs: ' + Math.round(v.heading) + '°</span>' : '') + '<br>' +
    '<span style="color:#ffd700;font-size:11px">⏱ Kein AIS seit: ~' + ageMin + ' min</span><br>' +
    (traveledKm > 1 ? '<span style="color:#ff8800;font-size:10px">→ Seitdem ~' + Math.round(traveledKm) + ' km geschaetzt</span><br>' : '') +
    '<div style="margin-top:5px;padding:4px;background:#1a1000;border-left:3px solid #ff8800;' +
    'color:#ffaa44;font-size:10px">⚠ AIS-Dunkelheit kann auf Sanktionsumgehung,<br>' +
    'militaerische Ops oder Piraterie hinweisen.</div>' +
    copBtn(v.lat, v.lon, 10) +
    '</div>'
  );

  ghostMarkers.push(lastPosMarker);
  ghostMarkers.push(lastCircle);
  liveMarkers.push(lastPosMarker);
  liveMarkers.push(lastCircle);
}}

async function fetchLiveUpdate() {{
  try {{
    const resp = await fetch(LIVE_API, {{signal: AbortSignal.timeout(15000)}});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();

    // Alte Live-Marker + Ghost-Marker entfernen
    liveMarkers.forEach(function(m) {{ map.removeLayer(m); }});
    liveMarkers  = [];
    ghostMarkers = [];

    // Neue Vorfälle (noch nicht bekannt)
    let newCount = 0;
    (data.incidents || []).forEach(function(inc) {{
      if (!knownTitles.has(inc.title)) {{
        knownTitles.add(inc.title);
        addLiveIncident(inc);
        newCount++;
      }}
    }});

    // Aktuelle Flugzeuge neu zeichnen
    (data.flights && data.flights.aircraft || []).forEach(function(ac) {{
      addLiveAircraft(ac);
    }});

    // Ghost-Marker: verschwundene Transponder (Flugzeuge)
    const vanished = data.vanished_aircraft || [];
    vanished.forEach(function(ac) {{ addGhostAircraft(ac); }});

    // Ghost-Marker: AIS-dunkle Schiffe
    const darkVessels = data.vanished_vessels || [];
    darkVessels.forEach(function(v) {{ addGhostVessel(v); }});

    const totalGhosts = vanished.length + darkVessels.length;
    if (totalGhosts > 0) newCount += totalGhosts;

    // Telegram Surge-Alerts
    const surges = data.telegram_surges || [];
    const surgeDiv = document.getElementById('surge-banner');
    if (surges.length > 0 && surgeDiv) {{
      const surgeHtml = surges.map(function(s) {{
        return '<span style="margin-right:12px">⚡ <b>@' + s.channel + '</b> Surge x' +
               s.score.toFixed(1) + ' (' + s.recent_count + ' Posts/3min) ' +
               '<a href="' + s.channel_url + '" target="_blank" ' +
               'style="color:#ffdd88;text-decoration:underline">→ öffnen</a></span>';
      }}).join('');
      surgeDiv.innerHTML = surgeHtml;
      surgeDiv.style.display = 'block';
    }} else if (surgeDiv) {{
      surgeDiv.style.display = 'none';
    }}

    // Satellit-Überflug-Ticker
    const passes = data.satellite_passes || [];
    const satDiv = document.getElementById('sat-ticker');
    if (passes.length > 0 && satDiv) {{
      const satHtml = passes.slice(0, 4).map(function(p) {{
        const inMin = p.in_min;
        const timing = inMin < 60
          ? '<span style="color:#00aaff">in ' + inMin + 'min</span>'
          : '<span style="color:#4488cc">in ' + Math.round(inMin/60) + 'h</span>';
        const src = p.source === 'n2yo.com API'
          ? '<span style="color:#00ff88">●</span>'
          : '<span style="color:#665544">~</span>';
        return src + ' <b>' + p.name + '</b> ' + timing +
               (p.start_utc ? ' (' + p.start_utc + ')' : '');
      }}).join('&nbsp;&nbsp;│&nbsp;&nbsp;');
      satDiv.innerHTML = '🛰 SATELLIT:&nbsp;&nbsp;' + satHtml;
      satDiv.style.display = 'block';
    }} else if (satDiv) {{
      satDiv.style.display = 'none';
    }}

    lastUpdate = Date.now();
    const parts = [];
    if (newCount - totalGhosts > 0) parts.push((newCount - totalGhosts) + ' neue Ereignisse');
    if (vanished.length    > 0) parts.push(vanished.length    + ' Transponder AUS');
    if (darkVessels.length > 0) parts.push(darkVessels.length + ' Schiff AIS-dunkel');
    if (surges.length      > 0) parts.push(surges.length      + ' Telegram Surge');
    if (passes.length      > 0) {{
      const nextPass = passes[0];
      parts.push('🛰 ' + nextPass.name + ' in ' + nextPass.in_min + 'min');
    }}
    // Eskalations-Score
    const esc     = data.escalation || {{}};
    const escBar  = document.getElementById('esc-bar');
    if (escBar && esc.score !== undefined) {{
      const sc  = esc.score || 0;
      const col = esc.color || '#00ff88';
      escBar.style.display = 'flex';
      escBar.style.borderColor = sc > 60 ? col : (sc > 40 ? '#884400' : '#003322');
      const fill = document.getElementById('esc-fill');
      if (fill) {{ fill.style.width = sc + '%'; fill.style.background = col; }}
      const lbl = document.getElementById('esc-label');
      if (lbl) {{ lbl.textContent = esc.level||'?'; lbl.style.color = col; }}
      const icn = document.getElementById('esc-icon');
      if (icn) icn.textContent = esc.icon || '🟢';
      const scEl = document.getElementById('esc-score');
      if (scEl) scEl.textContent = sc + '/100';
      const sigEl = document.getElementById('esc-signals');
      if (sigEl && esc.signal_count > 0)
        sigEl.textContent = esc.signal_count + ' Signal' + (esc.signal_count > 1 ? 'e' : '');
      if (sc > 40) parts.push((esc.icon||'') + ' ESK ' + sc);
    }}

    // ── HUMINT Taktische Meldungen (live) ──────────────────────────────────
    let humintLive = 0;
    (data.humint_markers || []).forEach(function(h) {{
      if (!h.lat || !h.lon) return;
      const col = h.color || '#ff8800';
      const ico = h.icon  || '📍';
      const icon = L.divIcon({{
        className: '',
        html: '<div style="position:relative;width:26px;height:26px">' +
              '<div style="position:absolute;inset:0;border-radius:3px;background:' + col +
              ';opacity:.15;animation:pulse 2.5s infinite"></div>' +
              '<div style="position:absolute;inset:0;display:flex;align-items:center;' +
              'justify-content:center;font-size:15px;filter:drop-shadow(0 0 4px ' + col + ')">' + ico + '</div>' +
              '</div>',
        iconSize:[26,26], iconAnchor:[13,13]
      }});
      const conf_pct = Math.round((h.confidence||0)*100);
      const m = L.marker([h.lat, h.lon], {{icon: icon, zIndexOffset: 200}})
        .bindPopup(
          '<div style="min-width:200px">' +
          '<b style="color:' + col + ';font-size:12px">' + (h.title||'⚡ HUMINT') + '</b><br>' +
          (h.units&&h.units.length ? '<span style="color:#ffcc44;font-size:10px">🪖 ' + h.units[0] + '</span><br>' : '') +
          '<span style="font-size:10px;color:#c8d6e0;display:block;margin:4px 0">' +
          (h.text||'').slice(0,160) + '</span>' +
          '<div style="display:flex;gap:8px;font-size:10px;color:#4a6070">' +
          '<span>Konf: <b style="color:' + col + '">' + conf_pct + '%</b></span>' +
          '<span>Quelle: ' + (h.source||'?') + '</span>' +
          '</div>' + copBtn(h.lat, h.lon, 13) + '</div>'
        ).addTo(map);
      liveMarkers.push(m);
      humintLive++;
    }});
    if (humintLive > 0) parts.push('🎯 ' + humintLive + ' HUMINT');

    // ── Fusion Threat Assessments (live) ───────────────────────────────────
    let fusionLive = 0;
    (data.fusion_threats || []).forEach(function(ft) {{
      if (!ft.lat || !ft.lon) return;
      const col = ft.color || '#ff0044';
      const ico = ft.icon  || '💥';
      const icon = L.divIcon({{
        className: '',
        html: '<div style="position:relative;width:38px;height:38px">' +
              '<div style="position:absolute;inset:0;border-radius:50%;border:2px solid ' + col +
              ';animation:pulse 1.2s infinite;opacity:.8"></div>' +
              '<div style="position:absolute;inset:6px;display:flex;align-items:center;' +
              'justify-content:center;font-size:18px">' + ico + '</div>' +
              '</div>',
        iconSize:[38,38], iconAnchor:[19,19]
      }});
      const sev_bg = {{KRITISCH:'#ff0044',HOCH:'#ff6600',MITTEL:'#ffaa00',NIEDRIG:'#00cc88'}};
      const sev_col = sev_bg[ft.severity] || col;
      const m = L.marker([ft.lat, ft.lon], {{icon: icon, zIndexOffset: 500}})
        .bindPopup(
          '<div style="min-width:220px">' +
          '<b style="color:' + col + ';font-size:13px">' + (ft.title||ft.pattern||'FUSION') + '</b>' +
          (ft.severity ? ' <span style="background:' + sev_col + ';color:#000;font-size:9px;' +
          'padding:1px 5px;border-radius:2px;font-weight:bold">' + ft.severity + '</span>' : '') + '<br>' +
          '<span style="font-size:10px;color:#c8d6e0;display:block;margin:4px 0">' +
          (ft.text||'').slice(0,200) + '</span>' +
          '<div style="display:flex;gap:8px;font-size:10px;color:#4a6070">' +
          '<span>Konf: <b style="color:' + col + '">' + Math.round((ft.confidence||0)*100) + '%</b></span>' +
          '<span>Signale: <b>' + (ft.signal_count||0) + '</b></span>' +
          (ft.signals ? '<span>' + (ft.signals||[]).join('+') + '</span>' : '') +
          '</div>' + copBtn(ft.lat, ft.lon, 11) + '</div>'
        ).addTo(map);
      liveMarkers.push(m);
      fusionLive++;
    }});
    if (fusionLive > 0) parts.push('🔗 ' + fusionLive + ' FUSION');

    const msg = parts.length > 0
      ? '🔴 LIVE – ' + parts.join(' | ')
      : '🟢 LIVE – Alles aktuell';
    setLiveStatus(true, msg);

  }} catch(e) {{
    setLiveStatus(false, '⚫ Live-Server offline – starte NEXUS');
    lastUpdate = Date.now(); // Countdown zurücksetzen auch bei Fehler
  }}
}}

// Sofort prüfen + dann alle 3 min
setTimeout(fetchLiveUpdate, 2000);
let _refreshTimer = setInterval(fetchLiveUpdate, REFRESH_MS);

// ── DARK / LIGHT MODE TOGGLE ─────────────────────────────
function toggleTheme() {{
  const body = document.body;
  const isLight = body.classList.toggle('light-mode');
  localStorage.setItem('nexus_theme', isLight ? 'light' : 'dark');
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = isLight ? '🌙 DUNKEL' : '☀ HELL / 🌙 DUNKEL';
  // Karte auf Light-Tiles umschalten wenn Hell-Modus
  if (window._nexusMap) {{
    if (isLight) {{
      if (window._nexusMap.hasLayer(darkLayer)) {{ window._nexusMap.removeLayer(darkLayer); cartoLayer.addTo(window._nexusMap); }}
    }} else {{
      if (window._nexusMap.hasLayer(cartoLayer)) {{ window._nexusMap.removeLayer(cartoLayer); darkLayer.addTo(window._nexusMap); }}
    }}
  }}
}}

// Gespeichertes Theme beim Laden wiederherstellen
(function() {{
  const saved = localStorage.getItem('nexus_theme');
  if (saved === 'light') {{
    document.body.classList.add('light-mode');
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = '🌙 DUNKEL';
  }}
}})();

// ── MOBILE: Statusbar kürzen ──────────────────────────────
(function() {{
  if (window.innerWidth < 600) {{
    // Map-Höhe reduzieren
    const map = document.getElementById('nexus-map');
    if (map) map.style.height = '280px';
    // Falls Map schon initialisiert: invalidateSize
    if (window._nexusMap) setTimeout(() => _nexusMap.invalidateSize(), 100);
  }}
}})();

// ── Chart.js Sparkline (T-62) ─────────────────────────────────────────────
let _sparkChart = null;
let _refreshSparkTimer = null;

(function loadChartJs() {{
  if (window.Chart) {{ initSparkline(); return; }}
  const s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
  s.onload  = initSparkline;
  s.onerror = function() {{
    const el = document.getElementById('sparkline-footer');
    if (el) el.textContent = 'Chart.js nicht ladbar – kein Internet?';
  }};
  document.head.appendChild(s);
}})();

function initSparkline() {{
  fetchSparkline();
  _refreshSparkTimer = setInterval(fetchSparkline, REFRESH_MS);
}}

function fetchSparkline() {{
  if (!LIVE_QUERY) return;
  var ctrl = new AbortController();
  var tid  = setTimeout(function(){{ ctrl.abort(); }}, 10000);
  fetch('http://localhost:11430/api/predict?region=' + encodeURIComponent(LIVE_QUERY) + '&hours=72',
        {{signal: ctrl.signal}})
    .then(function(r){{ clearTimeout(tid); return r.json(); }})
    .then(function(data) {{
      const sl = data && data.sparkline;
      if (!sl || !sl.labels || sl.labels.length === 0) {{
        const el = document.getElementById('sparkline-footer');
        if (el) el.textContent = 'Noch keine Verlaufsdaten – wird nach dem ersten Scan befüllt.';
        return;
      }}
      renderSparkline(sl);
    }})
    .catch(function() {{
      // Eskalations-Trend Panel ausblenden wenn Predict-API nicht läuft
      const row = document.getElementById('sparkline-row');
      if (row) row.style.display = 'none';
    }});
}}

function renderSparkline(sl) {{
  const ctx = document.getElementById('nexus-sparkline');
  if (!ctx || !window.Chart) return;

  const labels = sl.labels || [];
  const scores = sl.scores || [];
  const preds  = sl.pred_scores || [];
  const anom   = sl.anomalies || [];

  const histData = scores.map(function(v,i){{
    return (preds[i] === null || preds[i] === undefined) ? v : null;
  }});

  let lastHistIdx = -1;
  for (let i = histData.length-1; i >= 0; i--) {{ if (histData[i] !== null) {{ lastHistIdx=i; break; }} }}

  const predData = scores.map(function(v,i){{
    if (i === lastHistIdx) return scores[i];
    if (preds[i] !== null && preds[i] !== undefined) return preds[i];
    return null;
  }});

  const anomData = anom
    .filter(function(i){{ return i < labels.length && scores[i] !== null; }})
    .map(function(i){{ return {{x: labels[i], y: scores[i]}}; }});

  if (_sparkChart) {{ _sparkChart.destroy(); _sparkChart = null; }}

  const isDark = !document.body.classList.contains('light-mode');
  const gridC  = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.07)';
  const tickC  = isDark ? '#4a6070' : '#6a7a8a';
  const legendC= isDark ? '#8aa0b0' : '#3a5060';

  _sparkChart = new Chart(ctx, {{
    data: {{
      labels: labels,
      datasets: [
        {{
          type: 'line', label: 'Verlauf',
          data: histData,
          borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.07)',
          borderWidth: 2, fill: true, tension: 0.3,
          pointRadius: 0, pointHoverRadius: 4, spanGaps: false,
        }},
        {{
          type: 'line', label: '48h-Vorschau',
          data: predData,
          borderColor: '#ff8800', backgroundColor: 'rgba(255,136,0,0.05)',
          borderWidth: 2, borderDash: [6,4], fill: false, tension: 0.25,
          pointRadius: 0, spanGaps: false,
        }},
        {{
          type: 'scatter', label: '⚠ Anomalie',
          data: anomData,
          backgroundColor: '#ff2244',
          pointStyle: 'triangle', pointRadius: 7, pointHoverRadius: 9,
          showLine: false,
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{
          display: true, position: 'top',
          labels: {{ color: legendC, font: {{size:10, family:'Courier New'}}, boxWidth:14, padding:8 }}
        }},
        tooltip: {{
          mode: 'index', intersect: false,
          backgroundColor: 'rgba(8,16,28,0.95)', borderColor: '#1a3050', borderWidth: 1,
          titleColor: '#8aa0b0', bodyColor: '#c8d6e0',
          titleFont: {{family:'Courier New',size:10}}, bodyFont: {{family:'Courier New',size:10}},
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color: tickC, font: {{size:9, family:'Courier New'}}, maxTicksLimit:10, maxRotation:0 }},
          grid: {{ color: gridC }}
        }},
        y: {{
          min: 0, max: 100,
          ticks: {{ color: tickC, font: {{size:9, family:'Courier New'}}, stepSize:25 }},
          grid: {{ color: gridC }}
        }}
      }},
      interaction: {{ mode:'nearest', axis:'x', intersect:false }},
      animation: {{ duration: 500 }},
    }}
  }});

  const pred = sl.prediction || {{}};
  const parts = [];
  if (pred.pred_24h !== undefined && pred.pred_24h !== null) parts.push('+24h: ' + Math.round(pred.pred_24h));
  if (pred.pred_48h !== undefined && pred.pred_48h !== null) parts.push('+48h: ' + Math.round(pred.pred_48h));
  if (pred.trend)      parts.push('Trend: ' + pred.trend);
  if (pred.confidence) parts.push('Konfidenz: ' + pred.confidence);
  if (anom.length > 0) parts.push('⚠ ' + anom.length + ' Anomalie(n)');
  const footer = document.getElementById('sparkline-footer');
  if (footer) footer.textContent = parts.join('  ·  ') || 'Keine Vorhersagedaten verfügbar';
  const sub = document.getElementById('sparkline-subtitle');
  if (sub) sub.textContent = (sl.data_points||0) + ' Messpunkte · 72h Verlauf · 48h Vorschau';
}}

// ── Karten-Suche via Nominatim ────────────────────────────────────────────
(function initMapSearch() {{
  const mapEl = document.getElementById('nexus-map');
  if (!mapEl) return;
  const wrapper = mapEl.closest('.panel');
  if (!wrapper) return;
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:6px;padding:4px 0 2px 0;';
  bar.innerHTML = '<input id="map-search-input" type="text"' +
    ' placeholder="Ort suchen… (z.B. Charkiw, Hormuz)"' +
    ' style="flex:1;background:#0a1520;border:1px solid #1a3050;border-radius:4px;' +
    'color:#c8d6e0;font-family:monospace;font-size:11px;padding:4px 8px;outline:none">' +
    '<button id="map-search-btn"' +
    ' style="background:#0d1a2a;border:1px solid #1a3050;border-radius:4px;color:#6a90a8;' +
    'font-family:monospace;font-size:10px;padding:4px 10px;cursor:pointer">' +
    '\U0001f50d Suchen</button>';
  var _msi = document.getElementById('map-search-input');
  if (_msi) _msi.addEventListener('keydown', function(e){{ if(e.key==='Enter') mapSearch(); }});
  var _msb = document.getElementById('map-search-btn');
  if (_msb) {{
    _msb.addEventListener('click', mapSearch);
    _msb.addEventListener('mouseover', function(){{ this.style.background='#1a3050'; }});
    _msb.addEventListener('mouseout',  function(){{ this.style.background='#0d1a2a'; }});
  }}
  const ph = wrapper.querySelector('.ph');
  if (ph) ph.after(bar); else wrapper.prepend(bar);
}})();

function mapSearch() {{
  const inp = document.getElementById('map-search-input');
  if (!inp || !inp.value.trim()) return;
  const q = inp.value.trim();
  inp.style.borderColor = '#ff8800';
  fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q),
        {{headers: {{'Accept-Language':'de,en','User-Agent':'NEXUS-OSINT/1.0'}}}})
    .then(function(r){{ return r.json(); }})
    .then(function(res) {{
      inp.style.borderColor = '#1a3050';
      if (!res || res.length === 0) {{
        inp.style.borderColor = '#ff4444';
        inp.placeholder = 'Nicht gefunden: ' + q;
        setTimeout(function(){{ inp.style.borderColor='#1a3050'; inp.placeholder='Ort suchen…'; }},2500);
        return;
      }}
      const lat = parseFloat(res[0].lat), lon = parseFloat(res[0].lon);
      const name = res[0].display_name.split(',')[0];
      const _m = window._nexusMap;
      if (_m) {{
        _m.setView([lat,lon], 10, {{animate:true}});
        L.popup({{offset:[0,-8]}})
          .setLatLng([lat,lon])
          .setContent('<div style="font-family:monospace;font-size:12px">' +
            '<b style="color:#3b82f6">\U0001f4cd ' + name + '</b><br>' +
            lat.toFixed(4) + ', ' + lon.toFixed(4) + '</div>')
          .openOn(_m);
      }}
      inp.value = '';
    }})
    .catch(function(){{
      inp.style.borderColor = '#ff4444';
      setTimeout(function(){{ inp.style.borderColor='#1a3050'; }},2000);
    }});
}}

</script>
</body>
</html>
"""


# ======================================================
# Builder-Funktionen
# ======================================================

def _credibility_badge(source: str) -> str:
    """Gibt HTML-Badge für Quellen-Glaubwürdigkeit zurück."""
    try:
        from nexus_credibility import score_source  # type: ignore
        s = score_source(source)
        score = s.get("score", 5)
        label = s.get("label", "Unbekannt")
        if score >= 8:
            color, bg = "#00ff88", "#002a10"
        elif score >= 6:
            color, bg = "#ffd700", "#2a2000"
        elif score >= 4:
            color, bg = "#ff8800", "#2a1000"
        else:
            color, bg = "#ff4444", "#2a0000"
        return (
            f'<span title="{label}" style="display:inline-block;padding:1px 5px;'
            f'background:{bg};border:1px solid {color};color:{color};'
            f'border-radius:3px;font-size:9px;margin-left:4px;cursor:help">'
            f'Glaubw. {score}/10</span>'
        )
    except Exception:
        pass
    return ""


def _build_news_html(articles: list) -> str:
    if not articles:
        return '<div class="ni"><div class="nb">Keine Artikel verfügbar.</div></div>'

    # ── Quellen-Übersicht oben ───────────────────────────────────────────────
    cred_header = ""
    try:
        from nexus_credibility import score_source  # type: ignore
        scores = [score_source(a.get("source","")).get("score",5) for a in articles[:12]]
        high = sum(1 for s in scores if s >= 8)
        mid  = sum(1 for s in scores if 5 <= s < 8)
        low  = sum(1 for s in scores if s < 5)
        avg  = round(sum(scores) / len(scores), 1) if scores else 5
        avg_color = "#00ff88" if avg >= 7 else ("#ffd700" if avg >= 5 else "#ff4444")
        cred_header = (
            f'<div style="background:#080d12;border:1px solid var(--border);'
            f'border-radius:4px;padding:6px 10px;margin-bottom:10px;font-size:11px;'
            f'display:flex;gap:16px;align-items:center">'
            f'<span style="color:var(--muted)">Ø Quellen-Glaubwürdigkeit:</span>'
            f'<b style="color:{avg_color}">{avg}/10</b>'
            f'<span style="color:#00ff88">■ {high} hoch</span>'
            f'<span style="color:#ffd700">■ {mid} mittel</span>'
            f'<span style="color:#ff4444">■ {low} niedrig</span>'
            f'</div>'
        )
    except Exception:
        pass

    html = [cred_header]
    for a in articles[:12]:
        age_min = a.get("age_min", 9999)
        age_lbl = f"{age_min}min" if age_min < 120 else f"{age_min//60}h"
        tag = '<span class="tag tnew">NEU</span>' if age_min < 30 else ""
        title   = (a.get("title","") or "").replace("<","&lt;")
        body    = (a.get("summary","") or "")[:180].replace("<","&lt;")
        url     = a.get("url","#")
        src     = a.get("source","")
        date    = a.get("date","")
        cred_badge = _credibility_badge(src)
        # Übersetzungs-Badge
        lang = a.get("lang", "")
        was_translated = a.get("translated", False)
        lang_badge = ""
        if was_translated:
            flag = {"ru": "🇷🇺", "ar": "🇸🇦", "en": "🇬🇧"}.get(lang, "🌐")
            lang_badge = (
                f'<span title="Automatisch übersetzt aus {lang.upper()}" '
                f'style="display:inline-block;padding:1px 4px;background:#001a30;'
                f'border:1px solid #00aaff;color:#00aaff;border-radius:3px;'
                f'font-size:9px;margin-left:3px;cursor:help">{flag} übersetzt</span>'
            )
        # Warnung bei sehr niedriger Glaubwürdigkeit (Score <= 3)
        low_cred_warn = ""
        try:
            from nexus_credibility import score_source  # type: ignore
            sc = score_source(src).get("score", 5)
            if sc <= 3:
                low_cred_warn = (
                    '<div style="background:#2a0000;border-left:2px solid #ff4444;'
                    'padding:2px 6px;margin-top:3px;font-size:10px;color:#ff6666">'
                    '⚠ Niedrige Quellen-Glaubwürdigkeit – Inhalt kritisch prüfen</div>'
                )
        except Exception:
            pass
        html.append(
            f'<div class="ni">'
            f'<div class="nt">{tag}<a href="{url}" target="_blank">{title}</a></div>'
            f'<div class="nm">{src}{cred_badge}{lang_badge} · {date} · vor {age_lbl}</div>'
            f'{low_cred_warn}'
            f'<div class="nb">{body}</div>'
            f'</div>'
        )
    return "\n".join(html)  # cred_header ist bereits erster Eintrag


def _build_flights_html(fd: Optional[dict]) -> tuple[str, str, str]:
    if not fd:
        html = (
            '<div style="padding:18px 12px;text-align:center;color:var(--muted)">'
            '<div style="font-size:24px;margin-bottom:6px">✈</div>'
            '<div style="font-size:12px;color:var(--text)">Keine Flugdaten in dieser Region</div>'
            '<div style="font-size:10px;margin-top:5px;line-height:1.6">'
            'Kein geografischer Bezug erkannt.<br>'
            'Für Flugdaten Anfrage mit Region stellen<br>'
            '<span style="color:var(--accent)">z.B. "Lage Iran", "Ukraine", "Taiwan-Strasse"</span>'
            '</div></div>'
        )
        return html, "nicht geo-relevant", "pb"
    if "error" in fd:
        return f'<div style="color:var(--red)">{fd["error"]}</div>', "Fehler", "pb"

    total   = fd.get("total",0)
    airborne= fd.get("airborne",0)
    no_cs   = fd.get("no_callsign",0)
    susp    = fd.get("suspicious",[])
    region  = fd.get("region","")
    ts      = fd.get("timestamp","")

    badge_cls = "pb pb-alert" if susp else "pb pb-ok"
    badge_txt = f"⚠ {len(susp)} auffällig" if susp else f"{airborne} in der Luft"

    lines = [
        f'<div style="background:#0a1620;border:1px solid var(--border);padding:8px;border-radius:4px;margin-bottom:10px">'
        f'<div style="color:var(--accent);font-size:10px;margin-bottom:5px">{region} · {ts}</div>'
        f'<div style="display:flex;gap:14px">'
        f'<span style="color:var(--muted);font-size:11px">Transponder: <b style="color:var(--text)">{total}</b></span>'
        f'<span style="color:var(--muted);font-size:11px">Luft: <b style="color:var(--text)">{airborne}</b></span>'
        f'<span style="color:var(--muted);font-size:11px">Kein CS: <b style="color:{"var(--orange)" if no_cs>3 else "var(--green)"}">{no_cs}</b></span>'
        f'</div></div>'
    ]

    if susp:
        lines.append('<div style="color:var(--orange);font-size:10px;margin-bottom:6px">⚠ AUFFÄLLIG:</div>')
        for a in susp[:8]:
            cs  = a.get("callsign","(kein)")
            org = a.get("origin","?")
            alt = f"{a.get('altitude_ft','?')}ft" if a.get("altitude_ft") else "?"
            spd = f"{a.get('velocity_kmh','')}km/h" if a.get("velocity_kmh") else ""
            hint= (a.get("suspicious","") or "")[:55]
            lines.append(
                f'<div class="ship-item">'
                f'<span style="color:var(--accent)">{cs}</span>'
                f'<span style="color:var(--muted);font-size:11px">{org} | {alt} {spd}</span>'
                f'<span class="ship-alert" style="font-size:10px">{hint}</span></div>'
            )
    else:
        lines.append('<div style="color:var(--green);padding:6px 0;font-size:12px">✅ Keine auffälligen Flugzeuge.</div>')
        for a in (fd.get("aircraft",[]) or [])[:8]:
            cs  = a.get("callsign","(kein)")
            org = (a.get("origin","?") or "?")[:18]
            alt = f"{a.get('altitude_ft','?')}ft" if a.get("altitude_ft") else "am Boden"
            lines.append(
                f'<div class="ship-item">'
                f'<span style="color:var(--accent)">{cs}</span>'
                f'<span style="color:var(--muted);font-size:11px">{org} | {alt}</span></div>'
            )

    return "\n".join(lines), badge_txt, badge_cls


def _build_weather_html(wd: Optional[dict]) -> tuple[str, str, str]:
    if not wd:
        html = (
            '<div style="padding:18px 12px;text-align:center;color:var(--muted)">'
            '<div style="font-size:24px;margin-bottom:6px">⛅</div>'
            '<div style="font-size:12px;color:var(--text)">Keine Wetterdaten verfügbar</div>'
            '<div style="font-size:10px;margin-top:5px;line-height:1.6">'
            'Kein geografischer Bezug erkannt.<br>'
            '<span style="color:var(--accent)">z.B. "Lage Naher Osten", "Ukraine"</span>'
            '</div></div>'
        )
        return html, "nicht geo-relevant", "pb"
    if "error" in wd:
        return f'<div style="color:var(--muted)">Wetterdaten: {wd["error"]}</div>', "Fehler", "pb"

    ops = wd.get("ops", {})
    overall = ops.get("overall","gruen")
    badge_cls = {"gruen":"pb pb-ok","gelb":"pb","rot":"pb pb-alert"}.get(overall,"pb")
    badge_txt = {"gruen":"✅ Günstig","gelb":"⚠ Eingeschränkt","rot":"⛔ Kritisch"}.get(overall,"?")

    temp    = wd.get("temperature_c","?")
    wind    = wd.get("wind_kmh","?")
    gusts   = wd.get("wind_gusts_kmh","?")
    vis     = wd.get("visibility_km")
    desc    = wd.get("weather_desc","?")
    loc     = wd.get("location","?")
    ts      = wd.get("timestamp","?")
    wdir    = wd.get("wind_direction","?")

    dust_block = ""
    if wd.get("dust_warning"):
        dust_block = '<div class="dust-warn">⛔ SANDSTURM-WARNUNG – Sichtweite + Wind: Staubereignis möglich.<br>Alle Außenoperationen eingestellt.</div>'

    vis_str = f"{vis}km" if vis is not None else "keine Daten"

    air   = ops.get("air_ops","?")
    naval = ops.get("naval_ops","?")
    gnd   = ops.get("ground_ops","?")

    def _ops_cls(s):
        if "⛔" in s: return "ops-bad"
        if "⚠" in s:  return "ops-warn"
        return "ops-ok"

    html = [
        f'<div style="color:var(--muted);font-size:10px;margin-bottom:8px">{loc} · {ts}</div>'
    ]

    # 4 Stat-Boxen
    html.append(
        f'<div class="weather-grid">'
        f'<div class="wstat"><span class="wstat-val">{temp}°C</span><span class="wstat-lbl">TEMPERATUR</span></div>'
        f'<div class="wstat"><span class="wstat-val">{wind}<span style="font-size:14px">km/h</span></span>'
        f'<span class="wstat-lbl">WIND {wdir} (Böen {gusts})</span></div>'
        f'<div class="wstat"><span class="wstat-val">{vis_str}</span><span class="wstat-lbl">SICHTWEITE</span></div>'
        f'<div class="wstat"><span class="wstat-val" style="font-size:15px">{desc}</span>'
        f'<span class="wstat-lbl">WETTER</span></div>'
        f'</div>'
    )

    # Sandsturm-Warnung
    if dust_block:
        html.append(dust_block)

    # Operative Lagebewertung
    html.append(
        f'<div style="margin-bottom:8px">'
        f'<div class="ops-item"><span style="color:var(--muted)">✈ Luftoperationen</span>'
        f'<span class="{_ops_cls(air)}">{air}</span></div>'
        f'<div class="ops-item"><span style="color:var(--muted)">🚢 Seeoperationen</span>'
        f'<span class="{_ops_cls(naval)}">{naval}</span></div>'
        f'<div class="ops-item"><span style="color:var(--muted)">🚶 Bodenoperationen</span>'
        f'<span class="{_ops_cls(gnd)}">{gnd}</span></div>'
        f'</div>'
        f'<div style="color:var(--muted);font-size:9px;padding:4px 0;border-top:1px solid var(--border)">'
        f'⚠ Wetterpausen ≠ Waffenstillstand</div>'
    )

    # 6h Forecast
    forecast = wd.get("forecast", [])
    if forecast:
        html.append('<div class="fc-row">')
        for fc in forecast[:6]:
            t_str = fc.get("time","?")
            fc_desc = fc.get("desc","?")
            fc_wind = fc.get("wind_kmh","?")
            fc_temp = fc.get("temp_c","?")
            html.append(
                f'<div class="fc-item">'
                f'<div class="fc-time">{t_str}</div>'
                f'<div class="fc-desc">{fc_desc}</div>'
                f'<div class="fc-wind">{fc_temp}°C | {fc_wind}km/h</div>'
                f'</div>'
            )
        html.append('</div>')

    return "\n".join(html), badge_txt, badge_cls


def _build_maritime_html(md: Optional[dict], anomalies: Optional[dict] = None) -> tuple[str, str, str]:
    if not md:
        html = (
            '<div style="padding:14px 12px;text-align:center;color:var(--muted)">'
            '<div style="font-size:22px;margin-bottom:6px">🚢</div>'
            '<div style="font-size:12px;color:var(--text)">Keine maritimen Daten in dieser Anfrage</div>'
            '<div style="font-size:10px;margin-top:5px;line-height:1.6">'
            'Kein Schiffs- oder Meerengen-Kontext erkannt.<br>'
            '<span style="color:var(--accent)">z.B. "Suez", "Hormuz", "Rotes Meer", "Houthi"</span>'
            '</div></div>'
        )
        return html, "nicht relevant", "pb"
    if "error" in md:
        return f'<div style="color:var(--red)">{md["error"]}</div>', "Fehler", "pb"

    region     = md.get("region","")
    desc       = md.get("description","")
    alert_cnt  = md.get("alert_count", 0)
    alerts     = md.get("alerts", [])
    news       = md.get("news", [])
    ts         = md.get("timestamp","")

    # Anomalie-Alerts einrechnen
    anom_critical = (anomalies or {}).get("critical", 0)
    anom_warnings = (anomalies or {}).get("warnings", 0)
    total_alerts  = alert_cnt + anom_critical + anom_warnings

    if anom_critical > 0:
        badge_cls = "pb pb-alert"
        badge_txt = f"🔴 {anom_critical} ANOMALIE"
    elif total_alerts > 0:
        badge_cls = "pb pb-alert"
        badge_txt = f"⚠ {total_alerts} Alert"
    else:
        badge_cls = "pb pb-ok"
        badge_txt = "✅ Ruhig"

    lines = [
        f'<div style="background:#0a1620;border:1px solid var(--border);padding:8px;'
        f'border-radius:4px;margin-bottom:10px">'
        f'<div style="color:var(--accent);font-size:10px;margin-bottom:3px">{region} · {ts}</div>'
        f'<div style="color:var(--muted);font-size:11px">{desc}</div>'
        f'</div>'
    ]

    if alerts:
        lines.append(f'<div style="color:var(--orange);font-size:10px;margin-bottom:6px">⚠ ALARM-MELDUNGEN ({len(alerts)}):</div>')
        for a in alerts[:5]:
            title   = (a.get("title","") or "").replace("<","&lt;")
            summary = (a.get("summary","") or "")[:180].replace("<","&lt;")
            url     = a.get("url","#")
            src     = a.get("source","")
            date    = a.get("date","")
            lines.append(
                f'<div class="ship-item ship-alert">'
                f'<div>'
                f'<div><a href="{url}" target="_blank" style="color:var(--orange)">{title}</a></div>'
                f'<div style="font-size:10px;color:var(--muted)">{src} · {date}</div>'
                f'<div style="font-size:11px;color:var(--text)">{summary}</div>'
                f'</div></div>'
            )
    elif news:
        lines.append(f'<div style="color:var(--green);font-size:10px;margin-bottom:6px">✅ Aktuelle Meldungen ({len(news)}):</div>')
        for a in news[:5]:
            title   = (a.get("title","") or "").replace("<","&lt;")
            url     = a.get("url","#")
            src     = a.get("source","")
            date    = a.get("date","")
            lines.append(
                f'<div class="ship-item">'
                f'<div>'
                f'<a href="{url}" target="_blank">{title}</a>'
                f'<div style="font-size:10px;color:var(--muted)">{src} · {date}</div>'
                f'</div></div>'
            )
    else:
        lines.append('<div style="color:var(--muted);padding:6px 0">Keine aktuellen Meldungen.</div>')

    # ── Chokepoint-Status Block ───────────────────────────────────────────────
    if anomalies and anomalies.get("chokepoints"):
        active_choke = [c for c in anomalies["chokepoints"]
                        if c["alert_level"] in ("critical", "warning") or c["ships_count"] > 0]
        if active_choke:
            lines.append('<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px">')
            lines.append('<div style="color:var(--accent);font-size:10px;margin-bottom:5px">⚓ NADELÖHR-MONITOR</div>')
            for cp in active_choke[:6]:
                col = {"critical": "var(--red)", "warning": "var(--orange)", "ok": "var(--green)"}.get(
                    cp["alert_level"], "var(--muted)")
                lines.append(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'padding:3px 0;border-bottom:1px solid var(--border);font-size:11px">'
                    f'<span style="color:var(--text)">{cp["icon"]} {cp["short"]}</span>'
                    f'<span style="color:{col}">{cp["status"]}</span>'
                    f'</div>'
                )
            lines.append('</div>')

    # ── STS + Stop Anomalie-Alerts ────────────────────────────────────────────
    sts_list  = (anomalies or {}).get("sts_alerts", [])
    stop_list = (anomalies or {}).get("stop_alerts", [])
    if sts_list or stop_list:
        lines.append('<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px">')
        lines.append('<div style="color:var(--red);font-size:10px;margin-bottom:5px">🚨 MARITIME ANOMALIEN</div>')
        for a in (sts_list + stop_list)[:5]:
            col = "var(--red)" if a.get("severity") == "critical" else "var(--orange)"
            lines.append(
                f'<div style="background:#0d0010;border-left:3px solid {col};'
                f'padding:5px 8px;margin-bottom:5px;border-radius:2px">'
                f'<div style="color:{col};font-size:11px;font-weight:bold">{a.get("title","?")}</div>'
                f'<div style="color:var(--muted);font-size:10px;margin-top:2px">{a.get("summary","")[:160]}</div>'
                f'</div>'
            )
        lines.append('</div>')

    return "\n".join(lines), badge_txt, badge_cls


def _geocode_for_map(query: str) -> Optional[tuple[float, float]]:
    """Schnelles Geocoding für Kartenmittelpunkt. Gibt (lat, lon) oder None zurück."""
    import requests as _req
    try:
        r = _req.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": query, "count": 1, "language": "de", "format": "json"},
                     timeout=5)
        results = r.json().get("results", [])
        if results:
            return results[0]["latitude"], results[0]["longitude"]
    except Exception:
        pass
    return None


def _extract_incident_markers(articles: list,
                               center_lat: float = 0.0,
                               center_lon: float = 0.0) -> list[dict]:
    """
    Extrahiert Vorfalls-Marker aus Nachrichtenartikeln.
    Gibt Liste von {lat, lon, title, source, type} zurück.
    NUR Artikel mit echten Vorfall-Schlüsselwörtern werden geocodiert.

    center_lat/center_lon: Karten-Zentrum für Plausibilitätsprüfung.
    Marker weiter als MAX_DIST_KM vom Zentrum entfernt werden verworfen.
    """
    import requests as _req
    import re
    import math

    # ── Spezifische Konflikt-Keywords (keine generischen Begriffe) ───────────
    # Absichtlich NICHT enthalten: "drone", "fire", "military", "troops"
    # (zu generisch → Reddit-Rauschen "Can I donate a drone?" etc.)
    _INCIDENT_KEYWORDS = {
        # Deutsch – nur konkrete Ereignisse
        "angriff", "explosion", "rakete", "beschuss", "bomben", "bombardier",
        "absturz", "crash", "katastrophe", "erdbeben", "flut", "überschwemmung",
        "schuss", "schüsse", "gefecht", "kampf", "evakuier",
        "notstand", "sirene", "einschlag", "luftangriff", "luftschlag",
        "offensive", "invasion", "todesopfer", "tote", "verletzt",
        "brand ausgebrochen", "detonation",
        # Englisch – nur konkrete Ereignisse
        "attack", "explosion", "missile", "bomb", "blast",
        "crash", "earthquake", "flood", "tsunami",
        "shooting", "gunfire", "airstrike", "shelling",
        "offensive", "invasion", "battle", "casualties",
        "killed", "injured", "wounded", "detonation",
        "nuclear strike", "chemical attack", "hostage",
        "rocket fire", "drone strike", "air raid",
    }

    # ── Rauschen-Filter: Fragen und persönliche Posts überspringen ───────────
    # Reddit-Titel wie "Can I donate a drone?" oder "Is it safe to travel?"
    _NOISE_PREFIXES = (
        "can ", "is ", "does ", "how ", "would ", "what ", "are ", "will ",
        "should ", "why ", "which ", "who ", "when ", "could ", "do ",
        "has ", "have ", "was ", "were ", "did ", "any ", "anyone ",
        "help ", "advice ", "question ", "asking ", "opinion ",
        "looking for ", "need ", "want ", "update:", "weekly ",
        "daily ", "megathread", "[discussion]", "[question]",
    )

    def _is_noise(title: str) -> bool:
        t = title.lower().strip()
        # Fragen (enden mit ?)
        if t.endswith("?"):
            return True
        # Bekannte Rauschen-Präfixe
        if any(t.startswith(p) for p in _NOISE_PREFIXES):
            return True
        # Sehr kurze Titel ohne geografischen Kontext
        if len(t.split()) < 4:
            return True
        return False

    # ── Koordinaten-Plausibilitätsprüfung ────────────────────────────────────
    # Marker weiter als MAX_DIST_KM vom Karten-Zentrum entfernt = verwerfen
    MAX_DIST_KM = 3500  # ~halber Erdumfang/6 → deckt gesamte Region ab

    def _great_circle_km(lat1: float, lon1: float,
                         lat2: float, lon2: float) -> float:
        R = 6371.0
        dl = math.radians(lat2 - lat1)
        dln = math.radians(lon2 - lon1)
        a = (math.sin(dl / 2) ** 2
             + math.cos(math.radians(lat1))
             * math.cos(math.radians(lat2))
             * math.sin(dln / 2) ** 2)
        return R * 2 * math.asin(min(1.0, math.sqrt(a)))

    has_center = (center_lat != 0.0 or center_lon != 0.0)

    incidents = []
    seen: set = set()

    for a in (articles or [])[:20]:
        title = (a.get("title") or "").strip()
        if not title or title in seen:
            continue

        # ── Rauschen-Filter ──────────────────────────────────────────────────
        if _is_noise(title):
            continue

        # ── Keyword-Filter: nur echte Vorfälle ──────────────────────────────
        title_lower = title.lower()
        if not any(kw in title_lower for kw in _INCIDENT_KEYWORDS):
            continue  # Politischer/kultureller Artikel → kein Marker

        # Ort aus Titel extrahieren (einfaches Muster: "in <Ort>", "<Ort>:")
        candidates = []
        # "in Sinzig", "in Kiew", "in Gaza" etc.
        for m in re.finditer(r'\bin\s+([A-ZÄÖÜ][a-zA-ZäöüÄÖÜ\-]{2,20})', title):
            candidates.append(m.group(1))
        # "Sinzig:" oder "Kiew –" am Anfang
        m2 = re.match(r'^([A-ZÄÖÜ][a-zA-ZäöüÄÖÜ\-]{2,20})[\s:–-]', title)
        if m2:
            candidates.insert(0, m2.group(1))

        for candidate in candidates[:2]:
            if candidate.lower() in seen:
                continue
            # Einzel-Buchstaben und bekannte Nicht-Orte überspringen
            if len(candidate) < 3:
                continue
            try:
                r = _req.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": candidate, "count": 1,
                                     "language": "de", "format": "json"},
                             timeout=4)
                results = r.json().get("results", [])
                if results:
                    res = results[0]
                    m_lat = float(res["latitude"])
                    m_lon = float(res["longitude"])

                    # ── Koordinaten-Plausibilitätsprüfung ───────────────────
                    # (0,0) = Fehler, außerhalb der Region = verwerfen
                    if m_lat == 0.0 and m_lon == 0.0:
                        continue
                    if has_center:
                        dist = _great_circle_km(center_lat, center_lon,
                                                m_lat, m_lon)
                        if dist > MAX_DIST_KM:
                            continue  # Zu weit weg → nicht plausibel

                    seen.add(title)
                    seen.add(candidate.lower())
                    incidents.append({
                        "lat":    m_lat,
                        "lon":    m_lon,
                        "title":  title[:80],
                        "source": a.get("source", ""),
                        "url":    a.get("url", "#"),
                        "place":  candidate,
                    })
                    break
            except Exception:
                continue

    return incidents


def _build_map_data(fd: Optional[dict], md: Optional[dict],
                   center_lat: float = 25.0, center_lon: float = 50.0,
                   zoom: int = 4, query: str = "",
                   articles: Optional[list] = None) -> tuple:
    """Erstellt JSON-Daten für die Leaflet-Karte. Gibt (json_str, maritime_anomalies) zurück."""
    aircraft = []
    if fd and "aircraft" in fd:
        for a in (fd.get("aircraft", []) or []):
            if a.get("lat") and a.get("lon"):
                aircraft.append({
                    "lat":         a.get("lat"),
                    "lon":         a.get("lon"),
                    "callsign":    a.get("callsign", "(kein)"),
                    "origin":      a.get("origin", "?"),
                    "altitude_ft": a.get("altitude_ft"),
                    "velocity_kmh":a.get("velocity_kmh"),
                    "track":       a.get("track"),
                    "suspicious":  a.get("suspicious", ""),
                    "helicopter":  a.get("helicopter", False),
                })
        # Karten-Zentrum aus Flugdaten-Region –
        # NUR als Fallback wenn kein expliziter Stadtfokus übergeben wurde
        _has_explicit_center = (center_lat != 25.0 or center_lon != 50.0)
        if fd.get("center_lat") and fd.get("center_lon") and not _has_explicit_center:
            center_lat = fd["center_lat"]
            center_lon = fd["center_lon"]

    # Geocoding-Fallback: nur wenn noch keine Koordinaten gesetzt
    if query and center_lat == 25.0 and center_lon == 50.0:
        geo = _geocode_for_map(query)
        if geo:
            center_lat, center_lon = geo
            if zoom == 4:          # Zoom nur setzen wenn nicht schon explizit gesetzt
                zoom = 7

    # Vorfalls-Marker aus Nachrichten extrahieren
    # center_lat/center_lon als Plausibilitätshilfe übergeben
    incident_markers = []
    if articles:
        try:
            incident_markers = _extract_incident_markers(
                articles,
                center_lat=center_lat,
                center_lon=center_lon,
            )
        except Exception:
            pass

    maritime_points = []
    try:
        from nexus_maritime import MARITIME_REGIONS  # type: ignore
        for rname, rdata in MARITIME_REGIONS.items():
            lat, lon = rdata["center"]
            maritime_points.append({
                "lat":       lat,
                "lon":       lon,
                "name":      rname,
                "desc":      rdata.get("desc", ""),
                "alerts":    0,
                "alert_titles": [],   # wird unten befüllt
            })
        # Echte Alert-Daten aus md einbinden
        if md and "region" in md:
            alert_titles = [
                a.get("title", "")[:80] for a in (md.get("alerts") or [])[:5]
            ]
            for pt in maritime_points:
                if pt["name"] == md["region"]:
                    pt["alerts"]       = md.get("alert_count", 0)
                    pt["alert_titles"] = alert_titles
    except Exception:
        pass

    # GDELT Geo-Events
    gdelt_points = []
    if query:
        try:
            from nexus_gdelt import fetch_gdelt_geo_events  # type: ignore
            gdelt_points = fetch_gdelt_geo_events(query, hours=24, max_points=30)
        except Exception:
            pass

    # AIS Schiffspositionen + Maritime Anomalie-Detektion
    ais_vessels = []
    maritime_anomalies: dict = {}
    if query:
        try:
            from nexus_ais import get_vessels, vessels_for_map  # type: ignore
            _ais_raw = get_vessels(query)
            ais_vessels = vessels_for_map(query)
            # Anomalie-Detektion auf Rohdaten
            try:
                from nexus_maritime_anomaly import analyse_vessels  # type: ignore
                maritime_anomalies = analyse_vessels(_ais_raw.get("vessels", []))
            except Exception:
                pass
        except Exception:
            try:
                from nexus_ais import vessels_for_map  # type: ignore
                ais_vessels = vessels_for_map(query)
            except Exception:
                pass

    # Erdbeben (USGS)
    earthquakes = []
    if query:
        try:
            from nexus_seismic import earthquakes_for_map  # type: ignore
            earthquakes = earthquakes_for_map(query, hours=48)
        except Exception:
            pass

    # NOTAMs
    notams = []
    if query:
        try:
            from nexus_notam import notams_for_map  # type: ignore
            notams = notams_for_map(query)
        except Exception:
            pass

    # NASA FIRMS Brände
    fire_points = []
    if query:
        try:
            from nexus_firms import fires_for_map  # type: ignore
            fire_points = fires_for_map(query)
        except Exception:
            pass

    # NASA EONET Naturereignisse
    eonet_points = []
    if query:
        try:
            from nexus_eonet import eonet_for_map  # type: ignore
            eonet_points = eonet_for_map(query)
        except Exception:
            pass

    # Artillerie-Signal (Blitzortung / Wetter-Kontext)
    lightning_signals: list[dict] = []
    if query:
        try:
            from nexus_lightning import lightning_for_map  # type: ignore
            lightning_signals = lightning_for_map(query)
        except Exception:
            pass

    # ── Korrelations-Engine ───────────────────────────────────────────────────
    correlations = []
    try:
        from nexus_correlate import correlate_events  # type: ignore
        # Nur Artikel mit Geo-Koordinaten

        geo_articles = [a for a in (articles or []) if a.get("lat") and a.get("lon")]
        susp_aircraft = [a for a in aircraft if a.get("suspicious")]
        correlations = correlate_events(
            articles    = geo_articles,
            aircraft    = susp_aircraft,
            earthquakes = [e for e in earthquakes if e.get("osint_hint")],
            gdelt       = gdelt_points,
            incidents   = incident_markers,
        )
    except Exception:
        pass


# ── ACLED-Punkte aus Artikeln ─────────────────────────────────────────────────
    acled_points: list[dict] = []
    for a in (articles or []):
        if str(a.get("source", "")).startswith("ACLED") and a.get("lat") and a.get("lon"):
            acled_points.append({
                "lat":      a["lat"],
                "lon":      a["lon"],
                "title":    (a.get("title") or "")[:100],
                "event_type": a.get("event_type", ""),
                "priority": a.get("priority", "MITTEL"),
                "icon":     a.get("icon", "⚡"),
                "color":    {"KRITISCH": "#ff0000", "HOCH": "#ff6600",
                             "MITTEL": "#ffaa00", "NIEDRIG": "#00cc88"}.get(
                                 a.get("priority", "MITTEL"), "#ff6600"),
                "popup":    (a.get("title") or "")[:150],
            })

    # GPS-Jam Zonen
    gpsjam_zones: list[dict] = []
    if query:
        try:
            from nexus_gpsjam import gpsjam_for_map  # type: ignore
            gpsjam_zones = gpsjam_for_map(query)
        except Exception:
            pass

    # Schiffs-Tiefgang Alerts (aus maritime_data falls übergeben)
    draught_alerts: list[dict] = []
    if md and md.get("draught_alerts"):
        for da in (md.get("draught_alerts") or [])[:20]:
            if da.get("lat") and da.get("lon"):
                draught_alerts.append(da)

    class _SetEncoder(json.JSONEncoder):
        """Konvertiert Python-sets zu lists fuer JSON-Serialisierung."""
        def default(self, obj):
            if isinstance(obj, set):
                return sorted(obj)
            return super().default(obj)

    map_data_json = json.dumps({
        "center_lat":        center_lat,
        "center_lon":        center_lon,
        "zoom":              zoom,
        "aircraft":          aircraft,
        "maritime_points":   maritime_points,
        "gdelt_points":      gdelt_points,
        "ais_vessels":       ais_vessels,
        "earthquakes":       earthquakes,
        "notams":            notams,
        "fires":             fire_points,
        "eonet_points":      eonet_points,
        "incident_markers":  incident_markers,
        "correlations":      correlations,
        "lightning_signals": lightning_signals,
        "acled_points":      acled_points,
        "gpsjam_zones":        gpsjam_zones,
        "draught_alerts":      draught_alerts,
        "sts_alerts":          maritime_anomalies.get("sts_alerts", []),
        "stop_alerts":         maritime_anomalies.get("stop_alerts", []),
        "chokepoint_status":   maritime_anomalies.get("chokepoints", []),
        "humint_markers":      [],   # wird live nachgeladen
        "fusion_threats":      [],   # wird live nachgeladen
    }, ensure_ascii=False, cls=_SetEncoder)
    return map_data_json, maritime_anomalies


# ======================================================
# Haupt-Exportfunktion
# ======================================================


def generate_report(
    topic: str = "",
    analysis_text: str = "",
    articles = None,
    flight_data = None,
    weather_data = None,
    maritime_data = None,
    auto_open: bool = True,
    save_dir = None,
    query: str = "",
    map_center = None,
    map_zoom: int = 5,
    city_focus = None,
    escalation_data = None,
) -> str:
    """Erstellt den kompletten HTML-Lagebild-Report und speichert ihn."""
    ts      = datetime.now().strftime("%d.%m.%Y %H:%M")
    ts_utc  = datetime.utcnow().strftime("%H:%M")
    ts_fn   = datetime.now().strftime("%Y%m%d_%H%M")
    topic_s = (topic or query or "lage").lower().replace(" ", "_")[:30]

    # Karten-Zentrum bestimmen
    if map_center and len(map_center) >= 2:
        center_lat, center_lon = float(map_center[0]), float(map_center[1])
    elif city_focus:
        center_lat = city_focus.get("lat", 25.0)
        center_lon = city_focus.get("lon", 50.0)
        map_zoom   = city_focus.get("zoom", map_zoom)
    else:
        center_lat, center_lon = 25.0, 50.0

    # Karten-JSON bauen
    map_data, maritime_anomalies = _build_map_data(
        flight_data, maritime_data,
        center_lat=center_lat, center_lon=center_lon,
        zoom=map_zoom, query=query or topic,
        articles=articles,
    )

    # Panel-HTML
    flights_html, flights_badge, flights_badge_class   = _build_flights_html(flight_data)
    weather_html, weather_badge, weather_badge_class   = _build_weather_html(weather_data)
    maritime_html, maritime_badge, maritime_badge_class = _build_maritime_html(maritime_data, maritime_anomalies)
    news_html  = _build_news_html(articles or [])
    news_count = str(len(articles or []))
    query_disp = (query or topic or "Global").title()

    # Status-Items
    total_ac    = (flight_data or {}).get("total", 0)
    susp_ac     = len((flight_data or {}).get("suspicious", []))
    ship_alerts = (maritime_data or {}).get("alert_count", 0)
    sc_ac = "dr" if susp_ac else "dg"
    sc_sh = "dr" if ship_alerts else "dg"
    wdesc = (weather_data or {}).get("weather_desc", "-")
    status_items = (
        f'<div class="si"><span class="dot {sc_ac}"></span>'
        f'✈ {total_ac} Flugzeuge ({susp_ac} auffaellig)</div>'
        f'<div class="si"><span class="dot {sc_sh}"></span>'
        f'⚓ Maritime: {ship_alerts} Alarme</div>'
        f'<div class="si"><span class="dot dm"></span>'
        f'⛅ {wdesc}</div>'
    )

    events_panel_title = "⚓ MARITIME LAGE + EREIGNISSE"

    import base64 as _b64
    map_data_b64      = _b64.b64encode(map_data.encode()).decode()
    known_titles      = [a.get("title","")[:80] for a in (articles or []) if a.get("title")]
    known_titles_json = json.dumps(known_titles, ensure_ascii=False)
    live_query        = (query or topic or "").replace("'", "\\'")

    # LLM-Analyse HTML
    if analysis_text:
        paras = [p.strip() for p in analysis_text.strip().split("\n") if p.strip()]
        analysis_html = "".join(
            f'<p style="margin:0 0 8px">{p}</p>' for p in paras
        )
    else:
        analysis_html = (
            '<span style="color:var(--muted)">Keine KI-Analyse vorhanden. '
            'Starte NEXUS mit "l Ukraine" fuer eine vollstaendige Analyse.</span>'
        )

    # Eskalations-Box
    llm_box_html = ""
    if escalation_data:
        try:
            score = escalation_data.get("score", 0)
            label = escalation_data.get("label", "")
            trend = escalation_data.get("trend_symbol", "")
            col   = "#ff4444" if score >= 70 else "#ffaa00" if score >= 40 else "#00ff88"
            llm_box_html = (
                f'<div style="background:rgba(0,0,0,.4);border:1px solid {col};'
                f'border-radius:6px;padding:10px 14px;margin-bottom:8px">'
                f'<span style="color:{col};font-size:13px;font-weight:bold">'
                f'⚡ Eskalations-Score: {score}/100 {trend} {label}'
                f'</span></div>'
            )
        except Exception:
            pass

    # HTML zusammenbauen
    html_out = _HTML_TEMPLATE.format(
        timestamp            = ts,
        timestamp_utc        = ts_utc,
        status_items         = status_items,
        query_display        = query_disp,
        flights_badge        = flights_badge,
        flights_badge_class  = flights_badge_class,
        flights_html         = flights_html,
        weather_badge        = weather_badge,
        weather_badge_class  = weather_badge_class,
        weather_html         = weather_html,
        analysis_html        = analysis_html,
        llm_box_html         = llm_box_html,
        events_panel_title   = events_panel_title,
        maritime_badge       = maritime_badge,
        maritime_badge_class = maritime_badge_class,
        maritime_html        = maritime_html,
        news_count           = news_count,
        news_html            = news_html,
        map_data_b64         = map_data_b64,
        known_titles_json    = known_titles_json,
        live_query           = live_query,
    )

    # Datei speichern
    if save_dir:
        report_dir = Path(save_dir)
    else:
        report_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    report_dir.mkdir(parents=True, exist_ok=True)

    fname = f"nexus_report_{topic_s}_{ts_fn}.html"
    fpath = str(report_dir / fname)

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html_out)

    if auto_open:
        try:
            webbrowser.open(f"file:///{fpath.replace(os.sep, '/')}")
        except Exception:
            pass

    return fpath
