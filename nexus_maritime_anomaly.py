"""
NEXUS - Maritime Anomaly Detection
====================================
Erkennt drei Klassen ungewöhnlicher Schiffsbewegungen aus AIS-Rohdaten:

  1. CHOKEPOINT-MONITOR
     Zählt Schiffe an 8 globalen Nadelöhren und misst Durchschnittsgeschwindigkeit.
     Alarm wenn Durchfluss < 60% oder Ø-Speed < 3 kn (= Stau/Blockade).

  2. SHIP-TO-SHIP TRANSFER (STS)
     Zwei Schiffe < 800m voneinander in offener See (kein Hafen/Ankerplatz).
     Indikator: Öltransfer, Sanktionsumgehung, Versorgung auf See.

  3. DARK RENDEZVOUS / UNEXPECTED STOP
     Schiff SOG < 0.5 kn außerhalb bekannter Hafen- und Ankerzonen.
     Indikator: Wartet auf Rendezvous, Güterübergabe, Spionage.

Kalibrierung:
  STS_RADIUS_M          = 800    (Meter Mindestabstand für STS-Alert)
  STOP_SPEED_KN         = 0.5   (Knoten Schwellenwert für "gestoppt")
  PORT_EXCLUSION_RADIUS = 0.15  (Grad ~16km – kein Alert in Hafennähe)
  CHOKE_MIN_SHIPS       = 2     (Mindest-Schiffe in Chokepoint-Box für Analyse)
  CHOKE_SLOW_KN         = 4.0   (Unter dieser Ø-Speed: Stau-Alarm)
"""

from __future__ import annotations

import math
from typing import Optional


# ── Konfiguration ─────────────────────────────────────────────────────────────

STS_RADIUS_M          = 800     # Ship-to-Ship: Abstand in Metern
STOP_SPEED_KN         = 0.5     # Als "gestoppt" gewertet
PORT_EXCLUSION_RADIUS = 0.15    # ~16 km um jeden Hafen: kein Stop-Alert
CHOKE_MIN_SHIPS       = 2       # Mindest-Schiffe für Chokepoint-Analyse
CHOKE_SLOW_KN         = 4.0     # Ø-Speed unter diesem Wert → Stau-Verdacht
CHOKE_BLOCK_KN        = 1.5     # Ø-Speed unter diesem Wert → Blockade-Alarm


# ── Bekannte Häfen & Ankerplätze (lat, lon) ──────────────────────────────────
# Schiffe die hier stoppen sind nicht verdächtig

_KNOWN_PORTS: list[tuple[float, float, str]] = [
    # Persischer Golf
    (26.19,  56.27, "Bandar Abbas"),
    (25.28,  56.36, "Khasab"),
    (27.13,  56.08, "Qeshm"),
    (27.96,  50.83, "Bushehr"),
    (30.43,  48.17, "Abadan"),
    (29.86,  48.80, "Basra"),
    (29.37,  47.98, "Kuwait City"),
    (26.21,  50.60, "Manama/Sitra"),
    (24.47,  54.37, "Abu Dhabi KIZAD"),
    (25.28,  55.30, "Dubai Jebel Ali"),
    (25.30,  55.32, "Dubai Port Rashid"),
    (22.62,  59.57, "Salalah"),
    (23.62,  58.58, "Muscat"),
    # Rotes Meer / Bab-el-Mandeb
    (12.79,  44.99, "Aden"),
    (15.61,  32.55, "Port Sudan"),
    (21.49,  39.18, "Jeddah"),
    (29.93,  32.55, "Suez"),
    (29.87,  32.55, "Port Said"),
    # Mittelmeer
    (37.94,  23.63, "Piräus"),
    (40.99,  28.82, "Istanbul Haydarpaşa"),
    (35.92,  14.49, "Malta Valletta"),
    (43.30,   5.37, "Marseille"),
    (41.37,   2.15, "Barcelona"),
    (37.00,  15.29, "Augusta"),
    (40.64,  14.28, "Neapel"),
    (44.41,   8.92, "Genua"),
    # Atlantik / Nordsee
    (51.95,   4.14, "Rotterdam"),
    (51.26,   4.39, "Antwerpen"),
    (53.55,  10.00, "Hamburg"),
    (57.01,   9.88, "Frederikshavn"),
    (55.67,  12.56, "Kopenhagen"),
    (56.04,  12.69, "Helsingborg"),
    # Asien
    (1.29,  103.85, "Singapur"),
    (22.29,  114.16, "Hongkong"),
    (31.23,  121.47, "Shanghai"),
    (35.09,  129.04, "Busan"),
    (34.68,  135.18, "Osaka Kobe"),
    (35.44,  139.66, "Tokio Yokohama"),
    # Ostafrika
    (-4.06,  39.67, "Mombasa"),
    (-25.89,  32.90, "Maputo"),
    (-33.90,  18.42, "Kapstadt"),
    # Amerika
    (40.69, -74.04, "New York"),
    (29.94, -90.08, "New Orleans"),
    (25.77, -80.19, "Miami"),
    (10.62, -61.52, "Port of Spain"),
    (-23.00, -43.17, "Rio de Janeiro"),
    (-33.45, -70.65, "Valparaíso"),
    (-34.91, -56.18, "Montevideo"),
]


