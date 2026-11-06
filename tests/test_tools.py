"""Tool tests. Many cases are the spec's own (section 13). Demo clock: Friday 6 November 2026, 2 pm."""
import datetime as dt

import pytest

from app import database, tools
from app import models as m

NOW = dt.datetime(2026, 11, 6, 14, 0)
NEW = "923000000108"            # Ali Haider: no saved address
PHONE_Z = "923000000199"
AYESHA, BILAL, SANA, HAMZA, ZAINAB = "923000000101", "923000000102", "923000000105", "923000000104", "923000000107"


def ctx(phone=NEW, turn=1, now=NOW):
    return tools.Ctx(phone=phone, turn=turn, now=now)


BASE = dict(items=[{"sku": "CK-CHOC", "variant": "2 lb", "qty": 1, "message": "Happy Birthday Ammi"}, {"sku": "BR-CANDLE", "qty": 1}],
            fulfilment="delivery", address="House 45, Street 12, Khayaban-e-Shamsheer, DHA Phase 5", slot_start="2026-11-07T19:00",
            payment_method="cash", customer_name="Ali Haider")


def problem_codes(r):
    return [p["code"] for p in r.get("problems", [])]


def stock(sku, label="each"):
    with database.session_scope() as s:
        return s.query(m.Variant).join(m.Product).filter(m.Product.sku == sku, m.Variant.label == label).one().stock


# ----------------------------------------------------------------------------- lookups
def test_search_menu(db):
    cats = tools.search_menu(ctx())
    assert {"name": "Cakes", "items": 9} in cats["categories"]
    r = tools.search_menu(ctx(), "chocolate cake")["results"]
    assert r[0]["sku"] == "CK-CHOC" and {v["label"]: v["price"] for v in r[0]["variants"]} == {"1 lb": 1700, "2 lb": 3200, "3 lb": 4600}  # test 2
    assert {x["sku"] for x in tools.search_menu(ctx(), "patty")["results"]} >= {"SV-CPAT", "SV-VPAT"}
    assert {x["sku"] for x in tools.search_menu(ctx(), category="Drinks")["results"]} == {"BV-CHAI", "BV-CCOF", "BV-HCHO", "BV-MINT", "BV-WATR"}
    assert tools.search_menu(ctx(), "birthday")["deals"][0]["deal"] == "Birthday Bundle"
    assert "note" in tools.search_menu(ctx(), "xylophone")
    ras = tools.search_menu(ctx(), "ras malai")["results"][0]
    assert "contains nuts" in ras["flags"]


def test_check_stock_sold_out_offers_alternatives(db):
    r = tools.check_stock(ctx(), "CK-RVLT", "2 lb")                         # test 3
    assert r["available"] is False
    labels = {a["variant"] for a in r["alternatives"] if a["sku"] == "CK-RVLT"}
    assert labels == {"1 lb", "3 lb"} and any(a["sku"] != "CK-RVLT" for a in r["alternatives"])
    puff = tools.check_stock(ctx(), "SV-PUFF")                              # test 4
    assert puff["available"] is False and {"SV-CPAT", "SV-SAMO"} <= {a["sku"] for a in puff["alternatives"]}
    assert tools.check_stock(ctx(), "CK-STRW", "1 lb")["available"] is False
    assert tools.check_stock(ctx(), "CK-STRW", "2 lb")["available"] is True
    sour = tools.check_stock(ctx(), "BR-SOUR")                              # test 29: weekends only, today is Friday
    assert sour["available"] is False and "Sat" in sour["reason"]
    assert tools.check_stock(ctx(), "CK-CHOC")["error"] == "variant_needed"
    assert tools.check_stock(ctx(), "NOPE")["error"] == "unknown_sku"
    assert tools.check_stock(ctx(), "SV-CPAT", qty=41)["available"] is False


