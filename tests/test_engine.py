from datetime import date
from decimal import Decimal

from cashcontrol.domain.engine import compute_summary
from cashcontrol.domain.models import Advance, Expense, ExpenseStatus, PaidBy


def adv(monto):
    return Advance(expediente_id=1, fecha=date(2024, 1, 1), monto=Decimal(monto))


def exp(monto, categoria="sellos", estado=ExpenseStatus.PAID, pagado_por=PaidBy.ESCRIBANIA):
    return Expense(expediente_id=1, fecha=date(2024, 1, 2), monto=Decimal(monto),
                   categoria=categoria, estado=estado, pagado_por=pagado_por)


def test_fully_funded_surplus():
    s = compute_summary(1, [adv("500000")], [
        exp("200000"), exp("80000", "tasa_registral"), exp("150000", "honorarios"),
    ])
    assert s.total_recibido == Decimal("500000.00")
    assert s.costo_recuperable == Decimal("430000.00")
    assert s.honorarios_total == Decimal("150000.00")
    assert s.desembolsos_total == Decimal("280000.00")
    assert s.gastos_pagados == Decimal("430000.00")
    assert s.gastos_pendientes == Decimal("0.00")
    assert s.posicion_caja == Decimal("70000.00")
    assert s.financiando is False
    assert s.anticipo_suficiente is True
    assert s.excedente_a_devolver == Decimal("70000.00")
    assert s.saldo_a_cobrar == Decimal("0.00")


def test_financing_detection():
    s = compute_summary(1, [adv("100000")], [exp("180000"), exp("90000", "tasa_registral")])
    assert s.gastos_pagados == Decimal("270000.00")
    assert s.posicion_caja == Decimal("-170000.00")
    assert s.financiando is True
    assert s.monto_financiado == Decimal("170000.00")
    assert s.saldo_a_cobrar == Decimal("170000.00")


def test_pending_does_not_consume_cash():
    # Pending expenses are owed but not yet disbursed: cash position unaffected.
    s = compute_summary(1, [adv("330000")], [
        exp("120000"), exp("60000", "certificaciones"),
        exp("50000", "tasa_registral", ExpenseStatus.PENDING),
        exp("120000", "honorarios", ExpenseStatus.PENDING),
    ])
    assert s.gastos_pagados == Decimal("180000.00")
    assert s.gastos_pendientes == Decimal("170000.00")
    assert s.posicion_caja == Decimal("150000.00")
    assert s.costo_recuperable == Decimal("350000.00")
    assert s.saldo_a_cobrar == Decimal("20000.00")
    assert s.anticipo_suficiente is False


def test_client_paid_excluded_from_recoverable():
    s = compute_summary(1, [adv("100000")], [
        exp("50000"), exp("40000", "sellos", ExpenseStatus.PAID, PaidBy.CLIENT),
    ])
    # Only the escribanía-funded expense is recoverable / consumes cash.
    assert s.costo_recuperable == Decimal("50000.00")
    assert s.pagado_por_cliente == Decimal("40000.00")
    assert s.gastos_pagados == Decimal("50000.00")
    assert s.posicion_caja == Decimal("50000.00")


def test_no_cost_coverage_is_full():
    s = compute_summary(1, [adv("100000")], [])
    assert s.cobertura == Decimal("1.00")
    assert s.anticipo_suficiente is True
