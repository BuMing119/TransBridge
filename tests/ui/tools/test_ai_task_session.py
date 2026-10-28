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
from transbridge.application.translation.result_merge import (
    ResultConflictPolicy,
    ResultEntryState,
    merge_result_entries,
)
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.application.translation.task_scope import SourceTask
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.version_persistence import VersionPersistence

_application = None


@pytest.fixture(scope="module")
def qapp():
    # Global worker signal buses outlive a single module's fixture teardown.
    global _application
    _application = QApplication.instance() or QApplication([])
    return _application


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
        self.project_saved = self.snapshot_saved = False

    def create_snapshot(self, name, entries):
        self.snapshots.append((name, deepcopy(entries)))
        return SimpleNamespace(is_success=not self.fail_snapshot)

    def commit_translation(self, entries, *, commit_mutation=None):
        def publish():
            self.commits.append(deepcopy(entries))

        if commit_mutation is None:
            publish()
        else:
            commit_mutation(publish)
        return OperationResult.completed({}) if not self.fail_commit else OperationResult.cancelled()

    def save_translation(self, entries, name):
        self.saves.append((name, deepcopy(entries)))
        if not self.fail_save:
            self.project_saved = True
        return OperationResult.completed({}) if not self.fail_save else OperationResult.cancelled()

    def freeze_merge_translation(self, candidates, policy, **options):
        return lambda: self.merge_translation(candidates, policy, **options)

    def acknowledge_merge(self, result):
        assert result.is_success

    def freeze_save_project(self):
        entries = tuple(entry for slot in self.ctx.slots.values() for entry in slot.collection)
        return lambda: self.save_translation(entries, "current")

    def acknowledge_save(self):
        pass

    def mark_project_saved(self):
        self.project_saved = True

    def merge_translation(self, candidates, policy, *, commit_mutation=None, before_commit=None):
        entries = {entry.identity: entry for slot in self.ctx.slots.values() for entry in slot.collection}
        plan = merge_result_entries(
            {row.base.entry_key: row.base for row in candidates},
            {key: _state(entry) for key, entry in entries.items()},
            {row.incoming.entry_key: row.incoming for row in candidates},
            policy=policy,
        )
        if before_commit is not None:
            before_commit(plan)
        payload = tuple(
            replace(entries[row.entry_key], translation=row.translation, stage=row.stage) for row in plan.applied
        )
        result = self.commit_translation(payload, commit_mutation=commit_mutation)
        if not result.is_success:
            return result
        for row in plan.applied:
            entries[row.entry_key].translation, entries[row.entry_key].stage = row.translation, row.stage
        if plan.applied:
            self.project_saved = self.snapshot_saved = False
        return OperationResult.completed(plan)


def _state(entry):
    return ResultEntryState(entry.identity, entry.original, entry.translation, entry.stage, entry.context or "")


class _Evidence(TaskMergeEvidenceStore):
    """Observe the real precommit evidence and postcommit receipt persistence."""

    def __init__(self, directory, on_save=None):
        super().__init__(directory, "contract-regression")
        self.on_save = on_save
        self.receipts = []

    def save(self, *args):
        path = super().save(*args)
        if self.on_save is not None:
            self.on_save(*args)
        return path

    def acknowledge(self, candidates, plan, policy):
        self.receipts.append((candidates, plan, policy))
        return super().acknowledge(candidates, plan, policy)


