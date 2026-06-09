"""
nexus_timeseries.py  — T173
SQLite WAL-Mode Zeitreihendatenbank für alle NEXUS-Signale.

Tabellen:
  signals          — allgemeine Signale (Score, Sensor-Readings, Counts)
  entity_positions — Schiffs- und Flugzeug-Positionen über Zeit
  alerts           — ausgelöste Alerts mit Outcome-Tracking (für Calibration)

Kernfunktionen:
  record_signal(source, key, value, region, meta)
  record_position(entity_id, entity_type, lat, lon, speed, heading, meta)
  record_alert(source, level, score, region, context)
  query_range(source, key, hours)
  get_entity_history(entity_id, hours)
  compute_delta(source, key, hours_back, hours_window)
  get_score_trend(region, hours)
  escalation_history(region, hours)

Verwendung:
  from nexus_timeseries import record_signal, query_range, get_entity_history
  python nexus_timeseries.py --show-scores --region hormuz --hours 48
"""

from __future__ import annotations

import json
import sqlite3
import time
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ─── Datenbank-Pfad ───────────────────────────────────────────────────────────
_DB_DIR  = Path(__file__).parent / "nexus_data"
_DB_DIR.mkdir(exist_ok=True)
DB_PATH  = _DB_DIR / "nexus_timeseries.db"

# ─── Schema ───────────────────────────────────────────────────────────────────
_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-32000;

CREATE TABLE IF NOT EXISTS signals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,          -- Unix-Timestamp (float)
    source    TEXT    NOT NULL,          -- z.B. "nexus_escalation", "nexus_ais"
    key       TEXT    NOT NULL,          -- z.B. "escalation_score", "ship_count"
    value     REAL    NOT NULL,
    region    TEXT    DEFAULT '',        -- z.B. "hormuz", "taiwan", ""
    meta      TEXT    DEFAULT '{}'       -- JSON für Zusatzinfos
);

CREATE INDEX IF NOT EXISTS idx_signals_ts_src_key
    ON signals(ts DESC, source, key);
CREATE INDEX IF NOT EXISTS idx_signals_region
    ON signals(region, ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entity_positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    entity_id   TEXT    NOT NULL,        -- MMSI, Callsign, Name
    entity_type TEXT    NOT NULL,        -- "ship", "aircraft", "person"
    lat         REAL    NOT NULL,
    lon         REAL    NOT NULL,
    speed       REAL    DEFAULT 0.0,
    heading     REAL    DEFAULT 0.0,
    region      TEXT    DEFAULT '',
    meta        TEXT    DEFAULT '{}'     -- JSON: name, flag, type, etc.
);

