"""
NEXUS - Erdbeben-Modul (USGS) + Detonations-Detektor
Echtzeit-Seismik-Daten via USGS Earthquake API.
Komplett kostenlos, kein API-Key noetig.

Detonations-Filter:
  Natuerliche Beben:  Tiefe > 10km, Magnitude beliebig, globale Verteilung
  Explosionen/Tests:  Tiefe < 10km (oft 0-2km), Magnitude 1.0-4.5,
                      in bekannter Konfliktzone, kein tektonischer Hintergrund

  Kriterien fuer Flagging (OSINT-Hinweis, keine Gewissheit):
    HIGH:   Tiefe < 2km, Magnitude 1.0-4.0, Konfliktzonen-Mittelpunkt
    MEDIUM: Tiefe < 5km, Magnitude 1.0-4.5, Konfliktzone oder Grenzgebiet
    LOW:    Tiefe < 10km, Magnitude 1.5-3.5, geografisch auffaellig

Quellen:
  USGS FDSN API:   https://earthquake.usgs.gov/fdsnws/event/1/
  IRIS Seismogram: https://ds.iris.edu/wilber3/ (manuell)
"""

from __future__ import annotations

import requests
from datetime import datetime, timedelta, timezone
from typing import Optional

USGS_API        = "https://earthquake.usgs.gov/fdsnws/event/1/query"
REQUEST_TIMEOUT = 15

# Standard-Schwellen
MIN_MAG_MAP   = 2.5   # Auf Karte anzeigen
MIN_MAG_ALERT = 4.5   # Als Alarm markieren

# Detonations-Schwellen (niedriger als normale Erdbeben)
DET_MIN_MAG   = 1.0   # Kleiner Sprengkopf erkennbar ab M~1.0
DET_MAX_MAG   = 5.0   # Groesser als nuklearer Test = eigentlich natuerlich
DET_MAX_DEPTH = 10.0  # km – Oberflaechen-nah


# ── Konfliktzonen-Bounding-Boxes ─────────────────────────────────────────────
# (lat_min, lon_min, lat_max, lon_max, zone_name)
_CONFLICT_ZONES: list[tuple] = [
    (44.0,  22.0,  55.0,  42.0,  "Ukraine"),
    (29.0,  34.0,  35.0,  38.0,  "Gaza/Westjordanland"),
    (32.0,  35.5,  33.5,  36.5,  "Israel/Libanon"),
    (33.0,  35.5,  38.0,  42.5,  "Syrien"),
    (12.0,  42.0,  23.0,  55.0,  "Jemen"),
    (24.0,  44.0,  38.0,  63.0,  "Iran"),
    (28.0,  60.0,  38.0,  75.0,  "Afghanistan/Pakistan"),
    ( 3.0,   5.0,  15.0,  23.0,  "Sahel/Sudan"),
    (20.0,  46.0,  30.0,  60.0,  "Persischer Golf"),
    (38.0,  44.0,  42.0,  50.0,  "Suedkaukasus (Berg-Karabach)"),
    (11.0,  41.0,  15.5,  44.0,  "Tigray/Aethiopien"),
    (33.0, 122.0,  43.0, 132.0,  "Koreanische Halbinsel"),
]

# Tektonisch sehr aktive Zonen – KEIN Detonations-Alarm trotz flachem Beben
_TECTONIC_NOISE: list[tuple] = [
    (30.0, 128.0,  46.0, 148.0,  "Japan"),
    (34.0,  26.0,  42.0,  45.0,  "Tuerkei"),
    (-56.0,-73.0,  -2.0, -66.0,  "Suedamerika Suedwesten"),
    (36.0, -25.0,  44.0,  -7.0,  "Azoren/Atlantischer Ruecken"),
    (38.0, 140.0,  46.0, 148.0,  "Kurilenkette"),
    (-20.0,-180.0, 20.0,-150.0,  "Zentralpazifik"),
    ( 0.0, 120.0,  12.0, 136.0,  "Philippinen"),
    (38.0,  73.0,  42.0,  80.0,  "Pamir/Tadschikistan"),
]


