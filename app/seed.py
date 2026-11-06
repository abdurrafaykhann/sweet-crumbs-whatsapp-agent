"""Load data/sweet-crumbs-bakery-demo.md into the database.

    python -m app.seed            # create tables and load everything (drops existing tables)

The md file is the single source of truth. This is plain data loading (tables and fixed-format bullet lines),
not language understanding. It fails loudly (SeedError) if the spec no longer looks like it did, and it checks
the spec's own arithmetic (demo order totals) while loading."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import re

from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import models as m
from .database import get_engine, session_scope
from .rules import RulesConfig, resolve_area

MD_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "sweet-crumbs-bakery-demo.md"


class SeedError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- markdown helpers
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("**", "").replace("`", "")).strip()


def num(s: str) -> int | None:
    mm = re.search(r"\d[\d,]*", s or "")
    return int(mm.group(0).replace(",", "")) if mm else None


def section_body(md: str, prefix: str) -> str:
    """Text of the heading that starts with `prefix`, up to the next heading of the same or higher level."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        h = HEADING.match(line)
        if h and clean(h.group(2)).lower().startswith(prefix.lower()):
            level, out = len(h.group(1)), []
            for nxt in lines[i + 1:]:
                hh = HEADING.match(nxt)
                if hh and len(hh.group(1)) <= level:
                    break
                out.append(nxt)
            return "\n".join(out)
    raise SeedError(f"section not found in the spec: {prefix!r}")


def subsections(body: str, level: int = 3) -> dict[str, str]:
    out, title, buf = {}, None, []
    for line in body.splitlines():
        h = HEADING.match(line)
        if h and len(h.group(1)) == level:
            if title is not None:
                out[title] = "\n".join(buf)
            title, buf = clean(h.group(2)), []
        elif title is not None:
            buf.append(line)
    if title is not None:
        out[title] = "\n".join(buf)
    return out


def tables(body: str) -> list[list[dict]]:
    """Every markdown table in `body` as a list of row dicts keyed by header."""
    found, block = [], []
    for line in body.splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line)
        elif block:
            rows = [[clean(c) for c in r.strip().strip("|").split("|")] for r in block]
            if len(rows) >= 2 and all(set(c) <= set("-: ") for c in rows[1]):
                head = rows[0]
                found.append([dict(zip(head, r)) for r in rows[2:]])
            block = []
    return found


def split_top_level(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def to_min(text: str) -> int:
    mm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text.lower())
    if not mm:
        raise SeedError(f"cannot read a time from {text!r}")
    h, mi, ap = int(mm.group(1)) % 12, int(mm.group(2) or 0), mm.group(3)
    return (h + (12 if ap == "pm" else 0)) * 60 + mi


def hours_range(text: str) -> tuple[int, int]:
    a, b = re.split(r"\s*[–-]\s*", clean(text), maxsplit=1)
    o, c = to_min(a), to_min(b)
    return o, (c + 1440 if c <= o else c)


# ----------------------------------------------------------------------------- constants parsed from prose
def money(v: str) -> int:
    return int(v.replace(",", ""))


