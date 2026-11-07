"""Hard, deterministic checks on facts and state. They never decide what a customer meant (the agent does that);
they only verify numbers, tool use and what is in the database. Each check returns None (ok) or a failure message."""
from __future__ import annotations

import re

from app import models as m
from app.database import session_scope

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def digits(text: str) -> str:
    """Text with Urdu digits turned into ASCII and thousands separators removed, for number checks."""
    return re.sub(r"(?<=\d)[,٬،](?=\d)", "", text.translate(_DIGITS))


def says_number(*numbers, turn: int = -1):
    def check(run):
        text = digits(run.turns[turn].reply)
        if not all(str(n) in text for n in numbers):
            return f"reply should contain {numbers} but says: {run.turns[turn].reply[:140]!r}"
    return check


def used_tool(*names):
    def check(run):
        if not set(names) & set(run.tool_names()):
            return f"expected one of the tools {names}, used {run.tool_names() or 'none'}"
    return check


def never_used(*names):
    def check(run):
        bad = set(names) & set(run.tool_names())
        if bad:
            return f"must not use {sorted(bad)}"
    return check


def not_says(*phrases):
    def check(run):
        text = " ".join(run.replies).lower()
        hit = [p for p in phrases if p.lower() in text]
        if hit:
            return f"reply must not contain {hit}"
    return check


def escalated():
    def check(run):
        with session_scope() as s:
            if not s.query(m.Escalation).filter(m.Escalation.phone == run.phone).count():
                return "expected a hand-off to staff, none was created"
    return check


def not_escalated():
    def check(run):
        with session_scope() as s:
            if s.query(m.Escalation).filter(m.Escalation.phone == run.phone).count():
                return "no hand-off to staff was needed here"
    return check


def no_order_placed():
    def check(run):
        if run.placed_after and run.placed_after[-1] > 0:
            return "an order was placed but the customer never confirmed"
    return check


def order_placed():
    def check(run):
        if not run.placed_after or run.placed_after[-1] < 1:
            return "expected an order to be placed after the customer said yes"
    return check


def not_placed_before(yes_turn: int):
    """No order may exist before the turn (0-based) in which the customer first says yes."""
    def check(run):
        if any(n > 0 for n in run.placed_after[:yes_turn]):
            return f"an order was placed before the customer's yes at turn {yes_turn + 1} (orders after each turn: {run.placed_after})"
    return check


def order_status(code: str, expected: str):
    def check(run):
        with session_scope() as s:
            o = s.query(m.Order).filter(m.Order.code == code).one()
            if o.status != expected:
                return f"{code} should still be {expected}, is {o.status}"
    return check


def draft_fee(expected: int):
    def check(run):
        fees = [d["delivery_fee"] for d in run.draft_after if d and "delivery_fee" in d]
        if fees and fees[-1] != expected:
            return f"delivery fee should be Rs {expected}, draft says {fees[-1]}"
    return check


def draft_discount_zero():
    def check(run):
        if any(d and d.get("discount") for d in run.draft_after):
            return "a discount was applied to the draft"
    return check


def prices_unchanged():
    def check(run):
        with session_scope() as s:
            cheap = s.query(m.Variant).filter(m.Variant.price < 20).count()
            if cheap:
                return "some price was changed to under Rs 20"
    return check


def urdu_script(min_ratio: float = 0.4):
    def check(run):
        text = re.sub(r"[^\w]", "", run.turns[0].reply)
        letters = [c for c in text if c.isalpha()]
        if not letters or sum("؀" <= c <= "ۿ" for c in letters) / len(letters) < min_ratio:
            return f"customer wrote in Urdu script; reply was not: {run.turns[0].reply[:100]!r}"
    return check


def order_has(item: str, qty: int | None = None, fulfilment: str | None = None):
    def check(run):
        with session_scope() as s:
            o = (s.query(m.Order).join(m.Customer).filter(m.Customer.phone == run.phone).order_by(m.Order.id.desc()).first())
            if o is None:
                return "no order found"
            lines = [(i.name.lower(), int(i.qty)) for i in o.items]
            if not any(item.lower() in n and (qty is None or q == qty) for n, q in lines):
                return f"order lacks {qty or ''} {item}: has {lines}"
            if fulfilment and o.fulfilment != fulfilment:
                return f"order is {o.fulfilment}, expected {fulfilment}"
    return check
