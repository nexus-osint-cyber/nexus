"""
NEXUS - NOTAM-Modul (Notice to Airmen)
Aktive Luftsperrgebiete und Einschränkungen via FAA NOTAM API v1.
Kein API-Key nötig – öffentliche FAA/ICAO-Daten, deckt internationale NOTAMs ab.

T161: ICAO-basierte Abfragen für Nicht-FAA-Regionen (Iran, Naher Osten, etc.)

OSINT-Relevanz:
  • Aktive Luftsperrgebiete über Krisenregionen → Hinweis auf Militäraktivitäten
  • TFRs (Temporary Flight Restrictions) → VIP-Bewegungen, Übungen, Gefahrenlagen
  • D-Gebiete (Danger Areas) → Schieß- und Testgebiete aktiv
"""

from __future__ import annotations

import re
import sys
import requests
from datetime import datetime, timezone
from typing import Optional

NOTAM_API       = "https://external-api.faa.gov/notamapi/v1/notams"
AVWX_API        = "https://avwx.rest/api/notam/"   # Backup, braucht Key
REQUEST_TIMEOUT = 15

# ── T161: ICAO-Locations für internationale Krisenregionen ────────────────────
# FAA NOTAM API unterstützt auch internationalen ICAO-Location-Code-Abruf.
# Format: ?icaoLocation=OIII gibt alle NOTAMs für diesen Flughafen/FIR zurück.
# FIR = Flight Information Region – der wichtigste Suchterm für Flugräume.
_REGION_ICAO_LOCATIONS: dict[str, list[str]] = {
    "iran":             ["OIIX", "OIII", "OIKB", "OIFM", "OIBB", "OISS"],
    # OIIX = Tehran FIR/UIR, OIII = Mehrabad, OIKB = Bandar Abbas,
    # OIFM = Isfahan, OIBB = Bushehr (nuklear), OISS = Shiraz
    "israel":           ["LLLL", "LLOV", "LLTL"],
    # LLLL = Israel FIR, LLOV = Ben Gurion, LLTL = Tel Aviv Control
    "libanon":          ["OLLL", "OLBA"],
    # OLLL = Beirut FIR, OLBA = Beirut Int.
    "syrien":           ["OSTT", "OSDZ"],
    # OSTT = Damascus FIR, OSDZ = Damascus Int.
    "irak":             ["ORBB", "ORBI", "ORMM"],
    # ORBB = Baghdad FIR, ORBI = Baghdad Int., ORMM = Basra
    "jemen":            ["OYSC", "OYAA"],
    # OYSC = Sana'a FIR, OYAA = Aden
    "ukraine":          ["UKBV", "UKBB", "UKLL"],
    # UKBV = Dnipro FIR, UKBB = Boryspil, UKLL = Lviv
    "taiwan":           ["RCAA", "RCTP"],
    # RCAA = Taipei FIR, RCTP = Taoyuan Int.
    "korea-halbinsel":  ["RKRR", "RKSS"],
    # RKRR = Incheon FIR, RKSS = Incheon Int.
    "nordkorea":        ["ZKPY"],
    "naher osten":      ["OIIX", "OLLL", "OSTT", "ORBB", "OYSC", "LLLL"],
    "hormuz-strasse":   ["OIKB", "OOMM"],
    # OIKB = Bandar Abbas (Iran), OOMM = Muscat (Oman)
    "persischer golf":  ["OIKB", "OOMM", "OMAE", "OEGN"],
    # Bandar Abbas + Muscat + UAE + Saudi
    "rotes meer":       ["HECA", "OYAA", "HSSS"],
    # Cairo + Aden + Khartoum
    "sahel":            ["GAGO", "DRRN", "DFFD"],
    "sudan":            ["HSSS", "HSSN"],
    "libyen":           ["HLLL", "HLLM"],
    "somalia":          ["HCSM", "HCMS"],
    "afghanistan":      ["OAKX", "OAIX"],
    "pakistan":         ["OPKR", "OPKL", "OPIS"],
}


