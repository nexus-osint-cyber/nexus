"""
NEXUS - Reddit OSINT Modul
Liest öffentliche Subreddits via Reddit public JSON API.
Kein API-Key nötig — öffentliche Subreddits sind als .json abrufbar.

Relevante Subreddits für OSINT / Geopolitik:
  r/worldnews, r/ukraine, r/geopolitics, r/CredibleDefense,
  r/UkraineWarVideoReport, r/syriancivilwar, r/OSINT, ...
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

REQUEST_TIMEOUT = 12

# User-Agent: Reddit verlangt identifizierbaren UA im Format bot:name/version
# https://github.com/reddit-archive/reddit/wiki/API#rules
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ── In-Memory Cache (T157) ─────────────────────────────────────────────────────
_CACHE:    dict[str, list] = {}
_CACHE_TS: dict[str, float] = {}
_CACHE_TTL = 300  # 5 Minuten – Reddit ändert sich schnell, aber nicht schneller

# ── Standard OSINT-Subreddits ─────────────────────────────────────────────────
SUBREDDITS: dict[str, str] = {
    # Breaking News / Geopolitik
    "worldnews":             "r/worldnews",
    "geopolitics":           "r/geopolitics",
    "CredibleDefense":       "r/CredibleDefense",
    "GlobalPowers":          "r/GlobalPowers",
    # Ukraine / Russland
    "ukraine":               "r/ukraine",
    "UkraineWarVideoReport": "r/UkraineWarVideoReport",
    "RussiaUkraineWar2022":  "r/RussiaUkraineWar2022",
    # Naher Osten
    "IsraelPalestine":       "r/IsraelPalestine",
    "syriancivilwar":        "r/syriancivilwar",
    "iran":                  "r/iran",
    # Asien / Taiwan
    "geopolitics_news":      "r/geopolitics_news",
    "Sino":                  "r/Sino",
    # OSINT
    "osint":                 "r/OSINT",
    "geospatial":            "r/geospatial",
    "flightradar24":         "r/flightradar24",
}

# ── Region → Subreddit-Mapping ────────────────────────────────────────────────
_REGION_SUBS: dict[str, list[str]] = {
    "ukraine":         ["ukraine", "UkraineWarVideoReport", "RussiaUkraineWar2022",
                        "worldnews", "CredibleDefense"],
    "russland":        ["ukraine", "worldnews", "CredibleDefense", "geopolitics"],
    "naher osten":     ["IsraelPalestine", "worldnews", "geopolitics", "syriancivilwar"],
    "israel":          ["IsraelPalestine", "worldnews", "geopolitics"],
    "gaza":            ["IsraelPalestine", "worldnews"],
    "syrien":          ["syriancivilwar", "worldnews", "geopolitics"],
    "iran":            ["iran", "worldnews", "geopolitics", "CredibleDefense"],
    "taiwan":          ["Sino", "worldnews", "geopolitics", "CredibleDefense"],
    "china":           ["Sino", "worldnews", "geopolitics"],
    "korea":           ["worldnews", "geopolitics", "CredibleDefense"],
    "nato":            ["CredibleDefense", "geopolitics", "worldnews"],
    "militär":         ["CredibleDefense", "worldnews", "geopolitics"],
    "osint":           ["osint", "geospatial", "flightradar24"],
    "flug":            ["flightradar24", "osint", "worldnews"],
}


def _ts_to_age_min(unix_ts: float) -> int:
    """Unix-Timestamp → Minuten-Alter."""
    try:
        diff = datetime.now(timezone.utc).timestamp() - unix_ts
        return max(0, int(diff / 60))
    except Exception:
        return 9999


def _fetch_subreddit_rss(subreddit: str, limit: int, label: str) -> list[dict]:
    """
    Holt Posts via Reddits öffentlichen RSS-Feed (reddit.com/r/<sub>/new/.rss).
    T157-Fix: Reddit blockt die .json-API zunehmend mit 403 für Skript-Traffic,
    der klassische RSS-Endpunkt wird davon erfahrungsgemäß seltener erfasst.
    Liefert keine Score/Kommentar-Zahlen (RSS hat die nicht), dafür aber
    zuverlässig Titel/Link/Zeit/Inhalt – für OSINT-Zwecke das Wichtigste.
    """
    try:
        import feedparser
    except ImportError:
        print("[Reddit] feedparser fehlt – RSS-Weg nicht möglich (pip install feedparser)",
              file=sys.stderr)
        return []

    url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
    try:
        feed = feedparser.parse(url, request_headers=_HEADERS)
    except Exception as exc:
        print(f"[Reddit] r/{subreddit}: RSS-Fehler – {exc}", file=sys.stderr)
        return []

    entries = feed.get("entries", []) if isinstance(feed, dict) or hasattr(feed, "get") else []
    if not entries:
        return []

    posts = []
    for e in entries[:limit]:
        title = (getattr(e, "title", "") or "").strip()
        if not title or len(title) < 10:
            continue

        link   = getattr(e, "link", "") or f"https://reddit.com/r/{subreddit}"
        author = (getattr(e, "author", "") or "").replace("/u/", "").strip()

        age_min = 9999
        t = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                age_min = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() / 60))
            except Exception:
                pass
        if age_min > 72 * 60:
            continue

        # RSS liefert HTML-Snippet (Thumbnail-Tag + Text) – grob säubern
        raw = getattr(e, "summary", "") or ""
        summary = re.sub(r"<[^>]+>", " ", raw)
        summary = re.sub(r"&\w+;", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()[:280]

        posts.append({
            "title":      title[:160],
            "summary":    summary,
            "url":        link,
            "source":     f"Reddit/{label}",
            "date":       "",
            "age_min":    age_min,
            "score":      0,     # RSS enthält keine Upvote-Zahl
            "comments":   0,     # RSS enthält keine Kommentar-Zahl
            "flair":      "",
            "reddit_sub": subreddit,
            "author":     author,
        })

    return posts


def fetch_subreddit(subreddit: str, limit: int = 25,
                    sort: str = "new") -> list[dict]:
    """
    Holt Posts von einem Subreddit.
    sort: 'new' | 'hot' | 'rising'
    Gibt nexus_rss-kompatible Dicts zurück.

    T157-Fix: Reddit blockt die anonyme .json-API zunehmend mit 403.
    Primärweg ist jetzt der klassische RSS-Feed (kein Login/API-Key nötig,
    seltener von Bot-Filtern erfasst); die alte JSON-Methode bleibt als
    Fallback erhalten, falls auch RSS mal nichts liefert.
    In-Memory Cache + Retry-Logik bei 429 (T157).
    """
    cache_key = f"{subreddit}_{sort}_{limit}"
    now = time.monotonic()
    if cache_key in _CACHE and now - _CACHE_TS.get(cache_key, 0) < _CACHE_TTL:
        return _CACHE[cache_key]

    label = SUBREDDITS.get(subreddit, f"r/{subreddit}")

    # 1) Primärweg: RSS-Feed (umgeht das 403-Problem der .json-API meistens)
    rss_posts = _fetch_subreddit_rss(subreddit, limit, label)
    if rss_posts:
        _CACHE[cache_key]    = rss_posts
        _CACHE_TS[cache_key] = now
        return rss_posts

    print(f"[Reddit] r/{subreddit}: RSS leer/blockiert – versuche JSON-API als Fallback",
          file=sys.stderr)

    # 2) Fallback: alte public-JSON-Methode
    url    = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": limit, "raw_json": 1}
    label  = SUBREDDITS.get(subreddit, f"r/{subreddit}")

    data = None
    for attempt in range(3):  # max 3 Versuche
        try:
            r = requests.get(url, headers=_HEADERS, params=params,
                             timeout=REQUEST_TIMEOUT)

            if r.status_code == 429:
                # Reddit Rate-Limit: exponentielles Backoff
                wait = 2 ** attempt * 2   # 2s, 4s, 8s
                print(f"[Reddit] r/{subreddit}: 429 Rate-Limit, warte {wait}s...",
                      file=sys.stderr)
                time.sleep(wait)
                continue

            if r.status_code == 403:
                print(f"[Reddit] r/{subreddit}: 403 Forbidden (privat/gesperrt)",
                      file=sys.stderr)
                _CACHE[cache_key]    = []
                _CACHE_TS[cache_key] = now
                return []

            if r.status_code == 404:
                print(f"[Reddit] r/{subreddit}: 404 nicht gefunden", file=sys.stderr)
                _CACHE[cache_key]    = []
                _CACHE_TS[cache_key] = now
                return []

            if r.status_code != 200:
                print(f"[Reddit] r/{subreddit}: HTTP {r.status_code}", file=sys.stderr)
                continue

            # JSON prüfen (Reddit liefert manchmal HTML bei Fehlern)
            ct = r.headers.get("Content-Type", "")
            if "json" not in ct and not r.text.strip().startswith("{"):
                print(f"[Reddit] r/{subreddit}: Keine JSON-Antwort", file=sys.stderr)
                continue

            data = r.json()
            break  # Erfolg

        except requests.Timeout:
            print(f"[Reddit] r/{subreddit}: Timeout (Versuch {attempt+1})", file=sys.stderr)
            if attempt < 2:
                time.sleep(1)
            continue
        except Exception as exc:
            print(f"[Reddit] r/{subreddit}: Fehler – {exc}", file=sys.stderr)
            break

    if not data:
        _CACHE[cache_key]    = []
        _CACHE_TS[cache_key] = now
        return []

    posts = []
    for child in (data.get("data", {}).get("children") or []):
        d = child.get("data") or {}
        if not d:
            continue

        title = (d.get("title") or "").strip()
        if not title or len(title) < 10:
            continue

        # Alters-Filter: max 72h
        age_min = _ts_to_age_min(d.get("created_utc", 0))
        if age_min > 72 * 60:
            continue

        # URL: Link-Post → externe URL, Text-Post → Reddit-Permalink
        ext_url  = d.get("url", "")
        perm_url = f"https://reddit.com{d.get('permalink', '')}"
        out_url  = (ext_url if ext_url
                    and not ext_url.startswith("https://www.reddit")
                    and not ext_url.startswith("https://reddit")
                    else perm_url)

        # Summary aus Selftext oder Flair
        selftext = (d.get("selftext") or "").strip()
        flair    = d.get("link_flair_text") or ""
        summary  = selftext[:280] if selftext else (flair[:120] if flair else "")

        score    = int(d.get("score", 0))
        comments = int(d.get("num_comments", 0))

        posts.append({
            "title":      title[:160],
            "summary":    summary,
            "url":        out_url,
            "source":     f"Reddit/{label}",
            "date":       "",
            "age_min":    age_min,
            "score":      score,
            "comments":   comments,
            "flair":      flair,
            "reddit_sub": subreddit,
        })

    _CACHE[cache_key]    = posts
    _CACHE_TS[cache_key] = now
    return posts


def fetch_osint_reddit(keyword_filter: str = "",
                       limit_per_sub: int = 20,
                       max_subs: int = 5) -> list[dict]:
    """
    Holt Posts von relevanten Subreddits, optional nach Keyword gefiltert.
    Ranking: neueste + upvotete Posts zuerst.
    """
    kw = keyword_filter.lower().strip() if keyword_filter else ""

    # Subreddit-Auswahl
    subs_to_fetch: list[str] = []
    if kw:
        for region, subs in _REGION_SUBS.items():
            if region in kw or kw in region:
                subs_to_fetch.extend(subs)
        if not subs_to_fetch:
            # Fallback: globale Nachrichtenquellen + Keyword-Filter auf Posts
            subs_to_fetch = ["worldnews", "geopolitics", "CredibleDefense"]
    else:
        subs_to_fetch = ["worldnews", "geopolitics", "CredibleDefense",
                         "UkraineWarVideoReport"]

    # Deduplizieren
    seen: set = set()
    unique = [s for s in subs_to_fetch if not (s in seen or seen.add(s))]

    all_posts: list[dict] = []
    for sub in unique[:max_subs]:
        posts = fetch_subreddit(sub, limit=limit_per_sub)
        # Keyword-Filter auf Post-Ebene
        if kw and len(kw) > 3:
            posts = [
                p for p in posts
                if kw in p["title"].lower() or kw in p.get("summary", "").lower()
            ]
        all_posts.extend(posts)
        time.sleep(1.2)   # T157: Reddit Rate-Limit — 1.2s Pause zwischen Subreddits

    # Ranking: neuere + hoch upvotete Posts bevorzugen
    def _rank(p: dict) -> float:
        age   = p.get("age_min", 9999)
        score = max(0, p.get("score", 0))
        # Je älter, desto schlechter; hohe Scores verbessern Rang leicht
        return age / (1.0 + score * 0.005)

    all_posts.sort(key=_rank)
    return all_posts[:50]


def reddit_summary(keyword: str = "") -> str:
    """Text-Zusammenfassung für LLM-Kontext."""
    posts = fetch_osint_reddit(keyword_filter=keyword, limit_per_sub=10)
    if not posts:
        return f"[REDDIT] Keine aktuellen Posts für '{keyword}' gefunden."

    lines = [
        f"[REDDIT OSINT – {keyword or 'Allgemein'}]",
        f"Abgerufen: {len(posts)} Posts von Reddit-Subreddits",
    ]
    for p in posts[:10]:
        age   = p.get("age_min", 9999)
        age_s = f"{age}min" if age < 120 else f"{age // 60}h"
        score = p.get("score", 0)
        lines.append(
            f"  [{p['source']} · vor {age_s} · ↑{score}] {p['title']}"
        )
        if p.get("summary"):
            lines.append(f"    ↳ {p['summary'][:120]}")
    return "\n".join(lines)


def fetch_region(region: str, limit: int = 25) -> list[dict]:
    """Holt Posts fuer eine Region aus den passenden Subreddits."""
    return fetch_osint_reddit(keyword_filter=region.lower(), limit_per_sub=limit // 3 + 5)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NEXUS Reddit OSINT")
    parser.add_argument("region", nargs="?", default="worldnews",
                        help="Region oder Subreddit-Name")
    parser.add_argument("--limit", type=int, default=20, help="Max Posts")
    args = parser.parse_args()
    print(f"[Reddit] Lade Posts fuer: {args.region}")
    posts = fetch_osint_reddit(keyword_filter=args.region, limit_per_sub=args.limit // 3 + 5)
    print(f"[Reddit] {len(posts)} Posts gefunden\n")
    print(reddit_summary(args.region))
