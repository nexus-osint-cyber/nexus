"""
NEXUS – NASA EONET (Earth Observatory Natural Event Tracker)
Kostenlos, kein API-Key noetig.
Liefert: Waldbraende, Thermische Anomalien, Tropische Stuerme, Sandstuerme,
         Ueberschwemmungen, Vulkane, Erdbeben (von EONET klassifiziert).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

import requests

EONET_API    = "https://eonet.gsfc.nasa.gov/api/v3/events"
REQUEST_TIMEOUT = 12

# Ereignis-Kategorien mit Icon + OSINT-Relevanz
_CATEGORY_META: dict[str, dict] = {
    "wildfires":            {"icon": "🔥", "color": "#ff4400", "osint": "Waldbrand/Feuer – moeglicher Zusammenhang mit Kampfhandlungen"},
    "severeStorms":         {"icon": "⛈",  "color": "#4488ff", "osint": "Schweres Unwetter – operative Einschraenkungen moeglich"},
    "volcanoes":            {"icon": "🌋", "color": "#ff8800", "osint": "Vulkanaktivitaet"},
    "seaLakeIce":           {"icon": "🧊", "color": "#88ccff", "osint": "Eis/Frost – maritime Durchfahrt eingeschraenkt"},
    "earthquakes":          {"icon": "⚡", "color": "#ffdd00", "osint": "Erdbeben (EONET-klassifiziert)"},
    "floods":               {"icon": "🌊", "color": "#2266ff", "osint": "Ueberschwemmung – Logistik/Infrastruktur betroffen"},
    "landslides":           {"icon": "⛰", "color": "#aa6600", "osint": "Erdrutsch – Strassenverbindungen unterbrochen"},
    "drought":              {"icon": "☀",  "color": "#ffaa00", "osint": "Duerre – humanitaere Lage verschlechtert sich"},
    "dustHaze":             {"icon": "🌫",  "color": "#ccaa66", "osint": "Sandstrum/Dunst – Sichtweite stark reduziert, Luftoperationen eingeschraenkt"},
    "manmade":              {"icon": "⚙",  "color": "#ff4444", "osint": "Anthropogenes Ereignis (EONET-klassifiziert als Menschengemacht)"},
    "snow":                 {"icon": "❄",  "color": "#aaddff", "osint": "Schnee/Eis – Bodentruppen-Beweglichkeit eingeschraenkt"},
    "tempExtremes":         {"icon": "🌡",  "color": "#ff6600", "osint": "Temperaturextrem – operative Einschraenkungen"},
    "waterColor":           {"icon": "💧", "color": "#0088cc", "osint": "Wasserverfaerbung (moeglicherweise Umweltkatastrophe)"},
}

# Konfliktzonen-BBoxen fuer operative Bewertung (West, South, East, North)
_CONFLICT_ZONES: dict[str, tuple[float, float, float, float]] = {
    "Ukraine":       (22.0, 44.0, 40.0, 52.5),
    "Gaza/Israel":   (34.0, 29.5, 36.0, 33.5),
    "Syrien":        (35.5, 32.5, 42.5, 37.5),
    "Jemen":         (42.0, 12.0, 55.0, 19.0),
    "Sudan":         (21.0, 8.0,  39.0, 24.0),
    "Sahel":         (-18.0, 10.0, 24.0, 22.0),
}

def _in_conflict_zone(lat: float, lon: float) -> Optional[str]:
    for name, (w, s, e, n) in _CONFLICT_ZONES.items():
        if w <= lon <= e and s <= lat <= n:
            return name
    return None


def _geocode_bbox(region: str) -> Optional[tuple[float, float, float, float]]:
    """Einfache Geocodierung fuer eine Region."""
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": region, "count": 1, "language": "de", "format": "json"},
            timeout=8,
        )
        res = (r.json().get("results") or [None])[0]
        if res:
            lat, lon = res["latitude"], res["longitude"]
            fc = res.get("feature_code", "")
            m = 8.0 if fc.startswith("PC") else (3.0 if fc.startswith("A") else 2.0)
            return (lon - m, lat - m, lon + m, lat + m)
    except Exception:
        pass
    return None


def fetch_eonet_events(region: str = "", days: int = 7,
                       max_events: int = 50) -> list[dict]:
    """
    Holt aktuelle Naturereignisse von NASA EONET.
    Filtert optional auf eine Region (BBox).
    """
    params: dict = {
        "status":  "open",
        "days":    days,
        "limit":   max_events,
    }

    # Optionaler BBox-Filter
    bbox_str = ""
    if region:
        bbox = _geocode_bbox(region)
        if bbox:
            w, s, e, n = bbox
            bbox_str = f"{w:.2f},{s:.2f},{e:.2f},{n:.2f}"
            params["bbox"] = bbox_str

    try:
        r = requests.get(EONET_API, params=params, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            print(f"[EONET] HTTP {r.status_code}", file=sys.stderr)
            return []
        data = r.json()
    except Exception as e:
        print(f"[EONET] Fehler: {e}", file=sys.stderr)
        return []

    events = []
    for ev in (data.get("events") or []):
        try:
            cats = ev.get("categories") or []
            cat_id = cats[0].get("id", "") if cats else ""
            meta = _CATEGORY_META.get(cat_id, {"icon": "📍", "color": "#888888", "osint": ""})

            # Koordinaten aus letztem Geometrie-Eintrag
            geom = ev.get("geometry") or []
            if not geom:
                continue
            last_geo = geom[-1]
            coords = last_geo.get("coordinates")
            if not coords:
                continue

            # Punkt vs. Polygon
            if last_geo.get("type") == "Point":
                lon, lat = coords[0], coords[1]
            elif last_geo.get("type") == "Polygon":
                # Schwerpunkt des ersten Rings
                ring = coords[0]
                lon = sum(p[0] for p in ring) / len(ring)
                lat = sum(p[1] for p in ring) / len(ring)
            else:
                continue

            date_str = last_geo.get("date", "")
            title    = ev.get("title", "")

            # Operative Einschaetzung bei Sandsturm/Dust in Konfliktzone
            conflict = _in_conflict_zone(lat, lon)
            osint    = meta["osint"]
            if cat_id == "dustHaze" and conflict:
                osint = f"SANDSTRUM in {conflict} – Luftoperationen stark eingeschraenkt, Sichtweite <500m moeglich"
            elif cat_id in ("severeStorms", "floods") and conflict:
                osint = f"{meta['osint']} in KONFLIKTZONE {conflict}"
            elif cat_id == "wildfires" and conflict:
                osint = f"Feuer in {conflict} – Brandursache unklar (OSINT: moeglicherweise kampfbedingt)"

            events.append({
                "lat":      round(lat, 4),
                "lon":      round(lon, 4),
                "title":    title,
                "category": cat_id,
                "icon":     meta["icon"],
                "color":    meta["color"],
                "osint":    osint,
                "date":     date_str[:10] if date_str else "",
                "url":      ev.get("sources", [{}])[0].get("url", "") if ev.get("sources") else "",
                "conflict_zone": conflict or "",
                "source":   "NASA EONET",
            })
        except Exception:
            continue

    events.sort(key=lambda e: (e.get("conflict_zone") == "", e.get("date", "")), reverse=False)
    print(f"[EONET] {len(events)} Ereignisse geladen (Region: {region or 'global'})", file=sys.stderr)
    return events[:max_events]


def eonet_for_map(region: str = "") -> list[dict]:
    """Gibt EONET-Marker fuer die Leaflet-Karte zurueck."""
    return fetch_eonet_events(region=region, days=7, max_events=40)


def eonet_summary(region: str = "") -> str:
    """Text-Zusammenfassung fuer LLM."""
    events = fetch_eonet_events(region=region, days=7, max_events=20)
    if not events:
        return f"[NASA EONET] Keine aktuellen Naturereignisse in {region or 'der Region'} (letzte 7 Tage)."

    conflict_events = [e for e in events if e.get("conflict_zone")]
    lines = [f"[NASA EONET Naturereignisse – {region or 'Global'} (letzte 7 Tage)]",
             f"Gesamt: {len(events)} | In Konfliktzonen: {len(conflict_events)}"]
    for e in events[:6]:
        cz = f" | KONFLIKTZONE: {e['conflict_zone']}" if e.get("conflict_zone") else ""
        lines.append(f"  {e['icon']} {e['title']} | {e['date']}{cz}")
        if e.get("osint"):
            lines.append(f"    → {e['osint']}")
    return "\n".join(lines)


if __name__ == "__main__":
    region = sys.argv[1] if len(sys.argv) > 1 else ""
    print(eonet_summary(region))
