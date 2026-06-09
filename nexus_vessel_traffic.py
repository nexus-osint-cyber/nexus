"""
nexus_vessel_traffic.py — Hafenverkehr & Blockaden-Detektion
=============================================================
Misst Schiffsdichte nahe strategischer Häfen via öffentliche Quellen.
Erkennt Blockaden durch Verkehrseinbruch statt durch AIS-Dark-Vessels.

Logik: Eine Blockade bedeutet WENIGER Schiffe, nicht mehr dunkle.
  Normal:    Bandar Abbas → 40-60 Schiffe/Tag sichtbar
  Blockade:  Bandar Abbas → 3-8 Schiffe/Tag sichtbar → Einbruch -85%

Quellen (alle kostenlos):
  1. VesselFinder public map (HTML scraping)
  2. MarineTraffic public API (limitiert, kein Key nötig)
  3. PortWatch (IMF) — Hafenaktivitäts-Index
  4. UN COMTRADE (Handelsdaten als indirekter Indikator)

Bekannte strategische Häfen mit GPS + Normwert:
  Bandar Abbas (Iran), Kharg Island, Assaluyeh,
  Odessa (Ukraine), Mykolaiv, Cherson,
  Aden (Jemen), Hudaydah,
  Latakia (Syrien), ...

Aufruf:
  python nexus_vessel_traffic.py --hafen "Bandar Abbas"
  python nexus_vessel_traffic.py --region Iran
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

REQUEST_TIMEOUT = 15
BASIS_DIR  = os.path.dirname(os.path.abspath(__file__))
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ── Fallback: Bekannte strategische Häfen (wenn Overpass nicht verfügbar) ─────
# Format: name → (lat, lon, normaler_tagesverkehr, land)
STRATEGIC_PORTS: dict[str, tuple[float, float, int, str]] = {
    # Iran
    "Bandar Abbas":           (27.18, 56.27,  55, "Iran"),
    "Bandar Imam Khomeini":   (30.43, 49.07,  25, "Iran"),
    "Kharg Island":           (29.24, 50.32,  30, "Iran"),
    "Assaluyeh":              (27.50, 52.60,  20, "Iran"),
    "Chabahar":               (25.29, 60.64,  15, "Iran"),
    # Straße von Hormuz / Persischer Golf
    "Hormuz Strait":          (26.57, 56.50,  80, "Hormuz"),
    "Jebel Ali (Dubai)":      (25.01, 55.06, 120, "VAE"),
    "Dubai Port Rashid":      (25.27, 55.29,  60, "VAE"),
    "Abu Dhabi Zayed Port":   (24.49, 54.37,  40, "VAE"),
    "Fujairah":               (25.11, 56.34,  45, "VAE"),
    "Kuwait City Port":       (29.37, 47.97,  30, "Kuwait"),
    "Basra / Umm Qasr":       (30.03, 47.97,  35, "Irak"),
    "Muscat Port Sultan":     (23.63, 58.59,  40, "Oman"),
    "Salalah":                (17.00, 54.07,  50, "Oman"),
    "Dammam (Saudi)":         (26.43, 50.10,  45, "Saudi-Arabien"),
    "Jubail":                 (27.01, 49.66,  35, "Saudi-Arabien"),
    "Ras Tanura":             (26.65, 50.16,  50, "Saudi-Arabien"),
    # Ukraine / Schwarzes Meer
    "Odessa":                 (46.48, 30.73,  40, "Ukraine"),
    "Mykolaiv":               (46.96, 31.98,  25, "Ukraine"),
    "Cherson":                (46.62, 32.62,  20, "Ukraine"),
    "Mariupol":               (47.10, 37.55,  30, "Ukraine"),
    "Novorossiysk":           (44.72, 37.79,  60, "Russland"),
    "Sevastopol":             (44.62, 33.53,  25, "Russland/Ukraine"),
    "Constanta":              (44.17, 28.65,  45, "Rumänien"),
    "Istanbul Bosphorus":     (41.00, 29.00,  200, "Türkei"),
    # Rotes Meer / Jemen / Suez
    "Aden":                   (12.78, 44.99,  35, "Jemen"),
    "Hudaydah":               (14.80, 42.95,  20, "Jemen"),
    "Jeddah":                 (21.49, 39.18,  80, "Saudi-Arabien"),
    "Suez / Port Said":       (30.60, 32.35, 150, "Ägypten"),
    # Naher Osten / Mittelmeer
    "Latakia":                (35.52, 35.78,  25, "Syrien"),
    "Beirut":                 (33.89, 35.52,  30, "Libanon"),
    "Haifa":                  (32.82, 35.00,  40, "Israel"),
    "Ashdod":                 (31.80, 34.65,  35, "Israel"),
    # Asien
    "Kaohsiung":              (22.62, 120.27, 80, "Taiwan"),
    "Keelung":                (25.14, 121.74, 50, "Taiwan"),
    "Singapore":              (1.26,  103.82, 300, "Singapur"),
    "Shanghai":               (31.23, 121.47, 250, "China"),
    "Hong Kong":              (22.29, 114.16, 150, "China/HK"),
    "Busan":                  (35.10, 129.04, 100, "Südkorea"),
    "Incheon":                (37.45, 126.60,  60, "Südkorea"),
    # Europa
    "Rotterdam":              (51.90,   4.47, 200, "Niederlande"),
    "Hamburg":                (53.55,   9.97, 100, "Deutschland"),
    "Antwerpen":              (51.27,   4.40, 150, "Belgien"),
}

# ── PortWatch (IMF) API ───────────────────────────────────────────────────────
# IMF PortWatch misst Schiffsdurchfahrten an strategischen Chokepoints
# Kostenlos, kein Key: https://portwatch.imf.org/

PORTWATCH_API = "https://portwatch.imf.org/server/rest/services/Hosted/portwatch_daily/FeatureServer/0/query"

_PORTWATCH_PORTS: dict[str, int] = {
    # PortWatch Port-IDs (aus der IMF-Datenbank)
    "Hormuz":   1,
    "Suez":     2,
    "Bab-el-Mandeb": 3,
    "Malacca":  4,
    "Panama":   5,
    "Cape of Good Hope": 6,
    "Bosphorus": 7,
    "Gibraltar": 8,
}


def _fetch_portwatch(chokepoint: str = "Hormuz", days: int = 7) -> Optional[dict]:
    """
    Holt Schiffsdurchfahrten von IMF PortWatch für einen Chokepoint.
    Gibt dict mit täglichen Durchfahrten zurück.
    """
    port_id = _PORTWATCH_PORTS.get(chokepoint)
    if not port_id:
        return None

    try:
        params = {
            "where":       f"port_id={port_id}",
            "outFields":   "date,vessels_count,vessels_dwt,port_name",
            "orderByFields": "date DESC",
            "resultRecordCount": days,
            "f":           "json",
        }
        r = requests.get(PORTWATCH_API, params=params, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            return None
        data = r.json()
        features = data.get("features", [])
        if not features:
            return None

        records = []
        for f in features:
            attrs = f.get("attributes", {})
            records.append({
                "date":    attrs.get("date", ""),
                "vessels": attrs.get("vessels_count", 0),
                "dwt":     attrs.get("vessels_dwt", 0),
            })

        avg_vessels = sum(r["vessels"] for r in records) / len(records) if records else 0
        latest = records[0]["vessels"] if records else 0

        return {
            "chokepoint":   chokepoint,
            "latest":       latest,
            "avg_7d":       round(avg_vessels, 1),
            "change_pct":   round((latest - avg_vessels) / avg_vessels * 100, 1) if avg_vessels else 0,
            "records":      records,
        }

    except Exception as e:
        print(f"[VesselTraffic] PortWatch Fehler: {e}", file=sys.stderr)
        return None


# ── VesselFinder öffentliche Dichte ──────────────────────────────────────────

def _fetch_vesselfinder_density(lat: float, lon: float,
                                 radius_nm: float = 30) -> Optional[dict]:
    """
    Versucht Schiffsdichte via VesselFinder public API zu holen.
    Gibt Anzahl sichtbarer Schiffe in einem Radius zurück.
    """
    try:
        # VesselFinder public vessel list (kein Key für Basis-Abfrage)
        url = "https://www.vesselfinder.com/api/pub/vesselsonmap"
        params = {
            "bbox": f"{lon-0.5},{lat-0.5},{lon+0.5},{lat+0.5}",
            "zoom": 10,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; nexus-osint/2.0)",
            "Accept":     "application/json",
            "Referer":    "https://www.vesselfinder.com/",
        }
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            # VesselFinder gibt eine Liste von Schiffen zurück
            vessels = data if isinstance(data, list) else data.get("vessels", [])
            return {
                "source":  "VesselFinder",
                "count":   len(vessels),
                "vessels": vessels[:5],
            }
    except Exception:
        pass

    # Fallback: MarineTraffic public JSON
    try:
        url = f"https://www.marinetraffic.com/getData/get_data_json_3/z:10/X:{lon}/Y:{lat}/station:0"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.marinetraffic.com/",
        }, timeout=REQUEST_TIMEOUT)
        if r.ok:
            data = r.json()
            vessels = data.get("data", {}).get("rows", [])
            return {
                "source":  "MarineTraffic",
                "count":   len(vessels),
                "vessels": vessels[:5],
            }
    except Exception:
        pass

    return None


# ── Blockaden-Analyse ─────────────────────────────────────────────────────────

def analyse_port_blockade(port_name: str) -> dict:
    """
    Analysiert ob ein Hafen blockiert sein könnte.
    Vergleicht aktuellen Verkehr mit historischem Normwert.
    """
    port = STRATEGIC_PORTS.get(port_name)
    if not port:
        # Suche nach Teilstring
        for name, data in STRATEGIC_PORTS.items():
            if port_name.lower() in name.lower():
                port = data
                port_name = name
                break

    if not port:
        return {"error": f"Hafen '{port_name}' nicht in Datenbank"}

    lat, lon, normal_traffic, land = port
    ergebnis = {
        "hafen":          port_name,
        "land":           land,
        "lat":            lat,
        "lon":            lon,
        "normal_traffic": normal_traffic,
        "quellen":        [],
        "verdict":        "UNBEKANNT",
        "confidence":     0.0,
        "details":        [],
    }

    # 1. VesselFinder / MarineTraffic Dichte
    density = _fetch_vesselfinder_density(lat, lon)
    if density:
        count   = density["count"]
        pct     = round(count / normal_traffic * 100) if normal_traffic else 0
        einbruch = normal_traffic - count
        ergebnis["quellen"].append({
            "name":    density["source"],
            "count":   count,
            "normal":  normal_traffic,
            "pct":     pct,
        })
        if pct < 20:
            ergebnis["details"].append(
                f"⚠️ {density['source']}: Nur {count} Schiffe sichtbar "
                f"(normal: ~{normal_traffic}) — {pct}% des Normwerts → BLOCKADE WAHRSCHEINLICH"
            )
        elif pct < 50:
            ergebnis["details"].append(
                f"🟡 {density['source']}: {count} Schiffe ({pct}% des Normwerts) — deutlicher Einbruch"
            )
        else:
            ergebnis["details"].append(
                f"✅ {density['source']}: {count} Schiffe ({pct}% des Normwerts) — normaler Betrieb"
            )

    # 2. PortWatch für Hormuz / Bab-el-Mandeb
    chokepoint = None
    if "hormuz" in port_name.lower() or land == "Iran":
        chokepoint = "Hormuz"
    elif land == "Jemen":
        chokepoint = "Bab-el-Mandeb"

    if chokepoint:
        pw = _fetch_portwatch(chokepoint)
        if pw:
            change = pw["change_pct"]
            ergebnis["quellen"].append({
                "name":   f"IMF PortWatch ({chokepoint})",
                "latest": pw["latest"],
                "avg":    pw["avg_7d"],
                "change": change,
            })
            if change < -30:
                ergebnis["details"].append(
                    f"⚠️ IMF PortWatch: {chokepoint} {change:+.0f}% vs. 7d-Schnitt → signifikanter Rückgang"
                )
            elif change < -15:
                ergebnis["details"].append(
                    f"🟡 IMF PortWatch: {chokepoint} {change:+.0f}% — moderater Rückgang"
                )
            else:
                ergebnis["details"].append(
                    f"✅ IMF PortWatch: {chokepoint} {change:+.0f}% — normaler Bereich"
                )

    # 3. Verdict berechnen
    blockade_signals = sum(1 for d in ergebnis["details"] if "⚠️" in d)
    warning_signals  = sum(1 for d in ergebnis["details"] if "🟡" in d)
    normal_signals   = sum(1 for d in ergebnis["details"] if "✅" in d)

    if not ergebnis["details"]:
        ergebnis["verdict"]    = "KEINE_DATEN"
        ergebnis["confidence"] = 0.0
        ergebnis["details"].append("❓ Keine Verkehrsdaten verfügbar — manuelle Prüfung nötig")
    elif blockade_signals >= 2:
        ergebnis["verdict"]    = "BLOCKADE_WAHRSCHEINLICH"
        ergebnis["confidence"] = 0.85
    elif blockade_signals >= 1:
        ergebnis["verdict"]    = "VERKEHRSEINBRUCH"
        ergebnis["confidence"] = 0.55
    elif warning_signals >= 1:
        ergebnis["verdict"]    = "LEICHTER_RUECKGANG"
        ergebnis["confidence"] = 0.30
    else:
        ergebnis["verdict"]    = "NORMALBETRIEB"
        ergebnis["confidence"] = 0.80

    ergebnis["summary"] = (
        f"{ergebnis['verdict']} ({ergebnis['confidence']*100:.0f}% Konfidenz) | "
        f"{port_name} ({land}) | "
        f"{'; '.join(d[2:] for d in ergebnis['details'][:2])}"
    )
    return ergebnis


def _fetch_ports_overpass(bbox: tuple) -> list[dict]:
    """
    Holt Häfen dynamisch von OpenStreetMap für eine BBox.
    Gibt Liste von {name, lat, lon, type} zurück.
    """
    w, s, e, n = bbox
    ov_bbox = f"{s},{w},{n},{e}"
    query = f"""
