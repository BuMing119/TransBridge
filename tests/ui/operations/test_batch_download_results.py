"""Download result durability and independent local merge decisions."""

import json
from pathlib import Path

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_session import case as case, compare, states  # noqa: F401
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.ui.operations.batch_download_session import BatchDownloadSession


def reopen(case):  # noqa: F811
    old = case.session
    return BatchDownloadSession(
        case.context, case.runtime, reader_factory=old.reader_factory, service_factory=old.service_factory
    )


def test_bad_neighbor_does_not_block_saved_result_restore(case, monkeypatch):  # noqa: F811
    compare(case)
    store = case.session.result_store
    broken = store.create(case.session.stamp, "keep_current")
    path = store.directory(broken["result_id"]) / "result.json"
    path.write_bytes(b"{broken")
    calls = tuple(case.gateway.calls)
    session = reopen(case)
    monkeypatch.setattr(session, "_capture", lambda *_: pytest.fail("Restore must use the saved source"))

    records = session.saved_results()
    assert len(records) == 1 and records.diagnostics
    preview = session.restore(records[0]["result_id"], lambda *_: None, case.cancel)

    assert preview.updates == 2
    assert tuple(case.gateway.calls) == calls
    assert path.read_bytes() == b"{broken"


def test_global_remote_failure_keeps_completed_source_durable_and_reopenable(case, monkeypatch):  # noqa: F811
    read = case.gateway.get_file_translation

    def unauthorized(project_id, file_id):
        if file_id == 2:
            raise ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "access revoked", status=403)
        return read(project_id, file_id)

    monkeypatch.setattr(case.gateway, "get_file_translation", unauthorized)
    with pytest.raises(ExternalServiceError):
        compare(case)
    assert all(not translation for translation, _ in states(case).values())
    session = reopen(case)
    records = session.saved_results()
    assert len(records) == 1
    preview = session.restore(records[0]["result_id"], lambda *_: None, case.cancel)
    assert preview.updates == 1
    assert session.apply(lambda *_: None, case.cancel).updated == 1


def test_reopened_results_use_saved_base_and_never_refetch_cloud(case):  # noqa: F811
    compare(case)
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="新编辑", stage=1)
    calls = tuple(case.gateway.calls)
    session = reopen(case)
    record = session.saved_results()[0]
    session.restore(record["result_id"], lambda *_: None, case.cancel)
    receipt = session.apply(lambda *_: None, case.cancel)
    assert receipt.updated == 1 and receipt.requires_review
    assert states(case)[key] == ("新编辑", 1)
    assert tuple(case.gateway.calls) == calls


def test_deferred_conflict_does_not_block_other_source_and_can_be_chosen(case):  # noqa: F811
    compare(case, policy="defer")
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="新编辑", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    assert receipt.updated == 1 and receipt.preview.deferred == 1
    row = receipt.preview.review_rows[0]
    compare(case, policy="defer", decisions=((row.row_id, "incoming"),))
    assert case.session.apply(lambda *_: None, case.cancel).updated == 1
    assert states(case)[key][0].startswith("云端")


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


def test_unrelated_live_config_revision_does_not_invalidate_download(case):  # noqa: F811
    compare(case)
    case.context.config.config_revision += 1
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2


def test_result_export_contains_fixed_input_remote_and_preoverwrite_content(case, tmp_path):  # noqa: F811
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="覆盖前", stage=1)
    compare(case)
    case.session.apply(lambda *_: None, case.cancel)
    path = tmp_path / "results.json"
    case.session.export_results(path)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert len(result["source_results"]) == 2
    assert any(
        row["before"]["translation"] == "覆盖前" for merge in result["merge_evidence"] for row in merge["applied"]
    )


def test_restore_previous_content_preserves_later_edits(case):  # noqa: F811
    compare(case)
    case.session.apply(lambda *_: None, case.cancel)
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="后来编辑", stage=1)
    receipt = case.session.restore_previous(lambda *_: None, case.cancel)
    assert receipt.updated == 1 and receipt.preview.deferred == 1
    assert states(case)[key] == ("后来编辑", 1)


def test_source_result_disk_failure_keeps_memory_and_retry_does_not_refetch(case, monkeypatch):  # noqa: F811
    with monkeypatch.context() as patch:

        def fail(*_args, **_kwargs):
            raise OSError("result disk unavailable")

        patch.setattr(case.session.result_store, "save_source", fail)
        with pytest.raises(OSError, match="disk unavailable"):
            compare(case)
    assert compare(case).updates == 2
    assert case.gateway.calls.count(("file", 1)) == 1
    session = reopen(case)
    session.restore(session.saved_results()[0]["result_id"], lambda *_: None, case.cancel)
    assert session.apply(lambda *_: None, case.cancel).updated == 2


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


