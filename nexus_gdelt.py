"""
NEXUS - GDELT Integration
Weltweite Nachrichten-Ereignisse mit GPS-Koordinaten via GDELT API v2.
Kein API-Key erforderlich – komplett kostenlos.
GDELT erfasst alle ~15 Minuten neue Weltgeschehen.

Hinweis (T175): Das Resilienz-Muster dieses Moduls (Browser-UA, Cache,
Retry/Backoff bei 429/503, Zeitfenster-Fallback) wurde inzwischen als
generisches Toolkit extrahiert → siehe nexus_resilience.py. Künftige
Quellen-Reparaturen sollten dort die Bausteine (BROWSER_HEADERS, TTLCache,
retry_request, try_strategies) wiederverwenden statt das Muster erneut
ad-hoc nachzubauen.
"""

from __future__ import annotations

import re
import sys
import time
import requests
from datetime import datetime, timezone
from typing import Optional

GDELT_DOC_API  = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_GEO_API  = "https://api.gdeltproject.org/api/v2/geo/geo"
REQUEST_TIMEOUT = 20

# T173-Fix: GDELT liefert bei Default-UA ("python-requests/x.x") gehäuft leere
# Antworten/Drosselungen – ein realistischer Browser-UA reduziert das spürbar
# (selbes Muster wie beim Reddit-403-Problem, siehe nexus_reddit.py / T157).
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}

# ── In-Memory Cache (T173) ──────────────────────────────────────────────────
# GDELT-Indizes aktualisieren sich alle ~15 Minuten – häufigeres Pollen bringt
# nichts außer Drosselungsrisiko. Cache verringert Last + glättet Ausfälle.
_CACHE:    dict[str, list] = {}
_CACHE_TS: dict[str, float] = {}
_CACHE_TTL = 600  # 10 Minuten

# ── Deutsch → Englisch Query-Übersetzung ──────────────────────────────────────
# GDELT indiziert englischsprachige Medien, daher müssen DE-Namen übersetzt werden
_DE_TO_EN: dict[str, str] = {
    "naher osten":      "Middle East",
    "nahost":           "Middle East",
    "russland":         "Russia",
    "syrien":           "Syria",
    "irak":             "Iraq",
    "jemen":            "Yemen",
    "libanon":          "Lebanon",
    "ägypten":          "Egypt",
    "türkei":           "Turkey",
    "persischer golf":  "Persian Gulf",
    "rotes meer":       "Red Sea",
    "taiwan-strasse":   "Taiwan Strait",
    "korea-halbinsel":  "Korean Peninsula",
    "nordkorea":        "North Korea",
    "südkorea":         "South Korea",
    "schwarzes meer":   "Black Sea",
    "sahel":            "Sahel",
    "äthiopien":        "Ethiopia",
    "myanmar":          "Myanmar",
    "bangladesch":      "Bangladesh",
    "pakistan":         "Pakistan",
    "afghanistan":      "Afghanistan",
    "hormuz-strasse":   "Strait of Hormuz",
    "hormuzstrasse":    "Strait of Hormuz",
    # Städte
    "teheran":          "Tehran",
    "bagdad":           "Baghdad",
    "damaskus":         "Damascus",
    "beirut":           "Beirut",
    "sanaa":            "Sana'a",
    "kiew":             "Kyiv",
    "charkiw":          "Kharkiv",
    "mariupol":         "Mariupol",
    "moskau":           "Moscow",
    "peking":           "Beijing",
    "tokio":            "Tokyo",
}


def _translate_query(region: str) -> str:
    """Übersetzt deutschen Regionsnamen in englischen GDELT-Query."""
    r = region.lower().strip()
    # Direkte Übersetzung
    if r in _DE_TO_EN:
        return _DE_TO_EN[r]
    # Teilstring-Übersetzung
    for de, en in _DE_TO_EN.items():
        if de in r:
            return en
    # Fallback: Original (funktioniert für Iran, Ukraine, Gaza etc.)
    return region


# ── Hilfsfunktionen ─────────────────────────────────────────────────────────

def _parse_seendate(s: str) -> tuple[str, int]:
    """Parst GDELT-Datum '20240508T120000Z' → (lesbarer String, Minuten alt)."""
    try:
        dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        age_min = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
        return dt.strftime("%d.%m.%Y %H:%M"), max(0, age_min)
    except Exception:
        return s, 9999


