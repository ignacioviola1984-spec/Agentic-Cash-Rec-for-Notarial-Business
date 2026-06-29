"""Cash Control por Expediente — deterministic cash-control core for an
Argentine escribanía.

Package layout:
  cashcontrol.domain    Pure, deterministic business logic (no I/O, no LLM).
  cashcontrol.data      SQLite persistence, repositories, hash-chained audit.
  cashcontrol.services  Ingestion, matching orchestration, review, reports.
  cashcontrol.llm       Guarded LLM adapter (labels/prose only, never amounts).
"""

__version__ = "1.0.0"
