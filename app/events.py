"""Live updates for the dashboard: new orders, new escalations and customer messages since a watermark.
Used by the polling endpoint (GET /api/admin/updates) and by server-sent events (GET /api/admin/events)."""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models as m
from .database import session_scope


def watermarks(s: Session) -> dict:
    return {"order": s.query(func.max(m.Order.id)).scalar() or 0, "escalation": s.query(func.max(m.Escalation.id)).scalar() or 0,
            "message": s.query(func.max(m.Message.id)).scalar() or 0}


def new_since(s: Session, w: dict) -> dict:
    """Everything newer than the watermarks, plus the advanced watermarks."""
    orders = s.query(m.Order).filter(m.Order.id > w.get("order", 0)).order_by(m.Order.id).all()
    escs = s.query(m.Escalation).filter(m.Escalation.id > w.get("escalation", 0)).order_by(m.Escalation.id).all()
    msgs = s.query(m.Message).filter(m.Message.id > w.get("message", 0), m.Message.role == "customer").order_by(m.Message.id).all()
    all_msgs_max = s.query(func.max(m.Message.id)).scalar() or 0
    return {
        "orders": [{"code": o.code, "total": o.total, "status": o.status, "customer": o.customer.name or o.customer.phone, "fulfilment": o.fulfilment} for o in orders],
        "escalations": [{"id": e.id, "phone": e.phone, "reason": e.reason, "priority": e.priority} for e in escs],
        "messages": [{"phone": x.phone, "text": x.text[:120]} for x in msgs],
        "watermarks": {"order": max([w.get("order", 0)] + [o.id for o in orders]), "escalation": max([w.get("escalation", 0)] + [e.id for e in escs]),
                       "message": max(w.get("message", 0), all_msgs_max)},
    }


def poll(w: dict) -> dict:
    with session_scope() as s:
        return new_since(s, w)


async def stream(w: dict, interval: float = 2.0, max_loops: int | None = None):
    """Server-sent events: one `order`, `escalation` or `message` event per new row, a comment as keep-alive."""
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        update = await asyncio.get_running_loop().run_in_executor(None, poll, w)
        w = update["watermarks"]
        sent = False
        for kind, key in (("order", "orders"), ("escalation", "escalations"), ("message", "messages")):
            for item in update[key]:
                sent = True
                yield f"event: {kind}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
        if not sent:
            yield ": keep-alive\n\n"
        await asyncio.sleep(interval)
