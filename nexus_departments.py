"""
NEXUS – Department Architecture
================================
Unterteilt NEXUS in 6 spezialisierte Abteilungen mit eigenen Scores.

Abteilungen:
  OSINT   – Open Source Intelligence   (News, Feeds, Social, Telegram)
  GEOINT  – Geospatial Intelligence    (Satellit, Karten, Dark Zones)
  SIGINT  – Signals Intelligence       (Seismik, EM, Cyber, GPS-Jam)
  HUMINT  – Entity Intelligence        (Akteure, Regime-Profile, Muster)
  ECONINT – Economic Intelligence      (Handel, Waffen, Sanktionen)
  HUMANA  – Humanitarian Intelligence  (IDP, Blockade, Krisenfrühwarnung)

Jede Abteilung liefert:
  dept        – Abteilungsname (z.B. "OSINT")
  label       – Lesbare Bezeichnung
  icon        – Emoji-Icon
  score       – 0–100 (normiert)
  confidence  – "high" / "medium" / "low" / "none"
  findings    – Liste strukturierter Befunde mit Quelle + Score
  sources     – genutzte Module
  failed      – fehlgeschlagene / nicht installierte Module
  sub_scores  – Score pro Quelle
  timestamp   – ISO-8601 UTC

Master-Score (gewichteter Durchschnitt):
  OSINT×0.25 + GEOINT×0.20 + SIGINT×0.20 + HUMINT×0.15
  + ECONINT×0.10 + HUMANA×0.10
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════════════════════════════════════
# Metadaten der Abteilungen
# ═══════════════════════════════════════════════════════════════════════════════

DEPARTMENTS: dict[str, dict] = {
    "OSINT":   {
        "label":   "Open Source Intelligence",
        "icon":    "⚡",
        "weight":  0.25,
        "color":   "#f59e0b",
        "modules": ["nexus_rss", "nexus_gdelt", "nexus_acled", "nexus_telethon"],
        "desc":    "Nachrichtenlage, Feeds, Social Media, Telegram-Kanäle",
    },
    "GEOINT":  {
        "label":   "Geospatial Intelligence",
        "icon":    "🛰",
        "weight":  0.20,
        "color":   "#10b981",
        "modules": ["nexus_sentinel2", "nexus_overpass", "nexus_strava",
                    "nexus_geospy", "nexus_yolo", "nexus_geolocate"],
        "desc":    "Satellitenbilder, Militärinfrastruktur, Dark Zones, YOLO",
    },
    "SIGINT":  {
        "label":   "Signals Intelligence",
        "icon":    "📡",
        "weight":  0.20,
        "color":   "#ef4444",
        "modules": ["nexus_seismic", "nexus_lightning", "nexus_gpsjam",
                    "nexus_cyber", "nexus_fusion"],
        "desc":    "Seismik, EM-Signale, GPS-Jamming, Cyber-Anomalien",
    },
    "HUMINT":  {
        "label":   "Entity Intelligence",
        "icon":    "👤",
        "weight":  0.15,
        "color":   "#a78bfa",
        "modules": ["nexus_humint", "nexus_wikidata"],
        "desc":    "Akteurprofile, Regime-Struktur, historische Muster",
    },
    "ECONINT": {
        "label":   "Economic Intelligence",
        "icon":    "📊",
        "weight":  0.10,
        "color":   "#06b6d4",
        "modules": ["nexus_comtrade", "nexus_sipri"],
        "desc":    "Handelsdaten, Waffentransfers, Sanktions-Indikatoren",
    },
    "HUMANA":  {
        "label":   "Humanitarian Intelligence",
        "icon":    "🏥",
        "weight":  0.10,
        "color":   "#fb923c",
        "modules": ["nexus_reliefweb"],
        "desc":    "IDP-Bewegungen, Blockaden, humanitäre Frühwarnung",
    },
}

# Gewichtssumme = 1.0 prüfen
assert abs(sum(d["weight"] for d in DEPARTMENTS.values()) - 1.0) < 0.001, \
    "DEPARTMENTS-Gewichte müssen 1.0 ergeben"

# Region → (lat, lon) Zentrum für Module die Koordinaten brauchen
REGION_COORDS: dict[str, tuple[float, float]] = {
    "Iran":     (32.0,  53.0),
    "Israel":   (31.5,  35.0),
    "Gaza":     (31.4,  34.4),
    "Lebanon":  (33.9,  35.5),
    "Yemen":    (15.5,  48.0),
    "Syria":    (35.0,  38.0),
    "Iraq":     (33.0,  44.0),
    "Ukraine":  (49.0,  32.0),
    "Russia":   (55.7,  37.6),
    "Turkey":   (39.0,  35.0),
    "Saudi":    (24.0,  45.0),
}

# Bekannte Akteure pro Region für Wikidata-Entity-Resolution
REGION_ACTORS: dict[str, list[str]] = {
    "Iran":    ["IRGC", "Quds Force", "Islamic Revolutionary Guard Corps",
                "Ali Khamenei", "Hezbollah"],
    "Israel":  ["IDF", "Mossad", "Shin Bet", "Netanyahu"],
    "Gaza":    ["Hamas", "Islamic Jihad", "Palestinian Authority"],
    "Lebanon": ["Hezbollah", "Hassan Nasrallah", "Lebanese Armed Forces"],
    "Yemen":   ["Houthis", "Ansar Allah", "Saudi-Led Coalition", "AQAP"],
    "Syria":   ["SAA", "HTS", "SDF", "Assad"],
    "Ukraine": ["ZSU", "GUR", "Wagner Group", "Zelensky"],
    "Russia":  ["FSB", "GRU", "Putin", "Gerasimov"],
    "Iraq":    ["PMF", "Popular Mobilization Forces", "Kataib Hezbollah"],
}

MODULE_TIMEOUT: int = 40  # Max. Sekunden pro Modul-Call


# ═══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conf_label(score: float) -> str:
    if score >= 60: return "high"
    if score >= 30: return "medium"
    if score >  0:  return "low"
    return "none"


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def _safe(fn, *args, **kwargs):
    """Ruft fn(*args, **kwargs) auf; gibt None bei Exception zurück."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# ABTEILUNG 1: OSINT
