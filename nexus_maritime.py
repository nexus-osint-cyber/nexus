"""
NEXUS - Maritime Lage
Schiffsbewegungen in strategischen Meerengen via öffentlich zugängliche Quellen.
Tanker-Umleitungen und ungewöhnliche Bewegungen als OSINT-Signal.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

# ======================================================
# Strategische Seegebiete mit Koordinaten
# ======================================================
MARITIME_REGIONS: dict[str, dict] = {
    "Hormuz-Strasse": {
        "center": (26.5, 56.5),
        "bbox":   (22.0, 55.0, 27.0, 60.0),
        "desc":   "Nadelöhr für ~20% des globalen Ölhandels",
        "alert_keywords": ["tanker", "blockade", "iran", "seized", "attack"],
    },
    "Rotes Meer / Bab el-Mandeb": {
        "center": (12.5, 43.5),
        "bbox":   (11.0, 42.0, 15.0, 45.0),
        "desc":   "Suez-Zugang, Houthi-Operationsgebiet",
        "alert_keywords": ["houthi", "angriff", "attack", "drone", "missile", "diverted"],
    },
    "Suez-Kanal": {
        "center": (30.5, 32.3),
        "bbox":   (28.0, 31.5, 32.0, 33.5),
        "desc":   "12% des globalen Seehandels",
        "alert_keywords": ["blockiert", "blocked", "stranded", "diverted", "closed"],
    },
    "Bosporus": {
        "center": (41.1, 29.0),
        "bbox":   (40.5, 28.5, 41.5, 29.5),
        "desc":   "Schwarzmeer-Zugang, Russland-Ukraine-relevanz",
        "alert_keywords": ["russland", "ukraine", "warship", "closed", "blockade"],
    },
    "Taiwan-Strasse": {
        "center": (24.5, 119.5),
        "bbox":   (21.0, 117.0, 27.0, 122.0),
        "desc":   "China-Taiwan-Spannungsgebiet",
        "alert_keywords": ["china", "pla", "navy", "blockade", "exercise", "übung"],
    },
    "Schwarzes Meer": {
        "center": (43.0, 34.0),
        "bbox":   (40.5, 27.0, 47.0, 42.0),
        "desc":   "Ukraine-Konfliktgebiet, Getreidekorridore",
        "alert_keywords": ["mine", "ukraine", "russland", "angriff", "grain"],
    },
}

REQUEST_TIMEOUT = 12

# ======================================================
# MarineTraffic RSS-Feeds (öffentlich, kein Key)
# ======================================================
MT_RSS_FEEDS = [
    # Port-Anläufe und Abfahrten (öffentlich)
    "https://www.marinetraffic.com/rss/expected_arrivals/portid:1/",   # Rotterdam
    "https://www.marinetraffic.com/rss/expected_arrivals/portid:19/",  # Suez
]

# Marine-News RSS-Quellen
MARITIME_NEWS_RSS = [
    "https://www.maritime-executive.com/rss.xml",
    "https://splash247.com/feed/",
    "https://www.tradewindsnews.com/rss",
    "https://gcaptain.com/feed/",
    "https://www.hellenicshippingnews.com/feed/",
]


def _fetch_maritime_news(keywords: list[str], max_results: int = 8) -> list[dict]:
    """
    Holt maritime Nachrichten aus Fach-RSS-Feeds.
    Filtert nach relevanten Keywords (Region, Ereignistyp).
    """
    try:
        import feedparser
    except ImportError:
        return []

    articles = []
    seen_titles: set = set()

    for feed_url in MARITIME_NEWS_RSS:
        try:
            feed = feedparser.parse(feed_url, request_headers={
                "User-Agent": "NEXUS-OSINT/1.0"
            })
            for entry in feed.get("entries", [])[:10]:
                title   = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                link    = getattr(entry, "link", "") or ""
                text    = (title + " " + summary).lower()

                # Relevanz-Check
                if keywords and not any(kw.lower() in text for kw in keywords):
                    continue

                if title in seen_titles:
                    continue
                seen_titles.add(title)

                # Datum
                t = getattr(entry, "published_parsed", None)
                if t:
                    try:
                        dt = datetime(*t[:6], tzinfo=timezone.utc)
                        age_min = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
                        date_s = dt.strftime("%d.%m.%Y %H:%M UTC")
                    except Exception:
                        age_min = 9999
                        date_s = "?"
                else:
                    age_min = 9999
                    date_s = "?"

                # Nur Artikel der letzten 48h
                if age_min > 2880:
                    continue

                articles.append({
                    "title":   title.strip(),
                    "summary": summary.strip()[:250],
                    "url":     link,
                    "date":    date_s,
                    "age_min": age_min,
                    "source":  feed_url.split("/")[2],
                })

        except Exception:
            continue

        if len(articles) >= max_results:
            break

    articles.sort(key=lambda x: x["age_min"])
    return articles[:max_results]


def _fetch_vessel_movements_duckduckgo(region: str, keywords: list[str]) -> list[dict]:
    """
    Fallback: Sucht maritime Ereignisse via DuckDuckGo wenn RSS nicht ausreicht.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []

    queries = [
        f"{region} ship movement {datetime.now().year}",
        f"{region} tanker warship navy",
    ]
    if keywords:
        queries.append(f"{region} " + " ".join(keywords[:2]))

    results = []
    seen: set = set()

    for q in queries:
        try:
            with DDGS() as ddgs:
                for hit in ddgs.news(q, region="en-us", safesearch="moderate",
                                     max_results=4, timelimit="w"):
                    url = hit.get("url", "")
                    if url in seen:
                        continue
                    seen.add(url)
                    results.append({
                        "title":   hit.get("title", ""),
                        "summary": hit.get("body", "") or hit.get("excerpt", ""),
                        "url":     url,
                        "date":    hit.get("date", ""),
                        "source":  hit.get("source", ""),
                    })
        except Exception:
            continue

    return results[:6]


