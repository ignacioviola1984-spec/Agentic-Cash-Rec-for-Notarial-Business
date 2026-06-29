"""End-to-end flow over an isolated DB: seed, classify, reconcile, report."""
from decimal import Decimal

import pandas as pd

from cashcontrol.data import audit, repository as repo
from cashcontrol.domain.models import ExpedienteStatus, MatchStatus
from cashcontrol.services import (
    ingestion,
    matching_service,
    reports,
    seed,
    status_service,
)
from cashcontrol.services.queries import build_expediente_view, build_portfolio


def test_seed_produces_all_statuses(conn):
    seed.seed(conn)
    portfolio = build_portfolio(conn)
    statuses = {r.status for r in portfolio.rows}
    assert ExpedienteStatus.OK in statuses
    assert ExpedienteStatus.ATENCION in statuses
    assert ExpedienteStatus.RIESGO in statuses
    assert ExpedienteStatus.BLOQUEADO in statuses
    # Audit chain stays intact after the whole seed.
    ok, _ = audit.verify_chain(conn)
    assert ok is True


def test_blocked_file_is_pago_sin_anticipo(conn):
    seed.seed(conn)
    e = repo.get_expediente_by_codigo(conn, "EXP-2024-004")
    view = build_expediente_view(conn, e.id)
    assert view.status == ExpedienteStatus.BLOQUEADO
    tipos = {r.tipo for r in view.reviews if r.status.value == "open"}
    assert "pago_sin_anticipo" in tipos


def test_import_and_classify_expense(conn):
    # Minimal import path: one expediente, one advance, one uncategorised expense.
    ingestion.import_expedientes(conn, pd.DataFrame([
        {"codigo": "EXP-9", "caratula": "Test", "cliente": "Cliente Uno"}]))
    ingestion.import_advances(conn, pd.DataFrame([
        {"expediente": "EXP-9", "fecha": "05/03/2024", "monto": "$ 300.000,00"}]))
    res = ingestion.import_expenses(conn, pd.DataFrame([
        {"expediente": "EXP-9", "fecha": "06/03/2024", "monto": "200.000,00",
         "concepto": "Impuesto de sellos ARBA"}]))
    ingestion.finalize(conn, res)

    e = repo.get_expediente_by_codigo(conn, "EXP-9")
    expenses = repo.list_expenses(conn, e.id)
    assert len(expenses) == 1
    # Heuristic classifier maps "sellos" -> category sellos without an LLM.
    assert expenses[0].categoria == "sellos"
    assert expenses[0].monto == Decimal("200000.00")


def test_confirm_match_updates_status_and_audit(conn):
    seed.seed(conn)
    e = repo.get_expediente_by_codigo(conn, "EXP-2024-002")
    matching_service.generate_suggestions(conn, e.id)
    suggestions = [m for m in repo.list_matches_for_expediente(conn, e.id)
                   if m.status == MatchStatus.SUGGESTED]
    assert suggestions, "expected at least one suggestion for EXP-2024-002"
    matching_service.confirm_match(conn, suggestions[0].id)
    confirmed = [m for m in repo.list_matches_for_expediente(conn, e.id)
                 if m.status == MatchStatus.CONFIRMED]
    assert confirmed
    ok, _ = audit.verify_chain(conn)
    assert ok is True


def test_reports_export_bytes(conn):
    seed.seed(conn)
    csv_bytes = reports.export_portfolio_csv(conn)
    assert csv_bytes and b"codigo" in csv_bytes
    xlsx = reports.export_portfolio_excel(conn)
    assert xlsx[:2] == b"PK"  # xlsx is a zip
    e = repo.get_expediente_by_codigo(conn, "EXP-2024-001")
    exp_xlsx = reports.export_expediente_excel(conn, e.id)
    assert exp_xlsx[:2] == b"PK"


def test_unassigned_suggestion(conn):
    seed.seed(conn)
    suggestions = matching_service.suggest_assignments(conn)
    # The credit mentioning "Lucia Fernandez" should be suggested to EXP-2024-005.
    by_code = {s["movement"].referencia_banco: s["suggested_codigo"] for s in suggestions}
    assert by_code.get("TRF-0150") == "EXP-2024-005"
