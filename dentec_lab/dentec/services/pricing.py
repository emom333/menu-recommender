"""Procedure catalog, Dentec base prices and per-office negotiated prices.

Price changes never touch existing cases or invoices: every case line stores
the actual unit price charged at the time it was entered.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Office, OfficePrice, PriceHistory, Procedure
from ..money import from_cents, to_cents
from .common import BusinessError, audit, current_user


# --------------------------------------------------------------- procedures
def list_procedures(s: Session, include_inactive: bool = False) -> list[Procedure]:
    q = select(Procedure).order_by(Procedure.sort_order, func.lower(Procedure.name))
    if not include_inactive:
        q = q.where(Procedure.active.is_(True))
    return list(s.scalars(q))


def find_procedure(s: Session, name: str) -> Procedure | None:
    return s.scalar(select(Procedure).where(func.lower(Procedure.name) == (name or "").strip().lower()))


def create_procedure(s: Session, name: str, category: str = "Other", base_price=None,
                     taxable: bool = False) -> Procedure:
    name = (name or "").strip()
    if not name:
        raise BusinessError("Procedure name is required.")
    if find_procedure(s, name):
        raise BusinessError(f"Procedure '{name}' already exists.")
    max_sort = s.scalar(select(func.max(Procedure.sort_order))) or 0
    p = Procedure(name=name, category=(category or "Other").strip(), taxable=bool(taxable),
                  base_price_cents=None if base_price in (None, "") else to_cents(base_price),
                  is_custom=True, active=True, sort_order=max_sort + 10)
    s.add(p)
    s.flush()
    audit(s, "create_procedure", "procedure", p.id, name)
    return p


def update_procedure(s: Session, proc_id: int, *, name: str | None = None, category: str | None = None,
                     taxable: bool | None = None, active: bool | None = None) -> Procedure:
    p = s.get(Procedure, proc_id)
    if p is None:
        raise BusinessError("Procedure not found.")
    if name is not None and name.strip() and name.strip() != p.name:
        other = find_procedure(s, name)
        if other and other.id != p.id:
            raise BusinessError(f"Procedure '{name}' already exists.")
        audit(s, "rename_procedure", "procedure", p.id, f"{p.name} -> {name.strip()}")
        p.name = name.strip()
    if category is not None:
        p.category = category.strip() or "Other"
    if taxable is not None and taxable != p.taxable:
        p.taxable = taxable
        audit(s, "procedure_taxable", "procedure", p.id, f"{p.name}: taxable={taxable}")
    if active is not None and active != p.active:
        p.active = active
        audit(s, "procedure_active", "procedure", p.id, f"{p.name}: active={active}")
    return p


def set_base_price(s: Session, proc_id: int, price, reason: str = "") -> None:
    p = s.get(Procedure, proc_id)
    if p is None:
        raise BusinessError("Procedure not found.")
    new = None if price in (None, "") else to_cents(price)
    if new is not None and new < 0:
        raise BusinessError("Price cannot be negative.")
    if new == p.base_price_cents:
        return
    s.add(PriceHistory(office_id=None, procedure_id=p.id, old_price_cents=p.base_price_cents,
                       new_price_cents=new, changed_by=current_user().username, reason=reason))
    p.base_price_cents = new


# ------------------------------------------------------------- office prices
@dataclass
class PriceLookup:
    cents: int | None
    source: str  # "office" | "base" | "none"


def get_price(s: Session, office_id: int | None, proc_id: int) -> PriceLookup:
    if office_id:
        op = s.scalar(select(OfficePrice).where(OfficePrice.office_id == office_id,
                                                OfficePrice.procedure_id == proc_id,
                                                OfficePrice.active.is_(True)))
        if op is not None:
            return PriceLookup(op.price_cents, "office")
    p = s.get(Procedure, proc_id)
    if p is not None and p.base_price_cents is not None:
        return PriceLookup(p.base_price_cents, "base")
    return PriceLookup(None, "none")


def office_price_map(s: Session, office_id: int) -> dict[int, int]:
    rows = s.scalars(select(OfficePrice).where(OfficePrice.office_id == office_id, OfficePrice.active.is_(True)))
    return {r.procedure_id: r.price_cents for r in rows}


def set_office_price(s: Session, office_id: int, proc_id: int, price, reason: str = "") -> None:
    """Set (or with price None/"" remove) an office's negotiated price."""
    op = s.scalar(select(OfficePrice).where(OfficePrice.office_id == office_id,
                                            OfficePrice.procedure_id == proc_id))
    new = None if price in (None, "") else to_cents(price)
    if new is not None and new < 0:
        raise BusinessError("Price cannot be negative.")
    old = op.price_cents if (op is not None and op.active) else None
    if old == new:
        return
    if new is None:
        if op is not None:
            s.delete(op)
    elif op is None:
        s.add(OfficePrice(office_id=office_id, procedure_id=proc_id, price_cents=new, active=True))
    else:
        op.price_cents = new
        op.active = True
    s.add(PriceHistory(office_id=office_id, procedure_id=proc_id, old_price_cents=old, new_price_cents=new,
                       changed_by=current_user().username, reason=reason))
    s.flush()


