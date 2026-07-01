"""Database connection management and schema definition.

By default the app uses local SQLite (stdlib ``sqlite3``). When the environment
provides ``TURSO_DATABASE_URL`` and ``TURSO_AUTH_TOKEN``, the same schema and
repositories run against Turso (libSQL) instead, via a thin sqlite3-compatible
proxy so no other module changes. The libSQL driver is imported lazily, so its
absence never affects the SQLite path or the test suite.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.request
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
# Turso (libSQL) over the HTTP API ("Hrana v2 pipeline")
#
# We talk to Turso via plain HTTPS (stdlib urllib) instead of a compiled driver,
# so there is nothing to build on the host — it installs everywhere Python does.
# The classes below present a minimal sqlite3-compatible surface so repository.py
# / audit.py (which use ``conn.execute(...).fetchone()["col"]``, ``cur.lastrowid``
# and ``conn.executescript(...)``) work unchanged. Turso speaks SQLite SQL, so no
# query changes are needed either. Each statement autocommits; writes are durable
# on Turso immediately (no local replica to lose).
# ---------------------------------------------------------------------------
class _Row:
    """Row supporting positional (``row[0]``) and name (``row["col"]``) access,
    like :class:`sqlite3.Row`."""

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


def _py_to_arg(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode("ascii")}
    return {"type": "text", "value": str(v)}


def _arg_to_py(cell: dict):
    t = cell.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(cell.get("value"))
    if t == "float":
        return float(cell.get("value"))
    if t == "blob":
        return base64.b64decode(cell.get("base64", ""))
    return cell.get("value")


class _TursoResult:
    def __init__(self, result: dict) -> None:
        self._cols = [c.get("name") for c in (result.get("cols") or [])]
        self._rows = result.get("rows") or []
        self._i = 0
        lir = result.get("last_insert_rowid")
        self.lastrowid = int(lir) if lir not in (None, "") else None
        self.description = [(c,) for c in self._cols] if self._cols else None

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        row = self._rows[self._i]
        self._i += 1
        return _Row(self._cols, [_arg_to_py(c) for c in row])

    def fetchall(self):
        rows = [
            _Row(self._cols, [_arg_to_py(c) for c in row])
            for row in self._rows[self._i:]
        ]
        self._i = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class _TursoConn:
    """Minimal sqlite3-compatible connection backed by the Turso HTTP pipeline."""

    def __init__(self, http_base: str, auth_token: str) -> None:
        self._url = http_base.rstrip("/") + "/v2/pipeline"
        self._token = auth_token

    def _pipeline(self, requests: list[dict]) -> dict:
        body = json.dumps({"requests": requests}).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for r in data.get("results", []):
            if r.get("type") == "error":
                raise sqlite3.OperationalError(
                    "Turso: " + json.dumps(r.get("error", {}), ensure_ascii=False)
                )
        return data

    def execute(self, sql: str, params: tuple = ()):
        args = [_py_to_arg(p) for p in (params or ())]
        data = self._pipeline(
            [{"type": "execute", "stmt": {"sql": sql, "args": args}}, {"type": "close"}]
        )
        result = data["results"][0]["response"]["result"]
        return _TursoResult(result)

    def executescript(self, sql: str):
        stmts = [s.strip() for s in sql.split(";") if s.strip()]
        reqs = [{"type": "execute", "stmt": {"sql": s}} for s in stmts]
        reqs.append({"type": "close"})
        self._pipeline(reqs)
        return self

    def commit(self) -> None:
        # Each pipeline autocommits on Turso; nothing to flush.
        pass

    def close(self) -> None:
        pass


def _turso_http_base(database_url: str) -> str:
    """Map a libSQL connection URL to its HTTPS endpoint."""
    url = database_url.strip()
    for prefix in ("libsql://", "wss://", "ws://", "http://"):
        if url.startswith(prefix):
            return "https://" + url[len(prefix):]
    if url.startswith("https://"):
        return url
    return "https://" + url


def _connect_turso(database_url: str, auth_token: str):
    return _TursoConn(_turso_http_base(database_url), auth_token)


def active_backend() -> str:
    """Human-readable name of the backend that ``connect()`` will use, based on
    the current environment. Used by the UI to make the active store visible."""
    if os.environ.get("TURSO_DATABASE_URL", "").strip() and os.environ.get(
        "TURSO_AUTH_TOKEN", ""
    ).strip():
        return "Turso (libSQL)"
    return "SQLite local"


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
