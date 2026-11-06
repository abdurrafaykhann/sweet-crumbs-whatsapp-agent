"""Unit tests for app/rules.py. Cases come from sweet-crumbs-bakery-demo.md (sections 3, 4, 7, 12, 13).
Demo clock: Friday, 6 November 2026."""
import datetime as dt

import pytest

from app import rules
from app.rules import RulesConfig

CFG = RulesConfig()
FRI = dt.datetime(2026, 11, 6)  # Friday


def at(day_offset=0, hh=0, mm=0):
    return FRI + dt.timedelta(days=day_offset, hours=hh, minutes=mm)


# ----------------------------------------------------------------------------- delivery area
@pytest.mark.parametrize("text,status,zone", [
    ("DHA Phase 5", "served", "A"),
    ("House 45, Street 12, Khayaban-e-Shamsheer, DHA Phase 5", "served", "A"),
    ("Clifton block 4", "served", "A"),
    ("North Nazimabad", "served", "C"),            # spec test 6: Rs 300
    ("Gulistan-e-Jauhar", "served", "B"),
    ("jauhar block 7", "served", "B"),
    ("PECHS", "served", "B"),
    ("Shahrah-e-Faisal", "served", "B"),
    ("sharae faisal", "served", "B"),
    ("Scheme 33", "served", "C"),
    ("Bahria Town", "not_served", None),            # spec test 5
    ("Bahria Town Karachi", "not_served", None),
    ("DHA City", "not_served", None),               # longest match: 'DHA City' beats 'DHA'
    ("Malir Cantt", "not_served", None),
    ("house 15 sector 1 bahria town", "not_served", None),
])
def test_resolve_area(areas, text, status, zone):
    r = rules.resolve_area(text, areas)
    assert (r.status, r.zone_code) == (status, zone), r


def test_resolve_area_handles_typos(areas):
    r = rules.resolve_area("Gulshn e iqbal block 13", areas)
    assert r.status == "served" and r.zone_code == "B"
    assert rules.resolve_area("clifon", areas).zone_code == "A"


def test_bare_block_number_is_unclear(areas):
    """Spec test 33: 'Block 7 deliver karte ho?' must make the bot ask which area."""
    r = rules.resolve_area("Block 7", areas, block_candidates=("Gulshan-e-Iqbal", "Clifton", "Gulistan-e-Jauhar"))
    assert r.status == "unclear" and "Clifton" in r.candidates


def test_unknown_place(areas):
    assert rules.resolve_area("Lahore", areas).status == "unknown"
    assert rules.resolve_area("", areas).status == "unknown"


# ----------------------------------------------------------------------------- fees and minimum
def test_free_delivery_threshold():
    assert rules.delivery_fee(200, 4200, CFG) == 0         # spec test 7
    assert rules.delivery_fee(200, 4000, CFG) == 0         # SC-10422: exactly Rs 4,000
    assert rules.delivery_fee(200, 3999, CFG) == 200
    assert rules.delivery_fee(300, 1000, CFG) == 300


def test_minimum_order():
    assert not rules.meets_minimum(700, CFG)                # spec test 8
    assert rules.meets_minimum(1000, CFG)


# ----------------------------------------------------------------------------- sizes, eggless, message
@pytest.mark.parametrize("guests,size,fits", [(4, "1 lb", False), (8, "1 lb", True), (10, "2 lb", False), (15, "2 lb", True),
                                              (16, "3 lb", False), (22, "3 lb", True), (28, "4 lb", True)])
def test_suggest_size(guests, size, fits):
    r = rules.suggest_size(guests, CFG)
    assert r["size"] == size and r["fits_range"] == fits


def test_suggest_size_edges():
    assert rules.suggest_size(28)["on_menu"] is False       # 4 lb exists only as a custom cake
    assert rules.suggest_size(31) is None                    # events team
    assert rules.suggest_size(0) is None


def test_eggless_and_message():
    assert rules.eggless_surcharge("2 lb", CFG) == 600       # Rs 300 per lb
    assert rules.eggless_surcharge("each", CFG) == 0
    assert rules.message_ok("Happy Birthday Ammi", CFG)
    assert not rules.message_ok("x" * 31, CFG)


# ----------------------------------------------------------------------------- advance and human routing
def test_advance_rules():
    assert rules.advance_required(13400, True, CFG) == (True, 6700)      # SC-10430
    assert rules.advance_required(15000, False, CFG) == (False, 0)
    assert rules.advance_required(15001, False, CFG) == (True, 7501)
    assert rules.advance_required(3450, False, CFG) == (False, 0)


