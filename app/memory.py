"""Per-phone conversation memory: a LangGraph checkpointer keyed by thread_id = the customer's phone number."""
from __future__ import annotations

import os

from sqlalchemy.engine import make_url

from .database import DEFAULT_URL, normalize_url


def get_checkpointer(url: str | None = None):
    """SQLite file next to the app database (dev), PostgreSQL (production) or in-memory (tests / ':memory:')."""
    url = normalize_url(url or os.environ.get("DATABASE_URL") or DEFAULT_URL)
    if url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        pool = ConnectionPool(url.replace("+psycopg", "", 1), min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", "2")),
                              kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                              check=ConnectionPool.check_connection, open=True)  # a dead connection is replaced, not reused
        saver = PostgresSaver(pool)
        saver.setup()
        return saver
    db = make_url(url).database
    if not db or db == ":memory:":
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver
    conn = sqlite3.connect(f"{db}.memory", check_same_thread=False)
    return SqliteSaver(conn)