[out:json][timeout:15];
(
  node["harbour"="yes"]({ov_bbox});
  node["landuse"="harbour"]({ov_bbox});
  node["port"="yes"]({ov_bbox});
  way["harbour"="yes"]({ov_bbox});
  way["landuse"="harbour"]({ov_bbox});
  node["seamark:type"="harbour"]({ov_bbox});
);
out center;
"""
    try:
        r = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": "nexus-osint/2.0"},
            timeout=15,
        )
        if not r.ok:
            return []
        features = []
        for el in (r.json().get("elements") or []):
            if el.get("type") == "node":
                lat, lon = el.get("lat"), el.get("lon")
            else:
                c = el.get("center", {})
                lat, lon = c.get("lat"), c.get("lon")
            if not lat or not lon:
                continue
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:en") or tags.get("operator") or ""
            if name:
                features.append({"name": name[:50], "lat": round(float(lat), 4),
                                  "lon": round(float(lon), 4), "type": "hafen"})
        return features
    except Exception:
        return []


def get_ports_for_region(region: str) -> list[dict]:
    """
    Gibt alle Häfen einer Region zurück.
    1. Overpass API (dynamisch, alle OSM-Häfen)
    2. Fallback: STRATEGIC_PORTS Tabelle
    """
    # BBox für Region holen
    bbox = None
    try:
        from nexus_region import get_bbox_with_fallback  # type: ignore
        bbox, _ = get_bbox_with_fallback(region)
    except ImportError:
        pass

    if not bbox:
        # Einfache Länder-Lookup
        from nexus_firms import _REGION_BOXES  # type: ignore
        for name, box in _REGION_BOXES.items():
            if name.lower() in region.lower():
                bbox = box
                break

    # 1. Overpass
    if bbox:
        osm_ports = _fetch_ports_overpass(bbox)
        if osm_ports:
            print(f"[VesselTraffic] Overpass: {len(osm_ports)} Häfen für {region}", file=sys.stderr)
            return osm_ports

    # 2. Fallback: statische Liste
    fallback = []
    for port_name, (lat, lon, traffic, land) in STRATEGIC_PORTS.items():
        if land.lower() in region.lower() or region.lower() in land.lower():
            fallback.append({"name": port_name, "lat": lat, "lon": lon,
                             "type": "hafen", "traffic": traffic})
    if fallback:
        print(f"[VesselTraffic] Fallback: {len(fallback)} Häfen für {region}", file=sys.stderr)
    return fallback


def analyse_region_ports(region: str) -> list[dict]:
    """Analysiert alle Häfen einer Region — nutzt dynamische Overpass-Abfrage."""
    ports = get_ports_for_region(region)

    # Für Analyse: Häfen aus STRATEGIC_PORTS bevorzugen (haben Normwerte)
    ergebnisse = []
    analysiert = set()

    # Erst bekannte strategische Häfen
    for port_name, (lat, lon, traffic, land) in STRATEGIC_PORTS.items():
        if land.lower() in region.lower() or region.lower() in land.lower():
            if port_name not in analysiert:
                ergebnis = analyse_port_blockade(port_name)
                ergebnisse.append(ergebnis)
                analysiert.add(port_name)
                time.sleep(1.0)

    # Dann Overpass-Häfen die noch nicht dabei sind
    for p in ports[:5]:
        pname = p.get("name", "")
        if pname and pname not in analysiert:
            ergebnis = {
                "hafen": pname,
                "land": region,
                "lat": p["lat"], "lon": p["lon"],
                "normal_traffic": 20,  # Schätzwert
                "verdict": "KEINE_DATEN",
                "confidence": 0.0,
                "details": ["❓ Dynamisch gefundener Hafen — kein Normwert verfügbar"],
                "quellen": [],
                "summary": f"KEINE_DATEN | {pname} (via OSM)",
            }
            ergebnisse.append(ergebnis)

    return ergebnisse


def port_traffic_summary(region: str) -> str:
    """Text-Zusammenfassung für LLM und Verify-Modul."""
    results = analyse_region_ports(region)
    if not results:
        return f"[VESSEL TRAFFIC] Keine bekannten strategischen Häfen für {region}."

    lines = [f"[HAFENVERKEHR-ANALYSE — {region}]"]
    for r in results:
        icon = "⚠️" if "BLOCKADE" in r["verdict"] else ("🟡" if "EINBRUCH" in r["verdict"] else "✅")
        lines.append(f"  {icon} {r['hafen']}: {r['verdict']} ({r['confidence']*100:.0f}%)")
        for d in r["details"][:1]:
            lines.append(f"    {d}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS Hafenverkehr-Analyse")
    parser.add_argument("--hafen",  type=str, default="", help="Spezifischer Hafen (z.B. 'Bandar Abbas')")
    parser.add_argument("--region", type=str, default="", help="Alle Häfen einer Region (z.B. Iran)")
    parser.add_argument("--liste",  action="store_true",  help="Alle bekannten Häfen auflisten")
    args = parser.parse_args()

    if args.liste:
        print("\nBekannte strategische Häfen:\n")
        by_land: dict = {}
        for name, (lat, lon, traffic, land) in STRATEGIC_PORTS.items():
            by_land.setdefault(land, []).append(f"  {name:25s} ({lat:.2f}°N {lon:.2f}°E, ~{traffic} Schiffe/Tag)")
        for land, ports in sorted(by_land.items()):
            print(f"{land}:")
            for p in ports:
                print(p)
        sys.exit(0)

    if args.hafen:
        print(f"\n[NEXUS Vessel Traffic] Analysiere Hafen: {args.hafen}\n")
        r = analyse_port_blockade(args.hafen)
        print(f"Hafen:     {r['hafen']} ({r['land']})")
        print(f"Verdict:   {r['verdict']} ({r['confidence']*100:.0f}% Konfidenz)")
        print(f"\nDetails:")
        for d in r["details"]:
            print(f"  {d}")

    elif args.region:
        print(f"\n[NEXUS Vessel Traffic] Analysiere Region: {args.region}\n")
        results = analyse_region_ports(args.region)
        for r in results:
            print(f"  {r['hafen']}: {r['verdict']} ({r['confidence']*100:.0f}%)")
            for d in r["details"]:
                print(f"    {d}")

    else:
        print("Nutzung: --hafen 'Bandar Abbas' oder --region Iran oder --liste")
