"""
nexus_usns.py — Militär-Versorgungsschiff Tracking (T171)
==========================================================
Verfolgt USNS und NATO/RUS/CHN Marinelogistik-Schiffe in strategischen Seegebieten.

Datenquellen (key-frei, fallend):
  1. AISHub Anonymous (Hormuz, Rotes Meer, Mittelmeer, Pazifik)
  2. nexus_ais.py Fallback-Kette (AISStream → GlobalFishingWatch → datalastic)
  3. MarineTraffic Frei-Tier (Geschichtssuche via name)

Erkannte Klassen:
  USNS    — US Military Sealift Command (T-AO, T-AKE, T-AKR, T-AFS, T-AGS, T-AK)
  RFA     — Royal Fleet Auxiliary (UK)
  Durance — Französische Flotte
  CHN-AOR — Type-903/903A Versorger (PLA Navy)
  RUS-AOR — Berezina/Dubna-Klasse (russische Marine)
  Prepo   — US Army/Navy Prepositionierungs-Schiffe (LMSR, APS)
"""

from __future__ import annotations
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Kennung-Listen
# ─────────────────────────────────────────────────────────────────────────────

# Namens-Fragmente → Klasse, Nation, operative Bedeutung
_NAME_PATTERNS: list[tuple[str, str, str, str]] = [
    # (name_fragment, klasse, nation, bedeutung)
    # USNS MSC-Tanker (T-AO)
    ("HENRY J. KAISER",     "T-AO (Tanker)",       "USA",    "MSC-Tanker, versorgt CSG mit Treibstoff"),
    ("JOHN LEWIS",          "T-AO (Tanker)",       "USA",    "John-Lewis-Klasse Tanker (neueste MSC-Klasse)"),
    ("GUADALUPE",           "T-AO (Tanker)",       "USA",    "MSC-Tanker"),
    ("PECOS",               "T-AO (Tanker)",       "USA",    "MSC-Tanker"),
    ("BIG HORN",            "T-AO (Tanker)",       "USA",    "MSC-Tanker"),
    ("TIPPECANOE",          "T-AO (Tanker)",       "USA",    "MSC-Tanker"),
    ("PATUXENT",            "T-AO (Tanker)",       "USA",    "MSC-Tanker"),
    # USNS MSC Trocken-Versorger (T-AKE)
    ("LEWIS AND CLARK",     "T-AKE (Dry Cargo)",   "USA",    "Lewis&Clark-Klasse — kombinierter Versorger"),
    ("SACAGAWEA",           "T-AKE (Dry Cargo)",   "USA",    "T-AKE Trocken-/Munitions-Versorger"),
    ("ALAN SHEPARD",        "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("RICHARD E. BYRD",     "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("AMELIA EARHART",      "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("MATTHEW PERRY",       "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("CHARLES DREW",        "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("WILLIAM MCLEAN",      "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("MEDGAR EVERS",        "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("CARL BRASHEAR",       "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("WASHINGTON CHAMBERS", "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("CÉSAR CHÁVEZ",        "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("WALLY SCHIRRA",       "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("ROBERT PEARY",        "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    ("RICHARD BYRD",        "T-AKE (Dry Cargo)",   "USA",    "T-AKE"),
    # MSC Seeraumtransporter (T-AKR / Large Medium Speed RoRo)
    ("BOB HOPE",            "T-AKR (RoRo)",        "USA",    "LMSR — Großraumtransporter für Militärfahrzeuge"),
    ("FISHER",              "T-AKR (RoRo)",        "USA",    "LMSR"),
    ("SEAY",                "T-AKR (RoRo)",        "USA",    "LMSR"),
    ("MENDONCA",            "T-AKR (RoRo)",        "USA",    "LMSR"),
    ("PILILAAU",            "T-AKR (RoRo)",        "USA",    "LMSR"),
    ("SHUGHART",            "T-AKR (RoRo)",        "USA",    "LMSR"),
    ("GORDON",              "T-AKR (RoRo)",        "USA",    "LMSR"),
    ("YANO",                "T-AKR (RoRo)",        "USA",    "LMSR"),
    # UK Royal Fleet Auxiliary
    ("RFA ",                "RFA (UK)",            "GBR",    "Royal Fleet Auxiliary — UK-Marine-Logistik"),
    ("TIDE",                "RFA Tide (Tanker)",   "GBR",    "UK-Flottentanker"),
    ("FORT VICTORIA",       "RFA (Versorger)",     "GBR",    "AFSH-Klasse Volltanker"),
    ("ARGUS",               "RFA Argus",           "GBR",    "Lazarettschiff / Aviation Training"),
    ("LYME BAY",            "RFA Bay (LSD)",       "GBR",    "Landing Ship Dock"),
    ("MOUNTS BAY",          "RFA Bay (LSD)",       "GBR",    "Landing Ship Dock"),
    ("CARDIGAN BAY",        "RFA Bay (LSD)",       "GBR",    "Landing Ship Dock"),
    # France Marine Nationale / AOR
    ("DUQUESNE",            "AOR (FR)",            "FRA",    "Französischer Versorger"),
    ("SOMME",               "AOR (FR)",            "FRA",    "Durance-Klasse Versorger"),
    ("MARNE",               "AOR (FR)",            "FRA",    "Durance-Klasse"),
    ("MEUSE",               "AOR (FR)",            "FRA",    "Durance-Klasse"),
    ("DURANCE",             "AOR (FR)",            "FRA",    "Durance-Klasse"),
    ("VAR",                 "AOR (FR)",            "FRA",    "Durance-Klasse"),
    # China PLA Navy Type-903/903A
    ("QIANDAO HU",          "Type-903 (CHN)",      "CHN",    "PLAN AOR — kombinierter Versorger"),
    ("WEISHANHU",           "Type-903 (CHN)",      "CHN",    "PLAN AOR"),
    ("DONGPINGHU",          "Type-903A (CHN)",     "CHN",    "PLAN AOR (neuere Version)"),
    ("LUOMAHU",             "Type-903A (CHN)",     "CHN",    "PLAN AOR"),
    ("GAOYOUHU",            "Type-903A (CHN)",     "CHN",    "PLAN AOR"),
    ("CHAGAN HU",           "Type-903A (CHN)",     "CHN",    "PLAN AOR"),
    ("TAIHU",               "Type-903A (CHN)",     "CHN",    "PLAN AOR"),
    # Russia
    ("BEREZINA",            "AOR (RUS)",           "RUS",    "Russischer Großversorger"),
    ("DUBNA",               "AOR (RUS)",           "RUS",    "Klasse Dubna / Berezina"),
    ("YELETS",              "AOR (RUS)",           "RUS",    "Russischer Tanker/Versorger"),
    # Generic USNS prefix
    ("USNS ",               "USNS (MSC)",          "USA",    "US Military Sealift Command"),
    # Generic MSC prefix
    ("MSC ",                "MSC (Charterschiff)", "USA",    "MSC-gechartertes Schiff"),
]

# MMSI-Präfixe bekannter Marinen (3-stellig = Land-Präfix)
_NAVAL_MMSI_PREFIXES: dict[str, str] = {
    "338": "USA",
    "303": "USA (USCG/MSC)",
    "232": "GBR",
    "227": "FRA",
    "273": "RUS",
    "412": "CHN",
    "477": "CHN (HKG)",
    "244": "NLD",
    "211": "DEU",
    "247": "ITA",
    "224": "ESP",
}

# AIS-Schiffstyp-Codes für militärische / Versorger Kategorien
_MILITARY_SHIP_TYPES: set[int] = {
    35,  # Military ops
    51,  # SAR vessels
    55,  # Law Enforcement
}
_NAVAL_SUPPLY_TYPES: set[int] = {
    80, 81, 82, 83, 84, 89,  # Tanker
    70, 71, 72, 73, 74, 79,  # Cargo
}

# ─────────────────────────────────────────────────────────────────────────────
# Strategische Seegebiete
# ─────────────────────────────────────────────────────────────────────────────

STRATEGIC_REGIONS: dict[str, dict] = {
    "hormuz": {
        "name": "Straße von Hormuz",
        "lat_min": 23.5, "lat_max": 28.0,
        "lon_min": 54.0, "lon_max": 60.0,
        "significance": "Öl-Transitroute — USNS-Präsenz = US-Navy-Versorgung",
        "anchor_zone": {"lat_min": 25.0, "lat_max": 27.5, "lon_min": 55.0, "lon_max": 58.0},
    },
    "rotes_meer": {
        "name": "Rotes Meer / Bab-el-Mandeb",
        "lat_min": 12.0, "lat_max": 22.0,
        "lon_min": 40.0, "lon_max": 45.0,
        "significance": "Houthi-Sperr-Zone — Versorger zeigen Escortoperationen an",
        "anchor_zone": None,
    },
    "ostmittelmeer": {
        "name": "Östliches Mittelmeer",
        "lat_min": 30.0, "lat_max": 38.0,
        "lon_min": 28.0, "lon_max": 38.0,
        "significance": "Nahost-Krisengebiet — NATO-Logistik sichtbar",
        "anchor_zone": None,
    },
    "arabisches_meer": {
        "name": "Nördliches Arabisches Meer",
        "lat_min": 18.0, "lat_max": 25.0,
        "lon_min": 57.0, "lon_max": 66.0,
        "significance": "Transit-Zone für CSG-Versorgung Hormuz / Indien",
        "anchor_zone": None,
    },
    "suez": {
        "name": "Suezkanal-Zone",
        "lat_min": 29.5, "lat_max": 32.5,
        "lon_min": 31.5, "lon_max": 33.5,
        "significance": "Transitzählung Versorger Mittelmeer ↔ Indischer Ozean",
        "anchor_zone": None,
    },
    "taiwan": {
        "name": "Taiwan-Straße / Westpazifik",
        "lat_min": 21.0, "lat_max": 27.0,
        "lon_min": 118.0, "lon_max": 124.0,
        "significance": "PLAN AOR Präsenz = Flotteneinsatz nahe Taiwan",
        "anchor_zone": None,
    },
    "ostsee": {
        "name": "Ostsee",
        "lat_min": 54.0, "lat_max": 60.0,
        "lon_min": 10.0, "lon_max": 30.0,
        "significance": "NATO-Übung / Verstärkung Flanke",
        "anchor_zone": None,
    },
    "schwarzes_meer": {
        "name": "Schwarzes Meer (Ausgänge)",
        "lat_min": 41.0, "lat_max": 43.5,
        "lon_min": 28.5, "lon_max": 32.0,
        "significance": "Bosporus-Passage — Russische Seeversorgung Ukraine-Front",
        "anchor_zone": None,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Datenstrukturen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NavalVessel:
    name:        str
    klasse:      str
    nation:      str
    bedeutung:   str
    lat:         float
    lon:         float
    speed:       float
    heading:     int
    mmsi:        str
    ais_type:    int
    region:      str
    anchor:      bool          # speed < 1 kn = ankert
    threat_relevance: str      # "HOCH" | "MITTEL" | "NIEDRIG"


@dataclass
class NavalTracking:
    timestamp:     str
    regions_checked: list[str]
    vessels:       list[NavalVessel]
    usns_count:    int
    nato_count:    int
    chn_count:     int
    rus_count:     int
    anchoring:     int
    alerts:        list[str]
    summary:       str


# ─────────────────────────────────────────────────────────────────────────────
# AIS-Abfrage
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_aishub_region(lat_min: float, lon_min: float,
                          lat_max: float, lon_max: float) -> list[dict]:
    """AISHub anonymous query für Bounding Box."""
    try:
        params = {
            "username": "AH_ANONYMOUS_USER",
            "format":   1,
            "output":   "json",
            "compress": 0,
            "latmin":   lat_min,
            "latmax":   lat_max,
            "lonmin":   lon_min,
            "lonmax":   lon_max,
        }
        url = "https://data.aishub.net/ws.php?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list) and len(data) > 1:
            return data[1] if isinstance(data[1], list) else []
        return []
    except Exception:
        return []


def _fetch_nexus_ais_region(lat_min: float, lon_min: float,
                              lat_max: float, lon_max: float) -> list[dict]:
    """Verwendet nexus_ais.py Fallback-Kette."""
    try:
        from nexus_ais import _fetch_aisstream, _fetch_globalfishingwatch  # type: ignore
        vessels = _fetch_aisstream(lat_min, lon_min, lat_max, lon_max) or []
        if not vessels:
            vessels = _fetch_globalfishingwatch(lat_min, lon_min, lat_max, lon_max) or []
        return vessels or []
    except Exception:
        return []


def _normalize_vessel(raw: dict) -> dict:
    """Normalisiert verschiedene AIS-Format-Varianten zu einheitlichem Dict."""
    name  = str(raw.get("NAME", "")  or raw.get("name", "")  or "").upper().strip()
    lat   = float(raw.get("LATITUDE",  0) or raw.get("lat",   0) or 0)
    lon   = float(raw.get("LONGITUDE", 0) or raw.get("lon",   0) or 0)
    speed = float(raw.get("SOG",       0) or raw.get("speed", 0) or 0)
    hdg   = int(  raw.get("COG",       0) or raw.get("course",0) or 0)
    mmsi  = str(  raw.get("MMSI",      "") or raw.get("mmsi", "") or "")
    stype = int(  raw.get("SHIPTYPE",  0) or raw.get("ship_type", 0) or 0)
    return {"name": name, "lat": lat, "lon": lon,
            "speed": speed, "heading": hdg, "mmsi": mmsi, "ship_type": stype}


# ─────────────────────────────────────────────────────────────────────────────
# Klassifikation
# ─────────────────────────────────────────────────────────────────────────────

def _classify_vessel(name: str, mmsi: str,
                      ship_type: int) -> Optional[tuple[str, str, str]]:
    """
    Versucht ein Schiff als Marinelogistik zu klassifizieren.
    Gibt (klasse, nation, bedeutung) zurück oder None wenn kein Treffer.
    """
    # 1. Namensbasiert
    for fragment, klasse, nation, bedeutung in _NAME_PATTERNS:
        if fragment in name:
            return klasse, nation, bedeutung

    # 2. MMSI-Präfix für Militär
    prefix3 = mmsi[:3] if len(mmsi) >= 3 else ""
    if prefix3 in _NAVAL_MMSI_PREFIXES:
        nation = _NAVAL_MMSI_PREFIXES[prefix3]
        if ship_type in _MILITARY_SHIP_TYPES:
            return "Militär (AIS-Typ 35)", nation, "Militärschiff laut AIS-Typ"
        # MMSI allein reicht nicht — zu viele False Positives bei Handelsschiffen

    # 3. USNS-Präfix direkt
    if name.startswith("USNS "):
        return "USNS (unbekannte Klasse)", "USA", "US Military Sealift Command"

    return None


def _threat_relevance(klasse: str, nation: str, speed: float, region: str) -> str:
    """Bewertet operative Relevanz: HOCH / MITTEL / NIEDRIG."""
    score = 0
    # Hochwertig-Klassen
    if any(k in klasse for k in ["T-AO", "T-AKE", "T-AKR", "Type-903", "RFA"]):
        score += 3
    elif "USNS" in klasse or "MSC" in klasse:
        score += 2
    else:
        score += 1

    # Krisenregionen
    if region in ("hormuz", "rotes_meer", "taiwan", "schwarzes_meer"):
        score += 2
    elif region in ("arabisches_meer", "suez", "ostmittelmeer"):
        score += 1

    # Ankernde Schiffe in Krisengebiet = auffällig
    if speed < 1.0 and region in ("hormuz", "rotes_meer", "taiwan"):
        score += 1

    if score >= 5:
        return "HOCH"
    elif score >= 3:
        return "MITTEL"
    return "NIEDRIG"


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Tracking-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def track_naval_supply(regions: Optional[list[str]] = None,
                        max_vessels_per_region: int = 200) -> NavalTracking:
    """
    Abfrage aller angegebenen strategischen Regionen auf Marinelogistik-Schiffe.

    Args:
        regions: Liste von Regionsnamen aus STRATEGIC_REGIONS.keys().
                 None = alle 8 Regionen.
        max_vessels_per_region: Abbruch nach N Schiffen pro Region.

    Returns:
        NavalTracking mit allen gefundenen Schiffen und Bewertung.
    """
    if regions is None:
        regions = list(STRATEGIC_REGIONS.keys())

    all_vessels:  list[NavalVessel] = []
    seen_mmsi:    set[str]          = set()
    alerts:       list[str]         = []
    checked:      list[str]         = []

    for region_key in regions:
        region = STRATEGIC_REGIONS.get(region_key)
        if not region:
            continue
        checked.append(region_key)

        lat_min = region["lat_min"]
        lat_max = region["lat_max"]
        lon_min = region["lon_min"]
        lon_max = region["lon_max"]

        # Daten holen
        raw_vessels = _fetch_aishub_region(lat_min, lon_min, lat_max, lon_max)
        if not raw_vessels:
            raw_vessels = _fetch_nexus_ais_region(lat_min, lon_min, lat_max, lon_max)

        if not raw_vessels:
            continue

        # Anchor zone
        az = region.get("anchor_zone")
        anchor_count = 0

        for raw in raw_vessels[:max_vessels_per_region]:
            v = _normalize_vessel(raw)
            if not v["name"] and not v["mmsi"]:
                continue
            if v["mmsi"] and v["mmsi"] in seen_mmsi:
                continue

            # Klassifizierungsversuch
            clf = _classify_vessel(v["name"], v["mmsi"], v["ship_type"])
            if clf is None:
                continue  # Kein Marinelogistik-Schiff

            klasse, nation, bedeutung = clf

            # Anchor check
            anchored = v["speed"] < 1.0
            if az and anchored:
                if (az["lat_min"] <= v["lat"] <= az["lat_max"] and
                        az["lon_min"] <= v["lon"] <= az["lon_max"]):
                    anchor_count += 1

            threat = _threat_relevance(klasse, nation, v["speed"], region_key)

            vessel = NavalVessel(
                name       = v["name"] or f"MMSI:{v['mmsi']}",
                klasse     = klasse,
                nation     = nation,
                bedeutung  = bedeutung,
                lat        = v["lat"],
                lon        = v["lon"],
                speed      = v["speed"],
                heading    = v["heading"],
                mmsi       = v["mmsi"],
                ais_type   = v["ship_type"],
                region     = region_key,
                anchor     = anchored,
                threat_relevance = threat,
            )
            all_vessels.append(vessel)
            if v["mmsi"]:
                seen_mmsi.add(v["mmsi"])

        # Region-spezifische Alerts
        region_naval = [vv for vv in all_vessels if vv.region == region_key]
        usns_here = [vv for vv in region_naval if "USA" in vv.nation]
        chn_here  = [vv for vv in region_naval if "CHN" in vv.nation]
        rus_here  = [vv for vv in region_naval if "RUS" in vv.nation]

        rname = region["name"]
        if usns_here and region_key in ("hormuz", "rotes_meer", "taiwan"):
            alerts.append(f"⚠️ {len(usns_here)} USNS/MSC-Schiffe in {rname} — US-Navy-Logistik aktiv")
        if chn_here and region_key in ("taiwan", "rotes_meer"):
            alerts.append(f"🔴 {len(chn_here)} PLAN-Versorger in {rname} — chinesische Flotte versorgt")
        if rus_here and region_key in ("schwarzes_meer", "ostmittelmeer"):
            alerts.append(f"🔴 {len(rus_here)} russische Versorger in {rname}")
        if anchor_count >= 8:
            alerts.append(f"⚠️ {anchor_count} Schiffe ankern in {rname}-Wartegebiet — Stau / Blockade?")

    # Gesamt-Statistik
    usns_total = sum(1 for v in all_vessels if "USA" in v.nation)
    nato_total = sum(1 for v in all_vessels if v.nation in ("GBR", "FRA", "DEU", "NLD", "ITA", "ESP"))
    chn_total  = sum(1 for v in all_vessels if "CHN" in v.nation)
    rus_total  = sum(1 for v in all_vessels if "RUS" in v.nation)
    anch_total = sum(1 for v in all_vessels if v.anchor)

    high_count = sum(1 for v in all_vessels if v.threat_relevance == "HOCH")

    summary_parts = []
    if usns_total:
        summary_parts.append(f"{usns_total} USNS/MSC (USA)")
    if nato_total:
        summary_parts.append(f"{nato_total} NATO-Versorger")
    if chn_total:
        summary_parts.append(f"{chn_total} PLAN-AOR (CHN)")
    if rus_total:
        summary_parts.append(f"{rus_total} RUS-Versorger")

    if summary_parts:
        summary = "Marinelogistik: " + " | ".join(summary_parts)
        if high_count:
            summary += f" — {high_count} HOCH-Relevanz"
    else:
        summary = "Keine Marinelogistik-Schiffe in AIS sichtbar (Daten ggf. begrenzt)"

    return NavalTracking(
        timestamp        = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        regions_checked  = checked,
        vessels          = all_vessels,
        usns_count       = usns_total,
        nato_count       = nato_total,
        chn_count        = chn_total,
        rus_count        = rus_total,
        anchoring        = anch_total,
        alerts           = alerts,
        summary          = summary,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Livekarte: Marker für naval_for_map()
# ─────────────────────────────────────────────────────────────────────────────

# Farb-Schema nach Nation
_NATION_COLORS: dict[str, str] = {
    "USA": "#4488ff",    # blau
    "GBR": "#00aaff",    # hellblau
    "FRA": "#0066cc",    # dunkelblau
    "CHN": "#ff2200",    # rot
    "RUS": "#cc0000",    # dunkelrot
    "DEU": "#888888",
    "NLD": "#ff8800",
    "ITA": "#00cc44",
    "ESP": "#ddaa00",
}

_NATION_FLAGS: dict[str, str] = {
    "USA": "🇺🇸", "GBR": "🇬🇧", "FRA": "🇫🇷",
    "CHN": "🇨🇳", "RUS": "🇷🇺", "DEU": "🇩🇪",
    "NLD": "🇳🇱", "ITA": "🇮🇹", "ESP": "🇪🇸",
}


def naval_ships_for_map(regions: Optional[list[str]] = None) -> list[dict]:
    """
    Gibt Leaflet-kompatible Marker für alle Naval-Supply-Schiffe zurück.
    Nutzung: result["naval_supply"] in nexus_live_server._fetch_live_data()
    """
    tracking = track_naval_supply(regions)
    markers  = []

    for v in tracking.vessels:
        color   = _NATION_COLORS.get(v.nation, "#aaaaaa")
        flag    = _NATION_FLAGS.get(v.nation, "🚢")
        icon    = "⚓" if v.anchor else "⚓"
        rel_cls = {"HOCH": "#ff2222", "MITTEL": "#ff8800", "NIEDRIG": "#4488ff"}[v.threat_relevance]
        region_info = STRATEGIC_REGIONS.get(v.region, {})
        region_name = region_info.get("name", v.region)

        popup = (
            f"<b>{flag} {v.name}</b><br>"
            f"<b>Klasse:</b> {v.klasse}<br>"
            f"<b>Nation:</b> {v.nation}<br>"
            f"<b>Bedeutung:</b> {v.bedeutung}<br>"
            f"<b>Region:</b> {region_name}<br>"
            f"<b>Geschwindigkeit:</b> {v.speed:.1f} kn"
            + (" ⚓ ankert" if v.anchor else "") + "<br>"
            f"<b>Relevanz:</b> <span style='color:{rel_cls}'>{v.threat_relevance}</span><br>"
            + (f"<b>MMSI:</b> {v.mmsi}<br>" if v.mmsi else "")
        )

        markers.append({
            "lat":      v.lat,
            "lon":      v.lon,
            "type":     "naval-supply",
            "icon":     f"{flag}⚓",
            "color":    color,
            "category": v.klasse,
            "nation":   v.nation,
            "anchor":   v.anchor,
            "relevance": v.threat_relevance,
            "title":    f"{flag} {v.name} [{v.klasse}]",
            "popup":    popup,
            "source":   "AIS / MSC Watchlist",
        })

    return markers


def naval_summary() -> dict:
    """
    Schnell-Summary für nexus_escalation.py / Dashboard-Integration.
    """
    tracking = track_naval_supply(regions=["hormuz", "rotes_meer", "taiwan",
                                           "ostmittelmeer", "arabisches_meer"])
    return {
        "usns_count":   tracking.usns_count,
        "nato_count":   tracking.nato_count,
        "chn_count":    tracking.chn_count,
        "rus_count":    tracking.rus_count,
        "anchoring":    tracking.anchoring,
        "total":        len(tracking.vessels),
        "alerts":       tracking.alerts,
        "summary":      tracking.summary,
        "timestamp":    tracking.timestamp,
        "vessels":      [
            {
                "name":      v.name,
                "klasse":    v.klasse,
                "nation":    v.nation,
                "lat":       v.lat,
                "lon":       v.lon,
                "speed":     v.speed,
                "region":    v.region,
                "anchor":    v.anchor,
                "relevance": v.threat_relevance,
            }
            for v in tracking.vessels
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS Militär-Versorgungsschiff Tracking")
    parser.add_argument("--region", type=str, default=None,
                        help=f"Regionen (kommagetrennt). Verfügbar: {', '.join(STRATEGIC_REGIONS)}")
    parser.add_argument("--json", action="store_true",
                        help="JSON-Ausgabe")
    args = parser.parse_args()

    regions = [r.strip() for r in args.region.split(",")] if args.region else None

    print("[NEXUS USNS] Starte Naval-Supply-Tracking ...", file=sys.stderr)
    t = track_naval_supply(regions)

    if args.json:
        print(json.dumps({
            "timestamp": t.timestamp,
            "usns_count": t.usns_count,
            "nato_count": t.nato_count,
            "chn_count": t.chn_count,
            "rus_count": t.rus_count,
            "anchoring": t.anchoring,
            "alerts": t.alerts,
            "vessels": [
                {"name": v.name, "klasse": v.klasse, "nation": v.nation,
                 "lat": v.lat, "lon": v.lon, "speed": v.speed,
                 "region": v.region, "anchor": v.anchor,
                 "relevance": v.threat_relevance}
                for v in t.vessels
            ],
        }, indent=2))
    else:
        print(f"\n=== Naval Supply Tracking — {t.timestamp} ===")
        print(f"  Regionen: {', '.join(t.regions_checked)}")
        print(f"  USNS/MSC (USA): {t.usns_count}")
        print(f"  NATO-Versorger: {t.nato_count}")
        print(f"  PLAN-AOR (CHN): {t.chn_count}")
        print(f"  RUS-Versorger:  {t.rus_count}")
        print(f"  Ankernde:       {t.anchoring}")
        print()
        for alert in t.alerts:
            print(f"  {alert}")
        print()
        for v in sorted(t.vessels, key=lambda x: x.threat_relevance):
            flag = _NATION_FLAGS.get(v.nation, "🚢")
            anch = " ⚓" if v.anchor else ""
            print(f"  {flag} {v.name:<30} {v.klasse:<22} "
                  f"{v.region:<15} {v.speed:.1f}kn{anch} [{v.threat_relevance}]")
