"""Repositories: typed CRUD over the SQLite schema.

All money crosses this boundary as integer centavos; callers work in Decimal.
Dates are stored as ISO ``YYYY-MM-DD`` strings.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from ..domain.models import (
    Advance,
    BankMovement,
    Expediente,
    Expense,
    ExpenseStatus,
    Match,
    MatchStatus,
    MatchTargetType,
    MovementKind,
    PaidBy,
    ReviewItem,
    ReviewSeverity,
    ReviewStatus,
)
from ..domain.money import from_centavos, to_centavos


def _d(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


# ---------------------------------------------------------------------------
# Expedientes
# ---------------------------------------------------------------------------
def _row_to_expediente(r: sqlite3.Row) -> Expediente:
    return Expediente(
        id=r["id"],
        codigo=r["codigo"],
        caratula=r["caratula"],
        cliente=r["cliente"],
        escribano=r["escribano"],
        tipo_acto=r["tipo_acto"],
        fecha_apertura=_d(r["fecha_apertura"]),
        notas=r["notas"],
    )


def create_expediente(conn: sqlite3.Connection, e: Expediente) -> int:
    cur = conn.execute(
        """INSERT INTO expedientes (codigo, caratula, cliente, escribano, tipo_acto,
               fecha_apertura, notas)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            e.codigo,
            e.caratula,
            e.cliente,
            e.escribano,
            e.tipo_acto,
            e.fecha_apertura.isoformat() if e.fecha_apertura else None,
            e.notas,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_expediente(conn: sqlite3.Connection, expediente_id: int) -> Optional[Expediente]:
    r = conn.execute("SELECT * FROM expedientes WHERE id = ?", (expediente_id,)).fetchone()
    return _row_to_expediente(r) if r else None


def get_expediente_by_codigo(conn: sqlite3.Connection, codigo: str) -> Optional[Expediente]:
    r = conn.execute("SELECT * FROM expedientes WHERE codigo = ?", (codigo,)).fetchone()
    return _row_to_expediente(r) if r else None


def list_expedientes(conn: sqlite3.Connection) -> list[Expediente]:
    rows = conn.execute("SELECT * FROM expedientes ORDER BY codigo").fetchall()
    return [_row_to_expediente(r) for r in rows]


# ---------------------------------------------------------------------------
# Advances
# ---------------------------------------------------------------------------
def _row_to_advance(r: sqlite3.Row) -> Advance:
    return Advance(
        id=r["id"],
        expediente_id=r["expediente_id"],
        fecha=_d(r["fecha"]),
        monto=from_centavos(r["monto_centavos"]),
        metodo=r["metodo"],
        referencia=r["referencia"],
    )


