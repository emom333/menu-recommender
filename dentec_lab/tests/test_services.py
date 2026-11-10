"""Stage 2: OCR, payments, aging, credit notes, reports, deliveries, backup, auth."""
from datetime import date, timedelta
import zipfile

import pytest

from dentec.db import database
from dentec.services import (aging, auth, backup, cases as cs, deliveries, invoices as iv, ocr_import, offices,
                             payments, pricing, reports)
from dentec.services.cases import CaseInput, ItemInput
from dentec.services.common import BusinessError, CurrentUser, PermissionDenied, set_current_user


def P(s, n):
    return pricing.find_procedure(s, n).id


def office(s, name="Valley Smiles Dentistry", **kw):
    return offices.create_office(s, {"name": name, "dentist_name": "Dr. Maria Example", "phone": "(559) 555-0142",
                                     **kw})


def bill(s, o, number, inv_date, items, month=None, year=None, patient="Pat"):
    month = month or inv_date.month
    year = year or inv_date.year
    cs.save_case(s, CaseInput(o.id, year, month, patient, status="Approved",
                              items=[ItemInput(**i) for i in items]))
    inv = iv.create_draft(s, o.id, year, month, inv_date)
    return iv.finalize(s, inv.id, number, confirm_duplicates=True)


# ------------------------------------------------------------------ payments
def test_partial_and_multiple_payments(s):
    o = office(s)
    inv = bill(s, o, "1049", date(2026, 10, 31), [{"procedure_id": P(s, "Metal Partial")}])  # 285
    assert aging.invoice_status(s, inv) == "Unpaid"
    payments.record_payment(s, o.id, date(2026, 11, 5), "100.00", "Check", "1001", invoice_id=inv.id)
    assert aging.invoice_balance(s, inv) == 18500
    assert aging.invoice_status(s, inv) == "Partially Paid"
    payments.record_payment(s, o.id, date(2026, 11, 9), "185", "Credit Card", invoice_id=inv.id)
    assert aging.invoice_status(s, inv) == "Paid"
    assert len(payments.payments_for_invoice(s, inv.id)) == 2
    with pytest.raises(BusinessError):
        payments.record_payment(s, o.id, date(2026, 11, 9), "0", "Cash")


def test_overpayment_becomes_credit_and_auto_apply_oldest(s):
    o = office(s)
    i1 = bill(s, o, "1", date(2026, 8, 31), [{"procedure_id": P(s, "Bite Block")}], patient="A")  # 35
    i2 = bill(s, o, "2", date(2026, 9, 30), [{"procedure_id": P(s, "Repair")}], patient="B")  # 105
    p = payments.record_payment(s, o.id, date(2026, 10, 1), "200", "Check", "77")
    assert aging.invoice_balance(s, i1) == 0 and aging.invoice_balance(s, i2) == 0
    assert p.unapplied_cents == 6000
    a = aging.office_aging(s, o.id, date(2026, 10, 1))
    assert a.total_cents == -6000  # credit on account
    i3 = bill(s, o, "3", date(2026, 10, 31), [{"procedure_id": P(s, "Repair")}], patient="C")
    payments.apply_credit(s, p.id)
    assert aging.invoice_balance(s, i3) == 4500


def test_aging_buckets_and_previous_balance(s):
    o = office(s)
    bill(s, o, "100", date(2026, 6, 30), [{"procedure_id": P(s, "Bite Block")}], patient="A")   # 35, >90
    bill(s, o, "101", date(2026, 8, 31), [{"procedure_id": P(s, "Repair")}], patient="B")       # 105, 61-90
    bill(s, o, "102", date(2026, 9, 30), [{"procedure_id": P(s, "Teeth Try-In")}], patient="C")  # 99, 31-60
    a = aging.office_aging(s, o.id, date(2026, 10, 31))
    assert a.buckets == [0, 9900, 10500, 3500]
    cur = bill(s, o, "103", date(2026, 10, 31), [{"procedure_id": P(s, "Metal Partial")}], patient="D")
    # previous balance snapshot excludes the current invoice -> no double counting
    assert cur.previous_balance_cents == 9900 + 10500 + 3500
    import json
    snap = json.loads(cur.aging_snapshot)
    assert snap["buckets"] == [28500, 9900, 10500, 3500]
    assert snap["total_cents"] == cur.previous_balance_cents + cur.total_cents
    # a later payment doesn't change the reprinted snapshot
    payments.record_payment(s, o.id, date(2026, 11, 2), "35", "Cash")
    s.refresh(cur)
    assert json.loads(cur.aging_snapshot) == snap


