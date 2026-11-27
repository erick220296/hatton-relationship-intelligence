"""Thin wrapper around the Anthropic SDK.

* Uses the egress-audited HTTP client (all traffic is logged and allowlisted).
* Callers MUST pass already-masked text. `assert_masked` is a last-line check.
* Records token usage and cost on the current Langfuse observation.
* Honours the kill switch used for the "kill a tool" failure demo.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache

import anthropic
import psycopg

from hri.config.settings import cost_usd, kill_switch, settings
from hri.obs import tracing
from hri.privacy import egress
from hri.privacy.egress import EgressBlocked, egress_client, purpose_var
from hri.privacy.masker import MaskingViolation, assert_masked


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


@lru_cache
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings().anthropic_api_key or None, http_client=egress_client(),
                               max_retries=1, timeout=settings().brief_timeout_s)


@lru_cache
def batch_client() -> anthropic.Anthropic:
    """Long-timeout client for Message Batches uploads (still through the audited egress door)."""
    return anthropic.Anthropic(api_key=settings().anthropic_api_key or None, http_client=egress_client(timeout=600),
                               max_retries=0, timeout=600)


def complete(*, model: str, system: str, user: str, purpose: str, max_tokens: int = 2000,
             json_schema: dict | None = None, effort: str | None = "low", timeout: float | None = None,
             max_retries: int | None = None) -> LLMResult:
    """`timeout`/`max_retries` override the client defaults for one call (the brief path passes its
    remaining time budget and max_retries=0 so a slow call cannot outlive the deadline)."""
    if kill_switch():
        raise LLMUnavailable("LLM provider unavailable (kill switch)")
    if not settings().anthropic_api_key:
        raise LLMUnavailable("no model API key configured")
    try:   # last line of defence: nothing unmasked leaves the region, whatever the caller did
        assert_masked(system, user)
    except MaskingViolation as e:
        try:   # record the refusal; the body is NOT stored (it holds the very data we refused to send)
            egress._audit(host="api.anthropic.com", method="POST", path="/v1/messages", purpose=purpose,
                          allowed=False, request_body=f"[blocked by mask check: {e}; body not stored]")
        except Exception:
            pass   # the block itself must not depend on the audit write
        raise LLMUnavailable(f"blocked by mask check: {e}") from e
    token = purpose_var.set(purpose)
    t0 = time.perf_counter()
    try:
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        oc = {}
        if json_schema:
            oc["format"] = {"type": "json_schema", "schema": json_schema}
        if effort:
            oc["effort"] = effort
        if oc:
            kwargs["output_config"] = oc
        opts = {k: v for k, v in (("timeout", timeout), ("max_retries", max_retries)) if v is not None}
        resp = (client().with_options(**opts) if opts else client()).messages.create(**kwargs)
    except anthropic.APIError as e:   # every SDK error: network, timeout, 5xx, 429, 400/401/403/404 ...
        raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
    except (TypeError, EgressBlocked, psycopg.Error) as e:   # auth resolution, blocked host, egress-audit write
        raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
    finally:
        purpose_var.reset(token)
    if resp.stop_reason == "refusal":
        raise LLMUnavailable("model refused")
    if resp.stop_reason == "max_tokens" and json_schema:
        raise LLMUnavailable(f"{purpose}: output truncated at max_tokens={max_tokens}")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    u = resp.usage
    res = LLMResult(text, model, u.input_tokens, u.output_tokens,
                    cost_usd(model, u.input_tokens, u.output_tokens), int((time.perf_counter() - t0) * 1000))
    tracing.record_generation(name=purpose, model=model, input_text=user, output_text=text,
                              tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost=res.cost_usd)
    return res


def complete_json(**kw) -> tuple[dict, LLMResult]:
    r = complete(**kw)
    try:
        return json.loads(r.text), r
    except json.JSONDecodeError as e:   # treat malformed model output like an unavailable model
        raise LLMUnavailable(f"{kw.get('purpose')}: invalid JSON from model: {e}") from e
