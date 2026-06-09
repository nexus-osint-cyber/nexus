"""
nexus_knowledge.py  –  Persistenter Wissens-Graph (Entity-Tracker)

Problem:
  Jede NEXUS-Session ist gedaechtnislos.
  "Einheit X" tauchte gestern in Avdiivka auf, heute in Selydove –
  aber NEXUS hat keine Ahnung, dass das dieselbe Einheit ist, dass sie
  sich 40 km nach Sueden bewegt hat, und dass das ein Durchbruch bedeutet.

Loesung:
  SQLite-Datenbank die Entitaeten ueber Sessionen hinweg trackt:
    - Militaereinheiten    (Brigaden, Bataillone, Regimenter)
    - Schluesselorte       (Staedte, Doerfer, Huegel, Bruecken)
    - Schluessel-Personen  (Kommandeure, Minister, Sprecher)
    - Waffensysteme        (Erste Sichtung, Einsatzgebiet)
    - Wiederkehrende Events (Angriffsmuster, Eskalations-Cluster)

Was das bringt:
  - "Einheit X: zuletzt gesehen A -> B -> C, Bewegungsrichtung Nord"
  - "Ort Y: 3 Angriffe in 48h, Eskalationstrend +40%"
  - "Person Z: 4. Mal in 7 Tagen erwaehnt, Zusammenhang: Gegenoffensive"
  - LLM bekommt HISTORISCHEN KONTEXT statt nur aktueller Snapshot

Schema:
  entities       – Entitaeten-Katalog (Name, Typ, Aliases, Metadaten)
  observations   – Jede Erwaehnung mit Quelle, Zeit, Ort, Kontext
  movements      – Abgeleitete Bewegungen (A -> B) mit Richtung und Distanz
  entity_links   – Beziehungen zwischen Entitaeten (COMMANDED_BY, PART_OF, ...)

NER-Lite (keine externen Deps):
  Regex-basierte Extraktion von Militaereinheiten, Waffensystemen,
  Ortsangaben, Personen aus Artikeltexten.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

# ── Datenbank-Pfad ────────────────────────────────────────────────────────────
_DB_DIR  = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_DB_DIR, "nexus_knowledge.db")


# ============================================================
# SCHEMA
# ============================================================

_SCHEMA = """
PRAGMA journal_mode=MEMORY;
PRAGMA synchronous=OFF;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL,   -- UNIT | LOCATION | PERSON | WEAPON | EVENT
    aliases     TEXT    DEFAULT '[]',  -- JSON array
    first_seen  REAL    NOT NULL,
    last_seen   REAL    NOT NULL,
    obs_count   INTEGER DEFAULT 1,
    metadata    TEXT    DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       INTEGER NOT NULL REFERENCES entities(id),
    source          TEXT,
    article_title   TEXT,
    article_url     TEXT,
    observed_at     REAL    NOT NULL,   -- epoch
    location_name   TEXT,
    lat             REAL,
    lon             REAL,
    context_snippet TEXT,
    confidence      TEXT,
    created_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_entity ON observations(entity_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_time   ON observations(observed_at);

CREATE TABLE IF NOT EXISTS movements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id       INTEGER NOT NULL REFERENCES entities(id),
    from_location   TEXT,
    to_location     TEXT,
    from_lat        REAL,
    from_lon        REAL,
    to_lat          REAL,
    to_lon          REAL,
    observed_at     REAL    NOT NULL,
    direction_deg   REAL,
    direction_label TEXT,
    distance_km     REAL,
    source          TEXT
);
CREATE INDEX IF NOT EXISTS ix_mov_entity ON movements(entity_id, observed_at);

CREATE TABLE IF NOT EXISTS entity_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id     INTEGER NOT NULL REFERENCES entities(id),
    to_id       INTEGER NOT NULL REFERENCES entities(id),
    rel_type    TEXT    NOT NULL,   -- COMMANDED_BY | PART_OF | DEPLOYED_TO | ALLY | ENEMY
    confidence  REAL    DEFAULT 0.5,
    first_seen  REAL    NOT NULL,
    last_seen   REAL    NOT NULL,
    source      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_links ON entity_links(from_id, to_id, rel_type);
