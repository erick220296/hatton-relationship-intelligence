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
import functools
import logging
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
from hri.obs import tracing
from hri.obs.tracing import observe
from hri.privacy.masker import masker, unmask

log = logging.getLogger("hri.brief")
DATA_DOWN = "Relationship data service unavailable right now."

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
    unverified: bool         # the fact-check could not run: model-written claims were removed (fail closed)
    review: list             # reasons a person should review this brief
    warnings: list
    cost_usd: float
    timings: dict


def _elapsed(s: BriefState) -> float:
    return time.perf_counter() - s["t0"]


GENERATE_MIN_S = 12.0   # don't start the model if less than this is left of the brief's time budget


def _remaining(s: BriefState) -> float:
    return settings().brief_timeout_s - _elapsed(s)


def _mask_flush(*texts: str) -> list[str]:
    """Mask, then persist new pseudonyms (a DB write). Sync: callers run it in a worker thread."""
    mk = masker()
    out = [mk.mask(t) for t in texts]
    mk.flush()
    return out


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


# Langfuse is a third party: node spans never capture state (raw mail, evidence, unmasked brief).
# They carry only these scalar fields plus the size of every list/dict a node returns.
_SPAN_SCALARS = {"mode", "person_id", "attempts", "cost_usd", "unverified"}


def _span_counts(fn):
    @functools.wraps(fn)
    async def wrapper(state: BriefState):
        out = await fn(state)
        meta = {k: v for k, v in out.items() if k in _SPAN_SCALARS}
        meta.update({f"n_{k}": len(v) for k, v in out.items() if isinstance(v, (list, dict)) and k != "timings"})
        if isinstance(out.get("context"), dict):
            meta["context_stats"] = out["context"].get("stats")
        tracing.span_meta(**meta)
        return out
    return wrapper


# ---------------------------------------------------------------- nodes
@_timed("resolve")
@observe(name="resolve", capture_input=False, capture_output=False)
@_span_counts
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
        log.warning("data tools unavailable in resolve: %r", e)   # details in the server log, not the UI
        return {"mode": "unavailable", "warnings": [DATA_DOWN]}
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
@observe(name="gather", capture_input=False, capture_output=False)
@_span_counts
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
        log.warning("data tools unavailable in gather: %r", e)
        return {"mode": "unavailable", "warnings": s.get("warnings", []) + [DATA_DOWN]}
    return {"data": {"profile": profile, "threads": threads or [], "commitments": commitments or [],
                     "index": index or [], "hits": hits or []}}


