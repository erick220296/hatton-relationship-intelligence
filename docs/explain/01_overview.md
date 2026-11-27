# 01 · The whole system on one page

> Written for the panel. Facts come from the code on branch `pitch-fixes` (fixes F01–F09), dated 27 Nov 2026. File paths are relative to `repo/`.

## In one sentence
The system reads Hatton's email and calendar and turns them into a map of who knows whom and how well. From that map it writes short pre-meeting briefs in which every sentence is cited, and it lists warm relationships that have gone quiet. It never sends anything, and it masks names before any outside AI sees the text.

## How it works

### The three flows
1. **Ingest (fill the map).** Emails and meetings are read, checked and stored in a database. Each email is turned into numbers for similarity search, called an *embedding*; this runs on our own machine. A score is then computed for every partner–contact pair.
   Code: `src/hri/ingest/`, `src/hri/graph/warmth.py`. Details: files 03 and 04.
2. **Brief (answer "who am I meeting?").** A fixed, step-by-step AI pipeline gathers the facts, masks them, asks Claude to write the brief, and has a second model fact-check every sentence. It then puts the real names back.
   Code: `src/hri/agent/brief_graph.py`. Details: files 07 and 08.
3. **Quiet list (warn about fading relationships).** A plain formula, with no AI involved, flags relationships that used to be warm and have gone silent for longer than their usual rhythm.
   Code: `src/hri/graph/warmth.py: score_edge`, shown by `GET /quiet`. Details: file 04.

### The pieces (Docker containers)

*Docker* runs each part in its own sealed box, called a *container*. `docker-compose.yml` starts four boxes:

```
                 Partner's browser
                        │
                 ┌──────▼──────┐
                 │  UI         │  Streamlit web page (port 8501)
                 └──────┬──────┘
                        │ HTTP
                 ┌──────▼──────┐        masked text only       ┌────────────────────┐
                 │  API        │ ─────────────────────────────►│ Anthropic (Claude) │
                 │  FastAPI +  │   via the one "egress door"   └────────────────────┘
                 │  brief agent│ ─ ─ ─ masked text + counts ─ ─►  Langfuse (tracing; off now: no keys)
                 └──┬───────┬──┘
     tool calls     │       │ reads/writes briefs, vault, audit log
     (MCP, read-only)│      │
                 ┌──▼───┐   │
                 │ MCP  │   │
                 │server│   │
                 └──┬───┘   │
          SELECT-only role  │
                 ┌──▼───────▼──┐
                 │ Postgres +  │  email, people, meetings, scores, briefs,
                 │ pgvector    │  pseudonym vault, egress audit log
                 └─────────────┘
```

### The journey of one brief, from click to screen
1. In the UI (`src/hri/ui/streamlit_app.py`) the partner types a name and clicks **Brief me**. The UI sends `POST /brief {"query": "...", "partner_email": "..."}` to the API.
2. The API (`src/hri/api/app.py: brief`) gets the request.
   - If it names a meeting already prepared overnight, it returns that stored brief at once (mode `cached`).
   - Otherwise it starts the brief agent (`brief_graph.py: run_brief`) with a 25-second working budget and a hard stop at 29 s.
3. **resolve.** The agent asks the read-only MCP server which person this is (`find_person`). If two people share the name, it stops and asks the partner to choose. It does not guess.
4. **gather.** Four lookups run at the same time: profile and relationship scores, recent email threads, recorded commitments, and an index of older threads. When the request is for a meeting, a semantic search is added.
5. **build_context.**
   - Emails that look like instructions to an AI (a *prompt injection*) are withheld.
   - The history is cut to fit a 6,000-token budget. A *token* is roughly ¾ of a word.
   - Every name, email address and phone number is replaced by a code such as `PERSON_1a2b3c`.
6. **generate.** Claude Sonnet writes a JSON brief in six sections. Every sentence must cite a message ID.
7. **verify.**
   - Code deletes any sentence whose citation was not in the context.
   - Claude Haiku then checks each remaining sentence against its cited source and deletes unsupported ones.
   - If more than 30% were deleted and time allows, the agent rewrites once.
   - If the checker cannot run, all model text is removed. This is "fail closed".
8. **finalize.** The codes are turned back into real names, inside our own system. The header (name, title, status, dates, counts) is written by code, not by the model.
9. The API stores the brief. If the brief is doubtful, it marks it `needs_review`. Either way it returns the brief to the UI, which shows the sections, the citations and any removed sentences. A red badge appears if the brief needs partner review.

If anything fails on the way, the system still answers:
- **Model down or too slow:** a *facts-only brief*, written by code.
- **Data service down:** the last stored brief, labelled stale.
- **Name ambiguous:** a list of candidates to choose from.

## Why it was built this way
- **A fixed workflow, not a free-roaming agent** (`ADR.md §3 D6, §15`). A brief is a known procedure. A fixed path gives predictable timing and fewer ways for a hostile email to steer the AI.
  - *Rejected:* a five-agent "supervisor" design. Multi-agent systems use about 15× more tokens (Anthropic research cited in `ADR.md §14`) and add hand-off failures.
- **Postgres for everything** (`ADR.md D1`): the graph, the vectors, the vault and the audit log sit in one database that stays in-region.
  - *Rejected:* Neo4j or a hosted vector database. Each is one more place holding raw personal data.
- **Deterministic scores** for warmth and quiet (`ADR.md D5`). They are explainable and cost nothing to run.
  - *Rejected:* asking an AI to judge relationships.
- **Masking plus one exit door** (`ADR.md §5`). The client's hardest rule is "PII masked before any third-party model".

## What breaks it
- **No model key yet.** As of 27 Nov there is no funded key, so every brief is facts-only (`ADR.md §16`). Commitment extraction has not been run on the rebuilt database, so the commitment sections are empty (Ash's baseline eval, 27 Nov: `commitments 0`).
- **Brief quality is unproven on the fixed code.** The last real-model eval (25 Nov, before the fixes) passed 10 of 35 cases. The independent judge found 58% of claims grounded (`docs/evidence/brief_eval.md`).
- **No logins, open ports.** There is no login, and the ports use default passwords. That is fine on a laptop, but not for deployment (`ADR.md §16`).
- **The masking list can go stale.** New names from live mail are only masked after an API restart (`ADR.md §16`; `masker.py: masker`).

## Agentic pattern / course concept it shows
- Workflow with routing
- Parallel tool calls
- Prompt chaining with code gates
- Evaluator–optimizer (verifier plus one retry)
- Guardrails
- Context engineering
- Memory
- Graceful degradation
- Human in the loop

The full map is in `ADR.md §15`.

## 3 likely panel questions
1. **"Is this an agent or just a pipeline?"**
   It is a *workflow*: a fixed graph of steps built with LangGraph. Code decides the path, and the model writes and checks text inside it. We chose that on purpose. A brief is a known procedure with a 30-second limit, and a fixed path is faster, cheaper and harder for a hostile email to hijack. The agent-like parts are the conditional routing, the self-check with one retry, and asking the human when a name is ambiguous.
2. **"Where does the data go?"**
   Raw email stays in our containers and database. Only masked text, with codes instead of names, goes to Anthropic. Every one of those calls is logged in `egress_audit` and shown in the UI tab "What leaves the region". If tracing keys are added, masked text and counts also go to Langfuse.
3. **"What happens if Claude is down?"**
   The partner still gets a brief. It is built from the database by code, labelled "facts only", and flagged for partner review. There is never an error page for a model failure (fix F01; `llm.py: complete`).
