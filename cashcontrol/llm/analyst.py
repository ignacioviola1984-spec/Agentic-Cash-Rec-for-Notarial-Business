"""Analyst agents: interpret deterministic outputs and produce analysis and
recommendations.

Two scopes:
  * ``analyze_expediente`` — diagnosis + actionable recommendations for one file.
  * ``analyze_portfolio``  — cartera-level prioritisation.

Guarantees (production-critical):
  * The LLM never invents a monetary figure. Every number in the model's output
    must already appear in the grounded facts; otherwise the output is rejected.
  * If the API is unavailable, the key is missing, the response is malformed, or
    the grounding check fails, the agent degrades to a deterministic, rule-based
    recommendation set — so the feature never breaks the deployed app.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Optional

from .. import config_proxy as _cfg
from ..domain.engine import ExpedienteSummary
from ..domain.models import ExpedienteStatus
from ..domain.money import ZERO, format_ars
from .client import get_client, narrative_grounding_ok


@dataclass
class Recommendation:
    accion: str
    prioridad: str  # alta | media | baja
    fundamento: str = ""


@dataclass
class AnalysisResult:
    scope: str                       # 'expediente' | 'cartera'
    diagnostico: str
    riesgos: list[str] = field(default_factory=list)
    recomendaciones: list[Recommendation] = field(default_factory=list)
    confianza: str = "alta"          # alta | media | baja
    origen: str = "fallback"         # 'llm' | 'fallback'
    model: str = ""
    grounded: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "AnalysisResult":
        data = json.loads(text)
        recs = [Recommendation(**r) for r in data.get("recomendaciones", [])]
        data["recomendaciones"] = recs
        return AnalysisResult(**data)


_PRIORIDADES = {"alta", "media", "baja"}
_CONFIANZAS = {"alta", "media", "baja"}


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def _coerce_result(scope: str, data: dict, facts: str, model: str) -> Optional[AnalysisResult]:
    """Validate the parsed JSON and enforce the number-grounding guard."""
    try:
        diagnostico = str(data["diagnostico"]).strip()
        riesgos = [str(r).strip() for r in data.get("riesgos", []) if str(r).strip()]
        recs_raw = data.get("recomendaciones", [])
        recomendaciones: list[Recommendation] = []
        for r in recs_raw:
            accion = str(r.get("accion", "")).strip()
            if not accion:
                continue
            prioridad = str(r.get("prioridad", "media")).strip().lower()
            if prioridad not in _PRIORIDADES:
                prioridad = "media"
            recomendaciones.append(
                Recommendation(accion=accion, prioridad=prioridad,
                               fundamento=str(r.get("fundamento", "")).strip())
            )
        confianza = str(data.get("confianza", "media")).strip().lower()
        if confianza not in _CONFIANZAS:
            confianza = "media"
    except (KeyError, AttributeError, TypeError):
        return None

    if not diagnostico or not recomendaciones:
        return None

    # Grounding guard: no number anywhere in the output may be absent from facts.
    blob = " ".join(
        [diagnostico, *riesgos]
        + [f"{r.accion} {r.fundamento}" for r in recomendaciones]
    )
    if not narrative_grounding_ok(blob, facts):
        return None

    return AnalysisResult(
        scope=scope, diagnostico=diagnostico, riesgos=riesgos,
        recomendaciones=recomendaciones, confianza=confianza,
        origen="llm", model=model, grounded=True,
    )


# ---------------------------------------------------------------------------
# Deterministic fallbacks (also the safety net the whole product relies on)
# ---------------------------------------------------------------------------
def fallback_expediente(
    summary: ExpedienteSummary,
    status: ExpedienteStatus,
    reasons: list[str],
    review_tipos: set[str],
    unmatched: int,
) -> AnalysisResult:
    th = _cfg.thresholds()
    recs: list[Recommendation] = []

    if status == ExpedienteStatus.BLOQUEADO and "pago_sin_anticipo" in review_tipos:
        recs.append(Recommendation(
            accion="Regularizar antes de continuar: solicitar la provisión de fondos "
                   "del cliente o documentar la autorización para los pagos ya realizados.",
            prioridad="alta",
            fundamento=f"Se pagaron {format_ars(summary.gastos_pagados)} sin haber "
                       "recibido anticipo del cliente.",
        ))

    if summary.financiando:
        prioridad = "alta" if summary.monto_financiado >= th.financing_risk_amount else "media"
        recs.append(Recommendation(
            accion=f"Solicitar al cliente la reposición de fondos por "
                   f"{format_ars(summary.monto_financiado)}.",
            prioridad=prioridad,
            fundamento="La escribanía está adelantando fondos propios (posición de "
                       f"caja {format_ars(summary.posicion_caja)}).",
        ))

    if not summary.anticipo_suficiente and summary.saldo_a_cobrar > ZERO:
        recs.append(Recommendation(
            accion=f"Requerir el saldo pendiente de {format_ars(summary.saldo_a_cobrar)} "
                   "antes de afrontar los gastos restantes.",
            prioridad="media",
            fundamento=f"El anticipo cubre el {float(summary.cobertura) * 100:.0f}% "
                       "del costo recuperable.",
        ))

    if summary.gastos_pendientes > ZERO and summary.fondos_disponibles < summary.gastos_pendientes:
        recs.append(Recommendation(
            accion=f"Asegurar fondos para los gastos pendientes por "
                   f"{format_ars(summary.gastos_pendientes)}.",
            prioridad="media",
            fundamento=f"Fondos disponibles {format_ars(summary.fondos_disponibles)} "
                       "por debajo de los pendientes.",
        ))

    if unmatched > 0:
        recs.append(Recommendation(
            accion=f"Conciliar los {unmatched} movimiento(s) bancario(s) pendientes "
                   "para confirmar la trazabilidad de la caja.",
            prioridad="media",
            fundamento="Hay movimientos asignados al expediente sin conciliación humana.",
        ))

    if summary.excedente_a_devolver > ZERO and summary.gastos_pendientes == ZERO:
        recs.append(Recommendation(
            accion=f"Gestionar la devolución del excedente de "
                   f"{format_ars(summary.excedente_a_devolver)} al cliente.",
            prioridad="baja",
            fundamento="Todos los gastos están saldados y queda un excedente a favor del cliente.",
        ))

    if not recs:
        recs.append(Recommendation(
            accion="Sin acciones requeridas; mantener el monitoreo del expediente.",
            prioridad="baja",
            fundamento="Expediente financieramente OK.",
        ))

    diag = f"Estado financiero: {status.value}. " + (" ".join(reasons) if reasons else "")
    return AnalysisResult(
        scope="expediente", diagnostico=diag.strip(), riesgos=list(reasons),
        recomendaciones=recs, confianza="alta", origen="fallback", model="", grounded=True,
    )


def fallback_portfolio(ctx: dict) -> AnalysisResult:
    counts = ctx["status_counts"]
    prioritarios = ctx["prioritarios"]  # list of codigos (Bloqueado/Riesgo)
    recs: list[Recommendation] = []

    if prioritarios:
        recs.append(Recommendation(
            accion="Atender primero los expedientes críticos: " + ", ".join(prioritarios) + ".",
            prioridad="alta",
            fundamento=f"{counts.get('Bloqueado', 0)} bloqueado(s) y "
                       f"{counts.get('Riesgo', 0)} en riesgo.",
        ))
    if ctx["total_financiado"] > ZERO:
        recs.append(Recommendation(
            accion=f"Recuperar el financiamiento de la escribanía por "
                   f"{format_ars(ctx['total_financiado'])}.",
            prioridad="alta" if counts.get("Riesgo", 0) else "media",
            fundamento="Fondos propios adelantados a clientes en la cartera.",
        ))
    if ctx["total_a_cobrar"] > ZERO:
        recs.append(Recommendation(
            accion=f"Planificar el cobro de saldos pendientes por "
                   f"{format_ars(ctx['total_a_cobrar'])}.",
            prioridad="media",
            fundamento="Saldo total a cobrar de la cartera.",
        ))
    if not recs:
        recs.append(Recommendation(
            accion="Cartera saludable; mantener el monitoreo periódico.",
            prioridad="baja", fundamento="Sin expedientes críticos.",
        ))

    diag = (
        f"Cartera con {counts.get('OK', 0)} OK, {counts.get('Atencion', 0)} en atención, "
        f"{counts.get('Riesgo', 0)} en riesgo y {counts.get('Bloqueado', 0)} bloqueado(s)."
    )
    return AnalysisResult(
        scope="cartera", diagnostico=diag, riesgos=ctx.get("riesgos", []),
        recomendaciones=recs, confianza="alta", origen="fallback", model="", grounded=True,
    )


# ---------------------------------------------------------------------------
# Agent entry points
# ---------------------------------------------------------------------------
def analyze_expediente(
    facts: str,
    summary: ExpedienteSummary,
    status: ExpedienteStatus,
    reasons: list[str],
    review_tipos: set[str],
    unmatched: int,
) -> AnalysisResult:
    from .prompts import ANALYST_EXPEDIENTE_SYSTEM, ANALYST_USER

    client = get_client()
    if client.enabled:
        out = client.complete(
            ANALYST_EXPEDIENTE_SYSTEM, ANALYST_USER.format(facts=facts),
            max_tokens=900, temperature=0.2,
        )
        data = _extract_json(out) if out else None
        if data is not None:
            result = _coerce_result("expediente", data, facts, client.model)
            if result is not None:
                return result
    return fallback_expediente(summary, status, reasons, review_tipos, unmatched)


def analyze_portfolio(facts: str, ctx: dict) -> AnalysisResult:
    from .prompts import ANALYST_PORTFOLIO_SYSTEM, ANALYST_USER

    client = get_client()
    if client.enabled:
        out = client.complete(
            ANALYST_PORTFOLIO_SYSTEM, ANALYST_USER.format(facts=facts),
            max_tokens=900, temperature=0.2,
        )
        data = _extract_json(out) if out else None
        if data is not None:
            result = _coerce_result("cartera", data, facts, client.model)
            if result is not None:
                return result
    return fallback_portfolio(ctx)
