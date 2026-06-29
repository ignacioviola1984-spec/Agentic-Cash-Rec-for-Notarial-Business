"""Application bootstrap: ensure directories, open/initialise the database."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import config
from ..data import db


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    config.ensure_dirs()
    conn = db.connect(db_path)
    db.init_db(conn)
    return conn


def is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM expedientes").fetchone()
    return int(row["n"]) == 0
