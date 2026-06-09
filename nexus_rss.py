"""
NEXUS - RSS-Aggregator
Zieht gleichzeitig aus mehreren Nachrichtenquellen (Reuters, AP, DW, BBC u.a.)
Gibt strukturierten Kontext für den LLM zurück – ohne Algorithmus-Filter.
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from typing import Optional

# feedparser wird beim ersten Aufruf geladen
_feedparser = None


def _get_feedparser():
    global _feedparser
    if _feedparser is None:
        try:
            import feedparser as fp
            _feedparser = fp
        except ImportError:
            raise RuntimeError(
                "feedparser nicht installiert. Bitte ausführen: pip install feedparser"
            )
    return _feedparser


# ======================================================
# RSS-Quellen: Name -> URL
# Ausgewogen: westlich, russisch, arabisch, asiatisch
# ======================================================
RSS_FEEDS: dict[str, str] = {
    # ── OSINT / Analyse (HÖCHSTE PRIORITÄT) ─────────────────────────────────
    # ISW = Gold-Standard Ukraine-Kriegsanalyse, täglich aktualisiert
    "ISW Ukraine":       "https://www.understandingwar.org/feeds/all",
    "ISW Reports":       "https://isw.pub/UkrainianConflictUpdates",
    # Bellingcat = Geolocations, Verifikation, Investigativ-OSINT
    "Bellingcat":        "https://www.bellingcat.com/feed/",
    # Kyiv Independent = ukrainische Primärquelle (englisch)
    "Kyiv Independent":  "https://kyivindependent.com/rss",
    # RFE/RL = Radio Free Europe, mehrsprachig, Osteuropa-Fokus
    "RFE/RL Ukraine":    "https://www.rferl.org/api/z-ypeqpiquvup",
    "RFE/RL Russia":     "https://www.rferl.org/api/zrqjqmimpiri",
    # The Insider = russischsprachige Investigativ-Recherche
    "The Insider":       "https://theins.ru/feed",
    # Meduza = unabhängige russische Medien (englisch)
    "Meduza EN":         "https://meduza.io/rss/all",
    # Ukraine World = Ukraine-OSINT Aggregator
    "Ukraine World":     "https://ukraineworld.org/feed",
    # ── Sicherheit & Militär (OSINT-relevant) ───────────────────────────────
    "Defense News":      "https://www.defensenews.com/arc/outboundfeeds/rss/",
    "Breaking Defense":  "https://breakingdefense.com/feed/",
    "War on the Rocks":  "https://warontherocks.com/feed/",
    "RUSI":              "https://rusi.org/rss.xml",
    # ── Westlich / International (Quervergleich) ─────────────────────────────
    "Reuters World":     "https://feeds.reuters.com/reuters/worldNews",
    "Reuters Breaking":  "https://feeds.reuters.com/reuters/topNews",
    "AP News":           "https://feeds.apnews.com/rss/apf-topnews",
    "BBC World":         "https://feeds.bbci.co.uk/news/world/rss.xml",
    "DW Deutsch":        "https://rss.dw.com/rdf/rss-de-all",
    "DW English":        "https://rss.dw.com/rdf/rss-en-all",
    "Guardian World":    "https://www.theguardian.com/world/rss",
    # ── Iran / Naher Osten (T158) ────────────────────────────────────────────
    # Iran International = wichtigste unabhängige Quelle (Exil-TV London)
    "Iran International":  "https://www.iranintl.com/en/rss",
    # Radio Farda = RFE/RL Persisch-Dienst (Teheran-fokussiert)
    "Radio Farda":         "https://www.radiofarda.com/api/zrqjqmimpiri",
    # Middle East Eye = investigativer ME-Fokus
    "Middle East Eye":     "https://www.middleeasteye.net/rss",
    # Al-Monitor = Iran/Naher Osten Analyse
    "Al-Monitor":          "https://www.al-monitor.com/rss",
    # IRIB (Iranische Staatsmedien – für Narrativ-Analyse + Widersprüche)
    "IRNA English":        "https://en.irna.ir/rss",
    # MEE Arabic (arabischsprachig, für frühe Signale)
    "Al Jazeera Arabic":   "https://www.aljazeera.net/ajfeedsxmlrss20/",
    # ── Israel (T194: neu) ───────────────────────────────────────────────────
    # Times of Israel = schnellste englische Israel-Berichterstattung
    "Times of Israel":     "https://www.timesofisrael.com/feed/",
    # Jerusalem Post = größtes englisches Israel-Medium
    "Jerusalem Post":      "https://www.jpost.com/rss/rssfeedsfrontpage.aspx",
    # Haaretz EN = linksliberales Israeli-Leitmedium, gute Analysen
    "Haaretz EN":          "https://www.haaretz.com/srv/haaretz-eng",
    # Axios = bricht US-Außenpolitik/Israel-Iran oft als erstes
    "Axios World":         "https://api.axios.com/feed/",
    # i24 News = israelischer Nachrichtensender (mehrsprachig)
    "i24 News":            "https://www.i24news.tv/en/rss",
    # ── Deutsche Quellen (DPA-basiert) ──────────────────────────────────────
    # Tagesschau = ARD-Nachrichtenmagazin, hauptsächlich DPA-Quellen
    "Tagesschau":        "https://www.tagesschau.de/xml/rss2/",
    # Spiegel Online = Leitmedium Deutschland, DPA + Eigenrecherche
    "Spiegel Online":    "https://www.spiegel.de/schlagzeilen/index.rss",
    # Zeit Online = seriöses Leitmedium, DPA + Analyse
    "Zeit Online":       "https://newsfeed.zeit.de/index",
    # n-tv = Nachrichtensender, DPA-Agenturfeeds
    "n-tv":              "https://www.n-tv.de/rss",
    # ── Arabisch / Asiatisch ─────────────────────────────────────────────────
    "Al Jazeera EN":     "https://www.aljazeera.com/xml/rss/all.xml",
    "Kyodo News":        "https://english.kyodonews.net/rss/all.xml",
    # ── Russisch (Quervergleich / Narrativ-Analyse) ───────────────────────────
    # HINWEIS: RT/TASS sind Staatspropaganda – werden als "niedrige Glaubwürdigkeit"
    # markiert, aber für Narrativ-Analyse und Widerspruchs-Erkennung wertvoll
    "RT":                "https://www.rt.com/rss/",
    "TASS":              "https://tass.com/rss/v2.xml",
}

# Glaubwürdigkeits-Override für bekannte Propagandaquellen
RSS_CREDIBILITY_OVERRIDE: dict[str, float] = {
    "RT":   0.10,   # Staatspropaganda
    "TASS": 0.15,   # Staatspropaganda
    "ISW Ukraine":      0.90,   # Analyse-Gold-Standard
    "ISW Reports":      0.90,
    "Bellingcat":       0.92,   # Verifikations-Gold-Standard
    "Kyiv Independent": 0.78,   # Primärquelle, aber Partei
    "The Insider":      0.80,
    "Meduza EN":        0.82,
    "RUSI":             0.88,
    "Breaking Defense": 0.80,
    "War on the Rocks": 0.85,
    # Iran / Naher Osten
    "Iran International": 0.85,  # Bestes unabhängiges Iran-Medium
    "Radio Farda":      0.82,   # RFE/RL Persisch
    "Middle East Eye":  0.75,
    "Al-Monitor":       0.78,
    "IRNA English":     0.35,   # Iranische Staatspropaganda
    "Al Jazeera Arabic": 0.70,
    # Israel (T194)
    "Times of Israel":  0.80,   # Schnell, Israel-zentriert
    "Jerusalem Post":   0.75,   # Konservativ-israelisch
    "Haaretz EN":       0.82,   # Qualitätsanalysen, kritisch
    "Axios World":      0.80,   # US-Außenpolitik, exklusive Leaks
    "i24 News":         0.70,   # israelischer Sender
    # Deutsche Quellen
    "Tagesschau":        0.90,   # ARD Öffentlich-rechtlich, sehr zuverlässig
    "Spiegel Online":    0.82,   # Leitmedium, gute Verifikation
    "Zeit Online":       0.85,   # Leitmedium, hohe Analysequalität
    "n-tv":              0.75,   # Solide, eher schnell als tiefgründig
}

# Wenige Quellen für schnellen Abruf – JETZT MIT OSINT-PRIORISIERUNG
RSS_FEEDS_FAST: dict[str, str] = {
    "ISW Ukraine":        "https://www.understandingwar.org/feeds/all",
    "Bellingcat":         "https://www.bellingcat.com/feed/",
    "Kyiv Independent":   "https://kyivindependent.com/rss",
    "Iran International": "https://www.iranintl.com/en/rss",
    "Radio Farda":        "https://www.radiofarda.com/api/zrqjqmimpiri",
    "Middle East Eye":    "https://www.middleeasteye.net/rss",
    "Reuters World":      "https://feeds.reuters.com/reuters/worldNews",
    "BBC World":          "https://feeds.bbci.co.uk/news/world/rss.xml",
    "Defense News":       "https://www.defensenews.com/arc/outboundfeeds/rss/",
    "Al Jazeera EN":     "https://www.aljazeera.com/xml/rss/all.xml",
}

REQUEST_TIMEOUT = 10
MAX_PER_FEED    = 8    # max Artikel pro Quelle (erhöht für OSINT-Feeds)
MAX_TOTAL       = 60   # max Artikel gesamt (erhöht – LLM bekommt jetzt alle)


def _parse_date(entry) -> tuple[str, int]:
    """
    Gibt (formatiertes Datum, Alter in Minuten) zurück.
    """
    # feedparser füllt 'published_parsed' als time.struct_time
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if t:
        try:
            dt = datetime(*t[:6], tzinfo=timezone.utc)
            age_min = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
            return dt.strftime("%d.%m.%Y %H:%M UTC"), age_min
        except Exception:
            pass
    return "Datum unbekannt", 9999


def _fetch_feed(name: str, url: str, max_items: int,
                results: list, lock: threading.Lock) -> None:
    """Thread-Worker: lädt einen Feed und schreibt Ergebnisse in die Liste."""
    fp = _get_feedparser()
    try:
        feed = fp.parse(url, request_headers={
            "User-Agent": "NEXUS-OSINT/1.0 (educational OSINT tool)",
        })
        entries = feed.get("entries", [])[:max_items]
        for e in entries:
            title   = getattr(e, "title", "") or ""
            summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
            link    = getattr(e, "link", "") or ""
            date_s, age_min = _parse_date(e)

            # Zu alter Artikel überspringen (>12h für den schnellen Feed)
            if age_min > 720:
                continue

            item = {
                "source": name,
                "title": title.strip(),
                "summary": summary.strip()[:300],   # kürzen für LLM-Kontext
                "url": link,
                "date": date_s,
                "age_min": age_min,
            }
            with lock:
                results.append(item)
    except Exception:
        pass   # Feed nicht erreichbar -> ignorieren, andere laufen weiter


def fetch_news(fast: bool = False,
               max_total: int = MAX_TOTAL,
               max_per_feed: int = MAX_PER_FEED,
               keyword_filter: Optional[str] = None) -> list[dict]:
    """
    Holt News von allen (oder schnellen) Quellen parallel.
    Optional: keyword_filter filtert Artikel die das Wort enthalten.
    Gibt Liste von Dicts zurück, sortiert nach Aktualität.
    """
    _ = _get_feedparser()   # Import-Check vorab

    # T194: Region-Routing — Nahost-Feeds bei Iran/Israel vorn einschalten
    _MIDEAST_FEEDS = {
        "Iran International":  "https://www.iranintl.com/en/rss",
        "Radio Farda":         "https://www.radiofarda.com/api/zrqjqmimpiri",
        "Times of Israel":     "https://www.timesofisrael.com/feed/",
        "Jerusalem Post":      "https://www.jpost.com/rss/rssfeedsfrontpage.aspx",
        "Haaretz EN":          "https://www.haaretz.com/srv/haaretz-eng",
        "i24 News":            "https://www.i24news.tv/en/rss",
        "Al-Monitor":          "https://www.al-monitor.com/rss",
        "Middle East Eye":     "https://www.middleeasteye.net/rss",
        "Al Jazeera EN":       "https://www.aljazeera.com/xml/rss/all.xml",
    }
    _MIDEAST_REGIONS = {"iran", "israel", "gaza", "westbank", "irgc", "jerusalem",
                        "teheran", "tehran", "beirut", "lebanon", "hezbollah",
                        "hamas", "naher osten", "middle east", "hormuz"}
    use_mideast_boost = (
        keyword_filter and
        any(r in keyword_filter.lower() for r in _MIDEAST_REGIONS)
    )
    if use_mideast_boost:
        # Nahost-Feeds + Standard-Feeds zusammenführen (Nahost vorne)
        feeds = {**_MIDEAST_FEEDS, **(RSS_FEEDS_FAST if fast else RSS_FEEDS)}
    else:
        feeds = RSS_FEEDS_FAST if fast else RSS_FEEDS
    results: list = []
    lock = threading.Lock()
    threads = []

    for name, url in feeds.items():
        t = threading.Thread(
            target=_fetch_feed,
            args=(name, url, max_per_feed, results, lock),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Warten bis alle Threads fertig (max REQUEST_TIMEOUT Sekunden)
    for t in threads:
        t.join(timeout=REQUEST_TIMEOUT)

    # Credibility-Override für bekannte Quellen anwenden
    for art in results:
        src = art.get("source", "")
        if src in RSS_CREDIBILITY_OVERRIDE:
            art["credibility_score"] = RSS_CREDIBILITY_OVERRIDE[src]
            score = RSS_CREDIBILITY_OVERRIDE[src]
            if score >= 0.85:
                art["credibility_label"] = "HOCH"
            elif score >= 0.60:
                art["credibility_label"] = "MITTEL"
            else:
                art["credibility_label"] = "NIEDRIG (Propaganda-Check)"

    # OSINT-Feeds priorisieren: ISW/Bellingcat vor Mainstream
    _osint_prio = {"ISW Ukraine", "ISW Reports", "Bellingcat", "Kyiv Independent",
                   "Iran International", "Times of Israel", "Haaretz EN"}
    results.sort(key=lambda a: (
        0 if a.get("source") in _osint_prio else 1,
        a.get("age_min", 9999),
    ))

    # Keyword-Filter anwenden
    if keyword_filter:
        kw = keyword_filter.lower()
        filtered = [a for a in results
                    if kw in a.get("title", "").lower()
                    or kw in a.get("summary", "").lower()]
        results = filtered if filtered else results   # Fallback: alle zurückgeben

    return results[:max_total]


def fetch_articles_for_region(region: str, fast: bool = False,
                               max_total: int = MAX_TOTAL) -> list[dict]:
    """Wrapper: holt Artikel mit region-basiertem Keyword-Filter."""
    return fetch_news(fast=fast, max_total=max_total, keyword_filter=region)
