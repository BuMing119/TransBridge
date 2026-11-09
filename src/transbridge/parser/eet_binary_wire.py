# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bounds-checked primitive wire fields shared by EET records and numeric caches."""

import struct


class EetFormatError(ValueError):
    """Input does not satisfy the verified EET v3 structural subset."""


def _number(value: int, width: int) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < (1 << (width * 8)):
        raise EetFormatError(f"Value {value!r} is not uint{width * 8}")
    return value.to_bytes(width, "little")


def _signed(value: int, width: int) -> bytes:
    limit = 1 << (width * 8 - 1)
    if isinstance(value, bool) or not isinstance(value, int) or not -limit <= value < limit:
        raise EetFormatError(f"Value {value!r} is not int{width * 8}")
    return value.to_bytes(width, "little", signed=True)


def _blob(value: bytes) -> bytes:
    if not isinstance(value, bytes):
        raise EetFormatError("Variable data must be bytes")
    return _number(len(value), 4) + value


def _string(value: str, width: int = 4) -> bytes:
    if not isinstance(value, str):
        raise EetFormatError("Text fields must be strings")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise EetFormatError("Text is not representable as UTF-8") from exc
    return _number(len(encoded), width) + encoded


class _Reader:
    def __init__(self, content: bytes, *, origin: int = 0):
        self.content = content
        self.position = 0
        self.origin = origin

    @property
    def remaining(self) -> int:
        return len(self.content) - self.position

    def take(self, length: int) -> bytes:
        if length < 0 or length > self.remaining:
            raise EetFormatError(f"Truncated data at offset {self.origin + self.position}: requested {length} bytes")
        start = self.position
        self.position += length
        return self.content[start : self.position]

    def number(self, width: int) -> int:
        return struct.unpack("<I" if width == 4 else "<H", self.take(width))[0]

    def signed(self, width: int) -> int:
        return int.from_bytes(self.take(width), "little", signed=True)

    def blob(self) -> bytes:
        return self.take(self.number(4))

    def string(self, width: int = 4) -> str:
        raw = self.take(self.number(width))
        try:
            return raw.decode("utf-8")
        except UnicodeError as exc:
            raise EetFormatError(f"Invalid UTF-8 near offset {self.origin + self.position}") from exc

    def expect(self, expected: bytes) -> None:
        offset = self.origin + self.position
        if self.take(len(expected)) != expected:
            raise EetFormatError(f"Expected {expected!r} at offset {offset}")

    def finish(self) -> None:
        if self.remaining:
            raise EetFormatError(f"Unexpected {self.remaining} trailing bytes at offset {self.origin + self.position}")
