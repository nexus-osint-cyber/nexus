"""
nexus_confidence.py – Konfidenz-Scoring pro Aussage / Ereignis

Unterschied zu nexus_dedup.py:
  - nexus_dedup.py:     Gleiche ARTIKEL aus verschiedenen Quellen → Cluster
  - nexus_confidence.py: Einzelne AUSSAGEN/CLAIMS bewerten → Konfidenz

Was dieses Modul liefert:
  1. claim_confidence()   – Konfidenz für eine einzelne Behauptung bewerten
  2. score_articles()     – Liste von Artikeln mit Konfidenz-Labels versehen
  3. confidence_for_llm() – Formatierten Kontext für den LLM generieren

Konfidenz-Stufen (inspiriert von NATO STANAG 2511 / Bellingcat-Standard):
  ✅ BESTÄTIGT      – 3+ unabhängige Quellen, mind. 1 OSINT-Primärquelle
  🔵 WAHRSCHEINLICH – 2 unabhängige Quellen ODER 1 OSINT-Quelle mit Detail
  🟡 MÖGLICH        – 1 Quelle, aber plausibel und zeitlich konsistent
  ⚪ EINZELMELDUNG  – 1 Quelle, keine Bestätigung
  🔴 WIDERSPRÜCHLICH – Quellen widersprechen sich direkt
  ❓ UNBESTÄTIGT    – Gerücht, Social-Media-Behauptung ohne Belege

Einflussfaktoren auf den Score:
  + OSINT-Primärquelle (ISW, Bellingcat, RUSI)       +0.30
  + Mehrere unabhängige Quellen                       +0.20 pro Quelle
  + Spezifische Ortsangabe (GPS oder Ortsname)        +0.10
  + Zeitstempel vorhanden und aktuell (<2h)           +0.05
  + Einheits-/Waffenbezeichnung (z.B. "T-72", "HIMARS") +0.08
  – Staatliche Propagandaquelle (RT, TASS, MOD RU)   -0.40
  – Nur Social Media / Telegram ohne Überprüfung     -0.10
  – Widerspruch zu anderen Quellen                   -0.25
  – "Berichten zufolge", "angeblich", "soll"         -0.08
"""
from __future__ import annotations

import re
from typing import Optional

# ── Quellen-Kategorien ────────────────────────────────────────────────────────

# OSINT-Gold-Standard: Primärquellen mit Verifikations-Methodik
_OSINT_PRIMARY = {
    "ISW Ukraine", "ISW Reports", "Bellingcat", "RUSI",
    "War on the Rocks", "Breaking Defense", "The Insider",
    "GeoConfirmed", "GeoConfirmedUA",
}

# Seriöse westliche Medien (sekundär, gut aber nicht OSINT-Standard)
_MAINSTREAM_RELIABLE = {
    "Reuters World", "Reuters Breaking", "AP News", "BBC World",
    "Guardian World", "DW English", "DW Deutsch", "Defense News",
    "Kyiv Independent", "Meduza EN", "RFE/RL Ukraine", "RFE/RL Russia",
    "Ukraine World", "Al Jazeera EN",
}

# Bekannte Propagandaquellen – stark negative Gewichtung
_PROPAGANDA = {
    "RT", "TASS", "mod_russia", "ukraina_ru", "grey_zone",
    "RIA Novosti", "Sputnik", "CGTN",
}

# Hedging-Ausdrücke: signalisieren Unsicherheit des Autors selbst
_HEDGE_PATTERNS = [
    r"\balleged(?:ly)?\b",
    r"\bapparent(?:ly)?\b",
    r"\breport(?:ed(?:ly)?)?\b",
    r"\bclaim(?:ed|s)?\b",
    r"\bunconfirmed\b",
    r"\bpurported(?:ly)?\b",
    r"\bsupposed(?:ly)?\b",
    r"\bberichten zufolge\b",
    r"\bangeblich\b",
    r"\bsoll(en)?\b",
    r"\bwird berichtet\b",
    r"\bnicht best[äa]tigt\b",
    r"\bunklar\b",
]

