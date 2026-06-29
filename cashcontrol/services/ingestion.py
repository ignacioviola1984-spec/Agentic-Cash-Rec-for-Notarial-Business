"""Ingestion: import expedientes, advances, expenses and bank movements from
tolerant tabular data (CSV/Excel read into pandas DataFrames).

Design notes:
  * Headers are matched case/accent-insensitively against synonym sets, so real
    bank exports and office spreadsheets import without manual reshaping.
  * Every monetary value passes through :func:`money.parse_money` (exact Decimal)
    — the LLM is never involved in producing an amount.
  * Expense categories, when absent or unknown, are assigned by the guarded
    classifier (LLM if available, deterministic rules otherwise) — a *label*,
    never a number.
"""
from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from dateutil import parser as dateparser

import config
from ..data import audit, repository as repo
from ..domain.models import (
    Advance,
    BankMovement,
    Expediente,
    Expense,
    ExpenseStatus,
    MovementKind,
    PaidBy,
)
from ..domain.money import MoneyError, ZERO, parse_money
from ..llm.client import get_client
from . import matching_service, status_service


@dataclass
class ImportResult:
    kind: str
    inserted: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    affected_expedientes: set[int] = field(default_factory=set)

    def add_error(self, row: int, message: str) -> None:
        self.errors.append({"row": row, "error": message})


def _norm_header(h: str) -> str:
    folded = unicodedata.normalize("NFKD", str(h)).encode("ascii", "ignore").decode()
    return folded.strip().lower().replace(" ", "_")


# Column synonym sets -> canonical name.
_SYNONYMS: dict[str, dict[str, str]] = {
    "expedientes": {
        "codigo": "codigo", "expediente": "codigo", "exp": "codigo", "nro": "codigo",
        "numero": "codigo", "codigo_expediente": "codigo",
        "caratula": "caratula", "descripcion": "caratula", "detalle": "caratula",
        "cliente": "cliente", "requirente": "cliente", "parte": "cliente",
        "escribano": "escribano", "notario": "escribano",
        "tipo_acto": "tipo_acto", "tipo": "tipo_acto", "acto": "tipo_acto",
        "fecha_apertura": "fecha_apertura", "fecha": "fecha_apertura", "apertura": "fecha_apertura",
        "notas": "notas", "observaciones": "notas",
    },
    "advances": {
        "expediente": "expediente", "codigo": "expediente", "exp": "expediente",
        "fecha": "fecha", "fecha_pago": "fecha",
        "monto": "monto", "importe": "monto", "monto_recibido": "monto", "anticipo": "monto",
        "metodo": "metodo", "medio": "metodo", "forma_pago": "metodo",
        "referencia": "referencia", "ref": "referencia", "comprobante": "referencia",
    },
    "expenses": {
        "expediente": "expediente", "codigo": "expediente", "exp": "expediente",
        "fecha": "fecha",
        "monto": "monto", "importe": "monto", "gasto": "monto",
        "categoria": "categoria", "rubro": "categoria", "tipo_gasto": "categoria",
        "concepto": "concepto", "detalle": "concepto", "descripcion": "concepto",
        "estado": "estado", "pagado": "estado",
        "pagado_por": "pagado_por", "pagador": "pagado_por",
        "proveedor": "proveedor", "beneficiario": "proveedor",
        "referencia": "referencia", "ref": "referencia",
    },
    "bank_movements": {
        "fecha": "fecha",
        "monto": "monto", "importe": "monto",
        "credito": "credito", "haber": "credito", "ingreso": "credito", "entrada": "credito",
        "debito": "debito", "debe": "debito", "egreso": "debito", "salida": "debito",
        "tipo": "kind", "kind": "kind", "movimiento": "kind",
        "descripcion": "descripcion", "detalle": "descripcion", "concepto": "descripcion",
        "contraparte": "contraparte", "beneficiario": "contraparte", "ordenante": "contraparte",
        "referencia": "referencia_banco", "ref": "referencia_banco", "comprobante": "referencia_banco",
        "id": "referencia_banco",
        "cuenta": "cuenta", "account": "cuenta",
        "expediente": "expediente", "codigo": "expediente",
    },
}