@_timed("build_context")
@observe(name="build_context", capture_input=False, capture_output=False)
@_span_counts
async def build(s: BriefState) -> dict:
    d = s["data"]
    withheld = []
    for t in d["threads"]:
        for m in t["messages"]:
            if INJECTION_RE.search(m["body"] or ""):
                withheld.append({"id": m["id"], "sent_at": m["sent_at"], "subject": t["subject"]})
                m["body"] = "[message withheld by Hatton safety filter: it contains instructions aimed at AI systems]"
    ctx = await asyncio.to_thread(build_context, d["profile"], d["threads"], d["commitments"], d["index"],
                                  d["hits"], settings().context_budget_tokens)
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
    masked, = await asyncio.to_thread(_mask_flush, ctx.text)
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
@observe(name="generate", capture_input=False, capture_output=False)
@_span_counts
async def generate(s: BriefState) -> dict:
    left = _remaining(s)
    if left < GENERATE_MIN_S:   # not enough time for a model draft + fact-check: degrade now, not at the deadline
        return {"mode": "fallback", "warnings": s.get("warnings", []) + [
            f"Only {max(left, 0):.0f} s of the {settings().brief_timeout_s:.0f} s budget left; "
            "skipped the language model and showing facts-only brief."]}
    prof = s["data"]["profile"]
    meeting = s.get("meeting") or {}
    m_meeting, m_partner, m_person = await asyncio.to_thread(
        _mask_flush, f"{meeting.get('title', 'ad-hoc')} {str(meeting.get('start_at', ''))[:16]}",
        s.get("partner_email") or "any partner", prof["name"])
    user = BRIEF_USER.format(meeting=m_meeting, partner=m_partner, person=m_person,
                             context=s["context"]["masked_text"])
    if s.get("dropped") and s.get("attempts", 0) >= 1:
        user += ("\nPREVIOUS DRAFT PROBLEMS (fix them, do not repeat these claims):\n" +
                 "\n".join(f"- {d['text']} :: {d['reason']}" for d in s["dropped"][:10]))
    system = BRIEF_SYSTEM.format(max_words=vertical()["brief"]["max_words"])
    call_s = max(_remaining(s) - 6, 1.0)   # leave time to fact-check and render
    try:
        draft, res = await asyncio.wait_for(asyncio.to_thread(
            complete_json, model=settings().brief_model, system=system, user=user, purpose="brief_generate",
            max_tokens=1500, json_schema=BRIEF_SCHEMA, effort="low", timeout=call_s, max_retries=0),
            timeout=call_s + 1)
    except LLMUnavailable as e:
        return {"mode": "fallback", "warnings": s.get("warnings", []) + [f"Language model unavailable ({e}); "
                                                                         "showing facts-only brief."]}
    except asyncio.TimeoutError:
        return {"mode": "fallback", "warnings": s.get("warnings", []) + [
            f"Language model did not answer within {call_s:.0f} s; showing facts-only brief."]}
    return {"draft": draft, "attempts": s.get("attempts", 0) + 1, "cost_usd": s.get("cost_usd", 0) + res.cost_usd}


@_timed("verify")
@observe(name="verify", capture_input=False, capture_output=False)
@_span_counts
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
    unchecked = None   # why the fact-check could not run, if it could not
    if claims and _elapsed(s) < settings().brief_timeout_s - 6:
        ev = s["evidence"]
        cited = sorted({x for c in claims for x in c["cites"]})
        masked = dict(zip(cited, await asyncio.to_thread(_mask_flush, *(ev.get(x, "(missing)") for x in cited))))
        listing = "\n\n".join(f"CLAIM {i}: {c['text']}\nSOURCES:\n" + "\n".join(
            f"  [{x}] {masked[x]}" for x in c["cites"]) for i, c in enumerate(claims))
        call_s = max(_remaining(s) - 1, 1.0)
        try:
            out, res = await asyncio.wait_for(asyncio.to_thread(
                complete_json, model=settings().verify_model, system=VERIFY_SYSTEM, user=listing,
                purpose="brief_verify", max_tokens=4000, json_schema=VERIFY_SCHEMA, effort="low",
                timeout=call_s, max_retries=0), timeout=call_s + 1)
            cost += res.cost_usd
            verdicts = {v["i"]: v for v in out["verdicts"]}
            kept = []
            for i, c in enumerate(claims):   # fail closed: a claim with no verdict is not kept
                v = verdicts.get(i, {"supported": False, "reason": "no verdict returned"})
                if v["supported"]:
                    kept.append(c)
                else:
                    dropped.append({**c, "reason": f"verifier: {v['reason']}"})
            claims = kept
        except (LLMUnavailable, KeyError, TypeError) as e:   # down, or a malformed verdict list
            unchecked = f"Verifier unavailable ({e})"
        except asyncio.TimeoutError:
            unchecked = f"Verifier did not answer within {call_s:.0f} s"
    elif claims:
        unchecked = "Fact-check skipped to stay inside the latency budget"
    out = {"cost_usd": cost}
    if unchecked:   # fail closed: nothing the model wrote is shown unless it was fact-checked
        dropped += [{**c, "reason": f"not fact-checked: {unchecked.lower()}"} for c in claims]
        warnings.append(f"{unchecked}: {len(claims)} model-written claim(s) removed; showing facts-only brief.")
        out.update(unverified=True, review=(s.get("review") or []) + [
            f"{unchecked}: {len(claims)} model-written claim(s) could not be fact-checked and were removed."])
        claims = []
    out.update(verified={"claims": claims}, dropped=(s.get("dropped") or []) + dropped, warnings=warnings)
    return out


