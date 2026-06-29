"""Deterministic status classification.

Maps a computed :class:`ExpedienteSummary` (plus review/matching context) to one
of OK / Atencion / Riesgo / Bloqueado. The rules are explicit, ordered and
threshold-driven; the LLM is never consulted here. Each rule emits a
machine-generated Spanish reason so the UI can explain *why* without inventing
anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .. import config_proxy as _cfg
from .engine import ExpedienteSummary
from .models import ExpedienteStatus
from .money import ZERO, format_ars


@dataclass(frozen=True)
class StatusResult:
    status: ExpedienteStatus
    reasons: list[str] = field(default_factory=list)


def classify(
    summary: ExpedienteSummary,
    *,
    open_blocking_reviews: int = 0,
    unmatched_movements: int = 0,
) -> StatusResult:
    """Classify financial health. Severity is evaluated highest-first."""
    th = _cfg.thresholds()
    reasons: list[str] = []

    # --- BLOQUEADO -------------------------------------------------------
    if open_blocking_reviews > 0:
        return StatusResult(
            ExpedienteStatus.BLOQUEADO,
            [
                f"{open_blocking_reviews} revisión(es) bloqueante(s) abierta(s) "
                "que requieren acción humana antes de operar."
            ],
        )

    # --- RIESGO ----------------------------------------------------------
    risk_reasons: list[str] = []
    if summary.financiando and summary.monto_financiado >= th.financing_risk_amount:
        risk_reasons.append(
            "La escribanía está financiando al cliente por "
            f"{format_ars(summary.monto_financiado)} (supera el umbral de riesgo "
            f"{format_ars(th.financing_risk_amount)})."
        )
    if summary.saldo_a_cobrar >= th.balance_to_collect_risk_amount:
        risk_reasons.append(
            f"Saldo a cobrar de {format_ars(summary.saldo_a_cobrar)} supera el "
            f"umbral de riesgo {format_ars(th.balance_to_collect_risk_amount)}."
        )
    if (
        summary.costo_recuperable > ZERO
        and summary.cobertura < th.funding_attention_ratio
        and summary.gastos_pendientes > ZERO
    ):
        risk_reasons.append(
            f"Cobertura del anticipo {(summary.cobertura * 100):.0f}% por debajo "
            f"del mínimo con gastos pendientes de {format_ars(summary.gastos_pendientes)}."
        )
    if risk_reasons:
        return StatusResult(ExpedienteStatus.RIESGO, risk_reasons)

    # --- ATENCION --------------------------------------------------------
    if summary.financiando:
        reasons.append(
            "La escribanía está financiando al cliente por "
            f"{format_ars(summary.monto_financiado)}."
        )
    if not summary.anticipo_suficiente:
        reasons.append(
            f"El anticipo no alcanza: saldo a cobrar {format_ars(summary.saldo_a_cobrar)}."
        )
    if (
        summary.gastos_pendientes > ZERO
        and summary.fondos_disponibles < summary.gastos_pendientes
    ):
        reasons.append(
            f"Fondos disponibles {format_ars(summary.fondos_disponibles)} no cubren "
            f"los gastos pendientes {format_ars(summary.gastos_pendientes)}."
        )
    if unmatched_movements > 0:
        reasons.append(
            f"{unmatched_movements} movimiento(s) bancario(s) sin conciliar."
        )
    if reasons:
        return StatusResult(ExpedienteStatus.ATENCION, reasons)

    # --- OK --------------------------------------------------------------
    ok_reason = "Anticipo suficiente, sin financiamiento ni gastos pendientes descubiertos."
    if summary.excedente_a_devolver > ZERO:
        ok_reason += f" Excedente a devolver: {format_ars(summary.excedente_a_devolver)}."
    return StatusResult(ExpedienteStatus.OK, [ok_reason])
