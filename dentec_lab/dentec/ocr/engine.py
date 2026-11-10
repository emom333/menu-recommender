"""Local lab-slip OCR (OpenCV + Tesseract).  Nothing leaves the computer.

Pipeline for each page:
  1. load (JPG/PNG/TIFF/BMP, or PDF pages rendered at 200 dpi)
  2. grayscale + deskew using the form's long horizontal rules
  3. detect checkbox squares and measure how much ink is inside each
  4. blank the squares out and run Tesseract to get words + confidences
  5. locate the template's labels and read the text to the right of them
  6. read the tooth chart by ink density around each tooth position
  7. detect whether the signature line has ink on it

Every value carries a confidence and an ``uncertain`` flag.  Results are only
suggestions: the user must review and approve them in the Lab Slip Scanner.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

TEMPLATE_PATH = Path(__file__).with_name("labslip_template.json")
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
SUPPORTED_EXT = IMAGE_EXT | {".pdf"}
MIN_CONF = 70  # word confidence below this marks a field uncertain


# ------------------------------------------------------------- tesseract
def find_tesseract(configured: str = "") -> str | None:
    cands = []
    if configured:
        cands.append(configured)
    import sys
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    cands += [str(exe_dir / "tesseract" / "tesseract.exe"), str(exe_dir / "Tesseract-OCR" / "tesseract.exe")]
    if base:
        cands.append(str(base / "tesseract" / "tesseract.exe"))
    cands += [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return shutil.which("tesseract")


def tesseract_available(configured: str = "") -> tuple[bool, str]:
    path = find_tesseract(configured)
    if not path:
        return False, "Tesseract OCR was not found. Install it (see the Installation Guide) or set its path in Settings."
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = path
        ver = pytesseract.get_tesseract_version()
        return True, f"Tesseract {ver} ({path})"
    except Exception as exc:  # noqa: BLE001
        return False, f"Tesseract found at {path} but could not run: {exc}"


# ------------------------------------------------------------------ data
@dataclass
class FieldResult:
    key: str
    label: str
    group: str
    value: str = ""
    confidence: float = 0.0
    uncertain: bool = True
    note: str = ""


@dataclass
class CheckResult:
    key: str
    label: str
    group: str
    checked: bool = False
    fill_ratio: float = 0.0
    uncertain: bool = True
    found: bool = False


@dataclass
class SlipResult:
    fields: dict[str, FieldResult] = field(default_factory=dict)
    checkboxes: dict[str, CheckResult] = field(default_factory=dict)
    teeth: list[int] = field(default_factory=list)
    teeth_uncertain: bool = True
    teeth_note: str = ""
    signature_present: bool = False
    signature_uncertain: bool = True
    skew_angle: float = 0.0
    engine: str = ""
    error: str = ""
    raw_text: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "SlipResult":
        d = json.loads(s)
        r = cls(**{k: v for k, v in d.items() if k not in ("fields", "checkboxes")})
        r.fields = {k: FieldResult(**v) for k, v in d.get("fields", {}).items()}
        r.checkboxes = {k: CheckResult(**v) for k, v in d.get("checkboxes", {}).items()}
        return r

    def uncertain_items(self) -> list[str]:
        out = [f.label for f in self.fields.values() if f.uncertain and f.value]
        out += [c.label for c in self.checkboxes.values() if c.uncertain]
        if self.teeth_uncertain and self.teeth:
            out.append("Tooth chart")
        return out


def load_template(path: Path | None = None) -> dict:
    return json.loads(Path(path or TEMPLATE_PATH).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- images
def load_pages(path: str | Path, dpi: int = 200) -> list[np.ndarray]:
    """Return grayscale page images for an image or PDF file."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ValueError(f"Unsupported file type {ext}. Use JPG, PNG or PDF.")
    if ext == ".pdf":
        try:
            import pymupdf
        except ImportError:  # pragma: no cover
            import fitz as pymupdf
        pages = []
        with pymupdf.open(path) as doc:
            for p in doc:
                pix = p.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
                pages.append(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy())
        return pages
    data = np.fromfile(str(path), dtype=np.uint8)  # handles non-ASCII Windows paths
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Could not read image {path.name}.")
    return [img]


