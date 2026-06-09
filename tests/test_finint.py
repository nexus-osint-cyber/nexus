"""Tests for nexus_finint.py (T177 — FININT)."""
import pytest


def test_import():
    import nexus_finint  # noqa: F401


def test_finint_result_dataclass():
    from nexus_finint import FinintResult
    r = FinintResult(
        name="Test Entity", risk_level="CLEAN",
        sanctions_hits=[], company_hits=[], blockchain_hits=[],
        shell_score=0.0, notes=[]
    )
    assert r.risk_level == "CLEAN"


def test_finint_summary_no_crash(tmp_db, monkeypatch):
    """finint_summary() returns dict even with empty DB."""
    import nexus_finint as fi
    monkeypatch.setattr(fi, "OFAC_DB_PATH", tmp_db)
    result = fi.finint_summary()
    assert isinstance(result, dict)


def test_check_entity_clean(tmp_db, monkeypatch):
    """A clearly clean entity returns CLEAN when DB has no entries."""
    import nexus_finint as fi
    monkeypatch.setattr(fi, "OFAC_DB_PATH", tmp_db)
    # With empty DB, no sanctions hits possible
    result = fi.check_entity("Apple Inc", check_blockchain=False, check_companies=False)
    assert result.risk_level == "CLEAN"
    assert len(result.sanctions_hits) == 0


def test_bulk_check_empty():
    import nexus_finint as fi
    results = fi.bulk_check([])
    assert results == []


def test_bulk_check_returns_list(tmp_db, monkeypatch):
    import nexus_finint as fi
    monkeypatch.setattr(fi, "OFAC_DB_PATH", tmp_db)
    names = ["Company A", "Company B", "Company C"]
    results = fi.bulk_check(names)
    assert len(results) == 3
    for r in results:
        assert "name" in r
        assert "risk_level" in r


def test_finint_for_map_empty_ships(tmp_db, monkeypatch):
    import nexus_finint as fi
    monkeypatch.setattr(fi, "OFAC_DB_PATH", tmp_db)
    alerts = fi.finint_for_map(entities=[])
    assert isinstance(alerts, list)


def test_finint_for_map_vessel_list(tmp_db, monkeypatch):
    import nexus_finint as fi
    monkeypatch.setattr(fi, "OFAC_DB_PATH", tmp_db)
    ships = [
        {"mmsi": "123456789", "shipname": "TEST VESSEL", "lat": 26.0, "lon": 56.0},
        {"mmsi": "987654321", "shipname": "CLEAN SHIP",  "lat": 27.0, "lon": 57.0},
    ]
    alerts = fi.finint_for_map(entities=ships)
    assert isinstance(alerts, list)
    # With empty OFAC DB no alerts expected
    assert len(alerts) == 0


def test_ofac_cache_init(tmp_db, monkeypatch):
    """OFAC DB is created when initialised."""
    import nexus_finint as fi
    import sqlite3, os
    monkeypatch.setattr(fi, "OFAC_DB_PATH", tmp_db)
    fi._init_ofac_db()
    assert os.path.exists(tmp_db)
    con = sqlite3.connect(tmp_db)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "sdn_entries" in tables
    con.close()
