"""Run-frozen policy for filtering proven ParaTranz project-version echoes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from transbridge.application.terminology.effective import EffectiveSnapshotStatus
from transbridge.application.terminology.identity import normalize_original, normalize_translation
from transbridge.application.translation.terminology_run_snapshot import (
    FrozenTerminologyRunSnapshot,
    TerminologyRunSnapshotRef,
)

from .term_formats import TermEntry


class LegacyTermFilterPort(Protocol):
    def filter_entries(self, source: str, entries: Iterable[TermEntry]) -> tuple[TermEntry, ...]: ...


@dataclass(frozen=True, slots=True)
class ConfirmedTerminologyEchoLink:
    """Minimal S02 adapter payload proving one remote item is a local-version echo."""

    local_project_id: str
    local_variant_id: str
    remote_target_id: str
    remote_term_id: str
    local_term_id: str
    local_version_id: str
    local_content_digest: str
    original: str
    translation: str
    outcome: str = "confirmed"


@dataclass(frozen=True, slots=True)
class FrozenTerminologyEchoLinks:
    """One baseline revision captured with the AI run, never queried by a batch."""

    local_project_id: str
    local_variant_id: str
    remote_target_id: str
    revision: str
    links: tuple[ConfirmedTerminologyEchoLink, ...] = ()
    available: bool = True
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "links", tuple(self.links))
        if not all(
            value.strip()
            for value in (self.local_project_id, self.local_variant_id, self.remote_target_id, self.revision)
        ):
            raise ValueError("frozen terminology echo links require line, target, and revision identities")
        if not self.available and not self.diagnostic:
            raise ValueError("unavailable terminology echo links require a diagnostic")


class ProjectTerminologyEchoFilter:
    """Remove only confirmed remote echoes for the exact frozen local version."""

    def __init__(
        self,
        run_snapshot: FrozenTerminologyRunSnapshot,
        echo_links: FrozenTerminologyEchoLinks,
    ) -> None:
        ref = run_snapshot.ref
        if ref.status is not EffectiveSnapshotStatus.READY:
            raise ValueError("ParaTranz echo filtering requires a ready project terminology version")
        if (echo_links.local_project_id, echo_links.local_variant_id) != (
            ref.local_project_id,
            ref.local_variant_id,
        ):
            raise ValueError("terminology echo baseline belongs to another Project/Variant")
        self._run_snapshot = run_snapshot
        self._echo_links = echo_links
        self._proven_remote_ids = self._build_proven_remote_ids(ref)

    @property
    def diagnostic(self) -> str | None:
        return self._echo_links.diagnostic

    @property
    def baseline_revision(self) -> str:
        return self._echo_links.revision

    def filter_entries(self, source: str, entries: Iterable[TermEntry]) -> tuple[TermEntry, ...]:
        values = tuple(entries)
        if source.casefold() != "paratranz":
            return values
        if not self._echo_links.available:
            # A ready local version remains authoritative. If its baseline cannot
            # be verified, do not promote an unclassifiable remote copy into the
            # run as an independent authority.
            return ()
        return tuple(entry for entry in values if not self._is_proven_echo(entry))

    def _build_proven_remote_ids(self, ref: TerminologyRunSnapshotRef) -> dict[str, ConfirmedTerminologyEchoLink]:
        decisions = {item.term_id: item for item in self._run_snapshot.decisions}
        result: dict[str, ConfirmedTerminologyEchoLink] = {}
        for link in self._echo_links.links:
            if link.outcome != "confirmed":
                continue
            if (link.local_project_id, link.local_variant_id) != (ref.local_project_id, ref.local_variant_id):
                continue
            if link.remote_target_id != self._echo_links.remote_target_id:
                continue
            if link.local_version_id != ref.version_id or link.local_content_digest != ref.content_digest:
                continue
            decision = decisions.get(link.local_term_id)
            if decision is None or normalize_original(decision.original) != normalize_original(link.original):
                continue
            if not decision.suppressed and normalize_translation(decision.translation) != normalize_translation(
                link.translation
            ):
                continue
            result[str(link.remote_term_id)] = link
        return result

    def _is_proven_echo(self, entry: TermEntry) -> bool:
        if entry.external_id is None:
            return False
        link = self._proven_remote_ids.get(str(entry.external_id))
        if link is None:
            return False
        return normalize_original(entry.term) == normalize_original(link.original) and normalize_translation(
            entry.translation
        ) == normalize_translation(link.translation)


__all__ = [
    "ConfirmedTerminologyEchoLink",
    "FrozenTerminologyEchoLinks",
    "LegacyTermFilterPort",
    "ProjectTerminologyEchoFilter",
]
