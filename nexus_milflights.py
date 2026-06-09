"""
nexus_milflights.py — Militärflüge als Blockade-Indikator (T172)
=================================================================
Wertet Militär- und ISR-Flüge über strategischen Seegebieten aus
und berechnet daraus Blockade-Frühwarnsignale.

Logik:
  P-8 Poseidon  → Maritime Patrouille / ASW = Schiffe werden überwacht
  KC-135/KC-46  → Lufttankung = nachhaltige Operationen (≥12h)
  RC-135/EP-3   → SIGINT-Aufklärung = Kommunikationserfassung
  E-3 AWACS     → Luftraumkontrolle = Blockade-Luftschutz
  RQ-4/MQ-4C   → Dauerüberwachung = persistentes ISR

  P-8 + KC-Tanker in gleicher Region → "sustained maritime patrol" = HOCH
  RC-135 über Choke Point           → "signals collection active" = HOCH
  ≥3 verschiedene ISR-Typen         → "multi-domain surveillance" = KRITISCH

Datenquelle: nexus_flights.py → ADS-B Exchange / OpenSky
"""

from __future__ import annotations
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Strategische Seegebiete mit erweiterter Flug-Bounding-Box
# ─────────────────────────────────────────────────────────────────────────────

MARITIME_ZONES: dict[str, dict] = {
    "Hormuz-Strasse": {
        "query":       "Hormuz-Strasse",   # nexus_flights.py kennt diese Region
        "display":     "Strait of Hormuz",
        "lat_min": 18.0, "lat_max": 30.0,
        "lon_min": 50.0, "lon_max": 65.0,
        "significance": "Öl-Transitroute — ISR hier = US Navy überwacht Iran-Seestreitkräfte",
        "threat_multipler": 1.5,   # Hochkritische Region
    },
    "Rotes Meer": {
        "query":       "Rotes Meer",
        "display":     "Red Sea / Bab-el-Mandeb",
        "lat_min": 12.0, "lat_max": 22.0,
        "lon_min": 32.0, "lon_max": 45.0,
        "significance": "Houthi-Zone — P-8/RC-135 = aktive Anti-Schiff-Aufklärung",
        "threat_multipler": 1.4,
    },
    "Naher Osten": {
        "query":       "Naher Osten",
        "display":     "Eastern Mediterranean / Levant",
        "lat_min": 28.0, "lat_max": 38.0,
        "lon_min": 25.0, "lon_max": 42.0,
        "significance": "Nahost-Krisengebiet — AWACS + ISR = aktive Luftoperationen",
        "threat_multipler": 1.2,
    },
    "Taiwan": {
        "query":       "Taiwan",
        "display":     "Taiwan Strait / Western Pacific",
        "lat_min": 21.0, "lat_max": 27.0,
        "lon_min": 117.0, "lon_max": 125.0,
        "significance": "PLAN/USN Konfrontation — P-8 = ASW gegen PLAN-U-Boote",
        "threat_multipler": 1.5,
    },
    "Schwarzes Meer": {
        "query":       "Schwarzes Meer",
        "display":     "Black Sea (NATO approach)",
        "lat_min": 41.0, "lat_max": 48.0,
        "lon_min": 27.0, "lon_max": 41.0,
        "significance": "Ukraine-Kriegs-Theater — ISR-Aktivität = Frontlage-Überwachung",
        "threat_multipler": 1.3,
    },
    "Ukraine": {
        "query":       "Ukraine",
        "display":     "Ukraine / Eastern Front",
        "lat_min": 44.0, "lat_max": 52.0,
        "lon_min": 22.0, "lon_max": 40.0,
        "significance": "Kriegsgebiet — E-8 JSTARS/RC-135 = Truppenbewegungen",
        "threat_multipler": 1.1,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# ISR-Rollen und ihre Blockade-Relevanz
# ─────────────────────────────────────────────────────────────────────────────

# Relevanz-Gewichte nach ISR-Rolle
_ROLE_WEIGHTS: dict[str, float] = {
    "ASW/ISR":   3.0,   # P-8 Poseidon — direkte Maritime-Patrouille
    "ASW":       3.0,   # Anti-submarine warfare
    "SIGINT":    2.5,   # RC-135 — Signalaufklärung
    "ELINT":     2.5,   # Electronic intelligence
    "AWACS":     2.0,   # E-3 Sentry — Luftraumkontrolle
    "Tanker":    2.0,   # KC-135/KC-46 — Nachhaltige Operationen
    "ISR-UAV":   2.0,   # RQ-4/MQ-9 — Persistentes ISR
    "IMINT":     1.8,   # Bildaufklärung
    "ISR":       1.5,   # Allgemeine Aufklärung
    "GMTI":      1.5,   # E-8 JSTARS Bodenziel-Tracking
    "C2":        1.2,   # Kommandokontrolle
    "SIGINT/ASW": 3.0,  # Kombiniert
}

# Maximale Blockade-Signal-Stärken
_SIGNAL_LEVELS: list[tuple[float, str, str]] = [
    (8.0, "KRITISCH", "#ff0000"),
    (5.0, "HOCH",     "#ff4400"),
    (2.5, "MITTEL",   "#ff8800"),
    (1.0, "NIEDRIG",  "#ffcc00"),
    (0.0, "NORMAL",   "#00cc44"),
]

# ─────────────────────────────────────────────────────────────────────────────
# Datenstrukturen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MilFlight:
    callsign:   str
    icao24:     str
    isr_type:   str
    isr_role:   str
    isr_note:   str
    confidence: str
    lat:        float
    lon:        float
    altitude_ft: int
    speed_kts:  int
    heading:    int
    zone:       str
    weight:     float          # Blockade-Relevanz-Gewicht


@dataclass
class BlockadeSignal:
    zone:         str
    display_name: str
    significance: str
    score:        float        # Roher Score
    level:        str          # KRITISCH / HOCH / MITTEL / NIEDRIG / NORMAL
    color:        str
    flights:      list[MilFlight]
    isr_count:    int
    tanker_count: int
    asw_count:    int
    types_seen:   list[str]
    alerts:       list[str]


@dataclass
class BlockadeAssessment:
    timestamp:   str
    zones:       list[BlockadeSignal]
    total_mil:   int
    hottest_zone: str
    overall_level: str
    overall_color: str
    summary:     str
    alerts:      list[str]


# ─────────────────────────────────────────────────────────────────────────────
# Hauptfunktion
# ─────────────────────────────────────────────────────────────────────────────

def _score_to_level(score: float) -> tuple[str, str]:
    for threshold, level, color in _SIGNAL_LEVELS:
        if score >= threshold:
            return level, color
    return "NORMAL", "#00cc44"


def assess_blockade_signals(zones: Optional[list[str]] = None) -> BlockadeAssessment:
    """
    Bewertet militärische Flugaktivität über strategischen Seegebieten
    als Blockade-Frühwarnsignal.

    Args:
        zones: Liste von Zonennamen aus MARITIME_ZONES.keys().
               None = alle 6 Zonen.

    Returns:
        BlockadeAssessment mit Bewertung per Zone und Gesamt-Score.
    """
    try:
        from nexus_flights import get_flights, _classify_isr  # type: ignore
    except ImportError:
        return _empty_assessment(zones or list(MARITIME_ZONES.keys()))

    if zones is None:
        zones = list(MARITIME_ZONES.keys())

    zone_signals: list[BlockadeSignal] = []
    all_alerts:   list[str]            = []
    total_mil     = 0

    for zone_key in zones:
        zone = MARITIME_ZONES.get(zone_key)
        if not zone:
            continue

        # Flugdaten holen
        result = get_flights(zone["query"], max_results=150)
        if not result or "error" in result:
            continue

        aircraft_list = result.get("aircraft", [])
        if not aircraft_list:
            continue

        mil_flights: list[MilFlight] = []
        tanker_count = 0
        asw_count    = 0
        types_seen:  list[str] = []
        raw_score    = 0.0

        for ac in aircraft_list:
            callsign = str(ac.get("callsign", "") or "").upper().strip()
            icao24   = str(ac.get("icao24", "") or ac.get("hex", "") or "")
            lat      = float(ac.get("lat", 0) or 0)
            lon      = float(ac.get("lon", 0) or 0)
            alt_ft   = int(ac.get("altitude_ft", 0) or ac.get("baro_altitude", 0) or 0)
            spd      = int(ac.get("speed_kts", 0) or ac.get("velocity", 0) or 0)
            hdg      = int(ac.get("heading", 0) or ac.get("true_track", 0) or 0)

            if not lat or not lon:
                continue

            # Geo-Filter: nur Flüge in dieser Zone
            if not (zone["lat_min"] <= lat <= zone["lat_max"] and
                    zone["lon_min"] <= lon <= zone["lon_max"]):
                continue

            clf = _classify_isr(callsign, icao24)
            if not clf.get("is_isr"):
                continue

            role  = clf.get("isr_role", "ISR")
            itype = clf.get("isr_type", "Unbekannt")
            note  = clf.get("isr_note", "")
            conf  = clf.get("confidence", "")

            weight = _ROLE_WEIGHTS.get(role, 1.0)

            # Typ-Tracking
            if itype not in types_seen:
                types_seen.append(itype)

            # Spezifika
            if "Tanker" in role:
                tanker_count += 1
            if "ASW" in role:
                asw_count += 1

            raw_score += weight

            mil_flights.append(MilFlight(
                callsign    = callsign or icao24,
                icao24      = icao24,
                isr_type    = itype,
                isr_role    = role,
                isr_note    = note,
                confidence  = conf,
                lat         = lat,
                lon         = lon,
                altitude_ft = alt_ft,
                speed_kts   = spd,
                heading     = hdg,
                zone        = zone_key,
                weight      = weight,
            ))

        total_mil += len(mil_flights)

        # Kombinations-Boni
        has_tanker = tanker_count > 0
        has_asw    = asw_count > 0
        has_sigint = any("SIGINT" in f.isr_role or "ELINT" in f.isr_role
                         for f in mil_flights)
        has_awacs  = any("AWACS" in f.isr_role for f in mil_flights)

        if has_asw and has_tanker:
            raw_score += 2.0   # Nachhaltige maritime Patrouille
        if has_sigint and has_asw:
            raw_score += 1.5   # Multi-ISR koordiniert
        if has_awacs and has_asw:
            raw_score += 2.0   # Luftschutz + Maritime Überwachung = Blockade-Eskorte
        if len(types_seen) >= 3:
            raw_score += 1.5   # Multi-Domain Surveillance

        # Threat-Multiplikator der Zone
        final_score = raw_score * zone.get("threat_multipler", 1.0)
        level, color = _score_to_level(final_score)

        # Zone-Alerts
        z_alerts: list[str] = []
        dname = zone["display"]
        if level in ("KRITISCH", "HOCH"):
            z_alerts.append(f"🔴 {dname}: {level} — {len(mil_flights)} Militärflüge")
            if has_asw and has_tanker:
                z_alerts.append(f"  → P-8 + Tanker = sustained maritime patrol")
            if has_sigint:
                z_alerts.append(f"  → RC-135/SIGINT aktiv über {dname}")
            if has_awacs:
                z_alerts.append(f"  → AWACS = Luftraumkontrolle gesetzt")
        elif level == "MITTEL":
            z_alerts.append(f"🟡 {dname}: {level} — {len(mil_flights)} ISR-Flüge")

        all_alerts.extend(z_alerts)

        zone_signals.append(BlockadeSignal(
            zone         = zone_key,
            display_name = dname,
            significance = zone["significance"],
            score        = round(final_score, 2),
            level        = level,
            color        = color,
            flights      = mil_flights,
            isr_count    = len(mil_flights),
            tanker_count = tanker_count,
            asw_count    = asw_count,
            types_seen   = types_seen,
            alerts       = z_alerts,
        ))

    # Gesamt-Bewertung
    zone_signals.sort(key=lambda z: z.score, reverse=True)
    max_score = zone_signals[0].score if zone_signals else 0.0
    hottest   = zone_signals[0].zone if zone_signals else ""
    overall_level, overall_color = _score_to_level(max_score)

    type_diversity = len({t for z in zone_signals for t in z.types_seen})
    parts = []
    total_isr    = sum(z.isr_count for z in zone_signals)
    total_tanker = sum(z.tanker_count for z in zone_signals)
    total_asw    = sum(z.asw_count for z in zone_signals)
    if total_isr:
        parts.append(f"{total_isr} ISR-Flüge")
    if total_tanker:
        parts.append(f"{total_tanker} Tanker")
    if total_asw:
        parts.append(f"{total_asw} ASW/P-8")
    if type_diversity:
        parts.append(f"{type_diversity} Typen")
    summary = ("Militärflüge: " + " | ".join(parts)) if parts else \
              "Keine militärischen Flüge in ADS-B sichtbar"
    if hottest and max_score > 2.5:
        summary += f" — Hotspot: {zone_signals[0].display_name}"

    return BlockadeAssessment(
        timestamp      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        zones          = zone_signals,
        total_mil      = total_mil,
        hottest_zone   = hottest,
        overall_level  = overall_level,
        overall_color  = overall_color,
        summary        = summary,
        alerts         = all_alerts,
    )


def _empty_assessment(zones: list[str]) -> BlockadeAssessment:
    return BlockadeAssessment(
        timestamp      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        zones          = [],
        total_mil      = 0,
        hottest_zone   = "",
        overall_level  = "NORMAL",
        overall_color  = "#00cc44",
        summary        = "Keine Flugdaten verfügbar",
        alerts         = [],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Karten-Marker
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_ICONS: dict[str, str] = {
    "ASW/ISR":    "🔱",   # P-8
    "ASW":        "🔱",
    "SIGINT":     "📡",   # RC-135
    "ELINT":      "📡",
    "AWACS":      "🔭",   # E-3
    "Tanker":     "⛽",   # KC-135
    "ISR-UAV":    "🛸",   # RQ-4
    "GMTI":       "🎯",   # JSTARS
    "C2":         "📶",
    "SIGINT/ASW": "📡",
}

_LEVEL_COLORS: dict[str, str] = {
    "KRITISCH": "#ff0000",
    "HOCH":     "#ff4400",
    "MITTEL":   "#ff8800",
    "NIEDRIG":  "#ffcc00",
    "NORMAL":   "#00cc44",
}


def milflights_for_map(zones: Optional[list[str]] = None) -> list[dict]:
    """
    Gibt Leaflet-kompatible Marker für alle Militärflüge zurück.
    """
    assessment = assess_blockade_signals(zones)
    markers    = []

    for zone_sig in assessment.zones:
        for f in zone_sig.flights:
            if not f.lat or not f.lon:
                continue
            icon  = _ROLE_ICONS.get(f.isr_role, "✈️")
            color = zone_sig.color
            pulse = "pulse" if zone_sig.level in ("KRITISCH", "HOCH") else None

            popup = (
                f"<b>{icon} {f.callsign}</b><br>"
                f"<b>Typ:</b> {f.isr_type}<br>"
                f"<b>Rolle:</b> {f.isr_role}<br>"
                f"<b>Hinweis:</b> {f.isr_note}<br>"
                f"<b>Zone:</b> {zone_sig.display_name}<br>"
                f"<b>Blockade-Signal:</b> "
                f"<span style='color:{color}'>{zone_sig.level}</span><br>"
                f"<b>Höhe:</b> {f.altitude_ft:,} ft"
                + (f"&nbsp;|&nbsp;<b>Speed:</b> {f.speed_kts} kts" if f.speed_kts else "")
                + f"<br><small style='color:#8ab0c8'>{f.isr_note}</small>"
            )

            markers.append({
                "lat":      f.lat,
                "lon":      f.lon,
                "type":     "milflights",
                "icon":     icon,
                "color":    color,
                "callsign": f.callsign,
                "role":     f.isr_role,
                "isr_type": f.isr_type,
                "zone":     f.zone,
                "level":    zone_sig.level,
                "title":    f"{icon} {f.callsign} [{f.isr_type}]",
                "popup":    popup,
                "source":   "ADS-B Exchange / ISR-Filter",
                "_pulse":   pulse is not None,
            })

    return markers


def milflights_summary() -> dict:
    """
    Kompaktes Summary für Dashboard und nexus_escalation.py.
    """
    a = assess_blockade_signals(
        zones=["Hormuz-Strasse", "Rotes Meer", "Taiwan", "Naher Osten",
               "Schwarzes Meer", "Ukraine"])
    return {
        "timestamp":      a.timestamp,
        "total_mil":      a.total_mil,
        "overall_level":  a.overall_level,
        "overall_color":  a.overall_color,
        "hottest_zone":   a.hottest_zone,
        "summary":        a.summary,
        "alerts":         a.alerts,
        "zones": [
            {
                "zone":         z.zone,
                "display":      z.display_name,
                "score":        z.score,
                "level":        z.level,
                "isr_count":    z.isr_count,
                "tanker_count": z.tanker_count,
                "asw_count":    z.asw_count,
                "types_seen":   z.types_seen,
            }
            for z in a.zones
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Eskalations-Beitrag (für nexus_escalation.py)
# ─────────────────────────────────────────────────────────────────────────────

def escalation_contribution(region: str = "Hormuz-Strasse") -> dict:
    """
    Gibt normalisierten Eskalations-Beitrag 0.0–1.0 zurück.
    Maximale Wirkung bei Score ≥ 10 → 1.0.
    """
    try:
        a = assess_blockade_signals(zones=[region])
        if not a.zones:
            return {"score_raw": 0, "contribution": 0.0, "level": "NORMAL",
                    "details": "Keine Daten"}
        z = a.zones[0]
        contribution = min(z.score / 10.0, 1.0)
        return {
            "score_raw":    z.score,
            "contribution": round(contribution, 3),
            "level":        z.level,
            "isr_count":    z.isr_count,
            "types":        z.types_seen,
            "details":      z.alerts[0] if z.alerts else f"{z.isr_count} ISR-Flüge",
        }
    except Exception:
        return {"score_raw": 0, "contribution": 0.0, "level": "NORMAL", "details": "Fehler"}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json as _json

    parser = argparse.ArgumentParser(description="NEXUS Militärflüge Blockade-Indikator")
    parser.add_argument("--zone", type=str, default=None,
                        help=f"Zonen (kommagetrennt). Verfügbar: {', '.join(MARITIME_ZONES)}")
    parser.add_argument("--json", action="store_true", help="JSON-Ausgabe")
    args = parser.parse_args()

    zones = [z.strip() for z in args.zone.split(",")] if args.zone else None

    print("[NEXUS MilFlights] Analyse Militärflüge...", file=sys.stderr)
    a = assess_blockade_signals(zones)

    if args.json:
        print(_json.dumps({
            "timestamp": a.timestamp,
            "overall_level": a.overall_level,
            "total_mil": a.total_mil,
            "hottest_zone": a.hottest_zone,
            "summary": a.summary,
            "alerts": a.alerts,
            "zones": [
                {"zone": z.zone, "score": z.score, "level": z.level,
                 "isr_count": z.isr_count, "tanker_count": z.tanker_count,
                 "types": z.types_seen, "alerts": z.alerts}
                for z in a.zones
            ],
        }, indent=2))
    else:
        print(f"\n=== Militärflüge Blockade-Assessment — {a.timestamp} ===")
        print(f"  Gesamt-Level:  {a.overall_level}")
        print(f"  Militärflüge: {a.total_mil}")
        print(f"  Hotspot:      {a.hottest_zone}")
        print()
        for z in a.zones:
            bar = "█" * min(int(z.score), 20)
            print(f"  {z.display_name:<30} {z.level:<10} Score:{z.score:5.1f} {bar}")
            print(f"    ISR:{z.isr_count} Tanker:{z.tanker_count} ASW:{z.asw_count}"
                  f" Typen:{','.join(z.types_seen[:3])}")
            for al in z.alerts:
                print(f"    {al}")
        print()
        for al in a.alerts:
            print(f"  {al}")
