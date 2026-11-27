import copy
import os
import re
import threading
from contextlib import asynccontextmanager

import pytest

# B4: test runs must never send traces to the real Langfuse (repo/.env holds real keys and the Docker test
# container loads it). This runs before any hri module is imported, so hri.obs.tracing sees blank keys and
# stays a no-op. Only an explicit local sink run (LANGFUSE_HOST on localhost, as in docs/explain/fixes.md
# F02/F10) keeps its fake keys.
LOCAL_LANGFUSE = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)")
if not LOCAL_LANGFUSE.match(os.getenv("LANGFUSE_HOST", "")):
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs the running Postgres container")


def pytest_collection_modifyitems(config, items):
    if os.getenv("DATABASE_URL", "").find("@db:") == -1:
        skip = pytest.mark.skip(reason="no database container")
        for it in items:
            if "db" in it.keywords:
                it.add_marker(skip)


# ---------------------------------------------------------------- brief-graph harness (no DB, no MCP, no model)
# Synthetic fixture data shaped like the MCP tool results the brief graph consumes.
KNOWN = [{"name": "Guy Cameron", "org": "Graham Endowment"}, {"name": "Lucy Allan", "org": "Hatton Capital"}]
PROFILE = {"name": "Guy Cameron", "email": "guy.cameron@grahamendowment.example", "title": "Managing Director",
           "org": "Graham Endowment", "kind": "lp",
           "relationships": [{"partner": "Lucy Allan", "partner_email": "lucy.allan@hattoncap.example",
                              "status": "warm", "warmth": 70.0, "warmth_peak": 80.0,
                              "first_contact": "2025-01-10", "last_contact": "2026-11-02",
                              "last_inbound": "2026-11-01", "n_meetings": 3, "n_inbound": 12, "n_outbound": 10,
                              "explanation": "steady two-way contact"}]}
THREADS = [{"thread_id": "t1", "subject": "IC memo timing", "messages": [
    {"id": "m000001", "sent_at": "2026-11-01T10:00:00", "sender": "Guy Cameron", "sender_is_partner": False,
     "body": "Lucy, I will send the IC memo by Friday."},
    {"id": "m000002", "sent_at": "2026-11-02T09:00:00", "sender": "Lucy Allan", "sender_is_partner": True,
     "body": "Thanks Guy. We will share the updated model by Monday."}]}]
COMMITMENTS = [{"message_id": "m000002", "sent_at": "2026-11-02T09:00:00", "owner": "us",
                "text": "Share the updated model by Monday", "due": None}]
INDEX = [{"thread_id": "t0", "first_id": "m000000", "started": "2025-01-10T09:00:00", "subject": "Introduction",
          "n_messages": 3}]
MEETING = {"id": "c00001", "title": "Fund III update", "start_at": "2026-11-25T10:00:00", "status": "confirmed",
           "location": "London", "attendees": [
               {"person_id": 7, "email": PROFILE["email"], "is_partner": False},
               {"person_id": 1, "email": "lucy.allan@hattoncap.example", "is_partner": True}]}

# A claim set the stubbed model returns for "brief_generate" (plain text: no pseudonyms to unmask).
GOOD_DRAFT = {
    "who": [{"text": "Managing Director at the endowment.", "cites": ["PROFILE"]}],
    "history": [{"text": "Known since January 2025, three meetings.", "cites": ["PROFILE"]}],
    "last_discussed": [{"text": "Timing of the IC memo.", "cites": ["m000001"]}],
    "our_commitments": [{"text": "We will share the updated model by Monday.", "cites": ["m000002"]}],
    "their_commitments": [{"text": "They will send the IC memo by Friday.", "cites": ["m000001"]}],
    "signals": [],
}


class FakeTools:
    """Stands in for hri.agent.tools.MCPTools. `down=True` simulates the MCP server being unreachable."""

    def __init__(self, threads, meeting):
        self.threads = threads
        self.meeting = meeting

    async def call(self, name, **args):
        return {
            "find_person": lambda: [{"person_id": 7, "name": PROFILE["name"], "email": PROFILE["email"], "rank": 0}],
            "get_meeting": lambda: copy.deepcopy(self.meeting),
            "get_relationship": lambda: copy.deepcopy(PROFILE),
            "get_recent_threads": lambda: copy.deepcopy(self.threads),
            "get_commitments": lambda: copy.deepcopy(COMMITMENTS),
            "get_thread_index": lambda: copy.deepcopy(INDEX),
            "search_messages": lambda: [],
        }[name]()


class StubModel:
    """Replaces hri.agent.brief_graph.complete_json. Set `.brief` / `.verify` to a dict, an exception, or a
    callable(user_text) -> dict. Default verify: every claim supported. `.calls` records (purpose, kwargs)."""

    def __init__(self):
        self.brief = copy.deepcopy(GOOD_DRAFT)
        self.verify = None
        self.calls = []
        self.release = threading.Event()   # set at teardown so any "hanging" stub thread exits

    def __call__(self, **kw):
        from hri.llm import LLMResult
        purpose = kw.get("purpose")
        self.calls.append((purpose, kw))
        spec = self.brief if purpose == "brief_generate" else self.verify
        if spec is None:   # default verifier: all claims supported
            n = kw["user"].count("CLAIM ")
            spec = {"verdicts": [{"i": i, "reason": "stated in source", "supported": True} for i in range(n)]}
        if isinstance(spec, BaseException):
            raise spec
        out = spec(kw["user"]) if callable(spec) else copy.deepcopy(spec)
        return out, LLMResult(text="{}", model=kw.get("model", "stub"), tokens_in=100, tokens_out=50,
                              cost_usd=0.001, latency_ms=5)


@pytest.fixture
def brief_env(monkeypatch):
    """Brief graph with a stubbed model, fake MCP tools and an in-memory masker. Returns the StubModel;
    set `env.tools_down = True` to make the MCP server unreachable;
    edit `env.threads` / `env.meeting` to change what the tools return."""
    from hri.agent import brief_graph as bg
    from hri.agent.tools import ToolUnavailable
    from hri.privacy import masker as masker_mod

    mk = masker_mod.Masker(use_ner=False)
    mk.load_known_entities(KNOWN)
    monkeypatch.setattr(mk, "flush", lambda: None)            # no pseudonym-vault writes
    monkeypatch.setattr(masker_mod, "_masker", mk)

    stub = StubModel()
    stub.tools_down = False
    stub.threads = copy.deepcopy(THREADS)
    stub.meeting = copy.deepcopy(MEETING)

    @asynccontextmanager
    async def fake_mcp_tools(url=None):
        if stub.tools_down:
            raise ToolUnavailable("MCP server unreachable (test)")
        yield FakeTools(stub.threads, stub.meeting)

    monkeypatch.setattr(bg, "mcp_tools", fake_mcp_tools)
    monkeypatch.setattr(bg, "complete_json", stub)
    monkeypatch.setattr(bg, "_graph", None)
    yield stub
    stub.release.set()
