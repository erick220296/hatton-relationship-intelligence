"""Deterministic, explainable warmth score and quiet-relationship detection.

Warmth for a (partner, counterparty) edge at time t:
    raw(t)   = sum_i  w(type_i) * 0.5 ** ((t - t_i) / half_life)
    score(t) = 100 * (1 - exp(-raw(t) / scale))
where interactions are meetings, inbound mail (they wrote to us) and outbound
mail (we wrote to them). Inbound and meetings weigh more: reciprocity is the
signal, not our own outreach.

Quiet = the edge WAS warm (peak score over the lookback >= warm_threshold)
AND days since the last two-way contact (inbound or meeting) exceed
max(kind_min_gap, gap_multiplier * the edge's own median gap).
Using each edge's own cadence means a quarterly LP isn't flagged after a month,
while a banker who used to write weekly is flagged after a few weeks.

All parameters come from the vertical config (pe.yaml), so the partners can
tune what "warm" means for them without code changes.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime

from hri import clock, db
from hri.config.settings import vertical

LN2 = math.log(2)


def _interactions(partner_ids: set[int], only_pairs: set[tuple[int, int]] | None = None,
                  as_of: datetime | None = None):
    """-> {(partner_id, person_id): [(t, kind)]} with kind in meeting|inbound|outbound."""
    as_of = as_of or clock.now()
    out: dict[tuple[int, int], list] = defaultdict(list)
    rows = db.q("""SELECT m.sent_at AS t, m.from_id, r.person_id AS rcpt
                   FROM message m JOIN message_recipient r ON r.message_id = m.id
                   WHERE m.sent_at <= %s""", (as_of,))
    for r in rows:
        a, b = r["from_id"], r["rcpt"]
        if a in partner_ids and b not in partner_ids:
            out[(a, b)].append((r["t"], "outbound"))
        elif b in partner_ids and a not in partner_ids:
            out[(b, a)].append((r["t"], "inbound"))
    rows = db.q("""SELECT mt.start_at AS t, a1.person_id AS p, a2.person_id AS c
                   FROM meeting mt JOIN meeting_attendee a1 ON a1.meeting_id = mt.id
                   JOIN meeting_attendee a2 ON a2.meeting_id = mt.id
                   WHERE mt.status = 'confirmed' AND mt.start_at <= %s
                     AND a1.person_id = ANY(%s) AND NOT (a2.person_id = ANY(%s))""",
                (as_of, list(partner_ids), list(partner_ids)))
    for r in rows:
        out[(r["p"], r["c"])].append((r["t"], "meeting"))
    if only_pairs is not None:
        out = {k: v for k, v in out.items() if k in only_pairs}
    return out


def score_edge(events: list[tuple[datetime, str]], as_of: datetime, kind: str | None, cfg: dict) -> dict:
    w, hl, scale = cfg["warmth"]["weights"], cfg["warmth"]["half_life_days"], cfg["warmth"]["scale"]
    thr = cfg["warmth"]["warm_threshold"]
    events = sorted(events)

    def score_at(t: datetime) -> float:
        raw = sum(w[k] * math.exp(-LN2 * (t - ti).total_seconds() / 86400 / hl) for ti, k in events if ti <= t)
        return 100 * (1 - math.exp(-raw / scale))

    lookback = cfg["warmth"]["peak_lookback_days"]
    recent = [ti for ti, _ in events if (as_of - ti).days <= lookback]
    # score peaks right after an interaction; sample at each (cap work on very heavy edges)
    sample = recent if len(recent) <= 400 else recent[:: max(1, len(recent) // 400)]
    peak = max([score_at(t) for t in sample] + [0.0])
    now = score_at(as_of)

    two_way = sorted(ti for ti, k in events if k in ("inbound", "meeting"))
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(two_way, two_way[1:]) if (b - a).days >= 1]
    median_gap = statistics.median(gaps) if gaps else None
    last_two_way = two_way[-1] if two_way else None
    days_silent = (as_of - last_two_way).days if last_two_way else None

    q = cfg["quiet"]
    min_gap = q["kind_min_gap_days"].get(kind or "other", q["min_gap_days"])
    allowed_gap = max(min_gap, q["gap_multiplier"] * median_gap) if median_gap else min_gap

    first = events[0][0] if events else None
    # "established": enough two-way history that silence means something
    recent_two_way = [t for t in two_way if (as_of - t).days <= lookback]
    established = len(recent_two_way) >= q.get("min_two_way", 4) and \
        (recent_two_way[-1] - recent_two_way[0]).days >= q.get("min_span_days", 90) if recent_two_way else False
    if first and (as_of - first).days < 30:
        status = "new"
    elif established and peak >= thr and days_silent is not None and days_silent > allowed_gap:
        status = "quiet"
    elif now >= thr:
        status = "warm"
    elif peak >= thr:
        status = "cooling"
    else:
        status = "cold"
    return {
        "first_contact": first, "last_contact": events[-1][0] if events else None, "last_inbound": last_two_way,
        "n_inbound": sum(1 for _, k in events if k == "inbound"),
        "n_outbound": sum(1 for _, k in events if k == "outbound"),
        "n_meetings": sum(1 for _, k in events if k == "meeting"),
        "warmth": round(now, 1), "warmth_peak": round(peak, 1), "median_gap_days": median_gap, "status": status,
        "explanation": {"established": established, "days_since_two_way_contact": days_silent, "allowed_gap_days": round(allowed_gap, 1),
                        "median_gap_days": round(median_gap, 1) if median_gap else None,
                        "peak": round(peak, 1), "now": round(now, 1), "threshold": thr,
                        "unanswered_outbound_since": sum(1 for ti, k in events
                                                         if k == "outbound" and last_two_way and ti > last_two_way)},
    }


def recompute(only_pairs: set[tuple[int, int]] | None = None, as_of: datetime | None = None) -> int:
    as_of = as_of or clock.now()
    cfg = vertical()
    partners = {r["id"] for r in db.q("SELECT id FROM person WHERE is_partner")}
    kinds = {r["id"]: r["kind"] for r in db.q("SELECT p.id, o.kind FROM person p LEFT JOIN org o ON o.id=p.org_id")}
    edges = _interactions(partners, only_pairs, as_of)
    rows = []
    for (pa, pe), ev in edges.items():
        s = score_edge(ev, as_of, kinds.get(pe), cfg)
        rows.append((pa, pe, s["first_contact"], s["last_contact"], s["last_inbound"], s["n_inbound"],
                     s["n_outbound"], s["n_meetings"], s["warmth"], s["warmth_peak"], s["median_gap_days"],
                     s["status"], json.dumps(s["explanation"])))
    with db.conn() as c, c.cursor() as cur:
        cur.executemany("""
            INSERT INTO edge (partner_id, person_id, first_contact, last_contact, last_inbound, n_inbound,
                              n_outbound, n_meetings, warmth, warmth_peak, median_gap_days, status, explanation,
                              updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (partner_id, person_id) DO UPDATE SET
              first_contact=EXCLUDED.first_contact, last_contact=EXCLUDED.last_contact,
              last_inbound=EXCLUDED.last_inbound, n_inbound=EXCLUDED.n_inbound, n_outbound=EXCLUDED.n_outbound,
              n_meetings=EXCLUDED.n_meetings, warmth=EXCLUDED.warmth, warmth_peak=EXCLUDED.warmth_peak,
              median_gap_days=EXCLUDED.median_gap_days, status=EXCLUDED.status,
              explanation=EXCLUDED.explanation, updated_at=now()""", rows)
    return len(rows)


def quiet_list(partner_email: str | None = None, limit: int = 200) -> list[dict]:
    return db.q("""
        SELECT pa.name AS partner, pa.email AS partner_email, p.name, p.email, p.title, o.name AS org, o.kind,
               e.warmth, e.warmth_peak, e.last_inbound, e.explanation
        FROM edge e JOIN person pa ON pa.id = e.partner_id JOIN person p ON p.id = e.person_id
        LEFT JOIN org o ON o.id = p.org_id
        WHERE e.status = 'quiet' AND (%(pe)s::text IS NULL OR pa.email = %(pe)s)
        ORDER BY e.warmth_peak DESC LIMIT %(lim)s""", {"pe": partner_email, "lim": limit})
