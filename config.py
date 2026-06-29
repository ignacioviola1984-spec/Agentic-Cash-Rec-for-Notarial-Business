"""Central configuration for Cash Control por Expediente.

All tunable business thresholds live here so that the deterministic engine has a
single, auditable source of truth. Nothing in this file is computed by an LLM.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional .env loading (no hard dependency on python-dotenv)
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
EXPORTS_DIR = ROOT_DIR / "exports"
SAMPLE_DIR = ROOT_DIR / "sample_data"


@dataclass(frozen=True)
class Thresholds:
    """Business thresholds that drive the deterministic status classifier.

    Amounts are expressed in ARS (pesos), not centavos, for readability here;
    the engine converts to the internal integer-centavos representation.
    """

    # An expediente whose client advance covers at least this fraction of the
    # total recoverable cost is considered adequately funded.
    funding_ok_ratio: Decimal = Decimal("1.00")

    # Below this coverage ratio (but with no escribanía financing yet) the file
    # is flagged for attention.
    funding_attention_ratio: Decimal = Decimal("0.90")

    # The escribanía is considered to be financing the client whenever its cash
    # position (received - paid on behalf) is negative. This is the absolute
    # peso amount above which financing is treated as Risk rather than Attention.
    financing_risk_amount: Decimal = Decimal("150000.00")

    # Balance pending collection above this amount escalates an otherwise
    # Attention file to Risk.
    balance_to_collect_risk_amount: Decimal = Decimal("300000.00")

    # Tolerance (in ARS) used by the deterministic matcher to treat two amounts
    # as equal. Bank fees / rounding can introduce small deltas.
    match_amount_tolerance: Decimal = Decimal("0.00")

    # Maximum day gap for a deterministic date-proximity match signal.
    match_date_window_days: int = 5


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CASHCONTROL_DB_PATH") or (DATA_DIR / "cashcontrol.db")
        )
    )
    currency: str = field(default_factory=lambda: os.environ.get("CASHCONTROL_CURRENCY", "ARS"))
    llm_model: str = field(
        default_factory=lambda: os.environ.get("CASHCONTROL_LLM_MODEL", "claude-opus-4-8")
    )
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    thresholds: Thresholds = field(default_factory=Thresholds)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


SETTINGS = Settings()

# Canonical recoverable-expense categories used across ingestion, matching and
# reporting. The LLM/heuristic classifier may only assign one of these labels.
EXPENSE_CATEGORIES: tuple[str, ...] = (
    "sellos",            # Impuesto de sellos
    "tasa_registral",    # Tasas de inscripción registral
    "certificaciones",   # Certificados de dominio / inhibición
    "afip",              # Retenciones / impuestos nacionales
    "honorarios",        # Honorarios del escribano
    "diligenciamientos", # Trámites, gestores, oficios
    "gastos_varios",     # Otros gastos recuperables
)

# Categories that represent escribanía remuneration rather than third-party
# disbursements. Kept separate so reports can show disbursements vs fees.
FEE_CATEGORIES: tuple[str, ...] = ("honorarios",)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