def _v2_commands(ctx, calls, *, before_merge=None, before_save=None):
    """Exercise the real persistence adapter with a small authoritative merge port."""

    def merge(candidates, _runtime, *, policy, before_commit=None, commit_mutation=None, **expected):
        assert expected["expected_variant_ref"].identity.value == ctx.active_version_identity[1]
        if before_merge is not None:
            before_merge()
        entries = {entry.identity: entry for slot in ctx.slots.values() for entry in slot.collection}
        plan = merge_result_entries(
            {row.base.entry_key: row.base for row in candidates},
            {key: _state(entry) for key, entry in entries.items()},
            {row.incoming.entry_key: row.incoming for row in candidates},
            policy=policy,
        )

        def publish():
            if before_commit is not None:
                before_commit(plan)
            for row in plan.applied:
                entries[row.entry_key].translation, entries[row.entry_key].stage = row.translation, row.stage
            calls.append(("merge", plan))
            if plan.applied:
                ctx.variant_revision += 1

        publish() if commit_mutation is None else commit_mutation(publish)
        return OperationResult.completed(plan)

    def snapshot(name, _runtime):
        calls.append(("snapshot", name))
        return OperationResult.completed({})

    def save(_runtime):
        if before_save is not None:
            before_save()
        calls.append(("save", None))
        return OperationResult.completed({})

    return SimpleNamespace(merge_entry_states=merge, save_snapshot=snapshot, save=save)


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


def _complete(session):
    if session.completed:
        return session._commit_result
    result = session.apply_entries(set(session._draft.keys))
    session.finish()
    return result


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


def test_task_output_is_deeply_detached_and_commits_only_target_changes(qapp, session):
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
    assert not session._persistence.snapshots
    _complete(session)
    committed = session._persistence.commits[0]
    assert {entry.translation for entry in committed} == {"新译文", "润色译文"}
    assert len({entry.identity for entry in committed}) == 2
    assert len({entry.id for entry in committed}) == 1
    assert next(iter(original)).translation == "新译文"
    assert next(iter(session._ctx.collection)).translation == "新译文"
    assert session._ctx.notifications == [session._ctx.collection]
    assert session.can_save


def test_cancel_discards_output_without_reverting_user_edits(qapp, session):
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "机器输出"
    next(iter(session._ctx.collection)).translation = "用户编辑"
    session.rollback_uncommitted()
    with pytest.raises(RuntimeError, match="已取消"):
        _complete(session)
    assert next(iter(session._ctx.collection)).translation == "用户编辑"
    assert session._persistence.commits == []
    assert not session.can_save


@pytest.mark.parametrize("change", ["identity", "revision", "source", "collection", "unselected"])
def test_fixed_task_merges_through_unrelated_changes_but_rejects_identity_switch(qapp, session, change):
    _capture(qapp, session)
    ctx = session._ctx
    if change == "identity":
        ctx.active_version_identity = ("project", "other")
    elif change == "revision":
        ctx.variant_revision += 1
    elif change == "source":
        ctx.slots["first"] = CollectionSlot("First", ctx.collection)
    elif change == "collection":
        ctx.slots["first"].collection = TranslationEntryCollection(deepcopy(tuple(ctx.collection)))
    else:
        next(iter(ctx.slots["third"].collection)).translation = "用户更新其它插件"
    candidate = session.tasks[0].translate_entries[0]
    candidate.translation = "固定输入的结果"
    if change == "identity":
        with pytest.raises(RuntimeError, match="已变化"):
            _complete(session)
        assert session._persistence.commits == []
        assert not session.completed
    else:
        _complete(session)
        assert next(iter(ctx.collection)).translation == "固定输入的结果"
        assert session.completed
        if change == "unselected":
            assert next(iter(ctx.slots["third"].collection)).translation == "用户更新其它插件"


def test_failed_snapshot_and_commit_never_publish_output_and_are_retryable(qapp, session):
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "待提交"
    session._persistence.fail_commit = True
    with pytest.raises(RuntimeError, match="版本操作失败"):
        _complete(session)
    assert next(iter(session._ctx.collection)).translation == ""
    assert not session.can_save
    session._persistence.fail_commit = False
    _complete(session)
    assert next(iter(session._ctx.collection)).translation == "待提交"


def test_commit_capture_and_save_are_idempotent_and_failed_save_can_retry(qapp, session):
    _capture(qapp, session)
    _capture(qapp, session)
    assert not session._persistence.snapshots
    first = _complete(session)
    assert _complete(session) is first
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


