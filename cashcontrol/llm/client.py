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

import os
import re
import unicodedata
from typing import Optional, Sequence

import config


def _resolve_api_key() -> str:
    """Resolve the Anthropic key from (in order) the environment, the root
    settings, and Streamlit secrets. This lets the same code pick up the key
    whether it is set via .env locally or via Streamlit Cloud's secrets UI."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    key = (config.SETTINGS.anthropic_api_key or "").strip()
    if key:
        return key
    try:  # Streamlit secrets are only available inside a Streamlit runtime.
        import streamlit as st

        val = st.secrets.get("ANTHROPIC_API_KEY", "")  # type: ignore[attr-defined]
        if val:
            return str(val).strip()
    except Exception:
        pass
    return ""


def _resolve_model() -> str:
    model = os.environ.get("CASHCONTROL_LLM_MODEL", "").strip()
    if model:
        return model
    try:
        import streamlit as st

        val = st.secrets.get("CASHCONTROL_LLM_MODEL", "")  # type: ignore[attr-defined]
        if val:
            return str(val).strip()
    except Exception:
        pass
    return config.SETTINGS.llm_model

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
    # Conservative production defaults: bounded latency and a couple of retries
    # for transient (429/5xx/network) errors handled by the SDK.
    _TIMEOUT_S = 40.0
    _MAX_RETRIES = 2

    def __init__(self) -> None:
        self.api_key = _resolve_api_key()
        self.model = _resolve_model()
        self._client = None
        if self.api_key:
            try:  # pragma: no cover - exercised only with a real key
                import anthropic

                self._client = anthropic.Anthropic(
                    api_key=self.api_key,
                    timeout=self._TIMEOUT_S,
                    max_retries=self._MAX_RETRIES,
                )
            except Exception:
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> Optional[str]:
        """Return the model's text output, or ``None`` on any failure (no key,
        timeout, API error). Callers must have a deterministic fallback."""
        if self._client is None:
            return None
        try:  # pragma: no cover - requires network/key
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
            return "".join(parts).strip()
        except Exception:
            return None

    # Backwards-compatible internal alias used by the classify/suggest helpers.
    def _complete(self, system: str, user: str, max_tokens: int = 200) -> Optional[str]:
        return self.complete(system, user, max_tokens=max_tokens, temperature=0.0)

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
_DEFAULT_KEY: Optional[str] = None


def get_client() -> LLMClient:
    """Return a cached client, rebuilding it if the resolved key changed (e.g.
    a Streamlit secret was added after first load)."""
    global _DEFAULT, _DEFAULT_KEY
    key = _resolve_api_key()
    if _DEFAULT is None or _DEFAULT_KEY != key:
        _DEFAULT = LLMClient()
        _DEFAULT_KEY = key
    return _DEFAULT
