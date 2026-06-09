"""
nexus_frontline.py – Frontlinien-Daten (GeoJSON)

Lädt tagesaktuelle Frontlinien aus öffentlichen OSINT-Quellen:
  1. DeepStateMap.live API  (authoritative, täglich aktualisiert)
  2. ISW GitHub GeoJSON     (Institute for the Study of War)
  3. Community-Tracking     (GitHub-basiert, mehrere Repos)

Gibt GeoJSON-Feature-Collection zurück, die direkt in Leaflet
als L.geoJSON()-Layer geladen werden kann.

WARUM WICHTIG:
  Ohne Frontliniendaten sind GPS-Koordinaten kontextlos.
  Ein Angriff 5km vor der Frontlinie ist ein Vorstoß.
  Derselbe Angriff 50km dahinter ist ein Tiefenangriff.
  Das ist der Unterschied zwischen "Ereignis" und "Bedeutung".
"""
from __future__ import annotations

import json
import os
import time
import threading
from datetime import datetime
from typing import Optional

# ── Cache-Einstellungen ───────────────────────────────────────────────────────
_CACHE_DIR   = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE  = os.path.join(_CACHE_DIR, ".nexus_frontline_cache.json")
_CACHE_TTL   = 4 * 3600   # 4 Stunden – Frontlinie ändert sich nicht minütlich

# ── Datenquellen (in Prioritätsreihenfolge) ───────────────────────────────────
_SOURCES = [
    # DeepStateMap – beste Quelle, täglich von ukrainischen Analysten gepflegt
    {
        "name":    "DeepStateMap",
        "url":     "https://deepstatemap.live/api/history/last",
        "type":    "deepstate",
        "timeout": 10,
    },
    # ISW GitHub – GeoJSON der US-Kriegsforschungsgruppe
    {
        "name":    "ISW GitHub",
        "url":     "https://raw.githubusercontent.com/Institute-for-the-Study-of-War/ukraine-maps/main/data/ukraine-frontline.geojson",
        "type":    "geojson",
        "timeout": 8,
    },
    # Community-Frontlinie auf GitHub
    {
        "name":    "Community GeoJSON",
        "url":     "https://raw.githubusercontent.com/simonwoerpel/ukr-geo/main/frontline/frontline.geojson",
        "type":    "geojson",
        "timeout": 8,
    },
    # Alternative Community-Quelle
    {
        "name":    "UA Frontline Watch",
        "url":     "https://raw.githubusercontent.com/wartranslated/ukraine_frontline/main/frontline.geojson",
        "type":    "geojson",
        "timeout": 8,
    },
]


def _load_cache() -> Optional[dict]:
    """Lädt gecachte Frontliniendaten wenn noch aktuell."""
    try:
        if not os.path.exists(_CACHE_FILE):
            return None
        age = time.time() - os.path.getmtime(_CACHE_FILE)
        if age > _CACHE_TTL:
            return None
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _fetch_deepstate(url: str, timeout: int) -> Optional[dict]:
    """Parst DeepStateMap API-Antwort in GeoJSON."""
    import requests as _req
    try:
        r = _req.get(url, timeout=timeout,
                     headers={"User-Agent": "NEXUS-OSINT/1.0 (educational)"})
        r.raise_for_status()
        data = r.json()
        # DeepState gibt {"id":..., "date":..., "geojson": {...}} zurück
        geojson = data.get("geojson") or data
        if geojson.get("type") in ("FeatureCollection", "Feature",
                                    "LineString", "MultiLineString"):
            # Sicherstellen dass es eine FeatureCollection ist
            if geojson["type"] == "FeatureCollection":
                for feat in geojson.get("features", []):
                    feat.setdefault("properties", {})["source"] = "DeepStateMap"
                    feat.setdefault("properties", {})["date"] = data.get("date", "")
            return geojson
    except Exception:
        pass
    return None


