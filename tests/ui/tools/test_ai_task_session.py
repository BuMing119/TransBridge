from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.tools.ai_translator.task_scope import SourceTask
from transbridge.ui.version_persistence import VersionPersistence


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _entry(source, text=""):
    return TranslationEntry(
        "shared-legacy-id",
        "entry",
        "source",
        text,
        1 if text else 0,
        "NPC_:FULL",
        entry_key=EntryKey(SourceNamespace(source), "entry"),
        metadata=(("nested", {"value": [1]}),),
    )


class _Context:
    active_version_identity = ("project", "variant")
    project_revision = 3
    variant_revision = 4
    uses_authoritative_projection = True

    def __init__(self):
        self.slots = {
            "first": CollectionSlot("First", TranslationEntryCollection([_entry("first")])),
            "second": CollectionSlot("Second", TranslationEntryCollection([_entry("second", "旧译文")])),
            "third": CollectionSlot("Third", TranslationEntryCollection([_entry("third", "不变")])),
        }
        self.notifications = []
        self.collection_changed = SimpleNamespace(emit=self.notifications.append)

    @property
    def collection(self):
        return self.slots["first"].collection


class _Persistence:
    def __init__(self, ctx, identity):
        self.ctx, self.identity = ctx, identity
        self.snapshots, self.commits, self.saves = [], [], []
        self.fail_snapshot = self.fail_commit = self.fail_save = False

    def create_snapshot(self, name, entries):
        self.snapshots.append((name, deepcopy(entries)))
        return SimpleNamespace(is_success=not self.fail_snapshot)

    def commit_translation(self, entries):
        self.commits.append(deepcopy(entries))
        return SimpleNamespace(is_success=not self.fail_commit)

    def save_translation(self, entries, name):
        self.saves.append((name, deepcopy(entries)))
        return SimpleNamespace(is_success=not self.fail_save)


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(task_session, "VersionPersistence", _Persistence)
    ctx = _Context()
    tasks = []
    for key in ("first", "second"):
        slot = ctx.slots[key]
        entry = next(iter(slot.collection))
        tasks.append(
            SourceTask(
                key,
                slot.label,
                None,
                slot.collection,
                (entry,) if key == "first" else (),
                (entry,) if key == "second" else (),
            )
        )
    return task_session.TaskSession(ctx, tuple(tasks), SimpleNamespace(mode="mixed", run_id="test"))


def _wait(qapp, session):
    deadline = time.monotonic() + 5
    while session.is_busy and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert not session.is_busy


def _capture(qapp, session):
    success, errors = [], []
    session.capture_before(on_success=success.append, on_error=errors.append)
    _wait(qapp, session)
    return success, errors


def _save(qapp, session):
    success, errors = [], []
    session.save_translation(on_success=success.append, on_error=errors.append)
    _wait(qapp, session)
    return success, errors


def test_task_output_is_deeply_detached_until_all_sources_commit(qapp, session):
    original = session._ctx.collection
    translated = session.tasks[0].translate_entries[0]
    polished = session.tasks[1].polish_entries[0]
    assert translated is session.tasks[0].collection.get(translated.identity)
    translated.translation = "新译文"
    polished.translation = "润色译文"
    translated.metadata[0][1]["value"].append(2)
    assert next(iter(original)).translation == ""
    assert next(iter(original)).metadata[0][1]["value"] == [1]

    success, errors = _capture(qapp, session)
    assert success and not errors
    before = session._persistence.snapshots[0][1]
    assert [entry.translation for entry in before] == ["", "旧译文", "不变"]
    session.mark_completed()
    committed = session._persistence.commits[0]
    assert [entry.translation for entry in committed] == ["新译文", "润色译文", "不变"]
    assert len({entry.identity for entry in committed}) == 3
    assert len({entry.id for entry in committed}) == 1
    assert next(iter(original)).translation == ""
    assert next(iter(session._ctx.collection)).translation == "新译文"
    assert session._ctx.notifications == [session._ctx.collection]
    assert session.can_save


