# Project Guide: Hatton Relationship Intelligence

A plain-English guide for anyone new to this project: what it is, how it works, and how to run it.
Group 05 · AAI C01 capstone · Buildr Labs Agentic AI Bootcamp

---

## 1. What problem does this solve?

**The client** is Hatton Capital Partners, a (fictional) private-equity firm with six partners. They find deals through people they know: bankers, founders, lawyers and investors.

**The pain:** that knowledge is spread across six people's heads and about 40,000 emails. Things get missed:
- A banker who used to send deals goes quiet, and nobody notices.
- Before a meeting, a partner spends 30–45 minutes searching their inbox to remember who the person is.

**What we built** reads the partners' email and calendars and answers two questions:

| Question | Feature |
|---|---|
| "Brief me on this person before my meeting." | **Pre-meeting brief:** who they are, our history, what was last discussed, what we promised, what they promised. Ready in under 30 seconds, and every sentence links to the email it came from. |
| "Which good relationships have we let go cold?" | **Gone-quiet detection:** flags contacts who used to be warm but have gone silent much longer than their usual rhythm, and says why. |

**What it will never do:** send emails, book meetings or contact anyone. It only *informs*.

> Real partner email can't be used, so the project ships with a **synthetic corpus generator**. It creates about 40,000 realistic fake emails and calendar entries over two years. Because we planted the "cooled" relationships ourselves, we can check whether the system finds them.

---

## 2. The client's hard rules (and how we meet each one)

| Rule | What we did |
|---|---|
| **Budget:** under $900/month; price the backfill before running it | No AI call per email. The graph is built from email headers (free). AI is only used for commitments (batched, 50% cheaper) and for briefs (about $0.02 each). `make backfill` prints the cost **before** you pay it. |
| **Confidentiality:** data stays in the region and is masked before any outside AI sees it | Real names, emails, phone numbers, companies and deal names are swapped for tokens like `PERSON_1a2b3c` before anything goes to Claude. There is one "exit door" in the code, and every request through it is logged. The UI has a tab showing exactly what left. |
| **Accuracy:** a wrong brief is worse than none | Every claim must cite an email. Uncited claims are deleted. A second AI checks each claim against its source and removes unsupported ones. Name, title and dates come from the database, not from the AI. |
| **Latency:** a brief within 30 s ("we ask in the car") | Briefs for tomorrow's meetings are prepared overnight (instant). On-demand briefs have a 25 s internal time budget. |
| **No outbound action** | Read-only database user, no email libraries, and tests that prove it (`tests/test_no_outbound.py`). |

---

## 3. How it works

```
 ┌──────────────┐   ┌───────────────┐   ┌──────────────────────────────────────┐
 │ Emails +     │──►│ Parse & check │──►│ PostgreSQL database (the "graph")    │
 │ calendar     │   │ (bad records  │   │  people · organisations · messages   │
 └──────────────┘   │  quarantined) │   │  meetings · relationships · promises │
                    └───────────────┘   └──────────────────────────────────────┘
                                             │            ▲
                            local embeddings │            │ commitments extracted by Claude
                            (no API, free)   ▼            │ (masked text only, Batch API)
                                     Warmth / quiet scores (pure maths, no AI)

 Partner ──► Web UI ──► API ──► Brief agent (LangGraph) ──► MCP tool server (read-only) ──► DB
                                    │
                                    └─ mask ─► exit door (logged) ─► Claude ─► fact-check ─► unmask
```

### Step by step

1. **Ingest.** Emails and calendar entries are loaded. Broken records (bad date, missing sender, huge body) are set aside with a reason, and the rest carry on.
2. **Build the graph.** Who emailed whom, and who attended which meeting, comes straight from the headers. This is exact, so no AI is needed.
3. **Embeddings.** Each email is turned into a vector **on our own machine** (model `bge-small-en-v1.5`), so similar emails can be found later.
4. **Warmth and quiet.** Each relationship gets a warmth score. Meetings count most, their replies count more than our emails, and old activity fades over 90 days. A relationship is "quiet" when it was established but has now been silent for more than 3× its own normal gap. The settings live in `src/hri/config/verticals/pe.yaml`.
5. **Commitments.** Claude Haiku reads each email thread (masked) and pulls out promises such as "I'll send the deck by Thursday".
6. **Keep it current.** `live_feed` processes new mail as it arrives.
7. **Brief agent.** When a partner asks for a brief, a fixed pipeline runs:

