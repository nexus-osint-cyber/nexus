"""
NEXUS – SIPRI Waffentransfer-Monitor  (T202)
============================================
Verfolgt globale Waffenlieferungen via SIPRI Arms Transfers Database.
Identifiziert Lieferketten die als Eskalations-Vorläufer gelten.

Datenquellen:
  1. SIPRI AT Database CSV (öffentlich downloadbar)
     https://www.sipri.org/databases/armstransfers
  2. SIPRI Fact Sheet RSS / HTML (aktuelle Pressemitteilungen)
  3. Reuters/Jane's Defense aggregierter RSS-Feed als Backup

Rückgabe:
  get_transfers(supplier, recipient, years) → list[dict]
  Jede Lieferung: supplier, recipient, weapon_type, tiv_value,
                  year, status, notes

TIV = Trend Indicator Value (SIPRI-eigene Einheit für Rüstungsvolumen)

Eskalations-Relevanz:
  - Drohnen-Transfers an Iran / von Iran → hoher Eskalations-Wert
  - Ballistik-Raketen-Transfers → sehr hoch
  - Luft-/Raketenabwehr-Transfers → reaktiv (Verteidigung)

Abhängigkeiten: pip install requests
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT  = 20
_CACHE_DIR       = Path(__file__).parent / "nexus_sipri_cache"
_CACHE_TTL_H     = 24

# SIPRI Daten-URLs
SIPRI_AT_CSV_URL = "https://www.sipri.org/sites/default/files/SIPRI-Milex-data-1949-2024.xlsx"
SIPRI_AT_RSS     = "https://www.sipri.org/rss.xml"
SIPRI_PRESSROOM  = "https://www.sipri.org/media/press-release"

# Backup RSS-Feeds für Arms Transfer News
_BACKUP_FEEDS = [
    "https://breakingdefense.com/feed/",
    "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://www.janes.com/feeds/news",
]

# Hochrisiko-Waffenkategorien (TIV-gewichtet)
ESCALATION_WEIGHTS: dict[str, float] = {
    "ballistic missile":     3.0,
    "cruise missile":        2.5,
    "drone":                 2.0,
    "uav":                   2.0,
    "combat aircraft":       2.0,
    "fighter":               2.0,
    "surface-to-air missile":1.5,
    "anti-aircraft":         1.3,
    "submarine":             2.5,
    "warship":               1.8,
    "tank":                  1.5,
    "armoured vehicle":      1.2,
    "artillery":             1.5,
    "small arms":            0.5,
}

# Konflikt-relevante Länder für Filterung
CONFLICT_COUNTRIES = {
    "Iran", "Israel", "Russia", "Ukraine", "Yemen", "Syria", "Iraq",
    "Lebanon", "Saudi Arabia", "UAE", "China", "North Korea", "Turkey",
    "Hamas", "Hezbollah", "Houthi", "Gaza", "Palestine",
}

# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / (key.replace("/", "_") + ".json")

def _cache_get(key: str) -> Optional[list]:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        if time.time() - data.get("ts", 0) < _CACHE_TTL_H * 3600:
            return data.get("items")
    except Exception:
        pass
    return None

def _cache_set(key: str, items: list) -> None:
    try:
        _cache_path(key).write_text(
            json.dumps({"ts": time.time(), "items": items})
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Datenquellen
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_sipri_news() -> list[dict]:
    """Holt aktuelle SIPRI Arms Transfer Pressemitteilungen via RSS."""
    cached = _cache_get("sipri_news")
    if cached is not None:
        return cached
    items = []
    try:
        r = requests.get(SIPRI_AT_RSS,
                         headers={"User-Agent": "NEXUS-OSINT/1.0"},
                         timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r.text)
        for item in root.iter("item"):
            title       = (item.findtext("title") or "").strip()
            link        = (item.findtext("link")  or "").strip()
            description = (item.findtext("description") or "").strip()[:300]
            pubdate     = (item.findtext("pubDate") or "").strip()
            if any(kw in title.lower() or kw in description.lower()
                   for kw in ["arms", "weapon", "transfer", "deliver", "sale",
                               "drone", "missile", "military"]):
                items.append({
                    "title":       title,
                    "url":         link,
                    "description": description,
                    "date":        pubdate,
                    "source":      "SIPRI",
                })
    except Exception:
        pass

    # Backup RSS-Feeds
    if not items:
        for feed_url in _BACKUP_FEEDS:
            try:
                r = requests.get(feed_url,
                                 headers={"User-Agent": "NEXUS-OSINT/1.0"},
                                 timeout=12)
                r.raise_for_status()
                import xml.etree.ElementTree as ET
                root = ET.fromstring(r.text)
                for item in root.iter("item"):
                    title = (item.findtext("title") or "").strip()
                    if any(kw in title.lower()
                           for kw in ["arms transfer", "weapons sale", "drone deal",
                                      "missile deal", "military aid"]):
                        items.append({
                            "title":  title,
                            "url":    (item.findtext("link") or "").strip(),
                            "date":   (item.findtext("pubDate") or "").strip(),
                            "source": feed_url.split("/")[2],
                        })
                if items:
                    break
            except Exception:
                continue

    _cache_set("sipri_news", items)
    return items


def _fetch_conflict_transfers_rss(country: str) -> list[dict]:
    """
    Sucht in Defense-RSS nach Waffentransfers für ein bestimmtes Land.
    """
    cached = _cache_get(f"transfer_{country.lower()}")
    if cached is not None:
        return cached

    results = []
    search_terms = [country.lower(), "arms transfer", "weapons"]

    for feed_url in _BACKUP_FEEDS:
        try:
            r = requests.get(feed_url,
                             headers={"User-Agent": "NEXUS-OSINT/1.0"},
                             timeout=12)
            r.raise_for_status()
            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.text)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").lower()
                desc  = (item.findtext("description") or "").lower()
                text  = title + " " + desc
                if country.lower() in text and any(
                        kw in text for kw in ["arms", "weapon", "drone",
                                              "missile", "deliver", "sale"]):
                    weapon_type = _extract_weapon_type(title)
                    results.append({
                        "title":       (item.findtext("title") or "")[:100],
                        "supplier":    _extract_supplier(title),
                        "recipient":   country,
                        "weapon_type": weapon_type,
                        "date":        (item.findtext("pubDate") or "")[:20],
                        "url":         (item.findtext("link") or "").strip(),
                        "source":      feed_url.split("/")[2],
                        "tiv_estimate": None,
                        "escalation_score": ESCALATION_WEIGHTS.get(weapon_type.lower(), 1.0),
                    })
        except Exception:
            continue

    _cache_set(f"transfer_{country.lower()}", results)
    return results


def _extract_weapon_type(text: str) -> str:
    """Extrahiert Waffentyp aus Nachrichtentitel."""
    text = text.lower()
    for wtype in sorted(ESCALATION_WEIGHTS.keys(),
                         key=lambda x: len(x), reverse=True):
        if wtype in text:
            return wtype
    for kw in ["f-16", "f-35", "su-", "mig-", "apache", "chinook"]:
        if kw in text:
            return "combat aircraft"
    for kw in ["shahed", "kamikaze", "loitering"]:
        if kw in text:
            return "drone"
    for kw in ["iskander", "kinzhal", "fateh", "zolfaghar"]:
        if kw in text:
            return "ballistic missile"
    return "military equipment"


def _extract_supplier(text: str) -> str:
    """Versucht Lieferland aus Nachrichtentitel zu extrahieren."""
    text = text.lower()
    country_kw = {
        "russia": "Russia", "russian": "Russia",
        "iran": "Iran", "iranian": "Iran",
        "china": "China", "chinese": "China",
        "us ": "USA", "united states": "USA", "american": "USA",
        "north korea": "North Korea",
        "turkey": "Turkey", "turkish": "Turkey",
    }
    for kw, country in country_kw.items():
        if kw in text:
            return country
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def get_transfers(
    supplier:   Optional[str] = None,
    recipient:  Optional[str] = None,
    weapon_type: Optional[str] = None,
    limit:      int = 20,
) -> list[dict]:
    """
    Gibt bekannte Waffentransfers zurück, gefiltert nach Lieferant/Empfänger/Typ.

    Parameters
    ----------
    supplier    : z.B. "Russia", "Iran", "China"
    recipient   : z.B. "Iran", "Yemen", "Hamas"
    weapon_type : z.B. "drone", "missile" (optionaler Filter)
    limit       : Maximale Anzahl Treffer

    Returns
    -------
    list[dict] mit: supplier, recipient, weapon_type, date,
                    escalation_score, source, url
    """
    target = recipient or supplier or "Iran"

    # News-basierte Transfers
    news  = _fetch_sipri_news()
    xfers = _fetch_conflict_transfers_rss(target)

    # Kombinieren und filtern
    all_items = []
    for item in (news + xfers):
        text = (item.get("title", "") + " " +
                item.get("description", "")).lower()
        # Supplier-Filter
        if supplier and supplier.lower() not in text:
            continue
        # Recipient-Filter
        if recipient and recipient.lower() not in text:
            continue
        # Weapon-Filter
        if weapon_type and weapon_type.lower() not in text:
            continue

        wtype = item.get("weapon_type") or _extract_weapon_type(
            item.get("title", ""))
        all_items.append({
            "supplier":        item.get("supplier") or _extract_supplier(
                                   item.get("title", "")),
            "recipient":       recipient or target,
            "weapon_type":     wtype,
            "date":            item.get("date", "")[:20],
            "tiv_estimate":    item.get("tiv_estimate"),
            "escalation_score": ESCALATION_WEIGHTS.get(wtype, 1.0),
            "title":           item.get("title", "")[:100],
            "url":             item.get("url", ""),
            "source":          item.get("source", "unknown"),
        })

    # Nach Datum sortieren (neueste zuerst)
    all_items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return all_items[:limit]


def get_conflict_arms_flow(region: str) -> dict:
    """
    Analysiert den Waffenfluss in eine Konfliktregion.
    Gibt Lieferanten, Empfänger, Waffentypen und Eskalations-Score zurück.
    """
    cached = _cache_get(f"flow_{region.lower()}")
    if cached is not None:
        return {"status": "ok", "region": region, "data": cached}

    # Alle relevanten Transfers holen
    transfers = get_transfers(recipient=region, limit=30)

    if not transfers:
        return {"status": "keine_daten", "region": region, "count": 0}

    # Aggregieren
    suppliers     = {}
    weapon_types  = {}
    total_esc     = 0.0

    for t in transfers:
        sup = t["supplier"]
        wt  = t["weapon_type"]
        suppliers[sup]    = suppliers.get(sup, 0) + 1
        weapon_types[wt]  = weapon_types.get(wt, 0) + 1
        total_esc += t.get("escalation_score", 1.0)

    top_suppliers = sorted(suppliers.items(), key=lambda x: x[1], reverse=True)
    top_weapons   = sorted(weapon_types.items(), key=lambda x: x[1], reverse=True)

    result = {
        "status":          "ok",
        "region":          region,
        "count":           len(transfers),
        "escalation_index": round(total_esc / max(len(transfers), 1), 2),
        "top_suppliers":   top_suppliers[:5],
        "top_weapon_types": top_weapons[:5],
        "latest":          transfers[:3],
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(f"flow_{region.lower()}", [result])
    return result


def sipri_escalation_signal(region: str) -> dict:
    """
    Für nexus_escalation.py: Gibt Eskalations-Signal basierend auf Waffentransfers.
    """
    flow = get_conflict_arms_flow(region)
    if flow.get("status") != "ok" or not flow.get("count"):
        return {"status": "keine_daten", "score": 0.0}

    esc_idx = flow.get("escalation_index", 0)
    # Hochrisiko-Waffen erhöhen Score massiv
    high_risk = [wt for wt, _ in flow.get("top_weapon_types", [])
                 if ESCALATION_WEIGHTS.get(wt, 0) >= 2.0]

    base_score = min(20.0, esc_idx * 5.0)
    if high_risk:
        base_score *= 1.5

    return {
        "status":       "ok",
        "score":        round(base_score, 1),
        "count":        flow["count"],
        "high_risk_weapons": high_risk,
        "top_supplier": flow.get("top_suppliers", [["?", 0]])[0][0],
        "region":       region,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Iran"
    print(f"NEXUS SIPRI Waffentransfer-Monitor — Region: {region}")
    print("─" * 55)

    flow = get_conflict_arms_flow(region)
    print(f"Status:   {flow['status']}")
    print(f"Einträge: {flow.get('count', 0)}")
    print(f"Eskalations-Index: {flow.get('escalation_index', 0):.2f}")

    if flow.get("top_suppliers"):
        print("\nTop-Lieferanten:")
        for sup, cnt in flow.get("top_suppliers", []):
            print(f"  {sup}: {cnt}x")

    if flow.get("top_weapon_types"):
        print("\nWaffentypen:")
        for wt, cnt in flow.get("top_weapon_types", []):
            esc = ESCALATION_WEIGHTS.get(wt, 1.0)
            print(f"  {wt}: {cnt}x  (Eskalations-Gewicht: {esc})")

    sig = sipri_escalation_signal(region)
    print(f"\nEskalations-Signal: {sig['score']:.1f} pts")
    if sig.get("high_risk_weapons"):
        print(f"Hochrisiko-Waffen: {', '.join(sig['high_risk_weapons'])}")
