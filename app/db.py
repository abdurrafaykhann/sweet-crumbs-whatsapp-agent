"""Storage: catalog, orders, per-customer session state, message log, escalations.
One tiny adapter over two backends:
  - PostgreSQL when DATABASE_URL is set (production)
  - SQLite otherwise (local dev, tests, evals)
Callers use ``?`` placeholders and name- or index-based rows on both."""
import json
import os
import sqlite3
import time
from decimal import Decimal

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = os.environ.get("AGENT_DB", os.path.join(os.path.dirname(__file__), "..", "agent.db"))

from .shop import MENU

TABLES = ("variations", "products", "orders", "sessions", "messages", "escalations", "customers")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(id {PK}, name TEXT, category TEXT, aliases TEXT, description TEXT, ingredients TEXT,
    allergens TEXT, storage TEXT);
CREATE TABLE IF NOT EXISTS variations(id {PK}, product_id INTEGER, name TEXT, price INTEGER, stock INTEGER, lead_min INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS orders(id {PK}, customer TEXT, product_id INTEGER, qty INTEGER, total INTEGER, status TEXT,
    created {REAL}, items TEXT, delivery TEXT, address TEXT, note TEXT, fee INTEGER DEFAULT 0, ready_at TEXT, updated {REAL},
    payment_mode TEXT, due_now INTEGER DEFAULT 0, paid_amount INTEGER DEFAULT 0, payment_ref TEXT, payment_note TEXT, pay_by {REAL});
CREATE TABLE IF NOT EXISTS sessions(customer TEXT PRIMARY KEY, pending TEXT, lang TEXT, history TEXT);
CREATE TABLE IF NOT EXISTS messages(id {PK}, customer TEXT, role TEXT, text TEXT, ts {REAL},
    tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, latency_ms INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS customers(customer TEXT PRIMARY KEY, name TEXT, address TEXT);
CREATE TABLE IF NOT EXISTS escalations(id {PK}, customer TEXT, reason TEXT, ts {REAL}, resolved INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer);
CREATE INDEX IF NOT EXISTS idx_messages_customer ON messages(customer);
CREATE INDEX IF NOT EXISTS idx_variations_product ON variations(product_id);
"""


class Row(dict):
    """dict that also supports row[0] like a tuple (so both backends behave the same)."""

    def __getitem__(self, k):
        if isinstance(k, int):
            return list(self.values())[k]
        return super().__getitem__(k)


def _clean(v):
    return (int(v) if v == v.to_integral_value() else float(v)) if isinstance(v, Decimal) else v


def _pg_row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    return lambda values: Row(zip(names, [_clean(v) for v in values]))


class Cur:
    def __init__(self, cur):
        self._c = cur

    def fetchone(self):
        r = self._c.fetchone()
        return Row(r) if isinstance(r, sqlite3.Row) else r

    def fetchall(self):
        return [Row(r) if isinstance(r, sqlite3.Row) else r for r in self._c.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._c.rowcount


class Conn:
    def __init__(self, raw):
        self.raw = raw

    @staticmethod
    def _q(sql):
        return sql.replace("%", "%%").replace("?", "%s") if PG else sql  # literal % (LIKE) must be escaped for psycopg

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._q(sql), tuple(params))
        return Cur(cur)

    def executemany(self, sql, rows):
        cur = self.raw.cursor()
        cur.executemany(self._q(sql), rows)

    def insert_id(self, sql, params=()):
        """INSERT and return the new row id on either backend."""
        if PG:
            cur = self.raw.cursor()
            cur.execute(self._q(sql) + " RETURNING id", tuple(params))
            return cur.fetchone()["id"]
        return self.raw.execute(sql, tuple(params)).lastrowid

    def script(self, text):
        if PG:
            cur = self.raw.cursor()
            for stmt in [s.strip() for s in text.split(";") if s.strip()]:
                cur.execute(stmt)
        else:
            self.raw.executescript(text)

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def conn() -> Conn:
    if PG:
        import psycopg
        return Conn(psycopg.connect(DATABASE_URL, row_factory=_pg_row_factory))
    raw = sqlite3.connect(DB_PATH, timeout=30)
    raw.row_factory = sqlite3.Row
    return Conn(raw)


def _ddl():
    return SCHEMA.format(PK="BIGSERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY", REAL="DOUBLE PRECISION" if PG else "REAL")


def _drop_all(c):
    for t in TABLES:
        c.execute(f"DROP TABLE IF EXISTS {t}" + (" CASCADE" if PG else ""))


def init(reset=False):
    c = conn()
    if reset:
        _drop_all(c)
    else:
        try:  # databases from earlier demo versions lack newer columns: rebuild them (demo data only)
            c.execute("SELECT category FROM products LIMIT 1")
            c.execute("SELECT payment_mode FROM orders LIMIT 1")
        except Exception:
            c.raw.rollback()
            if c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='products'").fetchone()[0] if not PG else _pg_has(c, "products"):
                _drop_all(c)
    c.script(_ddl())
    if c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        seed_menu(c)
    c.commit()
    c.close()


def _pg_has(c, table):
    return c.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?", (table,)).fetchone()[0]


def seed_menu(c):
    for p in MENU:
        pid = c.insert_id("INSERT INTO products(name,category,aliases,description,ingredients,allergens,storage) VALUES(?,?,?,?,?,?,?)",
                          (p["name"], p["category"], p["aliases"], p["description"], p["ingredients"], p["allergens"], p["storage"]))
        for v in p["variations"]:
            c.execute("INSERT INTO variations(product_id,name,price,stock,lead_min,note) VALUES(?,?,?,?,?,?)",
                      (pid, v["name"], v["price"], v["stock"], v["lead_min"], v["note"]))


def find_product(c, query: str):
    q = (query or "").strip().lower()
    if not q:
        return None
    best = None
    for p in c.execute("SELECT * FROM products"):
        names = [p["name"].lower()] + [a.strip().lower() for a in p["aliases"].split(",")]
        for n in names:
            if q == n:
                return p
            if n and (n in q or q in n):
                # prefer longer alias matches ("chocolate cake" over "cake")
                if best is None or len(n) > best[0]:
                    best = (len(n), p)
    return best[1] if best else None


def get_session(c, customer):
    r = c.execute("SELECT * FROM sessions WHERE customer=?", (customer,)).fetchone()
    if not r:
        return {"pending": None, "lang": "en", "history": [], "turn": 0}
    h = json.loads(r["history"] or "[]")
    turn = 0
    if h and isinstance(h[0], dict) and h[0].get("_turn") is not None:
        turn = h[0]["_turn"]
        h = h[1:]
    return {"pending": json.loads(r["pending"]) if r["pending"] else None, "lang": r["lang"], "history": h, "turn": turn}


def save_session(c, customer, s):
    c.execute(
        "INSERT INTO sessions(customer,pending,lang,history) VALUES(?,?,?,?) "
        "ON CONFLICT(customer) DO UPDATE SET pending=excluded.pending, lang=excluded.lang, history=excluded.history",
        (customer, json.dumps(s["pending"]) if s["pending"] else None, s["lang"],
         json.dumps([{"_turn": s.get("turn", 0)}] + s["history"][-30:])),
    )


def log_message(c, customer, role, text, tin=0, tout=0, ms=0):
    c.execute("INSERT INTO messages(customer,role,text,ts,tokens_in,tokens_out,latency_ms) VALUES(?,?,?,?,?,?,?)",
              (customer, role, text, time.time(), tin, tout, ms))


def get_customer(c, customer):
    r = c.execute("SELECT * FROM customers WHERE customer=?", (customer,)).fetchone()
    return dict(r) if r else {"name": None, "address": None}


def save_customer(c, customer, name=None, address=None):
    cur = get_customer(c, customer)
    c.execute("INSERT INTO customers(customer,name,address) VALUES(?,?,?) "
              "ON CONFLICT(customer) DO UPDATE SET name=excluded.name, address=excluded.address",
              (customer, (name or cur["name"]), (address or cur["address"])))


import re as _re


def _norm_var(t: str) -> str:
    t = (t or "").lower().strip()
    t = _re.sub(r"(\d+(?:\.\d+)?)\s*(?:lbs?|pounds?|pound)\b", r"\1 lb", t)
    return _re.sub(r"\s+", " ", t)


def list_variations(c, product_id):
    return [dict(r) for r in c.execute("SELECT * FROM variations WHERE product_id=? ORDER BY id", (product_id,))]


def find_variation(c, product_id, text):
    """Pick the variation a customer meant ("2 lb", "walnut", "chicken cheese"); None if unclear.
    A product with a single variation is its own default."""
    vs = list_variations(c, product_id)
    if len(vs) == 1:
        return vs[0]
    q = _norm_var(text)
    if not q:
        return None
    prow = c.execute("SELECT name, aliases FROM products WHERE id=?", (product_id,)).fetchone()
    own = set(_re.findall(r"[a-z]{4,}", (prow["name"] + " " + prow["aliases"]).lower())) if prow else set()  # words of the product itself
    best, best_score = None, 0
    for v in vs:
        n = _norm_var(v["name"])
        if q == n:
            return v
        score = 0
        if _re.search(r"(?<![\w.])" + _re.escape(n) + r"(?![\w])", q):
            score = 100 + len(n)
        else:
            words = [w for w in _re.findall(r"[a-z]{4,}", n) if w not in own]
            score = 10 * sum(1 for w in words if w in q.split())
        if score > best_score:
            best, best_score = v, score
        elif score == best_score and score > 0:
            best = None  # ambiguous between two variations
    return best
