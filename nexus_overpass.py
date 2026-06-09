"""
NEXUS – OSM Overpass Militärinfrastruktur  (T204)
=================================================
Dediziertes Modul für OpenStreetMap-basierte Militär-OSINT.
Alle Funktionen geben strukturierte Koordinaten-Daten zurück.

Funktionen:
  query_military_near(lat, lon, radius_km)  → Alle Militärobjekte
  query_convoy_routes(lat1, lon1, lat2, lon2) → Mögliche Konvoi-Routen
  query_logistics_hubs(region)              → Kraftstoff, Repair, Supply
  query_border_crossings(region)            → Grenzübergänge + Status
  query_airfields(lat, lon, radius_km)      → Militärische Flugplätze
  get_military_map(region)                  → Vollständige Militärinfrastruktur

Abhängigkeiten: pip install requests
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

OVERPASS_URL     = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 30
REQUEST_TIMEOUT  = 35
_CACHE_DIR       = Path(__file__).parent / "nexus_overpass_cache"
_CACHE_TTL_H     = 12

# Regionen mit Bounding Boxes [S, W, N, E]
REGION_BBOX: dict[str, list[float]] = {
    "Iran":     [25.0, 44.0, 39.8, 63.5],
    "Israel":   [29.5, 34.0, 33.5, 35.9],
    "Gaza":     [31.2, 34.2, 31.6, 34.6],
    "Lebanon":  [33.0, 35.0, 34.7, 36.7],
    "Syria":    [32.3, 35.5, 37.4, 42.4],
    "Yemen":    [12.0, 42.5, 18.5, 54.0],
    "Iraq":     [29.0, 38.5, 37.5, 48.8],
    "Gulf":     [22.0, 48.0, 27.0, 60.0],
    "Ukraine":  [44.0, 22.0, 52.5, 40.3],
}

# Militär-OSM-Tags Kategorisierung
MIL_TYPES = {
    "airbase":      {"icon": "✈️", "risk": "HOCH",   "radius_km": 20},
    "base":         {"icon": "🔒", "risk": "HOCH",   "radius_km": 8},
    "barracks":     {"icon": "🏗",  "risk": "MITTEL",  "radius_km": 2},
    "range":        {"icon": "🎯", "risk": "MITTEL",  "radius_km": 15},
    "bunker":       {"icon": "⬛", "risk": "HOCH",   "radius_km": 3},
    "checkpoint":   {"icon": "🚧", "risk": "NIEDRIG","radius_km": 1},
    "training_area":{"icon": "🏕",  "risk": "NIEDRIG","radius_km": 10},
    "ammunition":   {"icon": "💣", "risk": "KRITISCH","radius_km": 5},
    "naval_base":   {"icon": "⚓", "risk": "HOCH",   "radius_km": 15},
    "nuclear":      {"icon": "☢",  "risk": "KRITISCH","radius_km": 25},
    "danger_area":  {"icon": "⚠",  "risk": "MITTEL",  "radius_km": 10},
}


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a  = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _overpass(query: str) -> list[dict]:
    """Führt Overpass-Abfrage aus."""
    try:
        r = requests.post(OVERPASS_URL, data={"data": query},
                          timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json().get("elements", [])
    except Exception:
        return []


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / (key[:80].replace("/","_") + ".json")

def _cached(key: str) -> Optional[list]:
    p = _cache_path(key)
    if not p.exists(): return None
    try:
        d = json.loads(p.read_text())
        if time.time() - d.get("ts", 0) < _CACHE_TTL_H * 3600:
            return d.get("data")
    except Exception: pass
    return None

def _store(key: str, data: list) -> list:
    try:
        _cache_path(key).write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception: pass
    return data


def _el_to_dict(el: dict, center_lat: float, center_lon: float) -> dict:
    """Konvertiert Overpass-Element zu strukturiertem Dict."""
    tags  = el.get("tags", {})
    if el.get("type") == "node":
        lat, lon = el.get("lat", 0), el.get("lon", 0)
    else:
        c = el.get("center", {})
        lat, lon = c.get("lat", 0), c.get("lon", 0)

    mil_type = (tags.get("military") or tags.get("aeroway") or
                tags.get("landuse") or "military")
    name     = (tags.get("name") or tags.get("name:en") or
                tags.get("operator") or "")
    type_info = MIL_TYPES.get(mil_type, {"icon": "⚫", "risk": "MITTEL", "radius_km": 5})

    dist = _haversine(center_lat, center_lon, lat, lon) if center_lat else 0

    return {
        "lat":      round(float(lat), 5),
        "lon":      round(float(lon), 5),
        "name":     name[:60],
        "mil_type": mil_type,
        "risk":     type_info["risk"],
        "icon":     type_info["icon"],
        "radius_km":type_info["radius_km"],
        "dist_km":  round(dist, 1),
        "osm_id":   el.get("id"),
        "osm_type": el.get("type"),
        "tags":     {k: v for k, v in tags.items()
                     if k in ("military","aeroway","landuse","name",
                               "operator","addr:country","access","note")},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def query_military_near(lat: float, lon: float,
                         radius_km: float = 50.0) -> list[dict]:
    """
    Findet alle Militärobjekte in radius_km um einen Punkt.

    Returns
    -------
    list[dict] mit: lat, lon, name, mil_type, risk, dist_km
    Sortiert nach Distanz.
    """
    radius_m = int(radius_km * 1000)
    cache_key = f"mil_{lat:.2f}_{lon:.2f}_{radius_m}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    query = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  node["military"](around:{radius_m},{lat},{lon});
  way["military"](around:{radius_m},{lat},{lon});
  relation["military"](around:{radius_m},{lat},{lon});
  way["landuse"="military"](around:{radius_m},{lat},{lon});
  way["aeroway"="airbase"](around:{radius_m},{lat},{lon});
  node["aeroway"="military"](around:{radius_m},{lat},{lon});
  way["aeroway"~"airstrip|aerodrome"]["military"](around:{radius_m},{lat},{lon});
);
out center qt 100;
"""
    elements = _overpass(query)
    results  = [_el_to_dict(el, lat, lon) for el in elements
                if el.get("lat") or el.get("center")]
    results.sort(key=lambda x: x["dist_km"])
    return _store(cache_key, results)


