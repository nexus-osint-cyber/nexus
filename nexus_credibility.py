"""
NEXUS - Quellen-Glaubwürdigkeits-Modul
Bewertet die Zuverlässigkeit jeder Nachrichtenquelle auf einer Skala 1-10.

Methode:
  1. Bekannte Quellen-Datenbank (manuell kuratiert + MediaBiasFactCheck)
  2. Muster-Erkennung (staatliche Medien, Propaganda-Indikatoren)
  3. Quell-Typ-Gewichtung (Reuters/AP > Blog > anonymer Telegram-Kanal)

Skala:
  9-10  Sehr zuverlässig (Reuters, AP, BBC, DW)
  7-8   Zuverlässig (seriöse Regionalmedien, etablierte OSINT-Konten)
  5-6   Mittel (parteiische aber transparente Medien, Reddit-Threads)
  3-4   Niedrig (staatlich beeinflusst, bekannte Bias, unverifizierbarer Telegram)
  1-2   Sehr niedrig (Propagandamedien, bekannte Desinformation)
"""

from __future__ import annotations
from typing import Optional
import re

# ── Bekannte Quellen-Datenbank ─────────────────────────────────────────────────
# Format: "Quellname (lowercase, teilstring)" → (score, bias_label, notes)
_SOURCE_DB: dict[str, tuple[int, str, str]] = {

    # ── Nachrichtenagenturen (9-10) ──────────────────────────────────────────
    "reuters":          (10, "neutral",     "Internationale Nachrichtenagentur, strenge Redaktionsstandards"),
    "ap ":              (10, "neutral",     "Associated Press – Branchenstandard für Faktentreue"),
    "afp":              (10, "neutral",     "Agence France-Presse"),
    "dpa":              (9,  "neutral",     "Deutsche Presse-Agentur"),
    "apa":              (9,  "neutral",     "Austria Presse Agentur"),

    # ── Internationale Qualitätsmedien (8-9) ─────────────────────────────────
    "bbc":              (9,  "leicht-links","BBC, öffentlich-rechtlich UK, hohe Standards"),
    "dw ":              (9,  "neutral",     "Deutsche Welle, Auslandsrundfunk"),
    "deutschewelle":    (9,  "neutral",     "Deutsche Welle"),
    "ard":              (8,  "neutral",     "ARD, öffentlich-rechtlich DE"),
    "zdf":              (8,  "neutral",     "ZDF, öffentlich-rechtlich DE"),
    "guardian":         (8,  "links",       "The Guardian, Qualitätsjournalismus aber links"),
    "nytimes":          (8,  "leicht-links","New York Times"),
    "wsj":              (8,  "leicht-rechts","Wall Street Journal"),
    "economist":        (8,  "zentrum",     "The Economist"),
    "aljazeera":        (7,  "qatar-nah",   "Al Jazeera – gut aber Katar-Einfluss beachten"),
    "france24":         (8,  "neutral",     "France 24, staatlich aber editoriell unabhängig"),
    "euronews":         (7,  "neutral",     "Euronews"),

    # ── OSINT-Spezialisten (7-9) ──────────────────────────────────────────────
    "bellingcat":       (9,  "osint",       "Führende OSINT-Organisation, verifizierte Methoden"),
    "geconfirmed":      (8,  "osint",       "Geolokation-spezialisiert, verifizierte Koordinaten"),
    "osint technical":  (7,  "osint",       "OSINT-Community, variabel"),
    "citeam":           (7,  "osint",       "CI Team OSINT"),
    "intelslava":       (5,  "pro-russland","Intel Slava Z – bekannte russische Tendenz"),

    # ── Ukrainische / Westliche Kriegsberichterstatter (6-8) ─────────────────
    "wartranslated":    (7,  "neutral",     "War Translated – Übersetzungen, meist genau"),
    "militarysummary":  (6,  "pro-ukraine", "Military Summary – Ukraine-nah"),
    "nexta":            (7,  "pro-belarus", "NEXTA – Belarus-Opposition"),
    "ukrainenow":       (6,  "pro-ukraine", "Ukraine Now – offiziell Ukraine-nah"),

    # ── Russische / Pro-russische Quellen (2-4) ───────────────────────────────
    "rt ":              (2,  "russland",    "Russia Today – staatliche Propaganda"),
    "russia today":     (2,  "russland",    "Staatliche Propaganda"),
    "sputnik":          (2,  "russland",    "Sputnik News – Propaganda"),
    "tass":             (3,  "russland",    "TASS – russische Staatsagentur"),
    "ria novosti":      (2,  "russland",    "RIA Novosti – Propaganda"),
    "rybar":            (3,  "pro-russland","Rybar Telegram – militärische Details aber russische Sicht"),

    # ── Naher Osten / Arabische Welt (5-7) ───────────────────────────────────
    "middle east eye":  (7,  "pro-palästina","Middle East Eye – gute Berichte aber Bias"),
    "haaretz":          (7,  "israel-links", "Haaretz – israelische Linke, kritisch"),
    "times of israel":  (6,  "israel-nah",   "Times of Israel"),
    "arab news":        (5,  "saudi",         "Arab News – saudi-arabische Einflüsse"),
    "conflict news":    (6,  "neutral",       "Conflict News Telegram"),

    # ── Reddit (5-7 je nach Subreddit) ───────────────────────────────────────
    "reddit/r/worldnews":       (6, "variabel", "r/worldnews – meist verlässlich, aber unmoderiert"),
    "reddit/r/ukraine":         (5, "pro-ukraine","r/ukraine – starke Ukraine-Tendenz"),
    "reddit/r/credibledefense": (7, "neutral",  "r/CredibleDefense – Militärexperten"),
    "reddit/r/geopolitics":     (6, "neutral",  "r/geopolitics – Diskussion"),
    "reddit/r/ukrainewarvideorepor": (5, "pro-ukraine", "r/UkraineWarVideoReport – Tendenz"),

    # ── Generische Typen ──────────────────────────────────────────────────────
    "telegram/":        (4,  "unbekannt",   "Telegram-Kanal – unverifiziert, Vorsicht"),
    "reddit/":          (5,  "variabel",    "Reddit – variiert je nach Subreddit"),
    "gdelt/":           (6,  "neutral",     "GDELT – aggregiert aus Medien, automatisch"),
    "rss/":             (6,  "variabel",    "RSS-Feed"),
}

