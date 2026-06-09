"""
NEXUS – Multi-Signal Fusion Engine  (Ebene 4 / Modul 4.5)
==========================================================
Fusioniert alle OSINT-Signalquellen räumlich und zeitlich zu
bewerteten Threat-Assessments:

  • "FIRMS-Feuer + AIS-Lücke + Seismik in 15 km → Seekriegsoperation"
  • "ISR-Flieger + HUMINT-Kontakt + GDELT-Event → Luft-/Bodenangriff"
  • "GPS-Jammer + NOTAM + Radar-Ausfall → Aktiver Luftkampf"
  • "Mehrfach-HUMINT + Blitz-Surge + Artillerie → Schwerer Beschuss"

Öffentliche API:
  fuse_signals(data, region)  → list[ThreatAssessment]
  fusion_for_map(data, region)→ list[dict]   (Karten-Marker)
  fusion_summary(assessments) → str           (LLM-Kontext)
  fusion_context(data, region)→ str           (Direkt für Pipeline)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Konstanten
# ─────────────────────────────────────────────────────────────────────────────

CLUSTER_RADIUS_KM = 25.0    # Räumlicher Fusionsradius
TIME_WINDOW_H     = 6.0     # Zeitfenster für Korrelation (Stunden)

# Mindest-Konfidenz für Ausgabe
MIN_CONFIDENCE    = 0.40


# ─────────────────────────────────────────────────────────────────────────────
# Cross-Validation zwischen Quellen (T177)
# ─────────────────────────────────────────────────────────────────────────────
# Kernidee: Ein einzelnes Ereignis, das von MEHREREN UNABHÄNGIGEN
# Erfassungs-METHODEN bestätigt wird (z.B. Satellitenbild + physikalischer
# Sensor + Augenzeugenbericht), ist deutlich glaubwürdiger als dasselbe
# Ereignis, das nur über mehrere Kanäle DERSELBEN Methode auftaucht
# (z.B. GDELT + ACLED berichten beide nur "was in den Medien steht" – das
# ist Mehrfach-BERICHTERSTATTUNG, keine unabhängige BESTÄTIGUNG).
#
# Diese Gruppierung ordnet jeden Signal-Typ einer "Erfassungs-Familie" zu.
# Stammen die Signale eines Clusters aus ≥2 unterschiedlichen Familien,
# gilt das Ereignis als CROSS-VALIDIERT (Konfidenz-Bonus). Stammen sie nur
# aus EINER Familie, bleibt es UNBESTÄTIGT-EINZELQUELLE (leichter Abschlag +
# expliziter Hinweis im Label/Report, analog zu nexus_confidence.py-Prinzip
# "Mehrfachnennung ≠ Bestätigung").
_SOURCE_FAMILIES: dict[str, set[str]] = {
    # Physikalische Sensoren – messen reale physische Ereignisse direkt,
    # unabhängig von menschlicher Berichterstattung
    "physikalische_sensoren": {
        "seismic", "lightning_spike", "radiation_spike", "gpsjam", "hf_activity",
    },
    # Satelliten-/Kamera-Bildgebung – visuelle Direktbeobachtung
    "satelliten_bildgebung": {
        "fire", "sar_anomaly", "vehicle_spotted", "webcam_motion",
    },
    # Transponder-/Bewegungsdaten – elektronische Signaturen von Fahrzeugen/Schiffen/Flugzeugen
    "transponder_bewegung": {
        "ais_gap", "isr_aircraft", "maritime", "movement_anomaly", "convoy_detected",
    },
    # Berichte/Medien/HUMINT – menschliche Beobachtung & Medienberichterstattung
    # (gdelt+acled zählen bewusst zur SELBEN Familie: beide aggregieren primär
    # Medienberichte, sind also keine unabhängige Bestätigung füreinander)
    "berichte_humint": {
        "gdelt", "acled", "humint_contact", "humint_drone",
        "netgraph_surge", "notam", "iaea_alert",
    },
}


def _signal_family(sig_type: str) -> str:
    """Ordnet einen Signal-Typ seiner Erfassungs-Familie zu (für Cross-Validation)."""
    for family, members in _SOURCE_FAMILIES.items():
        if sig_type in members:
            return family
    return "sonstige"


def _cross_validate(present_types: set[str]) -> dict[str, Any]:
    """
    Bewertet, ob ein Signal-Set aus mehreren UNABHÄNGIGEN Erfassungs-Familien
    stammt (= echte Cross-Validation) oder nur aus einer (= Mehrfachnennung
    ohne unabhängige Bestätigung).

    Rückgabe:
      families       – Liste der beteiligten Familien (sortiert)
      validated      – True wenn ≥2 unabhängige Familien vorhanden
      bonus          – Konfidenz-Multiplikator (>1.0 bei Validierung, <1.0 ohne)
      note           – Deutschsprachiger Hinweistext fürs Label/Report
    """
    families = sorted({_signal_family(t) for t in present_types})
    n = len(families)

    if n >= 3:
        return {"families": families, "validated": True, "bonus": 1.20,
                "note": f"✅ Cross-validiert durch {n} unabhängige Quell-Familien "
                        f"({', '.join(families)})"}
    if n == 2:
        return {"families": families, "validated": True, "bonus": 1.10,
                "note": f"✅ Cross-validiert durch 2 unabhängige Quell-Familien "
                        f"({', '.join(families)})"}
    if n == 1:
        return {"families": families, "validated": False, "bonus": 0.90,
                "note": f"⚠ Unbestätigt – alle Signale stammen aus derselben "
                        f"Quell-Familie ({families[0]}); unabhängige Bestätigung "
                        f"durch andere Erfassungsmethode fehlt noch"}
    return {"families": [], "validated": False, "bonus": 1.0, "note": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Threat-Patterns  (jedes Pattern: Name, Signale, Konfidenz-Bonus, Beschreibung)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    name:        str
    signals:     list[str]   # Signaltypen die vorhanden sein müssen
    confidence:  float       # Basis-Konfidenz wenn alle vorhanden
    label_de:    str         # Deutsches Label für Karte/Report
    icon:        str
    severity:    str         # KRITISCH | HOCH | MITTEL | NIEDRIG
    description: str


PATTERNS: list[Pattern] = [
    # ── Neue Ebene-4-Patterns ──────────────────────────────────────────────────
    Pattern(
        name="ground_convoy",
        signals=["vehicle_spotted", "movement_anomaly", "humint_contact"],
        confidence=0.80,
        label_de="🚛 Konvoi-Bewegung erkannt",
        icon="🚛🔍",
        severity="HOCH",
        description="Bildanalyse (Fahrzeuge) + Verkehrsanomalie + HUMINT → "
                    "Militärischer Konvoi oder Truppenbewegung detektiert.",
    ),
    Pattern(
        name="nuclear_incident",
        signals=["radiation_spike", "seismic", "iaea_alert"],
        confidence=0.90,
        label_de="☢ Nuklearer Vorfall",
        icon="☢️⚠",
        severity="KRITISCH",
        description="Strahlungsspitze + Seismik + IAEA-Meldung → "
                    "Möglicher nuklearer Zwischenfall oder Reaktor-Anomalie.",
    ),
    Pattern(
        name="nuclear_radiation_spike",
        signals=["radiation_spike", "iaea_alert"],
        confidence=0.75,
        label_de="☢ Strahlungs-Anomalie",
        icon="☢️📡",
        severity="KRITISCH",
        description="Erhöhte Strahlungsmesswerte + IAEA-Meldung → "
                    "Radiologischer Vorfall, Überprüfung erforderlich.",
    ),
    Pattern(
        name="pre_strike_signature",
        signals=["isr_aircraft", "hf_activity", "gpsjam"],
        confidence=0.78,
        label_de="⚡ Vorbereitende Angriffssignatur",
        icon="⚡📡",
        severity="KRITISCH",
        description="ISR-Aktivität + erhöhte HF-Kommunikation + GPS-Störung → "
                    "Klassische Signatur vor einem Präzisionsangriff.",
    ),
    Pattern(
        name="convoy_ambush_risk",
        signals=["vehicle_spotted", "fire", "acled"],
        confidence=0.72,
        label_de="🚛 Konvoi-Angriff",
        icon="🚛💥",
        severity="HOCH",
        description="Fahrzeugsichtung (Bild-KI) + FIRMS-Brand + ACLED-Kampfereignis → "
                    "Möglicher Hinterhalt oder Konvoi-Beschuss.",
    ),
    Pattern(
        name="webcam_nocturnal_activity",
        signals=["webcam_motion", "humint_contact", "gdelt"],
        confidence=0.68,
        label_de="📹 Nächtliche Militäraktivität",
        icon="📹🌙",
        severity="MITTEL",
        description="Kamera-Bewegungsalarm (nachts) + HUMINT + GDELT → "
                    "Verdächtige Aktivität im Dunkelbereich.",
    ),
    Pattern(
        name="info_warfare_surge",
        signals=["netgraph_surge", "gdelt", "humint_contact"],
        confidence=0.65,
        label_de="📣 Informationskriegs-Surge",
        icon="📣⚡",
        severity="MITTEL",
        description="Koordinierter Nachrichtensurge + GDELT-Event + HUMINT → "
                    "Mögliche Informationsoperation begleitend zu physischem Angriff.",
    ),
    Pattern(
        name="sar_vehicle_concentration",
        signals=["sar_anomaly", "vehicle_spotted", "movement_anomaly"],
        confidence=0.72,
        label_de="🛰 Fahrzeugkonzentration (SAR+Vision)",
        icon="🛰🚛",
        severity="HOCH",
        description="Sentinel-1 SAR-Anomalie + Fahrzeugbild-Analyse + Verkehrsanomalie → "
                    "Erhöhte Metallsignatur deutet auf Fahrzeugsammlung hin.",
    ),
    # ── Bestehende Patterns ────────────────────────────────────────────────────
    Pattern(
        name="naval_strike",
        signals=["fire", "ais_gap", "seismic"],
        confidence=0.85,
        label_de="⚓ Seekriegsoperation",
        icon="🚢💥",
        severity="KRITISCH",
        description="FIRMS-Feuer + AIS-Lücke + Seismik-Anomalie im selben Gebiet → "
                    "Möglicher Angriff auf Seestreitkräfte oder Hafen.",
    ),
    Pattern(
        name="airstrike",
        signals=["isr_aircraft", "fire", "humint_contact"],
        confidence=0.80,
        label_de="✈ Luftangriff",
        icon="✈️💥",
        severity="KRITISCH",
        description="ISR-Flugzeug + FIRMS-Feuer + HUMINT-Kontaktmeldung → "
                    "Wahrscheinlicher Luftangriff / Präzisionsschlag.",
    ),
    Pattern(
        name="artillery_barrage",
        signals=["humint_contact", "lightning_spike", "seismic"],
        confidence=0.75,
        label_de="💣 Schwerer Artilleriebeschuss",
        icon="💣⚡",
        severity="KRITISCH",
        description="HUMINT-Kontakt + Blitz-Signatur + Seismik → "
                    "Artillerie-Einsatz mit elektromagnetischer Signatur.",
    ),
    Pattern(
        name="drone_strike",
        signals=["humint_drone", "fire", "gdelt"],
        confidence=0.75,
        label_de="🛸 Drohnenangriff",
        icon="🛸💥",
        severity="HOCH",
        description="HUMINT-Drohnenmeldung + FIRMS-Feuer + GDELT-Event → "
                    "Drohnenangriff mit bestätigtem Brand.",
    ),
    Pattern(
        name="air_defense_active",
        signals=["gpsjam", "notam", "isr_aircraft"],
        confidence=0.70,
        label_de="🔺 Aktives Luftverteidigungssystem",
        icon="🔺📡",
        severity="HOCH",
        description="GPS-Jammer + NOTAM-Sperrgebiet + ISR-Aktivität → "
                    "Aktives Luftverteidigungssystem oder Luftkampf.",
    ),
    Pattern(
        name="ground_offensive",
        signals=["humint_contact", "acled", "gdelt"],
        confidence=0.70,
        label_de="⚡ Bodenoffensive",
        icon="⚡🗺",
        severity="HOCH",
        description="HUMINT-Bodenkontakt + ACLED-Kampfereignis + GDELT → "
                    "Koordinierter Bodenangriff oder Gegenoffensive.",
    ),
    Pattern(
        name="logistics_disruption",
        signals=["fire", "gdelt", "acled"],
        confidence=0.60,
        label_de="🔥 Logistik-/Infrastrukturangriff",
        icon="🔥🏭",
        severity="MITTEL",
        description="FIRMS-Feuer + GDELT-Infrastrukturereignis + ACLED → "
                    "Möglicher Angriff auf Logistik oder zivile Infrastruktur.",
    ),
    Pattern(
        name="naval_blockade",
        signals=["ais_gap", "gdelt", "seismic"],
        confidence=0.60,
        label_de="⚓ Seeblockade / Minenfeld",
        icon="⚓🚫",
        severity="MITTEL",
        description="AIS-Lücke + GDELT-Seeereignis + Seismik → "
                    "Mögliche Seeblockade oder Mineneinsatz.",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Geo-Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _centroid(points: list[tuple[float,float]]) -> tuple[float,float]:
    if not points:
        return (0.0, 0.0)
    return (
        round(sum(p[0] for p in points) / len(points), 4),
        round(sum(p[1] for p in points) / len(points), 4),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Signal-Normalisierung
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_signals(data: dict) -> list[dict]:
    """
    Flacht alle Signal-Quellen auf eine einheitliche Liste ab:
    [{type, lat, lon, ts, meta}, …]
    """
    now = time.time()
    signals: list[dict] = []
    cutoff = now - TIME_WINDOW_H * 3600

    # ── FIRMS Brände ──────────────────────────────────────────────────────────
    for f in (data.get("fires") or data.get("fire_points") or []):
        lat, lon = f.get("lat"), f.get("lon")
        if lat and lon:
            signals.append({"type":"fire","lat":lat,"lon":lon,
                             "ts": now, "meta": f})

    # ── AIS-Lücken (Schiffe die aus AIS verschwunden) ─────────────────────────
    md = data.get("maritime_data") or data.get("maritime") or {}
    for alert in (md.get("alerts") or []):
        lat, lon = alert.get("lat"), alert.get("lon")
        if lat and lon:
            atype = alert.get("alert_type","").lower()
            signals.append({"type":"ais_gap" if "ais" in atype or "dark" in atype else "maritime",
                             "lat":lat,"lon":lon, "ts":now, "meta":alert})
    # Maritime Regions mit vielen Alerts → als ais_gap werten wenn Titel "dark/AIS" enthält
    for pt in (data.get("maritime_points") or []):
        if pt.get("alerts", 0) > 0:
            titles_str = " ".join(pt.get("alert_titles") or []).lower()
            sig_type = "ais_gap" if ("ais" in titles_str or "dark" in titles_str or "gap" in titles_str) else "maritime"
            signals.append({"type": sig_type, "lat": pt["lat"], "lon": pt["lon"],
                             "ts": now, "meta": pt})

    # ── Seismik ───────────────────────────────────────────────────────────────
    for q in (data.get("earthquakes") or []):
        lat, lon = q.get("lat"), q.get("lon")
        if lat and lon and q.get("osint_hint"):
            signals.append({"type":"seismic","lat":lat,"lon":lon,
                             "ts":now,"meta":q})

    # ── ISR-Flugzeuge ─────────────────────────────────────────────────────────
    for ac in (data.get("aircraft") or []):
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat and lon and ac.get("suspicious"):
            susp = ac.get("suspicious","").lower()
            t = "isr_aircraft"
            signals.append({"type":t,"lat":lat,"lon":lon,"ts":now,"meta":ac})

    # ── HUMINT ────────────────────────────────────────────────────────────────
    for h in (data.get("humint_markers") or []):
        lat, lon = h.get("lat"), h.get("lon")
        if lat and lon:
            wcat = h.get("weapon_cat","")
            t = "humint_drone" if wcat == "drone" else "humint_contact"
            signals.append({"type":t,"lat":lat,"lon":lon,"ts":now,"meta":h})

    # ── GDELT Geo-Events ──────────────────────────────────────────────────────
    for g in (data.get("gdelt_points") or data.get("gdelt") or []):
        lat, lon = g.get("lat"), g.get("lon")
        if lat and lon:
            signals.append({"type":"gdelt","lat":lat,"lon":lon,"ts":now,"meta":g})

    # ── ACLED ─────────────────────────────────────────────────────────────────
    for a in (data.get("acled_points") or data.get("acled") or []):
        lat, lon = a.get("lat"), a.get("lon")
        if lat and lon:
            signals.append({"type":"acled","lat":lat,"lon":lon,"ts":now,"meta":a})

    # ── GPS-Jammer ────────────────────────────────────────────────────────────
    for z in (data.get("gpsjam_zones") or []):
        lat, lon = z.get("lat"), z.get("lon")
        if lat and lon:
            signals.append({"type":"gpsjam","lat":lat,"lon":lon,"ts":now,"meta":z})

    # ── NOTAM-Sperrzonen ──────────────────────────────────────────────────────
    for n in (data.get("notams") or []):
        lat, lon = n.get("lat"), n.get("lon")
        if lat and lon:
            signals.append({"type":"notam","lat":lat,"lon":lon,"ts":now,"meta":n})

    # ── Blitz / Artillerie-Signaturen ─────────────────────────────────────────
    for s in (data.get("lightning_signals") or data.get("lightning") or []):
        lat, lon = s.get("lat"), s.get("lon")
        if lat and lon:
            signals.append({"type":"lightning_spike","lat":lat,"lon":lon,
                             "ts":now,"meta":s})

    # ── Vision-Analyse (Fahrzeuge, Einheiten) ─────────────────────────────────
    for v in (data.get("vision_markers") or []):
        lat, lon = v.get("lat"), v.get("lon")
        if lat and lon:
            # Konvoi-/Fahrzeughinweis?
            convoy = v.get("convoy_hint") or v.get("vehicles", 0) > 2
            t = "vehicle_spotted"
            signals.append({"type":t,"lat":lat,"lon":lon,"ts":now,"meta":v})

    # ── Bewegungsanomalien (Konvois, Verkehrssurges) ──────────────────────────
    for m in (data.get("movement_alerts") or []):
        lat, lon = m.get("lat"), m.get("lon")
        if lat and lon:
            signals.append({"type":"movement_anomaly","lat":lat,"lon":lon,
                             "ts":now,"meta":m})

    # ── Webcam-Bewegungsalerts (besonders nachts) ─────────────────────────────
    for w in (data.get("webcam_alerts") or []):
        lat, lon = w.get("lat"), w.get("lon")
        if lat and lon:
            signals.append({"type":"webcam_motion","lat":lat,"lon":lon,
                             "ts":now,"meta":w})

    # ── SAR-Überflüge mit Anomalie-Score ─────────────────────────────────────
    for s in (data.get("sar_passes") or []):
        lat, lon = s.get("lat"), s.get("lon")
        if lat and lon and s.get("anomaly_score", 0) > 0.5:
            signals.append({"type":"sar_anomaly","lat":lat,"lon":lon,
                             "ts":now,"meta":s})

    # ── HF-Aktivität (WebSDR – Militärfrequenzen) ─────────────────────────────
    for h in (data.get("hf_signals") or []):
        lat, lon = h.get("lat"), h.get("lon")
        if lat and lon and h.get("confidence", 0) > 0.4:
            signals.append({"type":"hf_activity","lat":lat,"lon":lon,
                             "ts":now,"meta":h})

    # ── Strahlungsanomalien ───────────────────────────────────────────────────
    for r in (data.get("radiation_alerts") or []):
        lat, lon = r.get("lat"), r.get("lon")
        if lat and lon:
            level = r.get("alert_level", "NORMAL")
            t = "iaea_alert" if r.get("source") == "iaea" else "radiation_spike"
            signals.append({"type":t,"lat":lat,"lon":lon,"ts":now,"meta":r})

    # ── Netgraph-Surge (Informationssurge) ───────────────────────────────────
    ng = data.get("netgraph") or {}
    if ng.get("surge_topics"):
        # Kein Geo-Punkt für Netgraph – aber als Hinweis in Region-Cluster einfügen
        # Wir nutzen den GDELT-Schwerpunkt oder Zentrum der anderen Signale
        if signals:
            avg_lat = sum(s["lat"] for s in signals[:5]) / min(5, len(signals))
            avg_lon = sum(s["lon"] for s in signals[:5]) / min(5, len(signals))
            signals.append({"type":"netgraph_surge","lat":avg_lat,"lon":avg_lon,
                             "ts":now,"meta":ng})

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# Cluster-Builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_clusters(signals: list[dict], radius_km: float) -> list[list[dict]]:
    """
    Einfaches greedy Clustering: jedes Signal kommt in den nächsten Cluster
    dessen Zentrum innerhalb radius_km liegt, sonst neuer Cluster.
    """
    clusters: list[list[dict]] = []
    centers:  list[tuple[float,float]] = []

    for sig in signals:
        lat, lon = sig["lat"], sig["lon"]
        placed = False
        for i, ctr in enumerate(centers):
            if _haversine(lat, lon, ctr[0], ctr[1]) <= radius_km:
                clusters[i].append(sig)
                # Zentrum aktualisieren
                all_pts = [(s["lat"],s["lon"]) for s in clusters[i]]
                centers[i] = _centroid(all_pts)
                placed = True
                break
        if not placed:
            clusters.append([sig])
            centers.append((lat, lon))

    return clusters


# ─────────────────────────────────────────────────────────────────────────────
# Threat-Assessment
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ThreatAssessment:
    lat:         float
    lon:         float
    pattern:     Pattern
    confidence:  float
    signals:     list[str]      # Beteiligte Signal-Typen
    signal_count: int
    label:       str
    description: str
    ts:          float = field(default_factory=time.time)
    # T177 – Cross-Validation zwischen unabhängigen Quell-Familien
    source_families:  list[str] = field(default_factory=list)
    cross_validated:  bool      = False
    validation_note:  str       = ""


def _assess_cluster(cluster: list[dict]) -> list[ThreatAssessment]:
    """Prüft ein Cluster gegen alle Patterns und gibt Matches zurück."""
    if len(cluster) < 2:
        return []

    present_types = {s["type"] for s in cluster}
    center = _centroid([(s["lat"],s["lon"]) for s in cluster])
    assessments = []

    # Signal-Aliase: Typen die füreinander einspringen können
    _ALIASES = {
        "ais_gap":          {"maritime", "ais_gap"},
        "humint_contact":   {"humint_contact", "humint_drone"},
        "humint_drone":     {"humint_drone", "humint_contact"},
        "fire":             {"fire"},
        "seismic":          {"seismic"},
        "isr_aircraft":     {"isr_aircraft"},
        "gdelt":            {"gdelt", "acled"},
        "acled":            {"acled", "gdelt"},
        "lightning_spike":  {"lightning_spike"},
        "gpsjam":           {"gpsjam"},
        "notam":            {"notam"},
        # Ebene-4 Erweiterungen
        "vehicle_spotted":  {"vehicle_spotted"},
        "movement_anomaly": {"movement_anomaly"},
        "webcam_motion":    {"webcam_motion"},
        "sar_anomaly":      {"sar_anomaly"},
        "hf_activity":      {"hf_activity"},
        "radiation_spike":  {"radiation_spike", "iaea_alert"},
        "iaea_alert":       {"iaea_alert", "radiation_spike"},
        "netgraph_surge":   {"netgraph_surge"},
        "convoy_detected":  {"vehicle_spotted", "movement_anomaly"},
    }

    for pat in PATTERNS:
        # Wie viele der benötigten Signal-Typen sind vorhanden? (mit Alias-Matching)
        matched = [t for t in pat.signals
                   if present_types & _ALIASES.get(t, {t})]
        # Mindestens ceil(n/2)+1 Signale, bei 3 also ≥2, bei 2 also beide
        min_required = max(1, len(pat.signals) - 1)
        if len(matched) < min_required:
            continue
        ratio = len(matched) / len(pat.signals)

        # Konfidenz skalieren
        conf = round(pat.confidence * ratio
                     * min(1.0, len(cluster) / 3.0 * 0.5 + 0.5), 2)

        # T177: Cross-Validation – Konfidenz je nach Unabhängigkeit der
        # beteiligten Quell-Familien anheben (echte Bestätigung) oder
        # absenken (nur Mehrfachnennung derselben Erfassungsmethode)
        xval = _cross_validate(present_types)
        conf = round(min(0.99, conf * xval["bonus"]), 2)

        if conf < MIN_CONFIDENCE:
            continue

        # Detail-Label
        missing = [t for t in pat.signals if t not in present_types]
        label = pat.label_de
        if xval["validated"]:
            label = "🔗 " + label   # Cross-Validierungs-Marker im Label
        if missing:
            label += f" (ohne {', '.join(missing)})"

        assessments.append(ThreatAssessment(
            lat=center[0],
            lon=center[1],
            pattern=pat,
            confidence=conf,
            signals=sorted(present_types),
            signal_count=len(cluster),
            label=label,
            description=pat.description,
            source_families=xval["families"],
            cross_validated=xval["validated"],
            validation_note=xval["note"],
        ))

    assessments.sort(key=lambda a: a.confidence, reverse=True)
    return assessments[:1]  # Pro Cluster nur das stärkste Pattern


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def fuse_signals(
    data:   dict,
    region: str = "",
    radius_km: float = CLUSTER_RADIUS_KM,
) -> list[ThreatAssessment]:
    """
    Nimmt den kompletten NEXUS-Daten-Dict und gibt eine Liste von
    Bedrohungs-Einschätzungen zurück.

    Parameters
    ----------
    data       : Ergebnis-Dict von nexus_longtest / nexus_escalation
    region     : Optionaler Region-Name für Logging
    radius_km  : Clustering-Radius (Standard: CLUSTER_RADIUS_KM)

    Returns
    -------
    List[ThreatAssessment] — sortiert nach Konfidenz, stärkstes zuerst
    """
    signals = _normalize_signals(data)
    if not signals:
        return []

    clusters = _build_clusters(signals, radius_km)
    all_assessments: list[ThreatAssessment] = []

    for cluster in clusters:
        hits = _assess_cluster(cluster)
        all_assessments.extend(hits)

    all_assessments.sort(key=lambda a: a.confidence, reverse=True)
    return all_assessments


# ─────────────────────────────────────────────────────────────────────────────
# T199: Artillery Fusion — Seismik × Blitz Zeitkorrelation
# ─────────────────────────────────────────────────────────────────────────────

def check_artillery_correlation(
    seismic_events: list[dict],
    lightning_events: list[dict],
    window_s: float = 30.0,
    geo_radius_km: float = 80.0,
) -> list[dict]:
    """
    Korreliert Seismik- und Blitzereignisse nach Zeit und Ort.
    Artilleriebeschuss erzeugt charakteristische EM-Blitz-Signatur 0–30s
    vor/nach dem seismischen Einschlag-Signal.

    Parameters
    ----------
    seismic_events   : Liste von Dicts mit 'lat','lon','time' (UTC-Timestamp)
    lightning_events : Liste von Dicts mit 'lat','lon','time' (UTC-Timestamp)
    window_s         : Zeitfenster in Sekunden (Standard 30s)
    geo_radius_km    : Geo-Radius für Clustering (Standard 80km)

    Returns
    -------
    Liste von Korrelations-Treffern:
        {
          "lat", "lon",
          "artillery_probability" : float  0-1,
          "timestamp"             : float  (unix),
          "seismic_mag"           : float,
          "lightning_count"       : int,
          "delta_s"               : float  (Zeit-Abstand seismik→blitz),
          "evidence"              : str,
          "confidence_label"      : str
        }
    """
    if not seismic_events or not lightning_events:
        return []

    # Timestamps normalisieren
    def _to_ts(ev: dict) -> float:
        t = ev.get("time") or ev.get("ts") or ev.get("timestamp") or 0
        if isinstance(t, str):
            try:
                from datetime import datetime, timezone
                return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0
        return float(t) if t else 0.0

    results = []
    used_seismic = set()

    for i, seis in enumerate(seismic_events):
        ts_s  = _to_ts(seis)
        if ts_s == 0:
            continue
        lat_s = float(seis.get("lat") or seis.get("latitude") or 0)
        lon_s = float(seis.get("lon") or seis.get("longitude") or 0)
        mag   = float(seis.get("mag") or seis.get("magnitude") or 0)

        # Blitzereignisse im Zeit- und Geo-Fenster suchen
        nearby_lightning = []
        for lit in lightning_events:
            ts_l  = _to_ts(lit)
            if ts_l == 0:
                continue
            delta = abs(ts_s - ts_l)
            if delta > window_s:
                continue
            lat_l = float(lit.get("lat") or lit.get("latitude") or 0)
            lon_l = float(lit.get("lon") or lit.get("longitude") or 0)
            dist  = _haversine(lat_s, lon_s, lat_l, lon_l)
            if dist <= geo_radius_km:
                nearby_lightning.append({
                    "ts": ts_l,
                    "delta_s": ts_s - ts_l,   # negativ = Blitz NACH Seismik
                    "dist_km": round(dist, 1),
                    "lat": lat_l,
                    "lon": lon_l,
                })

        if not nearby_lightning:
            continue

        # Mittelpunkt der Korrelation
        all_lats = [lat_s] + [x["lat"] for x in nearby_lightning]
        all_lons = [lon_s] + [x["lon"] for x in nearby_lightning]
        centroid_lat = sum(all_lats) / len(all_lats)
        centroid_lon = sum(all_lons) / len(all_lons)

        # Wahrscheinlichkeits-Formel
        # - Mehrere Blitze = höhere Wahrscheinlichkeit
        # - Kleiner Zeitabstand = höher
        # - Stärkere Seismik = höher (M < 2 typisch für Einschläge)
        n_lit   = len(nearby_lightning)
        min_dt  = min(abs(x["delta_s"]) for x in nearby_lightning)
        # Magnituden unter M2.5 sind typisch für Artillerie/Einschläge
        # (natürliche Erdbeben ≥ M2.5 seltener in Konfliktzonen)
        mag_factor = max(0.3, 1.0 - max(0, mag - 2.0) * 0.15)
        time_factor = max(0.4, 1.0 - min_dt / window_s)
        count_factor = min(1.0, 0.5 + n_lit * 0.1)

        prob = round(min(0.95, mag_factor * time_factor * count_factor), 2)

        # Label
        if prob >= 0.75:
            conf_label = "HOCH"
        elif prob >= 0.55:
            conf_label = "MITTEL"
        else:
            conf_label = "NIEDRIG"

        avg_delta = sum(x["delta_s"] for x in nearby_lightning) / n_lit
        evidence = (
            f"Seismik M{mag:.1f} @ {lat_s:.3f},{lon_s:.3f} | "
            f"{n_lit} Blitz-Ereignis(se) im {geo_radius_km}km-Radius | "
            f"Zeitabstand ⌀{avg_delta:+.1f}s"
        )

        results.append({
            "lat":                  round(centroid_lat, 4),
            "lon":                  round(centroid_lon, 4),
            "artillery_probability": prob,
            "timestamp":            ts_s,
            "seismic_mag":          mag,
            "seismic_lat":          lat_s,
            "seismic_lon":          lon_s,
            "lightning_count":      n_lit,
            "delta_s":              round(avg_delta, 1),
            "evidence":             evidence,
            "confidence_label":     conf_label,
        })
        used_seismic.add(i)

    # Nach Wahrscheinlichkeit sortieren
    results.sort(key=lambda x: x["artillery_probability"], reverse=True)
    return results


def artillery_summary(region: str, data: dict) -> dict:
    """
    Schnelle Artillerie-Einschätzung für nexus_escalation.py.
    Gibt einen strukturierten Dict zurück.
    """
    seismic  = data.get("seismic_events")  or data.get("seismic")  or []
    lightning = data.get("lightning_events") or data.get("lightning") or []

    # Normalisieren falls raw API-Dicts
    if isinstance(seismic, dict):
        seismic = seismic.get("events") or seismic.get("items") or []
    if isinstance(lightning, dict):
        lightning = lightning.get("flashes") or lightning.get("events") or []

    hits = check_artillery_correlation(seismic, lightning)
    if not hits:
        return {"status": "keine_korrelation", "count": 0, "region": region}

    top = hits[0]
    return {
        "status":          "korrelation_gefunden",
        "count":           len(hits),
        "region":          region,
        "top_probability": top["artillery_probability"],
        "top_location":    {"lat": top["lat"], "lon": top["lon"]},
        "top_evidence":    top["evidence"],
        "confidence":      top["confidence_label"],
        "all_hits":        hits[:5],   # maximal 5 zurückgeben
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("NEXUS Fusion-Engine — Selbsttest")
    print("─" * 50)

    # Minimaler Test-Datensatz
    test_data = {
        "seismic_events": [
            {"lat": 31.5, "lon": 34.4, "time": 1700000000, "mag": 1.8},
            {"lat": 31.52, "lon": 34.41, "time": 1700000090, "mag": 2.1},
        ],
        "lightning_events": [
            {"lat": 31.48, "lon": 34.38, "time": 1700000015},
            {"lat": 31.50, "lon": 34.40, "time": 1700000018},
            {"lat": 31.51, "lon": 34.42, "time": 1700000095},
        ],
        "firms": [{"lat": 31.5, "lon": 34.4, "confidence": 80}],
        "gdelt":  [{"lat": 31.5, "lon": 34.4, "ts": 1700000000,
                    "cameo": "190", "goldstein": -8}],
        "acled":  [{"lat": 31.5, "lon": 34.4, "ts": 1700000000,
                    "event_type": "Explosions/Remote violence"}],
    }

    # Artillery-Test
    print("\n🔫 Artillery-Korrelation (Seismik × Blitz):")
    art = artillery_summary("Gaza", test_data)
    print(f"  Status:   {art['status']}")
    print(f"  Treffer:  {art['count']}")
    if art.get("top_probability"):
        print(f"  P(Arty):  {art['top_probability']:.0%}")
        print(f"  Ort:      {art['top_location']}")
        print(f"  Evidenz:  {art['top_evidence']}")

    # Fusion-Test
    print("\n🔗 Fusion-Engine:")
    hits = fuse_signals(test_data, region="Gaza")
    if hits:
        for h in hits:
            print(f"  {h.label} | Konfidenz {h.confidence:.0%} | {h.lat:.3f},{h.lon:.3f}")
    else:
        print("  Keine Cluster-Treffer (brauche mehr Signal-Vielfalt)")
