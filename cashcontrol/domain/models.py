"""Domain models for Cash Control por Expediente.

These are plain dataclasses used throughout the deterministic core. Amounts are
carried as :class:`decimal.Decimal` (pesos); persistence converts to/from
integer centavos at the repository boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional

from .money import ZERO


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class ExpedienteStatus(str, Enum):
    """Financial health classification, computed deterministically."""

    OK = "OK"
    ATENCION = "Atencion"
    RIESGO = "Riesgo"
    BLOQUEADO = "Bloqueado"


class MovementKind(str, Enum):
    """Direction of a bank movement from the escribanía's perspective."""

    CREDIT = "credit"  # money in (client advance, refund received)
    DEBIT = "debit"    # money out (expense paid on behalf of client)


class ExpenseStatus(str, Enum):
    PENDING = "pending"  # accrued/owed but not yet disbursed
    PAID = "paid"        # disbursed by the escribanía


class PaidBy(str, Enum):
    ESCRIBANIA = "escribania"  # escribanía advanced its own funds
    CLIENT = "client"          # paid directly with client-provided funds


class MatchStatus(str, Enum):
    SUGGESTED = "suggested"   # produced by the deterministic matcher
    CONFIRMED = "confirmed"   # signed off by a human reviewer
    REJECTED = "rejected"     # explicitly rejected by a human reviewer


class MatchTargetType(str, Enum):
    ADVANCE = "advance"   # bank movement <-> client advance/receipt
    EXPENSE = "expense"   # bank movement <-> recoverable expense


class ReviewStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ReviewSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"  # presence forces the expediente to Bloqueado


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
@dataclass
class Expediente:
    codigo: str
    caratula: str
    cliente: str
    escribano: str = ""
    tipo_acto: str = ""          # e.g. compraventa, hipoteca, sucesion
    fecha_apertura: Optional[date] = None
    id: Optional[int] = None
    notas: str = ""


@dataclass
class Advance:
    """Money received FROM the client (provisión de fondos / anticipo)."""

    expediente_id: int
    fecha: date
    monto: Decimal
    metodo: str = ""            # transferencia, cheque, efectivo
    referencia: str = ""
    id: Optional[int] = None


@dataclass
class Expense:
    """A recoverable expense the escribanía incurs on the client's behalf."""

    expediente_id: int
    fecha: date
    monto: Decimal
    categoria: str               # one of config.EXPENSE_CATEGORIES
    concepto: str = ""
    estado: ExpenseStatus = ExpenseStatus.PENDING
    pagado_por: PaidBy = PaidBy.ESCRIBANIA
    proveedor: str = ""
    referencia: str = ""
    id: Optional[int] = None
    categoria_origen: str = "manual"   # manual | rule | llm — provenance only


@dataclass
class BankMovement:
    """A line on the escribanía's bank/account statement."""

    fecha: date
    monto: Decimal               # always positive magnitude
    kind: MovementKind
    descripcion: str = ""
    contraparte: str = ""
    referencia_banco: str = ""
    cuenta: str = ""
    expediente_id: Optional[int] = None  # assignment (may be suggested/None)
    id: Optional[int] = None
    asignacion_origen: str = "manual"    # manual | rule | llm — provenance


@dataclass
class Match:
    """A link between a bank movement and an advance or expense.

    Suggested by the deterministic matcher; only a human reviewer can confirm.
    """

    movement_id: int
    target_type: MatchTargetType
    target_id: int
    score: Decimal = ZERO        # deterministic 0..1 confidence
    status: MatchStatus = MatchStatus.SUGGESTED
    rationale: str = ""          # which deterministic signals fired
    id: Optional[int] = None


@dataclass
class ReviewItem:
    """An action requiring human review."""

    expediente_id: Optional[int]
    tipo: str                    # e.g. unmatched_movement, financing, over_refund
    severity: ReviewSeverity
    mensaje: str
    status: ReviewStatus = ReviewStatus.OPEN
    contexto: str = ""           # JSON blob of supporting deterministic facts
    id: Optional[int] = None
