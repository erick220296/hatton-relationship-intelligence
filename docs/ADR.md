# Architecture Decision Record: Hatton Relationship Intelligence

Group 05 · Owner: Erick Lopez (Technical Lead / Agent Architect) · Status: living document

## 0. Scope, as cut by the sponsor
In scope:
- a relationship graph built from the **full** 40k backfill and kept current
- a pre-meeting brief
- quiet-relationship detection

Out of scope:
- pipeline/deal-stage tracking
- natural-language querying of the CRM
- a second vertical (proved by configuration in §11, not built)
- any outbound action

We deliberately shipped **two features, finished** (brief + quiet detection on one graph) rather than the six in our proposal.

## 1. Constraints → design drivers
| Constraint | Driver |
|---|---|
| Unmasked data stays in region; it is masked before any third-party model sees it | Masking + pseudonym vault + a single audited egress door (§5) |
| A brief that states something unsaid is worse than none | Citations on every claim, a deterministic gate, an LLM verifier, a facts-only fallback (§6) |
| A brief within 30 s ("we ask in the car") | Precompute overnight, on-demand path budgeted to under 30 s, hard timeouts (§7) |
| $900/month; backfill priced separately and before it runs | Deterministic graph, no LLM per message, Batch API, `estimate` step (§8) |
| Informs only; no outbound action | Read-only tools, a SELECT-only DB role, no action endpoints, a host allowlist, tests (§9) |

## 2. System overview
```
mail/calendar ─► parse+quarantine ─► Postgres graph (person, org, message, meeting, edge, commitment)
                                         │  local embeddings (pgvector)      ▲
                                         │                                  │ commitments (Batch API,
                                         ▼                                  │ masked → Claude → unmasked)
                                  warmth / quiet (deterministic)            │
                                         │
Partner ─► API ─► LangGraph brief agent ─► MCP server (read-only, role hri_ro) ─► Postgres
                     │ mask → egress door (allowlist + audit) ─► Claude ─► verify ─► unmask
                     └► Langfuse (masked payloads only)
```

## 3. Decisions

### D1. Graph store: Postgres + pgvector (chosen) vs Neo4j / a separate vector DB (rejected)
At ~1,500 people and ~1,600 edges, graph queries are one or two hops: who knows X, and what's the history between partner P and X. Relational joins answer these in milliseconds. One store means one backup, one access-control model (which we rely on for the read-only role) and one container. **Rejected:**
- **Neo4j:** a second datastore to secure in-region, with no query we need that SQL can't do.
- **Pinecone/Weaviate:** sends data out of the region, and is extra ops.

**Revisit if** multi-hop warm-path queries ("who can introduce us to X") come back into scope.

### D2. Graph built deterministically from headers (chosen) vs LLM entity extraction per message (rejected)
Who emailed whom and when, plus calendar attendees, is **exact** metadata. Using an LLM for it would add cost (40k calls), latency and hallucination risk to the one thing that must be correct. Organisation membership comes from email domains and the partners' address books. The LLM is used only where language understanding is needed: **commitments and thread topics**.

### D3. Commitment extraction: per thread, Claude Haiku via the Message Batches API, on masked text
- **Per thread, not per message:** commitments only make sense in conversation context, and this means about 3× fewer calls.
- **Batch API:** 50% cheaper, and the backfill isn't latency-sensitive.
- **Ongoing mail:** a synchronous call per updated thread.
- **Before running:** `backfill estimate` prices it (D-cost).
- **Guard:** commitments citing a message ID that isn't in the thread are discarded.

### D4. Embeddings: local `bge-small-en-v1.5` (chosen) vs OpenAI `text-embedding-3-small` (rejected)
Running locally keeps raw text in the region with no masking needed. It has zero API cost (the 40k backfill embeds in minutes on CPU) and no outage dependency. Retrieval is a secondary signal here (recency + graph do most of the work), so a small model is enough. The OpenAI alternative is priced in the cost model for comparison.

### D5. Warmth and quiet: deterministic, explainable, configurable (chosen) vs LLM judgement (rejected)
Partners need to trust and tune "gone quiet". The score is decayed reciprocal interaction. Quiet is judged against each relationship's own rhythm, and only for established relationships. Every flag ships with its explanation (days silent, usual gap, unanswered outreach). All parameters live in `pe.yaml`.

An LLM judgement would be non-reproducible, costly at 1,600 edges a day, and impossible to explain to a partner who disagrees. Tuning was done against the corpus ground truth; see the eval report for precision/recall and the misses.

### D6. Orchestration: LangGraph state machine with deterministic gates (chosen) vs a free-running tool-calling agent (rejected)
A brief is a known procedure: resolve → gather → budget → generate → verify → finalize. Letting a model choose tool calls freely would add latency variance and a larger injection surface, and buy nothing.

