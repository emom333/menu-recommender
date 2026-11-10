"""Stage 1: database, offices, pricing, monthly batch entry, invoices."""
from datetime import date

import pytest

from dentec.db.models import Procedure
from dentec.services import cases as cs, invoices as inv_svc, offices, pricing
from dentec.services.cases import CaseInput, ItemInput, PriceOverrideRequired
from dentec.services.common import BusinessError, CurrentUser, set_current_user


def P(s, name):
    return pricing.find_procedure(s, name)


def make_office(s, name="Sunrise Family Dental", copy="base", **kw):
    data = {"name": name, "dentist_name": "Dr. Test", "billing_address": "1 Main St\nFresno, CA 93720", **kw}
    return offices.create_office(s, data, copy_prices_from=copy)


def add_case(s, office, patient, items, month=10, year=2026, status="Approved", **kw):
    return cs.save_case(s, CaseInput(office_id=office.id, billing_year=year, billing_month=month,
                                     patient_name=patient, status=status,
                                     items=[ItemInput(**i) for i in items], **kw))


def test_seed_catalog_and_reference_prices(s):
    names = {p.name: p for p in pricing.list_procedures(s, True)}
    assert len(names) == 32
    for n in ["Custom Tray", "Bite Block", "Teeth Try-In", "Acrylic Partial Finish", "Full Denture Finish",
              "PFM Crown", "Full Zirconia Crown", "Flexible Partial", "Repair", "Reline", "E.Max Veneer",
              "Implant Custom Abutment + Zirconia Crown", "Bleaching Guard"]:
        assert n in names
    assert names["Teeth Try-In"].base_price_cents == 9900
    assert names["Full Denture Finish"].base_price_cents == 9900
    assert names["Teeth Try-In"].id != names["Full Denture Finish"].id
    assert names["Implant Custom Abutment + Zirconia Crown"].base_price_cents == 38500
    assert names["Reline"].base_price_cents is None


def test_office_crud_and_independent_prices(s):
    a = make_office(s, "Office A")
    b = make_office(s, "Office B")
    with pytest.raises(BusinessError):
        make_office(s, "office a")
    bb = P(s, "Bite Block")
    pricing.set_office_price(s, a.id, bb.id, "30.00")
    assert pricing.get_price(s, a.id, bb.id).cents == 3000
    assert pricing.get_price(s, b.id, bb.id).cents == 3500  # B untouched
    assert P(s, "Bite Block").base_price_cents == 3500  # base untouched
    offices.update_office(s, a.id, {"phone": "559-555-0100", "status": "On Hold"})
    assert s.get(type(a), a.id).status == "On Hold"


def test_copy_price_list_and_empty_office(s):
    a = make_office(s, "Office A")
    pricing.set_office_price(s, a.id, P(s, "PFM Crown").id, 79)
    c = make_office(s, "Office C", copy=a.id)
    assert pricing.get_price(s, c.id, P(s, "PFM Crown").id).cents == 7900
    e = make_office(s, "Office E", copy=None)
    assert pricing.office_price_map(s, e.id) == {}
    # falls back to the reference price, flagged as 'base'
    assert pricing.get_price(s, e.id, P(s, "PFM Crown").id).source == "base"


def test_bulk_adjust_and_history(s):
    a = make_office(s, "Office A")
    n = pricing.bulk_adjust(s, a.id, percent=10, round_to="1.00")
    assert n > 0
    assert pricing.get_price(s, a.id, P(s, "Bite Block").id).cents == 3900  # 38.50 -> 39
    assert pricing.get_price(s, a.id, P(s, "Full Zirconia Crown").id).cents == 9800  # 97.90 -> 98
    hist = pricing.price_history(s, a.id, P(s, "Bite Block").id)
    assert hist[0].old_price_cents == 3500 and hist[0].new_price_cents == 3900
    pricing.bulk_adjust(s, a.id, amount="-4", procedure_ids=[P(s, "Bite Block").id])
    assert pricing.get_price(s, a.id, P(s, "Bite Block").id).cents == 3500


def test_price_import_export(s, tmp_path):
    a = make_office(s, "Office A")
    f = tmp_path / "prices.csv"
    f.write_text("Procedure,Price,Category\nBite Block,31.50,\nWax Rim Special,22,Removable\nPFM Crown,,\n")
    r = pricing.import_price_list(s, a.id, f)
    assert r["updated"] == 2 and r["created"] == 1 and len(r["skipped"]) == 1
    assert pricing.get_price(s, a.id, P(s, "Wax Rim Special").id).cents == 2200
    out = pricing.export_price_list(s, a.id, tmp_path / "out.xlsx")
    b = make_office(s, "Office B", copy=None)
    pricing.import_price_list(s, b.id, out)
    assert pricing.get_price(s, b.id, P(s, "Bite Block").id).cents == 3150


def test_price_change_does_not_touch_existing_cases(s):
    a = make_office(s)
    c = add_case(s, a, "Patient A", [{"procedure_id": P(s, "Bite Block").id, "arch": "Upper"}])
    pricing.set_office_price(s, a.id, P(s, "Bite Block").id, 50)
    s.refresh(c)
    assert c.items[0].unit_price_cents == 3500
    c2 = add_case(s, a, "Patient B", [{"procedure_id": P(s, "Bite Block").id}])
    assert c2.items[0].unit_price_cents == 5000


