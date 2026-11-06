"""Small read helpers that turn database rows into the plain values app.rules expects."""
from __future__ import annotations

import dataclasses

from sqlalchemy.orm import Session

from . import models as m
from .rules import RulesConfig


def load_config(s: Session) -> RulesConfig:
    """RulesConfig from the settings seeded out of the spec (falls back to the defaults for anything missing)."""
    known = {f.name for f in dataclasses.fields(RulesConfig)}
    values = {row.key: row.value for row in s.query(m.Setting).all() if row.key in known}
    if "serving_guide" in values:
        values["serving_guide"] = {k: tuple(v) for k, v in values["serving_guide"].items()}
    return RulesConfig(**values)


def areas_as_dicts(s: Session) -> list[dict]:
    return [{"name": a.name, "zone_code": a.zone_code, "aliases": a.aliases, "note": a.note} for a in s.query(m.Area).all()]


def branch_hours(s: Session, branch_name: str) -> dict[str, tuple[int, int]]:
    b = s.query(m.Branch).filter(m.Branch.name == branch_name).one()
    return {h.day_group: (h.open_min, h.close_min) for h in b.hours}


def zone_fee(s: Session, code: str) -> m.DeliveryZone:
    return s.get(m.DeliveryZone, code)
