"""End-to-end UI test: drives the real screens (offscreen) through a full month.

Dialogs are auto-answered: message boxes are recorded, confirmations answer
Yes (except "print now?"), and modal dialogs return Accepted immediately.
"""
import os
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402

from dentec.db import database  # noqa: E402
from dentec.services import aging, auth, cases as cs, invoices as iv, pricing  # noqa: E402
from dentec.services.common import CurrentUser, set_current_user  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data" / "labslips"


@pytest.fixture()
def ui(home, monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    msgs = {"warning": [], "info": [], "question": []}

    def warning(parent, title, text, *a, **k):
        msgs["warning"].append(text)
        return QMessageBox.Ok

    def information(parent, title, text, *a, **k):
        msgs["info"].append(text)
        return QMessageBox.Ok

    def question(parent, title, text, *a, **k):
        msgs["question"].append(text)
        return QMessageBox.No if "Print it now" in text else QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(information))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(warning))
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    opened = []
    import dentec.ui.widgets as W
    monkeypatch.setattr(W, "open_file", lambda p: opened.append(Path(p)))
    for mod in ["dentec.ui.invoices_page", "dentec.ui.case_entry", "dentec.ui.reports_page",
                "dentec.ui.settings_page", "dentec.ui.deliveries_page"]:
        monkeypatch.setattr(mod + ".open_file", lambda p: opened.append(Path(p)), raising=False)
    with database.session_scope() as s:
        auth.create_user(s, "admin", "admin-pass", "admin", _bootstrap=True)
        auth.create_user(s, "staff", "staff-pass", "staff", _bootstrap=True)
    set_current_user(CurrentUser(id=1, username="admin", role="admin"))
    from dentec.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()
    yield w, app, msgs, opened, tmp_path
    w.backup_timer.stop()
    w.idle.timer.stop()
    w.close()


