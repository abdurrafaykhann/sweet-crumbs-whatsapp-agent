"""SQLAlchemy 2.0 models. Business data comes from data/sweet-crumbs-bakery-demo.md via app/seed.py.
Later milestones add conversations, handoff state and LLM usage tables."""
import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


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
