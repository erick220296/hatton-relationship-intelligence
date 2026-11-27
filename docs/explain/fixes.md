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