def test_incomplete_prefix_reopens_without_cloud_or_local_source(case, tmp_path, monkeypatch):  # noqa: F811
    first_path = next(path for path, file_id in case.gateway.files.items() if file_id == 1)
    case.gateway.files[first_path[:-5] + "_1.json"] = 3
    case.gateway.rows[3] = []
    case.gateway.fail.add(3)
    preview = compare(case, sources=(case.sources[0].source_id,))
    assert preview.updates == 0 and preview.deferred == 1
    assert preview.review_rows[0].translation == "云端1"
    assert not preview.review_rows[0].can_select_remote
    session = reopen(case)
    monkeypatch.setattr(session, "_capture", lambda *_: pytest.fail("Opening results must not hydrate local sources"))
    monkeypatch.setattr(session, "_check_target", lambda: pytest.fail("Opening results must not require cloud access"))
    record = session.saved_results()[0]
    preview = session.restore(record["result_id"], lambda *_: None, case.cancel)
    assert preview.review_rows[0].translation == "云端1" and not preview.can_apply
    path = tmp_path / "incomplete.json"
    session.export_results(path)
    saved = json.loads(path.read_text(encoding="utf-8"))["source_results"][0]
    assert saved["read"]["error"] and saved["remote"][0]["translation"] == "云端1"


@pytest.mark.parametrize("failure", ["cancel", "authorization"])
def test_global_failure_in_second_file_saves_first_file_before_raising(case, monkeypatch, failure):  # noqa: F811
    from transbridge.application.tasks import TaskCancelled

    first_path = next(path for path, file_id in case.gateway.files.items() if file_id == 1)
    case.gateway.files[first_path[:-5] + "_1.json"] = 3
    original = case.gateway.get_file_translation

    def fail(project_id, file_id):
        if file_id != 3:
            return original(project_id, file_id)
        if failure == "cancel":
            case.cancel.set()
            raise TaskCancelled("cancelled during second file")
        raise ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "revoked", status=403)

    monkeypatch.setattr(case.gateway, "get_file_translation", fail)
    with pytest.raises((TaskCancelled, ExternalServiceError)):
        compare(case, sources=(case.sources[0].source_id,))
    session = reopen(case)
    record = session.saved_results()[0]
    preview = session.restore(record["result_id"], lambda *_: None, case.cancel)
    assert not preview.can_apply and preview.review_rows[0].translation == "云端1"
    assert all(not translation for translation, _stage in states(case).values())


def test_prefix_disk_failure_still_allows_export_of_in_memory_evidence(case, tmp_path, monkeypatch):  # noqa: F811
    first_path = next(path for path, file_id in case.gateway.files.items() if file_id == 1)
    case.gateway.files[first_path[:-5] + "_1.json"] = 3
    case.gateway.fail.add(3)

    def fail(*_args):
        raise OSError("result disk unavailable")

    monkeypatch.setattr(case.session.result_store, "save_source", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        compare(case, sources=(case.sources[0].source_id,))
    path = tmp_path / "memory-prefix.json"
    case.session.export_results(path)
    saved = json.loads(path.read_text(encoding="utf-8"))["source_results"][0]
    assert saved["read"]["error"] and saved["remote"][0]["translation"] == "云端1"


def test_repeated_restore_cycles_reverse_the_latest_direction(case):  # noqa: F811
    compare(case)
    case.session.apply(lambda *_: None, case.cancel)
    for _ in range(2):
        assert case.session.restore_previous(lambda *_: None, case.cancel).updated == 2
        assert all(not translation for translation, _stage in states(case).values())
        assert case.session.restore_previous(lambda *_: None, case.cancel).updated == 2
        assert all(translation.startswith("云端") for translation, _stage in states(case).values())


def test_failed_publication_evidence_is_not_claimed_as_confirmed_overwrite(case, monkeypatch):  # noqa: F811
    compare(case)
    active = case.services.project_lifecycle.active
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    replace = case.services.filesystem.replace

    def fail_variant(source, destination):
        if Path(destination) == variant_path:
            raise PermissionError("variant disk unavailable")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(case.services.filesystem, "replace", fail_variant)
        with pytest.raises(RuntimeError):
            case.session.apply(lambda *_: None, case.cancel)
    assert case.session.record["merge_attempts"]
    assert not case.session.record.get("merges")
    with pytest.raises(ValueError, match="尚无覆盖前"):
        case.session.result_store.reverse_changes(case.session.record)
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2
    assert case.session.record["merges"]
