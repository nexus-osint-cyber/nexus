"""
NEXUS – Wikipedia-Injection (Stufe 3)
Holt automatisch Hintergrundkontext von Wikipedia für Länder/Konflikte/Orte.
Kostenlos, kein API-Key, kein Rate-Limit (außer bei Missbrauch).

Nutzung:
  from nexus_wiki import wiki_context
  ctx = wiki_context("Ukraine")         # Gibt kurzen Kontext-String zurück
  ctx = wiki_context("Hormuz-Straße")   # Funktioniert auch auf Deutsch
"""

from __future__ import annotations

import re
import time
from typing import Optional

import requests

REQUEST_TIMEOUT = 5
_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}
_CACHE_TTL = 3600  # 1 Stunde – Wikipedia-Fakten ändern sich selten

# ── Sprach-Varianten ──────────────────────────────────────────────────────────
# Zuerst Deutsch, dann Englisch als Fallback
_WIKI_APIS = [
    "https://de.wikipedia.org/api/rest_v1/page/summary/{title}",
    "https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
]

# Übersetzungs-/Suchbegriff-Mapping (Deutsch → Wikipedia-Suchbegriff)
_ALIAS_MAP: dict[str, str] = {
    # Regionen / Konflikte
    "naher osten":       "Naher Osten",
    "persischer golf":   "Persischer Golf",
    "hormuz":            "Straße von Hormus",
    "hormuz-strasse":    "Straße von Hormus",
    "taiwan-strasse":    "Taiwanstraße",
    "rotes meer":        "Rotes Meer",
    "schwarzes meer":    "Schwarzes Meer",
    "ostsee":            "Ostsee",
    "korea-halbinsel":   "Koreanische Halbinsel",
    "sahel":             "Sahel",
    # Länder
    "ukraine":           "Ukraine",
    "russland":          "Russland",
    "iran":              "Iran",
    "israel":            "Israel",
    "nordkorea":         "Nordkorea",
    "china":             "Volksrepublik China",
    "taiwan":            "Taiwan",
    "jemen":             "Jemen",
    "syrien":            "Syrien",
    "irak":              "Irak",
    "libanon":           "Libanon",
    "gaza":              "Gazastreifen",
    "mali":              "Mali",
    "sudan":             "Sudan",
    "äthiopien":         "Äthiopien",
    # Konflikte
    "ukraine krieg":     "Russischer Überfall auf die Ukraine 2022",
    "ukraine-krieg":     "Russischer Überfall auf die Ukraine 2022",
    "gazakrieg":         "Israelisch-Palästinensischer Konflikt",
    "houthi":            "Huthis",
}


# ── Abruf ─────────────────────────────────────────────────────────────────────

def _fetch_wiki(title: str, lang: str = "de") -> Optional[dict]:
    """Holt Wikipedia-Zusammenfassung für einen Titel."""
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": "NEXUS-OSINT/0.7 (educational OSINT tool)"
            },
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            d = r.json()
            return {
                "title":   d.get("title", title),
                "extract": d.get("extract", ""),
                "url":     d.get("content_urls", {}).get("desktop", {}).get("page", ""),
                "lang":    lang,
            }
    except Exception:
        pass
    return None


def _search_wiki(query: str, lang: str = "de") -> Optional[str]:
    """Sucht auf Wikipedia nach dem Begriff, gibt ersten Treffer zurück."""
    try:
        r = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 1,
                "format": "json",
            },
            headers={"User-Agent": "NEXUS-OSINT/0.7"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            results = r.json().get("query", {}).get("search", [])
            if results:
                return results[0]["title"]
    except Exception:
        pass
    return None


# ── Haupt-API ──────────────────────────────────────────────────────────────────

def get_wiki_summary(topic: str, max_chars: int = 600) -> Optional[dict]:
    """
    Gibt Wikipedia-Zusammenfassung für ein Thema zurück.
    Versucht Deutsch, dann Englisch.
    Nutzt Cache (1h TTL).
    """
    key = topic.lower().strip()
    now = time.monotonic()

    # Cache prüfen
    if key in _cache and now - _cache_ts.get(key, 0) < _CACHE_TTL:
        return _cache[key]

    # Alias-Mapping
    search_term = _ALIAS_MAP.get(key, topic)

    result = None
    for lang in ("de", "en"):
        # Direkt versuchen
        result = _fetch_wiki(search_term.replace(" ", "_"), lang)
        if result and result.get("extract"):
            break
        # Suche als Fallback
        found = _search_wiki(search_term, lang)
        if found:
            result = _fetch_wiki(found.replace(" ", "_"), lang)
            if result and result.get("extract"):
                break

    if result and result.get("extract"):
        # Auf max_chars kürzen, am Satzende
        extract = result["extract"]
        if len(extract) > max_chars:
            cut = extract[:max_chars].rfind(". ")
            extract = extract[:cut + 1] if cut > 0 else extract[:max_chars]
        result["extract"] = extract
        result["source_topic"] = topic
        _cache[key] = result
        _cache_ts[key] = now
        return result

    return None


def wiki_context(topic: str, max_chars: int = 500) -> str:
    """
    Gibt Wikipedia-Hintergrundkontext als formatierten String zurück.
    Für direkte Einbettung in LLM-Prompts.
    """
    data = get_wiki_summary(topic, max_chars)
    if not data:
        return ""
    lang_hint = " (DE)" if data["lang"] == "de" else " (EN)"
    return (
        f"\n[WIKIPEDIA-HINTERGRUND: {data['title']}{lang_hint}]\n"
        f"{data['extract']}\n"
        f"Quelle: {data.get('url', 'wikipedia.org')}"
    )


def wiki_inject_for_query(query: str) -> str:
    """
    Analysiert eine Lagebild-Anfrage und sucht automatisch
    nach den relevantesten Hintergrundinfos.
    Gibt formatierten Kontext zurück.
    """
    q_low = query.lower()
    contexts = []
    seen = set()

    # 1. Direkte Alias-Treffer
    for alias, title in _ALIAS_MAP.items():
        if alias in q_low and title not in seen:
            ctx = wiki_context(alias, max_chars=400)
            if ctx:
                contexts.append(ctx)
                seen.add(title)
                if len(contexts) >= 2:  # Maximal 2 Wikipedia-Blöcke
                    break

    # 2. Wenn kein Alias-Treffer: direkte Suche
    if not contexts:
        # Wichtigste Wörter extrahieren (Substantive, > 4 Zeichen)
        words = [w for w in re.findall(r'\b[A-ZÄÖÜ][a-zäöüß]{3,}\b', query)
                 if w.lower() not in {"Lage", "Bitte", "Aktuell", "Lagebild"}]
        for word in words[:2]:
            ctx = wiki_context(word, max_chars=350)
            if ctx and word not in seen:
                contexts.append(ctx)
                seen.add(word)

    return "\n".join(contexts)


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Wikipedia-Test")
    print("─" * 50)

    tests = ["Ukraine", "Hormuz-Strasse", "Gaza", "VIX"]
    for t in tests:
        print(f"\nThema: {t}")
        ctx = wiki_context(t, max_chars=300)
        if ctx:
            print(ctx[:250] + "...")
        else:
            print("  (kein Ergebnis)")

    print("\n\nAuto-Inject für 'Lage Iran Persischer Golf':")
    auto = wiki_inject_for_query("Lage Iran Persischer Golf")
    print(auto[:400] + "..." if len(auto) > 400 else auto)
