"""Business rules in plain Python. The LLM never calculates fees, zones, notice periods or refunds: it calls tools that call these.
Every function is pure (no database, no clock) so it can be unit tested; callers pass `now` from app.clock.now()."""
from __future__ import annotations

import datetime as dt
import difflib
import math
import re
from dataclasses import dataclass, field

SIZES_LB = ("1 lb", "2 lb", "3 lb", "4 lb")


@dataclass(frozen=True)
class RulesConfig:
    """Defaults mirror data/sweet-crumbs-bakery-demo.md; app.seed stores the parsed values and load_config() reads them back."""
    min_order: int = 1000
    free_delivery_from: int = 4000
    delivery_open: int = 10 * 60
    delivery_close: int = 23 * 60
    last_order: int = 22 * 60 + 30
    schedule_days: int = 7
    slot_minutes: int = 60
    slot_open: int = 11 * 60
    slot_close: int = 23 * 60
    jummah_start: int = 13 * 60
    jummah_end: int = 14 * 60 + 30
    advance_percent: int = 50
    advance_over: int = 15000
    bulk_total: int = 25000
    bulk_boxes: int = 50
    same_day_cake_cutoff: int = 21 * 60
    eggless_per_lb: int = 300
    eggless_notice_h: int = 24
    sugar_free_notice_h: int = 24
    custom_notice_h: int = 48
    platter_notice_h: int = 6
    photo_notice_h: int = 4
    tiered_notice_days: int = 5
    rs_per_point: int = 100
    redeem_block_points: int = 100
    redeem_block_value: int = 500
    birthday_pct: int = 10
    late_grace_min: int = 30
    message_max_chars: int = 30
    message_change_hours: int = 2
    refund_hours: int = 24
    serving_guide: dict = field(default_factory=lambda: {"1 lb": (6, 8), "2 lb": (12, 15), "3 lb": (18, 22), "4 lb": (25, 30)})


# ----------------------------------------------------------------------------- small helpers
def minutes(t: dt.datetime) -> int:
    return t.hour * 60 + t.minute


def fmt_minutes(m: int) -> str:
    """540 -> '9 am', 1470 -> '12:30 am', 1410 -> '11:30 pm'."""
    m %= 1440
    h, mm = divmod(m, 60)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12} {suffix}" if mm == 0 else f"{h12}:{mm:02d} {suffix}"


def day_group(d: dt.date) -> str:
    return "mon_thu" if d.weekday() <= 3 else "fri" if d.weekday() == 4 else "sat_sun"


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9؀-ۿ]+", " ", (text or "").lower()).strip()


# ----------------------------------------------------------------------------- delivery area and fees
@dataclass
class AreaMatch:
    status: str                       # served | not_served | unclear | unknown
    area: str | None = None
    zone_code: str | None = None
    note: str = ""
    candidates: tuple = ()
    fuzzy: bool = False


_FILLER = {"karachi", "pakistan", "near", "opposite", "the", "and", "behind", "next", "number", "no", "in", "at", "of", "wala", "ke", "ka", "ki", "mein", "hai"}
_ADDRESS_WORDS = {"block", "phase", "sector", "street", "lane", "flat", "house", "plot", "road", "gali", "apartment", "bungalow"}


def _alias_set(area: dict) -> set[str]:
    return {a for a in ({normalize(area["name"])} | {normalize(x) for x in area.get("aliases", [])}) if a}


