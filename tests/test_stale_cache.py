"""F09: when the data tools are down, the last stored brief of any mode is served, clearly labelled."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hri.api import app as api

client = TestClient(api.app)


@pytest.fixture
def tools_down(monkeypatch):
    async def unavailable(request, run_id, checkpointer=None, tags=None):
        return {"mode": "unavailable", "brief": None, "warnings": ["Relationship data service unavailable: test"],
                "cost_usd": 0, "latency_ms": 1, "timings": {}, "person_id": None, "partner_email": None,
                "meeting_id": None, "review": None}
    monkeypatch.setattr(api, "run_brief", unavailable)


@pytest.fixture
def stored():
    from hri import db
    person = db.q1("SELECT id FROM person WHERE NOT is_partner ORDER BY id DESC LIMIT 1")
    ids = []

    def store(mode, tag):
        ids.append(api._store({"person_id": person["id"], "mode": mode, "brief": {"tag": tag}, "latency_ms": 1,
                               "cost_usd": 0, "review": {"needed": mode == "fallback", "reasons": []}}))
        return ids[-1]
    yield person["id"], store
    db.q("DELETE FROM brief WHERE id = ANY(%s)", (ids,))   # only rows this test created


@pytest.mark.db
def test_tools_down_serves_last_fallback_brief_labelled(tools_down, stored):
    pid, store = stored
    bid = store("fallback", "fb")
    r = client.post("/brief", json={"person_id": pid})
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "stale_cache" and body["brief"] == {"tag": "fb"}
    assert body["brief_id"] == bid and body["stale_mode"] == "fallback"
    assert any(w.startswith("Showing the last good brief from") and "facts-only" in w for w in body["warnings"])


@pytest.mark.db
def test_stale_cache_prefers_a_verified_brief(tools_down, stored):
    pid, store = stored
    store("llm", "verified-older")
    store("fallback", "fb-newer")
    body = client.post("/brief", json={"person_id": pid}).json()
    assert body["brief"] == {"tag": "verified-older"} and "verified brief" in " ".join(body["warnings"])


@pytest.mark.db
def test_normal_cache_still_serves_only_verified_briefs(stored):
    from hri.api.app import _cached
    pid, store = stored
    store("fallback", "fb")
    assert _cached(person_id=pid) is None or _cached(person_id=pid)["mode"] == "llm"
    assert _cached(person_id=pid, any_mode=True)["content"] == {"tag": "fb"}


@pytest.mark.db
def test_stale_brief_without_review_field_is_flagged(tools_down, stored):
    """B2: a cached brief from before F05 has no `review`; serve it flagged, never as if it were checked."""
    pid, store = stored
    store("llm", "pre-F05")                      # content has no "review" key
    body = client.post("/brief", json={"person_id": pid}).json()
    assert body["mode"] == "stale_cache"
    assert body["review"] == {"needed": True, "reasons": ["brief predates fact-check fix"]}


@pytest.mark.db
def test_stale_brief_keeps_its_own_review_flag(tools_down, stored):
    pid, store = stored
    from hri import db
    bid = store("fallback", "has-review")
    db.q("UPDATE brief SET content = content || %s::jsonb WHERE id = %s",
         ('{"review": {"needed": false, "reasons": []}}', bid))
    assert client.post("/brief", json={"person_id": pid}).json()["review"] == {"needed": False, "reasons": []}
