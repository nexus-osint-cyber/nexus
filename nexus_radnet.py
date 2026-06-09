"""
NEXUS – Strahlungs-Monitoring  (Ebene 4 / Modul 4.14)
======================================================
Überwacht Strahlungsdaten aus mehreren öffentlichen Quellen:

  1. EPA RadNet (USA, kostenlos JSON-API)
     → Gamma-Strahlung an 140+ Messstationen
  2. IAEA RSS-Feed (International Atomic Energy Agency)
     → Offizielle nukleare Vorfallsmeldungen
  3. Safecast (crowd-sourced, Japan/Global)
     → Bürgerwissenschaftliche Messwerte
  4. EURDEP (European Radiological Data Exchange Platform)
     → Europäische Strahlungsmessnetz

Alarm-Logik:
  • Messwert > 3× Baseline dieser Station → ERHÖHT
  • Messwert > 10× Baseline → KRITISCH
  • IAEA-Vorfallsmeldung → SOFORT

Öffentliche API:
  get_radiation_data(region)       → list[RadPoint]
  radiation_for_map(region)        → list[dict]
  radiation_summary(points)        → str
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Datenklasse
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RadPoint:
    lat:         float
    lon:         float
    value_cpm:   float              # Messwert in CPM (Counts per Minute)
    baseline_cpm:float = 0.0       # Normalwert der Station
    unit:        str   = "CPM"
    station_id:  str   = ""
    station_name:str   = ""
    alert_level: str   = "NORMAL"  # NORMAL | ERHÖHT | KRITISCH
    anomaly_mult:float = 1.0       # Faktor über Baseline
    source:      str   = "radnet"
    description: str   = ""
    ts:          float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# Globale Baseline-Referenz (typische Hintergrundstrahlung, CPM)
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_BASELINE_CPM = 30.0  # Typischer Hintergrund USA/Europa

# Station-Baselines aus vorherigen Messungen (wird live befüllt)
_station_baseline: dict[str, float] = {}


def _anomaly_level(mult: float) -> str:
    if mult >= 10:
        return "KRITISCH"
    if mult >= 3:
        return "ERHÖHT"
    if mult >= 1.5:
        return "LEICHT_ERHÖHT"
    return "NORMAL"


# ─────────────────────────────────────────────────────────────────────────────
# 1. EPA RadNet
# ─────────────────────────────────────────────────────────────────────────────

# Grobe Region → EPA-Staaten-Filter
_REGION_STATES = {
    "usa":          None,  # Alle Stationen
    "north america":None,
    "default":      None,
}

def _fetch_epa_radnet() -> list[RadPoint]:
    """
    EPA RadNet JSON API (öffentlich, kein Key).
    https://www.epa.gov/radnet/near-real-time-and-laboratory-data-radnet
    """
    try:
        url = "https://www.epa.gov/sites/default/files/2020-01/radnet-data.json"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())

        points = []
        for station in (data.get("stations") or data.get("data") or [])[:50]:
            lat = float(station.get("latitude", station.get("lat", 0) or 0))
            lon = float(station.get("longitude", station.get("lon", 0) or 0))
            if not lat or not lon:
                continue

            sid    = str(station.get("siteID", station.get("id", "")))
            name   = str(station.get("siteName", station.get("name", sid)))
            # Aktueller Wert
            val    = float(station.get("gammaCPM", station.get("cpm", 0) or 0)
                           or station.get("currentValue", 0) or 0)
            if val <= 0:
                continue

            # Baseline
            baseline = _station_baseline.get(sid, _DEFAULT_BASELINE_CPM)
            if baseline <= 0:
                baseline = _DEFAULT_BASELINE_CPM
            # Baseline aktuell halten (gleitender Durchschnitt)
            _station_baseline[sid] = baseline * 0.95 + val * 0.05

            mult  = val / baseline
            level = _anomaly_level(mult)

            points.append(RadPoint(
                lat          = round(lat, 4),
                lon          = round(lon, 4),
                value_cpm    = round(val, 1),
                baseline_cpm = round(baseline, 1),
                station_id   = sid,
                station_name = name[:40],
                alert_level  = level,
                anomaly_mult = round(mult, 2),
                source       = "epa_radnet",
                description  = f"{name}: {val:.0f} CPM ({mult:.1f}× Baseline)",
            ))

        # Nur auffällige Werte zurückgeben
        return [p for p in points if p.alert_level != "NORMAL"]

    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 2. IAEA News / Incidents RSS
# ─────────────────────────────────────────────────────────────────────────────

_IAEA_RSS_URL = "https://www.iaea.org/feeds/topical-rss-feed.xml?topic=All&subtopic=All"
_NUCLEAR_KEYWORDS = re.compile(
    r'nuclear|radioactive|radiation|reactor|contamination|isotope|'
    r'plutonium|uranium|cesium|iodine|fukushima|chernobyl|NPP|kernenerg',
    re.IGNORECASE
)

def _fetch_iaea_incidents() -> list[RadPoint]:
    """Parsed IAEA RSS-Feed auf nukleare Vorfallsmeldungen."""
    try:
        req = urllib.request.Request(
            _IAEA_RSS_URL,
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read(200_000)

        root = ET.fromstring(xml_data)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}

        points = []
        for entry in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("title") or
                     entry.findtext("{http://www.w3.org/2005/Atom}title") or "")
            desc  = (entry.findtext("description") or
                     entry.findtext("{http://www.w3.org/2005/Atom}summary") or "")
            text  = title + " " + desc

            if not _NUCLEAR_KEYWORDS.search(text):
                continue

            # IAEA-Meldungen haben keine Koordinaten → Marker bei IAEA-HQ Wien
            points.append(RadPoint(
                lat          = 48.2359,   # Wien (IAEA-HQ)
                lon          = 16.4023,
                value_cpm    = 999,        # Symbolisch für IAEA-Alert
                alert_level  = "KRITISCH",
                anomaly_mult = 99.0,
                source       = "iaea",
                station_name = "IAEA-Meldung",
                description  = title[:120],
            ))

        return points[:5]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 3. EURDEP (Europäisches Strahlungsmessnetz)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_eurdep(region: str) -> list[RadPoint]:
    """
    EURDEP Radiation Data Exchange Platform (EU-Kommission, öffentlich).
    https://remap.jrc.ec.europa.eu/EURDEP.aspx
    """
    # Bounding-Box für Region
    _BBOX = {
        "ukraine":     "22,44,40,52",
        "deutschland": "6,47,15,55",
        "europa":      "5,35,32,60",
        "naher osten": "34,29,43,38",
        "weissrussland": "23,51,33,54",
        "belarus":     "23,51,33,54",
    }
    bbox = None
    for k, v in _BBOX.items():
        if k in region.lower():
            bbox = v
            break
    if not bbox:
        bbox = "5,35,32,60"  # Europa default

    try:
        w, s, e, n = bbox.split(",")
        url = (
            "https://remap.jrc.ec.europa.eu/api/data?"
            f"dateTimeFrom=2024-01-01T00:00:00Z"
            f"&boundingBox={w},{s},{e},{n}&source=EURDEP"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())

        points = []
        for item in (data.get("features") or [])[:30]:
            props = item.get("properties", {})
            coords = item.get("geometry", {}).get("coordinates", [0,0])
            lat = float(coords[1] if len(coords) > 1 else 0)
            lon = float(coords[0] if len(coords) > 0 else 0)
            if not lat or not lon:
                continue
            val = float(props.get("value", 0) or 0)
            if val <= 0:
                continue
            # EURDEP misst in µSv/h → zu CPM konvertieren (Näherung: 1 µSv/h ≈ 100 CPM)
            cpm = val * 100
            baseline = _DEFAULT_BASELINE_CPM
            mult = cpm / baseline
            level = _anomaly_level(mult)
            if level == "NORMAL":
                continue
            points.append(RadPoint(
                lat=lat, lon=lon, value_cpm=round(cpm,1),
                baseline_cpm=baseline, alert_level=level,
                anomaly_mult=round(mult,2), source="eurdep",
                station_name=props.get("stationId","")[:20],
                description=f"EURDEP: {val:.3f} µSv/h ({mult:.1f}×)",
            ))
        return points
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Safecast (crowd-sourced, kostenlos)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_safecast(region: str) -> list[RadPoint]:
    """Safecast.org API – bürgerwissenschaftliche Strahlungsdaten."""
    _LAT_LON = {
        "ukraine":  (48.5, 32.0),
        "japan":    (35.7, 139.7),
        "europa":   (50.0, 15.0),
    }
    lat_lon = (50.0, 15.0)
    for k, v in _LAT_LON.items():
        if k in region.lower():
            lat_lon = v
            break

    try:
        params = urllib.parse.urlencode({
            "latitude":  lat_lon[0],
            "longitude": lat_lon[1],
            "distance":  300,   # km
            "limit":     20,
        })
        req = urllib.request.Request(
            f"https://api.safecast.org/measurements.json?{params}",
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        points = []
        for m in (data if isinstance(data, list) else [])[:20]:
            lat = float(m.get("latitude", 0) or 0)
            lon = float(m.get("longitude", 0) or 0)
            val = float(m.get("value", 0) or 0)
            unit = m.get("unit", "cpm").lower()
            if not lat or not lon or val <= 0:
                continue

            cpm = val if "cpm" in unit else val * 100
            mult = cpm / _DEFAULT_BASELINE_CPM
            level = _anomaly_level(mult)
            if level == "NORMAL":
                continue
            points.append(RadPoint(
                lat=lat, lon=lon, value_cpm=round(cpm,1),
                baseline_cpm=_DEFAULT_BASELINE_CPM, alert_level=level,
                anomaly_mult=round(mult,2), source="safecast",
                description=f"Safecast: {val:.0f} {unit} ({mult:.1f}×)",
            ))
        return points
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def get_radiation_data(region: str = "Europa") -> list[RadPoint]:
    """Aggregiert Strahlungsdaten aus allen verfügbaren Quellen."""
    points: list[RadPoint] = []
    points.extend(_fetch_iaea_incidents())        # IAEA zuerst (höchste Priorität)
    points.extend(_fetch_epa_radnet())            # EPA RadNet (USA)
    points.extend(_fetch_eurdep(region))          # EURDEP (Europa)
    points.extend(_fetch_safecast(region))        # Safecast (crowdsourced)

    # Duplikate entfernen (gleiche Position)
    seen = set()
    unique = []
    for p in sorted(points, key=lambda x: x.anomaly_mult, reverse=True):
        key = (round(p.lat, 1), round(p.lon, 1))
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique[:20]


def radiation_for_map(region: str = "Europa") -> list[dict]:
    points = get_radiation_data(region)
    markers = []
    _COLOR = {"KRITISCH":"#ff0044","ERHÖHT":"#ff6600","LEICHT_ERHÖHT":"#ffaa00","NORMAL":"#00ff88"}
    for p in points:
        col = _COLOR.get(p.alert_level, "#ff8800")
        markers.append({
            "lat":          p.lat,
            "lon":          p.lon,
            "title":        f"☢ {p.station_name or p.source} – {p.alert_level}",
            "text":         p.description,
            "value_cpm":    p.value_cpm,
            "alert_level":  p.alert_level,
            "anomaly_mult": p.anomaly_mult,
            "confidence":   min(0.95, p.anomaly_mult / 15),
            "color":        col,
            "icon":         "☢",
            "source":       p.source,
        })
    return markers


def radiation_summary(points: list[RadPoint]) -> str:
    if not points:
        return ""
    crit = [p for p in points if p.alert_level == "KRITISCH"]
    high = [p for p in points if p.alert_level == "ERHÖHT"]
    lines = [f"[RADNET] {len(points)} Strahlungs-Anomalien "
             f"({len(crit)} kritisch, {len(high)} erhöht):\n"]
    for p in points[:6]:
        lines.append(
            f"  ☢ {p.station_name[:25]} [{p.lat:.2f},{p.lon:.2f}] "
            f"{p.alert_level} {p.anomaly_mult:.1f}×\n     {p.description[:80]}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print("Teste nexus_radnet.py...")
    results = get_radiation_data("Europa")
    print(f"Strahlungs-Anomalien: {len(results)}")
    for r in results[:5]:
        print(f"  ☢ {r.station_name[:25]} [{r.lat:.2f},{r.lon:.2f}] "
              f"{r.alert_level} {r.anomaly_mult:.1f}×")
    print(radiation_summary(results))
