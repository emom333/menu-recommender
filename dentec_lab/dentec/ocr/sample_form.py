"""Generate FICTIONAL lab-slip images for testing and training staff.

The layout follows the field list of the Dentec Dental Laboratory
prescription form.  It is used by the automated tests and by
``scripts/make_sample_labslips.py``.  All names are fictional.
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

W, H = LETTER
HAND = "Courier-Bold"  # stands in for neat hand printing

CHECK_ROWS = [
    ("PROCEDURE", ["Bite Block", "Metal Try-In", "Teeth Try-In", "Custom Shade", "Finish", "Dr. Die Trim"]),
    ("ALLOY / MARGIN", ["High Noble", "Noble", "Non-Precious", "Porcelain Butt Margin", "Metal Margin"]),
    ("DENTURE", ["FUD", "FLD", "PUD", "PLD", "Flexible", "Metal Framework", "Reline", "Repair"]),
    ("RESTORATION", ["PFM", "55% Gold", "2% Gold", "Non-Precious", "Noble", "E.max Press", "Zirconia Layered",
                     "E.max CAD", "Zirconia Full Contour"]),
]
KEY_BY_ROW_LABEL = {
    ("PROCEDURE", "Bite Block"): "bite_block", ("PROCEDURE", "Metal Try-In"): "metal_try_in",
    ("PROCEDURE", "Teeth Try-In"): "teeth_try_in", ("PROCEDURE", "Custom Shade"): "custom_shade",
    ("PROCEDURE", "Finish"): "finish", ("PROCEDURE", "Dr. Die Trim"): "dr_die_trim",
    ("ALLOY / MARGIN", "High Noble"): "high_noble", ("ALLOY / MARGIN", "Noble"): "noble_alloy",
    ("ALLOY / MARGIN", "Non-Precious"): "non_precious_alloy",
    ("ALLOY / MARGIN", "Porcelain Butt Margin"): "porcelain_butt_margin",
    ("ALLOY / MARGIN", "Metal Margin"): "metal_margin",
    ("DENTURE", "FUD"): "fud", ("DENTURE", "FLD"): "fld", ("DENTURE", "PUD"): "pud", ("DENTURE", "PLD"): "pld",
    ("DENTURE", "Flexible"): "flexible", ("DENTURE", "Metal Framework"): "metal_framework",
    ("DENTURE", "Reline"): "reline", ("DENTURE", "Repair"): "repair",
    ("RESTORATION", "PFM"): "pfm", ("RESTORATION", "55% Gold"): "gold_55", ("RESTORATION", "2% Gold"): "gold_2",
    ("RESTORATION", "Non-Precious"): "non_precious", ("RESTORATION", "Noble"): "noble",
    ("RESTORATION", "E.max Press"): "emax_press", ("RESTORATION", "Zirconia Layered"): "zirconia_layered",
    ("RESTORATION", "E.max CAD"): "emax_cad", ("RESTORATION", "Zirconia Full Contour"): "zirconia_full_contour",
}


def _field(c, x, y, label, value, width, fill):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, label)
    lw = c.stringWidth(label, "Helvetica-Bold", 10) + 6
    c.setLineWidth(0.6)
    c.line(x + lw, y - 3, x + width, y - 3)
    if fill and value:
        c.setFont(HAND, 12)
        c.drawString(x + lw + 4, y, value)


def draw_form(values: dict | None = None, path: str | Path | None = None) -> bytes:
    """Draw the form (blank if values is None).  Returns PDF bytes."""
    v = values or {}
    fill = values is not None
    buf = io.BytesIO()
    c = canvas.Canvas(buf if path is None else str(path), pagesize=LETTER)
    c.setTitle("Dentec Dental Laboratory - Lab Slip (sample)")
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(W / 2, H - 50, "DENTEC DENTAL LABORATORY")
    c.setFont("Helvetica", 9)
    c.drawCentredString(W / 2, H - 63, "2962 E Plymouth Way, Fresno, CA 93720   www.dentecdentallab.com")
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W / 2, H - 79, "LABORATORY PRESCRIPTION")
    c.setLineWidth(1.2)
    c.line(40, H - 86, W - 40, H - 86)

    y = H - 112
    _field(c, 40, y, "Doctor:", v.get("doctor"), 290, fill)
    _field(c, 320, y, "Office:", v.get("office"), 570, fill)
    y -= 26
    _field(c, 40, y, "Phone:", v.get("phone"), 220, fill)
    _field(c, 240, y, "Address:", v.get("address"), 570, fill)
    y -= 26
    _field(c, 40, y, "Patient:", v.get("patient_name"), 330, fill)
    _field(c, 350, y, "Sex:", v.get("sex"), 430, fill)
    _field(c, 450, y, "Age:", v.get("age"), 570, fill)
    y -= 26
    _field(c, 40, y, "Date:", v.get("date"), 250, fill)
    _field(c, 300, y, "Due Date:", v.get("due_date"), 570, fill)
    y -= 18
    checked = set(v.get("checked", []))
    for title, labels in CHECK_ROWS:
        y -= 22
        c.setFont("Helvetica-Bold", 9)
        c.drawString(40, y + 14, title)
        x = 40
        for lbl in labels:
            c.setFont("Helvetica", 10)
            tw = c.stringWidth(lbl, "Helvetica", 10)
            if x + 18 + tw > W - 40:
                y -= 20
                x = 40
            c.setLineWidth(0.9)
            c.rect(x, y - 2, 11, 11, stroke=1, fill=0)
            if fill and KEY_BY_ROW_LABEL[(title, lbl)] in checked:
                c.setLineWidth(1.8)
                c.line(x + 1.5, y - 0.5, x + 9.5, y + 7.5)
                c.line(x + 1.5, y + 7.5, x + 9.5, y - 0.5)
            c.drawString(x + 16, y, lbl)
            x += 16 + tw + 22
        y -= 10
    y -= 16
    _field(c, 40, y, "Buccal Margin:", v.get("buccal_margin"), 220, fill)
    _field(c, 240, y, "Shade:", v.get("shade"), 400, fill)
    y -= 24
    _field(c, 40, y, "Metal Design:", v.get("metal_design"), 200, fill)
    _field(c, 210, y, "Pontic Design:", v.get("pontic_design"), 390, fill)
    _field(c, 400, y, "Occlusal Design:", v.get("occlusal_design"), 570, fill)

    # Tooth chart
    y -= 34
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y + 12, "TOOTH CHART")
    teeth = set(v.get("teeth", []))
    step = (W - 120) / 16
    for row, (label, nums) in enumerate([("UPPER", list(range(1, 17))), ("LOWER", list(range(32, 16, -1)))]):
        yy = y - row * 30
        c.setFont("Helvetica-Bold", 9)
        c.drawString(40, yy - 3, label)
        for i, n in enumerate(nums):
            cx = 95 + i * step
            c.setFont("Helvetica", 10)
            c.drawCentredString(cx, yy - 3, str(n))
            if fill and n in teeth:
                c.setLineWidth(1.6)
                c.circle(cx, yy, 10, stroke=1, fill=0)
    y -= 70
    _field(c, 40, y, "Tooth #:", v.get("tooth_numbers"), 250, fill)
    y -= 26
    _field(c, 40, y, "Instructions:", v.get("instructions"), 570, fill)
    y -= 40
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Doctor Signature:")
    c.line(140, y - 3, 360, y - 3)
    if fill and v.get("signed", True):
        c.setLineWidth(1.4)
        p = c.beginPath()
        p.moveTo(150, y)
        for k in range(1, 40):
            p.lineTo(150 + k * 4.5, y + 6 * math.sin(k * 0.9) + 2)
        c.drawPath(p, stroke=1, fill=0)
    _field(c, 380, y, "License #:", v.get("license"), 570, fill)
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(40, 30, "Sample form generated for testing. Fictional data.")
    c.showPage()
    c.save()
    return buf.getvalue() if path is None else Path(path).read_bytes()


def render_scan(values: dict | None, out_path: str | Path, dpi: int = 200, skew_deg: float = 0.6,
                noise: bool = True, seed: int = 1) -> Path:
    """Draw the form and rasterise it like a scanned page (slight skew + noise)."""
    import cv2
    import numpy as np
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf
    pdf = draw_form(values)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    doc.close()
    if skew_deg:
        h, w = img.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), skew_deg, 1.0)
        img = cv2.warpAffine(img, m, (w, h), borderValue=255)
    if noise:
        rng = random.Random(seed)
        np_rng = np.random.default_rng(rng.randint(0, 10_000))
        n = np_rng.normal(0, 8, img.shape)
        img = np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path


SAMPLE_SLIPS = [
    {"doctor": "Dr. Maria Example", "office": "Valley Smiles Dentistry", "phone": "(559) 555-0142",
     "address": "1234 W Shaw Ave, Fresno", "patient_name": "Jordan Sample", "sex": "F", "age": "64",
     "date": "10/06/2026", "due_date": "10/13/2026", "checked": ["bite_block", "fud", "fld"],
     "license": "D 12345", "instructions": "Upper and lower bite blocks"},
    {"doctor": "Dr. Alan Placeholder", "office": "Sunrise Family Dental", "phone": "(559) 555-0177",
     "address": "88 E Herndon Ave, Clovis", "patient_name": "Casey Fictional", "sex": "M", "age": "71",
     "date": "10/08/2026", "due_date": "10/15/2026", "checked": ["teeth_try_in", "fud", "fld", "custom_shade"],
     "shade": "A2", "license": "D 67890"},
    {"doctor": "Dr. Maria Example", "office": "Valley Smiles Dentistry", "phone": "(559) 555-0142",
     "address": "1234 W Shaw Ave, Fresno", "patient_name": "Riley Testcase", "sex": "F", "age": "52",
     "date": "10/09/2026", "due_date": "10/20/2026", "checked": ["zirconia_full_contour", "metal_margin"],
     "teeth": [3, 14], "tooth_numbers": "3, 14", "shade": "B1", "license": "D 12345"},
    {"doctor": "Dr. Alan Placeholder", "office": "Sunrise Family Dental", "phone": "(559) 555-0177",
     "address": "88 E Herndon Ave, Clovis", "patient_name": "Morgan Demo", "sex": "M", "age": "58",
     "date": "10/12/2026", "due_date": "10/22/2026", "checked": ["finish", "pld", "flexible"],
     "license": "D 67890", "instructions": "Lower flexible partial finish"},
]
