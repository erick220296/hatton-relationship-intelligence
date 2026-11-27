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

---

## F07: Human review queue (backend) and decision log

**What was wrong.** F05 marks doubtful briefs with `review.needed`, but nothing happened next. There was no
list for a partner to work through, no way to record "I checked this", and no audit trail.

**What changed.**
- `src/hri/graph/schema.sql` + new `src/hri/graph/migrations/001_review_queue.sql`: `brief` gets `status`
  (`ok | needs_review | reviewed | rejected`) and `review_reasons`; a new `review_log` table records
  `brief_id, decision, note, reviewer, at`. The migration is additive and safe to re-run. It is needed because
  `schema.sql` only runs on an empty database volume. It has already been applied to the running stack.
- `src/hri/api/app.py`: saving a brief sets `status = needs_review` when `review.needed` is true.
  `GET /review` lists flagged briefs, newest first, with reasons. `POST /review/{brief_id}`
  `{"decision": "approve"|"reject", "note": ...}` updates the status and writes a `review_log` row in one
  transaction. It returns 404 for an unknown brief and 409 if the brief was already decided. It writes only
  to our own database: nothing is sent to anyone.
- `src/hri/agent/brief_graph.py`: the fallback review reason now says "model unavailable" explicitly.
- Tests: `tests/test_review_queue.py` (new). The decision must be approve/reject (422 otherwise). In
  Docker (db-marked): a flagged brief is queued, approved, leaves the queue and is logged once; a second
  decision returns 409; an unflagged brief is not queued; an unknown id returns 404. The tests delete the
  rows they create.

**Course concept.** *Human in the loop / escalation*: the agent does not hide uncertainty, it routes it to a
person. The decision log is the *audit trail*, and also a simple *episodic memory* of partner feedback.

**How to see it working.** `make kill-llm`; brief for jamie.tucker (response has `brief_id`);
`curl localhost:8000/review` lists it with the reason "model unavailable";
`curl -X POST localhost:8000/review/<id> -H 'content-type: application/json' -d '{"decision":"approve","note":"checked"}'`
→ 200, and the brief is gone from `/review`;
`docker compose exec db psql -U hri -c 'select * from review_log'` shows the row with its timestamp. Then `make revive-llm`.

