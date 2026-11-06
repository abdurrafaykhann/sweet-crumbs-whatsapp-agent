"""Per-customer message queue. Messages that arrive while the assistant is still answering the same customer wait in a queue; when
the current answer is done they are handled together as ONE request (one model call instead of one per message), in order, never
in parallel. Works on Redis (several app instances) or in memory (one process)."""
from __future__ import annotations

import logging

from . import agent, kv
from . import models as m
from .database import session_scope

log = logging.getLogger("sweetcrumbs.inbound")
LOCK_TTL = 300


def _key(prefix: str, phone: str) -> str:
    return f"wa:{prefix}:{phone}"


def process_incoming(phone: str, text: str, send, now=None) -> None:
    """Queue the message, then answer everything waiting for this customer unless another worker is already doing it.
    `send(phone, text)` delivers a reply (WhatsApp)."""
    with session_scope() as s:
        s.add(m.Message(phone=phone, role="customer", text=text, created_at=now or agent.clock.now()))  # log each raw message
    kv.queue_push(_key("q", phone), text)
    drain(phone, send, now=now)


def process_media(phone: str, kind: str, media_id: str, caption: str, send, now=None) -> None:
    """Images, documents, voice notes and other non-text messages: handled without the LLM (see app/handoff.py)."""
    from . import handoff
    with kv.customer_lock(phone, timeout=60):  # proceed even if the lock times out: logging the message matters more
        text = handoff.handle_media(phone, kind, media_id, caption, now)
    if text:
        send(phone, text)


def drain(phone: str, send, now=None) -> None:
    qkey, lkey = _key("q", phone), _key("lock", phone)
    while True:
        token = kv.try_lock(lkey, LOCK_TTL)
        if token is None:
            return  # another worker is answering this customer: it will pick our message up when it is done
        try:
            while True:
                batch = kv.queue_pop_all(qkey)
                if not batch:
                    break
                text = "\n".join(batch)
                try:
                    reply = agent.respond(phone, text, now=now, notify=lambda t: send(phone, t), log_customer=False)
                    if reply.text and not reply.silent:
                        send(phone, reply.text)
                except Exception:  # respond() already catches model and tool failures; this is the last line of defence
                    log.exception("could not answer %s", phone)
                    agent.escalate_system(phone, "assistant_error", f"Unexpected failure while answering: {text[:300]}")
                    send(phone, agent.GIVE_UP_TEXT[agent.customer_language(phone)])
        finally:
            kv.unlock(lkey, token)
        if kv.queue_len(qkey) == 0:  # a message may have arrived between the last pop and the unlock
            return