CONSTANTS = [  # (setting key, regex on the whole spec, cast of the groups)
    ("min_order", r"\*\*Minimum order:\*\*\s*Rs\s*([\d,]+)", lambda g: money(g[0])),
    ("free_delivery_from", r"\*\*Free delivery:\*\*\s*on orders of Rs\s*([\d,]+)", lambda g: money(g[0])),
    ("schedule_days", r"up to (\d+) days ahead", lambda g: int(g[0])),
    ("advance_percent", r"need a (\d+)% advance", lambda g: int(g[0])),
    ("advance_over", r"any standard order over Rs\s*([\d,]+)", lambda g: money(g[0])),
    ("bulk_total", r"Bulk orders:\*\* over Rs\s*([\d,]+)", lambda g: money(g[0])),
    ("bulk_boxes", r"more than (\d+) boxes", lambda g: int(g[0])),
    ("same_day_cake_cutoff", r"Same-day standard cakes:\*\* order by (\d+) pm", lambda g: (int(g[0]) + 12) * 60),
    ("custom_notice_h", r"Custom or fondant cakes:\*\* (\d+) hours", lambda g: int(g[0])),
    ("tiered_notice_days", r"Tiered or wedding cakes:\*\* (\d+) days", lambda g: int(g[0])),
    ("eggless_notice_h", r"Eggless or sugar-free cakes:\*\* (\d+) hours", lambda g: int(g[0])),
    ("sugar_free_notice_h", r"Eggless or sugar-free cakes:\*\* (\d+) hours", lambda g: int(g[0])),
    ("eggless_per_lb", r"add Rs (\d+) per lb", lambda g: int(g[0])),
    ("late_grace_min", r"more than (\d+) minutes after the promised time", lambda g: int(g[0])),
    ("message_max_chars", r"Up to (\d+) characters", lambda g: int(g[0])),
    ("message_change_hours", r"cake message can be changed until (\d+) hours", lambda g: int(g[0])),
    ("refund_hours", r"cancelled (\d+) hours or more before delivery", lambda g: int(g[0])),
    ("birthday_pct", r"(\d+)% off one cake", lambda g: int(g[0])),
    ("rs_per_point", r"1 point for every Rs (\d+)", lambda g: int(g[0])),
    ("redeem_block_points", r"(\d+) points = Rs\s*([\d,]+) off", lambda g: int(g[0])),
    ("redeem_block_value", r"(\d+) points = Rs\s*([\d,]+) off", lambda g: money(g[1])),
    ("jummah_start", r"closes for Jummah from (\d+):(\d+) to (\d+):(\d+) pm", lambda g: (int(g[0]) + 12) * 60 + int(g[1])),
    ("jummah_end", r"closes for Jummah from (\d+):(\d+) to (\d+):(\d+) pm", lambda g: (int(g[2]) + 12) * 60 + int(g[3])),
    ("delivery_open", r"\*\*Delivery hours:\*\* (\d+) am", lambda g: int(g[0]) * 60),
    ("delivery_close", r"\*\*Delivery hours:\*\* \d+ am – (\d+) pm", lambda g: (int(g[0]) + 12) * 60),
    ("last_order", r"Last order at (\d+):(\d+) pm", lambda g: (int(g[0]) + 12) * 60 + int(g[1])),
    ("slot_open", r"1-hour slots between (\d+) am and (\d+) pm", lambda g: int(g[0]) * 60),
    ("slot_close", r"1-hour slots between (\d+) am and (\d+) pm", lambda g: (int(g[1]) + 12) * 60),
    ("platter_notice_h", r"Both platters need (\d+) hours' notice", lambda g: int(g[0])),
    ("photo_notice_h", r"edible photo|Edible photo print[^\n]*?(\d+) hours' notice", None),  # read from the product row instead
]


def parse_constants(md: str) -> dict:
    out = {}
    for key, rx, cast in CONSTANTS:
        if cast is None:
            continue
        mm = re.search(rx, md)
        if not mm:
            raise SeedError(f"the spec no longer matches the expected wording for {key!r}: /{rx}/")
        out[key] = cast(mm.groups())
    guide = {f"{a} lb": (int(b), int(c)) for a, b, c in re.findall(r"(\d) lb serves (\d+)–(\d+)", md)}
    if set(guide) != {"1 lb", "2 lb", "3 lb", "4 lb"}:
        raise SeedError(f"serving guide not understood: {guide}")
    out["serving_guide"] = {k: list(v) for k, v in guide.items()}
    return out


