"""The agent's tools. Each acts only for the phone number that is chatting (taken from the request context, never from the
LLM's arguments), reads/writes the database and applies the Python rules in app.rules / app.pricing.

The confirm guard lives in confirm_order(): an order is placed only if (1) the customer has been shown a summary of the
CURRENT draft and (2) that summary was created in an EARLIER customer turn than this call."""
from __future__ import annotations

import datetime as dt
import difflib
import json
import threading
from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import inventory, orders, pricing, repo, rules
from . import models as m
from .database import session_scope

ACTIVE_STATUSES = ("draft", "awaiting_advance", "confirmed", "scheduled", "preparing", "ready_for_pickup", "out_for_delivery", "refund_processing")
FOODPANDA_NOTE = "ordering through Foodpanda if it shows us in their area (prices there are about 10% higher)"


@dataclass(frozen=True)
class Ctx:
    phone: str
    turn: int
    now: dt.datetime


def ctx_from_config(config: dict) -> Ctx:
    c = config["configurable"]
    return Ctx(phone=c["phone"], turn=int(c["turn"]), now=dt.datetime.fromisoformat(c["now"]))


def _compact(x):
    """Drop None / empty values so tool results stay short (free tiers are token limited)."""
    if isinstance(x, dict):
        return {k: _compact(v) for k, v in x.items() if v not in (None, "", [], {})}
    if isinstance(x, list):
        return [_compact(v) for v in x]
    return x


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ----------------------------------------------------------------------------- draft storage
def load_state(s: Session, phone: str) -> dict:
    st = pricing.new_state()
    row = s.get(m.Draft, phone)
    if row and row.state:
        st.update(row.state)
    return st


def save_state(s: Session, phone: str, st: dict) -> None:
    clean = json.loads(json.dumps(st))
    row = s.get(m.Draft, phone)
    if row is None:
        s.add(m.Draft(phone=phone, state=clean, updated_at=_utcnow()))
    else:
        row.state, row.updated_at = clean, _utcnow()


def _norm_item(i: dict) -> dict:
    deal = i.get("deal")
    return {"sku": i.get("sku"), "variant": None if deal else i.get("variant"), "qty": max(int(i.get("qty") or 1), 1),
            "eggless": bool(i.get("eggless")), "message": i.get("message"), "deal": deal,
            "swap_cake": i.get("swap_cake") or (i.get("variant") if deal else None)}


def apply_patch(st: dict, p: dict, reset: bool = False) -> list[str]:
    """Apply a partial update to a draft state. Returns error strings (bad date format...)."""
    errors: list[str] = []
    if reset:
        st.clear()
        st.update(pricing.new_state())
    if p.get("clear"):
        st.clear()
        st.update(pricing.new_state())
    if p.get("items") is not None:
        st["items"] = [_norm_item(i) for i in p["items"]]
    for i in p.get("add_items") or []:
        n = _norm_item(i)
        same = next((x for x in st["items"] if x.get("sku") == n["sku"] and x.get("variant") == n["variant"] and x.get("deal") == n["deal"]
                     and x.get("eggless") == n["eggless"]), None)
        if same and not n["deal"]:
            same["qty"] += n["qty"]
        else:
            st["items"].append(n)
    for key in p.get("remove") or []:
        st["items"] = [x for x in st["items"] if key not in (x.get("sku"), x.get("deal"))]
    if p.get("cake_message") is not None:  # "" means the customer wants no message
        for x in st["items"]:
            if x.get("message") is None and (str(x.get("sku") or "").startswith(("CK-", "CU-THEME")) or x.get("deal") == "Birthday Bundle"):
                x["message"] = p["cake_message"]
    for key, field in (("fulfilment", "fulfilment"), ("address", "address_text"), ("area", "area"), ("branch", "branch"),
                       ("payment_method", "payment_method"), ("customer_name", "customer_name"), ("notes", "notes"),
                       ("hide_price", "hide_price"), ("use_points", "use_points"), ("birthday_discount", "birthday_discount")):
        if p.get(key) is not None:
            st[field] = p[key]
    if p.get("slot_start"):
        d = pricing._parse_dt(p["slot_start"])
        if d is None:
            errors.append("slot_start must be an ISO local time like 2026-11-07T19:00")
        else:
            st["slot_start"], st["asap"] = d.isoformat(timespec="minutes"), False
    if p.get("asap"):
        st["asap"], st["slot_start"] = True, None
    if p.get("receiver_name") or p.get("receiver_phone"):
        g = dict(st.get("gift") or {})
        g.update({k: p[k] for k in ("receiver_name", "receiver_phone") if p.get(k)})
        st["gift"] = g
    st["version"] = int(st.get("version") or 0) + 1
    return errors