"""


# ============================================================
# DATENBANK
# ============================================================

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False,
                                isolation_level=None)
        _conn.row_factory = sqlite3.Row
        # NTFS/SMB-kompatibler Modus (kein WAL, kein fsync)
        _conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        _conn.execute("PRAGMA journal_mode=MEMORY")
        _conn.execute("PRAGMA synchronous=OFF")
        _conn.execute("PRAGMA foreign_keys=ON")
        # Tabellen anlegen
        for stmt in _SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("PRAGMA"):
                try:
                    _conn.execute(stmt)
                except Exception:
                    pass
    return _conn


def _now() -> float:
    return time.time()


# ============================================================
# NER-LITE  (Entity-Extraktion ohne externe Bibliotheken)
# ============================================================

# Militaereinheiten: ukrainisch + russisch
_RE_UNIT = re.compile(
    r"""
    (?:
        # Brigade / Battalion / Regiment / Division + Nummer/Name
        (?:\d{1,3}(?:st|nd|rd|th)?\s+)?
        (?:
            (?:Separate\s+)?
            (?:
                Motor(?:ized|ised)?\s+Rifle|
                Tank|Armou?red|Infantry|
                Air(?:\s*Assault|borne)|
                Special\s+Forces?|
                Mountain|
                Assault|
                Artillery|
                Marine|
                Guard(?:s)?|
                Mechanized|
                Motorized|
                Storm|
                Naval|
                Coastal\s+Defense
            )\s+
            (?:Brigade|Battalion|Regiment|Division|Corps|Army|Group|Unit|Company|Platoon|Detachment)
        )
        |
        # Russisch
        (?:\d{1,3}\s*(?:ya|aya|ogo|th|st|nd|rd)?\s*)?
        (?:
            Motostrelkovaya|Tankovaya|Desantnaya|
            GRU|FSB|Rosgvardiya|
            VDV|VMF|VKS
        )
        (?:\s+(?:Brigade|Brigada|Polk|Battalion|Bataillon|Polku|Gruppa))?
        |
        # Numerische Einheiten
        \d{2,5}(?:st|nd|rd|th)?\s+(?:Brigade|Battalion|Regiment|Division|Company|Platoon)
        |
        # Bekannte Named Units
        (?:
            Azov|Aidar|Dnipro|Territorial\s+Defense|
            Wagner|Kadyrovite|Chechen|
            SOBR|OMON|Spetsnaz|Alpha|Vympel|
            Georgian\s+Legion|Foreign\s+Legion|
            International\s+Legion|Kraken
        )(?:\s+(?:Unit|Group|Battalion|Brigade|Regiment|Forces?))?
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Wichtige Ukraine-Orte (erweiterbar)
_UKRAINE_LOCATIONS = {
    # Frontgebiete
    "Bakhmut", "Avdiivka", "Chasiv Yar", "Toretsk", "Pokrovsk",
    "Selydove", "Kurakhove", "Vuhledar", "Marinka", "Pervomaiske",
    "Robotyne", "Verbove", "Orikhiv", "Zaporizhzhia",
    "Kherson", "Nova Kakhovka", "Melitopol", "Berdiansk",
    "Mariupol", "Volnovakha", "Donetsk", "Luhansk",
    "Kramatorsk", "Sloviansk", "Kostiantynivka", "Lyman",
    "Siversk", "Bilohorivka", "Kreminna", "Svatove",
    "Kupyansk", "Izium", "Kharkiv", "Sumy",
    # Grenzgebiete Russland
    "Belgorod", "Bryansk", "Kursk", "Shebekino",
    # Hinterland
    "Kyiv", "Lviv", "Dnipro", "Odesa", "Mykolaiv",
    "Poltava", "Chernihiv", "Vinnytsia", "Zhytomyr",
    # Krim
    "Crimea", "Sevastopol", "Simferopol", "Kerch",
    # Russische Ziele
    "Bryansk", "Rostov", "Pskov", "Engels",
}

# Ortserkennung: bekannte Namen + kapitalisierte Woerter nach location-Keywords
_RE_LOCATION_KEYWORD = re.compile(
    r"""
    (?:
        (?:in|near|at|outside|around|north of|south of|east of|west of|
           towards?|from|to|seized?|captured?|liberated?|controlled?\s+by|
           fell\s+to|advance(?:d|s)?\s+(?:on|to|towards?|near)|
           withdraws?\s+from|retreated?\s+from|shelling\s+(?:in|of|near))\s+
        ([A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\-]+){0,2})
    )
    """,
    re.VERBOSE,
)

# Waffensysteme
_WEAPON_PATTERNS = [
    # Panzer
    r"\bT-(?:54|55|62|64|72|80|90|90M|14)\b",
    r"\bLeopard\s*(?:1|2|2A4|2A5|2A6)?\b",
    r"\bAbrams\b", r"\bChallenger\s*(?:2)?\b",
    r"\bBradley\b", r"\bMarder\b", r"\bCV-90\b",
    # Artillerie
    r"\bHIMARS\b", r"\bM777\b", r"\bPzH\s*2000\b", r"\bAS-90\b",
    r"\bATACMS\b", r"\bStorm\s+Shadow\b", r"\bScalp\b",
    r"\bGrad\b", r"\bBuratino\b", r"\bUragan\b", r"\bTornado\b",
    r"\bTOS-1\b",
    # Drohnen
    r"\bShahed(?:-\d+)?\b", r"\bGeran(?:-\d+)?\b",
    r"\bBayraktar\s*TB2\b", r"\bLancet(?:-\d+)?\b",
    r"\bOrlan(?:-\d+)?\b",
    r"\bUAV\b", r"\bFPV\s+drone\b",
    # Missiles
    r"\bKalibr\b", r"\bKinzhal\b", r"\bZircon\b",
    r"\bIskander(?:-M|-K)?\b", r"\bKh-\d+\b",
    r"\bNeptune\b", r"\bHrim-2\b",
    # Flugabwehr
    r"\bPatriot\b", r"\bNASAMS\b", r"\bIRIS-T\b",
    r"\bHawk\b", r"\bBuk(?:-M\d)?\b", r"\bS-\d+(?:00|0)\b",
    # Leichtbewaffnet
    r"\bJavelin\b", r"\bNLAW\b", r"\bAT-4\b", r"\bRPG(?:-\d+)?\b",
    r"\bStinger\b", r"\bMAN-PAD\b",
    # Schiffe
    r"\bMoskvaa?\b", r"\bPavlovsk\b",
    r"\bSubmarines?\b", r"\bLanding\s+(?:ship|craft)\b",
]
_RE_WEAPON = re.compile("|".join(_WEAPON_PATTERNS), re.IGNORECASE)

# Personen: Zelensky, Putin, Zaluzhny, etc.
_KEY_PERSONS = {
    "Zelensky", "Zelenskyy", "Zelenskiy",
    "Putin", "Mishustin", "Patrushev", "Gerasimov", "Shoigu", "Belousov",
    "Zaluzhny", "Syrsky", "Budanov", "Tarnavsky",
    "Blinken", "Austin", "Sullivan", "Biden", "Trump",
    "Stoltenberg", "Macron", "Scholz", "Sunak", "Baerbock",
    "Prigozhin", "Kadyrov",
}
_RE_PERSON = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _KEY_PERSONS) + r")\b",
    re.IGNORECASE,
)


