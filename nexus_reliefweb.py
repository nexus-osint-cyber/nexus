"""
NEXUS – ReliefWeb / OCHA HDX Humanitäre Früherkennung  (T205)
=============================================================
Humanitäre Krisenindikatoren als frühzeitige Eskalationswarnung.
Vertreibungszahlen steigen typisch 48–72h VOR den ersten Nachrichten.

Datenquellen:
  1. ReliefWeb API   https://api.reliefweb.int/v1/
     - Berichte, Lageupdates, Situation Reports
     - Kostenlos, kein Key
  2. OCHA HDX        https://data.humdata.org/api/
     - Displacement-Daten (IDP, Flüchtlinge)
     - Population Tracking
     - Kostenlos, kein Key

Eskalations-Signale:
  • IDP-Anstieg >20% in 48h → Offensive möglich
  • Neue Lager eröffnet → Bevölkerungsflucht
  • Hilfsgüter-Blockade (Nahrung/Medizin) → Belagerung
  • Humanitärer Korridor geschlossen → Zuspitzung

Abhängigkeiten: pip install requests
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

RELIEFWEB_API    = "https://api.reliefweb.int/v1"
HDX_API          = "https://data.humdata.org/api/3"
REQUEST_TIMEOUT  = 20
_CACHE_DIR       = Path(__file__).parent / "nexus_reliefweb_cache"
_CACHE_TTL_H     = 4    # Humanitäre Daten: alle 4h aktualisieren

# ReliefWeb Länder-ISO3
COUNTRY_ISO3: dict[str, str] = {
    "Iran":         "IRN",
    "Israel":       "ISR",
    "Gaza":         "PSE",
    "Palestine":    "PSE",
    "Lebanon":      "LBN",
    "Syria":        "SYR",
    "Iraq":         "IRQ",
    "Yemen":        "YEM",
    "Ukraine":      "UKR",
    "Russia":       "RUS",
    "Sudan":        "SDN",
    "Afghanistan":  "AFG",
    "Somalia":      "SOM",
    "Libya":        "LBY",
}

# Schlüsselwörter für Eskalations-Relevanz (gewichtet)
ESCALATION_KW: dict[str, float] = {
    "displacement":  2.0, "displaced":   2.0, "idp":          2.5,
    "evacuation":    2.5, "evacuate":    2.5, "flee":         2.0,
    "siege":         3.0, "blockade":    3.0, "encirclement": 3.0,
    "offensive":     2.5, "attack":      2.0, "airstrike":    3.0,
    "ceasefire":     1.0, "negotiation": 0.5,
    "humanitarian":  1.0, "corridor":    1.5,
    "famine":        2.0, "starvation":  2.5, "malnutrition": 1.5,
    "casualty":      2.0, "killed":      2.5, "wounded":      1.5,
    "refugee":       1.5, "asylum":      1.0,
    "mass grave":    3.5, "massacre":    3.5, "genocide":     4.0,
    "chemical":      4.0, "nuclear":     4.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / (key[:80].replace("/","_") + ".json")

def _cached(key: str) -> Optional[list]:
    p = _cache_path(key)
    if not p.exists(): return None
    try:
        d = json.loads(p.read_text())
        if time.time() - d.get("ts",0) < _CACHE_TTL_H * 3600:
            return d.get("data")
    except Exception: pass
    return None

def _store(key: str, data: list) -> list:
    try: _cache_path(key).write_text(json.dumps({"ts":time.time(),"data":data}))
    except Exception: pass
    return data


# ─────────────────────────────────────────────────────────────────────────────
# ReliefWeb API
# ─────────────────────────────────────────────────────────────────────────────

def _reliefweb_reports(country: str, limit: int = 15,
                        days_back: int = 7) -> list[dict]:
    """Holt aktuelle Situation Reports + Updates von ReliefWeb."""
    cache_key = f"rw_{country}_{days_back}"
    cached    = _cached(cache_key)
    if cached is not None:
        return cached

    iso3     = COUNTRY_ISO3.get(country, country[:3].upper())
    since    = (datetime.now(timezone.utc) - timedelta(days=days_back))
    since_s  = since.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    payload = {
        "appname":  "nexus-osint",
        "query": {
            "value": f"{country} OR {iso3}",
            "fields": ["title", "body-html"],
            "operator": "OR",
        },
        "filter": {
            "operator": "AND",
            "conditions": [
                {"field": "date.created", "value": {"from": since_s}},
                {"field": "country.iso3", "value": iso3},
            ],
        },
        "fields": {
            "include": ["title", "date", "source", "body",
                        "url_alias", "disaster_type", "vulnerable_groups"],
        },
        "sort": ["date:desc"],
        "limit": limit,
    }

    try:
        r = requests.post(
            f"{RELIEFWEB_API}/reports",
            json=payload,
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data  = r.json()
        items = []
        for report in data.get("data", []):
            fields = report.get("fields", {})
            title  = fields.get("title", "")
            body   = re.sub(r"<[^>]+>", " ", fields.get("body", ""))[:400]
            date   = (fields.get("date") or {}).get("created", "")[:10]
            source = ((fields.get("source") or [{}])[0].get("name", "")
                      if isinstance(fields.get("source"), list) else "")
            # Eskalations-Score
            text_lower = (title + " " + body).lower()
            esc_score  = sum(w for kw, w in ESCALATION_KW.items()
                             if kw in text_lower)
            items.append({
                "title":      title[:120],
                "body":       body.strip()[:300],
                "date":       date,
                "source":     source,
                "url":        "https://reliefweb.int" + fields.get("url_alias", ""),
                "disaster_types": [d.get("name","") for d in
                                   fields.get("disaster_type") or []],
                "esc_score":  round(esc_score, 1),
            })
        items.sort(key=lambda x: x["esc_score"], reverse=True)
        return _store(cache_key, items)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# HDX Displacement-Daten
# ─────────────────────────────────────────────────────────────────────────────

def _hdx_displacement(country: str) -> list[dict]:
    """Holt Displacement-Datensätze von HDX."""
    cache_key = f"hdx_disp_{country.lower()}"
    cached    = _cached(cache_key)
    if cached is not None:
        return cached

    try:
        r = requests.get(
            f"{HDX_API}/action/package_search",
            params={
                "q":    f"{country} displacement idp refugees",
                "rows": 5,
                "sort": "metadata_modified desc",
            },
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("result", {}).get("results", [])
        items   = []
        for pkg in results:
            items.append({
                "title":   pkg.get("title", "")[:100],
                "org":     pkg.get("organization", {}).get("title", ""),
                "date":    pkg.get("metadata_modified", "")[:10],
                "url":     f"https://data.humdata.org/dataset/{pkg.get('name','')}",
                "notes":   (pkg.get("notes") or "")[:200],
            })
        return _store(cache_key, items)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# ACAPS Crises Overview (öffentlich verfügbar)
# ─────────────────────────────────────────────────────────────────────────────

def _acaps_crisis(country: str) -> dict:
    """
    Holt ACAPS Krisen-Overview (öffentliche API).
    ACAPS = Assessment Capacities Project.
    """
    try:
        r = requests.get(
            "https://api.acaps.org/api/v1/country-profiles/",
            params={"name": country, "limit": 1},
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            p = results[0]
            return {
                "country":      p.get("name", country),
                "crisis_level": p.get("crisis_type", ""),
                "population":   p.get("population_total", 0),
                "idp":          p.get("idp", 0),
                "refugees":     p.get("refugees", 0),
            }
    except Exception:
        pass
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def get_humanitarian_situation(region: str, days_back: int = 7) -> dict:
    """
    Vollständige Humanitäre Lageeinschätzung für eine Region.

    Returns
    -------
    dict mit: status, region, reports[], displacement_data[],
              escalation_score, key_indicators, latest_update
    """
    reports      = _reliefweb_reports(region, limit=10, days_back=days_back)
    displacement = _hdx_displacement(region)
    acaps        = _acaps_crisis(region)

    if not reports and not displacement:
        return {"status": "keine_daten", "region": region}

    # Eskalations-Score aus Reports
    esc_scores = [r.get("esc_score", 0) for r in reports]
    avg_esc    = sum(esc_scores) / max(len(esc_scores), 1)

    # Schlüssel-Indikatoren
    key_indicators = []
    all_text = " ".join(r.get("title","") + " " + r.get("body","")
                        for r in reports).lower()
    for indicator, weight in sorted(ESCALATION_KW.items(),
                                     key=lambda x: x[1], reverse=True):
        if indicator in all_text:
            key_indicators.append(indicator)
        if len(key_indicators) >= 8:
            break

    # IDP-Daten aus ACAPS
    idp_count = acaps.get("idp", 0) if acaps else 0

    return {
        "status":            "ok",
        "region":            region,
        "report_count":      len(reports),
        "escalation_score":  round(avg_esc, 1),
        "key_indicators":    key_indicators,
        "idp_estimate":      idp_count,
        "displacement_datasets": len(displacement),
        "latest_reports":    reports[:5],
        "displacement_data": displacement[:3],
        "crisis_level":      acaps.get("crisis_level", "") if acaps else "",
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }


def reliefweb_escalation_signal(region: str) -> dict:
    """
    Für nexus_escalation.py: Schnelles Humanitär-Signal.
    """
    cached = _cached(f"esc_signal_{region}")
    if cached:
        return cached[0] if cached else {}

    reports = _reliefweb_reports(region, limit=5, days_back=3)
    if not reports:
        return {"status": "keine_daten", "score": 0.0, "region": region}

    top = reports[0]
    score = min(15.0, top.get("esc_score", 0) * 1.5)

    # Boost für kritische Schlagwörter
    title = top.get("title", "").lower()
    for critical in ("massacre", "chemical", "nuclear", "siege",
                     "blockade", "mass grave"):
        if critical in title:
            score = min(20.0, score + 5.0)
            break

    result = {
        "status":     "ok",
        "score":      round(score, 1),
        "region":     region,
        "top_report": top.get("title", ""),
        "indicators": [kw for kw in ESCALATION_KW
                       if kw in title][:4],
    }
    _store(f"esc_signal_{region}", [result])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Gaza"
    print(f"NEXUS ReliefWeb Humanitäre Früherkennung — {region}")
    print("─" * 55)

    sit = get_humanitarian_situation(region)
    print(f"Status:    {sit['status']}")
    print(f"Reports:   {sit.get('report_count', 0)}")
    print(f"Esc-Score: {sit.get('escalation_score', 0):.1f}")
    if sit.get("key_indicators"):
        print(f"Indikatoren: {', '.join(sit['key_indicators'])}")
    if sit.get("idp_estimate"):
        print(f"IDP: {sit['idp_estimate']:,}")

    print("\nAktuelle Berichte:")
    for r in sit.get("latest_reports", [])[:3]:
        print(f"  [{r['date']}] {r['title']}")
        print(f"   Score: {r['esc_score']:.1f} | {r['source']}")

    sig = reliefweb_escalation_signal(region)
    print(f"\nEskalations-Signal: {sig['score']:.1f} pts")