def copy_base_prices_to_office(s: Session, office_id: int) -> int:
    n = 0
    for p in list_procedures(s):
        if p.base_price_cents is not None:
            set_office_price(s, office_id, p.id, from_cents(p.base_price_cents), reason="Initial: Dentec base prices")
            n += 1
    return n


def copy_price_list(s: Session, from_office_id: int, to_office_id: int, overwrite: bool = True) -> int:
    if from_office_id == to_office_id:
        raise BusinessError("Choose a different office to copy from.")
    src = s.get(Office, from_office_id)
    if src is None or s.get(Office, to_office_id) is None:
        raise BusinessError("Dental office not found.")
    target = office_price_map(s, to_office_id)
    n = 0
    for proc_id, cents in office_price_map(s, from_office_id).items():
        if proc_id in target and not overwrite:
            continue
        set_office_price(s, to_office_id, proc_id, from_cents(cents), reason=f"Copied from {src.name}")
        n += 1
    audit(s, "copy_price_list", "office", to_office_id, f"from {src.name}: {n} prices")
    return n


def _round_to(cents: int, step_cents: int) -> int:
    if step_cents <= 1:
        return cents
    return int((Decimal(cents) / step_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * step_cents


def bulk_adjust(s: Session, office_id: int | None, *, percent=None, amount=None,
                procedure_ids: list[int] | None = None, round_to=None, reason: str = "") -> int:
    """Increase/decrease prices by a percentage or a fixed dollar amount.

    office_id None adjusts the Dentec base list.  Returns number changed.
    """
    if (percent in (None, "")) == (amount in (None, "")):
        raise BusinessError("Enter either a percentage or a dollar amount.")
    step = to_cents(round_to) if round_to not in (None, "") else 1
    changed = 0
    if office_id is None:
        targets = {p.id: p.base_price_cents for p in list_procedures(s, True) if p.base_price_cents is not None}
    else:
        targets = office_price_map(s, office_id)
    for proc_id, cents in targets.items():
        if procedure_ids is not None and proc_id not in procedure_ids:
            continue
        if percent not in (None, ""):
            new = int((Decimal(cents) * (1 + Decimal(str(percent)) / 100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        else:
            new = cents + to_cents(amount)
        new = max(0, _round_to(new, step))
        if new == cents:
            continue
        why = reason or (f"Bulk {percent}%" if percent not in (None, "") else f"Bulk {amount:+}" if isinstance(amount, (int, float)) else f"Bulk {amount}")
        if office_id is None:
            set_base_price(s, proc_id, from_cents(new), reason=why)
        else:
            set_office_price(s, office_id, proc_id, from_cents(new), reason=why)
        changed += 1
    audit(s, "bulk_price_adjust", "office" if office_id else "base_prices", office_id,
          f"percent={percent} amount={amount} changed={changed}")
    return changed


def price_history(s: Session, office_id: int | None = None, proc_id: int | None = None,
                  base_only: bool = False, limit: int = 500) -> list[PriceHistory]:
    q = select(PriceHistory).order_by(PriceHistory.changed_at.desc(), PriceHistory.id.desc()).limit(limit)
    if base_only:
        q = q.where(PriceHistory.office_id.is_(None))
    elif office_id is not None:
        q = q.where(PriceHistory.office_id == office_id)
    if proc_id is not None:
        q = q.where(PriceHistory.procedure_id == proc_id)
    return list(s.scalars(q))


# ------------------------------------------------------------ import/export
EXPORT_HEADER = ["Procedure", "Category", "Price", "Taxable", "Active"]


def price_list_rows(s: Session, office_id: int | None) -> list[list]:
    prices = office_price_map(s, office_id) if office_id else {}
    rows = []
    for p in list_procedures(s, include_inactive=True):
        cents = prices.get(p.id) if office_id else p.base_price_cents
        rows.append([p.name, p.category, "" if cents is None else f"{from_cents(cents):.2f}",
                     "Yes" if p.taxable else "No", "Yes" if p.active else "No"])
    return rows


def export_price_list(s: Session, office_id: int | None, path: str | Path) -> Path:
    path = Path(path)
    rows = price_list_rows(s, office_id)
    if path.suffix.lower() == ".xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Price List"
        ws.append(EXPORT_HEADER)
        for r in rows:
            r = list(r)
            r[2] = float(r[2]) if r[2] != "" else None
            ws.append(r)
        ws.column_dimensions["A"].width = 42
        ws.column_dimensions["B"].width = 34
        wb.save(path)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(EXPORT_HEADER)
            w.writerows(rows)
    return path


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [str(h or "").strip().lower() for h in next(it)]
        out = [dict(zip(header, r)) for r in it]
        wb.close()
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        header = [h.strip().lower() for h in next(rd)]
        return [dict(zip(header, r)) for r in rd]


def import_price_list(s: Session, office_id: int | None, path: str | Path,
                      create_missing: bool = True) -> dict:
    """Import prices from CSV/XLSX with columns Procedure, Price (+ optional Category, Taxable)."""
    path = Path(path)
    rows = _read_rows(path)
    result = {"updated": 0, "created": 0, "skipped": [], "unchanged": 0}
    for i, r in enumerate(rows, start=2):
        name = str(r.get("procedure") or r.get("name") or "").strip()
        price = r.get("price")
        if not name:
            continue
        if price is None or str(price).strip() == "":
            result["skipped"].append(f"Row {i}: '{name}' has no price")
            continue
        try:
            cents = to_cents(price if not isinstance(price, float) else f"{price:.2f}")
        except ValueError:
            result["skipped"].append(f"Row {i}: invalid price '{price}'")
            continue
        p = find_procedure(s, name)
        if p is None:
            if not create_missing:
                result["skipped"].append(f"Row {i}: unknown procedure '{name}'")
                continue
            taxable = str(r.get("taxable") or "").strip().lower() in ("yes", "y", "true", "1", "t")
            p = create_procedure(s, name, str(r.get("category") or "Other"), None, taxable)
            result["created"] += 1
        before = office_price_map(s, office_id).get(p.id) if office_id else p.base_price_cents
        if office_id:
            set_office_price(s, office_id, p.id, from_cents(cents), reason=f"Imported from {path.name}")
        else:
            set_base_price(s, p.id, from_cents(cents), reason=f"Imported from {path.name}")
        if before == cents:
            result["unchanged"] += 1
        else:
            result["updated"] += 1
    audit(s, "import_price_list", "office" if office_id else "base_prices", office_id,
          f"{path.name}: {result['updated']} updated, {result['created']} created, {len(result['skipped'])} skipped")
    return result
