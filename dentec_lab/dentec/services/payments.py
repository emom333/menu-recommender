"""Payments received from dental offices."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import PAYMENT_METHODS
from ..db.models import Invoice, Office, Payment, PaymentAllocation
from ..money import to_cents
from . import aging
from .common import BusinessError, audit, current_user, require_admin


def list_payments(s: Session, office_id: int | None = None, start: date | None = None,
                  end: date | None = None, invoice_id: int | None = None) -> list[Payment]:
    q = select(Payment).options(selectinload(Payment.allocations).selectinload(PaymentAllocation.invoice),
                                selectinload(Payment.office))
    if office_id:
        q = q.where(Payment.office_id == office_id)
    if start:
        q = q.where(Payment.payment_date >= start)
    if end:
        q = q.where(Payment.payment_date <= end)
    if invoice_id:
        q = q.where(Payment.id.in_(select(PaymentAllocation.payment_id).where(PaymentAllocation.invoice_id == invoice_id)))
    return list(s.scalars(q.order_by(Payment.payment_date.desc(), Payment.id.desc())))


def _allocate(s: Session, p: Payment, invoice_id: int | None, auto_apply: bool) -> None:
    remaining = p.unapplied_cents
    if invoice_id and remaining > 0:
        inv = s.get(Invoice, invoice_id)
        if inv is None or inv.office_id != p.office_id or inv.status != "Finalized" or inv.kind != "Invoice":
            raise BusinessError("Choose a finalized invoice belonging to this dental office.")
        bal = aging.invoice_balance(s, inv)
        amt = min(remaining, max(bal, 0))
        if amt > 0:
            p.allocations.append(PaymentAllocation(invoice_id=inv.id, amount_cents=amt))
            s.flush()
            remaining -= amt
    if auto_apply and remaining > 0:
        for inv, bal in aging.open_invoices(s, p.office_id):
            if remaining <= 0:
                break
            amt = min(remaining, bal)
            p.allocations.append(PaymentAllocation(invoice_id=inv.id, amount_cents=amt))
            s.flush()
            remaining -= amt


def record_payment(s: Session, office_id: int, payment_date: date, amount, method: str = "Check",
                   check_number: str = "", notes: str = "", invoice_id: int | None = None,
                   auto_apply: bool = True) -> Payment:
    """Record a payment.  It is applied to ``invoice_id`` first; any remainder is
    applied to the oldest open invoices (``auto_apply``) or kept as account credit."""
    if s.get(Office, office_id) is None:
        raise BusinessError("Select a dental office.")
    cents = to_cents(amount)
    if cents <= 0:
        raise BusinessError("Payment amount must be greater than zero.")
    if method not in PAYMENT_METHODS:
        raise BusinessError("Invalid payment method.")
    if payment_date is None:
        raise BusinessError("Payment date is required.")
    p = Payment(office_id=office_id, payment_date=payment_date, amount_cents=cents, method=method,
                check_number=(check_number or "").strip(), notes=notes or "", created_by=current_user().username)
    s.add(p)
    s.flush()
    _allocate(s, p, invoice_id, auto_apply)
    audit(s, "record_payment", "payment", p.id,
          f"office {office_id} {cents / 100:.2f} {method} {check_number} -> "
          + ", ".join(f"inv {a.invoice_id}: {a.amount_cents / 100:.2f}" for a in p.allocations))
    return p


def apply_credit(s: Session, payment_id: int, invoice_id: int | None = None) -> Payment:
    """Apply a payment's unapplied remainder to an invoice (or oldest invoices)."""
    p = s.get(Payment, payment_id)
    if p is None:
        raise BusinessError("Payment not found.")
    if p.unapplied_cents <= 0:
        raise BusinessError("This payment is fully applied.")
    _allocate(s, p, invoice_id, auto_apply=invoice_id is None)
    audit(s, "apply_payment_credit", "payment", p.id, f"invoice {invoice_id or 'oldest'}")
    return p


def delete_payment(s: Session, payment_id: int, reason: str = "") -> None:
    require_admin("deleting payments")
    p = s.get(Payment, payment_id)
    if p is None:
        return
    audit(s, "delete_payment", "payment", p.id, f"office {p.office_id} {p.amount_cents / 100:.2f} "
          f"{p.method} {p.check_number} dated {p.payment_date}; reason: {reason}")
    s.delete(p)


def payments_for_invoice(s: Session, invoice_id: int) -> list[tuple[Payment, int]]:
    rows = s.execute(select(Payment, PaymentAllocation.amount_cents)
                     .join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
                     .where(PaymentAllocation.invoice_id == invoice_id).order_by(Payment.payment_date))
    return [(p, amt) for p, amt in rows]
