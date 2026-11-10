"""Main window with sidebar navigation, idle lock and automatic backups."""
from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..db.database import session_scope
from ..services import backup
from ..services.common import audit, current_user, get_setting, get_settings, set_setting
from .case_entry import CaseEntryPage
from .dashboard import DashboardPage
from .deliveries_page import DeliveriesPage
from .invoices_page import InvoicesPage
from .offices_page import OfficesPage
from .payments_page import PaymentsPage
from .price_list import PriceListPage
from .reports_page import ReportsPage
from .scanner import ScannerPage
from .settings_page import SettingsPage

log = logging.getLogger("dentec")

PAGES = [
    ("dashboard", "Dashboard", DashboardPage),
    ("offices", "Dental Offices", OfficesPage),
    ("case_entry", "Monthly Case Entry", CaseEntryPage),
    ("scanner", "Lab Slip Scanner", ScannerPage),
    ("prices", "Price List", PriceListPage),
    ("invoices", "Monthly Invoices", InvoicesPage),
    ("payments", "Payments", PaymentsPage),
    ("reports", "Reports", ReportsPage),
    ("deliveries", "Deliveries", DeliveriesPage),
    ("settings", "Backup & Settings", SettingsPage),
]


class IdleWatcher(QObject):
    """Locks the application after a period without keyboard/mouse input."""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window = window
        self.last = datetime.now()
        self.minutes = 15
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(20_000)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.Wheel):
            self.last = datetime.now()
        return False

    def check(self):
        if (datetime.now() - self.last).total_seconds() >= self.minutes * 60 and not self.window.locked:
            self.window.lock()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  -  Dentec Dental Lab")
        self.resize(1440, 900)
        self.locked = False
        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QWidget()
        side.setObjectName("Sidebar")
        side.setStyleSheet("background: #1f2933;")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        brand = QLabel("DENTEC\nDENTAL LAB")
        brand.setObjectName("Brand")
        sub = QLabel("Lab Management")
        sub.setObjectName("BrandSub")
        sl.addWidget(brand)
        sl.addWidget(sub)
        self.nav = QListWidget()
        self.nav.setObjectName("Sidebar")
        self.nav.setFocusPolicy(Qt.NoFocus)
        sl.addWidget(self.nav, 1)
        self.user_box = QLabel()
        self.user_box.setObjectName("UserBox")
        self.user_box.setWordWrap(True)
        sl.addWidget(self.user_box)
        h.addWidget(side)

        self.stack = QStackedWidget()
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(16, 12, 16, 8)
        wl.addWidget(self.stack)
        h.addWidget(wrap, 1)

        self.pages: dict[str, QWidget] = {}
        self.keys: list[str] = []
        for key, label, cls in PAGES:
            page = cls(self)
            self.pages[key] = page
            self.keys.append(key)
            self.stack.addWidget(page)
            self.nav.addItem(QListWidgetItem(label))
        self.nav.currentRowChanged.connect(self._nav_changed)
        self.statusBar().showMessage(f"Version {__version__}  -  all data is stored on this computer")

        self.idle = IdleWatcher(self)
        QApplication.instance().installEventFilter(self.idle)
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(self.auto_backup)
        self.backup_timer.start(30 * 60 * 1000)
        self.apply_settings()
        self.update_user_box()
        self.nav.setCurrentRow(0)
        QTimer.singleShot(1500, self.auto_backup)

    # ------------------------------------------------------------- nav
    def _nav_changed(self, idx: int):
        if idx < 0:
            return
        self.stack.setCurrentIndex(idx)
        page = self.stack.widget(idx)
        try:
            page.refresh()
        except Exception:  # noqa: BLE001
            log.exception("refresh failed for %s", self.keys[idx])

    def goto(self, key: str):
        self.nav.setCurrentRow(self.keys.index(key))

    def current_page(self):
        return self.stack.currentWidget()

    def status(self, msg: str):
        self.statusBar().showMessage(msg, 8000)

    def offices_changed(self):
        for p in self.pages.values():
            if hasattr(p, "offices_changed") and p is not self.current_page():
                p.offices_changed()

    def data_changed(self):
        pass  # pages refresh themselves when shown

    def update_user_box(self):
        u = current_user()
        self.user_box.setText(f"Signed in: {u.username}\nRole: {u.role}")

    def apply_settings(self):
        with session_scope() as s:
            try:
                self.idle.minutes = max(1, int(get_setting(s, "idle_lock_minutes", "15") or 15))
            except ValueError:
                self.idle.minutes = 15

    # ---------------------------------------------------------- security
    def lock(self):
        from .login import LoginDialog
        self.locked = True
        with session_scope() as s:
            audit(s, "screen_locked")
        user = current_user().username
        self.centralWidget().setVisible(False)
        while True:
            dlg = LoginDialog(self, locked_user=user)
            if dlg.exec() == QDialog.Accepted:
                break
            # Cancel on the lock screen exits the program (data is already saved).
            QApplication.instance().quit()
            return
        self.centralWidget().setVisible(True)
        self.locked = False
        self.idle.last = datetime.now()
        self.update_user_box()
        self.current_page().refresh()

    # ----------------------------------------------------------- backups
    def make_backup(self, label: str = "manual"):
        with session_scope() as s:
            st = get_settings(s)
        path = backup.create_backup(backup.backup_dir(st), label=label)
        with session_scope() as s:
            set_setting(s, "last_backup", datetime.now().replace(microsecond=0).isoformat())
            audit(s, "backup", details=str(path))
        return path

    def auto_backup(self):
        try:
            with session_scope() as s:
                st = get_settings(s)
            if st.get("auto_backup", "1") != "1" or not backup.auto_backup_due(st.get("last_backup", "")):
                return
            path = self.make_backup("auto")
            backup.prune_backups(backup.backup_dir(st), int(st.get("backup_keep", "30") or 30))
            self.status(f"Automatic backup saved: {path.name}")
        except Exception:  # noqa: BLE001
            log.exception("automatic backup failed")
            self.status("Automatic backup FAILED - see Backup & Settings")

    def after_restore(self):
        for p in self.pages.values():
            if hasattr(p, "offices_changed"):
                p.offices_changed()
        self.lock()
        self.goto("dashboard")

    def closeEvent(self, e):
        try:
            with session_scope() as s:
                audit(s, "logout")
            self.auto_backup()
        finally:
            super().closeEvent(e)
