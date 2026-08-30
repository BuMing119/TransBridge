from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest
import requests

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.ports.paratranz_terms import (
    ParaTranzTerm,
    ParaTranzTermSnapshot,
    ParaTranzTermWrite,
    TermWriteOperation,
    TermWriteStatus,
)
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI
from transbridge.paratranz.terms_service import ParaTranzTermsService

_DIGEST = "a" * 64
_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _record(remote_id: int, term: str = "Dragonborn", translation: str = "龙裔", **extra):
    return {
        "id": remote_id,
        "term": term,
        "translation": translation,
        "variants": [f"{term}s"],
        "caseSensitive": True,
        "pos": "noun",
        "note": "canonical",
        "createdAt": "2026-08-30T00:00:00Z",
        **extra,
    }


def _response(payload, *, status: int = 200, headers: dict[str, str] | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    if payload is None:
        response._content = b""
    else:
        import json

        response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response.encoding = "utf-8"
    return response


def _entry(**overrides) -> TermEntry:
    values = {
        "term": "Dragonborn",
        "translation": "龙裔",
        "source": "project",
        "context": "plugin-only context",
        "created_at": "2026-01-01",
        "case_sensitive": True,
        "variants": ["Dovahkiin"],
        "pos": "noun",
        "note": "title",
        "external_id": 999,
        "metadata": {"project": "read-only", "nested": {"server": True}},
    }
    values.update(overrides)
    return TermEntry(**values)


def _service(api: MagicMock) -> ParaTranzTermsService:
    return ParaTranzTermsService(api, clock=lambda: _NOW)


def test_term_and_snapshot_validate_identity_digest_timezone_and_deep_freeze() -> None:
    entry = _entry()
    metadata = {"createdAt": "today", "future": {"flags": [1, 2]}}
    term = ParaTranzTerm(7, entry, None, _DIGEST, metadata)
    entry.term = "mutated"
    metadata["future"]["flags"].append(3)

    assert term.entry.term == "Dragonborn"
    assert isinstance(term.readonly_metadata, MappingProxyType)
    assert term.readonly_metadata["future"]["flags"] == (1, 2)
    with pytest.raises(TypeError):
        term.readonly_metadata["new"] = "blocked"

    snapshot = ParaTranzTermSnapshot(41, (term,), _DIGEST, _NOW, True)
    assert snapshot.items == (term,)
    with pytest.raises(ValueError, match="timezone-aware"):
        ParaTranzTermSnapshot(41, (term,), _DIGEST, datetime(2026, 8, 30), True)
    with pytest.raises(ValueError, match="SHA-256"):
        ParaTranzTerm(7, _entry(), None, "not-a-digest")
    with pytest.raises(ValueError, match="positive integer"):
        ParaTranzTerm(0, _entry(), None, _DIGEST)


def test_snapshot_reads_all_pages_sorts_items_and_preserves_unknown_fields() -> None:
    api = MagicMock()
    api.list_terms.side_effect = [
        {
            "terms": [_record(2, futureCapability={"mode": "read-only"})],
            "pagination": {"page": 1, "totalPages": 2},
        },
        {"terms": [_record(1, term="Jarl", translation="领主")], "pagination": {"page": 2, "totalPages": 2}},
    ]

    snapshot = _service(api).snapshot_terms(41, page_size=1)

    assert [item.remote_id for item in snapshot.items] == [1, 2]
    assert snapshot.items[1].entry.source == "paratranz"
    assert snapshot.items[1].entry.external_id == 2
    assert snapshot.items[1].readonly_metadata["futureCapability"]["mode"] == "read-only"
    assert snapshot.stable is True
    assert snapshot.diagnostics == ("snapshot_revision_unavailable",)
    assert snapshot.observed_at == _NOW
    assert len(snapshot.observed_digest) == 64
    assert [call.kwargs["page"] for call in api.list_terms.call_args_list] == [1, 2]
    assert all(call.kwargs["raw_response"] is True for call in api.list_terms.call_args_list)


def test_snapshot_revision_change_is_returned_as_unstable_diagnostic() -> None:
    api = MagicMock()
    api.list_terms.side_effect = [
        _response(
            {"terms": [_record(1)], "pagination": {"page": 1, "totalPages": 2}},
            headers={"ETag": "snapshot-a"},
        ),
        _response(
            {"terms": [_record(2)], "pagination": {"page": 2, "totalPages": 2}},
            headers={"ETag": "snapshot-b"},
        ),
    ]

    snapshot = _service(api).snapshot_terms(41, page_size=1)

    assert snapshot.stable is False
    assert snapshot.diagnostics == ("snapshot_revision_changed_between_pages",)


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ([{"terms": [{"term": "missing", "translation": "id"}]}], "valid remote id"),
        ([{"terms": [_record(1), _record(1, term="Other")]}], "duplicate remote ids"),
        (
            [
                {"terms": [_record(1)], "pagination": {"page": 1, "totalPages": 2}},
                {"terms": [_record(1)], "pagination": {"page": 2, "totalPages": 2}},
            ],
            "repeated a page",
        ),
    ],
)
def test_snapshot_fails_closed_for_invalid_identity_or_pagination(pages, message: str) -> None:
    api = MagicMock()
    api.list_terms.side_effect = pages
    with pytest.raises(ExternalServiceError, match=message) as captured:
        _service(api).snapshot_terms(41, page_size=1)
    assert captured.value.category is ExternalServiceCategory.INVALID_RESPONSE