# ═══════════════════════════════════════════════════════════════════════════════

def osint_score(region: str) -> dict:
    """
    Open Source Intelligence: RSS, GDELT, ACLED, Telethon.
    Score 0–100 aus Ereignis-Dichte und Angriffs-Keyword-Treffern.
    """
    used: list[str]    = []
    failed: list[str]  = []
    findings: list[dict] = []
    sub: list[float]   = []

    # ── RSS ──────────────────────────────────────────────────────────────────
    try:
        from nexus_rss import fetch_articles_for_region, RSS_ATTACK_KW, RSS_MED_KW
        articles = _safe(fetch_articles_for_region, region, fast=True) or []
        used.append("nexus_rss")
        n = len(articles)
        kw_lower = set()
        for a in articles:
            kw_lower.update((a.get("title") or "").lower().split())
        attack_hits = len(kw_lower & {k.lower() for k in RSS_ATTACK_KW})
        med_hits    = len(kw_lower & {k.lower() for k in RSS_MED_KW})
        rss_s = _clamp(min(40.0, n * 2.5) + attack_hits * 12.0 + med_hits * 4.0)
        sub.append(rss_s)
        if articles:
            findings.append({
                "source":          "RSS",
                "type":            "news_feed",
                "count":           n,
                "attack_keywords": attack_hits,
                "med_keywords":    med_hits,
                "top_headlines":   [a.get("title","") for a in articles[:3]],
                "score":           round(rss_s, 1),
            })
    except ImportError:
        failed.append("nexus_rss")

    # ── GDELT ────────────────────────────────────────────────────────────────
    try:
        from nexus_gdelt import fetch_gdelt_articles
        gdelt = _safe(fetch_gdelt_articles, region, hours=48, max_results=30) or []
        used.append("nexus_gdelt")
        n = len(gdelt)
        avg_rel = (sum(a.get("relevance_score", 0.5) for a in gdelt) / n) if n else 0
        gdelt_s = _clamp(n * 3.0 * max(0.3, avg_rel))
        sub.append(gdelt_s)
        if gdelt:
            findings.append({
                "source":       "GDELT",
                "type":         "global_events",
                "count":        n,
                "avg_relevance": round(avg_rel, 2),
                "top_events":   [a.get("title","") for a in gdelt[:3]],
                "score":        round(gdelt_s, 1),
            })
    except ImportError:
        failed.append("nexus_gdelt")

    # ── ACLED / UCDP ─────────────────────────────────────────────────────────
    try:
        from nexus_acled import fetch_ucdp_events
        acled = _safe(fetch_ucdp_events, region, days=14) or []
        used.append("nexus_acled")
        n = len(acled)
        high = [e for e in acled if e.get("priority") in ("KRITISCH", "HOCH")]
        acled_s = _clamp(len(high) * 12.0 + (n - len(high)) * 2.0)
        sub.append(acled_s)
        if acled:
            findings.append({
                "source":        "ACLED/UCDP",
                "type":          "conflict_events",
                "total":         n,
                "high_priority": len(high),
                "top_events":    [e.get("notes", e.get("description",""))[:60]
                                  for e in acled[:2]],
                "score":         round(acled_s, 1),
            })
    except ImportError:
        failed.append("nexus_acled")

    # ── Telethon (Telegram MTProto) ───────────────────────────────────────────
    try:
        from nexus_telethon import telethon_for_escalation
        tel = _safe(telethon_for_escalation, region) or {}
        used.append("nexus_telethon")
        top_score   = tel.get("top_score", 0.0)
        msg_count   = tel.get("message_count", 0)
        ch_active   = tel.get("channels_active", 0)
        surge       = tel.get("surge_active", False)
        # top_score ist ~0–10 Skala; normieren
        tel_s = _clamp(top_score * 8.0 + min(20.0, msg_count * 0.5) +
                       (15.0 if surge else 0.0))
        sub.append(tel_s)
        if top_score > 0 or msg_count > 0:
            findings.append({
                "source":          "Telegram",
                "type":            "social_surge",
                "channels_active": ch_active,
                "message_count":   msg_count,
                "surge_active":    surge,
                "top_score":       round(top_score, 2),
                "hint":            tel.get("hint", ""),
                "categories":      list(tel.get("categories", {}).keys())[:4],
                "score":           round(tel_s, 1),
            })
    except ImportError:
        failed.append("nexus_telethon")

    final = _clamp(sum(sub) / max(1, len(sub))) if sub else 0.0
    return _dept_result("OSINT", region, round(final, 1), findings, used, failed)