def _review(s: BriefState, mode: str) -> dict:
    """Why a person should look at this brief before (or instead of) trusting it. Feeds the review queue."""
    reasons = list(s.get("review") or [])
    if mode == "fallback":
        reasons.append("Facts-only brief (model unavailable or out of time): no model-written content.")
    kept, dropped = len((s.get("verified") or {}).get("claims", [])), len(s.get("dropped") or [])
    if not s.get("unverified") and kept + dropped and dropped / (kept + dropped) > 0.3:
        reasons.append(f"{dropped} of {kept + dropped} model-written claims failed citation or fact checks and "
                       "were removed (over 30%).")
    if s.get("withheld"):
        reasons.append(f"{len(s['withheld'])} message(s) withheld because they contained instructions aimed at AI "
                       f"systems ({', '.join(w['id'] for w in s['withheld'])}); a person should read them.")
    return {"needed": bool(reasons), "reasons": reasons}


def _after_verify(s: BriefState) -> str:
    if s.get("unverified"):
        return "fallback"   # fact-check could not run: show only code-rendered facts
    total = len(s["verified"]["claims"]) + len(s.get("dropped") or [])
    ratio = len(s.get("dropped") or []) / total if total else 0
    if ratio > 0.3 and s.get("attempts", 0) < 2 and _elapsed(s) < 12:
        return "generate"   # reflection: one bounded retry with the verifier's feedback
    return "finalize"


@_timed("finalize")
@observe(name="finalize", capture_input=False, capture_output=False)
@_span_counts
async def finalize(s: BriefState) -> dict:
    def render():   # unmask can read the pseudonym vault (DB): keep it off the event loop
        sections: dict[str, list] = {sec: [] for sec in SECTIONS}
        for c in s["verified"]["claims"]:
            sections[c["section"]].append({"text": unmask(c["text"]), "cites": c["cites"]})
        return sections, [{"text": unmask(d["text"]), "reason": unmask(d["reason"])} for d in s.get("dropped") or []]
    sections, dropped = await asyncio.to_thread(render)
    brief = fallback.header(s["data"], s.get("partner_email"), s.get("meeting"))
    brief["sections"] = sections
    brief["dropped_claims"] = dropped
    brief["context_stats"] = s["context"]["stats"]
    brief["profile_source"] = s["evidence"]["PROFILE"]   # exactly what PROFILE citations refer to (for audit/eval)
    brief["review"] = _review(s, "llm")
    return {"brief": brief, "mode": "llm"}


@_timed("fallback")
@observe(name="fallback", capture_input=False, capture_output=False)
@_span_counts
async def facts_only(s: BriefState) -> dict:
    brief = fallback.facts_only(s["data"], s.get("partner_email"), s.get("meeting"))
    if s.get("context"):   # what the context budgeter kept/compressed/omitted, even when the model is down
        brief["context_stats"] = s["context"]["stats"]
    if s.get("dropped"):   # shown as removed, for transparency; never as brief content
        brief["dropped_claims"] = await asyncio.to_thread(
            lambda: [{"text": unmask(d["text"]), "reason": unmask(d["reason"])} for d in s["dropped"]])
    brief["review"] = _review(s, "fallback")
    return {"brief": brief, "mode": "fallback"}


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
    g.add_conditional_edges("verify", _after_verify, ["generate", "finalize", "fallback"])
    g.add_edge("finalize", END)
    g.add_edge("fallback", END)
    return g.compile(checkpointer=checkpointer)


_graph = None


