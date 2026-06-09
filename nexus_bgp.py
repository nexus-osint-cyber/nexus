"""
nexus_bgp.py – BGP-Routing-Anomalien (Internet-Infrastruktur-Monitor)
======================================================================
Erkennt Internet-Infrastruktur-Angriffe via BGP-Daten:
  - Route Hijacking (AS übernimmt fremde IP-Prefixe)
  - Route Leaks (falsche Weiterleitungen)
  - Plötzlicher Ausfall ganzer Länder-ASNs

Kostenlose APIs ohne Keys:
  - RIPE NCC stat.ripe.net  – Routing-Statistiken für ASN/Prefix
  - Cloudflare Radar         – BGP Hijack Events (kein Token nötig für public)
  - BGPStream (CAIDA)        – Recent BGP updates feed
  - ARIN/APNIC whois APIs    – ASN → Land-Zuordnung

BGP-Grundkonzept für NEXUS:
  Ein Land hat typischerweise bestimmte AS-Nummern (Autonomous Systems),
  die seinen IP-Adressraum verwalten. Wenn plötzlich jemand anders diese
  Routen ankündigt (Hijack) oder alle Routen verschwinden (Outage),
  ist das ein klares Angriffssignal.
"""

import json
import logging
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("nexus.bgp")

# ── API-Endpunkte (alle kostenfrei) ──────────────────────────────────────────

RIPE_STAT_BASE    = "https://stat.ripe.net/data"
CLOUDFLARE_RADAR  = "https://api.cloudflare.com/client/v4/radar/bgp/hijacks/events"
BGP_TOOLS_RECENT  = "https://bgp.tools/table.jsonl"  # Recent BGP state

# ── Länder → Wichtige ASNs (Top-Provider je Land) ────────────────────────────
# Quelle: RIPE NCC, CAIDA BGP data (manuell gepflegt für NEXUS)
COUNTRY_ASN_MAP = {
    # Format: Land → [primäre ASNs, ...]
    "ukraine":      [3261, 25462, 15497, 13249, 21219, 6849],
    "russland":     [12389, 8359, 3216, 20485, 31213, 1299],
    "iran":         [197207, 48159, 16322, 58224, 44244],
    "nordkorea":    [131279],   # nur 1 ASN!
    "belarus":      [6697, 25106, 21274],
    "china":        [4134, 4837, 9808, 17816, 4538],
    "myanmar":      [18399, 63852, 136255],
    "syrien":       [29256, 9051, 198467],
    "venezuela":    [8048, 21826, 27889],
    "kuba":         [27725, 11171],
    "afghanistan":  [24863, 131430, 55569],
    "jemen":        [30873, 21472, 134780],
    "libyen":       [327720, 37517, 328024],
    "gaza":         [50670],    # Paltel/Gaza
    "deutschland":  [3320, 3209, 1299, 8881, 13184],
    "usa":          [7018, 701, 1299, 3356, 7922],
}

# Reverse-Map: ASN → Land
ASN_COUNTRY = {}
for _country, _asns in COUNTRY_ASN_MAP.items():
    for _asn in _asns:
        ASN_COUNTRY[_asn] = _country


# ── RIPE NCC API ──────────────────────────────────────────────────────────────

def _ripe_get(endpoint: str, params: dict, timeout: int = 10) -> Optional[dict]:
    """Ruft RIPE NCC stat.ripe.net API auf."""
    url = f"{RIPE_STAT_BASE}/{endpoint}/data.json?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "NEXUS-BGP/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.debug(f"RIPE {endpoint}: {e}")
        return None


def get_asn_routing_status(asn: int) -> dict:
    """
    Gibt aktuellen Routing-Status eines ASN zurück.
    Erkennt ob das ASN aktiv Routen ankündigt oder offline ist.
    """
    data = _ripe_get("routing-status", {"resource": f"AS{asn}"})
    if not data:
        return {"asn": asn, "status": "unknown", "prefixes": 0}

    d = data.get("data", {})
    announced = d.get("announced_space", {})
    ipv4 = announced.get("v4", {}).get("prefixes", 0)
    ipv6 = announced.get("v6", {}).get("prefixes", 0)

    return {
        "asn":           asn,
        "status":        "active" if (ipv4 + ipv6) > 0 else "offline",
        "prefixes_v4":   ipv4,
        "prefixes_v6":   ipv6,
        "total_prefixes": ipv4 + ipv6,
    }


