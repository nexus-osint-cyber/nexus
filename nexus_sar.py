"""
NEXUS – Sentinel-1 SAR Ship Detection  (Ebene 4 / Modul 4.11)
==============================================================
ESA Copernicus Sentinel-1 SAR-Radar via Sentinel Hub API:
  • Erkennt Schiffe als Radar-Reflexionen (helle Pixel = Metall)
  • Funktioniert nachts + durch Wolken
  • AIS-unabhängig: auch dunkle Schiffe (Dark Vessels)
  • 6-Tage-Überflug-Zyklus, ~10m Auflösung
  • Kostenlos: 30.000 Processing Units / Monat (Copernicus General)

API-Architektur:
  1. OAuth2 client_credentials → Bearer Token
  2. Catalog API → aktuelle Sentinel-1 Szenen in Region suchen
  3. Process API → SAR-Bild als PNG herunterladen (VV Kanal)
  4. Bild-Analyse → helle Pixel-Cluster = potenzielle Schiffe
  5. Anomalie-Score: Cluster-Dichte vs. Erwartungswert für Region

Sentinel Hub API Endpunkte:
  Token:     identity.dataspace.copernicus.eu/...
  Catalog:   sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search
  Process:   sh.dataspace.copernicus.eu/api/v1/process
  Stats:     sh.dataspace.copernicus.eu/api/v1/statistics

Config (config.py):
  COPERNICUS_CLIENT_ID     = "sh-..."
  COPERNICUS_CLIENT_SECRET = "..."
"""

from __future__ import annotations

import io
import json
import time
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    """Lädt Sentinel Hub Credentials (Env → Config → Hardcoded-Fallback)."""
    import os, importlib

    # 1. Umgebungsvariablen (nicht-leere Werte haben Vorrang)
    env_id  = os.environ.get("COPERNICUS_CLIENT_ID",     "").strip()
    env_sec = os.environ.get("COPERNICUS_CLIENT_SECRET", "").strip()
    if env_id and env_sec:
        return env_id, env_sec

    # 2. config.py – force reload um .pyc-Cache zu umgehen
    try:
        import config as _cfg  # type: ignore
        importlib.reload(_cfg)
        cid = getattr(_cfg, "COPERNICUS_CLIENT_ID",     "").strip()
        sec = getattr(_cfg, "COPERNICUS_CLIENT_SECRET", "").strip()
        if cid and sec:
            return cid, sec
    except Exception:
        pass

    # 3. Kein Fallback — Credentials müssen via config.py oder Umgebungsvariable gesetzt sein
    return (
        env_id  or "",
        env_sec or "",
    )


CLIENT_ID, CLIENT_SECRET = _load_credentials()

SH_TOKEN_URL   = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"
SH_STATS_URL   = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

# Fallback: Alaska SAR Facility (kein Account, nur Metadaten)
ASF_API        = "https://api.daac.asf.alaska.edu/services/search/param"

REQUEST_TIMEOUT = 20


# ─────────────────────────────────────────────────────────────────────────────
# OAuth2 Token (client_credentials flow)
# ─────────────────────────────────────────────────────────────────────────────

_token_cache: dict = {"token": "", "expires": 0.0}