def _fetch_geojson(url: str, timeout: int, source_name: str) -> Optional[dict]:
    """Lädt Standard-GeoJSON."""
    import requests as _req
    try:
        r = _req.get(url, timeout=timeout,
                     headers={"User-Agent": "NEXUS-OSINT/1.0 (educational)"})
        r.raise_for_status()
        data = r.json()
        # Properties mit Quellinfo anreichern
        if data.get("type") == "FeatureCollection":
            for feat in data.get("features", []):
                feat.setdefault("properties", {})["source"] = source_name
        return data
    except Exception:
        pass
    return None


def fetch_frontline(force_refresh: bool = False) -> Optional[dict]:
    """
    Hauptfunktion: Lädt Frontlinien-GeoJSON.

    Returns:
        GeoJSON FeatureCollection oder None wenn keine Quelle erreichbar.
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    for src in _SOURCES:
        try:
            if src["type"] == "deepstate":
                result = _fetch_deepstate(src["url"], src["timeout"])
            else:
                result = _fetch_geojson(src["url"], src["timeout"], src["name"])

            if result:
                # Metadaten hinzufügen
                if isinstance(result, dict):
                    result["_nexus_source"]   = src["name"]
                    result["_nexus_fetched"]  = datetime.utcnow().isoformat()
                _save_cache(result)
                return result
        except Exception:
            continue

    return None


def frontline_for_map() -> str:
    """
    Gibt die Frontlinien-Daten als JSON-String zurück,
    direkt nutzbar in Leaflet: L.geoJSON(JSON.parse(frontlineData))
    """
    data = fetch_frontline()
    if data:
        return json.dumps(data)
    return "null"


def frontline_summary() -> str:
    """Kurzzusammenfassung für Terminal-Ausgabe."""
    data = fetch_frontline()
    if not data:
        return "[FRONTLINE] Keine Daten verfügbar (alle Quellen offline)"
    source = data.get("_nexus_source", "unbekannt")
    fetched = data.get("_nexus_fetched", "")[:16]
    n_feat = len(data.get("features", []))
    return f"[FRONTLINE] {source} – {n_feat} Features geladen ({fetched} UTC)"


def get_frontline_layer_js() -> str:
    """
    Gibt JavaScript-Code zurück der den Frontlinien-Layer
    in die Leaflet-Karte einbaut.
    """
    data = fetch_frontline()
    if not data:
        return ""

    geojson_str = json.dumps(data)
    source_name = data.get("_nexus_source", "OSINT")
    fetched     = data.get("_nexus_fetched", "")[:10]

    js = f"""
// ── Frontlinien-Layer ({source_name}, {fetched}) ─────────────────────────────
(function() {{
    var frontlineData = {geojson_str};
    if (!frontlineData || !frontlineData.features) return;
    var frontlineLayer = L.geoJSON(frontlineData, {{
        style: function(feature) {{
            return {{
                color: '#ff4444',
                weight: 3,
                opacity: 0.85,
                dashArray: '8, 4',
            }};
        }},
        onEachFeature: function(feature, layer) {{
            var src  = (feature.properties || {{}}).source || '{source_name}';
            var date = (feature.properties || {{}}).date   || '{fetched}';
            layer.bindPopup('<b>Frontlinie</b><br>Quelle: ' + src + '<br>Stand: ' + date);
        }}
    }}).addTo(map);

    // Legende hinzufügen
    var legend = L.control({{position: 'bottomright'}});
    legend.onAdd = function() {{
        var div = L.DomUtil.create('div', 'info legend');
        div.style.cssText = 'background:rgba(0,0,0,0.7);padding:6px 10px;border-radius:4px;color:#fff;font-size:11px;';
        div.innerHTML = '<span style="border-bottom:3px dashed #ff4444;padding-bottom:2px">━━</span> Frontlinie ({source_name}, {fetched})';
        return div;
    }};
    legend.addTo(map);
}})();
"""
    return js


if __name__ == "__main__":
    print(frontline_summary())
    data = fetch_frontline()
    if data:
        print(f"GeoJSON-Typ: {data.get('type')}")
        print(f"Features: {len(data.get('features', []))}")