def test_credit_note_and_void(s):
    o = office(s)
    inv = bill(s, o, "500", date(2026, 10, 31), [{"procedure_id": P(s, "Metal Partial")}])
    with pytest.raises(BusinessError):
        iv.create_credit_note(s, inv.id, "CN-500", "400", "too much")
    cn = iv.create_credit_note(s, inv.id, "CN-500", "50", "Remake discount")
    assert cn.kind == "Credit Note" and cn.total_cents == -5000 and cn.status == "Finalized"
    assert aging.invoice_balance(s, inv) == 23500
    with pytest.raises(BusinessError):
        iv.void_invoice(s, inv.id, "mistake")  # has a credit applied
    set_current_user(CurrentUser(id=5, username="staff", role="staff"))
    with pytest.raises(PermissionDenied):
        iv.create_credit_note(s, inv.id, "CN-2", "5", "x")
    set_current_user(CurrentUser(id=1, username="admin", role="admin"))
    inv2 = bill(s, o, "501", date(2026, 11, 30), [{"procedure_id": P(s, "Repair")}], month=11, patient="Z")
    iv.void_invoice(s, inv2.id, "entered wrong office")
    assert inv2.status == "Void"
    # released record can be billed again under a new number
    again = iv.create_draft(s, o.id, 2026, 11)
    iv.finalize(s, again.id, "502")
    assert again.total_cents == 10500