def resolve_area(text: str, areas: list[dict], block_candidates: tuple = ()) -> AreaMatch:
    """Match free text (an address or an area name) to a delivery area.
    areas: [{"name", "zone_code" (None = not served), "aliases", "note"}]. The longest alias wins, so
    'DHA City' (not served) beats 'DHA' (zone A). A bare 'Block 7' is 'unclear': the caller must ask which area."""
    t = normalize(text)
    if not t:
        return AreaMatch("unknown")
    padded = f" {t} "
    hits = []
    for a in areas:
        for al in _alias_set(a):
            if f" {al} " in padded:
                hits.append((len(al), a))
    if hits:
        best = max(n for n, _ in hits)
        winners = {a["name"]: a for n, a in hits if n == best}
        zones = {a["zone_code"] for a in winners.values()}
        if len(winners) > 1 and len(zones) > 1:
            return AreaMatch("unclear", candidates=tuple(sorted(winners)))
        a = next(iter(winners.values()))
        return AreaMatch("served" if a["zone_code"] else "not_served", a["name"], a["zone_code"], a.get("note", ""))
    tokens = t.split()  # typo tolerance: 'gulshn', 'clifon'
    best_ratio, best_area = 0.0, None
    for size in (1, 2, 3):
        for i in range(len(tokens) - size + 1):
            window = " ".join(tokens[i:i + size])
            if len(window) < 5:
                continue
            for a in areas:
                for al in _alias_set(a):
                    if len(al) >= 5:
                        r = difflib.SequenceMatcher(None, window, al).ratio()
                        if r > best_ratio:
                            best_ratio, best_area = r, a
    if best_area is not None and best_ratio >= 0.84:
        return AreaMatch("served" if best_area["zone_code"] else "not_served", best_area["name"], best_area["zone_code"],
                         best_area.get("note", ""), fuzzy=True)
    if any(tok in _ADDRESS_WORDS for tok in tokens):
        # only house/block/street words and numbers left -> the customer forgot the area ("Block 7"); a leftover place name
        # we do not know ("Lahore") is "unknown" instead
        leftovers = [t for t in tokens if t not in _ADDRESS_WORDS and t not in _FILLER and not t.isdigit() and len(t) > 2]
        return AreaMatch("unknown") if leftovers else AreaMatch("unclear", candidates=tuple(block_candidates))
    return AreaMatch("unknown")


def delivery_fee(zone_fee: int, subtotal: int, cfg: RulesConfig = RulesConfig()) -> int:
    """Free from Rs 4,000 (subtotal before delivery), otherwise the zone's fee."""
    return 0 if subtotal >= cfg.free_delivery_from else zone_fee


def meets_minimum(subtotal: int, cfg: RulesConfig = RulesConfig()) -> bool:
    return subtotal >= cfg.min_order


# ----------------------------------------------------------------------------- sizes and prices
def suggest_size(guests: int, cfg: RulesConfig = RulesConfig()) -> dict | None:
    """Smallest cake that serves `guests` (rounds up across the gaps in the serving guide, e.g. 16-17 guests -> 3 lb).
    None above 30 guests: that is an events-team job. 4 lb exists in the guide but not on the menu (custom only)."""
    if guests < 1:
        return None
    for size in SIZES_LB:
        lo, hi = cfg.serving_guide[size]
        if guests <= hi:
            return {"size": size, "serves": (lo, hi), "fits_range": lo <= guests <= hi, "on_menu": size != "4 lb"}
    return None


def lb_of(label: str) -> int:
    m = re.match(r"\s*(\d+)\s*lb", label or "", re.I)
    return int(m.group(1)) if m else 0


def eggless_surcharge(size_label: str, cfg: RulesConfig = RulesConfig()) -> int:
    """Rs 300 per lb."""
    return cfg.eggless_per_lb * lb_of(size_label)


def message_ok(message: str, cfg: RulesConfig = RulesConfig()) -> bool:
    return len(message or "") <= cfg.message_max_chars


# ----------------------------------------------------------------------------- payment, advance, escalation routing
def advance_required(total: int, has_custom: bool, cfg: RulesConfig = RulesConfig()) -> tuple[bool, int]:
    """Custom/fondant cakes and any standard order over Rs 15,000 need a 50% advance."""
    need = has_custom or total > cfg.advance_over
    return need, (math.ceil(total * cfg.advance_percent / 100) if need else 0)


def human_routing_reasons(total: int, boxes: int = 0, kinds: frozenset = frozenset(), cfg: RulesConfig = RulesConfig()) -> list[str]:
    """Why an order must go to a person. kinds can contain 'wedding', 'tiered', 'custom', 'corporate'."""
    reasons = []
    if total > cfg.bulk_total:
        reasons.append("bulk_total")
    if boxes > cfg.bulk_boxes:
        reasons.append("bulk_boxes")
    if kinds & {"wedding", "tiered"}:
        reasons.append("wedding_or_tiered")
    if "custom" in kinds:
        reasons.append("custom_quote")
    return reasons


