"""LLM layer: provider selection from .env, retries with exponential backoff and jitter, a fallback provider per request,
a cooldown for providers that ran out of daily quota, and usage logging for every call.

    LLM_PRIMARY=groq:openai/gpt-oss-120b
    LLM_FALLBACK=google_genai:gemini-flash-lite-latest

The agent only needs `model.bind_tools(tools).invoke(messages)`, so ResilientModel is a small duck-typed wrapper (any
LangChain chat model, or a scripted fake in tests, can be a provider)."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

log = logging.getLogger("sweetcrumbs.llm")
logging.getLogger("langchain_google_genai.chat_models").setLevel(logging.ERROR)  # "dropping reasoning block from provider groq"

DEFAULT_PRIMARY = "groq:openai/gpt-oss-120b"
DEFAULT_FALLBACK = "google_genai:gemini-flash-lite-latest"
CURRENT_PHONE: contextvars.ContextVar[str] = contextvars.ContextVar("current_phone", default="")

# Free-tier limits observed in November 2026 (see docs/DECISIONS.md). Only used to show usage against the limits.
FREE_TIER_LIMITS = {
    "groq": {"tokens_per_minute": 8000, "tokens_per_day": 200000, "requests_per_day": 1000},
    "google_genai": {"requests_per_minute": 15},
}


class LLMUnavailable(RuntimeError):
    """Every provider failed (or is cooling down). The caller must still answer the customer."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# ----------------------------------------------------------------------------- error classification
@dataclass
class ErrorInfo:
    kind: str                      # rate_limit | server | timeout | auth | bad_request | other
    retry_after: float | None = None
    daily: bool = False            # quota for the day is gone: waiting is pointless


_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+(?:\.\d+)?)m(?!s))?\s*(?:(\d+(?:\.\d+)?)s)?", re.I)


def _seconds(text: str) -> float | None:
    m = _DURATION.fullmatch(text.strip())
    if not m or not any(m.groups()):
        return None
    h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def retry_hint(exc: Exception) -> float | None:
    """Seconds the provider asks us to wait: 'try again in 1m7.8s', 'retry in 7h28m44s', Retry-After header, retryDelay."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        try:
            v = headers.get("retry-after")
            if v:
                return float(v)
        except (TypeError, ValueError):
            pass
    text = str(exc)
    m = re.search(r"(?:try again|retry) in\s+((?:\d+(?:\.\d+)?\s*[hms]\s*)+)", text, re.I)  # '21m2.304s', '7h28m44.48s', '38.5s'
    if m:
        return _seconds(m.group(1))
    m = re.search(r"retryDelay['\"]?:\s*['\"]?([0-9.]+s)", text)
    return _seconds(m.group(1)) if m else None


def classify(exc: Exception) -> ErrorInfo:
    text, name = str(exc), type(exc).__name__.lower()
    low = text.lower()
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int):
        status = None
    hint = retry_hint(exc)
    if status == 429 or "ratelimit" in name or "rate limit" in low or "resource_exhausted" in low or "quota" in low:
        daily = any(w in low for w in ("per day", "tokens per day", "(tpd)", "(rpd)", "requestsperday")) or bool(hint and hint > 120)
        return ErrorInfo("rate_limit", hint, daily)
    if status in (500, 502, 503, 504) or any(w in low for w in ("overloaded", "high demand", "unavailable", "internal server error")):
        return ErrorInfo("server", hint)
    if "timeout" in name or "timed out" in low or "connection" in name:
        return ErrorInfo("timeout", hint)
    if status in (401, 403) or "api key" in low or "permission denied" in low or "authentication" in name:
        return ErrorInfo("auth")
    if status == 400 or "badrequest" in name:
        return ErrorInfo("bad_request")
    return ErrorInfo("other")


# ----------------------------------------------------------------------------- providers and the resilient model
@dataclass
class Provider:
    name: str                      # groq | google_genai (prefix of the 'provider:model' spec)
    model_id: str
    raw: object                    # a LangChain chat model with bind_tools()/invoke()
    cooldown_until: float = 0.0    # epoch seconds: skip this provider until then (daily quota gone, bad key)
    bound: object = field(default=None, repr=False)


def _prepare(messages: list, provider: str) -> list:
    """Make a conversation readable by `provider` even when part of it was written by another one.
    - Tool calls made by another provider cannot be replayed: Gemini 3 demands its own signed function-call parts, ids and formats differ.
      Those exchanges become one plain-text note of what was looked up, so a request can move between providers at any point.
    - Gemini's list-of-blocks content (text plus thought signatures) becomes plain text for the others."""
    foreign: dict[str, str] = {}
    notes: dict[int, list[str]] = {}
    out: list = []
    for x in messages:
        if isinstance(x, AIMessage) and x.tool_calls and (x.response_metadata or {}).get("served_by") != provider:
            idx = len(out)
            notes[idx] = []
            for tc in x.tool_calls:
                foreign[tc["id"]] = f"{tc['name']}({json.dumps(tc['args'], ensure_ascii=False)[:200]})"
            out.append(("note", idx, message_text(x)))
        elif isinstance(x, ToolMessage) and x.tool_call_id in foreign:
            last = max(notes)
            notes[last].append(f"{foreign[x.tool_call_id]} -> {str(x.content)[:600]}")
        else:
            if provider != "google_genai" and isinstance(x, AIMessage) and not isinstance(x.content, str):
                x = x.model_copy(update={"content": message_text(x)})
            out.append(x)
    result = []
    for x in out:
        if isinstance(x, tuple):
            _, idx, text = x
            lookups = "\n".join(notes[idx])
            result.append(HumanMessage(content=("[Already looked up for this request, by the tools]\n" + lookups) if lookups else "[tool call without result]"))
        else:
            result.append(x)
    return result


