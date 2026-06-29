"""Expediente detail: the screen that answers every required question for a
single file, with deterministic numbers and HITL actions."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ..data import repository as repo
from ..domain.models import ExpenseStatus, MatchStatus, MatchTargetType, ReviewStatus
from ..llm.client import get_client
from ..services import matching_service, review_service, reports, status_service
from ..services.queries import build_expediente_view
from ..services.serialization import summary_facts_text
from .common import money, status_badge


def _selector(conn):
    expedientes = repo.list_expedientes(conn)
    if not expedientes:
        st.info("No hay expedientes. Cargá datos primero.")
        return None
    options = {f"{e.codigo} · {e.caratula}": e.id for e in expedientes}
    current_id = st.session_state.get("exp_id", expedientes[0].id)
    labels = list(options.keys())
    ids = list(options.values())
    index = ids.index(current_id) if current_id in ids else 0
    chosen = st.selectbox("Expediente", labels, index=index)
    exp_id = options[chosen]
    st.session_state["exp_id"] = exp_id
    return exp_id


def render(conn) -> None:
    st.header("Detalle de expediente")
    exp_id = _selector(conn)
    if exp_id is None:
        return

    view = build_expediente_view(conn, exp_id)
    if view is None:
        st.error("Expediente no encontrado.")
        return

    e, s = view.expediente, view.summary
    head = st.columns([4, 2])
    head[0].markdown(
        f"### {e.codigo} — {e.caratula}\n"
        f"**Cliente:** {e.cliente}  ·  **Escribano:** {e.escribano or '—'}  ·  "
        f"**Acto:** {e.tipo_acto or '—'}"
    )
    head[1].markdown(status_badge(view.status.value), unsafe_allow_html=True)
    if view.reasons:
        head[1].caption(" ".join(view.reasons))

    # --- Preguntas clave (deterministic answers) -------------------------
    st.subheader("Preguntas clave")
    q = st.columns(2)
    q[0].markdown(f"""
- **¿Cuánto se recibió del cliente?** {money(s.total_recibido)}
- **¿Gastos pagados por la escribanía?** {money(s.gastos_pagados)}
- **¿Gastos pendientes?** {money(s.gastos_pendientes)}
- **¿El anticipo alcanza?** {"Sí" if s.anticipo_suficiente else "No"} (cobertura {float(s.cobertura) * 100:.0f}%)
""")
    q[1].markdown(f"""
