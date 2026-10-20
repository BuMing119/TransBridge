from __future__ import annotations

from dataclasses import replace
import hashlib
from io import StringIO
import json
import logging

import pytest

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.security.diagnostic_logging import DiagnosticLogFormatter
from transbridge.application.sync import (
    ConflictPolicy,
    DownloadDecision,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncOperation,
    SyncPlanner,
    UploadDecision,
)
from transbridge.paratranz.sync_diagnostics import log_sync_diagnostics

LOGGER = "transbridge.paratranz.sync_diagnostics"
NS = SourceNamespace("project:sync-diagnostics")
SCOPE = f"paratranz:project:7:source:{NS.value}"


def _local(key="same", *, original="Original body", context="", ref=None, qualified=True):
    return LocalEntrySnapshot(
        EntryKey(NS, key, original if qualified else None),
        EntryRevision(5),
        original,
        "Local translation body",
        context,
        1,
        None if ref is None else ExternalEntryRef("paratranz", "project:7", ref),
    )


def _remote(key="same", *, original="Original body", context="", ref=1):
    return RemoteEntrySnapshot(
        EntryKey(NS, key),
        "remote-revision",
        original,
        "Remote translation body",
        context,
        2,
        None if ref is None else ExternalEntryRef("paratranz", "project:7", ref),
    )


def _plan(local, remote, *, operation=SyncOperation.UPLOAD, **kwargs):
    return SyncPlanner(local_state_only=True).plan(
        local,
        remote,
        operation=operation,
        conflict_policy=ConflictPolicy.PREFER_LOCAL
        if operation is SyncOperation.UPLOAD
        else ConflictPolicy.PREFER_REMOTE,
        scope=SCOPE,
        **kwargs,
    )


def _events(caplog):
    records = [record for record in caplog.records if record.name == LOGGER]
    assert all(record.levelno == logging.INFO for record in records)
    assert all("\n" not in record.getMessage() and "\r" not in record.getMessage() for record in records)
    return [json.loads(record.getMessage()) for record in records]


@pytest.mark.parametrize("operation", [SyncOperation.UPLOAD, SyncOperation.DOWNLOAD])
def test_complete_preflight_logs_reason_groups_and_exact_candidate_evidence(caplog, operation):
    local = (_local(context="INFO:NAM1"),)
    remote = (_remote(context="INFO:NAM1"), _remote(ref=2, context="INFO:NAM1"))
    plan = _plan(local, remote, operation=operation)
    original_payload = plan.to_dict()

    with caplog.at_level(logging.INFO, logger=LOGGER):
        check_id = log_sync_diagnostics(plan, project_id=7, local_count=1)

    summary, item = _events(caplog)
    assert summary["event"] == "PARATRANZ_SYNC_CHECK"
    assert summary["check_id"] == item["check_id"] == check_id
    assert summary["operation"] == operation.value
    assert summary["plan_id"] == plan.plan_id
    assert summary["plan_hash"] == plan.plan_hash
    assert summary["project_id"] == 7
    assert summary["local_count"] == 1
    assert summary["action_counts"] == dict(plan.counts)
    assert summary["unresolved_count"] == plan.conflicts == 1
    assert summary["reason_counts"] == {"duplicate_remote_key": 1}
    assert summary["reason_groups"][0]["count"] == 1
    assert "云端" in summary["reason_groups"][0]["reason"]
    assert summary["unresolved_local_occurrences"] == 1
    assert item["event"] == "PARATRANZ_SYNC_UNMATCHED"
    assert item["sequence"] == 1
    assert item["reason_code"] == "duplicate_remote_key"
    assert item["candidate_state"] == "multiple_candidates"
    assert item["local_occurrences"] == 1
    assert item["local"]["revision"] == 5
    assert item["local"]["context"] == "INFO:NAM1"
    assert item["local"]["context_length"] == len("INFO:NAM1")
    assert item["local"]["context_truncated"] is False
    assert item["local"]["original_sha256"] == hashlib.sha256(local[0].original.encode()).hexdigest()
    assert item["candidate_count"] == item["selectable_candidate_count"] == 2
    assert item["original_equal_count"] == item["context_equal_count"] == 2
    assert {candidate["remote_id"] for candidate in item["candidates"]} == {1, 2}
    assert all(candidate["ref_equal"] is None for candidate in item["candidates"])
    assert plan.to_dict() == original_payload
    assert all(
        text not in caplog.text for text in ("Original body", "Local translation body", "Remote translation body")
    )