# ----------------------------------------------------------------------------- branches, zones, areas
AREA_ALIASES = {
    "dha": ["defence", "defense"],
    "gulshan-e-iqbal": ["gulshan", "gulshan iqbal", "gulshane iqbal"],
    "gulistan-e-jauhar": ["gulistan", "jauhar", "johar", "gulistan jauhar"],
    "north nazimabad": ["north naz"],
    "federal b area": ["fb area", "f b area"],
    "shahrah-e-faisal": ["sharae faisal", "shara e faisal", "sharah e faisal", "shahra e faisal", "sharea faisal"],
    "shaheed-e-millat road": ["shaheed millat"],
    "khayaban-e-ittehad": ["ittehad"],
    "kda scheme 1": ["kda scheme"],
    "seaview": ["sea view"],
    "bahria town karachi": ["bahria town", "bahria"],
    "malir cantt": ["malir cantonment", "malir cant"],
    "hawkesbay": ["hawks bay", "hawkes bay"],
}
BLOCK_AREAS = ("Gulshan-e-Iqbal", "Clifton", "Gulistan-e-Jauhar")  # areas with numbered blocks (spec test #33, Flow F.3)
LANG = {"english": "en", "roman urdu": "rur", "urdu": "ur"}


def load_branches(s: Session, md: str) -> int:
    rows = tables(section_body(md, "2. Branches and hours"))[0]
    for r in rows:
        name, note = re.match(r"^(.*?)\s*(?:\((.*)\))?$", r["Branch"]).groups()
        dine = r["Dine-in"]
        b = m.Branch(name=name, note=note or "", address=r["Address"], landmark=r["Landmark"], dine_in=dine.lower().startswith("yes"),
                     dine_in_note=re.sub(r"^(yes|no),?\s*", "", dine, flags=re.I))
        for group, col in (("mon_thu", "Mon–Thu"), ("fri", "Fri"), ("sat_sun", "Sat–Sun")):
            o, c = hours_range(r[col])
            b.hours.append(m.BranchHours(day_group=group, open_min=o, close_min=c))
        s.add(b)
    return len(rows)


def load_zones_and_areas(s: Session, md: str) -> tuple[int, int]:
    rows = tables(section_body(md, "3. Delivery"))[0]
    nz = na = 0
    for r in rows:
        code = r["Zone"]
        zone = None
        if code in ("A", "B", "C"):
            lo, hi = [int(x) for x in re.findall(r"\d+", r["Time"])[:2]]
            zone = m.DeliveryZone(code=code, fee=num(r["Fee"]), min_minutes=lo, max_minutes=hi)
            s.add(zone)
            s.flush()  # areas reference the zone: it must exist first
            nz += 1
        elif code.lower() != "not served":
            raise SeedError(f"unknown zone row {code!r}")
        for part in split_top_level(r["Areas"]):
            mm = re.match(r"^(.*?)\s*(?:\((.*)\))?$", part)
            name, note = mm.group(1).strip(), (mm.group(2) or "").strip()
            s.add(m.Area(name=name, zone_code=code if zone else None, note=note, aliases=AREA_ALIASES.get(name.lower(), [])))
            na += 1
    return nz, na


# ----------------------------------------------------------------------------- menu
NUTS = re.compile(r"contains (?:\w+ )?nuts|contains walnuts|\(contains", re.I)
NOTICE = re.compile(r"(\d+)\s*hours?'?\s*notice", re.I)


def _flags(name: str, notes: str) -> dict:
    text = f"{name} {notes}"
    mm = NOTICE.search(text)
    return {"contains_nuts": bool(NUTS.search(text)) and "nut-free" not in text.lower(),
            "notice_hours": int(mm.group(1)) if mm else 0}


def _stock(cell: str):
    cell = clean(cell)
    if "made to order" in cell.lower():
        return "made_to_order"
    n = num(cell)
    return n


