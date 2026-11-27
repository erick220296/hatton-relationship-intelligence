# Panel Prep: Evals and Data Privacy

How to show the panel two things:
1. **The output is correct**: the evals.
2. **We don't hand client data to Claude**: masking, the egress allowlist and the audit log.

Preparing: Erick and Yasiru. Related: [`TESTING_GUIDE.md`](TESTING_GUIDE.md), [`UI_TEST_DATA.md`](UI_TEST_DATA.md), [`runbook.md`](runbook.md).

---

## 0. Before the session (do this the day before)

```bash
open -a Docker
make up
curl localhost:8000/health                 # → {"ok":true}
curl -s localhost:8000/stats | python3 -m json.tool   # messages ≈ 39k, egress_blocked: 0
```

- [ ] `make test` passes (20 passed)
- [ ] `make eval-quiet` has been re-run (free)
- [ ] `make eval-brief` has been re-run (**about $1, about 10 min**). The last run is out of date; see §1.3.
- [ ] The leak scan has been re-run after the eval (§2.3), so the result covers the newest requests
- [ ] Screenshots of `docs/evidence/brief_eval.md`, `quiet_eval.json` and the leak scan output are saved as a backup in case the live demo fails

---

## 1. Proving the output is correct (evals)

### 1.1 What we test, and how

| Eval | Command | What it checks | Why it's trustworthy |
|---|---|---|---|
| Unit tests | `make test` | Masking removes PII, nothing can send email, warmth scoring, parser | Runs offline and is deterministic |
| Gone-quiet detection | `make eval-quiet` | Did we flag the relationships we deliberately cooled in the synthetic data? | Scored against an **answer key** (`data/corpus/ground_truth.json`) that the system never reads |
| Brief quality | `make eval-brief` | 35 cases: 21 ordinary, 8 awkward, 6 hostile | A **separate AI judge** (different prompt from our built-in verifier) re-checks every claim against the emails it cites |

The brief eval cases are in `evals/cases/` and the evaluation code is in `evals/run_brief_eval.py`.

### 1.2 How to read the results

**Gone quiet:** `docs/evidence/quiet_eval.json`

| Metric | Value | What it means |
|---|---|---|
| Recall | **0.933** (56/60) | We found 56 of the 60 relationships we deliberately cooled |
| Precision | **0.767** | About 3 in 4 flags are real. The rest are worth a look, not wrong alarms that would harm anyone. |
| F1 | 0.842 | |
| Dead deals flagged | **25/25** | Every deal that died was caught |

The 4 misses are listed in the JSON, so be ready to explain one.

**Briefs:** `docs/evidence/brief_eval.md`

The top of the file has the summary. Below it is a table with one row per case, showing pass/fail, mode, latency, number of claims, unsupported claims and the reason for failure.

| Metric | Last run (25 Nov) | What it means |
|---|---|---|
| Pass rate | 10/35 (29%) ⚠️ | A case passes only if it's fast, in the right mode **and** has zero unsupported claims |
| Grounded rate | 57.8% | Share of claims the judge could confirm from the cited email |
| Commitment recall | 42/56 | Planted promises ("I'll send the deck by Wednesday") the brief picked up |
| Hostile cases | 5/6 | Injection, SQL attack and unknown person handled safely |
| Latency p50 / p95 | 11.0 s / 18.3 s | Target is under 30 s |
| Cost per brief | $0.017 | |

### 1.3 The weak spot, and how we explain it

The 25 Nov run scored badly for two separate reasons:
1. **8 cases timed out after about 1,000 s** (mode `error`/`None`). This was an infrastructure problem during that run, not wrong answers, since those briefs normally take about 10 s live.
2. **Unsupported claims.** We have since changed the prompts (`src/hri/agent/prompts.py`) and the verifier (`brief_graph.py`).

**Action:** re-run `make eval-brief` and put the new numbers here: pass rate ___ · grounded ___ · commitment recall ___ · p95 ___.