Patterns used:
- **Prompt chaining** with code gates between steps.
- **Tool use via MCP.**
- **Reflection:** the verifier, plus one bounded regenerate with its feedback, gated on time left.
- **Graceful degradation:** a facts-only fallback, and a stale-cache fallback when data tools are down.
- **Human-in-the-loop on ambiguity:** same-name contacts make the agent return candidates rather than guess.
- **Human escalation:** any brief that abstained, fell back, or had claims removed goes to a "Needs partner review" queue (§15).

The full map of patterns to code is in §15.

### D7. MCP for data access
- **Read-only:** every tool is annotated `readOnlyHint` and connects as Postgres role `hri_ro`, which has SELECT only and no access to the pseudonym vault or the egress log.
- **Swappable data layer:** the agent never touches SQL. Swapping the mail provider (Outlook ↔ Gmail) changes the ingest adapter and the store, not the agent.
- **Separate service:** the MCP server runs as its own container, so "kill a tool" is real (`make kill-mcp`).

### D8. Memory
- **Working memory:** LangGraph state for one brief run, checkpointed to Postgres (`AsyncPostgresSaver`), so every run's state is recorded and auditable by `run_id` (a replay command is not built).
- **Long-term memory:** the graph itself (edges, commitments, thread topics) plus stored briefs. Each brief is also the fallback when tools fail.
- **Context engineering:** a 6k-token budget with explicit priority: profile → commitments → recent threads in full → older threads as index lines → semantic hits. Overflow degrades items to index lines and then drops them, and the brief reports what was compressed. This is the "exceed the context" demo: on the current database the heaviest relationship has 615 messages in 260 threads, about 27,000 tokens raw, more than 4× the 6,000-token budget. The brief keeps 8 threads in full, 40 as index lines, and omits 212, and says so.

### D9. Models
| Use | Model | Why |
|---|---|---|
| Commitment extraction | `claude-haiku-5-5` (Batch) | High volume, structured output, cheapest tier |
| Brief generation | `claude-sonnet-5-5`, effort low | Faithfulness to sources and terse writing; fits the 30 s budget |
| In-pipeline verifier | `claude-haiku-5-5` | Fast claim-by-claim check |
| Eval judge (offline) | `claude-sonnet-5-5` | A different model and prompt from the *verifier*. **Limitation:** it is the same model as the *writer*, so a panel can fairly raise self-preference bias. The next eval run should use a different model for the judge (e.g. Opus 5.5); see §16. |

Structured outputs (JSON schema) are used everywhere, so the gates parse reliably. Model IDs and prices live in config (`prices.yaml`, with source and date).

## 5. Confidentiality design (DoD #4)
1. **Raw mail stays in the containers:** Postgres, the MCP server, the API. Embeddings are computed locally.
2. **Masking before any third-party call:**
   - Regex for emails, phone numbers, URLs and UK-postcode address lines.
   - A dictionary of every known person (full, first and last name), organisation and deal codename (`Project X`).
   - A Presidio/spaCy NER sweep for unknown names and places.
   - Tokens are HMAC-derived (`PERSON_1a2b3c`), so they are stable across messages and the model can still reason about who said what.
