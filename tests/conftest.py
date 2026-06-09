"""Shared fixtures for NEXUS tests.

All tests run OFFLINE — no real API calls, no real DB writes to production.
Each test that needs a DB gets a fresh temporary file.
"""
import os
import sys
import tempfile
import pytest

# Add project root to path so imports work from tests/ subdir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def tmp_db(tmp_path):
    """Return path to a temporary SQLite database file."""
    return str(tmp_path / "test_nexus.db")


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temporary directory path (as str)."""
    return str(tmp_path)
