# 04 · Warmth score and "gone quiet" detection

## In one sentence
For every partner–contact pair, a fixed formula adds up recent contacts, with meetings and replies counting more and old contacts fading. It flags a relationship as *quiet* when it was clearly warm and has now been silent for much longer than its own usual rhythm.

## How it works
Code: `src/hri/graph/warmth.py`. All settings are in `src/hri/config/verticals/pe.yaml`.

### Step 1: collect the interactions (`_interactions`)
For each partner–contact pair, the code lists every:
- **inbound** email: the contact wrote to the partner;
- **outbound** email: the partner wrote to the contact;
- **meeting**: a confirmed, already-held meeting with both present. Cancelled and future meetings are ignored.

### Step 2: the warmth score (`score_edge`)
In words:
1. Each interaction earns points: **meeting 3.0, inbound 1.5, outbound 0.6** (`warmth.weights`).
2. Points fade with a **90-day half-life** (`half_life_days`). An interaction from 90 days ago counts half, from 180 days ago a quarter.
3. Add the faded points up. This is the *raw* score.
4. Squash it onto 0–100: **score = 100 × (1 − e^(−raw ÷ 12))** (`scale: 12`). The score rises fast at first, then flattens, so heavy contact cannot run off to infinity.

The code also records the **peak** score over the last 540 days (`peak_lookback_days`), sampled right after each interaction.

### Step 3: the status (in this order, `score_edge`)
1. **new**: the first contact was less than 30 days ago.
2. **quiet**: all of these are true:
   - the relationship is *established*: at least 4 two-way contacts (inbound or meeting) spread over at least 90 days within the lookback (`min_two_way`, `min_span_days`);
   - the peak was warm: ≥ 35 (`warm_threshold`);
   - the days since the last two-way contact are more than the **allowed gap**: the larger of
     - a floor per contact type (`kind_min_gap_days`: bank 45, company 60, fund 120, law 120, advisory 90, other 90), and
     - 3 × that pair's own **median gap** between two-way contacts (`gap_multiplier: 3.0`).
3. **warm**: the current score ≥ 35.
4. **cooling**: the peak was ≥ 35, but it is lower now.
5. **cold**: everything else.

Our own outbound emails **do not reset the silence clock**. Only the contact replying, or a meeting, does. The code counts unanswered outreach separately (`unanswered_outbound_since`).

Every pair stores an **explanation** (JSON): days silent, allowed gap, median gap, peak, current score, threshold, unanswered outreach. That is what the "Gone quiet" tab shows.

### Worked example (illustrative; I computed it with the `pe.yaml` settings, not taken from the data)
A banker emails the partner back every 14 days for a year, then stops. We look 60 days after the last reply.
- **Peak while active.** About 71: a reply every 14 days keeps the faded points near 14.7, and 100 × (1 − e^(−14.7/12)) ≈ 71. That is ≥ 35, so it *was* warm.
- **Score now.** 60 days of fading gives ≈ 14.7 × 0.63 ≈ 9.3 points, so the score is ≈ 54. That is still above 35: by score alone it looks "warm".
- **Allowed gap.** max(bank floor 45, 3 × median gap 14 = 42) = **45 days**.
- **Days silent = 60 > 45**, so the status is **quiet**.

The point: quiet is about *silence compared with this relationship's own rhythm*, not about a low score. Compare a fund LP who meets every 60 days. Their allowed gap is the larger of the fund floor (120) and 3 × their usual gap (at least 180), so 70 days of silence is **not** flagged (`tests/test_warmth_and_parse.py: test_slow_cadence_lp_not_flagged_early`).

### Results (against the planted answer key; see file 02)
- **Committed evidence** (`docs/evidence/quiet_eval.json`, original database):
  - 60 cooled; 98 flagged; 56 found
  - **precision 0.767, recall 0.933**
  - all 25 dead deals flagged (reported separately)
- **Running database, 27 Nov** (Ash, `work/eval_baseline.md` and `work/eval_batch1.md`, 3 identical runs): 93 flagged, 55 found, **precision 0.809, recall 0.917**.

*Precision* = of the ones we flagged, how many were truly cooled. *Recall* = of the truly cooled, how many we caught. The two runs differ because the database was rebuilt.

## Why it was built this way
- **Deterministic and explainable** (`ADR.md D5`). A partner can ask "why is this flagged?" and see the exact numbers. The same input always gives the same answer, it costs $0, and it runs over all edges in about 3 s (`backfill_report.json`: edges step 2.6 s).
- **Each pair's own rhythm.** A fixed "60 days silent" rule would wrongly flag quarterly LPs and miss weekly bankers. The median-gap rule adapts to each relationship.
- **Reciprocity counts most.** Our own emails are weighted lowest and do not reset silence, because a one-sided relationship is exactly what "gone quiet" means.
- **Tunable without code.** The thresholds live in `pe.yaml`, so partners can change what "warm" means. A second vertical (`agency.yaml`) shows the same engine with different numbers. It is not built or evaluated.
- **Rejected:** asking an AI to judge warmth. It costs money on every pair, gives different answers each run, cannot be explained, and would mean sending relationship data out.

## What breaks it
- **Statuses go stale between full re-scores.** The live feed re-scores only pairs with new mail (`live_feed.py: process` → `recompute(only_pairs=...)`). A relationship that goes silent gets no new mail, so it is never re-checked until someone runs `make backfill-edges`. No scheduler does that (file 03).
- **Recent cooling of slow relationships is missed.** That is where the 4–5 false negatives are (`quiet_eval.json: missed`; `ADR.md §13`). Their allowed gap is long, so they are not flagged yet.
- **Over-counting.**
  - If a contact emails partner A and only CCs partner B, B also gets an "inbound" point.
  - A partner email to 5 people counts as outbound for each of them (`_interactions`).
- **Dead deals look quiet.** All 25 are flagged. That is defensible, since the relationship did go quiet, but if counted as false positives the precision drops (committed evidence: 56 of 98 flagged are true cooled = 0.57).
- **The demo clock is fixed.** "Now" defaults to 25 Nov 2026 07:00 (`config/settings.py: as_of`) unless the live feed moves it forward.

## Agentic pattern / course concept it shows
*Use code where code is enough* (Anthropic, "Building effective agents"): not every step of an AI product should be AI. It also shows *explainability* and *configuration over code* (procedural knowledge in `pe.yaml`).

## 3 likely panel questions
1. **"How do you decide a relationship has gone quiet?"**
   It must have been clearly warm (a peak score of at least 35, with at least 4 two-way contacts over 90 days). Then the time since the contact last replied or met must be longer than the larger of a type-based floor (e.g. 45 days for bankers) and three times that relationship's usual gap. Our own unanswered emails do not count as contact.
2. **"How accurate is it?"**
   Against the 60 relationships we deliberately cooled: on the current database it flags 93, of which 55 are truly cooled. That is precision 0.81 and recall 0.92, the same on three runs (Ash, 27 Nov). It mainly misses relationships that cooled recently but normally have a slow rhythm.
3. **"Why not let the AI decide?"**
   Partners need to trust and question the flag. The formula shows its working (days silent vs allowed gap), is identical every run, costs nothing, and keeps relationship data in-region. AI judgement would be none of those.
