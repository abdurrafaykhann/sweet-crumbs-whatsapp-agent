"""Owner dashboard API (/api/admin/...). Password login (from .env) returns a JWT; every other route needs it.
Status changes and payment checks message the customer on WhatsApp in their language (app/notify.py)."""
from __future__ import annotations

import datetime as dt
import hmac
import os

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import analytics, clock, devtools, events, handoff, kv, orders, whatsapp
from . import models as m
from .database import session_scope

router = APIRouter(prefix="/api/admin")
TOKEN_HOURS = 12


# ----------------------------------------------------------------------------- auth
def _creds() -> tuple[str, str]:
    """(password, secret); a missing password or a JWT secret shorter than 16 characters switches the dashboard login off."""
    password, secret = os.environ.get("ADMIN_PASSWORD", ""), os.environ.get("JWT_SECRET", "")
    return (password, secret) if len(secret) >= 16 else ("", "")


def admin_required(request: Request, token: str | None = Query(None)) -> str:
    """Bearer token in the Authorization header (or ?token= for server-sent events, which cannot set headers)."""
    password, secret = _creds()
    if not password or not secret:
        raise HTTPException(503, "admin login is disabled: set ADMIN_PASSWORD and a JWT_SECRET of at least 16 characters")
    header = request.headers.get("authorization", "")
    raw = header[7:] if header.lower().startswith("bearer ") else token
    try:
        return jwt.decode(raw or "", secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "login required")


class Login(BaseModel):
    password: str


@router.post("/login")
def login(body: Login, request: Request):
    password, secret = _creds()
    if not password or not secret:
        raise HTTPException(503, "admin login is disabled: set ADMIN_PASSWORD and a JWT_SECRET of at least 16 characters")
    ip = request.client.host if request.client else "?"
    if not kv.allow(f"login:{ip}", limit=10, window=60):
        raise HTTPException(429, "too many attempts")
    if not hmac.compare_digest(body.password.encode(), password.encode()):
        raise HTTPException(401, "wrong password")
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=TOKEN_HOURS)
    return {"token": jwt.encode({"sub": "owner", "exp": exp}, secret, algorithm="HS256"), "expires_in_hours": TOKEN_HOURS}


auth = [Depends(admin_required)]


# ----------------------------------------------------------------------------- messaging the customer
def deliver(phone: str, text: str) -> None:
    """Send a system message (status update, payment check) over WhatsApp and keep it in the chat history."""
    whatsapp.send_text(phone, text)
    with session_scope() as s:
        s.add(m.Message(phone=phone, role="bot", text=text, tools=[{"name": "status_update"}], provider="system", created_at=clock.now()))


def _finish(result: dict) -> dict:
    """Turn a service result into an API response: send the customer notice, map errors to HTTP codes."""
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "order_not_found" else 409, result)
    note = result.pop("notify", None)
    if note:
        deliver(*note)
        result["customer_notified"] = True
    return result


# ----------------------------------------------------------------------------- orders
@router.get("/orders", dependencies=auth)
def list_orders(status: str | None = None, date: dt.date | None = None, branch: str | None = None, fulfilment: str | None = None,
                q: str | None = None, limit: int = 50, offset: int = 0):
    with session_scope() as s:
        return orders.list_orders(s, status=status, date=date, branch=branch, fulfilment=fulfilment, q=q, limit=min(limit, 200), offset=offset)


@router.get("/orders/{code}", dependencies=auth)
def order_detail(code: str):
    with session_scope() as s:
        o = orders.get(s, code)
        if o is None:
            raise HTTPException(404, "order not found")
        return orders.serialize(s, o, detail=True)


class StatusBody(BaseModel):
    status: str
    rider_name: str = ""
    eta: str | None = None


@router.post("/orders/{code}/status", dependencies=auth)
def set_status(code: str, b: StatusBody):
    with session_scope() as s:
        result = orders.change_status(s, code, b.status, rider_name=b.rider_name, eta=b.eta)
    return _finish(result)


class PaymentBody(BaseModel):
    action: str            # confirm | reject
    amount: int | None = None
    note: str = ""


@router.post("/orders/{code}/payment", dependencies=auth)
def check_payment(code: str, b: PaymentBody):
    with session_scope() as s:
        result = orders.verify_advance(s, code, b.action, amount=b.amount, note=b.note)
        phone = orders.get(s, code).customer.phone if result.get("ok") else None
        if phone:  # the person has dealt with the screenshot: close it, and give the chat back to the bot if nothing else is open
            for e in s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.status == "open", m.Escalation.reason == "payment_screenshot"):
                e.status, e.resolved_at = "resolved", clock.now()
            s.flush()
            still_open = s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.status == "open").count()
            conv = s.get(m.Conversation, phone)
            give_back = bool(conv and conv.escalated and not still_open and b.action == "confirm")
    out = _finish(result)
    if phone and give_back:
        handoff.hand_back(phone)
        out["bot_active"] = True
    return out


