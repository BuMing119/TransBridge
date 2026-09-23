"""File publications pin undo receipts in real Session storage."""

from dataclasses import replace
from types import SimpleNamespace
import zipfile

import pytest

from tests.application.assistant_requests.test_round_undo import _record, _setup
from transbridge.application.assistant_requests.file_undo_capture import (
    apply_file_commits,
    capture_file_commit,
    preflight_file_commits,
)
from transbridge.application.assistant_requests.models import EffectStatus, RequestError, RequestStatus
from transbridge.smart_assistant.tools.tool_archive import _tool_pack_archive

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _file_case(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, _ = case
    requests.update_request(
        context,
        execution.request_id,
        lambda request: replace(
            request,
            status=RequestStatus.OPEN,
            effects=tuple(replace(effect, status=EffectStatus.RUNNING) for effect in request.effects),
        ),
    )
    requests.transact(
        context,
        lambda state: state["undo_rounds"][execution.work_round_id]["effects"]["effect"].update(tool="pack_archive"),
    )
    return case


def test_real_archive_pack_and_explicit_file_inverse(composed, tmp_path):
    case = _file_case(composed)
    _, requests, context, _, _, _, execution, undo = case
    undo.file_backup_root = tmp_path / "private-undo"
    source = tmp_path / "source"
    source.mkdir()
    (source / "entry.txt").write_text("translated", encoding="utf-8")
    target = tmp_path / "output.zip"
    target.write_bytes(b"previous archive")
    gate = SimpleNamespace(
        service=SimpleNamespace(undo=undo),
        context=context,
        current_request=lambda: next(
            r for r in requests.requests(requests.state(context)) if r.request_id == execution.request_id
        ),
    )
    tool_context = SimpleNamespace(
        assistant_gate=gate,
        assistant_effect_id="effect",
        request_context=replace(context, authorized_roots=(str(tmp_path),)),
    )
    result = _tool_pack_archive({"src_dir": str(source), "archive_path": str(target)}, tool_context)
    assert result.success, result.message
    with zipfile.ZipFile(target) as archive:
        assert archive.read("entry.txt") == b"translated"
    commits = _record(case)["commits"]
    assert commits[0]["kind"] == "files" and commits[0]["status"] == "recorded"
    preflight_file_commits(undo, context, commits, backup_root=undo.file_backup_root)
    requests.update_request(
        context,
        execution.request_id,
        lambda request: replace(
            request,
            status=RequestStatus.CANCELLED,
            effects=tuple(replace(effect, status=EffectStatus.SUCCEEDED) for effect in request.effects),
        ),
    )
    outcome = undo.undo(context, execution.work_round_id)
    assert not outcome["partial"]
    assert target.read_bytes() == b"previous archive"


def test_file_failure_keeps_pending_evidence_and_never_retries(composed, tmp_path):
    case = _file_case(composed)
    _, _, context, _, _, _, execution, undo = case
    target = tmp_path / "output"

    def partial_write():
        target.write_bytes(b"partial")
        raise OSError("publication interrupted")

    with pytest.raises(OSError, match="interrupted"):
        capture_file_commit(
            undo,
            context,
            execution,
            "effect",
            [target],
            partial_write,
            backup_root=tmp_path / "backup",
        )
    assert target.read_bytes() == b"partial"
    assert _record(case)["commits"][0]["status"] == "pending"
    assert not undo.preview(context, execution.work_round_id)["available"]


def test_file_batch_rejects_overlap_without_replaying_old_preimage(composed, tmp_path):
    case = _file_case(composed)
    _, _, context, _, _, _, execution, undo = case
    target = tmp_path / "output"
    for value in (b"first", b"second"):
        capture_file_commit(
            undo,
            context,
            execution,
            "effect",
            [target],
            lambda: target.write_bytes(value),
            backup_root=tmp_path / "backup",
        )
    with pytest.raises(RequestError, match="UNDO_FILE_OVERLAP"):
        preflight_file_commits(undo, context, _record(case)["commits"], backup_root=tmp_path / "backup")
    assert target.read_bytes() == b"second"


def test_conflict_in_later_commit_prevents_earlier_file_restore(composed, tmp_path):
    case = _file_case(composed)
    _, _, context, _, _, _, execution, undo = case
    first, second = tmp_path / "first", tmp_path / "second"
    for target in (first, second):
        capture_file_commit(
            undo,
            context,
            execution,
            "effect",
            [target],
            lambda: target.write_bytes(b"new"),
            backup_root=tmp_path / "backup",
        )
    second.write_bytes(b"user change")
    result = apply_file_commits(undo, context, _record(case)["commits"], backup_root=tmp_path / "backup")
    assert result["status"] == "conflict" and result["restored"] == []
    assert first.read_bytes() == b"new"


def test_pack_rejects_unauthorized_output_before_creation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "outside.zip"
    context = SimpleNamespace(authorized_roots=(str(source),))
    result = _tool_pack_archive({"src_dir": str(source), "archive_path": str(output)}, context)
    assert not result.success
    assert not output.exists()