def load_menu(s: Session, md: str) -> tuple[int, int]:
    menu = subsections(section_body(md, "5. Menu"))
    nprod = nvar = 0

    def add(p: m.Product, variants: list[tuple[str, int, int | None]]):
        nonlocal nprod, nvar
        p.variants = [m.Variant(label=l, price=pr, stock=st, sort=i) for i, (l, pr, st) in enumerate(variants)]
        s.add(p)
        nprod += 1
        nvar += len(variants)

    # Cakes: three sizes with their own price and stock
    for r in tables(menu["Cakes"])[0]:
        stocks = [None] * 3
        made = False
        cell = r["Stock today (1/2/3 lb)"]
        if "made to order" in cell.lower():
            made = True
        else:
            stocks = [num(x) for x in cell.split("/")]
        notes = r["Notes"]
        variants = [(lbl, num(r[lbl]), stocks[i]) for i, lbl in enumerate(("1 lb", "2 lb", "3 lb")) if num(r[lbl]) is not None]
        f = _flags(r["Cake"], notes)
        add(m.Product(sku=r["SKU"], name=r["Cake"], category="Cakes", notes=notes, eggless_ok=r["Eggless?"].lower().startswith("y"),
                      is_cake=True, made_to_order=made, unit="each", **f), variants)

    # single-price sections: SKU | Item | Price | Stock
    for title, category in (("Tea and dry cakes", "Dry cakes"), ("Pastries, cupcakes and cheesecake", "Pastries and cheesecake"),
                            ("Bread and bakes", "Bread and bakes"), ("Biscuits and cookies", "Biscuits and cookies"),
                            ("Savoury", "Savoury"), ("Brownies and donuts", "Brownies and donuts"), ("Party platters", "Party platters")):
        body = menu[title]
        section_notice = NOTICE.search(body)
        for r in tables(body)[0]:
            name = r["Item"]
            days = ["Sat", "Sun"] if re.search(r"saturday and sunday only", name, re.I) else None
            f = _flags(name, "")
            if section_notice and not f["notice_hours"]:
                f["notice_hours"] = int(section_notice.group(1))
            add(m.Product(sku=r["SKU"], name=name, category=category, sold_days=days, unit="each", **f), [("each", num(r["Price"]), num(r["Stock"]))])

    # drinks: no stock figure, some are not delivered
    for r in tables(menu["Drinks"])[0]:
        add(m.Product(sku=r["SKU"], name=r["Item"], category="Drinks", delivery_ok=r["Delivery?"].lower().startswith("yes"),
                      notes="" if r["Delivery?"].lower().startswith("yes") else "Dine-in and takeaway only, not delivered", unit="each"),
            [("each", num(r["Price"]), None)])

    # custom cakes and add-ons: prices are sentences ("Rs 4,200 per lb, minimum 2 lb")
    for r in tables(menu["Custom and theme cakes"])[0]:
        sku = r["SKU"]
        name, price, notes = r["Item"], r["Price"], r["Notes"]
        if sku == "–":
            sku = "AD-" + re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:20]
        unit = "lb" if "per lb" in price else "digit" if "per digit" in price else "each"
        mn = re.search(r"minimum (\d+) lb", price)
        f = _flags(name, notes)
        is_custom = sku in ("CU-THEME", "CU-FIGURE")
        value = 0 if price.lower().startswith("free") else num(price)
        add(m.Product(sku=sku, name=name, category="Custom cakes and add-ons", notes=f"{price}. {notes}".strip(". "),
                      is_custom=is_custom, requires_human=(sku == "CU-THEME"), made_to_order=is_custom, unit=unit,
                      min_qty=int(mn.group(1)) if mn else 1, **f),
            [("per lb" if unit == "lb" else "each", value, None)])
    return nprod, nvar


# ----------------------------------------------------------------------------- deals
def _c(sku, qty=1, variant="each", swap=None):
    return {"sku": sku, "variant": variant, "qty": qty, "swap": swap or []}


