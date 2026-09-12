"""Storage boundary for full histories; persistence never schedules work."""

from __future__ import annotations

from typing import Protocol

from .transcript import AttachmentRef, TranscriptManifest, TranscriptMessage


class TranscriptStorePort(Protocol):
    def append(
        self, session_id: str, manifest: TranscriptManifest, messages: tuple[TranscriptMessage, ...]
    ) -> TranscriptManifest: ...

    def read(self, session_id: str, manifest: TranscriptManifest) -> tuple[TranscriptMessage, ...]: ...

    def write_artifact(self, session_id: str, data: bytes) -> AttachmentRef: ...

    def read_artifact(self, session_id: str, reference: AttachmentRef) -> bytes: ...

    def validate(self, session_id: str, manifest: TranscriptManifest) -> None: ...