def test_batch_entry_multiple_items_free_text(s):
    a = make_office(s)
    c = add_case(s, a, "Patient C", [
        {"procedure_id": P(s, "Full Denture Finish").id, "arch": "Upper"},
        {"procedure_name": "", "description": "Lower 2 Clasp", "arch": "Lower", "unit_price": "45"},
    ], status="Draft", case_number="C-100", case_date=date(2026, 10, 3), due_date=date(2026, 10, 10))
    assert c.total_cents == 9900 + 4500
    assert c.status == "Draft"
    summary = cs.batch_summary(s, a.id, 2026, 10)
    assert summary.draft == 1 and summary.count == 1
    found = cs.list_cases(s, search="clasp")
    assert [x.id for x in found] == [c.id]
    with pytest.raises(BusinessError):
        add_case(s, a, "", [{"procedure_id": P(s, "Repair").id}])
    with pytest.raises(BusinessError):  # Reline has no price on file
        add_case(s, a, "X", [{"procedure_id": P(s, "Reline").id}])
    add_case(s, a, "X", [{"procedure_id": P(s, "Reline").id, "unit_price": 120}])


def test_staff_price_override_requires_admin(s):
    a = make_office(s)
    set_current_user(CurrentUser(id=2, username="staff1", role="staff"))
    with pytest.raises(PriceOverrideRequired):
        add_case(s, a, "Patient D", [{"procedure_id": P(s, "Bite Block").id, "unit_price": "20"}])
    c = cs.save_case(s, CaseInput(office_id=a.id, billing_year=2026, billing_month=10, patient_name="Patient D",
                                  items=[ItemInput(procedure_id=P(s, "Bite Block").id, unit_price="20")]),
                     override_by="admin")
    assert c.items[0].unit_price_cents == 2000 and c.items[0].price_override_by == "admin"
    assert c.items[0].list_price_cents == 3500
    # re-saving unchanged override does not ask again
    cs.save_case(s, CaseInput(office_id=a.id, billing_year=2026, billing_month=10, patient_name="Patient D",
                              items=[ItemInput(procedure_id=P(s, "Bite Block").id, unit_price="20")]), case_id=c.id)


def test_invoice_lines_merge_upper_lower(s):
    a = make_office(s)
    add_case(s, a, "Patient A", [{"procedure_id": P(s, "Bite Block").id, "arch": "Upper"},
                                 {"procedure_id": P(s, "Bite Block").id, "arch": "Lower"}])
    add_case(s, a, "Patient B", [{"procedure_id": P(s, "Teeth Try-In").id, "arch": "Upper"},
                                 {"procedure_id": P(s, "Teeth Try-In").id, "arch": "Lower"}])
    add_case(s, a, "Patient D", [{"procedure_id": P(s, "Flexible Partial").id, "arch": "Lower",
                                  "description": "Finish"}])
    inv = inv_svc.create_draft(s, a.id, 2026, 10, date(2026, 10, 31))
    descs = [(l.patient_name, l.description, l.amount_cents) for l in inv.lines]
    assert ("Patient A", "Upper, Lower Bite Block (x2 @ $35.00)", 7000) in descs
    assert ("Patient B", "Upper, Lower Teeth Try-In (x2 @ $99.00)", 19800) in descs
    assert ("Patient D", "Lower Flexible Partial - Finish", 28500) in descs
    assert inv.total_cents == 7000 + 19800 + 28500


def test_manual_invoice_numbers_and_duplicate_billing(s):
    a = make_office(s, "Office A")
    b = make_office(s, "Office B")
    add_case(s, a, "Pa", [{"procedure_id": P(s, "Bite Block").id}])
    add_case(s, a, "Pdraft", [{"procedure_id": P(s, "Bite Block").id}], status="Draft")
    add_case(s, b, "Pb", [{"procedure_id": P(s, "Repair").id}])
    ia = inv_svc.create_draft(s, a.id, 2026, 10)
    assert ia.invoice_number == ""  # never auto-numbered
    with pytest.raises(BusinessError):
        inv_svc.finalize(s, ia.id)  # number required
    inv_svc.finalize(s, ia.id, "1049")
    assert ia.invoice_number == "1049" and ia.status == "Finalized"
    assert len(ia.lines) == 1  # the draft record is not billed
    # duplicate billing: nothing left to bill for A
    with pytest.raises(BusinessError):
        inv_svc.create_draft(s, a.id, 2026, 10)
    # different office, own sequence
    ib = inv_svc.create_draft(s, b.id, 2026, 10)
    with pytest.raises(inv_svc.DuplicateNumberWarning):
        inv_svc.finalize(s, ib.id, "1049")
    inv_svc.finalize(s, ib.id, "205")
    assert ib.invoice_number == "205"
    assert [i.invoice_number for i in inv_svc.list_invoices(s, number="104")] == ["1049"]
    # duplicate number allowed only with explicit confirmation, exact text preserved
    add_case(s, b, "Pb2", [{"procedure_id": P(s, "Repair").id}], month=11)
    ib2 = inv_svc.create_draft(s, b.id, 2026, 11)
    inv_svc.finalize(s, ib2.id, " 205-A ", confirm_duplicates=True)
    assert ib2.invoice_number == "205-A"


