"""Portfolio dashboard: KPIs, status distribution and the expediente list."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ..domain.models import ExpedienteStatus
from ..services.queries import build_portfolio
from .common import money, status_badge


def render(conn) -> None:
    st.header("Tablero de control de cartera")
    portfolio = build_portfolio(conn)

    if not portfolio.rows:
        st.info(
            "No hay expedientes cargados todavía. Andá a **Carga de datos** para "
            "importar planillas, o cargá los datos de ejemplo desde la barra lateral."
        )
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recibido de clientes", money(portfolio.total_recibido))
    c2.metric("Costo recuperable", money(portfolio.total_costo))
    c3.metric("Financiado por la escribanía", money(portfolio.total_financiado),
              help="Suma de fondos propios adelantados a clientes.")
    c4.metric("Saldo total a cobrar", money(portfolio.total_a_cobrar))

    st.subheader("Distribución por estado")
    sc = portfolio.status_counts
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("🟢 OK", sc.get(ExpedienteStatus.OK.value, 0))
    s2.metric("🟡 Atención", sc.get(ExpedienteStatus.ATENCION.value, 0))
    s3.metric("🟠 Riesgo", sc.get(ExpedienteStatus.RIESGO.value, 0))
    s4.metric("🔴 Bloqueado", sc.get(ExpedienteStatus.BLOQUEADO.value, 0))

    st.subheader("Expedientes")
    st.caption("Ordenados por severidad. Abrí un expediente para ver el detalle financiero.")

    for row in portfolio.rows:
        s = row.summary
        with st.container(border=True):
            top = st.columns([3, 2, 2, 2, 1.4])
            top[0].markdown(
                f"**{row.expediente.codigo}** · {row.expediente.caratula}<br>"
                f"<span style='color:#57606a'>{row.expediente.cliente}</span>",
                unsafe_allow_html=True,
            )
            top[1].markdown(status_badge(row.status.value), unsafe_allow_html=True)
            top[2].markdown(f"Recibido<br>**{money(s.total_recibido)}**", unsafe_allow_html=True)
            flag = money(s.monto_financiado) if s.financiando else money(s.saldo_a_cobrar)
            label = "Financiado" if s.financiando else "A cobrar"
            top[3].markdown(f"{label}<br>**{flag}**", unsafe_allow_html=True)
            if top[4].button("Abrir", key=f"open_{row.expediente.id}"):
                st.session_state["exp_id"] = row.expediente.id
                st.session_state["page"] = "Expediente"
                st.rerun()
            badges = []
            if row.open_reviews:
                badges.append(f"📝 {row.open_reviews} revisión(es)")
            if row.unmatched:
                badges.append(f"🔗 {row.unmatched} sin conciliar")
            if badges:
                st.caption(" · ".join(badges))
