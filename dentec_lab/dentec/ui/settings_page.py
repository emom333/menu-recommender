"""Backup & Settings: company, tax, backups, users/security, OCR, e-mail, activity log."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel,
                               QLineEdit, QSpinBox, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
                               QInputDialog)

from ..config import get_paths
from ..db import database
from ..db.models import AuditLog, User
from ..money import parse_rate
from ..ocr.engine import TEMPLATE_PATH, tesseract_available
from ..services import auth, backup, pricing
from ..services.common import audit, current_user, get_settings, set_setting
from .widgets import (button, confirm, error, fill_table, first_selected, h1, hint, info, make_table, open_file,
                      row, run_action)


class SettingsPage(QWidget):
    title = "Backup & Settings"

    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Backup & Settings"))
        self.admin_note = QLabel("Settings can only be changed by an administrator. You can make a backup and "
                                 "change your own password.")
        self.admin_note.setObjectName("Warn")
        lay.addWidget(self.admin_note)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.s: dict[str, QWidget] = {}
        self._build_backup()
        self._build_company()
        self._build_tax()
        self._build_security()
        self._build_ocr()
        self._build_email()
        self._build_log()

    # ---------------------------------------------------------- builders
    def _le(self, key, placeholder=""):
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        self.s[key] = w
        return w

    def _cb(self, key, text):
        w = QCheckBox(text)
        self.s[key] = w
        return w

    def _build_backup(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(hint("All data stays on this computer. A backup file contains the database and every stored lab "
                         "slip image. Keep a copy on an external drive (e.g. weekly) in case this computer fails."))
        f = QFormLayout()
        self.backup_dir = self._le("backup_dir", str(get_paths().backups))
        f.addRow("Backup folder", row(self.backup_dir, button("Browse...", self._browse_backup), stretch=False))
        f.addRow(self._cb("auto_backup", "Automatic daily backup (made when the program is running)"))
        keep = QSpinBox()
        keep.setRange(3, 365)
        self.s["backup_keep"] = keep
        f.addRow("Keep automatic backups", keep)
        self.last_backup = QLabel()
        f.addRow("Last backup", self.last_backup)
        l.addLayout(f)
        l.addLayout(row(button("Back Up Now", self.backup_now, primary=True), button("Restore From Backup...",
                        self.restore), button("Export Database (CSV)...", self.export_db),
                        button("Open Backup Folder", lambda: open_file(backup.backup_dir(self._settings()))),
                        button("Save Backup Settings", self.save_settings),
                        button("Change My Password...", self.change_my_password)))
        self.backups = make_table(["Backup file", "Size", "Created"], stretch_col=0)
        l.addWidget(QLabel("Backups in the backup folder:"))
        l.addWidget(self.backups, 1)
        l.addWidget(hint(f"Data folder: {get_paths().home}"))
        self.tabs.addTab(w, "Backup")

    def _build_company(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow("Company name", self._le("company_name"))
        f.addRow("Address line 1", self._le("company_address1"))
        f.addRow("Address line 2", self._le("company_address2"))
        f.addRow("Website", self._le("company_website"))
        f.addRow("Phone", self._le("company_phone"))
        f.addRow("E-mail", self._le("company_email"))
        f.addRow("Invoice terms text", self._le("invoice_terms"))
        f.addRow("Invoice footer text", self._le("invoice_footer"))
        f.addRow(self._cb("invoice_show_dates", "Show the case date (MM/DD) at the start of each invoice line"))
        days = self._le("delivery_days", "Tuesday,Thursday")
        f.addRow("Delivery days", days)
        f.addRow(button("Save", self.save_settings, primary=True))
        self.tabs.addTab(w, "Company && Invoice")

    def _build_tax(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(hint("Tax is not hard-coded. Confirm the correct treatment of your products with your tax "
                         "professional, then set the rate and which procedures are taxable. Tax is charged only on "
                         "taxable lines. Changing these settings never alters finalized invoices."))
        f = QFormLayout()
        f.addRow("Tax rate (%)", self._le("tax_rate", "e.g. 7.975"))
        m = QComboBox()
        m.addItem("Round tax once on the taxable subtotal (recommended)", "invoice")
        m.addItem("Round tax on each taxable line", "line")
        self.s["tax_method"] = m
        f.addRow("Calculation method", m)
        f.addRow("Tax label", self._le("tax_label"))
        l.addLayout(f)
        l.addWidget(QLabel("Taxable procedures (default for new lines; each line can still be changed):"))
        self.tax_table = make_table(["Procedure", "Category", "Taxable"], stretch_col=0)
        l.addWidget(self.tax_table, 1)
        l.addLayout(row(button("Save Tax Settings", self.save_tax, primary=True)))
        self.tabs.addTab(w, "Tax")

    def _build_security(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(hint("Patient information is protected by individual sign-ins, automatic screen lock, an "
                         "activity log, and local-only storage. Also turn on Windows BitLocker (or device "
                         "encryption) and keep Windows updated."))
        f = QFormLayout()
        lock = QSpinBox()
        lock.setRange(1, 240)
        lock.setSuffix(" minutes")
        self.s["idle_lock_minutes"] = lock
        f.addRow("Lock screen after inactivity", lock)
        f.addRow(self._cb("require_admin_price_override",
                          "Staff need administrator authorization to change a price on a record"))
        l.addLayout(f)
        l.addLayout(row(button("Save Security Settings", self.save_settings, primary=True),
                        button("Change My Password...", self.change_my_password)))
        l.addWidget(QLabel("Users"))
        self.users = make_table(["Username", "Full name", "Role", "Active", "Last sign-in"], stretch_col=1)
        l.addWidget(self.users, 1)
        l.addLayout(row(button("Add User...", self.add_user), button("Reset Password...", self.reset_pw),
                        button("Activate / Deactivate", self.toggle_user)))
        self.tabs.addTab(w, "Users && Security")

    def _build_ocr(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(hint("Lab slips are read by Tesseract OCR installed on this computer. No image or patient "
                         "information is sent anywhere. Leave the path empty to detect it automatically."))
        f = QFormLayout()
        f.addRow("Tesseract program", row(self._le("tesseract_path", r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                                          button("Browse...", self._browse_tess), stretch=False))
        self.ocr_status = QLabel()
        self.ocr_status.setWordWrap(True)
        f.addRow("Status", self.ocr_status)
        l.addLayout(f)
        l.addLayout(row(button("Save && Test", self.save_ocr, primary=True)))
        l.addWidget(hint(f"Advanced: field labels and billing suggestions are defined in {TEMPLATE_PATH.name} "
                         f"({TEMPLATE_PATH.parent})."))
        l.addStretch(1)
        self.tabs.addTab(w, "OCR")

    def _build_email(self):
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(hint("Optional: send invoice PDFs from your own e-mail account. Invoices are only e-mailed when "
                      "you click Email. The password is asked for once per session and never stored."))
        f.addRow("SMTP server", self._le("smtp_host", "e.g. smtp.gmail.com"))
        f.addRow("Port", self._le("smtp_port", "587"))
        f.addRow("Username", self._le("smtp_user"))
        f.addRow("From address", self._le("smtp_from"))
        f.addRow(self._cb("smtp_tls", "Use STARTTLS encryption"))
        f.addRow(button("Save", self.save_settings, primary=True))
        self.tabs.addTab(w, "E-mail")

    def _build_log(self):
        w = QWidget()
        l = QVBoxLayout(w)
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("Filter by user, action or details...")
        self.log_filter.textChanged.connect(self.reload_log)
        l.addLayout(row(self.log_filter, button("Refresh", self.reload_log), stretch=False))
        self.log = make_table(["Time", "User", "Action", "Item", "Details"], stretch_col=4)
        l.addWidget(self.log, 1)
        self.tabs.addTab(w, "Activity Log")

    # ------------------------------------------------------------- data
    def _settings(self) -> dict:
        with database.session_scope() as s:
            return get_settings(s)

    def refresh(self):
        st = self._settings()
        for k, w in self.s.items():
            v = st.get(k, "")
            if isinstance(w, QLineEdit):
                w.setText(v)
            elif isinstance(w, QCheckBox):
                w.setChecked(v == "1")
            elif isinstance(w, QSpinBox):
                w.setValue(int(v or w.minimum()))
            elif isinstance(w, QComboBox):
                i = w.findData(v)
                w.setCurrentIndex(max(i, 0))
        lb = st.get("last_backup", "")
        self.last_backup.setText(datetime.fromisoformat(lb).strftime("%m/%d/%Y %I:%M %p") if lb else "never")
        admin = current_user().is_admin
        self.admin_note.setVisible(not admin)
        for i in range(1, self.tabs.count() - 1):
            self.tabs.widget(i).setEnabled(admin)
        self.tabs.setTabEnabled(self.tabs.count() - 1, admin)
        self.reload_backups()
        self.reload_tax()
        self.reload_users()
        ok, msg = tesseract_available(st.get("tesseract_path", ""))
        self.ocr_status.setText(msg)
        if admin:
            self.reload_log()

    def save_settings(self):
        if not current_user().is_admin:
            error(self, "Only an administrator can change settings.")
            return
        def go(s):
            for k, w in self.s.items():
                if isinstance(w, QLineEdit):
                    v = w.text().strip()
                elif isinstance(w, QCheckBox):
                    v = "1" if w.isChecked() else "0"
                elif isinstance(w, QSpinBox):
                    v = str(w.value())
                else:
                    v = w.currentData()
                if k == "tax_rate":
                    v = str(parse_rate(v))
                set_setting(s, k, v)
            audit(s, "update_settings", "settings")
            return True
        try:
            parse_rate(self.s["tax_rate"].text())
        except ValueError as exc:
            error(self, str(exc))
            return
        if run_action(self, go):
            self.main.status("Settings saved")
            self.main.apply_settings()
            self.refresh()

    def reload_backups(self):
        rows = []
        files = backup.list_backups(backup.backup_dir(self._settings()))
        for f in files:
            st = f.stat()
            rows.append([f.name, f"{st.st_size / 1024 / 1024:.1f} MB",
                         datetime.fromtimestamp(st.st_mtime).strftime("%m/%d/%Y %I:%M %p")])
        fill_table(self.backups, rows, [str(f) for f in files])

    def _browse_backup(self):
        d = QFileDialog.getExistingDirectory(self, "Backup folder")
        if d:
            self.backup_dir.setText(d)

    def backup_now(self):
        try:
            path = self.main.make_backup("manual")
        except Exception as exc:  # noqa: BLE001
            error(self, f"Backup failed:\n{exc}")
            return
        self.refresh()
        info(self, f"Backup created:\n{path}")

    def restore(self):
        if not current_user().is_admin:
            error(self, "Only an administrator can restore a backup.")
            return
        sel = first_selected(self.backups)
        path, _ = QFileDialog.getOpenFileName(self, "Choose backup file", sel or str(backup.backup_dir(self._settings())),
                                              "Dentec backup (*.zip)")
        if not path:
            return
        try:
            manifest = backup.verify_backup(path)
        except ValueError as exc:
            error(self, str(exc))
            return
        if not confirm(self, f"Restore this backup?\n\n{path}\nCreated: {manifest.get('created', '?')}\n\n"
                             "ALL current data will be replaced by the backup. A safety backup of the current data "
                             "is made first."):
            return
        try:
            database.close_db()
            safety = backup.restore_backup(path)
        except Exception as exc:  # noqa: BLE001
            database.init_db(get_paths().db_file)
            error(self, f"Restore failed; your current data was not changed.\n{exc}")
            return
        database.init_db(get_paths().db_file)
        with database.session_scope() as s:
            audit(s, "restore_backup", details=f"{path}; safety backup {safety}")
        info(self, f"Backup restored.\n\nSafety copy of the previous data:\n{safety}\n\n"
                   "Please sign in again.")
        self.main.after_restore()

    def export_db(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export database",
                                              str(get_paths().home / f"DentecExport_{datetime.now():%Y%m%d}.zip"),
                                              "Zip (*.zip)")
        if path:
            backup.export_database(path)
            with database.session_scope() as s:
                audit(s, "export_database", details=path)
            info(self, f"Database exported (all tables as CSV plus the SQLite file):\n{path}\n\n"
                       "This file contains patient information - store it securely.")

    # ---- tax
    def reload_tax(self):
        with database.session_scope() as s:
            procs = pricing.list_procedures(s, include_inactive=True)
            data = [(p.id, p.name, p.category, p.taxable) for p in procs]
        self.tax_table.setRowCount(len(data))
        for r, (pid, name, cat, tax) in enumerate(data):
            it = QTableWidgetItem(name)
            it.setData(Qt.UserRole, pid)
            self.tax_table.setItem(r, 0, it)
            self.tax_table.setItem(r, 1, QTableWidgetItem(cat))
            c = QTableWidgetItem("Taxable")
            c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            c.setCheckState(Qt.Checked if tax else Qt.Unchecked)
            self.tax_table.setItem(r, 2, c)

    def save_tax(self):
        self.save_settings()
        def go(s):
            for r in range(self.tax_table.rowCount()):
                pid = self.tax_table.item(r, 0).data(Qt.UserRole)
                pricing.update_procedure(s, pid, taxable=self.tax_table.item(r, 2).checkState() == Qt.Checked)
            return True
        if current_user().is_admin and run_action(self, go):
            self.reload_tax()

    # ---- users
    def reload_users(self):
        with database.session_scope() as s:
            us = s.query(User).order_by(User.username).all()
            rows = [[u.username, u.full_name, u.role, "Yes" if u.active else "No",
                     u.last_login.strftime("%m/%d/%Y %H:%M") if u.last_login else ""] for u in us]
            ids = [u.id for u in us]
        fill_table(self.users, rows, ids)

    def add_user(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Add user")
        f = QFormLayout(dlg)
        un, fn, pw = QLineEdit(), QLineEdit(), QLineEdit()
        pw.setEchoMode(QLineEdit.Password)
        role = QComboBox()
        role.addItems(["staff", "admin"])
        f.addRow("Username", un)
        f.addRow("Full name", fn)
        f.addRow("Temporary password", pw)
        f.addRow("Role", role)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            if run_action(self, lambda s: auth.create_user(s, un.text(), pw.text(), role.currentText(), fn.text(),
                                                           must_change=True) and True):
                self.reload_users()

    def reset_pw(self):
        uid = first_selected(self.users)
        if not uid:
            return
        pw, ok = QInputDialog.getText(self, "Reset password", "New temporary password:", QLineEdit.Password)
        if ok and run_action(self, lambda s: auth.reset_password(s, uid, pw) or True):
            info(self, "Password reset. The user must choose a new password at next sign-in.")

    def toggle_user(self):
        uid = first_selected(self.users)
        if not uid:
            return
        with database.session_scope() as s:
            active = s.get(User, uid).active
        if run_action(self, lambda s: auth.set_user_active(s, uid, not active) or True):
            self.reload_users()

    def change_my_password(self):
        from .login import ChangePasswordDialog
        if current_user().id and ChangePasswordDialog(current_user().id, parent=self).exec():
            info(self, "Password changed.")

    # ---- ocr
    def _browse_tess(self):
        p, _ = QFileDialog.getOpenFileName(self, "Find tesseract.exe", r"C:\Program Files", "tesseract (tesseract*)")
        if p:
            self.s["tesseract_path"].setText(p)

    def save_ocr(self):
        self.save_settings()

    # ---- log
    def reload_log(self):
        t = self.log_filter.text().strip().lower()
        with database.session_scope() as s:
            q = s.query(AuditLog).order_by(AuditLog.id.desc())
            if t:
                like = f"%{t}%"
                q = q.filter(AuditLog.username.ilike(like) | AuditLog.action.ilike(like) | AuditLog.details.ilike(like))
            rows = [[a.timestamp.strftime("%m/%d/%Y %H:%M:%S"), a.username, a.action,
                     f"{a.entity} {a.entity_id or ''}".strip(), a.details] for a in q.limit(1000)]
        fill_table(self.log, rows)