def _remap(df, kind: str):
    syn = _SYNONYMS[kind]
    rename = {}
    for col in df.columns:
        canonical = syn.get(_norm_header(col))
        if canonical:
            rename[col] = canonical
    return df.rename(columns=rename)


def _cell(row: Any, key: str) -> str:
    if key not in row:
        return ""
    value = row[key]
    if value is None:
        return ""
    try:
        import pandas as pd  # local import; pandas is a runtime dep

        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _parse_date(raw: str, fallback: Optional[date] = None) -> Optional[date]:
    raw = raw.strip()
    if not raw:
        return fallback
    try:
        return dateparser.parse(raw, dayfirst=True).date()
    except (ValueError, OverflowError):
        return fallback


def _parse_estado(raw: str) -> ExpenseStatus:
    val = _norm_header(raw)
    if val in {"paid", "pagado", "pago", "si", "true", "1", "abonado"}:
        return ExpenseStatus.PAID
    return ExpenseStatus.PENDING


def _parse_pagado_por(raw: str) -> PaidBy:
    val = _norm_header(raw)
    if val in {"cliente", "client"}:
        return PaidBy.CLIENT
    return PaidBy.ESCRIBANIA


def _parse_kind(raw: str) -> Optional[MovementKind]:
    val = _norm_header(raw)
    if val in {"credit", "credito", "ingreso", "haber", "entrada"}:
        return MovementKind.CREDIT
    if val in {"debit", "debito", "egreso", "debe", "salida"}:
        return MovementKind.DEBIT
    return None


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------
def import_expedientes(conn: sqlite3.Connection, df, *, actor: str = "system") -> ImportResult:
    res = ImportResult(kind="expedientes")
    df = _remap(df, "expedientes")
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        codigo = _cell(row, "codigo")
        if not codigo:
            res.add_error(i, "Falta código de expediente")
            continue
        if repo.get_expediente_by_codigo(conn, codigo):
            res.skipped += 1
            continue
        e = Expediente(
            codigo=codigo,
            caratula=_cell(row, "caratula") or codigo,
            cliente=_cell(row, "cliente") or "(sin dato)",
            escribano=_cell(row, "escribano"),
            tipo_acto=_cell(row, "tipo_acto"),
            fecha_apertura=_parse_date(_cell(row, "fecha_apertura")),
            notas=_cell(row, "notas"),
        )
        eid = repo.create_expediente(conn, e)
        audit.record(conn, action="expediente_created", entity="expediente",
                     entity_id=eid, payload={"codigo": codigo}, actor=actor)
        res.inserted += 1
        res.affected_expedientes.add(eid)
    return res


def import_advances(conn: sqlite3.Connection, df, *, actor: str = "system") -> ImportResult:
    res = ImportResult(kind="advances")
    df = _remap(df, "advances")
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        codigo = _cell(row, "expediente")
        exp = repo.get_expediente_by_codigo(conn, codigo) if codigo else None
        if not exp:
            res.add_error(i, f"Expediente inexistente: {codigo!r}")
            continue
        try:
            monto = parse_money(_cell(row, "monto"))
        except MoneyError as exc:
            res.add_error(i, f"Monto inválido: {exc}")
            continue
        fecha = _parse_date(_cell(row, "fecha"))
        if fecha is None:
            res.add_error(i, "Fecha inválida o ausente")
            continue
        aid = repo.add_advance(conn, Advance(
            expediente_id=exp.id, fecha=fecha, monto=monto,
            metodo=_cell(row, "metodo"), referencia=_cell(row, "referencia"),
        ))
        audit.record(conn, action="advance_added", entity="advance", entity_id=aid,
                     payload={"expediente": codigo, "monto": str(monto)}, actor=actor)
        res.inserted += 1
        res.affected_expedientes.add(exp.id)
    return res


