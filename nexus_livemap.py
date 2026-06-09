"""
NEXUS – Persistentes Lagebild  (Ebene 4 / Live-Karte)
======================================================
Standalone HTML-Seite die sich selbst alle N Sekunden aktualisiert.
Kein L-Befehl nötig – bleibt im Browser offen.

Filter-Panel (collapsible Gruppen):
  FUSION       – Multi-Signal Threat-Assessments
  HUMINT       – Taktische Feldmeldungen, Vision-KI, Geo-Lokalisierung
  BEWEGUNG     – Konvois, Traffic-Anomalien, Webcam-Alerts
  FEUER/ANGRIFF– FIRMS-Brände, ACLED, GDELT, Blitz/Artillerie
  SENSOR / ISR – Flugzeuge, SAR, WebSDR, GPS-Jam, Seismik, NOTAMs
  MARITIME     – AIS-Schiffe, Ghost-Vessels, Tiefgang-Delta
  UMWELT/NUKLR – Strahlung (EPA+IAEA+EURDEP), EONET-Naturereignisse
  INFO-NETZ    – Netgraph-Surge, Astroturfing-Alerts

Öffentliche API:
  build_livemap_html(port)  -> str (komplettes HTML-Dokument)
"""

from __future__ import annotations


def build_livemap_html(port: int = 11430) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#00d4ff">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="NEXUS">
<title>NEXUS LAGEBILD</title>
<link rel="manifest" href="/nexus_manifest.json">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://leaflet.github.io/Leaflet.heat/dist/leaflet-heat.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#060b10;color:#c8d6e0;font-family:'Courier New',monospace;
     font-size:11px;overflow:hidden;height:100vh;display:flex;flex-direction:column}}
#header{{background:linear-gradient(90deg,#04111e,#071a2e);border-bottom:2px solid #00d4ff;
        padding:6px 14px;display:flex;align-items:center;gap:10px;flex-shrink:0;
        flex-wrap:wrap;z-index:1100}}
.logo{{color:#00d4ff;font-size:14px;font-weight:bold;letter-spacing:4px;white-space:nowrap}}
#esc-bar{{height:20px;flex:0 0 150px;border:1px solid #1e3a4a;border-radius:2px;
         overflow:hidden;position:relative}}
#esc-fill{{height:100%;background:#00ff88;transition:width 0.8s,background 0.8s}}
#esc-label{{position:absolute;inset:0;display:flex;align-items:center;
           justify-content:center;font-size:10px;color:#000;font-weight:bold}}
#hdr-counts{{display:flex;gap:8px;flex-wrap:wrap;font-size:10px;color:#4a8090}}
#hdr-counts span{{color:#aac8d8}}
.hdr-btn{{background:#071a2e;border:1px solid #1e3a4a;color:#00d4ff;padding:3px 8px;
         cursor:pointer;font-family:inherit;font-size:10px;border-radius:2px;white-space:nowrap}}
.hdr-btn:hover{{border-color:#00d4ff;background:#0a2035}}
#status-line{{margin-left:auto;font-size:10px;color:#4a6070;white-space:nowrap}}
#countdown{{color:#00ff88}}
#region-bar{{background:#04111e;border-bottom:1px solid #0d2035;padding:4px 14px;
            display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0;z-index:1100}}
.reg-btn{{background:#071a2e;border:1px solid #0d2035;color:#8ab0c8;padding:2px 8px;
         cursor:pointer;font-family:inherit;font-size:10px;border-radius:2px}}
.reg-btn:hover,.reg-btn.active{{border-color:#00d4ff;color:#00d4ff}}
#main{{flex:1;display:flex;overflow:hidden;position:relative}}
#map{{flex:1;z-index:100}}
#sidebar{{width:200px;flex-shrink:0;background:#060b10;border-left:1px solid #0d2035;
         overflow-y:auto;z-index:1000;display:flex;flex-direction:column}}
