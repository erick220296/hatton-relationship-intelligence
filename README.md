# Hatton Relationship Intelligence (Group 05)

This system turns six partners' email and calendars into a relationship graph. It answers two questions:
- "Brief me on this person before my meeting" (in under 30 seconds, every claim cited).
- "Which warm relationships have gone quiet?"

It informs only. It never emails, schedules or contacts anyone.

**New to the project? Start with [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md)**: a plain-English walkthrough of what it does, how it works and how to run it. To try it with test data, see [`docs/TESTING_GUIDE.md`](docs/TESTING_GUIDE.md).

Client brief and engagement docs: `docs/brief/`. Architecture and decisions: `docs/ADR.md`.

## Run it from a clean machine

```bash
cp .env.example .env              # add ANTHROPIC_API_KEY (+ Langfuse keys for tracing)
make build up                     # db, mcp, api, ui
make corpus                       # ~40k synthetic messages + calendar + ground truth (seeded)
make backfill                     # load -> local embeddings -> warmth/quiet -> PRICE the LLM extraction
make backfill-extract             # run extraction via the Batch API (only after reading the estimate)
make live                         # replay post-backfill mail: the graph stays current
open http://localhost:8501        # partner UI
```

Evidence:

```bash
make test        # no-outbound proofs, masking, warmth, parser
make eval        # quiet-detection P/R + brief eval (ordinary/awkward/hostile) -> docs/evidence/
make load        # morning burst: 15 concurrent and 45 concurrent -> p50/p95/p99, errors, cost
```

Failure demos: `make kill-llm` / `make revive-llm`, `make kill-mcp` / `make revive-mcp`, `make malformed`, `make big-context`.

## Layout

| Path | What |
|---|---|
| `corpus/generate.py` | Synthetic corpus and ground truth (cooled relationships, dead deals, name collisions, injection, malformed records) |
| `src/hri/ingest/` | Parse/quarantine, graph store, local embeddings, commitment extraction (Batch API), live feed |
| `src/hri/graph/` | Schema, warmth score, quiet detection |
| `src/hri/privacy/` | Masking (deterministic pseudonyms + vault) and the single audited egress door |
| `src/hri/mcp_server/` | Read-only MCP tools over a SELECT-only DB role |
| `src/hri/agent/` | LangGraph brief agent: resolve → gather → budget → generate → verify (→ retry) → finalize, plus fallback |
| `src/hri/api/`, `src/hri/ui/` | FastAPI and Streamlit |
| `src/hri/config/` | Vertical config (`pe.yaml`, `agency.yaml`), prices with source and date |
| `evals/`, `load/`, `tests/` | Evidence |
