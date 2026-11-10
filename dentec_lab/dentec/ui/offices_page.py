"""Dental Offices: customer profiles, history and balances."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QRadioButton, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from ..db.database import session_scope
from ..money import fmt
from ..services import aging, cases as case_svc, invoices as inv_svc, offices as off_svc, payments as pay_svc
from .widgets import (OfficeCombo, button, confirm, fill_table, first_selected, h1, hint, make_table, row,
                      run_action)


class NewOfficeDialog(QDialog):
    """Choose where the new office's price list comes from."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New dental office - price list")
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Each office has its own negotiated price list. Choose how to start it. "
                           "You can change any price later in Price List."))
        self.r_base = QRadioButton("Start from Dentec reference prices")
        self.r_copy = QRadioButton("Copy the price list of another office:")
        self.r_empty = QRadioButton("Start with an empty price list (enter prices later)")
        self.r_base.setChecked(True)
        self.src = OfficeCombo(include_inactive=True)
        self.src.setEnabled(False)
        self.r_copy.toggled.connect(self.src.setEnabled)
        lay.addWidget(self.r_base)
        lay.addLayout(row(self.r_copy, self.src))
        lay.addWidget(self.r_empty)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if self.src.count() == 0:
            self.r_copy.setEnabled(False)

    def choice(self):
        if self.r_copy.isChecked():
            return self.src.office_id()
        if self.r_empty.isChecked():
            return None
        return "base"