_NEXT_STEP = {
    "ready_for_summary": "Send the summary_text to the customer in their language (keep every number) and ask them to reply YES. "
                         "Do NOT call confirm_order in this turn.",
    "needs_human": "Collect the remaining details, then call escalate_to_human with a full summary: this needs our staff (custom/bulk/wedding).",
    "has_problems": "Tell the customer what is not possible and offer the alternatives from the problems; then update_draft.",
    "incomplete": "Ask the customer for the first one or two items in `missing` only.",
}


def _present(view: dict, st: dict) -> dict:
    out = {k: v for k, v in view.items() if k != "_internal"}
    out["draft_version"] = st["version"]
    out["next_step"] = _NEXT_STEP[view["status"]]
    return _compact(out)


_draft_locks: dict[str, threading.Lock] = {}
_draft_locks_guard = threading.Lock()


def _draft_lock(phone: str) -> threading.Lock:
    with _draft_locks_guard:
        return _draft_locks.setdefault(phone, threading.Lock())


def _draft_tool(ctx: Ctx, patch: dict, reset: bool) -> dict:
    # LangGraph runs tool calls of one model step concurrently: serialise draft changes per phone so none is lost
    with _draft_lock(ctx.phone), session_scope() as s:
        st = load_state(s, ctx.phone)
        if st.get("created_turn") is None or not st.get("items"):
            st["created_turn"] = ctx.turn
        # draft_order starts fresh only when the existing draft is from an EARLIER customer message; in the same message it
        # merges, so an update_draft that ran at the same moment is not wiped
        do_reset = bool(reset and st.get("items") and st.get("created_turn") != ctx.turn)
        errors = apply_patch(st, patch, reset=do_reset)
        if do_reset:
            st["created_turn"] = ctx.turn
        view = pricing.evaluate(s, ctx.phone, st, ctx.now)
        r = view["_internal"]["resolved"]
        if r.get("area"):
            st["area"], st["zone"] = r["area"], r.get("zone")
        if r.get("branch"):
            st["branch"] = r["branch"]
        if view["status"] == "ready_for_summary":  # the customer is about to be shown this: remember which version and turn
            st["summary_version"], st["summary_turn"] = st["version"], ctx.turn
        save_state(s, ctx.phone, st)
        out = _present(view, st)
        if errors:
            out["errors"] = errors
        return out


def draft_order(ctx: Ctx, **patch) -> dict:
    """Start a NEW draft (replaces any earlier one)."""
    return _draft_tool(ctx, patch, reset=True)


def update_draft(ctx: Ctx, **patch) -> dict:
    """Change the current draft; every change needs a fresh summary and a fresh YES."""
    return _draft_tool(ctx, patch, reset=False)


