"""Draft orders: validation and pricing. Deterministic Python only. The LLM collects details and calls tools; this module
decides what is valid, what is still missing and what everything costs.

A draft `state` is a plain dict (stored in the `drafts` table, one per phone number):
  items:  [{"sku", "variant", "qty", "eggless", "message"}  or  {"deal", "qty", "swap_cake", "message"}]
          message: None = not asked yet, "" = customer wants none, text = what to write on the cake
  fulfilment: "delivery" | "pickup";  address_text, area, zone, branch
  slot_start: ISO 1-hour slot start;  asap: bool
  payment_method: "cash" | "jazzcash" | "bank" | "counter";  customer_name
  gift: {"receiver_name", "receiver_phone"};  hide_price;  notes;  use_points;  birthday_discount
  version / summary_version / summary_turn: used by the confirm guard (see app.tools.confirm_order)
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from . import models as m
from . import repo, rules

PAYMENT_LABELS = {"cash": "Cash on delivery", "jazzcash": "JazzCash/Easypaisa", "bank": "Bank transfer", "counter": "Pay at the counter"}


def new_state() -> dict:
    return {"items": [], "fulfilment": None, "address_text": "", "area": None, "zone": None, "branch": None, "slot_start": None,
            "asap": False, "payment_method": None, "customer_name": None, "gift": None, "hide_price": False, "notes": "",
            "use_points": False, "birthday_discount": False, "version": 0, "summary_version": None, "summary_turn": None,
            "created_turn": None}


def _parse_dt(v) -> dt.datetime | None:
    if not v:
        return None
    if isinstance(v, dt.datetime):
        return v
    try:
        return dt.datetime.fromisoformat(str(v).replace(" ", "T"))
    except ValueError:
        return None


def norm_label(x: str) -> str:
    return re.sub(r"\s+", "", (x or "").lower()).replace("pounds", "lb").replace("pound", "lb").replace("lbs", "lb")


def pick_variant(product: m.Product, label: str | None):
    """Returns (variant, error). error: variant_needed | unknown_variant."""
    vs = product.variants
    if label:
        want = norm_label(label)
        for v in vs:
            if norm_label(v.label) == want:
                return v, None
        if want.isdigit():
            for v in vs:
                if norm_label(v.label) == want + "lb":
                    return v, None
        if len(vs) == 1 and want in ("each", "perlb", ""):
            return vs[0], None
        return None, "unknown_variant"
    return (vs[0], None) if len(vs) == 1 else (None, "variant_needed")


def variant_name(product: m.Product, v: m.Variant) -> str:
    return product.name if v.label in ("each", "per lb") else f"{product.name} ({v.label})"


def alternatives(s: Session, variant: m.Variant, qty: int = 1, limit: int = 3) -> list[dict]:
    """Closest in-stock alternatives: other sizes of the same item first, then similar items in the same category."""
    p = variant.product
    out = []
    for v in p.variants:
        if v.id != variant.id and (v.stock is None or v.stock >= qty):
            out.append({"sku": p.sku, "name": variant_name(p, v), "variant": v.label, "price": v.price})
    others = []
    for q in s.query(m.Product).filter(m.Product.category == p.category, m.Product.id != p.id).all():
        same = next((v for v in q.variants if v.label == variant.label), None) or (q.variants[0] if q.variants else None)
        if same is not None and (same.stock is None or same.stock >= qty) and not q.is_custom:
            others.append((abs(same.price - variant.price), {"sku": q.sku, "name": variant_name(q, same), "variant": same.label, "price": same.price}))
    others.sort(key=lambda t: t[0])
    return (out + [o for _, o in others])[:limit]


def _setting(s: Session, key: str, default=None):
    row = s.get(m.Setting, key)
    return row.value if row is not None else default


def resolve_branch(s: Session, text: str | None) -> m.Branch | None:
    t = rules.normalize(text or "")
    if not t:
        return None
    for b in s.query(m.Branch).all():
        n = rules.normalize(b.name)
        if t == n or t in n.split() or n.startswith(t) or (len(t) >= 3 and t in n):
            return b
    return None


def slot_text(start: dt.datetime, now: dt.datetime, minutes: int = 60) -> str:
    end = start + dt.timedelta(minutes=minutes)
    day = "Today" if start.date() == now.date() else "Tomorrow" if start.date() == now.date() + dt.timedelta(days=1) else start.strftime("%a %d %b")
    return f"{day}, {rules.fmt_minutes(rules.minutes(start))} to {rules.fmt_minutes(rules.minutes(end))}"


def _money(n: int) -> str:
    return f"Rs {n:,}"


def evaluate(s: Session, phone: str, state: dict, now: dt.datetime) -> dict:
    """Validate and price a draft. Returns a compact dict for the LLM and the confirm step:
    status: incomplete | has_problems | needs_human | ready_for_summary, plus missing[], problems[], totals, lines."""
    cfg = repo.load_config(s)
    areas = repo.areas_as_dicts(s)
    customer = s.query(m.Customer).filter(m.Customer.phone == phone).one_or_none()
    fulfilment = state.get("fulfilment")
    slot = _parse_dt(state.get("slot_start"))
    ref_day = slot.date() if slot else now.date()
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][ref_day.weekday()]
    problems: list[dict] = []
    missing: list[str] = []
    lines: list[dict] = []
    reserve: dict[int, int] = {}
    resolved: dict = {}
    kinds: set[str] = set()
    notice_h = 0
    boxes = 0
    has_custom = has_standard_cake = has_deal = False
    donuts: dict[str, dict] = {}

    def need(vid: int, q: int):
        reserve[vid] = reserve.get(vid, 0) + q

    def problem(code: str, **detail):
        problems.append({"code": code, **detail})

    items = state.get("items") or []
    if not items:
        missing.append("items")

    for it in items:
        qty = max(int(it.get("qty") or 1), 1)
        # ------------------------------------------------------------- deal (bundle) lines
        if it.get("deal"):
            deal = s.query(m.Deal).filter(m.Deal.name == it["deal"]).one_or_none()
            if deal is None or deal.kind != "bundle":
                problem("unknown_deal", deal=it.get("deal"))
                continue
            has_deal = True
            channel = {"delivery": "delivery", "pickup": "pickup"}.get(fulfilment or "")
            when = slot or now
            if not rules.deal_active({"days": deal.days, "start_min": deal.start_min, "end_min": deal.end_min, "channels": deal.channels,
                                      "valid_until": deal.valid_until}, when, channel):
                problem("deal_not_available", deal=deal.name, when=deal.when_text, where=deal.where_text)
            if qty < deal.min_qty:
                problem("deal_minimum", deal=deal.name, minimum=deal.min_qty)
            notice_h = max(notice_h, deal.notice_hours)
            comp_names = []
            line_reserve = []
            for c in deal.components:
                sku = c["sku"]
                swap = it.get("swap_cake")
                if swap and swap in c.get("swap", []):
                    sku = swap
                prod = s.query(m.Product).filter(m.Product.sku == sku).one()
                var = next((v for v in prod.variants if norm_label(v.label) == norm_label(c["variant"])), prod.variants[0])
                need(var.id, c["qty"] * qty)
                line_reserve.append([var.id, c["qty"] * qty])
                comp_names.append(f"{c['qty'] * qty} x {variant_name(prod, var)}")
                if prod.is_cake:
                    has_standard_cake = True
            if deal.name == "Office Hi-Tea Box":
                boxes += qty
            if it.get("message") is None and any(s.query(m.Product).filter(m.Product.sku == c["sku"]).one().is_cake for c in deal.components):
                missing.append(f"cake message for {deal.name} (up to {cfg.message_max_chars} characters, free; may be none)")
            elif it.get("message") and not rules.message_ok(it["message"], cfg):
                problem("message_too_long", maximum=cfg.message_max_chars)
            lines.append({"name": deal.name, "qty": qty, "unit_price": deal.price, "line_total": deal.price * qty, "sku": f"DEAL-{deal.id}",
                          "variant_id": None, "options": {"deal": deal.name, "includes": comp_names, "message": it.get("message") or "",
                                                           "reserve": line_reserve}})
            continue
        # ------------------------------------------------------------- normal product lines
        prod = s.query(m.Product).filter(m.Product.sku == (it.get("sku") or "")).one_or_none()
        if prod is None:
            problem("unknown_item", sku=it.get("sku"))
            continue
        var, err = pick_variant(prod, it.get("variant"))
        if var is None:
            problem(err, sku=prod.sku, item=prod.name, options=[{"variant": v.label, "price": v.price, "in_stock": v.stock is None or v.stock > 0}
                                                                  for v in prod.variants])
            continue
        if qty < prod.min_qty:
            problem("minimum_quantity", item=prod.name, minimum=prod.min_qty, unit=prod.unit)
        if prod.sold_days and weekday not in prod.sold_days:
            problem("not_sold_that_day", item=prod.name, sold_days=prod.sold_days, day=weekday)
        if fulfilment == "delivery" and not prod.delivery_ok:
            problem("not_deliverable", item=prod.name, hint="dine-in and takeaway only")
        unit = var.price
        eggless = bool(it.get("eggless"))
        surcharge = 0
        if eggless:
            if not prod.eggless_ok:
                problem("eggless_not_available", item=prod.name)
            else:
                surcharge = rules.eggless_surcharge(var.label, cfg) * qty
                notice_h = max(notice_h, cfg.eggless_notice_h)
        notice_h = max(notice_h, prod.notice_hours)
        if prod.is_custom:
            has_custom = True
            kinds.add("custom")
        elif prod.is_cake and not prod.made_to_order:
            has_standard_cake = True
        if prod.requires_human:
            kinds.add("custom")
        msg = it.get("message")
        if (prod.is_cake or prod.sku == "CU-THEME") and msg is None:
            missing.append(f"cake message for {prod.name} (up to {cfg.message_max_chars} characters, free; may be none)")
        elif msg and not rules.message_ok(msg, cfg):
            problem("message_too_long", item=prod.name, maximum=cfg.message_max_chars)
        if prod.sku in ("DS-DONG", "DS-DONN"):
            donuts[prod.sku] = {"prod": prod, "var": var, "qty": donuts.get(prod.sku, {}).get("qty", 0) + qty}
        if "box" in prod.name.lower() and "assorted" not in prod.name.lower():
            boxes += qty
        need(var.id, qty)
        lines.append({"name": variant_name(prod, var) + (" (eggless)" if eggless else ""), "qty": qty, "unit_price": unit,
                      "line_total": unit * qty + surcharge, "sku": prod.sku, "variant_id": var.id,
                      "options": {"eggless": eggless, "message": msg or "", "eggless_surcharge": surcharge, "reserve": [[var.id, qty]]}})

    # ------------------------------------------------------------- Wednesday Donut Day: buy 4, get 2 free (the cheaper ones)
    if donuts:
        deal = s.query(m.Deal).filter(m.Deal.name == "Wednesday Donut Day").one_or_none()
        channel = {"delivery": "delivery", "pickup": "pickup"}.get(fulfilment or "")
        if deal and rules.deal_active({"days": deal.days, "channels": deal.channels, "valid_until": deal.valid_until}, slot or now, channel):
            rule = deal.components[0]
            paid = sum(d["qty"] for d in donuts.values())
            free_units = rule["free"] * (paid // rule["buy"])
            if free_units:
                cheapest = min(donuts.values(), key=lambda d: d["var"].price)
                need(cheapest["var"].id, free_units)
                lines.append({"name": f"{cheapest['prod'].name} (free, Donut Day)", "qty": free_units, "unit_price": 0, "line_total": 0,
                              "sku": cheapest["prod"].sku, "variant_id": cheapest["var"].id,
                              "options": {"free": True, "reserve": [[cheapest["var"].id, free_units]]}})

    # ------------------------------------------------------------- stock (aggregated per variant)
    for vid, q in reserve.items():
        v = s.get(m.Variant, vid)
        if v.stock is not None and v.stock < q:
            problem("insufficient_stock", item=variant_name(v.product, v), sku=v.product.sku, variant=v.label, left=v.stock, wanted=q,
                    alternatives=alternatives(s, v, q))

    subtotal = sum(line["line_total"] for line in lines)

    # ------------------------------------------------------------- fulfilment, address, zone, minimum, fee
    fee = 0
    zone = None
    branch = None
    if fulfilment not in ("delivery", "pickup"):
        missing.append("fulfilment (delivery or pickup)")
    elif fulfilment == "delivery":
        text = (state.get("address_text") or "").strip()
        if not text:
            missing.append("delivery address (house/flat number, street, block or phase, and area)")
        else:
            hit = rules.resolve_area(f"{text} {state.get('area') or ''}", areas, tuple(_setting(s, "block_areas", [])))
            if hit.status == "served":
                zone = s.get(m.DeliveryZone, hit.zone_code)
                resolved.update(area=hit.area, zone=hit.zone_code)
                if not rules.meets_minimum(subtotal, cfg) and lines:
                    problem("below_minimum", minimum=cfg.min_order, subtotal=subtotal)
                fee = rules.delivery_fee(zone.fee, subtotal, cfg)
            elif hit.status == "not_served":
                problem("area_not_served", area=hit.area, hint="offer pickup from a branch, or Foodpanda")
            elif hit.status == "unclear":
                problem("area_unclear", ask_which_area=list(hit.candidates))
            else:
                problem("area_unknown", hint="ask which area of Karachi")
            if len(text) < 12 and hit.status == "served":
                missing.append("full address (house/flat number, street, block/phase) and a landmark if the address looks incomplete")
        gift = state.get("gift") or {}
        if gift and not (gift.get("receiver_name") and gift.get("receiver_phone")):
            missing.append("gift receiver's name and phone number")
    else:  # pickup
        branch = resolve_branch(s, state.get("branch"))
        if branch is None:
            missing.append("pickup branch (DHA Phase 6, Gulshan-e-Iqbal or Bahadurabad)")
        else:
            resolved["branch"] = branch.name

    # ------------------------------------------------------------- time
    eta = None
    if slot is not None:
        chk = rules.check_slot(slot, now, notice_h, has_standard_cake, delivery=(fulfilment == "delivery"), cfg=cfg)
        if not chk.ok:
            problem("time_not_possible", reason=chk.code, detail=chk.detail,
                    earliest=chk.earliest.isoformat(timespec="minutes") if chk.earliest else None)
        elif fulfilment == "pickup" and branch is not None and not rules.is_open(repo.branch_hours(s, branch.name), slot, cfg):
            problem("branch_closed_then", branch=branch.name)
    elif state.get("asap"):
        if notice_h > 0:
            earliest = rules.ceil_to_slot(now + dt.timedelta(hours=notice_h), cfg)
            problem("time_not_possible", reason="notice", detail=f"needs {notice_h} hours' notice", earliest=earliest.isoformat(timespec="minutes"))
        elif has_standard_cake and rules.minutes(now) >= cfg.same_day_cake_cutoff:
            problem("time_not_possible", reason="same_day_cutoff", detail="same-day cakes must be ordered before 9 pm")
        elif fulfilment == "delivery" and zone is not None:
            eta = rules.asap_eta(now, zone.max_minutes, cfg)
        elif fulfilment == "pickup":
            eta = now + dt.timedelta(minutes=30)  # assumption D8: ASAP pickup is ready in about 30 minutes
    else:
        missing.append("when they want it (now/ASAP or a date and time slot)")

    # ------------------------------------------------------------- customer, payment, discounts
    name = state.get("customer_name") or (customer.name if customer and customer.name else None)
    if not name:
        missing.append("customer name")
    discount = 0
    discount_note = ""
    if state.get("use_points"):
        pts = customer.points if customer else 0
        red = rules.redeemable(pts, subtotal, cfg)
        if red["rs_off"] == 0:
            problem("no_points_to_use", points=pts, needed=cfg.redeem_block_points)
        else:
            discount, discount_note = red["rs_off"], f"Crumbs Club: {red['points_used']} points"
    if state.get("birthday_discount"):
        cake = next((ln for ln in lines if ln["sku"].startswith("CK-")), None)
        bd = rules.birthday_discount(customer.birthday_month if customer else None, now, cake["unit_price"] if cake else 0, cfg)
        if state.get("use_points") or has_deal:
            problem("discount_no_stacking", hint="the birthday 10% cannot be combined with deals or Crumbs Club points")
        elif bd == 0:
            problem("birthday_discount_not_available", hint="needs a cake and a birthday this month on the customer's profile")
        else:
            discount, discount_note = bd, f"Birthday month: {cfg.birthday_pct}% off one cake"
    total = max(subtotal + fee - discount, 0)
    adv_needed, adv_amount = rules.advance_required(total, has_custom, cfg)
    method = state.get("payment_method")
    if method is None:
        missing.append("payment method (cash on delivery, JazzCash/Easypaisa or bank transfer)")
    elif method not in PAYMENT_LABELS:
        problem("payment_method_invalid", allowed=["cash", "jazzcash", "bank"] + (["counter"] if fulfilment == "pickup" else []))
    elif method == "counter" and fulfilment == "delivery":
        problem("payment_method_invalid", hint="card/counter payment is not available on delivery")
    elif adv_needed and method in ("cash", "counter"):
        problem("advance_needs_online_payment", advance_due=adv_amount, hint="the 50% advance is paid by JazzCash/Easypaisa or bank transfer; the rest on delivery")

    human = rules.human_routing_reasons(subtotal, boxes, frozenset(kinds), cfg)
    if problems:
        status = "has_problems"
    elif human:
        status = "needs_human"
    elif missing:
        status = "incomplete"
    else:
        status = "ready_for_summary"
    view = {"status": status, "missing": missing, "problems": problems, "human_reasons": human,
            "lines": [{k: ln[k] for k in ("name", "qty", "unit_price", "line_total")} | ({"message": ln["options"]["message"]} if ln["options"].get("message") else {})
                      for ln in lines],
            "subtotal": subtotal, "delivery_fee": fee, "discount": discount, "discount_note": discount_note, "total": total,
            "advance_due": adv_amount, "fulfilment": fulfilment, "address": state.get("address_text") or None,
            "area": resolved.get("area"), "zone": resolved.get("zone"), "branch": resolved.get("branch"),
            "slot": slot_text(slot, now) if slot else ("ASAP, around " + eta.strftime("%I:%M %p").lstrip("0") if eta else None),
            "payment_method": PAYMENT_LABELS.get(method), "customer_name": name, "is_gift": bool(state.get("gift"))}
    view["_internal"] = {"lines": lines, "reserve": reserve, "resolved": resolved, "eta": eta, "slot": slot, "has_custom": has_custom,
                         "advance_amount": adv_amount if adv_needed else 0, "fee": fee, "discount": discount, "total": total, "subtotal": subtotal,
                         "name": name, "branch_id": branch.id if branch else None, "zone": resolved.get("zone"), "points_used": (
                             rules.redeemable(customer.points if customer else 0, subtotal, cfg)["points_used"] if state.get("use_points") and discount else 0)}
    if status == "ready_for_summary":
        view["summary_text"] = render_summary(view, state)
    return view


def render_summary(view: dict, state: dict) -> str:
    """English summary in the spec's format; the LLM re-words it in the customer's language but keeps every number."""
    out = ["Please check your order:"]
    for ln in view["lines"]:
        price = "" if state.get("hide_price") else f" - {_money(ln['line_total'])}"
        out.append(f"- {ln['qty']} x {ln['name']}{price}")
        if ln.get("message"):
            out.append(f'  Message: "{ln["message"]}"')
    if not state.get("hide_price"):
        out.append(f"Subtotal: {_money(view['subtotal'])}")
        if view["fulfilment"] == "delivery":
            out.append(f"Delivery (Zone {view['zone']}): {_money(view['delivery_fee'])}" if view["delivery_fee"] else f"Delivery (Zone {view['zone']}): free")
        if view["discount"]:
            out.append(f"Discount ({view['discount_note']}): -{_money(view['discount'])}")
        out.append(f"*Total: {_money(view['total'])}* - {view['payment_method']}")
        if view["advance_due"]:
            out.append(f"Advance to pay now: {_money(view['advance_due'])}, balance {_money(view['total'] - view['advance_due'])} on delivery")
    if view["fulfilment"] == "delivery":
        out.append(f"Address: {view['address']}")
        if view["is_gift"]:
            g = state.get("gift") or {}
            out.append(f"For: {g.get('receiver_name')} ({g.get('receiver_phone')})")
    else:
        out.append(f"Pickup: {view['branch']} branch")
    out.append(f"When: {view['slot']}")
    out.append("Reply YES to confirm, or tell me what to change.")
    return "\n".join(out)
