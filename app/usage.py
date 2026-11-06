"""LLM request and token usage per provider, shown on the dashboard against the free-tier limits."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models as m
from .llm import FREE_TIER_LIMITS


def usage_summary(s: Session, now: dt.datetime | None = None) -> dict:
    """Per provider: requests and tokens today and in the last minute, errors, requests served as fallback, vs the free-tier limits."""
    now = now or m.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute_ago = now - dt.timedelta(minutes=1)
    out = {}
    for provider in [r[0] for r in s.query(m.LlmUsage.provider).distinct()] or []:
        def agg(since, ok=None):
            q = s.query(func.count(m.LlmUsage.id), func.coalesce(func.sum(m.LlmUsage.input_tokens), 0),
                        func.coalesce(func.sum(m.LlmUsage.output_tokens), 0)).filter(m.LlmUsage.provider == provider, m.LlmUsage.created_at >= since)
            if ok is not None:
                q = q.filter(m.LlmUsage.ok == ok)
            return q.one()
        n_day, in_day, out_day = agg(day_start)
        n_min, in_min, out_min = agg(minute_ago)
        errors = agg(day_start, ok=False)[0]
        fallbacks = s.query(func.count(m.LlmUsage.id)).filter(m.LlmUsage.provider == provider, m.LlmUsage.created_at >= day_start,
                                                              m.LlmUsage.fallback_from != "", m.LlmUsage.ok.is_(True)).scalar()
        last_error = s.query(m.LlmUsage.error_kind).filter(m.LlmUsage.provider == provider, m.LlmUsage.ok.is_(False)).order_by(m.LlmUsage.id.desc()).limit(1).scalar()
        limits = FREE_TIER_LIMITS.get(provider, {})
        out[provider] = {
            "today": {"requests": n_day, "input_tokens": int(in_day), "output_tokens": int(out_day), "errors": errors, "served_as_fallback": fallbacks},
            "last_minute": {"requests": n_min, "tokens": int(in_min + out_min)},
            "last_error": last_error or "",
            "limits": limits,
            "percent_of_limit": {k: round(100 * v / limits[k], 1) for k, v in {
                "tokens_per_day": int(in_day + out_day), "requests_per_day": n_day, "tokens_per_minute": int(in_min + out_min),
                "requests_per_minute": n_min}.items() if k in limits},
        }
    return out