# ----------------------------------------------------------------------------- read-only lookups
def search_menu(ctx: Ctx, query: str = "", category: str = "") -> dict:
    with session_scope() as s:
        products = s.query(m.Product).order_by(m.Product.id).all()
        cats: dict[str, int] = {}
        for p in products:
            cats[p.category] = cats.get(p.category, 0) + 1

        def card(p: m.Product) -> dict:
            flags = []
            if p.eggless_ok:
                flags.append("eggless possible (+Rs 300/lb, 24 h notice)")
            if p.contains_nuts:
                flags.append("contains nuts")
            if not p.delivery_ok:
                flags.append("dine-in/takeaway only")
            if p.sold_days:
                flags.append("only " + "/".join(p.sold_days))
                if ctx.now.strftime("%a") not in p.sold_days:
                    flags.append(f"NOT baked today ({ctx.now.strftime('%A')}): it IS on our menu, so say it is a weekend item and offer to schedule it for the next {'/'.join(p.sold_days)}")
            if p.made_to_order:
                flags.append("made to order")
            if p.notice_hours:
                flags.append(f"{p.notice_hours} h notice")
            if p.is_custom or p.requires_human:
                flags.append("custom: staff confirm the quote")
            if p.variants and all(v.stock == 0 for v in p.variants):
                flags.append("SOLD OUT today: offer the closest alternative")
            return {"sku": p.sku, "name": p.name, "category": p.category, "notes": p.notes if p.category == "Cakes" else "",
                    "variants": [{"label": v.label, "price": v.price, "left": v.stock, "in_stock": v.stock is None or v.stock > 0} for v in p.variants],
                    "flags": flags}

        if not query and not category:
            return {"categories": [{"name": k, "items": v} for k, v in cats.items()],
                    "hint": "do not list everything: ask which category, or search by name"}
        found = products
        if category:
            c = rules.normalize(category)
            found = [p for p in products if c in rules.normalize(p.category) or rules.normalize(p.category) in c]
        if query:
            tokens = [t for t in rules.normalize(query).split() if len(t) >= 2]
            scored = []
            for p in found:
                hay = rules.normalize(f"{p.name} {p.category} {p.sku} {p.notes}")
                words = hay.split()
                score = sum(2 for t in tokens if t in hay) + sum(1 for t in tokens if t not in hay and difflib.get_close_matches(t, words, 1, 0.82))
                if score:
                    scored.append((score, p))
            scored.sort(key=lambda x: -x[0])
            found = [p for _, p in scored][:8]
        else:
            found = found[:20]
        deals = []
        if query:
            q = rules.normalize(query)
            for d in s.query(m.Deal).all():
                if any(t in rules.normalize(f"{d.name} {d.includes}") for t in q.split() if len(t) >= 3):
                    deals.append({"deal": d.name, "price": d.price, "usual_price": d.usual_price, "includes": d.includes, "when": d.when_text,
                                  "where": d.where_text})
        return _compact({"results": [card(p) for p in found], "deals": deals[:3],
                         "note": None if found else "no match: try other English words, or list a category"})


def check_stock(ctx: Ctx, sku: str, variant: str = "", qty: int = 1) -> dict:
    with session_scope() as s:
        p = s.query(m.Product).filter(m.Product.sku == sku).one_or_none()
        if p is None:
            return {"error": "unknown_sku", "hint": "use search_menu to find the sku"}
        v, err = pricing.pick_variant(p, variant or None)
        if v is None:
            return {"error": err, "item": p.name, "options": [{"variant": x.label, "price": x.price, "left": x.stock} for x in p.variants]}
        day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][ctx.now.weekday()]
        if p.sold_days and day not in p.sold_days:
            return _compact({"item": pricing.variant_name(p, v), "available": False, "reason": f"only sold on {'/'.join(p.sold_days)}",
                             "alternatives": pricing.alternatives(s, v, qty)})
        ok = v.stock is None or v.stock >= qty
        return _compact({"item": pricing.variant_name(p, v), "sku": p.sku, "variant": v.label, "price": v.price, "available": ok,
                         "left": v.stock, "made_to_order": p.made_to_order or None, "notice_hours": p.notice_hours or None,
                         "alternatives": [] if ok else pricing.alternatives(s, v, qty)})


def check_delivery_area(ctx: Ctx, text: str) -> dict:
    with session_scope() as s:
        cfg = repo.load_config(s)
        hit = rules.resolve_area(text, repo.areas_as_dicts(s), tuple(pricing._setting(s, "block_areas", [])))
        base = {"status": hit.status, "minimum_order": cfg.min_order, "free_delivery_from": cfg.free_delivery_from}
        if hit.status == "served":
            z = s.get(m.DeliveryZone, hit.zone_code)
            return _compact({**base, "area": hit.area, "zone": z.code, "fee": z.fee, "minutes": [z.min_minutes, z.max_minutes], "note": hit.note})
        if hit.status == "not_served":
            return _compact({**base, "area": hit.area, "pickup_branches": [b.name for b in s.query(m.Branch).all()], "other_option": FOODPANDA_NOTE})
        if hit.status == "unclear":
            return {**base, "ask_which_area": list(hit.candidates)}
        return {**base, "hint": "area not recognised: ask which area of Karachi"}


def get_customer_profile(ctx: Ctx) -> dict:
    with session_scope() as s:
        c = s.query(m.Customer).filter(m.Customer.phone == ctx.phone).one_or_none()
        if c is None:
            return {"known": False, "hint": "new customer: ask for their name when you need it"}
        cfg = repo.load_config(s)
        orders = sorted(c.orders, key=lambda o: o.created_at, reverse=True)

        def brief(o: m.Order) -> dict:
            return {"code": o.code, "status": o.status, "total": o.total, "items": ", ".join(f"{int(i.qty)} x {i.name}" for i in o.items)}
        return _compact({
            "known": True, "name": c.name, "usual_language": {"en": "English", "rur": "Roman Urdu", "ur": "Urdu"}.get(c.language),
            "points": c.points, "points_value_rs": rules.redeemable(c.points, 10 ** 9, cfg)["rs_off"],
            "birthday_this_month": c.birthday_month == ctx.now.month,
            "saved_addresses": [{"label": a.label, "text": a.text, "zone": a.zone_code, "served": a.zone_code is not None} for a in c.addresses],
            "active_orders": [brief(o) for o in orders if o.status in ACTIVE_STATUSES],
            "recent_orders": [brief(o) for o in orders if o.status not in ACTIVE_STATUSES][:3]})


