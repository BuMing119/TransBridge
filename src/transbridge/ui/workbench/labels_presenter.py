"""Label mutation coordinator for the Workbench preview."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import random
import uuid


class LabelsPresenter:
    """Create detached mutations and commit them through one authoritative port."""

    def __init__(
        self,
        commit: Callable[[dict[str, set[str]], dict[str, dict]], bool],
    ) -> None:
        self._commit = commit

    @staticmethod
    def copies(
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping],
    ) -> tuple[dict[str, set[str]], dict[str, dict]]:
        return (
            {key: set(value) for key, value in entry_labels.items()},
            {key: dict(value) for key, value in label_library.items()},
        )

    def toggle(
        self,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping],
        entry_id: str,
        label_id: str,
        checked: bool,
    ) -> dict[str, set[str]] | None:
        updated, library = self.copies(entry_labels, label_library)
        labels = updated.setdefault(entry_id, set())
        labels.add(label_id) if checked else labels.discard(label_id)
        return updated if self._commit(updated, library) else None

    def create(
        self,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping],
        entry_id: str,
        name: str,
        colors: tuple[str, ...],
    ) -> tuple[dict[str, set[str]], dict[str, dict]] | None:
        updated, library = self.copies(entry_labels, label_library)
        label_id = uuid.uuid4().hex[:8]
        library[label_id] = {"name": name, "color": random.choice(colors)}
        updated.setdefault(entry_id, set()).add(label_id)
        return (updated, library) if self._commit(updated, library) else None
