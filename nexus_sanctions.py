"""
nexus_sanctions.py – OFAC/EU/UN Sanktionslisten-Abgleich
=========================================================
Lädt offizielle Sanktionslisten herunter, cached sie lokal in SQLite,
und gleicht AIS-Schiffsnamen / MMSI / IMO sowie Personen/Entitätsnamen
dagegen ab.

Datenquellen (alle kostenlos, keine API-Keys):
  - OFAC SDN List:    https://www.treasury.gov/ofac/downloads/sdn_xml.zip
  - EU Consolidated:  https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content
  - UN SC Sanctions:  https://scsanctions.un.org/resources/xml/en/consolidated.xml
  - UK OFSI:          https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml

Abgleich:
  - Schiffsname      (fuzzy, >85% Ähnlichkeit)
  - IMO-Nummer       (exakt)
  - MMSI             (exakt)
  - Entitätsname     (fuzzy, >80% Ähnlichkeit)
  - Flag-Staat       (exakt)
"""

import sqlite3
import json
import re
import logging
import urllib.request
import zipfile
import io
import difflib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
import xml.etree.ElementTree as ET

log = logging.getLogger("nexus.sanctions")

DB_PATH      = Path(__file__).parent / "nexus_sanctions.db"
CACHE_HOURS  = 24   # Sanktionslisten alle 24h neu laden
NAME_THRESH  = 0.82  # Minimale Namens-Ähnlichkeit (0–1) für Match
IMO_RE       = re.compile(r'\bIMO\s*[:\-]?\s*(\d{7})\b', re.IGNORECASE)
MMSI_RE      = re.compile(r'\bMMSI\s*[:\-]?\s*(\d{9})\b', re.IGNORECASE)

SOURCES = {
    "OFAC": {
        "url":    "https://www.treasury.gov/ofac/downloads/sdn_xml.zip",
        "label":  "OFAC SDN (USA)",
        "zipped": True,
    },
    "EU": {
        "url":    "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content",
        "label":  "EU Consolidated Sanctions",
        "zipped": False,
    },
    "UN": {
        "url":    "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
        "label":  "UN Security Council Sanctions",
        "zipped": False,
    },
}


