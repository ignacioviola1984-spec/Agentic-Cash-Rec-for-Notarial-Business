"""Shared UI helpers: connection caching, formatting, status styling."""
from __future__ import annotations

import sqlite3

import streamlit as st

import config
from ..data import db
from ..domain.models import ExpedienteStatus
from ..domain.money import format_ars as _format_ars

STATUS_COLORS = {
    ExpedienteStatus.OK.value: "#1a7f37",
    ExpedienteStatus.ATENCION.value: "#9a6700",
    ExpedienteStatus.RIESGO.value: "#bc4c00",
    ExpedienteStatus.BLOQUEADO.value: "#cf222e",
}

STATUS_EMOJI = {
    ExpedienteStatus.OK.value: "🟢",
    ExpedienteStatus.ATENCION.value: "🟡",
    ExpedienteStatus.RIESGO.value: "🟠",
    ExpedienteStatus.BLOQUEADO.value: "🔴",
}

STATUS_LABEL = {
    ExpedienteStatus.OK.value: "OK",
    ExpedienteStatus.ATENCION.value: "Atención",
    ExpedienteStatus.RIESGO.value: "Riesgo",
    ExpedienteStatus.BLOQUEADO.value: "Bloqueado",
}


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = db.connect()
    db.init_db(conn)
    return conn


def money(amount) -> str:
    return _format_ars(amount)


def status_badge(status_value: str) -> str:
    emoji = STATUS_EMOJI.get(status_value, "⚪")
    label = STATUS_LABEL.get(status_value, status_value)
    color = STATUS_COLORS.get(status_value, "#57606a")
    return (
        f"<span style='background:{color};color:white;padding:2px 10px;"
        f"border-radius:12px;font-weight:600;font-size:0.85rem'>{emoji} {label}</span>"
    )


def metric_money(col, label: str, amount, help_text: str = "") -> None:
    col.metric(label, money(amount), help=help_text or None)


_PRIORITY_COLOR = {"alta": "#cf222e", "media": "#bc4c00", "baja": "#1a7f37"}


def render_analysis(container, result) -> None:
    """Render an AnalysisResult (diagnosis, risks, recommendations) with a clear
    provenance note. Numbers always come from the deterministic engine."""
    container.markdown(f"**Diagnóstico**  \n{result.diagnostico}")
    if result.riesgos:
        container.markdown("**Riesgos**")
        for r in result.riesgos:
            container.markdown(f"- {r}")
    container.markdown("**Recomendaciones**")
    for rec in result.recomendaciones:
        color = _PRIORITY_COLOR.get(rec.prioridad, "#57606a")
        badge = (
            f"<span style='background:{color};color:white;padding:1px 8px;"
            f"border-radius:10px;font-size:0.72rem;font-weight:600'>"
            f"{rec.prioridad.upper()}</span>"
        )
        with container.container(border=True):
            container.markdown(f"{badge}&nbsp; {rec.accion}", unsafe_allow_html=True)
            if rec.fundamento:
                container.caption(f"↳ {rec.fundamento}")
    if result.origen == "llm":
        origen = f"🤖 Agente IA · modelo {result.model} · confianza {result.confianza}"
    else:
        origen = "⚙️ Determinista (reglas) · sin llamada a la API"
    container.caption(
        origen + " · Los montos provienen del cálculo determinista; el texto es "
        "interpretación y no modifica ninguna cifra."
    )
