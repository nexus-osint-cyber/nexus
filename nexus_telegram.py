"""
NEXUS – Task 53: Telegram-Kanal-Aggregator (erweitert)
=======================================================
Scrapt öffentliche Telegram-Kanäle via t.me/s/ — kein Account, kein Key.
Erweitert um:
  - Keyword-Scoring nach Kategorie (Detonation, Drohne, Artillerie, ...)
  - Trust-gewichtete Kanäle mit Region-Tags
  - Surge-Detektion mit Rolling-Baseline
  - telegram_for_escalation() für nexus_escalation.py
  - telegram_for_map() für nexus_report.py

Wichtig: Nur OSINT/Analyse-Zwecke, nur öffentliche Kanäle.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

try:
    from bs4 import BeautifulSoup
    _BS4_OK = True
except ImportError:
    _BS4_OK = False

REQUEST_TIMEOUT = 12
_BASE = "https://t.me/s/{channel}"

# ── Kanal-Registry mit Trust + Region ─────────────────────────────────────────
# Format: "channel_id": {"name": str, "region": str, "trust": float}
# trust: 0.0–1.0 (1.0 = sehr zuverlässig, 0.5 = unbekannt, < 0.5 = propagandaverdächtig)
CHANNEL_META: dict[str, dict] = {
    # ── Ukraine / Osteuropa – OSINT-Qualität (trust >= 0.80) ─────────────────
    "wartranslated":        {"name": "War Translated",           "region": "ukraine", "trust": 0.90},
    "DeepStateUA":          {"name": "DeepState UA",             "region": "ukraine", "trust": 0.88},
    "UkraineWarReport":     {"name": "Ukraine War Report",       "region": "ukraine", "trust": 0.82},
    "nexta_tv":             {"name": "NEXTA TV",                 "region": "ukraine", "trust": 0.82},
    "UkraineNow_eng":       {"name": "Ukraine Now (EN)",         "region": "ukraine", "trust": 0.80},
    "Ukraine_de":           {"name": "Ukraine Aktuell (DE)",     "region": "ukraine", "trust": 0.78},
    "ua_videos":            {"name": "UA Videos (Geolocated)",   "region": "ukraine", "trust": 0.85},
    "GeoConfirmedUA":       {"name": "GeoConfirmed Ukraine",     "region": "ukraine", "trust": 0.90},
    "UkrainianFront":       {"name": "Ukrainian Front",          "region": "ukraine", "trust": 0.80},
    "kievvlast":            {"name": "Kyiv Vlast",               "region": "ukraine", "trust": 0.78},
    "ukraina_ru":           {"name": "Ukraina.ru (RU-Narrativ)", "region": "ukraine", "trust": 0.40},
    # ── Ukraine – Militärische Analyse ───────────────────────────────────────
    "militarysummary":      {"name": "Military Summary",         "region": "ukraine", "trust": 0.75},
    "militarylandnet":      {"name": "Militarylandnet",          "region": "ukraine", "trust": 0.82},
    "defence_ua":           {"name": "Defence of Ukraine",       "region": "ukraine", "trust": 0.83},
    "GeneralStaffUA":       {"name": "UA General Staff (Offiziell)", "region": "ukraine", "trust": 0.72},
    # ── Russische Quellen (Narrativ-Analyse / Quervergleich) ─────────────────
    # HINWEIS: trust < 0.65 = propagandaverdächtig, trotzdem analytisch wertvoll
    "intelslava":           {"name": "Intel Slava Z (RU)",       "region": "ukraine", "trust": 0.55},
    "rybar":                {"name": "Rybar (RU Mil Blog)",      "region": "ukraine", "trust": 0.50},
    "mod_russia":           {"name": "RU MoD (Offizielle Prop)", "region": "ukraine", "trust": 0.20},
    "grey_zone":            {"name": "Grey Zone (RU Wagner)",    "region": "ukraine", "trust": 0.45},
    # ── Global OSINT (alle Regionen) ──────────────────────────────────────────
    "Conflict_News":        {"name": "Conflict News",            "region": "global",  "trust": 0.85},
    "OSINTtechnical":       {"name": "OSINT Technical",          "region": "global",  "trust": 0.88},
    "GeoConfirmed":         {"name": "GeoConfirmed (Global)",    "region": "global",  "trust": 0.90},
    "IntelDoge":            {"name": "Intel Doge",               "region": "global",  "trust": 0.82},
    "militaryosint":        {"name": "Military OSINT",           "region": "global",  "trust": 0.85},
    "CITeam_en":            {"name": "CI-Team OSINT",            "region": "global",  "trust": 0.83},
    "GeopoliticsLive":      {"name": "Geopolitics Live",         "region": "global",  "trust": 0.78},
    "IntelSlavaEnglish":    {"name": "Intel Slava (EN)",         "region": "global",  "trust": 0.60},
    "warmonitor":           {"name": "War Monitor",              "region": "global",  "trust": 0.80},
    "Flash_news_ua":        {"name": "Flash News UA",            "region": "ukraine", "trust": 0.78},
    # ── Naher Osten ───────────────────────────────────────────────────────────
    "MiddleEastSpectator":  {"name": "ME Spectator",             "region": "mideast", "trust": 0.80},
    "gazawartracker":       {"name": "Gaza War Tracker",         "region": "mideast", "trust": 0.75},
    "IsraelWarRoom":        {"name": "Israel War Room",          "region": "mideast", "trust": 0.70},
    "QudsNen":              {"name": "Quds News Network",        "region": "mideast", "trust": 0.60},
    "SyrianWarDaily":       {"name": "Syrian War Daily",         "region": "mideast", "trust": 0.75},
    # ── Asien / Indo-Pazifik ─────────────────────────────────────────────────
    "IndoPacificNews":      {"name": "Indo Pacific News",        "region": "pacific", "trust": 0.75},
    "TaiwanStraits":        {"name": "Taiwan Strait Watch",      "region": "pacific", "trust": 0.75},
    # ── Sicherheit / Cyber ────────────────────────────────────────────────────
    "tropoFAR":             {"name": "Troposcatter (Militär)",   "region": "global",  "trust": 0.75},
    "cybersecuritynews":    {"name": "Cybersecurity News",       "region": "global",  "trust": 0.80},
}

# Rückwärtskompatibilität: einfaches Dict channel→name
CHANNELS: dict[str, str] = {k: v["name"] for k, v in CHANNEL_META.items()}

# ── Keyword-Scoring-Tabelle ───────────────────────────────────────────────────
# (keyword_list, score_weight, kategorie)
KEYWORD_SCORES: list[tuple[list[str], float, str]] = [
    (["explosion", "взрыв", "blast", "detonation", "vbied", "ied"],           3.0, "detonation"),
    (["missile", "ракета", "rocket", "grad", "mlrs", "himars", "atacms"],     2.5, "missile"),
    (["drone", "drohne", "uav", "бпла", "shahed", "fpv", "lancet"],           2.5, "drone"),
    (["artillery", "арт", "arty", "howitzer", "strike", "shelling"],          2.0, "artillery"),
    (["advance", "наступление", "offensive", "breakthrough", "seized"],       2.0, "advance"),
    (["retreat", "отступление", "withdrawal", "abandoned", "falling back"],   2.0, "retreat"),
    (["airstrike", "авиаудар", "air strike", "bombing", "bomb drop"],         2.5, "airstrike"),
    (["ship", "naval", "submarine", "warship", "destroyer", "fleet"],         1.5, "naval"),
    (["nuclear", "ядерный", "nuke", "radioactive", "radiation"],              4.0, "nuclear"),
    (["chemical", "хим", "cbrn", "sarin", "chlorine", "toxic gas"],           3.5, "cbrn"),
    (["killed", "wounded", "потери", "убит", "casualty", "fatalities"],       1.5, "casualties"),
    (["ceasefire", "перемирие", "truce", "negotiation", "talks"],             1.0, "diplomacy"),
    (["mobilization", "мобилизация", "conscription", "draft order"],          1.5, "mobilization"),
    (["cyber", "hack", "ddos", "infrastructure attack", "power grid"],        2.0, "cyber"),
    (["urgent", "срочно", "breaking", "confirmed", "bestätigt", "alert"],     0.5, "urgency"),
    (["video", "footage", "видео", "geolocated", "confirmed footage"],        0.3, "evidence"),
]

# ── Region-Keywords für Relevanz-Filter ──────────────────────────────────────
_REGION_KEYWORDS: dict[str, list[str]] = {
    "ukraine":   ["ukraine", "ukraina", "kyiv", "kherson", "zaporizhzhia",
                  "donetsk", "luhansk", "kharkiv", "odesa", "bakhmut",
                  "russia", "россия", "украина", "зсу", "вс рф", "uaf"],
    "mideast":   ["israel", "gaza", "hamas", "hezbollah", "lebanon", "iran",
                  "syria", "iraq", "yemen", "houthi", "idf", "west bank",
                  "red sea", "strait of hormuz"],
    "pacific":   ["taiwan", "china", "pla", "south china sea", "japan",
                  "north korea", "strait", "philippines", "rok", "dprk"],
    "global":    [],  # kein Filter
}

# ── Region → Kanal-Mapping ────────────────────────────────────────────────────
# Ukraine: OSINT-Qualitätskanäle zuerst, Propagandakanäle ans Ende
# Stand Jun 2026: nur verifizierte Handles — ungültige entfernt (Conflict_News,
# GeoConfirmed, MiddleEastSpectator existieren nicht als Telegram-Handles).
_REGION_CHANNELS: dict[str, list[str]] = {
    "ukraine":         [
        # Primär OSINT (trust >= 0.80)
        "wartranslated", "GeoConfirmedUA", "ua_videos", "DeepStateUA",
        "UkraineWarReport", "nexta_tv", "UkraineNow_eng", "militarylandnet",
        "defence_ua", "Flash_news_ua",
        # Analyse
        "militarysummary", "OSINTtechnical",
        # Quervergleich (niedrige trust, Narrativ-Analyse)
        "intelslava", "rybar",
    ],
    "russland":        ["intelslava", "rybar", "militarysummary", "wartranslated",
                        "grey_zone"],
    "naher osten":     ["warmonitor", "OSINTdefender", "militarysummary"],
    "israel":          ["warmonitor", "OSINTdefender", "militarysummary"],
    "gaza":            ["warmonitor", "OSINTdefender", "gazawartracker"],
    "syrien":          ["warmonitor", "SyrianWarDaily", "OSINTdefender"],
    "iran":            ["warmonitor", "GeopoliticsLive", "OSINTdefender"],
    "lebanon":         ["warmonitor", "OSINTdefender"],
    "taiwan":          ["GeopoliticsLive", "IndoPacificNews", "OSINTdefender"],
    "korea":           ["GeopoliticsLive", "IndoPacificNews"],
    "nato":            ["militarysummary", "GeopoliticsLive", "OSINTtechnical"],
    "militär":         ["militarysummary", "OSINTtechnical", "tropoFAR"],
    "osint":           ["OSINTtechnical", "OSINTdefender", "CITeam_en"],
}


def _channel_url(channel: str) -> str:
    return _BASE.format(channel=channel)


def _parse_age_min(dt_str: str) -> int:
    """ISO-Datetime → Minuten-Alter."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        diff = datetime.now(timezone.utc) - dt
        return max(0, int(diff.total_seconds() / 60))
    except Exception:
        return 9999


