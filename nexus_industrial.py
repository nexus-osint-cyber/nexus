"""
nexus_industrial.py — Globale Industrieanlagen-Datenbank
=========================================================
Fragt OpenStreetMap (Overpass API) dynamisch nach Industrieanlagen
in einer Region ab: Raffinerien, Ölfelder, Gaskomplexe, Kraftwerke.

Kein API-Key nötig. Ergebnisse werden 24h gecacht (SQLite).

Wird von nexus_firms.py genutzt um FIRMS-Feuerpunkte zu klassifizieren:
  - Feuer nahe bekannter Industrieanlage → "erwartet, kein Angriff"
  - Feuer ohne Industrieanlage in der Nähe → "Anomalie, operativ relevant"

Funktioniert weltweit für jede Region die NEXUS beobachtet.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

OVERPASS_URL       = "https://overpass-api.de/api/interpreter"
REQUEST_TIMEOUT    = 25
CACHE_TTL_HOURS    = 24
CACHE_TTL_EMPTY_H  = 2     # Leere Ergebnisse kürzer cachen (verhindert Overpass-Dauerflut)
CACHE_RADIUS_KM    = 15.0  # Feuer innerhalb dieses Radius = industriell

BASIS_DIR  = os.path.dirname(os.path.abspath(__file__))
CACHE_DB   = os.path.join(BASIS_DIR, "nexus_industrial_cache.db")

# ── OSM-Tags für Industrieanlagen ─────────────────────────────────────────────
# Diese Tags kennzeichnen dauerhaft brennende / thermisch aktive Anlagen
_OSM_QUERIES = [
    # Raffinerien
    '  node["man_made"="petroleum_well"](bbox);',
    '  way["man_made"="petroleum_well"](bbox);',
    '  node["industrial"="refinery"](bbox);',
    '  way["industrial"="refinery"](bbox);',
    '  node["landuse"="industrial"]["industrial"="refinery"](bbox);',
    '  way["landuse"="industrial"]["industrial"="refinery"](bbox);',
    # Gas-Fackeln / Terminals
    '  node["man_made"="flare"](bbox);',
    '  node["man_made"="storage_tank"]["content"="oil"](bbox);',
    '  node["man_made"="storage_tank"]["content"="gas"](bbox);',
    '  node["industrial"="oil"](bbox);',
    '  node["industrial"="gas"](bbox);',
    # Kraftwerke
    '  node["power"="plant"](bbox);',
    '  way["power"="plant"](bbox);',
    # Petrochemie
    '  node["industrial"="chemical"](bbox);',
    '  way["industrial"="chemical"](bbox);',
    '  node["industrial"="petrochemical"](bbox);',
    '  way["industrial"="petrochemical"](bbox);',
    # Allgemeine Schwerindustrie
    '  node["man_made"="works"]["industrial"](bbox);',
    '  way["man_made"="works"]["industrial"](bbox);',
]

_TYPE_MAP = {
    "refinery":       "raffinerie",
    "petroleum_well": "oelfeld",
    "flare":          "gasfackel",
    "storage_tank":   "tank",
    "oil":            "oelanlage",
    "gas":            "gasanlage",
    "plant":          "kraftwerk",
    "chemical":       "chemie",
    "petrochemical":  "petrochemie",
    "works":          "industrie",
}


# ── Cache ─────────────────────────────────────────────────────────────────────
# T226: verhindert Log-Spam wenn nexus_firms.py get_industrial_sites() pro Feuerpunkt aufruft
_CACHE_LOG_ONCE: set[str] = set()

def _init_cache():
    con = sqlite3.connect(CACHE_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS industrial_cache (
            region      TEXT PRIMARY KEY,
            bbox        TEXT,
            data        TEXT,
            fetched_at  REAL
        )
    """)
    con.commit()
    con.close()