CREATE INDEX IF NOT EXISTS idx_entity_pos_id_ts
    ON entity_positions(entity_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_entity_pos_ts
    ON entity_positions(ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    source      TEXT    NOT NULL,        -- "nexus_escalation", "nexus_patrol", etc.
    level       TEXT    NOT NULL,        -- "KRITISCH","HOCH","MITTEL","NIEDRIG"
    score       REAL    DEFAULT 0.0,
    region      TEXT    DEFAULT '',
    context     TEXT    DEFAULT '',      -- Kurzbeschreibung was ausgelöst hat
    outcome     TEXT    DEFAULT NULL,    -- NULL = offen, "eskaliert", "nicht_eskaliert"
    outcome_ts  REAL    DEFAULT NULL,    -- Wann Outcome eingetragen wurde
    outcome_note TEXT   DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_region ON alerts(region, ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_outcome ON alerts(outcome);
"""

# ─── Verbindungsmanager ───────────────────────────────────────────────────────

@contextmanager
def _conn():
    """Thread-safe SQLite-Verbindung mit WAL."""
    con = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    """Erstellt Tabellen falls nicht vorhanden."""
    with _conn() as con:
        con.executescript(_SCHEMA)


# Einmalig beim Import initialisieren
init_db()

# ─── Schreibfunktionen ────────────────────────────────────────────────────────

def record_signal(
    source: str,
    key: str,
    value: float,
    region: str = "",
    meta: Optional[dict] = None,
    ts: Optional[float] = None,
) -> int:
    """
    Speichert ein Signal.
    Gibt die neue Zeilen-ID zurück.

    Beispiel:
        record_signal("nexus_escalation", "escalation_score", 72.5, "hormuz")
        record_signal("nexus_ais", "ship_count", 14, "hormuz", {"tanker": 6})
    """
    now = ts or time.time()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO signals(ts, source, key, value, region, meta) "
            "VALUES(?,?,?,?,?,?)",
            (now, source, key, float(value), region, meta_json),
        )
        return cur.lastrowid


def record_position(
    entity_id: str,
    entity_type: str,
    lat: float,
    lon: float,
    speed: float = 0.0,
    heading: float = 0.0,
    region: str = "",
    meta: Optional[dict] = None,
    ts: Optional[float] = None,
) -> int:
    """
    Speichert eine Entitäts-Position.
    entity_type: "ship" | "aircraft" | "person"

    Beispiel:
        record_position("123456789", "ship", 26.5, 56.2, speed=14.2,
                        region="hormuz", meta={"name":"USNS ALAN SHEPARD"})
    """
    now = ts or time.time()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO entity_positions"
            "(ts, entity_id, entity_type, lat, lon, speed, heading, region, meta) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (now, entity_id, entity_type, lat, lon, speed, heading, region, meta_json),
        )
        return cur.lastrowid


def record_alert(
    source: str,
    level: str,
    score: float,
    region: str = "",
    context: str = "",
    ts: Optional[float] = None,
) -> int:
    """
    Speichert einen Alert für späteres Calibration-Tracking.
    Gibt alert_id zurück (wird für record_outcome() benötigt).

    Beispiel:
        aid = record_alert("nexus_escalation", "HOCH", 78.3,
                           "hormuz", "P-8A + USNS detected")
    """
    now = ts or time.time()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO alerts(ts, source, level, score, region, context) "
            "VALUES(?,?,?,?,?,?)",
            (now, source, level, float(score), region, context),
        )
        return cur.lastrowid


def record_outcome(
    alert_id: int,
    outcome: str,
    note: str = "",
) -> bool:
    """
    Trägt Outcome für einen Alert ein.
    outcome: "eskaliert" | "nicht_eskaliert" | "unklar"

    Beispiel:
        record_outcome(42, "eskaliert", "Hormuz-Durchfahrt blockiert am 2024-03-15")
    """
    allowed = {"eskaliert", "nicht_eskaliert", "unklar"}
    if outcome not in allowed:
        raise ValueError(f"outcome muss einer von {allowed} sein")
    with _conn() as con:
        con.execute(
            "UPDATE alerts SET outcome=?, outcome_ts=?, outcome_note=? WHERE id=?",
            (outcome, time.time(), note, alert_id),
        )
        return True


# ─── Lesefunktionen ───────────────────────────────────────────────────────────

def query_range(
    source: str,
    key: str,
    hours: float = 24,
    region: str = "",
) -> list[dict]:
    """
    Gibt alle Signal-Einträge der letzten N Stunden zurück.

    Beispiel:
        scores = query_range("nexus_escalation", "escalation_score", 48, "hormuz")
        # → [{"ts": 1718300000.0, "value": 72.5, "region": "hormuz", ...}, ...]
    """
    cutoff = time.time() - hours * 3600
    with _conn() as con:
        if region:
            rows = con.execute(
                "SELECT * FROM signals WHERE source=? AND key=? AND region=? AND ts>=? "
                "ORDER BY ts ASC",
                (source, key, region, cutoff),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM signals WHERE source=? AND key=? AND ts>=? "
                "ORDER BY ts ASC",
                (source, key, cutoff),
            ).fetchall()
    return [dict(r) for r in rows]


def get_entity_history(
    entity_id: str,
    hours: float = 48,
) -> list[dict]:
    """
    Gibt Positions-History einer Entität zurück.

    Beispiel:
        track = get_entity_history("123456789", 72)
        # → [{"ts": ..., "lat": 26.5, "lon": 56.2, "speed": 14.2, ...}, ...]
    """
    cutoff = time.time() - hours * 3600
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM entity_positions WHERE entity_id=? AND ts>=? ORDER BY ts ASC",
            (entity_id, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_signal(
    source: str,
    key: str,
    region: str = "",
) -> Optional[dict]:
    """Gibt den aktuellsten Signal-Eintrag zurück."""
    with _conn() as con:
        if region:
            row = con.execute(
                "SELECT * FROM signals WHERE source=? AND key=? AND region=? "
                "ORDER BY ts DESC LIMIT 1",
                (source, key, region),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT * FROM signals WHERE source=? AND key=? "
                "ORDER BY ts DESC LIMIT 1",
                (source, key),
            ).fetchone()
    return dict(row) if row else None


# ─── Analyse-Funktionen ───────────────────────────────────────────────────────

def compute_delta(
    source: str,
    key: str,
    hours_back: float = 48,
    hours_window: float = 6,
    region: str = "",
) -> dict:
    """
    Vergleicht Mittelwert der letzten hours_window Stunden
    vs. Mittelwert von (hours_back - hours_window) bis hours_window.

    Gibt zurück:
        {"current_mean": float, "baseline_mean": float,
         "delta_abs": float, "delta_pct": float, "trend": "steigend"|"fallend"|"stabil"}

    Beispiel:
        d = compute_delta("nexus_escalation", "escalation_score", 48, 6, "hormuz")
        if d["delta_pct"] > 20:
            print(f"Score ist {d['delta_pct']:.0f}% über 48h-Baseline!")
    """
    now = time.time()
    window_start = now - hours_window * 3600
    baseline_start = now - hours_back * 3600
    baseline_end = window_start

    with _conn() as con:
        if region:
            cur_rows = con.execute(
                "SELECT value FROM signals WHERE source=? AND key=? AND region=? AND ts>=?",
                (source, key, region, window_start),
            ).fetchall()
            base_rows = con.execute(
                "SELECT value FROM signals WHERE source=? AND key=? AND region=? "
                "AND ts>=? AND ts<?",
                (source, key, region, baseline_start, baseline_end),
            ).fetchall()
        else:
            cur_rows = con.execute(
                "SELECT value FROM signals WHERE source=? AND key=? AND ts>=?",
                (source, key, window_start),
            ).fetchall()
            base_rows = con.execute(
                "SELECT value FROM signals WHERE source=? AND key=? AND ts>=? AND ts<?",
                (source, key, baseline_start, baseline_end),
            ).fetchall()

    def mean(rows):
        vals = [r[0] for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    cur_mean  = mean(cur_rows)
    base_mean = mean(base_rows)
    delta_abs = cur_mean - base_mean
    delta_pct = (delta_abs / base_mean * 100) if base_mean > 0 else 0.0

    if delta_pct > 10:
        trend = "steigend"
    elif delta_pct < -10:
        trend = "fallend"
    else:
        trend = "stabil"

    return {
        "current_mean":  round(cur_mean,  2),
        "baseline_mean": round(base_mean, 2),
        "delta_abs":     round(delta_abs, 2),
        "delta_pct":     round(delta_pct, 1),
        "trend":         trend,
        "n_current":     len(cur_rows),
        "n_baseline":    len(base_rows),
    }


def get_score_trend(region: str, hours: float = 48) -> list[dict]:
    """
    Gibt stündliche Durchschnitts-Eskalations-Scores für eine Region zurück.
    Nützlich für Livemap-Charts.

    Gibt zurück: [{"hour": "2024-03-15 14:00", "mean_score": 72.5, "n": 6}, ...]
    """
    cutoff = time.time() - hours * 3600
    with _conn() as con:
        rows = con.execute(
            "SELECT ts, value FROM signals "
            "WHERE source='nexus_escalation' AND key='escalation_score' "
            "AND region=? AND ts>=? ORDER BY ts ASC",
            (region, cutoff),
        ).fetchall()

    if not rows:
        return []

    # Gruppieren nach Stunden-Bucket
    buckets: dict[str, list[float]] = {}
    for row in rows:
        dt = datetime.fromtimestamp(row[0], tz=timezone.utc)
        bucket = dt.strftime("%Y-%m-%d %H:00")
        buckets.setdefault(bucket, []).append(row[1])

    return [
        {"hour": h, "mean_score": round(sum(v) / len(v), 1), "n": len(v)}
        for h, v in sorted(buckets.items())
    ]


def escalation_history(
    region: str = "",
    hours: float = 72,
    level_filter: Optional[str] = None,
) -> list[dict]:
    """
    Gibt Alert-History mit optionalem Level-Filter zurück.

    Beispiel:
        highs = escalation_history("hormuz", 72, "HOCH")
    """
    cutoff = time.time() - hours * 3600
    with _conn() as con:
        if region and level_filter:
            rows = con.execute(
                "SELECT * FROM alerts WHERE region=? AND level=? AND ts>=? ORDER BY ts DESC",
                (region, level_filter, cutoff),
            ).fetchall()
        elif region:
            rows = con.execute(
                "SELECT * FROM alerts WHERE region=? AND ts>=? ORDER BY ts DESC",
                (region, cutoff),
            ).fetchall()
        elif level_filter:
            rows = con.execute(
                "SELECT * FROM alerts WHERE level=? AND ts>=? ORDER BY ts DESC",
                (level_filter, cutoff),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM alerts WHERE ts>=? ORDER BY ts DESC",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def open_alerts(hours: float = 168) -> list[dict]:
    """Gibt alle Alerts ohne Outcome zurück (offen = noch nicht verifiziert)."""
    cutoff = time.time() - hours * 3600
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM alerts WHERE outcome IS NULL AND ts>=? ORDER BY ts DESC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def db_stats() -> dict:
    """Gibt Datenbank-Statistiken zurück."""
    with _conn() as con:
        n_signals   = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        n_positions = con.execute("SELECT COUNT(*) FROM entity_positions").fetchone()[0]
        n_alerts    = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        n_open      = con.execute("SELECT COUNT(*) FROM alerts WHERE outcome IS NULL").fetchone()[0]
        oldest_sig  = con.execute("SELECT MIN(ts) FROM signals").fetchone()[0]
        newest_sig  = con.execute("SELECT MAX(ts) FROM signals").fetchone()[0]

    def fmt_ts(ts):
        if ts is None:
            return "—"
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    db_size_mb = round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0

    return {
        "db_path":         str(DB_PATH),
        "db_size_mb":      db_size_mb,
        "n_signals":       n_signals,
        "n_positions":     n_positions,
        "n_alerts":        n_alerts,
        "n_alerts_open":   n_open,
        "oldest_signal":   fmt_ts(oldest_sig),
        "newest_signal":   fmt_ts(newest_sig),
    }


# ─── Convenience: Bulk-Record für Live-Server ─────────────────────────────────

def record_refresh_snapshot(data: dict) -> None:
    """
    Wird von nexus_live_server.py nach jedem Refresh aufgerufen.
    Speichert alle relevanten Scores/Counts aus dem data-Dict in die DB.

    data ist das Pipeline-Ergebnis von nexus_live_server.py
    (enthält escalation_report, ais_ships, flights, etc.)
    """
    now = time.time()

    # Eskalations-Score (global + pro Region)
    try:
        esc = data.get("escalation_report", {})
        global_score = esc.get("combined_score", 0)
        record_signal("nexus_escalation", "escalation_score", global_score, "", ts=now)
        record_signal("nexus_escalation", "escalation_level",
                      _level_to_int(esc.get("level", "NORMAL")), "", ts=now)

        for region, rscore in esc.get("region_scores", {}).items():
            record_signal("nexus_escalation", "escalation_score",
                          rscore, region, ts=now)
    except Exception:
        pass

    # AIS Schiff-Count
    try:
        ships = data.get("ais_ships", [])
        record_signal("nexus_ais", "ship_count", len(ships), "", ts=now)
        # Pro Region
        region_counts: dict[str, int] = {}
        for s in ships:
            r = s.get("region", "")
            if r:
                region_counts[r] = region_counts.get(r, 0) + 1
        for r, cnt in region_counts.items():
            record_signal("nexus_ais", "ship_count", cnt, r, ts=now)
    except Exception:
        pass

    # Flug-Count
    try:
        flights = data.get("flights", [])
        record_signal("nexus_flights", "flight_count", len(flights), "", ts=now)
    except Exception:
        pass

    # SAR Schiffe
    try:
        sar = data.get("sar_results", [])
        total_sar = sum(r.get("count", 0) for r in sar)
        record_signal("nexus_sar", "ship_count", total_sar, "", ts=now)
    except Exception:
        pass

    # ACLED Events
    try:
        acled = data.get("acled_events", [])
        record_signal("nexus_acled", "event_count", len(acled), "", ts=now)
    except Exception:
        pass

    # Schiffs-Positionen (MMSI-basiert)
    try:
        for ship in data.get("ais_ships", []):
            mmsi = str(ship.get("mmsi") or ship.get("MMSI") or "")
            lat  = float(ship.get("lat") or ship.get("LATITUDE") or 0)
            lon  = float(ship.get("lon") or ship.get("LONGITUDE") or 0)
            if mmsi and lat and lon:
                record_position(
                    entity_id=mmsi,
                    entity_type="ship",
                    lat=lat, lon=lon,
                    speed=float(ship.get("speed") or ship.get("SOG") or 0),
                    region=ship.get("region", ""),
                    meta={"name": ship.get("name") or ship.get("NAME", ""),
                           "flag": ship.get("flag", "")},
                    ts=now,
                )
    except Exception:
        pass

    # Flugzeug-Positionen
    try:
        for fl in data.get("flights", []):
            cs = str(fl.get("callsign") or fl.get("icao24") or "")
            lat = float(fl.get("lat") or fl.get("latitude") or 0)
            lon = float(fl.get("lon") or fl.get("longitude") or 0)
            if cs and lat and lon:
                record_position(
                    entity_id=cs,
                    entity_type="aircraft",
                    lat=lat, lon=lon,
                    speed=float(fl.get("speed") or fl.get("velocity") or 0),
                    heading=float(fl.get("heading") or fl.get("true_track") or 0),
                    region=fl.get("region", ""),
                    meta={"role": fl.get("role", ""),
                           "type": fl.get("aircraft_type", "")},
                    ts=now,
                )
    except Exception:
        pass


def _level_to_int(level: str) -> float:
    """Wandelt Eskalations-Level in numerischen Wert für Graphen."""
    return {"NORMAL": 0, "NIEDRIG": 1, "MITTEL": 2, "HOCH": 3, "KRITISCH": 4}.get(
        level.upper(), 0
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS Zeitreihendatenbank")
    parser.add_argument("--stats",        action="store_true", help="DB-Statistiken anzeigen")
    parser.add_argument("--show-scores",  action="store_true", help="Eskalations-Scores anzeigen")
    parser.add_argument("--show-alerts",  action="store_true", help="Offene Alerts anzeigen")
    parser.add_argument("--show-trend",   action="store_true", help="Stündlichen Score-Trend zeigen")
    parser.add_argument("--show-entity",  metavar="ENTITY_ID", help="Positions-History einer Entität")
    parser.add_argument("--delta",        action="store_true", help="Score-Delta (aktuelle vs. Baseline)")
    parser.add_argument("--record-outcome", metavar="ALERT_ID", type=int, help="Outcome für Alert eintragen")
    parser.add_argument("--outcome",      choices=["eskaliert","nicht_eskaliert","unklar"])
    parser.add_argument("--note",         default="", help="Notiz zum Outcome")
    parser.add_argument("--region",       default="", help="Region-Filter")
    parser.add_argument("--hours",        type=float, default=48, help="Zeitfenster in Stunden")
    parser.add_argument("--test-insert",  action="store_true", help="Test-Datenpunkte einfügen")
    args = parser.parse_args()

    if args.test_insert:
        print("Füge Test-Datenpunkte ein...")
        for i in range(10):
            ts_offset = time.time() - (9 - i) * 3600
            record_signal("nexus_escalation", "escalation_score",
                          50 + i * 3.5, "hormuz", ts=ts_offset)
            record_signal("nexus_ais", "ship_count", 8 + i, "hormuz", ts=ts_offset)
        aid = record_alert("nexus_escalation", "HOCH", 81.5, "hormuz",
                           "Test: P-8A + USNS detected")
        print(f"Test-Alert ID: {aid}")
        record_position("123456789", "ship", 26.5, 56.2, speed=14.2,
                        region="hormuz", meta={"name": "USNS TEST SHIP"})
        print("Fertig.")

    if args.stats:
        stats = db_stats()
        print("\n=== NEXUS Zeitreihen-DB ===")
        for k, v in stats.items():
            print(f"  {k:20s}: {v}")

    if args.show_scores:
        rows = query_range("nexus_escalation", "escalation_score",
                           args.hours, args.region)
        print(f"\n=== Eskalations-Scores ({args.region or 'alle'}, letzte {args.hours}h) ===")
        for r in rows[-20:]:
            dt = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            reg = f" [{r['region']}]" if r["region"] else ""
            print(f"  {dt}{reg}  Score: {r['value']:.1f}")

    if args.show_trend:
        trend = get_score_trend(args.region, args.hours)
        print(f"\n=== Stündlicher Score-Trend ({args.region or 'alle'}) ===")
        for t in trend:
            bar = "█" * int(t["mean_score"] / 5)
            print(f"  {t['hour']}  {t['mean_score']:5.1f}  {bar}")

    if args.delta:
        d = compute_delta("nexus_escalation", "escalation_score",
                          args.hours, 6, args.region)
        print(f"\n=== Score-Delta ({args.region or 'global'}) ===")
        print(f"  Aktuelle 6h:    {d['current_mean']:.1f} (n={d['n_current']})")
        print(f"  Baseline {args.hours}h:  {d['baseline_mean']:.1f} (n={d['n_baseline']})")
        print(f"  Delta:          {d['delta_abs']:+.1f} ({d['delta_pct']:+.1f}%)")
        print(f"  Trend:          {d['trend'].upper()}")

    if args.show_entity:
        history = get_entity_history(args.show_entity, args.hours)
        print(f"\n=== Positions-History: {args.show_entity} ===")
        for p in history[-20:]:
            dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            m = json.loads(p["meta"])
            name = m.get("name", "")
            print(f"  {dt}  {p['lat']:.4f},{p['lon']:.4f}  "
                  f"Speed={p['speed']:.1f}kn  {name}")

    if args.show_alerts:
        alerts = open_alerts(args.hours)
        print(f"\n=== Offene Alerts (letzte {args.hours}h) ===")
        if not alerts:
            print("  Keine offenen Alerts.")
        for a in alerts:
            dt = datetime.fromtimestamp(a["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            print(f"  [{a['id']:4d}] {dt}  {a['level']:8s}  "
                  f"Score={a['score']:.0f}  [{a['region']}]  {a['context'][:60]}")

    if args.record_outcome:
        if not args.outcome:
            parser.error("--outcome ist erforderlich wenn --record-outcome gesetzt ist")
        record_outcome(args.record_outcome, args.outcome, args.note)
        print(f"Outcome '{args.outcome}' für Alert #{args.record_outcome} gespeichert.")
