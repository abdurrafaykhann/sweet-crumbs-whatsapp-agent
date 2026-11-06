"""Infrastructure tests. Run on SQLite (default) and on Postgres: DATABASE_URL=postgresql://... pytest test_infra.py"""
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time

os.environ.setdefault("AGENT_DB", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ["AGENT_BRAIN"] = "rule"

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, kv, main, tools  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    db.init(reset=True)
    kv.set_client(fakeredis.FakeRedis(decode_responses=True))
    main.WA_APP_SECRET = ""
    sent = []
    main.send_whatsapp = lambda to, text: sent.append((to, text))
    main.mark_read = lambda mid: None
    yield sent


def wa_payload(mid, text, sender="923001112233", typ="text"):
    msg = {"id": mid, "from": sender, "type": typ}
    if typ == "text":
        msg["text"] = {"body": text}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def test_webhook_replies_and_dedupes(fresh):
    c = TestClient(main.app)
    p = wa_payload("wamid.1", "2 brownies chahiye")
    assert c.post("/webhook", json=p).status_code == 200
    assert len(fresh) == 1 and "kaunsi" in fresh[0][1].lower()  # brownies come in 3 flavours: asks which
    c.post("/webhook", json=p)  # Meta redelivers the same message id
    assert len(fresh) == 1, "duplicate delivery must not be answered twice"


def test_signature_enforced(fresh):
    main.WA_APP_SECRET = "s3cret"
    c = TestClient(main.app)
    body = json.dumps(wa_payload("wamid.2", "hello")).encode()
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 403
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert c.post("/webhook", content=body, headers={"x-hub-signature-256": good}).status_code == 200
    assert len(fresh) == 1


def test_non_text_gets_polite_reply(fresh):
    c = TestClient(main.app)
    c.post("/webhook", json=wa_payload("wamid.3", "", typ="audio"))
    assert len(fresh) == 1 and "text" in fresh[0][1].lower()


def test_verify_handshake():
    c = TestClient(main.app)
    ok = c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": main.VERIFY_TOKEN, "hub.challenge": "42"})
    assert ok.text == "42"
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"}).status_code == 403


def test_rate_limit():
    assert all(kv.allow("u1", limit=3, window=60) for _ in range(3))
    assert not kv.allow("u1", limit=3, window=60)
    assert kv.allow("u2", limit=3, window=60)


def test_health_reports_backends():
    h = TestClient(main.app).get("/health").json()
    assert h["db"] == ("postgres" if db.PG else "sqlite") and h["kv"] == "redis"


def _stock(name_var):
    c = db.conn()
    r = c.execute("SELECT v.stock FROM variations v JOIN products p ON p.id=v.product_id WHERE p.name || ' - ' || v.name = ?", (name_var,)).fetchone()
    c.close()
    return r[0]


def _confirm_turn(who):
    c = db.conn()
    s = db.get_session(c, who)
    s["turn"] += 1  # the customer's next message (their "yes")
    db.save_session(c, who, s)
    c.commit()
    c.close()


def test_two_buyers_one_last_item_no_oversell():
    """Both customers confirm at the same moment for the last 4 cakes: exactly one order may succeed."""
    c = db.conn()
    c.execute("UPDATE variations SET stock=4 WHERE name='1 lb' AND product_id=(SELECT id FROM products WHERE name='Chocolate Fudge Cake')")
    c.commit()
    c.close()
    for who in ("a", "b"):
        assert tools.run_tool(who, "propose_order", {"items": [{"product": "chocolate cake", "variation": "1 lb", "qty": 4}]})["ok"]
        _confirm_turn(who)
    results = {}

    def buy(who):
        results[who] = tools.run_tool(who, "create_order", {})

    ts = [threading.Thread(target=buy, args=(w,)) for w in ("a", "b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results.values() if r.get("ok")) == 1, results
    assert _stock("Chocolate Fudge Cake - 1 lb") == 0
    c = db.conn()
    assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    c.close()


def test_multi_item_order_total():
    r = tools.run_tool("m", "propose_order", {"items": [{"product": "chocolate cake", "variation": "1 lb", "qty": 1},
                                                          {"product": "brownie", "variation": "plain fudge", "qty": 2}],
                                              "delivery": "delivery", "address": "House 1, Gulshan", "note": "Happy Birthday Hina"})
    assert r["ok"] and r["total"] == 1900 + 400 + 150  # items + Rs 150 delivery fee (under Rs 3000)
    assert tools.run_tool("m", "create_order", {})["error"] == "customer_has_not_confirmed_yet"


def test_variation_required_before_ordering():
    r = tools.run_tool("v", "propose_order", {"items": [{"product": "brownie", "qty": 2}]})
    assert r["error"] == "variation_needed" and {o["name"] for o in r["options"]} == {"Plain Fudge", "Classic Walnut", "Nutella-Stuffed"}


def test_bare_product_word_does_not_pick_a_variation():
    r = tools.run_tool("v", "propose_order", {"items": [{"product": "bread", "qty": 1}]})
    assert r["error"] == "variation_needed"  # "bread" must not silently become "Milk Bread"


def test_lead_time_and_opening_hours():
    import datetime as dt
    tools.NOW = lambda: dt.datetime(2026, 11, 6, 10, 0)
    try:
        big = {"items": [{"product": "chocolate cake", "variation": "3 lb", "qty": 1}]}
        r = tools.run_tool("l", "propose_order", {**big, "ready_at": "2026-11-06 12:00"})
        assert r["error"] == "too_soon" and r["earliest"] == "2026-11-07 10:00"  # 3 lb needs 24 hours
        assert tools.run_tool("l", "propose_order", {**big, "ready_at": "2026-11-07 10:00"})["ok"]
        assert tools.run_tool("l", "propose_order", {**big, "ready_at": "2026-11-07 23:30"})["error"] == "outside_hours"
        small = tools.run_tool("l2", "propose_order", {"items": [{"product": "chocolate cake", "variation": "1 lb", "qty": 1}]})
        assert small["ok"] and "4:00 PM" in small["ready_at_text"]  # 6 hours after 10:00
        tools.NOW = lambda: dt.datetime(2026, 11, 6, 21, 30)  # late evening: kitchen opens next morning
        late = tools.run_tool("l3", "propose_order", {"items": [{"product": "chocolate cake", "variation": "1 lb", "qty": 1}]})
        assert "9:00 AM" in late["ready_at_text"] and "7 Nov" in late["ready_at_text"]
    finally:
        tools.NOW = tools._now


def test_tracking_gives_status_and_team_number():
    tools.run_tool("t", "propose_order", {"items": [{"product": "cupcake", "variation": "vanilla", "qty": 2}]})
    _confirm_turn("t")
    placed = tools.run_tool("t", "create_order", {})
    assert placed["ok"] and "+92" in placed["team_phone"]
    st = tools.run_tool("t", "order_status", {})
    assert st["found"] and st["status"] == "confirmed" and "+92" in st["team_phone"] and "ready_at_text" in st
    assert tools.run_tool("someone-else", "order_status", {"order_id": placed["order_id"]})["found"] is False  # privacy
    nf = tools.run_tool("nobody", "order_status", {})
    assert nf["found"] is False and "+92" in nf["team_phone"]


def test_shop_updates_status_via_api():
    tools.run_tool("s", "propose_order", {"items": [{"product": "cupcake", "variation": "vanilla", "qty": 1}]})
    _confirm_turn("s")
    oid = tools.run_tool("s", "create_order", {})["order_id"]
    c = TestClient(main.app)
    assert c.post(f"/api/orders/{oid}/status", json={"status": "out_for_delivery"}).status_code == 200
    assert c.post(f"/api/orders/{oid}/status", json={"status": "flying"}).status_code == 400
    assert tools.run_tool("s", "order_status", {})["status"] == "out_for_delivery"
    assert tools.run_tool("s", "cancel_order", {"order_id": oid})["error"].startswith("cannot_cancel")  # too late to cancel


def test_cancel_restores_stock():
    before = _stock("Cupcakes - Chocolate")
    tools.run_tool("x", "propose_order", {"items": [{"product": "cupcake", "variation": "chocolate", "qty": 5}]})
    _confirm_turn("x")
    oid = tools.run_tool("x", "create_order", {})["order_id"]
    assert _stock("Cupcakes - Chocolate") == before - 5
    assert tools.run_tool("x", "cancel_order", {"order_id": oid})["ok"]
    assert _stock("Cupcakes - Chocolate") == before


def test_menu_has_11_items_with_full_details():
    c = db.conn()
    ps = c.execute("SELECT * FROM products").fetchall()
    assert len(ps) == 11
    for p in ps:
        assert p["ingredients"] and p["allergens"] and p["storage"] and p["description"]
        assert len(db.list_variations(c, p["id"])) >= 2
    c.close()


# ------------------------------------------------------------------ payments, delivery details, order changes
def place(who, items, **kw):
    r = tools.run_tool(who, "propose_order", {"items": items, **kw})
    assert r["ok"], r
    _confirm_turn(who)
    out = tools.run_tool(who, "create_order", {})
    assert out["ok"], out
    return out


BIG = [{"product": "chocolate cake", "variation": "3 lb", "qty": 1}]       # Rs 5200: needs a 50% advance
SMALL = [{"product": "cupcake", "variation": "vanilla", "qty": 2}]          # Rs 300


def test_small_cash_order_is_confirmed_immediately():
    o = place("p1", SMALL)
    assert o["status"] == "confirmed" and o["payment_required"] is False


def test_large_order_requires_advance_and_waits_for_team(fresh):
    p = tools.run_tool("p2", "propose_order", {"items": BIG, "payment": "cash"})
    assert p["advance_required"] and p["payment_mode"] == "advance" and p["pay_now"] == 2600  # cash is not allowed for 3 lb: 50% advance
    _confirm_turn("p2")
    o = tools.run_tool("p2", "create_order", {})
    assert o["status"] == "awaiting_payment" and o["payment_required"] and o["pay_amount"] == 2600 and o["balance_on_delivery"] == 2600
    assert len(o["methods"]) == 3 and all(m["account_title"] for m in o["methods"])
    st = tools.run_tool("p2", "order_status", {})
    assert st["status"] == "awaiting_payment" and st["amount_to_pay_now"] == 2600 and len(st["methods"]) == 3
    sub = tools.run_tool("p2", "submit_payment", {"reference": "TID 998877", "method": "Easypaisa"})
    assert sub["ok"] and sub["status"] == "payment_submitted"
    assert tools.run_tool("p2", "order_status", {})["status"] == "payment_submitted"  # the AI cannot confirm; it only submits

    c = TestClient(main.app)
    assert c.post(f"/api/orders/{o['order_id']}/payment", json={"action": "confirm"}).status_code == 200
    st = tools.run_tool("p2", "order_status", {})
    assert st["status"] == "confirmed" and st["amount_paid_verified"] == 2600 and st["balance_due_on_delivery_or_pickup"] == 2600
    assert any(to == "p2" and "confirmed" in text.lower() and f"#{o['order_id']}" in text for to, text in fresh)  # customer was told
    msgs = c.get("/api/team-messages", params={"customer": "p2"}).json()
    assert len(msgs) == 1 and "confirmed" in msgs[0]["text"].lower()
    assert c.post(f"/api/orders/{o['order_id']}/payment", json={"action": "confirm"}).status_code == 409  # cannot confirm twice


def test_pay_online_in_full_for_a_small_order():
    p = tools.run_tool("p3", "propose_order", {"items": SMALL, "payment": "online"})
    assert p["payment_mode"] == "online" and p["pay_now"] == 300 and p["advance_required"] is False
    _confirm_turn("p3")
    assert tools.run_tool("p3", "create_order", {})["status"] == "awaiting_payment"


def test_team_can_reject_payment(fresh):
    o = place("p4", BIG)
    tools.run_tool("p4", "submit_payment", {"reference": "TID 1"})
    r = TestClient(main.app).post(f"/api/orders/{o['order_id']}/payment", json={"action": "reject", "reason": "amount is short"})
    assert r.status_code == 200
    st = tools.run_tool("p4", "order_status", {})
    assert st["status"] == "awaiting_payment" and "short" in st["last_payment_check"]
    assert any("verify" in text.lower() and "short" in text for to, text in fresh)


def test_screenshot_through_webhook_submits_payment_for_verification(fresh):
    o = place("923001112233", BIG)
    img = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.img1", "from": "923001112233", "type": "image", "image": {"id": "MEDIA1"}}]}}]}]}
    assert TestClient(main.app).post("/webhook", json=img).status_code == 200
    assert tools.run_tool("923001112233", "order_status", {})["status"] == "payment_submitted"
    assert fresh and "team" in fresh[-1][1].lower()


def test_screenshot_without_pending_payment_is_handled_politely(fresh):
    img = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.img2", "from": "923009998877", "type": "image", "image": {"id": "M2"}}]}}]}]}
    TestClient(main.app).post("/webhook", json=img)
    assert fresh and "+92" in fresh[-1][1]


def test_unpaid_reservation_expires_and_releases_stock():
    before = _stock("Chocolate Fudge Cake - 3 lb")
    o = place("p5", BIG)
    assert _stock("Chocolate Fudge Cake - 3 lb") == before - 1
    c = db.conn()
    c.execute("UPDATE orders SET pay_by=? WHERE id=?", (time.time() - 5, o["order_id"]))
    c.commit()
    c.close()
    st = tools.run_tool("p5", "order_status", {})
    assert st["status"] == "cancelled" and "not received" in st["status_text"]
    assert _stock("Chocolate Fudge Cake - 3 lb") == before


def test_delivery_needs_an_address_in_a_served_area():
    assert tools.run_tool("d1", "propose_order", {"items": SMALL, "delivery": "delivery"})["error"] == "address_needed"
    assert tools.run_tool("d1", "propose_order", {"items": SMALL, "delivery": "delivery", "address": "House 5, DHA Phase 6"})["error"] == "area_unclear"
    ok = tools.run_tool("d1", "propose_order", {"items": SMALL, "delivery": "delivery", "address": "House 5, Block 7, Gulshan-e-Iqbal"})
    assert ok["ok"] and ok["fee"] == 150


def test_customer_can_change_address_and_time_until_baking_starts():
    o = place("c1", SMALL, delivery="delivery", address="Flat 3, Gulshan-e-Iqbal Block 2")
    assert tools.run_tool("c1", "update_order", {"address": "House 9, Clifton Block 4"})["ok"]
    assert "Clifton" in tools.run_tool("c1", "order_status", {})["address"]
    assert tools.run_tool("c1", "update_order", {"address": "Somewhere in Lahore"})["error"] == "area_unclear"
    TestClient(main.app).post(f"/api/orders/{o['order_id']}/status", json={"status": "preparing"})
    assert tools.run_tool("c1", "update_order", {"note": "extra candles"})["error"] == "too_late_to_change"


def test_cancel_after_payment_goes_to_the_team():
    o = place("c2", BIG)
    tools.run_tool("c2", "submit_payment", {"reference": "TID 5"})
    r = tools.run_tool("c2", "cancel_order", {"order_id": o["order_id"]})
    assert r["error"] == "payment_involved" and "+92" in r["team_phone"]
    c = db.conn()
    assert c.execute("SELECT COUNT(*) FROM escalations WHERE reason LIKE '%refund%'").fetchone()[0] == 1
    c.close()
    assert tools.run_tool("c2", "order_status", {})["status"] == "payment_submitted"  # not cancelled behind the team's back


def test_cancel_unpaid_reservation_releases_stock():
    before = _stock("Chocolate Fudge Cake - 3 lb")
    o = place("c3", BIG)
    assert tools.run_tool("c3", "cancel_order", {"order_id": o["order_id"]})["ok"]
    assert _stock("Chocolate Fudge Cake - 3 lb") == before


def test_identical_order_cannot_be_placed_twice():
    o = place("dup1", SMALL)
    r = tools.run_tool("dup1", "propose_order", {"items": SMALL})
    assert r["error"] == "duplicate_order" and r["order_id"] == o["order_id"]
    other = tools.run_tool("dup1", "propose_order", {"items": [{"product": "cupcake", "variation": "vanilla", "qty": 3}]})
    assert other["ok"]  # a different order is fine


def test_delivery_fee_reason_is_explained():
    r = tools.run_tool("fee1", "propose_order", {"items": [{"product": "pineapple cake", "variation": "2 lb", "qty": 1}], "delivery": "delivery",
                                                 "address": "House 1, Gulshan-e-Iqbal"})
    assert r["fee"] == 0 and "Rs 3000 or more" in r["delivery_fee_note"]  # the free delivery is because of the total, not the area
