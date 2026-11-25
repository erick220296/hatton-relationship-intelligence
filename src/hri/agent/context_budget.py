"""Context engineering: fit a relationship's history into a fixed token budget.

Priority order (highest first), each item tagged with the message IDs it came from:
  1. Profile + relationship facts (always included, small)
  2. Open commitments (extracted, with message IDs)
  3. Recent threads, newest first, full text (bodies trimmed of signatures)
  4. Older threads as one-line index entries (date, subject, msg id)
  5. Semantic-search hits relevant to the meeting

When the budget is exhausted, lower-priority items degrade (full -> index line)
and then drop; what was dropped is reported in the brief's metadata, so the
"exceed the context" failure demo shows exactly what the strategy did.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SIG_RE = re.compile(r"\n(Best|Regards|Kind regards|Thanks|Cheers|Saludos),?\n.*", re.S)


def est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def trim_body(body: str, limit: int = 1200) -> str:
    body = SIG_RE.sub("", body or "").strip()
    return body[:limit] + (" [...]" if len(body) > limit else "")


@dataclass
class Budgeted:
    text: str
    tokens: int
    message_ids: set[str] = field(default_factory=set)
    stats: dict = field(default_factory=dict)


def build_context(profile: dict, threads: list[dict], commitments: list[dict], index: list[dict],
                  search_hits: list[dict], budget: int) -> Budgeted:
    out: list[str] = []
    ids: set[str] = set()
    used = 0
    stats = {"budget": budget, "threads_full": 0, "threads_indexed": 0, "threads_dropped": 0,
             "commitments": 0, "search_hits": 0}

    def add(block: str, msg_ids=()) -> bool:
        nonlocal used
        t = est_tokens(block)
        if used + t > budget:
            return False
        out.append(block)
        ids.update(msg_ids)
        used += t
        return True

    rels = "\n".join(
        f"- partner {r['partner']}: status={r['status']}, warmth={r['warmth']} (peak {r['warmth_peak']}), "
        f"first contact {str(r['first_contact'])[:10]}, last contact {str(r['last_contact'])[:10]}, "
        f"last time THEY engaged {str(r['last_inbound'])[:10]}, {r['n_inbound']} inbound / {r['n_outbound']} "
        f"outbound emails, {r['n_meetings']} meetings"
        for r in profile.get("relationships", []))
    add(f"## PROFILE\nName: {profile['name']}\nTitle: {profile.get('title')}\nOrganisation: {profile.get('org')}"
        f" ({profile.get('kind')})\n## RELATIONSHIPS (computed from mail and calendar)\n{rels or '- none'}")

    if commitments:
        lines = ["## COMMITMENTS (extracted earlier; cite the message_id)"]
        for c in commitments[:15]:
            lines.append(f"- [{c['message_id']}] {str(c['sent_at'])[:10]} owner={c['owner']}: {c['text']}"
                         + (f" (due {c['due']})" if c.get("due") else ""))
        if add("\n".join(lines), [c["message_id"] for c in commitments[:15]]):
            stats["commitments"] = min(15, len(commitments))

    add("## RECENT THREADS (newest first)")
    shown_threads = set()
    for i, t in enumerate(threads):
        msgs = "\n".join(f"[{m['id']}] {str(m['sent_at'])[:10]} from {m['sender']}"
                         f"{' (Hatton)' if m.get('sender_is_partner') else ''}:\n{trim_body(m['body'])}"
                         for m in t["messages"][-6:])
        block = f"### Thread: {t['subject']}\n{msgs}"
        if add(block, [m["id"] for m in t["messages"][-6:]]):
            stats["threads_full"] += 1
            shown_threads.add(t["thread_id"])
        else:
            last = t["messages"][-1]
            line = f"- [{last['id']}] {str(last['sent_at'])[:10]} \"{t['subject']}\" ({len(t['messages'])} msgs, not expanded)"
            if add(line, [last["id"]]):
                stats["threads_indexed"] += 1
                shown_threads.add(t["thread_id"])
            else:
                stats["threads_dropped"] += 1

    older = [x for x in index if x["thread_id"] not in shown_threads]
    if older:
        add(f"## OLDER THREADS (index only, {len(older)} total)")
        for x in older:
            if add(f"- [{x['first_id']}] {str(x['started'])[:10]} \"{x['subject']}\" ({x['n_messages']} msgs)",
                   [x["first_id"]]):
                stats["threads_indexed"] += 1
            else:
                stats["threads_dropped"] += 1

    hits = [h for h in search_hits if h["id"] not in ids]
    if hits:
        add("## RELATED OLDER MESSAGES (semantic search)")
        for h in hits:
            if add(f"[{h['id']}] {str(h['sent_at'])[:10]} {h['subject']}:\n{trim_body(h['body'], 600)}", [h["id"]]):
                stats["search_hits"] += 1
    stats["tokens_used"] = used
    return Budgeted("\n\n".join(out), used, ids, stats)