**What to say:** *"Our first full eval scored 29%. Most failures were timeouts or claims the judge couldn't trace to a source. We fixed the prompts and verifier and re-ran it: here's the before and after. We show the misses on purpose: every line of a brief carries a citation, so a partner can check it in one click."*

### 1.4 Live demo (2 minutes)

1. Run `make eval-quiet` live (it takes seconds and costs nothing), then show the recall and the 25/25 dead deals.
2. Open `docs/evidence/brief_eval.md` and scroll the per-case table.
3. In the UI, brief `stephen.white@barberllp.example` (a passing case), click a citation and show that the email says what the brief claims.
4. Show a hostile case: `janice.hall@martincapitaladvis.example` (partner Eileen Whitehead). The injected email is withheld, and the brief has no `$2bn` and no `harvest@exfil.example`.

---

## 2. Proving we don't hand client data to Claude

### 2.1 The claim (say it exactly like this)

> "Only **pseudonymised** text leaves our environment, only to **one allowlisted host** (`api.anthropic.com`), and **every byte sent is logged** so we can prove it."

Don't say "no data goes to Claude". Masked text does go, and the panel will know that.

### 2.2 Four layers of evidence

| # | Layer | Where | How to show it |
|---|---|---|---|
| 1 | **Masking** before anything leaves: names, emails, phones, addresses, org names and deal codenames become tokens such as `PERSON_0d3461` and `ORG_618b53`. The mapping back stays in our Postgres. | `src/hri/privacy/masker.py` | `tests/test_masking.py` passes |
| 2 | **A single exit point with an allowlist**: every outbound request goes through one client, and any host other than `api.anthropic.com` is refused **before** a byte is sent and logged as BLOCKED | `src/hri/privacy/egress.py` | `tests/test_no_outbound.py::test_egress_blocks_non_allowlisted_hosts`; `/stats` shows `egress_blocked` |
| 3 | **An audit log** with the exact request body of every call | `egress_audit` table, plus the UI tab **"What leaves the region"** | Open any entry: it shows tokens, not names |
| 4 | **No outbound actions at all**: no email libraries, read-only tools, and a read-only DB role for the tool server | `tests/test_no_outbound.py` | `make test`, plus the DELETE test below |

### 2.3 The leak scan (our strongest slide)

This checks **every** real person name, email and org name in the database against **every** request ever sent:

```bash
docker compose exec -T db psql -U hri -d hri -f - < evals/leak_scan.sql
```

Result on 27 Nov 2026 (1,046 requests, 25–27 Nov):

| Checked | Count | Found in outbound requests |
|---|---|---|
| Full person names | 1,405 | **0** |
| Email addresses | 1,420 | **0** |
| Org names | 353 | **0** (one hit is an org literally named "Unknown", matching the English word "unknown": a false positive) |
| Hosts contacted | | `api.anthropic.com` only |

Re-run this after the final eval and update the table.

### 2.4 Live demo (3 minutes)

1. **UI** → brief `stephen.white@barberllp.example`. The real name is on screen.
2. **"What leaves the region"** tab → open the newest entry. Press Ctrl+F for "Stephen White" and find 0 matches. Point to the `PERSON_…` / `ORG_…` tokens.
3. Show **Blocked egress attempts = 0** on the same tab.
4. Terminal: a spot-check for a single name:
   ```bash
   docker compose exec -T db psql -U hri -d hri -c \
     "select count(*) from egress_audit where request_body ilike '%Stephen White%';"   # → 0
   ```
5. Terminal: the full leak scan (§2.3) gives 0 leaks.
6. Terminal: prove the tool server can't write:
   ```bash
   docker compose exec -T db psql -U hri_ro -d hri -c "delete from person where id=-1;"
   # → ERROR: permission denied
   ```

### 2.5 Anthropic's side (Yasiru: verify before the session)

Checked 2026-11-27 by Yasiru (with AI assistance), from the live pages:

