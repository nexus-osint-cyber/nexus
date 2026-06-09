"""
NEXUS – Social Media Modul (Stufe 3)
Bluesky (AT Protocol), Mastodon, VK – kostenlos, kein API-Key für Basis-Abfragen.

OSINT-Wert:
  Social Media meldet Ereignisse oft 10-60 Minuten vor Mainstream-Medien.
  Besonders in Konfliktgebieten sind lokale Accounts oft die schnellsten Quellen.

APIs:
  Bluesky:  https://bsky.social/xrpc/ (public, kein Auth für Suche)
  Mastodon: https://mastodon.social/api/v2/search (public)
  VK:       https://vk.com/dev/wall.search (braucht basic token)
            → VK_ACCESS_TOKEN in config.py optional

Glaubwürdigkeit: Social Media ist unverified. NEXUS gibt immer Score 3-4/10
  und warnt explizit. Nur für Erstmeldungen, nicht für Faktengrundlage.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

REQUEST_TIMEOUT = 8
_cache: dict[str, list] = {}
_cache_ts: dict[str, float] = {}
_CACHE_TTL = 120   # 2 Minuten – Social Media veraltet schnell

# ── Glaubwürdigkeits-Hinweis (immer anzeigen) ──────────────────────────────────
SOCIAL_WARNING = "⚠ SOCIAL MEDIA: UNVERIFIZIERT. Nur als Erstmeldungs-Hinweis verwenden."


# ── Bluesky (AT Protocol) ──────────────────────────────────────────────────────

def fetch_bluesky(keyword: str, limit: int = 15) -> list[dict]:
    """
    Sucht Bluesky-Posts nach Keyword.
    Kein API-Key nötig für öffentliche Suche.
    """
    cache_key = f"bsky_{keyword}_{limit}"
    now = time.monotonic()
    if cache_key in _cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _cache[cache_key]

    try:
        r = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": keyword, "limit": min(limit, 25), "sort": "latest"},
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        posts = data.get("posts", [])
        result = []
        for p in posts:
            try:
                record   = p.get("record", {})
                author   = p.get("author", {})
                text     = record.get("text", "").strip()
                if not text or len(text) < 15:
                    continue
                created_at = record.get("createdAt", "")
                age_min = _parse_age_min(created_at)
                handle  = author.get("handle", "")
                display = author.get("displayName", handle)
                uri     = p.get("uri", "")
                # URI → Browser-URL konvertieren
                url = _bsky_uri_to_url(uri, handle)
                result.append({
                    "title":             text[:120],
                    "summary":           text[:300],
                    "url":               url,
                    "source":            f"Bluesky/@{handle}",
                    "author":            display,
                    "date":              created_at[:10] if created_at else "",
                    "age_min":           age_min,
                    "credibility_score": 3,   # Social Media: immer niedrig
                    "platform":          "bluesky",
                    "warning":           SOCIAL_WARNING,
                    "lang":              "en",
                })
            except Exception:
                continue
        result.sort(key=lambda x: x.get("age_min", 9999))
        _cache[cache_key] = result
        _cache_ts[cache_key] = now
        return result
    except Exception:
        return []


def _bsky_uri_to_url(uri: str, handle: str) -> str:
    """Konvertiert at://did:.../post/xxx → https://bsky.app/profile/.../post/xxx"""
    try:
        parts = uri.split("/")
        post_id = parts[-1]
        return f"https://bsky.app/profile/{handle}/post/{post_id}"
    except Exception:
        return "https://bsky.app"


# ── Mastodon ───────────────────────────────────────────────────────────────────

MASTODON_INSTANCES = [
    "https://mastodon.social",
    "https://infosec.exchange",     # Cybersecurity + OSINT Community
    "https://fosstodon.org",
]


def fetch_mastodon(keyword: str, limit: int = 15) -> list[dict]:
    """
    Sucht auf Mastodon-Instanzen nach Keyword.
    Kein API-Key nötig für öffentliche Suche.
    """
    cache_key = f"masto_{keyword}_{limit}"
    now = time.monotonic()
    if cache_key in _cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _cache[cache_key]

    result = []
    seen_ids = set()

    for instance in MASTODON_INSTANCES[:2]:   # Max 2 Instanzen
        try:
            r = requests.get(
                f"{instance}/api/v2/search",
                params={"q": keyword, "type": "statuses", "limit": min(limit, 20)},
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                continue
            statuses = r.json().get("statuses", [])
            for s in statuses:
                sid = s.get("id", "")
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)

                # HTML entfernen
                content_html = s.get("content", "")
                text = re.sub(r'<[^>]+>', ' ', content_html).strip()
                text = re.sub(r'\s+', ' ', text)[:300]
                if not text or len(text) < 15:
                    continue

                created_at = s.get("created_at", "")
                age_min    = _parse_age_min(created_at)
                account    = s.get("account", {})
                handle     = account.get("acct", "")
                url        = s.get("url", instance)

                result.append({
                    "title":             text[:120],
                    "summary":           text,
                    "url":               url,
                    "source":            f"Mastodon/@{handle}",
                    "author":            account.get("display_name", handle),
                    "date":              created_at[:10] if created_at else "",
                    "age_min":           age_min,
                    "credibility_score": 3,
                    "platform":          "mastodon",
                    "warning":           SOCIAL_WARNING,
                    "lang":              s.get("language", "en") or "en",
                })
        except Exception:
            continue

    result.sort(key=lambda x: x.get("age_min", 9999))
    _cache[cache_key] = result[:limit]
    _cache_ts[cache_key] = now
    return result[:limit]


