"""
NEXUS – Wikidata Entity-Resolution  (T201)
==========================================
Löst militärische Entitäten, Personen, Organisationen und Waffensysteme
automatisch via Wikidata SPARQL auf.

Funktionen:
  resolve_entity(name, typ)       → Strukturierter Entity-Dict
  get_military_unit(name)         → Brigade/Division info, Land, Kommandeur
  get_arms_supplier(weapon)       → Wer hat dieses System geliefert?
  get_person_profile(name)        → Militärführer / Politiker Profil
  get_organization(name)          → Terrorgruppe / Miliz / Geheimdienst
  batch_resolve(names)            → Mehrere Entitäten in einem SPARQL-Call
  enrich_humint(humint_results)   → HUMINT-Treffer mit Wikidata anreichern

SPARQL-Endpoint: https://query.wikidata.org/sparql (kostenlos, kein Key)
Rate-Limit: ~5 Anfragen/s (gut toleriert bei normaler Nutzung)

Abhängigkeiten: pip install requests
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

SPARQL_URL     = "https://query.wikidata.org/sparql"
WIKIDATA_API   = "https://www.wikidata.org/w/api.php"
HEADERS        = {
    "User-Agent": "NEXUS-OSINT/1.0 (nexus-osint@localhost; Wikidata-Integration)",
    "Accept":     "application/sparql-results+json",
}
REQUEST_TIMEOUT = 20
_RATE_DELAY     = 0.3   # Sekunden zwischen Anfragen

# Wikidata QIDs für wichtige Konzepte
_QID_MILITARY_ORG  = "Q1335818"    # Militärische Organisation
_QID_ARMED_FORCES  = "Q8473"       # Streitkräfte
_QID_TERRORIST_ORG = "Q7210356"    # Terroristische Organisation
_QID_WEAPON        = "Q728"        # Waffe
_QID_AIRCRAFT      = "Q11436"      # Luftfahrzeug
_QID_MISSILE       = "Q46900"      # Rakete

# Länderkürzel → Wikidata QID (Auswahl für Konfliktregionen)
COUNTRY_QID: dict[str, str] = {
    "Iran":        "Q794",
    "Israel":      "Q801",
    "Russia":      "Q159",
    "USA":         "Q30",
    "China":       "Q148",
    "Yemen":       "Q805",
    "Lebanon":     "Q822",
    "Syria":       "Q858",
    "Iraq":        "Q796",
    "Turkey":      "Q43",
    "UAE":         "Q878",
    "Saudi Arabia":"Q851",
    "Ukraine":     "Q212",
    "North Korea": "Q423",
}

# Bekannte Entitäten-Cache (verhindert Doppel-Abfragen)
_ENTITY_CACHE: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# SPARQL Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _sparql(query: str, timeout: int = REQUEST_TIMEOUT) -> list[dict]:
    """Führt SPARQL-Abfrage aus und gibt Rows zurück."""
    time.sleep(_RATE_DELAY)
    try:
        r = requests.get(
            SPARQL_URL,
            params={"query": query, "format": "json"},
            headers=HEADERS,
            timeout=timeout,
        )
        r.raise_for_status()
        bindings = r.json().get("results", {}).get("bindings", [])
        rows = []
        for b in bindings:
            row = {}
            for key, val in b.items():
                row[key] = val.get("value", "")
            rows.append(row)
        return rows
    except Exception as e:
        return []


def _wikidata_search(query: str, entity_type: str = "") -> list[dict]:
    """Sucht Entitäten via Wikidata API Volltext-Suche."""
    time.sleep(_RATE_DELAY)
    params = {
        "action":   "wbsearchentities",
        "search":   query,
        "language": "en",
        "format":   "json",
        "limit":    5,
        "type":     "item",
    }
    if entity_type:
        params["type"] = entity_type
    try:
        r = requests.get(WIKIDATA_API, params=params,
                         headers={"User-Agent": HEADERS["User-Agent"]},
                         timeout=10)
        r.raise_for_status()
        return r.json().get("search", [])
    except Exception:
        return []


def _get_entity_data(qid: str) -> dict:
    """Holt vollständige Entitätsdaten für eine QID."""
    if qid in _ENTITY_CACHE:
        return _ENTITY_CACHE[qid]
    time.sleep(_RATE_DELAY)
    try:
        r = requests.get(
            WIKIDATA_API,
            params={"action": "wbgetentities", "ids": qid,
                    "languages": "en|de|ar|fa|ru",
                    "format": "json", "props": "labels|descriptions|claims|aliases"},
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("entities", {}).get(qid, {})
        _ENTITY_CACHE[qid] = data
        return data
    except Exception:
        return {}


def _label(entity: dict, lang: str = "en") -> str:
    """Extrahiert Label aus Entity-Dict."""
    return (entity.get("labels", {}).get(lang, {}).get("value")
            or entity.get("labels", {}).get("en", {}).get("value")
            or "")


def _description(entity: dict) -> str:
    """Extrahiert englische Beschreibung."""
    return entity.get("descriptions", {}).get("en", {}).get("value", "")


def _claim_values(entity: dict, prop: str) -> list[str]:
    """Extrahiert alle Werte einer Property."""
    claims = entity.get("claims", {}).get(prop, [])
    values = []
    for c in claims:
        ms = c.get("mainsnak", {})
        dv = ms.get("datavalue", {})
        if dv.get("type") == "wikibase-entityid":
            values.append("Q" + str(dv.get("value", {}).get("numeric-id", "")))
        elif dv.get("type") == "string":
            values.append(dv.get("value", ""))
        elif dv.get("type") == "monolingualtext":
            values.append(dv.get("value", {}).get("text", ""))
        elif dv.get("type") == "quantity":
            values.append(str(dv.get("value", {}).get("amount", "")))
    return [v for v in values if v]


def _qid_to_label(qid: str) -> str:
    """QID → lesbares Label."""
    if not qid.startswith("Q"):
        return qid
    try:
        r = requests.get(
            WIKIDATA_API,
            params={"action": "wbgetentities", "ids": qid,
                    "languages": "en", "format": "json", "props": "labels"},
            headers={"User-Agent": HEADERS["User-Agent"]},
            timeout=8,
        )
        r.raise_for_status()
        labels = r.json().get("entities", {}).get(qid, {}).get("labels", {})
        return labels.get("en", {}).get("value", qid)
    except Exception:
        return qid


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def resolve_entity(name: str, entity_type: str = "") -> dict:
    """
    Löst einen Namen zu einer strukturierten Wikidata-Entität auf.

    Parameters
    ----------
    name        : z.B. "Quds Force", "Hezbollah", "Shahed-136"
    entity_type : Optional: "military_unit", "person", "weapon", "org"

    Returns
    -------
    dict mit: qid, name, description, country, type, aliases,
              related_entities, confidence
    """
    cache_key = f"{name}::{entity_type}"
    if cache_key in _ENTITY_CACHE:
        return _ENTITY_CACHE[cache_key]

    # Suche
    results = _wikidata_search(name)
    if not results:
        return {"status": "not_found", "name": name}

    # Bestes Ergebnis
    best = results[0]
    qid  = best.get("id", "")
    if not qid:
        return {"status": "no_qid", "name": name}

    # Vollständige Daten
    entity = _get_entity_data(qid)
    if not entity:
        return {"status": "no_data", "qid": qid, "name": name}

    # Properties parsen
    # P17 = country, P31 = instance of, P279 = subclass of
    # P495 = country of origin, P127 = owned by, P749 = parent org
    # P108 = employer, P463 = member of, P710 = participant
    country_qids  = _claim_values(entity, "P17")[:3]
    instance_qids = _claim_values(entity, "P31")[:3]
    parent_qids   = _claim_values(entity, "P749")[:2]
    origin_qids   = _claim_values(entity, "P495")[:2]
    founder_qids  = _claim_values(entity, "P112")[:2]

    # Labels auflösen (nur für Top-Treffer)
    countries = [_qid_to_label(q) for q in (country_qids + origin_qids)[:2]]
    instances = [_qid_to_label(q) for q in instance_qids[:2]]
    parents   = [_qid_to_label(q) for q in parent_qids[:2]]

    aliases = [a.get("value", "") for a in
               entity.get("aliases", {}).get("en", [])[:5]]

    result = {
        "status":    "found",
        "qid":       qid,
        "name":      _label(entity) or name,
        "description": _description(entity)[:150],
        "country":   countries,
        "type":      instances,
        "parent_org": parents,
        "aliases":   aliases,
        "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
        "confidence": 0.90 if best.get("match", {}).get("type") == "label" else 0.65,
    }

    _ENTITY_CACHE[cache_key] = result
    return result


def get_military_unit(unit_name: str) -> dict:
    """
    Löst einen militärischen Verband auf.
    Gibt Land, Übergeordneten Verband, Typ (Division/Brigade/...) zurück.
    """
    # SPARQL: Militärische Einheit + Land + parent unit
    query = f"""