DEAL_RULES = {  # structured schedule and components for each deal; the prose in the md stays as text for the bot
    "Birthday Bundle": dict(days=None, start=None, end=None, channels=["delivery", "pickup"], min_qty=1, valid_until=dt.date(2026, 12, 31),
                            kind="bundle", notice=0, components=[_c("CK-CHOC", 1, "2 lb", ["CK-BLFR", "CK-PINE"]), _c("CC-BOX6"), _c("BR-CANDLE")]),
    "Tea Time Deal": dict(days=["Mon", "Tue", "Wed", "Thu"], start=16 * 60, end=19 * 60, channels=["dine_in", "takeaway"], min_qty=1,
                          kind="bundle", notice=0, components=[_c("SV-CPAT", 2), _c("BS-RUSK"), _c("BV-CHAI", 2)]),
    "Sunday Breakfast Box": dict(days=["Sun"], start=9 * 60, end=13 * 60, channels=["delivery", "pickup"], min_qty=1,
                                 kind="bundle", notice=0, components=[_c("BR-CROI", 4), _c("BR-MILK-L"), _c("BR-BRWN")]),
    "Office Hi-Tea Box": dict(days=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], start=None, end=None, channels=["delivery"], min_qty=15,
                              kind="bundle", notice=24, components=[_c("SV-CPAT"), _c("SV-SAMO"), _c("DS-BROW"), _c("BV-WATR")]),
    "Wednesday Donut Day": dict(days=["Wed"], start=None, end=None, channels=["delivery", "pickup", "dine_in", "takeaway"], min_qty=1,
                                kind="buy_get", notice=0, components=[{"skus": ["DS-DONG", "DS-DONN"], "buy": 4, "free": 2}]),
}


def _variant_price(s: Session, sku: str, label: str) -> int:
    v = s.query(m.Variant).join(m.Product).filter(m.Product.sku == sku, m.Variant.label == label).one_or_none()
    if v is None:
        raise SeedError(f"deal component {sku} / {label} is not on the menu")
    return v.price


def load_deals(s: Session, md: str) -> int:
    rows = tables(section_body(md, "6. Deals"))[0]
    for r in rows:
        name = re.sub(r"\s*\(.*\)$", "", r["Deal"])
        rule = DEAL_RULES.get(name)
        if rule is None:
            raise SeedError(f"deal {name!r} in the spec has no schedule rule in app/seed.py DEAL_RULES")
        price, usual = num(r["Price"]), num(r["Usual price"])
        if rule["kind"] == "bundle":  # the spec's own arithmetic: components must add up to the stated usual price
            total = sum(_variant_price(s, c["sku"], c["variant"]) * c["qty"] for c in rule["components"])
            if total != usual:
                raise SeedError(f"{name}: components add up to Rs {total} but the spec says the usual price is Rs {usual}")
        s.add(m.Deal(name=name, price=price, usual_price=usual, includes=r["Includes"], when_text=r["When"],
                     where_text=r["Where"], days=rule["days"], start_min=rule["start"], end_min=rule["end"], channels=rule["channels"],
                     min_qty=rule["min_qty"], valid_until=rule.get("valid_until"), kind=rule["kind"], components=rule["components"],
                     notice_hours=rule["notice"]))
    missing = set(DEAL_RULES) - {re.sub(r"\s*\(.*\)$", "", r["Deal"]) for r in rows}
    if missing:
        raise SeedError(f"DEAL_RULES lists deals that are not in the spec: {missing}")
    return len(rows)


# ----------------------------------------------------------------------------- customers and demo orders
def _areas_as_dicts(s: Session) -> list[dict]:
    return [{"name": a.name, "zone_code": a.zone_code, "aliases": a.aliases, "note": a.note} for a in s.query(m.Area).all()]


