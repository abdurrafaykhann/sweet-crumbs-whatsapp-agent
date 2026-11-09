"""Optional intent + mood tagging of inbound customer messages, using the Roman Urdu classifier service
(https://github.com/<you>/roman-urdu-classifier, endpoint POST /classify).

Off unless CLASSIFIER_URL is set. If the service is slow, down or returns garbage the bot behaves exactly as before: tagging
never blocks or breaks a customer conversation.

    CLASSIFIER_URL=http://localhost:8100      service base URL (empty = feature off)
    CLASSIFIER_API_KEY=                       sent as x-api-key when set
    CLASSIFIER_TIMEOUT=2.0                    seconds to wait for the service
    ANGRY_AUTOFLAG=1                          1 = open a high-priority escalation (bot goes silent) for confident `angry` messages,
                                              0 = only show the tags in the dashboard (kill switch)
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import httpx

from . import models as m
from .database import session_scope

log = logging.getLogger("sweetcrumbs.classifier")

INTENTS = {"order", "price_or_menu", "delivery_or_area", "order_status", "complaint", "custom_cake", "payment", "greeting_or_thanks", "other"}
MOODS = {"happy", "neutral", "unhappy", "angry"}

ANGRY_ACK = {
    "en": "I'm sorry about this. I've asked our team to look at your chat right now, and a person will reply here shortly.",
    "rur": "Maazrat, is pareshani ke liye. Maine hamari team ko aap ki chat abhi dikhane ko keh diya hai, koi insaan jald yahan jawab dega.",
    "ur": "اس پریشانی کے لیے معذرت۔ میں نے ٹیم کو آپ کی چیٹ دیکھنے کو کہہ دیا ہے، کوئی صاحب جلد یہاں جواب دیں گے۔",
}


def enabled() -> bool:
    return bool(os.environ.get("CLASSIFIER_URL", "").strip())


def autoflag_enabled() -> bool:
    return os.environ.get("ANGRY_AUTOFLAG", "1").strip() not in ("0", "false", "no", "")


def classify(text: str) -> dict | None:
    """Call the service. Returns a validated dict or None on any problem."""
    if not enabled() or not text.strip():
        return None
    headers = {"x-api-key": os.environ["CLASSIFIER_API_KEY"]} if os.environ.get("CLASSIFIER_API_KEY") else {}
    try:
        r = httpx.post(os.environ["CLASSIFIER_URL"].rstrip("/") + "/classify", json={"text": text[:1000]}, headers=headers,
                       timeout=float(os.environ.get("CLASSIFIER_TIMEOUT", "2.0")))
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # network, timeout, bad status, bad JSON: all the same to the caller
        log.warning("classifier unavailable: %s", e)
        return None
    if d.get("intent") not in INTENTS or d.get("mood") not in MOODS:
        log.warning("classifier returned unknown labels: %s", {k: d.get(k) for k in ("intent", "mood")})
        return None
    return d


def handle_inbound(phone: str, text: str, message_id: int, send, now: dt.datetime | None = None) -> dict | None:
    """Tag one logged customer message; when the mood is confidently angry hand the chat to staff. Never raises."""
    try:
        d = classify(text)
        if d is None:
            return None
        flag = bool(d.get("flag_for_human")) and autoflag_enabled()
        with session_scope() as s:
            conv = s.get(m.Conversation, phone)
            already = bool(conv and conv.escalated)
            s.add(m.MessageTag(message_id=message_id, phone=phone, intent=d["intent"], mood=d["mood"],
                               intent_conf=float(d.get("intent_confidence") or 0), mood_conf=float(d.get("mood_confidence") or 0),
                               language=str(d.get("language") or ""), flagged=flag and not already, model=str(d.get("model") or "")[:40]))
        if flag and not already:
            from . import agent  # local import: agent imports a lot, and this module must stay cheap to load

            agent.escalate_system(phone, "angry_customer", f"Classifier: intent={d['intent']}, mood=angry (p={d.get('mood_confidence')}). "
                                  f"Message: {text[:200]}", "high", now)
            send(phone, ANGRY_ACK[agent.customer_language(phone)])
        return d
    except Exception:
        log.exception("tagging failed for %s", phone)
        return None


def tag_for_dashboard(t: m.MessageTag | None) -> dict | None:
    return None if t is None else {"intent": t.intent, "mood": t.mood, "mood_conf": round(t.mood_conf, 2), "flagged": t.flagged}
