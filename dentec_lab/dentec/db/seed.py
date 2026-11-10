"""Preloaded procedure catalog, Dentec reference prices and default settings."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import COMPANY_DEFAULTS
from .models import Procedure, Setting

# (name, category, reference price in dollars or None when no reference price)
# The first ten are Dentec's standard billing procedures; the rest come from
# the Dentec price list.  Teeth Try-In and Full Denture Finish are deliberately
# separate items even though both are $99.
STANDARD_PROCEDURES: list[tuple[str, str, float | None]] = [
    ("Custom Tray", "Removable Partial & Denture", 35.00),
    ("Bite Block", "Removable Partial & Denture", 35.00),
    ("Teeth Try-In", "Removable Partial & Denture", 99.00),
    ("Acrylic Partial Finish", "Removable Partial & Denture", 175.00),
    ("Full Denture Finish", "Removable Partial & Denture", 99.00),
    ("PFM Crown", "PFM", 89.00),
    ("Full Zirconia Crown", "All Ceramic", 89.00),
    ("Flexible Partial", "Removable Partial & Denture", 285.00),
    ("Repair", "Removable Partial & Denture", 105.00),
    ("Reline", "Removable Partial & Denture", None),
    # --- Dentec price list ---
    ("Custom Special Tray", "Removable Partial & Denture", 35.00),
    ("Full Denture Teeth Set Up", "Removable Partial & Denture", 99.00),
    ("Metal Partial", "Removable Partial & Denture", 285.00),
    ("Valplast Flexible Partial", "Removable Partial & Denture", 285.00),
    ("Removable Partial / Denture Repair", "Removable Partial & Denture", 105.00),
    ("Space Maintainer", "Retainer, Orthodontic & Mouth Guard", 105.00),
    ("Hawley Retainer", "Retainer, Orthodontic & Mouth Guard", 155.00),
    ("Soft Night Guard", "Retainer, Orthodontic & Mouth Guard", 105.00),
    ("Hard & Soft Night Guard", "Retainer, Orthodontic & Mouth Guard", 135.00),
    ("Bleaching Guard", "Retainer, Orthodontic & Mouth Guard", 105.00),
    ("Porcelain Zirconia Crown", "All Ceramic", 135.00),
    ("E.Max Crown", "All Ceramic", 150.00),
    ("E.Max Onlay", "All Ceramic", 150.00),
    ("E.Max Inlay", "All Ceramic", 150.00),
    ("E.Max Veneer", "All Ceramic", 150.00),
    ("PFM CoCr", "PFM", 89.00),
    ("Metal Wing / Rest", "PFM", 115.00),
    ("Pink Porcelain", "PFM", 35.00),
    ("Porcelain Shoulder Margin", "PFM", 35.00),
    ("Metal Occlusion", "PFM", 35.00),
    ("Implant Custom Abutment", "Implant", 295.00),
    ("Implant Custom Abutment + Zirconia Crown", "Implant", 385.00),
]

DEFAULT_SETTINGS = {
    **COMPANY_DEFAULTS,
    "schema_version": "1",
    # Tax: deliberately 0% until confirmed by the business's tax professional.
    "tax_rate": "0",
    "tax_method": "invoice",  # invoice = round tax once on taxable subtotal; line = per line
    "tax_label": "Sales Tax",
    "auto_backup": "1",
    "backup_keep": "30",
    "backup_dir": "",  # empty = <home>/backups
    "last_backup": "",
    "idle_lock_minutes": "15",
    "require_admin_price_override": "1",
    "tesseract_path": "",
    "invoice_footer": "Thank you for your business!",
    "invoice_terms": "Payment due within 30 days.",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_from": "",
    "smtp_tls": "1",
    "delivery_days": "Tuesday,Thursday",
}


def seed_defaults(s: Session) -> None:
    existing = {p.name for p in s.scalars(select(Procedure))}
    for idx, (name, cat, price) in enumerate(STANDARD_PROCEDURES):
        if name in existing:
            continue
        s.add(Procedure(name=name, category=cat,
                        base_price_cents=None if price is None else int(round(price * 100)),
                        taxable=False, active=True, is_custom=False, sort_order=(idx + 1) * 10))
    for key, value in DEFAULT_SETTINGS.items():
        if s.get(Setting, key) is None:
            s.add(Setting(key=key, value=value))
    s.flush()
