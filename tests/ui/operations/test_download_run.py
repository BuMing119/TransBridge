"""One requested download saves reliable work without manual plan acceptance."""

from threading import Event

import pytest

from transbridge.application.tasks import TaskCancelled
from transbridge.ui.operations.batch_download_view import DownloadPreview, DownloadReceipt
from transbridge.ui.operations.download_run import run_download
from transbridge.ui.operations.sync_review_view import SyncReviewRow


def run(preview, save, *, cancel=None):
    return run_download(
        ("source",), "prefer_remote", (), lambda *_args: preview, save, lambda *_args: None, cancel or Event()
    )


def test_one_request_completes_comparison_and_publication_without_returning_a_manual_preview():
    published = []

    def save(_progress, _cancel):
        published.append("durable")
        return DownloadReceipt(2, ("saved with recovery point",))

    outcome = run(DownloadPreview(2, 3, 0), save)

    assert outcome == DownloadReceipt(2, ("saved with recovery point",))
    assert published == ["durable"]


@pytest.mark.parametrize("preview", [DownloadPreview(0, 4, 0), DownloadPreview(3, 0, 1, can_apply=False)])
def test_non_actionable_result_never_reaches_publication(preview):
    outcome = run(preview, lambda *_args: pytest.fail("No actionable updates may be published"))

    assert outcome is preview


def test_reliable_updates_are_published_while_uncertain_rows_remain_reviewable():
    row = SyncReviewRow("key", "entry", "original", "local", "remote", "conflict")
    preview = DownloadPreview(2, 3, 1, review_rows=(row,))

    outcome = run(preview, lambda *_args: DownloadReceipt(2, ("saved",)))

    assert outcome.updated == 2 and outcome.requires_review
    assert outcome.details == ("saved",)
    assert outcome.preview.review_rows == (row,)
    assert outcome.preview.deferred == 1
    assert outcome.preview.updates == 0 and not outcome.preview.can_apply
    assert outcome.preview.unchanged == 5
    assert preview.updates == 2  # Presentation projection does not mutate the reviewed plan.


def test_rebased_plan_is_automatically_published_without_a_second_user_request():
    remaining = ["changed", "durable"]

    def save(_progress, _cancel):
        result = remaining.pop(0)
        if result == "changed":
            return DownloadReceipt(0, requires_review=True, preview=DownloadPreview(1, 3, 1))
        return DownloadReceipt(1, ("saved latest safe candidate",))

    outcome = run(DownloadPreview(2, 3, 0), save)

    assert outcome.updated == 1 and outcome.requires_review
    assert outcome.details == ("saved latest safe candidate",)
    assert outcome.preview.deferred == 1
    assert not remaining


def test_continuously_changing_content_stops_after_bounded_internal_refreshes():
    attempts = []

    def save(_progress, _cancel):
        attempts.append("rebase")
        return DownloadReceipt(0, requires_review=True, preview=DownloadPreview(1, 0, 0))

    outcome = run(DownloadPreview(1, 0, 0), save)

    assert len(attempts) == 3
    assert outcome.updated == 0 and outcome.requires_review
    assert "持续变化" in outcome.preview.status and "尚未保存" in outcome.preview.status


def test_cancellation_after_comparison_cannot_start_publication():
    cancelled = Event()

    def compare(*_args):
        cancelled.set()
        return DownloadPreview(1, 0, 0)

    with pytest.raises(TaskCancelled):
        run_download(
            ("source",),
            "prefer_remote",
            (),
            compare,
            lambda *_args: pytest.fail("Cancellation must stop the save"),
            lambda *_args: None,
            cancelled,
        )


def test_late_cancellation_cannot_reverse_an_already_published_receipt():
    cancelled = Event()

    def save(_progress, _cancel):
        cancelled.set()
        return DownloadReceipt(1, ("already durable",))

    assert run(DownloadPreview(1, 0, 0), save, cancel=cancelled) == DownloadReceipt(1, ("already durable",))


def test_save_failure_preserves_original_error_and_a_fresh_request_can_retry():
    preview = DownloadPreview(1, 0, 0)
    failure = PermissionError("Current version is locked")

    def fail(_progress, _cancel):
        raise failure

    with pytest.raises(PermissionError) as caught:
        run(preview, fail)
    assert caught.value is failure
    assert run(preview, lambda *_args: DownloadReceipt(1)).updated == 1


def test_rebase_that_leaves_only_conflicts_does_not_attempt_an_empty_commit():
    pending = DownloadPreview(0, 0, 1)
    outcome = run(
        DownloadPreview(1, 0, 0),
        lambda *_args: DownloadReceipt(0, requires_review=True, preview=pending),
    )

    assert outcome is pending


def test_successful_source_with_unsaved_local_edits_can_save_without_remote_changes():
    outcome = run(
        DownloadPreview(0, 3, 0, can_apply=False, can_save=True),
        lambda *_args: DownloadReceipt(0, ("local edits saved",)),
    )

    assert outcome == DownloadReceipt(0, ("local edits saved",))


def test_published_reliable_updates_keep_incomplete_source_retry_available():
    outcome = run(
        DownloadPreview(1, 0, 0, retryable=True),
        lambda *_args: DownloadReceipt(1),
    )

    assert outcome.updated == 1 and outcome.retryable


def test_pending_review_does_not_hide_incomplete_source_retry_after_publication():
    outcome = run(
        DownloadPreview(1, 0, 1, retryable=True),
        lambda *_args: DownloadReceipt(1),
    )

    assert outcome.updated == 1 and outcome.requires_review
    assert outcome.retryable and outcome.preview.retryable
    assert not outcome.preview.can_save and not outcome.preview.updates


def test_missing_sources_never_become_a_successful_save_receipt():
    preview = DownloadPreview(0, 0, 0, can_apply=False, retryable=True)

    outcome = run(preview, lambda *_args: pytest.fail("Incomplete sources cannot be saved"))

    assert outcome is preview and outcome.retryable
