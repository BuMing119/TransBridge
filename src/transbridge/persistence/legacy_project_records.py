"""Read-only record boundaries for historical project packages."""

from __future__ import annotations

from typing import Protocol
from zipfile import ZipFile


class LegacyProjectRecords(Protocol):
    @property
    def members(self) -> frozenset[str]: ...

    def read(self, member: str) -> bytes: ...


class LegacyArchiveRecords:
    def __init__(self, archive: ZipFile) -> None:
        self._archive = archive

    @property
    def members(self) -> frozenset[str]:
        return frozenset(item.filename for item in self._archive.infolist() if not item.is_dir())

    def read(self, member: str) -> bytes:
        return self._archive.read(member)
