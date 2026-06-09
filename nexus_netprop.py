"""
nexus_netprop.py — T130: Netzwerk-Propagations-Analyse
=======================================================
Analysiert wie Informationen durch OSINT-Netzwerke fließen:
  - Wer meldet zuerst? (First-Reporter-Analyse)
  - Koordinierte vs. organische Amplifikation
  - Weiterleitungs-Kaskaden (Forwarding chains)
  - Zeitliche Burst-Erkennung (koordinierte Veröffentlichung)
  - Source-Credibility-Scores aus bekannten OSINT-Netzwerken
  - State-linked Accounts / Netzwerke erkennen
"""

import sys
import math
import json
import re
import datetime
import collections
from typing import Optional

# ─── Debug ───────────────────────────────────────────────────────────────────

def _dbg(msg: str) -> None:
    print(f"[NETPROP] {msg}", file=sys.stderr)

# ─── Known Source Classification ─────────────────────────────────────────────
# Basiert auf öffentlich dokumentierter Zuordnung (DFRLab, Bellingcat, EU DisinfoLab)

# Tier 1: Established, verified OSINT sources (hoch glaubwürdig)
_TIER1_SOURCES = {
    # OSINT-Spezialisten
    "bellingcat", "osintdefender", "intelcrab", "ukraine_weapons_tracker",
    "UAWeapons", "naalsio26", "militarylandnet", "oryxspioenkop",
    # Westliche Leitmedien
    "reuters", "bbc", "apnews", "afp",
    # Ukrainische Primärquellen
    "kyivindependent", "ukrinform", "ukrainska_pravda", "hromadske",
    # ISW
    "isw", "understandingwar",
    # Internationale Institutionen
    "icrc", "osce", "unhcr",
    # Satellitendaten-Analyse
    "planet", "maxar", "iceye",
    # Radio Free Europe
    "rferl", "currenttime",
}

# Tier 2: Reliable regional / secondary (mittel glaubwürdig)
_TIER2_SOURCES = {
    "dw", "france24", "theguardian", "nytimes", "washingtonpost",
    "politico", "thehill", "foreignpolicy", "nationalpost",
    "nexta_tv", "euromaidan_press", "uawarreport",
    "militarysummary", "suriyakmaps",
    "bradyafric", "tpyxa_info", "visegrad24",
}

# Bekannte State-linked / Propagandaquellen
_STATE_LINKED = {
    # Russland
    "rt", "rt_com", "russia_today", "tass", "ria_novosti", "rianovosti",
    "sputnik", "sputniknews", "pravda_ru", "voenkor_kotenok",
    "rybar", "boris_rozhin", "colonelcassad", "anna_news",
    "wargonzo", "s_a_vlasov", "boris_rozhin",
    "readovkanews", "mash", "shot_shot",
    # Chinesisch
    "global_times", "globaltimes", "xinhua", "cgtn",
    # Iranisch
    "press_tv", "presstv",
    # Weißrussland
    "belta_by",
}

# Koordinations-Indikatoren (Accounts die oft zusammen posten)
_COORD_CLUSTERS = [
    # Russische Amplifikationscluster (öffentlich dokumentiert)
    {"rybar", "boris_rozhin", "colonelcassad", "wargonzo", "readovkanews"},
    # Pro-russische Telegram-Cluster
    {"anna_news", "mash", "shot_shot", "voenkor_kotenok"},
    # Offizieller russischer Staatskanal-Cluster
    {"rt", "tass", "ria_novosti", "sputnik"},
]

