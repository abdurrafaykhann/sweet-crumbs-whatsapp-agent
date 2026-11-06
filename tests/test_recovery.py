"""A customer is never left without an answer: model down, both models down, crashes, and silence while staff have the chat."""
import datetime as dt

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app import agent, database
from app import models as m
from app.llm import LLMUnavailable

from .fakes import ScriptedModel, call, say

NOW = dt.datetime(2026, 11, 6, 14, 0)
NEW, AYESHA = "923009990001", "923000000101"   # NEW is not in the database: English by default


def graph_for(*script):
    model = ScriptedModel(script=list(script))
    return model, agent.build_graph(model, InMemorySaver())


def bot_texts(phone):
    with database.session_scope() as s:
        return [r.text for r in s.query(m.Message).filter(m.Message.phone == phone, m.Message.role == "bot").order_by(m.Message.id)]


def test_model_down_then_back(db):
    _, g = graph_for(LLMUnavailable("every provider is cooling down"), say("Here is the price: Rs 3,200."))
    notes = []
    r = agent.respond(NEW, "chocolate cake 2 lb price", graph=g, now=NOW, notify=notes.append, retry_delay=0)
    assert r.text == "Here is the price: Rs 3,200." and notes == [agent.WAIT_TEXT["en"]]            # told to wait, then answered
    state = g.get_state({"configurable": {"thread_id": NEW}})
    assert [x.content for x in state.values["messages"] if isinstance(x, HumanMessage)] == ["chocolate cake 2 lb price"]  # not added twice
    assert bot_texts(NEW) == [agent.WAIT_TEXT["en"], "Here is the price: Rs 3,200."]


def test_the_wait_message_follows_the_customers_usual_language(db):
    _, g = graph_for(LLMUnavailable("down"), say("ok"))
    notes = []
    agent.respond(AYESHA, "hello", graph=g, now=NOW, notify=notes.append, retry_delay=0)
    assert notes == [agent.WAIT_TEXT["rur"]]                                                          # Ayesha writes Roman Urdu


def test_failure_after_a_tool_call_resumes_from_the_checkpoint(db):
    model, g = graph_for(call("get_info", {"topic": "delivery"}), LLMUnavailable("down"), say("Delivery is Rs 150, 200 or 300 by zone."))
    r = agent.respond(NEW, "delivery charges?", graph=g, now=NOW, notify=lambda t: None, retry_delay=0)
    assert "Rs 150" in r.text and model.idx == 3
    assert len([x for x in g.get_state({"configurable": {"thread_id": NEW}}).values["messages"] if isinstance(x, HumanMessage)]) == 1


def test_both_attempts_failing_hands_the_chat_to_staff_and_goes_silent(db):
    model, g = graph_for(LLMUnavailable("down"), LLMUnavailable("still down"))
    notes = []
    r = agent.respond(NEW, "I want a 2 lb pineapple cake tomorrow", graph=g, now=NOW, notify=notes.append, retry_delay=0)
    assert r.text == agent.GIVE_UP_TEXT["en"] and r.escalated and len(notes) == 1
    with database.session_scope() as s:
        e = s.query(m.Escalation).filter(m.Escalation.phone == NEW).one()
        assert e.reason == "llm_unavailable" and "pineapple" in e.summary and e.priority == "high"
        assert s.get(m.Conversation, NEW).escalated is True
    again = agent.respond(NEW, "hello?", graph=g, now=NOW)                                             # the model is not even called now
    assert again.silent and again.text == "" and model.idx == 2


def test_a_crash_is_not_retried_blindly_and_goes_to_staff(db):
    _, g = graph_for(RuntimeError("boom"))
    notes = []
    r = agent.respond(NEW, "hi", graph=g, now=NOW, notify=notes.append, retry_delay=0)
    assert r.text == agent.GIVE_UP_TEXT["en"] and notes == []
    with database.session_scope() as s:
        assert s.query(m.Escalation).filter(m.Escalation.phone == NEW).one().reason == "assistant_error"


def test_after_the_bot_escalates_it_stays_silent(db):
    model, g = graph_for(call("escalate_to_human", {"reason": "damaged cake", "summary": "cheesecake crushed", "priority": "high"}),
                         say("I'm sorry. Our care team will reply here shortly."))
    first = agent.respond(AYESHA, "the cake came crushed!!", graph=g, now=NOW)
    assert first.escalated and "care team" in first.text and not first.silent
    second = agent.respond(AYESHA, "hello??", graph=g, now=NOW)
    assert second.silent and second.text == "" and model.idx == 2
    with database.session_scope() as s:                                                                # but the customer's message is kept for staff
        texts = [r.text for r in s.query(m.Message).filter(m.Message.phone == AYESHA, m.Message.role == "customer")]
    assert texts == ["the cake came crushed!!", "hello??"]


def test_response_time_and_provider_are_recorded(db):
    from langchain_core.messages import AIMessage
    ai = AIMessage(content="hi", response_metadata={"served_by": "google_genai"})
    _, g = graph_for(ai)
    r = agent.respond(NEW, "hello", graph=g, now=NOW)
    assert r.provider == "google_genai"
    with database.session_scope() as s:
        row = s.query(m.Message).filter(m.Message.phone == NEW, m.Message.role == "bot").one()
        assert row.provider == "google_genai" and row.latency_ms >= 0