def _extract_entities_from_text(title: str, text: str) -> list[dict]:
    """
    NER-Lite: Extrahiert Entitaeten aus Titel + Text.
    Gibt Liste von {name, type, context} zurueck.
    """
    combined = (title or "") + " " + (text or "")[:2000]
    found: list[dict] = []
    seen: set[str] = set()

    def _add(name: str, etype: str, ctx: str) -> None:
        key = name.lower().strip()
        if key and key not in seen and len(key) > 2:
            seen.add(key)
            found.append({"name": name.strip(), "type": etype, "context": ctx[:200]})

    # Militaereinheiten
    for m in _RE_UNIT.finditer(combined):
        snippet = combined[max(0, m.start()-60):m.end()+60]
        _add(m.group(0).strip(), "UNIT", snippet)

    # Waffensysteme
    for m in _RE_WEAPON.finditer(combined):
        snippet = combined[max(0, m.start()-60):m.end()+60]
        _add(m.group(0).strip(), "WEAPON", snippet)

    # Bekannte Orte
    for loc in _UKRAINE_LOCATIONS:
        if re.search(r"\b" + re.escape(loc) + r"\b", combined, re.IGNORECASE):
            idx = combined.lower().find(loc.lower())
            snippet = combined[max(0, idx-60):idx+60] if idx >= 0 else ""
            _add(loc, "LOCATION", snippet)

    # Orte aus Kontext-Keywords
    for m in _RE_LOCATION_KEYWORD.finditer(combined):
        loc = m.group(1).strip()
        if 3 < len(loc) < 40 and loc not in seen:
            _add(loc, "LOCATION", combined[max(0, m.start()-40):m.end()+40])

    # Personen
    for m in _RE_PERSON.finditer(combined):
        snippet = combined[max(0, m.start()-60):m.end()+60]
        _add(m.group(1).strip(), "PERSON", snippet)

    return found


# ============================================================
# DATENBANK-OPERATIONEN
# ============================================================