- **¿La escribanía financia al cliente?** {"Sí, " + money(s.monto_financiado) if s.financiando else "No"}
- **¿Saldo a cobrar?** {money(s.saldo_a_cobrar)}
- **¿Excedente a devolver?** {money(s.excedente_a_devolver)}
- **¿Movimientos sin conciliar?** {len(view.unmatched_movements)}
""")

    # --- Metrics ---------------------------------------------------------
    m = st.columns(4)
    m[0].metric("Recibido", money(s.total_recibido))
    m[1].metric("Costo recuperable", money(s.costo_recuperable))
    m[2].metric("Posición de caja", money(s.posicion_caja),
                help="Recibido menos gastos efectivamente pagados por la escribanía.")
    m[3].metric("Honorarios", money(s.honorarios_total))

    # --- Narrative (guarded LLM / deterministic) -------------------------
    with st.expander("Resumen narrativo (números deterministas)"):
        facts = summary_facts_text(s, view.reasons)
        text, origen = get_client().narrative(facts)
        st.write(text)
        st.caption(f"Origen del texto: {origen} · Los montos provienen del cálculo determinista.")

    tabs = st.tabs(["Anticipos", "Gastos", "Movimientos", "Conciliación", "Revisiones", "Exportar"])

    # Anticipos
    with tabs[0]:
        if view.advances:
            st.dataframe(pd.DataFrame([{
                "Fecha": a.fecha.isoformat(), "Monto": money(a.monto),
                "Método": a.metodo, "Referencia": a.referencia,
            } for a in view.advances]), use_container_width=True, hide_index=True)
        else:
            st.caption("Sin anticipos registrados.")

    # Gastos with paid/pending toggle
    with tabs[1]:
        if not view.expenses:
            st.caption("Sin gastos registrados.")
        for x in view.expenses:
            cols = st.columns([2, 2, 3, 2, 2])
            cols[0].write(x.fecha.isoformat())
            cols[1].write(money(x.monto))
            origen_tag = "" if x.categoria_origen == "manual" else f" · _{x.categoria_origen}_"
            cols[2].write(f"{x.categoria}{origen_tag}  \n{x.concepto}")
            estado = "✅ Pagado" if x.estado == ExpenseStatus.PAID else "⏳ Pendiente"
            cols[3].write(f"{estado}  \n{x.pagado_por.value}")
            if x.estado == ExpenseStatus.PENDING:
                if cols[4].button("Marcar pagado", key=f"pay_{x.id}"):
                    repo.set_expense_estado(conn, x.id, ExpenseStatus.PAID)
                    status_service.recompute(conn, exp_id, actor="ui")
                    st.rerun()
            else:
                if cols[4].button("Marcar pendiente", key=f"unpay_{x.id}"):
                    repo.set_expense_estado(conn, x.id, ExpenseStatus.PENDING)
                    status_service.recompute(conn, exp_id, actor="ui")
                    st.rerun()

    # Movimientos
    with tabs[2]:
        if view.movements:
            st.dataframe(pd.DataFrame([{
                "Fecha": mv.fecha.isoformat(), "Tipo": mv.kind.value, "Monto": money(mv.monto),
                "Descripción": mv.descripcion, "Contraparte": mv.contraparte,
                "Ref": mv.referencia_banco,
                "Conciliado": "no" if mv in view.unmatched_movements else "sí",
            } for mv in view.movements]), use_container_width=True, hide_index=True)
        else:
            st.caption("Sin movimientos asignados a este expediente.")

    # Conciliación (matches)
    with tabs[3]:
        if st.button("Generar sugerencias de conciliación", key="gen_sug"):
            n = matching_service.generate_suggestions(conn, exp_id, actor="ui")
            st.success(f"{n} sugerencia(s) actualizada(s).")
            st.rerun()
        _render_matches(conn, view, exp_id)

    # Revisiones
    with tabs[4]:
        _render_reviews(conn, view, exp_id)

    # Exportar
    with tabs[5]:
        data = reports.export_expediente_excel(conn, exp_id)
        st.download_button(
            "Descargar reporte del expediente (Excel)", data=data,
            file_name=f"{e.codigo}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def _target_label(conn, m) -> str:
    if m.target_type == MatchTargetType.ADVANCE:
        a = next((x for x in repo.list_advances(conn, st.session_state["exp_id"]) if x.id == m.target_id), None)
        return f"Anticipo {money(a.monto)} ({a.fecha})" if a else f"Anticipo #{m.target_id}"
    x = next((x for x in repo.list_expenses(conn, st.session_state["exp_id"]) if x.id == m.target_id), None)
    return f"Gasto {money(x.monto)} {x.categoria}" if x else f"Gasto #{m.target_id}"


def _render_matches(conn, view, exp_id) -> None:
    if not view.matches:
        st.caption("Sin sugerencias ni conciliaciones. Generá sugerencias arriba.")
        return
    mv_by_id = {mv.id: mv for mv in view.movements}
    for m in view.matches:
        mv = mv_by_id.get(m.movement_id)
        if not mv:
            continue
        cols = st.columns([3, 3, 1.5, 2])
        cols[0].write(f"Mov **{money(mv.monto)}** {mv.kind.value} · {mv.fecha} · {mv.descripcion}")
        cols[1].write(_target_label(conn, m))
        cols[2].write(f"score {m.score}")
        if m.status == MatchStatus.SUGGESTED:
            b = cols[3].columns(2)
            if b[0].button("Confirmar", key=f"cm_{m.id}"):
                matching_service.confirm_match(conn, m.id, actor="ui")
                st.rerun()
            if b[1].button("Rechazar", key=f"rm_{m.id}"):
                matching_service.reject_match(conn, m.id, actor="ui")
                st.rerun()
        else:
            cols[3].write({"confirmed": "✅ confirmado", "rejected": "❌ rechazado"}.get(m.status.value, m.status.value))
        if m.rationale:
            st.caption(f"↳ {m.rationale}")


def _render_reviews(conn, view, exp_id) -> None:
    open_items = [r for r in view.reviews if r.status == ReviewStatus.OPEN]
    if not open_items:
        st.success("Sin revisiones abiertas para este expediente.")
    for r in open_items:
        icon = {"blocking": "🔴", "warning": "🟠", "info": "🔵"}.get(r.severity.value, "•")
        cols = st.columns([5, 1.5, 1.5])
        cols[0].write(f"{icon} **{r.tipo}** — {r.mensaje}")
        if cols[1].button("Resolver", key=f"rvr_{r.id}"):
            review_service.resolve(conn, r.id, actor="ui")
            st.rerun()
        if cols[2].button("Descartar", key=f"rvd_{r.id}"):
            review_service.dismiss(conn, r.id, actor="ui")
            st.rerun()
    resolved = [r for r in view.reviews if r.status != ReviewStatus.OPEN]
    if resolved:
        with st.expander(f"Historial de revisiones ({len(resolved)})"):
            st.dataframe(pd.DataFrame([{
                "tipo": r.tipo, "severidad": r.severity.value,
                "estado": r.status.value, "mensaje": r.mensaje,
            } for r in resolved]), use_container_width=True, hide_index=True)