# ── Globale Nadelöhre (Chokepoints) ──────────────────────────────────────────

_CHOKEPOINTS: list[dict] = [
    {
        "name":    "Straße von Hormuz",
        "short":   "Hormuz",
        "bbox":    (25.8, 56.2, 27.0, 57.8),   # lat_min, lon_min, lat_max, lon_max
        "normal_ships": 20,                      # Erwartete Schiffszahl
        "icon":    "⛽",
    },
    {
        "name":    "Suezkanal",
        "short":   "Suez",
        "bbox":    (29.9, 32.3, 31.3, 32.7),
        "normal_ships": 15,
        "icon":    "🚢",
    },
    {
        "name":    "Bab-el-Mandeb",
        "short":   "Bab-el-Mandeb",
        "bbox":    (11.8, 43.1, 13.0, 44.5),
        "normal_ships": 10,
        "icon":    "⚠",
    },
    {
        "name":    "Straße von Malakka",
        "short":   "Malakka",
        "bbox":    (1.0, 103.0, 6.0, 104.5),
        "normal_ships": 30,
        "icon":    "🚢",
    },
    {
        "name":    "Bosporus",
        "short":   "Bosporus",
        "bbox":    (40.9, 28.9, 41.2, 29.2),
        "normal_ships": 8,
        "icon":    "🚢",
    },
    {
        "name":    "Straße von Gibraltar",
        "short":   "Gibraltar",
        "bbox":    (35.7, -5.8, 36.2, -5.2),
        "normal_ships": 12,
        "icon":    "🚢",
    },
    {
        "name":    "Taiwanstraße",
        "short":   "Taiwan",
        "bbox":    (22.5, 119.5, 25.5, 121.0),
        "normal_ships": 15,
        "icon":    "⚠",
    },
    {
        "name":    "Straße von Maluku",
        "short":   "Lombok",
        "bbox":    (-8.8, 115.8, -8.3, 116.3),
        "normal_ships": 6,
        "icon":    "🚢",
    },
]


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanz in Metern zwischen zwei GPS-Koordinaten."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _near_port(lat: float, lon: float) -> Optional[str]:
    """Gibt Hafenname zurück wenn Koordinate in PORT_EXCLUSION_RADIUS liegt."""
    for plat, plon, pname in _KNOWN_PORTS:
        dist_deg = math.sqrt((lat - plat) ** 2 + (lon - plon) ** 2)
        if dist_deg <= PORT_EXCLUSION_RADIUS:
            return pname
    return None


def _in_bbox(lat: float, lon: float, bbox: tuple) -> bool:
    lat_min, lon_min, lat_max, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _ship_type_label(t: str) -> str:
    t = (t or "").lower()
    if "tanker" in t:       return "🛢 Tanker"
    if "cargo" in t:        return "📦 Frachter"
    if "container" in t:    return "📦 Container"
    if "naval" in t or "military" in t or "warship" in t: return "⚔ Kriegsschiff"
    if "fishing" in t:      return "🎣 Fischer"
    if "passenger" in t:    return "👥 Passagier"
    return "🚢 Unbekannt"


# ── 1. Chokepoint-Monitor ─────────────────────────────────────────────────────

