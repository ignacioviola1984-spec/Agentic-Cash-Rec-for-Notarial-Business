"""Deterministic reconciliation matcher.

Proposes links between bank movements and advances/expenses using only explicit
signals: amount equality (the anchor), date proximity and textual overlap.
Every suggestion carries a 0..1 score and a human-readable rationale listing the
signals that fired.

Crucially, the matcher only ever *suggests*. A suggestion is not a
reconciliation: a human reviewer must confirm it (HITL). The LLM may separately
propose which expediente a movement belongs to, but it never creates or alters
the amounts compared here.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from .. import config_proxy as _cfg
from .models import (
    Advance,
    BankMovement,
    Expense,
    Match,
    MatchStatus,
    MatchTargetType,
    MovementKind,
)
from .money import quantize

# Signal weights (sum to 1.0 when all fire).
W_AMOUNT = Decimal("0.60")
W_DATE = Decimal("0.20")
W_TEXT = Decimal("0.20")
SUGGEST_THRESHOLD = Decimal("0.60")  # amount alone already qualifies


def _normalize(text: str) -> set[str]:
    if not text:
        return set()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    cleaned = "".join(c if c.isalnum() else " " for c in folded)
    return {tok for tok in cleaned.split() if len(tok) >= 3}


def _date_score(days_apart: int, window: int) -> Decimal:
    if days_apart < 0 or days_apart > window:
        return Decimal("0")
    if window <= 0:
        return W_DATE if days_apart == 0 else Decimal("0")
    factor = Decimal(window - days_apart) / Decimal(window)
    return quantize(W_DATE * factor)


@dataclass(frozen=True)
class _Candidate:
    target_type: MatchTargetType
    target_id: int
    monto: Decimal
    fecha: object
    tokens: set[str]


def _movement_tokens(mov: BankMovement) -> set[str]:
    return _normalize(" ".join([mov.descripcion, mov.contraparte, mov.referencia_banco]))


def suggest_matches(
    movements: Sequence[BankMovement],
    advances: Sequence[Advance],
    expenses: Sequence[Expense],
    *,
    already_matched_targets: Iterable[tuple[str, int]] = (),
) -> list[Match]:
    """Return suggested matches sorted by descending score.

    ``already_matched_targets`` is a set of ``(target_type_value, target_id)``
    pairs already confirmed elsewhere, which are excluded from new suggestions.
    """
    th = _cfg.thresholds()
    tolerance = th.match_amount_tolerance
    window = th.match_date_window_days
    taken = set(already_matched_targets)

    candidates: list[_Candidate] = []
    for adv in advances:
        if adv.id is None or (MatchTargetType.ADVANCE.value, adv.id) in taken:
            continue
        candidates.append(
            _Candidate(
                MatchTargetType.ADVANCE,
                adv.id,
                adv.monto,
                adv.fecha,
                _normalize(" ".join([adv.metodo, adv.referencia])),
            )
        )
    for exp in expenses:
        if exp.id is None or (MatchTargetType.EXPENSE.value, exp.id) in taken:
            continue
        candidates.append(
            _Candidate(
                MatchTargetType.EXPENSE,
                exp.id,
                exp.monto,
                exp.fecha,
                _normalize(" ".join([exp.proveedor, exp.referencia, exp.concepto])),
            )
        )

    suggestions: list[Match] = []
    for mov in movements:
        if mov.id is None:
            continue
        want = (
            MatchTargetType.ADVANCE
            if mov.kind == MovementKind.CREDIT
            else MatchTargetType.EXPENSE
        )
        mov_tokens = _movement_tokens(mov)
        for cand in candidates:
            if cand.target_type != want:
                continue
            if abs(mov.monto - cand.monto) > tolerance:
                continue  # amount is the anchor; no amount match -> no suggestion

            signals = ["monto exacto" if tolerance == 0 else "monto dentro de tolerancia"]
            score = W_AMOUNT

            try:
                days_apart = abs((mov.fecha - cand.fecha).days)
            except Exception:  # pragma: no cover - defensive on bad dates
                days_apart = window + 1
            ds = _date_score(days_apart, window)
            if ds > 0:
                score += ds
                signals.append(f"fecha ±{days_apart}d")

            overlap = mov_tokens & cand.tokens
            if overlap:
                score += W_TEXT
                signals.append("ref: " + ", ".join(sorted(overlap)[:3]))

            score = quantize(min(score, Decimal("1.00")))
            if score >= SUGGEST_THRESHOLD:
                suggestions.append(
                    Match(
                        movement_id=mov.id,
                        target_type=cand.target_type,
                        target_id=cand.target_id,
                        score=score,
                        status=MatchStatus.SUGGESTED,
                        rationale="; ".join(signals),
                    )
                )

    suggestions.sort(key=lambda m: m.score, reverse=True)
    return suggestions
