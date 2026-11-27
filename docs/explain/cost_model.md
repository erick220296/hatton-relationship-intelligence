# Explainer: the cost model (Commercial Lead: Yasiru)

Files: `docs/cost_model.md` (the output), `evals/cost_model.py` (the script that writes it), `docs/cost_assumptions.yaml` (your inputs: volumes, server prices, partner time, price points), `src/hri/config/prices.yaml` (AI model prices).
Regenerate any time: `uv run python -m evals.cost_model`

## The one-sentence version
The system costs us about **$221 a month** to run for Hatton (a quarter of their $900 budget). One brief costs **1.7 cents** in AI fees. Reading the 40,000 old emails once cost **$1.08**. At a **$700/month** fee we make about **68% margin**, and Hatton saves money once it replaces about **4 first-meeting preps a month**.

## Why the client asked for two separate numbers
The client brief §6 says: "a capital cost paid once" (the backfill) vs "a running cost paid every day" (briefs). A single blended number "would tell us nothing". So:
- **One-off backfill:** $1.08 measured (Eric's real Batch API run: 14,818 threads, 8.5 minutes). The estimate *before* running was $0.89. The client asked us to "say how much before you run it", and we did. Embedding all 40k messages cost $0, because the embedding model runs on our own server.
- **Per brief:** $0.0173 mean, $0.0218 at p95 (95 out of 100 briefs cost less than that). Measured from 19 real briefs (Sonnet 5.5 writes, Haiku 5.5 verifies).

## How the monthly number is built (normal month)
| Line | Amount | How |
|---|---|---|
| Brief generation + verification | $6.59 | 12.5 briefs/day × 30.4 days × $0.0173 |
| Ongoing extraction of new mail | $0.71 | 400 msgs/day × 0.4 threads/msg × $0.00015 per thread (backfill cost per thread at full price, because live mail isn't batched) |
| Embeddings | $0 | local model |
| Server (London, 4 CPU / 16 GB) | $170.16 | $0.2331/hour × 730 hours (AWS price, looked up today) |
| Storage + backups | $15 | assumption, see "weak spots" |
| Langfuse (observability) | $29 | Core plan, langfuse.com/pricing |
| **Total** | **$221.46** | Peak (15 briefs/day, 800 msgs/day): $223.48 |

**The key insight to say out loud:** the cost is almost all **fixed** (the server). The AI part is about $7 a month. So more briefs barely change the bill: at 3× peak (45 a day) the total is only $239.

## Pricing and margin (the rule that can fail you)
The course rule: "Your pricing must cover your measured cost per interaction at the client's stated volume. If it does not, the commercial section fails outright." Ours covers it at every price point:
| Fee | Margin (normal) | Hatton break-even |
|---|---|---|
| $500 | 56% | 2.7 first-meeting preps/month |
| $700 | 68% | 3.7 |
| $900 | 75% | 4.8 |

**Break-even** (the client's "point at which this stops being cheaper than the people doing it today"): one first-meeting prep is 37.5 minutes of a partner's time (client: 30–45 min). At $300/hour that's $187.50. A $700 fee ÷ $187.50 = 3.7. So if Hatton has more than about 4 first meetings a month, the system is cheaper than doing it by hand.

## The weak spots (say them before the panel does)
1. **Partner hourly value ($300) is our assumption.** The client never told us. We used a conservative number; at $400 it gets even better.
2. **Storage price for London is an assumption** ($15). AWS's page only shows example rates. It's 7% of the bill, so it can't change the conclusion.
3. **Per-brief cost is from 19 briefs**, from Eric's run on 25 Nov, before tonight's fixes. It should be refreshed from Langfuse traces after the eval and load test re-run (needs the model key).
4. **Peak mail volume (800/day) is assumed** as 2× normal.
5. **Production residency:** if Hatton needs the AI calls pinned to a region, Anthropic charges 1.1× (`inference_geo`). That adds about $0.70/month. Negligible.

## Likely panel questions
- *"Why is the server the biggest cost?"* Because everything runs on one in-region machine (database, tools, API, UI, embeddings), which keeps the data in region. The AI is pay-per-use and tiny.
- *"Could it be cheaper?"* Yes: a smaller server, or Langfuse Hobby (free). But we priced production-grade, not the cheapest.
- *"What if they ask for 10× more briefs?"* AI cost scales linearly: about $66/month at 10×. Still under budget. The $900 budget supports about 1,300 briefs a day.
- *"Where does $0.0173 come from?"* Tokens counted on real calls × the price per million tokens from Anthropic's pricing page, looked up 27 Nov 2026.
