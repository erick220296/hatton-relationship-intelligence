"""In-region PII masking with deterministic, reversible pseudonyms.

Pipeline (all local, nothing leaves the container):
  1. Regex: emails, phone numbers, URLs, UK-postcode address lines.
  2. Dictionary: every known person (full, first, last name), organisation and
     deal codename from the graph -> stable token (PERSON_1a2b3c, ORG_..).
  3. Optional NER sweep (Presidio + spaCy) for names the graph doesn't know.

Tokens are HMAC-derived, so the same real value always gets the same token
(the model can reason about "PERSON_1a2b3c said X, then PERSON_1a2b3c ..."),
and the mapping back lives only in the `pseudonym` table in Postgres.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
from functools import lru_cache

from hri import db

SALT = os.getenv("PSEUDONYM_SALT", "hatton-demo-salt").encode()

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://\S+")
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s.-]?)?(?:\(?\d{2,5}\)?[\s.-]?){2,4}\d{3,4}")
POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")
PROJECT_RE = re.compile(r"\bProject [A-Z][a-z]+\b")
TOKEN_RE = re.compile(r"\b(PERSON|NAME|ORG|EMAIL|PHONE|ADDRESS|URL|PROJECT|DOMAIN)_[0-9a-f]{6}\b")


def token_for(kind: str, real: str) -> str:
    h = hmac.new(SALT, f"{kind}:{real}".encode(), hashlib.sha256).hexdigest()[:6]
    return f"{kind}_{h}"


class Masker:
    def __init__(self, use_ner: bool = False):
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], str] = {}
        self.reverse: dict[str, str] = {}
        self._dict_re: re.Pattern | None = None
        self._dict: dict[str, tuple[str, str]] = {}
        self.use_ner = use_ner
        self._ner = None

    # -------------------------------------------------------------- loading
    def load_known_entities(self, rows: list[dict] | None = None):
        """rows: [{name, org}] - defaults to everything in the graph."""
        if rows is None:
            rows = db.q("SELECT p.name, o.name AS org FROM person p LEFT JOIN org o ON o.id = p.org_id")
        d: dict[str, tuple[str, str]] = {}
        for r in rows:
            name = (r.get("name") or "").strip()
            if name:
                d[name] = ("PERSON", name)
                parts = name.split()
                for part in (parts[0], parts[-1]):
                    if len(part) > 2 and part not in d:
                        d[part] = ("NAME", part)
            org = (r.get("org") or "").strip()
            if org and len(org) > 3:
                d[org] = ("ORG", org)
        self._dict = d
        keys = sorted(d, key=len, reverse=True)
        self._dict_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b") if keys else None

    # -------------------------------------------------------------- masking
    def _tok(self, kind: str, real: str) -> str:
        t = token_for(kind, real)
        self._pending[(kind, real)] = t
        self.reverse[t] = real
        return t

    def mask(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            out_lines = []
            for line in text.split("\n"):
                if POSTCODE_RE.search(line):
                    out_lines.append(self._tok("ADDRESS", line.strip()))
                else:
                    out_lines.append(line)
            text = "\n".join(out_lines)
            text = EMAIL_RE.sub(lambda m: self._tok("EMAIL", m.group(0).lower()), text)
            text = URL_RE.sub(lambda m: self._tok("URL", m.group(0)), text)
            text = PHONE_RE.sub(lambda m: self._tok("PHONE", m.group(0))
                                if sum(ch.isdigit() for ch in m.group(0)) >= 9 else m.group(0), text)
            text = PROJECT_RE.sub(lambda m: self._tok("PROJECT", m.group(0)), text)
            if self._dict_re is not None:
                text = self._dict_re.sub(lambda m: self._tok(*self._dict[m.group(0)]), text)
            if self.use_ner:
                text = self._ner_sweep(text)
            return text

    def _ner_sweep(self, text: str) -> str:
        if self._ner is None:
            self._ner = _presidio()
        results = self._ner.analyze(text=text, language="en", entities=["PERSON", "LOCATION"])
        for r in sorted(results, key=lambda r: r.start, reverse=True):
            span = text[r.start:r.end]
            if TOKEN_RE.fullmatch(span) or r.score < 0.6:
                continue
            kind = "NAME" if r.entity_type == "PERSON" else "ADDRESS"
            text = text[:r.start] + self._tok(kind, span) + text[r.end:]
        return text

    def flush(self):
        """Persist new token mappings to the in-region vault."""
        with self._lock:
            pending, self._pending = self._pending, {}
        if not pending:
            return
        with db.conn() as c:
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO pseudonym (kind, real, token) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    [(k, r, t) for (k, r), t in pending.items()])



def unmask(text: str, extra: dict[str, str] | None = None) -> str:
    """Replace tokens with real values from the vault. Runs in-region only."""
    if not text:
        return text
    found = {m.group(0) for m in TOKEN_RE.finditer(text)}
    if not found:
        return text
    lookup = dict(_masker.reverse) if _masker else {}
    lookup.update(extra or {})
    missing = [t for t in found if t not in lookup]
    if missing:
        for r in db.q("SELECT token, real FROM pseudonym WHERE token = ANY(%s)", (missing,)):
            lookup[r["token"]] = r["real"]
    return TOKEN_RE.sub(lambda m: lookup.get(m.group(0), m.group(0)), text)


def _presidio():
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]})
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


_masker: Masker | None = None


def masker() -> Masker:
    global _masker
    if _masker is None:
        _masker = Masker(use_ner=os.getenv("MASK_NER", "1") == "1")
        _masker.load_known_entities()
    return _masker
