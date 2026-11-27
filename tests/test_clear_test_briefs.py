"""Demo prep: clearing queued test briefs goes through the review_log path, never deletes."""
from __future__ import annotations

import pytest

from hri.api import app as api
from hri.ops import clear_test_briefs as ctb


@pytest.mark.db
def test_clear_rejects_via_review_log_and_keeps_rows():
    from hri import db
    person = db.q1("SELECT id FROM person WHERE NOT is_partner ORDER BY id LIMIT 1")
    bid = api._store({"person_id": person["id"], "mode": "fallback", "brief": {"test": "clear"}, "latency_ms": 1,
                      "cost_usd": 0, "review": {"needed": True, "reasons": ["test"]}})
    try:
        assert ctb.clear([bid], dry_run=True) == {"queued": 1, "cleared": 0, "skipped": 0, "dry_run": True}
        assert ctb.clear([bid]) == {"queued": 1, "cleared": 1, "skipped": 0, "dry_run": False}
        assert db.q1("SELECT status FROM brief WHERE id = %s", (bid,))["status"] == "rejected"   # row kept
        log = db.q("SELECT decision, note, reviewer FROM review_log WHERE brief_id = %s", (bid,))
        assert log == [{"decision": "reject", "note": ctb.NOTE, "reviewer": "system"}]
        assert ctb.clear([bid])["skipped"] == 1                                                # idempotent
    finally:
        db.q("DELETE FROM review_log WHERE brief_id = %s", (bid,))
        db.q("DELETE FROM brief WHERE id = %s", (bid,))