# ═══════════════════════════════════════════════════════════════════════════════
# ABTEILUNG 2: GEOINT
# ═══════════════════════════════════════════════════════════════════════════════

def geoint_score(region: str) -> dict:
    """
    Geospatial Intelligence: Sentinel-2, Overpass, Dark Zones.
    Score aus Satellitenbild-Änderungen, Militärinfrastruktur-Dichte, GPS-Sperrzonen.
    """
    used: list[str]    = []
    failed: list[str]  = []
    findings: list[dict] = []
    sub: list[float]   = []

    # ── Sentinel-2 Change Detection ──────────────────────────────────────────
    try:
        from nexus_sentinel2 import detect_ground_changes
        changes = _safe(detect_ground_changes, region, days_back=45,
                        max_cloud=30, min_score=0.0) or []
        used.append("nexus_sentinel2")
        sig = [c for c in changes if c.get("change_score", 0) >= 0.30]
        max_cs = max((c.get("change_score", 0) for c in changes), default=0.0)
        s2_s = _clamp(len(sig) * 15.0 + max_cs * 50.0)
        sub.append(s2_s)
        if changes:
            ctypes = list({c.get("change_type","?") for c in changes[:8]})
            findings.append({
                "source":           "Sentinel-2",
                "type":             "satellite_change",
                "aois_checked":     len(changes),
                "significant":      len(sig),
                "max_change_score": round(max_cs, 3),
                "change_types":     ctypes,
                "top_sites":        [
                    {"name": c.get("name","?"),
                     "change_type": c.get("change_type","?"),
                     "score": round(c.get("change_score",0),3)}
                    for c in sorted(changes,
                        key=lambda x: x.get("change_score",0), reverse=True)[:3]
                ],
                "score": round(s2_s, 1),
            })
    except ImportError:
        failed.append("nexus_sentinel2")

    # ── OSM Militärinfrastruktur (Overpass) ──────────────────────────────────
    try:
        from nexus_overpass import get_military_map
        mmap = _safe(get_military_map, region) or {}
        used.append("nexus_overpass")
        if mmap.get("status") == "ok":
            total   = mmap.get("total_objects", 0)
            hi_risk = mmap.get("high_risk_count", 0)
            ovp_s   = _clamp(min(40.0, total * 1.5) + hi_risk * 8.0)
            sub.append(ovp_s)
            findings.append({
                "source":         "OSM/Overpass",
                "type":           "military_infrastructure",
                "total_objects":  total,
                "high_risk":      hi_risk,
                "by_type":        mmap.get("by_type", {}),
                "top_sites":      [
                    {"name": s.get("name","?"), "type": s.get("mil_type","?"),
                     "lat": s.get("lat"), "lon": s.get("lon")}
                    for s in (mmap.get("high_risk_sites") or [])[:3]
                ],
                "score": round(ovp_s, 1),
            })
    except ImportError:
        failed.append("nexus_overpass")

    # ── Dark Zones: GPS-Jam + Military Sperrzonen ────────────────────────────
    try:
        from nexus_strava import detect_dark_zones
        zones = _safe(detect_dark_zones, region,
                      min_confidence=0.25,
                      include_osm=True,
                      include_gpsjam=True,
                      include_notam=True) or []
        used.append("nexus_strava")
        multi   = [z for z in zones if z.get("zone_type") == "multi_signal"]
        mil_osm = [z for z in zones if z.get("zone_type") == "military_osm"]
        top_conf = max((z.get("confidence", 0.0) for z in zones), default=0.0)
        dz_s = _clamp(len(multi) * 25.0 + len(mil_osm) * 8.0 + top_conf * 40.0)
        sub.append(dz_s)
        if zones:
            findings.append({
                "source":       "DarkZone",
                "type":         "military_dark_zones",
                "total":        len(zones),
                "multi_signal": len(multi),
                "military_osm": len(mil_osm),
                "top_confidence": round(top_conf, 2),
                "top_zones":    [
                    {"name": z.get("name","?"),
                     "type": z.get("zone_type","?"),
                     "confidence": round(z.get("confidence",0),2),
                     "lat": z.get("lat"), "lon": z.get("lon")}
                    for z in sorted(zones,
                        key=lambda x: x.get("confidence",0), reverse=True)[:3]
                ],
                "score": round(dz_s, 1),
            })
    except ImportError:
        failed.append("nexus_strava")

    final = _clamp(sum(sub) / max(1, len(sub))) if sub else 0.0
    return _dept_result("GEOINT", region, round(final, 1), findings, used, failed)


