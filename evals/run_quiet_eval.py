"""DoD #3: quiet-relationship detection finds the relationships deliberately cooled in the corpus.

Compares edge.status == 'quiet' against ground_truth.json. Positives = trajectory 'cooling'.
Dead deals (we passed, then silence) are reported separately: flagging them is defensible,
so they are excluded from both precision and recall and shown on their own line.

    python -m evals.run_quiet_eval   -> docs/evidence/quiet_eval.json + console table
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from hri import db

GT = Path("data/corpus/ground_truth.json")
OUT = Path("docs/evidence/quiet_eval.json")


def main():
    gt = json.loads(GT.read_text())
    status = {(r["pe"], r["ce"]): r["status"] for r in db.q("""
        SELECT pa.email AS pe, p.email AS ce, e.status FROM edge e
        JOIN person pa ON pa.id = e.partner_id JOIN person p ON p.id = e.person_id""")}
    traj = {(r["partner"], r["person"]): r["trajectory"] for r in gt["relationships"]}
    flagged = {k for k, s in status.items() if s == "quiet"}
    cooled = {k for k, t in traj.items() if t == "cooling"}
    died = {k for k, t in traj.items() if t == "deal_died"}
    tp = flagged & cooled
    fp = flagged - cooled - died
    fn = cooled - flagged
    precision = len(tp) / max(1, len(flagged - died))
    recall = len(tp) / max(1, len(cooled))
    confusion = Counter((traj.get(k, "unknown"), s) for k, s in status.items())
    res = {
        "cooled_in_corpus": len(cooled), "flagged_quiet": len(flagged), "true_positives": len(tp),
        "false_positives": len(fp), "false_negatives": len(fn),
        "precision": round(precision, 3), "recall": round(recall, 3),
        "f1": round(2 * precision * recall / max(1e-9, precision + recall), 3),
        "dead_deals_flagged": f"{len(flagged & died)}/{len(died)}",
        "confusion": {f"{t} -> {s}": n for (t, s), n in sorted(confusion.items())},
        "missed": [{"partner": k[0], "person": k[1], "status_given": status.get(k, "no edge"),
                    "change_point": next(r["change_point"] for r in gt["relationships"]
                                         if (r["partner"], r["person"]) == k)} for k in sorted(fn)],
        "false_positive_examples": [{"partner": k[0], "person": k[1], "trajectory": traj.get(k)} for k in sorted(fp)][:15],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    print(f"cooled={len(cooled)} flagged={len(flagged)} TP={len(tp)} FP={len(fp)} FN={len(fn)} "
          f"precision={precision:.3f} recall={recall:.3f} dead deals flagged={res['dead_deals_flagged']}")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
