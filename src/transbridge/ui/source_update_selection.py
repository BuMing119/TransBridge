# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Build an explicit one-to-one batch from suggested and manually selected files."""

from collections import Counter
from dataclasses import dataclass
from os.path import normcase, normpath
from pathlib import PureWindowsPath

from transbridge.application.io.catalog import filename_format_hints
from transbridge.application.projects.source_input_candidates import InputNeed, match_input_candidates
from transbridge.application.projects.source_update_models import SourceUpdateTarget


@dataclass(frozen=True, slots=True)
class SourcePairing:
    source: SourceUpdateTarget
    candidates: tuple[str, ...]
    replacement: str | None
    skipped: bool


class SourceUpdateSelection:
    def __init__(self, sources: tuple[SourceUpdateTarget, ...]) -> None:
        self.sources = sources
        self._files: dict[str, str] = {}
        self._manual: dict[str, str] = {}
        self._skipped: set[str] = set()

    def add_files(self, files: tuple[str, ...]) -> None:
        for path in files:
            self._files.setdefault(_path_key(path), path)

    def rows(self) -> tuple[SourcePairing, ...]:
        needs = tuple(
            InputNeed(
                source.source_id,
                PureWindowsPath(source.location).name if source.location else source.label,
                0,
                False,
            )
            for source in self.sources
        )
        matches = match_input_candidates(needs, tuple(self._files.values()))
        suggestions = {
            source.source_id: tuple(
                path
                for path in matches[source.source_id]
                if source.format_id in filename_format_hints(PureWindowsPath(path).suffix)
            )
            for source in self.sources
        }
        counts = Counter(_path_key(path) for paths in suggestions.values() for path in paths)
        reserved = {_path_key(path) for path in self._manual.values()}
        rows = []
        for source in self.sources:
            key = source.source_id
            candidates = suggestions[key]
            skipped = key in self._skipped
            replacement = self._manual.get(key)
            if replacement is None and not skipped:
                replacement = _unique_candidate(candidates, counts, reserved)
            rows.append(SourcePairing(source, candidates, replacement, skipped))
        return tuple(rows)

    def choose(self, source_id: str, path: str) -> bool:
        key = _path_key(path)
        if any(
            row.source.source_id != source_id and row.replacement and _path_key(row.replacement) == key
            for row in self.rows()
        ):
            return False
        self._manual[source_id] = path
        self._skipped.discard(source_id)
        return True

    def skip(self, source_id: str) -> None:
        self._manual.pop(source_id, None)
        self._skipped.add(source_id)


def _unique_candidate(paths: tuple[str, ...], counts: Counter[str], reserved: set[str]) -> str | None:
    if len(paths) == 1:
        key = _path_key(paths[0])
        if counts[key] == 1 and key not in reserved:
            return paths[0]
    return None


def _path_key(path: str) -> str:
    return normcase(normpath(path))
