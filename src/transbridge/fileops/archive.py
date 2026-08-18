"""Safe ZIP/7z/RAR inspection and extraction behind one policy boundary."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
from typing import Protocol
import zipfile

from .archive_policy import (
    ArchiveBudget,
    ArchiveManifest,
    ArchiveMember,
    ArchiveMemberType,
    ArchivePolicy,
    ArchivePolicyDiagnostic,
    ArchivePolicyError,
)


class CancellationSignal(Protocol):
    @property
    def is_cancelled(self) -> bool: ...


class ArchiveInspector(Protocol):
    """Read-only archive boundary. Implementations must not extract members."""

    def list_members(self, archive_path: str) -> tuple[str, tuple[ArchiveMember, ...]]: ...


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    dest_dir: str
    extracted_count: int
    manifest: ArchiveManifest
    expanded_bytes: int


class ArchiveCapabilityError(RuntimeError, ValueError):
    """The selected archive backend cannot provide the required safe capability."""


class ArchiveExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ArchiveCancelledError(ArchiveExtractionError):
    def __init__(self) -> None:
        super().__init__("ARCHIVE_CANCELLED", "archive extraction was cancelled")


def _find_unrar() -> str:
    """Locate the RAR extraction helper without mutating global process paths."""
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(str(Path(meipass) / "unrar.exe"))
    candidates.append(str(Path(__file__).resolve().parent / "bin" / "unrar.exe"))
    candidates.append(str(Path(__file__).resolve().parent / "unrar.exe"))
    which = shutil.which("unrar") or shutil.which("unrar.exe")
    if which:
        candidates.append(which)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ArchiveCapabilityError("ARCHIVE_RAR_BACKEND_UNAVAILABLE: safe RAR extraction requires unrar.exe")


class LibraryArchiveInspector:
    """Metadata adapters for the three supported archive libraries."""

    def list_members(self, archive_path: str) -> tuple[str, tuple[ArchiveMember, ...]]:
        suffix = Path(archive_path).suffix.lower()
        if suffix == ".zip":
            return "zip", self._list_zip(archive_path)
        if suffix == ".7z":
            return "7z", self._list_7z(archive_path)
        if suffix == ".rar":
            return "rar", self._list_rar(archive_path)
        raise ArchiveCapabilityError(f"ARCHIVE_FORMAT_UNAVAILABLE: unsupported archive format {suffix or '<none>'}")

    @staticmethod
    def _list_zip(archive_path: str) -> tuple[ArchiveMember, ...]:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                return tuple(_zip_member(info) for info in archive.infolist())
        except zipfile.BadZipFile as exc:
            raise ArchiveExtractionError("ARCHIVE_CORRUPT", "ZIP central directory is invalid") from exc

    @staticmethod
    def _list_7z(archive_path: str) -> tuple[ArchiveMember, ...]:
        try:
            import py7zr
        except ImportError as exc:
            raise ArchiveCapabilityError("ARCHIVE_7Z_BACKEND_UNAVAILABLE: py7zr is not installed") from exc
        try:
            with py7zr.SevenZipFile(archive_path, mode="r") as archive:
                return tuple(
                    ArchiveMember(
                        name=info.filename,
                        uncompressed_size=info.uncompressed,
                        compressed_size=info.compressed,
                        member_type=(
                            ArchiveMemberType.DIRECTORY
                            if info.is_directory
                            else ArchiveMemberType.FILE
                            if info.is_file
                            else ArchiveMemberType.SYMLINK
                            if info.is_symlink
                            else ArchiveMemberType.SPECIAL
                        ),
                    )
                    for info in archive.list()
                )
        except ArchiveCapabilityError:
            raise
        except Exception as exc:
            raise ArchiveExtractionError("ARCHIVE_CORRUPT", "7z metadata cannot be enumerated") from exc

    @staticmethod
    def _list_rar(archive_path: str) -> tuple[ArchiveMember, ...]:
        try:
            import rarfile
        except ImportError as exc:
            raise ArchiveCapabilityError("ARCHIVE_RAR_BACKEND_UNAVAILABLE: rarfile is not installed") from exc
        try:
            with rarfile.RarFile(archive_path) as archive:
                return tuple(
                    ArchiveMember(
                        name=info.filename,
                        uncompressed_size=info.file_size,
                        compressed_size=info.compress_size,
                        member_type=(
                            ArchiveMemberType.DIRECTORY
                            if info.is_dir()
                            else ArchiveMemberType.SYMLINK
                            if info.is_symlink()
                            else ArchiveMemberType.FILE
                            if info.is_file()
                            else ArchiveMemberType.SPECIAL
                        ),
                    )
                    for info in archive.infolist()
                )
        except Exception as exc:
            raise ArchiveExtractionError("ARCHIVE_CORRUPT", "RAR metadata cannot be enumerated") from exc


def _replace_directory_with_retry(source: Path, destination: Path) -> None:
    """Expose a staged directory, tolerating bounded Windows sharing races."""
    attempts = 5 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            transient = os.name == "nt" and getattr(exc, "winerror", 5) in {5, 32}
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


class ArchiveExtractor:
    """Preflight, stage, verify, then expose an extracted directory."""

    def __init__(
        self,
        *,
        inspector: ArchiveInspector | None = None,
        policy: ArchivePolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inspector = inspector or LibraryArchiveInspector()
        self._policy = policy or ArchivePolicy()
        self._clock = clock

    def inspect(self, archive_path: str | os.PathLike[str]) -> ArchiveManifest:
        archive_format, members = self._inspector.list_members(os.fspath(archive_path))
        return self._policy.evaluate(members, archive_format=archive_format)

    def extract(
        self,
        archive_path: str | os.PathLike[str],
        dest_dir: str | os.PathLike[str],
        *,
        files: Collection[str] | None = None,
        progress: Callable[[int, int], None] | None = None,
        cancellation: object | None = None,
    ) -> ExtractionResult:
        started = self._clock()
        self._check_control(cancellation, started)
        manifest = self.inspect(archive_path)
        self._check_control(cancellation, started)
        selected = self._select_members(manifest, files)
        destination = Path(dest_dir)
        self._validate_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tb_extract_", dir=destination.parent))
        try:
            expanded = self._extract_to_staging(
                os.fspath(archive_path),
                staging,
                manifest.archive_format,
                selected,
                progress,
                cancellation,
                started,
            )
            self._verify_staging(staging, selected, expanded)
            self._check_control(cancellation, started)
            destination_was_empty = destination.exists()
            if destination_was_empty:
                destination.rmdir()  # validated empty; leave it untouched on earlier failures
            try:
                _replace_directory_with_retry(staging, destination)
            except OSError:
                if destination_was_empty:
                    destination.mkdir(exist_ok=True)
                raise
            return ExtractionResult(
                dest_dir=str(destination),
                extracted_count=len(selected),
                manifest=manifest,
                expanded_bytes=expanded,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _select_members(self, manifest: ArchiveManifest, files: Collection[str] | None) -> tuple[ArchiveMember, ...]:
        available = {member.name: member for member in manifest.files}
        if files is None:
            return manifest.files
        requested = {self._policy.normalize_member_name(name) for name in files}
        return tuple(member for name, member in available.items() if name in requested)

    @staticmethod
    def _validate_destination(destination: Path) -> None:
        if destination.is_symlink():
            raise ArchiveExtractionError("ARCHIVE_DESTINATION_UNSAFE", "destination is a link")
        if destination.exists():
            if not destination.is_dir():
                raise ArchiveExtractionError("ARCHIVE_DESTINATION_EXISTS", "destination is not a directory")
            try:
                next(destination.iterdir())
            except StopIteration:
                return
            raise ArchiveExtractionError("ARCHIVE_DESTINATION_NOT_EMPTY", "destination must be empty")

    def _extract_to_staging(
        self,
        archive_path: str,
        staging: Path,
        archive_format: str,
        selected: tuple[ArchiveMember, ...],
        progress: Callable[[int, int], None] | None,
        cancellation: object | None,
        started: float,
    ) -> int:
        if archive_format == "zip":
            return self._extract_zip(archive_path, staging, selected, progress, cancellation, started)
        if archive_format == "7z":
            return self._extract_7z(archive_path, staging, selected, progress, cancellation, started)
        if archive_format == "rar":
            return self._extract_rar(archive_path, staging, selected, progress, cancellation, started)
        raise ArchiveCapabilityError(f"ARCHIVE_FORMAT_UNAVAILABLE: {archive_format}")

    def _extract_zip(self, archive_path, staging, selected, progress, cancellation, started) -> int:
        expanded = 0
        try:
            with zipfile.ZipFile(archive_path) as archive:
                by_name = {info.filename: info for info in archive.infolist()}
                for index, member in enumerate(selected, 1):
                    self._check_control(cancellation, started)
                    info = by_name[member.source_name or member.name]
                    with archive.open(info, "r") as source:
                        expanded += self._write_stream(source, staging, member, expanded, cancellation, started)
                    self._report(progress, index, len(selected))
        except (ArchivePolicyError, ArchiveExtractionError, ArchiveCancelledError):
            raise
        except (KeyError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise ArchiveExtractionError("ARCHIVE_EXTRACT_FAILED", "ZIP extraction failed") from exc
        return expanded

    def _extract_7z(self, archive_path, staging, selected, progress, cancellation, started) -> int:
        try:
            import py7zr
        except ImportError as exc:
            raise ArchiveCapabilityError("ARCHIVE_7Z_BACKEND_UNAVAILABLE: py7zr is not installed") from exc
        expanded = 0
        try:
            with py7zr.SevenZipFile(archive_path, mode="r") as archive:
                for index, member in enumerate(selected, 1):
                    self._check_control(cancellation, started)
                    archive.extract(path=staging, targets=[member.source_name or member.name])
                    self._check_control(cancellation, started)
                    archive.reset()
                    target = self._target_for(staging, member.name)
                    if not target.is_file() or target.is_symlink():
                        raise ArchiveExtractionError(
                            "ARCHIVE_EXTRACT_MISMATCH", "7z backend produced an unexpected member"
                        )
                    member_size = target.stat().st_size
                    if member_size != member.uncompressed_size:
                        raise ArchiveExtractionError(
                            "ARCHIVE_SIZE_MISMATCH",
                            "expanded member size differs from inspected metadata",
                        )
                    expanded += member_size
                    self._check_actual_budget(member, member_size, expanded)
                    self._report(progress, index, len(selected))
        except (ArchivePolicyError, ArchiveExtractionError, ArchiveCancelledError):
            raise
        except Exception as exc:
            raise ArchiveExtractionError("ARCHIVE_EXTRACT_FAILED", "7z extraction failed") from exc
        return expanded

    def _extract_rar(self, archive_path, staging, selected, progress, cancellation, started) -> int:
        try:
            import rarfile
        except ImportError as exc:
            raise ArchiveCapabilityError("ARCHIVE_RAR_BACKEND_UNAVAILABLE: rarfile is not installed") from exc
        backend_error: ArchiveCapabilityError | None = None
        try:
            rarfile.UNRAR_TOOL = _find_unrar()
        except ArchiveCapabilityError as exc:
            # Stored RAR members are readable by rarfile itself.  A compressed
            # member will raise RarCannotExec below and surface this capability.
            backend_error = exc
        expanded = 0
        try:
            with rarfile.RarFile(archive_path) as archive:
                by_name = {info.filename: info for info in archive.infolist()}
                for index, member in enumerate(selected, 1):
                    self._check_control(cancellation, started)
                    with archive.open(by_name[member.source_name or member.name], "r") as source:
                        expanded += self._write_stream(source, staging, member, expanded, cancellation, started)
                    self._report(progress, index, len(selected))
        except (ArchivePolicyError, ArchiveExtractionError, ArchiveCancelledError):
            raise
        except rarfile.RarCannotExec as exc:
            raise backend_error or ArchiveCapabilityError(
                "ARCHIVE_RAR_BACKEND_UNAVAILABLE: no safe RAR decompressor is available"
            ) from exc
        except Exception as exc:
            raise ArchiveExtractionError("ARCHIVE_EXTRACT_FAILED", "RAR extraction failed") from exc
        return expanded

    def _write_stream(self, source, staging, member, expanded, cancellation, started) -> int:
        target = self._target_for(staging, member.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        member_size = 0
        with target.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                self._check_control(cancellation, started)
                member_size += len(chunk)
                self._check_actual_budget(member, member_size, expanded + member_size)
                output.write(chunk)
        if member_size != member.uncompressed_size:
            raise ArchiveExtractionError(
                "ARCHIVE_SIZE_MISMATCH", "expanded member size differs from inspected metadata"
            )
        return member_size

    def _check_actual_budget(self, member: ArchiveMember, member_size: int, total: int) -> None:
        budget = self._policy.budget
        if member_size > budget.max_single_file:
            raise ArchivePolicyError(
                ArchivePolicyDiagnostic("ARCHIVE_FILE_SIZE_LIMIT", "actual member size exceeds budget", member.name)
            )
        if total > budget.max_total_uncompressed:
            raise ArchivePolicyError(
                ArchivePolicyDiagnostic("ARCHIVE_TOTAL_SIZE_LIMIT", "actual expanded archive size exceeds budget")
            )

    @staticmethod
    def _target_for(staging: Path, member_name: str) -> Path:
        target = staging.joinpath(*member_name.split("/"))
        try:
            target.resolve(strict=False).relative_to(staging.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ArchiveExtractionError("ARCHIVE_PATH_UNSAFE", "member resolved outside extraction staging") from exc
        return target

    def _verify_staging(
        self,
        staging: Path,
        selected: tuple[ArchiveMember, ...],
        expanded: int,
    ) -> None:
        approved = {member.name.casefold() for member in selected}
        actual: set[str] = set()
        actual_total = 0
        for path in staging.rglob("*"):
            if path.is_symlink() or _is_reparse_point(path):
                raise ArchiveExtractionError("ARCHIVE_MEMBER_LINK", "backend created a link or reparse point")
            if path.is_file():
                relative = path.relative_to(staging).as_posix()
                actual.add(relative.casefold())
                actual_total += path.stat().st_size
            elif not path.is_dir():
                raise ArchiveExtractionError("ARCHIVE_MEMBER_SPECIAL", "backend created a special filesystem entry")
        if actual != approved or actual_total != expanded:
            raise ArchiveExtractionError("ARCHIVE_EXTRACT_MISMATCH", "staged output does not match approved manifest")

    def _check_control(self, cancellation: object | None, started: float) -> None:
        if _is_cancelled(cancellation):
            raise ArchiveCancelledError()
        if self._clock() - started > self._policy.budget.timeout_seconds:
            raise ArchiveExtractionError("ARCHIVE_TIMEOUT", "archive extraction exceeded time budget")

    @staticmethod
    def _report(progress, completed: int, total: int) -> None:
        if progress:
            progress(completed, total)


def inspect_archive(archive_path: str | os.PathLike[str], *, policy: ArchivePolicy | None = None) -> ArchiveManifest:
    return ArchiveExtractor(policy=policy).inspect(archive_path)


def extract(
    archive_path: str,
    dest_dir: str,
    *,
    files: list[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    policy: ArchivePolicy | None = None,
    budget: ArchiveBudget | None = None,
    cancellation: object | None = None,
) -> dict:
    """Compatibility facade returning the historical dict shape."""
    if policy is not None and budget is not None:
        raise ValueError("pass policy or budget, not both")
    active_policy = policy or ArchivePolicy(budget)
    result = ArchiveExtractor(policy=active_policy).extract(
        archive_path,
        dest_dir,
        files=files,
        progress=progress,
        cancellation=cancellation,
    )
    return {"dest_dir": result.dest_dir, "extracted_count": result.extracted_count}


def pack(
    src_dir: str,
    archive_path: str,
    *,
    fmt: str = "zip",
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Pack a directory as ZIP/7z; RAR creation is intentionally unavailable."""
    if fmt == "zip":
        return _pack_zip(src_dir, archive_path, progress)
    if fmt == "7z":
        return _pack_7z(src_dir, archive_path, progress)
    raise ValueError(f"unsupported archive format for packing: {fmt}")


