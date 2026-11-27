"""F08: the UI shows the review badge and the 'Needs partner review' tab, against a fake API (no stack needed)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "src/hri/ui/streamlit_app.py")
REASON = "Facts-only brief (model unavailable or out of time): no model-written content."
BRIEF = {"mode": "fallback", "brief_id": 58, "warnings": [], "latency_ms": 100, "cost_usd": 0,
         "review": {"needed": True, "reasons": [REASON]},
         "brief": {"person": {"name": "Jamie Tucker", "title": "Head of M&A", "org": "Wright & Co"},
                   "relationship": {"status": "warm"}, "sections": {}, "dropped_claims": []}}


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@pytest.fixture
def fake_api(monkeypatch):
    state = {"queue": [{"brief_id": 58, "person": "Jamie Tucker", "mode": "fallback", "created_at": "2026-11-27T15:46",
                        "meeting_id": None, "reasons": [REASON]}], "posts": []}

    def request(method, url, **kw):
        path = url.split("8000", 1)[-1]
        if method == "POST":
            state["posts"].append((path, kw.get("json")))
            if path.startswith("/review/"):
                state["queue"] = [q for q in state["queue"] if f"/review/{q['brief_id']}" != path]
                return _Resp({"status": "reviewed"})
            if path == "/brief":
                return _Resp(BRIEF)
        return _Resp({"/review": state["queue"], "/stats": {}}.get(path, []))
    monkeypatch.setattr(httpx, "request", request)
    return state


def test_review_tab_lists_flagged_brief_and_approve_removes_it(fake_api):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert at.tabs[1].label == "Needs partner review (1)"
    assert any(REASON in m.value for m in at.tabs[1].markdown)
    at.text_input(key="note58").input("checked the facts")
    at.button(key="approve58").click().run()
    assert ("/review/58", {"decision": "approve", "note": "checked the facts", "reviewer": None}) in fake_api["posts"]
    assert at.tabs[1].label == "Needs partner review (0)"            # gone after the rerun (a fresh load)
    assert AppTest.from_file(APP, default_timeout=30).run().tabs[1].label == "Needs partner review (0)"


def test_flagged_brief_shows_review_badge_with_reason(fake_api):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input[0].input("jamie.tucker@wrightco.example")
    next(b for b in at.button if b.label == "Brief me").click().run()
    badge = [e.value for e in at.error if "Flagged for partner review" in e.value]
    assert badge and REASON in badge[0]


def test_page_has_no_action_buttons(fake_api):
    at = AppTest.from_file(APP, default_timeout=30).run()
    labels = {b.label for b in at.button}
    assert labels <= {"Brief me", "Approve", "Reject"}, labels
    assert not any(w in l.lower() for l in labels for w in ("send", "email", "schedule", "book", "invite"))
