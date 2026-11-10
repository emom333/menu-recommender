"""Black & white, print-optimised US Letter invoice (ReportLab).

White background, black text, thin black rules, no colour and no images.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)
from xml.sax.saxutils import escape

from ..money import fmt, parse_rate

BLACK = (0, 0, 0)
PAGE_W, PAGE_H = LETTER
MARGIN = 0.5 * inch
HEADER_H = 1.55 * inch

ST_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=11)
ST_CELL_R = ParagraphStyle("cellr", parent=ST_CELL, alignment=TA_RIGHT)
ST_ADDR = ParagraphStyle("addr", fontName="Helvetica", fontSize=9.5, leading=12)
ST_LABEL = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8.5, leading=10)
ST_SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=8.5, leading=10.5)


def _p(text: str, style=ST_CELL) -> Paragraph:
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


class _NumberedCanvas(rl_canvas.Canvas):
    """Canvas that knows the total page count (for 'Page X of Y')."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("Helvetica", 8)
            self.drawRightString(PAGE_W - MARGIN, MARGIN - 14, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _draw_header(c: rl_canvas.Canvas, data: dict) -> None:
    co = data["company"]
    top = PAGE_H - MARGIN
    c.setFillColorRGB(*BLACK)
    c.setStrokeColorRGB(*BLACK)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(MARGIN, top - 16, co["name"])
    c.setFont("Helvetica", 9.5)
    y = top - 31
    for ln in [co.get("address1"), co.get("address2"), co.get("website"), co.get("phone"), co.get("email")]:
        if ln:
            c.drawString(MARGIN, y, ln)
            y -= 11.5

    title = data["title"]
    c.setFont("Helvetica-Bold", 18)
    c.drawRightString(PAGE_W - MARGIN, top - 16, title)
    # info box
    bw, bh = 2.75 * inch, 0.78 * inch
    bx, by = PAGE_W - MARGIN - bw, top - 26 - bh
    c.setLineWidth(0.6)
    c.rect(bx, by, bw, bh, stroke=1, fill=0)
    rows = [(data["number_label"], data["invoice_number"] or "(not entered)"),
            ("Invoice Date", data["invoice_date"]),
            ("Billing Period", data["period"])]
    if data.get("related"):
        rows.append(("Credit For Invoice", data["related"]))
    ry = by + bh - 15
    for lbl, val in rows:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(bx + 7, ry, lbl)
        c.setFont("Helvetica", 9.5)
        c.drawRightString(bx + bw - 7, ry, str(val))
        ry -= 13.5 if len(rows) <= 3 else 11.5
    if data.get("draft"):
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(PAGE_W / 2, top - 16, "DRAFT PREVIEW - NOT FINALIZED")
    c.setLineWidth(1)
    c.line(MARGIN, PAGE_H - MARGIN - HEADER_H + 6, PAGE_W - MARGIN, PAGE_H - MARGIN - HEADER_H + 6)


def _footer(c: rl_canvas.Canvas, data: dict) -> None:
    c.setFont("Helvetica", 8)
    c.drawString(MARGIN, MARGIN - 14, f"{data['company']['name']}  |  {data['number_label']} "
                                      f"{data['invoice_number'] or '-'}  |  {data['office_name']}")


def _on_page(c, doc):
    c.saveState()
    _draw_header(c, doc._inv_data)
    _footer(c, doc._inv_data)
    c.restoreState()


def render_invoice_pdf(data: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(path), pagesize=LETTER, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN + HEADER_H, bottomMargin=MARGIN + 6,
                          title=f"{data['title'].title()} {data['invoice_number']}",
                          author=data["company"]["name"], subject=f"{data['office_name']} {data['period']}")
    doc._inv_data = data
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_on_page)])
    W = doc.width
    story = []

    # Bill To / Ship To
    addr = Table([[_p("BILL TO", ST_LABEL), _p("SHIP TO", ST_LABEL)],
                  [_p(data["bill_to"], ST_ADDR), _p(data["ship_to"], ST_ADDR)]],
                 colWidths=[W / 2 - 4, W / 2 - 4], hAlign="LEFT", spaceAfter=0)
    addr.setStyle(TableStyle([
        ("BOX", (0, 0), (0, -1), 0.6, BLACK), ("BOX", (1, 0), (1, -1), 0.6, BLACK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [addr, Spacer(1, 10)]

    # Item table
    rows = [[_p("DESCRIPTION", ST_LABEL),
             _p("TAX", ST_LABEL), Paragraph("AMOUNT", ParagraphStyle("lh", parent=ST_LABEL, alignment=TA_RIGHT))]]
    for ln in data["lines"]:
        desc = ""
        if ln.get("date"):
            desc += f"{escape(ln['date'])}&nbsp;&nbsp;"
        if ln.get("patient"):
            desc += f"<b>{escape(ln['patient'])}</b> &ndash; "
        desc += escape(ln["description"])
        rows.append([Paragraph(desc, ST_CELL), _p("T" if ln["taxable"] else "", ST_CELL),
                     _p(fmt(ln["amount_cents"]), ST_CELL_R)])
    if len(rows) == 1:
        rows.append([_p("(no items)"), "", ""])
    tbl = Table(rows, colWidths=[W - 0.55 * inch - 1.15 * inch, 0.55 * inch, 1.15 * inch], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BLACK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, BLACK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, BLACK),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.4, BLACK),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [tbl, Spacer(1, 8)]

    # Totals
    t = data["totals"]
    tot_rows = [["Subtotal", fmt(t["subtotal_cents"])],
                ["Tax Rate", f"{t['tax_rate']}%"],
                [f"Tax (on {fmt(t['taxable_subtotal_cents'])} taxable)", fmt(t["tax_cents"])],
                [data["total_label"], fmt(t["total_cents"])]]
    bold_rows = [3]
    if data.get("show_previous_balance"):
        tot_rows += [["Previous Balance", fmt(t["previous_balance_cents"])],
                     ["TOTAL AMOUNT DUE", fmt(t["amount_due_cents"])]]
        bold_rows.append(5)
    tt = Table(tot_rows, colWidths=[2.45 * inch, 1.15 * inch], hAlign="RIGHT")
    ts = [("FONT", (0, 0), (-1, -1), "Helvetica", 9.5), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
          ("BOX", (0, 0), (-1, -1), 0.6, BLACK), ("LINEBELOW", (0, 0), (-1, -1), 0.25, BLACK),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    for r in bold_rows:
        ts += [("FONT", (0, r), (-1, r), "Helvetica-Bold", 10), ("LINEABOVE", (0, r), (-1, r), 0.9, BLACK)]
    tt.setStyle(TableStyle(ts))

    # Aging
    ag = data["aging"]
    ag_tbl = Table([["0-30 Days", "31-60 Days", "61-90 Days", "Over 90 Days", "Total Outstanding"],
                    [fmt(ag["buckets"][0]), fmt(ag["buckets"][1]), fmt(ag["buckets"][2]), fmt(ag["buckets"][3]),
                     fmt(ag["total_cents"])]], colWidths=[W / 5] * 5)
    ag_tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, 1), "Helvetica", 9.5),
        ("FONT", (4, 1), (4, 1), "Helvetica-Bold", 9.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.5, BLACK),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    tail = [tt, Spacer(1, 8), _p("ACCOUNT AGING", ST_LABEL), Spacer(1, 2), ag_tbl]
    if ag.get("unapplied_credit_cents"):
        tail += [Spacer(1, 2), _p(f"Includes unapplied payment credit of {fmt(ag['unapplied_credit_cents'])}.",
                                  ST_SMALL)]
    notes = [x for x in [data.get("notes"), data.get("terms"), data.get("footer")] if x]
    if notes:
        tail += [Spacer(1, 8)] + [_p(n, ST_SMALL) for n in notes]
    story.append(KeepTogether(tail))
    doc.build(story, canvasmaker=_NumberedCanvas)
    return path


# ------------------------------------------------------------- data prep
def invoice_pdf_data(s, inv) -> dict:
    """Gather everything needed to print an invoice (finalized: from snapshots)."""
    from ..services import aging as aging_svc
    from ..services.common import get_settings
    from ..services.invoices import compute_totals, period_label

    st = get_settings(s)
    office = inv.office
    lines = [{"date": l.line_date.strftime("%m/%d") if l.line_date else "", "patient": l.patient_name,
              "description": l.description, "taxable": l.taxable, "amount_cents": l.amount_cents}
             for l in inv.lines]
    if st.get("invoice_show_dates", "0") != "1":
        for l in lines:
            l["date"] = ""
    totals = {"subtotal_cents": inv.subtotal_cents, "taxable_subtotal_cents": inv.taxable_subtotal_cents,
              "tax_cents": inv.tax_cents, "total_cents": inv.total_cents}
    if inv.status == "Draft":
        totals = compute_totals(inv.lines, inv.tax_rate, inv.tax_method)
    rate = parse_rate(inv.tax_rate)
    totals["tax_rate"] = f"{rate.normalize():f}" if rate else "0.00"

    if inv.status == "Draft":
        prev = aging_svc.office_aging(s, office.id, as_of=inv.invoice_date, exclude_invoice_id=inv.id)
        ag = prev.as_dict()
        if inv.kind == "Invoice":
            ag["buckets"][0] += totals["total_cents"]
            ag["total_cents"] += totals["total_cents"]
        prev_bal = prev.total_cents
    else:
        prev_bal = inv.previous_balance_cents
        try:
            ag = json.loads(inv.aging_snapshot)
        except (ValueError, TypeError):
            ag = aging_svc.office_aging(s, office.id, as_of=inv.invoice_date).as_dict()
    totals["previous_balance_cents"] = prev_bal
    totals["amount_due_cents"] = prev_bal + totals["total_cents"]

    is_credit = inv.kind == "Credit Note"
    return {
        "company": {"name": st.get("company_name", "DENTEC DENTAL LAB"), "address1": st.get("company_address1", ""),
                    "address2": st.get("company_address2", ""), "website": st.get("company_website", ""),
                    "phone": st.get("company_phone", ""), "email": st.get("company_email", "")},
        "title": "CREDIT NOTE" if is_credit else "INVOICE",
        "number_label": "Credit Note No." if is_credit else "Invoice No.",
        "total_label": "CREDIT TOTAL" if is_credit else "INVOICE TOTAL",
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.strftime("%m/%d/%Y"),
        "period": period_label(inv.period_year, inv.period_month),
        "related": inv.related_invoice.invoice_number if (is_credit and inv.related_invoice) else "",
        "office_name": office.name,
        "bill_to": inv.bill_to, "ship_to": inv.ship_to,
        "lines": lines, "totals": totals, "aging": ag,
        "show_previous_balance": bool(office.show_previous_balance) and not is_credit,
        "notes": inv.notes if not is_credit else (inv.notes or ""),
        "terms": "" if is_credit else st.get("invoice_terms", ""),
        "footer": st.get("invoice_footer", ""),
        "draft": inv.status == "Draft",
    }


def invoice_filename(inv) -> str:
    safe_office = "".join(ch if ch.isalnum() else "_" for ch in inv.office.name)[:40].strip("_")
    num = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (inv.invoice_number or "DRAFT"))
    prefix = "CreditNote" if inv.kind == "Credit Note" else "Invoice"
    return f"{prefix}_{num}_{safe_office}_{inv.period_year}-{inv.period_month:02d}.pdf"


def export_invoice(s, inv, folder: Path | None = None) -> Path:
    from ..config import get_paths
    folder = folder or (get_paths().invoices / f"{inv.period_year}-{inv.period_month:02d}")
    name = invoice_filename(inv)
    if inv.status == "Draft":
        folder = get_paths().invoices / "_previews"
        name = f"PREVIEW_{inv.id}_{date.today():%Y%m%d}.pdf"
    return render_invoice_pdf(invoice_pdf_data(s, inv), Path(folder) / name)
