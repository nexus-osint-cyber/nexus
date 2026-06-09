"""
NEXUS - Ereignis-Korrelations-Engine
Erkennt automatisch wenn mehrere unabhängige Quellen dasselbe Ereignis beschreiben.

Methode:
  1. Geografische Nähe: Ereignisse im gleichen Radius (Standard: 150km)
  2. Zeitliche Nähe: Ereignisse im gleichen Zeitfenster (Standard: 120min)
  3. Thematische Ähnlichkeit: Schlüsselwörter überlappen
  4. Quell-Diversität: Mindestens 2 verschiedene Quell-Typen

Beispiel:
  [OpenSky]     Militärflugzeug über Rafah, 14:32 UTC
  [USGS]        Explosion-ähnliches Seismik-Signal bei Gaza, 14:38 UTC
  [Telegram]    "Luftangriff auf Rafah gemeldet", 14:41 UTC
  → KORRELATION: Wahrscheinlicher Luftangriff in Rafah (3 Quellen, Konfidenz: HOCH)
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Optional

# ── Konfiguration ─────────────────────────────────────────────────────────────
CORRELATION_RADIUS_KM  = 150   # Geografische Nähe
CORRELATION_TIME_MIN   = 120   # Zeitfenster in Minuten
MIN_SOURCES_FOR_ALERT  = 2     # Mindestanzahl Quellen für Korrelation
HIGH_CONFIDENCE_SOURCES = 3    # Ab hier: HOHE Konfidenz

# ── Incident-Keywords für thematische Ähnlichkeit ─────────────────────────────
_INCIDENT_GROUPS: dict[str, list[str]] = {
    "luftangriff": ["luftangriff", "airstrike", "air strike", "bombing", "bomb",
                    "rakete", "missile", "explosion", "blast", "strike"],
    "militär":     ["troops", "military", "forces", "truppen", "soldaten",
                    "panzer", "tank", "artillery", "artillerie"],
    "schiff":      ["ship", "vessel", "schiff", "maritime", "navy", "marine",
                    "tanker", "freighter", "warship"],
    "erdbeben":    ["earthquake", "erdbeben", "seismic", "tremor", "quake"],
    "brand":       ["fire", "feuer", "brand", "burning", "flame"],
    "unfall":      ["crash", "accident", "unfall", "absturz", "collision"],
    "angriff":     ["attack", "angriff", "assault", "offensive", "invasion"],
}


# ── Geo-Hilfsfunktionen ───────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Berechnet Distanz zwischen zwei GPS-Koordinaten in Kilometern."""
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _age_min_to_ts(age_min: int) -> float:
    """age_min (Minuten her) → Unix-Timestamp."""
    return time.time() - age_min * 60


def _ts_diff_min(ts1: float, ts2: float) -> float:
    return abs(ts1 - ts2) / 60


# ── Thematische Analyse ───────────────────────────────────────────────────────

def _extract_topics(text: str) -> set:
    """Extrahiert Themen-Gruppen aus Text als Set (unterstützt .add() und &-Schnittmenge)."""
    t = text.lower()
    found: set = set()
    for group, keywords in _INCIDENT_GROUPS.items():
        if any(kw in t for kw in keywords):
            found.add(group)
    return found


def _topics_overlap(topics1, topics2) -> bool:
    """Prüft ob zwei Themen-Listen überlappen."""
    return bool(topics1 & topics2) or not topics1 or not topics2


# ── Ereignis-Normalisierung ───────────────────────────────────────────────────

def _normalize_event(raw: dict, source_type: str) -> Optional[dict]:
    """
    Wandelt Rohdaten aus verschiedenen Quellen in einheitliches Format um.
    Gibt None zurück wenn kein Geo-Punkt vorhanden.
    """
    lat = raw.get("lat") or raw.get("latitude") or raw.get("center_lat")
    lon = raw.get("lon") or raw.get("longitude") or raw.get("center_lon")

    if not lat or not lon:
        return None

    title   = (raw.get("title") or raw.get("callsign") or raw.get("place") or "")
    summary = (raw.get("summary") or raw.get("suspicious") or raw.get("osint") or "")
    text    = f"{title} {summary}"
    topics  = _extract_topics(text)
    age_min = int(raw.get("age_min") or 0)

    return {
        "lat":         float(lat),
        "lon":         float(lon),
        "title":       title[:120],
        "summary":     summary[:200],
        "source_type": source_type,
        "source_name": raw.get("source") or raw.get("callsign") or source_type,
        "topics":      topics,
        "ts":          _age_min_to_ts(age_min),
        "age_min":     age_min,
        "url":         raw.get("url", "#"),
        "raw":         raw,
    }