@pytest.mark.parametrize(
    ("operation", "choice"),
    [(SyncOperation.UPLOAD, "keep_remote"), (SyncOperation.DOWNLOAD, "keep_local")],
)
def test_resolved_rows_are_not_logged_as_unmatched(caplog, operation, choice):
    local = (_local(),)
    remote = (_remote(), _remote(ref=2))
    first = _plan(local, remote, operation=operation)
    if operation is SyncOperation.UPLOAD:
        decisions = {"upload_decisions": (UploadDecision(first.upload_review[0].row_id, choice),)}
    else:
        decisions = {"download_decisions": (DownloadDecision(first.download_review[0].row_id, choice),)}
    plan = _plan(local, remote, operation=operation, **decisions)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    (summary,) = _events(caplog)
    assert summary["unresolved_count"] == 0
    assert summary["reason_counts"] == {}
    assert summary["reason_groups"] == []


def test_stale_reference_without_candidates_records_missing_id(caplog):
    plan = _plan((_local(ref=42),), ())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1, stale_decisions=True)

    summary, item = _events(caplog)
    assert summary["stale_decisions"] is True
    assert item["reason_code"] == "stale_remote_reference"
    assert item["local"]["external_ref"]["opaque_id"] == 42
    assert item["candidate_state"] == "no_candidates"
    assert item["candidate_count"] == item["selectable_candidate_count"] == 0
    assert item["candidates"] == []


def test_blocked_candidates_keep_individual_reasons_and_comparison_evidence(caplog):
    plan = _plan((_local(ref=1, context="local"),), (_remote(context="cloud"), _remote(ref=1, context="other")))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    _, item = _events(caplog)
    assert item["candidate_state"] == "all_unavailable"
    assert item["selectable_candidate_count"] == 0
    assert item["original_equal_count"] == 2
    assert item["context_equal_count"] == 0
    assert all(candidate["blocked_reason_code"] == "duplicate_remote_id" for candidate in item["candidates"])
    assert all(candidate["ref_equal"] is True for candidate in item["candidates"])
    assert all(candidate["blocked_reason"] for candidate in item["candidates"])


@pytest.mark.parametrize(
    ("local_original", "remote_original", "diagnostic_flag"),
    [
        ("  Original body\n", "Original body", "original_equal_after_strip"),
        ("Original\r\nbody", "Original\nbody", "original_equal_after_newline_normalization"),
        ("Original BODY", "original body", "original_equal_casefold"),
    ],
)
def test_normalized_original_evidence_does_not_change_matching(
    caplog, local_original, remote_original, diagnostic_flag
):
    local = (_local(original=local_original),)
    plan = _plan(local, (_remote(original=remote_original),))
    assert plan.conflicts == 1
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    summary, item = _events(caplog)
    assert summary["reason_counts"] == {"original_mismatch": 1}
    assert item["candidates"][0]["original_equal"] is False
    assert item["candidates"][0]["diagnostic_only"][diagnostic_flag] is True
    assert plan.conflicts == 1


def test_shared_target_reason_is_logged_even_when_each_candidate_is_individually_available(caplog):
    local = (_local(original="First original"), _local(original="Second original"))
    remote = (_remote(),)
    first = _plan(local, remote)
    plan = _plan(
        local,
        remote,
        upload_decisions=tuple(UploadDecision(row.row_id, row.candidates[0].token) for row in first.upload_review),
    )
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=2)

    summary, *items = _events(caplog)
    assert summary["unresolved_count"] == 2
    assert summary["reason_counts"] == {"shared_remote_candidate": 2}
    assert all(item["reason_code"] == "shared_remote_candidate" for item in items)
    assert all(item["candidates"][0]["selectable"] for item in items)
    assert all(item["candidates"][0]["blocked_reason_code"] == "" for item in items)


def test_duplicate_local_occurrences_are_separate_from_conflict_item_counts(caplog):
    local = (_local(), replace(_local(), translation="Another local body"))
    plan = _plan(local, (_remote(),))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=2)

    summary, item = _events(caplog)
    assert summary["local_count"] == summary["unresolved_local_occurrences"] == 2
    assert summary["unresolved_count"] == plan.conflicts == 1
    assert summary["reason_counts"] == {"duplicate_local_key": 1}
    assert item["local_occurrences"] == 2
    assert item["allow_remote_choice"] is False


def test_missing_review_details_do_not_omit_conflicts_or_invent_no_candidate_evidence(caplog):
    plan = replace(_plan((_local(ref=42),), ()), upload_review=())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    summary, item = _events(caplog)
    assert summary["unresolved_count"] == 1
    assert summary["detail_unavailable_count"] == 1
    assert item["detail_available"] is False
    assert item["entry_key"]["local_key"] == "same"
    assert item["reason_code"] == "stale_remote_reference"
    assert item["candidate_state"] == "detail_unavailable"
    assert item["candidate_count"] is None
    assert item["local_occurrences"] is None
    assert item["local"] is None