def test_external_save_cannot_claim_overwritten_task_result_is_durable(qapp, session):
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "AI成果"
    _complete(session)
    next(iter(session._ctx.collection)).translation = "运行后编辑"
    session._ctx.dirty = False
    assert not session.observe_project_saved()
    assert not session.project_saved


def test_session_keeps_original_project_history_location(session, tmp_path):
    run = task_session.TaskSession(
        session._ctx, (), SimpleNamespace(mode="translate"), project_dir=tmp_path / "original"
    )
    session._ctx.active_version_identity = ("other", "variant")
    assert run.version_identity == ("project", "variant")
    assert run.project_dir == tmp_path / "original"
    assert run.history_dir == tmp_path / "original" / "ai-task-history"


def test_v2_session_accepts_explicit_project_location(session, tmp_path):
    run = task_session.TaskSession(
        session._ctx, (), SimpleNamespace(mode="translate"), project_dir=tmp_path / "v2-project"
    )
    assert run.history_dir == tmp_path / "v2-project" / "ai-task-history"


def test_external_project_save_is_latched_without_claiming_snapshot_saved(qapp, session):
    session._ctx.dirty = False
    assert not session.observe_project_saved()
    _capture(qapp, session)
    _complete(session)
    session._ctx.dirty = True
    assert not session.observe_project_saved()
    session._ctx.dirty = False
    assert session.observe_project_saved()
    assert session.project_saved and not session.saved
    assert session.can_save
    assert not session.observe_project_saved()
    session._ctx.dirty = True
    assert session.project_saved  # Historical durability of this task's version stays latched.


@pytest.mark.parametrize("change", ["project", "entries", "unknown_dirty"])
def test_clean_unrelated_state_cannot_mark_task_result_saved(qapp, session, change):
    _capture(qapp, session)
    _complete(session)
    session._ctx.dirty = False
    if change == "project":
        session._ctx.active_version_identity = ("other", "variant")
    elif change == "entries":
        next(iter(session._ctx.collection)).translation = "外部修改"
    else:
        session._ctx.dirty = None
    assert not session.observe_project_saved()
    assert not session.project_saved


def test_authoritative_projection_is_not_overwritten_after_commit(qapp, session):
    _capture(qapp, session)
    projected = TranslationEntryCollection([replace(_entry("first", "候选"), stage=0)])

    def commit(entries, **options):
        session._ctx.slots["first"] = CollectionSlot("First", projected)
        session._ctx.variant_revision += 1
        return SimpleNamespace(is_success=True)

    session._persistence.commit_translation = commit
    session.tasks[0].translate_entries[0].translation = "候选"
    _complete(session)
    assert session._ctx.collection is projected
    success, errors = _save(qapp, session)
    assert success and not errors


def test_duplicate_identity_mapping_is_rejected_before_snapshot(session):
    ctx = session._ctx
    ctx.slots["third"].collection = TranslationEntryCollection(deepcopy(tuple(ctx.collection)))
    with pytest.raises(ValueError, match="重复 EntryKey"):
        task_session.TaskSession(ctx, (), SimpleNamespace(mode="translate"))


def test_real_version_persistence_preserves_all_source_entry_keys_and_commits_once(qapp, session):
    calls = []
    ctx = session._ctx
    ctx.runtime_context = object()

    ctx.project_commands = _v2_commands(ctx, calls)
    session._persistence = VersionPersistence(ctx, ctx.active_version_identity)
    _capture(qapp, session)
    session.tasks[0].translate_entries[0].translation = "翻译"
    _complete(session)
    _complete(session)
    success, errors = _save(qapp, session)
    assert success and not errors
    assert [kind for kind, _value in calls] == ["merge", "save"]
    plan = calls[0][1]
    assert len(plan.applied) == 1
    assert (plan.applied[0].entry_key, plan.applied[0].translation) == (
        session.tasks[0].translate_entries[0].identity,
        "翻译",
    )
    assert next(iter(ctx.slots["third"].collection)).translation == "不变"


