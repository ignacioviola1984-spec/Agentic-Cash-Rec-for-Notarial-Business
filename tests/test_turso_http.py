"""Exercise the Turso HTTP proxy end-to-end with a fake transport.

A fake ``urlopen`` parses the Hrana pipeline JSON, runs each statement against a
single in-memory SQLite connection, and returns Turso-shaped responses. This
validates the proxy (arg/row typing, lastrowid, executescript) against the real
repository code — without a network or the compiled driver.
"""
import io
import json
import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from cashcontrol.data import db, repository as repo
from cashcontrol.data import audit
from cashcontrol.domain.models import Advance, Expediente


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def turso(monkeypatch):
    backend = sqlite3.connect(":memory:")  # stands in for the Turso server
    backend.execute("PRAGMA foreign_keys = ON")

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        results = []
        for r in payload.get("requests", []):
            if r.get("type") != "execute":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = r["stmt"]
            args = [db._arg_to_py(a) for a in stmt.get("args", [])]
            cur = backend.execute(stmt["sql"], args)
            cols = [{"name": d[0]} for d in (cur.description or [])]
            rows = [[db._py_to_arg(v) for v in row] for row in cur.fetchall()]
            backend.commit()
            results.append({
                "type": "ok",
                "response": {"type": "execute", "result": {
                    "cols": cols, "rows": rows,
                    "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None,
                    "affected_row_count": cur.rowcount,
                }},
            })
        return _FakeResp(json.dumps({"results": results}).encode("utf-8"))

    monkeypatch.setattr(db.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://demo-org.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "faketoken")
    conn = db.connect()
    db.init_db(conn)
    yield conn
    backend.close()


def test_backend_is_turso_over_http(turso):
    assert db.active_backend() == "Turso (libSQL)"
    assert isinstance(turso, db._TursoConn)


def test_http_base_mapping():
    assert db._turso_http_base("libsql://x-y.turso.io") == "https://x-y.turso.io"
    assert db._turso_http_base("https://x-y.turso.io") == "https://x-y.turso.io"


def test_crud_roundtrip_over_turso(turso):
    eid = repo.create_expediente(turso, Expediente(
        codigo="EXP-T1", caratula="Compraventa Test", cliente="Cliente Turso"))
    assert isinstance(eid, int) and eid > 0

    got = repo.get_expediente_by_codigo(turso, "EXP-T1")
    assert got is not None
    assert got.codigo == "EXP-T1"
    assert got.cliente == "Cliente Turso"

    # Money (integer centavos) must round-trip exactly through the JSON typing.
    repo.add_advance(turso, Advance(eid, date(2024, 3, 4), Decimal("500000.00"),
                                    "transferencia", "TRF-1"))
    advances = repo.list_advances(turso, eid)
    assert len(advances) == 1
    assert advances[0].monto == Decimal("500000.00")


def test_audit_chain_over_turso(turso):
    audit.record(turso, action="a", entity="x", entity_id=1, payload={"v": 1})
    audit.record(turso, action="b", entity="x", entity_id=2, payload={"v": 2})
    ok, broken = audit.verify_chain(turso)
    assert ok is True and broken is None
