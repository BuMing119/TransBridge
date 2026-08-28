"""Safe, loss-aware conversion of untrusted values to Excel cells."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import json
import math

EXCEL_CELL_CHAR_LIMIT = 32_767
_FORMULA_PREFIXES = frozenset({"=", "+", "-", "@", "\t", "\r"})


class SpreadsheetValueError(ValueError):
    pass


def escape_illegal_xml_characters(value: str) -> str:
    """Represent XML-illegal code points visibly instead of dropping user data."""

    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        legal = (
            codepoint in {0x09, 0x0A, 0x0D}
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )
        if legal and codepoint not in {0xFFFE, 0xFFFF}:
            escaped.append(character)
        elif codepoint <= 0xFFFF:
            escaped.append(f"\\u{codepoint:04X}")
        else:
            escaped.append(f"\\U{codepoint:08X}")
    return "".join(escaped)


def spreadsheet_chunks(value: object) -> tuple[object, ...]:
    """Return deterministic, safe cell chunks without truncating long text."""

    scalar = spreadsheet_scalar(value)
    if not isinstance(scalar, str):
        return (scalar,)
    text = escape_illegal_xml_characters(scalar)
    if not text:
        return ("",)
    chunks: list[str] = []
    position = 0
    while position < len(text):
        formula = text[position] in _FORMULA_PREFIXES
        width = EXCEL_CELL_CHAR_LIMIT - (1 if formula else 0)
        chunk = text[position : position + width]
        chunks.append(f"'{chunk}" if formula else chunk)
        position += len(chunk)
    return tuple(chunks)


def spreadsheet_scalar(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str, date, datetime, time, Decimal)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpreadsheetValueError("Excel cells do not support non-finite numbers")
        return value
    if isinstance(value, Enum):
        return spreadsheet_scalar(value.value)
    if isinstance(value, (tuple, list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise SpreadsheetValueError(f"value cannot be represented safely in Excel: {type(value).__name__}") from exc
    raise SpreadsheetValueError(f"unsupported Excel scalar type: {type(value).__name__}")


def expanded_spreadsheet_rows(values: tuple[object, ...]) -> tuple[tuple[object, ...], ...]:
    """Split oversized cells into aligned continuation rows."""

    columns = tuple(spreadsheet_chunks(value) for value in values)
    segments = max((len(column) for column in columns), default=1)
    return tuple(tuple(column[index] if index < len(column) else "" for column in columns) for index in range(segments))


__all__ = [
    "EXCEL_CELL_CHAR_LIMIT",
    "SpreadsheetValueError",
    "escape_illegal_xml_characters",
    "expanded_spreadsheet_rows",
    "spreadsheet_chunks",
    "spreadsheet_scalar",
]