def _pack_zip(src_dir, archive_path, progress) -> str:
    source = Path(src_dir)
    files = [path for path in source.rglob("*") if path.is_file()]
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(files, 1):
            archive.write(path, path.relative_to(source))
            if progress:
                progress(index, len(files))
    return os.fspath(archive_path)


def _pack_7z(src_dir, archive_path, progress) -> str:
    try:
        import py7zr
    except ImportError as exc:
        raise ArchiveCapabilityError("ARCHIVE_7Z_BACKEND_UNAVAILABLE: py7zr is not installed") from exc
    with py7zr.SevenZipFile(archive_path, mode="w") as archive:
        archive.writeall(src_dir, arcname="")
    if progress:
        count = sum(1 for path in Path(src_dir).rglob("*") if path.is_file())
        progress(count, count)
    return os.fspath(archive_path)


def _zip_member(info: zipfile.ZipInfo) -> ArchiveMember:
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.is_dir():
        member_type = ArchiveMemberType.DIRECTORY
    elif stat.S_ISLNK(mode):
        member_type = ArchiveMemberType.SYMLINK
    elif stat.S_IFMT(mode) and not stat.S_ISREG(mode):
        member_type = ArchiveMemberType.SPECIAL
    else:
        member_type = ArchiveMemberType.FILE
    return ArchiveMember(info.filename, info.file_size, info.compress_size, member_type)


def _is_cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
