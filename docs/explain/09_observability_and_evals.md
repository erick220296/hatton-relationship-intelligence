# 09 · Observability, tests, evals and the load test

## In one sentence
Langfuse records every brief as one trace: timings, model calls, tokens, cost, tags and scores, with masked text and counts only. Three kinds of automated checks prove the system works: unit tests for logic and safety rules, evals against the planted answer key for quality, and a load test for speed under a burst.

## How it works

### Terms
- **Observability:** seeing what a running system did, and why.
- **Trace:** the record of one request, made of nested *spans* (one per step).
- **Langfuse:** a hosted tracing service for AI apps.
- **Unit test:** a small automatic check of one behaviour.
- **Eval:** a quality measurement against known right answers.
- **Load test:** many requests at once, to measure speed and errors.

### Part A: Langfuse tracing (`src/hri/obs/tracing.py`)
- **Off unless both keys are set** (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`). They are empty in `.env` today, so tracing is currently a no-op and the system runs normally without it.
- **What is captured when it is on:**
  - **One trace per brief** (fix F10, `brief_graph.py: run_brief`):
    - session = the run's ID;
    - tags: `brief`, plus `load-test` when the load test sends header `X-HRI-Run-Type: load-test`, plus the final mode (`llm`/`fallback`/…), plus `escalated` if review is needed;
    - **scores:** `latency_ms`, `cost_usd`, `dropped_ratio` (claims removed ÷ claims written), `citation_coverage` (claims with a valid citation ÷ claims written).
  - The trace ID is returned in the API response and stored with the brief (`api/app.py: _store`).
  - **One span per step** (resolve, gather, build_context, generate, verify, finalize, fallback) carrying **only counts and IDs**: mode, person_id, attempts, cost, the size of each list, and the context statistics (`brief_graph.py: _span_counts`).
  - **One "generation" per model call**, with the **masked** prompt and answer, token counts and cost (`tracing.record_generation`, called from `llm.py: complete`).
- **What is never captured:** raw email, the evidence map, unmasked briefs. Every `@observe` decorator has `capture_input=False, capture_output=False` (fix F02). A test scans all of `src/` to keep it that way (`tests/test_no_outbound.py: test_observe_never_captures_io`).
- **Proof without real keys** (27 Nov, `work/eval_batch1.md`): Ash pointed Langfuse at a fake receiver and ran 3 real-database briefs. The decoded traffic had **0 hits for 3,211 names, emails and organisation names**. A deliberately unsafe control *did* leak, which shows the scan works.
- **Per-step timings** are also returned in every API response (`timings`, from `brief_graph.py: _timed`), so latency is visible even without Langfuse.

### Part B: unit tests (`tests/`, run with `make test`)
| File | What it checks |
|---|---|
| `test_no_outbound.py` (8) | No mail or calendar libraries; no send-like calls; HTTP clients only in the egress module; every MCP tool read-only; no action-like API paths; egress blocks Gmail SMTP and Microsoft Graph; the tool DB role cannot write or read the vault; no `@observe` captures input or output |
| `test_masking.py` (3) | All PII removed from a sample email; codes stable and reversible; an injection payload's email address is masked |
| `test_warmth_and_parse.py` (7 functions, 10 cases) | Steady → warm; cooled → quiet; slow LP not flagged early; one-off burst never quiet; unanswered outreach doesn't count; malformed records rejected; HTML converted |
| `test_brief_graph.py` (22) | The brief agent with a **fake model, fake tools and an in-memory masker** (`tests/conftest.py: brief_env`, `StubModel`, `FakeTools`): happy path; model sees only masked text; bad citations dropped; unsupported claims dropped; model down → fallback; tools down → unavailable; fail-closed checker; review flag; deadline and hang cases; no 504; trace tags, scores and ID |
| `test_llm_errors.py` (4 functions) | No key, and 11 error types, all become "model unavailable"; a no-key brief returns fallback; a cancelled meeting keeps its warning |
| `test_review_queue.py` (3), `test_stale_cache.py` (3), `test_ui_review.py` (3), `test_trace_api.py` (2) | Review queue API and log; stale-cache labelling; UI badge, tab and approve button, and no send buttons; load-test tag and trace ID stored |

Tests marked `db` need the running database and are skipped outside Docker (`tests/conftest.py`).
- **Last recorded run:** `make test` → **55 passed**, 3 times (Ash, 27 Nov, `work/eval_batch1.md`, before F07–F10 tests were counted in full). I did not run the tests myself (read-only brief).

### Part C: evals (quality against the answer key)
- **Quiet detection** (`evals/run_quiet_eval.py`, `make eval-quiet`). Compares flagged-quiet pairs with the 60 planted cooled relationships; dead deals are reported separately.
  - Committed evidence: precision 0.767, recall 0.933 (`docs/evidence/quiet_eval.json`).
  - Current database: 0.809 / 0.917, the same on 3 runs (Ash, 27 Nov).
- **Brief quality** (`evals/run_brief_eval.py`, `make eval-brief`). 35 named cases through the real API:
  - *ordinary:* warm contacts, cooled contacts (status must be quiet), by meeting;
  - *awkward:* dead deal, name collisions, oversize history, Spanish email, cancelled meeting;
  - *hostile:* the prompt-injection email, nonsense and SQL-injection queries, unknown meeting.

  Each case has pass rules: latency ≤ 30 s, the expected mode, required warnings, forbidden phrases. A separate **judge** model (Sonnet, a stricter prompt than the in-pipeline checker) re-checks every final claim against its raw cited message, masked. It also measures **commitment recall** (planted commitments that the brief cites).
  - Last real run, 25 Nov, **before the fixes** (`docs/evidence/brief_eval.md`):
    - 10/35 passed (ordinary 2/21, awkward 3/8, hostile 5/6)
    - judge: 86 of 204 claims unsupported (**58% grounded**)
    - commitment recall 42/56
    - latency p50 11.0 s, p95 18.3 s
    - mean cost $0.0173
    - 7 requests hung for 950–1,085 s
- **Failure demos** as checks: `make kill-llm`, `make kill-mcp`, `make malformed`, `make big-context`.
- **Masking leak scan** (`evals/leak_scan.sql`): searches every logged outbound request for real full names, emails and org names of 6+ letters.

### Part D: load test (`load/run_load.py`, `make load`)
- Fires N brief requests at concurrency C (default targets: 15 and 45 at once) against the running API. It records p50/p95/p99, the share within 30 s, the error rate, modes and cost, and writes `docs/evidence/load_c{C}.json`.
- **Only fallback-path runs exist** (kill switch on, no key): concurrency 6, 30 requests, 3 runs → 30/30 within 30 s, 0 errors, p95 about 0.5–0.6 s (`work/eval_batch1_evidence/load_c6_run{1,2,3}.json`; `work/evidence-f06/`).

### What evidence exists, and what is missing
| Exists | Missing |
|---|---|
| Backfill report (real run, real batch IDs) | **A brief eval on the fixed code** (needs a key) |
| Quiet eval (committed + 3 current runs) | **A load test with real model calls**, at 15 and 45 concurrent |
| Brief eval ×2 from 25 Nov (pre-fix) | **Any real Langfuse traces or dashboard** (no keys) |
| Unit test runs (55 passed ×3) | **`egress_audit` rows / leak scan output** (no successful model call yet) |
| Fallback-path load runs (c=6) | Commitment extraction on the current database |
| Langfuse fake-receiver checks (0 PII) | A judge model different from the writer (`ADR.md §9`, §16) |

## Why it was built this way
- **Tracing is required by the course**, and it is the only way to see p95 latency and cost per brief in production.
- **Masked-only tracing** because Langfuse is a third party (client rule). The decorator default would have leaked, hence F02 and its test.
- **Fake model in unit tests** (F04). The agent's control flow (gates, fallbacks, deadline) can be tested free, fast and identically every time.
- **An answer-key eval** gives exact, repeatable numbers. The judge is separate from the checker, so the system does not mark its own homework with the same prompt.
- **Rejected:**
  - Manual spot-checks: not repeatable.
  - LLM-only "vibe" evals without an answer key.
  - Langfuse's default full capture: it leaks raw data.

## What breaks it
- **The judge shares a model with the writer** (Sonnet), so a panel can raise self-preference bias. `ADR.md §9` admits this and plans a different judge.
- **The brief eval is stale.** All brief-quality numbers predate fixes F01–F10.
- **Langfuse bypasses the egress log.** Its traffic does not go through our egress door, so it is not in `egress_audit`. Only the masking and the decorator settings protect it.
- **Scores measure our own checks** (`dropped_ratio`, `citation_coverage`), not the truth. The judge eval is the real quality measure (F10 limits).
- **Fallback-only load numbers** say nothing about real model latency under a burst.
- **Eval quirks:** the brief eval accepts a `cached` answer as `llm`; `leak_scan.sql` does not check first names, phones, addresses or short org names.

## Agentic pattern / course concept it shows
*Observability (tracing, tags, scores)*, *evaluation-driven development*, *LLM-as-judge*, *test doubles for agents*, *load and latency testing*, and *privacy-safe telemetry*.

## 3 likely panel questions
1. **"How do you know the briefs are accurate?"**
   We run 35 named cases (ordinary, awkward, hostile) against an answer key, and a separate judge model re-checks every claim against its source. The last real-model run was before tonight's fixes: 10 of 35 passed and 58% of claims were grounded. Since then the in-pipeline checker fails closed. We need a funded key to re-measure, and that is the first item on our open list.
2. **"What do you send to Langfuse?"**
   Masked prompts and answers for each model call, token counts and costs, step names with counts, and per-brief tags and scores. Never raw email or unmasked briefs. Every trace decorator turns input/output capture off, and a test enforces that. A fake-receiver check found zero real names in the traffic.
3. **"Did you load-test it?"**
   Only the facts-only path so far, because there is no model key: 30 requests at 6 concurrent, all within 30 s, no errors, p95 about half a second. The real-model load test at 15 and 45 concurrent is scripted (`make load`) and tagged in Langfuse, but has not been run.