def fetch_channel(channel: str, limit: int = 20) -> list[dict]:
    """
    Scrapt einen öffentlichen Telegram-Kanal via t.me/s/.
    Gibt Liste von Artikel-Dicts (nexus_rss-kompatibles Format) zurück.
    """
    if not _BS4_OK:
        return []

    url = _channel_url(channel)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }

    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results  = []
    ch_label = CHANNELS.get(channel, f"@{channel}")

    for wrap in soup.select(".tgme_widget_message_wrap")[:limit]:
        # ── Nachrichtentext ──────────────────────────────────────────────────
        text_el = wrap.select_one(".tgme_widget_message_text")
        if not text_el:
            # Reiner Medien-Post ohne Text → überspringen
            continue
        text = text_el.get_text(separator=" ", strip=True)
        if not text or len(text) < 20:
            continue

        # ── Datum ────────────────────────────────────────────────────────────
        time_el = wrap.select_one("time[datetime]")
        dt_str  = time_el["datetime"] if time_el else ""
        age_min = _parse_age_min(dt_str)

        # Ältere als 72h nicht einbeziehen
        if age_min > 72 * 60:
            continue

        # ── Post-URL ─────────────────────────────────────────────────────────
        link_el  = wrap.select_one("a.tgme_widget_message_date")
        post_url = link_el["href"] if link_el else url

        # ── Medien-Flag ──────────────────────────────────────────────────────
        has_media = bool(wrap.select_one(
            ".tgme_widget_message_photo_wrap, "
            ".tgme_widget_message_video_wrap, "
            ".tgme_widget_message_document_wrap"
        ))

        # ── Titel + Summary aus Text ──────────────────────────────────────────
        lines   = [l.strip() for l in text.split("\n") if l.strip()]
        title   = lines[0][:140] if lines else text[:140]
        summary = " ".join(lines[1:4])[:280] if len(lines) > 1 else ""

        # Emojis + überflüssige Sonderzeichen aus Titel entfernen
        title = re.sub(r"[\U00010000-\U0010ffff]", "", title).strip()
        if len(title) < 10:
            title = text[:100]

        results.append({
            "title":      title,
            "summary":    summary,
            "url":        post_url,
            "source":     f"Telegram/{ch_label}",
            "date":       dt_str[:10],
            "age_min":    age_min,
            "has_media":  has_media,
            "tg_channel": channel,
        })

    return results


