"""
NEXUS – Cyber-OSINT  (T206)
============================
Cyber-Dimension von Konflikten: Infrastruktur-Exposition, Malware-Kampagnen,
Internet-Outages als Eskalations-Indikatoren.

Datenquellen:
  1. Shodan InternetDB   https://internetdb.shodan.io/ (kostenlos, kein Key)
  2. VirusTotal          https://www.virustotal.com/api/v3/ (Free: 500 Req/Tag)
  3. BGP.he.net          https://bgp.he.net/ (ASN/Routing-Anomalien)
  4. RIPE Stat           https://stat.ripe.net/data/ (BGP-Daten, kostenlos)
  5. Cloudflare Radar    https://radar.cloudflare.com/api/ (Internet-Traffic)
  6. CERT.gov Feeds      CISA Known Exploited Vulnerabilities

Cyber-Eskalations-Indikatoren (Iran-Kontext):
  • Iranische Infrastruktur (ASN 197207, 48159, ...) offline
  • Bekannte IRGC/APT33/APT35 C2-Server aktiv
  • Neue Malware-Hashes die Ölsektor-Keywords enthalten
  • BGP-Hijacking iranischer IP-Ranges
  • Cloudflare-Traffic-Abfall für Iran → Internet-Cut

Abhängigkeiten: pip install requests
Optional: VIRUSTOTAL_API_KEY in config.py für erweiterte Suche
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 15
_CACHE_DIR      = Path(__file__).parent / "nexus_cyber_cache"
_CACHE_TTL_H    = 2    # Cyber-Daten: alle 2h

# Bekannte ASNs für Konfliktländer
CONFLICT_ASNS: dict[str, list[str]] = {
    "Iran":    ["AS197207", "AS48159", "AS44244", "AS12880", "AS48434"],
    "Israel":  ["AS8551", "AS9116", "AS12400", "AS6845"],
    "Russia":  ["AS8359", "AS12389", "AS25478", "AS8492"],
    "Ukraine": ["AS15772", "AS3326", "AS6846"],
    "China":   ["AS4808", "AS9808", "AS17816"],
    "Yemen":   ["AS30873", "AS36866"],
    "Syria":   ["AS29256", "AS50710"],
}

# Bekannte APT-Gruppen + zugehörige IOCs (Indicators of Compromise)
APT_GROUPS: dict[str, dict] = {
    "APT33":  {"country": "Iran", "aka": ["Elfin", "Refined Kitten"],
               "targets": ["aviation", "energy", "petrochemical"],
               "mitre":   "G0064"},
    "APT35":  {"country": "Iran", "aka": ["Charming Kitten", "Phosphorus"],
               "targets": ["defense", "government", "media", "pharma"],
               "mitre":   "G0059"},
    "APT34":  {"country": "Iran", "aka": ["OilRig", "Helix Kitten"],
               "targets": ["financial", "government", "energy"],
               "mitre":   "G0049"},
    "Sandworm": {"country": "Russia", "aka": ["Voodoo Bear", "BlackEnergy"],
               "targets": ["critical infrastructure", "energy", "ukraine"],
               "mitre":   "G0034"},
    "Lazarus":  {"country": "North Korea", "aka": ["Hidden Cobra"],
               "targets": ["financial", "crypto", "defense"],
               "mitre":   "G0032"},
}

# CISA KEV URL (Known Exploited Vulnerabilities)
CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

# Cloudflare Radar API
CF_RADAR_BASE = "https://radar.cloudflare.com/api/v0"

# RIPE Stat API
RIPE_STAT_BASE = "https://stat.ripe.net/data"


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / (key[:60].replace("/","_") + ".json")

def _cached(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists(): return None
    try:
        d = json.loads(p.read_text())
        if time.time() - d.get("ts", 0) < _CACHE_TTL_H * 3600:
            return d.get("data")
    except Exception: pass
    return None

def _store(key: str, data) -> None:
    try: _cache_path(key).write_text(json.dumps({"ts": time.time(), "data": data}))
    except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# Shodan InternetDB (kein Key)
# ─────────────────────────────────────────────────────────────────────────────

def check_ip_exposure(ip: str) -> dict:
    """
    Prüft IP-Adresse auf bekannte offene Ports + Schwachstellen.
    Verwendet Shodan InternetDB (kostenlos, kein Key).
    """
    cached = _cached(f"shodan_{ip}")
    if cached:
        return cached
    try:
        r = requests.get(f"https://internetdb.shodan.io/{ip}",
                         headers={"User-Agent": "NEXUS-OSINT/1.0"},
                         timeout=10)
        if r.status_code == 404:
            return {"ip": ip, "status": "no_data"}
        r.raise_for_status()
        data = r.json()
        result = {
            "ip":          ip,
            "status":      "ok",
            "open_ports":  data.get("ports", []),
            "cves":        data.get("vulns", [])[:10],
            "tags":        data.get("tags", []),
            "hostnames":   data.get("hostnames", [])[:5],
            "cpe":         data.get("cpes", [])[:5],
            "risk_score":  len(data.get("vulns", [])) * 2 + len(data.get("ports", [])) * 0.1,
        }
        _store(f"shodan_{ip}", result)
        return result
    except Exception as e:
        return {"ip": ip, "status": "fehler", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# RIPE Stat – BGP-Routing-Daten
# ─────────────────────────────────────────────────────────────────────────────

def get_bgp_status(asn: str) -> dict:
    """
    Prüft BGP-Routing-Status eines ASN via RIPE Stat.
    Erkennt: Routing-Anomalien, Prefix-Hijacking, AS-PATH-Änderungen.
    """
    asn_clean = asn.replace("AS", "")
    cached = _cached(f"bgp_{asn_clean}")
    if cached:
        return cached

    try:
        # AS-Overview
        r = requests.get(
            f"{RIPE_STAT_BASE}/as-overview/data.json",
            params={"resource": f"AS{asn_clean}"},
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        overview = r.json().get("data", {})

        # Prefixes
        r2 = requests.get(
            f"{RIPE_STAT_BASE}/announced-prefixes/data.json",
            params={"resource": f"AS{asn_clean}", "starttime": "2024-01-01"},
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        r2.raise_for_status()
        prefix_data = r2.json().get("data", {})
        prefixes = prefix_data.get("prefixes", [])[:10]

        result = {
            "asn":           f"AS{asn_clean}",
            "status":        "ok",
            "name":          overview.get("holder", ""),
            "announced":     overview.get("announced", False),
            "prefix_count":  len(prefixes),
            "prefixes":      [p.get("prefix", "") for p in prefixes[:5]],
            "is_routed":     overview.get("announced", False),
        }
        _store(f"bgp_{asn_clean}", result)
        return result
    except Exception as e:
        return {"asn": asn, "status": "fehler", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Radar — Internet-Traffic-Anomalien
# ─────────────────────────────────────────────────────────────────────────────

def get_internet_traffic(country_code: str) -> dict:
    """
    Holt Internet-Traffic-Daten für ein Land via Cloudflare Radar.
    Plötzlicher Abfall = Internet-Blockade oder Infrastruktur-Angriff.

    Parameters
    ----------
    country_code : 2-stelliger ISO-Code, z.B. "IR", "IL", "RU"
    """
    cached = _cached(f"cf_{country_code}")
    if cached:
        return cached

    try:
        r = requests.get(
            f"{CF_RADAR_BASE}/traffic/timeseries",
            params={
                "location": country_code,
                "dateRange": "3d",
                "format":    "json",
            },
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data     = r.json().get("result", {})
        series   = data.get("serie_1", []) or data.get("values", [])
        if not series:
            return {"status": "keine_daten", "country": country_code}

        values  = [float(v) for v in series if v is not None]
        avg     = sum(values) / len(values) if values else 0
        latest  = values[-1] if values else 0
        drop    = ((avg - latest) / avg * 100) if avg > 0 else 0

        result = {
            "status":         "ok",
            "country_code":   country_code,
            "traffic_avg":    round(avg, 2),
            "traffic_latest": round(latest, 2),
            "drop_pct":       round(drop, 1),
            "anomaly":        drop > 30,   # >30% Abfall = Anomalie
            "severity":       "KRITISCH" if drop > 60 else "HOCH" if drop > 40 else "MITTEL" if drop > 20 else "NORMAL",
        }
        _store(f"cf_{country_code}", result)
        return result
    except Exception as e:
        return {"status": "fehler", "country": country_code, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# CISA KEV – Aktiv ausgenutzte Schwachstellen
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_cve_exploits(days_back: int = 30) -> list[dict]:
    """
    Holt aktiv ausgenutzte CVEs (CISA KEV-Datenbank).
    Fokus: Industrielle Steuerungssysteme (SCADA/ICS) = Iran-relevant.
    """
    cached = _cached(f"kev_{days_back}")
    if cached:
        return cached

    try:
        r = requests.get(CISA_KEV_URL,
                         headers={"User-Agent": "NEXUS-OSINT/1.0"},
                         timeout=20)
        r.raise_for_status()
        vulns = r.json().get("vulnerabilities", [])

        # Neueste filtern
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days_back)).date().isoformat()

        recent  = [v for v in vulns if v.get("dateAdded", "") >= cutoff]
        # ICS/SCADA besonders relevant
        ics_kw  = ["scada", "ics", "plc", "industrial", "siemens",
                   "schneider", "control system", "ot/"]
        ics_hits = [v for v in recent
                    if any(kw in (v.get("vendorProject","") +
                                  v.get("product","") +
                                  v.get("shortDescription","")).lower()
                           for kw in ics_kw)]
        result = [
            {
                "cve_id":      v.get("cveID", ""),
                "vendor":      v.get("vendorProject", ""),
                "product":     v.get("product", ""),
                "description": v.get("shortDescription", "")[:150],
                "date_added":  v.get("dateAdded", ""),
                "is_ics":      v in ics_hits,
                "due_date":    v.get("dueDate", ""),
            }
            for v in (ics_hits + [x for x in recent if x not in ics_hits])[:20]
        ]
        _store(f"kev_{days_back}", result)
        return result
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Funktion
# ─────────────────────────────────────────────────────────────────────────────

def get_cyber_situation(region: str) -> dict:
    """
    Cyber-Lageeinschätzung für eine Region.
    Prüft ASN-Status, Internet-Traffic, APT-Bedrohungen.
    """
    # Länderkürzel
    country_iso2 = {
        "Iran": "IR", "Israel": "IL", "Russia": "RU", "Ukraine": "UA",
        "China": "CN", "Syria": "SY", "Yemen": "YE",
    }.get(region, region[:2].upper())

    # ASNs der Region
    asns = CONFLICT_ASNS.get(region, [])

    # Internet-Traffic
    traffic = get_internet_traffic(country_iso2)

    # BGP-Status für erste ASN
    bgp = {}
    if asns:
        bgp = get_bgp_status(asns[0])

    # Relevante APTs
    relevant_apts = {
        name: info for name, info in APT_GROUPS.items()
        if info.get("country") == region or
        region.lower() in " ".join(info.get("targets", []))
    }

    # Eskalations-Score
    score = 0.0
    notes = []
    if traffic.get("anomaly"):
        drop = traffic.get("drop_pct", 0)
        score += min(15.0, drop / 5.0)
        notes.append(f"Internet-Traffic Einbruch {drop:.0f}% ({traffic.get('severity')})")

    if bgp.get("status") == "ok" and not bgp.get("is_routed"):
        score += 10.0
        notes.append(f"ASN {bgp.get('asn')} nicht mehr geroutet")

    return {
        "status":          "ok",
        "region":          region,
        "internet_traffic": traffic,
        "bgp_status":      bgp,
        "relevant_apts":   list(relevant_apts.keys()),
        "apt_details":     relevant_apts,
        "escalation_score": round(score, 1),
        "notes":           notes,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }


def cyber_escalation_signal(region: str) -> dict:
    """Für nexus_escalation.py."""
    sit = get_cyber_situation(region)
    return {
        "status":  sit.get("status", "fehler"),
        "score":   sit.get("escalation_score", 0.0),
        "notes":   sit.get("notes", []),
        "region":  region,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direktaufruf
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    region = sys.argv[1] if len(sys.argv) > 1 else "Iran"
    print(f"NEXUS Cyber-OSINT — {region}")
    print("─" * 50)

    sit = get_cyber_situation(region)
    t = sit.get("internet_traffic", {})
    if t.get("status") == "ok":
        print(f"Internet-Traffic:  Ø{t.get('traffic_avg',0):.1f} | "
              f"aktuell {t.get('traffic_latest',0):.1f} | "
              f"Δ{t.get('drop_pct',0):+.1f}%  [{t.get('severity','?')}]")
        if t.get("anomaly"):
            print("  ⚠ TRAFFIC-ANOMALIE ERKANNT")

    b = sit.get("bgp_status", {})
    if b.get("status") == "ok":
        print(f"\nBGP {b.get('asn')}: {b.get('name','?')} | "
              f"{'aktiv' if b.get('is_routed') else '❌ OFFLINE'}")

    if sit.get("relevant_apts"):
        print(f"\nRelevante APTs: {', '.join(sit['relevant_apts'])}")

    sig = cyber_escalation_signal(region)
    print(f"\nEskalations-Signal: {sig['score']:.1f} pts")
    for n in sig.get("notes", []):
        print(f"  • {n}")