# ── Muster für automatische Typ-Erkennung ─────────────────────────────────────
_STATE_MEDIA_PATTERNS = [
    r"\brt\b", r"russia today", r"sputnik", r"xinhua", r"cctv",
    r"al-manar", r"press tv", r"hispan tv", r"telesur",
]
_OFFICIAL_GOV_PATTERNS = [
    r"mod\.ru", r"kremlin", r"ministry of defense",
    r"idf\.il", r"mod_ukraine",
]


def score_source(source_name: str) -> dict:
    """
    Bewertet eine Quelle und gibt dict zurück:
    {score, bias, label, color, notes}
    score: 1-10
    color: hex-Farbe für UI
    """
    s = source_name.lower().strip()

    # 1. Direktsuche in Datenbank
    for key, (score, bias, notes) in _SOURCE_DB.items():
        if key in s:
            return _build_result(score, bias, notes, source_name)

    # 2. Muster-Checks
    for pat in _STATE_MEDIA_PATTERNS:
        if re.search(pat, s):
            return _build_result(2, "staatliche-propaganda",
                                 "Staatliche Propaganda erkannt", source_name)

    for pat in _OFFICIAL_GOV_PATTERNS:
        if re.search(pat, s):
            return _build_result(4, "offiziell-gov",
                                 "Offizielles Regierungsmedium", source_name)

    # 3. Typ-Fallbacks
    if "telegram/" in s:
        return _build_result(4, "unbekannt", "Unbekannter Telegram-Kanal", source_name)
    if "reddit/" in s:
        return _build_result(5, "variabel", "Reddit-Community", source_name)
    if "gdelt" in s:
        return _build_result(6, "neutral", "GDELT-Aggregat", source_name)

    # 4. Unbekannte Quelle
    return _build_result(5, "unbekannt", "Unbekannte Quelle", source_name)


def _build_result(score: int, bias: str, notes: str, source: str) -> dict:
    if score >= 8:
        color = "#00ff88"
        label = "✓ Zuverlässig"
    elif score >= 6:
        color = "#ffd700"
        label = "~ Mittel"
    elif score >= 4:
        color = "#ff8800"
        label = "⚠ Niedrig"
    else:
        color = "#ff4444"
        label = "✗ Propaganda"

    return {
        "score":  score,
        "bias":   bias,
        "label":  label,
        "color":  color,
        "notes":  notes,
        "source": source,
    }


def enrich_articles(articles: list[dict]) -> list[dict]:
    """
    Fügt jedem Artikel einen Credibility-Score hinzu.
    Gibt die Liste sortiert nach Score × Frische zurück.
    """
    enriched = []
    for a in articles:
        src = a.get("source", "")
        cred = score_source(src)
        a = dict(a)  # Kopie
        a["credibility"] = cred
        enriched.append(a)

    # Ranking: Score × Frische (neuere + glaubwürdigere zuerst)
    def _rank(art: dict) -> float:
        age   = art.get("age_min", 9999)
        score = art["credibility"]["score"]
        return age / max(1, score)  # Kleiner = besser

    enriched.sort(key=_rank)
    return enriched


def credibility_context(articles: list[dict]) -> str:
    """Baut LLM-Kontext mit Credibility-Hinweisen."""
    enriched = enrich_articles(articles)
    if not enriched:
        return ""

    lines = ["[QUELLEN-GLAUBWÜRDIGKEIT]"]
    low_cred = [a for a in enriched if a["credibility"]["score"] <= 4]
    high_cred = [a for a in enriched if a["credibility"]["score"] >= 8]

    if low_cred:
        lines.append(f"⚠ {len(low_cred)} Artikel von niedrig-glaubwürdigen Quellen (Propaganda/unbekannt):")
        for a in low_cred[:3]:
            lines.append(f"  [{a['credibility']['score']}/10 – {a['credibility']['bias']}] {a.get('source','')} → {a.get('title','')[:60]}")

    if high_cred:
        lines.append(f"✓ {len(high_cred)} Artikel von zuverlässigen Quellen:")
        for a in high_cred[:3]:
            lines.append(f"  [{a['credibility']['score']}/10] {a.get('source','')} → {a.get('title','')[:60]}")

    lines.append("\nBitte berücksichtige die Quelle bei deiner Analyse. Propaganda-Quellen ggf. explizit kennzeichnen.")
    return "\n".join(lines)


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    test_sources = [
        "Reuters", "RT News", "Telegram/Intel Slava Z",
        "Reddit/r/worldnews", "BBC", "Sputnik",
        "Bellingcat", "Telegram/rybar", "Reddit/r/CredibleDefense",
        "GDELT/news", "Unknown Source",
    ]
    print("NEXUS Quellen-Credibility Test\n" + "─" * 40)
    for src in test_sources:
        r = score_source(src)
        bar = "█" * r["score"] + "░" * (10 - r["score"])
        print(f"  {bar} {r['score']:2}/10  {r['label']:<18}  {src}")
