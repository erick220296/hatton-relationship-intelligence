"""Demo prep: decide every brief currently waiting in the review queue as a cleared test run.

Uses the same code path as a partner clicking Reject (hri.api.app.review_decide): each brief's status
becomes 'rejected' and a review_log row records decision=reject, reviewer=system and the note. Nothing is
deleted, so the audit trail shows exactly what was cleared and when.

    make clear-test-briefs            # or: python -m hri.ops.clear_test_briefs [--dry-run]
"""
from __future__ import annotations

import argparse

from fastapi import HTTPException

NOTE = "test run, cleared before demo"


def clear(brief_ids: list[int] | None = None, dry_run: bool = False) -> dict:
    from hri.api.app import ReviewDecision, review_decide, review_queue
    ids = brief_ids if brief_ids is not None else [r["brief_id"] for r in review_queue(limit=100_000)]
    done, skipped = 0, 0
    if not dry_run:
        for bid in ids:
            try:
                review_decide(bid, ReviewDecision(decision="reject", note=NOTE, reviewer="system"))
                done += 1
            except HTTPException:   # decided meanwhile (409) or gone (404)
                skipped += 1
    return {"queued": len(ids), "cleared": done, "skipped": skipped, "dry_run": dry_run}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="only count what would be cleared")
    print(clear(dry_run=ap.parse_args().dry_run))


if __name__ == "__main__":
    main()
