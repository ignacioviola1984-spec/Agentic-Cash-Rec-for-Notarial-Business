"""Status recomputation and deterministic review-item generation.

``recompute`` is the single entry point that, for one expediente:
  1. loads its advances/expenses/movements,
  2. computes the exact financial summary (domain engine),
  3. refreshes auto-managed review items from explicit rules,
  4. classifies status (domain classifier), and
  5. persists the snapshot and records an audit entry when status changes.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from .. import config_proxy as _cfg
from ..data import audit, repository as repo
from ..domain.engine import ExpedienteSummary, compute_summary
from ..domain.models import ReviewItem, ReviewSeverity, ReviewStatus
from ..domain.money import ZERO, format_ars
from ..domain.status import classify
from .serialization import summary_to_json


# Review rules that recompute owns (auto-created and auto-resolved).
_AUTO_TIPOS = (
    "pago_sin_anticipo",      # blocking
    "dato_invalido",          # blocking
    "financiamiento",         # warning
    "anticipo_insuficiente",  # warning
    "movimientos_sin_conciliar",  # warning
    "devolver_excedente",     # info
)


def _open_item(conn: sqlite3.Connection, expediente_id: int, tipo: str):
    rows = repo.list_review_items(conn, expediente_id=expediente_id, only_open=True)
    for it in rows:
        if it.tipo == tipo:
            return it
    return None


def _ensure(conn, expediente_id, tipo, severity, mensaje, contexto, present: bool, actor: str):
    existing = _open_item(conn, expediente_id, tipo)
    if present and existing is None:
        rid = repo.add_review_item(
            conn,
            ReviewItem(
                expediente_id=expediente_id,
                tipo=tipo,
                severity=severity,
                mensaje=mensaje,
                contexto=json.dumps(contexto, ensure_ascii=False),
            ),
        )
        audit.record(
            conn,
            action="review_open",
            entity="review_item",
            entity_id=rid or "",
            payload={"expediente_id": expediente_id, "tipo": tipo, "severity": severity.value},
            actor=actor,
        )
    elif not present and existing is not None:
        repo.set_review_status(conn, existing.id, ReviewStatus.RESOLVED, resolved_by="system-auto")
        audit.record(
            conn,
            action="review_auto_resolved",
            entity="review_item",
            entity_id=existing.id,
            payload={"expediente_id": expediente_id, "tipo": tipo},
            actor=actor,
        )


def _refresh_reviews(conn, expediente_id, summary: ExpedienteSummary, unmatched: int, actor: str):
    th = _cfg.thresholds()

    # Blocking: expenses disbursed by the escribanía with zero client funding.
    _ensure(
        conn, expediente_id, "pago_sin_anticipo", ReviewSeverity.BLOCKING,
        "La escribanía pagó gastos sin haber recibido ningún anticipo del cliente.",
        {"gastos_pagados": str(summary.gastos_pagados)},
        present=(summary.total_recibido <= ZERO and summary.gastos_pagados > ZERO),
        actor=actor,
    )

    # Blocking: negative aggregate (data integrity problem).
    _ensure(
        conn, expediente_id, "dato_invalido", ReviewSeverity.BLOCKING,
        "Se detectaron montos negativos agregados; revisar los datos cargados.",
        {"total_recibido": str(summary.total_recibido), "costo": str(summary.costo_recuperable)},
        present=(summary.total_recibido < ZERO or summary.costo_recuperable < ZERO),
        actor=actor,
    )

    # Warning: escribanía financing above the risk threshold.
    _ensure(
        conn, expediente_id, "financiamiento", ReviewSeverity.WARNING,
        f"La escribanía financia {format_ars(summary.monto_financiado)} al cliente.",
        {"monto_financiado": str(summary.monto_financiado)},
        present=(summary.financiando and summary.monto_financiado >= th.financing_risk_amount),
        actor=actor,
    )

    # Warning: advance insufficient with pending expenses.
    _ensure(
        conn, expediente_id, "anticipo_insuficiente", ReviewSeverity.WARNING,
        f"Anticipo insuficiente; saldo a cobrar {format_ars(summary.saldo_a_cobrar)}.",
        {"saldo_a_cobrar": str(summary.saldo_a_cobrar)},
        present=(not summary.anticipo_suficiente and summary.gastos_pendientes > ZERO),
        actor=actor,
    )

    # Warning: bank movements assigned but not reconciled.
    _ensure(
        conn, expediente_id, "movimientos_sin_conciliar", ReviewSeverity.WARNING,
        f"{unmatched} movimiento(s) bancario(s) sin conciliar.",
        {"unmatched": unmatched},
        present=(unmatched > 0),
        actor=actor,
    )

    # Info: surplus to refund once everything is settled.
    _ensure(
        conn, expediente_id, "devolver_excedente", ReviewSeverity.INFO,
        f"Excedente a devolver al cliente: {format_ars(summary.excedente_a_devolver)}.",
        {"excedente": str(summary.excedente_a_devolver)},
        present=(summary.excedente_a_devolver > ZERO and summary.gastos_pendientes == ZERO),
        actor=actor,
    )


def recompute(conn: sqlite3.Connection, expediente_id: int, *, actor: str = "system") -> dict:
    advances = repo.list_advances(conn, expediente_id)
    expenses = repo.list_expenses(conn, expediente_id)
    movements = repo.list_movements(conn, expediente_id)
    confirmed_mv = repo.confirmed_movement_ids(conn)
    unmatched = [m for m in movements if m.id not in confirmed_mv]

    summary = compute_summary(expediente_id, advances, expenses)
    _refresh_reviews(conn, expediente_id, summary, len(unmatched), actor)

    open_blocking = repo.count_open_blocking(conn, expediente_id)
    result = classify(
        summary, open_blocking_reviews=open_blocking, unmatched_movements=len(unmatched)
    )

    previous = repo.get_status_row(conn, expediente_id)
    prev_status = previous["status"] if previous else None
    repo.upsert_status(
        conn,
        expediente_id,
        result.status.value,
        json.dumps(result.reasons, ensure_ascii=False),
        summary_to_json(summary),
    )
    if prev_status != result.status.value:
        audit.record(
            conn,
            action="status_changed",
            entity="expediente",
            entity_id=expediente_id,
            payload={"from": prev_status, "to": result.status.value, "reasons": result.reasons},
            actor=actor,
        )
    return {"status": result.status, "reasons": result.reasons, "summary": summary,
            "unmatched": len(unmatched)}


def recompute_all(conn: sqlite3.Connection, *, actor: str = "system") -> int:
    expedientes = repo.list_expedientes(conn)
    for e in expedientes:
        recompute(conn, e.id, actor=actor)
    return len(expedientes)
