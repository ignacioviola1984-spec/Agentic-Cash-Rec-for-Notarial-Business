"""Global review queue: every action requiring human attention, severity-first."""
from __future__ import annotations

import streamlit as st

from ..data import repository as repo
from ..services import review_service


def render(conn) -> None:
    st.header("Cola de revisión")
    st.caption("Acciones que requieren decisión humana, ordenadas por severidad.")

    items = repo.list_review_items(conn, only_open=True)
    if not items:
        st.success("No hay revisiones abiertas en toda la cartera.")
        return

    exp_codes = {e.id: e.codigo for e in repo.list_expedientes(conn)}
    for r in items:
        icon = {"blocking": "🔴 Bloqueante", "warning": "🟠 Atención", "info": "🔵 Info"}.get(
            r.severity.value, r.severity.value)
        with st.container(border=True):
            cols = st.columns([5, 1.4, 1.4])
            code = exp_codes.get(r.expediente_id, "—")
            cols[0].markdown(f"**{icon}** · {code} · `{r.tipo}`  \n{r.mensaje}")
            note_key = f"note_{r.id}"
            if cols[1].button("Resolver", key=f"res_{r.id}"):
                review_service.resolve(conn, r.id, actor="ui", note=st.session_state.get(note_key, ""))
                st.rerun()
            if cols[2].button("Descartar", key=f"dis_{r.id}"):
                review_service.dismiss(conn, r.id, actor="ui", note=st.session_state.get(note_key, ""))
                st.rerun()
