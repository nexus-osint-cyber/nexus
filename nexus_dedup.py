"""
nexus_dedup.py – Event-Deduplication Engine

Problem:
  Dasselbe Ereignis wird von Reuters, Telegram, Reddit und BBC gleichzeitig gemeldet.
  Ohne Deduplication sieht das LLM 4x dasselbe Ereignis und schätzt es als viermal
  so bedeutsam ein – das ist fundamental falsch.

Lösung:
  1. TF-IDF Ähnlichkeit zwischen Titeln (manuell implementiert, keine Deps)
  2. Zeitlicher Overlap (±90 Minuten Fenster)
  3. GPS-Clustering (optional, ±40km wenn beide Artikel Koordinaten haben)

Ausgabe:
  Jeder Artikel bekommt neue Felder:
    cluster_id:          Integer, Cluster-Nummer (gleiche Zahl = gleiches Ereignis)
    corroborating:       Liste der anderen Quellen die dasselbe meldeten
    corroborating_count: Anzahl unabhängiger Quellen
    confidence:          BESTÄTIGT (3+) / WAHRSCHEINLICH (2) / EINZELMELDUNG (1) / UNBESTÄTIGT
    is_canonical:        True wenn dies der beste Artikel im Cluster ist
    duplicate_of:        Index des kanonischen Artikels wenn is_canonical=False

Wichtig:
  deduplicate() gibt ALLE Artikel zurück, nicht nur kanonische.
  Der Aufrufer entscheidet ob er nur is_canonical=True filtert oder alle zeigt.
  So bleiben alle Quellinformationen erhalten.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

# ── Stopwörter (Deutsch + Englisch) ──────────────────────────────────────────
_STOPWORDS = {
    # Englisch
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "will", "would", "could", "should", "may", "might",
    "that", "this", "these", "those", "it", "its", "as", "if", "after",
    "before", "during", "over", "under", "after", "into", "through", "about",
    "than", "then", "when", "where", "who", "what", "how", "why",
    # Deutsch
    "der", "die", "das", "ein", "eine", "und", "oder", "aber", "in", "an",
    "auf", "zu", "für", "von", "mit", "bei", "nach", "aus", "über", "unter",
    "ist", "sind", "war", "waren", "hat", "haben", "wird", "worden",
    "sich", "auch", "als", "so", "wie", "noch", "schon", "mehr", "nicht",
    "beim", "dem", "den", "des", "einem", "einer", "eines", "im", "am",
}

# Ähnlichkeits-Schwellenwert (0.0–1.0)
# 0.30 erkennt Variationen desselben Events  (z.B. "strike on depot" / "hit ammo depot")
# 0.50 wäre nur fast-wörtliche Duplikate
SIMILARITY_THRESHOLD = 0.32

# Zeitfenster: Ereignisse innerhalb dieser Spanne gelten als gleichzeitig
MAX_AGE_DIFF_MIN = 90

# GPS-Cluster: max. Entfernung in km (nur wenn beide Artikel Koordinaten haben)
MAX_GPS_KM = 40.0

# Quellen-Vertrauens-Ranking für Canonical-Auswahl
_SOURCE_TRUST: dict[str, float] = {
    # OSINT-Gold-Standard
    "ISW Ukraine":       0.90,
    "ISW Reports":       0.90,
    "Bellingcat":        0.92,
    "RUSI":              0.88,
    "War on the Rocks":  0.85,
    "Breaking Defense":  0.80,
    # Unabhängige Investigativ-Medien
    "Kyiv Independent":  0.78,
    "Meduza EN":         0.82,
    "The Insider":       0.80,
    "RFE/RL Ukraine":    0.78,
    "RFE/RL Russia":     0.75,
    # Große westliche Medien
    "Reuters World":     0.75,
    "Reuters Breaking":  0.75,
    "BBC World":         0.73,
    "Guardian World":    0.72,
    "AP News":           0.74,
    "DW English":        0.70,
    "DW Deutsch":        0.70,
    "Defense News":      0.78,
    # Regionale Medien
    "Ukraine World":     0.72,
    "Al Jazeera EN":     0.65,
    "Kyodo News":        0.65,
    # Propaganda (niedrig, aber nicht ignoriert)
    "RT":                0.10,
    "TASS":              0.15,
}


# ── TF-IDF Implementierung (keine Bibliotheken nötig) ─────────────────────────

def _tokenize(text: str) -> list[str]:
    """Zerlegt Text in Tokens: Kleinbuchstaben, nur Wörter ≥3 Zeichen, keine Stopwörter."""
    tokens = re.findall(r"[a-zA-ZäöüÄÖÜß]{3,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _tf(tokens: list[str]) -> dict[str, float]:
    """Term Frequency."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {word: count / total for word, count in counts.items()}