class EditBody(BaseModel):
    address: str | None = None
    notes: str | None = None
    cake_message: str | None = None
    rider: str | None = None
    slot_start: str | None = None
    eta: str | None = None


@router.patch("/orders/{code}", dependencies=auth)
def edit_order(code: str, b: EditBody):
    with session_scope() as s:
        result = orders.edit(s, code, b.model_dump())
    return _finish(result)


class CancelBody(BaseModel):
    reason: str = "cancelled by staff"


@router.post("/orders/{code}/cancel", dependencies=auth)
def cancel_order(code: str, b: CancelBody):
    with session_scope() as s:
        result = orders.cancel(s, code, b.reason)
    return _finish(result)


# ----------------------------------------------------------------------------- conversations and handoff
@router.get("/conversations", dependencies=auth)
def conversations(q: str | None = None, escalated: bool | None = None):
    with session_scope() as s:
        rows = []
        for (phone,) in s.query(m.Message.phone).distinct():
            last = s.query(m.Message).filter(m.Message.phone == phone).order_by(m.Message.id.desc()).first()
            cust = s.query(m.Customer).filter(m.Customer.phone == phone).one_or_none()
            conv = s.get(m.Conversation, phone)
            esc = s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.status == "open").order_by(m.Escalation.id.desc()).first()
            rows.append({"phone": phone, "name": cust.name if cust else "", "last_text": last.text[:140], "last_role": last.role, "last_id": last.id,
                         "last_at": last.created_at.isoformat(timespec="minutes"), "bot_active": not (conv and conv.escalated),
                         "open_escalation": {"id": esc.id, "reason": esc.reason, "priority": esc.priority} if esc else None})
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in r["phone"] or ql in r["name"].lower() or ql in r["last_text"].lower()]
        if escalated is not None:
            rows = [r for r in rows if bool(r["open_escalation"]) == escalated]
        return sorted(rows, key=lambda r: -r["last_id"])


@router.get("/conversations/{phone}", dependencies=auth)
def conversation(phone: str):
    with session_scope() as s:
        msgs = s.query(m.Message).filter(m.Message.phone == phone).order_by(m.Message.id.desc()).limit(200).all()
        cust = s.query(m.Customer).filter(m.Customer.phone == phone).one_or_none()
        opens = s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.status == "open").all()
        state = devtools.chat_state(phone)
        return {"phone": phone, "customer": {"name": cust.name, "language": cust.language, "points": cust.points} if cust else None,
                "bot_active": state["bot_active"], "draft": state["draft"], "orders": state["orders"],
                "escalations": [handoff.escalation_brief(s, e) for e in opens],
                "messages": [{"id": x.id, "role": x.role, "text": x.text, "tools": x.tools or [], "attachment": x.attachment or None, "provider": x.provider,
                              "latency_ms": x.latency_ms, "at": x.created_at.isoformat(timespec="minutes")} for x in reversed(msgs)]}


@router.post("/conversations/{phone}/take-over", dependencies=auth)
def take_over(phone: str):
    return handoff.take_over(phone)


class ReplyBody(BaseModel):
    text: str


@router.post("/conversations/{phone}/reply", dependencies=auth)
def reply(phone: str, b: ReplyBody):
    if not b.text.strip():
        raise HTTPException(400, "empty message")
    return handoff.human_reply(phone, b.text.strip(), whatsapp.send_text)


@router.post("/conversations/{phone}/hand-back", dependencies=auth)
def hand_back(phone: str):
    return handoff.hand_back(phone)


@router.get("/escalations", dependencies=auth)
def escalations(status: str = "open"):
    with session_scope() as s:
        rows = s.query(m.Escalation).filter(m.Escalation.status == status).order_by(m.Escalation.id.desc()).all()
        return [handoff.escalation_brief(s, e) for e in rows]


class ResolveBody(BaseModel):
    hand_back: bool = False


@router.post("/escalations/{esc_id}/resolve", dependencies=auth)
def resolve(esc_id: int, b: ResolveBody | None = None):
    out = handoff.resolve_escalation(esc_id, hand_back_to_bot=bool(b and b.hand_back))
    if out is None:
        raise HTTPException(404, "escalation not found")
    return out


