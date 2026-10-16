"""Historical result review is sourced from attributed durable evidence."""

from dataclasses import replace
import json

import pytest

from tests.application.assistant_requests.test_round_undo import _commit, _setup
from transbridge.application.assistant_requests.models import ExecutionDispatch, RequestError, StepReceipt
from transbridge.application.assistant_requests.result_review import _exported_files, read_request_review

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def test_review_retains_recorded_before_after_without_reading_current_project(composed, monkeypatch):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    requests.undo = undo
    _commit(case)
    monkeypatch.setattr(
        undo.projects, "active_variant_snapshot", lambda: pytest.fail("review must not read live values")
    )
    review = read_request_review(requests, context, execution.request_id)
    assert len(review.changes) == 1
    assert (review.changes[0].before, review.changes[0].after) == ("before", "assistant")
    assert review.changes[0].original is None
    assert review.undo_available
    assert "1 条词条" in review.undo_summary
    assert "标签已修改" in review.changes[0].detail


def test_legacy_missing_receipts_are_explicit_not_reconstructed(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    requests.undo = undo
    requests.transact(context, lambda state: state.pop("undo_rounds"))
    review = read_request_review(requests, context, execution.request_id)
    assert not review.changes and not review.exported_files and not review.undo_available
    assert "没有可展示" in "".join(review.notices)
    assert "没有词条级结果" in review.outcomes[0].message


def test_failed_items_come_from_saved_result_even_after_task_history_is_gone(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    requests.undo = undo
    payload = {
        "tool": "set_stage",
        "result": {
            "partial": True,
            "message": "两条完成，一条失败",
            "failed_items": [{"entry_id": "missing", "reason": "已删除"}],
        },
    }
    requests.update_request(
        context,
        execution.request_id,
        lambda request: replace(request, effects=(replace(request.effects[0], receipt=json.dumps(payload)),)),
    )
    review = read_request_review(requests, context, execution.request_id)
    assert any(row.entry_id == "missing" and row.message == "已删除" for row in review.failures)
    assert not review.changes


def test_unknown_request_cannot_borrow_another_requests_receipts(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, _, undo = case
    requests.undo = undo
    _commit(case)
    with pytest.raises(RequestError, match="REVIEW_SCOPE_MISMATCH"):
        read_request_review(requests, context, "foreign")


def test_export_actions_use_authorized_targets_and_actual_saved_after_state(tmp_path):
    path = str(tmp_path / "translated.xml")
    commit = {"targets": [path]}
    assert _exported_files({"files": [{"path": path, "after": {"exists": True}}]}, commit) == [path]
    assert _exported_files({"files": [{"path": path, "after": {"exists": False}}]}, commit) == []
    with pytest.raises(ValueError, match="范围不一致"):
        _exported_files({"files": [{"path": str(tmp_path / "other.xml"), "after": {"exists": True}}]}, commit)


def test_cancelled_and_aborted_steps_are_not_reported_as_failed(composed):
    case = _setup(composed)
    _, requests, context, _, _, _, execution, undo = case
    requests.undo = undo
    dispatch = ExecutionDispatch(
        "dispatch",
        execution,
        "",
        "",
        status="cancelled",
        step_specs=(("edit_translation", "a"), ("edit_translation", "b"), ("set_stage", "c")),
        step_receipts=(StepReceipt("0", "cancelled"), StepReceipt("1", "aborted"), StepReceipt("2", "outcome_unknown")),
    )
    requests.update_request(context, execution.request_id, lambda request: replace(request, dispatches=(dispatch,)))
    review = read_request_review(requests, context, execution.request_id)
    assert [row.status for row in review.outcomes[-3:]] == ["cancelled", "aborted", "outcome_unknown"]
    assert [row.status for row in review.failures] == ["outcome_unknown"]
