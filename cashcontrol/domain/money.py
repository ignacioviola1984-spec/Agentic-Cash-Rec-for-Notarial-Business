"""Money handling for Cash Control por Expediente.

Design rules:
  * All monetary values are exact. We never use binary floats for money.
  * The canonical storage unit is the integer *centavo* (1 ARS = 100 centavos),
    which keeps SQL ``SUM`` exact and avoids any rounding drift.
  * The domain works in :class:`decimal.Decimal` with 2-decimal quantisation.
  * Parsing accepts Argentine (``1.234.567,89``) and plain (``1234567.89``)
    formats so that uploaded spreadsheets in either convention import cleanly.

This module is the single place where strings/floats become money, so the rest
of the system can assume amounts are already exact.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


class MoneyError(ValueError):
    """Raised when a value cannot be interpreted as a monetary amount."""


def quantize(amount: Decimal) -> Decimal:
    """Round to 2 decimals using banker-free half-up (accounting convention)."""
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def to_centavos(amount: Decimal | int | str) -> int:
    """Convert a peso amount to integer centavos (the storage unit)."""
    dec = amount if isinstance(amount, Decimal) else parse_money(str(amount))
    return int(quantize(dec) * 100)


def from_centavos(centavos: int) -> Decimal:
    """Convert stored integer centavos back to a quantised peso Decimal."""
    if centavos is None:
        return ZERO
    return quantize(Decimal(int(centavos)) / 100)


def parse_money(raw: object) -> Decimal:
    """Parse a human/spreadsheet money string into an exact Decimal.

    Handles thousands separators in both Argentine (``.`` thousands, ``,``
    decimal) and Anglo (``,`` thousands, ``.`` decimal) conventions, a leading
    currency symbol, parentheses for negatives, and an explicit sign.
    """
    if raw is None:
        raise MoneyError("empty monetary value")
    if isinstance(raw, Decimal):
        return quantize(raw)
    if isinstance(raw, (int,)):
        return quantize(Decimal(raw))
    if isinstance(raw, float):
        # Floats only enter from pandas-read numerics; convert via str to avoid
        # binary artefacts, but we still discourage float sources upstream.
        return quantize(Decimal(str(raw)))

    text = str(raw).strip()
    if not text:
        raise MoneyError("empty monetary value")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    # Strip currency symbols / spaces / non-breaking spaces / letters.
    for token in ("ARS", "AR$", "$", " ", " "):
        text = text.replace(token, "")
    text = text.strip()
    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    if not text:
        raise MoneyError(f"no digits in monetary value: {raw!r}")

    has_comma = "," in text
    has_dot = "." in text
    if has_comma and has_dot:
        # The right-most separator is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")  # Argentine
        else:
            text = text.replace(",", "")  # Anglo thousands
    elif has_comma:
        # Comma is decimal if it looks like one (<=2 trailing digits), else
        # treat as thousands separator.
        decimals = len(text.split(",")[-1])
        if decimals in (1, 2):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_dot:
        # Only dots present — ambiguous between decimal point and (Argentine)
        # thousands grouping. Treat as grouping when the digits form a clean
        # 1-3 / 3 / 3 ... pattern (e.g. "1.000", "1.234.567"); otherwise the
        # dot is the decimal point (e.g. "80000.00", "1500.5").
        parts = text.split(".")
        looks_grouped = (
            len(parts) >= 2
            and 1 <= len(parts[0]) <= 3
            and all(len(p) == 3 for p in parts[1:])
        )
        if looks_grouped:
            text = text.replace(".", "")
    # else: only digits — already an integer.

    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise MoneyError(f"invalid monetary value: {raw!r}") from exc

    if negative:
        value = -value
    return quantize(value)


def money_sum(values: Iterable[Decimal]) -> Decimal:
    total = ZERO
    for value in values:
        total += value
    return quantize(total)


def format_ars(amount: Decimal, *, with_symbol: bool = True) -> str:
    """Render an amount in Argentine convention: ``$ 1.234.567,89``."""
    q = quantize(amount)
    sign = "-" if q < 0 else ""
    integer, _, decimals = f"{abs(q):.2f}".partition(".")
    grouped = ""
    for idx, digit in enumerate(reversed(integer)):
        if idx and idx % 3 == 0:
            grouped = "." + grouped
        grouped = digit + grouped
    body = f"{grouped},{decimals}"
    prefix = "$ " if with_symbol else ""
    return f"{sign}{prefix}{body}"
