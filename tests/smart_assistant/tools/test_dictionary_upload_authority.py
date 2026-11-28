"""Verify dictionary authority, request cancellation and persistence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import monotonic
from types import SimpleNamespace
from xml.etree import ElementTree

from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.publish import CommitDecision
from transbridge.application.projects.dictionary_apply import dictionary_library
from transbridge.application.tasks.controls import CancellationToken
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import _dictionary_application
from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary, _tool_save_dictionary
from transbridge.smart_assistant.tools.tool_translator import _tool_get_task_result
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.project_rows import ProjectRowsCollection, build_paged_slots

project = project_fixture


@pytest.fixture(scope="session", autouse=True)
def dictionary_qapp():
    # ApiWorker's application-owned Qt status buses must outlive successive projects.
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def dictionary_directory(tmp_path, monkeypatch):
    directory = tmp_path / "dictionary"
    monkeypatch.setattr("transbridge.translation_memory.library.default_library_dir", lambda: directory)


def wait_for_qt(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def _snapshot(project):
    return project.services.project_lifecycle.active.variant.snapshot()


def test_current_source_personal_record_wins_and_preserves_stage(project):
    entry = load_source(project)
    assert entry.stage == 0 and not entry.translation
    library = dictionary_library(project.ctx)
    library.save((replace(entry, translation="Main value", stage=1),), project.source.stem, main="Shared")
    library.select_main("Shared")
    library.save((replace(entry, translation="Other value", stage=1),), "Other")
    library.save((replace(entry, translation="Source value", stage=1),), project.source.stem)
    before = _snapshot(project)
    context = ExecutionContext(app_context=project.ctx)
    request = SimpleNamespace(source_message_ids=(), execution_version_json="")
    apply_captured_scope(request, (), context)
    assert context.active_slot is None
    previous_results = _tool_get_task_result({}, context).data
    result = _tool_apply_dictionary({}, context)
    assert result.success, result.message
    assert result.data == {"applied": 1, "key_hits": 1, "text_hits": 0, "misses": 0}
    assert _tool_get_task_result({}, context).data == previous_results
    current = _snapshot(project)
    assert current.revision == before.revision + 1
    assert current.entries[0].translation == project.ctx.collection.get(entry.identity).translation == "Source value"
    assert current.entries[0].stage.value == project.ctx.collection.get(entry.identity).stage == 0
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    persisted = project.services.variants.load(current.ref).value.envelope.data["entries"][0]
    assert (persisted["translation"], persisted["stage"]) == ("Source value", 0)


def test_dictionary_application_requires_a_project_write_service(monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    entry = TranslationEntry("one", "one", "Dragon", "", 0, "NPC_:FULL")
    context = AppContext()
    try:
        context.add_slot(
            "fixture.esp", CollectionSlot("Fixture", TranslationEntryCollection([entry]), esp_path="fixture.esp")
        )
        dictionary_library(context).save((replace(entry, translation="Dictionary text", stage=1),), "fixture")
        result = _tool_apply_dictionary({}, ExecutionContext(app_context=context))
        assert not result.success and "工程写入服务不可用" in result.message
        assert context.collection.get(entry.identity).translation == ""
        assert not context.dirty
    finally:
        context.close_projection()


def test_dictionary_commit_failure_keeps_projection_and_aggregate_unchanged(current_project_factory, monkeypatch):
    entry = TranslationEntry("one", "one", "Dragon", "", 0, "NPC_:FULL")
    project = current_project_factory((entry,), esp_path="fixture.esp")
    dictionary_library(project.context).save((replace(entry, translation="Dictionary text", stage=1),), "fixture")
    before = project.aggregate.snapshot()

    def reject(*_args, **_kwargs):
        assert project.context.collection.get(entry.identity).translation == ""
        return OperationResult.failed(DomainError(ErrorCategory.INTERNAL, "DICTIONARY_WRITE", "injected write failure"))

    monkeypatch.setattr(project.harness.service, "commit_active_variant", reject)
    result = _tool_apply_dictionary({}, ExecutionContext(app_context=project.context))
    assert not result.success and "injected write failure" in result.message
    assert project.aggregate.snapshot() == before
    assert project.context.collection.get(entry.identity).translation == ""
    assert not project.context.dirty


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (_tool_apply_dictionary, {"overwrite": True}),
        (_tool_apply_dictionary, {"source_locale": "en"}),
        (_tool_apply_dictionary, {"target_locale": "fr"}),
        (_tool_save_dictionary, {"mod_file_id": "Other"}),
        (_tool_save_dictionary, {"scope": "project"}),
        (_tool_save_dictionary, {"tags": ["terms"]}),
        (_tool_save_dictionary, {"source_locale": "en"}),
        (_tool_save_dictionary, {"selected_only": "yes"}),
    ],
)
def test_dictionary_rejects_removed_or_invalid_arguments(project, tool, args):
    entry = load_source(project)
    library = dictionary_library(project.ctx)
    before = _snapshot(project)
    assert not tool(args, ExecutionContext(app_context=project.ctx)).success
    assert _snapshot(project) == before
    assert project.ctx.collection.get(entry.identity).translation == ""
    assert not tuple(library.browse())
    assert not ToolRegistry.get("apply_dictionary").parameters.get("properties")
    assert set(ToolRegistry.get("save_dictionary").parameters["properties"]) == {"selected_only"}


def test_loaded_empty_collection_returns_zero_counts(current_project_factory):
    project = current_project_factory(TranslationEntryCollection())
    before = project.aggregate.snapshot()
    result = _tool_apply_dictionary({}, ExecutionContext(app_context=project.context))
    assert result.success, result.message
    assert result.data == {"applied": 0, "key_hits": 0, "text_hits": 0, "misses": 0}
    assert project.aggregate.snapshot() == before


def test_dictionary_cannot_follow_a_changed_path_with_the_same_source_name(project):
    entry = load_source(project)
    context = ExecutionContext(app_context=project.ctx)
    project.ctx.eet_path = str(project.root / "other" / project.source.name)
    dictionary_library(project.ctx).save((replace(entry, translation="Different file", stage=1),), project.source.stem)
    before = _snapshot(project)
    result = _tool_apply_dictionary({}, context)
    assert not result.success and "来源文件" in result.message
    assert _snapshot(project) == before


@pytest.mark.parametrize("interruption", ["cancelled", "source_switched", "edited", "game_changed"])
def test_dictionary_checks_authority_after_preparing_on_a_worker(project, monkeypatch, interruption):
    entry = load_source(project)
    dictionary_library(project.ctx).save((replace(entry, translation="Saved", stage=1),), project.source.stem)
    prepared, release = Event(), Event()
    context = ExecutionContext(app_context=project.ctx)
    context.cancellation_token = CancellationToken()
    prepare = _dictionary_application.prepare_library_application
    bind = _dictionary_application.dictionary_library

    def observe_binding(app, **options):
        assert QThread.currentThread() == project.ctx.thread()
        return bind(app, **options)

    def hold_preparation(*args, **kwargs):
        assert QThread.currentThread() != project.ctx.thread()
        plan = prepare(*args, **kwargs)
        assert plan.result.applied == 1
        prepared.set()
        assert release.wait(5)
        return plan

    monkeypatch.setattr(_dictionary_application, "dictionary_library", observe_binding)
    monkeypatch.setattr(_dictionary_application, "prepare_library_application", hold_preparation)
    original_slot = project.ctx.active_slot
    with ThreadPoolExecutor(max_workers=1) as worker:
        call = worker.submit(_tool_apply_dictionary, {}, context)
        try:
            wait_for_qt(prepared.is_set)
            if interruption == "cancelled":
                context.cancellation_token._cancel("cancelled")
            elif interruption == "source_switched":
                project.ctx.add_slot("other.esp", CollectionSlot("Another source", TranslationEntryCollection()))
            elif interruption == "edited":
                assert project.ctx.update_projected_entry(entry.identity, translation="Later", stage=1).is_success
            else:
                project.ctx.game_profile = "different_game"
            before = _snapshot(project)
        finally:
            release.set()
        wait_for_qt(call.done)
    assert not call.result().success
    assert _snapshot(project) == before
    assert original_slot.collection.get(entry.identity).translation != "Saved"


@pytest.mark.parametrize(("match", "counts"), [("changed_original", (0, 0, 1)), ("text", (0, 1, 0))])
def test_dictionary_reports_match_outcomes_and_repeat_is_noop(project, match, counts):
    entry = load_source(project)
    if match == "changed_original":
        candidate = replace(entry, original="Outdated original", translation="Value", stage=1)
    else:
        candidate = TranslationEntry(
            id="different key",
            key="different key",
            original=entry.original,
            translation="Value",
            stage=1,
            context=entry.context,
        )
    dictionary_library(project.ctx).save((candidate,), project.source.stem)
    context = ExecutionContext(app_context=project.ctx)
    result = _tool_apply_dictionary({}, context)
    assert result.success, result.message
    assert tuple(result.data[key] for key in ("key_hits", "text_hits", "misses")) == counts
    expected = int(match == "text")
    assert result.data["applied"] == expected
    assert project.ctx.collection.get(entry.identity).translation == ("Value" if expected else "")
    before = _snapshot(project)
    repeated = _tool_apply_dictionary({}, context)
    assert repeated.success and repeated.data["applied"] == 0
    assert _snapshot(project) == before


def test_dictionary_save_uses_current_source_and_personal_library(project):
    entry = load_source(project)
    assert project.ctx.update_projected_entry(entry.identity, translation="Saved", stage=1).is_success
    library = dictionary_library(project.ctx)
    library.save((replace(entry, translation="Main", stage=1),), "Other", main="Shared")
    library.select_main("Shared")
    result = _tool_save_dictionary({}, ExecutionContext(app_context=project.ctx))
    assert result.success and result.data["saved"] == 1, result.message
    library.select_main("")
    assert list(library.browse()) == [(entry.original, "Saved", project.source.stem)]


def test_selected_dictionary_save_reads_exact_project_rows_on_worker(project, monkeypatch):
    tree = ElementTree.parse(project.source)
    another = ElementTree.fromstring(ElementTree.tostring(tree.getroot()[0]))
    another.find("ID").text = "00000002"
    another.find("IDSTEXTE").text = "2"
    another.find("ORIGINAL").text = "Another original"
    tree.getroot().append(another)
    tree.write(project.source, encoding="utf-8", xml_declaration=True)
    load_source(project)
    first, second = project.ctx.collection
    for entry in (first, second):
        assert project.ctx.update_projected_entry(entry.identity, translation=entry.original, stage=1).is_success
    assert project.services.gui_project_commands.save(project.request).is_success
    opener = project.services.current_project_opener
    prepared = opener.prepare_active(project.request, paged=True)
    assert prepared.is_success, prepared.diagnostics
    token = project.ctx.begin_project_open()
    try:
        opened = opener.activate(prepared.value, project.request)
        assert opened.is_success, opened.diagnostics
        snapshot = project.services.project_projection.snapshot()
        assert project.ctx.install_project_open(token, snapshot, build_paged_slots(prepared.value.records.paged_read))
    finally:
        project.ctx.finish_project_open(token)
    assert isinstance(project.ctx.collection, ProjectRowsCollection)
    selected = project.ctx.collection.get(second.identity)
    project.ctx.workbench_row_selection = SimpleNamespace(entries=lambda: (selected,))
    reads = []
    freeze = ProjectRowsCollection.freeze_entries

    def observe_freeze(collection):
        assert QThread.currentThread() == project.ctx.thread()
        read = freeze(collection)

        def observe(keys=None):
            reads.append((keys, QThread.currentThread() == project.ctx.thread()))
            return read(keys)

        return observe

    monkeypatch.setattr(ProjectRowsCollection, "freeze_entries", observe_freeze)
    with ThreadPoolExecutor(max_workers=1) as worker:
        context = ExecutionContext(app_context=project.ctx)
        call = worker.submit(_tool_save_dictionary, {"selected_only": True}, context)
        wait_for_qt(call.done)
        assert call.result().success, call.result().message
    assert reads == [((selected.identity,), False)]
    assert list(dictionary_library(project.ctx).browse()) == [
        (selected.original, selected.translation, project.source.stem)
    ]


@pytest.mark.parametrize("interruption", ["cancelled", "edited", "missing_gate", "revoked", "lost_receipt"])
def test_dictionary_save_transaction_respects_authority(project, monkeypatch, interruption):
    entry = load_source(project)
    library = dictionary_library(project.ctx)
    library.save((replace(entry, translation="Original value", stage=1),), project.source.stem)
    assert project.ctx.update_projected_entry(entry.identity, translation="New value", stage=1).is_success
    staged, release = Event(), Event()
    save = DictionaryLibrary.save

    def hold_stage(library, entries, mod_id, *, commit, **kwargs):
        def publish_after_check(publish):
            assert QThread.currentThread() != project.ctx.thread()
            staged.set()
            assert release.wait(5)
            commit(publish)

        return save(library, entries, mod_id, commit=publish_after_check, **kwargs)

    monkeypatch.setattr(DictionaryLibrary, "save", hold_stage)
    context = ExecutionContext(app_context=project.ctx)
    context.cancellation_token = CancellationToken()
    if interruption == "missing_gate":
        context.assistant_required = True
    elif interruption in {"revoked", "lost_receipt"}:

        def commit(effect_id, publish):
            assert effect_id == "dictionary-save"
            if interruption == "lost_receipt":
                publish()
                return CommitDecision(False, "ADMISSION_PERSIST_FAILED")
            return CommitDecision(False, "TURN_LEASE_STALE")

        context.assistant_gate = SimpleNamespace(commit=commit)
        context.assistant_effect_id = "dictionary-save"
    with ThreadPoolExecutor(max_workers=1) as worker:
        call = worker.submit(_tool_save_dictionary, {}, context)
        try:
            if interruption != "missing_gate":
                wait_for_qt(staged.is_set)
                if interruption == "cancelled":
                    context.cancellation_token._cancel("cancelled")
                elif interruption == "edited":
                    assert project.ctx.update_projected_entry(entry.identity, translation="Later", stage=1).is_success
        finally:
            release.set()
        wait_for_qt(call.done)

    assert staged.is_set() == (interruption != "missing_gate")
    result = call.result()
    if interruption == "lost_receipt":
        assert result.success and "助手回执未确认" in result.warnings[0]
        assert list(library.browse()) == [(entry.original, "New value", project.source.stem)]
    else:
        assert not result.success
        assert list(library.browse()) == [(entry.original, "Original value", project.source.stem)]
