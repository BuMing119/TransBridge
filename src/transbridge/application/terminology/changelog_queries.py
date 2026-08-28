"""Frozen changelog manifests and document-bound streaming queries."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from enum import StrEnum
from typing import Protocol

from .identity import canonical_digest
from .models import (
    CanonicalChange,
    ChangeLogDocument,
    ChangeLogDocumentManifest,
    ChangeLogDocumentRef,
)
from .ports import Page, PageRequest


class ChangeLogSection(StrEnum):
    MESSAGES = "messages"
    CHANGES = "changes"
    DIAGNOSTICS = "diagnostics"
    CONFLICT_GROUP_IDS = "conflict_group_ids"
    NO_EVIDENCE_TERM_IDS = "no_evidence_term_ids"
    MANUAL_ACTION_IDS = "manual_action_ids"


def build_changelog_manifest(document: ChangeLogDocument) -> ChangeLogDocumentManifest:
    sections = _document_sections(document)
    return ChangeLogDocumentManifest(
        document.ref,
        document.version_ref,
        document.locale,
        document.schema_version,
        document.template_digest,
        tuple(
            (section.value, canonical_digest(values, namespace=f"terminology.changelog-section.{section.value}.v1"))
            for section, values in sections
        ),
        tuple((section.value, len(values)) for section, values in sections),
    )


class ChangeLogQuerySource(Protocol):
    def get_changelog_manifest(self, ref: ChangeLogDocumentRef) -> ChangeLogDocumentManifest: ...

    def list_changelog_messages(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[tuple[str, tuple[str, ...]]]: ...

    def list_changelog_changes(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[CanonicalChange]: ...

    def list_changelog_diagnostics(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...

    def list_changelog_conflict_group_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...

    def list_changelog_no_evidence_term_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...

    def list_changelog_manual_action_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...


class ChangeLogQueryService:
    def __init__(self, source: ChangeLogQuerySource) -> None:
        self._source = source

    def manifest(self, ref: ChangeLogDocumentRef) -> ChangeLogDocumentManifest:
        return self._source.get_changelog_manifest(ref)

    def messages(self, ref: ChangeLogDocumentRef, *, page_size: int = 1000) -> Iterator[tuple[str, tuple[str, ...]]]:
        return _iterate(self._source.list_changelog_messages, ref, page_size)

    def changes(self, ref: ChangeLogDocumentRef, *, page_size: int = 1000) -> Iterator[CanonicalChange]:
        return _iterate(self._source.list_changelog_changes, ref, page_size)

    def diagnostics(self, ref: ChangeLogDocumentRef, *, page_size: int = 1000) -> Iterator[str]:
        return _iterate(self._source.list_changelog_diagnostics, ref, page_size)

    def conflict_group_ids(self, ref: ChangeLogDocumentRef, *, page_size: int = 1000) -> Iterator[str]:
        return _iterate(self._source.list_changelog_conflict_group_ids, ref, page_size)

    def no_evidence_term_ids(self, ref: ChangeLogDocumentRef, *, page_size: int = 1000) -> Iterator[str]:
        return _iterate(self._source.list_changelog_no_evidence_term_ids, ref, page_size)

    def manual_action_ids(self, ref: ChangeLogDocumentRef, *, page_size: int = 1000) -> Iterator[str]:
        return _iterate(self._source.list_changelog_manual_action_ids, ref, page_size)


def _iterate[T](
    query: Callable[[ChangeLogDocumentRef, PageRequest], Page[T]],
    ref: ChangeLogDocumentRef,
    page_size: int,
) -> Iterator[T]:
    request = PageRequest(limit=page_size)
    while True:
        page = query(ref, request)
        yield from page.items
        if page.next_cursor is None:
            return
        request = PageRequest(limit=page_size, cursor=page.next_cursor)


def _document_sections(document: ChangeLogDocument) -> tuple[tuple[ChangeLogSection, tuple[object, ...]], ...]:
    return (
        (ChangeLogSection.MESSAGES, document.user_messages),
        (ChangeLogSection.CHANGES, document.changes),
        (ChangeLogSection.DIAGNOSTICS, document.diagnostics),
        (ChangeLogSection.CONFLICT_GROUP_IDS, document.conflict_group_ids),
        (ChangeLogSection.NO_EVIDENCE_TERM_IDS, document.no_evidence_term_ids),
        (ChangeLogSection.MANUAL_ACTION_IDS, document.manual_action_ids),
    )


__all__ = [
    "ChangeLogQueryService",
    "ChangeLogQuerySource",
    "ChangeLogSection",
    "build_changelog_manifest",
]
