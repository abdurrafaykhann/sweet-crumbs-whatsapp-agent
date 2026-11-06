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


def set_client(c):  # tests inject fakeredis here
    global _client
    _client = c


def backend() -> str:
    return "redis" if client() else "memory"


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
    with _mem_lock:
        n = _mem.get(key, (0, 0))[0] + 1
        _mem[key] = (n, time.time() + window + 5)
    return n <= limit


def queue_push(key: str, item: str) -> None:
    r = client()
    if r:
        r.rpush(key, item)
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
    with _mem_lock:
        items = list(_mem.get(key, ([], 0))[0])
        _mem[key] = ([], 0)
        return items


def queue_len(key: str) -> int:
    r = client()
    if r:
        return int(r.llen(key))
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
