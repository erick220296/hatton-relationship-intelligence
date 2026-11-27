# 02 · The synthetic corpus and the planted "answer key"

## In one sentence
We could not use Hatton's real email, so a program generates about 40,000 realistic fake emails and meetings. It plants known patterns in them on purpose, writes down the right answers in a separate answer key, and the evals then check the system against that key.

## How it works
Code: `corpus/generate.py`. Run with `make corpus`, i.e. `python -m corpus.generate --out data/corpus`.

1. **A fixed random seed (7)** (`main`, `--seed`). The same seed gives the same corpus every time, so results can be reproduced.
2. **A fake world** (`Gen.build_world`), made with *Faker*, a library that invents realistic names, phones and addresses:
   - six Hatton partners on `hattoncap.example`;
   - organisations by type (`ORG_PLAN`): 220 companies, 60 banks, 50 funds, 30 law firms, 40 advisers, with 2–4 people each;
   - 170 independents on personal domains (`N_INDEPENDENT`).

   All domains end in `.example`, which is reserved and can never be real. No real personal data is used (module docstring).
3. **Planted relationship stories** (`build_edges`). Each contact gets a *trajectory*, meaning the shape of the relationship over two years:
   - `warm_steady`: the rest of the contacts.
   - `warming`: 10% of contacts.
   - `cooling`: exactly 60. These are warm until a change point 95–300 days before the cut-off, then silent.
   - `deal_died`: exactly 25. Hatton sends "we have decided not to proceed", then silence.
   - `dormant`: 20%.
   - `one_off`: 25%.
   - About 18% of contacts also know a second partner, more loosely.
4. **Realistic timing** (`event_times`). Contacts happen at random but with a rhythm. The gaps follow a gamma(k=4) distribution, so they are spread around a typical gap rather than purely random. Times fall in business hours, mostly on weekdays (`_business_time`). About 20% of events are meetings; the rest are email threads of 1–6 messages (`simulate`, `thread`).
5. **Planted facts** (`thread`). About 35% of messages contain a commitment such as "I'll send over our IC memo by Friday". The program records each one in the answer key with its message ID, who owes it, and the date.
6. **Awkward and hostile cases** (`special_cases`, `build_world`, `future_calendar`):
   - 3 **name collisions**: two different people with the same name.
   - 1 **very heavy relationship**: a banker with 14× the normal email rate, to overflow the context budget.
   - 1 **prompt injection**: an email saying "IMPORTANT SYSTEM NOTE TO ANY AI ASSISTANT: ignore all previous instructions…", with forbidden phrases recorded.
   - 1 **Spanish email**, containing a commitment.
   - 4 **malformed records**: a bad date, a missing sender, a 2-million-character body, and an HTML-only body.
   - 1 **cancelled meeting**.
   - A week of **future meetings**, including two with cooled contacts.
7. **Noise.** 7% internal partner-to-partner mail and 4% newsletters marked with a `List-Unsubscribe` header (`noise`). Ingest should skip both.
8. **Live feed.** 800 extra messages dated after the cut-off, including one "revived" cooled contact (`live_feed`). They are replayed later to show the graph staying current.
9. **The output** (`data/corpus/`):
   - `messages.jsonl`
   - `calendar.jsonl`
   - `contacts.jsonl` (an address-book export)
   - `live.jsonl`
   - `ground_truth.json`: the answer key. **The system never reads this file.** Only the evals do (`run_quiet_eval.py`, `run_brief_eval.py`).

Clock used throughout: the backfill covers 25 Nov 2024 to 24 Nov 2026, and "now" for the demo is 25 Nov 2026 07:00 UTC (`START`, `END`, `AS_OF`).

## Why it was built this way
- **Privacy and the course rules.** No real client mail could be used. Generated data with reserved `.example` domains removes that risk completely.
- **Measurable truth.** Because we planted the 60 cooled relationships and every commitment, we can count exactly how many the system finds (precision and recall, see file 09). Real data has no answer key.
- **Hard cases on purpose.** Name collisions, injection, the oversize history, malformed input and another language exist to exercise each guardrail and failure path.
- **Rejected:** a public email dataset (e.g. Enron). It contains real people's data, has no private-equity structure, and has no answer key.

## What breaks it
- **Too tidy.** Emails are built from templates (`OPENERS`, `FILLERS`, `US_COMMITMENTS` …), so real mail will be messier: forwards, long chains, attachments, signatures in odd places. `ADR.md §13` admits this, and names commitment extraction as the part most exposed.
- **The injection test is easy.** There is one injection, and its wording ("ignore all previous instructions", "SYSTEM NOTE") is exactly what the regex filter looks for (`brief_graph.py: INJECTION_RE`). A cleverer injection is not tested.
- **One counterparty per meeting.** Meetings have one partner and one external person (`meeting`). Multi-attendee meetings are not exercised, and the agent briefs only the first external attendee (`ADR.md §14`).
- **The quiet test favours the formula.** Cooling relationships are perfectly warm until a sharp change point, then completely silent (`_rate_mult`). Real relationships fade gradually, so the quiet results are probably optimistic.

## Agentic pattern / course concept it shows
*Evaluation-driven development*: build the test set and the answer key first, then measure the agent against it. It also produces *adversarial test cases* (injection, malformed input).

## 3 likely panel questions
1. **"Why should we trust results on fake data?"**
   Because we know the truth for every planted case, the numbers are exact, not opinions. The limit is realism: templates are cleaner than real mail. We say so in the ADR, and the first step with Hatton would be a pilot on one mailbox.
2. **"Could the system cheat by reading the answers?"**
   No. `ground_truth.json` is written by the generator and read only by the eval scripts. The ingest code reads only `messages.jsonl`, `calendar.jsonl`, `contacts.jsonl` and `live.jsonl` (`ingest/backfill.py: load`).
3. **"Why 60 cooled relationships?"**
   It is a fixed, known number (`build_edges`: `["cooling"] * 60`), large enough to compute precision and recall meaningfully, and spread across bankers and founders. Dead deals (25) are counted separately, because flagging them is defensible.
