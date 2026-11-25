"""MCP server exposing the relationship graph to the briefing agent.

Every tool is READ-ONLY by construction:
  * the server connects as Postgres role `hri_ro` (SELECT only, no vault access);
  * tools are annotated readOnlyHint=True;
  * there is no tool that sends mail, books meetings or contacts anyone.
tests/test_no_outbound.py enforces all three.

Data returned here is unmasked (it stays in-region); the agent masks it before
any third-party model call.
"""
from __future__ import annotations

import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from hri import db
from hri import clock

mcp = FastMCP("hatton-relationship-graph", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8001")),
              stateless_http=True, json_response=True)
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _clean(rows):
    return [{k: _iso(v) for k, v in r.items()} for r in rows]


@mcp.tool(annotations=RO)
def find_person(query: str, partner_email: str | None = None) -> list[dict]:
    """Find counterparties by name or email. Returns all matches (names can collide) with
    their organisation and which partners know them, strongest relationship first."""
    rows = db.q("""
        SELECT p.id AS person_id, p.name, p.email, p.title, o.name AS org, o.kind,
               COALESCE(json_agg(json_build_object('partner', pa.email, 'warmth', e.warmth, 'status', e.status)
                        ORDER BY e.warmth DESC) FILTER (WHERE e.partner_id IS NOT NULL), '[]') AS partners,
               COALESCE(max(e.warmth) FILTER (WHERE %(pe)s::text IS NULL OR pa.email = %(pe)s), -1) AS rank
        FROM person p LEFT JOIN org o ON o.id = p.org_id
        LEFT JOIN edge e ON e.person_id = p.id LEFT JOIN person pa ON pa.id = e.partner_id
        WHERE NOT p.is_partner AND (lower(p.name) = lower(%(q)s) OR lower(p.email) = lower(%(q)s)
              OR p.name ILIKE %(like)s)
        GROUP BY p.id, o.name, o.kind ORDER BY rank DESC LIMIT 10""",
        {"q": query.strip(), "like": f"%{query.strip()}%", "pe": partner_email})
    return _clean(rows)


@mcp.tool(annotations=RO)
def get_relationship(person_id: int) -> dict:
    """Profile of one counterparty and every partner's relationship with them (warmth, status, counts, dates)."""
    p = db.q1("""SELECT p.id AS person_id, p.name, p.email, p.title, o.name AS org, o.kind
                 FROM person p LEFT JOIN org o ON o.id = p.org_id WHERE p.id = %s""", (person_id,))
    if not p:
        return {"error": "unknown person_id"}
    edges = db.q("""SELECT pa.name AS partner, pa.email AS partner_email, e.first_contact, e.last_contact,
                           e.last_inbound, e.n_inbound, e.n_outbound, e.n_meetings, e.warmth, e.warmth_peak,
                           e.status, e.explanation
                    FROM edge e JOIN person pa ON pa.id = e.partner_id
                    WHERE e.person_id = %s ORDER BY e.warmth DESC""", (person_id,))
    colleagues = db.q("""SELECT p2.name, p2.title FROM person p2 JOIN person p ON p.org_id = p2.org_id
                         WHERE p.id = %s AND p2.id <> p.id AND p.org_id IS NOT NULL LIMIT 8""", (person_id,))
    return {**_clean([p])[0], "relationships": _clean(edges), "colleagues_at_org": colleagues}


@mcp.tool(annotations=RO)
def get_recent_threads(person_id: int, limit_threads: int = 8, max_messages: int = 400) -> list[dict]:
    """Most recent email threads involving the person, newest first, with full message text and message IDs.
    Only messages up to the system's as-of time are returned."""
    rows = db.q("""
        WITH mine AS (
          SELECT DISTINCT m.thread_id FROM message m
          LEFT JOIN message_recipient r ON r.message_id = m.id
          WHERE (m.from_id = %(p)s OR r.person_id = %(p)s) AND m.sent_at <= %(asof)s),
        ranked AS (
          SELECT m.thread_id, max(m.sent_at) AS last_at FROM message m JOIN mine USING (thread_id)
          WHERE m.sent_at <= %(asof)s GROUP BY m.thread_id ORDER BY last_at DESC LIMIT %(lt)s)
        SELECT m.id, m.thread_id, m.sent_at, s.name AS sender, s.is_partner AS sender_is_partner,
               m.subject, m.body, ts.topic
        FROM message m JOIN ranked USING (thread_id) JOIN person s ON s.id = m.from_id
        LEFT JOIN thread_summary ts ON ts.thread_id = m.thread_id
        WHERE m.sent_at <= %(asof)s
        ORDER BY ranked.last_at DESC, m.sent_at ASC LIMIT %(mm)s""",
        {"p": person_id, "lt": limit_threads, "mm": max_messages, "asof": clock.now()})
    threads: dict[str, dict] = {}
    for r in _clean(rows):
        t = threads.setdefault(r["thread_id"], {"thread_id": r["thread_id"], "subject": r["subject"],
                                                "topic": r["topic"], "messages": []})
        t["messages"].append({k: r[k] for k in ("id", "sent_at", "sender", "sender_is_partner", "body")})
    return list(threads.values())


