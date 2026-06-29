"""Reports page: portfolio table and downloadable exports."""
from __future__ import annotations

import streamlit as st

from ..services import reports
from ..services.queries import build_portfolio
from .common import money


def render(conn) -> None:
    st.header("Reportes")
    portfolio = build_portfolio(conn)
    if not portfolio.rows:
        st.info("No hay datos para reportar.")
        return

    df = reports.portfolio_dataframe(portfolio)
    st.subheader("Cartera completa")
    st.dataframe(df, use_container_width=True, hide_index=True)

    t = st.columns(4)
    t[0].metric("Total recibido", money(portfolio.total_recibido))
    t[1].metric("Total costo recuperable", money(portfolio.total_costo))
    t[2].metric("Total financiado", money(portfolio.total_financiado))
    t[3].metric("Total a cobrar", money(portfolio.total_a_cobrar))

    st.subheader("Exportar")
    c = st.columns(2)
    c[0].download_button(
        "Cartera (CSV)", data=reports.export_portfolio_csv(conn),
        file_name="cartera_cashcontrol.csv", mime="text/csv")
    c[1].download_button(
        "Cartera (Excel)", data=reports.export_portfolio_excel(conn),
        file_name="cartera_cashcontrol.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
