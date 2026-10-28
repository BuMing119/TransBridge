"""Assistant AI results remain detached, durable, scoped and explicitly reusable."""

from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest

from tests.application.projects.test_categorized_upload import project as project_fixture
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.ai_translator.translator import TranslationResult
from transbridge.application.io.publish import CommitDecision
from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.config.llm import LLMConfig
from transbridge.config.task_results import TaskResultPreferences
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.translation_task_merge import merge_saved_results
from transbridge.smart_assistant.tools.translation_task_results import (
    AssistantTaskResults,
    load_task_result,
    result_view,
)
from transbridge.smart_assistant.tools.translation_task_run import execute_task
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture


@pytest.fixture
def environment(project):
    services, sources, context, *_ = project
    context = replace(context, session_id="assistant-session")
    snapshot = sources.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42).value
    collection = TranslationEntryCollection(entry for source in snapshot.sources for entry in source.entries)
    active = services.project_lifecycle.active
    identity = active.project_ref.identity.value, active.formal_variant_ref.identity.value
    app = SimpleNamespace(
        collection=collection,
        active_version_identity=identity,
        project_commands=services.gui_project_commands,
        runtime_context=context,
        slots={},
        esp_path="fixed.esp",
        variant_revision=active.variant.revision,
        task_result_preferences_provider=lambda: TaskResultPreferences(ai_policy=ResultConflictPolicy.KEEP_CURRENT),
    )
    return app, services, context


def new_task(environment, *, mode="translate"):
    app, _services, _context = environment
    return AssistantTaskResults(
        app,
        app.collection,
        list(app.collection),
        LLMConfig(api_key="test-only", term_priority=[]),
        SimpleNamespace(snapshot_ref=None),
        mode=mode,
    )


def succeed(task):
    outcome = SimpleNamespace(task=task.task, polish={}, error="", translation=TranslationResult())
    outcome.translation.entry_outcomes = {
        entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", f"result:{entry.original}", 3)
        for entry in task.task.translate_entries
    }
    task.ledger.ingest((outcome,), set(task.ledger.entries))


def current_texts(environment):
    _app, services, _context = environment
    return {
        entry.entry_key: entry.translation for entry in services.project_lifecycle.active.variant.snapshot().entries
    }


def test_detached_results_merge_latest_content_by_key_and_preserve_conflict_evidence(environment):
    app, services, context = environment
    task = new_task(environment)
    succeed(task)
    key = next(iter(task.ledger.entries))
    assert services.gui_project_commands.update_entry(key, context, translation="manual", stage=5).is_success
    assert all(not entry.translation for entry in app.collection)
    task.persist(state="completed")
    summary, warning = task.merge()
    assert summary["applied_count"] == 1 and summary["kept_current_count"] == 1
    assert warning == "" and current_texts(environment)[key] == "manual"
    evidence = result_view(app, task.result_id)["merge_evidence"]
    assert evidence[0]["conflicts"][0]["current"]["translation"] == "manual"
    assert evidence[0]["conflicts"][0]["incoming"]["translation"].startswith("result:")


def test_remerge_uses_saved_results_and_new_policy_after_runtime_is_gone(environment):
    app, services, context = environment
    task = new_task(environment)
    succeed(task)
    key = next(iter(task.ledger.entries))
    assert services.gui_project_commands.update_entry(key, context, translation="manual", stage=5).is_success
    task.persist(state="completed")
    task.merge()
    TaskManager.reset()
    store, record, payload = load_task_result(app, task.result_id)
    summary, _warning = merge_saved_results(app, store, record, payload, policy="prefer_incoming")
    assert summary["applied_count"] == 1
    assert current_texts(environment)[key] == task.ledger.entries[key].text


