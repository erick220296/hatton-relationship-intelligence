from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from hri.config.settings import settings


def _configure(conn):
    register_vector(conn)


@lru_cache
def pool() -> ConnectionPool:
    return ConnectionPool(settings().database_url, min_size=1, max_size=20,
                          kwargs={"row_factory": dict_row, "autocommit": True},
                          configure=_configure, open=True)


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def q(sql: str, params=None) -> list[dict]:
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def q1(sql: str, params=None) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None
