"""Development helpers behind the web chat page and the terminal chat: reset one chat, re-seed everything, inspect a chat."""
from __future__ import annotations

from . import clock, pricing
from . import models as m
from .database import session_scope
from .tools import load_state


def _drop_thread(phone: str) -> None:
    """Forget the conversation memory (LangGraph thread) of a phone number."""
    try:
        from . import agent
        saver = agent.default_graph().checkpointer
        if saver is not None:
            saver.delete_thread(phone)
    except Exception:
        pass  # no model configured or nothing stored: nothing to forget


def reset_conversation(phone: str) -> None:
    """Delete the draft, conversation state, messages and escalations of one phone number (orders are kept)."""
    with session_scope() as s:
        for model in (m.Draft, m.Escalation, m.Message):
            s.query(model).filter(model.phone == phone).delete()
        s.query(m.Conversation).filter(m.Conversation.phone == phone).delete()
    _drop_thread(phone)


def reseed() -> dict:
    """Back to the spec's demo data: drops everything, loads it again, and forgets all conversation memory."""
    from . import seed
    with session_scope() as s:
        phones = [c.phone for c in s.query(m.Conversation).all()]
    report = seed.seed_database()
    for p in phones:
        _drop_thread(p)
    return report


def customers() -> list[dict]:
    with session_scope() as s:
        return [{"phone": c.phone, "name": c.name, "language": c.language, "points": c.points,
                 "orders": [o.code for o in c.orders]} for c in s.query(m.Customer).order_by(m.Customer.phone)]


def chat_state(phone: str, now=None) -> dict:
    """What the bot knows right now about one phone number: order in progress, flags, orders."""
    now = now or clock.now()
    with session_scope() as s:
        st = load_state(s, phone)
        conv = s.get(m.Conversation, phone)
        draft = None
        if st.get("items"):
            v = pricing.evaluate(s, phone, st, now)
            draft = {k: v[k] for k in ("status", "lines", "subtotal", "delivery_fee", "discount", "total", "fulfilment", "address", "branch", "slot",
                                       "payment_method", "customer_name", "missing", "problems") if v.get(k) not in (None, [], "")}
        orders = (s.query(m.Order).join(m.Customer).filter(m.Customer.phone == phone).order_by(m.Order.created_at.desc()).limit(6).all())
        return {"phone": phone, "now": now.isoformat(timespec="minutes"), "turn": conv.turn if conv else 0, "bot_active": not (conv and conv.escalated),
                "draft": draft, "orders": [{"code": o.code, "status": o.status, "total": o.total} for o in orders]}


def messages(phone: str, limit: int = 60) -> list[dict]:
    with session_scope() as s:
        rows = s.query(m.Message).filter(m.Message.phone == phone).order_by(m.Message.id.desc()).limit(limit).all()
        return [{"id": x.id, "role": x.role, "text": x.text, "tools": [t.get("name") for t in (x.tools or [])], "attachment": x.attachment or None,
                 "provider": x.provider} for x in reversed(rows)]
