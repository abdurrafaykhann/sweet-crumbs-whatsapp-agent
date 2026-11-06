"""Runs one scripted conversation against the real agent on a fresh copy of the seeded demo database.

Every run starts from the same data (the spec's demo customers and orders), so cases cannot affect each other."""
from __future__ import annotations

import datetime as dt
import pathlib
import shutil
import time
from dataclasses import dataclass, field

from app import agent, database, handoff, kv
from app import models as m
from app.database import session_scope

ROOT = pathlib.Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
TEMPLATE = CACHE / "template.db"
RUNDB = CACHE / "run.db"
DEFAULT_NOW = "2026-11-06T14:00:00"          # Friday, as in the spec
IMAGE = "[image]"                            # a turn that sends a payment screenshot instead of text


def _url(p: pathlib.Path) -> str:
    return f"sqlite:///{p.as_posix()}"


def fresh_db() -> None:
    """A clean copy of the seeded database (seeded once, then copied for every run)."""
    CACHE.mkdir(exist_ok=True)
    if not TEMPLATE.exists():
        from app.seed import seed_database
        database.reset_engine(_url(TEMPLATE))
        m.Base.metadata.create_all(database.get_engine())
        seed_database()
    database.reset_engine(_url(TEMPLATE)).dispose()   # release any open file before copying
    shutil.copy(TEMPLATE, RUNDB)
    database.reset_engine(_url(RUNDB))
    kv.set_client(None)
    kv.reset_memory()


@dataclass
class Turn:
    user: str
    reply: str = ""
    tools: list = field(default_factory=list)
    escalated: bool = False
    provider: str = ""
    seconds: float = 0.0
    tokens: int = 0


@dataclass
class Run:
    phone: str
    turns: list[Turn] = field(default_factory=list)
    placed_after: list[int] = field(default_factory=list)   # new orders for this phone after each turn
    draft_after: list[dict | None] = field(default_factory=list)
    error: str = ""                                          # the model was unavailable: the run says nothing about the agent

    @property
    def replies(self) -> list[str]:
        return [t.reply for t in self.turns]

    def tool_names(self) -> list[str]:
        return [x["name"] for t in self.turns for x in t.tools]


def _order_count(phone: str) -> int:
    with session_scope() as s:
        return s.query(m.Order).join(m.Customer).filter(m.Customer.phone == phone).count()


def run_script(phone: str, turns: list[str], graph, now: str = DEFAULT_NOW, pause: float = 0.0) -> Run:
    """Send each turn in order; record the reply, tools used and the order/draft state after it."""
    from app import devtools
    when = dt.datetime.fromisoformat(now)
    run = Run(phone=phone)
    base = _order_count(phone)
    for text in turns:
        t0 = time.perf_counter()
        if text == IMAGE:
            reply = handoff.handle_media(phone, "image", "eval-upload") or ""
            turn = Turn(text, reply, [], True)
        else:
            r = agent.respond(phone, text, graph=graph, now=when, retry_delay=1.0)
            turn = Turn(text, r.text, r.tools, r.escalated, r.provider, tokens=r.usage["input_tokens"] + r.usage["output_tokens"])
        turn.seconds = round(time.perf_counter() - t0, 1)
        run.turns.append(turn)
        run.placed_after.append(_order_count(phone) - base)
        run.draft_after.append(devtools.chat_state(phone, when)["draft"])
        if pause:
            time.sleep(pause)
    with session_scope() as s:
        bad = s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.reason.in_(["llm_unavailable", "assistant_error"])).count()
    if bad:
        run.error = "the model was unavailable or crashed during this run"
    return run