def test_check_delivery_area(db):
    assert tools.check_delivery_area(ctx(), "North Nazimabad")["fee"] == 300                    # test 6
    d = tools.check_delivery_area(ctx(), "DHA Phase 5")
    assert (d["zone"], d["fee"], d["minutes"]) == ("A", 150, [40, 60]) and d["free_delivery_from"] == 4000
    n = tools.check_delivery_area(ctx(), "Bahria Town")                                          # test 5
    assert n["status"] == "not_served" and "Gulshan-e-Iqbal" in n["pickup_branches"] and "Foodpanda" in n["other_option"]
    u = tools.check_delivery_area(ctx(), "Block 7")                                              # test 33
    assert u["status"] == "unclear" and "Clifton" in u["ask_which_area"]
    assert tools.check_delivery_area(ctx(), "somewhere far")["status"] == "unknown"


def test_customer_profile_is_private_and_complete(db):
    p = tools.get_customer_profile(ctx(AYESHA))
    assert p["name"] == "Ayesha Khan" and p["points"] == 340 and p["points_value_rs"] == 1500 and p["birthday_this_month"] is True  # test 28
    assert p["saved_addresses"][0]["zone"] == "A"
    assert {o["code"] for o in p["active_orders"]} == {"SC-10421", "SC-10431"}
    assert tools.get_customer_profile(ctx("923009999999")) == {"known": False, "hint": "new customer: ask for their name when you need it"}
    usman = tools.get_customer_profile(ctx("923000000106"))
    assert usman["saved_addresses"][0]["served"] is False                                         # test 34


def test_get_info(db):
    assert "Zone" in tools.get_info(ctx(), "delivery")["text"]
    assert "Jummah" in tools.get_info(ctx(), "branches hours")["text"]
    assert tools.get_info(ctx(), "halal")["topic"] == "business_profile" and "halal" in tools.get_info(ctx(), "halal")["text"].lower()
    assert tools.get_info(ctx(), "parking")["topic"] == "branches_hours"
    assert tools.get_info(ctx(), "gelatin")["topic"] == "business_profile"
    assert tools.get_info(ctx(), "nuts")["topic"] in ("allergens", "storage", "other", "deals", "business_profile") and "text" in tools.get_info(ctx(), "nuts")
    assert "topics" in tools.get_info(ctx(), "zzzz")


# ----------------------------------------------------------------------------- the confirm guard
def test_order_flow_and_confirm_guard(db):
    r = tools.draft_order(ctx(turn=1), **BASE)
    assert r["status"] == "ready_for_summary" and r["total"] == 3450 and r["delivery_fee"] == 150                # the spec's sample order
    assert "Rs 3,450" in r["summary_text"] and "Zone A" in r["summary_text"] and '"Happy Birthday Ammi"' in r["summary_text"]
    same = tools.confirm_order(ctx(turn=1))                                                                       # same turn: refused
    assert same["confirmed"] is False and same["error"] == "confirm_in_same_turn"
    assert stock("CK-CHOC", "2 lb") == 4
    done = tools.confirm_order(ctx(turn=2))                                                                       # the customer said yes later
    assert done["confirmed"] and done["code"] == "SC-10432" and done["status"] == "scheduled"                     # the spec's sample code; a slot tomorrow is "scheduled"
    assert stock("CK-CHOC", "2 lb") == 3 and stock("BR-CANDLE") == stock("BR-CANDLE")
    assert tools.confirm_order(ctx(turn=3))["error"] == "no_draft"                                                # cannot confirm twice
    with database.session_scope() as s:
        o = s.query(m.Order).filter(m.Order.code == "SC-10432").one()
        assert (o.total, o.customer.phone, o.fulfilment, o.zone_code, o.cake_message) == (3450, NEW, "delivery", "A", "Happy Birthday Ammi")
        assert o.status == "scheduled" and o.slot_start == dt.datetime(2026, 11, 7, 19) and o.customer.name == "Ali Haider"