def _in_zone(lat: float, lon: float,
             zones: list[tuple]) -> Optional[str]:
    """Gibt Zonenname zurueck, wenn (lat,lon) in einer Zone liegt."""
    for lat_min, lon_min, lat_max, lon_max, name in zones:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


def _detonation_confidence(mag: float, depth: Optional[float],
                            lat: float, lon: float) -> tuple[str, str]:
    """
    Gibt (confidence, hint) fuer moegliche Detonation zurueck.
    confidence: "high" | "medium" | "low" | ""
    hint:       Erklaerungstext
    """
    if depth is None:
        return "", ""
    if not (DET_MIN_MAG <= mag <= DET_MAX_MAG):
        return "", ""
    if depth > DET_MAX_DEPTH:
        return "", ""

    # Tektonisches Rauschen unterdruecken
    tectonic = _in_zone(lat, lon, _TECTONIC_NOISE)
    if tectonic:
        return "", ""

    conflict = _in_zone(lat, lon, _CONFLICT_ZONES)

    if depth < 2.0 and 1.0 <= mag <= 4.0 and conflict:
        return "high", (
            f"MOEGLICHE DETONATION ({conflict}) – "
            f"Tiefe {depth:.1f}km, M{mag} "
            f"entspricht ca. {_mag_to_tnt(mag)} TNT-Aequivalent"
        )
    elif depth < 5.0 and mag <= 4.5 and conflict:
        return "medium", (
            f"Verdaechtiges Beben ({conflict}) – "
            f"Tiefe {depth:.1f}km, M{mag} – Detonation nicht ausgeschlossen"
        )
    elif depth < 10.0 and 1.5 <= mag <= 3.5 and conflict:
        return "low", (
            f"Flaches Beben in Konfliktzone ({conflict}) – "
            f"Tiefe {depth:.1f}km, M{mag} – OSINT-Beobachtung empfohlen"
        )
    elif depth < 5.0 and 1.5 <= mag <= 3.5 and not conflict:
        # Flaches Beben ausserhalb bekannter Konfliktzonen aber trotzdem verdaechtig
        return "low", (
            f"Flaches Beben (Tiefe {depth:.1f}km, M{mag}) – "
            f"keine bekannte Konfliktzuordnung, aber ungewoehnlich"
        )

    return "", ""


def _mag_to_tnt(mag: float) -> str:
    """Grobe Umrechnung Magnitude -> TNT-Aequivalent (fuer OSINT-Kontext)."""
    # Formel: log10(E_TNT) = 1.5*M - 1.2 (sehr grob)
    import math
    tnt_kg = 10 ** (1.5 * mag - 1.2)
    if tnt_kg < 1000:
        return f"{tnt_kg:.0f} kg TNT"
    elif tnt_kg < 1_000_000:
        return f"{tnt_kg/1000:.1f} t TNT"
    else:
        return f"{tnt_kg/1_000_000:.1f} kt TNT"


# ── Haupt-Abfrage-Funktion ───────────────────────────────────────────────────

