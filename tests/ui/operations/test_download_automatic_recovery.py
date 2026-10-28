"""Automatic download continuation keeps the real application's save guards."""

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_autosave import disk_snapshot
from tests.ui.operations.test_batch_download_session import case as _case_fixture, states
from transbridge.application.tasks import TaskCancelled
from transbridge.ui.operations.download_run import run_download

case = _case_fixture


def run_with_new_edit(case):
    edited = case.sources[0].entries[0].identity
    attempts = []

    def save(progress, cancel):
        if not attempts:
            result = case.context.project_commands.update_entry(
                edited, case.context.runtime_context, translation="下载期间的新编辑", stage=1
            )
            assert result.is_success
        attempts.append(True)
        return case.session.apply(progress, cancel)

    outcome = run_download(
        tuple(source.source_id for source in case.sources),
        "defer",
        (),
        case.session.compare,
        save,
        lambda *_args: None,
        case.cancel,
    )
    return outcome, attempts


def test_new_target_edit_is_kept_while_other_source_is_automatically_saved(case):
    edited = case.sources[0].entries[0].identity
    reliable = case.sources[1].entries[0].identity
    outcome, attempts = run_with_new_edit(case)

    assert outcome.updated == 1 and outcome.requires_review
    assert outcome.preview.deferred == 1
    assert outcome.preview.review_rows[0].translation == "下载期间的新编辑"
    assert states(case)[edited] == ("下载期间的新编辑", 1)
    assert states(case)[reliable] == ("云端2", 3)
    assert not case.services.project_lifecycle.active.dirty
    assert not case.services.project_snapshots.list(case.context.runtime_context)
    assert case.session.record["merges"]
    assert len(attempts) == 1  # Independent targets are merged together in the first publication.


@pytest.mark.parametrize("keep_local", [True, False])
def test_review_after_partial_automatic_save_preserves_explicit_choice_on_retry(case, keep_local):
    outcome, _attempts = run_with_new_edit(case)
    edited = case.sources[0].entries[0].identity
    row = outcome.preview.review_rows[0]
    token = "keep_local" if keep_local else row.candidates[0].candidate_id
    calls = tuple(case.gateway.calls)

    for _ in range(2):
        run_download(
            tuple(source.source_id for source in case.sources),
            "defer",
            ((row.row_id, token),),
            case.session.compare,
            case.session.apply,
            lambda *_args: None,
            case.cancel,
        )
        expected = ("下载期间的新编辑", 1) if keep_local else ("云端1", 3)
        assert states(case)[edited] == expected
        assert not case.services.project_lifecycle.active.dirty
        assert tuple(case.gateway.calls) == calls


def test_cancel_after_real_comparison_does_not_create_a_restore_point_or_publish(case):
    before = case.services.project_lifecycle.active.variant.snapshot()

    def compare(*args):
        result = case.session.compare(*args)
        case.cancel.set()
        return result

    with pytest.raises(TaskCancelled):
        run_download(
            tuple(source.source_id for source in case.sources),
            "prefer_remote",
            (),
            compare,
            case.session.apply,
            lambda *_args: None,
            case.cancel,
        )

    assert case.services.project_lifecycle.active.variant.snapshot() == before
    assert not case.services.project_lifecycle.active.dirty
    assert not case.services.project_snapshots.list(case.context.runtime_context)


def test_noop_save_uses_authoritative_dirty_state_when_ui_projection_has_not_caught_up(case):
    for index, source in enumerate(case.sources, 1):
        assert case.context.project_commands.update_entry(
            source.entries[0].identity, case.context.runtime_context, translation=f"云端{index}", stage=3
        ).is_success
    assert case.services.project_lifecycle.active.dirty
    # A worker can observe the GUI projection before its queued refresh arrives.
    case.context.dirty = False

    outcome = run_download(
        tuple(source.source_id for source in case.sources),
        "prefer_remote",
        (),
        case.session.compare,
        case.session.apply,
        lambda *_args: None,
        case.cancel,
    )

    assert not case.services.project_lifecycle.active.dirty
    assert outcome.updated == 0
    assert disk_snapshot(case) == case.services.project_lifecycle.active.variant.snapshot()
