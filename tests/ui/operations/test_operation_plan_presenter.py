from __future__ import annotations

import time

import pytest

from transbridge.ui.operations import (
    FomodOperationMapper,
    OperationKind,
    OperationPlanDraft,
    OperationPlanError,
    OperationPlanPresenter,
    WriteOperationMapper,
)


class Submitter:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, kind, draft, preflight, owner_id):
        self.calls.append((kind, draft, preflight, owner_id))
        return f"run-{len(self.calls)}"


def draft(**changes) -> OperationPlanDraft:
    values = {
        "request": object(),
        "target": "target",
        "target_revision": "remote-r1",
        "input_fingerprint": "input-r1",
        "scope_summary": "3 个对象",
        "mode_summary": "仅原文",
        "conflict_summary": "发现冲突时停止",
        "backup_summary": "覆盖前备份",
        "estimated_impact": (("objects", 3),),
        "expected_side_effects": ("网络写入 3 个对象",),
    }
    values.update(changes)
    return OperationPlanDraft(**values)


def presenter(submitter: Submitter) -> OperationPlanPresenter:
    return OperationPlanPresenter(
        (
            WriteOperationMapper(),
            FomodOperationMapper(),
        ),
        submitter,
    )


def test_other_operations_keep_existing_confirmation_timeout_policy() -> None:
    subject = presenter(Submitter())
    opened = subject.open(OperationKind.WRITE, draft(), owner_id="gui")
    checked = subject.preflight(opened.session_id, owner_id="gui")
    assert checked.confirmation_token.expires_at is not None
    subject._confirmations._clock = lambda: checked.confirmation_token.expires_at + 1

    with pytest.raises(OperationPlanError) as expired:
        subject.confirm(opened.session_id, checked.confirmation_token, owner_id="gui")
    assert expired.value.code == "CONFIRMATION_EXPIRED"


def test_starting_a_new_check_revokes_previous_confirmation_even_when_check_fails(monkeypatch) -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    opened = subject.open(OperationKind.WRITE, draft(), owner_id="gui")
    first = subject.preflight(opened.session_id, owner_id="gui")

    def failed_check(_self, _draft):
        with pytest.raises(OperationPlanError) as checking:
            subject.confirm(opened.session_id, first.confirmation_token, owner_id="gui")
        assert checking.value.code == "PREFLIGHT_REQUIRED"
        raise RuntimeError("remote check failed")

    monkeypatch.setattr(WriteOperationMapper, "preflight", failed_check)
    with pytest.raises(RuntimeError, match="remote check failed"):
        subject.preflight(opened.session_id, owner_id="gui")
    with pytest.raises(OperationPlanError) as failed:
        subject.confirm(opened.session_id, first.confirmation_token, owner_id="gui")
    assert failed.value.code == "PREFLIGHT_REQUIRED"
    assert submitter.calls == []


def test_return_to_edit_invalidates_confirmation_and_preserves_new_draft() -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    opened = subject.open(OperationKind.WRITE, draft(), owner_id="gui")
    first = subject.preflight(opened.session_id, owner_id="gui")

    edited = subject.edit(
        opened.session_id,
        draft(mode_summary="上传译文", input_fingerprint="input-r2"),
        owner_id="gui",
    )

    with pytest.raises(OperationPlanError, match="preflight"):
        subject.confirm(opened.session_id, first.confirmation_token, owner_id="gui")
    assert edited.mode_summary == "上传译文"
    assert not submitter.calls


def test_one_final_confirmation_is_one_shot() -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    opened = subject.open(OperationKind.WRITE, draft(), owner_id="gui")
    checked = subject.preflight(opened.session_id, owner_id="gui")

    assert subject.confirm(opened.session_id, checked.confirmation_token, owner_id="gui") == "run-1"
    with pytest.raises(OperationPlanError, match="closed or unknown"):
        subject.confirm(opened.session_id, checked.confirmation_token, owner_id="gui")
    assert len(submitter.calls) == 1


def test_retained_session_requires_fresh_preflight_before_another_submission() -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    opened = subject.open(OperationKind.WRITE, draft(), owner_id="gui")
    checked = subject.preflight(opened.session_id, owner_id="gui")

    assert (
        subject.confirm(opened.session_id, checked.confirmation_token, owner_id="gui", retain_session=True) == "run-1"
    )
    assert not subject.state(opened.session_id, owner_id="gui").submit_enabled
    with pytest.raises(OperationPlanError, match="preflight"):
        subject.confirm(opened.session_id, checked.confirmation_token, owner_id="gui", retain_session=True)
    subject.edit(opened.session_id, draft(input_fingerprint="updated-local"), owner_id="gui")
    refreshed = subject.preflight(opened.session_id, owner_id="gui")
    assert (
        subject.confirm(opened.session_id, refreshed.confirmation_token, owner_id="gui", retain_session=True) == "run-2"
    )
    subject.cancel(opened.session_id, owner_id="gui")


def test_cancel_has_zero_feature_side_effect_and_100_sessions_remain_bounded() -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    started = time.perf_counter()
    for index in range(100):
        opened = subject.open(OperationKind.FOMOD, draft(input_fingerprint=f"input-{index}"), owner_id="gui")
        subject.cancel(opened.session_id, owner_id="gui")
    assert time.perf_counter() - started < 0.5
    assert submitter.calls == []


@pytest.mark.parametrize(
    ("kind", "changes", "blocking_id"),
    [
        (OperationKind.WRITE, {"output_ready": False}, "OUTPUT"),
        (OperationKind.FOMOD, {"overwrite_risk": True}, "OVERWRITE"),
    ],
)
def test_each_domain_preflights_before_submit(kind, changes, blocking_id) -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    opened = subject.open(kind, draft(**changes), owner_id="gui")
    checked = subject.preflight(opened.session_id, owner_id="gui")

    assert not checked.ready
    assert blocking_id in {item.check_id for item in checked.checks if item.status.value == "blocked"}
    assert checked.confirmation_token is None


def test_ui_contracts_preserve_application_result_identity() -> None:
    from transbridge.application.preflight.operation import (
        OperationKind as ApplicationOperationKind,
        OperationPreflightResult,
    )
    from transbridge.ui.operations.preflight_view import OperationPreflightResult as UiPreflightResult

    result = WriteOperationMapper().preflight(draft(locked_count=1))

    assert OperationKind is ApplicationOperationKind
    assert UiPreflightResult is OperationPreflightResult
    assert isinstance(result, OperationPreflightResult)
    assert result.ready
    assert next(item for item in result.checks if item.check_id == "LOCKED_HIDDEN").status.value == "warning"