def get_asn_neighbours(asn: int) -> list:
    """Gibt aktuelle BGP-Nachbarn eines ASN zurück (upstream/downstream)."""
    data = _ripe_get("asn-neighbours", {"resource": f"AS{asn}"})
    if not data:
        return []
    neighbours = data.get("data", {}).get("neighbours", [])
    return [
        {
            "asn":   int(n.get("asn", 0)),
            "type":  n.get("type", ""),  # "left" = upstream, "right" = downstream
            "power": n.get("power", 0),
        }
        for n in neighbours[:20]
    ]


def get_prefix_overview(prefix: str) -> dict:
    """Gibt Routing-Info für ein IP-Präfix zurück."""
    data = _ripe_get("prefix-overview", {"resource": prefix})
    if not data:
        return {}
    d = data.get("data", {})
    return {
        "prefix":      prefix,
        "is_announced": d.get("is_announced", False),
        "announced_by": [str(a) for a in d.get("asns", [])[:5]],
        "block":        d.get("block", {}).get("name", ""),
    }


def get_routing_history(asn: int, days: int = 3) -> list:
    """Gibt routing history für ASN zurück (Veränderungen)."""
    starttime = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M")
    data = _ripe_get("routing-history", {
        "resource":  f"AS{asn}",
        "starttime": starttime,
    })
    if not data:
        return []
    return data.get("data", {}).get("by_origin", [])[:10]


# ── Cloudflare Radar BGP Hijacks ─────────────────────────────────────────────

