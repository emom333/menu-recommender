"""Reports with CSV/PDF export."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QSpinBox, QVBoxLayout, QWidget

from ..config import get_paths
from ..db.database import session_scope
from ..services import reports as rep
from ..services.common import get_setting
from .widgets import (DateEdit, MonthPicker, OfficeCombo, button, error, fill_table, h1, hint, make_table,
                      open_file, row)


class ReportsPage(QWidget):
    title = "Reports"

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.report: rep.Report | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Reports"))
        lay.addWidget(hint("Revenue figures use finalized invoices (credit notes subtract). Choose a report and its "
                           "options, then Run. Export to CSV (Excel) or PDF."))
        self.kind = QComboBox()
        self.kind.addItems(list(rep.REPORTS))
        self.kind.currentIndexChanged.connect(self._options)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year)
        self.month = MonthPicker()
        self.p_from = MonthPicker(date.today().year, 1)
        self.p_to = MonthPicker()
        t = date.today()
        self.d_from = DateEdit(date(t.year, t.month, 1), allow_blank=False)
        self.d_to = DateEdit(t, allow_blank=False)
        self.office = OfficeCombo(include_inactive=True, blank="All offices")
        self.opt_widgets = {
            "year": [QLabel("Year"), self.year], "month": [QLabel("Month"), self.month],
            "period": [QLabel("From"), self.p_from, QLabel("To"), self.p_to],
            "dates": [QLabel("From"), self.d_from, QLabel("To"), self.d_to, self.office],
            "asof": [QLabel("As of"), self.d_to],
        }
        ws = [QLabel("Report"), self.kind, 12]
        seen = set()
        for group in self.opt_widgets.values():
            for w in group:
                if id(w) not in seen:
                    ws.append(w)
                    seen.add(id(w))
        ws += [12, button("Run", self.run, primary=True)]
        lay.addLayout(row(*ws))
        self.title_lbl = QLabel()
        self.title_lbl.setObjectName("Section")
        lay.addWidget(self.title_lbl)
        self.table = make_table(["-"], stretch_col=None)
        lay.addWidget(self.table, 1)
        lay.addLayout(row(button("Export CSV...", lambda: self.export("csv")),
                          button("Export PDF...", lambda: self.export("pdf"))))
        self._options()

    NEEDS = {"Monthly Revenue": "year", "Revenue by Dental Office": "period", "Revenue by Procedure": "period",
             "Outstanding Balances": "asof", "Payment History": "dates", "Monthly Case Count": "year",
             "Monthly Invoice Summary": "month"}

    def _options(self):
        need = self.NEEDS[self.kind.currentText()]
        visible = {id(w) for w in self.opt_widgets[need]}
        for group in self.opt_widgets.values():
            for w in group:
                w.setVisible(id(w) in visible)

    def refresh(self):
        self.office.reload()

    def offices_changed(self):
        self.office.reload()

    def build(self, s) -> rep.Report:
        k = self.kind.currentText()
        if k == "Monthly Revenue":
            return rep.monthly_revenue(s, self.year.value())
        if k == "Revenue by Dental Office":
            return rep.revenue_by_office(s, self.p_from.value(), self.p_to.value())
        if k == "Revenue by Procedure":
            return rep.revenue_by_procedure(s, self.p_from.value(), self.p_to.value())
        if k == "Outstanding Balances":
            return rep.outstanding_balances(s, self.d_to.value())
        if k == "Payment History":
            return rep.payment_history(s, self.d_from.value(), self.d_to.value(), self.office.office_id())
        if k == "Monthly Case Count":
            return rep.monthly_case_count(s, self.year.value())
        y, m = self.month.value()
        return rep.monthly_invoice_summary(s, y, m)

    def run(self):
        with session_scope() as s:
            self.report = r = self.build(s)
        self.title_lbl.setText(f"{r.title}  -  {r.subtitle}")
        self.table.setColumnCount(len(r.headers))
        self.table.setHorizontalHeaderLabels(r.headers)
        fill_table(self.table, r.rows + ([r.totals] if r.totals else []), right_cols=r.numeric_cols,
                   bold_last=bool(r.totals))

    def export(self, kind: str):
        if self.report is None:
            self.run()
        r = self.report
        default = get_paths().reports / f"{r.title} {r.subtitle}".replace("/", "-").replace(":", "")[:90]
        path, _ = QFileDialog.getSaveFileName(self, "Export report", f"{default}.{kind}",
                                              "CSV (*.csv)" if kind == "csv" else "PDF (*.pdf)")
        if not path:
            return
        try:
            if kind == "csv":
                rep.export_csv(r, path)
            else:
                with session_scope() as s:
                    company = get_setting(s, "company_name", "DENTEC DENTAL LAB")
                rep.export_pdf(r, path, company)
        except OSError as exc:
            error(self, f"Could not save the file:\n{exc}")
            return
        self.main.status(f"Saved {path}")
        open_file(path)
