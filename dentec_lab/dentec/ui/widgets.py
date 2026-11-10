"""Shared UI helpers."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
                               QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QCompleter)

from ..db.database import session_scope
from ..services.common import BusinessError, current_user
from ..services.invoices import MONTHS

log = logging.getLogger("dentec")
WARN_BG = QColor("#fff3cd")
LOCK_BG = QColor("#eeeeee")


# ---------------------------------------------------------------- messages
def error(parent, text: str, title: str = "Cannot complete") -> None:
    QMessageBox.warning(parent, title, text)


def info(parent, text: str, title: str = "Dentec Lab Manager") -> None:
    QMessageBox.information(parent, title, text)


def confirm(parent, text: str, title: str = "Please confirm", default_no: bool = True) -> bool:
    btn = QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No if default_no else QMessageBox.Yes)
    return btn == QMessageBox.Yes


def run_action(parent, fn, *args, success: str | None = None, **kwargs):
    """Run fn inside a DB transaction, showing business errors to the user.

    fn receives the session as its first argument.  Returns fn's result or None.
    """
    try:
        with session_scope() as s:
            result = fn(s, *args, **kwargs)
        if success:
            info(parent, success)
        return result
    except BusinessError as exc:
        error(parent, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.error("Unexpected error: %s\n%s", exc, traceback.format_exc())
        error(parent, f"An unexpected error occurred:\n{exc}\n\nDetails were written to the log file.", "Error")
    return None


def open_file(path: str | Path) -> None:
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def print_file(parent, path: str | Path) -> None:
    """Send a PDF to the printer via Qt's print dialog (falls back to the PDF viewer)."""
    try:
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        from PySide6.QtGui import QPainter, QPageSize
        from PySide6.QtCore import QSize, QRect

        doc = QPdfDocument(parent)
        doc.load(str(path))
        if doc.pageCount() == 0:
            raise RuntimeError("empty")
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.Letter))
        printer.setColorMode(QPrinter.GrayScale)
        dlg = QPrintDialog(printer, parent)
        if dlg.exec() != QDialog.Accepted:
            return
        painter = QPainter(printer)
        first = True
        for i in range(doc.pageCount()):
            if not first:
                printer.newPage()
            first = False
            target = printer.pageLayout().paintRectPixels(printer.resolution())
            img = doc.render(i, QSize(target.width(), target.height()))
            painter.drawImage(QRect(0, 0, target.width(), target.height()), img)
        painter.end()
    except Exception as exc:  # noqa: BLE001
        log.info("Qt printing unavailable (%s); opening PDF instead", exc)
        if sys.platform.startswith("win"):
            try:
                os.startfile(str(path), "print")  # noqa: S606
                return
            except OSError:
                pass
        open_file(path)