# ── ICAO-Länder-Präfixe → Koordinaten (für Karten-Platzierung) ────────────
_ICAO_CENTERS: dict[str, tuple[float, float]] = {
    "ED": (51.2,  10.4),   # Deutschland
    "EK": (56.0,  10.6),   # Dänemark
    "EF": (61.9,  25.7),   # Finnland
    "EH": (52.3,   5.3),   # Niederlande
    "EP": (52.0,  20.0),   # Polen
    "ET": (51.2,  10.4),   # Deutschland Militär
    "LB": (42.7,  25.5),   # Bulgarien
    "LC": (35.1,  33.4),   # Zypern
    "LG": (38.0,  23.7),   # Griechenland
    "LH": (47.2,  19.0),   # Ungarn
    "LI": (41.9,  12.5),   # Italien
    "LK": (49.8,  15.5),   # Tschechien
    "LL": (31.8,  35.2),   # Israel
    "LO": (47.8,  13.0),   # Österreich
    "LP": (39.4,  -8.2),   # Portugal
    "LS": (46.8,   8.2),   # Schweiz
    "LT": (39.9,  32.8),   # Türkei
    "LY": (44.8,  20.5),   # Serbien/Westbalkan
    "LZ": (48.6,  19.5),   # Slowakei
    "OK": (47.5,  19.1),   # ← eigentlich LH, Fehlerfall
    "OI": (32.4,  53.7),   # Iran
    "OJ": (31.9,  35.9),   # Jordanien
    "OL": (33.8,  35.5),   # Libanon
    "OM": (24.5,  54.4),   # UAE
    "OP": (33.7,  73.1),   # Pakistan
    "OR": (33.2,  44.4),   # Irak
    "OS": (33.5,  36.3),   # Syrien
    "OT": (25.3,  51.5),   # Katar
    "OY": (15.5,  44.2),   # Jemen
    "RK": (37.5, 127.0),   # Südkorea
    "RO": (35.5, 127.9),   # ← Japan-Alternativ
    "RJ": (35.7, 139.7),   # Japan
    "UK": (50.4,  30.5),   # Ukraine
    "UB": (40.4,  49.9),   # Aserbaidschan
    "UD": (40.2,  44.5),   # Armenien
    "UG": (42.3,  43.4),   # Georgien
    "UR": (47.0,  37.5),   # Russland Süd
    "ZB": (40.0, 116.6),   # China Peking
    "ZS": (31.2, 121.5),   # China Shanghai
    "VH": (22.3, 114.2),   # Hongkong
    "RC": (25.1, 121.2),   # Taiwan
    "WS": ( 1.4, 103.9),   # Singapur
}


def _icao_to_coords(icao: str) -> Optional[tuple[float, float]]:
    """Gibt ungefähre Koordinaten für ICAO-Präfix zurück."""
    if len(icao) >= 2:
        return _ICAO_CENTERS.get(icao[:2].upper())
    return None


# ── FAA NOTAM API ────────────────────────────────────────────────────────────

