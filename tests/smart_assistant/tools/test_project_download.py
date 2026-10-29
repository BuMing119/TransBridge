"""Assistant project downloads use the GUI's file reader, local merge and save."""

from dataclasses import replace
import json
from threading import Thread, get_ident
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
import pytest

from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture, write_json
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.paratranz import batch_download_snapshot
from transbridge.paratranz.service import ParaTranzService
from transbridge.smart_assistant.tools import _project_download
from transbridge.smart_assistant.tools.tool_paratranz import _tool_download_entries
from transbridge.smart_assistant.tools.tool_parser import _tool_import_json
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture


class Files:
    def __init__(self):
        self.files, self.rows, self.calls = {}, {}, []
        self.on_read = None

    def inventory(self, project_id):
        self.calls.append(("inventory", project_id, get_ident()))
        return dict(self.files)

    def get_file_translation(self, project_id, file_id):
        self.calls.append(("file", file_id, get_ident()))
        if self.on_read is not None:
            self.on_read()
        return self.rows[file_id]

    def close(self):
        pass


@pytest.fixture
def download(project, monkeypatch):
    entry = load_source(project)
    files, sessions = Files(), []
    source = project.ctx.project_commands.file_sources_catalog(project.request).value[0]
    files.files[source.label + "/任务/对话_[Discerning the Transmundane].json"] = 1
    files.rows[1] = [dict(paratranz_record_from_entry(entry), id=1, translation="共享流程译文", stage=1)]
    monkeypatch.setattr(batch_download_snapshot, "FilePublicationGateway", lambda _config: files)
    monkeypatch.setattr(
        ParaTranzService,
        "from_config",
        lambda _config: SimpleNamespace(list_projects=lambda **_: [SimpleNamespace(project_id=7)], close=lambda: None),
    )
    original = _project_download.ProjectDownloadSession

    class Session(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

    monkeypatch.setattr(_project_download, "ProjectDownloadSession", Session)
    return SimpleNamespace(project=project, entry=entry, source=source, files=files, sessions=sessions)


def assert_saved(case):
    active = case.project.services.project_lifecycle.active
    assert not active.dirty
    assert case.project.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()


def assert_no_download_results(case):
    assert not list(case.project.root.rglob("download-results"))
    for path in case.project.root.rglob("*.json"):
        if "ai-task-recovery" in path.parts:
            record = json.loads(path.read_text(encoding="utf-8"))
            assert not isinstance(record, dict) or record.get("result_kind") != "download"
    assert case.sessions
    assert all(session.plan is None and not session._reads and not session._bases for session in case.sessions)


def test_named_directory_download_matches_saves_and_clears_without_saved_results(download):
    case = download
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success, result.message
    assert result.data["updated_count"] == 1 and "result_id" not in result.data and "entries" not in result.data
    assert case.project.ctx.collection.get(case.entry.identity).translation == "共享流程译文"
    assert_saved(case)
    assert_no_download_results(case)
    assert [(kind, value) for kind, value, _ in case.files.calls] == [("inventory", 7), ("file", 1)]


def test_download_has_no_2000_entry_limit_and_next_call_reads_fresh_content(download):
    case = download
    target = case.files.rows[1][0]
    case.files.rows[1] = [
        {"id": i + 1, "key": f"other-{i}", "original": f"Other {i}", "translation": "额外译文", "stage": 1}
        for i in range(2100)
    ] + [dict(target, id=2101)]
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success and result.data["updated_count"] == 1
    assert len(case.project.ctx.collection) == 1
    case.files.rows[1][-1]["translation"] = "下一轮译文"
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success and result.data["updated_count"] == 1
    assert case.project.ctx.collection.get(case.entry.identity).translation == "下一轮译文"
    assert len(case.files.calls) == 4
    unchanged = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert unchanged.success and unchanged.data["updated_count"] == 0 and unchanged.data["unchanged_count"] == 1
    assert_saved(case)
    assert_no_download_results(case)


@pytest.mark.parametrize("before_read", [False, True])
def test_cancellation_never_saves_or_retains_download_content(download, before_read):
    case = download
    token = CancellationToken()
    if before_read:
        token._cancel("cancelled before downloading")
    else:
        case.files.on_read = lambda: token._cancel("cancelled during downloading")
    context = ExecutionContext(app_context=case.project.ctx)
    context.cancellation_token = token
    before = case.project.services.project_lifecycle.active.variant.snapshot()
    with pytest.raises(TaskCancelled):
        _tool_download_entries({"project_id": 7}, context)
    assert case.project.services.project_lifecycle.active.variant.snapshot() == before
    if case.sessions:
        assert_no_download_results(case)
    if before_read:
        assert not case.files.calls


def test_default_is_current_source_and_explicit_source_ids_can_select_another_registered_source(download):
    case = download
    path = write_json(case.project, "second.json", [{"key": "other", "original": "Other", "translation": ""}])
    imported = _tool_import_json(
        {"path": path, "action": "create_slot"}, ExecutionContext(app_context=case.project.ctx)
    )
    assert imported.success, imported.message
    sources = case.project.ctx.project_commands.file_sources_catalog(case.project.request).value
    second = next(source for source in sources if source.source_id != case.source.source_id)
    other = next(iter(case.project.ctx.collection))
    case.files.files[second.label + "/任务/对话_[Different name].json"] = 2
    case.files.rows[2] = [dict(paratranz_record_from_entry(other), id=2, translation="第二个来源", stage=1)]
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success and result.data["source_ids"] == [second.source_id]
    assert [(kind, value) for kind, value, _ in case.files.calls] == [("inventory", 7), ("file", 2)]
    result = _tool_download_entries(
        {"project_id": 7, "source_ids": [case.source.source_id]}, ExecutionContext(app_context=case.project.ctx)
    )
    assert result.success and result.data["source_ids"] == [case.source.source_id]
    assert [(kind, value) for kind, value, _ in case.files.calls][-2:] == [("inventory", 7), ("file", 1)]
    assert_saved(case)
    assert_no_download_results(case)


def test_background_assistant_commit_uses_its_gate_without_qt_lock_handoff(download):
    case = download
    context = ExecutionContext(app_context=case.project.ctx)
    entered, outcome = [], {}
    main_thread = get_ident()

    class Gate:
        def commit(self, effect_id, action):
            assert effect_id == "download-effect"
            entered.append(get_ident())
            action()
            return SimpleNamespace(accepted=True)

    context.assistant_gate = Gate()
    context.assistant_effect_id = "download-effect"

    def execute():
        outcome["result"] = _tool_download_entries({"project_id": 7}, context)

    worker = Thread(target=execute, daemon=True)
    worker.start()
    deadline = monotonic() + 10
    while worker.is_alive() and monotonic() < deadline:
        case.project.app.processEvents()
        QTest.qWait(2)
    assert not worker.is_alive(), "Download must not wait on Qt while owning the project lock"
    case.project.app.processEvents()
    assert outcome["result"].success, outcome["result"].message
    assert len(entered) == 1 and entered[0] != main_thread
    assert all(thread != main_thread for _, _, thread in case.files.calls)
    assert not case.project.ctx.authoritative_projection_diverged()
    assert context._target_variant_revision == case.project.ctx.variant_revision
    assert context._target_project_revision == case.project.ctx.project_revision
    assert context._target_collection is case.project.ctx.collection
    assert_saved(case)
    assert_no_download_results(case)


def test_empty_request_run_id_uses_the_shared_execution_identity(download):
    case = download
    context = ExecutionContext(app_context=case.project.ctx, request_context=replace(case.project.request, run_id=None))
    result = _tool_download_entries({"project_id": 7}, context)
    assert result.success and result.data["updated_count"] == 1
    assert_saved(case)
    assert_no_download_results(case)


def test_captured_version_change_before_download_does_not_retarget_the_request(download):
    case = download
    assert case.project.ctx.project_commands.save(case.project.request).is_success
    context = ExecutionContext(app_context=case.project.ctx)
    switched = case.project.ctx.project_commands.create_variant("new target", case.project.request, copy_active=True)
    assert switched.is_success, switched.diagnostics
    result = _tool_download_entries({"project_id": 7}, context)
    assert not result.success and not case.files.calls
    assert not next(iter(case.project.ctx.collection)).translation


def test_existing_assistant_gate_can_reject_final_save_without_retaining_candidates(download):
    case = download
    context = ExecutionContext(app_context=case.project.ctx)
    entered = []

    class Gate:
        def commit(self, effect_id, _action):
            entered.append(effect_id)
            return SimpleNamespace(accepted=False, reason="request cancelled")

    context.assistant_gate, context.assistant_effect_id = Gate(), "blocked-save"
    active = case.project.services.project_lifecycle.active
    before = case.project.services.variants.read_snapshot(active.formal_variant_ref)
    result = _tool_download_entries({"project_id": 7}, context)
    assert not result.success and entered == ["blocked-save"]
    assert case.project.services.variants.read_snapshot(active.formal_variant_ref) == before
    assert not case.project.ctx.collection.get(case.entry.identity).translation
    assert_no_download_results(case)


def test_failed_file_read_is_not_reported_as_no_update_and_leaves_no_results(download):
    case = download

    def fail():
        raise OSError("remote file unavailable")

    case.files.on_read = fail
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert not result.success and result.partial and result.data["retryable"]
    assert "无需更新" not in result.message
    assert not case.project.ctx.collection.get(case.entry.identity).translation
    assert_no_download_results(case)


def test_late_cancellation_after_commit_does_not_reverse_a_saved_result(download):
    case = download
    token = CancellationToken()
    context = ExecutionContext(app_context=case.project.ctx)
    context.cancellation_token = token

    class Gate:
        def commit(self, _effect_id, action):
            action()
            token._cancel("cancel arrived after successful save")
            return SimpleNamespace(accepted=True)

    context.assistant_gate, context.assistant_effect_id = Gate(), "late-cancel"
    result = _tool_download_entries({"project_id": 7}, context)
    assert result.success and result.data["updated_count"] == 1
    assert case.project.ctx.collection.get(case.entry.identity).translation == "共享流程译文"
    assert_saved(case)
    assert_no_download_results(case)


def test_acknowledgement_error_after_save_returns_success_with_warning(download, monkeypatch):
    case = download

    def fail_ack(*_args, **_kwargs):
        raise TimeoutError("UI acknowledgement unavailable")

    monkeypatch.setattr(ExecutionContext, "safe_mutate_wait", fail_ack)
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success and result.data["updated_count"] == 1
    assert result.warnings and "acknowledgement unavailable" in result.warnings[0]
    assert case.project.ctx.collection.get(case.entry.identity).translation == "共享流程译文"
    assert_saved(case)
    assert_no_download_results(case)


def test_kept_concurrent_local_edit_is_reported_as_unchanged(download):
    case = download

    def edit_during_read():
        result = case.project.ctx.project_commands.update_entry(
            case.entry.identity, case.project.request, translation="下载期间的本地编辑", stage=1
        )
        assert result.is_success, result.diagnostics

    case.files.on_read = edit_during_read
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success and result.data["updated_count"] == 0 and result.data["unchanged_count"] == 1
    assert case.project.ctx.collection.get(case.entry.identity).translation == "下载期间的本地编辑"
    assert_saved(case)
    assert_no_download_results(case)


def test_unresolved_translations_keep_local_text_and_return_only_final_statistics(download):
    case = download
    row = case.files.rows[1][0]
    case.files.rows[1].append(dict(row, id=2, translation="不同候选译文"))
    result = _tool_download_entries({"project_id": 7}, ExecutionContext(app_context=case.project.ctx))
    assert result.success and result.partial and result.data["pending_count"] == 1
    assert "无需更新" not in result.message
    assert not case.project.ctx.collection.get(case.entry.identity).translation
    assert "result_id" not in result.data and "entries" not in result.data
    assert_no_download_results(case)


def test_late_source_switch_does_not_acknowledge_another_source_as_the_captured_target(download, monkeypatch):
    case = download
    first_key = case.project.ctx.active_key
    path = write_json(case.project, "second.json", [{"key": "other", "original": "Other", "translation": ""}])
    imported = _tool_import_json(
        {"path": path, "action": "create_slot"}, ExecutionContext(app_context=case.project.ctx)
    )
    assert imported.success, imported.message
    second_key = case.project.ctx.active_key
    case.project.ctx.activate_slot(first_key)
    context = ExecutionContext(app_context=case.project.ctx)
    original = ExecutionContext.safe_mutate_wait

    def switch_before_acknowledgement(dispatch, action, **kwargs):
        case.project.ctx.activate_slot(second_key)
        return original(dispatch, action, **kwargs)

    monkeypatch.setattr(ExecutionContext, "safe_mutate_wait", switch_before_acknowledgement)
    result = _tool_download_entries({"project_id": 7}, context)
    assert result.success and result.data["updated_count"] == 1 and result.warnings
    assert context._target_source_key == first_key
    assert case.project.ctx.active_key == second_key
    assert_saved(case)
    assert_no_download_results(case)
