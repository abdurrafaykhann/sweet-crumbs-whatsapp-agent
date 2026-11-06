"""M5: webhook message types, production mode, development endpoints and the chat page."""
import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import database, kv, main, tools
from app import models as m


@pytest.fixture()
def api(db, monkeypatch):
    kv.set_client(fakeredis.FakeRedis(decode_responses=True))
    sent, asked = [], []

    class Reply:
        def __init__(self, text):
            self.text, self.tools, self.escalated, self.silent, self.provider = text, [], False, False, "groq"

    monkeypatch.setattr(main.agent, "respond", lambda phone, text, **kw: (asked.append((phone, text)), Reply(f"echo: {text}"))[1])
    monkeypatch.setattr(main, "send_whatsapp", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(main, "mark_read", lambda mid: None)
    monkeypatch.setattr(main, "WA_APP_SECRET", "")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENABLE_DEV_CHAT", raising=False)
    with TestClient(main.app) as client:
        yield client, sent, asked


def wrap(msg):
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_quick_reply_buttons_and_list_choices_are_answered_as_text(api):
    client, sent, asked = api
    client.post("/webhook", json=wrap({"id": "w1", "from": "923001110001", "type": "button", "button": {"text": "Track my order", "payload": "TRACK"}}))
    client.post("/webhook", json=wrap({"id": "w2", "from": "923001110001", "type": "interactive",
                                       "interactive": {"type": "list_reply", "list_reply": {"id": "x", "title": "2 lb Pineapple"}}}))
    client.post("/webhook", json=wrap({"id": "w3", "from": "923001110001", "type": "interactive",
                                       "interactive": {"type": "button_reply", "button_reply": {"id": "y", "title": "Yes, confirm"}}}))
    assert [t for _, t in asked] == ["Track my order", "2 lb Pineapple", "Yes, confirm"]


def test_reactions_and_status_updates_are_ignored(api):
    client, sent, asked = api
    client.post("/webhook", json=wrap({"id": "w4", "from": "923001110002", "type": "reaction", "reaction": {"emoji": "x", "message_id": "m"}}))
    client.post("/webhook", json={"entry": [{"changes": [{"value": {"statuses": [{"id": "s", "status": "delivered"}]}}]}]})
    assert sent == [] and asked == []


def test_production_refuses_unsigned_webhooks_and_hides_dev_tools(api, monkeypatch):
    client, sent, _ = api
    monkeypatch.setenv("ENV", "production")
    assert client.post("/webhook", json=wrap({"id": "w5", "from": "923001110003", "type": "text", "text": {"body": "hi"}})).status_code == 503
    assert client.get("/health").json()["env"] == "production"
    for path in ("/ui", "/api/dev/customers", "/api/dev/state?phone=1", "/api/dev/messages?phone=1"):
        assert client.get(path).status_code == 404, path
    assert client.post("/chat", json={"phone": "1", "text": "hi"}).status_code == 404
    assert client.post("/api/dev/reseed").status_code == 404
    monkeypatch.setenv("ENABLE_DEV_CHAT", "1")                                  # explicit opt-in
    assert client.get("/api/dev/customers").status_code == 200
    monkeypatch.setattr(main, "WA_APP_SECRET", "s3cret")                       # with a secret the webhook works again (signature checked)
    body = json.dumps(wrap({"id": "w6", "from": "923001110003", "type": "text", "text": {"body": "hi"}})).encode()
    import hashlib, hmac
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": sig}).status_code == 200 and len(sent) == 1


def test_the_chat_page_and_dev_endpoints(api):
    client, _, _ = api
    page = client.get("/ui")
    assert page.status_code == 200 and "Sweet Crumbs" in page.text
    cs = client.get("/api/dev/customers").json()
    assert len(cs) == 8 and {c["phone"] for c in cs} >= {"923000000101", "923000000108"}
    assert set(next(c for c in cs if c["phone"] == "923000000101")["orders"]) == {"SC-10421", "SC-10431"}


def test_dev_media_reset_state_and_messages(api):
    client, _, _ = api
    r = client.post("/api/dev/media", json={"phone": "923000000105", "kind": "image"}).json()
    assert "verify" in r["reply"].lower()
    with database.session_scope() as s:
        assert s.query(m.Escalation).filter(m.Escalation.phone == "923000000105").count() == 1
    tools.draft_order(tools.Ctx("923000000101", 1, __import__("datetime").datetime(2026, 11, 6, 14)), items=[{"sku": "DS-BROW", "qty": 2}])
    st = client.get("/api/dev/state", params={"phone": "923000000101"}).json()
    assert st["draft"]["lines"][0]["name"] == "Fudge Brownie" and st["bot_active"] is True and {o["code"] for o in st["orders"]} == {"SC-10421", "SC-10431"}
    assert client.get("/api/dev/state", params={"phone": "923000000105"}).json()["bot_active"] is False
    assert client.get("/api/dev/messages", params={"phone": "923000000105"}).json()[0]["attachment"]["kind"] == "image"
    assert client.post("/api/dev/reset", json={"phone": "923000000105"}).json() == {"ok": True}
    assert client.get("/api/dev/messages", params={"phone": "923000000105"}).json() == []
    assert client.get("/api/dev/state", params={"phone": "923000000105"}).json()["bot_active"] is True


def test_reseed_restores_the_spec_data(api):
    client, _, _ = api
    with database.session_scope() as s:
        s.query(m.Variant).update({m.Variant.stock: 0})
    report = client.post("/api/dev/reseed").json()
    assert report["orders"] == 9
    with database.session_scope() as s:
        v = s.query(m.Variant).join(m.Product).filter(m.Product.sku == "CK-CHOC", m.Variant.label == "2 lb").one()
        assert v.stock == 4


def test_one_command_start_prepares_the_database(seeded):
    from app import run
    assert run.prepare() is False       # data already there: kept