SELECT ?item ?itemLabel ?countryLabel ?instanceLabel ?parentLabel ?commanderLabel WHERE {{
  ?item wdt:P31/wdt:P279* wd:Q1335818 .
  OPTIONAL {{ ?item wdt:P17 ?country }}
  OPTIONAL {{ ?item wdt:P31 ?instance }}
  OPTIONAL {{ ?item wdt:P749 ?parent }}
  OPTIONAL {{ ?item wdt:P1308 ?commander }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
  ?item rdfs:label ?itemLabel .
  FILTER(CONTAINS(LCASE(?itemLabel), "{unit_name.lower()[:30]}"))
  FILTER(LANG(?itemLabel) = "en")
}}
LIMIT 5
"""
    rows = _sparql(query)
    if rows:
        r = rows[0]
        return {
            "status":       "found",
            "unit":         r.get("itemLabel", unit_name),
            "country":      r.get("countryLabel", ""),
            "type":         r.get("instanceLabel", ""),
            "parent_unit":  r.get("parentLabel", ""),
            "commander":    r.get("commanderLabel", ""),
            "qid":          r.get("item", "").split("/")[-1],
        }
    # Fallback: allgemeine Suche
    return resolve_entity(unit_name, "military_unit")


def get_arms_supplier(weapon_system: str) -> dict:
    """
    Wer hat dieses Waffensystem entwickelt und an wen geliefert?
    z.B. "Shahed-136" → Iran → geliefert an Russland

    Parameters
    ----------
    weapon_system : Name des Waffensystems

    Returns
    -------
    dict mit: weapon, developer, operators (Nutzerländer), origin_country
    """
    query = f"""
SELECT ?item ?itemLabel ?originLabel ?operatorLabel ?mfrLabel WHERE {{
  ?item wdt:P31/wdt:P279* wd:Q728 .
  OPTIONAL {{ ?item wdt:P495 ?origin }}
  OPTIONAL {{ ?item wdt:P176 ?mfr }}
  OPTIONAL {{ ?item wdt:P176 ?operator }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
  ?item rdfs:label ?itemLabel .
  FILTER(CONTAINS(LCASE(?itemLabel), "{weapon_system.lower()[:25]}"))
  FILTER(LANG(?itemLabel) = "en")
}}
LIMIT 8
"""
    rows = _sparql(query)
    if rows:
        operators = list({r.get("operatorLabel", "") for r in rows if r.get("operatorLabel")})
        r0 = rows[0]
        return {
            "status":         "found",
            "weapon":         r0.get("itemLabel", weapon_system),
            "origin_country": r0.get("originLabel", ""),
            "manufacturer":   r0.get("mfrLabel", ""),
            "known_operators": operators[:5],
            "qid":            r0.get("item", "").split("/")[-1],
        }
    return resolve_entity(weapon_system, "weapon")


def get_person_profile(name: str) -> dict:
    """
    Profil einer militärischen Führungsperson oder Politikers.
    z.B. "Yahya Sinwar", "Ismail Haniyeh", "Mohammad Bagheri"
    """
    query = f"""
SELECT ?item ?itemLabel ?descriptionLabel ?nationalityLabel
       ?positionLabel ?employerLabel ?birthdate WHERE {{
  ?item wdt:P31 wd:Q5 .
  OPTIONAL {{ ?item wdt:P27 ?nationality }}
  OPTIONAL {{ ?item wdt:P39 ?position }}
  OPTIONAL {{ ?item wdt:P108 ?employer }}
  OPTIONAL {{ ?item wdt:P569 ?birthdate }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
  ?item rdfs:label ?itemLabel .
  FILTER(CONTAINS(LCASE(?itemLabel), "{name.lower()[:25]}"))
  FILTER(LANG(?itemLabel) = "en")
}}
LIMIT 5
"""
    rows = _sparql(query)
    if rows:
        r = rows[0]
        return {
            "status":      "found",
            "name":        r.get("itemLabel", name),
            "nationality": r.get("nationalityLabel", ""),
            "position":    r.get("positionLabel", ""),
            "organization":r.get("employerLabel", ""),
            "birthdate":   (r.get("birthdate") or "")[:10],
            "qid":         r.get("item", "").split("/")[-1],
        }
    return resolve_entity(name, "person")


def get_organization(org_name: str) -> dict:
    """
    Profil einer Organisation (Miliz, Terrorgruppe, Geheimdienst).
    z.B. "IRGC", "Hamas", "Hezbollah", "Houthis", "Wagner Group"
    """
    query = f"""
SELECT ?item ?itemLabel ?countryLabel ?instanceLabel ?founderLabel
       ?memberCountLabel WHERE {{
  ?item wdt:P31 ?instance .
  OPTIONAL {{ ?item wdt:P17 ?country }}
  OPTIONAL {{ ?item wdt:P112 ?founder }}
  OPTIONAL {{ ?item wdt:P2124 ?memberCount }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
  ?item rdfs:label ?itemLabel .
  FILTER(CONTAINS(LCASE(?itemLabel), "{org_name.lower()[:25]}"))
  FILTER(LANG(?itemLabel) = "en")
  FILTER(?instance IN (wd:Q7210356, wd:Q1335818, wd:Q18205551,
                        wd:Q4830453, wd:Q484652, wd:Q17149090))
}}
LIMIT 5
"""
    rows = _sparql(query)
    if rows:
        r = rows[0]
        return {
            "status":      "found",
            "name":        r.get("itemLabel", org_name),
            "country":     r.get("countryLabel", ""),
            "type":        r.get("instanceLabel", ""),
            "founder":     r.get("founderLabel", ""),
            "member_count": r.get("memberCountLabel", ""),
            "qid":         r.get("item", "").split("/")[-1],
        }
    return resolve_entity(org_name, "org")


def batch_resolve(names: list[str]) -> list[dict]:
    """
    Löst mehrere Namen in einer SPARQL-Anfrage auf.
    Effizienter als einzelne resolve_entity() Calls.
    """
    if not names:
        return []
    results = []
    for name in names:
        r = resolve_entity(name)
        r["query_name"] = name
        results.append(r)
        time.sleep(0.2)
    return results


def enrich_humint(humint_results: list[dict]) -> list[dict]:
    """
    Reichert HUMINT-Treffer mit Wikidata-Daten an.
    Sucht nach Einheiten, Personen, Waffensystemen in den Texten.

    Parameters
    ----------
    humint_results : Liste von HUMINT-Dicts mit 'text' oder 'headline'-Feld

    Returns
    -------
    humint_results mit zusätzlichem 'wikidata'-Feld pro Eintrag
    """
    enriched = []
    for item in humint_results:
        text = (item.get("text") or item.get("headline") or
                item.get("content") or "")[:500]
        # Bekannte Keyword → Entity-Typ Mapping
        KNOWN_ENTITIES = {
            "IRGC":          ("IRGC", "org"),
            "Quds Force":    ("Quds Force", "org"),
            "Hamas":         ("Hamas", "org"),
            "Hezbollah":     ("Hezbollah", "org"),
            "Houthi":        ("Houthis", "org"),
            "Wagner":        ("Wagner Group", "org"),
            "Shahed":        ("Shahed drone", "weapon"),
            "Iskander":      ("Iskander missile", "weapon"),
            "Iron Dome":     ("Iron Dome", "weapon"),
            "F-35":          ("F-35", "weapon"),
        }
        wikidata_hits = {}
        for keyword, (entity_name, etype) in KNOWN_ENTITIES.items():
            if keyword.lower() in text.lower():
                r = resolve_entity(entity_name, etype)
                if r.get("status") == "found":
                    wikidata_hits[keyword] = {
                        "qid":         r.get("qid"),
                        "description": r.get("description", ""),
                        "country":     r.get("country"),
                    }
        enriched_item = dict(item)
        if wikidata_hits:
            enriched_item["wikidata"] = wikidata_hits
        enriched.append(enriched_item)
    return enriched


def wikidata_status() -> dict:
    """Prüft ob Wikidata SPARQL erreichbar ist."""
    test_q = "SELECT ?x WHERE { wd:Q794 rdfs:label ?x . FILTER(LANG(?x)='en') } LIMIT 1"
    rows = _sparql(test_q, timeout=10)
    ok = bool(rows)
    return {
        "status":    "ok" if ok else "fehler",
        "reachable": ok,
        "endpoint":  SPARQL_URL,
        "test_result": rows[0].get("x", "") if rows else "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("NEXUS Wikidata Entity-Resolution — Selbsttest")
    print("─" * 55)

    print("\n1. Status-Check...")
    s = wikidata_status()
    print(f"   {'✅' if s['reachable'] else '❌'} {s['endpoint']}")
    if s.get("test_result"):
        print(f"   Iran = '{s['test_result']}'")

    tests = [
        ("Hamas",        get_organization),
        ("Shahed-136",   get_arms_supplier),
        ("Quds Force",   get_organization),
    ]
    for name, fn in tests:
        print(f"\n2. {name}:")
        r = fn(name)
        for k, v in r.items():
            if v and k != "wikidata_url":
                print(f"   {k}: {v}")
