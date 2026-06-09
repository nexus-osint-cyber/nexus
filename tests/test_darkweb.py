"""Tests for nexus_darkweb.py (T176 — Dark Web Monitoring, offline-safe)."""
import pytest


def test_import():
    import nexus_darkweb  # noqa: F401


def test_tor_status_returns_dict():
    from nexus_darkweb import tor_status
    status = tor_status()
    assert isinstance(status, dict)
    assert "tor_online" in status


def test_tor_status_offline():
    """In test environment Tor is not running — should return tor_online=False."""
    from nexus_darkweb import tor_status
    status = tor_status()
    # We don't require it to be False (might run in CI with Tor),
    # but it must be a bool
    assert isinstance(status["tor_online"], bool)


def test_darkweb_summary_no_tor():
    """darkweb_summary() should return a safe dict even when Tor is offline."""
    from nexus_darkweb import darkweb_summary, tor_status
    if tor_status()["tor_online"]:
        pytest.skip("Tor is online — offline test not applicable")
    result = darkweb_summary()
    assert isinstance(result, dict)
    # Should indicate Tor is offline
    assert result.get("tor_online") is False or "tor_online" not in result


def test_darkweb_for_map_no_tor():
    """darkweb_for_map() returns empty list when Tor is offline (no blocking)."""
    from nexus_darkweb import darkweb_for_map, tor_status
    if tor_status()["tor_online"]:
        pytest.skip("Tor is online — offline test not applicable")
    result = darkweb_for_map()
    assert isinstance(result, list)
    assert result == []


def test_darkweb_scan_no_tor():
    """darkweb_scan() returns DarkwebReport even when Tor is offline."""
    from nexus_darkweb import darkweb_scan, DarkwebReport, tor_status
    if tor_status()["tor_online"]:
        pytest.skip("Tor is online — offline test not applicable")
    report = darkweb_scan(max_sources=1)
    assert hasattr(report, "findings") or isinstance(report, DarkwebReport)


def test_check_tor_function():
    from nexus_darkweb import _check_tor
    result = _check_tor()
    assert isinstance(result, bool)
