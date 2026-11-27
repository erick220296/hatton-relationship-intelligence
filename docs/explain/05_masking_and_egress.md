# 05 · Masking, the vault and the egress door

## In one sentence
Before any text goes to an outside AI, every known name, email, phone number, address and deal codename is swapped for a code such as `PERSON_1a2b3c`. The table that maps codes back to real values never leaves our database. All model traffic goes through one exit door that only allows Anthropic and logs every byte sent.

## How it works

### Terms
- **PII**: personally identifiable information (names, emails, phones, addresses).
- **Masking**: replacing PII with codes.
- **Pseudonym**: a stand-in code, e.g. `PERSON_7dca70`.
- **Vault**: the table that maps codes back to real values.
- **Egress**: data leaving our system.

### 1. The masker (`src/hri/privacy/masker.py: Masker.mask`)
It runs inside our containers, so nothing leaves while masking. In order:
1. **Address lines.** Any line containing a UK postcode becomes `ADDRESS_…`.
2. **Emails, web links, phone numbers** (9+ digits), and **deal codenames** of the form "Project Atlas" are found by pattern-matching (regular expressions) and become `EMAIL_…`, `URL_…`, `PHONE_…`, `PROJECT_…`.
3. **A dictionary of known names** (`load_known_entities`):
   - every person in the database, by full name (`PERSON_…`) and by first and last name of 3+ letters (`NAME_…`);
   - every organisation name of 4+ letters (`ORG_…`);
   - extra names and places found by spaCy NER when the mail was ingested (`ner_term` table, see file 03).

   Longest names are matched first.
4. **Optional per-request AI name-finder (Presidio).** Off by default (`MASK_NER=0`), because it adds about 8 s per brief (module docstring).

**Codes are stable.** `token_for` makes the code from a keyed hash (HMAC-SHA256) of the real value. The same person always gets the same code, so the model can still follow "PERSON_x said… then PERSON_x replied…".

### 2. The vault (`pseudonym` table, `schema.sql`)
- `Masker.flush()` saves each new code → real value pair to Postgres.
- `unmask()` turns codes in the model's answer back into real names, inside our system (`brief_graph.py: finalize`).
- The MCP tool server's database role `hri_ro` is explicitly **denied** access to the vault (`schema.sql: REVOKE SELECT ON pseudonym, egress_audit FROM hri_ro`), and a test proves it (`tests/test_no_outbound.py: test_tool_database_role_cannot_write`).

### 3. Where masking is applied
- **Brief context:** `brief_graph.py: build` (masks the whole context).
- **Meeting, partner and person fields:** `generate`.
- **Cited sources sent to the fact-checker:** `verify`.
- **Commitment-extraction prompts:** `ingest/extract.py: thread_prompt`.
- **The eval judge:** `evals/run_brief_eval.py: judge`.

### 4. The egress door (`src/hri/privacy/egress.py`)
- The Anthropic client is built with our own HTTP client (`egress_client`), which has two hooks:
  - **before sending** (`_on_request`): if the destination host is not on the allowlist (`ALLOWED_HOSTS = {"api.anthropic.com"}`), the request is blocked *before any bytes leave*, logged as blocked, and raises `EgressBlocked`;
  - **after the answer** (`_on_response`): the exact request body and the response (up to 200,000 characters) are written to the `egress_audit` table, with the purpose (e.g. `brief_generate`).
- If the audit write fails, the call fails. We do not allow an unlogged call.
- The UI tab **"What leaves the region"** shows these rows: the system prompt and the masked user text (`GET /egress`).

### What leaves, and what doesn't
| Goes out | Where | Logged in `egress_audit`? |
|---|---|---|
| Masked brief context, masked sources for fact-checking, masked threads for commitment extraction | Anthropic API (`api.anthropic.com`) | **Yes**, every call |
| Masked prompts and outputs, token counts, costs, step names, counts. **Only if Langfuse keys are set; they are empty today** | Langfuse (tracing) | **No.** Langfuse uses its own connection (`docs/explain/fixes.md`, F02 limits) |

| Stays in our containers | |
|---|---|
| Raw email bodies, the address book, calendar | Postgres |
| Embeddings (made locally) | Postgres |
| The pseudonym vault | Postgres (not readable by the tool role) |
| Unmasked final briefs | Postgres `brief` table and the partner's screen |

