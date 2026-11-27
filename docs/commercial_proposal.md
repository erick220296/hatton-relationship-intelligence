# Commercial proposal: Hatton Capital Partners, Relationship Intelligence

Owner: Commercial Lead (Yasiru Lelwala). All costs come from `docs/cost_model.md` (measured data, prices with source and date). Fees below are **our proposal**, not measured facts.

## 1. What Hatton gets
- A relationship graph covering the partnership's ~1,200 active relationships (the client's figure, brief §2), built from all six partners' mail and calendars and kept current. (Our synthetic test graph holds about 2,400 partner–contact relationships, including cold ones.)
- A cited pre-meeting brief in under 30 seconds: who they are, our history, what was last discussed, what each side promised. Every sentence links to the email it came from.
- An alert list of warm relationships that have gone quiet, with the reason.
- Guarantees: **raw** mail, the relationship graph and the pseudonym vault stay in Hatton's region; only **masked** text (names replaced by placeholders) is sent to the AI model provider, and every outbound payload is logged; the system never emails, schedules or contacts anyone. For production, the model call moves to an in-region endpoint (a configuration change).

## 2. Two economics, priced separately (client brief §6)
| | What it is | Our cost | Proposed price |
|---|---|---|---|
| **One-off onboarding** | Backfill of ~40,000 emails (extraction + local embedding), setup, warmth calibration with partners | $1.08 AI cost (measured) + setup labour | Backfill AI cost passed through **at cost**; setup quoted separately as a fixed fee |
| **Monthly service** | Running the system: server in region, AI per brief, observability, support | $221/month normal, $223 peak (measured) | See tiers below |

## 3. Tiers (all within the $900/month budget)
| Tier | Monthly fee | Includes | Margin at normal cost |
|---|---|---|---|
| Essentials | $500 | All three features; up to 600 briefs/month; standard support | $279 (56%) |
| **Standard (recommended)** | **$700** | Essentials + monthly accuracy review of 20 briefs with a partner, warmth-rule tuning, 90-day audit trail | **$479 (68%)** |
| Sovereign | $900 | Standard + deployment in Hatton's own AWS account (raw data, graph and vault held only in Hatton's own account; the AI model provider still receives masked text only), priority support | $679 (75%)* |

*Sovereign runs on infrastructure in Hatton's account; if Hatton pays AWS directly, the fee covers our service only. Margin shown assumes we carry the infrastructure.

Why tiers: they let Hatton choose how much control it wants over where data lives (the client's top constraint) without changing the system. The software is identical; only the hosting and service level differ.

## 4. Margin logic
- **Cost is ~97% fixed** (one in-region server + observability = $214 of the $221). AI is ~$7/month at normal volume.
- So margin barely moves with volume: at 3× peak (45 briefs/day) cost rises only to $239.
- **Every price point covers the measured cost per brief at the client's stated volume** (the course's pass/fail rule): $1.32–$2.37 charged per brief vs $0.58 all-in cost per brief.
- Overage: above 600 briefs/month, each extra brief costs us ~$0.02 in AI. We would not charge overage within the budget.

## 5. Value to Hatton and break-even
- One first-meeting prep = 30–45 minutes of partner time (client brief §2). At an **assumed** $300/hour, that's **$187.50** per prep.
- At $700/month, the system pays for itself once it replaces **3.7 first-meeting preps a month**. Hatton is expected to have many more than that (assumption to confirm: ~13/month).
- Beyond prep time: the brief says the real loss is **deals missed because a relationship went quiet unnoticed**. 68% of the pipeline comes through the network; one saved deal is worth far more than a year's fee. We do not put a number on that, because we can't measure it.

## 6. Assumptions to confirm with the client
1. Partner hourly value (we used $300, conservative).
2. Number of first meetings per month (break-even needs ~4).
3. Peak mail volume (we used 2× normal = 800/day).
4. Hatton's region (decides London vs another AWS region, and whether AI calls need the 1.1× data-residency multiplier for `inference_geo: "us"`, per https://platform.claude.com/docs/en/about-claude/pricing, looked up 2026-11-27; both effects are small).

## 7. Risks to the commercial case
- If the per-brief cost rises (e.g. a stronger model), the impact is small: switching the writer to Opus 5.5 adds ~$7/month.
- If Hatton insists on self-hosted observability (Langfuse in region), replace the $29 Langfuse Cloud plan with a second server (t3.xlarge in London: $0.1888/h × 730 h ≈ $138/month, AWS price feed, 2026-11-27). Monthly cost becomes about $221 − $29 + $138 = $330; every tier still covers cost, but the Essentials margin drops to about 34% ($170).
