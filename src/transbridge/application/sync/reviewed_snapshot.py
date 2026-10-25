"""Immutable remote input shared by download review, authorization and commit."""

from dataclasses import dataclass

from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import CancellationPort

from .models import RemoteEntrySnapshot


@dataclass(frozen=True, slots=True)
class ReviewedDownloadSnapshot:
    project_id: int
    namespace: SourceNamespace
    entries: tuple[RemoteEntrySnapshot, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        if any(entry.entry_key.namespace != self.namespace for entry in self.entries):
            raise ValueError("download snapshot contains another source namespace")

    def fetch(
        self,
        project_id: int,
        namespace: SourceNamespace,
        *,
        limit: int,
        cancellation: CancellationPort | None = None,
    ) -> tuple[RemoteEntrySnapshot, ...]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if (project_id, namespace) != (self.project_id, self.namespace):
            raise ValueError("reviewed download snapshot belongs to another target")
        if limit < 1 or len(self.entries) > limit:
            raise ValueError("reviewed download snapshot exceeds the requested limit")
        return self.entries