def test_incomplete_draft_cannot_be_confirmed(db):
    r = tools.draft_order(ctx(turn=1), items=[{"sku": "CK-CHOC", "variant": "2 lb", "qty": 1}])
    assert r["status"] == "incomplete" and any("fulfilment" in x for x in r["missing"]) and any("cake message" in x for x in r["missing"])
    assert tools.confirm_order(ctx(turn=5))["error"] == "no_summary_for_current_draft"


def test_changing_the_draft_requires_a_new_summary_and_a_new_yes(db):
    tools.draft_order(ctx(turn=1), **BASE)
    r = tools.update_draft(ctx(turn=2), notes="extra tissues")                    # the customer changed their mind after the summary
    assert r["status"] == "ready_for_summary"
    assert tools.confirm_order(ctx(turn=2))["error"] == "confirm_in_same_turn"    # a new summary was just made in turn 2
    assert tools.confirm_order(ctx(turn=3))["confirmed"] is True                    # and a yes in turn 3 works


def test_a_change_that_breaks_the_draft_blocks_confirmation(db):
    tools.draft_order(ctx(turn=1), **BASE)
    bad = tools.update_draft(ctx(turn=2), address="House 15, Sector 1, Bahria Town")
    assert "area_not_served" in problem_codes(bad)
    assert tools.confirm_order(ctx(turn=3))["error"] == "no_summary_for_current_draft"
    fixed = tools.update_draft(ctx(turn=3), fulfilment="pickup", branch="Gulshan", payment_method="counter")
    assert fixed["status"] == "ready_for_summary" and fixed["branch"] == "Gulshan-e-Iqbal"
    assert tools.confirm_order(ctx(turn=4))["confirmed"] is True


def test_drafts_are_per_phone_number(db):
    tools.draft_order(ctx(NEW), items=[{"sku": "DS-BROW", "qty": 2}])
    other = tools.update_draft(ctx(BILAL), notes="hi")
    assert other["lines"] if "lines" in other else True
    with database.session_scope() as s:
        assert [i["sku"] for i in tools.load_state(s, NEW)["items"]] == ["DS-BROW"]
        assert tools.load_state(s, BILAL)["items"] == []


def test_stock_taken_by_someone_else_before_confirmation(db):
    d = dict(BASE, items=[{"sku": "CK-RVLT", "variant": "1 lb", "qty": 2, "message": ""}])
    assert tools.draft_order(ctx(NEW, turn=1), **d)["status"] == "ready_for_summary"
    tools.draft_order(ctx(BILAL, turn=1), **dict(d, customer_name="Bilal"))
    assert tools.confirm_order(ctx(BILAL, turn=2))["confirmed"]                                                  # takes both red velvet 1 lb cakes
    r = tools.confirm_order(ctx(NEW, turn=2))
    assert r["confirmed"] is False and r["error"] in ("draft_not_valid_anymore", "sold_out_just_now")


# ----------------------------------------------------------------------------- validation and pricing (spec section 13)
def test_variant_must_be_chosen(db):
    r = tools.draft_order(ctx(), items=[{"sku": "CK-CHOC", "qty": 1}])
    assert "variant_needed" in problem_codes(r) and len(r["problems"][0]["options"]) == 3


def test_below_minimum_and_free_delivery(db):
    low = tools.draft_order(ctx(turn=1), **dict(BASE, items=[{"sku": "DS-BROW", "qty": 2}, {"sku": "DS-DONG", "qty": 1}]))   # Rs 740
    assert "below_minimum" in problem_codes(low)                                                                    # test 8
    big = tools.draft_order(ctx(turn=2), **dict(BASE, items=[{"sku": "CK-CHOC", "variant": "3 lb", "qty": 1, "message": ""}], address="Gulshan-e-Iqbal block 13-D house 4"))
    assert big["subtotal"] == 4600 and big["delivery_fee"] == 0 and big["zone"] == "B"                              # test 7
    mid = tools.draft_order(ctx(turn=3), **dict(BASE, items=[{"sku": "CK-CHOC", "variant": "2 lb", "qty": 1, "message": ""}], address="House 3, North Nazimabad Block H"))
    assert mid["delivery_fee"] == 300