def test_failed_result_save_retains_queryable_memory_and_retry_never_merges_early(environment, monkeypatch):
    app, _services, _context = environment
    task = new_task(environment)
    succeed(task)
    save = task.store.save_results
    monkeypatch.setattr(task.store, "save_results", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        task.persist(state="completed")
    view = result_view(app, task.result_id)
    assert view["state"] == "persistence_failed" and not view["durable"]
    assert {row["text"] for row in view["execution"]} == {row.text for row in task.ledger.entries.values()}
    assert set(current_texts(environment).values()) == {""}
    monkeypatch.setattr(task.store, "save_results", save)
    store, record, payload = load_task_result(app, task.result_id)
    summary, _warning = merge_saved_results(app, store, record, payload)
    assert summary["applied_count"] == 2


def test_query_survives_version_switch_but_merge_cannot_write_other_version(environment):
    app, _services, _context = environment
    task = new_task(environment)
    succeed(task)
    task.persist(state="completed")
    original_identity = app.active_version_identity
    app.active_version_identity = original_identity[0], "another-variant"
    assert len(result_view(app, task.result_id)["results"]) == 2
    with pytest.raises(ValueError, match="切换"):
        task.merge()
    assert set(current_texts(environment).values()) == {""}


def test_result_authorization_does_not_depend_on_task_manager(environment):
    app, _services, context = environment
    task = new_task(environment)
    succeed(task)
    task.persist(state="completed")
    app.runtime_context = replace(context, session_id="another-session")
    with pytest.raises(ValueError, match="会话"):
        result_view(app, task.result_id)
    app.runtime_context = replace(context, owner_id="another-owner")
    with pytest.raises(ValueError, match="会话"):
        load_task_result(app, task.result_id)


def test_task_start_never_retargets_captured_inputs_to_new_active_identity(environment):
    app, _services, context = environment
    ctx = ExecutionContext(app_context=app, request_context=context)
    app.active_version_identity = app.active_version_identity[0], "switched"
    with pytest.raises(ValueError, match="切换"):
        AssistantTaskResults(
            ctx,
            app.collection,
            list(app.collection),
            LLMConfig(api_key="test"),
            SimpleNamespace(snapshot_ref=None),
            mode="translate",
        )


def test_successful_merge_receipt_keeps_identity_without_retargeting_following_inputs(environment, monkeypatch):
    import json

    from transbridge.smart_assistant.request_scope import apply_captured_scope, execution_version

    app, services, context = environment
    app.project_revision = services.project_lifecycle.active.project.envelope.revision
    ctx = ExecutionContext(app_context=app, request_context=context)
    next_tool = ExecutionContext(app_context=app, request_context=context)
    task = AssistantTaskResults(
        ctx,
        app.collection,
        list(app.collection),
        LLMConfig(api_key="test"),
        SimpleNamespace(snapshot_ref=None),
        mode="translate",
    )
    succeed(task)
    task.persist(state="completed")
    original = app.project_commands.merge_entry_states

    def merge(*args, **kwargs):
        result = original(*args, **kwargs)
        app.variant_revision = services.project_lifecycle.active.variant.revision
        return result

    monkeypatch.setattr(app.project_commands, "merge_entry_states", merge)
    collection = ctx._target_collection
    task.merge()
    receipt = execution_version(ctx)
    assert json.loads(receipt) == {"active_version_identity": list(app.active_version_identity)}
    request = SimpleNamespace(source_message_ids=(), execution_version_json=receipt)
    captured_revision = next_tool._target_variant_revision
    apply_captured_scope(request, (), next_tool)
    assert next_tool._target_variant_revision == captured_revision
    assert next_tool._target_collection is collection
    assert ctx._target_collection is collection


@pytest.mark.parametrize("failure", ["exception", "rejected"])
def test_commit_receipt_failure_after_actual_publish_reports_applied(environment, failure):
    task = new_task(environment)
    succeed(task)
    task.persist(state="completed")

    def commit(_run_id, mutation):
        mutation()
        if failure == "exception":
            raise OSError("effect receipt disk full")
        return CommitDecision(False, "receipt unavailable")

    execution = SimpleNamespace(ref=SimpleNamespace(run_id="execution"), commit=commit)
    summary, warning = task.merge(execution=execution)
    assert summary["applied_count"] == 2 and "合入已完成" in warning
    assert all(text.startswith("result:") for text in current_texts(environment).values())


def test_denied_cancelled_execution_never_merges_but_preserves_saved_results(environment):
    task = new_task(environment)
    succeed(task)
    task.persist(state="cancelled")
    execution = SimpleNamespace(
        ref=SimpleNamespace(run_id="cancelled"),
        commit=lambda *_args: CommitDecision(False, "cancelled"),
    )
    with pytest.raises(RuntimeError, match="合入未完成"):
        task.merge(execution=execution)
    assert set(current_texts(environment).values()) == {""}
    assert len(result_view(environment[0], task.result_id)["results"]) == 2


def test_pending_merge_receipt_can_be_retried_without_reapplying(environment, monkeypatch):
    task = new_task(environment)
    succeed(task)
    task.persist(state="completed")
    from transbridge.application.translation import task_merge_evidence

    atomic = task_merge_evidence.atomic_json

    def fail_receipt(path, payload):
        if path.name.endswith(".applied.json"):
            raise OSError("receipt disk full")
        return atomic(path, payload)

    monkeypatch.setattr(task_merge_evidence, "atomic_json", fail_receipt)
    summary, warning = task.merge()
    assert summary["applied_count"] == 2 and not summary["receipt_saved"] and "合入已完成" in warning
    monkeypatch.setattr(task_merge_evidence, "atomic_json", atomic)
    view = result_view(environment[0], task.result_id)
    assert view["merge"]["receipt_saved"]
    evidence = TaskMergeEvidenceStore(task.store.root, task.result_id).load()
    assert len(evidence[0]["applied_keys"]) == 2


def test_cancelled_translation_keeps_only_explicit_valid_partial_outcomes(environment, monkeypatch):
    task = new_task(environment)
    task.terminology = SimpleNamespace(translator_kwargs=lambda: {})
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.translation_task_run._capture_references",
        lambda task, *_args: setattr(task, "references", None),
    )

    class Translator:
        def __init__(self, *_args, **_kwargs):
            pass

        def translate(self, *, collection, stop_event, **_kwargs):
            first, second = tuple(collection)
            assert not first.translation and not second.translation
            collection.add(replace(first, translation="finished", stage=3), overwrite=True)
            stop_event.set()
            return TranslationResult(
                entry_outcomes={
                    first.identity: TranslationEntryOutcome(first.identity, "succeeded", "finished", 3),
                    second.identity: TranslationEntryOutcome(second.identity, "cancelled", "", 0, "stopped"),
                }
            )

    monkeypatch.setattr("transbridge.ai_translator.translator.AutoTranslator", Translator)
    TaskManager.reset()
    manager = TaskManager()
    task_id = manager.register(stop_event=threading.Event())
    handle = manager.get_handle(task_id)
    worker = manager.start_thread(
        task_id,
        lambda: execute_task(task, manager, task_id, handle, None, None, intensity="medium", strategy="proofread"),
    )
    worker.join(5)
    try:
        assert not worker.is_alive()
        view = result_view(environment[0], task.result_id)
        assert view["state"] == "cancelled" and len(view["results"]) == 1
        assert view["results"][0]["text"] == "finished"
        assert {row["status"] for row in view["execution"]} == {"succeeded", "cancelled"}
        assert set(current_texts(environment).values()) == {""}
        assert all(not entry.translation for entry in environment[0].collection)
    finally:
        TaskManager.reset()


