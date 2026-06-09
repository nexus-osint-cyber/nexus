"""
NEXUS – Informations-Netzwerk-Analyse  (Ebene 4 / Modul 4.13)
==============================================================
Analysiert wie Informationen durch Telegram-Kanäle / Social Media fließen:

  • Wer postet was zuerst? (Ursprungskanal-Erkennung)
  • Wie schnell propagiert eine Meldung? (Geschwindigkeit = Wichtigkeit)
  • Koordinierte Accounts die gleichzeitig posten (Astroturfing-Detektion)
  • Welche Kanäle sind verlässliche Erstquellen vs. Amplifier?
  • Inhaltliche Cluster: welche Themen häufen sich?

Öffentliche API:
  analyze_propagation(articles)    → NetworkResult
  netgraph_for_map(articles, region) → list[dict]
  netgraph_summary(result)         → str
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Datenklassen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChannelNode:
    name:         str
    source_type:  str   = "telegram"   # telegram | twitter | reddit | rss
    post_count:   int   = 0
    first_post_ts:float = 0.0
    topics:       list  = field(default_factory=list)
    credibility:  float = 0.5          # 0-1 aus nexus_credibility
    is_amplifier: bool  = False        # Amplifier oder Original-Quelle?
    is_suspicious:bool  = False        # Koordiniert / Astroturfing?


@dataclass
class StoryCluster:
    story_hash:   str
    title:        str
    first_seen:   float
    last_seen:    float
    sources:      list[str]   = field(default_factory=list)
    propagation_speed: float  = 0.0    # Wie schnell verbreitet sich die Story? (Quellen/Stunde)
    viral_score:  float       = 0.0    # 0-1
    geo_hint:     str         = ""


@dataclass
class NetworkResult:
    story_clusters:   list[StoryCluster] = field(default_factory=list)
    channel_nodes:    list[ChannelNode]  = field(default_factory=list)
    astroturfing_alerts: list[str]       = field(default_factory=list)
    top_origin_channels: list[str]       = field(default_factory=list)
    surge_topics:     list[str]          = field(default_factory=list)
    analysis_ts:      float = field(default_factory=time.time)


# ─────────────────────────────────────────────────────────────────────────────
# Text-Fingerprint (für Duplikat-/Propagations-Erkennung)
# ─────────────────────────────────────────────────────────────────────────────

_STOPWORDS = {
    "the","a","an","is","in","on","at","of","to","and","or","for","with",
    "this","that","are","was","were","be","has","have","had","it","from",
    "its","not","but","by","he","she","they","we","you","what","how","when",
    "ein","eine","der","die","das","und","ist","im","am","zu","für","mit",
    "на","в","и","не","по","за","от","до","из","как","это","что","то",
    "на","у","та","що","як","він","вона","ми","ви","це","але","де",
}

def _text_fingerprint(text: str) -> str:
    """Erstellt Fingerprint für Ähnlichkeits-Vergleich."""
    words = re.findall(r'\b\w{3,}\b', text.lower())
    filtered = [w for w in words if w not in _STOPWORDS][:15]
    filtered.sort()
    return hashlib.md5(" ".join(filtered).encode()).hexdigest()[:8]


def _text_similarity(a: str, b: str) -> float:
    """Einfache Wort-Überschneidungs-Ähnlichkeit (Jaccard)."""
    words_a = set(re.findall(r'\b\w{3,}\b', a.lower())) - _STOPWORDS
    words_b = set(re.findall(r'\b\w{3,}\b', b.lower())) - _STOPWORDS
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Story-Clustering
# ─────────────────────────────────────────────────────────────────────────────

def _cluster_stories(articles: list[dict]) -> list[StoryCluster]:
    """Gruppiert ähnliche Artikel zu Story-Clustern."""
    clusters: list[StoryCluster] = []
    assigned: set[int] = set()

    for i, art in enumerate(articles):
        if i in assigned:
            continue
        title = art.get("title", "")
        if not title:
            continue

        ts    = time.time() - (art.get("age_min", 1440) * 60)
        fp    = _text_fingerprint(title)
        sources = [art.get("source", art.get("channel", "unknown"))]
        cluster = StoryCluster(
            story_hash = fp,
            title      = title[:80],
            first_seen = ts,
            last_seen  = ts,
            sources    = sources,
        )

        # Ähnliche Artikel finden
        for j, art2 in enumerate(articles):
            if j == i or j in assigned:
                continue
            title2 = art2.get("title", "")
            if _text_similarity(title, title2) >= 0.35:
                ts2 = time.time() - (art2.get("age_min", 1440) * 60)
                cluster.sources.append(art2.get("source", art2.get("channel", "?")))
                cluster.first_seen = min(cluster.first_seen, ts2)
                cluster.last_seen  = max(cluster.last_seen, ts2)
                assigned.add(j)

        # Virality-Score
        n_sources = len(set(cluster.sources))
        time_span_h = max(0.1, (cluster.last_seen - cluster.first_seen) / 3600)
        cluster.propagation_speed = round(n_sources / time_span_h, 2)
        cluster.viral_score = min(1.0, math.log(1 + n_sources) / 3.0
                                  + min(0.3, cluster.propagation_speed / 10))

        clusters.append(cluster)
        assigned.add(i)

    clusters.sort(key=lambda c: c.viral_score, reverse=True)
    return clusters[:20]


# ─────────────────────────────────────────────────────────────────────────────
# Kanal-Analyse
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_channels(articles: list[dict]) -> list[ChannelNode]:
    """Analysiert Kanal-Aktivität und Posting-Muster."""
    channel_data: dict[str, dict] = defaultdict(lambda: {
        "posts": [], "topics": [], "first_ts": float("inf"), "last_ts": 0.0,
    })

    for art in articles:
        src = art.get("source", art.get("channel", "unknown"))
        ts  = time.time() - (art.get("age_min", 1440) * 60)
        channel_data[src]["posts"].append(ts)
        channel_data[src]["first_ts"] = min(channel_data[src]["first_ts"], ts)
        channel_data[src]["last_ts"]  = max(channel_data[src]["last_ts"], ts)

        # Topic-Extraktion (einfach)
        title = (art.get("title", "") + " " + art.get("summary", "")).lower()
        for topic in ["strike", "explosion", "attack", "ukraine", "russia",
                      "missile", "drone", "convoy", "offensive", "retreat",
                      "удар", "атака", "дрон", "наступ", "ракета"]:
            if topic in title:
                channel_data[src]["topics"].append(topic)

    nodes = []
    for name, data in channel_data.items():
        posts     = sorted(data["posts"])
        n_posts   = len(posts)
        if n_posts == 0:
            continue

        # Interval-Analyse: sehr regelmäßige Posts = suspicious
        intervals = [posts[i+1]-posts[i] for i in range(len(posts)-1)] if n_posts > 1 else [999]
        avg_interval = sum(intervals) / len(intervals) if intervals else 999
        interval_std = math.sqrt(sum((x-avg_interval)**2 for x in intervals)/len(intervals)) if len(intervals) > 1 else 0

        is_suspicious = (
            n_posts > 5 and
            avg_interval < 120 and  # Alle 2 Minuten
            interval_std < avg_interval * 0.1  # Sehr regelmäßig
        )

        # Credibility aus nexus_credibility.py (wenn verfügbar)
        cred = 0.5
        try:
            from nexus_credibility import source_credibility_score  # type: ignore
            cred = source_credibility_score(name)
        except Exception:
            pass

        top_topics = [t for t, c in Counter(data["topics"]).most_common(3)]

        nodes.append(ChannelNode(
            name         = name,
            source_type  = "telegram" if "t.me" in name or "@" in name else "rss",
            post_count   = n_posts,
            first_post_ts= data["first_ts"],
            topics       = top_topics,
            credibility  = cred,
            is_amplifier = n_posts > 3 and cred < 0.5,
            is_suspicious= is_suspicious,
        ))

    nodes.sort(key=lambda n: n.post_count, reverse=True)
    return nodes[:25]


# ─────────────────────────────────────────────────────────────────────────────
# Astroturfing-Detektion
# ─────────────────────────────────────────────────────────────────────────────

def _detect_astroturfing(articles: list[dict], nodes: list[ChannelNode]) -> list[str]:
    """
    Sucht nach koordinierten Posting-Mustern:
    - Viele Kanäle posten identischen Text innerhalb kurzer Zeit
    - Neue Accounts mit hoher Aktivität
    """
    alerts = []

    # Identische Fingerprints von verschiedenen Quellen
    fp_sources: dict[str, list[str]] = defaultdict(list)
    for art in articles:
        fp = _text_fingerprint(art.get("title", ""))
        src = art.get("source", "?")
        fp_sources[fp].append(src)

    for fp, sources in fp_sources.items():
        unique_sources = set(sources)
        if len(unique_sources) >= 4:
            alerts.append(
                f"⚠ KOORDINIERT: '{sources[0][:30]}' – von {len(unique_sources)} Kanälen "
                f"gleichzeitig gepostet: {', '.join(list(unique_sources)[:3])}"
            )

    # Suspicious Kanäle
    for node in nodes:
        if node.is_suspicious:
            alerts.append(
                f"⚠ BOT-VERDACHT: {node.name} – {node.post_count} Posts "
                f"mit sehr regelmäßigen Abständen"
            )

    return alerts[:5]


# ─────────────────────────────────────────────────────────────────────────────
# Surge-Topic-Erkennung
# ─────────────────────────────────────────────────────────────────────────────

def _find_surge_topics(articles: list[dict]) -> list[str]:
    """Findet Themen die gerade überdurchschnittlich oft gepostet werden."""
    topic_ts: dict[str, list[float]] = defaultdict(list)
    keywords = re.compile(
        r'\b(?:attack|strike|offensive|retreat|explosion|captured|destroyed|'
        r'convoy|missile|drone|HIMARS|Lancet|Shahed|ceasefire|nuclear|'
        r'удар|атака|наступ|відступ|вибух|ракета|дрон|полонений)\b',
        re.IGNORECASE
    )
    for art in articles:
        text = art.get("title", "") + " " + art.get("summary", "")
        ts = time.time() - (art.get("age_min", 1440) * 60)
        for m in keywords.finditer(text):
            topic_ts[m.group().lower()].append(ts)

    # Nur Themen der letzten 2h mit ≥3 Erwähnungen
    two_h_ago = time.time() - 7200
    surging = []
    for topic, timestamps in topic_ts.items():
        recent = [t for t in timestamps if t >= two_h_ago]
        if len(recent) >= 3:
            surging.append((topic, len(recent)))
    surging.sort(key=lambda x: x[1], reverse=True)
    return [f"{t} (×{n})" for t, n in surging[:6]]


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def analyze_propagation(articles: list[dict]) -> NetworkResult:
    """Vollständige Netzwerk-Analyse eines Artikel-Batches."""
    if not articles:
        return NetworkResult()

    clusters = _cluster_stories(articles)
    nodes    = _analyze_channels(articles)
    alerts   = _detect_astroturfing(articles, nodes)
    surges   = _find_surge_topics(articles)

    # Top Ursprungskanäle (hohe Credibility + früh posten)
    origin_candidates = sorted(
        [n for n in nodes if not n.is_amplifier and not n.is_suspicious],
        key=lambda n: n.credibility * (1 / max(0.01, n.post_count)),
        reverse=True,
    )
    top_origins = [n.name for n in origin_candidates[:5]]

    return NetworkResult(
        story_clusters       = clusters,
        channel_nodes        = nodes,
        astroturfing_alerts  = alerts,
        top_origin_channels  = top_origins,
        surge_topics         = surges,
    )


def netgraph_summary(result: NetworkResult, max_items: int = 6) -> str:
    if not result.story_clusters and not result.surge_topics:
        return ""
    lines = ["[NETGRAPH] Informations-Netzwerk-Analyse:\n"]
    if result.surge_topics:
        lines.append(f"  ⚡ Surge-Themen: {', '.join(result.surge_topics[:5])}")
    if result.astroturfing_alerts:
        for a in result.astroturfing_alerts[:3]:
            lines.append(f"  {a}")
    if result.top_origin_channels:
        lines.append(f"  📡 Erstquellen: {', '.join(result.top_origin_channels[:3])}")
    if result.story_clusters:
        lines.append(f"\n  Virale Stories:")
        for c in result.story_clusters[:max_items]:
            if c.viral_score > 0.3:
                lines.append(
                    f"    → {c.title[:50]} "
                    f"({len(c.sources)} Quellen, {c.propagation_speed:.1f} Qu/h)"
                )
    return "\n".join(lines)


def netgraph_for_map(articles: list[dict], region: str = "") -> list[dict]:
    """
    Netgraph gibt keine geografischen Marker aus (Netzwerk-Analyse ist nicht geo-spezifisch).
    Gibt leere Liste zurück – wird im Live-Server als Context-String genutzt.
    """
    return []


if __name__ == "__main__":
    test_articles = [
        {"title": "Attack near Kharkiv confirmed", "source": "@ukraine_war", "age_min": 10},
        {"title": "Strike near Kharkiv reported", "source": "@mil_osint", "age_min": 12},
        {"title": "Explosion in Kharkiv area", "source": "rss_news", "age_min": 15},
        {"title": "Lancet drone attack confirmed", "source": "@rybar_en", "age_min": 5},
        {"title": "Lancet drone strike confirmed", "source": "@ua_mil", "age_min": 6},
        {"title": "Trump tweets something", "source": "news_feed", "age_min": 30},
    ]
    result = analyze_propagation(test_articles)
    print(netgraph_summary(result))
    print(f"\nKluster: {len(result.story_clusters)}")
    print(f"Kanäle: {len(result.channel_nodes)}")
    print(f"Alerts: {result.astroturfing_alerts}")
