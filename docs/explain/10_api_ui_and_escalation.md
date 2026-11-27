# 10 · API, UI and the "Needs partner review" queue

## In one sentence
A FastAPI web service is the single front door to the system: it runs briefs, serves the quiet list and the audit log, and stores every brief. A Streamlit page lets partners use it. Any brief the system is not sure about lands in a "Needs partner review" queue, where a partner approves or rejects it with a note.

## How it works

### Terms
- **API:** a set of web addresses (*endpoints*) that programs call.
- **FastAPI:** a Python library for building APIs.
- **Streamlit:** a Python library that turns a script into a web page.
- **Escalation / human in the loop:** handing a decision to a person when the system is unsure.

### The API (`src/hri/api/app.py`, port 8000)
| Endpoint | What it does |
|---|---|
| `POST /brief` | The main call. Body: a name, email, person ID or meeting ID, plus the partner's email (optional) and `fresh`. Details below. |
| `GET /review` | The review queue: briefs with status `needs_review`, newest first, with reasons (up to 50 by default) |
| `POST /review/{brief_id}` | `{"decision": "approve" or "reject", "note": "...", "reviewer": "..."}`. Sets the brief to `reviewed`/`rejected` and writes a `review_log` row in one transaction. Returns 404 for an unknown brief, 409 if already decided, 422 for a bad decision value. |
| `GET /quiet` | The gone-quiet list (file 04), optionally for one partner |
| `GET /egress` | The outbound audit log: what was sent to Anthropic, masked |
| `GET /stats` | Counts (messages, people, edges, commitments, egress calls and blocks), status totals, system clock, quarantine list |
| `GET /partners` | The six partners |
| `GET /meetings/upcoming` | Next-day meetings, via the MCP server (503 if it is down) |
| `POST /precompute` | Briefs every confirmed meeting in the next N days ahead of time, 4 at once. "Nightly" in the docs, but run by hand (`make precompute`) |
| `GET /health` | Database ping |

**What `POST /brief` does, step by step** (`brief`):
1. **Cache.** If it names a meeting and `fresh` is false, it returns the latest stored *verified* brief for that meeting at once (mode `cached`, 0 ms, review flag passed through).
2. **Run the agent.** Otherwise it starts the agent (`run_brief`, file 07). Blocking database work runs in worker threads, so one slow request cannot freeze the others (F06).
3. **Stale fallback.** If the data service was down (`unavailable`) or the deadline hit before any data arrived (`delayed`), it looks up the person in our own `person` table (exact name or email only). It then serves the **last stored brief of any kind**, preferring a verified one and filtering by partner (fix F09). The mode becomes `stale_cache`, with a warning such as "Showing the last good brief from … (N min old, facts-only brief): live relationship data is unavailable…".
4. **Store.** Verified and facts-only briefs are saved in the `brief` table (`_store`) with mode, latency, cost, trace ID, and **status `needs_review` if the review flag is set**, otherwise `ok`, plus the reasons.

**No action endpoints.** Nothing sends, books or contacts anyone. Review decisions are written only to our own database. A test checks that no endpoint path looks like an action (`tests/test_no_outbound.py: test_api_has_no_action_endpoints`).

### The UI (`src/hri/ui/streamlit_app.py`, port 8501)
- **Sidebar:** pick the partner; see the system clock, message and relationship counts, and the line "Read-only system: it never emails, schedules or contacts anyone."
- **Tab "Pre-meeting brief":**
  - type a name and click **Brief me**, or click one of the next-24-hours meetings;
  - the brief shows: name, title, organisation; status, warmth, last time they engaged; a mode badge ("verified", "prepared earlier", "FACTS ONLY (model down)", "STALE (data service down)"); each section's sentences with their message-ID citations; a folded list of claims the checker removed, with reasons; run details (latency, cost, timings, context statistics);
  - **a red badge "Flagged for partner review: (1) reason…"** when the review flag is set, with the brief number (F08);
  - if the name is ambiguous, a button per candidate.
- **Tab "Needs partner review (N)"** (F08): one card per flagged brief, showing who, mode, time, meeting, and the reasons. Each card has a **Note** box and **Approve** / **Reject** buttons that call `POST /review/{id}`. The card then disappears. It shows up to 50, with "50+" in the title when there are more.
- **Tab "Gone quiet":** a table of partner, contact, organisation, type, peak warmth, current warmth, days silent, usual gap, and unanswered emails since.
- **Tab "What leaves the region":** each logged outbound call, with its system prompt and the masked text sent. The number of blocked egress attempts is also shown.
- **Tab "Graph":** the raw statistics. It is a numbers view, not a drawn network.

