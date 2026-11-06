"""FastAPI app: /health, the WhatsApp Cloud API webhook, and development tools (web chat page, /chat).

The webhook verifies Meta's signature, answers Meta immediately, ignores redelivered message ids, rate-limits, queues messages per
customer, and sends replies from a background task. With ENV=production the signature secret is mandatory and the development
endpoints are switched off (unless ENABLE_DEV_CHAT=1)."""
import logging
import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import admin, agent, devtools, handoff, inbound, kv, whatsapp
from . import models as m
from .database import get_engine

log = logging.getLogger("sweetcrumbs")
STATIC = pathlib.Path(__file__).parent / "static"

VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "dev-verify-token")
WA_APP_SECRET = os.environ.get("WA_APP_SECRET", "")
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "20"))


def production() -> bool:
    return os.environ.get("ENV", "development").lower() == "production"


def dev_enabled() -> bool:
    return not production() or os.environ.get("ENABLE_DEV_CHAT") == "1"


def require_dev():
    if not dev_enabled():
        raise HTTPException(404, "not found")


@asynccontextmanager
async def lifespan(app: FastAPI):
    m.Base.metadata.create_all(get_engine())
    if os.environ.get("SEED_ON_START") == "1":  # one command seeds the demo data and starts everything (docker compose)
        from .seed import seed_if_empty
        seed_if_empty()
    if production() and not WA_APP_SECRET:
        log.error("ENV=production but WA_APP_SECRET is not set: the webhook will refuse every call")
    yield


app = FastAPI(title="Sweet Crumbs WhatsApp agent", lifespan=lifespan)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"ok": True, "db": get_engine().dialect.name, "kv": kv.backend(), "model": os.environ.get("LLM_PRIMARY", "groq:openai/gpt-oss-120b"),
            "env": "production" if production() else "development"}


# ----------------------------------------------------------------------------- development tools (off in production)
class Chat(BaseModel):
    phone: str = "923000000108"
    text: str


class PhoneBody(BaseModel):
    phone: str


class MediaBody(BaseModel):
    phone: str
    kind: str = "image"


@app.post("/chat")
def chat(c: Chat):
    require_dev()
    if not kv.allow(c.phone, RATE_LIMIT):
        raise HTTPException(429, "Too many messages, slow down a little.")
    with kv.customer_lock(c.phone, timeout=60) as got:  # one message at a time per customer, same lock as the webhook
        if not got:
            raise HTTPException(503, "still answering the previous message")
        r = agent.respond(c.phone, c.text)
    return {"reply": r.text, "tools": [t["name"] for t in r.tools], "escalated": r.escalated, "silent": r.silent, "provider": r.provider}


@app.get("/ui")
def chat_page():
    require_dev()
    return FileResponse(STATIC / "chat.html")


@app.get("/api/dev/customers")
def dev_customers():
    require_dev()
    return devtools.customers()


@app.get("/api/dev/messages")
def dev_messages(phone: str):
    require_dev()
    return devtools.messages(phone)


@app.get("/api/dev/state")
def dev_state(phone: str):
    require_dev()
    return devtools.chat_state(phone)


@app.post("/api/dev/media")
def dev_media(b: MediaBody):
    require_dev()
    return {"reply": handoff.handle_media(b.phone, b.kind, "dev-upload")}


@app.post("/api/dev/reset")
def dev_reset(b: PhoneBody):
    require_dev()
    devtools.reset_conversation(b.phone)
    return {"ok": True}


@app.post("/api/dev/reseed")
def dev_reseed():
    require_dev()
    return devtools.reseed()


# ----------------------------------------------------------------------------- WhatsApp webhook
@app.get("/webhook")
def verify(mode: str = Query(None, alias="hub.mode"), token: str = Query(None, alias="hub.verify_token"),
           challenge: str = Query(None, alias="hub.challenge")):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("forbidden", status_code=403)


def _signature_ok(raw: bytes, header: str) -> bool:
    return whatsapp.signature_ok(raw, header, WA_APP_SECRET)


def send_whatsapp(to: str, text: str):
    whatsapp.send_text(to, text)


def mark_read(message_id: str):
    whatsapp.mark_read(message_id)


def process_media(sender: str, kind: str, media_id: str, caption: str, message_id: str):
    mark_read(message_id)
    inbound.process_media(sender, kind, media_id, caption, send=send_whatsapp)


def process(sender: str, text: str, message_id: str):
    mark_read(message_id)
    inbound.process_incoming(sender, text, send=send_whatsapp)


@app.post("/webhook")
async def webhook(req: Request, bg: BackgroundTasks):
    if production() and not WA_APP_SECRET:
        raise HTTPException(503, "WA_APP_SECRET must be set in production")  # fail closed: never accept unsigned calls in production
    raw = await req.body()
    if not _signature_ok(raw, req.headers.get("x-hub-signature-256", "")):
        raise HTTPException(403, "bad signature")
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(400, "bad json")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):  # delivery/read "statuses" are not messages and are ignored
                mid, sender, kind = msg.get("id", ""), msg.get("from", ""), msg.get("type", "")
                if not sender or kind in whatsapp.IGNORED_TYPES or not kv.first_time(mid):
                    continue  # nothing to answer, or a redelivery of a message we already handled
                text = whatsapp.inbound_text(msg)
                if text is None:  # images/documents (payment proof) go to a person; voice notes etc. get a polite "text only"
                    media = msg.get(kind) or {}
                    bg.add_task(process_media, sender, kind, media.get("id", ""), media.get("caption", ""), mid)
                    continue
                if not kv.allow(sender, RATE_LIMIT):
                    log.warning("rate limited %s", sender)
                    continue
                bg.add_task(process, sender, text, mid)
    return {"ok": True}
