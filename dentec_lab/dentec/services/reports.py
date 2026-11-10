"""Business reports with CSV and PDF export."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db.models import Case, Invoice, Office
from ..money import fmt
from . import aging, payments
from .invoices import MONTHS, period_label


@dataclass
class Report:
    title: str
    subtitle: str
    headers: list[str]
    rows: list[list] = field(default_factory=list)
    totals: list | None = None
    numeric_cols: set[int] = field(default_factory=set)


def _ym(y: int, m: int) -> int:
    return y * 100 + m


def _final_invoices(s: Session, start: tuple[int, int], end: tuple[int, int]) -> list[Invoice]:
    q = (select(Invoice).options(selectinload(Invoice.office))
         .where(Invoice.status == "Finalized",
                (Invoice.period_year * 100 + Invoice.period_month) >= _ym(*start),
                (Invoice.period_year * 100 + Invoice.period_month) <= _ym(*end)))
    return list(s.scalars(q))


def monthly_revenue(s: Session, year: int) -> Report:
    r = Report(f"Monthly Revenue {year}", "Finalized invoices less credit notes, by billing period",
               ["Month", "Invoices", "Subtotal", "Tax", "Credits", "Net Revenue"], numeric_cols={1, 2, 3, 4, 5})
    tot = [0, 0, 0, 0, 0]
    for m in range(1, 13):
        invs = _final_invoices(s, (year, m), (year, m))
        n = sum(1 for i in invs if i.kind == "Invoice")
        sub = sum(i.subtotal_cents for i in invs if i.kind == "Invoice")
        tax = sum(i.tax_cents for i in invs if i.kind == "Invoice")
        cred = sum(i.total_cents for i in invs if i.kind == "Credit Note")
        net = sum(i.total_cents for i in invs)
        for k, v in enumerate([n, sub, tax, cred, net]):
            tot[k] += v
        r.rows.append([MONTHS[m - 1], n, fmt(sub), fmt(tax), fmt(cred), fmt(net)])
    r.totals = ["Total", tot[0], fmt(tot[1]), fmt(tot[2]), fmt(tot[3]), fmt(tot[4])]
    return r


def revenue_by_office(s: Session, start: tuple[int, int], end: tuple[int, int]) -> Report:
    r = Report("Revenue by Dental Office", f"{period_label(*start)} - {period_label(*end)}",
               ["Dental Office", "Invoices", "Subtotal", "Tax", "Credits", "Net Revenue"], numeric_cols={1, 2, 3, 4, 5})
    agg: dict[int, list] = {}
    for i in _final_invoices(s, start, end):
        a = agg.setdefault(i.office_id, [i.office.name, 0, 0, 0, 0, 0])
        if i.kind == "Invoice":
            a[1] += 1
            a[2] += i.subtotal_cents
            a[3] += i.tax_cents
        else:
            a[4] += i.total_cents
        a[5] += i.total_cents
    tot = [0, 0, 0, 0, 0]
    for a in sorted(agg.values(), key=lambda a: -a[5]):
        for k in range(5):
            tot[k] += a[k + 1]
        r.rows.append([a[0], a[1], fmt(a[2]), fmt(a[3]), fmt(a[4]), fmt(a[5])])
    r.totals = ["Total", tot[0], fmt(tot[1]), fmt(tot[2]), fmt(tot[3]), fmt(tot[4])]
    return r


def revenue_by_procedure(s: Session, start: tuple[int, int], end: tuple[int, int]) -> Report:
    r = Report("Revenue by Procedure", f"{period_label(*start)} - {period_label(*end)} (finalized invoices, "
               "before tax and credits)", ["Procedure", "Quantity", "Revenue", "Avg Price"], numeric_cols={1, 2, 3})
    q = (select(Case).options(selectinload(Case.items)).join(Invoice, Invoice.id == Case.invoice_id)
         .where(Invoice.status == "Finalized",
                (Invoice.period_year * 100 + Invoice.period_month) >= _ym(*start),
                (Invoice.period_year * 100 + Invoice.period_month) <= _ym(*end)))
    agg: dict[str, list[int]] = {}
    for c in s.scalars(q):
        for it in c.items:
            a = agg.setdefault(it.procedure_name or "(custom description)", [0, 0])
            a[0] += it.quantity
            a[1] += it.line_total_cents
    tq = tr = 0
    for name, (qty, cents) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        tq += qty
        tr += cents
        r.rows.append([name, qty, fmt(cents), fmt(cents // qty if qty else 0)])
    r.totals = ["Total", tq, fmt(tr), ""]
    return r


def outstanding_balances(s: Session, as_of: date | None = None) -> Report:
    as_of = as_of or date.today()
    r = Report("Outstanding Balances (A/R Aging)", f"As of {as_of:%m/%d/%Y}",
               ["Dental Office", "0-30 Days", "31-60 Days", "61-90 Days", "Over 90 Days", "Unapplied Credit",
                "Total Outstanding"], numeric_cols={1, 2, 3, 4, 5, 6})
    tot = [0] * 6
    for o, a in aging.all_offices_aging(s, as_of):
        vals = a.buckets + [-a.unapplied_credit_cents, a.total_cents]
        for k, v in enumerate(vals):
            tot[k] += v
        r.rows.append([o.name] + [fmt(v) for v in vals])
    r.totals = ["Total"] + [fmt(v) for v in tot]
    return r


def payment_history(s: Session, start: date, end: date, office_id: int | None = None) -> Report:
    r = Report("Payment History", f"{start:%m/%d/%Y} - {end:%m/%d/%Y}",
               ["Date", "Dental Office", "Method", "Check #", "Amount", "Applied To", "Notes"], numeric_cols={4})
    tot = 0
    for p in reversed(payments.list_payments(s, office_id, start, end)):
        tot += p.amount_cents
        applied = ", ".join(f"#{a.invoice.invoice_number} ({fmt(a.amount_cents)})" for a in p.allocations)
        if p.unapplied_cents:
            applied += (", " if applied else "") + f"unapplied {fmt(p.unapplied_cents)}"
        r.rows.append([p.payment_date.strftime("%m/%d/%Y"), p.office.name, p.method, p.check_number,
                       fmt(p.amount_cents), applied, p.notes])
    r.totals = ["Total", "", "", "", fmt(tot), "", ""]
    return r


def monthly_case_count(s: Session, year: int) -> Report:
    r = Report(f"Monthly Case Count {year}", "Patient records by billing month",
               ["Month", "Records", "Draft", "Approved (not invoiced)", "Invoiced", "Procedures", "Amount"],
               numeric_cols={1, 2, 3, 4, 5, 6})
    tot = [0] * 6
    cases = list(s.scalars(select(Case).options(selectinload(Case.items), selectinload(Case.invoice))
                           .where(Case.billing_year == year)))
    for m in range(1, 13):
        cs = [c for c in cases if c.billing_month == m]
        inv = sum(1 for c in cs if c.invoice is not None and c.invoice.status == "Finalized")
        draft = sum(1 for c in cs if c.status == "Draft")
        appr = len(cs) - draft - inv
        procs = sum(i.quantity for c in cs for i in c.items)
        amt = sum(c.total_cents for c in cs)
        vals = [len(cs), draft, appr, inv, procs, amt]
        for k, v in enumerate(vals):
            tot[k] += v
        r.rows.append([MONTHS[m - 1]] + vals[:5] + [fmt(amt)])
    r.totals = ["Total"] + tot[:5] + [fmt(tot[5])]
    return r


def monthly_invoice_summary(s: Session, year: int, month: int) -> Report:
    r = Report("Monthly Invoice Summary", period_label(year, month),
               ["Invoice #", "Type", "Dental Office", "Date", "Status", "Subtotal", "Tax", "Total", "Paid/Credited",
                "Balance"], numeric_cols={5, 6, 7, 8, 9})
    tot = [0] * 5
    invs = list(s.scalars(select(Invoice).options(selectinload(Invoice.office))
                          .where(Invoice.period_year == year, Invoice.period_month == month,
                                 Invoice.status != "Draft").order_by(Invoice.invoice_date, Invoice.id)))
    for i in invs:
        bal = aging.invoice_balance(s, i)
        paid = (i.total_cents - bal) if (i.kind == "Invoice" and i.status == "Finalized") else 0
        vals = [i.subtotal_cents, i.tax_cents, i.total_cents, paid, bal]
        if i.status != "Void":
            for k, v in enumerate(vals):
                tot[k] += v
        r.rows.append([i.invoice_number, i.kind, i.office.name, i.invoice_date.strftime("%m/%d/%Y"),
                       aging.invoice_status(s, i)] + [fmt(v) for v in vals])
    r.totals = ["Total", "", "", "", ""] + [fmt(v) for v in tot]
    return r


REPORTS = {
    "Monthly Revenue": "monthly_revenue",
    "Revenue by Dental Office": "revenue_by_office",
    "Revenue by Procedure": "revenue_by_procedure",
    "Outstanding Balances": "outstanding_balances",
    "Payment History": "payment_history",
    "Monthly Case Count": "monthly_case_count",
    "Monthly Invoice Summary": "monthly_invoice_summary",
}


# ----------------------------------------------------------------- export
def export_csv(rep: Report, path: str | Path) -> Path:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([rep.title])
        w.writerow([rep.subtitle])
        w.writerow([])
        w.writerow(rep.headers)
        w.writerows(rep.rows)
        if rep.totals:
            w.writerow(rep.totals)
    return path


def export_pdf(rep: Report, path: str | Path, company: str = "DENTEC DENTAL LAB") -> Path:
    from reportlab.lib.pagesizes import LETTER, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    path = Path(path)
    wide = len(rep.headers) > 6
    size = landscape(LETTER) if wide else LETTER
    doc = SimpleDocTemplate(str(path), pagesize=size, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch, title=rep.title)
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=14, leading=17)
    sm = ParagraphStyle("sm", fontName="Helvetica", fontSize=9, leading=11)
    cell = ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=10)
    data = [[Paragraph(f"<b>{escape(h)}</b>", cell) for h in rep.headers]]
    for row in rep.rows + ([rep.totals] if rep.totals else []):
        data.append([Paragraph(escape(str(v)), cell) for v in row])
    tbl = Table(data, repeatRows=1)
    st = [("GRID", (0, 0), (-1, -1), 0.4, (0, 0, 0)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, 0), 1, (0, 0, 0))]
    if rep.totals:
        st.append(("LINEABOVE", (0, -1), (-1, -1), 1.2, (0, 0, 0)))
    tbl.setStyle(TableStyle(st))
    doc.build([Paragraph(escape(company), sm), Paragraph(escape(rep.title), h1), Paragraph(escape(rep.subtitle), sm),
               Paragraph(f"Generated {date.today():%m/%d/%Y}", sm), Spacer(1, 8), tbl])
    return path


def office_names(s: Session) -> dict[int, str]:
    return {o.id: o.name for o in s.scalars(select(Office))}