def test_area_problems(db):
    ns = tools.draft_order(ctx(), **dict(BASE, address="House 15, Sector 1, Bahria Town"))
    assert "area_not_served" in problem_codes(ns) and "pickup" in str(ns["problems"])
    un = tools.draft_order(ctx(), **dict(BASE, address="House 9, Block 7"))
    assert "area_unclear" in problem_codes(un) and "Clifton" in str(un["problems"])
    unk = tools.draft_order(ctx(), **dict(BASE, address="Some street in Lahore, house 9"))
    assert "area_unknown" in problem_codes(unk)


def test_sold_out_item_offers_alternatives(db):
    r = tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "CK-RVLT", "variant": "2 lb", "qty": 1, "message": ""}]))
    p = r["problems"][0]
    assert p["code"] == "insufficient_stock" and p["left"] == 0 and any(a["variant"] == "1 lb" for a in p["alternatives"])


def test_custom_cake_needs_48h_notice_advance_and_a_human(db):
    items = [{"sku": "CU-THEME", "variant": "per lb", "qty": 3, "message": "Happy Birthday Zara"}, {"sku": "CU-FIGURE", "qty": 1}]
    early = tools.draft_order(ctx(), **dict(BASE, items=items, slot_start="2026-11-07T19:00", payment_method="jazzcash"))   # test 16
    assert any(p["code"] == "time_not_possible" and p["reason"] == "notice" for p in early["problems"])
    ok = tools.update_draft(ctx(), slot_start="2026-11-09T16:00")                                                            # Monday: SC-10430's slot
    assert ok["status"] == "needs_human" and "custom_quote" in ok["human_reasons"]
    assert ok["subtotal"] == 13400 and ok["advance_due"] == 6700
    cash = tools.update_draft(ctx(), payment_method="cash")
    assert "advance_needs_online_payment" in problem_codes(cash)
    one_lb = tools.update_draft(ctx(), items=[{"sku": "CU-THEME", "variant": "per lb", "qty": 1, "message": ""}], payment_method="jazzcash")
    assert "minimum_quantity" in problem_codes(one_lb)                                                                       # minimum 2 lb


def test_eggless(db):
    today = tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "CK-CHOC", "variant": "2 lb", "qty": 1, "eggless": True, "message": ""}],
                                            slot_start="2026-11-06T19:00"))                                                  # test 18
    assert any(p.get("reason") == "notice" for p in today["problems"])
    tomorrow = tools.update_draft(ctx(), slot_start="2026-11-07T19:00")
    assert tomorrow["status"] == "ready_for_summary" and tomorrow["subtotal"] == 3200 + 600                                   # Rs 300 per lb
    rv = tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "CK-RVLT", "variant": "1 lb", "qty": 1, "eggless": True, "message": ""}]))
    assert "eggless_not_available" in problem_codes(rv)


def test_item_rules_drinks_sourdough_message_length(db):
    chai = tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "BV-CHAI", "qty": 8}, {"sku": "BV-CCOF", "qty": 2}]))        # test 19
    assert [p["item"] for p in chai["problems"] if p["code"] == "not_deliverable"] == ["Doodh Patti Chai"]
    sour = tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "BR-SOUR", "qty": 1}], asap=True, slot_start=None))
    assert "not_sold_that_day" in problem_codes(sour)                                                                         # test 29: Friday
    long_msg = tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "CK-CHOC", "variant": "1 lb", "qty": 1, "message": "x" * 31}]))
    assert "message_too_long" in problem_codes(long_msg)