def classify_source(source_name: str) -> dict:
    """
    Klassifiziert eine Quelle nach Glaubwürdigkeit und Netzwerk-Zugehörigkeit.

    Returns: {
        tier: 1|2|3,
        tier_label: str,
        state_linked: bool,
        state: str|None,
        credibility_score: float (0-1),
        cluster: str|None
    }
    """
    name = source_name.lower().replace(" ", "_").replace("-", "_").replace("@", "")
    # Strip t.me/ prefix
    name = re.sub(r'^t\.me/', '', name)
    # Strip https?://
    name = re.sub(r'^https?://[^/]*/(@?)', '', name)

    result = {
        "tier": 3,
        "tier_label": "Unklassifiziert",
        "state_linked": False,
        "state": None,
        "credibility_score": 0.4,
        "cluster": None,
    }

    # Check state-linked first
    for sl in _STATE_LINKED:
        if sl in name or name in sl:
            result["state_linked"] = True
            result["tier"] = 3
            result["tier_label"] = "Staatlich-verlinkt"
            result["credibility_score"] = 0.15
            # Identify state
            ru_sources = {"rt", "rt_com", "russia_today", "tass", "ria_novosti",
                          "sputnik", "pravda_ru", "rybar", "boris_rozhin",
                          "colonelcassad", "wargonzo", "readovkanews", "mash",
                          "shot_shot", "anna_news", "voenkor_kotenok", "s_a_vlasov"}
            if any(s in name for s in ru_sources):
                result["state"] = "RU"
            elif "global_times" in name or "xinhua" in name or "cgtn" in name:
                result["state"] = "CN"
            elif "press_tv" in name:
                result["state"] = "IR"
            break

    # Check tier 1
    if not result["state_linked"]:
        for t1 in _TIER1_SOURCES:
            if t1.lower() in name or name in t1.lower():
                result["tier"] = 1
                result["tier_label"] = "Verifizierter OSINT"
                result["credibility_score"] = 0.85
                break

    # Check tier 2
    if result["tier"] == 3 and not result["state_linked"]:
        for t2 in _TIER2_SOURCES:
            if t2.lower() in name or name in t2.lower():
                result["tier"] = 2
                result["tier_label"] = "Seriöse Sekundärquelle"
                result["credibility_score"] = 0.65
                break

    # Check cluster membership
    for i, cluster in enumerate(_COORD_CLUSTERS):
        for member in cluster:
            if member in name or name in member:
                result["cluster"] = f"Cluster_{i+1}"
                break

    return result


# ─── Propagation Cascade Analysis ────────────────────────────────────────────

def analyze_cascade(events: list) -> dict:
    """
    Analysiert eine Reihe von Nachrichten/Artikeln auf Propagationsmuster.

    events: list of dicts with keys:
      - source: str (Quellname/Kanalname)
      - title: str
      - content: str (optional)
      - timestamp: str (ISO format or EXIF format)
      - url: str (optional)

    Returns: {
        first_reporter: str,
        cascade_order: list[dict],
        coordination_score: float (0-1),
        coordination_verdict: str,
        burst_detected: bool,
        burst_window_minutes: int | None,
        state_amplification: bool,
        state_sources: list[str],
        timeline_minutes: list[int],
        summary: str
    }
    """
    if not events:
        return {"error": "Keine Events"}

    result = {
        "first_reporter": None,
        "cascade_order": [],
        "coordination_score": 0.0,
        "coordination_verdict": "ORGANISCH",
        "burst_detected": False,
        "burst_window_minutes": None,
        "state_amplification": False,
        "state_sources": [],
        "timeline_minutes": [],
        "summary": "",
    }

    # Parse timestamps
    parsed = []
    for ev in events:
        ts = _parse_ts(ev.get("timestamp", ""))
        if ts:
            parsed.append({**ev, "_ts": ts})

    if not parsed:
        result["summary"] = "Keine parsebaren Zeitstempel"
        return result

    # Sort by time
    parsed.sort(key=lambda x: x["_ts"])

    # First reporter
    result["first_reporter"] = parsed[0].get("source", "unbekannt")

    # Timeline in minutes from first event
    t0 = parsed[0]["_ts"]
    timeline = []
    cascade = []
    for ev in parsed:
        mins = int((ev["_ts"] - t0).total_seconds() / 60)
        timeline.append(mins)
        src_class = classify_source(ev.get("source", ""))
        cascade.append({
            "source": ev.get("source", "?"),
            "minutes_after_first": mins,
            "tier": src_class["tier"],
            "state_linked": src_class["state_linked"],
            "credibility": src_class["credibility_score"],
        })

    result["timeline_minutes"] = timeline
    result["cascade_order"] = cascade

    # State amplification
    state_sources = [c["source"] for c in cascade if c["state_linked"]]
    result["state_sources"] = state_sources
    result["state_amplification"] = len(state_sources) > 0

    # Burst detection: many events within short window
    if len(parsed) >= 3:
        # Check if 3+ events within 30 minutes
        for i in range(len(timeline) - 2):
            window = timeline[i + 2] - timeline[i]
            if window <= 30:
                result["burst_detected"] = True
                result["burst_window_minutes"] = window
                break

    # Coordination score
    score = 0.0

    # Factor 1: Burst (many sources posting within minutes of each other)
    if result["burst_detected"] and result["burst_window_minutes"] is not None:
        bw = result["burst_window_minutes"]
        if bw <= 5:
            score += 0.5
        elif bw <= 15:
            score += 0.3
        else:
            score += 0.1

    # Factor 2: State-linked sources in cascade
    state_fraction = len(state_sources) / len(cascade) if cascade else 0
    score += state_fraction * 0.4

    # Factor 3: Known coordination clusters active
    active_clusters = set()
    for c in cascade:
        src = c["source"].lower()
        for i, cluster in enumerate(_COORD_CLUSTERS):
            for member in cluster:
                if member in src:
                    active_clusters.add(i)
    score += len(active_clusters) * 0.15

    # Factor 4: Low-tier sources posting before high-tier
    first_tier = cascade[0]["tier"] if cascade else 3
    high_tier_first = first_tier == 1
    if not high_tier_first and any(c["tier"] == 1 for c in cascade):
        score += 0.1  # Suspicious if state/unknown reports before verified OSINT

    result["coordination_score"] = min(1.0, round(score, 2))

    if score >= 0.7:
        result["coordination_verdict"] = "KOORDINIERT"
    elif score >= 0.4:
        result["coordination_verdict"] = "VERDÄCHTIG"
    elif score >= 0.2:
        result["coordination_verdict"] = "LEICHT AUFFÄLLIG"
    else:
        result["coordination_verdict"] = "ORGANISCH"

    # Summary
    lines = [
        f"Erster Meldender: {result['first_reporter']}",
        f"Events analysiert: {len(cascade)}",
        f"Koordinations-Score: {result['coordination_score']:.0%} → {result['coordination_verdict']}",
    ]
    if result["burst_detected"]:
        lines.append(f"⚠️ Burst: {len(cascade)} Quellen in {result['burst_window_minutes']} Minuten")
    if result["state_amplification"]:
        lines.append(f"⚠️ Staatliche Amplifikation: {', '.join(state_sources[:3])}")
    if active_clusters:
        lines.append(f"⚠️ Bekannte Koordinationscluster aktiv: {len(active_clusters)}")

    result["summary"] = "\n".join(lines)
    return result


