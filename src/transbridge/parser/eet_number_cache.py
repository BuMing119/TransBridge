# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""EET4 4.35 numeric-cache generation, including its exact Roman-number rules.

Ported from reflection-only IL of TextWitoutNumber.Create and the normal record
calculation path. No original-text hashes are calculated by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from .eet_binary_wire import EetFormatError, _number, _Reader, _string
from .eet_number_unicode import DIGIT_RANGES, WORD_RANGES

_NUMERIC = r"[\+\-]?\d+(\.?\d+)?"
_ROMAN = (
    r"\b(?=[MDCLXVI]+\b(?!('| am)))"
    r"M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\b(?!('| am))"
)


@dataclass(frozen=True)
class EetNumberOptions:
    max_utf16_units: int = 200
    roman: bool = True
    except_i_space: bool = True

    def __post_init__(self):
        if isinstance(self.max_utf16_units, bool) or not isinstance(self.max_utf16_units, int):
            raise ValueError("EET numeric maximum length must be an integer")
        if not 0 <= self.max_utf16_units <= 0x7FFFFFFF:
            raise ValueError("EET numeric maximum length must be in 0..2147483647")
        if not isinstance(self.roman, bool) or not isinstance(self.except_i_space, bool):
            raise ValueError("EET Roman-number options must be booleans")


@dataclass(frozen=True)
class EetNumberCache:
    template: str
    values: tuple[str, ...]

    def to_payload(self) -> bytes:
        return _string(self.template) + _number(len(self.values), 4) + b"".join(_string(value) for value in self.values)


def parse_number_cache(payload: bytes) -> EetNumberCache | None:
    """Read a cache payload excluding the enclosing uint32 blob length."""
    if not isinstance(payload, bytes):
        raise EetFormatError("Variable data must be bytes")
    if not payload:
        return None
    reader = _Reader(payload)
    template = reader.string()
    count = reader.number(4)
    if count > reader.remaining // 4:
        raise EetFormatError("Variable count exceeds remaining cache data")
    values = tuple(reader.string() for _ in range(count))
    reader.finish()
    return EetNumberCache(template, values)


def build_number_cache(
    text: str,
    *,
    group: str = "",
    field: str = "",
    options: EetNumberOptions = EetNumberOptions(),
) -> EetNumberCache | None:
    """Rebuild a record cache with UTF-16 indexing and at most 100 matches.

    Existing payloads are never normalized on read. This function creates a new
    cache using explicit options, since EET files do not store the regex settings.
    """
    if not isinstance(text, str):
        raise EetFormatError("Numeric-cache text must be a string")
    _string(text)
    if group == "SCPT" and field == "SCTX":
        return None
    raw = text.encode("utf-16-le")
    length = len(raw) // 2
    if not 0 < length <= options.max_utf16_units:
        return None
    units = "".join(chr(raw[i] | (raw[i + 1] << 8)) for i in range(0, len(raw), 2))
    matches = []
    pattern = _pattern(options.roman, options.except_i_space)
    position = 0
    while position <= length and len(matches) < 100:
        match = pattern.search(units, position)
        if match is None:
            break
        matches.append(match)
        # .NET NextMatch advances one UTF-16 unit after an empty match.
        position = match.end() + (match.start() == match.end())
    if not matches:
        return None
    template = units
    for i in reversed(range(len(matches))):
        match = matches[i]
        template = template[: match.start()] + f"%VAR{i:02d}%" + template[match.end() :]
    return EetNumberCache(_from_units(template), tuple(_from_units(match.group()) for match in matches))


def _from_units(value: str) -> str:
    return value.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le")


@lru_cache(maxsize=4)
def _pattern(roman: bool, except_i_space: bool) -> re.Pattern:
    # Build on UTF-16 units, avoiding Python's broader astral \d / different \b.
    digits = _character_class(DIGIT_RANGES)
    word = _character_class(WORD_RANGES)
    boundary = rf"(?:(?<={word})(?!{word})|(?<!{word})(?={word}))"
    pattern = _NUMERIC
    if roman:
        pattern += "|" + ("(?!I )" if except_i_space else "") + _ROMAN
    return re.compile(pattern.replace(r"\d", digits).replace(r"\b", boundary))


def _character_class(ranges: tuple[tuple[int, int], ...]) -> str:
    return "[" + "".join(rf"\u{start:04x}-\u{end:04x}" for start, end in ranges) + "]"
