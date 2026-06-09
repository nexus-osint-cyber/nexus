"""
nexus_displacement.py – UNHCR Vertreibungs- und Flucht-Tracking
===============================================================
Aggregiert Bevölkerungsbewegungen und Vertreibungsdaten aus:
  - UNHCR Population API     – Flüchtlinge, Binnenvertriebene (IDPs)
  - IOM DTM                  – Displacement Tracking Matrix
  - ACLED Displacement Events– Konflikt-bedingte Vertreibung
  - ReliefWeb API             – Humanitäre Lageberichte

Alle APIs kostenlos, kein API-Key erforderlich.

Interpretation für NEXUS:
  - Plötzlicher IDP-Anstieg → aktiver Konflikt / Eskalation
  - Rückkehrstrom → Konflikt-Deeskalation
  - Grenzüberschreitende Flucht → internationale Krise
  - Null-Bewegung trotz Konflikt → Bevölkerung eingeschlossen
"""

import json
import re
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional
import xml.etree.ElementTree as ET

log = logging.getLogger("nexus.displacement")

# ── API-Endpunkte ─────────────────────────────────────────────────────────────

UNHCR_API   = "https://api.unhcr.org/population/v1"
IOM_DTM_API = "https://dtm.iom.int/api/v1"
RELIEFWEB   = "https://api.reliefweb.int/v1"

# ── Länder-Codes (ISO3 → Deutsch) ────────────────────────────────────────────

ISO3_DE = {
    "UKR": "Ukraine",          "SYR": "Syrien",
    "AFG": "Afghanistan",      "VEN": "Venezuela",
    "MMR": "Myanmar",          "SOM": "Somalia",
    "SDN": "Sudan",            "ETH": "Äthiopien",
    "SSD": "Südsudan",         "COD": "Kongo (DRK)",
    "CAF": "Zentralafrika",    "MOZ": "Mosambik",
    "NGA": "Nigeria",          "MLI": "Mali",
    "BFA": "Burkina Faso",     "HTI": "Haiti",
    "IRQ": "Irak",             "LBY": "Libyen",
    "YEM": "Jemen",            "COL": "Kolumbien",
    "PSE": "Palästina",        "IRN": "Iran",
    "PAK": "Pakistan",         "BGD": "Bangladesch",
    "CMR": "Kamerun",          "NER": "Niger",
}

DE_ISO3 = {v.lower(): k for k, v in ISO3_DE.items()}
# Auch Englisch-Varianten
DE_ISO3.update({
    "ukraine": "UKR", "syria": "SYR", "afghanistan": "AFG",
    "venezuela": "VEN", "myanmar": "MMR", "somalia": "SOM",
    "sudan": "SDN", "ethiopia": "ETH", "south sudan": "SSD",
    "drc": "COD", "congo": "COD", "nigeria": "NGA",
    "mali": "MLI", "haiti": "HTI", "iraq": "IRQ",
    "libya": "LBY", "yemen": "YEM", "colombia": "COL",
    "palestine": "PSE", "gaza": "PSE",
})


# ── UNHCR Population API ─────────────────────────────────────────────────────

