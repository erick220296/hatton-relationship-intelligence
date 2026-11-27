"""F10: the API passes the load-test tag into the trace and stores the trace_id with the brief."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hri.api import app as api

client = TestClient(api.app)


@pytest.fixture
def fake_run(monkeypatch):
    seen = {}

    async def run(request, run_id, checkpointer=None, tags=None):
        seen["tags"] = tags
        return {"mode": seen.get("mode", "not_found"), "brief": seen.get("brief"), "warnings": [], "cost_usd": 0,
                "latency_ms": 1, "timings": {}, "person_id": seen.get("person_id"), "partner_email": None,
                "meeting_id": None, "review": {"needed": False, "reasons": []}, "trace_id": "trace-xyz"}
    monkeypatch.setattr(api, "run_brief", run)
    return seen


def test_load_test_header_becomes_a_trace_tag(fake_run):
    r = client.post("/brief", json={"query": "nobody"}, headers={"X-HRI-Run-Type": "load-test"})
    assert r.json()["trace_id"] == "trace-xyz" and fake_run["tags"] == ["load-test"]
    client.post("/brief", json={"query": "nobody"})
    assert fake_run["tags"] == []


@pytest.mark.db
def test_trace_id_is_stored_with_the_brief(fake_run):
    from hri import db
    fake_run.update(mode="fallback", brief={"tag": "F10"},
                    person_id=db.q1("SELECT id FROM person WHERE NOT is_partner LIMIT 1")["id"])
    body = client.post("/brief", json={"query": "x"}).json()
    try:
        assert db.q1("SELECT trace_id FROM brief WHERE id = %s", (body["brief_id"],))["trace_id"] == "trace-xyz"
    finally:
        db.q("DELETE FROM brief WHERE id = %s", (body["brief_id"],))