def db_recorder(provider: str, model: str, ok: bool, input_tokens: int, output_tokens: int, latency_ms: int, error_kind: str, fallback_from: str):
    """Write one llm_usage row. Logging must never break a customer's answer."""
    try:
        from . import models as m
        from .database import session_scope
        with session_scope() as s:
            s.add(m.LlmUsage(provider=provider, model=model, phone=CURRENT_PHONE.get(), input_tokens=input_tokens, output_tokens=output_tokens,
                             latency_ms=latency_ms, ok=ok, error_kind=error_kind, fallback_from=fallback_from))
    except Exception:
        log.exception("could not record llm usage")


class ResilientModel:
    """Tries providers in order. Per provider: up to `max_attempts` with exponential backoff and jitter (honouring the provider's own
    'try again in' hint), then the next provider answers that request. A provider whose daily quota is gone, or whose key is bad,
    is skipped for a while so the next requests do not wait for it. Every call is logged."""

    def __init__(self, providers: list[Provider], *, max_attempts: int = 3, base_delay: float = 1.0, max_wait: float = 20.0,
                 sleep=time.sleep, rng=random.random, clock=time.time, recorder=db_recorder):
        self.providers, self.max_attempts, self.base_delay, self.max_wait = providers, max_attempts, base_delay, max_wait
        self.sleep, self.rng, self.clock, self.recorder = sleep, rng, clock, recorder

    def bind_tools(self, tools):
        for p in self.providers:
            p.bound = p.raw.bind_tools(tools)
        return self

    def invoke(self, messages, config=None):
        now = self.clock()
        live = [p for p in self.providers if p.cooldown_until <= now]
        if not live:
            wait = min(p.cooldown_until for p in self.providers) - now if self.providers else None
            raise LLMUnavailable("every provider is cooling down", retry_after=wait)
        first = live[0].name
        last: Exception | None = None
        for p in live:
            msgs = _prepare(messages, p.name)
            for attempt in range(self.max_attempts):
                t0 = time.perf_counter()
                try:
                    ai = (p.bound or p.raw).invoke(msgs)
                except Exception as exc:  # classify every provider error the same way
                    last = exc
                    info = classify(exc)
                    self.recorder(p.name, p.model_id, False, 0, 0, int((time.perf_counter() - t0) * 1000), info.kind, "" if p.name == first else first)
                    log.warning("%s failed (%s, attempt %d): %s", p.name, info.kind, attempt + 1, str(exc)[:160])
                    if info.kind == "auth":
                        p.cooldown_until = self.clock() + 600
                        break
                    if info.kind == "rate_limit" and (info.daily or (info.retry_after or 0) > self.max_wait):
                        p.cooldown_until = self.clock() + (info.retry_after or 3600)
                        break
                    if attempt == self.max_attempts - 1 or (info.kind == "bad_request" and attempt >= 1):
                        break
                    backoff = min(self.max_wait, self.base_delay * 2 ** attempt)
                    delay = min(self.max_wait, max(info.retry_after or 0, backoff)) + self.rng() * self.base_delay  # exponential + jitter
                    self.sleep(delay)
                    continue
                usage = getattr(ai, "usage_metadata", None) or {}
                self.recorder(p.name, p.model_id, True, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                              int((time.perf_counter() - t0) * 1000), "", "" if p.name == first else first)
                ai.response_metadata = {**(ai.response_metadata or {}), "served_by": p.name, "model": p.model_id}
                return ai
        raise LLMUnavailable(f"all providers failed: {str(last)[:200]}")


# ----------------------------------------------------------------------------- building the default model from .env
_KEYS = {"groq": ("GROQ_API_KEY",), "google_genai": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def _has_key(provider: str) -> bool:
    return any(os.environ.get(k) for k in _KEYS.get(provider, ()))


def make_provider(spec: str, **kwargs) -> Provider:
    from langchain.chat_models import init_chat_model
    kwargs.setdefault("temperature", 0.3)
    kwargs.setdefault("max_retries", 0)  # retries and fallback are ours
    kwargs.setdefault("timeout", 60)
    name, _, model_id = spec.partition(":")
    return Provider(name=name, model_id=model_id, raw=init_chat_model(spec, **kwargs))


def get_model(spec: str | None = None, **kwargs) -> ResilientModel:
    """Primary and fallback from .env (LLM_PRIMARY, LLM_FALLBACK). A provider without an API key is skipped."""
    specs = [spec] if spec else [os.environ.get("LLM_PRIMARY") or DEFAULT_PRIMARY, os.environ.get("LLM_FALLBACK") or DEFAULT_FALLBACK]
    providers, seen = [], set()
    for sp in specs:
        name = sp.partition(":")[0]
        if sp in seen or not _has_key(name):
            if sp not in seen:
                log.warning("skipping %s: no API key in the environment", sp)
            continue
        seen.add(sp)
        providers.append(make_provider(sp, **kwargs))
    return ResilientModel(providers, max_attempts=int(os.environ.get("LLM_MAX_ATTEMPTS", "3")), max_wait=float(os.environ.get("LLM_MAX_WAIT", "20")))


def message_text(msg) -> str:
    """Plain text of a model reply. Groq returns a string; Gemini returns a list of content blocks."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts).strip()