def test_retry_failed_source_resets_partial_output_and_keeps_successful_draft(qapp, session):
    _capture(qapp, session)
    first, second = session.tasks
    first.translate_entries[0].translation = "成功副本"
    second.polish_entries[0].translation = "失败插件的部分输出"
    session.retry_entries({entry.identity for entry in session.tasks[1].entries})
    assert session.tasks[0] is first
    assert first.translate_entries[0].translation == "成功副本"
    retried = session.tasks[1]
    assert retried is not second
    assert retried.polish_entries[0].translation == "旧译文"
    assert retried.collection.get(retried.polish_entries[0].identity) is retried.polish_entries[0]
    _complete(session)
    with pytest.raises(RuntimeError, match="已经提交"):
        session.retry_entries({entry.identity for entry in session.tasks[1].entries})


def test_partial_apply_keeps_unselected_inputs_and_only_publishes_translation_fields(qapp, session):
    _capture(qapp, session)
    translated = session.tasks[0].translate_entries[0]
    rejected = session.tasks[1].polish_entries[0]
    translated.translation, translated.stage = "通过检查", 3
    translated.metadata[0][1]["value"].append(9)
    rejected.translation = "未采纳的草稿"

    first = session.apply_entries({translated.identity})
    assert not session.completed and session.can_save
    assert session.applied_keys == frozenset({translated.identity})
    assert session.apply_entries({translated.identity}) is first
    assert len(session._persistence.commits) == 1
    published = next(iter(session._ctx.collection))
    assert (published.translation, published.stage) == ("通过检查", 3)
    assert published.original == "source" and published.metadata[0][1]["value"] == [1]
    assert next(iter(session._ctx.slots["second"].collection)).translation == "旧译文"
    assert next(iter(session._ctx.slots["third"].collection)).translation == "不变"


def test_partial_apply_uses_latest_collection_candidate_after_entry_replacement(qapp, session):
    _capture(qapp, session)
    task = session.tasks[0]
    original_reference = task.translate_entries[0]
    task.collection.add(replace(original_reference, translation="替换后的候选", stage=3))
    assert original_reference.translation == ""
    session.apply_entries({original_reference.identity})
    assert next(iter(session._ctx.collection)).translation == "替换后的候选"


def test_retry_slice_preserves_source_context_and_other_drafts(qapp, session):
    ctx = session._ctx
    original = tuple(ctx.collection)[0]
    failed = replace(original, id="failed", key="failed", entry_key=EntryKey(SourceNamespace("first"), "failed"))
    outside = replace(original, id="outside", key="outside", entry_key=EntryKey(SourceNamespace("first"), "outside"))
    collection = TranslationEntryCollection([original, failed, outside])
    ctx.slots["first"].collection = collection
    run = task_session.TaskSession(
        ctx,
        (SourceTask("first", "First", None, collection, (original, failed), ()),),
        SimpleNamespace(mode="translate"),
    )
    _capture(qapp, run)
    successful, failed_draft = run.tasks[0].translate_entries
    successful.translation = "成功草稿"
    failed_draft.translation = "失败的半成品"
    failed_draft.original = "不可沿用的新输入"
    failed_draft.metadata[0][1]["value"].append(2)
    retried = run.retry_entries({failed.identity})

    assert len(retried) == 1 and len(retried[0].collection) == 3
    assert retried[0].polish_entries == ()
    assert len(retried[0].translate_entries) == 1
    restored = retried[0].translate_entries[0]
    assert (restored.original, restored.translation) == ("source", "")
    assert restored.metadata[0][1]["value"] == [1]
    assert run.tasks[0].translate_entries[0] is successful
    assert retried[0].collection.get(successful.identity).translation == "成功草稿"
    assert retried[0].collection is run.tasks[0].collection
    assert run.tasks[0].translate_entries[1] is restored
    assert next(iter(ctx.collection)).translation == ""