def fetch_osint_channels(keyword_filter: str = "",
                         limit_per_channel: int = 10,
                         max_channels: int = 6) -> list[dict]:
    """
    Holt Posts von relevanten Kanälen, optional nach Keyword gefiltert.
    Gibt kombinierte, nach Alter sortierte Liste zurück.
    """
    kw = keyword_filter.lower().strip() if keyword_filter else ""

    # Kanal-Auswahl
    channels_to_fetch: list[str] = []
    if kw:
        for region, chans in _REGION_CHANNELS.items():
            if region in kw or kw in region:
                channels_to_fetch.extend(chans)
        if not channels_to_fetch:
            # Allgemein: alle Kanäle, Keyword-Filter auf Post-Ebene
            channels_to_fetch = list(CHANNELS.keys())
    else:
        channels_to_fetch = list(CHANNELS.keys())

    # Deduplizieren, Reihenfolge erhalten
    seen_ch: set = set()
    unique = [c for c in channels_to_fetch if not (c in seen_ch or seen_ch.add(c))]

    all_posts: list[dict] = []
    for ch in unique[:max_channels]:
        posts = fetch_channel(ch, limit=limit_per_channel)
        # Keyword-Filter auf Post-Ebene
        if kw and len(kw) > 3:
            posts = [
                p for p in posts
                if kw in p["title"].lower() or kw in p.get("summary", "").lower()
            ]
        all_posts.extend(posts)
        time.sleep(0.4)   # Höflichkeitspause

    # Nach Alter sortieren (neueste zuerst)
    all_posts.sort(key=lambda x: x.get("age_min", 9999))
    return all_posts[:50]


