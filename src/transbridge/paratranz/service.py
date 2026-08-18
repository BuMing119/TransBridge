"""Typed ParaTranz service adapter over the legacy endpoint classes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceCategory,
    ExternalServiceError,
    ParaTranzEntry,
    ParaTranzProject,
    ParaTranzUploadHistory,
)

from .api.paratranz_export_api import ParatranzExportAPI
from .api.paratranz_history_api import ParatranzHistoryAPI
from .api.paratranz_project_api import ParatranzProjectAPI
from .api.paratranz_strings_api import ParatranzStringsAPI
from .config_manager import ParatranzConfig


class ParaTranzService:
    """Single typed facade consumed by application and Agent entry adapters."""

    def __init__(
        self,
        projects: ParatranzProjectAPI,
        strings: ParatranzStringsAPI,
        history: ParatranzHistoryAPI,
        exports: ParatranzExportAPI,
    ) -> None:
        self._projects = projects
        self._strings = strings
        self._history = history
        self._exports = exports

    @classmethod
    def from_config(cls, config: ParatranzConfig | object) -> ParaTranzService:
        return cls(
            ParatranzProjectAPI(config),
            ParatranzStringsAPI(config),
            ParatranzHistoryAPI(config),
            ParatranzExportAPI(config),
        )

    @staticmethod
    def _items(payload: Any, *keys: str) -> list[Mapping[str, Any]]:
        if isinstance(payload, list):
            values = payload
        elif isinstance(payload, Mapping):
            selected = next((payload[key] for key in keys if key in payload), None)
            if selected is None and all(isinstance(value, Mapping) for value in payload.values()):
                values = list(payload.values())
            else:
                values = selected
        else:
            values = None
        if not isinstance(values, list) or not all(isinstance(item, Mapping) for item in values):
            raise ExternalServiceError(
                ExternalServiceCategory.INVALID_RESPONSE,
                "ParaTranz collection response is invalid",
            )
        return values

    @staticmethod
    def _mapping(payload: Any, operation: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ExternalServiceError(
                ExternalServiceCategory.INVALID_RESPONSE,
                f"ParaTranz {operation} response is invalid",
            )
        return payload

    @staticmethod
    def _typed(factory, payload: Mapping[str, Any], operation: str):
        try:
            return factory(payload)
        except (TypeError, ValueError) as exc:
            raise ExternalServiceError(
                ExternalServiceCategory.INVALID_RESPONSE,
                f"ParaTranz {operation} schema is invalid ({type(exc).__name__})",
            ) from None

    def list_projects(
        self,
        *,
        uid: str | int | None = None,
        cancellation: CancellationPort | None = None,
    ) -> tuple[ParaTranzProject, ...]:
        payload = self._projects.list_projects(page=1, page_size=200, uid=uid, cancellation=cancellation)
        return tuple(
            self._typed(ParaTranzProject.from_mapping, item, "project")
            for item in self._items(payload, "projects", "results")
        )

    def get_project(
        self,
        project_id: int,
        *,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzProject:
        payload = self._projects.get_project(project_id, cancellation=cancellation)
        return self._typed(ParaTranzProject.from_mapping, self._mapping(payload, "project"), "project")

    def list_entries(
        self,
        project_id: int,
        *,
        limit: int,
        cancellation: CancellationPort | None = None,
    ) -> tuple[ParaTranzEntry, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        items: list[Mapping[str, Any]] = []
        page = 1
        while len(items) < limit:
            page_size = min(limit - len(items), 800)
            payload = self._strings.list_strings(
                project_id,
                page=page,
                page_size=page_size,
                cancellation=cancellation,
            )
            current = self._items(payload, "results", "strings")
            items.extend(current[:page_size])
            if len(current) < page_size:
                break
            page += 1
        return tuple(self._typed(ParaTranzEntry.from_mapping, item, "entry") for item in items[:limit])

    def upsert_entry(
        self,
        project_id: int,
        entry: ParaTranzEntry,
        *,
        force_overwrite: bool = False,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzEntry:
        matches = tuple(
            item
            for item in self.list_entries(project_id, limit=800, cancellation=cancellation)
            if item.key == entry.key
        )
        if len(matches) > 1:
            raise ExternalServiceError(
                ExternalServiceCategory.CONFLICT,
                "ParaTranz key resolves to multiple remote entries",
                safe_context={"key": entry.key},
            )
        if matches:
            existing = matches[0]
            if not force_overwrite and existing.translation and existing.translation != entry.translation:
                raise ExternalServiceError(
                    ExternalServiceCategory.CONFLICT,
                    "ParaTranz entry has a different remote translation",
                    safe_context={"key": entry.key, "remote_id": str(existing.remote_id)},
                )
            if existing.remote_id is None:
                raise ExternalServiceError(
                    ExternalServiceCategory.INVALID_RESPONSE,
                    "ParaTranz existing entry has no remote id",
                    safe_context={"key": entry.key},
                )
            payload = self._strings.update_string(
                project_id,
                existing.remote_id,
                entry.to_remote_payload(),
                cancellation=cancellation,
            )
            if payload is None:
                return ParaTranzEntry(
                    existing.remote_id,
                    entry.key,
                    entry.original,
                    entry.translation,
                    entry.context,
                    entry.stage,
                )
        else:
            payload = self._strings.create_string(project_id, entry.to_remote_payload(), cancellation=cancellation)
        return self._typed(
            ParaTranzEntry.from_mapping,
            self._mapping(payload, "upsert entry"),
            "upsert entry",
        )

    def delete_entry(
        self,
        project_id: int,
        remote_id: int,
        *,
        cancellation: CancellationPort | None = None,
    ) -> None:
        if isinstance(remote_id, bool) or not isinstance(remote_id, int) or remote_id < 1:
            raise ValueError("remote_id must be a positive integer")
        self._strings.delete_string(project_id, remote_id, cancellation=cancellation)

    def list_upload_history(
        self,
        project_id: int,
        *,
        limit: int,
        cancellation: CancellationPort | None = None,
    ) -> tuple[ParaTranzUploadHistory, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        payload = self._history.list_file_revisions(
            project_id,
            page=1,
            page_size=min(limit, 200),
            cancellation=cancellation,
        )
        return tuple(
            self._typed(ParaTranzUploadHistory.from_mapping, item, "upload history")
            for item in self._items(payload, "results", "revisions")[:limit]
        )

    def trigger_export(
        self,
        project_id: int,
        *,
        cancellation: CancellationPort | None = None,
    ) -> Mapping[str, Any]:
        return self._mapping(self._exports.trigger_export(project_id, cancellation=cancellation), "export")

    def get_artifacts(
        self,
        project_id: int,
        *,
        cancellation: CancellationPort | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        payload = self._exports.get_artifacts(project_id, cancellation=cancellation)
        if isinstance(payload, Mapping) and not any(key in payload for key in ("results", "artifacts")):
            return (dict(payload),)
        return tuple(dict(item) for item in self._items(payload, "results", "artifacts"))

    def download_artifact(
        self,
        project_id: int,
        destination: str,
        *,
        cancellation: CancellationPort | None = None,
    ) -> str:
        return self._exports.download_artifacts(
            project_id,
            destination,
            cancellation=cancellation,
        )

    def close(self) -> None:
        for client in (self._exports, self._history, self._strings, self._projects):
            client.close()
