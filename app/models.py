"""SQLAlchemy 2.0 models. Business data comes from data/sweet-crumbs-bakery-demo.md via app/seed.py.
Later milestones add conversations, handoff state and LLM usage tables."""
import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------------------- business setup
class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    note: Mapped[str] = mapped_column(String(80), default="")          # e.g. "main + kitchen"
    address: Mapped[str] = mapped_column(String(200))
    landmark: Mapped[str] = mapped_column(String(200), default="")
    dine_in: Mapped[bool] = mapped_column(Boolean, default=False)
    dine_in_note: Mapped[str] = mapped_column(String(120), default="")
    hours: Mapped[list["BranchHours"]] = relationship(back_populates="branch", cascade="all, delete-orphan")


class BranchHours(Base):
    """Minutes after midnight. close_min can exceed 1440 (closing after midnight, e.g. 12:30 am = 1470)."""
    __tablename__ = "branch_hours"
    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    day_group: Mapped[str] = mapped_column(String(10))                  # mon_thu | fri | sat_sun
    open_min: Mapped[int] = mapped_column(Integer)
    close_min: Mapped[int] = mapped_column(Integer)
    branch: Mapped[Branch] = relationship(back_populates="hours")


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)      # A | B | C
    fee: Mapped[int] = mapped_column(Integer)
    min_minutes: Mapped[int] = mapped_column(Integer)
    max_minutes: Mapped[int] = mapped_column(Integer)


class Area(Base):
    __tablename__ = "areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    zone_code: Mapped[str | None] = mapped_column(ForeignKey("delivery_zones.code"), nullable=True)  # None = not served
    note: Mapped[str] = mapped_column(String(120), default="")          # e.g. "up to Safoora Chowrangi"
    aliases: Mapped[list] = mapped_column(JSON, default=list)