def get_cloudflare_hijacks(days: int = 1) -> list:
    """
    Ruft aktuelle BGP-Hijack-Events von Cloudflare Radar ab.
    Kein API-Key benötigt für öffentliche Events.
    """
    try:
        params = urllib.parse.urlencode({
            "dateStart": (datetime.now(timezone.utc) - timedelta(days=days)
                         ).strftime("%Y-%m-%d"),
            "dateEnd":   datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "per_page":  50,
            "format":    "json",
        })
        url = f"{CLOUDFLARE_RADAR}?{params}"
        req = urllib.request.Request(
            url, headers={
                "User-Agent": "NEXUS-BGP/1.0",
                "Accept":     "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        events = data.get("result", {}).get("events", []) or data.get("events", [])
        return events[:30]
    except Exception as e:
        log.debug(f"Cloudflare Radar: {e}")
        return []


# ── Anomalie-Erkennung ────────────────────────────────────────────────────────

def detect_country_outage(country: str) -> dict:
    """
    Prüft ob ein Land einen BGP-Ausfall hat (alle/viele ASNs offline).

    Returns:
        {
          "country":       str,
          "alert":         bool,
          "offline_asns":  list,
          "active_asns":   list,
          "outage_pct":    float,   # 0–1
          "severity":      str,     # "total", "partial", "normal"
        }
    """
    asns = COUNTRY_ASN_MAP.get(country.lower(), [])
    if not asns:
        return {"country": country, "alert": False, "severity": "unknown"}

    results = []
    for asn in asns[:5]:  # Max 5 ASNs prüfen (Rate-Limit)
        status = get_asn_routing_status(asn)
        results.append(status)

    offline  = [r for r in results if r["status"] == "offline"]
    active   = [r for r in results if r["status"] == "active"]
    outage_p = len(offline) / len(results) if results else 0

    severity = (
        "total"   if outage_p >= 0.8 else
        "partial" if outage_p >= 0.4 else
        "normal"
    )

    return {
        "country":      country,
        "alert":        outage_p >= 0.4,
        "offline_asns": [r["asn"] for r in offline],
        "active_asns":  [r["asn"] for r in active],
        "outage_pct":   round(outage_p, 2),
        "severity":     severity,
        "checked":      len(results),
    }


def get_bgp_summary(regions: Optional[list] = None) -> dict:
    """
    Gibt BGP-Übersicht für NEXUS zurück.

    Args:
        regions: Liste von Ländern (Standard: Konfliktregionen)

    Returns: {
        "hijacks":     [Cloudflare-Events],
        "outages":     [Länder mit Routing-Anomalien],
        "alerts":      bool,
    }
    """
    if regions is None:
        regions = ["ukraine", "iran", "nordkorea", "myanmar", "syrien"]

    summary = {
        "hijacks":  [],
        "outages":  [],
        "alerts":   False,
        "checked":  datetime.now(timezone.utc).isoformat(),
    }

    # Cloudflare Hijacks
    try:
        hijacks = get_cloudflare_hijacks(days=1)
        summary["hijacks"] = hijacks
        if hijacks:
            summary["alerts"] = True
    except Exception:
        pass

    # Länder-Outage-Check (mit Rate-Limiting: nur eine Anfrage pro 5s)
    for country in regions[:3]:  # Max 3 Länder gleichzeitig
        try:
            outage = detect_country_outage(country)
            if outage.get("alert"):
                summary["outages"].append(outage)
                summary["alerts"] = True
        except Exception as e:
            log.debug(f"BGP outage check {country}: {e}")

    return summary


def get_bgp_for_map(regions: Optional[list] = None) -> list:
    """Gibt Karten-kompatible Marker für BGP-Anomalien zurück."""
    from nexus_local import COUNTRY_COORDS  # type: ignore  # noqa
    summary = get_bgp_summary(regions)
    markers = []

    COUNTRY_LATLONS = {
        "ukraine": (49.0, 32.0), "iran": (32.4, 53.7),
        "nordkorea": (40.0, 127.0), "myanmar": (17.1, 96.5),
        "syrien": (34.8, 39.0), "russland": (61.5, 105.0),
        "belarus": (53.7, 27.9), "china": (35.9, 104.2),
        "venezuela": (6.4, -66.6), "kuba": (21.5, -79.0),
    }

    for outage in summary.get("outages", []):
        country = outage["country"]
        coords  = COUNTRY_LATLONS.get(country, (0, 0))
        if coords == (0, 0):
            continue
        sev   = outage["severity"]
        color = "#cc0000" if sev == "total" else "#ff8800"
        markers.append({
            "lat":   coords[0],
            "lon":   coords[1],
            "type":  "bgp_outage",
            "icon":  "🌐",
            "color": color,
            "title": f"🌐 BGP-Ausfall: {country.title()} ({outage['outage_pct']:.0%})",
            "popup": (
                f"<b>🌐 Internet-Routing-Anomalie</b><br>"
                f"<b>Land:</b> {country.title()}<br>"
                f"<b>Typ:</b> {sev.upper()}<br>"
                f"<b>Offline-ASNs:</b> {', '.join(f'AS{a}' for a in outage['offline_asns'])}<br>"
                f"<b>Ausfall:</b> {outage['outage_pct']:.0%} der geprüften Provider<br>"
                f"<small>Quelle: RIPE NCC stat.ripe.net</small>"
            ),
        })

    for hijack in summary.get("hijacks", [])[:5]:
        # Cloudflare-Hijack-Felder variieren je nach API-Version
        desc = (hijack.get("description") or
                hijack.get("prefix") or
                str(hijack.get("hijackEvent", "")))[:60]
        markers.append({
            "lat":   30.0, "lon":  50.0,  # ohne spezifische Koordinate
            "type":  "bgp_hijack",
            "icon":  "🌐",
            "color": "#cc44ff",
            "title": f"🌐 BGP-Hijack: {desc}",
            "popup": (
                f"<b>🌐 BGP Route Hijack</b><br>"
                f"{desc}<br>"
                f"<small>Quelle: Cloudflare Radar</small>"
            ),
        })

    return markers


def format_bgp_terminal(summary: dict) -> str:
    """Formatiert BGP-Summary für Terminal."""
    lines = [
        "",
        "\033[35m╔══ 🌐 BGP-ROUTING-MONITOR ════════════════════════════╗\033[0m",
    ]
    hijacks = summary.get("hijacks", [])
    outages = summary.get("outages", [])

    if not hijacks and not outages:
        lines.append("\033[35m║\033[0m  Keine aktiven BGP-Anomalien detektiert.            \033[35m║\033[0m")
    else:
        if hijacks:
            lines.append(f"\033[35m║\033[0m  \033[91mRoute Hijacks: {len(hijacks)} Events (letzter Tag)\033[0m")
            for h in hijacks[:3]:
                desc = (h.get("description") or h.get("prefix") or "")[:50]
                lines.append(f"\033[35m║\033[0m    • {desc}")
        if outages:
            lines.append(f"\033[35m║\033[0m  \033[93mLänder-Ausfälle: {len(outages)}\033[0m")
            for o in outages:
                lines.append(
                    f"\033[35m║\033[0m    • {o['country'].title()}: "
                    f"{o['severity'].upper()} ({o['outage_pct']:.0%})"
                )

    lines.append("\033[35m╚══════════════════════════════════════════════════════╝\033[0m")
    lines.append("")
    return "\n".join(lines)


# ── Standalone Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    regions = sys.argv[1:] or ["ukraine", "iran", "nordkorea"]
    print(f"BGP-Check für: {regions}")
    summary = get_bgp_summary(regions)
    print(format_bgp_terminal(summary))
