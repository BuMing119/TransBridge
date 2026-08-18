"""FOMOD archive root discovery without implicit first-directory selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transbridge.fileops.archive import ArchiveExtractor, ExtractionResult

_PLUGIN_EXTENSIONS = {".esp", ".esm", ".esl"}


@dataclass(frozen=True, slots=True)
class RootCandidate:
    relative_path: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RootDetectionResult:
    candidates: tuple[RootCandidate, ...]
    selected: RootCandidate | None
    confirmation_required: bool


@dataclass(frozen=True, slots=True)
class FomodExtractionResult:
    extraction: ExtractionResult
    roots: RootDetectionResult


def detect_mod_roots(extracted_dir: str | Path) -> RootDetectionResult:
    """Score structural candidates and auto-select only an unambiguous root."""
    base = Path(extracted_dir).resolve(strict=True)
    candidates: list[RootCandidate] = []
    directories = [base, *(path for path in base.rglob("*") if path.is_dir())]
    for directory in directories:
        reasons: list[str] = []
        score = 0
        children = tuple(directory.iterdir())
        child_names = {child.name.casefold(): child for child in children}
        fomod = child_names.get("fomod")
        if fomod is not None and fomod.is_dir() and not fomod.is_symlink():
            module_config = fomod / "ModuleConfig.xml"
            info_xml = fomod / "info.xml"
            if module_config.is_file():
                score += 100
                reasons.append("fomod-module-config")
            elif info_xml.is_file():
                score += 80
                reasons.append("fomod-info")
        direct_plugins = [
            child for child in children if child.is_file() and child.suffix.casefold() in _PLUGIN_EXTENSIONS
        ]
        if direct_plugins:
            score += 60
            reasons.append("plugin")
        data_dir = child_names.get("data")
        if data_dir is not None and data_dir.is_dir() and not data_dir.is_symlink():
            if _contains_mod_marker(data_dir):
                score += 70
                reasons.append("data-directory")
        if score:
            relative = "." if directory == base else directory.relative_to(base).as_posix()
            candidates.append(RootCandidate(relative, score, tuple(reasons)))

    candidates = _remove_shadowed_candidates(candidates)
    ordered = tuple(sorted(candidates, key=lambda item: (-item.score, item.relative_path.casefold())))
    selected = ordered[0] if len(ordered) == 1 else None
    return RootDetectionResult(
        candidates=ordered,
        selected=selected,
        confirmation_required=len(ordered) > 1,
    )


def extract_fomod_archive(
    archive_path: str | Path,
    dest_dir: str | Path,
    *,
    extractor: ArchiveExtractor | None = None,
    progress=None,
    cancellation=None,
) -> FomodExtractionResult:
    """Securely extract first, then return an explicit root-selection plan."""
    extraction = (extractor or ArchiveExtractor()).extract(
        archive_path,
        dest_dir,
        progress=progress,
        cancellation=cancellation,
    )
    return FomodExtractionResult(extraction, detect_mod_roots(extraction.dest_dir))


def _contains_mod_marker(directory: Path) -> bool:
    for child in directory.iterdir():
        if child.is_file() and child.suffix.casefold() in _PLUGIN_EXTENSIONS:
            return True
        if child.is_dir() and child.name.casefold() == "fomod":
            return True
    return False


def _remove_shadowed_candidates(candidates: list[RootCandidate]) -> list[RootCandidate]:
    """A structural package root owns nested plugin/Data implementation paths."""
    by_path = {candidate.relative_path: candidate for candidate in candidates}
    retained: list[RootCandidate] = []
    for candidate in candidates:
        path = Path(candidate.relative_path)
        if path.name.casefold() == "data":
            parent_key = "." if path.parent == Path(".") else path.parent.as_posix()
            parent = by_path.get(parent_key)
            if parent is not None and "data-directory" in parent.reasons:
                continue
        ancestors = tuple(path.parents)
        if any(
            (ancestor_candidate := by_path.get("." if ancestor == Path(".") else ancestor.as_posix())) is not None
            and any(reason.startswith("fomod-") for reason in ancestor_candidate.reasons)
            for ancestor in ancestors
        ):
            continue
        retained.append(candidate)
    return retained
