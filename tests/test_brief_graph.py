"""Brief graph behaviour with a stubbed model (no API key, no MCP server, no database needed).

The `brief_env` fixture (tests/conftest.py) swaps in: a fake MCP tool server with synthetic data, an
in-memory masker, and a StubModel in place of `complete_json`. Each test sets what the "model" answers.
"""
from __future__ import annotations

from hri.agent import brief_graph as bg
from hri.llm import LLMUnavailable

REQ = {"query": "Guy Cameron", "partner_email": "lucy.allan@hattoncap.example"}


def _texts(brief: dict) -> list[str]:
    return [c["text"] for sec in brief["sections"].values() for c in sec]


async def test_happy_path_returns_verified_llm_brief(brief_env):
    res = await bg.run_brief(REQ, "t-happy")
    assert res["mode"] == "llm"
    assert "They will send the IC memo by Friday." in _texts(res["brief"])
    assert res["brief"]["dropped_claims"] == []
    assert res["brief"]["person"]["name"] == "Guy Cameron"           # code-rendered header
    assert [p for p, _ in brief_env.calls] == ["brief_generate", "brief_verify"]


async def test_model_only_ever_sees_masked_text(brief_env):
    await bg.run_brief(REQ, "t-masked")
    for _, kw in brief_env.calls:
        assert "Guy Cameron" not in kw["user"] and "guy.cameron@" not in kw["user"]


async def test_claim_with_invalid_citation_is_dropped(brief_env):
    brief_env.brief["signals"] = [{"text": "They plan to double their commitment.", "cites": ["m999999"]}]
    res = await bg.run_brief(REQ, "t-badcite")
    assert res["mode"] == "llm"
    assert "They plan to double their commitment." not in _texts(res["brief"])
    dropped = res["brief"]["dropped_claims"]
    assert any(d["text"] == "They plan to double their commitment." and "citation" in d["reason"] for d in dropped)


async def test_verifier_unsupported_claim_is_dropped(brief_env):
    def verdicts(user):
        n = user.count("CLAIM ")
        return {"verdicts": [{"i": i, "reason": "source says Monday, not Friday" if i == 4 else "ok",
                              "supported": i != 4} for i in range(n)]}
    brief_env.verify = verdicts
    res = await bg.run_brief(REQ, "t-unsupported")
    assert "They will send the IC memo by Friday." not in _texts(res["brief"])
    assert any(d["reason"].startswith("verifier:") for d in res["brief"]["dropped_claims"])


async def test_generate_llm_unavailable_gives_facts_only_brief(brief_env):
    brief_env.brief = LLMUnavailable("no model API key configured")
    res = await bg.run_brief(REQ, "t-down")
    assert res["mode"] == "fallback"
    texts = _texts(res["brief"])
    assert not any("IC memo by Friday" in t for t in texts if t.startswith("They"))   # no model-written text
    assert any("Share the updated model by Monday" in t for t in texts)               # code-rendered facts
    assert res["brief"]["sections"]["who"][0]["cites"] == ["PROFILE"]
    assert any("Language model unavailable" in w for w in res["warnings"])


async def test_tools_down_returns_unavailable_without_model_call(brief_env):
    brief_env.tools_down = True
    res = await bg.run_brief(REQ, "t-tools")
    assert res["mode"] == "unavailable"
    assert brief_env.calls == []


async def test_trace_metadata_carries_no_mail_text(brief_env, monkeypatch):
    """F02: what the node spans send to Langfuse is ids, counts and timings, never names, emails or bodies."""
    import json
    from hri.obs import tracing
    sent = []
    monkeypatch.setattr(tracing, "span_meta", lambda **kw: sent.append(kw))
    await bg.run_brief(REQ, "t-trace")
    assert len(sent) >= 7                     # resolve, gather, build_context, generate, verify, finalize, run
    blob = json.dumps(sent, default=str)
    for secret in ("Guy Cameron", "Lucy Allan", "guy.cameron@", "lucy.allan@", "Graham Endowment", "IC memo"):
        assert secret not in blob, secret