def telegram_summary(keyword: str = "") -> str:
    """Text-Zusammenfassung für LLM-Kontext."""
    if not _BS4_OK:
        return "[TELEGRAM] BeautifulSoup4 nicht installiert (pip install beautifulsoup4 --break-system-packages)"

    posts = fetch_osint_channels(keyword_filter=keyword, limit_per_channel=5)
    if not posts:
        return f"[TELEGRAM] Keine aktuellen Posts für '{keyword}' gefunden."

    lines = [
        f"[TELEGRAM OSINT – {keyword or 'Allgemein'}]",
        f"Abgerufen: {len(posts)} Posts von Telegram-OSINT-Kanälen",
    ]
    for p in posts[:10]:
        age = p.get("age_min", 9999)
        age_s = f"{age}min" if age < 120 else f"{age // 60}h"
        media = " 📷" if p.get("has_media") else ""
        lines.append(f"  [{p['source']} · vor {age_s}{media}] {p['title'][:100]}")
    return "\n".join(lines)


# ── Keyword-Scoring ──────────────────────────────────────────────────────────
def _score_post(post: dict) -> dict:
    """
    Bewertet einen Post nach Keyword-Kategorien und Trust-Gewichtung.
    Gibt erweiterten Post-Dict zurück mit 'score', 'categories', 'matched_keywords'.
    """
    text = (post.get("title", "") + " " + post.get("summary", "")).lower()
    ch_id = post.get("tg_channel", "")
    trust = CHANNEL_META.get(ch_id, {}).get("trust", 0.7)

    total_score = 0.0
    categories: list[str] = []
    matched_kws: list[str] = []

    for kw_list, weight, category in KEYWORD_SCORES:
        for kw in kw_list:
            if kw in text:
                total_score += weight
                if category not in categories:
                    categories.append(category)
                matched_kws.append(kw)
                break

    return {
        **post,
        "score":            round(total_score * trust, 2),
        "categories":       categories,
        "matched_keywords": matched_kws[:8],
        "trust":            trust,
        "channel_meta":     CHANNEL_META.get(ch_id, {}),
    }


