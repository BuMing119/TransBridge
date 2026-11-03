"""File classification contracts and retained ordinary-upload compatibility data."""

from dataclasses import dataclass

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.dialogue_order_evidence import DialogueOrderEvidence


@dataclass(frozen=True, slots=True)
class UploadSource:
    source_id: str
    label: str
    entries: tuple[TranslationEntry, ...]
    dialogue_order: DialogueOrderEvidence | None = None
    classification_entries: tuple[TranslationEntry, ...] | None = None


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    remote_id: int
    file_id: int
    key: str
    original: str
    translation: str
    context: str
    stage: int


@dataclass(frozen=True, slots=True)
class RemoteFile:
    file_id: int
    path: str
    entries: tuple[RemoteEntry, ...]


@dataclass(frozen=True, slots=True)
class CategorizedFile:
    source_id: str
    category_id: str
    path: str
    entries: tuple[TranslationEntry, ...]


class FileCreationRejected(ValueError):
    """The server explicitly rejected the file contents before importing entries."""