class OfficesPage(QWidget):
    title = "Dental Offices"

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.current_id: int | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Dental Offices"))
        lay.addWidget(hint("Every dental office is a separate customer with its own prices, records, invoices, "
                           "payments and balance. Offices are never combined on one invoice."))
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search offices, dentists, phone...")
        self.search.textChanged.connect(self.reload)
        self.show_inactive = QCheckBox("Show inactive")
        self.show_inactive.setChecked(True)
        self.show_inactive.toggled.connect(self.reload)
        ll.addLayout(row(self.search, self.show_inactive, stretch=False))
        self.table = make_table(["Dental Office", "Dentist", "Phone", "Status", "Balance"], stretch_col=0)
        self.table.itemSelectionChanged.connect(self._selected)
        ll.addWidget(self.table)
        ll.addLayout(row(button("+ New Office", self.new_office, primary=True)))
        split.addWidget(left)

        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        split.setSizes([430, 700])

        # --- profile tab
        prof = QWidget()
        g = QGridLayout(prof)
        self.f = {}
        def le(key):
            w = QLineEdit()
            self.f[key] = w
            return w
        def te(key):
            w = QPlainTextEdit()
            w.setFixedHeight(64)
            self.f[key] = w
            return w
        form1 = QFormLayout()
        form1.addRow("Dental Office Name *", le("name"))
        form1.addRow("Dentist Name", le("dentist_name"))
        form1.addRow("Contact Person", le("contact_person"))
        form1.addRow("Phone Number", le("phone"))
        form1.addRow("Email Address", le("email"))
        st = QComboBox()
        st.addItems(off_svc.OFFICE_STATUSES)
        self.f["status"] = st
        form1.addRow("Account Status", st)
        form2 = QFormLayout()
        form2.addRow("Billing Address", te("billing_address"))
        form2.addRow("Shipping Address", te("shipping_address"))
        form2.addRow("Account Notes", te("notes"))
        g.addLayout(form1, 0, 0)
        g.addLayout(form2, 0, 1)
        sec = QLabel("Billing preferences")
        sec.setObjectName("Section")
        g.addWidget(sec, 1, 0, 1, 2)
        form3 = QFormLayout()
        dm = QComboBox()
        dm.addItems(["Print", "Email", "Both"])
        self.f["delivery_method"] = dm
        form3.addRow("Send invoices by", dm)
        form3.addRow("Invoice email (if different)", le("invoice_email"))
        form3.addRow("Payment terms", le("payment_terms"))
        form4 = QFormLayout()
        self.f["tax_exempt"] = QCheckBox("Tax exempt (new lines default to non-taxable)")
        self.f["show_previous_balance"] = QCheckBox("Show previous balance on invoices")
        form4.addRow(self.f["tax_exempt"])
        form4.addRow(self.f["show_previous_balance"])
        g.addLayout(form3, 2, 0)
        g.addLayout(form4, 2, 1)
        self.btn_save = button("Save Office", self.save, primary=True)
        self.btn_del = button("Delete", self.delete, tooltip="Only offices without records can be deleted")
        g.addLayout(row(self.btn_save, self.btn_del), 3, 0, 1, 2)
        g.setRowStretch(4, 1)
        self.tabs.addTab(prof, "Profile")

        self.t_cases = make_table(["Period", "Date", "Patient", "Procedures", "Total", "Status"], stretch_col=3)
        self.t_inv = make_table(["Invoice #", "Type", "Date", "Period", "Total", "Balance", "Status"])
        self.t_pay = make_table(["Date", "Method", "Check #", "Amount", "Applied", "Notes"], stretch_col=5)
        bal = QWidget()
        bl = QVBoxLayout(bal)
        self.bal_label = QLabel()
        self.bal_label.setObjectName("Section")
        self.t_aging = make_table(["0-30 Days", "31-60 Days", "61-90 Days", "Over 90 Days", "Unapplied Credit",
                                   "Total Outstanding"])
        self.t_aging.setMaximumHeight(70)
        self.t_open = make_table(["Invoice #", "Date", "Age (days)", "Open Balance"])
        bl.addWidget(self.bal_label)
        bl.addWidget(self.t_aging)
        bl.addWidget(QLabel("Open invoices"))
        bl.addWidget(self.t_open)
        self.tabs.addTab(self.t_cases, "Case History")
        self.tabs.addTab(self.t_inv, "Invoices")
        self.tabs.addTab(self.t_pay, "Payments")
        self.tabs.addTab(bal, "Balance")
        self._set_enabled(False)

    # ----------------------------------------------------------------
    def refresh(self):
        self.reload()

    def reload(self):
        with session_scope() as s:
            offs = off_svc.list_offices(s, include_inactive=self.show_inactive.isChecked(), search=self.search.text())
            rows = [[o.name, o.dentist_name, o.phone, o.status, fmt(aging.office_aging(s, o.id).total_cents)]
                    for o in offs]
            ids = [o.id for o in offs]
        fill_table(self.table, rows, ids, right_cols={4})
        if self.current_id in ids:
            self.table.selectRow(ids.index(self.current_id))

    def _set_enabled(self, on: bool):
        for i in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(i, on)
        self.btn_del.setEnabled(on)

    def _selected(self):
        oid = first_selected(self.table)
        if oid is None:
            return
        self.load_office(oid)

    def load_office(self, oid: int):
        self.current_id = oid
        with session_scope() as s:
            o = s.get(off_svc.Office, oid)
            for k, w in self.f.items():
                v = getattr(o, k)
                if isinstance(w, QLineEdit):
                    w.setText(v or "")
                elif isinstance(w, QPlainTextEdit):
                    w.setPlainText(v or "")
                elif isinstance(w, QComboBox):
                    w.setCurrentText(v or "")
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
            cs = case_svc.list_cases(s, oid)
            crow = [[f"{c.billing_month:02d}/{c.billing_year}", c.case_date.strftime("%m/%d/%Y") if c.case_date else "",
                     c.patient_name, case_svc.case_label(c), fmt(c.total_cents),
                     ("Invoiced #" + c.invoice.invoice_number) if (c.invoice and c.invoice.status == "Finalized")
                     else ("On draft invoice" if c.invoice_id else c.status)] for c in reversed(cs)]
            invs = inv_svc.list_invoices(s, office_id=oid)
            irow = [[i.invoice_number, i.kind, i.invoice_date.strftime("%m/%d/%Y"),
                     inv_svc.period_label(i.period_year, i.period_month), fmt(i.total_cents),
                     fmt(aging.invoice_balance(s, i)), aging.invoice_status(s, i)] for i in invs]
            pays = pay_svc.list_payments(s, office_id=oid)
            prow = [[p.payment_date.strftime("%m/%d/%Y"), p.method, p.check_number, fmt(p.amount_cents),
                     ", ".join(f"#{a.invoice.invoice_number}" for a in p.allocations)
                     + (f" (unapplied {fmt(p.unapplied_cents)})" if p.unapplied_cents else ""), p.notes]
                    for p in pays]
            ag = aging.office_aging(s, oid)
            arow = [[fmt(v) for v in ag.buckets] + [fmt(-ag.unapplied_credit_cents), fmt(ag.total_cents)]]
            orow = [[i.invoice_number, i.invoice_date.strftime("%m/%d/%Y"), d, fmt(b)] for i, b, d in ag.invoices]
            name = o.name
        fill_table(self.t_cases, crow, right_cols={4})
        fill_table(self.t_inv, irow, right_cols={4, 5})
        fill_table(self.t_pay, prow, right_cols={3})
        fill_table(self.t_aging, arow, right_cols=set(range(6)))
        fill_table(self.t_open, orow, right_cols={2, 3})
        self.bal_label.setText(f"{name}: outstanding balance {arow[0][-1]}")
        self._set_enabled(True)

    def _form_data(self) -> dict:
        d = {}
        for k, w in self.f.items():
            if isinstance(w, QLineEdit):
                d[k] = w.text()
            elif isinstance(w, QPlainTextEdit):
                d[k] = w.toPlainText()
            elif isinstance(w, QComboBox):
                d[k] = w.currentText()
            elif isinstance(w, QCheckBox):
                d[k] = w.isChecked()
        return d

    def new_office(self):
        self.current_id = None
        self.table.clearSelection()
        for w in self.f.values():
            if isinstance(w, (QLineEdit, QPlainTextEdit)):
                w.clear()
        self.f["status"].setCurrentText("Active")
        self.f["delivery_method"].setCurrentText("Print")
        self.f["payment_terms"].setText("Net 30")
        self.f["tax_exempt"].setChecked(False)
        self.f["show_previous_balance"].setChecked(True)
        self._set_enabled(False)
        self.tabs.setCurrentIndex(0)
        self.f["name"].setFocus()

    def save(self):
        data = self._form_data()
        if self.current_id is None:
            if not data["name"].strip():
                from .widgets import error
                error(self, "Dental Office Name is required.")
                return
            dlg = NewOfficeDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            src = dlg.choice()
            oid = run_action(self, lambda s: off_svc.create_office(s, data, copy_prices_from=src).id)
        else:
            oid = run_action(self, lambda s: off_svc.update_office(s, self.current_id, data).id)
        if oid:
            self.current_id = oid
            self.reload()
            self.load_office(oid)
            self.main.offices_changed()
            self.main.status(f"Saved {data['name']}")

    def delete(self):
        if self.current_id is None:
            return
        if not confirm(self, "Delete this dental office? This cannot be undone."):
            return
        if run_action(self, lambda s: off_svc.delete_office(s, self.current_id) or True):
            self.new_office()
            self.reload()
            self.main.offices_changed()

