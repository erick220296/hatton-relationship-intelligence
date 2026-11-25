"""Ingest-time NER: find names/places in mail bodies that the graph doesn't already know and add
them to the masking dictionary. Runs once over the backfill and on each live batch, so query-time
masking is a fast dictionary pass (~0.1 s) with the same coverage as running NER per request (~8 s)."""
from __future__ import annotations

import re
from functools import lru_cache

from hri import db

STOP = {"Hi", "Hello", "Thanks", "Best", "Regards", "Project", "Partner", "Partners", "Fund", "Monday", "Tuesday",
        "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "IC", "NDA", "SPA", "Q1", "Q2", "Q3", "Q4",
        "LLP", "CEO", "CFO", "COO", "Unsubscribe", "Hola", "Gracias", "Saludos", "Re", "Following"}
CAP = re.compile(r"^[A-Z][a-zA-Z'\-]+( [A-Z][a-zA-Z'\-]+)*$")


@lru_cache
def _nlp():
    import spacy
    return spacy.load("en_core_web_sm", disable=["lemmatizer", "tagger", "attribute_ruler", "parser"])


def terms_in(texts: list[str]) -> set[tuple[str, str]]:
    found = set()
    for doc in _nlp().pipe(texts, batch_size=256):
        for e in doc.ents:
            t = e.text.strip()
            if e.label_ not in ("PERSON", "GPE", "LOC", "FAC") or len(t) < 3 or not CAP.match(t):
                continue
            if any(w in STOP for w in t.split()) or re.search(r"(PERSON|NAME|ORG|EMAIL|PHONE)_", t):
                continue
            found.add((t, "NAME" if e.label_ == "PERSON" else "ADDRESS"))
    return found


def run(message_ids: list[str] | None = None, batch: int = 2000) -> int:
    known = {r["name"] for r in db.q("SELECT name FROM person")} | \
            {p for r in db.q("SELECT name FROM person") for p in r["name"].split()}
    where, params = ("WHERE id = ANY(%s)", (message_ids,)) if message_ids is not None else ("", None)
    rows = db.q(f"SELECT id, left(body, 3000) AS body FROM message {where} ORDER BY id", params)
    new = set()
    for i in range(0, len(rows), batch):
        new |= {t for t in terms_in([r["body"] for r in rows[i:i + batch]]) if t[0] not in known}
    with db.conn() as c, c.cursor() as cur:
        cur.executemany("INSERT INTO ner_term (term, kind) VALUES (%s, %s) ON CONFLICT DO NOTHING", sorted(new))
    return len(new)
