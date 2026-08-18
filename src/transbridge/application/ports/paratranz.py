"""Typed application contracts for the ParaTranz remote service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ExternalServiceCategory(StrEnum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    TRANSPORT = "transport"
    INVALID_RESPONSE = "invalid_response"
    CANCELLED = "cancelled"


class ExternalServiceError(RuntimeError):
    """Stable, secret-free error projected across GUI, Agent and MCP."""

    def __init__(
        self,
        category: ExternalServiceCategory,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        safe_context: Mapping[str, str] | None = None,
    ) -> None:
        self.category = category
        self.status = status
        self.request_id = request_id
        self.retry_after = retry_after
        self.safe_context = tuple(sorted((safe_context or {}).items()))
        details = [message]
        if status is not None:
            details.append(f"status={status}")
        if request_id:
            details.append(f"request_id={request_id}")
        if self.safe_context:
            details.append("context=" + repr(dict(self.safe_context)))
        super().__init__("; ".join(details))


@dataclass(frozen=True, slots=True)
class ParaTranzProject:
    project_id: int
    name: str
    visibility: str | None = None
    member_count: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParaTranzProject:
        project_id = value.get("id")
        name = value.get("name")
        if isinstance(project_id, bool) or not isinstance(project_id, int):
            raise ValueError("ParaTranz project id must be an integer")
        if not isinstance(name, str) or not name:
            raise ValueError("ParaTranz project name must be a non-empty string")
        visibility = value.get("visibility")
        if visibility is not None and not isinstance(visibility, str):
            raise ValueError("ParaTranz project visibility must be a string or null")
        members = value.get("members", ())
        member_count = len(members) if isinstance(members, (list, tuple)) else 0
        return cls(project_id, name, visibility, member_count)


@dataclass(frozen=True, slots=True)
class ParaTranzEntry:
    remote_id: int | None
    key: str
    original: str
    translation: str
    context: str
    stage: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParaTranzEntry:
        remote_id = value.get("id")
        if remote_id is not None and (isinstance(remote_id, bool) or not isinstance(remote_id, int)):
            raise ValueError("ParaTranz entry id must be an integer or null")
        key = value.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("ParaTranz entry key must be a non-empty string")
        fields = {}
        for field in ("original", "translation", "context"):
            item = value.get(field, "")
            if not isinstance(item, str):
                raise ValueError(f"ParaTranz entry {field} must be a string")
            fields[field] = item
        stage = value.get("stage", 0)
        if isinstance(stage, bool) or not isinstance(stage, int):
            raise ValueError("ParaTranz entry stage must be an integer")
        return cls(remote_id, key, fields["original"], fields["translation"], fields["context"], stage)

    def to_remote_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "original": self.original,
            "translation": self.translation,
            "context": self.context,
            "stage": self.stage,
        }


@dataclass(frozen=True, slots=True)
class ParaTranzUploadHistory:
    revision_id: int | str
    status: str | None
    filename: str | None
    created_at: str | None
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParaTranzUploadHistory:
        revision_id = value.get("id")
        if isinstance(revision_id, bool) or not isinstance(revision_id, (int, str)):
            raise ValueError("ParaTranz revision id must be an integer or string")
        return cls(
            revision_id,
            value.get("status") if isinstance(value.get("status"), str) else None,
            value.get("filename") if isinstance(value.get("filename"), str) else None,
            value.get("createdAt") if isinstance(value.get("createdAt"), str) else None,
            dict(value),
        )


class CancellationPort(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


class ParaTranzPort(Protocol):
    def list_projects(
        self, *, uid: str | int | None = None, cancellation: CancellationPort | None = None
    ) -> tuple[ParaTranzProject, ...]: ...

    def get_project(self, project_id: int, *, cancellation: CancellationPort | None = None) -> ParaTranzProject: ...

    def list_entries(
        self, project_id: int, *, limit: int, cancellation: CancellationPort | None = None
    ) -> tuple[ParaTranzEntry, ...]: ...

    def upsert_entry(
        self,
        project_id: int,
        entry: ParaTranzEntry,
        *,
        force_overwrite: bool = False,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzEntry: ...

    def delete_entry(
        self,
        project_id: int,
        remote_id: int,
        *,
        cancellation: CancellationPort | None = None,
    ) -> None: ...

    def list_upload_history(
        self, project_id: int, *, limit: int, cancellation: CancellationPort | None = None
    ) -> tuple[ParaTranzUploadHistory, ...]: ...

    def trigger_export(self, project_id: int, *, cancellation: CancellationPort | None = None) -> Mapping[str, Any]: ...

    def get_artifacts(
        self, project_id: int, *, cancellation: CancellationPort | None = None
    ) -> tuple[Mapping[str, Any], ...]: ...

    def download_artifact(
        self,
        project_id: int,
        destination: str,
        *,
        cancellation: CancellationPort | None = None,
    ) -> str: ...
