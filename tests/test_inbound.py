"""Per-customer queue: ordered, never two model calls in parallel for one customer, quick follow-ups answered together."""
import threading
import time

import fakeredis
import pytest

from app import inbound, kv
from app import database
from app import models as m


class Handler:
    def __init__(self, hold=None, text_prefix="re: "):
        self.calls, self.active, self.max_active, self.hold, self.prefix = [], 0, 0, hold, text_prefix
        self.entered = threading.Event()

    def __call__(self, phone, text, **kw):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(text)
        self.entered.set()
        if self.hold is not None and len(self.calls) == 1:
            self.hold.wait(5)
        self.active -= 1
        return type("R", (), {"text": self.prefix + text, "silent": False})()


@pytest.fixture(params=["memory", "redis"])
def backend(request, db):
    kv.set_client(fakeredis.FakeRedis(decode_responses=True) if request.param == "redis" else None)
    yield request.param
    kv.set_client(None)


def test_messages_are_answered_in_order(backend, monkeypatch):
    h, sent = Handler(), []
    monkeypatch.setattr(inbound.agent, "respond", h)
    inbound.process_incoming("923001110001", "first", lambda p, t: sent.append(t))
    inbound.process_incoming("923001110001", "second", lambda p, t: sent.append(t))
    assert h.calls == ["first", "second"] and sent == ["re: first", "re: second"]


def test_quick_follow_ups_are_answered_together_and_never_in_parallel(backend, monkeypatch):
    hold, sent = threading.Event(), []
    h = Handler(hold=hold)
    monkeypatch.setattr(inbound.agent, "respond", h)
    phone = "923001110002"
    first = threading.Thread(target=inbound.process_incoming, args=(phone, "price of black forest", lambda p, t: sent.append(t)))
    first.start()
    assert h.entered.wait(5)                                                  # the first message is being answered
    inbound.process_incoming(phone, "and 2 lb", lambda p, t: sent.append(t))   # arrive meanwhile: queued, returns at once
    inbound.process_incoming(phone, "for tomorrow", lambda p, t: sent.append(t))
    assert h.calls == ["price of black forest"]
    hold.set()
    first.join(5)
    assert h.calls == ["price of black forest", "and 2 lb\nfor tomorrow"]      # one model call for the two follow-ups
    assert h.max_active == 1 and sent == ["re: price of black forest", "re: and 2 lb\nfor tomorrow"]


def test_each_raw_message_is_logged_for_the_inbox(backend, monkeypatch):
    monkeypatch.setattr(inbound.agent, "respond", Handler())
    inbound.process_incoming("923001110003", "one", lambda p, t: None)
    inbound.process_incoming("923001110003", "two", lambda p, t: None)
    with database.session_scope() as s:
        rows = s.query(m.Message).filter(m.Message.phone == "923001110003", m.Message.role == "customer").order_by(m.Message.id).all()
    assert [r.text for r in rows] == ["one", "two"]


def test_different_customers_do_not_wait_for_each_other(backend, monkeypatch):
    hold = threading.Event()
    slow, fast_sent = Handler(hold=hold), []
    calls = {}

    def respond(phone, text, **kw):
        calls[phone] = text
        return slow(phone, text) if phone.endswith("1") else type("R", (), {"text": "quick", "silent": False})()
    monkeypatch.setattr(inbound.agent, "respond", respond)
    t = threading.Thread(target=inbound.process_incoming, args=("923001110011", "slow customer", lambda p, x: None))
    t.start()
    assert slow.entered.wait(5)
    start = time.time()
    inbound.process_incoming("923001110012", "other customer", lambda p, x: fast_sent.append(x))
    assert fast_sent == ["quick"] and time.time() - start < 2
    hold.set()
    t.join(5)


def test_a_silent_reply_sends_nothing(backend, monkeypatch):
    sent = []
    monkeypatch.setattr(inbound.agent, "respond", lambda *a, **k: type("R", (), {"text": "", "silent": True})())
    inbound.process_incoming("923001110004", "hello?", lambda p, t: sent.append(t))
    assert sent == []


def test_an_unexpected_failure_still_answers_and_escalates(backend, monkeypatch):
    sent = []

    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(inbound.agent, "respond", boom)
    inbound.process_incoming("923001110005", "order please", lambda p, t: sent.append(t))
    assert sent and "team" in sent[0].lower()
    with database.session_scope() as s:
        assert s.query(m.Escalation).filter(m.Escalation.phone == "923001110005").one().reason == "assistant_error"