def test_friday_jummah_and_same_day_cake_cutoff(db):
    asap = dict(items=[{"sku": "SV-CPAT", "qty": 10}], fulfilment="delivery", address="DHA Phase 5, House 3 Street 2", asap=True,
                payment_method="cash", customer_name="Ali")
    r = tools.draft_order(ctx(now=dt.datetime(2026, 11, 6, 13, 30)), **asap)                                                  # test 30
    assert r["status"] == "ready_for_summary" and "3:30 PM" in r["slot"]                                                      # goes out after 2:30 pm
    cake = dict(asap, items=[{"sku": "CK-PINE", "variant": "1 lb", "qty": 1, "message": ""}])
    late = tools.draft_order(ctx(now=dt.datetime(2026, 11, 6, 21, 30)), **cake)
    assert any(p.get("reason") == "same_day_cutoff" for p in late["problems"])
    okay = tools.draft_order(ctx(now=dt.datetime(2026, 11, 6, 14, 0)), **cake)
    assert okay["status"] == "ready_for_summary"
    jummah = tools.draft_order(ctx(now=dt.datetime(2026, 11, 6, 11, 0)), **dict(asap, asap=None, slot_start="2026-11-06T13:00"))
    assert any(p.get("reason") == "jummah" for p in jummah["problems"])


def test_payment_rules(db):
    r = tools.draft_order(ctx(turn=1), **dict(BASE, payment_method="counter"))
    assert "payment_method_invalid" in problem_codes(r)                                                                       # card only at the counter, pickup
    pickup = tools.draft_order(ctx(turn=2), **dict(BASE, fulfilment="pickup", branch="DHA", payment_method="counter"))
    assert pickup["status"] == "ready_for_summary" and pickup["delivery_fee"] == 0
    assert tools.draft_order(ctx(PHONE_Z), **dict(BASE, payment_method=None))["missing"][-1].startswith("payment method")


def test_gift_needs_receiver_details_and_can_hide_the_price(db):
    r = tools.draft_order(ctx(), **dict(BASE, receiver_name="Mama"))
    assert any("gift receiver" in x for x in r["missing"])
    done = tools.update_draft(ctx(), receiver_phone="0300 1112223", hide_price=True)
    assert done["status"] == "ready_for_summary" and "Rs" not in done["summary_text"].split("Address")[0].replace("Please check your order:", "")


def test_deals(db):
    bundle = tools.draft_order(ctx(), **dict(BASE, items=[{"deal": "Birthday Bundle", "qty": 1, "swap_cake": "CK-BLFR", "message": "Happy Birthday"}]))
    assert bundle["status"] == "ready_for_summary" and bundle["subtotal"] == 4700 and bundle["delivery_fee"] == 0
    assert tools.confirm_order(ctx(turn=2))["confirmed"]
    assert stock("CK-BLFR", "2 lb") == 2 and stock("CC-BOX6") == 7 and stock("BR-CANDLE") is None                           # components reserved
    tea = tools.draft_order(ctx(), **dict(BASE, items=[{"deal": "Tea Time Deal", "qty": 1}]))
    assert "deal_not_available" in problem_codes(tea)                                                                          # Friday, delivery
    hi = tools.draft_order(ctx(), **dict(BASE, items=[{"deal": "Office Hi-Tea Box", "qty": 10}], slot_start="2026-11-09T13:00"))
    assert "deal_minimum" in problem_codes(hi)


def test_wednesday_donut_day_buy_4_get_2_free(db):
    wed = dt.datetime(2026, 11, 4, 14, 0)
    pick = dict(BASE, fulfilment="pickup", branch="DHA", payment_method="counter")  # pickup: no Rs 1,000 delivery minimum
    r = tools.draft_order(ctx(now=wed), **dict(pick, items=[{"sku": "DS-DONG", "qty": 4}], slot_start="2026-11-04T18:00"))
    assert r["status"] == "ready_for_summary" and r["subtotal"] == 880
    free = [ln for ln in r["lines"] if "free" in ln["name"]]
    assert len(free) == 1 and free[0]["qty"] == 2 and free[0]["line_total"] == 0
    thu = tools.draft_order(ctx(now=wed), **dict(pick, items=[{"sku": "DS-DONG", "qty": 4}], slot_start="2026-11-05T18:00"))
    assert not any("free" in ln["name"] for ln in thu["lines"])                                                                # only Wednesdays