# ----------------------------------------------------------------------------- opening hours
def is_open(hours: dict[str, tuple[int, int]], at: dt.datetime, cfg: RulesConfig = RulesConfig()) -> bool:
    """hours: {'mon_thu': (open_min, close_min), 'fri': ..., 'sat_sun': ...}; close may exceed 1440 (after midnight).
    Fridays every branch closes for Jummah 1:00-2:30 pm."""
    m = minutes(at)
    if at.weekday() == 4 and cfg.jummah_start <= m < cfg.jummah_end:
        return False
    o, c = hours[day_group(at.date())]
    if o <= m < c:
        return True
    _, pc = hours[day_group(at.date() - dt.timedelta(days=1))]
    return pc > 1440 and m < pc - 1440  # still open from last night


# ----------------------------------------------------------------------------- scheduling and notice
@dataclass
class TimeCheck:
    ok: bool
    code: str = ""                    # in_past | too_far | same_day_cutoff | notice | outside_delivery_slots | jummah
    earliest: dt.datetime | None = None
    detail: str = ""


def _overlaps_jummah(start: dt.datetime, cfg: RulesConfig) -> bool:
    if start.weekday() != 4:
        return False
    s = minutes(start)
    return s < cfg.jummah_end and s + cfg.slot_minutes > cfg.jummah_start


