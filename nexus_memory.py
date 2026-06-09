"""
NEXUS - Ereignisspeicher (SQLite)
Persistente Speicherung aller OSINT-Ereignisse für Trend-Analyse und Watchlist.
Datei: nexus_memory.db im gleichen Verzeichnis wie NEXUS.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "nexus_memory.db"


# ── Verbindung ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


# ── Schema erstellen ────────────────────────────────────────────────────────

def init_db() -> None:
    """Erstellt Tabellen falls noch nicht vorhanden. Sicher mehrfach aufrufbar."""
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT    NOT NULL,
                query     TEXT    DEFAULT '',
                etype     TEXT    DEFAULT '',
                title     TEXT    DEFAULT '',
                summary   TEXT    DEFAULT '',
                lat       REAL,
                lon       REAL,
                url       TEXT    DEFAULT '#',
                source    TEXT    DEFAULT '',
                severity  INTEGER DEFAULT 0,
                extra     TEXT    DEFAULT '{}'
            )""")
        db.execute("CREATE INDEX IF NOT EXISTS ix_ts    ON events(ts)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_query ON events(query)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_etype ON events(etype)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                term       TEXT    NOT NULL UNIQUE,
                added_ts   TEXT    NOT NULL,
                checked_ts TEXT,
                alert_cnt  INTEGER DEFAULT 0,
                active     INTEGER DEFAULT 1
            )""")
        db.commit()


# ── Einzelnes Ereignis speichern ────────────────────────────────────────────

def store_event(
    query:    str,
    etype:    str,
    title:    str,
    lat:      float = None,
    lon:      float = None,
    url:      str   = "#",
    source:   str   = "",
    summary:  str   = "",
    severity: int   = 0,
    extra:    dict  = None,
) -> int:
    """Speichert ein Ereignis. Gibt die neue ID zurück."""
    init_db()
    ts  = datetime.now(timezone.utc).isoformat()
    exj = json.dumps(extra or {}, ensure_ascii=False)
    with _conn() as db:
        cur = db.execute(
            "INSERT INTO events (ts,query,etype,title,summary,lat,lon,url,source,severity,extra)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, query, etype, title[:500], summary[:1000],
             lat, lon, url, source, severity, exj),
        )
        db.commit()
        return cur.lastrowid


# ── Batch-Speicherung ───────────────────────────────────────────────────────

def store_articles(query: str, articles: list[dict]) -> None:
    """Speichert eine Liste von Nachrichtenartikeln."""
    init_db()
    ts = datetime.now(timezone.utc).isoformat()
    exj = json.dumps({})
    with _conn() as db:
        for a in articles:
            db.execute(
                "INSERT INTO events (ts,query,etype,title,summary,url,source,severity,extra)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, query, "news",
                 (a.get("title") or "")[:500],
                 (a.get("summary") or "")[:1000],
                 a.get("url", "#"),
                 a.get("source", ""),
                 0, exj),
            )
        db.commit()


def store_flight_alerts(query: str, flight_data: dict) -> None:
    """Speichert auffällige Flugzeuge als Severity-1-Ereignisse."""
    for ac in (flight_data.get("suspicious") or []):
        if ac.get("suspicious"):
            store_event(
                query=query, etype="flight_alert",
                title=f"Auffälliges Flugzeug: {ac.get('callsign','?')}",
                summary=ac.get("suspicious", ""),
                lat=ac.get("lat"), lon=ac.get("lon"),
                source="OpenSky", severity=1,
                extra={"callsign": ac.get("callsign"), "origin": ac.get("origin")},
            )


def store_maritime_alerts(query: str, maritime_data: dict) -> None:
    """Speichert Maritime-Alarme."""
    for alert in (maritime_data.get("alerts") or [])[:10]:
        store_event(
            query=query, etype="maritime_alert",
            title=(alert.get("title") or "")[:200],
            url=alert.get("url", "#"),
            source=alert.get("source", "Maritime"),
            severity=1,
        )


# ── Abfragen ────────────────────────────────────────────────────────────────

def get_recent(query: str = None, hours: int = 24,
               etype: str = None, limit: int = 100) -> list[dict]:
    """Gibt Ereignisse der letzten X Stunden zurück."""
    init_db()
    since  = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    sql    = "SELECT * FROM events WHERE ts >= ?"
    params: list = [since]
    if query:
        sql += " AND query = ?";  params.append(query)
    if etype:
        sql += " AND etype  = ?"; params.append(etype)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with _conn() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_trend_text(query: str = None, days: int = 7) -> str:
    """Gibt Text-Zusammenfassung der gespeicherten Trends zurück."""
    init_db()
    since  = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql    = "SELECT etype, COUNT(*) as cnt FROM events WHERE ts >= ?"
    params: list = [since]
    if query:
        sql += " AND query = ?"; params.append(query)
    sql += " GROUP BY etype ORDER BY cnt DESC"
    with _conn() as db:
        rows = db.execute(sql, params).fetchall()
    if not rows:
        return f"Keine Ereignisse in den letzten {days} Tagen gespeichert."
    suffix = f" · Region: {query}" if query else ""
    lines  = [f"NEXUS Trend-Analyse ({days} Tage{suffix}):"]
    total  = 0
    for r in rows:
        lines.append(f"  {r['etype']:<22} {r['cnt']:>4}×")
        total += r["cnt"]
    lines.append(f"  {'GESAMT':<22} {total:>4}×")
    return "\n".join(lines)


def get_geo_events(hours: int = 24) -> list[dict]:
    """Gibt gespeicherte Ereignisse mit Koordinaten zurück (für Karten-Replay)."""
    init_db()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE ts>=? AND lat IS NOT NULL AND lon IS NOT NULL"
            " ORDER BY ts DESC LIMIT 200",
            (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def new_events_since(term: str) -> int:
    """Gibt Anzahl neuer Ereignisse seit letztem Watchlist-Check zurück."""
    init_db()
    with _conn() as db:
        row = db.execute(
            "SELECT checked_ts FROM watchlist WHERE term=?", (term,)
        ).fetchone()
        since = (row["checked_ts"] if row and row["checked_ts"]
                 else (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())
        cnt = db.execute(
            "SELECT COUNT(*) as c FROM events WHERE query=? AND ts>=?",
            (term, since)
        ).fetchone()
    return cnt["c"] if cnt else 0


# ── Watchlist ────────────────────────────────────────────────────────────────

def wl_add(term: str) -> bool:
    """Fügt Begriff zur Watchlist hinzu."""
    init_db()
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with _conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO watchlist (term,added_ts) VALUES (?,?)",
                (term.strip(), ts)
            )
            db.commit()
        return True
    except Exception:
        return False


def wl_remove(term: str) -> None:
    init_db()
    with _conn() as db:
        db.execute("DELETE FROM watchlist WHERE term=?", (term.strip(),))
        db.commit()


def wl_list() -> list[dict]:
    init_db()
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM watchlist WHERE active=1 ORDER BY added_ts DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def wl_mark_checked(term: str, new_alerts: int = 0) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            "UPDATE watchlist SET checked_ts=?, alert_cnt=alert_cnt+? WHERE term=?",
            (ts, new_alerts, term)
        )
        db.commit()


# ── Wartung ──────────────────────────────────────────────────────────────────

def cleanup(days: int = 30) -> int:
    """Löscht Ereignisse älter als X Tage. Gibt Anzahl gelöschter Einträge zurück."""
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as db:
        cur = db.execute("DELETE FROM events WHERE ts<?", (cutoff,))
        db.commit()
        return cur.rowcount


# ── Direktaufruf zum Testen ──────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    store_event("test", "news", "Testmeldung Ukraine", lat=50.45, lon=30.52,
                source="Test", url="https://example.com")
    store_event("test", "flight_alert", "GAF123 – Militärischer Callsign",
                lat=48.1, lon=11.6, source="OpenSky", severity=1)
    print(get_trend_text(days=1))
    print(f"\nGeo-Events: {len(get_geo_events(hours=1))}")
    print(f"\nDB: {DB_PATH}")
