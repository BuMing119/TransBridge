"""Archive member validation shared by every extraction backend.

The policy is deliberately independent from ZIP/7z/RAR libraries.  Backends may
only write members from an approved :class:`ArchiveManifest`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
import math
from pathlib import PurePosixPath, PureWindowsPath
import re


class ArchiveMemberType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    SPECIAL = "special"


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """One backend-neutral archive directory entry."""

    name: str
    uncompressed_size: int
    compressed_size: int | None
    member_type: ArchiveMemberType
    source_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("archive member name must be non-empty")
        _require_non_negative_int("uncompressed_size", self.uncompressed_size)
        if self.compressed_size is not None:
            _require_non_negative_int("compressed_size", self.compressed_size)
        if not isinstance(self.member_type, ArchiveMemberType):
            raise TypeError("member_type must be ArchiveMemberType")
        if self.source_name is not None and (not isinstance(self.source_name, str) or not self.source_name):
            raise ValueError("source_name must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class ArchiveBudget:
    """Hard extraction limits; all values include the boundary."""

    max_entries: int = 100_000
    max_total_uncompressed: int = 8 * 1024 * 1024 * 1024
    max_single_file: int = 4 * 1024 * 1024 * 1024
    max_compression_ratio: float = 1_000.0
    max_depth: int = 32
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        for name in ("max_entries", "max_total_uncompressed", "max_single_file", "max_depth"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("max_compression_ratio", "timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a positive finite number")
            if not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    archive_format: str
    members: tuple[ArchiveMember, ...]
    total_uncompressed: int
    total_compressed: int

    @property
    def files(self) -> tuple[ArchiveMember, ...]:
        return tuple(member for member in self.members if member.member_type is ArchiveMemberType.FILE)


@dataclass(frozen=True, slots=True)
class ArchivePolicyDiagnostic:
    code: str
    message: str
    member: str | None = None


class ArchivePolicyError(ValueError):
    def __init__(self, diagnostic: ArchivePolicyDiagnostic) -> None:
        self.diagnostic = diagnostic
        suffix = f" [{diagnostic.member}]" if diagnostic.member else ""
        super().__init__(f"{diagnostic.code}: {diagnostic.message}{suffix}")


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


class ArchivePolicy:
    """Normalise names and reject the complete archive before extraction."""

    def __init__(self, budget: ArchiveBudget | None = None) -> None:
        self.budget = budget or ArchiveBudget()

    def evaluate(
        self,
        members: Iterable[ArchiveMember],
        *,
        archive_format: str,
    ) -> ArchiveManifest:
        approved: list[ArchiveMember] = []
        seen: set[str] = set()
        total_uncompressed = 0
        total_compressed = 0
        compressed_total_complete = True

        for index, member in enumerate(members, 1):
            if index > self.budget.max_entries:
                self._reject("ARCHIVE_COUNT_LIMIT", "archive member count exceeds budget")
            if member.member_type is ArchiveMemberType.SYMLINK:
                self._reject("ARCHIVE_MEMBER_LINK", "links and junctions are not extractable", member.name)
            if member.member_type is ArchiveMemberType.SPECIAL:
                self._reject("ARCHIVE_MEMBER_SPECIAL", "special archive members are not extractable", member.name)

            normalized = self.normalize_member_name(member.name)
            identity = normalized.casefold()
            if identity in seen:
                self._reject("ARCHIVE_DUPLICATE_PATH", "member path is ambiguous after normalisation", member.name)
            seen.add(identity)
            depth = len(PurePosixPath(normalized).parts)
            if depth > self.budget.max_depth:
                self._reject("ARCHIVE_DEPTH_LIMIT", "member path depth exceeds budget", member.name)
            if member.uncompressed_size > self.budget.max_single_file:
                self._reject("ARCHIVE_FILE_SIZE_LIMIT", "member size exceeds budget", member.name)

            total_uncompressed += member.uncompressed_size
            total_compressed += member.compressed_size or 0
            if member.member_type is ArchiveMemberType.FILE and member.compressed_size is None:
                compressed_total_complete = False
            if total_uncompressed > self.budget.max_total_uncompressed:
                self._reject("ARCHIVE_TOTAL_SIZE_LIMIT", "expanded archive size exceeds budget")
            if member.member_type is ArchiveMemberType.FILE:
                ratio = _compression_ratio(member.uncompressed_size, member.compressed_size)
                if ratio > self.budget.max_compression_ratio:
                    self._reject(
                        "ARCHIVE_COMPRESSION_RATIO_LIMIT",
                        "member compression ratio exceeds budget",
                        member.name,
                    )
            approved.append(replace(member, name=normalized, source_name=member.source_name or member.name))

        overall_ratio = _compression_ratio(total_uncompressed, total_compressed)
        if approved and compressed_total_complete and overall_ratio > self.budget.max_compression_ratio:
            self._reject("ARCHIVE_COMPRESSION_RATIO_LIMIT", "archive compression ratio exceeds budget")
        return ArchiveManifest(
            archive_format=archive_format,
            members=tuple(approved),
            total_uncompressed=total_uncompressed,
            total_compressed=total_compressed,
        )

    @staticmethod
    def normalize_member_name(name: str) -> str:
        if "\x00" in name:
            ArchivePolicy._reject("ARCHIVE_PATH_UNSAFE", "member path contains NUL", name)
        slash_name = name.replace("\\", "/")
        windows_path = PureWindowsPath(slash_name)
        if (
            slash_name.startswith(("/", "//"))
            or windows_path.is_absolute()
            or windows_path.drive
            or _DRIVE_PREFIX.match(slash_name)
        ):
            ArchivePolicy._reject("ARCHIVE_PATH_UNSAFE", "absolute, UNC, and drive paths are forbidden", name)

        parts = slash_name.split("/")
        while parts and parts[-1] == "":
            parts.pop()
        if not parts or any(part in {"", ".", ".."} for part in parts):
            ArchivePolicy._reject("ARCHIVE_PATH_UNSAFE", "member path is empty or contains traversal", name)
        for part in parts:
            if ":" in part or part.endswith((" ", ".")) or _WINDOWS_RESERVED.match(part):
                ArchivePolicy._reject("ARCHIVE_PATH_UNSAFE", "member path is unsafe on Windows", name)
        return "/".join(parts)

    @staticmethod
    def _reject(code: str, message: str, member: str | None = None) -> None:
        raise ArchivePolicyError(ArchivePolicyDiagnostic(code, message, member))


def _require_non_negative_int(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _compression_ratio(uncompressed: int, compressed: int | None) -> float:
    if uncompressed == 0:
        return 0.0
    if compressed is None:
        return 1.0
    if compressed == 0:
        return math.inf
    return uncompressed / compressed