| Step | What happens |
|---|---|
| **resolve** | Find the person. If two contacts share a name, return both and let the partner choose (it never guesses). |
| **gather** | Fetch the profile, recent threads, commitments and similar emails through the MCP tool server. |
| **budget** | Fit everything into about 6,000 tokens. Older threads are shrunk to one-line summaries and the brief says what was dropped. |
| **generate** | Claude Sonnet writes the brief. Every claim must cite an email ID. |
| **verify** | Uncited claims are deleted. Claude Haiku checks each remaining claim, and if too many fail, the brief is rewritten once. |
| **finalize** | Swap the tokens back to real names and attach citations and warnings. |

**If something breaks:**
- AI down: a facts-only brief built from the database.
- Tool server down: the last saved brief, marked "stale".
- An email that looks like a prompt injection ("ignore your instructions…"): withheld from the brief and flagged to the partner.

---

## 4. Tech stack

| Part | Technology | Why |
|---|---|---|
| Orchestration | **LangGraph** | Required by the course; a fixed, auditable pipeline |
| Data access | **MCP server** (read-only tools) | Required; the agent never writes SQL and can't modify data |
| Database | **PostgreSQL + pgvector** | One store for the graph and the vectors; the scale is small |
| AI models | **Claude Sonnet 5.5** (briefs), **Claude Haiku 5.5** (extraction, checking) | Accuracy where it matters, low cost where volume is high |
| Embeddings | **fastembed / bge-small** (runs locally) | Free, and the text never leaves the machine |
| Masking | Regex + name dictionary + **Presidio/spaCy** | Catches known and unknown names |
| Tracing | **Langfuse** | Required; receives masked text only |
| API / UI | **FastAPI** / **Streamlit** | Simple and quick |
| Delivery | **Docker Compose** | Runs the same way on any machine |

---

## 5. Folder map

```
corpus/generate.py          creates the fake emails + calendar + "answer key" (ground_truth.json)
src/hri/
  ingest/                   load emails, embeddings, name detection, commitment extraction, live feed
  graph/                    database schema, warmth + quiet maths
  privacy/                  masker.py (fake tokens), egress.py (the single logged exit door)
  mcp_server/server.py      read-only tools: find_person, get_relationship, get_commitments, ...
  agent/                    the brief pipeline (brief_graph.py), prompts, context budget, fallback
  api/app.py                REST API
  ui/streamlit_app.py       partner web app
  config/                   settings, model prices (with source + date), vertical configs
evals/                      measures accuracy: quiet detection + brief quality
load/                       load test (15 and 45 people asking at once)
tests/                      proofs: no outbound action, masking works, scoring/parsing
docs/                       ADR (design decisions), runbook, client questions, roles, evidence/
```

---

## 6. How to run it

### What you need
- **Docker Desktop**, installed and **running**
- An **Anthropic API key** (for briefs and extraction)
- Optional: Langfuse keys (for the tracing dashboard)
- About 10 GB of free disk space and about 1 hour for the first full setup

### Step 1: configure
```bash
cp .env.example .env
# open .env and paste your ANTHROPIC_API_KEY (and Langfuse keys if you have them)
```

### Step 2: build and start
```bash
make build      # builds the Docker images (first time ~5–10 min)
make up         # starts 4 services: db, mcp, api, ui
```

### Step 3: create the data and load it
```bash
make corpus             # generates ~40k fake emails (seeded, so it's the same every time)
make backfill           # load → embeddings → name detection → scores → PRICE ESTIMATE
                        #   (the embedding step takes ~45 min on a laptop CPU)
make backfill-extract   # costs real money (~$1). Only run it after reading the estimate above
make live               # replay "new" mail to show the graph staying current
```