# Verifikations-Indikatoren: stärken den Score
_VERIFY_PATTERNS = [
    r"\bconfirmed\b",
    r"\bverified\b",
    r"\bgeoloc(?:ated|ation)\b",
    r"\bGPS\b",
    r"\bcoordinates?\b",
    r"\bsatellite image[ry]?\b",
    r"\bopen.?source\b",
    r"\bOSINT\b",
    r"\bbestätigt\b",
    r"\bverifiziert\b",
    r"\bGeolokal",
]

# Waffensystem-/Einheits-Bezeichnungen → Detailgrad signalisiert Primärquelle
_MILITARY_SPECIFICS = re.compile(
    r"\b(T-\d{2,3}|BMP-\d|BTR-\d|Ka-\d{2}|Mi-\d{2}|Su-\d{2}|MiG-\d{2}|"
    r"HIMARS|MLRS|Shaheed|Shahed|Lancet|Geran|Javelin|MANPADS|"
    r"\d{1,3}\.?\d{0,3}\s*mm|Kalibr|Khinzal|Kinzhal|ATACMS|Storm Shadow|"
    r"\d{1,3}\.\s*Brigade|Bataillon|Regiment|OTG|VSSU|ZSU|VDV|GRU|FSB)\b",
    re.IGNORECASE,
)

# GPS-Koordinaten-Muster
_GPS_PATTERN = re.compile(
    r"\b\d{1,3}\.\d{3,}\s*[,°]\s*\d{1,3}\.\d{3,}\b|"
    r"\b\d{1,2}°\d{1,2}[\'′]\d{0,2}[\"″]?\s*[NS][,\s]+\d{1,3}°\d{1,2}[\'′]\d{0,2}[\"″]?\s*[EW]\b"
)


def _count_hedges(text: str) -> int:
    """Zählt Hedging-Ausdrücke im Text."""
    count = 0
    text_lower = text.lower()
    for pattern in _HEDGE_PATTERNS:
        if re.search(pattern, text_lower):
            count += 1
    return count


def _count_verifications(text: str) -> int:
    """Zählt Verifikations-Indikatoren im Text."""
    count = 0
    text_lower = text.lower()
    for pattern in _VERIFY_PATTERNS:
        if re.search(pattern, text_lower):
            count += 1
    return count


