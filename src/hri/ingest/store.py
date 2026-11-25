"""Write parsed messages / meetings / contacts into the graph store (idempotent)."""
from __future__ import annotations

import json
from datetime import datetime

from hri import db
from hri.config.settings import vertical
from hri.ingest.parse import Addr, Malformed, ParsedMessage, parse


class Store:
    """Caches person/org ids so a 40k backfill doesn't do 100k lookups."""

    def __init__(self):
        self.person_ids: dict[str, int] = {r["email"]: r["id"] for r in db.q("SELECT id, email FROM person")}
        self.org_ids: dict[str, int] = {r["domain"]: r["id"] for r in db.q("SELECT id, domain FROM org")}
        self.internal = set(vertical()["internal_domains"])
        self.quarantined: list[dict] = []
        self.skipped_bulk = 0

    def org(self, domain: str, name: str | None = None, kind: str | None = None) -> int:
        if domain not in self.org_ids:
            r = db.q1("""INSERT INTO org (name, domain, kind) VALUES (%s, %s, %s)
                         ON CONFLICT (domain) DO UPDATE SET name = COALESCE(org.name, EXCLUDED.name)
                         RETURNING id""",
                      (name or domain.split(".")[0].title(), domain,
                       "internal" if domain in self.internal else (kind or "other")))
            self.org_ids[domain] = r["id"]
        return self.org_ids[domain]

    def person(self, a: Addr, title=None, company=None, kind=None) -> int:
        if a.email not in self.person_ids:
            domain = a.email.split("@")[1]
            personal = domain in ("mailbox.example", "post.example", "inbox.example")
            org_id = None if personal and not company else self.org(domain, company, kind)
            r = db.q1("""INSERT INTO person (name, email, org_id, title, is_partner) VALUES (%s,%s,%s,%s,%s)
                         ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                      (a.name, a.email, org_id, title, domain in self.internal))
            self.person_ids[a.email] = r["id"]
        return self.person_ids[a.email]

    def load_contacts(self, rows: list[dict]):
        for c in rows:
            domain = c["email"].split("@")[1]
            if c.get("company"):
                self.org(domain, c["company"], c.get("kind"))
            self.person(Addr(c["name"], c["email"].lower()), c.get("title"), c.get("company") or None, c.get("kind"))

    def add_message(self, raw: dict) -> ParsedMessage | None:
        try:
            m = parse(raw, tuple(vertical()["bulk_markers"]))
        except Malformed as e:
            self.quarantined.append({"id": raw.get("id") if isinstance(raw, dict) else None, "reason": str(e)})
            return None
        if m.bulk:
            self.skipped_bulk += 1
            return None
        sender = self.person(m.sender)
        rcpts = [(self.person(a), "to") for a in m.to] + [(self.person(a), "cc") for a in m.cc]
        with db.conn() as c:
            c.execute("""INSERT INTO message (id, thread_id, mailbox, sent_at, from_id, subject, body, in_reply_to)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                      (m.id, m.thread_id, m.mailbox, m.sent_at, sender, m.subject, m.body, m.in_reply_to))
            with c.cursor() as cur:
                cur.executemany("""INSERT INTO message_recipient (message_id, person_id, kind) VALUES (%s,%s,%s)
                                   ON CONFLICT DO NOTHING""", [(m.id, pid, k) for pid, k in rcpts])
        return m

    def add_meeting(self, mt: dict):
        org_id = self.person_ids.get(mt["organizer"].lower())
        db.q("""INSERT INTO meeting (id, title, start_at, end_at, organizer, status, location)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, start_at = EXCLUDED.start_at""",
             (mt["id"], mt["title"], datetime.fromisoformat(mt["start"]), datetime.fromisoformat(mt["end"]),
              org_id, mt.get("status", "confirmed"), mt.get("location")))
        with db.conn() as c, c.cursor() as cur:
            cur.executemany("INSERT INTO meeting_attendee VALUES (%s,%s) ON CONFLICT DO NOTHING",
                            [(mt["id"], self.person(Addr(e.split("@")[0], e.lower()))) for e in mt["attendees"]])

    def save_quarantine(self):
        db.q("""INSERT INTO kv (key, value) VALUES ('quarantine', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps(self.quarantined),))
