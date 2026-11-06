"""Human handoff. While a chat is escalated the bot stays silent; staff see a short brief of what was already collected, reply from
the dashboard, and hand the chat back. Payment screenshots always go to a person: the bot never says money was received."""
from __future__ import annotations

import datetime as dt

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.orm import Session

from . import clock, pricing
from . import models as m
from .database import session_scope
from .tools import Ctx, _escalate

MEDIA_TEXT = {
    "payment": {"en": "Thank you! I've passed your payment screenshot to our team to verify. You'll get a confirmation here shortly.",
                "rur": "Shukriya! Aap ka payment screenshot hamari team ko verify ke liye bhej diya hai. Confirmation yahan jald mil jayegi.",
                "ur": "شکریہ! آپ کا پیمنٹ اسکرین شاٹ تصدیق کے لیے ہماری ٹیم کو بھیج دیا ہے۔ تصدیق جلد یہیں مل جائے گی۔"},
    "unsupported": {"en": "Sorry, I can only read text right now. Could you type your message?",
                    "rur": "Maazrat, main abhi sirf text parh sakta hoon. Kya aap apna message likh kar bhej sakte hain?",
                    "ur": "معذرت، میں ابھی صرف ٹیکسٹ پڑھ سکتا ہوں۔ براہ کرم اپنا پیغام لکھ کر بھیجیں۔"},
}


def _lang(s: Session, phone: str) -> str:
    c = s.query(m.Customer).filter(m.Customer.phone == phone).one_or_none()
    return c.language if c and c.language in ("en", "rur", "ur") else "en"


def _conversation(s: Session, phone: str) -> m.Conversation:
    conv = s.get(m.Conversation, phone)
    if conv is None:
        conv = m.Conversation(phone=phone, turn=0)
        s.add(conv)
        s.flush()
    return conv


def _open_escalations(s: Session, phone: str) -> list[m.Escalation]:
    return s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.status == "open").order_by(m.Escalation.id).all()


# ----------------------------------------------------------------------------- owner brief
def escalation_brief(s: Session, esc: m.Escalation, now: dt.datetime | None = None) -> dict:
    """What staff need so the customer is not asked again: reason, who, what was collected, the last messages."""
    now = now or clock.now()
    cust = s.query(m.Customer).filter(m.Customer.phone == esc.phone).one_or_none()
    collected = None
    if esc.collected and esc.collected.get("items"):
        view = pricing.evaluate(s, esc.phone, esc.collected, now)
        collected = {"items": [f"{ln['qty']} x {ln['name']}" + (f' (message: "{ln["message"]}")' if ln.get("message") else "") for ln in view["lines"]],
                     "fulfilment": view["fulfilment"], "address": view["address"], "branch": view["branch"], "when": view["slot"],
                     "payment": view["payment_method"], "total": view["total"], "still_missing": view["missing"]}
        collected = {k: v for k, v in collected.items() if v not in (None, [], "")}
    last = s.query(m.Message).filter(m.Message.phone == esc.phone).order_by(m.Message.id.desc()).limit(6).all()
    return {"id": esc.id, "phone": esc.phone, "customer": cust.name if cust else "", "reason": esc.reason, "priority": esc.priority, "status": esc.status,
            "order_code": esc.order_code, "summary": esc.summary, "collected": collected, "created_at": esc.created_at.isoformat(),
            "last_messages": [{"role": x.role, "text": x.text} for x in reversed(last)]}


# ----------------------------------------------------------------------------- take over, reply, hand back
def take_over(phone: str, now: dt.datetime | None = None) -> dict:
    """Staff take the chat: the bot goes silent. An escalation is opened if there is none."""
    now = now or clock.now()
    with session_scope() as s:
        conv = _conversation(s, phone)
        conv.escalated = True
        if not _open_escalations(s, phone):
            _escalate(s, Ctx(phone, conv.turn, now), "staff took over", "Staff took over this chat from the dashboard.", "normal")
        s.add(m.Message(phone=phone, role="note", text="Staff took over this chat", created_at=now))
    return {"phone": phone, "bot_active": False}


