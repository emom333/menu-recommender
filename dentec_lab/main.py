"""Dentec Dental Lab Manager - program entry point.

Usage:
    python main.py              start the application
    python main.py --self-test  check the installation without opening windows
    python main.py --self-test --ui   ... and also open every screen once
    python main.py --sample     load the fictional sample database into a
                                separate data folder and start with it
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(log_dir / "dentec.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)


def _report(lines: list[str]) -> None:
    """Print (console builds) and always write dentec_selftest.txt (windowed builds have no console)."""
    text = "\n".join(lines)
    if sys.stdout is not None:
        print(text)
    try:
        Path("dentec_selftest.txt").write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass


def self_test(ui: bool = False) -> int:
    """Headless installation check (used after building the .exe)."""
    import tempfile
    from datetime import date

    from dentec.config import Paths, set_paths
    from dentec.db import database
    from dentec.ocr.engine import tesseract_available
    from dentec.pdf.invoice_pdf import export_invoice
    from dentec.services import backup, invoices, offices, pricing
    from dentec.services.cases import CaseInput, ItemInput, save_case

    lines = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            paths = set_paths(Paths(Path(tmp) / "home"))
            database.init_db(paths.db_file)
            with database.session_scope() as s:
                o = offices.create_office(s, {"name": "Self Test Office"})
                pid = pricing.find_procedure(s, "Bite Block").id
                save_case(s, CaseInput(o.id, 2026, 1, "Test Patient", status="Approved",
                                       items=[ItemInput(pid, arch="Upper"), ItemInput(pid, arch="Lower")]))
                inv = invoices.create_draft(s, o.id, 2026, 1, date(2026, 1, 31))
                invoices.finalize(s, inv.id, "TEST-1")
                pdf = export_invoice(s, inv)
                assert pdf.exists() and inv.total_cents == 7000, "invoice check failed"
            b = backup.create_backup(label="selftest")
            backup.verify_backup(b)
            lines.append("Database, pricing, invoice PDF and backup: OK")
            if ui:
                from PySide6.QtWidgets import QApplication
                from dentec.services import auth
                from dentec.services.common import set_current_user
                from dentec.ui.style import QSS
                app = QApplication.instance() or QApplication(sys.argv[:1])
                app.setStyleSheet(QSS)
                with database.session_scope() as s:
                    set_current_user(auth.to_current(auth.create_user(s, "admin", "selftest", "admin",
                                                                      _bootstrap=True)))
                from dentec.ui.main_window import MainWindow
                win = MainWindow()
                win.show()
                for key in win.keys:
                    win.goto(key)
                    app.processEvents()
                win.backup_timer.stop()
                win.idle.timer.stop()
                win.hide()
                lines.append(f"User interface: OK ({len(win.keys)} screens opened)")
            database.close_db()
        ok, msg = tesseract_available()
        lines.append("OCR: " + (msg if ok else "not available (" + msg + ")"))
        import PySide6
        lines.append(f"Qt: OK (PySide6 {PySide6.__version__})")
        lines.append("SELF-TEST PASSED")
        _report(lines)
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback
        lines.append("SELF-TEST FAILED: " + repr(exc))
        lines.append(traceback.format_exc())
        _report(lines)
        return 1


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test(ui="--ui" in args)

    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from dentec import APP_NAME
    from dentec.config import Paths, get_paths, program_dir, set_paths
    from dentec.db import database
    from dentec.services import auth
    from dentec.ui.style import QSS

    if "--sample" in args:
        import shutil
        sample_home = Path.home() / "Documents" / "DentecLab_SAMPLE"
        src = program_dir() / "sample_data" / "dentec_sample.db"
        paths = set_paths(Paths(sample_home))
        if not paths.db_file.exists() and src.exists():
            shutil.copy2(src, paths.db_file)
    paths = get_paths()
    setup_logging(paths.logs)
    log = logging.getLogger("dentec")
    log.info("Starting; data folder %s", paths.home)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

    def excepthook(exc_type, exc, tb):
        log.error("Unhandled error", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, "Unexpected error", f"{exc}\n\nThe details were written to:\n{paths.logs}")
    sys.excepthook = excepthook

    try:
        database.init_db(paths.db_file)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(None, APP_NAME, f"Could not open the database:\n{paths.db_file}\n\n{exc}")
        return 1

    from dentec.ui.login import FirstRunDialog, LoginDialog
    from dentec.ui.main_window import MainWindow
    with database.session_scope() as s:
        first = not auth.has_users(s)
    dlg = FirstRunDialog() if first else LoginDialog()
    if dlg.exec() != QDialog.Accepted:
        return 0
    win = MainWindow()
    win.showMaximized()
    rc = app.exec()
    database.close_db()
    return rc


if __name__ == "__main__":
    sys.exit(main())
