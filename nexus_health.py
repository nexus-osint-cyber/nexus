"""
nexus_health.py – WHO/ProMED Ausbruchs- und Seuchenfrühwarnung
==============================================================
Aggregiert offizielle Gesundheits-Warnmeldungen aus:
  - WHO Disease Outbreak News (DON) – RSS
  - ProMED Mail – weltweite Seuchen-Früherkennung (RSS)
  - CDC Travel Health Notices (RSS)
  - ECDC – Europäisches Infektionskrankheiten-Zentrum (Atom)

Keine API-Keys nötig. Alle Quellen sind öffentliche RSS-Feeds.

Ausgabe: Liste von Events mit Geo-Koordinaten (wenn verfügbar),
         Schweregrad-Score, Pathogen, betroffene Region.
"""

import re
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional
import xml.etree.ElementTree as ET

log = logging.getLogger("nexus.health")

# ── Feed-URLs ────────────────────────────────────────────────────────────────

FEEDS = {
    "WHO-DON": {
        "url":    "https://www.who.int/feeds/entity/csr/don/en/rss.xml",
        "label":  "WHO Disease Outbreak News",
        "weight": 1.0,
    },
    "ProMED": {
        "url":    "https://promedmail.org/feed/",
        "label":  "ProMED Mail",
        "weight": 0.8,
    },
    "CDC": {
        "url":    "https://wwwnc.cdc.gov/travel/notices/rss.xml",
        "label":  "CDC Travel Health Notices",
        "weight": 0.7,
    },
    "ECDC": {
        "url":    "https://www.ecdc.europa.eu/en/rss.xml",
        "label":  "ECDC",
        "weight": 0.7,
    },
}

# ── Schweregrad-Keywords ─────────────────────────────────────────────────────

SEVERITY_HIGH = [
    "ebola", "marburg", "plague", "cholera", "anthrax", "smallpox",
    "mpox", "monkeypox", "hemorrhagic", "hämorrhagisch", "outbreak",
    "emergency", "notfall", "pandemic", "warnung", "alert",
    "h5n1", "h1n1", "avian influenza", "novel coronavirus", "mers",
    "sars", "polio", "yellow fever", "dengue", "lassa",
]
SEVERITY_MED = [
    "measles", "masern", "hepatitis", "typhoid", "typhus", "salmonella",
    "listeria", "meningitis", "legionella", "norovirus", "influenza",
    "tuberculosis", "tuberkulose", "malaria", "zika",
]

# ── Länder → Koordinaten (häufigste Ausbruchsregionen) ──────────────────────

COUNTRY_COORDS = {
    "nigeria": (9.08, 8.68), "congo": (-4.32, 15.32), "drc": (-4.32, 15.32),
    "guinea": (11.0, -10.0), "sierra leone": (8.46, -11.78),
    "liberia": (6.43, -9.43), "cameroon": (3.85, 11.50),
    "ethiopia": (9.15, 40.49), "somalia": (5.15, 46.20),
    "sudan": (12.86, 30.22), "south sudan": (6.88, 31.57),
    "kenya": (-1.29, 36.82), "uganda": (1.37, 32.29),
    "mozambique": (-18.67, 35.53), "zimbabwe": (-19.02, 29.15),
    "madagascar": (-18.77, 46.87), "india": (20.59, 78.96),
    "pakistan": (30.38, 69.35), "bangladesh": (23.68, 90.35),
    "myanmar": (17.11, 96.46), "indonesia": (-0.79, 113.92),
    "philippines": (12.88, 121.77), "china": (35.86, 104.20),
    "brazil": (-14.24, -51.93), "peru": (-9.19, -75.02),
    "colombia": (4.57, -74.30), "haiti": (18.97, -72.29),
    "ukraine": (48.38, 31.17), "iraq": (33.22, 43.68),
    "syria": (34.80, 38.99), "yemen": (15.55, 48.52),
    "afghanistan": (33.94, 67.71), "saudi arabia": (23.89, 45.08),
    "iran": (32.43, 53.69), "turkey": (38.96, 35.24),
    "germany": (51.17, 10.45), "france": (46.23, 2.21),
    "usa": (37.09, -95.71), "united states": (37.09, -95.71),
    "mexico": (23.63, -102.55),
}