def human_reply(phone: str, text: str, send, now: dt.datetime | None = None) -> dict:
    """A staff member answers the customer. Taking over first if they had not yet."""
    now = now or clock.now()
    with session_scope() as s:
        was_active = not s.get(m.Conversation, phone) or not s.get(m.Conversation, phone).escalated
    if was_active:
        take_over(phone, now)
    with session_scope() as s:
        s.add(m.Message(phone=phone, role="human", text=text, created_at=now))
    send(phone, text)
    return {"phone": phone, "sent": text}


def hand_back(phone: str, graph=None, now: dt.datetime | None = None) -> dict:
    """The bot takes the chat again. What was said while it was silent (customer messages and staff replies) is added to its memory so
    it continues from where the conversation really is."""
    now = now or clock.now()
    injected: list = []
    with session_scope() as s:
        conv = _conversation(s, phone)
        conv.escalated = False
        opens = _open_escalations(s, phone)
        from_id = min((e.message_id for e in opens), default=None)
        if from_id is not None:
            for x in s.query(m.Message).filter(m.Message.phone == phone, m.Message.id > from_id, m.Message.role.in_(("customer", "human"))).order_by(m.Message.id):
                injected.append(HumanMessage(x.text) if x.role == "customer" else AIMessage(f"[A staff member replied to the customer] {x.text}"))
        for e in opens:
            e.status, e.resolved_at = "resolved", now
        s.add(m.Message(phone=phone, role="note", text="Handed back to the assistant", created_at=now))
    if injected:
        from . import agent
        (graph or agent.default_graph()).update_state({"configurable": {"thread_id": phone}}, {"messages": injected}, as_node="agent")
    return {"phone": phone, "bot_active": True, "added_to_memory": len(injected)}


def resolve_escalation(esc_id: int, hand_back_to_bot: bool = False, graph=None, now: dt.datetime | None = None) -> dict | None:
    now = now or clock.now()
    with session_scope() as s:
        e = s.get(m.Escalation, esc_id)
        if e is None:
            return None
        phone = e.phone
        if not hand_back_to_bot:
            e.status, e.resolved_at = "resolved", now
    if hand_back_to_bot:  # hand_back() resolves every open escalation of the chat and re-enables the bot
        return hand_back(phone, graph=graph, now=now)
    return {"id": esc_id, "status": "resolved"}


# ----------------------------------------------------------------------------- images, documents, voice notes
def handle_media(phone: str, kind: str, media_id: str = "", caption: str = "", now: dt.datetime | None = None) -> str:
    """A non-text WhatsApp message. Images and documents are treated as payment proof: they go to a person (high priority when an
    advance is waiting) and the bot says only that staff will verify it. Anything else gets a polite 'text only'. Returns the reply ('' = stay silent)."""
    now = now or clock.now()
    with session_scope() as s:
        conv = _conversation(s, phone)
        lang = _lang(s, phone)
        s.add(m.Message(phone=phone, role="customer", text=caption or f"[{kind}]", attachment={"kind": kind, "media_id": media_id}, created_at=now))
        if conv.escalated:  # staff already have this chat: keep the picture for them, say nothing
            opens = _open_escalations(s, phone)
            if opens and kind in ("image", "document"):
                opens[-1].summary = (opens[-1].summary + "\nThe customer sent an image.").strip()
            return ""
        if kind in ("image", "document"):
            waiting = (s.query(m.Order).join(m.Customer).filter(m.Customer.phone == phone, m.Order.status == "awaiting_advance")
                       .order_by(m.Order.created_at.desc()).all())
            summary = ("Customer sent a payment screenshot. "
                       + (f"Order {waiting[0].code} is waiting for the advance of Rs {waiting[0].advance_due:,}." if waiting
                          else "No order is waiting for an advance under this number: please check."))
            _escalate(s, Ctx(phone, conv.turn, now), "payment_screenshot", summary, "high" if waiting else "normal", waiting[0].code if waiting else "")
            conv.escalated = True
            text = MEDIA_TEXT["payment"][lang]
        else:
            text = MEDIA_TEXT["unsupported"][lang]
        s.add(m.Message(phone=phone, role="bot", text=text, created_at=now))
    return text

