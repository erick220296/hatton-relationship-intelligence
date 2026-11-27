"""HTTP API. Read-and-inform only: there is no endpoint that sends, books or contacts anything."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from hri import clock, db
from hri.agent.brief_graph import run_brief
from hri.agent.tools import ToolUnavailable, mcp_tools
from hri.config.settings import settings
from hri.graph.warmth import quiet_list
from hri.obs import tracing

log = logging.getLogger("hri.api")
_checkpointer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _checkpointer
    cm = None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        cm = AsyncPostgresSaver.from_conn_string(settings().database_url)
        _checkpointer = await cm.__aenter__()
        await _checkpointer.setup()
    except Exception as e:  # the brief still works without run checkpoints
        log.warning("checkpointer disabled: %s", e)
        _checkpointer, cm = None, None
    from hri.privacy.masker import masker
    await asyncio.to_thread(masker)   # warm the masking dictionary + NER model
    yield
    tracing.flush()
    if cm:
        await cm.__aexit__(None, None, None)


app = FastAPI(title="Hatton Relationship Intelligence", lifespan=lifespan)


class BriefRequest(BaseModel):
    query: str | None = None
    person_id: int | None = None
    meeting_id: str | None = None
    partner_email: str | None = None
    fresh: bool = False


def _store(res: dict) -> int | None:
    if not res.get("brief") or not res.get("person_id"):
        return None
    pid = db.q1("SELECT id FROM person WHERE email = %s", (res.get("partner_email"),)) if res.get("partner_email") else None
    r = db.q1("""INSERT INTO brief (person_id, partner_id, meeting_id, mode, content, latency_ms, cost_usd, trace_id)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
              (res["person_id"], pid["id"] if pid else None, res.get("meeting_id"), res["mode"],
               json.dumps(res["brief"], default=str), res["latency_ms"], res["cost_usd"], res.get("trace_id")))
    return r["id"]


def _cached(meeting_id: str | None = None, person_id: int | None = None) -> dict | None:
    if meeting_id:
        r = db.q1("""SELECT * FROM brief WHERE meeting_id = %s AND mode = 'llm' ORDER BY created_at DESC LIMIT 1""",
                  (meeting_id,))
    elif person_id:
        r = db.q1("""SELECT * FROM brief WHERE person_id = %s AND mode = 'llm' ORDER BY created_at DESC LIMIT 1""",
                  (person_id,))
    else:
        return None
    return r


@app.post("/brief")
async def brief(req: BriefRequest):
    if req.meeting_id and not req.fresh:
        c = await asyncio.to_thread(_cached, meeting_id=req.meeting_id)
        if c:
            return {"mode": "cached", "brief": c["content"], "warnings": [f"Prepared at {c['created_at']:%H:%M}"],
                    "latency_ms": 0, "cost_usd": 0, "brief_id": c["id"], "person_id": c["person_id"],
                    "review": c["content"].get("review")}
    run_id = str(uuid.uuid4())
    # run_brief never raises on the deadline: it degrades to facts-only (or 'delayed') inside the budget
    res = await run_brief(req.model_dump(exclude={"fresh"}), run_id, _checkpointer)
    res["run_id"] = run_id
    res["trace_id"] = tracing.current_trace_id()
    if res["mode"] in ("unavailable", "delayed"):
        pid = req.person_id
        if not pid and req.query:   # tools are down: resolve the name from our own store instead
            hits = await asyncio.to_thread(
                db.q, "SELECT id FROM person WHERE NOT is_partner AND (lower(name)=lower(%s) OR lower(email)=lower(%s))",
                (req.query.strip(), req.query.strip()))
            pid = hits[0]["id"] if len(hits) == 1 else None
        c = await asyncio.to_thread(_cached, meeting_id=req.meeting_id, person_id=pid)
        if c:
            res.update(brief=c["content"], mode="stale_cache", review=c["content"].get("review"))
            res["warnings"].append(f"Showing the last good brief from {c['created_at']:%Y-%m-%d %H:%M}.")
    if res["mode"] in ("llm", "fallback"):
        res["brief_id"] = await asyncio.to_thread(_store, res)
    return res


@app.get("/quiet")
def quiet(partner: str | None = None, limit: int = 100):
    return quiet_list(partner, limit)


@app.get("/egress")
def egress(limit: int = 50):
    return db.q("""SELECT id, at, host, path, purpose, allowed, status, left(request_body, 20000) AS request_body,
                          left(response_body, 5000) AS response_body
                   FROM egress_audit ORDER BY id DESC LIMIT %s""", (limit,))


@app.get("/stats")
def stats():
    r = db.q1("""SELECT (SELECT count(*) FROM message) AS messages, (SELECT count(*) FROM person WHERE NOT is_partner) AS people,
                        (SELECT count(*) FROM org) AS orgs, (SELECT count(*) FROM meeting) AS meetings,
                        (SELECT count(*) FROM edge) AS edges, (SELECT count(*) FROM commitment) AS commitments,
                        (SELECT count(*) FROM egress_audit) AS egress_calls,
                        (SELECT count(*) FROM egress_audit WHERE NOT allowed) AS egress_blocked""")
    r["by_status"] = {x["status"]: x["n"] for x in db.q("SELECT status, count(*) n FROM edge GROUP BY status")}
    r["as_of"] = clock.now().isoformat()
    q = db.q1("SELECT value FROM kv WHERE key='quarantine'")
    r["quarantined"] = q["value"] if q else []
    return r


@app.get("/partners")
def partners():
    return db.q("SELECT id, name, email FROM person WHERE is_partner ORDER BY name")


@app.get("/meetings/upcoming")
async def upcoming(partner: str | None = None, days: int = 1):
    try:
        async with mcp_tools() as t:
            return await t.call("upcoming_meetings", partner_email=partner, days=days)
    except ToolUnavailable as e:
        raise HTTPException(503, str(e))


@app.post("/precompute")
async def precompute(days: int = 1, concurrency: int = 4):
    """The 'brief ready before we ask' job: run nightly for tomorrow's meetings."""
    meetings = await upcoming(None, days)
    seen, todo = set(), []
    for m in meetings:
        if m["id"] not in seen and m["status"] == "confirmed":
            seen.add(m["id"])
            todo.append(m)
    sem = asyncio.Semaphore(concurrency)

    async def one(m):
        async with sem:
            return await brief(BriefRequest(meeting_id=m["id"], partner_email=m["partner_email"], fresh=True))
    results = await asyncio.gather(*(one(m) for m in todo), return_exceptions=True)
    ok = [r for r in results if isinstance(r, dict) and r.get("mode") == "llm"]
    return {"meetings": len(todo), "llm": len(ok), "other": len(results) - len(ok),
            "total_cost_usd": round(sum(r["cost_usd"] for r in ok), 4)}


@app.get("/health")
def health():
    db.q1("SELECT 1 AS ok")
    return {"ok": True}