def claim_confidence(
    title: str,
    text: str = "",
    source: str = "",
    corroborating_sources: Optional[list[str]] = None,
    age_min: int = 9999,
) -> dict:
    """
    Bewertet die Konfidenz einer einzelnen Behauptung.

    Returns:
        dict mit:
          score:       float 0.0–1.0
          level:       str ("BESTÄTIGT" / "WAHRSCHEINLICH" / usw.)
          badge:       str (Emoji)
          reasons:     list[str] – warum dieser Score
          hedged:      bool – Autor selbst unsicher?
    """
    full_text = f"{title} {text}".strip()
    reasons: list[str] = []
    score = 0.40  # Basis-Score für eine Meldung mit Quelle

    corr = corroborating_sources or []

    # ── Quellenqualität ──────────────────────────────────────────────────────
    if source in _OSINT_PRIMARY:
        score += 0.30
        reasons.append(f"OSINT-Primärquelle: {source}")
    elif source in _MAINSTREAM_RELIABLE:
        score += 0.15
        reasons.append(f"Seriöse Quelle: {source}")
    elif source in _PROPAGANDA:
        score -= 0.40
        reasons.append(f"Propagandaquelle: {source} – Skepsis angebracht")

    # ── Bestätigungen durch weitere Quellen ──────────────────────────────────
    osint_corr = [s for s in corr if s in _OSINT_PRIMARY]
    mainstream_corr = [s for s in corr if s in _MAINSTREAM_RELIABLE]

    for s in osint_corr:
        score += 0.25
        reasons.append(f"OSINT-Bestätigung: {s}")
    for s in mainstream_corr[:3]:
        score += 0.12
        reasons.append(f"Medien-Bestätigung: {s}")
    if len(corr) > 3:
        score += 0.05
        reasons.append(f"+{len(corr) - 3} weitere Quellen")

    # ── Text-Analyse ─────────────────────────────────────────────────────────
    # GPS-Koordinaten im Text?
    if _GPS_PATTERN.search(full_text):
        score += 0.12
        reasons.append("GPS-Koordinaten angegeben")

    # Waffensystem-/Einheitsbezeichnung?
    mil_matches = _MILITARY_SPECIFICS.findall(full_text)
    if mil_matches:
        score += 0.08
        reasons.append(f"Spezifische Bezeichnungen: {', '.join(set(mil_matches[:3]))}")

    # Verifikations-Indikatoren?
    n_verify = _count_verifications(full_text)
    if n_verify >= 2:
        score += 0.10
        reasons.append(f"Verifikationshinweise ({n_verify}x)")
    elif n_verify == 1:
        score += 0.05
        reasons.append("Verifikationshinweis")

    # Hedging-Ausdrücke?
    n_hedge = _count_hedges(full_text)
    hedged = n_hedge > 0
    if n_hedge >= 2:
        score -= 0.15
        reasons.append(f"Starkes Hedging ({n_hedge} Ausdrücke: unsichere Quelle)")
    elif n_hedge == 1:
        score -= 0.08
        reasons.append("Hedging-Ausdruck vorhanden")

    # Aktualität
    if age_min < 30:
        score += 0.05
        reasons.append("Sehr frische Meldung (<30min)")
    elif age_min > 360:
        score -= 0.05

    # Score begrenzen
    score = max(0.0, min(1.0, score))

    # ── Level bestimmen ──────────────────────────────────────────────────────
    n_unique = 1 + len(set(corr))
    has_osint_anywhere = (source in _OSINT_PRIMARY) or bool(osint_corr)

    if score >= 0.85 and n_unique >= 3:
        level = "BESTÄTIGT"
        badge = "✅"
    elif score >= 0.70 or (n_unique >= 2 and has_osint_anywhere):
        level = "WAHRSCHEINLICH"
        badge = "🔵"
    elif score >= 0.50 and not hedged:
        level = "MÖGLICH"
        badge = "🟡"
    elif source in _PROPAGANDA:
        level = "UNBESTÄTIGT"
        badge = "❓"
    elif score < 0.25:
        level = "UNBESTÄTIGT"
        badge = "❓"
    else:
        level = "EINZELMELDUNG"
        badge = "⚪"

    return {
        "score":   round(score, 3),
        "level":   level,
        "badge":   badge,
        "reasons": reasons,
        "hedged":  hedged,
        "n_sources": n_unique,
    }


def score_articles(articles: list[dict]) -> list[dict]:
    """
    Versieht eine Artikel-Liste mit Konfidenz-Labels.
    Berücksichtigt Dedup-Metadaten (corroborating, cluster_size) wenn vorhanden.

    Input:  Liste von Article-Dicts
    Output: Dieselbe Liste, jeder Artikel mit neuen Keys:
              conf_score, conf_level, conf_badge, conf_reasons, conf_hedged
    """
    result = []
    for a in articles:
        title  = a.get("title", "")
        text   = a.get("summary", "") or a.get("text", "")
        source = a.get("source", "")
        age    = a.get("age_min", 9999)

        # Dedup-Bestätigungen nutzen wenn vorhanden
        corr = a.get("corroborating", [])

        conf = claim_confidence(
            title=title,
            text=text,
            source=source,
            corroborating_sources=corr,
            age_min=age,
        )

        enriched = dict(a)
        enriched["conf_score"]   = conf["score"]
        enriched["conf_level"]   = conf["level"]
        enriched["conf_badge"]   = conf["badge"]
        enriched["conf_reasons"] = conf["reasons"]
        enriched["conf_hedged"]  = conf["hedged"]
        result.append(enriched)

    return result


