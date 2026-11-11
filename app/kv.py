"""Redis helpers with an in-memory fallback (so local dev needs no Redis).
Used for three things that must be shared across app instances:
  1. dedupe   : WhatsApp redelivers a webhook if we are slow; never answer the same message id twice
  2. rate limit: cap messages per customer per minute (protects the LLM budget)
  3. lock     : one message at a time per customer, so two quick messages cannot race on the same order/session"""
import contextlib
import os
import threading
import time

REDIS_URL = os.environ.get("REDIS_URL", "")
_client = None
_mem = {}
_mem_lock = threading.Lock()


def client():
    global _client
    if _client is None and REDIS_URL:
        import redis
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def reset_memory() -> None:
    """Forget the in-memory counters, locks and queues (tests)."""
    with _mem_lock:
        _mem.clear()
        _mem_locks.clear()


def set_client(c):  # tests inject fakeredis here
    global _client
    _client = c


def _db() -> bool:
    """Serverless hosting (Vercel) has no shared memory between requests: keep dedupe, rate limits, queues and locks in the database."""
    return not client() and os.environ.get("KV_BACKEND") == "db"


def backend() -> str:
    return "redis" if client() else "db" if _db() else "memory"


def _run(sql: str, **params):
    from sqlalchemy import text

    from .database import session_scope
    from types import SimpleNamespace
    with session_scope() as s:
        res = s.execute(text(sql), params)
        rows = res.fetchall() if res.returns_rows else []   # read everything before the transaction commits
        return SimpleNamespace(rowcount=res.rowcount, rows=rows, scalar=lambda: rows[0][0] if rows else None, fetchall=lambda: rows)


def _db_set_nx(key: str, value: str, ttl: float) -> bool:
    """Set `key` unless a live value exists. True when this call set it."""
    now = time.time()
    _run("DELETE FROM kv_items WHERE key = :k AND expires_at <= :now", k=key, now=now)
    res = _run("INSERT INTO kv_items (key, value, n, expires_at) VALUES (:k, :v, 1, :exp) ON CONFLICT (key) DO NOTHING", k=key, v=value, exp=now + ttl)
    return res.rowcount == 1


def _mem_set_nx(key, ttl):
    now = time.time()
    with _mem_lock:
        if key in _mem and _mem[key][1] > now:
            return False
        _mem[key] = (1, now + ttl)
        return True


def first_time(message_id: str, ttl: int = 86400) -> bool:
    """True the first time we see this message id, False for redeliveries."""
    if not message_id:
        return True
    r = client()
    if r:
        return bool(r.set(f"wa:seen:{message_id}", 1, nx=True, ex=ttl))
    if _db():
        return _db_set_nx(f"wa:seen:{message_id}", "1", ttl)
    return _mem_set_nx(f"wa:seen:{message_id}", ttl)


def allow(customer: str, limit: int = 20, window: int = 60) -> bool:
    """Fixed-window rate limit: at most `limit` messages per `window` seconds per customer."""
    r = client()
    key = f"wa:rate:{customer}:{int(time.time() // window)}"
    if r:
        n = r.incr(key)
        if n == 1:
            r.expire(key, window + 5)
        return n <= limit
    if _db():
        now = time.time()
        _run("DELETE FROM kv_items WHERE key = :k AND expires_at <= :now", k=key, now=now)
        n = _run("INSERT INTO kv_items (key, value, n, expires_at) VALUES (:k, '', 1, :exp) "
                 "ON CONFLICT (key) DO UPDATE SET n = kv_items.n + 1 RETURNING n", k=key, exp=now + window + 5).scalar()
        return int(n) <= limit
    with _mem_lock:
        n = _mem.get(key, (0, 0))[0] + 1
        _mem[key] = (n, time.time() + window + 5)
    return n <= limit


def queue_push(key: str, item: str) -> None:
    r = client()
    if r:
        r.rpush(key, item)
        return
    if _db():
        _run("INSERT INTO kv_queue (key, item) VALUES (:k, :i)", k=key, i=item)
        return
    with _mem_lock:
        _mem.setdefault(key, ([], 0))[0].append(item)


def queue_pop_all(key: str) -> list[str]:
    """Take everything waiting in the queue, atomically."""
    r = client()
    if r:
        pipe = r.pipeline(transaction=True)
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        items, _ = pipe.execute()
        return list(items)
    if _db():
        rows = _run("DELETE FROM kv_queue WHERE key = :k RETURNING id, item", k=key).fetchall()
        return [item for _, item in sorted(rows)]
    with _mem_lock:
        items = list(_mem.get(key, ([], 0))[0])
        _mem[key] = ([], 0)
        return items


def queue_len(key: str) -> int:
    r = client()
    if r:
        return int(r.llen(key))
    if _db():
        return int(_run("SELECT count(*) FROM kv_queue WHERE key = :k", k=key).scalar())
    with _mem_lock:
        return len(_mem.get(key, ([], 0))[0])


_mem_locks: dict[str, tuple[str, float]] = {}


def try_lock(key: str, ttl: int = 300) -> str | None:
    """Non-blocking lock. Returns an owner token, or None when someone else holds it."""
    import uuid
    token = uuid.uuid4().hex
    r = client()
    if r:
        return token if r.set(key, token, nx=True, ex=ttl) else None
    if _db():
        return token if _db_set_nx(key, token, ttl) else None
    with _mem_lock:
        held = _mem_locks.get(key)
        if held and held[1] > time.time():
            return None
        _mem_locks[key] = (token, time.time() + ttl)
        return token


def unlock(key: str, token: str) -> None:
    r = client()
    if r:
        try:
            if r.get(key) == token:
                r.delete(key)
        except Exception:
            pass  # the key expires on its own
        return
    if _db():
        _run("DELETE FROM kv_items WHERE key = :k AND value = :v", k=key, v=token)
        return
    with _mem_lock:
        if _mem_locks.get(key, (None, 0))[0] == token:
            del _mem_locks[key]


@contextlib.contextmanager
def customer_lock(customer: str, timeout: int = 120):
    """Blocking per-customer lock (same lock as the inbound queue worker). Yields True once acquired, False after `timeout` seconds."""
    key = f"wa:lock:{customer}"
    deadline = time.time() + timeout
    token = None
    while token is None and time.time() < deadline:
        token = try_lock(key, max(int(timeout), 1))
        if token is None:
            time.sleep(0.05)
    try:
        yield token is not None
    finally:
        if token:
            unlock(key, token)
