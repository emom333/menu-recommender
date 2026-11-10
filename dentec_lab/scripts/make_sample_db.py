"""Build the FICTIONAL sample database (sample_data/dentec_sample.db) and sample lab slips.

    python scripts/make_sample_db.py

All offices, dentists and patients are invented.  Sign in with:
    admin / dentec-admin      (administrator)
    frontdesk / dentec-staff  (staff)
"""
from __future__ import annotations

import random
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dentec.config import Paths, set_paths  # noqa: E402
from dentec.db import database  # noqa: E402
from dentec.services import auth, deliveries, invoices as iv, offices, payments, pricing  # noqa: E402
from dentec.services.cases import CaseInput, ItemInput, save_case  # noqa: E402
from dentec.services.common import CurrentUser, set_current_user, set_setting  # noqa: E402

OFFICES = [
    dict(name="Valley Smiles Dentistry", dentist_name="Dr. Maria Example", contact_person="Lena (office manager)",
         billing_address="1234 W Shaw Ave Ste 100\nFresno, CA 93711", phone="(559) 555-0142",
         email="billing@valleysmiles.example", payment_terms="Net 30"),
    dict(name="Sunrise Family Dental", dentist_name="Dr. Alan Placeholder", contact_person="Rosa",
         billing_address="88 E Herndon Ave\nClovis, CA 93612", shipping_address="88 E Herndon Ave, Suite B\nClovis, CA 93612",
         phone="(559) 555-0177", email="frontdesk@sunrisefamily.example", delivery_method="Email"),
    dict(name="Riverpark Dental Group", dentist_name="Dr. Priya Sample", contact_person="Accounts Payable",
         billing_address="PO Box 4410\nFresno, CA 93744", shipping_address="7790 N Blackstone Ave\nFresno, CA 93720",
         phone="(559) 555-0119", email="ap@riverpark.example"),
    dict(name="Tower District Denture Center", dentist_name="Dr. Sam Fictitious", contact_person="Sam",
         billing_address="510 E Olive Ave\nFresno, CA 93728", phone="(559) 555-0160", tax_exempt=True),
    dict(name="Kingsburg Smile Studio", dentist_name="Dr. Jo Imaginary", billing_address="1450 Draper St\n"
         "Kingsburg, CA 93631", phone="(559) 555-0133", status="Inactive", notes="Closed in 2026 - kept for history."),
]

FIRST = ["Alex", "Jordan", "Casey", "Riley", "Morgan", "Taylor", "Jamie", "Avery", "Quinn", "Reese", "Drew", "Parker",
         "Skyler", "Rowan", "Emerson", "Hayden", "Logan", "Sage", "Blake", "Cameron"]
LAST = ["Sample", "Fictional", "Testcase", "Demo", "Example", "Placeholder", "Mock", "Specimen", "Dummy", "Pretend"]

TEMPLATES = [
    [("Bite Block", "Upper"), ("Bite Block", "Lower")],
    [("Teeth Try-In", "Upper"), ("Teeth Try-In", "Lower")],
    [("Full Denture Finish", "Upper"), (None, "Lower", "2 Clasp", "45")],
    [("Flexible Partial", "Lower", "Finish")],
    [("Full Zirconia Crown", "", "", None, "3, 14", 2)],
    [("PFM Crown", "", "", None, "19")],
    [("Custom Tray", "Upper"), ("Custom Tray", "Lower")],
    [("Repair", "Upper", "Fractured denture base")],
    [("Acrylic Partial Finish", "Upper")],
    [("Soft Night Guard", "Upper")],
    [("E.Max Crown", "", "", None, "8")],
    [("Implant Custom Abutment + Zirconia Crown", "", "", None, "30")],
    [("Reline", "Lower", "Hard reline", "125")],
]


def items_for(s, tpl):
    out = []
    for t in tpl:
        name, arch = t[0], t[1]
        desc = t[2] if len(t) > 2 else ""
        price = t[3] if len(t) > 3 else None
        tooth = t[4] if len(t) > 4 else ""
        qty = t[5] if len(t) > 5 else 1
        pid = pricing.find_procedure(s, name).id if name else None
        out.append(ItemInput(procedure_id=pid, arch=arch, description=desc, unit_price=price, tooth_numbers=tooth,
                             quantity=qty))
    return out