def test_original_matched_same_local_key_results_keep_full_identity(environment):
    from tests.conftest import make_entry
    from transbridge.application.io.identity import EntryKey, SourceNamespace

    app, _services, _context = environment
    first = replace(make_entry("shared", "Alpha"), entry_key=EntryKey(SourceNamespace("test"), "shared", "Alpha"))
    second = replace(make_entry("shared", "Beta"), entry_key=EntryKey(SourceNamespace("test"), "shared", "Beta"))
    app.collection = TranslationEntryCollection((first, second))
    task = new_task(environment)
    succeed(task)
    task.persist(state="completed")
    _store, record, payload = load_task_result(app, task.result_id)
    assert len(record["sources"][0]["translate_keys"]) == 2
    assert {row["entry_key"]["original"] for row in payload["rows"]} == {"Alpha", "Beta"}
    assert {row["text"] for row in payload["rows"]} == {"result:Alpha", "result:Beta"}


@pytest.mark.parametrize("mode", ["translate", "polish", "postprocess"])
def test_assistant_manifest_uses_shared_config_signature_and_gui_never_reexecutes_it(environment, mode):
    from transbridge.application.translation.task_config_snapshot import execution_signature, restore_execution_snapshot
    from transbridge.ui.tools.ai_translator.task_recovery_binding import configure_recovery
    from transbridge.ui.tools.ai_translator.task_source_selection import recover_task_sources

    task = new_task(environment, mode=mode)
    restored = restore_execution_snapshot(LLMConfig(api_key="new-credential"), task.record["execution_config"])
    assert execution_signature(restored) == execution_signature(task.config) == task.record["config_digest"]
    restored.max_concurrent += 1
    assert execution_signature(restored) == task.record["config_digest"]
    restored.model = "changed-model"
    assert execution_signature(restored) != task.record["config_digest"]
    assert not task.record["supported"] and task.record["results_only"]
    with pytest.raises(ValueError, match="merge_task_result"):
        configure_recovery(SimpleNamespace(), task.record)
    with pytest.raises(ValueError, match="merge_task_result"):
        recover_task_sources((task.record, False), task.identity, mode, task.config, {})


