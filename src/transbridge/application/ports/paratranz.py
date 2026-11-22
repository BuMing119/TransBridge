# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Typed application contracts for the ParaTranz remote service."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
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
    INVALID_REQUEST = "invalid_request"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    LOCKED = "locked"
    FAILED_DEPENDENCY = "failed_dependency"


class ExternalServiceError(RuntimeError):
    """Stable, secret-free error projected across GUI, Agent and MCP."""

    def __init__(
        self,
        category: ExternalServiceCategory,
        message: str,
        *,
        status: int | None = None,
        request_id: str | None = None,
        remote_code: str | None = None,
        retry_after: float | None = None,
        safe_context: Mapping[str, str] | None = None,
    ) -> None:
        self.category = category
        self.status = status
        self.request_id = request_id
        self.remote_code = remote_code
        self.retry_after = retry_after
        self.safe_context = tuple(sorted((safe_context or {}).items()))
        details = [message]
        if status is not None:
            details.append(f"status={status}")
        if request_id:
            details.append(f"request_id={request_id}")
        if remote_code:
            details.append(f"remote_code={remote_code}")
        if self.safe_context:
            details.append("context=" + repr(dict(self.safe_context)))
        super().__init__("; ".join(details))


class ExportJobStatus(IntEnum):
    PENDING = 0
    SUCCEEDED = 1
    FAILED = 2


class ExportJobFailed(RuntimeError):
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        super().__init__(f"ParaTranz 导出任务 {job_id} 执行失败。")


@dataclass(frozen=True, slots=True)
class ParaTranzExportJob:
    job_id: int
    status: ExportJobStatus

    @classmethod
    def from_mapping(cls, payload: object, *, expected_id: int | None = None) -> ParaTranzExportJob:
        job_id = payload.get("id") if isinstance(payload, Mapping) else None
        status = payload.get("status") if isinstance(payload, Mapping) else None
        if (
            type(job_id) is not int
            or job_id < 1
            or (expected_id is not None and job_id != expected_id)
            or type(status) is not int
            or status not in (0, 1, 2)
        ):
            raise ExternalServiceError(
                ExternalServiceCategory.INVALID_RESPONSE, "ParaTranz export job ID or status is invalid"
            )
        return cls(job_id, ExportJobStatus(status))

    def raise_if_failed(self) -> None:
        if self.status is ExportJobStatus.FAILED:
            raise ExportJobFailed(self.job_id)


@dataclass(frozen=True, slots=True)
class ParaTranzProject:
    project_id: int
    name: str
    visibility: str | None = None
    member_count: int | None = None

    @staticmethod
    def overview_fields(value: Mapping[str, Any]) -> tuple[str | None, int | None]:
        privacy = value.get("privacy")
        visibility = {0: "public", 1: "internal", 2: "private"}.get(privacy) if type(privacy) is int else None
        if visibility is None and isinstance(value.get("visibility"), str):
            visibility = value["visibility"]
        members = value.get("members")
        if type(members) is int and members >= 0:
            return visibility, members
        return visibility, len(members) if isinstance(members, (list, tuple)) else None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParaTranzProject:
        project_id = value.get("id")
        name = value.get("name")
        if isinstance(project_id, bool) or not isinstance(project_id, int):
            raise ValueError("ParaTranz project id must be an integer")
        if not isinstance(name, str) or not name:
            raise ValueError("ParaTranz project name must be a non-empty string")
        if value.get("visibility") is not None and not isinstance(value["visibility"], str):
            raise ValueError("ParaTranz project visibility must be a string or null")
        return cls(project_id, name, *cls.overview_fields(value))


@dataclass(frozen=True, slots=True)
class ParaTranzEntry:
    remote_id: int | None
    key: str
    original: str
    translation: str
    context: str
    stage: int
    file_id: int | None = None

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
        file_id = value.get("fileId", value.get("file"))
        if isinstance(file_id, Mapping):
            file_id = file_id.get("id")
        if file_id is not None and (isinstance(file_id, bool) or not isinstance(file_id, int) or file_id < 1):
            raise ValueError("ParaTranz file id must be a positive integer or null")
        return cls(remote_id, key, fields["original"], fields["translation"], fields["context"], stage, file_id)

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
    revision_id: int | str | None
    status: str | None
    filename: str | None
    created_at: str | None
    raw: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParaTranzUploadHistory:
        revision_id = value.get("id")
        if revision_id is not None and (isinstance(revision_id, bool) or not isinstance(revision_id, (int, str))):
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

    def iter_entries(
        self, project_id: int, *, limit: int, cancellation: CancellationPort | None = None
    ) -> Iterator[ParaTranzEntry]: ...

    def update_entry_context(
        self,
        project_id: int,
        entry: ParaTranzEntry,
        *,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzEntry: ...

    def update_entry_translation(
        self,
        project_id: int,
        entry: ParaTranzEntry,
        *,
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
        self, project_id: int, *, page: int = 1, cancellation: CancellationPort | None = None
    ) -> tuple[ParaTranzUploadHistory, ...]: ...

    def trigger_export(self, project_id: int, *, cancellation: CancellationPort | None = None) -> Mapping[str, Any]: ...

    def get_job(
        self, project_id: int, job_id: int, *, cancellation: CancellationPort | None = None
    ) -> Mapping[str, Any]: ...

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
