"""Data ingestion page: upload CSV/Excel for the four data types."""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from ..services import ingestion

_TYPES = {
    "Expedientes": ("expedientes", ingestion.import_expedientes,
                    ["codigo", "caratula", "cliente", "escribano", "tipo_acto", "fecha_apertura"]),
    "Anticipos (recibido del cliente)": ("advances", ingestion.import_advances,
                    ["expediente", "fecha", "monto", "metodo", "referencia"]),
    "Gastos recuperables": ("expenses", ingestion.import_expenses,
                    ["expediente", "fecha", "monto", "categoria", "concepto", "estado", "pagado_por", "proveedor"]),
    "Movimientos bancarios": ("bank_movements", ingestion.import_bank_movements,
                    ["fecha", "monto", "credito", "debito", "descripcion", "contraparte", "referencia", "expediente"]),
}


def _template_csv(columns) -> bytes:
    return pd.DataFrame(columns=columns).to_csv(index=False).encode("utf-8-sig")


def _read_upload(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    raw = uploaded.read()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw), dtype=str)
    return pd.read_csv(io.BytesIO(raw), dtype=str, sep=None, engine="python")


def render(conn) -> None:
    st.header("Carga de datos")
    st.caption(
        "Importá planillas reales. Los encabezados se reconocen de forma flexible "
        "(mayúsculas/acentos/sinónimos). Todos los montos se parsean con aritmética "
        "exacta; el LLM nunca genera un monto."
    )

    kind_label = st.selectbox("Tipo de datos a importar", list(_TYPES.keys()))
    canonical, importer, columns = _TYPES[kind_label]

    st.download_button(
        f"Descargar plantilla ({canonical}.csv)",
        data=_template_csv(columns), file_name=f"plantilla_{canonical}.csv", mime="text/csv",
    )

    uploaded = st.file_uploader("Archivo CSV o Excel", type=["csv", "xlsx", "xls"], key=f"up_{canonical}")
    if uploaded is None:
        return

    try:
        df = _read_upload(uploaded)
    except Exception as exc:
        st.error(f"No se pudo leer el archivo: {exc}")
        return

    st.write(f"Vista previa ({len(df)} fila(s)):")
    st.dataframe(df.head(50), use_container_width=True, hide_index=True)

    if st.button("Importar", type="primary"):
        result = importer(conn, df, actor="ui")
        ingestion.finalize(conn, result, actor="ui")
        st.success(f"Importadas {result.inserted} fila(s). Omitidas (duplicadas/existentes): {result.skipped}.")
        if result.errors:
            st.warning(f"{len(result.errors)} fila(s) con error:")
            st.dataframe(pd.DataFrame(result.errors), use_container_width=True, hide_index=True)
        if result.affected_expedientes:
            st.caption(f"Recalculados {len(result.affected_expedientes)} expediente(s).")
