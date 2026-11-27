-- F07: human review queue + decision log. Additive and idempotent: safe to re-run on a loaded volume.
-- schema.sql only runs on an empty volume, so apply this to an existing one with:
--   docker compose exec -T db psql -U hri -d hri < src/hri/graph/migrations/001_review_queue.sql
ALTER TABLE brief ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ok';          -- ok | needs_review | reviewed | rejected
ALTER TABLE brief ADD COLUMN IF NOT EXISTS review_reasons JSONB;
CREATE INDEX IF NOT EXISTS brief_status_idx ON brief (status, created_at DESC);

CREATE TABLE IF NOT EXISTS review_log (    -- who decided what about which brief, and why (audit trail)
  id        BIGSERIAL PRIMARY KEY,
  brief_id  BIGINT REFERENCES brief(id),
  decision  TEXT NOT NULL,                 -- approve | reject
  note      TEXT,
  reviewer  TEXT,
  at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS review_log_brief_idx ON review_log (brief_id, at DESC);