# ═══════════════════════════════════════════════════════════════════════════════
# ABTEILUNG 3: SIGINT
# ═══════════════════════════════════════════════════════════════════════════════

def sigint_score(region: str) -> dict:
    """
    Signals Intelligence: Seismik, Blitzortung, GPS-Jamming, Cyber.
    Score aus physikalischen Signalen die auf militärische Aktivität hindeuten.
    """
    used: list[str]    = []
    failed: list[str]  = []
    findings: list[dict] = []
    sub: list[float]   = []

    # ── Seismik / Detonations-Kandidaten ─────────────────────────────────────
    try:
        from nexus_seismic import get_detonation_candidates
        dets = _safe(get_detonation_candidates, region, hours=48) or []
        used.append("nexus_seismic")
        high = [d for d in dets if d.get("det_confidence") == "high"]
        med  = [d for d in dets if d.get("det_confidence") == "medium"]
        sei_s = _clamp(len(high) * 35.0 + len(med) * 15.0)
        sub.append(sei_s)
        if dets:
            best = sorted(dets,
                key=lambda d: {"high":3,"medium":2,"low":1}.get(
                    d.get("det_confidence",""), 0), reverse=True)
            findings.append({
                "source":           "Seismik",
                "type":             "detonation_candidates",
                "total":            len(dets),
                "high_confidence":  len(high),
                "medium_confidence": len(med),
                "top_event":        {
                    "mag":        best[0].get("mag"),
                    "depth_km":   best[0].get("depth"),
                    "confidence": best[0].get("det_confidence"),
                    "lat":        best[0].get("lat"),
                    "lon":        best[0].get("lon"),
                    "time":       best[0].get("time"),
                } if best else {},
                "score": round(sei_s, 1),
            })
    except ImportError:
        failed.append("nexus_seismic")

    # ── Blitzortung / Artillerie-Flash ───────────────────────────────────────
    try:
        from nexus_lightning import analyze_lightning
        lit = _safe(analyze_lightning, region) or {}
        used.append("nexus_lightning")
        conf    = lit.get("confidence", "none")
        count   = lit.get("count", 0)
        conf_pts = {"high": 80.0, "medium": 50.0, "low": 20.0, "none": 0.0}
        lit_s = _clamp(conf_pts.get(conf, 0.0) + min(20.0, count * 2.0))
        sub.append(lit_s)
        if conf != "none":
            findings.append({
                "source":     "Blitzortung",
                "type":       "artillery_flash",
                "confidence": conf,
                "count":      count,
                "zone":       lit.get("zone"),
                "detail":     lit.get("detail", ""),
                "score":      round(lit_s, 1),
            })
    except ImportError:
        failed.append("nexus_lightning")

    # ── GPS-Jamming ──────────────────────────────────────────────────────────
    try:
        from nexus_gpsjam import check_gps_jamming
        gpsjam = _safe(check_gps_jamming, region) or {}
        used.append("nexus_gpsjam")
        intensity = gpsjam.get("intensity", "NIEDRIG")
        jam_pts   = {"HOCH": 75.0, "MITTEL": 45.0, "NIEDRIG": 5.0}
        jam_s     = _clamp(jam_pts.get(intensity, 0.0))
        sub.append(jam_s)
        if intensity in ("HOCH", "MITTEL"):
            findings.append({
                "source":    "GPS-Jam",
                "type":      "gps_jamming",
                "intensity": intensity,
                "zone":      gpsjam.get("zone"),
                "lat":       gpsjam.get("lat"),
                "lon":       gpsjam.get("lon"),
                "score":     round(jam_s, 1),
            })
    except ImportError:
        failed.append("nexus_gpsjam")

    # ── Cyber / Netzwerk-Anomalien ────────────────────────────────────────────
    try:
        from nexus_cyber import cyber_escalation_signal
        cy = _safe(cyber_escalation_signal, region) or {}
        used.append("nexus_cyber")
        raw_cy = cy.get("score", 0.0)    # 0–25 range
        cy_s   = _clamp(raw_cy * 4.0)   # → 0–100
        sub.append(cy_s)
        if raw_cy > 0:
            findings.append({
                "source": "Cyber",
                "type":   "network_anomalies",
                "raw_score": round(raw_cy, 1),
                "notes":  cy.get("notes", [])[:3],
                "score":  round(cy_s, 1),
            })
    except ImportError:
        failed.append("nexus_cyber")

    final = _clamp(sum(sub) / max(1, len(sub))) if sub else 0.0
    return _dept_result("SIGINT", region, round(final, 1), findings, used, failed)


