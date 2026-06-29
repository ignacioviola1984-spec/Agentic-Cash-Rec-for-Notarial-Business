"""Deterministic domain layer: money, models, balance engine, matching, status.

Nothing in this package performs I/O or calls an LLM. Every monetary figure is a
Decimal derived from stored source data. This is the trust boundary that
guarantees financial numbers are never invented.
"""
