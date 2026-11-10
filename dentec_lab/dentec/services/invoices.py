"""Monthly invoices, credit notes and duplicate-billing protection.

Invoice numbers are ALWAYS typed by the user.  This module never generates,
suggests into the field, or increments invoice numbers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db.models import Case, CaseItem, Invoice, InvoiceLine, Office
from ..money import parse_rate, tax_on, to_cents
from . import aging
from .common import BusinessError, audit, current_user, get_setting, require_admin
from .offices import office_address_block

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def period_label(year: int, month: int) -> str:
    return f"{MONTHS[month - 1]} {year}"


def get_invoice(s: Session, invoice_id: int) -> Invoice | None:
    return s.scalar(select(Invoice).where(Invoice.id == invoice_id)
                    .options(selectinload(Invoice.lines), selectinload(Invoice.office)))


def list_invoices(s: Session, office_id: int | None = None, status: str | None = None, number: str = "",
                  year: int | None = None, month: int | None = None, kind: str | None = None) -> list[Invoice]:
    q = select(Invoice).options(selectinload(Invoice.office))
    if office_id:
        q = q.where(Invoice.office_id == office_id)
    if status:
        q = q.where(Invoice.status == status)
    if number:
        q = q.where(Invoice.invoice_number.ilike(f"%{number.strip()}%"))
    if year:
        q = q.where(Invoice.period_year == year)
    if month:
        q = q.where(Invoice.period_month == month)
    if kind:
        q = q.where(Invoice.kind == kind)
    q = q.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
    return list(s.scalars(q))


# ----------------------------------------------------------- line building
def _arch_text(arches: list[str]) -> str:
    out: list[str] = []
    for a in arches:
        for part in (["Upper", "Lower"] if a == "Both" else [a]):
            if part and part not in out:
                out.append(part)
    return ", ".join(out)


def build_lines_for_case(case: Case) -> list[dict]:
    """Turn a case into invoice line dicts.

    Items that are identical except for the arch are merged, e.g. an Upper
    Bite Block and a Lower Bite Block become "Upper, Lower Bite Block".
    """
    groups: list[dict] = []
    for it in case.items:
        key = (it.procedure_name, it.description, it.tooth_numbers, it.unit_price_cents, it.taxable, it.quantity)
        if it.arch in ("Upper", "Lower"):
            g = next((g for g in groups if g["key"] == key and g["mergeable"] and it.arch not in g["arches"]), None)
            if g is not None:
                g["arches"].append(it.arch)
                g["qty"] += it.quantity
                continue
        groups.append({"key": key, "mergeable": it.arch in ("Upper", "Lower"), "arches": [it.arch],
                       "qty": it.quantity, "item": it})
    lines = []
    for g in groups:
        it: CaseItem = g["item"]
        arch = _arch_text(g["arches"])
        text = " ".join(x for x in [arch, it.procedure_name] if x)
        extras = []
        if it.description:
            extras.append(it.description)
        if it.tooth_numbers:
            extras.append(f"#{it.tooth_numbers}")
        if extras:
            if it.procedure_name:
                text = f"{text} - {', '.join(extras)}"
            else:  # free-text line, e.g. "Lower 2 Clasp"
                text = " ".join(x for x in [arch, ", ".join(extras)] if x)
        qty = g["qty"]
        amount = qty * it.unit_price_cents
        if qty > 1:
            text += f" (x{qty} @ ${it.unit_price_cents / 100:,.2f})"
        lines.append({"case_id": case.id, "patient_name": case.patient_name, "line_date": case.case_date,
                      "description": text, "quantity": qty, "unit_price_cents": it.unit_price_cents,
                      "amount_cents": amount, "taxable": it.taxable, "is_manual": False})
    return lines


def compute_totals(lines: list, tax_rate, method: str = "invoice") -> dict:
    rate = parse_rate(tax_rate)
    def g(l, k):
        return l[k] if isinstance(l, dict) else getattr(l, k)
    subtotal = sum(g(l, "amount_cents") for l in lines)
    taxable = sum(g(l, "amount_cents") for l in lines if g(l, "taxable"))
    if method == "line":
        tax = sum(tax_on(g(l, "amount_cents"), rate) for l in lines if g(l, "taxable"))
    else:
        tax = tax_on(taxable, rate)
    return {"subtotal_cents": subtotal, "taxable_subtotal_cents": taxable, "tax_cents": tax,
            "total_cents": subtotal + tax}


def _apply_totals(inv: Invoice) -> None:
    t = compute_totals(inv.lines, inv.tax_rate, inv.tax_method)
    inv.subtotal_cents = t["subtotal_cents"]
    inv.taxable_subtotal_cents = t["taxable_subtotal_cents"]
    inv.tax_cents = t["tax_cents"]
    inv.total_cents = t["total_cents"]


def billable_cases(s: Session, office_id: int, year: int, month: int, invoice_id: int | None = None) -> list[Case]:
    """Approved cases for office/period not on any other invoice."""
    q = (select(Case).options(selectinload(Case.items))
         .where(Case.office_id == office_id, Case.billing_year == year, Case.billing_month == month,
                Case.status == "Approved"))
    if invoice_id:
        q = q.where((Case.invoice_id.is_(None)) | (Case.invoice_id == invoice_id))
    else:
        q = q.where(Case.invoice_id.is_(None))
    q = q.order_by(Case.case_date.is_(None), Case.case_date, func.lower(Case.patient_name), Case.id)
    return list(s.scalars(q))


# --------------------------------------------------------------- drafting
def create_draft(s: Session, office_id: int, year: int, month: int, invoice_date: date | None = None,
                 invoice_number: str = "", case_ids: list[int] | None = None) -> Invoice:
    office = s.get(Office, office_id)
    if office is None:
        raise BusinessError("Dental office not found.")
    cases = billable_cases(s, office_id, year, month)
    if case_ids is not None:
        cases = [c for c in cases if c.id in set(case_ids)]
    if not cases:
        raise BusinessError(f"{office.name} has no approved, un-invoiced records for {period_label(year, month)}.")
    inv = Invoice(kind="Invoice", office_id=office_id, invoice_number=(invoice_number or "").strip(),
                  invoice_date=invoice_date or date.today(), period_year=year, period_month=month,
                  status="Draft", bill_to=office_address_block(office, "billing"),
                  ship_to=office_address_block(office, "shipping"),
                  tax_rate=get_setting(s, "tax_rate", "0") or "0",
                  tax_method=get_setting(s, "tax_method", "invoice") or "invoice",
                  created_by=current_user().username)
    s.add(inv)
    s.flush()
    for c in cases:
        c.invoice_id = inv.id
    s.flush()
    _rebuild_lines(s, inv)
    audit(s, "create_draft_invoice", "invoice", inv.id, f"{office.name} {period_label(year, month)}: "
          f"{len(cases)} records")
    return inv


def _rebuild_lines(s: Session, inv: Invoice) -> None:
    manual = [l for l in inv.lines if l.is_manual]
    inv.lines.clear()
    s.flush()
    cases = list(s.scalars(select(Case).options(selectinload(Case.items)).where(Case.invoice_id == inv.id)
                           .order_by(Case.case_date.is_(None), Case.case_date, func.lower(Case.patient_name),
                                     Case.id)))
    pos = 0
    for c in cases:
        for d in build_lines_for_case(c):
            inv.lines.append(InvoiceLine(position=pos, **d))
            pos += 1
    for m in manual:
        inv.lines.append(InvoiceLine(position=pos, case_id=None, patient_name=m.patient_name, line_date=m.line_date,
                                     description=m.description, quantity=m.quantity,
                                     unit_price_cents=m.unit_price_cents, amount_cents=m.amount_cents,
                                     taxable=m.taxable, is_manual=True))
        pos += 1
    s.flush()
    _apply_totals(inv)


def _require_draft(inv: Invoice | None) -> Invoice:
    if inv is None:
        raise BusinessError("Invoice not found.")
    if inv.status != "Draft":
        raise BusinessError("This invoice is finalized and protected from changes. "
                            "Use a credit note for corrections.")
    return inv


def refresh_draft(s: Session, invoice_id: int, include_new: bool = True) -> Invoice:
    """Re-read linked cases (and optionally pull in newly approved ones)."""
    inv = _require_draft(get_invoice(s, invoice_id))
    for c in s.scalars(select(Case).where(Case.invoice_id == inv.id)):
        if c.status != "Approved":
            c.invoice_id = None
    if include_new:
        for c in billable_cases(s, inv.office_id, inv.period_year, inv.period_month):
            c.invoice_id = inv.id
    s.flush()
    _rebuild_lines(s, inv)
    return inv


def remove_case_from_draft(s: Session, invoice_id: int, case_id: int) -> Invoice:
    inv = _require_draft(get_invoice(s, invoice_id))
    c = s.get(Case, case_id)
    if c is None or c.invoice_id != inv.id:
        raise BusinessError("That record is not on this invoice.")
    c.invoice_id = None
    s.flush()
    _rebuild_lines(s, inv)
    audit(s, "remove_case_from_draft", "invoice", inv.id, c.patient_name)
    return inv


def update_draft(s: Session, invoice_id: int, *, invoice_number: str | None = None,
                 invoice_date: date | None = None, bill_to: str | None = None, ship_to: str | None = None,
                 notes: str | None = None, tax_rate=None, tax_method: str | None = None) -> Invoice:
    inv = _require_draft(get_invoice(s, invoice_id))
    if invoice_number is not None:
        inv.invoice_number = invoice_number.strip()
    if invoice_date is not None:
        inv.invoice_date = invoice_date
    if bill_to is not None:
        inv.bill_to = bill_to
    if ship_to is not None:
        inv.ship_to = ship_to
    if notes is not None:
        inv.notes = notes
    if tax_rate is not None:
        inv.tax_rate = str(parse_rate(tax_rate))
    if tax_method is not None:
        if tax_method not in ("invoice", "line"):
            raise BusinessError("Invalid tax calculation method.")
        inv.tax_method = tax_method
    _apply_totals(inv)
    return inv


def add_manual_line(s: Session, invoice_id: int, description: str, amount, taxable: bool = False,
                    patient_name: str = "", quantity: int = 1) -> InvoiceLine:
    inv = _require_draft(get_invoice(s, invoice_id))
    if not (description or "").strip():
        raise BusinessError("Description is required.")
    unit = to_cents(amount)
    line = InvoiceLine(position=len(inv.lines), description=description.strip(), patient_name=patient_name.strip(),
                       quantity=int(quantity), unit_price_cents=unit, amount_cents=unit * int(quantity),
                       taxable=taxable, is_manual=True)
    inv.lines.append(line)
    s.flush()
    _apply_totals(inv)
    audit(s, "add_manual_line", "invoice", inv.id, f"{description} {amount}")
    return line


def remove_manual_line(s: Session, invoice_id: int, line_id: int) -> None:
    inv = _require_draft(get_invoice(s, invoice_id))
    line = next((l for l in inv.lines if l.id == line_id), None)
    if line is None or not line.is_manual:
        raise BusinessError("Only manually added lines can be removed. Remove the patient record instead.")
    inv.lines.remove(line)
    s.flush()
    _apply_totals(inv)


def delete_draft(s: Session, invoice_id: int) -> None:
    inv = _require_draft(get_invoice(s, invoice_id))
    for c in s.scalars(select(Case).where(Case.invoice_id == inv.id)):
        c.invoice_id = None
    audit(s, "delete_draft_invoice", "invoice", inv.id, f"#{inv.invoice_number or '(no number)'}")
    s.delete(inv)


# ------------------------------------------------------- number checks
@dataclass
class NumberCheck:
    ok: bool
    errors: list[str]
    warnings: list[str]


def check_invoice_number(s: Session, invoice_id: int, number: str) -> NumberCheck:
    """Validate a manually entered invoice number. Never changes it."""
    inv = get_invoice(s, invoice_id)
    number = (number or "").strip()
    errors, warnings = [], []
    if not number:
        errors.append("Enter the invoice number. Invoice numbers are never generated automatically.")
        return NumberCheck(False, errors, warnings)
    others = list(s.scalars(select(Invoice).options(selectinload(Invoice.office))
                            .where(func.lower(Invoice.invoice_number) == number.lower(), Invoice.id != invoice_id)))
    for o in others:
        where = "this office" if o.office_id == inv.office_id else o.office.name
        warnings.append(f"Invoice number {number} is already used by {where} "
                        f"({o.kind}, {o.status}, dated {o.invoice_date:%m/%d/%Y}).")
    return NumberCheck(True, errors, warnings)


def existing_for_period(s: Session, office_id: int, year: int, month: int, exclude_id: int | None = None) -> list[Invoice]:
    q = select(Invoice).where(Invoice.office_id == office_id, Invoice.period_year == year,
                              Invoice.period_month == month, Invoice.kind == "Invoice",
                              Invoice.status != "Void")
    if exclude_id:
        q = q.where(Invoice.id != exclude_id)
    return list(s.scalars(q))


def last_number_hint(s: Session, office_id: int) -> str:
    """The most recent number used for this office, shown only as a reference hint."""
    inv = s.scalar(select(Invoice).where(Invoice.office_id == office_id, Invoice.status == "Finalized",
                                         Invoice.kind == "Invoice", Invoice.invoice_number != "")
                   .order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).limit(1))
    return inv.invoice_number if inv else ""


# ------------------------------------------------------------- finalize
def finalize(s: Session, invoice_id: int, invoice_number: str | None = None,
             confirm_duplicates: bool = False) -> Invoice:
    inv = _require_draft(get_invoice(s, invoice_id))
    if invoice_number is not None:
        inv.invoice_number = invoice_number.strip()
    chk = check_invoice_number(s, inv.id, inv.invoice_number)
    if not chk.ok:
        raise BusinessError(chk.errors[0])
    if chk.warnings and not confirm_duplicates:
        raise DuplicateNumberWarning(chk.warnings)
    if inv.kind == "Invoice":
        _rebuild_lines(s, inv)  # snapshot the latest approved case data
        if not inv.lines:
            raise BusinessError("The invoice has no lines.")
        # Duplicate billing guard: every linked case must be exclusively ours.
        for c in s.scalars(select(Case).where(Case.invoice_id == inv.id)):
            if c.status != "Approved":
                raise BusinessError(f"{c.patient_name} is not approved for billing.")
    _apply_totals(inv)
    # Snapshot previous balance and aging as of the invoice date (for reprints).
    snap = aging.office_aging(s, inv.office_id, as_of=inv.invoice_date, exclude_invoice_id=inv.id)
    inv.previous_balance_cents = snap.total_cents
    inv.status = "Finalized"
    inv.finalized_at = datetime.now().replace(microsecond=0)
    inv.finalized_by = current_user().username
    s.flush()
    after = aging.office_aging(s, inv.office_id, as_of=inv.invoice_date)
    inv.aging_snapshot = json.dumps(after.as_dict())
    audit(s, "finalize_invoice", "invoice", inv.id,
          f"#{inv.invoice_number} {inv.office.name} total={inv.total_cents}")
    return inv


class DuplicateNumberWarning(BusinessError):
    def __init__(self, warnings: list[str]):
        super().__init__("\n".join(warnings))
        self.warnings = warnings


# ------------------------------------------------------ corrections/void
def void_invoice(s: Session, invoice_id: int, reason: str) -> Invoice:
    require_admin("voiding invoices")
    inv = get_invoice(s, invoice_id)
    if inv is None or inv.status != "Finalized":
        raise BusinessError("Only finalized invoices can be voided.")
    if not (reason or "").strip():
        raise BusinessError("A reason is required to void an invoice.")
    if aging.paid_cents(s, inv.id) or aging.credited_cents(s, inv.id):
        raise BusinessError("This invoice has payments or credit notes applied. "
                            "Remove them first, or issue a credit note instead.")
    inv.status = "Void"
    inv.void_reason = reason.strip()
    for c in s.scalars(select(Case).where(Case.invoice_id == inv.id)):
        c.invoice_id = None  # release records so they can be billed correctly
    audit(s, "void_invoice", "invoice", inv.id, f"#{inv.invoice_number}: {reason}")
    return inv


def create_credit_note(s: Session, original_id: int, invoice_number: str, amount, reason: str,
                       taxable_amount=None, credit_date: date | None = None,
                       confirm_duplicates: bool = False) -> Invoice:
    """Issue a credit note (negative invoice) against a finalized invoice."""
    require_admin("credit notes")
    orig = get_invoice(s, original_id)
    if orig is None or orig.status != "Finalized" or orig.kind != "Invoice":
        raise BusinessError("Credit notes can only be issued against finalized invoices.")
    if not (reason or "").strip():
        raise BusinessError("Enter a reason / description for the credit.")
    cents = abs(to_cents(amount))
    if cents <= 0:
        raise BusinessError("Credit amount must be greater than zero.")
    tax_cents = abs(to_cents(taxable_amount)) if taxable_amount not in (None, "") else 0
    if tax_cents > cents:
        raise BusinessError("Taxable portion cannot exceed the credit amount.")
    cn = Invoice(kind="Credit Note", office_id=orig.office_id, invoice_number=(invoice_number or "").strip(),
                 invoice_date=credit_date or date.today(), period_year=orig.period_year,
                 period_month=orig.period_month, status="Draft", bill_to=orig.bill_to, ship_to=orig.ship_to,
                 tax_rate=orig.tax_rate, tax_method=orig.tax_method, related_invoice_id=orig.id,
                 notes=f"Credit against invoice #{orig.invoice_number}", created_by=current_user().username)
    s.add(cn)
    s.flush()
    pos = 0
    if cents - tax_cents:
        cn.lines.append(InvoiceLine(position=pos, description=reason.strip(), quantity=1,
                                    unit_price_cents=-(cents - tax_cents), amount_cents=-(cents - tax_cents),
                                    taxable=False, is_manual=True))
        pos += 1
    if tax_cents:
        cn.lines.append(InvoiceLine(position=pos, description=reason.strip() + " (taxable)", quantity=1,
                                    unit_price_cents=-tax_cents, amount_cents=-tax_cents, taxable=True,
                                    is_manual=True))
    s.flush()
    _apply_totals(cn)
    bal = aging.invoice_balance(s, orig)
    if -cn.total_cents > bal:
        s.delete(cn)
        s.flush()
        raise BusinessError(f"Credit (${-cn.total_cents / 100:,.2f} incl. tax) exceeds the invoice's "
                            f"open balance (${bal / 100:,.2f}).")
    try:
        finalize(s, cn.id, confirm_duplicates=confirm_duplicates)
    except BusinessError:
        s.delete(cn)
        s.flush()
        raise
    audit(s, "credit_note", "invoice", cn.id, f"#{cn.invoice_number} against #{orig.invoice_number}: {reason}")
    return cn


def invoice_cases(s: Session, invoice_id: int) -> list[Case]:
    return list(s.scalars(select(Case).options(selectinload(Case.items)).where(Case.invoice_id == invoice_id)
                          .order_by(Case.case_date, Case.patient_name)))
