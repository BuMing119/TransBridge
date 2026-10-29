"""Filename-only hints for user-selected replacement source inputs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import stat

# Filename hints from application.io.catalog; these do not assert format validity.
_INPUT_SUFFIXES = frozenset({
    ".esp",
    ".esm",
    ".esl",
    ".eet",
    ".xml",
    ".json",
    ".sst",
    ".strings",
    ".dlstrings",
    ".ilstrings",
})


@dataclass(frozen=True, slots=True)
class InputNeed:
    key: str
    label: str
    entry_count: int
    ready: bool
    reference_path: str = ""


@dataclass(frozen=True, slots=True)
class InputCandidates:
    paths: tuple[str, ...]
    errors: tuple[str, ...] = ()


def collect_input_files(
    paths: Iterable[str] = (), *, directory: str | os.PathLike[str] | None = None
) -> InputCandidates:
    """Keep explicit selections and enumerate a selected directory without reading content.

    Call from a worker: directory discovery performs filesystem metadata operations.
    Errors describe unavailable paths without discarding other discovered candidates.
    """
    selected = list(paths)
    errors: list[str] = []
    if directory is not None:
        _collect_directory(Path(directory), selected, errors)
    return InputCandidates(tuple(selected), tuple(errors))


def _collect_directory(directory: Path, selected: list[str], errors: list[str]) -> None:
    pending = [directory]
    seen = set(selected)
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
            if _is_link(metadata):
                continue
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
        except OSError as exc:
            errors.append(f"{current}: {exc}")
            continue
        directories = []
        for entry in entries:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append(f"{entry.path}: {exc}")
                continue
            if _is_link(metadata):
                continue
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(Path(entry.path))
            elif stat.S_ISREG(metadata.st_mode) and Path(entry.name).suffix.casefold() in _INPUT_SUFFIXES:
                if entry.path not in seen:
                    selected.append(entry.path)
                    seen.add(entry.path)
        pending.extend(reversed(directories))


def _is_link(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_reparse_tag", None) == stat.IO_REPARSE_TAG_MOUNT_POINT
    )


def match_input_candidates(needs: tuple[InputNeed, ...], paths: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Suggest exact names for missing inputs, retaining ambiguity for user choice.

    Reference paths supply a basename only; they are never accessed. Suggestions
    neither validate candidate content nor reserve a path for a particular need.
    """
    by_name: dict[str, list[str]] = {}
    for path in paths:
        by_name.setdefault(_basename(path).casefold(), []).append(path)
    matches = {}
    for need in needs:
        if need.ready:
            continue
        names = {need.label.casefold()}
        if need.reference_path:
            names.add(_basename(need.reference_path).casefold())
        candidates = {path for name in names for path in by_name.get(name, ())}
        matches[need.key] = tuple(path for path in paths if path in candidates)
    return matches


def _basename(path: str) -> str:
    # A saved Windows reference must retain its basename when opened on POSIX.
    return PureWindowsPath(path).name if "\\" in path else Path(path).name
