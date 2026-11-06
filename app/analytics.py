"""Numbers for the owner: today, top items, areas, bot conversion, escalation rate, response time, LLM usage vs the free-tier limits."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, func
from sqlalchemy.orm import Session

from . import clock, repo, rules, usage
from . import models as m


def _day(column):
    """The calendar date of a timestamp column. (CAST(... AS DATE) is wrong on SQLite: it returns the year.)"""
    return func.date(column, type_=Date)


def _pct(num: int, den: int) -> float:
    return round(100 * num / den, 1) if den else 0.0


def compute(s: Session, today: dt.date | None = None, days: int = 7) -> dict:
    today = today or clock.today()
    since = dt.datetime.combine(today - dt.timedelta(days=days - 1), dt.time.min)
    live = m.Order.status != "cancelled"

    todays = s.query(m.Order).filter(_day(m.Order.created_at) == today, live).all()
    revenue = sum(o.total for o in todays)
    by_status = dict(s.query(m.Order.status, func.count(m.Order.id)).filter(_day(m.Order.created_at) == today).group_by(m.Order.status).all())

    top: dict[str, int] = {}
    for it in s.query(m.OrderItem).join(m.Order).filter(live, m.Order.created_at >= since - dt.timedelta(days=30 - days)).all():
        if not it.options.get("free"):
            top[it.name] = top.get(it.name, 0) + int(it.qty)
    top_items = [{"item": k, "qty": v} for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:8]]

    areas = repo.areas_as_dicts(s)
    by_area: dict[str, int] = {}
    for o in s.query(m.Order).filter(live, m.Order.created_at >= since).all():
        if o.fulfilment == "pickup":
            label = "Pickup"
        else:
            hit = rules.resolve_area(o.address_text, areas)
            label = hit.area or (f"Zone {o.zone_code}" if o.zone_code else "Other")
        by_area[label] = by_area.get(label, 0) + 1

    chatters = {r[0] for r in s.query(m.Message.phone).filter(m.Message.role == "customer", m.Message.created_at >= since).distinct()}
    converted = {r[0] for r in (s.query(m.Customer.phone).join(m.Order).filter(m.Order.source == "bot", m.Order.created_at >= since).distinct())} & chatters
    escalated = {r[0] for r in s.query(m.Escalation.phone).filter(m.Escalation.created_at >= since).distinct()} & chatters
    times = sorted(r[0] for r in s.query(m.Message.latency_ms).filter(m.Message.role == "bot", m.Message.latency_ms > 0, m.Message.created_at >= since))
    avg_ms = round(sum(times) / len(times)) if times else 0
    p90 = times[int(0.9 * (len(times) - 1))] if times else 0

    return {
        "date": today.isoformat(), "window_days": days,
        "today": {"orders": len(todays), "revenue": revenue, "by_status": by_status,
                  "open_escalations": s.query(m.Escalation).filter(m.Escalation.status == "open").count(),
                  "awaiting_payment_check": s.query(m.Order).filter(m.Order.status == "awaiting_advance").count()},
        "top_items": top_items, "orders_by_area": [{"area": k, "orders": v} for k, v in sorted(by_area.items(), key=lambda kv: -kv[1])],
        "bot": {"conversations": len(chatters), "converted": len(converted), "conversion_rate": _pct(len(converted), len(chatters)),
                "escalated": len(escalated), "escalation_rate": _pct(len(escalated), len(chatters)),
                "avg_response_ms": avg_ms, "p90_response_ms": p90, "answers": len(times)},
        "llm": usage.usage_summary(s),
    }
