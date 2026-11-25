"""Keep the graph current: replay post-backfill mail (~400/day) and update incrementally.

For each new message: parse/quarantine -> store -> embed -> recompute only the
edges it touches -> re-extract commitments for its thread (sync call, masked).

    python -m hri.ingest.live_feed --file data/corpus/live.jsonl [--limit 200] [--no-extract]
"""
from __future__ import annotations

import argparse
import json
import time

from hri import clock, db
from hri.graph import warmth
from hri.ingest.embed import embed_texts
from hri.ingest.store import Store


def process(records: list[dict], extract: bool = True) -> dict:
    t0 = time.time()
    st = Store()
    added = [m for m in (st.add_message(r) for r in records) if m]
    if added:
        vecs = embed_texts([f"{m.subject}\n{m.body[:2000]}" for m in added])
        with db.conn() as c, c.cursor() as cur:
            cur.executemany("UPDATE message SET embedding=%s WHERE id=%s", [(v, m.id) for v, m in zip(vecs, added)])
    partners = {r["id"] for r in db.q("SELECT id FROM person WHERE is_partner")}
    pairs = set()
    for m in added:
        ids = [st.person_ids[m.sender.email]] + [st.person_ids[a.email] for a in m.to + m.cc]
        for a in ids:
            for b in ids:
                if a in partners and b not in partners:
                    pairs.add((a, b))
    before = {(r["partner_id"], r["person_id"]): r["status"] for r in
              db.q("SELECT partner_id, person_id, status FROM edge")} if pairs else {}
    if added:
        clock.advance(max(m.sent_at for m in added))
    warmth.recompute(only_pairs=pairs)
    after = {(r["partner_id"], r["person_id"]): r["status"] for r in
             db.q("SELECT partner_id, person_id, status FROM edge")} if pairs else {}
    changes = [{"pair": k, "from": before.get(k), "to": after.get(k)} for k in pairs if before.get(k) != after.get(k)]
    extracted, cost = 0, 0.0
    if extract:
        from hri.ingest.extract import extract_sync
        from hri.llm import LLMUnavailable
        for tid in sorted({m.thread_id for m in added}):
            try:
                r = extract_sync(tid)
                extracted += 1
                cost += r["cost_usd"]
            except LLMUnavailable:
                break   # graph stays current; extraction catches up on the next run
    st.save_quarantine()
    return {"received": len(records), "stored": len(added), "quarantined": len(st.quarantined),
            "edges_touched": len(pairs), "status_changes": changes, "threads_extracted": extracted,
            "extract_cost_usd": round(cost, 5), "seconds": round(time.time() - t0, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/corpus/live.jsonl")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-extract", action="store_true")
    a = ap.parse_args()
    recs = [json.loads(l) for l in open(a.file)][: a.limit]
    print(json.dumps(process(recs, extract=not a.no_extract), indent=1, default=str))


if __name__ == "__main__":
    main()