def get_earthquakes(
    lat_min: float = -90, lon_min: float = -180,
    lat_max: float =  90, lon_max: float =  180,
    hours:   int   = 24,
    min_mag: float = MIN_MAG_MAP,
    include_small: bool = False,
) -> list[dict]:
    """
    Ruft Erdbeben vom USGS GeoJSON-Feed ab.
    include_small=True: auch M1.0+ fuer Detonations-Screening in Konfliktzonen.
    """
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    effective_min = DET_MIN_MAG if include_small else min_mag

    params = {
        "format":       "geojson",
        "starttime":    start.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime":      end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minlatitude":  lat_min,
        "maxlatitude":  lat_max,
        "minlongitude": lon_min,
        "maxlongitude": lon_max,
        "minmagnitude": effective_min,
        "orderby":      "time",
        "limit":        200,
    }
    try:
        r = requests.get(USGS_API, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = []
    for feat in (data.get("features") or []):
        props  = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates", [])
        if len(coords) < 2:
            continue

        lon, lat, depth = coords[0], coords[1], (coords[2] if len(coords) > 2 else None)
        mag    = props.get("mag", 0) or 0
        place  = props.get("place", "Unbekannter Ort")
        ts_ms  = props.get("time", 0)
        url    = props.get("url", "#")
        mtype  = props.get("magType", "")

        ts_dt  = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        ts_str = ts_dt.strftime("%d.%m.%Y %H:%M UTC")
        age_min= int((datetime.now(timezone.utc) - ts_dt).total_seconds() / 60)

        # Detonations-Analyse
        det_conf, det_hint = _detonation_confidence(mag, depth, lat, lon)
        conflict_zone      = _in_zone(lat, lon, _CONFLICT_ZONES)

        # Klassischer OSINT-Hinweis fuer groessere Beben
        osint_hint = det_hint if det_hint else ""
        if not osint_hint:
            if mag >= 6.0:
                osint_hint = "Starkes Erdbeben – moegliche humanitaere Auswirkungen"
            elif mag >= 4.5:
                osint_hint = "Relevantes Erdbeben fuer Krisenregion"
            elif depth is not None and depth < 5 and mag >= 3.5:
                osint_hint = f"Sehr flach ({depth:.1f}km) – moegl. Explosion/Test"

        results.append({
            "lat":            round(lat, 4),
            "lon":            round(lon, 4),
            "depth_km":       round(depth, 1) if depth is not None else None,
            "mag":            round(mag, 1),
            "mag_type":       mtype,
            "place":          place,
            "timestamp":      ts_str,
            "age_min":        age_min,
            "url":            url,
            "alert":          mag >= MIN_MAG_ALERT,
            "osint_hint":     osint_hint,
            "conflict_zone":  conflict_zone or "",
            "det_confidence": det_conf,
            "det_hint":       det_hint,
        })

    return results


# ── Regionale Abfrage ────────────────────────────────────────────────────────

_REGION_BOXES: dict[str, tuple] = {
    "Naher Osten":      (15.0,  25.0,  42.0,  65.0),
    "Ukraine":          (44.0,  22.0,  55.0,  42.0),
    "Taiwan-Strasse":   (18.0, 110.0,  35.0, 130.0),
    "Korea-Halbinsel":  (33.0, 122.0,  43.0, 132.0),
    "Iran":             (25.0,  44.0,  40.0,  64.0),
    "Tuerktei":         (36.0,  26.0,  42.0,  45.0),
    "Japan":            (30.0, 128.0,  46.0, 148.0),
    "Persischer Golf":  (20.0,  46.0,  30.0,  62.0),
    "Rotes Meer":       (10.0,  30.0,  28.0,  46.0),
    "Gaza":             (29.0,  34.0,  33.0,  36.5),
    "Israel":           (29.0,  34.0,  33.5,  36.0),
    "Syrien":           (33.0,  35.5,  38.0,  42.5),
    "Jemen":            (12.0,  42.0,  23.0,  55.0),
    "Afghanistan":      (28.0,  60.0,  38.5,  75.0),
    "Sudan":            ( 3.0,  22.0,  23.0,  40.0),
}


def get_earthquakes_for_region(region: str, hours: int = 24,
                                include_small: bool = True) -> list[dict]:
    """
    Erdbeben fuer eine benannte Region.
    Prioritaet: eigene _REGION_BOXES → nexus_region Fallback → Open-Meteo Geocoding
    """
    # 1. Eigene REGION_BOXES (schnell, offline)
    for name, box in _REGION_BOXES.items():
        if name.lower() in region.lower() or region.lower() in name.lower():
            return get_earthquakes(*box, hours=hours, include_small=include_small)

    # 2. nexus_region hierarchischer Fallback (z.B. "Natanz" → "Iran" → BBox)
    try:
        from nexus_region import get_bbox_with_fallback
        bbox, resolved = get_bbox_with_fallback(region)
        if bbox and resolved != "global":
            lon_min, lat_min, lon_max, lat_max = bbox
            return get_earthquakes(lat_min, lon_min, lat_max, lon_max,
                                   hours=hours, include_small=include_small)
    except ImportError:
        pass

    # 3. Open-Meteo Geocoding (fuer unbekannte Orte)
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": region, "count": 1, "language": "de", "format": "json"},
            timeout=8,
        )
        res = (r.json().get("results") or [None])[0]
        if res:
            lat, lon = res["latitude"], res["longitude"]
            m = 5.0
            return get_earthquakes(lat-m, lon-m, lat+m, lon+m, hours=hours,
                                   include_small=include_small)
    except Exception:
        pass

    return get_earthquakes(min_mag=MIN_MAG_ALERT, hours=hours)