def build(out_db: Path) -> None:
    rnd = random.Random(2026)
    tmp = Path(tempfile.mkdtemp())
    paths = set_paths(Paths(tmp / "home"))
    database.init_db(paths.db_file)
    set_current_user(CurrentUser(id=1, username="admin", role="admin"))
    with database.session_scope() as s:
        auth.create_user(s, "admin", "dentec-admin", "admin", "Lab Administrator", _bootstrap=True)
        auth.create_user(s, "frontdesk", "dentec-staff", "staff", "Front Desk")
        set_setting(s, "tax_rate", "0")
        set_setting(s, "company_phone", "")
        offs = []
        for i, d in enumerate(OFFICES):
            src = "base" if i != 2 else offs[0].id
            offs.append(offices.create_office(s, d, copy_prices_from=src))
        P = lambda n: pricing.find_procedure(s, n).id  # noqa: E731
        # negotiated prices
        pricing.set_office_price(s, offs[1].id, P("Bite Block"), "30.00", "Negotiated 2026")
        pricing.set_office_price(s, offs[1].id, P("Full Zirconia Crown"), "85.00", "Negotiated 2026")
        pricing.set_office_price(s, offs[2].id, P("Teeth Try-In"), "95.00", "Volume discount")
        pricing.set_office_price(s, offs[3].id, P("Full Denture Finish"), "110.00", "Rush service included")
        pricing.set_office_price(s, offs[3].id, P("Reline"), "125.00", "")
        pricing.bulk_adjust(s, offs[0].id, percent=3, round_to="1.00", reason="2026 increase")

        numbers = {offs[0].id: 1047, offs[1].id: 203, offs[2].id: 316, offs[3].id: 88, offs[4].id: 540}
        n_pat = 0
        for (y, m, inv_day) in [(2026, 8, 31), (2026, 9, 30), (2026, 10, 31)]:
            for o in offs:
                if o.status == "Inactive" and m > 8:
                    continue
                k = rnd.randint(4, 9)
                for j in range(k):
                    n_pat += 1
                    name = f"{FIRST[n_pat % len(FIRST)]} {LAST[(n_pat * 7) % len(LAST)]}"
                    tpl = TEMPLATES[rnd.randrange(len(TEMPLATES))]
                    d = date(y, m, min(28, 1 + j * 3))
                    save_case(s, CaseInput(o.id, y, m, name, case_number=f"{m:02d}-{n_pat:03d}", case_date=d,
                                           due_date=date(y, m, min(28, 1 + j * 3 + 7) if j * 3 + 8 <= 28 else 28),
                                           status="Approved", items=items_for(s, tpl)))
                inv = iv.create_draft(s, o.id, y, m, date(y, m, inv_day))
                iv.finalize(s, inv.id, str(numbers[o.id]))
                numbers[o.id] += 1
        # payments: Valley pays in full a month later, Sunrise partially, Riverpark late, Tower pays most
        for inv in iv.list_invoices(s, status="Finalized"):
            if inv.period_month == 10:
                continue
            pay_date = date(inv.invoice_date.year, inv.invoice_date.month + 1, 15)
            if inv.office_id == offs[0].id:
                payments.record_payment(s, inv.office_id, pay_date, f"{inv.total_cents / 100:.2f}", "Check",
                                        f"{4000 + inv.id}", invoice_id=inv.id)
            elif inv.office_id == offs[1].id:
                payments.record_payment(s, inv.office_id, pay_date, f"{inv.total_cents / 200:.2f}", "Credit Card",
                                        invoice_id=inv.id, notes="Partial payment")
            elif inv.office_id == offs[3].id and inv.period_month == 9:
                payments.record_payment(s, inv.office_id, pay_date, "200.00", "Cash", invoice_id=inv.id)
        # a credit note for a remake
        first_sun = [i for i in iv.list_invoices(s, office_id=offs[1].id) if i.period_month == 9][0]
        iv.create_credit_note(s, first_sun.id, f"CN-{first_sun.invoice_number}", "30.00",
                              "Bite block remake - courtesy credit", credit_date=date(2026, 10, 10))
        # November: batch in progress (drafts + approved)
        for j, o in enumerate(offs[:4]):
            for k in range(3):
                n_pat += 1
                name = f"{FIRST[n_pat % len(FIRST)]} {LAST[(n_pat * 3) % len(LAST)]}"
                save_case(s, CaseInput(o.id, 2026, 11, name, case_date=date(2026, 11, 2 + k * 2),
                                       due_date=date(2026, 11, 10 + k * 2),
                                       status="Approved" if k < 2 else "Draft",
                                       items=items_for(s, TEMPLATES[(j * 3 + k) % len(TEMPLATES)])))
        # delivery runs
        due = deliveries.cases_due(s, date(2026, 11, 1), date(2026, 11, 12))
        d1 = deliveries.create_delivery(s, date(2026, 11, 5), [c.id for c in due[:4]], driver="Chris")
        deliveries.complete_delivery(s, d1.id, date(2026, 11, 5))
        deliveries.create_delivery(s, date(2026, 11, 10), [c.id for c in due[4:8]], driver="Chris")
        set_setting(s, "last_backup", "")
    database.close_db()
    out_db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    src = sqlite3.connect(str(paths.db_file))
    if out_db.exists():
        out_db.unlink()
    dst = sqlite3.connect(str(out_db))
    src.backup(dst)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.close()
    src.close()
    shutil.rmtree(tmp, ignore_errors=True)


def make_slips(folder: Path) -> None:
    from dentec.ocr.sample_form import SAMPLE_SLIPS, draw_form, render_scan
    folder.mkdir(parents=True, exist_ok=True)
    for i, v in enumerate(SAMPLE_SLIPS):
        render_scan(v, folder / f"sample_labslip_{i + 1}.jpg", seed=i, skew_deg=[0.6, -0.8, 0.3, 1.0][i], noise=False)
    (folder / "blank_labslip_form.pdf").write_bytes(draw_form(None))


if __name__ == "__main__":
    build(ROOT / "sample_data" / "dentec_sample.db")
    make_slips(ROOT / "sample_data" / "labslips")
    print("Sample database and lab slips written to", ROOT / "sample_data")