# ── Haupt-Korrelations-Engine ─────────────────────────────────────────────────

def correlate_events(
    articles:   list[dict] = None,   # Nachrichten-Artikel
    aircraft:   list[dict] = None,   # Flugzeuge mit suspicious=...
    maritime:   list[dict] = None,   # Maritime Alerts
    earthquakes: list[dict] = None,  # Erdbeben
    gdelt:      list[dict] = None,   # GDELT Geo-Events
    incidents:  list[dict] = None,   # Existierende Incident-Marker
    radius_km:  float = CORRELATION_RADIUS_KM,
    time_min:   float = CORRELATION_TIME_MIN,
) -> list[dict]:
    """
    Findet Ereignis-Cluster aus mehreren Quellen.
    Gibt Liste von Korrelations-Alerts zurück, sortiert nach Konfidenz.
    """
    events: list[dict] = []

    # Artikel (RSS + Telegram + Reddit + GDELT)
    for a in (articles or []):
        if a.get("lat") and a.get("lon"):
            e = _normalize_event(a, "nachricht")
            if e:
                events.append(e)

    # Auffällige Flugzeuge
    for a in (aircraft or []):
        if a.get("suspicious") and a.get("lat") and a.get("lon"):
            e = _normalize_event(dict(a, title=f"✈ {a.get('callsign','?')} auffällig",
                                      summary=a.get("suspicious","")), "flug")
            if e:
                e["topics"].add("militär")
                events.append(e)

    # Maritime Alerts
    for m in (maritime or []):
        if m.get("lat") and m.get("lon"):
            e = _normalize_event(dict(m, title=m.get("title","Maritime Alert")), "maritim")
            if e:
                e["topics"].add("schiff")
                events.append(e)

    # Erdbeben (mit OSINT-Hinweis auf Explosion)
    for q in (earthquakes or []):
        if q.get("lat") and q.get("lon") and q.get("osint_hint"):
            e = _normalize_event(dict(q,
                title=f"M{q.get('mag','?')} {q.get('place','?')}",
                summary=q.get("osint_hint","")), "seismik")
            if e and "explosion" in q.get("osint_hint","").lower():
                e["topics"].add("luftangriff")
                events.append(e)

    # GDELT Geo-Events
    for g in (gdelt or []):
        if g.get("lat") and g.get("lon"):
            e = _normalize_event(g, "gdelt")
            if e:
                events.append(e)

    # Incident-Marker
    for i in (incidents or []):
        if i.get("lat") and i.get("lon"):
            e = _normalize_event(i, "osint")
            if e:
                events.append(e)

    if not events:
        return []

    # ── Clustering: Geo + Zeit + Thema ───────────────────────────────────────
    clusters: list[list[dict]] = []
    used: set = set()

    for i, ev in enumerate(events):
        if i in used:
            continue
        cluster = [ev]
        used.add(i)

        for j, other in enumerate(events):
            if j in used or j == i:
                continue
            dist  = _haversine_km(ev["lat"], ev["lon"], other["lat"], other["lon"])
            t_diff = _ts_diff_min(ev["ts"], other["ts"])
            if (dist <= radius_km and
                    t_diff <= time_min and
                    _topics_overlap(ev["topics"], other["topics"])):
                cluster.append(other)
                used.add(j)

        if len(cluster) >= MIN_SOURCES_FOR_ALERT:
            clusters.append(cluster)

    if not clusters:
        return []

    # ── Alerts aus Clustern erstellen ─────────────────────────────────────────
    alerts = []
    for cluster in clusters:
        # Quell-Diversität
        source_types = {e["source_type"] for e in cluster}
        n_sources    = len(cluster)

        # Konfidenz
        if n_sources >= HIGH_CONFIDENCE_SOURCES and len(source_types) >= 2:
            confidence = "HOCH"
            conf_color = "#ff4444"
        elif n_sources >= MIN_SOURCES_FOR_ALERT and len(source_types) >= 2:
            confidence = "MITTEL"
            conf_color = "#ff8800"
        else:
            confidence = "NIEDRIG"
            conf_color = "#ffd700"

        # Zentrum des Clusters (Durchschnitt)
        center_lat = sum(e["lat"] for e in cluster) / len(cluster)
        center_lon = sum(e["lon"] for e in cluster) / len(cluster)

        # Alle Themen sammeln
        all_topics = set()
        for e in cluster:
            all_topics.update(e["topics"])

        # Titel aus häufigstem Thema + Ort
        topic_str = ", ".join(sorted(all_topics)) if all_topics else "Unbekannt"

        # Frischestes Ereignis als Referenz
        newest = min(cluster, key=lambda e: e["age_min"])

        # Events serialisierbar machen: topics-Set -> sortierte Liste
        cluster_serializable = [
            {**e, "topics": sorted(e["topics"]) if isinstance(e.get("topics"), set) else e.get("topics", [])}
            for e in cluster
        ]
        alerts.append({
            "lat":          round(center_lat, 4),
            "lon":          round(center_lon, 4),
            "confidence":   confidence,
            "conf_color":   conf_color,
            "n_sources":    n_sources,
            "source_types": sorted(source_types),
            "topics":       sorted(all_topics),
            "topic_str":    topic_str,
            "title":        f"KORRELATION [{confidence}]: {topic_str.upper()} – {n_sources} Quellen",
            "newest_title": newest["title"],
            "newest_age":   newest["age_min"],
            "events":       cluster_serializable,
            "summary":      _build_cluster_summary(cluster),
        })

    # Nach Konfidenz + Frische sortieren
    _conf_order = {"HOCH": 0, "MITTEL": 1, "NIEDRIG": 2}
    alerts.sort(key=lambda a: (_conf_order.get(a["confidence"], 3), a["newest_age"]))
    return alerts