def test_launch_freezes_config_and_records_runtime_result_link(environment, monkeypatch):
    from transbridge.smart_assistant.tools.tool_translator import TranslationController

    app, _services, _context = environment
    config = LLMConfig(api_key="test", model="frozen-model", term_priority=[])
    monkeypatch.setattr("transbridge.paratranz.config_manager.LLMConfig.load_from_file", lambda: config)
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.translation_task_run._capture_references",
        lambda task, *_args: setattr(task, "references", None),
    )
    entered, release = threading.Event(), threading.Event()
    captured = []

    class Translator:
        def __init__(self, cfg, *_args, **_kwargs):
            self.config = cfg.llm_config

        def translate(self, *, collection, **_kwargs):
            entered.set()
            assert release.wait(5)
            captured.append(self.config.model)
            return TranslationResult(
                entry_outcomes={
                    entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "fixed-result", 3)
                    for entry in collection
                }
            )

    monkeypatch.setattr("transbridge.ai_translator.translator.AutoTranslator", Translator)
    TaskManager.reset()
    manager = TaskManager()
    result = TranslationController(task_manager=manager).start_translation({}, app, app.collection)
    try:
        assert result.success and entered.wait(5)
        config.model = "edited-while-running"
        handle = manager.get_handle(result.data["task_id"])
        assert handle.metadata["result_id"] == result.data["result_id"]
    finally:
        release.set()
        if result.success:
            manager.get_handle(result.data["task_id"])._thread.join(5)
        TaskManager.reset()
    assert captured == ["frozen-model"]
    assert result_view(app, result.data["result_id"])["state"] == "completed"


def test_polish_cancellation_preserves_valid_completed_candidate(environment, monkeypatch):
    app = environment[0]
    task = AssistantTaskResults(
        app,
        app.collection,
        [next(iter(app.collection))],
        LLMConfig(api_key="test"),
        SimpleNamespace(snapshot_ref=None),
        mode="polish",
    )
    task.terminology = SimpleNamespace(term_database_kwargs=lambda: {})
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.translation_task_run._capture_references",
        lambda task, *_args: setattr(task, "references", None),
    )
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._polish_llm_runtime.create_polish_llm_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(
            client=object(),
            close=lambda: None,
            log_store=SimpleNamespace(write_line=lambda *_args: None),
        ),
    )
    monkeypatch.setattr(
        "transbridge.ai_translator.term_database.TermDatabaseManager",
        lambda **_kwargs: SimpleNamespace(load_all=lambda: None),
    )

    def polish(*, targets, stop_event, **_kwargs):
        from transbridge.application.translation.entry_alias import ai_entry_id

        stop_event.set()
        return SimpleNamespace(
            results={
                ai_entry_id(targets[0]): SimpleNamespace(
                    accepted=False,
                    processing_status="completed",
                    candidate_translation="validated polish",
                    polished_translation="",
                    target_stage=3,
                    note="complete before cancellation",
                ),
            }
        )

    monkeypatch.setattr("transbridge.smart_assistant.tools._polish_execution.execute_polish", polish)
    TaskManager.reset()
    manager = TaskManager()
    task_id = manager.register()
    handle = manager.get_handle(task_id)
    worker = manager.start_thread(
        task_id,
        lambda: execute_task(task, manager, task_id, handle, None, None, intensity="heavy", strategy="strict"),
    )
    worker.join(5)
    try:
        assert not worker.is_alive()
        view = result_view(environment[0], task.result_id)
        assert view["state"] == "cancelled"
        assert [row["text"] for row in view["results"]] == ["validated polish"]
        assert set(current_texts(environment).values()) == {""}
    finally:
        TaskManager.reset()
