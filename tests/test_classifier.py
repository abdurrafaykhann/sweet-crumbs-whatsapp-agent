"""Intent + mood tagging from the optional classifier service: tags are stored, angry customers go to staff, failures change nothing."""
import datetime as dt

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app import agent, classifier, database, inbound, kv, main, whatsapp
from app import models as m

from .fakes import ScriptedModel

NOW = dt.datetime(2026, 11, 6, 14, 0)
PHONE = "923001119999"


class FakeResponse:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def json(self):
        return self._d


def good(intent="order_status", mood="neutral", flag=False, mood_conf=0.9):
    return {"intent": intent, "intent_confidence": 0.8, "mood": mood, "mood_confidence": mood_conf, "language": "roman_urdu",
            "flag_for_human": flag, "reason": "", "model": "fake"}


@pytest.fixture()
def svc(db, monkeypatch):
    kv.set_client(None)
    kv.reset_memory()
    monkeypatch.setenv("CLASSIFIER_URL", "http://classifier.test")
    monkeypatch.delenv("CLASSIFIER_API_KEY", raising=False)
    monkeypatch.setenv("ANGRY_AUTOFLAG", "1")
    monkeypatch.setenv("DEMO_NOW", NOW.isoformat())
    monkeypatch.setattr(agent, "_default_graph", agent.build_graph(ScriptedModel(script=[]), InMemorySaver()))
    calls = {"posts": []}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["posts"].append((url, json, headers, timeout))
        r = calls.get("reply", good())
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, FakeResponse) else FakeResponse(r)

    monkeypatch.setattr(classifier.httpx, "post", fake_post)
    def fake_respond(phone, text, **kw):  # like the real one: silent while staff have the chat
        with database.session_scope() as s:
            conv = s.get(m.Conversation, phone)
            silent = bool(conv and conv.escalated)
        return type("R", (), {"text": "" if silent else "ok", "silent": silent})()

    monkeypatch.setattr(agent, "respond", fake_respond)
    return calls


def receive(text, sent=None):
    sent = [] if sent is None else sent
    inbound.process_incoming(PHONE, text, lambda p, t: sent.append(t), now=NOW)
    return sent


def tags():
    with database.session_scope() as s:
        return [(t.intent, t.mood, t.flagged) for t in s.query(m.MessageTag).filter(m.MessageTag.phone == PHONE).order_by(m.MessageTag.id)]


def escalations():
    with database.session_scope() as s:
        return s.query(m.Escalation).filter(m.Escalation.phone == PHONE).all()


def test_disabled_without_url_does_nothing(svc, monkeypatch):
    monkeypatch.delenv("CLASSIFIER_URL")
    receive("mera order kahan hai")
    assert svc["posts"] == [] and tags() == []


def test_neutral_message_is_tagged_and_answered_normally(svc):
    sent = receive("mera order kahan hai")
    assert tags() == [("order_status", "neutral", False)] and escalations() == [] and sent == ["ok"]
    url, body, headers, timeout = svc["posts"][0]
    assert url == "http://classifier.test/classify" and body == {"text": "mera order kahan hai"} and timeout == 2.0 and headers == {}


def test_api_key_and_timeout_come_from_env(svc, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_API_KEY", "k1")
    monkeypatch.setenv("CLASSIFIER_TIMEOUT", "0.5")
    receive("hi")
    _, _, headers, timeout = svc["posts"][0]
    assert headers == {"x-api-key": "k1"} and timeout == 0.5


def test_angry_message_opens_high_priority_escalation_and_apologises(svc):
    svc["reply"] = good("complaint", "angry", flag=True, mood_conf=0.95)
    sent = receive("bakwas service!!! 3 ghante ho gaye")
    assert tags() == [("complaint", "angry", True)]
    esc = escalations()
    assert len(esc) == 1 and esc[0].reason == "angry_customer" and esc[0].priority == "high" and esc[0].status == "open"
    with database.session_scope() as s:
        assert s.get(m.Conversation, PHONE).escalated is True
    assert len(sent) == 1 and "team" in sent[0].lower()  # one apology, and the bot does not answer on top of the hand-off


def test_second_angry_message_does_not_duplicate_the_escalation(svc):
    svc["reply"] = good("complaint", "angry", flag=True)
    receive("bakwas service!!!")
    sent = receive("jawab do!!!")
    assert len(escalations()) == 1 and tags()[-1][2] is False and sent == []


def test_kill_switch_shows_tags_but_never_escalates(svc, monkeypatch):
    monkeypatch.setenv("ANGRY_AUTOFLAG", "0")
    svc["reply"] = good("complaint", "angry", flag=True)
    sent = receive("bakwas service!!!")
    assert tags() == [("complaint", "angry", False)] and escalations() == [] and sent == ["ok"]


@pytest.mark.parametrize("reply", [httpx.ConnectTimeout("slow"), FakeResponse({}, 500), FakeResponse({"intent": "pizza", "mood": "angry"}),
                                   FakeResponse({"intent": "order", "mood": "sad"}), FakeResponse(["not", "a", "dict"])])
def test_service_problems_change_nothing(svc, reply):
    svc["reply"] = reply
    sent = receive("mera order kahan hai")
    assert tags() == [] and escalations() == [] and sent == ["ok"]  # the customer is answered exactly as before


def test_unexpected_error_while_tagging_never_breaks_the_chat(svc, monkeypatch):
    monkeypatch.setattr(classifier, "classify", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    assert receive("hi") == ["ok"]


def test_urdu_script_message_is_tagged(svc):
    svc["reply"] = good("order_status", "neutral")
    receive("میرا آرڈر کہاں ہے؟")
    assert tags() == [("order_status", "neutral", False)]
    assert svc["posts"][0][1]["text"] == "میرا آرڈر کہاں ہے؟"


def test_long_message_is_truncated_before_sending(svc):
    receive("x" * 5000)
    assert len(svc["posts"][0][1]["text"]) == 1000


# ---------------------------------------------------------------- dashboard API
@pytest.fixture()
def api(svc, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "owner-pass")
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    monkeypatch.setattr(whatsapp, "send_text", lambda to, text: True)
    with TestClient(main.app) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/api/admin/login", json={"password": "owner-pass"}).json()["token"]
        yield c


def test_admin_lists_show_tags(api, svc):
    svc["reply"] = good("complaint", "angry", flag=True, mood_conf=0.97)
    receive("bakwas service!!!")
    row = next(r for r in api.get("/api/admin/conversations").json() if r["phone"] == PHONE)
    assert row["tag"] == {"intent": "complaint", "mood": "angry", "mood_conf": 0.97, "flagged": True}
    assert row["open_escalation"]["reason"] == "angry_customer" and row["open_escalation"]["priority"] == "high"
    detail = api.get(f"/api/admin/conversations/{PHONE}").json()
    cust = [x for x in detail["messages"] if x["role"] == "customer"][0]
    assert cust["tag"]["mood"] == "angry"
    assert any(x["reason"] == "angry_customer" for x in api.get("/api/admin/escalations").json())


def test_admin_without_tags_returns_null_not_missing(api, monkeypatch):
    monkeypatch.delenv("CLASSIFIER_URL")
    receive("hello")
    row = next(r for r in api.get("/api/admin/conversations").json() if r["phone"] == PHONE)
    assert row["tag"] is None