def load_customers(s: Session, md: str) -> int:
    rows = tables(section_body(md, "11. Demo customers"))[0]
    areas = _areas_as_dicts(s)
    for r in rows:
        c = m.Customer(phone=r["Phone"], name=r["Name"], language=LANG[r["Language"].lower()], points=num(r["Points"]) or 0,
                       birthday_month=(list(dt.datetime.strptime(r["Birthday month"], "%B").timetuple())[1]
                                       if r["Birthday month"] not in ("–", "") else None))
        cell = r["Saved address"]
        addrs = []
        if not cell.lower().startswith("none"):
            extra = re.search(r"\(\+\s*([^:]+):\s*(.*)\)\s*$", cell)
            main = cell[:extra.start()].strip() if extra else cell
            addrs.append(("home", main))
            if extra:
                addrs.append((extra.group(1).strip(), extra.group(2).strip()))
        zones = []
        for label, text in addrs:
            hit = resolve_area(text, areas)
            c.addresses.append(m.Address(label=label, text=text, area_name=hit.area, zone_code=hit.zone_code))
            zones.append(hit.zone_code or "Not served")
        expected = [z.strip() for z in r["Zone"].split("/")] if r["Zone"] != "–" else []
        if zones != expected:  # spec cross-check: the zone column must agree with our area matching
            raise SeedError(f"zone mismatch for {r['Name']}: spec says {expected}, area matching says {zones}")
        s.add(c)
    return len(rows)


def _status_key(label: str) -> str:
    return re.sub(r"[^a-z]+", "_", label.lower()).strip("_")


def load_status_labels(s: Session, md: str) -> int:
    rows = tables(section_body(md, "How to describe each status"))[0]
    for r in rows:
        s.add(m.StatusLabel(status=r["Status"], en=r["English"], rur=r["Roman Urdu"]))
    return len(rows)


def build_demo_orders(demo_today: dt.date) -> dict:
    """Item breakdown of the 9 demo orders (the md gives them as prose). Code, customer, status and total are
    taken from the md table and cross-checked in load_orders()."""
    def at(day_offset: int, hhmm: str) -> dt.datetime:
        h, mi = hhmm.split(":")
        return dt.datetime.combine(demo_today + dt.timedelta(days=day_offset), dt.time(int(h), int(mi)))

    return {
        "SC-10421": dict(items=[("CK-CHOC", "2 lb", 1, {"message": "Happy Birthday Ammi"}), ("BR-CANDLE", "each", 1, {})], addr="home", asap=True,
                         rider="Imran", eta=at(0, "18:35"), created=at(0, "17:42"), pay="cash", message="Happy Birthday Ammi"),
        "SC-10431": dict(items=[("PL-SAV", "each", 1, {})], addr="home", slot=(at(2, "17:00"), at(2, "18:00")), created=at(-1, "20:10"), pay="cash"),
        "SC-10422": dict(items=[("SV-CPAT", "each", 10, {}), ("DS-BROW", "each", 10, {})], addr="home", asap=True, created=at(0, "18:05"),
                         pay="jazzcash", paid=True),
        "SC-10398": dict(items=[("BS-NANK", "each", 2, {}), ("BS-RUSK", "each", 1, {})], addr="home", created=at(-2, "19:20"), pay="cash",
                         delivered=at(-2, "20:05")),
        "SC-10360": dict(items=[("CK-BLFR", "2 lb", 1, {})], addr="home", created=at(-9, "13:00"), pay="cash", notes="Cancelled before preparation"),
        "SC-10430": dict(items=[("CU-THEME", "per lb", 3, {"theme": "Frozen", "flavour": "chocolate fudge"}), ("CU-FIGURE", "each", 1, {})],
                         addr="home", slot=(at(3, "16:00"), at(3, "17:00")), created=at(-1, "15:30"), pay="jazzcash", advance=True, custom=True,
                         notes="Frozen theme cake, chocolate. Rs 6,700 advance pending."),
        "SC-10412": dict(items=[("CS-LOTUS", "each", 4, {})], addr="mother's", created=at(-3, "19:00"), pay="cash", delivered=at(-3, "20:10"),
                         refund=(1500, dt.date(2026, 11, 10)), notes="2 crushed slices. Rs 1,500 refund approved 5 Nov, due by 10 Nov."),
        "SC-10385": dict(items=[("CK-PINE", "1 lb", 1, {}), ("SV-CPAT", "each", 4, {})], addr="home", created=at(-7, "17:00"), pay="cash",
                         delivered=at(-7, "18:15"), notes="15 minutes late, within policy"),
        "SC-10425": dict(items=[("BR-MILK-L", "each", 2, {}), ("BR-CROI", "each", 4, {})], pickup="Gulshan-e-Iqbal", created=at(0, "11:20"), pay="counter"),
    }