def test_snapshot_empty_collection_is_stable_and_remote_limit_is_bounded() -> None:
    api = MagicMock()
    api.list_terms.return_value = {"terms": []}
    snapshot = _service(api).snapshot_terms(41)
    assert snapshot.items == ()
    assert snapshot.stable is True

    api.list_terms.return_value = {"terms": [_record(1), _record(2)]}
    with pytest.raises(ExternalServiceError, match="configured limit"):
        _service(api).snapshot_terms(41, max_terms=1)


def test_create_update_delete_send_only_writable_fields_and_project_results() -> None:
    api = MagicMock()
    api.create_term.return_value = _response(
        {"id": 71, "requestId": "body-request", **_record(71)},
        status=201,
        headers={"X-Request-ID": "remote-create", "ETag": "term-rev-1"},
    )
    api.update_term.return_value = _response(None, status=204, headers={"X-Request-ID": "remote-update"})
    api.delete_term.return_value = _response(None, status=204, headers={"X-Request-ID": "remote-delete"})
    service = _service(api)

    create = ParaTranzTermWrite(_entry(), TermWriteOperation.CREATE)
    update = ParaTranzTermWrite(
        _entry(note="updated"), TermWriteOperation.UPDATE, remote_id=71, expected_digest=_DIGEST
    )
    created = service.create_term(41, create)
    updated = service.update_term(41, update)
    deleted = service.delete_term(41, 71, expected_digest=_DIGEST)

    expected_create_payload = {
        "term": "Dragonborn",
        "translation": "龙裔",
        "variants": ["Dovahkiin"],
        "caseSensitive": True,
        "pos": "noun",
        "note": "title",
    }
    assert api.create_term.call_args.args == (41, expected_create_payload)
    assert set(api.update_term.call_args.args[2]) == {
        "term",
        "translation",
        "variants",
        "caseSensitive",
        "pos",
        "note",
    }
    assert "createdAt" not in api.update_term.call_args.args[2]
    assert "project" not in api.update_term.call_args.args[2]
    assert created.status is TermWriteStatus.CONFIRMED
    assert created.remote_id == 71
    assert created.server_revision == "term-rev-1"
    assert created.request_id == "remote-create"
    assert updated.confirmed and updated.remote_id == 71 and updated.request_id == "remote-update"
    assert deleted.confirmed and deleted.operation is TermWriteOperation.DELETE
    assert deleted.request_id == "remote-delete"


def test_create_without_remote_id_returns_unknown_reconcile_result() -> None:
    api = MagicMock()
    api.create_term.return_value = {"term": "Dragonborn", "translation": "龙裔"}
    result = _service(api).create_term(41, ParaTranzTermWrite(_entry(), TermWriteOperation.CREATE))
    assert result.status is TermWriteStatus.UNKNOWN
    assert result.remote_id is None
    assert result.diagnostics == ("remote_id_missing_reconcile_required",)


def test_write_models_reject_operation_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="must not include remote_id"):
        ParaTranzTermWrite(_entry(), TermWriteOperation.CREATE, remote_id=1)
    with pytest.raises(ValueError, match="positive integer"):
        ParaTranzTermWrite(_entry(), TermWriteOperation.UPDATE)

    api = MagicMock()
    with pytest.raises(ValueError, match="create ParaTranzTermWrite"):
        _service(api).create_term(41, ParaTranzTermWrite(_entry(), TermWriteOperation.UPDATE, remote_id=1))


def test_cancellation_is_checked_before_starting_each_operation() -> None:
    api = MagicMock()
    token = CancellationToken()
    token._cancel("cancel terms service")
    service = _service(api)
    with pytest.raises(TaskCancelled):
        service.snapshot_terms(41, cancellation=token)
    with pytest.raises(TaskCancelled):
        service.create_term(41, ParaTranzTermWrite(_entry(), TermWriteOperation.CREATE), cancellation=token)
    api.list_terms.assert_not_called()
    api.create_term.assert_not_called()


def test_cancellation_after_write_dispatch_is_unknown_not_a_lost_task_cancel() -> None:
    api = MagicMock()
    token = CancellationToken()

    def committed_response(*_args, **_kwargs):
        token._cancel("cancelled after remote accepted the write")
        return _response({"id": 71, **_record(71)}, status=201)

    api.create_term.side_effect = committed_response

    with pytest.raises(ExternalServiceError) as captured:
        _service(api).create_term(
            41,
            ParaTranzTermWrite(_entry(), TermWriteOperation.CREATE),
            cancellation=token,
        )

    assert captured.value.category is ExternalServiceCategory.CANCELLED
    assert "reconcile is required" in str(captured.value)
    assert isinstance(captured.value.__cause__, TaskCancelled)


def test_raw_terms_api_keeps_legacy_positions_and_disables_blind_write_retry() -> None:
    api = object.__new__(ParatranzTermsAPI)
    api._request = MagicMock(return_value=None)

    api.list_terms(41, 2, 25)
    api.create_term(41, {"term": "A", "translation": "甲"})
    api.update_term(41, 7, {"term": "A", "translation": "乙"})
    api.delete_term(41, 7)

    assert api._request.call_args_list[0].kwargs["expected_type"] == (list, dict)
    assert api._request.call_args_list[1].kwargs["expected_type"] is dict
    assert api._request.call_args_list[2].kwargs["retryable"] is False
    assert api._request.call_args_list[3].kwargs["retryable"] is False