def get_unhcr_stats(country_iso3: str, year: Optional[int] = None) -> dict:
    """
    Holt UNHCR-Statistiken für ein Land.
    Gibt Flüchtlings-, IDP- und Rückkehrzahlen zurück.
    """
    if year is None:
        year = datetime.now(timezone.utc).year - 1  # Letzte verfügbare Jahresdaten

    result = {
        "country": ISO3_DE.get(country_iso3, country_iso3),
        "iso3":    country_iso3,
        "year":    year,
        "refugees_from":   0,   # Aus diesem Land geflüchtet
        "refugees_in":     0,   # In diesem Land Schutzsuchende
        "idps":            0,   # Binnenvertriebene
        "returnees":       0,   # Rückkehrer
        "stateless":       0,   # Staatenlose
        "source":          "UNHCR",
    }

    try:
        # Flüchtlinge AUS dem Land (origin)
        url = (f"{UNHCR_API}/population/?limit=1&dataset=population"
               f"&displayType=totals&years={year}&origins={country_iso3}")
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        total = data.get("total", 0)
        items = data.get("items", [])
        if items:
            result["refugees_from"] = sum(
                int(item.get("refugees", 0) or 0) +
                int(item.get("asylum_seekers", 0) or 0)
                for item in items
            )

        # IDPs (Binnenvertriebene) – separater Endpoint
        url2 = (f"{UNHCR_API}/population/?limit=1&dataset=idmc"
                f"&years={year}&coo={country_iso3}")
        req2 = urllib.request.Request(url2, headers={"User-Agent": "NEXUS/1.0"})
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            data2 = json.loads(resp2.read())
        items2 = data2.get("items", [])
        if items2:
            result["idps"] = sum(
                int(item.get("idps", 0) or 0) for item in items2
            )

    except Exception as e:
        log.debug(f"UNHCR {country_iso3}: {e}")

    return result


def get_unhcr_top_crises(limit: int = 10) -> list:
    """
    Gibt die Länder mit den meisten Vertriebenen zurück.
    Basiert auf UNHCR-Jahresstatistiken der letzten verfügbaren Daten.
    """
    year   = datetime.now(timezone.utc).year - 1
    crises = []

    try:
        url = (f"{UNHCR_API}/population/?limit={limit}&dataset=population"
               f"&displayType=totals&years={year}&sortBy=refugees&sortOrder=desc")
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())

        for item in data.get("items", [])[:limit]:
            iso3    = item.get("coo_iso", item.get("coa_iso", ""))
            name    = ISO3_DE.get(iso3, item.get("coo_name", iso3))
            total   = (int(item.get("refugees", 0) or 0) +
                       int(item.get("asylum_seekers", 0) or 0))
            crises.append({
                "country": name,
                "iso3":    iso3,
                "total":   total,
                "year":    year,
                "source":  "UNHCR",
            })
    except Exception as e:
        log.debug(f"UNHCR top crises: {e}")

    return crises


# ── IOM DTM API ──────────────────────────────────────────────────────────────

def get_iom_operations(country: Optional[str] = None) -> list:
    """
    Ruft IOM DTM Displacement-Operationen ab.
    DTM = Displacement Tracking Matrix (real-time IDP monitoring).
    """
    try:
        params = {"limit": 20, "sort": "date:desc"}
        if country:
            iso3 = DE_ISO3.get(country.lower())
            if iso3:
                params["countries"] = iso3

        url = f"{IOM_DTM_API}/reports?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"User-Agent": "NEXUS/1.0",
                          "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        operations = []
        for item in (data if isinstance(data, list) else data.get("data", []))[:20]:
            country_name = (
                item.get("country") or
                ISO3_DE.get(item.get("iso3", ""), "") or ""
            )
            operations.append({
                "title":      item.get("title") or item.get("name", "")[:80],
                "country":    country_name,
                "iso3":       item.get("iso3", ""),
                "date":       item.get("date") or item.get("reportingDate", ""),
                "idp_total":  int(item.get("idpFigures", 0) or 0),
                "returnees":  int(item.get("returnFigures", 0) or 0),
                "location":   item.get("adminName1") or item.get("location", ""),
                "lat":        item.get("latitude"),
                "lon":        item.get("longitude"),
                "source":     "IOM DTM",
                "url":        item.get("url", ""),
            })
        return operations

    except Exception as e:
        log.debug(f"IOM DTM: {e}")
        return []


# ── ReliefWeb ────────────────────────────────────────────────────────────────

