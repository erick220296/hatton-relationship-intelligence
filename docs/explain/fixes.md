# Fixes, explained

One section per fix, in the order they were built. Each says what was wrong, what changed, which
course idea it shows, how to see it work, and what can still break it.

Run all tests: `uv run pytest -q` on your machine, or `make build && make test` in Docker.

---

## F04: Test harness for the brief agent, with a fake model

**What was wrong.** The brief agent (the LangGraph pipeline: resolve → gather → build_context →
generate → verify → finalize, with a facts-only fallback) had no tests at all. Its most important
behaviour (dropping bad claims, falling back when the model is down) could only be checked by hand, and
only with a paid API key.

**What changed.**
- `tests/conftest.py`: a `brief_env` fixture that swaps out the three outside dependencies:
  - the **model**: `complete_json` is replaced by a `StubModel` that returns answers the test chooses
    (a draft brief, verifier verdicts, or an error such as `LLMUnavailable`);
  - the **data tools** (MCP server): replaced by `FakeTools`, which return a small synthetic
    relationship (one contact, two messages, one commitment);
  - the **masker**: an in-memory masker that knows the fake names, so no database is needed.
- `tests/test_brief_graph.py` (new): 6 tests:
  1. happy path → mode `llm`, claims kept, the code-rendered header is present;
  2. the model only ever receives masked text (no real name or email in any prompt);
  3. a claim citing a message ID that is not in the context is dropped;
  4. a claim the verifier marks unsupported is dropped, with the verifier's reason;
  5. the model raises `LLMUnavailable` → mode `fallback`, facts-only brief, warning shown;
  6. data tools down → mode `unavailable`, and the model is never called.

**Course concept.** *Evaluation and testing of agents*: test doubles (stubs/fakes) let you test the
agent's control flow, guardrails and fallbacks deterministically, without cost and without a key.

**How to see it working.** `uv run pytest -q tests/test_brief_graph.py` → `6 passed`. In Docker:
`make build && make test` → 26 passed (20 before + 6 new).

**What still breaks it / limits.** The stub proves the *logic* around the model, not the *quality* of
real model output; that still needs the real model and the brief eval (`make eval-brief`). The fake data
is tiny, so the context-budget squeeze on very long histories is not exercised here.

---

## F01: Any model error gives the facts-only brief, never HTTP 500

**What was wrong.** `llm.complete` only turned four kinds of error (network, timeout, 5xx, rate limit)
into `LLMUnavailable`, the signal the agent uses to switch to the facts-only brief. Everything else
escaped and crashed the request with HTTP 500. With no API key in `.env` (our situation tonight) the SDK
raised `TypeError: Could not resolve authentication method`, so **every** brief failed. A wrong model name
(404), a bad key (401/403), a rejected request (400), a blocked egress host, or a failed egress-audit
write did the same. The live-feed extraction loop crashed on the same errors.

**What changed.**
- `src/hri/llm.py`: an empty `ANTHROPIC_API_KEY` is treated like the kill switch (no client is even
  built). All SDK errors (`anthropic.APIError` and its subclasses), `TypeError` from auth resolution,
  `EgressBlocked` and database errors from the egress audit now become `LLMUnavailable`. The brief's
  generate and verify steps and the live feed already catch `LLMUnavailable`, so they degrade instead of
  crashing.
- `src/hri/agent/brief_graph.py`: the facts-only brief now also carries `context_stats` (how many threads
  were kept in full, as index lines, or omitted), so the "exceed the context" demo works without a model.
- `tests/test_llm_errors.py` (new): 14 tests: empty key; 11 error types (401, 403, 400, 404, 429, 500,
  timeout, connection, TypeError, EgressBlocked, audit DB error) each map to `LLMUnavailable`; a brief
  run through the real `llm.complete_json` with no key returns mode `fallback`; a cancelled meeting keeps
  its CANCELLED warning in the fallback brief.

**Course concept.** *Graceful degradation* (fail soft): when a dependency fails, give a smaller but
correct answer instead of an error. The facts-only brief is built by code from the graph, so it cannot
hallucinate.

**How to see it working.** With `ANTHROPIC_API_KEY=` empty: `docker compose restart api`, then
`curl -s -X POST localhost:8000/brief -H 'content-type: application/json' -d '{"query":"jamie.tucker@wrightco.example","fresh":true}'`
→ HTTP 200, `"mode":"fallback"`, warning "Language model unavailable (no model API key configured)".
`make big-context` → exit 0 and prints threads full/indexed/dropped. `uv run pytest -q tests/test_llm_errors.py`.

**What still breaks it / limits.** A bug in *our* code (not the model) still gives a 500, on purpose: we
don't hide our own bugs. The live feed stops extracting at the first model error (graph stays current;
extraction catches up next run). If the model returns valid JSON with the wrong shape, the brief may
still fail.