class Setting(Base):
    """Business constants read from the spec (minimum order, notice periods, loyalty values...)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class InfoTopic(Base):
    """Facts the bot looks up with get_info(topic) instead of carrying them in the prompt."""
    __tablename__ = "info_topics"
    slug: Mapped[str] = mapped_column(String(60), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)


class StatusLabel(Base):
    __tablename__ = "status_labels"
    status: Mapped[str] = mapped_column(String(30), primary_key=True)
    en: Mapped[str] = mapped_column(String(80))
    rur: Mapped[str] = mapped_column(String(120))


# ----------------------------------------------------------------------------- catalogue
class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(60))
    notes: Mapped[str] = mapped_column(Text, default="")
    eggless_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    contains_nuts: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    sold_days: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e.g. ["Sat","Sun"]; None = every day
    notice_hours: Mapped[int] = mapped_column(Integer, default=0)
    is_cake: Mapped[bool] = mapped_column(Boolean, default=False)         # standard cream cakes: same-day cutoff applies
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)       # fondant/theme: 50% advance, human quote
    requires_human: Mapped[bool] = mapped_column(Boolean, default=False)
    made_to_order: Mapped[bool] = mapped_column(Boolean, default=False)
    unit: Mapped[str] = mapped_column(String(20), default="each")         # each | lb | digit
    min_qty: Mapped[int] = mapped_column(Integer, default=1)
    variants: Mapped[list["Variant"]] = relationship(back_populates="product", cascade="all, delete-orphan", order_by="Variant.sort")


class Variant(Base):
    """A size/option with its own price and stock. stock None = unlimited or made to order."""
    __tablename__ = "variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    label: Mapped[str] = mapped_column(String(40))                        # "1 lb", "2 lb", or "each"
    price: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    product: Mapped[Product] = relationship(back_populates="variants")
    __table_args__ = (UniqueConstraint("product_id", "label"),)


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usual_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    includes: Mapped[str] = mapped_column(Text, default="")
    when_text: Mapped[str] = mapped_column(String(200), default="")
    where_text: Mapped[str] = mapped_column(String(300), default="")
    days: Mapped[list | None] = mapped_column(JSON, nullable=True)        # ["Mon","Tue"...]; None = every day
    start_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[list] = mapped_column(JSON, default=list)            # delivery | pickup | dine_in | takeaway
    min_qty: Mapped[int] = mapped_column(Integer, default=1)
    valid_until: Mapped[dt.date | None] = mapped_column(nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="bundle")        # bundle | buy_get
    components: Mapped[list] = mapped_column(JSON, default=list)           # bundle: [{sku, variant, qty, swap}]; buy_get: [{skus, buy, free}]
    notice_hours: Mapped[int] = mapped_column(Integer, default=0)


# ----------------------------------------------------------------------------- customers and orders
class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    language: Mapped[str] = mapped_column(String(10), default="en")        # en | rur | ur
    points: Mapped[int] = mapped_column(Integer, default=0)
    birthday_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    addresses: Mapped[list["Address"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Address(Base):
    __tablename__ = "addresses"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    label: Mapped[str] = mapped_column(String(40), default="home")
    text: Mapped[str] = mapped_column(String(300))
    area_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    zone_code: Mapped[str | None] = mapped_column(String(2), nullable=True)   # None = not served
    customer: Mapped[Customer] = relationship(back_populates="addresses")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)               # SC-10421
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    source: Mapped[str] = mapped_column(String(10), default="bot")           # bot | demo (seeded) | staff
    fulfilment: Mapped[str] = mapped_column(String(10), default="delivery")  # delivery | pickup
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    address_text: Mapped[str] = mapped_column(String(300), default="")
    zone_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    subtotal: Mapped[int] = mapped_column(Integer, default=0)
    delivery_fee: Mapped[int] = mapped_column(Integer, default=0)
    discount: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    payment_method: Mapped[str] = mapped_column(String(20), default="cash")  # cash | jazzcash | bank | counter
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid")
    advance_due: Mapped[int] = mapped_column(Integer, default=0)
    advance_paid: Mapped[int] = mapped_column(Integer, default=0)
    slot_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    slot_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    asap: Mapped[bool] = mapped_column(Boolean, default=False)
    rider_name: Mapped[str] = mapped_column(String(60), default="")
    eta: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    cake_message: Mapped[str] = mapped_column(String(120), default="")
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False)
    receiver_name: Mapped[str] = mapped_column(String(80), default="")
    receiver_phone: Mapped[str] = mapped_column(String(20), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    refund_amount: Mapped[int] = mapped_column(Integer, default=0)
    refund_due: Mapped[dt.date | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderEvent(Base):
    """Timeline of an order: placed, status changes, payment checks, edits, cancellations (shown on the order page)."""
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(String(300))


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id"), nullable=True)
    sku: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))                         # snapshot: name + size
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit_price: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[int] = mapped_column(Integer)
    options: Mapped[dict] = mapped_column(JSON, default=dict)               # eggless, message...
    order: Mapped[Order] = relationship(back_populates="items")


# ----------------------------------------------------------------------------- conversations, drafts, handoff (M2)
class Conversation(Base):
    """One row per WhatsApp number. `turn` counts inbound customer messages: the confirm guard compares turns."""
    __tablename__ = "conversations"
    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)       # bot stays silent while True (M4)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Draft(Base):
    """The order in progress for a phone number. Lives in the database, not in the chat history, so trimming the
    history can never lose it. `state` is validated and priced by app.pricing."""
    __tablename__ = "drafts"
    phone: Mapped[str] = mapped_column(String(20), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    order_code: Mapped[str] = mapped_column(String(12), default="")
    reason: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="normal")      # normal | high
    status: Mapped[str] = mapped_column(String(10), default="open")          # open | resolved
    collected: Mapped[dict] = mapped_column(JSON, default=dict)               # draft/state at the moment of handoff
    message_id: Mapped[int] = mapped_column(Integer, default=0)               # last logged message when the handoff began
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    """Conversation log for the dashboard inbox (customer, bot, human) incl. which tools the bot used."""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    role: Mapped[str] = mapped_column(String(10))                            # customer | bot | human
    text: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(JSON, default=list)
    attachment: Mapped[dict] = mapped_column(JSON, default=dict)                      # customer images/documents: {kind, media_id}
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)                       # bot messages: time to answer the customer
    provider: Mapped[str] = mapped_column(String(20), default="")                     # bot messages: which LLM provider answered
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class MessageTag(Base):
    """Intent and mood the classifier (app/classifier.py) gave an inbound customer message. A separate table, so existing
    databases need no ALTER: create_all adds it."""
    __tablename__ = "message_tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    intent: Mapped[str] = mapped_column(String(24))
    mood: Mapped[str] = mapped_column(String(10))
    intent_conf: Mapped[float] = mapped_column(Float, default=0.0)
    mood_conf: Mapped[float] = mapped_column(Float, default=0.0)
    language: Mapped[str] = mapped_column(String(12), default="")
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)            # this tag opened an escalation
    model: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class LlmUsage(Base):
    """One row per model call (successful or not): which provider answered, tokens, latency, errors, fallbacks."""
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)   # real UTC, not the demo clock
    provider: Mapped[str] = mapped_column(String(20), index=True)                             # groq | google_genai
    model: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_kind: Mapped[str] = mapped_column(String(20), default="")                           # rate_limit | server | timeout | auth | ...
    fallback_from: Mapped[str] = mapped_column(String(20), default="")                        # set when this call served a request the primary could not


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
