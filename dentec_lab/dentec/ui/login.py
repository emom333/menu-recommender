"""Login, first-run administrator setup and password change dialogs."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from .. import APP_NAME
from ..db.database import session_scope
from ..services import auth
from ..services.common import BusinessError, set_current_user
from .widgets import error, hint


class LoginDialog(QDialog):
    def __init__(self, parent=None, locked_user: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME + (" - Locked" if locked_user else " - Sign in"))
        self.setMinimumWidth(360)
        self.user_id = None
        lay = QVBoxLayout(self)
        t = QLabel("DENTEC DENTAL LAB")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addWidget(hint("The screen was locked after a period of inactivity. Sign in to continue."
                           if locked_user else "Sign in to continue."))
        form = QFormLayout()
        self.username = QLineEdit(locked_user or "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Sign in")
        bb.accepted.connect(self._login)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        (self.password if locked_user else self.username).setFocus()

    def _login(self):
        with session_scope() as s:
            u = auth.authenticate(s, self.username.text(), self.password.text())
            if u is not None:
                set_current_user(auth.to_current(u))
                self.user_id = u.id
                must_change = u.must_change_password
        if self.user_id is None:
            error(self, "Incorrect username or password.")
            self.password.clear()
            return
        if must_change:
            dlg = ChangePasswordDialog(self.user_id, require_old=False, parent=self,
                                       message="Please choose a new password.")
            if dlg.exec() != QDialog.Accepted:
                self.user_id = None
                return
        self.accept()


class FirstRunDialog(QDialog):
    """Create the first administrator account."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME + " - First-time setup")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        t = QLabel("Welcome to Dentec Lab Manager")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addWidget(hint("Create the administrator account. Administrators can manage users, "
                           "authorize price adjustments, issue credit notes and restore backups."))
        form = QFormLayout()
        self.full = QLineEdit()
        self.username = QLineEdit("admin")
        self.pw1 = QLineEdit()
        self.pw2 = QLineEdit()
        for p in (self.pw1, self.pw2):
            p.setEchoMode(QLineEdit.Password)
        form.addRow("Full name", self.full)
        form.addRow("Username", self.username)
        form.addRow("Password", self.pw1)
        form.addRow("Confirm password", self.pw2)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._create)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _create(self):
        if self.pw1.text() != self.pw2.text():
            error(self, "Passwords do not match.")
            return
        try:
            with session_scope() as s:
                u = auth.create_user(s, self.username.text(), self.pw1.text(), "admin", self.full.text(),
                                     _bootstrap=True)
                set_current_user(auth.to_current(u))
        except BusinessError as exc:
            error(self, str(exc))
            return
        self.accept()


class ChangePasswordDialog(QDialog):
    def __init__(self, user_id: int, require_old: bool = True, parent=None, message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Change password")
        self.user_id = user_id
        self.require_old = require_old
        lay = QVBoxLayout(self)
        if message:
            lay.addWidget(hint(message))
        form = QFormLayout()
        self.old = QLineEdit()
        self.pw1 = QLineEdit()
        self.pw2 = QLineEdit()
        for p in (self.old, self.pw1, self.pw2):
            p.setEchoMode(QLineEdit.Password)
        if require_old:
            form.addRow("Current password", self.old)
        form.addRow("New password", self.pw1)
        form.addRow("Confirm new password", self.pw2)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        if self.pw1.text() != self.pw2.text():
            error(self, "Passwords do not match.")
            return
        try:
            with session_scope() as s:
                auth.change_password(s, self.user_id, self.old.text() if self.require_old else None, self.pw1.text())
        except BusinessError as exc:
            error(self, str(exc))
            return
        self.accept()