### Step 4: use it
Open **http://localhost:8501** in your browser.

| UI tab | What it shows |
|---|---|
| **Pre-meeting brief** | Type a name or choose a meeting to get a cited brief. Removed claims and warnings are shown. |
| **Gone quiet** | Relationships that have cooled, with the reason (days silent vs. usual gap). |
| **What leaves the region** | The exact masked text sent to the AI: proof of confidentiality. |
| **Graph** | Overview of people and relationships. |

You can also call the API directly at http://localhost:8000. Endpoints: `POST /brief`, `GET /quiet`, `GET /egress`, `GET /stats`, `GET /meetings/upcoming`, `POST /precompute`, `GET /health`.

### Step 5: prove it works
```bash
make test       # no-outbound proofs, masking, scoring/parsing
make eval       # accuracy reports → docs/evidence/
make load       # load test → latency p50/p95/p99 and cost
```

### Failure demos (for the pitch)
| Command | What you'll see |
|---|---|
| `make kill-llm` then ask for a brief | Facts-only brief, clearly labelled. Undo with `make revive-llm`. |
| `make kill-mcp` then ask for a brief | Last saved brief, marked stale. Undo with `make revive-mcp`. |
| `make malformed` | 5 broken records quarantined with reasons; nothing crashes. |
| `make big-context` | A 600+ email relationship squeezed into the budget, with a report of what was cut. |

### Other useful commands
`make logs` (follow the API/MCP logs) · `make down` (stop) · `make reset` (stop **and wipe the database**)

---

## 7. Results so far

| Measure | Result | Source |
|---|---|---|
| Emails loaded into the graph | 38,299 (full backfill; 3 malformed quarantined) | `docs/evidence/backfill_report.json` |
| Quiet detection: found the relationships we cooled | **93% recall** (56/60), 77% precision; dead deals 25/25 | `docs/evidence/quiet_eval.json` |
| Backfill extraction cost | Estimated $0.89, actual **$1.08** (14,818 threads) | `backfill_report.json` |
| Cost per brief | About **$0.017** | `docs/evidence/brief_eval.json` |
| Brief latency (AI path) | p50 11.0 s, p95 18.3 s | `brief_eval.json` |
| Brief accuracy | ⚠️ Needs work: 10/35 eval cases pass, 58% of claims grounded | `docs/evidence/brief_eval.md` |

### Still to do
- Improve brief accuracy and re-run `make eval` (the prompt fixes are in but haven't been re-measured)
- Run the load test (`make load`)
- Generate the cost model (`python -m evals.cost_model` → `docs/cost_model.md`)
- Langfuse dashboard evidence
- Commercial proposal (pricing, tiers, break-even)
- `docs/ai_disclosure.md`
- Pitch deck

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **Backfill** | The one-time load of the 2-year email history |
| **Graph** | The database of people, organisations and how they connect |
| **Warmth** | A score for how active and two-way a relationship is right now |
| **Quiet** | A once-warm relationship that has gone silent for too long |
| **Masking / pseudonym** | Replacing real names with tokens before the AI sees the text; the token-to-name table (the "vault") never leaves the database |
| **Egress** | Data leaving our system; there is exactly one exit door, and it is logged |
| **MCP** | Model Context Protocol: a standard way to give an AI agent tools (here, read-only database lookups) |
| **LangGraph** | A library for building AI workflows as step-by-step graphs |
| **Langfuse** | A dashboard that records every AI call (timing, tokens, cost) |
| **Batch API** | Send many AI requests at once, processed later at half price |
| **Context budget** | The cap on how much text goes into one AI request |
| **ADR** | Architecture Decision Record: what we chose, what we rejected, and why (`docs/ADR.md`) |

More detail: `docs/ADR.md` (design decisions) · `docs/runbook.md` (operations and failures) · `docs/client_questions.md` (assumptions) · `docs/roles.md` (who owns what)