### The escalation path, end to end
1. The agent sets `review.needed` with reasons (`brief_graph.py: _review`) when:
   - the fact-check could not run;
   - the brief is facts-only;
   - more than 30% of model claims were removed;
   - a possible prompt-injection email was withheld.
2. The API stores the brief as `needs_review` (`_store`).
3. The partner sees the red badge at once, and the brief in the review tab.
4. The partner approves or rejects it with a note. The `review_log` table records the brief, decision, note, reviewer and time: an **audit trail**.

Schema: `brief.status`, `brief.review_reasons` and `review_log` are in `schema.sql`. On an already-loaded database they come from `graph/migrations/001_review_queue.sql`, because `schema.sql` only runs on a fresh volume.

**Live evidence** (Ash, 27 Nov, model off): the injection contact's brief returned `review.needed: true` with two reasons, facts-only and "1 message(s) withheld … (m039897)", on 3 runs (`work/eval_batch1.md`, F05).

## Why it was built this way
- **One API for everything.** The UI, the evals, the load test and `precompute` all use the same endpoints, so what we test is what partners use.
- **Precompute plus cache** (`ADR.md §7`). Briefs for tomorrow's meetings can be ready before the partner asks, so they open instantly. On-demand briefs have the 25–29 s budget.
- **Stale but labelled beats nothing** (F09). With the data service down, the partner sees the last brief, its age and its kind.
- **An in-app review queue** (`ADR.md §14`). The course requires a human escalation path shown live. We chose an in-app queue over a Langfuse annotation queue because it is visible to the partner and can be demonstrated.
- **Streamlit** (`ADR.md §14`): fast to build, and pull-only and read-only like the planned server-rendered page.
- **Rejected:**
  - A Langfuse annotation queue: hidden from partners.
  - Email or Slack alerts for flagged briefs: these would be *outbound actions*, which the client forbids.
  - A custom React front end: more work for the same read-only behaviour.

## What breaks it
- **No logins.** Anyone who can reach port 8000 or 8501 can read unmasked briefs, approve reviews, or trigger `/precompute`, which spends money on model calls. The "reviewer" is whatever the caller sends, or the partner picked in the sidebar (`ADR.md §16`; F07/F08 limits).
- **The queue fills with repeats.** Every fresh facts-only brief is queued, including test and load-test runs, so the queue can hold many duplicates. F08 notes said more than 100 on 27 Nov. Clear old items before the demo.
- **Caching quirks.**
  - The meeting cache ignores which partner asks and how old the brief is (`_cached` without `partner_email` on the normal path).
  - The stale path finds people only by exact name or email.
  - A brief that has never been made has nothing to fall back to.
- **The UI has a few gaps.**
  - Claim text is shown with HTML allowed (`unsafe_allow_html=True` in `render`). Text that came from an email could inject HTML into the page.
  - The review tab count is only read on page load.
  - API errors show raw HTTP text.
- **Health check doesn't cover the tools.** `/health` checks only the database, so it says "ok" while the MCP server is down (Ash baseline, 27 Nov).
- **Precompute is not scheduled.** It runs only when someone calls it.

## Agentic pattern / course concept it shows
*Human in the loop / escalation with an audit trail*, *graceful degradation* (cache, stale cache), *routing* (cached vs live), and *no-outbound-action by design*.

## 3 likely panel questions
1. **"Show me your human escalation path."**
   Any brief that couldn't be fully verified is flagged and gets a red badge: it fell back to facts-only, too many claims were removed, or a suspicious email was withheld. It also lands in the "Needs partner review" tab. The partner reads the reasons, adds a note, and approves or rejects it. Each decision is logged with the time in `review_log`. Nothing is sent to anyone.
2. **"What does a partner see if the data service is down?"**
   The last stored brief for that person, preferably a verified one, labelled "STALE", with its date, age and kind. The warning says live data is unavailable. If no brief was ever made for that person, they are told it is unavailable rather than shown something wrong.
3. **"How do briefs arrive in under 30 seconds in the morning?"**
   `POST /precompute` prepares briefs for tomorrow's confirmed meetings in advance. Opening one by meeting returns the stored brief instantly. A new on-demand brief runs within the 25 s budget, or degrades to facts-only. In production, precompute would run every night; today it is triggered by hand.
