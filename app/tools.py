"""Agent tools. Guardrails live HERE (server side) so they hold no matter which LLM drives them:
- create_order only works on a proposal made in an EARLIER customer turn (no confirmation -> no order)
- an item with several variations (size / flavour) cannot be ordered until one is chosen
- ready times respect each item's lead time and the shop's opening hours; delivery addresses must be in a served area
- orders that need payment stay 'awaiting_payment' until a HUMAN team member verifies it (the AI can only submit proof)
- payment details come only from the shop config; unpaid reservations expire and release their stock
- stock is deducted atomically per variation; customers only see/change/cancel their own orders; quantities are capped."""
import datetime as dt
import json
import math
import time

from . import db
from .shop import PAYMENT, SHOP, STATUS_TEXT

MAX_QTY = 50
FMT = "%Y-%m-%d %H:%M"
CHANGEABLE = ("awaiting_payment", "payment_submitted", "confirmed")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5)  # Pakistan Standard Time


NOW = _now  # tests can replace this


def _hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def pretty(d: dt.datetime) -> str:
    return d.strftime("%A %d %b, %I:%M %p").replace(" 0", " ")


def clamp_to_hours(t: dt.datetime) -> dt.datetime:
    o, c = _hm(SHOP["open"]), _hm(SHOP["close"])
    if t.time() > c:
        return dt.datetime.combine(t.date() + dt.timedelta(days=1), o)
    if t.time() < o:
        return dt.datetime.combine(t.date(), o)
    return t


def earliest_ready(now: dt.datetime, lead_min: int, delivery: bool) -> dt.datetime:
    t = now + dt.timedelta(minutes=lead_min + (SHOP["delivery_extra_minutes"] if delivery else 0))
    return clamp_to_hours(t.replace(second=0, microsecond=0))


def _area_ok(address: str) -> bool:
    a = (address or "").lower()
    return any(k in a for k in SHOP["delivery_area_keys"])


ITEM_SCHEMA = {"type": "object", "properties": {
    "product": {"type": "string", "description": "product name, e.g. 'Pineapple Cake'"},
    "variation": {"type": "string", "description": "chosen size or flavour, e.g. '2 lb', 'Walnut', 'Chicken Cheese'"},
    "qty": {"type": "integer", "minimum": 1}}, "required": ["product", "qty"]}

