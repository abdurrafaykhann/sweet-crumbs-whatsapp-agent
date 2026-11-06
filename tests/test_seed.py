"""The seed must reproduce the spec exactly: the md file is the source of truth."""
import dataclasses
import datetime as dt
import pathlib

import pytest

from app import models as m
from app import repo, seed
from app.rules import RulesConfig

from .conftest import variant


def test_counts(seeded):
    assert seeded["branches"] == 3 and seeded["zones"] == 3 and seeded["deals"] == 5
    assert seeded["customers"] == 8 and seeded["orders"] == 9 and seeded["status_labels"] == 10
    assert seeded["areas"] == 7 + 12 + 9 + 10                             # zones A, B, C and the not-served list
    assert seeded["products"] >= 60 and seeded["variants"] >= seeded["products"]


def test_zones_and_areas(session):
    assert [(z.code, z.fee, z.min_minutes, z.max_minutes) for z in session.query(m.DeliveryZone).order_by(m.DeliveryZone.code)] == \
        [("A", 150, 40, 60), ("B", 200, 45, 75), ("C", 300, 60, 90)]
    served = session.query(m.Area).filter(m.Area.zone_code.is_not(None)).count()
    assert served == 28
    assert session.query(m.Area).filter(m.Area.name == "Bahria Town Karachi").one().zone_code is None
    assert session.query(m.Area).filter(m.Area.name == "Scheme 33").one().note == "up to Safoora Chowrangi"


def test_branches_and_hours(session):
    dha = session.query(m.Branch).filter(m.Branch.name == "DHA Phase 6").one()
    assert dha.note == "main + kitchen" and dha.dine_in and "24 seats" in dha.dine_in_note
    hours = {h.day_group: (h.open_min, h.close_min) for h in dha.hours}
    assert hours == {"mon_thu": (540, 1440), "fri": (540, 1440), "sat_sun": (540, 1470)}
    gul = repo.branch_hours(session, "Gulshan-e-Iqbal")
    assert gul == {"mon_thu": (540, 1410), "fri": (540, 1410), "sat_sun": (540, 1440)}
    assert not session.query(m.Branch).filter(m.Branch.name == "Gulshan-e-Iqbal").one().dine_in
    assert repo.branch_hours(session, "Bahadurabad")["sat_sun"] == (540, 1410)


def test_settings_match_rules_defaults(session):
    """The constants parsed out of the spec must equal RulesConfig's defaults (they are written from the same spec)."""
    cfg = repo.load_config(session)
    assert cfg == RulesConfig()
    parsed = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}
    assert parsed["min_order"] == 1000 and parsed["free_delivery_from"] == 4000 and parsed["jummah_end"] == 870


def test_menu_stock_snapshot(session):
    """Spec section 5: 0 means sold out today."""
    assert variant(session, "CK-CHOC", "1 lb").stock == 6 and variant(session, "CK-CHOC", "3 lb").stock == 2
    assert variant(session, "CK-RVLT", "2 lb").stock == 0                 # spec test 3
    assert variant(session, "CK-LOTUS", "3 lb").stock == 0
    assert variant(session, "CK-STRW", "1 lb").stock == 0 and variant(session, "CK-STRW", "2 lb").stock == 2
    assert {v.label for v in session.query(m.Product).filter(m.Product.sku == "CK-STRW").one().variants} == {"1 lb", "2 lb"}  # no 3 lb
    assert variant(session, "SV-PUFF").stock == 0                          # spec test 4
    assert variant(session, "CS-NY").stock == 0 and variant(session, "BR-SOUR").stock == 0
    assert variant(session, "SV-CPAT").price == 140 and variant(session, "SV-CPAT").stock == 40
    assert variant(session, "CK-CHOC", "2 lb").price == 3200               # spec test 2


def test_special_products(session):
    p = lambda sku: session.query(m.Product).filter(m.Product.sku == sku).one()
    sf = p("CK-SFCH")
    assert sf.made_to_order and sf.notice_hours == 24 and all(v.stock is None for v in sf.variants)
    assert p("BR-SOUR").sold_days == ["Sat", "Sun"]
    assert not p("BV-CHAI").delivery_ok and not p("BV-HCHO").delivery_ok and p("BV-CCOF").delivery_ok and p("BV-MINT").delivery_ok
    for sku in ("CK-MALAI", "BS-ALMD", "DS-NBRW", "DS-DONN", "DC-BANA"):
        assert p(sku).contains_nuts, sku
    assert not p("CK-CHOC").contains_nuts and not p("DS-BROW").contains_nuts
    assert p("PL-SAV").notice_hours == 6 and p("PL-DES").notice_hours == 6
    theme = p("CU-THEME")
    assert theme.is_custom and theme.requires_human and theme.unit == "lb" and theme.min_qty == 2 and theme.notice_hours == 48
    assert theme.variants[0].price == 4200
    assert p("CU-FIGURE").variants[0].price == 800 and p("CU-PHOTO").notice_hours == 4
    assert p("CK-CHOC").eggless_ok and not p("CK-RVLT").eggless_ok
    free = [v for v in session.query(m.Variant).join(m.Product).filter(m.Product.sku.like("AD-%")).all()]
    assert any(v.price == 0 for v in free) and any(v.price == 150 for v in free)   # message/knife free, number candle Rs 150


