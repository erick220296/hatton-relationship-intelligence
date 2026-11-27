# 08 · Context budget and memory

## In one sentence
The agent never pours a whole relationship history into the model. It fills a fixed 6,000-token budget in priority order and reports what it shrank or left out. Memory has two layers: a per-request notebook that is saved after every step, and the long-term graph plus stored briefs.

## How it works

### Part A: the context budget (`src/hri/agent/context_budget.py: build_context`)
**Terms.**
- **Context:** the text given to the model with the question.
- **Token:** a chunk of text, about ¾ of a word. The code estimates tokens as characters ÷ 4 (`est_tokens`).
- **Budget:** `context_budget_tokens = 6000` (`config/settings.py`).

**Priority order**: each block is added only if it still fits.
1. **Profile and relationships.** Name, title, organisation, and each partner's status, warmth, peak, first and last contact, last time *they* engaged, email and meeting counts. Always first.
2. **Commitments.** Up to 15, each with its message ID.
3. **Recent threads, newest first.** The last 6 messages of each thread, bodies trimmed to 1,200 characters and signatures removed (`trim_body`). A thread that does not fit in full is **shrunk to one index line** (date, subject, message count, last message ID). If even that line does not fit, it is **dropped**.
4. **Older threads as index lines.** At most 40 (`MAX_INDEX_LINES`). The rest are counted as dropped.
5. **Semantic-search hits** related to the meeting title, 600 characters each.

Every message ID that makes it into the context is remembered. Those are the only IDs a claim may cite (code gate, file 07). The function returns **statistics**: threads in full, as index lines, dropped, commitments, search hits, tokens used. If threads were dropped or more than 20 were index-only, the brief shows a "Long history" warning (`brief_graph.py: build`).

**What `make big-context` shows** (`evals/big_context.py`). It briefs the deliberately heavy banker relationship and prints the raw size against what was kept. Ash's run on the current database (27 Nov, 3 identical runs, `work/eval_batch1.md`): **615 messages, about 27,170 tokens of raw history → 8 threads in full, 40 as index lines, 212 omitted, 1,741 tokens used of 6,000.** Since fix F01 this works even without a model key, because the facts-only brief carries the statistics.

> Note: `ADR.md D8` describes the heaviest relationship as "667 messages, about 70k tokens raw". That was the original database. The current one measures 615 messages and about 27k tokens.

### Part B: memory
| Kind | What it is here | Where |
|---|---|---|
| **Working memory** (short-term) | The `BriefState` notebook for one request: request, raw data, masked context, evidence, draft, claims, warnings, timings. | `brief_graph.py: BriefState` |
| **Checkpoints** (working memory saved) | After every step the state is saved to Postgres by LangGraph's *checkpointer*, keyed by a fresh `run_id`. | `api/app.py: lifespan` sets up `AsyncPostgresSaver`; `_run_brief` passes `thread_id = run_id` and streams the state after every step |
| **Long-term memory** | The relationship graph (people, organisations, edges with warmth and explanations), extracted commitments, thread topics, and every stored brief. | `schema.sql` tables `person`, `org`, `edge`, `commitment`, `thread_summary`, `brief` |
| **Briefs as memory for failures** | Stored briefs are reused: the overnight cache (`mode cached`) and the "data service down" fallback (`stale_cache`). | `api/app.py: _cached`, `brief` |
| **Feedback memory** | Partner approve/reject decisions with notes. | `review_log` table (F07) |
| **Procedural memory** | How to score warmth and which brief sections to write, held as configuration. | `config/verticals/pe.yaml` |

Why the step-by-step state matters: since fix F06 the agent keeps the **last saved state** in memory while it runs. If the hard deadline fires, `_at_deadline` builds the facts-only brief from whatever was already gathered, instead of failing.

## Why it was built this way
- **A small budget on purpose.** A 6k budget is far below the model's maximum window. Smaller context means faster answers, lower cost, and better focus (models miss details buried in huge inputs). It also guarantees the 30-second target. `ADR.md §15` says "cost and focus degrade long before the 1M window".
- **Priority order** follows what a partner needs before a meeting: who, our standing, open promises, the latest conversations, then background.
- **Shrink before drop.** An index line keeps the fact that a thread existed, and keeps it citable, at a fraction of the cost.
- **Report what was trimmed.** It is honest, and it makes the "exceed the context" demo visible.
- **Memory = graph + stored briefs.** The graph already *is* long-term relationship memory, built deterministically.
- **Rejected:**
  - Dumping full history into a large window: slow, costly, unfocused.
  - Summarising the history with AI first: an extra model call, extra latency, and summaries can hallucinate.
  - A separate vector-only memory: retrieval is filtered by person through the graph first, so a heavier memory system adds little (`ADR.md §14`, Retrieval row).

## What breaks it
- **Rough token counting.** The estimate (characters ÷ 4) is approximate. Real token counts can differ, especially for non-English text.
- **Long single threads lose detail.** A thread shows only its last 6 messages, so an early commitment in a long thread may only appear through the commitments list. Today that list is empty (extraction has not been run on this database, file 03).
- **Checkpoints are saved but never replayed.** There is no endpoint or tool to re-open a run. They grow forever, with no clean-up, and they hold raw (unmasked) state in our database. That is in-region, but not minimal.
- **No personal memory.** The system does not remember a partner's preferences or past questions. `review_log` is the only feedback memory, and nothing yet learns from it.
- **Stale long-term memory.** Warmth statuses are only re-scored for pairs with new mail (file 04). Stored briefs can be days old; the stale label says so.

## Agentic pattern / course concept it shows
*Context engineering* (budgeting, prioritising, compressing, reporting), *working vs long-term memory*, and *checkpointing for resilience and audit*.

## 3 likely panel questions
1. **"What happens with a huge relationship history?"**
   The budgeter fills 6,000 tokens by priority. Recent threads go in full, older ones become one-line index entries, and the rest are counted as omitted. The brief says what it did. For our heaviest contact (615 messages, about 27k tokens) it kept 8 threads in full and 40 as index lines, and omitted 212, using 1,741 tokens.
2. **"Where is the memory in your agent?"**
   Working memory is the LangGraph state for one request, saved to Postgres after every step by the checkpointer. Long-term memory is the relationship graph, commitments and stored briefs. Stored briefs also serve as the fallback when the data service is down.
3. **"Why not just use the model's full context window?"**
   Speed, cost and accuracy. A 6k budget keeps us inside 30 seconds and a few cents per brief, and models are more accurate with less, more relevant text. We report exactly what was trimmed, so nothing is silently lost.
