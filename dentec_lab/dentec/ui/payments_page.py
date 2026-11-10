"""Payments: record full/partial payments and see open invoices per office."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QInputDialog, QLabel, QLineEdit,
                               QVBoxLayout, QWidget, QHBoxLayout)

from ..config import PAYMENT_METHODS
from ..db.database import session_scope
from ..money import fmt
from ..services import aging, payments as pay_svc
from ..services.common import current_user
from .widgets import (DateEdit, OfficeCombo, button, confirm, error, fill_table, first_selected, h1, hint,
                      make_table, row, run_action)


class PaymentsPage(QWidget):
    title = "Payments"

    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Payments"))
        lay.addWidget(hint("Record payments received from a dental office. Apply a payment to a specific invoice, or "
                           "let it pay the oldest open invoices first. Overpayments stay on the account as credit."))
        self.office = OfficeCombo(include_inactive=True)
        self.office.currentIndexChanged.connect(self.reload)
        self.balance = QLabel()
        self.balance.setObjectName("Section")
        lay.addLayout(row(QLabel("Dental Office"), self.office, 16, self.balance))

        mid = QHBoxLayout()
        g_open = QGroupBox("Open invoices (oldest first)")
        ol = QVBoxLayout(g_open)
        self.open = make_table(["Invoice #", "Invoice Date", "Age (days)", "Total", "Open Balance", "Status"])
        ol.addWidget(self.open)
        mid.addWidget(g_open, 3)

        g_new = QGroupBox("Record a payment")
        f = QFormLayout(g_new)
        self.p_date = DateEdit(date.today(), allow_blank=False)
        self.p_amount = QLineEdit()
        self.p_amount.setPlaceholderText("0.00")
        self.p_method = QComboBox()
        self.p_method.addItems(PAYMENT_METHODS)
        self.p_method.setCurrentText("Check")
        self.p_check = QLineEdit()
        self.p_apply = QComboBox()
        self.p_auto = QCheckBox("Apply any remainder to the oldest open invoices")
        self.p_auto.setChecked(True)
        self.p_notes = QLineEdit()
        f.addRow("Payment date", self.p_date)
        f.addRow("Amount *", self.p_amount)
        f.addRow("Method", self.p_method)
        f.addRow("Check number", self.p_check)
        f.addRow("Apply to", self.p_apply)
        f.addRow(self.p_auto)
        f.addRow("Notes", self.p_notes)
        f.addRow(row(button("Pay Full Balance of Selected", self.fill_selected),
                     button("Record Payment", self.record, primary=True)))
        mid.addWidget(g_new, 2)
        lay.addLayout(mid, 1)

        g_hist = QGroupBox("Payment history")
        hl = QVBoxLayout(g_hist)
        self.hist = make_table(["Date", "Amount", "Method", "Check #", "Applied To", "Unapplied", "Notes",
                                "Recorded By"], stretch_col=4)
        hl.addWidget(self.hist)
        hl.addLayout(row(button("Apply Unapplied Credit...", self.apply_credit),
                         button("Delete Payment (admin)", self.delete)))
        lay.addWidget(g_hist, 1)

    def refresh(self):
        self.office.reload()
        self.reload()

    def offices_changed(self):
        self.office.reload()

    def select_office(self, office_id: int, invoice_id: int | None = None):
        self.office.set_office(office_id)
        self.reload()
        if invoice_id:
            i = self.p_apply.findData(invoice_id)
            if i >= 0:
                self.p_apply.setCurrentIndex(i)

    def reload(self):
        oid = self.office.office_id()
        self.p_apply.clear()
        self.p_apply.addItem("Oldest open invoices first", None)
        if oid is None:
            fill_table(self.open, [])
            fill_table(self.hist, [])
            self.balance.setText("")
            return
        with session_scope() as s:
            ag = aging.office_aging(s, oid)
            orow, oids = [], []
            for inv, bal, days in ag.invoices:
                orow.append([inv.invoice_number, inv.invoice_date.strftime("%m/%d/%Y"), days, fmt(inv.total_cents),
                             fmt(bal), aging.invoice_status(s, inv)])
                oids.append(inv.id)
                self.p_apply.addItem(f"Invoice #{inv.invoice_number}  (open {fmt(bal)})", inv.id)
            pays = pay_svc.list_payments(s, oid)
            hrow = [[p.payment_date.strftime("%m/%d/%Y"), fmt(p.amount_cents), p.method, p.check_number,
                     ", ".join(f"#{a.invoice.invoice_number} {fmt(a.amount_cents)}" for a in p.allocations),
                     fmt(p.unapplied_cents) if p.unapplied_cents else "", p.notes, p.created_by] for p in pays]
            hids = [p.id for p in pays]
            total = ag.total_cents
            credit = ag.unapplied_credit_cents
        fill_table(self.open, orow, oids, right_cols={2, 3, 4})
        fill_table(self.hist, hrow, hids, right_cols={1, 5})
        self.balance.setText(f"Outstanding balance: {fmt(total)}" + (f"   (includes unapplied credit {fmt(credit)})"
                                                                     if credit else ""))

    def fill_selected(self):
        iid = first_selected(self.open)
        if not iid:
            error(self, "Select an open invoice.")
            return
        i = self.p_apply.findData(iid)
        self.p_apply.setCurrentIndex(i)
        r = self.open.currentRow()
        self.p_amount.setText(self.open.item(r, 4).text().replace("$", "").replace(",", ""))

    def record(self):
        oid = self.office.office_id()
        if oid is None:
            error(self, "Select a dental office.")
            return
        inv_id = self.p_apply.currentData()
        p = run_action(self, lambda s: pay_svc.record_payment(
            s, oid, self.p_date.value(), self.p_amount.text(), self.p_method.currentText(), self.p_check.text(),
            self.p_notes.text(), invoice_id=inv_id, auto_apply=self.p_auto.isChecked()).id)
        if p:
            self.main.status("Payment recorded")
            for w in (self.p_amount, self.p_check, self.p_notes):
                w.clear()
            self.reload()
            self.main.data_changed()

    def apply_credit(self):
        pid = first_selected(self.hist)
        if not pid:
            error(self, "Select a payment that has an unapplied amount.")
            return
        choices = [self.p_apply.itemText(i) for i in range(self.p_apply.count())]
        sel, ok = QInputDialog.getItem(self, "Apply credit", "Apply the unapplied amount to:", choices, 0, False)
        if not ok:
            return
        inv_id = self.p_apply.itemData(choices.index(sel))
        if run_action(self, lambda s: pay_svc.apply_credit(s, pid, inv_id) and True):
            self.reload()
            self.main.data_changed()

    def delete(self):
        pid = first_selected(self.hist)
        if not pid:
            return
        if not current_user().is_admin:
            error(self, "Only an administrator can delete payments.")
            return
        reason, ok = QInputDialog.getText(self, "Delete payment", "Reason (kept in the activity log):")
        if not ok:
            return
        if confirm(self, "Delete this payment? Invoice balances will increase again."):
            if run_action(self, lambda s: pay_svc.delete_payment(s, pid, reason) or True):
                self.reload()
                self.main.data_changed()