### Evidence that it works (27 Nov, Ash)
- **Rebuilt brief prompts for 5 contacts:** 0 real names, name parts or emails leaked, checked against all 1,791 person and organisation names (`work/eval_baseline.md` row 7b; repeated in `work/eval_batch1.md`).
- **Langfuse:** 3 real-database briefs sent to a fake Langfuse receiver gave **0 hits for 3,211 names, emails and org names** (`work/eval_batch1.md`, F02).
- **Unit tests** (`tests/test_masking.py`): a signature-style email loses every name, phone, postcode, email and project name; codes are stable and reversible.
- **The egress log itself is empty today.** No model call has succeeded without a key, so the "What leaves the region" tab cannot be demonstrated live yet (`work/eval_baseline.md` row 7a).

## Why it was built this way
- **The client's hard rule:** PII masked before any third-party model.
- **Dictionary first** (`ADR.md §5`, `§14`). The graph already knows every contact by name, so exact matching is fast (about 0.1 s) and reliable. NER at ingest time catches unknown names without the ~8 s per-request cost.
- **Stable codes** keep the text useful to the model. Random codes would break "who said what".
- **One door, with a log** turns "we promise" into "here is every byte we sent".
- **Rejected:**
  - Presidio on every request: too slow for the 30 s budget (`masker.py` docstring).
  - Sending unmasked text to a "trusted" provider: breaks the client rule.
  - A middle gateway such as OpenRouter: one more party seeing data, and no batch pricing (`ADR.md §14`).

## What breaks it (be honest)
- **The dictionary goes stale.** The API loads it once at start-up (`masker.py: masker`). Names added by live ingest are only masked after an API restart. A planned fix is in `ADR.md §16`.
- **Gaps in what is caught** (`masker.py: load_known_entities`, `ingest/ner.py: terms_in`):
  - Matching is case-sensitive ("GUY CAMERON" or "guy" would slip through).
  - Organisation names of 3 letters or fewer are skipped (e.g. "UBS", "KKR").
  - Company names mentioned in text that are not contacts' employers (e.g. a deal target) are not masked, because NER keeps only person and place names.
  - Bare web domains without `http` are not masked.
  - NER reads only the first 3,000 characters of a body and never the subject line.
- **The codes are weakly secret.** They are 6 hex characters. The hash key defaults to a fixed value in the code (`SALT = "hatton-demo-salt"`) unless `PSEUDONYM_SALT` is set, so someone with our code and a list of candidate names could recompute codes. Set a secret salt in production.
- **Langfuse bypasses the door.** Its traffic is not on the allowlist or in the audit log. It carries only masked text and counts, so masking quality is the safeguard there.
- **"Data stays in region" means the raw data.** Masked text does go to Anthropic's API. Say it exactly that way (`docs/explain/cost_model.md`, weak spot 6).
- **The promised safety check is missing.** The `llm.py` docstring promises an `assert_masked` check, but no such function exists. Masking is the caller's job; nothing at the door re-checks it.

## Agentic pattern / course concept it shows
*Guardrails* (input and egress), *privacy by design*, and *least privilege* (the tool role cannot see the vault). Also *auditability*: every outbound byte is recorded.

## 3 likely panel questions
1. **"Prove no real names reach Claude."**
   Every call to Claude is logged word-for-word in `egress_audit`, and the UI shows it. Ash rebuilt the exact prompts for 5 contacts and checked them against all 1,791 names in the database: zero leaks. The leak-scan script `evals/leak_scan.sql` checks the whole log. Without a key the log is currently empty, so we show the rebuilt prompts instead.
2. **"What if a name isn't in your dictionary?"**
   NER at ingest adds unknown names and places (904 terms in the committed backfill). Known gaps remain: lowercase or upper-case variants, very short company names, deal targets, and names that arrived after the last API restart. These are listed in the ADR with planned fixes.
3. **"How does the brief show real names if Claude only saw codes?"**
   The model answers with the codes. In `finalize`, our own code looks each code up in the vault (memory first, then the database) and replaces it with the real name. That happens inside our containers, and the vault never leaves the database.
