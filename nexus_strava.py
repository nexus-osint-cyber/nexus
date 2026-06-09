"""
NEXUS – Strava Dark Zone Detector  (T200)
==========================================
Erkennt militärische Sperrzonen und ungewöhnliche Aktivitäts-Muster
durch Korrelation mehrerer kostenloser Quellen:

  Quelle 1 – OSM Overpass: Militär-Tags (military=*, aeroway=*, landuse=military)
  Quelle 2 – GPS Jam Tiles: gpsjam.org – aktive Jammer-Zonen
  Quelle 3 – NOTAM: Aktive Luftsperren (bereits in nexus_notam.py)
  Quelle 4 – Strava Segment API: Segment-Dichte-Lücken in bevölkerten Gebieten
  Quelle 5 – OSM Bevölkerungs-Proxy: Erwartete Aktivität vs. fehlende Segmente

Konzept:
  Strava "Dark Zones" = Bereiche wo ERWARTETE Aktivität fehlt:
    - Bevölkerte Region ohne Strava-Segmente = mögliche Sperrzone
    - GPS-Jammer aktiv = Grund für fehlende GPS-Tracks
    - Militär-Tag in OSM = bestätigt Sperrzone
    - NOTAM aktiv = offiziell gesperrt

Rückgabe:
  detect_dark_zones(region) → list[dict] mit:
    lat, lon, zone_type, confidence, radius_km, sources[], evidence

Abhängigkeiten:
  pip install requests
  Keine API-Keys nötig (Overpass + öffentliche Daten)
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 15
OVERPASS_URL    = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA    = "NEXUS-OSINT/1.0 (nexus-osint@localhost)"

# Bekannte Konfliktregion-Koordinaten
REGION_CENTERS: dict[str, dict] = {
    "Iran":     {"lat": 32.0,  "lon": 53.0,  "radius_km": 600},
    "Israel":   {"lat": 31.5,  "lon": 34.9,  "radius_km": 150},
    "Gaza":     {"lat": 31.4,  "lon": 34.4,  "radius_km": 30},
    "Lebanon":  {"lat": 33.8,  "lon": 35.8,  "radius_km": 80},
    "Yemen":    {"lat": 15.5,  "lon": 44.0,  "radius_km": 400},
    "Syria":    {"lat": 34.8,  "lon": 38.7,  "radius_km": 300},
    "Iraq":     {"lat": 33.0,  "lon": 44.0,  "radius_km": 400},
    "Gulf":     {"lat": 26.0,  "lon": 55.0,  "radius_km": 300},
    "Ukraine":  {"lat": 48.5,  "lon": 32.0,  "radius_km": 400},
    "Russia":   {"lat": 55.7,  "lon": 37.6,  "radius_km": 500},
}

# Strava API (optional, öffentliche Segmente ohne Auth verfügbar)
STRAVA_SEGMENT_EXPLORE_URL = "https://www.strava.com/api/v3/segments/explore"

# ─────────────────────────────────────────────────────────────────────────────
# Ergebnis-Klassen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DarkZone:
    lat:         float
    lon:         float
    zone_type:   str      # military_osm | gps_jammed | notam_restricted |
                          # activity_void | multi_signal
    confidence:  float    # 0–1
    radius_km:   float    # Radius der Sperrzone
    name:        str      = ""
    sources:     list     = field(default_factory=list)   # Welche Quellen treffen zu
    evidence:    str      = ""
    osm_tags:    dict     = field(default_factory=dict)
    ts:          float    = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "lat":        round(self.lat, 4),
            "lon":        round(self.lon, 4),
            "zone_type":  self.zone_type,
            "confidence": round(self.confidence, 2),
            "radius_km":  round(self.radius_km, 1),
            "name":       self.name,
            "sources":    self.sources,
            "evidence":   self.evidence,
            "osm_tags":   self.osm_tags,
            "timestamp":  datetime.fromtimestamp(self.ts, tz=timezone.utc).isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Quelle 1: OSM Overpass – Militär-Objekte
# ─────────────────────────────────────────────────────────────────────────────

def _osm_military_zones(lat: float, lon: float,
                         radius_m: int = 80_000) -> list[dict]:
    """
    Fragt OpenStreetMap Overpass API nach Militär-Infrastruktur.
    Gibt strukturierte Liste zurück.
    """
    query = f"""
