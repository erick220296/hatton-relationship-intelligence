-- Hatton relationship-intelligence store. Postgres 16 + pgvector.
-- The relationship graph is relational: person/org nodes, partner->person edges.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS org (
  id        SERIAL PRIMARY KEY,
  name      TEXT NOT NULL,
  domain    TEXT UNIQUE NOT NULL,
  kind      TEXT                      -- bank, company, fund, law, advisory, internal, other
);

CREATE TABLE IF NOT EXISTS person (
  id         SERIAL PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT UNIQUE NOT NULL,
  org_id     INT REFERENCES org(id),
  title      TEXT,
  is_partner BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS person_name_idx ON person (lower(name));

CREATE TABLE IF NOT EXISTS message (
  id           TEXT PRIMARY KEY,
  thread_id    TEXT NOT NULL,
  mailbox      TEXT NOT NULL,          -- partner email whose mailbox it came from
  sent_at      TIMESTAMPTZ NOT NULL,
  from_id      INT REFERENCES person(id),
  subject      TEXT,
  body         TEXT,
  in_reply_to  TEXT,
  embedding    vector(384),
  ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS message_thread_idx ON message (thread_id);
CREATE INDEX IF NOT EXISTS message_sent_idx ON message (sent_at);

CREATE TABLE IF NOT EXISTS message_recipient (
  message_id TEXT REFERENCES message(id) ON DELETE CASCADE,
  person_id  INT REFERENCES person(id),
  kind       TEXT NOT NULL,            -- to | cc
  PRIMARY KEY (message_id, person_id, kind)
);
CREATE INDEX IF NOT EXISTS mr_person_idx ON message_recipient (person_id);

CREATE TABLE IF NOT EXISTS meeting (
  id         TEXT PRIMARY KEY,
  title      TEXT,
  start_at   TIMESTAMPTZ NOT NULL,
  end_at     TIMESTAMPTZ,
  organizer  INT REFERENCES person(id),
  status     TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | cancelled
  location   TEXT
);
CREATE INDEX IF NOT EXISTS meeting_start_idx ON meeting (start_at);

CREATE TABLE IF NOT EXISTS meeting_attendee (
  meeting_id TEXT REFERENCES meeting(id) ON DELETE CASCADE,
  person_id  INT REFERENCES person(id),
  PRIMARY KEY (meeting_id, person_id)
);

-- One edge per (partner, counterparty). Recomputed by hri.graph.warmth.
CREATE TABLE IF NOT EXISTS edge (
  partner_id      INT REFERENCES person(id),
  person_id       INT REFERENCES person(id),
  first_contact   TIMESTAMPTZ,
  last_contact    TIMESTAMPTZ,
  last_inbound    TIMESTAMPTZ,
  n_inbound       INT DEFAULT 0,
  n_outbound      INT DEFAULT 0,
  n_meetings      INT DEFAULT 0,
  warmth          REAL DEFAULT 0,      -- current score 0..100
  warmth_peak     REAL DEFAULT 0,      -- max score over trailing 18 months
  median_gap_days REAL,
  status          TEXT,                -- warm | cooling | quiet | cold | new
  explanation     JSONB,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (partner_id, person_id)
);

CREATE TABLE IF NOT EXISTS commitment (
  id          SERIAL PRIMARY KEY,
  message_id  TEXT REFERENCES message(id) ON DELETE CASCADE,
  thread_id   TEXT,
  owner       TEXT NOT NULL,           -- us | them
  text        TEXT NOT NULL,           -- unmasked, stored in-region
  due         TEXT,
  model       TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS commitment_thread_idx ON commitment (thread_id);

CREATE TABLE IF NOT EXISTS thread_summary (
  thread_id   TEXT PRIMARY KEY,
  topic       TEXT,
  extracted   BOOLEAN DEFAULT FALSE,
  model       TEXT
);

-- Pseudonym vault: the only mapping from tokens back to real values. Never leaves the DB.
CREATE TABLE IF NOT EXISTS pseudonym (
  kind   TEXT NOT NULL,
  real   TEXT NOT NULL,
  token  TEXT NOT NULL UNIQUE,
  PRIMARY KEY (kind, real)
);

-- Every byte that leaves the region is recorded here, exactly as sent.
CREATE TABLE IF NOT EXISTS egress_audit (
  id           BIGSERIAL PRIMARY KEY,
  at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  host         TEXT NOT NULL,
  method       TEXT,
  path         TEXT,
  purpose      TEXT,
  allowed      BOOLEAN NOT NULL,
  request_body TEXT,
  status       INT,
  response_body TEXT
);

CREATE TABLE IF NOT EXISTS brief (
  id          BIGSERIAL PRIMARY KEY,
  person_id   INT REFERENCES person(id),
  partner_id  INT REFERENCES person(id),
  meeting_id  TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  mode        TEXT,                    -- llm | fallback | cached
  content     JSONB,
  latency_ms  INT,
  cost_usd    NUMERIC(10,6),
  trace_id    TEXT
);
CREATE INDEX IF NOT EXISTS brief_person_idx ON brief (person_id, created_at DESC);

CREATE TABLE IF NOT EXISTS kv (
  key   TEXT PRIMARY KEY,
  value JSONB
);

-- Read-only role for the MCP server: the agent's tools physically cannot write.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hri_ro') THEN
    CREATE ROLE hri_ro LOGIN PASSWORD 'hri_ro';
  END IF;
END $$;
GRANT CONNECT ON DATABASE hri TO hri_ro;
GRANT USAGE ON SCHEMA public TO hri_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hri_ro;
REVOKE SELECT ON pseudonym, egress_audit FROM hri_ro;   -- tools never see the vault

-- Names/places found by NER at ingest time that the graph doesn't know; feeds the masking dictionary.
CREATE TABLE IF NOT EXISTS ner_term (
  term  TEXT PRIMARY KEY,
  kind  TEXT NOT NULL            -- NAME | ADDRESS
);
