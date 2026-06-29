"""Realistic sample data for an Argentine escribanía.

Produces expedientes that exercise every status (OK / Atención / Riesgo /
Bloqueado) and every workflow (matched, unmatched, unassigned, review items).
All amounts are explicit Decimals — nothing here is LLM-generated.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

from ..data import audit, repository as repo
from ..domain.models import (
    Advance,
    BankMovement,
    Expediente,
    Expense,
    ExpenseStatus,
    MatchStatus,
    MovementKind,
    PaidBy,
)
from . import matching_service, status_service


def _d(amount: str) -> Decimal:
    return Decimal(amount)


def _confirm_all_suggestions(conn: sqlite3.Connection, expediente_id: int) -> None:
    for m in repo.list_matches_for_expediente(conn, expediente_id):
        if m.status == MatchStatus.SUGGESTED:
            matching_service.confirm_match(conn, m.id, actor="seed")


def seed(conn: sqlite3.Connection) -> None:
    """Populate the database. Assumes an empty schema."""

    # ---- EXP-2024-001 — OK, fully reconciled ---------------------------
    e1 = repo.create_expediente(conn, Expediente(
        codigo="EXP-2024-001", caratula="Compraventa Pérez / González",
        cliente="Juan Pérez", escribano="Esc. M. López", tipo_acto="compraventa",
        fecha_apertura=date(2024, 3, 1)))
    repo.add_advance(conn, Advance(e1, date(2024, 3, 4), _d("500000.00"), "transferencia", "TRF-0012"))
    repo.add_expense(conn, Expense(e1, date(2024, 3, 6), _d("200000.00"), "sellos",
                                   "Impuesto de sellos", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "ARBA", "SEL-77"))
    repo.add_expense(conn, Expense(e1, date(2024, 3, 7), _d("80000.00"), "tasa_registral",
                                   "Inscripción dominio", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "RPI", "RPI-21"))
    repo.add_expense(conn, Expense(e1, date(2024, 3, 8), _d("150000.00"), "honorarios",
                                   "Honorarios escribano", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "Escribanía", ""))
    repo.add_movement(conn, BankMovement(date(2024, 3, 4), _d("500000.00"), MovementKind.CREDIT,
                                         "Transferencia recibida Perez", "Juan Perez", "TRF-0012", "CC-001", e1), "seed-1c")
    repo.add_movement(conn, BankMovement(date(2024, 3, 6), _d("200000.00"), MovementKind.DEBIT,
                                         "Pago ARBA sellos", "ARBA", "SEL-77", "CC-001", e1), "seed-1d1")
    repo.add_movement(conn, BankMovement(date(2024, 3, 7), _d("80000.00"), MovementKind.DEBIT,
                                         "Pago RPI inscripcion", "RPI", "RPI-21", "CC-001", e1), "seed-1d2")
    repo.add_movement(conn, BankMovement(date(2024, 3, 8), _d("150000.00"), MovementKind.DEBIT,
                                         "Honorarios", "Escribania", "", "CC-001", e1), "seed-1d3")

    # ---- EXP-2024-002 — Atención: anticipo algo corto, gastos pendientes
    e2 = repo.create_expediente(conn, Expediente(
        codigo="EXP-2024-002", caratula="Sucesión Ramírez", cliente="María Ramírez",
        escribano="Esc. M. López", tipo_acto="sucesion", fecha_apertura=date(2024, 4, 2)))
    repo.add_advance(conn, Advance(e2, date(2024, 4, 5), _d("330000.00"), "transferencia", "TRF-0044"))
    repo.add_expense(conn, Expense(e2, date(2024, 4, 8), _d("120000.00"), "sellos",
                                   "Sellos sucesión", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "ARBA", "SEL-90"))
    repo.add_expense(conn, Expense(e2, date(2024, 4, 9), _d("60000.00"), "certificaciones",
                                   "Certificado de dominio", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "RPI", "CD-11"))
    repo.add_expense(conn, Expense(e2, date(2024, 4, 15), _d("50000.00"), "tasa_registral",
                                   "Tasa registral", ExpenseStatus.PENDING, PaidBy.ESCRIBANIA, "RPI", ""))
    repo.add_expense(conn, Expense(e2, date(2024, 4, 20), _d("120000.00"), "honorarios",
                                   "Honorarios", ExpenseStatus.PENDING, PaidBy.ESCRIBANIA, "Escribanía", ""))
    repo.add_movement(conn, BankMovement(date(2024, 4, 5), _d("330000.00"), MovementKind.CREDIT,
                                         "Transferencia Ramirez", "Maria Ramirez", "TRF-0044", "CC-001", e2), "seed-2c")
    repo.add_movement(conn, BankMovement(date(2024, 4, 8), _d("120000.00"), MovementKind.DEBIT,
                                         "Pago sellos ARBA", "ARBA", "SEL-90", "CC-001", e2), "seed-2d1")
    # A debit that won't auto-match (amount differs) -> stays unmatched.
    repo.add_movement(conn, BankMovement(date(2024, 4, 10), _d("59500.00"), MovementKind.DEBIT,
                                         "Pago certificado (con recargo)", "RPI", "CD-11", "CC-001", e2), "seed-2d2")

    # ---- EXP-2024-003 — Riesgo: escribanía financiando -----------------
    e3 = repo.create_expediente(conn, Expediente(
        codigo="EXP-2024-003", caratula="Hipoteca Banco Sur / Díaz", cliente="Carlos Díaz",
        escribano="Esc. R. Sosa", tipo_acto="hipoteca", fecha_apertura=date(2024, 5, 3)))
    repo.add_advance(conn, Advance(e3, date(2024, 5, 6), _d("100000.00"), "efectivo", "REC-08"))
    repo.add_expense(conn, Expense(e3, date(2024, 5, 9), _d("180000.00"), "sellos",
                                   "Sellos hipoteca", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "ARBA", "SEL-103"))
    repo.add_expense(conn, Expense(e3, date(2024, 5, 10), _d("90000.00"), "tasa_registral",
                                   "Inscripción hipoteca", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "RPI", "RPI-55"))
    repo.add_movement(conn, BankMovement(date(2024, 5, 6), _d("100000.00"), MovementKind.CREDIT,
                                         "Efectivo Diaz", "Carlos Diaz", "REC-08", "CC-001", e3), "seed-3c")
    repo.add_movement(conn, BankMovement(date(2024, 5, 9), _d("180000.00"), MovementKind.DEBIT,
                                         "Pago sellos hipoteca", "ARBA", "SEL-103", "CC-001", e3), "seed-3d1")
    repo.add_movement(conn, BankMovement(date(2024, 5, 10), _d("90000.00"), MovementKind.DEBIT,
                                         "Inscripcion hipoteca", "RPI", "RPI-55", "CC-001", e3), "seed-3d2")

    # ---- EXP-2024-004 — Bloqueado: pago sin anticipo -------------------
    e4 = repo.create_expediente(conn, Expediente(
        codigo="EXP-2024-004", caratula="Compraventa Torres", cliente="Ana Torres",
        escribano="Esc. R. Sosa", tipo_acto="compraventa", fecha_apertura=date(2024, 6, 1)))
    repo.add_expense(conn, Expense(e4, date(2024, 6, 4), _d("50000.00"), "sellos",
                                   "Adelanto de sellos sin provisión", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "ARBA", "SEL-200"))
    repo.add_movement(conn, BankMovement(date(2024, 6, 4), _d("50000.00"), MovementKind.DEBIT,
                                         "Pago sellos sin fondos cliente", "ARBA", "SEL-200", "CC-001", e4), "seed-4d1")

    # ---- EXP-2024-005 — OK: chico y reconciliado -----------------------
    e5 = repo.create_expediente(conn, Expediente(
        codigo="EXP-2024-005", caratula="Donación Fernández", cliente="Lucía Fernández",
        escribano="Esc. M. López", tipo_acto="donacion", fecha_apertura=date(2024, 6, 10)))
    repo.add_advance(conn, Advance(e5, date(2024, 6, 12), _d("200000.00"), "transferencia", "TRF-0099"))
    repo.add_expense(conn, Expense(e5, date(2024, 6, 14), _d("80000.00"), "honorarios",
                                   "Honorarios", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "Escribanía", ""))
    repo.add_expense(conn, Expense(e5, date(2024, 6, 14), _d("40000.00"), "sellos",
                                   "Sellos donación", ExpenseStatus.PAID, PaidBy.ESCRIBANIA, "ARBA", "SEL-210"))
    repo.add_movement(conn, BankMovement(date(2024, 6, 12), _d("200000.00"), MovementKind.CREDIT,
                                         "Transferencia Fernandez", "Lucia Fernandez", "TRF-0099", "CC-001", e5), "seed-5c")
    repo.add_movement(conn, BankMovement(date(2024, 6, 14), _d("80000.00"), MovementKind.DEBIT,
                                         "Honorarios", "Escribania", "", "CC-001", e5), "seed-5d1")
    repo.add_movement(conn, BankMovement(date(2024, 6, 14), _d("40000.00"), MovementKind.DEBIT,
                                         "Sellos donacion", "ARBA", "SEL-210", "CC-001", e5), "seed-5d2")

    # ---- Unassigned bank movements (assignment workflow) ---------------
    repo.add_movement(conn, BankMovement(date(2024, 6, 18), _d("75000.00"), MovementKind.CREDIT,
                                         "Transferencia recibida - Lucia Fernandez", "Lucia Fernandez",
                                         "TRF-0150", "CC-001", None), "seed-unassigned-1")
    repo.add_movement(conn, BankMovement(date(2024, 6, 19), _d("12500.00"), MovementKind.DEBIT,
                                         "Comision bancaria", "Banco", "COM-01", "CC-001", None), "seed-unassigned-2")

    audit.record(conn, action="seed_loaded", entity="system", payload={"expedientes": 5}, actor="seed")

    # Generate suggestions, recompute all, and confirm matches for the clean files.
    matching_service.generate_all_suggestions(conn, actor="seed")
    status_service.recompute_all(conn, actor="seed")
    _confirm_all_suggestions(conn, e1)
    _confirm_all_suggestions(conn, e5)
    # e3 movements reconcile too (financing remains regardless of reconciliation).
    _confirm_all_suggestions(conn, e3)
    status_service.recompute_all(conn, actor="seed")
