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
_locks = {}


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


@contextlib.contextmanager
def customer_lock(customer: str, timeout: int = 120):
    """One message at a time per customer. Redis: SET NX EX with an owner token, released by compare-and-delete (no Lua needed).
    Yields True when the lock was acquired within `timeout` seconds, False otherwise."""
    r = client()
    if r:
        import uuid
        key, token = f"wa:lock:{customer}", uuid.uuid4().hex
        deadline = time.time() + timeout
        got = False
        while not got and time.time() < deadline:
            got = bool(r.set(key, token, nx=True, ex=timeout))
            if not got:
                time.sleep(0.05)
        try:
            yield got
        finally:
            if got:
                try:
                    if r.get(key) == token:
                        r.delete(key)
                except Exception:
                    pass  # the key expires on its own after `timeout` seconds
    else:
        with _mem_lock:
            lk = _locks.setdefault(customer, threading.Lock())
        got = lk.acquire(timeout=timeout)
        try:
            yield got
        finally:
            if got:
                lk.release()
