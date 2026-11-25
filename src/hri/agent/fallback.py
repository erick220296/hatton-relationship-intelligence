"""Deterministic brief parts - no LLM, so nothing here can be hallucinated.

`header` is shown on every brief. `facts_only` is the whole brief when the
language model is unavailable (graceful degradation for the failure demo).
"""
from __future__ import annotations

from datetime import datetime

from hri.agent.prompts import SECTIONS


def _d(v) -> str:
    return str(v)[:10] if v else "never"


def header(data: dict, partner_email: str | None, meeting: dict | None) -> dict:
    p = data["profile"]
    rels = p.get("relationships", [])
    mine = next((r for r in rels if r["partner_email"] == partner_email), rels[0] if rels else None)
    return {
        "person": {"name": p["name"], "email": p["email"], "title": p.get("title"), "org": p.get("org"),
                   "kind": p.get("kind")},
        "meeting": {k: meeting.get(k) for k in ("id", "title", "start_at", "status", "location")} if meeting else None,
        "partner_email": partner_email,
        "relationship": ({"status": mine["status"], "warmth": mine["warmth"], "peak": mine["warmth_peak"],
                          "first_contact": _d(mine["first_contact"]), "last_contact": _d(mine["last_contact"]),
                          "last_time_they_engaged": _d(mine["last_inbound"]), "meetings": mine["n_meetings"],
                          "emails_in": mine["n_inbound"], "emails_out": mine["n_outbound"],
                          "explanation": mine.get("explanation")} if mine else None),
        "other_partners": [{"partner": r["partner"], "status": r["status"], "warmth": r["warmth"]}
                           for r in rels if not mine or r["partner_email"] != mine["partner_email"]],
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def facts_only(data: dict, partner_email: str | None, meeting: dict | None) -> dict:
    b = header(data, partner_email, meeting)
    s = {sec: [] for sec in SECTIONS}
    p = data["profile"]
    s["who"].append({"text": f"{p['name']}, {p.get('title') or 'title unknown'} at {p.get('org') or 'no organisation on file'}.",
                     "cites": ["PROFILE"]})
    if b["relationship"]:
        r = b["relationship"]
        s["history"].append({"text": f"Known since {r['first_contact']}; {r['meetings']} meetings, "
                                     f"{r['emails_in']} emails from them, {r['emails_out']} to them. "
                                     f"Last contact {r['last_contact']}.", "cites": ["PROFILE"]})
        if r["status"] == "quiet":
            s["signals"].append({"text": f"Gone quiet: they last engaged on {r['last_time_they_engaged']}.",
                                 "cites": ["PROFILE"]})
    for t in data["threads"][:3]:
        last = t["messages"][-1]
        s["last_discussed"].append({"text": f"{_d(last['sent_at'])}: \"{t['subject']}\" "
                                            f"(last message from {last['sender']})", "cites": [last["id"]]})
    for c in data["commitments"][:6]:
        sec = "our_commitments" if c["owner"] == "us" else "their_commitments"
        s[sec].append({"text": f"{_d(c['sent_at'])}: {c['text']}", "cites": [c["message_id"]]})
    b["sections"] = s
    b["dropped_claims"] = []
    return b
