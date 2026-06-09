"""
NEXUS - NASA FIRMS Brandmodul (Fire Information for Resource Management System)
Echtzeit-Satelliten-Branderkennung via MODIS + VIIRS Sensoren.
Kostenlos, kein Key noetig fuer Karten-Tiles (NASA GIBS).
Optionaler MAP_KEY fuer Rohdaten-API (kostenlos registrierbar auf firms.modaps.eosdis.nasa.gov).
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timezone
from typing import Optional

import requests

REQUEST_TIMEOUT = 15
FIRMS_API_BASE  = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Datensaetze in Prioritaetsreihenfolge (neueste/zuverlaessigste zuerst)
_DATASETS = [
    "VIIRS_NOAA21_NRT",   # JPSS-2 / NOAA-21 (neueste, seit 2023)
    "VIIRS_NOAA20_NRT",   # JPSS-1 / NOAA-20 (sehr zuverlaessig)
    "VIIRS_SNPP_NRT",     # Suomi-NPP (aelterer Satellit)
    "MODIS_NRT",          # MODIS Terra+Aqua (breite Abdeckung, niedrigere Aufloesung)
]

# Bekannte Konfliktregionen Bounding Boxes (West, South, East, North)
_REGION_BOXES: dict[str, tuple[float, float, float, float]] = {
    "Ukraine":          (22.0, 44.0, 40.0, 52.5),
    "Charkiw":          (35.5, 49.2, 37.5, 51.0),
    "Kharkiv":          (35.5, 49.2, 37.5, 51.0),
    "Gaza":             (34.2, 31.2, 34.6, 31.6),
    "Israel":           (34.2, 29.5, 35.9, 33.5),
    "Naher Osten":      (25.0, 15.0, 70.0, 42.0),
    "Syrien":           (35.5, 32.5, 42.5, 37.5),
    "Iran":             (44.0, 25.0, 63.5, 40.0),
    "Hormuz-Strasse":   (56.0, 22.0, 60.0, 27.0),
    "Rotes Meer":       (32.0, 12.0, 44.0, 28.0),
    "Taiwan-Strasse":   (118.0, 21.0, 125.0, 27.0),
    "Korea-Halbinsel":  (124.0, 34.0, 130.0, 42.0),
    "Sahel":            (-18.0, 10.0, 24.0, 22.0),
    "Sudan":            (21.0, 8.0, 39.0, 24.0),
    "Jemen":            (42.0, 12.0, 55.0, 19.0),
}


# ── Bekannte Industrieanlagen: Fackeln / Raffinerien / Gas-Infrastruktur ──────
# Diese Feuerpunkte sind dauerhaft erwartet und KEIN Angriffs-Indikator.
# Format: (lat, lon, name, typ)
# Quellen: Satellitenbilder, Wikipedia, EIA, öffentliche Karten
_KNOWN_INDUSTRIAL: list[tuple[float, float, str, str]] = [
    # ── Iran ──────────────────────────────────────────────────────────────────
    (30.340, 48.280, "Abadan Raffinerie",              "raffinerie"),
    (30.430, 49.070, "Bandar Imam Khomeini Raffinerie","raffinerie"),
    (27.190, 56.270, "Bandar Abbas Raffinerie",        "raffinerie"),
    (32.500, 51.700, "Isfahan Raffinerie",              "raffinerie"),
    (35.500, 51.450, "Teheran Raffinerie (Shahr-e Ray)","raffinerie"),
    (34.070, 49.680, "Arak Raffinerie",                "raffinerie"),
    (38.090, 46.350, "Tabriz Raffinerie",              "raffinerie"),
    (36.800, 54.430, "Neka Raffinerie",                "raffinerie"),
    (26.800, 53.350, "Lavan Ölterminal",               "terminal"),
    (29.240, 50.320, "Kharg Island Ölterminal",        "terminal"),
    (27.500, 52.600, "South Pars / Assaluyeh (Gas)",   "gasfeld"),
    (29.900, 50.800, "Bushehr Petrochemie",            "petrochemie"),
    (30.200, 48.500, "Mahshahr Petrochemie",           "petrochemie"),
    (31.700, 48.700, "Masjed Soleyman Ölfeld",         "oelfeld"),
    (31.300, 49.200, "Ahvaz Ölfeld",                   "oelfeld"),
    (30.800, 49.500, "Gachsaran Ölfeld",               "oelfeld"),
    (28.900, 51.600, "Shiraz Raffinerie",              "raffinerie"),
    # ── Irak (Naher Osten Kontext) ────────────────────────────────────────────
    (30.500, 47.800, "Basra Raffinerie",               "raffinerie"),
    (35.320, 43.130, "Baiji Raffinerie",               "raffinerie"),
    (36.200, 44.000, "Kirkuk Ölfeld",                  "oelfeld"),
    # ── Saudi-Arabien ─────────────────────────────────────────────────────────
    (26.300, 50.100, "Ras Tanura Raffinerie",          "raffinerie"),
    (26.400, 49.900, "Abqaiq Ölanlage",               "oelfeld"),
    # ── Ukraine (Konfliktzonen) ───────────────────────────────────────────────
    (47.900, 35.100, "Saporizhzhia Industriezone",     "industrie"),
    (48.500, 38.000, "Lyssytschansk Raffinerie (Ruine)","raffinerie"),
]

_INDUSTRIAL_RADIUS_KM = 12.0  # Feuerpunkte innerhalb dieses Radius = industriell


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Berechnet Distanz in km zwischen zwei GPS-Punkten."""
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def classify_fire(lat: float, lon: float, frp: float = 0) -> dict:
    """
    Klassifiziert einen Feuerpunkt als industriell (erwartet) oder anomal (verdächtig).

    Rückgabe:
      type:     'industrial' | 'anomaly' | 'unknown'
      facility: Name der nächsten bekannten Anlage (wenn industriell)
      dist_km:  Distanz zur nächsten bekannten Anlage
      note:     Erklärungstext für Analyst
    """
    nearest_name = ""
    nearest_type = ""
    nearest_dist = 9999.0

    for ilat, ilon, iname, itype in _KNOWN_INDUSTRIAL:
        dist = _haversine_km(lat, lon, ilat, ilon)
        if dist < nearest_dist:
            nearest_dist = dist
            nearest_name = iname
            nearest_type = itype

    if nearest_dist <= _INDUSTRIAL_RADIUS_KM:
        return {
            "type":     "industrial",
            "facility": nearest_name,
            "fac_type": nearest_type,
            "dist_km":  round(nearest_dist, 1),
            "note":     f"Bekannte Industrieanlage: {nearest_name} ({nearest_type}, {nearest_dist:.1f}km) — Dauerfeuer erwartet, KEIN Angriffsindikator",
        }
    elif nearest_dist <= 30.0:
        return {
            "type":     "unknown",
            "facility": nearest_name,
            "fac_type": nearest_type,
            "dist_km":  round(nearest_dist, 1),
            "note":     f"Nähe zu {nearest_name} ({nearest_dist:.1f}km) — möglicherweise Ausbreitung oder neue Quelle",
        }
    else:
        return {
            "type":     "anomaly",
            "facility": "",
            "fac_type": "",
            "dist_km":  round(nearest_dist, 1),
            "note":     f"Kein bekanntes Industrieziel in {nearest_dist:.0f}km — ANOMALIE, operativ relevant",
        }


