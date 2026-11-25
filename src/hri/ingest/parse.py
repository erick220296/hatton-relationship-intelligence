"""Validate and normalise raw mail records. Malformed input is quarantined, never fatal."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime

MAX_BODY_CHARS = 50_000
_TAG_RE = re.compile(r"<[^>]+>")


class Malformed(ValueError):
    pass


@dataclass
class Addr:
    name: str
    email: str


@dataclass
class ParsedMessage:
    id: str
    thread_id: str
    mailbox: str
    sent_at: datetime
    sender: Addr
    to: list[Addr]
    cc: list[Addr]
    subject: str
    body: str
    in_reply_to: str | None
    bulk: bool
    truncated: bool


def _addr(a) -> Addr:
    if not isinstance(a, dict) or not a.get("email") or "@" not in a["email"]:
        raise Malformed(f"bad address: {a!r}")
    return Addr((a.get("name") or a["email"].split("@")[0]).strip(), a["email"].strip().lower())


def html_to_text(s: str) -> str:
    if "<" in s and ">" in s and re.search(r"</?(html|body|p|div|br|b)\b", s, re.I):
        s = re.sub(r"(?i)<br\s*/?>|</p>", "\n", s)
        s = html.unescape(_TAG_RE.sub("", s))
    return s


def parse(rec: dict, bulk_markers=("List-Unsubscribe",)) -> ParsedMessage:
    if not isinstance(rec, dict):
        raise Malformed("record is not an object")
    for k in ("id", "thread_id", "mailbox", "date"):
        if not rec.get(k):
            raise Malformed(f"missing {k}")
    try:
        sent_at = datetime.fromisoformat(str(rec["date"]))
    except ValueError as e:
        raise Malformed(f"unparseable date {rec['date']!r}") from e
    if sent_at.tzinfo is None:
        raise Malformed("date without timezone")
    sender = _addr(rec.get("from"))
    to = [_addr(a) for a in rec.get("to") or []]
    cc = [_addr(a) for a in rec.get("cc") or []]
    if not to and not cc:
        raise Malformed("no recipients")
    body = html_to_text(rec.get("body") or "")
    truncated = len(body) > MAX_BODY_CHARS
    if len(body) > 20 * MAX_BODY_CHARS:
        raise Malformed(f"body too large ({len(body)} chars)")
    headers = rec.get("headers") or {}
    return ParsedMessage(rec["id"], rec["thread_id"], rec["mailbox"].lower(), sent_at, sender, to, cc,
                         (rec.get("subject") or "").strip()[:500], body[:MAX_BODY_CHARS], rec.get("in_reply_to"),
                         any(m in headers for m in bulk_markers), truncated)