def import_expenses(conn: sqlite3.Connection, df, *, actor: str = "system") -> ImportResult:
    res = ImportResult(kind="expenses")
    df = _remap(df, "expenses")
    client = get_client()
    valid_categories = set(config.EXPENSE_CATEGORIES)
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        codigo = _cell(row, "expediente")
        exp = repo.get_expediente_by_codigo(conn, codigo) if codigo else None
        if not exp:
            res.add_error(i, f"Expediente inexistente: {codigo!r}")
            continue
        try:
            monto = parse_money(_cell(row, "monto"))
        except MoneyError as exc:
            res.add_error(i, f"Monto inválido: {exc}")
            continue
        fecha = _parse_date(_cell(row, "fecha"))
        if fecha is None:
            res.add_error(i, "Fecha inválida o ausente")
            continue

        concepto = _cell(row, "concepto")
        proveedor = _cell(row, "proveedor")
        categoria = _norm_header(_cell(row, "categoria"))
        if categoria in valid_categories:
            origen = "manual"
        else:
            categoria, origen = client.classify_expense(concepto, proveedor)
            if categoria not in valid_categories:
                categoria, origen = "gastos_varios", "rule"

        xid = repo.add_expense(conn, Expense(
            expediente_id=exp.id, fecha=fecha, monto=monto, categoria=categoria,
            concepto=concepto, estado=_parse_estado(_cell(row, "estado")),
            pagado_por=_parse_pagado_por(_cell(row, "pagado_por")),
            proveedor=proveedor, referencia=_cell(row, "referencia"),
            categoria_origen=origen,
        ))
        audit.record(conn, action="expense_added", entity="expense", entity_id=xid,
                     payload={"expediente": codigo, "monto": str(monto),
                              "categoria": categoria, "categoria_origen": origen}, actor=actor)
        res.inserted += 1
        res.affected_expedientes.add(exp.id)
    return res


def import_bank_movements(conn: sqlite3.Connection, df, *, actor: str = "system") -> ImportResult:
    res = ImportResult(kind="bank_movements")
    df = _remap(df, "bank_movements")
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        fecha = _parse_date(_cell(row, "fecha"))
        if fecha is None:
            res.add_error(i, "Fecha inválida o ausente")
            continue

        kind = _parse_kind(_cell(row, "kind"))
        monto: Optional[Decimal] = None
        credito = _cell(row, "credito")
        debito = _cell(row, "debito")
        try:
            if credito and parse_money(credito) != ZERO:
                monto, kind = parse_money(credito), MovementKind.CREDIT
            elif debito and parse_money(debito) != ZERO:
                monto, kind = parse_money(debito), MovementKind.DEBIT
            elif _cell(row, "monto"):
                raw = parse_money(_cell(row, "monto"))
                if kind is None:
                    kind = MovementKind.CREDIT if raw >= ZERO else MovementKind.DEBIT
                monto = abs(raw)
        except MoneyError as exc:
            res.add_error(i, f"Monto inválido: {exc}")
            continue

        if monto is None or kind is None:
            res.add_error(i, "No se pudo determinar monto o tipo (crédito/débito)")
            continue

        codigo = _cell(row, "expediente")
        exp = repo.get_expediente_by_codigo(conn, codigo) if codigo else None

        mov = BankMovement(
            fecha=fecha, monto=monto, kind=kind,
            descripcion=_cell(row, "descripcion"), contraparte=_cell(row, "contraparte"),
            referencia_banco=_cell(row, "referencia_banco"), cuenta=_cell(row, "cuenta"),
            expediente_id=exp.id if exp else None,
            asignacion_origen="manual" if exp else "manual",
        )
        dedupe = hashlib.sha256(
            "|".join([
                fecha.isoformat(), str(monto), kind.value, mov.descripcion,
                mov.referencia_banco, mov.cuenta, mov.contraparte,
            ]).encode("utf-8")
        ).hexdigest()
        mid = repo.add_movement(conn, mov, dedupe_key=dedupe)
        if mid is None:
            res.skipped += 1
            continue
        audit.record(conn, action="movement_added", entity="bank_movement", entity_id=mid,
                     payload={"monto": str(monto), "kind": kind.value,
                              "expediente": codigo or None}, actor=actor)
        res.inserted += 1
        if exp:
            res.affected_expedientes.add(exp.id)
    return res


def finalize(conn: sqlite3.Connection, result: ImportResult, *, actor: str = "system") -> None:
    """Recompute status and refresh suggestions for affected expedientes."""
    for eid in result.affected_expedientes:
        matching_service.generate_suggestions(conn, eid, actor=actor)
        status_service.recompute(conn, eid, actor=actor)