def get_info(ctx: Ctx, topic: str = "") -> dict:
    """Facts by topic name, or by searching the text of every topic ('halal', 'parking', 'gelatin'...)."""
    with session_scope() as s:
        topics = {t.slug: t for t in s.query(m.InfoTopic).all()}
        key = rules.normalize(topic).replace(" ", "_")
        if key in topics:
            t = topics[key]
            return {"topic": key, "title": t.title, "text": t.text[:3500]}
        words = [w for w in rules.normalize(topic).split() if len(w) >= 3]
        scored = []
        for slug, t in topics.items():
            hay, title = t.text.lower(), t.title.lower()
            score = sum(hay.count(w) + (3 if w in title else 0) for w in words)
            if score:
                scored.append((score, slug))
        scored.sort(reverse=True)
        if not scored:
            return {"topics": [{"topic": k, "about": t.title} for k, t in topics.items()], "hint": "pick one of these topics"}
        t = topics[scored[0][1]]
        out = {"topic": scored[0][1], "title": t.title, "text": t.text[:3500]}
        also = [slug for sc, slug in scored[1:3] if sc >= 0.6 * scored[0][0]]
        if also:
            out["also_see"] = also
        return out


# ----------------------------------------------------------------------------- confirm: the guard
def _next_code(s: Session) -> str:
    res = s.execute(update(m.Counter).where(m.Counter.name == "order").values(value=m.Counter.value + 1).returning(m.Counter.value))
    return f"SC-{res.scalar_one() - 1}"


def confirm_order(ctx: Ctx) -> dict:
    with session_scope() as s:
        st = load_state(s, ctx.phone)
        if not st.get("items"):
            return {"confirmed": False, "error": "no_draft", "hint": "there is no order in progress"}
        if st.get("summary_version") != st.get("version"):
            return {"confirmed": False, "error": "no_summary_for_current_draft",
                    "hint": "the customer has not been shown a summary of the current draft: show it and wait for their YES"}
        if st.get("summary_turn") is None or st["summary_turn"] >= ctx.turn:
            return {"confirmed": False, "error": "confirm_in_same_turn",
                    "hint": "the summary was created in this same customer message: send it and wait for the customer's YES in their next message"}
        view = pricing.evaluate(s, ctx.phone, st, ctx.now)
        if view["status"] != "ready_for_summary":
            return {"confirmed": False, "error": "draft_not_valid_anymore", **_present(view, st)}
        i = view["_internal"]
        ok, failed = inventory.reserve_many(s, [(vid, q) for vid, q in i["reserve"].items()])
        if not ok:
            v = s.get(m.Variant, failed)
            return {"confirmed": False, "error": "sold_out_just_now", "item": pricing.variant_name(v.product, v),
                    "alternatives": pricing.alternatives(s, v)}
        customer = s.query(m.Customer).filter(m.Customer.phone == ctx.phone).one_or_none()
        if customer is None:
            customer = m.Customer(phone=ctx.phone, name=i["name"] or "", language="en", points=0)
            s.add(customer)
        elif not customer.name and i["name"]:
            customer.name = i["name"]
        if i["points_used"]:
            customer.points -= i["points_used"]
        slot = i["slot"]
        today = ctx.now.date()
        status = "awaiting_advance" if i["advance_amount"] else ("scheduled" if slot and slot.date() > today else "confirmed")
        gift = st.get("gift") or {}
        messages = [ln["options"].get("message") for ln in i["lines"] if ln["options"].get("message")]
        code = _next_code(s)
        o = m.Order(code=code, customer=customer, status=status, fulfilment=st["fulfilment"], branch_id=i["branch_id"],
                    address_text=st.get("address_text") or "", zone_code=i["zone"], subtotal=i["subtotal"], delivery_fee=i["fee"],
                    discount=i["discount"], total=i["total"], payment_method=st["payment_method"],
                    payment_status="advance_pending" if i["advance_amount"] else "unpaid", advance_due=i["advance_amount"],
                    slot_start=slot, slot_end=(slot + dt.timedelta(hours=1)) if slot else None, asap=bool(st.get("asap")), eta=i["eta"],
                    cake_message=messages[0] if messages else "", is_gift=bool(gift), receiver_name=gift.get("receiver_name", ""),
                    receiver_phone=gift.get("receiver_phone", ""), notes=(st.get("notes") or "") + (" [price hidden on bill]" if st.get("hide_price") else ""),
                    created_at=ctx.now, updated_at=ctx.now)
        for ln in i["lines"]:
            o.items.append(m.OrderItem(variant_id=ln["variant_id"], sku=ln["sku"], name=ln["name"], qty=ln["qty"], unit_price=ln["unit_price"],
                                       line_total=ln["line_total"], options=ln["options"]))
        s.add(o)
        s.flush()
        orders.add_event(s, o, "placed", f"Placed by the assistant ({status}, {st['payment_method']}, total Rs {o.total:,})", ctx.now)
        s.query(m.Draft).filter(m.Draft.phone == ctx.phone).delete()
        pay = pricing._setting(s, "payment_methods", {})
        how = {"cash": f"Pay Rs {o.total - o.advance_due:,} in cash to the rider" if st["fulfilment"] == "delivery" else f"Pay Rs {o.total:,} at pickup",
               "counter": f"Pay Rs {o.total:,} at the counter (card accepted)",
               "jazzcash": pay.get("JazzCash / Easypaisa"), "bank": pay.get("Bank transfer")}[st["payment_method"]]
        return _compact({"confirmed": True, "code": code, "status": status, "total": o.total, "when": view["slot"], "payment": how,
                         "advance_due": o.advance_due or None,
                         "next": "for JazzCash/bank: ask them to send the payment screenshot here with the order code; a person verifies it"
                         if st["payment_method"] in ("jazzcash", "bank") else None})


