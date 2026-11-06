"""Human handoff: silence, briefs, take over / reply / hand back, payment screenshots."""
import datetime as dt

from langgraph.checkpoint.memory import InMemorySaver

from app import agent, database, handoff, tools
from app import models as m

from .fakes import ScriptedModel, call, say

NOW = dt.datetime(2026, 11, 6, 14, 0)
NEW, AYESHA, SANA, FATIMA = "923009990002", "923000000101", "923000000105", "923000000103"


def graph_for(*script):
    model = ScriptedModel(script=list(script))
    return model, agent.build_graph(model, InMemorySaver())


def conv(phone):
    with database.session_scope() as s:
        c = s.get(m.Conversation, phone)
        return c.escalated if c else None


def open_escalations(phone):
    with database.session_scope() as s:
        return s.query(m.Escalation).filter(m.Escalation.phone == phone, m.Escalation.status == "open").all()


# ----------------------------------------------------------------------------- owner brief
def test_the_brief_tells_staff_what_was_already_collected(db):
    ctx = tools.Ctx(NEW, 1, NOW)
    tools.draft_order(ctx, items=[{"sku": "CU-THEME", "variant": "per lb", "qty": 3, "message": "Frozen"}, {"sku": "CU-FIGURE", "qty": 1}],
                      fulfilment="delivery", address="House 9, Block 4, Clifton", slot_start="2026-11-09T16:00", payment_method="jazzcash", customer_name="Hira")
    with database.session_scope() as s:
        s.add(m.Conversation(phone=NEW, turn=1))
        s.add(m.Message(phone=NEW, role="customer", text="Frozen theme cake for Monday please"))
    tools.escalate_to_human(ctx, "custom cake quote", "Frozen theme, 3 lb, one figure, Monday 4 pm", "normal")
    with database.session_scope() as s:
        brief = handoff.escalation_brief(s, s.query(m.Escalation).filter(m.Escalation.phone == NEW).one(), NOW)
    assert brief["reason"] == "custom cake quote" and "Frozen" in brief["summary"]
    assert brief["collected"]["items"][0].startswith("3 x ") and brief["collected"]["address"].startswith("House 9") and brief["collected"]["total"] == 13400
    assert brief["last_messages"][-1]["text"] == "Frozen theme cake for Monday please"


# ----------------------------------------------------------------------------- take over, reply, hand back
def test_take_over_makes_the_bot_silent_and_staff_can_reply(db):
    model, g = graph_for()
    sent = []
    handoff.take_over(NEW, NOW)
    assert conv(NEW) is True and len(open_escalations(NEW)) == 1
    r = agent.respond(NEW, "hello, anyone there?", graph=g, now=NOW)
    assert r.silent and r.text == "" and model.idx == 0                                    # the model is not called
    handoff.human_reply(NEW, "Hi, this is Sara from Sweet Crumbs. How can I help?", lambda p, t: sent.append((p, t)), NOW)
    assert sent == [(NEW, "Hi, this is Sara from Sweet Crumbs. How can I help?")]
    with database.session_scope() as s:
        roles = [(x.role, x.text) for x in s.query(m.Message).filter(m.Message.phone == NEW).order_by(m.Message.id)]
    assert ("customer", "hello, anyone there?") in roles and ("human", "Hi, this is Sara from Sweet Crumbs. How can I help?") in roles


def test_replying_without_taking_over_first_takes_over(db):
    handoff.human_reply(NEW, "Hello from staff", lambda p, t: None, NOW)
    assert conv(NEW) is True


def test_hand_back_turns_the_bot_on_and_it_knows_what_was_said_meanwhile(db):
    model, g = graph_for(call("escalate_to_human", {"reason": "damaged cheesecake", "summary": "crushed", "priority": "high"}),
                         say("I'm sorry. Our care team will reply here shortly."), say("Happy to help. Anything else?"))
    agent.respond(AYESHA, "the cheesecake came crushed", graph=g, now=NOW)
    assert conv(AYESHA) is True
    agent.respond(AYESHA, "here is the photo, order SC-10421", graph=g, now=NOW)            # silent: staff have the chat
    handoff.human_reply(AYESHA, "We are sorry, a replacement is on its way.", lambda p, t: None, NOW)
    out = handoff.hand_back(AYESHA, graph=g, now=NOW)
    assert out["bot_active"] and out["added_to_memory"] == 2 and conv(AYESHA) is False
    assert open_escalations(AYESHA) == []                                                   # hand back resolves the escalation
    agent.respond(AYESHA, "thanks, and when will it arrive?", graph=g, now=NOW)
    seen = " ".join(str(x.content) for x in model.seen[-1])
    assert "here is the photo, order SC-10421" in seen and "replacement is on its way" in seen   # the bot continues with full context


def test_resolving_an_escalation_closes_it(db):
    with database.session_scope() as s:
        s.add(m.Conversation(phone=NEW, turn=1))
    tools.escalate_to_human(tools.Ctx(NEW, 1, NOW), "refund request", "wants a refund")
    with database.session_scope() as s:
        eid = s.query(m.Escalation).filter(m.Escalation.phone == NEW).one().id
    assert handoff.resolve_escalation(eid, now=NOW) == {"id": eid, "status": "resolved"}
    assert open_escalations(NEW) == [] and conv(NEW) is True       # resolved, but the bot stays off until it is handed back
    assert handoff.resolve_escalation(99999) is None


# ----------------------------------------------------------------------------- images, documents, voice notes
def test_a_payment_screenshot_goes_to_a_person_with_the_order_it_belongs_to(db):
    text = handoff.handle_media(SANA, "image", "MEDIA1", "advance sent", NOW)                # Sana's SC-10430 waits for its Rs 6,700 advance
    assert "verify" in text.lower() and "received" not in text.lower() and "confirmed" not in text.lower()   # never says money arrived
    e = open_escalations(SANA)[0]
    assert e.reason == "payment_screenshot" and e.priority == "high" and e.order_code == "SC-10430" and "6,700" in e.summary
    assert conv(SANA) is True
    with database.session_scope() as s:
        msg = s.query(m.Message).filter(m.Message.phone == SANA, m.Message.role == "customer").one()
    assert msg.attachment == {"kind": "image", "media_id": "MEDIA1"} and msg.text == "advance sent"


def test_the_screenshot_reply_follows_the_customers_language(db):
    assert handoff.handle_media(AYESHA, "image", "m") == handoff.MEDIA_TEXT["payment"]["rur"]               # Ayesha writes Roman Urdu
    assert handoff.handle_media(FATIMA, "document", "m") == handoff.MEDIA_TEXT["payment"]["ur"]           # Fatima writes Urdu


def test_a_screenshot_with_no_waiting_order_still_goes_to_a_person_at_normal_priority(db):
    handoff.handle_media(NEW, "image", "m")
    e = open_escalations(NEW)[0]
    assert e.priority == "normal" and "No order is waiting" in e.summary


def test_voice_notes_and_stickers_get_a_polite_text_only_reply_without_escalation(db):
    text = handoff.handle_media(NEW, "audio", "m")
    assert "text" in text.lower() and open_escalations(NEW) == [] and conv(NEW) is False


def test_media_while_staff_have_the_chat_is_kept_for_them_and_the_bot_says_nothing(db):
    handoff.take_over(NEW, NOW)
    assert handoff.handle_media(NEW, "image", "photo-of-the-damage") == ""
    assert "The customer sent an image" in open_escalations(NEW)[0].summary
    with database.session_scope() as s:
        assert s.query(m.Message).filter(m.Message.phone == NEW, m.Message.role == "customer").count() == 1
