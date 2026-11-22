# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Prepare workbench label changes and submit them through the project command port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import random
import uuid

from transbridge.application.io.identity import EntryKey


def label_catalog(library: Mapping[str, Mapping], counts: Mapping[str, int]) -> list[dict]:
    """Return the defined labels in workbench order, including currently unused labels."""
    return [
        {"id": key, "name": info.get("name", key), "color": info.get("color", ""), "count": counts.get(key, 0)}
        for key, info in library.items()
    ]


class LabelAliases:
    """Detect display IDs shared by distinct entries before addressing projected labels."""

    def __init__(self):
        self.identities = {}
        self.conflicts = set()

    def observe(self, entry, key):
        if self.identities.setdefault(key, entry.identity) != entry.identity:
            self.conflicts.add(key)

    def require_unique(self, targets):
        if self.conflicts.intersection(targets):
            raise ValueError("所选标签范围内有重复的词条显示 ID，无法唯一核对标签，操作未提交。")


class WorkbenchLabels:
    """Create detached mutations and commit them through one authoritative port."""

    def __init__(
        self,
        commit: Callable[[dict[str, set[str]], dict[str, dict], dict[EntryKey, set[str]]], bool],
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

    def toggle_many(
        self,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping],
        targets: Mapping[str, EntryKey],
        label_id: str,
        checked: bool,
    ) -> dict[str, set[str]] | None:
        updated, library = self.copies(entry_labels, label_library)
        exact_changes = {}
        for key, identity in targets.items():
            if not self.would_change(updated.get(key, ()), label_id, checked):
                continue
            labels = updated.setdefault(key, set())
            labels.add(label_id) if checked else labels.discard(label_id)
            exact_changes[identity] = labels
        return updated if not exact_changes or self._commit(updated, library, exact_changes) else None

    @staticmethod
    def would_change(membership, label_id, checked):
        return (label_id in membership) != checked

    def create_many(
        self,
        entry_labels: Mapping[str, set[str]],
        label_library: Mapping[str, Mapping],
        targets: Mapping[str, EntryKey],
        name: str,
        colors: tuple[str, ...],
    ) -> tuple[dict[str, set[str]], dict[str, dict]] | None:
        updated, library = self.copies(entry_labels, label_library)
        label_id = uuid.uuid4().hex[:8]
        library[label_id] = {"name": name, "color": random.choice(colors)}
        for key in targets:
            updated.setdefault(key, set()).add(label_id)
        changes = {identity: updated[key] for key, identity in targets.items()}
        return (updated, library) if self._commit(updated, library, changes) else None
