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

---

## F02: Langfuse receives only masked text and counts

**What was wrong.** Langfuse (tracing/observability) is a third-party service. Every step of the brief
agent was decorated with `@observe`, which by default **records the function's input and output**.
For our steps that is the whole agent state: raw emails, the evidence map and the unmasked brief. The
moment Langfuse keys went into `.env`, real names and email text would have left the region, breaking
the rule "PII masked before any third-party service". (The model calls were already safe: they send
masked text.)

**What changed.**
- `src/hri/agent/brief_graph.py`: all 8 `@observe` decorators now have `capture_input=False,
  capture_output=False`. A small `_span_counts` wrapper attaches only safe metadata to each step's span:
  `mode`, `person_id`, `attempts`, `cost_usd`, the *size* of each list/dict the step returned (e.g.
  `n_evidence`, `n_withheld`) and the context-budget numbers. The top-level `pre_meeting_brief` span gets
  mode, ids, latency, cost, timings and counts.
- `src/hri/obs/tracing.py`: new `span_meta()` helper (wraps `update_current_span(metadata=...)`, never
  breaks a brief if Langfuse fails). `record_generation` is unchanged: it still sends the *masked* prompt
  and output, which is useful for debugging and safe.
- Tests: `tests/test_no_outbound.py::test_observe_never_captures_io` reads every Python file in `src/`
  and fails if any `@observe` is missing either flag. `tests/test_brief_graph.py::test_trace_metadata_carries_no_mail_text`
  runs a brief and checks that the span metadata contains no name, email, organisation or message text.

**Course concept.** *Guardrails and data governance / privacy by design*: control what crosses the trust
boundary, and turn the rule into an automated test so a future edit cannot silently break it.

**How to see it working.** `uv run pytest -q tests/test_no_outbound.py tests/test_brief_graph.py`.
Without real keys you can still see exactly what would be sent: start a local fake Langfuse
(`python3 work/evidence-f02/sink.py /tmp/body.bin`), run the tests with
`LANGFUSE_PUBLIC_KEY=pk-lf-test LANGFUSE_SECRET_KEY=sk-lf-test LANGFUSE_HOST=http://127.0.0.1:9417`, then
`python3 work/evidence-f02/decode.py /tmp/body.bin` → 0 for every name/email, while the span names are
present. A control `@observe()` without the flags leaks the name (count 2), which proves the check works.
With real keys: run 3 briefs and export the traces from the Langfuse API, then grep for names.

**What still breaks it / limits.** Someone could put text into `span_meta(...)` by hand; the helper's
docstring forbids it, but only the metadata test on the brief path checks it. Langfuse traffic does not go
through our egress allowlist/audit (`privacy/egress.py`), so it is not in the `egress_audit` table.
The masked prompts sent by `record_generation` contain pseudonyms (`PERSON_1a2b3c`), not real names;
masking quality is the remaining risk (see test_masking.py).

---

## F05: The fact-checker fails closed and flags briefs for review

**What was wrong.** After the model writes a draft brief, each claim goes through two checks: its
citation must point at a message that was really in the context, then a second model (the verifier)
reads the cited source and says whether it supports the claim. But if the verifier was **down**, or was
**skipped** because time was running out, the claims were kept anyway with a small warning. The verifier
"failed open": unchecked model text reached the partner, which breaks "states nothing unsupported".
Also, a claim the verifier simply forgot to judge was kept.

**What changed.** (`src/hri/agent/brief_graph.py`, `src/hri/api/app.py`)
- If the verifier is unavailable, returns a broken answer, or is skipped for time, **every model-written
  claim is removed** (listed under `dropped_claims` with reason "not fact-checked: ...") and the graph goes
  to the facts-only brief (mode `fallback`). Only code-rendered facts are shown. There is no pointless retry.
- A claim with no verdict from the verifier is now dropped ("no verdict returned"), not kept.
- Every brief now carries `review: {needed, reasons}` (inside the brief and at the top of the API
  response; cached and stale-cache responses pass it through). `needed` is true when:
  the fact-check could not run; the brief is facts-only (mode `fallback`); more than 30% of model claims
  were removed; or a message was withheld as a prompt-injection attempt. This feeds the review queue (F07).
  It is stored inside the brief's JSON `content`, so no database change was needed.
- Tests (`tests/test_brief_graph.py`, 7 new): verifier down → no model text survives, review needed, no
  retry; the verify step on its own returns `verified.claims == []` when the verifier is down and when it
  is skipped for time; all claims supported → `review.needed` false; missing verdict → claim dropped;
  injection message → review reason names the withheld message ID and the injected text never reaches the
  model; fallback → review needed.

