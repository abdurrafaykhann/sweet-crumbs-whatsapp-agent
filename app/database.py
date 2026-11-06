"""SQLAlchemy engine and sessions. SQLite for development, PostgreSQL via DATABASE_URL.
The default file is sweetcrumbs.db (the legacy prototype used agent.db with a different schema)."""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "sqlite:///./sweetcrumbs.db"


def normalize_url(url: str) -> str:
    """Accept postgres:// and postgresql:// and always use the psycopg 3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str | None = None):
    url = normalize_url(url or os.environ.get("DATABASE_URL") or DEFAULT_URL)
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # WAL = readers do not block the writer; FK enforcement like Postgres
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        _engine = make_engine()
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine(url: str | None = None):
    """Point the app at another database (tests use this)."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope():
    get_engine()
    s: Session = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