def _idf(all_token_sets: list[list[str]]) -> dict[str, float]:
    """Inverse Document Frequency über alle Artikel."""
    n = len(all_token_sets)
    if n == 0:
        return {}
    doc_freq: Counter = Counter()
    for tokens in all_token_sets:
        for word in set(tokens):
            doc_freq[word] += 1
    return {
        word: math.log((n + 1) / (freq + 1)) + 1.0
        for word, freq in doc_freq.items()
    }


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """TF-IDF Vektor als dict {word: score}."""
    tf = _tf(tokens)
    return {word: tf_val * idf.get(word, 0.0) for word, tf_val in tf.items()}


def _cosine_similarity(v1: dict[str, float], v2: dict[str, float]) -> float:
    """Kosinus-Ähnlichkeit zwischen zwei Vektoren."""
    if not v1 or not v2:
        return 0.0
    dot = sum(v1.get(word, 0.0) * v2.get(word, 0.0) for word in v1)
    norm1 = math.sqrt(sum(x * x for x in v1.values()))
    norm2 = math.sqrt(sum(x * x for x in v2.values()))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Entfernung zwischen zwei GPS-Koordinaten in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _source_trust(article: dict) -> float:
    """Gibt den Trust-Score einer Quelle zurück."""
    # Expliziter credibility_score hat Vorrang
    if "credibility_score" in article:
        return float(article["credibility_score"])
    src = article.get("source", "")
    return _SOURCE_TRUST.get(src, 0.55)


def _are_same_event(a1: dict, a2: dict,
                    vec1: dict[str, float], vec2: dict[str, float]) -> bool:
    """
    Entscheidet ob zwei Artikel dasselbe Ereignis beschreiben.

    Kriterien:
      0. Identische URL  → sofort True (härtestes Duplikat-Signal)
      1. Textuell ähnlich (TF-IDF Kosinus ≥ SIMILARITY_THRESHOLD)
      2. Zeitlich nah (age_min Differenz ≤ MAX_AGE_DIFF_MIN)
      3. GPS-Konsistent (wenn beide Koordinaten haben: ≤ MAX_GPS_KM)
    """
    # 0. Identische URL → immer Duplikat
    url1 = (a1.get("url") or "").strip()
    url2 = (a2.get("url") or "").strip()
    if url1 and url2 and url1 == url2:
        return True

    # 1. Text-Ähnlichkeit
    sim = _cosine_similarity(vec1, vec2)
    if sim < SIMILARITY_THRESHOLD:
        return False

    # 2. Zeitliche Nähe
    age1 = a1.get("age_min", 9999)
    age2 = a2.get("age_min", 9999)
    if abs(age1 - age2) > MAX_AGE_DIFF_MIN:
        return False

    # 3. GPS-Plausibilität (optional)
    lat1, lon1 = a1.get("lat"), a1.get("lon")
    lat2, lon2 = a2.get("lat"), a2.get("lon")
    if lat1 and lon1 and lat2 and lon2:
        try:
            dist = _haversine_km(float(lat1), float(lon1), float(lat2), float(lon2))
            if dist > MAX_GPS_KM:
                return False
        except (TypeError, ValueError):
            pass  # Ungültige Koordinaten → ignorieren

    return True


def _assign_confidence(n_sources: int, has_osint: bool) -> str:
    """Konfidenz-Label basierend auf Quellenanzahl."""
    if n_sources >= 3:
        return "BESTÄTIGT"
    if n_sources == 2:
        return "WAHRSCHEINLICH"
    if has_osint:
        return "EINZELMELDUNG-OSINT"
    return "EINZELMELDUNG"