def test_human_routing():
    assert rules.human_routing_reasons(25001, cfg=CFG) == ["bulk_total"]
    assert rules.human_routing_reasons(25000, cfg=CFG) == []
    assert "bulk_boxes" in rules.human_routing_reasons(100, boxes=51, cfg=CFG)
    assert "wedding_or_tiered" in rules.human_routing_reasons(100, kinds=frozenset({"wedding"}), cfg=CFG)
    assert "custom_quote" in rules.human_routing_reasons(100, kinds=frozenset({"custom"}), cfg=CFG)


# ----------------------------------------------------------------------------- opening hours and Jummah
DHA = {"mon_thu": (540, 1440), "fri": (540, 1440), "sat_sun": (540, 1470)}
GULSHAN = {"mon_thu": (540, 1410), "fri": (540, 1410), "sat_sun": (540, 1440)}


def test_jummah_closes_every_branch():
    assert rules.is_open(DHA, at(0, 12, 59), CFG)
    assert not rules.is_open(DHA, at(0, 13, 0), CFG)
    assert not rules.is_open(GULSHAN, at(0, 14, 29), CFG)
    assert rules.is_open(GULSHAN, at(0, 14, 30), CFG)
    assert rules.is_open(DHA, at(-1, 14, 0), CFG)            # Thursday 2 pm is a normal day


def test_closing_times_and_midnight_spill():
    assert not rules.is_open(GULSHAN, at(-1, 23, 45), CFG)    # Thursday: Gulshan closes 11:30 pm
    assert not rules.is_open(DHA, at(1, 0, 15), CFG)           # Friday night DHA closes at 12 am sharp: closed at 12:15 am Saturday
    assert rules.is_open(DHA, at(2, 0, 15), CFG)               # Saturday night DHA stays open until 12:30 am: open at 12:15 am Sunday
    assert not rules.is_open(DHA, at(2, 0, 45), CFG)
    assert rules.is_open(GULSHAN, at(2, 23, 59), CFG)          # Saturday: Gulshan until 12 am


def test_fmt_minutes():
    assert [rules.fmt_minutes(x) for x in (540, 1470, 1410, 720, 0)] == ["9 am", "12:30 am", "11:30 pm", "12 pm", "12 am"]


# ----------------------------------------------------------------------------- scheduling and notice
def test_custom_cake_needs_48_hours():
    """Spec test 16: 'Frozen theme cake tomorrow' on Friday 2 pm."""
    r = rules.check_slot(at(1, 19), at(0, 14), notice_hours=48, cfg=CFG)
    assert not r.ok and r.code == "notice" and r.earliest == at(2, 14)
    assert rules.check_slot(at(3, 17), at(0, 14), notice_hours=48, cfg=CFG).ok


def test_eggless_today_needs_24_hours():
    """Spec test 18."""
    r = rules.check_slot(at(0, 19), at(0, 14), notice_hours=24, cfg=CFG)
    assert not r.ok and r.code == "notice" and r.earliest == at(1, 14)


def test_platter_needs_6_hours():
    now = at(0, 14)
    assert rules.check_slot(at(0, 18), now, notice_hours=6, cfg=CFG).code == "notice"
    assert rules.check_slot(at(0, 20), now, notice_hours=6, cfg=CFG).ok


def test_same_day_cake_cutoff_depends_on_time_of_day():
    """'Can I get a cake by 8?' answers differently at 2 pm and 9:30 pm."""
    assert rules.check_slot(at(0, 20), at(0, 14), is_standard_cake=True, cfg=CFG).ok
    late = rules.check_slot(at(0, 23), at(0, 21, 30), is_standard_cake=True, cfg=CFG)
    assert late.code == "same_day_cutoff" and late.earliest == at(1, 11)
    assert rules.check_slot(at(1, 11), at(0, 21, 30), is_standard_cake=True, cfg=CFG).ok  # tomorrow is fine


def test_friday_jummah_slots():
    r = rules.check_slot(at(0, 13), at(0, 11), cfg=CFG)
    assert r.code == "jummah" and r.earliest == at(0, 15)
    assert rules.check_slot(at(0, 14), at(0, 11), cfg=CFG).code == "jummah"   # 2-3 pm slot starts before 2:30
    assert rules.check_slot(at(0, 15), at(0, 11), cfg=CFG).ok
    assert rules.check_slot(at(7, 13), at(0, 11), cfg=CFG).code == "jummah"   # next Friday too
    assert rules.check_slot(at(6, 13), at(0, 11), cfg=CFG).ok                  # Thursday 1 pm is fine


def test_slot_window_and_horizon():
    assert rules.check_slot(at(1, 10), at(0, 11), cfg=CFG).code == "outside_delivery_slots"
    assert rules.check_slot(at(1, 22, 30), at(0, 11), cfg=CFG).code == "outside_delivery_slots"
    assert rules.check_slot(at(1, 22), at(0, 11), cfg=CFG).ok                   # last slot 10-11 pm
    assert rules.check_slot(at(8, 15), at(0, 11), cfg=CFG).code == "too_far"
    assert rules.check_slot(at(7, 15), at(0, 11), cfg=CFG).ok
    assert rules.check_slot(at(0, 9), at(0, 11), cfg=CFG).code == "in_past"