def _fetch_faa_notams(lat: float, lon: float, radius_nm: int = 200) -> list[dict]:
    """
    Holt NOTAMs von der FAA NOTAM API für einen Bereich.
    Kostenlos, kein Key – öffentliche FAA-Daten.
    """
    params = {
        "locationLatitude":  lat,
        "locationLongitude": lon,
        "locationRadius":    radius_nm,
        "pageSize":          50,
        "pageNum":           1,
    }
    headers = {
        "Accept": "application/json",
        "client_id":     "nexus_osint",
        "client_secret": "nexus_osint",
    }
    try:
        r = requests.get(NOTAM_API, params=params, headers=headers,
                         timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data   = r.json()
        notams = data.get("items") or []
        result = []
        for n in notams[:30]:
            props = n.get("properties") or {}
            geo   = n.get("geometry") or {}

            # Koordinaten aus Geometrie oder ICAO-Fallback
            lat_n, lon_n = None, None
            coords = geo.get("coordinates")
            if coords and isinstance(coords, list) and len(coords) >= 2:
                lon_n, lat_n = coords[0], coords[1]
            elif coords and isinstance(coords, list):
                # Polygon → Zentroid aus erstem Punkt
                try:
                    lon_n = coords[0][0][0]
                    lat_n = coords[0][0][1]
                except Exception:
                    pass

            icao = props.get("location", "")
            if lat_n is None:
                fallback = _icao_to_coords(icao)
                if fallback:
                    lat_n, lon_n = fallback

            text  = props.get("coreNOTAMData", {})
            notam_text = (text.get("notam", {}).get("text") or
                          props.get("abstractText") or "")[:300]

            classification = props.get("classification", "")
            series         = props.get("series", "")

            # Relevanz-Einschätzung
            osint = ""
            t = notam_text.upper()
            if any(w in t for w in ["AIRSPACE RESTRICTED","R AREA","PROHIBITED","P AREA"]):
                osint = "⛔ Sperrgebiet aktiv"
            elif any(w in t for w in ["MILITARY","MIL ","ARMED","EXERCISE","EXER"]):
                osint = "⚠ Militärische Aktivität"
            elif any(w in t for w in ["TFR","TEMPORARY FLIGHT"]):
                osint = "ℹ Temporäre Sperrzone"
            elif any(w in t for w in ["DRONE","UAS","UAV","RPAS"]):
                osint = "ℹ Drohnen-Einschränkung"

            result.append({
                "lat":      round(lat_n, 4) if lat_n else None,
                "lon":      round(lon_n, 4) if lon_n else None,
                "icao":     icao,
                "text":     notam_text,
                "series":   series,
                "class":    classification,
                "osint":    osint,
                "url":      f"https://www.notams.faa.gov/dinsQueryWeb/queryRetrievalMapAction.do",
            })
        return [r for r in result if r["lat"] is not None]
    except Exception:
        return []


# ── T161: ICAO-Location-basierter Abruf ─────────────────────────────────────

def _fetch_notams_by_icao(icao_locations: list[str],
                           max_per_location: int = 15) -> list[dict]:
    """
    T161: Ruft NOTAMs via ICAO-Location-Code vom FAA NOTAM API ab.
    Deckt internationale Flugräume ab (Iran = OIIX, Israel = LLLL usw.)

    Die FAA NOTAM API verteilt internationale ICAO-NOTAMs über das globale
    ICAO NOTAM Distribution System — kein eigener Key nötig.
    """
    all_notams: list[dict] = []
    seen_ids: set = set()

    for loc in icao_locations[:6]:  # max 6 Locations abfragen
        params = {
            "icaoLocation": loc,
            "pageSize":     max_per_location,
            "pageNum":      1,
        }
        headers = {
            "Accept":        "application/json",
            "client_id":     "nexus_osint",
            "client_secret": "nexus_osint",
        }
        try:
            r = requests.get(NOTAM_API, params=params, headers=headers,
                             timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                print(f"[NOTAM] ICAO {loc}: HTTP {r.status_code}", file=sys.stderr)
                continue

            data   = r.json()
            notams = data.get("items") or []

            for n in notams:
                props  = n.get("properties") or {}
                notam_id = props.get("notamNumber", "") or str(id(n))
                if notam_id in seen_ids:
                    continue
                seen_ids.add(notam_id)

                geo    = n.get("geometry") or {}
                lat_n, lon_n = None, None
                coords = geo.get("coordinates")
                if coords and isinstance(coords, list) and len(coords) >= 2:
                    try:
                        if isinstance(coords[0], (int, float)):
                            lon_n, lat_n = coords[0], coords[1]
                        else:
                            lon_n = coords[0][0][0]
                            lat_n = coords[0][0][1]
                    except Exception:
                        pass

                if lat_n is None:
                    fallback = _icao_to_coords(loc)
                    if fallback:
                        lat_n, lon_n = fallback

                text_data  = props.get("coreNOTAMData", {})
                notam_text = (text_data.get("notam", {}).get("text") or
                              props.get("abstractText") or "")[:300]

                osint = ""
                t = notam_text.upper()
                if any(w in t for w in ["AIRSPACE RESTRICTED", "R AREA", "PROHIBITED", "P AREA"]):
                    osint = "⛔ Sperrgebiet aktiv"
                elif any(w in t for w in ["MILITARY", "MIL ", "ARMED", "EXERCISE", "EXER"]):
                    osint = "⚠ Militärische Aktivität"
                elif any(w in t for w in ["TFR", "TEMPORARY FLIGHT"]):
                    osint = "ℹ Temporäre Sperrzone"
                elif any(w in t for w in ["DRONE", "UAS", "UAV", "RPAS"]):
                    osint = "ℹ Drohnen-Einschränkung"
                elif any(w in t for w in ["CLOSED", "CLSD", "NOT AVBL", "UNAVBL"]):
                    osint = "⚠ Flughafen/Airspace geschlossen"

                all_notams.append({
                    "lat":    round(lat_n, 4) if lat_n else None,
                    "lon":    round(lon_n, 4) if lon_n else None,
                    "icao":   loc,
                    "text":   notam_text,
                    "series": props.get("series", ""),
                    "class":  props.get("classification", ""),
                    "osint":  osint,
                    "url":    "https://www.notams.faa.gov/",
                })

            if notams:
                print(f"[NOTAM] ICAO {loc}: {len(notams)} NOTAMs", file=sys.stderr)

        except requests.Timeout:
            print(f"[NOTAM] ICAO {loc}: Timeout", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"[NOTAM] ICAO {loc}: Fehler – {exc}", file=sys.stderr)
            continue

    return [n for n in all_notams if n.get("lat") is not None]


# ── Öffentlicher NOTAM-Feed (Backup) ────────────────────────────────────────

def _fetch_notam_public(lat: float, lon: float) -> list[dict]:
    """Stub – ehemals Fallback-Funktion. Nicht mehr aktiv."""
    return []


# ── Hauptfunktion ────────────────────────────────────────────────────────────

def get_notams(region: str, hours: int = 24) -> list[dict]:
    """
    Holt aktive NOTAMs für eine Region.

    T161: Zweikanaliger Abruf:
    1. ICAO-Location-Code-Suche (präzise, via _REGION_ICAO_LOCATIONS)
    2. Lat/Lon-Radius-Suche (FAA API, breite Abdeckung)
    Ergebnisse werden zusammengeführt und dedupliziert.
    """
    r_low = region.lower().strip()

    # ── Kanal 1: ICAO-Location-basiert (T161) ──────────────────────────────
    icao_results: list[dict] = []
    icao_locations: list[str] = []
    for key, locs in _REGION_ICAO_LOCATIONS.items():
        if key in r_low or r_low in key:
            icao_locations = locs
            break

    # Auch nexus_region für unbekannte Regionen nutzen
    if not icao_locations:
        try:
            from nexus_region import resolve_chain
            chain = resolve_chain(region)
            for ancestor in chain:
                for key, locs in _REGION_ICAO_LOCATIONS.items():
                    if key in ancestor.lower():
                        icao_locations = locs
                        print(f"[NOTAM] Region '{region}' → ICAO via '{key}'",
                              file=sys.stderr)
                        break
                if icao_locations:
                    break
        except ImportError:
            pass

    if icao_locations:
        icao_results = _fetch_notams_by_icao(icao_locations)

    # ── Kanal 2: Lat/Lon-Radius-Suche ──────────────────────────────────────
    lat, lon = 25.0, 50.0  # Default Naher Osten

    _COORDS: dict[str, tuple[float, float]] = {
        "naher osten":     (29.5,  45.0),
        "ukraine":         (49.0,  32.0),
        "iran":            (32.4,  53.7),
        "israel":          (31.8,  35.2),
        "taiwan":          (23.5, 120.9),
        "hormuz-strasse":  (26.5,  56.5),
        "hormuz":          (26.5,  56.5),
        "schwarzes meer":  (43.0,  34.0),
        "korea-halbinsel": (37.5, 127.0),
        "syrien":          (34.8,  38.9),
        "türkei":          (39.9,  32.8),
        "deutschland":     (51.2,  10.4),
        "europa":          (48.0,  15.0),
        "ukraine":         (49.0,  32.0),
        "jemen":           (15.5,  48.0),
        "irak":            (33.2,  44.4),
        "libanon":         (33.8,  35.5),
        "persischer golf": (26.0,  52.0),
        "rotes meer":      (20.0,  38.0),
        "sahel":           (15.0,   0.0),
        "sudan":           (15.0,  32.0),
        "afghanistan":     (33.9,  67.7),
    }
    coord_found = False
    for name, coords in _COORDS.items():
        if name in r_low or r_low in name:
            lat, lon = coords
            coord_found = True
            break

    if not coord_found:
        # nexus_region Fallback für beliebige Orte
        try:
            from nexus_region import get_bbox_center
            lat_c, lon_c = get_bbox_center(region)
            if lat_c != 0.0 or lon_c != 0.0:
                lat, lon = lat_c, lon_c
                coord_found = True
        except ImportError:
            pass

    if not coord_found:
        # Open-Meteo Geocoding als letzter Fallback
        try:
            resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": region, "count": 1, "language": "de", "format": "json"},
                timeout=8,
            )
            res = (resp.json().get("results") or [None])[0]
            if res:
                lat, lon = res["latitude"], res["longitude"]
        except Exception:
            pass

    radius = 400 if not icao_results else 200  # kleinerer Radius wenn ICAO schon Daten hat
    geo_results = _fetch_faa_notams(lat, lon, radius_nm=radius)

    # ── Zusammenführen + Deduplizieren ─────────────────────────────────────
    all_results = icao_results + geo_results
    seen: set = set()
    deduped: list[dict] = []
    for n in all_results:
        key = f"{n.get('icao','')}_{(n.get('text',''))[:30]}"
        if key not in seen:
            seen.add(key)
            deduped.append(n)

    print(f"[NOTAM] {region}: {len(icao_results)} ICAO + {len(geo_results)} Geo "
          f"= {len(deduped)} gesamt", file=sys.stderr)
    return deduped


def notams_for_map(region: str) -> list[dict]:
    """Gibt NOTAM-Marker für die Live-Karte zurück."""
    notams = get_notams(region)
    return [
        {
            "lat":    n["lat"],
            "lon":    n["lon"],
            "icao":   n["icao"],
            "text":   n["text"][:120],
            "osint":  n["osint"],
        }
        for n in notams
        if n.get("lat") and n.get("lon")
    ]


def notam_summary(region: str) -> str:
    """Text-Zusammenfassung für LLM."""
    notams = get_notams(region)
    if not notams:
        return f"[NOTAM] Keine aktiven Luftsperren für {region} gefunden."
    military = [n for n in notams if "Militär" in n.get("osint", "")]
    restricted = [n for n in notams if "Sperrgebiet" in n.get("osint", "")]
    lines = [
        f"[NOTAM – {region}]",
        f"Aktive NOTAMs: {len(notams)} | Militär: {len(military)} | Sperrgebiete: {len(restricted)}",
    ]
    for n in notams[:5]:
        if n.get("osint"):
            lines.append(f"  {n['osint']} | {n['icao']} | {n['text'][:80]}")
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Deutschland"
    print(notam_summary(region))
    notams = get_notams(region)
    print(f"\n{len(notams)} NOTAMs gefunden")
    for n in notams[:3]:
        print(f"  [{n['icao']}] {n['osint']} | {n['text'][:80]}")
