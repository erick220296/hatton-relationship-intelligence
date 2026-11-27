# Cost model: Hatton Relationship Intelligence
_Generated 2026-11-27 by `python -m evals.cost_model` from measured evidence. Owner: Commercial Lead._
Inputs: `docs/evidence/backfill_report.json` (measured backfill), `docs/evidence/brief_eval.json` + `docs/evidence/load_c*.json` (measured per-brief cost), `egress_audit` table (ongoing extraction), `src/hri/config/prices.yaml` (model prices), `docs/cost_assumptions.yaml` (volumes, infrastructure, partner time).

## Unit prices (source and date)
- Extraction model `claude-haiku-5-5`: $0.1/$0.5 per MTok in/out · https://platform.claude.com/docs/en/about-claude/pricing · looked up 2026-11-27; Batch API discount ×0.5
- Brief model `claude-sonnet-5-5`: $2.0/$10.0 per MTok in/out · https://platform.claude.com/docs/en/about-claude/pricing · looked up 2026-11-27
- Verifier `claude-haiku-5-5`: $0.1/$0.5 per MTok in/out · https://platform.claude.com/docs/en/about-claude/pricing · looked up 2026-11-27
- Embeddings: local `bge-small-en-v1.5`, $0 API (compute included in infrastructure)
- vm_in_region: $170.16/month · AWS on-demand Linux price for m7i.xlarge in EU (London), from AWS's public price feed behind https://aws.amazon.com/ec2/pricing/on-demand/ (us-east-1: $0.2016/h) · looked up 2026-11-27
- storage_and_backups: $15/month · ASSUMPTION: https://aws.amazon.com/ebs/pricing/ worked examples use gp3 $0.08/GB-month and snapshots $0.05/GB-month, region not named (100x0.08 + 100x0.05 = $13); rounded up to $15 for London. Exact London rate not confirmed. · looked up 2026-11-27
- langfuse: $29/month · https://langfuse.com/pricing (Core $29/month; Hobby is free with 50k units/month and 30-day access) · looked up 2026-11-27
- Partner time: $300/hour (client brief §2 (minutes); hourly value is an ASSUMPTION pending client answer)

## 1. One-off backfill (capital cost, priced before running)
| | Tokens in | Tokens out | Cost |
|---|---|---|---|
| Estimate (before run) | 14,390,678 | 1,653,009 | $1.13 |
| **Actual** (15,618 threads, Batch API, 5.6 min) | 14,338,291 | 1,498,762 | **$1.0916** |
| Embedding 40k messages (local) | – | – | $0.00 API |

## 2. Running cost per unit (measured)
- **One pre-meeting brief:** mean **$0.0197**, p95 $0.0231 (n=87 measured briefs: generate + verify; real-model runs on 27 Nov after the fixes: brief eval `docs/evidence/brief_eval.json` + load tests `docs/evidence/load_c*.json`)
- Ongoing extraction per new thread (sync, unbatched): $0.00014 (derived from the measured backfill ($1.0916 / 15,618 threads) at full (non-batch) price)
- Masking: in-process, $0 marginal; observability (Langfuse) in infrastructure

## 3. Monthly cost
| | Normal | Peak |
|---|---|---|
| Briefs/month | 380 | 456 |
| LLM: brief generation + verification | $7.50 | $9.00 |
| LLM: entity/commitment extraction (ongoing) | $0.68 | $1.36 |
| Embeddings (local) | $0.00 | $0.00 |
| Infrastructure + observability | $214.16 | $214.16 |
| **Total** | **$222.34** | **$224.52** |
| vs client budget $900 | 25% | 25% |

## 4. Value and break-even
- Partner time displaced per brief: 37.5 min × $300/h = **$187.50**
- All-in cost per brief at normal volume (incl. fixed infra): **$0.59**
- Break-even: system is cheaper than the people doing it today above **1 briefs/month** (0.0/day).
- It stops being cheaper only below that volume; on the variable side a brief costs $0.0197 vs $187.50 of partner time, so it never stops being cheaper as volume rises.
- Budget ceiling: at current unit cost the $900/month budget supports ~1,142 briefs/day.

## 5. Price, margin and coverage of measured cost
All-in monthly fee to Hatton (backfill billed once, separately, at cost plus setup).
| Price / month | Margin at normal cost | Margin at peak cost | Price per brief at normal volume | Covers measured cost? | Hatton break-even (first-meeting preps replaced / month) |
|---|---|---|---|---|---|
| $500 | $277.66 (56%) | $275.48 (55%) | $1.32 vs $0.59 all-in cost | yes | 2.7 |
| $700 | $477.66 (68%) | $475.48 (68%) | $1.84 vs $0.59 all-in cost | yes | 3.7 |
| $900 | $677.66 (75%) | $675.48 (75%) | $2.37 vs $0.59 all-in cost | yes | 4.8 |

Hatton break-even = monthly fee ÷ $187.50 of partner time per first-meeting prep. Only first meetings are counted (the client's 30–45 min figure is for first meetings), so this is conservative: below that many first meetings a month, the fee costs Hatton more than the partner time it saves.
Measured per-brief cost comes from n=87 real briefs (27 Nov, after the fixes, each one also traced in Langfuse); re-run `make eval` and `make load` to refresh it.

## 6. Sensitivity
| Change | Effect on normal monthly total |
|---|---|
| Stress volume: 45 briefs/day (3× peak, load-test level) | total $242.51/month |
| Partner time valued at $400/h instead of $300/h | value per brief $250.00; break-even 0.9 briefs/month |
| Brief model → Opus 5.5 (2× Sonnet price) | +$7.50 |
| No Batch API for backfill | +$1.09 one-off |
| OpenAI embeddings instead of local (~400/day × ~400 tokens) | +$0.10 (and data leaves region) |