def check_chokepoints(vessels: list[dict]) -> list[dict]:
    """
    Analysiert alle 8 Nadelöhre und gibt Alerts zurück.

    Returns:
        Liste von Dicts mit:
        {name, short, ships_count, avg_speed_kn, status, alert_level, vessels_in_zone, ...}
    """
    results = []
    for cp in _CHOKEPOINTS:
        in_zone = [v for v in vessels
                   if v.get("lat") and v.get("lon")
                   and _in_bbox(float(v["lat"]), float(v["lon"]), cp["bbox"])]

        if not in_zone:
            # Keine Schiffe sichtbar – könnte AIS-Lücke sein, kein Alert
            results.append({
                "name":          cp["name"],
                "short":         cp["short"],
                "icon":          cp["icon"],
                "ships_count":   0,
                "avg_speed_kn":  None,
                "status":        "KEINE DATEN",
                "alert_level":   "info",
                "bbox":          cp["bbox"],
                "vessels_in_zone": [],
            })
            continue

        speeds = [float(v.get("speed_kn", v.get("speed", 0)) or 0)
                  for v in in_zone]
        avg_speed = sum(speeds) / len(speeds) if speeds else 0

        # Alert-Klassifikation
        if avg_speed <= CHOKE_BLOCK_KN and len(in_zone) >= CHOKE_MIN_SHIPS:
            status      = f"🔴 BLOCKADE – {len(in_zone)} Schiffe gestoppt (Ø {avg_speed:.1f} kn)"
            alert_level = "critical"
        elif avg_speed <= CHOKE_SLOW_KN and len(in_zone) >= CHOKE_MIN_SHIPS:
            status      = f"🟠 STAU – {len(in_zone)} Schiffe langsam (Ø {avg_speed:.1f} kn)"
            alert_level = "warning"
        elif len(in_zone) == 0:
            status      = "⚪ LEER – kein Schiffsverkehr detektiert"
            alert_level = "warning"
        else:
            status      = f"🟢 NORMAL – {len(in_zone)} Schiffe (Ø {avg_speed:.1f} kn)"
            alert_level = "ok"

        results.append({
            "name":          cp["name"],
            "short":         cp["short"],
            "icon":          cp["icon"],
            "ships_count":   len(in_zone),
            "avg_speed_kn":  round(avg_speed, 1),
            "status":        status,
            "alert_level":   alert_level,
            "bbox":          cp["bbox"],
            "vessels_in_zone": [
                {
                    "name":     v.get("name", "?"),
                    "flag":     v.get("flag", ""),
                    "type":     v.get("type", ""),
                    "speed_kn": float(v.get("speed_kn", v.get("speed", 0)) or 0),
                    "lat":      float(v["lat"]),
                    "lon":      float(v["lon"]),
                }
                for v in in_zone
            ],
        })
    return results


# ── 2. Ship-to-Ship Transfer Detection ───────────────────────────────────────

