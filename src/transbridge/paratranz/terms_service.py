"""Typed ParaTranz terminology facade over the legacy endpoint adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

import requests

from transbridge.ai_translator.term_formats import term_entry_from_mapping, term_entry_to_paratranz_dict
from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceCategory,
    ExternalServiceError,
)
from transbridge.application.ports.paratranz_terms import (
    ParaTranzTerm,
    ParaTranzTermPage,
    ParaTranzTermSnapshot,
    ParaTranzTermWrite,
    ParaTranzTermWriteResult,
    TermWriteOperation,
    TermWriteStatus,
)
from transbridge.application.tasks import TaskCancelled

from .api.paratranz_terms_api import ParatranzTermsAPI
from .config_manager import ParatranzConfig

_WRITABLE_FIELDS = frozenset({"term", "translation", "variants", "caseSensitive", "pos", "note"})
_IDENTITY_FIELDS = frozenset({"id", "external_id"})
_REVISION_HEADERS = ("ETag", "X-Snapshot-Revision", "X-Revision")
_REVISION_FIELDS = ("revision", "etag", "snapshotRevision")


def _canonical_digest(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _invalid(message: str) -> ExternalServiceError:
    return ExternalServiceError(ExternalServiceCategory.INVALID_RESPONSE, message)


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _header(headers: Mapping[str, Any], *names: str) -> str | None:
    folded = {str(key).casefold(): value for key, value in headers.items()}
    for name in names:
        value = folded.get(name.casefold())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class ParaTranzTermsService:
    """Map raw terminology HTTP responses into the application terminology port."""

    def __init__(
        self,
        terms: ParatranzTermsAPI,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._terms = terms
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_config(cls, config: ParatranzConfig | object) -> ParaTranzTermsService:
        return cls(ParatranzTermsAPI(config))

    @staticmethod
    def _raise_if_cancelled(cancellation: CancellationPort | None) -> None:
        if cancellation is not None:
            cancellation.raise_if_cancelled()

    @classmethod
    def _raise_if_cancelled_after_write(cls, cancellation: CancellationPort | None) -> None:
        try:
            cls._raise_if_cancelled(cancellation)
        except TaskCancelled as exc:
            raise ExternalServiceError(
                ExternalServiceCategory.CANCELLED,
                "terminology write cancellation crossed the dispatch boundary; reconcile is required",
            ) from exc

    @staticmethod
    def _response(value: Any, operation: str) -> tuple[Any, Mapping[str, Any], str | None]:
        if isinstance(value, requests.Response):
            headers: Mapping[str, Any] = value.headers
            request_id = _header(headers, "X-Request-ID", "X-Correlation-ID")
            if value.status_code == 204 or not value.content.strip():
                return None, headers, request_id
            try:
                payload = value.json()
            except ValueError:
                raise _invalid(f"ParaTranz {operation} response contains malformed JSON") from None
            return payload, headers, request_id
        return value, {}, None

    @staticmethod
    def _collection(payload: Any) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
        if isinstance(payload, list):
            values = payload
            wrapper: Mapping[str, Any] = {}
        elif isinstance(payload, Mapping):
            wrapper = payload
            selected = next(
                (payload[key] for key in ("terms", "results", "items", "data") if key in payload),
                None,
            )
            values = selected
        else:
            raise _invalid("ParaTranz terms collection response is invalid")
        if not isinstance(values, list) or not all(isinstance(item, Mapping) for item in values):
            raise _invalid("ParaTranz terms collection response is invalid")
        return values, wrapper

    @staticmethod
    def _revision(payload: Any, headers: Mapping[str, Any]) -> str | None:
        header_revision = _header(headers, *_REVISION_HEADERS)
        if header_revision:
            return header_revision
        if isinstance(payload, Mapping):
            for key in _REVISION_FIELDS:
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            meta = payload.get("meta")
            if isinstance(meta, Mapping):
                for key in _REVISION_FIELDS:
                    value = meta.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return None

    @staticmethod
    def _has_next(wrapper: Mapping[str, Any], *, page: int, page_size: int, count: int) -> bool:
        for key in ("hasNext", "has_next"):
            value = wrapper.get(key)
            if isinstance(value, bool):
                return value
        pagination = wrapper.get("pagination")
        metadata = pagination if isinstance(pagination, Mapping) else wrapper
        current_page = metadata.get("page", page)
        total_pages = metadata.get("totalPages", metadata.get("total_pages"))
        if (
            isinstance(current_page, int)
            and not isinstance(current_page, bool)
            and isinstance(total_pages, int)
            and not isinstance(total_pages, bool)
        ):
            return current_page < total_pages
        total = metadata.get("total")
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            return page * page_size < total
        return count == page_size

    @staticmethod
    def _term(item: Mapping[str, Any]) -> ParaTranzTerm:
        remote_id = item.get("id", item.get("external_id"))
        if isinstance(remote_id, bool) or not isinstance(remote_id, int) or remote_id < 1:
            raise _invalid("ParaTranz term response has no valid remote id")
        entry = term_entry_from_mapping(item, source="paratranz")
        if entry is None:
            raise _invalid("ParaTranz term response has no canonical term and translation")
        entry.external_id = remote_id
        revision = None
        for field in _REVISION_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                revision = value.strip()
                break
        readonly_metadata = {
            str(key): value
            for key, value in item.items()
            if key not in _WRITABLE_FIELDS and key not in _IDENTITY_FIELDS
        }
        return ParaTranzTerm(
            remote_id=remote_id,
            entry=entry,
            server_revision=revision,
            observed_digest=_canonical_digest(item),
            readonly_metadata=readonly_metadata,
        )

    def _page(
        self,
        project_id: int,
        page: int,
        page_size: int,
        cancellation: CancellationPort | None,
    ) -> ParaTranzTermPage:
        raw = self._terms.list_terms(
            project_id,
            page=page,
            page_size=page_size,
            cancellation=cancellation,
            raw_response=True,
        )
        payload, headers, _ = self._response(raw, "list terms")
        values, wrapper = self._collection(payload)
        items = tuple(self._term(item) for item in values)
        page_remote_ids = [item.remote_id for item in items]
        if len(page_remote_ids) != len(set(page_remote_ids)):
            raise _invalid("ParaTranz terms pagination returned duplicate remote ids")
        return ParaTranzTermPage(
            items=items,
            page=page,
            page_size=page_size,
            has_next=self._has_next(wrapper, page=page, page_size=page_size, count=len(items)),
            snapshot_revision=self._revision(payload, headers),
            page_digest=_canonical_digest(values),
        )

    def snapshot_terms(
        self,
        project_id: int,
        *,
        page_size: int = 200,
        max_terms: int = 100_000,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermSnapshot:
        project_id = _positive_integer(project_id, "project_id")
        page_size = _positive_integer(page_size, "page_size")
        max_terms = _positive_integer(max_terms, "max_terms")
        if page_size > 800:
            raise ValueError("page_size must not exceed 800")

        items: list[ParaTranzTerm] = []
        remote_ids: set[int] = set()
        page_digests: set[str] = set()
        revisions: list[str | None] = []
        diagnostics: list[str] = []
        page_number = 1
        stable = True
        while True:
            self._raise_if_cancelled(cancellation)
            page = self._page(project_id, page_number, page_size, cancellation)
            if page.page_digest in page_digests:
                raise _invalid("ParaTranz terms pagination repeated a page")
            page_digests.add(page.page_digest)
            duplicate_ids = remote_ids.intersection(item.remote_id for item in page.items)
            if duplicate_ids:
                raise _invalid("ParaTranz terms pagination returned duplicate remote ids")
            remote_ids.update(item.remote_id for item in page.items)
            items.extend(page.items)
            if len(items) > max_terms:
                raise _invalid("ParaTranz terms collection exceeded the configured limit")
            revisions.append(page.snapshot_revision)
            if len(revisions) > 1 and page.snapshot_revision != revisions[0]:
                stable = False
                if "snapshot_revision_changed_between_pages" not in diagnostics:
                    diagnostics.append("snapshot_revision_changed_between_pages")
            self._raise_if_cancelled(cancellation)
            if not page.has_next:
                break
            page_number += 1
            if page_number > max_terms + 1:
                raise _invalid("ParaTranz terms pagination did not terminate")

        if revisions and all(revision is None for revision in revisions):
            diagnostics.append("snapshot_revision_unavailable")
        sorted_items = tuple(sorted(items, key=lambda item: item.remote_id))
        aggregate = {
            "projectId": project_id,
            "terms": [{"remoteId": item.remote_id, "observedDigest": item.observed_digest} for item in sorted_items],
        }
        observed_at = self._clock()
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return ParaTranzTermSnapshot(
            project_id=project_id,
            items=sorted_items,
            observed_digest=_canonical_digest(aggregate),
            observed_at=observed_at,
            stable=stable,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _write_mapping(payload: Any, operation: TermWriteOperation) -> Mapping[str, Any] | None:
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise _invalid(f"ParaTranz {operation.value} term response is invalid")
        nested = payload.get("term")
        if isinstance(nested, Mapping):
            return nested
        return payload

    def _write_result(
        self,
        operation: TermWriteOperation,
        raw: Any,
        *,
        known_remote_id: int | None,
    ) -> ParaTranzTermWriteResult:
        payload, headers, response_request_id = self._response(raw, f"{operation.value} term")
        mapping = self._write_mapping(payload, operation)
        remote_id: int | None = known_remote_id
        if mapping is not None:
            candidate = mapping.get("id", mapping.get("external_id"))
            if candidate is not None:
                if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
                    raise _invalid(f"ParaTranz {operation.value} term response has an invalid remote id")
                if known_remote_id is not None and candidate != known_remote_id:
                    raise _invalid(f"ParaTranz {operation.value} term response changed the remote identity")
                remote_id = candidate
        request_id = response_request_id
        if request_id is None and mapping is not None:
            candidate_request_id = mapping.get("requestId", mapping.get("request_id"))
            if isinstance(candidate_request_id, str) and candidate_request_id.strip():
                request_id = candidate_request_id.strip()
        revision = self._revision(mapping, headers)
        digest = _canonical_digest(mapping) if mapping is not None else _canonical_digest(None)
        status = TermWriteStatus.CONFIRMED if remote_id is not None else TermWriteStatus.UNKNOWN
        diagnostics = () if status is TermWriteStatus.CONFIRMED else ("remote_id_missing_reconcile_required",)
        return ParaTranzTermWriteResult(
            operation=operation,
            remote_id=remote_id,
            server_revision=revision,
            observed_digest=digest,
            request_id=request_id,
            status=status,
            diagnostics=diagnostics,
        )

    def create_term(
        self,
        project_id: int,
        write: ParaTranzTermWrite,
        *,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermWriteResult:
        project_id = _positive_integer(project_id, "project_id")
        if not isinstance(write, ParaTranzTermWrite) or write.operation is not TermWriteOperation.CREATE:
            raise ValueError("create_term requires a create ParaTranzTermWrite")
        self._raise_if_cancelled(cancellation)
        raw = self._terms.create_term(
            project_id,
            term_entry_to_paratranz_dict(write.entry),
            cancellation=cancellation,
            raw_response=True,
        )
        self._raise_if_cancelled_after_write(cancellation)
        return self._write_result(TermWriteOperation.CREATE, raw, known_remote_id=None)

    def update_term(
        self,
        project_id: int,
        write: ParaTranzTermWrite,
        *,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermWriteResult:
        project_id = _positive_integer(project_id, "project_id")
        if not isinstance(write, ParaTranzTermWrite) or write.operation is not TermWriteOperation.UPDATE:
            raise ValueError("update_term requires an update ParaTranzTermWrite")
        self._raise_if_cancelled(cancellation)
        raw = self._terms.update_term(
            project_id,
            write.remote_id,
            term_entry_to_paratranz_dict(write.entry),
            cancellation=cancellation,
            raw_response=True,
        )
        self._raise_if_cancelled_after_write(cancellation)
        return self._write_result(TermWriteOperation.UPDATE, raw, known_remote_id=write.remote_id)

    def delete_term(
        self,
        project_id: int,
        remote_id: int,
        *,
        expected_revision: str | None = None,
        expected_digest: str | None = None,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermWriteResult:
        project_id = _positive_integer(project_id, "project_id")
        remote_id = _positive_integer(remote_id, "remote_id")
        if expected_revision is not None and (not isinstance(expected_revision, str) or not expected_revision.strip()):
            raise ValueError("expected_revision must be a non-empty string or null")
        if expected_digest is not None:
            try:
                bytes.fromhex(expected_digest)
            except (TypeError, ValueError):
                raise ValueError("expected_digest must be a lowercase SHA-256 digest") from None
            if len(expected_digest) != 64 or expected_digest != expected_digest.lower():
                raise ValueError("expected_digest must be a lowercase SHA-256 digest")
        self._raise_if_cancelled(cancellation)
        raw = self._terms.delete_term(
            project_id,
            remote_id,
            cancellation=cancellation,
            raw_response=True,
        )
        self._raise_if_cancelled_after_write(cancellation)
        return self._write_result(TermWriteOperation.DELETE, raw, known_remote_id=remote_id)

    def close(self) -> None:
        self._terms.close()


__all__ = ["ParaTranzTermsService"]