# ----------------------------------------------------------------------------- menu and stock (the bot reads the same rows)
@router.get("/menu", dependencies=auth)
def menu():
    with session_scope() as s:
        return [{"sku": p.sku, "name": p.name, "category": p.category, "made_to_order": p.made_to_order,
                 "variants": [{"id": v.id, "label": v.label, "price": v.price, "stock": v.stock} for v in p.variants]}
                for p in s.query(m.Product).order_by(m.Product.id)]


class VariantBody(BaseModel):
    price: int | None = None
    stock: int | None = None


@router.patch("/variants/{vid}", dependencies=auth)
def edit_variant(vid: int, b: VariantBody):
    with session_scope() as s:
        v = s.get(m.Variant, vid)
        if v is None:
            raise HTTPException(404, "variant not found")
        if "price" in b.model_fields_set:
            if b.price is None or b.price < 0:
                raise HTTPException(400, "price must be a number of rupees")
            v.price = b.price
        if "stock" in b.model_fields_set:
            if b.stock is not None and b.stock < 0:
                raise HTTPException(400, "stock cannot be negative")
            v.stock = b.stock  # null = unlimited / made to order
        return {"id": v.id, "label": v.label, "price": v.price, "stock": v.stock}


@router.post("/products/{sku}/sold-out", dependencies=auth)
def sold_out(sku: str):
    with session_scope() as s:
        p = s.query(m.Product).filter(m.Product.sku == sku).one_or_none()
        if p is None:
            raise HTTPException(404, "product not found")
        for v in p.variants:
            v.stock = 0
        return {"sku": sku, "sold_out": True}


class RestockBody(BaseModel):
    stock: int | None = None


@router.post("/products/{sku}/back-in-stock", dependencies=auth)
def back_in_stock(sku: str, b: RestockBody):
    with session_scope() as s:
        p = s.query(m.Product).filter(m.Product.sku == sku).one_or_none()
        if p is None:
            raise HTTPException(404, "product not found")
        for v in p.variants:
            v.stock = b.stock
        return {"sku": sku, "stock": b.stock}


# ----------------------------------------------------------------------------- customers, analytics, live updates
@router.get("/customers", dependencies=auth)
def customers(q: str | None = None):
    with session_scope() as s:
        query = s.query(m.Customer)
        if q:
            like = f"%{q.strip()}%"
            query = query.filter(m.Customer.name.ilike(like) | m.Customer.phone.like(like))
        out = []
        for c in query.order_by(m.Customer.name):
            live = [o for o in c.orders if o.status != "cancelled"]
            out.append({"id": c.id, "name": c.name, "phone": c.phone, "language": c.language, "points": c.points, "orders": len(c.orders),
                        "spent": sum(o.total for o in live), "last_order": max((o.created_at for o in c.orders), default=None) and max(o.created_at for o in c.orders).isoformat(timespec="minutes")})
        return out


@router.get("/customers/{cid}", dependencies=auth)
def customer(cid: int):
    with session_scope() as s:
        c = s.get(m.Customer, cid)
        if c is None:
            raise HTTPException(404, "customer not found")
        return {"id": c.id, "name": c.name, "phone": c.phone, "language": c.language, "points": c.points, "birthday_month": c.birthday_month,
                "addresses": [{"label": a.label, "text": a.text, "zone": a.zone_code} for a in c.addresses],
                "orders": [orders.serialize(s, o) for o in sorted(c.orders, key=lambda o: o.created_at, reverse=True)],
                "spent": sum(o.total for o in c.orders if o.status != "cancelled")}


@router.get("/analytics", dependencies=auth)
def analytics_view(date: dt.date | None = None, days: int = 7):
    with session_scope() as s:
        return analytics.compute(s, date, max(1, min(days, 90)))


@router.get("/updates", dependencies=auth)
def updates(after_order: int | None = None, after_escalation: int | None = None, after_message: int | None = None):
    """New rows since the watermarks. With no watermarks it only returns the current ones (where a fresh page starts from)."""
    if after_order is None and after_escalation is None and after_message is None:
        with session_scope() as s:
            return {"orders": [], "escalations": [], "messages": [], "watermarks": events.watermarks(s)}
    return events.poll({"order": after_order or 0, "escalation": after_escalation or 0, "message": after_message or 0})


@router.get("/events", dependencies=auth)
async def live(request: Request):
    """Server-sent events. Browsers' EventSource cannot send headers, so the token comes as ?token=."""
    with session_scope() as s:
        start = events.watermarks(s)

    async def gen():
        async for chunk in events.stream(start):
            if await request.is_disconnected():
                break
            yield chunk
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

