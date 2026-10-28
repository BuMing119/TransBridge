"""Real V2 facade, task actions and durable result review work together."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtWidgets import QPushButton
import pytest

from tests.application.projects.test_categorized_upload import project as project_fixture
from tests.ui.tools import test_ai_partial_task as support
from tests.ui.tools.test_task_recovery_binding import setup_binding
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.config.task_results import TaskResultPreferences
from transbridge.ui.context import AppContext
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import task_run
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_merge_dialog import TaskMergeDialog
from transbridge.ui.tools.ai_translator.task_merge_review import saved_state

project = project_fixture
qapp = support.qapp


@pytest.fixture
def real_task(project, qapp, monkeypatch, request):
    services, sources, context, *_ = project
    snapshot = sources.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42)
    assert snapshot.is_success
    app_context = AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=context,
        task_result_preferences_provider=lambda: TaskResultPreferences(ai_policy=ResultConflictPolicy.DEFER),
    )
    app_context._slots = {
        source.source_id: CollectionSlot(source.label, source.entries) for source in snapshot.value.sources
    }
    # A source snapshot contains immutable tuples; the UI collections own their
    # ordinary mutation boundary just as hydrated workbench slots do.
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    for slot in app_context._slots.values():
        slot.collection = TranslationEntryCollection(slot.collection)
    mode = getattr(request, "param", "translate")
    tasks = tuple(
        SourceTask(
            key,
            slot.label,
            None,
            slot.collection,
            tuple(slot.collection) if mode == "translate" else (),
            tuple(slot.collection) if mode == "polish" else (),
        )
        for key, slot in app_context.slots.items()
    )
    from tests.ui.tools.ai_runtime_support import ai_task_runtime
    from transbridge.ui.tools.ai_translator import task_launch
    from transbridge.ui.tools.ai_translator.run_controller import RunController
    from transbridge.ui.tools.ai_translator.run_spec import AiPreflightResult

    directory = services.gui_project_commands.task_result_directory(context)
    monkeypatch.setattr(task_run, "AiTaskWorker", support._Worker)
    monkeypatch.setattr(task_launch, "preflight_ai_run", lambda *_args, **_kwargs: AiPreflightResult())
    monkeypatch.setattr(task_launch.TermSourceInspector, "all_empty", lambda *_args: False)
    errors, progress = [], []
    monkeypatch.setattr(task_launch.QMessageBox, "warning", lambda _owner, title, error: errors.append((title, error)))
    owner = SimpleNamespace(
        _ctx=app_context,
        _config_presenter=SimpleNamespace(
            build=lambda: LLMConfig(model="test", polish_preview_enabled=mode == "polish")
        ),
        _view_port=SimpleNamespace(mode=mode, overwrite=False),
        _theme_view=None,
        _task_sources=lambda **_options: tasks,
        _task_project_directory_provider=lambda: directory,
        _run_controller=RunController(task_runtime=ai_task_runtime()),
        progress_window_created=SimpleNamespace(emit=progress.append),
        close=lambda: None,
    )
    launch = task_launch.TaskLaunch(owner)
    owner._task_launch = launch
    launch.start()
    support._wait(qapp, lambda: bool(progress) or bool(errors))
    assert not errors, errors
    window = progress[0]
    support._wait(qapp, lambda: window.run.worker is not None)
    window.hide()
    yield SimpleNamespace(
        window=window, ctx=app_context, services=services, context=context, directory=directory, launch=launch
    )
    if window.run.worker is not None:
        window.run.cancel()
        window.run.worker.publish(SourceOutcome(window.run.worker.tasks[0], cancelled=True))
    support._settle(window)
    window.dispose()
    app_context.close_projection()


def publish(case):
    run = case.window.run
    worker = run.worker
    outcomes = tuple(
        SourceOutcome(
            task,
            translation=SimpleNamespace(
                entry_outcomes={
                    entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "AI result", 3, "")
                    for entry in task.translate_entries
                }
            ),
            polish={
                entry.id: SimpleNamespace(
                    accepted=True,
                    confidence=0,
                    polished_translation="AI result",
                    processing_status="completed",
                    note="",
                    target_stage=2,
                )
                for entry in task.polish_entries
            },
        )
        for task in worker.tasks
    )
    worker.completed.emit(outcomes)
    worker.running = False
    worker.finished.emit()
    support._settle(case.window)


def current(case, key):
    return next(entry for slot in case.ctx.slots.values() for entry in slot.collection if entry.identity == key)


def open_review(case):
    case.window._open_report()
    result = case.window._dialogs[-1]
    button = next(
        button for button in result.findChildren(QPushButton) if button.text() == "处理已保存的结果 / 恢复译文"
    )
    button.click()
    return case.window._dialogs[-1]


def test_real_task_conflict_retains_result_and_applies_unrelated_success_without_history(real_task):
    case = real_task
    key = case.window.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(key, case.context, translation="manual", stage=5).is_success
    case.ctx.task_result_preferences_provider = lambda: TaskResultPreferences(
        ai_policy=ResultConflictPolicy.PREFER_INCOMING
    )
    publish(case)
    run = case.window.run
    assert not run.blocked and not run.busy
    assert run.entries.entries[key].merge_status == "pending"
    assert current(case, key).translation == "manual"
    assert len(run.entries.applied_keys) == 1
    saved = run.recovery.store.load_results(run.recovery.record["task_id"])
    assert len(saved["rows"]) == 2
    assert not case.services.project_snapshots.list(case.context)
    assert len(list((case.directory / "ai-task-history").glob("*/record-*.json"))) == 1


@pytest.mark.parametrize("real_task", ["polish"], indirect=True)
def test_preview_and_shutdown_preserve_zero_confidence_accepted_results_for_reopening(real_task, qapp):
    case = real_task
    publish(case)
    run = case.window.run
    assert run.state == "pending_confirmation" and run.worker is None
    saved = run.recovery.store.load_results(run.recovery.record["task_id"])
    assert len(saved["rows"]) == 2 and all(row["status"] == "succeeded" for row in saved["rows"])
    run.shutdown()
    support._settle(case.window)
    run.recovery.release()
    reopened = TaskMergeDialog(case.ctx, case.directory, run.recovery.record["task_id"])
    support._wait(qapp, lambda: not reopened.busy)
    assert len(reopened.review.rows) == 2 and not reopened.review.error
    assert all(row["decision"] == "pending" for row in reopened.review.rows.values())
    reopened.close()


def test_result_disk_failure_keeps_memory_and_retries_save_without_model(real_task, monkeypatch):
    case = real_task
    run = case.window.run
    save = run.recovery.store.save_results
    monkeypatch.setattr(run.recovery.store, "save_results", Mock(side_effect=PermissionError("disk full")))
    publish(case)
    assert all(row.status == "succeeded" for row in run.entries.entries.values())
    assert not run.entries.applied_keys and run.worker is None
    assert run.can_apply_success
    monkeypatch.setattr(run.recovery.store, "save_results", save)
    run.apply_results()
    support._settle(case.window)
    assert len(run.entries.applied_keys) == 2 and run.worker is None
    assert run.session.project_saved


def test_result_review_checks_actual_current_then_restores_overwritten_text(real_task, qapp):
    case = real_task
    run = case.window.run
    key = run.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(key, case.context, translation="manual", stage=5).is_success
    publish(case)
    dialog = open_review(case)
    support._wait(qapp, lambda: not dialog.busy)
    # Drive the same selected-row QAction used by the result window buttons.
    row_index = dialog.model.keys.index(key)
    dialog.table.selectRow(row_index)
    dialog.buttons[1].click()
    support._wait(qapp, lambda: not dialog.busy)
    assert current(case, key).translation == "AI result", dialog.review.error
    assert dialog.review.previous[key][0].translation == "manual"
    dialog.table.selectRow(dialog.model.keys.index(key))
    dialog.buttons[2].click()
    support._wait(qapp, lambda: not dialog.busy)
    assert (current(case, key).translation, current(case, key).stage) == ("manual", 5), dialog.review.error
    dialog.close()


def test_real_launch_auto_merge_retains_restorable_evidence_under_admitted_runtime_id(real_task, qapp):
    case = real_task
    run = case.window.run
    assert case.launch.options["run_id"] != run.request.run_id
    assert run.session.initial_entry_results.run_id == run.request.run_id
    assert run.session.merge_evidence_writer.task_id == run.request.run_id
    run.session.policy = ResultConflictPolicy.PREFER_INCOMING
    key = run.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(
        key, case.context, translation="before overwrite", stage=5
    ).is_success
    publish(case)
    assert current(case, key).translation == "AI result"
    dialog = open_review(case)
    try:
        support._wait(qapp, lambda: not dialog.busy)
        assert dialog.review.previous[key][0].translation == "before overwrite"
        dialog.review.resolve({key}, restore=True)
        support._wait(qapp, lambda: not dialog.busy)
        assert (current(case, key).translation, current(case, key).stage) == ("before overwrite", 5)
    finally:
        dialog.close()


def test_review_opened_before_new_edit_cannot_overwrite_that_edit(real_task, qapp):
    case = real_task
    run = case.window.run
    key = run.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(key, case.context, translation="manual", stage=5).is_success
    publish(case)
    dialog = open_review(case)
    support._wait(qapp, lambda: not dialog.busy)
    assert case.services.gui_project_commands.update_entry(
        key, case.context, translation="edited later", stage=2
    ).is_success
    dialog.table.selectRow(dialog.model.keys.index(key))
    dialog.buttons[1].click()
    support._wait(qapp, lambda: not dialog.busy)
    assert current(case, key).translation == "edited later"
    assert dialog.review.rows[key]["merge_reason"] == "decision_stale"
    dialog.close()


def test_repeated_overwrites_restore_the_most_recent_current_text(real_task, qapp):
    case = real_task
    run = case.window.run
    key = run.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(
        key, case.context, translation="manual one", stage=5
    ).is_success
    publish(case)
    dialog = open_review(case)
    support._wait(qapp, lambda: not dialog.busy)
    dialog.review.resolve({key})
    support._wait(qapp, lambda: not dialog.busy)
    assert current(case, key).translation == "AI result"
    assert case.services.gui_project_commands.update_entry(
        key, case.context, translation="manual two", stage=2
    ).is_success
    dialog.review.refresh()
    support._wait(qapp, lambda: not dialog.busy)
    dialog.review.resolve({key})
    support._wait(qapp, lambda: not dialog.busy)
    assert dialog.review.previous[key][0].translation == "manual two"
    dialog.review.resolve({key}, restore=True)
    support._wait(qapp, lambda: not dialog.busy)
    assert (current(case, key).translation, current(case, key).stage) == ("manual two", 2)
    # Identical content-addressed evidence can be reused for a later event.
    # The actual successful receipt, rather than old evidence mtime, orders it.
    assert case.services.gui_project_commands.update_entry(
        key, case.context, translation="manual one", stage=5
    ).is_success
    dialog.review.refresh()
    support._wait(qapp, lambda: not dialog.busy)
    dialog.review.resolve({key})
    support._wait(qapp, lambda: not dialog.busy)
    assert dialog.review.previous[key][0].translation == "manual one"
    dialog.review.resolve({key}, restore=True)
    support._wait(qapp, lambda: not dialog.busy)
    assert (current(case, key).translation, current(case, key).stage) == ("manual one", 5)
    dialog.close()


def test_failed_commit_evidence_is_not_a_restore_event(real_task, qapp, monkeypatch):
    case = real_task
    key = case.window.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(key, case.context, translation="manual", stage=5).is_success
    publish(case)
    dialog = open_review(case)
    support._wait(qapp, lambda: not dialog.busy)
    variant = case.services.project_lifecycle.active.variant
    commit = type(variant).commit
    monkeypatch.setattr(type(variant), "commit", Mock(side_effect=PermissionError("commit failed after evidence")))
    dialog.review.resolve({key})
    support._wait(qapp, lambda: not dialog.busy)
    assert current(case, key).translation == "manual"
    monkeypatch.setattr(type(variant), "commit", commit)
    assert case.services.gui_project_commands.update_entry(key, case.context, translation="latest", stage=2).is_success
    dialog.review.refresh()
    support._wait(qapp, lambda: not dialog.busy)
    assert key not in dialog.review.previous
    dialog.review.resolve({key}, restore=True)
    assert current(case, key).translation == "latest"
    assert "没有可恢复" in dialog.review.error
    dialog.close()


def test_result_review_keeps_evidence_io_off_gui(qapp, tmp_path, monkeypatch):
    from dataclasses import replace
    from threading import get_ident

    from transbridge.application.translation.task_entry_results import TaskEntryResults
    from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
    from transbridge.application.translation.task_result_recovery import save_recovery_results

    binding, _, session, window = setup_binding(tmp_path)
    ctx = window._ctx
    ctx.slots = {"first": ctx.slots["first"]}
    ctx.entry_labels, ctx.label_library = {}, {}
    from tests.ui.tools.test_ai_task_session import _v2_commands

    ctx.runtime_context = object()
    ctx.project_commands = _v2_commands(ctx, [])
    ledger = TaskEntryResults(session.tasks[:1], "run")
    ledger.entries = {
        key: replace(row, status="succeeded", text="AI result", stage=3, decision="accepted", attempt=1)
        for key, row in ledger.entries.items()
    }
    save_recovery_results(binding.store, "run", ledger)
    entry = next(iter(ctx.collection))
    entry.translation, entry.stage = "manual", 5
    gui, threads = get_ident(), []
    original_save = TaskMergeEvidenceStore.save

    def save(*args):
        threads.append(get_ident())
        return original_save(*args)

    monkeypatch.setattr(TaskMergeEvidenceStore, "save", save)
    dialog = TaskMergeDialog(ctx, tmp_path, "run", store=binding.store)
    support._wait(qapp, lambda: not dialog.busy)
    dialog.review.resolve({entry.identity})
    support._wait(qapp, lambda: not dialog.busy)
    assert entry.translation == "AI result", dialog.review.error
    assert threads and all(thread != gui for thread in threads)
    dialog.close()
    binding.release()


@pytest.mark.parametrize("real_task", ["translate", "polish"], indirect=True)
def test_explicit_history_continue_transfers_finished_run_lease_in_same_process(real_task, qapp):
    from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog
    from transbridge.ui.tools.ai_translator.task_recovery_binding import TaskRecoveryBinding
    from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry

    case = real_task
    window, run = case.window, case.window.run
    registry = AiTaskRegistry(case.ctx, directory_provider=lambda: case.directory)
    registry.register(window)
    publish(case)
    if run.state == "pending_confirmation":
        run.cancel()
        support._settle(window)
    assert run.state in {"completed", "cancelled"}
    original = run.recovery.store
    assert original._lease is not None
    resumed = []

    def continue_task(record, restart):
        assert not restart
        from dataclasses import replace

        request = replace(
            run.request,
            spec=replace(run.request.spec, run_id="same-process-continuation"),
            recovery_task_id=record["task_id"],
        )
        session = SimpleNamespace(
            project_dir=case.directory,
            version_identity=window.session.version_identity,
            tasks=window.session.tasks,
            completed=False,
            project_saved=False,
            policy=window.session.policy,
        )
        resumed.append(TaskRecoveryBinding(request, session))

    registry.recovery_requested.connect(continue_task)
    history = TaskHistoryDialog(registry)
    registry.history_windows.append(history)
    support._wait(qapp, lambda: not history.busy and history.tasks.topLevelItemCount() > 0)
    assert history.continue_button.isEnabled()
    history.continue_button.click()
    support._wait(qapp, lambda: len(resumed) == 1)
    assert len(resumed) == 1
    assert original._lease is None
    assert window.request.run_id not in registry.windows
    assert resumed[0].store._lease is not None
    assert "same-process-continuation" in resumed[0].record["execution_runs"]
    resumed[0].release()
    registry.shutdown()
    support._wait(qapp, lambda: not registry.busy)
    history.close()


@pytest.mark.parametrize("blocked_by", ["review", "records", "recovery", "receipt"])
def test_history_continuation_preserves_owner_with_review_or_persistence_error(real_task, qapp, blocked_by):
    from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry

    case = real_task
    run = case.window.run
    registry = AiTaskRegistry(case.ctx, directory_provider=lambda: case.directory)
    registry.register(case.window)
    publish(case)
    dialog = None
    if blocked_by == "review":
        dialog = open_review(case)
        support._wait(qapp, lambda: not dialog.busy)
    elif blocked_by == "records":
        run.records.error = "history disk error"
    elif blocked_by == "recovery":
        run.recovery.error = "result disk error"
    else:
        run.session.merge_evidence_writer.error = "receipt disk error"
    requested = []
    registry.recovery_requested.connect(lambda *_: requested.append(True))
    assert not registry.request_recovery(run.recovery.record)
    assert not requested and case.window.request.run_id in registry.windows
    assert run.recovery.store._lease is not None
    if dialog is not None:
        dialog.close()
    run.records.error = run.recovery.error = run.session.merge_evidence_writer.error = ""
    registry.windows.clear()
    registry.shutdown()
    support._wait(qapp, lambda: not registry.busy)


def test_existing_corrupted_evidence_is_not_treated_as_durable_before_overwrite(real_task):
    case = real_task
    key = case.window.session.tasks[0].entries[0].identity
    assert case.services.gui_project_commands.update_entry(key, case.context, translation="manual", stage=5).is_success
    publish(case)
    evidence = case.window.session.merge_evidence_writer
    records = evidence.load()
    assert records
    path = next(
        path for path in evidence.root.glob(f"{evidence._task_token}-*.json") if not path.name.endswith(".applied.json")
    )
    path.write_text("{", encoding="utf-8")
    from transbridge.application.translation.result_merge import ResultEntryChange, ResultMergeConflict, ResultMergePlan

    saved = records[0]
    # Recreate exactly the same payload so the addressed path is an existing hit.
    candidates = tuple(
        ResultEntryChange(saved_state(row["base"]), saved_state(row["incoming"])) for row in saved["candidates"]
    )
    plan = ResultMergePlan(
        conflicts=tuple(
            ResultMergeConflict(
                saved_state(row["base"]).entry_key,
                row["reason"],
                saved_state(row["base"]),
                None if row["current"] is None else saved_state(row["current"]),
                saved_state(row["incoming"]),
                row["decision"],
            )
            for row in saved["conflicts"]
        ),
    )
    with pytest.raises(ValueError, match="证据损坏"):
        evidence.save(candidates, plan, ResultConflictPolicy.DEFER)
    assert current(case, key).translation == "manual"
