"""Load test: the morning burst. The client asks for 10-15 briefs/day, mostly the morning before
meetings, so the realistic peak is all 15 at once; we also run 3x that.

    python -m load.run_load --concurrency 15 --requests 45 [--fresh]
      -> docs/evidence/load_<concurrency>.json  (p50/p95/p99, error rate, cost under load)

Requests target real people from the graph (warm relationships) with fresh=True so no cache hides latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from hri import db
from hri.config.settings import settings

OUT = Path("docs/evidence")


async def main_async(concurrency: int, n: int):
    targets = db.q("""SELECT e.person_id, pa.email AS partner FROM edge e JOIN person pa ON pa.id = e.partner_id
                      WHERE e.status IN ('warm','quiet') ORDER BY e.warmth DESC LIMIT %s""", (n,))
    sem = asyncio.Semaphore(concurrency)
    results = []

    # the header tags these briefs "load-test" in Langfuse, so the latency chart can filter them
    async with httpx.AsyncClient(base_url=settings().api_url, timeout=120,
                                 headers={"X-HRI-Run-Type": "load-test"}) as http:
        async def one(t):
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await http.post("/brief", json={"person_id": t["person_id"], "partner_email": t["partner"]})
                    body = r.json()
                    ok = r.status_code == 200 and body.get("mode") in ("llm", "fallback")
                    results.append({"ms": int((time.perf_counter() - t0) * 1000), "ok": ok, "mode": body.get("mode"),
                                    "status": r.status_code, "cost": body.get("cost_usd") or 0})
                except Exception as e:
                    results.append({"ms": int((time.perf_counter() - t0) * 1000), "ok": False, "mode": "error",
                                    "status": None, "error": repr(e), "cost": 0})
        t_start = time.perf_counter()
        await asyncio.gather(*(one(t) for t in targets))
        wall = time.perf_counter() - t_start

    lat = sorted(r["ms"] for r in results)
    pct = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))]
    modes = {}
    for r in results:
        modes[r["mode"]] = modes.get(r["mode"], 0) + 1
    summary = {"run_at": datetime.utcnow().isoformat() + "Z", "concurrency": concurrency, "requests": len(results),
               "wall_seconds": round(wall, 1), "throughput_per_min": round(len(results) / wall * 60, 1),
               "p50_ms": pct(0.5), "p95_ms": pct(0.95), "p99_ms": pct(0.99), "max_ms": lat[-1],
               "within_30s": f"{sum(1 for x in lat if x <= 30000)}/{len(lat)}",
               "error_rate": round(sum(not r["ok"] for r in results) / len(results), 3), "modes": modes,
               "total_cost_usd": round(sum(r["cost"] for r in results), 4),
               "cost_per_brief_usd": round(sum(r["cost"] for r in results) / max(1, sum(r["mode"] == "llm" for r in results)), 5)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"load_c{concurrency}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=15)
    ap.add_argument("--requests", type=int, default=45)
    a = ap.parse_args()
    asyncio.run(main_async(a.concurrency, a.requests))


if __name__ == "__main__":
    main()