def get_maritime_situation(region_name: str) -> dict:
    """
    Erstellt ein maritimes Lagebild für eine strategische Meerenge.
    Kombiniert Fach-RSS + DuckDuckGo-Fallback.
    """
    region_data = None
    matched_name = region_name

    for name, data in MARITIME_REGIONS.items():
        if region_name.lower() in name.lower() or name.lower() in region_name.lower():
            region_data = data
            matched_name = name
            break

    if not region_data:
        # Generisch: alle Meerengen relevant für den Begriff
        region_data = {
            "center": (0, 0),
            "bbox": (-90, -180, 90, 180),
            "desc": "Allgemein",
            "alert_keywords": [region_name.lower()],
        }

    keywords = region_data.get("alert_keywords", []) + [region_name.lower()]
    ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    # 1. Maritime Fach-News
    news = _fetch_maritime_news(keywords)

    # 2. DuckDuckGo als Ergänzung/Fallback
    if len(news) < 3:
        ddg_results = _fetch_vessel_movements_duckduckgo(matched_name, keywords)
        # Zusammenführen
        seen_titles = {n["title"] for n in news}
        for r in ddg_results:
            if r["title"] not in seen_titles:
                news.append(r)
                seen_titles.add(r["title"])

    # Alerts identifizieren
    alert_articles = []
    for article in news:
        text = (article.get("title", "") + " " + article.get("summary", "")).lower()
        if any(kw.lower() in text for kw in region_data.get("alert_keywords", [])):
            alert_articles.append(article)

    return {
        "region":        matched_name,
        "description":   region_data.get("desc", ""),
        "timestamp":     ts,
        "center_lat":    region_data["center"][0],
        "center_lon":    region_data["center"][1],
        "news":          news,
        "alerts":        alert_articles,
        "alert_count":   len(alert_articles),
        "summary":       _build_maritime_summary(matched_name, ts, news, alert_articles,
                                                  region_data.get("desc", "")),
    }


def _build_maritime_summary(region: str, ts: str, news: list,
                             alerts: list, desc: str) -> str:
    lines = [
        f"MARITIME LAGE – {region}",
        f"Stand: {ts}",
        f"Bedeutung: {desc}",
        f"Aktuelle Meldungen: {len(news)} | Alarm-relevante: {len(alerts)}",
    ]

    if alerts:
        lines.append(f"\n⚠ ALARM-MELDUNGEN ({len(alerts)}):")
        for a in alerts[:5]:
            lines.append(f"  🚢 {a['title']}")
            if a.get("summary"):
                lines.append(f"     {a['summary'][:150]}")

    elif news:
        lines.append("\nAKTUELLE MELDUNGEN:")
        for a in news[:4]:
            lines.append(f"  • {a['title']}")
    else:
        lines.append("Keine aktuellen maritimen Meldungen verfügbar.")

    return "\n".join(lines)


def maritime_for_llm(region_name: str) -> str:
    """Formatierter LLM-Kontext für maritime Lage."""
    result = get_maritime_situation(region_name)
    return f"[MARITIME LAGE – ECHTZEIT]\n{result['summary']}"


def get_all_straits_brief() -> str:
    """Schneller Überblick aller strategischen Meerengen."""
    lines = ["MARITIME ÜBERBLICK – Strategische Meerengen"]
    for region_name in ["Hormuz-Strasse", "Rotes Meer / Bab el-Mandeb",
                        "Suez-Kanal", "Bosporus", "Taiwan-Strasse"]:
        try:
            result = get_maritime_situation(region_name)
            alert_icon = " ⚠" if result["alert_count"] > 0 else " ✓"
            lines.append(
                f"  {region_name:<32} {result['alert_count']} Alarm-Meldungen{alert_icon}"
            )
        except Exception:
            lines.append(f"  {region_name:<32} [nicht erreichbar]")
        time.sleep(0.5)
    return "\n".join(lines)


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Hormuz-Strasse"
    print(maritime_for_llm(region))