def test_cancel_discards_output_without_reverting_user_edits(qapp, session):
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "机器输出"
    next(iter(session._ctx.collection)).translation = "用户编辑"
    session.rollback_uncommitted()
    with pytest.raises(RuntimeError, match="已取消"):
        session.mark_completed()
    assert next(iter(session._ctx.collection)).translation == "用户编辑"
    assert session._persistence.commits == []
    assert not session.can_save


@pytest.mark.parametrize("change", ["identity", "revision", "entry", "source", "collection", "unselected"])
def test_any_concurrent_version_or_source_change_blocks_whole_commit(qapp, session, change):
    _capture(qapp, session)
    ctx = session._ctx
    if change == "identity":
        ctx.active_version_identity = ("project", "other")
    elif change == "revision":
        ctx.variant_revision += 1
    elif change == "entry":
        next(iter(ctx.collection)).stage = 9
    elif change == "source":
        ctx.slots["first"] = CollectionSlot("First", ctx.collection)
    elif change == "collection":
        ctx.slots["first"].collection = TranslationEntryCollection(deepcopy(tuple(ctx.collection)))
    else:
        next(iter(ctx.slots["third"].collection)).translation = "用户更新其它插件"
    with pytest.raises(RuntimeError, match="已变化|已修改"):
        session.mark_completed()
    assert session._persistence.commits == []
    assert not session.completed


def test_failed_snapshot_and_commit_never_publish_output_and_are_retryable(qapp, session):
    session._persistence.fail_snapshot = True
    success, errors = _capture(qapp, session)
    assert not success and errors
    with pytest.raises(RuntimeError, match="快照尚未完成"):
        session.mark_completed()
    session._persistence.fail_snapshot = False
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "待提交"
    session._persistence.fail_commit = True
    with pytest.raises(RuntimeError, match="版本操作失败"):
        session.mark_completed()
    assert next(iter(session._ctx.collection)).translation == ""
    assert not session.can_save
    session._persistence.fail_commit = False
    session.mark_completed()
    assert next(iter(session._ctx.collection)).translation == "待提交"


def test_commit_capture_and_save_are_idempotent_and_failed_save_can_retry(qapp, session):
    _capture(qapp, session)
    _capture(qapp, session)
    assert len(session._persistence.snapshots) == 1
    first = session.mark_completed()
    assert session.mark_completed() is first
    assert len(session._persistence.commits) == 1
    session._persistence.fail_save = True
    success, errors = _save(qapp, session)
    assert not success and errors and session.can_save and not session.saved
    session._persistence.fail_save = False
    success, errors = _save(qapp, session)
    assert success and not errors and session.saved and not session.can_save
    _save(qapp, session)
    assert len(session._persistence.saves) == 2
    assert len(session._persistence.commits) == 1


def test_save_rejects_changes_after_completion(qapp, session):
    _capture(qapp, session)
    session.mark_completed()
    next(iter(session._ctx.collection)).translation = "运行后编辑"
    with pytest.raises(RuntimeError, match="已修改"):
        _save(qapp, session)
    assert session._persistence.saves == []


def test_authoritative_projection_is_not_overwritten_after_commit(qapp, session):
    _capture(qapp, session)
    projected = TranslationEntryCollection([_entry("first", "权威译文")])

    def commit(entries):
        session._ctx.slots["first"] = CollectionSlot("First", projected)
        session._ctx.variant_revision += 1
        return SimpleNamespace(is_success=True)

    session._persistence.commit_translation = commit
    session.mark_completed()
    assert session._ctx.collection is projected
    success, errors = _save(qapp, session)
    assert success and not errors


def test_duplicate_identity_mapping_is_rejected_before_snapshot(session):
    ctx = session._ctx
    ctx.slots["third"].collection = TranslationEntryCollection(deepcopy(tuple(ctx.collection)))
    with pytest.raises(ValueError, match="重复 EntryKey"):
        task_session.TaskSession(ctx, (), SimpleNamespace(mode="translate"))


