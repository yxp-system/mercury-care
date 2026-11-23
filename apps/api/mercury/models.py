import time
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid.uuid4())


def now() -> float:
    return time.time()


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Scoped(Record):
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    store_id: Mapped[str] = mapped_column(String(80), index=True)


def scoped_key():
    return UniqueConstraint("tenant_id", "store_id", "id")


def parent_key(field, table):
    return ForeignKeyConstraint(
        ["tenant_id", "store_id", field], [f"{table}.tenant_id", f"{table}.store_id", f"{table}.id"]
    )


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(160), unique=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    display_name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Scoped, Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    roles: Mapped[list] = mapped_column(JSON)
    customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", "store_id"),)


class Order(Scoped, Base):
    __tablename__ = "orders"
    customer_id: Mapped[str] = mapped_column(String(80), index=True)
    customer_name: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    paid_minor: Mapped[int] = mapped_column(Integer)
    refunded_minor: Mapped[int] = mapped_column(Integer, default=0)
    refund_reserved_minor: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="delivered")
    address: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String(8), default="GB")
    source_updated_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (
        scoped_key(),
        CheckConstraint("paid_minor >= 0 AND refunded_minor >= 0 AND refund_reserved_minor >= 0"),
        CheckConstraint("refunded_minor + refund_reserved_minor <= paid_minor"),
    )


class OrderLine(Scoped, Base):
    __tablename__ = "order_lines"
    order_id: Mapped[str] = mapped_column(String(80))
    sku: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    total_minor: Mapped[int] = mapped_column(Integer)
    remedy_minor: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        scoped_key(),
        parent_key("order_id", "orders"),
        CheckConstraint(
            "quantity > 0 AND total_minor >= 0 AND remedy_minor >= 0 AND remedy_minor <= total_minor"
        ),
    )


class Inventory(Scoped, Base):
    __tablename__ = "inventory"
    sku: Mapped[str] = mapped_column(String(100))
    on_hand: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    shipped: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("tenant_id", "store_id", "sku"),
        CheckConstraint("on_hand >= 0 AND reserved >= 0 AND reserved <= on_hand AND shipped >= 0"),
    )


class Case(Scoped, Base):
    __tablename__ = "cases"
    order_id: Mapped[str] = mapped_column(String(80))
    line_id: Mapped[str] = mapped_column(String(80))
    customer_id: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="open")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    version: Mapped[int] = mapped_column(Integer, default=1)
    plan_version: Mapped[int] = mapped_column(Integer, default=0)
    resolved_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (scoped_key(), parent_key("order_id", "orders"), parent_key("line_id", "order_lines"))


class Message(Scoped, Base):
    __tablename__ = "messages"
    case_id: Mapped[str] = mapped_column(String(80))
    author_id: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(120))
    __table_args__ = (parent_key("case_id", "cases"), UniqueConstraint("tenant_id", "store_id", "source_id"))


class Plan(Scoped, Base):
    __tablename__ = "plans"
    case_id: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    address: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    __table_args__ = (
        scoped_key(),
        parent_key("case_id", "cases"),
        UniqueConstraint("tenant_id", "store_id", "case_id", "version"),
        CheckConstraint("quantity > 0 AND amount_minor >= 0"),
    )


class Choice(Scoped, Base):
    __tablename__ = "choices"
    plan_id: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[str] = mapped_column(String(80))
    accepted: Mapped[bool] = mapped_column(Boolean)
    fingerprint: Mapped[str] = mapped_column(String(64))
    __table_args__ = (parent_key("plan_id", "plans"), UniqueConstraint("plan_id"))


class Approval(Scoped, Base):
    __tablename__ = "approvals"
    plan_id: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    __table_args__ = (parent_key("plan_id", "plans"), UniqueConstraint("plan_id", "decision"))


class Action(Scoped, Base):
    __tablename__ = "actions"
    case_id: Mapped[str] = mapped_column(String(80))
    plan_id: Mapped[str] = mapped_column(String(80), unique=True)
    request_id: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="prepared")
    fingerprint: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    remedy_minor: Mapped[int] = mapped_column(Integer, default=0)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (scoped_key(), parent_key("case_id", "cases"), parent_key("plan_id", "plans"))


class ProviderRequest(Scoped, Base):
    __tablename__ = "synthetic_provider_requests"
    request_id: Mapped[str] = mapped_column(String(80), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    receipt: Mapped[dict] = mapped_column(JSON)


class BusinessEvent(Scoped, Base):
    __tablename__ = "business_events"
    action_id: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[str] = mapped_column(String(120))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        parent_key("action_id", "actions"),
        UniqueConstraint("tenant_id", "store_id", "source_id"),
    )


class StockLedger(Scoped, Base):
    __tablename__ = "stock_ledger"
    action_id: Mapped[str] = mapped_column(String(80))
    sku: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    __table_args__ = (parent_key("action_id", "actions"), UniqueConstraint("action_id", "kind"))


class Policy(Scoped, Base):
    __tablename__ = "policies"
    family: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(8), default="GB")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    effective_from: Mapped[float] = mapped_column(Float)
    effective_until: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="synthetic")
    author_id: Mapped[str] = mapped_column(String(80))
    reviewer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list | None] = mapped_column(Vector(1536).with_variant(JSON, "sqlite"), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "store_id", "family", "version"),)


class Run(Scoped, Base):
    __tablename__ = "runs"
    case_id: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    input_version: Mapped[int] = mapped_column(Integer)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)
    used_tokens: Mapped[int] = mapped_column(Integer, default=0)
    unknown_usage: Mapped[bool] = mapped_column(Boolean, default=False)
    waiting_reason: Mapped[str] = mapped_column(Text, default="")
    draft: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (scoped_key(), parent_key("case_id", "cases"))


class RunStep(Scoped, Base):
    __tablename__ = "run_steps"
    run_id: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str] = mapped_column(String(24))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (parent_key("run_id", "runs"),)


class Job(Scoped, Base):
    __tablename__ = "jobs"
    kind: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str] = mapped_column(String(80))
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    fence: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str] = mapped_column(String(80), default="")
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    due_at: Mapped[float] = mapped_column(Float, default=now)
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (Index("jobs_ready", "status", "due_at"),)


class Attachment(Scoped, Base):
    __tablename__ = "attachments"
    case_id: Mapped[str] = mapped_column(String(80))
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    scan_status: Mapped[str] = mapped_column(String(24), default="unscanned")
    __table_args__ = (parent_key("case_id", "cases"),)


class Notification(Scoped, Base):
    __tablename__ = "notifications"
    case_id: Mapped[str] = mapped_column(String(80))
    recipient_id: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    request_id: Mapped[str] = mapped_column(String(80), unique=True, default=uid)
    intent_hash: Mapped[str] = mapped_column(String(64), unique=True)
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (parent_key("case_id", "cases"),)


class Claim(Scoped, Base):
    __tablename__ = "claims"
    case_id: Mapped[str] = mapped_column(String(80))
    counterparty: Mapped[str] = mapped_column(String(160))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    evidence: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(String(80))
    settlement_ref: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (parent_key("case_id", "cases"), CheckConstraint("amount_minor > 0"))


class Improvement(Scoped, Base):
    __tablename__ = "improvements"
    title: Mapped[str] = mapped_column(String(200))
    hypothesis: Mapped[str] = mapped_column(Text)
    measure: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="investigating")
    due_at: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list] = mapped_column(JSON)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Audit(Scoped, Base):
    __tablename__ = "audit"
    actor_id: Mapped[str] = mapped_column(String(80))
    verb: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(80), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