def _upsert_entity(db: sqlite3.Connection, name: str, etype: str,
                   observed_at: float) -> int:
    """Holt oder erstellt Entitaet, gibt entity_id zurueck."""
    cur = db.execute("SELECT id, last_seen, obs_count FROM entities WHERE name=?",
                     (name,))
    row = cur.fetchone()
    if row:
        if observed_at > row["last_seen"]:
            db.execute(
                "UPDATE entities SET last_seen=?, obs_count=obs_count+1 WHERE id=?",
                (observed_at, row["id"]),
            )
        return row["id"]
    else:
        cur = db.execute(
            "INSERT INTO entities(name, type, first_seen, last_seen, obs_count) "
            "VALUES (?,?,?,?,1)",
            (name, etype, observed_at, observed_at),
        )
        return cur.lastrowid


def _insert_observation(db: sqlite3.Connection, entity_id: int,
                        source: str, title: str, url: str,
                        observed_at: float, location: str,
                        lat: Optional[float], lon: Optional[float],
                        context: str, confidence: str) -> None:
    # Duplikat-Check: gleiche Quelle + gleicher Artikel + gleiche Entitaet
    cur = db.execute(
        "SELECT id FROM observations WHERE entity_id=? AND source=? AND article_title=? "
        "AND ABS(observed_at - ?) < 300",
        (entity_id, source, title, observed_at),
    )
    if cur.fetchone():
        return
    db.execute(
        "INSERT INTO observations(entity_id, source, article_title, article_url, "
        "observed_at, location_name, lat, lon, context_snippet, confidence, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (entity_id, source, title, url, observed_at, location,
         lat, lon, context, confidence, _now()),
    )


def _derive_movement(db: sqlite3.Connection, entity_id: int) -> None:
    """
    Leitet Bewegung ab wenn Einheit bei zwei verschiedenen Orten
    mit GPS in zeitlicher Folge gesehen wurde.
    """
    cur = db.execute(
        """SELECT location_name, lat, lon, observed_at, source
           FROM observations
           WHERE entity_id=? AND lat IS NOT NULL AND lon IS NOT NULL
             AND location_name IS NOT NULL AND location_name != ''
           ORDER BY observed_at DESC
           LIMIT 10""",
        (entity_id,),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return

    newest = rows[0]
    for prev in rows[1:]:
        if prev["location_name"] == newest["location_name"]:
            continue
        if prev["lat"] is None or prev["lon"] is None:
            continue

        # Bearing + Distanz berechnen
        lat1, lon1 = math.radians(prev["lat"]), math.radians(prev["lon"])
        lat2, lon2 = math.radians(newest["lat"]), math.radians(newest["lon"])
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        # Haversine
        a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        dist_km = 6371 * 2 * math.asin(math.sqrt(a))

        # Kompassrichtung
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
        bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
        dirs = ["N","NNO","NO","ONO","O","OSO","SO","SSO",
                "S","SSW","SW","WSW","W","WNW","NW","NNW"]
        label = dirs[int((bearing + 11.25) / 22.5) % 16]

        # Bereits vorhanden?
        ex = db.execute(
            "SELECT id FROM movements WHERE entity_id=? AND from_location=? AND to_location=?",
            (entity_id, prev["location_name"], newest["location_name"]),
        )
        if ex.fetchone():
            break

        db.execute(
            "INSERT INTO movements(entity_id, from_location, to_location, "
            "from_lat, from_lon, to_lat, to_lon, observed_at, "
            "direction_deg, direction_label, distance_km, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, prev["location_name"], newest["location_name"],
             prev["lat"], prev["lon"], newest["lat"], newest["lon"],
             newest["observed_at"], bearing, label, dist_km, newest["source"]),
        )
        break  # nur neueste Bewegung ableiten


# ============================================================
# HAUPT-API
# ============================================================

