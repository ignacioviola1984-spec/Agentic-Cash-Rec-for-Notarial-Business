from decimal import Decimal

import pytest

from cashcontrol.domain.money import (
    MoneyError,
    format_ars,
    from_centavos,
    money_sum,
    parse_money,
    to_centavos,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234.567,89", Decimal("1234567.89")),   # Argentine
        ("1,234,567.89", Decimal("1234567.89")),   # Anglo
        ("$ 500.000,00", Decimal("500000.00")),
        ("AR$ 1.000", Decimal("1000.00")),
        ("1500,5", Decimal("1500.50")),
        ("1500.5", Decimal("1500.50")),
        ("(2.000,00)", Decimal("-2000.00")),       # parentheses negative
        ("-3000", Decimal("-3000.00")),
        ("12345", Decimal("12345.00")),
        (1000, Decimal("1000.00")),
        (1000.25, Decimal("1000.25")),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def test_parse_money_invalid():
    with pytest.raises(MoneyError):
        parse_money("")
    with pytest.raises(MoneyError):
        parse_money("abc")


def test_centavos_roundtrip():
    assert to_centavos(Decimal("1234.56")) == 123456
    assert from_centavos(123456) == Decimal("1234.56")
    assert from_centavos(to_centavos(parse_money("999.999,99"))) == Decimal("999999.99")


def test_money_sum_exact():
    values = [Decimal("0.10")] * 10  # classic float-trap; must equal 1.00
    assert money_sum(values) == Decimal("1.00")


def test_format_ars():
    assert format_ars(Decimal("1234567.89")) == "$ 1.234.567,89"
    assert format_ars(Decimal("-500.00")) == "-$ 500,00"
    assert format_ars(Decimal("0")) == "$ 0,00"
    assert format_ars(Decimal("1000"), with_symbol=False) == "1.000,00"
