"""
NEXUS – Entity-Tracking System  (Palantir-Kern)
================================================
Verfolgt Akteure, Fahrzeuge, Gruppen, Organisationen über Zeit.

Features:
  • SQLite-Persistenz (nexus_entities.db)
  • Pattern-of-Life Analyse (Wann/Wo/Mit-wem taucht X auf?)
  • Sichtungs-Timeline mit Quellenangabe (Data Provenance)
  • Co-Occurrence → automatische Beziehungserkennung
  • Konfidenz-Scoring pro Entität
  • NER-Integration (nexus_ner.py falls vorhanden)
  • Direkter Import durch alle NEXUS-Module

Öffentliche API:
  get_tracker()                          → EntityTracker
  process_news_item(title, summary, ...) → List[entity_id]
  get_entity_summary_html(days)          → str (HTML-Block)
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Konstanten
# ─────────────────────────────────────────────────────────────────────────────

ENTITY_TYPES: Dict[str, str] = {
    "PERSON":       "👤",
    "VEHICLE":      "🚗",
    "AIRCRAFT":     "✈️",
    "VESSEL":       "⚓",
    "ORGANIZATION": "🏛️",
    "LOCATION":     "📍",
    "WEAPON":       "⚔️",
    "EVENT":        "💥",
    "UNKNOWN":      "❓",
}

# Mapping von spaCy-Labels auf unsere Typen
_NER_MAP: Dict[str, str] = {
    "PER":          "PERSON",
    "PERSON":       "PERSON",
    "ORG":          "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
    "GPE":          "LOCATION",
    "LOC":          "LOCATION",
    "LOCATION":     "LOCATION",
    "FAC":          "LOCATION",
    "NORP":         "ORGANIZATION",
    "VEHICLE":      "VEHICLE",
    "AIRCRAFT":     "AIRCRAFT",
    "VESSEL":       "VESSEL",
    "WEAPON":       "WEAPON",
    "EVENT":        "EVENT",
}

DB_PATH = Path(__file__).parent / "nexus_entities.db"

# Wörter die NICHT als Entitäten gelten
_STOPWORDS = {
    "The", "This", "That", "These", "Those", "In", "On", "At", "By", "For",
    "From", "With", "About", "Into", "Through", "After", "Before", "Under",
    "Der", "Die", "Das", "Ein", "Eine", "Im", "Am", "Und", "Oder", "Aber",
    "Nach", "Vor", "Mit", "Ohne", "Über", "Unter", "Durch", "Gegen",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
}


# ─────────────────────────────────────────────────────────────────────────────
# Datenklassen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EntitySighting:
    entity_id: str
    timestamp: str
    source: str
    source_url: str = ""
    context: str = ""
    location: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    confidence: float = 0.5
    related_entities: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# EntityTracker
# ─────────────────────────────────────────────────────────────────────────────

class EntityTracker:
    """Kern-Engine für persistentes Entity-Tracking."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db = Path(db_path)
        self._init_db()

    # ── DB-Setup ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        # Auto-Recovery: kaputte/leere DB loeschen und neu anlegen
        if self.db.exists() and self.db.stat().st_size == 0:
            try:
                self.db.unlink()
            except OSError:
                pass
        try:
            self._run_schema()
        except sqlite3.OperationalError:
            # Letzter Versuch: DB loeschen und neu starten
            try:
                self.db.unlink()
            except OSError:
                pass
            self._run_schema()

    def _run_schema(self) -> None:
        # Einzelne execute()-Aufrufe statt executescript() um implizites COMMIT zu vermeiden
        conn = sqlite3.connect(str(self.db))
        try:
            # Journal-Modus setzen (WAL bevorzugt, DELETE als Fallback)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                try:
                    conn.execute("PRAGMA journal_mode=DELETE")
                except Exception:
                    pass

            stmts = [
                """CREATE TABLE IF NOT EXISTS entities (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    entity_type   TEXT NOT NULL DEFAULT 'UNKNOWN',
                    aliases       TEXT NOT NULL DEFAULT '[]',
                    first_seen    TEXT,
                    last_seen     TEXT,
                    mention_count INTEGER NOT NULL DEFAULT 0,
                    confidence    REAL    NOT NULL DEFAULT 0.5,
                    metadata      TEXT    NOT NULL DEFAULT '{}',
                    tags          TEXT    NOT NULL DEFAULT '[]',
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
                )""",
                """CREATE TABLE IF NOT EXISTS sightings (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id        TEXT    NOT NULL,
                    timestamp        TEXT    NOT NULL,
                    source           TEXT,
                    source_url       TEXT,
                    context          TEXT,
                    location         TEXT,
                    lat              REAL,
                    lon              REAL,
                    confidence       REAL    NOT NULL DEFAULT 0.5,
                    related_entities TEXT    NOT NULL DEFAULT '[]',
                    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (entity_id) REFERENCES entities(id)
                )""",
                """CREATE TABLE IF NOT EXISTS relationships (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_a   TEXT NOT NULL,
                    entity_b   TEXT NOT NULL,
                    rel_type   TEXT NOT NULL DEFAULT 'CO_MENTIONED',
                    strength   REAL NOT NULL DEFAULT 0.3,
                    first_seen TEXT,
                    last_seen  TEXT,
                    evidence   TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (entity_a, entity_b, rel_type),
                    FOREIGN KEY (entity_a) REFERENCES entities(id),
                    FOREIGN KEY (entity_b) REFERENCES entities(id)
                )""",
                """CREATE TABLE IF NOT EXISTS provenance (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    data_hash        TEXT UNIQUE,
                    source_type      TEXT,
                    source_url       TEXT,
                    collected_at     TEXT,
                    raw_snippet      TEXT,
                    entity_ids       TEXT NOT NULL DEFAULT '[]',
                    reliability      REAL NOT NULL DEFAULT 0.5
                )""",
                "CREATE INDEX IF NOT EXISTS idx_sight_entity ON sightings(entity_id)",
                "CREATE INDEX IF NOT EXISTS idx_sight_time   ON sightings(timestamp)",
                "CREATE INDEX IF NOT EXISTS idx_rel_a        ON relationships(entity_a)",
                "CREATE INDEX IF NOT EXISTS idx_rel_b        ON relationships(entity_b)",
                "CREATE INDEX IF NOT EXISTS idx_ent_type     ON entities(entity_type)",
                "CREATE INDEX IF NOT EXISTS idx_ent_count    ON entities(mention_count DESC)",
            ]
            for stmt in stmts:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_id(name: str, entity_type: str) -> str:
        key = f"{entity_type}:{name.lower().strip()}"
        return hashlib.md5(key.encode()).hexdigest()[:14]

    # ── Entity CRUD ───────────────────────────────────────────────────────────

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        aliases: List[str] = None,
        confidence: float = 0.5,
        metadata: Dict = None,
        tags: List[str] = None,
    ) -> str:
        """Entität anlegen oder aktualisieren. Gibt die ID zurück."""
        eid  = self._make_id(name, entity_type)
        now  = datetime.utcnow().isoformat()
        meta = json.dumps(metadata or {})

        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT aliases, mention_count, confidence FROM entities WHERE id=?", (eid,)
            ).fetchone()

            if row:
                old_aliases = json.loads(row[0])
                merged      = list(set(old_aliases + (aliases or [])))
                new_conf    = max(row[2], confidence)
                c.execute(
                    """UPDATE entities
                       SET aliases=?, last_seen=?, mention_count=mention_count+1,
                           confidence=?, tags=?, metadata=?
                       WHERE id=?""",
                    (json.dumps(merged), now, new_conf,
                     json.dumps(tags or []), meta, eid),
                )
            else:
                c.execute(
                    """INSERT INTO entities
                       (id, name, entity_type, aliases, first_seen, last_seen,
                        mention_count, confidence, metadata, tags)
                       VALUES (?,?,?,?,?,?,1,?,?,?)""",
                    (eid, name.strip(), entity_type,
                     json.dumps(aliases or []), now, now,
                     confidence, meta, json.dumps(tags or [])),
                )
        return eid

    def add_sighting(
        self,
        entity_id: str,
        source: str,
        context: str = "",
        timestamp: str = None,
        source_url: str = "",
        location: str = None,
        lat: float = None,
        lon: float = None,
        confidence: float = 0.5,
        related: List[str] = None,
    ) -> None:
        ts = timestamp or datetime.utcnow().isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """INSERT INTO sightings
                   (entity_id, timestamp, source, source_url, context,
                    location, lat, lon, confidence, related_entities)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (entity_id, ts, source, source_url, context[:600],
                 location, lat, lon, confidence, json.dumps(related or [])),
            )
            c.execute(
                "UPDATE entities SET last_seen=? WHERE id=?", (ts, entity_id)
            )

    def add_relationship(
        self,
        entity_a: str,
        entity_b: str,
        rel_type: str = "CO_MENTIONED",
        strength: float = 0.3,
        evidence: str = "",
    ) -> None:
        """Beziehung zwischen zwei Entitäten speichern (normalisiert A < B)."""
        if entity_a > entity_b:
            entity_a, entity_b = entity_b, entity_a
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db) as c:
            row = c.execute(
                """SELECT id, strength, evidence FROM relationships
                   WHERE entity_a=? AND entity_b=? AND rel_type=?""",
                (entity_a, entity_b, rel_type),
            ).fetchone()

            if row:
                evid = json.loads(row[2])
                if evidence and evidence not in evid:
                    evid.append(evidence)
                new_str = min(1.0, row[1] + 0.08)
                c.execute(
                    """UPDATE relationships
                       SET strength=?, last_seen=?, evidence=?
                       WHERE id=?""",
                    (new_str, now, json.dumps(evid[-15:]), row[0]),
                )
            else:
                c.execute(
                    """INSERT INTO relationships
                       (entity_a, entity_b, rel_type, strength,
                        first_seen, last_seen, evidence)
                       VALUES (?,?,?,?,?,?,?)""",
                    (entity_a, entity_b, rel_type, strength,
                     now, now, json.dumps([evidence] if evidence else [])),
                )

    # ── Abfragen ──────────────────────────────────────────────────────────────

    def get_entity(self, entity_id: str) -> Optional[Dict]:
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                """SELECT id, name, entity_type, aliases, first_seen, last_seen,
                          mention_count, confidence, metadata, tags
                   FROM entities WHERE id=?""",
                (entity_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "name": row[1], "type": row[2],
            "aliases":  json.loads(row[3]),
            "first_seen": row[4], "last_seen": row[5],
            "mentions": row[6], "confidence": row[7],
            "metadata": json.loads(row[8]),
            "tags":     json.loads(row[9]),
            "icon":     ENTITY_TYPES.get(row[2], "❓"),
        }

    def get_all_entities(
        self, entity_type: str = None, limit: int = 100
    ) -> List[Dict]:
        with sqlite3.connect(self.db) as c:
            if entity_type:
                rows = c.execute(
                    """SELECT id, name, entity_type, mention_count, confidence,
                              first_seen, last_seen, tags
                       FROM entities WHERE entity_type=?
                       ORDER BY mention_count DESC LIMIT ?""",
                    (entity_type, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT id, name, entity_type, mention_count, confidence,
                              first_seen, last_seen, tags
                       FROM entities ORDER BY mention_count DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "type": r[2],
                "mentions": r[3], "confidence": r[4],
                "first_seen": r[5], "last_seen": r[6],
                "tags": json.loads(r[7] or "[]"),
                "icon": ENTITY_TYPES.get(r[2], "❓"),
            }
            for r in rows
        ]

    def search_entities(
        self, query: str, entity_type: str = None, limit: int = 20
    ) -> List[Dict]:
        q = f"%{query.lower()}%"
        with sqlite3.connect(self.db) as c:
            if entity_type:
                rows = c.execute(
                    """SELECT id, name, entity_type, mention_count, confidence, last_seen
                       FROM entities
                       WHERE (LOWER(name) LIKE ? OR LOWER(aliases) LIKE ?)
                         AND entity_type=?
                       ORDER BY mention_count DESC LIMIT ?""",
                    (q, q, entity_type, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT id, name, entity_type, mention_count, confidence, last_seen
                       FROM entities
                       WHERE LOWER(name) LIKE ? OR LOWER(aliases) LIKE ?
                       ORDER BY mention_count DESC LIMIT ?""",
                    (q, q, limit),
                ).fetchall()
        return [
            {"id": r[0], "name": r[1], "type": r[2],
             "mentions": r[3], "confidence": r[4], "last_seen": r[5],
             "icon": ENTITY_TYPES.get(r[2], "❓")}
            for r in rows
        ]

    def get_entity_timeline(
        self, entity_id: str, days: int = 30
    ) -> List[Dict]:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db) as c:
            rows = c.execute(
                """SELECT timestamp, source, context, location, lat, lon,
                          confidence, source_url
                   FROM sightings WHERE entity_id=? AND timestamp>=?
                   ORDER BY timestamp DESC LIMIT 100""",
                (entity_id, since),
            ).fetchall()
        return [
            {"ts": r[0], "source": r[1], "context": r[2],
             "location": r[3], "lat": r[4], "lon": r[5],
             "confidence": r[6], "url": r[7]}
            for r in rows
        ]

    # ── Pattern-of-Life ───────────────────────────────────────────────────────

    def get_pattern_of_life(self, entity_id: str) -> Dict:
        """Analysiert: Wann / Wo / Mit-wem taucht Entität auf?"""
        with sqlite3.connect(self.db) as c:
            rows = c.execute(
                """SELECT timestamp, source, location, lat, lon, related_entities
                   FROM sightings WHERE entity_id=? ORDER BY timestamp""",
                (entity_id,),
            ).fetchall()

        if not rows:
            return {"error": "Keine Sichtungsdaten vorhanden"}

        hours         = [0] * 24
        weekdays      = [0] * 7
        sources: Dict[str, int]     = {}
        locations: Dict[str, int]   = {}
        co_occur: Dict[str, int]    = {}

        for ts_str, src, loc, lat, lon, related_json in rows:
            try:
                dt = datetime.fromisoformat(ts_str)
                hours[dt.hour]     += 1
                weekdays[dt.weekday()] += 1
            except Exception:
                pass

            if src:
                sources[src] = sources.get(src, 0) + 1
            if loc:
                locations[loc] = locations.get(loc, 0) + 1
            for rid in json.loads(related_json or "[]"):
                co_occur[rid] = co_occur.get(rid, 0) + 1

        # Entitätsnamen für co-occurrence auflösen
        associates = []
        for eid, cnt in sorted(co_occur.items(), key=lambda x: -x[1])[:8]:
            e = self.get_entity(eid)
            name = e["name"] if e else eid
            associates.append({"id": eid, "name": name, "co_sightings": cnt})

        day_names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        peak_hour    = hours.index(max(hours)) if hours else 0
        peak_weekday = weekdays.index(max(weekdays)) if weekdays else 0

        return {
            "total_sightings":      len(rows),
            "first_seen":           rows[0][0],
            "last_seen":            rows[-1][0],
            "peak_hour_utc":        peak_hour,
            "peak_weekday":         day_names[peak_weekday],
            "activity_by_hour":     hours,
            "activity_by_weekday":  weekdays,
            "top_sources":          sorted(sources.items(), key=lambda x: -x[1])[:6],
            "top_locations":        sorted(locations.items(), key=lambda x: -x[1])[:6],
            "frequent_associates":  associates,
        }

    # ── Netzwerk-Daten ────────────────────────────────────────────────────────

    def get_entity_network(
        self,
        entity_id: str = None,
        min_strength: float = 0.25,
        max_nodes: int = 150,
    ) -> Dict:
        """Netzwerk-Graph für vis.js."""
        with sqlite3.connect(self.db) as c:
            if entity_id:
                rels = c.execute(
                    """SELECT entity_a, entity_b, rel_type, strength FROM relationships
                       WHERE (entity_a=? OR entity_b=?) AND strength>=?
                       ORDER BY strength DESC LIMIT 300""",
                    (entity_id, entity_id, min_strength),
                ).fetchall()
            else:
                rels = c.execute(
                    """SELECT entity_a, entity_b, rel_type, strength FROM relationships
                       WHERE strength>=?
                       ORDER BY strength DESC LIMIT 400""",
                    (min_strength,),
                ).fetchall()

            # Alle beteiligten Entity-IDs sammeln
            eids: set = set()
            if entity_id:
                eids.add(entity_id)
            for r in rels:
                eids.add(r[0])
                eids.add(r[1])

            # Entitätsdaten laden
            entities: Dict[str, Dict] = {}
            for eid in list(eids)[:max_nodes]:
                row = c.execute(
                    "SELECT id, name, entity_type, mention_count, confidence FROM entities WHERE id=?",
                    (eid,),
                ).fetchone()
                if row:
                    entities[eid] = {
                        "id": row[0], "name": row[1], "type": row[2],
                        "weight": row[3], "conf": row[4],
                    }

        # vis.js Nodes
        type_colors = {
            "PERSON":       "#3b82f6",
            "ORGANIZATION": "#8b5cf6",
            "LOCATION":     "#10b981",
            "VEHICLE":      "#f59e0b",
            "AIRCRAFT":     "#06b6d4",
            "VESSEL":       "#0ea5e9",
            "WEAPON":       "#ef4444",
            "EVENT":        "#f97316",
        }
        nodes = []
        node_ids = set(entities.keys())
        for e in entities.values():
            icon  = ENTITY_TYPES.get(e["type"], "❓")
            color = type_colors.get(e["type"], "#64748b")
            nodes.append({
                "id":    e["id"],
                "label": f"{icon} {e['name']}",
                "title": f"{e['type']} · {e['weight']}× gesehen",
                "group": e["type"],
                "value": min(max(e["weight"], 1), 60),
                "color": {"background": color, "border": color},
            })

        edges = []
        seen_edges: set = set()
        for r in rels:
            key = (r[0], r[1], r[2])
            if key in seen_edges:
                continue
            if r[0] in node_ids and r[1] in node_ids:
                seen_edges.add(key)
                edges.append({
                    "from":  r[0], "to": r[1],
                    "label": r[2],
                    "value": round(r[3], 2),
                    "title": f"{r[2]} (Stärke: {r[3]:.2f})",
                    "color": {"color": "#475569", "highlight": "#94a3b8"},
                })

        return {"nodes": nodes, "edges": edges,
                "meta": {"node_count": len(nodes), "edge_count": len(edges)}}

    # ── Statistiken ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        with sqlite3.connect(self.db) as c:
            total   = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            by_type = dict(c.execute(
                "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type"
            ).fetchall())
            sights  = c.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
            rels    = c.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
            recent  = c.execute(
                "SELECT COUNT(*) FROM sightings WHERE timestamp >= datetime('now','-24 hours')"
            ).fetchone()[0]
            top5    = c.execute(
                """SELECT name, entity_type, mention_count FROM entities
                   ORDER BY mention_count DESC LIMIT 5"""
            ).fetchall()

        return {
            "total_entities":    total,
            "by_type":           by_type,
            "total_sightings":   sights,
            "total_relationships": rels,
            "sightings_24h":     recent,
            "top_entities":      [{"name": r[0], "type": r[1], "mentions": r[2]} for r in top5],
        }

    # ── Provenance ────────────────────────────────────────────────────────────

    def add_provenance(
        self,
        source_type: str,
        source_url: str,
        raw_snippet: str,
        entity_ids: List[str],
        reliability: float = 0.5,
    ) -> str:
        h = hashlib.sha256((source_url + raw_snippet[:200]).encode()).hexdigest()[:16]
        with sqlite3.connect(self.db) as c:
            try:
                c.execute(
                    """INSERT OR IGNORE INTO provenance
                       (data_hash, source_type, source_url, collected_at,
                        raw_snippet, entity_ids, reliability)
                       VALUES (?,?,?,?,?,?,?)""",
                    (h, source_type, source_url,
                     datetime.utcnow().isoformat(),
                     raw_snippet[:1000], json.dumps(entity_ids), reliability),
                )
            except Exception:
                pass
        return h

    # ── Entitäts-Extraktion ───────────────────────────────────────────────────

    def extract_and_store(
        self,
        text: str,
        source: str,
        source_url: str = "",
        timestamp: str = None,
        region: str = None,
        lat: float = None,
        lon: float = None,
        min_confidence: float = 0.5,
    ) -> List[str]:
        """Extrahiert Entitäten aus Text und speichert Sichtungen."""
        raw_entities: List[Dict] = []

        # Primär: spaCy über nexus_ner.py
        try:
            import nexus_ner
            ner_result = nexus_ner.extract_entities(text)
            for e in ner_result:
                raw_entities.append({
                    "text":  e.get("text", e.get("name", "")),
                    "label": e.get("label", e.get("type", "UNKNOWN")),
                    "conf":  e.get("confidence", 0.65),
                })
        except Exception:
            pass

        # Fallback: Regex-Erkennung (kapitalisierte Wörter)
        if not raw_entities:
            raw_entities = self._regex_extract(text)

        # Normalisierung + Speicherung
        entity_ids: List[str] = []
        id_to_name:  Dict[str, str] = {}

        for raw in raw_entities:
            name  = raw["text"].strip()
            label = raw["label"]
            conf  = float(raw.get("conf", 0.5))

            if not name or len(name) < 3 or name in _STOPWORDS:
                continue

            etype = _NER_MAP.get(label, "UNKNOWN")
            if etype == "UNKNOWN" and conf < 0.6:
                continue

            eid = self.upsert_entity(name, etype, confidence=conf)
            if eid not in id_to_name:
                id_to_name[eid] = name
                entity_ids.append(eid)

        # Sichtungen + Co-Occurrence-Beziehungen anlegen
        for eid in entity_ids:
            related = [x for x in entity_ids if x != eid]
            self.add_sighting(
                entity_id  = eid,
                source     = source,
                source_url = source_url,
                context    = text[:400],
                timestamp  = timestamp,
                location   = region,
                lat        = lat,
                lon        = lon,
                confidence = min_confidence,
                related    = related,
            )
            for other in related:
                self.add_relationship(eid, other, "CO_MENTIONED",
                                      strength=0.3, evidence=source)

        # Provenance
        if entity_ids:
            self.add_provenance(source, source_url, text[:500], entity_ids)

        return entity_ids

    @staticmethod
    def _regex_extract(text: str) -> List[Dict]:
        """Einfache Regex-Extraktion als Fallback (ohne spaCy)."""
        pattern = re.compile(
            r'\b([A-ZÜÖÄ][a-züöäß]+(?:\s+[A-ZÜÖÄ][a-züöäß]+){0,3})\b'
        )
        found = []
        seen:  set = set()
        for m in pattern.finditer(text):
            word = m.group(1)
            if word not in _STOPWORDS and len(word) > 3 and word not in seen:
                seen.add(word)
                found.append({"text": word, "label": "UNKNOWN", "conf": 0.4})
        return found[:25]