def ingest_articles(articles: list[dict]) -> dict:
    """
    Verarbeitet eine Artikelliste und speichert alle Entitaeten
    mit ihren Beobachtungen in der SQLite-Datenbank.

    Gibt Statistiken zurueck: {new_entities, new_observations, ...}
    """
    if not articles:
        return {"new_entities": 0, "new_observations": 0, "updated_entities": 0}

    db = _get_conn()
    new_ent = 0
    new_obs = 0
    upd_ent = 0

    for art in articles:
        title      = art.get("title", "")
        text       = art.get("summary", art.get("text", ""))
        source     = art.get("source", "")
        url        = art.get("url", art.get("link", ""))
        confidence = art.get("conf_badge", art.get("confidence", ""))
        age_min    = art.get("age_min", 0)
        lat        = art.get("lat") or art.get("latitude")
        lon        = art.get("lon") or art.get("longitude")
        observed_at = _now() - (age_min or 0) * 60

        # NER
        entities = _extract_entities_from_text(title, text)
        if not entities:
            continue

        # Hauptort aus Artikel (fuer Einheiten ohne eigene GPS)
        article_location = None
        for e in entities:
            if e["type"] == "LOCATION":
                article_location = e["name"]
                break

        for ent in entities:
            # Vor-Update pruefen ob Entitaet neu
            cur = db.execute("SELECT id FROM entities WHERE name=?", (ent["name"],))
            existed = cur.fetchone() is not None

            eid = _upsert_entity(db, ent["name"], ent["type"], observed_at)

            if not existed:
                new_ent += 1
            else:
                upd_ent += 1

            # Ort: bei UNIT/WEAPON → Ort aus NER; bei LOCATION → selbst
            obs_location = ent["name"] if ent["type"] == "LOCATION" else article_location
            obs_lat = lat if ent["type"] in ("UNIT", "WEAPON") else None
            obs_lon = lon if ent["type"] in ("UNIT", "WEAPON") else None

            _insert_observation(
                db, eid, source, title, url, observed_at,
                obs_location, obs_lat, obs_lon,
                ent["context"], confidence,
            )
            new_obs += 1

            # Bewegungsableitung nur fuer Einheiten
            if ent["type"] == "UNIT":
                _derive_movement(db, eid)

    db.commit()
    return {
        "new_entities":    new_ent,
        "new_observations": new_obs,
        "updated_entities": upd_ent,
    }


def get_entity_timeline(name: str, limit: int = 20) -> list[dict]:
    """
    Gibt Zeitline einer Entitaet zurueck (neueste zuerst).
    """
    db = _get_conn()
    cur = db.execute("SELECT id FROM entities WHERE name=? COLLATE NOCASE", (name,))
    row = cur.fetchone()
    if not row:
        # Fuzzy: Teilstring
        cur = db.execute(
            "SELECT id, name FROM entities WHERE name LIKE ? LIMIT 5",
            (f"%{name}%",),
        )
        rows = cur.fetchall()
        if not rows:
            return []
        row = rows[0]

    eid = row["id"]
    cur = db.execute(
        """SELECT source, article_title, observed_at, location_name, lat, lon,
                  context_snippet, confidence
           FROM observations
           WHERE entity_id=?
           ORDER BY observed_at DESC LIMIT ?""",
        (eid, limit),
    )
    result = []
    for r in cur.fetchall():
        ts = datetime.fromtimestamp(r["observed_at"], tz=timezone.utc)
        result.append({
            "source":    r["source"],
            "title":     r["article_title"],
            "when":      ts.strftime("%d.%m. %H:%M"),
            "when_epoch": r["observed_at"],
            "location":  r["location_name"],
            "lat":       r["lat"],
            "lon":       r["lon"],
            "context":   r["context_snippet"],
            "confidence": r["confidence"],
        })
    return result


def get_entity_movements(name: str) -> list[dict]:
    """Gibt Bewegungspfad einer Einheit zurueck."""
    db = _get_conn()
    cur = db.execute(
        "SELECT id FROM entities WHERE name=? COLLATE NOCASE", (name,)
    )
    row = cur.fetchone()
    if not row:
        cur = db.execute(
            "SELECT id FROM entities WHERE name LIKE ? LIMIT 1",
            (f"%{name}%",),
        )
        row = cur.fetchone()
    if not row:
        return []
    eid = row["id"]
    cur = db.execute(
        """SELECT from_location, to_location, from_lat, from_lon,
                  to_lat, to_lon, observed_at, direction_label, distance_km, source
           FROM movements WHERE entity_id=?
           ORDER BY observed_at ASC""",
        (eid,),
    )
    result = []
    for r in cur.fetchall():
        ts = datetime.fromtimestamp(r["observed_at"], tz=timezone.utc)
        result.append({
            "from":       r["from_location"],
            "to":         r["to_location"],
            "direction":  r["direction_label"],
            "distance_km": round(r["distance_km"] or 0, 1),
            "when":       ts.strftime("%d.%m."),
            "source":     r["source"],
        })
    return result


