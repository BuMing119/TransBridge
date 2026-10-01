"""ParaTranz display order without changing local context or entry identity."""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

PARATRANZ_CONTEXT_ORDER_METADATA = "transbridge.io.paratranz.context_order"
MAX_CONTEXT_ORDER = 99_999_999
_CONTEXT = r"[A-Z0-9_]{4}:[A-Z0-9_]{4}(?:\|[0-9A-Fa-f]{8}|\|)?"
_ORDERED_CONTEXT = re.compile(rf"([0-9]{{8}})\|({_CONTEXT})")
_BASE_CONTEXT = re.compile(_CONTEXT)


def parse_ordered_context(context: str | None) -> tuple[str | None, int | None]:
    """Recognize only the structured TransBridge display prefix."""
    match = _ORDERED_CONTEXT.fullmatch(context) if isinstance(context, str) else None
    return (match[2], int(match[1])) if match else (context, None)


def format_ordered_context(context: str | None, order: int) -> str | None:
    """Project a strict eight-digit prefix, leaving free-form context intact."""
    validate_context_order(order)
    base, _ = parse_ordered_context(context)
    if not isinstance(base, str) or not _BASE_CONTEXT.fullmatch(base):
        return base
    return f"{order:08d}|{base}"


def validate_context_order(order: object) -> None:
    if type(order) is not int or not 0 <= order <= MAX_CONTEXT_ORDER:
        raise ValueError("ParaTranz context order must be an integer between 0 and 99999999.")


def context_orders(entries: Sequence[Any]) -> tuple[int, ...]:
    """Use a complete unique source sequence, otherwise preserve input order."""
    orders: list[int | None] = []
    for entry in entries:
        metadata = dict(getattr(entry, "metadata", ()))
        order = metadata.get("plugin.source_order", metadata.get(PARATRANZ_CONTEXT_ORDER_METADATA))
        if type(order) is int and order >= 0:
            validate_context_order(order)
        else:
            order = None
        orders.append(order)
    if all(order is not None for order in orders) and len(set(orders)) == len(orders):
        return tuple(orders)
    if entries:
        validate_context_order(len(entries) - 1)
    return tuple(range(len(entries)))