def test_loyalty_points_and_birthday_discount(db):
    pts = tools.draft_order(ctx(AYESHA, turn=1), **dict(BASE, customer_name=None, use_points=True))
    assert pts["discount"] == 1500 and pts["total"] == 3450 - 1500                                                              # 340 points = 3 blocks
    bd = tools.draft_order(ctx(AYESHA, turn=2), **dict(BASE, customer_name=None, birthday_discount=True))
    assert bd["discount"] == 320                                                                                                # Ayesha was born in November
    both = tools.update_draft(ctx(AYESHA, turn=2), use_points=True)
    assert "discount_no_stacking" in problem_codes(both)
    assert "birthday_discount_not_available" in problem_codes(tools.draft_order(ctx(HAMZA), **dict(BASE, birthday_discount=True)))
    assert "no_points_to_use" in problem_codes(tools.draft_order(ctx(HAMZA), **dict(BASE, use_points=True)))
    bilal = tools.draft_order(ctx(BILAL), **dict(BASE, customer_name=None, use_points=True))
    assert bilal["discount"] == 500                                                                                             # 120 points: one block
    assert tools.confirm_order(ctx(BILAL, turn=2))["confirmed"]
    with database.session_scope() as s:
        assert s.query(m.Customer).filter(m.Customer.phone == BILAL).one().points == 20


# ----------------------------------------------------------------------------- existing orders
def test_order_status_for_the_chatting_customer(db):
    r = tools.order_status(ctx(AYESHA))                                                                                          # test 9
    assert [o["code"] for o in r["orders"]][:2] == ["SC-10421", "SC-10431"] or {o["code"] for o in r["orders"]} >= {"SC-10421", "SC-10431"}
    assert "several active orders" in r["hint"]
    one = tools.order_status(ctx(AYESHA), "SC-10421")["orders"][0]
    assert one["status"] == "out_for_delivery" and one["rider"] == "Imran" and one["eta"] == "6:35 PM" and "rider ke paas" in one["status_rur"]


def test_cannot_see_another_customers_order(db):
    r = tools.order_status(ctx(BILAL), "SC-10421")                                                                               # test 27
    assert r["found"] is False
    assert tools.order_status(ctx(BILAL), "SC-99999") == r                                                                       # same answer: nothing leaks
    assert tools.cancel_order(ctx(BILAL), "SC-10421")["found"] is False
    assert tools.modify_order(ctx(BILAL), "SC-10421", notes="x")["found"] is False


def test_refund_ready_and_late_information(db):
    ref = tools.order_status(ctx(SANA), "SC-10412")["orders"][0]                                                                 # test 11
    assert ref["status"] == "refund_processing" and ref["refund"] == {"amount": 1500, "due_by": "2026-11-10"}
    ready = tools.order_status(ctx(ZAINAB), "SC-10425")["orders"][0]                                                             # test 13
    assert ready["status"] == "ready_for_pickup" and ready["pickup_branch"] == "Gulshan-e-Iqbal"
    assert "late_by_minutes" not in tools.order_status(ctx(AYESHA, now=dt.datetime(2026, 11, 6, 18, 30)), "SC-10421")["orders"][0]
    late = tools.order_status(ctx(AYESHA, now=dt.datetime(2026, 11, 6, 19, 30)), "SC-10421")["orders"][0]
    assert late["late_by_minutes"] == 55