# ─── Timestamp Parsing ───────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> Optional[datetime.datetime]:
    """Parst verschiedene Timestamp-Formate."""
    if not ts_str:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(ts_str.strip(), fmt)
            return dt.replace(tzinfo=None)  # normalize to naive
        except ValueError:
            continue
    return None


# ─── Article-Level Propagation from NEXUS Pipeline ───────────────────────────

def analyze_articles_propagation(articles: list) -> dict:
    """
    Analysiert NEXUS-Artikel-Feed auf Propagationsmuster.
    articles: list from nexus_rss / nexus_telegram (dicts with 'source', 'title', 'published', 'content')

    Returns: {
        by_topic: dict[topic -> cascade_result],
        global_stats: dict,
        top_first_reporters: list,
        state_amplification_events: list,
        coordination_alerts: list
    }
    """
    result = {
        "by_topic": {},
        "global_stats": {},
        "top_first_reporters": [],
        "state_amplification_events": [],
        "coordination_alerts": [],
    }

    if not articles:
        return result

    # Group articles by topic similarity
    topic_groups = _group_by_topic(articles)

    first_reporters = collections.Counter()
    total_state_amp = 0

    for topic, group in topic_groups.items():
        if len(group) < 2:
            continue

        # Convert to cascade format
        events = []
        for art in group:
            events.append({
                "source": art.get("source", "unknown"),
                "title": art.get("title", ""),
                "content": art.get("content", "")[:200],
                "timestamp": art.get("published", art.get("timestamp", "")),
                "url": art.get("url", ""),
            })

        cascade = analyze_cascade(events)
        result["by_topic"][topic] = cascade

        if cascade.get("first_reporter"):
            first_reporters[cascade["first_reporter"]] += 1

        if cascade.get("state_amplification"):
            total_state_amp += 1
            result["state_amplification_events"].append({
                "topic": topic,
                "state_sources": cascade["state_sources"],
                "coordination_score": cascade["coordination_score"],
            })

        if cascade.get("coordination_verdict") in ("KOORDINIERT", "VERDÄCHTIG"):
            result["coordination_alerts"].append({
                "topic": topic,
                "verdict": cascade["coordination_verdict"],
                "score": cascade["coordination_score"],
                "burst": cascade.get("burst_detected"),
                "state": cascade.get("state_amplification"),
            })

    # Global stats
    source_classes = [classify_source(a.get("source", "")) for a in articles]
    tier1_count = sum(1 for s in source_classes if s["tier"] == 1)
    state_count = sum(1 for s in source_classes if s["state_linked"])

    result["global_stats"] = {
        "total_articles": len(articles),
        "topics_analyzed": len(topic_groups),
        "tier1_sources": tier1_count,
        "state_linked_count": state_count,
        "state_amplification_topics": total_state_amp,
        "coordination_alerts": len(result["coordination_alerts"]),
    }

    result["top_first_reporters"] = [
        {"source": src, "count": cnt}
        for src, cnt in first_reporters.most_common(5)
    ]

    return result