def _at_deadline(last: dict) -> dict:
    """The hard deadline hit mid-graph: build the facts-only brief from whatever was already gathered
    (or report 'delayed' if not even the data arrived). Never raises, never returns model text."""
    msg = f"Brief deadline ({settings().brief_timeout_s + 4:.0f} s) reached before the brief was finished."
    s = {**last, "warnings": list(last.get("warnings") or []) + [msg + " Showing facts-only brief."],
         "review": list(last.get("review") or []) + [msg], "verified": {"claims": []}, "dropped": []}
    if last.get("mode") in ("ambiguous", "not_found", "unavailable"):
        return last
    if not last.get("data"):
        return {**s, "mode": "delayed", "brief": None, "warnings": s["warnings"][:-1] + [
            msg + " No relationship data yet; try again."], "delayed_review": {"needed": True, "reasons": s["review"]}}
    brief = fallback.facts_only(s["data"], s.get("partner_email"), s.get("meeting"))
    if s.get("context"):
        brief["context_stats"] = s["context"]["stats"]
    brief["review"] = _review(s, "fallback")
    return {**s, "brief": brief, "mode": "fallback"}


@observe(name="pre_meeting_brief", capture_input=False, capture_output=False)
async def run_brief(request: dict[str, Any], run_id: str, checkpointer=None, tags: list[str] | None = None) -> dict:
    """One brief = one Langfuse trace: session = run_id, tags (brief, mode, escalated, load-test ...),
    scores (latency_ms, cost_usd, dropped_ratio, citation_coverage). Returns the trace_id with the result."""
    base_tags = ["brief", *(tags or [])]
    with tracing.trace_attributes(tags=base_tags, session_id=run_id):
        res, out = await _run_brief(request, run_id, checkpointer)
        dropped = out.get("dropped") or []
        total = len((out.get("verified") or {}).get("claims", [])) + len(dropped)
        bad_cite = sum(d.get("reason") == "missing or invalid citation" for d in dropped)
        final = base_tags + [res["mode"]] + (["escalated"] if (res.get("review") or {}).get("needed") else [])
        with tracing.trace_attributes(tags=final):   # mode/escalation are only known at the end
            pass
        tracing.score_trace(latency_ms=res["latency_ms"], cost_usd=res["cost_usd"],
                            dropped_ratio=len(dropped) / total if total else 0.0,
                            citation_coverage=(total - bad_cite) / total if total else None)
        res["trace_id"] = tracing.current_trace_id()
    return res


async def _run_brief(request: dict[str, Any], run_id: str, checkpointer=None) -> tuple[dict, dict]:
    global _graph
    if _graph is None or checkpointer is not None:
        _graph = build_graph(checkpointer)
    t0 = time.perf_counter()
    state: BriefState = {"request": request, "t0": t0, "warnings": [], "cost_usd": 0.0, "timings": {}}
    cfg = {"configurable": {"thread_id": run_id}}
    last: dict = dict(state)

    async def _run():
        nonlocal last
        async for values in _graph.astream(state, cfg, stream_mode="values"):
            last = values   # the full state after each step: what we fall back on at the deadline
    try:
        await asyncio.wait_for(_run(), timeout=settings().brief_timeout_s + 4)
        out = last
    except asyncio.TimeoutError:
        out = _at_deadline(last)
    res = {"mode": out.get("mode", "llm"), "brief": out.get("brief"), "candidates": out.get("candidates"),
           "warnings": out.get("warnings", []), "cost_usd": round(out.get("cost_usd", 0.0), 6),
           "latency_ms": int((time.perf_counter() - t0) * 1000), "timings": out.get("timings", {}),
           "person_id": out.get("person_id"), "partner_email": out.get("partner_email"),
           "meeting_id": (out.get("meeting") or {}).get("id"),
           "review": (out.get("brief") or {}).get("review") or out.get("delayed_review")}
    tracing.span_meta(mode=res["mode"], person_id=res["person_id"], meeting_id=res["meeting_id"],
                      latency_ms=res["latency_ms"], cost_usd=res["cost_usd"], timings=res["timings"],
                      n_warnings=len(res["warnings"]), n_dropped=len(out.get("dropped") or []))
    return res, out
