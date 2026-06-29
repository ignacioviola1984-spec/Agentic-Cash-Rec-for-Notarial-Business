"""Deterministic balance engine.

Given the raw financial entities of a single expediente, this module computes —
with exact Decimal arithmetic and no LLM involvement — every figure the product
must answer:

  * How much was received from the client
  * Which expenses were paid by the escribanía on the client's behalf
  * Which expenses remain pending
  * Whether the client's advance is sufficient
  * Whether the escribanía is financing the client (and by how much)
  * Which balance remains to be collected (or refunded)

The output :class:`ExpedienteSummary` is a pure function of its inputs, which
makes it fully testable and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from .. import config_proxy as _cfg  # late-bound settings access
from .models import Advance, Expense, ExpenseStatus, PaidBy
from .money import ZERO, money_sum, quantize


@dataclass(frozen=True)
class ExpedienteSummary:
    expediente_id: int

    # Inflows
    total_recibido: Decimal              # advances received from the client

    # Recoverable cost borne by the escribanía (paid + pending, escribanía-funded)
    costo_recuperable: Decimal
    honorarios_total: Decimal            # subset: escribanía fees
    desembolsos_total: Decimal           # subset: third-party disbursements

    # Expense settlement state (escribanía-funded only)
    gastos_pagados: Decimal              # disbursed by the escribanía
    gastos_pendientes: Decimal           # accrued, not yet disbursed

    # Expenses the client paid directly (informational; not owed to escribanía)
    pagado_por_cliente: Decimal

    # Cash position = received - paid-on-behalf
    posicion_caja: Decimal               # >0 funds held; <0 escribanía financing
    fondos_disponibles: Decimal          # max(posicion_caja, 0)
    monto_financiado: Decimal            # max(-posicion_caja, 0)

    # Economic balance = received - recoverable cost
    balance_neto: Decimal                # >0 surplus; <0 shortfall
    saldo_a_cobrar: Decimal              # max(-balance_neto, 0)
    excedente_a_devolver: Decimal        # max(balance_neto, 0)

    cobertura: Decimal                   # received / recoverable (0..>1)
    anticipo_suficiente: bool
    financiando: bool

    counts: dict = field(default_factory=dict)


def _recoverable(expenses: Sequence[Expense]) -> list[Expense]:
    """Expenses the client owes the escribanía (escribanía-funded)."""
    return [e for e in expenses if e.pagado_por == PaidBy.ESCRIBANIA]


def compute_summary(
    expediente_id: int,
    advances: Sequence[Advance],
    expenses: Sequence[Expense],
) -> ExpedienteSummary:
    """Compute the full deterministic financial summary for one expediente."""

    total_recibido = money_sum(a.monto for a in advances)

    recoverable = _recoverable(expenses)
    costo_recuperable = money_sum(e.monto for e in recoverable)

    fee_cats = set(_cfg.fee_categories())
    honorarios_total = money_sum(e.monto for e in recoverable if e.categoria in fee_cats)
    desembolsos_total = quantize(costo_recuperable - honorarios_total)

    gastos_pagados = money_sum(
        e.monto for e in recoverable if e.estado == ExpenseStatus.PAID
    )
    gastos_pendientes = money_sum(
        e.monto for e in recoverable if e.estado == ExpenseStatus.PENDING
    )

    pagado_por_cliente = money_sum(
        e.monto for e in expenses if e.pagado_por == PaidBy.CLIENT
    )

    posicion_caja = quantize(total_recibido - gastos_pagados)
    fondos_disponibles = posicion_caja if posicion_caja > ZERO else ZERO
    monto_financiado = -posicion_caja if posicion_caja < ZERO else ZERO

    balance_neto = quantize(total_recibido - costo_recuperable)
    saldo_a_cobrar = -balance_neto if balance_neto < ZERO else ZERO
    excedente_a_devolver = balance_neto if balance_neto > ZERO else ZERO

    if costo_recuperable > ZERO:
        cobertura = quantize(total_recibido / costo_recuperable)
    else:
        # No recoverable cost yet: any funds received over-cover by definition.
        cobertura = Decimal("1.00") if total_recibido >= ZERO else ZERO

    return ExpedienteSummary(
        expediente_id=expediente_id,
        total_recibido=total_recibido,
        costo_recuperable=costo_recuperable,
        honorarios_total=honorarios_total,
        desembolsos_total=desembolsos_total,
        gastos_pagados=gastos_pagados,
        gastos_pendientes=gastos_pendientes,
        pagado_por_cliente=pagado_por_cliente,
        posicion_caja=posicion_caja,
        fondos_disponibles=fondos_disponibles,
        monto_financiado=monto_financiado,
        balance_neto=balance_neto,
        saldo_a_cobrar=saldo_a_cobrar,
        excedente_a_devolver=excedente_a_devolver,
        cobertura=cobertura,
        anticipo_suficiente=total_recibido >= costo_recuperable,
        financiando=posicion_caja < ZERO,
        counts={
            "advances": len(advances),
            "expenses": len(expenses),
            "expenses_pending": sum(
                1 for e in recoverable if e.estado == ExpenseStatus.PENDING
            ),
            "expenses_paid": sum(
                1 for e in recoverable if e.estado == ExpenseStatus.PAID
            ),
        },
    )
