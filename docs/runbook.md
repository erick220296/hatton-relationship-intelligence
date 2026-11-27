# Operations runbook

Owner: Observability Owner (Wenura). Draft by the Technical Lead; review and own it.

## Services
| Service | Port | What it does | Health check |
|---|---|---|---|
| `db` | 5432 | Postgres + pgvector: graph, vault, egress audit, briefs, checkpoints | `pg_isready -U hri` |
| `mcp` | 8001 | Read-only MCP tools (role `hri_ro`) | Brief requests succeed; `docker compose ps mcp` |
| `api` | 8000 | FastAPI + LangGraph brief agent | `GET /health` |
| `ui` | 8501 | Streamlit partner UI | Page loads |

Scheduled jobs (production):
- **Nightly 02:00:** full warmth/quiet recompute (`backfill edges`).
- **Nightly 05:30:** `POST /precompute?days=1` (briefs ready before the morning).
- **Every 15 min:** `live_feed` over new mail.

## What breaks, how you'd know, what it costs, how to recover

| Failure | How you'd know | Impact | Cost | Recovery / rollback |
|---|---|---|---|---|
| **Model provider down or rate-limited** | Briefs come back `mode=fallback` with a warning; Langfuse shows no generations; `egress_audit` shows non-200s | Partners get a facts-only brief (header, last threads, extracted commitments). No narrative. | $0 while down | Automatic: the next request retries. Extraction pauses and catches up on the next live run (threads stay `extracted=false`). |
| **MCP / data tool server down** | `mode=stale_cache` or `unavailable`; API log "MCP server unreachable" | The last good brief is served, labelled stale | $0 | `docker compose restart mcp` |
| **Database down** | `/health` 500; everything fails | No briefs | – | Restart `db`; restore from the nightly snapshot. Volume: `pgdata`. |
| **Brief over 30 s** | Langfuse latency p95 > 25 s; brief warning "Skipped LLM fact-check" | Verifier skipped, so claims are only citation-checked (still cited) | – | Check the per-node timings in the response (`timings`). Lower `context_budget_tokens` or switch `BRIEF_MODEL`. |
| **Verifier drops many claims** | `dropped_claims` high; one regenerate happens | Shorter brief | +1 generation (~$0.01) | Inspect the dropped reasons in the UI. If systematic, adjust the prompt and re-run `make eval` before shipping. |
| **Malformed mail** | `kv.quarantine` grows; `/stats` → `quarantined` | That message is skipped | $0 | Inspect the reason, fix the adapter, re-ingest by ID (idempotent). |
| **Prompt-injection email** | Brief warning "message(s) withheld" | The message is excluded from the brief | $0 | A partner reviews the message manually. |
| **Masking gap** (a new name not in the dictionary) | Real name visible in `egress_audit.request_body` | Confidentiality incident | – | Run `backfill ner` and restart `api` (reloads the dictionary). Grep egress_audit for the name to scope the exposure. |
| **Egress to an unknown host** | `egress_audit.allowed = false` rows; `/stats` → `egress_blocked` | Request refused before sending | $0 | Investigate the code path; never widen `ALLOWED_HOSTS` without review. |
| **Cost spike** | Langfuse cost per day; `brief.cost_usd` sum | Budget | Up to the monthly cap | Set an Anthropic workspace spend limit at about 2× the forecast (see `docs/cost_model.md`). |

## Deploy / rollback
- **Deploy:** `git pull && docker compose build && docker compose up -d`. Schema changes are additive (`CREATE ... IF NOT EXISTS`).
- **Rollback:** `git checkout <previous tag> && docker compose build && docker compose up -d`. Data is untouched.
- **Re-score after a config change** (`pe.yaml`): `make backfill-edges`. Deterministic, ~3 s.

## Failure demo script (pitch)
1. `make kill-llm`, request a brief: facts-only. Then `make revive-llm`.
2. `make kill-mcp`, request a brief by name: stale cache. Then `make revive-mcp`.
3. `make malformed`: 5/5 quarantined with reasons.
4. `make big-context`: 614 messages compressed to the budget, with a report of what was kept.
