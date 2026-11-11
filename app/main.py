"""FastAPI app: /health, the WhatsApp Cloud API webhook, and development tools (web chat page, /chat).

The webhook verifies Meta's signature, answers Meta immediately, ignores redelivered message ids, rate-limits, queues messages per
customer, and sends replies from a background task. With ENV=production the signature secret is mandatory and the development
endpoints are switched off (unless ENABLE_DEV_CHAT=1)."""
import logging
import os
import pathlib
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import admin, agent, classifier, devtools, handoff, inbound, kv, whatsapp
from . import models as m
from .database import get_engine, session_scope

log = logging.getLogger("sweetcrumbs")
STATIC = pathlib.Path(__file__).parent / "static"
DASHBOARD = pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "dist"   # built by `npm run build` in dashboard/

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


_ready = False
_ready_lock = threading.Lock()


def prepare_database() -> None:
    """Create the tables and, with SEED_ON_START=1, load the demo data when the database is empty."""
    global _ready
    m.Base.metadata.create_all(get_engine())
    if os.environ.get("SEED_ON_START") == "1":  # one command seeds the demo data and starts everything (docker compose)
        from .seed import seed_if_empty
        seed_if_empty()
    _ready = True


def ensure_ready() -> None:
    """Serverless hosts may not run the startup hook: the first request prepares the database instead (once per instance)."""
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            prepare_database()


def inline_replies() -> bool:
    """INLINE_REPLIES=1 (Vercel): answer inside the webhook request, because a serverless function stops once it has responded."""
    return os.environ.get("INLINE_REPLIES") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    prepare_database()
    if production() and not WA_APP_SECRET:
        log.error("ENV=production but WA_APP_SECRET is not set: the webhook will refuse every call")
    yield


app = FastAPI(title="Sweet Crumbs WhatsApp agent", lifespan=lifespan)
app.include_router(admin.router)


@app.middleware("http")
async def _prepare_on_first_request(request: Request, call_next):
    if not _ready:
        await run_in_threadpool(ensure_ready)
    return await call_next(request)


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
        if classifier.enabled():  # same optional tagging step as the WhatsApp path (inbound.process_incoming)
            with session_scope() as s:
                msg = m.Message(phone=c.phone, role="customer", text=c.text, created_at=agent.clock.now())
                s.add(msg)
                s.flush()
                message_id = msg.id
            classifier.handle_inbound(c.phone, c.text, message_id, lambda phone, text: None)
            r = agent.respond(c.phone, c.text, log_customer=False)
        else:
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
    jobs = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):  # delivery/read "statuses" are not messages and are ignored
                mid, sender, kind = msg.get("id", ""), msg.get("from", ""), msg.get("type", "")
                if not sender or kind in whatsapp.IGNORED_TYPES or not kv.first_time(mid):
                    continue  # nothing to answer, or a redelivery of a message we already handled
                text = whatsapp.inbound_text(msg)
                if text is None:  # images/documents (payment proof) go to a person; voice notes etc. get a polite "text only"
                    media = msg.get(kind) or {}
                    jobs.append((process_media, (sender, kind, media.get("id", ""), media.get("caption", ""), mid)))
                    continue
                if not kv.allow(sender, RATE_LIMIT):
                    log.warning("rate limited %s", sender)
                    continue
                jobs.append((process, (sender, text, mid)))
    for fn, args in jobs:
        if inline_replies():
            await run_in_threadpool(fn, *args)   # serverless: finish the reply before answering Meta
        else:
            bg.add_task(fn, *args)
    return {"ok": True}


# The owner dashboard (React single-page app, hash routes). Mounted last so it never shadows an API route.
if DASHBOARD.is_dir():
    app.mount("/admin", StaticFiles(directory=DASHBOARD, html=True), name="dashboard")
else:
    @app.get("/admin", include_in_schema=False)
    def dashboard_missing():
        return PlainTextResponse("The dashboard is not built. Run: cd dashboard && npm install && npm run build", status_code=503)
