"""The system's notion of "now". Defaults to settings.as_of (the morning after the
backfill); the live feed advances it as newer mail arrives. Stored in kv so every
container agrees."""
from __future__ import annotations

import json
import time
from datetime import datetime

from hri import db
from hri.config.settings import settings

_cache: tuple[float, datetime] | None = None


def now() -> datetime:
    global _cache
    if _cache and time.time() - _cache[0] < 5:
        return _cache[1]
    r = db.q1("SELECT value FROM kv WHERE key = 'as_of'")
    t = datetime.fromisoformat(r["value"]) if r else settings().as_of
    _cache = (time.time(), t)
    return t


def advance(t: datetime):
    global _cache
    if t > now():
        db.q("""INSERT INTO kv (key, value) VALUES ('as_of', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps(t.isoformat()),))
        _cache = None