def confidence_for_llm(articles: list[dict], max_articles: int = 40) -> str:
    """
    Gibt LLM-optimierten Kontext aus konfidenz-bewerteten Artikeln zurück.

    Format:
      BESTÄTIGTE/WAHRSCHEINLICHE Ereignisse zuerst
      Danach MÖGLICHE / EINZELMELDUNGEN
      Propaganda und UNBESTÄTIGT am Ende mit klarer Warnung

    Jeder Artikel zeigt Badge + Quellenangabe + Konfidenz-Grund.
    """
    if not articles:
        return "[KONFIDENZ] Keine Artikel"

    scored = score_articles(articles)

    _LEVEL_ORDER = {
        "BESTÄTIGT":    0,
        "WAHRSCHEINLICH": 1,
        "MÖGLICH":      2,
        "EINZELMELDUNG": 3,
        "UNBESTÄTIGT":  4,
        "WIDERSPRÜCHLICH": 5,
    }

    scored.sort(key=lambda a: (
        _LEVEL_ORDER.get(a.get("conf_level", "EINZELMELDUNG"), 3),
        a.get("age_min", 9999),
    ))

    n_conf    = sum(1 for a in scored if a["conf_level"] == "BESTÄTIGT")
    n_prob    = sum(1 for a in scored if a["conf_level"] == "WAHRSCHEINLICH")
    n_single  = sum(1 for a in scored if "EINZELMELDUNG" in a["conf_level"])
    n_unreli  = sum(1 for a in scored if a["conf_level"] in ("UNBESTÄTIGT", "WIDERSPRÜCHLICH"))

    lines = [
        f"[KONFIDENZ-BEWERTUNG | {len(scored)} Meldungen]",
        f"  ✅ BESTÄTIGT: {n_conf}  |  🔵 WAHRSCHEINLICH: {n_prob}  |  ⚪ EINZELMELDUNG: {n_single}  |  ❓ UNBESTÄTIGT: {n_unreli}",
        "",
    ]

    for a in scored[:max_articles]:
        badge  = a.get("conf_badge", "⚪")
        level  = a.get("conf_level", "EINZELMELDUNG")
        src    = a.get("source", "?")
        title  = (a.get("title") or "")[:110]
        text   = (a.get("summary") or a.get("text") or "")[:200].strip()
        age    = a.get("age_min", 9999)
        score  = a.get("conf_score", 0.5)
        corr   = a.get("corroborating", [])

        age_str = f"{age}min" if age < 120 else f"{age // 60}h"
        score_str = f"{int(score * 100)}%"

        line = f"{badge} [{level} {score_str}] [{src}] {title} ({age_str})"
        if corr:
            line += f"\n   Bestätigt von: {', '.join(corr[:4])}"
        if text and text[:40] not in title:
            line += f"\n   {text}"

        # Warnung bei Propaganda
        if a.get("conf_level") == "UNBESTÄTIGT" and src in _PROPAGANDA:
            line += f"\n   ⚠ PROPAGANDAQUELLE – nur für Narrativ-Analyse verwenden"

        lines.append(line)

    return "\n".join(lines)


def confidence_summary(articles: list[dict]) -> str:
    """Kurze Zusammenfassung für Terminal-Ausgabe."""
    if not articles:
        return "[KONFIDENZ] Keine Artikel"
    scored = score_articles(articles)
    n = len(scored)
    levels = {}
    for a in scored:
        lvl = a.get("conf_level", "EINZELMELDUNG")
        levels[lvl] = levels.get(lvl, 0) + 1
    parts = [f"{lvl}: {count}" for lvl, count in sorted(levels.items())]
    return f"[KONFIDENZ] {n} Meldungen – " + " | ".join(parts)


if __name__ == "__main__":
    test_articles = [
        {
            "title": "Ukrainian forces strike Russian supply depot near Luhansk",
            "summary": "Confirmed by geolocated satellite imagery. GPS coordinates: 48.566, 39.340",
            "source": "Bellingcat",
            "age_min": 45,
            "corroborating": ["Reuters World", "Kyiv Independent", "@GeoConfirmedUA"],
        },
        {
            "title": "Allegedly Russian troops massing near border, reports claim",
            "summary": "Unconfirmed reports suggest possible buildup",
            "source": "r/ukraine",
            "age_min": 120,
            "corroborating": [],
        },
        {
            "title": "ISW: Frontline situation near Avdiivka remains contested",
            "summary": "T-72 tanks and BMP-2 infantry fighting vehicles confirmed via OSINT",
            "source": "ISW Ukraine",
            "age_min": 60,
            "corroborating": ["RUSI", "Defense News"],
        },
        {
            "title": "Russia claims major victory in Kharkiv region",
            "summary": "Ministry of Defense announces capture of strategic positions",
            "source": "TASS",
            "age_min": 30,
            "corroborating": ["RT"],
        },
    ]

    print(confidence_summary(test_articles))
    print()
    print(confidence_for_llm(test_articles))