.filter-group{{border-bottom:1px solid #0d2035}}
.fg-hdr{{display:flex;align-items:center;justify-content:space-between;
        padding:6px 10px;cursor:pointer;user-select:none;background:#071a2e}}
.fg-hdr:hover{{background:#0a2035}}
.fg-title{{font-size:10px;font-weight:bold;letter-spacing:1px}}
.fg-arrow{{color:#4a6070;font-size:10px}}
.fg-body{{padding:4px 8px 6px}}
.fl-row{{display:flex;align-items:center;justify-content:space-between;
        padding:2px 2px;margin-bottom:1px}}
.fl-lbl{{font-size:10px;color:#8ab0c8;cursor:pointer;flex:1}}
.fl-cnt{{font-size:9px;color:#4a6070;margin-right:4px;min-width:18px;text-align:right}}
.sw{{width:26px;height:13px;background:#1e3a4a;border-radius:7px;cursor:pointer;
    position:relative;flex-shrink:0;transition:background 0.2s}}
.sw.on{{background:#006688}}
.sw::after{{content:'';position:absolute;left:2px;top:2px;width:9px;height:9px;
           border-radius:50%;background:#4a8090;transition:left 0.2s,background 0.2s}}
.sw.on::after{{left:15px;background:#00d4ff}}
.all-toggle{{font-size:9px;color:#4a6070;cursor:pointer;padding:2px 2px;text-align:right}}
.all-toggle:hover{{color:#00d4ff}}
.leaflet-container{{background:#060b10}}
.leaflet-tile-pane{{filter:brightness(0.85) saturate(0.7)}}
.leaflet-popup-content-wrapper{{background:#071a2e;color:#c8d6e0;border:1px solid #1e3a4a;
  border-radius:3px;font-size:11px;font-family:'Courier New',monospace;max-width:300px}}
.leaflet-popup-tip{{background:#071a2e}}
.leaflet-popup-content{{margin:8px 12px;line-height:1.5}}
.ptitle{{color:#00d4ff;font-weight:bold;font-size:12px;margin-bottom:4px}}
.pbadge{{display:inline-block;padding:1px 5px;border-radius:2px;font-size:9px;
        font-weight:bold;color:#000;margin-right:4px}}
.prow{{display:flex;gap:6px;margin-top:2px;font-size:10px}}
.pkey{{color:#4a8090}} .pval{{color:#c8d6e0}}
.plink{{color:#00d4ff;text-decoration:none;font-size:10px}}
.plink:hover{{text-decoration:underline}}
#ng-box{{font-size:10px;color:#8ab0c8;padding:4px 2px;line-height:1.5}}
@keyframes pulse{{0%,100%{{transform:scale(1);opacity:1}}50%{{transform:scale(1.6);opacity:0.6}}}}
@keyframes pfst{{0%,100%{{transform:scale(1);opacity:1}}50%{{transform:scale(2.0);opacity:0.3}}}}
.pulse{{animation:pulse 2s infinite}} .pfst{{animation:pfst 1s infinite}}
#sidebar::-webkit-scrollbar{{width:4px}}
#sidebar::-webkit-scrollbar-track{{background:#04111e}}
#sidebar::-webkit-scrollbar-thumb{{background:#1e3a4a}}

/* ── MOBILE RESPONSIVE (T94) ─────────────────────────────────── */
#mob-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.65);
             z-index:1050;backdrop-filter:blur(3px)}}
#mob-overlay.active{{display:block}}
#bottom-nav{{display:none}}

@media(max-width:768px){{
  body{{font-size:13px;-webkit-tap-highlight-color:transparent}}

  /* Header kompakt */
  #header{{padding:8px 12px;gap:8px;flex-wrap:nowrap;overflow:hidden}}
  .logo{{font-size:13px;letter-spacing:2px}}
  #esc-bar{{flex:0 0 120px;height:22px}}
  #esc-label{{font-size:9px}}
  #hdr-counts{{display:none}}
  #status-line{{display:none}}
  .hdr-btn{{display:none !important}}
  #btn-mob-refresh{{display:flex !important;min-height:36px;padding:4px 10px;
                   align-items:center;justify-content:center}}
  #btn-mob-filter{{display:flex !important;min-height:36px;padding:4px 10px;
                  align-items:center;justify-content:center}}

  /* Region-Bar horizontal scroll */
  #region-bar{{overflow-x:auto;flex-wrap:nowrap;gap:4px;padding:6px 10px;
              scrollbar-width:none;-ms-overflow-style:none}}
  #region-bar::-webkit-scrollbar{{display:none}}
  .reg-btn{{padding:6px 14px;font-size:12px;white-space:nowrap;border-radius:14px}}
  .reg-btn.active{{background:#071a2e}}

  /* Karte voller Bildschirm */
  #main{{flex:1;display:flex;overflow:hidden}}
  #map{{flex:1}}

  /* Sidebar → Bottom Drawer */
  #sidebar{{
    position:fixed !important;
    bottom:56px;left:0;right:0;top:auto !important;
    width:100% !important;
    height:72vh;
    border-left:none !important;
    border-top:2px solid #00d4ff;
    border-radius:16px 16px 0 0;
    z-index:1100;
    transform:translateY(105%);
    transition:transform 0.3s cubic-bezier(0.4,0,0.2,1);
    padding-bottom:12px;
  }}
  #sidebar.mob-open{{transform:translateY(0)}}
  #drawer-handle{{
    display:block;
    width:40px;height:4px;
    background:#1e3a4a;border-radius:2px;
    margin:10px auto 6px;cursor:pointer;flex-shrink:0;
  }}

  /* Filter-Gruppen: größere Touch-Targets */
  .fg-hdr{{padding:12px 14px}}
  .fg-title{{font-size:12px}}
  .fl-row{{padding:6px 4px}}
  .fl-lbl{{font-size:12px}}
  .fl-cnt{{font-size:11px;min-width:22px}}
  .sw{{width:36px;height:20px}}
  .sw::after{{width:14px;height:14px;top:3px;left:3px}}
  .sw.on::after{{left:19px}}
  .all-toggle{{font-size:11px;padding:6px 4px}}

  /* Leaflet Zoom hoch schieben */
  .leaflet-control-zoom{{margin-bottom:68px !important}}
  .leaflet-popup-content-wrapper{{max-width:85vw}}

  /* Bottom Nav */
  #bottom-nav{{
    display:flex;
    position:fixed;bottom:0;left:0;right:0;
    height:56px;
    background:#04111e;
    border-top:1px solid #0d2035;
    z-index:1200;
    justify-content:space-around;
    align-items:stretch;
    padding-bottom:env(safe-area-inset-bottom,0);
  }}
  .bnav-btn{{
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:2px;flex:1;
    background:none;border:none;color:#4a6070;cursor:pointer;
    font-family:'Courier New',monospace;
    -webkit-tap-highlight-color:transparent;
    transition:color 0.15s;
    padding:6px 0;
  }}
  .bnav-btn.active{{color:#00d4ff}}
  .bnav-btn:active{{opacity:0.7}}
  .bnav-icon{{font-size:20px;line-height:1}}
  .bnav-lbl{{font-size:9px;letter-spacing:0.5px;text-transform:uppercase}}

  /* Sidebar Steuerung Buttons */
  #sidebar .hdr-btn{{
    display:flex !important;min-height:40px;
    align-items:center;justify-content:center;
  }}
  #ng-box{{font-size:12px}}
  .leaflet-control-attribution{{display:none}}
}}

/* ── Wirtschafts-Badge (T165) ─────────────────────────────── */
#econ-badge{{
  position:absolute;left:8px;bottom:28px;z-index:900;
  background:rgba(4,17,30,0.88);border:1px solid #1e3a4a;
  border-radius:3px;padding:5px 8px;font-size:9px;
  color:#8ab0c8;pointer-events:none;max-width:200px;
  display:none;
}}
#econ-badge.visible{{display:block}}
.eb-row{{display:flex;justify-content:space-between;gap:10px;margin-bottom:2px}}
.eb-key{{color:#4a8090}}
.eb-val{{color:#00ff88;font-weight:bold}}
.eb-pos{{color:#00ff88}} .eb-neg{{color:#ff4444}} .eb-neu{{color:#8aa0b0}}
.eb-stress{{margin-top:3px;padding-top:3px;border-top:1px solid #0d2035;
           display:flex;justify-content:space-between}}
#esc-trend{{font-size:13px;margin-left:3px;vertical-align:middle}}

/* ── Markercluster Dark-Theme Override ──────────────────────── */
.marker-cluster-small,.marker-cluster-medium,.marker-cluster-large{{
  background:rgba(255,68,0,0.25)!important;
}}
.marker-cluster-small div,.marker-cluster-medium div,.marker-cluster-large div{{
  background:rgba(255,68,0,0.5)!important;color:#fff!important;font-size:10px!important;
  font-family:'Courier New',monospace!important;font-weight:bold!important;
}}
</style>
</head>
<body>
<div id="mob-overlay" onclick="closeMobDrawer()"></div>
<nav id="bottom-nav">
  <button class="bnav-btn active" id="bnav-map" onclick="bnavMap()">
    <span class="bnav-icon">🗺</span><span class="bnav-lbl">Karte</span></button>
  <button class="bnav-btn" id="bnav-filter" onclick="bnavFilter()">
    <span class="bnav-icon">⚙</span><span class="bnav-lbl">Filter</span></button>
  <button class="bnav-btn" id="bnav-refresh" onclick="doRefresh()">
    <span class="bnav-icon">↻</span><span class="bnav-lbl">Refresh</span></button>
  <button class="bnav-btn" id="bnav-tl" onclick="window.open(location.origin+'/timeline','_blank')">
    <span class="bnav-icon">📅</span><span class="bnav-lbl">Timeline</span></button>
  <button class="bnav-btn" id="bnav-wl" onclick="window.open(location.origin+'/watchlist','_blank')">
    <span class="bnav-icon">📋</span><span class="bnav-lbl">Watchlist</span></button>
</nav>
<div id="header">
  <span class="logo">◈ NEXUS</span>
  <div id="esc-bar"><div id="esc-fill" style="width:0%"></div>
    <div id="esc-label">GRUEN<span id="esc-trend"></span></div></div>
  <div id="hdr-counts">
    <span id="c-fusion">🔗0</span>
    <span id="c-humint">🎯0</span>
    <span id="c-fires">🔥0</span>
    <span id="c-flights">✈0</span>
    <span id="c-ais">⚓0</span>
    <span id="c-rad">☢0</span>
    <span id="c-hf">📻0</span>
    <span id="c-move">🚛0</span>
  </div>
  <button class="hdr-btn" onclick="doRefresh()">↻ Jetzt</button>
  <button class="hdr-btn" onclick="toggleSidebar()">⚙ Filter</button>
  <button class="hdr-btn" id="sat-btn" onclick="toggleSat()">🛰 Sat</button>
  <button class="hdr-btn" onclick="window.open(location.origin+'/timeline','_blank')">📅 Timeline</button>
  <button class="hdr-btn" onclick="window.open(location.origin+'/api/pdf_export?region='+encodeURIComponent(curReg),'_blank')">📄 PDF</button>
  <button class="hdr-btn" onclick="window.open(location.origin+'/delta','_blank')">🔴🔵 Delta</button>
  <button class="hdr-btn" onclick="window.open(location.origin+'/watchlist','_blank')">📋 Watchlist</button>
  <button class="hdr-btn" id="btn-mob-refresh" style="display:none" onclick="doRefresh()">↻</button>
  <button class="hdr-btn" id="btn-mob-filter" style="display:none" onclick="bnavFilter()">⚙ Filter</button>
  <div id="status-line"><span id="status-txt">–</span> | ↻<span id="countdown">–</span></div>
</div>
<div id="region-bar">
  <span style="color:#4a6070;font-size:10px;align-self:center">Region:</span>
  <button class="reg-btn active" onclick="setReg(this,'Ukraine')">Ukraine</button>
  <button class="reg-btn" onclick="setReg(this,'Naher Osten')">Naher Osten</button>
  <button class="reg-btn" onclick="setReg(this,'Rotes Meer')">Rotes Meer</button>
  <button class="reg-btn" onclick="setReg(this,'Persischer Golf')">P.Golf</button>
  <button class="reg-btn" onclick="setReg(this,'Taiwan')">Taiwan</button>
  <button class="reg-btn" onclick="setReg(this,'Europa')">Europa</button>
</div>
<div id="main">
  <div id="map"></div>
  <div id="econ-badge"></div>
  <div id="sidebar">
    <div id="drawer-handle" onclick="closeMobDrawer()"></div>

    <!-- FUSION -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('fusion')">
        <span class="fg-title" style="color:#ff4466">🔗 FUSION THREATS</span>
        <span class="fg-arrow" id="arr-fusion">▼</span></div>
      <div class="fg-body" id="body-fusion">
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('fusion')">Threat-Assessments</span>
          <span class="fl-cnt" id="fc-fusion">0</span>
          <div class="sw on" id="sw-fusion" onclick="togLyr('fusion')"></div></div>
      </div></div>

    <!-- HUMINT -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('humint')">
        <span class="fg-title" style="color:#ffcc00">🎯 HUMINT / VISION</span>
        <span class="fg-arrow" id="arr-humint">▼</span></div>
      <div class="fg-body" id="body-humint">
        <div class="all-toggle" onclick="grpAll('humint',true)">alle an</div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('humint')">Feldmeldungen</span>
          <span class="fl-cnt" id="fc-humint">0</span>
          <div class="sw on" id="sw-humint" onclick="togLyr('humint')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('vision')">Bild-KI (LLaVA)</span>
          <span class="fl-cnt" id="fc-vision">0</span>
          <div class="sw on" id="sw-vision" onclick="togLyr('vision')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('geo')">Geo-Lokalisierung</span>
          <span class="fl-cnt" id="fc-geo">0</span>
          <div class="sw on" id="sw-geo" onclick="togLyr('geo')"></div></div>
      </div></div>

    <!-- BEWEGUNG -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('bewegung')">
        <span class="fg-title" style="color:#ff8800">🚛 BEWEGUNG</span>
        <span class="fg-arrow" id="arr-bewegung">▼</span></div>
      <div class="fg-body" id="body-bewegung">
        <div class="all-toggle" onclick="grpAll('bewegung',true)">alle an</div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('movement')">Konvois / Traffic</span>
          <span class="fl-cnt" id="fc-movement">0</span>
          <div class="sw on" id="sw-movement" onclick="togLyr('movement')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('webcam')">Webcam-Bewegung</span>
          <span class="fl-cnt" id="fc-webcam">0</span>
          <div class="sw on" id="sw-webcam" onclick="togLyr('webcam')"></div></div>
      </div></div>

    <!-- FEUER -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('feuer')">
        <span class="fg-title" style="color:#ff4400">🔥 FEUER / ANGRIFF</span>
        <span class="fg-arrow" id="arr-feuer">▼</span></div>
      <div class="fg-body" id="body-feuer">
        <div class="all-toggle" onclick="grpAll('feuer',true)">alle an</div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('fires')">FIRMS Brände</span>
          <span class="fl-cnt" id="fc-fires">0</span>
          <div class="sw on" id="sw-fires" onclick="togLyr('fires')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('acled')">ACLED Konflikte</span>
          <span class="fl-cnt" id="fc-acled">0</span>
          <div class="sw on" id="sw-acled" onclick="togLyr('acled')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('gdelt')">GDELT Events</span>
          <span class="fl-cnt" id="fc-gdelt">0</span>
          <div class="sw on" id="sw-gdelt" onclick="togLyr('gdelt')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('incidents')">Vorfalls-Marker</span>
          <span class="fl-cnt" id="fc-incidents">0</span>
          <div class="sw on" id="sw-incidents" onclick="togLyr('incidents')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('lightning')">Blitz/Artillerie</span>
          <span class="fl-cnt" id="fc-lightning">0</span>
          <div class="sw on" id="sw-lightning" onclick="togLyr('lightning')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togHeat()">🌡 Heatmap</span>
          <span class="fl-cnt" id="fc-heat">0</span>
          <div class="sw on" id="sw-heat" onclick="togHeat()"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togCluster()">🔴 Hotspot-Cluster</span>
          <span class="fl-cnt" id="fc-cluster">0</span>
          <div class="sw on" id="sw-cluster" onclick="togCluster()"></div></div>
      </div></div>

    <!-- SENSOR/ISR -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('sensor')">
        <span class="fg-title" style="color:#00d4ff">📡 SENSOR / ISR</span>
        <span class="fg-arrow" id="arr-sensor">▼</span></div>
      <div class="fg-body" id="body-sensor">
        <div class="all-toggle" onclick="grpAll('sensor',true)">alle an</div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('flights')">Flugzeuge</span>
          <span class="fl-cnt" id="fc-flights">0</span>
          <div class="sw on" id="sw-flights" onclick="togLyr('flights')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('milflights')">✈️ ISR-Blockade</span>
          <span class="fl-cnt" id="fc-milflights">0</span>
          <div class="sw on" id="sw-milflights" onclick="togLyr('milflights')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('patrol')">🔍 Verhaltens-Anomalien</span>
          <span class="fl-cnt" id="fc-patrol">0</span>
          <div class="sw on" id="sw-patrol" onclick="togLyr('patrol')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('vanac')">Ghost-Flugzeuge</span>
          <span class="fl-cnt" id="fc-vanac">0</span>
          <div class="sw on" id="sw-vanac" onclick="togLyr('vanac')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('sar')">SAR Sentinel-1</span>
          <span class="fl-cnt" id="fc-sar">0</span>
          <div class="sw on" id="sw-sar" onclick="togLyr('sar')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('hfsdr')">WebSDR / HF</span>
          <span class="fl-cnt" id="fc-hfsdr">0</span>
          <div class="sw on" id="sw-hfsdr" onclick="togLyr('hfsdr')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('gpsjam')">GPS-Jamming</span>
          <span class="fl-cnt" id="fc-gpsjam">0</span>
          <div class="sw on" id="sw-gpsjam" onclick="togLyr('gpsjam')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('seismic')">Seismik</span>
          <span class="fl-cnt" id="fc-seismic">0</span>
          <div class="sw on" id="sw-seismic" onclick="togLyr('seismic')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('notam')">NOTAMs</span>
          <span class="fl-cnt" id="fc-notam">0</span>
          <div class="sw on" id="sw-notam" onclick="togLyr('notam')"></div></div>
      </div></div>

    <!-- MARITIME -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('maritime')">
        <span class="fg-title" style="color:#00aaff">⚓ MARITIME</span>
        <span class="fg-arrow" id="arr-maritime">▼</span></div>
      <div class="fg-body" id="body-maritime">
        <div class="all-toggle" onclick="grpAll('maritime',true)">alle an</div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('ais')">AIS Schiffe</span>
          <span class="fl-cnt" id="fc-ais">0</span>
          <div class="sw on" id="sw-ais" onclick="togLyr('ais')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('vanv')">Ghost-Vessels</span>
          <span class="fl-cnt" id="fc-vanv">0</span>
          <div class="sw on" id="sw-vanv" onclick="togLyr('vanv')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('draught')">Tiefgang-Delta</span>
          <span class="fl-cnt" id="fc-draught">0</span>
          <div class="sw on" id="sw-draught" onclick="togLyr('draught')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('naval')">⚓ Marinelogistik</span>
          <span class="fl-cnt" id="fc-naval">0</span>
          <div class="sw on" id="sw-naval" onclick="togLyr('naval')"></div></div>
      </div></div>

    <!-- UMWELT/NUKLR -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('umwelt')">
        <span class="fg-title" style="color:#aaff44">🌍 UMWELT / NUKLR</span>
        <span class="fg-arrow" id="arr-umwelt">▼</span></div>
      <div class="fg-body" id="body-umwelt">
        <div class="all-toggle" onclick="grpAll('umwelt',true)">alle an</div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('radiation')">Strahlung ☢</span>
          <span class="fl-cnt" id="fc-radiation">0</span>
          <div class="sw on" id="sw-radiation" onclick="togLyr('radiation')"></div></div>
        <div class="fl-row"><span class="fl-lbl" onclick="togLyr('eonet')">EONET Natur</span>
          <span class="fl-cnt" id="fc-eonet">0</span>
          <div class="sw on" id="sw-eonet" onclick="togLyr('eonet')"></div></div>
      </div></div>

    <!-- INFO-NETZ -->
    <div class="filter-group">
      <div class="fg-hdr" onclick="togGrp('info')">
        <span class="fg-title" style="color:#cc44ff">📣 INFO-NETZ</span>
        <span class="fg-arrow" id="arr-info">▶</span></div>
      <div class="fg-body" id="body-info" style="display:none">
        <div id="ng-box">Keine Daten.</div>
      </div></div>

    <!-- Steuerung -->
    <div style="padding:8px 10px;border-top:1px solid #0d2035">
      <button class="hdr-btn" style="width:100%;margin-bottom:4px" onclick="allLyrs(true)">◉ Alle AN</button>
      <button class="hdr-btn" style="width:100%" onclick="allLyrs(false)">○ Alle AUS</button>
    </div>

    <!-- Legende -->
    <div style="padding:8px 10px;border-top:1px solid #0d2035;font-size:10px;color:#4a6070;line-height:1.8">
      <div style="color:#8ab0c8;font-weight:bold;margin-bottom:2px">LEGENDE</div>
      <div>🔗 Fusion-Threat</div><div>🎯 HUMINT</div>
      <div>👁 Bild-KI</div><div>🚛 Konvoi</div>
      <div>📹 Webcam-Alert</div><div>🔥 FIRMS-Brand</div>
      <div>✈ Flugzeug</div><div>👻 Ghost-Signal</div>
      <div>⚓ AIS-Schiff</div><div>🛰 SAR-Pass</div>
      <div>📻 HF-Signal</div><div>📡 GPS-Jam</div>
      <div>⚡ Seismik</div><div>☢ Strahlung</div>
      <div>🌍 Natur-Event</div>
    </div>
  </div>
</div>

<script>
const PORT = {port};
const BASE = window.location.origin;  // funktioniert auf Handy + PC
const REFRESH_SEC = 120;

// ── Map ──────────────────────────────────────────────────────────────────────
const map = L.map('map',{{center:[48.5,32.0],zoom:6,zoomControl:true,attributionControl:false}});
// Deutsche OSM-Kacheln (Beschriftung auf Deutsch) mit CSS-Dunkelfilter
const tDark = L.tileLayer('https://tile.openstreetmap.de/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'© OpenStreetMap-Mitwirkende'}});
// Dunkelfilter via CSS auf das Kachel-Canvas (invertiert + hue-rotate = Dark Mode)
document.addEventListener('DOMContentLoaded',()=>{{
  const style=document.createElement('style');
  style.textContent='.leaflet-tile-pane{{filter:invert(1) hue-rotate(180deg) brightness(0.85) saturate(0.9);}}';
  document.head.appendChild(style);
}});
const tSat  = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',{{maxZoom:19}});
tDark.addTo(map);
let satMode=false;
function toggleSat(){{
  satMode=!satMode;
  if(satMode){{map.removeLayer(tDark);map.addLayer(tSat);document.getElementById('sat-btn').textContent='🌑 Dark';}}
  else{{map.removeLayer(tSat);map.addLayer(tDark);document.getElementById('sat-btn').textContent='🛰 Sat';}}
}}

// ── Layers ───────────────────────────────────────────────────────────────────
const LYR={{
  fusion:L.layerGroup().addTo(map), humint:L.layerGroup().addTo(map),
  vision:L.layerGroup().addTo(map), geo:L.layerGroup().addTo(map),
  movement:L.layerGroup().addTo(map), webcam:L.layerGroup().addTo(map),
  fires:L.layerGroup().addTo(map), acled:L.layerGroup().addTo(map),
  gdelt:L.layerGroup().addTo(map), incidents:L.layerGroup().addTo(map),
  lightning:L.layerGroup().addTo(map), flights:L.layerGroup().addTo(map),
  vanac:L.layerGroup().addTo(map), sar:L.layerGroup().addTo(map),
  hfsdr:L.layerGroup().addTo(map), gpsjam:L.layerGroup().addTo(map),
  seismic:L.layerGroup().addTo(map), notam:L.layerGroup().addTo(map),
  ais:L.layerGroup().addTo(map), vanv:L.layerGroup().addTo(map),
  draught:L.layerGroup().addTo(map), radiation:L.layerGroup().addTo(map),
  eonet:L.layerGroup().addTo(map),
  viirs:L.layerGroup().addTo(map), health:L.layerGroup().addTo(map),
  sanctions:L.layerGroup().addTo(map), bgp:L.layerGroup().addTo(map),
  displacement:L.layerGroup().addTo(map),
  naval:L.layerGroup().addTo(map),
  milflights:L.layerGroup().addTo(map),
  patrol:L.layerGroup().addTo(map),
}};
const lyrOn={{}};
Object.keys(LYR).forEach(k=>lyrOn[k]=true);

// ── Hotspot-Cluster Layer (T165) ─────────────────────────────────────────────
let clusterGroup = null;
let clusterOn = true;
function _initCluster(){{
  if(typeof L.markerClusterGroup === 'undefined') return;
  if(clusterGroup && map.hasLayer(clusterGroup)) map.removeLayer(clusterGroup);
  clusterGroup = L.markerClusterGroup({{
    maxClusterRadius: 50,
    showCoverageOnHover: false,
    iconCreateFunction: function(cl){{
      const n = cl.getChildCount();
      const sz = n >= 50 ? 'large' : n >= 10 ? 'medium' : 'small';
      return L.divIcon({{html:'<div><span>'+n+'</span></div>',
        className:'marker-cluster marker-cluster-'+sz,iconSize:[40,40]}});
    }}
  }});
  if(clusterOn) clusterGroup.addTo(map);
}}
_initCluster();
function togCluster(){{
  clusterOn = !clusterOn;
  const sw = document.getElementById('sw-cluster');
  if(sw) sw.className = 'sw' + (clusterOn ? ' on' : '');
  if(!clusterGroup) return;
  clusterOn ? (!map.hasLayer(clusterGroup) && clusterGroup.addTo(map))
            : (map.hasLayer(clusterGroup) && map.removeLayer(clusterGroup));
}}

// ── Heatmap ───────────────────────────────────────────────────────────────────
let heatLayer = null;
let heatOn = true;

function togHeat(){{
  heatOn = !heatOn;
  const sw = document.getElementById('sw-heat');
  if(sw) sw.className = 'sw' + (heatOn ? ' on' : '');
  if(heatLayer){{
    heatOn ? (!map.hasLayer(heatLayer) && map.addLayer(heatLayer))
           : (map.hasLayer(heatLayer) && map.removeLayer(heatLayer));
  }}
}}

function updateHeatmap(d){{
  const pts = [];
  // ACLED Konflikte: Gewichtung nach Fatalities
  (d.acled||[]).forEach(a=>{{
    if(a.lat&&a.lon) pts.push([a.lat, a.lon, Math.min(1.0, 0.3 + (a.fatalities||0)*0.05)]);
  }});
  // GDELT Events
  (d.gdelt_points||[]).forEach(g=>{{
    if(g.lat&&g.lon) pts.push([g.lat, g.lon, 0.25]);
  }});
  // FIRMS Brände: Gewichtung nach FRP
  (d.fires||[]).forEach(f=>{{
    if(f.lat&&f.lon) pts.push([f.lat, f.lon, Math.min(1.0, 0.2 + Math.log((f.frp||1)+1)*0.1)]);
  }});
  // Seismik: Gewichtung nach Magnitude
  (d.earthquakes||[]).forEach(q=>{{
    if(q.lat&&q.lon) pts.push([q.lat, q.lon, Math.min(1.0, ((q.magnitude||q.mag||1)-1)*0.15)]);
  }});
  // Incidents & Lightning
  (d.incidents||[]).forEach(i=>{{ if(i.lat&&i.lon) pts.push([i.lat, i.lon, 0.3]); }});
  (d.lightning_signals||[]).forEach(s=>{{ if(s.lat&&s.lon) pts.push([s.lat, s.lon, 0.2]); }});
  // Fusion-Threats: höchste Gewichtung
  (d.fusion_threats||[]).forEach(ft=>{{
    if(ft.lat&&ft.lon){{
      const w = {{KRITISCH:1.0,HOCH:0.75,MITTEL:0.5,NIEDRIG:0.2}}[ft.severity||'MITTEL']||0.5;
      pts.push([ft.lat, ft.lon, w]);
    }}
  }});

  if(heatLayer && map.hasLayer(heatLayer)) map.removeLayer(heatLayer);
  if(pts.length === 0){{ heatLayer = null; setFC('heat', 0); return; }}

  heatLayer = L.heatLayer(pts, {{
    radius: 30,
    blur: 22,
    maxZoom: 12,
    max: 1.0,
    gradient: {{0.0:'#001a33', 0.2:'#003366', 0.4:'#0066aa', 0.6:'#ff8800', 0.8:'#ff4400', 1.0:'#ff0044'}}
  }});
  if(heatOn) heatLayer.addTo(map);
  setFC('heat', pts.length);
}}

const GRP={{
  fusion:['fusion'], humint:['humint','vision','geo'],
  bewegung:['movement','webcam'],
  feuer:['fires','acled','gdelt','incidents','lightning'],
  sensor:['flights','vanac','sar','hfsdr','gpsjam','seismic','notam'],
  maritime:['ais','vanv','draught'],
  umwelt:['radiation','eonet'],
}};
const grpOpen={{}};
['fusion','humint','bewegung','feuer','sensor','maritime','umwelt','info'].forEach(g=>grpOpen[g]=true);
grpOpen.info=false;
document.getElementById('body-info').style.display='none';

function togGrp(g){{
  grpOpen[g]=!grpOpen[g];
  const b=document.getElementById('body-'+g);
  const a=document.getElementById('arr-'+g);
  if(b)b.style.display=grpOpen[g]?'':'none';
  if(a)a.textContent=grpOpen[g]?'▼':'▶';
}}
function togLyr(id){{
  lyrOn[id]=!lyrOn[id];
  const sw=document.getElementById('sw-'+id);
  if(sw)sw.className='sw'+(lyrOn[id]?' on':'');
  lyrOn[id]?(!map.hasLayer(LYR[id])&&map.addLayer(LYR[id])):(map.hasLayer(LYR[id])&&map.removeLayer(LYR[id]));
}}
function grpAll(g,on){{(GRP[g]||[]).forEach(id=>{{if(lyrOn[id]!==on)togLyr(id);}});}}
function allLyrs(on){{Object.keys(LYR).forEach(id=>{{if(lyrOn[id]!==on)togLyr(id);}});}}
function toggleSidebar(){{
  if(window.innerWidth<=768){{ bnavFilter(); return; }}
  const s=document.getElementById('sidebar');
  s.style.display=s.style.display==='none'?'':'none';
}}
function isMob(){{return window.innerWidth<=768;}}
function openMobDrawer(){{
  document.getElementById('sidebar').classList.add('mob-open');
  document.getElementById('mob-overlay').classList.add('active');
  document.getElementById('bnav-filter').classList.add('active');
  document.getElementById('bnav-map').classList.remove('active');
}}
function closeMobDrawer(){{
  document.getElementById('sidebar').classList.remove('mob-open');
  document.getElementById('mob-overlay').classList.remove('active');
  document.getElementById('bnav-filter').classList.remove('active');
  document.getElementById('bnav-map').classList.add('active');
}}
function bnavMap(){{
  if(isMob()) closeMobDrawer();
  document.querySelectorAll('.bnav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('bnav-map').classList.add('active');
}}
function bnavFilter(){{
  if(isMob()){{
    const open=document.getElementById('sidebar').classList.contains('mob-open');
    open ? closeMobDrawer() : openMobDrawer();
  }} else {{
    toggleSidebar();
  }}
}}

// ── Helpers ──────────────────────────────────────────────────────────────────
function esc(s){{return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
function mkIco(em,col,sz,p){{
  const cls=p==='fast'?'pfst':p?'pulse':'';
  const px=sz||16;
  return L.divIcon({{html:`<div class="${{cls}}" style="font-size:${{px}}px;line-height:1;filter:drop-shadow(0 0 4px ${{col||'#fff'}})">${{em}}</div>`,iconSize:[px,px],iconAnchor:[px/2,px/2],className:''}});
}}
function badge(conf){{
  const p=Math.round((conf||0)*100);
  const c=conf>0.7?'#ff4444':conf>0.4?'#ff8800':'#666';
  return `<span class="pbadge" style="background:${{c}}">${{p}}%</span>`;
}}
function setFC(id,n){{const e=document.getElementById('fc-'+id);if(e)e.textContent=n||0;}}

// ── Renderers ─────────────────────────────────────────────────────────────────
function rFusion(items){{
  LYR.fusion.clearLayers();
  (items||[]).forEach(ft=>{{
    if(!ft.lat||!ft.lon)return;
    const sev=ft.severity||'MITTEL';
    const col={{KRITISCH:'#ff0044',HOCH:'#ff6600',MITTEL:'#ffaa00',NIEDRIG:'#00cc88'}}[sev]||'#ff6600';
    L.marker([ft.lat,ft.lon],{{icon:mkIco(ft.icon||'🔗',col,22,'fast')}})
     .bindPopup(`<div class="ptitle">${{esc(ft.title||ft.label)}}</div>
       ${{badge(ft.confidence)}} <span class="pbadge" style="background:${{col}}">${{sev}}</span>
       <div class="prow"><span class="pkey">Signale:</span><span class="pval">${{esc((ft.signals||[]).join(', '))}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(ft.text||ft.description||'')}}</div>`)
     .addTo(LYR.fusion);
  }});
  setFC('fusion',(items||[]).length);
}}

function rHumint(items){{
  LYR.humint.clearLayers();
  (items||[]).forEach(h=>{{
    if(!h.lat||!h.lon)return;
    const c=h.confidence||0.5;
    const col=c>0.7?'#ff4444':c>0.4?'#ffcc00':'#888';
    L.marker([h.lat,h.lon],{{icon:mkIco('🎯',col,18,c>0.7?'pulse':null)}})
     .bindPopup(`<div class="ptitle">🎯 ${{esc(h.unit_name||h.title||'HUMINT')}}</div>
       ${{badge(c)}}
       <div class="prow"><span class="pkey">Waffe:</span><span class="pval">${{esc(h.weapon_cat||'-')}}</span></div>
       <div class="prow"><span class="pkey">Quelle:</span><span class="pval">${{esc(h.source||'-')}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(h.text||'')}}</div>`)
     .addTo(LYR.humint);
  }});
  setFC('humint',(items||[]).length);
}}

function rVision(items){{
  LYR.vision.clearLayers();
  (items||[]).forEach(v=>{{
    if(!v.lat||!v.lon)return;
    L.marker([v.lat,v.lon],{{icon:mkIco('👁','#cc44ff',16)}})
     .bindPopup(`<div class="ptitle">👁 Bild-KI</div>
       ${{badge(v.confidence)}}
       <div class="prow"><span class="pkey">Fahrzeuge:</span><span class="pval">${{v.vehicles||0}}</span></div>
       <div class="prow"><span class="pkey">Schäden:</span><span class="pval">${{esc(v.damage||'-')}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(v.summary||v.text||'')}}</div>`)
     .addTo(LYR.vision);
  }});
  setFC('vision',(items||[]).length);
}}

function rGeo(items){{
  LYR.geo.clearLayers();
  (items||[]).forEach(g=>{{
    if(!g.lat||!g.lon)return;
    L.marker([g.lat,g.lon],{{icon:mkIco('📍','#aa88ff',14)}})
     .bindPopup(`<div class="ptitle">📍 Geo-Lok</div>
       <div class="prow"><span class="pkey">Methode:</span><span class="pval">${{esc(g.method||'-')}}</span></div>
       <div class="prow"><span class="pkey">Genauigkeit:</span><span class="pval">${{g.accuracy_km||'?'}} km</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(g.place_name||g.title||'')}}</div>`)
     .addTo(LYR.geo);
  }});
  setFC('geo',(items||[]).length);
}}

function rMovement(items){{
  LYR.movement.clearLayers();
  (items||[]).forEach(mv=>{{
    if(!mv.lat||!mv.lon)return;
    const conv=mv.convoy_hint||mv.icon==='🚛';
    L.marker([mv.lat,mv.lon],{{icon:mkIco(conv?'🚛':'🚗','#ff8800',16,conv?'pulse':null)}})
     .bindPopup(`<div class="ptitle">${{conv?'🚛 Konvoi':'🚗 Traffic-Anomalie'}}</div>
       <div class="prow"><span class="pkey">Typ:</span><span class="pval">${{esc(mv.anomaly_type||'-')}}</span></div>
       <div class="prow"><span class="pkey">Schwere:</span><span class="pval">${{esc(mv.severity||'-')}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(mv.text||mv.description||'')}}</div>`)
     .addTo(LYR.movement);
  }});
  setFC('movement',(items||[]).length);
}}

function rWebcam(items){{
  LYR.webcam.clearLayers();
  (items||[]).forEach(w=>{{
    if(!w.lat||!w.lon)return;
    const n=w.night_anomaly;
    L.marker([w.lat,w.lon],{{icon:mkIco(n?'🔴':'📹',n?'#ff0044':'#ff8800',16,n?'pulse':null)}})
     .bindPopup(`<div class="ptitle">${{esc(w.title||'Webcam Alert')}}</div>
       <div class="prow"><span class="pkey">Bewegung:</span><span class="pval">${{w.motion_pct||0}}% Pixel</span></div>
       ${{n?'<div style="color:#ff4444;font-weight:bold">⚠ NACHTS</div>':''}}`)
     .addTo(LYR.webcam);
  }});
  setFC('webcam',(items||[]).length);
}}

function rFires(items){{
  LYR.fires.clearLayers();
  (items||[]).forEach(f=>{{
    if(!f.lat||!f.lon)return;
    const frp=f.frp||f.value||1;
    const sz=Math.min(22,12+Math.log(frp+1)*2);
    L.marker([f.lat,f.lon],{{icon:mkIco('🔥','#ff4400',sz)}})
     .bindPopup(`<div class="ptitle">🔥 FIRMS Brand</div>
       <div class="prow"><span class="pkey">FRP:</span><span class="pval">${{frp}} MW</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(f.title||f.text||'')}}</div>`)
     .addTo(LYR.fires);
  }});
  setFC('fires',(items||[]).length);
}}

function rAcled(items){{
  LYR.acled.clearLayers();
  (items||[]).forEach(a=>{{
    if(!a.lat||!a.lon)return;
    const col=a.color||'#ff6600';
    L.marker([a.lat,a.lon],{{icon:mkIco(a.icon||'⚔',col,13)}})
     .bindPopup(`<div class="ptitle">⚔ ACLED ${{esc(a.event_type||'')}}</div>
       <div class="prow"><span class="pkey">Datum:</span><span class="pval">${{esc(a.date||'-')}}</span></div>
       <div class="prow"><span class="pkey">Fatal:</span><span class="pval">${{a.fatalities||0}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(a.title||a.text||'')}}</div>`)
     .addTo(LYR.acled);
  }});
  setFC('acled',(items||[]).length);
}}

function rGdelt(items){{
  LYR.gdelt.clearLayers();
  (items||[]).forEach(g=>{{
    if(!g.lat||!g.lon)return;
    L.marker([g.lat,g.lon],{{icon:mkIco('📰','#aa8800',12)}})
     .bindPopup(`<div class="ptitle">📰 GDELT</div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(g.title||g.text||'')}}</div>
       ${{g.url?`<a class="plink" href="${{esc(g.url)}}" target="_blank">→ Quelle</a>`:''}}`).addTo(LYR.gdelt);
  }});
  setFC('gdelt',(items||[]).length);
}}

function rIncidents(items){{
  LYR.incidents.clearLayers();
  (items||[]).forEach(i=>{{
    if(!i.lat||!i.lon)return;
    L.marker([i.lat,i.lon],{{icon:mkIco('⚡','#ffaa00',13)}})
     .bindPopup(`<div class="ptitle">⚡ ${{esc(i.type||'Vorfall')}}</div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(i.text||i.title||'')}}</div>`)
     .addTo(LYR.incidents);
  }});
  setFC('incidents',(items||[]).length);
}}

function rLightning(items){{
  LYR.lightning.clearLayers();
  (items||[]).forEach(s=>{{
    if(!s.lat||!s.lon)return;
    const art=s.signal_type==='ARTILLERY'||s.is_artillery;
    L.marker([s.lat,s.lon],{{icon:mkIco(art?'💥':'⚡','#ffff00',14,art?'pulse':null)}})
     .bindPopup(`<div class="ptitle">${{art?'💥 Artillerie':'⚡ Blitz'}}</div>
       <div class="prow"><span class="pkey">Intensität:</span><span class="pval">${{s.count||s.intensity||'-'}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(s.text||s.description||'')}}</div>`)
     .addTo(LYR.lightning);
  }});
  setFC('lightning',(items||[]).length);
}}

function rFlights(fl,vanAc){{
  LYR.flights.clearLayers(); LYR.vanac.clearLayers();
  const ac=(fl&&fl.aircraft)||[];
  ac.forEach(a=>{{
    if(!a.lat||!a.lon)return;
    const susp=(a.suspicious||'').toLowerCase();
    const isr=susp.includes('isr')||susp.includes('awacs')||susp.includes('rc-')||susp.includes('e-8');
    const col=isr?'#ff0088':susp?'#ff8800':'#00aaff';
    L.marker([a.lat,a.lon],{{icon:mkIco(isr?'🔭':'✈',col,isr?18:13,isr?'pulse':null)}})
     .bindPopup(`<div class="ptitle">${{isr?'🔭':'✈'}} ${{esc(a.callsign||'(kein)')}}</div>
       <div class="prow"><span class="pkey">Höhe:</span><span class="pval">${{a.altitude_ft||'?'}} ft</span></div>
       <div class="prow"><span class="pkey">Speed:</span><span class="pval">${{a.velocity_kmh||'?'}} km/h</span></div>
       ${{susp?`<div style="color:#ff8800">⚠ ${{esc(a.suspicious)}}</div>`:''}}`)
     .addTo(LYR.flights);
  }});
  (vanAc||[]).forEach(a=>{{
    if(!a.lat||!a.lon)return;
    L.marker([a.lat,a.lon],{{icon:mkIco('👻','#666688',16,'pulse')}})
     .bindPopup(`<div class="ptitle">👻 Ghost: ${{esc(a.callsign||'?')}}</div>
       <div style="color:#ff8800">Transponder-Off vor ${{a.snap_age_s||'?'}}s</div>`)
     .addTo(LYR.vanac);
  }});
  setFC('flights',ac.length); setFC('vanac',(vanAc||[]).length);
}}

function rSar(items){{
  LYR.sar.clearLayers();
  let shipCount=0;
  (items||[]).forEach(s=>{{
    if(!s.lat||!s.lon)return;
    if(s.type==='sar-ship'){{
      // ── SAR-erkanntes Schiff ──────────────────────────────────────────────
      shipCount++;
      const col=s.color||'#ff8800';
      const catLower=(s.category||'').toLowerCase();
      const ico=catLower.includes('frachter')||catLower.includes('tanker')?'🚢':
                catLower.includes('kriegs')||catLower.includes('zerstörer')||catLower.includes('fregatte')?'⚔️':
                catLower.includes('träger')?'🛩':
                catLower.includes('u-boot')?'🔱':
                catLower.includes('drohne')||catLower.includes('usv')?'🤖':'🛰';
      L.marker([s.lat,s.lon],{{icon:mkIco(ico,col,20,'pulse')}})
       .bindPopup(s.popup||`<b>${{esc(s.title||'SAR Ziel')}}</b>`)
       .addTo(LYR.sar);
    }} else {{
      // ── SAR-Überflug / Szenen-Marker (Fallback) ───────────────────────────
      const anom=s.anomaly_score||0.3;
      const col=s.color||(anom>0.6?'#cc44ff':'#884499');
      L.marker([s.lat,s.lon],{{icon:mkIco('🛰',col,16)}})
       .bindPopup(s.popup||`<div class="ptitle">🛰 Sentinel-1 SAR</div>
         <div class="prow"><span class="pkey">Datum:</span><span class="pval">${{esc(s.sensing_date||'-')}}</span></div>
         <div class="prow"><span class="pkey">Anomalie:</span><span class="pval">${{Math.round(anom*100)}}%</span></div>
         ${{s.cop_link?`<a class="plink" href="${{esc(s.cop_link)}}" target="_blank">→ Copernicus</a>`:''}}`).addTo(LYR.sar);
    }}
  }});
  setFC('sar',(items||[]).length);
}}

function rNaval(items){{
  LYR.naval.clearLayers();
  (items||[]).forEach(v=>{{
    if(!v.lat||!v.lon)return;
    const col = {{USA:'#4488ff',GBR:'#00aaff',FRA:'#0066cc',
                  CHN:'#ff2200',RUS:'#cc0000'}}[v.nation]||'#aaaaaa';
    const rel  = v.relevance||'NIEDRIG';
    const pulse= rel==='HOCH'?'pulse':null;
    const ico  = v.anchor?'⚓':'🚢';
    L.marker([v.lat,v.lon],{{icon:mkIco(ico,col,18,pulse)}})
     .bindPopup(v.popup||`<b>${{esc(v.title||'Naval')}}</b>`)
     .addTo(LYR.naval);
  }});
  setFC('naval',(items||[]).length);
}}

function rMilflights(items){{
  LYR.milflights.clearLayers();
  (items||[]).forEach(f=>{{
    if(!f.lat||!f.lon)return;
    const col = {{KRITISCH:'#ff0000',HOCH:'#ff4400',MITTEL:'#ff8800',
                  NIEDRIG:'#ffcc00',NORMAL:'#00cc44'}}[f.level]||'#00aaff';
    const pulse = (f.level==='KRITISCH'||f.level==='HOCH')?'pulse':null;
    const ico = {{ASW:'🔱',SIGINT:'📡',ELINT:'📡',AWACS:'🔭',Tanker:'⛽',
                  'ISR-UAV':'🛸',GMTI:'🎯',C2:'📶'}}[f.role]||'✈️';
    L.marker([f.lat,f.lon],{{icon:mkIco(ico,col,18,pulse)}})
     .bindPopup(f.popup||`<b>${{esc(f.callsign||'MilFlight')}}</b>`)
     .addTo(LYR.milflights);
  }});
  setFC('milflights',(items||[]).length);
}}

function rPatrol(items){{
  LYR.patrol.clearLayers();
  (items||[]).forEach(a=>{{
    if(!a.lat||!a.lon)return;
    const col = {{KRITISCH:'#ff2222',HOCH:'#ff8800',MITTEL:'#ffcc00',
                  NIEDRIG:'#44ff44'}}[a.level]||'#aaaaaa';
    const pulse = (a.level==='KRITISCH'||a.level==='HOCH')?'pulse':null;
    const ico   = {{REGION_CHANGE:'🗺️',SPEED_ANOMALY:'⚡',ANCHOR_DRIFT:'⚓',
                   SPEED_STOP:'🛑',NEW_ENTITY:'👁️'}}[a.anomaly_type]||'⚠️';
    L.marker([a.lat,a.lon],{{icon:mkIco(ico,col,18,pulse)}})
     .bindPopup(a.popup||`<b>${{esc(a.description||'Anomalie')}}</b>`)
     .addTo(LYR.patrol);
  }});
  setFC('patrol',(items||[]).length);
}}

function rHF(items){{
  LYR.hfsdr.clearLayers();
  (items||[]).forEach(h=>{{
    if(!h.lat||!h.lon)return;
    L.marker([h.lat,h.lon],{{icon:mkIco('📻','#ff00ff',16,h.confidence>0.6?'pulse':null)}})
     .bindPopup(`<div class="ptitle">📻 ${{esc(h.title||h.label||'HF-Signal')}}</div>
       <div class="prow"><span class="pkey">Freq:</span><span class="pval">${{h.freq_mhz||'?'}} MHz</span></div>
       <div class="prow"><span class="pkey">SNR:</span><span class="pval">${{h.strength_db||'?'}} dB</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(h.text||h.description||'')}}</div>`)
     .addTo(LYR.hfsdr);
  }});
  setFC('hfsdr',(items||[]).length);
}}

function rGpsjam(items){{
  LYR.gpsjam.clearLayers();
  (items||[]).forEach(z=>{{
    if(!z.lat||!z.lon)return;
    L.circle([z.lat,z.lon],{{radius:(z.radius_km||50)*1000,color:'#ff6600',weight:1,fillColor:'#ff6600',fillOpacity:0.08}})
     .bindPopup(`<div class="ptitle">📡 GPS-Jamming</div>
       <div class="prow"><span class="pkey">Stärke:</span><span class="pval">${{esc(z.intensity||'-')}}</span></div>
       <div class="prow"><span class="pkey">Radius:</span><span class="pval">${{z.radius_km||'?'}} km</span></div>`)
     .addTo(LYR.gpsjam);
    L.marker([z.lat,z.lon],{{icon:mkIco('📡','#ff6600',12)}}).addTo(LYR.gpsjam);
  }});
  setFC('gpsjam',(items||[]).length);
}}

function rSeismic(items){{
  LYR.seismic.clearLayers();
  (items||[]).forEach(q=>{{
    if(!q.lat||!q.lon)return;
    const mag=q.magnitude||q.mag||1;
    const hit=q.osint_hint||q.impact;
    L.marker([q.lat,q.lon],{{icon:mkIco(hit?'💥':'⚡',hit?'#ff4400':'#888844',Math.min(22,10+mag*2),hit?'pulse':null)}})
     .bindPopup(`<div class="ptitle">${{hit?'💥 Einschlag':'⚡ Erdbeben'}} M${{mag}}</div>
       <div class="prow"><span class="pkey">Tiefe:</span><span class="pval">${{q.depth_km||'?'}} km</span></div>
       ${{hit?`<div style="color:#ff8800">⚠ ${{esc(q.osint_hint||q.impact||'')}}</div>`:''}}`)
     .addTo(LYR.seismic);
  }});
  setFC('seismic',(items||[]).length);
}}

function rNotam(items){{
  LYR.notam.clearLayers();
  (items||[]).forEach(n=>{{
    if(!n.lat||!n.lon)return;
    L.marker([n.lat,n.lon],{{icon:mkIco('🚫','#ff8800',13)}})
     .bindPopup(`<div class="ptitle">🚫 NOTAM ${{esc(n.notam_id||'')}}</div>
       <div class="prow"><span class="pkey">Höhe:</span><span class="pval">${{n.lower_ft||0}}–${{n.upper_ft||'?'}} ft</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc((n.text||n.description||'').slice(0,150))}}</div>`)
     .addTo(LYR.notam);
  }});
  setFC('notam',(items||[]).length);
}}

function rAis(vessels,vanV){{
  LYR.ais.clearLayers(); LYR.vanv.clearLayers();
  (vessels||[]).forEach(v=>{{
    if(!v.lat||!v.lon)return;
    L.marker([v.lat,v.lon],{{icon:mkIco('⚓','#0088ff',12)}})
     .bindPopup(`<div class="ptitle">⚓ ${{esc(v.name||v.vessel||'(unbekannt)')}}</div>
       <div class="prow"><span class="pkey">Typ:</span><span class="pval">${{esc(v.type||'-')}}</span></div>
       <div class="prow"><span class="pkey">Speed:</span><span class="pval">${{v.speed||v.speed_kn||'?'}} kn</span></div>`)
     .addTo(LYR.ais);
  }});
  (vanV||[]).forEach(v=>{{
    if(!v.lat||!v.lon)return;
    L.marker([v.lat,v.lon],{{icon:mkIco('👻','#226688',16,'pulse')}})
     .bindPopup(`<div class="ptitle">👻 Ghost: ${{esc(v.name||'?')}}</div>
       <div style="color:#ff8800">AIS-Dark vor ${{v.snap_age_s||'?'}}s</div>`)
     .addTo(LYR.vanv);
  }});
  setFC('ais',(vessels||[]).length); setFC('vanv',(vanV||[]).length);
}}

function rDraught(items){{
  LYR.draught.clearLayers();
  (items||[]).forEach(d=>{{
    if(!d.lat||!d.lon)return;
    L.marker([d.lat,d.lon],{{icon:mkIco('⚖','#00aaff',12)}})
     .bindPopup(`<div class="ptitle">⚖ Tiefgang-Delta: ${{esc(d.name||'?')}}</div>
       <div class="prow"><span class="pkey">Änderung:</span><span class="pval">${{esc(d.delta||'-')}}</span></div>`)
     .addTo(LYR.draught);
  }});
  setFC('draught',(items||[]).length);
}}

function rRadiation(items){{
  LYR.radiation.clearLayers();
  (items||[]).forEach(r=>{{
    if(!r.lat||!r.lon)return;
    const lv=r.alert_level||'NORMAL';
    const col={{KRITISCH:'#ff0044',ERHOEHT:'#ff6600','LEICHT_ERHOEHT':'#ffaa00',NORMAL:'#00ff88'}}[lv]||
              (lv.includes('KRIT')?'#ff0044':lv.includes('ERH')?'#ff6600':'#00ff88');
    const p=lv.includes('KRIT')?'fast':lv.includes('ERH')?'pulse':null;
    L.marker([r.lat,r.lon],{{icon:mkIco('☢',col,18,p)}})
     .bindPopup(`<div class="ptitle">☢ ${{esc(r.title||r.station_name||'Strahlung')}}</div>
       <span class="pbadge" style="background:${{col}}">${{lv}}</span>
       <div class="prow"><span class="pkey">CPM:</span><span class="pval">${{r.value_cpm||'?'}}</span></div>
       <div class="prow"><span class="pkey">Faktor:</span><span class="pval">${{r.anomaly_mult||'?'}}×</span></div>
       <div class="prow"><span class="pkey">Quelle:</span><span class="pval">${{esc(r.source||'-')}}</span></div>
       <div style="margin-top:4px;font-size:10px;color:#8ab0c8">${{esc(r.text||r.description||'')}}</div>`)
     .addTo(LYR.radiation);
  }});
  setFC('radiation',(items||[]).length);
}}

function rEonet(items){{
  LYR.eonet.clearLayers();
  (items||[]).forEach(e=>{{
    if(!e.lat||!e.lon)return;
    L.marker([e.lat,e.lon],{{icon:mkIco(e.icon||'🌍',e.color||'#44aa44',13)}})
     .bindPopup(`<div class="ptitle">${{esc(e.title||'EONET Event')}}</div>
       <div class="prow"><span class="pkey">Kategorie:</span><span class="pval">${{esc(e.category||'-')}}</span></div>`)
     .addTo(LYR.eonet);
  }});
  setFC('eonet',(items||[]).length);
}}

// ── VIIRS Nachtlichter ───────────────────────────────────────────────────────
function rViirs(items){{
  LYR.viirs.clearLayers();
  (items||[]).forEach(v=>{{
    if(!v.lat||!v.lon)return;
    L.marker([v.lat,v.lon],{{icon:mkIco('⬛','#333344',16,'pulse')}})
     .bindPopup(v.popup||`<b>⬛ VIIRS Verdunkelung</b><br>${{esc(v.title||'')}}`).addTo(LYR.viirs);
    // Schwarzer Kreis für Dunkelbereich
    L.circle([v.lat,v.lon],{{radius:80000,color:'#222233',weight:1,fillColor:'#111122',fillOpacity:0.25}}).addTo(LYR.viirs);
  }});
  setFC('viirs',(items||[]).length);
}}

// ── Gesundheits-Frühwarnung ──────────────────────────────────────────────────
function rHealth(items){{
  LYR.health.clearLayers();
  (items||[]).forEach(h=>{{
    if(!h.lat||!h.lon)return;
    const col=h.color||'#cc0000';
    L.marker([h.lat,h.lon],{{icon:mkIco('🦠',col,16,h.color&&h.color=='#cc0000'?'pulse':null)}})
     .bindPopup(h.popup||`<b>🦠 ${{esc(h.title||'')}}</b>`).addTo(LYR.health);
  }});
  setFC('health',(items||[]).length);
}}

// ── Sanktions-Treffer ────────────────────────────────────────────────────────
function rSanctions(items){{
  LYR.sanctions.clearLayers();
  (items||[]).forEach(s=>{{
    if(!s.lat||!s.lon)return;
    L.marker([s.lat,s.lon],{{icon:mkIco('⚖️','#cc0000',20,'pulse')}})
     .bindPopup(s.popup||`<b>⚖️ ${{esc(s.title||'')}}</b>`).addTo(LYR.sanctions);
  }});
  setFC('sanctions',(items||[]).length);
}}

// ── BGP-Routing-Anomalien ────────────────────────────────────────────────────
function rBgp(items){{
  LYR.bgp.clearLayers();
  (items||[]).forEach(b=>{{
    if(!b.lat||!b.lon)return;
    const sev = b.severity||b.level||'MITTEL';
    const col = {{KRITISCH:'#ff0044',HOCH:'#ff6600',MITTEL:'#ffaa00',NIEDRIG:'#00cc88'}}[sev]||'#cc44ff';
    const pulse = (sev==='KRITISCH'||sev==='HOCH')?'pulse':null;
    L.marker([b.lat,b.lon],{{icon:mkIco('🌐',col,16,pulse)}})
     .bindPopup(b.popup||`<b>🌐 BGP-Anomalie</b><br>${{esc(b.title||b.description||'')}}<br>
       <span style="color:${{col}}">${{sev}}</span>`)
     .addTo(LYR.bgp);
  }});
  setFC('bgp',(items||[]).length);
}}

// ── Vertreibungs-Tracking ─────────────────────────────────────────────────────
function rDisplacement(items){{
  LYR.displacement.clearLayers();
  (items||[]).forEach(d=>{{
    if(!d.lat||!d.lon)return;
    const n = d.people||d.count||0;
    const col = n>100000?'#ff2200':n>10000?'#ff8800':'#ffcc00';
    L.marker([d.lat,d.lon],{{icon:mkIco('🚶',col,18)}})
     .bindPopup(d.popup||`<b>🚶 Vertreibung</b><br>${{esc(d.title||d.region||'')}}<br>
       Betroffene: <b>${{(n).toLocaleString()||'?'}}</b>`)
     .addTo(LYR.displacement);
  }});
  setFC('displacement',(items||[]).length);
}}

// ── Region setzen ─────────────────────────────────────────────────────────────
let curReg = 'Ukraine';
function setReg(btn, region){{
  curReg = region;
  document.querySelectorAll('.reg-btn').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
  doRefresh();
}}

// ── Haupt-Lade-Funktion ───────────────────────────────────────────────────────
let _loading = false;
async function loadAll(){{
  if(_loading) return;
  _loading = true;
  const stamp = document.getElementById('hdr-time');
  if(stamp) stamp.textContent = '⟳ Lade...';
  try {{
    const resp = await fetch(`${{BASE}}/api/data?query=${{encodeURIComponent(curReg)}}`);
    if(!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
    const d = await resp.json();

    // ── Alle Renderer aufrufen ───────────────────────────────────────────────
    rFusion(d.fusion_events||[]);
    rHumint(d.humint_data||[]);
    rVision(d.vision_data||[]);
    rGeo(d.geo_data||[]);
    rMovement(d.movement_data||[]);
    rWebcam(d.webcam_data||[]);
    rFires(d.fires||[]);
    rAcled(d.acled_events||[]);
    rGdelt(d.gdelt_events||[]);
    rIncidents(d.incidents||[]);
    rLightning(d.lightning||[]);

    const fl = d.flights||{{}};
    rFlights(fl.aircraft||fl.aircraft_list||[], fl.vanishing_aircraft||[]);

    rSar(d.sar_data||[]);
    rNaval(d.naval_data||[]);
    rMilflights(d.milflights||[]);
    rPatrol(d.patrol_anomalies||[]);
    rHF(d.hf_data||[]);
    rGpsjam(d.gpsjam||[]);
    rSeismic(d.seismic_events||[]);
    rNotam(d.notam_data||[]);

    const ais = d.ais||{{}};
    rAis(ais.vessels||ais.vessel_list||[], ais.vanishing_vessels||[]);

    rDraught(d.draught_alerts||[]);
    rRadiation(d.radiation_data||[]);
    rEonet(d.eonet_events||[]);
    rViirs(d.viirs_dark||[]);
    rHealth(d.health_alerts||[]);
    rSanctions(d.sanctions_hits||[]);
    rBgp(d.bgp_anomalies||[]);
    rDisplacement(d.displacement||[]);

    // Heatmap aktualisieren
    buildHeat();

    // Econ-Badge
    const econ = d.economics||{{}};
    const eb = document.getElementById('econ-badge');
    if(eb && econ.oil_usd) {{
      eb.textContent = `🛢️ ${{econ.oil_usd}}$ | 🇺🇦 ${{econ.usd_uah||'?'}} UAH`;
      eb.style.display = 'block';
    }}

    // Zeitstempel
    const ts = d.timestamp || new Date().toLocaleTimeString('de-DE');
    if(stamp) stamp.textContent = `Stand: ${{ts}}`;

  }} catch(e) {{
    console.error('[NEXUS] loadAll Fehler:', e);
    if(stamp) stamp.textContent = `Fehler: ${{e.message}}`;
  }} finally {{
    _loading = false;
  }}
}}

// ── Refresh + Countdown ───────────────────────────────────────────────────────
let _countdown = REFRESH_SEC;
let _timer = null;

function doRefresh(){{
  _countdown = REFRESH_SEC;
  loadAll();
}}

function _tick(){{
  _countdown--;
  const el = document.getElementById('hdr-cd');
  if(el) el.textContent = `↻ ${{_countdown}}s`;
  if(_countdown <= 0){{
    _countdown = REFRESH_SEC;
    loadAll();
  }}
}}

// ── Start ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', ()=>{{
  loadAll();
  _timer = setInterval(_tick, 1000);
}});
</script>
</body>
</html>
"""