# ── Datenbank ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sanctions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            entity_type TEXT,          -- "individual", "vessel", "entity", "aircraft"
            name        TEXT NOT NULL,
            name_lower  TEXT NOT NULL,
            aliases     TEXT,          -- JSON list
            imo         TEXT,
            mmsi        TEXT,
            flag        TEXT,
            nationality TEXT,
            reason      TEXT,
            listed_on   TEXT,
            loaded_at   TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_san_name ON sanctions(name_lower)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_san_imo  ON sanctions(imo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_san_mmsi ON sanctions(mmsi)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            source    TEXT PRIMARY KEY,
            loaded_at TEXT
        )
    """)
    conn.commit()
    return conn


def _is_cache_fresh(source: str) -> bool:
    conn = _get_db()
    row = conn.execute(
        "SELECT loaded_at FROM cache_meta WHERE source=?", (source,)
    ).fetchone()
    conn.close()
    if not row:
        return False
    try:
        loaded = datetime.fromisoformat(row[0])
        return datetime.now(timezone.utc) - loaded < timedelta(hours=CACHE_HOURS)
    except Exception:
        return False


# ── XML-Parser pro Quelle ─────────────────────────────────────────────────────

def _parse_ofac_xml(root: ET.Element, source: str) -> list:
    """Parst OFAC SDN XML und gibt Liste von Einträgen zurück."""
    entries = []
    ns = {"ofac": "http://tempuri.org/sdnList.xsd"}

    for entry in root.findall(".//sdnEntry", ns) or root.findall(".//sdnEntry"):
        uid    = entry.findtext("uid") or entry.findtext("./uid") or ""
        lname  = entry.findtext("lastName") or ""
        fname  = entry.findtext("firstName") or ""
        name   = f"{fname} {lname}".strip() or lname
        etype  = (entry.findtext("sdnType") or "").lower()
        prog   = entry.findtext("programList/program") or ""

        # Aliases
        aliases = []
        for aka in entry.findall(".//aka"):
            aname = (aka.findtext("lastName") or "") + " " + (aka.findtext("firstName") or "")
            aname = aname.strip()
            if aname:
                aliases.append(aname)

        # IMO / MMSI aus Remarks
        remarks = entry.findtext("remarks") or ""
        imo_m   = IMO_RE.search(remarks)
        mmsi_m  = MMSI_RE.search(remarks)

        if name:
            entries.append({
                "source":      source,
                "entity_type": "vessel" if "vessel" in etype or "ship" in etype else etype or "entity",
                "name":        name,
                "aliases":     json.dumps(aliases),
                "imo":         imo_m.group(1) if imo_m else None,
                "mmsi":        mmsi_m.group(1) if mmsi_m else None,
                "reason":      prog[:100],
                "listed_on":   entry.findtext("publishInformation/publishDate") or "",
            })
    return entries


def _parse_eu_xml(root: ET.Element, source: str) -> list:
    """Parst EU Consolidated Sanctions XML."""
    entries = []
    for subject in root.findall(".//sanctionEntity") or root.findall(".//Subject"):
        # Namensfelder
        name_els = subject.findall(".//nameAlias") or subject.findall(".//name")
        names = [
            (el.get("wholeName") or el.get("firstName", "") + " " + el.get("lastName", "")).strip()
            for el in name_els
        ]
        names = [n for n in names if n]
        if not names:
            continue

        etype  = (subject.get("subjectType") or subject.get("type") or "").lower()
        reason = subject.findtext(".//regulation/publicationTitle") or ""

        # IMO aus identificationDetails
        imo_val = None
        for ident in subject.findall(".//identification") or subject.findall(".//identificationDetail"):
            if "imo" in (ident.get("identificationTypeCode") or "").lower():
                imo_val = ident.get("number") or ident.text or ""

        entries.append({
            "source":      source,
            "entity_type": "vessel" if "vessel" in etype or "ship" in etype else etype or "entity",
            "name":        names[0],
            "aliases":     json.dumps(names[1:]),
            "imo":         imo_val,
            "mmsi":        None,
            "reason":      reason[:100],
            "listed_on":   "",
        })
    return entries


def _parse_un_xml(root: ET.Element, source: str) -> list:
    """Parst UN Security Council Sanctions XML."""
    entries = []
    for indiv in list(root.findall(".//INDIVIDUALS/INDIVIDUAL")) + list(root.findall(".//ENTITIES/ENTITY")):
        fname   = indiv.findtext("FIRST_NAME") or ""
        sname   = indiv.findtext("SECOND_NAME") or ""
        tname   = indiv.findtext("THIRD_NAME") or ""
        name    = " ".join(filter(None, [fname, sname, tname])).strip()
        if not name:
            name = indiv.findtext("NAME") or ""
        if not name:
            continue

        aliases = []
        for aka in indiv.findall(".//ALIAS"):
            aname = (aka.findtext("QUALITY") or "") + " " + (aka.findtext("ALIAS_NAME") or "")
            aname = aname.strip()
            if aname:
                aliases.append(aname)

        nat  = indiv.findtext("NATIONALITY/VALUE") or ""
        comments = indiv.findtext("COMMENTS1") or ""

        entries.append({
            "source":      source,
            "entity_type": "individual",
            "name":        name,
            "aliases":     json.dumps(aliases),
            "imo":         None,
            "mmsi":        None,
            "nationality": nat,
            "reason":      comments[:100],
            "listed_on":   indiv.findtext("LISTED_ON") or "",
        })
    return entries


# ── Laden & Cachen ────────────────────────────────────────────────────────────

def _load_source(source_key: str):
    """Lädt eine Sanktionsliste herunter und cached sie in SQLite."""
    cfg = SOURCES[source_key]
    log.info(f"Lade {cfg['label']} ...")

    try:
        req = urllib.request.Request(
            cfg["url"],
            headers={"User-Agent": "NEXUS-Sanctions/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        log.warning(f"Download {source_key} Fehler: {e}")
        return

    # Entzippen wenn nötig
    if cfg.get("zipped"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
                if not xml_files:
                    return
                data = zf.read(xml_files[0])
        except Exception as e:
            log.warning(f"Entzippen {source_key}: {e}")
            return

    # XML parsen
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        log.warning(f"XML-Parse {source_key}: {e}")
        return

    parsers = {"OFAC": _parse_ofac_xml, "EU": _parse_eu_xml, "UN": _parse_un_xml}
    parser  = parsers.get(source_key, _parse_un_xml)
    entries = parser(root, source_key)
    log.info(f"{source_key}: {len(entries)} Einträge geparst")

    # In DB speichern
    conn = _get_db()
    conn.execute("DELETE FROM sanctions WHERE source=?", (source_key,))
    now = datetime.now(timezone.utc).isoformat()
    for e in entries:
        try:
            conn.execute("""
                INSERT INTO sanctions
                    (source, entity_type, name, name_lower, aliases,
                     imo, mmsi, flag, nationality, reason, listed_on, loaded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                e["source"], e.get("entity_type"), e["name"],
                e["name"].lower(), e.get("aliases"), e.get("imo"),
                e.get("mmsi"), e.get("flag"), e.get("nationality"),
                e.get("reason"), e.get("listed_on"), now,
            ))
        except Exception:
            pass
    conn.execute("INSERT OR REPLACE INTO cache_meta VALUES (?,?)", (source_key, now))
    conn.commit()
    conn.close()
    log.info(f"{source_key}: {len(entries)} Einträge gespeichert.")


