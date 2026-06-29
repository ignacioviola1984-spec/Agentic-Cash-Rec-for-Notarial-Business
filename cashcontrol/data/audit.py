"""Tamper-evident, hash-chained audit log.

Every state-changing action appends one row whose ``hash`` is
``sha256(prev_hash + canonical_payload)``. Because each entry commits to its
predecessor, altering or deleting any historical row invalidates every hash that
follows it, which :func:`verify_chain` detects.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _compute_hash(prev_hash: str, body: dict[str, Any]) -> str:
    payload = prev_hash + _canonical(body)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return row["hash"] if row else ""


def record(
    conn: sqlite3.Connection,
    *,
    action: str,
    entity: str,
    entity_id: str | int = "",
    payload: Optional[dict[str, Any]] = None,
    actor: str = "system",
    commit: bool = True,
) -> dict[str, Any]:
    """Append an audit entry and return its persisted fields."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev_hash = _last_hash(conn)
    body = {
        "ts": ts,
        "actor": actor,
        "action": action,
        "entity": entity,
        "entity_id": str(entity_id),
        "payload": payload or {},
    }
    entry_hash = _compute_hash(prev_hash, body)
    conn.execute(
        """
        INSERT INTO audit_log (ts, actor, action, entity, entity_id, payload, prev_hash, hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            actor,
            action,
            entity,
            str(entity_id),
            _canonical(body["payload"]),
            prev_hash,
            entry_hash,
        ),
    )
    if commit:
        conn.commit()
    return {**body, "prev_hash": prev_hash, "hash": entry_hash}


def verify_chain(conn: sqlite3.Connection) -> tuple[bool, Optional[int]]:
    """Recompute the chain. Returns ``(ok, first_broken_row_id)``."""
    prev_hash = ""
    rows = conn.execute(
        "SELECT id, ts, actor, action, entity, entity_id, payload, prev_hash, hash "
        "FROM audit_log ORDER BY id ASC"
    ).fetchall()
    for row in rows:
        body = {
            "ts": row["ts"],
            "actor": row["actor"],
            "action": row["action"],
            "entity": row["entity"],
            "entity_id": row["entity_id"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
        }
        expected = _compute_hash(prev_hash, body)
        if row["prev_hash"] != prev_hash or row["hash"] != expected:
            return False, row["id"]
        prev_hash = row["hash"]
    return True, None
