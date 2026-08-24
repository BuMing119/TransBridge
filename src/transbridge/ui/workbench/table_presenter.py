"""Render-session ownership for the Workbench translation table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from transbridge.converter.translation_entry import TranslationEntry


@dataclass(frozen=True, slots=True)
class RenderSession:
    generation: int
    projection_revision: int | None
    entries: tuple[TranslationEntry, ...]


class TranslationTablePort(Protocol):
    def start_render(
        self,
        session: RenderSession,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping[str, str]],
    ) -> None: ...


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
    ) -> RenderSession:
        self._generation += 1
        self._session = RenderSession(
            self._generation,
            projection_revision,
            tuple(entries),
        )
        self._view.start_render(self._session, entry_labels, label_library)
        return self._session

    def invalidate(self) -> None:
        self._generation += 1
        self._session = RenderSession(self._generation, None, ())
