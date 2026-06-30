"""Database connection management and schema definition.

By default the app uses local SQLite (stdlib ``sqlite3``). When the environment
provides ``TURSO_DATABASE_URL`` and ``TURSO_AUTH_TOKEN``, the same schema and
repositories run against Turso (libSQL) instead, via a thin sqlite3-compatible
proxy so no other module changes. The libSQL driver is imported lazily, so its
absence never affects the SQLite path or the test suite.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS expedientes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL UNIQUE,
    caratula        TEXT NOT NULL,
    cliente         TEXT NOT NULL,
    escribano       TEXT NOT NULL DEFAULT '',
    tipo_acto       TEXT NOT NULL DEFAULT '',
    fecha_apertura  TEXT,
    notas           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS advances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id   INTEGER NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    fecha           TEXT NOT NULL,
    monto_centavos  INTEGER NOT NULL,
    metodo          TEXT NOT NULL DEFAULT '',
    referencia      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS expenses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id     INTEGER NOT NULL REFERENCES expedientes(id) ON DELETE CASCADE,
    fecha             TEXT NOT NULL,
    monto_centavos    INTEGER NOT NULL,
    categoria         TEXT NOT NULL,
    concepto          TEXT NOT NULL DEFAULT '',
    estado            TEXT NOT NULL DEFAULT 'pending',
    pagado_por        TEXT NOT NULL DEFAULT 'escribania',
    proveedor         TEXT NOT NULL DEFAULT '',
    referencia        TEXT NOT NULL DEFAULT '',
    categoria_origen  TEXT NOT NULL DEFAULT 'manual',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bank_movements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha             TEXT NOT NULL,
    monto_centavos    INTEGER NOT NULL,
    kind              TEXT NOT NULL,
    descripcion       TEXT NOT NULL DEFAULT '',
    contraparte       TEXT NOT NULL DEFAULT '',
    referencia_banco  TEXT NOT NULL DEFAULT '',
    cuenta            TEXT NOT NULL DEFAULT '',
    expediente_id     INTEGER REFERENCES expedientes(id) ON DELETE SET NULL,
    asignacion_origen TEXT NOT NULL DEFAULT 'manual',
    dedupe_key        TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_movements_dedupe
    ON bank_movements(dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_id   INTEGER NOT NULL REFERENCES bank_movements(id) ON DELETE CASCADE,
    target_type   TEXT NOT NULL,
    target_id     INTEGER NOT NULL,
    score         TEXT NOT NULL DEFAULT '0',
    status        TEXT NOT NULL DEFAULT 'suggested',
    rationale     TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_match_unique
    ON matches(movement_id, target_type, target_id);

CREATE TABLE IF NOT EXISTS review_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id INTEGER REFERENCES expedientes(id) ON DELETE CASCADE,
    tipo          TEXT NOT NULL,
    severity      TEXT NOT NULL,
    mensaje       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    contexto      TEXT NOT NULL DEFAULT '',
    dedupe_key    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT,
    resolved_by   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_review_dedupe
    ON review_items(dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS expediente_status (
    expediente_id INTEGER PRIMARY KEY REFERENCES expedientes(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    reasons       TEXT NOT NULL DEFAULT '[]',
    summary_json  TEXT NOT NULL DEFAULT '{}',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT NOT NULL,                 -- 'expediente' | 'cartera'
    expediente_id INTEGER REFERENCES expedientes(id) ON DELETE CASCADE,
    facts_hash    TEXT NOT NULL,
    content_json  TEXT NOT NULL,
    origen        TEXT NOT NULL DEFAULT 'fallback',
    model         TEXT NOT NULL DEFAULT '',
    grounded      INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_agent_lookup
    ON agent_analyses(scope, expediente_id, facts_hash, id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL DEFAULT 'system',
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    prev_hash   TEXT NOT NULL DEFAULT '',
    hash        TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# libSQL / Turso compatibility proxy
#
# Makes a libSQL connection look enough like a stdlib sqlite3 connection that
# repository.py / audit.py (which use ``conn.execute(...).fetchone()["col"]``,
# ``cur.lastrowid`` and ``conn.executescript(...)``) work unchanged.
# ---------------------------------------------------------------------------
class _Row:
    """Row supporting both positional (``row[0]``) and name (``row["col"]``)
    access, like :class:`sqlite3.Row`."""

    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: list[str], vals: list) -> None:
        self._cols = cols
        self._vals = list(vals)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._cols.index(key)]

    def keys(self) -> list[str]:
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)


class _Cursor:
    def __init__(self, raw) -> None:
        self._raw = raw

    def _cols(self) -> list[str]:
        desc = getattr(self._raw, "description", None) or []
        return [d[0] for d in desc]

    @property
    def lastrowid(self):
        return getattr(self._raw, "lastrowid", None)

    @property
    def description(self):
        return getattr(self._raw, "description", None)

    def fetchone(self):
        row = self._raw.fetchone()
        if row is None:
            return None
        return _Row(self._cols(), row)

    def fetchall(self):
        cols = self._cols()
        return [_Row(cols, r) for r in self._raw.fetchall()]

    def __iter__(self):
        cols = self._cols()
        for r in self._raw.fetchall():
            yield _Row(cols, r)


class _ConnProxy:
    """sqlite3-compatible facade over a libSQL connection. ``commit()`` also
    syncs the embedded replica up to Turso so writes are durable."""

    def __init__(self, raw) -> None:
        self._raw = raw

    def execute(self, sql: str, params: tuple = ()):  # noqa: D401
        return _Cursor(self._raw.execute(sql, params))

    def executescript(self, sql: str):
        # libSQL exposes executescript on the connection; fall back to splitting.
        if hasattr(self._raw, "executescript"):
            self._raw.executescript(sql)
        else:  # pragma: no cover - defensive
            for stmt in (s.strip() for s in sql.split(";")):
                if stmt:
                    self._raw.execute(stmt)
        return self

    def commit(self) -> None:
        self._raw.commit()
        sync = getattr(self._raw, "sync", None)
        if callable(sync):
            try:
                sync()
            except Exception:
                pass

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass


def _connect_turso(database_url: str, auth_token: str):
    import libsql_experimental as libsql  # lazy: only when Turso is configured

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    replica = str(config.DATA_DIR / "turso_replica.db")
    raw = libsql.connect(replica, sync_url=database_url, auth_token=auth_token)
    # Pull the latest state from Turso on startup (fresh container = empty replica).
    try:
        raw.sync()
    except Exception:
        pass
    raw.execute("PRAGMA foreign_keys = ON")
    return _ConnProxy(raw)


def connect(db_path: Optional[Path] = None):
    """Open a connection. Uses Turso (libSQL) when ``TURSO_DATABASE_URL`` and
    ``TURSO_AUTH_TOKEN`` are set; otherwise local SQLite (the default)."""
    turso_url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    turso_token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if turso_url and turso_token:
        return _connect_turso(turso_url, turso_token)

    path = Path(db_path) if db_path else config.SETTINGS.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the connection is cached and reused across
    # Streamlit's per-rerun threads. Streamlit serialises script runs per
    # session and writes here are short, so this is safe for the expected
    # single-office concurrency; WAL mode keeps reads/writes consistent.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Drop all data (used by tests and the 'reset demo' action)."""
    tables = [
        "audit_log",
        "agent_analyses",
        "expediente_status",
        "review_items",
        "matches",
        "bank_movements",
        "expenses",
        "advances",
        "expedientes",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    init_db(conn)
