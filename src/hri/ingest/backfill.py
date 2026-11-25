"""One-off backfill of the full corpus.

    python -m hri.ingest.backfill load        # contacts, messages, calendar -> graph (no LLM)
    python -m hri.ingest.backfill embed       # local embeddings (in-region, $0 API cost)
    python -m hri.ingest.backfill edges       # warmth / quiet scores
    python -m hri.ingest.backfill estimate    # price the LLM extraction BEFORE running it
    python -m hri.ingest.backfill extract [--limit N] [--sync]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from hri import db
from hri.graph import warmth
from hri.ingest.store import Store

CORPUS = Path("data/corpus")
REPORT = Path("docs/evidence/backfill_report.json")


def _report(key: str, value):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    data[key] = value
    REPORT.write_text(json.dumps(data, indent=1, default=str))


def load():
    t0 = time.time()
    st = Store()
    st.load_contacts([json.loads(l) for l in open(CORPUS / "contacts.jsonl")])
    n = 0
    for line in open(CORPUS / "messages.jsonl"):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            st.quarantined.append({"id": None, "reason": f"invalid json: {e}"})
            continue
        if st.add_message(raw):
            n += 1
        if n and n % 5000 == 0:
            print(f"  {n} messages")
    for line in open(CORPUS / "calendar.jsonl"):
        st.add_meeting(json.loads(line))
    st.save_quarantine()
    counts = db.q1("""SELECT (SELECT count(*) FROM message) AS messages, (SELECT count(*) FROM person) AS people,
                             (SELECT count(*) FROM org) AS orgs, (SELECT count(*) FROM meeting) AS meetings""")
    out = {**counts, "skipped_bulk": st.skipped_bulk, "quarantined": st.quarantined,
           "seconds": round(time.time() - t0, 1)}
    _report("load", out)
    print(json.dumps(out, indent=1, default=str))


def embed(batch: int = 256):
    from hri.ingest.embed import embed_texts
    t0, n = time.time(), 0
    while True:
        rows = db.q("SELECT id, subject, body FROM message WHERE embedding IS NULL LIMIT %s", (batch,))
        if not rows:
            break
        vecs = embed_texts([f"{r['subject']}\n{r['body'][:2000]}" for r in rows])
        with db.conn() as c, c.cursor() as cur:
            cur.executemany("UPDATE message SET embedding = %s WHERE id = %s",
                            [(v, r["id"]) for v, r in zip(vecs, rows)])
        n += len(rows)
        if n % 5120 < batch:
            print(f"  embedded {n}")
    out = {"embedded": n, "seconds": round(time.time() - t0, 1), "model": "BAAI/bge-small-en-v1.5 (local)"}
    _report("embed", out)
    print(out)


def edges():
    t0 = time.time()
    n = warmth.recompute()
    stats = db.q("SELECT status, count(*) AS n FROM edge GROUP BY status ORDER BY n DESC")
    out = {"edges": n, "by_status": {r["status"]: r["n"] for r in stats}, "seconds": round(time.time() - t0, 1)}
    _report("edges", out)
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["load", "embed", "edges", "estimate", "extract", "all"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sync", action="store_true")
    a = ap.parse_args()
    from hri.ingest import extract as ex
    if a.step in ("load", "all"):
        load()
    if a.step in ("embed", "all"):
        embed()
    if a.step in ("edges", "all"):
        edges()
    if a.step in ("estimate", "all"):
        est = ex.estimate()
        _report("extract_estimate", est)
        print(json.dumps(est, indent=1))
    if a.step == "extract":
        if a.sync:
            res = [ex.extract_sync(t) for t in ex._threads_needing_extraction(a.limit or 20)]
            out = {"threads": len(res), "cost_usd": round(sum(r["cost_usd"] for r in res), 4)}
        else:
            out = ex.extract_batch(a.limit)
        _report("extract_actual", out)
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
