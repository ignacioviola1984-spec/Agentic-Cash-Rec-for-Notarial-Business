"""Helpers to turn an :class:`ExpedienteSummary` into JSON-safe dicts and into
the grounded fact text used by the narrative layer."""
from __future__ import annotations

import json
from decimal import Decimal

from ..domain.engine import ExpedienteSummary
from ..domain.money import format_ars

_MONEY_FIELDS = (
    "total_recibido",
    "costo_recuperable",
    "honorarios_total",
    "desembolsos_total",
    "gastos_pagados",
    "gastos_pendientes",
    "pagado_por_cliente",
    "posicion_caja",
    "fondos_disponibles",
    "monto_financiado",
    "balance_neto",
    "saldo_a_cobrar",
    "excedente_a_devolver",
)


def summary_to_dict(summary: ExpedienteSummary) -> dict:
    out: dict = {"expediente_id": summary.expediente_id}
    for field in _MONEY_FIELDS:
        out[field] = str(getattr(summary, field))
    out["cobertura"] = str(summary.cobertura)
    out["anticipo_suficiente"] = summary.anticipo_suficiente
    out["financiando"] = summary.financiando
    out["counts"] = summary.counts
    return out


def summary_to_json(summary: ExpedienteSummary) -> str:
    return json.dumps(summary_to_dict(summary), ensure_ascii=False)


def summary_facts_text(summary: ExpedienteSummary, reasons: list[str]) -> str:
    """A grounded, human-readable fact sheet (every number is deterministic)."""
    lines = [
        f"Recibido del cliente: {format_ars(summary.total_recibido)}.",
        f"Costo recuperable total: {format_ars(summary.costo_recuperable)} "
        f"(desembolsos {format_ars(summary.desembolsos_total)}, "
        f"honorarios {format_ars(summary.honorarios_total)}).",
        f"Gastos pagados por la escribanía: {format_ars(summary.gastos_pagados)}.",
        f"Gastos pendientes: {format_ars(summary.gastos_pendientes)}.",
        f"Posición de caja: {format_ars(summary.posicion_caja)}.",
    ]
    if summary.financiando:
        lines.append(f"La escribanía financia {format_ars(summary.monto_financiado)}.")
    if summary.saldo_a_cobrar > Decimal("0"):
        lines.append(f"Saldo a cobrar: {format_ars(summary.saldo_a_cobrar)}.")
    if summary.excedente_a_devolver > Decimal("0"):
        lines.append(f"Excedente a devolver: {format_ars(summary.excedente_a_devolver)}.")
    lines.extend(reasons)
    return " ".join(lines)