def add_advance(conn: sqlite3.Connection, a: Advance) -> int:
    cur = conn.execute(
        """INSERT INTO advances (expediente_id, fecha, monto_centavos, metodo, referencia)
           VALUES (?, ?, ?, ?, ?)""",
        (a.expediente_id, a.fecha.isoformat(), to_centavos(a.monto), a.metodo, a.referencia),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_advances(conn: sqlite3.Connection, expediente_id: int) -> list[Advance]:
    rows = conn.execute(
        "SELECT * FROM advances WHERE expediente_id = ? ORDER BY fecha, id", (expediente_id,)
    ).fetchall()
    return [_row_to_advance(r) for r in rows]


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
def _row_to_expense(r: sqlite3.Row) -> Expense:
    return Expense(
        id=r["id"],
        expediente_id=r["expediente_id"],
        fecha=_d(r["fecha"]),
        monto=from_centavos(r["monto_centavos"]),
        categoria=r["categoria"],
        concepto=r["concepto"],
        estado=ExpenseStatus(r["estado"]),
        pagado_por=PaidBy(r["pagado_por"]),
        proveedor=r["proveedor"],
        referencia=r["referencia"],
        categoria_origen=r["categoria_origen"],
    )


def add_expense(conn: sqlite3.Connection, e: Expense) -> int:
    cur = conn.execute(
        """INSERT INTO expenses (expediente_id, fecha, monto_centavos, categoria, concepto,
               estado, pagado_por, proveedor, referencia, categoria_origen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            e.expediente_id,
            e.fecha.isoformat(),
            to_centavos(e.monto),
            e.categoria,
            e.concepto,
            e.estado.value,
            e.pagado_por.value,
            e.proveedor,
            e.referencia,
            e.categoria_origen,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_expenses(conn: sqlite3.Connection, expediente_id: int) -> list[Expense]:
    rows = conn.execute(
        "SELECT * FROM expenses WHERE expediente_id = ? ORDER BY fecha, id", (expediente_id,)
    ).fetchall()
    return [_row_to_expense(r) for r in rows]


def set_expense_estado(conn: sqlite3.Connection, expense_id: int, estado: ExpenseStatus) -> None:
    conn.execute("UPDATE expenses SET estado = ? WHERE id = ?", (estado.value, expense_id))
    conn.commit()


# ---------------------------------------------------------------------------
# Bank movements
# ---------------------------------------------------------------------------
def _row_to_movement(r: sqlite3.Row) -> BankMovement:
    return BankMovement(
        id=r["id"],
        fecha=_d(r["fecha"]),
        monto=from_centavos(r["monto_centavos"]),
        kind=MovementKind(r["kind"]),
        descripcion=r["descripcion"],
        contraparte=r["contraparte"],
        referencia_banco=r["referencia_banco"],
        cuenta=r["cuenta"],
        expediente_id=r["expediente_id"],
        asignacion_origen=r["asignacion_origen"],
    )


def add_movement(conn: sqlite3.Connection, m: BankMovement, dedupe_key: Optional[str] = None) -> Optional[int]:
    """Insert a bank movement. Returns the new id, or ``None`` if it duplicates
    an existing row (same ``dedupe_key``)."""
    try:
        cur = conn.execute(
            """INSERT INTO bank_movements (fecha, monto_centavos, kind, descripcion,
                   contraparte, referencia_banco, cuenta, expediente_id, asignacion_origen, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                m.fecha.isoformat(),
                to_centavos(m.monto),
                m.kind.value,
                m.descripcion,
                m.contraparte,
                m.referencia_banco,
                m.cuenta,
                m.expediente_id,
                m.asignacion_origen,
                dedupe_key,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None


def list_movements(conn: sqlite3.Connection, expediente_id: int) -> list[BankMovement]:
    rows = conn.execute(
        "SELECT * FROM bank_movements WHERE expediente_id = ? ORDER BY fecha, id",
        (expediente_id,),
    ).fetchall()
    return [_row_to_movement(r) for r in rows]


def list_unassigned_movements(conn: sqlite3.Connection) -> list[BankMovement]:
    rows = conn.execute(
        "SELECT * FROM bank_movements WHERE expediente_id IS NULL ORDER BY fecha, id"
    ).fetchall()
    return [_row_to_movement(r) for r in rows]


def list_all_movements(conn: sqlite3.Connection) -> list[BankMovement]:
    rows = conn.execute("SELECT * FROM bank_movements ORDER BY fecha, id").fetchall()
    return [_row_to_movement(r) for r in rows]


def get_movement(conn: sqlite3.Connection, movement_id: int) -> Optional[BankMovement]:
    r = conn.execute("SELECT * FROM bank_movements WHERE id = ?", (movement_id,)).fetchone()
    return _row_to_movement(r) if r else None


def assign_movement(
    conn: sqlite3.Connection, movement_id: int, expediente_id: Optional[int], origen: str = "manual"
) -> None:
    conn.execute(
        "UPDATE bank_movements SET expediente_id = ?, asignacion_origen = ? WHERE id = ?",
        (expediente_id, origen, movement_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------
def _row_to_match(r: sqlite3.Row) -> Match:
    return Match(
        id=r["id"],
        movement_id=r["movement_id"],
        target_type=MatchTargetType(r["target_type"]),
        target_id=r["target_id"],
        score=Decimal(r["score"]),
        status=MatchStatus(r["status"]),
        rationale=r["rationale"],
    )


def upsert_suggested_match(conn: sqlite3.Connection, m: Match) -> int:
    """Insert a suggestion; if the (movement, target) pair already exists and is
    still ``suggested``, refresh its score/rationale. Confirmed/rejected pairs
    are left untouched."""
    existing = conn.execute(
        "SELECT id, status FROM matches WHERE movement_id = ? AND target_type = ? AND target_id = ?",
        (m.movement_id, m.target_type.value, m.target_id),
    ).fetchone()
    if existing:
        if existing["status"] == MatchStatus.SUGGESTED.value:
            conn.execute(
                "UPDATE matches SET score = ?, rationale = ? WHERE id = ?",
                (str(m.score), m.rationale, existing["id"]),
            )
            conn.commit()
        return int(existing["id"])
    cur = conn.execute(
        """INSERT INTO matches (movement_id, target_type, target_id, score, status, rationale)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (m.movement_id, m.target_type.value, m.target_id, str(m.score), m.status.value, m.rationale),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_match(conn: sqlite3.Connection, match_id: int) -> Optional[Match]:
    r = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    return _row_to_match(r) if r else None


def set_match_status(conn: sqlite3.Connection, match_id: int, status: MatchStatus) -> None:
    conn.execute(
        "UPDATE matches SET status = ?, resolved_at = datetime('now') WHERE id = ?",
        (status.value, match_id),
    )
    conn.commit()


def list_matches_for_movement(conn: sqlite3.Connection, movement_id: int) -> list[Match]:
    rows = conn.execute(
        "SELECT * FROM matches WHERE movement_id = ? ORDER BY CAST(score AS REAL) DESC",
        (movement_id,),
    ).fetchall()
    return [_row_to_match(r) for r in rows]


def list_matches_for_expediente(conn: sqlite3.Connection, expediente_id: int) -> list[Match]:
    rows = conn.execute(
        """SELECT mt.* FROM matches mt
           JOIN bank_movements bm ON bm.id = mt.movement_id
           WHERE bm.expediente_id = ?
           ORDER BY CAST(mt.score AS REAL) DESC""",
        (expediente_id,),
    ).fetchall()
    return [_row_to_match(r) for r in rows]


def confirmed_target_keys(conn: sqlite3.Connection) -> set[tuple[str, int]]:
    rows = conn.execute(
        "SELECT target_type, target_id FROM matches WHERE status = 'confirmed'"
    ).fetchall()
    return {(r["target_type"], r["target_id"]) for r in rows}


def confirmed_movement_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT movement_id FROM matches WHERE status = 'confirmed'"
    ).fetchall()
    return {r["movement_id"] for r in rows}


# ---------------------------------------------------------------------------
# Review items
# ---------------------------------------------------------------------------
def _row_to_review(r: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=r["id"],
        expediente_id=r["expediente_id"],
        tipo=r["tipo"],
        severity=ReviewSeverity(r["severity"]),
        mensaje=r["mensaje"],
        status=ReviewStatus(r["status"]),
        contexto=r["contexto"],
    )


def add_review_item(conn: sqlite3.Connection, item: ReviewItem, dedupe_key: Optional[str] = None) -> Optional[int]:
    try:
        cur = conn.execute(
            """INSERT INTO review_items (expediente_id, tipo, severity, mensaje, status, contexto, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                item.expediente_id,
                item.tipo,
                item.severity.value,
                item.mensaje,
                item.status.value,
                item.contexto,
                dedupe_key,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None  # already raised (same dedupe_key)


def list_review_items(
    conn: sqlite3.Connection,
    *,
    expediente_id: Optional[int] = None,
    only_open: bool = False,
) -> list[ReviewItem]:
    clauses, params = [], []
    if expediente_id is not None:
        clauses.append("expediente_id = ?")
        params.append(expediente_id)
    if only_open:
        clauses.append("status = 'open'")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM review_items{where} ORDER BY "
        "CASE severity WHEN 'blocking' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC",
        params,
    ).fetchall()
    return [_row_to_review(r) for r in rows]


def count_open_blocking(conn: sqlite3.Connection, expediente_id: int) -> int:
    r = conn.execute(
        "SELECT COUNT(*) AS n FROM review_items "
        "WHERE expediente_id = ? AND status = 'open' AND severity = 'blocking'",
        (expediente_id,),
    ).fetchone()
    return int(r["n"])


def set_review_status(
    conn: sqlite3.Connection, review_id: int, status: ReviewStatus, resolved_by: str = ""
) -> None:
    conn.execute(
        "UPDATE review_items SET status = ?, resolved_at = datetime('now'), resolved_by = ? WHERE id = ?",
        (status.value, resolved_by, review_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Status snapshots
# ---------------------------------------------------------------------------
def upsert_status(
    conn: sqlite3.Connection, expediente_id: int, status: str, reasons_json: str, summary_json: str
) -> None:
    conn.execute(
        """INSERT INTO expediente_status (expediente_id, status, reasons, summary_json, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(expediente_id) DO UPDATE SET
               status = excluded.status,
               reasons = excluded.reasons,
               summary_json = excluded.summary_json,
               updated_at = datetime('now')""",
        (expediente_id, status, reasons_json, summary_json),
    )
    conn.commit()


def get_status_row(conn: sqlite3.Connection, expediente_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM expediente_status WHERE expediente_id = ?", (expediente_id,)
    ).fetchone()


def list_status_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM expediente_status").fetchall()