def check_sts_proximity(vessels: list[dict]) -> list[dict]:
    """
    Findet Schiffspaare die sich ungewöhnlich nahe sind (STS-Transfer Verdacht).

    Returns:
        Liste von Dicts mit: ship_a, ship_b, distance_m, lat, lon, alert_type
    """
    alerts = []
    valid = [v for v in vessels if v.get("lat") and v.get("lon")]

    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            v1, v2 = valid[i], valid[j]
            lat1, lon1 = float(v1["lat"]), float(v1["lon"])
            lat2, lon2 = float(v2["lat"]), float(v2["lon"])

            dist_m = _haversine_m(lat1, lon1, lat2, lon2)
            if dist_m > STS_RADIUS_M:
                continue

            # Hafennähe ausschließen
            port1 = _near_port(lat1, lon1)
            port2 = _near_port(lat2, lon2)
            if port1 or port2:
                continue

            # Durchschnittsposition
            mid_lat = (lat1 + lat2) / 2
            mid_lon = (lon1 + lon2) / 2

            # Speed beider Schiffe
            s1 = float(v1.get("speed_kn", v1.get("speed", 0)) or 0)
            s2 = float(v2.get("speed_kn", v2.get("speed", 0)) or 0)
            both_slow = s1 < 3.0 and s2 < 3.0

            # Typ-Hinweis
            t1 = _ship_type_label(v1.get("type", ""))
            t2 = _ship_type_label(v2.get("type", ""))

            if both_slow:
                title    = f"⛽ STS-Transfer: {v1.get('name','?')} ↔ {v2.get('name','?')}"
                severity = "critical"
                summary  = (f"Beide Schiffe gestoppt ({s1:.1f}/{s2:.1f} kn) "
                            f"auf offener See – {dist_m:.0f}m Abstand")
            else:
                title    = f"🔍 Nahkontakt: {v1.get('name','?')} ↔ {v2.get('name','?')}"
                severity = "warning"
                summary  = (f"Ungewöhnliche Nähe ({dist_m:.0f}m) auf offener See, "
                            f"Speed: {s1:.1f}/{s2:.1f} kn")

            alerts.append({
                "title":       title,
                "summary":     summary,
                "severity":    severity,
                "distance_m":  round(dist_m),
                "lat":         round(mid_lat, 4),
                "lon":         round(mid_lon, 4),
                "ship_a": {
                    "name":  v1.get("name", "?"),
                    "flag":  v1.get("flag", ""),
                    "type":  t1,
                    "mmsi":  v1.get("mmsi", ""),
                    "speed": s1,
                    "lat":   lat1,
                    "lon":   lon1,
                },
                "ship_b": {
                    "name":  v2.get("name", "?"),
                    "flag":  v2.get("flag", ""),
                    "type":  t2,
                    "mmsi":  v2.get("mmsi", ""),
                    "speed": s2,
                    "lat":   lat2,
                    "lon":   lon2,
                },
            })

    return alerts


# ── 3. Unexpected Stop / Dark Rendezvous ─────────────────────────────────────

def check_unexpected_stops(vessels: list[dict]) -> list[dict]:
    """
    Findet Schiffe die auf offener See gestoppt haben (kein Hafen, kein Ankerplatz).

    Returns:
        Liste von Dicts mit: name, lat, lon, speed_kn, summary, severity
    """
    alerts = []
    for v in vessels:
        lat = v.get("lat")
        lon = v.get("lon")
        if not lat or not lon:
            continue

        lat, lon = float(lat), float(lon)
        speed = float(v.get("speed_kn", v.get("speed", 0)) or 0)

        if speed > STOP_SPEED_KN:
            continue  # Schiff fährt – kein Alert

        # Hafennähe ausschließen
        port = _near_port(lat, lon)
        if port:
            continue  # Normales Ankern im Hafen

        # Chokepoint-Nähe (dort ankern manchmal Schiffe auf Warteposition)
        in_cp = any(_in_bbox(lat, lon, cp["bbox"]) for cp in _CHOKEPOINTS)

        ship_type = _ship_type_label(v.get("type", ""))
        name = v.get("name", "Unbekannt")
        flag = v.get("flag", "")

        if in_cp:
            title    = f"🟠 CHOKEPOINT-STOP: {name} {flag}"
            summary  = (f"{ship_type} stoppt an Nadelöhr – "
                        f"SOG {speed:.1f} kn | mgl. Blockade oder Warteschlange")
            severity = "warning"
        else:
            title    = f"🔴 DARK RENDEZVOUS: {name} {flag}"
            summary  = (f"{ship_type} gestoppt in offenem Meer – "
                        f"SOG {speed:.1f} kn | mgl. STS-Transfer, Übergabe oder Spionage")
            severity = "critical"

        alerts.append({
            "title":    title,
            "summary":  summary,
            "severity": severity,
            "name":     name,
            "flag":     flag,
            "type":     ship_type,
            "mmsi":     v.get("mmsi", ""),
            "speed_kn": speed,
            "lat":      round(lat, 4),
            "lon":      round(lon, 4),
            "in_chokepoint": in_cp,
        })

    return alerts


# ── Haupt-Analyse: alle drei Detektoren kombiniert ────────────────────────────

