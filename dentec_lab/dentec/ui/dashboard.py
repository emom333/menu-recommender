"""Dashboard: what needs attention today."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QGridLayout, QGroupBox, QVBoxLayout, QWidget

from ..db.database import session_scope
from ..money import fmt
from ..services import aging, cases as case_svc, deliveries as dsvc, ocr_import, payments as pay_svc
from ..services.common import get_setting
from ..services.invoices import period_label
from .widgets import StatCard, button, fill_table, h1, hint, make_table, row


def prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


class DashboardPage(QWidget):
    title = "Dashboard"

    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Dashboard"))
        self.sub = hint("")
        lay.addWidget(self.sub)
        g = QGridLayout()
        self.c_ar = StatCard("TOTAL OUTSTANDING")
        self.c_90 = StatCard("OVER 90 DAYS")
        self.c_month = StatCard("RECORDS THIS MONTH")
        self.c_unbilled = StatCard("APPROVED, NOT INVOICED")
        self.c_ocr = StatCard("LAB SLIPS TO REVIEW")
        self.c_backup = StatCard("LAST BACKUP")
        for i, c in enumerate([self.c_ar, self.c_90, self.c_month, self.c_unbilled, self.c_ocr, self.c_backup]):
            g.addWidget(c, 0, i)
        lay.addLayout(g)
        lay.addLayout(row(button("Enter Monthly Cases", lambda: self.main.goto("case_entry"), primary=True),
                          button("Scan Lab Slips", lambda: self.main.goto("scanner")),
                          button("Create Monthly Invoices", lambda: self.main.goto("invoices")),
                          button("Record a Payment", lambda: self.main.goto("payments"))))
        grid = QGridLayout()
        g1 = QGroupBox("Waiting to be invoiced")
        l1 = QVBoxLayout(g1)
        self.t_unbilled = make_table(["Period", "Dental Office", "Approved", "Draft", "Amount"], stretch_col=1)
        l1.addWidget(self.t_unbilled)
        g2 = QGroupBox("Balances by office")
        l2 = QVBoxLayout(g2)
        self.t_bal = make_table(["Dental Office", "0-30", "31-60", "61-90", "Over 90", "Total"], stretch_col=0)
        l2.addWidget(self.t_bal)
        g3 = QGroupBox("Recent payments")
        l3 = QVBoxLayout(g3)
        self.t_pay = make_table(["Date", "Dental Office", "Method", "Amount"], stretch_col=1)
        l3.addWidget(self.t_pay)
        g4 = QGroupBox("Next delivery runs")
        l4 = QVBoxLayout(g4)
        self.t_del = make_table(["Date", "Status", "Cases", "Offices"], stretch_col=3)
        l4.addWidget(self.t_del)
        grid.addWidget(g1, 0, 0)
        grid.addWidget(g2, 0, 1)
        grid.addWidget(g3, 1, 0)
        grid.addWidget(g4, 1, 1)
        lay.addLayout(grid, 1)

    def refresh(self):
        t = date.today()
        y, m = t.year, t.month
        py, pm = prev_month(y, m)
        with session_scope() as s:
            rows = aging.all_offices_aging(s, t)
            total = sum(a.total_cents for _, a in rows)
            over90 = sum(a.buckets[3] for _, a in rows)
            bal_rows = [[o.name] + [fmt(v) for v in a.buckets] + [fmt(a.total_cents)] for o, a in rows]
            n_month = case_svc.count_cases(s, y, m)
            unb = []
            amt = 0
            for (yy, mm) in [(py, pm), (y, m)]:
                for r in case_svc.unbilled_by_office(s, yy, mm):
                    unb.append([period_label(yy, mm), r["office"], r["approved"], r["draft"], fmt(r["amount_cents"])])
                    amt += r["amount_cents"]
            pend = len(ocr_import.list_queue(s))
            lb = get_setting(s, "last_backup", "")
            pays = pay_svc.list_payments(s)[:12]
            pay_rows = [[p.payment_date.strftime("%m/%d/%Y"), p.office.name, p.method, fmt(p.amount_cents)]
                        for p in pays]
            dels = dsvc.list_deliveries(s, start=t)
            del_rows = [[d.delivery_date.strftime("%a %m/%d"), d.status, len(d.items),
                         ", ".join(sorted({i.office.name for i in d.items}))] for d in reversed(dels[-8:])]
        self.sub.setText(f"Today is {t:%A, %B %d, %Y}.")
        self.c_ar.set(fmt(total), f"{len(rows)} offices with a balance")
        self.c_90.set(fmt(over90), "follow up on old invoices" if over90 else "nothing overdue")
        self.c_month.set(str(n_month), period_label(y, m))
        self.c_unbilled.set(fmt(amt), f"{period_label(py, pm)} + {period_label(y, m)}")
        self.c_ocr.set(str(pend), "pending review in Lab Slip Scanner")
        from datetime import datetime
        if lb:
            d = datetime.fromisoformat(lb)
            self.c_backup.set(d.strftime("%m/%d %I:%M %p"), "local backup")
        else:
            self.c_backup.set("Never", "make a backup in Backup & Settings")
        fill_table(self.t_unbilled, unb, right_cols={2, 3, 4})
        fill_table(self.t_bal, bal_rows, right_cols={1, 2, 3, 4, 5})
        fill_table(self.t_pay, pay_rows, right_cols={3})
        fill_table(self.t_del, del_rows)
