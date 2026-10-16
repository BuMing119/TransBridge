import threading
from types import SimpleNamespace

import pytest

from tests.ui.tools.terminology.test_sync_view import _presenter, _unknown_result
from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobState
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.ui.tools.terminology import sync_presenter
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncViewState


def test_duplicate_completion_cannot_restore_old_run_after_new_preflight():
    entered, release = threading.Event(), threading.Event()
    main = threading.get_ident()
    result = _unknown_result()

    def read_result(*_args):
        if threading.get_ident() != main:
            entered.set()
            assert release.wait(3)
        return result

    service = SimpleNamespace(
        status=lambda *_args: SimpleNamespace(is_terminal=True, state=JobState.COMPLETED),
        result=read_result,
        preflight=lambda *_args: SimpleNamespace(available=True),
    )
    presenter = _presenter(service)
    ref = JobRef("job", "owner", "run")
    presenter._state = TerminologySyncViewState(job_ref=ref)
    completed = []
    slow = threading.Thread(target=lambda: completed.append(presenter.complete_job(ref)))
    slow.start()
    try:
        assert entered.wait(3)
        assert presenter.complete_job(ref).result is result
        current = presenter.preflight(TerminologySyncMode.BACKUP)
        assert current.job_ref is None
        release.set()
        slow.join(3)
        assert not slow.is_alive()
        assert completed == [current]
        assert presenter.state is current
        assert presenter.state.result is None
    finally:
        release.set()
        slow.join(3)


@pytest.mark.parametrize("terminal", [False, True])
def test_new_preflight_also_rejects_status_read_started_before_its_service_call(terminal):
    entered, release = threading.Event(), threading.Event()
    result_reads = []

    def status(*_args):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(is_terminal=terminal, state=JobState.COMPLETED if terminal else JobState.RUNNING)

    service = SimpleNamespace(
        status=status,
        result=lambda *_args: result_reads.append(True) or _unknown_result(),
        preflight=lambda *_args: SimpleNamespace(available=True),
    )
    presenter = _presenter(service)
    ref = JobRef("job", "owner", "run")
    slow = threading.Thread(target=lambda: presenter.complete_job(ref))
    slow.start()
    try:
        assert entered.wait(3)
        current = presenter.preflight(TerminologySyncMode.BACKUP)
        release.set()
        slow.join(3)
        assert not slow.is_alive()
        assert presenter.state is current
        assert result_reads == []
    finally:
        release.set()
        slow.join(3)


def test_repeated_completion_reuses_display_projection_for_identical_result(monkeypatch):
    result = _unknown_result()
    service = SimpleNamespace(
        status=lambda *_args: SimpleNamespace(is_terminal=True, state=JobState.COMPLETED),
        result=lambda *_args: result,
    )
    presenter = _presenter(service)
    created = []
    original = sync_presenter.OutcomeProjection

    def build(result, items):
        created.append(result)
        return original(result, items)

    monkeypatch.setattr(sync_presenter, "OutcomeProjection", build)
    ref = JobRef("job", "owner", "run")
    first = presenter.complete_job(ref)
    second = presenter.complete_job(ref)
    assert created == [result]
    assert second.result_projection is first.result_projection


@pytest.mark.parametrize("terminal", [False, True])
def test_old_activity_and_completion_starting_after_new_preflight_cannot_rebind_history(terminal):
    status_reads = []
    service = SimpleNamespace(
        preflight=lambda *_args: SimpleNamespace(available=True),
        status=lambda *_args: status_reads.append(True),
    )
    presenter = _presenter(service)
    ref = JobRef("job", "owner", "old-run")
    # A newly opened window still accepts history before the user begins work.
    assert presenter.observe_activity(ref, JobState.RUNNING, terminal=False)
    current = presenter.preflight(TerminologySyncMode.BACKUP)
    state = JobState.COMPLETED if terminal else JobState.RUNNING
    assert not presenter.observe_activity(ref, state, terminal=terminal)
    assert presenter.complete_job(ref) is current
    assert presenter.state is current
    assert current.job_ref is None and current.result is None
    assert status_reads == []
