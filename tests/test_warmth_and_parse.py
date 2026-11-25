from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hri.config.settings import vertical
from hri.graph.warmth import score_edge
from hri.ingest.parse import Malformed, parse

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


def _cadence(days_between, until_days_ago, n=30, kinds=("inbound", "outbound", "meeting")):
    ev, t = [], NOW - timedelta(days=until_days_ago + days_between * n)
    for i in range(n):
        ev.append((t, kinds[i % len(kinds)]))
        t += timedelta(days=days_between)
    return ev


def test_steady_relationship_is_warm():
    s = score_edge(_cadence(10, 3), NOW, "bank", vertical())
    assert s["status"] == "warm"


def test_cooled_relationship_is_quiet():
    s = score_edge(_cadence(10, 120), NOW, "bank", vertical())
    assert s["status"] == "quiet"
    assert s["explanation"]["days_since_two_way_contact"] >= 120


def test_slow_cadence_lp_not_flagged_early():
    s = score_edge(_cadence(60, 70, n=10), NOW, "fund", vertical())
    assert s["status"] != "quiet"


def test_one_off_burst_never_quiet():
    ev = [(NOW - timedelta(days=200, hours=h), "inbound") for h in range(0, 48, 6)]
    assert score_edge(ev, NOW, "company", vertical())["status"] != "quiet"


def test_unanswered_outreach_does_not_count_as_contact():
    ev = _cadence(10, 150) + [(NOW - timedelta(days=d), "outbound") for d in (60, 30, 5)]
    s = score_edge(ev, NOW, "bank", vertical())
    assert s["status"] == "quiet" and s["explanation"]["unanswered_outbound_since"] == 3


BASE = {"id": "m1", "thread_id": "t1", "mailbox": "a@hattoncap.example", "date": "2026-01-01T10:00:00+00:00",
        "from": {"name": "A", "email": "a@x.example"}, "to": [{"name": "B", "email": "b@hattoncap.example"}],
        "subject": "s", "body": "b"}


@pytest.mark.parametrize("patch,reason", [({"date": "not-a-date"}, "date"), ({"from": None}, "address"),
                                          ({"to": [], "cc": []}, "recipients"), ({"body": "A" * 2_000_000}, "large")])
def test_malformed_is_rejected_cleanly(patch, reason):
    with pytest.raises(Malformed):
        parse({**BASE, **patch})


def test_html_body_converted():
    m = parse({**BASE, "body": "<html><body><p>Hello <b>there</b></p></body></html>"})
    assert m.body.strip() == "Hello there"