def deduplicate(articles: list[dict],
                similarity_threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    """
    Hauptfunktion: Gruppiert Duplikate und gibt angereicherte Artikel zurück.

    Jeder Artikel bekommt folgende neue Felder:
      cluster_id, corroborating, corroborating_count, confidence,
      is_canonical, duplicate_of (Index des kanonischen Artikels)

    Input/Output: Liste von Article-Dicts (in-place-Kopie, Original unverändert).
    """
    if not articles:
        return []

    # Tiefe Kopie um Originale nicht zu verändern
    arts = [dict(a) for a in articles]
    n = len(arts)

    # ── TF-IDF vorbereiten ───────────────────────────────────────────────────
    # Kombiniere Titel + Summary für bessere Trefferquote
    texts = [
        (a.get("title") or "") + " " + (a.get("summary") or "")[:200]
        for a in arts
    ]
    all_tokens = [_tokenize(t) for t in texts]
    idf_map   = _idf(all_tokens)
    vectors   = [_tfidf_vector(toks, idf_map) for toks in all_tokens]

    # ── Union-Find für Cluster-Zuweisung ─────────────────────────────────────
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Paarweise Ähnlichkeit (O(n²) – bei 60 Artikeln: 3600 Vergleiche, OK)
    for i in range(n):
        for j in range(i + 1, n):
            if _are_same_event(arts[i], arts[j], vectors[i], vectors[j]):
                union(i, j)

    # ── Cluster-Gruppen aufbauen ─────────────────────────────────────────────
    from collections import defaultdict
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    # ── Kanonischen Artikel pro Cluster wählen ───────────────────────────────
    # Kriterien: höchster Trust-Score → ältester (kleinster age_min) → kürzester Titel
    canonical: dict[int, int] = {}
    for root, members in clusters.items():
        best = max(members, key=lambda i: (
            _source_trust(arts[i]),          # höchster Trust zuerst
            -arts[i].get("age_min", 9999),   # aktuellster (negiert, da max)
        ))
        canonical[root] = best

    # ── Artikel mit Cluster-Metadaten anreichern ─────────────────────────────
    _OSINT_SOURCES = {
        "ISW Ukraine", "ISW Reports", "Bellingcat", "RUSI",
        "War on the Rocks", "Breaking Defense", "Kyiv Independent",
        "Meduza EN", "The Insider",
    }

    for i in range(n):
        root   = find(i)
        members = clusters[root]
        canon_i = canonical[root]

        corr_sources = [
            arts[j].get("source", "?")
            for j in members
            if j != i
        ]
        n_unique_sources = len({arts[j].get("source", "") for j in members})
        has_osint = any(
            arts[j].get("source", "") in _OSINT_SOURCES for j in members
        )

        arts[i]["cluster_id"]          = root
        arts[i]["corroborating"]        = corr_sources
        arts[i]["corroborating_count"]  = n_unique_sources - 1
        arts[i]["confidence"]           = _assign_confidence(n_unique_sources, has_osint)
        arts[i]["is_canonical"]         = (i == canon_i)
        arts[i]["duplicate_of"]         = canon_i if i != canon_i else None
        arts[i]["cluster_size"]         = len(members)

    return arts


def get_canonical(articles: list[dict]) -> list[dict]:
    """
    Gibt nur die kanonischen Artikel zurück (bestes Exemplar pro Cluster).
    Nützlich wenn man 60 Artikel auf ~20 eindeutige Ereignisse reduzieren will.
    """
    deduped = deduplicate(articles)
    return [a for a in deduped if a.get("is_canonical", True)]


def dedup_summary(articles: list[dict]) -> str:
    """Terminal-Zusammenfassung der Deduplication für Debugging."""
    if not articles:
        return "[DEDUP] Keine Artikel"

    deduped = deduplicate(articles)
    canonical = [a for a in deduped if a.get("is_canonical")]
    confirmed = [a for a in canonical if a["confidence"] == "BESTÄTIGT"]
    probable  = [a for a in canonical if a["confidence"] == "WAHRSCHEINLICH"]
    single    = [a for a in canonical if "EINZELMELDUNG" in a["confidence"]]

    n_before = len(articles)
    n_after  = len(canonical)
    removed  = n_before - n_after

    lines = [
        f"[DEDUP] {n_before} Artikel → {n_after} eindeutige Ereignisse "
        f"(-{removed} Duplikate entfernt)",
        f"  BESTÄTIGT (3+ Quellen): {len(confirmed)}",
        f"  WAHRSCHEINLICH (2 Quellen): {len(probable)}",
        f"  EINZELMELDUNG: {len(single)}",
    ]
    if confirmed:
        lines.append("  Top BESTÄTIGTE Ereignisse:")
        for a in confirmed[:5]:
            corr = ", ".join(a.get("corroborating", [])[:3])
            lines.append(f"    ✅ [{a.get('source','')}] {a.get('title','')[:70]}")
            lines.append(f"       Bestätigt von: {corr}")
    return "\n".join(lines)


def deduplicated_for_llm(articles: list[dict], max_articles: int = 50) -> str:
    """
    Gibt LLM-optimierten Kontext aus deduplizierten Artikeln zurück.

    Format:
      BESTÄTIGTE Ereignisse zuerst (3+ Quellen)
      Dann WAHRSCHEINLICHE (2 Quellen)
      Dann EINZELMELDUNGEN

    Jeder Artikel zeigt welche Quellen bestätigt haben.
    """
    if not articles:
        return "[DEDUP] Keine Artikel"

    deduped = deduplicate(articles)
    canonical = [a for a in deduped if a.get("is_canonical")]

    # Sortierung: Konfidenz > Aktualität
    _CONF_ORDER = {
        "BESTÄTIGT": 0,
        "WAHRSCHEINLICH": 1,
        "EINZELMELDUNG-OSINT": 2,
        "EINZELMELDUNG": 3,
    }
    canonical.sort(key=lambda a: (
        _CONF_ORDER.get(a.get("confidence", "EINZELMELDUNG"), 3),
        a.get("age_min", 9999),
    ))

    lines = [
        f"[DEDUPLIZIERTE NACHRICHTEN | {len(canonical)} Ereignisse aus {len(articles)} Meldungen]",
        "",
    ]

    for a in canonical[:max_articles]:
        conf   = a.get("confidence", "EINZELMELDUNG")
        src    = a.get("source", "?")
        title  = (a.get("title") or "")[:120]
        text   = (a.get("summary") or a.get("text") or "")[:250].strip()
        age    = a.get("age_min", 9999)
        corr   = a.get("corroborating", [])
        n_src  = a.get("cluster_size", 1)

        # Konfidenz-Badge
        badge = {"BESTÄTIGT": "✅", "WAHRSCHEINLICH": "🔵", "EINZELMELDUNG-OSINT": "🟡", "EINZELMELDUNG": "⚪"}.get(conf, "⚪")

        age_str = f"{age}min" if age < 120 else f"{age // 60}h"

        line = f"{badge} [{conf}] [{src}] {title} ({age_str})"
        if corr:
            line += f"\n   Bestätigt von: {', '.join(corr[:5])}"
        if text and text[:50] not in title:
            line += f"\n   {text}"

        lines.append(line)

    return "\n".join(lines)


if __name__ == "__main__":
    # Schnell-Test mit Demo-Artikeln
    test_articles = [
        {"title": "Ukraine forces strike Russian supply depot near Luhansk",
         "source": "Reuters World", "age_min": 30, "summary": "Ukrainian artillery hits ammunition depot"},
        {"title": "Ukrainian artillery strikes ammo depot in occupied territories",
         "source": "@wartranslated", "age_min": 25, "summary": "Large explosion reported near Luhansk supply point"},
        {"title": "UA forces hit Russian ammo depot",
         "source": "Kyiv Independent", "age_min": 35, "summary": "Strike on supply depot in Luhansk region"},
        {"title": "Explosion near Luhansk reported on social media",
         "source": "r/ukraine", "age_min": 20, "summary": "Users report large explosion in occupied Luhansk area"},
        {"title": "ISW: Russian defensive lines weakening near Avdiivka",
         "source": "ISW Ukraine", "age_min": 60, "summary": "Institute for the Study of War daily assessment"},
        {"title": "NATO summit discusses Ukraine air defense",
         "source": "BBC World", "age_min": 120, "summary": "Alliance members pledge more air defense systems"},
    ]

    print(dedup_summary(test_articles))
    print()
    print(deduplicated_for_llm(test_articles))