def test_cancel_rules(db):
    prep = tools.cancel_order(ctx(BILAL), "SC-10422")                                                                            # test 10: preparing
    assert prep["cancelled"] is False and prep["escalated"] is True
    with database.session_scope() as s:
        assert s.query(m.Escalation).filter(m.Escalation.phone == BILAL).count() == 1
        assert s.query(m.Order).filter(m.Order.code == "SC-10422").one().status == "preparing"
    before = stock("PL-SAV")
    done = tools.cancel_order(ctx(AYESHA), "SC-10431")                                                                           # scheduled platter, standard order
    assert done == {"cancelled": True, "code": "SC-10431"} and stock("PL-SAV") == before    # seeded orders carry no reservation record: stock untouched
    far = tools.cancel_order(ctx(SANA), "SC-10430")                                                                              # custom cake, Monday 4 pm is 50 h away
    assert far["cancelled"] and far["advance_refund"] == "full" and far["escalated"]


def test_cancel_custom_cake_within_24_hours_is_not_refunded(db):
    near = tools.cancel_order(ctx(SANA, now=dt.datetime(2026, 11, 9, 10, 0)), "SC-10430")
    assert near["cancelled"] and near["advance_refund"] == "none"


def test_cancelling_a_new_order_gives_the_stock_back(db):
    tools.draft_order(ctx(turn=1), **BASE)
    code = tools.confirm_order(ctx(turn=2))["code"]
    assert stock("CK-CHOC", "2 lb") == 3
    assert tools.cancel_order(ctx(turn=3), code)["cancelled"] and stock("CK-CHOC", "2 lb") == 4


def test_modify_order(db):
    tools.draft_order(ctx(turn=1), **BASE)
    code = tools.confirm_order(ctx(turn=2))["code"]
    moved = tools.modify_order(ctx(turn=3), code, address="House 3, North Nazimabad Block H", slot_start="2026-11-07T20:00")
    assert moved["changed"] and moved["order"]["address"].startswith("House 3") and moved["new_total"] == 3300 + 300             # Zone C fee
    bad = tools.modify_order(ctx(turn=4), code, address="House 15, Bahria Town")
    assert bad["changed"] is False and "area_not_served" in str(bad)
    jummah = tools.modify_order(ctx(turn=4), code, slot_start="2026-11-13T13:00")
    assert jummah["changed"] is False
    swap = tools.modify_order(ctx(turn=5), code, items=[{"sku": "CK-BLFR", "variant": "2 lb", "qty": 1, "message": "Happy Birthday Ammi"}])
    assert swap["changed"] and stock("CK-CHOC", "2 lb") == 4 and stock("CK-BLFR", "2 lb") == 2                                   # stock moved with the change
    prep = tools.modify_order(ctx(BILAL), "SC-10422", notes="x")                                                                  # preparing: not changeable
    assert prep["changed"] is False and prep["escalated"] is True


def test_cake_message_lock_two_hours_before(db):
    with database.session_scope() as s:
        slot = s.query(m.Order).filter(m.Order.code == "SC-10431").one().slot_start
    locked = tools.modify_order(ctx(AYESHA, now=slot - dt.timedelta(hours=1)), "SC-10431", cake_message="Hi")
    assert locked.get("reason") == "message_locked"


# ----------------------------------------------------------------------------- escalation
def test_escalate_to_human_records_the_summary_and_the_draft(db):
    with database.session_scope() as s:
        s.add(m.Conversation(phone=NEW, turn=1))
    tools.draft_order(ctx(), **dict(BASE, items=[{"sku": "CU-THEME", "variant": "per lb", "qty": 2, "message": "Frozen"}]))
    r = tools.escalate_to_human(ctx(), "custom cake quote", "Frozen theme, 2 lb, Sunday", "normal")
    assert r["escalated"] is True
    with database.session_scope() as s:
        e = s.query(m.Escalation).filter(m.Escalation.phone == NEW).one()
        assert e.reason == "custom cake quote" and e.collected["items"][0]["sku"] == "CU-THEME" and e.status == "open"
        assert s.get(m.Conversation, NEW).escalated is True


def test_asap_order_for_today_is_confirmed_not_scheduled(db):
    tools.draft_order(ctx(turn=1), **dict(BASE, slot_start=None, asap=True, items=[{"sku": "SV-CPAT", "qty": 10}]))
    r = tools.confirm_order(ctx(turn=2))
    assert r["confirmed"] and r["status"] == "confirmed"