def analyse_vessels(vessels: list[dict]) -> dict:
    """
    Führt alle drei Anomalie-Detektoren aus und gibt konsolidiertes Ergebnis zurück.

    Args:
        vessels: Liste von Schiffs-Dicts aus nexus_ais.get_vessels()

    Returns:
        {
            "chokepoints":  [...],   # Chokepoint-Status aller 8 Engstellen
            "sts_alerts":   [...],   # Ship-to-Ship Transfer Verdacht
            "stop_alerts":  [...],   # Unerwartete Stopps auf offener See
            "total_alerts": int,     # Summe kritischer Alerts
            "summary":      str,     # Kurztext für Statuszeile
        }
    """
    choke  = check_chokepoints(vessels)
    sts    = check_sts_proximity(vessels)
    stops  = check_unexpected_stops(vessels)

    critical = (
        sum(1 for c in choke if c["alert_level"] == "critical") +
        sum(1 for s in sts   if s["severity"]    == "critical") +
        sum(1 for s in stops if s["severity"]    == "critical")
    )
    warnings = (
        sum(1 for c in choke if c["alert_level"] == "warning") +
        sum(1 for s in sts   if s["severity"]    == "warning") +
        sum(1 for s in stops if s["severity"]    == "warning")
    )

    if critical > 0:
        summary = f"🔴 {critical} KRITISCH | {warnings} Warnungen"
    elif warnings > 0:
        summary = f"🟠 {warnings} maritime Anomalien erkannt"
    else:
        summary = "🟢 Keine maritimen Anomalien"

    return {
        "chokepoints":  choke,
        "sts_alerts":   sts,
        "stop_alerts":  stops,
        "total_alerts": critical + warnings,
        "critical":     critical,
        "warnings":     warnings,
        "summary":      summary,
    }


def anomaly_text_summary(result: dict) -> str:
    """Formatierter Text für Lagebild / LLM-Kontext."""
    lines = []

    # Chokepoints mit Problemen
    for cp in result.get("chokepoints", []):
        if cp["alert_level"] in ("critical", "warning"):
            lines.append(f"  [{cp['short']}] {cp['status']}")

    # STS-Alerts
    for a in result.get("sts_alerts", []):
        lines.append(f"  {a['title']} – {a['summary']}")

    # Stop-Alerts
    for a in result.get("stop_alerts", []):
        lines.append(f"  {a['title']} – {a['summary']}")

    if not lines:
        return ""
    return "[MARITIME ANOMALIEN]\n" + "\n".join(lines)


# ── Direkt-Test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("NEXUS Maritime Anomaly Detection – Selbsttest\n")

    # Simulierte Schiffe: STS-Szenario im Persischen Golf
    test_vessels = [
        # STS-Paar auf offener See (Hormuz)
        {"name": "ATLAS",       "lat": 26.40, "lon": 56.90, "speed_kn": 0.3,
         "flag": "🇮🇷", "type": "Tanker",  "mmsi": "422001000"},
        {"name": "SHADOW_1",    "lat": 26.40, "lon": 56.902,"speed_kn": 0.2,
         "flag": "🇵🇦", "type": "Tanker",  "mmsi": "352001000"},

        # Unexpected Stop – offenes Meer, kein Hafen
        {"name": "GHOST_VESSEL","lat": 24.50, "lon": 58.10, "speed_kn": 0.0,
         "flag": "🏴", "type": "Cargo",   "mmsi": "000000001"},

        # Chokepoint Hormuz – langsam
        {"name": "NORMAL_1",    "lat": 26.55, "lon": 56.80, "speed_kn": 9.5,
         "flag": "🇸🇦", "type": "Tanker",  "mmsi": "403001000"},
        {"name": "SLOW_1",      "lat": 26.48, "lon": 56.85, "speed_kn": 1.8,
         "flag": "🇮🇳", "type": "Cargo",   "mmsi": "419001000"},
        {"name": "SLOW_2",      "lat": 26.52, "lon": 56.82, "speed_kn": 2.1,
         "flag": "🇨🇳", "type": "Tanker",  "mmsi": "412001000"},
    ]

    result = analyse_vessels(test_vessels)
    print(f"Gesamtstatus: {result['summary']}\n")

    print("── Chokepoints ──")
    for cp in result["chokepoints"]:
        if cp["ships_count"] > 0 or cp["alert_level"] != "info":
            print(f"  {cp['icon']} {cp['name']}: {cp['status']}")

    print("\n── STS-Alerts ──")
    for a in result["sts_alerts"]:
        print(f"  {a['title']}")
        print(f"    {a['summary']}")

    print("\n── Unexpected Stops ──")
    for a in result["stop_alerts"]:
        print(f"  {a['title']}")
        print(f"    {a['summary']}")