def _cache_get(region: str) -> Optional[list[dict]]:
    try:
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT data, fetched_at FROM industrial_cache WHERE region=?",
            (region.lower(),)
        ).fetchone()
        con.close()
        if not row:
            return None
        data = json.loads(row[0])
        age_h = (time.time() - row[1]) / 3600
        # Leere Ergebnisse kürzer cachen (CACHE_TTL_EMPTY_H), volle Ergebnisse länger
        ttl = CACHE_TTL_EMPTY_H if not data else CACHE_TTL_HOURS
        if age_h > ttl:
            return None
        return data  # kann [] sein — das ist OK, verhindert Endlos-Overpass-Anfragen
    except Exception:
        return None


def _cache_set(region: str, bbox: tuple, data: list[dict]):
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        con.execute(
            "INSERT OR REPLACE INTO industrial_cache VALUES (?,?,?,?)",
            (region.lower(), str(bbox), json.dumps(data), time.time())
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[Industrial] Cache-Fehler: {e}", file=sys.stderr)


# ── Overpass API ──────────────────────────────────────────────────────────────

def _bbox_to_overpass(bbox: tuple) -> str:
    """Konvertiert (W,S,E,N) → Overpass-Format (S,W,N,E)."""
    w, s, e, n = bbox
    return f"{s},{w},{n},{e}"


def _fetch_from_overpass(bbox: tuple) -> list[dict]:
    """Fragt Overpass API nach Industrieanlagen in einer BBox."""
    ov_bbox = _bbox_to_overpass(bbox)

    # Bei sehr großen BBoxen (>500km²) nur die wichtigsten Tags abfragen
    w, s, e, n = bbox
    bbox_area = (e - w) * (n - s)
    if bbox_area > 100:  # Großes Land → nur Raffinerien + Kraftwerke + Häfen
        query_parts = "\n".join(
            q.replace("bbox", ov_bbox) for q in _OSM_QUERIES
            if any(t in q for t in ["refinery", "petroleum_well", "plant", "flare"])
        )
        timeout = 25
    else:
        query_parts = "\n".join(q.replace("bbox", ov_bbox) for q in _OSM_QUERIES)
        timeout = 20

    query = f"""
[out:json][timeout:{timeout}][maxsize:10000000];
(
{query_parts}
);
out center;
"""
    try:
        r = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "nexus-osint/2.0 industrial-classifier"},
        )
        if r.status_code == 429:
            print("[Industrial] Overpass Rate-Limit — warte 10s", file=sys.stderr)
            time.sleep(10)
            r = requests.post(OVERPASS_URL, data={"data": query},
                              timeout=REQUEST_TIMEOUT)

        if not r.ok:
            print(f"[Industrial] Overpass HTTP {r.status_code}", file=sys.stderr)
            return []

        data = r.json()
        facilities = []

        for el in (data.get("elements") or []):
            # Koordinaten: node hat lat/lon direkt, way hat "center"
            if el.get("type") == "node":
                lat = el.get("lat")
                lon = el.get("lon")
            else:
                center = el.get("center", {})
                lat = center.get("lat")
                lon = center.get("lon")

            if not lat or not lon:
                continue

            tags  = el.get("tags", {})
            name  = (tags.get("name") or tags.get("name:en") or
                     tags.get("operator") or "")[:60]

            # Typ bestimmen
            fac_type = "industrie"
            for tag_key in ["industrial", "man_made", "power"]:
                val = tags.get(tag_key, "")
                if val in _TYPE_MAP:
                    fac_type = _TYPE_MAP[val]
                    break

            # Nur behalten wenn Koordinaten im gültigen Bereich
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                facilities.append({
                    "lat":      round(float(lat), 5),
                    "lon":      round(float(lon), 5),
                    "name":     name,
                    "type":     fac_type,
                    "osm_id":   el.get("id", 0),
                })

        return facilities

    except requests.Timeout:
        print("[Industrial] Overpass Timeout", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[Industrial] Fehler: {e}", file=sys.stderr)
        return []


# ── Haupt-API ─────────────────────────────────────────────────────────────────

def get_industrial_sites(region: str, bbox: tuple) -> list[dict]:
    """
    Gibt Liste von Industrieanlagen für eine Region zurück.
    Nutzt Cache (24h), dann Overpass API, dann Fallback-Datenbank.

    bbox: (W, S, E, N)
    Rückgabe: [{"lat": ..., "lon": ..., "name": ..., "type": ...}, ...]
    """
    _init_cache()

    # 1. Cache prüfen
    cached = _cache_get(region)
    if cached is not None:
        # Nur einmal pro Session pro Region loggen (nicht 300× pro FIRMS-Lauf)
        if region not in _CACHE_LOG_ONCE:
            _CACHE_LOG_ONCE.add(region)
            print(f"[Industrial] Cache: {len(cached)} Anlagen für {region}", file=sys.stderr)
        return cached

    # 2. Overpass API
    print(f"[Industrial] Lade Industrieanlagen für {region} von Overpass...", file=sys.stderr)
    sites = _fetch_from_overpass(bbox)

    if sites:
        print(f"[Industrial] {len(sites)} Anlagen gefunden, Cache gespeichert", file=sys.stderr)
        _cache_set(region, bbox, sites)
        return sites

    # 3. Fallback: eingebaute Daten für häufige Regionen
    fallback = _get_fallback(region)
    if fallback:
        print(f"[Industrial] Fallback: {len(fallback)} bekannte Anlagen für {region}", file=sys.stderr)
        _cache_set(region, bbox, fallback)
        return fallback

    # 4. Leeres Ergebnis cachen (TTL=2h) — verhindert Overpass-Dauerflut bei
    #    Regionen ohne Industriedaten (z.B. Gaza, kleines Stadtgebiet).
    print(f"[Industrial] Keine Industrieanlagen für {region} gefunden — Cache 2h", file=sys.stderr)
    _cache_set(region, bbox, [])
    return []


# ── Fallback-Datenbank (häufige Krisenregionen) ───────────────────────────────
# Wird genutzt wenn Overpass nicht erreichbar ist

_FALLBACK_DATA: dict[str, list[tuple]] = {
    # (lat, lon, name, typ)
    "iran": [
        (30.340, 48.280, "Abadan Raffinerie",              "raffinerie"),
        (30.430, 49.070, "Bandar Imam Khomeini",           "raffinerie"),
        (27.190, 56.270, "Bandar Abbas Raffinerie",        "raffinerie"),
        (32.500, 51.700, "Isfahan Raffinerie",             "raffinerie"),
        (35.500, 51.450, "Teheran Raffinerie",             "raffinerie"),
        (34.070, 49.680, "Arak Raffinerie",                "raffinerie"),
        (38.090, 46.350, "Tabriz Raffinerie",              "raffinerie"),
        (29.240, 50.320, "Kharg Island Terminal",          "terminal"),
        (26.800, 53.350, "Lavan Terminal",                 "terminal"),
        (27.500, 52.600, "South Pars / Assaluyeh",        "gasfeld"),
        (31.700, 48.700, "Masjed Soleyman Ölfeld",        "oelfeld"),
        (31.300, 49.200, "Ahvaz Ölfeld",                  "oelfeld"),
        (30.800, 49.500, "Gachsaran Ölfeld",              "oelfeld"),
        (33.720, 51.730, "Natanz Nuklearanlage",          "nuklear"),
        (34.880, 50.980, "Fordow Nuklearanlage",          "nuklear"),
        (28.920, 50.830, "Bushehr Kernkraftwerk",         "kraftwerk"),
    ],
    "ukraine": [
        # Raffinerien & Ölinfrastruktur
        (48.500, 38.000, "Lysychansk Raffinerie",          "raffinerie"),
        (49.800, 24.000, "Drohobych Raffinerie",           "raffinerie"),
        (49.400, 27.000, "Kremenchuk Raffinerie",          "raffinerie"),
        (47.100, 37.700, "Mariupol Hafen / Ölanlage",     "terminal"),
        # Kernkraftwerke & Kraftwerke
        (47.900, 35.100, "Zaporizhzhia KKW",               "kraftwerk"),
        (51.400, 30.100, "Tschernobyl Zone",               "nuklear"),
        (47.500, 35.200, "Enerhodar Wärmekraftwerk",       "kraftwerk"),
        (48.520, 32.290, "Kremenchuk Wasserkraftwerk",     "kraftwerk"),
        (47.850, 35.080, "Dniprovska HES",                 "kraftwerk"),
        (48.600, 38.500, "Luhanska Wärmekraftwerk",       "kraftwerk"),
        (50.100, 29.000, "Rivne KKW",                      "kraftwerk"),
        (48.700, 32.500, "Pivdennoukrainsk KKW",          "kraftwerk"),
        (51.500, 33.700, "Chmelnyzkyj KKW",               "kraftwerk"),
        # Stahlwerke & Schwerindustrie
        (47.100, 37.550, "Azovstal Mariupol (Stahlwerk)", "industrie"),
        (47.100, 37.650, "Mariupol Ilyich Stahlwerk",     "industrie"),
        (47.880, 35.010, "Zaporizhzhia Stahlwerk",        "industrie"),
        (47.900, 33.400, "Kryvyi Rih ArcelorMittal",      "industrie"),
        (48.500, 35.000, "Dnipro Stahlwerk",              "industrie"),
        (48.400, 34.900, "Kamianske Industriezone",       "industrie"),
        (48.000, 37.800, "Donezk Industriezone",          "industrie"),
        (48.100, 38.100, "Makijiwka Kokerei",             "industrie"),
        (48.050, 38.020, "Avdiivka Kokerei",              "industrie"),
        # Chemieindustrie
        (48.950, 38.500, "Sievierodonetsk Azot Chemie",  "chemie"),
        (49.000, 33.400, "Kremenchuk Petrochemie",        "petrochemie"),
        (46.900, 31.900, "Mykolaiv Aluminiumwerk",        "industrie"),
        (48.460, 35.040, "Dnipro Chemiewerk",             "chemie"),
        # Häfen & Terminals
        (46.480, 30.730, "Odessa Hafen",                  "terminal"),
        (46.960, 31.980, "Mykolaiv Hafen",               "terminal"),
        (46.620, 32.620, "Cherson Hafen",                "terminal"),
        (47.100, 37.550, "Mariupol Hafen",               "terminal"),
        # Eisenerz & Bergbau
        (47.900, 33.350, "Kryvyi Rih Eisenerzmine",      "oelfeld"),
        (48.150, 38.150, "Donbas Kohlebergbau",          "oelfeld"),
    ],
    "russland": [
        (59.950, 30.320, "Kirishskaya Raffinerie",         "raffinerie"),
        (53.200, 50.150, "Samarskaya Raffinerie",          "raffinerie"),
        (54.700, 55.900, "Ufimskaya Raffinerie",           "raffinerie"),
        (56.800, 60.600, "Sverdlovsk Industriezone",       "industrie"),
        (68.970, 33.080, "Murmansk Industriezone",        "industrie"),
        (61.000, 69.000, "Surgut Ölfeld",                 "oelfeld"),
        (61.700, 50.800, "Ukhta Raffinerie",               "raffinerie"),
    ],
    "israel": [
        (31.800, 34.640, "Ashdod Raffinerie",              "raffinerie"),
        (32.820, 35.020, "Haifa Raffinerie",               "raffinerie"),
        (31.000, 35.000, "Negev Dimona (nuklear)",        "nuklear"),
    ],
    "irak": [
        (30.500, 47.800, "Basra Raffinerie",               "raffinerie"),
        (35.320, 43.130, "Baiji Raffinerie",               "raffinerie"),
        (36.200, 44.000, "Kirkuk Ölfeld",                  "oelfeld"),
        (30.900, 46.200, "Majnoon Ölfeld",                "oelfeld"),
    ],
    "saudi-arabien": [
        (26.300, 50.100, "Ras Tanura Raffinerie",          "raffinerie"),
        (26.400, 49.900, "Abqaiq Ölanlage",               "oelfeld"),
        (26.700, 49.600, "Dhahran Ölfeld",                "oelfeld"),
        (21.400, 39.800, "Jeddah Raffinerie",              "raffinerie"),
    ],
    "jemen": [
        (13.000, 45.000, "Aden Raffinerie",                "raffinerie"),
        (15.300, 44.200, "Marib Gasfeld",                  "gasfeld"),
        (14.800, 42.950, "Hodeidah Hafen",                 "terminal"),
    ],
    "yemen": [
        (13.000, 45.000, "Aden Raffinerie",                "raffinerie"),
        (15.300, 44.200, "Marib Gasfeld",                  "gasfeld"),
        (14.800, 42.950, "Hodeidah Hafen",                 "terminal"),
    ],
    "libanon": [
        (33.890, 35.490, "Zouk Kraftwerk",                 "kraftwerk"),
        (33.550, 35.370, "Jiyeh Kraftwerk",                "kraftwerk"),
        (33.880, 35.540, "Dora Raffinerie Beirut",         "raffinerie"),
        (33.570, 35.210, "Sidon Raffinerie",               "raffinerie"),
        (34.340, 35.650, "Tripoli Ölanlage",               "terminal"),
    ],
    "lebanon": [
        (33.890, 35.490, "Zouk Kraftwerk",                 "kraftwerk"),
        (33.550, 35.370, "Jiyeh Kraftwerk",                "kraftwerk"),
        (33.880, 35.540, "Dora Raffinerie Beirut",         "raffinerie"),
        (33.570, 35.210, "Sidon Raffinerie",               "raffinerie"),
        (34.340, 35.650, "Tripoli Ölanlage",               "terminal"),
    ],
    "gaza": [
        (31.520, 34.450, "Gaza Kraftwerk",                 "kraftwerk"),
        (31.550, 34.470, "Gaza Hafen (nördlich)",          "terminal"),
    ],
    "taiwan": [
        (22.700, 120.400, "Linyuan Raffinerie",            "raffinerie"),
        (25.050, 121.500, "Taoyuan Kraftwerk",             "kraftwerk"),
    ],
    "naher osten": [
        (26.300, 50.100, "Ras Tanura",                     "raffinerie"),
        (27.500, 52.600, "South Pars",                     "gasfeld"),
        (30.340, 48.280, "Abadan",                         "raffinerie"),
    ],
}


def _get_fallback(region: str) -> list[dict]:
    """Gibt Fallback-Daten für eine Region zurück."""
    r = region.lower().strip()
    for key, entries in _FALLBACK_DATA.items():
        if key in r or r in key:
            return [
                {"lat": e[0], "lon": e[1], "name": e[2], "type": e[3]}
                for e in entries
            ]
    return []


# ── Distanz-Klassifikation ────────────────────────────────────────────────────

def classify_fire_global(lat: float, lon: float, region: str,
                          bbox: tuple, frp: float = 0) -> dict:
    """
    Klassifiziert einen Feuerpunkt global — nutzt Overpass API + Fallback.
    Ersetzt classify_fire() in nexus_firms.py für globale Anwendung.
    """
    import math

    sites = get_industrial_sites(region, bbox)
    if not sites:
        return {
            "type":     "unknown",
            "facility": "",
            "fac_type": "",
            "dist_km":  9999,
            "note":     "Keine Industriedaten verfügbar — manuelle Prüfung empfohlen",
        }

    nearest_name = ""
    nearest_type = ""
    nearest_dist = 9999.0

    for s in sites:
        try:
            dlat = math.radians(s["lat"] - lat)
            dlon = math.radians(s["lon"] - lon)
            a = (math.sin(dlat/2)**2 +
                 math.cos(math.radians(lat)) * math.cos(math.radians(s["lat"])) *
                 math.sin(dlon/2)**2)
            dist = 6371.0 * 2 * math.asin(math.sqrt(a))
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_name = s.get("name", "Unbekannte Anlage")
                nearest_type = s.get("type", "industrie")
        except Exception:
            continue

    nearest_dist = round(nearest_dist, 1)

    if nearest_dist <= CACHE_RADIUS_KM:
        return {
            "type":     "industrial",
            "facility": nearest_name,
            "fac_type": nearest_type,
            "dist_km":  nearest_dist,
            "note":     f"Bekannte Industrieanlage: {nearest_name} ({nearest_type}, {nearest_dist}km) — Dauerfeuer erwartet, KEIN Angriffsindikator",
        }
    elif nearest_dist <= 30.0:
        return {
            "type":     "unknown",
            "facility": nearest_name,
            "fac_type": nearest_type,
            "dist_km":  nearest_dist,
            "note":     f"Nähe zu {nearest_name} ({nearest_dist}km) — möglicherweise Ausbreitung",
        }
    else:
        return {
            "type":     "anomaly",
            "facility": "",
            "fac_type": "",
            "dist_km":  nearest_dist,
            "note":     f"Kein bekanntes Industrieziel in {nearest_dist:.0f}km — ANOMALIE, operativ relevant",
        }


# ── Cache-Verwaltung ──────────────────────────────────────────────────────────

def cache_stats() -> dict:
    """Zeigt gecachte Regionen an."""
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        rows = con.execute(
            "SELECT region, fetched_at, length(data) FROM industrial_cache ORDER BY fetched_at DESC"
        ).fetchall()
        con.close()
        return {
            "cached_regions": [
                {
                    "region": r[0],
                    "age_h":  round((time.time() - r[1]) / 3600, 1),
                    "size_kb": round(r[2] / 1024, 1),
                }
                for r in rows
            ]
        }
    except Exception:
        return {}


def clear_cache(region: str = ""):
    """Löscht Cache (alle oder eine Region)."""
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        if region:
            con.execute("DELETE FROM industrial_cache WHERE region=?", (region.lower(),))
        else:
            con.execute("DELETE FROM industrial_cache")
        con.commit()
        con.close()
        print(f"[Industrial] Cache gelöscht{' für ' + region if region else ' (alle)'}")
    except Exception as e:
        print(f"[Industrial] Cache-Fehler: {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS Industrieanlagen-Abfrage")
    parser.add_argument("--region", type=str, default="Iran",  help="Region (z.B. Iran, Ukraine)")
    parser.add_argument("--stats",  action="store_true",       help="Cache-Status anzeigen")
    parser.add_argument("--clear",  action="store_true",       help="Cache leeren")
    args = parser.parse_args()

    if args.stats:
        st = cache_stats()
        print("\nGecachte Regionen:")
        for r in st.get("cached_regions", []):
            print(f"  {r['region']:20s} — vor {r['age_h']}h — {r['size_kb']}KB")
        sys.exit(0)

    if args.clear:
        clear_cache()
        sys.exit(0)

    # Industrieanlagen für Region abfragen
    region = args.region
    print(f"\n[NEXUS Industrial] Lade Anlagen für '{region}'...")

    # BBox aus nexus_region holen
    bbox = None
    try:
        from nexus_region import get_bbox_with_fallback  # type: ignore
        bbox, resolved = get_bbox_with_fallback(region)
        if bbox:
            print(f"  Region aufgelöst: {resolved} | BBox: {bbox}")
    except ImportError:
        pass

    if not bbox:
        # Einfache Fallback-BBox
        from nexus_firms import _REGION_BOXES  # type: ignore
        for name, box in _REGION_BOXES.items():
            if name.lower() in region.lower():
                bbox = box
                break

    if not bbox:
        print("  ⚠ Keine BBox gefunden — nutze Fallback-Daten")
        bbox = (44.0, 25.0, 63.5, 40.0)  # Iran als Default

    sites = get_industrial_sites(region, bbox)
    print(f"\n  {len(sites)} Industrieanlagen gefunden:\n")

    by_type: dict = {}
    for s in sites:
        t = s.get("type", "?")
        by_type.setdefault(t, []).append(s)

    for typ, anlagen in sorted(by_type.items()):
        print(f"  {typ.upper()} ({len(anlagen)}):")
        for a in anlagen[:5]:
            print(f"    {a['lat']:.3f}°N {a['lon']:.3f}°E  {a['name'] or '(unbenannt)'}")
        if len(anlagen) > 5:
            print(f"    ... +{len(anlagen)-5} weitere")