def _build_cluster_summary(cluster: list[dict]) -> str:
    """Baut lesbaren Text aus einem Cluster."""
    lines = []
    for e in sorted(cluster, key=lambda x: x["age_min"]):
        age_s = f"{e['age_min']}min" if e["age_min"] < 120 else f"{e['age_min']//60}h"
        lines.append(f"  [{e['source_type'].upper()} · {age_s}] {e['title'][:80]}")
    return "\n".join(lines)


def correlation_text_summary(alerts: list[dict]) -> str:
    """Formatierter Text für LLM-Kontext."""
    if not alerts:
        return ""
    lines = [f"[KORRELIERTE EREIGNISSE – {len(alerts)} Cluster erkannt]"]
    for a in alerts[:5]:
        lines.append(
            f"\n  ⚡ {a['title']}"
            f"\n     Quellen: {', '.join(a['source_types'])}"
            f"\n{a['summary']}"
        )
    return "\n".join(lines)


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    # Beispiel-Ereignisse die korrelieren sollten (Rafah-Scenario)
    test_articles = [
        {"lat": 31.30, "lon": 34.25, "title": "Luftangriff auf Rafah gemeldet",
         "source": "Telegram/Conflict_News", "age_min": 10},
        {"lat": 31.35, "lon": 34.30, "title": "Explosion heard in southern Gaza",
         "source": "Reddit/r/worldnews", "age_min": 12},
    ]
    test_seismic = [
        {"lat": 31.28, "lon": 34.22, "mag": 2.1, "place": "Gaza Strip",
         "osint_hint": "mögliche Explosion/Untergrundtest", "age_min": 8},
    ]
    test_aircraft = [
        {"lat": 31.40, "lon": 34.20, "callsign": "IAF123",
         "suspicious": "Militärischer Callsign-Präfix", "age_min": 5},
    ]

    alerts = correlate_events(
        articles=test_articles,
        earthquakes=test_seismic,
        aircraft=test_aircraft,
    )
    print(f"NEXUS Korrelations-Test: {len(alerts)} Cluster\n")
    for a in alerts:
        print(f"  {a['title']}")
        print(f"  Konfidenz: {a['confidence']} | Quellen: {a['n_sources']}")
        print(f"  {a['summary']}")
        print()
