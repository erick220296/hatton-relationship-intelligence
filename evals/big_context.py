"""Failure demo: 'exceed the context'. Briefs the heaviest relationship in the corpus and prints
what the context budgeter kept, compressed and dropped."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from hri import db
from hri.config.settings import settings

gt = json.loads(Path("data/corpus/ground_truth.json").read_text())
h = gt["heavy"]
p = db.q1("SELECT id, name FROM person WHERE email = %s", (h["person"],))
n = db.q1("""SELECT count(DISTINCT m.id) n, count(DISTINCT m.thread_id) t, sum(length(m.body)) chars FROM message m
             LEFT JOIN message_recipient r ON r.message_id = m.id WHERE m.from_id = %(p)s OR r.person_id = %(p)s""",
          {"p": p["id"]})
print(f"{p['name']}: {n['n']} messages in {n['t']} threads, ~{n['chars'] // 4:,} tokens of raw history; "
      f"context budget {settings().context_budget_tokens:,} tokens")
res = httpx.post(f"{settings().api_url}/brief", json={"person_id": p["id"], "partner_email": h["partner"]},
                 timeout=90).json()
print(json.dumps({"mode": res["mode"], "latency_ms": res["latency_ms"], "cost_usd": res["cost_usd"],
                  "context": (res.get("brief") or {}).get("context_stats"), "warnings": res["warnings"]}, indent=1))
