"""Monthly batch case (patient record) entry."""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import ARCH_CHOICES, get_paths
from ..db.models import Attachment, Case, CaseItem, Invoice, Office, Procedure
from ..money import to_cents
from . import pricing
from .common import BusinessError, PermissionDenied, audit, current_user, get_setting

CASE_STATUSES = ["Draft", "Approved"]


class PriceOverrideRequired(PermissionDenied):
    """Raised when a staff user changes a price without administrator approval."""


@dataclass
class ItemInput:
    procedure_id: int | None = None
    procedure_name: str = ""
    arch: str = ""
    description: str = ""
    tooth_numbers: str = ""
    quantity: int = 1
    unit_price: object = None  # dollars (str/float/Decimal); None = use office price
    taxable: bool | None = None  # None = procedure/office default

    @classmethod
    def from_dict(cls, d: dict) -> "ItemInput":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CaseInput:
    office_id: int
    billing_year: int
    billing_month: int
    patient_name: str
    case_number: str = ""
    case_date: date | None = None
    due_date: date | None = None
    arch: str = ""
    internal_notes: str = ""
    technical_details: dict | None = None
    status: str = "Draft"
    source: str = "Manual"
    items: list[ItemInput] = field(default_factory=list)


def is_locked(case: Case) -> bool:
    """True when the case is on a finalized (or voided) invoice and must not change."""
    return case.invoice is not None and case.invoice.status != "Draft"


def get_case(s: Session, case_id: int) -> Case | None:
    return s.scalar(select(Case).where(Case.id == case_id)
                    .options(selectinload(Case.items), selectinload(Case.attachments)))


def list_cases(s: Session, office_id: int | None = None, year: int | None = None, month: int | None = None,
               search: str = "", status: str | None = None, uninvoiced_only: bool = False) -> list[Case]:
    q = select(Case).options(selectinload(Case.items), selectinload(Case.office), selectinload(Case.invoice))
    if office_id:
        q = q.where(Case.office_id == office_id)
    if year:
        q = q.where(Case.billing_year == year)
    if month:
        q = q.where(Case.billing_month == month)
    if status:
        q = q.where(Case.status == status)
    if uninvoiced_only:
        q = q.where(Case.invoice_id.is_(None))
    if search:
        like = f"%{search.strip()}%"
        q = q.outerjoin(Office, Office.id == Case.office_id).where(or_(
            Case.patient_name.ilike(like), Case.case_number.ilike(like), Case.internal_notes.ilike(like),
            Office.name.ilike(like),
            Case.id.in_(select(CaseItem.case_id).where(or_(CaseItem.procedure_name.ilike(like),
                                                            CaseItem.description.ilike(like),
                                                            CaseItem.tooth_numbers.ilike(like))))))
    q = q.order_by(Case.case_date.is_(None), Case.case_date, Case.id)
    return list(s.scalars(q).unique())


def _validate(s: Session, data: CaseInput) -> None:
    if not (data.patient_name or "").strip():
        raise BusinessError("Patient Name is required.")
    if s.get(Office, data.office_id) is None:
        raise BusinessError("Select a dental office.")
    if not (1 <= int(data.billing_month) <= 12) or not (2000 <= int(data.billing_year) <= 2100):
        raise BusinessError("Invalid billing month / year.")
    if data.arch not in ARCH_CHOICES:
        raise BusinessError(f"Invalid arch '{data.arch}'.")
    if data.status not in CASE_STATUSES:
        raise BusinessError("Invalid case status.")
    if data.due_date and data.case_date and data.due_date < data.case_date:
        raise BusinessError("Due Date cannot be before the case Date.")
    if data.status == "Approved" and not data.items:
        raise BusinessError("Add at least one procedure before approving the record for billing.")
    for n, it in enumerate(data.items, 1):
        if it.arch not in ARCH_CHOICES:
            raise BusinessError(f"Line {n}: invalid arch '{it.arch}'.")
        if not it.procedure_id and not (it.procedure_name or it.description).strip():
            raise BusinessError(f"Line {n}: choose a procedure or enter a description.")
        try:
            q = int(it.quantity)
        except (TypeError, ValueError):
            raise BusinessError(f"Line {n}: quantity must be a whole number.")
        if q <= 0:
            raise BusinessError(f"Line {n}: quantity must be at least 1.")


