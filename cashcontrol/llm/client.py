"""Guarded LLM adapter with a deterministic fallback.

Public surface:
  * :func:`classify_expense`  -> category label (closed set)
  * :func:`suggest_expediente` -> expediente code or None
  * :func:`narrative`          -> grounded prose (or deterministic fallback)

Every method works without an API key by using rule-based heuristics. When a key
is present the LLM is consulted but its output is validated: category labels must
be in the allowed set; narratives must pass the number-grounding guard.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional, Sequence

import config

# Keyword -> category heuristics (used as fallback AND to validate LLM output).
_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("sello", "impuesto de sello"), "sellos"),
    (("registro", "inscrip", "rpi", "matricul", "folio real"), "tasa_registral"),
    (("certific", "dominio", "inhibic", "anotacion", "informe"), "certificaciones"),
    (("afip", "iva", "retenc", "percep", "ganancia", "iibb", "ingresos brutos"), "afip"),
    (("honorar", "escriban"), "honorarios"),
    (("gestor", "oficio", "diligenc", "tramite", "tasa de justicia", "edicto"), "diligenciamientos"),
]


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return folded.lower()


def classify_expense_heuristic(concepto: str, proveedor: str = "") -> str:
    blob = _norm(f"{concepto} {proveedor}")
    for keys, category in _KEYWORDS:
        if any(k in blob for k in keys):
            return category
    return "gastos_varios"


def suggest_expediente_heuristic(
    movimiento_text: str, expedientes: Sequence[tuple[str, str, str]]
) -> Optional[str]:
    """expedientes: list of (codigo, caratula, cliente)."""
    blob = _norm(movimiento_text)
    for codigo, caratula, cliente in expedientes:
        if codigo and _norm(codigo) in blob:
            return codigo
    # Fall back to client-name token overlap.
    for codigo, caratula, cliente in expedientes:
        cli = _norm(cliente)
        tokens = [t for t in cli.split() if len(t) >= 4]
        if tokens and all(t in blob for t in tokens):
            return codigo
    for codigo, caratula, cliente in expedientes:
        for t in (t for t in _norm(cliente).split() if len(t) >= 5):
            if t in blob:
                return codigo
    return None


_NUMBER_RE = re.compile(r"\d[\d.,]*")


def _numbers_in(text: str) -> set[str]:
    return {n.strip(".,").replace(".", "").replace(",", "") for n in _NUMBER_RE.findall(text)}


def narrative_grounding_ok(output: str, grounding: str) -> bool:
    """True iff every numeric token in *output* also appears in *grounding*."""
    allowed = _numbers_in(grounding)
    for num in _numbers_in(output):
        if num and num not in allowed:
            return False
    return True


class LLMClient:
    def __init__(self) -> None:
        self._settings = config.SETTINGS
        self._client = None
        if self._settings.llm_enabled:
            try:  # pragma: no cover - exercised only with a real key
                import anthropic

                self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
            except Exception:
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _complete(self, system: str, user: str, max_tokens: int = 200) -> Optional[str]:
        if self._client is None:
            return None
        try:  # pragma: no cover - requires network/key
            msg = self._client.messages.create(
                model=self._settings.llm_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
            return "".join(parts).strip()
        except Exception:
            return None

    # -- public API ------------------------------------------------------
    def classify_expense(self, concepto: str, proveedor: str = "") -> tuple[str, str]:
        """Return (category, origen) where origen is 'rule' or 'llm'."""
        from .prompts import CLASSIFY_SYSTEM, CLASSIFY_USER

        categories = config.EXPENSE_CATEGORIES
        out = self._complete(
            CLASSIFY_SYSTEM,
            CLASSIFY_USER.format(
                categories=", ".join(categories), concepto=concepto, proveedor=proveedor
            ),
            max_tokens=12,
        )
        if out:
            label = _norm(out).strip().strip(".")
            if label in categories:
                return label, "llm"
        return classify_expense_heuristic(concepto, proveedor), "rule"

    def suggest_expediente(
        self, movimiento_text: str, expedientes: Sequence[tuple[str, str, str]]
    ) -> tuple[Optional[str], str]:
        from .prompts import ASSIGN_SYSTEM, ASSIGN_USER

        valid_codes = {c for c, _, _ in expedientes}
        listing = "\n".join(f"- {c} | {ca} | {cl}" for c, ca, cl in expedientes)
        out = self._complete(
            ASSIGN_SYSTEM,
            ASSIGN_USER.format(movimiento=movimiento_text, expedientes=listing),
            max_tokens=20,
        )
        if out:
            candidate = out.strip().split()[0].strip().strip(".,")
            if candidate in valid_codes:
                return candidate, "llm"
        return suggest_expediente_heuristic(movimiento_text, expedientes), "rule"

    def narrative(self, facts: str) -> tuple[str, str]:
        """Return (text, origen). LLM output is rejected if it introduces any
        number absent from *facts*; in that case we return the facts verbatim."""
        from .prompts import NARRATIVE_SYSTEM, NARRATIVE_USER

        out = self._complete(NARRATIVE_SYSTEM, NARRATIVE_USER.format(facts=facts), max_tokens=300)
        if out and narrative_grounding_ok(out, facts):
            return out, "llm"
        return facts, "deterministic"


_DEFAULT: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LLMClient()
    return _DEFAULT