def load_orders(s: Session, md: str, demo_today: dt.date) -> int:
    rows = tables(section_body(md, "12. Demo orders"))[0]
    by_code = build_demo_orders(demo_today)
    zones = {z.code: z for z in s.query(m.DeliveryZone).all()}
    cfg = RulesConfig()
    n = 0
    for r in rows:
        code = r["Code"]
        spec = by_code.get(code)
        if spec is None:
            raise SeedError(f"demo order {code} has no item breakdown in app/seed.py build_demo_orders")
        customer = s.query(m.Customer).filter(m.Customer.name.like(f"{r['Customer']}%")).one()
        o = m.Order(code=code, customer=customer, status=_status_key(r["Status"]), created_at=spec["created"], updated_at=spec["created"],
                    payment_method=spec["pay"], asap=bool(spec.get("asap")), rider_name=spec.get("rider", ""), eta=spec.get("eta"),
                    delivered_at=spec.get("delivered"), cake_message=spec.get("message", ""), notes=spec.get("notes", ""))
        subtotal = 0
        for sku, label, qty, opts in spec["items"]:
            v = (s.query(m.Variant).join(m.Product).filter(m.Product.sku == sku, m.Variant.label == label).one())
            line = v.price * qty
            subtotal += line
            o.items.append(m.OrderItem(variant_id=v.id, sku=sku, name=f"{v.product.name} ({label})" if label not in ("each", "per lb") else v.product.name,
                                       qty=qty, unit_price=v.price, line_total=line, options=opts))
        if "pickup" in spec:
            o.fulfilment, o.branch_id = "pickup", s.query(m.Branch).filter(m.Branch.name == spec["pickup"]).one().id
            fee = 0
        else:
            addr = next(a for a in customer.addresses if a.label == spec["addr"])
            o.fulfilment, o.address_text, o.zone_code = "delivery", addr.text, addr.zone_code
            fee = 0 if subtotal >= cfg.free_delivery_from else zones[addr.zone_code].fee
        o.subtotal, o.delivery_fee, o.total = subtotal, fee, subtotal + fee
        if "slot" in spec:
            o.slot_start, o.slot_end = spec["slot"]
        if spec.get("paid"):
            o.payment_status = "paid"
        if spec.get("advance") or spec.get("custom"):
            o.advance_due = math.ceil(o.total * 50 / 100)
            o.payment_status = "advance_pending"
        if "refund" in spec:
            o.refund_amount, o.refund_due = spec["refund"]
        spec_total = num(r["Total"])
        if o.total != spec_total:  # the spec's own arithmetic must hold
            raise SeedError(f"{code}: items add up to Rs {o.total} (subtotal {subtotal} + fee {fee}) but the spec says Rs {spec_total}")
        s.add(o)
        n += 1
    s.flush()
    return n


