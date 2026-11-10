"""Dental office (customer) management."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Case, Invoice, Office, Payment
from .common import BusinessError, audit

OFFICE_FIELDS = ["name", "dentist_name", "contact_person", "billing_address", "shipping_address",
                 "phone", "email", "notes", "status", "delivery_method", "invoice_email",
                 "tax_exempt", "payment_terms", "show_previous_balance"]
OFFICE_STATUSES = ["Active", "Inactive", "On Hold"]


def list_offices(s: Session, include_inactive: bool = True, search: str = "") -> list[Office]:
    q = select(Office).order_by(func.lower(Office.name))
    if not include_inactive:
        q = q.where(Office.status != "Inactive")
    if search:
        like = f"%{search.strip()}%"
        q = q.where(Office.name.ilike(like) | Office.dentist_name.ilike(like)
                    | Office.contact_person.ilike(like) | Office.phone.ilike(like))
    return list(s.scalars(q))


def _validate(data: dict) -> None:
    if not (data.get("name") or "").strip():
        raise BusinessError("Dental Office Name is required.")
    if data.get("status") and data["status"] not in OFFICE_STATUSES:
        raise BusinessError("Invalid account status.")


def create_office(s: Session, data: dict, copy_prices_from: int | None | str = "base") -> Office:
    """Create an office.

    copy_prices_from: "base" to start from the Dentec reference prices,
    an office id to copy that office's negotiated prices, or None for an
    empty price list.
    """
    _validate(data)
    dup = s.scalar(select(Office).where(func.lower(Office.name) == data["name"].strip().lower()))
    if dup:
        raise BusinessError(f"A dental office named '{dup.name}' already exists.")
    office = Office(**{k: (v.strip() if isinstance(v, str) else v) for k, v in data.items() if k in OFFICE_FIELDS})
    s.add(office)
    s.flush()
    audit(s, "create_office", "office", office.id, office.name)
    from . import pricing
    if copy_prices_from == "base":
        pricing.copy_base_prices_to_office(s, office.id)
    elif copy_prices_from:
        pricing.copy_price_list(s, int(copy_prices_from), office.id, overwrite=True)
    return office


def update_office(s: Session, office_id: int, data: dict) -> Office:
    office = s.get(Office, office_id)
    if office is None:
        raise BusinessError("Dental office not found.")
    merged = {k: getattr(office, k) for k in OFFICE_FIELDS}
    merged.update({k: v for k, v in data.items() if k in OFFICE_FIELDS})
    _validate(merged)
    dup = s.scalar(select(Office).where(func.lower(Office.name) == merged["name"].strip().lower(),
                                        Office.id != office_id))
    if dup:
        raise BusinessError(f"A dental office named '{dup.name}' already exists.")
    changes = []
    for k in OFFICE_FIELDS:
        if k in data:
            v = data[k].strip() if isinstance(data[k], str) else data[k]
            if getattr(office, k) != v:
                changes.append(k)
                setattr(office, k, v)
    if changes:
        audit(s, "update_office", "office", office.id, ", ".join(changes))
    return office


def delete_office(s: Session, office_id: int) -> None:
    office = s.get(Office, office_id)
    if office is None:
        return
    used = (s.scalar(select(func.count(Case.id)).where(Case.office_id == office_id))
            or s.scalar(select(func.count(Invoice.id)).where(Invoice.office_id == office_id))
            or s.scalar(select(func.count(Payment.id)).where(Payment.office_id == office_id)))
    if used:
        raise BusinessError("This office has cases, invoices or payments and cannot be deleted. "
                            "Set its Account Status to Inactive instead.")
    audit(s, "delete_office", "office", office.id, office.name)
    s.delete(office)


def office_address_block(office: Office, which: str = "billing") -> str:
    addr = office.billing_address if which == "billing" else (office.shipping_address or office.billing_address)
    lines = [office.name]
    if office.dentist_name:
        lines.append(office.dentist_name)
    lines += [ln for ln in (addr or "").splitlines() if ln.strip()]
    if which == "billing" and office.phone:
        lines.append(office.phone)
    return "\n".join(lines)