# ----------------------------------------------------------------------------- existing orders
def _own_order(s: Session, phone: str, code: str) -> m.Order | None:
    return (s.query(m.Order).join(m.Customer).filter(m.Customer.phone == phone, m.Order.code == code.strip().upper()).one_or_none())


def _order_card(s: Session, o: m.Order, now: dt.datetime) -> dict:
    label = s.get(m.StatusLabel, o.status)
    cfg = repo.load_config(s)
    promised = o.eta or o.slot_end
    late = bool(promised and o.status in ("confirmed", "scheduled", "preparing", "out_for_delivery") and now > promised + dt.timedelta(minutes=cfg.late_grace_min))
    branch = s.get(m.Branch, o.branch_id).name if o.branch_id else None
    return _compact({
        "code": o.code, "status": o.status, "status_en": label.en if label else o.status, "status_rur": label.rur if label else o.status,
        "items": [f"{int(i.qty)} x {i.name}" for i in o.items], "total": o.total, "fulfilment": o.fulfilment, "address": o.address_text,
        "pickup_branch": branch, "slot": pricing.slot_text(o.slot_start, now) if o.slot_start else None,
        "eta": o.eta.strftime("%I:%M %p").lstrip("0") if o.eta else None, "rider": o.rider_name, "payment_method": o.payment_method,
        "payment_status": o.payment_status, "advance_due": o.advance_due or None, "refund": (
            {"amount": o.refund_amount, "due_by": o.refund_due.isoformat() if o.refund_due else None} if o.refund_amount else None),
        "delivered_at": o.delivered_at.strftime("%d %b %I:%M %p") if o.delivered_at else None, "notes": o.notes, "cake_message": o.cake_message,
        "late_by_minutes": int((now - promised).total_seconds() // 60) if late else None})


def order_status(ctx: Ctx, code: str = "") -> dict:
    with session_scope() as s:
        if code:
            o = _own_order(s, ctx.phone, code)
            if o is None:  # same answer for "does not exist" and "belongs to someone else": never reveal other customers' orders
                return {"found": False, "hint": "no order with that code under this phone number"}
            return {"found": True, "orders": [_order_card(s, o, ctx.now)]}
        orders = (s.query(m.Order).join(m.Customer).filter(m.Customer.phone == ctx.phone).order_by(m.Order.created_at.desc()).all())
        active = [o for o in orders if o.status in ACTIVE_STATUSES]
        recent = [o for o in orders if o.status not in ACTIVE_STATUSES][:2]
        if not orders:
            return {"found": False, "hint": "no orders under this phone number"}
        out = {"found": True, "orders": [_order_card(s, o, ctx.now) for o in active + recent]}
        if len(active) > 1:
            out["hint"] = "several active orders: ask which one the customer means"
        return out


def _escalate(s: Session, ctx: Ctx, reason: str, summary: str = "", priority: str = "normal", order_code: str = "") -> m.Escalation:
    """One open escalation per customer: a second call (the cancel tool escalates by itself and the model may also call
    escalate_to_human) adds its details to the open one instead of creating a duplicate in the owner's queue."""
    open_one = s.query(m.Escalation).filter(m.Escalation.phone == ctx.phone, m.Escalation.status == "open").order_by(m.Escalation.id.desc()).first()
    if open_one is not None:
        if summary and summary not in open_one.summary:
            open_one.summary = (open_one.summary + "\n" + summary).strip()
        if reason[:120] not in open_one.reason:
            open_one.reason = f"{open_one.reason}; {reason}"[:120]
        if priority == "high":
            open_one.priority = "high"
        if order_code and not open_one.order_code:
            open_one.order_code = order_code
        return open_one
    st = load_state(s, ctx.phone)
    last_id = s.query(m.Message.id).filter(m.Message.phone == ctx.phone).order_by(m.Message.id.desc()).limit(1).scalar() or 0
    e = m.Escalation(phone=ctx.phone, message_id=last_id, order_code=order_code, reason=reason[:120], summary=summary, priority=priority,
                     collected=json.loads(json.dumps(st)) if st.get("items") else {}, created_at=ctx.now)
    s.add(e)
    conv = s.get(m.Conversation, ctx.phone)
    if conv is not None:
        conv.escalated = True
    return e


def escalate_to_human(ctx: Ctx, reason: str, summary: str = "", priority: str = "normal", order_code: str = "") -> dict:
    with session_scope() as s:
        e = _escalate(s, ctx, reason, summary, priority, order_code)
        s.flush()
        return {"escalated": True, "id": e.id, "hint": "tell the customer once that our staff will reply in this chat; do not promise outcomes or refunds"}


def cancel_order(ctx: Ctx, code: str) -> dict:
    with session_scope() as s:
        o = _own_order(s, ctx.phone, code)
        if o is None:
            return {"found": False, "hint": "no order with that code under this phone number"}
        is_custom = any(i.sku.startswith("CU-") for i in o.items)
        rule = rules.cancellation(o.status, is_custom, o.slot_start, ctx.now, repo.load_config(s))
        if not rule["allowed"]:
            _escalate(s, ctx, "cancel_after_preparation", f"Customer asked to cancel {o.code} (status {o.status}); kitchen already started", "high", o.code)
            return {"cancelled": False, "code": o.code, "status": o.status, "escalated": True,
                    "explain": "the kitchen has already started on it, so it cannot be cancelled now; our team will contact them"}
        for item in o.items:  # only stock this system reserved is given back (the seeded demo orders carry no reservation record)
            for vid, q in (item.options or {}).get("reserve") or []:
                inventory.release(s, vid, q)
        o.status, o.updated_at = "cancelled", ctx.now
        orders.add_event(s, o, "cancel", "Cancelled by the customer in the chat", ctx.now)
        out = {"cancelled": True, "code": o.code}
        if rule["escalate"]:  # custom cake: a person handles the advance refund
            _escalate(s, ctx, "custom_cake_cancellation", f"Custom order {o.code} cancelled; advance refund: {rule['refund']}", "normal", o.code)
            out.update(escalated=True, advance_refund=rule["refund"])
        return out


def modify_order(ctx: Ctx, code: str, items: list | None = None, address: str | None = None, slot_start: str | None = None,
                 cake_message: str | None = None, notes: str | None = None, payment_method: str | None = None) -> dict:
    with session_scope() as s:
        o = _own_order(s, ctx.phone, code)
        if o is None:
            return {"found": False, "hint": "no order with that code under this phone number"}
        if not rules.can_change(o.status):
            _escalate(s, ctx, "change_after_preparation", f"Customer asked to change {o.code} (status {o.status})", "normal", o.code)
            return {"changed": False, "code": o.code, "status": o.status, "escalated": True,
                    "explain": "preparation has started, so it cannot be changed; our team will contact them"}
        if cake_message is not None and set((address, slot_start, items, notes, payment_method)) == {None}:
            if o.slot_start and not rules.cake_message_editable(o.slot_start, ctx.now, repo.load_config(s)):
                return {"changed": False, "reason": "message_locked", "explain": "the cake message can be changed until 2 hours before delivery"}
        # rebuild a draft from the order, apply the change, validate with the same rules as a new order
        st = pricing.new_state()
        st.update(fulfilment=o.fulfilment, address_text=o.address_text, slot_start=o.slot_start.isoformat(timespec="minutes") if o.slot_start else None,
                  asap=bool(o.asap and not o.slot_start), payment_method=o.payment_method, customer_name=o.customer.name, notes=o.notes,
                  branch=s.get(m.Branch, o.branch_id).name if o.branch_id else None)
        if o.is_gift:
            st["gift"] = {"receiver_name": o.receiver_name, "receiver_phone": o.receiver_phone}
        for it in o.items:
            if it.options.get("free"):
                continue
            if it.options.get("deal"):
                st["items"].append({"deal": it.options["deal"], "qty": int(it.qty), "message": it.options.get("message")})
            else:
                var = s.get(m.Variant, it.variant_id)
                st["items"].append({"sku": it.sku, "variant": var.label if var else None, "qty": int(it.qty),
                                    "eggless": bool(it.options.get("eggless")), "message": it.options.get("message") or ""})
        nested = s.begin_nested()
        for it in o.items:  # give this order's stock back while re-validating, so it does not block itself
            for vid, q in it.options.get("reserve") or []:
                inventory.release(s, vid, q)
        errs = apply_patch(st, {"items": items, "address": address, "slot_start": slot_start, "cake_message": cake_message, "notes": notes,
                                "payment_method": payment_method})
        view = pricing.evaluate(s, ctx.phone, st, ctx.now)
        if errs or view["status"] != "ready_for_summary":
            nested.rollback()
            return _compact({"changed": False, "problems": view["problems"], "missing": view["missing"], "errors": errs})
        i = view["_internal"]
        ok, failed = inventory.reserve_many(s, list(i["reserve"].items()))
        if not ok:
            nested.rollback()
            return {"changed": False, "reason": "sold_out", "hint": "not enough stock for the change"}
        nested.commit()
        o.items.clear()
        for ln in i["lines"]:
            o.items.append(m.OrderItem(variant_id=ln["variant_id"], sku=ln["sku"], name=ln["name"], qty=ln["qty"], unit_price=ln["unit_price"],
                                       line_total=ln["line_total"], options=ln["options"]))
        slot = i["slot"]
        o.address_text, o.zone_code, o.subtotal, o.delivery_fee, o.discount, o.total = (st.get("address_text") or "", i["zone"], i["subtotal"],
                                                                                         i["fee"], i["discount"], i["total"])
        o.slot_start, o.slot_end = slot, (slot + dt.timedelta(hours=1)) if slot else None
        o.payment_method, o.notes, o.updated_at = st["payment_method"], st.get("notes") or "", ctx.now
        msgs = [ln["options"].get("message") for ln in i["lines"] if ln["options"].get("message")]
        o.cake_message = msgs[0] if msgs else ""
        orders.add_event(s, o, "edit", "Changed by the customer in the chat", ctx.now)
        return {"changed": True, "order": _order_card(s, o, ctx.now), "new_total": o.total}


# ----------------------------------------------------------------------------- LangChain wrappers
def for_llm(result):
    """What the model sees. A draft that is ready already carries summary_text with every line and number, so the structured
    copies are dropped (tokens); the Python functions still return everything, for tests and the dashboard."""
    if isinstance(result, dict) and result.get("status") == "ready_for_summary" and "summary_text" in result:
        return {k: result[k] for k in ("status", "total", "summary_text", "draft_version", "next_step") if k in result}
    return result


def build_tools() -> list:
    """The 12 tools as LangChain StructuredTools with compact JSON schemas (free-tier token budgets).
    The phone number, turn and clock come from the run config; any argument the schema does not list (for example a
    made-up `phone`) is dropped before the tool runs."""
    from langchain_core.tools import StructuredTool

    def S(desc: str = "", **kw) -> dict:
        d = {"type": "string", **kw}
        if desc:
            d["description"] = desc
        return d

    B, I = {"type": "boolean"}, {"type": "integer"}
    METHODS = ["cash", "jazzcash", "bank", "counter"]
    ITEM_HELP = ("items: [{sku, variant e.g. '2 lb' (for a deal: swapped cake sku), qty, eggless, "
                 "message (cake text, '' = none, omit if not asked yet), deal (name, instead of sku)}]")
    ITEM = {"type": "object", "properties": {"sku": S(), "variant": S(), "qty": I, "eggless": B, "message": S(), "deal": S()}}
    FIELDS = {
        "fulfilment": S(enum=["delivery", "pickup"]), "address": S("full address as written"), "branch": S("DHA Phase 6, Gulshan-e-Iqbal or Bahadurabad"),
        "slot_start": S("1-hour slot start, ISO like 2026-11-07T19:00"), "asap": B, "payment_method": S(enum=METHODS), "customer_name": S(),
        "cake_message": S("for cakes not answered yet; '' = none"), "receiver_name": S("gift"), "receiver_phone": S("gift"), "hide_price": B,
        "notes": S(), "use_points": B, "birthday_discount": B}
    ITEMS = {"type": "array", "items": ITEM}

    def obj(props: dict, required: tuple = ()) -> dict:
        d = {"type": "object", "properties": props}
        if required:
            d["required"] = list(required)
        return d

    def wrap(fn, name: str, description: str, schema: dict, also_accept: tuple = ()):
        allowed = set(schema["properties"]) | set(also_accept)
        ints = {k for k, v in schema["properties"].items() if v.get("type") == "integer"}

        def run(config: RunnableConfig, **kwargs):
            kw = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
            for k in ints & set(kw):
                kw[k] = int(kw[k])
            return for_llm(fn(ctx_from_config(config), **kw))
        return StructuredTool.from_function(func=run, name=name, description=description, args_schema=schema)

    return [wrap(*spec) for spec in [
        (search_menu, "search_menu", "Menu items with prices, stock, flags. Use English words; empty query lists categories.",
         obj({"query": S("e.g. 'chocolate cake'"), "category": S("e.g. Cakes, Savoury, Drinks")})),
        (check_stock, "check_stock", "Live stock of one item/size, with in-stock alternatives if unavailable.",
         obj({"sku": S(), "variant": S(), "qty": I}, ("sku",))),
        (check_delivery_area, "check_delivery_area", "Zone, fee and time for an area/address; says if not served or unclear.",
         obj({"text": S("area or address")}, ("text",))),
        (get_customer_profile, "get_customer_profile", "This customer's name, points, saved addresses, active and recent orders.", obj({})),
        (get_info, "get_info", "Business facts: hours, branches, delivery, payment, deals, policies, allergens, storage, loyalty.",
         obj({"topic": S("branches_hours, delivery, payment, deals, ordering_rules, cancellation_changes, complaints_refunds, storage, "
                         "allergens, loyalty, business_profile, serving_guide, other; or a keyword like 'halal'")})),
        (draft_order, "draft_order", "Start the order draft with its items (a new draft if the earlier one is from an earlier message). Returns validation, "
         "totals, what is missing. Other fields can be sent too, or with update_draft. " + ITEM_HELP, obj({"items": ITEMS}, ("items",)), tuple(FIELDS)),
        (update_draft, "update_draft", "Change the current draft; same fields as draft_order plus add_items, remove (skus), clear. Returns validation, totals, "
         "what is missing. items replaces ALL items. " + ITEM_HELP, obj({"items": ITEMS, "add_items": ITEMS, "remove": {"type": "array", "items": S()},
                                                                           "clear": B, **FIELDS})),
        (confirm_order, "confirm_order", "Place the order. Only after the customer said yes to the summary in an EARLIER message; fails otherwise.", obj({})),
        (order_status, "order_status", "Status of the customer's own orders (one code, or all active and recent).",
         obj({"code": S("e.g. SC-10421; empty = all my orders")})),
        (modify_order, "modify_order", "Change items (replaces ALL), address, time or cake message of the customer's order before preparation starts. " + ITEM_HELP,
         obj({"code": S(), "items": ITEMS, "address": S(), "slot_start": S(), "cake_message": S(), "notes": S(),
              "payment_method": S(enum=METHODS)}, ("code",))),
        (cancel_order, "cancel_order", "Cancel the customer's order if allowed (not after preparation started).", obj({"code": S()}, ("code",))),
        (escalate_to_human, "escalate_to_human", "Hand the chat to staff with a summary; the bot then stays silent.",
         obj({"reason": S(), "summary": S("what was collected, so the customer is not asked again"), "priority": S(enum=["normal", "high"]),
              "order_code": S()}, ("reason",))),
    ]]
