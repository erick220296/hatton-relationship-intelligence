"""Cost model built from MEASURED data (Batch results, brief eval, load test, egress audit),
not guesses. Writes docs/cost_model.md. Re-run after every evidence refresh.

Inputs (all produced by the system itself):
  docs/evidence/backfill_report.json   extract_actual (tokens + $ for the one-off backfill)
  docs/evidence/brief_eval.json        per-brief cost/latency distribution
  docs/evidence/load_c*.json           cost under load
  egress_audit table                   per-call token usage for ongoing extraction
  src/hri/config/prices.yaml           unit prices with source + date
  docs/cost_assumptions.yaml           infra prices + partner-time value (Commercial Lead owns)

    python -m evals.cost_model
"""
from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

import yaml

from hri import db
from hri.config.settings import cost_usd, prices, settings

EV = Path("docs/evidence")
ASSUME = Path("docs/cost_assumptions.yaml")
DAYS = 30.4


def _json(p):
    p = EV / p
    return json.loads(p.read_text()) if p.exists() else None


def measured_ongoing_extraction_per_thread() -> dict:
    rows = db.q("""SELECT response_body FROM egress_audit WHERE purpose = 'extract' AND allowed AND status = 200
                   ORDER BY id DESC LIMIT 500""")
    tin, tout = [], []
    for r in rows:
        try:
            u = json.loads(r["response_body"])["usage"]
            tin.append(u["input_tokens"])
            tout.append(u["output_tokens"])
        except Exception:
            continue
    if not tin:
        return {"n": 0}
    m = settings().extract_model
    return {"n": len(tin), "tokens_in": statistics.mean(tin), "tokens_out": statistics.mean(tout),
            "usd": cost_usd(m, statistics.mean(tin), statistics.mean(tout))}