def save_page_png(img: np.ndarray, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("Could not encode page image.")
    buf.tofile(str(dest))
    return dest


def _normalise_size(gray: np.ndarray) -> np.ndarray:
    """Scale so the page is ~1700 px wide (US Letter at 200 dpi)."""
    h, w = gray.shape[:2]
    target = 1700
    if abs(w - target) / target < 0.08:
        return gray
    f = target / w
    return cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)


def estimate_skew(gray: np.ndarray) -> float:
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    lines = cv2.HoughLinesP(inv, 1, np.pi / 1800, threshold=200, minLineLength=gray.shape[1] // 5, maxLineGap=8)
    angles = []
    if lines is not None:
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
            a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(a) < 8:
                angles.append(a)
    return float(statistics.median(angles)) if angles else 0.0


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    angle = estimate_skew(gray)
    if abs(angle) < 0.05:
        return gray, 0.0
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=255), angle


def find_checkboxes(binary_inv: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Square, box-like contours of checkbox size."""
    contours, _ = cv2.findContours(binary_inv, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (18 <= w <= 60 and 18 <= h <= 60) or not (0.75 <= w / h <= 1.33):
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.06 * peri, True)
        if len(approx) != 4:
            continue
        # must look like an outline, not a solid blob / letter
        if not _is_box_outline(binary_inv, x, y, w, h):
            continue
        boxes.append((x, y, w, h))
    # de-duplicate (inner/outer contours of the same box)
    boxes.sort(key=lambda b: (b[1], b[0]))
    out: list[tuple[int, int, int, int]] = []
    for b in boxes:
        if any(abs(b[0] - o[0]) < 8 and abs(b[1] - o[1]) < 8 for o in out):
            continue
        out.append(b)
    return out


def _is_box_outline(b: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """All four sides must be (almost) continuous straight strokes.

    This rejects letters such as D, O or A whose outline is box-sized."""
    t = max(2, min(w, h) // 8)

    def side(strip: np.ndarray, axis: int) -> float:
        # fraction of positions along the side where the strip has ink
        return float((strip.max(axis=axis) > 0).mean()) if strip.size else 0.0
    top = side(b[y:y + t, x + t:x + w - t], 0)
    bottom = side(b[y + h - t:y + h, x + t:x + w - t], 0)
    left = side(b[y + t:y + h - t, x:x + t], 1)
    right = side(b[y + t:y + h - t, x + w - t:x + w], 1)
    return min(top, bottom, left, right) >= 0.85


def box_fill_ratio(binary_inv: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    m = max(3, int(min(w, h) * 0.22))
    inner = binary_inv[y + m:y + h - m, x + m:x + w - m]
    return float(inner.mean() / 255.0) if inner.size else 0.0


# ------------------------------------------------------------ OCR words
@dataclass
class Word:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int
    line: tuple

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    @property
    def right(self):
        return self.x + self.w


def ocr_words(img: np.ndarray, tesseract_cmd: str) -> list[Word]:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    d = pytesseract.image_to_data(img, config="--psm 3",
                                  output_type=pytesseract.Output.DICT)
    words = []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        if not t:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = 0.0
        words.append(Word(t, conf, d["left"][i], d["top"][i], d["width"][i], d["height"][i],
                          (d["block_num"][i], d["par_num"][i], d["line_num"][i])))
    return words


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9%#.]", "", t.lower()).rstrip(".:")


def _same_row(a: Word, b: Word) -> bool:
    return abs(a.cy - b.cy) < max(a.h, b.h) * 0.7


def find_label(words: list[Word], label: str) -> list[tuple[int, int]]:
    """All (start, end) word-index spans matching a (multi-word) label."""
    parts = [_norm(p) for p in label.split() if _norm(p)]
    if not parts:
        return []
    hits = []
    for i in range(len(words) - len(parts) + 1):
        ok = True
        for k, p in enumerate(parts):
            w = _norm(words[i + k].text)
            if k and not _same_row(words[i], words[i + k]):
                ok = False
                break
            if w == p:
                continue
            if len(p) >= 4 and abs(len(w) - len(p)) <= 1 and SequenceMatcher(None, w, p).ratio() >= 0.8:
                continue
            ok = False
            break
        if ok:
            hits.append((i, i + len(parts) - 1))
    return hits


def _reading_order(words: list[Word]) -> list[Word]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.cy):
        if rows and abs(rows[-1][0].cy - w.cy) < w.h * 0.6:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    for r in rows:
        out += sorted(r, key=lambda w: w.x)
    return out


# --------------------------------------------------------------- engine
class LabSlipOCR:
    def __init__(self, tesseract_cmd: str | None = None, template: dict | None = None):
        self.template = template or load_template()
        self.tesseract_cmd = tesseract_cmd or find_tesseract()

    def process_file(self, path: str | Path) -> list[tuple[np.ndarray, SlipResult]]:
        return [(img, self.process_image(img)) for img in load_pages(path)]

    def process_image(self, gray: np.ndarray) -> SlipResult:
        res = SlipResult()
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        gray = _normalise_size(gray)
        gray, angle = deskew(gray)
        res.skew_angle = round(angle, 2)
        binary_inv = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
        boxes = find_checkboxes(binary_inv)
        if not self.tesseract_cmd:
            res.error = "Tesseract OCR is not installed; enter the data manually from the image."
            return res
        clean = gray.copy()
        for (x, y, w, h) in boxes:
            cv2.rectangle(clean, (x - 2, y - 2), (x + w + 2, y + h + 2), 255, -1)
        try:
            words = _reading_order(ocr_words(clean, self.tesseract_cmd))
        except Exception as exc:  # noqa: BLE001
            res.error = f"OCR failed: {exc}"
            return res
        res.engine = "tesseract"
        res.raw_text = " ".join(w.text for w in words)
        spans = self._label_spans(words)
        self._read_text_fields(words, spans, res)
        self._read_checkboxes(words, spans, boxes, binary_inv, res)
        self._read_tooth_chart(words, binary_inv, res)
        self._read_signature(words, spans, binary_inv, res)
        self._post_process(res)
        return res

    # ---- labels
    def _label_spans(self, words: list[Word]) -> dict[str, list[tuple[int, int]]]:
        """Find every label; drop matches that are part of a longer label match."""
        all_labels: dict[str, list[tuple[int, int]]] = {}
        names = set()
        for f in self.template["text_fields"]:
            names.update(f["labels"])
        for c in self.template["checkboxes"]:
            names.add(c["label"])
        names.update(self.template["signature"]["labels"])
        names.update(["Upper", "Lower", "Tooth Chart"])
        for n in names:
            all_labels[n] = find_label(words, n)
        flat = [(s, e, n) for n, sp in all_labels.items() for (s, e) in sp]
        keep: dict[str, list[tuple[int, int]]] = {n: [] for n in names}
        for s, e, n in flat:
            if any(s2 <= s and e <= e2 and (e2 - s2) > (e - s) for s2, e2, _ in flat):
                continue
            keep[n].append((s, e))
        return keep

    def _label_starts(self, spans) -> set[int]:
        return {s for sp in spans.values() for (s, _) in sp}

    def _read_text_fields(self, words, spans, res: SlipResult) -> None:
        starts = self._label_starts(spans)
        for f in self.template["text_fields"]:
            fr = FieldResult(f["key"], f["label"], f["group"])
            best = None
            for lbl in sorted(f["labels"], key=lambda x: -len(x.split())):
                if spans.get(lbl):
                    best = spans[lbl][0]
                    break
            if best is None:
                fr.note = "label not found"
                res.fields[f["key"]] = fr
                continue
            s, e = best
            lab = words[e]
            vals = []
            last_right = lab.right
            for j in range(e + 1, len(words)):
                w = words[j]
                if not _same_row(lab, w) or w.x < lab.x:
                    break
                if j in starts:
                    break
                if w.x - last_right > lab.h * 6 and vals:
                    break
                vals.append(w)
                last_right = w.right
            text = " ".join(w.text for w in vals).strip(" :_|")
            text = re.sub(r"^[:#\-_|]+\s*", "", text).strip()
            fr.value = text
            if vals:
                fr.confidence = round(min(w.conf for w in vals), 1)
                fr.uncertain = fr.confidence < MIN_CONF
            if f.get("kind") == "date" and text:
                d = parse_date(text)
                if d:
                    fr.value = d.strftime("%m/%d/%Y")
                else:
                    fr.uncertain = True
                    fr.note = "could not read date"
            res.fields[f["key"]] = fr

    def _read_checkboxes(self, words, spans, boxes, binary_inv, res: SlipResult) -> None:
        for c in self.template["checkboxes"]:
            cr = CheckResult(c["key"], c["label"], c["group"])
            hits = sorted(spans.get(c["label"], []), key=lambda se: (words[se[0]].cy, words[se[0]].x))
            occ = c.get("occurrence", 1)
            if len(hits) >= occ:
                s, _e = hits[occ - 1]
                w = words[s]
                cands = [b for b in boxes if b[0] + b[2] <= w.x + 4 and w.x - (b[0] + b[2]) < w.h * 3.5
                         and abs((b[1] + b[3] / 2) - w.cy) < w.h * 0.9]
                if cands:
                    box = max(cands, key=lambda b: b[0])
                    ratio = box_fill_ratio(binary_inv, box)
                    cr.found = True
                    cr.fill_ratio = round(ratio, 3)
                    cr.checked = ratio >= 0.12
                    cr.uncertain = 0.05 <= ratio <= 0.2
            res.checkboxes[c["key"]] = cr

    def _read_tooth_chart(self, words, binary_inv, res: SlipResult) -> None:
        rows = {}
        for anchor in ("UPPER", "LOWER"):
            cand = [w for w in words if _norm(w.text) == anchor.lower()]
            # the chart rows are the ones followed by many numbers on the same line
            best = None
            for a in cand:
                nums = [w for w in words if _same_row(a, w) and w.x > a.right and re.fullmatch(r"\d{1,2}", w.text)]
                if best is None or len(nums) > len(best[1]):
                    best = (a, nums)
            if best and len(best[1]) >= 4:
                rows[anchor] = best
        if not rows:
            res.teeth_note = "Tooth chart not found - mark teeth manually."
            return
        # Column x positions: fit x = a + b*index from recognised numbers in either row
        pts = []
        for anchor, (a, nums) in rows.items():
            for w in nums:
                n = int(w.text)
                idx = (n - 1) if anchor == "UPPER" and 1 <= n <= 16 else (32 - n) if anchor == "LOWER" and 17 <= n <= 32 else None
                if idx is not None:
                    pts.append((idx, w.cx, w.h))
        if len(pts) < 4:
            res.teeth_note = "Tooth numbers not readable - mark teeth manually."
            return
        idxs = np.array([p[0] for p in pts], float)
        xs = np.array([p[1] for p in pts], float)
        b, a = np.polyfit(idxs, xs, 1)
        h = int(statistics.median(p[2] for p in pts))
        row_y = {}
        for anchor in ("UPPER", "LOWER"):
            if anchor in rows:
                row_y[anchor] = rows[anchor][0].cy
        if "UPPER" not in row_y:
            row_y["UPPER"] = row_y["LOWER"] - 3.3 * h
        if "LOWER" not in row_y:
            row_y["LOWER"] = row_y["UPPER"] + 3.3 * h
        scores = {}
        r = int(h * 1.45)
        for anchor in ("UPPER", "LOWER"):
            cy = int(row_y[anchor])
            for idx in range(16):
                cx = int(a + b * idx)
                n = idx + 1 if anchor == "UPPER" else 32 - idx
                y0, y1 = max(cy - r, 0), min(cy + r, binary_inv.shape[0])
                x0, x1 = max(cx - r, 0), min(cx + r, binary_inv.shape[1])
                patch = binary_inv[y0:y1, x0:x1].astype(np.float32) / 255
                inner = binary_inv[max(cy - h // 2 - 2, 0):cy + h // 2 + 2,
                                   max(cx - int(h * 0.9), 0):cx + int(h * 0.9)].astype(np.float32) / 255
                ring = (patch.sum() - inner.sum()) / max(patch.size - inner.size, 1)
                scores[n] = ring
        vals = sorted(scores.values())
        base = statistics.median(vals)
        marked = sorted(n for n, v in scores.items() if v > base + 0.05)
        borderline = [n for n, v in scores.items() if base + 0.03 < v <= base + 0.05]
        res.teeth = marked
        res.teeth_uncertain = True  # always ask a person to verify the chart
        res.teeth_note = ("Please verify the tooth chart." +
                          (f" Unclear: {', '.join(map(str, sorted(borderline)))}." if borderline else ""))

    def _read_signature(self, words, spans, binary_inv, res: SlipResult) -> None:
        hit = None
        for lbl in sorted(self.template["signature"]["labels"], key=lambda x: -len(x.split())):
            if spans.get(lbl):
                hit = spans[lbl][0]
                break
        if hit is None:
            return
        w = words[hit[1]]
        x0 = int(w.right + 10)
        x1 = min(binary_inv.shape[1], x0 + int(w.h * 16))
        y0, y1 = int(w.y - w.h * 0.8), int(w.y + w.h * 0.6)  # stay above the signature rule
        region = binary_inv[max(y0, 0):y1, x0:x1]
        ratio = float(region.mean() / 255) if region.size else 0
        res.signature_present = ratio > 0.02
        res.signature_uncertain = 0.008 < ratio < 0.03

    def _post_process(self, res: SlipResult) -> None:
        # Teeth written in the "Tooth #" field complement the chart
        written = res.fields.get("tooth_numbers")
        if written and written.value:
            nums = [int(n) for n in re.findall(r"\d{1,2}", written.value) if 1 <= int(n) <= 32]
            if nums and set(nums) != set(res.teeth):
                res.teeth_note += f" Written tooth numbers: {', '.join(map(str, nums))}."
                if not res.teeth:
                    res.teeth = sorted(set(nums))
        phone = res.fields.get("phone")
        if phone and phone.value:
            digits = re.sub(r"\D", "", phone.value)
            if len(digits) == 10:
                phone.value = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
            else:
                phone.uncertain = True


# ---------------------------------------------------------------- helpers
def parse_date(text: str) -> date | None:
    t = re.sub(r"[^\d/\-.]", "", text.replace("O", "0").replace("o", "0").replace("l", "1"))
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def match_office(res: SlipResult, offices: list) -> tuple[object | None, float]:
    """Best matching office for the slip's office/doctor/phone text."""
    def norm(t):
        t = (t or "").lower()
        t = re.sub(r"\b(dr|dds|dmd|inc|llc|the)\b\.?", " ", t)
        return re.sub(r"[^a-z0-9]", "", t)
    off_txt = norm(res.fields.get("office", FieldResult("", "", "")).value)
    doc_txt = norm(res.fields.get("doctor", FieldResult("", "", "")).value)
    phone = re.sub(r"\D", "", res.fields.get("phone", FieldResult("", "", "")).value)
    best, best_score = None, 0.0
    for o in offices:
        score = 0.0
        if off_txt:
            score = max(score, SequenceMatcher(None, off_txt, norm(o.name)).ratio())
        if doc_txt and o.dentist_name:
            score = max(score, SequenceMatcher(None, doc_txt, norm(o.dentist_name)).ratio() * 0.95)
        if len(phone) >= 10 and re.sub(r"\D", "", o.phone or "")[-10:] == phone[-10:]:
            score = max(score, 0.97)
        if score > best_score:
            best, best_score = o, score
    return best, round(best_score, 3)


def suggest_billing(res: SlipResult, template: dict | None = None) -> list[dict]:
    """Suggested invoice lines (procedure name, arch, tooth numbers, qty)."""
    template = template or load_template()
    on = {k for k, c in res.checkboxes.items() if c.checked}
    upper = [t for t in res.teeth if 1 <= t <= 16]
    lower = [t for t in res.teeth if 17 <= t <= 32]
    out = []
    for rule in template["billing_rules"]:
        if not all(k in on for k in rule.get("when_all", [])):
            continue
        if rule.get("when_any") and not any(k in on for k in rule["when_any"]):
            continue
        if any(k in on for k in rule.get("when_none", [])):
            continue
        if rule.get("per_tooth"):
            teeth = res.teeth
            out.append({"procedure": rule["procedure"], "arch": "Upper" if teeth and not lower else
                        "Lower" if teeth and not upper else "", "tooth_numbers": ", ".join(map(str, teeth)),
                        "quantity": max(1, len(teeth))})
            continue
        arches = []
        if rule.get("arch_from"):
            arches = [a for k, a in rule["arch_from"].items() if k in on]
        else:
            if on & {"fud", "pud"}:
                arches.append("Upper")
            if on & {"fld", "pld"}:
                arches.append("Lower")
        for a in (arches or [""]):
            out.append({"procedure": rule["procedure"], "arch": a, "tooth_numbers": "", "quantity": 1})
    return out