def _get_token() -> str:
    """Holt OAuth2 Bearer Token für Sentinel Hub API."""
    if not CLIENT_ID or not CLIENT_SECRET:
        return ""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"] - 60:
        return _token_cache["token"]
    try:
        body = urllib.parse.urlencode({
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }).encode()
        req = urllib.request.Request(
            SH_TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            j = json.loads(resp.read())
            tok = j.get("access_token", "")
            exp = int(j.get("expires_in", 600))
            _token_cache["token"]   = tok
            _token_cache["expires"] = now + exp
            return tok
    except Exception:
        return ""


def sh_available() -> bool:
    """True wenn Sentinel Hub API konfiguriert und Token holbar."""
    return bool(_get_token())


def sar_status() -> dict:
    """Gibt Status-Dict für test_sar.py und Pipeline zurück."""
    ok = sh_available()
    return {
        "sentinel_hub": {
            "available": ok,
            "message": (
                "✅ Sentinel Hub OAuth2 aktiv (Ship Detection verfügbar)"
                if ok else
                "❌ Sentinel Hub nicht verfügbar – Credentials prüfen"
            ),
        },
        "processing_units": {
            "monthly_free": 30_000,
            "note": "Copernicus General Account – 30.000 PU/Monat kostenlos",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Region → BBox
# ─────────────────────────────────────────────────────────────────────────────

_BBOX: dict[str, tuple[float, float, float, float]] = {
    # ── Übersichtsregionen (2°+ Bbox, 870m/px → RCS-Klasse, kein Typ) ──────────
    "ukraine":          (44.3, 22.1, 52.4, 40.2),
    "naher osten":      (29.0, 34.0, 37.5, 43.5),
    "israel":           (29.5, 34.2, 33.4, 35.9),
    "gaza":             (31.2, 34.0, 31.8, 34.6),
    "rotes meer":       (12.0, 41.0, 22.0, 44.5),
    "jemen":            (12.0, 42.0, 19.0, 54.0),
    "persischer golf":  (23.0, 48.0, 27.5, 56.5),
    "golf":             (23.0, 48.0, 27.5, 56.5),
    "hormuz":           (25.5, 56.0, 27.5, 59.5),
    "hormuz-strasse":   (25.5, 56.0, 27.5, 59.5),
    "taiwan":           (21.5, 118.0, 25.5, 122.5),
    "ostsee":           (53.0, 9.0,  66.0, 30.0),
    "nordsee":          (51.0, -4.0, 61.0, 10.0),
    "schwarzes meer":   (40.0, 27.0, 47.0, 42.0),
    "mittelmeer":       (30.0, -6.0, 46.0, 37.0),
    "suez":             (29.5, 31.5, 31.5, 33.5),
    "syrien":           (32.3, 35.7, 37.3, 42.4),
    "taiwan-strasse":   (23.0, 119.0, 25.5, 121.5),

    # ── Zoom-Regionen: 0.15° → 65m/px → Schiffsgröße grob bestimmbar ──────────
    # Empfehlung: für Typerkennung (Tanker vs. Fregatte vs. U-Boot)
    "hormuz-zoom":      (26.35, 56.15, 26.50, 56.35),  # Hauptfahrwasser Westeingang
    "suez-zoom":        (30.85, 32.30, 31.00, 32.50),   # Suezkanal-Einfahrt Nord
    "taiwan-zoom":      (24.90, 120.90, 25.05, 121.10), # Taiwan-Straße Mitte
    "ostsee-zoom":      (54.90, 12.80, 55.05, 13.00),   # Fehmarnbelt

    # ── Fine-Zoom: 0.05° → 22m/px → zuverlässige Form+Längen-Klassifikation ────
    # Sentinel-1 native ~10m/px, 0.05° gibt ~22m/px (beste Auflösung mit 256px)
    "hormuz-fine":      (26.42, 56.22, 26.47, 56.27),   # 0.05°: Enge Einfahrt
    "suez-fine":        (30.88, 32.33, 30.93, 32.38),   # 0.05°: Suezkanal schmal
    "taiwan-fine":      (24.93, 120.93, 24.98, 120.98), # 0.05°: Straße Mitte
}

# Zoom-Stufen und ihre Auflösung (Info für User)
_ZOOM_INFO: dict[str, str] = {
    "hormuz-zoom":   "65m/px – Größenklasse erkennbar (Tanker vs. Fregatte)",
    "suez-zoom":     "65m/px – Größenklasse erkennbar",
    "taiwan-zoom":   "65m/px – Größenklasse erkennbar",
    "ostsee-zoom":   "65m/px – Größenklasse erkennbar",
    "hormuz-fine":   "22m/px – Formklassifikation (Typ, Länge, Elongation)",
    "suez-fine":     "22m/px – Formklassifikation",
    "taiwan-fine":   "22m/px – Formklassifikation",
}


def _region_bbox(region: str) -> Optional[tuple[float, float, float, float]]:
    """
    lat_min, lon_min, lat_max, lon_max

    Unterstützt zusätzlich Koordinaten-Zoom-Syntax:
      "zoom:lat,lon"           → 0.1°×0.1° Box (43m/px) – Standardzoom
      "zoom:lat,lon,0.05"      → 0.05°×0.05° Box (22m/px) – Fein
      "zoom:lat,lon,0.2"       → 0.2°×0.2° Box (87m/px) – Grob

    Beispiele:
      zoom:26.45,56.25         → Hormuz Hauptfahrwasser
      zoom:26.45,56.25,0.05    → Hormuz Fein (22m/px)
    """
    r = region.lower().strip()

    # Koordinaten-Zoom: "zoom:lat,lon" oder "zoom:lat,lon,size"
    if r.startswith("zoom:"):
        try:
            parts = r[5:].split(",")
            lat   = float(parts[0])
            lon   = float(parts[1])
            size  = float(parts[2]) if len(parts) > 2 else 0.1   # Standard 0.1°
            size  = max(0.01, min(size, 2.0))                     # Clamp 0.01°–2°
            half  = size / 2
            return (lat - half, lon - half, lat + half, lon + half)
        except (ValueError, IndexError):
            pass

    # Reguläre Benennungen: exakter Match zuerst, dann Substring
    # (ohne Prio würde "hormuz" auf "hormuz-zoom" anschlagen)
    if r in _BBOX:
        return _BBOX[r]
    for key, bbox in _BBOX.items():
        if key in r or r in key:
            return bbox

    # Fallback: nexus_ais._region_to_bbox
    try:
        from nexus_ais import _region_to_bbox  # type: ignore
        return _region_to_bbox(region)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Catalog API – Sentinel-1 Szenen suchen
# ─────────────────────────────────────────────────────────────────────────────

def _catalog_search(
    lat_min: float, lon_min: float,
    lat_max: float, lon_max: float,
    days_back: int = 12,
    max_items: int = 6,
) -> list[dict]:
    """
    Sucht aktuelle Sentinel-1 GRD Szenen via Sentinel Hub Catalog API.
    Gibt Szenen-Metadaten zurück (ID, Datum, Orbit, Footprint).
    """
    token = _get_token()
    if not token:
        return []

    now_utc = datetime.now(timezone.utc)
    from_dt = (now_utc - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")
    to_dt   = now_utc.strftime("%Y-%m-%dT23:59:59Z")

    payload = json.dumps({
        "bbox":        [lon_min, lat_min, lon_max, lat_max],
        "datetime":    f"{from_dt}/{to_dt}",
        "collections": ["sentinel-1-grd"],
        "limit":       max_items,
        "fields": {
            "include": ["id", "properties.datetime", "properties.sat:orbit_state",
                        "geometry", "bbox"],
        },
    }).encode()

    req = urllib.request.Request(
        SH_CATALOG_URL,
        data=payload,
        headers={
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
            "Accept":         "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read())
        return data.get("features") or []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Process API – SAR-Bild für Ship Detection
# ─────────────────────────────────────────────────────────────────────────────

# EvalScript: VV Kanal in dB-Skala (logarithmisch)
# Physik: Wasser = -20 bis -15 dB, Schiff = -5 bis +10 dB (Metallreflexion)
# Mapping: [-30 dB, 0 dB] → [0, 255]
# Schiffe erscheinen als sehr helle Punkte/Cluster (Pixel > 130)
_SHIP_DETECTION_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV"], units: "LINEAR_POWER" }],
    output: { bands: 1, sampleType: "UINT8" }
  };
}
function evaluatePixel(sample) {
  var vv = sample.VV;
  if (vv <= 0) return [0];
  // Dezibel: dB = 10 * log10(linear_power)
  var db = 10.0 * Math.log(vv) / Math.LN10;
  // Skalierung: -30 dB = 0, 0 dB = 255
  // Wasser: typisch -20 bis -15 dB → Pixel 85-127
  // Schiff:  typisch  -8 bis +5 dB → Pixel 183-255  (klar erkennbar)
  var scaled = (db + 30.0) / 30.0;
  return [Math.round(Math.max(0, Math.min(255, scaled * 255)))];
}
"""

# Schwellenwert: Pixel > SHIP_THRESHOLD = potenzielle Schiffsreflexion
# dB-Physik: Wasser = -20 bis -15 dB → Pixel 85-127
#            Land/Vegetation = -10 bis -5 dB → Pixel 153-212
#            Schiffe (Metall) = -8 bis +5 dB → Pixel 183-255
# 180 px = -9.6 dB: deutlich über Meeresrauschen, unter Land-Wolken-Clutter
SHIP_THRESHOLD  = 180    # SAR-Schiff-Schwellenwert: ≥ −9.6 dB (Metall-Reflexion)
MIN_CLUSTER_PX  = 1      # Schiffe können bei großer Bbox sub-pixel erscheinen


def _process_sar_tile(
    lat_min: float, lon_min: float,
    lat_max: float, lon_max: float,
    scene_id: Optional[str] = None,
    size: int = 256,
) -> Optional[bytes]:
    """
    Holt SAR-Bild (VV-Kanal) vom Sentinel Hub Process API.
    Gibt PNG-Bytes zurück oder None bei Fehler.
    Processing Units-Kosten: ~256x256 Pixel = ~1 PU (von 30.000/Monat)
    """
    token = _get_token()
    if not token:
        return None

    now_utc = datetime.now(timezone.utc)
    from_dt = (now_utc - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
    to_dt   = now_utc.strftime("%Y-%m-%dT23:59:59Z")

    # Begrenze die Fläche auf max 2° × 2° um PUs zu sparen
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
    if lat_span > 2.0:
        lat_mid = (lat_min + lat_max) / 2
        lat_min, lat_max = lat_mid - 1.0, lat_mid + 1.0
    if lon_span > 2.0:
        lon_mid = (lon_min + lon_max) / 2
        lon_min, lon_max = lon_mid - 1.0, lon_mid + 1.0

    payload: dict = {
        "input": {
            "bounds": {
                "bbox": [lon_min, lat_min, lon_max, lat_max],
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{
                "type": "sentinel-1-grd",
                "dataFilter": {
                    # Nur Zeitfilter – keine acquisitionMode/polarization-Filter,
                    # da diese bei Sentinel-1C andere Werte haben können.
                    # Debug-Script hat ohne diese Filter einwandfrei funktioniert.
                    "timeRange": {"from": from_dt, "to": to_dt},
                },
                "processing": {
                    "orthorectify": True,
                    "backCoeff":    "GAMMA0_TERRAIN",
                },
            }],
        },
        "output": {
            "width":  size,
            "height": size,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": _SHIP_DETECTION_EVALSCRIPT,
    }

    if scene_id:
        payload["input"]["data"][0]["dataFilter"]["mosaickingOrder"] = "mostRecent"

    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            SH_PROCESS_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
                "Accept":        "image/png",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Ship Detection aus SAR-Bild
# ─────────────────────────────────────────────────────────────────────────────

def _detect_ships_in_sar(png_bytes: bytes,
                          lat_min: float, lon_min: float,
                          lat_max: float, lon_max: float,
                          threshold: int = SHIP_THRESHOLD,
                          min_cluster_px: int = MIN_CLUSTER_PX,
                          ) -> list[dict]:
    """
    Analysiert SAR-PNG (dB-skaliert) auf helle Pixel-Cluster = Schiffe.

    dB-Skala: -30dB=0px, 0dB=255px
    Wasser:  -20 bis -15 dB → Pixel 85-127  (unter Schwellenwert)
    Schiff:   -8 bis  +5 dB → Pixel 183-255 (deutlich über Schwellenwert)
    Schwellenwert 130 ≈ -13.7 dB (klar über Meereshintergrund)

    Integriert nexus_sar_classify für Zieltyp-Bestimmung.
    """
    try:
        from PIL import Image
    except ImportError:
        return []

    try:
        # T170: Bildverbesserung vor Detection
        enhanced = _enhance_sar_image(png_bytes)
        img  = Image.open(io.BytesIO(enhanced)).convert("L")
        w, h = img.size
        pix  = list(img.getdata())
    except Exception:
        return []

    # Geo-Ausdehnung des Bildausschnitts (begrenzt auf max 2°×2°)
    lon_span = min(lon_max - lon_min, 2.0)
    lat_span = min(lat_max - lat_min, 2.0)

    # T170: Adaptive CFAR-Maske (fällt auf festen Threshold zurück wenn Bild zu klein)
    if w >= 64 and h >= 64:
        mask = _cfar_threshold_map(pix, w, h, guard=2, win=8, scale=2.5,
                                    floor=threshold)
    else:
        mask = [1 if v >= threshold else 0 for v in pix]

    # Cluster-Labeling (4-Nachbarschaft, iterativ)
    labels = [0] * (w * h)
    current_label = 0
    clusters: dict[int, list[int]] = {}

    def flood_fill(start: int, label: int) -> None:
        stack = [start]
        while stack:
            idx = stack.pop()
            if idx < 0 or idx >= w * h or labels[idx] or not mask[idx]:
                continue
            labels[idx] = label
            clusters.setdefault(label, []).append(idx)
            row, col = divmod(idx, w)
            if col > 0:     stack.append(idx - 1)
            if col < w - 1: stack.append(idx + 1)
            if row > 0:     stack.append(idx - w)
            if row < h - 1: stack.append(idx + w)

    for i in range(w * h):
        if mask[i] and not labels[i]:
            current_label += 1
            flood_fill(i, current_label)

    # Koordinaten-Mapping
    lat_range = lat_max - lat_min
    lon_range = lon_max - lon_min

    # Classification-Modul (optional, fällt still zurück wenn nicht verfügbar)
    try:
        from nexus_sar_classify import full_classify  # type: ignore
        use_classify = True
    except ImportError:
        use_classify = False

    ships = []
    for lbl, pixels in clusters.items():
        if len(pixels) < min_cluster_px:
            continue
        rows    = [p // w for p in pixels]
        cols    = [p %  w for p in pixels]
        cen_row = sum(rows) / len(rows)
        cen_col = sum(cols) / len(cols)

        lat = lat_max - (cen_row / h) * lat_range
        lon = lon_min + (cen_col / w) * lon_range

        brightness  = sum(pix[p] for p in pixels) / len(pixels)
        size_score  = min(len(pixels) / 15, 1.0)
        bright_score = max((brightness - threshold) / (255 - threshold), 0.0)
        confidence  = round(min(0.35 * size_score + 0.65 * bright_score, 0.95), 2)

        entry: dict = {
            "lat":        round(lat, 4),
            "lon":        round(lon, 4),
            "size_px":    len(pixels),
            "brightness": round(brightness, 1),
            "confidence": confidence,
            "source":     "Sentinel-1 SAR",
        }

        # Cluster-Größenfilter: Schiffe sind kleine, isolierte Reflexionen.
        # Große Cluster = Landmasse (küstennahe Regionen wie Hormuz haben viel Land).
        # Berechne max. Schiffsgröße in Pixeln basierend auf Auflösung.
        m_per_px = max(lon_span, lat_span) * 111_320 / w
        # Größtes Schiff: ~400m. Cluster größer als das = kein Schiff, sondern Land.
        max_ship_px = max(4, int(400 / m_per_px) + 3) if m_per_px > 0 else 200
        if len(pixels) > max_ship_px:
            continue  # Land-Cluster verwerfen

        # Zieltyp-Klassifikation
        if use_classify and len(pixels) >= 1:
            try:
                clf = full_classify(
                    pixels, brightness,
                    img_width    = w,
                    img_lon_span = lon_span,
                    img_lat_span = lat_span,
                    with_db_compare = True,
                    db_top_n     = 3,
                )
                entry["category"]        = clf.category
                entry["subcategory"]     = clf.subcategory
                entry["length_m"]        = clf.length_m
                entry["width_m"]         = clf.width_m
                entry["aspect_ratio"]    = clf.aspect_ratio
                entry["rcs_class"]       = clf.rcs_class
                entry["shape_note"]      = clf.shape_note
                entry["clf_confidence"]  = clf.confidence
                entry["possible_classes"]= clf.possible_classes
            except Exception:
                pass

        ships.append(entry)  # ← Schiff zur Ergebnisliste hinzufügen

    # Sortiere nach Confidence, begrenze auf 30
    ships.sort(key=lambda x: x["confidence"], reverse=True)
    return ships[:30]


# ─────────────────────────────────────────────────────────────────────────────
# T170: SAR-Bildverbesserung + Adaptive CFAR-Schwelle + LLaVA-Analyse
# ─────────────────────────────────────────────────────────────────────────────

def _enhance_sar_image(png_bytes: bytes) -> bytes:
    """
    Verbessert SAR-PNG für präzisere Schiffserkennung:
      1. Histogramm-Stretch (1%/99%-Perzentile)
      2. Gamma-Korrektur (γ=0.6) → hebt schwache Schiffsreflexionen
      3. Kontrast-Boost (1.4×)
    Gibt ursprüngliche Bytes zurück wenn PIL fehlt oder Fehler.
    """
    try:
        from PIL import Image, ImageEnhance
        import io as _io
        img = Image.open(_io.BytesIO(png_bytes)).convert("L")
        pix = list(img.getdata())
        n = len(pix)
        # 1. Histogramm-Stretch: 1% Low-Clip, 99% High-Clip
        sorted_pix = sorted(pix)
        lo = sorted_pix[max(0, int(n * 0.01))]
        hi = sorted_pix[min(n - 1, int(n * 0.99))]
        if hi > lo:
            stretched = [
                min(255, max(0, int((v - lo) * 255 / (hi - lo))))
                for v in pix
            ]
            img.putdata(stretched)
        # 2. Gamma-Korrektur (γ=0.6 hellt dunkle Reflexionen auf)
        gamma_table = [min(255, int(255 * (i / 255) ** 0.6)) for i in range(256)]
        img = img.point(gamma_table)
        # 3. Kontrast-Boost
        img = ImageEnhance.Contrast(img).enhance(1.4)
        out = _io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return png_bytes


def _cfar_threshold_map(pix: list[int], w: int, h: int,
                         guard: int = 2, win: int = 8,
                         scale: float = 2.5,
                         floor: int = 130) -> list[int]:
    """
    CA-CFAR (Cell-Averaging Constant False Alarm Rate).
    Berechnet für jeden Pixel einen adaptiven Schwellenwert basierend auf
    dem lokalen Hintergrund-Mittelwert (Sliding-Window mit Guard-Cells).

    Schwellenwert = max(bg_mean × scale, floor)

    Vorteile gegenüber festem Threshold:
    - Robuster gegen Meereshintergrund-Variationen (Wind/Wellen)
    - Geringere False-Positives bei Küsten-Helligkeitsübergängen
    - Höhere Erkennungsrate für schwache Ziele

    Verwendet 2D-Prefix-Summen für O(N) Gesamtkomplexität.
    Gibt binäre Maske zurück (1=Schiff-Kandidat, 0=Hintergrund).
    """
    # 2D Prefix-Summe (0-basiert, um Randbedingungen zu vereinfachen)
    # psum[r+1][c+1] = Summe aller Pixel in [0..r] × [0..c]
    pw = w + 1
    psum = [0] * (pw * (h + 1))
    for row in range(h):
        for col in range(w):
            psum[(row + 1) * pw + (col + 1)] = (
                pix[row * w + col]
                + psum[row * pw + (col + 1)]
                + psum[(row + 1) * pw + col]
                - psum[row * pw + col]
            )

    def rect_sum_n(r1: int, c1: int, r2: int, c2: int):
        """Summe + Pixelanzahl im Rechteck [r1,r2]×[c1,c2] (inklusiv, geclampt)."""
        r1 = max(0, r1); c1 = max(0, c1)
        r2 = min(h - 1, r2); c2 = min(w - 1, c2)
        if r1 > r2 or c1 > c2:
            return 0, 0
        s = (psum[(r2 + 1) * pw + (c2 + 1)]
             - psum[r1 * pw + (c2 + 1)]
             - psum[(r2 + 1) * pw + c1]
             + psum[r1 * pw + c1])
        return s, (r2 - r1 + 1) * (c2 - c1 + 1)

    mask = []
    for row in range(h):
        for col in range(w):
            v = pix[row * w + col]
            s_out, n_out = rect_sum_n(row - win, col - win, row + win, col + win)
            s_in,  n_in  = rect_sum_n(row - guard, col - guard,
                                       row + guard, col + guard)
            n_bg = n_out - n_in
            if n_bg > 4:
                bg_mean = (s_out - s_in) / n_bg
                threshold = max(bg_mean * scale, float(floor))
            else:
                threshold = float(floor)
            mask.append(1 if v >= threshold else 0)

    return mask


def _llava_sar_analyze(png_bytes: bytes, region: str, ship_count: int) -> str:
    """
    Sendet SAR-PNG an lokales Ollama LLaVA-Modell zur Schiffstyp-Interpretation.
    Gibt leerstring zurück wenn Ollama nicht erreichbar oder Fehler.

    Analysiert:
    - Schiffsformationen und -konzentration
    - Geschätzte Schiffstypen (Tanker, Kriegsschiff, Container, Klein)
    - Ungewöhnliche Muster (Konvoi, Ankergruppe, Dunkelschiff-Lücken)
    """
    try:
        import base64
        b64 = base64.b64encode(png_bytes).decode("ascii")
        prompt = (
            f"This is a Sentinel-1 SAR (Synthetic Aperture Radar) satellite image "
            f"of the {region} area. Bright pixels = high radar backscatter = metal "
            f"surfaces = ships. {ship_count} ship-like targets were algorithmically "
            f"detected.\n\n"
            "Briefly analyze (max 80 words, operational intel style):\n"
            "1. Ship formations or density patterns\n"
            "2. Likely ship types (tanker, warship, container, fishing)\n"
            "3. Unusual patterns: convoy, anchored group, dark vessel gaps\n"
            "4. Any anomalies worth noting"
        )
        payload = json.dumps({
            "model":   "llava",
            "prompt":  prompt,
            "images":  [b64],
            "stream":  False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read())
        note = body.get("response", "").strip()
        return note if note else ""
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# ASF Alaska Fallback (kein Account nötig)
# ─────────────────────────────────────────────────────────────────────────────

def _asf_search(lat_min: float, lon_min: float,
                lat_max: float, lon_max: float,
                days: int = 12) -> list[dict]:
    """Alaska SAR Facility – Szenen-Metadaten, kein Account."""
    params = {
        "platform":       "Sentinel-1",
        "processingLevel":"GRD_HD",
        "start":          (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00UTC"),
        "end":            datetime.utcnow().strftime("%Y-%m-%dT23:59:59UTC"),
        "intersectsWith": (f"POLYGON(({lon_min} {lat_min},{lon_max} {lat_min},"
                           f"{lon_max} {lat_max},{lon_min} {lat_max},{lon_min} {lat_min}))"),
        "maxResults":     6,
        "output":         "json",
    }
    try:
        url = ASF_API + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        results = []
        for item in data:
            for scene in (item if isinstance(item, list) else [item]):
                results.append({
                    "scene_id": scene.get("granuleName", ""),
                    "date":     scene.get("startTime", "")[:10],
                    "lat":      float(scene.get("centerLat", (lat_min + lat_max) / 2)),
                    "lon":      float(scene.get("centerLon", (lon_min + lon_max) / 2)),
                    "source":   "ASF/Alaska",
                })
        return results
    except Exception:
        return []


def _eo_browser_link(lat: float, lon: float, date: str = "") -> str:
    """Direkter EO-Browser-Link für SAR Quickview (kein Account)."""
    if not date:
        date = datetime.utcnow().strftime("%Y-%m-%d")
    return (
        f"https://apps.sentinel-hub.com/eo-browser/?zoom=11"
        f"&lat={lat:.4f}&lng={lon:.4f}"
        f"&datasetId=S1_GRD_IW"
        f"&fromTime={date}T00%3A00%3A00.000Z"
        f"&toTime={date}T23%3A59%3A59.999Z"
        f"&layerId=SAR-INTENSITY"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Öffentliche API
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SarShipDetection:
    """Ergebnis einer SAR-Schiffsanalyse für eine Region."""
    region:        str
    scene_date:    str
    scene_id:      str
    ships:         list[dict]     # [{lat, lon, confidence, size_px, ...}]
    ship_count:    int
    anomaly_score: float          # 0-1: wie viel Aktivität relativ zur Region
    method:        str            # "sentinel-hub" | "asf-fallback"
    eo_link:       str            # Link zum EO Browser
    lat:           float          # Zentrum
    lon:           float
    description:   str
    llava_analysis: str = field(default="")  # T170: LLaVA-Schiffsinterpretation


def detect_ships(region: str, max_ships: int = 20) -> SarShipDetection:
    """
    Hauptfunktion: SAR Ship Detection für eine Region.

    Mit Sentinel Hub API:
      → Szene suchen → SAR-Bild holen → Helle Cluster = Schiffe
    Ohne Key:
      → ASF Metadaten + EO Browser Links (kein Bild-Download)
    """
    bbox = _region_bbox(region)
    if not bbox:
        lat_c, lon_c = 51.0, 10.0
        return _empty_detection(region, "Region nicht erkannt", lat_c, lon_c)

    lat_min, lon_min, lat_max, lon_max = bbox
    lat_c = (lat_min + lat_max) / 2
    lon_c = (lon_min + lon_max) / 2

    # ── Weg 1: Sentinel Hub (mit Credentials) ────────────────────────────────
    if sh_available():
        # Szene finden
        scenes = _catalog_search(lat_min, lon_min, lat_max, lon_max,
                                 days_back=14, max_items=3)
        scene_id   = ""
        scene_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if scenes:
            s          = scenes[0]
            scene_id   = s.get("id", "")
            scene_date = (s.get("properties", {}).get("datetime") or scene_date)[:10]

        # Auflösung berechnen (clipped wie in _process_sar_tile)
        lon_span = min(lon_max - lon_min, 2.0)
        lat_span = min(lat_max - lat_min, 2.0)
        m_per_px = round(max(lon_span, lat_span) * 111_320 / 256)
        if m_per_px > 200:
            res_info = f"{m_per_px}m/px (Übersicht – nur RCS-Klasse)"
        elif m_per_px > 50:
            res_info = f"{m_per_px}m/px (Zoom – Größenklasse erkennbar)"
        else:
            res_info = f"{m_per_px}m/px (Fein – Typ/Form klassifizierbar)"

        # SAR-Bild holen und analysieren
        png = _process_sar_tile(lat_min, lon_min, lat_max, lon_max,
                                scene_id=scene_id, size=256)
        ships: list[dict] = []
        if png and len(png) > 500:
            ships = _detect_ships_in_sar(
                png, lat_min, lon_min, lat_max, lon_max,
                threshold=SHIP_THRESHOLD, min_cluster_px=MIN_CLUSTER_PX,
            )

        # T170: LLaVA Schiffstyp-Analyse (nur wenn SAR-Bild + Schiffe vorhanden)
        llava_note = ""
        if png and ships:
            llava_note = _llava_sar_analyze(png, region, len(ships))

        # Anomalie-Score: Schiffsdichte für Regionstyp
        expected = {"ostsee": 8, "nordsee": 12, "mittelmeer": 10,
                    "rotes meer": 4, "hormuz": 6, "suez": 5}.get(
            region.lower(), 5)
        anomaly = min(len(ships) / max(expected, 1), 1.0)

        return SarShipDetection(
            region         = region,
            scene_date     = scene_date,
            scene_id       = scene_id,
            ships          = ships[:max_ships],
            ship_count     = len(ships),
            anomaly_score  = round(anomaly, 2),
            method         = "sentinel-hub",
            eo_link        = _eo_browser_link(lat_c, lon_c, scene_date),
            lat            = lat_c,
            lon            = lon_c,
            description    = (f"SAR {scene_date}: {len(ships)} Ziele erkannt"
                              f" | Auflösung: {res_info}"),
            llava_analysis = llava_note,
        )

    # ── Weg 2: ASF Fallback (nur Metadaten) ──────────────────────────────────
    asf_scenes = _asf_search(lat_min, lon_min, lat_max, lon_max, days=12)
    scene_date = asf_scenes[0]["date"] if asf_scenes else datetime.utcnow().strftime("%Y-%m-%d")
    eo_link    = _eo_browser_link(lat_c, lon_c, scene_date)

    return SarShipDetection(
        region        = region,
        scene_date    = scene_date,
        scene_id      = asf_scenes[0].get("scene_id", "") if asf_scenes else "",
        ships         = [],
        ship_count    = 0,
        anomaly_score = 0.0,
        method        = "asf-fallback",
        eo_link       = eo_link,
        lat           = lat_c,
        lon           = lon_c,
        description   = (f"Sentinel-1 Szene {scene_date} verfügbar – "
                         "Ship Detection benötigt Sentinel Hub Credentials"),
    )


def sar_for_map(region: str = "Nordsee") -> list[dict]:
    """
    Gibt Karten-Marker für SAR-erkannte Schiffe zurück.
    Kompatibel mit nexus_report.py Karten-Format.
    """
    result = detect_ships(region)
    markers = []

    if result.method == "sentinel-hub" and result.ships:
        for s in result.ships:
            conf  = s["confidence"]
            cat   = s.get("category", "Unbekannt")
            subcat= s.get("subcategory", "")
            rcs   = s.get("rcs_class", "")

            # Farbe nach Kategorie + Konfidenz
            cat_lower = cat.lower()
            if "träger" in cat_lower or "flugzeug" in cat_lower:
                color = "#cc0000"      # rot: Träger
            elif "u-boot" in cat_lower:
                color = "#660099"      # violett: U-Boot
            elif "kriegs" in cat_lower or "zerstörer" in cat_lower:
                color = "#ff4400"      # orange-rot: Kriegsschiff
            elif "drohne" in cat_lower or "usv" in cat_lower:
                color = "#ff00ff"      # magenta: Drohne
            elif conf > 0.7:
                color = "#ff8800"
            else:
                color = "#ffbb00"

            # Klassen-Vergleich formatieren
            top_classes = s.get("possible_classes", [])[:3]
            classes_html = ""
            if top_classes:
                classes_html = "<br><b>Mögliche Klassen:</b><br>"
                for tc in top_classes:
                    bar = "█" * int(tc["match"] * 10)
                    classes_html += (
                        f"&nbsp;{tc['match']:.0%} {bar} {tc['class']} "
                        f"[{tc['nation']}]<br>"
                        f"&nbsp;&nbsp;<small>{tc['note']}</small><br>"
                    )

            dim_str = ""
            if s.get("length_m"):
                dim_str = f"~{s['length_m']:.0f}m × {s.get('width_m',0):.0f}m"

            markers.append({
                "lat":      s["lat"],
                "lon":      s["lon"],
                "type":     "sar-ship",
                "icon":     "🛰",
                "color":    color,
                "category": cat,
                "title":    f"🛰 {cat} [{conf:.0%}]",
                "popup":    (
                    f"<b>🛰 SAR Ziel-Klassifikation</b><br>"
                    f"<b>Typ:</b> {cat}<br>"
                    f"<b>Subtyp:</b> {subcat}<br>"
                    f"<b>Größe:</b> {dim_str or str(s['size_px']) + 'px'}<br>"
                    f"<b>RCS:</b> {rcs} ({s['brightness']:.0f}/255 dB-Skala)<br>"
                    f"<b>Konfidenz:</b> {conf:.0%}<br>"
                    f"<b>Hinweis:</b> {s.get('shape_note', '')}<br>"
                    f"AIS-Status: unbekannt (SAR-only)<br>"
                    f"Datum: {result.scene_date}<br>"
                    f"{classes_html}"
                    + (f"<hr style='border-color:#1e3a4a;margin:4px 0'>"
                       f"<b>🤖 LLaVA-Analyse:</b><br>"
                       f"<small style='color:#aac8e0'>{result.llava_analysis}</small><br>"
                       if result.llava_analysis else "")
                    + f"<a href='{result.eo_link}' target='_blank'>EO Browser öffnen</a>"
                ),
                "source":   "Sentinel-1 SAR",
            })
    else:
        # Kein Bild → einzelner Szenen-Marker mit EO Browser Link
        markers.append({
            "lat":   result.lat,
            "lon":   result.lon,
            "type":  "sar",
            "icon":  "🛰",
            "color": "#6644cc",
            "title": f"🛰 Sentinel-1 SAR {result.scene_date}",
            "popup": (
                f"SAR Szene {result.scene_date}<br>"
                f"Methode: {result.method}<br>"
                f"<a href='{result.eo_link}' target='_blank'>EO Browser oeffnen</a>"
            ),
            "source": "Sentinel-1 SAR",
        })

    return markers


def sar_for_report(region: str = "Hormuz") -> dict:
    """
    Gibt SAR-Ergebnis als Report-Dict zurueck.
    Kompatibel mit nexus_report.py Pipeline-Format.
    """
    result = detect_ships(region)
    return {
        "region":       result.region,
        "scene_date":   result.scene_date,
        "ship_count":   result.ship_count,
        "anomaly":      result.anomaly_score,
        "method":       result.method,
        "ships":        result.ships,
        "eo_link":      result.eo_link,
        "description":  result.description,
        "lat":          result.lat,
        "lon":          result.lon,
    }
                           