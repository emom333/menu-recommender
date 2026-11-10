"""Lab Slip Scanner: import slips, review OCR beside the original, approve."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QProgressDialog, QPushButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from ..config import ARCH_CHOICES
from ..db.database import session_scope
from ..money import fmt
from ..ocr.engine import SUPPORTED_EXT, load_template, match_office, suggest_billing, tesseract_available
from ..services import cases as case_svc, ocr_import, offices as off_svc, pricing
from ..services.cases import CaseInput, PriceOverrideRequired
from ..services.common import BusinessError, get_setting
from .case_entry import ItemsGrid
from .widgets import (MonthPicker, OfficeCombo, authorize_admin, button, confirm, error, h1, hint, info,
                      row, run_action)


class _Signals(QObject):
    done = Signal(object)
    progress = Signal(int, str)


class ImportJob(QRunnable):
    """Runs storage + OCR for a list of files off the UI thread."""

    def __init__(self, files: list[str], engine):
        super().__init__()
        self.files = files
        self.engine = engine
        self.signals = _Signals()

    def run(self):
        results, errors = [], []
        for i, f in enumerate(self.files):
            self.signals.progress.emit(i, f)
            try:
                results += ocr_import.prepare_file(f, self.engine)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f}: {exc}")
        self.signals.done.emit((results, errors))


class ImageView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.label = QLabel("No lab slip selected")
        self.label.setAlignment(Qt.AlignCenter)
        self.setWidget(self.label)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.pix: QPixmap | None = None
        self.zoom = 0.0  # 0 = fit width

    def set_image(self, path: str | None):
        self.pix = QPixmap(path) if path else None
        self.zoom = 0.0
        self._apply()

    def _apply(self):
        if not self.pix or self.pix.isNull():
            self.label.setText("Image not available")
            self.label.adjustSize()
            return
        w = self.viewport().width() - 4 if self.zoom == 0 else int(self.pix.width() * self.zoom)
        self.label.setPixmap(self.pix.scaledToWidth(max(w, 100), Qt.SmoothTransformation))
        self.label.adjustSize()

    def zoom_by(self, f: float):
        if not self.pix:
            return
        cur = self.zoom or (self.viewport().width() - 4) / max(self.pix.width(), 1)
        self.zoom = min(max(cur * f, 0.1), 3.0)
        self._apply()

    def fit(self):
        self.zoom = 0.0
        self._apply()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.zoom == 0:
            self._apply()


class ScannerPage(QWidget):
    title = "Lab Slip Scanner"

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.doc_id: int | None = None
        self.result = None
        self.template = load_template()
        self.pool = QThreadPool.globalInstance()
        lay = QVBoxLayout(self)
        lay.addWidget(h1("Lab Slip Scanner"))
        self.status = QLabel()
        self.status.setObjectName("Hint")
        lay.addWidget(hint("Import scanned lab slips (JPG, PNG or PDF; several at once). Text and check marks are "
                           "read on this computer only. Yellow values are uncertain - compare them with the original "
                           "image and correct them. Nothing is saved until you approve it."))
        lay.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)

        # queue
        q = QWidget()
        ql = QVBoxLayout(q)
        ql.setContentsMargins(0, 0, 0, 0)
        self.filter = QComboBox()
        self.filter.addItems(["Pending", "Approved", "Rejected", "All"])
        self.filter.currentIndexChanged.connect(self.reload)
        ql.addLayout(row(QLabel("Show"), self.filter))
        self.queue = QListWidget()
        self.queue.currentItemChanged.connect(lambda cur, _p: cur and self.load_doc(cur.data(Qt.UserRole)))
        ql.addWidget(self.queue)
        ql.addWidget(button("Import Lab Slips...", self.import_files, primary=True))
        ql.addLayout(row(button("Re-run OCR", self.rerun), button("Reject", self.reject)))
        split.addWidget(q)

        # image
        img = QWidget()
        il = QVBoxLayout(img)
        il.setContentsMargins(0, 0, 0, 0)
        self.view = ImageView()
        il.addLayout(row(button("Zoom +", lambda: self.view.zoom_by(1.25)),
                         button("Zoom -", lambda: self.view.zoom_by(0.8)), button("Fit", self.view.fit)))
        il.addWidget(self.view, 1)
        split.addWidget(img)

        # review form
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        form_w = QWidget()
        self.form = QVBoxLayout(form_w)
        sc.setWidget(form_w)
        split.addWidget(sc)
        split.setSizes([180, 480, 700])

        g = QGroupBox("Dental office and billing month")
        f = QFormLayout(g)
        self.office = OfficeCombo()
        self.match = QLabel()
        self.match.setObjectName("Hint")
        self.month = MonthPicker()
        f.addRow("Bill to office *", self.office)
        f.addRow("", self.match)
        f.addRow("Billing month", self.month)
        self.form.addWidget(g)

        self.fields: dict[str, QLineEdit] = {}
        groups: dict[str, QFormLayout] = {}
        for fd in self.template["text_fields"]:
            grp = fd["group"]
            if grp not in groups:
                gb = QGroupBox(grp)
                groups[grp] = QFormLayout(gb)
                self.form.addWidget(gb)
            w = QLineEdit()
            w.textEdited.connect(lambda _t, w=w: self._clear_flag(w))
            self.fields[fd["key"]] = w
            groups[grp].addRow(fd["label"], w)
        self.case_no = QLineEdit()
        groups["Patient Information"].addRow("Case Number", self.case_no)

        self.checks: dict[str, QCheckBox] = {}
        cgroups: dict[str, QGridLayout] = {}
        counts: dict[str, int] = {}
        for c in self.template["checkboxes"]:
            grp = c["group"]
            if grp not in cgroups:
                gb = QGroupBox(f"{grp} (check marks)")
                cgroups[grp] = QGridLayout(gb)
                counts[grp] = 0
                self.form.addWidget(gb)
            cb = QCheckBox(c["label"])
            cb.toggled.connect(lambda _v, cb=cb: self._clear_flag(cb))
            self.checks[c["key"]] = cb
            n = counts[grp]
            cgroups[grp].addWidget(cb, n // 3, n % 3)
            counts[grp] += 1

        tg = QGroupBox("Tooth chart (click to toggle)")
        tl = QGridLayout(tg)
        self.teeth: dict[int, QPushButton] = {}
        tl.addWidget(QLabel("Upper"), 0, 0)
        tl.addWidget(QLabel("Lower"), 1, 0)
        for i in range(16):
            for r, n in ((0, i + 1), (1, 32 - i)):
                b = QPushButton(str(n))
                b.setCheckable(True)
                b.setFixedWidth(28)
                b.setStyleSheet("QPushButton:checked { background: #1f2933; color: white; font-weight: bold; }")
                self.teeth[n] = b
                tl.addWidget(b, r, i + 1)
        self.teeth_note = QLabel()
        self.teeth_note.setObjectName("Hint")
        self.teeth_note.setWordWrap(True)
        tl.addWidget(self.teeth_note, 2, 0, 1, 17)
        self.form.addWidget(tg)
        self.signature = QCheckBox("Doctor signature present")
        self.form.addWidget(self.signature)

        bg = QGroupBox("Billing lines for the invoice (suggested from the check marks - review!)")
        bl = QVBoxLayout(bg)
        self.default_arch = QComboBox()
        self.default_arch.addItems(ARCH_CHOICES)
        self.grid = ItemsGrid(self)
        self.grid.setMinimumHeight(170)
        self.grid.setColumnWidth(1, 190)
        self.total_label = QLabel()
        bl.addWidget(self.grid)
        bl.addLayout(row(button("+ Line", lambda: self.grid.add_line()), button("Remove Line", self.grid.remove_selected),
                         button("Rebuild Suggestions", self.rebuild_suggestions), None, self.total_label))
        self.form.addWidget(bg)
        self.office.currentIndexChanged.connect(self._office_changed)

        self.verified = QCheckBox("I have verified the flagged (yellow) values against the original lab slip")
        self.save_as = QComboBox()
        self.save_as.addItems(["Approved for billing", "Draft"])
        self.btn_approve = button("Approve && Save Record", self.approve, primary=True)
        self.form.addWidget(self.verified)
        self.form.addLayout(row(QLabel("Save record as"), self.save_as, None, self.btn_approve))
        self.form.addStretch(1)
        self._enable_form(False)

    # ----------------------------------------------------------------
    def update_totals(self):
        total = sum(self.grid.line_total(r) or 0 for r in range(self.grid.rowCount()))
        for r in range(self.grid.rowCount()):
            t = self.grid.line_total(r)
            if self.grid.cellWidget(r, 7):
                self.grid.cellWidget(r, 7).setText(fmt(t) if t is not None else "?")
        self.total_label.setText(f"Total {fmt(total)}")

    def refresh(self):
        self.office.reload()
        with session_scope() as s:
            ok, msg = tesseract_available(get_setting(s, "tesseract_path", ""))
        self.ocr_ok = ok
        self.status.setText(("OCR engine: " + msg) if ok else ("OCR not available - " + msg +
                            " You can still import slips and type the data while looking at the image."))
        self.reload()

    def offices_changed(self):
        self.office.reload()

    def reload(self):
        st = self.filter.currentText()
        with session_scope() as s:
            docs = ocr_import.list_queue(s, None if st == "All" else st)
            items = []
            for d in docs:
                r = ocr_import.result_of(d)
                patient = r.fields.get("patient_name").value if r.fields.get("patient_name") else ""
                flag = len(r.uncertain_items())
                items.append((d.id, f"{patient or '(no name read)'}\n{d.original_name} p{d.page_number}  "
                                    f"[{d.status}]" + (f"  {flag} to check" if flag and d.status == "Pending" else "")))
        self.queue.blockSignals(True)
        self.queue.clear()
        for did, text in items:
            li = QListWidgetItem(text)
            li.setData(Qt.UserRole, did)
            self.queue.addItem(li)
        self.queue.blockSignals(False)
        if items:
            ids = [i[0] for i in items]
            self.queue.setCurrentRow(ids.index(self.doc_id) if self.doc_id in ids else 0)
        else:
            self.doc_id = None
            self.view.set_image(None)
            self._enable_form(False)

    def _enable_form(self, on: bool):
        self.form.parentWidget().setEnabled(on)

    def _flag(self, w, uncertain: bool):
        w.setProperty("uncertain", "true" if uncertain else "false")
        if isinstance(w, QCheckBox):
            w.setStyleSheet("QCheckBox { background: #fff3cd; }" if uncertain else "")
        w.style().unpolish(w)
        w.style().polish(w)

    def _clear_flag(self, w):
        if w.property("uncertain") == "true":
            self._flag(w, False)

    def _office_changed(self):
        self.grid.set_catalog(self.office.office_id())

    def load_doc(self, doc_id: int):
        with session_scope() as s:
            from ..db.models import OcrDocument
            d = s.get(OcrDocument, doc_id)
            if d is None:
                return
            r = ocr_import.result_of(d)
            path = str(case_svc.document_path(d.rel_path))
            status = d.status
            offices = off_svc.list_offices(s, include_inactive=False)
            best, score = match_office(r, offices)
            best_id = best.id if best else None
            best_name = best.name if best else ""
        self.doc_id = doc_id
        self.result = r
        self.view.set_image(path)
        self._enable_form(status == "Pending")
        for key, w in self.fields.items():
            fr = r.fields.get(key)
            w.setText(fr.value if fr else "")
            self._flag(w, bool(fr and fr.value and fr.uncertain))
        for key, cb in self.checks.items():
            cr = r.checkboxes.get(key)
            cb.blockSignals(True)
            cb.setChecked(bool(cr and cr.checked))
            cb.blockSignals(False)
            self._flag(cb, bool(cr and cr.uncertain))
        for n, b in self.teeth.items():
            b.setChecked(n in r.teeth)
        self.teeth_note.setText(r.teeth_note)
        self.signature.setChecked(r.signature_present)
        self._flag(self.signature, r.signature_uncertain and r.signature_present)
        self.case_no.clear()
        if best_id and score >= 0.75:
            self.office.set_office(best_id)
            self.match.setText(f"Matched '{best_name}' from the slip (confidence {score:.0%}). Please confirm.")
            self._flag(self.office, score < 0.9)
        else:
            self.office.setCurrentIndex(-1)
            self.match.setText("Could not match the office on the slip - choose it manually." +
                               (f" Closest: {best_name} ({score:.0%})." if best_name else ""))
            self._flag(self.office, True)
        dt = case_svc_date(r.fields.get("date").value if r.fields.get("date") else "")
        if dt:
            self.month.set_value(dt.year, dt.month)
        self.grid.set_catalog(self.office.office_id())
        self.rebuild_suggestions()
        self.verified.setChecked(False)
        n_flag = len(r.uncertain_items())
        self.verified.setText(("I have verified the %d flagged (yellow) value(s) against the original lab slip"
                               % n_flag) if n_flag else "I have checked the values against the original lab slip")
        if r.error:
            self.match.setText(self.match.text() + "  OCR: " + r.error)

    def _current_result(self):
        """Apply the user's corrections to a copy of the OCR result."""
        r = self.result
        for key, w in self.fields.items():
            if key in r.fields:
                r.fields[key].value = w.text().strip()
        for key, cb in self.checks.items():
            if key in r.checkboxes:
                r.checkboxes[key].checked = cb.isChecked()
        r.teeth = sorted(n for n, b in self.teeth.items() if b.isChecked())
        r.signature_present = self.signature.isChecked()
        return r

    def rebuild_suggestions(self):
        if self.result is None:
            return
        r = self._current_result()
        sugg = suggest_billing(r, self.template)
        self.grid.setRowCount(0)
        with session_scope() as s:
            for sg in sugg:
                p = pricing.find_procedure(s, sg["procedure"])
                item = {"arch": sg["arch"], "tooth_numbers": sg["tooth_numbers"], "quantity": sg["quantity"]}
                if p is not None:
                    item["procedure_id"] = p.id
                    price = self.grid.price_for(p.id)
                    if price is not None:
                        item["unit_price_cents"] = price
                    item["taxable"] = p.taxable and not self.grid.office_tax_exempt
                else:
                    item["description"] = sg["procedure"]
                self.grid.add_line(item)
        if not sugg:
            self.grid.add_line()
        self.update_totals()

    def import_files(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "Import lab slips", "", f"Lab slips ({exts})")
        if files:
            self.import_paths(files)

    def import_paths(self, files: list[str], wait: bool = False):
        with session_scope() as s:
            engine = ocr_import.make_engine(s)
        if not engine.tesseract_cmd:
            engine = None
        dlg = QProgressDialog("Reading lab slips...", None, 0, len(files), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        job = ImportJob(files, engine)
        job.signals.progress.connect(lambda i, f: dlg.setValue(i))
        job.signals.done.connect(lambda res: self._import_done(res, dlg))
        self._job = job
        self.pool.start(job)
        if wait:
            self.pool.waitForDone()
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

    def _import_done(self, res, dlg):
        dlg.close()
        prepared, errors = res
        docs = run_action(self, lambda s: [d.id for d in ocr_import.save_prepared(s, prepared)])
        self.filter.setCurrentText("Pending")
        if docs:
            self.doc_id = docs[0]
        self.reload()
        msg = f"{len(prepared)} page(s) imported and waiting for review."
        if errors:
            msg += "\n\nProblems:\n" + "\n".join(errors)
            error(self, msg)
        else:
            self.main.status(msg)

    def rerun(self):
        if self.doc_id and run_action(self, lambda s: ocr_import.rerun_ocr(s, self.doc_id) and True):
            self.load_doc(self.doc_id)

    def reject(self):
        if self.doc_id and confirm(self, "Reject this lab slip? It will not create a record (the image is kept)."):
            if run_action(self, lambda s: ocr_import.reject(s, self.doc_id, "rejected in review") or True):
                self.doc_id = None
                self.reload()

    def approve(self):
        if self.doc_id is None:
            return
        oid = self.office.office_id()
        if oid is None:
            error(self, "Choose the dental office to bill.")
            return
        r = self._current_result()
        patient = self.fields["patient_name"].text().strip()
        y, m = self.month.value()
        try:
            items = self.grid.items()
        except ValueError as exc:
            error(self, str(exc))
            return
        data = CaseInput(office_id=oid, billing_year=y, billing_month=m, patient_name=patient,
                         case_number=self.case_no.text(),
                         case_date=case_svc_date(self.fields["date"].text()),
                         due_date=case_svc_date(self.fields["due_date"].text()),
                         internal_notes=self.fields["instructions"].text(),
                         status="Approved" if self.save_as.currentIndex() == 0 else "Draft", items=items)
        tech = ocr_import.technical_summary(r)
        override = None
        while True:
            try:
                with session_scope() as s:
                    ocr_import.approve(s, self.doc_id, data, technical=tech,
                                             confirmed_uncertain=self.verified.isChecked(), override_by=override)
                break
            except PriceOverrideRequired as exc:
                override = authorize_admin(self, str(exc))
                if not override:
                    return
            except (BusinessError, ValueError) as exc:
                error(self, str(exc))
                return
        self.main.status(f"Saved record for {patient}")
        self.main.data_changed()
        self.doc_id = None
        self.reload()
        if self.queue.count() == 0:
            info(self, "All lab slips in the queue have been reviewed.")


def case_svc_date(text: str) -> date | None:
    from ..ocr.engine import parse_date
    return parse_date(text) if text else None