def ceil_to_slot(t: dt.datetime, cfg: RulesConfig = RulesConfig()) -> dt.datetime:
    """Next whole-hour delivery slot at or after t, inside 11 am - 11 pm, skipping the Friday Jummah slots."""
    if t.minute or t.second or t.microsecond:
        t = t.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    while True:
        m = minutes(t)
        if m < cfg.slot_open:
            t = t.replace(hour=cfg.slot_open // 60, minute=0)
            continue
        if m + cfg.slot_minutes > cfg.slot_close:
            t = (t + dt.timedelta(days=1)).replace(hour=cfg.slot_open // 60, minute=0)
            continue
        if _overlaps_jummah(t, cfg):
            t = t.replace(hour=cfg.jummah_end // 60 + 1, minute=0)  # 3 pm
            continue
        return t


def check_slot(start: dt.datetime, now: dt.datetime, notice_hours: int = 0, is_standard_cake: bool = False,
               delivery: bool = True, cfg: RulesConfig = RulesConfig()) -> TimeCheck:
    """Can the customer have this 1-hour slot? Checks, in order: past, too far ahead, same-day cake cutoff,
    notice period (eggless/sugar-free 24 h, custom 48 h, platters 6 h...), delivery slot window, Friday Jummah."""
    if start < now:
        return TimeCheck(False, "in_past", ceil_to_slot(now + dt.timedelta(hours=notice_hours), cfg))
    if (start.date() - now.date()).days > cfg.schedule_days:
        return TimeCheck(False, "too_far", detail=f"at most {cfg.schedule_days} days ahead")
    if is_standard_cake and start.date() == now.date() and minutes(now) >= cfg.same_day_cake_cutoff:
        tomorrow = (now + dt.timedelta(days=1)).replace(hour=cfg.slot_open // 60, minute=0, second=0, microsecond=0)
        return TimeCheck(False, "same_day_cutoff", ceil_to_slot(tomorrow, cfg), "same-day cakes must be ordered before 9 pm")
    need = now + dt.timedelta(hours=notice_hours)
    if start < need:
        return TimeCheck(False, "notice", ceil_to_slot(need, cfg), f"needs {notice_hours} hours' notice")
    if delivery and (minutes(start) < cfg.slot_open or minutes(start) + cfg.slot_minutes > cfg.slot_close):
        return TimeCheck(False, "outside_delivery_slots", ceil_to_slot(start, cfg), "delivery slots run 11 am to 11 pm")
    if _overlaps_jummah(start, cfg):
        return TimeCheck(False, "jummah", ceil_to_slot(start, cfg), "all branches close for Jummah 1:00-2:30 pm")
    return TimeCheck(True)


def asap_eta(now: dt.datetime, zone_max_minutes: int, cfg: RulesConfig = RulesConfig()) -> dt.datetime:
    """Promised time for an order placed now: after the last-order time or before opening it starts at the next 10 am,
    on Fridays nothing leaves during Jummah (goes out after 2:30 pm)."""
    base = now
    m = minutes(now)
    if m > cfg.last_order:
        base = (now + dt.timedelta(days=1)).replace(hour=cfg.delivery_open // 60, minute=0, second=0, microsecond=0)
    elif m < cfg.delivery_open:
        base = now.replace(hour=cfg.delivery_open // 60, minute=0, second=0, microsecond=0)
    if base.weekday() == 4 and cfg.jummah_start <= minutes(base) < cfg.jummah_end:
        base = base.replace(hour=cfg.jummah_end // 60, minute=cfg.jummah_end % 60)
    return base + dt.timedelta(minutes=zone_max_minutes)


# ----------------------------------------------------------------------------- order lifecycle
CHANGEABLE_STATUSES = frozenset({"draft", "awaiting_advance", "confirmed", "scheduled"})


def can_change(status: str) -> bool:
    """Items, address and time can change until preparation starts."""
    return status in CHANGEABLE_STATUSES


def cake_message_editable(slot_start: dt.datetime, now: dt.datetime, cfg: RulesConfig = RulesConfig()) -> bool:
    """The cake message can change until 2 hours before delivery."""
    return slot_start - now >= dt.timedelta(hours=cfg.message_change_hours)


def cancellation(status: str, is_custom: bool, slot_start: dt.datetime | None, now: dt.datetime,
                 cfg: RulesConfig = RulesConfig()) -> dict:
    """What happens if the customer asks to cancel. The bot never refunds money itself: refunds always go to a person."""
    if status not in CHANGEABLE_STATUSES:
        return {"allowed": False, "escalate": True, "refund": "n/a", "reason": "preparation_started"}
    if is_custom:
        refundable = slot_start is not None and slot_start - now >= dt.timedelta(hours=cfg.refund_hours)
        return {"allowed": True, "escalate": True, "refund": "full" if refundable else "none", "reason": "custom_cake"}
    return {"allowed": True, "escalate": False, "refund": "n/a", "reason": "standard"}


def is_late(promised: dt.datetime, actual: dt.datetime, cfg: RulesConfig = RulesConfig()) -> bool:
    """More than 30 minutes after the promised time: the delivery fee is refunded (a person processes it)."""
    return actual - promised > dt.timedelta(minutes=cfg.late_grace_min)


# ----------------------------------------------------------------------------- loyalty (assumptions D6 in docs/DECISIONS.md)
def points_earned(total: int, cfg: RulesConfig = RulesConfig()) -> int:
    return total // cfg.rs_per_point


def redeemable(points: int, order_total: int, cfg: RulesConfig = RulesConfig()) -> dict:
    """100 points = Rs 500 off, used in whole blocks and never above the order total."""
    blocks = points // cfg.redeem_block_points
    blocks = min(blocks, order_total // cfg.redeem_block_value)
    return {"blocks": blocks, "points_used": blocks * cfg.redeem_block_points, "rs_off": blocks * cfg.redeem_block_value}


def birthday_discount(birthday_month: int | None, now: dt.datetime, cake_price: int, cfg: RulesConfig = RulesConfig()) -> int:
    """10% off one cake in the customer's birthday month (does not stack with deals or points, decision D6)."""
    if birthday_month is not None and birthday_month == now.month:
        return round(cake_price * cfg.birthday_pct / 100)
    return 0


# ----------------------------------------------------------------------------- deals
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def deal_active(deal: dict, at: dt.datetime, channel: str | None = None) -> bool:
    """deal: {'days': [...]|None, 'start_min', 'end_min', 'channels': [...], 'valid_until': date|None}."""
    if deal.get("valid_until") and at.date() > deal["valid_until"]:
        return False
    if deal.get("days") and _DAYS[at.weekday()] not in deal["days"]:
        return False
    s, e = deal.get("start_min"), deal.get("end_min")
    if s is not None and e is not None and not (s <= minutes(at) < e):
        return False
    if channel and deal.get("channels") and channel not in deal["channels"]:
        return False
    return True
