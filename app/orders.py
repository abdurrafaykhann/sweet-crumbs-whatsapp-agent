"""Staff actions on orders (the dashboard): search and filters, status changes, advance verification, cancellation and edits.
Each action returns {"ok": bool, ...}; when the customer must be told, the result carries `notify` = (phone, text) in the customer's
language, and the caller sends it after the database change is saved (see app/admin.py)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, func, or_
from sqlalchemy.orm import Session

from . import clock, inventory, notify, repo, rules
from . import models as m

LABELS = {"draft": "Waiting for confirmation", "awaiting_advance": "Waiting for the 50% advance", "confirmed": "Confirmed", "scheduled": "Scheduled",
          "preparing": "Preparing", "ready_for_pickup": "Ready for pickup", "out_for_delivery": "Out for delivery", "delivered": "Delivered",
          "cancelled": "Cancelled", "refund_processing": "Refund processing"}


def _day(column):
    """The calendar date of a timestamp column. (CAST(... AS DATE) is wrong on SQLite: it returns the year.)"""
    return func.date(column, type_=Date)


def add_event(s: Session, order: m.Order, kind: str, text: str, now: dt.datetime | None = None) -> None:
    s.add(m.OrderEvent(order_id=order.id, kind=kind, text=text[:300], created_at=now or clock.now()))


def summary(o: m.Order) -> str:
    return ", ".join(f"{int(i.qty)} x {i.name}" for i in o.items)


def get(s: Session, code: str) -> m.Order | None:
    return s.query(m.Order).filter(m.Order.code == code.strip().upper()).one_or_none()


def serialize(s: Session, o: m.Order, detail: bool = False) -> dict:
    branch = s.get(m.Branch, o.branch_id).name if o.branch_id else None
    out = {"code": o.code, "status": o.status, "status_label": LABELS.get(o.status, o.status), "source": o.source,
           "customer": {"name": o.customer.name, "phone": o.customer.phone, "language": o.customer.language},
           "fulfilment": o.fulfilment, "branch": branch, "address": o.address_text, "zone": o.zone_code, "items": summary(o),
           "subtotal": o.subtotal, "delivery_fee": o.delivery_fee, "discount": o.discount, "total": o.total,
           "payment_method": o.payment_method, "payment_status": o.payment_status, "advance_due": o.advance_due, "advance_paid": o.advance_paid,
           "slot_start": o.slot_start.isoformat(timespec="minutes") if o.slot_start else None,
           "slot_end": o.slot_end.isoformat(timespec="minutes") if o.slot_end else None, "asap": o.asap,
           "eta": o.eta.isoformat(timespec="minutes") if o.eta else None, "rider": o.rider_name, "notes": o.notes, "cake_message": o.cake_message,
           "is_gift": o.is_gift, "receiver_name": o.receiver_name, "receiver_phone": o.receiver_phone,
           "refund_amount": o.refund_amount, "refund_due": o.refund_due.isoformat() if o.refund_due else None,
           "created_at": o.created_at.isoformat(timespec="minutes"), "next_statuses": rules.next_statuses(o.status, o.fulfilment)}
    if detail:
        out["lines"] = [{"name": i.name, "qty": int(i.qty), "unit_price": i.unit_price, "line_total": i.line_total, "options": i.options} for i in o.items]
        out["events"] = [{"at": e.created_at.isoformat(timespec="minutes"), "kind": e.kind, "text": e.text}
                         for e in s.query(m.OrderEvent).filter(m.OrderEvent.order_id == o.id).order_by(m.OrderEvent.id)]
    return out


def list_orders(s: Session, *, status: str | None = None, date: dt.date | None = None, branch: str | None = None, fulfilment: str | None = None,
                q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    query = s.query(m.Order).join(m.Customer)
    if status:
        query = query.filter(m.Order.status == status)
    if date:
        query = query.filter(or_(_day(m.Order.created_at) == date, _day(m.Order.slot_start) == date))
    if branch:
        query = query.join(m.Branch, m.Branch.id == m.Order.branch_id).filter(m.Branch.name.ilike(f"%{branch}%"))
    if fulfilment:
        query = query.filter(m.Order.fulfilment == fulfilment)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(m.Order.code.ilike(like), m.Customer.name.ilike(like), m.Customer.phone.like(like)))
    total = query.count()
    rows = query.order_by(m.Order.created_at.desc(), m.Order.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [serialize(s, o) for o in rows]}


def _parse_eta(value, now: dt.datetime) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value
    text = str(value).strip()
    try:
        if len(text) <= 5 and ":" in text:  # "18:35" means today
            h, mi = text.split(":")
            return now.replace(hour=int(h), minute=int(mi), second=0, microsecond=0)
        return dt.datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None


def _fmt_time(t: dt.datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def change_status(s: Session, code: str, new_status: str, *, rider_name: str = "", eta=None, now: dt.datetime | None = None) -> dict:
    now = now or clock.now()
    o = get(s, code)
    if o is None:
        return {"ok": False, "error": "order_not_found"}
    if not rules.valid_transition(o.status, new_status, o.fulfilment):
        return {"ok": False, "error": "transition_not_allowed", "from": o.status, "allowed": rules.next_statuses(o.status, o.fulfilment)}
    if new_status == "out_for_delivery" and not rider_name.strip():
        return {"ok": False, "error": "rider_name_required"}
    if new_status == "cancelled":
        return cancel(s, code, "cancelled by staff", now=now)
    old = o.status
    o.status, o.updated_at = new_status, now
    fields: dict = {"code": o.code}
    text = f"Status: {LABELS.get(old, old)} -> {LABELS[new_status]}"
    if new_status == "out_for_delivery":
        zone = s.get(m.DeliveryZone, o.zone_code) if o.zone_code else None
        o.rider_name = rider_name.strip()
        o.eta = _parse_eta(eta, now) or (now + dt.timedelta(minutes=zone.max_minutes if zone else 60))
        fields.update(rider=o.rider_name, eta=_fmt_time(o.eta))
        text += f" (rider {o.rider_name}, ETA {_fmt_time(o.eta)})"
    if new_status == "ready_for_pickup":
        fields["branch"] = s.get(m.Branch, o.branch_id).name if o.branch_id else "our"
    if new_status == "delivered":
        o.delivered_at = now
        earned = rules.points_earned(o.total, repo.load_config(s))
        o.customer.points += earned
        fields["points"] = earned
        promised = o.eta or o.slot_end
        if promised and rules.is_late(promised, now, repo.load_config(s)):
            text += f"; delivered {int((now - promised).total_seconds() // 60)} min after the promised time: the delivery fee is refundable (a person processes it)"
    add_event(s, o, "status", text, now)
    return {"ok": True, "order": o.code, "status": new_status, "notify": (o.customer.phone, notify.status_text(new_status, o.customer.language, **fields))}


def cancel(s: Session, code: str, reason: str = "cancelled by staff", *, now: dt.datetime | None = None) -> dict:
    """Staff cancel an order (any status before delivery). Stock this system reserved goes back on sale."""
    now = now or clock.now()
    o = get(s, code)
    if o is None:
        return {"ok": False, "error": "order_not_found"}
    if o.status in ("delivered", "cancelled"):
        return {"ok": False, "error": "transition_not_allowed", "from": o.status, "allowed": []}
    for item in o.items:
        for vid, q in (item.options or {}).get("reserve") or []:
            inventory.release(s, vid, q)
    text = f"Cancelled: {reason}"
    if any(i.sku.startswith("CU-") for i in o.items):
        rule = rules.cancellation(o.status, True, o.slot_start, now, repo.load_config(s))
        text += f". Custom cake: advance refund {rule['refund']} (a person processes it)"
    o.status, o.updated_at = "cancelled", now
    add_event(s, o, "cancel", text, now)
    return {"ok": True, "order": o.code, "status": "cancelled", "notify": (o.customer.phone, notify.status_text("cancelled", o.customer.language, code=o.code))}


def verify_advance(s: Session, code: str, action: str, *, amount: int | None = None, note: str = "", now: dt.datetime | None = None) -> dict:
    """A person has looked at the payment screenshot. The bot never decides this."""
    now = now or clock.now()
    o = get(s, code)
    if o is None:
        return {"ok": False, "error": "order_not_found"}
    if o.status != "awaiting_advance":
        return {"ok": False, "error": "not_waiting_for_an_advance", "status": o.status}
    if action == "confirm":
        paid = int(amount) if amount else o.advance_due
        o.advance_paid, o.payment_status = paid, "advance_paid"
        o.status = "scheduled" if o.slot_start and o.slot_start.date() > now.date() else "confirmed"
        o.updated_at = now
        add_event(s, o, "payment", f"Advance of Rs {paid:,} verified by staff. Order {o.status}." + (f" Note: {note}" if note else ""), now)
        return {"ok": True, "order": o.code, "status": o.status,
                "notify": (o.customer.phone, notify.status_text("advance_confirmed", o.customer.language, code=o.code, amount=f"{paid:,}"))}
    if action == "reject":
        add_event(s, o, "payment", "Payment could not be verified" + (f": {note}" if note else ""), now)
        return {"ok": True, "order": o.code, "status": o.status,
                "notify": (o.customer.phone, notify.status_text("advance_rejected", o.customer.language, code=o.code))}
    return {"ok": False, "error": "unknown_action"}


EDITABLE = {"address": "address_text", "notes": "notes", "cake_message": "cake_message", "rider": "rider_name"}


def edit(s: Session, code: str, changes: dict, *, now: dt.datetime | None = None) -> dict:
    """Staff correct an order: address, notes, cake message, rider, slot or ETA."""
    now = now or clock.now()
    o = get(s, code)
    if o is None:
        return {"ok": False, "error": "order_not_found"}
    if o.status in ("delivered", "cancelled"):
        return {"ok": False, "error": "order_is_closed"}
    done = []
    for key, attr in EDITABLE.items():
        if changes.get(key) is not None:
            setattr(o, attr, changes[key])
            done.append(key)
    if changes.get("slot_start"):
        start = _parse_eta(changes["slot_start"], now)
        if start is None:
            return {"ok": False, "error": "bad_slot_start"}
        o.slot_start, o.slot_end, o.asap = start, start + dt.timedelta(hours=1), False
        done.append("slot")
    if changes.get("eta"):
        eta = _parse_eta(changes["eta"], now)
        if eta is None:
            return {"ok": False, "error": "bad_eta"}
        o.eta = eta
        done.append("eta")
    if not done:
        return {"ok": False, "error": "nothing_to_change"}
    o.updated_at = now
    add_event(s, o, "edit", "Edited by staff: " + ", ".join(done), now)
    return {"ok": True, "order": o.code, "changed": done}
