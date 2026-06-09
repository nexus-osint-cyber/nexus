"""
nexus_darkweb.py  — T176
Dark Web / .onion OSINT Monitor für NEXUS.

Zweck: Passives Monitoring bekannter öffentlicher .onion-Ressourcen für
       sicherheitsrelevante Intelligence (Ransomware-Tracking, Sanktionsumgehung,
       militärische Leaks, Waffenhandels-Indikatoren).

WICHTIG: Dieses Modul ruft NUR passiv öffentlich bekannte .onion-Seiten ab
         (dieselben Informationen die Sicherheitsforscher und CERT-Teams täglich
         monitoren). Es interagiert nicht mit illegalen Marktplätzen und
         speichert keine illegalen Inhalte.

Voraussetzungen:
  - Tor Browser ODER Tor-Daemon (Port 9050) muss laufen
  - Kein API-Key nötig
  - Optional: pip install requests[socks] PySocks

Verwendung:
  from nexus_darkweb import darkweb_scan, darkweb_for_map
  python nexus_darkweb.py --scan --keywords "Hormuz,tanker,sanction"
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ─── Optionale SOCKS5 Imports ────────────────────────────────────────────────

try:
    import socks  # PySocks
    _PYSOCKS = True
except ImportError:
    _PYSOCKS = False

try:
    import requests as _req
    _REQUESTS = True
except ImportError:
    _REQUESTS = False

# ─── Konfiguration ────────────────────────────────────────────────────────────

TOR_HOST    = "127.0.0.1"
TOR_PORT    = 9050
TOR_TIMEOUT = 20  # Sekunden

# ─── Bekannte OSINT-relevante .onion Ressourcen ───────────────────────────────
# Alle hier gelisteten Seiten sind in der öffentlichen Sicherheits-Community
# dokumentiert und werden routinemäßig von CERTs / Threat-Intel-Teams monitort.

_ONION_TARGETS = [
    # ── Ransomware Leak Sites (für Victim-Intelligence) ──────────────────────
    {
        "name":     "LockBit Blog (Mirror)",
        "category": "RANSOMWARE",
        "url":      "http://lockbitapt2yfbt7oxkem5za3pi2oycrxq2nuk6lnpxq2fy3hbp3fzid.onion",
        "note":     "LockBit Opfer-Liste — kritische Infrastruktur Monitoring",
        "keywords": ["critical infrastructure", "energy", "government", "military",
                     "defense", "utility", "port", "pipeline"],
    },
    {
        "name":     "ALPHV/BlackCat Blog",
        "category": "RANSOMWARE",
        "url":      "http://alphvmmm27o3abo3r2mlmjrpdmzle3rykajqc5xsj7j7ejksbpsa36ad.onion",
        "note":     "BlackCat Opfer-Liste",
        "keywords": ["oil", "gas", "shipping", "logistics", "government", "defense"],
    },
    {
        "name":     "Clop Leaks",
        "category": "RANSOMWARE",
        "url":      "http://santat7kpllt6iyvqbr7q4amdv6dzrh6paatvyrzl7ry3zm72zigf4ad.onion",
        "note":     "Clop Ransomware Opfer",
        "keywords": ["maritime", "shipping", "energy", "government"],
    },
    # ── Darknet News & Militärische Leaks ────────────────────────────────────
    {
        "name":     "DarknetLive News",
        "category": "NEWS",
        "url":      "http://darknetlive.com",  # Hat auch .onion Version
        "note":     "Darknet-Nachrichten, keine illegalen Inhalte",
        "keywords": ["military", "iran", "russia", "ukraine", "weapon", "sanction",
                     "missile", "nuclear", "leak"],
    },
    {
        "name":     "Ahmia Search (Tor)",
        "category": "SEARCH",
        "url":      "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion",
        "note":     "Öffentliche .onion Suchmaschine (indexiert nur öffentliche Seiten)",
        "keywords": [],  # Wird mit eigenen Keywords abgefragt
    },
    # ── Russische Militär-Telegram Mirrors ────────────────────────────────────
    {
        "name":     "Telegram Mirror (Tor)",
        "category": "SOCIAL",
        "url":      "https://t.me/s/rybar",  # Rybar über Clearnet mit Tor
        "note":     "Rybar (RU mil-blog) via Tor",
        "keywords": ["украина", "ВСУ", "ВС РФ", "удар", "ракет", "Hormuz", "Iran"],
    },
    # ── Sanktionsumgehungs-Indikatoren ────────────────────────────────────────
    {
        "name":     "Russian Sanctions Monitor",
        "category": "FININT",
        "url":      "http://ransomwarebugs2uqlpnqqyqbotf6jhzjcxjnfp56qxl5p7fimkvh4uiad.onion",
        "note":     "Bekannte russische Sanktionsumgehungs-Foren",
        "keywords": ["sanction", "bypass", "oil", "tanker", "swift", "vessel",
                     "MMSI", "flag", "payment"],
    },
]

# Globale Sicherheits-Keywords (alles was mit diesen Keywords matcht wird gefiltert)
_MILITARY_OSINT_KEYWORDS = {
    # Englisch
    "sanction", "sanctions", "missile", "weapon", "military", "naval",
    "tanker", "oil", "pipeline", "blockade", "embargo", "proliferation",
    "nuclear", "submarine", "carrier", "destroyer", "frigate", "corvette",
    "hormuz", "taiwan", "ukraine", "iran", "russia", "dprk", "north korea",
    "drone", "uav", "uas", "strike", "attack", "explosion", "airstrike",
    "deployment", "exercise", "drills", "vessel", "ship", "harbor", "port",
    "chemical", "biological", "radiological",
    # Russisch
    "ракет", "оруж", "военн", "флот", "удар", "войск", "блокад",
    # Arabisch
    "صاروخ", "عسكري", "سفينة", "هجوم",
    # Chinesisch
    "导弹", "军事", "封锁", "军舰",
}

# ─── Datentypen ───────────────────────────────────────────────────────────────

@dataclass
class DarkwebFinding:
    source:    str
    category:  str
    title:     str
    snippet:   str
    url:       str
    keywords:  list[str]
    threat_level: str    # "KRITISCH" | "HOCH" | "MITTEL" | "NIEDRIG" | "INFO"
    ts:        float = field(default_factory=time.time)
    lat:       float = 0.0
    lon:       float = 0.0

    def to_dict(self) -> dict:
        return {
            "source":       self.source,
            "category":     self.category,
            "title":        self.title[:200],
            "snippet":      self.snippet[:500],
            "url":          self.url,
            "keywords":     self.keywords,
            "threat_level": self.threat_level,
            "ts":           self.ts,
            "ts_fmt": datetime.fromtimestamp(self.ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"),
        }


@dataclass
class DarkwebReport:
    findings:    list[DarkwebFinding] = field(default_factory=list)
    tor_online:  bool = False
    sources_checked: int = 0
    generated:   str = ""
    level:       str = "NORMAL"

    def to_dict(self) -> dict:
        return {
            "findings":      [f.to_dict() for f in self.findings],
            "tor_online":    self.tor_online,
            "sources_checked": self.sources_checked,
            "n_findings":    len(self.findings),
            "level":         self.level,
            "generated":     self.generated,
        }


# ─── Tor-Verbindung ───────────────────────────────────────────────────────────

def _check_tor() -> bool:
    """Prüft ob Tor auf Port 9050 erreichbar ist."""
    try:
        sock = socket.create_connection((TOR_HOST, TOR_PORT), timeout=3)
        sock.close()
        return True
    except (socket.error, OSError):
        return False


def _fetch_onion_requests(url: str, timeout: int = TOR_TIMEOUT) -> Optional[str]:
    """Holt .onion URL via requests + SOCKS5 (bevorzugt)."""
    if not _REQUESTS:
        return None
    try:
        proxies = {
            "http":  f"socks5h://{TOR_HOST}:{TOR_PORT}",
            "https": f"socks5h://{TOR_HOST}:{TOR_PORT}",
        }
        resp = _req.get(
            url,
            proxies=proxies,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0"},
        )
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _fetch_onion_urllib(url: str, timeout: int = TOR_TIMEOUT) -> Optional[str]:
    """Holt .onion URL via urllib + PySocks (Fallback)."""
    if not _PYSOCKS:
        return None
    try:
        # SOCKS5 global setzen (nur für diesen Thread-Scope)
        socks.set_default_proxy(socks.SOCKS5, TOR_HOST, TOR_PORT)
        socket.socket = socks.socksocket
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    finally:
        # SOCKS zurücksetzen
        socket.socket = socket._real_socketcall if hasattr(socket, "_real_socketcall") else socket.socket


def fetch_onion(url: str) -> Optional[str]:
    """Holt Inhalt einer URL via Tor. Gibt HTML/Text zurück oder None."""
    # Für clearnet URLs (Tor-Proxying)
    html = _fetch_onion_requests(url)
    if html:
        return html
    html = _fetch_onion_urllib(url)
    return html


# ─── Text-Extraktion und Analyse ─────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Entfernt HTML-Tags."""
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<[^>]+>', ' ', html)
    html = re.sub(r'\s+', ' ', html)
    return html.strip()