def _geocode_bbox(region: str) -> Optional[tuple[float, float, float, float]]:
    """
    Geocodiert eine Region zu einer Bounding Box (W, S, E, N).
    Adaptiver Margin je nach Ortstyp.
    """
    # Bekannte Regionen direkt
    for name, box in _REGION_BOXES.items():
        if name.lower() in region.lower() or region.lower() in name.lower():
            return box
    # Open-Meteo Geocoding mit adaptivem Margin
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
            if fc.startswith("PC"):
                m = 8.0
            elif fc.startswith("A"):
                m = 3.0
            elif fc in ("PPLC", "PPLA"):
                m = 2.0
            else:
                m = 1.5
            return (lon - m, lat - m, lon + m, lat + m)
    except Exception:
        pass
    return None


def fetch_firms_fires(region: str, days: int = 1,
                      map_key: str = "") -> list[dict]:
    """
    Holt aktive Feuerpunkte via NASA FIRMS API.
    Probiert mehrere Satelliten-Datensaetze (NOAA-21 -> NOAA-20 -> SNPP -> MODIS).
    map_key wird automatisch aus config.py gelesen wenn nicht angegeben (T153).
    Nutzt nexus_region.py fuer hierarchischen Fallback (T152/Global).
    """
    # T153: Key automatisch aus config laden wenn nicht explizit uebergeben
    if not map_key:
        map_key = _get_key()
    if not map_key:
        return []

    # T152/Global: Erst nexus_region, dann _REGION_BOXES, dann Geocoding
    bbox = None
    resolved_name = region
    try:
        from nexus_region import get_bbox_with_fallback
        bbox, resolved_name = get_bbox_with_fallback(region)
    except ImportError:
        pass

    if not bbox:
        # Eigene REGION_BOXES als Fallback
        bbox = _geocode_bbox(region)

    if not bbox:
        print(f"[FIRMS] Region nicht gefunden: {region}", file=sys.stderr)
        return []

    w, s, e, n = bbox
    bbox_str = f"{w:.2f},{s:.2f},{e:.2f},{n:.2f}"

    for dataset in _DATASETS:
        url = f"{FIRMS_API_BASE}/{map_key}/{dataset}/{bbox_str}/{days}"
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 401:
                print(f"[FIRMS] API-Key ungueltig (401). Key: {map_key[:8]}...",
                      file=sys.stderr)
                return []
            if r.status_code == 429:
                print("[FIRMS] Rate-Limit erreicht (429)", file=sys.stderr)
                return []
            if not r.ok:
                print(f"[FIRMS] {dataset}: HTTP {r.status_code}", file=sys.stderr)
                continue

            text = r.text.strip()
            if not text or text.startswith("<!") or len(text) < 10:
                continue

            reader = csv.DictReader(io.StringIO(text))
            fires  = []
            now_ts = datetime.now(timezone.utc).timestamp()

            for row in reader:
                try:
                    lat  = float(row.get("latitude",  0))
                    lon  = float(row.get("longitude", 0))
                    if not lat or not lon:
                        continue
                    conf  = row.get("confidence", "n")
                    frp   = float(row.get("frp", 0) or 0)
                    date  = row.get("acq_date", "")
                    time_ = row.get("acq_time", "0000")

                    try:
                        dt_str = f"{date} {time_[:2]}:{time_[2:]}:00"
                        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=timezone.utc)
                        age_min = int((now_ts - dt.timestamp()) / 60)
                    except Exception:
                        age_min = 999

                    osint = ""
                    if frp > 500:
                        osint = "Sehr intensives Feuer (FRP>{:.0f}MW) - moglicher Grossbrand/Treffer".format(frp)
                    elif frp > 100:
                        osint = "Intensives Feuer ({:.0f}MW)".format(frp)
                    elif frp > 20:
                        osint = "Mittleres Feuer ({:.0f}MW)".format(frp)
                    elif conf == "h":
                        osint = "Feuer bestaetigt (hohe Konfidenz)"

                    # Industriefeuer-Klassifikation — global via nexus_industrial
                    try:
                        from nexus_industrial import classify_fire_global  # type: ignore
                        klassifikation = classify_fire_global(lat, lon, region, bbox, frp)
                    except Exception:
                        klassifikation = classify_fire(lat, lon, frp)
                    fire_type = klassifikation["type"]

                    # OSINT-Note anpassen je nach Typ
                    if fire_type == "industrial":
                        osint = klassifikation["note"]
                    elif fire_type == "anomaly" and frp > 20:
                        osint = f"⚠️ ANOMALIE: {klassifikation['note']}"
                        if frp > 100:
                            osint += f" | FRP {frp:.0f}MW — sehr intensiv"
                    elif osint:
                        osint = osint + (f" | {klassifikation['note']}" if fire_type != "industrial" else "")

                    fires.append({
                        "lat":        round(lat, 4),
                        "lon":        round(lon, 4),
                        "frp":        frp,
                        "confidence": conf,
                        "date":       date,
                        "age_min":    age_min,
                        "osint":      osint,
                        "source":     f"NASA FIRMS/{dataset}",
                        "dataset":    dataset,
                        "fire_type":  fire_type,
                        "facility":   klassifikation.get("facility", ""),
                        "dist_km":    klassifikation.get("dist_km", 0),
                    })
                except Exception:
                    continue

            if fires:
                fires = [f for f in fires if f["frp"] > 5 or f["confidence"] == "h"]
                fires.sort(key=lambda f: f.get("frp", 0), reverse=True)
                print(f"[FIRMS] {dataset}: {len(fires)} Feuerpunkte fuer {region}",
                      file=sys.stderr)
                return fires[:75]
            else:
                print(f"[FIRMS] {dataset}: Keine Daten fuer {region} (BBox: {bbox_str})",
                      file=sys.stderr)
                continue

        except requests.Timeout:
            print(f"[FIRMS] {dataset}: Timeout", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"[FIRMS] {dataset}: Fehler - {exc}", file=sys.stderr)
            continue

    print(f"[FIRMS] Alle Datensaetze ohne Ergebnis fuer {region}", file=sys.stderr)
    return []


