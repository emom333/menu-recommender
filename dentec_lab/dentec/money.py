"""Money helpers. All amounts are stored in the database as integer cents."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENT = Decimal("0.01")


def to_cents(value) -> int:
    """Convert a user value ("$1,234.50", 12.5, Decimal) to integer cents."""
    if value is None or value == "":
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value * 100
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace(",", "")
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        if s == "" or s == "-":
            return 0
        try:
            d = Decimal(s)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid amount: {value!r}") from exc
        if neg:
            d = -d
    else:
        d = Decimal(str(value))
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents: int | None) -> Decimal:
    return (Decimal(int(cents or 0)) / 100).quantize(CENT)


def fmt(cents: int | None, symbol: bool = True) -> str:
    d = from_cents(cents)
    neg = d < 0
    s = f"{abs(d):,.2f}"
    if symbol:
        s = "$" + s
    return f"-{s}" if neg else s


def parse_rate(value) -> Decimal:
    """Parse a percentage like '7.975' or '7.975%' to Decimal('7.975')."""
    if value is None or str(value).strip() == "":
        return Decimal("0")
    s = str(value).strip().replace("%", "")
    try:
        d = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid tax rate: {value!r}") from exc
    if d < 0 or d > 100:
        raise ValueError("Tax rate must be between 0 and 100 percent")
    return d


def tax_on(cents: int, rate: Decimal) -> int:
    """Tax in cents on an amount in cents at a percentage rate (half-up)."""
    return int((Decimal(cents) * Decimal(rate) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