def test_rechecking_same_empty_plan_produces_distinct_check_ids(caplog):
    plan = _plan((), ())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        first = log_sync_diagnostics(plan, project_id=7, local_count=0)
        second = log_sync_diagnostics(plan, project_id=7, local_count=0)

    events = _events(caplog)
    assert first != second
    assert [item["check_id"] for item in events] == [first, second]
    assert all(item["unresolved_count"] == 0 and item["reason_counts"] == {} for item in events)
    assert events[0]["plan_id"] == events[1]["plan_id"]


def test_thousands_of_unmatched_entries_are_logged_without_truncation(caplog):
    local = tuple(_local(key=f"INFO:{number:06d}") for number in range(2181))
    plan = _plan(local, ())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=len(local))

    summary, *items = _events(caplog)
    assert summary["reason_counts"] == {"original_match_required": 2181}
    assert summary["unresolved_count"] == len(items) == 2181
    assert [item["sequence"] for item in items] == list(range(1, 2182))
    assert {item["entry_key"]["local_key"] for item in items} == {entry.entry_key.local_key for entry in local}


@pytest.mark.parametrize(
    "context", ["INFO:NAM1\nsecond\rline\u2028new\u2029paragraph\u0085next", "token=topsecret; other"]
)
def test_real_diagnostic_formatter_preserves_single_line_json_and_redacts_credentials(context):
    local = (_local(key="INFO\nPARATRANZ_SYNC_CHECK", context=context),)
    plan = _plan(local, ())
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(DiagnosticLogFormatter("%(message)s"))
    logger = logging.getLogger(LOGGER)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        log_sync_diagnostics(plan, project_id=7, local_count=1)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    lines = output.getvalue().splitlines()
    assert len(lines) == 2
    summary, item = (json.loads(line) for line in lines)
    assert summary["unresolved_count"] == 1
    assert item["entry_key"]["local_key"] == local[0].entry_key.local_key
    assert "topsecret" not in output.getvalue()
    assert item["local"]["context"] == ("***REDACTED*** other" if "token=" in context else context)
    assert local[0].context == context


def test_long_contexts_are_bounded_but_hash_length_and_comparison_use_complete_values(caplog):
    shared_prefix = "相同的上下文" * 100
    local_context = shared_prefix + "local tail"
    different_context = shared_prefix + "remote tail"
    local = (_local(context=local_context),)
    remote = (
        _remote(original="Different", context=local_context, ref=1),
        _remote(original="Different", context=different_context, ref=2),
    )
    plan = _plan(local, remote)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    _, item = _events(caplog)
    for value, original_context in (
        (item["local"], local_context),
        *((candidate, remote[candidate["remote_id"] - 1].context) for candidate in item["candidates"]),
    ):
        assert value["context"] == original_context[:240]
        assert value["context_length"] == len(original_context)
        assert value["context_sha256"] == hashlib.sha256(original_context.encode()).hexdigest()
        assert value["context_truncated"] is True
    by_id = {candidate["remote_id"]: candidate for candidate in item["candidates"]}
    assert by_id[1]["context_equal"] is True
    assert by_id[2]["context_equal"] is False
    assert item["context_equal_count"] == 1
    assert "local tail" not in caplog.text
    assert "remote tail" not in caplog.text
    assert local[0].context == local_context


def test_context_credentials_are_redacted_before_preview_truncation(caplog):
    secret = "ghp_" + "a" * 30
    context = "x" * 235 + secret + " " + "trailing context" * 40
    local = (_local(context=context),)
    plan = _plan(local, (_remote(original="Different", context=context),))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    _, item = _events(caplog)
    for value in (item["local"], item["candidates"][0]):
        assert value["context"] == "x" * 235 + "***RE"
        assert value["context_truncated"] is True
        assert value["context_length"] == len(context)
        assert value["context_sha256"] == hashlib.sha256(context.encode()).hexdigest()
    assert "ghp_" not in caplog.text
    assert secret not in caplog.text
    assert item["candidates"][0]["context_equal"] is True
    assert local[0].context == context


def test_external_reference_metadata_and_body_credentials_are_never_logged(caplog):
    secret = "sk-abcdefghijklmnop"
    local = _local(original=f"Original credential {secret}", context="BOOK:FULL", ref=8)
    # Snapshot constructors strip reference metadata; manually replaced test data
    # still verifies that the logger projects reference identity fields only.
    reference = ExternalEntryRef("paratranz", "project:7", 8, (("credential", "plain-private-value"),))
    object.__setattr__(local, "external_ref", reference)
    plan = _plan((local,), ())
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_sync_diagnostics(plan, project_id=7, local_count=1)

    assert secret not in caplog.text
    assert "plain-private-value" not in caplog.text
    _, item = _events(caplog)
    assert item["local"]["external_ref"] == {"system": "paratranz", "scope": "project:7", "opaque_id": 8}
    assert local.original == f"Original credential {secret}"