def get_reliefweb_reports(country: Optional[str] = None,
                          query: str = "displacement IDP refugees",
                          limit: int = 10) -> list:
    """
    Ruft humanitäre Lageberichte von ReliefWeb ab.
    ReliefWeb ist das UN OCHA-Portal für Humanitarian Intelligence.
    """
    try:
        payload = {
            "query": {"value": query, "operator": "AND"},
            "filter": {"operator": "AND", "conditions": []},
            "fields": {"include": ["title", "date", "country", "url",
                                   "body-html", "source"]},
            "sort":   ["date:desc"],
            "limit":  limit,
        }

        if country:
            iso3 = DE_ISO3.get(country.lower())
            if iso3:
                payload["filter"]["conditions"].append({
                    "field": "country.iso3",
                    "value": iso3,
                })

        body = json.dumps(payload).encode()
        url  = f"{RELIEFWEB}/reports?appname=nexus-osint"
        req  = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "NEXUS/1.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())

        reports = []
        for item in data.get("data", [])[:limit]:
            fields = item.get("fields", {})
            country_list = [c.get("name", "") for c in (fields.get("country") or [])]
            source_list  = [s.get("name", "") for s in (fields.get("source") or [])]
            # HTML-Tags entfernen für Zusammenfassung
            body_text = re.sub(r'<[^>]+>', '', fields.get("body-html") or "")[:200]

            reports.append({
                "title":    fields.get("title", "")[:80],
                "date":     fields.get("date", {}).get("original", "")[:10],
                "country":  ", ".join(country_list[:2]),
                "source":   ", ".join(source_list[:1]),
                "summary":  body_text.strip(),
                "url":      fields.get("url", ""),
            })
        return reports

    except Exception as e:
        log.debug(f"ReliefWeb: {e}")
        return []


# ── Koordinaten für Karte ─────────────────────────────────────────────────────

COUNTRY_COORDS_DISP = {
    "UKR": (49.0, 32.0), "SYR": (34.8, 39.0), "AFG": (33.9, 67.7),
    "VEN": (6.4, -66.6),  "MMR": (17.1, 96.5), "SOM": (5.2, 46.2),
    "SDN": (12.9, 30.2),  "ETH": (9.2, 40.5),  "SSD": (6.9, 31.6),
    "COD": (-4.3, 15.3),  "CAF": (6.6, 20.9),  "MOZ": (-18.7, 35.5),
    "NGA": (9.1, 8.7),    "MLI": (17.6, -2.0), "BFA": (12.4, -1.6),
    "HTI": (19.0, -72.3), "IRQ": (33.2, 43.7), "LBY": (26.3, 17.2),
    "YEM": (15.6, 48.5),  "COL": (4.6, -74.3), "PSE": (31.9, 35.2),
}


# ── Haupt-Funktion ─────────────────────────────────────────────────────────────

def get_displacement_data(country: Optional[str] = None) -> dict:
    """
    Vollständige Vertreibungsdaten für eine Region oder Top-Krisen.

    Returns: {
        "unhcr_stats": dict (wenn country angegeben),
        "top_crises":  list,
        "iom_ops":     list,
        "reports":     list,
    }
    """
    result = {
        "unhcr_stats": None,
        "top_crises":  [],
        "iom_ops":     [],
        "reports":     [],
    }

    if country:
        iso3 = DE_ISO3.get(country.lower())
        if iso3:
            result["unhcr_stats"] = get_unhcr_stats(iso3)
        result["iom_ops"] = get_iom_operations(country)
        result["reports"] = get_reliefweb_reports(country)
    else:
        result["top_crises"] = get_unhcr_top_crises(limit=8)
        result["iom_ops"]    = get_iom_operations()
        result["reports"]    = get_reliefweb_reports(
            query="displacement conflict IDPs refugees crisis"
        )

    return result