- **No training on API data by default.** Commercial Terms, Section B: *"Anthropic may not train models on Customer Content from Services."* https://www.anthropic.com/legal/commercial-terms (effective 17 Jun 2025). Privacy Center (dated 18 Aug 2026): *"By default, we will not use your inputs or outputs from our commercial products (e.g. Claude for Work, Anthropic API, …) to train our models."* https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training
- **Retention: 30 days by default.** *"For Anthropic API users, we automatically delete inputs and outputs on our backend within 30 days of receipt or generation, except:"* (exceptions: customer-controlled storage such as the Files API, agreed arrangements such as ZDR, Usage Policy enforcement, and legal requirements). If flagged by trust and safety systems: *"We retain inputs and outputs for up to 2 years."* https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data (dated 1 Jul 2026)
- **Batch API results:** *"Batch results are available for 29 days after creation."* Batch processing is **not** eligible for zero data retention. https://platform.claude.com/docs/en/build-with-claude/batch-processing. This matters for us: the backfill used Batch, which is why only **masked** text ever goes into it.
- **Zero data retention (ZDR)** is available by arrangement through Anthropic sales, per organisation, and not for Batch. https://platform.claude.com/docs/en/manage-claude/api-and-data-retention

**Say it like this:** "Anthropic contractually may not train on our API data. They delete it within 30 days by default, 29 days for batch results. And what they receive is already masked: placeholders, not names. For production we'd add a zero-data-retention agreement for the live brief calls."

---

## 3. Questions the panel will likely ask

| Question | Answer | Who |
|---|---|---|
| "How do you know the brief is right?" | Every line cites a source email. A built-in verifier removes claims it can't support (shown in the UI as "claims removed by the verifier"), and an independent judge scored 35 cases. Show the before and after numbers. | Erick |
| "Your pass rate is low." | Pass means zero unsupported claims plus speed plus the right mode, which is a strict bar. Explain the timeouts, the fixes and the re-run. Hostile cases pass 5/6. | Erick |
| "Isn't the answer key just your own opinion?" | The synthetic data generator **planted** the cooled relationships, dead deals and traps and wrote them to `ground_truth.json`. The system never reads that file. | Erick |
| "Does client data go to Anthropic?" | Pseudonymised text does, to one allowlisted host, with every request logged. Leak scan: 0 of 3,178 identifiers. | Erick |
| "Could a name slip through masking?" | Yes, that's possible: for example a first name on its own, or a name the masker doesn't know. Known names are masked from the graph, and unknown names are found at ingest by spaCy. A slower per-request scan (Presidio, about 8 s) exists but is off for speed. The leak scan is how we monitor it. | Erick |
| "Masked text still shows the deal content?" | Yes: topics and amounts are visible, but not who is involved. The mapping back never leaves our DB. | Erick |
| "Does Anthropic train on it?" | Quote the verified terms from §2.5. | Yasiru |
| "What does this cost per brief or per partner?" | About $0.017 per brief (from the eval). Use the cost model and break-even from `docs/cost_model.md` / `evals/cost_model.py`. | Yasiru |
| "What if Claude is down?" | `make kill-llm` gives a facts-only brief in about 0.4 s, with no AI involved. | Erick |
| "Could a malicious email hijack the AI?" | Messages aimed at AI systems are withheld and flagged (demo: Janice Hall). Even if one got through, the system has no send or write capability. | Erick |

---

## 4. Split of prep work

| Task | Erick | Yasiru |
|---|---|---|
| Re-run `make eval-brief` and update §1.3 | ✅ | |
| Re-run the leak scan and update §2.3 | ✅ | |
| Rehearse demos §1.4 and §2.4 | ✅ | ✅ (backup presenter) |
| Verify Anthropic's data terms (§2.5) | | ✅ |
| Cost per brief and break-even slide | | ✅ |
| Backup screenshots of all evidence | ✅ | ✅ |
| Mock Q&A using §3: each asks the other | ✅ | ✅ |

> Wenura owns `evals/` and `docs/evidence/` (see `roles.md`), so check the final numbers with Wenura before the session.