# ── RSS-Parsing ───────────────────────────────────────────────────────────────

def _parse_rss(xml_bytes: bytes, source_key: str) -> list:
    """Parst einen RSS/Atom-Feed und gibt Liste von Items zurück."""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning(f"RSS-Parse-Fehler {source_key}: {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        desc  = (item.findtext("description") or "").strip()
        link  = (item.findtext("link") or "").strip()
        pub   = (item.findtext("pubDate") or "").strip()
        items.append({"title": title, "desc": desc, "link": link,
                      "pub": pub, "source": source_key})

    # Atom
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        desc  = (entry.findtext("atom:summary", namespaces=ns) or
                 entry.findtext("atom:content", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        link  = link_el.get("href", "") if link_el is not None else ""
        pub   = (entry.findtext("atom:published", namespaces=ns) or "").strip()
        items.append({"title": title, "desc": desc, "link": link,
                      "pub": pub, "source": source_key})

    return items


def _fetch_feed(url: str, source_key: str, timeout: int = 10) -> list:
    """Holt und parst einen RSS/Atom-Feed. Gibt [] bei Fehler zurück."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "NEXUS-HealthMonitor/1.0",
                          "Accept": "application/rss+xml, application/xml, text/xml"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return _parse_rss(data, source_key)
    except Exception as e:
        log.debug(f"Feed {source_key} Fehler: {e}")
        return []


# ── Schweregrad + Geo ─────────────────────────────────────────────────────────

def _severity_score(title: str, desc: str) -> tuple[str, float]:
    """
    Gibt (Schweregrad-String, Score 0–1) zurück.
    high=0.8–1.0, medium=0.5–0.7, low=0.2–0.4
    """
    text = (title + " " + desc).lower()

    # Explosiv-Keywords (Bioterror, Pandemie)
    if any(k in text for k in SEVERITY_HIGH):
        # Zusätzliche Punkte für häufige Treffer
        hits = sum(1 for k in SEVERITY_HIGH if k in text)
        return ("high", min(1.0, 0.75 + hits * 0.05))

    if any(k in text for k in SEVERITY_MED):
        hits = sum(1 for k in SEVERITY_MED if k in text)
        return ("medium", min(0.74, 0.5 + hits * 0.04))

    return ("low", 0.2)


def _extract_country(text: str) -> Optional[tuple[str, float, float]]:
    """
    Sucht nach Ländernamen im Text und gibt (country, lat, lon) zurück.
    """
    text_lower = text.lower()
    for country, (lat, lon) in COUNTRY_COORDS.items():
        if country in text_lower:
            return (country.title(), lat, lon)
    return None


def _extract_pathogen(text: str) -> str:
    """Extrahiert Krankheitserreger aus Text."""
    pathogens = [
        "ebola", "marburg", "cholera", "plague", "anthrax", "mpox",
        "monkeypox", "h5n1", "h1n1", "mers-cov", "mers", "sars-cov-2",
        "covid", "sars", "dengue", "zika", "chikungunya", "lassa",
        "nipah", "hendra", "rift valley", "west nile", "polio",
        "measles", "masern", "typhoid", "typhus", "hepatitis a",
        "hepatitis b", "hepatitis c", "meningitis", "malaria",
        "tuberculosis", "influenza",
    ]
    text_lower = text.lower()
    for p in pathogens:
        if p in text_lower:
            return p.title()
    return "Unbekannt"


# ── Haupt-Funktion ─────────────────────────────────────────────────────────────

def get_health_alerts(
    region: Optional[str] = None,
    max_age_days: int = 7,
    min_severity: str = "low",
) -> list:
    """
    Sammelt aktuelle Gesundheits-Warnmeldungen.

    Args:
        region:       Filter nach Länder-/Regionsname (optional)
        max_age_days: Nur Meldungen der letzten N Tage
        min_severity: "low", "medium", oder "high"

    Returns:
        Liste von Event-Dicts, sortiert nach Schweregrad.
    """
    all_items = []

    for key, feed_cfg in FEEDS.items():
        items = _fetch_feed(feed_cfg["url"], key)
        all_items.extend(items)

    log.info(f"Health: {len(all_items)} Rohmeldungen aus {len(FEEDS)} Feeds")

    # Verarbeiten
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    events = []
    seen_titles = set()

    severity_min = {"low": 0.0, "medium": 0.5, "high": 0.75}.get(min_severity, 0.0)

    for item in all_items:
        title = item.get("title", "")
        desc  = item.get("desc", "")
        if not title:
            continue

        # Duplikat-Filter
        title_key = re.sub(r'\W+', '', title.lower())[:50]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # Schweregrad
        severity, score = _severity_score(title, desc)
        if score < severity_min:
            continue

        # Geo
        geo = _extract_country(title + " " + desc)
        if region and geo:
            if region.lower() not in geo[0].lower():
                # Region-Filter: nur Events die zur angegebenen Region passen
                # Großzügig: prüfe ob Region irgendwo im Text vorkommt
                full_text = (title + " " + desc).lower()
                if region.lower() not in full_text:
                    continue

        pathogen = _extract_pathogen(title + " " + desc)
        feed_label = FEEDS.get(item.get("source", ""), {}).get("label", item.get("source", ""))

        event = {
            "title":    title,
            "desc":     (desc[:200] + "...") if len(desc) > 200 else desc,
            "link":     item.get("link", ""),
            "source":   feed_label,
            "severity": severity,
            "score":    score,
            "pathogen": pathogen,
            "pub":      item.get("pub", ""),
        }

        if geo:
            event["country"] = geo[0]
            event["lat"]     = geo[1]
            event["lon"]     = geo[2]

        events.append(event)

    # Sortieren nach Schweregrad
    events.sort(key=lambda x: x["score"], reverse=True)
    log.info(f"Health: {len(events)} relevante Meldungen nach Filterung")
    return events[:50]


def get_health_for_map(region: Optional[str] = None) -> list:
    """Gibt Karten-kompatible Marker für Gesundheits-Alerts zurück."""
    alerts = get_health_alerts(region=region, min_severity="medium")
    markers = []
    for a in alerts:
        if not a.get("lat") or not a.get("lon"):
            continue
        sev    = a["severity"]
        color  = "#cc0000" if sev == "high" else "#ff8800" if sev == "medium" else "#ffcc00"
        markers.append({
            "lat":   a["lat"],
            "lon":   a["lon"],
            "type":  "health",
            "icon":  "🦠",
            "color": color,
            "title": a["title"][:60],
            "popup": (
                f"<b>🦠 {a['title'][:80]}</b><br>"
                f"<b>Erreger:</b> {a['pathogen']}<br>"
                f"<b>Land:</b> {a.get('country', '?')}<br>"
                f"<b>Schwere:</b> {sev.upper()} ({a['score']:.0%})<br>"
                f"<b>Quelle:</b> {a['source']}<br>"
                f"<small>{a['pub'][:20]}</small>"
                + (f"<br><a href='{a['link']}' target='_blank'>→ Meldung</a>"
                   if a.get("link") else "")
            ),
            "source": a["source"],
        })
    return markers[:30]


def format_health_terminal(alerts: list) -> str:
    """Formatiert Health-Alerts für Terminal-Ausgabe."""
    if not alerts:
        return "\n  Keine aktuellen Gesundheitswarnungen in den Feeds.\n"

    lines = [
        "",
        "\033[31m╔══ 🦠 GESUNDHEITS-FRÜHWARNUNG ════════════════════════╗\033[0m",
    ]
    for a in alerts[:10]:
        sev_col = (
            "\033[91m" if a["severity"] == "high" else
            "\033[93m" if a["severity"] == "medium" else "\033[37m"
        )
        lines.append(
            f"\033[31m║\033[0m  {sev_col}[{a['severity'].upper():6}]\033[0m "
            f"{a['title'][:55]}"
        )
        if a.get("country"):
            lines.append(
                f"\033[31m║\033[0m         "
                f"Land: {a['country']:<15} "
                f"Erreger: {a['pathogen']:<15} "
                f"Quelle: {a['source'][:12]}"
            )
    lines.append("\033[31m╚══════════════════════════════════════════════════════╝\033[0m")
    lines.append("")
    return "\n".join(lines)


# ── Standalone Test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else None
    alerts = get_health_alerts(region=region, max_age_days=14)
    print(format_health_terminal(alerts))
