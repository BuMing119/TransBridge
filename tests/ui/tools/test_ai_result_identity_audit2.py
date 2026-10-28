"""Saved AI results retain their manifest target when opened after a switch."""

from dataclasses import replace
from threading import Event

import pytest

from tests.ui.tools import test_ai_partial_task as support
from tests.ui.tools.test_task_recovery_binding import setup_binding
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_result_recovery import save_recovery_results
from transbridge.ui.tools.ai_translator.task_merge_dialog import TaskMergeDialog

qapp = support.qapp


@pytest.fixture
def saved_result(tmp_path):
    binding, _, session, window = setup_binding(tmp_path)
    ctx = window._ctx
    ctx.slots = {"first": ctx.slots["first"]}
    ctx.entry_labels, ctx.label_library = {}, {}
    from tests.ui.tools.test_ai_task_session import _v2_commands

    ctx.runtime_context = object()
    ctx.project_commands = _v2_commands(ctx, [])
    ledger = TaskEntryResults(session.tasks[:1], "run")
    ledger.entries = {
        key: replace(row, status="succeeded", text="AI from original variant", stage=3, decision="accepted", attempt=1)
        for key, row in ledger.entries.items()
    }
    save_recovery_results(binding.store, "run", ledger)
    yield binding, session, ctx
    binding.release()


@pytest.mark.parametrize("action", ["adopt", "keep"])
@pytest.mark.parametrize("identity", [("project", "other-variant"), ("other-project", "variant")])
def test_saved_result_opened_on_another_variant_cannot_change_that_variant(
    qapp, tmp_path, saved_result, action, identity
):
    binding, session, ctx = saved_result
    saved = binding.store.load_results("run")
    ctx.active_version_identity = identity
    entry = next(iter(ctx.collection))
    entry.translation, entry.stage = "Other variant manual", 5
    dialog = TaskMergeDialog(ctx, tmp_path, "run", store=binding.store)
    try:
        support._wait(qapp, lambda: not dialog.busy)
        assert dialog.review.rows  # Old results remain available for read-only viewing.
        dialog.review.resolve({entry.identity}, keep=action == "keep")
        support._wait(qapp, lambda: not dialog.busy)
        assert entry.translation == "Other variant manual"
        assert binding.store.load_results("run") == saved
        assert "工程版本" in dialog.review.error
        assert dialog.review.identity == session.version_identity
    finally:
        dialog.close()


def test_switch_during_refresh_remains_read_only_and_return_to_target_allows_review(
    qapp, tmp_path, saved_result, monkeypatch
):
    binding, session, ctx = saved_result
    loading, release = Event(), Event()
    load = binding.store.load

    def paused_load(task_id):
        loading.set()
        if not release.wait(5):
            raise RuntimeError("test did not release result loading")
        return load(task_id)

    monkeypatch.setattr(binding.store, "load", paused_load)
    dialog = TaskMergeDialog(ctx, tmp_path, "run", store=binding.store)
    try:
        support._wait(qapp, loading.is_set)
        ctx.active_version_identity = ("project", "other-variant")
        release.set()
        support._wait(qapp, lambda: not dialog.busy)
        entry = next(iter(ctx.collection))
        assert dialog.review.identity == session.version_identity
        assert dialog.review.rows and not dialog.review.current
        dialog.review.resolve({entry.identity})
        assert "工程版本" in dialog.review.error
        assert entry.translation != "AI from original variant"
        ctx.active_version_identity = session.version_identity
        dialog.review.refresh()
        support._wait(qapp, lambda: not dialog.busy)
        assert not dialog.review.error
        dialog.review.resolve({entry.identity})
        support._wait(qapp, lambda: not dialog.busy)
        assert entry.translation == "AI from original variant", dialog.review.error
    finally:
        release.set()
        support._wait(qapp, lambda: not dialog.busy)
        dialog.close()