def _build_item(s: Session, office: Office, it: ItemInput, pos: int, override_by: str | None,
                prev: CaseItem | None) -> CaseItem:
    proc = s.get(Procedure, it.procedure_id) if it.procedure_id else None
    lookup = pricing.get_price(s, office.id, proc.id) if proc else pricing.PriceLookup(None, "none")
    if it.unit_price in (None, ""):
        if lookup.cents is None:
            raise BusinessError(f"Line {pos + 1}: no price on file for "
                                f"'{proc.name if proc else it.description}'. Enter a unit price.")
        unit = lookup.cents
    else:
        unit = to_cents(it.unit_price)
    if unit < 0:
        raise BusinessError(f"Line {pos + 1}: price cannot be negative.")
    override = ""
    # A price differs from the office's list price -> manual adjustment.
    if lookup.cents is not None and unit != lookup.cents:
        already = prev is not None and prev.unit_price_cents == unit and prev.price_override_by
        if already:
            override = prev.price_override_by
        else:
            user = current_user()
            need_admin = get_setting(s, "require_admin_price_override", "1") == "1"
            if user.is_admin:
                override = user.username
            elif override_by:
                override = override_by
            elif need_admin:
                raise PriceOverrideRequired(
                    f"Line {pos + 1}: the price differs from {office.name}'s price list. "
                    "An administrator must authorize manual price adjustments.")
            else:
                override = user.username
    if it.taxable is None:
        taxable = bool(proc.taxable) if proc else False
        if office.tax_exempt:
            taxable = False
    else:
        taxable = bool(it.taxable)
    return CaseItem(position=pos, procedure_id=proc.id if proc else None,
                    procedure_name=proc.name if proc else (it.procedure_name or "").strip(),
                    arch=it.arch or "", description=(it.description or "").strip(),
                    tooth_numbers=(it.tooth_numbers or "").strip(), quantity=int(it.quantity),
                    unit_price_cents=unit, list_price_cents=lookup.cents, taxable=taxable,
                    price_override_by=override)


def save_case(s: Session, data: CaseInput, case_id: int | None = None, override_by: str | None = None) -> Case:
    _validate(s, data)
    office = s.get(Office, data.office_id)
    if case_id:
        case = get_case(s, case_id)
        if case is None:
            raise BusinessError("Patient record not found.")
        if is_locked(case):
            raise BusinessError("This record is on a finalized invoice and cannot be changed. "
                                "Use a credit note for corrections.")
        if case.invoice_id and (case.office_id != data.office_id or case.billing_year != data.billing_year
                                or case.billing_month != data.billing_month):
            raise BusinessError("This record is on a draft invoice. Remove it from the draft invoice "
                                "(or delete the draft) before changing its office or billing month.")
        prev_items = list(case.items)
    else:
        case = Case(created_by=current_user().username)
        prev_items = []

    new_items = []
    for pos, it in enumerate(data.items):
        prev = prev_items[pos] if pos < len(prev_items) else None
        new_items.append(_build_item(s, office, it, pos, override_by, prev))

    case.office_id = data.office_id
    case.billing_year = int(data.billing_year)
    case.billing_month = int(data.billing_month)
    case.patient_name = data.patient_name.strip()
    case.case_number = (data.case_number or "").strip()
    case.case_date = data.case_date
    case.due_date = data.due_date
    case.arch = data.arch or ""
    case.internal_notes = data.internal_notes or ""
    if data.technical_details is not None:
        case.technical_details = json.dumps(data.technical_details)
    case.status = data.status
    case.source = data.source or "Manual"
    case.updated_at = datetime.now().replace(microsecond=0)
    if case.id is None:
        s.add(case)
    case.items.clear()
    s.flush()
    case.items.extend(new_items)
    s.flush()
    overrides = [i for i in new_items if i.price_override_by]
    audit(s, "update_case" if case_id else "create_case", "case", case.id,
          f"{case.patient_name} | {office.name} | {case.billing_month:02d}/{case.billing_year} | "
          f"{len(new_items)} items | status={case.status}"
          + (f" | price overrides by {', '.join(sorted({i.price_override_by for i in overrides}))}" if overrides else ""))
    return case


def set_status(s: Session, case_ids: list[int], status: str) -> int:
    if status not in CASE_STATUSES:
        raise BusinessError("Invalid status.")
    n = 0
    for cid in case_ids:
        c = get_case(s, cid)
        if c is None or c.status == status:
            continue
        if is_locked(c):
            raise BusinessError(f"{c.patient_name}: already invoiced; status cannot change.")
        if status == "Approved" and not c.items:
            raise BusinessError(f"{c.patient_name}: has no procedures and cannot be approved.")
        if status == "Draft" and c.invoice_id:
            raise BusinessError(f"{c.patient_name}: is on a draft invoice. Remove it from the invoice first.")
        c.status = status
        audit(s, "case_status", "case", c.id, f"{c.patient_name} -> {status}")
        n += 1
    return n


