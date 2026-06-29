"""Report generation and export (CSV / Excel).

All figures originate from the deterministic engine. Exports are byte streams so
the Streamlit UI can offer downloads directly.
"""
from __future__ import annotations

import io
import sqlite3
from decimal import Decimal

import pandas as pd

from ..domain.money import format_ars
from .queries import ExpedienteView, Portfolio, build_expediente_view, build_portfolio


def portfolio_dataframe(portfolio: Portfolio) -> pd.DataFrame:
    records = []
    for r in portfolio.rows:
        s = r.summary
        records.append({
            "codigo": r.expediente.codigo,
            "caratula": r.expediente.caratula,
            "cliente": r.expediente.cliente,
            "estado": r.status.value,
            "recibido": float(s.total_recibido),
            "costo_recuperable": float(s.costo_recuperable),
            "gastos_pagados": float(s.gastos_pagados),
            "gastos_pendientes": float(s.gastos_pendientes),
            "posicion_caja": float(s.posicion_caja),
            "financiado": float(s.monto_financiado),
            "saldo_a_cobrar": float(s.saldo_a_cobrar),
            "excedente_a_devolver": float(s.excedente_a_devolver),
            "cobertura": float(s.cobertura),
            "revisiones_abiertas": r.open_reviews,
            "sin_conciliar": r.unmatched,
        })
    return pd.DataFrame.from_records(records)


def expediente_dataframes(view: ExpedienteView) -> dict[str, pd.DataFrame]:
    s = view.summary
    resumen = pd.DataFrame([
        ("Estado", view.status.value),
        ("Recibido del cliente", format_ars(s.total_recibido)),
        ("Costo recuperable total", format_ars(s.costo_recuperable)),
        ("  Desembolsos a terceros", format_ars(s.desembolsos_total)),
        ("  Honorarios", format_ars(s.honorarios_total)),
        ("Gastos pagados", format_ars(s.gastos_pagados)),
        ("Gastos pendientes", format_ars(s.gastos_pendientes)),
        ("Posición de caja", format_ars(s.posicion_caja)),
        ("Fondos disponibles", format_ars(s.fondos_disponibles)),
        ("Monto financiado por la escribanía", format_ars(s.monto_financiado)),
        ("Saldo a cobrar", format_ars(s.saldo_a_cobrar)),
        ("Excedente a devolver", format_ars(s.excedente_a_devolver)),
        ("Cobertura del anticipo", f"{float(s.cobertura) * 100:.0f}%"),
        ("Anticipo suficiente", "Sí" if s.anticipo_suficiente else "No"),
    ], columns=["Concepto", "Valor"])

    advances = pd.DataFrame([{
        "fecha": a.fecha.isoformat(), "monto": float(a.monto),
        "metodo": a.metodo, "referencia": a.referencia,
    } for a in view.advances])

    expenses = pd.DataFrame([{
        "fecha": e.fecha.isoformat(), "monto": float(e.monto), "categoria": e.categoria,
        "concepto": e.concepto, "estado": e.estado.value, "pagado_por": e.pagado_por.value,
        "proveedor": e.proveedor, "referencia": e.referencia,
    } for e in view.expenses])

    movements = pd.DataFrame([{
        "fecha": m.fecha.isoformat(), "monto": float(m.monto), "tipo": m.kind.value,
        "descripcion": m.descripcion, "contraparte": m.contraparte,
        "referencia": m.referencia_banco, "cuenta": m.cuenta,
    } for m in view.movements])

    reviews = pd.DataFrame([{
        "tipo": r.tipo, "severidad": r.severity.value, "estado": r.status.value,
        "mensaje": r.mensaje,
    } for r in view.reviews])

    return {
        "Resumen": resumen,
        "Anticipos": advances if not advances.empty else pd.DataFrame(columns=["fecha", "monto"]),
        "Gastos": expenses if not expenses.empty else pd.DataFrame(columns=["fecha", "monto"]),
        "Movimientos": movements if not movements.empty else pd.DataFrame(columns=["fecha", "monto"]),
        "Revisiones": reviews if not reviews.empty else pd.DataFrame(columns=["tipo", "mensaje"]),
    }


def export_portfolio_csv(conn: sqlite3.Connection) -> bytes:
    df = portfolio_dataframe(build_portfolio(conn))
    return df.to_csv(index=False).encode("utf-8-sig")


def export_portfolio_excel(conn: sqlite3.Connection) -> bytes:
    portfolio = build_portfolio(conn)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        portfolio_dataframe(portfolio).to_excel(writer, sheet_name="Cartera", index=False)
        totals = pd.DataFrame([
            ("Total recibido", format_ars(portfolio.total_recibido)),
            ("Total costo recuperable", format_ars(portfolio.total_costo)),
            ("Total financiado", format_ars(portfolio.total_financiado)),
            ("Total a cobrar", format_ars(portfolio.total_a_cobrar)),
            *[(f"Expedientes {k}", v) for k, v in portfolio.status_counts.items()],
        ], columns=["Concepto", "Valor"])
        totals.to_excel(writer, sheet_name="Totales", index=False)
    return buffer.getvalue()


def export_expediente_excel(conn: sqlite3.Connection, expediente_id: int) -> bytes:
    view = build_expediente_view(conn, expediente_id)
    if view is None:
        raise ValueError(f"Expediente {expediente_id} inexistente")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, df in expediente_dataframes(view).items():
            df.to_excel(writer, sheet_name=sheet[:31], index=False)
    return buffer.getvalue()
