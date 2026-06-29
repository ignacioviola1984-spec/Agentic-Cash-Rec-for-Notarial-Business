"""Reconciliation page: assign unassigned movements and confirm match suggestions."""
from __future__ import annotations

import streamlit as st

from ..data import repository as repo
from ..domain.models import MatchStatus, MatchTargetType
from ..services import matching_service
from .common import money


def render(conn) -> None:
    st.header("Conciliación")

    # --- Unassigned movements -------------------------------------------
    st.subheader("Movimientos sin asignar a expediente")
    st.caption(
        "El sistema sugiere un expediente (por código o nombre del cliente). "
        "La asignación es siempre una acción humana."
    )
    expedientes = repo.list_expedientes(conn)
    code_to_id = {e.codigo: e.id for e in expedientes}
    options = ["— sin asignar —"] + [f"{e.codigo} · {e.cliente}" for e in expedientes]

    suggestions = matching_service.suggest_assignments(conn)
    if not suggestions:
        st.success("No hay movimientos sin asignar.")
    for item in suggestions:
        mv = item["movement"]
        sug_code = item["suggested_codigo"]
        cols = st.columns([3, 3, 2])
        cols[0].write(f"**{money(mv.monto)}** {mv.kind.value} · {mv.fecha}  \n{mv.descripcion} · {mv.contraparte}")
        default_idx = 0
        if sug_code:
            label = next((o for o in options if o.startswith(sug_code + " ")), None)
            if label:
                default_idx = options.index(label)
            cols[1].caption(f"Sugerido: **{sug_code}** ({item['origen']})")
        choice = cols[1].selectbox("Asignar a", options, index=default_idx, key=f"asg_{mv.id}",
                                   label_visibility="collapsed")
        if cols[2].button("Asignar", key=f"asgbtn_{mv.id}"):
            target_id = None
            if choice != options[0]:
                code = choice.split(" · ")[0]
                target_id = code_to_id.get(code)
            origen = "manual"
            if sug_code and choice.startswith(sug_code + " "):
                origen = item["origen"]
            matching_service.assign_movement_to_expediente(
                conn, mv.id, target_id, origen=origen, actor="ui")
            st.rerun()

    st.divider()

    # --- Pending match suggestions across the portfolio -----------------
    st.subheader("Sugerencias de conciliación pendientes")
    if st.button("Regenerar sugerencias para toda la cartera"):
        n = matching_service.generate_all_suggestions(conn, actor="ui")
        st.success(f"{n} sugerencia(s) actualizada(s).")
        st.rerun()

    any_pending = False
    for e in expedientes:
        matches = [m for m in repo.list_matches_for_expediente(conn, e.id)
                   if m.status == MatchStatus.SUGGESTED]
        if not matches:
            continue
        any_pending = True
        st.markdown(f"**{e.codigo}** · {e.caratula}")
        advances = {a.id: a for a in repo.list_advances(conn, e.id)}
        expenses = {x.id: x for x in repo.list_expenses(conn, e.id)}
        for m in matches:
            mv = repo.get_movement(conn, m.movement_id)
            if m.target_type == MatchTargetType.ADVANCE:
                tgt = advances.get(m.target_id)
                tgt_label = f"Anticipo {money(tgt.monto)}" if tgt else "Anticipo"
            else:
                tgt = expenses.get(m.target_id)
                tgt_label = f"Gasto {money(tgt.monto)} {tgt.categoria}" if tgt else "Gasto"
            cols = st.columns([4, 3, 1, 2])
            cols[0].write(f"Mov {money(mv.monto)} {mv.kind.value} · {mv.fecha}")
            cols[1].write(tgt_label)
            cols[2].write(f"{m.score}")
            b = cols[3].columns(2)
            if b[0].button("✓", key=f"gc_{m.id}", help="Confirmar"):
                matching_service.confirm_match(conn, m.id, actor="ui")
                st.rerun()
            if b[1].button("✗", key=f"gr_{m.id}", help="Rechazar"):
                matching_service.reject_match(conn, m.id, actor="ui")
                st.rerun()
    if not any_pending:
        st.caption("No hay sugerencias pendientes de confirmación.")