# ── Artikel abrufen ─────────────────────────────────────────────────────────

def _relevance_score(title: str, query: str) -> float:
    """
    Einfacher Relevanz-Score: Anteil der Query-Wörter im Titel.
    0.0 = kein Treffer, 1.0 = alle Wörter gefunden.
    """
    if not title or not query:
        return 0.0
    q_words = [w.lower() for w in query.split() if len(w) > 2]
    if not q_words:
        return 1.0  # Kein Filter möglich
    title_l = title.lower()
    hits = sum(1 for w in q_words if w in title_l)
    return hits / len(q_words)


def fetch_gdelt_articles(query: str, hours: int = 48,
                         max_records: int = 25,
                         min_relevance: float = 0.1) -> list[dict]:
    """
    Holt aktuelle Artikel zu einer Query von GDELT.
    Format kompatibel mit nexus_rss.py-Artikeln.

    min_relevance: Mindest-Relevanz-Score (0.0–1.0).
    0.1 = mindestens ein Keyword-Treffer im Titel (sehr offen, da GDELT selbst filtert).

    Neu (T154):
    - Automatische DE→EN Query-Übersetzung
    - Retry-Logik bei 429/503
    - Stderr-Logging für Diagnose

    Neu (T173 – Fallback-Pattern nach Reddit-Vorbild, da T154 weiterhin
    konsequent 0 Ergebnisse lieferte):
    - Realistischer Browser-User-Agent (GDELT drosselt/leert Antworten bei
      Default-"python-requests"-UA ähnlich wie Reddit es bei anonymem
      Skript-Traffic tat)
    - In-Memory-Cache (10 Min – GDELT aktualisiert ohnehin nur alle ~15 Min,
      häufigeres Pollen erhöht nur das Drosselungsrisiko)
    - Automatischer Zeitfenster-Fallback: liefert das angefragte Zeitfenster
      0 rohe Artikel (z.B. weil in den letzten 6h zufällig nichts indiziert
      wurde), wird automatisch mit einem deutlich breiteren Fenster
      (max 1 Woche) nachgefragt, bevor "keine Daten" gemeldet wird
    - Diagnose-Logging zeigt jetzt auch einen Rohausschnitt der Antwort,
      wenn JSON-Parsing fehlschlägt (HTML-Fehlerseiten, Drosselungs-Hinweise)
    """
    # T154: Deutsche Regionsnamen in Englisch übersetzen
    en_query = _translate_query(query)
    if en_query != query:
        print(f"[GDELT] Query übersetzt: '{query}' → '{en_query}'", file=sys.stderr)

    # T225: GDELT lehnt Anfragen ab wenn einzelne Wörter < 4 Zeichen sind.
    # Fehlermeldung: "Your search contained a keyword that was too short."
    # Workaround: Wörter < 4 Zeichen aus der Query entfernen. Mindestens 1 Wort
    # muss übrig bleiben, sonst leere Query → Fallback auf ursprünglichen Query.
    _words = en_query.split()
    _long_words = [w for w in _words if len(w) >= 4]
    if _long_words and len(_long_words) < len(_words):
        en_query = " ".join(_long_words)
        print(f"[GDELT] Kurze Wörter entfernt: → '{en_query}'", file=sys.stderr)

    # GDELT lehnt Sonderzeichen ab ("illegal character"): Bindestriche (Ben-Gvir),
    # Unicode-Apostrophe/Curly-Quotes (U+2018–201F), Doppelpunkte, Kommata u.a.
    # [^\w\s] entfernt alle nicht-Wort/nicht-Leerzeichen in einem Schritt.
    _clean = re.sub(r"[^\w\s]", " ", en_query)
    _clean = " ".join(_clean.split())  # Mehrfach-Leerzeichen normalisieren
    if _clean != en_query:
        print(f"[GDELT] Sonderzeichen entfernt: → '{_clean}'", file=sys.stderr)
        en_query = _clean

    cache_key = f"{en_query}_{hours}_{max_records}_{min_relevance}"
    now = time.monotonic()
    if cache_key in _CACHE and now - _CACHE_TS.get(cache_key, 0) < _CACHE_TTL:
        return _CACHE[cache_key]

    def _store(result: list) -> list:
        _CACHE[cache_key]    = result
        _CACHE_TS[cache_key] = now
        return result

    def _one_request(timespan_h: int) -> tuple[Optional[list], int]:
        """
        Ein einzelner GDELT-Abruf mit gegebenem Zeitfenster.
        Rückgabe: (gefilterte_artikel_oder_None, roh_anzahl)
        None = Fehler/keine verwertbare Antwort (kein sinnvoller raw_count).
        """
        params = {
            "query":      en_query,
            "mode":       "artlist",
            "maxrecords": min(max_records * 2, 250),  # mehr holen, dann filtern
            "timespan":   f"{min(timespan_h, 168)}h",  # GDELT max 1 Woche
            "format":     "json",
            "sort":       "DateDesc",                  # neueste zuerst
        }

        for attempt in range(2):
            try:
                r = requests.get(GDELT_DOC_API, params=params,
                                 headers=_HEADERS, timeout=REQUEST_TIMEOUT)

                if r.status_code == 429:
                    print(f"[GDELT] Rate-Limit (429), warte 5s... (Versuch {attempt+1})",
                          file=sys.stderr)
                    time.sleep(5)
                    continue
                if r.status_code == 503:
                    print(f"[GDELT] Service unavailable (503), warte 3s... (Versuch {attempt+1})",
                          file=sys.stderr)
                    time.sleep(3)
                    continue
                if r.status_code != 200:
                    print(f"[GDELT] HTTP {r.status_code} für '{en_query}' "
                          f"({timespan_h}h-Fenster)", file=sys.stderr)
                    return None, 0

                # JSON-Parsing (GDELT liefert manchmal HTML bei Fehlern/Drosselung)
                text = r.text.strip()
                if not text or text.startswith("<"):
                    snippet = text[:160].replace("\n", " ")
                    print(f"[GDELT] Keine JSON-Antwort (HTML/leer) für '{en_query}' "
                          f"({timespan_h}h) – Antwortausschnitt: '{snippet}'", file=sys.stderr)
                    return None, 0

                try:
                    data = r.json()
                except Exception as exc:
                    snippet = text[:160].replace("\n", " ")
                    print(f"[GDELT] JSON-Fehler für '{en_query}': {exc} – "
                          f"Antwortausschnitt: '{snippet}'", file=sys.stderr)
                    return None, 0

                raw_count = len(data.get("articles") or [])
                articles = []

                for a in (data.get("articles") or []):
                    title = (a.get("title") or "")[:120]
                    # Relevanz gegen EN-Query prüfen
                    score = _relevance_score(title, en_query)
                    if score < min_relevance:
                        continue
                    date_str, age_min = _parse_seendate(a.get("seendate", ""))
                    articles.append({
                        "title":     title,
                        "url":       a.get("url", "#"),
                        "source":    f"GDELT/{a.get('domain', '?')}",
                        "date":      date_str,
                        "summary":   "",
                        "age_min":   age_min,
                        "country":   a.get("sourcecountry", ""),
                        "lang":      a.get("language", ""),
                        "_gdelt_rel": round(score, 2),
                    })

                # Nach Relevanz sortieren (beste zuerst), dann auf max_records kürzen
                articles.sort(key=lambda x: -x.get("_gdelt_rel", 0))
                result = articles[:max_records]
                print(f"[GDELT] '{en_query}' ({timespan_h}h-Fenster): "
                      f"{raw_count} roh → {len(result)} gefiltert", file=sys.stderr)
                return result, raw_count

            except requests.Timeout:
                print(f"[GDELT] Timeout für '{en_query}' (Versuch {attempt+1}, "
                      f"{timespan_h}h-Fenster)", file=sys.stderr)
                if attempt == 0:
                    time.sleep(2)
                continue
            except Exception as exc:
                print(f"[GDELT] Fehler fur '{en_query}': {exc}", file=sys.stderr)
                return None, 0

        return None, 0

    # 1) Versuch mit dem angefragten Zeitfenster
    result, raw_count = _one_request(hours)
    if result:
        return _store(result)

    # 2) T173-Fallback: breites Zeitfenster wenn angefragtes Fenster leer war
    if raw_count == 0 and hours <= 48:
        result_fb, _ = _one_request(168)
        if result_fb:
            return _store(result_fb)
    return []