# ─── Topic Grouping (lightweight TF-IDF style) ───────────────────────────────

def _group_by_topic(articles: list, min_overlap: float = 0.15) -> dict:
    """
    Gruppiert Artikel nach Thema über keyword overlap.
    Returns dict: {representative_title -> [article, ...]}
    """
    if not articles:
        return {}

    # Stopwords (multilingual OSINT context)
    _STOP = {
        "the", "a", "an", "is", "in", "of", "to", "and", "or", "on", "at",
        "for", "by", "with", "as", "from", "that", "this", "was", "are",
        "be", "has", "have", "had", "it", "its", "not", "but", "new", "more",
        "über", "die", "der", "das", "und", "ist", "von", "mit", "nach",
        "ein", "eine", "im", "am", "dem", "des", "den", "sich", "aus",
    }

    def _keywords(text: str) -> set:
        words = re.findall(r'\b[A-Za-zÀ-ÿа-яА-ЯёЁ]{4,}\b', text.lower())
        return {w for w in words if w not in _STOP}

    def _jaccard(s1: set, s2: set) -> float:
        if not s1 or not s2:
            return 0.0
        inter = len(s1 & s2)
        union = len(s1 | s2)
        return inter / union if union else 0.0

    # Build keyword sets
    kw_sets = []
    for art in articles:
        text = (art.get("title", "") + " " + art.get("content", "")[:300])
        kw_sets.append(_keywords(text))

    # Union-Find for grouping
    parent = list(range(len(articles)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            if _jaccard(kw_sets[i], kw_sets[j]) >= min_overlap:
                union(i, j)

    # Build groups
    groups = collections.defaultdict(list)
    for i, art in enumerate(articles):
        root = find(i)
        groups[root].append(art)

    # Use first article title as representative key
    result = {}
    for root, group in groups.items():
        key = group[0].get("title", f"Thema_{root}")[:60]
        result[key] = group

    return result


# ─── LLM Context Formatter ───────────────────────────────────────────────────

def netprop_for_llm(prop_result: dict) -> str:
    """
    Formatiert Netzwerk-Propagations-Ergebnisse für LLM-Kontext.
    """
    if not prop_result:
        return ""

    lines = ["## Netzwerk-Propagations-Analyse", ""]

    stats = prop_result.get("global_stats", {})
    if stats:
        lines.append(f"**Quellen-Mix:** {stats.get('total_articles', 0)} Artikel, "
                     f"Tier-1: {stats.get('tier1_sources', 0)}, "
                     f"Staatlich-verlinkt: {stats.get('state_linked_count', 0)}")

    # Coordination alerts
    alerts = prop_result.get("coordination_alerts", [])
    if alerts:
        lines.append(f"\n**⚠️ Koordinations-Alarm ({len(alerts)} Themen):**")
        for alert in alerts[:3]:
            flags = []
            if alert.get("burst"):
                flags.append("Burst-Posting")
            if alert.get("state"):
                flags.append("Staatliche Amplifikation")
            flag_str = " + ".join(flags) if flags else ""
            lines.append(f"  • [{alert['verdict']}] {alert['topic'][:50]} — Score {alert['score']:.0%} {flag_str}")

    # State amplification
    state_events = prop_result.get("state_amplification_events", [])
    if state_events:
        lines.append(f"\n**Staatliche Quellen aktiv bei {len(state_events)} Themen:**")
        for ev in state_events[:3]:
            srcs = ", ".join(ev.get("state_sources", [])[:3])
            lines.append(f"  • {ev['topic'][:50]}: {srcs}")

    # Top first reporters
    reporters = prop_result.get("top_first_reporters", [])
    if reporters:
        lines.append(f"\n**Erste Meldungen von:** {', '.join(r['source'] for r in reporters[:4])}")

    return "\n".join(lines)


# ─── Self-Test ───────────────────────────────────────────────────────────────

def _self_test():
    print("=== nexus_netprop.py Selbsttest ===")

    # Test 1: Source classification
    print("\n[1] Quellen-Klassifizierung")
    tests = [
        ("bellingcat", "Tier 1"),
        ("rt", "Staatlich-RU"),
        ("rybar", "Staatlich-RU"),
        ("kyivindependent", "Tier 1"),
        ("reuters", "Tier 1"),
        ("unknownblogger123", "Unklassifiziert"),
    ]
    for name, expected in tests:
        c = classify_source(name)
        tier = f"Tier {c['tier']}" if not c["state_linked"] else f"Staatlich-{c.get('state', '?')}"
        status = "✓" if expected in tier or expected == tier else "≈"
        print(f"  {status} {name}: {tier} (Score: {c['credibility_score']:.2f})")

    # Test 2: Cascade analysis
    print("\n[2] Kaskaden-Analyse")
    now = datetime.datetime.utcnow()
    test_events = [
        {"source": "osintdefender", "title": "Russian forces attack Kharkiv",
         "timestamp": (now - datetime.timedelta(hours=2)).isoformat()},
        {"source": "kyivindependent", "title": "Attack on Kharkiv confirmed",
         "timestamp": (now - datetime.timedelta(hours=1, minutes=55)).isoformat()},
        {"source": "rt", "title": "Special operation near Kharkiv",
         "timestamp": (now - datetime.timedelta(hours=1, minutes=53)).isoformat()},
        {"source": "ria_novosti", "title": "Kharkiv operation underway",
         "timestamp": (now - datetime.timedelta(hours=1, minutes=52)).isoformat()},
        {"source": "tass", "title": "Liberation operation Kharkiv",
         "timestamp": (now - datetime.timedelta(hours=1, minutes=51)).isoformat()},
        {"source": "reuters", "title": "Ukraine says Russia attacks Kharkiv",
         "timestamp": (now - datetime.timedelta(hours=1, minutes=30)).isoformat()},
    ]
    cascade = analyze_cascade(test_events)
    print(f"  Erster Meldender: {cascade['first_reporter']}")
    print(f"  Koordinations-Score: {cascade['coordination_score']:.0%}")
    print(f"  Verdict: {cascade['coordination_verdict']}")
    print(f"  Staatliche Quellen: {cascade['state_sources']}")
    print(f"  Burst erkannt: {cascade['burst_detected']}")

    # Test 3: Full pipeline
    print("\n[3] Artikel-Pipeline")
    test_articles = [
        {"source": "reuters", "title": "Frontline update Zaporizhzhia",
         "published": (now - datetime.timedelta(hours=3)).isoformat(),
         "content": "Russian forces attack near Zaporizhzhia city"},
        {"source": "kyivindependent", "title": "Update Zaporizhzhia front",
         "published": (now - datetime.timedelta(hours=2, minutes=50)).isoformat(),
         "content": "Attack confirmed near Zaporizhzhia"},
        {"source": "rybar", "title": "Operation Zaporizhzhia",
         "published": (now - datetime.timedelta(hours=2, minutes=45)).isoformat(),
         "content": "Special operation near Zaporizhzhia"},
        {"source": "bellingcat", "title": "HIMARS strike Donetsk",
         "published": (now - datetime.timedelta(hours=5)).isoformat(),
         "content": "HIMARS strike confirmed in Donetsk region"},
        {"source": "osintdefender", "title": "HIMARS impact Donetsk",
         "published": (now - datetime.timedelta(hours=4, minutes=55)).isoformat(),
         "content": "HIMARS rocket impact Donetsk"},
    ]
    prop = analyze_articles_propagation(test_articles)
    stats = prop["global_stats"]
    print(f"  Artikel: {stats['total_articles']}, Themen: {stats['topics_analyzed']}")
    print(f"  Tier-1: {stats['tier1_sources']}, Staatlich: {stats['state_linked_count']}")
    print(f"  Koordinations-Alarme: {stats['coordination_alerts']}")
    print(f"  Top-Meldende: {prop['top_first_reporters']}")

    # Test 4: LLM formatter
    print("\n[4] LLM-Kontext")
    ctx = netprop_for_llm(prop)
    print(ctx[:300])

    print("\n=== Selbsttest abgeschlossen ===")


if __name__ == "__main__":
    _self_test()
