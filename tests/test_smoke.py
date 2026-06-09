"""Smoke tests — all 6 new modules import cleanly and expose required public API."""
import importlib
import pytest

REQUIRED_API = {
    "nexus_timeseries": [
        "record_signal", "record_position", "record_alert",
        "record_outcome", "query_range", "get_entity_history",
        "compute_delta", "get_score_trend", "record_refresh_snapshot",
    ],
    "nexus_patrol": [
        "patrol_anomalies", "patrol_for_map", "patrol_summary",
        "_compute_baseline", "_mad",
    ],
    "nexus_nlp": [
        "detect_language", "translate_to_en", "extract_entities",
        "extract_locations", "analyze_text",
        "enrich_telegram_messages", "enrich_gdelt_events",
    ],
    "nexus_darkweb": [
        "_check_tor", "fetch_onion", "darkweb_scan",
        "darkweb_for_map", "darkweb_summary", "tor_status",
    ],
    "nexus_finint": [
        "check_entity", "check_ship", "bulk_check",
        "finint_for_map", "finint_summary",
        "_update_ofac_cache",
    ],
    "nexus_calibration": [
        "log_alert", "record_outcome", "calibration_report",
        "get_calibrated_score", "auto_log_from_escalation",
        "show_open_alerts", "calibration_db_stats",
    ],
}


@pytest.mark.parametrize("module_name,functions", REQUIRED_API.items())
def test_module_api(module_name, functions):
    """Each module exposes its required public functions."""
    mod = importlib.import_module(module_name)
    missing = [f for f in functions if not hasattr(mod, f)]
    assert missing == [], f"{module_name} is missing: {missing}"


@pytest.mark.parametrize("module_name", REQUIRED_API.keys())
def test_module_no_import_error(module_name):
    """Module imports without raising any exception."""
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.skip(f"Optional dependency missing: {e}")
    except Exception as e:
        pytest.fail(f"{module_name} raised {type(e).__name__}: {e}")
