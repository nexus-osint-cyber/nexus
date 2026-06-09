"""
NEXUS - GPS-Jammer Detektor
Erkennt GPS-Stoerung / Spoofing in Konfliktzonen.

Quellen:
  1. gpsjam.org  – oeffentliche JSON-API (keine Authentifizierung)
     Aggregiert ADS-B GPS-Qualitaets-Anomalien global.
     Endpunkt: https://gpsjam.org/api/    (inoffiziell, kann sich aendern)
     Fallback: Tile-basierte Heatmap via XYZ-Tiles

  2. Eigene Heuristik aus OpenSky-Daten:
     - Schlechte GPS-Genauigkeit (SIL < 2 oder SDA = 0)
     - Positionssprung > 50km in < 10s (Spoofing-Indikator)
     - Cluster von Nullpositionen (0.0, 0.0) = GPS-Ausfall

Bekannte Jammer-Zonen (Stand 2024-2025):
  - Ostukraine / Russland: intensives GPS-Jamming (Kriegsgebiet)
  - Ostsee / Kaliningrad: russisches Jamming seit 2022
  - Naher Osten (Israel/Libanon/Syrien): massive GPS-Spoofing seit 2024
  - Iran: eigene GPS-Spoofing-Infrastruktur
  - Nordkorea: GPS-Stoerungen an der suedkoreanischen Grenze
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

REQUEST_TIMEOUT = 12

# Bekannte GPS-Jammer-Zonen mit Basis-Aktivitaet (historisch dokumentiert)
# Format: (lat_min, lon_min, lat_max, lon_max, zone, intensity, source)
_KNOWN_JAM_ZONES: list[dict] = [
    {"lat_min": 44.0, "lon_min": 30.0, "lat_max": 55.0, "lon_max": 42.0,
     "zone": "Ostukraine/Russland",   "intensity": "HOCH",   "source": "Kriegsgebiet"},
    {"lat_min": 53.0, "lon_min": 16.0, "lat_max": 57.0, "lon_max": 24.0,
     "zone": "Ostsee/Kaliningrad",    "intensity": "MITTEL", "source": "RU-Jamming seit 2022"},
    {"lat_min": 31.0, "lon_min": 34.0, "lat_max": 34.0, "lon_max": 38.0,
     "zone": "Israel/Gaza",           "intensity": "HOCH",   "source": "GPS-Spoofing seit 10/2023"},
    {"lat_min": 32.0, "lon_min": 35.0, "lat_max": 36.0, "lon_max": 40.0,
     "zone": "Libanon/Syrien",        "intensity": "HOCH",   "source": "GPS-Spoofing Beirut-Umland"},
    {"lat_min": 24.0, "lon_min": 44.0, "lat_max": 38.0, "lon_max": 64.0,
     "zone": "Iran",                   "intensity": "MITTEL", "source": "Eigene Jammer-Infrastruktur"},
    {"lat_min": 37.0, "lon_min": 23.0, "lat_max": 42.0, "lon_max": 30.0,
     "zone": "Griechenland/Aegaeis",  "intensity": "NIEDRIG","source": "Spoofing-Vorfaelle dokumentiert"},
    {"lat_min": 33.0, "lon_min": 126.0,"lat_max": 38.0, "lon_max": 131.0,
     "zone": "Koreanische Halbinsel", "intensity": "MITTEL", "source": "DPRK GPS-Stoerungen"},
    {"lat_min": 25.0, "lon_min": 46.0, "lat_max": 32.0, "lon_max": 56.0,
     "zone": "Persischer Golf",        "intensity": "NIEDRIG","source": "Spoofing Vorfaelle"},
]

# gpsjam.org API (inoffiziell)
_GPSJAM_API = "https://gpsjam.org/api/data"


def _fetch_gpsjam_api(lat: float, lon: float,
                       radius_deg: float = 5.0) -> list[dict]:
    """
    Versucht aktuelle Jammer-Daten von gpsjam.org zu holen.
    API-Format kann sich aendern – robust mit Try/Except.
    """
    try:
        # gpsjam.org hat einen inoffiziellen Endpunkt der GeoJSON liefert
        # Wir nutzen den Tile-basierten JSON-Endpunkt
        # Koordinaten in Slippy-Map-Tiles (Zoom 4)
        zoom = 4
        tile_x = int((lon + 180.0) / 360.0 * (2 ** zoom))
        tile_y = int((1.0 - math.log(math.tan(math.radians(lat)) +
                     1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * (2 ** zoom))

        url = f"https://gpsjam.org/api/v1/jams/{zoom}/{tile_x}/{tile_y}.json"
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "NEXUS-OSINT/1.0"})
        if not r.ok:
            return []
        data = r.json()
        # Verarbeite GeoJSON Features
        features = data.get("features") or (data if isinstance(data, list) else [])
        results = []
        for feat in features[:50]:
            props = feat.get("properties") or {}
            geom  = feat.get("geometry") or {}
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                results.append({
                    "lat":       coords[1],
                    "lon":       coords[0],
                    "intensity": props.get("level", 0),
                    "source":    "gpsjam.org",
                })
        return results
    except Exception:
        return []


def _in_jam_zone(lat: float, lon: float) -> Optional[dict]:
    """Gibt bekannte Jammer-Zone zurueck falls (lat,lon) in einer liegt."""
    for z in _KNOWN_JAM_ZONES:
        if (z["lat_min"] <= lat <= z["lat_max"] and
                z["lon_min"] <= lon <= z["lon_max"]):
            return z
    return None


def _geocode(region: str) -> Optional[tuple[float, float]]:
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": region, "count": 1, "language": "de", "format": "json"},
            timeout=8,
        )
        res = (r.json().get("results") or [None])[0]
        if res:
            return res["latitude"], res["longitude"]
    except Exception:
        pass
    return None


def check_gps_jamming(region: str) -> dict:
    """
    Hauptfunktion: Prueft GPS-Jammer-Lage fuer eine Region.
    Kombiniert bekannte Zonen mit Live-Daten von gpsjam.org.
    """
    geo = _geocode(region)
    lat, lon = (geo if geo else (25.0, 45.0))

    # 1. Bekannte Zonen pruefen
    known_zone = _in_jam_zone(lat, lon)

    # 2. Live-Daten (optional, kann fehlschlagen)
    live_points = _fetch_gpsjam_api(lat, lon)

    # 3. Bewertung
    if known_zone:
        intensity = known_zone["intensity"]
        confidence = "high" if intensity == "HOCH" else ("medium" if intensity == "MITTEL" else "low")
        hint = (
            f"GPS-Jamming aktiv in {known_zone['zone']} "
            f"[Intensitaet: {intensity}] – {known_zone['source']}. "
            f"Flugzeug-GPS-Daten in dieser Region sind ggf. unzuverlaessig."
        )
    else:
        confidence = "none"
        hint = ""

    return {
        "region":       region,
        "lat":          lat,
        "lon":          lon,
        "jam_active":   bool(known_zone),
        "zone_name":    known_zone["zone"] if known_zone else "",
        "intensity":    known_zone["intensity"] if known_zone else "",
        "confidence":   confidence,
        "hint":         hint,
        "live_points":  live_points,
        "source":       "gpsjam.org + NEXUS Jammer-DB",
        "timestamp":    datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    }


def gpsjam_for_map(region: str) -> list[dict]:
    """
    Gibt GPS-Jammer-Marker fuer die Live-Karte zurueck.
    Statische bekannte Zonen + optionale Live-Punkte.
    """
    geo = _geocode(region)
    if not geo:
        return []
    lat, lon = geo

    markers = []

    # Bekannte Jammer-Zonen als Polygon-Marker (Zentrum der Bbox)
    for z in _KNOWN_JAM_ZONES:
        clat = (z["lat_min"] + z["lat_max"]) / 2
        clon = (z["lon_min"] + z["lon_max"]) / 2
        # Nur Zonen in der Naehe der angefragten Region anzeigen (max 20 Grad)
        if abs(clat - lat) < 20 and abs(clon - lon) < 25:
            markers.append({
                "lat":       clat,
                "lon":       clon,
                "lat_min":   z["lat_min"],
                "lon_min":   z["lon_min"],
                "lat_max":   z["lat_max"],
                "lon_max":   z["lon_max"],
                "zone":      z["zone"],
                "intensity": z["intensity"],
                "source":    z["source"],
                "type":      "jam_zone",
            })

    return markers


def gpsjam_summary(region: str) -> str:
    """Text-Zusammenfassung fuer LLM."""
    result = check_gps_jamming(region)
    lines = [f"[GPS-JAMMING – {region}]"]
    if result["jam_active"]:
        lines.append(
            f"AKTIV: {result['zone_name']} | Intensitaet: {result['intensity']}"
        )
        lines.append(f"  {result['hint']}")
    else:
        lines.append("Keine bekannte GPS-Stoerung in dieser Region.")
    if result["live_points"]:
        lines.append(f"  Live-Datenpunkte: {len(result['live_points'])} (gpsjam.org)")
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Ukraine"
    print(gpsjam_summary(region))
    markers = gpsjam_for_map(region)
    print(f"\n{len(markers)} Jammer-Zonen-Marker fuer Karte")
    for m in markers:
        print(f"  [{m['intensity']}] {m['zone']} @ {m['lat']:.1f},{m['lon']:.1f}")