# ═══════════════════════════════════════════════════════════════════════════════
# ABTEILUNG 4: HUMINT  (Entity Intelligence)
# ═══════════════════════════════════════════════════════════════════════════════

def humint_score(region: str) -> dict:
    """
    Entity Intelligence: Wikidata-Profile + HUMINT aus Telegram-Nachrichten.
    Beantwortet: Wer sind die Akteure? Wie ist das Regime strukturiert?
    Welche historischen Muster gibt es?
    """
    used: list[str]    = []
    failed: list[str]  = []
    findings: list[dict] = []
    sub: list[float]   = []

    # ── Wikidata Entity Resolution ────────────────────────────────────────────
    try:
        from nexus_wikidata import resolve_entity
        actors   = REGION_ACTORS.get(region, [])
        resolved = []
        for actor in actors[:5]:          # max 5 um API zu schonen
            r = _safe(resolve_entity, actor)
            if r and r.get("status") == "found":
                resolved.append({
                    "name":        r.get("label", actor),
                    "qid":         r.get("qid", ""),
                    "type":        r.get("instance_of", ""),
                    "country":     r.get("country", ""),
                    "description": r.get("description", "")[:120],
                    "wikipedia":   r.get("wikipedia_url", ""),
                })
        used.append("nexus_wikidata")
        wd_s = _clamp(len(resolved) * 14.0)
        sub.append(wd_s)
        findings.append({
            "source":           "Wikidata",
            "type":             "entity_profiles",
            "actors_resolved":  len(resolved),
            "actors_queried":   len(actors[:5]),
            "entities":         resolved,
            "score":            round(wd_s, 1),
        })
    except ImportError:
        failed.append("nexus_wikidata")

    # ── HUMINT aus Telegram-Texten ────────────────────────────────────────────
    try:
        from nexus_humint import extract_humint
        from nexus_telethon import telethon_for_escalation
        tel = _safe(telethon_for_escalation, region) or {}
        # telethon_for_escalation liefert kein messages-Feld direkt
        # → Versuche fetch_iran_signals für Rohdaten
        try:
            from nexus_telethon import fetch_iran_signals
            posts = _safe(fetch_iran_signals, hours_back=6, min_score=0.3) or []
            texts = [p.get("text", p.get("content","")) for p in posts if
                     p.get("text") or p.get("content")]
        except Exception:
            texts = []

        if texts:
            hits = _safe(extract_humint, texts, region) or []
            used.append("nexus_humint")
            n = len(hits)
            hum_s = _clamp(n * 18.0)
            sub.append(hum_s)
            if hits:
                findings.append({
                    "source":    "HUMINT",
                    "type":      "field_intelligence",
                    "hits":      n,
                    "top_hits":  [
                        {
                            "text":   getattr(h, "text", str(h))[:80],
                            "coords": (getattr(h, "lat", None),
                                       getattr(h, "lon", None)),
                            "confidence": getattr(h, "confidence", "?"),
                        }
                        for h in (hits[:3] if hasattr(hits[0], "text") else
                                  [{"text": str(h)} for h in hits[:3]])
                    ],
                    "score": round(hum_s, 1),
                })
        else:
            used.append("nexus_humint")
            sub.append(0.0)
    except ImportError:
        failed.append("nexus_humint")

    final = _clamp(sum(sub) / max(1, len(sub))) if sub else 0.0
    return _dept_result("HUMINT", region, round(final, 1), findings, used, failed)


# ═══════════════════════════════════════════════════════════════════════════════
# ABTEILUNG 5: ECONINT
# ═══════════════════════════════════════════════════════════════════════════════