def test_finalized_invoice_is_protected(s):
    a = make_office(s)
    c = add_case(s, a, "Pa", [{"procedure_id": P(s, "Bite Block").id}])
    inv = inv_svc.create_draft(s, a.id, 2026, 10)
    inv_svc.finalize(s, inv.id, "318")
    with pytest.raises(BusinessError):
        inv_svc.update_draft(s, inv.id, invoice_number="999")
    with pytest.raises(BusinessError):
        cs.save_case(s, CaseInput(office_id=a.id, billing_year=2026, billing_month=10, patient_name="Changed",
                                  items=[ItemInput(procedure_id=P(s, "Repair").id)]), case_id=c.id)
    with pytest.raises(BusinessError):
        cs.delete_case(s, c.id)
    with pytest.raises(BusinessError):
        cs.set_status(s, [c.id], "Draft")


def test_draft_refresh_edit_and_delete_release_cases(s):
    a = make_office(s)
    c = add_case(s, a, "Pa", [{"procedure_id": P(s, "Bite Block").id}])
    inv = inv_svc.create_draft(s, a.id, 2026, 10)
    # editing a record while on a draft invoice is allowed
    cs.save_case(s, CaseInput(office_id=a.id, billing_year=2026, billing_month=10, patient_name="Pa",
                              status="Approved", items=[ItemInput(procedure_id=P(s, "Repair").id)]), case_id=c.id)
    add_case(s, a, "Late", [{"procedure_id": P(s, "Bite Block").id}])
    inv_svc.refresh_draft(s, inv.id)
    assert inv.total_cents == 10500 + 3500
    inv_svc.remove_case_from_draft(s, inv.id, c.id)
    assert inv.total_cents == 3500
    inv_svc.delete_draft(s, inv.id)
    assert all(x.invoice_id is None for x in cs.list_cases(s, a.id))


def test_tax_calculation(s):
    from dentec.services.common import set_setting
    set_setting(s, "tax_rate", "7.975")
    a = make_office(s)
    pid = P(s, "Full Zirconia Crown").id
    add_case(s, a, "Pa", [{"procedure_id": pid, "taxable": True, "quantity": 3},
                          {"procedure_id": P(s, "Bite Block").id, "taxable": False}])
    inv = inv_svc.create_draft(s, a.id, 2026, 10)
    assert inv.tax_rate == "7.975"
    assert inv.subtotal_cents == 26700 + 3500
    assert inv.taxable_subtotal_cents == 26700
    assert inv.tax_cents == 2129  # 267.00 * 7.975% = 21.29325
    assert inv.total_cents == 30200 + 2129
    inv_svc.finalize(s, inv.id, "1")
    # changing the rate later never touches historical invoices
    set_setting(s, "tax_rate", "9")
    s.refresh(inv)
    assert inv.tax_cents == 2129 and inv.tax_rate == "7.975"
    # per-line method
    totals = inv_svc.compute_totals([{"amount_cents": 333, "taxable": True}] * 3, "10", "line")
    assert totals["tax_cents"] == 99
    assert inv_svc.compute_totals([{"amount_cents": 333, "taxable": True}] * 3, "10", "invoice")["tax_cents"] == 100


def test_tax_exempt_office_defaults(s):
    p = P(s, "PFM Crown")
    pricing.update_procedure(s, p.id, taxable=True)
    a = make_office(s, "Exempt", tax_exempt=True)
    b = make_office(s, "Normal")
    ca = add_case(s, a, "Pa", [{"procedure_id": p.id}])
    cb = add_case(s, b, "Pb", [{"procedure_id": p.id}])
    assert not ca.items[0].taxable and cb.items[0].taxable


def test_invoice_pdf(s, home):
    from dentec.pdf.invoice_pdf import export_invoice
    a = make_office(s, shipping_address="9 Ship Rd\nClovis, CA")
    for i in range(80):  # forces multiple pages
        add_case(s, a, f"Patient {i:02d}", [{"procedure_id": P(s, "Bite Block").id, "arch": "Upper"},
                                           {"procedure_id": P(s, "Bite Block").id, "arch": "Lower"}])
    inv = inv_svc.create_draft(s, a.id, 2026, 10, date(2026, 10, 31))
    prev = export_invoice(s, inv)
    assert prev.exists() and "PREVIEW" in prev.name
    inv_svc.finalize(s, inv.id, "1049")
    out = export_invoice(s, inv)
    assert out.exists() and "1049" in out.name
    import pymupdf
    doc = pymupdf.open(out)
    assert doc.page_count >= 2
    text = "".join(p.get_text() for p in doc)
    assert "DENTEC DENTAL LAB" in text and "1049" in text and "Patient 79" in text
    assert "Page 1 of" in text and "0-30 Days" in text and "Total Outstanding" in text