def _get_key(map_key: str = "") -> str:
    """Liest Key aus Argument oder config.py."""
    if map_key:
        return map_key
    try:
        import config
        return getattr(config, "FIRMS_MAP_KEY", "")
    except Exception:
        return ""


def fires_for_map(region: str, map_key: str = "") -> list[dict]:
    """Gibt Feuer-Marker fuer die Leaflet-Karte zurueck."""
    fires = fetch_firms_fires(region, days=1, map_key=_get_key(map_key))
    return [
        {
            "lat":     f["lat"],
            "lon":     f["lon"],
            "frp":     f["frp"],
            "osint":   f["osint"],
            "age_min": f["age_min"],
        }
        for f in fires
    ]


def firms_summary(region: str, map_key: str = "") -> str:
    """Text-Zusammenfassung fuer LLM."""
    key = _get_key(map_key)
    if not key:
        return (
            f"[NASA FIRMS] Satelliten-Brandmonitoring fuer {region}: "
            "Kein MAP_KEY konfiguriert. "
            "Kostenlos registrieren: https://firms.modaps.eosdis.nasa.gov/api/map_key/"
        )
    fires = fetch_firms_fires(region, days=1, map_key=key)
    if not fires:
        return f"[NASA FIRMS] Keine signifikanten Braende in {region} in den letzten 24h."

    industrial = [f for f in fires if f.get("fire_type") == "industrial"]
    anomalies  = [f for f in fires if f.get("fire_type") == "anomaly"]
    unknown    = [f for f in fires if f.get("fire_type") == "unknown"]
    intense    = [f for f in fires if f["frp"] > 100]
    dataset    = fires[0].get("dataset", "VIIRS") if fires else "VIIRS"

    lines = [
        f"[NASA FIRMS Satelliten-Brand-Erkennung - {region} via {dataset}]",
        f"Gesamt: {len(fires)} Feuerpunkte | Industriell (erwartet): {len(industrial)} | Anomalien: {len(anomalies)} | Unbekannt: {len(unknown)}",
    ]

    # Anomalien zuerst — das ist das Wichtige
    if anomalies:
        lines.append(f"\n  ⚠️ OPERATIVE ANOMALIEN ({len(anomalies)}) — nicht bei bekannten Anlagen:")
        for f in sorted(anomalies, key=lambda x: -x["frp"])[:5]:
            age_s = f"{f['age_min']}min" if f["age_min"] < 120 else f"{f['age_min']//60}h"
            lines.append(
                f"    → FRP:{f['frp']:.0f}MW | {f['lat']:.3f}°N {f['lon']:.3f}°E | vor {age_s}"
            )

    # Dann Industriefeuer kurz zusammengefasst
    if industrial:
        fac_namen = list({f["facility"] for f in industrial if f.get("facility")})[:4]
        lines.append(f"\n  🏭 Industriefeuer (erwartet, kein Angriffsindikator): {len(industrial)} Punkte")
        if fac_namen:
            lines.append(f"     Anlagen: {', '.join(fac_namen)}")

    # Intense fires Hinweis
    if intense:
        lines.append(f"\n  🔥 Sehr intensiv (>100MW): {len(intense)} Punkte")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    region  = sys.argv[1] if len(sys.argv) > 1 else "Ukraine"
    map_key = sys.argv[2] if len(sys.argv) > 2 else ""
    print(firms_summary(region, map_key))
    if not map_key and not _get_key():
        print("\nTipp: MAP_KEY kostenlos holen auf:")
        print("  https://firms.modaps.eosdis.nasa.gov/api/map_key/")
