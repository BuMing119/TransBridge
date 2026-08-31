"""Qt-free presentation state for destructive/remote operation plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OperationKind(StrEnum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    WRITE = "write"
    FOMOD = "fomod"


class EditableControl(StrEnum):
    TEXT = "text"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    REMOTE_PROJECT = "remote_project"


@dataclass(frozen=True, slots=True)
class EditableFieldState:
    field_id: str
    label: str
    value: str
    required: bool = False
    enabled: bool = True
    control: EditableControl = EditableControl.TEXT
    display_value: str = ""
    options: tuple[tuple[str, str], ...] = ()
    help_text: str = ""

    def __post_init__(self) -> None:
        if not self.field_id.strip() or not self.label.strip():
            raise ValueError("editable operation fields require an id and label")
        if len({value for value, _label in self.options}) != len(self.options):
            raise ValueError("editable operation field option values must be unique")


@dataclass(frozen=True, slots=True)
class OperationPlanViewState:
    session_id: str
    revision: int
    kind: OperationKind
    title: str
    target: str
    scope_summary: str
    mode_summary: str
    conflict_summary: str
    backup_summary: str
    estimated_impact: tuple[tuple[str, int], ...]
    editable_fields: tuple[EditableFieldState, ...] = ()
    warnings: tuple[str, ...] = ()
    request_digest: str = ""
    submit_enabled: bool = False
    submit_disabled_reason: str = "请先运行预检"

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.title.strip() or not self.target.strip():
            raise ValueError("operation plan identity, title, and target must not be empty")
        if self.revision < 1:
            raise ValueError("operation plan revision must be positive")
        if len({key for key, _ in self.estimated_impact}) != len(self.estimated_impact):
            raise ValueError("estimated impact keys must be unique")
        if any(value < 0 for _, value in self.estimated_impact):
            raise ValueError("estimated impact values must not be negative")
        if self.request_digest and len(self.request_digest) != 64:
            raise ValueError("request digest must be a SHA-256 digest")
        if not self.submit_enabled and not self.submit_disabled_reason.strip():
            raise ValueError("disabled submit state requires a reason")
