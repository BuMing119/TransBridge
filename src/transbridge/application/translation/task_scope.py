"""Immutable source inputs shared by translation task entry points."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceTask:
    key: str
    label: str
    esp_path: str | None
    collection: object
    translate_entries: tuple
    polish_entries: tuple
    postprocess_entries: tuple = ()

    @property
    def entries(self) -> tuple:
        return self.translate_entries + self.polish_entries + self.postprocess_entries
