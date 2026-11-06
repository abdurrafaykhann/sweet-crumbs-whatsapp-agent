"""The LLM layer: error classification, retries with backoff and jitter, fallback per request, cooldowns, usage logging."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app import database, llm, usage
from app import models as m
from app.llm import LLMUnavailable, Provider, ResilientModel, classify


class Boom(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


GROQ_TPD = ("Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` ... on tokens per day (TPD): "
            "Limit 200000, Used 197513, Requested 2644. Please try again in 21m2.304s. Need more tokens? Upgrade to Dev Tier'}}")
GEMINI_RPD = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash, "
              "description: Generate content requests per day limit exceeded on Free Tier. Please retry in 7h28m44.48s.")
GEMINI_RPM = "429 RESOURCE_EXHAUSTED. ... requests per minute limit exceeded on Free Tier. Please retry in 38.5s."
OVERLOAD = "503 UNAVAILABLE. This model is currently experiencing high demand. Spikes in demand are usually temporary."


class Fake:
    """A provider's chat model: plays a script of replies (AIMessage) and errors (Exception)."""

    def __init__(self, *script):
        self.script, self.calls, self.seen = list(script), 0, []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        self.seen.append(messages)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text="hi", i=10, o=3):
    return AIMessage(content=text, usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o})


class Rec:
    def __init__(self):
        self.rows = []

    def __call__(self, *row):
        self.rows.append(row)


def model(primary, fallback=None, **kw):
    rec, sleeps = Rec(), []
    ps = [Provider("groq", "oss", primary)] + ([Provider("google_genai", "lite", fallback)] if fallback else [])
    t = {"now": 1000.0}
    mdl = ResilientModel(ps, sleep=sleeps.append, rng=lambda: 0.5, clock=lambda: t["now"], recorder=rec, **kw).bind_tools([])
    return mdl, rec, sleeps, t


# ----------------------------------------------------------------------------- classification
def test_classify_real_provider_errors():
    a = classify(Boom(GROQ_TPD, 429))
    assert (a.kind, a.daily) == ("rate_limit", True) and a.retry_after == pytest.approx(21 * 60 + 2.304)
    b = classify(Boom(GEMINI_RPD))
    assert (b.kind, b.daily) == ("rate_limit", True) and b.retry_after == pytest.approx(7 * 3600 + 28 * 60 + 44.48)
    c = classify(Boom(GEMINI_RPM))
    assert (c.kind, c.daily) == ("rate_limit", False) and c.retry_after == pytest.approx(38.5)
    assert classify(Boom(OVERLOAD, 503)).kind == "server"
    assert classify(Boom("Request timed out.")).kind == "timeout"
    assert classify(Boom("API key not valid", 401)).kind == "auth"
    assert classify(Boom("tool_use_failed", 400)).kind == "bad_request"
    assert classify(ValueError("weird")).kind == "other"


# ----------------------------------------------------------------------------- retries, backoff, fallback
def test_first_provider_answers_and_usage_is_logged():
    p = Fake(ok("hello", 120, 7))
    mdl, rec, sleeps, _ = model(p, Fake())
    ai = mdl.invoke([HumanMessage("hi")])
    assert ai.content == "hello" and ai.response_metadata["served_by"] == "groq" and sleeps == []
    assert rec.rows[0][:5] == ("groq", "oss", True, 120, 7) and rec.rows[0][-1] == ""


def test_transient_error_is_retried_with_exponential_backoff_and_jitter():
    p = Fake(Boom(OVERLOAD, 503), Boom(OVERLOAD, 503), ok("finally"))
    mdl, rec, sleeps, _ = model(p, Fake())
    assert mdl.invoke([HumanMessage("hi")]).content == "finally" and p.calls == 3
    assert sleeps == [1 + 0.5, 2 + 0.5]                                           # base * 2**attempt, plus jitter (rng fixed at 0.5)
    assert [r[2] for r in rec.rows] == [False, False, True]


def test_short_rate_limit_waits_for_the_providers_hint_then_retries():
    p = Fake(Boom(GEMINI_RPM), ok("ok"))
    mdl, _, sleeps, _ = model(p, Fake(), max_wait=60)
    assert mdl.invoke([HumanMessage("hi")]).content == "ok"
    assert sleeps == [pytest.approx(38.5 + 0.5)] and p.calls == 2


def test_after_the_attempts_the_fallback_answers_that_request():
    primary, fallback = Fake(*[Boom(OVERLOAD, 503)] * 3), Fake(ok("from gemini"))
    mdl, rec, _, _ = model(primary, fallback)
    ai = mdl.invoke([HumanMessage("hi")])
    assert ai.content == "from gemini" and ai.response_metadata["served_by"] == "google_genai" and primary.calls == 3
    assert rec.rows[-1][0] == "google_genai" and rec.rows[-1][-1] == "groq"       # logged as served in place of groq


def test_daily_quota_means_no_waiting_and_a_cooldown():
    primary, fallback = Fake(Boom(GROQ_TPD, 429)), Fake(ok("a"), ok("b"))
    mdl, _, sleeps, t = model(primary, fallback)
    assert mdl.invoke([HumanMessage("1")]).content == "a" and sleeps == [] and primary.calls == 1
    assert mdl.invoke([HumanMessage("2")]).content == "b" and primary.calls == 1   # skipped while cooling down: no wasted call, no wait
    t["now"] += 21 * 60 + 5                                                          # the quota window has passed
    primary.script.append(ok("primary is back"))
    assert mdl.invoke([HumanMessage("3")]).content == "primary is back"