3. **The vault:** the `pseudonym` table holds token → real. It is unreadable by the tool role and never leaves the database. Output is unmasked in-region.
4. **One door out:** `privacy/egress.py` is the only HTTP client that can leave. A host allowlist is enforced before any bytes are sent, and every request and response body is written to `egress_audit`. The UI tab "What leaves the region" shows these exactly.
5. **Langfuse** receives only the masked strings that were sent to the model, plus IDs and counts. *(Corrected 27 Nov: before fix F02 the `@observe` decorators captured every node's raw input and output by default, which would have sent unmasked mail to Langfuse once keys were set. All decorators now set `capture_input=False, capture_output=False`, and a test scans `src/` to keep it that way.)*
6. **Production note:** for full residency we would point the SDK at an in-region model endpoint (e.g. Claude on a cloud provider's UK/EU region). It is a client-construction change, recorded as a follow-up.

## 6. Accuracy design (DoD #1)
1. Every claim must cite message IDs from its context, or `PROFILE`.
2. **Deterministic gate:** uncited claims, or claims citing IDs not in the context, are deleted.
3. **Verifier (Haiku):** checks each claim against the cited text and deletes unsupported ones. If more than 30% are dropped and time allows, there is one regenerate with feedback.
4. **Header facts are not LLM-written:** name, title, organisation, status, dates and counts are rendered from the graph.
5. **Prompt-injection filter:** emails containing instructions aimed at AI systems are withheld from the context and flagged to the partner.
6. **The partner sees the evidence:** removed claims are listed, and every claim's citations are shown.

## 7. Latency design
- **Overnight:** `POST /precompute` prepares briefs for tomorrow's meetings. A request by `meeting_id` returns the cached brief in ~0 s.
- **On demand:** a 25 s internal budget. If the verifier can't run in time, the system now **fails closed**: model-written claims are removed and the brief is flagged for review (fix F05); it never returns unchecked claims. If the deadline is close, it degrades to verified or facts-only content (F06). **Latency evidence status (27 Nov):** the degraded path is measured (6 concurrent × 30 requests, 0 errors, max ~0.6 s, `work/eval_batch1.md`); the real-model p95/p99 under load and the Langfuse latency chart need a funded model key and are still open (§16).

## 8. Cost design
Detail is in `docs/cost_model.md`, which is built on measured Langfuse/egress token counts.
- The backfill is a separate one-off line: extraction via Batch, and embeddings at $0 API cost.
- The running cost per brief is generation + verification, measured.
- No per-message LLM call for graph building. That decision alone is the difference between affordable and not.

## 9. No outbound action (DoD #5)
Proved by `tests/test_no_outbound.py`:
- no mail/calendar/messaging libraries
- no send-like calls
- HTTP clients only in the egress module
- every MCP tool read-only
- no action-like API endpoints
- egress to SMTP/Graph hosts blocked
- the tool DB role cannot INSERT, UPDATE or DELETE, nor read the vault

## 10. Failure behaviour (pitch demo)
| Failure | Behaviour |
|---|---|
| LLM down (`make kill-llm`) | Facts-only brief from the graph, clearly labelled. Extraction pauses and catches up later. |
| MCP/data tool down (`make kill-mcp`) | The last good brief is served, labelled stale, with a warning. |
| Malformed input (`make malformed`) | The record is quarantined with a reason; the batch continues. |
| Context exceeded (`make big-context`) | The budgeter compresses and drops by priority, and reports what it did. |
| Ambiguous name | Candidates are returned; the partner chooses. |
| Any model error, including a missing or invalid key (fix F01) | Treated as "model unavailable": facts-only brief, HTTP 200, never a 500. |
| Verifier down, malformed or skipped for time (fix F05) | Fails **closed**: model-written claims are dropped, not kept, and the brief is flagged `needs_review` with the reason. |
| 30 s deadline approaching (fix F06) | Degrades to verified or facts-only content built from the data already gathered; never an HTTP 504. |
| Brief abstained, fell back or was flagged | Lands in the **"Needs partner review"** queue, the human escalation path (fixes F07/F08). |

## 11. Second vertical: configuration claim
`config/verticals/agency.yaml` changes the entity labels, warmth half-life and weights, quiet thresholds and brief sections. The engine reads only the vertical file for domain behaviour. Pointing `VERTICAL=agency` re-scores the graph with no code change. Not built or evaluated, per the sponsor.

## 12. Assumptions (pending client answers; see `docs/client_questions.md`)
- All six mailboxes are in scope; 2-year backfill; internal mail and newsletters excluded.
- Warmth and quiet definitions as configured in `pe.yaml`.
- Brief content as listed in the six sections; ≤300 words; no suggested questions.
- The region is UK/EU; a masked-only model API is acceptable for the demo.
- Peak = 15 concurrent brief requests (morning burst); tested at 3×.

## 13. Known limitations
- The synthetic corpus is template-rendered, so real mail will be messier (forwards, long chains, attachments). Commitment extraction is the component most exposed to this.
- Quiet detection misses relationships that cooled very recently relative to a slow cadence (see the eval's misses list).
- First-name pseudonyms are per name string, not per person, so two contacts named "James" share a NAME token in masked text. The full-name token disambiguates them.

## 14. Design review: our v2.2 architecture vs the build (decisions taken 27 Nov)
Before the build, the team wrote a fuller target architecture (v2.2). Where the code differs, we compared the two and chose deliberately. Rejected options are recorded here because "did you choose, or did you accumulate?" is the question.

| Topic | v2.2 design said | Build does | Decision and why |
|---|---|---|---|
| Topology | Deterministic workflow, router → parallel specialists → judge; *rejected* the proposal's five-agent supervisor | Same: one LangGraph state machine, parallel data gathering, verifier | **Agree.** Multi-agent systems use ~15× the tokens of a single chat (Anthropic, "How we built our multi-agent research system") and add handoff failure points; a brief has one shared context and a 30 s limit. |
| Database | Supabase (hosted Postgres + pgvector) | Postgres + pgvector in Docker | **Build wins for the demo:** same SQL and pgvector, one fewer external processor holding raw PII, reproducible offline. Supabase (or self-hosted Supabase) stays the production-hosting option; it is a connection-string change. |
| Model gateway | OpenRouter (student build), direct in-region endpoint in production | Direct Anthropic SDK behind one egress door | **Build wins:** no middle party between us and the model (stronger confidentiality story), and the Batch API halves the backfill cost. OpenRouter rejected: an extra processor, and no batch pricing. |
| Delivery surface | Read-only server-rendered web page | Streamlit UI + FastAPI | **Build wins for speed:** same pull-only, read-only behaviour, with less to build. Neither sends anything. |
| Multi-attendee meetings | `Send` fan-out, one branch per external attendee | Briefs the first external attendee | **Deferred:** the golden path is one external attendee. Recorded as a known limitation. |
| Retrieval | Hybrid keyword + vector + reranker | Entity-first (graph) + recent threads + vector hits, under a token budget | **Build is enough:** retrieval is filtered to the person first, so the candidate set is small; a reranker adds latency for little gain at this size. |
| PII safety net | Presidio over the dictionary masker | Dictionary masker + regex + spaCy NER sweep, HMAC tokens | **Equivalent:** both put a dictionary first and NER as the net. |
| Incremental sync | Cursor-based job every ~15 min | `make live` replays post-backfill mail through the same ingest (idempotent) | **Partly:** the ingest path is the same; a scheduler is a production item. |
| Deployment | Terraform + ECR + EC2 via SSM + GitHub OIDC | Docker Compose, reproducible from a clean machine | **Deferred to production:** the course requires "containerised, reproducible", which Compose meets. The AWS path is the production plan, not demo scope. |
| Escalation | Langfuse annotation queue | In-app "Needs partner review" queue | **Build (after fixes F07/F08) wins:** it can be shown live, which the rubric requires. |

## 15. Agentic patterns map (where each pattern lives, and why)
| Pattern | Where in the code | Why here |
|---|---|---|
| **Workflow, not autonomous agent** (Anthropic, "Building effective agents") | `agent/brief_graph.py` `build_graph()`: fixed nodes resolve → gather → build_context → generate → verify → finalize/fallback | A brief is a known procedure. Fixed paths give predictable latency and a small injection surface. |
| **Prompt chaining with code gates** | Code checks between generate and verify (citation IDs must exist in context; uncited claims deleted) | Cheap, deterministic checks catch what they can before a model judges the rest. |
| **Routing** | Conditional edges `_route()` / `_after_verify()`; cached (precomputed) vs live path in `api/app.py` | Each request takes the cheapest path that is still safe. |
| **Parallelisation (sectioning)** | `gather`: `asyncio.gather(profile, threads, commitments, index)` over MCP | Independent lookups run at the same time to protect the 30 s budget. |
| **Evaluator–optimizer (reflection), bounded** | `verify` (Haiku) checks each claim; at most one regenerate with feedback, only if time allows | Accuracy without unbounded loops. |
| **Guardrails** | Input: injection filter withholds instruction-like emails. Egress: masker + `privacy/egress.py` allowlist + audit log. Output: unmask in-region only. Verifier fails closed (F05). | The client's two hardest rules: nothing unsupported, nothing unmasked leaves. |
| **Tool use via MCP, least privilege** | `mcp_server/server.py`, read-only tools on the SELECT-only `hri_ro` role | One swappable, provably read-only data boundary; "kill a tool" is a real separate service. |
| **Context engineering / budgeting** | `agent/context_budget.py`: fixed token budget with priority order, degrade to index lines, then drop, and report what was trimmed | Cost and focus degrade long before the 1M window; this is the "exceed the context" demo. |
| **Memory** | Working: LangGraph state + checkpointer per `run_id`. Long-term: the graph, commitments, topics and stored briefs. Procedural: `config/verticals/pe.yaml` (warmth rules, brief sections) | Course standard: working context plus a long-term store. |
| **Graceful degradation** | Facts-only fallback (model down), stale cache (tools down), deadline degrade (F06) | The system always answers with something true, or says it can't. |
| **Human-in-the-loop** | Ambiguous names return candidates; "Needs partner review" queue (F07/F08) | The client must stay in authority; humans review what the system couldn't verify. |

## 16. Open items (honest status, 27 Nov)
- **Needs a funded model key** (not yet available): commitment extraction on the rebuilt database (~$1), the brief-eval re-run on the fixed code (the last run, 25 Nov, passed 10/35 cases with 58% of claims grounded, partly due to an eval bug fixed since), and the load test with real model calls (p95/p99 from Langfuse).
- **The eval judge** should move to a model different from the writer for the re-run.
- **Masker:** the dictionary loads once per API process, so names added by live ingest are masked only after a restart (runbook), and very short organisation names are skipped. Planned fix: reload after ingest, case-insensitive matching.
- **Exposure:** ports and default passwords are fine on a laptop but must sit behind authentication before any public deployment.
