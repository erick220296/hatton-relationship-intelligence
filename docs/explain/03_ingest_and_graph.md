# 03 · Ingest and the relationship graph

## In one sentence
Ingest reads every email and meeting and checks it. Anything broken goes into a quarantine list instead of crashing the run. Good records are stored as people, organisations and contacts in Postgres, and each email gets a local embedding for similarity search.

## How it works

### Terms
- **Graph.** A map of nodes (people, organisations) and links (who emailed or met whom). Here it lives in ordinary database tables (`src/hri/graph/schema.sql`). The key link table is `edge`: one row per *partner × contact* pair.
- **Embedding.** A list of 384 numbers that captures what a text is about. Similar texts get similar numbers, which makes "find related emails" possible.
- **pgvector.** A Postgres add-on that stores embeddings and searches them.

### The backfill (one-off load of two years of history)
Run with `make backfill`. Code: `src/hri/ingest/backfill.py: main`.

1. **load** (`backfill.load`)
   - Contacts first (`store.Store.load_contacts`). Each person is linked to an organisation by email domain (`Store.org`, `Store.person`). Anyone on `hattoncap.example` is marked as a partner (`is_partner`).
   - Then every email:
     - **Parse and validate** (`ingest/parse.py: parse`). A record is rejected as `Malformed` if it is missing its id, thread, mailbox or date; has an unreadable date or one without a timezone; has a bad address; has no recipients; or has a body over 1,000,000 characters. HTML bodies are converted to text, and bodies over 50,000 characters are cut.
     - **Quarantine** (`Store.add_message`). A malformed record is added to a quarantine list with the reason, and the run continues. The list is saved in the `kv` table (`save_quarantine`) and shown at `GET /stats`.
     - **Skip bulk mail.** Newsletters with a `List-Unsubscribe` header are skipped (`bulk_markers` in `config/verticals/pe.yaml`).
     - **Store.** Writes `message` and `message_recipient` rows. Inserts use `ON CONFLICT DO NOTHING`, so running it twice does not duplicate anything.
   - Then the calendar (`Store.add_meeting`).
2. **embed** (`backfill.embed` → `ingest/embed.py: embed_texts`). The *bge-small-en-v1.5* model runs locally through *fastembed* on subject + body (first 2,000 characters), in batches of 256. No API call is made, so the cost is $0.
3. **ner** (`ingest/ner.py: run`). A language model called *spaCy* finds person and place names in email bodies that are not already known contacts. They go into the `ner_term` table, which feeds the masking dictionary (file 05). *NER* = named-entity recognition, which means spotting names in text.
4. **edges** (`graph/warmth.py: recompute`). Computes the warmth score and quiet status for every partner–contact pair (file 04).
5. **estimate / extract** (`ingest/extract.py`).
   - First it *prices* the AI commitment extraction without spending anything (`estimate`).
   - Then `make backfill-extract` sends each thread, **masked**, to Claude Haiku through the *Message Batches API*, which costs 50% less and returns answers within hours (`extract_batch`).
   - The model returns commitments ("us"/"them", text, due) with a message ID. Any ID the model made up is rejected (`_save`).

Each step writes its numbers to `docs/evidence/backfill_report.json`.

### The live feed (keeping the map current)
Run with `make live`. Code: `src/hri/ingest/live_feed.py: process`. For each batch of new mail:
1. Same parse, quarantine and store steps.
2. Embed the new messages.
3. Run NER on them.
4. Find the partner–contact pairs they touch.
5. Move the system clock forward to the newest message (`clock.advance`; the clock is stored in `kv` so all containers agree).
6. Re-score only the touched pairs.
7. Re-extract commitments for the touched threads. If the model is unavailable, it stops extracting and catches up next run.

It reports any status changes, e.g. cooling → warm.

### Measured numbers (from the files, with their date)
- **Committed backfill report** (`docs/evidence/backfill_report.json`, original run):
  - 38,299 messages stored; 1,600 bulk skipped; 3 quarantined
  - 1,420 people; 385 orgs; 3,478 meetings
  - embedding took 2,631 s
  - 904 NER terms
  - extraction of 14,818 threads: estimated $0.89, actual $1.08 (computed from measured tokens × list price)
- **Running database on 27 Nov** (Ash's baseline, `work/eval_baseline.md`): 39,099 messages, 1,414 people, 2,454 edges, **0 commitments**. Extraction has not been run on the rebuilt database because there is no key.
- **`make malformed`:** 5 received, 0 stored, 5 quarantined, on 3 runs (Ash, 27 Nov).

## Why it was built this way
- **The graph comes from email headers, not AI** (`ADR.md D2`). Who wrote to whom, and when, is exact data. Building it by code is free and fully explainable.
  - *Rejected:* an AI call per message to extract people and links. That costs money on every message and can hallucinate. `ADR.md §8` calls this decision "the difference between affordable and not".
- **Postgres + pgvector, not a graph database** (`ADR.md D1`). One database for the graph, the vectors, the vault and the audit log. The queries we need are simple joins.
  - *Rejected:* Neo4j and Pinecone/Weaviate. Each is another store to secure, and the hosted ones send data out of the region.
- **Local embeddings** (`ADR.md D4`). Raw text never leaves, cost is $0, and there is no outage dependency.
  - *Rejected:* OpenAI embeddings. They would send raw email text out of the region.
- **Quarantine, not crash.** One bad record from Outlook or Gmail should not stop 40,000 good ones.
- **Batch API for the backfill.** Half price, and the backfill is not urgent.

## What breaks it
- **No scheduler.** The live feed and the nightly full re-score are manual `make` commands. `docs/runbook.md` describes "every 15 min" and "nightly" jobs that do not exist in the code (`ADR.md §14` says "a scheduler is a production item").
- **Re-runs re-process duplicates.** `Store.add_message` returns a message even if it was already stored, so the live feed re-embeds and pays again to re-extract (`store.py: add_message`, `live_feed.py: process`).
- **Some bad input still crashes.**
  - An invalid JSON line in the live file crashes `live_feed.main` before anything is processed.
  - A malformed calendar record crashes `backfill.load`.
  - A non-text `mailbox` field gives a Python error instead of quarantine (`parse.py: parse`).
- **Database setup only runs once.** `schema.sql` runs only when the database volume is first created. Later table changes need a migration file (e.g. `graph/migrations/001_review_queue.sql`), applied by hand.
- **Commitments are empty right now.** Extraction needs a funded key, so the "what we owe / what they owe" sections are blank on the current database.

## Agentic pattern / course concept it shows
- *Data pipeline and validation in front of an agent*: clean, structured, trustworthy context.
- *RAG foundations*: embeddings and vector search. *RAG* = retrieval-augmented generation, which means fetching relevant text and giving it to the model.
- *Cost-aware design*: estimate before you spend, and use batch processing.

## 3 likely panel questions
1. **"How do you handle bad data?"**
   Every record is validated (`parse.py: parse`). Broken ones go to a quarantine list with the reason, visible at `/stats`, and the run continues. `make malformed` feeds 5 broken records: 0 are stored, 5 are quarantined, and nothing crashes.
2. **"Why didn't you use AI to build the graph?"**
   Because the headers already tell us exactly who emailed or met whom and when. Code is free, exact and explainable. AI is used only where meaning matters (commitments, and the brief), and only on masked text.
3. **"How does the graph stay up to date?"**
   `make live` replays new mail through the same ingest code. It updates only the pairs that changed and moves the system clock forward. In production this would run on a schedule; that scheduler is not built yet.