def test_deals(session):
    d = {x.name: x for x in session.query(m.Deal).all()}
    assert set(d) == {"Birthday Bundle", "Tea Time Deal", "Sunday Breakfast Box", "Office Hi-Tea Box", "Wednesday Donut Day"}
    assert d["Birthday Bundle"].usual_price - d["Birthday Bundle"].price == 400    # "saves Rs 400"
    assert d["Birthday Bundle"].valid_until == dt.date(2026, 12, 31)
    assert d["Tea Time Deal"].days == ["Mon", "Tue", "Wed", "Thu"] and d["Tea Time Deal"].start_min == 960
    assert d["Office Hi-Tea Box"].min_qty == 15 and d["Wednesday Donut Day"].price is None


def test_customers(session):
    c = lambda phone: session.query(m.Customer).filter(m.Customer.phone == phone).one()
    ayesha = c("923000000101")
    assert (ayesha.name, ayesha.language, ayesha.points, ayesha.birthday_month) == ("Ayesha Khan", "rur", 340, 11)
    assert ayesha.addresses[0].zone_code == "A" and "DHA Ph 5" in ayesha.addresses[0].text
    sana = c("923000000105")
    assert {(a.label, a.zone_code) for a in sana.addresses} == {("home", "A"), ("mother's", "B")}
    assert c("923000000106").addresses[0].zone_code is None                       # Usman: Bahria Town, not served (test 34)
    assert c("923000000108").addresses == [] and c("923000000103").language == "ur"
    assert c("923000000102").birthday_month is None


def test_demo_orders_match_the_spec(session):
    o = lambda code: session.query(m.Order).filter(m.Order.code == code).one()
    first = o("SC-10421")
    assert (first.status, first.total, first.delivery_fee, first.rider_name) == ("out_for_delivery", 3450, 150, "Imran")
    assert first.eta == dt.datetime(2026, 11, 6, 18, 35) and first.customer.phone == "923000000101"
    assert o("SC-10431").status == "scheduled" and o("SC-10431").delivery_fee == 0 and o("SC-10431").slot_start == dt.datetime(2026, 11, 8, 17)
    assert o("SC-10422").status == "preparing" and o("SC-10422").total == 4000 and o("SC-10422").payment_status == "paid"
    assert o("SC-10360").status == "cancelled" and o("SC-10360").delivery_fee == 300
    adv = o("SC-10430")
    assert adv.status == "awaiting_advance" and adv.total == 13400 and adv.advance_due == 6700
    ref = o("SC-10412")
    assert ref.status == "refund_processing" and ref.refund_amount == 1500 and ref.refund_due == dt.date(2026, 11, 10)
    assert o("SC-10425").status == "ready_for_pickup" and o("SC-10425").fulfilment == "pickup" and o("SC-10425").branch_id is not None
    assert {x.code for x in session.query(m.Order).all()} == {"SC-10421", "SC-10431", "SC-10422", "SC-10398", "SC-10360", "SC-10430",
                                                               "SC-10412", "SC-10385", "SC-10425"}
    assert session.get(m.Setting, "next_order_number").value == 10432             # the sample confirmation uses SC-10432


def test_orders_are_private_to_their_customer(session):
    ayesha = session.query(m.Customer).filter(m.Customer.phone == "923000000101").one()
    assert {x.code for x in ayesha.orders} == {"SC-10421", "SC-10431"}


def test_info_topics_and_status_labels(session):
    slugs = {t.slug for t in session.query(m.InfoTopic).all()}
    assert {"branches_hours", "delivery", "payment", "deals", "allergens", "storage", "loyalty", "serving_guide", "cancellation_changes"} <= slugs
    assert "halal" in session.get(m.InfoTopic, "business_profile").text.lower()
    assert "Jummah" in session.get(m.InfoTopic, "branches_hours").text
    assert session.get(m.StatusLabel, "out_for_delivery").rur.startswith("rider ke paas")
    assert session.get(m.Setting, "demo_today").value == "2026-11-06"


# ----------------------------------------------------------------------------- the seed refuses a changed spec
def test_seed_fails_loudly_when_the_spec_changes(seeded, tmp_path):
    original = seed.MD_PATH.read_text(encoding="utf-8")
    broken = tmp_path / "broken.md"
    broken.write_text(original.replace("**Minimum order:** Rs 1,000", "**Minimum order:** about one thousand"), encoding="utf-8")
    with pytest.raises(seed.SeedError, match="min_order"):
        seed.seed_database(pathlib.Path(broken))
    bad_total = tmp_path / "bad_total.md"
    bad_total.write_text(original.replace("| Rs 3,450 (incl. Rs 150 delivery) |", "| Rs 3,400 (incl. Rs 150 delivery) |"), encoding="utf-8")
    with pytest.raises(seed.SeedError, match="SC-10421"):
        seed.seed_database(pathlib.Path(bad_total))
    seed.seed_database()  # restore the good data for any test that runs after this one
