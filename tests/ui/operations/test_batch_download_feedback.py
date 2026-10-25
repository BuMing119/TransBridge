"""Automatic matching reports kept local content without an extra save prompt."""

import logging

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_session import case as _case_fixture, compare, states

case = _case_fixture


def test_no_matching_original_stays_local_without_review_and_explains_the_skip(case, caplog):
    first, second = case.sources
    before = states(case)
    case.gateway.rows[1][0]["original"] = "Different source text from an older plugin"

    with caplog.at_level(logging.INFO, logger="transbridge.paratranz.sync_diagnostics"):
        preview = compare(case)
    receipt = case.session.apply(lambda *_args: None, case.cancel)

    assert preview.updates == 1 and preview.unchanged == 1 and preview.deferred == 0
    assert preview.can_apply and not preview.retryable
    assert receipt.updated == 1 and not receipt.retryable
    assert states(case)[first.entries[0].identity] == before[first.entries[0].identity]
    assert states(case)[second.entries[0].identity] == ("云端2", 3)
    assert any("无一致原文，保留本地 1 条" in detail for detail in receipt.details)
    assert "PARATRANZ_SYNC_CHECK" in caplog.text


def test_original_fallback_is_reported_and_remote_only_rows_do_not_inflate_unchanged_count(case):
    case.gateway.rows[1][0]["key"] = "old-key-before-source-update"
    case.gateway.rows[1].append(
        dict(case.gateway.rows[1][0], key="remote-only", original="Not in this local plugin", id=99999)
    )

    preview = compare(case)
    receipt = case.session.apply(lambda *_args: None, case.cancel)

    assert preview.updates == 2 and preview.deferred == 0
    assert receipt.updated == 2
    assert any("按原文匹配 1 条" in detail for detail in receipt.details)
    repeated = compare(case)
    assert repeated.updates == 0 and repeated.unchanged == 2
    assert repeated.can_apply and not repeated.can_save and not repeated.retryable


def test_diagnostic_failure_cannot_block_automatic_save(case, monkeypatch, caplog):
    from transbridge.ui.operations import batch_download_session as module

    def unavailable(*_args, **_kwargs):
        raise OSError("Diagnostic storage is temporarily unavailable")

    monkeypatch.setattr(module, "log_sync_diagnostics", unavailable)

    preview = compare(case)
    receipt = case.session.apply(lambda *_args: None, case.cancel)

    assert preview.updates == receipt.updated == 2
    assert not case.services.project_lifecycle.active.dirty
    assert "Download diagnostics failed" in caplog.text
