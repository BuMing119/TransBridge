"""A source-incomplete save must not trap navigation after persisting edits."""

from types import SimpleNamespace

import pytest

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationResult
from transbridge.application.projects import DirtyDecision

from . import test_project_open_operation

_wait_until = test_project_open_operation._wait_until
opening = test_project_open_operation.opening


@pytest.mark.parametrize("persisted", [True, False])
def test_open_after_partial_save_only_continues_for_durably_saved_translations(opening, persisted):
    case = opening
    warning = "译文已保存，来源待补充"
    receipt = OperationResult.partial(
        {"dirty": not persisted, "source_content_complete": False},
        counts=OperationCounts(succeeded=1, failed=1),
        diagnostics=(Diagnostic("PROJECT_SAVE_SOURCE_CONTENT_MISSING", warning),),
    )
    case.host.project_commands = SimpleNamespace(save=lambda *_args, **_kwargs: receipt)
    activate = case.host.current_project_opener.activate

    def activate_after_save(*args, dirty_decision, **options):
        assert dirty_decision is DirtyDecision.SAVE
        return activate(*args, dirty_decision=None, **options)

    case.host.current_project_opener.activate = activate_after_save
    case.release.set()
    assert case.operation.start(
        lambda: OperationResult.completed(SimpleNamespace(hydrations=())),
        dirty_decision=DirtyDecision.SAVE,
        success_verb="opened",
        show_error_dialog=False,
        on_success=case.host.successes.append,
        on_failure=lambda *value: case.host.failures.append(value),
    )
    _wait_until(lambda: case.host.project_open_worker is None)

    assert case.host.current_project_opener.committed is persisted
    if persisted:
        assert case.context.project_name == "new"
        assert case.host.successes and not case.host.failures
        # The success message retains the warning instead of immediately
        # replacing it with a second status-bar notification.
        assert warning in case.host.messages[-1].splitlines()
    else:
        assert case.context.project_name == "old"
        assert case.host.failures and not case.host.successes
