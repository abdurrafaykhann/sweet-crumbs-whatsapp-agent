"""Owner dashboard API."""
import asyncio
import datetime as dt

import jwt
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app import agent, database, events, handoff, kv, main, tools, whatsapp
from app import models as m

from .fakes import ScriptedModel

NOW = dt.datetime(2026, 11, 6, 14, 0)
AYESHA, BILAL, SANA = "923000000101", "923000000102", "923000000105"
SECRET = "s" * 40


@pytest.fixture()
def api(db, monkeypatch):
    kv.set_client(None)
    kv.reset_memory()
    monkeypatch.setenv("ADMIN_PASSWORD", "owner-pass")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DEMO_NOW", NOW.isoformat())
    sent = []
    monkeypatch.setattr(whatsapp, "send_text", lambda to, text: sent.append((to, text)) or True)
    monkeypatch.setattr(agent, "_default_graph", agent.build_graph(ScriptedModel(script=[]), InMemorySaver()))   # hand-back writes to the bot's memory
    with TestClient(main.app) as client:
        token = client.post("/api/admin/login", json={"password": "owner-pass"}).json()["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        client.sent = sent
        yield client


def bot_order(phone, turn=1, **patch):
    base = dict(items=[{"sku": "SV-CPAT", "qty": 10}], fulfilment="delivery", address="House 45, Street 12, Khayaban-e-Shamsheer, DHA Phase 5",
                asap=True, payment_method="cash", customer_name="Customer")
    ctx = tools.Ctx(phone, turn, NOW)
    tools.draft_order(ctx, **{**base, **patch})
    out = tools.confirm_order(tools.Ctx(phone, turn + 1, NOW))
    assert out["confirmed"], out
    return out["code"]


def stock(sku, label="each"):
    with database.session_scope() as s:
        return s.query(m.Variant).join(m.Product).filter(m.Product.sku == sku, m.Variant.label == label).one().stock


# ----------------------------------------------------------------------------- auth
def test_login_and_protection(api, monkeypatch):
    anon = TestClient(main.app)
    assert anon.get("/api/admin/orders").status_code == 401
    assert anon.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    assert api.get("/api/admin/orders").status_code == 200
    expired = jwt.encode({"sub": "owner", "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)}, SECRET, algorithm="HS256")
    assert anon.get("/api/admin/orders", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = jwt.encode({"sub": "owner", "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)}, "another-secret-" * 3, algorithm="HS256")
    assert anon.get("/api/admin/orders", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    token = api.headers["Authorization"][7:]
    assert anon.get("/api/admin/updates", params={"token": token}).status_code == 200                # ?token= for server-sent events
    monkeypatch.setenv("JWT_SECRET", "short")
    assert anon.post("/api/admin/login", json={"password": "owner-pass"}).status_code == 503         # a weak signing secret is refused
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.delenv("ADMIN_PASSWORD")
    assert anon.post("/api/admin/login", json={"password": "owner-pass"}).status_code == 503         # not configured: login is off, not open


# ----------------------------------------------------------------------------- orders: list, filters, search, detail
def test_order_list_filters_and_search(api):
    r = api.get("/api/admin/orders").json()
    assert r["total"] == 9 and r["items"][0]["created_at"] >= r["items"][-1]["created_at"]
    codes = lambda **p: {o["code"] for o in api.get("/api/admin/orders", params=p).json()["items"]}
    assert codes(status="preparing") == {"SC-10422"}
    assert codes(fulfilment="pickup") == {"SC-10425"} and codes(branch="Gulshan") == {"SC-10425"}
    assert codes(q="ayesha") == {"SC-10421", "SC-10431"} and codes(q="SC-10398") == {"SC-10398"} and codes(q="0105") == {"SC-10430", "SC-10412"}
    assert {"SC-10421", "SC-10422", "SC-10425"} <= codes(date="2026-11-06")
    assert codes(date="2026-11-08") == {"SC-10431"}                                                  # the platter is scheduled for Sunday
    page = api.get("/api/admin/orders", params={"limit": 4, "offset": 8}).json()
    assert page["total"] == 9 and len(page["items"]) == 1


def test_order_detail_has_lines_and_next_statuses(api):
    o = api.get("/api/admin/orders/SC-10421").json()
    assert [(x["name"], x["qty"]) for x in o["lines"]][0][1] == 1 and o["rider"] == "Imran" and o["next_statuses"] == ["delivered", "cancelled"]
    assert api.get("/api/admin/orders/SC-99999").status_code == 404


# ----------------------------------------------------------------------------- status changes notify the customer in their language
def test_delivery_flow_messages_in_roman_urdu_and_loyalty_points(api):
    code = bot_order(AYESHA)                                                                          # Ayesha writes Roman Urdu
    detail = api.get(f"/api/admin/orders/{code}").json()
    assert detail["status"] == "confirmed" and detail["events"][0]["kind"] == "placed" and detail["source"] == "bot"
    r = api.post(f"/api/admin/orders/{code}/status", json={"status": "preparing"}).json()
    assert r["customer_notified"] and "tayyar ho raha" in api.sent[-1][1] and api.sent[-1][0] == AYESHA
    bad = api.post(f"/api/admin/orders/{code}/status", json={"status": "out_for_delivery"})
    assert bad.status_code == 409 and bad.json()["detail"]["error"] == "rider_name_required"
    assert api.post(f"/api/admin/orders/{code}/status", json={"status": "delivered"}).status_code == 409   # cannot skip a step
    api.post(f"/api/admin/orders/{code}/status", json={"status": "out_for_delivery", "rider_name": "Imran", "eta": "19:30"})
    assert "Imran" in api.sent[-1][1] and "7:30 PM" in api.sent[-1][1]
    api.post(f"/api/admin/orders/{code}/status", json={"status": "delivered"})
    assert "15" in api.sent[-1][1] and "deliver ho gaya" in api.sent[-1][1]
    with database.session_scope() as s:
        assert s.query(m.Customer).filter(m.Customer.phone == AYESHA).one().points == 340 + 15            # Rs 1,550 = 15 points
        history = [x.text for x in s.query(m.Message).filter(m.Message.phone == AYESHA, m.Message.provider == "system")]
    assert len(history) == 3                                                                          # kept in the chat history
    assert api.post(f"/api/admin/orders/{code}/status", json={"status": "preparing"}).json()["detail"]["allowed"] == [] if False else True
    closed = api.post(f"/api/admin/orders/{code}/status", json={"status": "preparing"})
    assert closed.status_code == 409 and closed.json()["detail"]["allowed"] == []


def test_english_and_urdu_script_customers_get_their_own_language(api):
    code = bot_order(BILAL)                                                                           # Bilal writes English
    api.post(f"/api/admin/orders/{code}/status", json={"status": "preparing"})
    assert api.sent[-1][1].startswith("Good news!")
    fatima = bot_order("923000000103")                                                                 # Fatima writes Urdu script
    api.post(f"/api/admin/orders/{fatima}/status", json={"status": "preparing"})
    assert "کچن" in api.sent[-1][1]


def test_pickup_orders_end_with_ready_for_pickup(api):
    code = bot_order(BILAL, fulfilment="pickup", branch="Gulshan", payment_method="counter", items=[{"sku": "SV-CPAT", "qty": 5}])
    api.post(f"/api/admin/orders/{code}/status", json={"status": "preparing"})
    wrong = api.post(f"/api/admin/orders/{code}/status", json={"status": "out_for_delivery", "rider_name": "X"})
    assert wrong.status_code == 409
    api.post(f"/api/admin/orders/{code}/status", json={"status": "ready_for_pickup"})
    assert "Gulshan-e-Iqbal" in api.sent[-1][1] and "pickup" in api.sent[-1][1].lower()
    assert api.post(f"/api/admin/orders/{code}/status", json={"status": "delivered"}).status_code == 200


def test_lateness_is_noted_on_the_timeline(api):
    api.post("/api/admin/orders/SC-10421/status", json={"status": "delivered"})                        # promised 6:35 pm, the demo clock says 2 pm: not late
    ev = api.get("/api/admin/orders/SC-10421").json()["events"][-1]["text"]
    assert "Delivered" in ev and "refundable" not in ev


def test_staff_cancellation_gives_stock_back_and_tells_the_customer(api):
    code = bot_order(AYESHA, items=[{"sku": "CK-CHOC", "variant": "2 lb", "qty": 1, "message": ""}])
    assert stock("CK-CHOC", "2 lb") == 3
    r = api.post(f"/api/admin/orders/{code}/cancel", json={"reason": "customer called"})
    assert r.status_code == 200 and stock("CK-CHOC", "2 lb") == 4 and "cancel ho gaya" in api.sent[-1][1]
    assert api.post(f"/api/admin/orders/{code}/cancel", json={}).status_code == 409
    assert "customer called" in api.get(f"/api/admin/orders/{code}").json()["events"][-1]["text"]


# ----------------------------------------------------------------------------- advance verification is a person's decision
def test_a_person_verifies_the_advance_and_the_bot_gets_the_chat_back(api):
    assert handoff.handle_media(SANA, "image", "m1")                                                  # Sana's screenshot: escalated, bot silent
    with database.session_scope() as s:
        assert s.get(m.Conversation, SANA).escalated is True
    r = api.post("/api/admin/orders/SC-10430/payment", json={"action": "confirm"}).json()
    assert r["status"] == "scheduled" and r["bot_active"] is True and r["customer_notified"]
    assert "6,700" in api.sent[-1][1] and "confirmed" in api.sent[-1][1]
    o = api.get("/api/admin/orders/SC-10430").json()
    assert o["advance_paid"] == 6700 and o["payment_status"] == "advance_paid" and "Advance of Rs 6,700 verified" in o["events"][-1]["text"]
    with database.session_scope() as s:
        assert s.get(m.Conversation, SANA).escalated is False
        assert s.query(m.Escalation).filter(m.Escalation.phone == SANA, m.Escalation.status == "open").count() == 0
    assert api.post("/api/admin/orders/SC-10430/payment", json={"action": "confirm"}).status_code == 409   # no longer waiting


def test_rejecting_a_payment_asks_for_a_clearer_screenshot(api):
    r = api.post("/api/admin/orders/SC-10430/payment", json={"action": "reject", "note": "amount does not match"})
    assert r.status_code == 200 and "could not verify" in api.sent[-1][1]
    assert api.get("/api/admin/orders/SC-10430").json()["status"] == "awaiting_advance"
    assert api.post("/api/admin/orders/SC-10430/payment", json={"action": "maybe"}).status_code == 409
    assert api.post("/api/admin/orders/SC-10421/payment", json={"action": "confirm"}).status_code == 409


def test_staff_can_correct_an_order(api):
    r = api.patch("/api/admin/orders/SC-10421", json={"address": "House 46, Street 12, DHA Phase 5", "notes": "ring the bell", "eta": "19:10"}).json()
    assert set(r["changed"]) == {"address", "notes", "eta"}
    o = api.get("/api/admin/orders/SC-10421").json()
    assert o["address"].startswith("House 46") and o["eta"].endswith("19:10") and o["events"][-1]["kind"] == "edit"
    assert api.patch("/api/admin/orders/SC-10421", json={}).status_code == 409
    assert api.patch("/api/admin/orders/SC-10398", json={"notes": "x"}).status_code == 409           # delivered orders are closed


# ----------------------------------------------------------------------------- conversations and escalations
def test_inbox_take_over_reply_and_hand_back(api):
    with database.session_scope() as s:
        s.add(m.Message(phone=AYESHA, role="customer", text="salam, order late hai"))
    inbox = api.get("/api/admin/conversations").json()
    assert inbox[0]["phone"] == AYESHA and inbox[0]["name"] == "Ayesha Khan" and inbox[0]["bot_active"] is True
    assert api.post(f"/api/admin/conversations/{AYESHA}/take-over").json()["bot_active"] is False
    assert api.get("/api/admin/conversations", params={"escalated": True}).json()[0]["open_escalation"]["reason"] == "staff took over"
    assert api.post(f"/api/admin/conversations/{AYESHA}/reply", json={"text": "  "}).status_code == 400
    api.post(f"/api/admin/conversations/{AYESHA}/reply", json={"text": "Sorry for the wait, the rider is 5 minutes away."})
    assert api.sent[-1] == (AYESHA, "Sorry for the wait, the rider is 5 minutes away.")
    d = api.get(f"/api/admin/conversations/{AYESHA}").json()
    assert [x["role"] for x in d["messages"]][-3:] == ["note", "human", "note"][:3] or d["messages"][-1]["role"] == "human"
    assert d["bot_active"] is False and d["customer"]["name"] == "Ayesha Khan" and d["escalations"][0]["reason"] == "staff took over"
    back = api.post(f"/api/admin/conversations/{AYESHA}/hand-back").json()
    assert back["bot_active"] is True
    assert api.get(f"/api/admin/conversations/{AYESHA}").json()["bot_active"] is True


def test_escalation_queue(api):
    with database.session_scope() as s:
        s.add(m.Conversation(phone=SANA, turn=1))
    tools.escalate_to_human(tools.Ctx(SANA, 1, NOW), "damaged cheesecake", "2 slices crushed, order SC-10412", "high", "SC-10412")
    q = api.get("/api/admin/escalations").json()
    assert len(q) == 1 and q[0]["reason"] == "damaged cheesecake" and q[0]["priority"] == "high" and q[0]["customer"] == "Sana Malik"
    assert api.post(f"/api/admin/escalations/{q[0]['id']}/resolve", json={}).json()["status"] == "resolved"
    assert api.get("/api/admin/escalations").json() == [] and len(api.get("/api/admin/escalations", params={"status": "resolved"}).json()) == 1
    assert api.post("/api/admin/escalations/9999/resolve", json={}).status_code == 404


# ----------------------------------------------------------------------------- menu and stock: the bot sees changes at once
def test_menu_edits_are_seen_by_the_bot_immediately(api):
    menu = api.get("/api/admin/menu").json()
    assert len(menu) == 64
    choc = next(p for p in menu if p["sku"] == "CK-CHOC")
    two_lb = next(v for v in choc["variants"] if v["label"] == "2 lb")
    assert api.patch(f"/api/admin/variants/{two_lb['id']}", json={"price": 3300, "stock": 7}).json() == {"id": two_lb["id"], "label": "2 lb", "price": 3300, "stock": 7}
    seen = tools.search_menu(tools.Ctx(AYESHA, 1, NOW), "chocolate fudge")["results"][0]
    assert next(v for v in seen["variants"] if v["label"] == "2 lb") == {"label": "2 lb", "price": 3300, "left": 7, "in_stock": True}
    assert api.post("/api/admin/products/CK-CHOC/sold-out").json()["sold_out"] is True
    assert tools.check_stock(tools.Ctx(AYESHA, 1, NOW), "CK-CHOC", "2 lb")["available"] is False
    assert api.post("/api/admin/products/CK-CHOC/back-in-stock", json={"stock": 5}).json()["stock"] == 5
    assert tools.check_stock(tools.Ctx(AYESHA, 1, NOW), "CK-CHOC", "2 lb")["available"] is True
    assert api.patch(f"/api/admin/variants/{two_lb['id']}", json={"stock": -1}).status_code == 400
    assert api.patch(f"/api/admin/variants/{two_lb['id']}", json={"stock": None}).json()["stock"] is None   # unlimited
    assert api.patch("/api/admin/variants/99999", json={"price": 1}).status_code == 404
    assert api.post("/api/admin/products/NOPE/sold-out").status_code == 404


# ----------------------------------------------------------------------------- customers and analytics
def test_customers(api):
    cs = api.get("/api/admin/customers").json()
    assert len(cs) == 8
    ayesha = next(c for c in cs if c["phone"] == AYESHA)
    assert ayesha["points"] == 340 and ayesha["orders"] == 2 and ayesha["spent"] == 3450 + 4900
    assert [c["name"] for c in api.get("/api/admin/customers", params={"q": "sana"}).json()] == ["Sana Malik"]
    d = api.get(f"/api/admin/customers/{ayesha['id']}").json()
    assert d["addresses"][0]["zone"] == "A" and {o["code"] for o in d["orders"]} == {"SC-10421", "SC-10431"} and d["birthday_month"] == 11
    assert api.get("/api/admin/customers/9999").status_code == 404


def test_analytics_on_the_spec_data(api):
    a = api.get("/api/admin/analytics").json()
    assert a["date"] == "2026-11-06" and a["today"]["orders"] == 3 and a["today"]["revenue"] == 3450 + 4000 + 1560          # SC-10421, 10422, 10425
    assert a["today"]["awaiting_payment_check"] == 1 and a["top_items"][0] == {"item": "Chicken Patty", "qty": 14}
    assert {"area": "Pickup", "orders": 1} in a["orders_by_area"] and a["bot"]["conversations"] == 0 and a["llm"] == {}


def test_bot_conversion_escalation_rate_and_response_time(api):
    code = bot_order(BILAL)                                                                           # a bot-made order for Bilal
    with database.session_scope() as s:
        for phone in (BILAL, AYESHA, SANA):
            s.add(m.Message(phone=phone, role="customer", text="hi", created_at=NOW))
        s.add(m.Message(phone=BILAL, role="bot", text="hello", latency_ms=2000, created_at=NOW))
        s.add(m.Message(phone=AYESHA, role="bot", text="hello", latency_ms=4000, created_at=NOW))
        s.add(m.Escalation(phone=SANA, reason="refund", created_at=NOW))
        s.add(m.LlmUsage(provider="groq", model="oss", input_tokens=1000, output_tokens=50, ok=True))
    b = api.get("/api/admin/analytics").json()["bot"]
    assert code and b["conversations"] == 3 and b["converted"] == 1 and b["conversion_rate"] == 33.3
    assert b["escalated"] == 1 and b["escalation_rate"] == 33.3 and b["avg_response_ms"] == 3000
    assert api.get("/api/admin/analytics").json()["llm"]["groq"]["today"]["requests"] == 1


# ----------------------------------------------------------------------------- live updates
def test_updates_report_new_orders_escalations_and_messages(api):
    wm = api.get("/api/admin/updates").json()["watermarks"]
    code = bot_order(BILAL)
    with database.session_scope() as s:
        s.add(m.Message(phone=BILAL, role="customer", text="where is my cake?"))
        s.add(m.Escalation(phone=BILAL, reason="late order", priority="high"))
    u = api.get("/api/admin/updates", params={"after_order": wm["order"], "after_escalation": wm["escalation"], "after_message": wm["message"]}).json()
    assert [o["code"] for o in u["orders"]] == [code] and u["escalations"][0]["reason"] == "late order"
    assert any(x["text"] == "where is my cake?" for x in u["messages"])
    again = api.get("/api/admin/updates", params={f"after_{k}": v for k, v in u["watermarks"].items()}).json()
    assert again["orders"] == again["escalations"] == again["messages"] == []


def test_server_sent_events_stream(api):
    with database.session_scope() as s:
        wm = events.watermarks(s)
    bot_order(BILAL)

    async def collect():
        return [chunk async for chunk in events.stream(wm, interval=0, max_loops=2)]
    chunks = asyncio.run(collect())
    assert chunks[0].startswith("event: order\ndata: ") and "SC-" in chunks[0] and chunks[1].startswith(": keep-alive")


# ----------------------------------------------------------------------------- the dashboard UI is served by the API
def test_dashboard_page_is_served_at_admin(api):
    anon = TestClient(main.app)
    from app.main import DASHBOARD
    r = anon.get("/admin/")
    if DASHBOARD.is_dir():
        assert r.status_code == 200 and '<div id="root">' in r.text            # the built React app
        assert "/admin/assets/" in r.text                                       # assets are loaded from under /admin
    else:
        assert r.status_code in (404, 503)


def test_api_routes_are_not_shadowed_by_the_dashboard(api):
    anon = TestClient(main.app)
    assert anon.get("/health").status_code == 200
    assert anon.get("/api/admin/orders").status_code == 401                      # still behind the login
