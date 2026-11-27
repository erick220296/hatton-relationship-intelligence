# What changed on `pitch-fixes`, and why

For Erick, from Yasiru (28 Nov 2026). Your `main` branch is untouched; this branch sits on top of it.

## In one paragraph
We reviewed your build against Hatton's six-point Definition of Done and the Buildr Labs marking (live failure demo, human escalation, evidence in Langfuse, cost from measured traces). The design stayed the same: same LangGraph workflow, same MCP server, same Postgres. The changes close the gaps that would have lost marks on the day: errors that showed as crashes, a fact-checker that failed open, no visible escalation path, no measured cost, and evidence still from your machine. Each fix has its own commit (F01–F16, B1–B7).

## What changed, grouped by why

| Why | Fixes | What it does now |
|---|---|---|
| **Never crash in front of the panel** | F01, F06, F09, B1, B2 | Any model error gives a facts-only brief (no HTTP 500). Close to the 30 s limit, it returns what is already verified (no 504). With MCP down, it serves the last brief, labelled stale. The UI degrades quietly. |
| **Accuracy rule: say nothing unsupported** | F05, F04 | If the verifier fails, all model text is dropped and the brief is flagged `needs_review` (fails closed). Test harness for the brief graph with a stubbed model. |
| **Privacy rule: masked before any outside model** | F02, F03, F11, F12 | Langfuse gets masked text and counts only. Secrets and data stay out of the Docker image; DB and MCP ports are localhost only. A leak test runs on real payloads with a negative control (0 leaks; 435 with masking off). A last-line check runs at the egress door. |
| **Human escalation shown live** | F07, F08, B3 | Review queue API plus decision log, the "Needs partner review" tab and badge, and `make clear-test-briefs` for demo prep (no deletes). |
| **Evidence and observability** | F10, B4, B5, B6 | One Langfuse trace per brief with scores (latency, cost, dropped ratio, citation coverage). Tests never send traces to real Langfuse. Docs use the v2/v3 Langfuse API. The eval judge is now a different model (Opus) from the writer. |
| **Real-model evidence (27 Nov, ≈$3.40)** | K01–K03 | Extraction $1.09 (estimated $1.13 first), 15,618 threads, 0 errors. Load: 15 at once, 30/30 under 30 s, p95 14.6 s, 0 errors. Eval: 21/35 strict, 95.1% of claims grounded. |
| **Commercial (my role)** | F13, F14 | Cost model built from measured data, with every price sourced and dated: $0.0197 per brief, $222/month. Proposal with three tiers and margins. |
| **Docs for the pitch** | F15, F16 + docs | ADR §14 (v2.2 design vs build), §15 (patterns map), §16 (open items). Demo script, panel prep, plain-language explainers in `docs/explain/`. |
| **UI bug** | B7 | "Gone quiet" and "Next 24h" were empty under partner "(any)". Quiet and masking evidence re-run on the current database (precision 0.809, recall 0.917). |

## Still open (honest list, also in ADR §16)
1. The verifier over-deletes true claims (e.g. "decided not to proceed" on dead deals).
2. The eval judge isn't shown the thread subject, and the month "May" gets masked as a name. Both lower the measured score; re-running the eval after fixing costs about $1.
3. New names are masked only after an API restart.
4. No login yet; fine on a laptop, not for deployment.

## How to check it
`make up`, then `make test` (84 tests, no model calls). Failure demo: `make kill-llm`, `make kill-mcp`, `make malformed`, `make big-context`. The evidence is in `docs/evidence/`, and every decision with its reason is in `docs/ADR.md`.

**AI assistance:** much of this was written and tested with AI coding agents working under my direction. Every number above was measured on the running system.
