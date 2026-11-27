"""F11: masking proven end to end on the REAL payloads (needs the Docker stack: db + mcp).

For 20+ real contacts we run the brief agent's own steps (resolve → gather → build_context → generate →
verify) with the model call replaced by a recorder, so we capture the exact `system` + `user` text that
would leave the region, without calling any model. Each payload is scanned for every person full name,
email and organisation name in the database. Negative control: the same with masking switched off must
find leaks, which proves the scan can see them.

Writes docs/evidence/masking_leak.json (deterministic: no timestamp, so re-runs leave git clean).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "masking_leak.json"


def _cases():
    """~24 requests across the shapes that stress masking. Chosen from the data, not hard-coded people."""
    from hri import db
    q = lambda sql: [dict(r) for r in db.q(sql)]   # noqa: E731
    pick = """SELECT e.person_id, pa.email AS partner_email, '{label}' AS label
              FROM edge e JOIN person pa ON pa.id = e.partner_id JOIN person p ON p.id = e.person_id
              WHERE {where} ORDER BY {order} LIMIT {n}"""
    cases = []
    cases += q(pick.format(label="warm", where="e.status = 'warm'", order="e.warmth DESC", n=5))
    cases += q(pick.format(label="quiet", where="e.status = 'quiet'", order="e.warmth_peak DESC", n=4))
    cases += q(pick.format(label="cooling", where="e.status = 'cooling'", order="e.warmth_peak DESC", n=3))
    cases += q(pick.format(label="new", where="e.status = 'new'", order="e.person_id", n=2))
    cases += q(pick.format(label="heavy", where="true",
                           order="(e.n_inbound + e.n_outbound) DESC", n=3))
    cases += q(pick.format(label="name collision",
                           where="p.name IN (SELECT name FROM person GROUP BY name HAVING count(*) > 1)",
                           order="p.name, e.warmth DESC", n=4))
    cases = [{"person_id": c["person_id"], "partner_email": c["partner_email"], "label": c["label"]} for c in cases]
    cases.append({"query": "janice.hall@martincapitaladvis.example",
                  "partner_email": "eileen.whitehead@hattoncap.example", "label": "injection"})
    cases.append({"meeting_id": "c03353", "label": "meeting"})
    cases.append({"meeting_id": "c03478", "label": "cancelled meeting"})
    return cases


def _scanner():
    """Every person full name / email and organisation name in the DB. Names, emails and multi-word orgs
    match case-insensitively; one-word orgs (surnames such as 'Bell', 'Young') match case-sensitively,
    otherwise ordinary words like 'young' would count as leaks."""
    from hri import db
    people = db.q("SELECT name, email FROM person")
    orgs = [r["name"] for r in db.q("SELECT name FROM org") if r["name"] and len(r["name"]) >= 4]
    ci = {p["name"] for p in people if p["name"] and len(p["name"]) >= 4} | \
         {p["email"].lower() for p in people if p["email"]} | {o for o in orgs if " " in o}
    cs = {o for o in orgs if " " not in o}
    alt = lambda xs: "|".join(re.escape(x) for x in sorted(xs, key=len, reverse=True))   # noqa: E731
    ci_re = re.compile(rf"(?<![\w@.])({alt(ci)})(?![\w])", re.I)
    cs_re = re.compile(rf"\b({alt(cs)})\b")

    def scan(text: str) -> list[str]:
        return ci_re.findall(text) + cs_re.findall(text)
    return scan, {"person_names_and_emails": len(people) * 2, "org_names": len(orgs)}


async def _payloads(req: dict) -> list[str]:
    """The exact model inputs for one brief: the generate call and the verify call."""
    from hri.agent import brief_graph as bg
    from hri.llm import LLMResult
    sent = []

    def recorder(**kw):
        sent.append(kw["system"] + "\n" + kw["user"])
        if kw["purpose"] == "brief_generate":   # one claim per message in context, so verify masks real sources
            ids = re.findall(r"\[(m\d+)\]", kw["user"])[:12] or ["PROFILE"]
            draft = {sec: [] for sec in ("who", "history", "our_commitments", "their_commitments", "signals")}
            draft["last_discussed"] = [{"text": "Discussed in this message.", "cites": [i]} for i in ids]
            return draft, LLMResult("{}", "rec", 0, 0, 0.0, 0)
        return {"verdicts": []}, LLMResult("{}", "rec", 0, 0, 0.0, 0)

    s = {"request": {k: v for k, v in req.items() if k != "label"}, "t0": time.perf_counter(), "warnings": [],
         "cost_usd": 0.0, "timings": {}}
    orig, bg.complete_json = bg.complete_json, recorder
    try:
        for node in (bg.resolve, bg.gather, bg.build, bg.generate, bg.verify):
            s.update(await node(s))
            if s.get("mode") in ("ambiguous", "not_found", "unavailable", "fallback"):
                break
    finally:
        bg.complete_json = orig
    return sent


async def test_real_payloads_contain_no_names_emails_or_orgs():
    from hri.privacy.masker import MaskingViolation, assert_masked
    scan, checked = _scanner()
    cases = _cases()
    rows, payloads, leaks, f12_blocked = [], 0, 0, 0
    for req in cases:
        sent = await _payloads(req)
        hits = [h for p in sent for h in scan(p)]
        for p in sent:
            try:
                assert_masked(p)
            except MaskingViolation:
                f12_blocked += 1
        payloads += len(sent)
        leaks += len(hits)
        rows.append({"label": req["label"], "payloads": len(sent), "chars": sum(map(len, sent)), "leaks": len(hits),
                     "placeholders": len(set(re.findall(r"\b(?:PERSON|NAME|ORG|EMAIL|PROJECT)_[0-9a-f]{6}\b",
                                                        " ".join(sent))))})
    control = await test_negative_control_masking_off_finds_leaks(_return=True)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps({
        "what": "exact model payloads (generate + verify) rebuilt for real contacts, model call replaced by a recorder",
        "contacts": len(cases), "payloads": payloads, "identifiers_checked": checked, "leaks": leaks,
        "f12_assert_masked_blocked": f12_blocked, "negative_control": control, "per_contact": rows}, indent=1))
    assert len(cases) >= 20 and payloads >= 20
    assert leaks == 0, [r for r in rows if r["leaks"]]
    assert f12_blocked == 0          # the egress-side check (F12) accepts every properly masked payload
    assert control["leaks"] > 0


async def test_negative_control_masking_off_finds_leaks(_return: bool = False):
    """Same pipeline with masking switched off: the scan must find real names, or the test above proves nothing."""
    from hri.privacy import masker as masker_mod
    scan, _ = _scanner()
    original = masker_mod.Masker.mask
    masker_mod.Masker.mask = lambda self, text: text
    try:
        cases = _cases()[:5]
        found = 0
        for req in cases:
            found += sum(len(scan(p)) for p in await _payloads(req))
    finally:
        masker_mod.Masker.mask = original
    result = {"masking": "off", "contacts": len(cases), "leaks": found}
    if _return:
        return result
    assert found > 0