def get_active_entities(hours: float = 48.0,
                        etype: Optional[str] = None) -> list[dict]:
    """
    Gibt alle Entitaeten zurueck die in den letzten N Stunden erwaehnt wurden.
    etype: 'UNIT' | 'LOCATION' | 'PERSON' | 'WEAPON' | None = alle
    """
    db = _get_conn()
    since = _now() - hours * 3600
    if etype:
        cur = db.execute(
            """SELECT e.id, e.name, e.type, e.obs_count, e.last_seen,
                      COUNT(o.id) as recent_obs
               FROM entities e
               JOIN observations o ON o.entity_id=e.id
               WHERE o.observed_at >= ? AND e.type=?
               GROUP BY e.id ORDER BY recent_obs DESC LIMIT 50""",
            (since, etype),
        )
    else:
        cur = db.execute(
            """SELECT e.id, e.name, e.type, e.obs_count, e.last_seen,
                      COUNT(o.id) as recent_obs
               FROM entities e
               JOIN observations o ON o.entity_id=e.id
               WHERE o.observed_at >= ?
               GROUP BY e.id ORDER BY recent_obs DESC LIMIT 80""",
            (since,),
        )
    result = []
    for r in cur.fetchall():
        ts = datetime.fromtimestamp(r["last_seen"], tz=timezone.utc)
        result.append({
            "id":         r["id"],
            "name":       r["name"],
            "type":       r["type"],
            "obs_count":  r["obs_count"],
            "recent_obs": r["recent_obs"],
            "last_seen":  ts.strftime("%d.%m. %H:%M"),
        })
    return result


def get_hotspots(hours: float = 24.0, min_obs: int = 2) -> list[dict]:
    """
    Orte mit haeufigsten Beobachtungen = potenzielle Hotspots.
    """
    db = _get_conn()
    since = _now() - hours * 3600
    cur = db.execute(
        """SELECT location_name, COUNT(*) as cnt,
                  MAX(observed_at) as latest,
                  GROUP_CONCAT(DISTINCT source) as sources
           FROM observations
           WHERE observed_at >= ? AND location_name IS NOT NULL
             AND location_name != ''
           GROUP BY location_name
           HAVING cnt >= ?
           ORDER BY cnt DESC
           LIMIT 20""",
        (since, min_obs),
    )
    result = []
    for r in cur.fetchall():
        ts = datetime.fromtimestamp(r["latest"], tz=timezone.utc)
        result.append({
            "location": r["location_name"],
            "obs_count": r["cnt"],
            "last_seen": ts.strftime("%d.%m. %H:%M"),
            "sources":   r["sources"] or "",
        })
    return result


def get_db_stats() -> dict:
    """Datenbank-Statistiken."""
    db = _get_conn()
    stats = {}
    for table in ("entities", "observations", "movements"):
        cur = db.execute(f"SELECT COUNT(*) as c FROM {table}")
        stats[table] = cur.fetchone()["c"]
    # Nach Typ
    cur = db.execute("SELECT type, COUNT(*) as c FROM entities GROUP BY type")
    stats["by_type"] = {r["type"]: r["c"] for r in cur.fetchall()}
    # Aeltester + neuester Eintrag
    cur = db.execute(
        "SELECT MIN(observed_at) as mn, MAX(observed_at) as mx FROM observations"
    )
    row = cur.fetchone()
    if row and row["mn"]:
        mn = datetime.fromtimestamp(row["mn"], tz=timezone.utc)
        mx = datetime.fromtimestamp(row["mx"], tz=timezone.utc)
        stats["oldest"] = mn.strftime("%d.%m.%Y %H:%M")
        stats["newest"] = mx.strftime("%d.%m.%Y %H:%M")
    return stats


# ============================================================
# LLM-KONTEXT-GENERIERUNG
# ============================================================

