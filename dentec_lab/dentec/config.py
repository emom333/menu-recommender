"""Application paths and constants.

Data location rules (first match wins):
  1. Environment variable DENTEC_HOME.
  2. A file named ``portable.txt`` next to the executable / main.py:
     data is kept in a ``DentecData`` folder beside the program.
  3. ``<Documents>/DentecLab`` in the current user's profile.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Directory holding bundled read-only resources."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "dentec" / "resources"
    return Path(__file__).resolve().parent / "resources"


def _default_home() -> Path:
    env = os.environ.get("DENTEC_HOME")
    if env:
        return Path(env)
    if (program_dir() / "portable.txt").exists():
        return program_dir() / "DentecData"
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    return docs / "DentecLab"


class Paths:
    """All on-disk locations used by the application."""

    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else _default_home()
        self.data = self.home / "data"
        self.db_file = self.data / "dentec.db"
        self.documents = self.home / "documents"  # original lab slip images
        self.invoices = self.home / "invoices"  # generated invoice PDFs
        self.reports = self.home / "reports"
        self.backups = self.home / "backups"
        self.logs = self.home / "logs"

    def ensure(self) -> "Paths":
        for p in (self.home, self.data, self.documents, self.invoices,
                  self.reports, self.backups, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self


_paths: Paths | None = None


def get_paths() -> Paths:
    global _paths
    if _paths is None:
        _paths = Paths().ensure()
    return _paths


def set_paths(paths: Paths) -> Paths:
    global _paths
    _paths = paths.ensure()
    return _paths


COMPANY_DEFAULTS = {
    "company_name": "DENTEC DENTAL LAB",
    "company_address1": "2962 E Plymouth Way",
    "company_address2": "Fresno, CA 93720",
    "company_website": "www.dentecdentallab.com",
    "company_phone": "",
    "company_email": "",
}

ARCH_CHOICES = ["", "Upper", "Lower", "Both"]
PAYMENT_METHODS = ["Cash", "Check", "Credit Card", "Bank Transfer", "Other"]