# ── VK (ВКонтакте) ─────────────────────────────────────────────────────────────

def fetch_vk(keyword: str, limit: int = 10) -> list[dict]:
    """
    Sucht in öffentlichen VK-Gruppen nach Keyword.
    Braucht VK_ACCESS_TOKEN in config.py (kostenlos via vk.com/apps).
    Ohne Token: leere Liste (kein Fehler).
    """
    try:
        import config  # type: ignore
        token = getattr(config, "VK_ACCESS_TOKEN", "")
    except ImportError:
        token = ""

    if not token:
        return []

    cache_key = f"vk_{keyword}_{limit}"
    now = time.monotonic()
    if cache_key in _cache and now - _cache_ts.get(cache_key, 0) < _CACHE_TTL:
        return _cache[cache_key]

    # OSINT-relevante russische Gruppen
    VK_GROUPS = [
        "rybar",             # Rybar - große russische OSINT-Gruppe
        "wargonzo",          # War Gonzo
        "milinfolive",       # Militärinfos
    ]

    result = []
    for group in VK_GROUPS[:2]:
        try:
            r = requests.get(
                "https://api.vk.com/method/wall.search",
                params={
                    "domain":        group,
                    "query":         keyword,
                    "count":         min(limit, 5),
                    "access_token":  token,
                    "v":             "5.131",
                    "lang":          "ru",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get("response", {}).get("items", [])
            for item in items:
                text = (item.get("text", "") or "")[:300].strip()
                if not text or len(text) < 15:
                    continue
                ts     = item.get("date", 0)
                age_min = max(0, int((time.time() - ts) / 60)) if ts else 9999
                post_id = item.get("id", "")
                url     = f"https://vk.com/{group}?w=wall-{item.get('owner_id',0)}_{post_id}"
                result.append({
                    "title":             text[:120],
                    "summary":           text,
                    "url":               url,
                    "source":            f"VK/{group}",
                    "author":            group,
                    "date":              datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
                    "age_min":           age_min,
                    "credibility_score": 3,    # VK/russische Propaganda: immer niedrig bewerten
                    "platform":          "vk",
                    "warning":           SOCIAL_WARNING + " RUSSISCHE QUELLE — Propaganda-Bias möglich.",
                    "lang":              "ru",
                })
        except Exception:
            continue

    _cache[cache_key] = result
    _cache_ts[cache_key] = now
    return result


# ── Wikipedia Recent Changes als Event-Detector ────────────────────────────────

def fetch_wiki_recent_changes(topic: str, minutes: int = 120) -> list[dict]:
    """
    Wenn ein Wikipedia-Artikel plötzlich viele Edits bekommt → Ereignis passiert.
    Sehr nützlicher Proxy-Indikator: "Kharkiv" Artikel hat 15 Edits in 2h → Angriff?
    """
    # Wikipedia Recent Changes API
    # Sucht nach Änderungen die den Topic im Titel enthalten
    cache_key = f"wiki_rc_{topic}_{minutes}"
    now_mono = time.monotonic()
    if cache_key in _cache and now_mono - _cache_ts.get(cache_key, 0) < 300:
        return _cache[cache_key]

    results = []
    for lang in ("uk", "en", "ru"):   # Ukrainisch, Englisch, Russisch
        try:
            r = requests.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action":  "query",
                    "list":    "recentchanges",
                    "rcprop":  "title|timestamp|sizes|user|comment",
                    "rclimit": "50",
                    "rctype":  "edit",
                    "rcshow":  "!bot",
                    "format":  "json",
                },
                headers={"User-Agent": "NEXUS-OSINT/0.7"},
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                continue
            changes = r.json().get("query", {}).get("recentchanges", [])
            topic_lower = topic.lower()
            for ch in changes:
                title = ch.get("title", "")
                if topic_lower not in title.lower():
                    continue
                ts_str    = ch.get("timestamp", "")
                age_min   = _parse_age_min(ts_str)
                if age_min > minutes:
                    continue
                comment   = ch.get("comment", "")
                size_diff = ch.get("newlen", 0) - ch.get("oldlen", 0)
                results.append({
                    "title":             f"📝 Wikipedia [{lang.upper()}]: '{title}' wurde bearbeitet",
                    "summary":           f"Edit: {comment[:100]} | Größe: {'+' if size_diff > 0 else ''}{size_diff} Bytes",
                    "url":               f"https://{lang}.wikipedia.org/wiki/{title.replace(' ','_')}",
                    "source":            f"Wikipedia/{lang.upper()} RecentChanges",
                    "date":              ts_str[:10],
                    "age_min":           age_min,
                    "credibility_score": 6,   # Wikipedia-Edits als Indikator, nicht als Quelle
                    "platform":          "wikipedia_rc",
                    "lang":              lang,
                })
        except Exception:
            continue

    # Gruppieren: wenn derselbe Artikel mehrfach, nur einmal zeigen aber Anzahl melden
    title_counts: dict[str, int] = {}
    title_first: dict[str, dict] = {}
    for rc in results:
        t = rc["title"]
        title_counts[t] = title_counts.get(t, 0) + 1
        if t not in title_first:
            title_first[t] = rc

    final = []
    for title, item in title_first.items():
        cnt = title_counts[title]
        if cnt > 1:
            item["title"] += f" ({cnt}x in {minutes} min)"
            if cnt >= 5:
                item["credibility_score"] = 7   # Viele schnelle Edits = höhere Relevanz
        final.append(item)

    final.sort(key=lambda x: -title_counts.get(x["title"].split(" (")[0], 1))
    _cache[cache_key] = final
    _cache_ts[cache_key] = now_mono
    return final


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _parse_age_min(timestamp_str: str) -> int:
    """Parst ISO8601-Timestamp und gibt Alter in Minuten zurück."""
    if not timestamp_str:
        return 9999
    try:
        # ISO 8601: 2024-01-15T14:30:00Z oder 2024-01-15T14:30:00.000Z
        ts_clean = timestamp_str.rstrip("Z").split(".")[0]
        dt = datetime.fromisoformat(ts_clean).replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        return max(0, int(diff.total_seconds() / 60))
    except Exception:
        return 9999


# ── Haupt-API ──────────────────────────────────────────────────────────────────

def fetch_social_media(
    keyword: str,
    limit_per_platform: int = 10,
    include_wiki_rc: bool = True,
) -> list[dict]:
    """
    Aggregiert Social Media Posts von allen verfügbaren Plattformen.
    Gibt sortiertete Liste zurück (neueste zuerst).
    """
    all_posts = []

    # Bluesky
    try:
        bsky = fetch_bluesky(keyword, limit=limit_per_platform)
        all_posts.extend(bsky)
    except Exception:
        pass

    # Mastodon
    try:
        masto = fetch_mastodon(keyword, limit=limit_per_platform)
        all_posts.extend(masto)
    except Exception:
        pass

    # VK (nur wenn Token vorhanden)
    try:
        vk = fetch_vk(keyword, limit=min(limit_per_platform, 5))
        all_posts.extend(vk)
    except Exception:
        pass

    # Wikipedia Recent Changes
    if include_wiki_rc:
        try:
            wiki_rc = fetch_wiki_recent_changes(keyword, minutes=180)
            all_posts.extend(wiki_rc)
        except Exception:
            pass

    # Duplikate entfernen
    seen = set()
    unique = []
    for p in all_posts:
        key = p.get("title", "")[:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    # Sortieren: neueste zuerst
    unique.sort(key=lambda x: x.get("age_min", 9999))
    return unique[:limit_per_platform * 3]


def social_for_llm(keyword: str, limit: int = 8) -> str:
    """Gibt Social-Media-Zusammenfassung für LLM-Kontext zurück."""
    posts = fetch_social_media(keyword, limit_per_platform=6)
    if not posts:
        return ""

    lines = [f"\n[SOCIAL MEDIA / ECHTZEIT-SIGNALE: {keyword}]"]
    lines.append(SOCIAL_WARNING)
    lines.append("")

    for p in posts[:limit]:
        age = p.get("age_min", 9999)
        age_s = f"{age}min" if age < 120 else f"{age//60}h"
        platform = p.get("platform", "social")
        lines.append(
            f"  [{platform.upper()} | vor {age_s}] "
            f"{p.get('title','')[:100]}"
        )

    return "\n".join(lines)


# ── Direktaufruf zum Testen ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("NEXUS Social Media Test")
    print("─" * 50)
    import sys
    kw = sys.argv[1] if len(sys.argv) > 1 else "Ukraine"
    print(f"Suche: '{kw}'\n")

    print("--- Bluesky ---")
    bsky = fetch_bluesky(kw, limit=5)
    for p in bsky[:3]:
        print(f"  [{p['age_min']}min] {p['title'][:80]}")
    print(f"  ({len(bsky)} Posts gefunden)")

    print("\n--- Mastodon ---")
    masto = fetch_mastodon(kw, limit=5)
    for p in masto[:3]:
        print(f"  [{p['age_min']}min] {p['title'][:80]}")
    print(f"  ({len(masto)} Posts gefunden)")

    print("\n--- Wikipedia Recent Changes ---")
    wiki = fetch_wiki_recent_changes(kw, minutes=240)
    for p in wiki[:3]:
        print(f"  {p['title']}")
    print(f"  ({len(wiki)} Änderungen in 4h)")
