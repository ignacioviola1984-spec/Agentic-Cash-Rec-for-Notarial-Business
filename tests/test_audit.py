from cashcontrol.data import audit


def test_chain_grows_and_verifies(conn):
    audit.record(conn, action="a", entity="x", entity_id=1, payload={"v": 1})
    audit.record(conn, action="b", entity="x", entity_id=2, payload={"v": 2})
    audit.record(conn, action="c", entity="x", entity_id=3, payload={"v": 3})
    ok, broken = audit.verify_chain(conn)
    assert ok is True
    assert broken is None


def test_tamper_is_detected(conn):
    audit.record(conn, action="a", entity="x", entity_id=1, payload={"v": 1})
    audit.record(conn, action="b", entity="x", entity_id=2, payload={"v": 2})
    # Tamper with a historical payload directly in the table.
    conn.execute("UPDATE audit_log SET payload = ? WHERE id = 1", ('{"v":999}',))
    conn.commit()
    ok, broken = audit.verify_chain(conn)
    assert ok is False
    assert broken == 1


def test_first_entry_links_to_empty_prev(conn):
    entry = audit.record(conn, action="genesis", entity="system", payload={})
    assert entry["prev_hash"] == ""
    assert len(entry["hash"]) == 64
