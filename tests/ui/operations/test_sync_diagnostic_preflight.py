"""Production preflight writes correlated diagnostic logs without applying changes."""

from dataclasses import replace
import json
import logging
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.io.identity import EntryKey
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.operations import sync_preparation
from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.operations.production_support import sync_request
from transbridge.ui.operations.sync_preparation import prepare_sync_request

DIAGNOSTIC_LOGGER = "transbridge.paratranz.sync_diagnostics"
CHECK_EVENT = "PARATRANZ_SYNC_CHECK"
ITEM_EVENT = "PARATRANZ_SYNC_UNMATCHED"


@pytest.fixture(params=(OperationKind.DOWNLOAD, OperationKind.UPLOAD), ids=("download", "upload"))
def diagnostic_case(request, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=DIAGNOSTIC_LOGGER)
    entries = tuple(
        replace(entry, entry_key=EntryKey(entry.identity.namespace, entry.key, entry.original))
        for entry in make_test_collection(3)
    )
    first, ambiguous, mismatch = entries
    remote = [
        ParaTranzEntry(7, first.key, first.original, "明确对应的云端译文", first.context, 1),
        ParaTranzEntry(8, ambiguous.key, ambiguous.original, "云端甲", ambiguous.context, 1),
        ParaTranzEntry(9, ambiguous.key, ambiguous.original, "云端乙", ambiguous.context, 2),
        ParaTranzEntry(10, mismatch.key, "不同来源中的另一句原文", "其他来源的译文", mismatch.context, 2),
    ]
    writes, closed = [], []
    service = SimpleNamespace(
        list_projects=lambda **_kwargs: (SimpleNamespace(project_id=42, name="诊断测试"),),
        list_entries=lambda *_args, **_kwargs: tuple(remote),
        update_entry=lambda *_args, **_kwargs: writes.append("update"),
        update_entry_translation=lambda *_args, **_kwargs: writes.append("translation"),
        create_entry=lambda *_args, **_kwargs: writes.append("create"),
        delete_entry=lambda *_args, **_kwargs: writes.append("delete"),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(ParaTranzService, "from_config", classmethod(lambda _cls, _config: service))
    context = SimpleNamespace(
        collection=TranslationEntryCollection(entries),
        active_version_identity=("project", "variant"),
        active_project_id="project",
        project_revision=0,
        variant_revision=0,
        paratranz_binding={
            "project_id": 42,
            "project_name": "诊断测试",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 5,
        },
        config=SimpleNamespace(
            token="diagnostic-test-secret-token",
            base_url="https://paratranz.cn",
            user_id=5,
            config_revision=1,
        ),
        current_user={"id": 5},
    )
    sync, ready, reason = sync_request(context, request.param, False)
    assert ready, reason
    case = SimpleNamespace(
        request=sync,
        kind=request.param,
        context=context,
        entries=entries,
        remote=remote,
        service=service,
        closed=closed,
    )
    yield case
    assert not writes, "检查和诊断不能写入 ParaTranz"
    assert tuple(context.collection) == entries, "检查和诊断不能修改本地内容"
    assert context.config.token not in caplog.text


def _events(caplog, event):
    messages = [json.loads(record.getMessage()) for record in caplog.records if record.name == DIAGNOSTIC_LOGGER]
    return [message for message in messages if message["event"] == event]


def _prepare(request):
    prepared, result = prepare_sync_request(request)
    assert prepared is not None
    prepared.close()
    return prepared.plan, result


def test_diagnostic_failure_does_not_discard_prepared_plan_or_its_service(diagnostic_case, caplog, monkeypatch):
    def fail_diagnostics(*_args, **_kwargs):
        raise RuntimeError("diagnostic projection failed")

    monkeypatch.setattr(sync_preparation, "log_sync_diagnostics", fail_diagnostics)
    prepared, result = prepare_sync_request(diagnostic_case.request)
    assert prepared is not None
    assert prepared.plan.conflicts == 2
    assert result.checks
    assert not diagnostic_case.closed
    prepared.close()
    assert diagnostic_case.closed == [True]
    assert not _events(caplog, CHECK_EVENT)
    failures = [record for record in caplog.records if "PARATRANZ_SYNC_DIAGNOSTICS_FAILED" in record.getMessage()]
    assert len(failures) == 1
    assert failures[0].exc_info is not None


def test_preflight_logs_each_unmatched_entry_and_exact_reason_totals(diagnostic_case, caplog):
    case = diagnostic_case
    plan, result = _prepare(case.request)
    summaries = _events(caplog, CHECK_EVENT)
    details = _events(caplog, ITEM_EVENT)

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["operation"] == case.kind.value
    assert summary["project_id"] == 42
    assert summary["plan_id"] == plan.plan_id
    assert summary["plan_hash"] == plan.plan_hash
    assert summary["local_count"] == 3
    assert summary["unresolved_count"] == plan.conflicts == len(details) == 2
    assert summary["reason_counts"] == {"duplicate_remote_key": 1, "original_mismatch": 1}
    assert summary["action_counts"] == dict(plan.counts)
    assert summary["stale_decisions"] is False
    assert {group["code"]: group["count"] for group in summary["reason_groups"]} == summary["reason_counts"]
    assert all(group["reason"] and group["reason"] != group["code"] for group in summary["reason_groups"])
    assert {item["check_id"] for item in details} == {summary["check_id"]}
    assert {item["sequence"] for item in details} == {1, 2}
    assert {item["row_id"] for item in details} == {row.row_id for row in result.sync_review}

    by_key = {item["entry_key"]["local_key"]: item for item in details}
    ambiguous = by_key[case.entries[1].key]
    assert ambiguous["reason_code"] == "duplicate_remote_key"
    assert ambiguous["reason"] and ambiguous["reason"] != ambiguous["reason_code"]
    assert ambiguous["candidate_count"] == len(ambiguous["candidates"]) == 2
    assert {candidate["remote_id"] for candidate in ambiguous["candidates"]} == {8, 9}
    assert ambiguous["original_equal_count"] == 2
    mismatch = by_key[case.entries[2].key]
    assert mismatch["reason_code"] == "original_mismatch"
    assert mismatch["candidate_count"] == 1
    assert mismatch["original_equal_count"] == 0
    assert mismatch["local"]["context"] == case.entries[2].context
    assert mismatch["candidates"][0]["remote_id"] == 10


def test_rechecking_same_snapshot_has_a_new_check_id(diagnostic_case, caplog):
    case = diagnostic_case
    _prepare(case.request)
    _prepare(case.request)

    first, second = _events(caplog, CHECK_EVENT)
    assert first["check_id"] != second["check_id"]
    assert first["plan_id"] == second["plan_id"]
    details = _events(caplog, ITEM_EVENT)
    for summary in (first, second):
        assert sum(item["check_id"] == summary["check_id"] for item in details) == summary["unresolved_count"]


def test_stale_choice_logs_only_the_rebuilt_plan_and_marks_stale_decisions(diagnostic_case, caplog):
    case = diagnostic_case
    _, previous = _prepare(case.request)
    row = next(row for row in previous.sync_review if row.key == case.entries[1].key)
    choice = row.candidates[0].candidate_id
    case.remote[1:3] = [replace(entry, translation="检查之后云端译文改变") for entry in case.remote[1:3]]
    field = "download_decisions" if case.kind is OperationKind.DOWNLOAD else "upload_decisions"
    stale = replace(case.request, **{field: ((row.row_id, choice),)})
    caplog.clear()

    plan, result = _prepare(stale)

    summaries = _events(caplog, CHECK_EVENT)
    assert len(summaries) == 1
    assert summaries[0]["stale_decisions"] is True
    assert summaries[0]["unresolved_count"] == plan.conflicts
    assert any("选择已清除" in check.reason for check in result.checks)
    assert all(not row.decision for row in result.sync_review)
    assert len(_events(caplog, ITEM_EVENT)) == plan.conflicts


def test_clean_preflight_also_records_a_zero_conflict_summary(diagnostic_case, caplog):
    case = diagnostic_case
    case.remote[:] = [
        ParaTranzEntry(index + 20, entry.key, entry.original, "唯一对应的更新", entry.context, 1)
        for index, entry in enumerate(case.entries)
    ]
    plan, _result = _prepare(case.request)

    (summary,) = _events(caplog, CHECK_EVENT)
    assert summary["unresolved_count"] == plan.conflicts == 0
    assert summary["reason_counts"] == {}
    assert summary["reason_groups"] == []
    assert not _events(caplog, ITEM_EVENT)


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (ExternalServiceError(ExternalServiceCategory.TIMEOUT, "测试网络超时"), "PARATRANZ_TIMEOUT"),
        (RuntimeError("测试快照读取失败"), "PARATRANZ_PREFLIGHT_FAILED"),
        (PermissionError("测试账号无权访问"), "PARATRANZ_MEMBER_REQUIRED"),
    ),
)
def test_failed_check_records_failure_without_a_success_summary(diagnostic_case, caplog, failure, code):
    case = diagnostic_case

    def fail(*_args, **_kwargs):
        raise failure

    case.service.list_entries = fail
    prepared, result = prepare_sync_request(case.request)

    assert prepared is None
    assert result.checks[0].check_id == code
    assert case.closed == [True]
    failures = [
        record.getMessage() for record in caplog.records if "PARATRANZ_SYNC_CHECK_FAILED" in record.getMessage()
    ]
    assert len(failures) == 1
    assert "project_id=42" in failures[0] and f"operation={case.kind.value}" in failures[0]
    assert f"code={code}" in failures[0]
    assert not _events(caplog, CHECK_EVENT)
    assert not _events(caplog, ITEM_EVENT)


def test_changed_target_is_logged_without_fetching_or_success_summary(diagnostic_case, caplog):
    case = diagnostic_case
    case.context.config.config_revision += 1
    prepared, result = prepare_sync_request(case.request)

    assert prepared is None
    assert result.checks[0].check_id == "PARATRANZ_TARGET_STALE"
    assert "PARATRANZ_SYNC_CHECK_FAILED" in caplog.text
    assert "code=PARATRANZ_TARGET_STALE" in caplog.text
    assert not case.closed
    assert not _events(caplog, CHECK_EVENT)


def test_invalid_request_is_logged_as_failure(caplog):
    prepared, result = prepare_sync_request(None)

    assert prepared is None
    assert result.checks[0].check_id == "SYNC_REQUEST_INVALID"
    assert "PARATRANZ_SYNC_CHECK_FAILED code=SYNC_REQUEST_INVALID" in caplog.text
    assert not _events(caplog, CHECK_EVENT)