# ---------------------------------------------------------------- reports
def test_reports_and_exports(s, tmp_path):
    o1 = office(s, "Office One")
    o2 = office(s, "Office Two", phone="")
    bill(s, o1, "1", date(2026, 10, 31), [{"procedure_id": P(s, "Bite Block"), "quantity": 2}])
    bill(s, o2, "1", date(2026, 10, 31), [{"procedure_id": P(s, "Repair")}])
    payments.record_payment(s, o1.id, date(2026, 11, 3), "20", "Cash")
    cs.save_case(s, CaseInput(o1.id, 2026, 10, "Draft guy", items=[ItemInput(P(s, "Bite Block"))]))
    r = reports.monthly_revenue(s, 2026)
    assert r.rows[9][1] == 2 and r.rows[9][5] == "$175.00"
    r = reports.revenue_by_office(s, (2026, 1), (2026, 12))
    assert [x[0] for x in r.rows] == ["Office Two", "Office One"]
    r = reports.revenue_by_procedure(s, (2026, 10), (2026, 10))
    assert ["Repair", 1, "$105.00", "$105.00"] in r.rows and ["Bite Block", 2, "$70.00", "$35.00"] in r.rows
    r = reports.outstanding_balances(s, date(2026, 11, 10))
    assert r.totals[-1] == "$155.00"
    r = reports.payment_history(s, date(2026, 11, 1), date(2026, 11, 30))
    assert len(r.rows) == 1
    r = reports.monthly_case_count(s, 2026)
    assert r.rows[9][1:5] == [3, 1, 0, 2]
    r = reports.monthly_invoice_summary(s, 2026, 10)
    assert len(r.rows) == 2
    for name, fn in reports.REPORTS.items():
        assert hasattr(reports, fn)
    c = reports.export_csv(r, tmp_path / "x.csv")
    assert "Monthly Invoice Summary" in c.read_text(encoding="utf-8-sig")
    pdf = reports.export_pdf(r, tmp_path / "x.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"


# -------------------------------------------------------------- deliveries
def test_deliveries(s, tmp_path):
    o = office(s)
    c1 = cs.save_case(s, CaseInput(o.id, 2026, 11, "P1", due_date=date(2026, 11, 10), items=[ItemInput(P(s, "Bite Block"))]))
    cs.save_case(s, CaseInput(o.id, 2026, 11, "P2", due_date=date(2026, 11, 12), items=[ItemInput(P(s, "Repair"))]))
    days = deliveries.next_delivery_dates(date(2026, 11, 9))
    assert [d.weekday() for d in days] == [1, 3, 1, 3]
    due = deliveries.cases_due(s, date(2026, 11, 9), date(2026, 11, 12))
    assert len(due) == 2
    d = deliveries.create_delivery(s, days[0], [c.id for c in due], driver="Sam")
    with pytest.raises(BusinessError):
        deliveries.add_case(s, d.id, c1.id)
    assert deliveries.cases_due(s, date(2026, 11, 9), date(2026, 11, 12)) == []
    deliveries.export_delivery_pdf(s, d, tmp_path / "d.pdf")
    deliveries.complete_delivery(s, d.id, date(2026, 11, 10))
    assert d.status == "Completed" and all(i.delivered for i in d.items)
    assert deliveries.list_deliveries(s, search="P2")[0].id == d.id
    # delivery records never block invoicing
    cs.set_status(s, [c1.id], "Approved")
    assert iv.create_draft(s, o.id, 2026, 11).total_cents == 3500


# --------------------------------------------------------------------- auth
def test_auth_and_roles(s):
    auth.create_user(s, "admin", "secret1", "admin", _bootstrap=True)
    set_current_user(CurrentUser(id=None, username="admin", role="admin"))
    u = auth.create_user(s, "Jane", "pass1234")
    assert u.username == "jane"
    assert auth.authenticate(s, "jane", "pass1234") is not None
    assert auth.authenticate(s, "jane", "wrong") is None
    with pytest.raises(BusinessError):
        auth.create_user(s, "short", "123")
    assert auth.verify_admin_credentials(s, "admin", "secret1")
    assert not auth.verify_admin_credentials(s, "jane", "pass1234")
    admin = auth.authenticate(s, "admin", "secret1")
    with pytest.raises(BusinessError):
        auth.set_user_active(s, admin.id, False)  # last admin
    set_current_user(auth.to_current(u))
    with pytest.raises(PermissionDenied):
        auth.create_user(s, "bob", "pass1234")
    from dentec.db.models import AuditLog
    actions = [a.action for a in s.query(AuditLog).all()]
    assert "login" in actions and "login_failed" in actions


# ------------------------------------------------------------------- backup
def test_backup_restore_export(home, tmp_path):
    with database.session_scope() as s:
        o = office(s, "Before Backup")
        oid = o.id
    doc = home.documents / "2026" / "slip.txt"
    doc.parent.mkdir(parents=True)
    doc.write_text("original slip")
    b = backup.create_backup(label="manual")
    m = backup.verify_backup(b)
    assert m["documents"] == 1
    with database.session_scope() as s:
        offices.update_office(s, oid, {"name": "After Backup"})
    doc.write_text("changed")
    database.close_db()
    safety = backup.restore_backup(b)
    assert safety.exists()
    database.init_db(home.db_file)
    with database.session_scope() as s:
        from dentec.db.models import Office
        assert s.get(Office, oid).name == "Before Backup"
    assert doc.read_text() == "original slip"
    exp = backup.export_database(tmp_path / "export.zip")
    with zipfile.ZipFile(exp) as z:
        assert "csv/offices.csv" in z.namelist() and "dentec.db" in z.namelist()
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        backup.verify_backup(bad)
    assert backup.auto_backup_due("") and not backup.auto_backup_due(
        (__import__("datetime").datetime.now() - timedelta(hours=2)).isoformat())


# ---------------------------------------------------------------------- OCR
@pytest.fixture(scope="module")
def slips(tmp_path_factory):
    from dentec.ocr.sample_form import SAMPLE_SLIPS, draw_form, render_scan
    d = tmp_path_factory.mktemp("slips")
    files = [render_scan(v, d / f"slip{i}.jpg", seed=i, skew_deg=[0.6, -0.8, 0.3, 1.0][i])
             for i, v in enumerate(SAMPLE_SLIPS)]
    pdf = d / "two_page.pdf"
    import pymupdf
    out = pymupdf.open()
    for v in SAMPLE_SLIPS[:2]:
        out.insert_pdf(pymupdf.open(stream=draw_form(v), filetype="pdf"))
    out.save(pdf)
    png = d / "slip3.png"
    import cv2
    cv2.imwrite(str(png), cv2.imread(str(files[3])))
    return files, pdf, png


needs_tesseract = pytest.mark.skipif(not __import__("dentec.ocr.engine", fromlist=["x"]).find_tesseract(),
                                     reason="tesseract not installed")


@needs_tesseract
def test_ocr_reads_sample_slips(slips):
    from dentec.ocr.engine import LabSlipOCR, suggest_billing
    from dentec.ocr.sample_form import SAMPLE_SLIPS
    files, _, _ = slips
    eng = LabSlipOCR()
    for f, exp in zip(files, SAMPLE_SLIPS):
        [(_, r)] = eng.process_file(f)
        for k in ["patient_name", "date", "due_date", "phone", "office", "doctor"]:
            assert r.fields[k].value == exp[k], (f.name, k, r.fields[k])
        assert sorted(k for k, c in r.checkboxes.items() if c.checked) == sorted(exp["checked"]), f.name
        assert r.signature_present
    [(_, r)] = eng.process_file(files[2])
    assert r.teeth == [3, 14] and r.teeth_uncertain  # chart always needs human verification
    assert suggest_billing(r) == [{"procedure": "Full Zirconia Crown", "arch": "Upper",
                                   "tooth_numbers": "3, 14", "quantity": 2}]
    [(_, r)] = eng.process_file(files[3])
    assert suggest_billing(r) == [{"procedure": "Flexible Partial", "arch": "Lower", "tooth_numbers": "",
                                   "quantity": 1}]


@needs_tesseract
def test_ocr_import_review_approve(s, slips):
    files, pdf, png = slips
    o = office(s)
    office(s, "Sunrise Family Dental", dentist_name="Dr. Alan Placeholder", phone="(559) 555-0177")
    docs = ocr_import.import_file(s, pdf)  # multi-page PDF
    docs += ocr_import.import_file(s, files[0])
    docs += ocr_import.import_file(s, png)
    assert len(docs) == 4 and all(d.status == "Pending" for d in docs)
    from dentec.ocr.engine import match_office
    res = ocr_import.result_of(docs[1])
    m, score = match_office(res, offices.list_offices(s))
    assert m.name == "Sunrise Family Dental" and score > 0.9
    # original image preserved
    assert cs.document_path(docs[0].rel_path).exists() and cs.document_path(docs[0].source_rel_path).exists()
    # human correction before saving; uncertain results need explicit confirmation
    from dentec.ocr.engine import SlipResult
    d = docs[2]
    r = ocr_import.result_of(d)
    r.fields["patient_name"].uncertain = True
    r.fields["patient_name"].value = "Jordan Sampel"
    d.extracted_json = r.to_json()
    data = CaseInput(o.id, 2026, 10, "Jordan Sample", status="Approved",
                     items=[ItemInput(P(s, "Bite Block"), arch="Upper"), ItemInput(P(s, "Bite Block"), arch="Lower")])
    with pytest.raises(BusinessError):
        ocr_import.approve(s, d.id, data)
    cid = ocr_import.approve(s, d.id, data, technical=ocr_import.technical_summary(r), confirmed_uncertain=True)
    case = cs.get_case(s, cid)
    assert case.source == "OCR" and case.patient_name == "Jordan Sample" and len(case.attachments) == 1
    assert "bite_block" in case.technical_details
    with pytest.raises(BusinessError):
        ocr_import.approve(s, d.id, data, confirmed_uncertain=True)
    ocr_import.reject(s, docs[3].id, "duplicate")
    assert len(ocr_import.list_queue(s)) == 2
    with pytest.raises(BusinessError):
        ocr_import.import_file(s, files[0].with_suffix(".docx"))
    _ = SlipResult