def econint_score(region: str) -> dict:
    """
    Economic Intelligence: UN Comtrade + SIPRI Waffentransfers.
    Erkennt: Sanktions-Umgehung, Dual-Use-Güter, Aufrüstungs-Muster.
    """
    used: list[str]    = []
    failed: list[str]  = []
    findings: list[dict] = []
    sub: list[float]   = []

    # ── UN Comtrade Handelsdaten ──────────────────────────────────────────────
    try:
        from nexus_comtrade import comtrade_escalation_signal
        sig = _safe(comtrade_escalation_signal, region) or {}
        used.append("nexus_comtrade")
        if sig.get("status") != "error":
            raw = sig.get("score", 0.0)       # 0–10
            ct_s = _clamp(raw * 10.0)          # → 0–100
            sub.append(ct_s)
            findings.append({
                "source":              "UN Comtrade",
                "type":                "trade_anomaly",
                "raw_score":           round(raw, 1),
                "sanctions_indicator": sig.get("sanctions_indicator", False),
                "anomaly_score":       sig.get("anomaly_score", 0),
                "notes":               sig.get("notes", [])[:4],
                "score":               round(ct_s, 1),
            })
    except ImportError:
        failed.append("nexus_comtrade")

    # ── SIPRI Waffentransfers ─────────────────────────────────────────────────
    try:
        from nexus_sipri import sipri_escalation_signal
        sig = _safe(sipri_escalation_signal, region) or {}
        used.append("nexus_sipri")
        if sig.get("status") != "error":
            raw = sig.get("score", 0.0)        # 0–30 (capped)
            sp_s = _clamp(raw * 3.3)           # → 0–100
            sub.append(sp_s)
            findings.append({
                "source":            "SIPRI",
                "type":              "arms_transfers",
                "raw_score":         round(raw, 1),
                "transfer_count":    sig.get("count", 0),
                "high_risk_weapons": sig.get("high_risk_weapons", [])[:4],
                "top_supplier":      sig.get("top_supplier"),
                "score":             round(sp_s, 1),
            })
    except ImportError:
        failed.append("nexus_sipri")

    final = _clamp(sum(sub) / max(1, len(sub))) if sub else 0.0
    return _dept_result("ECONINT", region, round(final, 1), findings, used, failed)


# ═══════════════════════════════════════════════════════════════════════════════
# ABTEILUNG 6: HUMANA
# ═══════════════════════════════════════════════════════════════════════════════

def humana_score(region: str) -> dict:
    """
    Humanitarian Intelligence: ReliefWeb / OCHA HDX.
    Frühwarner: IDP-Anstieg, Blockade, Belagerung — oft vor Nachrichtenlage.
    """
    used: list[str]    = []
    failed: list[str]  = []
    findings: list[dict] = []
    sub: list[float]   = []

    try:
        from nexus_reliefweb import reliefweb_escalation_signal
        sig = _safe(reliefweb_escalation_signal, region) or {}
        used.append("nexus_reliefweb")
        if sig.get("status") not in ("error", None):
            raw = sig.get("score", 0.0)        # 0–20 (capped in module)
            rw_s = _clamp(raw * 5.0)           # → 0–100
            sub.append(rw_s)
            findings.append({
                "source":            "ReliefWeb/OCHA",
                "type":              "humanitarian_situation",
                "raw_score":         round(raw, 1),
                "top_report":        sig.get("top_report", ""),
                "critical_keywords": sig.get("indicators", sig.get("critical_keywords", []))[:4],
                "idp_count":         sig.get("idp_count"),
                "score":             round(rw_s, 1),
            })
    except ImportError:
        failed.append("nexus_reliefweb")

    final = _clamp(sum(sub) / max(1, len(sub))) if sub else 0.0
    return _dept_result("HUMANA", region, round(final, 1), findings, used, failed)


# ═══════════════════════════════════════════════════════════════════════════════
# Interner Builder
# ═══════════════════════════════════════════════════════════════════════════════

def _dept_result(
    dept: str,
    region: str,
    score: float,
    findings: list[dict],
    used: list[str],
    failed: list[str],
) -> dict:
    meta = DEPARTMENTS[dept]
    return {
        "dept":       dept,
        "label":      meta["label"],
        "icon":       meta["icon"],
        "weight":     meta["weight"],
        "score":      score,
        "confidence": _conf_label(score),
        "findings":   findings,
        "sources":    used,
        "failed":     failed,
        "sub_scores": {f["source"]: f["score"] for f in findings},
        "region":     region,
        "timestamp":  _ts(),
    }


