"""Monthly Invoices: billing run, invoice editor, finalization, reprints."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
                               QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from ..db.database import session_scope
from ..money import fmt, parse_rate
from ..pdf.invoice_pdf import export_invoice
from ..services import aging, cases as case_svc, invoices as inv_svc
from ..services.common import BusinessError, current_user, get_settings
from .widgets import (DateEdit, MonthPicker, OfficeCombo, button, confirm, error, fill_table, first_selected, h1,
                      hint, info, make_table, open_file, print_file, row, run_action, selected_data)

_smtp_password: str | None = None


def email_invoice(parent, invoice_id: int) -> None:
    global _smtp_password
    from ..services.emailer import send_email
    with session_scope() as s:
        inv = inv_svc.get_invoice(s, invoice_id)
        if inv.status != "Finalized":
            error(parent, "Only finalized invoices can be e-mailed.")
            return
        path = export_invoice(s, inv)
        to = inv.office.invoice_email or inv.office.email
        st = get_settings(s)
        subj = (f"{st.get('company_name', 'Dentec Dental Lab').title()} - {inv.kind} {inv.invoice_number} - "
                f"{inv_svc.period_label(inv.period_year, inv.period_month)}")
        body = (f"Dear {inv.office.contact_person or inv.office.name},\n\nPlease find attached {inv.kind.lower()} "
                f"{inv.invoice_number} for {inv_svc.period_label(inv.period_year, inv.period_month)} "
                f"(total {fmt(inv.total_cents)}).\n\nThank you,\n{st.get('company_name', '')}\n"
                f"{st.get('company_phone', '')}")
    if not st.get("smtp_host"):
        info(parent, "E-mail sending is not set up (Backup & Settings > E-mail). The PDF folder will open so you "
                     "can attach the invoice in your own e-mail program.\n\n" + str(path))
        open_file(path.parent)
        return
    to, ok = QInputDialog.getText(parent, "E-mail invoice", "Send to:", QLineEdit.Normal, to)
    if not ok or not to:
        return
    if _smtp_password is None and st.get("smtp_user"):
        pw, ok = QInputDialog.getText(parent, "E-mail password", f"Password for {st.get('smtp_user')}:",
                                      QLineEdit.Password)
        if not ok:
            return
        _smtp_password = pw
    try:
        send_email(st, _smtp_password or "", to, subj, body, [path])
    except Exception as exc:  # noqa: BLE001
        _smtp_password = None
        error(parent, f"The e-mail could not be sent:\n{exc}")
        return
    with session_scope() as s:
        from ..services.common import audit
        audit(s, "email_invoice", "invoice", invoice_id, to)
    info(parent, f"Invoice e-mailed to {to}.")


class CreditNoteDialog(QDialog):
    def __init__(self, inv_number: str, balance: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Credit note against invoice #{inv_number}")
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"Finalized invoices cannot be edited. A credit note reduces the open balance of "
                           f"invoice #{inv_number} (currently {fmt(balance)}). Enter the credit note number yourself."))
        form = QFormLayout()
        self.number = QLineEdit()
        self.number.setPlaceholderText("Credit note number (required)")
        self.date = DateEdit(date.today(), allow_blank=False)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("Credit amount before tax, e.g. 35.00")
        self.taxable = QLineEdit()
        self.taxable.setPlaceholderText("Taxable part of the credit (blank = none)")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Description, e.g. Patient A - remake credit")
        form.addRow("Credit note number *", self.number)
        form.addRow("Date", self.date)
        form.addRow("Amount *", self.amount)
        form.addRow("Taxable portion", self.taxable)
        form.addRow("Description *", self.reason)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class InvoiceEditor(QDialog):
    """Review / edit a draft invoice, finalize it, or view a finalized one."""

    def __init__(self, invoice_id: int, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.invoice_id = invoice_id
        self.resize(1080, 760)
        lay = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setObjectName("PageTitle")
        lay.addWidget(self.title)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        lay.addWidget(self.banner)

        top = QGridLayout()
        g1 = QGroupBox("Invoice")
        f1 = QFormLayout(g1)
        self.number = QLineEdit()
        self.number.setPlaceholderText("Type the invoice number (never generated automatically)")
        f = self.number.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        self.number.setFont(f)
        self.number.textEdited.connect(self._check_number)
        self.number_hint = QLabel()
        self.number_hint.setObjectName("Hint")
        self.number_hint.setWordWrap(True)
        self.date = DateEdit(date.today(), allow_blank=False)
        self.tax_rate = QLineEdit()
        self.tax_rate.setMaximumWidth(90)
        self.tax_method = QComboBox()
        self.tax_method.addItem("Round tax once on the taxable subtotal", "invoice")
        self.tax_method.addItem("Round tax on each taxable line", "line")
        f1.addRow("Invoice Number *", self.number)
        f1.addRow("", self.number_hint)
        f1.addRow("Invoice Date", self.date)
        f1.addRow("Tax Rate (%)", row(self.tax_rate, self.tax_method))
        g2 = QGroupBox("Bill To / Ship To")
        f2 = QGridLayout(g2)
        self.bill_to = QPlainTextEdit()
        self.ship_to = QPlainTextEdit()
        for w in (self.bill_to, self.ship_to):
            w.setFixedHeight(92)
        f2.addWidget(QLabel("Bill To"), 0, 0)
        f2.addWidget(QLabel("Ship To"), 0, 1)
        f2.addWidget(self.bill_to, 1, 0)
        f2.addWidget(self.ship_to, 1, 1)
        top.addWidget(g1, 0, 0)
        top.addWidget(g2, 0, 1)
        top.setColumnStretch(1, 1)
        lay.addLayout(top)

        self.lines = make_table(["Patient", "Description", "Tax", "Amount", "Source"], stretch_col=1)
        lay.addWidget(self.lines, 1)
        self.totals = QLabel()
        self.totals.setObjectName("Section")
        self.totals.setAlignment(Qt.AlignRight)
        lay.addWidget(self.totals)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes printed on the invoice (optional)")
        self.notes.setFixedHeight(48)
        lay.addWidget(self.notes)

        # draft buttons
        self.b_refresh = button("Refresh From Records", self.refresh_lines,
                                tooltip="Re-read the approved records (also adds newly approved ones)")
        self.b_rm_case = button("Remove Selected Record", self.remove_case)
        self.b_add = button("Add Manual Line...", self.add_manual)
        self.b_rm_line = button("Remove Manual Line", self.remove_manual)
        self.b_save = button("Save Draft", self.save_draft)
        self.b_preview = button("Preview PDF", self.preview)
        self.b_final = button("Finalize Invoice", self.finalize, primary=True)
        self.b_delete = button("Delete Draft", self.delete_draft)
        # finalized buttons
        self.b_pdf = button("Open PDF", self.open_pdf)
        self.b_print = button("Print", self.print_pdf, primary=True)
        self.b_email = button("Email", lambda: email_invoice(self, self.invoice_id))
        self.b_credit = button("Credit Note...", self.credit_note)
        self.b_void = button("Void...", self.void)
        self.b_close = button("Close", self.accept)
        self.draft_row = row(self.b_refresh, self.b_rm_case, self.b_add, self.b_rm_line, None, self.b_delete,
                             self.b_save, self.b_preview, self.b_final)
        self.final_row = row(self.b_pdf, self.b_print, self.b_email, None, self.b_credit, self.b_void, self.b_close)
        lay.addLayout(self.draft_row)
        lay.addLayout(self.final_row)
        self.load()

    # ------------------------------------------------------------------
    def load(self):
        with session_scope() as s:
            inv = inv_svc.get_invoice(s, self.invoice_id)
            self.is_draft = inv.status == "Draft"
            self.kind = inv.kind
            self.office_id = inv.office_id
            period = inv_svc.period_label(inv.period_year, inv.period_month)
            self.title.setText(f"{inv.kind} - {inv.office.name} - {period}")
            self.number.setText(inv.invoice_number)
            self.date.set_value(inv.invoice_date)
            self.tax_rate.setText(inv.tax_rate)
            self.tax_method.setCurrentIndex(0 if inv.tax_method == "invoice" else 1)
            self.bill_to.setPlainText(inv.bill_to)
            self.ship_to.setPlainText(inv.ship_to)
            self.notes.setPlainText(inv.notes)
            rows, data = [], []
            for l in inv.lines:
                rows.append([l.patient_name, l.description, "T" if l.taxable else "", fmt(l.amount_cents),
                             "manual line" if l.is_manual else "record"])
                data.append(("manual", l.id) if l.is_manual else ("case", l.case_id))
            totals = (inv_svc.compute_totals(inv.lines, inv.tax_rate, inv.tax_method) if self.is_draft else
                      {"subtotal_cents": inv.subtotal_cents, "taxable_subtotal_cents": inv.taxable_subtotal_cents,
                       "tax_cents": inv.tax_cents, "total_cents": inv.total_cents})
            hint_no = inv_svc.last_number_hint(s, inv.office_id)
            others = inv_svc.existing_for_period(s, inv.office_id, inv.period_year, inv.period_month, inv.id)
            status = aging.invoice_status(s, inv)
            bal = aging.invoice_balance(s, inv)
            void_reason = inv.void_reason
            n_cases = len({l.case_id for l in inv.lines if l.case_id})
        fill_table(self.lines, rows, data, right_cols={3})
        self.totals.setText(f"{n_cases} patient records   |   Subtotal {fmt(totals['subtotal_cents'])}   |   "
                            f"Taxable {fmt(totals['taxable_subtotal_cents'])}   |   Tax {fmt(totals['tax_cents'])}"
                            f"   |   TOTAL {fmt(totals['total_cents'])}")
        self.number_hint.setText(("Last number used for this office: " + hint_no + " (for reference only)")
                                 if hint_no else "Each office may use its own numbering sequence.")
        for w in (self.number, self.tax_rate, self.notes):
            w.setReadOnly(not self.is_draft)
        for w in (self.bill_to, self.ship_to):
            w.setReadOnly(not self.is_draft)
        self.date.setEnabled(self.is_draft)
        self.tax_method.setEnabled(self.is_draft)
        for b in (self.b_refresh, self.b_rm_case, self.b_add, self.b_rm_line, self.b_save, self.b_preview,
                  self.b_final, self.b_delete):
            b.setVisible(self.is_draft)
        for b in (self.b_pdf, self.b_print, self.b_email, self.b_credit, self.b_void):
            b.setVisible(not self.is_draft)
        self.b_credit.setVisible(not self.is_draft and self.kind == "Invoice" and status != "Void")
        self.b_void.setVisible(not self.is_draft and status not in ("Void",) and self.kind == "Invoice")
        if self.is_draft:
            msg = "DRAFT - review the lines, type the invoice number, preview, then Finalize."
            if others:
                msg += (f"  Note: this office already has invoice(s) "
                        f"{', '.join('#' + o.invoice_number for o in others)} for this period.")
            self.banner.setObjectName("Warn")
            self.banner.setText(msg)
        elif status == "Void":
            self.banner.setObjectName("Warn")
            self.banner.setText(f"VOID - {void_reason}")
        else:
            self.banner.setObjectName("Ok")
            self.banner.setText(f"Finalized and protected from changes.  Status: {status}.  "
                                f"Open balance: {fmt(bal)}.")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self._check_number()

    def _check_number(self):
        if not self.is_draft:
            return
        with session_scope() as s:
            chk = inv_svc.check_invoice_number(s, self.invoice_id, self.number.text())
        if chk.warnings:
            self.number.setProperty("uncertain", "true")
            self.number.setToolTip("\n".join(chk.warnings))
        else:
            self.number.setProperty("uncertain", "false")
            self.number.setToolTip("")
        self.number.style().unpolish(self.number)
        self.number.style().polish(self.number)

    def _save(self, s):
        try:
            rate = parse_rate(self.tax_rate.text())
        except ValueError as exc:
            raise BusinessError(str(exc)) from exc
        return inv_svc.update_draft(s, self.invoice_id, invoice_number=self.number.text(),
                                    invoice_date=self.date.value(), bill_to=self.bill_to.toPlainText(),
                                    ship_to=self.ship_to.toPlainText(), notes=self.notes.toPlainText(),
                                    tax_rate=str(rate), tax_method=self.tax_method.currentData())

    def save_draft(self, quiet: bool = False) -> bool:
        ok = run_action(self, lambda s: self._save(s) and True)
        if ok:
            self.load()
            if not quiet:
                self.main.status("Draft invoice saved")
        return bool(ok)

    def refresh_lines(self):
        if self.save_draft(quiet=True) and run_action(self, lambda s: inv_svc.refresh_draft(s, self.invoice_id) and True):
            self.load()

    def remove_case(self):
        d = first_selected(self.lines)
        if not d or d[0] != "case":
            error(self, "Select a line that comes from a patient record.")
            return
        if confirm(self, "Remove this patient record from the invoice? It stays approved and can be billed later."):
            if run_action(self, lambda s: inv_svc.remove_case_from_draft(s, self.invoice_id, d[1]) and True):
                self.load()

    def add_manual(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Add manual line")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        pat = QLineEdit()
        desc = QLineEdit()
        amt = QLineEdit()
        tax = QCheckBox("Taxable")
        form.addRow("Patient (optional)", pat)
        form.addRow("Description *", desc)
        form.addRow("Amount *", amt)
        form.addRow(tax)
        lay.addLayout(form)
        lay.addWidget(hint("Use for adjustments such as delivery fees or discounts (negative amount)."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        if run_action(self, lambda s: inv_svc.add_manual_line(s, self.invoice_id, desc.text(), amt.text(),
                                                             tax.isChecked(), pat.text()) and True):
            self.load()

    def remove_manual(self):
        d = first_selected(self.lines)
        if not d or d[0] != "manual":
            error(self, "Select a manual line.")
            return
        if run_action(self, lambda s: inv_svc.remove_manual_line(s, self.invoice_id, d[1]) or True):
            self.load()

    def preview(self):
        if not self.save_draft(quiet=True):
            return
        with session_scope() as s:
            path = export_invoice(s, inv_svc.get_invoice(s, self.invoice_id))
        open_file(path)

    def finalize(self):
        if not self.save_draft(quiet=True):
            return
        number = self.number.text().strip()
        with session_scope() as s:
            chk = inv_svc.check_invoice_number(s, self.invoice_id, number)
            inv = inv_svc.get_invoice(s, self.invoice_id)
            total = fmt(inv_svc.compute_totals(inv.lines, inv.tax_rate, inv.tax_method)["total_cents"])
            office = inv.office.name
        if not chk.ok:
            error(self, chk.errors[0])
            self.number.setFocus()
            return
        dup = bool(chk.warnings)
        if dup and not confirm(self, "Possible duplicate invoice number:\n\n" + "\n".join(chk.warnings) +
                               f"\n\nUse number {number} anyway?"):
            return
        if not confirm(self, f"Finalize invoice {number} for {office}, total {total}?\n\nAfter finalizing, the "
                             "invoice and its records are locked. Corrections require a credit note.",
                       default_no=False):
            return
        def go(s):
            inv = inv_svc.finalize(s, self.invoice_id, number, confirm_duplicates=dup)
            return export_invoice(s, inv)
        path = run_action(self, go)
        if path:
            self.load()
            self.main.status(f"Invoice {number} finalized - saved {path.name}")
            self.main.data_changed()
            if confirm(self, "Invoice finalized and saved as PDF.\n\nPrint it now?", default_no=False):
                print_file(self, path)

    def delete_draft(self):
        if confirm(self, "Delete this draft invoice? The patient records are released and stay approved."):
            if run_action(self, lambda s: inv_svc.delete_draft(s, self.invoice_id) or True):
                self.main.data_changed()
                self.accept()

    def _pdf(self):
        with session_scope() as s:
            return export_invoice(s, inv_svc.get_invoice(s, self.invoice_id))

    def open_pdf(self):
        open_file(self._pdf())

    def print_pdf(self):
        print_file(self, self._pdf())

    def credit_note(self):
        with session_scope() as s:
            inv = inv_svc.get_invoice(s, self.invoice_id)
            bal = aging.invoice_balance(s, inv)
            num = inv.invoice_number
        if not current_user().is_admin:
            error(self, "Only an administrator can issue credit notes. Ask an administrator to sign in.")
            return
        dlg = CreditNoteDialog(num, bal, self)
        if dlg.exec() != QDialog.Accepted:
            return
        def go(s, confirm_dup=False):
            return inv_svc.create_credit_note(s, self.invoice_id, dlg.number.text(), dlg.amount.text(),
                                              dlg.reason.text(), dlg.taxable.text() or None, dlg.date.value(),
                                              confirm_duplicates=confirm_dup).id
        try:
            with session_scope() as s:
                cn_id = go(s)
        except inv_svc.DuplicateNumberWarning as w:
            if not confirm(self, str(w) + "\n\nUse this number anyway?"):
                return
            cn_id = run_action(self, lambda s: go(s, True))
        except (BusinessError, ValueError) as exc:
            error(self, str(exc))
            return
        if cn_id:
            self.main.data_changed()
            self.load()
            InvoiceEditor(cn_id, self.main, self).exec()

    def void(self):
        if not current_user().is_admin:
            error(self, "Only an administrator can void invoices.")
            return
        reason, ok = QInputDialog.getText(self, "Void invoice", "Reason for voiding (required). The patient "
                                          "records will be released so they can be billed again:")
        if not ok:
            return
        if run_action(self, lambda s: inv_svc.void_invoice(s, self.invoice_id, reason) and True):
            self.main.data_changed()
            self.load()


class InvoicesPage(QWidget):
    title = "Monthly Invoices"

    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Monthly Invoices"))
        lay.addWidget(hint("Step 1: pick the billing month. Step 2: select offices with approved records and create "
                           "draft invoices. Step 3: open each draft, type its invoice number, preview and finalize. "
                           "Each office gets its own invoice; a record can only ever be billed once."))
        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)

        top = QGroupBox("Billing run - offices with approved records not yet invoiced")
        tl = QVBoxLayout(top)
        self.month = MonthPicker()
        self.month.changed.connect(self.reload_run)
        tl.addLayout(row(QLabel("Billing month"), self.month))
        self.run = make_table(["Dental Office", "Approved Records", "Draft Records (not billable)", "Amount",
                               "Existing Invoices This Month"], stretch_col=0, multi=True)
        self.run.doubleClicked.connect(self.create_drafts)
        tl.addWidget(self.run)
        tl.addLayout(row(button("Create Draft Invoice(s) for Selected", self.create_drafts, primary=True),
                         button("Open Records in Case Entry", self.open_in_case_entry)))
        split.addWidget(top)

        bot = QGroupBox("Invoices")
        bl = QVBoxLayout(bot)
        self.f_office = OfficeCombo(include_inactive=True, blank="All offices")
        self.f_status = QComboBox()
        self.f_status.addItems(["All", "Draft", "Finalized", "Void"])
        self.f_number = QLineEdit()
        self.f_number.setPlaceholderText("Search invoice #")
        self.f_period = QCheckBox("Only this billing month")
        for w in (self.f_office, self.f_status):
            w.currentIndexChanged.connect(self.reload_list)
        self.f_number.textChanged.connect(self.reload_list)
        self.f_period.toggled.connect(self.reload_list)
        bl.addLayout(row(self.f_office, self.f_status, self.f_number, self.f_period))
        self.list = make_table(["Invoice #", "Type", "Dental Office", "Invoice Date", "Period", "Total", "Balance",
                                "Status"], stretch_col=2)
        self.list.doubleClicked.connect(self.open_selected)
        bl.addWidget(self.list)
        bl.addLayout(row(button("Open", self.open_selected, primary=True), button("Reprint / Save PDF", self.reprint),
                         button("Print", self.print_selected), button("Email", self.email_selected)))
        split.addWidget(bot)
        split.setSizes([330, 420])

    def refresh(self):
        self.f_office.reload()
        self.reload_run()

    def offices_changed(self):
        self.f_office.reload()

    def reload_run(self):
        y, m = self.month.value()
        with session_scope() as s:
            rows, ids = [], []
            for r in case_svc.unbilled_by_office(s, y, m):
                ex = inv_svc.existing_for_period(s, r["office_id"], y, m)
                rows.append([r["office"], r["approved"], r["draft"], fmt(r["amount_cents"]),
                             ", ".join(f"#{i.invoice_number or '(draft)'}" for i in ex)])
                ids.append(r["office_id"])
        fill_table(self.run, rows, ids, right_cols={1, 2, 3})
        self.reload_list()

    def reload_list(self):
        y, m = self.month.value()
        st = self.f_status.currentText()
        with session_scope() as s:
            invs = inv_svc.list_invoices(s, office_id=self.f_office.office_id(),
                                         status=None if st == "All" else st, number=self.f_number.text(),
                                         year=y if self.f_period.isChecked() else None,
                                         month=m if self.f_period.isChecked() else None)
            rows = [[i.invoice_number or "(not entered)", i.kind, i.office.name, i.invoice_date.strftime("%m/%d/%Y"),
                     inv_svc.period_label(i.period_year, i.period_month), fmt(i.total_cents),
                     fmt(aging.invoice_balance(s, i)), aging.invoice_status(s, i)] for i in invs]
            ids = [i.id for i in invs]
        fill_table(self.list, rows, ids, right_cols={5, 6})

    def create_drafts(self):
        ids = selected_data(self.run)
        if not ids:
            error(self, "Select one or more offices in the billing run list.")
            return
        y, m = self.month.value()
        created = []
        for oid in ids:
            with session_scope() as s:
                draft = inv_svc.find_draft(s, oid, y, m)
                draft_id = draft.id if draft else None
            if draft_id:
                run_action(self, lambda s, d=draft_id: inv_svc.refresh_draft(s, d) and True)
                created.append(draft_id)
                continue
            inv_id = run_action(self, lambda s, o=oid: inv_svc.create_draft(s, o, y, m, _default_invoice_date(y, m)).id)
            if inv_id:
                created.append(inv_id)
        self.reload_run()
        if created:
            self.main.status(f"{len(created)} draft invoice(s) ready")
            for inv_id in created:
                InvoiceEditor(inv_id, self.main, self).exec()
            self.reload_run()

    def open_in_case_entry(self):
        oid = first_selected(self.run)
        if oid:
            y, m = self.month.value()
            self.main.goto("case_entry")
            self.main.pages["case_entry"].open_batch(oid, y, m)

    def open_selected(self):
        iid = first_selected(self.list)
        if iid:
            InvoiceEditor(iid, self.main, self).exec()
            self.reload_run()

    def open_invoice(self, invoice_id: int):
        InvoiceEditor(invoice_id, self.main, self).exec()
        self.reload_run()

    def _pdf(self):
        iid = first_selected(self.list)
        if not iid:
            error(self, "Select an invoice.")
            return None
        with session_scope() as s:
            return export_invoice(s, inv_svc.get_invoice(s, iid))

    def reprint(self):
        p = self._pdf()
        if p:
            open_file(p)

    def print_selected(self):
        p = self._pdf()
        if p:
            print_file(self, p)

    def email_selected(self):
        iid = first_selected(self.list)
        if iid:
            email_invoice(self, iid)


def _default_invoice_date(y: int, m: int) -> date:
    """Last day of the billing month, or today if the month is still running."""
    import calendar
    return min(date(y, m, calendar.monthrange(y, m)[1]), date.today())
