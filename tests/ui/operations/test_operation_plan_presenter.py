from __future__ import annotations

import time

import pytest

from transbridge.ui.operations import (
    DownloadOperationMapper,
    FomodOperationMapper,
    OperationKind,
    OperationPlanDraft,
    OperationPlanError,
    OperationPlanPresenter,
    UploadOperationMapper,
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
            UploadOperationMapper(),
            DownloadOperationMapper(),
            WriteOperationMapper(),
            FomodOperationMapper(),
        ),
        submitter,
    )


def test_return_to_edit_invalidates_confirmation_and_preserves_new_draft() -> None:
    submitter = Submitter()
    subject = presenter(submitter)
    opened = subject.open(OperationKind.UPLOAD, draft(), owner_id="gui")
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
    opened = subject.open(OperationKind.DOWNLOAD, draft(), owner_id="gui")
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
        (OperationKind.UPLOAD, {"credentials_ready": False}, "CREDENTIAL"),
        (OperationKind.DOWNLOAD, {"permission_ready": False}, "PERMISSION"),
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
