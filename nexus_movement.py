"""
NEXUS – Konvoi & Traffic-Anomalie-Detektion  (Ebene 4 / Modul 4.9)
===================================================================
Erkennt ungewöhnliche Fahrzeugbewegungen auf Straßen:

  1. HERE Maps Traffic API (Free-Tier: 250k Req/Mo)
     → Stau-Anomalien auf strategischen Routen
  2. Waze Public Data Feed (kein Key)
     → Nutzermeldungen (Polizei, Stau, Unfall) auf Konfliktrouten
  3. Overpass-API (OpenStreetMap, kostenlos)
     → Strategische Routen (Autobahnen, Militärstraßen) in Region
  4. TomTom Traffic (Free-Tier, optional)
     → Backup falls HERE nicht konfiguriert

Öffentliche API:
  get_traffic_anomalies(region)    → list[dict]
  movement_for_map(region)         → list[dict]   (Karten-Marker)
  movement_summary(anomalies)      → str           (LLM-Kontext)

Config (config.py):
  HERE_API_KEY   = ""    # Optional – https://developer.here.com (Free Tier)
  TOMTOM_KEY     = ""    # Optional – https://developer.tomtom.com
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

try:
    import config
    HERE_KEY   = getattr(config, "HERE_API_KEY", "")
    TOMTOM_KEY = getattr(config, "TOMTOM_KEY", "")
except ImportError:
    HERE_KEY   = ""
    TOMTOM_KEY = ""


# ─────────────────────────────────────────────────────────────────────────────
# Region → Bounding-Box
# ─────────────────────────────────────────────────────────────────────────────

# Grobe Bounding-Boxes für bekannte Konfliktregionen
_REGION_BBOX = {
    "ukraine":         (44.3, 22.1, 52.4, 40.2),
    "naher osten":     (29.0, 34.0, 37.5, 43.5),
    "israel":          (29.5, 34.2, 33.4, 35.9),
    "rotes meer":      (12.0, 41.0, 22.0, 44.5),
    "persischer golf": (23.0, 48.0, 27.5, 56.5),
    "taiwan":          (21.5, 118.0, 25.5, 122.5),
    "korea":           (34.0, 124.0, 38.6, 129.5),
    "syrien":          (32.3, 35.7, 37.3, 42.4),
    "jemen":           (12.0, 42.0, 19.0, 54.0),
}

def _get_bbox(region: str) -> Optional[tuple[float,float,float,float]]:
    key = region.lower()
    for k, bbox in _REGION_BBOX.items():
        if k in key or key in k:
            return bbox
    return None


def _bbox_center(bbox: tuple) -> tuple[float, float]:
    return ((bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2)


# ─────────────────────────────────────────────────────────────────────────────
# Overpass-API: Strategische Routen laden
# ─────────────────────────────────────────────────────────────────────────────

def _get_strategic_roads(bbox: tuple[float,float,float,float]) -> list[dict]:
    """
    Holt Hauptstraßen aus OSM über Overpass-API.
    Gibt Liste mit {lat, lon, name, highway_type} zurück.
    """
    south, west, north, east = bbox
    query = f"""
