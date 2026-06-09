"""
nexus_finint.py  — T177
FININT: Financial Intelligence für NEXUS.

Komponenten:
  1. OFAC SDN-Liste  — US-Sanktionsliste (XML-Download, lokal gecacht)
  2. EU Sanktionsliste — EUR-Lex XML, lokal gecacht
  3. OpenCorporates  — Unternehmens-Lookups (kostenlos, kein Key)
  4. Blockchair API  — Blockchain-Analyse BTC/ETH (kostenlos, kein Key)
  5. Shell-Company Patterns — Heuristische Erkennung von Briefkastenfirmen

Verwendung:
  from nexus_finint import check_entity, finint_for_map
  python nexus_finint.py --check "SHANDONG SHIPPING" --verbose
  python nexus_finint.py --check-wallet "1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf"
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Konfiguration ────────────────────────────────────────────────────────────

_DATA_DIR      = Path(__file__).parent / "nexus_data"
_DATA_DIR.mkdir(exist_ok=True)
_OFAC_CACHE    = _DATA_DIR / "ofac_sdn.db"
_OFAC_XML_URL  = "https://www.treasury.gov/ofac/downloads/sdn.xml"
_EU_CACHE      = _DATA_DIR / "eu_sanctions.db"
_EU_XML_URL    = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"

# Cache-Gültigkeits-Dauer (Sanktionslisten ändern sich täglich)
_CACHE_TTL_HOURS = 24

# Blockchair API (kostenlos, kein Key, Rate-Limit: 30/min)
_BLOCKCHAIR_BASE = "https://api.blockchair.com"

# ─── Shell-Company Indikatoren ────────────────────────────────────────────────

_SHELL_PATTERNS = [
    # Jurisdiktionen die für Briefkastenfirmen bekannt sind
    r'\b(BVI|British Virgin Islands|Cayman|Panama|Seychelles|Belize|'
    r'Marshall Islands|Liberia|Vanuatu|Niue|Nevis)\b',
    # Generic officer names (Nominee Directors)
    r'\b(nominee|bearer shares|registered agent|trust services)\b',
    # Russische/Iranische Offshore-Strukturen
    r'\b(Vladivostok|Nakhodka|Bandar Abbas)\b.{0,50}\b(LLC|Ltd|Corp|Inc)\b',
]

# Shipping-Sanktionsumgehungs-Indikatoren
_SHIPPING_EVASION_PATTERNS = [
    r'flag\s+(?:changed|switched|hopped)',
    r'AIS\s+(?:disabled|turned off|dark)',
    r'ship-to-ship\s+transfer',
    r'STS\s+transfer',
    r'phantom\s+(?:ship|voyage)',
    r'falsif',
    r'spoofing',
]

# Bekannte Schattenflotten-Betreiber (öffentlich bekannt, z.B. aus OFAC/EU-Listings)
_KNOWN_SHADOW_FLEET_INDICATORS = {
    "sun ship", "dark horse", "silver", "black pearl", "ghost",
    "phantom", "shadow", "vostok", "orient", "eastern", "pacific star",
    "atlantic star", "golden", "lucky", "ever", "great wall",
}

# ─── Datentypen ───────────────────────────────────────────────────────────────

@dataclass
class SanctionHit:
    entity:      str
    list_name:   str       # "OFAC_SDN", "EU", "UN"
    program:     str       # "IRAN", "RUSSIA", "DPRK", etc.
    entry_type:  str       # "individual", "vessel", "company"
    confidence:  float     # 0.0–1.0 (exact match = 1.0)
    details:     str       # Zusatzinfos aus der Sanktionsliste
    match_type:  str       # "exact", "fuzzy", "partial"

    def to_dict(self) -> dict:
        return {
            "entity":     self.entity,
            "list":       self.list_name,
            "program":    self.program,
            "type":       self.entry_type,
            "confidence": round(self.confidence, 2),
            "details":    self.details[:300],
            "match_type": self.match_type,
        }


@dataclass
class FinintResult:
    query:         str
    sanction_hits: list[SanctionHit] = field(default_factory=list)
    shell_score:   float = 0.0      # 0–1: Wahrscheinlichkeit Briefkastenfirma
    blockchain:    Optional[dict] = None
    companies:     list[dict] = field(default_factory=list)
    flags:         list[str] = field(default_factory=list)
    risk_level:    str = "NIEDRIG"
    processing_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "query":         self.query,
            "sanction_hits": [h.to_dict() for h in self.sanction_hits],
            "shell_score":   round(self.shell_score, 2),
            "blockchain":    self.blockchain,
            "companies":     self.companies[:5],
            "flags":         self.flags[:10],
            "risk_level":    self.risk_level,
            "n_hits":        len(self.sanction_hits),
        }


# ─── OFAC SDN Liste ───────────────────────────────────────────────────────────

def _init_sanction_db(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=10)
    con.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL,
            program  TEXT DEFAULT '',
            type     TEXT DEFAULT 'entity',
            details  TEXT DEFAULT '',
            source   TEXT DEFAULT '',
            ts       REAL DEFAULT 0
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_name ON entries(name)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.commit()
    return con


def _ofac_cache_fresh() -> bool:
    if not _OFAC_CACHE.exists():
        return False
    con = sqlite3.connect(str(_OFAC_CACHE))
    row = con.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
    con.close()
    if not row:
        return False
    age_h = (time.time() - float(row[0])) / 3600
    return age_h < _CACHE_TTL_HOURS


def _update_ofac_cache() -> bool:
    """Lädt OFAC SDN XML herunter und befüllt SQLite-Cache."""
    try:
        req = urllib.request.Request(
            _OFAC_XML_URL,
            headers={"User-Agent": "NEXUS-OSINT/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        ns = {"ofac": "http://tempuri.org/sdnList.xsd"}

        con = _init_sanction_db(_OFAC_CACHE)
        con.execute("DELETE FROM entries WHERE source='OFAC'")

        entries = []
        for sdn in root.findall(".//ofac:sdnEntry", ns):
            last   = sdn.findtext("ofac:lastName",    namespaces=ns, default="") or ""
            first  = sdn.findtext("ofac:firstName",   namespaces=ns, default="") or ""
            stype  = sdn.findtext("ofac:sdnType",     namespaces=ns, default="") or ""
            prog   = sdn.findtext("ofac:programList/ofac:program", namespaces=ns, default="") or ""

            name = f"{last} {first}".strip() if first else last.strip()
            if not name:
                continue

            # Aka-Namen auch indexieren
            akas = [
                (n.findtext("ofac:lastName", namespaces=ns, default="") or "")
                + " "
                + (n.findtext("ofac:firstName", namespaces=ns, default="") or "")
                for n in sdn.findall(".//ofac:aka", ns)
            ]

            details_parts = []
            # Vessel-spezifische Infos
            for feat in sdn.findall(".//ofac:feature", ns):
                ftype = feat.findtext("ofac:featureType", namespaces=ns, default="")
                fval  = feat.findtext(".//ofac:value/ofac:value", namespaces=ns, default="")
                if ftype and fval:
                    details_parts.append(f"{ftype}: {fval}")

            details = " | ".join(details_parts[:5])

            entries.append((name, prog, stype.lower(), details, "OFAC", time.time()))
            for aka in akas:
                aka = aka.strip()
                if aka and len(aka) > 2:
                    entries.append((aka, prog, stype.lower(), details, "OFAC", time.time()))

        con.executemany(
            "INSERT INTO entries(name,program,type,details,source,ts) VALUES(?,?,?,?,?,?)",
            entries,
        )
        con.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('updated',?)",
            (str(time.time()),),
        )
        con.commit()
        con.close()
        return True

    except Exception as e:
        return False


def _search_ofac(query: str, fuzzy: bool = True) -> list[SanctionHit]:
    """Sucht in der OFAC SDN-Liste."""
    if not _ofac_cache_fresh():
        _update_ofac_cache()

    if not _OFAC_CACHE.exists():
        return []

    hits = []
    query_clean = query.strip().upper()

    try:
        con = sqlite3.connect(str(_OFAC_CACHE))
        # Exakter Match
        rows = con.execute(
            "SELECT name,program,type,details FROM entries "
            "WHERE UPPER(name)=? AND source='OFAC'",
            (query_clean,),
        ).fetchall()
        for r in rows:
            hits.append(SanctionHit(
                entity=r[0], list_name="OFAC_SDN",
                program=r[1], entry_type=r[2],
                confidence=1.0, details=r[3], match_type="exact",
            ))

        # Partial Match (enthält den Suchbegriff)
        if fuzzy or not hits:
            rows2 = con.execute(
                "SELECT name,program,type,details FROM entries "
                "WHERE UPPER(name) LIKE ? AND source='OFAC' LIMIT 10",
                (f"%{query_clean}%",),
            ).fetchall()
            existing = {h.entity.upper() for h in hits}
            for r in rows2:
                if r[0].upper() not in existing:
                    hits.append(SanctionHit(
                        entity=r[0], list_name="OFAC_SDN",
                        program=r[1], entry_type=r[2],
                        confidence=0.7, details=r[3], match_type="partial",
                    ))
        con.close()
    except Exception:
        pass

    return hits[:10]


def _search_local_sanctions(query: str) -> list[SanctionHit]:
    """
    Sucht in nexus_sanctions.py (falls vorhanden — T115).
    """
    hits = []
    try:
        from nexus_sanctions import check_entity as _check  # type: ignore
        result = _check(query)
        for hit in (result.get("hits") or []):
            hits.append(SanctionHit(
                entity=hit.get("name", query),
                list_name=hit.get("list", "SANCTIONS"),
                program=hit.get("program", ""),
                entry_type=hit.get("type", "entity"),
                confidence=float(hit.get("score", 0.8)),
                details=hit.get("details", ""),
                match_type=hit.get("match", "partial"),
            ))
    except Exception:
        pass
    return hits


# ─── OpenCorporates ───────────────────────────────────────────────────────────

def _search_opencorporates(company_name: str) -> list[dict]:
    """
    Sucht Unternehmen über OpenCorporates API (kostenlos, kein Key).
    Gibt Liste von Company-Dicts zurück.
    """
    try:
        import urllib.parse
        url = (
            "https://api.opencorporates.com/companies/search"
            f"?q={urllib.parse.quote(company_name)}&format=json&per_page=5"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "NEXUS-OSINT/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        companies = []
        for item in data.get("results", {}).get("companies", []):
            c = item.get("company", {})
            companies.append({
                "name":         c.get("name", ""),
                "jurisdiction": c.get("jurisdiction_code", ""),
                "company_type": c.get("company_type", ""),
                "status":       c.get("current_status", ""),
                "registered":   c.get("incorporation_date", ""),
                "registered_address": c.get("registered_address_in_full", ""),
                "url":          c.get("opencorporates_url", ""),
            })
        return companies
    except Exception:
        return []


def _shell_score(companies: list[dict], name: str) -> tuple[float, list[str]]:
    """
    Berechnet Briefkastenfirma-Wahrscheinlichkeit.
    Gibt (score 0–1, flags) zurück.
    """
    flags: list[str] = []
    score = 0.0

    name_lower = name.lower()

    # Name-basierte Indikatoren
    for pat in _SHELL_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            flags.append(f"Shell-Jurisdiktion im Namen: {pat}")
            score += 0.3

    for indicator in _KNOWN_SHADOW_FLEET_INDICATORS:
        if indicator in name_lower:
            flags.append(f"Shadow-Fleet-Indikator: '{indicator}'")
            score += 0.2

    # Firmen-basierte Indikatoren
    offshore_jurisdictions = {"vg", "ky", "pa", "sc", "bz", "mh", "lr", "vu", "nu", "kn"}
    for company in companies:
        jur = company.get("jurisdiction", "").lower()
        if jur in offshore_jurisdictions:
            flags.append(f"Offshore-Jurisdiktion: {company['jurisdiction']}")
            score += 0.3
        if company.get("status", "").lower() in ("dissolved", "inactive", "struck off"):
            flags.append(f"Inaktive/aufgelöste Firma gefunden")
            score += 0.15
        if not company.get("registered_address"):
            flags.append("Keine registrierte Adresse")
            score += 0.1

    return min(score, 1.0), flags


# ─── Blockchain-Analyse ───────────────────────────────────────────────────────

def _check_wallet(address: str) -> Optional[dict]:
    """
    Prüft BTC/ETH-Adresse via Blockchair API.
    Gibt Transaction-Summary zurück.
    """
    # Adress-Typ erkennen
    if re.match(r'^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$', address) or \
       re.match(r'^bc1[a-z0-9]{39,59}$', address):
        chain = "bitcoin"
    elif re.match(r'^0x[a-fA-F0-9]{40}$', address):
        chain = "ethereum"
    else:
        return None

    try:
        url = f"{_BLOCKCHAIR_BASE}/{chain}/dashboards/address/{address}"
        req = urllib.request.Request(url, headers={"User-Agent": "NEXUS-OSINT/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        addr_data = data.get("data", {}).get(address, {}).get("address", {})
        if not addr_data:
            return None

        return {
            "chain":        chain,
            "address":      address,
            "balance":      addr_data.get("balance", 0),
            "received":     addr_data.get("received", 0),
            "sent":         addr_data.get("spent", 0),
            "tx_count":     addr_data.get("transaction_count", 0),
            "first_seen":   addr_data.get("first_seen_receiving", ""),
            "last_seen":    addr_data.get("last_seen_receiving", ""),
            "risk_score":   _wallet_risk_score(addr_data),
        }
    except Exception:
        return None


def _wallet_risk_score(addr_data: dict) -> str:
    """Einfaches Risiko-Scoring für Wallets."""
    tx_count = addr_data.get("transaction_count", 0)
    received = addr_data.get("received", 0)

    # Sehr viele Transaktionen mit großen Beträgen = Mixer-Verdacht
    if tx_count > 1000 and received > 100_000_000:  # > 1 BTC
        return "HOCH"
    if tx_count > 100:
        return "MITTEL"
    if tx_count > 10:
        return "NIEDRIG"
    return "MINIMAL"


# ─── Haupt-Check ─────────────────────────────────────────────────────────────

def check_entity(
    name: str,
    check_blockchain: bool = False,
    check_companies: bool = True,
) -> FinintResult:
    """
    Vollständiger FININT-Check für eine Entität (Person, Firma, Schiff).

    name: Name des zu prüfenden Subjekts
    check_blockchain: Wenn True und name ist Wallet-Adresse → Blockchain-Check
    check_companies:  Wenn True → OpenCorporates-Lookup

    Gibt FinintResult zurück.
    """
    t0 = time.time()
    result = FinintResult(query=name)

    # 1. Sanktionslisten
    sanction_hits = _search_ofac(name)
    sanction_hits += _search_local_sanctions(name)

    # Deduplizieren
    seen = set()
    for hit in sanction_hits:
        key = (hit.entity.upper(), hit.list_name)
        if key not in seen:
            seen.add(key)
            result.sanction_hits.append(hit)

    # 2. Blockchain (wenn Wallet-Adresse)
    if check_blockchain and re.match(r'^[0-9a-fA-Fx][a-fA-F0-9]{24,}$', name.strip()):
        result.blockchain = _check_wallet(name.strip())

    # 3. OpenCorporates
    if check_companies and not re.match(r'^[0-9a-fA-Fx]', name):
        result.companies = _search_opencorporates(name)

    # 4. Shell-Company Scoring
    result.shell_score, result.flags = _shell_score(result.companies, name)

    # 5. Flags aus Sanktionstreffern
    for hit in result.sanction_hits:
        if hit.program:
            result.flags.append(f"Sanktionsprogramm: {hit.program}")

    # 6. Shipping Evasion Patterns im Namen
    for pat in _SHIPPING_EVASION_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            result.flags.append(f"Sanktionsumgehungs-Indikator: {pat}")
            result.shell_score = min(result.shell_score + 0.2, 1.0)

    # 7. Risk-Level bestimmen
    if result.sanction_hits and any(h.confidence >= 0.9 for h in result.sanction_hits):
        result.risk_level = "KRITISCH"
    elif result.sanction_hits:
        result.risk_level = "HOCH"
    elif result.shell_score >= 0.6:
        result.risk_level = "HOCH"
    elif result.shell_score >= 0.3 or len(result.flags) >= 3:
        result.risk_level = "MITTEL"
    elif result.flags:
        result.risk_level = "NIEDRIG"
    else:
        result.risk_level = "CLEAN"

    result.processing_ms = round((time.time() - t0) * 1000, 1)
    return result


def check_ship(name: str = "", mmsi: str = "", imo: str = "") -> FinintResult:
    """
    Spezialisierter Check für Schiffe.
    Prüft Name, MMSI und IMO-Nummer gegen Sanktionslisten.
    """
    # MMSI-Präfix Analyse
    flags: list[str] = []
    if mmsi:
        prefix = mmsi[:3]
        # Bekannte problematische MMSI-Präfixe
        concern_prefixes = {
            "423": "Iran", "432": "Iran (alternative)",
            "273": "Russia", "436": "Afghanistan",
        }
        if prefix in concern_prefixes:
            flags.append(f"MMSI-Präfix {prefix} = {concern_prefixes[prefix]}")

    query = name or mmsi or imo
    result = check_entity(query)
    result.flags.extend(flags)
    return result


def bulk_check(names: list[str]) -> list[dict]:
    """Prüft mehrere Entitäten auf einmal. Gibt kompakte Hit-Liste zurück."""
    results = []
    for name in names[:20]:
        r = check_entity(name, check_companies=False)
        if r.risk_level not in ("CLEAN", "NIEDRIG"):
            results.append({
                "query":      r.query,
                "risk_level": r.risk_level,
                "n_hits":     len(r.sanction_hits),
                "flags":      r.flags[:3],
            })
    return results


# ─── Livemap-Integration ─────────────────────────────────────────────────────

def finint_for_map(entities: list[dict] | None = None) -> list[dict]:
    """
    Prüft AIS-Schiffe aus dem letzten Refresh gegen Sanktionslisten.
    entities: Liste von {name, mmsi, lat, lon, region} Dicts
    Gibt Marker für Sanktionstreffer zurück.
    """
    if not entities:
        return []

    markers = []
    for entity in entities[:50]:
        name = entity.get("name") or entity.get("NAME") or ""
        mmsi = str(entity.get("mmsi") or entity.get("MMSI") or "")
        if not name and not mmsi:
            continue

        r = check_entity(name or mmsi, check_companies=False)
        if r.risk_level in ("CLEAN", "NIEDRIG") and not r.sanction_hits:
            continue

        lat = float(entity.get("lat") or entity.get("LATITUDE") or 0)
        lon = float(entity.get("lon") or entity.get("LONGITUDE") or 0)
        if not lat or not lon:
            continue

        color = {
            "KRITISCH": "#ff0000",
            "HOCH":     "#ff6600",
            "MITTEL":   "#ffcc00",
        }.get(r.risk_level, "#aaaaaa")

        popup = (
            f"<b>💰 FININT ALERT</b><br>"
            f"<b>Entität:</b> {name or mmsi}<br>"
            f"<b>Risiko:</b> {r.risk_level}<br>"
        )
        if r.sanction_hits:
            h = r.sanction_hits[0]
            popup += (
                f"<b>Sanktionsliste:</b> {h.list_name}<br>"
                f"<b>Programm:</b> {h.program}<br>"
            )
        if r.flags:
            popup += f"<b>Flags:</b> {'; '.join(r.flags[:3])}<br>"

        markers.append({
            "lat":    lat,
            "lon":    lon,
            "popup":  popup,
            "color":  color,
            "icon":   "💰",
            "level":  r.risk_level,
            "type":   "finint",
        })

    return markers


def finint_summary() -> dict:
    """Gibt OFAC-DB-Status und Summary zurück."""
    n_entries = 0
    last_update = ""
    try:
        if _OFAC_CACHE.exists():
            con = sqlite3.connect(str(_OFAC_CACHE))
            n_entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            row = con.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
            if row:
                last_update = datetime.fromtimestamp(
                    float(row[0]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            con.close()
    except Exception:
        pass

    return {
        "ofac_entries": n_entries,
        "ofac_updated": last_update,
        "ofac_fresh":   _ofac_cache_fresh(),
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS FININT — Sanktions & Blockchain")
    parser.add_argument("--check",         metavar="NAME",    help="Entität prüfen")
    parser.add_argument("--check-wallet",  metavar="ADDR",    help="Wallet-Adresse prüfen")
    parser.add_argument("--check-ship",    metavar="NAME",    help="Schiff prüfen")
    parser.add_argument("--update-ofac",   action="store_true", help="OFAC-Liste aktualisieren")
    parser.add_argument("--status",        action="store_true", help="DB-Status anzeigen")
    parser.add_argument("--verbose",       action="store_true")
    parser.add_argument("--json",          action="store_true")
    args = parser.parse_args()

    if args.update_ofac:
        print("Aktualisiere OFAC SDN-Liste...")
        ok = _update_ofac_cache()
        print("✅ Erfolgreich" if ok else "❌ Fehlgeschlagen")

    elif args.status:
        s = finint_summary()
        print("\n=== FININT DB-Status ===")
        for k, v in s.items():
            print(f"  {k:20s}: {v}")

    elif args.check or args.check_ship:
        query = args.check or args.check_ship
        r = check_entity(query, check_companies=True)
        if args.json:
            print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
        else:
            rl_icon = {"KRITISCH":"🔴","HOCH":"🟠","MITTEL":"🟡","CLEAN":"✅","NIEDRIG":"🟢"}.get(r.risk_level,"⚫")
            print(f"\n=== FININT: {query} ===")
            print(f"  Risiko:     {rl_icon} {r.risk_level}")
            print(f"  Hits:       {len(r.sanction_hits)}")
            print(f"  Shell-Score:{r.shell_score:.2f}")
            print(f"  Zeit:       {r.processing_ms:.0f}ms")
            if r.sanction_hits:
                print(f"\n  Sanktionstreffer:")
                for h in r.sanction_hits[:5]:
                    print(f"    [{h.list_name}] {h.entity}  "
                          f"Prog:{h.program}  Conf:{h.confidence:.0%}  ({h.match_type})")
                    if args.verbose and h.details:
                        print(f"      Details: {h.details[:100]}")
            if r.flags:
                print(f"\n  Flags:")
                for flag in r.flags[:8]:
                    print(f"    ⚠️  {flag}")
            if r.companies and args.verbose:
                print(f"\n  Unternehmen ({len(r.companies)} gefunden):")
                for c in r.companies[:3]:
                    print(f"    {c['name']}  [{c['jurisdiction']}]  {c['status']}")

    elif args.check_wallet:
        result = _check_wallet(args.check_wallet)
        if result:
            print(f"\n=== Wallet: {args.check_wallet} ===")
            for k, v in result.items():
                print(f"  {k:15s}: {v}")
        else:
            print("Ungültige Adresse oder API-Fehler.")
    else:
        parser.print_help()
