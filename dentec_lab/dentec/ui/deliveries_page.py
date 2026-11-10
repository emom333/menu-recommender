"""Optional delivery runs (Tuesday / Thursday)."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..config import get_paths
from ..db.database import session_scope
from ..services import deliveries as dsvc
from ..services.common import get_setting
from .widgets import (DateEdit, button, confirm, error, fill_table, first_selected, h1, hint, make_table,
                      open_file, row, run_action, selected_data)


class DeliveriesPage(QWidget):
    title = "Deliveries"

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.delivery_id: int | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Deliveries"))
        lay.addWidget(hint("Optional. Plan Tuesday/Thursday delivery runs, grouped by dental office, and mark them "
                           "completed. Delivery records never affect invoicing."))
        top = QHBoxLayout()
        g1 = QGroupBox("Delivery runs")
        l1 = QVBoxLayout(g1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search previous deliveries (office, patient, driver)...")
        self.search.textChanged.connect(self.reload)
        self.f_status = QComboBox()
        self.f_status.addItems(["All", "Planned", "Completed"])
        self.f_status.currentIndexChanged.connect(self.reload)
        l1.addLayout(row(self.search, self.f_status, stretch=False))
        self.runs = make_table(["Date", "Day", "Status", "Cases", "Offices", "Driver"], stretch_col=4)
        self.runs.itemSelectionChanged.connect(self._run_selected)
        l1.addWidget(self.runs)
        self.new_date = QComboBox()
        self.driver = QLineEdit()
        self.driver.setPlaceholderText("Driver (optional)")
        l1.addLayout(row(QLabel("New run on"), self.new_date, self.driver,
                         button("Create Delivery Run", self.create, primary=True)))
        top.addWidget(g1, 1)

        g2 = QGroupBox("Selected run - grouped by dental office")
        l2 = QVBoxLayout(g2)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Office / Case", "Delivered"])
        self.tree.setColumnWidth(0, 420)
        self.tree.itemChanged.connect(self._item_toggled)
        l2.addWidget(self.tree)
        l2.addLayout(row(button("Mark Run Completed", self.complete, primary=True), button("Reopen", self.reopen),
                         button("Print Delivery List", self.print_list), button("Remove Case", self.remove_item),
                         button("Delete Run", self.delete)))
        top.addWidget(g2, 1)
        lay.addLayout(top, 1)

        g3 = QGroupBox("Cases due (not yet on a delivery run)")
        l3 = QVBoxLayout(g3)
        self.due_from = DateEdit(date.today() - timedelta(days=7), allow_blank=False)
        self.due_to = DateEdit(date.today() + timedelta(days=7), allow_blank=False)
        self.due_from.dateChanged.connect(self.reload_due)
        self.due_to.dateChanged.connect(self.reload_due)
        l3.addLayout(row(QLabel("Due from"), self.due_from, QLabel("to"), self.due_to))
        self.due = make_table(["Due Date", "Dental Office", "Patient", "Procedures", "Status"], stretch_col=3,
                              multi=True)
        l3.addWidget(self.due)
        l3.addLayout(row(button("Add Selected to Selected Run", self.add_selected)))
        lay.addWidget(g3, 1)

    def refresh(self):
        with session_scope() as s:
            days = get_setting(s, "delivery_days", "Tuesday,Thursday")
        self.new_date.clear()
        for d in dsvc.next_delivery_dates(date.today(), days, 6):
            self.new_date.addItem(f"{dsvc.WEEKDAYS[d.weekday()]} {d:%m/%d/%Y}", d)
        self.reload()
        self.reload_due()

    def reload(self):
        st = self.f_status.currentText()
        with session_scope() as s:
            ds = dsvc.list_deliveries(s, search=self.search.text(), status=None if st == "All" else st)
            rows = [[d.delivery_date.strftime("%m/%d/%Y"), dsvc.WEEKDAYS[d.delivery_date.weekday()], d.status,
                     len(d.items), ", ".join(sorted({i.office.name for i in d.items})), d.driver] for d in ds]
            ids = [d.id for d in ds]
        fill_table(self.runs, rows, ids)
        if self.delivery_id in ids:
            self.runs.selectRow(ids.index(self.delivery_id))
        else:
            self.tree.clear()

    def reload_due(self):
        from ..services.cases import case_label
        with session_scope() as s:
            cs = dsvc.cases_due(s, self.due_from.value(), self.due_to.value())
            rows = [[c.due_date.strftime("%m/%d/%Y"), c.office.name, c.patient_name, case_label(c), c.status]
                    for c in cs]
            ids = [c.id for c in cs]
        fill_table(self.due, rows, ids)

    def _run_selected(self):
        did = first_selected(self.runs)
        if did is None:
            return
        self.delivery_id = did
        self.tree.blockSignals(True)
        self.tree.clear()
        with session_scope() as s:
            d = s.get(dsvc.Delivery, did)
            for office, items in dsvc.grouped_by_office(d).items():
                top = QTreeWidgetItem([f"{office}  ({len(items)})", ""])
                self.tree.addTopLevelItem(top)
                for it in items:
                    ch = QTreeWidgetItem([it.description, ""])
                    ch.setData(0, Qt.UserRole, it.id)
                    ch.setFlags(ch.flags() | Qt.ItemIsUserCheckable)
                    ch.setCheckState(1, Qt.Checked if it.delivered else Qt.Unchecked)
                    top.addChild(ch)
                top.setExpanded(True)
        self.tree.blockSignals(False)

    def _item_toggled(self, item, col):
        iid = item.data(0, Qt.UserRole)
        if iid and col == 1:
            run_action(self, lambda s: dsvc.set_item_delivered(s, iid, item.checkState(1) == Qt.Checked) or True)

    def create(self):
        d = self.new_date.currentData()
        ids = selected_data(self.due)
        did = run_action(self, lambda s: dsvc.create_delivery(s, d, ids, self.driver.text()).id)
        if did:
            self.delivery_id = did
            self.reload()
            self.reload_due()

    def add_selected(self):
        if not self.delivery_id:
            error(self, "Select or create a delivery run first.")
            return
        ids = selected_data(self.due)
        if run_action(self, lambda s: [dsvc.add_case(s, self.delivery_id, i) for i in ids]):
            self.reload()
            self.reload_due()

    def remove_item(self):
        it = self.tree.currentItem()
        iid = it.data(0, Qt.UserRole) if it else None
        if iid and run_action(self, lambda s: dsvc.remove_item(s, iid) or True):
            self.reload()
            self.reload_due()

    def complete(self):
        if self.delivery_id and run_action(self, lambda s: dsvc.complete_delivery(s, self.delivery_id) and True):
            self.reload()

    def reopen(self):
        if self.delivery_id and run_action(self, lambda s: dsvc.reopen_delivery(s, self.delivery_id) or True):
            self.reload()

    def delete(self):
        if self.delivery_id and confirm(self, "Delete this delivery run?"):
            if run_action(self, lambda s: dsvc.delete_delivery(s, self.delivery_id) or True):
                self.delivery_id = None
                self.reload()
                self.reload_due()

    def print_list(self):
        if not self.delivery_id:
            return
        path = get_paths().reports / f"Delivery_{self.delivery_id}.pdf"
        if run_action(self, lambda s: dsvc.export_delivery_pdf(s, s.get(dsvc.Delivery, self.delivery_id), path) or True):
            open_file(path)
