"""Monthly Case Entry: fast month-end batch entry of patient records per office."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSpinBox, QSplitter,
                               QTableWidget, QVBoxLayout, QWidget, QCompleter)

from ..config import ARCH_CHOICES
from ..db.database import session_scope
from ..money import fmt, from_cents, to_cents
from ..services import cases as case_svc, pricing
from ..services.cases import CaseInput, ItemInput, PriceOverrideRequired
from ..services.common import BusinessError
from .widgets import (DateEdit, MonthPicker, OfficeCombo, authorize_admin, button, confirm, error, fill_table,
                      h1, hint, make_table, open_file, row, run_action, selected_data)

ITEM_COLS = ["Arch", "Procedure", "Additional Description", "Tooth #", "Qty", "Unit Price", "Tax", "Total"]


class ItemsGrid(QTableWidget):
    """Editable procedure lines for one patient record."""

    def __init__(self, page):
        super().__init__(0, len(ITEM_COLS))
        self.page = page
        self.setHorizontalHeaderLabels(ITEM_COLS)
        self.verticalHeader().setVisible(False)
        h = self.horizontalHeader()
        for c in range(len(ITEM_COLS)):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        for c, w in enumerate([84, 210, 0, 64, 52, 74, 34, 74]):
            if w:
                self.setColumnWidth(c, w)
        self.verticalHeader().setDefaultSectionSize(30)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.procs: list[tuple[int, str]] = []
        self.office_prices: dict[int, int] = {}
        self.base_prices: dict[int, int | None] = {}
        self.proc_taxable: dict[int, bool] = {}
        self.office_tax_exempt = False
        self.read_only = False

    def set_catalog(self, office_id: int | None):
        with session_scope() as s:
            procs = pricing.list_procedures(s)
            self.procs = [(p.id, p.name) for p in procs]
            self.base_prices = {p.id: p.base_price_cents for p in procs}
            self.proc_taxable = {p.id: p.taxable for p in procs}
            self.office_prices = pricing.office_price_map(s, office_id) if office_id else {}
            from ..db.models import Office
            o = s.get(Office, office_id) if office_id else None
            self.office_tax_exempt = bool(o and o.tax_exempt)

    def price_for(self, proc_id: int | None) -> int | None:
        if proc_id is None:
            return None
        if proc_id in self.office_prices:
            return self.office_prices[proc_id]
        return self.base_prices.get(proc_id)

    def add_line(self, item: dict | None = None):
        item = item or {}
        r = self.rowCount()
        self.insertRow(r)
        arch = QComboBox()
        arch.addItems(ARCH_CHOICES)
        arch.setCurrentText(item.get("arch", self.page.default_arch.currentText()))
        proc = QComboBox()
        proc.setEditable(True)
        proc.setInsertPolicy(QComboBox.NoInsert)
        proc.completer().setCompletionMode(QCompleter.PopupCompletion)
        proc.completer().setFilterMode(Qt.MatchContains)
        proc.addItem("(free-text description)", None)
        for pid, name in self.procs:
            p = self.price_for(pid)
            proc.addItem(f"{name}" + (f"  -  {fmt(p)}" if p is not None else "  -  (no price)"), pid)
        if item.get("procedure_id") is not None and proc.findData(item["procedure_id"]) < 0:
            proc.addItem(item.get("procedure_name", "(inactive procedure)"), item["procedure_id"])
        desc = QLineEdit(item.get("description", ""))
        tooth = QLineEdit(item.get("tooth_numbers", ""))
        qty = QSpinBox()
        qty.setRange(1, 999)
        qty.setValue(int(item.get("quantity", 1)))
        price = QLineEdit(f"{from_cents(item['unit_price_cents']):.2f}" if "unit_price_cents" in item else "")
        price.setAlignment(Qt.AlignRight)
        tax = QCheckBox()
        tax.setChecked(bool(item.get("taxable", False)))
        total = QLabel()
        total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        for c, w in enumerate([arch, proc, desc, tooth, qty, price, tax, total]):
            self.setCellWidget(r, c, w)
        idx = proc.findData(item.get("procedure_id"))
        proc.setCurrentIndex(idx if idx >= 0 else 0)
        # signals
        proc.currentIndexChanged.connect(lambda _i, p=proc: self._proc_changed(p))
        arch.currentTextChanged.connect(lambda t, a=arch: self._arch_changed(a, t))
        qty.valueChanged.connect(self.page.update_totals)
        price.textChanged.connect(self.page.update_totals)
        tax.toggled.connect(self.page.update_totals)
        if not item:
            proc.setFocus()
        for w in (arch, proc, desc, tooth, qty, price, tax):
            w.setEnabled(not self.read_only)
        self.page.update_totals()

    def _row_of(self, widget) -> int:
        for r in range(self.rowCount()):
            for c in range(self.columnCount()):
                if self.cellWidget(r, c) is widget:
                    return r
        return -1

    def _proc_changed(self, combo: QComboBox):
        r = self._row_of(combo)
        pid = combo.currentData()
        price = self.price_for(pid)
        self.cellWidget(r, 5).setText(f"{from_cents(price):.2f}" if price is not None else "")
        tax = self.proc_taxable.get(pid, False) and not self.office_tax_exempt
        self.cellWidget(r, 6).setChecked(bool(tax))
        self.page.update_totals()

    def _arch_changed(self, combo: QComboBox, text: str):
        r = self._row_of(combo)
        qty: QSpinBox = self.cellWidget(r, 4)
        if text == "Both" and qty.value() == 1:
            qty.setValue(2)
        elif text in ("Upper", "Lower") and qty.value() == 2:
            qty.setValue(1)

    def remove_selected(self):
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True)
        if not rows and self.rowCount():
            rows = [self.rowCount() - 1]
        for r in rows:
            self.removeRow(r)
        self.page.update_totals()

    def items(self) -> list[ItemInput]:
        out = []
        for r in range(self.rowCount()):
            proc: QComboBox = self.cellWidget(r, 1)
            pid = proc.currentData()
            if pid is not None and proc.currentText() != proc.itemText(proc.currentIndex()):
                j = proc.findText(proc.currentText(), Qt.MatchContains)
                pid = proc.itemData(j) if j >= 0 else None
            desc = self.cellWidget(r, 2).text().strip()
            price_txt = self.cellWidget(r, 5).text().strip()
            if pid is None and not desc and not price_txt:
                continue  # ignore completely empty lines
            out.append(ItemInput(procedure_id=pid,
                                 arch=self.cellWidget(r, 0).currentText(), description=desc,
                                 tooth_numbers=self.cellWidget(r, 3).text().strip(),
                                 quantity=self.cellWidget(r, 4).value(),
                                 unit_price=price_txt if price_txt else None,
                                 taxable=self.cellWidget(r, 6).isChecked()))
        return out

    def line_total(self, r: int) -> int | None:
        try:
            return to_cents(self.cellWidget(r, 5).text()) * self.cellWidget(r, 4).value()
        except ValueError:
            return None


class CaseEntryPage(QWidget):
    title = "Monthly Case Entry"

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.case_id: int | None = None
        self.pending_files: list[str] = []
        self.locked = False
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Monthly Case Entry"))
        lay.addWidget(hint("Choose the dental office and billing month, then enter each lab slip. Records can be "
                           "saved as Draft (incomplete) and approved for billing later. Shortcuts: Ctrl+N new "
                           "record, Ctrl+L add line, Ctrl+S save draft, Ctrl+Enter approve & next."))
        self.office = OfficeCombo()
        self.month = MonthPicker()
        self.office.currentIndexChanged.connect(self.load_batch)
        self.month.changed.connect(self.load_batch)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this batch (patient, case #, procedure)...")
        self.search.textChanged.connect(self.load_batch)
        self.summary = QLabel()
        self.summary.setObjectName("Section")
        lay.addLayout(row(QLabel("Dental Office"), self.office, 12, QLabel("Billing Month"), self.month, 12,
                          self.search))
        lay.addWidget(self.summary)

        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)
        # ---- batch list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = make_table(["Date", "Patient", "Case #", "Procedures", "Total", "Status"], stretch_col=3,
                                multi=True)
        self.table.itemSelectionChanged.connect(self._row_selected)
        ll.addWidget(self.table)
        ll.addLayout(row(button("+ New Record", self.new_record, primary=True),
                         button("Approve Selected", lambda: self.set_status("Approved"),
                                tooltip="Mark selected records ready for invoicing"),
                         button("Back to Draft", lambda: self.set_status("Draft")),
                         button("Delete", self.delete_selected)))
        split.addWidget(left)

        # ---- record form
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.banner = QLabel()
        self.banner.setObjectName("Warn")
        self.banner.setWordWrap(True)
        self.banner.hide()
        rl.addWidget(self.banner)
        g = QGroupBox("Patient record")
        form = QFormLayout(g)
        self.patient = QLineEdit()
        self.patient.setPlaceholderText("Patient name (required)")
        self.case_no = QLineEdit()
        self.case_no.setPlaceholderText("optional")
        self.date = DateEdit(None)
        self.due = DateEdit(None)
        self.default_arch = QComboBox()
        self.default_arch.addItems(ARCH_CHOICES)
        form.addRow("Patient Name *", self.patient)
        form.addRow(QLabel("Case Number"), row(self.case_no, QLabel("  Date"), self.date, QLabel("  Due Date"),
                                               self.due, QLabel("  Default arch"), self.default_arch))
        rl.addWidget(g)
        self.grid = ItemsGrid(self)
        rl.addWidget(QLabel("Procedures (one line per procedure; use free text for anything not in the list)"))
        rl.addWidget(self.grid, 1)
        self.total_label = QLabel()
        self.total_label.setObjectName("Section")
        self.btn_add = button("+ Add Line", lambda: self.grid.add_line())
        self.btn_rem = button("Remove Line", self.grid.remove_selected)
        rl.addLayout(row(self.btn_add, self.btn_rem, None, self.total_label))
        bottom = QHBoxLayout()
        ng = QGroupBox("Internal notes (not printed on invoices)")
        nl = QVBoxLayout(ng)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(70)
        nl.addWidget(self.notes)
        ag = QGroupBox("Original lab slip attachments")
        al = QVBoxLayout(ag)
        self.attachments = QListWidget()
        self.attachments.setFixedHeight(70)
        self.attachments.itemDoubleClicked.connect(self._open_attachment)
        al.addWidget(self.attachments)
        self.btn_attach = button("Attach image/PDF...", self.attach)
        self.btn_unattach = button("Remove", self.remove_attachment)
        al.addLayout(row(self.btn_attach, button("Open", lambda: self._open_attachment(self.attachments.currentItem())),
                         self.btn_unattach))
        self.tech = QLabel()
        self.tech.setObjectName("Hint")
        self.tech.setWordWrap(True)
        bottom.addWidget(ng, 1)
        bottom.addWidget(ag, 1)
        rl.addLayout(bottom)
        rl.addWidget(self.tech)
        self.btn_draft = button("Save Draft", lambda: self.save("Draft"))
        self.btn_approve = button("Save && Approve for Billing", lambda: self.save("Approved", next_record=True),
                                  primary=True)
        rl.addLayout(row(None, self.btn_draft, self.btn_approve, stretch=False))
        split.addWidget(right)
        split.setSizes([450, 900])

        QShortcut(QKeySequence("Ctrl+N"), self, self.new_record)
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.grid.add_line())
        QShortcut(QKeySequence("Ctrl+S"), self, lambda: self.save("Draft"))
        QShortcut(QKeySequence("Ctrl+Return"), self, lambda: self.save("Approved", next_record=True))
        QShortcut(QKeySequence("Ctrl+Enter"), self, lambda: self.save("Approved", next_record=True))
        self._loaded_office = None

    # ------------------------------------------------------------ batch
    def refresh(self):
        self.office.reload()
        self.load_batch()

    def offices_changed(self):
        self.office.reload()

    def open_batch(self, office_id: int, year: int, month: int, case_id: int | None = None):
        self.office.set_office(office_id)
        self.month.set_value(year, month)
        self.load_batch()
        if case_id:
            self.load_record(case_id)

    def load_batch(self):
        oid = self.office.office_id()
        year, month = self.month.value()
        if oid is None:
            fill_table(self.table, [])
            self.summary.setText("Select a dental office to begin.")
            return
        if oid != self._loaded_office:
            self._loaded_office = oid
            self.grid.set_catalog(oid)
            if self.case_id is None:
                self.new_record()
        with session_scope() as s:
            cs = case_svc.list_cases(s, oid, year, month, search=self.search.text())
            rows, ids = [], []
            for c in cs:
                if c.invoice and c.invoice.status == "Finalized":
                    st = f"Invoiced #{c.invoice.invoice_number}"
                elif c.invoice_id:
                    st = "On draft invoice"
                else:
                    st = c.status
                rows.append([c.case_date.strftime("%m/%d") if c.case_date else "", c.patient_name, c.case_number,
                             case_svc.case_label(c), fmt(c.total_cents), st])
                ids.append(c.id)
            sm = case_svc.batch_summary(s, oid, year, month)
        fill_table(self.table, rows, ids, right_cols={4})
        self.summary.setText(f"{sm.count} records  |  {sm.draft} draft  |  {sm.approved} approved, not yet "
                             f"invoiced ({fmt(sm.approved_uninvoiced_cents)})  |  {sm.invoiced} invoiced  |  "
                             f"batch total {fmt(sm.total_cents)}")
        if self.case_id in ids:
            self.table.blockSignals(True)
            self.table.selectRow(ids.index(self.case_id))
            self.table.blockSignals(False)

    def _row_selected(self):
        ids = selected_data(self.table)
        if len(ids) == 1 and ids[0] != self.case_id:
            self.load_record(ids[0])

    # ----------------------------------------------------------- record
    def _set_locked(self, locked: bool, msg: str = ""):
        self.locked = locked
        self.grid.read_only = locked
        for w in (self.patient, self.case_no, self.date, self.due, self.default_arch, self.notes, self.btn_add,
                  self.btn_rem, self.btn_draft, self.btn_approve, self.btn_unattach):
            w.setEnabled(not locked)
        self.banner.setVisible(bool(msg))
        self.banner.setText(msg)

    def new_record(self):
        self.case_id = None
        self.pending_files = []
        self.table.clearSelection()
        self._set_locked(False)
        self.patient.clear()
        self.case_no.clear()
        self.date.set_value(None)
        self.due.set_value(None)
        self.notes.clear()
        self.attachments.clear()
        self.tech.clear()
        self.grid.setRowCount(0)
        self.grid.add_line()
        self.patient.setFocus()
        self.update_totals()

    def load_record(self, case_id: int):
        with session_scope() as s:
            c = case_svc.get_case(s, case_id)
            if c is None:
                return
            locked = case_svc.is_locked(c)
            on_draft = bool(c.invoice_id) and not locked
            inv_no = c.invoice.invoice_number if c.invoice else ""
            data = {"patient": c.patient_name, "case_no": c.case_number, "date": c.case_date, "due": c.due_date,
                    "arch": c.arch, "notes": c.internal_notes, "tech": c.technical_details, "source": c.source,
                    "items": [{"procedure_id": i.procedure_id, "procedure_name": i.procedure_name, "arch": i.arch,
                               "description": i.description, "tooth_numbers": i.tooth_numbers,
                               "quantity": i.quantity, "unit_price_cents": i.unit_price_cents, "taxable": i.taxable}
                              for i in c.items],
                    "atts": [(a.id, a.original_name, a.rel_path) for a in c.attachments]}
        self.case_id = case_id
        self.pending_files = []
        self._set_locked(False)
        self.patient.setText(data["patient"])
        self.case_no.setText(data["case_no"])
        self.date.set_value(data["date"])
        self.due.set_value(data["due"])
        self.default_arch.setCurrentText(data["arch"])
        self.notes.setPlainText(data["notes"])
        self.grid.setRowCount(0)
        for it in data["items"]:
            self.grid.add_line(it)
        if not data["items"]:
            self.grid.add_line()
        self.attachments.clear()
        for aid, name, rel in data["atts"]:
            li = QListWidgetItem(name)
            li.setData(Qt.UserRole, (aid, rel))
            self.attachments.addItem(li)
        tech = ""
        if data["tech"]:
            try:
                t = json.loads(data["tech"])
                parts = [f"{k}: {v}" for k, v in t.get("fields", {}).items()]
                if t.get("checked"):
                    parts.append("checked: " + ", ".join(t["checked"]))
                if t.get("teeth"):
                    parts.append("teeth: " + ", ".join(map(str, t["teeth"])))
                tech = "Lab slip technical details (not billed): " + "; ".join(parts)
            except ValueError:
                pass
        self.tech.setText(tech)
        if locked:
            self._set_locked(True, f"This record is on finalized invoice #{inv_no} and is protected. "
                                   "Corrections are made with a credit note (Monthly Invoices).")
        elif on_draft:
            self.banner.setText("This record is on a draft invoice. Changes are picked up when the draft is "
                                "refreshed or finalized.")
            self.banner.show()
        self.update_totals()

    def update_totals(self):
        total = 0
        bad = False
        for r in range(self.grid.rowCount()):
            t = self.grid.line_total(r)
            lbl = self.grid.cellWidget(r, 7)
            if t is None:
                bad = True
                if lbl:
                    lbl.setText("?")
            else:
                total += t
                if lbl:
                    lbl.setText(fmt(t))
            proc = self.grid.cellWidget(r, 1)
            price = self.grid.cellWidget(r, 5)
            if proc is not None and price is not None:
                lp = self.grid.price_for(proc.currentData())
                try:
                    differs = lp is not None and price.text().strip() != "" and to_cents(price.text()) != lp
                except ValueError:
                    differs = True
                price.setProperty("uncertain", "true" if differs else "false")
                price.setToolTip(f"Office price: {fmt(lp)} - manual adjustment requires admin authorization"
                                 if differs else "")
                price.style().unpolish(price)
                price.style().polish(price)
        self.total_label.setText(f"Record total: {fmt(total)}" + ("  (check prices)" if bad else ""))

    def _input(self, status: str) -> CaseInput:
        y, m = self.month.value()
        return CaseInput(office_id=self.office.office_id() or 0, billing_year=y, billing_month=m,
                         patient_name=self.patient.text(), case_number=self.case_no.text(),
                         case_date=self.date.value(), due_date=self.due.value(),
                         arch=self.default_arch.currentText(), internal_notes=self.notes.toPlainText(),
                         status=status, items=self.grid.items())

    def save(self, status: str, next_record: bool = False):
        if self.locked:
            return
        if self.office.office_id() is None:
            error(self, "Select a dental office first.")
            return
        try:
            data = self._input(status)
        except ValueError as exc:
            error(self, str(exc))
            return
        override = None
        while True:
            try:
                with session_scope() as s:
                    c = case_svc.save_case(s, data, self.case_id, override_by=override)
                    for f in self.pending_files:
                        case_svc.add_attachment(s, c.id, f)
                    cid = c.id
                break
            except PriceOverrideRequired as exc:
                override = authorize_admin(self, str(exc))
                if not override:
                    return
            except (BusinessError, ValueError) as exc:
                error(self, str(exc))
                return
        self.pending_files = []
        self.main.status(f"Saved {data.patient_name} ({status})")
        self.case_id = cid
        self.load_batch()
        if next_record:
            self.new_record()
        else:
            self.load_record(cid)

    def set_status(self, status: str):
        ids = selected_data(self.table)
        if not ids:
            error(self, "Select one or more records in the list.")
            return
        n = run_action(self, lambda s: case_svc.set_status(s, ids, status))
        if n is not None:
            self.main.status(f"{n} record(s) set to {status}")
            self.load_batch()
            if self.case_id:
                self.load_record(self.case_id)

    def delete_selected(self):
        ids = selected_data(self.table)
        if not ids:
            return
        if not confirm(self, f"Delete {len(ids)} selected record(s)? This cannot be undone."):
            return
        def go(s):
            for i in ids:
                case_svc.delete_case(s, i)
            return True
        if run_action(self, go):
            self.new_record()
            self.load_batch()

    # ------------------------------------------------------- attachments
    def attach(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Attach original lab slip", "",
                                                "Images and PDF (*.jpg *.jpeg *.png *.pdf *.tif *.tiff)")
        if not files:
            return
        if self.case_id is None:
            self.pending_files += files
            for f in files:
                li = QListWidgetItem(Path(f).name + "  (saved with the record)")
                li.setData(Qt.UserRole, (None, f))
                self.attachments.addItem(li)
            return
        if run_action(self, lambda s: [case_svc.add_attachment(s, self.case_id, f) for f in files]):
            self.load_record(self.case_id)

    def remove_attachment(self):
        it = self.attachments.currentItem()
        if it is None:
            return
        aid, ref = it.data(Qt.UserRole)
        if aid is None:
            self.pending_files.remove(ref)
            self.attachments.takeItem(self.attachments.row(it))
            return
        if confirm(self, "Remove this attachment from the record? (The stored file is kept.)"):
            if run_action(self, lambda s: case_svc.remove_attachment(s, aid) or True):
                self.load_record(self.case_id)

    def _open_attachment(self, it):
        if it is None:
            return
        aid, ref = it.data(Qt.UserRole)
        open_file(ref if aid is None else case_svc.document_path(ref))

