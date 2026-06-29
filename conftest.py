"""Pytest configuration: ensure the repo root is importable and provide a
fresh, isolated SQLite connection per test."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cashcontrol.data import db  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    database = tmp_path / "test.db"
    connection = db.connect(database)
    db.init_db(connection)
    yield connection
    connection.close()