def _error_result(dept: str, region: str, error: str) -> dict:
    meta = DEPARTMENTS.get(dept, {})
    return {
        "dept":       dept,
        "label":      meta.get("label", dept),
        "icon":       meta.get("icon", "?"),
        "weight":     meta.get("weight", 0.0),
        "score":      0.0,
        "confidence": "none",
        "findings":   [],
        "sources":    [],
        "failed":     ["error"],
        "error":      error,
        "region":     region,
        "timestamp":  _ts(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER SCORE — alle Abteilungen zusammen
# ═══════════════════════════════════════════════════════════════════════════════

DEPT_FUNCTIONS: dict[str, callable] = {
    "OSINT":   osint_score,
    "GEOINT":  geoint_score,
    "SIGINT":  sigint_score,
    "HUMINT":  humint_score,
    "ECONINT": econint_score,
    "HUMANA":  humana_score,
}


def compute_department_scores(
    region:   str,
    depts:    Optional[list[str]] = None,
    parallel: bool = True,
    timeout:  int  = 90,
) -> dict:
    """
    Berechnet Scores für alle (oder gefilterte) Abteilungen.

    Parameters
    ----------
    region   : Zielregion (z.B. "Iran", "Gaza", "Ukraine")
    depts    : Liste der gewünschten Abteilungen, z.B. ["OSINT", "SIGINT"]
               None = alle 6 Abteilungen
    parallel : True = parallele Ausführung via ThreadPoolExecutor (schneller)
               False = sequentiell (besser für Debugging)
    timeout  : Max. Sekunden gesamt für alle parallelen Calls

    Returns
    -------
    {
      region, master_score, master_level, master_icon,
      departments: {dept: result, ...},
      dept_summary: {dept: {score, confidence, icon, sources}, ...},
      active_depts, weights_used, timestamp
    }
    """
    # Ziel-Abteilungen validieren
    target = [d.upper() for d in (depts or list(DEPT_FUNCTIONS.keys()))
              if d.upper() in DEPT_FUNCTIONS]
    if not target:
        raise ValueError(f"Keine gültigen Abteilungen. Gültig: {list(DEPT_FUNCTIONS)}")

    results: dict[str, dict] = {}

    if parallel and len(target) > 1:
        with ThreadPoolExecutor(max_workers=min(6, len(target))) as ex:
            fut_map = {ex.submit(DEPT_FUNCTIONS[d], region): d for d in target}
            for fut in as_completed(fut_map, timeout=timeout + 15):
                d = fut_map[fut]
                try:
                    results[d] = fut.result(timeout=timeout)
                except Exception as e:
                    results[d] = _error_result(d, region, str(e))
        # Timeouts (nicht in as_completed angekommen)
        for d in target:
            if d not in results:
                results[d] = _error_result(d, region, "timeout")
    else:
        for d in target:
            try:
                results[d] = DEPT_FUNCTIONS[d](region)
            except Exception as e:
                results[d] = _error_result(d, region, str(e))

    # ── Master-Score ──────────────────────────────────────────────────────────
    total_w   = sum(DEPARTMENTS[d]["weight"] for d in target)
    weighted  = sum(results[d].get("score", 0.0) * DEPARTMENTS[d]["weight"]
                    for d in target)
    master    = _clamp(weighted / total_w) if total_w > 0 else 0.0

    if   master >= 81: level, icon = "KRITISCH", "⛔"
    elif master >= 61: level, icon = "ROT",       "🔴"
    elif master >= 41: level, icon = "ORANGE",    "🟠"
    elif master >= 21: level, icon = "GELB",      "🟡"
    else:              level, icon = "GRUEN",     "🟢"

    return {
        "region":       region,
        "master_score": round(master, 1),
        "master_level": level,
        "master_icon":  icon,
        "departments":  results,
        "active_depts": target,
        "dept_summary": {
            d: {
                "score":      results[d].get("score", 0.0),
                "confidence": results[d].get("confidence", "none"),
                "icon":       DEPARTMENTS[d]["icon"],
                "label":      DEPARTMENTS[d]["label"],
                "sources":    len(results[d].get("sources", [])),
                "findings":   len(results[d].get("findings", [])),
                "weight_pct": int(DEPARTMENTS[d]["weight"] * 100),
            }
            for d in target
        },
        "weights_used": {d: DEPARTMENTS[d]["weight"] for d in target},
        "timestamp":    _ts(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Formatierter Report
# ═══════════════════════════════════════════════════════════════════════════════

_LEVEL_COLORS = {
    "KRITISCH": "\033[91m",  # Hellrot
    "ROT":      "\033[31m",  # Rot
    "ORANGE":   "\033[33m",  # Gelb/Orange
    "GELB":     "\033[93m",  # Hellgelb
    "GRUEN":    "\033[92m",  # Grün
}
_RESET = "\033[0m"


def format_department_report(result: dict, compact: bool = False,
                             color: bool = True) -> str:
    """
    Gibt formatierten Konsolen-Report aus `compute_department_scores()` zurück.

    Parameters
    ----------
    result  : Rückgabe von compute_department_scores()
    compact : True = nur Score-Zeilen, keine Findings
    color   : True = ANSI-Farben (für Terminal)
    """
    def c(level: str, text: str) -> str:
        if not color: return text
        return _LEVEL_COLORS.get(level, "") + text + _RESET

    master = result["master_score"]
    mlevel = result["master_level"]
    micon  = result["master_icon"]

    lines = [
        "╔══ NEXUS DEPARTMENT REPORT " + "═" * 32 + "╗",
        f"  Region   : {result['region']}",
        f"  {micon} " + c(mlevel, f"MASTER-SCORE: {master:.1f}/100  [{mlevel}]"),
        f"  Abteilungen: {', '.join(result['active_depts'])}",
        f"  Zeitstempel: {result['timestamp'][:19]} UTC",
        "╠" + "═" * 59 + "╣",
    ]

    for dept_name in result["active_depts"]:
        dept = result["departments"].get(dept_name, {})
        meta = DEPARTMENTS.get(dept_name, {})
        score  = dept.get("score", 0.0)
        conf   = dept.get("confidence", "none").upper()
        icon   = meta.get("icon", "")
        weight = int(meta.get("weight", 0) * 100)

        # Score-Balken (20 Zeichen)
        filled = int(score / 5)
        bar    = "█" * filled + "░" * (20 - filled)

        # Level bestimmen für Farbe
        if   score >= 81: dlvl = "KRITISCH"
        elif score >= 61: dlvl = "ROT"
        elif score >= 41: dlvl = "ORANGE"
        elif score >= 21: dlvl = "GELB"
        else:             dlvl = "GRUEN"

        score_str = c(dlvl, f"{score:5.1f}")
        lines.append(
            f"  {icon} {dept_name:<8} │{bar}│ {score_str} [{conf}] (Gew.{weight}%)"
        )

        if not compact:
            for f in dept.get("findings", [])[:2]:
                src = f.get("source", "?")
                typ = f.get("type", "")
                fs  = f.get("score", 0)
                lines.append(f"    └─ {src}: {typ} → +{fs:.0f} pt")
            if dept.get("failed"):
                lines.append(f"    ✗  Fehler: {', '.join(dept['failed'])}")

    lines.append("╚" + "═" * 59 + "╝")
    return "\n".join(lines)


def department_brief(result: dict) -> str:
    """Einzeilige Zusammenfassung für Logs und Watchlist-Alerts."""
    master = result["master_score"]
    icon   = result["master_icon"]
    level  = result["master_level"]
    region = result["region"]
    top    = sorted(result["dept_summary"].items(),
                    key=lambda x: x[1]["score"], reverse=True)
    top3   = ", ".join(f"{d}:{v['score']:.0f}" for d, v in top[:3])
    return (f"[NEXUS] {icon} {region} Master:{master:.1f} [{level}] — "
            f"Top: {top3} | {result['timestamp'][:16]}")


# ═══════════════════════════════════════════════════════════════════════════════
# Direktaufruf / CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse, json, sys

    ap = argparse.ArgumentParser(
        description="NEXUS Department Score Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python nexus_departments.py --region Iran
  python nexus_departments.py --region Gaza --dept OSINT SIGINT
  python nexus_departments.py --region Israel --dept GEOINT --json
  python nexus_departments.py --region Lebanon --seq --compact
        """
    )
    ap.add_argument("--region", default="Iran",
                    help="Zielregion (default: Iran)")
    ap.add_argument("--dept", nargs="*", metavar="DEPT",
                    help=f"Abteilungen filtern: {list(DEPT_FUNCTIONS)}")
    ap.add_argument("--json",    action="store_true",
                    help="JSON-Ausgabe (maschinenlesbar)")
    ap.add_argument("--compact", action="store_true",
                    help="Kompakte Ausgabe ohne Findings")
    ap.add_argument("--seq",     action="store_true",
                    help="Sequentiell statt parallel (für Debugging)")
    ap.add_argument("--no-color", action="store_true",
                    help="Keine ANSI-Farben")
    args = ap.parse_args()

    print(f"[NEXUS Departments] Region: {args.region}", file=sys.stderr)
    if args.dept:
        print(f"[NEXUS Departments] Filter: {args.dept}", file=sys.stderr)

    r = compute_department_scores(
        region=args.region,
        depts=args.dept,
        parallel=not args.seq,
    )

    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_department_report(r, compact=args.compact,
                                       color=not args.no_color))
        print()
        print(department_brief(r))