def test_a_bad_key_moves_to_the_fallback_and_is_skipped_for_a_while():
    primary, fallback = Fake(Boom("Invalid API key", 401)), Fake(ok("a"), ok("b"))
    mdl, _, _, _ = model(primary, fallback)
    mdl.invoke([HumanMessage("1")])
    mdl.invoke([HumanMessage("2")])
    assert primary.calls == 1


def test_everything_failing_raises_llm_unavailable():
    mdl, rec, _, _ = model(Fake(*[Boom(OVERLOAD, 503)] * 3), Fake(*[Boom(OVERLOAD, 503)] * 3))
    with pytest.raises(LLMUnavailable):
        mdl.invoke([HumanMessage("hi")])
    assert len(rec.rows) == 6 and not any(r[2] for r in rec.rows)


def test_all_providers_cooling_down_raises_with_a_wait_time():
    mdl, _, _, _ = model(Fake(Boom(GROQ_TPD, 429)), Fake(Boom(GEMINI_RPD)))
    with pytest.raises(LLMUnavailable):
        mdl.invoke([HumanMessage("1")])
    with pytest.raises(LLMUnavailable) as e:
        mdl.invoke([HumanMessage("2")])
    assert e.value.retry_after and e.value.retry_after > 60


def _exchange(served_by):
    from langchain_core.messages import ToolMessage
    ai = AIMessage(content=[{"type": "text", "text": "checking", "extras": {"signature": "s" * 300}}] if served_by == "google_genai" else "checking",
                   tool_calls=[{"name": "check_delivery_area", "args": {"text": "PECHS"}, "id": "call_1", "type": "tool_call"}],
                   response_metadata={"served_by": served_by})
    return [HumanMessage("do you deliver to PECHS?"), ai, ToolMessage(content='{"zone": "B", "fee": 200}', tool_call_id="call_1")]


def test_tool_calls_written_by_groq_are_never_replayed_to_gemini():
    """The live failure: Groq made a tool call, then hit its quota; Gemini rejects function calls without its own thought signature."""
    fallback = Fake(ok("PECHS is Zone B, Rs 200."))
    mdl = ResilientModel([Provider("groq", "oss", Fake(Boom(GROQ_TPD, 429))), Provider("google_genai", "lite", fallback)],
                         sleep=lambda s: None, rng=lambda: 0, recorder=Rec()).bind_tools([])
    mdl.invoke(_exchange("groq"))
    sent = fallback.seen[0]
    assert not any(getattr(x, "tool_calls", None) for x in sent) and not any(type(x).__name__ == "ToolMessage" for x in sent)
    assert "check_delivery_area" in str(sent[-1].content) and '"fee": 200' in str(sent[-1].content)         # what was looked up is kept, as text


def test_tool_calls_written_by_gemini_are_flattened_for_groq_and_signatures_dropped():
    fallback = Fake(ok("done"))
    mdl = ResilientModel([Provider("google_genai", "lite", Fake(*[Boom(OVERLOAD, 503)] * 3)), Provider("groq", "oss", fallback)],
                         sleep=lambda s: None, rng=lambda: 0, recorder=Rec()).bind_tools([])
    mdl.invoke(_exchange("google_genai"))
    sent = fallback.seen[0]
    assert not any(getattr(x, "tool_calls", None) for x in sent) and "s" * 50 not in str(sent)


def test_a_provider_keeps_the_tool_calls_it_wrote_itself():
    own = Fake(ok("fine"))
    mdl = ResilientModel([Provider("groq", "oss", own)], sleep=lambda s: None, rng=lambda: 0, recorder=Rec()).bind_tools([])
    mdl.invoke(_exchange("groq"))
    assert any(getattr(x, "tool_calls", None) for x in own.seen[0]) and any(type(x).__name__ == "ToolMessage" for x in own.seen[0])


# ----------------------------------------------------------------------------- usage summary
def test_usage_summary_against_the_free_tier_limits(seeded):
    with database.session_scope() as s:
        s.query(m.LlmUsage).delete()
        for _ in range(3):
            s.add(m.LlmUsage(provider="groq", model="oss", input_tokens=2000, output_tokens=100, ok=True))
        s.add(m.LlmUsage(provider="groq", model="oss", ok=False, error_kind="rate_limit"))
        s.add(m.LlmUsage(provider="google_genai", model="lite", input_tokens=500, output_tokens=50, ok=True, fallback_from="groq"))
    with database.session_scope() as s:
        u = usage.usage_summary(s)
    g = u["groq"]
    assert g["today"]["requests"] == 4 and g["today"]["errors"] == 1 and g["today"]["input_tokens"] == 6000 and g["last_error"] == "rate_limit"
    assert g["percent_of_limit"]["tokens_per_day"] == round(100 * 6300 / 200000, 1)
    assert u["google_genai"]["today"]["served_as_fallback"] == 1 and "requests_per_minute" in u["google_genai"]["percent_of_limit"]


def test_providers_without_keys_are_skipped(monkeypatch):
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert llm.get_model().providers == []
    with pytest.raises(LLMUnavailable):
        llm.get_model().bind_tools([]).invoke([HumanMessage("hi")])
