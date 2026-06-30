"""Agent analysis orchestration.

Builds the grounded fact sheet (the only source of numbers the agent may use),
checks the per-facts-hash cache (cost control — no API call if the underlying
deterministic numbers are unchanged), invokes the analyst agent, persists the
result and records an audit entry with metadata only.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional

from .. import config_proxy as _cfg
from ..data import audit, repository as repo
from ..domain.models import ExpedienteStatus, ReviewStatus
from ..domain.money import format_ars
from ..llm.analyst import AnalysisResult, analyze_expediente, analyze_portfolio
from .queries import build_expediente_view, build_portfolio
from .serialization import summary_facts_text


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fact sheets (grounding source)
# ---------------------------------------------------------------------------
def build_expediente_facts(view) -> str:
    s = view.summary
    e = view.expediente
    th = _cfg.thresholds()
    open_tipos = sorted({r.tipo for r in view.reviews if r.status == ReviewStatus.OPEN})
    lines = [
        f"Expediente {e.codigo} — {e.caratula}. Cliente: {e.cliente}.",
        f"Estado financiero determinista: {view.status.value}.",
        summary_facts_text(s, view.reasons),
        f"Cobertura del anticipo: {float(s.cobertura) * 100:.0f}%.",
        f"Movimientos bancarios sin conciliar: {len(view.unmatched_movements)}.",
        f"Cantidad de gastos pagados: {s.counts.get('expenses_paid', 0)}; "
        f"pendientes: {s.counts.get('expenses_pending', 0)}.",
        f"Umbral de financiamiento de riesgo: {format_ars(th.financing_risk_amount)}.",
        f"Umbral de saldo a cobrar de riesgo: {format_ars(th.balance_to_collect_risk_amount)}.",
    ]
    if open_tipos:
        lines.append("Revisiones abiertas: " + ", ".join(open_tipos) + ".")
    return "\n".join(lines)


def build_portfolio_facts(portfolio) -> tuple[str, dict]:
    c = portfolio.status_counts
    crit = [
        r for r in portfolio.rows
        if r.status in (ExpedienteStatus.BLOQUEADO, ExpedienteStatus.RIESGO)
    ]
    lines = [
        f"Cartera de {len(portfolio.rows)} expediente(s).",
        f"Estados: OK {c.get('OK', 0)}, Atención {c.get('Atencion', 0)}, "
        f"Riesgo {c.get('Riesgo', 0)}, Bloqueado {c.get('Bloqueado', 0)}.",
        f"Total recibido de clientes: {format_ars(portfolio.total_recibido)}.",
        f"Total costo recuperable: {format_ars(portfolio.total_costo)}.",
        f"Total financiado por la escribanía: {format_ars(portfolio.total_financiado)}.",
        f"Saldo total a cobrar: {format_ars(portfolio.total_a_cobrar)}.",
    ]
    for r in crit:
        lines.append(
            f"{r.expediente.codigo} ({r.status.value}): financiado "
            f"{format_ars(r.summary.monto_financiado)}, a cobrar "
            f"{format_ars(r.summary.saldo_a_cobrar)}."
        )
    ctx = {
        "status_counts": c,
        "prioritarios": [r.expediente.codigo for r in crit],
        "total_financiado": portfolio.total_financiado,
        "total_a_cobrar": portfolio.total_a_cobrar,
        "riesgos": [f"{r.expediente.codigo}: {r.status.value}" for r in crit],
    }
    return "\n".join(lines), ctx


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def get_or_generate_expediente_analysis(
    conn: sqlite3.Connection, expediente_id: int, *, force: bool = False, actor: str = "ui"
) -> Optional[AnalysisResult]:
    view = build_expediente_view(conn, expediente_id)
    if view is None:
        return None
    facts = build_expediente_facts(view)
    facts_hash = _hash(facts)

    if not force:
        cached = repo.get_cached_analysis(conn, "expediente", expediente_id, facts_hash)
        if cached:
            return AnalysisResult.from_json(cached["content_json"])

    open_tipos = {r.tipo for r in view.reviews if r.status == ReviewStatus.OPEN}
    result = analyze_expediente(
        facts, view.summary, view.status, view.reasons, open_tipos,
        len(view.unmatched_movements),
    )
    _persist(conn, "expediente", expediente_id, facts_hash, result, actor)
    return result


def get_or_generate_portfolio_analysis(
    conn: sqlite3.Connection, *, force: bool = False, actor: str = "ui"
) -> Optional[AnalysisResult]:
    portfolio = build_portfolio(conn)
    if not portfolio.rows:
        return None
    facts, ctx = build_portfolio_facts(portfolio)
    facts_hash = _hash(facts)

    if not force:
        cached = repo.get_cached_analysis(conn, "cartera", None, facts_hash)
        if cached:
            return AnalysisResult.from_json(cached["content_json"])

    result = analyze_portfolio(facts, ctx)
    _persist(conn, "cartera", None, facts_hash, result, actor)
    return result


def peek_expediente_analysis(
    conn: sqlite3.Connection, expediente_id: int
) -> Optional[AnalysisResult]:
    """Return a cached analysis for the current facts, or None — never calls the API."""
    view = build_expediente_view(conn, expediente_id)
    if view is None:
        return None
    facts_hash = _hash(build_expediente_facts(view))
    row = repo.get_cached_analysis(conn, "expediente", expediente_id, facts_hash)
    return AnalysisResult.from_json(row["content_json"]) if row else None


def peek_portfolio_analysis(conn: sqlite3.Connection) -> Optional[AnalysisResult]:
    portfolio = build_portfolio(conn)
    if not portfolio.rows:
        return None
    facts, _ctx = build_portfolio_facts(portfolio)
    row = repo.get_cached_analysis(conn, "cartera", None, _hash(facts))
    return AnalysisResult.from_json(row["content_json"]) if row else None


def _persist(conn, scope, expediente_id, facts_hash, result: AnalysisResult, actor: str) -> None:
    repo.insert_analysis(
        conn, scope=scope, expediente_id=expediente_id, facts_hash=facts_hash,
        content_json=result.to_json(), origen=result.origen, model=result.model,
        grounded=result.grounded,
    )
    audit.record(
        conn, action="agent_analysis", entity=scope, entity_id=expediente_id or "",
        payload={
            "scope": scope, "origen": result.origen, "model": result.model,
            "grounded": result.grounded, "recomendaciones": len(result.recomendaciones),
        },
        actor=actor,
    )
