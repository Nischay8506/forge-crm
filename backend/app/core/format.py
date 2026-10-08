"""Display formats shared by sentences, cards and the audit log."""

from decimal import Decimal, InvalidOperation
from typing import Any


def inr(amount: Any) -> str:
    """Rupees with Indian digit grouping: 150000 -> '₹1,50,000', 1234.5 -> '₹1,234.50'."""
    try:
        d = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return f"₹{amount}"
    sign, d = ("-" if d < 0 else ""), abs(d)
    whole = d.to_integral_value(rounding="ROUND_DOWN")
    text = f"{d:.2f}" if d != whole else str(whole)
    digits, _, paise = text.partition(".")
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{sign}₹{grouped}" + (f".{paise}" if paise else "")
