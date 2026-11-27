"""F07: human review queue. Flagged briefs are listed; a partner's decision is logged in our own DB only."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hri.api import app as api

client = TestClient(api.app)   # no `with`: lifespan (checkpointer, masker warm-up) is not needed here


def test_decision_must_be_approve_or_reject():
    r = client.post("/review/1", json={"decision": "send", "note": "x"})
    assert r.status_code == 422


@pytest.fixture
def flagged_brief():
    from hri import db
    person = db.q1("SELECT id FROM person WHERE NOT is_partner ORDER BY id LIMIT 1")
    res = {"person_id": person["id"], "partner_email": None, "meeting_id": None, "mode": "fallback",
           "brief": {"test": "F07"}, "latency_ms": 1, "cost_usd": 0, "trace_id": None,
           "review": {"needed": True, "reasons": ["Facts-only brief (model unavailable or out of time)"]}}
    bid = api._store(res)
    yield bid
    db.q("DELETE FROM review_log WHERE brief_id = %s", (bid,))   # remove only what this test created
    db.q("DELETE FROM brief WHERE id = %s", (bid,))


@pytest.mark.db
def test_flagged_brief_is_queued_then_approved_and_logged(flagged_brief):
    from hri import db
    queue = client.get("/review").json()
    item = next(q for q in queue if q["brief_id"] == flagged_brief)
    assert "model unavailable" in item["reasons"][0]
    r = client.post(f"/review/{flagged_brief}", json={"decision": "approve", "note": "checked"})
    assert r.status_code == 200 and r.json()["status"] == "reviewed"
    assert flagged_brief not in [q["brief_id"] for q in client.get("/review").json()]
    log = db.q("SELECT decision, note, at FROM review_log WHERE brief_id = %s", (flagged_brief,))
    assert len(log) == 1 and log[0]["decision"] == "approve" and log[0]["note"] == "checked" and log[0]["at"]
    assert client.post(f"/review/{flagged_brief}", json={"decision": "reject"}).status_code == 409


@pytest.mark.db
def test_unflagged_brief_is_not_queued():
    from hri import db
    person = db.q1("SELECT id FROM person WHERE NOT is_partner ORDER BY id LIMIT 1")
    bid = api._store({"person_id": person["id"], "mode": "llm", "brief": {"test": "F07"}, "latency_ms": 1,
                      "cost_usd": 0, "review": {"needed": False, "reasons": []}})
    try:
        assert db.q1("SELECT status FROM brief WHERE id = %s", (bid,))["status"] == "ok"
        assert bid not in [q["brief_id"] for q in client.get("/review").json()]
        assert client.post("/review/999999999", json={"decision": "approve"}).status_code == 404
    finally:
        db.q("DELETE FROM brief WHERE id = %s", (bid,))
