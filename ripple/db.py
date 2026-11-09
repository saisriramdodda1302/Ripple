import atexit
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def _configure(conn):
    register_vector(conn)


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url, min_size=1, max_size=8, open=True,
            kwargs={"row_factory": dict_row}, configure=_configure,
        )
        atexit.register(_pool.close)
    return _pool


@contextmanager
def connect():
    with pool().connection() as conn:
        yield conn


def init_schema(dim: int | None = None):
    sql = SCHEMA_PATH.read_text().replace("{DIM}", str(dim or settings.embed_dim))
    # the vector extension has to exist before we register the type on the pool's connections
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(sql)


def fetch_all(sql, params=None):
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql, params=None):
    with connect() as conn:
        return conn.execute(sql, params).fetchone()
