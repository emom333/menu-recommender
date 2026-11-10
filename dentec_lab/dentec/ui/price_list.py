"""Price List: Dentec reference prices and each office's negotiated prices."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..db.database import session_scope
from ..money import fmt, from_cents, to_cents
from ..services import pricing

from .widgets import (OfficeCombo, WARN_BG, button, cell, confirm, error, fill_table, h1, hint, info, make_table,
                      row, run_action)

BASE = "__base__"


class BulkDialog(QDialog):
    def __init__(self, procs: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bulk price change")
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Increase or decrease prices. Use a negative number to decrease. "
                           "Existing records and invoices keep the prices they were charged."))
        form = QFormLayout()
        self.r_pct = QRadioButton("Percentage (%)")
        self.r_amt = QRadioButton("Dollar amount ($)")
        self.r_pct.setChecked(True)
        self.value = QLineEdit()
        self.value.setPlaceholderText("e.g. 5 or -10")
        self.round = QComboBox()
        self.round.addItems(["No rounding", "0.50", "1.00", "5.00"])
        self.round.setCurrentIndex(2)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("e.g. 2027 price increase")
        form.addRow(row(self.r_pct, self.r_amt))
        form.addRow("Change by", self.value)
        form.addRow("Round to nearest", self.round)
        form.addRow("Reason", self.reason)
        lay.addLayout(form)
        lay.addWidget(QLabel("Apply to (none selected = all procedures with a price):"))
        self.list = QListWidget()
        for pid, name in procs:
            li = QListWidgetItem(name)
            li.setData(Qt.UserRole, pid)
            li.setFlags(li.flags() | Qt.ItemIsUserCheckable)
            li.setCheckState(Qt.Unchecked)
            self.list.addItem(li)
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def params(self) -> dict:
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
               if self.list.item(i).checkState() == Qt.Checked]
        v = self.value.text().strip()
        return {"percent": v if self.r_pct.isChecked() else None, "amount": v if self.r_amt.isChecked() else None,
                "procedure_ids": ids or None, "reason": self.reason.text(),
                "round_to": None if self.round.currentIndex() == 0 else self.round.currentText()}


class ProcedureDialog(QDialog):
    def __init__(self, parent=None, proc=None):
        super().__init__(parent)
        self.setWindowTitle("Procedure" if proc else "New custom procedure")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(proc.name if proc else "")
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(["Removable Partial & Denture", "Retainer, Orthodontic & Mouth Guard", "All Ceramic",
                                "PFM", "Implant", "Other"])
        self.category.setCurrentText(proc.category if proc else "Other")
        self.price = QLineEdit("" if (proc is None or proc.base_price_cents is None)
                               else f"{from_cents(proc.base_price_cents):.2f}")
        self.taxable = QCheckBox("Taxable by default")
        self.taxable.setChecked(bool(proc and proc.taxable))
        self.active = QCheckBox("Active (available for new records)")
        self.active.setChecked(proc.active if proc else True)
        form.addRow("Procedure name", self.name)
        form.addRow("Category", self.category)
        if proc is None:
            form.addRow("Dentec reference price", self.price)
        form.addRow(self.taxable)
        if proc is not None:
            form.addRow(self.active)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class HistoryDialog(QDialog):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Price change history")
        self.resize(820, 480)
        lay = QVBoxLayout(self)
        t = make_table(["Date", "Procedure", "Old Price", "New Price", "Changed By", "Reason"], stretch_col=5)
        fill_table(t, rows, right_cols={2, 3})
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class PriceListPage(QWidget):
    title = "Price List"

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.loading = False
        self.dirty: dict[int, str] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Price List"))
        lay.addWidget(hint("Dentec reference prices are a starting point only. Each dental office has its own "
                           "negotiated prices. Price changes apply to new records only; existing records and "
                           "finalized invoices never change. Double-click a procedure name to edit it."))
        self.target = QComboBox()
        self.target.setMinimumWidth(320)
        self.target.currentIndexChanged.connect(self.reload)
        self.show_inactive = QCheckBox("Show inactive procedures")
        self.show_inactive.toggled.connect(self.reload)
        lay.addLayout(row(QLabel("Price list for"), self.target, 12, self.show_inactive))
        self.table = make_table(["Procedure", "Category", "Price", "Dentec Reference", "Taxable", "Active"],
                                stretch_col=0)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._cell_double)
        lay.addWidget(self.table, 1)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Reason for change (optional, kept in price history)")
        self.btn_save = button("Save Price Changes", self.save_changes, primary=True)
        lay.addLayout(row(self.reason, self.btn_save, stretch=False))
        lay.addLayout(row(button("Copy From Office...", self.copy_from), button("Bulk Increase/Decrease...", self.bulk),
                          button("Import CSV/Excel...", self.import_prices), button("Export...", self.export_prices),
                          button("Price History", self.history), 20,
                          button("+ New Custom Procedure", self.new_procedure)))
        self._fill_targets()

    def refresh(self):
        self._fill_targets()

    def offices_changed(self):
        self._fill_targets()

    def _fill_targets(self):
        cur = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItem("Dentec reference prices (base list)", BASE)
        combo = OfficeCombo(include_inactive=True)
        for i in range(combo.count()):
            self.target.addItem(combo.itemText(i), combo.itemData(i))
        combo.deleteLater()
        i = self.target.findData(cur)
        self.target.setCurrentIndex(i if i >= 0 else 0)
        self.target.blockSignals(False)
        self.reload()

    def office_id(self):
        d = self.target.currentData()
        return None if d == BASE else d

    def select_office(self, office_id: int):
        i = self.target.findData(office_id)
        if i >= 0:
            self.target.setCurrentIndex(i)

    def reload(self):
        if self.dirty and not confirm(self, "Discard unsaved price changes?"):
            return
        self.dirty = {}
        oid = self.office_id()
        with session_scope() as s:
            procs = pricing.list_procedures(s, include_inactive=self.show_inactive.isChecked())
            prices = pricing.office_price_map(s, oid) if oid else {}
            data = [(p.id, p.name, p.category, (prices.get(p.id) if oid else p.base_price_cents), p.base_price_cents,
                     p.taxable, p.active) for p in procs]
        self.loading = True
        self.table.setRowCount(len(data))
        for r, (pid, name, cat, price, base, tax, active) in enumerate(data):
            for c, (val, editable, right) in enumerate([
                    (name, False, False), (cat, False, False),
                    ("" if price is None else f"{from_cents(price):.2f}", True, True),
                    ("" if base is None else fmt(base), False, True),
                    ("Yes" if tax else "No", False, False), ("Yes" if active else "No", False, False)]):
                it = cell(val, pid if c == 0 else None, right)
                if not editable:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.loading = False
        self.btn_save.setEnabled(False)

    def _item_changed(self, it: QTableWidgetItem):
        if self.loading or it.column() != 2:
            return
        pid = self.table.item(it.row(), 0).data(Qt.UserRole)
        self.dirty[pid] = it.text().strip()
        it.setBackground(WARN_BG)
        self.btn_save.setEnabled(True)

    def save_changes(self):
        if not self.dirty:
            return
        oid = self.office_id()
        for pid, v in self.dirty.items():
            try:
                if v:
                    to_cents(v)
            except ValueError as exc:
                error(self, str(exc))
                return
        reason = self.reason.text()
        def go(s):
            for pid, v in self.dirty.items():
                if oid:
                    pricing.set_office_price(s, oid, pid, v or None, reason)
                else:
                    pricing.set_base_price(s, pid, v or None, reason)
            return len(self.dirty)
        n = run_action(self, go)
        if n is not None:
            self.dirty = {}
            self.reason.clear()
            self.reload()
            self.main.status(f"{n} price(s) saved")

    def _cell_double(self, r, c):
        if c == 2:
            return
        pid = self.table.item(r, 0).data(Qt.UserRole)
        with session_scope() as s:
            p = s.get(pricing.Procedure, pid)
            dlg = ProcedureDialog(self, p)
        if dlg.exec() != QDialog.Accepted:
            return
        if run_action(self, lambda s: pricing.update_procedure(
                s, pid, name=dlg.name.text(), category=dlg.category.currentText(),
                taxable=dlg.taxable.isChecked(), active=dlg.active.isChecked()) and True):
            self.reload()

    def new_procedure(self):
        dlg = ProcedureDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        oid = self.office_id()
        def go(s):
            p = pricing.create_procedure(s, dlg.name.text(), dlg.category.currentText(), dlg.price.text() or None,
                                         dlg.taxable.isChecked())
            return p.id
        if run_action(self, go):
            self.reload()
            if oid:
                info(self, "Procedure created. Enter this office's price in the Price column and save.")

    def copy_from(self):
        oid = self.office_id()
        if not oid:
            error(self, "Select a dental office (not the reference list) to copy prices into.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Copy price list")
        lay = QVBoxLayout(dlg)
        src = OfficeCombo(include_inactive=True)
        over = QCheckBox("Overwrite prices this office already has")
        over.setChecked(True)
        lay.addWidget(QLabel(f"Copy prices into: {self.target.currentText()}"))
        lay.addLayout(row(QLabel("From office"), src))
        lay.addWidget(over)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        n = run_action(self, lambda s: pricing.copy_price_list(s, src.office_id(), oid, over.isChecked()))
        if n is not None:
            info(self, f"{n} prices copied.")
            self.reload()

    def bulk(self):
        with session_scope() as s:
            procs = [(p.id, p.name) for p in pricing.list_procedures(s)]
        dlg = BulkDialog(procs, self)
        if dlg.exec() != QDialog.Accepted:
            return
        prm = dlg.params()
        oid = self.office_id()
        if not confirm(self, f"Apply this change to {self.target.currentText()}?", default_no=False):
            return
        n = run_action(self, lambda s: pricing.bulk_adjust(s, oid, **prm))
        if n is not None:
            info(self, f"{n} prices changed.")
            self.reload()

    def import_prices(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import price list", "", "Price lists (*.csv *.xlsx)")
        if not path:
            return
        oid = self.office_id()
        res = run_action(self, lambda s: pricing.import_price_list(s, oid, path))
        if res:
            msg = f"Updated: {res['updated']}\nUnchanged: {res['unchanged']}\nNew procedures: {res['created']}"
            if res["skipped"]:
                msg += "\n\nSkipped:\n" + "\n".join(res["skipped"][:20])
            info(self, msg, "Import complete")
            self.reload()

    def export_prices(self):
        name = self.target.currentText().split("  (")[0].replace("/", "-")
        path, _ = QFileDialog.getSaveFileName(self, "Export price list", f"Prices - {name}.xlsx",
                                              "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        oid = self.office_id()
        if run_action(self, lambda s: pricing.export_price_list(s, oid, path)):
            self.main.status(f"Exported to {path}")

    def history(self):
        oid = self.office_id()
        with session_scope() as s:
            hs = pricing.price_history(s, oid, base_only=oid is None)
            rows = [[h.changed_at.strftime("%m/%d/%Y %H:%M"), h.procedure.name,
                     "" if h.old_price_cents is None else fmt(h.old_price_cents),
                     "" if h.new_price_cents is None else fmt(h.new_price_cents), h.changed_by, h.reason] for h in hs]
        HistoryDialog(rows, self).exec()