def main():
    a = yaml.safe_load(ASSUME.read_text())
    bf = (_json("backfill_report.json") or {}).get("extract_actual", {})
    be = (_json("brief_eval.json") or {}).get("results", [])
    brief_costs = [r["cost_usd"] for r in be if r.get("mode") == "llm" and r.get("cost_usd")]
    for f in sorted(EV.glob("load_c*.json")):
        brief_costs += [x["cost"] for x in json.loads(f.read_text())["results"] if x.get("mode") == "llm"]
    per_brief = statistics.mean(brief_costs) if brief_costs else None
    p95_brief = sorted(brief_costs)[int(0.95 * (len(brief_costs) - 1))] if brief_costs else None
    ex = measured_ongoing_extraction_per_thread()
    if not ex.get("n") and bf.get("cost_usd") and bf.get("threads"):
        # No live (sync) extraction calls logged yet: derive the per-thread cost from the measured Batch
        # backfill and remove the Batch discount, because ongoing extraction runs unbatched.
        ex = {"n": 0, "usd": bf["cost_usd"] / bf["threads"] / prices()["batch_discount"],
              "basis": f"derived from the measured backfill (${bf['cost_usd']} / {bf['threads']:,} threads) "
                       f"at full (non-batch) price"}

    vol = a["volume"]
    infra = a["infrastructure"]
    infra_month = sum(x["usd_per_month"] for x in infra.values())

    def month(briefs_per_day, msgs_per_day):
        threads_per_day = msgs_per_day * vol["threads_per_message"]
        llm_briefs = briefs_per_day * DAYS * (per_brief or 0)
        llm_extract = threads_per_day * DAYS * ex.get("usd", 0)
        return {"briefs": briefs_per_day * DAYS, "llm_briefs": llm_briefs, "llm_extract": llm_extract,
                "embeddings": 0.0, "infra": infra_month, "total": llm_briefs + llm_extract + infra_month}

    normal = month(vol["briefs_per_day_normal"], vol["messages_per_day_normal"])
    peak = month(vol["briefs_per_day_peak"], vol["messages_per_day_peak"])
    partner_hr = a["partner_time"]["usd_per_hour"]
    minutes_saved = a["partner_time"]["minutes_saved_per_brief"]
    value_per_brief = partner_hr * minutes_saved / 60
    # all-in cost per brief = monthly total / briefs delivered
    allin_normal = normal["total"] / normal["briefs"]
    # break-even: briefs/month at which system cost == value of partner time displaced
    breakeven_briefs = infra_month / max(1e-9, value_per_brief - (per_brief or 0))
    budget = a["client_budget_usd_per_month"]
    # volume at which we exceed the $900 budget (LLM briefs only scaling)
    headroom_briefs_per_day = (budget - infra_month - normal["llm_extract"]) / DAYS / max(1e-9, per_brief or 1)

    def src(model):
        p = prices()["models"][model]
        return f"${p['input_per_mtok']}/${p['output_per_mtok']} per MTok in/out · {p['source']} · looked up {p['looked_up']}"

    lines = [
        "# Cost model: Hatton Relationship Intelligence",
        f"_Generated {date.today()} by `python -m evals.cost_model` from measured evidence. Owner: Commercial Lead._",
        "Inputs: `docs/evidence/backfill_report.json` (measured backfill), `docs/evidence/brief_eval.json` + "
        "`docs/evidence/load_c*.json` (measured per-brief cost), `egress_audit` table (ongoing extraction), "
        "`src/hri/config/prices.yaml` (model prices), `docs/cost_assumptions.yaml` (volumes, infrastructure, partner time).",
        "", "## Unit prices (source and date)",
        f"- Extraction model `{settings().extract_model}`: {src(settings().extract_model)}; Batch API discount ×{prices()['batch_discount']}",
        f"- Brief model `{settings().brief_model}`: {src(settings().brief_model)}",
        f"- Verifier `{settings().verify_model}`: {src(settings().verify_model)}",
        "- Embeddings: local `bge-small-en-v1.5`, $0 API (compute included in infrastructure)",
    ] + [f"- {k}: ${v['usd_per_month']}/month · {v['source']} · looked up {v['looked_up']}" for k, v in infra.items()] + [
        f"- Partner time: ${partner_hr}/hour ({a['partner_time']['source']})",
        "", "## 1. One-off backfill (capital cost, priced before running)",
        "| | Tokens in | Tokens out | Cost |", "|---|---|---|---|",
        f"| Estimate (before run) | {(_json('backfill_report.json') or {}).get('extract_estimate', {}).get('est_input_tokens', '?'):,} | "
        f"{(_json('backfill_report.json') or {}).get('extract_estimate', {}).get('est_output_tokens', '?'):,} | "
        f"${(_json('backfill_report.json') or {}).get('extract_estimate', {}).get('est_cost_usd_batch', '?')} |",
        f"| **Actual** ({bf.get('threads', '?'):,} threads, Batch API, {bf.get('wall_minutes', '?')} min) | "
        f"{bf.get('tokens_in', 0):,} | {bf.get('tokens_out', 0):,} | **${bf.get('cost_usd', '?')}** |",
        "| Embedding 40k messages (local) | – | – | $0.00 API |",
        "", "## 2. Running cost per unit (measured)",
        f"- **One pre-meeting brief:** mean **${per_brief:.4f}**, p95 ${p95_brief:.4f} (n={len(brief_costs)} measured briefs: generate + verify; "
        f"measured in the 25 Nov eval run on the code *before* the 27 Nov fixes, `docs/evidence/brief_eval.json`)"
        if per_brief else "- One pre-meeting brief: _run `make eval` / `make load` first_",
        f"- Ongoing extraction per new thread (sync, unbatched): ${ex.get('usd', 0):.5f} "
        + (f"({ex['basis']})" if ex.get("basis") else f"(n={ex.get('n')} measured calls)"),
        f"- Masking: in-process, $0 marginal; observability (Langfuse) in infrastructure",
        "", "## 3. Monthly cost", "| | Normal | Peak |", "|---|---|---|",
        f"| Briefs/month | {normal['briefs']:.0f} | {peak['briefs']:.0f} |",
        f"| LLM: brief generation + verification | ${normal['llm_briefs']:.2f} | ${peak['llm_briefs']:.2f} |",
        f"| LLM: entity/commitment extraction (ongoing) | ${normal['llm_extract']:.2f} | ${peak['llm_extract']:.2f} |",
        "| Embeddings (local) | $0.00 | $0.00 |",
        f"| Infrastructure + observability | ${infra_month:.2f} | ${infra_month:.2f} |",
        f"| **Total** | **${normal['total']:.2f}** | **${peak['total']:.2f}** |",
        f"| vs client budget ${budget} | {normal['total'] / budget:.0%} | {peak['total'] / budget:.0%} |",
        "", "## 4. Value and break-even",
        f"- Partner time displaced per brief: {minutes_saved} min × ${partner_hr}/h = **${value_per_brief:.2f}**",
        f"- All-in cost per brief at normal volume (incl. fixed infra): **${allin_normal:.2f}**",
        f"- Break-even: system is cheaper than the people doing it today above **{breakeven_briefs:.0f} briefs/month** "
        f"({breakeven_briefs / DAYS:.1f}/day).",
        f"- It stops being cheaper only below that volume; on the variable side a brief costs "
        f"${per_brief or 0:.4f} vs ${value_per_brief:.2f} of partner time, so it never stops being cheaper as volume rises.",
        f"- Budget ceiling: at current unit cost the $900/month budget supports ~{headroom_briefs_per_day:,.0f} briefs/day.",
        "", "## 5. Price, margin and coverage of measured cost",
        "All-in monthly fee to Hatton (backfill billed once, separately, at cost plus setup).",
        "| Price / month | Margin at normal cost | Margin at peak cost | Price per brief at normal volume | Covers measured cost? | Hatton break-even (first-meeting preps replaced / month) |",
        "|---|---|---|---|---|---|",
    ] + [
        f"| ${p:,} | ${p - normal['total']:,.2f} ({(p - normal['total']) / p:.0%}) | "
        f"${p - peak['total']:,.2f} ({(p - peak['total']) / p:.0%}) | ${p / normal['briefs']:.2f} vs "
        f"${allin_normal:.2f} all-in cost | {'yes' if p > peak['total'] else 'NO'} | {p / value_per_brief:.1f} |"
        for p in a.get("pricing", {}).get("price_points_usd_per_month", [])
    ] + [
        "",
        f"Hatton break-even = monthly fee ÷ ${value_per_brief:.2f} of partner time per first-meeting prep. "
        "Only first meetings are counted (the client's 30–45 min figure is for first meetings), so this is conservative: "
        "below that many first meetings a month, the fee costs Hatton more than the partner time it saves.",
        f"Measured per-brief cost comes from n={len(brief_costs)} briefs; re-run `make eval` and `make load` "
        "to refresh it from Langfuse-traced runs.",
        "", "## 6. Sensitivity", "| Change | Effect on normal monthly total |", "|---|---|",
        f"| Stress volume: {vol.get('briefs_per_day_stress', 45)} briefs/day (3× peak, load-test level) | "
        f"total ${month(vol.get('briefs_per_day_stress', 45), vol['messages_per_day_peak'])['total']:.2f}/month |",
        f"| Partner time valued at $400/h instead of ${partner_hr}/h | value per brief "
        f"${400 * minutes_saved / 60:.2f}; break-even {infra_month / max(1e-9, 400 * minutes_saved / 60 - (per_brief or 0)):.1f} briefs/month |",
        f"| Brief model → Opus 5.5 (2× Sonnet price) | +${normal['llm_briefs']:.2f} |",
        f"| No Batch API for backfill | +${bf.get('cost_usd', 0):.2f} one-off |",
        f"| OpenAI embeddings instead of local (~{vol['messages_per_day_normal']}/day × ~400 tokens) | "
        f"+${vol['messages_per_day_normal'] * DAYS * 400 / 1e6 * prices()['embeddings']['openai_text_embedding_3_small']['per_mtok']:.2f} (and data leaves region) |",
    ]
    Path("docs/cost_model.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