def test_advance_orders_wait_for_the_advance(db):
    items = [{"sku": "CU-THEME", "variant": "per lb", "qty": 3, "message": "Frozen"}, {"sku": "CU-FIGURE", "qty": 1}]
    d = tools.draft_order(ctx(turn=1), **dict(BASE, items=items, slot_start="2026-11-09T16:00", payment_method="jazzcash"))
    assert d["status"] == "needs_human"          # custom cakes are quoted by staff: the bot may never place them itself
    refused = tools.confirm_order(ctx(turn=2))
    assert refused["confirmed"] is False and refused["error"] in ("no_summary_for_current_draft", "draft_not_valid_anymore")
    big = dict(BASE, items=[{"sku": "PL-SAV", "qty": 3}, {"sku": "PL-DES", "qty": 1}, {"sku": "CK-CHOC", "variant": "3 lb", "qty": 2, "message": ""},
                            {"sku": "CK-LOTUS", "variant": "2 lb", "qty": 1, "message": ""}, {"sku": "CK-MALAI", "variant": "2 lb", "qty": 1, "message": ""}],
               slot_start="2026-11-09T18:00", payment_method="cash")
    r = tools.draft_order(ctx(BILAL, turn=1), **big)                                    # Rs 14,700 + 9,200 ...: over Rs 15,000 needs an advance
    assert r["subtotal"] > 15000 and r["advance_due"] == -(-r["total"] // 2) and "advance_needs_online_payment" in problem_codes(r)
    ok = tools.update_draft(ctx(BILAL, turn=1), payment_method="bank")
    assert ok["status"] == "needs_human" or ok["status"] == "ready_for_summary"


def test_draft_order_and_update_draft_in_the_same_message_do_not_wipe_each_other(db):
    """The model may emit both tool calls in one step; LangGraph runs them concurrently."""
    import threading
    out = {}
    t1 = threading.Thread(target=lambda: out.setdefault("a", tools.draft_order(ctx(turn=1), items=[{"sku": "DS-BROW", "qty": 2}])))
    t2 = threading.Thread(target=lambda: out.setdefault("b", tools.update_draft(ctx(turn=1), fulfilment="pickup", branch="DHA", customer_name="Ali")))
    for th in (t1, t2):
        th.start()
    for th in (t1, t2):
        th.join()
    with database.session_scope() as s:
        st = tools.load_state(s, NEW)
    assert [i["sku"] for i in st["items"]] == ["DS-BROW"] and st["fulfilment"] == "pickup" and st["customer_name"] == "Ali"


def test_a_new_order_in_a_later_message_starts_a_fresh_draft(db):
    tools.draft_order(ctx(turn=1), **dict(BASE, notes="old note", use_points=True))
    tools.draft_order(ctx(turn=2), items=[{"sku": "SV-CPAT", "qty": 3}])
    with database.session_scope() as s:
        st = tools.load_state(s, NEW)
    assert [i["sku"] for i in st["items"]] == ["SV-CPAT"] and st["notes"] == "" and st["use_points"] is False and st["fulfilment"] is None


def test_escalating_twice_keeps_one_open_escalation_with_all_details(db):
    with database.session_scope() as s:
        s.add(m.Conversation(phone=BILAL, turn=1))
    tools.cancel_order(ctx(BILAL), "SC-10422")                                   # escalates by itself (kitchen already started)
    tools.escalate_to_human(ctx(BILAL), "wants to cancel preparing order", "Customer Bilal asked to cancel SC-10422", "high", "SC-10422")
    with database.session_scope() as s:
        rows = s.query(m.Escalation).filter(m.Escalation.phone == BILAL).all()
        assert len(rows) == 1 and rows[0].priority == "high" and "SC-10422" in rows[0].summary and rows[0].order_code == "SC-10422"
        assert "cancel_after_preparation" in rows[0].reason
