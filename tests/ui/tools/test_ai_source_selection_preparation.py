from __future__ import annotations

from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from tests.ui.tools import test_ai_session_preparation as support
from transbridge.application.translation.task_config_snapshot import execution_signature as signature
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.scope_presenter import TranslationScope
from transbridge.ui.tools.ai_translator.task_empty_recovery import EmptyRecoveryCompletion
from transbridge.ui.tools.ai_translator.task_scope import SourceWorkbenchScope, TaskScope
from transbridge.ui.tools.ai_translator.task_session import TaskSession
from transbridge.ui.tools.ai_translator.task_source_selection import SelectionIntent, capture_selection_intent
from transbridge.ui.workbench.filters_presenter import entry_category

qapp = support.qapp


def _window(monkeypatch, mode="mixed", scope=None):
    ctx, tasks, spec = support._inputs(monkeypatch, 4)
    slot = ctx.slots["first"]
    entries = tuple(
        replace(entry, translation="旧译文" if i % 2 else "", stage=i % 2) for i, entry in enumerate(slot.collection)
    )
    slot.collection = TranslationEntryCollection(entries)
    ctx.active_slot = slot
    ctx.entry_labels = {entries[0].id: {"chosen"}}
    workbench = SourceWorkbenchScope(entries[:2], (entries[0].id,))
    return SimpleNamespace(
        _ctx=ctx,
        _task_scope=TaskScope(ctx, workbench, entry_category),
        _view=SimpleNamespace(sources_panel=SimpleNamespace(selected_slots=lambda: (slot,))),
        _view_port=SimpleNamespace(mode=mode, overwrite=False),
        _scope_presenter=SimpleNamespace(state=scope or TranslationScope()),
    ), spec


@pytest.mark.parametrize("mode", ["translate", "mixed", "polish"])
@pytest.mark.parametrize("preset", [None, "table_view", "selection", "labels"])
def test_selection_reuses_same_scope_core_in_background(qapp, monkeypatch, mode, preset):
    scope = (
        TranslationScope(label_filters=frozenset({"chosen"}))
        if preset == "labels"
        else TranslationScope(preset=preset, selected_entry_ids=frozenset({"e0"}))
    )
    window, spec = _window(monkeypatch, mode, scope)
    config = LLMConfig()
    expected = window._task_scope.build(
        window._view.sources_panel.selected_slots(), scope, mode=mode, config=config, overwrite=False
    )
    gui, threads = get_ident(), []
    original = TaskScope.build

    def build(scope, *args, **kwargs):
        threads.append(get_ident())
        return original(scope, *args, **kwargs)

    monkeypatch.setattr(TaskScope, "build", build)
    success, errors = [], []
    job = TaskSession.create_async(
        window._ctx,
        (),
        spec,
        selection_intent=capture_selection_intent(window, config),
        on_success=success.append,
        on_error=errors.append,
    )
    support._wait(qapp, lambda: not job.busy)
    assert success and not errors and threads and threads != [gui]
    actual = success[0].tasks
    assert [
        (t.key, [e.identity for e in t.translate_entries], [e.identity for e in t.polish_entries]) for t in actual
    ] == [
        (t.key, [e.identity for e in t.translate_entries], [e.identity for e in t.polish_entries])
        for t in expected
        if t.entries
    ]


def test_recovery_selection_uses_detached_record_and_preserves_applied_filter(qapp, monkeypatch):
    window, spec = _window(monkeypatch, "polish")
    config = LLMConfig()
    entries = tuple(window._ctx.active_slot.collection)
    window._recovery_selection = (
        {
            "project_id": "project",
            "variant_id": "variant",
            "mode": "polish",
            "config_digest": signature(config),
            "sources": [{"key": "first", "translate_keys": [], "polish_keys": [e.identity.to_dict() for e in entries]}],
            "applied_entries": [
                {
                    "entry_key": entries[1].identity.to_dict(),
                    "original": entries[1].original,
                    "text": entries[1].translation,
                    "stage": entries[1].stage,
                }
            ],
        },
        False,
    )
    success, errors = [], []
    job = TaskSession.create_async(
        window._ctx,
        (),
        spec,
        selection_intent=capture_selection_intent(window, config),
        on_success=success.append,
        on_error=errors.append,
    )
    support._wait(qapp, lambda: not job.busy)
    assert success and not errors
    session = success[0]
    assert [entry.identity for entry in session.tasks[0].polish_entries] == [entries[i].identity for i in (0, 2, 3)]
    assert session.selection_intent.recovery == window._recovery_selection
    assert session.selection_intent.recovery is not window._recovery_selection


def test_label_changes_while_worker_selects_fail_closed(qapp, monkeypatch):
    window, spec = _window(monkeypatch, scope=TranslationScope(label_filters=frozenset({"chosen"})))
    entered, released = Event(), Event()
    original = SelectionIntent.select

    def select(intent, *args):
        entered.set()
        assert released.wait(5)
        return original(intent, *args)

    monkeypatch.setattr(SelectionIntent, "select", select)
    success, errors = [], []
    job = TaskSession.create_async(
        window._ctx,
        (),
        spec,
        selection_intent=capture_selection_intent(window, LLMConfig()),
        on_success=success.append,
        on_error=errors.append,
    )
    support._wait(qapp, entered.is_set)
    window._ctx.entry_labels = {}
    released.set()
    support._wait(qapp, lambda: not job.busy)
    assert not success and errors and "标签" in errors[0]


@pytest.mark.parametrize("record_changed", [False, True])
def test_empty_recovery_prepare_is_read_only_and_commit_rechecks_record(tmp_path, record_changed):
    from tests.ui.tools.test_task_recovery_binding import setup_binding

    binding, _request, _session, window = setup_binding(tmp_path)
    binding.release()
    selected = window._recovery_selection
    prepared = EmptyRecoveryCompletion(tmp_path, selected).prepare()
    assert binding.store.load(binding.record["task_id"])["state"] == "preparing"
    if record_changed:
        binding.store.update(binding.record["task_id"], state="cancelled")
        with pytest.raises(ValueError, match="已变化"):
            prepared.commit()
        assert binding.store.load(binding.record["task_id"])["state"] == "cancelled"
    else:
        result = prepared.commit()
        assert result["state"] == "completed"
