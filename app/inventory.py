"""Stock handling. Stock is per variant (size/option). NULL stock = unlimited or made to order.
reserve() is atomic: it only deducts when enough stock is still there, so two customers confirming at the same
moment can never both get the last cake."""
from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import models as m


def available(s: Session, variant_id: int) -> int | None:
    """Units left, or None when the variant is unlimited / made to order."""
    return s.get(m.Variant, variant_id).stock


def reserve(s: Session, variant_id: int, qty: int) -> bool:
    v = s.get(m.Variant, variant_id)
    if v is None or qty < 1:
        return False
    if v.stock is None:
        return True
    res = s.execute(update(m.Variant).where(m.Variant.id == variant_id, m.Variant.stock >= qty)
                    .values(stock=m.Variant.stock - qty).execution_options(synchronize_session=False))
    s.expire(v)
    return res.rowcount == 1


def release(s: Session, variant_id: int, qty: int) -> None:
    """Give units back (cancelled order). Unlimited variants are left alone."""
    s.execute(update(m.Variant).where(m.Variant.id == variant_id, m.Variant.stock.is_not(None))
              .values(stock=m.Variant.stock + qty).execution_options(synchronize_session=False))
    v = s.get(m.Variant, variant_id)
    if v is not None:
        s.expire(v)


def reserve_many(s: Session, items: list[tuple[int, int]]) -> tuple[bool, int | None]:
    """All-or-nothing for a whole order. Returns (ok, first variant id that did not have enough stock)."""
    nested = s.begin_nested()
    for variant_id, qty in items:
        if not reserve(s, variant_id, qty):
            nested.rollback()
            return False, variant_id
    nested.commit()
    return True, None