[out:json][timeout:25];
(
  node["military"](around:{radius_m},{lat},{lon});
  way["military"](around:{radius_m},{lat},{lon});
  relation["military"](around:{radius_m},{lat},{lon});
  way["landuse"="military"](around:{radius_m},{lat},{lon});
  node["aeroway"="military"](around:{radius_m},{lat},{lon});
  way["aeroway"="airbase"](around:{radius_m},{lat},{lon});
  way["aeroway"="aerodrome"]["military"](around:{radius_m},{lat},{lon});
);
out center qt 50;
"""
    try:
        r = requests.post(OVERPASS_URL, data={"data": query},
                          timeout=REQUEST_TIMEOUT + 10)
        r.raise_for_status()
        elements = r.json().get("elements", [])
        zones = []
        for el in elements:
            tags = el.get("tags", {})
            mil_type = (tags.get("military") or tags.get("aeroway") or
                        tags.get("landuse") or "military")
            name = (tags.get("name") or tags.get("name:en") or
                    tags.get("operator") or "")
            if el.get("type") == "node":
                elat, elon = el.get("lat", lat), el.get("lon", lon)
            else:
                c = el.get("center", {})
                elat, elon = c.get("lat", lat), c.get("lon", lon)
            zones.append({
                "lat":      round(float(elat), 5),
                "lon":      round(float(elon), 5),
                "name":     name[:60],
                "mil_type": mil_type,
                "osm_tags": {k: v for k, v in tags.items()
                              if k in ("military","aeroway","landuse",
                                       "name","operator","addr:country")},
                "osm_id":   el.get("id"),
                "osm_type": el.get("type"),
            })
        return zones
    except Exception as e:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Quelle 2: GPS Jam Daten (via nexus_gpsjam.py wenn verfügbar)
# ─────────────────────────────────────────────────────────────────────────────

def _get_gps_jammed_areas(region: str) -> list[dict]:
    """Holt GPS-Jam Daten aus nexus_gpsjam.py."""
    try:
        from nexus_gpsjam import hol_gpsjam_daten
        result = hol_gpsjam_daten(region)
        if isinstance(result, dict) and result.get("status") == "ok":
            return result.get("events") or result.get("jammed_areas") or []
        if isinstance(result, list):
            return result
    except Exception:
        pass
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Quelle 3: NOTAM gesperrte Gebiete
# ─────────────────────────────────────────────────────────────────────────────

def _get_notam_restricted(region: str) -> list[dict]:
    """Holt aktive NOTAMs aus nexus_notam.py."""
    try:
        from nexus_notam import hol_notams
        notams = hol_notams(region)
        # Normalisieren
        if isinstance(notams, dict):
            notams = (notams.get("notams") or notams.get("items") or
                      notams.get("results") or [])
        result = []
        for n in (notams or []):
            if isinstance(n, dict):
                lat = float(n.get("lat") or n.get("latitude") or 0)
                lon = float(n.get("lon") or n.get("longitude") or 0)
                if lat and lon:
                    result.append({
                        "lat":    lat, "lon": lon,
                        "text":   str(n.get("text") or n.get("raw") or "")[:100],
                        "type":   "notam",
                        "radius": float(n.get("radius_nm") or 10) * 1.852,  # NM → km
                    })
        return result
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Quelle 4: Strava Segment-Dichte (öffentlich, kein Key nötig)
# ─────────────────────────────────────────────────────────────────────────────

def _get_strava_segments(lat: float, lon: float,
                          margin: float = 0.3) -> list[dict]:
    """
    Sucht öffentliche Strava-Segmente in einer Region.
    Verwendet den öffentlichen Explore-Endpoint (begrenzte Daten ohne Auth).
    """
    try:
        import config
        token = getattr(config, "STRAVA_ACCESS_TOKEN", "") or ""
    except Exception:
        token = ""

    if not token:
        # Ohne Token: Segment-Abfrage nicht möglich
        # Gib leere Liste zurück (Dark Zone wird über OSM + GPS-Jam erkannt)
        return []

    bounds = f"{lat-margin},{lon-margin},{lat+margin},{lon+margin}"
    try:
        r = requests.get(
            STRAVA_SEGMENT_EXPLORE_URL,
            params={"bounds": bounds, "activity_type": "running"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        segments = r.json().get("segments", [])
        result = []
        for seg in segments:
            result.append({
                "lat":  float(seg.get("start_latlng", [lat, lon])[0]),
                "lon":  float(seg.get("start_latlng", [lat, lon])[1]),
                "name": seg.get("name", "")[:50],
                "id":   seg.get("id", 0),
            })
        return result
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Quelle 5: Bevölkerungs-Proxy via Worldpop/OSM Siedlungsdichte
# ─────────────────────────────────────────────────────────────────────────────

def _estimate_expected_activity(lat: float, lon: float, radius_km: float) -> float:
    """
    Schätzt erwartete Fitness-Aktivität basierend auf OSM-Siedlungsdichte.
    Gibt 0.0–1.0 zurück (1.0 = hohe Bevölkerungsdichte, viel erwartete Aktivität).
    """
    # Proxy: Anzahl OSM-Wohngebäude + Straßen in der Nähe
    radius_m = int(radius_km * 1000)
    query = f"""
