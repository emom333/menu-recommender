"""Optional Tuesday/Thursday delivery runs.  Never blocks invoicing."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db.models import Case, Delivery, DeliveryItem
from .common import BusinessError, audit, current_user

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def next_delivery_dates(start: date | None = None, days: str = "Tuesday,Thursday", count: int = 4) -> list[date]:
    wanted = {WEEKDAYS.index(d.strip()) for d in days.split(",") if d.strip() in WEEKDAYS} or {1, 3}
    d = start or date.today()
    out = []
    while len(out) < count:
        if d.weekday() in wanted:
            out.append(d)
        d += timedelta(days=1)
    return out


def list_deliveries(s: Session, start: date | None = None, end: date | None = None, search: str = "",
                    status: str | None = None) -> list[Delivery]:
    q = select(Delivery).options(selectinload(Delivery.items).selectinload(DeliveryItem.office),
                                 selectinload(Delivery.items).selectinload(DeliveryItem.case))
    if start:
        q = q.where(Delivery.delivery_date >= start)
    if end:
        q = q.where(Delivery.delivery_date <= end)
    if status:
        q = q.where(Delivery.status == status)
    out = list(s.scalars(q.order_by(Delivery.delivery_date.desc(), Delivery.id.desc())))
    if search:
        t = search.lower()
        out = [d for d in out if t in (d.notes or "").lower() or t in (d.driver or "").lower() or any(
            t in (i.description or "").lower() or t in i.office.name.lower() for i in d.items)]
    return out


def create_delivery(s: Session, delivery_date: date, case_ids: list[int], driver: str = "", notes: str = "") -> Delivery:
    if delivery_date is None:
        raise BusinessError("Choose a delivery date.")
    d = Delivery(delivery_date=delivery_date, driver=driver, notes=notes, created_by=current_user().username)
    s.add(d)
    s.flush()
    for cid in case_ids:
        add_case(s, d.id, cid)
    audit(s, "create_delivery", "delivery", d.id, f"{delivery_date} {len(case_ids)} cases")
    return d


def add_case(s: Session, delivery_id: int, case_id: int) -> DeliveryItem:
    d = s.get(Delivery, delivery_id)
    c = s.get(Case, case_id)
    if d is None or c is None:
        raise BusinessError("Delivery or patient record not found.")
    if any(i.case_id == case_id for i in d.items):
        raise BusinessError(f"{c.patient_name} is already on this delivery.")
    from .cases import case_label
    it = DeliveryItem(case_id=c.id, office_id=c.office_id, description=f"{c.patient_name} - {case_label(c)}"[:300])
    d.items.append(it)
    s.flush()
    return it


def remove_item(s: Session, item_id: int) -> None:
    it = s.get(DeliveryItem, item_id)
    if it is not None:
        s.delete(it)


def set_item_delivered(s: Session, item_id: int, delivered: bool) -> None:
    it = s.get(DeliveryItem, item_id)
    if it is None:
        raise BusinessError("Delivery item not found.")
    it.delivered = delivered


def complete_delivery(s: Session, delivery_id: int, completed_on: date | None = None) -> Delivery:
    d = s.get(Delivery, delivery_id)
    if d is None:
        raise BusinessError("Delivery not found.")
    for it in d.items:
        it.delivered = True
    d.status = "Completed"
    when = completed_on or date.today()
    d.completed_at = datetime.combine(when, datetime.now().time()).replace(microsecond=0)
    audit(s, "complete_delivery", "delivery", d.id, f"{d.delivery_date} completed {when}")
    return d


def reopen_delivery(s: Session, delivery_id: int) -> None:
    d = s.get(Delivery, delivery_id)
    if d is not None:
        d.status = "Planned"
        d.completed_at = None


def delete_delivery(s: Session, delivery_id: int) -> None:
    d = s.get(Delivery, delivery_id)
    if d is not None:
        audit(s, "delete_delivery", "delivery", d.id, str(d.delivery_date))
        s.delete(d)


def cases_due(s: Session, start: date, end: date) -> list[Case]:
    """Cases due in a window that are not already on a delivery."""
    on_delivery = select(DeliveryItem.case_id).where(DeliveryItem.case_id.is_not(None))
    q = (select(Case).options(selectinload(Case.office), selectinload(Case.items))
         .where(Case.due_date >= start, Case.due_date <= end, Case.id.not_in(on_delivery))
         .order_by(Case.due_date, Case.office_id))
    return list(s.scalars(q))


def grouped_by_office(d: Delivery) -> dict[str, list[DeliveryItem]]:
    out: dict[str, list[DeliveryItem]] = {}
    for it in sorted(d.items, key=lambda i: (i.office.name.lower(), i.description)):
        out.setdefault(it.office.name, []).append(it)
    return out


def export_delivery_pdf(s: Session, d: Delivery, path) -> None:
    from pathlib import Path
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=14, leading=18)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=8)
    sm = ParagraphStyle("sm", fontName="Helvetica", fontSize=9.5, leading=12)
    doc = SimpleDocTemplate(str(Path(path)), pagesize=LETTER, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch, title="Delivery List")
    story = [Paragraph("DENTEC DENTAL LAB - DELIVERY LIST", h1),
             Paragraph(f"{WEEKDAYS[d.delivery_date.weekday()]} {d.delivery_date:%m/%d/%Y}"
                       + (f" &nbsp; Driver: {escape(d.driver)}" if d.driver else "")
                       + f" &nbsp; Status: {d.status}", sm)]
    if d.notes:
        story.append(Paragraph(escape(d.notes), sm))
    for office_name, items in grouped_by_office(d).items():
        o = items[0].office
        addr = (o.shipping_address or o.billing_address or "").replace("\n", ", ")
        story.append(Paragraph(escape(office_name), h2))
        if addr or o.phone:
            story.append(Paragraph(escape(" | ".join(x for x in [addr, o.phone] if x)), sm))
        rows = [["", "Case", "Received By"]] + [["[X]" if i.delivered else "[  ]", Paragraph(escape(i.description), sm), ""]
                                                for i in items]
        t = Table(rows, colWidths=[0.45 * inch, 4.9 * inch, 1.9 * inch])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, (0, 0, 0)), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [Spacer(1, 3), t]
    if not d.items:
        story.append(Paragraph("(no cases on this delivery)", sm))
    doc.build(story)