def test_snapshot_success_after_cancel_does_not_start_execution(qapp, session):
    success, errors = [], []
    session.capture_before(on_success=success.append, on_error=errors.append)
    session.rollback_uncommitted()
    _wait(qapp, session)
    assert not success and errors
    assert not session.completed


def test_real_version_persistence_preserves_all_source_entry_keys_and_commits_once(qapp, session):
    calls = []
    ctx = session._ctx
    ctx.runtime_context = object()

    def commit(states, _runtime, **expected):
        calls.append(("commit", states))
        assert expected["expected_project_revision"] == 3
        assert expected["expected_variant_revision"] == 4
        ctx.variant_revision = 5
        return OperationResult.completed({"revision": 5})

    def snapshot(name, _runtime):
        calls.append(("snapshot", name))
        return OperationResult.completed({"name": name})

    def save(_runtime):
        calls.append(("save", None))
        return OperationResult.completed({})

    ctx.project_commands = SimpleNamespace(replace_entry_states=commit, save_snapshot=snapshot, save=save)
    session._persistence = VersionPersistence(ctx, ctx.active_version_identity)
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "翻译"
    session.mark_completed()
    session.mark_completed()
    success, errors = _save(qapp, session)
    assert success and not errors
    assert [kind for kind, _value in calls] == ["snapshot", "commit", "save", "snapshot"]
    assert len(calls[1][1]) == 3
    assert calls[1][1][session.tasks[0].translate_entries[0].identity] == ("翻译", 0)


def test_legacy_ambiguous_ids_fail_closed(session):
    session._ctx.uses_authoritative_projection = False
    with pytest.raises(ValueError, match="迁移到 V2"):
        task_session.TaskSession(session._ctx, (), SimpleNamespace(mode="translate"))


def test_real_legacy_persistence_retains_unselected_plugin(qapp, session, tmp_path):
    from transbridge.persistence.variant_store import VariantStore

    ctx = session._ctx
    ctx.uses_authoritative_projection = False
    tasks = []
    for key, slot in ctx.slots.items():
        slot.collection = TranslationEntryCollection(replace(entry, id=key) for entry in slot.collection)
        if key == "first":
            tasks.append(SourceTask(key, slot.label, None, slot.collection, tuple(slot.collection), ()))
    ctx.active_project = SimpleNamespace(variant_dir=lambda _name: tmp_path)
    ctx.active_variant = "main"
    ctx.variant_store = VariantStore(tmp_path / "current.json")
    ctx.entry_labels, ctx.label_library = {}, {}
    run = task_session.TaskSession(ctx, tuple(tasks), SimpleNamespace(mode="translate"))
    run._persistence = VersionPersistence(ctx, ctx.active_version_identity)
    success, errors = _capture(qapp, run)
    assert success and not errors
    assert ctx.variant_store.translations == {"first": "", "second": "旧译文", "third": "不变"}
    run.tasks[0].translate_entries[0].translation = "翻译"
    run.mark_completed()
    success, errors = _save(qapp, run)
    assert success and not errors
    restored = VariantStore.load(tmp_path / "current.json")
    assert restored.translations == {"first": "翻译", "second": "旧译文", "third": "不变"}


def test_retry_failed_source_resets_partial_output_and_keeps_successful_draft(qapp, session):
    _capture(qapp, session)
    first, second = session.tasks
    first.translate_entries[0].translation = "成功副本"
    second.polish_entries[0].translation = "失败插件的部分输出"
    session.reset_sources({"second"})
    assert session.tasks[0] is first
    assert first.translate_entries[0].translation == "成功副本"
    retried = session.tasks[1]
    assert retried is not second
    assert retried.polish_entries[0].translation == "旧译文"
    assert retried.collection.get(retried.polish_entries[0].identity) is retried.polish_entries[0]
    session.mark_completed()
    with pytest.raises(RuntimeError, match="已经提交"):
        session.reset_sources({"second"})
