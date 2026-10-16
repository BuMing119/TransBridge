from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QDialog, QWidget
import pytest

from transbridge.application.terminology.models import CanonicalDiff, DraftRef, TerminologyDraft
from transbridge.ui.tools.terminology import version_interactions as interactions

_APP = QApplication.instance() or QApplication([])


class _Presenter:
    def __init__(self):
        self.closed = False
        self.preview = SimpleNamespace(diff=CanonicalDiff("old", "new", "digest", ()))
        self.draft = TerminologyDraft(DraftRef("draft", "project", "variant", "old", "base", 2, "decisions"))
        self.events = []

    def preview_publish(self):
        self.events.append("preview")
        return self.preview

    def publish_preview(self, preview):
        self.events.append(("publish", preview))

    def active_draft(self):
        self.events.append("read draft")
        return self.draft

    def discard_draft(self, ref):
        assert ref is self.draft.ref
        self.events.append(("discard", ref))
        self.draft = None

    def restore(self, version_ref):
        assert self.draft is None
        self.events.append(("restore", version_ref))


@pytest.fixture
def flow():
    parent = QWidget()
    presenter = _Presenter()
    reads = []
    jobs = []
    controller = interactions.VersionInteractionController(
        parent,
        presenter,
        lambda call, success, message: reads.append((call, success, message)),
        lambda call, message: jobs.append((call, message)),
        lambda: presenter.events.append("refresh"),
    )
    yield controller, presenter, reads, jobs
    parent.close()


def _finish_read(reads):
    call, success, _message = reads.pop(0)
    result = call()
    success(result)


@pytest.mark.parametrize("accepted", [True, False])
def test_publish_waits_for_preview_and_submits_exact_reviewed_object(flow, monkeypatch, accepted):
    controller, presenter, reads, jobs = flow
    observed = []

    def review(dialog):
        observed.append(dialog.changes.summary.text())
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(interactions.ChangeReviewDialog, "exec", review)
    controller.publish()
    assert presenter.events == []
    assert jobs == []
    _finish_read(reads)
    assert "old → new" in observed[0]
    assert len(jobs) == int(accepted)
    if accepted:
        jobs[0][0]()
        assert presenter.events[-1] == ("publish", presenter.preview)
        assert presenter.events[-1][1] is presenter.preview


def test_closing_context_during_review_prevents_publish(flow, monkeypatch):
    controller, presenter, reads, jobs = flow

    def close_while_reviewing(_dialog):
        presenter.closed = True
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(interactions.ChangeReviewDialog, "exec", close_while_reviewing)
    controller.publish()
    _finish_read(reads)
    assert jobs == []


@pytest.mark.parametrize("accepted", [True, False])
def test_discard_requires_review_and_refreshes_only_after_success(flow, monkeypatch, accepted):
    controller, presenter, reads, jobs = flow
    observed = []
    monkeypatch.setattr(interactions, "_confirm", lambda *args: observed.append(args) or accepted)
    controller.discard()
    _finish_read(reads)
    assert "0 条术语决定、0 条人工调整记录和 0 项冲突处理" in observed[0][2]
    assert "已发布版本和历史会保留" in observed[0][2]
    assert "refresh" not in presenter.events
    if accepted:
        assert len(reads) == 1
        _finish_read(reads)
        assert presenter.draft is None
        assert presenter.events[-1] == "refresh"
    else:
        assert reads == []
        assert presenter.draft is not None
    assert jobs == []


@pytest.mark.parametrize("accepted", [True, False])
def test_restore_explicitly_discards_draft_then_submits_restore(flow, monkeypatch, accepted):
    controller, presenter, reads, jobs = flow
    observed = []
    monkeypatch.setattr(interactions, "_confirm", lambda *args: observed.append(args) or accepted)
    version = SimpleNamespace(version_id="historical-version")
    controller.restore(version)
    _finish_read(reads)
    assert "historical-version" in observed[0][2]
    assert observed[0][3] == "放弃草稿并恢复"
    assert jobs == []
    if accepted:
        _finish_read(reads)
        assert presenter.draft is None
        assert presenter.events[-1] == "refresh"
        jobs[0][0]()
        assert presenter.events[-1] == ("restore", version)
    else:
        assert presenter.draft is not None
        assert reads == []


def test_failed_discard_never_submits_restore_or_clears_ui(flow, monkeypatch):
    controller, presenter, reads, jobs = flow
    monkeypatch.setattr(interactions, "_confirm", lambda *_args: True)

    def stale(_ref):
        raise RuntimeError("草稿已变化")

    presenter.discard_draft = stale
    controller.restore(SimpleNamespace(version_id="old"))
    _finish_read(reads)
    with pytest.raises(RuntimeError, match="草稿已变化"):
        _finish_read(reads)
    assert jobs == []
    assert "refresh" not in presenter.events
    assert presenter.draft is not None


def test_restore_without_draft_needs_only_history_confirmation(flow, monkeypatch):
    controller, presenter, reads, jobs = flow
    presenter.draft = None
    observed = []
    monkeypatch.setattr(interactions, "_confirm", lambda *args: observed.append(args) or True)
    controller.restore("old-version")
    _finish_read(reads)
    assert observed[0][3] == "创建新版"
    assert reads == []
    jobs[0][0]()
    assert presenter.events[-1] == ("restore", "old-version")
