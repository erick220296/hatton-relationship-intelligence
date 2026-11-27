RUN = docker compose run --rm app

.PHONY: up down build corpus backfill-load backfill-embed backfill-edges backfill-estimate backfill-extract backfill \
        live test eval eval-quiet eval-brief load precompute kill-llm revive-llm kill-mcp revive-mcp malformed \
        big-context clear-test-briefs clear-test-briefs-dry reset logs

build:            ## build images (incl. the one-off `app` image used by make test)
	docker compose --profile tools build
up:               ## start db, mcp, api, ui
	docker compose up -d db mcp api ui
down:
	docker compose down
reset:            ## wipe the database volume
	docker compose down -v
logs:
	docker compose logs -f api mcp

corpus:           ## generate the synthetic corpus + ground truth (seeded)
	$(RUN) python -m corpus.generate --out data/corpus

backfill-load:
	$(RUN) python -m hri.ingest.backfill load
backfill-embed:
	$(RUN) python -m hri.ingest.backfill embed
backfill-ner:
	$(RUN) python -m hri.ingest.backfill ner
backfill-edges:
	$(RUN) python -m hri.ingest.backfill edges
backfill-estimate: ## price the LLM extraction BEFORE running it
	$(RUN) python -m hri.ingest.backfill estimate
backfill-extract: ## run the extraction via the Batch API (costs money - run estimate first)
	$(RUN) python -m hri.ingest.backfill extract
backfill: backfill-load backfill-embed backfill-ner backfill-edges backfill-estimate

live:             ## replay post-backfill mail (keep the graph current)
	$(RUN) python -m hri.ingest.live_feed --file data/corpus/live.jsonl

precompute:       ## prepare briefs for the next day's meetings
	curl -s -X POST "http://localhost:8000/precompute?days=1" | python3 -m json.tool

test:
	$(RUN) pytest -q

eval-quiet:
	$(RUN) python -m evals.run_quiet_eval
eval-brief:
	$(RUN) python -m evals.run_brief_eval
eval: eval-quiet eval-brief

load:             ## load test against the running API
	$(RUN) python -m load.run_load --concurrency 15 --requests 45
	$(RUN) python -m load.run_load --concurrency 45 --requests 90

# ---- failure demos (pitch) ----
kill-llm:         ## simulate the model provider going down
	docker compose exec api touch /tmp/hri_llm_down
revive-llm:
	docker compose exec api rm -f /tmp/hri_llm_down
kill-mcp:         ## kill the data tool server mid-session
	docker compose stop mcp
revive-mcp:
	docker compose start mcp
malformed:        ## feed malformed records through ingest
	$(RUN) python -m hri.ingest.live_feed --file evals/cases/malformed.jsonl --no-extract
clear-test-briefs: ## demo prep: reject every queued review item as "test run" (logged in review_log, nothing deleted)
	$(RUN) python -m hri.ops.clear_test_briefs
clear-test-briefs-dry: ## count what clear-test-briefs would clear
	$(RUN) python -m hri.ops.clear_test_briefs --dry-run

big-context:      ## brief for the heaviest relationship (exceeds the context budget)
	$(RUN) python -m evals.big_context
