# Questions for Hatton Capital Partners (Group 05)

Sent: ______ (fill in date/time sent). Answers received: ______

Every question still unanswered at build time became a written assumption, listed in the ADR (§ Assumptions) and repeated here.

| # | Question | Why it matters | Assumption until answered |
|---|---|---|---|
| 1 | **What makes a relationship "warm" to you?** Is it a meeting in the last N months, them replying, them sending us deals? Does a one-sided chase count? | Defines the warmth score and what "gone quiet" means | Warmth weights meetings 3×, their emails 1.5× and our emails 0.6×, with a 90-day half-life. Our own unanswered emails don't make a relationship warm. |
| 2 | **What gap counts as "gone quiet"?** Does it differ for bankers, founders and LPs? | Quiet threshold per contact type | Quiet = silent for more than 3× that relationship's own usual gap, with a floor of 45 days (bankers), 60 (founders), 90 (advisers) or 120 (LPs and lawyers). Only relationships that were established (≥4 two-way contacts over ≥90 days) qualify. |
| 3 | **Which of the six partners' mailboxes are in scope?** Are shared or assistant inboxes in scope? | Ingest scope and consent | All six partners; no shared inboxes. |
| 4 | **What must a brief contain, and what is noise?** How long should it be? Do you want suggested questions? | Brief template | Who they are, our history, what was last discussed, what we owe, what they owe, and relationship signals. At most 300 words. No suggested questions, because those are opinion and not sourced. |
| 5 | **How far back should the backfill go?** | Backfill cost, and how relevant old context is | 2 years. |
| 6 | **Which region is "our region"** (UK, EU, other)? Is a model API hosted outside it acceptable if it only ever sees masked data? Is a cloud observability tool acceptable on the same basis? | Hosting, and the confidentiality design | UK/EU. All unmasked data stays in our containers. Only pseudonymised text reaches the model API and Langfuse. Production would use an in-region model endpoint (see the ADR). |
| 7 | **What counts as sensitive and must be masked?** Names, email addresses, phone numbers, company names, deal codenames, amounts? | The masking rules | We mask names, emails, phone numbers, addresses, organisations, URLs and deal codenames. Amounts and dates are not masked, because briefs need them. |
| 8 | **What does a partner's hour cost the firm** (fully loaded)? | The break-even calculation | See the cost model's assumption line. |
| 9 | **What is peak demand?** For example, how many briefs at 7am on a Monday, and conference weeks? | Load test target | Peak is 15 briefs requested at once, and we test 3× that (45). |
| 10 | **How should a brief be delivered?** On a phone in the car? Ready the night before? | Delivery and latency strategy | A mobile web page. Briefs for tomorrow's meetings are prepared overnight; asking on demand takes under 30 seconds. |
| 11 | **Is email between partners a relationship signal?** | Noise filtering | No. Internal mail and newsletters are excluded from relationship scoring. |
| 12 | **When two partners know the same person, whose relationship counts?** | Brief framing | The brief shows the asking partner's relationship first, and every other partner's below it. |
| 13 | **Should the system ever suggest reaching out to a quiet contact** (without acting)? | Human authority | It flags and explains. It never drafts or sends anything. |
