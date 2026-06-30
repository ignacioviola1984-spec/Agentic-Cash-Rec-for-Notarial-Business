"""Cash Control por Expediente — Streamlit application entrypoint.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

import config
from cashcontrol import __version__
from cashcontrol.data import db
from cashcontrol.services import seed, status_service, matching_service
from cashcontrol.services.bootstrap import is_empty
from cashcontrol.ui import (
    auditoria,
    common,
    dashboard,
    expediente,
    ingest,
    reconcile,
    reportes,
    review,
)

st.set_page_config(page_title="Cash Control por Expediente", page_icon="💼", layout="wide")


def _bridge_secrets() -> None:
    """Expose Streamlit Cloud secrets as environment variables so the existing
    config/LLM layer (which reads os.environ) picks them up. Secrets are never
    logged or committed; they live only in the Streamlit Cloud secrets store."""
    for key in (
        "ANTHROPIC_API_KEY",
        "CASHCONTROL_LLM_MODEL",
        "CASHCONTROL_AUTOSEED",
        "TURSO_DATABASE_URL",
        "TURSO_AUTH_TOKEN",
    ):
        if os.environ.get(key):
            continue
        try:
            val = st.secrets.get(key)  # type: ignore[attr-defined]
        except Exception:
            val = None
        if val:
            os.environ[key] = str(val)


_bridge_secrets()

PAGES = {
    "Tablero": dashboard.render,
    "Expediente": expediente.render,
    "Carga de datos": ingest.render,
    "Conciliación": reconcile.render,
    "Revisión": review.render,
    "Reportes": reportes.render,
    "Auditoría": auditoria.render,
}


def _sidebar(conn) -> str:
    st.sidebar.title("💼 Cash Control")
    st.sidebar.caption("Control de caja por expediente · Escribanía")

    if "page" not in st.session_state:
        st.session_state["page"] = "Tablero"
    names = list(PAGES.keys())
    index = names.index(st.session_state["page"]) if st.session_state["page"] in names else 0
    choice = st.sidebar.radio("Navegación", names, index=index)
    st.session_state["page"] = choice

    st.sidebar.divider()
    st.sidebar.subheader("Acciones")

    if is_empty(conn):
        if st.sidebar.button("Cargar datos de ejemplo", use_container_width=True):
            seed.seed(conn)
            st.sidebar.success("Datos de ejemplo cargados.")
            st.rerun()
    else:
        if st.sidebar.button("Recalcular toda la cartera", use_container_width=True):
            matching_service.generate_all_suggestions(conn, actor="ui")
            n = status_service.recompute_all(conn, actor="ui")
            st.sidebar.success(f"{n} expediente(s) recalculado(s).")
            st.rerun()
        with st.sidebar.expander("Zona de datos"):
            st.caption("Reinicia la base y vuelve a cargar el ejemplo.")
            if st.button("Reiniciar con datos de ejemplo", use_container_width=True):
                db.reset_db(conn)
                seed.seed(conn)
                st.rerun()

    st.sidebar.divider()
    from cashcontrol.llm.client import get_client
    llm_on = get_client().enabled
    st.sidebar.caption(
        ("🤖 Agente IA activo (análisis/recomendaciones/clasificación)." if llm_on
         else "⚙️ Modo determinista (sin API). Reglas para analizar/clasificar/sugerir.")
        + "\n\nLos montos, balances, estados y reportes son siempre deterministas."
    )
    st.sidebar.caption(f"v{__version__} · moneda {config.SETTINGS.currency}")
    return choice


def _maybe_autoseed(conn) -> None:
    """Seed the sample dataset on first load when the DB is empty.

    Enabled by default so a demo deployment shows data on first load.
    Set CASHCONTROL_AUTOSEED=0 to disable for a real, start-empty install.
    """
    flag = os.environ.get("CASHCONTROL_AUTOSEED", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return
    if is_empty(conn):
        try:
            seed.seed(conn)
        except Exception:
            # A concurrent first load may have seeded already; ignore.
            pass


def main() -> None:
    conn = common.get_conn()
    # Idempotent schema sync on every run (not only when the cached connection is
    # first created). This self-heals schema drift after a code update on a warm
    # Streamlit process, where get_conn() returns a connection built by older code
    # and new CREATE TABLE IF NOT EXISTS statements would otherwise never run.
    db.init_db(conn)
    _maybe_autoseed(conn)
    page = _sidebar(conn)
    PAGES[page](conn)


main()
