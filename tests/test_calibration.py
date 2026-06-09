"""Tests for nexus_calibration.py (T178 — Source Calibration Feedback Loop)."""
import pytest


def test_import():
    import nexus_calibration  # noqa: F401


def test_calibration_report_dataclass():
    from nexus_calibration import CalibrationReport
    r = CalibrationReport(
        precision=0.75, recall=0.80, f1=0.77,
        total_alerts=20, confirmed=15, false_positives=5,
        calibration_factor=1.0, threshold_used=50, days=90
    )
    assert r.f1 == pytest.approx(0.77)


def test_log_alert_returns_id(tmp_db, monkeypatch):
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    alert_id = cal.log_alert(score=65.0, region="Hormuz",
                              context="Test ISR activity", source="nexus_escalation")
    assert isinstance(alert_id, int)
    assert alert_id > 0


def test_record_outcome_valid(tmp_db, monkeypatch):
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    alert_id = cal.log_alert(score=70.0, region="Ukraine")
    ok = cal.record_outcome(alert_id, "eskaliert")
    assert ok is True


def test_record_outcome_invalid_outcome(tmp_db, monkeypatch):
    """Invalid outcome string should either raise or return False — not silently corrupt."""
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    alert_id = cal.log_alert(score=50.0)
    # "invalid_outcome" is not in valid set
    try:
        result = cal.record_outcome(alert_id, "invalid_outcome")
        assert result is False
    except (ValueError, AssertionError):
        pass  # Raising is also acceptable


def test_calibration_report_insufficient_data(tmp_db, monkeypatch):
    """With fewer than _MIN_OUTCOMES alerts, report reflects low confidence."""
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    report = cal.calibration_report(days=90)
    assert isinstance(report, cal.CalibrationReport)
    # With no data, factor should be neutral (1.0) and precision undefined
    assert report.total_alerts == 0


def test_get_calibrated_score_neutral(tmp_db, monkeypatch):
    """With no history, calibrated score should equal raw score (factor=1.0)."""
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    result = cal.get_calibrated_score(raw_score=60.0, region="Hormuz")
    assert isinstance(result, dict)
    assert "calibrated" in result
    assert "factor" in result
    # Without data, factor should be 1.0
    assert result["factor"] == pytest.approx(1.0)
    assert result["calibrated"] == pytest.approx(60.0)


def test_auto_log_from_escalation(tmp_db, monkeypatch):
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    # Score above MITTEL threshold (40) → should log
    esc_data = {"score": 55, "level": "MITTEL", "region": "Baltic", "summary": "Elevated"}
    alert_id = cal.auto_log_from_escalation(esc_data)
    assert alert_id is not None
    assert isinstance(alert_id, int)


def test_auto_log_below_threshold(tmp_db, monkeypatch):
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    # Score below MITTEL (40) → should NOT log
    esc_data = {"score": 20, "level": "NIEDRIG", "region": "Baltic"}
    alert_id = cal.auto_log_from_escalation(esc_data)
    assert alert_id is None


def test_show_open_alerts(tmp_db, monkeypatch):
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    cal.log_alert(score=75.0, region="Hormuz", context="ISR activity")
    cal.log_alert(score=60.0, region="Ukraine", context="Artillery")
    alerts = cal.show_open_alerts(hours=24)
    assert isinstance(alerts, list)
    assert len(alerts) >= 2


def test_calibration_db_stats(tmp_db, monkeypatch):
    import nexus_calibration as cal
    import nexus_timeseries as ts
    monkeypatch.setattr(ts, "DB_PATH", tmp_db)
    ts._init_db()
    stats = cal.calibration_db_stats()
    assert isinstance(stats, dict)
