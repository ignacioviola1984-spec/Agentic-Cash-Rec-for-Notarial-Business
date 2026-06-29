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
