"""F01: any model error becomes LLMUnavailable (so the brief degrades to facts-only instead of HTTP 500).
No network: the SDK client is replaced by one that raises the error under test."""
from __future__ import annotations

import anthropic
import httpx2
import psycopg
import pytest

from hri import llm
from hri.agent import brief_graph as bg
from hri.config.settings import settings
from hri.privacy.egress import EgressBlocked

URL = "https://api.anthropic.com/v1/messages"


def _status(cls, code):
    req = httpx2.Request("POST", URL)
    return cls(f"HTTP {code}", response=httpx2.Response(code, request=req), body=None)


ERRORS = [
    _status(anthropic.AuthenticationError, 401),
    _status(anthropic.PermissionDeniedError, 403),
    _status(anthropic.BadRequestError, 400),
    _status(anthropic.NotFoundError, 404),            # e.g. a wrong model name
    _status(anthropic.RateLimitError, 429),
    _status(anthropic.InternalServerError, 500),
    anthropic.APITimeoutError(request=httpx2.Request("POST", URL)),
    anthropic.APIConnectionError(request=httpx2.Request("POST", URL)),
    TypeError("Could not resolve authentication method"),
    EgressBlocked("egress to evil.example is not allowlisted"),
    psycopg.OperationalError("egress audit write failed"),
]


class _Raising:
    def __init__(self, err):
        self.messages = self
        self.err = err
        self.called = False

    def create(self, **kw):
        self.called = True
        raise self.err


@pytest.fixture
def keyed(monkeypatch):
    from hri.privacy import masker as masker_mod
    mk = masker_mod.Masker()
    mk.load_known_entities([{"name": "Guy Cameron", "org": "Graham Endowment"}])   # in-memory: no DB
    monkeypatch.setattr(masker_mod, "_masker", mk)
    monkeypatch.setattr(llm, "kill_switch", lambda: False)
    monkeypatch.setattr(settings(), "anthropic_api_key", "test-key-not-real")


def _call():
    return llm.complete(model="m", system="s", user="masked text", purpose="test")


def test_empty_api_key_is_treated_like_the_kill_switch(monkeypatch):
    monkeypatch.setattr(llm, "kill_switch", lambda: False)
    monkeypatch.setattr(settings(), "anthropic_api_key", "")
    monkeypatch.setattr(llm, "client", lambda: pytest.fail("must not build a client without a key"))
    with pytest.raises(llm.LLMUnavailable, match="no model API key"):
        _call()


@pytest.mark.parametrize("err", ERRORS, ids=lambda e: type(e).__name__)
def test_every_model_error_maps_to_llm_unavailable(keyed, monkeypatch, err):
    fake = _Raising(err)
    monkeypatch.setattr(llm, "client", lambda: fake)
    with pytest.raises(llm.LLMUnavailable):
        _call()
    assert fake.called   # the error came from the (fake) HTTP call, not from an earlier check


async def test_brief_without_key_degrades_to_facts_only(brief_env, monkeypatch):
    """The real llm.complete_json (not the stub) with no key: the brief graph returns mode fallback."""
    monkeypatch.setattr(bg, "complete_json", llm.complete_json)
    monkeypatch.setattr(llm, "kill_switch", lambda: False)
    monkeypatch.setattr(settings(), "anthropic_api_key", "")
    res = await bg.run_brief({"query": "Guy Cameron"}, "t-nokey")
    assert res["mode"] == "fallback"
    assert any("Language model unavailable (no model API key configured)" in w for w in res["warnings"])
    assert res["brief"]["context_stats"]["threads_full"] == 1


async def test_cancelled_meeting_warning_survives_fallback(brief_env, monkeypatch):
    brief_env.meeting["status"] = "cancelled"
    brief_env.brief = llm.LLMUnavailable("no model API key configured")
    res = await bg.run_brief({"meeting_id": "c00001"}, "t-cancel")
    assert res["mode"] == "fallback"
    assert any("CANCELLED" in w for w in res["warnings"])