def delete_case(s: Session, case_id: int) -> None:
    c = get_case(s, case_id)
    if c is None:
        return
    if c.invoice_id:
        raise BusinessError("This record is on an invoice and cannot be deleted.")
    audit(s, "delete_case", "case", c.id, f"{c.patient_name} | office {c.office_id} | "
          f"{c.billing_month:02d}/{c.billing_year}")
    s.delete(c)


# ------------------------------------------------------------- attachments
def store_document(src: str | Path, subfolder: str | None = None) -> str:
    """Copy a file into the local document store and return its relative path."""
    src = Path(src)
    if not src.exists():
        raise BusinessError(f"File not found: {src}")
    sub = subfolder or datetime.now().strftime("%Y/%m")
    dest_dir = get_paths().documents / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in src.name)[-80:]
    dest = dest_dir / f"{uuid.uuid4().hex[:10]}_{safe}"
    shutil.copy2(src, dest)
    return dest.relative_to(get_paths().documents).as_posix()


def document_path(rel_path: str) -> Path:
    return get_paths().documents / rel_path


def add_attachment(s: Session, case_id: int, src: str | Path, already_stored: bool = False) -> Attachment:
    c = s.get(Case, case_id)
    if c is None:
        raise BusinessError("Patient record not found.")
    rel = str(src) if already_stored else store_document(src)
    a = Attachment(case_id=case_id, rel_path=rel, original_name=Path(src).name)
    s.add(a)
    s.flush()
    audit(s, "add_attachment", "case", case_id, Path(src).name)
    return a


def remove_attachment(s: Session, attachment_id: int) -> None:
    a = s.get(Attachment, attachment_id)
    if a is None:
        return
    if a.case and is_locked(a.case):
        raise BusinessError("Attachments of invoiced records cannot be removed.")
    audit(s, "remove_attachment", "case", a.case_id, a.original_name)
    s.delete(a)  # the file itself is kept in the document store (never silently destroyed)


# ---------------------------------------------------------------- summaries
@dataclass
class BatchSummary:
    count: int = 0
    draft: int = 0
    approved: int = 0
    invoiced: int = 0
    total_cents: int = 0
    approved_uninvoiced_cents: int = 0


def batch_summary(s: Session, office_id: int, year: int, month: int) -> BatchSummary:
    out = BatchSummary()
    for c in list_cases(s, office_id, year, month):
        out.count += 1
        out.total_cents += c.total_cents
        if c.invoice_id:
            out.invoiced += 1
        elif c.status == "Approved":
            out.approved += 1
            out.approved_uninvoiced_cents += c.total_cents
        else:
            out.draft += 1
    return out


def unbilled_by_office(s: Session, year: int, month: int) -> list[dict]:
    """Approved, not-yet-invoiced cases grouped by office for a billing period."""
    rows: dict[int, dict] = {}
    for c in list_cases(s, year=year, month=month, uninvoiced_only=True):
        r = rows.setdefault(c.office_id, {"office_id": c.office_id, "office": c.office.name,
                                          "approved": 0, "draft": 0, "amount_cents": 0})
        if c.status == "Approved":
            r["approved"] += 1
            r["amount_cents"] += c.total_cents
        else:
            r["draft"] += 1
    return sorted(rows.values(), key=lambda r: r["office"].lower())


def case_label(c: Case) -> str:
    parts = []
    for i in c.items:
        t = " ".join(x for x in [i.arch if i.arch != "Both" else "Upper & Lower", i.procedure_name] if x)
        parts.append(t or i.description)
    return "; ".join(parts)


def months_with_cases(s: Session, office_id: int) -> list[tuple[int, int]]:
    rows = s.execute(select(Case.billing_year, Case.billing_month).where(Case.office_id == office_id)
                     .group_by(Case.billing_year, Case.billing_month)
                     .order_by(Case.billing_year.desc(), Case.billing_month.desc()))
    return [(y, m) for y, m in rows]


def count_cases(s: Session, year: int, month: int) -> int:
    return s.scalar(select(func.count(Case.id)).where(Case.billing_year == year, Case.billing_month == month)) or 0


def invoice_of(s: Session, case: Case) -> Invoice | None:
    return s.get(Invoice, case.invoice_id) if case.invoice_id else None
