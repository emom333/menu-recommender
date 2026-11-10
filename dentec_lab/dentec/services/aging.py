"""Balances and accounts-receivable aging.

An invoice's open balance = total - payments allocated to it - credit notes
issued against it.  Office balances are the sum of invoice balances minus any
unapplied payment credit, so nothing is ever counted twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Invoice, Office, Payment, PaymentAllocation

BUCKETS = ["0-30 Days", "31-60 Days", "61-90 Days", "Over 90 Days"]


def paid_cents(s: Session, invoice_id: int, as_of: date | None = None) -> int:
    q = (select(func.coalesce(func.sum(PaymentAllocation.amount_cents), 0))
         .join(Payment, Payment.id == PaymentAllocation.payment_id)
         .where(PaymentAllocation.invoice_id == invoice_id))
    if as_of:
        q = q.where(Payment.payment_date <= as_of)
    return int(s.scalar(q) or 0)


def credited_cents(s: Session, invoice_id: int, as_of: date | None = None) -> int:
    q = select(func.coalesce(func.sum(Invoice.total_cents), 0)).where(
        Invoice.related_invoice_id == invoice_id, Invoice.kind == "Credit Note", Invoice.status == "Finalized")
    if as_of:
        q = q.where(Invoice.invoice_date <= as_of)
    return -int(s.scalar(q) or 0)


def invoice_balance(s: Session, inv: Invoice, as_of: date | None = None) -> int:
    if inv.status != "Finalized" or inv.kind != "Invoice":
        return 0
    return inv.total_cents - paid_cents(s, inv.id, as_of) - credited_cents(s, inv.id, as_of)


def invoice_status(s: Session, inv: Invoice) -> str:
    if inv.status == "Draft":
        return "Draft"
    if inv.status == "Void":
        return "Void"
    if inv.kind == "Credit Note":
        return "Credit Applied"
    bal = invoice_balance(s, inv)
    if bal <= 0:
        return "Paid"
    if bal < inv.total_cents:
        return "Partially Paid"
    return "Unpaid"


def bucket_for(days: int) -> int:
    if days <= 30:
        return 0
    if days <= 60:
        return 1
    if days <= 90:
        return 2
    return 3


@dataclass
class Aging:
    buckets: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    unapplied_credit_cents: int = 0
    invoices: list[tuple[Invoice, int, int]] = field(default_factory=list)  # (invoice, balance, age days)

    @property
    def total_cents(self) -> int:
        return sum(self.buckets) - self.unapplied_credit_cents

    def as_dict(self) -> dict:
        return {"buckets": self.buckets, "unapplied_credit_cents": self.unapplied_credit_cents,
                "total_cents": self.total_cents}


def office_aging(s: Session, office_id: int, as_of: date | None = None,
                 exclude_invoice_id: int | None = None) -> Aging:
    as_of = as_of or date.today()
    out = Aging()
    q = select(Invoice).where(Invoice.office_id == office_id, Invoice.kind == "Invoice",
                              Invoice.status == "Finalized", Invoice.invoice_date <= as_of)
    if exclude_invoice_id:
        q = q.where(Invoice.id != exclude_invoice_id)
    for inv in s.scalars(q.order_by(Invoice.invoice_date, Invoice.id)):
        bal = invoice_balance(s, inv, as_of)
        if bal == 0:
            continue
        days = (as_of - inv.invoice_date).days
        out.buckets[bucket_for(days)] += bal
        out.invoices.append((inv, bal, days))
    # Payments received but not yet applied to an invoice reduce the balance.
    unapplied = 0
    for p in s.scalars(select(Payment).where(Payment.office_id == office_id, Payment.payment_date <= as_of)):
        # allocations to invoices dated after as_of were not "applied" yet on that date
        unapplied += p.amount_cents - sum(a.amount_cents for a in p.allocations
                                          if a.invoice is not None and a.invoice.invoice_date <= as_of)
    out.unapplied_credit_cents = max(0, unapplied)
    return out


def all_offices_aging(s: Session, as_of: date | None = None, include_zero: bool = False) -> list[tuple[Office, Aging]]:
    rows = []
    for o in s.scalars(select(Office).order_by(func.lower(Office.name))):
        a = office_aging(s, o.id, as_of)
        if include_zero or a.total_cents or any(a.buckets):
            rows.append((o, a))
    return rows


def open_invoices(s: Session, office_id: int) -> list[tuple[Invoice, int]]:
    out = []
    for inv in s.scalars(select(Invoice).where(Invoice.office_id == office_id, Invoice.kind == "Invoice",
                                               Invoice.status == "Finalized")
                         .order_by(Invoice.invoice_date, Invoice.id)):
        bal = invoice_balance(s, inv)
        if bal > 0:
            out.append((inv, bal))
    return out