def get_displacement_for_map(country: Optional[str] = None) -> list:
    """Gibt Karten-kompatible Marker für Vertreibungs-Daten zurück."""
    data    = get_displacement_data(country)
    markers = []

    # Top-Krisen als Kreise
    for crisis in data.get("top_crises", [])[:10]:
        iso3   = crisis.get("iso3", "")
        coords = COUNTRY_COORDS_DISP.get(iso3)
        if not coords:
            continue
        total  = crisis.get("total", 0)
        radius = min(max(total / 100_000, 50), 500) * 1000  # km → m
        markers.append({
            "lat":    coords[0],
            "lon":    coords[1],
            "type":   "displacement",
            "icon":   "🏕",
            "color":  "#ff8c00",
            "title":  f"🏕 {crisis['country']}: {total:,} Vertriebene",
            "popup":  (
                f"<b>🏕 Vertreibungskrise: {crisis['country']}</b><br>"
                f"<b>Flüchtlinge/Asylsuchende:</b> {total:,}<br>"
                f"<b>Jahr:</b> {crisis.get('year','?')}<br>"
                f"<small>Quelle: UNHCR</small>"
            ),
        })

    # IOM-Operationen
    for op in data.get("iom_ops", [])[:5]:
        lat = op.get("lat")
        lon = op.get("lon")
        if not lat or not lon:
            continue
        idps = op.get("idp_total", 0)
        markers.append({
            "lat":   float(lat),
            "lon":   float(lon),
            "type":  "displacement",
            "icon":  "🏕",
            "color": "#ffaa00",
            "title": f"🏕 IOM: {op.get('title','?')[:40]}",
            "popup": (
                f"<b>🏕 IOM DTM Operation</b><br>"
                f"{op.get('title','')[:80]}<br>"
                f"<b>IDPs:</b> {idps:,}<br>"
                f"<b>Rückkehrer:</b> {op.get('returnees',0):,}<br>"
                f"<b>Ort:</b> {op.get('location','?')}<br>"
                f"<b>Datum:</b> {op.get('date','?')[:10]}"
                + (f"<br><a href='{op['url']}' target='_blank'>→ IOM DTM</a>"
                   if op.get("url") else "")
            ),
        })

    return markers


def format_displacement_terminal(data: dict) -> str:
    """Formatiert Vertreibungsdaten für Terminal."""
    lines = [
        "",
        "\033[33m╔══ 🏕 VERTREIBUNGS-TRACKING (UNHCR/IOM) ══════════════╗\033[0m",
    ]

    stats = data.get("unhcr_stats")
    if stats:
        lines.append(
            f"\033[33m║\033[0m  Land: {stats['country']:<20} Jahr: {stats['year']}"
        )
        if stats["refugees_from"]:
            lines.append(
                f"\033[33m║\033[0m  Flüchtlinge (aus): {stats['refugees_from']:>10,}"
            )
        if stats["idps"]:
            lines.append(
                f"\033[33m║\033[0m  Binnenvertriebene: {stats['idps']:>10,}"
            )

    crises = data.get("top_crises", [])
    if crises:
        lines.append(f"\033[33m║\033[0m")
        lines.append(f"\033[33m║\033[0m  Top-Vertreibungskrisen ({crises[0].get('year','?')}):")
        for c in crises[:6]:
            bar = "█" * min(int(c.get("total", 0) / 500_000), 20)
            lines.append(
                f"\033[33m║\033[0m    {c['country']:<18} "
                f"{c.get('total',0):>8,}  \033[33m{bar}\033[0m"
            )

    ops = data.get("iom_ops", [])
    if ops:
        lines.append(f"\033[33m║\033[0m")
        lines.append(f"\033[33m║\033[0m  IOM DTM Aktuell ({len(ops)} Operationen):")
        for op in ops[:3]:
            lines.append(
                f"\033[33m║\033[0m    {op.get('date','?')[:10]} "
                f"{op.get('country','?'):<12} "
                f"IDPs: {op.get('idp_total',0):>7,}"
            )

    lines.append("\033[33m╚══════════════════════════════════════════════════════╝\033[0m")
    lines.append("")
    return "\n".join(lines)


# ── Standalone Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    country = sys.argv[1] if len(sys.argv) > 1 else None
    data    = get_displacement_data(country)
    print(format_displacement_terminal(data))
