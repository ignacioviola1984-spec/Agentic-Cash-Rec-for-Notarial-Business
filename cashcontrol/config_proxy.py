"""Thin, lazy bridge from the package to the root ``config`` module.

The deterministic domain reads thresholds and category lists through these
functions so it never imports the root module at definition time (avoiding
import-order coupling) and so tests can monkeypatch a single seam.
"""
from __future__ import annotations

from decimal import Decimal


def _settings():
    import config  # root-level module

    return config.SETTINGS


def thresholds():
    return _settings().thresholds


def fee_categories() -> tuple[str, ...]:
    import config

    return config.FEE_CATEGORIES


def expense_categories() -> tuple[str, ...]:
    import config

    return config.EXPENSE_CATEGORIES


def currency() -> str:
    return _settings().currency