**Course concept.** *Evaluator–optimizer loop / reflection with a guardrail that fails closed*: when the
check cannot run, treat the output as unverified and withhold it, rather than trusting it. Plus
*human in the loop*: the `review` flag routes doubtful briefs to a person.

**How to see it working.** `uv run pytest -q tests/test_brief_graph.py`. Live: `make kill-llm`, then
`curl -s -X POST localhost:8000/brief -H 'content-type: application/json' -d '{"query":"janice.hall@martincapitaladvis.example","fresh":true}'`
→ `"review": {"needed": true, "reasons": ["Facts-only brief ...", "1 message(s) withheld ... (m039897) ..."]}`. Then `make revive-llm`.

**What still breaks it / limits.** The verifier is a smaller model than the judge, so a claim it wrongly
marks "supported" still gets through (the 42% judge finding needs the real model to re-measure). The
injection filter is a regex: a cleverly worded injection is not withheld (but the model is still told
emails are data, not instructions). The 30% threshold is a judgement call, not tuned.

---

## F06: The 30-second deadline degrades to facts-only, never HTTP 504

**What was wrong.** The promise is a brief in under 30 seconds. The code kept it by *failing*: the whole
agent ran under `asyncio.wait_for(29 s)`, and when that fired the API answered **HTTP 504** with no brief at
all. There was no time check before the expensive model call, so the model could start with 2 s left.
The SDK waited up to 25 s per call **and retried once**. Masking, the pseudonym-vault writes, unmasking and
database calls ran directly on the event loop, so one slow call froze every other request on the server
(a likely contributor to the 16–18 minute hangs seen in the eval).

**What changed.**
- `src/hri/agent/brief_graph.py`:
  - Before the model writes a draft, it checks the time left. If less than 12 s remain, it skips the model
    and gives the facts-only brief straight away.
  - Each model call gets a timeout from the **remaining** budget (draft: time left − 6 s, to leave room for
    the fact-check; fact-check: time left − 1 s) and `max_retries=0`. If the draft call is too slow → facts
    only; if the fact-check is too slow → F05's fail-closed path (model claims removed, review flagged).
  - The hard deadline no longer raises. The graph now streams its state after every step; if the deadline
    fires mid-way, `_at_deadline` builds the facts-only brief from the data already gathered (mode
    `fallback`, review needed). If not even the data arrived, it returns mode `delayed` with a review flag.
  - Masking + vault writes, context building and unmasking now run in worker threads (`asyncio.to_thread`),
    so they no longer block the event loop.
- `src/hri/llm.py`: `complete()` accepts `timeout` and `max_retries` for a single call (other callers keep the
  old defaults).
- `src/hri/api/app.py`: the `HTTPException(504)` branch is gone; DB reads/writes on the brief path run in
  worker threads; a `delayed` result tries the last good cached brief, like `unavailable` does.
- Tests (`tests/test_brief_graph.py`, 6 new): a model that hangs for 60 s → the brief returns in about 20 s
  (under 30) as facts only with review needed; brief-path model calls carry a budget timeout and no retries;
  too little time → the model is never called; a hang somewhere else (context building) → the hard deadline
  builds facts from the gathered data; a hang before any data → `delayed`, not an error; no 504 in the API.

**Course concept.** *Graceful degradation under a latency budget (deadline propagation)*: pass the remaining
time down to every step, decide early, and always return the best safe answer you have. Also *async
concurrency*: never run blocking work on the event loop.

**How to see it working.** `uv run pytest -q tests/test_brief_graph.py -k "hang or deadline or time or 504"`
(about 32 s, because one test really waits out the 20 s model timeout). `grep -n 'HTTPException(504' src/hri/api/app.py`
→ nothing. Load: `make kill-llm`, then `docker compose run --rm app python -m load.run_load --concurrency 6 --requests 30`
→ 30/30 within 30 s, error rate 0, then `make revive-llm`.

**What still breaks it / limits.** A worker thread whose call hangs is abandoned, not killed; the real SDK
call ends at its own timeout, but a hang inside our own DB code would keep a thread busy (the thread pool is
finite, about 32 workers). The 12 s / 6 s margins are estimates from the old eval timings, not tuned with the
real model tonight (no key). The load test above exercises the fallback path only; the latency of real model
briefs under load still needs a key to measure.