def knowledge_for_llm(articles: Optional[list[dict]] = None,
                      topic: str = "") -> str:
    """
    Generiert Wissens-Graph-Kontext fuer LLM-Analyse.
    Wird direkt in den Prompt injiziert.

    Inhalt:
    1. Aktive Einheiten (48h) mit letztem Standort
    2. Einheiten-Bewegungen (Richtung + Distanz)
    3. Aktivitaets-Hotspots
    4. Schluessel-Personen erwaehnt
    """
    # Neue Artikel einpflegen wenn angegeben
    if articles:
        ingest_articles(articles)

    lines = ["=== WISSENS-GRAPH (Persistenter Entity-Tracker) ==="]

    # ── Aktive Einheiten ──────────────────────────────────────────────────────
    units = get_active_entities(hours=72, etype="UNIT")
    if units:
        lines.append("\n[AKTIVE EINHEITEN – letzte 72h]")
        for u in units[:15]:
            timeline = get_entity_timeline(u["name"], limit=3)
            locations = [t["location"] for t in timeline if t["location"]]
            loc_str = " → ".join(dict.fromkeys(locations)) if locations else "Ort unbekannt"
            movements = get_entity_movements(u["name"])
            move_str = ""
            if movements:
                last_move = movements[-1]
                move_str = (f" [Bewegung: {last_move['direction']} "
                            f"{last_move['distance_km']}km]")
            lines.append(
                f"  • {u['name']}  |  {loc_str}{move_str}  "
                f"|  {u['recent_obs']}x erwaehnt  |  zuletzt {u['last_seen']}"
            )

    # ── Orte-Hotspots ─────────────────────────────────────────────────────────
    hotspots = get_hotspots(hours=24, min_obs=2)
    if hotspots:
        lines.append("\n[AKTIVITAETS-HOTSPOTS – letzte 24h]")
        for h in hotspots[:10]:
            lines.append(
                f"  • {h['location']}  |  {h['obs_count']}x Aktivitaet  "
                f"|  Quellen: {h['sources'][:60]}"
            )

    # ── Bewegungspfade ────────────────────────────────────────────────────────
    movements_all = []
    for u in units[:10]:
        mvs = get_entity_movements(u["name"])
        if mvs:
            movements_all.append((u["name"], mvs))

    if movements_all:
        lines.append("\n[BEOBACHTETE EINHEITENBEWEGUNGEN]")
        for unit_name, mvs in movements_all:
            path = " → ".join(
                f"{m['from']}→{m['to']}({m['direction']},{m['distance_km']}km)"
                for m in mvs[-3:]
            )
            lines.append(f"  • {unit_name}: {path}")

    # ── Schluessel-Personen ───────────────────────────────────────────────────
    persons = get_active_entities(hours=72, etype="PERSON")
    if persons:
        lines.append("\n[ERWAEHNTE PERSONEN – letzte 72h]")
        person_list = ", ".join(
            f"{p['name']} ({p['recent_obs']}x)" for p in persons[:8]
        )
        lines.append(f"  {person_list}")

    # ── Datenbank-Status ──────────────────────────────────────────────────────
    stats = get_db_stats()
    lines.append(
        f"\n[DATENBANK: {stats.get('entities',0)} Entitaeten | "
        f"{stats.get('observations',0)} Beobachtungen | "
        f"{stats.get('movements',0)} Bewegungen"
        + (f" | Daten seit {stats.get('oldest','?')}" if stats.get("oldest") else "")
        + "]"
    )

    if len(lines) <= 2:
        return ""  # Nichts gefunden – kein Kontext hinzufuegen

    return "\n".join(lines)


def knowledge_summary() -> str:
    """Kurzzusammenfassung fuer Terminal-Ausgabe."""
    stats = get_db_stats()
    units = get_active_entities(hours=48, etype="UNIT")
    hotspots = get_hotspots(hours=24, min_obs=2)
    by_type = stats.get("by_type", {})
    return (
        f"[KNOWLEDGE] DB: {stats.get('entities',0)} Entitaeten "
        f"(Einheiten:{by_type.get('UNIT',0)} "
        f"Orte:{by_type.get('LOCATION',0)} "
        f"Waffen:{by_type.get('WEAPON',0)} "
        f"Personen:{by_type.get('PERSON',0)}) | "
        f"{stats.get('observations',0)} Beobachtungen | "
        f"{len(units)} Einheiten aktiv (48h) | "
        f"{len(hotspots)} Hotspots (24h)"
    )


# ============================================================
# MAP-DATEN  (fuer nexus_report.py / Leaflet)
# ============================================================

def get_map_data(hours: float = 48.0) -> dict:
    """
    Gibt Daten fuer Karten-Overlays zurueck.
    Wird von nexus_live_server.py verwendet.
    """
    units = get_active_entities(hours=hours, etype="UNIT")
    hotspots = get_hotspots(hours=24, min_obs=2)
    movements_data = []

    for u in units[:20]:
        mvs = get_entity_movements(u["name"])
        if mvs:
            movements_data.append({
                "unit":      u["name"],
                "movements": mvs,
            })

    return {
        "active_units":   units,
        "hotspots":       hotspots,
        "unit_movements": movements_data,
    }


# ============================================================
# QUERY-INTERFACE  (fuer Sprachsteuerung)
# ============================================================