def query_airfields(lat: float, lon: float,
                    radius_km: float = 200.0) -> list[dict]:
    """
    Findet militärische und zivile Flugplätze in der Umgebung.
    Nützlich für Luftangriffs-Raum-Analyse.
    """
    radius_m = int(radius_km * 1000)
    cache_key = f"air_{lat:.2f}_{lon:.2f}_{radius_m}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    query = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  way["aeroway"~"airbase|aerodrome|airstrip"](around:{radius_m},{lat},{lon});
  node["aeroway"~"airbase|aerodrome"](around:{radius_m},{lat},{lon});
);
out center qt 50;
"""
    elements = _overpass(query)
    results  = []
    for el in elements:
        tags  = el.get("tags", {})
        if el.get("type") == "node":
            elat, elon = el.get("lat", 0), el.get("lon", 0)
        else:
            c = el.get("center", {})
            elat, elon = c.get("lat", 0), c.get("lon", 0)
        results.append({
            "lat":      round(float(elat), 5),
            "lon":      round(float(elon), 5),
            "name":     tags.get("name") or tags.get("iata") or "",
            "icao":     tags.get("icao", ""),
            "iata":     tags.get("iata", ""),
            "type":     tags.get("aeroway", "aerodrome"),
            "military": bool(tags.get("military")),
            "dist_km":  round(_haversine(lat, lon, elat, elon), 1),
        })
    results.sort(key=lambda x: x["dist_km"])
    return _store(cache_key, results)


def query_logistics_hubs(
    region: str,
    radius_km: float = 300.0,
) -> dict:
    """
    Sucht Logistik-Infrastruktur: Kraftstoff-Depots, Häfen, Bahnhöfe.
    Zeigt mögliche Versorgungsrouten.
    """
    bbox = REGION_BBOX.get(region, [])
    if not bbox:
        return {"status": "unbekannte_region", "region": region}

    cache_key = f"logistics_{region}"
    cached = _cached(cache_key)
    if cached is not None:
        return {"status": "ok", "region": region, "data": cached}

    s, w, n, e = bbox
    query = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  node["amenity"="fuel"](  {s},{w},{n},{e});
  node["railway"="station"]({s},{w},{n},{e});
  way["harbour"="yes"](    {s},{w},{n},{e});
  node["man_made"="petroleum_well"]({s},{w},{n},{e});
  node["industrial"="fuel"]({s},{w},{n},{e});
  way["landuse"="industrial"]["name"~"depot|fuel|supply|military",i]
      ({s},{w},{n},{e});
);
out center qt 200;
"""
    elements = _overpass(query)
    hubs = []
    for el in elements:
        tags = el.get("tags", {})
        if el.get("type") == "node":
            elat, elon = el.get("lat", 0), el.get("lon", 0)
        else:
            c = el.get("center", {})
            elat, elon = c.get("lat", 0), c.get("lon", 0)
        hub_type = (tags.get("amenity") or tags.get("railway") or
                    tags.get("man_made") or tags.get("industrial") or "hub")
        hubs.append({
            "lat":      round(float(elat), 5),
            "lon":      round(float(elon), 5),
            "name":     (tags.get("name") or hub_type)[:50],
            "type":     hub_type,
            "operator": tags.get("operator", "")[:40],
        })

    return {"status": "ok", "region": region,
            "count": len(hubs), "data": _store(cache_key, hubs)}


