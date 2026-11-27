# Live demo script: Hatton Relationship Intelligence (DRAFT, finalised after the batch-2 fixes pass evaluation)

Target length about 12 minutes plus questions. UI: http://localhost:8501 (tabs: Pre-meeting brief · Gone quiet · What leaves the region · Graph · Needs partner review). API: http://localhost:8000. Langfuse: project dashboard.

**The model key is live** (spend cap $5). Use real briefs for the golden path; keep `make kill-llm` for the failure demo.

## 0. Before the panel (T−30 min)
```bash
cd repo && make up            # db, mcp, api, ui
curl -s localhost:8000/health # {"ok": true}
make revive-llm && make revive-mcp   # both kill switches OFF
```
Open these tabs in the browser: the UI, the Langfuse dashboard, `docs/evidence/` (eval + load results) and `docs/cost_model.md`.

## 1. The problem (1 min, no screen)
"Six partners, about 1,200 relationships, 40,000 unindexed emails. They lose deals because they forget people. They asked for three things: a graph that stays current, a brief in 30 seconds that says nothing the emails don't support, and an alert when a warm relationship goes quiet. Masked before any AI sees it, never sends anything, under $900 a month."

## 2. The design in one picture (2 min): ADR §2 diagram + §15 patterns map
"Plain code wherever code can do the job, a model only where only a model can, and a check after every model."
- The graph and quiet detection are **pure code**: free, explainable, can't hallucinate.
- The brief is a **LangGraph workflow**, not a free-running agent: resolve → gather (parallel, over read-only MCP) → fit into the token budget → write (Sonnet) → verify (Haiku) → finalize, or fall back.
- **Why not multi-agent?** One shared context and a 30 s limit; multi-agent costs about 15× the tokens.

## 3. Golden path (2 min): tab "Pre-meeting brief"
1. Pick a meeting from tomorrow's list (precomputed → arrives instantly) or type `jamie.tucker@wrightco.example` → **Brief me**.
2. Point at: the header facts (rendered from the graph, not the model), the sections, a **citation on every sentence**, and the removed-claims list.
3. With the model on: a full written brief in about 10–15 s, including our/their commitments with citations. Show its trace in Langfuse (spans, latency, cost; masked data only).

## 4. Gone quiet (1 min): tab "Gone quiet"
"We planted 60 cooled relationships in the synthetic corpus. The system found 55 of them (recall 0.92, precision 0.81 on tonight's rebuild), and 25 of 25 dead deals." Read one reason string aloud (usual gap vs days silent).

## 5. What leaves the region (1 min): tab "What leaves the region"
Show an outbound payload: placeholders such as `PERSON_1a2b…`, no real names. "Every byte that goes to the model is logged here; Langfuse gets masked text and counts only." Mention the leak test (0 of 1,791 names leaked) and its negative control.

## 6. Break it on purpose (3 min): rehearse these, don't improvise
| Demo | Command | What the panel sees |
|---|---|---|
| Kill the AI | `make kill-llm`, then request a brief | A facts-only brief in about 0.1 s, labelled, with a review flag. `make revive-llm` after. |
| Kill a tool | `make kill-mcp`, then request a brief already generated once | The last brief, labelled stale (fix F09). `make revive-mcp` after. |
| Malformed input | `make malformed` | 5 of 5 bad records quarantined with reasons; ingest continues. |
| Hostile email | Brief for `janice.hall@martincapitaladvis.example` | The injection email is **withheld** and named in the review reasons; it never reaches the model. |
| Exceed the context | `make big-context` | The heaviest relationship (615 messages, about 27k tokens, 4× the 6k budget) is fitted into the budget: 8 threads in full, 40 as index lines, 212 omitted, and it says so. |

## 7. Human escalation (1 min): tab "Needs partner review"
Briefs that fell back, had claims removed or withheld an email appear here with the reason. A partner marks one reviewed and adds a note; that decision is logged. "The system informs; a human decides."

## 8. Proof and money (2 min)
- No outbound action: `make test` (7 no-outbound tests, including a read-only database role that refuses writes).
- Eval report (`docs/evidence/brief_eval.md`): 21/35 strict passes, 95.1% of claims grounded, Opus as an independent judge, up from 10/35 and 58%. Load test (`docs/evidence/load_c15.json`): 15 concurrent, 30/30 within 30 s, p95 14.6 s, 0 errors.
- Cost (`docs/cost_model.md`): backfill **$1.09** once (estimated $1.13 before running); **$0.0197** per brief; **$222/month** normal; at **$700/month** a **68% margin**; Hatton breaks even at about **4 first-meeting preps a month**.

## Recovery lines (if something goes wrong live)
- The UI hangs → use the API: `curl -s -X POST localhost:8000/brief -H 'content-type: application/json' -d '{"query":"jamie.tucker@wrightco.example","fresh":true}'`
- A stale container → `docker compose restart api`
- Never say "it worked on my machine". Say what failed, what the system did about it, and which test proves the behaviour.
