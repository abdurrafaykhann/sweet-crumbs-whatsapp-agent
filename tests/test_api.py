"""Webhook behaviour: signature, de-duplication, background reply, rate limit, per-customer serialisation."""
import hashlib
import hmac
import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import kv, main


@pytest.fixture()
def api(seeded, monkeypatch):
    kv.set_client(fakeredis.FakeRedis(decode_responses=True))
    sent, asked = [], []

    class Reply:
        def __init__(self, text):
            self.text, self.tools, self.escalated = text, [{"name": "get_info"}], False

    def fake_respond(phone, text, **kw):
        asked.append((phone, text))
        return Reply(f"echo: {text}")

    monkeypatch.setattr(main.agent, "respond", fake_respond)
    monkeypatch.setattr(main, "send_whatsapp", lambda to, text: sent.append((to, text)))
    monkeypatch.setattr(main, "mark_read", lambda mid: None)
    monkeypatch.setattr(main, "WA_APP_SECRET", "")
    with TestClient(main.app) as client:
        yield client, sent, asked


def payload(mid, text, sender="923001112233", typ="text"):
    msg = {"id": mid, "from": sender, "type": typ}
    if typ == "text":
        msg["text"] = {"body": text}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_health(api):
    client, *_ = api
    h = client.get("/health").json()
    assert h["ok"] and h["db"] in ("sqlite", "postgresql") and h["kv"] == "redis"


def test_chat_endpoint(api):
    client, *_ = api
    r = client.post("/chat", json={"phone": "923000000101", "text": "salam"}).json()
    assert r == {"reply": "echo: salam", "tools": ["get_info"], "escalated": False}


def test_webhook_answers_in_the_background_and_dedupes(api):
    client, sent, asked = api
    p = payload("wamid.1", "chocolate cake kitne ka hai")
    assert client.post("/webhook", json=p).status_code == 200
    assert sent == [("923001112233", "echo: chocolate cake kitne ka hai")]
    client.post("/webhook", json=p)                                    # Meta redelivers the same message id
    assert len(sent) == 1 and len(asked) == 1


def test_signature_is_enforced_when_a_secret_is_set(api, monkeypatch):
    client, sent, _ = api
    monkeypatch.setattr(main, "WA_APP_SECRET", "s3cret")
    body = json.dumps(payload("wamid.2", "hello")).encode()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 403
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": good}).status_code == 200
    assert len(sent) == 1


def test_verification_handshake(api):
    client, *_ = api
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": main.VERIFY_TOKEN, "hub.challenge": "42"})
    assert ok.text == "42"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"}).status_code == 403


def test_non_text_messages_get_a_polite_reply(api):
    client, sent, asked = api
    client.post("/webhook", json=payload("wamid.3", "", typ="audio"))
    assert sent == [("923001112233", main.NON_TEXT)] and asked == []


def test_rate_limit_per_customer(api, monkeypatch):
    client, sent, _ = api
    monkeypatch.setattr(main, "RATE_LIMIT", 2)
    for i in range(4):
        client.post("/webhook", json=payload(f"wamid.r{i}", f"msg {i}", sender="923005550000"))
    assert len(sent) == 2


def test_a_busy_customer_gets_a_polite_message_not_silence(api, monkeypatch):
    client, sent, _ = api

    class NeverFree:
        def __enter__(self):
            return False

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(main.kv, "customer_lock", lambda phone, timeout=120: NeverFree())
    client.post("/webhook", json=payload("wamid.b", "hi"))
    assert sent == [("923001112233", main.BUSY)]
