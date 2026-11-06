"""WhatsApp Cloud API helpers: sending text, read receipts, signature check, and reading inbound messages.
Configuration is read from the environment at call time (WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, WA_GRAPH_URL)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

import httpx

log = logging.getLogger("sweetcrumbs.whatsapp")

IGNORED_TYPES = {"reaction"}  # an emoji reaction to one of our messages needs no answer


def _cfg():
    return (os.environ.get("WA_ACCESS_TOKEN", ""), os.environ.get("WA_PHONE_NUMBER_ID", ""),
            os.environ.get("WA_GRAPH_URL", "https://graph.facebook.com/v21.0"))


def configured() -> bool:
    token, phone_id, _ = _cfg()
    return bool(token and phone_id)


def send_text(to: str, text: str) -> bool:
    """Send a WhatsApp text. Returns False (and logs) if it could not be sent; never raises."""
    token, phone_id, graph = _cfg()
    if not (token and phone_id):
        log.info("WhatsApp send skipped (no credentials): to=%s", to)
        return False
    try:
        r = httpx.post(f"{graph}/{phone_id}/messages", headers={"Authorization": f"Bearer {token}"}, timeout=20,
                       json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text, "preview_url": False}})
        if r.status_code >= 300:
            log.error("WhatsApp send failed %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception:
        log.exception("WhatsApp send error")
        return False


def mark_read(message_id: str) -> None:
    token, phone_id, graph = _cfg()
    if not (token and phone_id and message_id):
        return
    try:
        httpx.post(f"{graph}/{phone_id}/messages", headers={"Authorization": f"Bearer {token}"}, timeout=10,
                   json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except Exception:
        pass


def signature_ok(raw: bytes, header: str, secret: str) -> bool:
    """Meta signs every webhook call with the app secret (X-Hub-Signature-256). No secret = development: accept."""
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def inbound_text(msg: dict) -> str | None:
    """The text of an inbound message if it is text-like: plain text, a quick-reply button or an interactive list/button reply."""
    kind = msg.get("type")
    if kind == "text":
        return (msg.get("text") or {}).get("body") or ""
    if kind == "button":
        b = msg.get("button") or {}
        return b.get("text") or b.get("payload") or ""
    if kind == "interactive":
        i = msg.get("interactive") or {}
        r = i.get("button_reply") or i.get("list_reply") or {}
        return r.get("title") or ""
    return None