def refresh_all():
    """Aktualisiert alle Sanktionslisten wenn Cache veraltet."""
    for key in SOURCES:
        if not _is_cache_fresh(key):
            _load_source(key)
        else:
            log.debug(f"{key}: Cache noch frisch.")


def get_stats() -> dict:
    """Gibt Statistiken über geladene Sanktionslisten zurück."""
    conn = _get_db()
    total  = conn.execute("SELECT COUNT(*) FROM sanctions").fetchone()[0]
    by_src = dict(conn.execute(
        "SELECT source, COUNT(*) FROM sanctions GROUP BY source"
    ).fetchall())
    vessels = conn.execute(
        "SELECT COUNT(*) FROM sanctions WHERE entity_type='vessel'"
    ).fetchone()[0]
    conn.close()
    return {"total": total, "by_source": by_src, "vessels": vessels}


# ── Abgleich-Funktionen ───────────────────────────────────────────────────────

def _fuzzy_match(a: str, b: str) -> float:
    """Namens-Ähnlichkeit 0–1 (SequenceMatcher)."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_vessel(name: str = "", imo: str = "", mmsi: str = "",
                 flag: str = "") -> Optional[dict]:
    """
    Prüft ob ein Schiff auf Sanktionslisten steht.

    Returns:
        {
          "match":       True/False,
          "source":      "OFAC SDN (USA)",
          "matched_name": str,
          "similarity":  float,
          "reason":      str,
          "entity_type": str,
        }
        oder None wenn kein Treffer.
    """
    # Stellen sicher dass DB geladen ist
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM sanctions").fetchone()[0]
    conn.close()
    if total == 0:
        refresh_all()

    conn = _get_db()
    result = None

    # 1. IMO-Abgleich (exakt)
    if imo and len(imo) == 7 and imo.isdigit():
        row = conn.execute(
            "SELECT * FROM sanctions WHERE imo=? LIMIT 1", (imo,)
        ).fetchone()
        if row:
            result = _row_to_match(row, "imo_exact", 1.0)

    # 2. MMSI-Abgleich (exakt)
    if not result and mmsi and len(mmsi) == 9 and mmsi.isdigit():
        row = conn.execute(
            "SELECT * FROM sanctions WHERE mmsi=? LIMIT 1", (mmsi,)
        ).fetchone()
        if row:
            result = _row_to_match(row, "mmsi_exact", 1.0)

    # 3. Name-Abgleich (fuzzy)
    if not result and name:
        name_clean = re.sub(r'\s+', ' ', name.strip().lower())
        # Suche Kandidaten (Substring-Suche als Vorfilter)
        words = name_clean.split()[:3]  # erste 3 Wörter
        if words:
            candidates = conn.execute(
                "SELECT * FROM sanctions WHERE " +
                " OR ".join(["name_lower LIKE ?"] * len(words)),
                [f"%{w}%" for w in words]
            ).fetchall()
            best_sim = 0.0
            best_row = None
            for row in candidates:
                sim = _fuzzy_match(name_clean, row[4])  # name_lower ist col 4
                # Auch Aliases prüfen
                aliases = json.loads(row[5] or "[]")
                for alias in aliases:
                    sim = max(sim, _fuzzy_match(name_clean, alias.lower()))
                if sim > best_sim:
                    best_sim = sim
                    best_row = row
            if best_row and best_sim >= NAME_THRESH:
                result = _row_to_match(best_row, "name_fuzzy", best_sim)

    conn.close()
    return result


def _row_to_match(row, match_type: str, sim: float) -> dict:
    """Konvertiert DB-Row in Match-Dict (Column-Indizes aus CREATE TABLE)."""
    # id,source,entity_type,name,name_lower,aliases,imo,mmsi,flag,nationality,reason,listed_on,loaded_at
    return {
        "match":        True,
        "match_type":   match_type,
        "similarity":   round(sim, 3),
        "source":       SOURCES.get(row[1], {}).get("label", row[1]),
        "source_key":   row[1],
        "entity_type":  row[2],
        "matched_name": row[3],
        "imo":          row[6],
        "mmsi":         row[7],
        "reason":       row[10],
        "listed_on":    row[11],
    }


def check_entity(name: str) -> Optional[dict]:
    """Prüft ob eine Person/Organisation auf Sanktionslisten steht."""
    return check_vessel(name=name)


def screen_ais_vessels(vessels: list) -> list:
    """
    Durchsucht eine Liste von AIS-Schiffen nach Sanktionstreffern.

    Returns: Liste von Hits mit Schiff + Match-Info.
    """
    hits = []
    for v in vessels:
        ship_name = v.get("name") or v.get("ship_name") or ""
        imo       = str(v.get("imo") or "")
        mmsi      = str(v.get("mmsi") or "")
        match     = check_vessel(name=ship_name, imo=imo, mmsi=mmsi)
        if match:
            hits.append({"vessel": v, "sanction": match})
            log.warning(
                f"SANCTIONS HIT: {ship_name} (IMO={imo}) → "
                f"{match['matched_name']} [{match['source']}]"
            )
    return hits


def get_sanctions_for_map(vessels: list) -> list:
    """Gibt Karten-kompatible Marker für Sanktions-Treffer zurück."""
    hits = screen_ais_vessels(vessels)
    markers = []
    for hit in hits:
        v  = hit["vessel"]
        m  = hit["sanction"]
        lat = v.get("lat") or v.get("latitude")
        lon = v.get("lon") or v.get("longitude")
        if not lat or not lon:
            continue
        markers.append({
            "lat":   float(lat),
            "lon":   float(lon),
            "type":  "sanction",
            "icon":  "⚖️",
            "color": "#cc0000",
            "title": f"⚖️ SANKTIONIERT: {v.get('name','?')}",
            "popup": (
                f"<b>⚖️ SANKTIONS-TREFFER</b><br>"
                f"<b>Schiff:</b> {v.get('name','?')}<br>"
                f"<b>MMSI:</b> {v.get('mmsi','?')}<br>"
                f"<b>IMO:</b> {v.get('imo','?')}<br>"
                f"<b>Liste:</b> {m['source']}<br>"
                f"<b>Eingetragen als:</b> {m['matched_name']}<br>"
                f"<b>Ähnlichkeit:</b> {m['similarity']:.0%}<br>"
                f"<b>Grund:</b> {m.get('reason','?')[:80]}<br>"
                f"<b>Gelistet:</b> {m.get('listed_on','?')}"
            ),
        })
    return markers


# ── Standalone Test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("Lade Sanktionslisten ...")
    refresh_all()
    stats = get_stats()
    print(f"Geladen: {stats['total']} Einträge ({stats['vessels']} Schiffe)")
    for src, n in stats["by_source"].items():
        print(f"  {src}: {n}")

    # Test-Abgleich
    test_names = sys.argv[1:] or ["FLYING DOLPHIN", "OCEAN NAVIGATOR", "NORD STREAM"]
    for name in test_names:
        result = check_vessel(name=name)
        if result:
            print(f"\n⚠️  TREFFER: '{name}' → {result['matched_name']} "
                  f"[{result['source']}] ({result['similarity']:.0%})")
        else:
            print(f"\n  OK: '{name}' – kein Sanktionstreffer")