# ----------------------------------------------------------------------------- knowledge for get_info(topic)
INFO_SOURCES = [  # (slug, title, heading prefix in the spec)
    ("business_profile", "Business profile", "1. Business profile"),
    ("branches_hours", "Branches and opening hours", "2. Branches and hours"),
    ("delivery", "Delivery zones, fees and rules", "3. Delivery"),
    ("payment", "Payment methods and advance rules", "4. Payment"),
    ("deals", "Deals and offers", "6. Deals"),
    ("ordering_rules", "Ordering rules and notice periods", "Ordering rules"),
    ("cancellation_changes", "Cancellation and changes", "Cancellation and changes"),
    ("complaints_refunds", "Complaints and refunds", "Complaints and refunds"),
    ("storage", "Storage and serving", "Storage"),
    ("allergens", "Allergens", "Allergens"),
    ("loyalty", "Crumbs Club loyalty", "Crumbs Club"),
    ("other", "Franchise, jobs, classes, privacy", "Other"),
]


def load_info(s: Session, md: str) -> int:
    for slug, title, prefix in INFO_SOURCES:
        s.add(m.InfoTopic(slug=slug, title=title, text=section_body(md, prefix).strip()))
    guide = [ln for ln in section_body(md, "5. Menu").splitlines() if ln.startswith(("**Serving guide", "**Eggless"))]
    s.add(m.InfoTopic(slug="serving_guide", title="Cake sizes and serving guide", text="\n".join(guide)))
    return len(INFO_SOURCES) + 1


# ----------------------------------------------------------------------------- entry point
def demo_today_from(md: str) -> dt.date:
    mm = re.search(r"\*\*today is \w+, (\d+) (\w+) (\d{4})\*\*", md)
    if not mm:
        raise SeedError("demo date line not found in the spec")
    return dt.datetime.strptime(f"{mm.group(1)} {mm.group(2)} {mm.group(3)}", "%d %B %Y").date()


def seed_database(path: pathlib.Path = MD_PATH, reset: bool = True) -> dict:
    md = pathlib.Path(path).read_text(encoding="utf-8")
    engine = get_engine()
    if reset:
        m.Base.metadata.drop_all(engine)
    m.Base.metadata.create_all(engine)
    report = {}
    with session_scope() as s:
        s.autoflush = False  # we flush explicitly between steps (orders reference customers/variants built earlier)
        today = demo_today_from(md)
        consts = parse_constants(md)
        for k, v in {**consts, "demo_today": today.isoformat(), "block_areas": list(BLOCK_AREAS)}.items():
            s.add(m.Setting(key=k, value=v))
        report["branches"] = load_branches(s, md)
        report["zones"], report["areas"] = load_zones_and_areas(s, md)
        report["products"], report["variants"] = load_menu(s, md)
        s.flush()
        report["deals"] = load_deals(s, md)
        s.flush()
        report["customers"] = load_customers(s, md)
        report["status_labels"] = load_status_labels(s, md)
        s.flush()
        report["orders"] = load_orders(s, md, today)
        last = max(int(o.code.split("-")[1]) for o in s.query(m.Order).all())
        s.add(m.Setting(key="next_order_number", value=last + 1))
        report["info_topics"] = load_info(s, md)
        s.add(m.Counter(name="order", value=last + 1))  # atomic source of the next order code (SC-10432...)
        pay = {r["Method"]: r["Details"] for r in tables(section_body(md, "4. Payment"))[0]}
        s.add(m.Setting(key="payment_methods", value=pay))
    return report


def seed_if_empty() -> bool:
    """Load the demo data only when the database has none (used by SEED_ON_START=1 so one command seeds and starts)."""
    m.Base.metadata.create_all(get_engine())
    with session_scope() as s:
        if s.query(m.Product).count() > 0:
            return False
    seed_database(reset=False)
    return True


def main():
    ap = argparse.ArgumentParser(description="Load the Sweet Crumbs demo data from the spec markdown.")
    ap.add_argument("--file", default=str(MD_PATH))
    args = ap.parse_args()
    rep = seed_database(pathlib.Path(args.file))
    print("seeded:", json.dumps(rep))


if __name__ == "__main__":
    main()
