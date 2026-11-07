"""The eval harness itself, offline: the cases are well formed, the checks catch what they should, and a scripted run works end to end."""
import json

from langgraph.checkpoint.memory import InMemorySaver

from app import agent
from evals import checks, harness, judge, runner
from evals.cases import CASES, by_id
from evals.harness import Run, Turn
from tests.fakes import ScriptedModel, say


def test_all_35_spec_cases_are_present_with_at_least_five_rewordings():
    ids = {c.id for c in CASES}
    assert {str(i) for i in range(1, 36)} <= ids
    for c in CASES:
        assert len(c.variants) >= 2, c.id
        assert c.expect and c.turns
    for i in range(1, 36):
        assert len(by_id(str(i)).variants) >= 5, i


def test_rewordings_cover_several_languages_and_tones():
    styles = {s for c in CASES for s, _ in c.variants}
    assert {"polite", "rushed", "angry", "typo", "roman", "urdu"} <= styles
    assert any(any("\u0600" <= ch <= "\u06ff" for ch in t) for c in CASES for _, turns in c.variants for t in turns)   # Urdu script


def test_number_check_reads_urdu_digits_and_separators():
    run = Run("x", [Turn("q", "قیمت Rs 3,200 ہے")])
    assert checks.says_number(3200)(run) is None
    assert checks.says_number(3300)(run)
    assert checks.digits("۳٬۲۰۰") == "3200"


def test_order_checks_use_the_turn_by_turn_order_count():
    run = Run("x", [Turn("a"), Turn("b")], placed_after=[1, 1])
    assert checks.not_placed_before_last_turn()(run)
    assert checks.order_placed()(Run("x", [Turn("a")], placed_after=[1])) is None
    assert checks.no_order_placed()(Run("x", [Turn("a")], placed_after=[0])) is None


def test_judge_parses_json_even_with_chatter():
    out = judge.parse('Sure!\n{"facts": true, "pass": true, "reason": "ok"}')
    assert out["pass"] is True
    try:
        judge.parse("no json here")
        assert False
    except ValueError:
        pass


def test_scripted_run_end_to_end_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "CACHE", tmp_path)
    monkeypatch.setattr(harness, "TEMPLATE", tmp_path / "template.db")
    monkeypatch.setattr(harness, "RUNDB", tmp_path / "run.db")
    case = by_id("1")
    graph = agent.build_graph(ScriptedModel(script=[say("Assalamualaikum! Welcome to Sweet Crumbs.")]), InMemorySaver())
    judge_model = ScriptedModel(script=[say(json.dumps({"facts": True, "pass": True, "reason": "greets"}))])
    rec = runner.run_variant(case, "original", case.turns, 1, graph, judge_model, "scripted", "scripted")
    assert rec["passed"] and rec["status"] == "ok" and rec["turns"][0]["reply"].startswith("Assalam")
    s = runner.summarise([rec])
    assert s["passed"] == 1 and s["by_category"]["info"] == (1, 1)
    assert "Passed 1/1" in runner.markdown(s, [rec], "t")


def test_paced_waits_for_the_providers_retry_hint_then_resumes():
    from app.llm import LLMUnavailable
    slept = []

    class Flaky:
        n = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, messages, config=None):
            self.n += 1
            if self.n == 1:
                raise LLMUnavailable("cooling down", retry_after=21.3)
            return "answer"

    paced = runner.Paced(Flaky(), 0, max_wait=60, sleep=slept.append)
    assert paced.invoke([]) == "answer"
    assert any(22 <= s <= 23 for s in slept)            # waited the hinted time (+1 s), then retried


def test_paced_gives_up_on_daily_quota_sized_waits():
    import pytest
    from app.llm import LLMUnavailable

    class Daily:
        def invoke(self, messages, config=None):
            raise LLMUnavailable("daily quota", retry_after=7 * 3600)

    with pytest.raises(runner.QuotaExhausted):
        runner.Paced(Daily(), 0, max_wait=900, sleep=lambda s: None).invoke([])


def test_judge_is_a_small_model_from_the_other_provider(monkeypatch):
    monkeypatch.delenv("LLM_JUDGE", raising=False)
    assert runner.default_judge("groq:openai/gpt-oss-120b").startswith("google_genai:")
    assert runner.default_judge("google_genai:gemini-flash-lite-latest") == "groq:openai/gpt-oss-20b"