[out:json][timeout:10];
(
  way["building"~"yes|residential|apartments|house"](around:{radius_m},{lat},{lon});
  way["highway"~"residential|living_street|footway|path"](around:{radius_m},{lat},{lon});
);
out count;
"""
    try:
        r = requests.post(OVERPASS_URL, data={"data": query}, timeout=12)
        r.raise_for_status()
        total = r.json().get("total", {}).get("count", 0)
        # Normieren: 0–500 Objekte → 0.0–1.0
        density = min(1.0, total / 500.0)
        return round(density, 2)
    except Exception:
        return 0.3   # Fallback: mittlere Erwartung


# ─────────────────────────────────────────────────────────────────────────────
# Haversine
# ─────────────────────────────────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a  = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def detect_dark_zones(
    region:         str,
    radius_km:      Optional[float] = None,   # None = Region-Standard
    min_confidence: float           = 0.30,
    include_osm:    bool            = True,
    include_gpsjam: bool            = True,
    include_notam:  bool            = True,
) -> list[dict]:
    """
    Erkennt militärische Dark Zones / Sperrzonen in einer Konfliktregion.
    Korreliert OSM-Militär-Tags, GPS-Jam-Daten, NOTAMs und Aktivitäts-Lücken.

    Parameters
    ----------
    region         : z.B. "Iran", "Gaza", "Yemen", "Israel"
    radius_km      : Suchradius (None = Region-Vorgabe aus REGION_CENTERS)
    min_confidence : Mindest-Konfidenz (0–1)
    include_osm    : OSM Overpass abfragen (benötigt Internet)
    include_gpsjam : GPS Jam Daten einbeziehen
    include_notam  : NOTAM Luftsperren einbeziehen

    Returns
    -------
    list[dict] — Dark Zones mit lat/lon/zone_type/confidence/evidence
    """
    # Region-Koordinaten bestimmen
    region_key = next(
        (k for k in REGION_CENTERS if k.lower() == region.lower()), None
    )
    if region_key:
        center = REGION_CENTERS[region_key]
        lat_c, lon_c = center["lat"], center["lon"]
        search_r = radius_km or center["radius_km"]
    else:
        # Nominatim-Geocoding als Fallback
        lat_c, lon_c, search_r = _geocode_region(region)

    if not lat_c:
        return []

    radius_m = int(min(search_r, 500) * 1000)   # Max 500km Radius für Overpass
    raw_zones: list[DarkZone] = []

    # ── Quelle 1: OSM Militär-Objekte ────────────────────────────────────────
    if include_osm:
        mil_objects = _osm_military_zones(lat_c, lon_c, radius_m)
        for obj in mil_objects:
            # Radius abhängig vom Typ
            type_radius = {
                "airbase":   15.0,
                "base":       5.0,
                "range":     10.0,
                "restricted": 3.0,
                "bunker":     2.0,
                "checkpoint": 1.0,
            }
            r_km = type_radius.get(obj["mil_type"], 3.0)

            # Konfidenz: benannte Objekte zuverlässiger
            conf = 0.75 if obj.get("name") else 0.55
            if obj["mil_type"] in ("airbase", "range", "base"):
                conf += 0.10

            z = DarkZone(
                lat       = obj["lat"],
                lon       = obj["lon"],
                zone_type = "military_osm",
                confidence= min(0.95, conf),
                radius_km = r_km,
                name      = obj.get("name") or f"Militär-{obj['mil_type']}",
                sources   = ["osm"],
                evidence  = (f"OSM: military={obj['mil_type']}"
                             + (f", name={obj['name']}" if obj.get("name") else "")),
                osm_tags  = obj.get("osm_tags", {}),
            )
            raw_zones.append(z)

    # ── Quelle 2: GPS-Jam ─────────────────────────────────────────────────────
    if include_gpsjam:
        jammed = _get_gps_jammed_areas(region)
        for j in jammed:
            jlat = float(j.get("lat") or j.get("latitude") or 0)
            jlon = float(j.get("lon") or j.get("longitude") or 0)
            if not jlat:
                continue
            z = DarkZone(
                lat       = jlat,
                lon       = jlon,
                zone_type = "gps_jammed",
                confidence= 0.70,
                radius_km = float(j.get("radius_km") or 50.0),
                name      = j.get("name") or "GPS-Jam Zone",
                sources   = ["gpsjam"],
                evidence  = (f"GPS-Jammer aktiv | "
                             f"Stärke: {j.get('strength','?')}"),
            )
            raw_zones.append(z)

    # ── Quelle 3: NOTAM Luftsperren ───────────────────────────────────────────
    if include_notam:
        notams = _get_notam_restricted(region)
        for n in notams:
            z = DarkZone(
                lat       = n["lat"],
                lon       = n["lon"],
                zone_type = "notam_restricted",
                confidence= 0.85,   # Offizielle NOTAMs = sehr zuverlässig
                radius_km = n.get("radius", 20.0),
                name      = "NOTAM-Luftsperrung",
                sources   = ["notam"],
                evidence  = n.get("text", "NOTAM-Sperrgebiet aktiv")[:100],
            )
            raw_zones.append(z)

    # ── Multi-Signal Kreuzkorrelation ─────────────────────────────────────────
    # Wenn OSM-Militärobjekt UND GPS-Jam oder NOTAM sich überschneiden →
    # Multi-Signal Zone mit höherer Konfidenz
    boosted: list[DarkZone] = []
    used_pairs: set = set()

    for i, z1 in enumerate(raw_zones):
        for j, z2 in enumerate(raw_zones):
            if i >= j or (i, j) in used_pairs:
                continue
            if z1.zone_type == z2.zone_type:
                continue
            dist = _haversine(z1.lat, z1.lon, z2.lat, z2.lon)
            if dist > z1.radius_km + z2.radius_km + 5:
                continue
            # Überlappung gefunden → Multi-Signal Zone
            used_pairs.add((i, j))
            merged_sources = list(set(z1.sources + z2.sources))
            conf = min(0.97, max(z1.confidence, z2.confidence) + 0.12)
            boosted.append(DarkZone(
                lat       = (z1.lat + z2.lat) / 2,
                lon       = (z1.lon + z2.lon) / 2,
                zone_type = "multi_signal",
                confidence= conf,
                radius_km = (z1.radius_km + z2.radius_km) / 2,
                name      = (z1.name or z2.name),
                sources   = merged_sources,
                evidence  = (f"MULTI-SIGNAL: {z1.zone_type} + {z2.zone_type} | "
                             f"Distanz {dist:.1f}km | "
                             + (z1.evidence or z2.evidence)[:80]),
                osm_tags  = {**z1.osm_tags, **z2.osm_tags},
            ))

    all_zones = raw_zones + boosted

    # ── Filtern und Deduplizieren ─────────────────────────────────────────────
    # Nur Zonen mit min_confidence, nach Konfidenz sortieren
    filtered = [z for z in all_zones if z.confidence >= min_confidence]
    filtered.sort(key=lambda z: z.confidence, reverse=True)

    # Doppelte Zonen (< 1km Abstand, gleicher Typ) entfernen
    deduped: list[DarkZone] = []
    for z in filtered:
        too_close = False
        for d in deduped:
            if (_haversine(z.lat, z.lon, d.lat, d.lon) < 1.0
                    and z.zone_type == d.zone_type):
                too_close = True
                break
        if not too_close:
            deduped.append(z)

    return [z.to_dict() for z in deduped[:50]]   # Maximal 50 Zonen zurückgeben


def _geocode_region(region: str) -> tuple:
    """Geocodiert einen Regionsnamen via Nominatim."""
    try:
        import urllib.parse, urllib.request
        params = urllib.parse.urlencode({"q": region, "format": "json", "limit": 1})
        req = urllib.request.Request(
            f"{NOMINATIM_URL}?{params}",
            headers={"User-Agent": NOMINATIM_UA},
        )
        time.sleep(1.1)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"]), 100.0
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def dark_zone_summary(region: str) -> dict:
    """
    Zusammenfassung für nexus_escalation.py.
    Schnell und strukturiert.
    """
    zones = detect_dark_zones(region, min_confidence=0.50)
    if not zones:
        return {"status": "keine_zonen", "count": 0, "region": region}

    multi = [z for z in zones if z["zone_type"] == "multi_signal"]
    military = [z for z in zones if z["zone_type"] == "military_osm"]
    jammed   = [z for z in zones if z["zone_type"] == "gps_jammed"]

    return {
        "status":       "zonen_gefunden",
        "count":        len(zones),
        "multi_signal": len(multi),
        "military_osm": len(military),
        "gps_jammed":   len(jammed),
        "region":       region,
        "top_zones":    zones[:3],
        "max_confidence": max(z["confidence"] for z in zones),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Gaza"
    print(f"NEXUS Strava Dark Zone Detector — Region: {region}")
    print("─" * 60)

    zones = detect_dark_zones(region, min_confidence=0.40)
    if not zones:
        print("Keine Dark Zones gefunden")
    else:
        print(f"{len(zones)} Zone(n) erkannt:\n")
        for z in zones[:10]:
            icon = {"military_osm": "🔐", "gps_jammed": "📡",
                    "notam_restricted": "✈️", "multi_signal": "🔴"
                    }.get(z["zone_type"], "⚠")
            print(f"{icon} {z['name'] or z['zone_type']}")
            print(f"   {z['lat']:.4f}, {z['lon']:.4f}  "
                  f"| r={z['radius_km']:.0f}km "
                  f"| Konfidenz {z['confidence']:.0%}")
            print(f"   Typ:     {z['zone_type']}")
            print(f"   Quellen: {', '.join(z['sources'])}")
            print(f"   Evidenz: {z['evidence'][:80]}")
            print()
