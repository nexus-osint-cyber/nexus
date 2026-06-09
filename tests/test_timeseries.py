"""Tests for nexus_timeseries.py (T173 — SQLite WAL time-series DB)."""
import time
import pytest


# ─── Import guard ────────────────────────────────────────────────────────────

def test_import():
    """Module imports without error."""
    import nexus_timeseries  # noqa: F401


# ─── DB initialisation ───────────────────────────────────────────────────────

def test_db_init(tmp_db, monkeypatch):
    """Database file is created and tables exist."""
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    import sqlite3
    con = sqlite3.connect(tmp_db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "signals" in tables
    assert "entity_positions" in tables
    assert "alerts" in tables
    con.close()


def test_wal_mode(tmp_db, monkeypatch):
    """Database uses WAL journal mode."""
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    import sqlite3
    con = sqlite3.connect(tmp_db)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    con.close()


# ─── record_signal ────────────────────────────────────────────────────────────

def test_record_signal_returns_int(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    row_id = ts.record_signal("test_source", "score", 42.0, region="TestRegion")
    assert isinstance(row_id, int)
    assert row_id > 0


def test_record_signal_stored(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    ts.record_signal("ais", "ship_count", 7, region="Hormuz")
    rows = ts.query_range("ais", "ship_count", hours=1, region="Hormuz")
    assert len(rows) >= 1
    assert rows[-1]["value"] == 7.0


def test_record_signal_meta(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    ts.record_signal("radar", "ping", 1.0, meta={"freq": "X-band"})
    rows = ts.query_range("radar", "ping", hours=1)
    assert len(rows) >= 1
    # meta should round-trip as dict or None (implementation-dependent)


# ─── record_position ─────────────────────────────────────────────────────────

def test_record_position(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    row_id = ts.record_position(
        entity_id="123456789", entity_type="vessel",
        lat=26.3, lon=56.7, speed=12.5, heading=180,
        region="Hormuz", meta={}, ts=None
    )
    assert isinstance(row_id, int)
    history = ts.get_entity_history("123456789", hours=1)
    assert len(history) >= 1
    assert abs(history[0]["lat"] - 26.3) < 0.001


# ─── record_alert & record_outcome ───────────────────────────────────────────

def test_alert_lifecycle(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    alert_id = ts.record_alert(
        source="escalation", level="HOCH", score=74.0,
        region="Hormuz", context="Test alert"
    )
    assert isinstance(alert_id, int) and alert_id > 0
    ok = ts.record_outcome(alert_id, "eskaliert", note="Verified via MarineTraffic")
    assert ok is True


def test_record_outcome_invalid_id(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    # Non-existent alert — should return False, not raise
    result = ts.record_outcome(999999, "eskaliert")
    assert result is False


# ─── query_range ─────────────────────────────────────────────────────────────

def test_query_range_empty(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    rows = ts.query_range("nonexistent_source", "nonexistent_key", hours=24)
    assert rows == []


def test_query_range_region_filter(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    ts.record_signal("ais", "count", 5.0, region="Hormuz")
    ts.record_signal("ais", "count", 8.0, region="Baltic")
    hormuz = ts.query_range("ais", "count", hours=1, region="Hormuz")
    baltic = ts.query_range("ais", "count", hours=1, region="Baltic")
    assert len(hormuz) == 1
    assert len(baltic) == 1
    assert hormuz[0]["value"] == 5.0
    assert baltic[0]["value"] == 8.0


# ─── compute_delta ────────────────────────────────────────────────────────────

def test_compute_delta_insufficient_data(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    result = ts.compute_delta("ais", "score", hours_back=48, hours_window=6)
    # Should return a dict with a "sufficient_data" key or similar, not crash
    assert isinstance(result, dict)


# ─── record_refresh_snapshot ─────────────────────────────────────────────────

def test_record_refresh_snapshot(tmp_db, monkeypatch):
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    snapshot = {
        "escalation_report": {"region": "Hormuz", "score": 55, "level": "MITTEL"},
        "ais_ships": [],
        "milflights": [],
    }
    # Should not raise
    ts.record_refresh_snapshot(snapshot)
