import threading

from app import database, inventory
from app import models as m

from .conftest import variant


def _set_stock(vid, stock):
    with database.session_scope() as s:
        s.get(m.Variant, vid).stock = stock


def _vid(sku, label="each"):
    with database.session_scope() as s:
        return variant(s, sku, label).id


def test_reserve_and_release(seeded):
    vid = _vid("DS-BROW")
    _set_stock(vid, 5)
    with database.session_scope() as s:
        assert inventory.reserve(s, vid, 3) and inventory.available(s, vid) == 2
        assert not inventory.reserve(s, vid, 3) and inventory.available(s, vid) == 2        # not enough: nothing changes
        inventory.release(s, vid, 3)
        assert inventory.available(s, vid) == 5


def test_unlimited_and_made_to_order_never_run_out(seeded):
    vid = _vid("BV-CCOF")                                                                    # drinks have no stock figure
    with database.session_scope() as s:
        assert inventory.available(s, vid) is None and inventory.reserve(s, vid, 500)
        inventory.release(s, vid, 500)
        assert inventory.available(s, vid) is None


def test_reserve_many_is_all_or_nothing(seeded):
    a, b = _vid("DS-BROW"), _vid("DS-DONG")
    _set_stock(a, 10)
    _set_stock(b, 2)
    with database.session_scope() as s:
        ok, failed = inventory.reserve_many(s, [(a, 4), (b, 3)])
        assert not ok and failed == b
        assert inventory.available(s, a) == 10 and inventory.available(s, b) == 2             # the first item was rolled back
        ok, failed = inventory.reserve_many(s, [(a, 4), (b, 2)])
        assert ok and failed is None and inventory.available(s, a) == 6 and inventory.available(s, b) == 0


def test_two_customers_race_for_the_last_cakes(seeded):
    """Both confirm at once for the last 2 red velvet cakes: exactly one gets them."""
    vid = _vid("CK-RVLT", "1 lb")
    _set_stock(vid, 2)
    results, barrier = [], threading.Barrier(2)

    def buy():
        with database.session_scope() as s:
            barrier.wait()
            results.append(inventory.reserve(s, vid, 2))

    threads = [threading.Thread(target=buy) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False, True]
    with database.session_scope() as s:
        assert inventory.available(s, vid) == 0
