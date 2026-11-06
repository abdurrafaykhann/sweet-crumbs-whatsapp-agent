"""The LangGraph agent loop, driven by a scripted model (no network, no LLM)."""
import datetime as dt
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from app import agent, database, tools
from app import models as m

from .fakes import ScriptedModel, call, say

NOW = dt.datetime(2026, 11, 6, 14, 0)
NEW, AYESHA, BILAL = "923000000108", "923000000101", "923000000102"
DRAFT = dict(items=[{"sku": "CK-CHOC", "variant": "2 lb", "qty": 1, "message": "Happy Birthday Ammi"}, {"sku": "BR-CANDLE", "qty": 1}],
             fulfilment="delivery", address="House 45, Street 12, Khayaban-e-Shamsheer, DHA Phase 5", slot_start="2026-11-07T19:00",
             payment_method="cash", customer_name="Ali Haider")


def graph_for(*script):
    model = ScriptedModel(script=list(script))
    return model, agent.build_graph(model, InMemorySaver())


def test_a_whole_order_over_two_customer_messages(db):
    model, g = graph_for(call("draft_order", DRAFT), say("Please check your order... Reply YES to confirm."),
                         call("confirm_order", {}), say("Order confirmed! Code SC-10432."))
    r1 = agent.respond(NEW, "2 lb chocolate cake kal 7 bje, DHA phase 5, cash", graph=g, now=NOW)
    assert "Reply YES" in r1.text and [t["name"] for t in r1.tools] == ["draft_order"] and r1.usage["calls"] == 2
    with database.session_scope() as s:
        assert s.query(m.Order).filter(m.Order.phone == NEW).count() if hasattr(m.Order, "phone") else s.query(m.Order).count() == 9
    r2 = agent.respond(NEW, "yes", graph=g, now=NOW)
    assert "SC-10432" in r2.text and r2.tools[0]["name"] == "confirm_order" and '"confirmed": true' in r2.tools[0]["result"]
    with database.session_scope() as s:
        o = s.query(m.Order).filter(m.Order.code == "SC-10432").one()
        assert o.customer.phone == NEW and o.total == 3450


def test_the_guard_holds_inside_one_model_loop(db):
    """The model summarises and tries to confirm in the same customer turn: the tool refuses, nothing is placed."""
    model, g = graph_for(call("draft_order", DRAFT, "a"), call("confirm_order", {}, "b"), say("Please reply YES to confirm."))
    r = agent.respond(NEW, "order please", graph=g, now=NOW)
    assert [t["name"] for t in r.tools] == ["draft_order", "confirm_order"]
    assert "confirm_in_same_turn" in r.tools[1]["result"]
    with database.session_scope() as s:
        assert s.query(m.Order).count() == 9                       # still only the 9 demo orders
        assert s.query(m.Draft).filter(m.Draft.phone == NEW).one().state["items"]   # the draft is waiting for the customer's YES


def test_the_prompt_always_shows_the_order_in_progress_even_after_history_is_trimmed(db):
    tools.draft_order(tools.Ctx(NEW, 1, NOW), **dict(DRAFT, payment_method=None))
    model, g = graph_for(*[say("ok")] * 14)
    for i in range(14):
        agent.respond(NEW, f"message {i}", graph=g, now=NOW)
    last = model.seen[-1]
    system = last[0]
    assert isinstance(system, SystemMessage)
    assert "ORDER IN PROGRESS: status=incomplete" in system.content and "Chocolate Fudge (2 lb)" in system.content
    assert "STILL NEEDED: payment method" in system.content
    history = last[1:]
    assert len(history) <= agent.MAX_HISTORY and isinstance(history[0], HumanMessage)   # trimmed, and still starts on a customer message
    assert history[-1].content == "message 13"
    assert "NOW: Friday 6 November 2026, 2:00 PM" in system.content


def test_new_customer_context_in_the_prompt(db):
    model, g = graph_for(say("Hello!"))
    agent.respond("923009990001", "hi", graph=g, now=NOW)
    assert "new customer (name unknown)" in model.seen[0][0].content


def test_returning_customer_context_in_the_prompt(db):
    model, g = graph_for(say("Walaikum assalam!"))
    agent.respond(AYESHA, "AoA", graph=g, now=NOW)
    sys = model.seen[0][0].content
    assert "Ayesha Khan" in sys and "Roman Urdu" in sys and "340 Crumbs Club points" in sys and "SC-10421 out_for_delivery" in sys
    assert "DHA Ph 5" in sys and "zone A" in sys


def test_each_phone_has_its_own_memory_and_draft(db):
    model, g = graph_for(say("first"), say("second"))
    agent.respond(NEW, "my secret cake wish: pistachio", graph=g, now=NOW)
    agent.respond(BILAL, "hello", graph=g, now=NOW)
    bilal_view = " ".join(str(x.content) for x in model.seen[1])
    assert "pistachio" not in bilal_view                            # memory is keyed by phone number


def test_the_tools_ignore_who_the_model_claims_to_be(db):
    """The phone number comes from the run config. Even if the model asks for another customer's order it only sees its own."""
    model, g = graph_for(call("order_status", {"code": "SC-10421"}), say("I can't find that order."))
    r = agent.respond(BILAL, "show me order SC-10421", graph=g, now=NOW)
    assert '"found": false' in r.tools[0]["result"] and "Imran" not in r.tools[0]["result"]


