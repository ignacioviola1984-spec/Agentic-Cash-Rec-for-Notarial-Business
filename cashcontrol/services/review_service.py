"""Human review workflow: resolve or dismiss review items.

Only the human-actionable transitions live here. Auto-managed items (created and
cleared by :mod:`status_service`) can still be force-resolved by a reviewer; the
next recompute will recreate them if the underlying condition persists, which is
the correct, honest behaviour.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ..data import audit, repository as repo
from ..domain.models import ReviewStatus
from . import status_service


def resolve(conn: sqlite3.Connection, review_id: int, *, actor: str = "reviewer", note: str = "") -> None:
    item = _get(conn, review_id)
    repo.set_review_status(conn, review_id, ReviewStatus.RESOLVED, resolved_by=actor)
    audit.record(
        conn,
        action="review_resolved",
        entity="review_item",
        entity_id=review_id,
        payload={"note": note, "tipo": item.tipo if item else None},
        actor=actor,
    )
    if item and item.expediente_id:
        status_service.recompute(conn, item.expediente_id, actor=actor)


def dismiss(conn: sqlite3.Connection, review_id: int, *, actor: str = "reviewer", note: str = "") -> None:
    item = _get(conn, review_id)
    repo.set_review_status(conn, review_id, ReviewStatus.DISMISSED, resolved_by=actor)
    audit.record(
        conn,
        action="review_dismissed",
        entity="review_item",
        entity_id=review_id,
        payload={"note": note, "tipo": item.tipo if item else None},
        actor=actor,
    )
    if item and item.expediente_id:
        status_service.recompute(conn, item.expediente_id, actor=actor)


def _get(conn: sqlite3.Connection, review_id: int):
    for it in repo.list_review_items(conn):
        if it.id == review_id:
            return it
    return None