def get_detonation_candidates(region: str, hours: int = 24) -> list[dict]:
    """
    Gibt nur moegliche Detonations-Kandidaten zurueck (det_confidence != "").
    Sortiert nach Konfidenz (high -> medium -> low) dann nach Alter.
    """
    quakes = get_earthquakes_for_region(region, hours=hours, include_small=True)
    candidates = [q for q in quakes if q.get("det_confidence")]
    order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda x: (order.get(x["det_confidence"], 9), x["age_min"]))
    return candidates


# ── Karten-Export ────────────────────────────────────────────────────────────

def earthquakes_for_map(region: str, hours: int = 24) -> list[dict]:
    """Gibt Erdbeben-Marker fuer die Live-Karte zurueck."""
    quakes = get_earthquakes_for_region(region, hours=hours, include_small=True)
    result = []
    for q in quakes:
        # Nur auf Karte zeigen wenn: normal >= MIN_MAG_MAP ODER Detonations-Kandidat
        if q["mag"] < MIN_MAG_MAP and not q.get("det_confidence"):
            continue
        result.append({
            "lat":            q["lat"],
            "lon":            q["lon"],
            "mag":            q["mag"],
            "depth_km":       q["depth_km"],
            "place":          q["place"],
            "timestamp":      q["timestamp"],
            "url":            q["url"],
            "alert":          q["alert"],
            "osint_hint":     q["osint_hint"],
            "conflict_zone":  q.get("conflict_zone", ""),
            "det_confidence": q.get("det_confidence", ""),
            "det_hint":       q.get("det_hint", ""),
        })
    return result


# ── Text-Zusammenfassung ─────────────────────────────────────────────────────

def seismic_summary(region: str, hours: int = 24) -> str:
    """Gibt Text-Zusammenfassung fuer den LLM zurueck."""
    quakes = get_earthquakes_for_region(region, hours=hours, include_small=True)
    if not quakes:
        return f"[SEISMIK] Keine Erdbeben in {region} (letzte {hours}h)."

    alerts     = [q for q in quakes if q["alert"]]
    detonation = [q for q in quakes if q.get("det_confidence")]
    lines = [
        f"[SEISMIK – {region} | letzte {hours}h]",
        f"Beben gesamt: {len(quakes)} | Relevant (>={MIN_MAG_ALERT}): {len(alerts)} "
        f"| Detonations-Kandidaten: {len(detonation)}",
    ]
    if detonation:
        lines.append("DETONATIONS-SCREENING:")
        for q in detonation[:4]:
            conf_icons = {"high": "🔴", "medium": "🟡", "low": "⚪"}
            icon = conf_icons.get(q["det_confidence"], "?")
            lines.append(
                f"  {icon} [{q['det_confidence'].upper()}] M{q['mag']} "
                f"Tiefe {q['depth_km']}km | {q['place']} | {q['timestamp']}"
            )
            lines.append(f"    → {q['det_hint']}")
    lines.append("Groesste natuerliche Beben:")
    for q in sorted(quakes, key=lambda x: -x["mag"])[:3]:
        if q.get("det_confidence"):
            continue
        hint = f" [{q['osint_hint']}]" if q["osint_hint"] else ""
        lines.append(f"  M{q['mag']} | {q['place']} | {q['timestamp']}{hint}")
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Ukraine"
    print(seismic_summary(region, hours=48))
    print()
    cands = get_detonation_candidates(region, hours=48)
    if cands:
        print(f"Detonations-Kandidaten ({len(cands)}):")
        for c in cands:
            print(f"  [{c['det_confidence'].upper()}] M{c['mag']} {c['depth_km']}km – {c['place']}")
            print(f"    {c['det_hint']}")
    else:
        print("Keine Detonations-Kandidaten.")