TOOL_SCHEMAS = [
    {"name": "get_product_details", "description": "Ingredients, allergens, storage, description and all variations (size/flavour) with prices and availability of one product.",
     "input_schema": {"type": "object", "properties": {"product": {"type": "string"}}, "required": ["product"]}},
    {"name": "check_stock", "description": "Live availability and price of a product (optionally one variation).",
     "input_schema": {"type": "object", "properties": {"product": {"type": "string"}, "variation": {"type": "string"}}, "required": ["product"]}},
    {"name": "save_customer_info", "description": "Save the customer's name and/or delivery address once they tell you.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "address": {"type": "string"}}}},
    {"name": "propose_order", "description": "Prepare an order and get totals, ready time and what must be paid now. Does NOT place it: read it back and wait for the "
                                              "customer's yes in their NEXT message. Errors tell you what is missing (variation_needed, address_needed, area_unclear, too_soon, outside_hours...).",
     "input_schema": {"type": "object", "properties": {
         "items": {"type": "array", "items": ITEM_SCHEMA, "minItems": 1},
         "delivery": {"type": "string", "enum": ["pickup", "delivery"]},
         "address": {"type": "string", "description": "Full address incl. area; required for delivery (omit to use the saved one)."},
         "ready_at": {"type": "string", "description": "When the customer wants it, Pakistan time, YYYY-MM-DD HH:MM. Omit for the earliest possible."},
         "payment": {"type": "string", "enum": ["cash", "online"], "description": "cash = pay on delivery/pickup; online = pay now. Large orders need an online advance regardless."},
         "note": {"type": "string", "description": "Exact text to write on a cake, allergies, other requests"}},
         "required": ["items"]}},
    {"name": "remember_product", "description": "Remember which product the customer wants while you ask them for details.",
     "input_schema": {"type": "object", "properties": {"product": {"type": "string"}, "qty": {"type": "integer"}}, "required": ["product"]}},
    {"name": "create_order", "description": "Place the pending order. Only call when the customer's latest message is an explicit yes. If payment is due, the result contains the payment details to send.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cancel_pending", "description": "Discard the pending (not yet placed) order when the customer declines or starts over.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_payment_details", "description": "Amount to pay and all payment methods/accounts for the customer's order that is waiting for payment (use when they ask again).",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}}},
    {"name": "submit_payment", "description": "Record that the customer says they paid (transaction ID or description). Moves the order to 'payment_submitted' for a team member to verify. It does NOT confirm the order.",
     "input_schema": {"type": "object", "properties": {"reference": {"type": "string", "description": "transaction ID or what the customer said"},
                                                       "method": {"type": "string"}, "amount": {"type": "integer"}, "order_id": {"type": "integer"}},
                      "required": ["reference"]}},
    {"name": "order_status", "description": "Where an order is: status, payment state, ready time, delivery details and the team's contact number. Latest order if no id.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}}},
    {"name": "update_order", "description": "Change the delivery address, ready time or note of a placed order (only before baking starts).",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "address": {"type": "string"},
                                                       "ready_at": {"type": "string"}, "note": {"type": "string"}}}},
    {"name": "cancel_order", "description": "Cancel one of the customer's PLACED orders (before baking starts). If money was paid, the team handles it.",
     "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]}},
    {"name": "escalate", "description": "Hand the chat to the team: complaints, refunds, health or severe allergy questions, bulk/custom requests, anything unclear or risky.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]

TEAM = {"team_phone": SHOP["team_phone"], "team_phone_hours": SHOP["team_phone_hours"]}


def run_tool(customer: str, name: str, args: dict) -> dict:
    c = db.conn()
    try:
        _expire_unpaid(c)
        s = db.get_session(c, customer)
        out = _dispatch(c, s, customer, name, args or {})
        db.save_session(c, customer, s)
        c.commit()
        return out
    finally:
        c.close()


def _line(i):
    return f"{i['qty']} x {i['name']} ({i['variation']})" if i.get("variation") else f"{i['qty']} x {i['name']}"


def _summary(items):
    return ", ".join(_line(i) for i in items)


def _normalize_items(a):
    items = a.get("items")
    if not items and a.get("product"):  # legacy single-item call (rule brain)
        items = [{"product": a["product"], "variation": a.get("variation"), "qty": a.get("qty")}]
    return items or []


def _opts(vs):
    return [{"name": v["name"], "price": v["price"], "note": v["note"], "in_stock": v["stock"] > 0} for v in vs]


def _items_of(r):
    try:
        return json.loads(r["items"]) if r["items"] else []
    except Exception:
        return []


def _restore_stock(c, r):
    for it in _items_of(r):
        if it.get("variation_id"):
            c.execute("UPDATE variations SET stock=stock+? WHERE id=?", (it["qty"], it["variation_id"]))


def _expire_unpaid(c):
    """Release reservations whose payment window passed (stock goes back on sale)."""
    for r in c.execute("SELECT * FROM orders WHERE status='awaiting_payment' AND pay_by IS NOT NULL AND pay_by < ?", (time.time(),)).fetchall():
        _restore_stock(c, r)
        c.execute("UPDATE orders SET status='cancelled', payment_note='expired', updated=? WHERE id=?", (time.time(), r["id"]))


def payment_block(amount):
    return {"pay_amount": amount, "methods": PAYMENT["methods"], "hold_minutes": PAYMENT["hold_minutes"],
            "verify_time": PAYMENT["verify_minutes"], "how_to_pay": PAYMENT["how"]}


def _dispatch(c, s, customer, name, a):
    if name == "get_product_details":
        p = db.find_product(c, a.get("product", ""))
        if not p:
            return {"found": False, "hint": "no such product; offer the menu"}
        return {"found": True, "name": p["name"], "category": p["category"], "description": p["description"],
                "ingredients": p["ingredients"], "allergens": p["allergens"], "storage": p["storage"],
                "variations": _opts(db.list_variations(c, p["id"]))}

    if name == "check_stock":
        p = db.find_product(c, a.get("product", ""))
        if not p:
            return {"found": False}
        vs = db.list_variations(c, p["id"])
        if a.get("variation"):
            v = db.find_variation(c, p["id"], a["variation"])
            if v:
                return {"found": True, "name": p["name"], "variation": v["name"], "price": v["price"], "stock": v["stock"],
                        "in_stock": v["stock"] > 0}
        return {"found": True, "name": p["name"], "variations": _opts(vs), "in_stock": any(v["stock"] > 0 for v in vs)}

    if name == "save_customer_info":
        db.save_customer(c, customer, a.get("name"), a.get("address"))
        return {"ok": True}

    if name == "propose_order":
        raw = _normalize_items(a)
        if not raw:
            return {"ok": False, "error": "no_items"}
        items, subtotal, lead = [], 0, 0
        for it in raw[:10]:
            p = db.find_product(c, it.get("product", ""))
            if not p:
                return {"ok": False, "error": "unknown_product", "product": it.get("product")}
            try:
                qty = int(it.get("qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            v = db.find_variation(c, p["id"], it.get("variation") or "") or db.find_variation(c, p["id"], it.get("product") or "")
            if not v:
                return {"ok": False, "error": "variation_needed", "product": p["name"], "options": _opts(db.list_variations(c, p["id"]))}
            if qty < 1 or qty > MAX_QTY:
                return {"ok": False, "error": "bad_quantity", "max": MAX_QTY, "hint": "bigger quantities need a team quote: escalate"}
            if v["stock"] < qty:
                return {"ok": False, "error": "insufficient_stock", "name": p["name"], "variation": v["name"], "stock": v["stock"]}
            items.append({"product_id": p["id"], "variation_id": v["id"], "name": p["name"], "variation": v["name"], "qty": qty,
                          "price": v["price"]})
            subtotal += v["price"] * qty
            lead = max(lead, v["lead_min"])
        delivery = a.get("delivery") or "pickup"
        address = (a.get("address") or "").strip()
        if delivery == "delivery":
            address = address or (db.get_customer(c, customer).get("address") or "")
            if not address:
                return {"ok": False, "error": "address_needed", "hint": "ask for the full delivery address with area",
                        "delivery_areas": SHOP["delivery_areas"]}
            if not _area_ok(address):
                return {"ok": False, "error": "area_unclear", "delivery_areas": SHOP["delivery_areas"],
                        "hint": "ask which area this is; if it is outside the delivery areas offer pickup, or escalate so the team can confirm"}
        else:
            address = ""
        now = NOW()
        earliest = earliest_ready(now, lead, delivery == "delivery")
        requested = (a.get("ready_at") or "").strip()
        auto = not requested
        if requested:
            try:
                ready = dt.datetime.strptime(requested, FMT)
            except ValueError:
                return {"ok": False, "error": "bad_datetime", "hint": "use YYYY-MM-DD HH:MM in Pakistan time"}
            if ready < earliest - dt.timedelta(minutes=1):
                return {"ok": False, "error": "too_soon", "earliest": earliest.strftime(FMT), "earliest_text": pretty(earliest),
                        "lead_minutes": lead, "hint": "this order needs more preparation time; offer the earliest time or a later one"}
            if not (_hm(SHOP["open"]) <= ready.time() <= _hm(SHOP["close"])):
                return {"ok": False, "error": "outside_hours", "hours": SHOP["hours_text"]}
        else:
            ready = earliest
        fee = SHOP["delivery_fee"] if delivery == "delivery" and subtotal < SHOP["free_delivery_over"] else 0
        total = subtotal + fee
        advance_required = total >= PAYMENT["advance_required_from"]
        wants_online = (a.get("payment") or "cash") == "online"
        mode = "online" if wants_online else ("advance" if advance_required else "cod")
        due_now = math.ceil(total * PAYMENT["advance_percent"] / 100) if mode == "advance" else (total if mode == "online" else 0)
        new = {"items": items, "subtotal": subtotal, "fee": fee, "total": total, "delivery": delivery, "address": address,
               "note": (a.get("note") or "").strip(), "ready_at": ready.strftime(FMT), "auto_time": auto,
               "payment_mode": mode, "due_now": due_now, "advance_required": advance_required}
        # duplicate guard: an identical active order from this customer in the last hour (e.g. a double "yes") must not be placed twice
        q = "SELECT id FROM orders WHERE customer=? AND status IN ('awaiting_payment','payment_submitted','confirmed') AND items=? AND created>?"
        qp = [customer, json.dumps(items), time.time() - 3600]
        if not auto:
            q += " AND ready_at=?"
            qp.append(ready.strftime(FMT))
        dup = c.execute(q + " ORDER BY id DESC LIMIT 1", qp).fetchone()
        if dup:
            return {"ok": False, "error": "duplicate_order", "order_id": dup["id"],
                    "hint": "an identical order was just placed; tell the customer it is already placed (give the order number) and do not place another"}
        cur = s["pending"]
        if cur and "items" in cur:
            skip = ("ready_at", "auto_time")
            same = {k: cur.get(k) for k in new if k not in skip} == {k: new[k] for k in new if k not in skip}
            if same and (auto or cur.get("ready_at") == new["ready_at"]):
                return {"ok": True, "awaiting_confirmation": True, **_view(cur)}  # identical proposal again: keep its original turn
        new["turn"] = s.get("turn", 0)
        s["pending"] = new
        return {"ok": True, "awaiting_confirmation": True, **_view(new)}

    if name == "remember_product":
        if a.get("qty"):
            s["pending"] = {"awaiting_variation": {"product": a.get("product", ""), "qty": int(a["qty"])}}
        else:
            s["pending"] = {"awaiting_qty": a.get("product", "")}
        return {"ok": True}

    if name == "create_order":
        pend = s["pending"]
        if not pend or "items" not in pend:
            return {"ok": False, "error": "no_pending_order"}
        if pend.get("turn") == s.get("turn", 0):
            return {"ok": False, "error": "customer_has_not_confirmed_yet",
                    "hint": "read the order back with the total and ask for yes; place it only after their next message"}
        for it in pend["items"]:  # atomic: only deduct if enough stock is still there (safe under concurrent customers)
            got = c.execute("UPDATE variations SET stock=stock-? WHERE id=? AND stock>=?", (it["qty"], it["variation_id"], it["qty"]))
            if got.rowcount != 1:
                c.raw.rollback()  # undo deductions already made for earlier items in this order
                s["pending"] = None
                return {"ok": False, "error": "insufficient_stock", "name": it["name"], "variation": it["variation"]}
        first = pend["items"][0]
        due = int(pend.get("due_now") or 0)
        status = "awaiting_payment" if due > 0 else "confirmed"
        oid = c.insert_id(
            "INSERT INTO orders(customer,product_id,qty,total,status,created,items,delivery,address,note,fee,ready_at,updated,"
            "payment_mode,due_now,paid_amount,pay_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (customer, first["product_id"], first["qty"], pend["total"], status, time.time(), json.dumps(pend["items"]),
             pend["delivery"], pend["address"], pend["note"], pend["fee"], pend["ready_at"], time.time(),
             pend.get("payment_mode", "cod"), due, 0, (time.time() + PAYMENT["hold_minutes"] * 60) if due > 0 else None))
        s["pending"] = None
        out = {"ok": True, "order_id": oid, "status": status, "summary": _summary(pend["items"]), "name": first["name"], "qty": first["qty"],
               "total": pend["total"], "delivery": pend["delivery"],
               "ready_at_text": pretty(dt.datetime.strptime(pend["ready_at"], FMT)), **TEAM}
        if due > 0:
            out.update(payment_required=True, **payment_block(due), balance_on_delivery=pend["total"] - due,
                       note="NOT confirmed yet: reserved until payment is verified by the team")
        else:
            out.update(payment_required=False, payment="cash on delivery / pickup")
        return out

    if name == "cancel_pending":
        had = s["pending"] is not None
        s["pending"] = None
        return {"ok": True, "had_pending": had}

    if name in ("get_payment_details", "submit_payment", "order_status", "update_order", "cancel_order"):
        return _order_tools(c, customer, name, a)

    if name == "escalate":
        c.execute("INSERT INTO escalations(customer,reason,ts) VALUES(?,?,?)", (customer, a.get("reason", ""), time.time()))
        s["pending"] = None
        return {"ok": True, "handed_off": True, **TEAM}

    return {"ok": False, "error": "unknown_tool"}


def _find_order(c, customer, oid, statuses=None):
    if oid:
        return c.execute("SELECT * FROM orders WHERE id=? AND customer=?", (oid, customer)).fetchone()  # own orders only
    q, p = "SELECT * FROM orders WHERE customer=?", [customer]
    if statuses:
        q += " AND status IN (%s)" % ",".join("?" * len(statuses))
        p += list(statuses)
    return c.execute(q + " ORDER BY id DESC LIMIT 1", p).fetchone()


def _order_tools(c, customer, name, a):
    if name == "get_payment_details":
        r = _find_order(c, customer, a.get("order_id"), ("awaiting_payment",))
        if not r or r["status"] != "awaiting_payment":
            return {"ok": False, "error": "no_order_awaiting_payment", **TEAM}
        return {"ok": True, "order_id": r["id"], **payment_block(r["due_now"]), "balance_on_delivery": r["total"] - r["due_now"]}

    if name == "submit_payment":
        r = _find_order(c, customer, a.get("order_id"), ("awaiting_payment", "payment_submitted"))
        if not r or r["status"] not in ("awaiting_payment", "payment_submitted"):
            if r and r["status"] in ("confirmed", "preparing", "ready", "out_for_delivery", "delivered"):
                return {"ok": False, "error": "already_confirmed", "order_id": r["id"], **TEAM}
            return {"ok": False, "error": "no_order_awaiting_payment", **TEAM}
        ref = (a.get("reference") or "").strip()[:200]
        note = " | ".join(x for x in [a.get("method"), f"Rs {a['amount']}" if a.get("amount") else None] if x)
        c.execute("UPDATE orders SET status='payment_submitted', payment_ref=?, payment_note=?, updated=? WHERE id=?",
                  (ref, note, time.time(), r["id"]))
        return {"ok": True, "order_id": r["id"], "status": "payment_submitted", "amount_expected": r["due_now"],
                "verify_time": PAYMENT["verify_minutes"], "note": "NOT confirmed yet: a team member must verify the payment", **TEAM}

    if name == "order_status":
        r = _find_order(c, customer, a.get("order_id"))
        if not r:
            return {"found": False, "hint": "no order under this WhatsApp number; ask for the order number, or give the team contact", **TEAM}
        return _order_view(r)

    if name == "update_order":
        r = _find_order(c, customer, a.get("order_id"))
        if not r:
            return {"ok": False, "error": "order_not_found", **TEAM}
        if r["status"] not in CHANGEABLE:
            return {"ok": False, "error": "too_late_to_change", "hint": "baking has started; ask them to call the team", **TEAM}
        if a.get("address"):
            if r["delivery"] != "delivery":
                return {"ok": False, "error": "order_is_pickup", "hint": "this is a pickup order"}
            if not _area_ok(a["address"]):
                return {"ok": False, "error": "area_unclear", "delivery_areas": SHOP["delivery_areas"]}
            c.execute("UPDATE orders SET address=? WHERE id=?", (a["address"].strip(), r["id"]))
        if a.get("ready_at"):
            try:
                ready = dt.datetime.strptime(a["ready_at"].strip(), FMT)
            except ValueError:
                return {"ok": False, "error": "bad_datetime", "hint": "use YYYY-MM-DD HH:MM"}
            lead = 0
            for it in _items_of(r):
                v = c.execute("SELECT lead_min FROM variations WHERE id=?", (it.get("variation_id"),)).fetchone()
                lead = max(lead, v[0] if v else 0)
            earliest = earliest_ready(NOW(), lead, r["delivery"] == "delivery")
            if ready < earliest - dt.timedelta(minutes=1):
                return {"ok": False, "error": "too_soon", "earliest_text": pretty(earliest), "hint": "offer the earliest time or a later one"}
            if not (_hm(SHOP["open"]) <= ready.time() <= _hm(SHOP["close"])):
                return {"ok": False, "error": "outside_hours", "hours": SHOP["hours_text"]}
            c.execute("UPDATE orders SET ready_at=? WHERE id=?", (ready.strftime(FMT), r["id"]))
        if a.get("note"):
            c.execute("UPDATE orders SET note=? WHERE id=?", (a["note"].strip(), r["id"]))
        c.execute("UPDATE orders SET updated=? WHERE id=?", (time.time(), r["id"]))
        return {"ok": True, **_order_view(c.execute("SELECT * FROM orders WHERE id=?", (r["id"],)).fetchone())}

    if name == "cancel_order":
        r = c.execute("SELECT * FROM orders WHERE id=? AND customer=?", (a.get("order_id"), customer)).fetchone()
        if not r:
            return {"ok": False, "error": "order_not_found", **TEAM}
        if r["status"] not in CHANGEABLE:
            return {"ok": False, "error": f"cannot_cancel_status_{r['status']}", "hint": "baking has started; ask them to call the team", **TEAM}
        if (r["paid_amount"] or 0) > 0 or r["status"] == "payment_submitted":
            c.execute("INSERT INTO escalations(customer,reason,ts) VALUES(?,?,?)",
                      (customer, f"Cancellation requested for order #{r['id']} with payment involved (refund needed)", time.time()))
            return {"ok": False, "error": "payment_involved", "hint": "the team cancels and refunds personally; they were notified", **TEAM}
        _restore_stock(c, r)
        c.execute("UPDATE orders SET status='cancelled', updated=? WHERE id=?", (time.time(), r["id"]))
        return {"ok": True, "order_id": r["id"], "status": "cancelled"}


# ------------------------------------------------------------------ team (human) actions: used by the dashboard API
def _team_order(c, order_id):
    return c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def team_confirm_payment(order_id: int, amount=None) -> dict:
    """A team member has seen the money arrive. Only this (never the AI) moves an order to 'confirmed'."""
    c = db.conn()
    try:
        r = _team_order(c, order_id)
        if not r:
            return {"ok": False, "error": "order_not_found"}
        if r["status"] not in ("awaiting_payment", "payment_submitted"):
            return {"ok": False, "error": f"order_is_{r['status']}"}
        paid = int(amount) if amount else int(r["due_now"] or 0)
        c.execute("UPDATE orders SET status='confirmed', paid_amount=?, payment_note=?, updated=?, pay_by=NULL WHERE id=?",
                  (paid, "verified by team", time.time(), order_id))
        c.commit()
        return {"ok": True, "customer": r["customer"], "order": _order_view(_team_order(c, order_id))}
    finally:
        c.close()


def team_reject_payment(order_id: int, reason: str = "") -> dict:
    """Payment could not be verified (wrong amount, no money seen): the customer is asked to re-send, the hold is extended."""
    c = db.conn()
    try:
        r = _team_order(c, order_id)
        if not r:
            return {"ok": False, "error": "order_not_found"}
        if r["status"] not in ("awaiting_payment", "payment_submitted"):
            return {"ok": False, "error": f"order_is_{r['status']}"}
        c.execute("UPDATE orders SET status='awaiting_payment', payment_note=?, updated=?, pay_by=? WHERE id=?",
                  (f"rejected: {reason}"[:200], time.time(), time.time() + PAYMENT["hold_minutes"] * 60, order_id))
        c.commit()
        return {"ok": True, "customer": r["customer"], "reason": reason, "order": _order_view(_team_order(c, order_id))}
    finally:
        c.close()


def _view(p):
    """What the model/brain sees of a pending proposal (no internal ids)."""
    first = p["items"][0]
    mode = p.get("payment_mode", "cod")
    due = p.get("due_now", 0)
    return {"summary": _summary(p["items"]), "name": f"{first['name']} ({first['variation']})", "qty": first["qty"],
            "subtotal": p["subtotal"], "fee": p["fee"], "total": p["total"], "delivery": p["delivery"], "address": p["address"],
            "note": p["note"], "ready_at_text": pretty(dt.datetime.strptime(p["ready_at"], FMT)),
            "time_was_chosen_by_customer": not p.get("auto_time", False),
            "delivery_fee_note": ("" if p["delivery"] != "delivery" else
                                  (f"delivery fee Rs {p['fee']} (free for orders of Rs {SHOP['free_delivery_over']} or more)" if p["fee"]
                                   else f"delivery is free because the order is Rs {SHOP['free_delivery_over']} or more")),
            "payment_mode": mode, "pay_now": due, "balance_on_delivery_or_pickup": p["total"] - due if mode != "online" else 0,
            "advance_required": p.get("advance_required", False)}


def _order_view(r):
    items = _items_of(r)
    first = items[0] if items else {"name": "item", "variation": "", "qty": r["qty"]}
    ready = None
    if r["ready_at"]:
        try:
            ready = dt.datetime.strptime(r["ready_at"], FMT)
        except ValueError:
            ready = None
    status = r["status"]
    text = STATUS_TEXT.get(status, status)
    if status == "cancelled" and r["payment_note"] == "expired":
        text = "This order was cancelled because the payment was not received within the reservation time."
    paid = int(r["paid_amount"] or 0)
    out = {"found": True, "order_id": r["id"], "summary": _summary(items) if items else f"{r['qty']} x item",
           "name": f"{first['name']} ({first['variation']})" if first.get("variation") else first["name"], "qty": first["qty"],
           "total": r["total"], "status": status, "status_text": text,
           "delivery": r["delivery"] or "pickup", "address": r["address"] or "", "note": r["note"] or "",
           "payment_mode": r["payment_mode"] or "cod", "amount_paid_verified": paid, "balance_due_on_delivery_or_pickup": max(r["total"] - paid, 0),
           "now_text": pretty(NOW()), **TEAM}
    if status == "awaiting_payment":
        out.update(amount_to_pay_now=int(r["due_now"] or 0), methods=PAYMENT["methods"], how_to_pay=PAYMENT["how"])
        if r["payment_note"] and str(r["payment_note"]).startswith("rejected"):
            out["last_payment_check"] = r["payment_note"]
        if r["pay_by"]:
            out["reservation_until_text"] = pretty(dt.datetime.fromtimestamp(r["pay_by"], dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5))
    if ready:
        out["ready_at_text"] = pretty(ready)
        out["is_late"] = status in ("confirmed", "preparing") and NOW() > ready + dt.timedelta(minutes=15)
    if status == "ready" and r["delivery"] == "pickup":
        out["pickup_address"] = SHOP["address"]
    return out