def query(text: str) -> str:
    """
    Natuerlichsprachliche Abfrage: "Was weisst du ueber die 64. Brigade?"
    Gibt formatierten Text zurueck.
    """
    text_lower = text.lower()

    # Einheiten-Abfrage
    unit_kw = ["einheit", "brigade", "bataillon", "regiment", "division", "unit",
                "battalion", "corps", "kompanie", "company"]
    if any(kw in text_lower for kw in unit_kw):
        # Entitaet aus Query extrahieren
        units = get_active_entities(hours=168, etype="UNIT")
        best = None
        for u in units:
            words = u["name"].lower().split()
            if any(w in text_lower for w in words if len(w) > 4):
                best = u
                break
        if best:
            timeline = get_entity_timeline(best["name"], limit=5)
            movements = get_entity_movements(best["name"])
            lines = [f"[KNOWLEDGE] {best['name']}"]
            lines.append(f"  Beobachtungen gesamt: {best['obs_count']}")
            if timeline:
                lines.append("  Letzte Sichtungen:")
                for t in timeline[:4]:
                    loc = f" @ {t['location']}" if t["location"] else ""
                    lines.append(f"    {t['when']}{loc} ({t['source']})")
            if movements:
                lines.append("  Bewegungspfad:")
                for m in movements[-4:]:
                    lines.append(
                        f"    {m['from']} → {m['to']} "
                        f"({m['direction']}, {m['distance_km']}km) {m['when']}"
                    )
            return "\n".join(lines)
        return "[KNOWLEDGE] Keine Einheiten-Daten fuer diese Anfrage."

    # Ort-Abfrage
    loc_kw = ["ort", "stadt", "gebiet", "region", "front", "location", "where",
               "wo", "bei", "nahe", "hotspot"]
    if any(kw in text_lower for kw in loc_kw):
        hotspots = get_hotspots(hours=48, min_obs=1)
        if hotspots:
            lines = ["[KNOWLEDGE] Aktivitaets-Hotspots (48h):"]
            for h in hotspots[:8]:
                lines.append(
                    f"  {h['location']}: {h['obs_count']}x "
                    f"({h['last_seen']}) – {h['sources'][:50]}"
                )
            return "\n".join(lines)
        return "[KNOWLEDGE] Keine Hotspot-Daten."

    # Allgemeine Statistik
    return knowledge_summary()


# ============================================================
# SELF-TEST
# ============================================================

if __name__ == "__main__":
    print("[TEST] nexus_knowledge.py")

    test_articles = [
        {
            "title": "64th Motor Rifle Brigade advances near Avdiivka",
            "summary": (
                "Russian forces from the 64th Motor Rifle Brigade were observed "
                "advancing towards Avdiivka from the east. The unit, previously "
                "spotted near Donetsk, appears to be part of a larger push. "
                "T-72 tanks and BMP-2 vehicles were confirmed by Bayraktar TB2 UAV footage."
            ),
            "source": "ISW Reports",
            "age_min": 30,
            "lat": 48.12, "lon": 37.75,
            "conf_badge": "✅ BESTAETIGT",
        },
        {
            "title": "155th Naval Infantry Brigade spotted in Zaporizhzhia region",
            "summary": (
                "Satellite imagery confirms 155th Naval Infantry Brigade redeployment "
                "from Zaporizhzhia towards Robotyne. Wagner mercenaries also active "
                "in the area. HIMARS strike hit logistics hub near Melitopol."
            ),
            "source": "Bellingcat",
            "age_min": 90,
            "conf_badge": "WAHRSCHEINLICH",
        },
        {
            "title": "Zelensky meets Austin at Pentagon to discuss ATACMS supply",
            "summary": (
                "President Zelensky held talks with Defense Secretary Austin regarding "
                "additional ATACMS deliveries. Patriot air defense systems will be "
                "deployed to protect Kyiv and Kharkiv from Shahed drone attacks."
            ),
            "source": "Reuters World",
            "age_min": 120,
        },
    ]

    stats = ingest_articles(test_articles)
    print(f"  Ingested: {stats}")
    print()

    # Statistik
    print(knowledge_summary())
    print()

    # Zeitline
    print("[TEST] Einheiten-Timeline:")
    tl = get_entity_timeline("64th Motor Rifle Brigade")
    for t in tl:
        print(f"  {t['when']} @ {t['location']} via {t['source']}")
    print()

    # Hotspots
    print("[TEST] Hotspots:")
    hs = get_hotspots(hours=24*365, min_obs=1)
    for h in hs[:5]:
        print(f"  {h['location']}: {h['obs_count']}x")
    print()

    # LLM-Kontext
    ctx = knowledge_for_llm()
    print("[TEST] LLM-Kontext (Auszug):")
    for line in ctx.split("\n")[:20]:
        print(" ", line)

    print("\n[TEST OK]")