# ------------------------------------------------------------------ inputs
class DateEdit(QDateEdit):
    """Date field that can be left blank (shows a blank when cleared)."""

    def __init__(self, value: date | None = None, allow_blank: bool = True, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("MM/dd/yyyy")
        self._blank_allowed = allow_blank
        self.setMinimumDate(QDate(1999, 12, 31))
        self.setSpecialValueText(" " if allow_blank else "")
        self.set_value(value)

    def set_value(self, value: date | None) -> None:
        if value is None and self._blank_allowed:
            self.setDate(self.minimumDate())
        else:
            v = value or date.today()
            self.setDate(QDate(v.year, v.month, v.day))

    def value(self) -> date | None:
        d = self.date()
        if self._blank_allowed and d == self.minimumDate():
            return None
        return date(d.year(), d.month(), d.day())

    def keyPressEvent(self, e):  # Delete/Backspace on a fully selected field clears it
        if self._blank_allowed and e.key() in (Qt.Key_Delete,) :
            self.setDate(self.minimumDate())
            return
        super().keyPressEvent(e)


class MonthPicker(QWidget):
    changed = Signal()

    def __init__(self, year: int | None = None, month: int | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.month = QComboBox()
        self.month.addItems(MONTHS)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        t = date.today()
        self.month.setCurrentIndex((month or t.month) - 1)
        self.year.setValue(year or t.year)
        lay.addWidget(self.month)
        lay.addWidget(self.year)
        self.month.currentIndexChanged.connect(self.changed)
        self.year.valueChanged.connect(self.changed)

    def value(self) -> tuple[int, int]:
        return self.year.value(), self.month.currentIndex() + 1

    def set_value(self, year: int, month: int) -> None:
        self.blockSignals(True)
        self.year.setValue(year)
        self.month.setCurrentIndex(month - 1)
        self.blockSignals(False)
        self.changed.emit()


class OfficeCombo(QComboBox):
    """Searchable dental office selector."""

    def __init__(self, include_inactive: bool = False, blank: str | None = None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.completer().setFilterMode(Qt.MatchContains)
        self.setMinimumWidth(260)
        self._inactive = include_inactive
        self._blank = blank
        self.reload()

    def reload(self) -> None:
        from ..services.offices import list_offices
        cur = self.office_id()
        self.blockSignals(True)
        self.clear()
        if self._blank is not None:
            self.addItem(self._blank, None)
        with session_scope() as s:
            for o in list_offices(s, include_inactive=self._inactive):
                label = o.name + ("" if o.status == "Active" else f"  ({o.status})")
                self.addItem(label, o.id)
        if cur is not None:
            self.set_office(cur)
        self.blockSignals(False)

    def office_id(self) -> int | None:
        i = self.currentIndex()
        if i < 0:
            return None
        # editable combos can hold typed text that doesn't match an item
        if self.currentText() != self.itemText(i):
            j = self.findText(self.currentText(), Qt.MatchFixedString)
            if j < 0:
                return None
            i = j
        return self.itemData(i)

    def set_office(self, office_id: int | None) -> None:
        i = self.findData(office_id)
        if i >= 0:
            self.setCurrentIndex(i)


# ------------------------------------------------------------------ tables
def make_table(headers: list[str], stretch_col: int | None = None, select_rows: bool = True,
               multi: bool = False) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    if select_rows:
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_col is not None:
        h.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    t.setSortingEnabled(False)
    t.verticalHeader().setDefaultSectionSize(26)
    return t


def cell(text, data=None, align_right: bool = False, bold: bool = False, bg: QColor | None = None) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    if data is not None:
        it.setData(Qt.UserRole, data)
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if bold:
        f = QFont()
        f.setBold(True)
        it.setFont(f)
    if bg is not None:
        it.setBackground(bg)
    return it


def fill_table(t: QTableWidget, rows: list[list], data: list | None = None, right_cols: set[int] = frozenset(),
               bold_last: bool = False) -> None:
    t.setSortingEnabled(False)
    t.setRowCount(0)
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            it = cell(v, data[r] if (data is not None and c == 0) else None, c in right_cols,
                      bold=bold_last and r == len(rows) - 1)
            t.setItem(r, c, it)


def selected_data(t: QTableWidget) -> list:
    rows = sorted({i.row() for i in t.selectedIndexes()})
    out = []
    for r in rows:
        it = t.item(r, 0)
        if it is not None:
            out.append(it.data(Qt.UserRole))
    return out


def first_selected(t: QTableWidget):
    d = selected_data(t)
    return d[0] if d else None


# ------------------------------------------------------------------ layout
def h1(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("PageTitle")
    return lb


def hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Hint")
    lb.setWordWrap(True)
    return lb


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setObjectName("Rule")
    return f


def button(text: str, slot=None, primary: bool = False, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    if slot:
        b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def row(*widgets, stretch: bool = True) -> QHBoxLayout:
    lay = QHBoxLayout()
    for w in widgets:
        if isinstance(w, int):
            lay.addSpacing(w)
        elif w is None:
            lay.addStretch(1)
        elif isinstance(w, (QHBoxLayout, QVBoxLayout)):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    if stretch and None not in widgets:
        lay.addStretch(1)
    return lay


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "-", sub: str = ""):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        self.t = QLabel(title)
        self.t.setObjectName("CardTitle")
        self.v = QLabel(value)
        self.v.setObjectName("CardValue")
        self.s = QLabel(sub)
        self.s.setObjectName("Hint")
        self.s.setWordWrap(True)
        for w in (self.t, self.v, self.s):
            lay.addWidget(w)

    def set(self, value: str, sub: str = "") -> None:
        self.v.setText(value)
        self.s.setText(sub)


# ------------------------------------------------------- admin authorization
class AdminAuthDialog(QDialog):
    """Ask an administrator to type their credentials to authorize an action."""

    def __init__(self, reason: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Administrator authorization")
        self.approved_by: str | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(hint(reason))
        form = QFormLayout()
        self.user = QLineEdit()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        form.addRow("Admin username", self.user)
        form.addRow("Password", self.pw)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        from ..services.auth import verify_admin_credentials
        with session_scope() as s:
            ok = verify_admin_credentials(s, self.user.text(), self.pw.text())
            from ..services.common import audit
            audit(s, "admin_authorization" if ok else "admin_authorization_failed", details=self.user.text())
        if not ok:
            error(self, "Invalid administrator credentials.")
            return
        self.approved_by = self.user.text().strip().lower()
        self.accept()


def authorize_admin(parent, reason: str) -> str | None:
    """Return the approving admin's username (current user if admin)."""
    if current_user().is_admin:
        return current_user().username
    dlg = AdminAuthDialog(reason, parent)
    if dlg.exec() == QDialog.Accepted:
        return dlg.approved_by
    return None