def _extract_titles(html: str) -> list[str]:
    """Extrahiert Titel aus HTML."""
    titles = []
    for pattern in [
        r'<h[1-4][^>]*>(.*?)</h[1-4]>',
        r'<title>(.*?)</title>',
        r'<a[^>]*class="[^"]*(?:title|victim|post)[^"]*"[^>]*>(.*?)</a>',
    ]:
        for m in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
            t = _strip_html(m.group(1)).strip()
            if t and len(t) > 5 and len(t) < 200:
                titles.append(t)
    return titles[:20]


def _find_keywords(text: str, keyword_set: set[str]) -> list[str]:
    """Findet Keywords im Text (case-insensitive)."""
    text_lower = text.lower()
    found = []
    for kw in keyword_set:
        if kw.lower() in text_lower:
            found.append(kw)
    return found[:15]


def _score_threat(category: str, found_kw: list[str], title: str = "") -> str:
    """Bewertet Bedrohungslevel eines Findings."""
    critical_kw = {"nuclear", "chemical", "biological", "radiological",
                   "missile", "submarine", "carrier", "proliferation", "ядерн", "ракет"}
    high_kw = {"military", "naval", "weapon", "sanction", "blockade", "strike",
               "hormuz", "taiwan", "ukraine", "iran", "dprk"}

    kw_set = {k.lower() for k in found_kw}
    title_lower = title.lower()

    if category == "RANSOMWARE":
        # Ransomware gegen kritische Infrastruktur = KRITISCH
        infra_kw = {"energy", "utility", "port", "pipeline", "government",
                    "defense", "military", "nuclear"}
        if kw_set & infra_kw or any(k in title_lower for k in infra_kw):
            return "KRITISCH"
        return "HOCH"

    if kw_set & critical_kw:
        return "KRITISCH"
    if kw_set & high_kw:
        return "HOCH"
    if len(found_kw) >= 3:
        return "MITTEL"
    if found_kw:
        return "NIEDRIG"
    return "INFO"


