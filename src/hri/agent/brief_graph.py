"""Pre-meeting brief agent (LangGraph).

    resolve ──► gather ──► build_context ──► generate ──► verify ──► finalize
       │           │                            │           │  ▲
       │           │                            │           └──┘ one bounded regenerate (reflection)
       ▼           ▼                            ▼
     (ambiguous / not found / tools down)   fallback (LLM down: facts-only brief, no LLM)

Patterns: prompt chaining with deterministic gates, tool use via MCP, reflection
(verifier + bounded retry), graceful degradation, human-in-the-loop on ambiguity
(the agent asks which person rather than guessing).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from hri.agent import fallback
from hri.agent.context_budget import build_context
from hri.agent.prompts import (BRIEF_SCHEMA, BRIEF_SYSTEM, BRIEF_USER, SECTIONS, VERIFY_SCHEMA,
                               VERIFY_SYSTEM)
from hri.agent.tools import ToolUnavailable, mcp_tools
from hri.config.settings import settings, vertical
from hri.llm import LLMUnavailable, complete_json
from hri.obs.tracing import observe
from hri.privacy.masker import masker, unmask

INJECTION_RE = re.compile(r"(ignore (all )?(previous|prior) instructions|system (note|prompt)|"
                          r"\bAI (assistant|system|model)s?\b|you are now|disregard (the|all) )", re.I)


class BriefState(TypedDict, total=False):
    request: dict            # {query?, person_id?, meeting_id?, partner_email?}
    t0: float
    person_id: int
    partner_email: str | None
    meeting: dict | None
    candidates: list
    data: dict               # raw MCP results (unmasked, in-region)
    withheld: list           # messages withheld as prompt-injection attempts
    context: dict            # {masked_text, message_ids, stats}
    evidence: dict           # message id -> raw source text (in-region; masked only when sent to the verifier)
    draft: dict
    attempts: int
    verified: dict
    dropped: list
    brief: dict
    mode: str                # llm | fallback | ambiguous | not_found | unavailable
    warnings: list
    cost_usd: float
    timings: dict


def _elapsed(s: BriefState) -> float:
    return time.perf_counter() - s["t0"]


def _timed(name):
    def deco(fn):
        async def wrapper(state: BriefState):
            t = time.perf_counter()
            out = await fn(state)
            timings = dict(state.get("timings") or {})
            timings[name] = int((time.perf_counter() - t) * 1000)
            out["timings"] = timings
            return out
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


# ---------------------------------------------------------------- nodes
@_timed("resolve")
@observe(name="resolve")
async def resolve(s: BriefState) -> dict:
    req = s["request"]
    try:
        async with mcp_tools() as t:
            meeting, partner = None, req.get("partner_email")
            if req.get("meeting_id"):
                meeting = await t.call("get_meeting", meeting_id=req["meeting_id"])
                if "error" in meeting:
                    return {"mode": "not_found", "warnings": [f"No meeting {req['meeting_id']}"]}
                if meeting.get("status") == "cancelled":
                    s.setdefault("warnings", []).append("This meeting is marked CANCELLED in the calendar.")
                ext = [a for a in meeting["attendees"] if not a["is_partner"]]
                partner = partner or next((a["email"] for a in meeting["attendees"] if a["is_partner"]), None)
                if not ext:
                    return {"mode": "not_found", "meeting": meeting, "warnings": ["Internal meeting: no counterparty"]}
                return {"person_id": ext[0]["person_id"], "partner_email": partner, "meeting": meeting,
                        "warnings": s.get("warnings", [])}
            if req.get("person_id"):
                return {"person_id": int(req["person_id"]), "partner_email": partner, "meeting": None}
            cands = await t.call("find_person", query=req.get("query", ""), partner_email=partner)
    except ToolUnavailable as e:
        return {"mode": "unavailable", "warnings": [f"Relationship data service unavailable: {e}"]}
    if not cands:
        return {"mode": "not_found", "warnings": [f"No contact matches '{req.get('query')}'"]}
    exact = [c for c in cands if c["name"].lower() == req.get("query", "").strip().lower()
             or c["email"].lower() == req.get("query", "").strip().lower()]
    pool = exact or cands
    known = [c for c in pool if c["rank"] is not None and c["rank"] >= 0] if partner else []
    if len(pool) == 1 or len(known) == 1:
        c = (known or pool)[0]
        return {"person_id": c["person_id"], "partner_email": partner, "meeting": None}
    # Several people share this name: ask, don't guess.
    return {"mode": "ambiguous", "candidates": pool,
            "warnings": [f"{len(pool)} contacts match '{req.get('query')}'. Choose one."]}


@_timed("gather")
@observe(name="gather")
async def gather(s: BriefState) -> dict:
    pid = s["person_id"]
    try:
        async with mcp_tools() as t:
            profile, threads, commitments, index = await asyncio.gather(
                t.call("get_relationship", person_id=pid),
                t.call("get_recent_threads", person_id=pid, limit_threads=vertical()["brief"]["recent_threads"]),
                t.call("get_commitments", person_id=pid),
                t.call("get_thread_index", person_id=pid, limit=1000))
            hits = []
            if s.get("meeting") and s["meeting"].get("title"):
                try:
                    hits = await t.call("search_messages", person_id=pid, query=s["meeting"]["title"], k=4)
                except ToolUnavailable:
                    pass   # optional enrichment
    except ToolUnavailable as e:
        return {"mode": "unavailable", "warnings": s.get("warnings", []) + [f"Relationship data service unavailable: {e}"]}
    return {"data": {"profile": profile, "threads": threads or [], "commitments": commitments or [],
                     "index": index or [], "hits": hits or []}}


@_timed("build_context")
@observe(name="build_context")
async def build(s: BriefState) -> dict:
    d = s["data"]
    withheld = []
    for t in d["threads"]:
        for m in t["messages"]:
            if INJECTION_RE.search(m["body"] or ""):
                withheld.append({"id": m["id"], "sent_at": m["sent_at"], "subject": t["subject"]})
                m["body"] = "[message withheld by Hatton safety filter: it contains instructions aimed at AI systems]"
    ctx = build_context(d["profile"], d["threads"], d["commitments"], d["index"], d["hits"],
                        settings().context_budget_tokens)
    mk = masker()
    # raw (unmasked, in-region) evidence; only the sources a claim actually cites get masked, in verify
    evidence = {"PROFILE": ctx.text.split("## COMMITMENTS")[0].split("## RECENT THREADS")[0]}
    for t in d["threads"]:
        for m in t["messages"]:
            evidence[m["id"]] = f"{m['sent_at'][:10]} from {m['sender']}, thread \"{t['subject']}\": {m['body'][:2500]}"
    for c in d["commitments"]:
        evidence.setdefault(c["message_id"], f"{c['sent_at'][:10]} commitment ({c['owner']}): {c['text']}")
    for x in d["index"]:
        evidence.setdefault(x["first_id"], f"{x['started'][:10]} thread subject: {x['subject']}")
    for h in d["hits"]:
        evidence.setdefault(h["id"], f"{h['sent_at'][:10]} {h['subject']}: {h['body']}")
    masked = mk.mask(ctx.text)
    mk.flush()
    warnings = list(s.get("warnings", []))
    if withheld:
        warnings.append(f"{len(withheld)} message(s) withheld: they contained instructions aimed at AI systems "
                        f"({', '.join(w['id'] for w in withheld)}). Review manually.")
    if ctx.stats["threads_dropped"] or ctx.stats["threads_indexed"] > 20:
        warnings.append(f"Long history: {ctx.stats['threads_full']} threads in full, "
                        f"{ctx.stats['threads_indexed']} as index lines, {ctx.stats['threads_dropped']} omitted "
                        f"to fit the {ctx.stats['budget']}-token context budget.")
    return {"context": {"masked_text": masked, "message_ids": sorted(ctx.message_ids), "stats": ctx.stats},
            "evidence": evidence, "withheld": withheld, "warnings": warnings}


@_timed("generate")
@observe(name="generate")
async def generate(s: BriefState) -> dict:
    mk = masker()
    prof = s["data"]["profile"]
    meeting = s.get("meeting") or {}
    user = BRIEF_USER.format(meeting=mk.mask(f"{meeting.get('title', 'ad-hoc')} {str(meeting.get('start_at', ''))[:16]}"),
                             partner=mk.mask(s.get("partner_email") or "any partner"),
                             person=mk.mask(prof["name"]), context=s["context"]["masked_text"])
    if s.get("dropped") and s.get("attempts", 0) >= 1:
        user += ("\nPREVIOUS DRAFT PROBLEMS (fix them, do not repeat these claims):\n" +
                 "\n".join(f"- {d['text']} :: {d['reason']}" for d in s["dropped"][:10]))
    mk.flush()
    system = BRIEF_SYSTEM.format(max_words=vertical()["brief"]["max_words"])
    try:
        draft, res = await asyncio.to_thread(complete_json, model=settings().brief_model, system=system, user=user,
                                             purpose="brief_generate", max_tokens=1500, json_schema=BRIEF_SCHEMA,
                                             effort="low")
    except LLMUnavailable as e:
        return {"mode": "fallback", "warnings": s.get("warnings", []) + [f"Language model unavailable ({e}); "
                                                                         "showing facts-only brief."]}
    return {"draft": draft, "attempts": s.get("attempts", 0) + 1, "cost_usd": s.get("cost_usd", 0) + res.cost_usd}


@_timed("verify")
@observe(name="verify")
async def verify(s: BriefState) -> dict:
    valid = set(s["context"]["message_ids"]) | {"PROFILE"}
    claims, dropped = [], []
    for sec in SECTIONS:
        for c in s["draft"].get(sec, []):
            cites = [x.strip("[] ") for x in c.get("cites", [])]
            if not cites or not set(cites) <= valid:
                dropped.append({"section": sec, "text": c["text"], "reason": "missing or invalid citation"})
                continue
            claims.append({"section": sec, "text": c["text"], "cites": cites})
    warnings = list(s.get("warnings", []))
    cost = s.get("cost_usd", 0.0)
    if claims and _elapsed(s) < settings().brief_timeout_s - 6:
        ev, mk = s["evidence"], masker()
        listing = "\n\n".join(f"CLAIM {i}: {c['text']}\nSOURCES:\n" + "\n".join(
            f"  [{x}] {mk.mask(ev.get(x, '(missing)'))}" for x in c["cites"]) for i, c in enumerate(claims))
        mk.flush()
        try:
            out, res = await asyncio.to_thread(complete_json, model=settings().verify_model, system=VERIFY_SYSTEM,
                                               user=listing, purpose="brief_verify", max_tokens=4000,
                                               json_schema=VERIFY_SCHEMA, effort="low")
            cost += res.cost_usd
            bad = {v["i"]: v["reason"] for v in out["verdicts"] if not v["supported"]}
            kept = []
            for i, c in enumerate(claims):
                if i in bad:
                    dropped.append({**c, "reason": f"verifier: {bad[i]}"})
                else:
                    kept.append(c)
            claims = kept
        except LLMUnavailable:
            warnings.append("Verifier unavailable: claims are citation-checked but not fact-checked.")
    elif claims:
        warnings.append("Skipped LLM fact-check to stay inside the latency budget; claims are citation-checked.")
    return {"verified": {"claims": claims}, "dropped": (s.get("dropped") or []) + dropped,
            "warnings": warnings, "cost_usd": cost}


def _after_verify(s: BriefState) -> str:
    total = len(s["verified"]["claims"]) + len(s.get("dropped") or [])
    ratio = len(s.get("dropped") or []) / total if total else 0
    if ratio > 0.3 and s.get("attempts", 0) < 2 and _elapsed(s) < 12:
        return "generate"   # reflection: one bounded retry with the verifier's feedback
    return "finalize"


@_timed("finalize")
@observe(name="finalize")
async def finalize(s: BriefState) -> dict:
    sections: dict[str, list] = {sec: [] for sec in SECTIONS}
    for c in s["verified"]["claims"]:
        sections[c["section"]].append({"text": unmask(c["text"]), "cites": c["cites"]})
    brief = fallback.header(s["data"], s.get("partner_email"), s.get("meeting"))
    brief["sections"] = sections
    brief["dropped_claims"] = [{"text": unmask(d["text"]), "reason": unmask(d["reason"])} for d in s.get("dropped") or []]
    brief["context_stats"] = s["context"]["stats"]
    brief["profile_source"] = s["evidence"]["PROFILE"]   # exactly what PROFILE citations refer to (for audit/eval)
    return {"brief": brief, "mode": "llm"}


@_timed("fallback")
@observe(name="fallback")
async def facts_only(s: BriefState) -> dict:
    return {"brief": fallback.facts_only(s["data"], s.get("partner_email"), s.get("meeting")), "mode": "fallback"}


# ---------------------------------------------------------------- graph
def _route(after: str):
    def r(s: BriefState) -> str:
        if s.get("mode") in ("ambiguous", "not_found", "unavailable"):
            return END
        if s.get("mode") == "fallback":
            return "fallback"
        return after
    return r


def build_graph(checkpointer=None):
    g = StateGraph(BriefState)
    g.add_node("resolve", resolve)
    g.add_node("gather", gather)
    g.add_node("build_context", build)
    g.add_node("generate", generate)
    g.add_node("verify", verify)
    g.add_node("finalize", finalize)
    g.add_node("fallback", facts_only)
    g.add_edge(START, "resolve")
    g.add_conditional_edges("resolve", _route("gather"), ["gather", END])
    g.add_conditional_edges("gather", _route("build_context"), ["build_context", END])
    g.add_edge("build_context", "generate")
    g.add_conditional_edges("generate", _route("verify"), ["verify", "fallback", END])
    g.add_conditional_edges("verify", _after_verify, ["generate", "finalize"])
    g.add_edge("finalize", END)
    g.add_edge("fallback", END)
    return g.compile(checkpointer=checkpointer)


_graph = None


@observe(name="pre_meeting_brief")
async def run_brief(request: dict[str, Any], run_id: str, checkpointer=None) -> dict:
    global _graph
    if _graph is None or checkpointer is not None:
        _graph = build_graph(checkpointer)
    t0 = time.perf_counter()
    state: BriefState = {"request": request, "t0": t0, "warnings": [], "cost_usd": 0.0, "timings": {}}
    cfg = {"configurable": {"thread_id": run_id}}
    out = await asyncio.wait_for(_graph.ainvoke(state, cfg), timeout=settings().brief_timeout_s + 4)
    return {"mode": out.get("mode", "llm"), "brief": out.get("brief"), "candidates": out.get("candidates"),
            "warnings": out.get("warnings", []), "cost_usd": round(out.get("cost_usd", 0.0), 6),
            "latency_ms": int((time.perf_counter() - t0) * 1000), "timings": out.get("timings", {}),
            "person_id": out.get("person_id"), "partner_email": out.get("partner_email"),
            "meeting_id": (out.get("meeting") or {}).get("id")}
