"""SQLAlchemy ORM models.

Money is stored as integer cents (columns ending in ``_cents``) so that
calculations are exact.  Tax rates are stored as decimal strings.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, String,
                        Text, UniqueConstraint, Index)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# --------------------------------------------------------------- users/audit
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="staff")  # admin | staff
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    username: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ offices
class Office(Base):
    __tablename__ = "offices"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    dentist_name: Mapped[str] = mapped_column(String(200), default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    billing_address: Mapped[str] = mapped_column(Text, default="")
    shipping_address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="Active")  # Active | Inactive | On Hold
    # Billing preferences
    delivery_method: Mapped[str] = mapped_column(String(16), default="Print")  # Print | Email | Both
    invoice_email: Mapped[str] = mapped_column(String(200), default="")
    tax_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_terms: Mapped[str] = mapped_column(String(64), default="Net 30")
    show_previous_balance: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    prices: Mapped[list["OfficePrice"]] = relationship(back_populates="office", cascade="all, delete-orphan")


# ---------------------------------------------------------------- procedures
class Procedure(Base):
    __tablename__ = "procedures"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    category: Mapped[str] = mapped_column(String(100), default="Other")
    base_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Dentec reference price
    taxable: Mapped[bool] = mapped_column(Boolean, default=False)  # default taxable status
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=1000)


class OfficePrice(Base):
    __tablename__ = "office_prices"
    __table_args__ = (UniqueConstraint("office_id", "procedure_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id", ondelete="CASCADE"))
    procedure_id: Mapped[int] = mapped_column(ForeignKey("procedures.id"))
    price_cents: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    office: Mapped[Office] = relationship(back_populates="prices")
    procedure: Mapped[Procedure] = relationship()


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id", ondelete="CASCADE"), nullable=True)  # None = Dentec base list
    procedure_id: Mapped[int] = mapped_column(ForeignKey("procedures.id"))
    old_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    changed_by: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(200), default="")

    procedure: Mapped[Procedure] = relationship()


# --------------------------------------------------------------------- cases
class Case(Base):
    """One patient record (one lab slip) for a dental office in a billing month."""
    __tablename__ = "cases"
    __table_args__ = (Index("ix_cases_office_period", "office_id", "billing_year", "billing_month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    billing_year: Mapped[int] = mapped_column(Integer)
    billing_month: Mapped[int] = mapped_column(Integer)
    patient_name: Mapped[str] = mapped_column(String(200), index=True)
    case_number: Mapped[str] = mapped_column(String(64), default="")
    case_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    arch: Mapped[str] = mapped_column(String(16), default="")
    internal_notes: Mapped[str] = mapped_column(Text, default="")
    technical_details: Mapped[str] = mapped_column(Text, default="")  # JSON from lab slip (not billing)
    status: Mapped[str] = mapped_column(String(16), default="Draft")  # Draft | Approved
    source: Mapped[str] = mapped_column(String(16), default="Manual")  # Manual | OCR
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    created_by: Mapped[str] = mapped_column(String(64), default="")

    office: Mapped[Office] = relationship()
    items: Mapped[list["CaseItem"]] = relationship(back_populates="case", cascade="all, delete-orphan",
                                                   order_by="CaseItem.position")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    invoice: Mapped["Invoice | None"] = relationship(foreign_keys=[invoice_id])

    @property
    def total_cents(self) -> int:
        return sum(i.line_total_cents for i in self.items)


class CaseItem(Base):
    __tablename__ = "case_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    procedure_id: Mapped[int | None] = mapped_column(ForeignKey("procedures.id"), nullable=True)
    procedure_name: Mapped[str] = mapped_column(String(200), default="")  # snapshot of name at entry
    arch: Mapped[str] = mapped_column(String(16), default="")
    description: Mapped[str] = mapped_column(Text, default="")  # additional free text
    tooth_numbers: Mapped[str] = mapped_column(String(128), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_cents: Mapped[int] = mapped_column(Integer, default=0)  # actual price charged
    list_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # office price at entry
    taxable: Mapped[bool] = mapped_column(Boolean, default=False)
    price_override_by: Mapped[str] = mapped_column(String(64), default="")

    case: Mapped[Case] = relationship(back_populates="items")
    procedure: Mapped[Procedure | None] = relationship()

    @property
    def line_total_cents(self) -> int:
        return int(self.quantity or 0) * int(self.unit_price_cents or 0)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True)
    rel_path: Mapped[str] = mapped_column(String(500))  # relative to the documents folder
    original_name: Mapped[str] = mapped_column(String(300), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    case: Mapped[Case | None] = relationship(back_populates="attachments")


class OcrDocument(Base):
    """An imported lab slip page awaiting human review."""
    __tablename__ = "ocr_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    rel_path: Mapped[str] = mapped_column(String(500))  # page image, relative to documents
    source_rel_path: Mapped[str] = mapped_column(String(500), default="")  # original upload (PDF etc.)
    original_name: Mapped[str] = mapped_column(String(300), default="")
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="Pending")  # Pending | Approved | Rejected
    extracted_json: Mapped[str] = mapped_column(Text, default="")
    ocr_engine: Mapped[str] = mapped_column(String(64), default="")
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------ invoices
class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="Invoice")  # Invoice | Credit Note
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    invoice_number: Mapped[str] = mapped_column(String(64), default="", index=True)  # MANUALLY entered
    invoice_date: Mapped[date] = mapped_column(Date)
    period_year: Mapped[int] = mapped_column(Integer)
    period_month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="Draft")  # Draft | Finalized | Void
    bill_to: Mapped[str] = mapped_column(Text, default="")
    ship_to: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tax_rate: Mapped[str] = mapped_column(String(16), default="0")
    tax_method: Mapped[str] = mapped_column(String(16), default="invoice")  # invoice | line
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    taxable_subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, default=0)
    previous_balance_cents: Mapped[int] = mapped_column(Integer, default=0)  # snapshot at finalization
    aging_snapshot: Mapped[str] = mapped_column(Text, default="")  # JSON snapshot at finalization
    related_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finalized_by: Mapped[str] = mapped_column(String(64), default="")
    void_reason: Mapped[str] = mapped_column(Text, default="")

    office: Mapped[Office] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                      order_by="InvoiceLine.position")
    related_invoice: Mapped["Invoice | None"] = relationship(remote_side=[id])


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    patient_name: Mapped[str] = mapped_column(String(200), default="")
    line_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    taxable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


# ------------------------------------------------------------------ payments
class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    payment_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(32), default="Check")
    check_number: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_by: Mapped[str] = mapped_column(String(64), default="")

    office: Mapped[Office] = relationship()
    allocations: Mapped[list["PaymentAllocation"]] = relationship(back_populates="payment", cascade="all, delete-orphan")

    @property
    def allocated_cents(self) -> int:
        return sum(a.amount_cents for a in self.allocations)

    @property
    def unapplied_cents(self) -> int:
        return self.amount_cents - self.allocated_cents


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)

    payment: Mapped[Payment] = relationship(back_populates="allocations")
    invoice: Mapped[Invoice] = relationship()


# ---------------------------------------------------------------- deliveries
class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="Planned")  # Planned | Completed
    driver: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")

    items: Mapped[list["DeliveryItem"]] = relationship(back_populates="delivery", cascade="all, delete-orphan")


class DeliveryItem(Base):
    __tablename__ = "delivery_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id", ondelete="CASCADE"))
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"), nullable=True)
    office_id: Mapped[int] = mapped_column(ForeignKey("offices.id"))
    description: Mapped[str] = mapped_column(String(300), default="")
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)

    delivery: Mapped[Delivery] = relationship(back_populates="items")
    case: Mapped[Case | None] = relationship()
    office: Mapped[Office] = relationship()