def test_partial_apply_save_retry_reuses_persistence_without_rebuilding_history(qapp, session):
    _capture(qapp, session)
    first, second = session.tasks[0].translate_entries[0], session.tasks[1].polish_entries[0]
    first.translation = "首次成功"
    second.translation = "首次失败输出"
    session.apply_entries({first.identity})
    persistence = session._persistence
    success, errors = _save(qapp, session)
    assert success and not errors and session.saved
    retried = session.retry_entries({second.identity})
    assert retried[0].polish_entries[0].translation == "旧译文"
    retried[0].polish_entries[0].translation = "重试成功"
    session.apply_entries({second.identity})
    assert session._persistence is persistence
    assert not session.saved and not session.project_saved
    assert session.can_save and not session.completed
    assert next(iter(session._ctx.collection)).translation == "首次成功"
    assert next(iter(session._ctx.slots["second"].collection)).translation == "重试成功"
    session._persistence.fail_save = True
    success, errors = _save(qapp, session)
    assert not success and errors
    session._persistence.fail_save = False
    success, errors = _save(qapp, session)
    assert success and not errors
    assert len(session._persistence.commits) == 2 and len(session._persistence.saves) == 3
    assert not session._persistence.snapshots
    session.finish()
    assert session.completed


def test_cancel_after_partial_apply_preserves_applied_and_allows_save(qapp, session):
    _capture(qapp, session)
    first, second = session.tasks[0].translate_entries[0], session.tasks[1].polish_entries[0]
    first.translation, second.translation = "已应用", "未应用"
    session.apply_entries({first.identity})
    session.rollback_uncommitted()
    with pytest.raises(RuntimeError, match="已取消"):
        session.apply_entries({second.identity})
    with pytest.raises(RuntimeError, match="已取消"):
        session.retry_entries({second.identity})
    with pytest.raises(RuntimeError, match="已取消"):
        session.require_current()
    assert next(iter(session._ctx.collection)).translation == "已应用"
    assert next(iter(session._ctx.slots["second"].collection)).translation == "旧译文"
    success, errors = _save(qapp, session)
    assert success and not errors and session.saved


@pytest.mark.parametrize("action", ["apply", "retry"])
def test_external_edit_after_partial_commit_preserves_current_and_retry_uses_fixed_input(
    qapp, session, tmp_path, action
):
    _capture(qapp, session)
    first, second = session.tasks[0].translate_entries[0], session.tasks[1].polish_entries[0]
    first.translation = "已应用"
    session.apply_entries({first.identity})
    next(iter(session._ctx.slots["second"].collection)).translation = "用户新编辑"
    operation = session.apply_entries if action == "apply" else session.retry_entries
    second.translation = "新AI结果"
    evidence = []
    session.merge_evidence_writer = _Evidence(tmp_path, lambda *args: evidence.append(args))
    result = operation({second.identity})
    assert next(iter(session._ctx.slots["second"].collection)).translation == "用户新编辑"
    if action == "apply":
        assert result.kept_current_keys == (second.identity,)
        assert second.identity not in session.applied_keys
        assert evidence and evidence[0][1].conflicts
    else:
        assert result[0].polish_entries[0].translation == "旧译文"


def test_unknown_or_applied_keys_cannot_be_retried(qapp, session):
    _capture(qapp, session)
    unknown = EntryKey(SourceNamespace("first"), "unknown")
    with pytest.raises(ValueError, match="不属于"):
        session.apply_entries({unknown})
    with pytest.raises(ValueError, match="不属于"):
        session.retry_entries({unknown})
    key = session.tasks[0].translate_entries[0].identity
    session.apply_entries({key})
    with pytest.raises(ValueError, match="已应用"):
        session.retry_entries({key})