def fetch_scored_posts(
    region: Optional[str] = None,
    hours_back: int = 6,
    max_channels: int = 8,
    min_score: float = 1.0,
) -> list[dict]:
    """
    Holt Posts von relevanten Kanälen, bewertet sie per Keyword-Scoring,
    filtert nach Region und Mindestscore.
    """
    # Kanal-Auswahl nach Region priorisieren
    if region:
        r_low = region.lower()
        # Erst Kanäle der exakten Region, dann global
        ordered = sorted(
            CHANNEL_META.items(),
            key=lambda x: (
                0 if x[1].get("region") in [r_low, "global"] else 1,
                -x[1].get("trust", 0)
            )
        )
    else:
        ordered = sorted(CHANNEL_META.items(), key=lambda x: -x[1].get("trust", 0))

    selected = [ch_id for ch_id, _ in ordered[:max_channels]]

    cutoff_age_min = hours_back * 60
    all_scored: list[dict] = []
    seen_urls: set[str] = set()

    for ch_id in selected:
        posts = fetch_channel(ch_id, limit=15)
        for post in posts:
            if post.get("age_min", 9999) > cutoff_age_min:
                continue
            url = post.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            scored = _score_post(post)
            # Region-relevanz prüfen
            if region:
                r_low = region.lower()
                rk = _REGION_KEYWORDS.get(r_low, [])
                if rk:
                    text = (scored.get("title", "") + " " + scored.get("summary", "")).lower()
                    if not any(k in text for k in rk):
                        # Wenn Kanal explizit für diese Region, trotzdem behalten
                        if CHANNEL_META.get(ch_id, {}).get("region") != r_low:
                            continue

            if scored["score"] >= min_score:
                all_scored.append(scored)

        time.sleep(0.3)

    all_scored.sort(key=lambda x: x["score"], reverse=True)
    return all_scored[:40]


def telegram_for_escalation(region: Optional[str] = None) -> dict:
    """
    Kompaktes Signal-Objekt für nexus_escalation.py.

    Returns:
        {
          surge_active: bool,
          surge_factor: float,
          top_score:    float,
          message_count: int,
          categories:   dict,
          hint:         str,
          channels_active: int,
        }
    """
    posts = fetch_scored_posts(region=region, hours_back=3, max_channels=6, min_score=0.5)

    surge_alerts = detect_surges(keyword_filter=region or "")
    surge_active = len(surge_alerts) > 0
    surge_factor = surge_alerts[0]["score"] if surge_alerts else 1.0

    top_score = posts[0]["score"] if posts else 0.0

    # Kategorie-Häufigkeit
    cat_counts: dict[str, int] = {}
    for p in posts:
        for cat in p.get("categories", []):
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    hint = ""
    if posts:
        best = posts[0]
        cats = ", ".join(best.get("categories", [])[:3])
        source = best.get("source", "")
        hint = f"{source}: {best.get('title', '')[:80]}… [{cats}]"

    return {
        "surge_active":    surge_active,
        "surge_factor":    round(surge_factor, 2),
        "top_score":       top_score,
        "message_count":   len(posts),
        "categories":      cat_counts,
        "hint":            hint,
        "channels_active": len(surge_alerts),
    }


def telegram_for_map(region: Optional[str] = None) -> list[dict]:
    """
    Top-Nachrichten als strukturierte Map-Objekte für nexus_report.py.
    Kein Geo-Koordinaten (Telegram hat selten GPS), aber Info-Panels.
    """
    posts = fetch_scored_posts(region=region, hours_back=4, max_channels=5, min_score=2.0)
    markers = []
    for p in posts[:10]:
        ch_id = p.get("tg_channel", "")
        markers.append({
            "type":         "telegram",
            "channel":      ch_id,
            "channel_name": CHANNEL_META.get(ch_id, {}).get("name", ch_id),
            "text":         p.get("title", "")[:200],
            "score":        p["score"],
            "categories":   p.get("categories", []),
            "url":          p.get("url", ""),
            "timestamp":    p.get("date", ""),
            "age_min":      p.get("age_min", 0),
            "has_media":    p.get("has_media", False),
        })
    return markers


