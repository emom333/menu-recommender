"""Lab slip import queue: store originals, run local OCR, human review/approval."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_paths
from ..db.models import Attachment, OcrDocument
from ..ocr.engine import SUPPORTED_EXT, LabSlipOCR, SlipResult, find_tesseract, load_pages, save_page_png
from . import cases as case_svc
from .common import BusinessError, audit, current_user, get_setting


def import_file(s: Session, path: str | Path, run_ocr: bool = True, engine: LabSlipOCR | None = None) -> list[OcrDocument]:
    """Copy a JPG/PNG/PDF into the document store and queue each page for review."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXT:
        raise BusinessError(f"{path.name}: unsupported file type. Use JPG, PNG or PDF.")
    src_rel = case_svc.store_document(path, subfolder=f"labslips/{datetime.now():%Y/%m}")
    pages = load_pages(case_svc.document_path(src_rel))
    if engine is None and run_ocr:
        engine = LabSlipOCR(tesseract_cmd=find_tesseract(get_setting(s, "tesseract_path", "")))
    docs = []
    for n, img in enumerate(pages, start=1):
        if path.suffix.lower() == ".pdf" or len(pages) > 1:
            rel = f"labslips/{datetime.now():%Y/%m}/{uuid.uuid4().hex[:10]}_{path.stem[:40]}_p{n}.png"
            save_page_png(img, case_svc.document_path(rel))
        else:
            rel = src_rel
        result = engine.process_image(img) if (run_ocr and engine) else SlipResult(error="OCR not run")
        doc = OcrDocument(rel_path=rel, source_rel_path=src_rel, original_name=path.name, page_number=n,
                          status="Pending", extracted_json=result.to_json(), ocr_engine=result.engine)
        s.add(doc)
        s.flush()
        docs.append(doc)
    audit(s, "import_labslip", "ocr_document", docs[0].id if docs else None, f"{path.name}: {len(docs)} page(s)")
    return docs


def rerun_ocr(s: Session, doc_id: int) -> OcrDocument:
    doc = s.get(OcrDocument, doc_id)
    if doc is None:
        raise BusinessError("Document not found.")
    engine = LabSlipOCR(tesseract_cmd=find_tesseract(get_setting(s, "tesseract_path", "")))
    img = load_pages(case_svc.document_path(doc.rel_path))[0]
    r = engine.process_image(img)
    doc.extracted_json = r.to_json()
    doc.ocr_engine = r.engine
    return doc


def list_queue(s: Session, status: str | None = "Pending") -> list[OcrDocument]:
    q = select(OcrDocument).order_by(OcrDocument.imported_at, OcrDocument.id)
    if status:
        q = q.where(OcrDocument.status == status)
    return list(s.scalars(q))


def result_of(doc: OcrDocument) -> SlipResult:
    try:
        return SlipResult.from_json(doc.extracted_json)
    except (ValueError, TypeError, KeyError):
        return SlipResult(error="No OCR data")


def approve(s: Session, doc_id: int, data: case_svc.CaseInput, technical: dict | None = None,
            confirmed_uncertain: bool = False, override_by: str | None = None) -> int:
    """Save the reviewed data as a patient record. Requires explicit confirmation
    when the OCR flagged anything as uncertain."""
    doc = s.get(OcrDocument, doc_id)
    if doc is None:
        raise BusinessError("Document not found.")
    if doc.status != "Pending":
        raise BusinessError("This lab slip was already reviewed.")
    res = result_of(doc)
    if (res.uncertain_items() or res.error) and not confirmed_uncertain:
        raise BusinessError("Some values were flagged as uncertain. Check them against the original image and "
                            "tick 'I have verified the flagged values' before approving.")
    data.source = "OCR"
    if technical is not None:
        data.technical_details = technical
    case = case_svc.save_case(s, data, override_by=override_by)
    s.add(Attachment(case_id=case.id, rel_path=doc.rel_path, original_name=f"{doc.original_name} (page {doc.page_number})"))
    if doc.source_rel_path and doc.source_rel_path != doc.rel_path:
        s.add(Attachment(case_id=case.id, rel_path=doc.source_rel_path, original_name=doc.original_name))
    doc.status = "Approved"
    doc.case_id = case.id
    doc.reviewed_by = current_user().username
    doc.reviewed_at = datetime.now().replace(microsecond=0)
    audit(s, "approve_labslip", "ocr_document", doc.id, f"-> case {case.id} {case.patient_name}")
    return case.id


def reject(s: Session, doc_id: int, reason: str = "") -> None:
    doc = s.get(OcrDocument, doc_id)
    if doc is None:
        raise BusinessError("Document not found.")
    doc.status = "Rejected"
    doc.reviewed_by = current_user().username
    doc.reviewed_at = datetime.now().replace(microsecond=0)
    audit(s, "reject_labslip", "ocr_document", doc.id, reason)


def technical_summary(res: SlipResult) -> dict:
    """Technical (non-billing) lab slip details kept with the case."""
    return {
        "fields": {k: f.value for k, f in res.fields.items() if f.value},
        "checked": sorted(k for k, c in res.checkboxes.items() if c.checked),
        "teeth": res.teeth,
        "signature_present": res.signature_present,
    }


def as_json(d: dict) -> str:
    return json.dumps(d)
