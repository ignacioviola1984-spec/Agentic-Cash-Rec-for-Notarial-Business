from datetime import date
from decimal import Decimal

from cashcontrol.domain.engine import compute_summary
from cashcontrol.domain.models import Advance, Expense, ExpenseStatus, ExpedienteStatus, PaidBy
from cashcontrol.domain.status import classify


def adv(m):
    return Advance(1, date(2024, 1, 1), Decimal(m))


def exp(m, cat="sellos", estado=ExpenseStatus.PAID):
    return Expense(1, date(2024, 1, 2), Decimal(m), cat, estado=estado)


def test_ok_when_funded_and_clean():
    s = compute_summary(1, [adv("500000")], [exp("200000"), exp("80000", "tasa_registral")])
    r = classify(s, open_blocking_reviews=0, unmatched_movements=0)
    assert r.status == ExpedienteStatus.OK


def test_blocked_overrides_everything():
    s = compute_summary(1, [adv("500000")], [exp("200000")])
    r = classify(s, open_blocking_reviews=1, unmatched_movements=0)
    assert r.status == ExpedienteStatus.BLOQUEADO


def test_risk_on_large_financing():
    s = compute_summary(1, [adv("100000")], [exp("180000"), exp("90000", "tasa_registral")])
    r = classify(s)
    assert r.status == ExpedienteStatus.RIESGO


def test_attention_on_unmatched_only():
    s = compute_summary(1, [adv("500000")], [exp("200000")])
    r = classify(s, unmatched_movements=2)
    assert r.status == ExpedienteStatus.ATENCION


def test_attention_on_minor_shortfall():
    # Coverage >= 0.90 keeps it Atención (not Riesgo), with pending expenses.
    s = compute_summary(1, [adv("330000")], [
        exp("180000"), exp("170000", "honorarios", ExpenseStatus.PENDING)])
    r = classify(s)
    assert r.status == ExpedienteStatus.ATENCION
