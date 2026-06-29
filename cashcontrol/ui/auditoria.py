"""Audit page: verify the hash chain and browse the immutable event log."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from ..data import audit


def render(conn) -> None:
    st.header("Auditoría")
    st.caption(
        "Registro encadenado por hash. Cada evento referencia el hash del anterior, "
        "por lo que cualquier alteración de un registro histórico rompe la cadena."
    )

    ok, broken = audit.verify_chain(conn)
    if ok:
        st.success("✅ Cadena de auditoría íntegra y verificada.")
    else:
        st.error(f"❌ Cadena rota a partir del registro #{broken}. Posible manipulación de datos.")

    rows = conn.execute(
        "SELECT id, ts, actor, action, entity, entity_id, payload, hash "
        "FROM audit_log ORDER BY id DESC LIMIT 500"
    ).fetchall()
    if not rows:
        st.info("Sin eventos registrados todavía.")
        return

    records = []
    for r in rows:
        try:
            payload = json.dumps(json.loads(r["payload"]), ensure_ascii=False)
        except Exception:
            payload = r["payload"]
        records.append({
            "#": r["id"], "fecha (UTC)": r["ts"], "actor": r["actor"], "acción": r["action"],
            "entidad": r["entity"], "id": r["entity_id"], "datos": payload,
            "hash": r["hash"][:12] + "…",
        })
    st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
    st.caption(f"Mostrando los últimos {len(records)} eventos.")