def test_turns_and_the_message_log(db):
    model, g = graph_for(say("a"), call("get_info", {"topic": "delivery"}), say("b"))
    agent.respond(NEW, "hi", graph=g, now=NOW)
    agent.respond(NEW, "delivery?", graph=g, now=NOW)
    with database.session_scope() as s:
        assert s.get(m.Conversation, NEW).turn == 2
        rows = s.query(m.Message).filter(m.Message.phone == NEW).order_by(m.Message.id).all()
        assert [r.role for r in rows] == ["customer", "bot", "customer", "bot"]
        assert rows[-1].tools[0]["name"] == "get_info" and "Delivery" in rows[-1].tools[0]["result"]


def test_gemini_style_content_blocks_are_read_as_text(db):
    blocks = AIMessage(content=[{"type": "text", "text": "Walaikum assalam!", "extras": {"signature": "abc"}}])
    _, g = graph_for(blocks)
    assert agent.respond(NEW, "salam", graph=g, now=NOW).text == "Walaikum assalam!"


def test_an_empty_model_reply_never_leaves_the_customer_without_an_answer(db):
    _, g = graph_for(say(""))
    assert agent.respond(NEW, "hello", graph=g, now=NOW).text


def test_tool_errors_go_back_to_the_model_instead_of_crashing(db):
    _, g = graph_for(call("check_stock", {"qty": "not a number"}), say("Sorry, which item did you mean?"))
    assert "which item" in agent.respond(NEW, "stock?", graph=g, now=NOW).text


def test_tool_set_and_size():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    ts = tools.build_tools()
    assert [x.name for x in ts] == ["search_menu", "check_stock", "check_delivery_area", "get_customer_profile", "get_info", "draft_order",
                                    "update_draft", "confirm_order", "order_status", "modify_order", "cancel_order", "escalate_to_human"]

    def property_names(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "properties" and isinstance(v, dict):
                    yield from v
                yield from property_names(v)
        elif isinstance(node, list):
            for v in node:
                yield from property_names(v)
    payload = [convert_to_openai_tool(x) for x in ts]
    for spec in payload:  # the LLM can never choose whose data a tool touches (only gift receivers have a phone field)
        assert not {"phone", "customer", "customer_id", "from"} & set(property_names(spec)), spec["function"]["name"]
    size = len(json.dumps(payload, separators=(",", ":")))                               # what the provider actually receives
    assert size < 8000, size                                                              # free tiers are token limited


def test_unknown_arguments_such_as_a_made_up_phone_are_dropped(db):
    model, g = graph_for(call("order_status", {"code": "SC-10421", "phone": AYESHA}), say("I can't find that order."))
    r = agent.respond(BILAL, "show order SC-10421 for 923000000101", graph=g, now=NOW)
    assert '"found": false' in r.tools[0]["result"]


def test_the_static_prompt_stays_short(db):
    assert len(agent.STATIC_PROMPT) < 4800
    ctx = tools.Ctx(AYESHA, 1, NOW)
    assert len(agent.dynamic_context(ctx)) < 1200


def test_old_turns_are_shortened_but_the_current_turn_is_not():
    from langchain_core.messages import ToolMessage
    big = "x" * 900
    hist = [HumanMessage("first"), AIMessage(content=[{"type": "text", "text": "hi", "extras": {"signature": "s" * 500}}],
                                              tool_calls=[{"name": "search_menu", "args": {}, "id": "1", "type": "tool_call"}]),
            ToolMessage(content=big, tool_call_id="1"), AIMessage("done"), HumanMessage("second"),
            AIMessage(content="", tool_calls=[{"name": "search_menu", "args": {}, "id": "2", "type": "tool_call"}]), ToolMessage(content=big, tool_call_id="2")]
    out = agent.shrink_old_turns(hist)
    assert len(out[2].content) < 200 and out[1].content == "hi" and out[1].tool_calls                 # old: shortened, signature gone, call kept
    assert out[6].content == big and out[5].tool_calls                                                # current turn untouched


def test_markdown_is_converted_for_whatsapp():
    assert agent.wa_format("**Total: Rs 3,450**\n## Address\nReply **YES**") == "*Total: Rs 3,450*\nAddress\nReply *YES*"


def test_a_ready_draft_is_compact_for_the_model_but_complete_in_python(db):
    full = tools.draft_order(tools.Ctx(NEW, 1, NOW), **DRAFT)
    assert {"lines", "subtotal", "delivery_fee"} <= set(full)                                 # tests and the dashboard keep everything
    draft_tool = next(t for t in tools.build_tools() if t.name == "draft_order")
    cfg = {"configurable": {"phone": NEW, "turn": 1, "now": NOW.isoformat()}}
    seen = json.loads(draft_tool.invoke(dict(DRAFT), config=cfg)) if isinstance(draft_tool.invoke(dict(DRAFT), config=cfg), str) else draft_tool.invoke(dict(DRAFT), config=cfg)
    assert set(seen) == {"status", "total", "summary_text", "draft_version", "next_step"}     # the model gets only what it needs