def test_real_v2_partial_commits_merge_latest_targets_and_retry_save_without_history(qapp, session, monkeypatch):
    monkeypatch.setattr(task_session, "VersionPersistence", VersionPersistence)
    ctx = session._ctx
    calls = []
    ctx.runtime_context = object()
    fail_save = False

    def saving():
        if fail_save:
            raise OSError("磁盘暂时不可写")

    ctx.project_commands = _v2_commands(ctx, calls, before_save=saving)
    session._persistence = VersionPersistence(ctx, ctx.active_version_identity)
    _capture(qapp, session)
    first, second = session.tasks[0].translate_entries[0], session.tasks[1].polish_entries[0]
    first.translation, second.translation = "翻译成功", "校对待重试"
    session.apply_entries({first.identity})
    assert _save(qapp, session)[0]
    failed = session.retry_entries({second.identity})[0].polish_entries[0]
    failed.translation = "校对成功"
    next(iter(ctx.slots["third"].collection)).translation = "其它来源新编辑"
    ctx.variant_revision += 7
    session.apply_entries({failed.identity})
    fail_save = True
    success, errors = _save(qapp, session)
    assert not success and errors and not session.project_saved
    fail_save = False
    assert _save(qapp, session)[0]
    commits = [data for kind, data in calls if kind == "merge"]
    assert len(commits) == 2
    assert [(row.entry_key, row.translation) for row in commits[0].applied] == [(first.identity, "翻译成功")]
    assert [(row.entry_key, row.translation) for row in commits[1].applied] == [(second.identity, "校对成功")]
    assert next(iter(ctx.slots["third"].collection)).translation == "其它来源新编辑"
    assert [kind for kind, _ in calls].count("save") == 2
    snapshots = [data for kind, data in calls if kind == "snapshot"]
    assert not snapshots


def test_failed_second_application_preserves_saved_generation(qapp, session, monkeypatch):
    _capture(qapp, session)
    first, second = session.tasks[0].translate_entries[0], session.tasks[1].polish_entries[0]
    first.translation, second.translation = "已保存", "待应用"
    session.apply_entries({first.identity})
    assert _save(qapp, session)[0]
    previous = session._persistence
    previous.fail_commit = True
    with pytest.raises(RuntimeError, match="版本操作失败"):
        session.apply_entries({second.identity})
    assert session._persistence is previous and session.saved
    assert session.applied_keys == frozenset({first.identity})
    assert next(iter(session._ctx.slots["second"].collection)).translation == "旧译文"
    previous.fail_commit = False
    session.apply_entries({second.identity})
    assert next(iter(session._ctx.slots["second"].collection)).translation == "待应用"


@pytest.mark.parametrize("change", ["original", "context", "missing", "candidate_original"])
def test_structural_target_change_is_pending_even_when_prefer_incoming(qapp, session, tmp_path, change):
    _capture(qapp, session)
    candidate = session.tasks[0].translate_entries[0]
    candidate.translation = "AI成果"
    current = next(iter(session._ctx.collection))
    if change == "original":
        current.original = "新原文"
    elif change == "context":
        current.context = "新上下文"
    elif change == "missing":
        session._ctx.slots["first"].collection = TranslationEntryCollection()
    else:
        candidate.original = "候选偷偷改变的原文"
    evidence = []
    session.policy = ResultConflictPolicy.PREFER_INCOMING
    session.merge_evidence_writer = _Evidence(tmp_path, lambda *args: evidence.append(args))
    plan = session.apply_entries({candidate.identity})
    assert not plan.applied and not session.applied_keys
    assert [conflict.reason for conflict in plan.pending] == ["source_changed_or_missing"]
    assert current.translation == ""
    assert evidence and evidence[0][1].pending == plan.pending
    assert session.merge_evidence_writer.load()[0]["applied_keys"] == []


def test_save_current_project_does_not_mark_replaced_ai_result_durable(qapp, session):
    _capture(qapp, session)
    candidate = session.tasks[0].translate_entries[0]
    candidate.translation = "AI成果"
    session.apply_entries({candidate.identity})
    next(iter(session._ctx.collection)).translation = "用户后续编辑"
    success, errors = _save(qapp, session)
    assert not success and errors and "替换" in errors[0]
    assert session._persistence.project_saved
    assert not session.project_saved and not session.saved