def test_full_month_workflow(ui, monkeypatch):
    w, app, msgs, opened, tmp = ui

    # 1. Add dental offices -----------------------------------------------------
    w.goto("offices")
    op = w.pages["offices"]
    op.new_office()
    op.f["name"].setText("Valley Smiles Dentistry")
    op.f["dentist_name"].setText("Dr. Maria Example")
    op.f["phone"].setText("(559) 555-0142")
    op.f["billing_address"].setPlainText("1234 W Shaw Ave\nFresno, CA 93711")
    op.save()
    op.new_office()
    op.f["name"].setText("Sunrise Family Dental")
    op.f["dentist_name"].setText("Dr. Alan Placeholder")
    op.save()
    assert not msgs["warning"], msgs["warning"]
    with database.session_scope() as s:
        from dentec.services.offices import list_offices
        offs = {o.name: o.id for o in list_offices(s)}
        bb = pricing.find_procedure(s, "Bite Block").id
    valley, sunrise = offs["Valley Smiles Dentistry"], offs["Sunrise Family Dental"]

    # 2./3. Individual price + copy price list -------------------------------
    w.goto("prices")
    pp = w.pages["prices"]
    pp.select_office(sunrise)
    app.processEvents()
    for r in range(pp.table.rowCount()):
        if pp.table.item(r, 0).text() == "Bite Block":
            pp.table.item(r, 2).setText("30.00")
    pp.reason.setText("Negotiated")
    pp.save_changes()
    with database.session_scope() as s:
        assert pricing.get_price(s, sunrise, bb).cents == 3000
        assert pricing.get_price(s, valley, bb).cents == 3500
        assert pricing.price_history(s, sunrise, bb)[0].reason == "Negotiated"

    # 4. Monthly batch entry through the form ---------------------------------
    w.goto("case_entry")
    ce = w.pages["case_entry"]
    ce.open_batch(valley, 2026, 10)
    ce.new_record()
    ce.patient.setText("Jordan Sample")
    ce.date.set_value(date(2026, 10, 6))
    row0 = ce.grid
    row0.cellWidget(0, 0).setCurrentText("Upper")
    row0.cellWidget(0, 1).setCurrentIndex(row0.cellWidget(0, 1).findData(bb))
    assert row0.cellWidget(0, 5).text() == "35.00"
    ce.grid.add_line()
    row0.cellWidget(1, 0).setCurrentText("Lower")
    row0.cellWidget(1, 1).setCurrentIndex(row0.cellWidget(1, 1).findData(bb))
    ce.save("Approved", next_record=True)
    # free-text line, saved as draft first then approved from the list
    ce.patient.setText("Casey Fictional")
    ce.grid.cellWidget(0, 0).setCurrentText("Lower")
    ce.grid.cellWidget(0, 2).setText("2 Clasp")
    ce.grid.cellWidget(0, 5).setText("45.00")
    ce.save("Draft")
    assert not msgs["warning"], msgs["warning"]
    with database.session_scope() as s:
        cases = cs.list_cases(s, valley, 2026, 10)
        assert [(c.patient_name, c.status, c.total_cents) for c in cases] == [
            ("Jordan Sample", "Approved", 7000), ("Casey Fictional", "Draft", 4500)]

    ce.table.selectRow(1)
    ce.set_status("Approved")
    # "Both" arch defaults quantity to 2
    ce.new_record()
    ce.grid.cellWidget(0, 0).setCurrentText("Both")
    assert ce.grid.cellWidget(0, 4).value() == 2

    # staff price override needs admin authorization
    set_current_user(CurrentUser(id=2, username="staff", role="staff"))
    import dentec.ui.case_entry as CE
    asked = []
    monkeypatch.setattr(CE, "authorize_admin", lambda parent, reason: asked.append(reason) or "admin")
    ce.open_batch(sunrise, 2026, 10)
    ce.new_record()
    ce.patient.setText("Riley Testcase")
    ce.grid.cellWidget(0, 1).setCurrentIndex(ce.grid.cellWidget(0, 1).findData(bb))
    assert ce.grid.cellWidget(0, 5).text() == "30.00"  # Sunrise's own price
    ce.grid.cellWidget(0, 5).setText("25.00")
    ce.save("Approved")
    assert asked, "admin authorization was not requested"
    set_current_user(CurrentUser(id=1, username="admin", role="admin"))
    with database.session_scope() as s:
        c = cs.list_cases(s, sunrise, 2026, 10)[0]
        assert c.items[0].unit_price_cents == 2500 and c.items[0].price_override_by == "admin"

    # 5./6./7. Import lab slips, review OCR, correct, approve ----------------
    from dentec.ocr.engine import find_tesseract
    if find_tesseract():
        w.goto("scanner")
        sp = w.pages["scanner"]
        files = [str(SAMPLES / "sample_labslip_1.jpg"), str(SAMPLES / "sample_labslip_2.jpg")]
        sp.import_paths(files, wait=True)
        for _ in range(20):
            app.processEvents()
        assert sp.queue.count() == 2
        sp.queue.setCurrentRow(0)
        app.processEvents()
        assert sp.fields["patient_name"].text() == "Jordan Sample"
        assert sp.office.office_id() == valley  # matched from the slip
        assert sp.checks["bite_block"].isChecked()
        sp.fields["patient_name"].setText("Jordan Sample Jr")  # human correction
        sp.month.set_value(2026, 10)
        sp.verified.setChecked(True)
        sp.approve()
        assert not msgs["warning"], msgs["warning"]
        with database.session_scope() as s:
            c = [c for c in cs.list_cases(s, valley, 2026, 10) if c.source == "OCR"][0]
            assert c.patient_name == "Jordan Sample Jr" and c.total_cents == 7000 and c.attachments
        assert sp.queue.count() == 1
        sp.queue.setCurrentRow(0)
        sp.reject()
        assert sp.queue.count() == 0

    # 8./9./10./11. Generate invoices with manual numbers -------------------
    w.goto("invoices")
    ip = w.pages["invoices"]
    ip.month.set_value(2026, 10)
    ip.reload_run()
    assert ip.run.rowCount() == 2
    ip.run.selectAll()
    ip.create_drafts()
    with database.session_scope() as s:
        drafts = {i.office_id: i.id for i in iv.list_invoices(s, status="Draft")}
    from dentec.ui.invoices_page import InvoiceEditor
    ed = InvoiceEditor(drafts[valley], w)
    assert ed.number.text() == ""  # never pre-filled
    ed.finalize()
    assert any("Enter the invoice number" in m for m in msgs["warning"])
    msgs["warning"].clear()
    ed.number.setText("1049")
    ed.tax_rate.setText("7.975")
    ed.finalize()
    assert not msgs["warning"], msgs["warning"]
    ed2 = InvoiceEditor(drafts[sunrise], w)
    ed2.number.setText("1049")  # duplicate of another office -> warning, user confirms
    ed2.finalize()
    assert any("already used" in q for q in msgs["question"])
    with database.session_scope() as s:
        a = iv.get_invoice(s, drafts[valley])
        b = iv.get_invoice(s, drafts[sunrise])
        assert a.status == b.status == "Finalized"
        assert a.invoice_number == "1049" and a.tax_rate == "7.975"
        assert a.total_cents == 7000 + 4500 + 7000  # tax only on taxable lines (none here)
        assert iv.billable_cases(s, valley, 2026, 10) == []  # nothing can be billed twice
    ip.reload_run()
    assert ip.run.rowCount() == 0
    # 14. reprint
    ip.f_number.setText("1049")
    ip.reload_list()
    ip.list.selectRow(0)
    ip.reprint()
    assert opened and opened[-1].suffix == ".pdf" and opened[-1].exists()

    # 12. partial payment ------------------------------------------------------
    w.goto("payments")
    pay = w.pages["payments"]
    pay.select_office(valley, drafts[valley])
    pay.p_amount.setText("100.00")
    pay.p_check.setText("5521")
    pay.record()
    with database.session_scope() as s:
        inv = iv.get_invoice(s, drafts[valley])
        assert aging.invoice_status(s, inv) == "Partially Paid"
        assert aging.invoice_balance(s, inv) == 18500 - 10000

    # 13./15. reports + export ------------------------------------------------
    w.goto("reports")
    rp = w.pages["reports"]
    for i in range(rp.kind.count()):
        rp.kind.setCurrentIndex(i)
        rp.run()
        assert rp.table.rowCount() >= 1
    rp.kind.setCurrentText("Outstanding Balances")
    rp.run()
    out_csv = tmp / "aging.csv"
    out_pdf = tmp / "aging.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_csv), "")))
    rp.export("csv")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out_pdf), "")))
    rp.export("pdf")
    assert out_csv.exists() and out_pdf.exists()

    # deliveries ----------------------------------------------------------------
    w.goto("deliveries")
    dp = w.pages["deliveries"]
    dp.refresh()
    dp.create()
    assert dp.runs.rowCount() == 1

    # 16. backup and restore ------------------------------------------------------
    w.goto("settings")
    st = w.pages["settings"]
    st.backup_now()
    backups = list((w.pages["settings"]._settings().get("backup_dir") and Path(st._settings()["backup_dir"])
                    or database_home() / "backups").glob("DentecBackup_*.zip"))
    assert backups
    with database.session_scope() as s:
        from dentec.services.offices import update_office
        update_office(s, valley, {"name": "Renamed After Backup"})
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(backups[0]), "")))
    st.restore()
    with database.session_scope() as s:
        from dentec.db.models import Office
        assert s.get(Office, valley).name == "Valley Smiles Dentistry"

    # every page refreshes cleanly after all of this
    for key in w.keys:
        w.goto(key)
        app.processEvents()
    assert not [m for m in msgs["warning"]], msgs["warning"]


def database_home():
    from dentec.config import get_paths
    return get_paths().home
