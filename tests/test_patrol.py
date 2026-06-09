"""Tests for nexus_patrol.py (T174 — Pattern-of-Life Engine)."""
import pytest


def test_import():
    import nexus_patrol  # noqa: F401


def test_dataclasses_exist():
    from nexus_patrol import PatrolAnomaly, PatrolReport
    assert PatrolAnomaly is not None
    assert PatrolReport is not None


def test_mad_function():
    """MAD (Median Absolute Deviation) — basic math check."""
    from nexus_patrol import _mad
    values = [10.0, 12.0, 11.0, 100.0, 10.5]  # 100 is outlier
    mad = _mad(values)
    # Median = 11, deviations = [1,1,0,89,0.5], MAD = 1
    assert 0.0 <= mad <= 5.0  # should be small — MAD is robust to outlier


def test_mad_empty():
    from nexus_patrol import _mad
    assert _mad([]) == 0.0


def test_mad_single():
    from nexus_patrol import _mad
    assert _mad([42.0]) == 0.0


def test_patrol_summary_no_crash(tmp_db, monkeypatch):
    """patrol_summary() returns a dict even with empty DB."""
    import nexus_timeseries as ts
    import nexus_patrol as pt
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    result = pt.patrol_summary()
    assert isinstance(result, dict)


def test_patrol_for_map_no_crash(tmp_db, monkeypatch):
    """patrol_for_map() returns a list even with no data."""
    import nexus_timeseries as ts
    import nexus_patrol as pt
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    result = pt.patrol_for_map()
    assert isinstance(result, list)


def test_patrol_anomalies_no_crash(tmp_db, monkeypatch):
    """patrol_anomalies() returns PatrolReport without crashing on empty DB."""
    import nexus_timeseries as ts
    import nexus_patrol as pt
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    report = pt.patrol_anomalies(max_entities=10)
    assert hasattr(report, "anomalies")
    assert isinstance(report.anomalies, list)


def test_compute_baseline_insufficient_data(tmp_db, monkeypatch):
    """Baseline returns None when fewer than 5 data points exist."""
    import nexus_timeseries as ts
    import nexus_patrol as pt
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    # Only 3 positions — below minimum
    for i in range(3):
        ts.record_position("TEST_MMSI", "vessel", 26.3 + i*0.01, 56.7,
                           speed=10.0, heading=180, region="Hormuz", meta={})
    baseline = pt._compute_baseline("TEST_MMSI")
    assert baseline is None  # Not enough data


def test_no_false_positives_fresh_db(tmp_db, monkeypatch):
    """No anomalies should be generated when there is no baseline data."""
    import nexus_timeseries as ts
    import nexus_patrol as pt
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    # Add a few recent positions — insufficient for baseline
    for i in range(4):
        ts.record_position("FRESH_SHIP", "vessel", 26.0, 56.0 + i*0.1,
                           speed=8.0, heading=270, region="Gulf", meta={})
    report = pt.patrol_anomalies(max_entities=50)
    fresh_anomalies = [a for a in report.anomalies if a.entity_id == "FRESH_SHIP"]
    assert len(fresh_anomalies) == 0  # No baseline → no anomalies