@mcp.tool(annotations=RO)
def get_thread_index(person_id: int, skip_threads: int = 0, limit: int = 200) -> list[dict]:
    """Compact index (date, subject, sender, message count) of older threads - used when the full
    history does not fit the context budget."""
    return _clean(db.q("""
        SELECT m.thread_id, min(m.sent_at) AS started, max(m.sent_at) AS last_at, count(*) AS n_messages,
               (array_agg(m.subject ORDER BY m.sent_at))[1] AS subject, (array_agg(m.id ORDER BY m.sent_at))[1] AS first_id
        FROM message m LEFT JOIN message_recipient r ON r.message_id = m.id
        WHERE (m.from_id = %(p)s OR r.person_id = %(p)s) AND m.sent_at <= %(asof)s
        GROUP BY m.thread_id ORDER BY last_at DESC OFFSET %(s)s LIMIT %(l)s""",
        {"p": person_id, "s": skip_threads, "l": limit, "asof": clock.now()}))


@mcp.tool(annotations=RO)
def get_commitments(person_id: int, limit: int = 30) -> list[dict]:
    """Commitments extracted from threads with this person (owner us|them), newest first, with source message IDs."""
    return _clean(db.q("""
        SELECT c.message_id, c.thread_id, c.owner, c.text, c.due, m.sent_at
        FROM commitment c JOIN message m ON m.id = c.message_id
        WHERE m.sent_at <= %(asof)s AND c.thread_id IN (
            SELECT DISTINCT m2.thread_id FROM message m2 LEFT JOIN message_recipient r ON r.message_id = m2.id
            WHERE m2.from_id = %(p)s OR r.person_id = %(p)s)
        ORDER BY m.sent_at DESC LIMIT %(l)s""", {"p": person_id, "l": limit, "asof": clock.now()}))


@mcp.tool(annotations=RO)
def get_meeting(meeting_id: str) -> dict:
    """A calendar event and its attendees (partners flagged)."""
    mt = db.q1("SELECT id, title, start_at, end_at, status, location FROM meeting WHERE id = %s", (meeting_id,))
    if not mt:
        return {"error": "unknown meeting_id"}
    att = db.q("""SELECT p.id AS person_id, p.name, p.email, p.is_partner FROM meeting_attendee a
                  JOIN person p ON p.id = a.person_id WHERE a.meeting_id = %s""", (meeting_id,))
    return {**_clean([mt])[0], "attendees": att}


@mcp.tool(annotations=RO)
def upcoming_meetings(partner_email: str | None = None, days: int = 1) -> list[dict]:
    """Confirmed meetings in the next `days` days from the as-of time, optionally for one partner."""
    return _clean(db.q("""
        SELECT mt.id, mt.title, mt.start_at, mt.status, pa.email AS partner_email
        FROM meeting mt JOIN meeting_attendee a ON a.meeting_id = mt.id JOIN person pa ON pa.id = a.person_id
        WHERE pa.is_partner AND mt.start_at BETWEEN %(t)s AND %(t)s + make_interval(days => %(d)s)
          AND (%(pe)s::text IS NULL OR pa.email = %(pe)s)
        ORDER BY mt.start_at""", {"t": clock.now(), "d": days, "pe": partner_email}))


@mcp.tool(annotations=RO)
def search_messages(person_id: int, query: str, k: int = 5) -> list[dict]:
    """Semantic search (local embeddings) over this person's messages - finds older relevant context."""
    from hri.ingest.embed import embed_one
    vec = embed_one(query)
    return _clean(db.q("""
        SELECT m.id, m.thread_id, m.sent_at, m.subject, left(m.body, 1200) AS body
        FROM message m WHERE m.sent_at <= %(asof)s AND m.embedding IS NOT NULL AND m.id IN (
            SELECT m2.id FROM message m2 LEFT JOIN message_recipient r ON r.message_id = m2.id
            WHERE m2.from_id = %(p)s OR r.person_id = %(p)s)
        ORDER BY m.embedding <=> %(v)s LIMIT %(k)s""", {"p": person_id, "v": vec, "k": k, "asof": clock.now()}))


@mcp.tool(annotations=RO)
def quiet_relationships(partner_email: str | None = None, limit: int = 50) -> list[dict]:
    """Relationships that used to be warm and have gone quiet, strongest former relationship first."""
    from hri.graph.warmth import quiet_list
    return _clean(quiet_list(partner_email, limit))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