def test_asap_eta():
    assert rules.asap_eta(at(0, 13, 30), 60, CFG) == at(0, 15, 30)      # spec test 30: Friday 1:30 pm goes out after 2:30
    assert rules.asap_eta(at(0, 11, 0), 60, CFG) == at(0, 12, 0)
    assert rules.asap_eta(at(0, 23, 0), 60, CFG) == at(1, 11, 0)        # after the 10:30 pm last order: next morning 10 am
    assert rules.asap_eta(at(0, 22, 30), 45, CFG) == at(0, 23, 15)      # exactly the last-order time is still accepted
    assert rules.asap_eta(at(1, 8, 0), 60, CFG) == at(1, 11, 0)         # before opening


# ----------------------------------------------------------------------------- cancellation, changes, lateness
def test_cancellation_rules():
    now = at(0, 14)
    assert rules.cancellation("confirmed", False, None, now, CFG) == {"allowed": True, "escalate": False, "refund": "n/a", "reason": "standard"}
    r = rules.cancellation("preparing", False, None, now, CFG)           # spec test 10
    assert not r["allowed"] and r["escalate"]
    assert not rules.cancellation("out_for_delivery", False, None, now, CFG)["allowed"]
    custom_far = rules.cancellation("awaiting_advance", True, at(3, 16), now, CFG)
    assert custom_far["allowed"] and custom_far["refund"] == "full" and custom_far["escalate"]
    custom_near = rules.cancellation("confirmed", True, at(1, 10), now, CFG)
    assert custom_near["refund"] == "none"
    assert rules.cancellation("confirmed", True, at(1, 14), now, CFG)["refund"] == "full"    # exactly 24 h counts


def test_changes_and_message_edit_window():
    assert all(rules.can_change(s) for s in ("draft", "awaiting_advance", "confirmed", "scheduled"))
    assert not any(rules.can_change(s) for s in ("preparing", "out_for_delivery", "delivered", "cancelled"))
    assert rules.cake_message_editable(at(0, 20), at(0, 17), CFG)
    assert rules.cake_message_editable(at(0, 20), at(0, 18), CFG)       # exactly 2 hours
    assert not rules.cake_message_editable(at(0, 20), at(0, 18, 1), CFG)


def test_late_delivery_policy():
    promised = at(0, 18, 0)
    assert rules.is_late(promised, at(0, 18, 31), CFG)
    assert not rules.is_late(promised, at(0, 18, 30), CFG)
    assert not rules.is_late(promised, at(0, 18, 15), CFG)              # SC-10385: 15 minutes late is within policy


# ----------------------------------------------------------------------------- loyalty
def test_points_and_redemption():
    assert rules.points_earned(3450, CFG) == 34
    assert rules.redeemable(340, 3450, CFG) == {"blocks": 3, "points_used": 300, "rs_off": 1500}   # spec test 28
    assert rules.redeemable(340, 1000, CFG)["rs_off"] == 1000            # never more than the order total
    assert rules.redeemable(99, 5000, CFG)["rs_off"] == 0
    assert rules.redeemable(610, 1200, CFG)["rs_off"] == 1000


def test_birthday_discount():
    now = at(0, 14)                                                      # November
    assert rules.birthday_discount(11, now, 3200, CFG) == 320            # Ayesha and Zainab were born in November
    assert rules.birthday_discount(3, now, 3200, CFG) == 0
    assert rules.birthday_discount(None, now, 3200, CFG) == 0


# ----------------------------------------------------------------------------- deals
TEA = {"days": ["Mon", "Tue", "Wed", "Thu"], "start_min": 960, "end_min": 1140, "channels": ["dine_in", "takeaway"]}
SUNDAY = {"days": ["Sun"], "start_min": 540, "end_min": 780, "channels": ["delivery", "pickup"]}
BUNDLE = {"days": None, "channels": ["delivery", "pickup"], "valid_until": dt.date(2026, 12, 31)}


def test_deal_windows():
    thu = at(-1, 17)
    assert rules.deal_active(TEA, thu, "dine_in")
    assert not rules.deal_active(TEA, thu, "delivery")                   # no delivery because of the chai
    assert not rules.deal_active(TEA, at(0, 17), "dine_in")              # Friday
    assert not rules.deal_active(TEA, at(-1, 15, 59), "dine_in")
    assert rules.deal_active(SUNDAY, at(2, 10), "delivery")              # Sunday 8 Nov
    assert not rules.deal_active(SUNDAY, at(2, 14), "delivery")
    assert rules.deal_active(BUNDLE, at(0, 12), "pickup")
    assert not rules.deal_active(BUNDLE, dt.datetime(2027, 1, 1, 12), "pickup")
