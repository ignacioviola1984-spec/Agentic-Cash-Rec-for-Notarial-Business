"""Read-model assembly for the UI and reports.

Builds a complete, deterministic view of an expediente or the whole portfolio.
Summaries are computed live from stored source rows; the status label is read
from the persisted snapshot (kept current by ``status_service.recompute``).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..data import repository as repo
from ..domain.engine import ExpedienteSummary, compute_summary
from ..domain.models import (
    Advance,
    BankMovement,
    Expediente,
    Expense,
    ExpedienteStatus,
    Match,
    MatchStatus,
    ReviewItem,
)
from ..domain.money import ZERO


@dataclass
class ExpedienteView:
    expediente: Expediente
    summary: ExpedienteSummary
    status: ExpedienteStatus
    reasons: list[str]
    advances: list[Advance]
    expenses: list[Expense]
    movements: list[BankMovement]
    matches: list[Match]
    reviews: list[ReviewItem]
    unmatched_movements: list[BankMovement] = field(default_factory=list)


def _status_from_snapshot(conn, expediente_id) -> tuple[ExpedienteStatus, list[str]]:
    row = repo.get_status_row(conn, expediente_id)
    if not row:
        return ExpedienteStatus.OK, []
    try:
        reasons = json.loads(row["reasons"])
    except Exception:
        reasons = []
    try:
        status = ExpedienteStatus(row["status"])
    except ValueError:
        status = ExpedienteStatus.OK
    return status, reasons


def build_expediente_view(conn: sqlite3.Connection, expediente_id: int) -> Optional[ExpedienteView]:
    exp = repo.get_expediente(conn, expediente_id)
    if not exp:
        return None
    advances = repo.list_advances(conn, expediente_id)
    expenses = repo.list_expenses(conn, expediente_id)
    movements = repo.list_movements(conn, expediente_id)
    matches = repo.list_matches_for_expediente(conn, expediente_id)
    reviews = repo.list_review_items(conn, expediente_id=expediente_id)
    summary = compute_summary(expediente_id, advances, expenses)
    status, reasons = _status_from_snapshot(conn, expediente_id)

    confirmed_mv = repo.confirmed_movement_ids(conn)
    unmatched = [m for m in movements if m.id not in confirmed_mv]

    return ExpedienteView(
        expediente=exp, summary=summary, status=status, reasons=reasons,
        advances=advances, expenses=expenses, movements=movements, matches=matches,
        reviews=reviews, unmatched_movements=unmatched,
    )


@dataclass
class PortfolioRow:
    expediente: Expediente
    status: ExpedienteStatus
    summary: ExpedienteSummary
    open_reviews: int
    unmatched: int


@dataclass
class Portfolio:
    rows: list[PortfolioRow]
    total_recibido: Decimal
    total_costo: Decimal
    total_financiado: Decimal
    total_a_cobrar: Decimal
    status_counts: dict[str, int]


def build_portfolio(conn: sqlite3.Connection) -> Portfolio:
    rows: list[PortfolioRow] = []
    total_recibido = ZERO
    total_costo = ZERO
    total_financiado = ZERO
    total_a_cobrar = ZERO
    status_counts = {s.value: 0 for s in ExpedienteStatus}
    confirmed_mv = repo.confirmed_movement_ids(conn)

    for exp in repo.list_expedientes(conn):
        advances = repo.list_advances(conn, exp.id)
        expenses = repo.list_expenses(conn, exp.id)
        movements = repo.list_movements(conn, exp.id)
        summary = compute_summary(exp.id, advances, expenses)
        status, _ = _status_from_snapshot(conn, exp.id)
        open_reviews = len(repo.list_review_items(conn, expediente_id=exp.id, only_open=True))
        unmatched = sum(1 for m in movements if m.id not in confirmed_mv)

        total_recibido += summary.total_recibido
        total_costo += summary.costo_recuperable
        total_financiado += summary.monto_financiado
        total_a_cobrar += summary.saldo_a_cobrar
        status_counts[status.value] = status_counts.get(status.value, 0) + 1

        rows.append(PortfolioRow(exp, status, summary, open_reviews, unmatched))

    order = {
        ExpedienteStatus.BLOQUEADO: 0,
        ExpedienteStatus.RIESGO: 1,
        ExpedienteStatus.ATENCION: 2,
        ExpedienteStatus.OK: 3,
    }
    rows.sort(key=lambda r: (order[r.status], r.expediente.codigo))

    return Portfolio(
        rows=rows,
        total_recibido=total_recibido,
        total_costo=total_costo,
        total_financiado=total_financiado,
        total_a_cobrar=total_a_cobrar,
        status_counts=status_counts,
    )