def query_border_crossings(region: str) -> list[dict]:
    """
    Findet Grenzübergänge der Region.
    Relevant für Truppenbewegungen und Versorgung.
    """
    bbox = REGION_BBOX.get(region, [])
    if not bbox:
        return []

    cache_key = f"border_{region}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    s, w, n, e = bbox
    query = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  node["barrier"="border_control"]({s},{w},{n},{e});
  node["border_type"="checkpoint"]({s},{w},{n},{e});
  node["crossing:barrier"]({s},{w},{n},{e});
  way["border_type"]({s},{w},{n},{e});
);
out center qt 50;
"""
    elements = _overpass(query)
    crossings = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("ref") or "Grenzübergang"
        crossings.append({
            "lat":     round(float(el.get("lat") or el.get("center", {}).get("lat", 0)), 5),
            "lon":     round(float(el.get("lon") or el.get("center", {}).get("lon", 0)), 5),
            "name":    name[:50],
            "type":    tags.get("barrier") or tags.get("border_type", "border"),
            "country": tags.get("addr:country", ""),
        })
    return _store(cache_key, crossings)


def get_military_map(region: str) -> dict:
    """
    Vollständige Militärinfrastruktur-Karte einer Region.
    Kombiniert Bases, Flugplätze, Logistik, Grenzübergänge.
    """
    bbox = REGION_BBOX.get(region, [])
    if not bbox:
        return {"status": "unbekannte_region"}

    s, w, n, e = bbox
    center_lat = (s + n) / 2
    center_lon = (w + e) / 2

    # Alle Militärobjekte in der Region
    radius_km = _haversine(s, w, n, e) / 2 + 50
    military  = query_military_near(center_lat, center_lon, min(radius_km, 500))

    # Nach Typ gruppieren
    by_type: dict[str, list] = {}
    for obj in military:
        t = obj["mil_type"]
        by_type.setdefault(t, []).append(obj)

    # Hochrisiko-Objekte
    high_risk = [obj for obj in military
                 if obj.get("risk") in ("HOCH", "KRITISCH")]

    return {
        "status":         "ok",
        "region":         region,
        "bbox":           bbox,
        "total_objects":  len(military),
        "high_risk_count": len(high_risk),
        "by_type":        {k: len(v) for k, v in by_type.items()},
        "high_risk_sites": high_risk[:10],
        "all_objects":    military[:100],
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }


def overpass_status() -> dict:
    """Prüft Overpass-API Erreichbarkeit."""
    try:
        r = requests.get("https://overpass-api.de/api/status",
                         timeout=8)
        return {"status": "ok", "reachable": True,
                "info": r.text[:100]}
    except Exception as e:
        return {"status": "fehler", "reachable": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Gaza"
    print(f"NEXUS Overpass Militärinfrastruktur — {region}")
    print("─" * 55)

    s = overpass_status()
    print(f"API: {'✅' if s['reachable'] else '❌'}")

    print(f"\nMilitärkarte {region}...")
    m = get_military_map(region)
    print(f"Gesamt-Objekte: {m.get('total_objects', 0)}")
    print(f"Hochrisiko:     {m.get('high_risk_count', 0)}")
    print(f"Typen: {m.get('by_type', {})}")
    if m.get("high_risk_sites"):
        print("\nTop Hochrisiko-Objekte:")
        for site in m["high_risk_sites"][:5]:
            print(f"  {site['icon']} {site['name'] or site['mil_type']} "
                  f"@ {site['lat']},{site['lon']} "
                  f"[{site['risk']}]")