[out:json][timeout:20];
(
  way["highway"~"motorway|trunk|primary"]["name"]
     ({south},{west},{north},{east});
);
out center 20;
""".strip()

    try:
        data = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": "NEXUS-OSINT/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())
            roads = []
            for el in (result.get("elements") or [])[:20]:
                center = el.get("center", {})
                lat = center.get("lat")
                lon = center.get("lon")
                if lat and lon:
                    roads.append({
                        "lat":  lat,
                        "lon":  lon,
                        "name": (el.get("tags") or {}).get("name", "Unbekannte Straße"),
                        "type": (el.get("tags") or {}).get("highway", "road"),
                    })
            return roads
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Waze Public Feed
# ─────────────────────────────────────────────────────────────────────────────

_WAZE_ALERTS_MILITARY = {
    "police", "hazard", "jam", "accident", "road_closed",
    "misc", "construction",
}

def _fetch_waze(bbox: tuple[float,float,float,float]) -> list[dict]:
    """
    Waze LiveMap API (public, kein Key).
    Gibt Alerts in der Region zurück.
    """
    south, west, north, east = bbox
    url = (
        "https://www.waze.com/live-map/api/georss"
        f"?bottom={south}&top={north}&left={west}&right={east}"
        "&ma=200&mj=200&mu=200"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer":    "https://www.waze.com/live-map",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        alerts = []
        for alert in (data.get("alerts") or [])[:30]:
            lat  = alert.get("location", {}).get("y")
            lon  = alert.get("location", {}).get("x")
            atype = alert.get("type", "").lower()
            if lat and lon:
                alerts.append({
                    "lat":     lat,
                    "lon":     lon,
                    "type":    atype,
                    "subtype": alert.get("subtype", ""),
                    "street":  alert.get("street", ""),
                    "source":  "waze",
                    "ts":      alert.get("pubMillis", 0),
                })
        return alerts
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# HERE Maps Traffic
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_here_traffic(bbox: tuple[float,float,float,float]) -> list[dict]:
    """HERE Maps Traffic API – braucht HERE_API_KEY in config.py."""
    if not HERE_KEY:
        return []

    south, west, north, east = bbox
    url = (
        "https://data.traffic.hereapi.com/v7/incidents"
        f"?in=bbox:{west},{south},{east},{north}"
        "&locationReferencing=shape"
        f"&apiKey={HERE_KEY}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        incidents = []
        for inc in (data.get("results") or [])[:20]:
            loc = inc.get("location", {})
            shape = loc.get("shape", {}).get("links", [{}])
            if shape:
                pt = shape[0].get("points", [{}])[0]
                lat = pt.get("lat")
                lon = pt.get("lng")
                if lat and lon:
                    desc = inc.get("incidentDetails", {})
                    incidents.append({
                        "lat":        lat,
                        "lon":        lon,
                        "type":       inc.get("type", "unknown"),
                        "severity":   inc.get("criticality", 0),
                        "description":desc.get("description", {}).get("value", ""),
                        "road":       loc.get("description", {}).get("value", ""),
                        "source":     "here",
                    })
        return incidents
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Anomalie-Bewertung
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MovementAnomaly:
    lat:         float
    lon:         float
    road_name:   str    = ""
    anomaly_type: str   = "traffic_jam"   # traffic_jam | road_closed | unusual_activity
    severity:    int    = 1               # 1-5
    confidence:  float  = 0.4
    description: str    = ""
    source:      str    = ""
    convoy_hint: bool   = False           # Deutet auf Konvoi hin?
    ts:          float  = field(default_factory=time.time)


_CONVOY_KEYWORDS = {
    "military", "army", "convoy", "troops", "tank", "vehicle",
    "колонна", "колони", "техніка", "военный", "армія",
    "конвой", "транспорт", "military vehicle",
}

def _is_convoy_hint(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _CONVOY_KEYWORDS)


# Konvoi-Signatur-Keywords (erweitert)
_CONVOY_KEYWORDS = {
    "military", "convoy", "troops", "armor", "armour", "tank",
    "military vehicle", "troop movement", "army", "soldier",
    "militär", "konvoi", "panzer", "truppe", "soldat",
    "closed", "gesperrt", "roadblock", "checkpoint", "sperrung",
    "explosions", "explosion", "detonation", "artillery", "rocket",
}

def _is_convoy_hint(text: str) -> bool:
    """Verbesserte Konvoi-Erkennung mit erweiterter Signatur-Liste."""
    t = text.lower()
    return any(kw in t for kw in _CONVOY_KEYWORDS)


# Historischer Anomalie-Puffer für Z-Score-Berechnung
_ALERT_HISTORY: dict[str, list[float]] = {}  # region → list of severity scores

def _update_baseline(region: str, severity: float) -> tuple[float, float]:
    """Aktualisiert Baseline + gibt (mean, std) zurück."""
    hist = _ALERT_HISTORY.setdefault(region, [])
    hist.append(severity)
    if len(hist) > 200:
        _ALERT_HISTORY[region] = hist[-200:]
    import statistics as _s
    mean = _s.mean(hist)
    std  = _s.stdev(hist) if len(hist) > 1 else 1.0
    return mean, max(std, 0.1)


def _assess_anomaly(item: dict, roads: list[dict]) -> Optional[MovementAnomaly]:
    """Verbesserte Anomalie-Bewertung mit Z-Score und erweiterter Signatur-Erkennung."""
    lat = item.get("lat")
    lon = item.get("lon")
    if not lat or not lon:
        return None

    # Nähe zu strategischer Straße (Radius: 20km)
    near_strategic = False
    road_name      = ""
    min_dist       = float("inf")
    for road in roads:
        dist = math.sqrt((lat - road["lat"])**2 + (lon - road["lon"])**2) * 111
        if dist < min_dist:
            min_dist = dist
        if dist < 20:
            near_strategic = True
            road_name = road["name"]
            break

    # Ohne strategische Straßen nur bei starkem Signal fortsetzen
    severity_raw = float(item.get("severity", 1))
    if not near_strategic and roads and severity_raw < 3:
        return None

    itype = item.get("type", "").lower()
    desc  = (item.get("description", "") + " " + item.get("street", "") +
             " " + item.get("subtype", ""))
    convoy = _is_convoy_hint(desc)

    # Uhrzeit-Faktor: 22:00–05:00 UTC ist verdächtiger
    hour         = time.gmtime().tm_hour
    night_bonus  = 0.18 if (hour >= 22 or hour <= 5) else 0.0
    dusk_bonus   = 0.08 if (hour in (18, 19, 20, 21, 5, 6)) else 0.0

    # Z-Score Anomalie-Bewertung
    region_key = "{}_{:.0f}_{:.0f}".format(itype, round(lat, 1), round(lon, 1))
    mean_sev, std_sev = _update_baseline(region_key, severity_raw)
    z_score = (severity_raw - mean_sev) / std_sev

    # Konfidenz-Berechnung (schärfer, mehr Faktoren)
    conf = 0.25
    conf += min(0.20, z_score * 0.07)           # Z-Score: Abweichung von Baseline
    conf += night_bonus + dusk_bonus
    if convoy:                   conf += 0.30    # Konvoi-Signatur
    if itype in ("road_closed",
                 "closure", "misc"): conf += 0.12
    if near_strategic:           conf += 0.15
    if min_dist < 5:             conf += 0.08    # sehr nah an strategischer Route
    if severity_raw >= 4:        conf += 0.10    # hohe Schwere

    conf = min(0.95, max(0.0, conf))

    # Schweregrad aus Z-Score ableiten
    severity_adj = min(5, max(1, int(2.5 + z_score)))

    return MovementAnomaly(
        lat=lat, lon=lon,
        road_name=road_name,
        anomaly_type="convoy_hint" if convoy else "traffic_anomaly",
        severity=severity_adj,
        confidence=round(conf, 2),
        description=desc[:120],
        source=item.get("source", "unknown"),
        convoy_hint=convoy,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def get_traffic_anomalies(region: str = "Ukraine") -> list[MovementAnomaly]:
    """
    Aggregiert Traffic-Daten und bewertet Konvoi-Wahrscheinlichkeit.
    """
    bbox = _get_bbox(region)
    if not bbox:
        return []

    # Strategische Routen (Overpass)
    roads = _get_strategic_roads(bbox)

    # Traffic-Daten von mehreren Quellen
    raw_items = []
    raw_items.extend(_fetch_waze(bbox))
    raw_items.extend(_fetch_here_traffic(bbox))

    # Bewertung
    anomalies = []
    for item in raw_items:
        result = _assess_anomaly(item, roads)
        if result and result.confidence >= 0.28:
            anomalies.append(result)

    anomalies.sort(key=lambda a: a.confidence, reverse=True)
    return anomalies[:25]


# ─────────────────────────────────────────────────────────────────────────────
# Karten-Output
# ─────────────────────────────────────────────────────────────────────────────

_SEV_COLOR = {1: "#4466aa", 2: "#ff8800", 3: "#ff6600", 4: "#ff4400", 5: "#ff0000"}

def movement_for_map(region: str = "Ukraine") -> list[dict]:
    anomalies = get_traffic_anomalies(region)
    markers = []
    for a in anomalies:
        col  = _SEV_COLOR.get(a.severity, "#ff8800")
        icon = "🚛" if a.convoy_hint else "🚗"
        title = ("🚛 KONVOI-VERDACHT" if a.convoy_hint else "🚗 Traffic-Anomalie")
        if a.road_name:
            title += f" · {a.road_name[:25]}"
        markers.append({
            "lat":          a.lat,
            "lon":          a.lon,
            "title":        title,
            "text":         a.description[:150],
            "anomaly_type": a.anomaly_type,
            "severity":     a.severity,
            "confidence":   a.confidence,
            "convoy_hint":  a.convoy_hint,
            "road_name":    a.road_name,
            "source":       a.source,
            "color":        col,
            "icon":         icon,
        })
    return markers


def movement_summary(anomalies: list[MovementAnomaly], max_items: int = 6) -> str:
    if not anomalies:
        return ""
    convoy_n = sum(1 for a in anomalies if a.convoy_hint)
    lines = [f"[BEWEGUNG] {len(anomalies)} Traffic-Anomalien ({convoy_n} Konvoi-Verdacht):\n"]
    for i, a in enumerate(anomalies[:max_items], 1):
        flag = "🚛 KONVOI" if a.convoy_hint else "🚗 Anomalie"
        lines.append(
            f"  {i}. {flag} [{a.lat:.3f},{a.lon:.3f}] "
            f"Schwere:{a.severity}/5 Konf:{a.confidence:.0%}"
            f" ({a.source})\n     {a.description[:80]}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print("Teste nexus_movement.py...")
    results = get_traffic_anomalies("Ukraine")
    print(f"Gefunden: {len(results)} Anomalien")
    for a in results[:5]:
        flag = "🚛" if a.convoy_hint else "🚗"
        print(f"  {flag} [{a.lat:.3f},{a.lon:.3f}] {a.anomaly_type} "
              f"Konf:{a.confidence:.0%} Str:{a.road_name}")
    print(movement_summary(results))
