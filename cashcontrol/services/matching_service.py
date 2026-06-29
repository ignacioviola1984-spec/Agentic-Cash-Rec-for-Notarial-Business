"""Reconciliation orchestration: generate deterministic suggestions, let a human
confirm/reject them, and suggest expediente assignment for unassigned movements.

HITL guarantees:
  * Suggestions are never auto-confirmed. A confirmation is an explicit human act.
  * Unassigned movements are only *suggested* an expediente; assignment requires
    a human action through :func:`assign_movement_to_expediente`.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ..data import audit, repository as repo
from ..domain.matching import suggest_matches
from ..domain.models import MatchStatus, MatchTargetType
from ..llm.client import get_client
from . import status_service


def generate_suggestions(conn: sqlite3.Connection, expediente_id: int, *, actor: str = "system") -> int:
    movements = repo.list_movements(conn, expediente_id)
    advances = repo.list_advances(conn, expediente_id)
    expenses = repo.list_expenses(conn, expediente_id)
    confirmed = repo.confirmed_target_keys(conn)

    suggestions = suggest_matches(
        movements, advances, expenses, already_matched_targets=confirmed
    )
    created = 0
    for m in suggestions:
        repo.upsert_suggested_match(conn, m)
        created += 1
    if created:
        audit.record(
            conn,
            action="matches_suggested",
            entity="expediente",
            entity_id=expediente_id,
            payload={"count": created},
            actor=actor,
        )
    return created


def generate_all_suggestions(conn: sqlite3.Connection, *, actor: str = "system") -> int:
    total = 0
    for e in repo.list_expedientes(conn):
        total += generate_suggestions(conn, e.id, actor=actor)
    return total


def _expediente_of_match(conn: sqlite3.Connection, match_id: int) -> Optional[int]:
    m = repo.get_match(conn, match_id)
    if not m:
        return None
    mov = repo.get_movement(conn, m.movement_id)
    return mov.expediente_id if mov else None


def confirm_match(conn: sqlite3.Connection, match_id: int, *, actor: str = "reviewer") -> None:
    m = repo.get_match(conn, match_id)
    if not m:
        return
    repo.set_match_status(conn, match_id, MatchStatus.CONFIRMED)
    audit.record(
        conn,
        action="match_confirmed",
        entity="match",
        entity_id=match_id,
        payload={
            "movement_id": m.movement_id,
            "target_type": m.target_type.value,
            "target_id": m.target_id,
            "score": str(m.score),
        },
        actor=actor,
    )
    exp_id = _expediente_of_match(conn, match_id)
    if exp_id:
        status_service.recompute(conn, exp_id, actor=actor)


def reject_match(conn: sqlite3.Connection, match_id: int, *, actor: str = "reviewer") -> None:
    m = repo.get_match(conn, match_id)
    if not m:
        return
    repo.set_match_status(conn, match_id, MatchStatus.REJECTED)
    audit.record(
        conn,
        action="match_rejected",
        entity="match",
        entity_id=match_id,
        payload={"movement_id": m.movement_id},
        actor=actor,
    )
    exp_id = _expediente_of_match(conn, match_id)
    if exp_id:
        status_service.recompute(conn, exp_id, actor=actor)


def suggest_assignments(conn: sqlite3.Connection) -> list[dict]:
    """For each unassigned movement, deterministically/LLM-suggest an expediente.

    Returns dicts with the movement and the suggested code+origen. Nothing is
    mutated; assignment stays a human action.
    """
    movements = repo.list_unassigned_movements(conn)
    expedientes = repo.list_expedientes(conn)
    catalog = [(e.codigo, e.caratula, e.cliente) for e in expedientes]
    code_to_id = {e.codigo: e.id for e in expedientes}
    client = get_client()

    out: list[dict] = []
    for mov in movements:
        text = " ".join([mov.descripcion, mov.contraparte, mov.referencia_banco])
        codigo, origen = client.suggest_expediente(text, catalog)
        out.append(
            {
                "movement": mov,
                "suggested_codigo": codigo,
                "suggested_expediente_id": code_to_id.get(codigo) if codigo else None,
                "origen": origen,
            }
        )
    return out


def assign_movement_to_expediente(
    conn: sqlite3.Connection,
    movement_id: int,
    expediente_id: Optional[int],
    *,
    origen: str = "manual",
    actor: str = "reviewer",
) -> None:
    repo.assign_movement(conn, movement_id, expediente_id, origen=origen)
    audit.record(
        conn,
        action="movement_assigned",
        entity="bank_movement",
        entity_id=movement_id,
        payload={"expediente_id": expediente_id, "origen": origen},
        actor=actor,
    )
    if expediente_id:
        generate_suggestions(conn, expediente_id, actor=actor)
        status_service.recompute(conn, expediente_id, actor=actor)
