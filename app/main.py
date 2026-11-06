"""FastAPI app: /health, /chat (development), and the WhatsApp Cloud API webhook.
The webhook verifies Meta's signature, answers Meta immediately, ignores redelivered message ids, rate-limits and serialises
messages per customer, and sends the reply from a background task."""
import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import agent, kv
from . import models as m
from .database import get_engine

log = logging.getLogger("sweetcrumbs")

VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "dev-verify-token")
WA_TOKEN = os.environ.get("WA_ACCESS_TOKEN", "")
WA_PHONE_ID = os.environ.get("WA_PHONE_NUMBER_ID", "")
WA_APP_SECRET = os.environ.get("WA_APP_SECRET", "")
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "20"))
GRAPH = os.environ.get("WA_GRAPH_URL", "https://graph.facebook.com/v21.0")
NON_TEXT = "Sorry, I can only read text right now. Could you type your message?"
BUSY = "One moment please, I'm still replying to your previous message."


@asynccontextmanager
async def lifespan(app: FastAPI):
    m.Base.metadata.create_all(get_engine())
    if os.environ.get("SEED_ON_START") == "1":  # one command seeds the demo data and starts everything (docker compose)
        from .seed import seed_if_empty
        seed_if_empty()
    yield


app = FastAPI(title="Sweet Crumbs WhatsApp agent", lifespan=lifespan)


def answer(phone: str, text: str) -> str:
    """One customer message in, one reply out, serialised per customer."""
    with kv.customer_lock(phone) as got:
        if not got:
            return BUSY
        return agent.respond(phone, text).text


class Chat(BaseModel):
    phone: str = "923000000108"
    text: str


@app.get("/health")
def health():
    return {"ok": True, "db": get_engine().dialect.name, "kv": kv.backend(), "model": os.environ.get("LLM_PRIMARY", "groq:openai/gpt-oss-120b")}


@app.post("/chat")
def chat(c: Chat):
    if not kv.allow(c.phone, RATE_LIMIT):
        raise HTTPException(429, "Too many messages, slow down a little.")
    r = agent.respond(c.phone, c.text)
    return {"reply": r.text, "tools": [t["name"] for t in r.tools], "escalated": r.escalated}


@app.get("/webhook")
def verify(mode: str = Query(None, alias="hub.mode"), token: str = Query(None, alias="hub.verify_token"),
           challenge: str = Query(None, alias="hub.challenge")):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("forbidden", status_code=403)


def _signature_ok(raw: bytes, header: str) -> bool:
    if not WA_APP_SECRET:
        return True  # development only: always set WA_APP_SECRET in production
    expected = "sha256=" + hmac.new(WA_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def send_whatsapp(to: str, text: str):
    if not (WA_TOKEN and WA_PHONE_ID):
        log.info("WhatsApp send skipped (no credentials): to=%s", to)
        return
    try:
        r = httpx.post(f"{GRAPH}/{WA_PHONE_ID}/messages", headers={"Authorization": f"Bearer {WA_TOKEN}"}, timeout=20,
                       json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text, "preview_url": False}})
        if r.status_code >= 300:
            log.error("WhatsApp send failed %s: %s", r.status_code, r.text[:300])
    except Exception:
        log.exception("WhatsApp send error")


def mark_read(message_id: str):
    if not (WA_TOKEN and WA_PHONE_ID and message_id):
        return
    try:
        httpx.post(f"{GRAPH}/{WA_PHONE_ID}/messages", headers={"Authorization": f"Bearer {WA_TOKEN}"}, timeout=10,
                   json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except Exception:
        pass


def process(sender: str, text: str, message_id: str):
    mark_read(message_id)
    try:
        reply = answer(sender, text)
    except Exception:
        log.exception("agent error")  # M3 adds the provider fallback and the human escalation for this case
        reply = "Sorry, something went wrong on our side. A team member will reply shortly."
    if reply:
        send_whatsapp(sender, reply)


@app.post("/webhook")
async def webhook(req: Request, bg: BackgroundTasks):
    raw = await req.body()
    if not _signature_ok(raw, req.headers.get("x-hub-signature-256", "")):
        raise HTTPException(403, "bad signature")
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(400, "bad json")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):  # delivery/read "statuses" are ignored
                mid, sender = msg.get("id", ""), msg.get("from", "")
                if not sender or not kv.first_time(mid):
                    continue  # redelivery of a message we already handled
                if msg.get("type") != "text":
                    bg.add_task(send_whatsapp, sender, NON_TEXT)  # payment screenshots go to a human in M4
                    continue
                if not kv.allow(sender, RATE_LIMIT):
                    log.warning("rate limited %s", sender)
                    continue
                bg.add_task(process, sender, msg["text"]["body"], mid)
    return {"ok": True}
