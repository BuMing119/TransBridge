"""Render-session ownership for the Workbench translation table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.workbench.resident_rows import ResidentTableRows


@dataclass(frozen=True, slots=True)
class RenderSession:
    generation: int
    projection_revision: int | None
    entries: tuple[TranslationEntry, ...]
    content_scope: tuple[object, ...] | None = None


class TranslationTablePort(Protocol):
    def start_render(
        self,
        session: RenderSession,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping[str, str]],
    ) -> None: ...

    def start_logical_range(self, session, total, rows, entry_labels, label_library) -> None: ...


class TablePresenter:
    """Separate render generations from filter and projection revisions."""

    def __init__(self, view: TranslationTablePort) -> None:
        self._view = view
        self._generation = 0
        self._session = RenderSession(0, None, ())

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def session(self) -> RenderSession:
        return self._session

    def render(
        self,
        entries: Sequence[TranslationEntry],
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping[str, str]],
        *,
        projection_revision: int | None = None,
        content_scope: tuple[object, ...] | None = None,
    ) -> RenderSession:
        self._generation += 1
        self._session = RenderSession(
            self._generation,
            projection_revision,
            tuple(entries),
            content_scope,
        )
        self._view.start_render(self._session, entry_labels, label_library)
        return self._session

    def invalidate(self) -> None:
        self._generation += 1
        self._session = RenderSession(self._generation, None, ())

    def render_logical(self, total, rows, entry_labels, label_library, *, projection_revision, content_scope):
        self._generation += 1
        entries = rows.entries if isinstance(rows, ResidentTableRows) else tuple(rows.values())
        self._session = RenderSession(self._generation, projection_revision, entries, content_scope)
        self._view.start_logical_range(self._session, total, rows, entry_labels, label_library)
        return self._session

    def replace_entries(
        self, entries: Sequence[TranslationEntry], *, projection_revision: int | None = None
    ) -> RenderSession:
        """Replace data after the view verifies membership and order are unchanged."""
        self._session = RenderSession(
            self._generation, projection_revision, tuple(entries), self._session.content_scope
        )
        return self._session