# ── Direktaufruf zum Testen ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if not _BS4_OK:
        print("❌ BeautifulSoup4 fehlt:")
        print("   pip install beautifulsoup4 --break-system-packages")
        print("   oder: venv\\Scripts\\pip install beautifulsoup4")
        sys.exit(1)

    kw = sys.argv[1] if len(sys.argv) > 1 else "ukraine"
    print(f"\nNEXUS Telegram OSINT – teste '{kw}'...")
    print("=" * 60)

    # Scored posts
    posts = fetch_scored_posts(region=kw, hours_back=6, max_channels=4, min_score=0.5)
    print(f"\n📊 {len(posts)} bewertete Posts:\n")
    for p in posts[:8]:
        age_s = f"{p['age_min']}min" if p['age_min'] < 120 else f"{p['age_min']//60}h"
        media = " 📷" if p.get("has_media") else ""
        cats  = ", ".join(p.get("categories", [])[:3])
        print(f"  [{p['score']:.1f}] {p.get('source','')} · vor {age_s}{media}")
        print(f"  {p['title'][:100]}")
        if cats:
            print(f"  → Kategorien: {cats}")
        print(f"  🔗 {p['url']}")
        print()

    # Escalation-Signal
    sig = telegram_for_escalation(region=kw)
    print(f"📈 Eskalationssignal:")
    print(f"   Surge aktiv:   {sig['surge_active']}")
    print(f"   Surge-Faktor:  {sig['surge_factor']}×")
    print(f"   Top-Score:     {sig['top_score']}")
    print(f"   Nachrichten:   {sig['message_count']}")
    print(f"   Kategorien:    {sig['categories']}")
    if sig['hint']:
        print(f"   Hinweis:       {sig['hint'][:100]}")


# ── Surge-Detektor ─────────────────────────────────────────────────────────────
# Rolling-Window: Speichert (channel -> [timestamp, ...]) der letzten Posts
import collections
_surge_history: dict[str, list[float]] = collections.defaultdict(list)
_surge_lock = __import__("threading").Lock()
SURGE_WINDOW_S  = 900   # 15-Minuten-Fenster fuer Baseline
SURGE_THRESHOLD = 5.0   # Faktor: aktuelle Rate / Baseline >= 5 = Surge


def _record_post_times(channel: str, posts: list[dict]) -> None:
    """Traegt Post-Zeitstempel in den Rolling-Buffer ein."""
    now = time.monotonic()
    with _surge_lock:
        buf = _surge_history[channel]
        for p in posts:
            age_s = (p.get("age_min") or 0) * 60
            ts    = now - age_s
            buf.append(ts)
        # Nur letzte 15 Minuten behalten
        cutoff = now - SURGE_WINDOW_S
        _surge_history[channel] = [t for t in buf if t >= cutoff]


def _surge_score(channel: str) -> float:
    """
    Gibt Surge-Score zurueck: aktuelle Rate (letzte 3min) / Baseline (letzte 15min).
    Score > SURGE_THRESHOLD = Spike.
    """
    now = now_ts = time.monotonic()
    with _surge_lock:
        buf = _surge_history.get(channel, [])
    if len(buf) < 3:
        return 0.0
    recent   = [t for t in buf if t >= now - 180]   # letzte 3 Minuten
    baseline = [t for t in buf if t >= now - 900]   # letzte 15 Minuten
    if not baseline:
        return 0.0
    rate_recent   = len(recent)   / 3.0    # Posts/Min aktuell
    rate_baseline = len(baseline) / 15.0   # Posts/Min Baseline
    if rate_baseline < 0.1:
        return 0.0
    return round(rate_recent / rate_baseline, 2)


def detect_surges(keyword_filter: str = "", top_n: int = 5) -> list[dict]:
    """
    Pollt Kanaele, traegt Zeitstempel ein und gibt Surge-Alerts zurueck.
    Gibt Liste von {channel, score, recent_count, msg} zurueck.
    """
    # Aktuelle Posts holen (relativ schnell, limit_per_channel=15 fuer guten Sample)
    posts = fetch_osint_channels(
        keyword_filter  = keyword_filter,
        limit_per_channel = 15,
        max_channels    = top_n,
    )

    # Posts nach Kanal gruppieren und Zeitstempel eintragen
    by_channel: dict[str, list[dict]] = collections.defaultdict(list)
    for p in posts:
        ch = p.get("tg_channel", "")
        if ch:
            by_channel[ch].append(p)

    for ch, ch_posts in by_channel.items():
        _record_post_times(ch, ch_posts)

    # Surge-Scores berechnen
    alerts = []
    for ch in by_channel:
        score = _surge_score(ch)
        if score >= SURGE_THRESHOLD:
            recent = [t for t in _surge_history.get(ch, [])
                      if t >= time.monotonic() - 180]
            alerts.append({
                "channel":      ch,
                "channel_url":  f"https://t.me/s/{ch}",
                "score":        score,
                "recent_count": len(recent),
                "message":      f"Surge x{score:.1f} in @{ch} – {len(recent)} Posts in 3min",
                "source":       "Telegram Surge",
            })

    alerts.sort(key=lambda a: a["score"], reverse=True)
    return alerts