**What still breaks it / limits.** There are no logins: `reviewer` is whatever the caller sends (default
"partner"). Every fresh fallback brief is queued, including load-test runs, so the queue can fill with
repeats for the same person. The tool server's read-only database role does not see `review_log` on the
existing volume (it doesn't need to).

---

## F08: Human review in the UI: badge and "Needs partner review" tab

**What was wrong.** The review queue (F07) existed only as an API. On stage, nobody could see that a brief
had been flagged, or act on it.

**What changed.** (`src/hri/ui/streamlit_app.py`)
- Any brief with `review.needed` shows a red badge: **"Flagged for partner review: (1) reason …"** plus its
  brief number.
- A new tab, **"Needs partner review (N)"**, lists `GET /review`: who, mode, time, reasons. Each card has a
  Note field and **Approve** / **Reject** buttons that call `POST /review/{id}`; the list refreshes and the
  card disappears. It shows at most 50 cards ("50+" in the tab title when there are more).
- Tests: `tests/test_ui_review.py` (new; uses Streamlit's `AppTest` with a fake API, no stack needed). The
  tab lists the flagged brief, Approve sends `{"decision": "approve", "note": ...}`, and the brief is gone
  after the rerun and on a fresh load. A flagged brief shows the badge with its reason. The only buttons on
  the page are Brief me, Approve and Reject (plus meeting buttons when there are meetings): no
  send/email/schedule.

**Course concept.** *Human in the loop*, made visible: the escalation is part of the product, not a log file.

**How to see it working.** `make kill-llm`; open http://localhost:8501; brief `jamie.tucker@wrightco.example`
→ red "Flagged for partner review" badge. Reload, open the "Needs partner review" tab, type a note, click
Approve → the card disappears and stays gone after reload; `select * from review_log` shows your note.
`make revive-llm`. Tests: `uv run pytest -q tests/test_ui_review.py`.

**What still breaks it / limits.** The tab count is read when the page loads, so a brief you just made
appears in the tab after the next reload. Every fresh fallback brief is queued, so after test or load runs
the list is long and repetitive (more than 100 right now). For a clean demo, approve or reject the old ones
first, or ask for a "clear test briefs" step. There are no logins: the reviewer is the partner picked in
the sidebar.

---

## F09: When the data service is down, show the last brief of any kind, clearly labelled

**What was wrong.** The "kill a tool" demo stops the MCP data server. The API then tried to serve the last
stored brief, but only *verified* (`llm`) briefs counted. Tonight there are none (no model key), so the
partner got "no brief" (`mode: unavailable`).

**What changed.** (`src/hri/api/app.py`)
- `_cached(..., any_mode=True)` is used only on the stale path (data service down, or `delayed`). It accepts
  a verified or a facts-only brief, prefers verified, and filters by partner when one is given. The normal
  "prepared earlier" cache still serves verified briefs only.
- The response is `mode: stale_cache` with the old brief, its `brief_id`, `stale_mode` and review flag, plus a
  warning: "Showing the last good brief from 2026-11-27 15:50 (0 min old, facts-only brief): live
  relationship data is unavailable, so it may be out of date."
- Tests: `tests/test_stale_cache.py` (new, Docker/db-marked, data tools stubbed as down). The last fallback
  brief is served with the label; a verified brief is preferred over a newer facts-only one; the normal cache
  still ignores facts-only briefs. The tests delete the rows they create.

**Course concept.** *Graceful degradation / resilience to tool failure*: when a tool dies mid-session, fall
back to the last known-good answer, and say plainly how old it is and what kind it is.

**How to see it working.** `make kill-llm`; brief jamie.tucker with `"fresh":true` (stores a facts-only
brief); `make kill-mcp`;
`curl -s -X POST localhost:8000/brief -H 'content-type: application/json' -d '{"query":"jamie.tucker@wrightco.example"}'`
→ 200, `mode: stale_cache`, brief present, warning "Showing the last good brief from …". `make revive-mcp`
→ normal again; `make revive-llm`.

**What still breaks it / limits.** With the tools down, a name is resolved from our own `person` table
(exact name or email only); an ambiguous or partial name gets no cached brief. If no brief was ever made for
that person, there is nothing to show (mode stays `unavailable`). The cached brief can be days old; the
warning says so, but the facts in it are not refreshed.

---

## F10: Every brief is one Langfuse trace with its id, tags, scores and cost

**What was wrong.** The trace id saved with each brief was always empty: the API asked for the "current
trace" *after* the brief's trace had already closed. Traces had no tags, so fallback, escalated and load-test
runs could not be filtered, and no scores, so the latency distribution and quality numbers could not be
charted. The old `update_trace` helper also called a method that this Langfuse version does not have.

**What changed.**
- `src/hri/agent/brief_graph.py`: `run_brief` (the root `pre_meeting_brief` span) now:
  - sets the trace's **session = run_id** and tags `brief` (+ `load-test` when asked) at the start, then
    `brief, <mode>, escalated` (if review is needed) at the end;
  - sends **scores**: `latency_ms`, `cost_usd`, `dropped_ratio` (claims removed ÷ claims written; 0 when
    nothing was written) and `citation_coverage` (claims with a valid citation ÷ claims written; skipped when
    the model wrote nothing);
  - captures **trace_id** while the trace is still open and returns it. The graph body moved unchanged into
    `_run_brief`.
- `src/hri/obs/tracing.py`: `trace_attributes()` (wraps Langfuse `propagate_attributes`) and `score_trace()`.
  Both are no-ops without keys and never break a brief. Only fixed labels, ids and numbers are sent.
- `src/hri/api/app.py`: the header `X-HRI-Run-Type: load-test` becomes the `load-test` tag; the returned
  trace_id is stored in `brief.trace_id`.
- `load/run_load.py`: sends that header, so load-test briefs are tagged.
- Cost: each model call is already recorded as a generation with token usage and cost (unchanged); the trace
  total comes from those, and `cost_usd` is also a score.
- Tests: `tests/test_brief_graph.py` (+2: session/tags/scores/trace_id on a normal brief with one bad
  citation → dropped_ratio 1/6, citation_coverage 5/6; a fallback brief is tagged `fallback, escalated`);
  `tests/test_trace_api.py` (new: the header becomes the tag; in Docker, the trace_id is stored with the brief).

**Course concept.** *Observability and evaluation in production*: every run is traceable end to end, tagged
for filtering, and carries quality and latency scores you can chart (p50/p95/p99) and tie to cost.

**How to see it working.** Without keys, use the local fake Langfuse: `python3 work/evidence-f02/sink.py /tmp/b.bin`,
then `LANGFUSE_PUBLIC_KEY=pk-lf-test LANGFUSE_SECRET_KEY=sk-lf-test LANGFUSE_HOST=http://127.0.0.1:9417 uv run pytest -q tests/test_brief_graph.py -k "happy or generate_llm_unavailable"`,
then `python3 work/evidence-f10/decode_traces.py /tmp/b.bin` → tag sets `brief`, `brief llm`,
`brief fallback escalated`; scores latency_ms, cost_usd, dropped_ratio (+ citation_coverage on the llm
brief), each with its trace id; 0 PII hits. With real keys: POST /brief returns a non-null `trace_id` equal to
`select trace_id from brief order by id desc limit 1`, and the trace shows the tags and scores.

**What still breaks it / limits.** Without Langfuse keys `trace_id` is null (correct, there is no trace).
Child spans carry only the `brief` tag; the mode/escalated tags are set on the root span at the end (Langfuse
shows the trace's tags as the union, as far as I can tell; this is not checked against the real service yet).
Scores measure our own checks (citations, verifier drops), not ground truth: the judge eval is still the real
quality measure.

---

## F03: Secrets and data stay out of the image; database and tool server on localhost only

**What was wrong.** The Dockerfile ends with `COPY . .`, and there was no `.dockerignore`, so the image layers
contained `.env` (where the API keys go), the host's `.venv`, and `.git`. Anyone who got the image got the
secrets. Also, Postgres (5432, password `hri`) and the MCP tool server (8001, which returns *unmasked*
relationship data) were published on every network interface, so other machines on the same Wi-Fi could
reach them.

**What changed.**
- `.dockerignore` (new): excludes `.env*` (keeps `.env.example`), `.venv/`, `data/`, `.git/`, caches and
  `*.egg-info`. Containers still get their settings at run time from compose `env_file: .env`, which is
  read when the container starts and is not stored in the image.
- `docker-compose.yml`: db and mcp ports are bound to `127.0.0.1`. The API (8000) and UI (8501) were left as
  they were, as Boris asked (only DB and MCP).
- `Makefile`: `make build` now also builds the one-off `app` image that `make test` uses. Before, it silently
  skipped it (that service sits behind a compose "profile"), so `make build && make test` could test old code.
- The running stack was rebuilt and recreated (db, mcp, api, ui). The database volume was kept: still
  39,099 messages.

**Course concept.** *Secure deployment / least exposure*: secrets are injected at run time, never baked
into images, and internal services are not exposed to the network.

**How to see it working.** `docker run --rm --entrypoint ls repo-api -a /app` → no `.env`, `.venv` or `data`.
`docker compose ps` → `127.0.0.1:5432` and `127.0.0.1:8001`, no `0.0.0.0` for those two. `make test` passes;
http://localhost:8501 loads.

**What still breaks it / limits.** The API and UI still listen on all interfaces (by choice, so the demo can
be shown from another device); there's no login on them. Old images built before this fix may still exist
in local caches or registries: rebuild and remove them. `.env` must still never be committed (it is in
`.gitignore`).

---

## F12: A last-line mask check at the door out (assert_masked)

**What was wrong.** The header of `llm.py` promised an `assert_masked` safety check, but it did not exist.
Whether a prompt was masked depended entirely on every caller remembering to mask. One forgotten
`mask()` call would send real names to the model provider.

**What changed.**
- `src/hri/privacy/masker.py`: `Masker.find_unmasked(text)` finds any real email address, or the full name
  of any known person from the masking dictionary (case-insensitive). It returns only the *kinds* found,
  never the values. `assert_masked(*texts)` raises `MaskingViolation` on a hit. It fails closed: if the
  dictionary can't be loaded, nothing is sent.
- `src/hri/llm.py`: `complete()` runs `assert_masked(system, user)` right before the HTTP call (after the
  kill-switch and key checks). On a hit it writes an `egress_audit` row marked **blocked** (purpose and
  reason, *not* the body, because the body holds the data we refused to send) and raises `LLMUnavailable`.
  The brief then becomes facts-only with the review flag (F01/F05).
- Tests (`tests/test_masking.py`, +4): names (any letter case) and emails are caught; a properly masked
  payload passes. Negative controls: placeholders only, a name not in the dictionary, and a surname alone are
  not flagged. An unmasked payload is blocked with **zero HTTP calls** and one audit row with no name in
  it; a masked payload passes and reaches the HTTP call. `tests/test_llm_errors.py` now uses an in-memory
  masker and checks that the fake HTTP call was really reached.

**Course concept.** *Defence in depth / guardrail at the boundary*: one independent check at the single
exit point, so a bug upstream cannot leak data.

**How to see it working.** `uv run pytest -q tests/test_masking.py`. Live, with no key, briefs still come
back as facts-only (no regression): the check runs only when a call would really be made.

**What still breaks it / limits.** It knows only names in our dictionary (people and NER terms from ingest)
and email addresses. A phone number, an address, or a name we've never seen is not caught here; the masker's
own rules cover those. It catches full names, not a first or last name alone (to avoid blocking ordinary
words). Because it is case-insensitive while the masker is not, an all-caps name in an email ("JAMIE
TUCKER") is blocked here (fail closed: facts-only brief) rather than masked. The Batch API extraction
path (`batch_client`) does not go through `complete()`, so it is not checked here.

---

## F11: Masking proven end to end on the real payloads

**What was wrong.** We had unit tests for the masker on sample text, but no proof on the *actual* prompts the
agent would send for real contacts. With no API key, the egress log is empty, so it could not serve as
evidence either.

**What changed.**
- `tests/test_masking_e2e.py` (new, needs the Docker stack). For **24 real contacts**, chosen from the data:
  warm, quiet, cooling, new, the heaviest histories, shared names, the injection case, a meeting and a
  cancelled meeting. It runs the agent's own steps (resolve → gather → build_context → generate → verify)
  with the model call replaced by a *recorder*. That captures the exact `system + user` text of both model
  calls (**48 payloads**) without calling any model. Every payload is scanned for **every** person full name and
  email in the database (2,840 identifiers) and every organisation name (385). Names, emails and multi-word
  orgs match in any letter case; one-word orgs (surnames such as "Bell") match exact case, otherwise ordinary
  words like "young" would count as leaks. Each payload also has to pass the F12 `assert_masked` check.
- **Negative control:** the same pipeline with masking switched off must find leaks (it finds 435 across 5
  contacts). This proves the scan really can see a leak, so "0 leaks" means something.
- Result, written to `docs/evidence/masking_leak.json`: 24 contacts, 48 payloads, **0 leaks**, 0 blocked by
  F12, negative control 435 leaks.

**Course concept.** *Evaluation with controls*: a test is only evidence if it can fail, so run it on the real
inputs and prove it detects the thing it looks for (a negative control).

**How to see it working.** `make build && make test` (this test runs in Docker), or just this test:
`docker compose run --rm app pytest -q tests/test_masking_e2e.py`, then open `docs/evidence/masking_leak.json`.

**What still breaks it / limits.** It checks people and organisations we *know*. A name that appears only
inside an email body and was never found by ingest-time NER is not in the dictionary, so it is not checked
here (Ash's earlier probe and the NER step cover part of that). There are no non-English names in the
synthetic corpus, so that case is not tested. Phone numbers and street addresses are covered by the masker's
own unit tests, not by this scan. The verify payload is built from a synthetic draft (one claim per cited
message), not a real model draft.