# ─────────────────────────────────────────────────────────────────────────────
# Globale Singleton-Instanz
# ─────────────────────────────────────────────────────────────────────────────

_tracker: Optional[EntityTracker] = None


def get_tracker() -> EntityTracker:
    global _tracker
    if _tracker is None:
        _tracker = EntityTracker()
    return _tracker


# ─────────────────────────────────────────────────────────────────────────────
# Öffentliche Komfort-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def process_news_item(
    title: str,
    summary: str,
    source: str,
    url: str = "",
    timestamp: str = None,
    region: str = None,
    lat: float = None,
    lon: float = None,
) -> List[str]:
    """Verarbeitet einen Nachrichtenartikel und extrahiert Entitäten."""
    text = f"{title}. {summary}"
    return get_tracker().extract_and_store(
        text, source, url, timestamp, region, lat, lon
    )


def get_entity_summary_html(days: int = 7) -> str:
    """Erzeugt HTML-Zusammenfassung der getrackte Entitäten."""
    tracker = get_tracker()
    stats   = tracker.get_stats()
    ents    = tracker.get_all_entities(limit=15)

    rows = ""
    for e in ents:
        last    = (e["last_seen"] or "")[:10]
        conf_pc = int(e["confidence"] * 100)
        bar_w   = max(4, conf_pc)
        rows += f"""
        <tr>
          <td>{e['icon']} <strong>{e['name']}</strong></td>
          <td style="color:#94a3b8;font-size:11px">{e['type']}</td>
          <td style="text-align:center">{e['mentions']}</td>
          <td>
            <div style="background:rgba(255,255,255,.08);border-radius:3px;height:6px;width:80px">
              <div style="background:#3b82f6;width:{bar_w}%;height:100%;border-radius:3px"></div>
            </div>
          </td>
          <td style="color:#64748b;font-size:11px">{last}</td>
        </tr>"""

    by_type_html = " · ".join(
        f"{ENTITY_TYPES.get(k,'❓')} {v} {k}" for k, v in stats["by_type"].items()
    )

    return f"""
<div style="background:rgba(255,255,255,.04);border-radius:8px;padding:14px;margin:10px 0">
  <h3 style="color:#60a5fa;margin:0 0 8px;font-size:14px">🔍 Entity-Tracking</h3>
  <div style="font-size:11px;color:#94a3b8;margin-bottom:10px">
    <span style="color:#e2e8f0;font-weight:600">{stats['total_entities']}</span> Entitäten ·
    <span style="color:#e2e8f0;font-weight:600">{stats['total_sightings']}</span> Sichtungen ·
    <span style="color:#e2e8f0;font-weight:600">{stats['total_relationships']}</span> Verbindungen ·
    <span style="color:#4ade80;font-weight:600">{stats['sightings_24h']}</span> letzte 24h
  </div>
  <div style="font-size:11px;color:#64748b;margin-bottom:10px">{by_type_html}</div>
  <table style="width:100%;border-collapse:collapse;font-size:12px">
    <thead>
      <tr style="color:#64748b;font-size:10px;text-transform:uppercase">
        <th style="text-align:left;padding:4px 8px">Entität</th>
        <th style="text-align:left;padding:4px 8px">Typ</th>
        <th style="text-align:center;padding:4px 8px">Sichtungen</th>
        <th style="padding:4px 8px">Konfidenz</th>
        <th style="text-align:left;padding:4px 8px">Zuletzt</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


def cli_summary() -> str:
    """Terminal-Zusammenfassung für NEXUS-Konsole."""
    tracker = get_tracker()
    stats   = tracker.get_stats()
    top5    = stats.get("top_entities", [])

    lines = [
        f"  🔍 Entity-Tracking: {stats['total_entities']} Akteure | "
        f"{stats['total_sightings']} Sichtungen | "
        f"{stats['total_relationships']} Verbindungen | "
        f"{stats['sightings_24h']} in 24h",
    ]
    if top5:
        lines.append("  📊 Top-Entitäten:")
        for e in top5:
            icon = ENTITY_TYPES.get(e["type"], "❓")
            lines.append(f"      {icon} {e['name']} ({e['type']}) – {e['mentions']}× gesehen")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone-Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = get_tracker()
    eid = t.add_entity("TEST-001", "unit", {"region": "Ukraine"})
    found = t.get_entity(eid)
    stats = t.get_stats()
    ok = found is not None
    print("EntityTracker OK: id=" + str(eid) + " found=" + str(ok))
    print("Stats: " + str(stats["total_entities"]) + " entities")
