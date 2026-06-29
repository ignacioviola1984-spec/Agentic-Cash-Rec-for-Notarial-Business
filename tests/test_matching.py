from datetime import date
from decimal import Decimal

from cashcontrol.domain.matching import suggest_matches
from cashcontrol.domain.models import (
    Advance,
    BankMovement,
    Expense,
    MatchTargetType,
    MovementKind,
)


def test_exact_amount_credit_matches_advance():
    mov = BankMovement(date(2024, 3, 4), Decimal("500000.00"), MovementKind.CREDIT,
                       "Transferencia Perez", "Juan Perez", "TRF-0012", id=10)
    adv = Advance(1, date(2024, 3, 4), Decimal("500000.00"), "transferencia", "TRF-0012", id=5)
    out = suggest_matches([mov], [adv], [])
    assert len(out) == 1
    assert out[0].target_type == MatchTargetType.ADVANCE
    assert out[0].target_id == 5
    assert out[0].score == Decimal("1.00")  # amount + date + ref overlap


def test_debit_matches_expense_not_advance():
    mov = BankMovement(date(2024, 3, 6), Decimal("200000.00"), MovementKind.DEBIT,
                       "Pago ARBA", "ARBA", "SEL-77", id=11)
    adv = Advance(1, date(2024, 3, 6), Decimal("200000.00"), id=6)
    exp = Expense(1, date(2024, 3, 6), Decimal("200000.00"), "sellos", proveedor="ARBA",
                  referencia="SEL-77", id=7)
    out = suggest_matches([mov], [adv], [exp])
    assert len(out) == 1
    assert out[0].target_type == MatchTargetType.EXPENSE
    assert out[0].target_id == 7


def test_amount_mismatch_no_suggestion():
    mov = BankMovement(date(2024, 3, 6), Decimal("199000.00"), MovementKind.DEBIT, id=12)
    exp = Expense(1, date(2024, 3, 6), Decimal("200000.00"), "sellos", id=8)
    assert suggest_matches([mov], [], [exp]) == []


def test_already_matched_targets_excluded():
    mov = BankMovement(date(2024, 3, 4), Decimal("500000.00"), MovementKind.CREDIT, id=10)
    adv = Advance(1, date(2024, 3, 4), Decimal("500000.00"), id=5)
    out = suggest_matches([mov], [adv], [],
                          already_matched_targets={(MatchTargetType.ADVANCE.value, 5)})
    assert out == []


def test_date_far_lowers_but_amount_anchors():
    mov = BankMovement(date(2024, 3, 30), Decimal("80000.00"), MovementKind.DEBIT, id=13)
    exp = Expense(1, date(2024, 3, 1), Decimal("80000.00"), "tasa_registral", id=9)
    out = suggest_matches([mov], [], [exp])
    assert len(out) == 1
    # Amount only (date outside window, no text overlap) -> 0.60
    assert out[0].score == Decimal("0.60")