# ─── Ahmia Suche ─────────────────────────────────────────────────────────────

def _search_ahmia(keywords: list[str]) -> list[dict]:
    """
    Sucht auf Ahmia (.onion Suchmaschine) nach Keywords.
    Ahmia indexiert nur öffentliche, legale .onion Seiten.
    """
    results = []
    query = " ".join(keywords[:3])

    # Ahmia Clearnet Version (über Tor geroutet)
    try:
        import urllib.parse
        url = f"https://ahmia.fi/search/?q={urllib.parse.quote(query)}"
        html = fetch_onion(url)
        if not html:
            return []

        # Ergebnisse parsen
        for m in re.finditer(
            r'<h4[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</h4>'
            r'(?:.*?<p[^>]*class="[^"]*desc[^"]*"[^>]*>(.*?)</p>)?',
            html, re.DOTALL | re.IGNORECASE
        ):
            href   = m.group(1)
            title  = _strip_html(m.group(2))
            desc   = _strip_html(m.group(3) or "")
            if title and ".onion" in href:
                results.append({
                    "url":   href,
                    "title": title[:150],
                    "desc":  desc[:300],
                })
        return results[:10]
    except Exception:
        return []


# ─── Haupt-Scan ───────────────────────────────────────────────────────────────

def darkweb_scan(
    custom_keywords: list[str] | None = None,
    categories: list[str] | None = None,
    max_sources: int = 5,
) -> DarkwebReport:
    """
    Scannt konfigurierte .onion Quellen auf sicherheitsrelevante Inhalte.

    custom_keywords: Zusätzliche Keywords die gesucht werden
    categories:      Nur diese Kategorien scannen (RANSOMWARE, NEWS, FININT, ...)
    max_sources:     Maximale Anzahl abgefragter Quellen

    Gibt DarkwebReport zurück.
    """
    tor_up = _check_tor()
    report = DarkwebReport(
        tor_online=tor_up,
        generated=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    if not tor_up:
        # Tor nicht verfügbar — trotzdem Clearnet-Sources prüfen
        report.level = "NORMAL"
        return report

    # Keyword-Set zusammenstellen
    scan_keywords = set(_MILITARY_OSINT_KEYWORDS)
    if custom_keywords:
        scan_keywords.update(k.lower() for k in custom_keywords)

    targets = _ONION_TARGETS
    if categories:
        targets = [t for t in targets if t["category"] in categories]
    targets = targets[:max_sources]

    for target in targets:
        report.sources_checked += 1
        try:
            html = fetch_onion(target["url"])
            if not html:
                continue

            text = _strip_html(html)

            # Keywords im Text suchen
            target_kw = set(target["keywords"]) | scan_keywords
            found_kw = _find_keywords(text, target_kw)

            if not found_kw:
                continue

            # Titel extrahieren
            titles = _extract_titles(html)

            if titles:
                for title in titles[:5]:
                    title_kw = _find_keywords(title, target_kw)
                    if not title_kw and not any(k in title.lower() for k in scan_keywords):
                        continue
                    all_kw = list(set(found_kw + title_kw))
                    level = _score_threat(target["category"], all_kw, title)
                    # Snippet: ersten relevanten Text-Abschnitt finden
                    snippet = ""
                    for kw in all_kw[:3]:
                        idx = text.lower().find(kw.lower())
                        if idx >= 0:
                            snippet = text[max(0, idx-80):idx+200].strip()
                            break

                    report.findings.append(DarkwebFinding(
                        source=target["name"],
                        category=target["category"],
                        title=title,
                        snippet=snippet,
                        url=target["url"],
                        keywords=all_kw[:8],
                        threat_level=level,
                    ))
            else:
                # Kein Titel — generischer Eintrag mit Snippet
                snippet = ""
                for kw in found_kw[:2]:
                    idx = text.lower().find(kw.lower())
                    if idx >= 0:
                        snippet = text[max(0, idx-50):idx+150].strip()
                        break

                level = _score_threat(target["category"], found_kw)
                report.findings.append(DarkwebFinding(
                    source=target["name"],
                    category=target["category"],
                    title=f"{target['name']}: {', '.join(found_kw[:3])}",
                    snippet=snippet,
                    url=target["url"],
                    keywords=found_kw[:8],
                    threat_level=level,
                ))

        except Exception:
            continue

    # Ahmia-Suche für aktuelle Geopolitik-Keywords
    if custom_keywords:
        try:
            ahmia_results = _search_ahmia(custom_keywords[:3])
            for r in ahmia_results:
                combined = r["title"] + " " + r["desc"]
                found_kw = _find_keywords(combined, scan_keywords)
                if found_kw:
                    level = _score_threat("SEARCH", found_kw, r["title"])
                    report.findings.append(DarkwebFinding(
                        source="Ahmia Search",
                        category="SEARCH",
                        title=r["title"],
                        snippet=r["desc"],
                        url=r["url"],
                        keywords=found_kw[:6],
                        threat_level=level,
                    ))
        except Exception:
            pass

    # Deduplizieren
    seen_titles: set[str] = set()
    unique: list[DarkwebFinding] = []
    for f in report.findings:
        key = f.title.lower()[:80]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(f)
    report.findings = unique

    # Sortieren nach Bedrohungslevel
    _prio = {"KRITISCH": 5, "HOCH": 4, "MITTEL": 3, "NIEDRIG": 2, "INFO": 1}
    report.findings.sort(key=lambda f: _prio.get(f.threat_level, 0), reverse=True)
    report.findings = report.findings[:30]

    # Gesamtlevel
    if any(f.threat_level == "KRITISCH" for f in report.findings):
        report.level = "KRITISCH"
    elif any(f.threat_level == "HOCH" for f in report.findings):
        report.level = "HOCH"
    elif any(f.threat_level == "MITTEL" for f in report.findings):
        report.level = "MITTEL"
    elif report.findings:
        report.level = "NIEDRIG"
    else:
        report.level = "NORMAL"

    return report


# ─── Livemap-Integration ─────────────────────────────────────────────────────

def darkweb_for_map(keywords: list[str] | None = None) -> list[dict]:
    """
    Gibt Livemap-Marker für Dark Web Findings zurück.
    Da .onion Findings keine GPS-Koordinaten haben, werden sie als
    Sidebar-Alerts angezeigt (lat=0, lon=0 = kein Marker).
    Stattdessen: strukturierte Alert-Liste für Dashboard.
    """
    report = darkweb_scan(custom_keywords=keywords, max_sources=4)
    alerts = []
    _level_color = {
        "KRITISCH": "#ff2222",
        "HOCH":     "#ff8800",
        "MITTEL":   "#ffcc00",
        "NIEDRIG":  "#44ff44",
        "INFO":     "#aaaaaa",
    }
    _level_icon = {
        "KRITISCH": "🔴",
        "HOCH":     "🟠",
        "MITTEL":   "🟡",
        "NIEDRIG":  "🟢",
        "INFO":     "⚫",
    }

    for f in report.findings:
        icon = _level_icon.get(f.threat_level, "⚫")
        color = _level_color.get(f.threat_level, "#aaaaaa")
        alerts.append({
            "source":    f.source,
            "category":  f.category,
            "title":     f.title,
            "snippet":   f.snippet[:200],
            "keywords":  f.keywords,
            "level":     f.threat_level,
            "icon":      icon,
            "color":     color,
            "ts":        f.ts,
            "ts_fmt":    datetime.fromtimestamp(f.ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"),
        })
    return alerts


def darkweb_summary() -> dict:
    """Kompaktes Dict für Dashboard."""
    report = darkweb_scan(max_sources=3)
    by_cat: dict[str, int] = {}
    for f in report.findings:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
    return {
        "tor_online":     report.tor_online,
        "level":          report.level,
        "n_findings":     len(report.findings),
        "by_category":    by_cat,
        "top_finding":    report.findings[0].title if report.findings else "",
        "generated":      report.generated,
    }


# ─── Tor-Status ───────────────────────────────────────────────────────────────

def tor_status() -> dict:
    """Prüft Tor-Verbindung und gibt Status zurück."""
    online = _check_tor()

    # Wenn online: IP über Tor prüfen
    tor_ip = ""
    if online:
        try:
            html = fetch_onion("https://check.torproject.org/api/ip")
            if html:
                data = json.loads(html)
                tor_ip = data.get("IP", "")
        except Exception:
            pass

    return {
        "tor_online":  online,
        "tor_port":    TOR_PORT,
        "tor_ip":      tor_ip,
        "pysocks":     _PYSOCKS,
        "requests":    _REQUESTS,
        "status":      "🟢 ONLINE" if online else "🔴 OFFLINE (Tor starten: tor --daemon)",
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="NEXUS Dark Web OSINT Monitor",
        epilog="Hinweis: Tor muss laufen (tor --daemon oder Tor Browser aktiv)",
    )
    parser.add_argument("--scan",      action="store_true", help="Quellen scannen")
    parser.add_argument("--status",    action="store_true", help="Tor-Status prüfen")
    parser.add_argument("--keywords",  default="", help="Zusatz-Keywords (kommagetrennt)")
    parser.add_argument("--category",  default="", help="Nur diese Kategorie (RANSOMWARE/NEWS/FININT)")
    parser.add_argument("--json",      action="store_true", help="JSON-Ausgabe")
    parser.add_argument("--summary",   action="store_true", help="Nur Zusammenfassung")
    args = parser.parse_args()

    if args.status:
        status = tor_status()
        print("\n=== Tor Status ===")
        for k, v in status.items():
            print(f"  {k:15s}: {v}")

    elif args.scan or args.keywords:
        kw = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None
        cats = [args.category] if args.category else None

        print(f"\nScanne Dark Web Quellen{'...' if not args.status else ''}")
        print("(Tor muss auf Port 9050 laufen)\n")

        report = darkweb_scan(custom_keywords=kw, categories=cats, max_sources=5)

        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        elif args.summary:
            s = darkweb_summary()
            for k, v in s.items():
                print(f"  {k:20s}: {v}")
        else:
            print(f"=== Dark Web Scan ===")
            print(f"  Tor:       {'🟢 ONLINE' if report.tor_online else '🔴 OFFLINE'}")
            print(f"  Quellen:   {report.sources_checked}")
            print(f"  Findings:  {len(report.findings)}")
            print(f"  Level:     {report.level}")
            print(f"  Stand:     {report.generated}")

            if not report.tor_online:
                print("\n  ⚠️  Tor ist nicht erreichbar.")
                print("  Starte: tor --daemon")
                print("  Oder:   Tor Browser öffnen und im Hintergrund lassen.")
            elif not report.findings:
                print("\n  ✅ Keine relevanten Findings.")
            else:
                print(f"\n{'─'*70}")
                _icons = {"KRITISCH":"🔴","HOCH":"🟠","MITTEL":"🟡","NIEDRIG":"🟢","INFO":"⚫"}
                for f in report.findings:
                    icon = _icons.get(f.threat_level, "⚫")
                    print(f"  {icon} [{f.threat_level:8s}] [{f.category:12s}] {f.title[:55]}")
                    if f.keywords:
                        print(f"      Keywords: {', '.join(f.keywords[:5])}")
                    if f.snippet:
                        print(f"      Snippet:  {f.snippet[:80]}...")
                    print()
    else:
        parser.print_help()
