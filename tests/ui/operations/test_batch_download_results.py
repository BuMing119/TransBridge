"""Only merged translations outlive a download round."""

from pathlib import Path

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_session import case as case, compare, states  # noqa: F401
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.ui.operations.batch_download_session import BatchDownloadSession


def assert_discarded(case):  # noqa: F811
    session = case.session
    assert session.plan is session.snapshot is session.merge_result is None
    assert not session._reads and not session._scopes and not session._bases and not session.review_states
    assert not session.decisions and not session._verified
    assert not list(Path(case.services.root).rglob("download-results"))
    assert not list(Path(case.services.root).rglob("merge-*.json"))


def test_success_discards_data_and_next_round_reads_fresh_cloud_content(case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        case.context.project_commands, "task_result_directory", lambda *_: pytest.fail("No result files")
    )
    assert compare(case).updates == 2
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2
    assert_discarded(case)
    case.gateway.rows[1][0]["translation"] = "下一轮的新译文"
    assert compare(case).updates == 1
    assert case.session.apply(lambda *_: None, case.cancel).updated == 1
    assert states(case)[case.sources[0].entries[0].identity][0] == "下一轮的新译文"
    assert case.gateway.calls.count(("inventory", 42)) == 2
    assert case.gateway.calls.count(("file", 1)) == 2
    assert_discarded(case)


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_read_discards_successful_prefix(case, monkeypatch, cancelled):  # noqa: F811
    read = case.gateway.get_file_translation

    def fail(project_id, file_id):
        if file_id == 2:
            if cancelled:
                case.cancel.set()
            raise ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "access revoked", status=403)
        return read(project_id, file_id)

    with monkeypatch.context() as patch:
        patch.setattr(case.gateway, "get_file_translation", fail)
        with pytest.raises(Exception, match="revoked|取消"):
            compare(case)
    assert all(not translation for translation, _ in states(case).values())
    assert_discarded(case)
    case.cancel.clear()
    assert compare(case).updates == 2
    assert case.gateway.calls.count(("file", 1)) == 2


def test_discard_and_new_session_never_reuse_previous_download(case):  # noqa: F811
    compare(case)
    case.session.discard()
    assert_discarded(case)
    old = case.session
    case.session = BatchDownloadSession(
        case.context, case.runtime, reader_factory=old.reader_factory, service_factory=old.service_factory
    )
    compare(case)
    assert case.gateway.calls.count(("inventory", 42)) == 2
    assert case.gateway.calls.count(("file", 1)) == 2


def test_deferred_conflict_can_be_chosen_in_this_round(case):  # noqa: F811
    compare(case, policy="defer")
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="新编辑", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    assert receipt.updated == 1 and receipt.preview.deferred == 1
    row = receipt.preview.review_rows[0]
    calls = tuple(case.gateway.calls)
    compare(case, policy="defer", decisions=((row.row_id, "incoming"),))
    assert case.session.apply(lambda *_: None, case.cancel).updated == 1
    assert states(case)[key][0].startswith("云端")
    assert tuple(case.gateway.calls) == calls
    assert_discarded(case)


def test_explicit_choice_does_not_overwrite_edit_made_after_review(case):  # noqa: F811
    compare(case, policy="defer")
    key = case.sources[0].entries[0].identity
    commands = case.context.project_commands
    commands.update_entry(key, case.context.runtime_context, translation="第一次编辑", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    row = receipt.preview.review_rows[0]
    compare(case, policy="defer", decisions=((row.row_id, "incoming"),))
    commands.update_entry(key, case.context.runtime_context, translation="再次编辑", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    assert receipt.updated == 0 and receipt.preview.deferred == 1
    assert states(case)[key] == ("再次编辑", 1)


def test_all_deferred_results_are_reviewable_before_any_commit(case):  # noqa: F811
    selected = (case.sources[0].source_id,)
    compare(case, sources=selected, policy="defer")
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="手动修改", stage=1)
    preview = compare(case, sources=selected, policy="defer")
    assert preview.updates == 0 and preview.deferred == 1 and len(preview.review_rows) == 1
    row = preview.review_rows[0]
    preview = compare(case, sources=selected, policy="defer", decisions=((row.row_id, "incoming"),))
    assert preview.updates == 1 and preview.deferred == 0
    assert case.session.apply(lambda *_: None, case.cancel).updated == 1
