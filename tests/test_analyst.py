from datetime import date
from decimal import Decimal

from cashcontrol.domain.engine import compute_summary
from cashcontrol.domain.models import Advance, Expense, ExpenseStatus, ExpedienteStatus
from cashcontrol.llm import analyst
from cashcontrol.llm.analyst import (
    AnalysisResult,
    Recommendation,
    _coerce_result,
    _extract_json,
    analyze_expediente,
    fallback_expediente,
)


def _summary_financing():
    return compute_summary(
        1,
        [Advance(1, date(2024, 1, 1), Decimal("100000"))],
        [Expense(1, date(2024, 1, 2), Decimal("180000"), "sellos", estado=ExpenseStatus.PAID)],
    )


FACTS = "Recibido 100000. Costo recuperable 180000. Financiado 80000. Posicion -80000."


class _FakeClient:
    enabled = True
    model = "test-model"

    def __init__(self, out):
        self._out = out

    def complete(self, *a, **k):
        return self._out


def test_extract_json_handles_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('texto {"a": 2} cola') == {"a": 2}
    assert _extract_json("no json") is None


def test_fallback_recommends_financing_recovery():
    s = _summary_financing()
    res = fallback_expediente(s, ExpedienteStatus.RIESGO, ["financiando"], set(), 0)
    assert res.origen == "fallback"
    assert res.recomendaciones
    joined = " ".join(r.accion for r in res.recomendaciones)
    assert "reposición" in joined.lower()
    # Fallback only cites deterministic amounts (80.000 present in the summary).
    assert "80.000" in joined


def test_coerce_rejects_invented_number():
    data = {
        "diagnostico": "Cobrar 999999 ya.",  # 999999 absent from FACTS -> rejected
        "riesgos": [],
        "recomendaciones": [{"accion": "x", "prioridad": "alta", "fundamento": ""}],
        "confianza": "alta",
    }
    assert _coerce_result("expediente", data, FACTS, "m") is None


def test_coerce_accepts_grounded_numbers():
    data = {
        "diagnostico": "La escribanía financia 80000.",
        "riesgos": ["financiamiento"],
        "recomendaciones": [
            {"accion": "Solicitar reposición por 80000.", "prioridad": "alta", "fundamento": "posición -80000"}
        ],
        "confianza": "alta",
    }
    res = _coerce_result("expediente", data, FACTS, "m")
    assert res is not None
    assert res.origen == "llm"
    assert res.grounded is True


def test_analyze_uses_llm_when_grounded(monkeypatch):
    out = (
        '{"diagnostico": "Financia 80000.", "riesgos": [], '
        '"recomendaciones": [{"accion": "Reponer 80000.", "prioridad": "alta", "fundamento": ""}], '
        '"confianza": "alta"}'
    )
    monkeypatch.setattr(analyst, "get_client", lambda: _FakeClient(out))
    res = analyze_expediente(FACTS, _summary_financing(), ExpedienteStatus.RIESGO, [], set(), 0)
    assert res.origen == "llm"


def test_analyze_falls_back_when_ungrounded(monkeypatch):
    out = (
        '{"diagnostico": "Cobrar 555555.", '
        '"recomendaciones": [{"accion": "x", "prioridad": "alta", "fundamento": ""}], '
        '"confianza": "alta"}'
    )
    monkeypatch.setattr(analyst, "get_client", lambda: _FakeClient(out))
    res = analyze_expediente(FACTS, _summary_financing(), ExpedienteStatus.RIESGO, ["r"], set(), 0)
    assert res.origen == "fallback"  # invented number -> deterministic fallback


def test_analysis_result_roundtrip():
    res = AnalysisResult(
        scope="expediente", diagnostico="d", riesgos=["a"],
        recomendaciones=[Recommendation("acc", "alta", "f")], confianza="media",
        origen="llm", model="m", grounded=True,
    )
    back = AnalysisResult.from_json(res.to_json())
    assert back.recomendaciones[0].accion == "acc"
    assert back.scope == "expediente"


def test_service_caches_and_audits(conn):
    from cashcontrol.data import audit, repository as repo
    from cashcontrol.services import analysis_service, seed

    seed.seed(conn)
    e = repo.get_expediente_by_codigo(conn, "EXP-2024-003")
    first = analysis_service.get_or_generate_expediente_analysis(conn, e.id, actor="test")
    assert first is not None and first.recomendaciones
    # Cached lookup (no regeneration) returns an equivalent result.
    cached = analysis_service.peek_expediente_analysis(conn, e.id)
    assert cached is not None
    assert cached.diagnostico == first.diagnostico
    ok, _ = audit.verify_chain(conn)
    assert ok is True
